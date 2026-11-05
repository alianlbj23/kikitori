"""COMET（Unbabel/wmt22-comet-da）計分與 paired bootstrap（隔離環境，CPU）。

COMET 需要 PyTorch，絕不能進專案依賴；由 menu.py 以下列方式呼叫：

    uv run --isolated --no-project --python 3.12 \
        --with-requirements experiments/translate_eval/requirements-comet.txt \
        python -I -X utf8 experiments/translate_eval/score_comet.py <scoring 目錄> <模型快取目錄>

模型（Apache-2.0，約 2.3 GB）下載到 data/（git-ignore）。每個（資料集, 系統）的逐單位
分數依譯文內容的雜湊快取，重跑只算新的或改過的系統。以 `python -I` 執行，只用標準庫、
numpy 與 comet，不 import 同目錄的模組。
"""

import hashlib
import json
import re
import sys
from pathlib import Path
from typing import Any

import numpy as np
from comet import download_model, load_from_checkpoint

MODEL = "Unbabel/wmt22-comet-da"
SEED = 12345
N_SAMPLES = 1000
BATCH = 16


def _digest(units: list[dict[str, Any]], hyps: list[str]) -> str:
    payload = json.dumps(
        [[u["src"], u["ref"], h] for u, h in zip(units, hyps, strict=True)]
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def paired_bootstrap(base: np.ndarray, other: np.ndarray) -> dict[str, float]:
    """對逐單位分數做 paired bootstrap（固定種子）。

    回傳差值（other − base）的平均、95% 信賴區間，以及雙尾 p 值：把 bootstrap 的差值
    分布平移到以 0 為中心後，看有多少樣本的絕對值不小於實際差值（與 sacrebleu 的
    paired bootstrap 相同思路）。
    """
    rng = np.random.default_rng(SEED)
    n = len(base)
    idx = rng.integers(0, n, size=(N_SAMPLES, n))
    diff = other - base
    deltas = diff[idx].mean(axis=1)
    observed = float(diff.mean())
    p = (1 + int(np.sum(np.abs(deltas - observed) >= abs(observed)))) / (N_SAMPLES + 1)
    lo, hi = np.percentile(deltas, [2.5, 97.5])
    return {"delta": observed, "ci_low": float(lo), "ci_high": float(hi), "p_value": p}


def main() -> None:
    if len(sys.argv) != 3:
        raise SystemExit("用法：score_comet.py <scoring 目錄> <模型快取目錄>")
    folder = Path(sys.argv[1])
    model_dir = Path(sys.argv[2])
    cache_dir = folder / "comet_cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    sets = sorted(folder.glob("set_*.json"))
    if not sets:
        raise SystemExit(f"{folder} 裡沒有 set_*.json；先在選單產生計分輸入")

    model = None
    out: dict[str, Any] = {
        "model": MODEL,
        "n_samples": N_SAMPLES,
        "seed": SEED,
        "sets": {},
    }
    for path in sets:
        data = json.loads(path.read_text(encoding="utf-8"))
        units = data["units"]
        per_system: dict[str, np.ndarray] = {}
        for key, hyps in data["systems"].items():
            digest = _digest(units, hyps)
            # key 可能含「:」（wmt25:…），Windows 檔名不能用，換成底線。
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", key)
            cache = cache_dir / f"{data['set']}__{safe}.json"
            if cache.exists():
                cached = json.loads(cache.read_text(encoding="utf-8"))
                if cached.get("digest") == digest:
                    per_system[key] = np.array(cached["scores"])
                    continue
            if model is None:
                print(f"載入 {MODEL}（第一次會下載約 2.3 GB）…", flush=True)
                model = load_from_checkpoint(
                    download_model(MODEL, saving_directory=model_dir)
                )
            print(f"COMET {data['set']} / {key}：{len(units)} 單位…", flush=True)
            samples = [
                {"src": u["src"], "mt": h, "ref": u["ref"]}
                for u, h in zip(units, hyps, strict=True)
            ]
            result = model.predict(
                samples, batch_size=BATCH, gpus=0, progress_bar=False
            )
            scores = [float(s) for s in result.scores]
            cache.write_text(
                json.dumps({"digest": digest, "scores": scores}), encoding="utf-8"
            )
            per_system[key] = np.array(scores)

        if not per_system:
            continue
        means = {k: float(v.mean()) for k, v in per_system.items()}
        # 「最佳」只在本機受測設定中選（wmt25: 開頭的是校準用的官方輸出）。
        local = [k for k in means if not k.startswith("wmt25:")] or list(means)
        best = max(local, key=lambda k: means[k])
        baseline = data["baseline"] if data["baseline"] in per_system else best
        report: dict[str, Any] = {"scores": means, "best": best, "baseline": baseline}
        report["vs_baseline"] = {
            k: paired_bootstrap(per_system[baseline], v)
            for k, v in per_system.items()
            if k != baseline
        }
        report["vs_best"] = {
            k: paired_bootstrap(per_system[best], v)
            for k, v in per_system.items()
            if k != best
        }
        report["contrasts"] = [
            {"a": a, "b": b, **paired_bootstrap(per_system[a], per_system[b])}
            for a, b in data.get("contrasts", [])
            if a in per_system and b in per_system
        ]
        out["sets"][data["set"]] = report
        for key, mean in sorted(means.items(), key=lambda kv: -kv[1]):
            print(f"  {data['set']:10s} {key:16s} COMET {mean:.4f}")
    (folder / "comet.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    print(f"已寫入 {folder / 'comet.json'}")


if __name__ == "__main__":
    main()
