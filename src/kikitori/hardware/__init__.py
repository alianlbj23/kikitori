"""硬體偵測與模型組合：決定這台電腦該用哪些 ASR／翻譯模型與運算裝置。

位於引擎與 asr／translate 之間：引擎的 `hardware.detect` 呼叫 `hardware_report()`
（或 `detect()` + `recommend()`），`models.list` 呼叫 `models_overview()`，設定頁的
手動選擇用 `validate_combination()` 檢查，GPU 執行環境（cuBLAS）由使用者按鈕觸發
`install_gpu_runtime()`。規格見 `docs/specs/hardware.md`。
"""

from collections.abc import Sequence
from pathlib import Path
from typing import Any

from kikitori.hardware.detection import auto_cpu_threads, detect
from kikitori.hardware.gpu_runtime import (
    CUBLAS_VERSION,
    GPU_RUNTIME_ID,
    GpuRuntimeError,
    GpuRuntimeStatus,
    default_runtime_root,
    gpu_runtime_status,
    install_gpu_runtime,
    installed_runtime_dir,
)
from kikitori.hardware.messages import CATALOG as MESSAGE_CATALOG
from kikitori.hardware.messages import Message, message
from kikitori.hardware.overview import models_overview
from kikitori.hardware.recommend import (
    cpu_threads_per_model,
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
    ModelCombination,
    TranslationServerInfo,
    TranslationStatus,
    ValidationResult,
)
from kikitori.translate import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL


def hardware_report(
    *,
    base_url: str = DEFAULT_BASE_URL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    cuda_library_dirs: Sequence[Path] = (),
    allow_restricted: bool = True,
) -> dict[str, Any]:
    """`hardware.detect` 的結果：`{"hardware": {...}, "recommendation": {...}}`（可直接 JSON 化）。

    參數同 `detect()` 與 `recommend()`；會執行 `nvidia-smi` 與連線翻譯伺服器，
    最久約數秒，請不要在讀 stdin 的執行緒上呼叫。
    """
    info = detect(
        base_url=base_url,
        api_key_env=api_key_env,
        cuda_library_dirs=cuda_library_dirs,
    )
    return {
        "hardware": info.to_dict(),
        "recommendation": recommend(info, allow_restricted=allow_restricted).to_dict(),
    }


__all__ = [
    "CUBLAS_VERSION",
    "GPU_RUNTIME_ID",
    "MESSAGE_CATALOG",
    "AsrChoice",
    "CpuInfo",
    "CublasInfo",
    "GpuInfo",
    "GpuRuntimeError",
    "GpuRuntimeStatus",
    "HardwareInfo",
    "LanguageSelection",
    "MemoryInfo",
    "Message",
    "ModelCombination",
    "TranslationServerInfo",
    "TranslationStatus",
    "ValidationResult",
    "auto_cpu_threads",
    "cpu_threads_per_model",
    "default_runtime_root",
    "detect",
    "gpu_runtime_status",
    "hardware_report",
    "install_gpu_runtime",
    "installed_runtime_dir",
    "message",
    "models_overview",
    "recommend",
    "validate_combination",
]
