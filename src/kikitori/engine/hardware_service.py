"""引擎的硬體與模型方法：偵測快取、推薦、模型總覽、組合驗證、設定與組合的轉換。

位於 engine 的方法層（server 在背景工作執行緒上呼叫），把 `kikitori.hardware`
的純函式（`recommend()`、`validate_combination()`）與 `HardwareProvider` 的 I/O
（偵測、總覽）接到設定上：設定檔是 config 的格式（`asr.fast_model`…），hardware
用 `ModelCombination` 的格式（`fast_asr.model`…），兩者的對應只寫在這裡。
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kikitori.config import Settings
from kikitori.engine.services import HardwareProvider, ModelCatalog
from kikitori.hardware import (
    HardwareInfo,
    ModelCombination,
    recommend,
    validate_combination,
)
from kikitori.translate.presets import PRESETS, normalize_model_name

DETECTION_CACHE_SECS = 10.0
"""`models.list`／`models.validate`／`settings.apply_recommendation` 重用偵測結果的時間。

UI 開設定頁時通常連續呼叫好幾個方法，不必每次都重跑 nvidia-smi 與連線翻譯伺服器；
但要短到使用者剛開好 Ollama、按下重新整理就看得到。`hardware.detect` 一律重新偵測。
"""


def combination_from_settings(settings: Settings) -> dict[str, Any]:
    """把設定轉成 `validate_combination()` 的輸入（`ModelCombination.to_dict()` 的形狀）。

    `cpu_threads` 為 null（自動）時以 0 代表，驗證只檢查明確指定的值。
    """
    asr = settings.asr
    return {
        "fast_asr": {
            "model": asr.fast_model,
            "device": asr.device,
            "compute_type": asr.fast_compute_type,
            "cpu_threads": asr.fast_cpu_threads or 0,
        },
        "final_asr": {
            "model": asr.final_model,
            "device": asr.device,
            "compute_type": asr.final_compute_type,
            "cpu_threads": asr.final_cpu_threads or 0,
        },
        "translation_fast": settings.translation.fast_model,
        "translation_final": settings.translation.final_model,
    }


def recommendation_patch(rec: ModelCombination) -> dict[str, Any]:
    """把推薦組合轉成 `settings.set` 的部分設定（深度合併用）。

    - `asr.device` 直接用推薦的裝置（cuda／cpu），不用 auto：推薦 CPU 時（例如缺
      cuBLAS）若存成 auto，asr 會先試 CUDA、失敗後整個行程都不再試，裝好 GPU 執行環境
      也要重啟引擎；兩條路徑的裝置不同時（目前的推薦表不會發生）才存 auto。
    - 推薦的執行緒數 0（GPU 或核心數未知）存成 null（自動）。
    - 翻譯模型為 null（伺服器不在本機，無從推薦）時保留目前設定。
    - `cuda_library_dirs` 不動：kikitori 下載的 GPU 執行環境由 factory 自動加入。
    """
    fast, final = rec.fast_asr, rec.final_asr
    asr: dict[str, Any] = {
        "fast_model": fast.model,
        "final_model": final.model,
        "device": fast.device if fast.device == final.device else "auto",
        "fast_compute_type": fast.compute_type,
        "final_compute_type": final.compute_type,
        "fast_cpu_threads": fast.cpu_threads or None,
        "final_cpu_threads": final.cpu_threads or None,
    }
    patch: dict[str, Any] = {"asr": asr}
    translation = {
        key: value
        for key, value in (
            ("fast_model", rec.translation_fast),
            ("final_model", rec.translation_final),
        )
        if value is not None
    }
    if translation:
        patch["translation"] = translation
    return patch


def legacy_presets(installed: tuple[str, ...] | None) -> list[dict[str, Any]]:
    """`models.list` 第一版的 `translation.presets`（保留給尚未改用 `models` 的 UI）。"""
    names = None if installed is None else {normalize_model_name(m) for m in installed}
    return [
        {
            "model": preset.model,
            "label": preset.label,
            "license": preset.license,
            "license_url": preset.license_url,
            "commercial_use": preset.commercial_use,
            "excluded_regions": list(preset.excluded_regions),
            "installed": None if names is None else preset.model in names,
        }
        for preset in PRESETS.values()
    ]


@dataclass(frozen=True)
class _Cached:
    key: tuple[object, ...]
    at: float
    info: HardwareInfo


def _detect_key(settings: Settings) -> tuple[object, ...]:
    # 只有影響偵測結果的設定；allow_restricted 只影響推薦（純函式，每次重算）。
    return (
        settings.translation.base_url,
        settings.translation.api_key_env,
        settings.asr.cuda_library_dirs,
    )


class HardwareService:
    """`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation` 的實作。

    方法會偵測硬體（數秒），只能在背景工作執行緒上呼叫；快取有鎖，`invalidate()`
    可從其他執行緒呼叫（例如 GPU 執行環境裝好之後）。
    """

    def __init__(
        self,
        provider: HardwareProvider,
        catalog: ModelCatalog,
        *,
        cache_secs: float = DETECTION_CACHE_SECS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            provider: 偵測與總覽的 I/O。
            catalog: ASR 模型清單（判斷哪些已下載）。
            cache_secs: 偵測結果的重用時間。
            clock: 單調時鐘（測試可注入）。
        """
        self._provider = provider
        self._catalog = catalog
        self._cache_secs = cache_secs
        self._clock = clock
        self._lock = threading.Lock()
        self._cached: _Cached | None = None
        # invalidate() 每次加一；偵測期間若改變，代表結果可能已過時，不放進快取
        self._generation = 0

    def invalidate(self) -> None:
        """丟掉快取的偵測結果（硬體或 GPU 執行環境狀態改變時）。"""
        with self._lock:
            self._cached = None
            self._generation += 1

    def info(self, settings: Settings, *, fresh: bool) -> HardwareInfo:
        """偵測結果；`fresh=False` 時重用 `cache_secs` 內、相同設定的結果。"""
        key = _detect_key(settings)
        with self._lock:
            cached = self._cached
            generation = self._generation
        if (
            not fresh
            and cached is not None
            and cached.key == key
            and self._clock() - cached.at < self._cache_secs
        ):
            return cached.info
        info = self._provider.detect(settings)
        with self._lock:
            if self._generation == generation:
                self._cached = _Cached(key, self._clock(), info)
        return info

    def recommendation(
        self, settings: Settings, *, fresh: bool = False
    ) -> tuple[HardwareInfo, ModelCombination]:
        """偵測結果與依 `translation.allow_restricted` 算出的推薦組合。"""
        info = self.info(settings, fresh=fresh)
        return info, recommend(
            info, allow_restricted=settings.translation.allow_restricted
        )

    def report(self, settings: Settings) -> dict[str, Any]:
        """`hardware.detect`：一律重新偵測。"""
        info, rec = self.recommendation(settings, fresh=True)
        return {"hardware": info.to_dict(), "recommendation": rec.to_dict()}

    def models_list(self, settings: Settings) -> dict[str, Any]:
        """`models.list`：`models_overview()` 加上第一版的翻譯欄位（只增不改）。"""
        info, rec = self.recommendation(settings)
        overview = self._provider.models_overview(info, rec)
        server = info.translation_server
        translation = dict(overview["translation"])
        translation.update(
            base_url=server.base_url,
            installed=None if server.models is None else list(server.models),
            error=server.error,
            presets=legacy_presets(server.models),
        )
        return {**overview, "translation": translation}

    def validate(self, settings: Settings) -> dict[str, Any]:
        """`models.validate`：以目前硬體檢查設定中的模型組合。"""
        info = self.info(settings, fresh=False)
        downloaded = {m.id for m in self._catalog.asr_models() if m.downloaded}
        result = validate_combination(
            combination_from_settings(settings),
            info,
            downloaded_asr=downloaded,
            allow_restricted=settings.translation.allow_restricted,
        )
        return result.to_dict()
