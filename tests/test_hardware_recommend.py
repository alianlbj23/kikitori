"""`recommend()` 與 `validate_combination()` 的測試：各硬體等級、Ollama 狀態與手動覆寫。"""

import json
import unittest
from collections.abc import Sequence
from dataclasses import replace
from unittest import mock

from kikitori.asr.registry import MODELS, ModelSpec
from kikitori.hardware.messages import Message
from kikitori.hardware.recommend import (
    ASR_CANDIDATES,
    CUDA_CONTEXT_MB,
    DESKTOP_VRAM_RESERVE_MB,
    MIN_TRANSLATION_BUDGET_GB,
    SHARED_WORKER_EXTRA_MB,
    asr_vram_need_mb,
    recommend,
    validate_combination,
)
from kikitori.hardware.types import (
    AsrChoice,
    CpuInfo,
    CublasInfo,
    GpuInfo,
    HardwareInfo,
    LanguageSelection,
    MemoryInfo,
    TranslationServerInfo,
)
from kikitori.languages import ASR_QUALITY_MODELS
from kikitori.translate.presets import PRESETS

GIB = 1024**3
ALL_MODELS = (
    "qwen3.5:9b",
    "qwen3.5:4b",
    "hy-mt1.5-7b:latest",
    "hy-mt1.5-1.8b:latest",
    "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
)
LOCAL = "http://localhost:11434/v1"


def codes(messages: Sequence[Message]) -> list[str]:
    return [m.code for m in messages]


def pairs(messages: Sequence[Message]) -> list[tuple[str, dict[str, object]]]:
    return [(m.code, m.params) for m in messages]


def gpu(
    vram_mb: int = 12227,
    cc: str | None = "12.0",
    driver: str | None = "621.01",
    name: str = "NVIDIA GeForce RTX 5070",
    index: int = 0,
) -> GpuInfo:
    return GpuInfo(index, name, vram_mb, 1000, driver, cc)


def make_info(
    *,
    gpus: tuple[GpuInfo, ...] = (),
    cublas: bool = True,
    supported: bool = True,
    ram_gb: float | None = 64,
    physical: int | None = 8,
    logical: int | None = 16,
    os: str = "windows",
    machine: str = "AMD64",
    apple: bool = False,
    reachable: bool = True,
    models: tuple[str, ...] | None = ALL_MODELS,
    base_url: str = LOCAL,
    local: bool = True,
) -> HardwareInfo:
    return HardwareInfo(
        os=os,  # type: ignore[arg-type]
        os_version="test",
        machine=machine,
        cpu=CpuInfo("Test CPU", physical, logical, machine),
        memory=MemoryInfo(None if ram_gb is None else int(ram_gb * GIB), None),
        gpus=gpus,
        apple_silicon=apple,
        cublas=CublasInfo(
            supported_platform=supported,
            available=cublas and supported,
            library_dirs=("C:/cublas",) if cublas and supported else (),
            source="runtime" if cublas and supported else None,
        ),
        translation_server=TranslationServerInfo(
            base_url=base_url,
            is_local=local,
            reachable=reachable,
            models=models if reachable else None,
            error=None if reachable else "cannot connect",
        ),
    )


class RecommendTierTest(unittest.TestCase):
    # 8 GB 以上：即時 large-v3-turbo、完整 large-v3（experiments/vtuber_eval）。與本機翻譯
    # LLM 共用時，在「只差精度」（int8_float16 品質相同）的組合中挑讓翻譯模型等級最高、
    # 精度又最高的那個。翻譯預算 = VRAM − 1024 − ASR（registry 數值，context 只算一次）。

    def test_nvidia_12gb_uses_turbo_and_large_v3_int8_with_9b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),)))
        self.assertEqual(rec.tier, "nvidia_8gb")
        self.assertEqual(
            rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "int8_float16")
        )
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "int8_float16"))
        self.assertEqual(rec.translation_fast, "qwen3.5:9b")
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        # 12227 − 1024 − (1240 + 2140 − 500) = 8323 MiB ≈ 8.13 GB ≥ 7.5（9B 的門檻）；
        # turbo 用 float16 時只剩 7263 MiB ≈ 7.09 GB，只能放 4B。
        self.assertAlmostEqual(
            rec.translation_budget_gb or 0, (12227 - 1024 - 2880) / 1024, places=1
        )
        self.assertIn("asr_gpu_int8_saves_vram", codes(rec.reasons))
        self.assertIn(
            ("asr_bgm_robust_final", {"final_model": "large-v3"}), pairs(rec.reasons)
        )
        self.assertIn(
            (
                "asr_gpu",
                {
                    "gpu_name": "NVIDIA GeForce RTX 5070",
                    "vram_mb": 12227,
                    "fast_model": "large-v3-turbo",
                    "final_model": "large-v3",
                    "compute_type": "int8_float16",
                },
            ),
            pairs(rec.reasons),
        )
        self.assertFalse(rec.gpu_runtime_missing)
        self.assertEqual(rec.warnings, ())

    def test_nvidia_16gb_keeps_float16_and_9b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(16303, "8.9", name="RTX 4080"),)))
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertNotIn("asr_gpu_int8_saves_vram", codes(rec.reasons))

    def test_nvidia_10gb_uses_int8_final_and_4b(self) -> None:
        # 10240 − 1024：兩個 float16 → 3246 MiB（< 4 GB）；final int8 → 5276（4B）；
        # 兩個都 int8 → 6336，仍是 4B，所以只把 final 改 int8。
        rec = recommend(make_info(gpus=(gpu(10240, "8.6", name="RTX 3080"),)))
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "int8_float16"))
        self.assertEqual(rec.translation_final, "qwen3.5:4b")
        reason = next(m for m in rec.reasons if m.code == "asr_gpu")
        self.assertEqual(reason.params["compute_type"], "float16 / int8_float16")

    def test_nvidia_11gb_float16_already_fits_4b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(11264, "7.5", name="RTX 2080 Ti"),)))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))
        self.assertEqual(rec.fast_asr.compute_type, "float16")
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_nvidia_8gb_card_uses_int8_and_4b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(8188, "8.9", name="RTX 4060"),)))
        self.assertEqual(rec.tier, "nvidia_8gb")
        self.assertEqual(
            rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "int8_float16")
        )
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "int8_float16"))
        # 8188 − 1024 − 2880 = 4284 MiB ≈ 4.18 GB
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_smallest_8gb_tier_card_falls_back_to_small_live_model(self) -> None:
        # 7680 − 1024 − 2880 = 3776 MiB < 4 GB：翻譯只剩 HY-MT 1.8B（translate_eval：每個
        # 資料集都顯著較差且會幻覺），所以改用 small 即時（2290 MiB）讓 4B 放得下。
        rec = recommend(make_info(gpus=(gpu(7680, "8.6", name="Edge 8GB"),)))
        self.assertEqual(rec.fast_asr, AsrChoice("small", "cuda", "int8_float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "int8_float16"))
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_local_combination_always_fits_the_card(self) -> None:
        # 8–24 GB 的每個等級：ASR + 推薦翻譯模型的權重 + 桌面保留 ≤ VRAM，且翻譯預算
        # 至少到 4B 的門檻。
        for vram in range(7680, 24577, 256):
            with self.subTest(vram=vram):
                info = make_info(gpus=(gpu(vram, "8.9"),))
                rec = recommend(info)
                self.assertEqual(rec.final_asr.model, "large-v3")
                need = asr_vram_need_mb((rec.fast_asr, rec.final_asr))
                budget = vram - DESKTOP_VRAM_RESERVE_MB - need
                self.assertGreaterEqual(budget / 1024, MIN_TRANSLATION_BUDGET_GB)
                llm = PRESETS[rec.translation_final or ""].approx_vram_gb
                self.assertLessEqual(need + llm * 1024 + DESKTOP_VRAM_RESERVE_MB, vram)
                result = validate_combination(rec.to_dict(), info)
                self.assertNotIn("vram_overflow", codes(result.warnings))

    def test_translation_not_needed_uses_float16(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),)), languages=langs(source="zh-CN", target="zh-TW")
        )
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))

    def test_nvidia_6gb_uses_int8_float16(self) -> None:
        rec = recommend(make_info(gpus=(gpu(6144, "7.5", name="GTX 1660"),)))
        self.assertEqual(rec.tier, "nvidia_4gb")
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cuda", "int8_float16"))
        self.assertEqual(
            rec.final_asr, AsrChoice("large-v3-turbo", "cuda", "int8_float16")
        )
        # 6144 − 1024 − (450 + 1240 − 500) = 3930 MiB ≈ 3.84 GB < 4 GB 門檻
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")

    def test_nvidia_4gb_gets_small_translation_with_offload_warning(self) -> None:
        rec = recommend(make_info(gpus=(gpu(4096, "8.6", name="RTX 3050"),)))
        self.assertEqual(rec.tier, "nvidia_4gb")
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")
        self.assertIn("translation_model_exceeds_budget", codes(rec.warnings))

    def test_nvidia_under_4gb_uses_base_and_small(self) -> None:
        rec = recommend(make_info(gpus=(gpu(2048, "7.5", name="MX450"),)))
        self.assertEqual(rec.tier, "nvidia_lt4gb")
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cuda", "int8_float16"))
        self.assertEqual(rec.final_asr, AsrChoice("small", "cuda", "int8_float16"))

    def test_tiny_vram_falls_back_to_cpu(self) -> None:
        rec = recommend(make_info(gpus=(gpu(1024, "7.5", name="Tiny"),)))
        self.assertEqual(rec.tier, "cpu")
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertIn("asr_vram_insufficient", codes(rec.warnings))

    def test_cpu_high_ram(self) -> None:
        rec = recommend(make_info(ram_gb=32, physical=8))
        self.assertEqual(rec.tier, "cpu")
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cpu", "int8", 4))
        self.assertEqual(rec.final_asr, AsrChoice("small", "cpu", "int8", 4))
        self.assertIsNone(rec.translation_budget_gb)
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")

    def test_16gb_reported_as_15_6_is_still_high(self) -> None:
        self.assertEqual(recommend(make_info(ram_gb=15.6, physical=6)).tier, "cpu")

    def test_cpu_low_ram(self) -> None:
        rec = recommend(make_info(ram_gb=8, physical=8))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.model, "tiny")
        self.assertEqual(rec.final_asr.model, "base")
        self.assertIn(("cpu_low_ram", {"ram_gb": 8.0}), pairs(rec.reasons))

    def test_cpu_four_cores(self) -> None:
        rec = recommend(make_info(ram_gb=32, physical=4, logical=8))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 2)

    def test_cpu_very_low_ram_warns_about_llm(self) -> None:
        rec = recommend(make_info(ram_gb=4, physical=2, logical=4))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 1)
        self.assertIn("low_ram_for_translation", codes(rec.warnings))

    def test_unknown_ram_is_conservative(self) -> None:
        rec = recommend(make_info(ram_gb=None, physical=None, logical=None))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 0)
        self.assertIn("memory_unknown", codes(rec.warnings))

    def test_apple_silicon(self) -> None:
        rec = recommend(
            make_info(
                os="macos", machine="arm64", apple=True, ram_gb=16, supported=False
            )
        )
        self.assertEqual(rec.tier, "apple_silicon")
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cpu", "int8", 4))
        self.assertEqual(rec.final_asr.model, "small")
        self.assertIsNotNone(rec.translation_budget_gb)
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertIn("asr_apple_silicon_untested", codes(rec.reasons))

    def test_apple_silicon_8gb_gets_smallest_translation(self) -> None:
        rec = recommend(
            make_info(
                os="macos", machine="arm64", apple=True, ram_gb=8, supported=False
            )
        )
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")

    def test_gpu_without_cublas_uses_cpu_and_flags_runtime(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),), cublas=False))
        self.assertTrue(rec.gpu_runtime_missing)
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertIn("gpu_runtime_missing", codes(rec.warnings))
        # Ollama 仍可用整張卡
        self.assertAlmostEqual(
            rec.translation_budget_gb or 0, (12227 - 1024) / 1024, places=1
        )

    def test_old_driver(self) -> None:
        rec = recommend(make_info(gpus=(gpu(driver="472.12"),)))
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertFalse(rec.gpu_runtime_missing)
        self.assertIn(
            ("cuda_driver_too_old", {"driver_version": "472.12", "min_driver": 525}),
            pairs(rec.warnings),
        )

    def test_unknown_compute_capability(self) -> None:
        rec = recommend(make_info(gpus=(gpu(cc=None),)))
        self.assertEqual(rec.fast_asr.device, "cpu")

    def test_maxwell_not_supported(self) -> None:
        rec = recommend(make_info(gpus=(gpu(4096, "5.2", name="GTX 970"),)))
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertIn(
            (
                "cuda_compute_capability_unsupported",
                {"gpu_name": "GTX 970", "compute_capability": "5.2"},
            ),
            pairs(rec.warnings),
        )

    def test_pascal_6_1_uses_int8(self) -> None:
        rec = recommend(make_info(gpus=(gpu(8192, "6.1", name="GTX 1080"),)))
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "int8"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "int8"))
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_linux_aarch64_gpu_not_used_for_asr(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),), os="linux", machine="aarch64", supported=False)
        )
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertIn("cuda_platform_unsupported", codes(rec.warnings))

    def test_multiple_gpus_use_largest(self) -> None:
        rec = recommend(
            make_info(
                gpus=(
                    gpu(4096, "7.5", "550.1", "Small", 0),
                    gpu(24576, "8.9", "550.1", "Big", 1),
                )
            )
        )
        self.assertEqual(rec.tier, "nvidia_8gb")
        self.assertIn(
            ("multiple_gpus", {"gpu_count": 2, "gpu_name": "Big"}), pairs(rec.warnings)
        )

    def test_remote_translation_uses_large_v3_and_no_translation_model(self) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(),),
                base_url="https://api.example.com/v1",
                local=False,
                models=("gpt-x",),
            )
        )
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertIsNone(rec.translation_fast)
        self.assertIsNone(rec.translation_final)
        self.assertIsNone(rec.translation_budget_gb)
        self.assertIn(
            ("asr_large_v3_remote_translation", {"final_model": "large-v3"}),
            pairs(rec.reasons),
        )

    def test_remote_translation_on_8gb_uses_float16_large_models(self) -> None:
        # 不和 LLM 共用：8188 − 1024 ≥ 2300 + 4170 − 500，兩個都 float16（延遲最低）。
        rec = recommend(
            make_info(
                gpus=(gpu(8188, "8.9"),), local=False, base_url="https://x.example/v1"
            )
        )
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))

    def test_lower_tiers_keep_previous_combinations_with_remote_translation(
        self,
    ) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(6144, "7.5"),), local=False, base_url="https://x.example/v1"
            )
        )
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cuda", "int8_float16"))
        self.assertEqual(
            rec.final_asr, AsrChoice("large-v3-turbo", "cuda", "int8_float16")
        )


class RecommendTranslationTest(unittest.TestCase):
    def test_ollama_unreachable_recommends_top_choice_with_warning(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),), reachable=False))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertIn("translation_server_unreachable", codes(rec.warnings))
        self.assertNotIn("translation_model_not_installed", codes(rec.warnings))

    def test_best_not_installed_picks_best_installed(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),), models=("hy-mt1.5-7b:latest", "qwen3.5:4b"))
        )
        self.assertEqual(rec.translation_final, "qwen3.5:4b")
        self.assertIn(
            (
                "translation_better_model_available",
                {"model": "qwen3.5:4b", "better_model": "qwen3.5:9b"},
            ),
            pairs(rec.reasons),
        )
        self.assertNotIn("translation_model_not_installed", codes(rec.warnings))

    def test_nothing_installed_warns(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),), models=()))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertEqual(
            [
                m.params
                for m in rec.warnings
                if m.code == "translation_model_not_installed"
            ],
            [{"model": "qwen3.5:9b"}],
        )

    def test_only_sakura_installed_never_recommended(self) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(),), models=("hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",)
            )
        )
        self.assertNotIn("Sakura", rec.translation_final or "")
        self.assertIn("translation_model_not_installed", codes(rec.warnings))

    def test_restricted_region_excludes_hy_mt(self) -> None:
        rec = recommend(make_info(ram_gb=32), allow_restricted=False)
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_tag_less_installed_name_matches(self) -> None:
        rec = recommend(make_info(ram_gb=32, models=("hy-mt1.5-1.8b",)))
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")
        self.assertNotIn("translation_model_not_installed", codes(rec.warnings))

    def test_server_error_without_model_list(self) -> None:
        info = make_info(gpus=(gpu(),))
        info = replace(
            info,
            translation_server=TranslationServerInfo(
                LOCAL, True, True, None, "HTTP 500"
            ),
        )
        rec = recommend(info)
        self.assertIn(
            ("translation_model_list_unavailable", {"error": "HTTP 500"}),
            pairs(rec.warnings),
        )

    def test_to_dict_is_json(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),)))
        data = json.loads(json.dumps(rec.to_dict()))
        self.assertEqual(data["fast_asr"]["model"], "large-v3-turbo")
        self.assertEqual(data["tier"], "nvidia_8gb")
        self.assertIsInstance(data["reasons"], list)
        self.assertEqual(set(data["reasons"][0]), {"code", "params", "message"})
        self.assertEqual(data["translation_status"], "recommended")
        self.assertIsNone(data["languages"])


def langs(
    mode: str = "media",
    source: str = "ja",
    target: str = "zh-TW",
    partner: str = "ja",
) -> LanguageSelection:
    return LanguageSelection(mode, source, target, partner)  # type: ignore[arg-type]


JA_ONLY = ModelSpec(
    id="fake-ja-only",
    repo_id="example/fake-ja-only",
    revision="0" * 40,
    files=(),
    languages=("ja",),
    n_mels=128,
    speed_tier="slow",
    license="MIT",
    approx_cpu_ram_mb=2000,
    approx_gpu_vram_mb=2300,
    approx_gpu_vram_int8_mb=1240,
)


class LanguageSelectionTest(unittest.TestCase):
    def test_pairs_per_mode(self) -> None:
        self.assertEqual(langs().translation_pairs, (("ja", "zh-TW"),))
        self.assertEqual(
            langs("interpret", target="zh-TW", partner="en").translation_pairs,
            (("en", "zh-TW"), ("zh-TW", "en")),
        )
        self.assertEqual(langs(source="zh-CN").translation_pairs, ())
        auto = langs(source="auto", target="es").translation_pairs
        self.assertEqual(len(auto), 18)
        self.assertNotIn(("es", "es"), auto)

    def test_spoken_languages_and_whisper_codes(self) -> None:
        self.assertEqual(langs(source="ko").spoken_languages, ("ko",))
        self.assertEqual(
            langs("interpret", target="zh-TW", partner="ja").asr_whisper_codes,
            ("zh", "ja"),
        )
        self.assertEqual(langs(source="auto").spoken_languages, ())
        self.assertEqual(len(langs(source="auto").asr_whisper_codes), 19)

    def test_rejects_unnormalized_or_unknown_codes(self) -> None:
        for bad in (
            {"mode": "karaoke"},
            {"target_language": "zh"},
            {"source_language": "xx"},
            {"target_language": "zh-hant"},
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                LanguageSelection(**{"mode": "media", **bad})  # type: ignore[arg-type]


class RecommendLanguageTest(unittest.TestCase):
    def test_selection_is_echoed(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),)), languages=langs(source="ko"))
        self.assertEqual(rec.languages, langs(source="ko"))
        self.assertEqual(rec.to_dict()["languages"]["source_language"], "ko")
        self.assertEqual(rec.translation_status, "recommended")

    def test_c_tier_listening_language_warns(self) -> None:
        # 越南語在 large-v3 也是 C（Common Voice 15 為 39.8%）：換不到更好的模型，只提示
        rec = recommend(
            make_info(gpus=(gpu(),)), languages=langs(source="vi", target="en")
        )
        self.assertEqual(rec.final_asr.model, "large-v3")
        self.assertEqual(rec.fast_asr.model, "large-v3-turbo")
        self.assertIn(
            ("asr_language_quality_low", {"model": "large-v3", "language": "vi"}),
            pairs(rec.warnings),
        )

    def test_languages_where_only_turbo_is_c_use_large_v3_live_too(self) -> None:
        # th／ar／hi：turbo 是 C、large-v3 較好（turbo 官方公告承認泰語退化）→ 即時也用
        # large-v3，兩條路徑同精度、共用一份權重。
        for language in ("th", "ar", "hi"):
            with self.subTest(language=language):
                rec = recommend(
                    make_info(gpus=(gpu(),)),
                    languages=langs(source=language, target="en"),
                )
                self.assertEqual(rec.fast_asr, rec.final_asr)
                self.assertEqual(
                    rec.final_asr, AsrChoice("large-v3", "cuda", "int8_float16")
                )
                # 12227 − 1024 − (2140 + 512) = 8551 MiB → 9B
                self.assertEqual(rec.translation_final, "qwen3.5:9b")
                self.assertIn(
                    (
                        "asr_live_model_for_language",
                        {
                            "language": language,
                            "fast_model": "large-v3-turbo",
                            "model": "large-v3",
                        },
                    ),
                    pairs(rec.reasons),
                )
                self.assertNotIn("asr_language_quality_low", codes(rec.warnings))
        rec = recommend(
            make_info(gpus=(gpu(16303, "8.9"),)),
            languages=langs(source="th", target="en"),
        )
        self.assertEqual(rec.fast_asr, AsrChoice("large-v3", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3", "cuda", "float16"))

    def test_a_or_b_tier_does_not_warn(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),)), languages=langs(source="ko", target="es")
        )
        self.assertNotIn("asr_language_quality_low", codes(rec.warnings))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")

    def test_interpret_checks_both_languages(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),)),
            languages=langs("interpret", target="zh-TW", partner="vi"),
        )
        low = [m.params for m in rec.warnings if m.code == "asr_language_quality_low"]
        self.assertEqual(low, [{"model": "large-v3", "language": "vi"}])

    def test_auto_source_recommends_for_the_target_only(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),)), languages=langs(source="auto", target="zh-TW")
        )
        self.assertIn(
            ("source_language_auto", {"target_language": "zh-TW"}), pairs(rec.reasons)
        )
        self.assertNotIn("asr_language_quality_low", codes(rec.warnings))
        self.assertEqual(rec.translation_status, "recommended")
        self.assertEqual(rec.translation_final, "qwen3.5:9b")

    def test_same_whisper_language_needs_no_translation_model(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),)), languages=langs(source="zh-CN", target="zh-TW")
        )
        self.assertEqual(rec.translation_status, "not_needed")
        self.assertIsNone(rec.translation_fast)
        self.assertIsNone(rec.translation_final)
        self.assertIn(
            (
                "translation_not_needed",
                {"source_language": "zh-CN", "target_language": "zh-TW"},
            ),
            pairs(rec.reasons),
        )
        self.assertNotIn("translation_model_not_installed", codes(rec.warnings))

    def test_unsupported_pair_is_signalled(self) -> None:
        narrow = {
            name: replace(p, languages=frozenset({"ja", "zh-TW", "en"}))
            for name, p in PRESETS.items()
        }
        with mock.patch.dict(PRESETS, narrow):
            rec = recommend(
                make_info(gpus=(gpu(),)), languages=langs(source="ja", target="th")
            )
        self.assertEqual(rec.translation_status, "unsupported")
        self.assertIsNone(rec.translation_final)
        self.assertIn(
            (
                "translation_unsupported",
                {"source_language": "ja", "target_language": "th"},
            ),
            pairs(rec.warnings),
        )
        # 推薦了「沒有模型」：不再提示「建議的模型尚未安裝」
        self.assertNotIn("translation_model_not_installed", codes(rec.warnings))

    def test_remote_server_is_not_checked_for_languages(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),), local=False, base_url="https://x.example/v1"),
            languages=langs(source="ko", target="es"),
        )
        self.assertEqual(rec.translation_status, "remote")
        self.assertIn("translation_remote", codes(rec.reasons))

    def test_single_language_asr_model_only_for_its_language(self) -> None:
        candidates = dict(ASR_CANDIDATES)
        candidates["nvidia_8gb"] = (("large-v3-turbo",), ("fake-ja-only", "large-v3"))
        info = make_info(gpus=(gpu(),))
        with (
            mock.patch.dict(MODELS, {"fake-ja-only": JA_ONLY}),
            mock.patch.dict(ASR_CANDIDATES, candidates),
        ):
            ja = recommend(info, languages=langs(source="ja"))
            en = recommend(info, languages=langs(source="en", target="ja"))
            auto = recommend(info, languages=langs(source="auto"))
            both = recommend(
                info, languages=langs("interpret", target="zh-TW", partner="ja")
            )
            legacy = recommend(info)
        self.assertEqual(ja.final_asr.model, "fake-ja-only")
        for rec in (en, auto, both, legacy):
            self.assertEqual(rec.final_asr.model, "large-v3")

    def test_every_registry_model_has_quality_data_and_tiers_end_multilingual(
        self,
    ) -> None:
        self.assertLessEqual(set(MODELS), set(ASR_QUALITY_MODELS))
        for tier, slots in ASR_CANDIDATES.items():
            for candidates in slots:
                with self.subTest(tier=tier, candidates=candidates):
                    self.assertEqual(
                        MODELS[candidates[-1]].languages, ("multilingual",)
                    )


class ValidateLanguageTest(unittest.TestCase):
    def test_translation_model_must_support_the_pair(self) -> None:
        sakura = "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
        info = make_info(gpus=(gpu(),))
        data = combo(translation=sakura)
        result = validate_combination(data, info, languages=langs(source="ko"))
        self.assertIn(
            (
                "translation_model_language_unsupported",
                {"model": sakura, "source_language": "ko", "target_language": "zh-TW"},
            ),
            pairs(result.warnings),
        )
        ok = validate_combination(data, info, languages=langs(source="ja"))
        self.assertNotIn("translation_model_language_unsupported", codes(ok.warnings))

    def test_low_quality_language_and_single_language_asr(self) -> None:
        info = make_info()
        data = combo(fast="base", final="small", device="cpu", translation=None)
        result = validate_combination(data, info, languages=langs(source="th"))
        self.assertIn(
            ("asr_language_quality_low", {"model": "small", "language": "th"}),
            pairs(result.warnings),
        )
        with mock.patch.dict(MODELS, {"fake-ja-only": JA_ONLY}):
            data = combo(fast="base", final="fake-ja-only", device="cpu")
            result = validate_combination(data, info, languages=langs(source="ko"))
        self.assertIn(
            (
                "asr_model_language_unsupported",
                {"path": "final_asr", "model": "fake-ja-only", "language": "ko"},
            ),
            pairs(result.warnings),
        )


class VramNeedTest(unittest.TestCase):
    def test_context_counted_once(self) -> None:
        need = asr_vram_need_mb(
            [
                AsrChoice("small", "cuda", "float16"),
                AsrChoice("large-v3-turbo", "cuda", "float16"),
            ]
        )
        self.assertEqual(need, 1000 + 2300 - CUDA_CONTEXT_MB)

    def test_same_model_and_precision_is_loaded_once(self) -> None:
        # factory 讓兩條路徑共用一份權重（CTranslate2 兩個 worker）；第二個 worker 的
        # 工作記憶體另計 SHARED_WORKER_EXTRA_MB。
        shared = AsrChoice("large-v3", "cuda", "int8_float16")
        self.assertEqual(
            asr_vram_need_mb([shared, shared]), 2140 + SHARED_WORKER_EXTRA_MB
        )
        mixed = AsrChoice("large-v3", "cuda", "float16")
        self.assertEqual(
            asr_vram_need_mb([mixed, shared]), 4170 + 2140 - CUDA_CONTEXT_MB
        )

    def test_cpu_models_ignored(self) -> None:
        self.assertEqual(asr_vram_need_mb([AsrChoice("small", "cpu", "int8")]), 0)


def combo(
    fast: str = "small",
    final: str = "large-v3-turbo",
    device: str = "cuda",
    compute_type: str | None = None,
    translation: str | None = "qwen3.5:9b",
) -> dict[str, object]:
    return {
        "fast_asr": {"model": fast, "device": device, "compute_type": compute_type},
        "final_asr": {"model": final, "device": device, "compute_type": compute_type},
        "translation_fast": translation,
        "translation_final": translation,
    }


class ValidateCombinationTest(unittest.TestCase):
    def test_recommended_combination_is_valid(self) -> None:
        info = make_info(gpus=(gpu(),))
        result = validate_combination(recommend(info).to_dict(), info)
        self.assertTrue(result.ok, result.errors)
        self.assertEqual(result.warnings, ())

    def test_unknown_asr_model(self) -> None:
        result = validate_combination(combo(fast="huge"), make_info(gpus=(gpu(),)))
        self.assertFalse(result.ok)
        self.assertEqual(result.errors[0].code, "asr_model_unknown")
        self.assertEqual(result.errors[0].params["path"], "fast_asr")

    def test_cuda_without_gpu(self) -> None:
        result = validate_combination(combo(), make_info())
        self.assertFalse(result.ok)
        self.assertIn(("asr_cuda_no_gpu", {"path": "fast_asr"}), pairs(result.errors))

    def test_cuda_without_cublas(self) -> None:
        result = validate_combination(combo(), make_info(gpus=(gpu(),), cublas=False))
        self.assertIn("asr_cuda_cublas_missing", codes(result.errors))

    def test_cuda_on_macos(self) -> None:
        info = make_info(os="macos", machine="arm64", apple=True, supported=False)
        result = validate_combination(combo(), info)
        self.assertFalse(result.ok)

    def test_auto_without_cublas_is_warning(self) -> None:
        result = validate_combination(
            combo(fast="base", final="small", device="auto"),
            make_info(gpus=(gpu(),), cublas=False),
        )
        self.assertTrue(result.ok)
        self.assertIn("asr_auto_uses_cpu", codes(result.warnings))

    def test_bad_device_and_types(self) -> None:
        data = {
            "fast_asr": {"model": "base", "device": "tpu"},
            "final_asr": {"model": 3, "cpu_threads": -1},
            "translation_fast": "",
            "translation_final": 5,
        }
        result = validate_combination(data, make_info())
        self.assertEqual(
            codes(result.errors),
            [
                "asr_device_invalid",
                "asr_model_unknown",
                "asr_cpu_threads_invalid",
                "translation_model_name_invalid",
                "translation_model_name_invalid",
            ],
        )

    def test_not_a_mapping(self) -> None:
        self.assertFalse(validate_combination([], make_info()).ok)  # type: ignore[arg-type]
        self.assertFalse(
            validate_combination(
                {"fast_asr": "base", "final_asr": None}, make_info()
            ).ok
        )

    def test_unknown_compute_type(self) -> None:
        result = validate_combination(
            combo(compute_type="int4"), make_info(gpus=(gpu(),))
        )
        self.assertIn("int4", [e.params.get("compute_type") for e in result.errors])

    def test_float16_on_cpu_is_warning(self) -> None:
        result = validate_combination(
            combo(fast="base", final="small", device="cpu", compute_type="float16"),
            make_info(),
        )
        self.assertTrue(result.ok)
        self.assertIn(
            (
                "asr_compute_type_cpu_fallback",
                {"path": "fast_asr", "compute_type": "float16"},
            ),
            pairs(result.warnings),
        )

    def test_float16_on_pascal_is_warning(self) -> None:
        result = validate_combination(
            combo(compute_type="float16"), make_info(gpus=(gpu(8192, "6.1"),))
        )
        self.assertTrue(result.ok)
        self.assertIn("asr_compute_type_gpu_fallback", codes(result.warnings))

    def test_large_model_on_cpu_warns(self) -> None:
        result = validate_combination(
            combo(fast="large-v3", final="large-v3", device="cpu"), make_info()
        )
        self.assertTrue(result.ok)
        self.assertEqual(codes(result.warnings).count("asr_model_slow_on_cpu"), 2)

    def test_fast_larger_than_final_and_both_unreliable(self) -> None:
        result = validate_combination(
            combo(fast="large-v3-turbo", final="large-v3-turbo"),
            make_info(gpus=(gpu(),)),
        )
        self.assertIn("asr_no_speech_unavailable", codes(result.warnings))
        result = validate_combination(
            combo(fast="medium", final="small"), make_info(gpus=(gpu(),))
        )
        self.assertIn("asr_fast_larger_than_final", codes(result.warnings))

    def test_not_downloaded_warning(self) -> None:
        result = validate_combination(
            combo(), make_info(gpus=(gpu(),)), downloaded_asr={"small"}
        )
        self.assertIn(
            (
                "asr_model_not_downloaded",
                {"path": "final_asr", "model": "large-v3-turbo"},
            ),
            pairs(result.warnings),
        )

    def test_vram_overflow_warning(self) -> None:
        result = validate_combination(
            combo(fast="large-v3", final="large-v3", compute_type="float16"),
            make_info(gpus=(gpu(8188, "8.9"),)),
        )
        self.assertIn("vram_overflow", codes(result.warnings))

    def test_translation_checks(self) -> None:
        info = make_info(gpus=(gpu(),), models=("qwen3.5:9b",))
        data = combo(translation=None)
        data["translation_fast"] = "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
        data["translation_final"] = "hy-mt1.5-7b"
        result = validate_combination(data, info, allow_restricted=False)
        self.assertTrue(result.ok)
        found = pairs(result.warnings)
        self.assertIn(
            (
                "translation_license_non_commercial",
                {
                    "model": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
                    "license": "CC BY-NC-SA 4.0",
                },
            ),
            found,
        )
        self.assertIn(
            (
                "translation_license_region_restricted",
                {
                    "model": "hy-mt1.5-7b",
                    "license": "Tencent HY Community License",
                    "regions": "EU,UK,KR",
                },
            ),
            found,
        )
        self.assertIn("translation_model_not_on_server", codes(result.warnings))
        self.assertIn("translation_models_differ", codes(result.warnings))

    def test_unknown_translation_model_has_no_license_warning(self) -> None:
        info = make_info(gpus=(gpu(),), models=("my-model",))
        data = combo(translation="my-model")
        result = validate_combination(data, info)
        self.assertTrue(result.ok)
        self.assertFalse(
            any(c.startswith("translation_license") for c in codes(result.warnings))
        )

    def test_result_to_dict(self) -> None:
        result = validate_combination(combo(fast="x"), make_info())
        data = result.to_dict()
        self.assertFalse(data["ok"])
        self.assertIsInstance(data["errors"], list)
        self.assertEqual(data["errors"][0]["code"], "asr_model_unknown")
        self.assertIsInstance(data["errors"][0]["message"], str)


if __name__ == "__main__":
    unittest.main()
