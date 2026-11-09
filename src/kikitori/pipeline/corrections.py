"""原文校正：把辨識結果裡固定會寫錯的詞換成正確寫法（v0.3 G4）。

位於 pipeline 的原文處理（`Pipeline._original()`）：在字形轉換之後、穩定前綴與翻譯之前
套用，所以畫面上的原文、送去翻譯的文字與字幕紀錄都是校正後的版本。Whisper 對主播名、
組合名、遊戲名常有固定的錯法（`docs/specs/v0.3-features.md` G4）；術語表只能告訴翻譯模型
怎麼翻，改不了原文。

比對規則與術語表相同：不分大小寫、全形與半形（NFKC）視為同一個寫法。最長的錯誤寫法優先，
一次掃描完成，替換後的文字不會再被其他規則替換（不連鎖）。
"""

import re
import unicodedata
from collections.abc import Iterable
from dataclasses import dataclass

MAX_CORRECTIONS = 200
"""最多幾筆校正（設定檔 `translation.glossary.corrections`）。"""
MAX_CORRECTION_CHARS = 100
"""錯的寫法與正確寫法各自最多幾個字元。"""
SEPARATORS = ("=>", "→")
"""設定頁文字框分隔兩邊的符號；規則本身不能含這些符號。"""


def _key(text: str) -> str:
    return unicodedata.normalize("NFKC", text).casefold()


@dataclass(frozen=True)
class Correction:
    """一筆校正：`wrong` 出現在辨識結果時換成 `right`。

    Raises:
        TypeError: 不是字串。
        ValueError: 去頭尾空白後為空、超過 `MAX_CORRECTION_CHARS`、含控制字元，
            或兩邊完全相同（只差大小寫、全形半形可以：例如統一成 "Apex"）。
    """

    wrong: str
    right: str

    def __post_init__(self) -> None:
        for name in ("wrong", "right"):
            value = getattr(self, name)
            if not isinstance(value, str):
                raise TypeError(f"{name} must be a string, got {type(value).__name__}")
            value = value.strip()
            if not value or len(value) > MAX_CORRECTION_CHARS:
                raise ValueError(f"{name} must be 1-{MAX_CORRECTION_CHARS} characters")
            if any(unicodedata.category(ch) == "Cc" for ch in value):
                raise ValueError(f"{name} must not contain control characters")
            if any(sep in value for sep in SEPARATORS):
                # 設定頁一行一筆「錯 => 對」：含分隔符號的規則寫不回文字框
                raise ValueError(f"{name} must not contain '=>' or '→'")
            object.__setattr__(self, name, value)
        if self.wrong == self.right:
            raise ValueError("wrong must differ from right")


_HALFWIDTH_VOICING = "ﾞﾟ"
"""半形片假名的濁點、半濁點：NFKC 會把它們與前一個字合成一個字（ﾊﾟ → パ）。"""


def _groups(text: str) -> list[tuple[int, int, str]]:
    """把 `text` 切成「一個字＋後面的結合符號」，各自正規化：(起點, 終點, 比對用文字)。

    以組為單位正規化才能把比對結果對回原文的位置（NFKC 會改變長度）。韓文的組合用字母
    （conjoining jamo，U+1100 起）不會合成一組；Whisper 輸出的是預組字，所以不處理。
    """
    groups: list[tuple[int, int, str]] = []
    i = 0
    while i < len(text):
        j = i + 1
        while j < len(text) and (
            unicodedata.combining(text[j]) or text[j] in _HALFWIDTH_VOICING
        ):
            j += 1
        groups.append((i, j, _key(text[i:j])))
        i = j
    return groups


class Corrections:
    """編譯好的校正表；`apply()` 可以在多個執行緒同時呼叫（不改狀態）。"""

    def __init__(self, entries: Iterable[Correction]) -> None:
        """
        Raises:
            ValueError: 兩筆的錯誤寫法在比對規則下相同（設定檔驗證已擋；直接建構時也不默默取第一筆）。
        """
        self._targets: dict[str, str] = {}
        for entry in entries:
            key = _key(entry.wrong)
            if key in self._targets:
                raise ValueError(f"duplicate correction for {entry.wrong!r}")
            self._targets[key] = entry.right
        keys = sorted(self._targets, key=len, reverse=True)
        self._pattern = (
            re.compile("|".join(re.escape(k) for k in keys)) if keys else None
        )

    def __bool__(self) -> bool:
        return self._pattern is not None

    def apply(self, text: str) -> str:
        """`text` 裡每個錯的寫法換成正確寫法（一次掃描、最長優先、不連鎖）。

        在正規化後的文字上比對，只接受剛好落在字（含結合符號）邊界上的結果，再換掉原文
        對應的那一段；其他部分保持原樣（不把整句轉成 NFKC）。
        """
        if self._pattern is None or not text:
            return text
        groups = _groups(text)
        normalized = "".join(g[2] for g in groups)
        # 正規化文字的位置 → 原文位置（只有組的邊界才有對應）
        boundary: dict[int, int] = {}
        offset = 0
        for start, _end, piece in groups:
            boundary[offset] = start
            offset += len(piece)
        boundary[offset] = len(text)
        out: list[str] = []
        copied = 0
        for match in self._pattern.finditer(normalized):
            begin = boundary.get(match.start())
            end = boundary.get(match.end())
            if begin is None or end is None or begin < copied:
                continue
            out.append(text[copied:begin])
            out.append(self._targets[match.group(0)])
            copied = end
        out.append(text[copied:])
        return "".join(out)
