"""在背景工作執行緒上處理耗時的請求，完成後才寫出回應。

位於 engine 的分派層與 server 之間。stdin 在主執行緒依序處理，而 `hardware.detect`
這類請求會執行 `nvidia-smi`、連線翻譯伺服器（數秒）；若在主執行緒上等待，期間
`session.stop`、`engine.shutdown` 都會被卡住。這裡用「一條」工作執行緒依收到的順序
執行它們：同類請求之間的回應順序與請求順序相同，也不會同時跑兩次偵測；與其他
（立即回應的）請求之間則以 `id` 對應，回應可能晚於之後送出的請求。
"""

import logging
import queue
import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

from kikitori.engine import protocol as p
from kikitori.engine.io import ChannelClosedError

logger = logging.getLogger(__name__)

Job = Callable[[], Mapping[str, Any]]


@dataclass(frozen=True)
class Deferred:
    """處理函式回傳它，表示「回應由背景工作稍後寫出」。

    Attributes:
        job: 在工作執行緒上執行，回傳結果物件；丟 `ProtocolError` 會變成錯誤回應。
        after: 成功回應寫出之後才執行（同一條工作執行緒）；例如啟動背景下載，讓 UI
            一定先拿到回應、才收到進度事件。工作失敗時不執行。
        unsent: `job` 成功、但回應沒能寫出時執行（`after` 不會執行）：撤銷 `job` 預先
            保留的東西（例如登記好、還沒啟動的下載），否則它會一直留著。
    """

    job: Job
    after: Callable[[], None] | None = None
    unsent: Callable[[], None] | None = None


@dataclass(frozen=True)
class _Task:
    request_id: int
    method: str
    job: Job
    after: Callable[[], None] | None = None
    unsent: Callable[[], None] | None = None


class DeferredRunner:
    """一條工作執行緒、FIFO 佇列；每個工作的結果或錯誤寫成一則回應。"""

    def __init__(
        self,
        send: Callable[[Mapping[str, Any]], None],
        *,
        name: str = "engine-worker",
    ) -> None:
        """
        Args:
            send: 寫出一則協定訊息（`MessageWriter.send`）。
            name: 工作執行緒名稱（出現在 log 與除錯工具中）。
        """
        self._send = send
        self._name = name
        self._queue: queue.Queue[_Task | None] = queue.Queue()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._closed = False

    def submit(
        self,
        request_id: int,
        method: str,
        job: Job,
        after: Callable[[], None] | None = None,
        unsent: Callable[[], None] | None = None,
    ) -> None:
        """排入一個工作；第一次呼叫時才啟動執行緒（大多數 session 用不到它）。

        Raises:
            RuntimeError: 已呼叫過 `close()`。
        """
        with self._lock:
            if self._closed:
                raise RuntimeError("the engine is shutting down")
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name=self._name, daemon=True
                )
                self._thread.start()
            self._queue.put(_Task(request_id, method, job, after, unsent))

    def close(self, timeout: float) -> None:
        """不再接受新工作；等目前的工作在 `timeout` 秒內結束，尚未開始的直接丟棄。

        偵測本身都有逾時（nvidia-smi 5 s、翻譯伺服器 2 s），等不到時記 warning 放手
        （daemon 執行緒，行程結束時一併終止），不讓引擎卡在結束階段。
        """
        with self._lock:
            self._closed = True
            thread = self._thread
        dropped = 0
        while True:
            try:
                task = self._queue.get_nowait()
            except queue.Empty:
                break
            if task is not None:
                dropped += 1
        if dropped:
            logger.info("dropped %d pending request(s) at shutdown", dropped)
        self._queue.put(None)
        if thread is not None:
            thread.join(timeout)
            if thread.is_alive():
                logger.warning("%s did not finish within %.1f s", self._name, timeout)

    def _run(self) -> None:
        while True:
            task = self._queue.get()
            if task is None:
                return
            response: dict[str, Any] | None = None
            try:
                response = self._execute(task)
                self._send(response)
                if task.after is not None and "result" in response:
                    self._run_after(task)
            except ChannelClosedError:
                self._run_unsent(task, response)
                logger.error("%s: protocol output closed", task.method)
                return
            except (TypeError, ValueError) as exc:
                self._run_unsent(task, response)
                # 結果無法序列化（例如 NaN）是程式錯誤；仍要回應這個請求，且工作執行緒
                # 不能因此結束，否則之後的背景請求永遠等不到回應。
                logger.exception("%s: cannot encode the result", task.method)
                try:
                    self._send(
                        p.error_response(
                            task.request_id,
                            p.INTERNAL_ERROR,
                            f"cannot encode the result: {type(exc).__name__}",
                        )
                    )
                except ChannelClosedError:
                    logger.error("%s: protocol output closed", task.method)
                    return

    def _run_after(self, task: _Task) -> None:
        assert task.after is not None
        try:
            task.after()
        except Exception:
            # 回應已經寫出，無法再改成錯誤；記錄下來，工作執行緒繼續服務之後的請求
            logger.exception("%s: follow-up after the response failed", task.method)

    def _run_unsent(self, task: _Task, response: dict[str, Any] | None) -> None:
        """成功的結果沒寫出去：`after` 不會執行，改執行 `unsent` 撤銷 `job` 保留的東西。

        `_send` 失敗前一定已經拿到 `response`；它是錯誤回應（`job` 失敗）時 `job` 沒有
        保留任何東西，不必撤銷。`after` 本身失敗不會走到這裡（`_run_after` 已處理）。
        """
        if task.unsent is None or response is None or "result" not in response:
            return
        try:
            task.unsent()
        except Exception:
            logger.exception(
                "%s: cleanup after the unsent response failed", task.method
            )

    def _execute(self, task: _Task) -> dict[str, Any]:
        try:
            result = task.job()
        except p.ProtocolError as exc:
            return p.error_response(task.request_id, exc.code, exc.message)
        except ChannelClosedError:
            raise
        except Exception as exc:
            logger.exception("method %s failed", task.method)
            return p.error_response(
                task.request_id, p.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
            )
        return p.response(task.request_id, result)
