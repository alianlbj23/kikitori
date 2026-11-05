"""建立評測集：YODAS 影片層級語言檢查、抽樣、MUSAN 混音（vtuber_eval 的第 2 步）。

角色：暫時性實驗的資料準備，不屬於正式程式碼。在專案環境執行（需要 kikitori 的
Whisper 做語言偵測、Silero VAD 量語音活躍段能量）。輸入 `prepare.py` 產生的
`data/candidates.json`，輸出 `data/manifest.json`（每段音訊的條件、block、參考文字、
混音的噪音檔／offset／增益）與 `data/mixed/`、`data/nonspeech/`、`data/clips/anime/` 的 WAV。

MUSAN 沿用 `experiments/silero_reset/data/musan/`（CC BY 4.0，見該處 ATTRIBUTION.md），
不另外下載；不存在時請先在 silero_reset 的選單下載。
"""

import importlib.util
import json
import random
import sys
import wave
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.asr import FAST_DECODE, load_transcriber
from kikitori.vad import WINDOW_SAMPLES, SileroVad

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
DATA = HERE / "data"
MUSAN = ROOT / "experiments" / "silero_reset" / "data" / "musan"
SAMPLE_RATE = 16_000
SEED = 20261105

# 抽樣規模（README「資料」一節）
YODAS_TARGET = 500
NOISY_TARGET = 200
CPU_YODAS_TARGET = 150
CPU_FLEURS_COUNT = 50
LID_PER_VIDEO = 6
LID_MIN_JA = 5  # 6 段中至少 5 段偵測為 ja 才保留整支影片
LID_MODEL = "medium"

# 混音條件：music 只用無人聲的流行類（fma／jamendo／rfm，ANNOTATIONS 第 3 欄 N），
# 較接近直播 BGM；noise 只用前景音效（排除 free-sound 的 Background noises 清單）當遊戲音效代理
MUSIC_SNRS = (20, 15, 10, 5, 0)
NOISE_SNRS = (10, 5)
MUSIC_DIRS = ("fma", "jamendo", "rfm")
VAD_SPEECH_PROBABILITY = 0.5
MIN_ACTIVE_WINDOWS = 3
NONSPEECH_MUSIC = 30
NONSPEECH_NOISE = 20
NONSPEECH_SECS = 6.0
# 非語音片段的音量：等於 YODAS 語音活躍段 RMS 中位數減 10 dB（即 BGM 在 10 dB SNR 的音量）
NONSPEECH_REL_DB = -10.0
MAX_NOISE_RETRIES = 10
MIN_NOISE_RMS = 1e-4


@dataclass(frozen=True)
class Item:
    """manifest 中的一段評測音訊。"""

    id: str
    set: str  # yodas／fleurs／reazon／anime／nonspeech
    condition: (
        str  # clean、music20…music0、noise10、noise5、nonspeech-music、nonspeech-noise
    )
    block: str
    reference: str
    seconds: float
    wav: str  # 相對於 data/
    source_id: str
    cpu_subset: bool = False
    mix: dict[str, Any] = field(default_factory=dict)


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


def write_wav(path: Path, samples: npt.NDArray[np.int16]) -> None:
    """寫 16 kHz 單聲道 PCM16 WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.astype("<i2").tobytes())


def capped_sample(
    groups: dict[str, list[str]], target: int, rng: random.Random
) -> list[str]:
    """每組最多取 c 個，c 取「總數不超過 target」的最大值；讓影片越多越好、不被長片主導。

    之後還不到 target 時，從還有剩的組各再取 1 個（組的順序隨機），直到 target。
    """
    shuffled = {g: rng.sample(sorted(v), len(v)) for g, v in sorted(groups.items())}
    cap = 1
    while sum(min(cap + 1, len(v)) for v in shuffled.values()) <= target:
        if cap + 1 > max(len(v) for v in shuffled.values()):
            break
        cap += 1
    chosen = [u for v in shuffled.values() for u in v[:cap]]
    leftovers = [g for g in sorted(shuffled) if len(shuffled[g]) > cap]
    rng.shuffle(leftovers)
    for g in leftovers[: max(0, target - len(chosen))]:
        chosen.append(shuffled[g][cap])
    return sorted(chosen)


def speech_active_power(samples: npt.NDArray[np.int16], vad: SileroVad) -> float:
    """Silero 判為語音（機率 ≥ 0.5）的 32 ms 視窗之平均功率（float，滿刻度 1.0）。

    活躍視窗少於 3 個時退回整段功率（極短或極小聲的片段）。
    """
    x = samples.astype(np.float32) / 32768.0
    vad.reset()
    active: list[npt.NDArray[np.float32]] = []
    for start in range(0, len(x) - WINDOW_SAMPLES + 1, WINDOW_SAMPLES):
        window = x[start : start + WINDOW_SAMPLES]
        if vad.probability(window) >= VAD_SPEECH_PROBABILITY:
            active.append(window)
    if len(active) < MIN_ACTIVE_WINDOWS:
        return float(np.mean(x**2))
    return float(np.mean(np.concatenate(active) ** 2))


@dataclass
class NoisePool:
    """MUSAN 噪音檔（延遲載入、快取）。"""

    files: list[Path]
    cache: dict[Path, npt.NDArray[np.float32]] = field(default_factory=dict)

    def load(self, path: Path) -> npt.NDArray[np.float32]:
        if path not in self.cache:
            self.cache[path] = read_wav(path).astype(np.float32) / 32768.0
        return self.cache[path]

    def segment(
        self, rng: random.Random, length: int
    ) -> tuple[npt.NDArray[np.float32], dict[str, Any]]:
        """隨機挑檔與 offset，取出 `length` 樣本（檔案太短就循環接續）；跳過近乎無聲的段。"""
        for _ in range(MAX_NOISE_RETRIES):
            path = rng.choice(self.files)
            audio = self.load(path)
            if len(audio) == 0:
                continue
            offset = rng.randrange(len(audio))
            reps = (offset + length) // len(audio) + 1
            seg = np.tile(audio, reps)[offset : offset + length]
            if float(np.sqrt(np.mean(seg**2))) >= MIN_NOISE_RMS:
                return seg, {
                    "noise_file": path.relative_to(MUSAN).as_posix(),
                    "offset_samples": offset,
                    "looped": bool(offset + length > len(audio)),
                }
        raise RuntimeError("could not find a non-silent noise segment")


def music_files() -> list[Path]:
    """無人聲的流行類 MUSAN music（ANNOTATIONS 第 3 欄為 N）。"""
    files: list[Path] = []
    for name in MUSIC_DIRS:
        folder = MUSAN / "music" / name
        for line in (folder / "ANNOTATIONS").read_text(encoding="utf-8").splitlines():
            cols = line.split()
            if len(cols) >= 3 and cols[2] == "N":
                path = folder / f"{cols[0]}.wav"
                if not path.exists():
                    raise FileNotFoundError(path)
                files.append(path)
    return sorted(files)


def noise_files() -> list[Path]:
    """MUSAN noise 的前景音效（排除 free-sound ANNOTATIONS 的 Background noises）。"""
    background = {
        line.strip()
        for line in (MUSAN / "noise/free-sound/ANNOTATIONS")
        .read_text(encoding="utf-8")
        .splitlines()[1:]
        if line.strip()
    }
    return sorted(
        p for p in (MUSAN / "noise").glob("*/*.wav") if p.stem not in background
    )


def mix_at_snr(
    speech: npt.NDArray[np.int16],
    noise: npt.NDArray[np.float32],
    speech_power: float,
    snr_db: float,
) -> tuple[npt.NDArray[np.int16], dict[str, Any]]:
    """以語音活躍段功率為基準混到指定 SNR；削波時整體縮小（SNR 不變）。"""
    if speech_power <= 0:
        raise ValueError("speech has no energy; cannot mix at a target SNR")
    noise_power = float(np.mean(noise**2))
    gain = float(np.sqrt(speech_power / (noise_power * 10 ** (snr_db / 10))))
    mixed = speech.astype(np.float32) / 32768.0 + gain * noise
    peak = float(np.max(np.abs(mixed)))
    scale = 0.99 / peak if peak > 0.99 else 1.0
    out = np.round(mixed * scale * 32767).astype(np.int16)
    return out, {"snr_db": snr_db, "noise_gain": gain, "clip_scale": scale}


def _load_sibling(path: Path, name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def video_language_check(
    candidates: list[dict[str, Any]], cuda_dirs: list[Path], device: str
) -> dict[str, dict[str, Any]]:
    """每支 YODAS 影片抽 6 段，以 Whisper medium 不指定語言辨識，統計偵測為 ja 的段數。

    結果快取在 `data/lid.json`；會影響抽樣，所以只做影片層級（不逐句挑模型辨識得好的）。
    """
    cache_path = DATA / "lid.json"
    if cache_path.exists():
        return json.loads(cache_path.read_text(encoding="utf-8"))
    by_video: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_video.setdefault(c["block"], []).append(c)
    transcriber = load_transcriber(
        LID_MODEL,
        device=device,  # type: ignore[arg-type]
        options=FAST_DECODE,
        cuda_library_dirs=cuda_dirs,
    )
    transcriber.warmup()
    rng = random.Random(f"{SEED}-lid")
    result: dict[str, dict[str, Any]] = {}
    for video in sorted(by_video):
        utts = sorted(by_video[video], key=lambda c: c["id"])
        picked = rng.sample(utts, min(LID_PER_VIDEO, len(utts)))
        languages = [
            transcriber.transcribe(read_wav(DATA / c["wav"])).language for c in picked
        ]
        result[video] = {"languages": languages, "ja": languages.count("ja")}
        print(f"  {video}: {languages}", flush=True)
    cache_path.write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result


def build(cuda_dirs: list[Path], device: str) -> list[Item]:
    """產生 manifest 的所有項目並寫出混音 WAV。"""
    if not (MUSAN / "music").is_dir():
        raise FileNotFoundError(
            f"{MUSAN} 不存在：請先在 experiments/silero_reset 的選單下載 MUSAN"
        )
    data = json.loads((DATA / "candidates.json").read_text(encoding="utf-8"))
    candidates: list[dict[str, Any]] = data["candidates"]
    yodas = [c for c in candidates if c["set"] == "yodas"]

    # 1. 影片層級語言檢查
    lid = video_language_check(yodas, cuda_dirs, device)
    kept_videos = {
        v for v, r in lid.items() if r["ja"] >= min(LID_MIN_JA, len(r["languages"]))
    }
    print(f"語言檢查：{len(kept_videos)}/{len(lid)} 支影片保留", flush=True)

    # 2. 抽樣（每支影片上限相同，讓 block 數最多）
    by_id = {c["id"]: c for c in candidates}
    groups: dict[str, list[str]] = {}
    for c in yodas:
        if c["block"] in kept_videos:
            groups.setdefault(c["block"], []).append(c["id"])
    rng = random.Random(f"{SEED}-sample")
    yodas_ids = capped_sample(groups, YODAS_TARGET, rng)
    chosen_groups: dict[str, list[str]] = {}
    for u in yodas_ids:
        chosen_groups.setdefault(by_id[u]["block"], []).append(u)
    noisy_ids = capped_sample(chosen_groups, NOISY_TARGET, rng)
    cpu_ids = set(capped_sample(chosen_groups, CPU_YODAS_TARGET, rng))
    fleurs = sorted(c["id"] for c in candidates if c["set"] == "fleurs")
    cpu_ids.update(rng.sample(fleurs, CPU_FLEURS_COUNT))

    items: list[Item] = []
    for c in candidates:
        if c["set"] == "yodas" and c["id"] not in yodas_ids:
            continue
        items.append(
            Item(
                id=c["id"],
                set=c["set"],
                condition="clean",
                block=c["block"],
                reference=c["reference"],
                seconds=c["seconds"],
                wav=c["wav"],
                source_id=c["id"],
                cpu_subset=c["id"] in cpu_ids,
            )
        )

    # 3. 動漫 6 段（沿用 asr_benchmark 的切法與參考文字）
    benchmark = _load_sibling(
        ROOT / "experiments/asr_benchmark/benchmark.py", "asr_benchmark_benchmark"
    )
    for clip in benchmark.load_clips():
        if clip.reference is None:
            continue
        rel = f"clips/anime/{clip.name}.wav"
        write_wav(DATA / rel, clip.audio)
        items.append(
            Item(
                clip.name,
                "anime",
                "clean",
                clip.name,
                clip.reference,
                clip.seconds,
                rel,
                clip.name,
                cpu_subset=True,
            )
        )

    # 4. 混音：每段固定一首 BGM 與 offset，五個 SNR 共用（SNR 曲線是成對比較）
    vad = SileroVad()
    music = NoisePool(music_files())
    noise = NoisePool(noise_files())
    print(
        f"MUSAN：無人聲 music {len(music.files)} 首、前景 noise {len(noise.files)} 個",
        flush=True,
    )
    speech_rms: list[float] = []
    for utt in noisy_ids:
        c = by_id[utt]
        speech = read_wav(DATA / c["wav"])
        power = speech_active_power(speech, vad)
        speech_rms.append(float(np.sqrt(power)))
        for kind, pool, snrs in (
            ("music", music, MUSIC_SNRS),
            ("noise", noise, NOISE_SNRS),
        ):
            seg, info = pool.segment(random.Random(f"{SEED}-{utt}-{kind}"), len(speech))
            for snr in snrs:
                mixed, gains = mix_at_snr(speech, seg, power, snr)
                condition = f"{kind}{snr}"
                rel = f"mixed/{condition}/{utt}.wav"
                write_wav(DATA / rel, mixed)
                items.append(
                    Item(
                        id=f"{utt}@{condition}",
                        set="yodas",
                        condition=condition,
                        block=c["block"],
                        reference=c["reference"],
                        seconds=c["seconds"],
                        wav=rel,
                        source_id=utt,
                        mix={
                            **info,
                            **gains,
                            "speech_active_rms": float(np.sqrt(power)),
                        },
                    )
                )

    # 5. 非語音（只有 BGM 或音效）：輸出任何文字就算幻覺
    level = float(np.median(speech_rms)) * 10 ** (NONSPEECH_REL_DB / 20)
    length = int(NONSPEECH_SECS * SAMPLE_RATE)
    for kind, pool, count in (
        ("music", music, NONSPEECH_MUSIC),
        ("noise", noise, NONSPEECH_NOISE),
    ):
        for i in range(count):
            seg, info = pool.segment(
                random.Random(f"{SEED}-nonspeech-{kind}-{i}"), length
            )
            gain = level / float(np.sqrt(np.mean(seg**2)))
            out = np.clip(seg * gain, -0.99, 0.99)
            name = f"nonspeech-{kind}-{i:02d}"
            rel = f"nonspeech/{name}.wav"
            write_wav(DATA / rel, np.round(out * 32767).astype(np.int16))
            items.append(
                Item(
                    name,
                    "nonspeech",
                    f"nonspeech-{kind}",
                    name,
                    "",
                    NONSPEECH_SECS,
                    rel,
                    name,
                    cpu_subset=True,
                    mix={**info, "rms": level},
                )
            )
    return items


def main(cuda_dirs: list[Path], device: str) -> None:
    """建立評測集並寫出 `data/manifest.json`。"""
    items = build(cuda_dirs, device)
    summary: dict[str, int] = {}
    for item in items:
        key = f"{item.set}/{item.condition}"
        summary[key] = summary.get(key, 0) + 1
    blocks = {i.block for i in items if i.set == "yodas"}
    (DATA / "manifest.json").write_text(
        json.dumps(
            {
                "seed": SEED,
                "music_snrs": MUSIC_SNRS,
                "noise_snrs": NOISE_SNRS,
                "summary": summary,
                "items": [asdict(i) for i in items],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"manifest：{summary}；YODAS 影片 {len(blocks)} 支", flush=True)


if __name__ == "__main__":
    # `-I` 會忽略 PYTHONIOENCODING；Windows 主控台預設編碼印不出中文，所以明確改成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.argv[1:2] == ["--worker"]:
        request = json.loads(sys.stdin.read())
        main([Path(p) for p in request["cuda_dirs"]], request["device"])
    else:
        print("請用選單執行：uv run python -I experiments/vtuber_eval/menu.py")
