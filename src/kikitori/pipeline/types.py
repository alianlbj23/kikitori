"""pipeline 對外的型別：模式、字幕更新、設定與元件組合。

位於 pipeline 子套件的合約層：engine 透過 `SubtitleUpdate` 送出 `subtitle` 事件，
factory 依設定組出 `PipelineComponents`。`SubtitleUpdate` 照抄
`docs/specs/app-architecture.md`；其餘型別見 `docs/specs/pipeline-engine.md`。
"""

import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from kikitori.asr.types import Transcriber, Transcript
from kikitori.translate.types import Translator
from kikitori.vad.model import VoiceModel

Mode = Literal["media", "interpret"]
MODES: tuple[Mode, ...] = ("media", "interpret")

PipelineState = Literal["idle", "running", "error"]

TranscriptFilter = Callable[[Transcript], str | None]
"""回傳 None 表示保留這個辨識結果；否則回傳丟掉的原因（供 log）。"""

StatusCallback = Callable[[PipelineState, str | None], None]
LogCallback = Callable[[str, str], None]
"""`(level, message)`；level 為 "debug"、"info"、"warning"、"error"。"""


@dataclass(frozen=True)
class SubtitleUpdate:
    """一則字幕更新。

    Attributes:
        utterance_id: 句子編號（每個 session 從 1 開始）。
        original: 原文。
        translation: 譯文；翻譯失敗或語言組合不支援時為空字串。
        source_language: ASR 偵測的 ISO 639-1 語言碼。
        target_language: 譯文語言（"zh-TW"、"ja"、"en"）。
        is_final: False 為即時字幕（之後會被取代）；True 為完整翻譯（最終版）。
            原文與譯文皆為空的完整結果表示「這句其實不是語音，清掉即時字幕」。
        start_sample: 句子起點（16 kHz 樣本，以串流開頭為 0）。
        end_sample: 句子終點（不含）。
    """

    utterance_id: int
    original: str
    translation: str
    source_language: str
    target_language: str
    is_final: bool
    start_sample: int
    end_sample: int


@dataclass(frozen=True)
class PipelineConfig:
    """一個 session 的 pipeline 參數。

    Attributes:
        mode: "media"（影片翻譯）或 "interpret"（即時口譯）。
        pause_secs: 靜音多久算一句結束。
        max_secs: 句長上限。
        target_language: 使用者的語言（譯文語言），目前只有 "zh-TW"。
        partner_language: interpret 模式的對方語言（"ja" 或 "en"）。
        partial_interval_ms: 每隔多少毫秒的新音訊做一次即時辨識。
        context_lines: 完整路徑保留幾句前文。
        asr_prompt_context: 完整路徑是否以前文當 ASR prompt。
        final_queue_size: 完整路徑佇列容量；滿了 capture 執行緒會等待（背壓）。
        read_timeout: 每次 `AudioSource.read()` 的等待上限（秒）。

    Raises:
        ValueError: 參數超出範圍。
    """

    mode: Mode
    pause_secs: float
    max_secs: float
    target_language: str = "zh-TW"
    partner_language: str = "ja"
    partial_interval_ms: int = 700
    context_lines: int = 3
    asr_prompt_context: bool = True
    final_queue_size: int = 64
    read_timeout: float = 0.1

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {self.mode!r}")
        for name in ("pause_secs", "max_secs", "read_timeout"):
            value = getattr(self, name)
            if not (
                isinstance(value, int | float)
                and not isinstance(value, bool)
                and math.isfinite(value)
                and value > 0
            ):
                raise ValueError(f"{name} must be a positive number, got {value!r}")
        for name, low in (
            ("partial_interval_ms", 1),
            ("context_lines", 0),
            ("final_queue_size", 1),
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int) or value < low:
                raise ValueError(f"{name} must be an integer >= {low}, got {value!r}")


@dataclass(frozen=True)
class PipelineComponents:
    """pipeline 需要的元件；測試以假物件組成，正式版由 `factory.build_components()` 建立。

    即時與完整路徑各用一個 transcriber 實例，才能並行；翻譯器可以共用（執行緒安全）。
    """

    voice_model: VoiceModel
    fast_transcriber: Transcriber
    final_transcriber: Transcriber
    fast_translator: Translator
    final_translator: Translator
    transcript_filter: TranscriptFilter
    final_no_speech_reliable: bool = True
    """完整模型的 `no_speech_probability` 是否可用；False 時改用即時模型的值過濾。"""
