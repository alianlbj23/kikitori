"""語言碼正規化與翻譯請求驗證。

ASR 回傳 ISO 639-1（中文為 "zh"），設定與 UI 用 "zh-TW"；這裡統一成內部語言碼，
並在送出網路請求前擋掉不支援的組合，避免浪費一次模型呼叫。
"""

from collections.abc import Collection

from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.types import TranslationRequest

# 對應架構規格的兩種模式：media（ja/en → zh-TW）與 interpret（另加 zh → ja/en）。
SUPPORTED_PAIRS: frozenset[tuple[str, str]] = frozenset(
    {("ja", "zh-TW"), ("en", "zh-TW"), ("zh", "ja"), ("zh", "en")}
)

MAX_TEXT_CHARS = 2000
"""一句字幕的長度上限；超過多半是 ASR 失控，送出去只會拖慢翻譯。"""

DEFAULT_MAX_CONTEXT_LINES = 3

_ALIASES: dict[str, str] = {
    "ja": "ja",
    "en": "en",
    "zh": "zh",
    "zh-tw": "zh-TW",
    "zh-hant": "zh-TW",
}


def normalize_language(code: str) -> str:
    """把語言碼正規化成內部用的 "ja"、"en"、"zh"、"zh-TW"。

    不分大小寫，底線視同連字號。

    Raises:
        InvalidRequestError: 不是字串或不是支援的語言碼。
    """
    if not isinstance(code, str):
        raise InvalidRequestError(
            f"language code must be str, got {type(code).__name__}"
        )
    key = code.strip().lower().replace("_", "-")
    try:
        return _ALIASES[key]
    except KeyError:
        raise InvalidRequestError(f"unsupported language code: {code[:20]!r}") from None


def validate_request(
    request: TranslationRequest,
    pairs: Collection[tuple[str, str]] = SUPPORTED_PAIRS,
    max_context_lines: int = DEFAULT_MAX_CONTEXT_LINES,
) -> TranslationRequest:
    """驗證並正規化請求。

    原文與前文去頭尾空白、略過空白前文、只保留最後 `max_context_lines` 句；
    原文為 "zh-TW" 時視同 "zh"。

    Args:
        request: 呼叫端給的請求。
        pairs: 允許的（原文, 譯文）組合，依模型而定。
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

    source = normalize_language(request.source_language)
    if source == "zh-TW":
        source = "zh"
    target = normalize_language(request.target_language)
    if (source, target) not in pairs:
        raise InvalidRequestError(f"unsupported language pair: {source} -> {target}")

    # 字串本身也是可迭代的，必須明確擋掉，否則會被拆成一個個字當前文。
    if not isinstance(request.context, (tuple, list)):
        raise InvalidRequestError("context must be a tuple of str")
    if not all(isinstance(line, str) for line in request.context):
        raise InvalidRequestError("context must contain only str")
    context = tuple(line.strip() for line in request.context if line.strip())
    context = context[-max_context_lines:] if max_context_lines > 0 else ()

    return TranslationRequest(text, source, target, context)
