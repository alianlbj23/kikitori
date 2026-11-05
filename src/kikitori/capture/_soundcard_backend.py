"""soundcard 後端：Linux PulseAudio／PipeWire（monitor + 麥克風）與 macOS CoreAudio（麥克風）。

位於 `devices.py`（列裝置、開裝置的分派）之下。裝置一律以 48 kHz、原生聲道數開啟，
由 `ThreadedSource` 的擷取執行緒轉成 16 kHz 單聲道 int16。Windows 不用 soundcard：
它的 WASAPI 實作要求混音格式必須是 WAVEFORMATEXTENSIBLE，遇到回報
WAVE_FORMAT_IEEE_FLOAT 的裝置會 assert 失敗（見 `docs/specs/capture.md`）。
soundcard 只在 `load_soundcard()` 時匯入，讓本模組在沒有音訊系統的環境也能匯入。
"""

import importlib
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import ModuleType
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.capture.errors import (
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
)
from kikitori.capture.resample import MAX_CHANNELS
from kikitori.capture.threaded import (
    JOIN_TIMEOUT,
    MAX_BUFFER_SECONDS,
    OPEN_TIMEOUT,
    ThreadedSource,
    positive_seconds,
)
from kikitori.capture.types import DeviceInfo, DeviceKind

logger = logging.getLogger(__name__)

BACKEND = "soundcard"
# 多數裝置原生就是 48 kHz，以此開啟時系統不必轉換，重採樣品質由我們的濾波器決定。
CAPTURE_RATE = 48000
# 20 ms：PulseAudio 的 fragsize 由它決定（不設會是秒級延遲）。
BLOCK_SECONDS = 0.02

_PULSE_STREAM_FAILED = "stream is in status failed"


def load_soundcard(
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> ModuleType:
    """匯入 soundcard；缺少系統音訊函式庫（例如 libpulse）時拋 `BackendUnavailableError`。"""
    try:
        return importer("soundcard")
    except (ImportError, OSError, NotImplementedError, RuntimeError) as exc:
        raise BackendUnavailableError(f"soundcard backend unavailable: {exc}") from exc


def make_device_id(kind: DeviceKind, native_id: str) -> str:
    """組成對外的不透明裝置 id。"""
    return f"{BACKEND}:{kind}:{native_id}"


def map_error(exc: BaseException) -> CaptureError:
    """把 soundcard／系統回報的例外轉成 capture 的例外類別，保留原因。"""
    if isinstance(exc, CaptureError):
        return exc
    message = f"{type(exc).__name__}: {exc}"
    mapped: CaptureError
    if isinstance(exc, IndexError):
        mapped = DeviceNotFoundError(f"audio device not found: {message}")
    elif _PULSE_STREAM_FAILED in str(exc).lower():
        mapped = DeviceLostError(f"audio device disconnected: {message}")
    else:
        mapped = CaptureError(f"audio capture failed: {message}")
    mapped.__cause__ = exc
    return mapped


def _kind_of(mic: Any) -> DeviceKind:
    return "loopback" if bool(getattr(mic, "isloopback", False)) else "microphone"


def _default_loopback_id(sc: ModuleType, platform: str) -> str | None:
    """預設輸出裝置對應的 monitor source 原生 id；沒有預設輸出裝置時回傳 None。"""
    if platform == "darwin":
        return None  # CoreAudio 沒有 loopback；系統聲音走 ScreenCaptureKit。
    try:
        speaker = sc.default_speaker()
    except (RuntimeError, IndexError) as exc:
        logger.info("no default output device: %s", exc)
        return None
    # PulseAudio 為每個 sink 建立名為 "<sink>.monitor" 的 monitor source。
    return f"{speaker.id}.monitor"


def _default_microphone_id(sc: ModuleType) -> str | None:
    try:
        return str(sc.default_microphone().id)
    except (RuntimeError, IndexError) as exc:
        logger.info("no default input device: %s", exc)
        return None


def _all_microphones(sc: ModuleType, platform: str) -> list[Any]:
    try:
        # macOS 的 soundcard 遇到 include_loopback=True 只會發警告，所以只在其他平台要求。
        return list(sc.all_microphones(include_loopback=platform != "darwin"))
    except (RuntimeError, IndexError, OSError) as exc:
        raise map_error(exc) from exc


def list_devices(sc: ModuleType, platform: str) -> list[DeviceInfo]:
    """列出 soundcard 看得到的 loopback（monitor）與麥克風裝置。"""
    mics = _all_microphones(sc, platform)
    defaults: dict[DeviceKind, str | None] = {
        "loopback": _default_loopback_id(sc, platform),
        "microphone": _default_microphone_id(sc),
    }
    devices = []
    for mic in mics:
        kind = _kind_of(mic)
        native_id = str(mic.id)
        devices.append(
            DeviceInfo(
                id=make_device_id(kind, native_id),
                name=str(mic.name),
                kind=kind,
                is_default=native_id == defaults[kind],
            )
        )
    return devices


def find_microphone(
    sc: ModuleType, platform: str, kind: DeviceKind, native_id: str | None
) -> Any:
    """依原生 id（None 表示預設裝置）找到 soundcard 的 microphone 物件。

    只做完全比對；soundcard 自己的 get_microphone 會做名稱模糊比對，可能開錯裝置。
    """
    if native_id is None:
        native_id = (
            _default_loopback_id(sc, platform)
            if kind == "loopback"
            else _default_microphone_id(sc)
        )
        if native_id is None:
            raise DeviceNotFoundError(f"no default {kind} device")
    for mic in _all_microphones(sc, platform):
        if str(mic.id) == native_id and _kind_of(mic) == kind:
            return mic
    raise DeviceNotFoundError(f"{kind} device not found: {native_id}")


def _channel_count(mic: Any) -> int:
    channels = mic.channels
    if isinstance(channels, list | tuple):
        channels = len(channels)
    if isinstance(channels, bool) or not isinstance(channels, int):
        raise CaptureError(f"device reported an invalid channel count: {channels!r}")
    if not 1 <= channels <= MAX_CHANNELS:
        raise CaptureError(f"device reported an unsupported channel count: {channels}")
    return channels


class _RecorderReader:
    def __init__(self, recorder: Any, sample_rate: int, channels: int) -> None:
        self._recorder = recorder
        self.sample_rate = sample_rate
        self.channels = channels

    def read_block(self) -> npt.NDArray[np.float32]:
        # numframes=None：拿系統目前已有的資料，不為湊滿固定長度而多等。
        return self._recorder.record(numframes=None)


class SoundcardSource(ThreadedSource):
    """以 soundcard 擷取單一裝置（`find_microphone()` 的結果）的 `AudioSource`。"""

    def __init__(
        self,
        microphone: Any,
        *,
        capture_rate: int = CAPTURE_RATE,
        block_seconds: float = BLOCK_SECONDS,
        max_buffer_seconds: float = MAX_BUFFER_SECONDS,
        open_timeout: float = OPEN_TIMEOUT,
        join_timeout: float = JOIN_TIMEOUT,
    ) -> None:
        if isinstance(capture_rate, bool) or not isinstance(capture_rate, int):
            raise TypeError(f"capture_rate must be an int, got {capture_rate!r}")
        if not 8000 <= capture_rate <= 384000:
            raise ValueError(
                f"capture_rate must be in [8000, 384000], got {capture_rate}"
            )
        self.microphone = microphone
        block_frames = max(
            1, round(positive_seconds("block_seconds", block_seconds) * capture_rate)
        )

        @contextmanager
        def opener() -> Iterator[_RecorderReader]:
            channels = _channel_count(microphone)
            recorder = microphone.recorder(
                samplerate=capture_rate, channels=channels, blocksize=block_frames
            )
            with recorder as rec:
                yield _RecorderReader(rec, capture_rate, channels)

        super().__init__(
            opener,
            map_error=map_error,
            max_buffer_seconds=max_buffer_seconds,
            open_timeout=open_timeout,
            join_timeout=join_timeout,
        )
