"""`VoiceModel` 介面：「512 樣本視窗 → 人聲機率」的模型層抽象。

`Segmenter` 只依賴這個介面，所以之後換成其他 VAD 模型不必改切句邏輯；
測試也用假的實作餵腳本化機率，讓事件邏輯可決定性地驗證。
"""

from typing import Protocol

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16000
WINDOW_SAMPLES = 512  # Silero v5+ 在 16 kHz 只接受 512 樣本（32 ms）

AudioWindow = npt.NDArray[np.int16] | npt.NDArray[np.float32]


class VoiceModel(Protocol):
    """逐視窗輸出人聲機率的串流模型。

    實作可以有跨視窗的內部狀態；呼叫端必須依時間順序餵入視窗。
    """

    def probability(self, window: AudioWindow) -> float:
        """回傳這個 512 樣本視窗（16 kHz 單聲道）含人聲的機率，範圍 0–1。"""
        ...

    def reset(self) -> None:
        """清除跨視窗的內部狀態，之後的輸入視為新串流的開頭。"""
        ...
