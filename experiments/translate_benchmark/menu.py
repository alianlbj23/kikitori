"""翻譯模型實測（互動選單）：對本機 Ollama 的每個模型跑固定測試句。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集。量測串流的首字延遲
（第一次產出可顯示的譯文）與總延遲，並用 `postprocess.simplified_characters`
檢查譯文是否混入簡體專用字（原始輸出與簡轉繁之後各算一次）。

用法：uv run python -I experiments/translate_benchmark/menu.py
"""

import json
import statistics
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from kikitori.translate import (
    PRESETS,
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationRequest,
    TranslatorConfig,
    supports,
)
from kikitori.translate.postprocess import simplified_characters, to_taiwan_traditional

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
OLLAMA = "http://localhost:11434"
CONTEXT_LINES = 2


def installed_models() -> list[str]:
    """向 Ollama 原生 API 查已安裝的模型。"""
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as resp:
        data = json.load(resp)
    return [m["name"] for m in data.get("models", [])]


def load_lines() -> list[dict[str, Any]]:
    return json.loads((HERE / "lines.json").read_text(encoding="utf-8"))["lines"]


def run_model(model: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    """對一個模型跑全部支援的測試句；回傳可存成 JSON 的結果。"""
    preset = PRESETS[model]
    # 關掉簡轉繁，才能量到模型原始輸出的簡體字；轉換後的結果另外計算。
    translator = OpenAICompatibleTranslator(
        TranslatorConfig(model=model, read_timeout=120.0, convert_script=False)
    )
    print(f"\n=== {model}：暖機（載入模型）…")
    start = time.perf_counter()
    try:
        translator.translate(TranslationRequest("こんにちは", "ja"))
        warmup = time.perf_counter() - start
    except TranslationError as exc:
        print(f"暖機失敗：{exc}")
        return {"model": model, "error": str(exc), "items": []}
    print(f"暖機 {warmup:.1f} s")

    items: list[dict[str, Any]] = []
    history: dict[str, list[str]] = {}
    for line in lines:
        if not supports(preset, line["source"], line["target"]):
            continue
        scene = history.setdefault(line["scene"], [])
        context = tuple(scene[-CONTEXT_LINES:])
        scene.append(line["text"])
        req = TranslationRequest(line["text"], line["source"], line["target"], context)
        item: dict[str, Any] = {
            "id": line["id"],
            "text": line["text"],
            "target": line["target"],
        }
        start = time.perf_counter()
        ttft = None
        updates = 0
        output = ""
        try:
            for output in translator.stream(req):
                if ttft is None:
                    ttft = time.perf_counter() - start
                updates += 1
        except TranslationError as exc:
            item["error"] = f"{type(exc).__name__}: {exc}"
        item.update(
            ttft=ttft,
            total=time.perf_counter() - start,
            updates=updates,
            raw=output,
        )
        if line["target"] == "zh-TW":
            converted = to_taiwan_traditional(output)
            item.update(
                converted=converted,
                simplified_raw=simplified_characters(output),
                simplified_after=simplified_characters(converted),
            )
        print(
            f"{line['id']} {item['ttft'] or 0:5.2f}s/{item['total']:5.2f}s "
            f"{line['text']} → {item.get('converted', output)}"
            + (
                f"  [簡體:{''.join(item['simplified_raw'])}]"
                if item.get("simplified_raw")
                else ""
            )
            + (f"  [錯誤:{item['error']}]" if "error" in item else "")
        )
        items.append(item)
    return {"model": model, "warmup_s": warmup, "items": items}


def summarize(result: dict[str, Any]) -> dict[str, Any]:
    items = [i for i in result["items"] if "error" not in i and i["ttft"] is not None]
    zh = [i for i in items if i["target"] == "zh-TW"]
    ttft = [i["ttft"] for i in items]
    total = [i["total"] for i in items]

    def p90(xs: list[float]) -> float:
        return (
            statistics.quantiles(xs, n=10)[-1]
            if len(xs) >= 2
            else (xs[0] if xs else 0.0)
        )

    return {
        "model": result["model"],
        "ok": len(items),
        "errors": len(result["items"]) - len(items),
        "ttft_median": statistics.median(ttft) if ttft else 0.0,
        "ttft_p90": p90(ttft),
        "total_median": statistics.median(total) if total else 0.0,
        "total_p90": p90(total),
        "zh_lines": len(zh),
        "simplified_raw_lines": sum(1 for i in zh if i["simplified_raw"]),
        "simplified_after_lines": sum(1 for i in zh if i["simplified_after"]),
    }


def print_summary(summaries: list[dict[str, Any]]) -> None:
    print(
        "\n| 模型 | 成功 | 錯誤 | 首字中位數 | 首字 P90 | 總延遲中位數 | 總延遲 P90 "
        "| 原始輸出含簡體（句） | 轉換後含簡體（句） |"
    )
    print("|---|---|---|---|---|---|---|---|---|")
    for s in summaries:
        print(
            f"| `{s['model']}` | {s['ok']} | {s['errors']} | {s['ttft_median']:.2f} s "
            f"| {s['ttft_p90']:.2f} s | {s['total_median']:.2f} s | {s['total_p90']:.2f} s "
            f"| {s['simplified_raw_lines']}/{s['zh_lines']} "
            f"| {s['simplified_after_lines']}/{s['zh_lines']} |"
        )


def result_path(model: str) -> Path:
    safe = "".join(c if c.isalnum() or c in "-._" else "_" for c in model)
    return RESULTS_DIR / f"{safe}.json"


def benchmark(models: list[str]) -> None:
    lines = load_lines()
    RESULTS_DIR.mkdir(exist_ok=True)
    summaries = []
    for model in models:
        result = run_model(model, lines)
        result["summary"] = summarize(result)
        result["date"] = time.strftime("%Y-%m-%d %H:%M")
        result_path(model).write_text(
            json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        summaries.append(result["summary"])
    print_summary(summaries)


def show_saved() -> None:
    summaries = []
    for path in sorted(RESULTS_DIR.glob("*.json")):
        summaries.append(json.loads(path.read_text(encoding="utf-8"))["summary"])
    if not summaries:
        print("還沒有結果。")
        return
    print_summary(summaries)


def choose(prompt: str, options: list[str]) -> int | None:
    """顯示編號選單，回傳選到的索引；輸入 0 或空白回傳 None。"""
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    print("  0) 返回")
    while True:
        answer = input(prompt).strip()
        if answer in ("", "0"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("請輸入選單上的編號。")


def main() -> None:
    actions = [
        "列出本機 Ollama 模型",
        "對所有已安裝且有預設組的模型執行實測",
        "選一個模型執行實測",
        "顯示已存的結果摘要",
    ]
    while True:
        print("\n翻譯模型實測")
        picked = choose("選擇：", actions)
        if picked is None:
            return
        try:
            if picked == 0:
                for name in installed_models():
                    print(f"  {name}{'' if name in PRESETS else '（沒有預設組）'}")
            elif picked == 1:
                benchmark([m for m in installed_models() if m in PRESETS])
            elif picked == 2:
                known = [m for m in installed_models() if m in PRESETS]
                index = choose("模型：", known)
                if index is not None:
                    benchmark([known[index]])
            else:
                show_saved()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            print(f"無法連到 Ollama（{OLLAMA}）：{exc}")


if __name__ == "__main__":
    main()
