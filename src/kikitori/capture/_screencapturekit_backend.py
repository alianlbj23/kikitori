"""macOS 系統聲音後端：ScreenCaptureKit（macOS 13+），經由 PyObjC 呼叫。

位於 `devices.py` 之下，是 macOS 上 `kind="loopback"` 的預設來源。ScreenCaptureKit
沒有純音訊模式，所以擷取整個主螢幕但把影像壓到 2×2、每秒 1 張並丟棄，只取音訊。
系統在我們建立的 serial dispatch queue 上推送樣本（推送式，不需要擷取執行緒）。

PyObjC 的呼叫集中在 `_PyObjCApi`；其餘邏輯（解碼、錯誤對應、緩衝）是純 Python，
可在任何平台以假物件測試。所有 PyObjC 匯入都延遲到 `start()`。
"""

import functools
import logging
import platform as platform_mod
import sys
import threading
from collections.abc import Callable
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

from kikitori.capture.buffer import ChunkBuffer, validate_timeout
from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
    PermissionDeniedError,
)
from kikitori.capture.resample import AudioConverter
from kikitori.capture.threaded import (
    JOIN_TIMEOUT,
    MAX_BUFFER_SECONDS,
    OPEN_TIMEOUT,
    positive_seconds,
)
from kikitori.capture.types import SAMPLE_RATE, AudioChunk, DeviceInfo

logger = logging.getLogger(__name__)

BACKEND = "screencapturekit"
DEVICE_ID = f"{BACKEND}:loopback:system"
# 裝置名稱會原樣顯示在 UI 的裝置選單；UI 有多種介面語系，所以用英文（與其他平台的
# 裝置名稱一樣是系統提供的名稱，不由引擎翻譯）。
DEVICE_NAME = "System audio (ScreenCaptureKit)"
MIN_MACOS_MAJOR = 13  # SCStreamConfiguration.capturesAudio 從 macOS 13 開始
CAPTURE_RATE = 48000
# 要求單聲道：由系統降混音，就不必判斷 CMSampleBuffer 是交錯還是平面格式。
CAPTURE_CHANNELS = 1
SC_ERROR_USER_DECLINED = -3801  # SCStreamErrorUserDeclined
_PERMISSION_HINT = (
    "Screen Recording permission is required: enable it in System Settings > "
    "Privacy & Security > Screen & System Audio Recording, then restart the app"
)


def is_supported(platform: str = sys.platform, mac_version: str | None = None) -> bool:
    """此平台是否能用 ScreenCaptureKit 擷取系統聲音（macOS 13 以上）。"""
    if platform != "darwin":
        return False
    version = platform_mod.mac_ver()[0] if mac_version is None else mac_version
    try:
        major = int(version.split(".")[0])
    except ValueError:
        logger.warning("cannot parse macOS version %r", version)
        return False
    return major >= MIN_MACOS_MAJOR


def system_audio_device() -> DeviceInfo:
    """ScreenCaptureKit 系統聲音在裝置清單中的項目（macOS 唯一的 loopback，故為預設）。"""
    return DeviceInfo(id=DEVICE_ID, name=DEVICE_NAME, kind="loopback", is_default=True)


def decode_mono_float32(raw: bytes, frames: int) -> npt.NDArray[np.float32]:
    """把單聲道 float32 PCM 位元組轉成 (frames, 1) 陣列；長度不符就報錯。"""
    if frames < 0 or len(raw) != frames * 4:
        raise CaptureError(
            f"unexpected ScreenCaptureKit audio layout: {len(raw)} bytes for {frames} frames"
        )
    return np.frombuffer(raw, dtype=np.float32).reshape(-1, 1)


def map_ns_error(
    code: int | None, description: str, *, while_running: bool = False
) -> CaptureError:
    """把 ScreenCaptureKit 的 NSError（錯誤碼與說明）轉成 capture 的例外類別。"""
    if code == SC_ERROR_USER_DECLINED:
        return PermissionDeniedError(f"{_PERMISSION_HINT} ({description})")
    if while_running:
        return DeviceLostError(
            f"ScreenCaptureKit stream stopped: {description} (code {code})"
        )
    return CaptureError(f"ScreenCaptureKit failed: {description} (code {code})")


def _ns_error_info(error: Any) -> tuple[int | None, str]:
    if error is None:
        return None, "unknown error"
    return int(error.code()), str(error.localizedDescription())


class _StreamHandler:
    """接收 ScreenCaptureKit 回呼的純 Python 物件；在 serial dispatch queue 上依序被呼叫。

    回呼不得拋例外（會穿過 ObjC 邊界），錯誤一律記錄並交給緩衝，由 `read()` 拋出。
    """

    def __init__(
        self,
        buffer: ChunkBuffer,
        audio_type: int,
        extract: Callable[[Any], tuple[bytes, int]],
    ) -> None:
        self._buffer = buffer
        self._audio_type = audio_type
        self._extract = extract
        self._converter = AudioConverter(CAPTURE_RATE, CAPTURE_CHANNELS)

    def on_sample_buffer(self, sample_buffer: Any, output_type: int) -> None:
        if int(output_type) != self._audio_type:
            return  # 影像畫面：只是為了滿足 ScreenCaptureKit 而存在，直接丟棄
        try:
            raw, frames = self._extract(sample_buffer)
            if frames == 0:
                return
            self._buffer.put(self._converter.process(decode_mono_float32(raw, frames)))
        except Exception as exc:  # noqa: BLE001
            # ObjC 回呼邊界：例外不能往外拋，改由讀取端拋出。
            error = (
                exc
                if isinstance(exc, CaptureError)
                else CaptureError(f"audio callback failed: {exc}")
            )
            if error is not exc:
                error.__cause__ = exc
            logger.error("ScreenCaptureKit audio callback failed: %s", error)
            self._buffer.fail(error)

    def on_stopped(self, error: Any) -> None:
        code, description = _ns_error_info(error)
        mapped = map_ns_error(code, description, while_running=True)
        logger.error("ScreenCaptureKit stream stopped: %s", mapped)
        self._buffer.fail(mapped)


class _ScreenCaptureApi(Protocol):
    """`ScreenCaptureKitSource` 需要的 ScreenCaptureKit 操作；測試以假物件替換。"""

    audio_output_type: int

    def shareable_content(self, timeout: float) -> Any: ...

    def start_stream(
        self, content: Any, handler: _StreamHandler, timeout: float
    ) -> Any: ...

    def stop_stream(self, stream: Any, timeout: float) -> None: ...

    def extract_audio(self, sample_buffer: Any) -> tuple[bytes, int]: ...


def _wait(event: threading.Event, timeout: float, what: str) -> None:
    if not event.wait(timeout):
        raise CaptureError(
            f"timed out after {timeout:.1f}s waiting for ScreenCaptureKit {what}"
        )


@functools.cache
def _output_class(objc: Any) -> Any:
    """定義接收樣本與停止通知的 ObjC 類別（ObjC 類別名稱全域唯一，只能定義一次）。"""
    ns_object = objc.lookUpClass("NSObject")
    protocols = [
        objc.protocolNamed("SCStreamOutput"),
        objc.protocolNamed("SCStreamDelegate"),
    ]

    class KikitoriStreamOutput(ns_object, protocols=protocols):  # type: ignore[misc,valid-type]
        def stream_didOutputSampleBuffer_ofType_(
            self, stream: Any, sample_buffer: Any, output_type: int
        ) -> None:
            self.handler.on_sample_buffer(sample_buffer, output_type)

        def stream_didStopWithError_(self, stream: Any, error: Any) -> None:
            self.handler.on_stopped(error)

    return KikitoriStreamOutput


class _PyObjCApi:
    """以 PyObjC 實作的 ScreenCaptureKit 操作（只在 macOS 可用）。"""

    def __init__(self) -> None:
        try:
            import AppKit
            import CoreMedia
            import dispatch
            import objc
            import ScreenCaptureKit
        except ImportError as exc:
            raise BackendUnavailableError(
                f"ScreenCaptureKit bindings unavailable: {exc}"
            ) from exc
        self._appkit = AppKit
        self._cm = CoreMedia
        self._dispatch = dispatch
        self._objc = objc
        self._sck = ScreenCaptureKit
        self.audio_output_type = int(ScreenCaptureKit.SCStreamOutputTypeAudio)
        # ObjC 不持有 output／queue 的強參照，必須由我們保留到串流停止。
        self._keepalive: list[Any] = []

    def _ensure_window_server_connection(self) -> None:
        # 命令列程式（例如 sidecar）沒有 NSApplication 時，ScreenCaptureKit 可能因為
        # 沒有 WindowServer 連線而失敗；設成 Prohibited 讓程式不出現在 Dock。
        # NSApplication 只能在主執行緒建立，其他執行緒就跳過並記錄。
        if threading.current_thread() is not threading.main_thread():
            logger.debug(
                "not on the main thread; skipping NSApplication initialisation"
            )
            return
        app = self._appkit.NSApplication.sharedApplication()
        app.setActivationPolicy_(self._appkit.NSApplicationActivationPolicyProhibited)

    def shareable_content(self, timeout: float) -> Any:
        self._ensure_window_server_connection()
        done = threading.Event()
        result: dict[str, Any] = {}

        def completion(content: Any, error: Any) -> None:
            result["content"] = content
            result["error"] = error
            done.set()

        # 第一次呼叫時系統會跳出「螢幕與系統錄音」權限詢問。
        self._sck.SCShareableContent.getShareableContentWithCompletionHandler_(
            completion
        )
        _wait(done, timeout, "shareable content")
        if result["error"] is not None:
            raise map_ns_error(*_ns_error_info(result["error"]))
        content = result["content"]
        if content is None or not content.displays():
            raise DeviceNotFoundError(
                "no display available for ScreenCaptureKit audio capture"
            )
        return content

    def start_stream(
        self, content: Any, handler: _StreamHandler, timeout: float
    ) -> Any:
        sck = self._sck
        display = content.displays()[0]
        content_filter = sck.SCContentFilter.alloc().initWithDisplay_excludingWindows_(
            display, []
        )
        config = sck.SCStreamConfiguration.alloc().init()
        config.setCapturesAudio_(True)
        config.setSampleRate_(CAPTURE_RATE)
        config.setChannelCount_(CAPTURE_CHANNELS)
        config.setExcludesCurrentProcessAudio_(True)  # 不把自己的聲音錄回來
        config.setWidth_(2)
        config.setHeight_(2)
        config.setMinimumFrameInterval_(self._cm.CMTimeMake(1, 1))
        config.setShowsCursor_(False)

        output = _output_class(self._objc).alloc().init()
        output.handler = handler
        queue = self._dispatch.dispatch_queue_create(
            b"io.kikitori.capture.screencapturekit", None
        )
        stream = sck.SCStream.alloc().initWithFilter_configuration_delegate_(
            content_filter, config, output
        )
        # 同時註冊 screen output（回呼中丟棄），避免系統因找不到 screen output 一直記錄錯誤。
        for output_type in (sck.SCStreamOutputTypeScreen, sck.SCStreamOutputTypeAudio):
            ok, error = stream.addStreamOutput_type_sampleHandlerQueue_error_(
                output, output_type, queue, None
            )
            if not ok:
                raise map_ns_error(*_ns_error_info(error))
        self._keepalive = [output, queue, stream]

        done = threading.Event()
        result: dict[str, Any] = {}

        def completion(error: Any) -> None:
            result["error"] = error
            done.set()

        stream.startCaptureWithCompletionHandler_(completion)
        _wait(done, timeout, "stream start")
        if result["error"] is not None:
            self._keepalive = []
            raise map_ns_error(*_ns_error_info(result["error"]))
        return stream

    def stop_stream(self, stream: Any, timeout: float) -> None:
        done = threading.Event()
        result: dict[str, Any] = {}

        def completion(error: Any) -> None:
            result["error"] = error
            done.set()

        stream.stopCaptureWithCompletionHandler_(completion)
        try:
            _wait(done, timeout, "stream stop")
            if result["error"] is not None:
                code, description = _ns_error_info(result["error"])
                logger.warning(
                    "ScreenCaptureKit stop reported an error: %s (code %s)",
                    description,
                    code,
                )
        finally:
            self._keepalive = []

    def extract_audio(self, sample_buffer: Any) -> tuple[bytes, int]:
        cm = self._cm
        if not cm.CMSampleBufferIsValid(
            sample_buffer
        ) or not cm.CMSampleBufferDataIsReady(sample_buffer):
            return b"", 0
        frames = int(cm.CMSampleBufferGetNumSamples(sample_buffer))
        block = cm.CMSampleBufferGetDataBuffer(sample_buffer)
        if block is None or frames == 0:
            return b"", 0
        length = int(cm.CMBlockBufferGetDataLength(block))
        # PyObjC 文件建議用 CMBlockBufferCopyDataBytes；GetDataPointer 無法在 Python 使用。
        status, data = cm.CMBlockBufferCopyDataBytes(block, 0, length, None)
        if status != 0:
            raise CaptureError(
                f"CMBlockBufferCopyDataBytes failed with status {status}"
            )
        return bytes(data), frames


class ScreenCaptureKitSource:
    """以 ScreenCaptureKit 擷取 macOS 系統聲音的 `AudioSource`。"""

    def __init__(
        self,
        *,
        api_factory: Callable[[], _ScreenCaptureApi] = _PyObjCApi,
        max_buffer_seconds: float = MAX_BUFFER_SECONDS,
        open_timeout: float = OPEN_TIMEOUT,
        join_timeout: float = JOIN_TIMEOUT,
    ) -> None:
        self._api_factory = api_factory
        max_samples = round(
            positive_seconds("max_buffer_seconds", max_buffer_seconds) * SAMPLE_RATE
        )
        self._open_timeout = positive_seconds("open_timeout", open_timeout)
        self._join_timeout = positive_seconds("join_timeout", join_timeout)
        self._buffer = ChunkBuffer(max(1, max_samples))
        self._api: _ScreenCaptureApi | None = None
        self._stream: Any = None
        self._started = False

    @property
    def dropped_samples(self) -> int:
        """因消費者太慢而丟掉的 16 kHz 樣本數。"""
        return self._buffer.dropped_samples

    def start(self) -> None:
        """取得螢幕內容並開始擷取；權限被拒時拋 `PermissionDeniedError`。"""
        if self._started:
            raise CaptureError("source already started")
        self._started = True
        try:
            api = self._api_factory()
            content = api.shareable_content(self._open_timeout)
            handler = _StreamHandler(
                self._buffer, api.audio_output_type, api.extract_audio
            )
            self._stream = api.start_stream(content, handler, self._open_timeout)
            self._api = api
        except CaptureError as exc:
            logger.error("cannot start ScreenCaptureKit capture: %s", exc)
            self._buffer.close()
            raise
        except Exception as exc:
            # PyObjC 層的非預期錯誤（例如綁定 API 與預期不符）也以 CaptureError 回報給 engine。
            logger.error("cannot start ScreenCaptureKit capture: %r", exc)
            self._buffer.close()
            raise CaptureError(
                f"ScreenCaptureKit start failed: {type(exc).__name__}: {exc}"
            ) from exc

    def read(self, timeout: float) -> AudioChunk:
        """等待最多 `timeout` 秒，回傳累積的 16 kHz 單聲道 int16 音訊；逾時回傳空陣列。"""
        timeout = validate_timeout(timeout)
        if not self._started:
            raise CaptureError("read() called before start()")
        return self._buffer.get(timeout)

    def stop(self) -> None:
        """停止擷取；可重複呼叫。"""
        api, stream = self._api, self._stream
        self._api = None
        self._stream = None
        try:
            if api is not None and stream is not None:
                api.stop_stream(stream, self._join_timeout)
        finally:
            self._buffer.close()
