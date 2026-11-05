"""引擎傳給 UI 的結構化訊息：`{"code", "params", "message"}`。

位於 hardware 模組的介面層：推薦（`recommend()`）、組合驗證（`validate_combination()`）
與引擎的預覽方法不再回傳某個語言寫死的句子，而是回傳穩定的 `code` 與基本型別的
`params`，UI 依介面語系查字典組句；`message` 是英文備援，給紀錄檔、CLI 與 UI 不認得的
code 使用。

`CATALOG` 是訊息碼的唯一來源，`docs/specs/hardware.md` 的「訊息碼」表與它逐字一致
（`tests/test_hardware_messages.py` 檢查）；新增或修改 code 時兩邊一起改，既有 code 的
意義與參數不改（UI 的字典依賴它們）。
"""

import math
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Literal

ParamValue = str | int | float
ParamType = Literal["str", "int", "float"]

_TYPE_NAMES: dict[type, ParamType] = {str: "str", int: "int", float: "float"}


@dataclass(frozen=True)
class MessageSpec:
    """一個訊息碼的參數與英文範本。

    Attributes:
        params: 參數名稱（依範本中的順序）；值一律是基本型別，單位寫在名稱裡（`_mb`、`_gb`）。
        types: 參數名稱 → "str"、"int" 或 "float"。
        english: `str.format` 範本（英文備援）。
    """

    params: tuple[str, ...]
    types: Mapping[str, ParamType]
    english: str


def _spec(english: str, **params: type) -> MessageSpec:
    return MessageSpec(
        params=tuple(params),
        types=MappingProxyType({k: _TYPE_NAMES[v] for k, v in params.items()}),
        english=english,
    )


# 依用途分組；組內順序即 hardware.md 表格的順序。`path` 為 "fast_asr"／"final_asr"
# （即時／完整路徑），`field` 為 "translation_fast"／"translation_final"；語言參數是
# 語言清單的語言碼（或 "auto"），UI 依介面語系顯示語言名稱。
CATALOG: Mapping[str, MessageSpec] = MappingProxyType(
    {
        # --- 推薦：ASR（reasons）---
        "asr_gpu": _spec(
            "{gpu_name} ({vram_mb} MiB VRAM): {fast_model} for live subtitles, "
            "{final_model} for final subtitles ({compute_type})",
            gpu_name=str,
            vram_mb=int,
            fast_model=str,
            final_model=str,
            compute_type=str,
        ),
        "asr_gpu_int8_saves_vram": _spec(
            "int8_float16 halves the VRAM used by speech recognition, "
            "leaving more for the translation model"
        ),
        "asr_final_no_speech_unreliable": _spec(
            "{final_model} cannot tell speech from silence (no_speech); "
            "{fast_model}'s no_speech and the voice detector filter non-speech instead",
            final_model=str,
            fast_model=str,
        ),
        "asr_bgm_robust_final": _spec(
            "{final_model} for final subtitles holds up best against background music "
            "and game sound in livestreams (kikitori's Japanese livestream evaluation)",
            final_model=str,
        ),
        "asr_live_model_for_language": _spec(
            "{fast_model} is much less accurate for {language}, so live subtitles also "
            "use {model} (loaded once and shared by both)",
            language=str,
            fast_model=str,
            model=str,
        ),
        "asr_large_v3_remote_translation": _spec(
            "Translation does not run on this computer, so final subtitles use "
            "{final_model} (similar quality, reliable no_speech, about 0.33 s per sentence)",
            final_model=str,
        ),
        "asr_apple_silicon": _spec(
            "Apple Silicon: speech recognition runs on the CPU on macOS; "
            "{fast_model} for live subtitles, {final_model} for final subtitles",
            fast_model=str,
            final_model=str,
        ),
        "asr_apple_silicon_untested": _spec(
            "Apple Silicon latency has not been measured yet; using the same models "
            "as a CPU with 16 GB of RAM or more"
        ),
        "asr_cpu": _spec(
            "Speech recognition runs on the CPU (16 GB of RAM or more): "
            "{fast_model} for live subtitles, {final_model} for final subtitles",
            fast_model=str,
            final_model=str,
        ),
        "asr_cpu_large_models_slow": _spec(
            "medium and larger models need 3.6 s or more per sentence on a CPU, "
            "too slow for live subtitles"
        ),
        "asr_cpu_low": _spec(
            "Lightweight models keep latency and memory low: "
            "{fast_model} for live subtitles, {final_model} for final subtitles",
            fast_model=str,
            final_model=str,
        ),
        "cpu_low_ram": _spec("{ram_gb:.1f} GB of RAM is less than 16 GB", ram_gb=float),
        "cpu_few_cores": _spec(
            "{physical_cores} physical CPU cores (at most {max_cores})",
            physical_cores=int,
            max_cores=int,
        ),
        "cpu_info_incomplete": _spec("The hardware information is incomplete"),
        # --- 推薦：ASR（warnings）---
        "multiple_gpus": _spec(
            "{gpu_count} NVIDIA GPUs found; using {gpu_name}, the one with the most VRAM",
            gpu_count=int,
            gpu_name=str,
        ),
        "cuda_platform_unsupported": _spec(
            "CTranslate2 has no CUDA build for this platform (Windows and Linux x86-64 "
            "only); speech recognition uses the CPU"
        ),
        "cuda_driver_too_old": _spec(
            "NVIDIA driver {driver_version} is too old (CUDA 12 needs {min_driver} or "
            "newer); speech recognition uses the CPU until the driver is updated",
            driver_version=str,
            min_driver=int,
        ),
        "cuda_compute_capability_unknown": _spec(
            "Cannot read the GPU's compute capability (the driver may be too old); "
            "speech recognition uses the CPU"
        ),
        "cuda_compute_capability_unsupported": _spec(
            "{gpu_name} (compute capability {compute_capability}) does not support "
            "CTranslate2's float16/int8 acceleration; speech recognition uses the CPU",
            gpu_name=str,
            compute_capability=str,
        ),
        "gpu_runtime_missing": _spec(
            "cuBLAS is not installed: GPU acceleration needs the GPU runtime download "
            "(about 570 MB); until then speech recognition uses the CPU"
        ),
        "asr_vram_insufficient": _spec(
            "Not enough VRAM for the speech recognition models (about {need_mb} MiB "
            "needed); speech recognition uses the CPU",
            need_mb=int,
        ),
        "memory_unknown": _spec(
            "Cannot read the amount of memory; using the lightest speech recognition models"
        ),
        "asr_low_quality_model": _spec(
            "{model} makes noticeably more recognition errors", model=str
        ),
        "asr_language_quality_low": _spec(
            "{model} recognizes {language} poorly (error rate above 20% on public "
            "benchmarks)",
            model=str,
            language=str,
        ),
        # --- 推薦：翻譯（reasons）---
        "translation_remote": _spec(
            "The translation server is not on this computer ({base_url}); "
            "choose one of its models in Settings",
            base_url=str,
        ),
        "translation_cpu": _spec(
            "No usable GPU: the translation model runs on the CPU, so the smallest "
            "model is chosen (CPU speed not measured)"
        ),
        "translation_budget_vram": _spec(
            "About {budget_gb:.1f} GB of VRAM is left for the translation model after "
            "speech recognition and a 1 GB reserve",
            budget_gb=float,
        ),
        "translation_budget_unified": _spec(
            "About {budget_gb:.1f} GB of unified memory is left for the translation "
            "model after speech recognition and the system reserve",
            budget_gb=float,
        ),
        "translation_same_model": _spec(
            "Live and final translation use the same model: each sentence takes under "
            "0.3 s on a GPU, while switching models in Ollama takes 1.5-18 s"
        ),
        "translation_different_models": _spec(
            "Live and final translation use different models"
        ),
        "translation_better_model_available": _spec(
            "Chose {model} from the installed models; installing {better_model} "
            "gives better quality",
            model=str,
            better_model=str,
        ),
        "translation_not_needed": _spec(
            "{source_language} and {target_language} are the same spoken language: "
            "no translation model is needed, only script conversion",
            source_language=str,
            target_language=str,
        ),
        "source_language_auto": _spec(
            "The listening language is detected automatically: the translation model "
            "is chosen to translate any language into {target_language}",
            target_language=str,
        ),
        # --- 推薦：翻譯（warnings）---
        "translation_server_unreachable": _spec(
            "Cannot connect to the translation server {base_url} (is Ollama running?); "
            "installed models cannot be checked",
            base_url=str,
        ),
        "translation_model_list_unavailable": _spec(
            "Cannot get the translation server's model list: {error}", error=str
        ),
        "low_ram_for_translation": _spec(
            "Less than 8 GB of RAM: running the translation model and speech "
            "recognition together may not fit"
        ),
        "translation_model_not_installed": _spec(
            "The recommended translation model {model} is not installed yet",
            model=str,
        ),
        "translation_model_exceeds_budget": _spec(
            "{model} needs about {model_gb:.1f} GB, more than the {budget_gb:.1f} GB "
            "available; Ollama will run part of it on the CPU, which is slower",
            model=str,
            model_gb=float,
            budget_gb=float,
        ),
        "translation_unsupported": _spec(
            "No recommended translation model supports {source_language} -> "
            "{target_language}",
            source_language=str,
            target_language=str,
        ),
        # --- 組合驗證（errors）---
        "combination_not_object": _spec("The model combination must be an object"),
        "asr_path_not_object": _spec("{path} must be an object", path=str),
        "asr_model_unknown": _spec(
            "{path}: unknown speech recognition model {model} (available: {available})",
            path=str,
            model=str,
            available=str,
        ),
        "asr_device_invalid": _spec(
            "{path}: device must be auto, cpu or cuda", path=str
        ),
        "asr_compute_type_not_string": _spec(
            "{path}: compute_type must be a string or null", path=str
        ),
        "asr_cpu_threads_invalid": _spec(
            "{path}: cpu_threads must be an integer from 0 to 256", path=str
        ),
        "asr_cuda_no_gpu": _spec(
            "{path}: CUDA is selected but no NVIDIA GPU was found", path=str
        ),
        "asr_cuda_platform_unsupported": _spec(
            "{path}: CUDA is selected but CTranslate2 has no CUDA build for this "
            "platform (Windows and Linux x86-64 only)",
            path=str,
        ),
        "asr_cuda_driver_too_old": _spec(
            "{path}: CUDA is selected but NVIDIA driver {driver_version} is too old "
            "(CUDA 12 needs {min_driver} or newer)",
            path=str,
            driver_version=str,
            min_driver=int,
        ),
        "asr_cuda_compute_capability_unknown": _spec(
            "{path}: CUDA is selected but the GPU's compute capability is unknown "
            "(the driver may be too old)",
            path=str,
        ),
        "asr_cuda_compute_capability_unsupported": _spec(
            "{path}: CUDA is selected but {gpu_name} (compute capability "
            "{compute_capability}) does not support CTranslate2's float16/int8 "
            "acceleration",
            path=str,
            gpu_name=str,
            compute_capability=str,
        ),
        "asr_cuda_cublas_missing": _spec(
            "{path}: CUDA is selected but cuBLAS is not installed; download the GPU "
            "runtime first",
            path=str,
        ),
        "asr_compute_type_unknown": _spec(
            "{path}: unknown compute type {compute_type} (available: {available})",
            path=str,
            compute_type=str,
            available=str,
        ),
        "translation_model_name_invalid": _spec(
            "{field} must be a model name of 1-{max_chars} characters or null",
            field=str,
            max_chars=int,
        ),
        # --- 組合驗證（warnings）---
        "asr_auto_uses_cpu": _spec(
            "{path}: auto will use the CPU (the GPU cannot run speech recognition now)",
            path=str,
        ),
        "asr_compute_type_cpu_fallback": _spec(
            "{path}: the CPU does not support {compute_type}; CTranslate2 will use "
            "another type (usually float32, slower)",
            path=str,
            compute_type=str,
        ),
        "asr_compute_type_gpu_fallback": _spec(
            "{path}: compute capability {compute_capability} does not support "
            "{compute_type}; CTranslate2 will use float32 or int8_float32 (slower, "
            "more VRAM)",
            path=str,
            compute_type=str,
            compute_capability=str,
        ),
        "asr_model_slow_on_cpu": _spec(
            "{path}: {model} needs 3.6 s or more per sentence on a CPU; subtitles "
            "will lag noticeably",
            path=str,
            model=str,
        ),
        "asr_model_not_downloaded": _spec(
            "{path}: speech recognition model {model} is not downloaded yet; it is "
            "downloaded before starting",
            path=str,
            model=str,
        ),
        "asr_model_language_unsupported": _spec(
            "{path}: {model} cannot recognize {language}",
            path=str,
            model=str,
            language=str,
        ),
        "asr_fast_larger_than_final": _spec(
            "The live model {fast_model} is larger than the final model "
            "{final_model}: live subtitles may be slower than final ones",
            fast_model=str,
            final_model=str,
        ),
        "asr_no_speech_unavailable": _spec(
            "Neither {fast_model} nor {final_model} can filter non-speech with "
            "no_speech; singing and noise are more likely to be transcribed",
            fast_model=str,
            final_model=str,
        ),
        "translation_model_not_on_server": _spec(
            "The translation server does not have {model}", model=str
        ),
        "translation_model_language_unsupported": _spec(
            "{model} does not support {source_language} -> {target_language}",
            model=str,
            source_language=str,
            target_language=str,
        ),
        "translation_license_non_commercial": _spec(
            "The license of {model} ({license}) forbids commercial use",
            model=str,
            license=str,
        ),
        "translation_license_region_restricted": _spec(
            "The license of {model} ({license}) does not apply in {regions}",
            model=str,
            license=str,
            regions=str,
        ),
        "translation_models_differ": _spec(
            "Live and final translation use different models: the local Ollama "
            "takes 1.5-18 s each time it switches models"
        ),
        "vram_overflow": _spec(
            "Speech recognition and translation need about {need_mb} MiB of VRAM, "
            "more than the {available_mb} MiB available on {gpu_name}; they may be "
            "slow or fail to load",
            need_mb=int,
            available_mb=int,
            gpu_name=str,
        ),
        "asr_ram_over_half": _spec(
            "The speech recognition models on the CPU use more than half of the RAM"
        ),
        # --- 語言預覽（settings.preview_languages）---
        "translation_disabled": _spec(
            "Translation is off: only the original text is shown and no translation "
            "model is needed"
        ),
    }
)


def _check_value(code: str, name: str, kind: ParamType, value: object) -> None:
    # bool 是 int 的子類別，但在訊息裡一定是程式錯誤
    if isinstance(value, bool):
        ok = False
    elif kind == "str":
        ok = isinstance(value, str)
    elif kind == "int":
        ok = isinstance(value, int)
    else:
        ok = isinstance(value, int | float) and math.isfinite(value)
    if not ok:
        raise ValueError(f"{code}: parameter {name} must be {kind}, got {value!r}")


@dataclass(frozen=True)
class Message:
    """一則結構化訊息；用 `message()` 建立（會檢查參數）。

    Attributes:
        code: `CATALOG` 中的訊息碼。
        items: 參數（依 `CATALOG` 的順序）；以 tuple 保存，物件才是真正不可變。
    """

    code: str
    items: tuple[tuple[str, ParamValue], ...] = ()

    @property
    def params(self) -> dict[str, ParamValue]:
        """參數的 dict 複本。"""
        return dict(self.items)

    @property
    def message(self) -> str:
        """英文備援句子。"""
        return CATALOG[self.code].english.format(**self.params)

    def __str__(self) -> str:
        return self.message

    def to_dict(self) -> dict[str, Any]:
        """協定格式：`{"code", "params", "message"}`。"""
        return {"code": self.code, "params": self.params, "message": self.message}


def message(code: str, **params: ParamValue) -> Message:
    """建立訊息並檢查 code 與參數。

    Raises:
        ValueError: code 不在 `CATALOG`、參數多或少、或型別不對（都是程式錯誤）。
    """
    spec = CATALOG.get(code)
    if spec is None:
        raise ValueError(f"unknown message code {code!r}")
    if set(params) != set(spec.params):
        raise ValueError(
            f"{code}: expected parameters {list(spec.params)}, got {sorted(params)}"
        )
    for name in spec.params:
        _check_value(code, name, spec.types[name], params[name])
    return Message(code, tuple((name, params[name]) for name in spec.params))
