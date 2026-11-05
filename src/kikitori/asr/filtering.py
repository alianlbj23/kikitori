"""辨識結果的幻覺過濾：判斷一個 `Transcript` 是否該丟掉。

位於 asr 與 pipeline 之間：pipeline 對每個辨識結果呼叫 `rejection_reason()`，
有回傳原因就不送去翻譯。Whisper 在靜音、BGM、歌聲時常輸出訓練資料裡的
字幕套話（「ご視聴ありがとうございました」）或重複迴圈，這裡集中處理。
門檻都在 `FilterConfig`，由設定頁覆寫。
"""

import unicodedata
import zlib
from dataclasses import dataclass, field

from kikitori.asr.types import Transcript


def normalize_for_match(text: str) -> str:
    """比對用的正規化：NFKC、小寫、去掉標點／符號／空白。"""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in "LN")


# Whisper 在沒有語音時常見的輸出（來自字幕資料的片尾套話）。整句相等才過濾，
# 以可讀形式列出，比對前經 `normalize_for_match` 正規化。
DEFAULT_HALLUCINATIONS: frozenset[str] = frozenset(
    {
        # 日文
        "ご視聴ありがとうございました",
        "ご清聴ありがとうございました",
        "チャンネル登録よろしくお願いします",
        "チャンネル登録お願いします",
        "最後までご視聴頂きありがとうございました",
        "最後までご視聴いただきありがとうございました",
        # 英文
        "Thanks for watching!",
        "Thank you for watching.",
        "Please subscribe.",
        "Subtitles by the Amara.org community",
        # large-v3 在靜音時的挪威語字幕署名（本機實測）
        "Teksting av Nicolai Winther",
        # 中文
        "字幕由Amara.org社区提供",
        "字幕由Amara.org社區提供",
        "请不吝点赞 订阅 转发 打赏支持明镜与点点栏目",
        "請不吝點贊 訂閱 轉發 打賞支持明鏡與點點欄目",
        "谢谢观看",
        "謝謝觀看",
    }
)

# 真人也常單獨說的短句：只有在 no_speech 偏高時才當成幻覺
DEFAULT_SOFT_HALLUCINATIONS: frozenset[str] = frozenset(
    {
        "Thank you.",
        "Thanks.",
        "you",
        "ありがとうございました",
        "おやすみなさい",
        "谢谢",
        "謝謝",
    }
)


@dataclass(frozen=True)
class FilterConfig:
    """幻覺過濾的門檻。

    Attributes:
        no_speech_threshold: 與 `log_prob_threshold` 同時成立才算靜音（Whisper 的規則）。
        log_prob_threshold: 見上。
        hard_no_speech_threshold: 單獨超過就算靜音，不看 avg_logprob（本機實測語音最高 0.43、非語音最低 0.51）。
        min_avg_logprob: 平均對數機率低於此值就丟掉（歌聲、BGM 常見）。
        compression_ratio_threshold: zlib 壓縮比高於此值代表重複迴圈。
        soft_no_speech_threshold: 「Thank you」這類短句在 no_speech 高於此值時才過濾。
        hallucinations: 整句相等就過濾的已知幻覺句；建構時自動正規化。
        soft_hallucinations: 需搭配 `soft_no_speech_threshold` 的短句；建構時自動正規化。
    """

    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    hard_no_speech_threshold: float = 0.8
    min_avg_logprob: float = -1.5
    compression_ratio_threshold: float = 2.4
    soft_no_speech_threshold: float = 0.3
    hallucinations: frozenset[str] = field(default=DEFAULT_HALLUCINATIONS)
    soft_hallucinations: frozenset[str] = field(default=DEFAULT_SOFT_HALLUCINATIONS)

    def __post_init__(self) -> None:
        for name in (
            "no_speech_threshold",
            "hard_no_speech_threshold",
            "soft_no_speech_threshold",
        ):
            value = getattr(self, name)
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be within [0, 1], got {value}")
        if self.compression_ratio_threshold <= 0:
            raise ValueError("compression_ratio_threshold must be positive")
        # 讓呼叫端可以用可讀的句子設定清單；frozen dataclass 只能用 object.__setattr__
        for name in ("hallucinations", "soft_hallucinations"):
            phrases = frozenset(normalize_for_match(p) for p in getattr(self, name)) - {
                ""
            }
            object.__setattr__(self, name, phrases)


def compression_ratio(text: str) -> float:
    """UTF-8 長度 ÷ zlib 壓縮後長度；與 Whisper／faster-whisper 的定義相同。"""
    data = text.encode("utf-8")
    if not data:
        return 0.0
    return len(data) / len(zlib.compress(data))


def rejection_reason(
    transcript: Transcript, config: FilterConfig | None = None
) -> str | None:
    """判斷辨識結果是否該丟掉。

    Returns:
        None 表示保留；否則是簡短的英文原因（供 log 使用）。
    """
    config = FilterConfig() if config is None else config
    normalized = normalize_for_match(transcript.text)
    if not normalized:
        return "empty"
    no_speech = transcript.no_speech_probability
    if no_speech > config.no_speech_threshold and (
        transcript.avg_logprob < config.log_prob_threshold
    ):
        return "no_speech"
    if no_speech > config.hard_no_speech_threshold:
        return "no_speech_high"
    if transcript.avg_logprob < config.min_avg_logprob:
        return "low_logprob"
    if compression_ratio(transcript.text) > config.compression_ratio_threshold:
        return "repetitive"
    if normalized in config.hallucinations:
        return "known_hallucination"
    if (
        normalized in config.soft_hallucinations
        and no_speech > config.soft_no_speech_threshold
    ):
        return "known_hallucination"
    return None
