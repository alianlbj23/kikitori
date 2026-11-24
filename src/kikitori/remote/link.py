"""遠端運算的連線底層：一條 WebSocket 連線的單一寫入執行緒、有上限的送出佇列與看門狗。

位於 remote 模組的傳輸層，A 端客戶端與 B 端伺服器共用（docs/specs/remote-compute.md「A 端的
執行緒與狀態」、設計審查第 2 點）。

為什麼不讓各條執行緒直接 `connection.send()`：`websockets` 同步 API 的 `send` 平常**沒有逾時**
（`sendall` 在 `protocol_mutex` 裡可以無限期卡住，`close()` 也要同一把鎖）。Wi-Fi 斷線通常不會
送出 FIN／RST，這時直接送的執行緒——B 的 pipeline worker、A 的擷取執行緒——會一起卡死，
`session.stop` 也停不下來。所以：

- 其他執行緒只把訊息放進有上限的佇列（`send()` 不會阻塞）；佇列滿表示連線已經壞了，直接中斷。
- 只有一條寫入執行緒真的送出；看門狗發現它卡在同一則訊息超過 `write_stall` 秒就強制關 socket。
- 中斷一律直接關底層 socket（`abort()`），不走需要 `protocol_mutex` 的 `connection.close()`：
  卡住的 `sendall` 與 `recv` 都會立刻返回錯誤。
"""

import logging
import queue
import threading
import time
from collections.abc import Callable

from websockets.exceptions import ConnectionClosed
from websockets.sync.connection import Connection

from kikitori.remote.sockets import shutdown_socket

logger = logging.getLogger(__name__)

WRITE_STALL_SECS = 5.0
"""一則訊息送了這麼久還沒送完（對方不收、網路斷了）就中斷連線。"""

WATCHDOG_INTERVAL = 0.25
CLOSE_TIMEOUT = 2.0

Message = str | bytes


class _Close:
    """佇列裡的「送完前面的訊息後正常關閉」標記。"""

    def __init__(self, code: int, reason: str) -> None:
        self.code = code
        self.reason = reason


class Link:
    """包住一條已經建立的 WebSocket 連線，負責所有送出。

    收訊（`connection.recv`）仍由呼叫端的單一接收執行緒處理；`abort()` 會讓它立刻收到
    `ConnectionClosed`。
    """

    def __init__(
        self,
        connection: Connection,
        *,
        max_queue: int,
        name: str,
        write_stall: float = WRITE_STALL_SECS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            connection: 已完成握手的連線。
            max_queue: 送出佇列最多幾則；超過表示對方跟不上或連線已斷，連線被中斷。
            name: 執行緒名稱的前綴（log 用）。
            write_stall: 一則訊息最多送幾秒。
            clock: 看門狗用的時鐘（測試可注入）。

        Raises:
            ValueError: `max_queue` 或 `write_stall` 不是正數。
        """
        if max_queue <= 0 or write_stall <= 0:
            raise ValueError("max_queue and write_stall must be positive")
        self._connection = connection
        self._queue: queue.Queue[Message | _Close | None] = queue.Queue(max_queue)
        self._name = name
        self._write_stall = write_stall
        self._clock = clock
        self._lock = threading.Lock()
        self._writing_since: float | None = None
        self._closed = threading.Event()
        self._reason: str | None = None
        self._writer = threading.Thread(
            target=self._write_loop, name=f"{name}-writer", daemon=True
        )
        self._watchdog = threading.Thread(
            target=self._watch, name=f"{name}-watchdog", daemon=True
        )

    @property
    def closed(self) -> bool:
        """連線已中斷或已關閉（之後 `send()` 一律回 False）。"""
        return self._closed.is_set()

    @property
    def reason(self) -> str | None:
        """中斷的原因（`abort()` 的說明、佇列滿、寫入卡住）；正常關閉或還開著時為 None。"""
        with self._lock:
            return self._reason

    def start(self) -> None:
        """啟動寫入執行緒與看門狗。"""
        self._writer.start()
        self._watchdog.start()

    def send(self, message: Message) -> bool:
        """把一則訊息放進送出佇列（不阻塞）。

        Returns:
            False 表示沒有放進去：連線已關閉，或佇列滿了（此時連線同時被中斷）。
        """
        if self._closed.is_set():
            return False
        try:
            self._queue.put_nowait(message)
        except queue.Full:
            self.abort("the send queue is full (the peer is not receiving)")
            return False
        return True

    def close(self, code: int = 1000, reason: str = "") -> None:
        """送完佇列裡的訊息後正常關閉（不阻塞；卡住時由看門狗中斷）。可重複呼叫。"""
        if self._closed.is_set():
            return
        try:
            self._queue.put_nowait(_Close(code, reason))
        except queue.Full:
            self.abort("the send queue is full while closing")

    def abort(self, reason: str) -> None:
        """立刻中斷：直接關底層 socket（不等任何鎖）。可重複呼叫、可在任何執行緒呼叫。"""
        with self._lock:
            if self._closed.is_set():
                return
            self._reason = reason
            self._closed.set()
        logger.info("%s: aborting the connection: %s", self._name, reason)
        shutdown_socket(self._connection.socket)
        # 叫醒在等佇列的寫入執行緒
        try:
            self._queue.put_nowait(None)
        except queue.Full:
            pass  # 佇列滿：寫入執行緒手上有訊息，send 失敗後會看到 closed 而結束

    def join(self, timeout: float) -> bool:
        """等寫入執行緒與看門狗結束；回傳是否都已結束。"""
        deadline = self._clock() + timeout
        for thread in (self._writer, self._watchdog):
            if thread.ident is None or thread is threading.current_thread():
                continue
            thread.join(max(0.0, deadline - self._clock()))
        return not (self._writer.is_alive() or self._watchdog.is_alive())

    def _write_loop(self) -> None:
        while not self._closed.is_set():
            item = self._queue.get()
            if item is None or self._closed.is_set():
                return
            with self._lock:
                self._writing_since = self._clock()
            try:
                if isinstance(item, _Close):
                    self._closed.set()
                    self._connection.close(item.code, item.reason)
                    return
                self._connection.send(item)
            except ConnectionClosed:
                self.abort("the connection was closed")
                return
            except OSError as exc:
                self.abort(f"sending failed: {exc}")
                return
            finally:
                with self._lock:
                    self._writing_since = None

    def _watch(self) -> None:
        while not self._closed.wait(WATCHDOG_INTERVAL):
            with self._lock:
                since = self._writing_since
            if since is not None and self._clock() - since > self._write_stall:
                self.abort(f"sending stalled for more than {self._write_stall:.0f} s")
                return
        # 正常關閉（`_Close`）：`connection.close()` 本身有 close_timeout，但它也可能卡在
        # sendall；給它一點時間，還沒完成就直接關 socket。
        deadline = self._clock() + CLOSE_TIMEOUT + self._write_stall
        while self._writer.is_alive() and self._clock() < deadline:
            self._writer.join(WATCHDOG_INTERVAL)
        if self._writer.is_alive():
            shutdown_socket(self._connection.socket)
