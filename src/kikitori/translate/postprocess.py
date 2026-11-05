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
# 也會是沒有收尾的碎片（translate_eval：HY-MT 1.8B 的 <|/code>、<|extra_0>、
# <|begin_quote>、<| recreation |）。串流到一半時只看得到 "<|" 這種開頭。
# 「<|」在一般字幕裡不會出現，所以只要看到它就當成特殊 token 的開頭：碎片本身到第一個
# 「>」、換行、下一個「<」或文字結尾為止（最多 40 字）。
_SPECIAL_FRAGMENT = re.compile(r"<\|[^<>\n]{0,40}>?")

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


def strip_special_tokens(text: str) -> str:
    """去掉漏出的特殊 token（完整的 `<|…|>` 或沒有收尾的碎片）。

    開頭的 token（例如 `<|startoftext|>`）只去掉 token 本身。其他位置的 token 連同
    之後的文字一起丟掉：模型已經越過回合結尾，translate_eval 的錯誤清單裡 token 之後
    幾乎都是把同一句再輸出一次（`…嗎？<|begin_quote>…嗎？`）或憑空續寫的段落。
    """
    while True:
        stripped = text.lstrip()
        match = _SPECIAL_FRAGMENT.match(stripped)
        if match is None:
            break
        text = stripped[match.end() :]
    index = text.find("<|")
    return text if index < 0 else text[:index]


def clean_subtitle(text: str) -> str:
    """去掉漏出的特殊 token、頭尾空白、開頭的「譯文：」類標籤，以及包住整句的成對引號。"""
    text = strip_special_tokens(text)
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


def _is_han(char: str) -> bool:
    return "㐀" <= char <= "鿿"


@functools.lru_cache(maxsize=4096)
def _is_traditional_only(char: str) -> bool:
    """繁體專用字：OpenCC `t2s` 會改掉的漢字（例如「這」「們」「傢」）。"""
    return _is_han(char) and _convert("t2s.json", char) != char


@functools.lru_cache(maxsize=4096)
def _is_simplified_only(char: str) -> bool:
    if char in _SIMPLIFIED_IN_BIG5:
        return True
    if not _is_han(char):  # 只看 CJK 漢字；假名、拉丁字母不算
        return False
    try:
        char.encode("big5")
    except UnicodeEncodeError:
        return _convert("s2tw.json", char) != char
    return False


def simplified_characters(text: str) -> list[str]:
    """依出現順序回傳文字中的簡體專用字（台灣正體不會用到的字）。"""
    return [c for c in text if _is_simplified_only(c)]


# 片段轉換後要還原的字：這些字兩岸都用，OpenCC 卻會改寫（s2twp 把「台」改成「臺」、
# 「傢伙」改成「傢夥」，s2t 把「吃」改成「喫」）；原文本來就是這個字時保留原字。
_KEEP_ORIGINAL = {"台": "臺", "伙": "夥", "吃": "喫"}


def _simplified_windows(text: str) -> list[tuple[int, int]]:
    """要轉換的片段：每個簡體專用字，加上兩側各一個相鄰漢字（給 OpenCC 詞組比對用，
    例如「后来」「干净」「头发」），重疊或相鄰的片段合併。"""
    windows: list[tuple[int, int]] = []
    for i, char in enumerate(text):
        if not _is_simplified_only(char):
            continue
        start = i - 1 if i > 0 and _is_han(text[i - 1]) else i
        end = i + 2 if i + 1 < len(text) and _is_han(text[i + 1]) else i + 1
        if windows and start <= windows[-1][1]:
            windows[-1] = (windows[-1][0], max(windows[-1][1], end))
        else:
            windows.append((start, end))
    return windows


def _convert_window(window: str) -> str:
    # 先把片段裡的繁體鄰字轉回簡體，OpenCC 的詞組才比對得到（「頭发」→「头发」→「頭髮」；
    # 直接 s2twp 會得到「頭發」）。
    converted = _convert("s2twp.json", _convert("t2s.json", window))
    for original, wrong in _KEEP_ORIGINAL.items():
        if original in window and wrong not in window:
            converted = converted.replace(wrong, original)
    return converted


def to_taiwan_traditional(text: str) -> str:
    """有簡體專用字時轉成台灣正體（OpenCC `s2twp`，含台灣慣用詞）；否則原樣回傳。

    - 已是繁體的文字不轉：實測 s2twp 會把正確的「傢伙」改成「傢夥」、「台」改成「臺」。
    - 整句沒有任何繁體專用字（模型整句輸出簡體，例如 Sakura）：整句 s2twp。
    - 繁體為主、只漏出幾個簡體字（translate_eval：HY-MT 7B 的 zh-TW 輸出有 4–14% 是這種
      句子）：只轉簡體字附近的片段（`_simplified_windows()`），句中其他字維持原樣，
      避免整句轉換時連帶產生「臺」「傢夥」「好喫」。
    """
    if not simplified_characters(text):
        return text
    if not any(_is_traditional_only(c) for c in text):
        return _convert("s2twp.json", text)
    parts: list[str] = []
    last = 0
    for start, end in _simplified_windows(text):
        parts.append(text[last:start])
        parts.append(_convert_window(text[start:end]))
        last = end
    parts.append(text[last:])
    return "".join(parts)


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
