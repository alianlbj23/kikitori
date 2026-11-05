"""評測工具的測試：噪音產生器、混音、指標。"""

import math
import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from kikitori.evaluation import metrics, mixing, noise, wav
from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES
from kikitori.vad.silero import SileroVad


def spectrum_slope_db_per_octave(audio: np.ndarray, low: float, high: float) -> float:
    """平均多段的功率譜，回傳 low–high Hz 之間每倍頻的斜率（dB）。"""
    seg = 4096
    frames = audio[: len(audio) // seg * seg].astype(np.float64).reshape(-1, seg)
    power = (np.abs(np.fft.rfft(frames * np.hanning(seg), axis=1)) ** 2).mean(axis=0)
    freqs = np.fft.rfftfreq(seg, 1 / SAMPLE_RATE)
    band = (freqs >= low) & (freqs <= high)
    slope, _ = np.polyfit(np.log2(freqs[band]), 10 * np.log10(power[band]), 1)
    return float(slope)


class NoiseGeneratorTest(unittest.TestCase):
    def test_same_seed_gives_same_output(self) -> None:
        for gen in (noise.white_noise, noise.pink_noise):
            with self.subTest(gen=gen.__name__):
                a = gen(SAMPLE_RATE, level_dbfs=-20.0, seed=3)
                b = gen(SAMPLE_RATE, level_dbfs=-20.0, seed=3)
                c = gen(SAMPLE_RATE, level_dbfs=-20.0, seed=4)
                self.assertEqual(a.dtype, np.int16)
                self.assertEqual(a.shape, (SAMPLE_RATE,))
                np.testing.assert_array_equal(a, b)
                self.assertFalse(np.array_equal(a, c))

    def test_rms_matches_requested_level(self) -> None:
        for gen in (noise.white_noise, noise.pink_noise):
            for level in (-40.0, -20.0, -12.0):
                with self.subTest(gen=gen.__name__, level=level):
                    audio = gen(SAMPLE_RATE * 2, level_dbfs=level, seed=0)
                    self.assertAlmostEqual(noise.rms_dbfs(audio), level, delta=0.1)

    def test_pink_noise_falls_3db_per_octave(self) -> None:
        audio = noise.pink_noise(SAMPLE_RATE * 20, level_dbfs=-20.0, seed=1)
        slope = spectrum_slope_db_per_octave(audio, 50.0, 6000.0)
        self.assertAlmostEqual(slope, -3.0, delta=0.3)

    def test_white_noise_is_flat(self) -> None:
        audio = noise.white_noise(SAMPLE_RATE * 20, level_dbfs=-20.0, seed=1)
        slope = spectrum_slope_db_per_octave(audio, 50.0, 6000.0)
        self.assertAlmostEqual(slope, 0.0, delta=0.3)

    def test_rms_dbfs_of_known_signal(self) -> None:
        audio = np.full(100, 3277, dtype=np.int16)  # 約 0.1 full scale
        self.assertAlmostEqual(noise.rms_dbfs(audio), -20.0, delta=0.01)
        self.assertEqual(noise.rms_dbfs(np.zeros(10, dtype=np.int16)), -np.inf)

    def test_invalid_arguments_are_rejected(self) -> None:
        bad_calls = [
            {"num_samples": 0, "level_dbfs": -20.0, "seed": 0},
            {"num_samples": -5, "level_dbfs": -20.0, "seed": 0},
            {"num_samples": 100, "level_dbfs": 1.0, "seed": 0},
            {"num_samples": 100, "level_dbfs": float("nan"), "seed": 0},
            {"num_samples": 100, "level_dbfs": -200.0, "seed": 0},
        ]
        for gen in (noise.white_noise, noise.pink_noise):
            for kwargs in bad_calls:
                with (
                    self.subTest(gen=gen.__name__, **kwargs),
                    self.assertRaises(ValueError),
                ):
                    gen(**kwargs)  # type: ignore[arg-type]

    def test_non_int_length_is_rejected(self) -> None:
        for gen in (noise.white_noise, noise.pink_noise):
            with self.subTest(gen=gen.__name__), self.assertRaises(TypeError):
                gen(1.5, level_dbfs=-20.0, seed=0)  # type: ignore[arg-type]


class NoiseIsNotSpeechTest(unittest.TestCase):
    """真模型：白噪音、粉紅噪音整段的機率都要低於 0.5。"""

    def test_noise_probability_stays_below_threshold(self) -> None:
        vad = SileroVad()
        for gen in (noise.white_noise, noise.pink_noise):
            for level in (-40.0, -20.0, -10.0):
                with self.subTest(gen=gen.__name__, level=level):
                    vad.reset()
                    audio = gen(SAMPLE_RATE * 5, level_dbfs=level, seed=7)
                    probs = [
                        vad.probability(audio[i : i + WINDOW_SAMPLES])
                        for i in range(
                            0, len(audio) - WINDOW_SAMPLES + 1, WINDOW_SAMPLES
                        )
                    ]
                    self.assertLess(max(probs), 0.5)


class FrameLabelTest(unittest.TestCase):
    def test_window_center_decides_label(self) -> None:
        # 視窗中心 256、768、1280、1792；只有 768 落在 [512, 1100)。
        labels = metrics.frame_labels(4, [(512, 1100)])
        np.testing.assert_array_equal(labels, [False, True, False, False])

    def test_no_segments(self) -> None:
        self.assertFalse(metrics.frame_labels(3, []).any())


class RocAucTest(unittest.TestCase):
    def test_hand_computed_auc(self) -> None:
        # 正例 0.35、0.8 對負例 0.1、0.4：4 組中 3 組排對 → 0.75。
        auc = metrics.roc_auc(
            np.array([0.1, 0.4, 0.35, 0.8]), np.array([False, False, True, True])
        )
        self.assertAlmostEqual(auc, 0.75)

    def test_ties_count_half(self) -> None:
        auc = metrics.roc_auc(np.array([0.5, 0.5]), np.array([True, False]))
        self.assertAlmostEqual(auc, 0.5)
        auc = metrics.roc_auc(
            np.array([0.2, 0.5, 0.5, 0.9]), np.array([False, True, False, True])
        )
        self.assertAlmostEqual(auc, (1 + 0.5 + 1 + 1) / 4)

    def test_perfect_and_inverted(self) -> None:
        labels = np.array([False, False, True])
        self.assertAlmostEqual(metrics.roc_auc(np.array([0.1, 0.2, 0.9]), labels), 1.0)
        self.assertAlmostEqual(metrics.roc_auc(np.array([0.9, 0.8, 0.1]), labels), 0.0)

    def test_single_class_is_undefined(self) -> None:
        scores = np.array([0.1, 0.9])
        self.assertTrue(math.isnan(metrics.roc_auc(scores, np.array([True, True]))))
        self.assertTrue(math.isnan(metrics.roc_auc(scores, np.array([False, False]))))

    def test_mismatched_lengths_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            metrics.roc_auc(np.array([0.1]), np.array([True, False]))


class ErrorRateTest(unittest.TestCase):
    def test_false_alarm_and_miss(self) -> None:
        probs = np.array([0.6, 0.2, 0.5, 0.9, 0.3])
        labels = np.array([False, False, False, True, True])
        # 非人聲 0.6、0.2、0.5 中 0.6 與 0.5 達門檻 → 2/3；人聲 0.9、0.3 中 0.3 未達 → 1/2。
        self.assertAlmostEqual(metrics.false_alarm_rate(probs, labels, 0.5), 2 / 3)
        self.assertAlmostEqual(metrics.miss_rate(probs, labels, 0.5), 1 / 2)

    def test_all_speech_or_all_non_speech(self) -> None:
        probs = np.array([0.6, 0.2])
        all_speech = np.array([True, True])
        self.assertTrue(math.isnan(metrics.false_alarm_rate(probs, all_speech, 0.5)))
        self.assertAlmostEqual(metrics.miss_rate(probs, all_speech, 0.5), 0.5)
        no_speech = np.array([False, False])
        self.assertTrue(math.isnan(metrics.miss_rate(probs, no_speech, 0.5)))
        self.assertAlmostEqual(metrics.false_alarm_rate(probs, no_speech, 0.5), 0.5)


class OnsetLatencyTest(unittest.TestCase):
    def test_latencies_skip_continuations_and_misses(self) -> None:
        truth = [(1000, 5000), (5500, 9000), (20000, 21000)]
        detections = [
            metrics.Detection(start_sample=0, end_sample=10000, started_at=5096),
            metrics.Detection(start_sample=17000, end_sample=25000, started_at=21600),
        ]
        # 第 1 段：5096 − 1000 = 4096 樣本 = 256 ms。
        # 第 2 段：偵測在它開始前就已確認（與第 1 段併成一句），不算新的開始。
        # 第 3 段：21600 − 20000 = 1600 樣本 = 100 ms。
        latencies = metrics.onset_latencies_ms(truth, detections)
        self.assertEqual(latencies, [256.0, 100.0])
        median, p90 = metrics.median_and_p90(latencies)
        self.assertAlmostEqual(median, 178.0)
        self.assertAlmostEqual(p90, 100 + 0.9 * 156)

    def test_grazing_detection_is_ignored(self) -> None:
        # 誤報 [0, 1100) 只擦到段落開頭 100 樣本（< 250 ms）；對應的是 [1000, 6000)。
        detections = [
            metrics.Detection(start_sample=0, end_sample=1100, started_at=900),
            metrics.Detection(start_sample=1000, end_sample=6000, started_at=5000),
        ]
        self.assertEqual(
            metrics.onset_latencies_ms([(1000, 5000)], detections), [250.0]
        )

    def test_earliest_substantial_detection_is_the_match(self) -> None:
        # 段落 [16000, 64000) 被切成兩句；延遲以較早的那句為準，而不是重疊較長的那句。
        detections = [
            metrics.Detection(start_sample=16000, end_sample=30000, started_at=24000),
            metrics.Detection(start_sample=30000, end_sample=64000, started_at=38000),
        ]
        self.assertEqual(
            metrics.onset_latencies_ms([(16000, 64000)], detections), [500.0]
        )

    def test_no_detection_gives_empty_list(self) -> None:
        self.assertEqual(metrics.onset_latencies_ms([(0, 100)], []), [])
        median, p90 = metrics.median_and_p90([])
        self.assertTrue(math.isnan(median) and math.isnan(p90))


class SegmentCountTest(unittest.TestCase):
    def test_premature_cuts(self) -> None:
        truth = [(0, 10000), (20000, 30000)]
        ends = [5000, 9000, 10500, 28400]

        def det(end: int) -> metrics.Detection:
            return metrics.Detection(
                start_sample=end - 100, end_sample=end, started_at=end - 50
            )

        # 5000：距第 1 段終點 5000 樣本 → 算；9000：62.5 ms → 不算；
        # 10500：不在人聲內；28400：剛好 100 ms，不算「超過」。
        self.assertEqual(metrics.premature_cuts(truth, [det(e) for e in ends]), 1)

    def test_missed_segments_need_half_overlap_from_one_detection(self) -> None:
        truth = [(0, 1000), (2000, 3000), (5000, 6000)]
        detections = [
            metrics.Detection(0, 600, 300),
            metrics.Detection(2000, 2400, 2300),
            metrics.Detection(2500, 2900, 2800),
            metrics.Detection(4000, 5500, 4500),
        ]
        # 第 2 段被兩句各蓋 400 樣本，單句都不到一半 → 漏抓 1 段。
        self.assertEqual(metrics.missed_segments(truth, detections), 1)

    def test_everything_missed_without_detections(self) -> None:
        self.assertEqual(metrics.missed_segments([(0, 10), (20, 30)], []), 2)
        self.assertEqual(metrics.premature_cuts([(0, 10)], []), 0)


def speech_like_track() -> tuple[np.ndarray, list[tuple[int, int]]]:
    """「人聲」區間放 220 Hz 正弦（−20 dBFS 左右），其餘為數位靜音。"""
    track = np.zeros(SAMPLE_RATE * 4, dtype=np.int16)
    segments = [(8000, 24000), (32000, 40000), (48000, 60000)]
    t = np.arange(SAMPLE_RATE * 4) / SAMPLE_RATE
    sine = (3000 * np.sin(2 * np.pi * 220 * t)).astype(np.int16)
    for start, end in segments:
        track[start:end] = sine[start:end]
    return track, segments


def measured_snr_db(
    speech: np.ndarray, background: np.ndarray, segments: list
) -> float:
    mask = np.zeros(len(speech), dtype=bool)
    for start, end in segments:
        mask[start:end] = True
    s = np.sqrt(np.mean(speech[mask].astype(np.float64) ** 2))
    b = np.sqrt(np.mean(background.astype(np.float64) ** 2))
    return 20 * math.log10(s / b)


class MixingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.track, self.segments = speech_like_track()
        self.truth = {
            "samples": len(self.track),
            "segments": [
                {
                    "file": f"c{i}",
                    "start_sample": s,
                    "end_sample": e,
                    "gap_after_secs": 0.5,
                }
                for i, (s, e) in enumerate(self.segments)
            ],
        }
        self.background = noise.pink_noise(SAMPLE_RATE, level_dbfs=-30.0, seed=5)

    def test_gap_fill_keeps_speech_samples(self) -> None:
        result = mixing.fill_gaps(
            self.track, self.truth, self.background, snr_db=10.0, label="pink"
        )
        self.assertEqual(result.audio.dtype, np.int16)
        self.assertEqual(len(result.audio), len(self.track))
        for start, end in self.segments:
            np.testing.assert_array_equal(
                result.audio[start:end], self.track[start:end]
            )
        gaps = np.ones(len(self.track), dtype=bool)
        for start, end in self.segments:
            gaps[start:end] = False
        self.assertGreater(np.count_nonzero(result.audio[gaps]), gaps.sum() * 0.9)
        snr = measured_snr_db(self.track, result.audio[gaps], self.segments)
        self.assertAlmostEqual(snr, 10.0, delta=0.2)

    def test_overlay_reaches_requested_snr(self) -> None:
        for snr_db in (20.0, 10.0, 5.0):
            with self.subTest(snr_db=snr_db):
                result = mixing.overlay(
                    self.track, self.truth, self.background, snr_db=snr_db, label="pink"
                )
                added = result.audio.astype(np.int32) - self.track
                snr = measured_snr_db(self.track, added, self.segments)
                self.assertAlmostEqual(snr, snr_db, delta=0.2)

    def test_overlay_avoids_clipping_by_scaling_mix(self) -> None:
        loud = (self.track.astype(np.int32) * 10).clip(-32768, 32767).astype(np.int16)
        result = mixing.overlay(
            loud, self.truth, self.background, snr_db=0.0, label="x"
        )
        # 不截斷而是整條等比例縮小：結果應等於小音量版本的混音放大到滿刻度。
        quiet = mixing.overlay(
            self.track, self.truth, self.background, snr_db=0.0, label="x"
        ).audio.astype(np.float64)
        expected = quiet * 32767 / np.abs(quiet).max()
        np.testing.assert_allclose(result.audio, expected, atol=12)
        self.assertLessEqual(int(np.abs(result.audio.astype(np.int32)).max()), 32767)

    def test_truth_keeps_positions_and_records_mix(self) -> None:
        result = mixing.overlay(
            self.track, self.truth, self.background, snr_db=5.0, label="pink"
        )
        self.assertEqual(result.truth["samples"], len(self.track))
        self.assertEqual(result.truth["segments"], self.truth["segments"])
        self.assertEqual(
            result.truth["mix"],
            {"mode": "overlay", "background": "pink", "snr_db": 5.0},
        )
        result = mixing.fill_gaps(
            self.track, self.truth, self.background, snr_db=10.0, label="pink"
        )
        self.assertEqual(result.truth["mix"]["mode"], "gap_fill")

    def test_short_background_is_looped(self) -> None:
        short = self.background[:1000]
        result = mixing.overlay(self.track, self.truth, short, snr_db=10.0, label="s")
        added = result.audio.astype(np.int32) - self.track
        np.testing.assert_array_equal(added[:1000], added[1000:2000])

    def test_gap_fill_without_gaps_is_rejected(self) -> None:
        track = self.track[8000:24000].copy()
        truth = {
            "samples": len(track),
            "segments": [{"start_sample": 0, "end_sample": len(track)}],
        }
        with self.assertRaises(ValueError) as ctx:
            mixing.fill_gaps(track, truth, self.background, snr_db=10.0, label="x")
        self.assertIn("no gaps", str(ctx.exception))

    def test_segments_parsed_from_truth(self) -> None:
        self.assertEqual(mixing.segments_from_truth(self.truth), self.segments)

    def test_invalid_truth_is_rejected(self) -> None:
        bad_truths = [
            {"samples": 100},
            {"samples": 100, "segments": [{"start_sample": 50, "end_sample": 200}]},
            {"samples": 100, "segments": [{"start_sample": 50, "end_sample": 40}]},
            {"samples": 100, "segments": [{"start_sample": "0", "end_sample": 40}]},
            {
                "samples": 100,
                "segments": [
                    {"start_sample": 0, "end_sample": 50},
                    {"start_sample": 40, "end_sample": 60},
                ],
            },
        ]
        for truth in bad_truths:
            with self.subTest(truth=truth), self.assertRaises(ValueError):
                mixing.segments_from_truth(truth)

    def test_invalid_inputs_are_rejected(self) -> None:
        silent_bg = np.zeros(100, dtype=np.int16)
        with self.assertRaises(ValueError):
            mixing.overlay(self.track, self.truth, silent_bg, snr_db=10.0, label="x")
        with self.assertRaises(ValueError):
            mixing.overlay(self.track[:-1], self.truth, self.background, 10.0, "x")
        with self.assertRaises(ValueError):
            mixing.overlay(
                self.track, self.truth, self.background, snr_db=float("nan"), label="x"
            )
        with self.assertRaises(ValueError):
            mixing.overlay(
                np.zeros_like(self.track), self.truth, self.background, 10.0, "x"
            )


class WavTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        audio = noise.white_noise(1234, level_dbfs=-20.0, seed=0)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "a.wav"
            wav.write_wav(path, audio)
            np.testing.assert_array_equal(wav.read_wav(path), audio)

    def test_rejects_wrong_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "stereo.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(2)
                w.setsampwidth(2)
                w.setframerate(SAMPLE_RATE)
                w.writeframes(b"\0\0" * 20)
            with self.assertRaises(ValueError):
                wav.read_wav(path)
            path = Path(tmp) / "8k.wav"
            with wave.open(str(path), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(8000)
                w.writeframes(b"\0\0" * 20)
            with self.assertRaises(ValueError):
                wav.read_wav(path)
            path = Path(tmp) / "junk.wav"
            path.write_bytes(b"not a wav")
            with self.assertRaises(ValueError):
                wav.read_wav(path)


if __name__ == "__main__":
    unittest.main()
