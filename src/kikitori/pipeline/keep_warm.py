"""長時間 session 中讓本機翻譯伺服器保留模型：閒置太久就送一次很短的預熱請求。

位於 pipeline 層的背景工作：`factory.attach_keep_warm()` 建立它並把兩個翻譯器包成
`ActivityTranslator`（每次翻譯都算活動），`Pipeline.start()` 啟動、pipeline 以任何方式
結束時停止。為什麼需要：Ollama 預設閒置 5 分鐘就卸載模型，而 OpenAI 相容端點不能指定
`keep_alive`；影片長時間沒有對白之後，下一句的即時翻譯會因為模型重新載入而逾時。

本模組不知道翻譯細節，只呼叫注入的 `ping`；時間與等待也都可注入，測試不必真的睡。
"""

import logging
import math
import threading
import time
from collections.abc import Callable, Iterator
from typing import Protocol

from kikitori.translate.errors import TranslationError
from kikitori.translate.types import TranslationRequest, Translator

logger = logging.getLogger(__name__)

WARN_INTERVAL = 30 * 60.0
"""連續失敗時 warning 的最短間隔（秒）；之間的失敗只記 debug。伺服器沒開時每個間隔都會
失敗，不限流的話日誌與 UI 會被同一則訊息洗版。"""


class KeepWarm:
    """翻譯請求停了 `interval` 秒就呼叫一次 `ping`，直到 `stop()`。

    `ping` 本身也算活動，所以閒置期間每 `interval` 秒送一次。`ping` 在背景執行緒上
    執行，不會擋住 pipeline；`TranslationError` 記 warning（限流），其他例外記 error，
    兩者都不會讓執行緒結束。

    只顯示原文（翻譯關閉）時是「不動作」（`active=False`）：不送任何請求，`start()` 也
    不建立執行緒，翻譯伺服器完全不被打擾。執行中開啟翻譯（`set_active(True)`）時才建立
    執行緒，並先在背景送一次預熱——session 開始時沒有預熱過，模型多半還沒載入。
    """

    def __init__(
        self,
        ping: Callable[[], None],
        interval: float,
        *,
        clock: Callable[[], float] = time.monotonic,
        wait: Callable[[float], bool] | None = None,
        warn_interval: float = WARN_INTERVAL,
        active: bool = True,
    ) -> None:
        """
        Args:
            ping: 送一次保溫請求；失敗時丟 `TranslationError`（應自帶逾時，見 `factory`）。
            interval: 閒置多少秒才送（> 0）。
            clock: 單調時鐘（秒）；測試可注入假時鐘。
            wait: 背景執行緒等待 `timeout` 秒（`stop()`、`set_active(True)` 會提早
                喚醒），已停止時回傳 True。測試可注入「推進假時鐘」的版本。
            warn_interval: 連續失敗時 warning 的最短間隔（秒）。
            active: False 表示翻譯目前關閉：不送請求，直到 `set_active(True)`。

        Raises:
            ValueError: `interval` 不是正的有限數。
        """
        if not (math.isfinite(interval) and interval > 0):
            raise ValueError(f"interval must be a positive number, got {interval!r}")
        self._ping = ping
        self.interval = float(interval)
        self._clock = clock
        self._stop = threading.Event()
        # 讓等待中的背景執行緒提早醒來：stop() 與 set_active(True)（要立即預熱）
        self._wake = threading.Event()
        self._wait = wait or self._default_wait
        self._warn_interval = warn_interval
        self._lock = threading.Lock()
        self._last_activity = clock()
        self._in_flight = 0
        self._last_warning: float | None = None
        self._failing = False
        self._thread: threading.Thread | None = None
        self._started = False
        self._active = active
        self._ping_requested = False

    # --- 活動 -------------------------------------------------------------------

    def touch(self) -> None:
        """記錄一次翻譯活動。執行緒安全。"""
        now = self._clock()
        with self._lock:
            self._last_activity = max(self._last_activity, now)

    def begin(self) -> None:
        """一個翻譯請求開始；在對應的 `end()` 之前不算閒置。"""
        with self._lock:
            self._in_flight += 1
        self.touch()

    def end(self) -> None:
        """`begin()` 的那個請求結束（成功或失敗）。"""
        with self._lock:
            self._in_flight = max(0, self._in_flight - 1)
        self.touch()

    def idle_for(self) -> float:
        """距上次活動幾秒；有請求進行中時為 0（請求可能比間隔還久）。"""
        with self._lock:
            if self._in_flight:
                return 0.0
            return self._clock() - self._last_activity

    # --- 排程 -------------------------------------------------------------------

    @property
    def active(self) -> bool:
        """翻譯是否開啟（關閉時不送任何請求）。"""
        with self._lock:
            return self._active

    def set_active(self, active: bool) -> None:
        """翻譯開啟或關閉（執行緒安全、不阻塞）。

        從關閉變成開啟時，要求背景執行緒立即送一次預熱；已 `start()` 但還沒有執行緒
        （以關閉狀態開始的 session）時在這裡建立。`stop()` 之後不再建立。
        """
        with self._lock:
            turned_on = active and not self._active
            self._active = active
            if turned_on:
                self._ping_requested = True
            spawn = (
                active
                and self._started
                and self._thread is None
                and not self._stop.is_set()
            )
        if spawn:
            self._spawn()
        self._wake.set()

    def tick(self) -> float:
        """要求預熱時立即送；否則閒置滿 `interval` 才送。回傳下次該檢查前要等幾秒。

        翻譯關閉時什麼都不送。
        """
        if self._stop.is_set():
            return self.interval
        with self._lock:
            if not self._active:
                return self.interval
            requested, self._ping_requested = self._ping_requested, False
        if requested:
            logger.info("translation turned on; warming up the translation model")
            self.ping_now()
            return self.interval
        idle = self.idle_for()
        if idle < self.interval:
            return self.interval - idle
        if self._stop.is_set():
            return self.interval
        logger.info(
            "translation idle for %.0f s; sending a keep-warm request to the local server",
            idle,
        )
        self.ping_now()
        return self.interval

    def ping_now(self) -> None:
        """立即送一次保溫請求（算一次活動）；失敗只記 log，不往外拋。"""
        try:
            self._ping()
        except TranslationError as exc:
            self._log_failure(exc)
        except Exception as exc:  # noqa: BLE001 - 背景執行緒的邊界：程式錯誤也只記錄（限流），執行緒不能因此結束
            self._log_failure(exc, unexpected=True)
        else:
            if self._failing:
                logger.info("keep-warm request succeeded again")
            self._failing = False
            self._last_warning = None
        finally:
            self.touch()

    def _log_failure(self, exc: Exception, *, unexpected: bool = False) -> None:
        """伺服器的問題（`TranslationError`）記 warning；其他例外是程式錯誤，記 error 與
        traceback。兩者共用限流：`warn_interval` 內重複的失敗只記 debug。"""
        now = self._clock()
        self._failing = True
        message = "keep-warm request to the translation server failed: %s: %s"
        if (
            self._last_warning is None
            or now - self._last_warning >= self._warn_interval
        ):
            self._last_warning = now
            logger.log(
                logging.ERROR if unexpected else logging.WARNING,
                message + " (repeats within %.0f s are logged at debug level)",
                type(exc).__name__,
                exc,
                self._warn_interval,
                exc_info=exc if unexpected else None,
            )
        else:
            logger.debug(message, type(exc).__name__, exc)

    # --- 執行緒 -----------------------------------------------------------------

    @property
    def stopped(self) -> bool:
        """`stop()` 是否已被呼叫。"""
        return self._stop.is_set()

    @property
    def is_running(self) -> bool:
        """背景執行緒是否還在執行（可能正在送最後一個請求）。"""
        thread = self._thread
        return thread is not None and thread.is_alive()

    def start(self) -> None:
        """開始排程；閒置時間從現在算起。翻譯關閉時先不建立執行緒（見 `set_active()`）。

        Raises:
            RuntimeError: 已經啟動過，或無法建立執行緒。
        """
        with self._lock:
            if self._started:
                raise RuntimeError("KeepWarm can only be started once")
            self._started = True
            spawn = self._active
        self.touch()
        if spawn:
            self._spawn()

    def _spawn(self) -> None:
        with self._lock:
            if self._thread is not None:
                return
            self._thread = threading.Thread(
                target=self._run, name="translation-keep-warm", daemon=True
            )
            thread = self._thread
        thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        """停止排程；可重複呼叫。

        不等正在進行的請求（最多是它自己的讀取逾時）：執行緒是 daemon，請求結束後就
        自行結束，之後不會再送。`timeout` 只是給它一點時間乾淨地結束。
        """
        self._stop.set()
        self._wake.set()
        with self._lock:
            thread = self._thread
        if thread is None or thread is threading.current_thread():
            return
        thread.join(timeout)
        if thread.is_alive():
            logger.debug("keep-warm request still in flight; it ends on its own")

    def _default_wait(self, timeout: float) -> bool:
        self._wake.wait(timeout)
        # 先清旗標再看 stop：清掉之後才設定的喚醒會留到下一次等待，不會遺失
        self._wake.clear()
        return self._stop.is_set()

    def _run(self) -> None:
        while not self._wait(self.tick()):
            if self._stop.is_set():
                return


class WarmUpOnEnable:
    """`translation.keep_warm_secs = 0`（不定期保溫）時用：執行中開啟翻譯才在背景預熱一次。

    為什麼 0 也需要：以「只顯示原文」開始的 session 沒有開場預熱，執行中開啟翻譯時
    模型多半還沒載入，第一句的即時翻譯會因冷啟動（4–10 秒）逾時。0 的意思是「不要
    定期送請求」，不是「開啟翻譯時也不預熱」。與 `KeepWarm.set_active()` 相同的
    `start()`／`stop()`／`set_active()` 介面，pipeline 不必分辨。

    預熱在 daemon 執行緒上執行，不阻塞呼叫端；同時只有一個預熱在進行（快速連續切換
    時不會堆積請求）。失敗只記 warning（程式錯誤記 error），不影響 session。
    """

    def __init__(self, ping: Callable[[], None], *, active: bool) -> None:
        """
        Args:
            ping: 送一次預熱請求；失敗時丟 `TranslationError`（應自帶逾時）。
            active: 翻譯目前是否開啟；開啟中的 session 已在開始時預熱過。
        """
        self._ping = ping
        self._lock = threading.Lock()
        self._active = active
        self._started = False
        self._stopped = False
        self._thread: threading.Thread | None = None

    @property
    def active(self) -> bool:
        """翻譯是否開啟。"""
        with self._lock:
            return self._active

    def start(self) -> None:
        """開始接受切換（不送任何請求：開場預熱已由 `warm_up_translation()` 處理）。"""
        with self._lock:
            self._started = True

    def set_active(self, active: bool) -> None:
        """翻譯開啟或關閉（執行緒安全、不阻塞）；從關閉變成開啟時在背景預熱一次。"""
        with self._lock:
            turned_on = active and not self._active
            self._active = active
            busy = self._thread is not None and self._thread.is_alive()
            if not turned_on or not self._started or self._stopped or busy:
                return
            self._thread = threading.Thread(
                target=self._run, name="translation-warm-up", daemon=True
            )
            thread = self._thread
        thread.start()

    def stop(self, timeout: float = 0.5) -> None:
        """之後不再預熱；不等進行中的請求（daemon，請求結束後自行結束）。可重複呼叫。"""
        with self._lock:
            self._stopped = True

    def _run(self) -> None:
        logger.info("translation turned on; warming up the translation model")
        try:
            self._ping()
        except TranslationError as exc:
            logger.warning(
                "cannot warm up the translation model: %s: %s", type(exc).__name__, exc
            )
        except Exception:
            logger.exception("warming up the translation model failed")


class RequestTracker(Protocol):
    """`ActivityTranslator` 回報請求開始與結束的對象（`KeepWarm` 實作它）。"""

    def begin(self) -> None: ...

    def end(self) -> None: ...


class ActivityTranslator:
    """把每次翻譯的開始與結束回報給 `tracker` 的 `Translator` 包裝。"""

    def __init__(self, inner: Translator, tracker: RequestTracker) -> None:
        self._inner = inner
        self._tracker = tracker

    def translate(self, request: TranslationRequest) -> str:
        """同 `Translator.translate`；例外原樣往外拋。"""
        self._tracker.begin()
        try:
            return self._inner.translate(request)
        finally:
            self._tracker.end()

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        """同 `Translator.stream`；串流結束（含中斷、例外）時才算請求結束。"""
        self._tracker.begin()
        try:
            yield from self._inner.stream(request)
        finally:
            self._tracker.end()
