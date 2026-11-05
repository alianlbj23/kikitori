"""ArraySource 與 FileSource 的測試：切塊、即時節奏（假時鐘）、WAV 解碼與錯誤路徑。"""

import tempfile
import unittest
import wave
from pathlib import Path

import numpy as np

from kikitori.capture.errors import CaptureError
from kikitori.capture.sources import ArraySource, FileSource


class FakeClock:
    """可控制的單調時鐘；sleep 只推進時間，不真的等待。"""

    def __init__(self) -> None:
        self.now = 100.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def write_wav(path: Path, frames: bytes, rate: int, channels: int, width: int) -> None:
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(width)
        w.setframerate(rate)
        w.writeframes(frames)


class ArraySourceTest(unittest.TestCase):
    def test_non_realtime_chunks_then_exhausted(self) -> None:
        audio = np.arange(10, dtype=np.int16)
        src = ArraySource(audio, chunk_samples=4)
        src.start()
        got = [src.read(timeout=1.0) for _ in range(3)]
        self.assertEqual([len(g) for g in got], [4, 4, 2])
        np.testing.assert_array_equal(np.concatenate(got), audio)
        self.assertTrue(src.exhausted)
        self.assertEqual(src.read(timeout=1.0).shape, (0,))
        self.assertEqual(src.read(timeout=1.0).dtype, np.int16)
        src.stop()

    def test_read_before_start_raises(self) -> None:
        src = ArraySource(np.zeros(4, dtype=np.int16))
        with self.assertRaises(CaptureError):
            src.read(timeout=0)

    def test_double_start_raises(self) -> None:
        src = ArraySource(np.zeros(4, dtype=np.int16))
        src.start()
        with self.assertRaises(CaptureError):
            src.start()

    def test_stop_is_idempotent_and_read_after_stop_is_empty(self) -> None:
        src = ArraySource(np.ones(8, dtype=np.int16), chunk_samples=4)
        src.start()
        src.stop()
        src.stop()
        self.assertEqual(src.read(timeout=0).shape, (0,))

    def test_rejects_wrong_format(self) -> None:
        for bad in (
            np.zeros(4, dtype=np.float32),
            np.zeros((4, 2), dtype=np.int16),
            [1, 2, 3],
        ):
            with self.subTest(bad=type(bad)), self.assertRaises(ValueError):
                ArraySource(bad)  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            ArraySource(np.zeros(4, dtype=np.int16), chunk_samples=0)

    def test_read_does_not_alias_input(self) -> None:
        audio = np.zeros(4, dtype=np.int16)
        src = ArraySource(audio, chunk_samples=4)
        src.start()
        out = src.read(timeout=0)
        out[:] = 5
        self.assertEqual(int(audio.sum()), 0)

    def test_realtime_paces_by_wall_clock(self) -> None:
        clock = FakeClock()
        audio = np.arange(16000, dtype=np.int16)  # 1 秒
        src = ArraySource(
            audio,
            chunk_samples=320,
            realtime=True,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        src.start()
        # 剛開始還沒有任何樣本到期：等到第一塊（20 ms）到期為止。
        first = src.read(timeout=1.0)
        self.assertEqual(len(first), 320)
        self.assertAlmostEqual(sum(clock.sleeps), 0.02, places=6)
        # 時間往前 100 ms：一次拿到這段時間到期的全部樣本。
        clock.now += 0.1
        second = src.read(timeout=0.0)
        self.assertEqual(len(second), 1600)
        np.testing.assert_array_equal(np.concatenate([first, second]), audio[:1920])

    def test_realtime_timeout_shorter_than_next_chunk_returns_empty(self) -> None:
        clock = FakeClock()
        src = ArraySource(
            np.zeros(16000, dtype=np.int16),
            chunk_samples=320,
            realtime=True,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        src.start()
        out = src.read(timeout=0.005)
        self.assertEqual(out.shape, (0,))
        self.assertAlmostEqual(sum(clock.sleeps), 0.005, places=6)

    def test_realtime_eventually_exhausts(self) -> None:
        clock = FakeClock()
        src = ArraySource(
            np.ones(1000, dtype=np.int16),
            chunk_samples=320,
            realtime=True,
            clock=clock.monotonic,
            sleep=clock.sleep,
        )
        src.start()
        total = 0
        for _ in range(20):
            total += len(src.read(timeout=0.05))
        self.assertEqual(total, 1000)
        self.assertTrue(src.exhausted)


class FileSourceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def read_all(self, src: FileSource) -> np.ndarray:
        src.start()
        parts = []
        while not src.exhausted:
            parts.append(src.read(timeout=1.0))
        src.stop()
        return np.concatenate(parts)

    def test_16k_mono_int16_is_bit_exact(self) -> None:
        audio = (np.sin(np.arange(8000) / 10) * 10000).astype(np.int16)
        path = self.dir / "a.wav"
        write_wav(path, audio.astype("<i2").tobytes(), 16000, 1, 2)
        np.testing.assert_array_equal(self.read_all(FileSource(path)), audio)

    def test_48k_stereo_is_downmixed_and_resampled(self) -> None:
        t = np.arange(48000) / 48000
        left = (np.sin(2 * np.pi * 1000 * t) * 16000).astype(np.int16)
        right = np.zeros_like(left)
        frames = np.stack([left, right], axis=1).astype("<i2").tobytes()
        path = self.dir / "b.wav"
        write_wav(path, frames, 48000, 2, 2)
        out = self.read_all(FileSource(path))
        self.assertEqual(out.dtype, np.int16)
        self.assertEqual(len(out), 16000)
        mid = out[1600:-1600].astype(np.float64)
        # 左聲道 1 kHz、右聲道靜音 → 降混音後振幅減半（8000 → RMS ≈ 5657）。
        self.assertAlmostEqual(np.sqrt(np.mean(mid**2)), 8000 / np.sqrt(2), delta=60)

    def test_8bit_and_24bit_and_32bit_decoding(self) -> None:
        cases = {
            1: (bytes([128, 192, 64]), [0, 16384, -16384]),
            3: (
                b"".join(
                    v.to_bytes(3, "little", signed=True) for v in (0, 4194304, -8388608)
                ),
                [0, 16384, -32768],
            ),
            4: (
                b"".join(
                    v.to_bytes(4, "little", signed=True)
                    for v in (0, 1073741824, -(2**31))
                ),
                [0, 16384, -32768],
            ),
        }
        for width, (frames, expected) in cases.items():
            with self.subTest(width=width):
                path = self.dir / f"w{width}.wav"
                write_wav(path, frames, 16000, 1, width)
                np.testing.assert_array_equal(self.read_all(FileSource(path)), expected)

    def test_missing_file_raises_capture_error(self) -> None:
        src = FileSource(self.dir / "nope.wav")
        with self.assertRaises(CaptureError):
            src.start()

    def test_non_wav_file_raises_capture_error(self) -> None:
        path = self.dir / "junk.wav"
        path.write_bytes(b"not a wav file at all")
        with self.assertRaises(CaptureError):
            FileSource(path).start()

    def test_realtime_flag_is_forwarded(self) -> None:
        clock = FakeClock()
        path = self.dir / "c.wav"
        write_wav(path, np.zeros(16000, dtype="<i2").tobytes(), 16000, 1, 2)
        src = FileSource(path, realtime=True, clock=clock.monotonic, sleep=clock.sleep)
        src.start()
        self.assertEqual(len(src.read(timeout=0.0)), 0)
        clock.now += 0.5
        self.assertEqual(len(src.read(timeout=0.0)), 8000)

    def test_invalid_chunk_seconds(self) -> None:
        for bad in (0.0, -1.0, float("nan")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                FileSource(self.dir / "x.wav", chunk_seconds=bad)


if __name__ == "__main__":
    unittest.main()
