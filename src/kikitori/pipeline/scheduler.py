"""雙路徑的工作排程：即時路徑的「最新槽」與完整路徑的有界 FIFO。

位於 pipeline 內部，連接 capture 執行緒（生產者）與兩個 worker（消費者）。
即時字幕只有最新的有意義，所以槽裡只留一個工作、舊的直接取代；完整結果
一句都不能少，所以用有界佇列，滿了讓生產者等待（背壓）。
"""

import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass, field

import numpy as np
import numpy.typing as npt


@dataclass(frozen=True, eq=False)
class Job:
    """一次辨識工作：某句話 `[start_sample, end_sample)` 的音訊。"""

    utterance_id: int
    audio: npt.NDArray[np.int16] = field(repr=False)
    start_sample: int
    end_sample: int
    ended_at: float | None = None
    """完整工作：VAD 判定句子結束時的時鐘值（pipeline 注入的時鐘，秒），算 `latency_ms` 用。"""


class LatestSlot:
    """只保留最新一個工作的槽；`put()` 會取代尚未取走的舊工作。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._job: Job | None = None
        self._closed = False
        self.dropped = 0
        """被新工作取代而沒有執行的工作數（診斷用）。"""

    def put(self, job: Job) -> None:
        """放入工作；槽內原有的工作被丟棄。關閉後放入無效果。"""
        with self._cond:
            if self._closed:
                return
            if self._job is not None:
                self.dropped += 1
            self._job = job
            self._cond.notify_all()

    def take(self, timeout: float | None) -> Job | None:
        """取出工作；最多等 `timeout` 秒（None 為不等），逾時或已關閉回傳 None。"""
        with self._cond:
            if timeout is not None and timeout > 0:
                self._cond.wait_for(
                    lambda: self._job is not None or self._closed, timeout
                )
            job, self._job = self._job, None
            return job

    def close(self) -> None:
        """關閉並清空；喚醒等待中的 `take()`。"""
        with self._cond:
            self._closed = True
            self._job = None
            self._cond.notify_all()


class FinalQueue:
    """完整路徑的有界 FIFO；工作不會被丟棄。"""

    def __init__(self, maxsize: int) -> None:
        """
        Args:
            maxsize: 容量；滿了 `put()` 會等待。
        """
        self._queue: queue.Queue[Job] = queue.Queue(maxsize=maxsize)

    def put(
        self, job: Job, should_abort: Callable[[], bool], poll: float = 0.1
    ) -> bool:
        """放入工作；佇列滿時每 `poll` 秒檢查一次 `should_abort()`。

        Returns:
            True 表示已放入；False 表示在等待中被要求放棄（pipeline 停止）。
        """
        while True:
            try:
                self._queue.put(job, timeout=poll)
                return True
            except queue.Full:
                if should_abort():
                    return False

    def put_nowait(self, job: Job) -> None:
        """不等待地放入（逐步模式用）。

        Raises:
            RuntimeError: 佇列已滿；逐步模式要先 `run_pending()`。
        """
        try:
            self._queue.put_nowait(job)
        except queue.Full:
            raise RuntimeError(
                "final queue is full; call run_pending() before feeding more audio"
            ) from None

    def take(self, timeout: float | None) -> Job | None:
        """取出最舊的工作；最多等 `timeout` 秒（None 為不等），沒有工作回傳 None。"""
        try:
            if timeout is None or timeout <= 0:
                return self._queue.get_nowait()
            return self._queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def empty(self) -> bool:
        """佇列是否為空。"""
        return self._queue.empty()

    def size(self) -> int:
        """目前在佇列中等待的工作數（約略值：其他執行緒可能同時放入或取出）。"""
        return self._queue.qsize()

    def clear(self) -> int:
        """丟棄所有尚未處理的工作（只在停止時使用），回傳丟棄數。"""
        count = 0
        while self.take(None) is not None:
            count += 1
        return count
