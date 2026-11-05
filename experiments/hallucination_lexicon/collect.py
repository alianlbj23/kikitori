"""收集 Whisper 在非語音音訊上的輸出：產生非語音片段、判定 VAD、逐模型辨識。

角色：暫時性實驗（不屬於正式程式碼），結果整理成同資料夾的 `lexicon.json` 與
README.md，供 `kikitori.asr.filtering` 的依語言幻覺片語表使用。由 `menu.py` 呼叫。

流程：
1. `build_segments()`：從 MUSAN（噪音、無人聲的音樂）、數位靜音、白／粉紅噪音、
   模擬遊戲音效的噪音脈衝切出 1／2／4／6／10 s 片段，並用 app 的 Silero VAD
   （`Segmenter` 預設參數）判定每段會不會被當成人聲送進 ASR。片段寫進
   `results/segments.npz`（含 MUSAN 衍生音訊，git 忽略）。
2. `run_model()`：每個模型在獨立子行程中載入，對每段音訊只 encode 一次，
   再以 app 的 `FAST_DECODE`／`FINAL_DECODE` 對每個強制語言解碼，
   輸出逐列寫進 `results/raw-<模型>.jsonl`（可中斷後續跑）。

為什麼共用 encode：Whisper 的成本主要在 encoder，而 encoder 輸出與語言、解碼參數
無關；共用後 19 種語言 × 2 種模式只要 encode 一次。解碼走 `WhisperTranscriber`
的內部方法，與 `transcribe()` 相同；每次執行前先抽樣比對兩者結果一致才繼續。
"""

import io
import json
import subprocess
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.asr import (
    FAST_DECODE,
    FINAL_DECODE,
    SAMPLE_RATE,
    DecodeOptions,
    WhisperTranscriber,
    load_transcriber,
)
from kikitori.asr.audio import to_float32
from kikitori.asr.features import log_mel_spectrogram, split_windows
from kikitori.evaluation import noise, wav
from kikitori.vad import PauseEndpoint, Segmenter, SileroVad, UtteranceEnded

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
MUSAN_DIR = ROOT / "experiments" / "silero_reset" / "data" / "musan"
RESULTS_DIR = HERE / "results"
SEGMENTS_NPZ = RESULTS_DIR / "segments.npz"
SEGMENTS_JSON = RESULTS_DIR / "segments.json"
WORKER_TIMEOUT_SECS = 4 * 3600

SEED = 20261105
LENGTHS_SECS: tuple[int, ...] = (1, 2, 4, 6, 10)
# app 的 20 個字幕語言對應的 Whisper 語言碼（繁簡中文共用 zh）
LANGUAGES: tuple[str, ...] = (
    "ja", "en", "zh", "ko", "es", "fr", "de", "pt", "ru", "it",
    "ar", "hi", "id", "vi", "th", "tr", "pl", "nl", "uk",
)  # fmt: skip
MODES: dict[str, DecodeOptions] = {"fast": FAST_DECODE, "final": FINAL_DECODE}
# MUSAN 片段的響度（RMS dBFS）在這幾個值中隨機選，涵蓋小聲的背景到明顯的 BGM
MUSAN_LEVELS_DBFS: tuple[float, ...] = (-40.0, -30.0, -20.0)
# 原始音訊比這更小聲就當成近乎無聲，不拿來放大（放大後只剩底噪，與數位靜音重複）
MIN_SOURCE_DBFS = -70.0
PEAK_LIMIT = 0.98 * 32767


@dataclass(frozen=True)
class SegmentMeta:
    """一段非語音音訊的描述（音訊本身在 npz）。"""

    id: str
    kind: str  # silence／white／pink／burst／musan-noise／musan-music
    source: str
    seconds: int
    level_dbfs: float | None
    vad_passed: bool
    vad_speech_secs: float


def musan_music_without_vocals(musan_dir: Path = MUSAN_DIR) -> list[Path]:
    """MUSAN 音樂中 ANNOTATIONS 標為沒有人聲（第 3 欄為 N）的檔案。"""
    files: list[Path] = []
    for annotations in sorted((musan_dir / "music").glob("*/ANNOTATIONS")):
        for line in annotations.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) < 3 or parts[2] not in ("Y", "N"):
                continue
            path = annotations.parent / f"{parts[0]}.wav"
            if parts[2] == "N" and path.is_file():
                files.append(path)
    return files


def musan_noise(musan_dir: Path = MUSAN_DIR) -> list[Path]:
    """MUSAN 噪音的全部檔案（free-sound 與 sound-bible）。"""
    return sorted((musan_dir / "noise").glob("*/*.wav"))


def _scale(audio: npt.NDArray[np.float64], level_dbfs: float) -> npt.NDArray[np.int16]:
    rms = float(np.sqrt(np.mean(audio**2)))
    if rms == 0:
        return np.zeros(len(audio), dtype=np.int16)
    scaled = audio * (32768 * 10 ** (level_dbfs / 20) / rms)
    peak = float(np.max(np.abs(scaled)))
    if peak > PEAK_LIMIT:
        scaled *= PEAK_LIMIT / peak  # 不削波：寧可比目標小聲
    return np.round(scaled).astype(np.int16)


def burst_sfx(seconds: int, seed: int) -> npt.NDArray[np.int16]:
    """以噪音脈衝與短促的掃頻音近似遊戲音效（打擊、爆炸、UI 提示音）。

    底噪 −60 dBFS，上面疊 1 秒約 1–2 個事件；每個事件是隨機頻帶的濾波噪音或
    掃頻正弦，配指數衰減包絡，長 50–400 ms。
    """
    rng = np.random.default_rng(seed)
    n = seconds * SAMPLE_RATE
    out = noise.white_noise(n, -60.0, seed).astype(np.float64)
    events = max(1, int(rng.integers(seconds, 2 * seconds + 1)))
    for _ in range(events):
        length = int(rng.uniform(0.05, 0.4) * SAMPLE_RATE)
        start = int(rng.integers(0, max(1, n - length)))
        t = np.arange(length) / SAMPLE_RATE
        envelope = np.exp(-t / rng.uniform(0.02, 0.15))
        if rng.random() < 0.6:
            spectrum = np.fft.rfft(rng.standard_normal(length))
            freqs = np.fft.rfftfreq(length, 1 / SAMPLE_RATE)
            low = rng.uniform(40, 2000)
            high = low * rng.uniform(2, 8)
            spectrum[(freqs < low) | (freqs > high)] = 0
            event = np.fft.irfft(spectrum, n=length)
        else:
            f0, f1 = rng.uniform(300, 3000, size=2)
            phase = 2 * np.pi * np.cumsum(np.linspace(f0, f1, length)) / SAMPLE_RATE
            event = np.sin(phase)
        peak = float(np.max(np.abs(event))) or 1.0
        gain = 32767 * 10 ** (rng.uniform(-18, -3) / 20) / peak
        out[start : start + length] += event * envelope * gain
    return np.clip(np.round(out), -32768, 32767).astype(np.int16)


def _vad_decision(audio: npt.NDArray[np.int16], vad: SileroVad) -> tuple[bool, float]:
    """用 app 的 `Segmenter` 預設參數判斷這段會不會產生句子，回傳（是否通過, 人聲秒數）。"""
    segmenter = Segmenter(vad, endpoint=PauseEndpoint(0.6, 10.0))
    events = segmenter.process(audio) + segmenter.flush()
    ended = [e for e in events if isinstance(e, UtteranceEnded)]
    speech = sum(e.end_sample - e.start_sample for e in ended) / SAMPLE_RATE
    return bool(ended), speech


Clip = tuple[str, str, int, float | None, npt.NDArray[np.int16]]


def _musan_clips(
    files: list[Path], kind: str, per_length: int, rng: np.random.Generator
) -> list[Clip]:
    """每個長度切 `per_length` 段，每段隨機挑檔案與位置（同一檔可被挑中多次）。"""
    clips: list[Clip] = []
    for seconds in LENGTHS_SECS:
        n = seconds * SAMPLE_RATE
        made = attempts = 0
        while made < per_length:
            attempts += 1
            if attempts > 20 * per_length:
                raise ValueError(f"cannot cut {per_length} {kind} clips of {seconds}s")
            path = files[int(rng.integers(len(files)))]
            audio = wav.read_wav(path)
            if len(audio) < n:
                continue
            start = int(rng.integers(0, len(audio) - n + 1))
            piece = audio[start : start + n]
            if noise.rms_dbfs(piece) < MIN_SOURCE_DBFS:
                continue
            level = float(rng.choice(MUSAN_LEVELS_DBFS))
            source = f"{path.parent.name}/{path.stem}@{start / SAMPLE_RATE:.1f}s"
            clips.append(
                (kind, source, seconds, level, _scale(piece.astype(np.float64), level))
            )
            made += 1
    return clips


def _synthetic_clips() -> list[Clip]:
    clips: list[Clip] = []
    for seconds in LENGTHS_SECS:
        n = seconds * SAMPLE_RATE
        clips.append(("silence", "digital", seconds, None, np.zeros(n, dtype=np.int16)))
        for level in (-60.0, -45.0, -30.0):
            audio = noise.white_noise(n, level, SEED + seconds)
            clips.append(("white", "generated", seconds, level, audio))
        for level in (-50.0, -35.0):
            audio = noise.pink_noise(n, level, SEED + seconds)
            clips.append(("pink", "generated", seconds, level, audio))
        for k in range(2):
            audio = burst_sfx(seconds, SEED + 100 * k + seconds)
            clips.append(("burst", f"seed{k}", seconds, None, audio))
    return clips


def build_segments(pool_per_length: int, kept_per_length: int) -> list[SegmentMeta]:
    """產生非語音片段、判定 VAD，挑出要送 ASR 的片段寫進 `results/`。

    MUSAN 片段只有少數會被 VAD 當成人聲（本機約 2%），所以先切一個大池子
    （噪音、音樂各 `pool_per_length` 段／長度）全部跑 VAD，再保留「全部 VAD 通過的」
    ＋「每種、每個長度 `kept_per_length` 段沒通過的」。合成片段全部保留。

    Returns:
        保留的片段；另外把池子的 VAD 通過率寫進 `results/vad_pool.json`。
    """
    if kept_per_length < 1 or pool_per_length < kept_per_length:
        raise ValueError("need 1 <= kept_per_length <= pool_per_length")
    rng = np.random.default_rng(SEED)
    pool = _synthetic_clips()
    pool += _musan_clips(musan_noise(), "musan-noise", pool_per_length, rng)
    pool += _musan_clips(
        musan_music_without_vocals(), "musan-music", pool_per_length, rng
    )

    vad = SileroVad()
    decisions = [_vad_decision(clip[4], vad) for clip in pool]
    kept_failed: dict[tuple[str, int], int] = {}
    pool_stats: dict[str, dict[str, int]] = {}
    metas: list[SegmentMeta] = []
    arrays: dict[str, npt.NDArray[np.int16]] = {}
    for (kind, source, seconds, level, audio), (passed, speech) in zip(
        pool, decisions, strict=True
    ):
        stats = pool_stats.setdefault(
            f"{kind} {seconds}s", {"total": 0, "vad_passed": 0}
        )
        stats["total"] += 1
        stats["vad_passed"] += passed
        if kind.startswith("musan") and not passed:
            key = (kind, seconds)
            if kept_failed.get(key, 0) >= kept_per_length:
                continue
            kept_failed[key] = kept_failed.get(key, 0) + 1
        seg_id = f"s{len(metas):04d}"
        metas.append(SegmentMeta(seg_id, kind, source, seconds, level, passed, speech))
        arrays[seg_id] = audio
    RESULTS_DIR.mkdir(exist_ok=True)
    np.savez_compressed(SEGMENTS_NPZ, **arrays)
    SEGMENTS_JSON.write_text(
        json.dumps([asdict(m) for m in metas], ensure_ascii=False, indent=1), "utf-8"
    )
    (RESULTS_DIR / "vad_pool.json").write_text(
        json.dumps(pool_stats, indent=1), "utf-8"
    )
    return metas


def load_segments() -> tuple[list[SegmentMeta], dict[str, npt.NDArray[np.int16]]]:
    """讀取 `build_segments()` 的結果。"""
    metas = [
        SegmentMeta(**m) for m in json.loads(SEGMENTS_JSON.read_text(encoding="utf-8"))
    ]
    with np.load(SEGMENTS_NPZ) as data:
        arrays = {m.id: data[m.id].astype(np.int16) for m in metas}
    return metas, arrays


def raw_path(model_id: str) -> Path:
    """某模型的逐列輸出檔。"""
    return RESULTS_DIR / f"raw-{model_id}.jsonl"


def _encode(transcriber: WhisperTranscriber, audio: npt.NDArray[np.int16]) -> Any:
    model = transcriber._model
    windows = split_windows(log_mel_spectrogram(to_float32(audio), model.n_mels))
    if len(windows) != 1:
        raise ValueError("segments must fit in one 30 s window")
    return model.encode(transcriber._storage(windows[0][np.newaxis]), to_cpu=False)


def _decode(
    transcriber: WhisperTranscriber, encoded: Any, language: str, options: DecodeOptions
) -> tuple[str, float, float]:
    transcriber.options = options
    prompt = transcriber._build_prompt(language, [])
    decoded = transcriber._generate_with_fallback(encoded, prompt)
    return decoded.text, decoded.no_speech_prob, decoded.avg_logprob


def _check_equivalence(
    transcriber: WhisperTranscriber, arrays: dict[str, npt.NDArray[np.int16]]
) -> None:
    """抽樣確認共用 encode 的解碼與 `transcribe()` 結果相同（避免內部 API 變動而不自知）。"""
    for seg_id in list(arrays)[:4:3]:
        encoded = _encode(transcriber, arrays[seg_id])
        for language in ("ja", "en"):
            for options in MODES.values():
                text, no_speech, logprob = _decode(
                    transcriber, encoded, language, options
                )
                transcriber.options = options
                ref = transcriber.transcribe(arrays[seg_id], language=language)
                if (
                    ref.text != text
                    or abs(ref.no_speech_probability - no_speech) > 1e-4
                    or abs(ref.avg_logprob - logprob) > 1e-3
                ):
                    raise RuntimeError(
                        f"shared-encode decode differs from transcribe() on {seg_id} "
                        f"{language}: {text!r} vs {ref.text!r}"
                    )


def _complete_segments(out: Path) -> set[str]:
    """已完整跑完的片段；殘缺的列（中斷時寫到一半）從檔案移除，讓它們重跑。"""
    if not out.exists():
        return set()
    rows: list[dict[str, Any]] = []
    for line in out.read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # 中斷時寫到一半的最後一列，丟掉後重跑該段
    expected = len(MODES) * len(LANGUAGES)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["segment"]] = counts.get(row["segment"], 0) + 1
    done = {seg for seg, n in counts.items() if n == expected}
    kept = [row for row in rows if row["segment"] in done]
    if len(kept) != len(rows):
        out.write_text(
            "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in kept), "utf-8"
        )
    return done


def run_model(
    model_id: str, device: str, compute_type: str, cuda_dir: Path | None
) -> int:
    """在目前行程跑完一個模型的所有片段 × 語言 × 模式；已完成的片段略過。回傳新寫入的列數。"""
    metas, arrays = load_segments()
    out = raw_path(model_id)
    done = _complete_segments(out)
    transcriber = load_transcriber(
        model_id,
        device=device,
        compute_type=compute_type,
        options=FAST_DECODE,
        cuda_library_dirs=[cuda_dir] if cuda_dir else (),
    )
    transcriber.warmup()
    _check_equivalence(transcriber, arrays)
    written = 0
    started = time.perf_counter()
    # 以固定的亂數順序跑：中途停下時，已完成的部分仍是全部片段的隨機子樣本
    order = np.random.default_rng(SEED).permutation(len(metas))
    with out.open("a", encoding="utf-8") as handle:
        for number, meta in enumerate((metas[int(i)] for i in order), start=1):
            if meta.id in done:
                continue
            seg_start = time.perf_counter()
            encoded = _encode(transcriber, arrays[meta.id])
            lines: list[str] = []
            for mode, options in MODES.items():
                for language in LANGUAGES:
                    text, no_speech, logprob = _decode(
                        transcriber, encoded, language, options
                    )
                    row = {
                        "segment": meta.id,
                        "model": model_id,
                        "device": transcriber.device,
                        "mode": mode,
                        "language": language,
                        "text": text,
                        "no_speech": no_speech,
                        "avg_logprob": logprob,
                    }
                    lines.append(json.dumps(row, ensure_ascii=False) + "\n")
            # 一段的所有列一次寫入，中途被中斷時比較不會留下半段
            handle.write("".join(lines))
            handle.flush()
            written += len(lines)
            print(
                f"{model_id} {number}/{len(metas)} {meta.id} {meta.kind} {meta.seconds}s "
                f"{time.perf_counter() - seg_start:.1f}s（累計 {time.perf_counter() - started:.0f}s）",
                file=sys.stderr,
                flush=True,
            )
    return written


def run_in_subprocess(
    model_id: str, device: str, compute_type: str, cuda_dir: Path | None
) -> None:
    """在獨立子行程執行 `run_model`（CUDA 狀態與記憶體不互相影響）；進度直接顯示。"""
    payload = json.dumps(
        {
            "model": model_id,
            "device": device,
            "compute_type": compute_type,
            "cuda_dir": str(cuda_dir) if cuda_dir else None,
        }
    )
    proc = subprocess.run(
        [sys.executable, "-I", str(Path(__file__).resolve()), "--worker"],
        input=payload,
        text=True,
        encoding="utf-8",
        timeout=WORKER_TIMEOUT_SECS,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"{model_id} worker failed (exit {proc.returncode})")


def _worker() -> None:
    # `-I` 會忽略 PYTHONIOENCODING；進度訊息含中文，Windows 主控台預設字碼頁會變亂碼
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8")
    request = json.loads(sys.stdin.read())
    cuda_dir = Path(request["cuda_dir"]) if request["cuda_dir"] else None
    written = run_model(
        request["model"], request["device"], request["compute_type"], cuda_dir
    )
    print(f"{request['model']}: wrote {written} rows", file=sys.stderr)


if __name__ == "__main__":
    if sys.argv[1:] == ["--worker"]:
        _worker()
    else:
        print(
            "請用選單執行：uv run python -I experiments/hallucination_lexicon/menu.py"
        )
