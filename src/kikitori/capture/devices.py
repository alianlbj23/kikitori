"""裝置清單與開啟：依作業系統把 `list_devices()`／`open_source()` 分派到對應後端。

| 平台 | loopback | microphone |
|---|---|---|
| Windows | WASAPI（PyAudioWPatch） | WASAPI（PyAudioWPatch） |
| Linux | PulseAudio／PipeWire monitor（soundcard） | PulseAudio（soundcard） |
| macOS | ScreenCaptureKit（PyObjC） | CoreAudio（soundcard） |

裝置 id 格式為 `<backend>:<kind>:<原生 id>`，對呼叫端是不透明字串。
"""

import logging
import sys

from kikitori.capture import _screencapturekit_backend as sck
from kikitori.capture import _soundcard_backend as soundcard
from kikitori.capture import _wasapi_backend as wasapi
from kikitori.capture.errors import BackendUnavailableError, DeviceNotFoundError
from kikitori.capture.types import DEVICE_KINDS, AudioSource, DeviceInfo, DeviceKind

logger = logging.getLogger(__name__)

_SUPPORTED_PLATFORMS = ("win32", "linux", "darwin")


def _platform(platform: str | None) -> str:
    current = sys.platform if platform is None else platform
    if current not in _SUPPORTED_PLATFORMS:
        raise BackendUnavailableError(f"audio capture is not supported on {current!r}")
    return current


def list_devices(*, platform: str | None = None) -> list[DeviceInfo]:
    """列出此平台可擷取的 loopback 與麥克風裝置。

    沒有可用的音訊系統（例如 Linux 沒有 PulseAudio）時拋 `BackendUnavailableError`。
    """
    current = _platform(platform)
    if current == "win32":
        return wasapi.list_devices(wasapi.load_pyaudio())
    devices: list[DeviceInfo] = []
    if current == "darwin" and sck.is_supported(current):
        devices.append(sck.system_audio_device())
    devices.extend(soundcard.list_devices(soundcard.load_soundcard(), current))
    return devices


def _parse_device_id(device_id: str) -> tuple[str, DeviceKind, str]:
    parts = device_id.split(":", 2)
    if len(parts) != 3 or not parts[2] or parts[1] not in DEVICE_KINDS:
        raise DeviceNotFoundError(f"malformed device id: {device_id!r}")
    backend, kind, native_id = parts
    return backend, kind, native_id  # type: ignore[return-value]


def _validate(device_id: str | None, kind: str) -> None:
    if kind not in DEVICE_KINDS:
        raise ValueError(f"kind must be one of {DEVICE_KINDS}, got {kind!r}")
    if device_id is not None and (not isinstance(device_id, str) or not device_id):
        raise ValueError(
            f"device_id must be None or a non-empty string, got {device_id!r}"
        )


def _open_default(current: str, kind: DeviceKind) -> AudioSource:
    if current == "win32":
        return wasapi.WasapiSource(kind, None)
    if current == "darwin" and kind == "loopback":
        if not sck.is_supported(current):
            raise BackendUnavailableError(
                "system audio capture needs macOS 13 or later; route the output to a "
                "virtual device such as BlackHole and select it as the source instead"
            )
        return sck.ScreenCaptureKitSource()
    sc = soundcard.load_soundcard()
    return soundcard.SoundcardSource(soundcard.find_microphone(sc, current, kind, None))


def open_source(
    device_id: str | None, kind: DeviceKind, *, platform: str | None = None
) -> AudioSource:
    """建立擷取來源（尚未開啟裝置，`start()` 才開啟）。

    `device_id` 為 None 時使用該 `kind` 的預設裝置；有指定時開啟該裝置，`kind` 不影響選擇
    （例如 macOS 使用者可以在 media 模式選 BlackHole 這種輸入裝置當系統聲音）。
    """
    _validate(device_id, kind)
    current = _platform(platform)
    if device_id is None:
        return _open_default(current, kind)

    backend, id_kind, native_id = _parse_device_id(device_id)
    if id_kind != kind:
        logger.info("opening %s device %r for a %s source", id_kind, device_id, kind)
    if backend == wasapi.BACKEND and current == "win32":
        return wasapi.WasapiSource(id_kind, native_id)
    if backend == soundcard.BACKEND and current in ("linux", "darwin"):
        sc = soundcard.load_soundcard()
        return soundcard.SoundcardSource(
            soundcard.find_microphone(sc, current, id_kind, native_id)
        )
    if device_id == sck.DEVICE_ID and current == "darwin":
        return _open_default(current, "loopback")
    raise DeviceNotFoundError(f"device {device_id!r} is not available on {current}")
