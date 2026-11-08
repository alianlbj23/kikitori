"""session 執行中的量測：輸入音量（RMS dBFS）與完整字幕延遲的中位數。

位於 pipeline 內部，給 engine 每秒送出的 `metrics` 事件使用（`Pipeline.metrics()`）。
音量以「最近約 1 秒的擷取音訊」計算（以樣本數為窗，不讀時鐘），capture 執行緒寫、
engine 的回報執行緒讀，所以用鎖保護。
"""

import math
import statistics
import threading
from collections import deque
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

FULL_SCALE = 32768.0
"""int16 的滿刻度：振幅 ±32768 的方波為 0 dBFS。"""
SILENCE_DBFS = -120.0
"""完全沒有聲音（或還沒有音訊）時回報的下限；log(0) 沒有定義。"""
LATENCY_WINDOW = 20
"""延遲中位數取最近幾句完整字幕。"""
LEVEL_BLOCK = 1600
"""音量窗的細分單位（16 kHz 時 100 ms）：擷取區塊可能很大，切小才能準確地只看最近約 1 秒。"""


@dataclass(frozen=True)
class PipelineMetrics:
    """`Pipeline.metrics()` 的快照。

    Attributes:
        level_dbfs: 最近約 1 秒擷取音訊的 RMS（dBFS），[−120.0, 0.0]，取到小數一位。
        backlog: 句子已結束、還在完整路徑佇列中等待的句數（不含正在處理的那一句）。
        latency_ms_median: 最近 20 句完整字幕 `latency_ms` 的中位數（四捨五入成整數）；
            還沒有完整字幕時為 None。被濾掉的「清除」結果不算。
    """

    level_dbfs: float
    backlog: int
    latency_ms_median: int | None


def rms_dbfs(sum_squares: float, count: int) -> float:
    """平方和與樣本數 → RMS dBFS，限制在 [−120.0, 0.0]、取到小數一位。"""
    if count <= 0 or sum_squares <= 0:
        return SILENCE_DBFS
    rms = math.sqrt(sum_squares / count)
    value = 20.0 * math.log10(rms / FULL_SCALE)
    return round(min(0.0, max(SILENCE_DBFS, value)), 1)


class LevelMeter:
    """最近 `window` 個樣本（以 `LEVEL_BLOCK` 為單位，誤差小於一個單位）的 RMS 音量。"""

    def __init__(self, window: int) -> None:
        """
        Args:
            window: 窗的樣本數（16 kHz 時 16000 ≈ 1 秒）。

        Raises:
            ValueError: `window` 不是正整數。
        """
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError(f"window must be a positive integer, got {window!r}")
        self._window = window
        self._lock = threading.Lock()
        self._blocks: deque[tuple[float, int]] = deque()
        self._sum = 0.0
        self._count = 0

    def add(self, audio: npt.NDArray[np.int16]) -> None:
        """加入一段擷取音訊（任何長度；空陣列不影響）。"""
        if len(audio) == 0:
            return
        tail = audio[-self._window :]
        # float64 平方和：int16 平方在 int32 會溢位；整數值的 float64 在這個範圍是精確的
        squares = np.square(tail, dtype=np.float64)
        blocks = [
            (
                float(squares[i : i + LEVEL_BLOCK].sum()),
                len(squares[i : i + LEVEL_BLOCK]),
            )
            for i in range(0, len(squares), LEVEL_BLOCK)
        ]
        with self._lock:
            for sum_squares, count in blocks:
                self._blocks.append((sum_squares, count))
                self._sum += sum_squares
                self._count += count
            # 丟掉最舊的單位，只要剩下的仍涵蓋整個窗
            while self._blocks and self._count - self._blocks[0][1] >= self._window:
                old_sum, old_count = self._blocks.popleft()
                self._sum -= old_sum
                self._count -= old_count
            if not self._blocks:
                self._sum, self._count = 0.0, 0
            # 浮點累減可能留下極小的負數或殘值；全是靜音時歸零
            self._sum = max(self._sum, 0.0)

    def dbfs(self) -> float:
        """目前窗內的 RMS dBFS。"""
        with self._lock:
            return rms_dbfs(self._sum, self._count)


class LatencyWindow:
    """最近 `size` 句完整字幕延遲的中位數（執行緒安全）。"""

    def __init__(self, size: int = LATENCY_WINDOW) -> None:
        self._lock = threading.Lock()
        self._values: deque[int] = deque(maxlen=size)

    def add(self, latency_ms: int) -> None:
        """記下一句的延遲（毫秒）。"""
        with self._lock:
            self._values.append(latency_ms)

    def median(self) -> int | None:
        """中位數（四捨五入）；沒有資料時 None。"""
        with self._lock:
            values = list(self._values)
        if not values:
            return None
        return round(statistics.median(values))
