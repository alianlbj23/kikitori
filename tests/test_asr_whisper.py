"""WhisperTranscriber 的測試：注入假的 CTranslate2 模型，不需下載模型。"""

import json
import tempfile
import threading
import time
import unittest
from collections.abc import Iterable
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest import mock

import numpy as np

from kikitori.asr import whisper as whisper_module
from kikitori.asr.registry import ModelFile, ModelSpec
from kikitori.asr.tokenizer import WhisperTokenizer, bytes_to_unicode
from kikitori.asr.whisper import (
    FAST_DECODE,
    FINAL_DECODE,
    MAX_PROMPT_TOKENS,
    DecodeOptions,
    WhisperTranscriber,
    load_transcriber,
    preload_cuda_libraries,
)

SPECIALS = [
    "<|endoftext|>",
    "<|startoftranscript|>",
    "<|en|>",
    "<|zh|>",
    "<|ja|>",
    "<|translate|>",
    "<|transcribe|>",
    "<|startoflm|>",
    "<|startofprev|>",
    "<|nospeech|>",
    "<|notimestamps|>",
]


def tokenizer_data() -> dict[str, Any]:
    vocab = {ch: i for i, ch in enumerate(bytes_to_unicode().values())}
    added = [
        {"id": 256 + i, "content": t, "special": True} for i, t in enumerate(SPECIALS)
    ]
    return {
        "model": {"type": "BPE", "vocab": vocab, "merges": []},
        "added_tokens": added,
    }


TOKENIZER = WhisperTokenizer(tokenizer_data())


def result(text: str, score: float = -0.1, no_speech: float = 0.01) -> SimpleNamespace:
    ids = TOKENIZER.encode(text)
    return SimpleNamespace(
        sequences_ids=[ids], scores=[score], no_speech_prob=no_speech
    )


class FakeWhisper:
    """記錄呼叫並依序回傳預先給定結果的假模型。"""

    def __init__(
        self,
        results: Iterable[SimpleNamespace] = (),
        languages: list[tuple[str, float]] | None = None,
        multilingual: bool = True,
        n_mels: int = 80,
    ) -> None:
        self.results = list(results)
        self.languages = languages or [
            ("<|ja|>", 0.6),
            ("<|en|>", 0.3),
            ("<|zh|>", 0.1),
        ]
        self.is_multilingual = multilingual
        self.n_mels = n_mels
        self.encoded: list[np.ndarray] = []
        self.detect_calls = 0
        self.generate_calls: list[tuple[list[list[int]], dict[str, Any]]] = []
        self.busy = False
        self.overlap = False

    def encode(self, features: Any, to_cpu: bool = False) -> Any:
        self.encoded.append(np.asarray(features))
        return ("encoded", len(self.encoded) - 1)

    def detect_language(self, features: Any) -> list[list[tuple[str, float]]]:
        self.detect_calls += 1
        return [self.languages]

    def generate(
        self, features: Any, prompts: list[list[int]], **kwargs: Any
    ) -> list[Any]:
        if self.busy:
            self.overlap = True
        self.busy = True
        time.sleep(0.01)
        self.busy = False
        self.generate_calls.append((prompts, kwargs))
        if not self.results:
            return [result("default")]
        return [self.results.pop(0)]


def make(
    model: FakeWhisper, options: DecodeOptions | None = None
) -> WhisperTranscriber:
    return WhisperTranscriber(model, TOKENIZER, options, storage=lambda a: a)


AUDIO = np.zeros(16000, dtype=np.int16)
T = TOKENIZER
JA_PROMPT = [T.sot, T.language_token("ja"), T.transcribe, T.no_timestamps]


class TranscribeTest(unittest.TestCase):
    def test_given_language_skips_detection(self) -> None:
        model = FakeWhisper([result(" こんにちは")])
        out = make(model).transcribe(AUDIO, language="ja")
        self.assertEqual(out.text, "こんにちは")
        self.assertEqual(out.language, "ja")
        self.assertEqual(out.language_probability, 1.0)
        self.assertEqual(model.detect_calls, 0)
        prompts, _ = model.generate_calls[0]
        self.assertEqual(prompts, [JA_PROMPT])

    def test_features_shape_passed_to_encoder(self) -> None:
        model = FakeWhisper(n_mels=128)
        make(model).transcribe(AUDIO, language="ja")
        self.assertEqual(model.encoded[0].shape, (1, 128, 3000))
        self.assertEqual(model.encoded[0].dtype, np.float32)

    def test_auto_detects_language(self) -> None:
        model = FakeWhisper([result(" hi")])
        out = make(model).transcribe(AUDIO)
        self.assertEqual((out.language, out.language_probability), ("ja", 0.6))
        self.assertEqual(model.detect_calls, 1)

    def test_candidate_languages_renormalize(self) -> None:
        model = FakeWhisper([result(" hi")])
        out = make(model, DecodeOptions(candidate_languages=("en", "zh"))).transcribe(
            AUDIO
        )
        self.assertEqual(out.language, "en")
        self.assertAlmostEqual(out.language_probability, 0.75)
        prompts, _ = model.generate_calls[0]
        self.assertEqual(prompts[0][1], T.language_token("en"))

    def test_unsupported_candidate_language_rejected(self) -> None:
        with self.assertRaises(ValueError):
            make(FakeWhisper(), DecodeOptions(candidate_languages=("fr",)))

    def test_prompt_is_prefixed_with_startofprev(self) -> None:
        model = FakeWhisper()
        make(model).transcribe(AUDIO, language="ja", prompt="  前の文  ")
        prompts, _ = model.generate_calls[0]
        self.assertEqual(prompts[0], [T.sot_prev, *T.encode(" 前の文"), *JA_PROMPT])

    def test_blank_prompt_is_ignored(self) -> None:
        model = FakeWhisper()
        make(model).transcribe(AUDIO, language="ja", prompt="   ")
        self.assertEqual(model.generate_calls[0][0], [JA_PROMPT])

    def test_long_prompt_keeps_last_tokens(self) -> None:
        model = FakeWhisper()
        text = "".join(f"{i % 10}" for i in range(1000))
        make(model).transcribe(AUDIO, language="ja", prompt=text)
        prompt = model.generate_calls[0][0][0]
        kept = prompt[1 : -len(JA_PROMPT)]
        self.assertEqual(len(kept), MAX_PROMPT_TOKENS)
        self.assertEqual(kept, T.encode(" " + text)[-MAX_PROMPT_TOKENS:])

    def test_avg_logprob_is_recovered_from_score(self) -> None:
        # 4 個 token、score -0.5、length_penalty 1 → 累積 -2.0 → 平均 -2.0 / 5
        model = FakeWhisper([result("abcd", score=-0.5, no_speech=0.2)])
        out = make(model).transcribe(AUDIO, language="ja")
        self.assertAlmostEqual(out.avg_logprob, -0.4)
        self.assertAlmostEqual(out.no_speech_probability, 0.2)

    def test_generate_arguments(self) -> None:
        model = FakeWhisper()
        make(model, FAST_DECODE).transcribe(AUDIO, language="ja")
        _, kwargs = model.generate_calls[0]
        self.assertEqual(kwargs["beam_size"], 1)
        self.assertEqual(kwargs["max_length"], len(JA_PROMPT) + 128)
        self.assertTrue(kwargs["return_scores"])
        self.assertTrue(kwargs["return_no_speech_prob"])
        self.assertTrue(kwargs["suppress_blank"])
        suppress = kwargs["suppress_tokens"]
        self.assertEqual(suppress[0], -1)
        for token in (
            T.transcribe,
            T.translate,
            T.sot,
            T.sot_prev,
            T.sot_lm,
            T.no_speech,
        ):
            self.assertIn(token, suppress)
        self.assertNotIn(T.no_timestamps, suppress)

    def test_max_new_tokens(self) -> None:
        default = FakeWhisper()
        make(default).transcribe(AUDIO, language="ja")
        self.assertEqual(default.generate_calls[0][1]["max_length"], 448)
        model = FakeWhisper()
        make(model, DecodeOptions(max_new_tokens=50)).transcribe(AUDIO, language="ja")
        self.assertEqual(model.generate_calls[0][1]["max_length"], len(JA_PROMPT) + 50)
        with self.assertRaises(ValueError):
            make(FakeWhisper(), DecodeOptions(max_new_tokens=446)).transcribe(
                AUDIO, language="ja"
            )


class FallbackTest(unittest.TestCase):
    def test_repetitive_output_retries_with_sampling(self) -> None:
        model = FakeWhisper([result(" ha" * 60), result(" fine")])
        out = make(model, FINAL_DECODE).transcribe(AUDIO, language="en")
        self.assertEqual(out.text, "fine")
        self.assertEqual(len(model.generate_calls), 2)
        _, kwargs = model.generate_calls[1]
        self.assertEqual(kwargs["sampling_temperature"], 0.2)
        self.assertEqual(kwargs["beam_size"], 1)
        self.assertEqual(kwargs["num_hypotheses"], 5)
        self.assertEqual(kwargs["sampling_topk"], 0)

    def test_silence_does_not_retry(self) -> None:
        model = FakeWhisper([result(" you", score=-3.0, no_speech=0.9)])
        out = make(model, FINAL_DECODE).transcribe(AUDIO, language="en")
        self.assertEqual(len(model.generate_calls), 1)
        self.assertEqual(out.text, "you")

    def test_all_attempts_fail_picks_best_non_repetitive(self) -> None:
        model = FakeWhisper(
            [
                result(" low", score=-3.0),
                result(" ha" * 60, score=-0.1),
                result(" lower", score=-4.0),
            ]
        )
        out = make(model, FINAL_DECODE).transcribe(AUDIO, language="en")
        self.assertEqual(len(model.generate_calls), 3)
        self.assertEqual(out.text, "low")

    def test_default_options_never_retry(self) -> None:
        model = FakeWhisper([result(" low", score=-3.0)])
        make(model).transcribe(AUDIO, language="en")
        self.assertEqual(len(model.generate_calls), 1)


class LongAudioTest(unittest.TestCase):
    def test_audio_over_30_seconds_is_split(self) -> None:
        model = FakeWhisper(
            [
                result("あいう", score=-0.2, no_speech=0.3),
                result("えお", score=-0.6, no_speech=0.05),
            ]
        )
        out = make(model).transcribe(np.zeros(16000 * 45, dtype=np.int16))
        self.assertEqual(len(model.encoded), 2)
        self.assertEqual(model.detect_calls, 1)
        self.assertEqual(out.text, "あいうえお")  # 日文直接相連
        self.assertAlmostEqual(out.no_speech_probability, 0.05)
        # 依 token 數 + 1 加權：あいう 9 bytes → 9 個 token；えお 6 個 token
        a = -0.2 * 9 / 10
        b = -0.6 * 6 / 7
        self.assertAlmostEqual(out.avg_logprob, (a * 10 + b * 7) / 17)

    def test_space_separated_languages_join_with_space(self) -> None:
        model = FakeWhisper([result(" Hello"), result(" world")])
        out = make(model).transcribe(
            np.zeros(16000 * 45, dtype=np.int16), language="en"
        )
        self.assertEqual(out.text, "Hello world")


class ValidationTest(unittest.TestCase):
    def test_bad_audio(self) -> None:
        transcriber = make(FakeWhisper())
        with self.assertRaises(TypeError):
            transcriber.transcribe(np.zeros(100, dtype=np.float32))
        with self.assertRaises(ValueError):
            transcriber.transcribe(np.zeros(0, dtype=np.int16))

    def test_unsupported_language(self) -> None:
        with self.assertRaises(ValueError):
            make(FakeWhisper()).transcribe(AUDIO, language="fr")
        with self.assertRaises(ValueError):
            make(FakeWhisper()).transcribe(AUDIO, language="zh-TW")

    def test_english_only_model(self) -> None:
        model = FakeWhisper([result(" hi")], multilingual=False)
        transcriber = make(model)
        out = transcriber.transcribe(AUDIO)
        self.assertEqual((out.language, out.language_probability), ("en", 1.0))
        self.assertEqual(model.generate_calls[0][0], [[T.sot, T.no_timestamps]])
        self.assertEqual(model.detect_calls, 0)
        with self.assertRaises(ValueError):
            transcriber.transcribe(AUDIO, language="ja")

    def test_decode_options_validation(self) -> None:
        for kwargs in (
            {"beam_size": 0},
            {"best_of": 0},
            {"temperatures": ()},
            {"temperatures": (-0.1,)},
            {"candidate_languages": ()},
            {"max_new_tokens": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                DecodeOptions(**kwargs)  # type: ignore[arg-type]

    def test_model_errors_propagate(self) -> None:
        model = FakeWhisper()
        model.generate = mock.Mock(side_effect=RuntimeError("CUDA failed"))  # type: ignore[method-assign]
        with self.assertRaisesRegex(RuntimeError, "CUDA failed"):
            make(model).transcribe(AUDIO, language="ja")


class ConcurrencyAndWarmupTest(unittest.TestCase):
    def test_calls_are_serialized(self) -> None:
        model = FakeWhisper()
        transcriber = make(model)
        threads = [
            threading.Thread(target=transcriber.transcribe, args=(AUDIO, "ja"))
            for _ in range(4)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(model.generate_calls), 4)
        self.assertFalse(model.overlap)

    def test_warmup_runs_encoder_and_one_decode_step(self) -> None:
        model = FakeWhisper(n_mels=128)
        make(model).warmup()
        self.assertEqual(model.encoded[0].shape, (1, 128, 3000))
        prompts, kwargs = model.generate_calls[0]
        self.assertEqual(kwargs["max_length"], len(prompts[0]) + 1)


class LoadTranscriberTest(unittest.TestCase):
    def setUp(self) -> None:
        # CUDA 失敗紀錄是行程層級的狀態，每個測試從乾淨狀態開始
        whisper_module._cuda_failure = None
        self.addCleanup(setattr, whisper_module, "_cuda_failure", None)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.model_path = self.root / "local-model"
        self.model_path.mkdir()
        for name in ("model.bin", "config.json"):
            (self.model_path / name).write_bytes(b"x")
        (self.model_path / "tokenizer.json").write_text(
            json.dumps(tokenizer_data()), encoding="utf-8"
        )

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_argument_validation(self) -> None:
        with self.assertRaises(ValueError):
            load_transcriber()
        with self.assertRaises(ValueError):
            load_transcriber("tiny", model_path=self.model_path)
        with self.assertRaises(ValueError):
            load_transcriber(model_path=self.model_path, device="tpu")  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            load_transcriber(model_path=self.model_path, cpu_threads=-1)

    def test_local_path_missing_files(self) -> None:
        (self.model_path / "model.bin").unlink()
        with self.assertRaisesRegex(FileNotFoundError, "model.bin"):
            load_transcriber(model_path=self.model_path, device="cpu")
        with self.assertRaises(FileNotFoundError):
            load_transcriber(model_path=self.root / "nope", device="cpu")

    def test_registry_model_not_downloaded(self) -> None:
        with self.assertRaisesRegex(FileNotFoundError, "not downloaded"):
            load_transcriber("tiny", cache_dir=self.root / "cache", device="cpu")

    def test_registry_model_download_on_demand(self) -> None:
        spec = ModelSpec(
            id="fake",
            repo_id="org/fake",
            revision="0" * 40,
            files=(ModelFile("tokenizer.json", 1, "0" * 64),),
            languages=("multilingual",),
            n_mels=80,
            speed_tier="fast",
            license="MIT",
            approx_cpu_ram_mb=1,
            approx_gpu_vram_mb=1,
            approx_gpu_vram_int8_mb=1,
        )

        def fake_download(spec: ModelSpec, cache_dir: Path, **_: Any) -> Path:
            target = cache_dir / "fake" / ("0" * 12)
            target.mkdir(parents=True)
            (target / "tokenizer.json").write_text(
                json.dumps(tokenizer_data()), encoding="utf-8"
            )
            return target

        with (
            mock.patch(
                "kikitori.asr.whisper.download_model", side_effect=fake_download
            ) as dl,
            mock.patch(
                "kikitori.asr.whisper._create_model", return_value=FakeWhisper()
            ),
        ):
            transcriber = load_transcriber(
                spec, cache_dir=self.root / "cache", device="cpu", download=True
            )
        self.assertEqual(dl.call_count, 1)
        self.assertEqual(transcriber.name, "fake")

    def test_cpu_defaults_to_int8(self) -> None:
        with mock.patch(
            "kikitori.asr.whisper._create_model", return_value=FakeWhisper()
        ) as cm:
            transcriber = load_transcriber(
                model_path=self.model_path, device="cpu", cpu_threads=4
            )
        cm.assert_called_once_with(self.model_path, "cpu", "int8", 4)
        self.assertEqual(
            (transcriber.device, transcriber.compute_type), ("cpu", "int8")
        )

    def test_auto_uses_cuda_when_it_works(self) -> None:
        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=1),
            mock.patch(
                "kikitori.asr.whisper._create_model", return_value=FakeWhisper()
            ) as cm,
        ):
            transcriber = load_transcriber(model_path=self.model_path)
        self.assertEqual(transcriber.device, "cuda")
        self.assertEqual(cm.call_args.args[1:3], ("cuda", "float16"))

    def test_auto_falls_back_to_cpu_when_cuda_fails(self) -> None:
        def create(
            path: Path, device: str, compute_type: str, threads: int
        ) -> FakeWhisper:
            if device == "cuda":
                raise RuntimeError("Library cublas64_12.dll is not found")
            return FakeWhisper()

        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=1),
            mock.patch("kikitori.asr.whisper._create_model", side_effect=create),
            self.assertLogs("kikitori.asr.whisper", level="WARNING") as logs,
        ):
            transcriber = load_transcriber(model_path=self.model_path)
        self.assertEqual(transcriber.device, "cpu")
        self.assertIn("cublas64_12.dll", logs.output[0])

    def test_explicit_cuda_failure_raises(self) -> None:
        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=1),
            mock.patch(
                "kikitori.asr.whisper._create_model",
                side_effect=RuntimeError("no cublas"),
            ),
            self.assertRaisesRegex(RuntimeError, "no cublas"),
        ):
            load_transcriber(model_path=self.model_path, device="cuda")
        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=0),
            self.assertRaisesRegex(RuntimeError, "no CUDA device"),
        ):
            load_transcriber(model_path=self.model_path, device="cuda")

    def test_cuda_is_not_retried_after_failure_in_same_process(self) -> None:
        calls: list[str] = []

        def create(
            path: Path, device: str, compute_type: str, threads: int
        ) -> FakeWhisper:
            calls.append(device)
            if device == "cuda":
                raise RuntimeError("cublas missing")
            return FakeWhisper()

        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=1),
            mock.patch("kikitori.asr.whisper._create_model", side_effect=create),
            self.assertLogs("kikitori.asr.whisper", level="WARNING"),
        ):
            load_transcriber(model_path=self.model_path)
            second = load_transcriber(model_path=self.model_path)
            with self.assertRaisesRegex(RuntimeError, "restart the engine"):
                load_transcriber(model_path=self.model_path, device="cuda")
        self.assertEqual(calls, ["cuda", "cpu", "cpu"])
        self.assertEqual(second.device, "cpu")

    def test_missing_cublas_dir_falls_back_without_poisoning_cuda(self) -> None:
        with (
            mock.patch("ctranslate2.get_cuda_device_count", return_value=1),
            mock.patch(
                "kikitori.asr.whisper._create_model", return_value=FakeWhisper()
            ) as cm,
            mock.patch(
                "kikitori.asr.whisper.preload_cuda_libraries",
                side_effect=FileNotFoundError("cublas64_12.dll not found"),
            ),
            self.assertLogs("kikitori.asr.whisper", level="WARNING"),
        ):
            transcriber = load_transcriber(
                model_path=self.model_path, cuda_library_dirs=[self.root]
            )
        self.assertEqual(transcriber.device, "cpu")
        self.assertEqual(cm.call_args.args[1], "cpu")
        self.assertIsNone(whisper_module._cuda_failure)


class PreloadCudaTest(unittest.TestCase):
    def test_missing_libraries_named_per_platform(self) -> None:
        for platform_name, expected in (
            ("win32", "cublasLt64_12.dll"),
            ("linux", "libcublasLt.so.12"),
        ):
            with (
                self.subTest(platform=platform_name),
                tempfile.TemporaryDirectory() as tmp,
                mock.patch("kikitori.asr.whisper.sys.platform", platform_name),
                self.assertRaisesRegex(FileNotFoundError, expected),
            ):
                preload_cuda_libraries([Path(tmp)])

    def test_macos_is_noop(self) -> None:
        with mock.patch("kikitori.asr.whisper.sys.platform", "darwin"):
            self.assertEqual(preload_cuda_libraries([Path("/nonexistent")]), [])


if __name__ == "__main__":
    unittest.main()
