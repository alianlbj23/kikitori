"""兩種模式的語言規則：辨識出的語言 → 譯文語言、要不要翻譯。

位於 pipeline 的決策層，即時與完整路徑共用，確保同一句兩條路徑的譯文語言一致。
規則照架構規格：media 一律譯成 zh-TW；interpret 偵測到中文時譯成對方語言，
否則譯成 zh-TW。
"""

from dataclasses import dataclass
from typing import Literal

from kikitori.pipeline.types import Mode
from kikitori.translate import SUPPORTED_PAIRS

RouteAction = Literal["translate", "copy", "unsupported"]


@dataclass(frozen=True)
class Route:
    """一句話的翻譯方式。

    Attributes:
        source: 正規化後的原文語言（"zh"、"ja"、"en"…）。
        target: 譯文語言（"zh-TW"、"ja"、"en"）。
        action: "translate" 要翻譯；"copy" 原文已是譯文語言（譯文 = 原文）；
            "unsupported" 翻譯模組不支援這個組合（譯文留空）。
    """

    source: str
    target: str
    action: RouteAction


def base_language(code: str) -> str:
    """ASR 語言碼的主語言部分，小寫：`"zh-TW"` → `"zh"`、`"JA"` → `"ja"`。"""
    return code.strip().lower().replace("_", "-").split("-", 1)[0]


def route(
    mode: Mode, detected: str, target_language: str, partner_language: str
) -> Route:
    """依模式與 ASR 偵測的語言決定譯文語言與翻譯方式。

    Args:
        mode: "media" 或 "interpret"。
        detected: ASR 回傳的語言碼（ISO 639-1）。
        target_language: 使用者的語言（目前為 "zh-TW"）。
        partner_language: interpret 模式的對方語言（"ja" 或 "en"）。
    """
    source = base_language(detected)
    if mode == "interpret" and source == "zh":
        target = partner_language
    else:
        target = target_language
    if source == base_language(target):
        return Route(source, target, "copy")
    if (source, target) in SUPPORTED_PAIRS:
        return Route(source, target, "translate")
    return Route(source, target, "unsupported")
