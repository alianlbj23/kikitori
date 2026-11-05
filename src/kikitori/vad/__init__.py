"""VAD 切句模組：把 16 kHz 單聲道音訊切成一句一句，交給語音辨識。

位於音訊擷取之後、語音辨識之前；時間一律用樣本計數，不用牆鐘時間。
對外只需要這裡匯出的名稱：`Segmenter` 搭配 `SileroVad`，設定 `ResetPolicy`
與 `EndpointPolicy`，處理三種事件。
"""

from kikitori.vad.endpoint import EndpointPolicy, EndReason, PauseEndpoint
from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES, VoiceModel
from kikitori.vad.segmenter import (
    ResetPolicy,
    Segmenter,
    SegmentEvent,
    UtteranceAudio,
    UtteranceEnded,
    UtteranceStarted,
)
from kikitori.vad.silero import SileroVad

__all__ = [
    "SAMPLE_RATE",
    "WINDOW_SAMPLES",
    "EndReason",
    "EndpointPolicy",
    "PauseEndpoint",
    "ResetPolicy",
    "SegmentEvent",
    "Segmenter",
    "SileroVad",
    "UtteranceAudio",
    "UtteranceEnded",
    "UtteranceStarted",
    "VoiceModel",
]
