"""把 `lexicon.json` 匯出成引擎用的 `src/kikitori/asr/hallucination_lexicon.py`。

片語表的正式資料是 Python 模組（PyInstaller 會自動收進匯入的模組，資料檔則要另外列出），
但人工審閱與實驗彙整用的是 `lexicon.json`；兩者必須逐字一致（`tests/test_asr_hallucination_lexicon.py`
會檢查），所以改完 JSON 後用這裡重新產生，不要手改 Python 檔。
"""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LEXICON_JSON = Path(__file__).resolve().parent / "lexicon.json"
LEXICON_PY = ROOT / "src" / "kikitori" / "asr" / "hallucination_lexicon.py"


def export(src: Path = LEXICON_JSON, dst: Path = LEXICON_PY) -> int:
    """產生 Python 資料檔，回傳片語總數。"""
    data = json.loads(src.read_text(encoding="utf-8"))
    languages = data["languages"]

    lines = [
        '"""Whisper 在非語音上的已知幻覺片語，依 Whisper 語言碼分表（`asr.filtering` 的資料）。',
        "",
        "位於 asr 模組的資料層：`filtering.DEFAULT_HALLUCINATIONS` 由這份表建立。內容是",
        "`experiments/hallucination_lexicon/lexicon.json`（2026-11-05 實測，方法與限制見該目錄的",
        "README）的逐字複製：small／large-v3-turbo／large-v3 對強制 19 個語言、確定沒有人聲的",
        "音訊（MUSAN 噪音與無人聲音樂、數位靜音、白／粉紅噪音、合成音效）的輸出，人工分成",
        "`hard`（字幕署名、片尾套話，不論辨識語言都濾）與 `soft`（真人也可能單獨說的短句，",
        "只在同語言且 no_speech 偏高時濾）。英文另參考 Barański et al.（ICASSP 2025）。",
        "",
        "為什麼是 Python 資料而不是 JSON 資源：PyInstaller 會自動收進匯入的模組，資料檔則要在",
        "`packaging/engine/build.py` 另外列出，漏列時打包版會靜默地沒有片語表。更新時改",
        "`lexicon.json` 後重新產生這個檔案（保持兩者一致），不要只改其中一個。",
        "",
        "中文片語同時列簡體與繁體（繁體為人工轉寫）；印地語、泰語沒有找到可確定是套話的句子，",
        "全部放 soft。片語是可讀形式，比對前經 `filtering.normalize_for_match()` 正規化。",
        '"""',
        "",
        "from collections.abc import Mapping",
        "from typing import Final",
        "",
        "LEXICON_SOURCE: Final = (",
    ]
    source = data["source"]
    # 來源說明拆成數行字串，避免一行過長
    words = source.split(" ")
    chunk: list[str] = []
    for word in words:
        if len(" ".join([*chunk, word])) > 70 and chunk:
            lines.append(f"    {json.dumps(' '.join(chunk) + ' ', ensure_ascii=False)}")
            chunk = []
        chunk.append(word)
    lines.append(f"    {json.dumps(' '.join(chunk), ensure_ascii=False)}")
    lines += [
        ")",
        '"""片語表的來源與分類原則（英文，與 `lexicon.json` 的 `source` 相同）。"""',
        "",
        f'LEXICON_GENERATED: Final = "{data["generated"]}"',
        "",
        "HALLUCINATION_LEXICON: Final[Mapping[str, Mapping[str, tuple[str, ...]]]] = {",
    ]
    for language, entry in languages.items():
        lines.append(f"    {json.dumps(language)}: {{")
        for kind in ("hard", "soft"):
            phrases = entry.get(kind, [])
            assert len(phrases) == len(set(phrases)), (language, kind)
            if not phrases:
                lines.append(f'        "{kind}": (),')
                continue
            lines.append(f'        "{kind}": (')
            for phrase in phrases:
                lines.append(f"            {json.dumps(phrase, ensure_ascii=False)},")
            lines.append("        ),")
        lines.append("    },")
    lines += [
        "}",
        '"""`{Whisper 語言碼 或 "*": {"hard": (...), "soft": (...)}}`；"*" 為不分語言的署名。"""',
        "",
    ]
    dst.write_text("\n".join(lines), encoding="utf-8", newline="\n")
    return sum(len(e["hard"]) + len(e["soft"]) for e in languages.values())
