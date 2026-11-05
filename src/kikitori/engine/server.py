"""stdio NDJSON 引擎伺服器：Tauri 程式與 Python 處理管線之間的唯一入口。

位於整個 Python 套件的最上層：讀 stdin 的請求、分派到各方法、把 pipeline 的字幕
與狀態轉成事件寫到 stdout。stdin 讀取在主執行緒依序處理；載入模型、下載這類
耗時工作放在背景執行緒，讓 `session.stop`、`engine.shutdown` 隨時能被處理。
協定見 `docs/specs/app-architecture.md`，細節見 `docs/specs/pipeline-engine.md`。
"""

import dataclasses
import logging
import platform
import threading
import time
from collections.abc import Callable, Mapping
from importlib import metadata
from typing import Any, BinaryIO

from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
    PermissionDeniedError,
)
from kikitori.capture.types import AudioSource, DeviceKind
from kikitori.config import SettingsError
from kikitori.engine import protocol as p
from kikitori.engine.deferred import Deferred, DeferredRunner
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
from kikitori.hardware import GPU_RUNTIME_ID
from kikitori.pipeline import MODES, Mode, Pipeline, PipelineState, SubtitleUpdate
from kikitori.pipeline.factory import UnknownModelError, pipeline_config

ENGINE_NAME = "kikitori-engine"
STOP_TIMEOUT = 5.0
DOWNLOAD_JOIN_TIMEOUT = 2.0
DEFERRED_JOIN_TIMEOUT = 2.0
PROGRESS_INTERVAL = 0.25
MAX_DEVICE_ID_CHARS = 512

KIND_FOR_MODE: dict[Mode, DeviceKind] = {"media": "loopback", "interpret": "microphone"}

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


class _Session:
    """一次 session：背景載入 → 執行 → 結束。`lock` 保護 pipeline 的建立與停止。"""

    def __init__(self, session_id: int, mode: Mode) -> None:
        self.id = session_id
        self.mode = mode
        self.cancelled = threading.Event()
        self.lock = threading.Lock()
        self.pipeline: Pipeline | None = None
        self.terminal_sent = False
        self.loader: threading.Thread | None = None


class _Download:
    def __init__(self, model: str) -> None:
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
    ) -> None:
        """
        Args:
            services: 外部功能（真實或測試用的假實作）。
            writer: stdout 的訊息寫出器。
            version: `engine.info` 回報的版本；省略時讀取套件版本。
        """
        self._services = services
        self._writer = writer
        self._version = version or engine_version()
        self._shutdown = threading.Event()
        self._session_lock = threading.Lock()
        self._session: _Session | None = None
        self._next_session_id = 1
        self._downloads: dict[str, _Download] = {}
        self._downloads_lock = threading.Lock()
        # 偵測硬體可能要數秒：在背景工作執行緒上處理，stdin 仍能立即處理 session.stop 等。
        self._deferred = DeferredRunner(self._send)
        self._hardware = HardwareService(services.hardware, services.models)
        # 回應寫出後才執行的動作：讓 UI 先拿到 session_id，再收到該 session 的事件。
        self._after_response: list[Callable[[], None]] = []
        self._handlers: dict[str, Handler] = {
            "engine.info": self._engine_info,
            "hardware.detect": self._hardware_detect,
            "models.list": self._models_list,
            "models.download": self._models_download,
            "models.validate": self._models_validate,
            "devices.list": self._devices_list,
            "settings.get": self._settings_get,
            "settings.set": self._settings_set,
            "settings.apply_recommendation": self._settings_apply_recommendation,
            "session.start": self._session_start,
            "session.stop": self._session_stop,
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
            self._deferred.submit(request.id, request.method, result.job)
            return
        self._send(p.response(request.id, result))
        actions, self._after_response = self._after_response, []
        for action in actions:
            action()

    def close(self) -> None:
        """停止 session、放棄尚未開始的背景請求、取消下載（可重複呼叫）。"""
        self._stop_session()
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
    ) -> None:
        self._send_event(
            "status",
            {"state": state, "detail": detail, "session_id": session_id, "code": code},
        )

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

    def _devices_list(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params)
        try:
            devices = self._services.list_devices()
        except CaptureError as exc:
            raise p.ProtocolError(_capture_code(exc), str(exc)) from exc
        return {"devices": [dataclasses.asdict(d) for d in devices]}

    def _settings_result(self) -> Mapping[str, Any]:
        store = self._services.settings_store
        return {"settings": store.get().to_dict(), "path": str(store.path)}

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
        p.reject_unknown_params(params)
        settings = self._services.settings_store.get()

        def job() -> Mapping[str, Any]:
            _, rec = self._hardware.recommendation(settings)
            patch = recommendation_patch(rec)
            try:
                self._services.settings_store.update(patch)
            except OSError as exc:
                logger.error("cannot save settings: %s", exc)
                raise p.ProtocolError(
                    p.SETTINGS_IO_ERROR, f"cannot save settings: {exc}"
                ) from exc
            # SettingsError 不轉成 invalid_settings：推薦組合不合法是程式錯誤（internal_error）
            return {
                **self._settings_result(),
                "applied": patch,
                "recommendation": rec.to_dict(),
            }

        return Deferred(job)

    # --- models -----------------------------------------------------------------

    def _models_list(self, params: dict[str, Any]) -> Deferred:
        p.reject_unknown_params(params)
        settings = self._services.settings_store.get()
        return Deferred(lambda: self._hardware.models_list(settings))

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
        known = {m.id: m for m in self._services.models.asr_models()}
        if model not in known:
            available = ", ".join([*known, GPU_RUNTIME_ID])
            raise p.ProtocolError(
                p.UNKNOWN_MODEL,
                f"unknown model {model[:100]!r}; available: {available}",
            )
        catalog = self._services.models

        def download(progress: ProgressCallback, cancel: threading.Event) -> None:
            catalog.download(model, progress, cancel)

        return self._start_download(model, known[model].size_bytes, download)

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

        return self._start_download(GPU_RUNTIME_ID, status.download_bytes or 0, install)

    def _start_download(
        self, model: str, expected_total: int, job: DownloadJob
    ) -> Mapping[str, Any]:
        with self._downloads_lock:
            if model in self._downloads:
                raise p.ProtocolError(
                    p.DOWNLOAD_IN_PROGRESS, f"{model} is already downloading"
                )
            download = _Download(model)
            self._downloads[model] = download
        download.thread = threading.Thread(
            target=self._run_download,
            args=(download, expected_total, job),
            name=f"download-{model}",
            daemon=True,
        )
        self._after_response.append(download.thread.start)
        return {"model": model, "started": True}

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

        try:
            send(0, expected_total, "downloading", None)
            job(progress, download.cancel)
            send(total, total, "done", None)
            logger.info("%s downloaded", download.model)
        except ChannelClosedError:
            logger.error("download of %s: protocol output closed", download.model)
        except Exception as exc:
            logger.exception("download of %s failed", download.model)
            try:
                send(0, total, "error", f"{type(exc).__name__}: {exc}")
            except ChannelClosedError:
                logger.error("cannot report download failure: protocol output closed")
        finally:
            with self._downloads_lock:
                self._downloads.pop(download.model, None)

    # --- session ----------------------------------------------------------------

    def _session_start(self, params: dict[str, Any]) -> Mapping[str, Any]:
        p.reject_unknown_params(params, ("mode", "device_id"))
        mode = params.get("mode")
        if mode not in MODES:
            raise p.ProtocolError(
                p.INVALID_PARAMS, f"mode must be one of {list(MODES)}, got {mode!r}"
            )
        settings = self._services.settings_store.get()
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
            session = _Session(self._next_session_id, mode)
            self._next_session_id += 1
            self._session = session

        session.loader = threading.Thread(
            target=self._load_session,
            args=(session, settings, source),
            name=f"session-{session.id}",
            daemon=True,
        )
        self._after_response.append(session.loader.start)
        return {"session_id": session.id}

    def _load_session(
        self, session: _Session, settings: Any, source: AudioSource
    ) -> None:
        try:
            self._session_status(session, "loading", None, None)
            try:
                components = self._services.build_components(settings, session.mode)
            except UnknownModelError as exc:
                self._session_failed(session, "unknown_model", str(exc))
                return
            except FileNotFoundError as exc:
                self._session_failed(
                    session,
                    "model_not_downloaded",
                    f"{exc}; download it with models.download first",
                )
                return
            except Exception as exc:
                logger.exception("loading session %d failed", session.id)
                self._session_failed(
                    session, "load_error", f"{type(exc).__name__}: {exc}"
                )
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
                    return
                session.pipeline = pipeline
                try:
                    pipeline.start(source)
                except CaptureError as exc:
                    logger.error("cannot start audio capture: %s", exc)
                    self._session_failed(session, _capture_code(exc), str(exc))
                except Exception as exc:
                    logger.exception("starting session %d failed", session.id)
                    self._session_failed(
                        session, "load_error", f"{type(exc).__name__}: {exc}"
                    )
        except ChannelClosedError:
            logger.error("session %d: protocol output closed", session.id)

    def _on_subtitle(self, session: _Session, update: SubtitleUpdate) -> None:
        data = dataclasses.asdict(update)
        data["session_id"] = session.id
        self._send_event("subtitle", data)

    def _on_pipeline_status(
        self, session: _Session, state: PipelineState, detail: str | None
    ) -> None:
        code = "pipeline_error" if state == "error" else None
        self._session_status(session, state, detail, code)

    def _session_failed(self, session: _Session, code: str, message: str) -> None:
        self._session_status(session, "error", message, code)

    def _session_status(
        self, session: _Session, state: str, detail: str | None, code: str | None
    ) -> None:
        """送出這個 session 的狀態；結束狀態（idle／error）只送一次並釋放 session。"""
        terminal = state in ("idle", "error")
        with self._session_lock:
            if session.terminal_sent:
                return
            if terminal:
                session.terminal_sent = True
                if self._session is session:
                    self._session = None
                # pipeline 的回呼參照 session，session 又參照 pipeline：不切斷的話這個循環
                # 要等循環垃圾回收才會釋放，GPU 上的 ASR 模型（每個 session 約 2.6 GB）會
                # 一直佔著，開幾次 session 就把顯示記憶體用完。結束後 pipeline 只剩自己的
                # 執行緒參照，執行緒結束時以參照計數立即釋放。不取 session.lock：
                # `_load_session` 持有它時也可能走到這裡（啟動失敗）。
                session.pipeline = None
        self._send_status(state, detail, session.id, code)

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
