"""不需要音效卡的 `AudioSource`：`ArraySource`（記憶體中的陣列）與 `FileSource`（WAV 檔）。

給測試、評測與管線離線處理使用；可選擇依即時速度送出，模擬真實裝置的節奏。
"""

import math
import time
import wave
from collections.abc import Callable
from pathlib import Path

import numpy as np

from kikitori.capture.buffer import empty_chunk, validate_timeout
from kikitori.capture.errors import CaptureError
from kikitori.capture.resample import AudioConverter
from kikitori.capture.types import SAMPLE_RATE, AudioChunk

DEFAULT_CHUNK_SAMPLES = 320  # 20 ms，接近真實裝置每次交付的量

# 浮點時間換算成樣本數時加一點容差，避免 0.02 s × 16000 算成 319.999… 少一個樣本。
_SAMPLE_EPSILON = 1e-6


class ArraySource:
    """把已是 16 kHz 單聲道 int16 的陣列當成音訊來源。

    非即時模式下每次 `read()` 立刻回傳下一塊 `chunk_samples` 個樣本；即時模式下
    依牆鐘時間送出已「播放到」的樣本（至少湊滿一塊才回傳，逾時回傳空陣列）。
    資料用完後 `read()` 立刻回傳空陣列，`exhausted` 變成 True。
    """

    def __init__(
        self,
        audio: AudioChunk,
        *,
        chunk_samples: int = DEFAULT_CHUNK_SAMPLES,
        realtime: bool = False,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if (
            not isinstance(audio, np.ndarray)
            or audio.dtype != np.int16
            or audio.ndim != 1
        ):
            raise ValueError("audio must be a 1-D int16 numpy array at 16 kHz")
        if isinstance(chunk_samples, bool) or not isinstance(chunk_samples, int):
            raise TypeError(f"chunk_samples must be an int, got {chunk_samples!r}")
        if chunk_samples <= 0:
            raise ValueError(f"chunk_samples must be positive, got {chunk_samples}")
        self._audio = audio
        self._chunk = chunk_samples
        self._realtime = realtime
        self._clock = clock
        self._sleep = sleep
        self._pos = 0
        self._started_at: float | None = None
        self._stopped = False

    @property
    def exhausted(self) -> bool:
        """已啟動且全部樣本都已送出。"""
        return self._started_at is not None and self._pos >= len(self._audio)

    def start(self) -> None:
        """開始送出音訊；即時模式從此刻起計時。"""
        if self._started_at is not None:
            raise CaptureError("source already started")
        self._started_at = self._clock()

    def stop(self) -> None:
        """停止；之後 `read()` 一律回傳空陣列。"""
        self._stopped = True

    def read(self, timeout: float) -> AudioChunk:
        """回傳下一段音訊；即時模式最多等 `timeout` 秒。"""
        timeout = validate_timeout(timeout)
        if self._started_at is None:
            raise CaptureError("read() called before start()")
        if self._stopped or self._pos >= len(self._audio):
            return empty_chunk()
        if not self._realtime:
            return self._take(self._pos + self._chunk)
        return self._read_paced(self._started_at, timeout)

    def _read_paced(self, started_at: float, timeout: float) -> AudioChunk:
        total = len(self._audio)
        deadline = self._clock() + timeout
        while True:
            now = self._clock()
            due = min(
                total, math.floor((now - started_at) * SAMPLE_RATE + _SAMPLE_EPSILON)
            )
            if due - self._pos >= self._chunk or (due == total and due > self._pos):
                return self._take(due)
            next_due = started_at + min(self._pos + self._chunk, total) / SAMPLE_RATE
            wait = min(next_due, deadline) - now
            if wait <= 0:
                return empty_chunk()
            self._sleep(wait)

    def _take(self, end: int) -> AudioChunk:
        end = min(end, len(self._audio))
        out = self._audio[self._pos : end].copy()
        self._pos = end
        return out


def _decode_pcm(raw: bytes, width: int) -> np.ndarray:
    """把 WAV 的整數 PCM 位元組解成 [-1, 1) 的 float。"""
    if width == 1:  # WAV 的 8-bit 是無號數
        return (np.frombuffer(raw, dtype=np.uint8).astype(np.float64) - 128.0) / 128.0
    if width == 2:
        return np.frombuffer(raw, dtype="<i2").astype(np.float64) / 32768.0
    if width == 3:
        b = np.frombuffer(raw, dtype=np.uint8).reshape(-1, 3).astype(np.int32)
        v = b[:, 0] | (b[:, 1] << 8) | (b[:, 2] << 16)
        v = np.where(v >= 1 << 23, v - (1 << 24), v)
        return v.astype(np.float64) / float(1 << 23)
    if width == 4:
        return np.frombuffer(raw, dtype="<i4").astype(np.float64) / float(1 << 31)
    raise CaptureError(f"unsupported WAV sample width: {width} bytes")


def read_wav(path: Path) -> AudioChunk:
    """讀取整數 PCM WAV（8/16/24/32 位元、任意取樣率與聲道數），轉成 16 kHz 單聲道 int16。"""
    try:
        with wave.open(str(path), "rb") as w:
            channels = w.getnchannels()
            width = w.getsampwidth()
            rate = w.getframerate()
            if w.getcomptype() != "NONE":
                raise CaptureError(
                    f"compressed WAV is not supported: {w.getcomptype()}"
                )
            raw = w.readframes(w.getnframes())
    except (OSError, EOFError, wave.Error) as exc:
        raise CaptureError(f"cannot read WAV file {path}: {exc}") from exc
    samples = _decode_pcm(raw, width)
    if len(samples) % channels:
        raise CaptureError(f"truncated WAV data in {path}")
    try:
        converter = AudioConverter(rate, channels)
    except (TypeError, ValueError) as exc:
        raise CaptureError(f"unsupported WAV format in {path}: {exc}") from exc
    return converter.process(samples.reshape(-1, channels))


class FileSource:
    """從 WAV 檔讀取音訊的 `AudioSource`；`start()` 時整個檔案解碼並轉成管線格式。

    整檔放進記憶體（16 kHz int16 一小時約 115 MB），只適合測試與離線評測。
    """

    def __init__(
        self,
        path: str | Path,
        *,
        realtime: bool = False,
        chunk_seconds: float = DEFAULT_CHUNK_SAMPLES / SAMPLE_RATE,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not (math.isfinite(chunk_seconds) and 0 < chunk_seconds <= 10):
            raise ValueError(f"chunk_seconds must be in (0, 10], got {chunk_seconds}")
        self.path = Path(path)
        self._realtime = realtime
        self._chunk_samples = max(1, round(chunk_seconds * SAMPLE_RATE))
        self._clock = clock
        self._sleep = sleep
        self._inner: ArraySource | None = None

    @property
    def exhausted(self) -> bool:
        """已啟動且整個檔案都已送出。"""
        return self._inner is not None and self._inner.exhausted

    def start(self) -> None:
        """讀取並轉換 WAV 檔；檔案不存在或格式不支援時拋出 `CaptureError`。"""
        if self._inner is not None:
            raise CaptureError("source already started")
        inner = ArraySource(
            read_wav(self.path),
            chunk_samples=self._chunk_samples,
            realtime=self._realtime,
            clock=self._clock,
            sleep=self._sleep,
        )
        inner.start()
        self._inner = inner

    def read(self, timeout: float) -> AudioChunk:
        """回傳下一段音訊；即時模式最多等 `timeout` 秒。"""
        if self._inner is None:
            raise CaptureError("read() called before start()")
        return self._inner.read(timeout)

    def stop(self) -> None:
        """停止；之後 `read()` 一律回傳空陣列。"""
        if self._inner is not None:
            self._inner.stop()
