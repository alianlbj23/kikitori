"""隔離測試：不經過引擎，反覆載入／辨識／釋放 Whisper 模型，找出每個 session 成長的來源。

暫時性實驗，不屬於正式程式碼。情境 1 看到每個 session 結束後顯示記憶體與私有記憶體都
穩定成長；這裡把引擎、pipeline、擷取、翻譯都拿掉，比較幾種做法（每種在獨立的子行程，
環境變數要在匯入 ctranslate2 之前設定）：

- `kikitori-thread`：`kikitori.asr.load_transcriber()` 在新的執行緒載入、另一條新的執行緒
  辨識（引擎的做法：session 載入執行緒、pipeline 的 worker 執行緒），主執行緒釋放。
- `kikitori-main`：同上但全部在主執行緒。
- `kikitori-async`：同 `kikitori-thread`，但 `CT2_CUDA_ALLOCATOR=cuda_malloc_async`
  （不用 CUB 的快取配置器）。
- `raw-load`：不經 kikitori，直接 `ctranslate2.models.Whisper(...)` 再 `del`（不辨識）。
- `raw-unload`：同上，但 `del` 之前先 `unload_model()`（候選修正）。
- `raw-infer`：直接 `ctranslate2.models.Whisper(...)`，跑一次與 `warmup()` 相同的 encode（結果留在
  GPU）＋一步 generate，再 `del`——不經 kikitori 的任何程式碼。
- `raw-infer-unload`：同上，但 `del` 之前先 `unload_model()`。
- `raw-reuse`：候選修正——整個行程只建立一次模型物件；每輪 `load_model()`、推論、
  `unload_model()`（權重離開 GPU，但模型的工作執行緒與它的 CUDA 資源留著重用）。

每輪結束（釋放、`gc.collect()`、等 3 秒）後記錄自己的私有記憶體、顯示記憶體（dedicated+shared）、
執行緒與 handle 數，回報第 2 輪之後的斜率。
"""

from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import RESULTS, TRACKS, load_clip, r1, slope
from monitor import Monitor

logger = logging.getLogger("soak.ct2")

VARIANTS: dict[str, dict[str, str]] = {
    "kikitori-thread": {},
    "kikitori-main": {},
    "kikitori-async": {"CT2_CUDA_ALLOCATOR": "cuda_malloc_async"},
    "raw-load": {},
    "raw-unload": {},
    "raw-infer": {},
    "raw-infer-unload": {},
    "raw-reuse": {},
}
KEYS = ("private_mib", "gpu_mib", "threads", "handles")


def _steps(variant: str, model: str) -> list[Callable[[dict[str, Any]], None]]:
    """這種做法一輪要依序執行的步驟（每步接收同一個 holder）。"""
    import numpy as np

    from kikitori.asr import load_transcriber
    from kikitori.asr.cache import default_cache_dir
    from kikitori.asr.download import model_dir
    from kikitori.asr.registry import get_model
    from kikitori.asr.whisper import preload_cuda_libraries
    from kikitori.hardware import installed_runtime_dir

    runtime = installed_runtime_dir()
    dirs = [runtime] if runtime else []
    audio = (load_clip(TRACKS / "anime.wav")[: 16000 * 6] * 32767).astype(np.int16)

    if variant.startswith("kikitori"):

        def load(h: dict[str, Any]) -> None:
            h["t"] = load_transcriber(
                model, device="cuda", compute_type="float16", cuda_library_dirs=dirs
            )

        def run(h: dict[str, Any]) -> None:
            h["text"] = h["t"].transcribe(audio, language="ja").text

        return [load, run]

    import ctranslate2

    preload_cuda_libraries(dirs)
    path = os.fspath(model_dir(get_model(model), default_cache_dir()))

    def raw_load(h: dict[str, Any]) -> None:
        h["m"] = ctranslate2.models.Whisper(
            path, device="cuda", compute_type="float16", inter_threads=1
        )

    def raw_unload(h: dict[str, Any]) -> None:
        h["m"].unload_model()

    def raw_infer(h: dict[str, Any]) -> None:
        m = h["m"]
        zeros = np.zeros((1, m.n_mels, 3000), dtype=np.float32)
        encoded = m.encode(ctranslate2.StorageView.from_array(zeros), to_cpu=False)
        # <|startoftranscript|> <|en|> <|transcribe|> <|notimestamps|>（多語模型的 id）
        m.generate(encoded, [[50258, 50259, 50359, 50363]], beam_size=1, max_length=5)

    shared: dict[str, Any] = {}

    def reuse_load(h: dict[str, Any]) -> None:
        if "m" not in shared:
            shared["m"] = ctranslate2.models.Whisper(
                path, device="cuda", compute_type="float16", inter_threads=1
            )
        else:
            shared["m"].load_model()
        h["m"] = shared["m"]

    return {
        "raw-reuse": [reuse_load, raw_infer, raw_unload],
        "raw-load": [raw_load],
        "raw-unload": [raw_load, raw_unload],
        "raw-infer": [raw_load, raw_infer],
        "raw-infer-unload": [raw_load, raw_infer, raw_unload],
    }[variant]


def _child(variant: str, cycles: int, model: str, out: Path) -> int:
    steps = _steps(variant, model)
    in_threads = variant in ("kikitori-thread", "kikitori-async")
    mon = Monitor(out)
    mon.track_root("self", os.getpid())
    rows: list[dict[str, Any]] = []
    errors: list[str] = []
    with mon:
        time.sleep(3)
        for i in range(cycles):
            holder: dict[str, Any] = {}
            for step in steps:
                if in_threads:
                    failure: list[BaseException] = []

                    def target(
                        fn: Callable[[dict[str, Any]], None] = step,
                        h: dict[str, Any] = holder,
                        f: list[BaseException] = failure,
                    ) -> None:
                        try:
                            fn(h)
                        except Exception as exc:  # noqa: BLE001 - 轉交主執行緒回報
                            f.append(exc)

                    th = threading.Thread(target=target)
                    th.start()
                    th.join()
                    if failure:
                        raise failure[0]
                else:
                    step(holder)
            if "text" in holder and i == 0:
                errors.append(f"text sample: {holder['text'][:40]!r}")
            holder.clear()
            gc.collect()
            time.sleep(3)
            now = mon.now()
            row: dict[str, Any] = {"i": i}
            for k in KEYS:
                vals = [v for _, v in mon.series("self", k, now - 1.5, now)]
                row[k] = vals[-1] if vals else None
            rows.append(row)
            print(json.dumps(row), flush=True)
    result = {
        "variant": variant,
        "rows": rows,
        "notes": errors,
        "slopes_after_2": {
            k: r1(
                slope(
                    [(float(r["i"]), float(r[k])) for r in rows[2:] if r[k] is not None]
                ),
                2,
            )
            for k in KEYS
        },
    }
    print("RESULT " + json.dumps(result), flush=True)
    return 0


def run_all(cycles: int = 12, model: str = "small", only: str = "") -> int:
    """依序在子行程執行每種做法，印出每輪成長（MiB／輪）。`only` 以逗號列出要跑的做法。"""
    run_dir = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}-ct2-isolation"
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"results: {run_dir}", flush=True)
    summary: dict[str, Any] = {}
    wanted = [v for v in only.split(",") if v] or list(VARIANTS)
    for variant, extra in VARIANTS.items():
        if variant not in wanted:
            continue
        env = dict(os.environ, **extra)
        proc = subprocess.run(
            [
                sys.executable,
                "-X",
                "utf8",
                __file__,
                "--child",
                variant,
                str(cycles),
                model,
                str(run_dir / f"{variant}.ndjson"),
            ],
            env=env,
            capture_output=True,
            timeout=1800,
            check=False,
        )
        out = proc.stdout.decode("utf-8", "replace")
        (run_dir / f"{variant}.log").write_text(
            out + proc.stderr.decode("utf-8", "replace"), "utf-8"
        )
        line = next((x for x in out.splitlines() if x.startswith("RESULT ")), None)
        if proc.returncode != 0 or line is None:
            print(f"  {variant}: failed (exit {proc.returncode}); see {variant}.log")
            summary[variant] = {"failed": proc.returncode}
            continue
        res = json.loads(line[len("RESULT ") :])
        summary[variant] = res
        first, last = res["rows"][0], res["rows"][-1]
        print(
            f"  {variant:16s} slope/cycle {res['slopes_after_2']}; cycle 0 → {cycles - 1}: "
            f"private {first['private_mib']} → {last['private_mib']} MiB, "
            f"GPU {first['gpu_mib']} → {last['gpu_mib']} MiB, "
            f"threads {first['threads']} → {last['threads']} {res['notes']}",
            flush=True,
        )
    (run_dir / "summary.json").write_text(json.dumps(summary, indent=2), "utf-8")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    if len(sys.argv) == 6 and sys.argv[1] == "--child":
        sys.exit(_child(sys.argv[2], int(sys.argv[3]), sys.argv[4], Path(sys.argv[5])))
    if len(sys.argv) == 3 and sys.argv[1] == "--only":
        sys.exit(run_all(only=sys.argv[2]))
    sys.exit(run_all())
