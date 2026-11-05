"""引擎依賴的外部服務：設定、裝置、音訊來源、pipeline 元件、硬體偵測、模型清單。

位於 engine 與其他模組之間。伺服器只透過 `EngineServices` 使用這些功能，所以
測試能換成假的實作（見 `testing.py`）。預設實作用真實的 capture、`pipeline.factory`
與 `kikitori.hardware`（偵測、模型總覽、GPU 執行環境安裝）。
"""

import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from kikitori.capture.types import AudioSource, DeviceInfo, DeviceKind
from kikitori.config import Settings, SettingsStore, default_settings_path
from kikitori.hardware import (
    GpuRuntimeStatus,
    HardwareInfo,
    ModelCombination,
)
from kikitori.pipeline.factory import AsrModelInfo
from kikitori.pipeline.types import Mode, PipelineComponents

ProgressCallback = Callable[[int, int], None]


class HardwareProvider(Protocol):
    """硬體偵測、模型總覽與 GPU 執行環境（`kikitori.hardware` 的接縫）。

    推薦與組合驗證是純函式，由伺服器直接呼叫 `kikitori.hardware`；這裡只放有 I/O、
    測試需要換掉的部分。
    """

    def detect(self, settings: Settings) -> HardwareInfo:
        """偵測這台電腦與翻譯伺服器（會執行外部程式與網路請求，最久數秒）。"""
        ...

    def models_overview(
        self, info: HardwareInfo, recommendation: ModelCombination
    ) -> dict[str, Any]:
        """`{"asr": [...], "gpu_runtime": {...}, "translation": {"server", "models"}}`。"""
        ...

    def gpu_runtime_status(self) -> GpuRuntimeStatus:
        """GPU 執行環境是否支援、是否已安裝（只看檔案，很快）。"""
        ...

    def install_gpu_runtime(
        self, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """下載並安裝 GPU 執行環境；`cancel` 被設定時停止。失敗時丟例外。"""
        ...


class ModelCatalog(Protocol):
    """ASR 模型的清單與下載（`models.download` 的 ASR 部分）。"""

    def asr_models(self) -> list[AsrModelInfo]:
        """可選的 ASR 模型與是否已下載。"""
        ...

    def download(
        self, model_id: str, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """下載 ASR 模型；`cancel` 被設定時停止。失敗時丟例外。"""
        ...


@dataclass(frozen=True)
class EngineServices:
    """伺服器需要的全部外部功能。

    Attributes:
        settings_store: 設定的唯一持有者。
        list_devices: 列出擷取裝置。
        open_source: `(device_id, kind)` → 尚未 start 的 `AudioSource`。
        build_components: `(settings, mode)` → pipeline 元件（可能要載入模型數秒）。
        hardware: 硬體偵測、模型總覽與 GPU 執行環境。
        models: ASR 模型清單與下載。
        fake: 是否為測試用的假服務（回報在 `engine.info`）。
    """

    settings_store: SettingsStore
    list_devices: Callable[[], list[DeviceInfo]]
    open_source: Callable[[str | None, DeviceKind], AudioSource]
    build_components: Callable[[Settings, Mode], PipelineComponents]
    hardware: HardwareProvider
    models: ModelCatalog
    fake: bool = False


def platform_name() -> str:
    """ "windows"、"macos"、"linux" 或 `sys.platform` 的原值。"""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


class DefaultHardwareProvider:
    """真實的實作：直接呼叫 `kikitori.hardware`。"""

    def detect(self, settings: Settings) -> HardwareInfo:
        """見 `hardware.detect()`；設定指定的 cuBLAS 目錄優先於其他位置。"""
        from kikitori.hardware import detect

        return detect(
            base_url=settings.translation.base_url,
            api_key_env=settings.translation.api_key_env,
            cuda_library_dirs=[Path(d) for d in settings.asr.cuda_library_dirs],
        )

    def models_overview(
        self, info: HardwareInfo, recommendation: ModelCombination
    ) -> dict[str, Any]:
        """見 `hardware.models_overview()`（ASR 快取與 GPU 執行環境用預設位置）。"""
        from kikitori.hardware import models_overview

        return models_overview(info, recommendation)

    def gpu_runtime_status(self) -> GpuRuntimeStatus:
        """見 `hardware.gpu_runtime_status()`。"""
        from kikitori.hardware import gpu_runtime_status

        return gpu_runtime_status()

    def install_gpu_runtime(
        self, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """見 `hardware.install_gpu_runtime()`（從 PyPI 下載 NVIDIA cuBLAS wheel）。"""
        from kikitori.hardware import install_gpu_runtime

        install_gpu_runtime(progress=progress, cancel=cancel)


class DefaultModelCatalog:
    """真實的 ASR 模型清單與下載，經由 `pipeline.factory`（engine 唯一的 ASR 接縫）。"""

    def asr_models(self) -> list[AsrModelInfo]:
        """見 `factory.asr_model_catalog()`。"""
        from kikitori.pipeline.factory import asr_model_catalog

        return asr_model_catalog()

    def download(
        self, model_id: str, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """見 `factory.download_asr_model()`。"""
        from kikitori.pipeline.factory import download_asr_model

        download_asr_model(model_id, progress, cancel)


def default_services() -> EngineServices:
    """正式使用的服務：真實 capture、設定檔、factory 與 hardware 模組。"""
    from kikitori.capture import list_devices, open_source
    from kikitori.pipeline.factory import build_session_components

    return EngineServices(
        settings_store=SettingsStore(default_settings_path()),
        list_devices=list_devices,
        open_source=open_source,
        build_components=build_session_components,
        hardware=DefaultHardwareProvider(),
        models=DefaultModelCatalog(),
    )
