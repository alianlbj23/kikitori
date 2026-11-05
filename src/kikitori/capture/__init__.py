"""聲音擷取模組：系統播放聲音（loopback）與麥克風 → 16 kHz 單聲道 int16。

位於管線最前端，輸出交給 VAD 切句。對外只需要這裡匯出的名稱：
`list_devices()` 列裝置、`open_source()` 建立 `AudioSource`，測試與離線處理用
`ArraySource`／`FileSource`。平台相關的函式庫（PyAudioWPatch、soundcard、PyObjC）
一律延遲到真的列裝置或開裝置時才匯入，所以本套件在任何作業系統都能匯入。
規格見 `docs/specs/capture.md`。
"""

from kikitori.capture.devices import list_devices, open_source
from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
    PermissionDeniedError,
)
from kikitori.capture.resample import AudioConverter, StreamingResampler
from kikitori.capture.sources import ArraySource, FileSource, read_wav
from kikitori.capture.types import (
    DEVICE_KINDS,
    SAMPLE_RATE,
    AudioChunk,
    AudioSource,
    DeviceInfo,
    DeviceKind,
)

__all__ = [
    "DEVICE_KINDS",
    "SAMPLE_RATE",
    "ArraySource",
    "AudioChunk",
    "AudioConverter",
    "AudioSource",
    "BackendUnavailableError",
    "CaptureError",
    "DeviceInfo",
    "DeviceKind",
    "DeviceLostError",
    "DeviceNotFoundError",
    "FileSource",
    "PermissionDeniedError",
    "StreamingResampler",
    "list_devices",
    "open_source",
    "read_wav",
]
