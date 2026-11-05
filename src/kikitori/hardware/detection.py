"""`detect()`：一次收集作業系統、CPU、RAM、NVIDIA GPU、cuBLAS 與翻譯伺服器狀態。

位於 hardware 模組的入口層：引擎的 `hardware.detect` 呼叫它，再把結果交給
`recommend()`。任何一項偵測失敗都只記 log 並寫進 `HardwareInfo.errors`，
不讓整個偵測失敗——UI 仍要能顯示其他資訊並讓使用者手動選模型。
"""

import logging
import os
import platform
import sys
from collections.abc import Sequence
from pathlib import Path

from kikitori.hardware import system
from kikitori.hardware.gpu_runtime import installed_runtime_dir
from kikitori.hardware.nvidia import (
    NVIDIA_SMI_TIMEOUT_SECS,
    NvidiaSmiError,
    find_cublas,
    find_nvidia_smi,
    query_nvidia_gpus,
)
from kikitori.hardware.recommend import cpu_threads_per_model
from kikitori.hardware.translation_server import (
    PROBE_TIMEOUT_SECS,
    probe_translation_server,
)
from kikitori.hardware.types import GpuInfo, HardwareInfo, OsName
from kikitori.translate import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL

logger = logging.getLogger(__name__)


def _detect_gpus(name: OsName, timeout: float) -> tuple[list[GpuInfo], list[str]]:
    if name not in ("windows", "linux"):
        # macOS 沒有 NVIDIA 驅動；其他平台 CTranslate2 也沒有 CUDA wheel
        return [], []
    executable = find_nvidia_smi(name)
    if executable is None:
        logger.info("nvidia-smi not found; assuming no NVIDIA GPU")
        return [], []
    try:
        gpus, problems = query_nvidia_gpus(executable, timeout=timeout)
    except NvidiaSmiError as exc:
        logger.warning("NVIDIA GPU detection failed: %s", exc)
        return [], [f"gpu: {exc}"]
    for problem in problems:
        logger.warning("nvidia-smi: %s", problem)
    return gpus, [f"gpu: {p}" for p in problems]


def auto_cpu_threads() -> int:
    """設定 `asr.*_cpu_threads` 為 null（自動）時每個 ASR 模型用的 CPU 執行緒數。

    只偵測 CPU（不執行 nvidia-smi、不連網，毫秒等級），規則與 `recommend()` 相同：
    `min(8, 實體核心 // 2)`；核心數未知時 0（CTranslate2 預設值）。
    """
    name = system.os_name(sys.platform)
    cpu, _ = system.detect_cpu(name, platform.machine())
    return cpu_threads_per_model(cpu)


def detect(
    *,
    base_url: str = DEFAULT_BASE_URL,
    api_key_env: str = DEFAULT_API_KEY_ENV,
    cuda_library_dirs: Sequence[Path] = (),
    runtime_root: Path | None = None,
    nvidia_smi_timeout: float = NVIDIA_SMI_TIMEOUT_SECS,
    probe_timeout: float = PROBE_TIMEOUT_SECS,
) -> HardwareInfo:
    """偵測目前這台電腦。

    Args:
        base_url: 翻譯 API 的 base URL（設定 `translation.base_url`）。
        api_key_env: 讀翻譯 API 金鑰的環境變數名稱（設定 `translation.api_key_env`）。
        cuda_library_dirs: 使用者指定的 cuBLAS 目錄（優先於其他位置）。
        runtime_root: GPU 執行環境根目錄；省略時用 `default_runtime_root()`。
        nvidia_smi_timeout: `nvidia-smi` 的逾時秒數。
        probe_timeout: 翻譯伺服器的連線與讀取逾時秒數。

    Returns:
        `HardwareInfo`；偵測失敗的項目為 None 或空，原因在 `errors`。
    """
    name = system.os_name(sys.platform)
    machine = platform.machine()
    errors: list[str] = []

    cpu, cpu_errors = system.detect_cpu(name, machine)
    memory, memory_errors = system.detect_memory(name)
    gpus, gpu_errors = _detect_gpus(name, nvidia_smi_timeout)
    errors += cpu_errors + memory_errors + gpu_errors

    try:
        runtime = installed_runtime_dir(runtime_root)
    except OSError as exc:
        logger.warning("cannot check the GPU runtime: %s", exc)
        errors.append(f"gpu runtime: {exc}")
        runtime = None
    cublas = find_cublas(
        name,
        machine,
        configured_dirs=cuda_library_dirs,
        runtime_dir=runtime,
        env=os.environ,
    )
    server = probe_translation_server(base_url, api_key_env, timeout=probe_timeout)

    return HardwareInfo(
        os=name,
        os_version=system.os_version(name),
        machine=machine,
        cpu=cpu,
        memory=memory,
        gpus=tuple(gpus),
        apple_silicon=system.detect_apple_silicon(name, machine),
        cublas=cublas,
        translation_server=server,
        errors=tuple(errors),
    )
