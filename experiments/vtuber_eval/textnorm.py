"""CER 用的文字正規化（有版本號），與 kotoba-whisper 公開 CER 的算法對齊。

角色：vtuber_eval 評測的計分基礎（暫時性實驗，不屬於正式程式碼）。只用標準庫，
讓隔離環境的 `prepare.py` 與專案環境的 `analyze.py` 都能載入同一份規則。

- `normalize_basic()`：逐字重現 OpenAI Whisper `BasicTextNormalizer()`
  （`whisper/normalizers/basic.py`，`remove_diacritics=False`、`split_letters=False`）
  後再去掉所有空白——kotoba-whisper README 的評測程式就是
  `normalizer(text).replace(" ", "")`，所以數字能和它公開的 CER 對照。
- `normalize_no_fillers()`：同上，但先依固定規則刪掉日文填充詞與網路笑聲
  （規則見 `FILLER_RULES_VERSION` 下方說明）。字幕常把「えー」「あの」省略，
  模型卻照實寫出，這個變體把這類「不是錯誤的差異」排除。
"""

import re
import unicodedata

NORMALIZER_VERSION = "whisper-basic-nospace-v1"
FILLER_RULES_VERSION = "ja-fillers-v1"

# Whisper BasicTextNormalizer 的兩條括號規則（只認 ASCII 括號，且在 NFKC 之前執行，
# 所以全形「（笑）」不會整段刪掉，只會刪掉括號本身；照原樣重現，不自行「修正」）
_BRACKETS = re.compile(r"[<\[][^>\]]*[>\]]")
_PARENS = re.compile(r"\(([^)]+?)\)")
_SPACES = re.compile(r"\s+")

# ja-fillers-v1：在 BasicTextNormalizer 之後（標點已變成空白）逐「片段」套用。
# 1. 整個片段都是填充詞（可連續多個）就刪掉。「あの」「その」「まあ」只在獨立成段時
#    才算填充詞，因為黏在後面的「あの人」「その時」是指示詞、不能刪。
#    應答詞「うん」「ええ」「はい」是內容（回應），不算填充詞。
# 2. 片段開頭的拉長音填充詞（含「ー」或促音、不會是實詞開頭）也刪掉：
#    えー／えーと／えっと／えーっと／ええと／あのー／うーん／んー／あー。
# 3. 片段結尾的 w 串（NFKC 後的「ｗ」）是網路笑聲，刪掉；獨立的「草」「笑」也刪。
_FILLER_WORD = (
    r"(?:え[ーっ]+と?|ええと|えと|あ[ー]+|あのー*|そのー*|うー+ん|ん[ー]+|"
    r"まあ|まぁ|なんか|w+|草|笑)"
)
_WHOLE_FILLER = re.compile(rf"^(?:{_FILLER_WORD})+$")
_LEADING_FILLER = re.compile(
    r"^(?:え[ーっ]+と|ええと|えー+|あのー+|うーん|んー+|あー+)+"
)
_TRAILING_LAUGH = re.compile(r"w+$")


def _whisper_basic(text: str) -> str:
    """Whisper `BasicTextNormalizer()` 的逐字重現（輸出保留單一空白分隔）。"""
    s = text.lower()
    s = _BRACKETS.sub("", s)
    s = _PARENS.sub("", s)
    s = "".join(
        " " if unicodedata.category(c)[0] in "MSP" else c
        for c in unicodedata.normalize("NFKC", s)
    )
    s = s.lower()
    return _SPACES.sub(" ", s)


def normalize_basic(text: str) -> str:
    """`NORMALIZER_VERSION`：BasicTextNormalizer 後去掉所有空白（kotoba 的算法）。"""
    return _whisper_basic(text).replace(" ", "")


def _strip_fillers(chunk: str) -> str:
    if _WHOLE_FILLER.match(chunk):
        return ""
    chunk = _LEADING_FILLER.sub("", chunk)
    return _TRAILING_LAUGH.sub("", chunk) if not chunk.isascii() else chunk


def normalize_no_fillers(text: str) -> str:
    """`FILLER_RULES_VERSION`：先套 BasicTextNormalizer，再逐片段刪填充詞，最後去空白。"""
    chunks = _whisper_basic(text).split()
    return "".join(_strip_fillers(c) for c in chunks)


def edit_distance(a: str, b: str) -> int:
    """逐字元的 Levenshtein 距離（替換、插入、刪除各算 1）。"""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]
