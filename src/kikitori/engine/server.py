"""stdio NDJSON 引擎伺服器：Tauri 程式與 Python 處理管線之間的唯一入口。

位於整個 Python 套件的最上層：讀 stdin 的請求、分派到各方法、把 pipeline 的字幕
與狀態轉成事件寫到 stdout。stdin 讀取在主執行緒依序處理；載入模型、下載這類
耗時工作放在背景執行緒，讓 `session.stop`、`engine.shutdown` 隨時能被處理。
協定見 `docs/specs/app-architecture.md`，細節見 `docs/specs/pipeline-engine.md`。
"""

import dataclasses
import logging
import math
import platform
import threading
import time
from collections.abc import Callable, Mapping
from importlib import metadata
from typing import Any, BinaryIO, Literal
from urllib.parse import urlsplit

from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
    PermissionDeniedError,
)
from kikitori.capture.types import AudioSource, DeviceKind
from kikitori.config import Settings, SettingsError, check_interpret_languages
from kikitori.engine import protocol as p
from kikitori.engine.deferred import Deferred, DeferredRunner
from kikitori.engine.furigana_service import (
    DefaultFuriganaProvider,
    DictionaryInUseError,
    FuriganaService,
)
from kikitori.engine.hardware_service import HardwareService, recommendation_patch
from kikitori.engine.io import (
    LINE_TOO_LONG,
    ChannelClosedError,
    EventLogHandler,
    MessageWriter,
    read_lines,
)
from kikitori.engine.services import (
    EngineServices,
    ProgressCallback,
    platform_name,
)
from kikitori.engine.speaker_model_service import DefaultSpeakerModelProvider
from kikitori.furigana.dictionary import DICTIONARY_ID
from kikitori.hardware import GPU_RUNTIME_ID
from kikitori.hardware.ollama import (
    OllamaError,
    UnloadError,
    validate_pull_model_name,
)
from kikitori.hardware.translation_server import is_loopback_host
from kikitori.languages import LANGUAGES
from kikitori.pipeline import MODES, Mode, Pipeline, PipelineState, SubtitleUpdate
from kikitori.pipeline.factory import (
    UnknownModelError,
    pipeline_config,
    translation_needed,
)
from kikitori.pipeline.types import PipelineComponents, release_components
from kikitori.speakers.model import SPEAKER_MODEL_ID
from kikitori.translate import redact_url
from kikitori.translate.presets import normalize_model_name

ENGINE_NAME = "kikitori-engine"
STOP_TIMEOUT = 5.0
DOWNLOAD_JOIN_TIMEOUT = 2.0
DEFERRED_JOIN_TIMEOUT = 2.0
LOADER_POLL_SECS = 0.1
"""等前一個 session 載入結束時，每隔多久檢查一次「這個 session 也被停止了」。"""
PROGRESS_INTERVAL = 0.25
MAX_DEVICE_ID_CHARS = 512
MAX_MODEL_ID_CHARS = 100
METRICS_INTERVAL = 1.0
"""`metrics` 事件的間隔（秒）：狀態列的音量表與延遲，每秒一次就夠，不會塞爆 stdout。"""
METRICS_JOIN_TIMEOUT = 1.0
TRANSLATION_UNLOAD_BUDGET = 1.5
"""引擎結束時卸載本機翻譯模型的總時限（秒）。app 送出 `engine.shutdown` 後只等 3 秒就
強制結束引擎（`app/src-tauri/src/engine.rs` 的 `SHUTDOWN_TIMEOUT`），停止 session 也要
時間；一個卸載請求實測 45 ms，伺服器不回應時剩下的模型放棄（Ollama 閒置 5 分鐘後仍會
自己卸載）。"""
MIN_UNLOAD_REMAINING_SECS = 0.05
"""剩下的時間少於這個就不再送卸載請求（Windows 的計時精度約 15 ms，太短的請求只會逾時）。"""
RELEASE_TIMEOUT = 5.0
"""session 結束時等元件釋放（ASR 模型卸載、放回模型池）的上限（秒），之後才送出結束狀態。

每個 transcriber 的 `close()` 最多等進行中的推論 2 秒（`CLOSE_TIMEOUT`），即時與完整路徑
各一個；再加上卸載。逾時的話記 warning、照樣送出結束狀態（UI 不能一直卡在停止中），釋放
在背景繼續，期間 `models.delete` 仍把模型當成使用中。"""

KIND_FOR_MODE: dict[Mode, DeviceKind] = {"media": "loopback", "interpret": "microphone"}
PREVIEW_LANGUAGE_PARAMS = (
    "source_language",
    "target_language",
    "partner_language",
    "mode",
)

DownloadKind = Literal["asr", "gpu_runtime", "translation", "furigana", "speaker"]
"""`download_progress` 的 `kind`。"""

logger = logging.getLogger(__name__)

DownloadJob = Callable[[ProgressCallback, threading.Event], None]
"""背景下載：`(progress, cancel)`；失敗時丟例外。"""

Handler = Callable[[dict[str, Any]], Mapping[str, Any] | Deferred]
"""處理函式：回傳結果（立即回應），或 `Deferred`（在背景工作執行緒上完成後回應）。"""


def engine_version() -> str:
    """已安裝套件的版本（單一來源是 pyproject.toml）。"""
    try:
        return metadata.version("kikitori")
    except metadata.PackageNotFoundError:
        return "0.0.0+unknown"


def _capture_code(exc: CaptureError) -> str:
    if isinstance(exc, DeviceNotFoundError):
        return "device_not_found"
    if isinstance(exc, PermissionDeniedError):
        return "permission_denied"
    if isinstance(exc, DeviceLostError):
        return "device_lost"
    if isinstance(exc, BackendUnavailableError):
        return "capture_unavailable"
    return "capture_error"


def _clear_tracebacks(exc: BaseException) -> None:
    """記錄之後丟掉例外（與它的 cause／context）的 traceback。

    traceback 參照這個載入執行緒的 frame（pipeline、元件、音訊來源）；例外若還被別處保存
    （例如音訊來源、pipeline 的 `error`），那些 frame 就與保存者形成循環，GPU 上的模型要
    等循環垃圾回收才釋放（memory_soak #1）。與 `Pipeline._fail()` 相同，log 已記錄完整
    traceback，之後只需要類別與訊息。
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        current.with_traceback(None)
        current = current.__cause__ or current.__context__


def subtitle_event(update: SubtitleUpdate, session_id: int) -> dict[str, Any]:
    """`subtitle` 事件的 data：`SubtitleUpdate` 的欄位 + `session_id`。

    即時字幕帶 `stable_chars` 與 `translation_stable_chars`（沒有 `latency_ms`）；完整字幕帶
    `latency_ms`（可能是 null，沒有兩個穩定前綴）。只放有意義的那些，UI 不必猜 null 是「沒有」
    還是「0」。
    """
    data = dataclasses.asdict(update)
    if update.speaker is None:
        data.pop("speaker")  # 影片翻譯：沒有「誰說的」
    if update.voice is None:
        data.pop("voice")  # 沒開說話者標籤、即時字幕或判斷不出來
    if update.is_final:
        data.pop("stable_chars")
        data.pop("translation_stable_chars")
    else:
        data.pop("latency_ms")
        for key in ("stable_chars", "translation_stable_chars"):
            if data[key] is None:
                data[key] = 0
    data["session_id"] = session_id
    return data


class _MetricsReporter:
    """session 執行中每隔 `interval` 秒送一則 `metrics` 事件（一條 daemon 執行緒）。

    `stop()` 返回之後不會再送出（送出與停止共用一把鎖），所以 `status idle／error`
    之後不會再看到這個 session 的 metrics。例外：送出卡住（例如 stdout 管線塞滿）超過
    `METRICS_JOIN_TIMEOUT` 時 `stop()` 記 warning 後照樣返回——結束狀態比保證順序重要，
    那則卡住的 metrics 之後才寫出的話會落在結束狀態之後。pipeline 已結束時自己停止，即使
    有人忘了呼叫 `stop()` 也不會一直留著執行緒。
    """

    def __init__(
        self,
        session_id: int,
        pipeline: Pipeline,
        send: Callable[[str, Mapping[str, Any]], None],
        interval: float,
    ) -> None:
        self._session_id = session_id
        self._pipeline = pipeline
        self._send = send
        self._interval = interval
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=f"session-{session_id}-metrics", daemon=True
        )

    def start(self) -> None:
        """開始回報（`stop()` 之後呼叫也只會讓執行緒立即結束）。"""
        self._thread.start()

    def stop(self) -> None:
        """停止並等執行緒結束（合計最多約 `METRICS_JOIN_TIMEOUT` 秒；可重複呼叫）。"""
        self._stop.set()
        deadline = time.monotonic() + METRICS_JOIN_TIMEOUT
        # 等進行中的送出完成：之後不會再有 metrics。不能無限等：送出可能卡在寫 stdout，
        # 那時結束狀態也送不出去，整個 session 的結束流程跟著卡住。
        if self._lock.acquire(timeout=METRICS_JOIN_TIMEOUT):
            self._lock.release()
        else:
            logger.warning(
                "%s is stuck sending; stopping without waiting for it",
                self._thread.name,
            )
        if (
            self._thread.ident is not None
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(max(0.0, deadline - time.monotonic()))
            if self._thread.is_alive():
                logger.warning("%s did not stop in time", self._thread.name)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._stop.is_set() or self._pipeline.wait(0):
                    return
                if self._pipeline.state != "running":
                    continue
                metrics = self._pipeline.metrics()
                try:
                    self._send(
                        "metrics",
                        {
                            "session_id": self._session_id,
                            "level_dbfs": metrics.level_dbfs,
                            "backlog": metrics.backlog,
                            "latency_ms_median": metrics.latency_ms_median,
                            # v0.3 G8：句子進行中／之外的音量（沒有那一類時為 null）
                            "speech_level_dbfs": metrics.speech_level_dbfs,
                            "background_level_dbfs": metrics.background_level_dbfs,
                        },
                    )
                except ChannelClosedError:
                    logger.error("metrics: protocol output closed")
                    return
                except Exception:
                    # 量測只是輔助顯示：程式錯誤記錄後停止回報，不影響 session
                    logger.exception("metrics reporting failed; stopping it")
                    return


class _Session:
    """一次 session：背景載入 → 執行 → 結束。`lock` 保護 pipeline 的建立與停止。"""

    def __init__(self, session_id: int, mode: Mode, asr_models: frozenset[str]) -> None:
        self.id = session_id
        self.mode = mode
        self.asr_models = asr_models
        """這個 session 載入的 ASR 模型（`models.delete` 的「使用中」判斷）。"""
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.status_lock = threading.Lock()
        """非結束狀態的「檢查還沒結束 → 寫出」與設定 `terminal_sent` 互斥（見 `_session_status`）。"""
        self.pipeline: Pipeline | None = None
        self.terminal_sent = False
        self.loader: threading.Thread | None = None
        # 載入期間收到的 session.set_translation：pipeline 建好時套用（受 lock 保護）
        self.translation_override: bool | None = None
        # 以下受伺服器的 _session_lock 保護；結束狀態送出前取走並釋放
        self.components: PipelineComponents | None = None
        self.releasing = False
        """元件已取走、還在釋放中（`models.delete` 仍視為使用中）。"""
        self.metrics: _MetricsReporter | None = None


class _Download:
    def __init__(self, kind: DownloadKind, model: str) -> None:
        self.kind = kind
        self.model = model
        self.cancel = threading.Event()
        self.thread: threading.Thread | None = None


class EngineServer:
    """處理協定請求並送出事件。一個行程一個實例。"""

    def __init__(
        self,
        services: EngineServices,
        writer: MessageWriter,
        *,
        version: str | None = None,
        metrics_interval: float = METRICS_INTERVAL,
    ) -> None:
        """
        Args:
            services: 外部功能（真實或測試用的假實作）。
            writer: stdout 的訊息寫出器。
            version: `engine.info` 回報的版本；省略時讀取套件版本。
            metrics_interval: `metrics` 事件的間隔秒數（測試用較短的值）。

        Raises:
            ValueError: `metrics_interval` 不是有限的正數。
        """
        if (
            isinstance(metrics_interval, bool)
            or not isinstance(metrics_interval, int | float)
            or not math.isfinite(metrics_interval)
            or metrics_interval <= 0
        ):
            raise ValueError(
                f"metrics_interval must be a positive number, got {metrics_interval!r}"
            )
        self._metrics_interval = float(metrics_interval)
        self._services = services
        self._writer = writer
        self._version = version or engine_version()
        self._shutdown = threading.Event()
        # 這次執行在本機翻譯服務上用過的 (base_url, api_key_env, model)：結束時請
        # Ollama 卸載，顯示記憶體才會立刻釋放（見 `_unload_translation_models`）
        self._used_translation_targets: set[tuple[str, str, str]] = set()
        self._used_translation_lock = threading.Lock()
        self._session_lock = threading.Lock()
        self._session: _Session | None = None
        self._next_session_id = 1
        # 以 (kind, model) 為鍵：ASR 模型 id 與翻譯模型名稱是不同的命名空間
        self._downloads: dict[tuple[DownloadKind, str], _Download] = {}
        self._downloads_lock = threading.Lock()
        # 偵測硬體可能要數秒：在背景工作執行緒上處理，stdin 仍能立即處理 session.stop 等。
        self._deferred = DeferredRunner(self._send)
        self._hardware = HardwareService(services.hardware, services.models)
        # 振假名辭典：第一句日文字幕時才載入、session 結束或關閉時釋放（見 furigana_service）
        self._furigana = FuriganaService(
            services.furigana
            if services.furigana is not None
            else DefaultFuriganaProvider()
        )
        self._speaker_model = (
            services.speaker_model
            if services.speaker_model is not None
            else DefaultSpeakerModelProvider()
        )
        # 回應寫出後才執行的動作：讓 UI 先拿到 session_id，再收到該 session 的事件。
        self._after_response: list[Callable[[], None]] = []
        # 回應沒寫出去時撤銷處理函式預先做的事（對應 `Deferred.unsent`）
        self._unsent_response: list[Callable[[], None]] = []
        # 最後一個 session 的載入執行緒：下一個 session 先等它結束（見 `_load_session`）
        self._last_loader: threading.Thread | None = None
        # 可能還在用 ASR 模型的 session（執行中、載入中、或已停止但載入執行緒還沒結束）；
        # models.delete 據此回 model_in_use。受 _session_lock 保護。
        self._live_sessions: list[_Session] = []
        self._handlers: dict[str, Handler] = {
            "engine.info": self._engine_info,
            "hardware.detect": self._hardware_detect,
            "models.list": self._models_list,
            "models.download": self._models_download,
            "models.validate": self._models_validate,
            "models.pull_translation": self._models_pull_translation,
            "models.delete": self._models_delete,
            "languages.list": self._languages_list,
            "devices.list": self._devices_list,
            "settings.get": self._settings_get,
            "settings.set": self._settings_set,
            "settings.apply_recommendation": self._settings_apply_recommendation,
            "settings.preview_recommendation": self._settings_preview_recommendation,
            "settings.preview_languages": self._settings_preview_languages,
            "session.start": self._session_start,
            "session.stop": self._session_stop,
            "session.set_translation": self._session_set_translation,
            "session.set_furigana": self._session_set_furigana,
            "engine.shutdown": self._engine_shutdown,
        }

    # --- 主迴圈 ---------------------------------------------------------------

    def serve(self, stdin: BinaryIO) -> int:
        """讀 stdin 直到 EOF 或 `engine.shutdown`，清理後回傳結束碼。

        Returns:
            0 正常結束；1 表示 stdout 已無法寫入。
        """
        bridge = EventLogHandler(self._writer)
        logging.getLogger("kikitori").addHandler(bridge)
        # 設定檔無效時 store 會記 warning，經 bridge 變成 log 事件。
        self._services.settings_store.load()
        try:
            self._send_status("idle", None, None, None)
            for line in read_lines(stdin):
                if line is LINE_TOO_LONG:
                    self._send(
                        p.error_response(
                            None,
                            p.INVALID_REQUEST,
                            f"line longer than {p.MAX_LINE_BYTES} bytes",
                        )
                    )
                elif isinstance(line, bytes) and line.strip():
                    self.handle_line(line)
                if self._shutdown.is_set():
                    break
            else:
                logger.info("stdin closed; shutting down")
            return 0
        except ChannelClosedError:
            logger.error("protocol output closed; shutting down")
            return 1
        finally:
            self.close()
            logging.getLogger("kikitori").removeHandler(bridge)

    def handle_line(self, line: bytes) -> None:
        """處理一行請求並寫出回應；任何錯誤都變成錯誤回應，不會往外拋（寫出失敗除外）。

        Raises:
            ChannelClosedError: stdout 無法寫入。
        """
        try:
            request = p.parse_request(line)
        except p.ProtocolError as exc:
            self._send(p.error_response(exc.request_id, exc.code, exc.message))
            return
        handler = self._handlers.get(request.method)
        if handler is None:
            self._send(
                p.error_response(
                    request.id,
                    p.UNKNOWN_METHOD,
                    f"unknown method {request.method[:100]!r}",
                )
            )
            return
        try:
            result = handler(request.params)
        except p.ProtocolError as exc:
            self._send(p.error_response(request.id, exc.code, exc.message))
            return
        except ChannelClosedError:
            raise
        except Exception as exc:
            logger.exception("method %s failed", request.method)
            self._send(
                p.error_response(
                    request.id, p.INTERNAL_ERROR, f"{type(exc).__name__}: {exc}"
                )
            )
            return
        if isinstance(result, Deferred):
            # 回應由工作執行緒在完成時寫出（以 id 對應，可能晚於之後的請求）。
            self._deferred.submit(
                request.id, request.method, result.job, result.after, result.unsent
            )
            return
        actions, self._after_response = self._after_response, []
        unsent, self._unsent_response = self._unsent_response, []
        try:
            self._send(p.response(request.id, result))
        except Exception:
            # 回應沒寫出去：之後的動作（啟動下載執行緒）不執行，預先登記的下載也要撤銷，
            # 否則同一個模型之後永遠是 download_in_progress
            for undo in unsent:
                undo()
            raise
        for action in actions:
            action()

    def close(self) -> None:
        """停止 session、卸載本機翻譯模型、放棄尚未開始的背景請求、取消下載、釋放模型
        （可重複呼叫）。"""
        self._stop_session()
        # session 停了才卸載：之後不會再有翻譯請求把模型載回來（進行中的翻譯會先做完，
        # Ollama 之後才卸載，實測）。排在 ASR 釋放之前：app 等不及而強制結束引擎時，
        # 引擎行程內的 ASR 模型由作業系統收回，Ollama 行程裡的模型卻會一直佔著顯示記憶體。
        self._unload_translation_models()
        self._deferred.close(DEFERRED_JOIN_TIMEOUT)
        with self._downloads_lock:
            downloads = list(self._downloads.values())
        for download in downloads:
            download.cancel.set()
        for download in downloads:
            # 回應沒寫出去時執行緒不會被啟動（ident 為 None），不能 join。
            if download.thread is not None and download.thread.ident is not None:
                download.thread.join(DOWNLOAD_JOIN_TIMEOUT)
                if download.thread.is_alive():
                    logger.warning(
                        "download of %s did not stop in time", download.model
                    )
        self._furigana.close()
        release = self._services.release_models
        if release is not None:
            try:
                release()
            except Exception:
                # 結束流程不能因釋放失敗中斷；行程結束時作業系統仍會收回
                logger.exception("releasing ASR models failed")

    def _note_translation_models(self, settings: Settings) -> None:
        """記下 session 會用到的本機翻譯模型；遠端服務不記（不對它多送請求）。"""
        t = settings.translation
        base_url = t.base_url.strip()
        if not is_loopback_host(urlsplit(base_url).hostname or ""):
            return
        with self._used_translation_lock:
            for model in (t.fast_model, t.final_model):
                self._used_translation_targets.add((base_url, t.api_key_env, model))

    def _unload_translation_models(self) -> None:
        """請本機 Ollama 立刻卸載這次用過的翻譯模型，總共最多 `TRANSLATION_UNLOAD_BUDGET` 秒。

        不是 Ollama 的伺服器會拒絕（記 warning）；失敗不影響結束流程。
        """
        with self._used_translation_lock:
            used = sorted(self._used_translation_targets)
            self._used_translation_targets.clear()
        if not used:
            return
        deadline = time.monotonic() + TRANSLATION_UNLOAD_BUDGET
        for i, (base_url, api_key_env, model) in enumerate(used):
            remaining = deadline - time.monotonic()
            if remaining < MIN_UNLOAD_REMAINING_SECS:
                skipped = ", ".join(m for _, _, m in used[i:])
                logger.warning(
                    "no time left to unload translation model(s) %s; "
                    "the server unloads them after its own idle timeout",
                    skipped,
                )
                return
            try:
                self._services.translation_models.unload(
                    base_url, api_key_env, model, remaining
                )
            except UnloadError as exc:
                # 模型沒安裝（404）或本機服務不是 Ollama：每次結束都一樣，不是故障
                logger.info("translation server declined to unload %s: %s", model, exc)
            except (OllamaError, ValueError) as exc:
                logger.warning(
                    "could not unload translation model %s from %s: %s",
                    model,
                    redact_url(base_url),
                    exc,
                )
            except Exception:
                # 結束流程不能因此中斷：之後還要取消下載、釋放 ASR 模型（同 `close()`）
                logger.exception(
                    "unloading translation model %s from %s failed",
                    model,
                    redact_url(base_url),
                )
            else:
                logger.info("unloaded translation model %s", model)

    # --- 送出 -----------------------------------------------------------------

    def _send(self, message: Mapping[str, Any]) -> None:
        self._writer.send(message)

    def _send_event(self, name: str, data: Mapping[str, Any]) -> None:
        self._send(p.event(name, data))

    def _send_status(
        self,
        state: str,
        detail: str | None,
        session_id: int | None,
        code: str | None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        event: dict[str, Any] = {
            "state": state,
            "detail": detail,
            "session_id": session_id,
            "code": code,
        }
        # data 只在有內容時出現（例如 translation_model_missing 的模型清單），
        # 其他 status 事件的形狀與之前完全相同
        if data is not None:
            event["data"] = dict(data)
        self._send_event("status", event)

    # --- engine / hardware / devices / settings --------------------------------

    def _engine_info(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        return {
            "name": ENGINE_NAME,
            "version": self._version,
            "protocol_version": p.PROTOCOL_VERSION,
            "platform": platform_name(),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "fake": self._services.fake,
        }

    def _hardware_detect(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params)
        # 設定在收到請求時取出：之後的 settings.set 不影響這次偵測
        settings = self._services.settings_store.get()
        return Deferred(lambda: self._hardware.report(settings))

    def _languages_list(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        # 語言清單只在 languages/catalog.py；UI 不自己維護一份
        return {
            "languages": [
                {
                    "code": lang.code,
                    "english_name": lang.english_name,
                    "native_name": lang.native_name,
                    "whisper_code": lang.whisper_code,
                    "rtl": lang.rtl,
                    "no_space": lang.no_space,
                    "asr_quality": dict(lang.asr_quality),
                }
                for lang in LANGUAGES
            ]
        }

    def _devices_list(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        try:
            devices = self._services.list_devices()
        except CaptureError as exc:
            raise p.ProtocolError(_capture_code(exc), str(exc)) from exc
        return {"devices": [dataclasses.asdict(d) for d in devices]}

    def _settings_result(self) -> Mapping[str, Any]:
        store = self._services.settings_store
        snap = store.snapshot()
        # is_new：還沒有設定檔（第一次執行），UI 據此自動套用硬體推薦。
        # revision：UI 的回應可能晚於較新的一份才寫回畫面，以此丟掉舊的那份
        return {
            "settings": snap.settings.to_dict(),
            "path": str(store.path),
            "is_new": snap.is_new,
            "revision": snap.revision,
        }

    def _settings_get(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        return self._settings_result()

    def _settings_set(self, params: dict[str, Any]) -> Mapping[str, Any]:
        try:
            self._services.settings_store.update(params)
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        except OSError as exc:
            logger.error("cannot save settings: %s", exc)
            raise p.ProtocolError(
                p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
            ) from exc
        return self._settings_result()

    def _settings_apply_recommendation(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params, ("only_if_new",))
        only_if_new = params.get("only_if_new", False)
        if not isinstance(only_if_new, bool):
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"only_if_new must be true or false, got {only_if_new!r}",
            )
        settings = self._services.settings_store.get()

        def job() -> Mapping[str, Any]:
            store = self._services.settings_store
            _, rec = self._hardware.recommendation(settings)
            patch = recommendation_patch(rec)
            try:
                # only_if_new：第一次執行的自動套用。是否已存過要在寫入的同一把鎖內判斷，
                # 使用者在偵測期間存的組合才不會被覆寫。
                written = (
                    store.update_if_new(patch) if only_if_new else store.update(patch)
                )
            except OSError as exc:
                logger.error("cannot save settings: %s", exc)
                raise p.ProtocolError(
                    p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
                ) from exc
            # SettingsError 不轉成 invalid_settings：推薦組合不合法是程式錯誤（internal_error）
            return {
                **self._settings_result(),
                "applied": None if written is None else patch,
                "recommendation": rec.to_dict(),
            }

        return Deferred(job)

    def _settings_preview_recommendation(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params)

        def job() -> Mapping[str, Any]:
            # 設定在執行時才取（不是收到請求時）：預覽可能排在其他背景請求之後，
            # differs 要和 UI 收到回應當下的設定比較，剛存的組合才不會被誤判成「不同」。
            return self._hardware.preview(self._services.settings_store.get())

        # 轉換規則只在引擎：UI 用 differs 決定是否提示「有更適合的組合」，不自己比較
        return Deferred(job)

    def _settings_preview_languages(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params, PREVIEW_LANGUAGE_PARAMS)
        if "mode" in params and params["mode"] not in MODES:
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"mode must be one of {list(MODES)}, got {params['mode']!r}",
            )
        # 語言碼的驗證與正規化沿用設定（別名、口譯不能同一個 Whisper 語言），
        # 不合法時與 settings.set 一樣回 invalid_settings；合併結果只用來預覽，不存檔。
        try:
            candidate = self._services.settings_store.get().merged(dict(params))
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc

        def job() -> Mapping[str, Any]:
            # differs 與 preview_recommendation 相同，比對執行當下的設定
            current = self._services.settings_store.get()
            return self._hardware.preview_languages(candidate, current)

        return Deferred(job)

    # --- models -----------------------------------------------------------------

    def _models_list(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params)
        settings = self._services.settings_store.get()

        def job() -> Mapping[str, Any]:
            return {
                **self._hardware.models_list(settings),
                "furigana": self._furigana_status(),
                "speaker_model": self._speaker_model.status().to_dict(),
            }

        return Deferred(job)

    def _furigana_status(self) -> dict[str, Any]:
        """`models.list` 的 `furigana`：辭典的狀態加上 `in_use`（正被開啟）。"""
        status = self._furigana.provider.status().to_dict()
        status["in_use"] = self._furigana.in_use
        return status

    def _models_validate(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params, ("settings",))
        current = self._services.settings_store.get()
        patch = params.get("settings", {})
        if not isinstance(patch, dict):
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"settings must be an object, got {type(patch).__name__}",
            )
        # 格式錯誤在這裡就回 invalid_settings（與 settings.set 相同）；只有合法的設定
        # 才需要偵測硬體來檢查組合。
        try:
            candidate = current.merged(patch)
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        return Deferred(lambda: self._hardware.validate(candidate))

    def _models_download(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params, ("model", "accept_license"))
        model = params.get("model")
        if not isinstance(model, str) or not model:
            raise p.ProtocolError(
                p.INVALID_PARAMS, f"model must be a non-empty string, got {model!r}"
            )
        if model == GPU_RUNTIME_ID:
            return self._download_gpu_runtime(params)
        if "accept_license" in params:
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"accept_license only applies to {GPU_RUNTIME_ID!r}",
            )
        if model == DICTIONARY_ID:
            return self._download_furigana()
        if model == SPEAKER_MODEL_ID:
            return self._download_speaker_model()
        known = {m.id: m for m in self._services.models.asr_models()}
        if model not in known:
            available = ", ".join(
                [*known, GPU_RUNTIME_ID, DICTIONARY_ID, SPEAKER_MODEL_ID]
            )
            raise p.ProtocolError(
                p.UNKNOWN_MODEL,
                f"unknown model {model[:100]!r}; available: {available}",
            )
        catalog = self._services.models

        def download(progress: ProgressCallback, cancel: threading.Event) -> None:
            catalog.download(model, progress, cancel)

        thread = self._register_download(
            "asr", model, known[model].size_bytes, download
        )
        self._after_response.append(thread.start)
        self._unsent_response.append(lambda: self._unregister_download("asr", model))
        return {"model": model, "started": True}

    def _download_gpu_runtime(self, params: dict[str, Any]) -> Mapping[str, Any]:
        """NVIDIA cuBLAS：使用者必須明確同意 NVIDIA EULA（`accept_license: true`）。"""
        accepted = params.get("accept_license", False)
        if not isinstance(accepted, bool):
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"accept_license must be true or false, got {accepted!r}",
            )
        status = self._services.hardware.gpu_runtime_status()
        # 平台不支援時不要求同意授權：那裡根本不會下載 NVIDIA 的檔案
        if not status.supported:
            raise p.ProtocolError(
                p.GPU_RUNTIME_UNSUPPORTED,
                "the GPU runtime is only available on Windows and Linux x86-64",
            )
        if not accepted:
            raise p.ProtocolError(
                p.LICENSE_NOT_ACCEPTED,
                f"downloading {status.package} {status.version} from PyPI requires "
                f"accepting the {status.license}: {status.license_url} "
                "(resend with accept_license: true after the user agrees)",
            )
        logger.info("user accepted the %s (%s)", status.license, status.license_url)

        def install(progress: ProgressCallback, cancel: threading.Event) -> None:
            try:
                self._services.hardware.install_gpu_runtime(progress, cancel)
            finally:
                # 不論成功與否，cuBLAS 的狀態可能變了；下一次偵測不能用舊結果
                self._hardware.invalidate()

        thread = self._register_download(
            "gpu_runtime", GPU_RUNTIME_ID, status.download_bytes or 0, install
        )
        self._after_response.append(thread.start)
        self._unsent_response.append(
            lambda: self._unregister_download("gpu_runtime", GPU_RUNTIME_ID)
        )
        return {"model": GPU_RUNTIME_ID, "started": True}

    def _download_furigana(self) -> Mapping[str, Any]:
        """振假名辭典（SudachiDict core，約 69 MiB）：使用者打開振假名時才下載。"""
        provider = self._furigana.provider

        def install(progress: ProgressCallback, cancel: threading.Event) -> None:
            try:
                provider.install(progress, cancel)
            finally:
                # 成功時，之前因為沒有辭典而停用的 session 下一句就會載入
                self._furigana.dictionary_changed()

        thread = self._register_download(
            "furigana", DICTIONARY_ID, provider.status().download_bytes, install
        )
        self._after_response.append(thread.start)
        self._unsent_response.append(
            lambda: self._unregister_download("furigana", DICTIONARY_ID)
        )
        return {"model": DICTIONARY_ID, "started": True}

    def _download_speaker_model(self) -> Mapping[str, Any]:
        """說話者向量模型（約 27 MiB）：使用者打開說話者標籤時才下載；下一個 session 開始使用。"""
        provider = self._speaker_model

        def install(progress: ProgressCallback, cancel: threading.Event) -> None:
            provider.install(progress, cancel)

        thread = self._register_download(
            "speaker", SPEAKER_MODEL_ID, provider.status().download_bytes, install
        )
        self._after_response.append(thread.start)
        self._unsent_response.append(
            lambda: self._unregister_download("speaker", SPEAKER_MODEL_ID)
        )
        return {"model": SPEAKER_MODEL_ID, "started": True}

    def _delete_speaker_model(self) -> Mapping[str, Any]:
        """刪除說話者向量模型；下載中回 `model_in_use`。

        執行中的 session 已把模型讀進記憶體，不受影響（見 `speaker_model_service`）。
        """
        with self._downloads_lock:
            downloading = ("speaker", SPEAKER_MODEL_ID) in self._downloads
        if downloading:
            raise p.ProtocolError(
                p.MODEL_IN_USE, "the speaker embedding model is in use by a download"
            )
        try:
            freed = self._speaker_model.delete()
        except FileNotFoundError as exc:
            raise p.ProtocolError(p.MODEL_NOT_DOWNLOADED, str(exc)) from exc
        logger.info("deleted the speaker embedding model (%d bytes)", freed)
        # 與振假名相同：沒有模型時一併關閉，設定與畫面才一致。存檔失敗只記錄。
        if self._services.settings_store.get().speakers.enabled:
            try:
                self._services.settings_store.update({"speakers": {"enabled": False}})
            except (SettingsError, OSError) as exc:
                logger.error("cannot turn speaker labels off after deleting: %s", exc)
        return {"model": SPEAKER_MODEL_ID, "freed_bytes": freed}

    def _delete_furigana(self) -> Mapping[str, Any]:
        """刪除振假名辭典；開啟中（session 執行中且振假名開啟）或下載中回 `model_in_use`。"""
        with self._downloads_lock:
            downloading = ("furigana", DICTIONARY_ID) in self._downloads
        if downloading:
            raise p.ProtocolError(
                p.MODEL_IN_USE, "the furigana dictionary is in use by a download"
            )
        try:
            freed = self._furigana.delete_dictionary()
        except DictionaryInUseError as exc:
            raise p.ProtocolError(
                p.MODEL_IN_USE, f"{exc}; turn furigana off first"
            ) from exc
        except FileNotFoundError as exc:
            raise p.ProtocolError(p.MODEL_NOT_DOWNLOADED, str(exc)) from exc
        logger.info("deleted the furigana dictionary (%d bytes)", freed)
        # 沒有辭典的振假名只會每個 session 記一次 warning：一併關閉，設定與畫面才一致
        # （開關打開時會再下載）。存檔失敗不影響已完成的刪除，只記錄。
        if self._services.settings_store.get().furigana.enabled:
            self._furigana.set_enabled(False)
            try:
                self._services.settings_store.update({"furigana": {"enabled": False}})
            except (SettingsError, OSError) as exc:
                logger.error("cannot turn furigana off after deleting it: %s", exc)
        return {"model": DICTIONARY_ID, "freed_bytes": freed}

    def _models_pull_translation(self, params: dict[str, Any]) -> Deferred:
        """Ollama 的 `/api/pull`：替使用者下載翻譯模型（進度同 `download_progress`）。"""
        p.reject_unknown_params(params, ("model",))
        try:
            model = validate_pull_model_name(params.get("model"))
        except ValueError as exc:
            raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc
        settings = self._services.settings_store.get()
        translation = self._services.translation_models
        started: list[threading.Thread] = []

        def pull(progress: ProgressCallback, cancel: threading.Event) -> None:
            try:
                translation.pull(settings, model, progress, cancel)
            finally:
                # 伺服器上的模型可能變了：推薦與總覽不能用舊的偵測結果
                self._hardware.invalidate()

        def job() -> Mapping[str, Any]:
            # 要連線檢查伺服器（數秒），所以在背景工作執行緒上；錯誤以錯誤碼回應，
            # UI 才能分辨「沒開 Ollama」與「不是 Ollama（請自行安裝）」。
            state = translation.server_state(settings)
            # 訊息會回到 UI 並寫進 log：網址中的帳密一律遮蔽
            base_url = redact_url(settings.translation.base_url)
            detail = f": {state.error}" if state.error else ""
            if not state.reachable:
                raise p.ProtocolError(
                    p.TRANSLATION_SERVER_UNREACHABLE,
                    f"cannot connect to the translation server {base_url}{detail}",
                )
            if state.auth_failed:
                raise p.ProtocolError(
                    p.TRANSLATION_SERVER_AUTH_FAILED,
                    f"the translation server {base_url} rejected the request "
                    "(HTTP 401/403); check the API key in the environment variable "
                    f"named by translation.api_key_env{detail}",
                )
            if not state.is_ollama:
                raise p.ProtocolError(
                    p.PULL_UNSUPPORTED,
                    f"the translation server {base_url} is not Ollama; "
                    f"install {model} on that server yourself{detail}",
                )
            started.append(self._register_download("translation", model, 0, pull))
            return {"model": model, "started": True}

        def start() -> None:
            for thread in started:
                thread.start()

        def unsent() -> None:
            # 回應沒寫出去，start() 不會執行：撤銷 job 中的登記
            if started:
                self._unregister_download("translation", model)

        return Deferred(job, after=start, unsent=unsent)

    def _models_delete(self, params: dict[str, Any]) -> Mapping[str, Any]:
        """刪除已下載的 ASR 模型（F7）。在主執行緒上同步執行。

        為什麼不放到背景：`session.start`、`models.download` 也都在主執行緒上登記，同步執行
        時「使用中」的檢查與刪除之間不會有新的 session 或下載開始。刪除只是幾個檔案，很快。
        """
        p.reject_unknown_params(params, ("model",))
        model = params.get("model")
        if not isinstance(model, str) or not model or len(model) > MAX_MODEL_ID_CHARS:
            raise p.ProtocolError(
                p.INVALID_PARAMS,
                f"model must be a non-empty string of at most {MAX_MODEL_ID_CHARS} "
                f"characters, got {str(model)[:MAX_MODEL_ID_CHARS]!r}",
            )
        if model == DICTIONARY_ID:
            return self._delete_furigana()
        if model == SPEAKER_MODEL_ID:
            return self._delete_speaker_model()
        catalog = self._services.models
        known = {m.id: m for m in catalog.asr_models()}
        if model not in known:
            raise p.ProtocolError(
                p.UNKNOWN_MODEL,
                f"unknown ASR model {model!r}; available: {', '.join(known)}",
            )
        users = self._model_users(model)
        if users:
            raise p.ProtocolError(
                p.MODEL_IN_USE,
                f"ASR model {model} is in use by {', '.join(users)}; stop it first",
            )
        if not known[model].downloaded:
            raise p.ProtocolError(
                p.MODEL_NOT_DOWNLOADED, f"ASR model {model} is not downloaded"
            )
        try:
            freed = catalog.delete(model)
        except FileNotFoundError as exc:
            raise p.ProtocolError(p.MODEL_NOT_DOWNLOADED, str(exc)) from exc
        except UnknownModelError as exc:
            raise p.ProtocolError(p.UNKNOWN_MODEL, str(exc)) from exc
        logger.info("deleted ASR model %s (%d bytes)", model, freed)
        return {"model": model, "freed_bytes": freed}

    def _model_users(self, model: str) -> list[str]:
        """正在使用這個 ASR 模型的 session 與下載（給錯誤訊息用）。"""
        users: list[str] = []
        with self._session_lock:
            self._prune_live_sessions()
            users += [
                f"session {s.id}" for s in self._live_sessions if model in s.asr_models
            ]
        with self._downloads_lock:
            if ("asr", model) in self._downloads:
                users.append("a download")
        return users

    def _prune_live_sessions(self) -> None:
        """丟掉已結束、元件已釋放完、載入執行緒也結束的 session（呼叫端持有 _session_lock）。"""
        self._live_sessions = [
            s
            for s in self._live_sessions
            if not s.terminal_sent
            or s.components is not None
            or s.releasing
            or (s.loader is not None and s.loader.is_alive())
        ]

    def _register_download(
        self, kind: DownloadKind, model: str, expected_total: int, job: DownloadJob
    ) -> threading.Thread:
        """登記一個下載並建立（尚未啟動的）執行緒；呼叫端在回應寫出後才啟動它。

        Raises:
            ProtocolError: `download_in_progress`（同一個模型已在下載）。
        """
        with self._downloads_lock:
            if (kind, model) in self._downloads:
                raise p.ProtocolError(
                    p.DOWNLOAD_IN_PROGRESS, f"{model} is already downloading"
                )
            download = _Download(kind, model)
            self._downloads[(kind, model)] = download
        download.thread = threading.Thread(
            target=self._run_download,
            args=(download, expected_total, job),
            name=f"download-{kind}-{model}",
            daemon=True,
        )
        return download.thread

    def _unregister_download(self, kind: DownloadKind, model: str) -> None:
        """撤銷尚未啟動的下載（回應沒寫出去時）；已啟動的不動，由執行緒結束時自行移除。"""
        with self._downloads_lock:
            download = self._downloads.get((kind, model))
            if download is None:
                return
            thread = download.thread
            if thread is not None and thread.ident is not None:
                return
            del self._downloads[(kind, model)]
        logger.info("download of %s was not started: the response was not sent", model)

    def _run_download(
        self, download: _Download, expected_total: int, job: DownloadJob
    ) -> None:
        last_sent = 0.0
        last_fraction = -1.0
        total = expected_total

        def send(downloaded: int, size: int, state: str, error: str | None) -> None:
            self._send_event(
                "download_progress",
                {
                    "model": download.model,
                    "kind": download.kind,
                    "downloaded_bytes": downloaded,
                    "total_bytes": size,
                    "state": state,
                    "error": error,
                },
            )

        def progress(downloaded: int, reported_total: int) -> None:
            nonlocal last_sent, last_fraction, total
            total = reported_total
            now = time.monotonic()
            fraction = downloaded / reported_total if reported_total > 0 else 0.0
            # 1 MiB 回報一次，GB 級模型會有上千次；節流避免塞爆 stdout。
            if now - last_sent < PROGRESS_INTERVAL and fraction - last_fraction < 0.01:
                return
            last_sent, last_fraction = now, fraction
            send(downloaded, reported_total, "downloading", None)

        # 最後的 done／error 在撤銷登記之後才送：UI 收到 done 就可能立刻 models.delete，
        # 登記還在時會被誤判成「下載中」而得到 model_in_use。
        final: tuple[str, str | None] | None = None
        try:
            send(0, expected_total, "downloading", None)
            job(progress, download.cancel)
            final = ("done", None)
            logger.info("%s downloaded", download.model)
        except ChannelClosedError:
            logger.error("download of %s: protocol output closed", download.model)
        except Exception as exc:
            logger.exception("download of %s failed", download.model)
            final = ("error", f"{type(exc).__name__}: {exc}")
        finally:
            with self._downloads_lock:
                self._downloads.pop((download.kind, download.model), None)
        if final is None:
            return
        state, error = final
        try:
            send(total if state == "done" else 0, total, state, error)
        except ChannelClosedError:
            logger.error(
                "cannot report the %s of %s: protocol output closed",
                "completion" if state == "done" else "failure",
                download.model,
            )

    # --- session ----------------------------------------------------------------

    def _session_start(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params, ("mode", "device_id"))
        mode = params.get("mode")
        if mode not in MODES:
            raise p.ProtocolError(
                p.INVALID_PARAMS, f"mode must be one of {list(MODES)}, got {mode!r}"
            )
        settings = self._services.settings_store.get()
        if mode == "interpret":
            # 設定只在 `mode` 為 interpret 時檢查對方語言；這裡的 mode 由請求決定、不經過
            # 設定驗證，所以要再檢查一次，否則口譯無法靠語言偵測分辨說話者。
            try:
                check_interpret_languages(
                    settings.target_language, settings.partner_language
                )
            except SettingsError as exc:
                raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        if "device_id" in params:
            device_id = params["device_id"]
            if device_id is not None and (
                not isinstance(device_id, str)
                or not device_id.strip()
                or len(device_id) > MAX_DEVICE_ID_CHARS
            ):
                raise p.ProtocolError(
                    p.INVALID_PARAMS,
                    f"device_id must be null or a non-empty string, got {device_id!r}",
                )
        else:
            device_id = settings.mode_settings(mode).device_id

        with self._session_lock:
            if self._session is not None:
                raise p.ProtocolError(
                    p.SESSION_ACTIVE,
                    f"session {self._session.id} is already running; stop it first",
                )
            try:
                source = self._services.open_source(device_id, KIND_FOR_MODE[mode])
            except CaptureError as exc:
                raise p.ProtocolError(_capture_code(exc), str(exc)) from exc
            except ValueError as exc:
                raise p.ProtocolError(p.INVALID_PARAMS, str(exc)) from exc
            # 翻譯關閉的 session 也記：執行中可以開啟翻譯；卸載沒載入的模型不會載入它。
            # 在建立 session 之前：萬一出錯，不會留下一個永遠不啟動的 session。
            self._note_translation_models(settings)
            session = _Session(
                self._next_session_id,
                mode,
                frozenset((settings.asr.fast_model, settings.asr.final_model)),
            )
            self._next_session_id += 1
            self._session = session
            # 之後只替這個 session 的日文字幕標振假名；辭典等第一句需要時才載入
            self._furigana.begin_session(session.id, settings.furigana.enabled)
            # 順便清掉舊的紀錄：開關很多次 session 時清單不會一直變長
            self._prune_live_sessions()
            self._live_sessions.append(session)
            previous = self._last_loader
            session.loader = threading.Thread(
                target=self._load_session,
                args=(session, settings, source, previous),
                name=f"session-{session.id}",
                daemon=True,
            )
            self._last_loader = session.loader

        self._after_response.append(session.loader.start)
        return {"session_id": session.id}

    def _load_session(
        self,
        session: _Session,
        settings: Any,
        source: AudioSource,
        previous: threading.Thread | None = None,
    ) -> None:
        """背景載入：等前一個 session 的載入結束 → 翻譯模型檢查 → 建立元件 → 開始。

        停止（`session.cancelled`）在每一步之間檢查：檢查翻譯伺服器（數秒）、載入 ASR
        與預熱（`build_components`，數秒到數十秒）本身不能中斷，停止後做完這一步就結束，
        不再進行下一步（`build_components` 也拿到 `session.cancelled`：ASR 載入完時已停止
        就不預熱）。載入期間每完成一步送一則帶 `data` 的 `loading`（`_loading_progress`）。

        為什麼要等前一個 session：被停止的 session 會立即送出 idle 並釋放 `_session`，
        UI 可以馬上開始下一個；但它的載入執行緒可能還卡在 `build_components()` 裡。兩個
        執行緒同時載入 ASR 會在 GPU 上配置兩份模型（每份約 2.6 GB），預熱也會與 Ollama
        搶顯示記憶體（見 factory 的 `build_session_components()`）。所以新的 session 先
        送出 `loading`，再等前一個載入執行緒結束（它看到 cancelled 後不會啟動 pipeline）；
        等待期間這個 session 也能被停止。代價是開始得比較慢（最多是前一個 session 剩下
        的那一步，例如預熱的 60 秒讀取逾時），而不是讓兩份模型同時佔用記憶體。
        """
        try:
            self._session_status(session, "loading", None, None)
            if not self._wait_for_loader(session, previous):
                return
            missing = self._missing_translation_models(settings, session.mode)
            if session.cancelled.is_set():
                # 檢查期間被停止：不要再載入 ASR（數秒、佔 GPU）
                return
            if missing:
                # 先於載入 ASR（數秒）回報：UI 以 models.pull_translation 下載後重試
                self._session_failed(
                    session,
                    "translation_model_missing",
                    "translation model(s) not installed on the Ollama server: "
                    f"{', '.join(missing)}; download them with "
                    "models.pull_translation",
                    data={"models": missing},
                )
                return
            try:
                components = self._services.build_components(
                    settings,
                    session.mode,
                    progress=lambda step, done, total: self._loading_progress(
                        session, step, done, total
                    ),
                    cancelled=session.cancelled,
                )
            except ChannelClosedError:
                # 送進度時 stdout 已關閉（引擎正在結束）：不是載入錯誤，交給外層記錄
                raise
            except UnknownModelError as exc:
                _clear_tracebacks(exc)
                self._session_failed(session, "unknown_model", str(exc))
                return
            except FileNotFoundError as exc:
                _clear_tracebacks(exc)
                self._session_failed(
                    session,
                    "model_not_downloaded",
                    f"{exc}; download it with models.download first",
                )
                return
            except Exception as exc:
                logger.exception("loading session %d failed", session.id)
                _clear_tracebacks(exc)
                self._session_failed(
                    session, "load_error", f"{type(exc).__name__}: {exc}"
                )
                return
            with self._session_lock:
                # 之後由送出結束狀態的一方取走並釋放（見 `_session_status`）
                session.components = components
                stopped = session.terminal_sent
            if stopped or session.cancelled.is_set():
                # 載入與預熱期間被停止：釋放元件（ASR 模型放回模型池、權重離開 GPU）
                logger.info("session %d stopped while loading", session.id)
                self._release_components(session)
                return

            pipeline = Pipeline(
                components,
                pipeline_config(settings, session.mode),
                on_subtitle=lambda update: self._on_subtitle(session, update),
                on_status=lambda state, detail: self._on_pipeline_status(
                    session, state, detail
                ),
            )
            with session.lock:
                if session.cancelled.is_set():
                    self._release_components(session)
                    return
                session.pipeline = pipeline
                if session.translation_override is not None:
                    pipeline.set_translation_enabled(session.translation_override)
                reporter = _MetricsReporter(
                    session.id, pipeline, self._send_event, self._metrics_interval
                )
                with self._session_lock:
                    # 結束狀態送出時取走並停止；已經結束（不會發生，保險）就不啟動
                    if not session.terminal_sent:
                        session.metrics = reporter
                        reporter.start()
                try:
                    pipeline.start(source)
                except CaptureError as exc:
                    logger.error("cannot start audio capture: %s", exc)
                    _clear_tracebacks(exc)
                    self._session_failed(session, _capture_code(exc), str(exc))
                except Exception as exc:
                    logger.exception("starting session %d failed", session.id)
                    _clear_tracebacks(exc)
                    self._session_failed(
                        session, "load_error", f"{type(exc).__name__}: {exc}"
                    )
        except ChannelClosedError:
            logger.error("session %d: protocol output closed", session.id)

    def _loading_progress(
        self, session: _Session, step: str, done: int, total: int
    ) -> None:
        """載入進度：`status loading` 加上 `data: {step, done, total}`。

        第一則 `loading`（開始載入時）沒有 `data`，舊的 UI 照樣只看 `state`。session 已送出
        結束狀態（例如載入中被停止）後的進度由 `_session_status` 丟掉。
        """
        self._session_status(
            session,
            "loading",
            None,
            None,
            data={"step": step, "done": done, "total": total},
        )

    def _wait_for_loader(
        self, session: _Session, previous: threading.Thread | None
    ) -> bool:
        """等前一個 session 的載入執行緒結束（理由見 `_load_session`）。

        Returns:
            False 表示等待期間這個 session 也被停止了（不必再載入）。
        """
        if previous is None or not previous.is_alive():
            return not session.cancelled.is_set()
        logger.info(
            "session %d: waiting for session %s to finish loading",
            session.id,
            previous.name.removeprefix("session-"),
        )
        while previous.is_alive():
            if session.cancelled.is_set():
                return False
            previous.join(LOADER_POLL_SECS)
        return not session.cancelled.is_set()

    def _on_subtitle(self, session: _Session, update: SubtitleUpdate) -> None:
        data = subtitle_event(update, session.id)
        ruby = self._furigana.annotate(
            session.id, update.original, update.source_language
        )
        if ruby is not None:
            data["ruby"] = [[start, end, reading] for start, end, reading in ruby]
        self._send_event("subtitle", data)

    def _on_pipeline_status(
        self, session: _Session, state: PipelineState, detail: str | None
    ) -> None:
        code = None
        if state == "error":
            # 執行中的擷取錯誤（例如拔掉 USB 裝置的 DeviceLostError）用擷取的 code，
            # 介面才能說明是裝置的問題；其他錯誤是 pipeline_error。session.pipeline 在
            # pipeline.start() 之前就設好，只有送出結束狀態（_session_status）後才清成
            # None，而之後的狀態都會被 terminal_sent 丟掉，所以這裡讀得到。
            pipeline = session.pipeline
            error = pipeline.error if pipeline is not None else None
            code = (
                _capture_code(error)
                if isinstance(error, CaptureError)
                else "pipeline_error"
            )
        self._session_status(session, state, detail, code)

    def _missing_translation_models(self, settings: Settings, mode: Mode) -> list[str]:
        """這個 session 會用到、但 Ollama 上沒有的翻譯模型。

        只有確定「伺服器是 Ollama、連得上、而且列得出已安裝的模型」時才回報缺少：那時
        UI 可以用 `models.pull_translation` 補上再重試。連不上或不是 Ollama 時照常開始——
        字幕的原文不依賴翻譯，每句翻譯失敗只顯示原文並提醒一次（pipeline 的既有行為）；
        使用者可能正要開 Ollama，或用的是其他服務，不該因此無法開始。只顯示原文或不需要
        翻譯的語言組合根本不檢查（不連線）。
        """
        if not translation_needed(settings, mode):
            return []
        try:
            state = self._services.translation_models.server_state(settings)
        except Exception:
            # 檢查只是為了提早提示；檢查本身的程式錯誤不能讓 session 開不起來
            logger.exception("cannot check the translation server")
            return []
        if state.auth_failed:
            # 與連不上相同：無法確認模型在不在，照常開始（原文不依賴翻譯）。但這不是
            # 「Ollama 還沒開」那種會自己好的狀況，每句翻譯都會失敗，所以記 warning
            # （經 log 事件到 UI），讓使用者知道要檢查金鑰。不另送 status code：status
            # 的 code 只用在 error 狀態，session 本身仍會正常開始。
            logger.warning(
                "the translation server rejected the API key (HTTP 401/403); "
                "starting without checking translation models, and translations will "
                "fail until the key in translation.api_key_env is fixed: %s",
                state.error,
            )
            return []
        if not state.reachable or not state.is_ollama or state.installed is None:
            logger.info(
                "not checking translation models (reachable=%s, ollama=%s): %s",
                state.reachable,
                state.is_ollama,
                state.error,
            )
            return []
        installed = {normalize_model_name(m) for m in state.installed}
        t = settings.translation
        return [
            model
            for model in dict.fromkeys((t.fast_model, t.final_model))
            if normalize_model_name(model) not in installed
        ]

    def _session_set_translation(self, params: dict[str, Any]) -> Mapping[str, Any]:
        """執行中切換「顯示翻譯」，並存進設定的 `translation.enabled`。"""
        p.reject_unknown_params(params, ("enabled",))
        enabled = params.get("enabled")
        if not isinstance(enabled, bool):
            raise p.ProtocolError(
                p.INVALID_PARAMS, f"enabled must be true or false, got {enabled!r}"
            )
        # 先存設定：存不進去就回錯誤、不切換，畫面與設定才不會不一致
        try:
            self._services.settings_store.update({"translation": {"enabled": enabled}})
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        except OSError as exc:
            logger.error("cannot save settings: %s", exc)
            raise p.ProtocolError(
                p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
            ) from exc
        with self._session_lock:
            session = self._session
        applied_to = None
        if session is not None:
            with session.lock:
                # 還在載入（pipeline 尚未建立）時記下來，建好時套用
                session.translation_override = enabled
                pipeline = session.pipeline
            if pipeline is not None:
                pipeline.set_translation_enabled(enabled)
            applied_to = session.id
        return {**self._settings_result(), "session_id": applied_to}

    def _session_set_furigana(self, params: dict[str, Any]) -> Mapping[str, Any]:
        """執行中切換振假名，並存進設定的 `furigana.enabled`。

        開啟時辭典必須已下載（否則 `model_not_downloaded`，設定不變）：UI 先以
        `models.download {"model": "furigana-dictionary"}` 下載，完成後再開啟。
        """
        p.reject_unknown_params(params, ("enabled",))
        enabled = params.get("enabled")
        if not isinstance(enabled, bool):
            raise p.ProtocolError(
                p.INVALID_PARAMS, f"enabled must be true or false, got {enabled!r}"
            )
        if enabled and not self._furigana.provider.status().installed:
            raise p.ProtocolError(
                p.MODEL_NOT_DOWNLOADED,
                "the furigana dictionary is not downloaded; download it with "
                f"models.download (model {DICTIONARY_ID}) first",
            )
        # 先存設定：存不進去就回錯誤、不切換，畫面與設定才不會不一致
        try:
            self._services.settings_store.update({"furigana": {"enabled": enabled}})
        except SettingsError as exc:
            raise p.ProtocolError(p.INVALID_SETTINGS, str(exc)) from exc
        except OSError as exc:
            logger.error("cannot save settings: %s", exc)
            raise p.ProtocolError(
                p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
            ) from exc
        self._furigana.set_enabled(enabled)
        with self._session_lock:
            session = self._session
        return {
            **self._settings_result(),
            "session_id": None if session is None else session.id,
        }

    def _session_failed(
        self,
        session: _Session,
        code: str,
        message: str,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        self._session_status(session, "error", message, code, data)

    def _session_status(
        self,
        session: _Session,
        state: str,
        detail: str | None,
        code: str | None,
        data: Mapping[str, Any] | None = None,
    ) -> None:
        """送出這個 session 的狀態；結束狀態（idle／error）只送一次並釋放 session。

        結束時依序：停止 metrics（之後不會再有這個 session 的 metrics）→ 釋放元件（ASR
        模型卸載、放回模型池，最多 `RELEASE_TIMEOUT` 秒）→ 送出狀態。為什麼先釋放：UI
        收到 idle／error 就可能開始下一個 session，舊模型不能還佔著顯示記憶體。
        """
        terminal = state in ("idle", "error")
        if not terminal:
            # 檢查與寫出在同一把鎖內：否則已通過檢查的 loading（載入進度）或 running 可能在
            # 另一條執行緒送出 idle 之後才寫出，UI 會在結束後又看到這個 session 在載入。
            # 不用 _session_lock：寫 stdout 可能卡住，不能擋住其他請求。
            with session.status_lock:
                with self._session_lock:
                    if session.terminal_sent:
                        return
                self._send_status(state, detail, session.id, code, data)
            return
        reporter: _MetricsReporter | None = None
        # 等進行中的非結束狀態寫完才標記結束（見上）；之後的非結束狀態都會被丟掉
        with session.status_lock, self._session_lock:
            if session.terminal_sent:
                return
            session.terminal_sent = True
            reporter, session.metrics = session.metrics, None
            if self._session is session:
                self._session = None
            # pipeline 的回呼參照 session，session 又參照 pipeline：不切斷的話這個循環
            # 要等循環垃圾回收才會釋放，GPU 上的 ASR 模型（每個 session 約 2.6 GB）會
            # 一直佔著，開幾次 session 就把顯示記憶體用完。結束後 pipeline 只剩自己的
            # 執行緒參照，執行緒結束時以參照計數立即釋放。不取 session.lock：
            # `_load_session` 持有它時也可能走到這裡（啟動失敗）。
            session.pipeline = None
        if reporter is not None:
            reporter.stop()
        self._release_components(session)
        # 辭典與 ASR 模型一樣在送出結束狀態之前釋放
        self._furigana.end_session(session.id)
        self._send_status(state, detail, session.id, code, data)

    def _release_components(self, session: _Session) -> None:
        """取走並釋放 session 的元件，最多等 `RELEASE_TIMEOUT` 秒（可重複呼叫）。

        只有一方會取到元件，所以不會釋放兩次。釋放在另一條執行緒上進行：`close()` 卡住
        （例如推論不返回）時，這裡逾時後記 warning 就返回，結束狀態照樣送出；釋放做完
        之前 `session.releasing` 為 True，`models.delete` 仍視為使用中。
        """
        with self._session_lock:
            components, session.components = session.components, None
            if components is None:
                return
            session.releasing = True

        def release() -> None:
            try:
                release_components(components)
            finally:
                with self._session_lock:
                    session.releasing = False

        thread = threading.Thread(
            target=release, name=f"session-{session.id}-release", daemon=True
        )
        thread.start()
        thread.join(RELEASE_TIMEOUT)
        if thread.is_alive():
            logger.warning(
                "session %d is still releasing its models after %.1f s; "
                "reporting the session as ended anyway",
                session.id,
                RELEASE_TIMEOUT,
            )

    def _session_stop(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        self._stop_session()
        return {}

    def _stop_session(self) -> None:
        with self._session_lock:
            session = self._session
        if session is None:
            return
        session.cancelled.set()
        with session.lock:
            pipeline = session.pipeline
        if pipeline is not None:
            pipeline.stop(STOP_TIMEOUT)
        # 還在載入模型時 pipeline 尚未建立；載入完會看到 cancelled 而不啟動。
        self._session_status(session, "idle", None, None)

    def _engine_shutdown(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        self._shutdown.set()
        return {}
