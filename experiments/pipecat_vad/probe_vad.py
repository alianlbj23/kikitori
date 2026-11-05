"""Experiment: drive pipecat's SileroVADAnalyzer standalone, chunk by chunk.

Role: feasibility check #2 (per-chunk VAD state) and #3 (per-32 ms voice
probability) for using pipecat's VAD as kikitori's VAD, without any pipeline.
Run with `python -I` because the tracks are derived from downloaded files.
"""

import asyncio
import json
import time
from collections import Counter
from pathlib import Path

import numpy as np
import soundfile as sf
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams, VADState

TRACKS_DIR = Path(__file__).resolve().parent / "data" / "tracks"
SAMPLE_RATE = 16000
CHUNK = 512  # Silero's fixed window at 16 kHz = 32 ms


class RecordingSileroVAD(SileroVADAnalyzer):
    """Keeps the confidence Silero computed for each window.

    analyze_audio() only returns the state. Calling voice_confidence() a second
    time ourselves would advance Silero's recurrent state twice per window, so we
    capture the value from the single call the analyzer already makes.
    """

    def __init__(self, params: VADParams, auto_reset: bool = True) -> None:
        super().__init__(sample_rate=SAMPLE_RATE, params=params)
        self.confidences: list[float] = []
        self._auto_reset = auto_reset

    def _maybe_reset_model(self, confidence: float) -> None:
        # pipecat resets Silero's RNN every ~2 s of QUIET; in this experiment a reset
        # that lands on a speech onset suppressed detection for 1-2 s, so allow
        # turning it off to compare. (Overrides a private method: may break on upgrade.)
        if self._auto_reset:
            super()._maybe_reset_model(confidence)

    def voice_confidence(self, buffer: bytes) -> float:
        raw = super().voice_confidence(buffer)
        # Silero returns a 1-element array, not a float; the base analyzer only
        # compares it, so returning the original value keeps its behaviour intact.
        self.confidences.append(float(np.ravel(raw)[0]))
        return raw


def segments_from_states(states: list[VADState]) -> list[tuple[int, int]]:
    """Speech spans in samples: from the STARTING that got confirmed to the STOPPING
    that ended in QUIET (pipecat reports both transitions only after a delay)."""
    spans: list[tuple[int, int]] = []
    start: int | None = None
    stop: int | None = None
    prev = VADState.QUIET
    for i, state in enumerate(states):
        pos = i * CHUNK
        if state == VADState.STARTING and prev == VADState.QUIET:
            start = pos
        elif state == VADState.STOPPING and prev == VADState.SPEAKING:
            stop = pos
        elif state == VADState.SPEAKING and prev == VADState.STOPPING:
            stop = None
        elif (
            state == VADState.QUIET and prev == VADState.STOPPING and start is not None
        ):
            spans.append((start, stop if stop is not None else pos))
            start, stop = None, None
        elif state == VADState.QUIET and prev == VADState.STARTING:
            start = None
        prev = state
    if start is not None:
        spans.append((start, len(states) * CHUNK))
    return spans


async def run_track(
    name: str, params: VADParams, auto_reset: bool = True
) -> dict[str, object]:
    audio, rate = sf.read(TRACKS_DIR / f"{name}.wav", dtype="int16")
    if rate != SAMPLE_RATE or audio.ndim != 1:
        raise ValueError(f"{name}.wav must be 16 kHz mono, got {rate} Hz {audio.shape}")
    vad = RecordingSileroVAD(params, auto_reset)
    vad.set_sample_rate(SAMPLE_RATE)
    states: list[VADState] = []
    timings: list[float] = []
    for offset in range(0, len(audio) - CHUNK + 1, CHUNK):
        chunk = audio[offset : offset + CHUNK].tobytes()
        t0 = time.perf_counter()
        states.append(await vad.analyze_audio(chunk))
        timings.append(time.perf_counter() - t0)
    await vad.cleanup()

    if len(vad.confidences) != len(states):
        raise RuntimeError(
            f"expected one confidence per chunk, got {len(vad.confidences)} "
            f"for {len(states)} chunks"
        )
    return {
        "states": states,
        "confidences": vad.confidences,
        "segments": segments_from_states(states),
        "ms_per_chunk": 1000 * float(np.mean(timings)),
    }


def fmt_secs(sample: int) -> str:
    return f"{sample / SAMPLE_RATE:6.2f}s"


def report(name: str, result: dict[str, object], truth: dict[str, object]) -> None:
    states: list[VADState] = result["states"]  # type: ignore[assignment]
    conf = np.array(result["confidences"])
    counts = Counter(s.name for s in states)
    print(f"\n=== {name} ({len(states)} chunks of 32 ms)")
    print(f"  states seen : {dict(counts)}")
    print(
        f"  probability : min {conf.min():.3f}  max {conf.max():.3f}  "
        f"mean {conf.mean():.3f}  (one value per chunk)"
    )
    print(f"  speed       : {result['ms_per_chunk']:.2f} ms per 32 ms chunk")
    print("  VAD segments vs ground truth:")
    found = result["segments"]
    for i, (s, e) in enumerate(found):  # type: ignore[misc]
        print(f"    vad   #{i}: {fmt_secs(s)} -> {fmt_secs(e)}")
    for seg in truth["segments"]:  # type: ignore[index]
        print(
            f"    truth    : {fmt_secs(seg['start_sample'])} -> "
            f"{fmt_secs(seg['end_sample'])}  (gap after {seg['gap_after_secs']}s)"
        )


async def main() -> None:
    truth = json.loads((TRACKS_DIR / "ground_truth.json").read_text(encoding="utf-8"))
    summary: dict[str, object] = {}
    # Default stop_secs=0.2 splits at every breath; 0.6 s is closer to a subtitle line.
    configs: dict[str, tuple[VADParams, bool]] = {
        "default": (VADParams(), True),
        "stop0.6": (VADParams(stop_secs=0.6), True),
        "stop0.6-noreset": (VADParams(stop_secs=0.6), False),
    }
    runs = [("synthetic", "default"), ("synthetic", "stop0.6-noreset")] + [
        (track, cfg) for track in ("anime", "vtuber") for cfg in configs
    ]
    for name, cfg in runs:
        params, auto_reset = configs[cfg]
        result = await run_track(name, params, auto_reset)
        report(f"{name} [{cfg}]", result, truth["tracks"][name])
        summary[f"{name}/{cfg}"] = {
            "segments": result["segments"],
            "states": [s.name for s in result["states"]],  # type: ignore[attr-defined]
            "confidences": [round(c, 4) for c in result["confidences"]],  # type: ignore[attr-defined]
        }
    (TRACKS_DIR / "vad_result.json").write_text(json.dumps(summary), encoding="utf-8")


if __name__ == "__main__":
    asyncio.run(main())
