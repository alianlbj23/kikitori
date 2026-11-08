"""前文保險：帶前文的譯文看起來把前文也翻出來時，改不帶前文重翻一次。

位於 translate 模組、包在任何 `Translator` 外面（pipeline 的 factory 用它包兩條路徑的
翻譯器）。為什麼：experiments/translate_eval 中帶前兩句原文讓 ASR 片段的 COMET 顯著提高
（9B +0.015、4B +0.032），所以前文要留；代價是小模型偶爾把前文一起翻出來（4B 在 social
的標籤行上 3–4/311 次）。這種輸出的特徵是長度遠超過原文，或直接抄出前文原句；遇到時
不帶前文重翻一次，比整個關掉前文便宜得多（多數句子不受影響）。
"""

import logging
import unicodedata
from collections.abc import Iterator
from dataclasses import replace

from kikitori.translate.types import TranslationRequest, Translator

logger = logging.getLogger(__name__)

LENGTH_RATIO = 3
"""譯文字數超過原文的這個倍數（且超過 `MIN_SUSPICIOUS_CHARS`）視為異常。"""
LATIN_TARGET_RATIO = 8
"""原文是中日韓、譯文是拼音文字時的倍數：日文一個字常對應好幾個英文字母，3 倍會誤判
正常譯文（translate_eval 的「過長」判斷對英文也用 8 倍）。"""
MIN_SUSPICIOUS_CHARS = 40
"""短句的譯文本來就可能是原文的好幾倍（「草」→「笑死我了」），40 字以下不判斷長度。"""
MIN_ECHO_CHARS = 6
"""前文要至少這麼長才檢查「原句出現在譯文裡」，避免「はい」這種短句碰巧相同。"""

_CJK_SOURCES = frozenset({"ja", "zh", "ko"})
_CJK_TARGETS = frozenset({"ja", "zh-TW", "zh-CN", "ko"})


def _normalize(text: str) -> str:
    return unicodedata.normalize("NFKC", text).strip()


def looks_like_context_leak(translation: str, request: TranslationRequest) -> bool:
    """帶前文的譯文是否像是把前文也翻出來（或抄出來）了。

    兩個條件之一：

    - 長度：譯文字數 > 原文字數 × 3（中日韓原文翻成拼音文字時 × 8），而且 > 40 字。
    - 抄出前文：任一句至少 6 字的前文原句完整出現在譯文裡（NFKC 正規化後比對）。

    沒有前文的請求一律回傳 False（重翻也不會不同）。
    """
    if not request.context:
        return False
    source = request.text.strip()
    ratio = (
        LATIN_TARGET_RATIO
        if request.source_language in _CJK_SOURCES
        and request.target_language not in _CJK_TARGETS
        else LENGTH_RATIO
    )
    length = len(translation.strip())
    if length > MIN_SUSPICIOUS_CHARS and length > ratio * len(source):
        return True
    normalized = _normalize(translation)
    return any(
        len(line) >= MIN_ECHO_CHARS and line in normalized
        for line in (_normalize(c) for c in request.context)
    )


class ContextFallbackTranslator:
    """包住另一個 `Translator`：譯文像是翻出前文時，改不帶前文重翻一次（只重翻一次）。

    `stream()` 在累積的譯文一變得異常（過長，或已完整抄出某句前文）就關掉原本的串流、
    改送不帶前文的請求，字幕不必等錯的譯文說完；異常的那一段不會產出。
    """

    def __init__(self, inner: Translator) -> None:
        self.inner = inner

    def __repr__(self) -> str:
        return f"ContextFallbackTranslator({self.inner!r})"

    def translate(self, request: TranslationRequest) -> str:
        """同 `Translator.translate()`；例外原樣往上拋。"""
        text = self.inner.translate(request)
        if looks_like_context_leak(text, request):
            _log_retry(request, text)
            return self.inner.translate(replace(request, context=()))
        return text

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        """同 `Translator.stream()`：每次產出「到目前為止的完整譯文」。"""
        # 先呼叫內層的 stream()：與被包住的翻譯器一樣，請求驗證在呼叫時就進行。
        parts = self.inner.stream(request)
        if not request.context:
            return parts
        return self._stream(request, parts)

    def _stream(
        self, request: TranslationRequest, parts: Iterator[str]
    ) -> Iterator[str]:
        try:
            for text in parts:
                if looks_like_context_leak(text, request):
                    break
                yield text
            else:
                return
        finally:
            close = getattr(parts, "close", None)
            if close is not None:
                close()
        _log_retry(request, text)
        yield from self.inner.stream(replace(request, context=()))


def _log_retry(request: TranslationRequest, translation: str) -> None:
    # 只記長度：原文與譯文是使用者看的內容，不放進 log。
    logger.info(
        "translation looks like it includes the context (%d chars for %d source "
        "chars); retranslating without context",
        len(translation),
        len(request.text),
    )
