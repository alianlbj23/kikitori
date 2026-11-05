"""硬體偵測與模型組合的資料型別（凍結 dataclass，可直接轉成 JSON）。

位於 hardware 模組的介面層：`detect()` 產生 `HardwareInfo`，`recommend()` 讀它產生
`ModelCombination`，引擎用 `to_dict()` 回傳給 Tauri。型別集中在這裡，讓偵測、推薦、
驗證與測試共用同一份定義。
"""

from dataclasses import dataclass, field
from typing import Any, Literal

from kikitori.hardware.messages import Message
from kikitori.languages import WHISPER_CODES, UnknownLanguageError, get_language

OsName = Literal["windows", "macos", "linux", "other"]
AsrDeviceChoice = Literal["cpu", "cuda"]
CublasSource = Literal["configured", "runtime", "system"]
TranslationStatus = Literal["recommended", "remote", "not_needed", "unsupported"]
"""翻譯模型推薦的結果：

- `recommended`：選了支援這個語言組合的模型。
- `remote`：翻譯伺服器不在本機，不推薦（使用者在設定頁選該服務的模型）。
- `not_needed`：收聽與字幕語言是同一個 Whisper 語言（只做字形轉換）。
- `unsupported`：這個 VRAM 等級的推薦模型都不支援這個語言組合。
"""
SelectionMode = Literal["media", "interpret"]
AUTO_SOURCE = "auto"


@dataclass(frozen=True)
class CpuInfo:
    """CPU 型號與核心數；取不到的欄位為 None。"""

    model: str | None
    physical_cores: int | None
    logical_cores: int | None
    architecture: str


@dataclass(frozen=True)
class MemoryInfo:
    """實體記憶體（位元組）；取不到的欄位為 None。"""

    total_bytes: int | None
    available_bytes: int | None


@dataclass(frozen=True)
class GpuInfo:
    """`nvidia-smi` 回報的一張 NVIDIA GPU。

    Attributes:
        index: `nvidia-smi` 的順序（PCI bus 順序，不一定等於 CUDA 裝置順序）。
        name: 型號，例如 "NVIDIA GeForce RTX 5070"。
        memory_total_mb: VRAM 總量（MiB）。
        memory_used_mb: 偵測當下已用的 VRAM（MiB）；含其他程式與已載入的 Ollama 模型。
        driver_version: 驅動版本，例如 "621.01"。
        compute_capability: 例如 "12.0"；舊驅動不支援查詢時為 None。
    """

    index: int
    name: str
    memory_total_mb: int
    memory_used_mb: int | None
    driver_version: str | None
    compute_capability: str | None

    @property
    def compute_capability_tuple(self) -> tuple[int, int] | None:
        """把 "8.6" 轉成 (8, 6)；格式不對時回傳 None。"""
        if self.compute_capability is None:
            return None
        major, _, minor = self.compute_capability.partition(".")
        if not (major.isdigit() and minor.isdigit()):
            return None
        return int(major), int(minor)

    @property
    def driver_major(self) -> int | None:
        """驅動主版本（"621.01" → 621）。"""
        if self.driver_version is None:
            return None
        major = self.driver_version.split(".", 1)[0]
        return int(major) if major.isdigit() else None


@dataclass(frozen=True)
class CublasInfo:
    """cuBLAS 12 是否找得到（只檢查檔案存在，不實際載入）。

    Attributes:
        supported_platform: 此平台的 CTranslate2 wheel 是否支援 CUDA
            （只有 Windows x86-64 與 Linux x86-64）。
        available: 兩個必要的函式庫都找到了。
        library_dirs: 找到函式庫的目錄；傳給 `load_transcriber(cuda_library_dirs=...)`。
        source: 第一個找到的位置類型："configured"（設定指定）、"runtime"
            （kikitori 下載的 GPU 執行環境）、"system"（CUDA Toolkit、PATH 等）。
    """

    supported_platform: bool
    available: bool
    library_dirs: tuple[str, ...] = ()
    source: CublasSource | None = None


@dataclass(frozen=True)
class TranslationServerInfo:
    """OpenAI 相容翻譯伺服器（預設本機 Ollama）的狀態。

    Attributes:
        base_url: 設定中的 base URL。
        is_local: 主機是本機迴路位址（模型會和 ASR 共用這台電腦的 GPU）。
        reachable: 有收到 HTTP 回應。
        models: `GET {base_url}/models` 列出的模型；無法取得時為 None。
        error: 連不上或回應不合法的原因（不含金鑰）。
    """

    base_url: str
    is_local: bool
    reachable: bool
    models: tuple[str, ...] | None = None
    error: str | None = None


@dataclass(frozen=True)
class HardwareInfo:
    """`detect()` 的結果。

    Attributes:
        os: "windows"、"macos"、"linux" 或 "other"。
        os_version: 給人看的版本字串，例如 "Windows 11 (10.0.26200)"。
        machine: `platform.machine()`，例如 "AMD64"、"x86_64"、"arm64"。
        cpu: CPU 資訊。
        memory: 記憶體資訊。
        gpus: NVIDIA GPU（沒有或偵測失敗時為空）。
        apple_silicon: macOS 且為 Apple Silicon（含在 Rosetta 下執行的情況）。
        cublas: cuBLAS 12 搜尋結果。
        translation_server: 翻譯伺服器狀態。
        errors: 各項偵測失敗的原因（已記 log）；偵測失敗不會讓整個 `detect()` 失敗。
    """

    os: OsName
    os_version: str
    machine: str
    cpu: CpuInfo
    memory: MemoryInfo
    gpus: tuple[GpuInfo, ...]
    apple_silicon: bool
    cublas: CublasInfo
    translation_server: TranslationServerInfo
    errors: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """JSON 化；另保留引擎舊版回應的 `cpu_count`、`ram_bytes` 欄位。"""
        return {
            "os": self.os,
            "os_version": self.os_version,
            "machine": self.machine,
            "cpu": {
                "model": self.cpu.model,
                "physical_cores": self.cpu.physical_cores,
                "logical_cores": self.cpu.logical_cores,
                "architecture": self.cpu.architecture,
            },
            "cpu_count": self.cpu.logical_cores,
            "memory": {
                "total_bytes": self.memory.total_bytes,
                "available_bytes": self.memory.available_bytes,
            },
            "ram_bytes": self.memory.total_bytes,
            "gpus": [
                {
                    "index": g.index,
                    "name": g.name,
                    "memory_total_mb": g.memory_total_mb,
                    "memory_used_mb": g.memory_used_mb,
                    "driver_version": g.driver_version,
                    "compute_capability": g.compute_capability,
                }
                for g in self.gpus
            ],
            "apple_silicon": self.apple_silicon,
            "cublas": {
                "supported_platform": self.cublas.supported_platform,
                "available": self.cublas.available,
                "library_dirs": list(self.cublas.library_dirs),
                "source": self.cublas.source,
            },
            "translation_server": {
                "base_url": self.translation_server.base_url,
                "is_local": self.translation_server.is_local,
                "reachable": self.translation_server.reachable,
                "models": None
                if self.translation_server.models is None
                else list(self.translation_server.models),
                "error": self.translation_server.error,
            },
            "errors": list(self.errors),
        }


@dataclass(frozen=True)
class LanguageSelection:
    """推薦依據的語言選擇（與設定檔的語言欄位相同的語言碼）。

    Attributes:
        mode: "media"（影片翻譯）或 "interpret"（即時口譯）。
        source_language: 影片翻譯的收聽語言或 "auto"；口譯不使用。
        target_language: 字幕語言；口譯時是「我的語言」。
        partner_language: 口譯的對方語言；影片翻譯不使用。

    Raises:
        ValueError: 模式或語言碼不合法（語言碼必須是正規化後的清單語言碼）。
    """

    mode: SelectionMode
    source_language: str = AUTO_SOURCE
    target_language: str = "zh-TW"
    partner_language: str = "ja"

    def __post_init__(self) -> None:
        if self.mode not in ("media", "interpret"):
            raise ValueError(f"mode must be media or interpret, got {self.mode!r}")
        for name in ("source_language", "target_language", "partner_language"):
            value = getattr(self, name)
            if name == "source_language" and value == AUTO_SOURCE:
                continue
            try:
                code = get_language(value).code
            except UnknownLanguageError as exc:
                raise ValueError(f"{name}: {exc}") from None
            if code != value:
                raise ValueError(f"{name}: expected {code!r}, got {value!r}")

    @property
    def spoken_languages(self) -> tuple[str, ...]:
        """會被辨識的語言（清單語言碼）；影片翻譯的 "auto" 為空（任何語言）。"""
        if self.mode == "interpret":
            return (self.target_language, self.partner_language)
        if self.source_language == AUTO_SOURCE:
            return ()
        return (self.source_language,)

    @property
    def asr_whisper_codes(self) -> tuple[str, ...]:
        """ASR 模型必須支援的 Whisper 語言；"auto" 時是清單涵蓋的全部語言。"""
        if not self.spoken_languages:
            return WHISPER_CODES
        return tuple(
            dict.fromkeys(get_language(c).whisper_code for c in self.spoken_languages)
        )

    @property
    def translation_pairs(self) -> tuple[tuple[str, str], ...]:
        """翻譯模型必須支援的（原文, 譯文）方向；只做字形轉換的方向不列入。

        口譯：對方語言 → 我的語言、我的語言 → 對方語言。影片翻譯：收聽語言 → 字幕語言；
        "auto" 時是清單中每個其他 Whisper 語言 → 字幕語言（原文語言要到辨識時才知道）。
        """
        target = get_language(self.target_language)
        if self.mode == "interpret":
            return (
                (self.partner_language, self.target_language),
                (self.target_language, self.partner_language),
            )
        if self.source_language == AUTO_SOURCE:
            return tuple(
                (code, target.code)
                for code in WHISPER_CODES
                if code != target.whisper_code
            )
        if get_language(self.source_language).whisper_code == target.whisper_code:
            return ()
        return ((self.source_language, self.target_language),)

    def to_dict(self) -> dict[str, Any]:
        """JSON 化。"""
        return {
            "mode": self.mode,
            "source_language": self.source_language,
            "target_language": self.target_language,
            "partner_language": self.partner_language,
        }


@dataclass(frozen=True)
class AsrChoice:
    """一條路徑的 ASR 模型與運算設定（對應 `load_transcriber()` 的參數）。

    Attributes:
        model: asr registry 的 id。
        device: "cpu" 或 "cuda"。
        compute_type: CTranslate2 精度，例如 "float16"、"int8_float16"、"int8"。
        cpu_threads: CPU 執行緒數；0 表示 CTranslate2 預設值。
    """

    model: str
    device: AsrDeviceChoice
    compute_type: str
    cpu_threads: int = 0

    def to_dict(self) -> dict[str, Any]:
        """JSON 化。"""
        return {
            "model": self.model,
            "device": self.device,
            "compute_type": self.compute_type,
            "cpu_threads": self.cpu_threads,
        }


@dataclass(frozen=True)
class ModelCombination:
    """`recommend()` 的結果：即時／完整路徑的 ASR 與翻譯模型。

    Attributes:
        tier: 硬體等級 id（見 `docs/specs/hardware.md`）。
        fast_asr: 即時路徑 ASR。
        final_asr: 完整路徑 ASR。
        translation_fast: 即時路徑翻譯模型；翻譯伺服器不在本機時為 None（不推薦）。
        translation_final: 完整路徑翻譯模型；同上。
        translation_budget_gb: 估計可給翻譯模型的 VRAM（GB）；CPU 推論時為 None。
        gpu_runtime_missing: 有可用的 NVIDIA GPU 但缺 cuBLAS；UI 應顯示下載按鈕。
        reasons: 為什麼這樣選（結構化訊息，UI 依介面語系組句）。
        warnings: 需要使用者注意的事（結構化訊息）。
        translation_status: 翻譯模型推薦的結果（見 `TranslationStatus`）。
        languages: 推薦依據的語言選擇；None 表示沒有考慮語言（舊呼叫端）。
    """

    tier: str
    fast_asr: AsrChoice
    final_asr: AsrChoice
    translation_fast: str | None
    translation_final: str | None
    translation_budget_gb: float | None = None
    gpu_runtime_missing: bool = False
    reasons: tuple[Message, ...] = field(default=())
    warnings: tuple[Message, ...] = field(default=())
    translation_status: TranslationStatus = "recommended"
    languages: LanguageSelection | None = None

    def to_dict(self) -> dict[str, Any]:
        """JSON 化。"""
        return {
            "tier": self.tier,
            "fast_asr": self.fast_asr.to_dict(),
            "final_asr": self.final_asr.to_dict(),
            "translation_fast": self.translation_fast,
            "translation_final": self.translation_final,
            "translation_budget_gb": self.translation_budget_gb,
            "gpu_runtime_missing": self.gpu_runtime_missing,
            "reasons": [m.to_dict() for m in self.reasons],
            "warnings": [m.to_dict() for m in self.warnings],
            "translation_status": self.translation_status,
            "languages": None if self.languages is None else self.languages.to_dict(),
        }


@dataclass(frozen=True)
class ValidationResult:
    """手動選擇的組合驗證結果（結構化訊息）；`errors` 為空才可使用。"""

    errors: tuple[Message, ...] = ()
    warnings: tuple[Message, ...] = ()

    @property
    def ok(self) -> bool:
        """沒有錯誤。"""
        return not self.errors

    def to_dict(self) -> dict[str, Any]:
        """JSON 化。"""
        return {
            "ok": self.ok,
            "errors": [m.to_dict() for m in self.errors],
            "warnings": [m.to_dict() for m in self.warnings],
        }
