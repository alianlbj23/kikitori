"""擷取執行緒（或系統回呼）與消費者之間的有界緩衝。

位於 capture 後端與 `AudioSource.read()` 之間。上限以 16 kHz 樣本數計；
滿了就丟最舊的資料並計數——即時字幕寧可跳過舊音訊，也不要讓延遲越積越多。
擷取端的錯誤透過 `fail()` 交給讀取端，在已收到的資料讀完之後拋出。
"""

import logging
import math
import threading
from collections import deque

import numpy as np

from kikitori.capture.errors import CaptureError, detached_copy
from kikitori.capture.types import AudioChunk

logger = logging.getLogger(__name__)


def empty_chunk() -> AudioChunk:
    """回傳空的 int16 音訊片段（`read()` 逾時時的回傳值）。"""
    return np.zeros(0, dtype=np.int16)


def validate_timeout(timeout: float) -> float:
    """確認 `timeout` 是有限的非負秒數。"""
    if isinstance(timeout, bool) or not isinstance(timeout, int | float):
        raise TypeError(f"timeout must be a number, got {timeout!r}")
    if not math.isfinite(timeout) or timeout < 0:
        raise ValueError(f"timeout must be a finite non-negative number, got {timeout}")
    return float(timeout)


class ChunkBuffer:
    """執行緒安全、以樣本數為上限、滿了丟最舊資料的音訊緩衝。"""

    def __init__(self, max_samples: int) -> None:
        if isinstance(max_samples, bool) or not isinstance(max_samples, int):
            raise TypeError(f"max_samples must be an int, got {max_samples!r}")
        if max_samples <= 0:
            raise ValueError(f"max_samples must be positive, got {max_samples}")
        self._max_samples = max_samples
        self._chunks: deque[AudioChunk] = deque()
        self._queued = 0
        self._dropped = 0
        self._error: BaseException | None = None
        self._closed = False
        self._cond = threading.Condition()

    @property
    def dropped_samples(self) -> int:
        """因緩衝滿而丟掉的 16 kHz 樣本總數。"""
        with self._cond:
            return self._dropped

    @property
    def queued_samples(self) -> int:
        """目前尚未被讀走的樣本數。"""
        with self._cond:
            return self._queued

    def put(self, chunk: AudioChunk) -> None:
        """放入一段 int16 一維音訊；關閉或失敗後放入的資料會被忽略。"""
        if chunk.dtype != np.int16 or chunk.ndim != 1:
            raise ValueError(
                f"expected 1-D int16 audio, got {chunk.dtype} {chunk.shape}"
            )
        if len(chunk) == 0:
            return
        with self._cond:
            if self._closed or self._error is not None:
                return
            self._chunks.append(chunk)
            self._queued += len(chunk)
            overflow = self._queued - self._max_samples
            if overflow > 0:
                self._drop_oldest(overflow)
            self._cond.notify_all()

    def _drop_oldest(self, count: int) -> None:
        if self._dropped == 0:
            logger.warning(
                "capture buffer full; dropping oldest audio (consumer too slow)"
            )
        self._dropped += count
        self._queued -= count
        while count > 0:
            head = self._chunks[0]
            if len(head) <= count:
                self._chunks.popleft()
                count -= len(head)
            else:
                self._chunks[0] = head[count:]
                count = 0

    def fail(self, error: BaseException) -> None:
        """記錄擷取端的錯誤；讀取端讀完剩餘資料後會拋出它（每次一份新的複本）。"""
        # 錯誤的 traceback 是擷取端（已結束）的 frame，裡面參照 source；buffer 被 source 持有，
        # 留著會形成循環。訊息與類別已足夠回報，呼叫端要 traceback 應在 fail() 之前記錄。
        error.with_traceback(None)
        with self._cond:
            if self._error is None:
                self._error = error
            self._cond.notify_all()

    def close(self) -> None:
        """正常結束：之後 `get()` 讀完剩餘資料就立刻回傳空陣列。"""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def get(self, timeout: float) -> AudioChunk:
        """等待最多 `timeout` 秒，回傳目前累積的全部音訊；逾時回傳空陣列。"""
        timeout = validate_timeout(timeout)
        with self._cond:
            self._cond.wait_for(
                lambda: self._chunks or self._error is not None or self._closed,
                timeout=timeout,
            )
            if self._chunks:
                out = np.concatenate(self._chunks)
                self._chunks.clear()
                self._queued = 0
                return out
            if self._error is not None:
                # 不重新丟出保存的同一個物件：丟出處的 frame 會接到它的 traceback 上，
                # 讀取端（pipeline）就被 buffer 經由例外參照住（見 `detached_copy()`）
                if isinstance(self._error, CaptureError):
                    raise detached_copy(self._error)
                raise CaptureError(f"capture failed: {self._error}") from self._error
            return empty_chunk()
