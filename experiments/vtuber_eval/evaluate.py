"""逐段跑 ASR，存下辨識文字、過濾結果與延遲（vtuber_eval 的第 3 步）。

角色：暫時性實驗，不屬於正式程式碼。由 `menu.py` 呼叫；每個設定（模型 × 裝置 × 精度
× 模式）在獨立子行程執行，逐段把結果附加到 `results/<label>.jsonl`，中斷後重跑會
跳過已完成的段落（GPU 與其他實驗共用，長時間執行可能被打斷）。

解碼一律用 app 的 `FAST_DECODE`（greedy）或 `FINAL_DECODE`（beam 5、溫度 fallback），
語言固定 `ja`，過濾用 app 目前的 `rejection_reason()`；`analyze.py` 計分時會以
當下的 `rejection_reason()` 重新判定（過濾規則改了不必重跑模型）。
"""

import importlib.util
import json
import os
import random
import subprocess
import sys
import time
import wave
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from types import ModuleType
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "data"
RESULTS = HERE / "results"
SAMPLE_RATE = 16_000
WORKER_TIMEOUT_SECS = 6 * 3600
NVIDIA_SMI_TIMEOUT_SECS = 10
GPU_SNAPSHOT_EVERY = 200
ORDER_SEED = 20261105


def _load_module(path: Path, name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _benchmark() -> ModuleType:
    """asr_benchmark 的 benchmark.py（kotoba-whisper-v2.0 對照規格、`resolve_spec()`）。"""
    return _load_module(
        ROOT / "experiments/asr_benchmark/benchmark.py", "asr_benchmark_benchmark"
    )


@dataclass(frozen=True)
class RunConfig:
    """一次評測的設定。"""

    model: str
    device: Literal["cpu", "cuda"]
    compute_type: str
    mode: Literal["fast", "final"]
    subset: Literal["full", "cpu"] = "full"
    cpu_threads: int = 0

    @property
    def label(self) -> str:
        """結果檔名用的標籤，例如 `large-v3_cuda_float16_final_full`。"""
        threads = f"_t{self.cpu_threads}" if self.device == "cpu" else ""
        return (
            f"{self.model}_{self.device}_{self.compute_type}{threads}"
            f"_{self.mode}_{self.subset}"
        )


def read_wav(path: Path) -> npt.NDArray[np.int16]:
    """讀 16 kHz 單聲道 PCM16 WAV；格式不符就丟錯。"""
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
            1,
            2,
            SAMPLE_RATE,
        ):
            raise ValueError(f"{path} must be 16 kHz mono PCM16")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").copy()


def load_manifest() -> list[dict[str, Any]]:
    """讀 `data/manifest.json` 的項目。"""
    path = DATA / "manifest.json"
    if not path.exists():
        raise FileNotFoundError(f"{path} 不存在：請先執行「建立評測集」")
    return json.loads(path.read_text(encoding="utf-8"))["items"]


def items_for(subset: str) -> list[dict[str, Any]]:
    """設定要跑的項目，以固定 seed 打亂順序。

    打亂是為了讓 GPU 被其他程式佔用的時段平均分散到各條件，延遲才能互相比較。
    """
    items = load_manifest()
    if subset == "cpu":
        items = [i for i in items if i["cpu_subset"]]
    random.Random(ORDER_SEED).shuffle(items)
    return items


def gpu_snapshot() -> dict[str, float] | None:
    """整張 GPU 的已用記憶體（MB）與使用率（%）；沒有 nvidia-smi 時回傳 None。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECS,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"nvidia-smi failed: {exc}", file=sys.stderr)
        return None
    fields = out.strip().splitlines()[0].split(",") if out.strip() else []
    try:
        return {"memory_used_mb": float(fields[0]), "utilization": float(fields[1])}
    except (IndexError, ValueError):
        print(f"unexpected nvidia-smi output: {out!r}", file=sys.stderr)
        return None


def done_ids(path: Path) -> set[str]:
    """已完成的段落 id（續跑用）；最後一行寫到一半就忽略該行。"""
    if not path.exists():
        return set()
    ids: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            ids.add(json.loads(line)["id"])
        except (json.JSONDecodeError, KeyError):
            print(f"skip malformed line in {path.name}", file=sys.stderr)
    return ids


def run_config(config: RunConfig, cuda_dir: Path | None) -> None:
    """載入模型並逐段辨識（由子行程呼叫）。結果逐行附加到 jsonl。"""
    import ctranslate2

    from kikitori.asr import FAST_DECODE, FINAL_DECODE, load_transcriber
    from kikitori.asr import rejection_reason as reject

    RESULTS.mkdir(exist_ok=True)
    out_path = RESULTS / f"{config.label}.jsonl"
    meta_path = RESULTS / f"{config.label}.meta.json"
    skip = done_ids(out_path)
    items = [i for i in items_for(config.subset) if i["id"] not in skip]
    if not items:
        print(f"{config.label}: already complete", flush=True)
        return

    spec = _benchmark().resolve_spec(config.model)
    options = FAST_DECODE if config.mode == "fast" else FINAL_DECODE
    start = time.perf_counter()
    transcriber = load_transcriber(
        spec,
        device=config.device,
        compute_type=config.compute_type,
        cpu_threads=config.cpu_threads,
        options=options,
        cuda_library_dirs=[cuda_dir] if cuda_dir else (),
    )
    transcriber.warmup()
    load_secs = time.perf_counter() - start
    snapshots = [{"done": 0, **(gpu_snapshot() or {})}]

    # 上次被中斷時檔尾可能是寫到一半、沒有換行的行：先補換行，新列才不會黏在它後面
    if out_path.exists() and out_path.stat().st_size > 0:
        with out_path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            needs_newline = f.read(1) != b"\n"
        if needs_newline:
            with out_path.open("ab") as f:
                f.write(b"\n")
    with out_path.open("a", encoding="utf-8") as out:
        for n, item in enumerate(items, start=1):
            audio = read_wav(DATA / item["wav"])
            t0 = time.perf_counter()
            result = transcriber.transcribe(audio, language="ja")
            latency = time.perf_counter() - t0
            row = {
                "id": item["id"],
                "set": item["set"],
                "condition": item["condition"],
                "block": item["block"],
                "source_id": item["source_id"],
                "seconds": item["seconds"],
                "latency": latency,
                "text": result.text,
                "language": result.language,
                "no_speech": result.no_speech_probability,
                "avg_logprob": result.avg_logprob,
                "rejected_at_run": reject(result),
            }
            out.write(json.dumps(row, ensure_ascii=False) + "\n")
            out.flush()
            if config.device == "cuda" and n % GPU_SNAPSHOT_EVERY == 0:
                snapshots.append({"done": n, **(gpu_snapshot() or {})})
            if n % 100 == 0:
                print(f"{config.label}: {n}/{len(items)}", flush=True)

    previous = (
        json.loads(meta_path.read_text(encoding="utf-8")) if meta_path.exists() else {}
    )
    meta = {
        "config": asdict(config),
        "label": config.label,
        "model_repo": spec.repo_id,
        "model_revision": spec.revision,
        "actual_device": transcriber.device,
        "actual_compute_type": transcriber.compute_type,
        "ctranslate2": ctranslate2.__version__,
        "decode": asdict(options),
        "runs": [
            *previous.get("runs", []),
            {
                "finished": datetime.now().isoformat(timespec="seconds"),
                "items": len(items),
                "load_secs": load_secs,
                "gpu_snapshots": snapshots,
            },
        ],
    }
    meta_path.write_text(json.dumps(meta, indent=1), encoding="utf-8")
    print(f"{config.label}: finished {len(items)} items", flush=True)


def run_in_subprocess(config: RunConfig, cuda_dir: Path | None) -> None:
    """在獨立子行程執行 `run_config`；失敗時丟 RuntimeError（附 stderr 結尾）。"""
    payload = json.dumps(
        {"config": asdict(config), "cuda_dir": str(cuda_dir) if cuda_dir else None}
    )
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
        input=payload,
        text=True,
        encoding="utf-8",
        stderr=subprocess.PIPE,
        timeout=WORKER_TIMEOUT_SECS,
        check=False,
        env=env,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{config.label} failed:\n{proc.stderr[-3000:]}")


def _worker() -> None:
    request = json.loads(sys.stdin.read())
    config = RunConfig(**request["config"])
    cuda_dir = Path(request["cuda_dir"]) if request["cuda_dir"] else None
    run_config(config, cuda_dir)


if __name__ == "__main__":
    # `-I` 會忽略 PYTHONIOENCODING；Windows 主控台預設編碼印不出中文，所以明確改成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.argv[1:] == ["--worker"]:
        _worker()
    else:
        print("請用選單執行：uv run python -I experiments/vtuber_eval/menu.py")
