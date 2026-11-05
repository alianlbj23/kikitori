"""把模型的原始輸出整理成可以直接上字幕的譯文。

模型偶爾會輸出思考過程、加引號或「譯文：」標籤，或輸出與譯文語言不符的字形（尤其是
Sakura 一律輸出簡體）；
這些都在這裡統一處理，讓 `translate()` 與 `stream()` 得到一致的結果。
"""

import functools
import re
import threading

import opencc

from kikitori.languages import UnknownLanguageError, get_language

_THINK_BLOCK = re.compile(r"<(think|thinking)>.*?</\1>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN = re.compile(r"<(?:think|thinking)>", re.IGNORECASE)
_THINK_CLOSE = re.compile(r"</(?:think|thinking)>", re.IGNORECASE)
# 串流時標籤可能被切在兩個片段之間（例如先收到 "<thi"），先藏起來等下一段。
_PARTIAL_OPEN_AT_END = re.compile(
    r"<(?:t|th|thi|thin|think|thinki|thinkin|thinking)$", re.IGNORECASE
)

# 聊天範本的特殊 token（例如 HY-MT 的 <|eos|>、<|extra_0|>）偶爾會以文字形式漏出來，
# 串流到一半時只看得到 "<|" 這種碎片。
_SPECIAL_TOKEN = re.compile(r"<\|[A-Za-z0-9_]*\|>")
_PARTIAL_SPECIAL_AT_END = re.compile(r"<\|[A-Za-z0-9_]*\|?$")

# 只列「標籤 + 冒號」的形式；正常字幕以這些詞加冒號開頭的情況極少。
_LEADING_LABEL = re.compile(
    r"^(?:譯文|译文|翻譯|翻译|中文翻譯|中文翻译|翻訳|訳文|번역|translation|traducción"
    r"|traduction|übersetzung|tradução|traduzione|перевод)\s*[:：]\s*",
    re.IGNORECASE,
)
_QUOTE_PAIRS = {'"': '"', "'": "'", "“": "”", "‘": "’", "「": "」", "『": "』"}

# 簡體專用字 = OpenCC s2tw 會改掉、而且不在 Big5 字集裡的字。Big5 是台灣的傳統字集，
# 用它排除「才、吃、伙、台、里、后」這類兩岸都用、OpenCC 卻會改寫的字。
# 下列字雖在 Big5 裡，但在台灣文字中幾乎只會以簡體身分出現。
_SIMPLIFIED_IN_BIG5 = frozenset("么于网扰")

_converters: dict[str, opencc.OpenCC] = {}
# OpenCC 的 Python 綁定沒有說明是否執行緒安全；轉換很快，直接加鎖最保險。
_converter_lock = threading.Lock()


def strip_thinking(text: str) -> str:
    """去掉思考內容（`<think>…</think>`、`<thinking>…</thinking>`）。

    - 完整區塊整段移除。
    - 只有開頭標籤（還在思考中）：從標籤起全部丟掉。
    - 只有結束標籤（聊天範本已預先放入開頭標籤）：丟掉結束標籤之前的內容。
    - 結尾是不完整的開頭標籤（例如 "<thi"）：先藏起來。
    """
    text = _THINK_BLOCK.sub("", text)
    opening = _THINK_OPEN.search(text)
    if opening:
        text = text[: opening.start()]
    closing = None
    for closing in _THINK_CLOSE.finditer(text):
        pass
    if closing:
        text = text[closing.end() :]
    return _PARTIAL_OPEN_AT_END.sub("", text)


def clean_subtitle(text: str) -> str:
    """去掉漏出的特殊 token、頭尾空白、開頭的「譯文：」類標籤，以及包住整句的成對引號。"""
    text = _PARTIAL_SPECIAL_AT_END.sub("", _SPECIAL_TOKEN.sub("", text))
    text = _LEADING_LABEL.sub("", text.strip()).strip()
    if len(text) >= 2:
        opening, closing = text[0], text[-1]
        inner = text[1:-1]
        # 只有整句被同一對引號包住才去掉；「走吧」他說「快點」這種保留。
        if (
            _QUOTE_PAIRS.get(opening) == closing
            and opening not in inner
            and closing not in inner
        ):
            text = inner.strip()
    return text


def _convert(config: str, text: str) -> str:
    with _converter_lock:
        converter = _converters.get(config)
        if converter is None:
            converter = _converters[config] = opencc.OpenCC(config)
        return converter.convert(text)


@functools.lru_cache(maxsize=4096)
def _is_simplified_only(char: str) -> bool:
    if char in _SIMPLIFIED_IN_BIG5:
        return True
    if not ("㐀" <= char <= "鿿"):  # 只看 CJK 漢字；假名、拉丁字母不算
        return False
    try:
        char.encode("big5")
    except UnicodeEncodeError:
        return _convert("s2tw.json", char) != char
    return False


def simplified_characters(text: str) -> list[str]:
    """依出現順序回傳文字中的簡體專用字（台灣正體不會用到的字）。"""
    return [c for c in text if _is_simplified_only(c)]


def to_taiwan_traditional(text: str) -> str:
    """有簡體專用字時，以 OpenCC `s2twp` 轉成台灣正體（含台灣慣用詞）；否則原樣回傳。

    已是繁體的文字不轉：實測 s2twp 會把正確的「傢伙」改成「傢夥」、「台」改成「臺」。
    """
    if not simplified_characters(text):
        return text
    return _convert("s2twp.json", text)


def to_script(text: str, language: str) -> str:
    """依語言清單的 `script_conversion` 轉換字形：zh-TW → 台灣正體、zh-CN → 簡體。

    沒有轉換規則或不在清單內的語言原樣回傳。zh-TW 沿用選擇性轉換
    （`to_taiwan_traditional`），簡體則整句 `t2s`：已是簡體的文字轉了也不變。
    """
    try:
        conversion = get_language(language).script_conversion
    except UnknownLanguageError:
        return text
    if conversion is None:
        return text
    if conversion == "s2twp":
        return to_taiwan_traditional(text)
    return _convert(f"{conversion}.json", text)


def finalize_translation(raw: str, target_language: str, convert: bool) -> str:
    """把模型原始輸出整理成字幕譯文。

    Args:
        raw: 模型輸出（可以是串流到一半的累積內容）。
        target_language: 正規化後的譯文語言碼。
        convert: 是否依譯文語言做字形轉換（見 `to_script()`）。
    """
    text = clean_subtitle(strip_thinking(raw))
    if convert:
        text = to_script(text, target_language)
    return text
