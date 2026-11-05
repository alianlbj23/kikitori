"""ASR 基準測試：在本機量測各 Whisper 模型的載入時間、RTF、延遲、記憶體與 CER。

角色：暫時性實驗（不屬於正式程式碼），結果寫進同資料夾的 README.md 與
`docs/specs/asr.md` 的硬體等級推薦表。由 `menu.py` 呼叫；每個設定在獨立的子行程中
執行，讓峰值記憶體只反映該模型。

素材：`experiments/pipecat_vad/data/`（已被 git 忽略，只在本機）的動漫與 VTuber 片段，
從 16 kHz 的 `tracks/*.wav` 依 `ground_truth.json` 的樣本位置切出；參考文字來自
`raw/manifest.json`（只有動漫片段有）。另加兩段非語音（合成噪音音軌、2 秒數位靜音）
檢查幻覺過濾。
"""

import ctypes
import json
import os
import subprocess
import sys
import time
import unicodedata
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

import numpy as np
import numpy.typing as npt

from kikitori.asr import (
    FAST_DECODE,
    FINAL_DECODE,
    MODELS,
    ModelFile,
    ModelSpec,
    Transcript,
    load_transcriber,
    rejection_reason,
)

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA_DIR = ROOT / "experiments" / "pipecat_vad" / "data"
RESULTS_DIR = HERE / "results"
SAMPLE_RATE = 16_000
WORKER_TIMEOUT_SECS = 3600
NVIDIA_SMI_TIMEOUT_SECS = 10

# 只用於對照的日文專用模型（不在正式 registry：只支援日文，訓練資料授權待確認）
EXTRA_MODELS: dict[str, ModelSpec] = {
    "kotoba-whisper-v2.0": ModelSpec(
        id="kotoba-whisper-v2.0",
        repo_id="kotoba-tech/kotoba-whisper-v2.0-faster",
        revision="f44edd35eaeb2274e85ac7b31fb2c6f59ff1c4bc",
        files=(
            ModelFile(
                "config.json",
                2394,
                "a9306624f5ec14270a014b647e5c316b6e03a662c369758d1b90697a7b0655b9",
            ),
            ModelFile(
                "model.bin",
                1512927867,
                "60d2bc2e33de9d43f2745be09caefe1161acab670f6796d4a750d8d848382b36",
            ),
            ModelFile(
                "preprocessor_config.json",
                340,
                "7ccc62c6f2765af1f3b46c00c9b5894426835a05021c8b9c01eecb6dfb542711",
            ),
            ModelFile(
                "tokenizer.json",
                2481381,
                "f70c9740a90657b489cf05b0fa0605c1d497db542f11a70a8cc80a025c94c7d8",
            ),
            ModelFile(
                "vocabulary.json",
                1068114,
                "c69260f2ab26d659b7c398f9a2b2b48ed0df16c3b47d7326782fd9cba71690c1",
            ),
        ),
        languages=("ja",),
        n_mels=128,
        speed_tier="medium",
        license="Apache-2.0",
        approx_cpu_ram_mb=1786,
        approx_gpu_vram_mb=2170,
        approx_gpu_vram_int8_mb=1180,
        no_speech_reliable=False,
    )
}


def resolve_spec(model_id: str) -> ModelSpec:
    """registry 或對照用模型。"""
    if model_id in MODELS:
        return MODELS[model_id]
    if model_id in EXTRA_MODELS:
        return EXTRA_MODELS[model_id]
    raise KeyError(f"unknown model {model_id!r}")


@dataclass(frozen=True)
class Clip:
    """一段測試音訊。"""

    name: str
    audio: npt.NDArray[np.int16]
    reference: str | None
    kind: Literal["speech", "noise"]

    @property
    def seconds(self) -> float:
        return len(self.audio) / SAMPLE_RATE


def read_wav_int16(path: Path) -> npt.NDArray[np.int16]:
    """讀 16 kHz 單聲道 PCM16 WAV；格式不符就丟錯。"""
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
            1,
            2,
            SAMPLE_RATE,
        ):
            raise ValueError(f"{path} must be 16 kHz mono PCM16")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype=np.int16).copy()


def load_clips(data_dir: Path = DATA_DIR) -> list[Clip]:
    """依 ground_truth.json 從音軌切出各片段，附上參考文字。"""
    tracks = data_dir / "tracks"
    truth = json.loads((tracks / "ground_truth.json").read_text(encoding="utf-8"))
    if truth.get("sample_rate") != SAMPLE_RATE:
        raise ValueError("ground_truth.json must be 16 kHz")
    manifest_path = data_dir / "raw" / "manifest.json"
    references: dict[str, str] = {}
    if manifest_path.exists():
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        for items in manifest.values():
            for item in items:
                if isinstance(item.get("text"), str):
                    references[item["file"]] = item["text"]

    clips: list[Clip] = []
    for track in ("anime", "vtuber"):
        audio = read_wav_int16(tracks / f"{track}.wav")
        for seg in truth["tracks"][track]["segments"]:
            start, end = int(seg["start_sample"]), int(seg["end_sample"])
            if not 0 <= start < end <= len(audio):
                raise ValueError(f"bad segment {seg}")
            name = Path(seg["file"]).stem
            clips.append(
                Clip(
                    name, audio[start:end].copy(), references.get(seg["file"]), "speech"
                )
            )
    clips.append(
        Clip("synthetic_noise", read_wav_int16(tracks / "synthetic.wav"), None, "noise")
    )
    clips.append(
        Clip(
            "digital_silence", np.zeros(2 * SAMPLE_RATE, dtype=np.int16), None, "noise"
        )
    )
    return clips


def normalize_for_cer(text: str) -> str:
    """CER 用的正規化：NFKC、小寫、只留文字與數字（去掉標點與空白）。"""
    folded = unicodedata.normalize("NFKC", text).casefold()
    return "".join(ch for ch in folded if unicodedata.category(ch)[0] in "LN")


def edit_distance(a: str, b: str) -> int:
    """Levenshtein 距離（逐字元）。"""
    previous = list(range(len(b) + 1))
    for i, ca in enumerate(a, start=1):
        current = [i]
        for j, cb in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (ca != cb))
            )
        previous = current
    return previous[-1]


def corpus_cer(pairs: list[tuple[str, str]]) -> float:
    """(參考, 假設) 清單的整體 CER = 總編輯距離 ÷ 參考總字數。"""
    edits = sum(
        edit_distance(normalize_for_cer(r), normalize_for_cer(h)) for r, h in pairs
    )
    chars = sum(len(normalize_for_cer(r)) for r, _ in pairs)
    if chars == 0:
        raise ValueError("references are empty")
    return edits / chars


def peak_rss_mb() -> float:
    """目前行程的峰值常駐記憶體（MB）。"""
    if sys.platform == "win32":

        class Counters(ctypes.Structure):
            _fields_ = [
                ("cb", ctypes.c_ulong),
                ("PageFaultCount", ctypes.c_ulong),
                ("PeakWorkingSetSize", ctypes.c_size_t),
                ("WorkingSetSize", ctypes.c_size_t),
                ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPagedPoolUsage", ctypes.c_size_t),
                ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                ("PagefileUsage", ctypes.c_size_t),
                ("PeakPagefileUsage", ctypes.c_size_t),
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(Counters)
        kernel32 = ctypes.WinDLL("kernel32")
        psapi = ctypes.WinDLL("psapi")
        kernel32.GetCurrentProcess.restype = ctypes.c_void_p
        handle = kernel32.GetCurrentProcess()
        if not psapi.GetProcessMemoryInfo(
            ctypes.c_void_p(handle), ctypes.byref(counters), counters.cb
        ):
            raise OSError("GetProcessMemoryInfo failed")
        return counters.PeakWorkingSetSize / 2**20
    import resource

    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / 2**20 if sys.platform == "darwin" else peak / 2**10


def gpu_memory_used_mb() -> float | None:
    """整張 GPU 已用的記憶體（MB）；沒有 nvidia-smi 時回傳 None。

    Windows WDDM 模式下 nvidia-smi 不提供各行程用量，所以用整卡用量的前後差估計。
    """
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=NVIDIA_SMI_TIMEOUT_SECS,
            check=True,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    return float(first) if first.replace(".", "", 1).isdigit() else None


@dataclass(frozen=True)
class RunConfig:
    """一次量測的設定。"""

    model: str
    device: Literal["cpu", "cuda"]
    compute_type: str
    mode: Literal["fast", "final"]
    cpu_threads: int = 0

    @property
    def label(self) -> str:
        threads = (
            f" t{self.cpu_threads}" if self.device == "cpu" and self.cpu_threads else ""
        )
        return f"{self.model} {self.device} {self.compute_type}{threads} {self.mode}"


def run_config(
    config: RunConfig, cache_dir: Path, cuda_dir: Path | None
) -> dict[str, Any]:
    """在目前行程載入模型並跑完所有片段（由子行程呼叫）。"""
    clips = load_clips()
    options = FAST_DECODE if config.mode == "fast" else FINAL_DECODE
    gpu_before = gpu_memory_used_mb() if config.device == "cuda" else None
    rss_before = peak_rss_mb()

    start = time.perf_counter()
    transcriber = load_transcriber(
        resolve_spec(config.model),
        cache_dir=cache_dir,
        device=config.device,
        compute_type=config.compute_type,
        cpu_threads=config.cpu_threads,
        options=options,
        cuda_library_dirs=[cuda_dir] if cuda_dir else (),
    )
    load_secs = time.perf_counter() - start
    start = time.perf_counter()
    transcriber.warmup()
    warmup_secs = time.perf_counter() - start
    # 載入＋暖機後立刻量，縮短其他程式（例如 Ollama）改變 VRAM 用量的時間窗
    gpu_loaded = gpu_memory_used_mb() if config.device == "cuda" else None

    rows: list[dict[str, Any]] = []
    for clip in clips:
        language = "ja" if clip.kind == "speech" else None
        start = time.perf_counter()
        result = transcriber.transcribe(clip.audio, language=language)
        latency = time.perf_counter() - start
        detected = (
            transcriber.transcribe(clip.audio) if clip.kind == "speech" else result
        )
        rows.append(
            {
                "clip": clip.name,
                "kind": clip.kind,
                "seconds": clip.seconds,
                "latency": latency,
                "rtf": latency / clip.seconds,
                "text": result.text,
                "reference": clip.reference,
                "no_speech": result.no_speech_probability,
                "avg_logprob": result.avg_logprob,
                "rejected": rejection_reason(result),
                "detected_language": detected.language,
                "detected_probability": detected.language_probability,
            }
        )
    gpu_after = gpu_memory_used_mb() if config.device == "cuda" else None

    speech = [r for r in rows if r["kind"] == "speech"]
    with_ref = [(r["reference"], r["text"]) for r in speech if r["reference"]]
    latencies = sorted(r["latency"] for r in speech)
    return {
        "config": asdict(config),
        "label": config.label,
        "actual_device": transcriber.device,
        "load_secs": load_secs,
        "warmup_secs": warmup_secs,
        "rtf": sum(r["latency"] for r in speech) / sum(r["seconds"] for r in speech),
        "latency_median": latencies[len(latencies) // 2],
        "latency_max": latencies[-1],
        "cer_anime": corpus_cer(with_ref) if with_ref else None,
        "language_id_ja": sum(r["detected_language"] == "ja" for r in speech)
        / len(speech),
        "noise_rejected": sum(
            r["rejected"] is not None for r in rows if r["kind"] == "noise"
        ),
        "speech_rejected": sum(r["rejected"] is not None for r in speech),
        "peak_rss_mb": peak_rss_mb(),
        "rss_before_mb": rss_before,
        "gpu_load_delta_mb": (gpu_loaded - gpu_before)
        if gpu_loaded is not None and gpu_before is not None
        else None,
        "gpu_delta_mb": (gpu_after - gpu_before)
        if gpu_after is not None and gpu_before is not None
        else None,
        "rows": rows,
    }


def run_in_subprocess(
    config: RunConfig, cache_dir: Path, cuda_dir: Path | None
) -> dict[str, Any]:
    """在獨立子行程執行 `run_config`，讓峰值記憶體不受前一個模型影響。"""
    payload = json.dumps(
        {
            "config": asdict(config),
            "cache_dir": str(cache_dir),
            "cuda_dir": str(cuda_dir) if cuda_dir else None,
        }
    )
    proc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
        input=payload,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=WORKER_TIMEOUT_SECS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{config.label} failed:\n{proc.stderr[-2000:]}")
    return json.loads(proc.stdout.strip().splitlines()[-1])


def rescore(result: dict[str, Any]) -> dict[str, Any]:
    """用目前的 `rejection_reason()` 重新判定已存的結果（調整過濾門檻後不必重跑模型）。"""
    rows = []
    for row in result["rows"]:
        transcript = Transcript(
            text=row["text"],
            language="ja" if row["kind"] == "speech" else row["detected_language"],
            language_probability=1.0,
            no_speech_probability=row["no_speech"],
            avg_logprob=row["avg_logprob"],
        )
        rows.append({**row, "rejected": rejection_reason(transcript)})
    noise = [r for r in rows if r["kind"] == "noise"]
    speech = [r for r in rows if r["kind"] == "speech"]
    return {
        **result,
        "rows": rows,
        "noise_rejected": sum(r["rejected"] is not None for r in noise),
        "speech_rejected": sum(r["rejected"] is not None for r in speech),
    }


def collect_results(results_dir: Path = RESULTS_DIR) -> list[dict[str, Any]]:
    """讀取所有結果檔；同一設定只留最新一次，並以目前的過濾規則重新判定。"""
    latest: dict[str, dict[str, Any]] = {}
    for path in sorted(results_dir.glob("run-*.json")):
        for result in json.loads(path.read_text(encoding="utf-8")):
            if "gpu_load_delta_mb" not in result:
                continue  # 早期格式（沒有載入後 VRAM）不列入
            latest[result["label"]] = rescore(result)
    return list(latest.values())


def summary_row(result: dict[str, Any]) -> str:
    """一列 Markdown 表格。"""
    cer = result["cer_anime"]
    gpu = result["gpu_load_delta_mb"]
    return (
        f"| {result['label']} | {result['load_secs']:.1f} | {result['rtf']:.3f} "
        f"| {result['latency_median'] * 1000:.0f} | {result['latency_max'] * 1000:.0f} "
        f"| {'—' if cer is None else f'{cer:.3f}'} | {result['language_id_ja']:.2f} "
        f"| {result['noise_rejected']}/2 | {result['speech_rejected']} "
        f"| {result['peak_rss_mb']:.0f} | {'—' if gpu is None else f'{gpu:.0f}'} |"
    )


SUMMARY_HEADER = (
    "| 設定 | 載入 s | RTF | 延遲中位 ms | 延遲最大 ms | CER（動漫） | ja 偵測率 "
    "| 非語音被濾掉 | 語音被誤濾 | 峰值 RSS MB | VRAM 增量 MB（載入後） |\n"
    "|---|---|---|---|---|---|---|---|---|---|---|"
)


def _worker() -> None:
    request = json.loads(sys.stdin.read())
    config = RunConfig(**request["config"])
    cuda_dir = Path(request["cuda_dir"]) if request["cuda_dir"] else None
    result = run_config(config, Path(request["cache_dir"]), cuda_dir)
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        os.environ.setdefault("PYTHONIOENCODING", "utf-8")
        _worker()
    else:
        print("請用選單執行：uv run python -I experiments/asr_benchmark/menu.py")
