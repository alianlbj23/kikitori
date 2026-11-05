"""拉取式後端共用的擷取執行緒：開啟裝置、反覆讀取、轉換格式、放進有界緩衝。

位於各平台後端（WASAPI、soundcard）與 `AudioSource` 介面之間。後端只需提供
一個在擷取執行緒內呼叫的 `opener`，回傳 context manager 形式的 `BlockReader`；
裝置的開啟、讀取、關閉因此都在同一條執行緒內進行（COM／PortAudio／soundcard
都要求如此或在此情況下最安全）。
"""

import logging
import math
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Protocol

import numpy as np
import numpy.typing as npt

from kikitori.capture.buffer import ChunkBuffer, validate_timeout
from kikitori.capture.errors import CaptureError
from kikitori.capture.resample import AudioConverter
from kikitori.capture.types import SAMPLE_RATE, AudioChunk

logger = logging.getLogger(__name__)

MAX_BUFFER_SECONDS = 5.0
OPEN_TIMEOUT = 5.0
JOIN_TIMEOUT = 2.0


class BlockReader(Protocol):
    """擷取執行緒內使用的裝置讀取器。"""

    @property
    def sample_rate(self) -> int:
        """裝置交付的取樣率（Hz）。"""
        ...

    @property
    def channels(self) -> int:
        """裝置交付的聲道數。"""
        ...

    def read_block(self) -> npt.NDArray[np.float32]:
        """回傳 (樣本數, 聲道數) 的 float 音訊；必須在有限時間內返回（可回傳空區塊）。"""
        ...


Opener = Callable[[], AbstractContextManager[BlockReader]]
ErrorMapper = Callable[[BaseException], CaptureError]


def positive_seconds(name: str, value: float) -> float:
    """確認 `value` 是有限的正秒數。"""
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise TypeError(f"{name} must be a number, got {value!r}")
    if not (math.isfinite(value) and value > 0):
        raise ValueError(
            f"{name} must be a finite positive number of seconds, got {value!r}"
        )
    return float(value)


def _default_mapper(exc: BaseException) -> CaptureError:
    if isinstance(exc, CaptureError):
        return exc
    mapped = CaptureError(f"audio capture failed: {type(exc).__name__}: {exc}")
    mapped.__cause__ = exc
    return mapped


class ThreadedSource:
    """以一條 daemon 擷取執行緒實作 `AudioSource`。

    `start()` 最多等 `open_timeout` 秒讓裝置開啟；開啟失敗或逾時都會拋錯。
    執行中的錯誤會在已收到的資料讀完後，由 `read()` 拋出。
    """

    def __init__(
        self,
        opener: Opener,
        *,
        map_error: ErrorMapper = _default_mapper,
        max_buffer_seconds: float = MAX_BUFFER_SECONDS,
        open_timeout: float = OPEN_TIMEOUT,
        join_timeout: float = JOIN_TIMEOUT,
    ) -> None:
        self._opener = opener
        self._map_error = map_error
        max_samples = round(
            positive_seconds("max_buffer_seconds", max_buffer_seconds) * SAMPLE_RATE
        )
        self._open_timeout = positive_seconds("open_timeout", open_timeout)
        self._join_timeout = positive_seconds("join_timeout", join_timeout)
        self._buffer = ChunkBuffer(max(1, max_samples))
        self._stop_event = threading.Event()
        self._opened = threading.Event()
        self._open_error: CaptureError | None = None
        self._thread: threading.Thread | None = None
        self.device_sample_rate: int | None = None
        self.device_channels: int | None = None

    @property
    def dropped_samples(self) -> int:
        """因消費者太慢而丟掉的 16 kHz 樣本數。"""
        return self._buffer.dropped_samples

    def _thread_alive(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self) -> None:
        """開啟裝置並開始擷取。"""
        if self._thread is not None:
            raise CaptureError("source already started")
        self._thread = threading.Thread(
            target=self._run, name="kikitori-capture", daemon=True
        )
        self._thread.start()
        if not self._opened.wait(self._open_timeout):
            self._stop_event.set()
            raise CaptureError(
                f"timed out after {self._open_timeout:.1f}s opening audio device"
            )
        if self._open_error is not None:
            self._thread.join(self._join_timeout)
            raise self._open_error

    def read(self, timeout: float) -> AudioChunk:
        """等待最多 `timeout` 秒，回傳累積的 16 kHz 單聲道 int16 音訊；逾時回傳空陣列。"""
        timeout = validate_timeout(timeout)
        if self._thread is None:
            raise CaptureError("read() called before start()")
        return self._buffer.get(timeout)

    def stop(self) -> None:
        """停止擷取並等待擷取執行緒結束；可重複呼叫。"""
        self._stop_event.set()
        thread = self._thread
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(self._join_timeout)
            if thread.is_alive():
                # 底層呼叫卡住（例如音訊伺服器沒回應）；執行緒是 daemon，不會阻擋程式結束。
                logger.warning(
                    "capture thread did not stop within %.1fs", self._join_timeout
                )
        self._buffer.close()

    def _run(self) -> None:
        opened = False
        try:
            with self._opener() as reader:
                converter = AudioConverter(reader.sample_rate, reader.channels)
                self.device_sample_rate = reader.sample_rate
                self.device_channels = reader.channels
                opened = True
                self._opened.set()
                while not self._stop_event.is_set():
                    self._buffer.put(converter.process(reader.read_block()))
        except Exception as exc:  # noqa: BLE001
            # 執行緒邊界：任何錯誤都不能讓執行緒默默結束，記錄後交給讀取端拋出。
            error = self._map_error(exc)
            logger.error("audio capture failed (%s): %s", type(error).__name__, error)
            if not opened:
                self._open_error = error
            self._buffer.fail(error)
        finally:
            self._opened.set()
            self._buffer.close()
