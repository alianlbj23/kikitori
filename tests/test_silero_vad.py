"""SileroVad 的測試：模型雜湊驗證、模型輸入輸出規格。"""

import hashlib
import tempfile
import unittest
from importlib import resources
from pathlib import Path

import numpy as np

from kikitori.vad import silero
from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES


class PackagedModelTest(unittest.TestCase):
    def test_packaged_model_matches_pinned_hash(self) -> None:
        data = resources.files("kikitori.vad").joinpath("models/silero_vad.onnx")
        digest = hashlib.sha256(data.read_bytes()).hexdigest()
        self.assertEqual(digest, silero.MODEL_SHA256)

    def test_read_verified_model_returns_packaged_bytes(self) -> None:
        data = silero.read_verified_model()
        self.assertEqual(hashlib.sha256(data).hexdigest(), silero.MODEL_SHA256)

    def test_license_is_packaged(self) -> None:
        text = (
            resources.files("kikitori.vad")
            .joinpath("models/LICENSE-silero-vad")
            .read_text(encoding="utf-8")
        )
        self.assertIn("MIT License", text)
        self.assertIn("Copyright (c) 2020-present Silero Team", text)

    def test_tampered_model_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "silero_vad.onnx"
            path.write_bytes(b"not the real model")
            with self.assertRaises(silero.ModelIntegrityError) as ctx:
                silero.read_verified_model(path)
        self.assertIn(silero.MODEL_SHA256, str(ctx.exception))
        self.assertIn(str(path), str(ctx.exception))

    def test_missing_model_file_is_reported(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "gone.onnx"
            with self.assertRaises(FileNotFoundError):
                silero.read_verified_model(path)


class ModelSignatureTest(unittest.TestCase):
    """真模型的輸入輸出要與規格一致：input / state (2, B, 128) / sr。"""

    def test_session_inputs_and_outputs_match_spec(self) -> None:
        session = silero.create_session(silero.read_verified_model())
        inputs = {i.name: i.shape for i in session.get_inputs()}
        self.assertEqual(set(inputs), {"input", "state", "sr"})
        self.assertEqual(inputs["state"][0], 2)
        self.assertEqual(inputs["state"][2], 128)
        self.assertEqual(len(session.get_outputs()), 2)


class RecordingSession:
    """假的 ONNX session：記下每次的輸入，回傳固定機率與「state + 1」。"""

    def __init__(self, prob: float = 0.25) -> None:
        self.calls: list[dict[str, np.ndarray]] = []
        self.output_names: list[list[str] | None] = []
        self.prob = prob

    def run(
        self, output_names: list[str] | None, input_feed: dict[str, np.ndarray]
    ) -> list[np.ndarray]:
        self.output_names.append(output_names)
        self.calls.append({k: np.array(v, copy=True) for k, v in input_feed.items()})
        return [
            np.array([[self.prob]], dtype=np.float32),
            (input_feed["state"] + 1).astype(np.float32),
        ]


def ramp_window(start: int) -> np.ndarray:
    return (np.arange(start, start + WINDOW_SAMPLES) % 30000).astype(np.int16)


class SileroVadContextTest(unittest.TestCase):
    def setUp(self) -> None:
        self.session = RecordingSession()
        self.vad = silero.SileroVad(session=self.session)

    def test_returns_session_probability(self) -> None:
        self.assertAlmostEqual(self.vad.probability(ramp_window(0)), 0.25, places=6)
        self.assertEqual(self.session.output_names, [["output", "stateN"]])

    def test_input_is_576_samples_with_zero_context_first(self) -> None:
        window = ramp_window(100)
        self.vad.probability(window)
        feed = self.session.calls[0]
        self.assertEqual(feed["input"].shape, (1, 576))
        self.assertEqual(feed["input"].dtype, np.float32)
        np.testing.assert_array_equal(feed["input"][0, :64], np.zeros(64))
        np.testing.assert_allclose(feed["input"][0, 64:], window / 32768.0)
        self.assertEqual(int(feed["sr"]), 16000)
        self.assertEqual(feed["sr"].dtype, np.int64)

    def test_context_is_tail_of_previous_window(self) -> None:
        first, second = ramp_window(0), ramp_window(5000)
        self.vad.probability(first)
        self.vad.probability(second)
        feed = self.session.calls[1]["input"][0]
        np.testing.assert_allclose(feed[:64], first[-64:] / 32768.0)
        np.testing.assert_allclose(feed[64:], second / 32768.0)

    def test_state_is_carried_between_calls(self) -> None:
        self.vad.probability(ramp_window(0))
        self.vad.probability(ramp_window(1))
        first, second = (c["state"] for c in self.session.calls)
        self.assertEqual(first.shape, (2, 1, 128))
        self.assertEqual(first.dtype, np.float32)
        np.testing.assert_array_equal(first, np.zeros((2, 1, 128)))
        np.testing.assert_array_equal(second, np.ones((2, 1, 128)))

    def test_reset_clears_context_and_state(self) -> None:
        self.vad.probability(ramp_window(7))
        self.vad.probability(ramp_window(9))
        self.vad.reset()
        self.vad.probability(ramp_window(11))
        feed = self.session.calls[-1]
        np.testing.assert_array_equal(feed["input"][0, :64], np.zeros(64))
        np.testing.assert_array_equal(feed["state"], np.zeros((2, 1, 128)))

    def test_float32_window_is_used_as_is(self) -> None:
        window = np.linspace(-0.5, 0.5, WINDOW_SAMPLES, dtype=np.float32)
        self.vad.probability(window)
        np.testing.assert_array_equal(self.session.calls[0]["input"][0, 64:], window)

    def test_without_context_input_is_512_samples(self) -> None:
        session = RecordingSession()
        vad = silero.SileroVad(context_samples=0, session=session)
        vad.probability(ramp_window(0))
        vad.probability(ramp_window(1))
        self.assertEqual(session.calls[1]["input"].shape, (1, 512))
        np.testing.assert_allclose(
            session.calls[1]["input"][0], ramp_window(1) / 32768.0
        )


class SileroVadValidationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.vad = silero.SileroVad(session=RecordingSession())

    def test_rejects_wrong_length(self) -> None:
        for n in (0, 511, 513, 576):
            with self.subTest(n=n), self.assertRaises(ValueError) as ctx:
                self.vad.probability(np.zeros(n, dtype=np.int16))
            self.assertIn(str(n), str(ctx.exception))

    def test_rejects_wrong_dtype(self) -> None:
        for dtype in (np.int32, np.float64, np.uint8):
            with self.subTest(dtype=dtype), self.assertRaises(ValueError) as ctx:
                self.vad.probability(np.zeros(WINDOW_SAMPLES, dtype=dtype))
            self.assertIn(np.dtype(dtype).name, str(ctx.exception))

    def test_rejects_non_1d(self) -> None:
        with self.assertRaises(ValueError):
            self.vad.probability(np.zeros((1, WINDOW_SAMPLES), dtype=np.int16))

    def test_rejects_non_array(self) -> None:
        with self.assertRaises(TypeError):
            self.vad.probability([0] * WINDOW_SAMPLES)  # type: ignore[arg-type]

    def test_rejects_non_finite_float(self) -> None:
        window = np.zeros(WINDOW_SAMPLES, dtype=np.float32)
        window[3] = np.nan
        with self.assertRaises(ValueError):
            self.vad.probability(window)

    def test_rejects_invalid_context_samples(self) -> None:
        for n in (-1, 65, 1.5):
            with self.subTest(n=n), self.assertRaises(ValueError):
                silero.SileroVad(context_samples=n, session=RecordingSession())  # type: ignore[arg-type]

    def test_rejects_unexpected_state_shape(self) -> None:
        class BadSession(RecordingSession):
            def run(self, output_names, input_feed):  # type: ignore[override]
                return [np.array([[0.1]], np.float32), np.zeros((2, 1, 64))]

        vad = silero.SileroVad(session=BadSession())
        with self.assertRaises(RuntimeError):
            vad.probability(np.zeros(WINDOW_SAMPLES, dtype=np.int16))


def probabilities(vad: silero.SileroVad, audio: np.ndarray) -> np.ndarray:
    n = len(audio) // WINDOW_SAMPLES
    return np.array(
        [
            vad.probability(audio[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES])
            for i in range(n)
        ]
    )


class SileroVadRealModelTest(unittest.TestCase):
    """真模型：非人聲的合成訊號機率都要低於 0.5。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.vad = silero.SileroVad()

    def setUp(self) -> None:
        self.vad.reset()

    def test_silence_is_not_speech(self) -> None:
        probs = probabilities(self.vad, np.zeros(SAMPLE_RATE * 2, dtype=np.int16))
        self.assertLess(probs.max(), 0.5)

    def test_sine_is_not_speech(self) -> None:
        t = np.arange(SAMPLE_RATE * 3) / SAMPLE_RATE
        sine = (0.5 * 32767 * np.sin(2 * np.pi * 440 * t)).astype(np.int16)
        probs = probabilities(self.vad, sine)
        self.assertLess(probs.max(), 0.5)

    def test_reset_gives_identical_output(self) -> None:
        rng = np.random.default_rng(1)
        audio = (rng.standard_normal(SAMPLE_RATE) * 3000).astype(np.int16)
        first = probabilities(self.vad, audio)
        self.vad.reset()
        second = probabilities(self.vad, audio)
        np.testing.assert_array_equal(first, second)


if __name__ == "__main__":
    unittest.main()
