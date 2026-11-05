"""句尾判斷：決定進行中的句子何時結束。

`Segmenter` 在每個視窗後詢問 `EndpointPolicy`；之後的 Smart Turn、文字句尾
判斷都實作同一個介面接回來。時間一律以樣本數表示（16 kHz）。
"""

import math
from typing import Literal, Protocol

from kikitori.vad.model import SAMPLE_RATE

EndReason = Literal["pause", "max_length", "external", "flush"]


class EndpointPolicy(Protocol):
    """根據句長與已靜音長度決定是否結束這句。"""

    def decide(self, utterance_samples: int, silence_samples: int) -> EndReason | None:
        """回傳結束原因；還不該結束時回傳 None。

        Args:
            utterance_samples: 句子從起點（含前置緩衝）到目前的樣本數。
            silence_samples: 目前連續靜音的樣本數；沒有在靜音中時為 0。
        """
        ...


class PauseEndpoint:
    """靜音達 `pause_secs` 或句長達 `max_secs` 就結束這句。"""

    def __init__(self, pause_secs: float = 0.6, max_secs: float = 10.0) -> None:
        """
        Args:
            pause_secs: 結束一句所需的靜音秒數；預設 0.6 秒，實驗中比 0.2 秒
                更接近一句字幕。
            max_secs: 句長上限；超過就切斷，避免語音辨識等太久才拿到句子。

        Raises:
            ValueError: 不是正的有限數。
        """
        self.pause_samples = _positive_secs_to_samples("pause_secs", pause_secs)
        self.max_samples = _positive_secs_to_samples("max_secs", max_secs)

    def decide(self, utterance_samples: int, silence_samples: int) -> EndReason | None:
        """靜音達門檻回傳 `"pause"`；句長達上限回傳 `"max_length"`；否則 None。

        兩者同時成立時回傳 `"pause"`：句子已自然結束，不算被切斷。
        """
        if silence_samples >= self.pause_samples:
            return "pause"
        if utterance_samples >= self.max_samples:
            return "max_length"
        return None


def _positive_secs_to_samples(name: str, secs: float) -> int:
    if not (isinstance(secs, int | float) and math.isfinite(secs) and secs > 0):
        raise ValueError(f"{name} must be a positive finite number, got {secs!r}")
    return round(secs * SAMPLE_RATE)
