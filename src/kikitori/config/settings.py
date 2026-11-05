"""使用者設定：dataclass、預設值、嚴格驗證與 schema 遷移。

位於 config 模組的資料層：引擎以 `Settings` 建立 pipeline，`settings.get`／`set`
直接收送 `to_dict()` 的 JSON。設定檔是使用者可以手改的外部輸入，所以每個欄位都
驗證型別與範圍，錯誤訊息帶欄位路徑（例如 `media.pause_secs`）。不存 API key
本身，只存讀取它的環境變數名稱。結構見 `docs/specs/pipeline-engine.md`。
"""

import copy
import json
import math
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from pathlib import PurePath
from typing import Any, Literal, cast

from kikitori.pipeline.types import MODES, Mode
from kikitori.translate import DEFAULT_API_KEY_ENV, DEFAULT_BASE_URL, TranslatorConfig

SCHEMA_VERSION = 2
"""目前的設定檔版本；改變結構時加一並在 `MIGRATIONS` 補上轉換。"""

Migration = Callable[[dict[str, Any]], dict[str, Any]]


def _migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """v1 → v2：`asr.compute_type`（兩條路徑共用）拆成 `fast_compute_type`／`final_compute_type`。

    hardware 的推薦對兩條路徑可能給不同精度，所以 v2 分開存；v1 的值原樣複製到兩邊。
    v2 新增的 `*_cpu_threads`、`translation.allow_restricted` 缺少時用預設值，不必遷移。
    `asr` 不是物件時原樣保留，交給驗證報錯（訊息才會帶正確的欄位路徑）。
    """
    result = dict(data)
    asr = result.get("asr")
    if isinstance(asr, Mapping) and "compute_type" in asr:
        section = dict(asr)
        value = section.pop("compute_type")
        section.setdefault("fast_compute_type", value)
        section.setdefault("final_compute_type", value)
        result["asr"] = section
    return result


MIGRATIONS: dict[int, Migration] = {1: _migrate_v1_to_v2}
"""`MIGRATIONS[n]` 把版本 n 的設定 dict 轉成版本 n + 1。"""

AsrDevice = Literal["auto", "cpu", "cuda"]
ASR_DEVICES: tuple[AsrDevice, ...] = ("auto", "cpu", "cuda")
TARGET_LANGUAGES: tuple[str, ...] = ("zh-TW",)
PARTNER_LANGUAGES: tuple[str, ...] = ("ja", "en")

PAUSE_RANGE = (0.1, 5.0)
MAX_RANGE = (1.0, 30.0)
PARTIAL_INTERVAL_RANGE = (200, 5000)
CONTEXT_LINES_RANGE = (0, 10)
MAX_DEVICE_ID_CHARS = 512
MAX_ASR_MODEL_CHARS = 100
MAX_TRANSLATION_MODEL_CHARS = 200
MAX_SUBTITLE_BYTES = 16 * 1024
MAX_SUBTITLE_DEPTH = 5

MAX_CUDA_DIRS = 16
CPU_THREADS_RANGE = (1, 256)
KEEP_WARM_RANGE = (10, 3600)
"""`translation.keep_warm_secs` 的範圍（0 另外允許，表示關閉）。"""
MAX_PATH_CHARS = 1024
COMPUTE_TYPES: tuple[str, ...] = (
    "int8",
    "int8_float32",
    "int8_float16",
    "int8_bfloat16",
    "int16",
    "float16",
    "bfloat16",
    "float32",
)
"""CTranslate2 支援的精度名稱（不含 "auto"／"default"：精度由 asr 模組依裝置決定）。"""

_ASR_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]+$")


class SettingsError(ValueError):
    """設定不合法；訊息以欄位路徑開頭，例如 `media.pause_secs: ...`。"""


@dataclass(frozen=True)
class ModeSettings:
    """單一模式的句尾判斷與擷取裝置。

    Attributes:
        pause_secs: 靜音多久算一句結束。
        max_secs: 句長上限。
        device_id: `list_devices()` 給的裝置 id；None 表示該類型的預設裝置。
    """

    pause_secs: float
    max_secs: float
    device_id: str | None = None


@dataclass(frozen=True)
class AsrSettings:
    """即時與完整路徑的 ASR 模型（asr registry 的 id）與運算設定。

    預設值以「沒有 GPU 的一般電腦」為準（docs/specs/asr.md 的 CPU ≥ 16 GB 等級：
    即時 base、完整 small），hardware 偵測後可用 `settings.apply_recommendation` 覆寫。
    `device` 兩條路徑共用：asr 模組在同一行程內 CUDA 失敗一次就不再嘗試，分開設定
    也無法讓兩條路徑各自成功，而 hardware 的推薦也一律讓兩條路徑用同一個裝置。

    Attributes:
        fast_model: 即時路徑的模型。
        final_model: 完整路徑的模型。
        device: "auto" 有可用的 CUDA 就用，否則 CPU。
        fast_compute_type: 即時路徑的 CTranslate2 精度；None 依實際裝置用 asr 的預設值。
        final_compute_type: 完整路徑的精度；同上。
        fast_cpu_threads: 即時路徑在 CPU 上的執行緒數；None 為自動
            （`hardware.auto_cpu_threads()`：兩條路徑合計不超過實體核心數）。
        final_cpu_threads: 完整路徑的執行緒數；同上。
        cuda_library_dirs: 使用者指定的 cuBLAS 12 目錄（絕對路徑）；kikitori 下載的
            GPU 執行環境不必列在這裡，factory 會自動加上。
    """

    fast_model: str = "base"
    final_model: str = "small"
    device: AsrDevice = "auto"
    fast_compute_type: str | None = None
    final_compute_type: str | None = None
    fast_cpu_threads: int | None = None
    final_cpu_threads: int | None = None
    cuda_library_dirs: tuple[str, ...] = ()


@dataclass(frozen=True)
class TranslationSettings:
    """OpenAI 相容翻譯 API 的位置與模型；`api_key_env` 是環境變數名稱，不是金鑰。

    Attributes:
        allow_restricted: 是否推薦有地區限制授權的翻譯模型（例如 HY-MT 的授權不適用於
            歐盟、英國、南韓）。預設 False：引擎不知道使用者身在何處，用語系猜地區並不
            可靠，而推薦了不能用的授權會讓使用者在不知情下違約；使用者在設定頁確認
            自己不在這些地區後才打開。只影響推薦與驗證警告，不阻止使用者手動選用。
        keep_warm_secs: session 執行中翻譯請求停了這麼多秒，就對本機翻譯伺服器送一次
            預熱請求，讓模型不被卸載（Ollama 預設閒置 5 分鐘就卸載，OpenAI 相容端點
            無法指定 keep_alive）。0 關閉；遠端服務一律不送。
    """

    base_url: str = DEFAULT_BASE_URL
    fast_model: str = "qwen3.5:4b"
    final_model: str = "qwen3.5:4b"
    api_key_env: str = DEFAULT_API_KEY_ENV
    allow_restricted: bool = False
    keep_warm_secs: int = 240


@dataclass(frozen=True)
class PipelineSettings:
    """雙路徑協調器的參數。

    Attributes:
        partial_interval_ms: 句子進行中每隔多少毫秒的新音訊做一次即時辨識。
        no_speech_threshold: `no_speech_probability` 高於此值視為非語音。
        context_lines: 完整路徑帶幾句前文（翻譯 context 與 ASR prompt）。
        asr_prompt_context: 完整路徑是否以前文原文當 ASR prompt。
    """

    partial_interval_ms: int = 700
    no_speech_threshold: float = 0.6
    context_lines: int = 3
    asr_prompt_context: bool = True


def _default_media() -> ModeSettings:
    return ModeSettings(pause_secs=0.6, max_secs=10.0)


def _default_interpret() -> ModeSettings:
    return ModeSettings(pause_secs=0.5, max_secs=8.0)


@dataclass(frozen=True)
class Settings:
    """全部使用者設定。用 `from_dict()` 從外部資料建立（會驗證），`to_dict()` 輸出 JSON。

    `subtitle` 是 UI 的字幕顯示偏好（字型、位置…），引擎不解讀、原樣保存；
    請把它當唯讀資料，修改請用 `merged()`。
    """

    schema_version: int = SCHEMA_VERSION
    mode: Mode = "media"
    target_language: str = "zh-TW"
    partner_language: str = "ja"
    media: ModeSettings = field(default_factory=_default_media)
    interpret: ModeSettings = field(default_factory=_default_interpret)
    asr: AsrSettings = field(default_factory=AsrSettings)
    translation: TranslationSettings = field(default_factory=TranslationSettings)
    pipeline: PipelineSettings = field(default_factory=PipelineSettings)
    subtitle: dict[str, Any] = field(default_factory=dict)

    def mode_settings(self, mode: Mode) -> ModeSettings:
        """回傳指定模式的設定。"""
        if mode == "media":
            return self.media
        if mode == "interpret":
            return self.interpret
        raise ValueError(f"unknown mode {mode!r}")

    def to_dict(self) -> dict[str, Any]:
        """轉成可直接 `json.dumps` 的 dict（深複製，呼叫端可以修改）。"""
        return {
            "schema_version": self.schema_version,
            "mode": self.mode,
            "target_language": self.target_language,
            "partner_language": self.partner_language,
            "media": _mode_to_dict(self.media),
            "interpret": _mode_to_dict(self.interpret),
            "asr": {
                "fast_model": self.asr.fast_model,
                "final_model": self.asr.final_model,
                "device": self.asr.device,
                "fast_compute_type": self.asr.fast_compute_type,
                "final_compute_type": self.asr.final_compute_type,
                "fast_cpu_threads": self.asr.fast_cpu_threads,
                "final_cpu_threads": self.asr.final_cpu_threads,
                "cuda_library_dirs": list(self.asr.cuda_library_dirs),
            },
            "translation": {
                "base_url": self.translation.base_url,
                "fast_model": self.translation.fast_model,
                "final_model": self.translation.final_model,
                "api_key_env": self.translation.api_key_env,
                "allow_restricted": self.translation.allow_restricted,
                "keep_warm_secs": self.translation.keep_warm_secs,
            },
            "pipeline": {
                "partial_interval_ms": self.pipeline.partial_interval_ms,
                "no_speech_threshold": self.pipeline.no_speech_threshold,
                "context_lines": self.pipeline.context_lines,
                "asr_prompt_context": self.pipeline.asr_prompt_context,
            },
            "subtitle": copy.deepcopy(self.subtitle),
        }

    @classmethod
    def from_dict(cls, data: object) -> "Settings":
        """驗證並建立設定；必要時先遷移舊版 schema。

        缺少的欄位用預設值；未知欄位、型別或範圍錯誤一律丟 `SettingsError`。

        Raises:
            SettingsError: 不合法；訊息帶欄位路徑。
        """
        return _parse(migrate(data))

    def merged(self, patch: object) -> "Settings":
        """把部分設定深度合併進目前設定並驗證，回傳新物件（本身不變）。

        物件遞迴合併，其他值整個取代。`patch` 帶 `schema_version` 時必須等於
        目前版本。

        Raises:
            SettingsError: `patch` 不是物件或合併結果不合法。
        """
        if not isinstance(patch, Mapping):
            raise SettingsError(
                f"settings: expected an object, got {type(patch).__name__}"
            )
        if "schema_version" in patch and patch["schema_version"] != SCHEMA_VERSION:
            raise SettingsError(
                f"schema_version: must be {SCHEMA_VERSION}, "
                f"got {patch['schema_version']!r}"
            )
        return Settings.from_dict(_deep_merge(self.to_dict(), patch))


def migrate(
    data: object,
    migrations: Mapping[int, Migration] = MIGRATIONS,
    current: int = SCHEMA_VERSION,
) -> dict[str, Any]:
    """把任一舊版設定 dict 依序轉成 `current` 版本。

    Raises:
        SettingsError: 不是物件、`schema_version` 缺少或不是正整數、版本比程式新
            （不能降版，以免覆寫新版程式的設定），或缺少某一步的遷移。
    """
    if not isinstance(data, Mapping):
        raise SettingsError(f"settings: expected an object, got {type(data).__name__}")
    if "schema_version" not in data:
        raise SettingsError("schema_version: missing")
    version = data["schema_version"]
    if isinstance(version, bool) or not isinstance(version, int) or version < 1:
        raise SettingsError(
            f"schema_version: expected a positive integer, got {version!r}"
        )
    if version > current:
        raise SettingsError(
            f"schema_version: {version} is newer than this program supports "
            f"({current}); update kikitori"
        )
    result = dict(data)
    for step in range(version, current):
        if step not in migrations:
            raise SettingsError(
                f"schema_version: no migration from {step} to {step + 1}"
            )
        result = migrations[step](result)
        result["schema_version"] = step + 1
    return result


def _mode_to_dict(mode: ModeSettings) -> dict[str, Any]:
    return {
        "pause_secs": mode.pause_secs,
        "max_secs": mode.max_secs,
        "device_id": mode.device_id,
    }


def _deep_merge(base: dict[str, Any], patch: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in patch.items():
        if isinstance(value, Mapping) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


# --- 驗證 -------------------------------------------------------------------


def _section(
    data: Mapping[str, Any], key: str, allowed: tuple[str, ...]
) -> dict[str, Any]:
    """取出子物件並擋掉未知欄位；缺少時回傳空 dict（全部用預設值）。"""
    if key not in data:
        return {}
    value = data[key]
    if not isinstance(value, Mapping):
        raise SettingsError(f"{key}: expected an object, got {type(value).__name__}")
    _reject_unknown(value, allowed, prefix=f"{key}.")
    return dict(value)


def _reject_unknown(
    data: Mapping[str, Any], allowed: tuple[str, ...], prefix: str = ""
) -> None:
    for key in data:
        if key not in allowed:
            raise SettingsError(f"{prefix}{key}: unknown setting")


def _number(value: object, path: str, low: float, high: float) -> float:
    if (
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(value)
        or not low <= value <= high
    ):
        raise SettingsError(
            f"{path}: expected a number in [{low}, {high}], got {value!r}"
        )
    return float(value)


def _integer(value: object, path: str, low: int, high: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise SettingsError(
            f"{path}: expected an integer in [{low}, {high}], got {value!r}"
        )
    return value


def _choice(value: object, path: str, choices: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in choices:
        raise SettingsError(f"{path}: expected one of {list(choices)}, got {value!r}")
    return value


def _boolean(value: object, path: str) -> bool:
    if not isinstance(value, bool):
        raise SettingsError(f"{path}: expected true or false, got {value!r}")
    return value


def _parse_mode(
    data: Mapping[str, Any], key: str, default: ModeSettings
) -> ModeSettings:
    section = _section(data, key, ("pause_secs", "max_secs", "device_id"))
    pause = _number(
        section.get("pause_secs", default.pause_secs), f"{key}.pause_secs", *PAUSE_RANGE
    )
    max_secs = _number(
        section.get("max_secs", default.max_secs), f"{key}.max_secs", *MAX_RANGE
    )
    if max_secs <= pause:
        raise SettingsError(
            f"{key}.max_secs: must be greater than pause_secs ({pause}), got {max_secs}"
        )
    device_id = section.get("device_id", default.device_id)
    if device_id is not None and (
        not isinstance(device_id, str)
        or not device_id.strip()
        or len(device_id) > MAX_DEVICE_ID_CHARS
    ):
        raise SettingsError(
            f"{key}.device_id: expected null or a non-empty string of at most "
            f"{MAX_DEVICE_ID_CHARS} characters, got {device_id!r}"
        )
    return ModeSettings(pause_secs=pause, max_secs=max_secs, device_id=device_id)


def _parse_asr(data: Mapping[str, Any]) -> AsrSettings:
    default = AsrSettings()
    section = _section(
        data,
        "asr",
        (
            "fast_model",
            "final_model",
            "device",
            "fast_compute_type",
            "final_compute_type",
            "fast_cpu_threads",
            "final_cpu_threads",
            "cuda_library_dirs",
        ),
    )
    models: dict[str, str] = {}
    for name in ("fast_model", "final_model"):
        value = section.get(name, getattr(default, name))
        if (
            not isinstance(value, str)
            or not 0 < len(value) <= MAX_ASR_MODEL_CHARS
            or not _ASR_MODEL_ID.match(value)
        ):
            raise SettingsError(
                f"asr.{name}: expected a model id (letters, digits, '.', '_', '-'; "
                f"at most {MAX_ASR_MODEL_CHARS} characters), got {value!r}"
            )
        models[name] = value
    device = _choice(section.get("device", default.device), "asr.device", ASR_DEVICES)
    compute_types: dict[str, str | None] = {}
    threads: dict[str, int | None] = {}
    for role in ("fast", "final"):
        name = f"{role}_compute_type"
        value = section.get(name, getattr(default, name))
        compute_types[name] = (
            None if value is None else _choice(value, f"asr.{name}", COMPUTE_TYPES)
        )
        name = f"{role}_cpu_threads"
        value = section.get(name, getattr(default, name))
        threads[name] = (
            None
            if value is None
            else _integer(value, f"asr.{name}", *CPU_THREADS_RANGE)
        )
    dirs = section.get("cuda_library_dirs", list(default.cuda_library_dirs))
    if not isinstance(dirs, list) or len(dirs) > MAX_CUDA_DIRS:
        raise SettingsError(
            f"asr.cuda_library_dirs: expected a list of at most {MAX_CUDA_DIRS} "
            f"paths, got {dirs!r}"
        )
    for i, entry in enumerate(dirs):
        if (
            not isinstance(entry, str)
            or not 0 < len(entry) <= MAX_PATH_CHARS
            or not PurePath(entry).is_absolute()
        ):
            raise SettingsError(
                f"asr.cuda_library_dirs[{i}]: expected an absolute path, got {entry!r}"
            )
    return AsrSettings(
        fast_model=models["fast_model"],
        final_model=models["final_model"],
        device=cast(AsrDevice, device),
        fast_compute_type=compute_types["fast_compute_type"],
        final_compute_type=compute_types["final_compute_type"],
        fast_cpu_threads=threads["fast_cpu_threads"],
        final_cpu_threads=threads["final_cpu_threads"],
        cuda_library_dirs=tuple(dirs),
    )


def _translator_check(path: str, **kwargs: object) -> None:
    # 沿用 translate 模組的驗證，確保設定能直接建立翻譯器。
    try:
        TranslatorConfig(**kwargs)  # type: ignore[arg-type]
    except (TypeError, ValueError) as exc:
        raise SettingsError(f"{path}: {exc}") from exc


def _parse_translation(data: Mapping[str, Any]) -> TranslationSettings:
    default = TranslationSettings()
    section = _section(
        data,
        "translation",
        (
            "base_url",
            "fast_model",
            "final_model",
            "api_key_env",
            "allow_restricted",
            "keep_warm_secs",
        ),
    )
    values: dict[str, str] = {}
    for name in ("base_url", "fast_model", "final_model", "api_key_env"):
        value = section.get(name, getattr(default, name))
        if not isinstance(value, str):
            raise SettingsError(
                f"translation.{name}: expected a string, got {type(value).__name__}"
            )
        values[name] = value
    for name in ("fast_model", "final_model"):
        if len(values[name]) > MAX_TRANSLATION_MODEL_CHARS:
            raise SettingsError(
                f"translation.{name}: at most {MAX_TRANSLATION_MODEL_CHARS} characters"
            )
        _translator_check(f"translation.{name}", model=values[name])
    _translator_check("translation.base_url", model="x", base_url=values["base_url"])
    _translator_check(
        "translation.api_key_env", model="x", api_key_env=values["api_key_env"]
    )
    allow_restricted = _boolean(
        section.get("allow_restricted", default.allow_restricted),
        "translation.allow_restricted",
    )
    keep_warm_secs = _integer(
        section.get("keep_warm_secs", default.keep_warm_secs),
        "translation.keep_warm_secs",
        0,
        KEEP_WARM_RANGE[1],
    )
    if 0 < keep_warm_secs < KEEP_WARM_RANGE[0]:
        raise SettingsError(
            f"translation.keep_warm_secs: must be 0 (off) or "
            f"{KEEP_WARM_RANGE[0]}–{KEEP_WARM_RANGE[1]}, got {keep_warm_secs}"
        )
    return TranslationSettings(
        **values, allow_restricted=allow_restricted, keep_warm_secs=keep_warm_secs
    )


def _parse_pipeline(data: Mapping[str, Any]) -> PipelineSettings:
    default = PipelineSettings()
    section = _section(
        data,
        "pipeline",
        (
            "partial_interval_ms",
            "no_speech_threshold",
            "context_lines",
            "asr_prompt_context",
        ),
    )
    return PipelineSettings(
        partial_interval_ms=_integer(
            section.get("partial_interval_ms", default.partial_interval_ms),
            "pipeline.partial_interval_ms",
            *PARTIAL_INTERVAL_RANGE,
        ),
        no_speech_threshold=_number(
            section.get("no_speech_threshold", default.no_speech_threshold),
            "pipeline.no_speech_threshold",
            0.0,
            1.0,
        ),
        context_lines=_integer(
            section.get("context_lines", default.context_lines),
            "pipeline.context_lines",
            *CONTEXT_LINES_RANGE,
        ),
        asr_prompt_context=_boolean(
            section.get("asr_prompt_context", default.asr_prompt_context),
            "pipeline.asr_prompt_context",
        ),
    )


def _check_json(value: object, path: str, depth: int) -> None:
    if depth > MAX_SUBTITLE_DEPTH:
        raise SettingsError(f"{path}: nested deeper than {MAX_SUBTITLE_DEPTH} levels")
    if value is None or isinstance(value, bool | int | str):
        return
    if isinstance(value, float):
        if not math.isfinite(value):
            raise SettingsError(f"{path}: NaN and Infinity are not allowed")
        return
    if isinstance(value, list):
        for i, item in enumerate(value):
            _check_json(item, f"{path}[{i}]", depth + 1)
        return
    if isinstance(value, Mapping):
        for key, item in value.items():
            if not isinstance(key, str):
                raise SettingsError(f"{path}: keys must be strings, got {key!r}")
            _check_json(item, f"{path}.{key}", depth + 1)
        return
    raise SettingsError(f"{path}: unsupported value type {type(value).__name__}")


def _parse_subtitle(data: Mapping[str, Any]) -> dict[str, Any]:
    value = data.get("subtitle", {})
    if not isinstance(value, Mapping):
        raise SettingsError(f"subtitle: expected an object, got {type(value).__name__}")
    _check_json(value, "subtitle", 1)
    size = len(json.dumps(value, ensure_ascii=False).encode("utf-8"))
    if size > MAX_SUBTITLE_BYTES:
        raise SettingsError(
            f"subtitle: {size} bytes exceeds the limit of {MAX_SUBTITLE_BYTES}"
        )
    return copy.deepcopy(dict(value))


_TOP_LEVEL = (
    "schema_version",
    "mode",
    "target_language",
    "partner_language",
    "media",
    "interpret",
    "asr",
    "translation",
    "pipeline",
    "subtitle",
)


def _parse(data: Mapping[str, Any]) -> Settings:
    _reject_unknown(data, _TOP_LEVEL)
    default = Settings()
    return replace(
        default,
        schema_version=SCHEMA_VERSION,
        mode=cast(Mode, _choice(data.get("mode", default.mode), "mode", MODES)),
        target_language=_choice(
            data.get("target_language", default.target_language),
            "target_language",
            TARGET_LANGUAGES,
        ),
        partner_language=_choice(
            data.get("partner_language", default.partner_language),
            "partner_language",
            PARTNER_LANGUAGES,
        ),
        media=_parse_mode(data, "media", default.media),
        interpret=_parse_mode(data, "interpret", default.interpret),
        asr=_parse_asr(data),
        translation=_parse_translation(data),
        pipeline=_parse_pipeline(data),
        subtitle=_parse_subtitle(data),
    )
