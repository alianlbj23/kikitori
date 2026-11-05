"""依設定建立真實的 pipeline 元件；也是 engine 接上 ASR 實作的唯一接縫。

位於 pipeline 與各實作模組之間：engine 只呼叫這裡的函式，不直接匯入 asr、
Silero 或翻譯器實作。`kikitori.asr` 的實作（CTranslate2 Whisper、下載器）一律在
函式內延遲匯入，所以 pipeline／engine 的測試不會載入 ctranslate2 或任何模型；
ASR 的公開 API 若改變，只需要修改本檔。
"""

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from kikitori.asr.types import Transcriber, Transcript
from kikitori.config import Settings
from kikitori.config.settings import AsrDevice
from kikitori.pipeline.types import (
    Mode,
    PipelineComponents,
    PipelineConfig,
    TranscriptFilter,
)
from kikitori.translate import (
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationRequest,
    Translator,
    TranslatorConfig,
)

TranscriberRole = Literal["fast", "final"]

logger = logging.getLogger(__name__)

# 即時字幕重試只會更晚，逾時也要比完整路徑短；完整路徑用翻譯模組的預設值。
FAST_TRANSLATE_READ_TIMEOUT = 10.0
FAST_TRANSLATE_RETRIES = 0
# 預熱時翻譯伺服器可能要從磁碟載入模型（實測 qwen3.5:9b 冷啟動 4–10 秒）；不重試，
# 失敗只記 warning，字幕照樣開始。
WARM_UP_READ_TIMEOUT = 60.0
_WARM_UP_TEXT = {"ja": "はい。", "en": "Okay."}


class UnknownModelError(ValueError):
    """設定或請求中的 ASR 模型 id 不在模型清單中。"""


@dataclass(frozen=True)
class AsrModelInfo:
    """`models.list` 回報的一個 ASR 模型。"""

    id: str
    size_bytes: int
    license: str
    speed_tier: str
    downloaded: bool


def candidate_languages(mode: Mode, partner_language: str) -> tuple[str, ...]:
    """ASR 自動偵測語言時的候選範圍：media 為 ja／en，interpret 為 zh 與對方語言。"""
    if mode == "interpret":
        return ("zh", partner_language)
    return ("ja", "en")


def pipeline_config(settings: Settings, mode: Mode) -> PipelineConfig:
    """把使用者設定轉成這個模式的 `PipelineConfig`。"""
    mode_settings = settings.mode_settings(mode)
    return PipelineConfig(
        mode=mode,
        pause_secs=mode_settings.pause_secs,
        max_secs=mode_settings.max_secs,
        target_language=settings.target_language,
        partner_language=settings.partner_language,
        partial_interval_ms=settings.pipeline.partial_interval_ms,
        context_lines=settings.pipeline.context_lines,
        asr_prompt_context=settings.pipeline.asr_prompt_context,
    )


def create_transcriber(
    model_id: str,
    *,
    device: AsrDevice,
    role: TranscriberRole,
    languages: tuple[str, ...] | None,
    compute_type: str | None = None,
    cpu_threads: int = 0,
    cuda_library_dirs: Sequence[str | Path] = (),
) -> Transcriber:
    """載入 ASR 模型（不下載）。

    Args:
        model_id: asr registry 的模型 id。
        device: "auto"、"cpu" 或 "cuda"。
        role: "fast" 用 greedy 解碼（延遲低）；"final" 用 beam search 與溫度 fallback。
        languages: 自動偵測時的候選語言；None 表示不限制。
        compute_type: CTranslate2 精度；None 用 asr 模組依裝置的預設值。
        cpu_threads: CPU 執行緒數；0 表示 CTranslate2 預設值。
        cuda_library_dirs: cuBLAS 12 所在目錄（見 `asr_cuda_library_dirs()`）。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        FileNotFoundError: 模型尚未下載（請先 `models.download`）。
        RuntimeError: 模型載入失敗或指定的 CUDA 無法使用（同一行程 CUDA 失敗過後
            asr 會要求重啟引擎，訊息原樣成為 status error 的 detail）。
    """
    from kikitori.asr import FAST_DECODE, FINAL_DECODE, get_model, load_transcriber

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    base = FAST_DECODE if role == "fast" else FINAL_DECODE
    options = replace(base, candidate_languages=languages)
    return load_transcriber(
        spec,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        options=options,
        cuda_library_dirs=[Path(d) for d in cuda_library_dirs],
        download=False,
    )


def asr_cuda_library_dirs(settings: Settings) -> list[Path]:
    """要預載 cuBLAS 的目錄：設定指定的目錄在前，再加上已安裝的 GPU 執行環境。

    GPU 執行環境由使用者在 UI 下載（`models.download gpu-runtime`），不寫進設定，
    所以每次建立元件時都重新檢查；這樣安裝完成後的下一個 session 就會用到它。
    檢查失敗（例如磁碟讀取錯誤）只記 warning：最壞情況是 ASR 改用 CPU。
    """
    from kikitori.hardware import installed_runtime_dir

    dirs = [Path(d) for d in settings.asr.cuda_library_dirs]
    try:
        runtime = installed_runtime_dir()
    except OSError as exc:
        logger.warning("cannot check the GPU runtime: %s", exc)
        runtime = None
    if runtime is not None and runtime not in dirs:
        dirs.append(runtime)
    return dirs


def resolve_cpu_threads(value: int | None, device: AsrDevice) -> int:
    """設定的執行緒數 → `load_transcriber(cpu_threads=...)`。

    None（自動）時：`device="cuda"` 用 0（GPU 推論不看這個值），否則用
    `hardware.auto_cpu_threads()`（兩條路徑同時在 CPU 上跑也不超過實體核心數）。
    """
    if value is not None:
        return value
    if device == "cuda":
        return 0
    from kikitori.hardware import auto_cpu_threads

    return auto_cpu_threads()


def no_speech_reliable(model_id: str) -> bool:
    """模型的 `no_speech_probability` 能否用來過濾（large-v3-turbo 恆為 0，不能）。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
    """
    from kikitori.asr import get_model

    try:
        return get_model(model_id).no_speech_reliable
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None


def create_transcript_filter(no_speech_threshold: float) -> TranscriptFilter:
    """asr 的幻覺過濾（空字串、no_speech、低信心、重複、已知幻覺句），門檻取自設定。"""
    from kikitori.asr import FilterConfig, rejection_reason

    config = FilterConfig(no_speech_threshold=no_speech_threshold)

    def reject(transcript: Transcript) -> str | None:
        return rejection_reason(transcript, config)

    return reject


def asr_model_catalog() -> list[AsrModelInfo]:
    """列出可選的 ASR 模型與是否已下載到使用者快取目錄。"""
    from kikitori.asr import MODELS, default_cache_dir, is_downloaded

    cache = default_cache_dir()
    return [
        AsrModelInfo(
            id=spec.id,
            size_bytes=spec.total_bytes,
            license=spec.license,
            speed_tier=spec.speed_tier,
            downloaded=is_downloaded(spec, cache),
        )
        for spec in MODELS.values()
    ]


def download_asr_model(
    model_id: str,
    progress: Callable[[int, int], None],
    cancel: threading.Event,
) -> None:
    """下載並驗證 ASR 模型到使用者快取目錄；已下載時立即回報完成。

    Args:
        model_id: asr registry 的模型 id。
        progress: `(downloaded_bytes, total_bytes)` 回報。
        cancel: 被設定時在下一個區塊前停止。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        ModelDownloadError: 網路、HTTP、磁碟空間、雜湊錯誤或被取消（asr 模組的例外）。
    """
    from kikitori.asr import default_cache_dir, download_model, get_model

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    download_model(spec, default_cache_dir(), progress=progress, cancel=cancel)


def create_translators(
    settings: Settings,
) -> tuple[OpenAICompatibleTranslator, OpenAICompatibleTranslator]:
    """建立即時與完整路徑的翻譯器（同一個 API；即時路徑不重試、逾時較短）。"""
    t = settings.translation
    fast = OpenAICompatibleTranslator(
        TranslatorConfig(
            model=t.fast_model,
            base_url=t.base_url,
            api_key_env=t.api_key_env,
            read_timeout=FAST_TRANSLATE_READ_TIMEOUT,
            max_retries=FAST_TRANSLATE_RETRIES,
        )
    )
    final = OpenAICompatibleTranslator(
        TranslatorConfig(
            model=t.final_model, base_url=t.base_url, api_key_env=t.api_key_env
        )
    )
    return fast, final


def build_components(settings: Settings, mode: Mode) -> PipelineComponents:
    """依設定建立一個 session 的全部元件（載入 Silero 與兩個 ASR 模型，可能要數秒）。

    即時與完整路徑各載入一個 transcriber 實例，即使模型相同也分開，才能並行。

    Raises:
        UnknownModelError、FileNotFoundError、RuntimeError: 見 `create_transcriber()`。
        ModelIntegrityError: Silero 模型檔雜湊不符。
    """
    from kikitori.vad import SileroVad

    languages = candidate_languages(mode, settings.partner_language)
    fast_translator, final_translator = create_translators(settings)
    asr = settings.asr
    cuda_dirs = asr_cuda_library_dirs(settings)
    return PipelineComponents(
        voice_model=SileroVad(),
        fast_transcriber=create_transcriber(
            asr.fast_model,
            device=asr.device,
            role="fast",
            languages=languages,
            compute_type=asr.fast_compute_type,
            cpu_threads=resolve_cpu_threads(asr.fast_cpu_threads, asr.device),
            cuda_library_dirs=cuda_dirs,
        ),
        final_transcriber=create_transcriber(
            asr.final_model,
            device=asr.device,
            role="final",
            languages=languages,
            compute_type=asr.final_compute_type,
            cpu_threads=resolve_cpu_threads(asr.final_cpu_threads, asr.device),
            cuda_library_dirs=cuda_dirs,
        ),
        fast_translator=fast_translator,
        final_translator=final_translator,
        transcript_filter=create_transcript_filter(
            settings.pipeline.no_speech_threshold
        ),
        final_no_speech_reliable=no_speech_reliable(asr.final_model),
    )


def warm_up_translation(
    settings: Settings,
    mode: Mode,
    *,
    make_translator: Callable[[TranslatorConfig], Translator] = (
        OpenAICompatibleTranslator
    ),
) -> None:
    """讓本機翻譯伺服器先載入這個 session 要用的模型（完整路徑的先，再即時路徑）。

    Ollama 閒置 5 分鐘就卸載模型，下一次請求才從磁碟載入。不預熱的話，第一句的即時翻譯
    會逾時（即時路徑只等 10 秒），完整字幕也會整批延後。遠端服務不需要載入，也不該為此
    多花一次請求，所以只預熱 loopback 位址。失敗只記 warning，不中斷 session。
    """
    t = settings.translation
    host = urlsplit(t.base_url).hostname or ""
    from kikitori.hardware.translation_server import is_loopback_host

    if not is_loopback_host(host):
        return
    source = settings.partner_language if mode == "interpret" else "ja"
    request = TranslationRequest(
        text=_WARM_UP_TEXT.get(source, _WARM_UP_TEXT["ja"]),
        source_language=source if source in _WARM_UP_TEXT else "ja",
        target_language=settings.target_language,
    )
    for model in dict.fromkeys((t.final_model, t.fast_model)):
        translator = make_translator(
            TranslatorConfig(
                model=model,
                base_url=t.base_url,
                api_key_env=t.api_key_env,
                read_timeout=WARM_UP_READ_TIMEOUT,
                max_retries=0,
                max_tokens=32,
            )
        )
        try:
            translator.translate(request)
        except TranslationError as exc:
            # 伺服器沒開或模型不在：之後每句翻譯也會失敗並由 pipeline 回報，不必再試下一個。
            logger.warning("cannot warm up translation model %s: %s", model, exc)
            return


def build_session_components(settings: Settings, mode: Mode) -> PipelineComponents:
    """engine 用的元件建立：載入 ASR 的同時預熱翻譯模型，兩者都完成才回傳。

    兩件事互不相干（GPU 上的 ASR 與翻譯伺服器），並行可以少等數秒；session 在這段期間
    維持 `loading`，`running` 時第一句就能即時翻譯。

    Raises:
        同 `build_components()`。
    """
    warm = threading.Thread(
        target=warm_up_translation,
        args=(settings, mode),
        name="translation-warm-up",
        daemon=True,
    )
    warm.start()
    try:
        return build_components(settings, mode)
    finally:
        warm.join(WARM_UP_READ_TIMEOUT + 10)
        if warm.is_alive():
            logger.warning("translation warm-up still running; starting anyway")
