"""Windows 後端：以 PyAudioWPatch（PortAudio 的 WASAPI loopback 分支）擷取系統聲音與麥克風。

位於 `devices.py`（列裝置、開裝置的分派）之下，由 `ThreadedSource` 的擷取執行緒呼叫。
只列 WASAPI host API 的裝置（避免 MME／DirectSound 重複出現）；輸出裝置的 loopback
由 PyAudioWPatch 列成獨立的輸入裝置（名稱帶 "[Loopback]"）。
裝置以原生取樣率、原生聲道數、float32 開啟，不靠系統轉換格式。
"""

import importlib
import logging
import threading
import time
from collections import Counter
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
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
)
from kikitori.capture.types import DeviceInfo, DeviceKind

logger = logging.getLogger(__name__)

BACKEND = "wasapi"
BLOCK_SECONDS = 0.02
# 沒資料時的輪詢間隔：遠小於 VAD 的 32 ms 視窗，CPU 負擔可忽略。
POLL_INTERVAL = 0.005
# WASAPI loopback 在沒有聲音播放時完全不送資料；VAD 需要靜音樣本才能判斷句尾，
# 所以超過這段時間沒資料，就依經過的時間補零（soundcard 也是這樣處理）。
SILENCE_FILL_AFTER = 0.05

# PortAudio 的初始化、列舉與開關串流不是執行緒安全的；engine 可能在擷取中列裝置。
_PA_LOCK = threading.Lock()


def load_pyaudio(
    importer: Callable[[str], ModuleType] = importlib.import_module,
) -> ModuleType:
    """匯入 PyAudioWPatch；不在 Windows 或安裝不完整時拋 `BackendUnavailableError`。"""
    try:
        return importer("pyaudiowpatch")
    except (ImportError, OSError) as exc:
        raise BackendUnavailableError(f"WASAPI backend unavailable: {exc}") from exc


def make_device_id(kind: DeviceKind, native_id: str) -> str:
    """組成對外的不透明裝置 id。"""
    return f"{BACKEND}:{kind}:{native_id}"


@dataclass(frozen=True)
class _Entry:
    kind: DeviceKind
    native_id: str
    name: str
    info: dict[str, Any]


def _enumerate(pa_mod: ModuleType, pa: Any) -> list[_Entry]:
    """列出 WASAPI 的 loopback 與輸入裝置。

    PortAudio 的裝置索引在裝置增減後會變動，所以對外 id 用裝置名稱；
    同名裝置依出現順序加上 "#2"、"#3" 區分。
    """
    try:
        infos = list(
            pa.get_device_info_generator_by_host_api(host_api_type=pa_mod.paWASAPI)
        )
    except OSError as exc:
        raise BackendUnavailableError(f"WASAPI host API unavailable: {exc}") from exc
    seen: Counter[tuple[DeviceKind, str]] = Counter()
    entries = []
    for info in infos:
        kind: DeviceKind
        if info.get("isLoopbackDevice"):
            kind = "loopback"
        elif int(info.get("maxInputChannels", 0)) > 0:
            kind = "microphone"
        else:
            continue  # 一般輸出裝置：它的 loopback 會另外列出
        name = str(info["name"])
        seen[(kind, name)] += 1
        n = seen[(kind, name)]
        entries.append(_Entry(kind, name if n == 1 else f"{name}#{n}", name, info))
    return entries


def _default_index(pa: Any, kind: DeviceKind) -> int | None:
    try:
        if kind == "loopback":
            info = pa.get_default_wasapi_loopback()
        else:
            info = pa.get_default_wasapi_device(d_in=True)
        return int(info["index"])
    except (OSError, LookupError, ValueError) as exc:
        logger.info("no default %s device: %s", kind, exc)
        return None


def list_devices(pa_mod: ModuleType) -> list[DeviceInfo]:
    """列出 WASAPI loopback 與麥克風裝置。"""
    with _PA_LOCK:
        pa = pa_mod.PyAudio()
        try:
            entries = _enumerate(pa_mod, pa)
            defaults = {
                kind: _default_index(pa, kind) for kind in ("loopback", "microphone")
            }
        finally:
            pa.terminate()
    return [
        DeviceInfo(
            id=make_device_id(e.kind, e.native_id),
            name=e.name,
            kind=e.kind,
            is_default=int(e.info["index"]) == defaults[e.kind],
        )
        for e in entries
    ]


def _resolve(
    pa_mod: ModuleType, pa: Any, kind: DeviceKind, native_id: str | None
) -> _Entry:
    entries = _enumerate(pa_mod, pa)
    if native_id is None:
        index = _default_index(pa, kind)
        match = [e for e in entries if e.kind == kind and int(e.info["index"]) == index]
        if not match:
            raise DeviceNotFoundError(f"no default {kind} device")
        return match[0]
    for e in entries:
        if e.kind == kind and e.native_id == native_id:
            return e
    raise DeviceNotFoundError(f"{kind} device not found: {native_id}")


def _stream_format(info: dict[str, Any]) -> tuple[int, int]:
    channels = int(info["maxInputChannels"])
    rate = round(float(info["defaultSampleRate"]))
    if not 1 <= channels <= MAX_CHANNELS:
        raise CaptureError(f"device reported an unsupported channel count: {channels}")
    if not 8000 <= rate <= 384000:
        raise CaptureError(f"device reported an unsupported sample rate: {rate}")
    return rate, channels


class _StreamReader:
    """以非阻塞輪詢讀取 PortAudio 串流；loopback 沒資料時依時間補零。"""

    def __init__(
        self,
        stream: Any,
        sample_rate: int,
        channels: int,
        *,
        fill_silence: bool,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._stream = stream
        self.sample_rate = sample_rate
        self.channels = channels
        self._fill_silence = fill_silence
        self._clock = clock
        self._sleep = sleep
        self._idle_since: float | None = None

    def read_block(self) -> npt.NDArray[np.float32]:
        try:
            available = int(self._stream.get_read_available())
            if available > 0:
                raw = self._stream.read(available, exception_on_overflow=False)
            else:
                raw = None
        except OSError as exc:
            raise DeviceLostError(
                f"audio device stopped delivering data: {exc}"
            ) from exc
        if raw is not None:
            self._idle_since = None
            return np.frombuffer(raw, dtype=np.float32).reshape(-1, self.channels)

        now = self._clock()
        if self._idle_since is None:
            self._idle_since = now
        idle = now - self._idle_since
        if self._fill_silence and idle >= SILENCE_FILL_AFTER:
            frames = int(idle * self.sample_rate)
            # 只推進補上的整數樣本對應的時間，餘數留到下次，長時間下來不會漂移。
            self._idle_since += frames / self.sample_rate
            return np.zeros((frames, self.channels), dtype=np.float32)
        self._sleep(POLL_INTERVAL)
        return np.zeros((0, self.channels), dtype=np.float32)


def _close_stream(stream: Any) -> None:
    try:
        stream.stop_stream()
        stream.close()
    except OSError as exc:
        # 串流已失效（例如裝置被拔除）時關閉也會失敗；記錄即可，原本的錯誤才是要回報的。
        logger.warning("closing WASAPI stream failed: %s", exc)


def _open_stream(
    pa_mod: ModuleType, pa: Any, kind: DeviceKind, native_id: str | None
) -> tuple[Any, int, int]:
    """解析裝置並以原生格式開啟輸入串流，回傳 (stream, 取樣率, 聲道數)。"""
    entry = _resolve(pa_mod, pa, kind, native_id)
    rate, channels = _stream_format(entry.info)
    index = int(entry.info["index"])
    # PortAudio 遇到超出範圍的索引會直接當掉，開啟前再確認一次。
    if not 0 <= index < int(pa.get_device_count()):
        raise DeviceNotFoundError(f"device index out of range: {index}")
    try:
        stream = pa.open(
            format=pa_mod.paFloat32,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=index,
            frames_per_buffer=max(1, round(rate * BLOCK_SECONDS)),
        )
    except OSError as exc:
        hint = (
            "; check Windows microphone privacy settings"
            if kind == "microphone"
            else ""
        )
        raise CaptureError(f"cannot open {entry.name!r}: {exc}{hint}") from exc
    return stream, rate, channels


def map_error(exc: BaseException) -> CaptureError:
    """把 PortAudio 的 OSError（args 為 (錯誤碼, 訊息)）轉成 capture 的例外類別。"""
    if isinstance(exc, CaptureError):
        return exc
    mapped = CaptureError(f"WASAPI capture failed: {type(exc).__name__}: {exc}")
    mapped.__cause__ = exc
    return mapped


class WasapiSource(ThreadedSource):
    """以 PyAudioWPatch 擷取 WASAPI 裝置的 `AudioSource`。

    裝置在擷取執行緒內解析（`native_id` 為 None 表示預設裝置）並開啟，
    所以找不到裝置的錯誤會從 `start()` 拋出。
    """

    def __init__(
        self,
        kind: DeviceKind,
        native_id: str | None,
        *,
        loader: Callable[[], ModuleType] = load_pyaudio,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        max_buffer_seconds: float = MAX_BUFFER_SECONDS,
        open_timeout: float = OPEN_TIMEOUT,
        join_timeout: float = JOIN_TIMEOUT,
    ) -> None:
        self.kind = kind
        self.native_id = native_id

        @contextmanager
        def opener() -> Iterator[_StreamReader]:
            pa_mod = loader()
            with _PA_LOCK:
                pa = pa_mod.PyAudio()
                try:
                    stream, rate, channels = _open_stream(pa_mod, pa, kind, native_id)
                except BaseException:
                    pa.terminate()
                    raise
            try:
                yield _StreamReader(
                    stream,
                    rate,
                    channels,
                    fill_silence=kind == "loopback",
                    clock=clock,
                    sleep=sleep,
                )
            finally:
                with _PA_LOCK:
                    try:
                        _close_stream(stream)
                    finally:
                        pa.terminate()

        super().__init__(
            opener,
            map_error=map_error,
            max_buffer_seconds=max_buffer_seconds,
            open_timeout=open_timeout,
            join_timeout=join_timeout,
        )
