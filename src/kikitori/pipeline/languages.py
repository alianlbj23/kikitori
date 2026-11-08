"""兩種模式的語言規則：辨識出的語言 → 譯文語言、要不要翻譯、原文怎麼顯示。

位於 pipeline 的決策層，即時與完整路徑共用，確保同一句兩條路徑的譯文語言一致。
規則見 `docs/specs/languages-i18n.md` 的「翻譯路由」：media 一律譯成字幕語言；
interpret 偵測到「我的語言」時譯成對方語言，否則譯成我的語言；Whisper 語言相同時
不翻譯、只做字形轉換；關閉翻譯時一律不翻譯。語言資料來自 `kikitori.languages`。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from kikitori.languages import WHISPER_CODES, UnknownLanguageError, get_language
from kikitori.pipeline.types import Mode
from kikitori.translate.postprocess import to_script

RouteAction = Literal["translate", "copy", "unsupported", "off"]


@dataclass(frozen=True)
class Route:
    """一句話的翻譯方式。

    Attributes:
        source: 正規化後的原文語言（Whisper 語言碼："zh"、"ja"…）。
        target: 譯文語言（語言清單的語言碼："zh-TW"、"ko"…）。
        action: "translate" 要翻譯；"copy" 原文與譯文是同一個 Whisper 語言（譯文 =
            原文轉成譯文語言的字形）；"unsupported" 原文語言不在語言清單內（譯文留空）；
            "off" 使用者關閉了翻譯（譯文留空）。
    """

    source: str
    target: str
    action: RouteAction


def base_language(code: str) -> str:
    """ASR 語言碼的主語言部分，小寫：`"zh-TW"` → `"zh"`、`"JA"` → `"ja"`。"""
    return code.strip().lower().replace("_", "-").split("-", 1)[0]


def route(
    mode: Mode,
    detected: str,
    target_language: str,
    partner_language: str,
    translation_enabled: bool = True,
) -> Route:
    """依模式與 ASR 偵測的語言決定譯文語言與翻譯方式。

    Args:
        mode: "media" 或 "interpret"。
        detected: ASR 回傳的語言碼（Whisper 語言碼）。
        target_language: 字幕語言；口譯時是「我的語言」（語言清單的語言碼）。
        partner_language: interpret 模式的對方語言（語言清單的語言碼）。
        translation_enabled: False 時一律不翻譯（"off"）。

    Raises:
        UnknownLanguageError: `target_language` 不在語言清單內。
    """
    source = base_language(detected)
    if mode == "interpret" and source == get_language(target_language).whisper_code:
        target = partner_language
    else:
        target = target_language
    if not translation_enabled:
        return Route(source, target, "off")
    if source not in WHISPER_CODES:
        return Route(source, target, "unsupported")
    if source == get_language(target).whisper_code:
        return Route(source, target, "copy")
    return Route(source, target, "translate")


def display_original(text: str, detected: str, languages: str | Sequence[str]) -> str:
    """原文的顯示文字：轉成設定中同一個 Whisper 語言的字形（繁／簡）。

    Whisper 的中文辨識結果通常是簡體；口譯時這是使用者自己說的話，聽中文影片時它也可能
    直接當譯文（"copy"）。依序找 `languages` 中第一個與辨識語言相同的語言套用它的轉換；
    只有中文有轉換規則：日文的新字體（気、駅）不是簡體，不能動。

    Args:
        text: 辨識出的原文。
        detected: ASR 回傳的語言碼。
        languages: 設定中的語言（語言清單的語言碼），越前面越優先；不認得的略過。
    """
    if isinstance(languages, str):
        languages = (languages,)
    source = base_language(detected)
    for code in languages:
        try:
            lang = get_language(code)
        except UnknownLanguageError:
            continue
        if lang.whisper_code == source:
            return to_script(text, lang.code)
    return text


def copy_translation(text: str, target_language: str) -> str:
    """ "copy" 路由的譯文：原文轉成譯文語言的字形（例如 zh → zh-CN 轉簡體）。"""
    return to_script(text, target_language)
