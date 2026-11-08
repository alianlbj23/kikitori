"""引擎的硬體與模型方法：偵測快取、推薦、模型總覽、組合驗證、語言預覽、設定與組合的轉換。

位於 engine 的方法層（server 在背景工作執行緒上呼叫），把 `kikitori.hardware`
的純函式（`recommend()`、`validate_combination()`）與 `HardwareProvider` 的 I/O
（偵測、總覽）接到設定上：設定檔是 config 的格式（`asr.fast_model`…），hardware
用 `ModelCombination` 的格式（`fast_asr.model`…），兩者的對應只寫在這裡。推薦一律
依設定中的語言（`language_selection()`），換語言就會得到新的推薦與新的指紋。
"""

import hashlib
import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from kikitori.config import Settings
from kikitori.engine.services import HardwareProvider, ModelCatalog
from kikitori.hardware import (
    HardwareInfo,
    LanguageSelection,
    Message,
    ModelCombination,
    message,
    recommend,
    validate_combination,
)
from kikitori.pipeline.types import Mode
from kikitori.translate.presets import PRESETS, normalize_model_name

TRANSLATION_BYTES_PER_GB = 1000**3
"""翻譯預設組的 `approx_vram_gb` 是權重檔大小（Ollama 以十進位 GB 顯示），換算成位元組用。"""

DETECTION_CACHE_SECS = 10.0
"""`models.list`／`models.validate`／`settings.apply_recommendation` 重用偵測結果的時間。

UI 開設定頁時通常連續呼叫好幾個方法，不必每次都重跑 nvidia-smi 與連線翻譯伺服器；
但要短到使用者剛開好 Ollama、按下重新整理就看得到。`hardware.detect` 一律重新偵測。
"""


def language_selection(
    settings: Settings, mode: Mode | None = None
) -> LanguageSelection:
    """設定中的語言（與模式）→ 推薦與驗證用的 `LanguageSelection`。

    Args:
        settings: 已驗證的設定（語言碼都是正規化後的清單語言碼）。
        mode: 要推薦的模式；省略時用設定的 `mode`（上次選的模式）。
    """
    return LanguageSelection(
        mode=mode or settings.mode,
        source_language=settings.source_language,
        target_language=settings.target_language,
        partner_language=settings.partner_language,
    )


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


FINGERPRINT_CHARS = 16
"""推薦指紋的長度（SHA-256 前 16 個十六進位字元，64 位元：只用來分辨推薦是否改變）。"""


def recommendation_fingerprint(
    patch: dict[str, Any], languages: LanguageSelection | None = None
) -> str:
    """推薦的穩定識別碼：同一份 patch 與語言（不論鍵的順序）得到同一個字串。

    UI 以它記住「不要再提醒」過哪個推薦（存在設定的 `ui` 物件），推薦改變時指紋跟著
    改變，才能再提醒一次。以 patch 而不是 `ModelCombination` 計算：原因與警告的訊息
    改變但寫入的設定相同時，不必再提醒。語言也算進去：換了語言是新的情境，即使推薦的
    模型相同也要能再提醒（例如新語言的辨識品質較低）。沒有語言時只算 patch。
    """
    data: dict[str, Any] = (
        patch
        if languages is None
        else {"patch": patch, "languages": languages.to_dict()}
    )
    text = json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:FINGERPRINT_CHARS]


def recommendation_preview(settings: Settings, rec: ModelCombination) -> dict[str, Any]:
    """`settings.preview_recommendation`：套用推薦「會」寫入什麼，不存檔。

    `differs` 比較合併 patch 後的設定與目前設定，所以只有推薦涵蓋的欄位（模型、裝置、
    精度、執行緒、翻譯模型）會影響結果；`patch` 與 `settings.apply_recommendation` 的
    `applied` 完全相同（同一個 `recommendation_patch()`）。

    Raises:
        SettingsError: 推薦組合轉成的設定不合法（程式錯誤）。
    """
    patch = recommendation_patch(rec)
    return {
        "patch": patch,
        "recommendation": rec.to_dict(),
        "differs": settings.merged(patch) != settings,
        "fingerprint": recommendation_fingerprint(patch, rec.languages),
    }


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
    """`hardware.detect`、`models.list`、`models.validate` 與推薦相關方法的實作。

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
        """偵測結果與依設定的語言、`translation.allow_restricted` 算出的推薦組合。"""
        info = self.info(settings, fresh=fresh)
        return info, recommend(
            info,
            allow_restricted=settings.translation.allow_restricted,
            languages=language_selection(settings),
        )

    def report(self, settings: Settings) -> dict[str, Any]:
        """`hardware.detect`：一律重新偵測。"""
        info, rec = self.recommendation(settings, fresh=True)
        return {"hardware": info.to_dict(), "recommendation": rec.to_dict()}

    def preview(self, settings: Settings) -> dict[str, Any]:
        """`settings.preview_recommendation`：重用近期的偵測結果，不存檔。"""
        _, rec = self.recommendation(settings)
        return recommendation_preview(settings, rec)

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
            languages=language_selection(settings),
        )
        return result.to_dict()

    def preview_languages(
        self, candidate: Settings, current: Settings
    ) -> dict[str, Any]:
        """`settings.preview_languages`：這組語言的推薦組合與要下載什麼，不存檔、不下載。

        Args:
            candidate: 目前設定合併了要預覽的語言（與模式）之後的設定。
            current: 目前設定（判斷套用後是否不同）。

        Returns:
            見 `docs/specs/pipeline-engine.md` 的 `settings.preview_languages`。
        """
        info, rec = self.recommendation(candidate)
        patch = recommendation_patch(rec)
        merged = candidate.merged(patch)
        asr = self._asr_downloads(rec)
        needed = (
            candidate.translation.enabled and rec.translation_status != "not_needed"
        )
        translation = _translation_downloads(info, rec) if needed else []
        sizes = [entry["download_bytes"] for entry in (*asr, *translation)]
        messages: list[Message] = []
        if not candidate.translation.enabled:
            messages.append(message("translation_disabled"))
        languages = language_selection(candidate)
        return {
            "languages": languages.to_dict(),
            "recommendation": rec.to_dict(),
            "patch": patch,
            "settings": merged.to_dict(),
            "differs": merged != current,
            "fingerprint": recommendation_fingerprint(patch, languages),
            "models": {"asr": asr, "translation": translation},
            "translation_needed": needed,
            "download_bytes": sum(size for size in sizes if size is not None),
            "download_size_known": all(size is not None for size in sizes),
            "messages": [m.to_dict() for m in messages],
        }

    def _asr_downloads(self, rec: ModelCombination) -> list[dict[str, Any]]:
        """推薦的 ASR 模型（同一個模型兩條路徑合併成一筆）與是否需要下載。"""
        catalog = {m.id: m for m in self._catalog.asr_models()}
        entries: dict[str, dict[str, Any]] = {}
        for role, choice in (("fast", rec.fast_asr), ("final", rec.final_asr)):
            entry = entries.get(choice.model)
            if entry is None:
                known = catalog.get(choice.model)
                installed = known is not None and known.downloaded
                size = None if known is None else known.size_bytes
                entry = entries[choice.model] = {
                    "model": choice.model,
                    "roles": [],
                    "installed": installed,
                    "size_bytes": size,
                    "download_bytes": 0 if installed else size,
                }
            entry["roles"].append(role)
        return list(entries.values())


def _translation_downloads(
    info: HardwareInfo, rec: ModelCombination
) -> list[dict[str, Any]]:
    """推薦的翻譯模型與是否需要下載（伺服器連不上時 `installed` 為 null）。

    大小是預設組的權重大小（約略值，`size_approximate`）；不認得的模型為 null。
    """
    server_models = info.translation_server.models
    installed = (
        None
        if server_models is None
        else {normalize_model_name(m) for m in server_models}
    )
    entries: dict[str, dict[str, Any]] = {}
    for role, model in (
        ("fast", rec.translation_fast),
        ("final", rec.translation_final),
    ):
        if model is None:
            continue
        name = normalize_model_name(model)
        entry = entries.get(name)
        if entry is None:
            preset = PRESETS.get(name)
            size = (
                None
                if preset is None
                else round(preset.approx_vram_gb * TRANSLATION_BYTES_PER_GB)
            )
            present = None if installed is None else name in installed
            entry = entries[name] = {
                "model": model,
                "roles": [],
                "installed": present,
                "size_bytes": size,
                "size_approximate": True,
                # 連不上伺服器時不知道有沒有裝：當成可能要下載，大小照列
                "download_bytes": 0 if present else size,
            }
        entry["roles"].append(role)
    return list(entries.values())
