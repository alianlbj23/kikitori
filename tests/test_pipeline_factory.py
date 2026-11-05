"""pipeline.factory 的測試：設定 → 元件參數的對應。不載入真模型、不連網。

`load_transcriber` 以 mock 取代，只檢查傳給 asr 的參數。
"""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kikitori.config import Settings
from kikitori.pipeline import factory


class FactoryTest(unittest.TestCase):
    def test_candidate_languages(self) -> None:
        self.assertEqual(factory.candidate_languages("media", "ja"), ("ja", "en"))
        self.assertEqual(factory.candidate_languages("interpret", "en"), ("zh", "en"))

    def test_pipeline_config_per_mode(self) -> None:
        s = Settings().merged(
            {"partner_language": "en", "pipeline": {"partial_interval_ms": 900}}
        )
        media = factory.pipeline_config(s, "media")
        interpret = factory.pipeline_config(s, "interpret")
        self.assertEqual((media.pause_secs, media.max_secs), (0.6, 10.0))
        self.assertEqual((interpret.pause_secs, interpret.max_secs), (0.5, 8.0))
        self.assertEqual(interpret.partner_language, "en")
        self.assertEqual(media.partial_interval_ms, 900)

    def test_create_transcriber_passes_settings_to_asr(self) -> None:
        cuda_dir = str(Path(tempfile.gettempdir()).resolve())
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "small",
                device="cuda",
                role="fast",
                languages=("zh", "ja"),
                compute_type="int8_float16",
                cuda_library_dirs=(cuda_dir,),
            )
        (spec,), kwargs = load.call_args
        self.assertEqual(spec.id, "small")
        self.assertEqual(kwargs["device"], "cuda")
        self.assertEqual(kwargs["compute_type"], "int8_float16")
        self.assertEqual(kwargs["cuda_library_dirs"], [Path(cuda_dir)])
        self.assertFalse(kwargs["download"])
        self.assertEqual(kwargs["options"].beam_size, 1)  # 即時路徑 greedy
        self.assertEqual(kwargs["options"].candidate_languages, ("zh", "ja"))

    def test_final_role_uses_beam_search(self) -> None:
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "base", device="cpu", role="final", languages=None
            )
        self.assertGreater(load.call_args.kwargs["options"].beam_size, 1)

    def test_unknown_model(self) -> None:
        with self.assertRaises(factory.UnknownModelError):
            factory.create_transcriber(
                "huge-v9", device="cpu", role="fast", languages=None
            )
        with self.assertRaises(factory.UnknownModelError):
            factory.no_speech_reliable("huge-v9")

    def test_no_speech_reliability_from_registry(self) -> None:
        self.assertFalse(factory.no_speech_reliable("large-v3-turbo"))
        self.assertTrue(factory.no_speech_reliable("small"))

    def test_translators(self) -> None:
        fast, final = factory.create_translators(Settings())
        self.assertEqual(fast.config.max_retries, 0)
        self.assertLess(fast.config.read_timeout, final.config.read_timeout)
        self.assertEqual(final.config.model, Settings().translation.final_model)

    def test_transcript_filter_uses_asr_rules(self) -> None:
        from kikitori.asr.types import Transcript

        reject = factory.create_transcript_filter(0.6)
        self.assertIsNone(reject(Transcript("今日はいい天気", "ja", 1.0, 0.05, -0.3)))
        self.assertIsNotNone(
            reject(Transcript("ご視聴ありがとうございました", "ja", 1.0, 0.0, -0.3))
        )


if __name__ == "__main__":
    unittest.main()
