"""Segmenter 的測試：以腳本化機率的假模型驗證事件邏輯、句尾判斷與重置策略。"""

import itertools
import unittest

import numpy as np

from kikitori.vad.endpoint import PauseEndpoint
from kikitori.vad.model import WINDOW_SAMPLES, AudioWindow
from kikitori.vad.segmenter import (
    ResetPolicy,
    Segmenter,
    SegmentEvent,
    UtteranceAudio,
    UtteranceEnded,
    UtteranceStarted,
)

W = WINDOW_SAMPLES


class ScriptedModel:
    """依序回傳預先給定的機率；記錄每次 reset 發生在第幾個視窗之前。"""

    def __init__(self, probs: list[float]) -> None:
        self.probs = list(probs)
        self.calls = 0
        self.resets: list[int] = []

    def probability(self, window: AudioWindow) -> float:
        if window.shape != (W,):
            raise AssertionError(f"segmenter fed a window of shape {window.shape}")
        if self.calls >= len(self.probs):
            raise AssertionError(
                f"model called {self.calls + 1} times, script too short"
            )
        p = self.probs[self.calls]
        self.calls += 1
        return p

    def reset(self) -> None:
        self.resets.append(self.calls)


def numbered_audio(num_samples: int) -> np.ndarray:
    # 每個樣本值不同，才能驗證事件裡的音訊位置正確。
    return (np.arange(num_samples) % 30000).astype(np.int16)


def run(
    probs: list[float],
    chunk: int = W,
    extra_samples: int = 0,
    flush: bool = False,
    reset_policy: ResetPolicy = ResetPolicy.STREAM_START,
    **kwargs: object,
) -> tuple[list[SegmentEvent], ScriptedModel, np.ndarray]:
    model = ScriptedModel(probs)
    seg = Segmenter(model, reset_policy=reset_policy, **kwargs)  # type: ignore[arg-type]
    audio = numbered_audio(len(probs) * W + extra_samples)
    events: list[SegmentEvent] = []
    for i in range(0, len(audio), chunk):
        events += seg.process(audio[i : i + chunk])
    if flush:
        events += seg.flush()
    return events, model, audio


def kinds(events: list[SegmentEvent]) -> list[str]:
    return [type(e).__name__ for e in events]


def utterance_audio(events: list[SegmentEvent]) -> tuple[int, np.ndarray]:
    chunks = [e for e in events if isinstance(e, UtteranceAudio)]
    for prev, nxt in itertools.pairwise(chunks):
        if prev.start_sample + len(prev.audio) != nxt.start_sample:
            raise AssertionError("UtteranceAudio chunks are not contiguous")
    return chunks[0].start_sample, np.concatenate([c.audio for c in chunks])


class BasicEventsTest(unittest.TestCase):
    def test_start_audio_then_pause_end(self) -> None:
        probs = [0.0] * 10 + [0.9] * 20 + [0.0] * 25
        events, _, audio = run(probs)
        # 候選起點在視窗 10（樣本 5120）；累積 250 ms（4000 樣本）在視窗 17 結束時
        # （樣本 9216）確認，起點再往前含 200 ms（3200 樣本）前置緩衝。
        self.assertEqual(events[0], UtteranceStarted(start_sample=1920))
        self.assertIsInstance(events[1], UtteranceAudio)
        self.assertEqual(events[1].start_sample, 1920)
        self.assertEqual(len(events[1].audio), 9216 - 1920)
        # 靜音從視窗 30（樣本 15360）開始，達 0.6 s（9600 樣本）在視窗 48 結束時結束。
        self.assertEqual(events[-1], UtteranceEnded(1920, 25088, "pause"))
        self.assertEqual(kinds(events[2:-1]), ["UtteranceAudio"] * (48 - 17))
        start, joined = utterance_audio(events)
        np.testing.assert_array_equal(joined, audio[start:25088])

    def test_audio_is_read_only_copy(self) -> None:
        events, _, _ = run([0.9] * 10)
        chunk = next(e for e in events if isinstance(e, UtteranceAudio))
        with self.assertRaises(ValueError):
            chunk.audio[0] = 1

    def test_short_speech_does_not_start(self) -> None:
        events, _, _ = run([0.0] * 5 + [0.9] * 7 + [0.0] * 30)
        self.assertEqual(events, [])

    def test_speech_reaching_min_length_starts(self) -> None:
        events, _, _ = run([0.0] * 5 + [0.9] * 8 + [0.0] * 30)
        self.assertEqual(kinds(events)[0], "UtteranceStarted")
        self.assertEqual(kinds(events)[-1], "UtteranceEnded")

    def test_pre_roll_is_clamped_at_stream_start(self) -> None:
        events, _, _ = run([0.9] * 10)
        self.assertEqual(events[0], UtteranceStarted(start_sample=0))

    def test_second_utterance_does_not_overlap_first(self) -> None:
        # 第一句在視窗 27 結束（樣本 14336）；第二句候選起點緊接著，前置緩衝不可越過上一句。
        probs = [0.9] * 8 + [0.0] * 19 + [0.9] * 10
        events, _, _ = run(probs, pre_roll_ms=200)
        ends = [e for e in events if isinstance(e, UtteranceEnded)]
        starts = [e for e in events if isinstance(e, UtteranceStarted)]
        self.assertEqual(ends[0].end_sample, 27 * W)
        self.assertEqual(starts[1].start_sample, 27 * W)


class HysteresisTest(unittest.TestCase):
    def test_between_thresholds_does_not_start(self) -> None:
        events, _, _ = run([0.45] * 30)
        self.assertEqual(events, [])

    def test_between_thresholds_keeps_candidate(self) -> None:
        events, _, _ = run([0.9] * 3 + [0.4] * 5 + [0.0] * 30)
        self.assertEqual(kinds(events)[0], "UtteranceStarted")

    def test_low_probability_drops_candidate(self) -> None:
        events, _, _ = run([0.9] * 4 + [0.2] + [0.9] * 4 + [0.0] * 30)
        self.assertEqual(events, [])

    def test_between_thresholds_does_not_start_pause(self) -> None:
        events, _, _ = run([0.9] * 10 + [0.4] * 40)
        self.assertNotIn("UtteranceEnded", kinds(events))

    def test_between_thresholds_does_not_clear_pause(self) -> None:
        # 靜音從視窗 10 開始；中間 0.4 不打斷，所以仍在視窗 28 結束時滿 0.6 s。
        probs = [0.9] * 10 + [0.1] * 5 + [0.4] * 5 + [0.1] * 15
        events, _, _ = run(probs)
        self.assertEqual(events[-1], UtteranceEnded(0, 29 * W, "pause"))

    def test_speech_clears_pause(self) -> None:
        probs = [0.9] * 10 + [0.1] * 15 + [0.9] + [0.1] * 25
        events, _, _ = run(probs)
        self.assertEqual(events[-1], UtteranceEnded(0, 45 * W, "pause"))

    def test_custom_threshold_moves_neg_threshold(self) -> None:
        # threshold 0.7 → neg_threshold 0.55：0.6 屬於中間帶，不會開始靜音。
        events, _, _ = run([0.8] * 10 + [0.6] * 30, threshold=0.7)
        self.assertNotIn("UtteranceEnded", kinds(events))
        events, _, _ = run([0.8] * 10 + [0.5] * 30, threshold=0.7)
        self.assertIn("UtteranceEnded", kinds(events))


class FlushTest(unittest.TestCase):
    def test_flush_ends_utterance_with_leftover_samples(self) -> None:
        events, _, audio = run([0.9] * 20, extra_samples=100, flush=True)
        self.assertEqual(events[-1], UtteranceEnded(0, 20 * W + 100, "flush"))
        self.assertEqual(len(events[-2].audio), 100)
        _, joined = utterance_audio(events)
        np.testing.assert_array_equal(joined, audio)

    def test_flush_without_utterance_returns_nothing(self) -> None:
        events, _, _ = run([0.0] * 5, extra_samples=10, flush=True)
        self.assertEqual(events, [])

    def test_flush_starts_new_stream(self) -> None:
        model = ScriptedModel([0.9] * 20)
        seg = Segmenter(model, reset_policy=ResetPolicy.STREAM_START)
        seg.process(numbered_audio(10 * W))
        seg.flush()
        events = seg.process(numbered_audio(10 * W))
        self.assertEqual(events[0], UtteranceStarted(start_sample=0))


class BatchSizeTest(unittest.TestCase):
    def test_events_identical_for_any_chunking(self) -> None:
        probs = [0.0] * 7 + [0.9] * 30 + [0.2] * 25 + [0.8] * 9 + [0.4] * 3 + [0.0] * 30
        reference, _, _ = run(probs, chunk=W, extra_samples=77, flush=True)
        self.assertGreater(len(reference), 4)
        for chunk in (160, 1000, 1, 4096):
            with self.subTest(chunk=chunk):
                events, _, _ = run(probs, chunk=chunk, extra_samples=77, flush=True)
                self.assertEqual(events, reference)


class ConstructionTest(unittest.TestCase):
    def test_default_reset_policy_is_after_long_non_speech(self) -> None:
        # 評測後選定的預設值（見 experiments/silero_reset/README.md）。
        model = ScriptedModel([0.0] * 130)
        seg = Segmenter(model)
        seg.process(numbered_audio(130 * W))
        self.assertEqual(model.resets, [0, 63, 126])

    def test_reset_policy_must_be_enum(self) -> None:
        for bad in (None, "stream_start", 1):
            with self.subTest(bad=bad), self.assertRaises(TypeError):
                Segmenter(ScriptedModel([]), reset_policy=bad)  # type: ignore[arg-type]

    def test_invalid_parameters_are_rejected(self) -> None:
        for kwargs in (
            {"threshold": 0.0},
            {"threshold": 1.5},
            {"threshold": float("nan")},
            {"threshold": 0.5, "neg_threshold": 0.5},
            {"threshold": 0.5, "neg_threshold": 0.0},
            {"min_speech_ms": -1},
            {"pre_roll_ms": -1},
            {"pre_roll_ms": float("inf")},
        ):
            with self.subTest(**kwargs), self.assertRaises(ValueError):
                Segmenter(
                    ScriptedModel([]),
                    reset_policy=ResetPolicy.STREAM_START,
                    **kwargs,  # type: ignore[arg-type]
                )

    def test_invalid_audio_is_rejected(self) -> None:
        seg = Segmenter(ScriptedModel([]), reset_policy=ResetPolicy.STREAM_START)
        with self.assertRaises(ValueError):
            seg.process(np.zeros(W, dtype=np.float32))
        with self.assertRaises(ValueError):
            seg.process(np.zeros((2, W), dtype=np.int16))
        with self.assertRaises(TypeError):
            seg.process([0] * W)  # type: ignore[arg-type]

    def test_empty_input_is_noop(self) -> None:
        seg = Segmenter(ScriptedModel([]), reset_policy=ResetPolicy.STREAM_START)
        self.assertEqual(seg.process(np.zeros(0, dtype=np.int16)), [])


class PauseEndpointTest(unittest.TestCase):
    def test_pause_decision(self) -> None:
        endpoint = PauseEndpoint(pause_secs=0.6)
        self.assertIsNone(
            endpoint.decide(utterance_samples=20000, silence_samples=9599)
        )
        self.assertEqual(
            endpoint.decide(utterance_samples=20000, silence_samples=9600), "pause"
        )

    def test_custom_pause_changes_end(self) -> None:
        events, _, _ = run(
            [0.9] * 10 + [0.0] * 20, endpoint=PauseEndpoint(pause_secs=0.2)
        )
        # 0.2 s = 3200 樣本 → 第 7 個靜音視窗結束時（樣本 17 * 512）。
        self.assertEqual(events[-1], UtteranceEnded(0, 17 * W, "pause"))

    def test_invalid_pause_is_rejected(self) -> None:
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PauseEndpoint(pause_secs=bad)


class MaxLengthTest(unittest.TestCase):
    def test_max_length_decision(self) -> None:
        endpoint = PauseEndpoint()  # max_secs 預設 10 秒 = 160000 樣本
        self.assertIsNone(endpoint.decide(utterance_samples=159999, silence_samples=0))
        self.assertEqual(
            endpoint.decide(utterance_samples=160000, silence_samples=0), "max_length"
        )
        # 同時滿足時以 pause 為準：句子是自然結束，不是被切斷。
        self.assertEqual(
            endpoint.decide(utterance_samples=160000, silence_samples=9600), "pause"
        )

    def test_long_speech_is_cut_and_continues(self) -> None:
        events, _, _ = run([0.9] * 60, endpoint=PauseEndpoint(max_secs=1.0))
        ends = [e for e in events if isinstance(e, UtteranceEnded)]
        starts = [e for e in events if isinstance(e, UtteranceStarted)]
        # 1 s = 16000 樣本 → 第 32 個視窗結束時（樣本 16384）切斷。
        self.assertEqual(ends[0], UtteranceEnded(0, 32 * W, "max_length"))
        # 人聲沒停，下一句緊接著開始，前置緩衝不越過上一句，音訊不重複也不遺漏。
        self.assertEqual(starts[1], UtteranceStarted(32 * W))

    def test_invalid_max_is_rejected(self) -> None:
        for bad in (0.0, -2.0, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                PauseEndpoint(max_secs=bad)


class RequestEndTest(unittest.TestCase):
    def make(self, probs: list[float]) -> tuple[Segmenter, ScriptedModel]:
        model = ScriptedModel(probs)
        return Segmenter(model, reset_policy=ResetPolicy.STREAM_START), model

    def test_request_end_ends_on_next_window(self) -> None:
        seg, _ = self.make([0.9] * 12)
        events = seg.process(numbered_audio(10 * W))
        self.assertEqual(kinds(events)[0], "UtteranceStarted")
        seg.request_end()
        events = seg.process(numbered_audio(W))
        self.assertEqual(kinds(events), ["UtteranceAudio", "UtteranceEnded"])
        self.assertEqual(events[-1], UtteranceEnded(0, 11 * W, "external"))

    def test_request_end_waits_for_full_window(self) -> None:
        seg, _ = self.make([0.9] * 12)
        seg.process(numbered_audio(10 * W))
        seg.request_end()
        self.assertEqual(seg.process(numbered_audio(100)), [])
        events = seg.process(numbered_audio(W - 100))
        self.assertEqual(events[-1], UtteranceEnded(0, 11 * W, "external"))

    def test_request_end_without_utterance_has_no_effect(self) -> None:
        seg, _ = self.make([0.0] * 3 + [0.9] * 20)
        seg.request_end()
        seg.process(numbered_audio(3 * W))
        events = seg.process(numbered_audio(20 * W))
        self.assertEqual(kinds(events).count("UtteranceStarted"), 1)
        self.assertNotIn("UtteranceEnded", kinds(events))

    def test_request_end_during_candidate_has_no_effect(self) -> None:
        seg, _ = self.make([0.9] * 20)
        seg.process(numbered_audio(3 * W))  # 候選中，尚未確認開始
        seg.request_end()
        events = seg.process(numbered_audio(17 * W))
        self.assertEqual(kinds(events).count("UtteranceStarted"), 1)
        self.assertNotIn("UtteranceEnded", kinds(events))

    def test_request_is_consumed_once(self) -> None:
        seg, _ = self.make([0.9] * 40)
        seg.process(numbered_audio(10 * W))
        seg.request_end()
        events = seg.process(numbered_audio(30 * W))
        ended = [e for e in events if isinstance(e, UtteranceEnded)]
        self.assertEqual(ended, [UtteranceEnded(0, 11 * W, "external")])


class StreamStartResetTest(unittest.TestCase):
    def test_resets_only_at_stream_start(self) -> None:
        probs = [0.9] * 10 + [0.0] * 25 + [0.9] * 10 + [0.0] * 100
        events, model, _ = run(probs)
        self.assertEqual(kinds(events).count("UtteranceEnded"), 2)
        self.assertEqual(model.resets, [0])

    def test_flush_resets_for_next_stream(self) -> None:
        _, model, _ = run([0.9] * 10 + [0.0] * 5, flush=True)
        self.assertEqual(model.resets, [0, 15])

    def test_long_silence_does_not_reset(self) -> None:
        _, model, _ = run([0.0] * 200)
        self.assertEqual(model.resets, [0])


class AfterUtteranceResetTest(unittest.TestCase):
    POLICY = ResetPolicy.AFTER_UTTERANCE

    def test_resets_after_each_utterance(self) -> None:
        probs = [0.9] * 10 + [0.0] * 25 + [0.9] * 10 + [0.0] * 30
        events, model, _ = run(probs, reset_policy=self.POLICY)
        ends = [e.end_sample // W for e in events if isinstance(e, UtteranceEnded)]
        # 第一句在第 29 個視窗後結束，第二句在第 64 個視窗後結束，各重置一次。
        self.assertEqual(ends, [29, 64])
        self.assertEqual(model.resets, [0, 29, 64])

    def test_resets_after_external_and_max_length_end(self) -> None:
        model = ScriptedModel([0.9] * 60)
        seg = Segmenter(
            model,
            reset_policy=self.POLICY,
            endpoint=PauseEndpoint(max_secs=1.0),
        )
        seg.process(numbered_audio(10 * W))
        seg.request_end()
        seg.process(numbered_audio(50 * W))
        # external：第 11 個視窗後；之後的句子從樣本 11*W 起，滿 1 秒在第 43 個視窗後切斷。
        self.assertEqual(model.resets, [0, 11, 43])

    def test_flush_resets_only_once(self) -> None:
        _, model, _ = run([0.9] * 10, flush=True, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0, 10])

    def test_no_reset_without_utterance(self) -> None:
        _, model, _ = run([0.0] * 200, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0])


class AfterLongNonSpeechResetTest(unittest.TestCase):
    POLICY = ResetPolicy.AFTER_LONG_NON_SPEECH

    def test_resets_every_period_of_non_speech(self) -> None:
        # 2.0 s = 32000 樣本 → 每 63 個非人聲視窗重置一次。
        _, model, _ = run([0.0] * 200, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0, 63, 126, 189])

    def test_custom_period(self) -> None:
        _, model, _ = run(
            [0.0] * 100, reset_policy=self.POLICY, reset_after_non_speech_secs=1.0
        )
        self.assertEqual(model.resets, [0, 32, 64, 96])

    def test_waits_until_last_five_windows_are_quiet(self) -> None:
        # 第 63 個視窗時已滿 2 秒，但最近 5 個視窗含 0.2（≥ 0.1），等到第 68 個才重置。
        probs = [0.0] * 60 + [0.2] * 3 + [0.0] * 10
        _, model, _ = run(probs, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0, 68])

    def test_speech_restarts_the_count(self) -> None:
        # 50 個靜音視窗不足 2 秒；人聲句子在第 79 個視窗後結束，之後再滿 63 個才重置。
        probs = [0.0] * 50 + [0.9] * 10 + [0.0] * 90
        _, model, _ = run(probs, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0, 142])

    def test_candidate_restarts_the_count(self) -> None:
        # 短暫人聲（未達 min_speech）也打斷非人聲的計數。
        probs = [0.0] * 50 + [0.9] * 3 + [0.0] * 70
        _, model, _ = run(probs, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0, 116])

    def test_no_reset_after_utterance_itself(self) -> None:
        probs = [0.9] * 10 + [0.0] * 30
        _, model, _ = run(probs, reset_policy=self.POLICY)
        self.assertEqual(model.resets, [0])

    def test_invalid_period_is_rejected(self) -> None:
        for bad in (0.0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                Segmenter(
                    ScriptedModel([]),
                    reset_policy=self.POLICY,
                    reset_after_non_speech_secs=bad,
                )


if __name__ == "__main__":
    unittest.main()
