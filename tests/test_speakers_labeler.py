"""說話者向量（假的 ONNX session）與 labeler 的組合。"""

import hashlib
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import numpy as np

from kikitori.asr.download import ModelIntegrityError
from kikitori.asr.registry import ModelFile
from kikitori.speakers.clustering import OnlineSpeakers
from kikitori.speakers.embedding import SpeakerEmbedder, create_session
from kikitori.speakers.labeler import VoiceLabeler, load_labeler
from kikitori.speakers.model import CAMPPLUS_ZH_EN, model_path


class _Session:
    """依音訊音量回傳固定方向的向量，記下收到的特徵形狀。"""

    def __init__(self, output: np.ndarray | None = None) -> None:
        self.output = output
        self.shapes: list[tuple[int, ...]] = []

    def run(self, output_names, input_feed):
        x = input_feed["x"]
        self.shapes.append(x.shape)
        if self.output is not None:
            return [self.output]
        # 減掉時間平均後每個 bin 的平均是 0：用它檢查 global-mean 真的做了
        assert np.allclose(x.mean(axis=1), 0.0, atol=1e-4)
        return [np.full((1, 4), 2.0, dtype=np.float32)]


def _tone(secs: float, freq: float = 300.0) -> np.ndarray:
    n = np.arange(int(secs * 16000))
    return (8000 * np.sin(2 * np.pi * freq * n / 16000)).astype(np.int16)


class SpeakerEmbedderTest(unittest.TestCase):
    def test_features_and_normalisation(self) -> None:
        session = _Session()
        vector = SpeakerEmbedder(session, 4).embed(_tone(1.0))
        self.assertEqual(session.shapes, [(1, 100, 80)])
        self.assertEqual(vector.dtype, np.float32)
        np.testing.assert_allclose(vector, [0.5, 0.5, 0.5, 0.5], rtol=1e-6)

    def test_rejects_bad_audio_and_bad_outputs(self) -> None:
        embedder = SpeakerEmbedder(_Session(), 4)
        with self.assertRaises(ValueError):
            embedder.embed(_tone(1.0).astype(np.float32))
        with self.assertRaises(ValueError):
            embedder.embed(np.zeros((2, 800), dtype=np.int16))
        for output in (
            np.ones((1, 3), dtype=np.float32),
            np.zeros((1, 4), dtype=np.float32),
            np.full((1, 4), np.nan, dtype=np.float32),
        ):
            with self.subTest(output=output.tolist()), self.assertRaises(ValueError):
                SpeakerEmbedder(_Session(output), 4).embed(_tone(1.0))
        with self.assertRaises(ValueError):
            SpeakerEmbedder(_Session(), 0)

    def test_create_session_rejects_a_non_onnx_model(self) -> None:
        with self.assertRaises(Exception):  # noqa: B017 - onnxruntime 的例外型別依版本不同
            create_session(b"not an onnx model")


class VoiceLabelerTest(unittest.TestCase):
    def test_labels_follow_the_embeddings(self) -> None:
        voices = {
            300.0: np.array([1, 0], np.float32),
            900.0: np.array([0, 1], np.float32),
        }

        def embed(audio: np.ndarray) -> np.ndarray:
            # 用過零率分辨兩個假的「聲音」
            crossings = np.count_nonzero(np.diff(np.signbit(audio)))
            return voices[300.0] if crossings / len(audio) < 0.06 else voices[900.0]

        labeler = VoiceLabeler(embed, OnlineSpeakers())
        labels = [labeler.label(_tone(2.0, f)) for f in (300.0, 900.0, 300.0)]
        self.assertEqual(labels, [1, 2, 1])
        self.assertEqual(labeler.label(_tone(0.3, 900.0)), 2)  # 短句但夠像已知的人
        self.assertIsNone(labeler.label(np.zeros(0, dtype=np.int16)))
        fresh = VoiceLabeler(embed, OnlineSpeakers())
        self.assertIsNone(fresh.label(_tone(0.3)))  # 第一句就很短：不開新的人

    def test_load_labeler_needs_a_verified_model(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with self.assertRaises(FileNotFoundError):
                load_labeler(None, root=root)
            payload = b"z" * 64
            spec = replace(
                CAMPPLUS_ZH_EN,
                file=ModelFile("m.onnx", 64, hashlib.sha256(b"other").hexdigest()),
            )
            path = model_path(root, spec)
            path.parent.mkdir(parents=True)
            path.write_bytes(payload)
            with self.assertRaises(ModelIntegrityError):
                load_labeler(None, root=root, spec=spec)
            with self.assertRaises(ValueError):
                load_labeler(1, root=root, spec=spec)


if __name__ == "__main__":
    unittest.main()
