"""收聽／字幕語言清單：唯一的語言資料來源。

設定驗證、翻譯路由、提示詞與（之後的）UI 語言選單都從這裡取得語言，不各自維護一份。
語言碼分兩層：

- 清單語言碼（`Language.code`）：設定與字幕用，例如 "zh-TW"、"zh-CN"、"ja"。
- Whisper 語言碼（`Language.whisper_code`）：ASR 辨識與偵測用，例如 "zh"、"ja"。
  繁中與簡中在 Whisper 都是 "zh"，所以 "zh" 不是清單語言碼：字幕語言必須決定繁簡，
  而辨識結果只知道「中文」。`normalize_language("zh")` 因此會拒絕，
  `to_whisper_code()` 則兩種都接受。

清單固定，不開放任意語言碼：每個語言都要有提示詞名稱、字形轉換規則與幻覺清單等資料。
規格見 `docs/specs/languages-i18n.md`。
"""

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Literal

AsrQuality = Literal["A", "B", "C"]
"""Whisper 在公開基準上的錯誤率等級：A ≤ 10%、B 10–20%、C > 20%（WER；中日泰為 CER）。"""


class UnknownLanguageError(ValueError):
    """語言碼不在清單內（或不是字串）。"""


# --- 各 ASR 模型（asr registry 的 id）對每個語言的辨識品質等級 -----------------------
#
# 出處：research_notes/VTuber 直播模型選型調查/language_coverage.md（2026-11-05）。
# 推薦時用它提示「這個語言用這個模型辨識品質較低」，所以寧可低估：
#
# - large-v3、large-v3-turbo：OpenAI README 的 large-v3 圖與 turbo 公告圖（FLEURS 與
#   Common Voice 15），取兩個資料集中「較差」的值分級（§1.2 主表的「分級 v3／turbo」）。
#   vi 在 FLEURS 是 8.6、Common Voice 15 卻是 39.8，依規則為 C；turbo 圖沒收錄 vi
#   （large-v3 已 > 20%），同樣是 C。
# - small、base：Whisper 論文 Table 13（FLEURS）的逐尺寸數字（§1.3「推論的分級」）。
#   這些模型沒有 v3 世代、也沒有 Common Voice 15 的數字，而 Common Voice 普遍更差，
#   所以只會高估、不會低估；表中每一格都不比 large-v3 好。
# - tiny、medium：同一張 Table 13（§1.3 表格），再與 large-v3 的等級取較差者
#   （例如 medium 的 ja FLEURS 7.1 是 A，但 large-v3 的 ja 只有 B，就記 B）：較小的
#   模型實際上不會比 large-v3 好，只看 FLEURS 會過度樂觀。
# 未列出的模型（之後新增的模型）沒有資料，`asr_quality()` 回傳 None，推薦不提示。
_A, _B, _C = "A", "B", "C"
_LANGUAGE_ORDER = (
    "ja",
    "en",
    "zh-TW",
    "zh-CN",
    "ko",
    "es",
    "fr",
    "de",
    "pt",
    "ru",
    "it",
    "ar",
    "hi",
    "id",
    "vi",
    "th",
    "tr",
    "pl",
    "nl",
    "uk",
)
# 每列依 _LANGUAGE_ORDER：ja en zh-TW zh-CN ko es fr de pt ru it ar hi id vi th tr pl nl uk
_QUALITY_ROWS: dict[str, tuple[str, ...]] = {
    "large-v3": (_B, _A, _A, _B, _A, _A, _B, _A, _A, _A, _A, _B, _B, _A, _C, _A, _B, _A, _A, _B),
    "large-v3-turbo": (_B, _B, _B, _B, _A, _A, _B, _A, _A, _A, _A, _C, _C, _A, _C, _C, _B, _A, _A, _B),
    "medium": (_B, _A, _B, _B, _B, _A, _B, _A, _A, _A, _A, _C, _C, _B, _C, _B, _B, _A, _A, _B),
    "small": (_B, _A, _C, _C, _B, _A, _B, _B, _A, _B, _A, _C, _C, _B, _C, _C, _B, _B, _B, _B),
    "base": (_C, _A, _C, _C, _C, _A, _C, _B, _B, _C, _B, _C, _C, _C, _C, _C, _C, _C, _C, _C),
    "tiny": (_C, _B, _C, _C, _C, _B, _C, _C, _C, _C, _C, _C, _C, _C, _C, _C, _C, _C, _C, _C),
}  # fmt: skip

ASR_QUALITY_MODELS: tuple[str, ...] = tuple(_QUALITY_ROWS)
"""有品質等級資料的 ASR 模型（asr registry 的 id）。"""


def _quality(code: str) -> Mapping[str, AsrQuality]:
    index = _LANGUAGE_ORDER.index(code)
    return MappingProxyType(
        {model: row[index] for model, row in _QUALITY_ROWS.items()}  # type: ignore[misc]
    )


@dataclass(frozen=True)
class Language:
    """清單中的一個語言。

    Attributes:
        code: 清單語言碼（BCP 47 形式，例如 "zh-TW"）。
        english_name: 英文名，用於英文提示詞與紀錄。
        native_name: 該語言自己的名稱，用於語言選單與提示詞。
        whisper_code: Whisper 的語言碼（ISO 639-1；繁簡中文皆為 "zh"）。
        no_space: 書寫時詞與詞之間不加空白（中、日、泰）。
        script_conversion: 輸出要套用的 OpenCC 設定名稱（不含 ".json"）；None 表示不轉換。
        rtl: 由右到左書寫。
        asr_quality: ASR 模型（asr registry 的 id）→ 這個語言的辨識品質等級
            （唯讀；來源見本模組的 `_QUALITY_ROWS`）。
    """

    code: str
    english_name: str
    native_name: str
    whisper_code: str
    no_space: bool = False
    script_conversion: str | None = None
    rtl: bool = False
    asr_quality: Mapping[str, AsrQuality] = field(
        default_factory=lambda: MappingProxyType({})
    )


LANGUAGES: tuple[Language, ...] = (
    Language("ja", "Japanese", "日本語", "ja", no_space=True),
    Language("en", "English", "English", "en"),
    # 台灣正體用 s2twp（含台灣慣用詞），且只在出現簡體專用字時才轉（見
    # translate.postprocess.to_taiwan_traditional）：已是繁體的文字經 s2twp 會被改錯。
    Language(
        "zh-TW",
        "Chinese (Traditional, Taiwan)",
        "繁體中文",
        "zh",
        no_space=True,
        script_conversion="s2twp",
    ),
    Language(
        "zh-CN",
        "Chinese (Simplified)",
        "简体中文",
        "zh",
        no_space=True,
        script_conversion="t2s",
    ),
    Language("ko", "Korean", "한국어", "ko"),
    Language("es", "Spanish", "Español", "es"),
    Language("fr", "French", "Français", "fr"),
    Language("de", "German", "Deutsch", "de"),
    Language("pt", "Portuguese", "Português", "pt"),
    Language("ru", "Russian", "Русский", "ru"),
    Language("it", "Italian", "Italiano", "it"),
    Language("ar", "Arabic", "العربية", "ar", rtl=True),
    Language("hi", "Hindi", "हिन्दी", "hi"),
    Language("id", "Indonesian", "Bahasa Indonesia", "id"),
    Language("vi", "Vietnamese", "Tiếng Việt", "vi"),
    Language("th", "Thai", "ไทย", "th", no_space=True),
    Language("tr", "Turkish", "Türkçe", "tr"),
    Language("pl", "Polish", "Polski", "pl"),
    Language("nl", "Dutch", "Nederlands", "nl"),
    Language("uk", "Ukrainian", "Українська", "uk"),
)

# 品質等級集中在 `_QUALITY_ROWS` 一張表（方便對照研究筆記），這裡再併進每個語言。
LANGUAGES = tuple(replace(lang, asr_quality=_quality(lang.code)) for lang in LANGUAGES)

_BY_CODE: dict[str, Language] = {lang.code: lang for lang in LANGUAGES}

WHISPER_CODES: tuple[str, ...] = tuple(
    dict.fromkeys(lang.whisper_code for lang in LANGUAGES)
)
"""清單涵蓋的 Whisper 語言碼（依清單順序、不重複）；也是自動偵測的候選範圍。"""

# 舊版設定與 BCP 47 常見寫法；鍵為小寫、底線換成連字號後的形式。
_ALIASES: dict[str, str] = {
    "zh-hant": "zh-TW",
    "zh-hant-tw": "zh-TW",
    "zh-hans": "zh-CN",
    "zh-hans-cn": "zh-CN",
}
_LOOKUP: dict[str, str] = {code.lower(): code for code in _BY_CODE} | _ALIASES

# Whisper 層級的名稱：中文不分繁簡。
_WHISPER_NAMES: dict[str, str] = {"zh": "Chinese"}

_MAX_ERROR_CODE_CHARS = 20


def _key(code: object) -> str:
    if not isinstance(code, str):
        raise UnknownLanguageError(
            f"language code must be str, got {type(code).__name__}"
        )
    return code.strip().lower().replace("_", "-")


def language_codes() -> tuple[str, ...]:
    """清單語言碼，依清單順序。"""
    return tuple(_BY_CODE)


def normalize_language(code: str) -> str:
    """把語言碼（不分大小寫、底線視同連字號、接受舊別名）正規化成清單語言碼。

    Raises:
        UnknownLanguageError: 不是字串、不在清單內，或是不分繁簡的 "zh"。
    """
    key = _key(code)
    if key in _LOOKUP:
        return _LOOKUP[key]
    shown = code[:_MAX_ERROR_CODE_CHARS]
    if key == "zh":
        raise UnknownLanguageError(
            "language code 'zh' is ambiguous for subtitles; use 'zh-TW' or 'zh-CN'"
        )
    raise UnknownLanguageError(f"unsupported language code: {shown!r}")


def get_language(code: str) -> Language:
    """依語言碼（含別名）取得清單中的語言。

    Raises:
        UnknownLanguageError: 同 `normalize_language()`。
    """
    return _BY_CODE[normalize_language(code)]


def to_whisper_code(code: str) -> str:
    """清單語言碼（含別名）或清單涵蓋的 Whisper 語言碼 → Whisper 語言碼。

    ASR 回傳的是 Whisper 語言碼（中文為 "zh"），設定用的是清單語言碼；兩者都會出現在
    翻譯請求的原文語言，所以這裡都接受。

    Raises:
        UnknownLanguageError: 不是字串，或兩種語言碼都對不上。
    """
    key = _key(code)
    if key in WHISPER_CODES:
        return key
    if key in _LOOKUP:
        return _BY_CODE[_LOOKUP[key]].whisper_code
    raise UnknownLanguageError(
        f"unsupported language code: {code[:_MAX_ERROR_CODE_CHARS]!r}"
    )


def whisper_language_name(whisper_code: str) -> str:
    """Whisper 語言碼的英文名（"zh" → "Chinese"），用於英文提示詞的原文語言。

    Raises:
        UnknownLanguageError: 不是清單涵蓋的 Whisper 語言碼。
    """
    if whisper_code not in WHISPER_CODES:
        raise UnknownLanguageError(
            f"unsupported whisper language: {str(whisper_code)[:_MAX_ERROR_CODE_CHARS]!r}"
        )
    if whisper_code in _WHISPER_NAMES:
        return _WHISPER_NAMES[whisper_code]
    return next(
        lang.english_name for lang in LANGUAGES if lang.whisper_code == whisper_code
    )


def asr_quality(code: str, model: str) -> AsrQuality | None:
    """`model`（asr registry 的 id）辨識 `code`（清單語言碼，含別名）的品質等級。

    沒有資料的模型回傳 None（呼叫端不提示，而不是猜一個等級）。

    Raises:
        UnknownLanguageError: 語言碼不在清單內。
    """
    return get_language(code).asr_quality.get(model)
