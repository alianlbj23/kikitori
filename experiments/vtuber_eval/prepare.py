"""下載公開語料並切成 16 kHz 單聲道 WAV＋候選清單（vtuber_eval 的第 1 步）。

角色：暫時性實驗的資料準備，不屬於正式程式碼。讀 parquet／FLAC 需要 pyarrow 與
soundfile，這兩個不是專案依賴，所以本檔**只在隔離環境執行**（由 `menu.py` 呼叫）：

    uv run --isolated --no-project --with pyarrow==26.0.0 --with soundfile==0.14.0 \
        --with numpy==2.5.4 python -I experiments/vtuber_eval/prepare.py --worker

本檔不 import kikitori（隔離環境裡沒有）。輸出：
- `data/clips/<set>/*.wav`：16 kHz 單聲道 PCM16。
- `data/candidates.json`：每段的 id、組別、block（bootstrap 的抽樣單位）、參考文字、長度。
YODAS 只做「與模型無關」的字幕過濾；以影片為單位的語言檢查與最終抽樣在
`build_sets.py`（需要 kikitori 的 Whisper）。
"""

import hashlib
import importlib.util
import io
import json
import random
import sys
import tarfile
import urllib.request
import wave
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType

import numpy as np
import numpy.typing as npt

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CLIPS = DATA / "clips"
SAMPLE_RATE = 16_000
DOWNLOAD_TIMEOUT_SECS = 60
SEED = 20261105

# 固定 revision 與 SHA-256：重跑時拿到的是同一份資料
YODAS_REV = "0cad6a0ebec75e945fef92084052be69457fe9d1"
FLEURS_REV = "70bb2e84b976b7e960aa89f1c648e09c59f894dd"
REAZON_REV = "dd08bfb9dfc1cef4e4d0609fd78c3755d48b926f"
HF = "https://huggingface.co/datasets"


@dataclass(frozen=True)
class Remote:
    """一個要下載的檔案。"""

    url: str
    path: Path
    sha256: str


REMOTES: dict[str, list[Remote]] = {
    "yodas": [
        Remote(
            f"{HF}/espnet/yodas/resolve/{YODAS_REV}/data/ja000/text/00000000.txt",
            DATA / "yodas/ja000/text_00000000.txt",
            "f684c74721e8821394541a1879fe8c1e43f151f28e7ce76ad721406231b43b72",
        ),
        Remote(
            f"{HF}/espnet/yodas/resolve/{YODAS_REV}/data/ja000/duration/00000000.txt",
            DATA / "yodas/ja000/duration_00000000.txt",
            "c86879fd2e152b70224febe11dc7d3465f73dde3c4e8e1558a07bee3710e9269",
        ),
        Remote(
            f"{HF}/espnet/yodas/resolve/{YODAS_REV}/data/ja000/audio/00000000.tar.gz",
            DATA / "yodas/ja000/audio_00000000.tar.gz",
            "e95fcd0a9692dd738b2ca4d0838f292fc971ba1f9d185b638024257d8fade6a4",
        ),
    ],
    "fleurs": [
        Remote(
            f"{HF}/google/fleurs/resolve/{FLEURS_REV}/data/ja_jp/test.tsv",
            DATA / "fleurs/test.tsv",
            "5dd9643511437414681ad3f23508596c621cdf78978724a09f1f06fefe9d300b",
        ),
        Remote(
            f"{HF}/google/fleurs/resolve/{FLEURS_REV}/data/ja_jp/audio/test.tar.gz",
            DATA / "fleurs/test.tar.gz",
            "5de465fa7aaafc4e2c13aba44771550b8cd2dd29bb9b265daeb6d92ca8e0c136",
        ),
    ],
    "reazon": [
        Remote(
            f"{HF}/japanese-asr/ja_asr.reazonspeech_test/resolve/{REAZON_REV}"
            "/data/test-00000-of-00002.parquet",
            DATA / "reazonspeech_test/test-00000-of-00002.parquet",
            "c98bb0926f12780f3e218a948cadafcb754cc74244b81148b9c08aa19769af95",
        ),
    ],
}

# 抽樣規模（見 README「資料」一節）
YODAS_MAX_CANDIDATES_PER_VIDEO = 40
FLEURS_COUNT = 200
FLEURS_MAX_SECS = 25.0
REAZON_COUNT = 300

# YODAS 字幕過濾（與模型無關）：長度、字速、文字種類
YODAS_MIN_SECS, YODAS_MAX_SECS = 1.0, 10.0
YODAS_MIN_CPS, YODAS_MAX_CPS = 2.0, 14.0  # 正規化後字元數 ÷ 秒
YODAS_MIN_CHARS = 3
YODAS_MIN_JA_RATIO = 0.8
_MUSIC_MARKS = ("♪", "♫", "♬", "🎵", "🎶", "音楽", "BGM", "http")


def _load_textnorm() -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，改用檔案路徑載入同資料夾的模組
    spec = importlib.util.spec_from_file_location(
        "vtuber_eval_textnorm", HERE / "textnorm.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError("cannot load textnorm.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


textnorm = _load_textnorm()


@dataclass(frozen=True)
class Candidate:
    """一段候選音訊。"""

    id: str
    set: str  # yodas／fleurs／reazon
    block: str  # bootstrap 的抽樣單位（影片、句子 id 或單句）
    reference: str
    seconds: float
    wav: str  # 相對於 data/ 的路徑


def sha256_of(path: Path) -> str:
    """檔案的 SHA-256（分塊讀，不整檔載入）。"""
    digest = hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def fetch(remote: Remote) -> None:
    """下載到 `.part` 再改名；已存在且雜湊相符就跳過，不符就丟錯（不自動刪）。"""
    if remote.path.exists():
        actual = sha256_of(remote.path)
        if actual != remote.sha256:
            raise ValueError(
                f"{remote.path} sha256 {actual} != {remote.sha256}；請手動刪除後重跑"
            )
        return
    remote.path.parent.mkdir(parents=True, exist_ok=True)
    part = remote.path.with_name(remote.path.name + ".part")
    print(f"下載 {remote.url}", flush=True)
    with (
        urllib.request.urlopen(remote.url, timeout=DOWNLOAD_TIMEOUT_SECS) as response,
        part.open("wb") as out,
    ):
        while chunk := response.read(1 << 20):
            out.write(chunk)
    actual = sha256_of(part)
    if actual != remote.sha256:
        raise ValueError(f"{remote.url} sha256 {actual} != {remote.sha256}")
    part.replace(remote.path)


def write_wav(path: Path, samples: npt.NDArray[np.int16]) -> None:
    """寫 16 kHz 單聲道 PCM16 WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(SAMPLE_RATE)
        wav.writeframes(samples.astype("<i2").tobytes())


def decode_wav(data: bytes) -> npt.NDArray[np.int16]:
    """解 WAV bytes；必須是 16 kHz 單聲道 PCM16（YODAS 是），否則丟錯。"""
    with wave.open(io.BytesIO(data), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getframerate()) != (
            1,
            2,
            SAMPLE_RATE,
        ):
            raise ValueError("expected 16 kHz mono PCM16")
        return np.frombuffer(wav.readframes(wav.getnframes()), dtype="<i2").copy()


def decode_soundfile(data: bytes) -> npt.NDArray[np.int16]:
    """用 soundfile 解 FLAC／浮點 WAV（FLEURS、ReazonSpeech），轉成 int16。

    必須已是 16 kHz 單聲道；不做重新取樣，格式不符就丟錯。
    先讀成 float32 再自己縮放：libsndfile 把浮點 WAV 直接讀成 int16 時預設不縮放，
    −1–1 的值會被截成 0／±1（FLEURS 曾因此整批變成靜音）。
    """
    import soundfile as sf  # 只在隔離環境裡有

    samples, rate = sf.read(io.BytesIO(data), dtype="float32")
    if rate != SAMPLE_RATE or samples.ndim != 1:
        raise ValueError(f"expected 16 kHz mono, got {rate} Hz shape {samples.shape}")
    peak = float(np.max(np.abs(samples))) if len(samples) else 0.0
    if peak < 1e-3:
        raise ValueError(f"audio is (near) silent, peak {peak}")
    return np.round(np.clip(samples, -1.0, 32767 / 32768) * 32768).astype(np.int16)


def _is_japanese_char(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3040 <= code <= 0x30FF  # 平假名、片假名（含ー）
        or 0x4E00 <= code <= 0x9FFF  # CJK 統一漢字
        or 0x3400 <= code <= 0x4DBF
        or ch.isdigit()
        or ch == "々"
    )


def yodas_reject_reason(text: str, seconds: float) -> str | None:
    """YODAS 字幕的過濾規則；回傳 None 表示保留。規則與 README 一致。"""
    if not YODAS_MIN_SECS <= seconds <= YODAS_MAX_SECS:
        return "duration"
    if any(mark in text for mark in _MUSIC_MARKS):
        return "music_or_link"
    if any(b in text for b in "[]()（）［］【】<>〈〉"):
        return "bracket_annotation"
    norm = textnorm.normalize_basic(text)
    if len(norm) < YODAS_MIN_CHARS:
        return "too_short"
    if any(0xAC00 <= ord(c) <= 0xD7AF or 0x1100 <= ord(c) <= 0x11FF for c in norm):
        return "hangul"
    if not any(0x3040 <= ord(c) <= 0x30FF for c in norm):
        return "no_kana"
    if sum(_is_japanese_char(c) for c in norm) / len(norm) < YODAS_MIN_JA_RATIO:
        return "non_japanese_script"
    cps = len(norm) / seconds
    if not YODAS_MIN_CPS <= cps <= YODAS_MAX_CPS:
        return "chars_per_sec"
    return None


def prepare_yodas() -> list[Candidate]:
    """YODAS ja000 第 0 片：過濾字幕，每支影片最多留 40 段候選並切出 WAV。"""
    base = DATA / "yodas/ja000"
    texts: dict[str, str] = {}
    for line in (base / "text_00000000.txt").read_text(encoding="utf-8").splitlines():
        utt, _, text = line.partition(" ")
        texts[utt] = text.strip()
    durations: dict[str, float] = {}
    for line in (base / "duration_00000000.txt").read_text("utf-8").splitlines():
        utt, _, secs = line.partition(" ")
        durations[utt] = float(secs)

    reasons: dict[str, int] = {}
    by_video: dict[str, list[str]] = {}
    for utt, text in texts.items():
        reason = yodas_reject_reason(text, durations.get(utt, -1.0))
        if reason is not None:
            reasons[reason] = reasons.get(reason, 0) + 1
            continue
        by_video.setdefault(utt[:11], []).append(utt)
    rng = random.Random(SEED)
    chosen: set[str] = set()
    for video in sorted(by_video):
        utts = sorted(by_video[video])
        rng.shuffle(utts)
        chosen.update(utts[:YODAS_MAX_CANDIDATES_PER_VIDEO])
    print(
        f"YODAS：{len(texts)} 段、{len({u[:11] for u in texts})} 支影片；"
        f"過濾 {reasons}；候選 {len(chosen)} 段、{len(by_video)} 支影片",
        flush=True,
    )

    candidates: list[Candidate] = []
    with tarfile.open(base / "audio_00000000.tar.gz", "r:gz") as tar:
        for member in tar:
            utt = Path(member.name).stem
            if utt not in chosen or not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read {member.name}")
            samples = decode_wav(handle.read())
            rel = f"clips/yodas/{utt}.wav"
            write_wav(DATA / rel, samples)
            candidates.append(
                Candidate(
                    utt, "yodas", utt[:11], texts[utt], len(samples) / SAMPLE_RATE, rel
                )
            )
    if len(candidates) != len(chosen):
        raise ValueError(f"audio missing for {len(chosen) - len(candidates)} utts")
    return sorted(candidates, key=lambda c: c.id)


def prepare_fleurs() -> list[Candidate]:
    """FLEURS ja_jp test：隨機 200 段（≤ 25 s），block 為句子 id（同句多位朗讀者）。"""
    base = DATA / "fleurs"
    rows = []
    for line in (base / "test.tsv").read_text(encoding="utf-8").splitlines():
        cols = line.split("\t")
        if len(cols) < 7:
            raise ValueError(f"bad FLEURS row: {line[:80]!r}")
        sentence_id, file, raw, _, _, num_samples, _ = cols[:7]
        if int(num_samples) / SAMPLE_RATE <= FLEURS_MAX_SECS:
            rows.append((sentence_id, file, raw))
    rng = random.Random(SEED)
    picked = {row[1]: row for row in rng.sample(sorted(rows), FLEURS_COUNT)}

    candidates: list[Candidate] = []
    with tarfile.open(base / "test.tar.gz", "r:gz") as tar:
        for member in tar:
            name = Path(member.name).name
            if name not in picked or not member.isfile():
                continue
            handle = tar.extractfile(member)
            if handle is None:
                raise ValueError(f"cannot read {member.name}")
            samples = decode_soundfile(handle.read())
            sentence_id, _, raw = picked[name]
            utt = f"fleurs-{Path(name).stem}"
            rel = f"clips/fleurs/{utt}.wav"
            write_wav(DATA / rel, samples)
            candidates.append(
                Candidate(
                    utt,
                    "fleurs",
                    f"fleurs-{sentence_id}",
                    raw,
                    len(samples) / SAMPLE_RATE,
                    rel,
                )
            )
    if len(candidates) != FLEURS_COUNT:
        raise ValueError(f"FLEURS: found {len(candidates)} of {FLEURS_COUNT}")
    return sorted(candidates, key=lambda c: c.id)


def prepare_reazon() -> list[Candidate]:
    """ReazonSpeech test（kotoba 的評測鏡像）第 0 片：隨機 300 段。

    資料沒有節目或說話者 id（檔名是雜湊），所以每段自成一個 block。
    """
    import pyarrow.parquet as pq  # 只在隔離環境裡有

    table = pq.read_table(DATA / "reazonspeech_test/test-00000-of-00002.parquet")
    rng = random.Random(SEED)
    indices = sorted(rng.sample(range(table.num_rows), REAZON_COUNT))
    candidates: list[Candidate] = []
    for index in indices:
        row = table.slice(index, 1).to_pylist()[0]
        data = decode_soundfile(row["audio"]["bytes"])
        utt = f"reazon-{index:05d}"
        rel = f"clips/reazon/{utt}.wav"
        write_wav(DATA / rel, data)
        candidates.append(
            Candidate(
                utt, "reazon", utt, row["transcription"], len(data) / SAMPLE_RATE, rel
            )
        )
    return candidates


def main() -> None:
    """下載三個來源、切出 WAV、寫 `candidates.json`。"""
    for remotes in REMOTES.values():
        for remote in remotes:
            fetch(remote)
    candidates = [*prepare_yodas(), *prepare_fleurs(), *prepare_reazon()]
    out = DATA / "candidates.json"
    out.write_text(
        json.dumps(
            {
                "normalizer": textnorm.NORMALIZER_VERSION,
                "seed": SEED,
                "revisions": {
                    "espnet/yodas": YODAS_REV,
                    "google/fleurs": FLEURS_REV,
                    "japanese-asr/ja_asr.reazonspeech_test": REAZON_REV,
                },
                "candidates": [asdict(c) for c in candidates],
            },
            ensure_ascii=False,
            indent=1,
        ),
        encoding="utf-8",
    )
    counts: dict[str, int] = {}
    for c in candidates:
        counts[c.set] = counts.get(c.set, 0) + 1
    print(f"已寫入 {out}：{counts}", flush=True)


if __name__ == "__main__":
    # `-I` 會忽略 PYTHONIOENCODING；Windows 主控台預設編碼印不出中文，所以明確改成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.argv[1:] == ["--worker"]:
        main()
    else:
        print("請用選單執行：uv run python -I experiments/vtuber_eval/menu.py")
