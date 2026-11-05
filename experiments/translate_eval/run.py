"""用正式的 kikitori.translate 翻譯器逐句翻譯評測資料（品質）與量延遲。

每句一個請求，前文只帶同一文件的前兩句原文（與 pipeline 相同），用 `stream()`
同時記錄首字延遲與總延遲。輸出逐句追加到 JSONL，中斷後再跑會從沒翻過的句子繼續。

延遲數字：品質跑的同時 GPU 可能被其他工作（ASR 實驗）共用，Ollama 會在 VRAM 不足
時把部分層放到 CPU，延遲會偏高；所以另有「只量延遲」模式，等 GPU 空閒時單獨重跑。
"""

import dataclasses
import json
import statistics
import subprocess
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from common import (
    CONTEXT_LINES,
    EVAL_SETS,
    LATENCY_DIR,
    SYSTEMS_BY_KEY,
    SystemConfig,
    output_path,
    read_json,
    read_jsonl,
    set_path,
    system_applies,
    write_json,
)

from kikitori.translate import (
    PRESETS,
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationRequest,
    TranslatorConfig,
)
from kikitori.translate.postprocess import to_script

# 每次 socket 讀取的上限；GPU 被共用、模型部分在 CPU 時第一次載入可能很久。
READ_TIMEOUT = 180.0
LATENCY_PER_SET = 60  # 只量延遲時每個資料集取前幾句
NEWLINE = b"\n"


def make_translator(system: SystemConfig) -> OpenAICompatibleTranslator:
    """依設定建立正式翻譯器。

    `convert_script=False` 才能拿到模型原始輸出（算簡體殘留）；之後用正式程式的
    `to_script()` 轉成使用者實際看到的譯文，結果與 `convert_script=True` 相同。
    """
    preset = PRESETS[system.model]
    if not system.context:
        # 不帶前文：用預設組的副本關掉前文，其他取樣參數不動。
        preset = dataclasses.replace(preset, supports_context=False)
    config = TranslatorConfig(
        model=system.model,
        temperature=system.temperature,
        read_timeout=READ_TIMEOUT,
        convert_script=False,
    )
    return OpenAICompatibleTranslator(config, preset=preset)


def gpu_snapshot() -> str:
    """記錄當下 GPU 記憶體用量（延遲是否受共用影響的佐證）；查不到就回傳原因。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=name,memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader",
            ],
            capture_output=True,
            text=True,
            timeout=10,
            check=True,
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return f"unavailable: {type(exc).__name__}"


def translate_one(
    translator: OpenAICompatibleTranslator,
    text: str,
    target: str,
    context: tuple[str, ...],
) -> dict[str, Any]:
    """翻一句，回傳原始輸出、最終譯文與延遲；翻譯錯誤記在 `error`，不中斷整批。"""
    request = TranslationRequest(text, "ja", target, context)
    start = time.perf_counter()
    ttft: float | None = None
    raw = ""
    error: str | None = None
    try:
        for raw in translator.stream(request):
            if ttft is None:
                ttft = time.perf_counter() - start
    except TranslationError as exc:
        error = f"{type(exc).__name__}: {exc}"
    total = time.perf_counter() - start
    return {
        "raw": raw,
        "output": to_script(raw, target) if raw else "",
        "error": error,
        "ttft": ttft,
        "total": total,
    }


def warmup(translator: OpenAICompatibleTranslator, target: str) -> float:
    """送一句短請求讓 Ollama 載入模型；回傳載入時間（秒）。"""
    start = time.perf_counter()
    translator.translate(TranslationRequest("こんにちは", "ja", target))
    return time.perf_counter() - start


def run_quality(system: SystemConfig, set_names: list[str]) -> None:
    """對一組設定翻譯指定資料集的全部句子（可續跑）。"""
    translator = make_translator(system)
    loaded = False
    for name in set_names:
        eval_set = EVAL_SETS[name]
        if not system_applies(system, eval_set):
            continue
        data = read_json(set_path(name))
        path = output_path(system.key, name)
        _drop_partial_line(path)
        done = {row["id"] for row in read_jsonl(path)}
        todo = [s for s in data["segments"] if s["id"] not in done]
        if not todo:
            print(f"{system.key} / {name}：已完成（{len(done)} 句）")
            continue
        if not loaded:
            print(
                f"\n=== {system.key}：暖機（載入 {system.model}）…  GPU：{gpu_snapshot()}"
            )
            print(f"暖機 {warmup(translator, eval_set.target):.1f} s")
            loaded = True
        print(f"{system.key} / {name}：{len(todo)} 句待翻（已完成 {len(done)}）")
        # 前文依文件內順序取，所以要從整個資料集重建，而不是只看待翻的句子。
        history: dict[str, list[str]] = {}
        contexts: dict[str, tuple[str, ...]] = {}
        for seg in data["segments"]:
            prev = history.setdefault(seg["doc"], [])
            contexts[seg["id"]] = tuple(prev[-CONTEXT_LINES:]) if system.context else ()
            prev.append(seg["src"])
        path.parent.mkdir(parents=True, exist_ok=True)
        errors = 0
        with path.open("a", encoding="utf-8") as fh:
            for i, seg in enumerate(todo, 1):
                result = translate_one(
                    translator, seg["src"], eval_set.target, contexts[seg["id"]]
                )
                row = {"id": seg["id"], "context": list(contexts[seg["id"]]), **result}
                fh.write(_json_line(row))
                fh.flush()
                errors += result["error"] is not None
                if i % 50 == 0 or i == len(todo):
                    print(
                        f"  {i}/{len(todo)}  錯誤 {errors}  最後一句：{seg['src'][:30]} → {result['output'][:40]}"
                    )


def _drop_partial_line(path: Path) -> None:
    """上次中斷時最後一行可能沒寫完（沒有換行）；截掉它，續跑時才不會接在壞行後面。"""
    if not path.exists():
        return
    data = path.read_bytes()
    if data and not data.endswith(NEWLINE):
        cut = data.rfind(NEWLINE) + 1
        print(f"{path.name}：截掉上次中斷時沒寫完的最後一行")
        path.write_bytes(data[:cut])


def run_quality_grouped(keys: list[str], set_names: list[str]) -> None:
    """依模型分組，同一個模型的幾組設定同時跑（各用一條執行緒）。

    Ollama 對同一個已載入的模型會平行處理多個請求（實測吞吐量約為單一請求的兩倍），
    不同模型則依序跑，避免兩個模型同時佔 VRAM。平行請求會互相拖慢延遲，所以這裡量到
    的延遲不能當真；乾淨的延遲用 `run_latency()` 逐一量。
    """
    groups: dict[str, list[str]] = {}
    for key in keys:
        groups.setdefault(SYSTEMS_BY_KEY[key].model, []).append(key)
    for model, members in groups.items():
        print(f"##### {model}：{', '.join(members)} 同時執行")
        with ThreadPoolExecutor(max_workers=len(members)) as pool:
            futures = [
                pool.submit(run_quality, SYSTEMS_BY_KEY[key], set_names)
                for key in members
            ]
            for future in futures:
                future.result()  # 讓執行緒裡的例外往上丟


def _json_line(row: dict[str, Any]) -> str:
    return json.dumps(row, ensure_ascii=False) + "\n"


def run_latency(system: SystemConfig, set_names: list[str]) -> dict[str, Any]:
    """只量延遲：每個資料集取前 LATENCY_PER_SET 句，結果另存，不動品質輸出。"""
    translator = make_translator(system)
    first = next(
        EVAL_SETS[n] for n in set_names if system_applies(system, EVAL_SETS[n])
    )
    gpu_before = gpu_snapshot()
    load_s = warmup(translator, first.target)
    rows: list[dict[str, Any]] = []
    for name in set_names:
        eval_set = EVAL_SETS[name]
        if not system_applies(system, eval_set):
            continue
        history: dict[str, list[str]] = {}
        for seg in read_json(set_path(name))["segments"][:LATENCY_PER_SET]:
            prev = history.setdefault(seg["doc"], [])
            context = tuple(prev[-CONTEXT_LINES:]) if system.context else ()
            prev.append(seg["src"])
            result = translate_one(translator, seg["src"], eval_set.target, context)
            rows.append({"set": name, "id": seg["id"], **result})
    summary = {
        "system": system.key,
        "date": time.strftime("%Y-%m-%d %H:%M"),
        "gpu_before": gpu_before,
        "gpu_after": gpu_snapshot(),
        "load_s": load_s,
        **latency_summary(rows),
    }
    write_json(LATENCY_DIR / f"{system.key}.json", {"summary": summary, "rows": rows})
    return summary


def latency_summary(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """首字與總延遲的中位數與 P90（只算成功的句子）。"""
    ok = [r for r in rows if r.get("error") is None and r.get("ttft") is not None]

    def pct(values: list[float], q: int) -> float | None:
        if not values:
            return None
        if len(values) == 1:
            return values[0]
        return statistics.quantiles(values, n=100, method="inclusive")[q - 1]

    ttft = [r["ttft"] for r in ok]
    total = [r["total"] for r in ok]
    return {
        "n": len(rows),
        "ok": len(ok),
        "ttft_p50": pct(ttft, 50),
        "ttft_p90": pct(ttft, 90),
        "total_p50": pct(total, 50),
        "total_p90": pct(total, 90),
    }
