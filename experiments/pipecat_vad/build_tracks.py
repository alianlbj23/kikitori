"""Experiment helper: turn downloaded clips into 16 kHz mono test tracks with ground truth.

Role: produces the inputs for probe_vad.py / probe_smart_turn.py. Each track is clips
joined by known silences, so the true speech segment positions (in samples) are known.
Run with `python -I` because it parses downloaded (untrusted) files.
"""

import json
from pathlib import Path

import numpy as np
import soundfile as sf
import soxr

SAMPLE_RATE = 16000
HERE = Path(__file__).resolve().parent
RAW_DIR = HERE / "data" / "raw"
OUT_DIR = HERE / "data" / "tracks"

# Gap after each clip, in seconds. 0.15 s is shorter than pipecat's default
# stop_secs (0.2 s), so those two clips should come out as one segment.
GAPS_SECS = [1.0, 0.15, 1.0, 0.5, 1.0, 1.5]
LEAD_IN_SECS = 1.0


def load_clip(path: Path) -> np.ndarray:
    data, rate = sf.read(path, dtype="float32", always_2d=True)
    mono = data.mean(axis=1)
    if rate != SAMPLE_RATE:
        mono = soxr.resample(mono, rate, SAMPLE_RATE)
    return mono.astype(np.float32)


def to_int16(audio: np.ndarray) -> np.ndarray:
    return (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)


def silence(secs: float) -> np.ndarray:
    return np.zeros(int(secs * SAMPLE_RATE), dtype=np.float32)


def build_track(name: str, clip_files: list[str]) -> dict[str, object]:
    parts = [silence(LEAD_IN_SECS)]
    cursor = len(parts[0])
    segments: list[dict[str, object]] = []
    for file, gap in zip(clip_files, GAPS_SECS, strict=False):
        clip = load_clip(RAW_DIR / file)
        segments.append(
            {
                "file": file,
                "start_sample": cursor,
                "end_sample": cursor + len(clip),
                "gap_after_secs": gap,
            }
        )
        parts += [clip, silence(gap)]
        cursor += len(clip) + int(gap * SAMPLE_RATE)
    audio = np.concatenate(parts)
    sf.write(OUT_DIR / f"{name}.wav", to_int16(audio), SAMPLE_RATE, subtype="PCM_16")
    return {"samples": len(audio), "segments": segments}


def build_synthetic() -> dict[str, object]:
    # Negative control: nothing here is a human voice, so VAD should stay QUIET.
    rng = np.random.default_rng(0)
    t = np.arange(2 * SAMPLE_RATE) / SAMPLE_RATE
    audio = np.concatenate(
        [
            silence(2.0),
            0.5 * np.sin(2 * np.pi * 440 * t).astype(np.float32),
            0.1 * rng.standard_normal(SAMPLE_RATE).astype(np.float32),
            silence(1.0),
        ]
    )
    sf.write(OUT_DIR / "synthetic.wav", to_int16(audio), SAMPLE_RATE, subtype="PCM_16")
    return {"samples": len(audio), "segments": []}


def build_turn_samples(clip_file: str) -> dict[str, str]:
    # Smart Turn input: the same utterance whole vs. cut mid-sentence.
    clip = load_clip(RAW_DIR / clip_file)
    cut = clip[: int(len(clip) * 0.55)]
    sf.write(OUT_DIR / "turn_complete.wav", to_int16(clip), SAMPLE_RATE)
    sf.write(OUT_DIR / "turn_incomplete.wav", to_int16(cut), SAMPLE_RATE)
    return {"complete": "turn_complete.wav", "incomplete": "turn_incomplete.wav"}


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    manifest = json.loads((RAW_DIR / "manifest.json").read_text(encoding="utf-8"))
    truth = {
        "sample_rate": SAMPLE_RATE,
        "tracks": {
            "synthetic": build_synthetic(),
            "anime": build_track("anime", [c["file"] for c in manifest["anime"]]),
            "vtuber": build_track("vtuber", [c["file"] for c in manifest["vtuber"]]),
        },
        "turn_samples": build_turn_samples(manifest["anime"][2]["file"]),
    }
    (OUT_DIR / "ground_truth.json").write_text(
        json.dumps(truth, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for name, track in truth["tracks"].items():
        secs = track["samples"] / SAMPLE_RATE
        print(f"{name}: {secs:.2f}s, {len(track['segments'])} clips")


if __name__ == "__main__":
    main()
