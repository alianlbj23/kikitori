"""中日同形異義詞：比較提示詞寫法與翻譯模型（互動選單）。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集。問題：中文「豬排便當」被翻成
「豚の排泄物」——中文與日文共用漢字，模型用另一種語言的讀法斷詞、理解同形詞。

比較的對象：

- Qwen3.5（app 的預設組）：`old-ja`（改版前：日文寫指示、無內建詞表）、`zh-instr`
  （中文寫指示）、`zh-instr+terms`（中文寫指示＋內建的中日同形詞表＝目前 app 的做法）。
  全部經由正式程式碼的 `OpenAICompatibleTranslator._prepare()` 組出請求。
- 翻譯專用模型：TranslateGemma（Google，Ollama 官方模型庫）、Seed-X-PPO-7B
  （ByteDance，社群 GGUF 量化；官方不建議用非官方量化，結果可能偏低），各用 model card
  的官方提示格式。

判斷：陷阱句要含 must 的任一詞、不能含 forbid 的任一詞；全部句子另外檢查「沒翻譯」
（譯成日文卻沒有假名，或譯成中文卻有假名）。陷阱句再分成「內建詞表有收」與「沒收」，
後者才看得出提示詞本身的效果（詞表收了的詞答對是預期中的事）。temperature 一律 0。

用法：
  uv run python -I -X utf8 experiments/kanji_false_friends/menu.py
  uv run python -I -X utf8 experiments/kanji_false_friends/menu.py --batch models=qwen3.5:9b,translategemma:4b
"""

import json
import re
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.translate import (
    OpenAICompatibleTranslator,
    TranslationRequest,
    TranslatorConfig,
    prompts,
)
from kikitori.translate.glossary import Glossary
from kikitori.translate.postprocess import to_taiwan_traditional

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
OLLAMA = "http://localhost:11434"
REQUEST_TIMEOUT = 180.0
KANA = re.compile(r"[぀-ゟ゠-ヿ]")
GLOSSARY = Glossary()  # app 的預設：內建表開啟、沒有使用者詞條

LANG = {
    "zh": ("Chinese", "zh-Hant"),
    "ja": ("Japanese", "ja"),
    "zh-TW": ("Chinese", "zh-Hant"),
}
SEED_X_NAME = {"zh": "Chinese", "ja": "Japanese", "zh-TW": "Chinese"}
SEED_X_TAG = {"ja": "<ja>", "zh-TW": "<zh>"}


def post(path: str, body: dict[str, Any]) -> dict[str, Any]:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{OLLAMA}{path}", data, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.load(resp)


def chat(payload: dict[str, Any]) -> str:
    out = post("/v1/chat/completions", {**payload, "stream": False})
    return (out["choices"][0]["message"]["content"] or "").strip()


def qwen_variant(name: str) -> Callable[[str, dict[str, Any]], str]:
    def run(model: str, line: dict[str, Any]) -> str:
        glossary = GLOSSARY if name == "zh-instr+terms" else None
        translator = OpenAICompatibleTranslator(
            TranslatorConfig(model=model, read_timeout=REQUEST_TIMEOUT),
            glossary=glossary,
        )
        request = TranslationRequest(line["text"], line["source"], line["target"])
        if name == "old-ja":
            # 改版前：沒有（原文, 譯文）專用範本，中翻日用日文寫的指示
            with mock.patch.dict(prompts._PAIR_TEMPLATES, clear=True):
                prepared = translator._prepare(request)
        else:
            prepared = translator._prepare(request)
        return chat(json.loads(translator._payload(prepared, stream=False)))

    return run


def translategemma(model: str, line: dict[str, Any]) -> str:
    src_name, src_code = LANG[line["source"]]
    tgt_name, tgt_code = LANG[line["target"]]
    prompt = (
        f"You are a professional {src_name} ({src_code}) to {tgt_name} ({tgt_code}) "
        f"translator. Your goal is to accurately convey the meaning and nuances of the "
        f"original {src_name} text while adhering to {tgt_name} grammar, vocabulary, and "
        f"cultural sensitivities.\nProduce only the {tgt_name} translation, without any "
        f"additional explanations or commentary. Please translate the following "
        f"{src_name} text into {tgt_name}:\n\n\n{line['text']}"
    )
    return chat(
        {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 256,
        }
    )


def seed_x(model: str, line: dict[str, Any]) -> str:
    # model card：沒有 chat template，提示詞結尾要加譯文語言的標籤
    prompt = (
        f"Translate the following {SEED_X_NAME[line['source']]} sentence into "
        f"{SEED_X_NAME[line['target']]}:\n{line['text']} {SEED_X_TAG[line['target']]}"
    )
    out = post(
        "/api/generate",
        {
            "model": model,
            "prompt": prompt,
            "raw": True,
            "stream": False,
            "options": {"temperature": 0, "num_predict": 256},
        },
    )
    return (
        out["response"].strip().splitlines()[0].strip()
        if out["response"].strip()
        else ""
    )


def variants_for(model: str) -> dict[str, Callable[[str, dict[str, Any]], str]]:
    if model.startswith("qwen3.5"):
        return {n: qwen_variant(n) for n in ("old-ja", "zh-instr", "zh-instr+terms")}
    if "translategemma" in model:
        return {"official": translategemma}
    if "seed-x" in model.lower():
        return {"official": seed_x}
    raise SystemExit(f"no prompt format for {model}")


def judge(line: dict[str, Any], output: str) -> dict[str, bool]:
    untranslated = (
        not KANA.search(output) if line["target"] == "ja" else bool(KANA.search(output))
    )
    if line.get("control"):
        return {"untranslated": untranslated}
    correct = any(w in output for w in line["must"]) and not any(
        w in output for w in line["forbid"]
    )
    return {"untranslated": untranslated, "correct": correct and not untranslated}


def covered(line: dict[str, Any]) -> bool:
    return bool(GLOSSARY.lookup(line["text"], line["source"], line["target"]))


def load_lines() -> list[dict[str, Any]]:
    return json.loads((HERE / "lines.json").read_text(encoding="utf-8"))["lines"]


def run_model(model: str, lines: list[dict[str, Any]]) -> dict[str, Any]:
    variants = variants_for(model)
    print(f"\n=== {model}")
    items = []
    for line in lines:
        row: dict[str, Any] = {
            "id": line["id"],
            "text": line["text"],
            "covered": covered(line),
            "variants": {},
        }
        for name, run in variants.items():
            started = time.perf_counter()
            try:
                output = run(model, line)
            except (urllib.error.URLError, TimeoutError, KeyError, ValueError) as exc:
                print(f"  {line['id']} {name}: ERROR {type(exc).__name__}: {exc}")
                row["variants"][name] = {"error": f"{type(exc).__name__}: {exc}"}
                continue
            if line["target"] == "zh-TW":
                output = to_taiwan_traditional(output)  # app 也會轉，比對才公平
            verdict = judge(line, output)
            row["variants"][name] = {
                "output": output,
                "secs": round(time.perf_counter() - started, 2),
                **verdict,
            }
            mark = (
                "  " if line.get("control") else ("OK" if verdict["correct"] else "NG")
            )
            flag = " [沒翻]" if verdict["untranslated"] else ""
            print(f"  {line['id']} {name:15} {mark}{flag} {output[:70]}")
        items.append(row)
    return {"model": model, "items": items}


def summarize(result: dict[str, Any]) -> list[str]:
    out = [
        f"## {result['model']}",
        "",
        "| 方向 | 寫法 | 陷阱句（詞表有收） | 陷阱句（詞表沒收） | 沒翻譯（全部句） | 每句秒數中位數 |",
        "|---|---|---|---|---|---|",
    ]
    names = sorted({n for r in result["items"] for n in r["variants"]})
    for prefix, label in (("zj-", "中→日"), ("jz-", "日→中")):
        rows = [r for r in result["items"] if r["id"].startswith(prefix)]
        if not rows:
            continue
        traps = [r for r in rows if not r["id"].split("-")[1].startswith("c")]
        for name in names:

            def ok(subset: list[dict[str, Any]], name: str = name) -> str:
                hit = sum(
                    1 for r in subset if r["variants"].get(name, {}).get("correct")
                )
                return f"{hit}/{len(subset)}"

            raw = sum(
                1 for r in rows if r["variants"].get(name, {}).get("untranslated")
            )
            secs = sorted(
                r["variants"][name]["secs"]
                for r in rows
                if "secs" in r["variants"].get(name, {})
            )
            median = f"{secs[len(secs) // 2]:.2f}" if secs else "-"
            out.append(
                f"| {label} | {name} | {ok([r for r in traps if r['covered']])} | "
                f"{ok([r for r in traps if not r['covered']])} | {raw}/{len(rows)} | {median} |"
            )
    return out


def save(result: dict[str, Any]) -> Path:
    RESULTS_DIR.mkdir(exist_ok=True)
    name = re.sub(r"[^A-Za-z0-9._-]+", "_", result["model"])
    path = RESULTS_DIR / f"{name}.json"
    path.write_text(json.dumps(result, ensure_ascii=False, indent=1), encoding="utf-8")
    return path


def installed_models() -> list[str]:
    with urllib.request.urlopen(f"{OLLAMA}/api/tags", timeout=5) as resp:
        return [m["name"] for m in json.load(resp).get("models", [])]


def run(models: list[str]) -> None:
    lines = load_lines()
    for model in models:
        result = run_model(model, lines)
        print(f"saved {save(result)}")
        print("\n".join(summarize(result)))


def show_saved() -> None:
    for path in sorted(RESULTS_DIR.glob("*.json")):
        print("\n".join(summarize(json.loads(path.read_text(encoding="utf-8")))))
        print()


def choose(prompt: str, options: list[str]) -> int | None:
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    answer = input(prompt).strip()
    if answer.isdigit() and 1 <= int(answer) <= len(options):
        return int(answer) - 1
    return None


def main() -> None:
    if "--batch" in sys.argv:
        params = dict(a.split("=", 1) for a in sys.argv[1:] if "=" in a)
        run([m for m in params.get("models", "qwen3.5:9b").split(",") if m])
        return
    while True:
        print("\n中日同形異義詞：提示詞與模型比較")
        pick = choose("選擇（Enter 結束）：", ["選模型執行", "顯示已存的摘要"])
        if pick is None:
            return
        if pick == 0:
            models = installed_models()
            m = choose("模型：", models)
            if m is not None:
                run([models[m]])
        else:
            show_saved()


if __name__ == "__main__":
    main()
