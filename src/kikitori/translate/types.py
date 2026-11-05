"""翻譯模組的對外合約：pipeline 只透過這裡的型別使用翻譯器。

定義照抄 `docs/specs/app-architecture.md` 的 translate 一節；要改合約必須先改
架構規格。驗證放在實作端（`languages.validate_request`），因為支援哪些語言
組合取決於模型。
"""

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class TranslationRequest:
    """一句要翻譯的原文。

    Attributes:
        text: 原文（一句字幕）。
        source_language: 原文語言碼，ISO 639-1（例如 "ja"）；中文為 "zh"。
        target_language: 譯文語言碼，預設 "zh-TW"。
        context: 前幾句原文（由舊到新），只供參考、不翻譯，提升連貫性。
    """

    text: str
    source_language: str
    target_language: str = "zh-TW"
    context: tuple[str, ...] = ()


class Translator(Protocol):
    """把一句原文翻成字幕用的譯文。"""

    def translate(self, request: TranslationRequest) -> str:
        """一次回傳完整譯文。"""
        ...

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        """逐段回傳累積中的譯文：每次產出都是「到目前為止的完整譯文」。"""
        ...
