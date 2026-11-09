"""說話者向量的 fbank 與 kaldi-native-fbank 一致。

參考值由 kaldi-native-fbank 1.22.4（sherpa-onnx 2.0.0 內建的版本）以相同參數算出並寫死：
dither=0、snip_edges=false、80 bins、low 20 Hz、high -400（7600 Hz），輸入同下面的 `_signal()`。
"""

import unittest

import numpy as np

from kikitori.speakers.features import NUM_BINS, fbank, mel_banks

# (frame, 80 格總和, [bin 0, 10, 40, 79])；第 0 與 49 格是邊緣反射
_REFERENCE = {
    0: (-144.1566, (-3.7209, -0.8985, -0.6458, -6.5194)),
    1: (-807.5281, (-9.729, -2.7981, -11.8723, -15.9424)),
    25: (-738.5723, (-11.2036, -5.8498, -8.7472, -15.9424)),
    48: (-724.1223, (-11.4208, -5.8484, -11.9781, -15.9424)),
    49: (-167.9194, (-5.3452, -0.865, -3.1122, -5.2942)),
}


def _signal(length: int = 8000) -> np.ndarray:
    n = np.arange(length)
    sr = 16000
    return (
        0.3 * np.sin(2 * np.pi * 440 * n / sr)
        + 0.2 * np.sin(2 * np.pi * 1234.5 * n / sr)
        + 0.1 * np.sin(2 * np.pi * (100 + 0.25 * n) * n / sr)
    ).astype(np.float32)


class FbankTest(unittest.TestCase):
    def test_matches_kaldi_native_fbank(self) -> None:
        features = fbank(_signal())
        self.assertEqual(features.shape, (50, NUM_BINS))
        self.assertEqual(features.dtype, np.float32)
        for frame, (total, bins) in _REFERENCE.items():
            with self.subTest(frame=frame):
                self.assertAlmostEqual(float(features[frame].sum()), total, delta=0.01)
                for b, expected in zip((0, 10, 40, 79), bins, strict=True):
                    self.assertAlmostEqual(
                        float(features[frame, b]), expected, delta=1e-3
                    )

    def test_silence_hits_the_log_floor(self) -> None:
        features = fbank(np.zeros(1000, dtype=np.float32))
        self.assertEqual(features.shape, (6, NUM_BINS))
        self.assertAlmostEqual(float(features.max()), -15.9424, places=3)

    def test_shorter_than_one_frame_reflects_repeatedly(self) -> None:
        features = fbank(_signal(100))
        self.assertEqual(features.shape, (1, NUM_BINS))
        self.assertAlmostEqual(float(features[0].sum()), -123.6633, delta=0.01)

    def test_rejects_bad_input(self) -> None:
        with self.assertRaises(ValueError):
            fbank(np.zeros(0, dtype=np.float32))
        with self.assertRaises(ValueError):
            fbank(np.zeros(79, dtype=np.float32))  # 不到半格：0 個 frame
        with self.assertRaises(ValueError):
            fbank(np.zeros((2, 400), dtype=np.float32))

    def test_mel_banks_are_read_only(self) -> None:
        banks = mel_banks()
        self.assertEqual(banks.shape, (NUM_BINS, 257))
        self.assertTrue(np.all(banks[:, -1] == 0))
        with self.assertRaises(ValueError):
            banks[0, 0] = 1.0


if __name__ == "__main__":
    unittest.main()
