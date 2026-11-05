"""pipeline：雙路徑協調器，把擷取到的音訊變成字幕更新。

位於 capture、vad、asr、translate 之上，engine 之下。對外：`Pipeline`、
`SubtitleUpdate`、`PipelineConfig`、`PipelineComponents`。真實元件的建立放在
`kikitori.pipeline.factory`（需要時才匯入，它會載入 ASR 與 ONNX 模型）。
規格見 `docs/specs/pipeline-engine.md`。
"""

from kikitori.pipeline.filtering import default_transcript_filter
from kikitori.pipeline.languages import Route, route
from kikitori.pipeline.pipeline import Pipeline
from kikitori.pipeline.types import (
    MODES,
    Mode,
    PipelineComponents,
    PipelineConfig,
    PipelineState,
    SubtitleUpdate,
    TranscriptFilter,
)

__all__ = [
    "MODES",
    "Mode",
    "Pipeline",
    "PipelineComponents",
    "PipelineConfig",
    "PipelineState",
    "Route",
    "SubtitleUpdate",
    "TranscriptFilter",
    "default_transcript_filter",
    "route",
]
