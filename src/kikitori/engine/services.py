"""引擎依賴的外部服務：設定、裝置、音訊來源、pipeline 元件、硬體偵測、模型清單、翻譯模型。

位於 engine 與其他模組之間。伺服器只透過 `EngineServices` 使用這些功能，所以
測試能換成假的實作（見 `testing.py`）。預設實作用真實的 capture、`pipeline.factory`
與 `kikitori.hardware`（偵測、模型總覽、GPU 執行環境安裝、Ollama 下載翻譯模型）。
"""

import logging
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
from kikitori.pipeline.factory import AsrModelInfo, LoadProgress
from kikitori.pipeline.types import Mode, PipelineComponents

ProgressCallback = Callable[[int, int], None]

logger = logging.getLogger(__name__)


class ComponentBuilder(Protocol):
    """建立一個 session 的 pipeline 元件（載入模型，數秒到數十秒）。"""

    def __call__(
        self,
        settings: Settings,
        mode: Mode,
        *,
        progress: LoadProgress | None = None,
        cancelled: threading.Event | None = None,
    ) -> PipelineComponents:
        """見 `factory.build_session_components()`。

        Args:
            settings: 這個 session 的設定。
            mode: session 模式。
            progress: 每完成一個載入步驟呼叫 `(步驟, 已完成數, 總數)`；可以不呼叫
                （例如假服務），引擎只是少送進度。
            cancelled: session 已被停止；之後的步驟可以省略（載入本身不能中斷）。
        """
        ...


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

    def delete(self, model_id: str) -> int:
        """刪除已下載的 ASR 模型（含未完成下載的殘留），回傳釋放的位元組數。

        呼叫端（引擎）先確認模型不在使用中。

        Raises:
            UnknownModelError: 模型 id 不在清單中。
            FileNotFoundError: 模型沒有下載。
            OSError、ValueError: 刪除失敗或路徑不安全（不在模型快取內、是連結）。
        """
        ...


@dataclass(frozen=True)
class TranslationServerState:
    """翻譯伺服器現在能不能替使用者下載模型。

    Attributes:
        reachable: 有收到 HTTP 回應。
        is_ollama: 是 Ollama（有原生的 `/api/version`），才能用 `/api/pull` 下載。
        installed: 伺服器回報的模型名稱；取不到時為 None。
        error: 連不上或回應不對的原因（英文，不含金鑰與網址中的帳密）。
        auth_failed: 伺服器回 401／403：金鑰錯誤或缺少，無法確認是不是 Ollama。
            與「不是 Ollama」分開，UI 才能請使用者檢查金鑰，而不是叫他自行安裝模型。
    """

    reachable: bool
    is_ollama: bool
    installed: tuple[str, ...] | None = None
    error: str | None = None
    auth_failed: bool = False


class TranslationModels(Protocol):
    """翻譯伺服器上的模型：狀態與下載（`models.pull_translation`、`session.start` 的檢查）。"""

    def server_state(self, settings: Settings) -> TranslationServerState:
        """連線檢查翻譯伺服器（最久數秒）；任何錯誤都放進結果，不丟例外。"""
        ...

    def pull(
        self,
        settings: Settings,
        model: str,
        progress: ProgressCallback,
        cancel: threading.Event,
    ) -> None:
        """請 Ollama 下載模型；`cancel` 被設定時停止。失敗時丟例外。"""
        ...


@dataclass(frozen=True)
class EngineServices:
    """伺服器需要的全部外部功能。

    Attributes:
        settings_store: 設定的唯一持有者。
        list_devices: 列出擷取裝置。
        open_source: `(device_id, kind)` → 尚未 start 的 `AudioSource`。
        build_components: `(settings, mode, *, progress, cancelled)` → pipeline 元件
            （可能要載入模型數秒，見 `ComponentBuilder`）。
        hardware: 硬體偵測、模型總覽與 GPU 執行環境。
        models: ASR 模型清單與下載。
        translation_models: 翻譯伺服器的狀態與（Ollama）模型下載。
        fake: 是否為測試用的假服務（回報在 `engine.info`）。
        release_models: 引擎結束時釋放行程內保留的模型（ASR 模型池）；None 表示不需要。
    """

    settings_store: SettingsStore
    list_devices: Callable[[], list[DeviceInfo]]
    open_source: Callable[[str | None, DeviceKind], AudioSource]
    build_components: ComponentBuilder
    hardware: HardwareProvider
    models: ModelCatalog
    translation_models: TranslationModels
    fake: bool = False
    release_models: Callable[[], None] | None = None


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

    def delete(self, model_id: str) -> int:
        """見 `factory.delete_asr_model()`。"""
        from kikitori.pipeline.factory import delete_asr_model

        return delete_asr_model(model_id)


class DefaultTranslationModels:
    """真實的實作：`GET {base_url}/models` 判斷可連與已安裝的模型，再以 Ollama 原生的
    `/api/version` 判斷能不能下載（`kikitori.hardware.ollama`）。"""

    def server_state(self, settings: Settings) -> TranslationServerState:
        """見 `TranslationModels.server_state()`。"""
        from kikitori.hardware.ollama import (
            NotOllamaError,
            OllamaAuthError,
            OllamaUnreachableError,
            ollama_version,
        )
        from kikitori.hardware.translation_server import probe_translation_server

        t = settings.translation
        info = probe_translation_server(t.base_url, t.api_key_env)
        if not info.reachable:
            return TranslationServerState(False, False, None, info.error)
        try:
            version = ollama_version(t.base_url, api_key_env=t.api_key_env)
        except OllamaAuthError as exc:
            logger.info("translation server rejected the API key: %s", exc)
            return TranslationServerState(
                True, False, info.models, str(exc), auth_failed=True
            )
        except NotOllamaError as exc:
            logger.info("translation server is not Ollama: %s", exc)
            return TranslationServerState(True, False, info.models, str(exc))
        except (OllamaUnreachableError, ValueError) as exc:
            # 剛剛還連得上：伺服器在兩個請求之間關掉了
            logger.info("translation server went away: %s", exc)
            return TranslationServerState(False, False, None, str(exc))
        logger.debug("translation server is Ollama %s", version)
        return TranslationServerState(True, True, info.models, info.error)

    def pull(
        self,
        settings: Settings,
        model: str,
        progress: ProgressCallback,
        cancel: threading.Event,
    ) -> None:
        """見 `hardware.ollama.pull_model()`。"""
        from kikitori.hardware.ollama import pull_model

        t = settings.translation
        pull_model(
            t.base_url,
            model,
            progress=progress,
            cancel=cancel,
            api_key_env=t.api_key_env,
        )


def default_services() -> EngineServices:
    """正式使用的服務：真實 capture、設定檔、factory 與 hardware 模組。"""
    from kikitori.capture import list_devices, open_source
    from kikitori.pipeline.factory import build_session_components, release_asr_models

    return EngineServices(
        settings_store=SettingsStore(default_settings_path()),
        list_devices=list_devices,
        open_source=open_source,
        build_components=build_session_components,
        hardware=DefaultHardwareProvider(),
        models=DefaultModelCatalog(),
        translation_models=DefaultTranslationModels(),
        release_models=release_asr_models,
    )
