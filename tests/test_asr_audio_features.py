"""asr 的輸入驗證與 log-mel 特徵測試（不需模型）。"""

import unittest

import numpy as np

from kikitori.asr.audio import to_float32, validate_audio
from kikitori.asr.features import (
    FRAMES_PER_WINDOW,
    HOP_LENGTH,
    N_FFT,
    log_mel_spectrogram,
    mel_filters,
    split_windows,
)


class ValidateAudioTest(unittest.TestCase):
    def test_accepts_1d_int16(self) -> None:
        audio = np.zeros(1600, dtype=np.int16)
        self.assertIs(validate_audio(audio), audio)

    def test_rejects_non_array(self) -> None:
        with self.assertRaises(TypeError):
            validate_audio([0, 1, 2])

    def test_rejects_wrong_dtype(self) -> None:
        for dtype in (np.float32, np.int32, np.uint8):
            with self.subTest(dtype=dtype), self.assertRaises(TypeError):
                validate_audio(np.zeros(100, dtype=dtype))

    def test_rejects_multichannel(self) -> None:
        with self.assertRaises(ValueError):
            validate_audio(np.zeros((100, 2), dtype=np.int16))
        with self.assertRaises(ValueError):
            validate_audio(np.zeros((100, 1), dtype=np.int16))

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            validate_audio(np.zeros(0, dtype=np.int16))

    def test_to_float32_scale(self) -> None:
        out = to_float32(np.array([-32768, 0, 16384], dtype=np.int16))
        self.assertEqual(out.dtype, np.float32)
        np.testing.assert_allclose(out, [-1.0, 0.0, 0.5])


class MelFilterTest(unittest.TestCase):
    def test_shape_and_non_negative(self) -> None:
        for n_mels in (80, 128):
            filters = mel_filters(n_mels)
            self.assertEqual(filters.shape, (n_mels, N_FFT // 2 + 1))
            self.assertTrue(np.all(filters >= 0))
            # 每個頻帶至少涵蓋一個 FFT bin
            self.assertTrue(np.all(filters.max(axis=1) > 0))

    def test_cached_result_is_read_only(self) -> None:
        with self.assertRaises(ValueError):
            mel_filters(80)[0, 0] = 1.0

    def test_rejects_non_positive(self) -> None:
        with self.assertRaises(ValueError):
            mel_filters(0)


class LogMelTest(unittest.TestCase):
    def test_frame_count(self) -> None:
        # 補 160 樣本後 center STFT，再丟掉最後一個 frame：frames = n // 160 + 1
        for n in (16000, 16001, 480000):
            features = log_mel_spectrogram(np.zeros(n, dtype=np.float32), 80)
            self.assertEqual(features.shape, (80, n // HOP_LENGTH + 1))
            self.assertEqual(features.dtype, np.float32)

    def test_silence_is_constant_floor(self) -> None:
        features = log_mel_spectrogram(np.zeros(16000, dtype=np.float32), 80)
        # log10(1e-10) = -10 → (-10 + 4) / 4 = -1.5
        # float32 的 log10 不保證正確捨入：Linux x86-64 的 numpy 在支援 AVX-512 的 CPU
        # 上改用 SVML（誤差上限 4 ULP），CI 實測差 1 ULP（-10.00000095）。-10 附近
        # 1 ULP = spacing(10)，正規化除以 4 後是 spacing(10) / 4；容許 4 ULP 即 spacing(10)。
        np.testing.assert_allclose(
            features, -1.5, rtol=0, atol=float(np.spacing(np.float32(10)))
        )

    def test_dynamic_range_is_clamped_to_8(self) -> None:
        rng = np.random.default_rng(0)
        audio = (rng.standard_normal(32000) * 0.3).astype(np.float32)
        audio[:16000] = 0
        features = log_mel_spectrogram(audio, 128)
        # 正規化前的動態範圍最多 8（log10），正規化後最多 2
        self.assertLessEqual(float(features.max() - features.min()), 2.0 + 1e-5)

    def test_matches_direct_reference(self) -> None:
        # 不用 stride 技巧、逐 frame 計算的參考實作
        rng = np.random.default_rng(1)
        audio = (rng.standard_normal(4000) * 0.1).astype(np.float32)
        padded = np.pad(
            np.pad(audio, (0, HOP_LENGTH)), (N_FFT // 2, N_FFT // 2), mode="reflect"
        )
        window = np.hanning(N_FFT + 1)[:-1]
        n_frames = 1 + (len(padded) - N_FFT) // HOP_LENGTH
        spec = np.stack(
            [
                np.abs(
                    np.fft.rfft(
                        padded[i * HOP_LENGTH : i * HOP_LENGTH + N_FFT] * window
                    )
                )
                ** 2
                for i in range(n_frames - 1)
            ],
            axis=1,
        )
        log_spec = np.log10(np.clip(mel_filters(80) @ spec, 1e-10, None))
        expected = (np.maximum(log_spec, log_spec.max() - 8.0) + 4.0) / 4.0
        np.testing.assert_allclose(log_mel_spectrogram(audio, 80), expected, atol=1e-4)

    def test_rejects_2d(self) -> None:
        with self.assertRaises(ValueError):
            log_mel_spectrogram(np.zeros((2, 100), dtype=np.float32), 80)


class SplitWindowsTest(unittest.TestCase):
    def test_short_input_is_padded_with_zeros(self) -> None:
        features = np.ones((80, 101), dtype=np.float32)
        windows = split_windows(features)
        self.assertEqual(len(windows), 1)
        self.assertEqual(windows[0].shape, (80, FRAMES_PER_WINDOW))
        # 最後一個 frame 不算內容
        self.assertTrue(np.all(windows[0][:, :100] == 1))
        self.assertTrue(np.all(windows[0][:, 100:] == 0))

    def test_exactly_30_seconds_is_one_window(self) -> None:
        self.assertEqual(len(split_windows(np.ones((80, 3001), dtype=np.float32))), 1)

    def test_long_input_is_split(self) -> None:
        features = np.arange(4376, dtype=np.float32)[np.newaxis].repeat(80, axis=0)
        windows = split_windows(features)
        self.assertEqual(len(windows), 2)
        self.assertEqual(windows[1][0, 0], 3000)
        self.assertEqual(windows[1][0, 1374], 4374)
        self.assertEqual(windows[1][0, 1375], 0)


if __name__ == "__main__":
    unittest.main()
