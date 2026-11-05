"""雙路徑協調器：AudioSource → Segmenter → 即時／完整路徑 → `SubtitleUpdate`。

位於 capture、vad、asr、translate 之上，engine 之下。句子進行中由即時路徑
（較小的 ASR 模型、只做最新的一次）送出會被取代的字幕；句子結束後完整路徑
（較準的模型、帶前文）送出最終版。設計與保證（順序、不丟完整結果、背壓、錯誤）
見 `docs/specs/pipeline-engine.md`。

時間一律用樣本計數；pipeline 本身不讀牆鐘時間（注入的時鐘只用來排程即時辨識與量測
完整字幕的延遲）。除了引擎用的執行緒模式，
另有不開執行緒的逐步模式（`feed()`／`end_of_stream()`／`run_pending()`），
讓測試能決定性地重現排程情境。
"""

import dataclasses
import logging
import threading
import time
from collections import deque
from collections.abc import Callable, Iterator
from typing import Literal

import numpy as np
import numpy.typing as npt

from kikitori.asr.types import Transcript
from kikitori.capture.types import AudioSource
from kikitori.pipeline.languages import (
    Route,
    base_language,
    copy_translation,
    display_original,
    route,
)
from kikitori.pipeline.metrics import LatencyWindow, LevelMeter, PipelineMetrics
from kikitori.pipeline.scheduler import FinalQueue, Job, LatestSlot
from kikitori.pipeline.stability import PartialStability
from kikitori.pipeline.types import (
    LogCallback,
    PipelineComponents,
    PipelineConfig,
    PipelineState,
    StatusCallback,
    SubtitleUpdate,
)
from kikitori.translate.errors import TranslationError
from kikitori.translate.types import TranslationRequest
from kikitori.vad import (
    SAMPLE_RATE,
    PauseEndpoint,
    Segmenter,
    SegmentEvent,
    UtteranceAudio,
    UtteranceEnded,
    UtteranceStarted,
)

KEEP_TAIL_SECS = 0.2
"""`pause` 結束的句子裁掉句尾靜音時保留的長度：Whisper 在字尾突然截斷時容易漏字。"""

JOIN_TIMEOUT_FLOOR = 0.05

logger = logging.getLogger(__name__)


class _Utterance:
    """capture 執行緒上進行中的一句話（只有 capture 執行緒讀寫）。"""

    def __init__(self, utterance_id: int, start_sample: int) -> None:
        self.id = utterance_id
        self.start_sample = start_sample
        self.chunks: list[npt.NDArray[np.int16]] = []
        self.length = 0
        self.last_trigger = 0

    def append(self, audio: npt.NDArray[np.int16]) -> None:
        self.chunks.append(audio)
        self.length += len(audio)

    def audio(self) -> npt.NDArray[np.int16]:
        if not self.chunks:
            return np.zeros(0, dtype=np.int16)
        joined = np.concatenate(self.chunks)
        # 合併後只保留一塊，下一次觸發不必重新串接全部片段。
        self.chunks = [joined]
        return joined


class Pipeline:
    """把音訊轉成字幕更新的雙路徑協調器。

    一個 `Pipeline` 只跑一次：執行緒模式 `start()` → `stop()`／讀完；或逐步模式
    `feed()`／`end_of_stream()`／`run_pending()`，兩者不可混用。
    """

    def __init__(
        self,
        components: PipelineComponents,
        config: PipelineConfig,
        *,
        on_subtitle: Callable[[SubtitleUpdate], None],
        on_status: StatusCallback | None = None,
        on_log: LogCallback | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            components: VAD 模型、兩個 transcriber、兩個翻譯器與非語音過濾。
            config: 模式與排程參數。
            on_subtitle: 每則字幕更新；在 worker 執行緒上呼叫，丟出例外視為致命錯誤。
            on_status: 狀態改變（"running"、"idle"、"error" + 說明）。
            on_log: 可恢復的警告（例如翻譯失敗）。
            clock: 量測即時辨識耗時（觸發間隔）與完整字幕延遲（`latency_ms`）用的時鐘
                （秒）；不影響音訊位置。測試可注入假時鐘。
        """
        self._components = components
        self._config = config
        self._on_subtitle = on_subtitle
        self._on_status = on_status
        self._on_log = on_log
        self._segmenter = Segmenter(
            components.voice_model,
            endpoint=PauseEndpoint(config.pause_secs, config.max_secs),
        )
        self._interval = max(1, round(config.partial_interval_ms * SAMPLE_RATE / 1000))
        self._clock = clock
        # 上一次即時辨識的耗時（換成樣本數）。Whisper 每次都要跑完整的 30 秒 encoder，
        # 耗時幾乎與句長無關；比間隔還慢時就拉長間隔，避免 CPU 被即時路徑佔滿、
        # 拖慢完整路徑。由即時 worker 寫、capture 執行緒讀（int 指定是原子的）。
        self._partial_cost = 0
        # 即時路徑最後一次看到的 no_speech（依句子）：完整模型的 no_speech
        # 不可靠時（例如 large-v3-turbo 恆為 0）改用它來過濾。
        self._fast_no_speech: dict[int, float] = {}
        self._tail_trim = max(
            0, round((config.pause_secs - KEEP_TAIL_SECS) * SAMPLE_RATE)
        )

        self._slot = LatestSlot()
        self._finals = FinalQueue(config.final_queue_size)
        # 即時路徑的穩定前綴與「要不要重送即時翻譯」（只有即時 worker 使用）
        self._stability = PartialStability()
        # metrics：擷取音訊的音量（capture 執行緒寫）與完整字幕延遲（完整 worker 寫）
        self._level = LevelMeter(SAMPLE_RATE)
        self._latencies = LatencyWindow()

        # 送出字幕與前文都在這把鎖下，保證同一句的即時結果不會晚於完整結果。
        self._emit_lock = threading.Lock()
        self._last_final_id = 0
        self._partial_shown: set[int] = set()
        self._history: deque[tuple[str, str]] = deque(
            maxlen=max(config.context_lines, 1)
        )

        self._state_lock = threading.Lock()
        self._state: PipelineState = "idle"
        self._error: BaseException | None = None
        self._run_mode: Literal["threaded", "stepped"] | None = None
        self._stop = threading.Event()
        self._input_done = threading.Event()
        self._done = threading.Event()
        self._warnings: dict[str, str] = {}
        self._warn_lock = threading.Lock()

        # 以下只由 capture 執行緒（或逐步模式的呼叫端）讀寫；_ended_id 另由 worker 讀取。
        self._next_id = 1
        self._current: _Utterance | None = None
        self._ended_id = 0
        self._source: AudioSource | None = None
        self._threads: list[threading.Thread] = []
        self._keep_warm_started = False
        # 執行中可切換的「是否翻譯」（session.set_translation）：每句在決定路由時讀一次，
        # 所以之後的句子依新狀態處理、已送出的句子不受影響。bool 的讀寫是原子的，worker
        # 讀到舊值或新值都是合法的狀態，不需要鎖。
        self._translation_enabled = config.translation_enabled

    # --- 公開狀態 -------------------------------------------------------------

    @property
    def state(self) -> PipelineState:
        """ "idle"（未開始或已結束）、"running" 或 "error"。"""
        with self._state_lock:
            return self._state

    @property
    def error(self) -> BaseException | None:
        """讓這次執行以 "error" 結束的例外（`on_status("error", …)` 送出前就已設定）；否則 None。

        engine 用它把擷取錯誤（例如 `DeviceLostError`）對應到 status 的 `code`。
        """
        with self._state_lock:
            return self._error

    @property
    def translation_enabled(self) -> bool:
        """目前是否翻譯（初始值為 `PipelineConfig.translation_enabled`）。"""
        return self._translation_enabled

    def set_translation_enabled(self, enabled: bool) -> None:
        """執行中開關翻譯（執行緒安全，可在任何執行緒、任何時候呼叫）。

        之後才決定路由的句子依新狀態處理；已送出的字幕不補翻、也不清掉譯文。元件有保溫
        工作且支援 `set_active()` 時一併切換：關閉翻譯時不再對翻譯伺服器送保溫請求（不佔
        GPU），開啟時由保溫工作在背景先送一次預熱（不阻塞呼叫端）。

        Raises:
            TypeError: `enabled` 不是 bool。
        """
        if not isinstance(enabled, bool):
            raise TypeError(f"enabled must be bool, got {type(enabled).__name__}")
        self._translation_enabled = enabled
        logger.info("translation %s", "enabled" if enabled else "disabled")
        set_active = getattr(self._components.keep_warm, "set_active", None)
        if not callable(set_active):
            return
        try:
            set_active(enabled)
        except Exception as exc:  # 保溫只是加速；切換本身已生效，記錄並回報
            logger.warning("cannot switch the keep-warm task", exc_info=exc)
            self._log(
                "warning",
                f"cannot switch the keep-warm task: {type(exc).__name__}: {exc}",
            )

    def metrics(self) -> PipelineMetrics:
        """目前的音量、待處理的完整句數與延遲中位數（執行緒安全，任何時候都可呼叫）。"""
        return PipelineMetrics(
            level_dbfs=self._level.dbfs(),
            backlog=self._finals.size(),
            latency_ms_median=self._latencies.median(),
        )

    @property
    def dropped_partials(self) -> int:
        """被較新的即時工作取代而沒有執行的次數。"""
        return self._slot.dropped

    def wait(self, timeout: float | None = None) -> bool:
        """等到這次執行結束（讀完、停止或錯誤）；回傳是否已結束。"""
        return self._done.wait(timeout)

    # --- 執行緒模式 -----------------------------------------------------------

    def start(self, source: AudioSource) -> None:
        """啟動音訊來源與三條 worker 執行緒，送出 "running"；元件有 `keep_warm` 時一併啟動
        （pipeline 結束時停止）。

        Raises:
            RuntimeError: 已經啟動過或用過逐步模式。
            CaptureError: `source.start()` 失敗（不會進入 running）。
        """
        self._claim("threaded")
        source.start()
        self._source = source
        self._set_running()
        # 在 worker 之前啟動：非即時來源可能一下就讀完，capture 執行緒在 start() 返回前就
        # 呼叫 _finish()；保溫若在那之後才啟動，就沒有人停止它。
        self._start_keep_warm()
        self._threads = [
            threading.Thread(
                target=self._capture_loop, name="pipeline-capture", daemon=True
            ),
            threading.Thread(
                target=self._partial_loop, name="pipeline-partial", daemon=True
            ),
            threading.Thread(
                target=self._final_loop, name="pipeline-final", daemon=True
            ),
        ]
        # capture 最後啟動：它讀完時以 worker 的 is_alive() 判斷要不要等，未啟動的
        # 執行緒 is_alive() 為 False。先啟動 capture 的話，非即時來源可能在 worker
        # 啟動前就讀完，於是不等完整結果就送出 idle（單核 CI 上實際發生過）。
        # worker 在 _input_done 設定前不會自行結束，所以這個順序下 is_alive() 可靠。
        for thread in (*self._threads[1:], self._threads[0]):
            thread.start()

    def _start_keep_warm(self) -> None:
        keep_warm = self._components.keep_warm
        if keep_warm is None:
            return
        # 先標記：start() 進行到一半失敗時，結束時仍會呼叫 stop() 收拾。
        self._keep_warm_started = True
        try:
            keep_warm.start()
        except Exception as exc:  # 保溫只是加速，失敗不能讓 session 停下；記錄並回報
            logger.warning("cannot keep the translation model warm", exc_info=exc)
            self._log(
                "warning",
                f"cannot keep the translation model warm: {type(exc).__name__}: {exc}",
            )

    def stop(self, timeout: float = 5.0) -> None:
        """使用者主動中斷：停止擷取、丟棄尚未開始的工作，送出 "idle"。可重複呼叫。

        卡在模型呼叫裡的執行緒在 `timeout` 秒內沒結束就記 warning 後放手；
        它之後的結果不會送出。
        """
        self._stop.set()
        self._slot.close()
        dropped = self._finals.clear()
        if dropped:
            logger.info("stop: discarded %d pending final jobs", dropped)
        self._stop_source()
        deadline = _Deadline(timeout)
        for thread in self._threads:
            if thread is threading.current_thread():
                continue
            thread.join(deadline.remaining())
            if thread.is_alive():
                logger.warning("%s did not stop within %.1f s", thread.name, timeout)
        self._finish("idle", None)

    def _capture_loop(self) -> None:
        source = self._source
        assert source is not None
        try:
            while not self._stop.is_set():
                chunk = source.read(self._config.read_timeout)
                if len(chunk):
                    self._level.add(chunk)
                    self._handle(self._segmenter.process(chunk))
                elif getattr(source, "exhausted", False) is True:
                    self._handle(self._segmenter.flush())
                    break
            self._input_done.set()
            if self._stop.is_set():
                return
            # 讀完：等兩個 worker 把剩下的工作做完再宣告結束。
            for thread in self._threads[1:]:
                while thread.is_alive() and not self._stop.is_set():
                    thread.join(0.1)
            if not self._stop.is_set():
                self._finish("idle", None)
        except Exception as exc:  # noqa: BLE001 - 轉成 error 狀態回報（_fail 記錄 traceback）
            self._fail(exc)

    def _partial_loop(self) -> None:
        try:
            while not self._stop.is_set() and not self._input_done.is_set():
                job = self._slot.take(0.1)
                if job is not None:
                    self._run_partial(job)
        except Exception as exc:  # noqa: BLE001 - 轉成 error 狀態回報（_fail 記錄 traceback）
            self._fail(exc)

    def _final_loop(self) -> None:
        try:
            while not self._stop.is_set():
                job = self._finals.take(0.1)
                if job is not None:
                    self._run_final(job)
                elif self._input_done.is_set() and self._finals.empty():
                    # _input_done 在最後一次 put 之後才設定，所以此時佇列確定已空。
                    return
        except Exception as exc:  # noqa: BLE001 - 轉成 error 狀態回報（_fail 記錄 traceback）
            self._fail(exc)

    # --- 逐步模式 -------------------------------------------------------------

    def feed(self, audio: npt.NDArray[np.int16]) -> None:
        """逐步模式：同步把音訊送進切句器並排入工作（不執行辨識）。"""
        self._claim("stepped")
        self._level.add(audio)
        self._guarded(lambda: self._handle(self._segmenter.process(audio)))

    def end_of_stream(self) -> None:
        """逐步模式：串流結束，收尾進行中的句子。"""
        self._claim("stepped")
        self._guarded(lambda: self._handle(self._segmenter.flush()))
        self._input_done.set()

    def run_pending(self) -> None:
        """逐步模式：先執行最新槽中的即時工作，再依序執行全部完整工作。

        串流已結束且工作都做完時送出 "idle"。錯誤與執行緒模式相同，
        轉成 "error" 狀態而不是往外拋。
        """
        self._claim("stepped")

        def run() -> None:
            job = self._slot.take(None)
            if job is not None:
                self._run_partial(job)
            while (final := self._finals.take(None)) is not None:
                self._run_final(final)
            if self._input_done.is_set():
                self._finish("idle", None)

        self._guarded(run)

    def _guarded(self, fn: Callable[[], None]) -> None:
        if self._done.is_set():
            return
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - 轉成 error 狀態回報（_fail 記錄 traceback）
            self._fail(exc)

    def _claim(self, mode: Literal["threaded", "stepped"]) -> None:
        with self._state_lock:
            if self._run_mode is None:
                self._run_mode = mode
                first = True
            elif self._run_mode == mode and mode == "stepped":
                first = False
            else:
                raise RuntimeError(f"pipeline already used in {self._run_mode} mode")
        if first and mode == "stepped":
            self._set_running()

    # --- capture 端：事件 → 工作 ---------------------------------------------

    def _handle(self, events: list[SegmentEvent]) -> None:
        for event in events:
            if isinstance(event, UtteranceStarted):
                self._current = _Utterance(self._next_id, event.start_sample)
                self._next_id += 1
            elif isinstance(event, UtteranceAudio):
                current = self._require_current()
                current.append(event.audio)
                interval = max(self._interval, self._partial_cost)
                if current.length - current.last_trigger >= interval:
                    current.last_trigger = current.length
                    self._slot.put(
                        Job(
                            current.id,
                            current.audio(),
                            current.start_sample,
                            current.start_sample + current.length,
                        )
                    )
            elif isinstance(event, UtteranceEnded):
                self._end_utterance(event)

    def _require_current(self) -> _Utterance:
        if self._current is None:
            raise RuntimeError("segmenter emitted audio without an utterance")
        return self._current

    def _end_utterance(self, event: UtteranceEnded) -> None:
        current = self._require_current()
        audio = current.audio()
        length = event.end_sample - event.start_sample
        if len(audio) != length:
            raise RuntimeError(
                f"utterance {current.id} has {len(audio)} samples, expected {length}"
            )
        if event.reason == "pause" and self._tail_trim:
            keep = max(
                length - self._tail_trim,
                min(length, round(KEEP_TAIL_SECS * SAMPLE_RATE)),
            )
            audio = audio[:keep]
        job = Job(
            current.id,
            audio,
            event.start_sample,
            event.start_sample + len(audio),
            ended_at=self._clock(),
        )
        # 先標記結束，即時 worker 才會丟掉這句剩下的即時工作。
        self._ended_id = current.id
        self._current = None
        if self._run_mode == "stepped":
            self._finals.put_nowait(job)
        else:
            self._finals.put(job, self._stop.is_set)

    # --- 即時路徑 -------------------------------------------------------------

    def _run_partial(self, job: Job) -> None:
        if self._partial_obsolete(job):
            return
        started = self._clock()
        transcript = self._components.fast_transcriber.transcribe(job.audio)
        self._partial_cost = max(0, round((self._clock() - started) * SAMPLE_RATE))
        with self._emit_lock:
            if job.utterance_id > self._last_final_id:
                self._fast_no_speech[job.utterance_id] = (
                    transcript.no_speech_probability
                )
        if self._partial_obsolete(job):
            return
        reason = self._components.transcript_filter(transcript)
        if reason is not None:
            logger.debug("partial %d filtered: %s", job.utterance_id, reason)
            return
        original = self._original(transcript)
        stable = self._stability.observe(job.utterance_id, original)
        decision = self._route(transcript.language)
        if decision.action != "translate":
            text = self._copied(transcript, decision)
            self._emit(
                job,
                original,
                text,
                transcript.language,
                decision,
                final=False,
                stable_chars=stable,
            )
            return
        if not self._stability.should_translate(original, stable):
            # 穩定部分沒有變長：只是尾巴在閃爍，重新翻譯只會讓譯文跟著閃、佔用翻譯伺服器。
            # 沿用這句上一次的即時譯文，原文與 stable_chars 照常更新。
            self._emit(
                job,
                original,
                self._stability.translation,
                transcript.language,
                decision,
                final=False,
                stable_chars=stable,
            )
            return
        self._stability.mark_requested(original, stable)
        request = TranslationRequest(
            original, decision.source, decision.target, self._context(decision.source)
        )
        emitted = False
        try:
            stream: Iterator[str] = self._components.fast_translator.stream(request)
            try:
                for translation in stream:
                    if self._partial_obsolete(job):
                        return
                    self._stability.translation = translation
                    emitted = (
                        self._emit(
                            job,
                            original,
                            translation,
                            transcript.language,
                            decision,
                            final=False,
                            stable_chars=stable,
                        )
                        or emitted
                    )
            finally:
                close = getattr(stream, "close", None)
                if callable(close):
                    close()
            self._clear_warning("translate")
        except TranslationError as exc:
            self._warn("translate", f"translation failed: {type(exc).__name__}: {exc}")
            if not emitted:
                self._emit(
                    job,
                    original,
                    self._stability.translation,
                    transcript.language,
                    decision,
                    final=False,
                    stable_chars=stable,
                )

    def _partial_obsolete(self, job: Job) -> bool:
        # 句子已結束：完整結果馬上就到，算力留給下一句。
        return self._stop.is_set() or job.utterance_id <= self._ended_id

    # --- 完整路徑 -------------------------------------------------------------

    def _run_final(self, job: Job) -> None:
        if self._stop.is_set():
            return
        prompt = self._asr_prompt()
        transcript = self._components.final_transcriber.transcribe(
            job.audio, prompt=prompt
        )
        reason = self._components.transcript_filter(self._for_filter(job, transcript))
        if reason is not None:
            logger.debug("final %d filtered: %s", job.utterance_id, reason)
            self._emit_cleared(job)
            return
        original = self._original(transcript)
        decision = self._route(transcript.language)
        translation = self._copied(transcript, decision)
        if decision.action == "translate":
            request = TranslationRequest(
                original,
                decision.source,
                decision.target,
                self._context(decision.source),
            )
            try:
                translation = self._components.final_translator.translate(request)
                self._clear_warning("translate")
            except TranslationError as exc:
                self._warn(
                    "translate", f"translation failed: {type(exc).__name__}: {exc}"
                )
        self._emit(
            job, original, translation, transcript.language, decision, final=True
        )

    def _for_filter(self, job: Job, transcript: Transcript) -> Transcript:
        """完整模型的 no_speech 不可靠時，換成即時模型對同一句的 no_speech 再過濾。"""
        with self._emit_lock:
            fast_no_speech = self._fast_no_speech.pop(job.utterance_id, None)
        if self._components.final_no_speech_reliable:
            return transcript
        if fast_no_speech is None:
            # 句子太短（沒觸發過即時辨識）時補跑一次即時模型；短句正是最常見的誤觸發。
            fast_no_speech = self._components.fast_transcriber.transcribe(
                job.audio
            ).no_speech_probability
        return dataclasses.replace(transcript, no_speech_probability=fast_no_speech)

    def _asr_prompt(self) -> str | None:
        # 口譯模式兩種語言交錯，前文 prompt 會把語言偵測拉向上一位說話者，所以不帶。
        if not self._config.asr_prompt_context or self._config.mode != "media":
            return None
        if self._config.context_lines == 0:
            return None
        with self._emit_lock:
            lines = [text for _, text in self._history]
        return " ".join(lines) or None

    def _context(self, source: str) -> tuple[str, ...]:
        if self._config.context_lines == 0:
            return ()
        with self._emit_lock:
            return tuple(text for lang, text in self._history if lang == source)

    def _original(self, transcript: Transcript) -> str:
        config = self._config
        # 原文用「說這個語言的人」設定的字形：口譯時先看我的語言、再看對方語言；
        # 影片翻譯時先看收聽語言（例如聽繁中、字幕簡中時原文仍顯示繁體）。
        if config.mode == "interpret":
            languages = (config.target_language, config.partner_language)
        elif config.source_language != "auto":
            languages = (config.source_language, config.target_language)
        else:
            languages = (config.target_language,)
        return display_original(transcript.text.strip(), transcript.language, languages)

    @staticmethod
    def _copied(transcript: Transcript, decision: Route) -> str:
        """不送翻譯器的譯文："copy" 是原文轉成譯文語言的字形，其他為空字串。"""
        if decision.action != "copy":
            return ""
        return copy_translation(transcript.text.strip(), decision.target)

    def _route(self, detected: str) -> Route:
        decision = route(
            self._config.mode,
            detected,
            self._config.target_language,
            self._config.partner_language,
            self._translation_enabled,
        )
        if decision.action == "unsupported":
            self._warn(
                "language",
                f"no translation for {decision.source} -> {decision.target}; "
                "showing the original only",
            )
        return decision

    # --- 送出 -----------------------------------------------------------------

    def _emit(
        self,
        job: Job,
        original: str,
        translation: str,
        source_language: str,
        decision: Route,
        *,
        final: bool,
        stable_chars: int | None = None,
    ) -> bool:
        latency = self._latency_ms(job) if final else None
        update = SubtitleUpdate(
            utterance_id=job.utterance_id,
            original=original,
            translation=translation,
            source_language=source_language,
            target_language=decision.target,
            is_final=final,
            start_sample=job.start_sample,
            end_sample=job.end_sample,
            stable_chars=None if final else stable_chars,
            latency_ms=latency,
        )
        with self._emit_lock:
            if final:
                self._last_final_id = max(self._last_final_id, job.utterance_id)
                self._fast_no_speech.pop(job.utterance_id, None)
                self._partial_shown.discard(job.utterance_id)
                if self._config.context_lines and original:
                    self._history.append((base_language(source_language), original))
            elif job.utterance_id <= self._last_final_id:
                return False
            else:
                self._partial_shown.add(job.utterance_id)
            if self._stop.is_set():
                return False
            self._on_subtitle(update)
            if final and latency is not None and original:
                self._latencies.add(latency)
            return True

    def _latency_ms(self, job: Job) -> int | None:
        """VAD 判定句子結束 → 現在（送出完整字幕）的毫秒數；量不到時 None。"""
        if job.ended_at is None:
            return None
        return max(0, round((self._clock() - job.ended_at) * 1000))

    def _emit_cleared(self, job: Job) -> None:
        """完整結果被濾掉：若曾送出即時字幕，送一則空的完整結果讓 UI 清掉它。"""
        with self._emit_lock:
            self._last_final_id = max(self._last_final_id, job.utterance_id)
            shown = job.utterance_id in self._partial_shown
            self._partial_shown.discard(job.utterance_id)
            if not shown or self._stop.is_set():
                return
            self._on_subtitle(
                SubtitleUpdate(
                    utterance_id=job.utterance_id,
                    original="",
                    translation="",
                    source_language="",
                    target_language="",
                    is_final=True,
                    start_sample=job.start_sample,
                    end_sample=job.end_sample,
                    latency_ms=self._latency_ms(job),
                )
            )

    # --- 狀態、警告、錯誤 ------------------------------------------------------

    def _set_running(self) -> None:
        with self._state_lock:
            self._state = "running"
        self._notify_status("running", None)

    def _finish(
        self,
        state: PipelineState,
        detail: str | None,
        error: BaseException | None = None,
    ) -> None:
        with self._state_lock:
            if self._done.is_set():
                return
            self._state = state
            self._error = error
            self._done.set()
        self._stop_keep_warm()
        self._notify_status(state, detail)

    def _stop_keep_warm(self) -> None:
        keep_warm = self._components.keep_warm
        if keep_warm is None or not self._keep_warm_started:
            return
        try:
            keep_warm.stop()
        except Exception as exc:  # 停止流程不能因背景工作的錯誤中斷；記錄並回報
            logger.error("stopping the keep-warm task failed", exc_info=exc)
            self._log(
                "error",
                f"stopping the keep-warm task failed: {type(exc).__name__}: {exc}",
            )

    def _fail(self, exc: BaseException) -> None:
        logger.error("pipeline failed", exc_info=exc)
        self._stop.set()
        self._slot.close()
        self._stop_source()
        # traceback 已經記錄過；留著的話它的 frame（locals 有 self）會讓
        # pipeline → 來源／error → traceback → frame → pipeline 成為循環，結束後元件
        # （GPU 上的 ASR 模型）要等循環垃圾回收才釋放。
        exc.with_traceback(None)
        self._finish("error", f"{type(exc).__name__}: {exc}", exc)

    def _stop_source(self) -> None:
        if self._source is None:
            return
        try:
            self._source.stop()
        except Exception as exc:  # 停止流程不能因來源的錯誤中斷；記錄並回報
            logger.error("stopping audio source failed", exc_info=exc)
            self._log(
                "error", f"stopping audio source failed: {type(exc).__name__}: {exc}"
            )

    def _notify_status(self, state: PipelineState, detail: str | None) -> None:
        if self._on_status is None:
            return
        try:
            self._on_status(state, detail)
        except Exception as exc:  # 狀態回呼本身失敗時已沒有其他回報管道，只能記錄
            logger.error("status callback failed", exc_info=exc)

    def _warn(self, key: str, message: str) -> None:
        # 例如 Ollama 沒開時每句都會失敗；同一則訊息連續出現只回報一次。
        with self._warn_lock:
            if self._warnings.get(key) == message:
                return
            self._warnings[key] = message
        logger.warning("%s", message)
        self._log("warning", message)

    def _clear_warning(self, key: str) -> None:
        with self._warn_lock:
            self._warnings.pop(key, None)

    def _log(self, level: str, message: str) -> None:
        if self._on_log is None:
            return
        try:
            self._on_log(level, message)
        except Exception as exc:  # log 回呼本身失敗時已沒有其他回報管道，只能記錄
            logger.error("log callback failed", exc_info=exc)


class _Deadline:
    # 只用在 stop() 的 join 等待上限，與音訊位置無關。
    def __init__(self, timeout: float) -> None:
        self._end = time.monotonic() + max(timeout, 0.0)

    def remaining(self) -> float:
        return max(self._end - time.monotonic(), JOIN_TIMEOUT_FLOOR)
