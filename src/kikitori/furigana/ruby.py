"""振假名的對齊：把形態素的讀音分配到原文中的漢字上（純函式，不依賴 Sudachi）。

位於 furigana 模組的最底層：`analyzer.py` 把 Sudachi 的形態素轉成 `Token` 後交給
`ruby_for_tokens()`，得到 `subtitle` 事件的 `ruby` 欄位。規則見 `docs/specs/furigana.md`：

- 只標含漢字的片段，讀音一律平假名；送り仮名不重複標（「食べる」只在「食」上標「た」）。
- 詞中間有假名時逐段對齊（「取り扱い」→ 取＝と、扱＝あつか）；對齊不唯一或對不上時，去掉
  頭尾相同的假名後整段標；連整段都無法確定（讀音不是假名、詞裡有數字或符號）就不標。
- 位置以 code point 計（Python 的字串索引），與 UI 的 `stable_chars` 相同單位。
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

RubySpan = tuple[int, int, str]
"""`(start, end, reading)`：原文 `[start, end)`（code point）上方標 `reading`（平假名）。"""

# 々（繰り返し）、〆、〇 與當作量詞用的ヶ（「一ヶ月」）都跟著漢字讀，算成漢字
_KANJI_LIKE = frozenset("々〆〇ヶ")
_KATAKANA_START = 0x30A1  # ァ
_KATAKANA_END = 0x30F6  # ヶ
_KANA_OFFSET = 0x60  # カタカナ → ひらがな
_LONG_VOWEL = "ー"
MAX_READING_CHARS = 64
"""一個詞的讀音上限（與 Rust／TS 的驗證相同）：更長的不標，免得整句的 `ruby` 被丟掉。"""
MAX_ALIGN_RUNS = 16
"""漢字與假名交錯超過這麼多段的詞不逐段對齊（正規表示式回溯會變慢），直接整詞標。"""


def is_kanji(ch: str) -> bool:
    """`ch`（單一字元）是否算漢字：CJK 統合漢字（含擴充 A–H、相容漢字）與々〆〇ヶ。"""
    if ch in _KANJI_LIKE:
        return True
    code = ord(ch)
    return (
        0x4E00 <= code <= 0x9FFF
        or 0x3400 <= code <= 0x4DBF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x323AF
    )


def has_kanji(text: str) -> bool:
    """`text` 是否含漢字。"""
    return any(is_kanji(ch) for ch in text)


def _is_hiragana(ch: str) -> bool:
    return "ぁ" <= ch <= "ゖ" or ch == _LONG_VOWEL


def _is_kana(ch: str) -> bool:
    return _is_hiragana(ch) or "ァ" <= ch <= "ヺ" or ch == "ー"


def to_hiragana(text: str) -> str:
    """片假名轉平假名（ヴ→ゔ、ヵ→ゕ；長音「ー」與其他字元不變）。"""
    return "".join(
        chr(ord(ch) - _KANA_OFFSET)
        if _KATAKANA_START <= ord(ch) <= _KATAKANA_END and ch != "ヶ"
        else ch
        for ch in text
    )


def _runs(surface: str) -> list[tuple[bool, int, int]] | None:
    """把詞切成漢字段與假名段 `(is_kanji, start, end)`；有其他字元（數字、符號）時 None。"""
    runs: list[tuple[bool, int, int]] = []
    for i, ch in enumerate(surface):
        if is_kanji(ch):
            kind = True
        elif _is_kana(ch):
            kind = False
        else:
            return None
        if runs and runs[-1][0] == kind:
            runs[-1] = (kind, runs[-1][1], i + 1)
        else:
            runs.append((kind, i, i + 1))
    return runs


def _match_runs(
    surface: str, runs: list[tuple[bool, int, int]], reading: str, lazy: bool
) -> list[str] | None:
    """以假名段為錨點，把讀音切給每個漢字段；對不上時 None。"""
    group = "(.+?)" if lazy else "(.+)"
    pattern = "".join(
        group if kanji else re.escape(to_hiragana(surface[start:end]))
        for kanji, start, end in runs
    )
    match = re.fullmatch(pattern, reading)
    return None if match is None else list(match.groups())


def _whole_word(surface: str, reading: str) -> list[RubySpan]:
    """去掉頭尾相同的假名，剩下的整段標上剩下的讀音；剩下的部分沒有漢字就不標。"""
    folded = to_hiragana(surface)
    start = 0
    while (
        start < len(folded)
        and start < len(reading)
        and not is_kanji(surface[start])
        and folded[start] == reading[start]
    ):
        start += 1
    end, tail = len(surface), len(reading)
    while (
        end > start
        and tail > start
        and not is_kanji(surface[end - 1])
        and folded[end - 1] == reading[tail - 1]
    ):
        end -= 1
        tail -= 1
    core_reading = reading[start:tail]
    if not core_reading or not has_kanji(surface[start:end]):
        return []
    return [(start, end, core_reading)]


def align(surface: str, reading: str) -> list[RubySpan]:
    """一個詞的振假名，位置相對於 `surface`。

    Args:
        surface: 原文中的詞。
        reading: 詞的讀音（片假名或平假名，例如 Sudachi 的 `reading_form()`）。

    Returns:
        依位置排序、不重疊的 `(start, end, 平假名讀音)`；不含漢字或無法對齊時為空。
    """
    if not has_kanji(surface):
        return []
    folded = to_hiragana(reading)
    if (
        not folded
        or len(folded) > MAX_READING_CHARS
        or not all(_is_hiragana(ch) for ch in folded)
    ):
        # 讀音不是假名（未登錄詞時 Sudachi 可能給空字串或原文）：不知道怎麼念就不標
        return []
    runs = _runs(surface)
    if runs is None:
        return []
    if len(runs) > MAX_ALIGN_RUNS:
        return _whole_word(surface, folded)
    lazy = _match_runs(surface, runs, folded, lazy=True)
    # 兩種切法結果不同表示假名錨點在讀音中出現不只一處，逐段對齊不唯一：整詞標，不猜
    if lazy is not None and lazy == _match_runs(surface, runs, folded, lazy=False):
        kanji_runs = [(start, end) for kanji, start, end in runs if kanji]
        return [
            (start, end, text)
            for (start, end), text in zip(kanji_runs, lazy, strict=True)
        ]
    return _whole_word(surface, folded)


@dataclass(frozen=True)
class Token:
    """形態素分析的一個詞。

    Attributes:
        surface: 原文中的詞（依序出現在原文裡）。
        reading: 讀音（片假名或平假名）。
        parts: 更細的切分（Sudachi 的 A 單位）；讀音接起來與整詞相同時才用來逐詞標，
            否則（例如連濁「日本橋」＝にほんばし，拆開是にほん＋はし）用整詞的讀音。
    """

    surface: str
    reading: str
    parts: tuple["Token", ...] = ()


def _token_spans(token: Token) -> list[RubySpan]:
    parts = token.parts
    if (
        len(parts) > 1
        and "".join(p.surface for p in parts) == token.surface
        and to_hiragana("".join(p.reading for p in parts)) == to_hiragana(token.reading)
    ):
        spans: list[RubySpan] = []
        offset = 0
        for part in parts:
            spans += [
                (offset + s, offset + e, r)
                for s, e, r in align(part.surface, part.reading)
            ]
            offset += len(part.surface)
        return spans
    return align(token.surface, token.reading)


def ruby_for_tokens(text: str, tokens: Iterable[Token]) -> list[RubySpan]:
    """整句的振假名，位置相對於 `text`（code point）。

    詞依序在 `text` 中尋找（從上一個詞的結尾開始）；找不到的詞略過，不影響其他詞。

    Args:
        text: 原文（`subtitle` 事件的 `original`）。
        tokens: 依出現順序的詞。

    Returns:
        依位置排序、不重疊的 `(start, end, 平假名讀音)`。
    """
    spans: list[RubySpan] = []
    position = 0
    for token in tokens:
        if not token.surface:
            continue
        start = text.find(token.surface, position)
        if start < 0:
            continue
        spans += [(start + s, start + e, r) for s, e, r in _token_spans(token)]
        position = start + len(token.surface)
    return spans
