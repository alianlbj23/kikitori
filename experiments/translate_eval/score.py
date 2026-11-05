"""chrF／BLEU 計分與 paired bootstrap（在隔離環境執行，不進專案依賴）。

由 menu.py 以下列方式呼叫（sacrebleu 只存在於這個暫時環境）：

    uv run --isolated --no-project --python 3.12 \
        --with-requirements experiments/translate_eval/requirements-score.txt \
        python -I -X utf8 experiments/translate_eval/score.py <scoring 目錄>

讀 `results/scoring/set_<資料集>.json`（menu 先在專案環境產生：中文兩邊都已用 OpenCC
t2s 統一成簡體），寫 `results/scoring/sacrebleu.json`。以 `python -I` 執行，所以只用
標準庫與 sacrebleu，不 import 同目錄的模組。
"""

import json
import sys
from pathlib import Path
from typing import Any

import sacrebleu
from sacrebleu.metrics import BLEU, CHRF
from sacrebleu.significance import PairedTest

N_SAMPLES = 1000  # sacrebleu 的 bootstrap 種子固定為 12345（SACREBLEU_SEED 預設值）


def _metrics(target: str) -> dict[str, Any]:
    # 中文用 sacrebleu 的 zh tokenizer（逐字切中文）；英文用 WMT 標準的 13a。
    tokenize = "zh" if target.startswith("zh") else "13a"
    return {"chrF": CHRF(), "BLEU": BLEU(tokenize=tokenize)}


def _num(value: object) -> float | None:
    """sacrebleu 的結果可能是 numpy float32（json 不能序列化）；統一成 float。"""
    return None if value is None else float(value)  # type: ignore[arg-type]


def _paired(
    order: list[str], systems: dict[str, list[str]], refs: list[str], target: str
) -> dict[str, dict[str, Any]]:
    """以 order[0] 為基準，對其他系統做 paired bootstrap；回傳每個系統的分數、CI、p 值。"""
    test = PairedTest(
        [(key, systems[key]) for key in order],
        _metrics(target),
        references=[refs],
        test_type="bs",
        n_samples=N_SAMPLES,
    )
    _, results = test()
    out: dict[str, dict[str, Any]] = {key: {} for key in order}
    for metric, rows in results.items():
        if metric == "System":
            continue
        # sacrebleu 以指標自己的名稱回報（CHRF() 是 "chrF2"），統一成 _metrics() 的名稱。
        name = "chrF" if metric.startswith("chrF") else metric
        for key, result in zip(order, rows, strict=True):
            out[key][name] = {
                "score": _num(result.score),
                "mean": _num(result.mean),
                "ci": _num(result.ci),
                "p_value": _num(result.p_value),
            }
    return out


def score_set(path: Path) -> dict[str, Any]:
    """一個資料集：各系統的 chrF／BLEU，以及對基準與對最佳系統的 paired bootstrap。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    refs = [u["ref"] for u in data["units"]]
    systems: dict[str, list[str]] = data["systems"]
    if not systems:
        return {"set": data["set"], "scores": {}}
    for key, hyps in systems.items():
        if len(hyps) != len(refs):
            raise ValueError(
                f"{path.name} {key}：譯文 {len(hyps)} 句、參考 {len(refs)} 句"
            )
    target = data["target"]
    metrics = _metrics(target)
    plain = {
        key: {name: m.corpus_score(hyps, [refs]).score for name, m in metrics.items()}
        for key, hyps in systems.items()
    }
    # 「最佳」只在本機受測設定中選（wmt25: 開頭的是校準用的官方輸出）。
    local = [k for k in plain if not k.startswith("wmt25:")] or list(plain)
    best = max(local, key=lambda k: plain[k]["chrF"])
    baseline = data["baseline"] if data["baseline"] in systems else best
    report: dict[str, Any] = {
        "set": data["set"],
        "target": target,
        "units": len(refs),
        "signature": {name: str(m.get_signature()) for name, m in metrics.items()},
        "scores": plain,
        "best": best,
        "baseline": baseline,
    }
    report["contrasts"] = []
    for a, b in data.get("contrasts", []):
        result = _paired([a, b], systems, refs, target)[b]
        report["contrasts"].append(
            {
                "a": a,
                "b": b,
                **{
                    name: {
                        "delta": plain[b][name] - plain[a][name],
                        "p_value": r["p_value"],
                    }
                    for name, r in result.items()
                },
            }
        )
    if len(systems) > 1:
        others = [k for k in systems if k != baseline]
        report["vs_baseline"] = _paired([baseline, *others], systems, refs, target)
        if best != baseline:
            rest = [k for k in systems if k != best]
            report["vs_best"] = _paired([best, *rest], systems, refs, target)
    return report


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("用法：score.py <scoring 目錄>")
    folder = Path(sys.argv[1])
    sets = sorted(folder.glob("set_*.json"))
    if not sets:
        raise SystemExit(f"{folder} 裡沒有 set_*.json；先在選單產生計分輸入")
    out = {"sacrebleu": sacrebleu.__version__, "n_samples": N_SAMPLES, "sets": {}}
    for path in sets:
        print(f"計分 {path.name} …", flush=True)
        report = score_set(path)
        if not report["scores"]:
            print("  （還沒有完成的系統，略過）")
            continue
        out["sets"][report["set"]] = report
        for key, s in sorted(report["scores"].items(), key=lambda kv: -kv[1]["chrF"]):
            print(f"  {key:16s} chrF {s['chrF']:6.2f}  BLEU {s['BLEU']:6.2f}")
    (folder / "sacrebleu.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"已寫入 {folder / 'sacrebleu.json'}")


if __name__ == "__main__":
    main()
