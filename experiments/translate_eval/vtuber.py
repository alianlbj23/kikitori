"""VTuber 風格日文句（沒有參考譯文）：把前幾名設定的譯文並排，供人工檢查。

句子沿用 `experiments/translate_benchmark/lines.json` 的 22 句日文（自寫，含 草、スパチャ、
推し 等），再加上本目錄 `vtuber_extra.json` 的 8 句補充用語（てぇてぇ、初見、同接、切り抜き
等，也是自寫）；同一場景依序翻譯，前文規則與品質評測相同。
"""

from typing import Any

from common import (
    CONTEXT_LINES,
    HERE,
    SYSTEMS_BY_KEY,
    VTUBER_DIR,
    SystemConfig,
    read_json,
    write_json,
)
from run import make_translator, translate_one, warmup

LINES_PATH = HERE.parent / "translate_benchmark" / "lines.json"
EXTRA_PATH = HERE / "vtuber_extra.json"


def load_ja_lines() -> list[dict[str, Any]]:
    """日文來源的句子（lines.json＋補充句）；譯文語言由呼叫端決定。"""
    lines = read_json(LINES_PATH)["lines"] + read_json(EXTRA_PATH)["lines"]
    return [line for line in lines if line["source"] == "ja"]


def run_vtuber(
    system: SystemConfig, targets: tuple[str, ...] = ("zh-TW", "en")
) -> None:
    """用一組設定翻譯全部日文句（zh-TW 與 en 各一次），結果存 results/vtuber/<key>.json。"""
    translator = make_translator(system)
    lines = load_ja_lines()
    print(f"\n=== {system.key}：暖機 {warmup(translator, targets[0]):.1f} s")
    out: dict[str, Any] = {"system": system.key, "items": {}}
    for target in targets:
        if system.zh_only and not target.startswith("zh"):
            continue
        history: dict[str, list[str]] = {}
        for line in lines:
            prev = history.setdefault(line["scene"], [])
            context = tuple(prev[-CONTEXT_LINES:]) if system.context else ()
            prev.append(line["text"])
            result = translate_one(translator, line["text"], target, context)
            out["items"].setdefault(line["id"], {})[target] = result["output"] or (
                f"（錯誤：{result['error']}）"
            )
            print(f"{line['id']} {target} {line['text']} → {result['output']}")
    write_json(VTUBER_DIR / f"{system.key}.json", out)


def render_table(target: str) -> str:
    """把已存的 VTuber 結果並排成 Markdown 表格（一欄一個設定）。"""
    saved = sorted(VTUBER_DIR.glob("*.json"))
    results = [read_json(p) for p in saved]
    results = [r for r in results if r["system"] in SYSTEMS_BY_KEY]
    if not results:
        return "還沒有 VTuber 句結果。"
    header = "| 原文 | " + " | ".join(f"`{r['system']}`" for r in results) + " |"
    rows = [header, "|---" * (len(results) + 1) + "|"]
    for line in load_ja_lines():
        cells = [r["items"].get(line["id"], {}).get(target, "—") for r in results]
        cells = [c.replace("|", "\\|").replace("\n", " ") for c in cells]
        rows.append(f"| {line['text']} | " + " | ".join(cells) + " |")
    return "\n".join(rows)
