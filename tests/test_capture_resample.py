"""capture 的降混音、重採樣與 int16 轉換測試：頻率響應、切塊不變性、輸入驗證。"""

import unittest

import numpy as np

from kikitori.capture.resample import (
    AudioConverter,
    StreamingResampler,
    downmix_to_mono,
    float_to_int16,
)


def tone(freq: float, rate: int, seconds: float, amplitude: float = 0.5) -> np.ndarray:
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(np.square(x.astype(np.float64)))))


def steady(x: np.ndarray, rate: int) -> np.ndarray:
    """去掉頭尾各 0.1 秒，避開濾波器的暫態。"""
    margin = rate // 10
    return x[margin:-margin]


class StreamingResamplerTest(unittest.TestCase):
    def test_output_length_matches_ratio(self) -> None:
        for in_rate in (48000, 44100, 32000, 22050, 8000):
            with self.subTest(in_rate=in_rate):
                r = StreamingResampler(in_rate, 16000)
                out = r.process(np.zeros(in_rate, dtype=np.float32))
                self.assertEqual(out.shape, (16000,))

    def test_passband_tone_survives(self) -> None:
        for in_rate in (48000, 44100):
            for freq in (1000.0, 6000.0):
                with self.subTest(in_rate=in_rate, freq=freq):
                    x = tone(freq, in_rate, 1.0)
                    y = StreamingResampler(in_rate, 16000).process(x)
                    gain_db = 20 * np.log10(
                        rms(steady(y, 16000)) / rms(steady(x, in_rate))
                    )
                    self.assertLess(abs(gain_db), 0.2)

    def test_tone_above_nyquist_is_attenuated_more_than_40_db(self) -> None:
        for in_rate in (48000, 44100):
            for freq in (8500.0, 10000.0, 15000.0):
                with self.subTest(in_rate=in_rate, freq=freq):
                    x = tone(freq, in_rate, 1.0)
                    y = StreamingResampler(in_rate, 16000).process(x)
                    gain_db = 20 * np.log10(
                        rms(steady(y, 16000)) / rms(steady(x, in_rate)) + 1e-12
                    )
                    self.assertLess(gain_db, -40.0)

    def test_tone_frequency_is_preserved(self) -> None:
        x = tone(1000.0, 48000, 1.0)
        y = steady(StreamingResampler(48000, 16000).process(x), 16000)
        spectrum = np.abs(np.fft.rfft(y * np.hanning(len(y))))
        peak_hz = np.argmax(spectrum) * 16000 / len(y)
        self.assertAlmostEqual(peak_hz, 1000.0, delta=16000 / len(y) + 1)

    def test_block_split_invariance(self) -> None:
        rng = np.random.default_rng(0)
        x = rng.uniform(-1, 1, 44100).astype(np.float32)
        for in_rate in (48000, 44100):
            with self.subTest(in_rate=in_rate):
                whole = StreamingResampler(in_rate, 16000).process(x)
                r = StreamingResampler(in_rate, 16000)
                cuts = sorted(rng.integers(0, len(x), 40).tolist())
                pieces = np.split(x, cuts)
                parts = [r.process(p) for p in pieces]
                np.testing.assert_allclose(np.concatenate(parts), whole, atol=1e-6)

    def test_single_sample_blocks_match_whole(self) -> None:
        x = tone(440.0, 44100, 0.05)
        whole = StreamingResampler(44100, 16000).process(x)
        r = StreamingResampler(44100, 16000)
        parts = [r.process(x[i : i + 1]) for i in range(len(x))]
        np.testing.assert_allclose(np.concatenate(parts), whole, atol=1e-6)

    def test_same_rate_passes_through(self) -> None:
        x = tone(1000.0, 16000, 0.1)
        y = StreamingResampler(16000, 16000).process(x)
        np.testing.assert_array_equal(y, x.astype(np.float64))

    def test_empty_block(self) -> None:
        r = StreamingResampler(48000, 16000)
        self.assertEqual(r.process(np.zeros(0, dtype=np.float32)).shape, (0,))

    def test_invalid_rates_rejected(self) -> None:
        for bad in (0, -16000, 10_000_000):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                StreamingResampler(bad, 16000)
        for wrong_type in (16000.5, True, "16000"):
            with self.subTest(wrong_type=wrong_type), self.assertRaises(TypeError):
                StreamingResampler(wrong_type, 16000)  # type: ignore[arg-type]

    def test_rejects_non_1d_input(self) -> None:
        with self.assertRaises(ValueError):
            StreamingResampler(48000, 16000).process(np.zeros((10, 2)))


class DownmixTest(unittest.TestCase):
    def test_averages_channels(self) -> None:
        frames = np.array([[1.0, 0.0], [0.5, -0.5], [-1.0, -1.0]], dtype=np.float32)
        np.testing.assert_allclose(downmix_to_mono(frames), [0.5, 0.0, -1.0])

    def test_mono_column_and_1d_pass_through(self) -> None:
        col = np.array([[0.1], [0.2]], dtype=np.float32)
        np.testing.assert_allclose(downmix_to_mono(col), [0.1, 0.2])
        np.testing.assert_allclose(downmix_to_mono(np.array([0.3, 0.4])), [0.3, 0.4])

    def test_rejects_3d(self) -> None:
        with self.assertRaises(ValueError):
            downmix_to_mono(np.zeros((2, 2, 2)))


class FloatToInt16Test(unittest.TestCase):
    def test_scales_rounds_and_clips(self) -> None:
        x = np.array([0.0, 0.5, -0.5, 1.0, -1.0, 2.0, -2.0, 1 / 65536])
        y = float_to_int16(x)
        self.assertEqual(y.dtype, np.int16)
        np.testing.assert_array_equal(
            y, [0, 16384, -16384, 32767, -32768, 32767, -32768, 0]
        )

    def test_nan_becomes_zero(self) -> None:
        y = float_to_int16(np.array([np.nan, np.inf, -np.inf]))
        np.testing.assert_array_equal(y, [0, 32767, -32768])


class AudioConverterTest(unittest.TestCase):
    def test_stereo_48k_to_16k_mono_int16(self) -> None:
        conv = AudioConverter(48000, 2)
        left = tone(1000.0, 48000, 0.5)
        block = np.stack([left, left], axis=1)
        out = conv.process(block)
        self.assertEqual(out.dtype, np.int16)
        self.assertEqual(out.ndim, 1)
        self.assertEqual(len(out), 8000)
        self.assertAlmostEqual(
            rms(steady(out, 16000)) / 32768, rms(steady(left, 48000)), delta=0.01
        )

    def test_channel_mismatch_rejected(self) -> None:
        conv = AudioConverter(48000, 2)
        with self.assertRaises(ValueError):
            conv.process(np.zeros((10, 3), dtype=np.float32))

    def test_mono_converter_accepts_1d(self) -> None:
        conv = AudioConverter(16000, 1)
        out = conv.process(np.array([0.5, -0.5], dtype=np.float32))
        np.testing.assert_array_equal(out, [16384, -16384])

    def test_invalid_channel_count(self) -> None:
        for bad in (0, -1, 65):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                AudioConverter(48000, bad)
        with self.assertRaises(TypeError):
            AudioConverter(48000, 2.0)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
