"""pipeline 對外的型別：模式、字幕更新、設定與元件組合。

位於 pipeline 子套件的合約層：engine 透過 `SubtitleUpdate` 送出 `subtitle` 事件，
factory 依設定組出 `PipelineComponents`。`SubtitleUpdate` 照抄
`docs/specs/app-architecture.md`；其餘型別見 `docs/specs/pipeline-engine.md`。
"""

import logging
import math
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Protocol

from kikitori.asr.types import Transcriber, Transcript
from kikitori.languages import UnknownLanguageError, get_language
from kikitori.translate.types import Translator
from kikitori.vad.model import VoiceModel

logger = logging.getLogger(__name__)

Mode = Literal["media", "interpret"]
MODES: tuple[Mode, ...] = ("media", "interpret")

PipelineState = Literal["idle", "running", "error"]

TranscriptFilter = Callable[[Transcript], str | None]
"""回傳 None 表示保留這個辨識結果；否則回傳丟掉的原因（供 log）。"""


class BackgroundTask(Protocol):
    """跟著 session 執行的背景工作（例如 `keep_warm.KeepWarm`）。"""

    def start(self) -> None:
        """`Pipeline.start()` 成功後呼叫；失敗時丟例外（只記 warning，不中斷 session）。"""
        ...

    def stop(self, timeout: float = ...) -> None:
        """pipeline 結束（停止、讀完或錯誤）時呼叫；不可長時間阻塞。"""
        ...


StatusCallback = Callable[[PipelineState, str | None], None]
LogCallback = Callable[[str, str], None]
"""`(level, message)`；level 為 "debug"、"info"、"warning"、"error"。"""


@dataclass(frozen=True)
class SubtitleUpdate:
    """一則字幕更新。

    Attributes:
        utterance_id: 句子編號（每個 session 從 1 開始）。
        original: 原文。
        translation: 譯文；翻譯失敗、語言組合不支援或關閉翻譯時為空字串。
        source_language: ASR 偵測的語言碼（Whisper 語言碼，中文為 "zh"）。
        target_language: 譯文語言（語言清單的語言碼，例如 "zh-TW"、"ko"）。
        is_final: False 為即時字幕（之後會被取代）；True 為完整翻譯（最終版）。
            原文與譯文皆為空的完整結果表示「這句其實不是語音，清掉即時字幕」。
        start_sample: 句子起點（16 kHz 樣本，以串流開頭為 0）。
        end_sample: 句子終點（不含）。
        stable_chars: 只有即時字幕有（完整字幕為 None）：`original` 開頭有幾個字元
            （Python 字元數）與同一句上一則即時字幕相同（LocalAgreement-2），之後的
            即時結果大概不會再改；第一則為 0，不超過 `len(original)`。
        latency_ms: 只有完整字幕有（即時字幕為 None）：從 VAD 判定句子結束到送出這則
            完整字幕的毫秒數（含排隊、完整辨識與翻譯；不含判定句尾所需的靜音）。
    """

    utterance_id: int
    original: str
    translation: str
    source_language: str
    target_language: str
    is_final: bool
    start_sample: int
    end_sample: int
    # 架構規格之後新增的欄位都有預設值：只建構原本欄位的呼叫端不受影響
    stable_chars: int | None = None
    latency_ms: int | None = None


@dataclass(frozen=True)
class PipelineConfig:
    """一個 session 的 pipeline 參數。

    Attributes:
        mode: "media"（影片翻譯）或 "interpret"（即時口譯）。
        pause_secs: 靜音多久算一句結束。
        max_secs: 句長上限。
        target_language: 字幕語言；口譯時是使用者的語言（語言清單的語言碼）。
        partner_language: interpret 模式的對方語言（語言清單的語言碼）。
        source_language: media 模式的收聽語言（語言清單的語言碼）或 "auto"；只影響
            原文的字形顯示，辨識語言由 factory 設定給 ASR。
        translation_enabled: False 時只顯示原文，完全不呼叫翻譯器。
        partial_interval_ms: 每隔多少毫秒的新音訊做一次即時辨識。
        context_lines: 完整路徑保留幾句前文。
        asr_prompt_context: 完整路徑是否以前文當 ASR prompt。
        final_queue_size: 完整路徑佇列容量；滿了 capture 執行緒會等待（背壓）。
        read_timeout: 每次 `AudioSource.read()` 的等待上限（秒）。

    Raises:
        ValueError: 參數超出範圍或語言碼不在語言清單內。
        TypeError: `translation_enabled` 不是 bool。
    """

    mode: Mode
    pause_secs: float
    max_secs: float
    target_language: str = "zh-TW"
    partner_language: str = "ja"
    source_language: str = "auto"
    translation_enabled: bool = True
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
        for name in ("target_language", "partner_language", "source_language"):
            value = getattr(self, name)
            if name == "source_language" and value == "auto":
                continue
            try:
                normalized = get_language(value).code
            except UnknownLanguageError as exc:
                raise ValueError(f"{name}: {exc}") from None
            if normalized != value:
                raise ValueError(
                    f"{name}: expected the normalized code {normalized!r}, got {value!r}"
                )
        if not isinstance(self.translation_enabled, bool):
            raise TypeError("translation_enabled must be bool")


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
    keep_warm: BackgroundTask | None = None
    """執行緒模式的 session 執行期間讓本機翻譯模型保持載入；None 表示不需要。"""


def release_components(components: PipelineComponents) -> None:
    """session 結束後釋放元件持有的資源：呼叫每個 transcriber 的 `close()`（有的話）。

    真實的 Whisper transcriber 會把 CTranslate2 模型卸載並放回行程內的模型池（每建立一個
    新的模型物件就漏約 8 MiB 顯示記憶體，重複使用同一個物件則不會；見 `asr.md`）。即時與
    完整路徑共用同一個實例時只關一次。失敗只記錄：釋放是收尾，不能讓 session 的結束流程
    中斷（最壞情況是這個模型物件不回到池中，交給參照計數釋放）。
    """
    seen: set[int] = set()
    for transcriber in (components.fast_transcriber, components.final_transcriber):
        if id(transcriber) in seen:
            continue
        seen.add(id(transcriber))
        close = getattr(transcriber, "close", None)
        if not callable(close):
            continue
        try:
            close()
        except Exception:
            logger.exception("closing the ASR transcriber failed")
