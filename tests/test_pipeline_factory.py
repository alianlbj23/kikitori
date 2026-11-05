"""pipeline.factory 的測試：設定 → 元件參數的對應。不載入真模型、不連網。

`load_transcriber` 以 mock 取代，只檢查傳給 asr 的參數。
"""

import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

from kikitori.config import Settings
from kikitori.languages import WHISPER_CODES
from kikitori.pipeline import factory
from kikitori.pipeline.types import PipelineComponents
from kikitori.translate import (
    TranslationError,
    TranslationRequest,
    TranslationTimeoutError,
    TranslatorConfig,
)


class FactoryTest(unittest.TestCase):
    def test_asr_languages_media_with_explicit_source(self) -> None:
        # 指定收聽語言：直接告訴 Whisper，不做語言偵測
        s = Settings().merged({"source_language": "ko"})
        self.assertEqual(factory.asr_languages(s, "media"), ("ko", ("ko",)))
        s = Settings().merged({"source_language": "zh-CN"})
        self.assertEqual(factory.asr_languages(s, "media"), ("zh", ("zh",)))

    def test_asr_languages_media_auto_is_limited_to_the_catalog(self) -> None:
        s = Settings().merged({"source_language": "auto"})
        self.assertEqual(factory.asr_languages(s, "media"), (None, WHISPER_CODES))

    def test_asr_languages_interpret_is_target_and_partner(self) -> None:
        s = Settings().merged({"partner_language": "en"})
        self.assertEqual(factory.asr_languages(s, "interpret"), (None, ("zh", "en")))
        s = Settings().merged({"target_language": "ko", "partner_language": "zh-CN"})
        self.assertEqual(factory.asr_languages(s, "interpret"), (None, ("ko", "zh")))
        # 收聽語言只影響影片翻譯
        s = Settings().merged({"source_language": "fr"})
        self.assertEqual(factory.asr_languages(s, "interpret"), (None, ("zh", "ja")))

    def test_asr_languages_names_forced_and_candidate_languages(self) -> None:
        media = factory.asr_languages(Settings(), "media")
        self.assertEqual(
            (media.forced_language, media.candidate_languages), ("ja", ("ja",))
        )
        interpret = factory.asr_languages(Settings(), "interpret")
        self.assertIsNone(interpret.forced_language)
        self.assertEqual(interpret.candidate_languages, ("zh", "ja"))

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
        self.assertEqual(media.source_language, "ja")
        self.assertTrue(media.translation_enabled)
        off = factory.pipeline_config(
            Settings().merged(
                {"source_language": "auto", "translation": {"enabled": False}}
            ),
            "media",
        )
        self.assertEqual(off.source_language, "auto")
        self.assertFalse(off.translation_enabled)

    def test_create_transcriber_passes_forced_language(self) -> None:
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "base",
                device="cpu",
                role="final",
                candidate_languages=("ko",),
                forced_language="ko",
            )
        options = load.call_args.kwargs["options"]
        self.assertEqual(
            (options.forced_language, options.candidate_languages), ("ko", ("ko",))
        )
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "base", device="cpu", role="fast", candidate_languages=None
            )
        self.assertIsNone(load.call_args.kwargs["options"].forced_language)

    def test_create_transcriber_passes_settings_to_asr(self) -> None:
        cuda_dir = str(Path(tempfile.gettempdir()).resolve())
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "small",
                device="cuda",
                role="fast",
                candidate_languages=("zh", "ja"),
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
                "base", device="cpu", role="fast", candidate_languages=None
            )
            self.assertEqual(load.call_args.kwargs["cpu_threads"], 0)
            factory.create_transcriber(
                "base",
                device="cpu",
                role="fast",
                candidate_languages=None,
                cpu_threads=3,
            )
            self.assertEqual(load.call_args.kwargs["cpu_threads"], 3)

    def test_final_role_uses_beam_search(self) -> None:
        with mock.patch("kikitori.asr.load_transcriber") as load:
            factory.create_transcriber(
                "base", device="cpu", role="final", candidate_languages=None
            )
        self.assertGreater(load.call_args.kwargs["options"].beam_size, 1)

    def test_unknown_model(self) -> None:
        with self.assertRaises(factory.UnknownModelError):
            factory.create_transcriber(
                "huge-v9", device="cpu", role="fast", candidate_languages=None
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

    def test_media_source_language_is_forced_on_both_paths(self) -> None:
        calls = self._calls(Settings().merged({"source_language": "es"}))
        for role in ("fast", "final"):
            options = calls[role]["options"]
            self.assertEqual(options.forced_language, "es")  # type: ignore[attr-defined]
            self.assertEqual(options.candidate_languages, ("es",))  # type: ignore[attr-defined]

    def test_media_auto_detects_within_catalog(self) -> None:
        calls = self._calls(Settings().merged({"source_language": "auto"}))
        for role in ("fast", "final"):
            options = calls[role]["options"]
            self.assertIsNone(options.forced_language)  # type: ignore[attr-defined]
            self.assertEqual(options.candidate_languages, WHISPER_CODES)  # type: ignore[attr-defined]

    def test_runtime_check_failure_is_logged_not_fatal(self) -> None:
        self.runtime_dir.side_effect = OSError("disk unplugged")
        with self.assertLogs("kikitori.pipeline.factory", "WARNING"):
            calls = self._calls(Settings())
        self.assertEqual(calls["fast"]["cuda_library_dirs"], [])


class _RecordingTranslator:
    def __init__(self, config: TranslatorConfig, log: list, fail: bool) -> None:
        self.config = config
        self._log = log
        self._fail = fail

    def translate(self, request: TranslationRequest) -> str:
        self._log.append((self.config.model, request))
        if self._fail:
            raise TranslationTimeoutError("no response within 60.0s")
        return "好"

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        raise AssertionError("warm-up must not stream")


class WarmUpTranslationTest(unittest.TestCase):
    """session 載入時先讓本機翻譯伺服器載入模型（Ollama 閒置 5 分鐘就會卸載模型，
    冷啟動要 4–10 秒：沒有預熱時第一句的即時翻譯逾時、前幾句的完整字幕晚 8–16 秒）。"""

    def _warm(self, settings: Settings, mode: str, fail: bool = False) -> list:
        self.configs: list[TranslatorConfig] = []
        log: list = []

        def make(config: TranslatorConfig) -> _RecordingTranslator:
            self.configs.append(config)
            return _RecordingTranslator(config, log, fail)

        factory.warm_up_translation(settings, mode, make_translator=make)
        return log

    def test_media_warms_the_shared_model_once(self) -> None:
        log = self._warm(Settings(), "media")
        self.assertEqual(len(log), 1)
        model, request = log[0]
        self.assertEqual(model, Settings().translation.final_model)
        self.assertEqual(
            (request.source_language, request.target_language), ("ja", "zh-TW")
        )
        self.assertTrue(request.text.strip())
        self.assertEqual(self.configs[0].max_retries, 0)
        self.assertEqual(self.configs[0].read_timeout, factory.WARM_UP_READ_TIMEOUT)

    def test_interpret_uses_partner_language_and_both_models(self) -> None:
        settings = Settings().merged(
            {
                "partner_language": "en",
                "translation": {
                    "fast_model": "qwen3.5:4b",
                    "final_model": "qwen3.5:9b",
                },
            }
        )
        log = self._warm(settings, "interpret")
        self.assertEqual([m for m, _ in log], ["qwen3.5:9b", "qwen3.5:4b"])
        self.assertEqual(log[0][1].source_language, "en")
        self.assertEqual(log[0][1].target_language, "zh-TW")

    def test_media_uses_the_source_language_direction(self) -> None:
        settings = Settings().merged({"source_language": "ko", "target_language": "es"})
        ((_, request),) = self._warm(settings, "media")
        self.assertEqual(
            (request.source_language, request.target_language), ("ko", "es")
        )
        self.assertTrue(request.text.strip())

    def test_media_auto_picks_a_source_different_from_the_target(self) -> None:
        settings = Settings().merged(
            {
                "source_language": "auto",
                "target_language": "ja",
                "partner_language": "en",
            }
        )
        ((_, request),) = self._warm(settings, "media")
        self.assertNotEqual(request.source_language, "ja")
        self.assertEqual(request.target_language, "ja")

    def test_auto_source_respects_direction_limited_models(self) -> None:
        settings = Settings().merged(
            {
                "source_language": "auto",
                "translation": {
                    "fast_model": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
                    "final_model": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
                },
            }
        )
        ((_, request),) = self._warm(settings, "media")
        self.assertEqual(
            (request.source_language, request.target_language), ("ja", "zh-TW")
        )

    def test_unsupported_direction_is_not_warmed(self) -> None:
        # Sakura 只做日 → 中：不論哪個分支，模型不支援的方向都不送預熱（送了只會被拒絕）
        sakura = "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
        models = {"fast_model": sakura, "final_model": sakura}
        media_en = Settings().merged({"source_language": "en", "translation": models})
        self.assertIsNone(factory.warm_up_request(media_en, "media"))
        self.assertEqual(self._warm(media_en, "media"), [])
        interpret_en = Settings().merged(
            {"partner_language": "en", "translation": models}
        )
        self.assertIsNone(factory.warm_up_request(interpret_en, "interpret"))
        # 支援的方向照常預熱
        interpret_ja = Settings().merged({"translation": models})
        request = factory.warm_up_request(interpret_ja, "interpret")
        assert request is not None
        self.assertEqual(
            (request.source_language, request.target_language), ("ja", "zh-TW")
        )

    def test_every_language_has_warm_up_text(self) -> None:
        for code in WHISPER_CODES:
            with self.subTest(code=code):
                self.assertTrue(factory.warm_up_text(code).strip())
        # 清單外的語言退回英文，不丟例外
        self.assertEqual(factory.warm_up_text("nn"), factory.warm_up_text("en"))

    def test_same_language_media_needs_no_warm_up(self) -> None:
        # 聽中文、看中文字幕只做字形轉換，不會用到翻譯模型
        settings = Settings().merged(
            {"source_language": "zh-CN", "target_language": "zh-TW"}
        )
        self.assertEqual(self._warm(settings, "media"), [])

    def test_translation_disabled_is_never_warmed(self) -> None:
        settings = Settings().merged({"translation": {"enabled": False}})
        for mode in ("media", "interpret"):
            with self.subTest(mode=mode):
                self.assertEqual(self._warm(settings, mode), [])

    def test_remote_server_is_not_warmed(self) -> None:
        settings = Settings().merged(
            {"translation": {"base_url": "https://api.example.com/v1"}}
        )
        self.assertEqual(self._warm(settings, "media"), [])

    def test_failure_is_a_warning_and_stops(self) -> None:
        settings = Settings().merged(
            {"translation": {"fast_model": "a", "final_model": "b"}}
        )
        with self.assertLogs("kikitori.pipeline.factory", "WARNING") as logs:
            log = self._warm(settings, "media", fail=True)
        self.assertEqual(len(log), 1)
        self.assertIn("no response", logs.output[0])

    def test_warm_up_runs_after_asr_is_loaded(self) -> None:
        # 不能與 ASR 同時載入：兩個行程同時在 GPU 上配置記憶體時，Ollama 估算的可用顯示
        # 記憶體會過時（端到端驗收時 llama-server 以 CUDA 錯誤當掉）。依序載入時它看到的
        # 是 ASR 載入後真正剩下的記憶體，不夠時會自己把部分層放到 CPU。
        components = object()
        order: list[str] = []
        with (
            mock.patch.object(
                factory,
                "build_components",
                side_effect=lambda *a: order.append("asr") or components,
            ) as build,
            mock.patch.object(
                factory,
                "warm_up_translation",
                side_effect=lambda *a: order.append("translation"),
            ) as warm,
            mock.patch.object(
                factory,
                "attach_keep_warm",
                side_effect=lambda c, *a: order.append("keep_warm") or c,
            ) as attach,
        ):
            result = factory.build_session_components(Settings(), "interpret")
        self.assertIs(result, components)
        self.assertEqual(order, ["asr", "translation", "keep_warm"])
        build.assert_called_once_with(Settings(), "interpret")
        warm.assert_called_once_with(Settings(), "interpret")
        attach.assert_called_once_with(components, Settings(), "interpret")

    def test_build_errors_propagate_without_warming(self) -> None:
        with (
            mock.patch.object(
                factory, "build_components", side_effect=FileNotFoundError("x")
            ),
            mock.patch.object(factory, "warm_up_translation") as warm,
            self.assertRaises(FileNotFoundError),
        ):
            factory.build_session_components(Settings(), "media")
        warm.assert_not_called()

    def test_engine_uses_the_warming_builder(self) -> None:
        from kikitori.engine.services import default_services

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict("os.environ", {"KIKITORI_CONFIG_DIR": tmp}),
        ):
            services = default_services()
        self.assertIs(services.build_components, factory.build_session_components)


def _components() -> PipelineComponents:
    """attach_keep_warm 只換翻譯器與 keep_warm；其他元件不必是真的。"""
    return PipelineComponents(
        voice_model=mock.Mock(),
        fast_transcriber=mock.Mock(),
        final_transcriber=mock.Mock(),
        fast_translator=_RecordingTranslator(TranslatorConfig(model="fast"), [], False),
        final_translator=_RecordingTranslator(
            TranslatorConfig(model="final"), [], False
        ),
        transcript_filter=lambda t: None,
    )


class KeepWarmTest(unittest.TestCase):
    """長時間 session 中翻譯請求停了一陣子（例如影片沒有對白）時，定期對本機翻譯伺服器
    送一次預熱請求：Ollama 閒置 5 分鐘就卸載模型，OpenAI 相容端點又不能設 keep_alive。"""

    def test_local_server_gets_a_keep_warm_task_and_tracked_translators(self) -> None:
        components = _components()
        result = factory.attach_keep_warm(components, Settings(), "media")
        keep = result.keep_warm
        self.assertIsNotNone(keep)
        self.assertEqual(keep.interval, 240.0)
        self.assertFalse(keep.is_running)  # pipeline.start() 才啟動
        before = keep.idle_for()
        request = TranslationRequest("はい。", "ja", "zh-TW")
        self.assertEqual(result.final_translator.translate(request), "好")
        self.assertLessEqual(keep.idle_for(), before)
        # 其他元件原樣保留
        self.assertIs(result.voice_model, components.voice_model)

    def test_ping_uses_the_warm_up_request_and_raises_on_failure(self) -> None:
        log: list = []
        configs: list[TranslatorConfig] = []

        def make(config: TranslatorConfig) -> _RecordingTranslator:
            configs.append(config)
            return _RecordingTranslator(config, log, fail=False)

        settings = Settings().merged(
            {"translation": {"fast_model": "a", "final_model": "b"}}
        )
        factory.ping_translation_models(settings, "media", make_translator=make)
        self.assertEqual([m for m, _ in log], ["b", "a"])
        self.assertTrue(
            all(c.read_timeout == factory.WARM_UP_READ_TIMEOUT for c in configs)
        )
        self.assertTrue(all(c.max_retries == 0 for c in configs))

        def failing(config: TranslatorConfig) -> _RecordingTranslator:
            return _RecordingTranslator(config, log, fail=True)

        with self.assertRaisesRegex(factory.WarmUpError, "b: no response") as caught:
            factory.ping_translation_models(settings, "media", make_translator=failing)
        # 保溫的背景工作以 TranslationError 判斷是「伺服器的問題」（限流的 warning）。
        self.assertIsInstance(caught.exception, TranslationError)
        self.assertIsInstance(caught.exception.__cause__, TranslationTimeoutError)

    def test_keep_warm_pings_through_the_factory(self) -> None:
        log: list = []
        result = factory.attach_keep_warm(
            _components(),
            Settings(),
            "media",
            make_translator=lambda c: _RecordingTranslator(c, log, fail=False),
        )
        assert result.keep_warm is not None
        result.keep_warm.ping_now()
        self.assertEqual(len(log), 1)
        self.assertEqual(log[0][0], Settings().translation.final_model)

    def test_remote_server_is_never_pinged(self) -> None:
        components = _components()
        settings = Settings().merged(
            {"translation": {"base_url": "https://api.example.com/v1"}}
        )
        self.assertIs(
            factory.attach_keep_warm(components, settings, "media"), components
        )

    def test_translation_disabled_gets_no_keep_warm(self) -> None:
        components = _components()
        settings = Settings().merged({"translation": {"enabled": False}})
        self.assertIs(
            factory.attach_keep_warm(components, settings, "media"), components
        )

    def test_translation_disabled_session_never_calls_the_translator(self) -> None:
        # 只顯示原文：預熱、保溫、pipeline 都不呼叫翻譯器（以計數的假翻譯器確認）
        calls: list = []
        settings = Settings().merged({"translation": {"enabled": False}})

        def make(config: TranslatorConfig) -> _RecordingTranslator:
            return _RecordingTranslator(config, calls, fail=False)

        factory.warm_up_translation(settings, "media", make_translator=make)
        components = factory.attach_keep_warm(
            _components(), settings, "media", make_translator=make
        )
        self.assertIsNone(components.keep_warm)
        self.assertEqual(calls, [])

    def test_disabled_by_zero(self) -> None:
        components = _components()
        settings = Settings().merged({"translation": {"keep_warm_secs": 0}})
        self.assertIs(
            factory.attach_keep_warm(components, settings, "media"), components
        )


if __name__ == "__main__":
    unittest.main()
