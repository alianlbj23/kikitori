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

    def test_create_transcriber_cpu_threads_default_and_explicit(self) -> None:
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "base", device="cpu", role="fast", languages=None
            )
            self.assertEqual(load.call_args.kwargs["cpu_threads"], 0)
            factory.create_transcriber(
                "base", device="cpu", role="fast", languages=None, cpu_threads=3
            )
            self.assertEqual(load.call_args.kwargs["cpu_threads"], 3)

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


class BuildComponentsAsrTest(unittest.TestCase):
    """設定 → 兩條路徑各自的 `load_transcriber` 參數（含 GPU 執行環境目錄）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name).resolve()
        self.configured = root / "cuda-configured"
        self.runtime = root / "gpu-runtime" / "cublas-12.8.4.1"
        patches = [
            mock.patch("kikitori.asr.load_transcriber"),
            mock.patch("kikitori.vad.SileroVad"),
            mock.patch("kikitori.hardware.installed_runtime_dir"),
            mock.patch("kikitori.hardware.auto_cpu_threads", return_value=4),
        ]
        self.load, _, self.runtime_dir, self.auto_threads = [p.start() for p in patches]
        for p in patches:
            self.addCleanup(p.stop)
        self.runtime_dir.return_value = self.runtime

    def _calls(self, settings: Settings) -> dict[str, dict[str, object]]:
        factory.build_components(settings, "media")
        self.assertEqual(self.load.call_count, 2)
        by_role: dict[str, dict[str, object]] = {}
        for call in self.load.call_args_list:
            role = "fast" if call.kwargs["options"].beam_size == 1 else "final"
            by_role[role] = {"model": call.args[0].id, **call.kwargs}
        return by_role

    def test_per_path_parameters_and_cuda_dirs(self) -> None:
        settings = Settings().merged(
            {
                "asr": {
                    "fast_model": "small",
                    "final_model": "large-v3-turbo",
                    "device": "cuda",
                    "fast_compute_type": "int8_float16",
                    "final_compute_type": "float16",
                    "fast_cpu_threads": 2,
                    "final_cpu_threads": 6,
                    "cuda_library_dirs": [str(self.configured)],
                }
            }
        )
        calls = self._calls(settings)
        fast, final = calls["fast"], calls["final"]
        self.assertEqual(
            (fast["model"], fast["device"], fast["compute_type"], fast["cpu_threads"]),
            ("small", "cuda", "int8_float16", 2),
        )
        self.assertEqual(
            (
                final["model"],
                final["device"],
                final["compute_type"],
                final["cpu_threads"],
            ),
            ("large-v3-turbo", "cuda", "float16", 6),
        )
        # 設定的目錄優先，再加上已安裝的 GPU 執行環境
        expected = [self.configured, self.runtime]
        self.assertEqual(fast["cuda_library_dirs"], expected)
        self.assertEqual(final["cuda_library_dirs"], expected)
        self.auto_threads.assert_not_called()

    def test_auto_threads_and_no_runtime(self) -> None:
        self.runtime_dir.return_value = None
        calls = self._calls(Settings())
        for role in ("fast", "final"):
            self.assertEqual(calls[role]["device"], "auto")
            self.assertIsNone(calls[role]["compute_type"])
            self.assertEqual(calls[role]["cpu_threads"], 4)
            self.assertEqual(calls[role]["cuda_library_dirs"], [])

    def test_cuda_device_with_auto_threads_uses_ctranslate2_default(self) -> None:
        settings = Settings().merged({"asr": {"device": "cuda"}})
        calls = self._calls(settings)
        self.assertEqual(calls["fast"]["cpu_threads"], 0)
        self.assertEqual(calls["final"]["cpu_threads"], 0)
        self.auto_threads.assert_not_called()

    def test_runtime_dir_not_duplicated(self) -> None:
        settings = Settings().merged(
            {"asr": {"cuda_library_dirs": [str(self.runtime)]}}
        )
        calls = self._calls(settings)
        self.assertEqual(calls["fast"]["cuda_library_dirs"], [self.runtime])

    def test_runtime_check_failure_is_logged_not_fatal(self) -> None:
        self.runtime_dir.side_effect = OSError("disk unplugged")
        with self.assertLogs("kikitori.pipeline.factory", "WARNING"):
            calls = self._calls(Settings())
        self.assertEqual(calls["fast"]["cuda_library_dirs"], [])


if __name__ == "__main__":
    unittest.main()
