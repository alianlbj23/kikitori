"""依硬體與語言推薦模型組合，以及驗證使用者手動選擇的組合。

位於 hardware 模組的決策層，兩個函式都是純函式（只讀 `HardwareInfo`，不做 I/O），
方便窮舉測試。ASR 的等級表來自 `docs/specs/asr.md`（本機 RTX 5070／Ryzen 7 5700X
實測），翻譯模型的順序來自 `translate.recommend_translation_models()`；本模組負責把
兩者接起來：ASR 先佔 VRAM，剩下的才分給同一張 GPU 上的本機 LLM（Ollama）。

語言只影響「能不能用」與提示，不改變硬體等級：ASR 模型依硬體挑（只支援單一語言的模型
只在該語言時才會被選）、語言品質低時加警告；翻譯模型在支援這個語言組合的預設組中依
VRAM 順序挑。原因與警告都是結構化訊息（`messages.py`），由 UI 依介面語系組句。
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from kikitori.asr.registry import MODELS, ModelSpec
from kikitori.hardware.messages import Message, message
from kikitori.hardware.types import (
    AsrChoice,
    AsrDeviceChoice,
    CpuInfo,
    GpuInfo,
    HardwareInfo,
    LanguageSelection,
    ModelCombination,
    TranslationStatus,
    ValidationResult,
)
from kikitori.languages import WHISPER_CODES, asr_quality, get_language
from kikitori.translate.presets import (
    PRESETS,
    NoSupportingModelError,
    normalize_model_name,
    preset_for_model,
    recommend_translation_models,
    supports,
)

MIB = 1024 * 1024
GIB = 1024 * MIB

DESKTOP_VRAM_RESERVE_MB = 1024
"""保留給桌面、瀏覽器、影片播放等其他程式的 VRAM。"""
CUDA_CONTEXT_MB = 500
"""registry 的 VRAM 量測各含一份 CUDA context；同一行程的兩個模型共用一份。"""
TIER_8GB_MIN_MB = 7680
"""標稱 8 GB 的卡 nvidia-smi 回報約 8188 MiB；留 0.5 GiB 誤差。"""
TIER_4GB_MIN_MB = 3584
LARGE_V3_MIN_MB = 11264
"""≥ 12 GB（容許誤差）且不跑本機 LLM 時，完整路徑改用 large-v3（no_speech 可用）。"""
MIN_DRIVER_MAJOR = 525
"""CUDA 12.x minor version compatibility 的最低驅動（NVIDIA CUDA Compatibility 文件）。"""
CPU_HIGH_RAM_MIN_BYTES = 15 * GIB
"""標稱 16 GB 的電腦扣掉內顯共用後常回報 15.x GiB。"""
CPU_LOW_MAX_CORES = 4
LOW_RAM_FOR_LLM_BYTES = 8 * GIB
APPLE_GPU_MEMORY_FRACTION = 0.65
"""macOS 預設讓 GPU 使用約 2/3 的統一記憶體（Metal recommendedMaxWorkingSetSize）。"""
APPLE_OS_RESERVE_MB = 4096
MAX_CPU_THREADS_PER_MODEL = 8

CPU_COMPUTE_TYPES = frozenset({"int8", "int8_float32", "int16", "float32"})
CUDA_COMPUTE_TYPES = frozenset(
    {
        "float16",
        "int8_float16",
        "int8",
        "int8_float32",
        "float32",
        "bfloat16",
        "int8_bfloat16",
    }
)
ALL_COMPUTE_TYPES = CPU_COMPUTE_TYPES | CUDA_COMPUTE_TYPES
_FP16_TYPES = frozenset({"float16", "bfloat16", "float32"})
CPU_SLOW_FAST_MODELS = frozenset({"medium", "large-v3-turbo", "large-v3"})
CPU_SLOW_FINAL_MODELS = frozenset({"medium", "large-v3-turbo", "large-v3"})
MAX_TRANSLATION_MODEL_CHARS = 200
LOW_QUALITY_TIER = "C"

ASR_CANDIDATES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "nvidia_8gb": (("small",), ("large-v3-turbo",)),
    "nvidia_8gb_large": (("small",), ("large-v3",)),
    "nvidia_4gb": (("base",), ("large-v3-turbo",)),
    "nvidia_lt4gb": (("base",), ("small",)),
    "cpu": (("base",), ("small",)),
    "cpu_low": (("tiny",), ("base",)),
    "apple_silicon": (("base",), ("small",)),
}
"""每個硬體等級的（即時, 完整）候選模型，越前面越優先。

只支援單一語言的模型（例如之後加入的日語特化模型）放在多語言模型前面：辨識語言全部
在它支援的範圍內才選它，否則選下一個。最後一個一定是多語言模型（測試檢查），所以
「自動偵測」與其他語言永遠有模型可用。`nvidia_8gb_large` 是 `nvidia_8gb` 在 VRAM
寬裕、翻譯不在本機時的變體（回報的 tier 仍是 `nvidia_8gb`）。
"""


# --- 共用的小工具 --------------------------------------------------------------


def _asr_vram_mb(spec: ModelSpec, compute_type: str) -> int:
    # float32 約是 float16 的兩倍；registry 沒量，保守估計
    if compute_type == "float32":
        return spec.approx_gpu_vram_mb * 2
    if compute_type in _FP16_TYPES:
        return spec.approx_gpu_vram_mb
    return spec.approx_gpu_vram_int8_mb


def asr_vram_need_mb(choices: Collection[AsrChoice]) -> int:
    """在 CUDA 上的 ASR 模型合計 VRAM（MiB），扣掉重複計算的 CUDA context。"""
    cuda = [c for c in choices if c.device == "cuda" and c.model in MODELS]
    if not cuda:
        return 0
    total = sum(_asr_vram_mb(MODELS[c.model], c.compute_type) for c in cuda)
    return total - CUDA_CONTEXT_MB * (len(cuda) - 1)


def asr_ram_need_mb(choices: Collection[AsrChoice]) -> int:
    """在 CPU 上的 ASR 模型合計 RAM（MiB）。"""
    return sum(
        MODELS[c.model].approx_cpu_ram_mb
        for c in choices
        if c.device == "cpu" and c.model in MODELS
    )


def primary_gpu(info: HardwareInfo) -> GpuInfo | None:
    """VRAM 最大的 GPU。

    CUDA 預設的裝置順序是「最快的在前」（`CUDA_DEVICE_ORDER=FASTEST_FIRST`），與
    nvidia-smi 的 PCI 順序不同；CTranslate2 用 CUDA 裝置 0，通常就是 VRAM 最大的那張。
    """
    if not info.gpus:
        return None
    return max(info.gpus, key=lambda g: g.memory_total_mb)


def _cuda_problem(info: HardwareInfo, gpu: GpuInfo) -> Message | None:
    """這張 GPU 不能跑 CTranslate2 CUDA 的原因（`cuda_*` 訊息）；可以時回傳 None。"""
    if not info.cublas.supported_platform:
        return message("cuda_platform_unsupported")
    driver = gpu.driver_major
    if driver is not None and driver < MIN_DRIVER_MAJOR:
        return message(
            "cuda_driver_too_old",
            driver_version=gpu.driver_version or "",
            min_driver=MIN_DRIVER_MAJOR,
        )
    cc = gpu.compute_capability_tuple
    if cc is None:
        return message("cuda_compute_capability_unknown")
    if cc < (7, 0) and cc != (6, 1):
        return message(
            "cuda_compute_capability_unsupported",
            gpu_name=gpu.name,
            compute_capability=gpu.compute_capability or "",
        )
    return None


def cpu_threads_per_model(cpu: CpuInfo) -> int:
    """每個 ASR 模型在 CPU 上的執行緒數；核心數未知時 0（CTranslate2 預設值）。

    即時與完整模型會同時跑，兩者合計不超過實體核心數（asr.md），每個最多 8。
    """
    cores = cpu.physical_cores or (cpu.logical_cores // 2 if cpu.logical_cores else 0)
    if cores <= 0:
        return 0
    return max(1, min(MAX_CPU_THREADS_PER_MODEL, cores // 2))


def _gb(mb: float) -> float:
    return round(mb / 1024, 2)


def _pick_model(candidates: tuple[str, ...], whisper_codes: tuple[str, ...]) -> str:
    """第一個支援全部辨識語言的候選；都不支援時用最後一個（多語言模型）。"""
    for model in candidates:
        spec = MODELS.get(model)
        if spec is not None and all(spec.supports(code) for code in whisper_codes):
            return model
    return candidates[-1]


# --- 推薦 ----------------------------------------------------------------------


@dataclass(frozen=True)
class _AsrPlan:
    tier: str
    fast: AsrChoice
    final: AsrChoice
    reasons: tuple[Message, ...]


def _models(slot: str, whisper_codes: tuple[str, ...]) -> tuple[str, str]:
    fast, final = ASR_CANDIDATES[slot]
    return _pick_model(fast, whisper_codes), _pick_model(final, whisper_codes)


def _cpu_plan(
    info: HardwareInfo, whisper_codes: tuple[str, ...], warnings: list[Message]
) -> _AsrPlan:
    threads = cpu_threads_per_model(info.cpu)
    total = info.memory.total_bytes
    cores = info.cpu.physical_cores or (
        info.cpu.logical_cores // 2 if info.cpu.logical_cores else None
    )
    if info.apple_silicon:
        fast, final = _models("apple_silicon", whisper_codes)
        return _AsrPlan(
            "apple_silicon",
            AsrChoice(fast, "cpu", "int8", threads),
            AsrChoice(final, "cpu", "int8", threads),
            (
                message("asr_apple_silicon", fast_model=fast, final_model=final),
                message("asr_apple_silicon_untested"),
            ),
        )
    if total is None:
        warnings.append(message("memory_unknown"))
    high = (
        total is not None
        and total >= CPU_HIGH_RAM_MIN_BYTES
        and (cores is None or cores > CPU_LOW_MAX_CORES)
    )
    if high:
        fast, final = _models("cpu", whisper_codes)
        return _AsrPlan(
            "cpu",
            AsrChoice(fast, "cpu", "int8", threads),
            AsrChoice(final, "cpu", "int8", threads),
            (
                message("asr_cpu", fast_model=fast, final_model=final),
                message("asr_cpu_large_models_slow"),
            ),
        )
    why: list[Message] = []
    if total is not None and total < CPU_HIGH_RAM_MIN_BYTES:
        why.append(message("cpu_low_ram", ram_gb=round(total / GIB, 1)))
    if cores is not None and cores <= CPU_LOW_MAX_CORES:
        why.append(
            message("cpu_few_cores", physical_cores=cores, max_cores=CPU_LOW_MAX_CORES)
        )
    if not why:
        why.append(message("cpu_info_incomplete"))
    fast, final = _models("cpu_low", whisper_codes)
    warnings.append(message("asr_low_quality_model", model=fast))
    return _AsrPlan(
        "cpu_low",
        AsrChoice(fast, "cpu", "int8", threads),
        AsrChoice(final, "cpu", "int8", threads),
        (*why, message("asr_cpu_low", fast_model=fast, final_model=final)),
    )


def _gpu_plan(
    gpu: GpuInfo, translation_local: bool, whisper_codes: tuple[str, ...]
) -> _AsrPlan:
    total = gpu.memory_total_mb
    cc = gpu.compute_capability_tuple
    pascal = cc == (6, 1)  # 只支援 int8（CTranslate2 文件：float16 需要 7.0 以上）

    def plan(tier: str, slot: str, ctype: str, *extra: Message) -> _AsrPlan:
        fast, final = _models(slot, whisper_codes)
        reasons = [
            message(
                "asr_gpu",
                gpu_name=gpu.name,
                vram_mb=total,
                fast_model=fast,
                final_model=final,
                compute_type=ctype,
            ),
            *extra,
        ]
        fast_spec, final_spec = MODELS.get(fast), MODELS.get(final)
        if (
            final_spec is not None
            and not final_spec.no_speech_reliable
            and fast_spec is not None
            and fast_spec.no_speech_reliable
        ):
            reasons.append(
                message(
                    "asr_final_no_speech_unreliable",
                    final_model=final,
                    fast_model=fast,
                )
            )
        return _AsrPlan(
            tier,
            AsrChoice(fast, "cuda", ctype),
            AsrChoice(final, "cuda", ctype),
            tuple(reasons),
        )

    if total >= TIER_8GB_MIN_MB:
        ctype = "int8" if pascal else "float16"
        if total >= LARGE_V3_MIN_MB and not translation_local:
            final = _models("nvidia_8gb_large", whisper_codes)[1]
            return plan(
                "nvidia_8gb",
                "nvidia_8gb_large",
                ctype,
                message("asr_large_v3_remote_translation", final_model=final),
            )
        return plan("nvidia_8gb", "nvidia_8gb", ctype)
    ctype = "int8" if pascal else "int8_float16"
    if total >= TIER_4GB_MIN_MB:
        return plan(
            "nvidia_4gb", "nvidia_4gb", ctype, message("asr_gpu_int8_saves_vram")
        )
    return plan("nvidia_lt4gb", "nvidia_lt4gb", ctype)


def _translation_budget_mb(
    info: HardwareInfo, gpu: GpuInfo | None, plan: _AsrPlan
) -> float | None:
    choices = (plan.fast, plan.final)
    if gpu is not None:
        # Ollama 自帶 CUDA runtime，就算 ASR 不能用 GPU，翻譯仍可用這張卡
        return max(
            0.0,
            gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB - asr_vram_need_mb(choices),
        )
    if info.apple_silicon and info.memory.total_bytes:
        total_mb = info.memory.total_bytes / MIB
        return max(
            0.0,
            min(
                total_mb * APPLE_GPU_MEMORY_FRACTION,
                total_mb - asr_ram_need_mb(choices) - APPLE_OS_RESERVE_MB,
            ),
        )
    return None


def _unsupported_pair(
    pairs: tuple[tuple[str, str], ...], allow_restricted: bool
) -> tuple[str, str]:
    """要回報的組合：第一個「沒有任何可推薦模型支援」的方向；都個別支援時用第一個。"""
    usable = [
        p
        for p in PRESETS.values()
        if p.commercial_use and (allow_restricted or not p.excluded_regions)
    ]
    for source, target in pairs:
        if not any(supports(p, source, target) for p in usable):
            return source, target
    return pairs[0]


@dataclass(frozen=True)
class _TranslationPlan:
    fast: str | None
    final: str | None
    budget_gb: float | None
    status: TranslationStatus


def _recommend_translation(
    info: HardwareInfo,
    gpu: GpuInfo | None,
    plan: _AsrPlan,
    allow_restricted: bool,
    languages: LanguageSelection | None,
    reasons: list[Message],
    warnings: list[Message],
) -> _TranslationPlan:
    server = info.translation_server
    if not server.is_local:
        reasons.append(message("translation_remote", base_url=server.base_url))
        return _TranslationPlan(None, None, None, "remote")

    budget_mb = _translation_budget_mb(info, gpu, plan)
    budget_gb = None if budget_mb is None else _gb(budget_mb)
    pairs: tuple[tuple[str, str], ...] | None = None
    if languages is not None:
        pairs = languages.translation_pairs
        if not pairs:
            reasons.append(
                message(
                    "translation_not_needed",
                    source_language=languages.source_language,
                    target_language=languages.target_language,
                )
            )
            return _TranslationPlan(None, None, budget_gb, "not_needed")
        if languages.mode == "media" and languages.source_language == "auto":
            reasons.append(
                message(
                    "source_language_auto", target_language=languages.target_language
                )
            )

    installed: tuple[str, ...] | None = server.models
    if not server.reachable:
        warnings.append(
            message("translation_server_unreachable", base_url=server.base_url)
        )
    elif installed is None:
        warnings.append(
            message("translation_model_list_unavailable", error=server.error or "")
        )

    try:
        choice = recommend_translation_models(
            budget_gb, installed, allow_restricted, pairs
        )
        ideal = recommend_translation_models(budget_gb, None, allow_restricted, pairs)
    except NoSupportingModelError:
        # 不看語言（pairs 為 None）時每個等級都有可推薦的模型，只有語言篩選會走到這裡
        assert pairs is not None and languages is not None
        source, target = _unsupported_pair(pairs, allow_restricted)
        warnings.append(
            message(
                "translation_unsupported",
                source_language=_shown_source(source, languages),
                target_language=target,
            )
        )
        return _TranslationPlan(None, None, budget_gb, "unsupported")

    if budget_gb is None:
        reasons.append(message("translation_cpu"))
        total = info.memory.total_bytes
        if total is not None and total < LOW_RAM_FOR_LLM_BYTES:
            warnings.append(message("low_ram_for_translation"))
    elif gpu is None:
        reasons.append(message("translation_budget_unified", budget_gb=budget_gb))
    else:
        reasons.append(message("translation_budget_vram", budget_gb=budget_gb))
    reasons.append(
        message("translation_same_model")
        if choice.fast == choice.final
        else message("translation_different_models")
    )

    available = (
        None if installed is None else {normalize_model_name(m) for m in installed}
    )
    for model, best in dict.fromkeys(
        ((choice.fast, ideal.fast), (choice.final, ideal.final))
    ):
        if available is not None and model not in available:
            warnings.append(message("translation_model_not_installed", model=model))
        elif model != best:
            reasons.append(
                message(
                    "translation_better_model_available", model=model, better_model=best
                )
            )
        preset = PRESETS.get(model)
        if (
            budget_gb is not None
            and preset is not None
            and preset.approx_vram_gb > budget_gb
        ):
            warnings.append(
                message(
                    "translation_model_exceeds_budget",
                    model=model,
                    model_gb=preset.approx_vram_gb,
                    budget_gb=budget_gb,
                )
            )
    return _TranslationPlan(choice.fast, choice.final, budget_gb, "recommended")


def _shown_source(source: str, languages: LanguageSelection) -> str:
    """訊息裡的原文語言：自動偵測時是 "auto"（方向裡的是 Whisper 語言碼，例如 "zh"，
    不分繁簡，UI 無法顯示成清單語言），其他情況本來就是清單語言碼。"""
    if languages.mode == "media" and languages.source_language == "auto":
        return "auto"
    return source


def _language_quality_warnings(
    languages: LanguageSelection, model: str
) -> list[Message]:
    """辨識語言用這個模型品質為 C 級時的警告（每個語言一則）。"""
    return [
        message("asr_language_quality_low", model=model, language=code)
        for code in languages.spoken_languages
        if asr_quality(code, model) == LOW_QUALITY_TIER
    ]


def recommend(
    info: HardwareInfo,
    *,
    allow_restricted: bool = True,
    languages: LanguageSelection | None = None,
) -> ModelCombination:
    """依偵測結果與語言選擇推薦 ASR 與翻譯模型組合（純函式）。

    Args:
        info: `detect()` 的結果。
        allow_restricted: False 時不推薦有地區限制授權的翻譯模型（例如使用者在
            歐盟、英國、南韓時排除 HY-MT）。非商用授權的模型一律不推薦。
        languages: 收聽／字幕語言（影片翻譯）或兩個方向（口譯）。None 時不看語言
            （ASR 只用多語言模型、翻譯模型不檢查語言組合）。

    Returns:
        `ModelCombination`；`reasons`／`warnings` 為結構化訊息（`messages.py`）。
    """
    reasons: list[Message] = []
    warnings: list[Message] = []
    gpu = primary_gpu(info)
    gpu_runtime_missing = False
    plan: _AsrPlan | None = None
    # 不看語言時要求支援全部語言，等於只選多語言模型（與加入語言前的行為相同）
    whisper_codes = (
        languages.asr_whisper_codes if languages is not None else WHISPER_CODES
    )

    if gpu is not None:
        if len(info.gpus) > 1:
            warnings.append(
                message("multiple_gpus", gpu_count=len(info.gpus), gpu_name=gpu.name)
            )
        problem = _cuda_problem(info, gpu)
        if problem is not None:
            warnings.append(problem)
        elif not info.cublas.available:
            gpu_runtime_missing = True
            warnings.append(message("gpu_runtime_missing"))
        else:
            plan = _gpu_plan(gpu, info.translation_server.is_local, whisper_codes)
            need = asr_vram_need_mb((plan.fast, plan.final))
            if need > gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB:
                warnings.append(message("asr_vram_insufficient", need_mb=need))
                plan = None
    if plan is None:
        plan = _cpu_plan(info, whisper_codes, warnings)
    reasons.extend(plan.reasons)
    if languages is not None:
        warnings.extend(_language_quality_warnings(languages, plan.final.model))

    translation = _recommend_translation(
        info, gpu, plan, allow_restricted, languages, reasons, warnings
    )
    return ModelCombination(
        tier=plan.tier,
        fast_asr=plan.fast,
        final_asr=plan.final,
        translation_fast=translation.fast,
        translation_final=translation.final,
        translation_budget_gb=translation.budget_gb,
        gpu_runtime_missing=gpu_runtime_missing,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
        translation_status=translation.status,
        languages=languages,
    )


# --- 手動覆寫的驗證 --------------------------------------------------------------


def _parse_asr(
    data: object, path: str, errors: list[Message]
) -> tuple[str, str, str | None, int] | None:
    if not isinstance(data, Mapping):
        errors.append(message("asr_path_not_object", path=path))
        return None
    model = data.get("model")
    device = data.get("device", "auto")
    compute_type = data.get("compute_type")
    threads = data.get("cpu_threads", 0)
    ok = True
    if not isinstance(model, str) or model not in MODELS:
        errors.append(
            message(
                "asr_model_unknown",
                path=path,
                model=str(model)[:50],
                available=", ".join(MODELS),
            )
        )
        ok = False
    if device not in ("auto", "cpu", "cuda"):
        errors.append(message("asr_device_invalid", path=path))
        ok = False
    if compute_type is not None and not isinstance(compute_type, str):
        errors.append(message("asr_compute_type_not_string", path=path))
        ok = False
    if (
        isinstance(threads, bool)
        or not isinstance(threads, int)
        or not 0 <= threads <= 256
    ):
        errors.append(message("asr_cpu_threads_invalid", path=path))
        ok = False
    if not ok:
        return None
    assert isinstance(model, str) and isinstance(device, str)
    return model, device, compute_type, threads


def _check_asr_device(
    info: HardwareInfo,
    path: str,
    model: str,
    device: str,
    compute_type: str | None,
    errors: list[Message],
    warnings: list[Message],
) -> AsrDeviceChoice:
    """檢查裝置與精度；回傳實際會用的裝置（auto 依硬體判斷）。"""
    gpu = primary_gpu(info)
    cuda_ok = (
        gpu is not None and _cuda_problem(info, gpu) is None and info.cublas.available
    )
    if device == "cuda":
        if gpu is None:
            errors.append(message("asr_cuda_no_gpu", path=path))
        elif (problem := _cuda_problem(info, gpu)) is not None:
            # 與推薦的 `cuda_*` 訊息同一組參數，另加 path（code 加上 `asr_` 前綴）
            errors.append(message(f"asr_{problem.code}", path=path, **problem.params))
        elif not info.cublas.available:
            errors.append(message("asr_cuda_cublas_missing", path=path))
    elif device == "auto" and gpu is not None and not cuda_ok:
        warnings.append(message("asr_auto_uses_cpu", path=path))
    actual: AsrDeviceChoice = (
        "cuda" if device == "cuda" or (device == "auto" and cuda_ok) else "cpu"
    )

    if compute_type is not None:
        if compute_type not in ALL_COMPUTE_TYPES:
            errors.append(
                message(
                    "asr_compute_type_unknown",
                    path=path,
                    compute_type=compute_type[:30],
                    available=", ".join(sorted(ALL_COMPUTE_TYPES)),
                )
            )
        elif actual == "cpu" and compute_type not in CPU_COMPUTE_TYPES:
            # CTranslate2 不會報錯，而是自動轉成 CPU 支援的型別（官方 quantization 文件）
            warnings.append(
                message(
                    "asr_compute_type_cpu_fallback",
                    path=path,
                    compute_type=compute_type,
                )
            )
        elif actual == "cuda" and gpu is not None:
            cc = gpu.compute_capability_tuple
            if (
                cc is not None
                and cc < (7, 0)
                and compute_type in ("float16", "int8_float16")
            ):
                warnings.append(
                    message(
                        "asr_compute_type_gpu_fallback",
                        path=path,
                        compute_type=compute_type,
                        compute_capability=gpu.compute_capability or "",
                    )
                )
    if actual == "cpu":
        slow = CPU_SLOW_FAST_MODELS if path == "fast_asr" else CPU_SLOW_FINAL_MODELS
        if model in slow:
            warnings.append(message("asr_model_slow_on_cpu", path=path, model=model))
    return actual


def _translation_name(
    data: Mapping[str, Any], key: str, errors: list[Message]
) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > MAX_TRANSLATION_MODEL_CHARS
        or not value.isprintable()
    ):
        errors.append(
            message(
                "translation_model_name_invalid",
                field=key,
                max_chars=MAX_TRANSLATION_MODEL_CHARS,
            )
        )
        return None
    return value.strip()


def _language_checks(
    languages: LanguageSelection,
    chosen: list[tuple[str, AsrChoice]],
    translation_models: list[str],
    warnings: list[Message],
) -> None:
    """語言相關的警告：ASR 不支援辨識語言、品質 C 級、翻譯模型不支援語言組合。"""
    for path, choice in chosen:
        spec = MODELS[choice.model]
        for code in languages.spoken_languages:
            if not spec.supports(get_language(code).whisper_code):
                warnings.append(
                    message(
                        "asr_model_language_unsupported",
                        path=path,
                        model=choice.model,
                        language=code,
                    )
                )
    finals = [c for path, c in chosen if path == "final_asr"]
    if finals:
        warnings.extend(_language_quality_warnings(languages, finals[0].model))
    for name in translation_models:
        preset = preset_for_model(name)
        if preset.license == "unknown":
            continue  # 不認得的模型不知道支援哪些語言，不猜
        for source, target in languages.translation_pairs:
            if not supports(preset, source, target):
                warnings.append(
                    message(
                        "translation_model_language_unsupported",
                        model=name,
                        source_language=_shown_source(source, languages),
                        target_language=target,
                    )
                )
                break


def validate_combination(
    data: Mapping[str, Any],
    info: HardwareInfo,
    *,
    downloaded_asr: Collection[str] | None = None,
    allow_restricted: bool = True,
    languages: LanguageSelection | None = None,
) -> ValidationResult:
    """驗證使用者手動選的組合（純函式）；格式錯誤也以 `errors` 回報，不丟例外。

    Args:
        data: 與 `ModelCombination.to_dict()` 相同的形狀，至少要有 `fast_asr`、
            `final_asr`（各含 `model`，選填 `device`＝auto／cpu／cuda、`compute_type`、
            `cpu_threads`），選填 `translation_fast`、`translation_final`。
        info: `detect()` 的結果。
        downloaded_asr: 已下載的 ASR 模型 id；None 表示不檢查。
        allow_restricted: False 時，選了有地區限制授權的翻譯模型會產生警告。
        languages: 目前的語言選擇；None 表示不做語言相關的檢查。

    Returns:
        `errors`（無法使用）與 `warnings`（可用但要注意），皆為結構化訊息。
    """
    errors: list[Message] = []
    warnings: list[Message] = []
    if not isinstance(data, Mapping):
        return ValidationResult(errors=(message("combination_not_object"),))

    chosen: list[tuple[str, AsrChoice]] = []
    for key in ("fast_asr", "final_asr"):
        parsed = _parse_asr(data.get(key), key, errors)
        if parsed is None:
            continue
        model, device, compute_type, threads = parsed
        actual = _check_asr_device(
            info, key, model, device, compute_type, errors, warnings
        )
        if downloaded_asr is not None and model not in downloaded_asr:
            warnings.append(message("asr_model_not_downloaded", path=key, model=model))
        default_type = "float16" if actual == "cuda" else "int8"
        chosen.append(
            (key, AsrChoice(model, actual, compute_type or default_type, threads))
        )

    if len(chosen) == 2:
        fast, final = chosen[0][1], chosen[1][1]
        if MODELS[fast.model].approx_cpu_ram_mb > MODELS[final.model].approx_cpu_ram_mb:
            warnings.append(
                message(
                    "asr_fast_larger_than_final",
                    fast_model=fast.model,
                    final_model=final.model,
                )
            )
        if (
            not MODELS[final.model].no_speech_reliable
            and not MODELS[fast.model].no_speech_reliable
        ):
            warnings.append(
                message(
                    "asr_no_speech_unavailable",
                    fast_model=fast.model,
                    final_model=final.model,
                )
            )

    fast_t = _translation_name(data, "translation_fast", errors)
    final_t = _translation_name(data, "translation_final", errors)
    server = info.translation_server
    installed = (
        None
        if server.models is None
        else {normalize_model_name(m) for m in server.models}
    )
    translation_models = list(
        dict.fromkeys(m for m in (fast_t, final_t) if m is not None)
    )
    for name in translation_models:
        if installed is not None and normalize_model_name(name) not in installed:
            warnings.append(message("translation_model_not_on_server", model=name))
        preset = preset_for_model(name)
        if preset.license == "unknown":
            continue
        if not preset.commercial_use:
            warnings.append(
                message(
                    "translation_license_non_commercial",
                    model=name,
                    license=preset.license,
                )
            )
        if preset.excluded_regions and not allow_restricted:
            warnings.append(
                message(
                    "translation_license_region_restricted",
                    model=name,
                    license=preset.license,
                    regions=",".join(preset.excluded_regions),
                )
            )
    if (
        fast_t
        and final_t
        and normalize_model_name(fast_t) != normalize_model_name(final_t)
        and server.is_local
    ):
        warnings.append(message("translation_models_differ"))
    if languages is not None:
        _language_checks(languages, chosen, translation_models, warnings)

    choices = [c for _, c in chosen]
    gpu = primary_gpu(info)
    if gpu is not None and choices:
        need = asr_vram_need_mb(choices)
        if server.is_local:
            llm = max(
                (
                    PRESETS[normalize_model_name(m)].approx_vram_gb
                    for m in (fast_t, final_t)
                    if m is not None and normalize_model_name(m) in PRESETS
                ),
                default=0.0,
            )
            need += int(llm * 1024)
        available_mb = gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB
        if need > available_mb:
            warnings.append(
                message(
                    "vram_overflow",
                    need_mb=need,
                    available_mb=available_mb,
                    gpu_name=gpu.name,
                )
            )
    total_ram = info.memory.total_bytes
    if total_ram is not None and asr_ram_need_mb(choices) * MIB > total_ram * 0.5:
        warnings.append(message("asr_ram_over_half"))
    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))
