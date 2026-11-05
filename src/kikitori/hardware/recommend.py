"""依硬體推薦模型組合，以及驗證使用者手動選擇的組合。

位於 hardware 模組的決策層，兩個函式都是純函式（只讀 `HardwareInfo`，不做 I/O），
方便窮舉測試。ASR 的等級表來自 `docs/specs/asr.md`（本機 RTX 5070／Ryzen 7 5700X
實測），翻譯模型的順序來自 `translate.recommend_translation_models()`；本模組負責把
兩者接起來：ASR 先佔 VRAM，剩下的才分給同一張 GPU 上的本機 LLM（Ollama）。
"""

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from typing import Any

from kikitori.asr.registry import MODELS, ModelSpec
from kikitori.hardware.types import (
    AsrChoice,
    AsrDeviceChoice,
    GpuInfo,
    HardwareInfo,
    ModelCombination,
    ValidationResult,
)
from kikitori.translate.presets import (
    PRESETS,
    normalize_model_name,
    preset_for_model,
    recommend_translation_models,
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


def _cuda_problem(info: HardwareInfo, gpu: GpuInfo) -> str | None:
    """這張 GPU 不能跑 CTranslate2 CUDA 的原因；可以時回傳 None。"""
    if not info.cublas.supported_platform:
        return "此平台的 CTranslate2 沒有 CUDA 版本（只支援 Windows／Linux x86-64），ASR 改用 CPU"
    driver = gpu.driver_major
    if driver is not None and driver < MIN_DRIVER_MAJOR:
        return f"NVIDIA 驅動 {gpu.driver_version} 太舊（CUDA 12 需要 {MIN_DRIVER_MAJOR} 以上），ASR 改用 CPU；更新驅動後可用 GPU"
    cc = gpu.compute_capability_tuple
    if cc is None:
        return "無法取得 GPU 的 compute capability（驅動可能太舊），ASR 改用 CPU"
    if cc < (7, 0) and cc != (6, 1):
        return f"{gpu.name}（compute capability {gpu.compute_capability}）不支援 CTranslate2 的 float16／int8 加速，ASR 改用 CPU"
    return None


def _cpu_threads(info: HardwareInfo) -> int:
    # 即時與完整模型會同時跑，兩者合計不超過實體核心數（asr.md）
    cores = info.cpu.physical_cores or (
        info.cpu.logical_cores // 2 if info.cpu.logical_cores else 0
    )
    if cores <= 0:
        return 0
    return max(1, min(MAX_CPU_THREADS_PER_MODEL, cores // 2))


def _gb(mb: float) -> float:
    return round(mb / 1024, 2)


# --- 推薦 ----------------------------------------------------------------------


@dataclass(frozen=True)
class _AsrPlan:
    tier: str
    fast: AsrChoice
    final: AsrChoice
    reasons: tuple[str, ...]


def _cpu_plan(info: HardwareInfo, warnings: list[str]) -> _AsrPlan:
    threads = _cpu_threads(info)
    total = info.memory.total_bytes
    cores = info.cpu.physical_cores or (
        info.cpu.logical_cores // 2 if info.cpu.logical_cores else None
    )
    if info.apple_silicon:
        return _AsrPlan(
            "apple_silicon",
            AsrChoice("base", "cpu", "int8", threads),
            AsrChoice("small", "cpu", "int8", threads),
            (
                "Apple Silicon：CTranslate2 在 macOS 只有 CPU（Accelerate），即時用 base、完整用 small",
                "Apple Silicon 的延遲尚未實測，暫比照「CPU、RAM ≥ 16 GB」",
            ),
        )
    if total is None:
        warnings.append("無法取得記憶體大小，先用最輕量的 ASR 組合")
    high = (
        total is not None
        and total >= CPU_HIGH_RAM_MIN_BYTES
        and (cores is None or cores > CPU_LOW_MAX_CORES)
    )
    if high:
        return _AsrPlan(
            "cpu",
            AsrChoice("base", "cpu", "int8", threads),
            AsrChoice("small", "cpu", "int8", threads),
            (
                "ASR 在 CPU 執行（RAM ≥ 16 GB）：即時用 base（約 0.45 s）、完整用 small（約 1.3 s）",
                "medium 以上在 CPU 每句要 3.6 s 以上，不適合即時字幕",
            ),
        )
    why = []
    if total is not None and total < CPU_HIGH_RAM_MIN_BYTES:
        why.append(f"RAM {total / GIB:.1f} GB < 16 GB")
    if cores is not None and cores <= CPU_LOW_MAX_CORES:
        why.append(f"實體核心 {cores} ≤ {CPU_LOW_MAX_CORES}")
    reason = "、".join(why) or "硬體資訊不完整"
    warnings.append("低階 CPU 組合：tiny 的辨識品質明顯較差（錯字多）")
    return _AsrPlan(
        "cpu_low",
        AsrChoice("tiny", "cpu", "int8", threads),
        AsrChoice("base", "cpu", "int8", threads),
        (f"{reason}：即時用 tiny、完整用 base，降低延遲與記憶體",),
    )


def _gpu_plan(gpu: GpuInfo, translation_local: bool) -> _AsrPlan:
    total = gpu.memory_total_mb
    cc = gpu.compute_capability_tuple
    pascal = cc == (6, 1)  # 只支援 int8（CTranslate2 文件：float16 需要 7.0 以上）
    vram = f"{gpu.name}（{total / 1024:.1f} GB VRAM）"
    if total >= TIER_8GB_MIN_MB:
        ctype = "int8" if pascal else "float16"
        final = "large-v3-turbo"
        reasons = [
            f"{vram}：即時用 small、完整用 large-v3-turbo（本機實測 0.13 s＋0.15 s）",
            "large-v3-turbo 的 no_speech 不可用，以 small 的 no_speech 與 VAD 輔助過濾",
        ]
        if total >= LARGE_V3_MIN_MB and not translation_local:
            final = "large-v3"
            reasons = [
                f"{vram}，翻譯不在本機：完整路徑改用 large-v3（品質相當、no_speech 可用，約 0.33 s）",
            ]
        return _AsrPlan(
            "nvidia_8gb",
            AsrChoice("small", "cuda", ctype),
            AsrChoice(final, "cuda", ctype),
            tuple(reasons),
        )
    ctype = "int8" if pascal else "int8_float16"
    if total >= TIER_4GB_MIN_MB:
        return _AsrPlan(
            "nvidia_4gb",
            AsrChoice("base", "cuda", ctype),
            AsrChoice("large-v3-turbo", "cuda", ctype),
            (
                f"{vram}：即時用 base、完整用 large-v3-turbo，以 int8_float16 省一半 VRAM 給翻譯模型",
            ),
        )
    return _AsrPlan(
        "nvidia_lt4gb",
        AsrChoice("base", "cuda", ctype),
        AsrChoice("small", "cuda", ctype),
        (f"{vram}：即時用 base、完整用 small（int8_float16）",),
    )


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


def _recommend_translation(
    info: HardwareInfo,
    gpu: GpuInfo | None,
    plan: _AsrPlan,
    allow_restricted: bool,
    reasons: list[str],
    warnings: list[str],
) -> tuple[str | None, str | None, float | None]:
    server = info.translation_server
    if not server.is_local:
        reasons.append(
            f"翻譯伺服器不在本機（{server.base_url}），不推薦翻譯模型；請在設定頁選擇該服務提供的模型"
        )
        return None, None, None

    budget_mb = _translation_budget_mb(info, gpu, plan)
    budget_gb = None if budget_mb is None else _gb(budget_mb)
    installed: tuple[str, ...] | None = server.models
    if not server.reachable:
        warnings.append(
            f"無法連線到翻譯伺服器 {server.base_url}（Ollama 是否已啟動？）；無法確認已安裝的模型"
        )
    elif installed is None:
        warnings.append(f"無法取得翻譯伺服器的模型清單：{server.error}")

    choice = recommend_translation_models(budget_gb, installed, allow_restricted)
    ideal = recommend_translation_models(budget_gb, None, allow_restricted)
    if budget_gb is None:
        reasons.append(
            "沒有可用的 GPU：翻譯模型在 CPU 執行，選最小的模型（CPU 速度未實測）"
        )
        total = info.memory.total_bytes
        if total is not None and total < LOW_RAM_FOR_LLM_BYTES:
            warnings.append("RAM 小於 8 GB，本機翻譯模型與 ASR 同時執行可能不夠用")
    else:
        where = "統一記憶體" if gpu is None else "VRAM"
        reasons.append(
            f"扣掉 ASR 與 {DESKTOP_VRAM_RESERVE_MB / 1024:.0f} GB 保留後，約 {budget_gb:.1f} GB {where} 可給翻譯模型"
        )
    reasons.append(
        "即時與完整翻譯用同一個模型：GPU 上每句都在 0.3 s 內，Ollama 換模型要 6–18 s"
        if choice.fast == choice.final
        else "即時與完整翻譯使用不同模型"
    )

    available = (
        None if installed is None else {normalize_model_name(m) for m in installed}
    )
    for model, best in dict.fromkeys(
        ((choice.fast, ideal.fast), (choice.final, ideal.final))
    ):
        if available is not None and model not in available:
            warnings.append(f"建議的翻譯模型「{model}」尚未安裝，請先在 Ollama 安裝")
        elif model != best:
            reasons.append(f"已安裝的模型中選「{model}」；安裝「{best}」品質較好")
        preset = PRESETS.get(model)
        if (
            budget_gb is not None
            and preset is not None
            and preset.approx_vram_gb > budget_gb
        ):
            warnings.append(
                f"「{model}」約需 {preset.approx_vram_gb} GB，超過可用的 {budget_gb:.1f} GB，"
                "Ollama 會把部分運算放到 CPU，速度較慢"
            )
    return choice.fast, choice.final, budget_gb


def recommend(info: HardwareInfo, *, allow_restricted: bool = True) -> ModelCombination:
    """依偵測結果推薦 ASR 與翻譯模型組合（純函式）。

    Args:
        info: `detect()` 的結果。
        allow_restricted: False 時不推薦有地區限制授權的翻譯模型（例如使用者在
            歐盟、英國、南韓時排除 HY-MT）。非商用授權的模型一律不推薦。

    Returns:
        `ModelCombination`；`reasons`／`warnings` 為繁體中文，可直接顯示。
    """
    reasons: list[str] = []
    warnings: list[str] = []
    gpu = primary_gpu(info)
    gpu_runtime_missing = False
    plan: _AsrPlan | None = None

    if gpu is not None:
        if len(info.gpus) > 1:
            warnings.append(
                f"偵測到 {len(info.gpus)} 張 NVIDIA GPU，以 VRAM 最大的 {gpu.name} 計算"
            )
        problem = _cuda_problem(info, gpu)
        if problem is not None:
            warnings.append(problem)
        elif not info.cublas.available:
            gpu_runtime_missing = True
            warnings.append(
                "cuBLAS 未安裝，GPU 加速需要先下載 GPU 執行環境（約 570 MB）；在那之前 ASR 使用 CPU"
            )
        else:
            plan = _gpu_plan(gpu, info.translation_server.is_local)
            need = asr_vram_need_mb((plan.fast, plan.final))
            if need > gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB:
                warnings.append(
                    f"VRAM 不足以放 ASR 模型（需要約 {need / 1024:.1f} GB），ASR 改用 CPU"
                )
                plan = None
    if plan is None:
        plan = _cpu_plan(info, warnings)
    reasons.extend(plan.reasons)

    fast_t, final_t, budget = _recommend_translation(
        info, gpu, plan, allow_restricted, reasons, warnings
    )
    return ModelCombination(
        tier=plan.tier,
        fast_asr=plan.fast,
        final_asr=plan.final,
        translation_fast=fast_t,
        translation_final=final_t,
        translation_budget_gb=budget,
        gpu_runtime_missing=gpu_runtime_missing,
        reasons=tuple(reasons),
        warnings=tuple(warnings),
    )


# --- 手動覆寫的驗證 --------------------------------------------------------------


def _parse_asr(
    data: object, path: str, errors: list[str]
) -> tuple[str, str, str | None, int] | None:
    if not isinstance(data, Mapping):
        errors.append(f"{path} 必須是物件")
        return None
    model = data.get("model")
    device = data.get("device", "auto")
    compute_type = data.get("compute_type")
    threads = data.get("cpu_threads", 0)
    ok = True
    if not isinstance(model, str) or model not in MODELS:
        errors.append(
            f"{path}.model：未知的 ASR 模型 {str(model)[:50]!r}（可用：{', '.join(MODELS)}）"
        )
        ok = False
    if device not in ("auto", "cpu", "cuda"):
        errors.append(f"{path}.device 必須是 auto、cpu 或 cuda")
        ok = False
    if compute_type is not None and not isinstance(compute_type, str):
        errors.append(f"{path}.compute_type 必須是字串或 null")
        ok = False
    if (
        isinstance(threads, bool)
        or not isinstance(threads, int)
        or not 0 <= threads <= 256
    ):
        errors.append(f"{path}.cpu_threads 必須是 0–256 的整數")
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
    errors: list[str],
    warnings: list[str],
) -> AsrDeviceChoice:
    """檢查裝置與精度；回傳實際會用的裝置（auto 依硬體判斷）。"""
    gpu = primary_gpu(info)
    cuda_ok = (
        gpu is not None and _cuda_problem(info, gpu) is None and info.cublas.available
    )
    if device == "cuda":
        if gpu is None:
            errors.append(f"{path}：選了 CUDA，但沒有偵測到 NVIDIA GPU")
        elif (problem := _cuda_problem(info, gpu)) is not None:
            errors.append(f"{path}：選了 CUDA，但 {problem}")
        elif not info.cublas.available:
            errors.append(f"{path}：選了 CUDA，但 cuBLAS 未安裝；請先下載 GPU 執行環境")
    elif device == "auto" and gpu is not None and not cuda_ok:
        warnings.append(f"{path}：auto 會使用 CPU（GPU 目前無法用於 ASR）")
    actual: AsrDeviceChoice = (
        "cuda" if device == "cuda" or (device == "auto" and cuda_ok) else "cpu"
    )

    if compute_type is not None:
        if compute_type not in ALL_COMPUTE_TYPES:
            errors.append(
                f"{path}.compute_type：未知的精度 {compute_type[:30]!r}"
                f"（可用：{', '.join(sorted(ALL_COMPUTE_TYPES))}）"
            )
        elif actual == "cpu" and compute_type not in CPU_COMPUTE_TYPES:
            # CTranslate2 不會報錯，而是自動轉成 CPU 支援的型別（官方 quantization 文件）
            warnings.append(
                f"{path}：CPU 不支援 {compute_type}，CTranslate2 會自動改用其他精度（通常是 float32，較慢）"
            )
        elif actual == "cuda" and gpu is not None:
            cc = gpu.compute_capability_tuple
            if (
                cc is not None
                and cc < (7, 0)
                and compute_type in ("float16", "int8_float16")
            ):
                warnings.append(
                    f"{path}：compute capability {gpu.compute_capability} 不支援 {compute_type}，"
                    "CTranslate2 會改用 float32 或 int8_float32（較慢、較耗 VRAM）"
                )
    if actual == "cpu":
        slow = CPU_SLOW_FAST_MODELS if path == "fast_asr" else CPU_SLOW_FINAL_MODELS
        if model in slow:
            warnings.append(
                f"{path}：{model} 在 CPU 上每句要 3.6 s 以上，字幕會明顯延遲"
            )
    return actual


def _translation_name(
    data: Mapping[str, Any], key: str, errors: list[str]
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
            f"{key} 必須是 1–{MAX_TRANSLATION_MODEL_CHARS} 字的模型名稱或 null"
        )
        return None
    return value.strip()


def validate_combination(
    data: Mapping[str, Any],
    info: HardwareInfo,
    *,
    downloaded_asr: Collection[str] | None = None,
    allow_restricted: bool = True,
) -> ValidationResult:
    """驗證使用者手動選的組合（純函式）；格式錯誤也以 `errors` 回報，不丟例外。

    Args:
        data: 與 `ModelCombination.to_dict()` 相同的形狀，至少要有 `fast_asr`、
            `final_asr`（各含 `model`，選填 `device`＝auto／cpu／cuda、`compute_type`、
            `cpu_threads`），選填 `translation_fast`、`translation_final`。
        info: `detect()` 的結果。
        downloaded_asr: 已下載的 ASR 模型 id；None 表示不檢查。
        allow_restricted: False 時，選了有地區限制授權的翻譯模型會產生警告。

    Returns:
        `errors`（無法使用）與 `warnings`（可用但要注意），皆為繁體中文。
    """
    errors: list[str] = []
    warnings: list[str] = []
    if not isinstance(data, Mapping):
        return ValidationResult(errors=("組合必須是物件",))

    chosen: list[AsrChoice] = []
    for key in ("fast_asr", "final_asr"):
        parsed = _parse_asr(data.get(key), key, errors)
        if parsed is None:
            continue
        model, device, compute_type, threads = parsed
        actual = _check_asr_device(
            info, key, model, device, compute_type, errors, warnings
        )
        if downloaded_asr is not None and model not in downloaded_asr:
            warnings.append(f"{key}：ASR 模型 {model} 尚未下載，開始前會先下載")
        default_type = "float16" if actual == "cuda" else "int8"
        chosen.append(AsrChoice(model, actual, compute_type or default_type, threads))

    if len(chosen) == 2:
        fast, final = chosen
        if MODELS[fast.model].approx_cpu_ram_mb > MODELS[final.model].approx_cpu_ram_mb:
            warnings.append("即時模型比完整模型大：即時字幕可能比完整翻譯還慢")
        if (
            not MODELS[final.model].no_speech_reliable
            and not MODELS[fast.model].no_speech_reliable
        ):
            warnings.append(
                "兩條路徑的模型都無法用 no_speech 過濾非語音，歌聲或雜音較容易被誤辨識"
            )

    fast_t = _translation_name(data, "translation_fast", errors)
    final_t = _translation_name(data, "translation_final", errors)
    server = info.translation_server
    installed = (
        None
        if server.models is None
        else {normalize_model_name(m) for m in server.models}
    )
    for name in dict.fromkeys(m for m in (fast_t, final_t) if m is not None):
        if installed is not None and normalize_model_name(name) not in installed:
            warnings.append(f"翻譯伺服器上沒有「{name}」")
        preset = preset_for_model(name)
        if preset.license == "unknown":
            continue
        if not preset.commercial_use:
            warnings.append(f"「{name}」的授權（{preset.license}）禁止商業用途")
        if preset.excluded_regions and not allow_restricted:
            warnings.append(
                f"「{name}」的授權（{preset.license}）不適用於 {'、'.join(preset.excluded_regions)}"
            )
    if (
        fast_t
        and final_t
        and normalize_model_name(fast_t) != normalize_model_name(final_t)
        and server.is_local
    ):
        warnings.append("即時與完整翻譯用不同模型：本機 Ollama 換模型每次要 6–18 秒")

    gpu = primary_gpu(info)
    if gpu is not None and chosen:
        need = asr_vram_need_mb(chosen)
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
        if need > gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB:
            warnings.append(
                f"ASR 與翻譯模型合計約需 {need / 1024:.1f} GB VRAM，超過 {gpu.name} 可用的 "
                f"{(gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB) / 1024:.1f} GB；可能變慢或載入失敗"
            )
    total_ram = info.memory.total_bytes
    if total_ram is not None and asr_ram_need_mb(chosen) * MIB > total_ram * 0.5:
        warnings.append("CPU 上的 ASR 模型會用掉超過一半的 RAM")
    return ValidationResult(errors=tuple(errors), warnings=tuple(warnings))
