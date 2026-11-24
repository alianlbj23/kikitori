"""遠端運算 B 端的聲音來源：把網路收到的音訊當成一個 `AudioSource` 交給現有的 `Pipeline`。

位於 remote 模組（docs/specs/remote-compute.md「架構」）：B 用不改動的 pipeline 處理 A 送來的
連續音訊，所以只需要一個「別人推進來、pipeline 自己讀」的來源。網路執行緒呼叫 `push()`，
pipeline 的 capture 執行緒呼叫 `read()`。

緩衝有上限：pipeline 的 capture 迴圈只做切句，正常時遠比即時快；緩衝滿了代表 B 卡住了。
這時丟掉音訊會讓樣本計數（字幕時間軸）錯位，所以丟出 `PushSourceOverflow` 讓 session 失敗、
告訴 A，而不是悄悄丟資料。
"""

import threading
from collections import deque

import numpy as np
import numpy.typing as npt

AudioChunk = npt.NDArray[np.int16]


class PushSourceOverflow(RuntimeError):
    """緩衝已滿：讀的一方跟不上（B 卡住），這個 session 不能再保證時間軸。"""


class PushSource:
    """執行緒安全、有上限的推送式聲音來源（16 kHz 單聲道 int16）。"""

    def __init__(self, max_samples: int) -> None:
        """
        Args:
            max_samples: 還沒被讀走的樣本上限（例如 30 秒 = 480,000）。

        Raises:
            ValueError: `max_samples` 不是正整數。
        """
        if max_samples <= 0:
            raise ValueError(f"max_samples must be positive, got {max_samples}")
        self._max = max_samples
        self._chunks: deque[AudioChunk] = deque()
        self._pending = 0
        self._received = 0
        self._closed = False
        self._cond = threading.Condition()

    @property
    def received_samples(self) -> int:
        """目前為止收到的樣本總數（A 的時間軸；斷線續傳時用來對齊）。"""
        with self._cond:
            return self._received

    @property
    def exhausted(self) -> bool:
        """已關閉而且緩衝讀完了（`Pipeline` 據此收尾最後一句並結束）。"""
        with self._cond:
            return self._closed and self._pending == 0

    def start(self) -> None:
        """`AudioSource` 介面：沒有裝置要開。"""

    def push(self, audio: AudioChunk) -> None:
        """加入一段音訊（網路執行緒）。

        Raises:
            ValueError: 不是一維 int16。
            RuntimeError: 已關閉。
            PushSourceOverflow: 緩衝已滿（這段不會加入）。
        """
        if audio.ndim != 1 or audio.dtype != np.int16:
            raise ValueError(
                f"audio must be 1-D int16, got {audio.dtype} {audio.shape}"
            )
        if len(audio) == 0:
            return
        with self._cond:
            if self._closed:
                raise RuntimeError("the source is closed")
            if self._pending + len(audio) > self._max:
                raise PushSourceOverflow(
                    f"{self._pending + len(audio)} samples would be pending, limit {self._max}"
                )
            self._chunks.append(audio.copy())
            self._pending += len(audio)
            self._received += len(audio)
            self._cond.notify_all()

    def read(self, timeout: float) -> AudioChunk:
        """等最多 `timeout` 秒，回傳目前累積的全部音訊；逾時或已讀完回空陣列。"""
        with self._cond:
            self._cond.wait_for(lambda: self._pending > 0 or self._closed, timeout)
            if not self._chunks:
                return np.zeros(0, dtype=np.int16)
            joined = np.concatenate(list(self._chunks))
            self._chunks.clear()
            self._pending = 0
            return joined

    def close(self) -> None:
        """A 說串流結束：之後不能再推；讀完剩下的音訊後 `exhausted` 為 True。"""
        with self._cond:
            self._closed = True
            self._cond.notify_all()

    def stop(self) -> None:
        """`AudioSource` 介面：停止（丟掉沒讀的音訊，叫醒等待中的讀取）。可重複呼叫。"""
        with self._cond:
            self._closed = True
            self._chunks.clear()
            self._pending = 0
            self._cond.notify_all()
