"""`recommend()` 與 `validate_combination()` 的測試：各硬體等級、Ollama 狀態與手動覆寫。"""

import json
import unittest
from dataclasses import replace

from kikitori.hardware.recommend import (
    CUDA_CONTEXT_MB,
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
    MemoryInfo,
    TranslationServerInfo,
)

GIB = 1024**3
ALL_MODELS = (
    "qwen3.5:9b",
    "qwen3.5:4b",
    "hy-mt1.5-7b:latest",
    "hy-mt1.5-1.8b:latest",
    "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
)
LOCAL = "http://localhost:11434/v1"


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
    def test_nvidia_12gb_uses_small_and_turbo_fp16_and_9b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),)))
        self.assertEqual(rec.tier, "nvidia_8gb")
        self.assertEqual(rec.fast_asr, AsrChoice("small", "cuda", "float16"))
        self.assertEqual(rec.final_asr, AsrChoice("large-v3-turbo", "cuda", "float16"))
        self.assertEqual(rec.translation_fast, "qwen3.5:9b")
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        # 12227 − 1024 保留 − (1000 + 2300 − 500) ASR
        self.assertAlmostEqual(
            rec.translation_budget_gb or 0, (12227 - 1024 - 2800) / 1024, places=1
        )
        self.assertFalse(rec.gpu_runtime_missing)
        self.assertEqual(rec.warnings, ())

    def test_nvidia_8gb_card_gets_4b(self) -> None:
        rec = recommend(make_info(gpus=(gpu(8188, "8.9", name="RTX 4060"),)))
        self.assertEqual(rec.tier, "nvidia_8gb")
        self.assertEqual(rec.final_asr.compute_type, "float16")
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

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
        self.assertTrue(any("部分運算放到 CPU" in w for w in rec.warnings))

    def test_nvidia_under_4gb_uses_base_and_small(self) -> None:
        rec = recommend(make_info(gpus=(gpu(2048, "7.5", name="MX450"),)))
        self.assertEqual(rec.tier, "nvidia_lt4gb")
        self.assertEqual(rec.fast_asr, AsrChoice("base", "cuda", "int8_float16"))
        self.assertEqual(rec.final_asr, AsrChoice("small", "cuda", "int8_float16"))

    def test_tiny_vram_falls_back_to_cpu(self) -> None:
        rec = recommend(make_info(gpus=(gpu(1024, "7.5", name="Tiny"),)))
        self.assertEqual(rec.tier, "cpu")
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertTrue(any("VRAM 不足" in w for w in rec.warnings))

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
        self.assertTrue(any("RAM 8.0 GB" in r for r in rec.reasons))

    def test_cpu_four_cores(self) -> None:
        rec = recommend(make_info(ram_gb=32, physical=4, logical=8))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 2)

    def test_cpu_very_low_ram_warns_about_llm(self) -> None:
        rec = recommend(make_info(ram_gb=4, physical=2, logical=4))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 1)
        self.assertTrue(any("8 GB" in w for w in rec.warnings))

    def test_unknown_ram_is_conservative(self) -> None:
        rec = recommend(make_info(ram_gb=None, physical=None, logical=None))
        self.assertEqual(rec.tier, "cpu_low")
        self.assertEqual(rec.fast_asr.cpu_threads, 0)
        self.assertTrue(any("記憶體" in w for w in rec.warnings))

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
        self.assertTrue(any("未實測" in r for r in rec.reasons))

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
        self.assertTrue(any("cuBLAS 未安裝" in w for w in rec.warnings))
        # Ollama 仍可用整張卡
        self.assertAlmostEqual(
            rec.translation_budget_gb or 0, (12227 - 1024) / 1024, places=1
        )

    def test_old_driver(self) -> None:
        rec = recommend(make_info(gpus=(gpu(driver="472.12"),)))
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertFalse(rec.gpu_runtime_missing)
        self.assertTrue(any("驅動" in w for w in rec.warnings))

    def test_unknown_compute_capability(self) -> None:
        rec = recommend(make_info(gpus=(gpu(cc=None),)))
        self.assertEqual(rec.fast_asr.device, "cpu")

    def test_maxwell_not_supported(self) -> None:
        rec = recommend(make_info(gpus=(gpu(4096, "5.2", name="GTX 970"),)))
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertTrue(any("5.2" in w for w in rec.warnings))

    def test_pascal_6_1_uses_int8(self) -> None:
        rec = recommend(make_info(gpus=(gpu(8192, "6.1", name="GTX 1080"),)))
        self.assertEqual(rec.fast_asr, AsrChoice("small", "cuda", "int8"))
        self.assertEqual(rec.final_asr.compute_type, "int8")

    def test_linux_aarch64_gpu_not_used_for_asr(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),), os="linux", machine="aarch64", supported=False)
        )
        self.assertEqual(rec.fast_asr.device, "cpu")
        self.assertTrue(any("x86-64" in w for w in rec.warnings))

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
        self.assertTrue(any("2 張" in w and "Big" in w for w in rec.warnings))

    def test_remote_translation_uses_large_v3_and_no_translation_model(self) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(),),
                base_url="https://api.example.com/v1",
                local=False,
                models=("gpt-x",),
            )
        )
        self.assertEqual(rec.final_asr.model, "large-v3")
        self.assertIsNone(rec.translation_fast)
        self.assertIsNone(rec.translation_final)
        self.assertIsNone(rec.translation_budget_gb)

    def test_remote_translation_on_8gb_keeps_turbo(self) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(8188, "8.9"),), local=False, base_url="https://x.example/v1"
            )
        )
        self.assertEqual(rec.final_asr.model, "large-v3-turbo")


class RecommendTranslationTest(unittest.TestCase):
    def test_ollama_unreachable_recommends_top_choice_with_warning(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),), reachable=False))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertTrue(any("Ollama 是否已啟動" in w for w in rec.warnings))
        self.assertFalse(any("尚未安裝" in w for w in rec.warnings))

    def test_best_not_installed_picks_best_installed(self) -> None:
        rec = recommend(
            make_info(gpus=(gpu(),), models=("hy-mt1.5-7b:latest", "qwen3.5:4b"))
        )
        self.assertEqual(rec.translation_final, "qwen3.5:4b")
        self.assertTrue(any("qwen3.5:9b" in r and "品質較好" in r for r in rec.reasons))
        self.assertFalse(any("尚未安裝" in w for w in rec.warnings))

    def test_nothing_installed_warns(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),), models=()))
        self.assertEqual(rec.translation_final, "qwen3.5:9b")
        self.assertEqual(sum("尚未安裝" in w for w in rec.warnings), 1)

    def test_only_sakura_installed_never_recommended(self) -> None:
        rec = recommend(
            make_info(
                gpus=(gpu(),), models=("hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",)
            )
        )
        self.assertNotIn("Sakura", rec.translation_final or "")
        self.assertTrue(any("尚未安裝" in w for w in rec.warnings))

    def test_restricted_region_excludes_hy_mt(self) -> None:
        rec = recommend(make_info(ram_gb=32), allow_restricted=False)
        self.assertEqual(rec.translation_final, "qwen3.5:4b")

    def test_tag_less_installed_name_matches(self) -> None:
        rec = recommend(make_info(ram_gb=32, models=("hy-mt1.5-1.8b",)))
        self.assertEqual(rec.translation_final, "hy-mt1.5-1.8b:latest")
        self.assertFalse(any("尚未安裝" in w for w in rec.warnings))

    def test_server_error_without_model_list(self) -> None:
        info = make_info(gpus=(gpu(),))
        info = replace(
            info,
            translation_server=TranslationServerInfo(
                LOCAL, True, True, None, "HTTP 500"
            ),
        )
        rec = recommend(info)
        self.assertTrue(any("HTTP 500" in w for w in rec.warnings))

    def test_to_dict_is_json(self) -> None:
        rec = recommend(make_info(gpus=(gpu(),)))
        data = json.loads(json.dumps(rec.to_dict()))
        self.assertEqual(data["fast_asr"]["model"], "small")
        self.assertEqual(data["tier"], "nvidia_8gb")
        self.assertIsInstance(data["reasons"], list)


class VramNeedTest(unittest.TestCase):
    def test_context_counted_once(self) -> None:
        need = asr_vram_need_mb(
            [
                AsrChoice("small", "cuda", "float16"),
                AsrChoice("large-v3-turbo", "cuda", "float16"),
            ]
        )
        self.assertEqual(need, 1000 + 2300 - CUDA_CONTEXT_MB)

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
        self.assertIn("fast_asr.model", result.errors[0])

    def test_cuda_without_gpu(self) -> None:
        result = validate_combination(combo(), make_info())
        self.assertFalse(result.ok)
        self.assertTrue(any("沒有偵測到 NVIDIA GPU" in e for e in result.errors))

    def test_cuda_without_cublas(self) -> None:
        result = validate_combination(combo(), make_info(gpus=(gpu(),), cublas=False))
        self.assertTrue(any("cuBLAS" in e for e in result.errors))

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
        self.assertTrue(any("auto 會使用 CPU" in w for w in result.warnings))

    def test_bad_device_and_types(self) -> None:
        data = {
            "fast_asr": {"model": "base", "device": "tpu"},
            "final_asr": {"model": 3, "cpu_threads": -1},
            "translation_fast": "",
            "translation_final": 5,
        }
        result = validate_combination(data, make_info())
        self.assertEqual(len(result.errors), 5)

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
        self.assertTrue(any("int4" in e for e in result.errors))

    def test_float16_on_cpu_is_warning(self) -> None:
        result = validate_combination(
            combo(fast="base", final="small", device="cpu", compute_type="float16"),
            make_info(),
        )
        self.assertTrue(result.ok)
        self.assertTrue(any("CPU 不支援 float16" in w for w in result.warnings))

    def test_float16_on_pascal_is_warning(self) -> None:
        result = validate_combination(
            combo(compute_type="float16"), make_info(gpus=(gpu(8192, "6.1"),))
        )
        self.assertTrue(result.ok)
        self.assertTrue(any("6.1" in w for w in result.warnings))

    def test_large_model_on_cpu_warns(self) -> None:
        result = validate_combination(
            combo(fast="large-v3", final="large-v3", device="cpu"), make_info()
        )
        self.assertTrue(result.ok)
        self.assertGreaterEqual(sum("3.6 s" in w for w in result.warnings), 2)

    def test_fast_larger_than_final_and_both_unreliable(self) -> None:
        result = validate_combination(
            combo(fast="large-v3-turbo", final="large-v3-turbo"),
            make_info(gpus=(gpu(),)),
        )
        self.assertTrue(any("no_speech" in w for w in result.warnings))
        result = validate_combination(
            combo(fast="medium", final="small"), make_info(gpus=(gpu(),))
        )
        self.assertTrue(any("即時模型比完整模型大" in w for w in result.warnings))

    def test_not_downloaded_warning(self) -> None:
        result = validate_combination(
            combo(), make_info(gpus=(gpu(),)), downloaded_asr={"small"}
        )
        self.assertTrue(any("large-v3-turbo 尚未下載" in w for w in result.warnings))

    def test_vram_overflow_warning(self) -> None:
        result = validate_combination(
            combo(fast="large-v3", final="large-v3", compute_type="float16"),
            make_info(gpus=(gpu(8188, "8.9"),)),
        )
        self.assertTrue(any("VRAM" in w for w in result.warnings))

    def test_translation_checks(self) -> None:
        info = make_info(gpus=(gpu(),), models=("qwen3.5:9b",))
        data = combo(translation=None)
        data["translation_fast"] = "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
        data["translation_final"] = "hy-mt1.5-7b"
        result = validate_combination(data, info, allow_restricted=False)
        self.assertTrue(result.ok)
        joined = "\n".join(result.warnings)
        self.assertIn("禁止商業用途", joined)
        self.assertIn("不適用於 EU、UK、KR", joined)
        self.assertIn("翻譯伺服器上沒有", joined)
        self.assertIn("1.5–18 秒", joined)

    def test_unknown_translation_model_has_no_license_warning(self) -> None:
        info = make_info(gpus=(gpu(),), models=("my-model",))
        data = combo(translation="my-model")
        result = validate_combination(data, info)
        self.assertTrue(result.ok)
        self.assertFalse(any("授權" in w for w in result.warnings))

    def test_result_to_dict(self) -> None:
        result = validate_combination(combo(fast="x"), make_info())
        data = result.to_dict()
        self.assertFalse(data["ok"])
        self.assertIsInstance(data["errors"], list)


if __name__ == "__main__":
    unittest.main()
