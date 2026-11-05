"""Experiment: use pipecat's LocalSmartTurnAnalyzerV3 standalone (no pipeline).

Role: feasibility check #4 — can Smart Turn v3 tell a finished utterance from one
cut off mid-sentence, on Japanese anime / VTuber voices?
Run with `python -I` because the tracks are derived from downloaded files.
"""

import asyncio
import json
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from pipecat.audio.turn.smart_turn.local_smart_turn_v3 import LocalSmartTurnAnalyzerV3

TRACKS_DIR = Path(__file__).resolve().parent / "data" / "tracks"
SAMPLE_RATE = 16000
CHUNK = 512
CUT_RATIO = 0.55  # "incomplete" = first 55 % of the clip, i.e. cut mid-sentence


def load_track(name: str) -> np.ndarray:
    audio, rate = sf.read(TRACKS_DIR / f"{name}.wav", dtype="int16")
    if rate != SAMPLE_RATE or audio.ndim != 1:
        raise ValueError(f"{name}.wav must be 16 kHz mono, got {rate} Hz {audio.shape}")
    return audio


def clips_from_truth() -> list[tuple[str, np.ndarray]]:
    truth = json.loads((TRACKS_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    clips: list[tuple[str, np.ndarray]] = []
    for track in ("anime", "vtuber"):
        audio = load_track(track)
        for seg in truth["tracks"][track]["segments"]:
            clips.append((seg["file"], audio[seg["start_sample"] : seg["end_sample"]]))
    return clips


def predict_direct(analyzer: LocalSmartTurnAnalyzerV3, clip: np.ndarray) -> float:
    """Path B: call the model directly on audio we sliced ourselves.

    _predict_endpoint is private but is a pure function of the samples, unlike the
    public path whose buffer is keyed on time.monotonic().
    """
    result = analyzer._predict_endpoint(clip.astype(np.float32) / 32768.0)
    return float(result["probability"])


async def predict_public(clip: np.ndarray, realtime: bool) -> float | None:
    """Path A: the public API, fed as a live stream would be (is_speech=True)."""
    analyzer = LocalSmartTurnAnalyzerV3(sample_rate=SAMPLE_RATE)
    analyzer.set_sample_rate(SAMPLE_RATE)
    for offset in range(0, len(clip) - CHUNK + 1, CHUNK):
        analyzer.append_audio(clip[offset : offset + CHUNK].tobytes(), is_speech=True)
        if realtime:
            await asyncio.sleep(CHUNK / SAMPLE_RATE)
    _state, metrics = await analyzer.analyze_end_of_turn()
    await analyzer.cleanup()
    return None if metrics is None else float(metrics.probability)


async def main() -> None:
    clips = clips_from_truth()
    analyzer = LocalSmartTurnAnalyzerV3(sample_rate=SAMPLE_RATE)
    analyzer.set_sample_rate(SAMPLE_RATE)

    print("Path B (direct _predict_endpoint): P(turn complete)")
    print(f"  {'clip':16s} {'secs':>5s}  {'full':>6s}  {'cut 55%':>7s}  ms/call")
    ok = 0
    for name, clip in clips:
        t0 = time.perf_counter()
        full = predict_direct(analyzer, clip)
        ms = 1000 * (time.perf_counter() - t0)
        cut = predict_direct(analyzer, clip[: int(len(clip) * CUT_RATIO)])
        ok += full > 0.5 and cut <= 0.5
        print(
            f"  {name:16s} {len(clip) / SAMPLE_RATE:5.2f}  {full:6.3f}  {cut:7.3f}  {ms:6.1f}"
        )
    print(f"  -> full>0.5 and cut<=0.5 on {ok}/{len(clips)} clips")

    # Path A on two clips only: real-time feeding takes as long as the audio.
    print("\nPath A (public append_audio + analyze_end_of_turn):")
    for name, clip in clips[:1] + clips[6:7]:
        cut = clip[: int(len(clip) * CUT_RATIO)]
        for realtime in (False, True):
            full_p = await predict_public(clip, realtime)
            cut_p = await predict_public(cut, realtime)
            mode = "real-time" if realtime else "fast    "
            print(f"  {name:16s} {mode}  full {full_p}  cut {cut_p}")
    await analyzer.cleanup()


if __name__ == "__main__":
    asyncio.run(main())
