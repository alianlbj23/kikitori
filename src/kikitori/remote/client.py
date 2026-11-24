"""遠端運算的 A 端客戶端：把這台電腦擷取的聲音送給運算主機，把它送回的字幕交給引擎。

位於 remote 模組的客戶端層（docs/specs/remote-compute.md「一場遠端字幕的時序」「A 端的執行緒與
狀態」「斷線重連」）。引擎在 `compute.mode == "remote"` 時用 `RemoteClientSession` 取代本機的
`Pipeline`：

- **連線**：自己建 socket（停止時關掉它就能中斷還在連線的握手）、TLS 1.3、出示自己的憑證、
  只信任 `servers.json` 裡配對過的主機（憑證當信任錨＋握手後比對 SPKI）、不走系統 proxy、
  不送 User-Agent。
- **執行緒**：擷取（讀聲音 → 切成 100 ms 的訊框）、接收（每條連線一條，唯一呼叫 `recv` 的
  執行緒）、心跳（每 3 秒 ping，量來回時間）、重連（斷線時才有）；送出全部經過 `Link`
  （單一寫入執行緒，見 `remote.link`）。
- **斷線續接**：B 還沒確認（`ack`）的音訊留在記憶體（最多 `BUFFER_SECS` 秒）；斷線後以退避
  重連、送 `session.resume`，從 B 回報的位置補送（限速 `RESEND_SPEED` 倍即時，B 的緩衝才不會
  溢位），B 補送 A 沒收到的完整字幕，A 依 `utterance_id` 去掉重複的。從最後收到 B 的訊息起
  `RECONNECT_SECS` 秒內接不回來就是 `error remote_lost`。
- **B 的回傳一律當不可信**（Open WebUI CVE-2025-64496 的教訓）：先由 `protocol.parse_server`
  驗外形，字幕與 metrics 再逐欄驗證、只留認得的欄位，才交給引擎；B 不能觸發 A 的任何指令。
- **停止**不依賴 B：送 `session.stop` → 最多等 `STOP_WAIT` 秒 B 回 idle → 關 socket → 等執行緒
  結束才返回，之後不會再冒出字幕（設計審查第 11 點）。
"""

import logging
import re
import socket
import ssl
import threading
import time
from collections import deque
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

import numpy as np
import numpy.typing as npt
from websockets.exceptions import (
    ConnectionClosed,
    InvalidHandshake,
    InvalidStatus,
)
from websockets.sync.client import ClientConnection, connect

from kikitori.capture import AudioSource, CaptureError
from kikitori.config import Settings
from kikitori.pipeline.types import Mode
from kikitori.remote import protocol as rp
from kikitori.remote.identity import Identity, spki_sha256_der
from kikitori.remote.link import Link
from kikitori.remote.pairing import TrustStore
from kikitori.remote.sockets import SafeClientConnection, shutdown_socket

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT = 5.0
HELLO_TIMEOUT = 10.0
HEARTBEAT_SECS = 3.0
DEAD_AFTER_SECS = 10.0
"""這麼久沒收到 B 的任何訊息（B 每 3 秒回 pong）就判定斷線。"""
RECONNECT_SECS = 30.0
"""從最後收到 B 的訊息起，這麼久還接不回來就放棄（B 保留 45 秒，必須比這個長）。"""
BUFFER_SECS = 60.0
"""B 還沒確認的音訊最多留多久；超過表示斷太久，放棄。"""
RESEND_SPEED = 8.0
"""續接時補送音訊的速度（即時的幾倍）：B 的音訊緩衝只有 30 秒，一口氣送完會溢位。"""
STOP_WAIT = 2.0
"""停止時最多等 B 回 idle 多久；之後直接關連線。"""
FRAME_SAMPLES = rp.SAMPLE_RATE // 10
SEND_QUEUE = 64
"""送出佇列上限（約 6 秒的音訊訊框）：滿了表示連線已經壞了。"""
READ_TIMEOUT = 0.1
MAX_BAD_EVENTS = 20
"""B 送來這麼多則不合格的字幕就中止（壞掉或惡意的主機）。"""
MAX_TEXT_CHARS = 10_000
MAX_LOG_CHARS = 2_000
MAX_VOICE = 64
_LANGUAGE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8}){0,2}$")
_LOG_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING}
_BACKOFF = (0.5, 1.0, 2.0, 4.0)
_FATAL_CONNECT_CODES = frozenset(
    {
        "remote_not_paired",
        "remote_incompatible",
        "remote_not_configured",
        "remote_identity_error",
    }
)
"""重連時遇到這些就不必再試（再試也一樣）。"""

RemoteCode = str
"""A 端的錯誤碼：`remote_not_configured`、`remote_unreachable`、`remote_not_paired`、
`remote_busy`、`remote_incompatible`、`remote_lost`、`remote_model_not_downloaded`、`remote_error`。"""

StatusSink = Callable[[str, str | None, str | None, Mapping[str, Any] | None], None]
"""`(state, detail, code, data)`：B 的 loading（含進度）／running、斷線中的 running
（`data={"reconnecting": True}`），以及結束狀態（只送一次）。"""
EventSink = Callable[[str, dict[str, Any]], None]
"""`(name, data)`：驗證過的 subtitle／metrics（不含 session_id，由引擎補上）。"""


class RemoteError(Exception):
    """連線或握手失敗；`code` 給介面翻譯，訊息給 log 與說明。"""

    def __init__(self, code: RemoteCode, message: str) -> None:
        super().__init__(message)
        self.code = code


def client_ssl_context(identity: Identity, servers: TrustStore) -> ssl.SSLContext:
    """TLS 1.3、出示自己的憑證、只信任配對過的運算主機。

    Raises:
        RemoteError: 還沒有配對過任何運算主機（`remote_not_paired`）。
    """
    trusted = servers.trusted_pem()
    if not trusted:
        raise RemoteError(
            "remote_not_paired", "this computer has not been paired with a compute host"
        )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    # 身分靠配對時記下的憑證（信任錨＋SPKI），不靠主機名稱：使用者可能填 IP 或 Tailscale 名稱
    context.check_hostname = False
    context.verify_mode = ssl.CERT_REQUIRED
    try:
        context.load_cert_chain(identity.cert_path, identity.key_path)
        context.load_verify_locations(cadata=trusted)
    except (OSError, ssl.SSLError) as exc:
        raise RemoteError(
            "remote_identity_error", f"cannot load this computer's certificate: {exc}"
        ) from exc
    return context


def _uri_host(host: str) -> str:
    return f"[{host}]" if ":" in host else host


@dataclass
class _Conn:
    """一條連到運算主機的連線（重連時換一條新的）。"""

    connection: ClientConnection
    link: Link
    receiver: threading.Thread | None = field(default=None)


class RemoteClientSession:
    """一場遠端字幕（A 端）。`connect()` → `start(source)` → `stop()`；一個物件只用一次。

    回呼在本物件的執行緒上呼叫（不是呼叫 `start()` 的那條）：結束狀態只送一次，而且使用者
    呼叫 `stop()` 時不送（由引擎自己送 idle）。
    """

    def __init__(
        self,
        *,
        identity: Identity,
        servers: TrustStore,
        host: str,
        port: int,
        app_version: str,
        mode: Mode,
        settings: Settings,
        on_status: StatusSink,
        on_event: EventSink,
        capture_code: Callable[[CaptureError], str],
        connect_timeout: float = CONNECT_TIMEOUT,
        dead_after: float = DEAD_AFTER_SECS,
        heartbeat: float = HEARTBEAT_SECS,
        reconnect_secs: float = RECONNECT_SECS,
        buffer_secs: float = BUFFER_SECS,
        resend_speed: float = RESEND_SPEED,
    ) -> None:
        """
        Args:
            identity: 這台電腦的憑證。
            servers: 配對過的運算主機。
            host: 運算主機的位址（`compute.host`）。
            port: 運算主機的埠。
            app_version: 握手時告訴 B 的版本。
            mode: session 模式。
            settings: A 的設定（只有白名單內的欄位會送給 B）。
            on_status: 狀態回呼。
            on_event: 事件回呼。
            capture_code: 擷取錯誤 → 錯誤碼（與本機相同的對應）。
            connect_timeout: 建立 TCP 連線的逾時。
            dead_after: 多久沒收到 B 的訊息判定斷線。
            heartbeat: 心跳間隔。
            reconnect_secs: 斷線後多久內要接回來（從最後收到 B 的訊息起算）。
            buffer_secs: B 還沒確認的音訊最多留幾秒。
            resend_speed: 續接時補送音訊的速度（即時的幾倍）。
        """
        self._identity = identity
        self._servers = servers
        self._host = host
        self._port = port
        self._app_version = app_version
        self._mode = mode
        self._settings = settings
        self._on_status = on_status
        self._on_event = on_event
        self._capture_code = capture_code
        self._connect_timeout = connect_timeout
        self._dead_after = dead_after
        self._heartbeat = heartbeat
        self._reconnect_secs = reconnect_secs
        self._max_buffer = round(buffer_secs * rp.SAMPLE_RATE)
        self._resend_speed = resend_speed

        self._lock = threading.Lock()
        self._callback_lock = threading.Lock()
        """回呼與「是否已結束」互斥：結束狀態之後不會再有任何狀態或事件（各執行緒都會呼叫回呼）。"""
        # 以下受 _lock 保護
        self._sock: socket.socket | None = None
        self._conn: _Conn | None = None
        self._live = False
        """擷取執行緒可以直接送出（重連補送期間為 False，新的音訊只進緩衝）。"""
        self._seq = 0
        """目前這條連線的下一個音訊序號（每條連線從 0 起）。"""
        self._captured = 0
        """這一場到目前為止擷取的樣本數（A 的時間軸）。"""
        self._frames: deque[tuple[int, npt.NDArray[np.int16]]] = deque()
        """B 還沒確認的音訊：(起始樣本, 音訊)。"""
        self._buffered = 0
        self._audio_done = False
        self._end_sent = False
        self._remote_id: int | None = None
        self._last_final_id = 0
        self._last_rx = time.monotonic()
        self._terminal_sent = False
        self._error: BaseException | None = None
        self._pings: dict[int, float] = {}
        self._next_nonce = 1
        self._rtt: float | None = None
        self._bad_events = 0

        self._models: dict[str, str] = {}
        self._source: AudioSource | None = None
        self._cancelled = threading.Event()
        self._stopping = threading.Event()
        self._remote_ended = threading.Event()
        self._finished = threading.Event()
        self._started = False
        self._threads: list[threading.Thread] = []

    # --- 狀態 -------------------------------------------------------------------

    @property
    def models(self) -> dict[str, str]:
        """B 會用的模型（`welcome.models`；顯示用）。"""
        return dict(self._models)

    @property
    def rtt_ms(self) -> float | None:
        """最近一次心跳量到的來回時間（毫秒）；還沒量到時為 None。"""
        with self._lock:
            return None if self._rtt is None else self._rtt * 1000

    @property
    def error(self) -> BaseException | None:
        """讓這場以 error 結束的擷取錯誤（引擎用來對應錯誤碼）；否則 None。"""
        with self._lock:
            return self._error

    def wait(self, timeout: float | None = None) -> bool:
        """等這場結束（B 回結束狀態、斷線或停止）；回傳是否已結束。"""
        return self._finished.wait(timeout)

    # --- 連線 -------------------------------------------------------------------

    def connect(self) -> None:
        """連上運算主機並完成握手（阻塞，最多約 `connect_timeout + HELLO_TIMEOUT` 秒）。

        Raises:
            RemoteError: 連不上、沒有配對、主機忙碌、版本不同、或回應不合格。
        """
        conn = self._open()
        with self._lock:
            self._conn = conn

    def _open(self) -> _Conn:
        """建立一條新連線並完成 hello／welcome。

        Raises:
            RemoteError: 同 `connect()`。
        """
        if not self._host:
            raise RemoteError("remote_not_configured", "no compute host is set")
        context = client_ssl_context(self._identity, self._servers)
        try:
            sock = socket.create_connection(
                (self._host, self._port), timeout=self._connect_timeout
            )
        except OSError as exc:
            raise RemoteError(
                "remote_unreachable", f"cannot connect to the compute host: {exc}"
            ) from exc
        with self._lock:
            if self._cancelled.is_set():
                sock.close()
                raise RemoteError("remote_unreachable", "cancelled")
            self._sock = sock
        try:
            connection = connect(
                f"wss://{_uri_host(self._host)}:{self._port}{rp.PATH}",
                sock=sock,
                ssl=context,
                # 一定直連：系統 proxy 會看到（而且可能記錄）連線的目的地
                proxy=None,
                compression=None,
                open_timeout=HELLO_TIMEOUT,
                ping_interval=None,
                close_timeout=2.0,
                max_size=rp.MAX_TEXT_BYTES,
                user_agent_header=None,
                # 17.x：直接拿到連線（不是 context manager，也不要它自動重連——重連自己做）
                legacy=True,
                # 關閉前先等讀取執行緒結束（見 remote.sockets：Windows 的 handle 重用）
                create_connection=SafeClientConnection,
            )
        except ssl.SSLCertVerificationError as exc:
            sock.close()
            raise RemoteError(
                "remote_not_paired",
                "the compute host's certificate is not the one paired with this computer",
            ) from exc
        except InvalidStatus as exc:
            sock.close()
            raise RemoteError(
                "remote_error",
                "the compute host refused the connection "
                f"(HTTP {exc.response.status_code})",
            ) from exc
        except (ssl.SSLError, InvalidHandshake, ConnectionClosed, EOFError) as exc:
            # TLS 1.3：B 拒絕我們的憑證時，用戶端常常已經以為握手完成，之後才看到連線被關
            sock.close()
            raise RemoteError(
                "remote_not_paired",
                f"the compute host refused this computer (not paired?): {exc}",
            ) from exc
        except (OSError, TimeoutError) as exc:
            sock.close()
            raise RemoteError(
                "remote_unreachable", f"connecting to the compute host failed: {exc}"
            ) from exc
        self._check_server_identity(connection)
        link = Link(connection, max_queue=SEND_QUEUE, name="remote-client")
        link.start()
        conn = _Conn(connection, link)
        try:
            link.send(rp.client_hello(self._app_version))
            reply = self._recv_handshake(conn)
        except BaseException:
            link.abort("handshake failed")  # 不留下寫入執行緒與看門狗
            raise
        kind = reply["type"]
        if kind == "busy":
            link.abort("busy")
            raise RemoteError(
                "remote_busy", "the compute host is busy with another session"
            )
        if kind == "incompatible":
            link.abort("incompatible")
            raise RemoteError(
                "remote_incompatible",
                "the compute host runs an incompatible version of kikitori",
            )
        if kind != "welcome":
            link.abort("unexpected reply")
            raise RemoteError("remote_error", f"unexpected {kind} during the handshake")
        models = reply["models"]
        self._models = {str(k)[:32]: str(v)[:200] for k, v in list(models.items())[:8]}
        return conn

    def _check_server_identity(self, connection: ClientConnection) -> None:
        sock = connection.socket
        der = (
            sock.getpeercert(binary_form=True)
            if isinstance(sock, ssl.SSLSocket)
            else None
        )
        if not der or not self._servers.is_trusted(spki_sha256_der(der)):
            shutdown_socket(connection.socket)
            raise RemoteError(
                "remote_not_paired",
                "the compute host's key is not the one paired with this computer",
            )

    def _recv_handshake(self, conn: _Conn) -> dict[str, Any]:
        """握手階段收一則訊息（還沒有接收執行緒）。"""
        try:
            message = conn.connection.recv(timeout=HELLO_TIMEOUT)
        except TimeoutError as exc:
            conn.link.abort("no reply")
            raise RemoteError(
                "remote_unreachable", "the compute host did not answer"
            ) from exc
        except ConnectionClosed as exc:
            conn.link.abort("closed during the handshake")
            rcvd = exc.rcvd
            if rcvd is not None and rcvd.code == 1008 and rcvd.reason == "not paired":
                raise RemoteError(
                    "remote_not_paired",
                    "the compute host no longer trusts this computer",
                ) from exc
            raise RemoteError(
                "remote_unreachable", "the compute host closed the connection"
            ) from exc
        try:
            if not isinstance(message, str):
                raise rp.ProtocolViolation("expected a text message")
            reply = rp.parse_server(message)
        except rp.ProtocolViolation as exc:
            conn.link.abort("invalid reply")
            raise RemoteError(
                "remote_error", f"the compute host sent an invalid reply: {exc}"
            ) from exc
        with self._lock:
            self._last_rx = time.monotonic()
        return reply

    def cancel(self) -> None:
        """中斷還在進行的連線（例如使用者在連線時按停止）。可重複呼叫。"""
        self._cancelled.set()
        with self._lock:
            sock = self._sock
            conn = self._conn
        if sock is not None:
            shutdown_socket(sock)
        if conn is not None:
            conn.link.abort("cancelled")

    def cancel_if_connecting(self) -> None:
        """`stop()` 在 `connect()` 還沒完成時呼叫：中斷連線。"""
        with self._lock:
            connected = self._conn is not None
        if not connected:
            self.cancel()

    # --- 執行 -------------------------------------------------------------------

    def start(self, source: AudioSource) -> None:
        """開始擷取、送出 `session.start` 並啟動執行緒（`connect()` 成功之後才可以）。

        Raises:
            RuntimeError: 還沒連上或已經開始過。
            CaptureError: `source.start()` 失敗（連線會被關閉）。
        """
        with self._lock:
            conn = self._conn
        if conn is None or self._started:
            raise RuntimeError("connect() first, and start() only once")
        self._started = True
        try:
            source.start()
        except BaseException:
            conn.link.abort("capture failed")
            raise
        self._source = source
        with self._lock:
            conn.link.send(rp.client_session_start(self._mode, self._settings))
            self._live = True
        self._start_receiver(conn)
        for target, name in (
            (self._capture_loop, "remote-capture"),
            (self._heartbeat_loop, "remote-heartbeat"),
        ):
            thread = threading.Thread(target=target, name=name, daemon=True)
            self._threads.append(thread)
            thread.start()

    def _start_receiver(self, conn: _Conn) -> None:
        thread = threading.Thread(
            target=self._receive_loop, args=(conn,), name="remote-receive", daemon=True
        )
        conn.receiver = thread
        self._threads.append(thread)
        thread.start()

    def set_translation_enabled(self, enabled: bool) -> None:
        """執行中開關翻譯（轉給 B；斷線中的切換會遺失，B 沿用原本的狀態）。"""
        with self._lock:
            conn = self._conn
        if conn is not None:
            conn.link.send(rp.client_set_translation(enabled))

    def stop(self, timeout: float = 5.0) -> None:
        """使用者按停止：請 B 停、有上限地等它回 idle、關連線、等執行緒結束。可重複呼叫。

        返回之後不會再呼叫任何回呼。
        """
        deadline = time.monotonic() + timeout
        self._stopping.set()
        self.cancel_if_connecting()
        with self._lock:
            conn = self._conn
            live = self._live
        if conn is not None and live and self._started and not conn.link.closed:
            conn.link.send(rp.client_session_stop())
            self._remote_ended.wait(min(STOP_WAIT, timeout))
        self.cancel()
        self._join(deadline)
        self._stop_source()
        if conn is not None:
            conn.link.join(max(0.0, deadline - time.monotonic()))
        self._finished.set()

    def _join(self, deadline: float) -> None:
        for thread in list(self._threads):
            if thread is threading.current_thread() or thread.ident is None:
                continue
            thread.join(max(0.0, deadline - time.monotonic()))
            if thread.is_alive():
                logger.warning("%s did not stop in time", thread.name)

    def _stop_source(self) -> None:
        source = self._source
        if source is None:
            return
        try:
            source.stop()
        except Exception:
            logger.exception("stopping the audio source failed")

    def _finish(
        self,
        state: str,
        detail: str | None,
        code: str | None,
        error: BaseException | None = None,
    ) -> None:
        """結束這一場（只送一次；使用者按停止時不送）。"""
        with self._callback_lock:
            with self._lock:
                if self._terminal_sent:
                    return
                self._terminal_sent = True
                self._error = error
                conn = self._conn
            if not self._stopping.is_set():
                self._on_status(state, detail, code, None)
        self._remote_ended.set()
        self._cancelled.set()
        if conn is not None:
            # 告訴 B 這一場結束了（例如 A 的擷取壞了），再正常關閉：直接斷線的話 B 會當成
            # 網路斷了，把 session 與 GPU 保留 45 秒等續接。連線已斷時 send 只是不會成功。
            conn.link.send(rp.client_session_stop())
            conn.link.close(1000, "session ended")
        else:
            with self._lock:
                sock = self._sock
            if sock is not None:
                shutdown_socket(sock)  # 重連中的握手一併中斷
        self._stop_source()
        self._finished.set()

    def _emit_status(self, state: str, data: Mapping[str, Any] | None = None) -> None:
        """非結束狀態；已結束或使用者停止之後不送。"""
        with self._callback_lock:
            if self._terminal_sent or self._stopping.is_set():
                return
            self._on_status(state, None, None, data)

    def _emit_event(self, name: str, data: dict[str, Any]) -> None:
        with self._callback_lock:
            if self._terminal_sent or self._stopping.is_set():
                return
            self._on_event(name, data)

    # --- 音訊 -------------------------------------------------------------------

    def _capture_loop(self) -> None:
        source = self._source
        assert source is not None
        pending = np.zeros(0, dtype=np.int16)
        try:
            while not (self._stopping.is_set() or self._remote_ended.is_set()):
                chunk = source.read(READ_TIMEOUT)
                if len(chunk):
                    pending = (
                        np.concatenate((pending, chunk)) if len(pending) else chunk
                    )
                    while len(pending) >= FRAME_SAMPLES:
                        self._push(pending[:FRAME_SAMPLES])
                        pending = pending[FRAME_SAMPLES:]
                elif getattr(source, "exhausted", False) is True:
                    if len(pending):
                        self._push(pending)
                    with self._lock:
                        self._audio_done = True
                    self._send_end_if_live()
                    return
        except CaptureError as exc:
            logger.error("audio capture failed: %s", exc)
            self._finish("error", str(exc), self._capture_code(exc), exc)
        except Exception as exc:
            logger.exception("sending audio to the compute host failed")
            self._finish("error", f"{type(exc).__name__}: {exc}", "remote_error", exc)

    def _push(self, audio: npt.NDArray[np.int16]) -> None:
        """一個訊框：放進未確認緩衝；連線正常時直接送出（序號與補送共用同一把鎖）。"""
        if self._stopping.is_set():
            return
        with self._lock:
            start = self._captured
            self._captured += len(audio)
            self._frames.append((start, audio))
            self._buffered += len(audio)
            overflow = self._buffered > self._max_buffer
            if self._live and self._conn is not None and not overflow:
                self._conn.link.send(rp.encode_audio(self._seq, start, audio))
                self._seq = (self._seq + 1) % 2**32
        if overflow:
            self._finish(
                "error",
                "the compute host has not confirmed the audio for too long",
                "remote_lost",
            )

    def _send_end_if_live(self) -> None:
        with self._lock:
            if not (self._audio_done and self._live and self._conn is not None):
                return
            if self._end_sent:
                return
            self._end_sent = True
            self._conn.link.send(rp.client_end_audio())

    def _heartbeat_loop(self) -> None:
        while not self._finished.wait(self._heartbeat):
            with self._lock:
                conn = self._conn
                if conn is None:
                    continue  # 重連中
                nonce = self._next_nonce
                self._next_nonce += 1
                self._pings[nonce] = time.perf_counter()
                # 只留最近幾則：B 不回 pong 時不會一直變多
                for old in [n for n in self._pings if n < nonce - 8]:
                    del self._pings[old]
            # Windows 的 monotonic 只有約 15.6 ms 的解析度：RTT 用 perf_counter
            conn.link.send(rp.client_ping(nonce))

    # --- 收訊 -------------------------------------------------------------------

    def _receive_loop(self, conn: _Conn) -> None:
        try:
            while True:
                message = conn.connection.recv(timeout=self._dead_after)
                with self._lock:
                    self._last_rx = time.monotonic()
                if not isinstance(message, str):
                    raise rp.ProtocolViolation("the compute host sent binary data")
                self._dispatch(rp.parse_server(message))
                if self._terminal_sent:
                    return
        except TimeoutError:
            self._lost(
                conn, f"no word from the compute host for {self._dead_after:.0f} s"
            )
        except ConnectionClosed:
            self._lost(conn, "the connection to the compute host was lost")
        except rp.ProtocolViolation as exc:
            logger.warning("the compute host sent an invalid message: %s", exc)
            self._finish(
                "error", f"invalid message from the compute host: {exc}", "remote_error"
            )
        except Exception as exc:
            logger.exception("handling a message from the compute host failed")
            self._finish("error", f"{type(exc).__name__}: {exc}", "remote_error")

    def _dispatch(self, message: dict[str, Any]) -> None:
        kind = message["type"]
        if kind == "status":
            self._on_remote_status(message)
        elif kind == "event":
            self._on_remote_event(message["name"], message["data"])
        elif kind == "pong":
            with self._lock:
                sent = self._pings.pop(message["nonce"], None)
                if sent is not None:
                    self._rtt = time.perf_counter() - sent
        elif kind == "ack":
            self._on_ack(message["sample"])
        elif kind == "session.started":
            with self._lock:
                if self._remote_id is not None:
                    raise rp.ProtocolViolation("a second session.started")
                self._remote_id = message["session_id"]
        else:  # welcome／busy／incompatible／resumed 只會在握手時出現
            raise rp.ProtocolViolation(f"unexpected {kind}")

    def _on_ack(self, sample: int) -> None:
        with self._lock:
            if sample > self._captured:
                raise rp.ProtocolViolation("ack beyond the audio sent")
            while (
                self._frames and self._frames[0][0] + len(self._frames[0][1]) <= sample
            ):
                _, audio = self._frames.popleft()
                self._buffered -= len(audio)

    def _on_remote_status(self, message: dict[str, Any]) -> None:
        state = message["state"]
        detail = message.get("detail")
        if state in ("loading", "running"):
            self._emit_status(state, message.get("data"))
            return
        if state == "idle":
            self._finish("idle", None, None)
            return
        code = message.get("code")
        mapped = (
            "remote_model_not_downloaded"
            if code in ("model_not_downloaded", "unknown_model")
            else "remote_error"
        )
        self._finish("error", f"compute host: {detail or code or 'error'}", mapped)

    def _on_remote_event(self, name: str, data: dict[str, Any]) -> None:
        if self._stopping.is_set():
            return
        if name == "log":
            level = _LOG_LEVELS.get(str(data.get("level")), logging.WARNING)
            text = data.get("message")
            if isinstance(text, str):
                # 經 kikitori 的 log handler 轉成介面上的 log 事件
                logger.log(level, "compute host: %s", text[:MAX_LOG_CHARS])
            return
        checked = (
            self._check_subtitle(data) if name == "subtitle" else _check_metrics(data)
        )
        if checked is None:
            self._bad_events += 1
            logger.warning("dropping an invalid %s event from the compute host", name)
            if self._bad_events > MAX_BAD_EVENTS:
                raise rp.ProtocolViolation("too many invalid events")
            return
        self._bad_events = 0  # 只算連續的不合格事件
        if name == "subtitle":
            with self._lock:
                # 續接時 B 會補送最近的完整字幕：已經顯示過的（以及它們的即時字幕）丟掉
                if checked["utterance_id"] <= self._last_final_id:
                    return
                if checked["is_final"]:
                    self._last_final_id = checked["utterance_id"]
        if name == "metrics":
            checked["rtt_ms"] = self.rtt_ms
        self._emit_event(name, checked)

    # --- 斷線續接 ---------------------------------------------------------------

    def _lost(self, conn: _Conn, detail: str) -> None:
        """這條連線斷了：還能續接就在背景重連，不行就結束。"""
        conn.link.abort(detail)
        if self._stopping.is_set() or self._terminal_sent:
            return
        with self._lock:
            if self._conn is not conn:
                return  # 已經換成新的連線（舊的接收執行緒晚一步結束）
            self._conn = None
            self._live = False
            resumable = self._remote_id is not None
        if not resumable:
            self._finish("error", detail, "remote_lost")
            return
        logger.warning("lost the compute host (%s); reconnecting", detail)
        self._emit_status("running", {"reconnecting": True})
        thread = threading.Thread(
            target=self._reconnect_loop, name="remote-reconnect", daemon=True
        )
        self._threads.append(thread)
        thread.start()

    def _reconnect_loop(self) -> None:
        try:
            self._reconnect()
        except Exception as exc:
            # 程式錯誤也不能讓這條執行緒靜默結束：介面會永遠停在「重新連線中」
            logger.exception("reconnecting to the compute host failed")
            self._finish("error", f"{type(exc).__name__}: {exc}", "remote_error")

    def _reconnect(self) -> None:
        with self._lock:
            deadline = self._last_rx + self._reconnect_secs
        attempt = 0
        last_error = "the connection to the compute host was lost"
        while not (self._stopping.is_set() or self._terminal_sent):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            try:
                conn = self._open()
                outcome = self._resume(conn)
            except RemoteError as exc:
                if exc.code in _FATAL_CONNECT_CODES:
                    self._finish("error", str(exc), exc.code)
                    return
                outcome = "retry"
                last_error = str(exc)
            if outcome != "retry":
                return  # 接回來了，或 _resume 已經結束這一場
            delay = _BACKOFF[min(attempt, len(_BACKOFF) - 1)]
            attempt += 1
            if self._stopping.wait(min(delay, max(0.0, remaining))):
                return
        if not self._stopping.is_set():
            self._finish(
                "error",
                f"could not reconnect within {self._reconnect_secs:.0f} s: {last_error}",
                "remote_lost",
            )

    def _resume(self, conn: _Conn) -> str:
        """在新連線上接回這一場、補送音訊。

        Returns:
            `"ok"` 接回來了；`"retry"` 這條連線在續接握手時又斷了（暫時性，繼續重試）；
            `"ended"` B 拒絕或回應不合格，已經結束這一場。
        """
        with self._lock:
            remote_id = self._remote_id
            last_final = self._last_final_id
            acked = self._captured - self._buffered
        assert remote_id is not None
        conn.link.send(rp.client_session_resume(remote_id, last_final))
        try:
            reply = self._recv_handshake(conn)
        except RemoteError as exc:
            conn.link.abort("resume handshake failed")
            if exc.code == "remote_error":  # B 送來不合格的回應：不值得再試
                self._finish("error", str(exc), "remote_error")
                return "ended"
            return "retry"
        if reply["type"] == "resume_failed":
            conn.link.abort("resume failed")
            self._finish(
                "error",
                f"the compute host could not resume the session: {reply['reason']}",
                "remote_lost",
            )
            return "ended"
        if reply["type"] != "resumed" or reply["session_id"] != remote_id:
            conn.link.abort("unexpected reply")
            self._finish(
                "error", f"unexpected {reply['type']} on resume", "remote_error"
            )
            return "ended"
        sample = reply["sample"]
        with self._lock:
            captured = self._captured
        # B 的回傳不可信：只能落在「已確認」與「已擷取」之間，否則時間軸會錯
        if not acked <= sample <= captured:
            conn.link.abort("bad resume position")
            self._finish(
                "error",
                "the compute host reported an impossible audio position",
                "remote_error",
            )
            return "ended"
        with self._lock:
            self._conn = conn
            self._seq = 0
        self._start_receiver(conn)
        self._resend(conn, sample)
        if not self._stopping.is_set() and not self._terminal_sent:
            logger.info("reconnected to the compute host")
            if reply["state"] in ("loading", "running"):
                self._emit_status(reply["state"], {"reconnecting": False})
        return "ok"

    def _resend(self, conn: _Conn, position: int) -> None:
        """從 `position` 起依序補送緩衝的音訊（限速），追上之後改回直接送出。"""
        while not (self._stopping.is_set() or self._terminal_sent):
            with self._lock:
                if self._conn is not conn:
                    return  # 補送中又斷線：新的重連會從 B 回報的位置重來
                frame = next(
                    (
                        (start, audio)
                        for start, audio in self._frames
                        if start + len(audio) > position
                    ),
                    None,
                )
                if frame is None:
                    # 追上了：之後擷取的音訊直接送出（在同一把鎖內切換，不會漏送或重複）
                    self._live = True
                    break
                start, audio = frame
                if start < position:
                    audio = audio[position - start :]
                    start = position
                conn.link.send(rp.encode_audio(self._seq, start, audio))
                self._seq = (self._seq + 1) % 2**32
                position = start + len(audio)
            self._stopping.wait(len(audio) / rp.SAMPLE_RATE / self._resend_speed)
        self._send_end_if_live()

    # --- 驗證 B 的回傳 -----------------------------------------------------------

    def _check_subtitle(self, data: dict[str, Any]) -> dict[str, Any] | None:
        """只留認得的欄位並驗證；不合格回 None。延遲加上單程時間（RTT/2）。"""
        try:
            out: dict[str, Any] = {
                "utterance_id": _int(data["utterance_id"], 1, 2**53),
                "original": _text(data["original"]),
                "translation": _text(data["translation"]),
                "source_language": _language(data["source_language"]),
                "target_language": _language(data["target_language"]),
                "is_final": _bool(data["is_final"]),
            }
            with self._lock:
                captured = self._captured
            start = _int(data["start_sample"], 0, captured)
            end = _int(data["end_sample"], start, captured)
            out["start_sample"], out["end_sample"] = start, end
            if out["is_final"]:
                latency = data.get("latency_ms")
                if latency is not None:
                    latency = _int(latency, 0, 24 * 3600 * 1000)
                    rtt = self.rtt_ms
                    if rtt is not None:
                        latency += round(rtt / 2)
                out["latency_ms"] = latency
                voice = data.get("voice")
                if voice is not None:
                    out["voice"] = _int(voice, 1, MAX_VOICE)
            else:
                out["stable_chars"] = _int(
                    data.get("stable_chars", 0), 0, MAX_TEXT_CHARS
                )
                out["translation_stable_chars"] = _int(
                    data.get("translation_stable_chars", 0), 0, MAX_TEXT_CHARS
                )
            speaker = data.get("speaker")
            if speaker is not None:
                if speaker not in ("self", "partner"):
                    return None
                out["speaker"] = speaker
        except (KeyError, _Invalid):
            return None
        return out


class _Invalid(Exception):
    """B 送來的某個欄位不合格（整則事件丟掉）。"""


def _check_metrics(data: dict[str, Any]) -> dict[str, Any] | None:
    out: dict[str, Any] = {}
    try:
        for key in (
            "level_dbfs",
            "speech_level_dbfs",
            "background_level_dbfs",
        ):
            value = data.get(key)
            out[key] = None if value is None else _float(value, -200.0, 50.0)
        out["backlog"] = _int(data.get("backlog", 0), 0, 1_000_000)
        median = data.get("latency_ms_median")
        out["latency_ms_median"] = (
            None if median is None else _int(median, 0, 24 * 3600 * 1000)
        )
    except _Invalid:
        return None
    return out


def _int(value: object, low: int, high: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise _Invalid(f"expected an integer in [{low}, {high}]")
    return value


def _float(value: object, low: float, high: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise _Invalid("expected a number")
    number = float(value)
    if not low <= number <= high:
        raise _Invalid(f"expected a number in [{low}, {high}]")
    return number


def _bool(value: object) -> bool:
    if not isinstance(value, bool):
        raise _Invalid("expected true or false")
    return value


def _text(value: object) -> str:
    if not isinstance(value, str) or len(value) > MAX_TEXT_CHARS:
        raise _Invalid("expected a string")
    return value


def _language(value: object) -> str:
    if not isinstance(value, str) or not _LANGUAGE.match(value):
        raise _Invalid("expected a language code")
    return value
