"""ASR 基準測試腳本的純邏輯測試（CER、片段切割、選單輸入解析）；不需要模型與本機音訊。

`experiments/` 不是套件，依檔案路徑載入。
"""

import importlib.util
import json
import tempfile
import unittest
import wave
from pathlib import Path
from types import ModuleType
from unittest import mock

import numpy as np

EXPERIMENT_DIR = (
    Path(__file__).resolve().parent.parent / "experiments" / "asr_benchmark"
)


def load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"asr_benchmark_{name}", EXPERIMENT_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = load("benchmark")
menu = load("menu")


def write_wav(path: Path, audio: np.ndarray, rate: int = 16000) -> None:
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(audio.astype(np.int16).tobytes())


class CerTest(unittest.TestCase):
    def test_edit_distance(self) -> None:
        self.assertEqual(benchmark.edit_distance("kitten", "sitting"), 3)
        self.assertEqual(benchmark.edit_distance("", "abc"), 3)
        self.assertEqual(benchmark.edit_distance("同じ", "同じ"), 0)

    def test_normalization_ignores_punctuation_width_and_case(self) -> None:
        self.assertEqual(
            benchmark.normalize_for_cer("うん。 アイ、ＡＢ？"), "うんアイab"
        )

    def test_corpus_cer_weights_by_reference_length(self) -> None:
        pairs = [("あいうえお", "あいうえお"), ("かき", "かく")]
        self.assertAlmostEqual(benchmark.corpus_cer(pairs), 1 / 7)

    def test_empty_references_rejected(self) -> None:
        with self.assertRaises(ValueError):
            benchmark.corpus_cer([("。", "x")])


class LoadClipsTest(unittest.TestCase):
    def test_segments_and_references(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            data = Path(tmp)
            (data / "tracks").mkdir()
            (data / "raw").mkdir()
            track = np.arange(32000, dtype=np.int16)
            for name in ("anime", "vtuber", "synthetic"):
                write_wav(data / "tracks" / f"{name}.wav", track)
            truth = {
                "sample_rate": 16000,
                "tracks": {
                    "anime": {
                        "segments": [
                            {
                                "file": "anime_001.mp3",
                                "start_sample": 100,
                                "end_sample": 200,
                            }
                        ]
                    },
                    "vtuber": {
                        "segments": [
                            {
                                "file": "vtuber_000.mp3",
                                "start_sample": 0,
                                "end_sample": 50,
                            }
                        ]
                    },
                },
            }
            (data / "tracks" / "ground_truth.json").write_text(
                json.dumps(truth), encoding="utf-8"
            )
            manifest = {
                "anime": [{"file": "anime_001.mp3", "text": "こんにちは"}],
                "vtuber": [{"file": "vtuber_000.mp3"}],
            }
            (data / "raw" / "manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8"
            )

            clips = benchmark.load_clips(data)

        self.assertEqual(
            [c.name for c in clips],
            ["anime_001", "vtuber_000", "synthetic_noise", "digital_silence"],
        )
        self.assertEqual(clips[0].reference, "こんにちは")
        np.testing.assert_array_equal(clips[0].audio, track[100:200])
        self.assertIsNone(clips[1].reference)
        self.assertEqual(
            [c.kind for c in clips], ["speech", "speech", "noise", "noise"]
        )

    def test_wrong_sample_rate_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.wav"
            write_wav(path, np.zeros(10, dtype=np.int16), rate=44100)
            with self.assertRaises(ValueError):
                benchmark.read_wav_int16(path)


class MenuInputTest(unittest.TestCase):
    def test_multi_select_and_all(self) -> None:
        with (
            mock.patch("builtins.input", side_effect=["3,1", "a"]),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(menu.ask_choices(["x", "y", "z"], "pick"), [0, 2])
            self.assertEqual(menu.ask_choices(["x", "y", "z"], "pick"), [0, 1, 2])

    def test_invalid_input_asks_again(self) -> None:
        with (
            mock.patch("builtins.input", side_effect=["9", "", "a", "1,2", "2"]),
            mock.patch("builtins.print"),
        ):
            self.assertEqual(menu.ask_choices(["x", "y"], "pick", allow_all=False), [1])

    def test_unknown_model(self) -> None:
        with self.assertRaises(KeyError):
            benchmark.resolve_spec("nope")
        self.assertEqual(
            benchmark.resolve_spec("kotoba-whisper-v2.0").languages, ("ja",)
        )


class RescoreTest(unittest.TestCase):
    def test_rescore_uses_current_filter(self) -> None:
        def row(
            clip: str, kind: str, text: str, no_speech: float, language: str = "en"
        ) -> dict[str, object]:
            return {
                "clip": clip,
                "kind": kind,
                "text": text,
                "no_speech": no_speech,
                "avg_logprob": -0.3,
                "detected_language": language,
                "rejected": None,
            }

        result = {
            "label": "x",
            "rows": [
                row("a", "speech", "こんにちは", 0.01),
                # 幻覺片語只比對辨識出的語言：日文套話是以 ja 的語言 token 輸出的
                row("n1", "noise", "ご視聴ありがとうございました", 0.01, "ja"),
                row("n2", "noise", "", 0.9),
            ],
        }
        rescored = benchmark.rescore(result)
        self.assertEqual(
            (rescored["noise_rejected"], rescored["speech_rejected"]), (2, 0)
        )
        self.assertEqual(rescored["rows"][1]["rejected"], "known_hallucination")


if __name__ == "__main__":
    unittest.main()
