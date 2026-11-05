"""引擎依賴的外部服務：設定、裝置、音訊來源、pipeline 元件、硬體偵測、模型清單。

位於 engine 與其他模組之間。伺服器只透過 `EngineServices` 使用這些功能，所以
測試能換成假的實作（見 `testing.py`），之後的 hardware 里程碑也只需提供新的
`HardwareProvider`。預設實作用真實的 capture、`pipeline.factory` 與標準庫。
"""

import ctypes
import json
import logging
import os
import platform
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

from kikitori.capture.types import AudioSource, DeviceInfo, DeviceKind
from kikitori.config import Settings, SettingsStore, default_settings_path
from kikitori.pipeline.factory import AsrModelInfo
from kikitori.pipeline.types import Mode, PipelineComponents

TRANSLATION_LIST_TIMEOUT = 3.0
MAX_MODEL_LIST_BYTES = 1024 * 1024

logger = logging.getLogger(__name__)


class ModelListError(ValueError):
    """翻譯 API 的模型清單回應不合法（太大、不是 JSON、格式不對）。"""


ProgressCallback = Callable[[int, int], None]


class HardwareProvider(Protocol):
    """`hardware.detect` 的實作。"""

    def detect(self) -> dict[str, Any]:
        """回傳 `{"hardware": {...}, "recommendation": {...} | None}`（可直接 JSON 化）。"""
        ...


class ModelCatalog(Protocol):
    """`models.list`／`models.download` 的實作。"""

    def asr_models(self) -> list[AsrModelInfo]:
        """可選的 ASR 模型與是否已下載。"""
        ...

    def installed_translation_models(self, settings: Settings) -> list[str]:
        """翻譯 API 上已安裝的模型名稱。

        Raises:
            OSError、ValueError: 連不上或回應格式不對。
        """
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
        hardware: 硬體偵測。
        models: 模型清單與下載。
        fake: 是否為測試用的假服務（回報在 `engine.info`）。
    """

    settings_store: SettingsStore
    list_devices: Callable[[], list[DeviceInfo]]
    open_source: Callable[[str | None, DeviceKind], AudioSource]
    build_components: Callable[[Settings, Mode], PipelineComponents]
    hardware: HardwareProvider
    models: ModelCatalog
    fake: bool = False


# --- 硬體 -------------------------------------------------------------------


def _ram_bytes() -> int | None:
    """實體記憶體大小；取不到時回傳 None（並記 warning）。"""
    try:
        if sys.platform == "win32":

            class _MemoryStatusEx(ctypes.Structure):
                _fields_ = [
                    ("dwLength", ctypes.c_ulong),
                    ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong),
                    ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong),
                    ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong),
                    ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
                ]

            status = _MemoryStatusEx()
            status.dwLength = ctypes.sizeof(_MemoryStatusEx)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):  # type: ignore[attr-defined]
                raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")
            return int(status.ullTotalPhys)
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages <= 0 or page_size <= 0:
            raise ValueError(f"sysconf returned {pages} pages of {page_size} bytes")
        return int(pages * page_size)
    except (OSError, ValueError, AttributeError) as exc:
        logger.warning("cannot detect RAM size: %s", exc)
        return None


class BasicHardwareProvider:
    """只用標準庫的硬體偵測（作業系統、CPU、RAM）；GPU 與推薦留給 hardware 里程碑。"""

    def detect(self) -> dict[str, Any]:
        """回傳硬體資訊；`gpus` 為空、`recommendation` 為 None（尚未實作）。"""
        machine = platform.machine()
        return {
            "hardware": {
                "os": platform_name(),
                "os_version": platform.version(),
                "machine": machine,
                "cpu_count": os.cpu_count(),
                "ram_bytes": _ram_bytes(),
                "gpus": [],
                "apple_silicon": sys.platform == "darwin"
                and machine.lower() in ("arm64", "aarch64"),
            },
            "recommendation": None,
        }


def platform_name() -> str:
    """ "windows"、"macos"、"linux" 或 `sys.platform` 的原值。"""
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    if sys.platform.startswith("linux"):
        return "linux"
    return sys.platform


# --- 翻譯模型清單 -------------------------------------------------------------


def _models_url(base_url: str) -> str:
    parts = urllib.parse.urlsplit(base_url.rstrip("/"))
    netloc = parts.netloc
    # Windows 的 localhost 先解析成 ::1，而 Ollama 只聽 IPv4，每次會白等約 2 秒
    # （見 translate.md）；HTTPS 不改寫，避免憑證主機名稱不符。
    if parts.scheme == "http" and parts.hostname == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
    return urllib.parse.urlunsplit(
        (parts.scheme, netloc, parts.path + "/models", "", "")
    )


def list_openai_models(
    base_url: str,
    api_key_env: str,
    timeout: float = TRANSLATION_LIST_TIMEOUT,
) -> list[str]:
    """以 OpenAI 相容的 `GET {base_url}/models` 列出模型名稱。

    Raises:
        OSError: 連線、逾時或 HTTP 錯誤（`urllib.error.URLError` 是 OSError）。
        ValueError: 回應太大、不是 JSON 或格式不對。
    """
    url = _models_url(base_url)
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    key = os.environ.get(api_key_env, "").strip()
    if key:
        if not key.isascii() or not key.isprintable():
            raise ValueError(
                f"environment variable {api_key_env} has invalid characters"
            )
        request.add_header("Authorization", f"Bearer {key}")
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        raw = resp.read(MAX_MODEL_LIST_BYTES + 1)
    if len(raw) > MAX_MODEL_LIST_BYTES:
        raise ModelListError("model list response is too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelListError(f"model list is not valid JSON: {exc}") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ModelListError("model list has no 'data' array")
    names = [
        item["id"]
        for item in items
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    ]
    return names


class DefaultModelCatalog:
    """真實的模型清單：ASR 走 `pipeline.factory`，翻譯模型問 OpenAI 相容 API。"""

    def asr_models(self) -> list[AsrModelInfo]:
        """見 `factory.asr_model_catalog()`。"""
        from kikitori.pipeline.factory import asr_model_catalog

        return asr_model_catalog()

    def installed_translation_models(self, settings: Settings) -> list[str]:
        """見 `list_openai_models()`。"""
        return list_openai_models(
            settings.translation.base_url, settings.translation.api_key_env
        )

    def download(
        self, model_id: str, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """見 `factory.download_asr_model()`。"""
        from kikitori.pipeline.factory import download_asr_model

        download_asr_model(model_id, progress, cancel)


def default_services() -> EngineServices:
    """正式使用的服務：真實 capture、設定檔、factory 與標準庫硬體偵測。"""
    from kikitori.capture import list_devices, open_source
    from kikitori.pipeline.factory import build_components

    return EngineServices(
        settings_store=SettingsStore(default_settings_path()),
        list_devices=list_devices,
        open_source=open_source,
        build_components=build_components,
        hardware=BasicHardwareProvider(),
        models=DefaultModelCatalog(),
    )
