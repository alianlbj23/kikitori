"""辨識結果的幻覺過濾：判斷一個 `Transcript` 是否該丟掉。

位於 asr 與 pipeline 之間：pipeline 對每個辨識結果呼叫 `rejection_reason()`，
有回傳原因就不送去翻譯。Whisper 在靜音、BGM、歌聲時常輸出訓練資料裡的
字幕套話（「ご視聴ありがとうございました」）或重複迴圈，這裡集中處理。
門檻都在 `FilterConfig`，由設定頁覆寫。

已知幻覺片語依 Whisper 語言碼分表，每個語言分成兩類：
- `hard`：整句的字幕套話（「Thanks for watching!」「谢谢观看」）。幾乎不可能是另一個語言的
  正常語音，而 Whisper 常把它們標成別的語言（英文套話標成 nl、中文套話標成 ja），所以
  **不論辨識出哪個語言**，所有語言的 hard 片語都套用。
- `soft`：真人也常單獨說的短句（「Thank you.」「谢谢」）。只比對辨識語言那一張表（加上
  `ANY_LANGUAGE` 表），而且 no_speech 偏高時才濾，避免把其他語言的正常句子濾掉。

預設片語表在 `hallucination_lexicon.py`：`experiments/hallucination_lexicon/` 對 19 個
Whisper 語言實測、人工分類的 `lexicon.json`（`{語言: {"hard": [...], "soft": [...]}}`）的
Python 版本。更新片語時改 `lexicon.json` 並重新產生該檔案。
"""

import unicodedata
import zlib
from collections.abc import Collection, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal

from kikitori.asr.hallucination_lexicon import HALLUCINATION_LEXICON
from kikitori.asr.types import Transcript

ANY_LANGUAGE = "*"
"""片語表中「不論辨識出哪個語言都套用」的鍵。"""

PhraseKind = Literal["hard", "soft"]
PHRASE_KINDS: tuple[PhraseKind, ...] = ("hard", "soft")

LanguagePhrases = Mapping[PhraseKind, frozenset[str]]
"""一個語言的片語：`{"hard": ..., "soft": ...}`（正規化後兩個鍵都在）。"""

HallucinationTable = Mapping[str, LanguagePhrases]
"""Whisper 語言碼（或 `ANY_LANGUAGE`）→ 該語言的 hard／soft 片語。"""

HallucinationTableInput = Mapping[str, Mapping[str, Collection[str]]] | Collection[str]
"""`FilterConfig.hallucinations` 接受的格式：依語言分表，或單一集合（視為不分語言的 hard）。"""


_KEEP_MARKS = (("\u0900", "\u097f"), ("\u0e00", "\u0e7f"))
"""保留組合記號（Mn／Mc）的區段：天城文（印地語）與泰文。"""


def _keep_for_match(ch: str) -> bool:
    category = unicodedata.category(ch)
    if category[0] in "LN":
        return True
    # 天城文與泰文的母音、聲調記號是字的一部分（「करते हैं」與「करती है」只差在記號），
    # 去掉會讓不同的詞變成同一個片語；用實測輸出比對，保留後印地語片語的命中只少 1/761、
    # 泰語不變。阿拉伯文的母音符號可有可無、拉丁字母的重音經 NFKC 已合成成字母，照舊忽略。
    return category in ("Mn", "Mc") and any(lo <= ch <= hi for lo, hi in _KEEP_MARKS)


def normalize_for_match(text: str) -> str:
    """比對用的正規化：NFKC、小寫、去掉標點／符號／空白（天城文與泰文的組合記號保留）。"""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if _keep_for_match(ch))


def _phrases(hard: set[str], soft: set[str]) -> LanguagePhrases:
    return MappingProxyType({"hard": frozenset(hard), "soft": frozenset(soft)})


# Whisper 在沒有語音時常見的輸出（字幕資料的片尾套話、字幕署名、擬聲詞），依語言分表。
# 以可讀形式保存，比對前經 `normalize_for_match` 正規化（`FilterConfig` 建構時）。
# 字幕署名放在不分語言的 "*" 表只是為了分類清楚：hard 片語本來就不論語言標籤都套用
# （large-v3 在靜音上輸出挪威語署名時，本機實測語言標籤是候選語言之一而不是 "no"）。
DEFAULT_HALLUCINATIONS: HallucinationTable = MappingProxyType(
    {
        language: _phrases(set(entry["hard"]), set(entry["soft"]))
        for language, entry in HALLUCINATION_LEXICON.items()
    }
)


def _language_key(language: str) -> str:
    return language.strip().lower().replace("_", "-").split("-", 1)[0]


def _normalize_phrases(entries: object, path: str) -> frozenset[str]:
    # 字串本身也是可迭代的，必須明確擋掉，否則會被拆成一個個字當片語。
    if isinstance(entries, str) or not isinstance(entries, Collection):
        raise TypeError(f"{path} must be a collection of phrases")
    if not all(isinstance(p, str) for p in entries):
        raise TypeError(f"{path} must contain only str")
    return frozenset(normalize_for_match(p) for p in entries) - {""}


def _normalize_table(phrases: object) -> HallucinationTable:
    """片語表正規化：鍵取主語言並小寫、每個語言補齊 hard／soft、片語經 `normalize_for_match`。

    單一集合視為不分語言的 hard 片語。

    Raises:
        TypeError: 格式不對（不是 dict 或集合、語言對應的不是 hard／soft 物件、片語是字串）。
        ValueError: 出現 hard／soft 以外的種類（多半是拼錯；靜默忽略會讓片語失效）。
    """
    name = "hallucinations"
    if isinstance(phrases, str) or not isinstance(phrases, Mapping | Collection):
        raise TypeError(f"{name} must be a mapping or a collection of phrases")
    if not isinstance(phrases, Mapping):
        phrases = {ANY_LANGUAGE: {"hard": phrases}}
    merged: dict[str, dict[PhraseKind, frozenset[str]]] = {}
    for language, entry in phrases.items():
        if not isinstance(language, str):
            raise TypeError(f"{name} keys must be language codes, got {language!r}")
        if not isinstance(entry, Mapping):
            raise TypeError(
                f"{name}[{language!r}] must map 'hard'/'soft' to phrase collections"
            )
        unknown = sorted(str(kind) for kind in entry if kind not in PHRASE_KINDS)
        if unknown:
            raise ValueError(
                f"{name}[{language!r}]: unknown phrase kinds {unknown} "
                f"(expected {list(PHRASE_KINDS)})"
            )
        key = ANY_LANGUAGE if language == ANY_LANGUAGE else _language_key(language)
        target = merged.setdefault(key, {kind: frozenset() for kind in PHRASE_KINDS})
        for kind in PHRASE_KINDS:
            if kind in entry:
                target[kind] |= _normalize_phrases(
                    entry[kind], f"{name}[{language!r}][{kind!r}]"
                )
    return MappingProxyType({k: MappingProxyType(v) for k, v in merged.items()})


@dataclass(frozen=True)
class FilterConfig:
    """幻覺過濾的門檻。

    Attributes:
        no_speech_threshold: 與 `log_prob_threshold` 同時成立才算靜音（Whisper 的規則）。
        log_prob_threshold: 見上。
        hard_no_speech_threshold: 單獨超過就算靜音，不看 avg_logprob（本機實測語音最高 0.43、非語音最低 0.51）。
        min_avg_logprob: 平均對數機率低於此值就丟掉（歌聲、BGM 常見）。
        compression_ratio_threshold: zlib 壓縮比高於此值代表重複迴圈。
        soft_no_speech_threshold: soft 片語（「Thank you」這類短句）在 no_speech 高於此值時才過濾。
        hallucinations: 已知幻覺片語 `{Whisper 語言碼: {"hard": [...], "soft": [...]}}`
            （`ANY_LANGUAGE` 為不分語言；hard／soft 可省略其一）。所有語言的 hard 片語
            不論辨識語言都套用；soft 只比對辨識語言與 `ANY_LANGUAGE` 的表。給單一集合時
            視為不分語言的 hard 片語。建構時自動正規化成 `HallucinationTable`。
        hard_phrases: 所有語言 hard 片語的聯集（正規化後）；由 `hallucinations` 算出，不能指定。
    """

    no_speech_threshold: float = 0.6
    log_prob_threshold: float = -1.0
    hard_no_speech_threshold: float = 0.8
    min_avg_logprob: float = -1.5
    compression_ratio_threshold: float = 2.4
    soft_no_speech_threshold: float = 0.3
    hallucinations: HallucinationTableInput = field(default=DEFAULT_HALLUCINATIONS)
    hard_phrases: frozenset[str] = field(init=False, repr=False, compare=False)

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
        table = _normalize_table(self.hallucinations)
        object.__setattr__(self, "hallucinations", table)
        # 每句都要比對 hard 片語，先算好聯集，不必每次走訪所有語言
        hard: frozenset[str] = frozenset().union(
            *(entry["hard"] for entry in table.values())
        )
        object.__setattr__(self, "hard_phrases", hard)

    def soft_phrases_for(self, language: str) -> frozenset[str]:
        """某個 Whisper 語言要比對的（正規化後）soft 片語：該語言的表加上不分語言的表。"""
        table = self.hallucinations
        assert isinstance(table, Mapping)  # __post_init__ 已正規化
        result: frozenset[str] = frozenset()
        for key in (_language_key(language), ANY_LANGUAGE):
            entry = table.get(key)
            if entry is not None:
                result |= entry["soft"]
        return result


_DEFAULT_CONFIG = FilterConfig()
"""`rejection_reason()` 沒給設定時用；FilterConfig 不可變，預先建好就不必每句重新正規化
整份片語表（數百個片語）。"""


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
    config = _DEFAULT_CONFIG if config is None else config
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
    # hard 片語不看語言標籤：Whisper 對這些套話的語言標籤不可靠（見模組說明）。
    if normalized in config.hard_phrases:
        return "known_hallucination"
    # soft 短句真人也常說：只比對辨識出的語言，而且 no_speech 要偏高。
    if (
        normalized in config.soft_phrases_for(transcript.language)
        and no_speech > config.soft_no_speech_threshold
    ):
        return "known_hallucination"
    return None
