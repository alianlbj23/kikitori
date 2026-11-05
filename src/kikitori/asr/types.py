"""語音辨識模組對外的介面：辨識結果與辨識器 Protocol。

位於 VAD 切句之後、翻譯之前；pipeline 只透過這裡的 `Transcriber` 使用辨識器，
實作（CTranslate2 Whisper 或測試用假物件）可以替換。定義與
`docs/specs/app-architecture.md` 的 asr 一節逐字一致。
"""

from dataclasses import dataclass
from typing import Protocol

import numpy as np


@dataclass(frozen=True)
class Transcript:
    """一句音訊的辨識結果。

    Attributes:
        text: 辨識出的文字（已去頭尾空白）。
        language: ISO 639-1 語言代碼，例如 "ja"。
        language_probability: 語言偵測的機率；呼叫端指定語言時為 1.0。
        no_speech_probability: 模型判斷「沒有語音」的機率，供幻覺過濾使用。
        avg_logprob: 輸出 token 的平均對數機率，越接近 0 越有信心。
    """

    text: str
    language: str
    language_probability: float
    no_speech_probability: float
    avg_logprob: float


class Transcriber(Protocol):
    """把一句 16 kHz 單聲道 int16 音訊轉成文字。"""

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        """辨識一句音訊。

        Args:
            audio: 一維 int16、16 kHz、單聲道。
            language: ISO 639-1 代碼；None 表示自動偵測。
            prompt: 前文提示（例如前幾句原文），提升用字一致性。
        """
        ...
