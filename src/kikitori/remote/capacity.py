"""運算主機（B）的能力 `HostInfo`：顯示卡、已下載的辨識模型、每個模型跑不跑得動、建議的組合。

位於 remote 模組的伺服器側（docs/specs/compute-capacity.md「二、運算主機的能力」）。B 在
`welcome` 裡送出它，A 連上之前就知道「這台能用什麼模型、會不會超出負荷」。

- `build_host_info()` 是純函式：只讀設定、偵測結果與「哪些模型已下載」，方便窮舉測試。
  記憶體估算沿用 `hardware.recommend` 的 `asr_vram_need_mb`／`asr_ram_need_mb`（同一個模型、
  同精度只算一份權重），建議組合沿用 `hardware.recommend()`，不另寫一份規則。
- `HostCapacity` 把偵測（nvidia-smi、連翻譯伺服器，數秒）快取 30 秒：A 連線時才問，不必每次
  重跑；「哪些模型已下載」每次重查（只看檔案，毫秒等級），A 指定模型時才判斷得準。
- `fit_as_fast`：同一個模型改當即時辨識（搭 B 目前的完整模型）時的負荷，A 的即時模型選單用；
  與 `fit` 同一套公式（`combined_need_mb`）。
- `current.translation`／`translation_models`（compute-capacity「七」）：B 設定的翻譯模型與翻譯
  伺服器已安裝的模型（偵測時 `GET /models` 的結果），給 A 的翻譯模型選單與 B 檢查 A 要改的模型。
  只放 A 收得下的名稱（`remote.protocol.is_portable_model_name`）：A 選了 B 列出的名稱，
  卻被 A 自己的驗證擋下，使用者會無從理解。
- 本模組不匯入 engine：remote.host 在 engine 載入的途中就會被匯入，反過來匯入會循環。
"""

import logging
import threading
import time
from collections.abc import Callable, Collection
from typing import Any, Literal, Protocol
from urllib.parse import urlsplit

from kikitori.asr.registry import MODELS
from kikitori.asr.whisper import default_compute_type
from kikitori.config import Settings
from kikitori.hardware.recommend import (
    DESKTOP_VRAM_RESERVE_MB,
    asr_ram_need_mb,
    asr_vram_need_mb,
    primary_gpu,
    recommend,
)
from kikitori.hardware.translation_server import is_loopback_host
from kikitori.hardware.types import AsrChoice, HardwareInfo, LanguageSelection
from kikitori.remote.protocol import is_portable_model_name
from kikitori.translate.presets import normalize_model_name, preset_for_model

logger = logging.getLogger(__name__)

CACHE_SECS = 30.0
"""偵測結果的重用時間：A 在設定頁連續問幾次（開頁、切角色）不必每次重跑 nvidia-smi。"""
CPU_RAM_FRACTION = 0.7
"""CPU 推論時可用的系統記憶體比例：其餘留給作業系統、瀏覽器與翻譯模型。"""
OK_RATIO = 0.8
"""合計需求 ≤ 可用量的 80% 才算「跑得動」；80–100% 是「可能吃緊」（其他程式一多就不夠）。"""
MAX_GPU_NAME_CHARS = 64
MAX_TRANSLATION_MODELS = 64
"""`translation_models` 最多幾筆（與 A 端驗證的上限相同；Ollama 很少裝這麼多）。"""

Device = Literal["cuda", "cpu"]
Fit = Literal["ok", "tight", "over"]
FITS: tuple[Fit, ...] = ("ok", "tight", "over")

DetectFn = Callable[[Settings], HardwareInfo]
DownloadedFn = Callable[[], Collection[str]]


def _languages(settings: Settings) -> LanguageSelection:
    # 與 engine.hardware_service.language_selection() 相同；不匯入 engine（見模組說明）
    return LanguageSelection(
        mode=settings.mode,
        source_language=settings.source_language,
        target_language=settings.target_language,
        partner_language=settings.partner_language,
    )


def _compute_type(value: str | None, device: Device) -> str:
    """設定的精度；null 用 asr 在該裝置的預設（與 pipeline 載入時相同）。"""
    return value if value is not None else default_compute_type(device)


def resolve_device(settings: Settings, recommended_device: Device | None) -> Device:
    """B 實際會用的裝置：設定指定的；`auto` 時依硬體建議（建議會檢查驅動、cuBLAS 等）。

    偵測失敗（沒有建議）時 `auto` 當作 CPU：不知道能不能用 CUDA 時保守估計。
    """
    device = settings.asr.device
    if device in ("cuda", "cpu"):
        return device
    return recommended_device or "cpu"


def model_need_mb(model: str, device: Device, compute_type: str) -> int:
    """單一模型在這個裝置與精度上的記憶體需求（MiB；CUDA 是 VRAM、CPU 是 RAM）。"""
    choice = AsrChoice(model, device, compute_type)
    if device == "cuda":
        return asr_vram_need_mb([choice])
    return asr_ram_need_mb([choice])


def combined_need_mb(
    settings: Settings,
    device: Device,
    *,
    fast: str | None = None,
    final: str | None = None,
) -> int:
    """即時與完整辨識模型一起載入時的合計需求（MiB）。

    `fast`／`final` 是候選模型，取代 B 設定裡的那一路（None＝用 B 目前的）；精度仍是 B 設定的
    那一路的精度。`fit` 換完整、`fit_as_fast` 換即時，用的是同一個公式。同一個模型、同精度只算
    一份權重（`asr_vram_need_mb` 的規則，與 pipeline 實際載入一致）。
    """
    asr = settings.asr
    choices = [
        AsrChoice(
            asr.fast_model if fast is None else fast,
            device,
            _compute_type(asr.fast_compute_type, device),
        ),
        AsrChoice(
            asr.final_model if final is None else final,
            device,
            _compute_type(asr.final_compute_type, device),
        ),
    ]
    if device == "cuda":
        return asr_vram_need_mb(choices)
    return asr_ram_need_mb(choices)


def local_translation_mb(settings: Settings) -> int:
    """同一台電腦上的翻譯模型（Ollama）佔的顯示卡記憶體（MiB，取權重大小）；不在這台時 0。

    不看 `translation.enabled`：A 開了翻譯時 B 用的是 B 自己設定的翻譯模型，B 自己關著
    翻譯也一樣會載入。即時與完整用同一個模型時只算一次。不認得的模型算 0（沒有大小資料）。
    """
    translation = settings.translation
    host = urlsplit(translation.base_url).hostname or ""
    if not is_loopback_host(host):
        return 0
    models = {
        normalize_model_name(m)
        for m in (translation.fast_model, translation.final_model)
        if m.strip()
    }
    return round(sum(preset_for_model(m).approx_vram_gb for m in models) * 1024)


def available_mb(
    info: HardwareInfo | None, device: Device, translation_mb: int = 0
) -> float | None:
    """可給辨識模型的記憶體（MiB）；不知道時 None。

    CUDA：VRAM 總量扣掉留給桌面與其他程式的 1 GiB（與硬體建議相同），再扣掉同一張卡上的
    翻譯模型（`translation_mb`）。CPU：系統記憶體的 70%。
    """
    if info is None:
        return None
    if device == "cuda":
        gpu = primary_gpu(info)
        if gpu is None:
            return None
        return float(
            max(0, gpu.memory_total_mb - DESKTOP_VRAM_RESERVE_MB - translation_mb)
        )
    total = info.memory.total_bytes
    if total is None:
        return None
    return total / (1024 * 1024) * CPU_RAM_FRACTION


def fit_for(need_mb: int, available: float | None) -> Fit | None:
    """合計需求對照可用量：≤ 80% 跑得動、≤ 100% 可能吃緊、更多超出負荷；不知道可用量時 None。"""
    if available is None:
        return None
    if need_mb <= available * OK_RATIO:
        return "ok"
    if need_mb <= available:
        return "tight"
    return "over"


def installed_translation_models(info: HardwareInfo | None) -> list[str] | None:
    """B 的翻譯伺服器已安裝的模型（偵測時 `GET /models` 的結果）；不知道時 None。

    名稱照伺服器列的（A 選了之後原樣寫進 B 的設定）；A 收不下的名稱略過，最多
    `MAX_TRANSLATION_MODELS` 筆，A 端驗證才不會因為一台裝了很多模型的 B 把清單丟掉。
    """
    if info is None or info.translation_server.models is None:
        return None
    names = [
        name
        for name in dict.fromkeys(info.translation_server.models)
        if is_portable_model_name(name)
    ]
    return names[:MAX_TRANSLATION_MODELS]


def current_translation(settings: Settings) -> dict[str, str] | None:
    """`current.translation`：B 設定的 `{fast, final}` 翻譯模型；A 收不下時整組 None。

    空字串（沒設那一路）照送。不送半組：A 的選單會把缺的那一路當成「沒設」，誤導使用者。
    """
    pair = {
        "fast": settings.translation.fast_model,
        "final": settings.translation.final_model,
    }
    if all(name == "" or is_portable_model_name(name) for name in pair.values()):
        return pair
    return None


def build_host_info(
    settings: Settings,
    info: HardwareInfo | None,
    downloaded: Collection[str],
) -> dict[str, Any]:
    """組出 `HostInfo`（JSON 形狀見 docs/specs/compute-capacity.md「二」）。

    Args:
        settings: B 目前的設定（模型、裝置、精度、語言）。
        info: 硬體偵測結果；偵測失敗時 None（`gpu`／`recommended` 為 null、`fit` 為 null）。
        downloaded: B 已下載的 ASR 模型 id。
    """
    recommendation = (
        None
        if info is None
        else recommend(
            info,
            allow_restricted=settings.translation.allow_restricted,
            languages=_languages(settings),
        )
    )
    device = resolve_device(
        settings, None if recommendation is None else recommendation.final_asr.device
    )
    final_type = _compute_type(settings.asr.final_compute_type, device)
    available = available_mb(
        info, device, local_translation_mb(settings) if device == "cuda" else 0
    )
    gpu = None if info is None else primary_gpu(info)
    have = set(downloaded)
    return {
        "gpu": None
        if gpu is None
        else {
            "name": gpu.name[:MAX_GPU_NAME_CHARS],
            "vram_total_mb": gpu.memory_total_mb,
        },
        "device": device,
        "compute_type": final_type,
        "current": {
            "fast": settings.asr.fast_model,
            "final": settings.asr.final_model,
            "translation": current_translation(settings),
        },
        "recommended": None
        if recommendation is None
        else {
            "fast": recommendation.fast_asr.model,
            "final": recommendation.final_asr.model,
        },
        "models": [
            {
                "id": spec.id,
                "downloaded": spec.id in have,
                "need_mb": model_need_mb(spec.id, device, final_type),
                "fit": fit_for(
                    combined_need_mb(settings, device, final=spec.id), available
                ),
                "fit_as_fast": fit_for(
                    combined_need_mb(settings, device, fast=spec.id), available
                ),
            }
            for spec in MODELS.values()
        ],
        "translation_model": settings.translation.final_model
        if is_portable_model_name(settings.translation.final_model)
        else None,
        "translation_models": installed_translation_models(info),
    }


class HostCapacitySource(Protocol):
    """`RemoteServer` 需要的介面（`HostCapacity` 或測試用的假物件）。"""

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        """目前的 `HostInfo`；`detect=False` 時只用快取的硬體偵測結果。"""

    def downloaded_models(self) -> frozenset[str]:
        """已下載的 ASR 模型 id；讀不到時丟 `OSError`。"""

    def invalidate(self) -> None:
        """丟掉快取的偵測結果（翻譯模型清單可能剛變過）。"""


class HostCapacity:
    """B 的 `HostInfo` 來源：偵測結果快取 `cache_secs` 秒，設定與已下載的模型每次重讀。

    可以從多條連線執行緒同時呼叫；同一時間只跑一次偵測（其他執行緒等它、再用它的結果）。
    """

    def __init__(
        self,
        settings: Callable[[], Settings],
        *,
        detect: DetectFn,
        downloaded: DownloadedFn,
        cache_secs: float = CACHE_SECS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            settings: 讀 B 目前的設定（每次呼叫都重讀：B 的使用者可能剛換了模型）。
            detect: 硬體偵測（nvidia-smi、翻譯伺服器；可能要數秒，內部各自有逾時）。
            downloaded: B 已下載的 ASR 模型 id。
            cache_secs: 偵測結果的重用時間。
            clock: 單調時鐘（測試注入）。
        """
        self._settings = settings
        self._detect = detect
        self._downloaded = downloaded
        self._cache_secs = cache_secs
        self._clock = clock
        self._lock = threading.Lock()
        self._detect_lock = threading.Lock()
        self._cached: tuple[float, HardwareInfo | None] | None = None

    def invalidate(self) -> None:
        """丟掉快取的偵測結果（例如剛下載了模型或裝好 GPU 加速元件）。"""
        with self._lock:
            self._cached = None

    def downloaded_models(self) -> frozenset[str]:
        """B 已下載的 ASR 模型 id（每次重查）。

        Raises:
            OSError: 讀不到模型資料夾。
        """
        return frozenset(self._downloaded())

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        """目前的 `HostInfo`；偵測或讀模型資料夾失敗時照樣回答（對應欄位為 null／未下載）。

        Args:
            detect: 快取過期時是否重新偵測硬體。開字幕的握手傳 False：不讓 A 多等幾秒，
                沒有快取時 `gpu`／`recommended` 為 null、`fit` 為 null。
        """
        settings = self._settings()
        if detect:
            info = self._hardware(settings)
        else:
            hit = self._fresh()
            info = None if hit is None else hit[1]
        try:
            downloaded: Collection[str] = self.downloaded_models()
        except OSError as exc:
            # 只影響「已下載」標示：A 仍看得到顯示卡與建議；指定模型時 B 會再檢查一次
            logger.error("cannot list the downloaded ASR models: %s", exc)
            downloaded = ()
        return build_host_info(settings, info, downloaded)

    def _fresh(self) -> tuple[float, HardwareInfo | None] | None:
        """還有效的快取（`(時間, 偵測結果)`；結果為 None＝上次偵測失敗）；沒有時 None。"""
        with self._lock:
            cached = self._cached
        if cached is not None and self._clock() - cached[0] < self._cache_secs:
            return cached
        return None

    def _hardware(self, settings: Settings) -> HardwareInfo | None:
        hit = self._fresh()
        if hit is not None:
            return hit[1]
        with self._detect_lock:
            hit = self._fresh()  # 等鎖的期間別人可能剛偵測完
            if hit is not None:
                return hit[1]
            try:
                info: HardwareInfo | None = self._detect(settings)
            except Exception:
                # 偵測失敗也要回答 A（gpu／recommended 為 null）；失敗結果同樣快取，
                # 不讓每次 probe 都重跑一次會失敗的偵測
                logger.warning(
                    "hardware detection for the host info failed", exc_info=True
                )
                info = None
            with self._lock:
                self._cached = (self._clock(), info)
            return info


def default_detect(settings: Settings) -> HardwareInfo:
    """真實的硬體偵測（與引擎的 `DefaultHardwareProvider.detect` 相同參數）。"""
    from pathlib import Path

    from kikitori.hardware import detect

    return detect(
        base_url=settings.translation.base_url,
        api_key_env=settings.translation.api_key_env,
        cuda_library_dirs=[Path(d) for d in settings.asr.cuda_library_dirs],
    )


def default_downloaded() -> frozenset[str]:
    """這台電腦的模型快取裡已下載的 ASR 模型（`pipeline.factory` 是 ASR 唯一的接縫）。"""
    from kikitori.pipeline.factory import asr_model_catalog

    return frozenset(m.id for m in asr_model_catalog() if m.downloaded)
