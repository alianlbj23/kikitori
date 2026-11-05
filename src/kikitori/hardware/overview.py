"""`models.list` 用的總覽：ASR 模型、GPU 執行環境、翻譯模型（含授權標示）。

位於 hardware 模組的彙整層：把 asr registry、GPU 執行環境狀態、翻譯伺服器的已安裝
模型與 translate 預設組合成一份可直接 JSON 化的 dict，讓 UI 的模型選擇頁只需呼叫一次。
會檢查磁碟上的下載狀態，但不連網（翻譯伺服器的模型清單來自 `HardwareInfo`）。
"""

from pathlib import Path
from typing import Any

from kikitori.asr.cache import default_cache_dir
from kikitori.asr.download import is_downloaded
from kikitori.asr.registry import MODELS
from kikitori.hardware.gpu_runtime import gpu_runtime_status
from kikitori.hardware.types import HardwareInfo, ModelCombination
from kikitori.translate.presets import (
    PRESETS,
    normalize_model_name,
    preset_for_model,
)


def _asr_roles(model_id: str, rec: ModelCombination | None) -> list[str]:
    if rec is None:
        return []
    return [
        role
        for role, choice in (("fast", rec.fast_asr), ("final", rec.final_asr))
        if choice.model == model_id
    ]


def _translation_roles(name: str, rec: ModelCombination | None) -> list[str]:
    if rec is None:
        return []
    return [
        role
        for role, model in (
            ("fast", rec.translation_fast),
            ("final", rec.translation_final),
        )
        if model is not None and normalize_model_name(model) == name
    ]


def models_overview(
    info: HardwareInfo,
    recommendation: ModelCombination | None = None,
    *,
    cache_dir: Path | None = None,
    runtime_root: Path | None = None,
) -> dict[str, Any]:
    """組出 `models.list` 的結果。

    Args:
        info: `detect()` 的結果（翻譯伺服器的模型清單取自這裡）。
        recommendation: `recommend()` 的結果；用來標出 `recommended_for`。
        cache_dir: ASR 模型快取；省略時用 `asr.default_cache_dir()`。
        runtime_root: GPU 執行環境根目錄；省略時用預設位置。

    Returns:
        `{"asr": [...], "gpu_runtime": {...}, "translation": {"server": {...}, "models": [...]}}`，
        欄位見 `docs/specs/hardware.md`。
    """
    cache = default_cache_dir() if cache_dir is None else cache_dir
    asr = [
        {
            "id": spec.id,
            "size_bytes": spec.total_bytes,
            "downloaded": is_downloaded(spec, cache),
            "license": spec.license,
            "speed_tier": spec.speed_tier,
            "no_speech_reliable": spec.no_speech_reliable,
            "cpu_ram_mb": spec.approx_cpu_ram_mb,
            "vram_mb": spec.approx_gpu_vram_mb,
            "vram_int8_mb": spec.approx_gpu_vram_int8_mb,
            "recommended_for": _asr_roles(spec.id, recommendation),
        }
        for spec in MODELS.values()
    ]

    runtime = gpu_runtime_status(runtime_root).to_dict()
    runtime["cublas_available"] = info.cublas.available
    runtime["cublas_source"] = info.cublas.source

    server = info.translation_server
    installed = (
        []
        if server.models is None
        else [normalize_model_name(m) for m in server.models]
    )
    names = list(dict.fromkeys([*PRESETS, *installed]))
    translation_models = []
    for name in names:
        preset = preset_for_model(name)
        known = name in PRESETS
        translation_models.append(
            {
                "name": name,
                "label": preset.label,
                "installed": None if server.models is None else name in installed,
                "known": known,
                "recommended_for": _translation_roles(name, recommendation),
                "license": preset.license if known else None,
                "license_url": preset.license_url if known else None,
                "commercial_use": preset.commercial_use if known else None,
                "excluded_regions": list(preset.excluded_regions),
                "approx_vram_gb": preset.approx_vram_gb if known else None,
            }
        )
    return {
        "asr": asr,
        "gpu_runtime": runtime,
        "translation": {
            "server": {
                "base_url": server.base_url,
                "is_local": server.is_local,
                "reachable": server.reachable,
                "error": server.error,
            },
            "models": translation_models,
        },
    }
