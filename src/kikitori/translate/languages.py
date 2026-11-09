"""翻譯請求的語言碼正規化與驗證。

語言資料一律來自 `kikitori.languages`。原文語言是 ASR 的 Whisper 語言碼（中文為 "zh"，
也接受清單語言碼如 "zh-TW"），譯文語言是清單語言碼（必須分繁簡）。在送出網路請求前
擋掉不支援的組合，避免浪費一次模型呼叫；Whisper 語言相同的組合（例如 zh → zh-TW）
不需要翻譯模型，由 pipeline 只做字形轉換，所以這裡也視為不合法。
"""

from collections.abc import Collection

from kikitori.languages import (
    UnknownLanguageError,
    get_language,
    language_codes,
    to_whisper_code,
)
from kikitori.languages import (
    normalize_language as _catalog_normalize,
)
from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.types import TranslationRequest

ALL_LANGUAGE_CODES: frozenset[str] = frozenset(language_codes())
"""語言清單的全部語言碼；通用模型（不限語言）的預設支援範圍。"""

SAKURA_PAIRS: frozenset[tuple[str, str]] = frozenset({("ja", "zh-TW"), ("ja", "zh-CN")})
"""Sakura 模型支援的（Whisper 原文, 清單譯文）方向。

官方只訓練日翻簡中，繁中由 OpenCC 轉換。預設組（`presets`）與提示詞（`prompts`）共用
這一份：`presets` 匯入 `prompts`，放在兩者都能匯入的這裡才不會循環匯入。
"""

MAX_TEXT_CHARS = 2000
"""一句字幕的長度上限；超過多半是 ASR 失控，送出去只會拖慢翻譯。"""

DEFAULT_MAX_CONTEXT_LINES = 3


def normalize_language(code: str) -> str:
    """把譯文語言碼正規化成清單語言碼（"zh-TW"、"ja"…）。

    不分大小寫，底線視同連字號，接受舊別名（"zh-Hant" 等）。

    Raises:
        InvalidRequestError: 不是字串、不在清單內，或是不分繁簡的 "zh"。
    """
    try:
        return _catalog_normalize(code)
    except UnknownLanguageError as exc:
        raise InvalidRequestError(str(exc)) from None


def normalize_source_language(code: str) -> str:
    """把原文語言碼正規化成 Whisper 語言碼（"zh-TW" → "zh"、"JA" → "ja"）。

    Raises:
        InvalidRequestError: 不是字串，或不是清單涵蓋的語言。
    """
    try:
        return to_whisper_code(code)
    except UnknownLanguageError as exc:
        raise InvalidRequestError(str(exc)) from None


def normalize_pair(
    source_language: str,
    target_language: str,
    languages: Collection[str] = ALL_LANGUAGE_CODES,
    pairs: Collection[tuple[str, str]] | None = None,
) -> tuple[str, str]:
    """驗證並正規化一個翻譯方向。

    Args:
        source_language: 原文語言（Whisper 或清單語言碼）。
        target_language: 譯文語言（清單語言碼）。
        languages: 模型支援的清單語言碼；原文只要有任一個同 Whisper 語言的清單語言
            在內就算支援（例如 "zh" 對 {"zh-TW"}）。
        pairs: 模型只支援特定方向時的（Whisper 原文, 清單譯文）組合；None 表示不限。

    Returns:
        （Whisper 原文語言碼, 清單譯文語言碼）。

    Raises:
        InvalidRequestError: 語言碼不合法、兩者 Whisper 語言相同，或模型不支援。
    """
    source = normalize_source_language(source_language)
    target = normalize_language(target_language)
    if get_language(target).whisper_code == source:
        raise InvalidRequestError(
            f"nothing to translate: {source} -> {target} is the same language"
        )
    source_supported = any(
        get_language(code).whisper_code == source
        for code in languages
        if code in ALL_LANGUAGE_CODES
    )
    if target not in languages or not source_supported:
        raise InvalidRequestError(
            f"unsupported language pair for this model: {source} -> {target}"
        )
    if pairs is not None and (source, target) not in pairs:
        raise InvalidRequestError(
            f"unsupported language pair for this model: {source} -> {target}"
        )
    return source, target


def validate_request(
    request: TranslationRequest,
    languages: Collection[str] = ALL_LANGUAGE_CODES,
    pairs: Collection[tuple[str, str]] | None = None,
    max_context_lines: int = DEFAULT_MAX_CONTEXT_LINES,
) -> TranslationRequest:
    """驗證並正規化請求。

    原文與前文去頭尾空白、略過空白前文、只保留最後 `max_context_lines` 句；
    原文語言換成 Whisper 語言碼（"zh-TW" 視同 "zh"）。

    Args:
        request: 呼叫端給的請求。
        languages: 模型支援的清單語言碼（見 `normalize_pair()`）。
        pairs: 模型只支援特定方向時的組合；None 表示不限。
        max_context_lines: 前文最多保留幾句。

    Returns:
        正規化後的新請求。

    Raises:
        InvalidRequestError: 型別錯誤、原文空白或過長、語言碼或組合不支援。
    """
    if not isinstance(request, TranslationRequest):
        raise InvalidRequestError(
            f"expected TranslationRequest, got {type(request).__name__}"
        )
    if not isinstance(request.text, str):
        raise InvalidRequestError("text must be str")
    text = request.text.strip()
    if not text:
        raise InvalidRequestError("text is empty")
    if len(text) > MAX_TEXT_CHARS:
        raise InvalidRequestError(
            f"text too long: {len(text)} chars (max {MAX_TEXT_CHARS})"
        )

    source, target = normalize_pair(
        request.source_language, request.target_language, languages, pairs
    )

    # 字串本身也是可迭代的，必須明確擋掉，否則會被拆成一個個字當前文。
    if not isinstance(request.context, (tuple, list)):
        raise InvalidRequestError("context must be a tuple of str")
    if not all(isinstance(line, str) for line in request.context):
        raise InvalidRequestError("context must contain only str")
    context = tuple(line.strip() for line in request.context if line.strip())
    context = context[-max_context_lines:] if max_context_lines > 0 else ()
    if not isinstance(request.background, str):
        raise InvalidRequestError("background must be str")

    return TranslationRequest(text, source, target, context, request.background.strip())
