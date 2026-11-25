"""session 執行中的量測：輸入音量（RMS dBFS）、完整字幕延遲的中位數與完整辨識的負荷。

位於 pipeline 內部，給 engine 每秒送出的 `metrics` 事件使用（`Pipeline.metrics()`）。
音量以「最近約 1 秒的擷取音訊」計算（以樣本數為窗，不讀時鐘），capture 執行緒寫、
engine 的回報執行緒讀，所以用鎖保護。負荷（`BusyMeter`／`RtfMeter`，
docs/specs/compute-capacity.md）由完整 worker 寫、回報執行緒讀，同樣用鎖保護；
忙碌度需要時間，所以時鐘由 pipeline 注入（與 `latency_ms` 同一個）。
"""

import math
import statistics
import threading
import time
from collections import deque
from collections.abc import Callable
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
BUSY_WINDOW_SECS = 30.0
BUSY_MIN_SPAN_SECS = 10.0
"""忙碌度分母的下限：session 剛開始、第一句還在算時，讀數不會因為分母太小而衝到 1.0。"""
"""完整辨識忙碌度的時間窗（秒）：夠長才不會因為一句長句就跳到 100%，夠短才看得出最近變吃緊。"""
RTF_WINDOW = 10
"""即時倍率取最近幾句完整辨識的中位數。"""


@dataclass(frozen=True)
class PipelineMetrics:
    """`Pipeline.metrics()` 的快照。

    Attributes:
        level_dbfs: 最近約 1 秒擷取音訊的 RMS（dBFS），[−120.0, 0.0]，取到小數一位。
        backlog: 句子已結束、還在完整路徑佇列中等待的句數（不含正在處理的那一句）。
        latency_ms_median: 最近 20 句完整字幕 `latency_ms` 的中位數（四捨五入成整數）；
            還沒有完整字幕時為 None。被濾掉的「清除」結果不算。
        speech_level_dbfs: 最近約 10 秒內「句子進行中」的音訊 RMS（v0.3 G8）；這段時間
            沒有句子時為 None。
        background_level_dbfs: 同一個窗內「句子之外」的音訊 RMS（BGM、遊戲音效、靜音）；
            一直在說話時為 None。
        final_busy: 最近 30 秒內完整辨識實際在算的時間比例 [0.0, 1.0]（`BusyMeter`）；
            還沒有完整辨識時為 None。
        rtf: 最近 10 句完整辨識「計算秒數 ÷ 音訊秒數」的中位數（`RtfMeter`）；小於 1 才
            跟得上。還沒有完成的完整辨識時為 None。
    """

    level_dbfs: float
    backlog: int
    latency_ms_median: int | None
    speech_level_dbfs: float | None = None
    background_level_dbfs: float | None = None
    final_busy: float | None = None
    rtf: float | None = None


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


BACKGROUND_WINDOW = 10 * 16_000
"""語音／背景音量的窗（16 kHz 時 10 秒）：BGM 太大聲要持續一陣子才值得提醒。"""


class SplitLevelMeter:
    """最近 `window` 個樣本內，語音與非語音兩部分各自的 RMS（v0.3 G8）。

    兩部分共用同一個以「擷取到的樣本」計的窗，所以一直在說話時背景的讀數會被擠出窗外
    （回報 None），不會留下過時的值。分類由呼叫端決定（pipeline：切句器是否在句子中）。
    capture 執行緒寫、engine 的回報執行緒讀，所以用鎖保護。
    """

    def __init__(self, window: int) -> None:
        """
        Args:
            window: 窗的樣本數。

        Raises:
            ValueError: `window` 不是正整數。
        """
        if isinstance(window, bool) or not isinstance(window, int) or window < 1:
            raise ValueError(f"window must be a positive integer, got {window!r}")
        self._window = window
        self._lock = threading.Lock()
        # (是否語音, 平方和, 樣本數)，以 LEVEL_BLOCK 為單位
        self._blocks: deque[tuple[bool, float, int]] = deque()
        self._count = 0

    def add(self, audio: npt.NDArray[np.int16], *, speech: bool) -> None:
        """加入一段擷取音訊並標示它是不是句子進行中的音訊（空陣列不影響）。"""
        if len(audio) == 0:
            return
        squares = np.square(audio[-self._window :], dtype=np.float64)
        blocks = [
            (
                speech,
                float(squares[i : i + LEVEL_BLOCK].sum()),
                len(squares[i : i + LEVEL_BLOCK]),
            )
            for i in range(0, len(squares), LEVEL_BLOCK)
        ]
        with self._lock:
            for block in blocks:
                self._blocks.append(block)
                self._count += block[2]
            while self._blocks and self._count - self._blocks[0][2] >= self._window:
                self._count -= self._blocks.popleft()[2]

    def levels(self) -> tuple[float | None, float | None]:
        """(語音 dBFS, 背景 dBFS)；窗內沒有那一類的樣本時為 None。"""
        with self._lock:
            blocks = list(self._blocks)
        result: list[float | None] = []
        for kind in (True, False):
            mine = [(s, n) for k, s, n in blocks if k is kind]
            count = sum(n for _, n in mine)
            result.append(rms_dbfs(sum(s for s, _ in mine), count) if count else None)
        return result[0], result[1]


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


class BusyMeter:
    """最近 `window_secs` 秒內「完整辨識正在算」的時間比例（執行緒安全）。

    負荷以這個為主（docs/specs/compute-capacity.md 的決策）：待處理句數要等到已經跟不上才
    變大，即時倍率在說話稀疏時看不出壓力，忙碌度能提早顯示「接近上限」。進行中的那次也
    算進去：一句算很久時不能等它算完才顯示忙碌。還不滿一個窗時以「第一次開始到現在」為
    分母，否則 session 剛開始的 30 秒內都會低估。
    """

    def __init__(
        self,
        window_secs: float = BUSY_WINDOW_SECS,
        *,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            window_secs: 時間窗（秒）。
            clock: 單調時鐘（秒）；pipeline 注入它自己的時鐘，測試注入假時鐘。

        Raises:
            ValueError: `window_secs` 不是有限的正數。
        """
        if (
            isinstance(window_secs, bool)
            or not isinstance(window_secs, int | float)
            or not math.isfinite(window_secs)
            or window_secs <= 0
        ):
            raise ValueError(
                f"window_secs must be a positive number, got {window_secs!r}"
            )
        self._window = float(window_secs)
        self._clock = clock
        self._lock = threading.Lock()
        self._intervals: deque[tuple[float, float]] = deque()
        self._active: float | None = None
        self._first: float | None = None

    def begin(self) -> None:
        """一次完整辨識開始。

        Raises:
            RuntimeError: 上一次還沒有 `end()`（完整路徑只有一條 worker，不會重疊）。
        """
        with self._lock:
            if self._active is not None:
                raise RuntimeError("BusyMeter.begin() called twice without end()")
            now = self._clock()
            self._active = now
            if self._first is None:
                self._first = now

    def end(self) -> float:
        """這次完整辨識結束；回傳耗時（秒，≥ 0）。

        Raises:
            RuntimeError: 沒有對應的 `begin()`。
        """
        with self._lock:
            if self._active is None:
                raise RuntimeError("BusyMeter.end() called without begin()")
            start, self._active = self._active, None
            # 時鐘倒退（不應發生）時當作 0 秒，不讓比例變成負的
            end = max(start, self._clock())
            self._intervals.append((start, end))
            self._prune(end)
            return end - start

    def ratio(self) -> float | None:
        """目前窗內的忙碌比例 [0.0, 1.0]，取到小數三位；還沒有開始過時 None。"""
        with self._lock:
            if self._first is None:
                return None
            now = self._clock()
            self._prune(now)
            elapsed = now - self._first
            if elapsed <= 0:
                return 0.0
            # 分母至少 BUSY_MIN_SPAN_SECS（但不超過窗）：剛開始的幾秒不誤報「跟不上」
            span = min(
                self._window, max(elapsed, min(BUSY_MIN_SPAN_SECS, self._window))
            )
            low = now - span
            busy = sum(
                max(0.0, min(end, now) - max(start, low))
                for start, end in self._intervals
            )
            if self._active is not None:
                busy += max(0.0, now - max(self._active, low))
            return round(min(1.0, busy / span), 3)

    def _prune(self, now: float) -> None:
        # 在 self._lock 之內呼叫：整段都在窗外的紀錄不再需要
        while self._intervals and self._intervals[0][1] < now - self._window:
            self._intervals.popleft()


class RtfMeter:
    """最近 `size` 句完整辨識的即時倍率（計算秒數 ÷ 音訊秒數）中位數（執行緒安全）。

    用中位數而不是平均：偶爾一句因為 GPU 被其他程式搶走而特別慢，不該讓整體看起來跟不上。
    """

    def __init__(self, size: int = RTF_WINDOW) -> None:
        """
        Raises:
            ValueError: `size` 不是正整數。
        """
        if isinstance(size, bool) or not isinstance(size, int) or size < 1:
            raise ValueError(f"size must be a positive integer, got {size!r}")
        self._lock = threading.Lock()
        self._values: deque[float] = deque(maxlen=size)

    def add(self, compute_secs: float, audio_secs: float) -> None:
        """記下一句。音訊長度為 0 時略過（算不出倍率）。

        Raises:
            ValueError: 數值不是有限值，或計算秒數為負。
        """
        if not math.isfinite(compute_secs) or not math.isfinite(audio_secs):
            raise ValueError(
                f"expected finite seconds, got {compute_secs!r} / {audio_secs!r}"
            )
        if compute_secs < 0:
            raise ValueError(f"compute_secs must be >= 0, got {compute_secs!r}")
        if audio_secs <= 0:
            return
        with self._lock:
            self._values.append(compute_secs / audio_secs)

    def median(self) -> float | None:
        """中位數，取到小數三位；沒有資料時 None。"""
        with self._lock:
            values = list(self._values)
        if not values:
            return None
        return round(statistics.median(values), 3)
