"""依設定建立真實的 pipeline 元件；也是 engine 接上 ASR 實作的唯一接縫。

位於 pipeline 與各實作模組之間：engine 只呼叫這裡的函式，不直接匯入 asr、
Silero 或翻譯器實作。`kikitori.asr` 的實作（CTranslate2 Whisper、下載器）一律在
函式內延遲匯入，所以 pipeline／engine 的測試不會載入 ctranslate2 或任何模型；
ASR 的公開 API 若改變，只需要修改本檔。
"""

import logging
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING, Literal, NamedTuple
from urllib.parse import urlsplit

from kikitori.asr.types import Transcriber, Transcript
from kikitori.config import Settings
from kikitori.config.settings import AUTO_LANGUAGE, AsrDevice
from kikitori.languages import WHISPER_CODES, get_language
from kikitori.pipeline.keep_warm import ActivityTranslator, KeepWarm, WarmUpOnEnable
from kikitori.pipeline.types import (
    Mode,
    PipelineComponents,
    PipelineConfig,
    TranscriptFilter,
)
from kikitori.translate import (
    ContextFallbackTranslator,
    Glossary,
    OpenAICompatibleTranslator,
    TranslationError,
    TranslationRequest,
    Translator,
    TranslatorConfig,
    preset_for_model,
    supports,
)

TranscriberRole = Literal["fast", "final"]

if TYPE_CHECKING:
    from kikitori.asr import DecodeOptions, WhisperTranscriber

logger = logging.getLogger(__name__)

# 即時字幕重試只會更晚，逾時也要比完整路徑短；完整路徑用翻譯模組的預設值。
FAST_TRANSLATE_READ_TIMEOUT = 10.0
FAST_TRANSLATE_RETRIES = 0
# 預熱時翻譯伺服器可能要從磁碟載入模型（實測 qwen3.5:9b 冷啟動 4–10 秒）；不重試，
# 失敗只記 warning，字幕照樣開始。
WARM_UP_READ_TIMEOUT = 60.0
# 預熱只是讓伺服器載入模型，譯文不重要；用原文語言的短句，請求的語言方向才與 session
# 一致（只支援特定方向的模型，例如 Sakura，才不會拒絕）。
_WARM_UP_TEXT = {
    "ja": "はい。",
    "en": "Okay.",
    "zh": "好的。",
    "ko": "네.",
    "es": "Sí.",
    "fr": "Oui.",
    "de": "Ja.",
    "pt": "Sim.",
    "ru": "Да.",
    "it": "Sì.",
    "ar": "نعم.",
    "hi": "हाँ।",
    "id": "Ya.",
    "vi": "Vâng.",
    "th": "ใช่",
    "tr": "Evet.",
    "pl": "Tak.",
    "nl": "Ja.",
    "uk": "Так.",
}


class UnknownModelError(ValueError):
    """設定或請求中的 ASR 模型 id 不在模型清單中。"""


class WarmUpError(TranslationError):
    """預熱／保溫請求失敗；訊息帶模型名稱，`__cause__` 是原本的翻譯錯誤。"""

    def __init__(self, model: str, cause: TranslationError) -> None:
        super().__init__(f"{model}: {cause}")
        self.model = model


@dataclass(frozen=True)
class AsrModelInfo:
    """`models.list` 回報的一個 ASR 模型。"""

    id: str
    size_bytes: int
    license: str
    speed_tier: str
    downloaded: bool
    disk_bytes: int | None = None
    """下載好的模型資料夾實際佔用的位元組數（`<快取>/<id>` 底下的檔案）；沒下載時 None。"""


class AsrLanguages(NamedTuple):
    """ASR 的語言設定，都是 Whisper 語言碼。

    Attributes:
        forced_language: 固定的辨識語言（不做語言偵測）；None 表示自動偵測。
        candidate_languages: 自動偵測的範圍。
    """

    forced_language: str | None
    candidate_languages: tuple[str, ...]


def asr_languages(settings: Settings, mode: Mode) -> AsrLanguages:
    """依模式與設定決定 ASR 的固定辨識語言與偵測範圍。

    - media、指定收聽語言：固定為該語言，不做語言偵測（短句的偵測容易誤判）。
    - media、"auto"：偵測範圍限語言清單涵蓋的 Whisper 語言。
    - interpret：偵測範圍限我的語言與對方語言（靠偵測分辨誰在說話）。
    """
    if mode == "interpret":
        return AsrLanguages(
            forced_language=None,
            candidate_languages=(
                get_language(settings.target_language).whisper_code,
                get_language(settings.partner_language).whisper_code,
            ),
        )
    if settings.source_language == AUTO_LANGUAGE:
        return AsrLanguages(forced_language=None, candidate_languages=WHISPER_CODES)
    forced = get_language(settings.source_language).whisper_code
    return AsrLanguages(forced_language=forced, candidate_languages=(forced,))


def warm_up_text(whisper_code: str) -> str:
    """預熱用的短句；清單外的語言用英文。"""
    return _WARM_UP_TEXT.get(whisper_code, _WARM_UP_TEXT["en"])


def pipeline_config(settings: Settings, mode: Mode) -> PipelineConfig:
    """把使用者設定轉成這個模式的 `PipelineConfig`。"""
    mode_settings = settings.mode_settings(mode)
    return PipelineConfig(
        mode=mode,
        pause_secs=mode_settings.pause_secs,
        max_secs=mode_settings.max_secs,
        target_language=settings.target_language,
        partner_language=settings.partner_language,
        source_language=settings.source_language,
        translation_enabled=settings.translation.enabled,
        partial_interval_ms=settings.pipeline.partial_interval_ms,
        context_lines=settings.pipeline.context_lines,
        asr_prompt_context=settings.pipeline.asr_prompt_context,
    )


def create_transcriber(
    model_id: str,
    *,
    device: AsrDevice,
    role: TranscriberRole,
    candidate_languages: tuple[str, ...] | None,
    forced_language: str | None = None,
    compute_type: str | None = None,
    cpu_threads: int = 0,
    cuda_library_dirs: Sequence[str | Path] = (),
    workers: int = 1,
) -> "WhisperTranscriber":
    """載入 ASR 模型（不下載）。

    Args:
        model_id: asr registry 的模型 id。
        device: "auto"、"cpu" 或 "cuda"。
        role: "fast" 用 greedy 解碼（延遲低）；"final" 用 beam search 與溫度 fallback。
        candidate_languages: 自動偵測的範圍；None 表示不限制。
        forced_language: 固定的辨識語言（不做偵測）；None 表示自動偵測。
        compute_type: CTranslate2 精度；None 用 asr 模組依裝置的預設值。
        cpu_threads: CPU 執行緒數；0 表示 CTranslate2 預設值。
        cuda_library_dirs: cuBLAS 12 所在目錄（見 `asr_cuda_library_dirs()`）。
        workers: 可並行的解碼數；2 表示之後會用 `with_options()` 讓另一條路徑共用。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        FileNotFoundError: 模型尚未下載（請先 `models.download`）。
        RuntimeError: 模型載入失敗或指定的 CUDA 無法使用（同一行程 CUDA 失敗過後
            asr 會要求重啟引擎，訊息原樣成為 status error 的 detail）。
    """
    from kikitori.asr import get_model, load_transcriber

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    return load_transcriber(
        spec,
        device=device,
        compute_type=compute_type,
        cpu_threads=cpu_threads,
        options=decode_options(role, candidate_languages, forced_language),
        cuda_library_dirs=[Path(d) for d in cuda_library_dirs],
        download=False,
        workers=workers,
    )


def decode_options(
    role: TranscriberRole,
    candidate_languages: tuple[str, ...] | None,
    forced_language: str | None,
) -> "DecodeOptions":
    """這條路徑的解碼參數：即時 greedy、完整 beam search，語言範圍依設定。"""
    from kikitori.asr import FAST_DECODE, FINAL_DECODE

    base = FAST_DECODE if role == "fast" else FINAL_DECODE
    return replace(
        base, candidate_languages=candidate_languages, forced_language=forced_language
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
    from kikitori.asr import MODELS, default_cache_dir, model_disk_bytes

    cache = default_cache_dir()
    models: list[AsrModelInfo] = []
    for spec in MODELS.values():
        disk = model_disk_bytes(spec, cache)
        models.append(
            AsrModelInfo(
                id=spec.id,
                size_bytes=spec.total_bytes,
                license=spec.license,
                speed_tier=spec.speed_tier,
                downloaded=disk is not None,
                disk_bytes=disk,
            )
        )
    return models


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


def delete_asr_model(model_id: str, cache_dir: Path | None = None) -> int:
    """刪除已下載的 ASR 模型（含續傳用的 `.part` 與其他版本的殘留），回傳釋放的位元組數。

    先把模型池中這個模型的閒置物件丟掉：它們之後 `load_model()` 會從這個資料夾重新讀檔。
    呼叫端（引擎）負責確認沒有 session 或下載正在使用它。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        FileNotFoundError: 模型沒有下載。
        UnsafeModelPathError（ValueError）: 資料夾是連結或不在模型快取內，拒絕刪除。
        OSError: 刪除失敗（例如檔案被其他程式開著）。
    """
    from kikitori.asr import MODEL_POOL, default_cache_dir, delete_model, get_model

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    cache = default_cache_dir() if cache_dir is None else cache_dir
    evicted = MODEL_POOL.evict_under(cache / spec.id)
    if evicted:
        logger.info(
            "dropped %d pooled %s model object(s) before deleting", evicted, model_id
        )
    return delete_model(spec, cache)


def release_asr_models() -> None:
    """引擎結束時丟掉模型池中所有閒置的 ASR 模型物件。"""
    from kikitori.asr import MODEL_POOL

    MODEL_POOL.clear()


def create_glossary(settings: Settings) -> Glossary:
    """依設定建立術語表（內建表開關＋使用者詞條）；兩條路徑共用同一個。"""
    g = settings.translation.glossary
    return Glossary(entries=g.entries, builtin=g.builtin)


def create_translators(
    settings: Settings,
) -> tuple[ContextFallbackTranslator, ContextFallbackTranslator]:
    """建立即時與完整路徑的翻譯器（同一個 API；即時路徑不重試、逾時較短）。

    兩者共用設定的術語表，並包上前文保險（`ContextFallbackTranslator`：帶前文的譯文像是
    把前文也翻出來時，改不帶前文重翻一次）。
    """
    t = settings.translation
    glossary = create_glossary(settings)
    fast = OpenAICompatibleTranslator(
        TranslatorConfig(
            model=t.fast_model,
            base_url=t.base_url,
            api_key_env=t.api_key_env,
            read_timeout=FAST_TRANSLATE_READ_TIMEOUT,
            max_retries=FAST_TRANSLATE_RETRIES,
        ),
        glossary=glossary,
    )
    final = OpenAICompatibleTranslator(
        TranslatorConfig(
            model=t.final_model, base_url=t.base_url, api_key_env=t.api_key_env
        ),
        glossary=glossary,
    )
    return ContextFallbackTranslator(fast), ContextFallbackTranslator(final)


def build_components(settings: Settings, mode: Mode) -> PipelineComponents:
    """依設定建立一個 session 的全部元件（載入 Silero 與兩個 ASR 模型，可能要數秒）。

    兩條路徑的模型、精度與執行緒數都相同時（例如 th／ar／hi 的推薦兩條都用 large-v3）
    只載入一次：CTranslate2 以兩個 worker 共用同一份權重（VRAM 只算一個模型），完整路徑
    用 `with_options()` 換成自己的解碼參數，兩條路徑仍可並行。其他情況各載入一個。

    Raises:
        UnknownModelError、FileNotFoundError、RuntimeError: 見 `create_transcriber()`。
        ModelIntegrityError: Silero 模型檔雜湊不符。
    """
    from kikitori.vad import SileroVad

    forced_language, candidate_languages = asr_languages(settings, mode)
    fast_translator, final_translator = create_translators(settings)
    asr = settings.asr
    cuda_dirs = asr_cuda_library_dirs(settings)
    fast_threads = resolve_cpu_threads(asr.fast_cpu_threads, asr.device)
    final_threads = resolve_cpu_threads(asr.final_cpu_threads, asr.device)
    shared = (
        asr.fast_model == asr.final_model
        and _resolved_compute_type(asr.fast_compute_type, asr.device)
        == _resolved_compute_type(asr.final_compute_type, asr.device)
        and fast_threads == final_threads
    )
    fast_transcriber = create_transcriber(
        asr.fast_model,
        device=asr.device,
        role="fast",
        candidate_languages=candidate_languages,
        forced_language=forced_language,
        compute_type=asr.fast_compute_type,
        cpu_threads=fast_threads,
        cuda_library_dirs=cuda_dirs,
        workers=2 if shared else 1,
    )
    final_transcriber: Transcriber | None = None
    try:
        if shared:
            final_transcriber = fast_transcriber.with_options(
                decode_options("final", candidate_languages, forced_language)
            )
        else:
            final_transcriber = create_transcriber(
                asr.final_model,
                device=asr.device,
                role="final",
                candidate_languages=candidate_languages,
                forced_language=forced_language,
                compute_type=asr.final_compute_type,
                cpu_threads=final_threads,
                cuda_library_dirs=cuda_dirs,
            )
        return PipelineComponents(
            voice_model=SileroVad(),
            fast_transcriber=fast_transcriber,
            final_transcriber=final_transcriber,
            fast_translator=fast_translator,
            final_translator=final_translator,
            transcript_filter=create_transcript_filter(
                settings.pipeline.no_speech_threshold
            ),
            final_no_speech_reliable=no_speech_reliable(asr.final_model),
        )
    except BaseException:
        # 已載入的模型放回模型池（不然這個 CTranslate2 物件被丟掉，GPU 上漏約 8 MiB），
        # 例外照樣往上拋，由引擎回報 unknown_model／model_not_downloaded／load_error
        for transcriber in dict.fromkeys(
            t for t in (fast_transcriber, final_transcriber) if t is not None
        ):
            close = getattr(transcriber, "close", None)
            if callable(close):
                close()
        raise


def _resolved_compute_type(value: str | None, device: AsrDevice) -> str | None:
    """設定的精度；null 在固定裝置上換成 asr 的預設精度（CUDA float16、CPU int8）。

    與 hardware 的 VRAM 計算（`validate_combination()` 同樣把 null 換成裝置預設）一致，
    「null」和明寫的預設精度才會被當成同一份權重。`auto` 時實際裝置要載入後才知道，原樣比較。
    """
    if value is not None or device == "auto":
        return value
    from kikitori.asr.whisper import default_compute_type

    return default_compute_type(device)


def _is_local_server(settings: Settings) -> bool:
    """翻譯服務在 loopback 位址（本機 Ollama 等）；遠端（可能付費）服務不預熱、不保溫。"""
    from kikitori.hardware.translation_server import is_loopback_host

    return is_loopback_host(urlsplit(settings.translation.base_url).hostname or "")


def translation_needed(settings: Settings, mode: Mode) -> bool:
    """這個 session 會不會用到翻譯模型（不看模型支援哪些語言）。

    只顯示原文，或影片翻譯的收聽語言與字幕語言是同一個 Whisper 語言（只做字形轉換）時
    不需要；口譯一定要（兩個語言的 Whisper 語言必定不同），收聽語言為 "auto" 時也要。
    """
    if not settings.translation.enabled:
        return False
    if mode == "interpret" or settings.source_language == AUTO_LANGUAGE:
        return True
    return (
        get_language(settings.source_language).whisper_code
        != get_language(settings.target_language).whisper_code
    )


def warm_up_request(settings: Settings, mode: Mode) -> TranslationRequest | None:
    """預熱／保溫用的翻譯請求；這個 session 不會用到翻譯模型時回傳 None。

    方向與 session 一致：口譯為對方語言 → 我的語言，影片翻譯為收聽語言 → 字幕語言。
    收聽語言是 "auto" 時挑完整路徑模型支援的第一個語言。關閉翻譯、收聽語言與字幕
    語言是同一個 Whisper 語言（只做字形轉換），或完整路徑模型不支援這個方向（例如
    Sakura 只做日 → 中）時回傳 None：送出去只會被拒絕。
    """
    if not settings.translation.enabled:
        return None
    target = settings.target_language
    preset = preset_for_model(settings.translation.final_model)
    if mode == "interpret":
        sources: Sequence[str] = (get_language(settings.partner_language).whisper_code,)
    elif settings.source_language != AUTO_LANGUAGE:
        sources = (get_language(settings.source_language).whisper_code,)
    else:
        sources = WHISPER_CODES
    # 三種情況共用同一個判斷；`supports()` 也涵蓋同一個 Whisper 語言（不需翻譯）的情況。
    source = next((c for c in sources if supports(preset, c, target)), None)
    if source is None:
        return None
    return TranslationRequest(
        text=warm_up_text(source), source_language=source, target_language=target
    )


def ping_translation_models(
    settings: Settings,
    mode: Mode,
    *,
    make_translator: Callable[[TranslatorConfig], Translator] = (
        OpenAICompatibleTranslator
    ),
) -> None:
    """對翻譯服務送一句很短的翻譯，完整路徑的模型先，即時路徑的模型不同時再送。

    session 開始時的預熱與執行中的保溫共用這個請求（60 秒讀取逾時、不重試、最多 32
    token）。不檢查服務在不在本機，呼叫端負責。`warm_up_request()` 為 None（不需要
    翻譯模型）時什麼都不送。

    Raises:
        TranslationError: 第一個失敗的模型（訊息帶模型名稱）；後面的模型不再送。
    """
    t = settings.translation
    request = warm_up_request(settings, mode)
    if request is None:
        return
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
            raise WarmUpError(model, exc) from exc


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
    只顯示原文（`translation.enabled=false`）或不需要翻譯的語言組合不預熱。
    """
    if warm_up_request(settings, mode) is None or not _is_local_server(settings):
        return
    try:
        ping_translation_models(settings, mode, make_translator=make_translator)
    except WarmUpError as exc:
        logger.warning("cannot warm up translation model %s", exc)


def attach_keep_warm(
    components: PipelineComponents,
    settings: Settings,
    mode: Mode,
    *,
    make_translator: Callable[[TranslatorConfig], Translator] = (
        OpenAICompatibleTranslator
    ),
    clock: Callable[[], float] = time.monotonic,
) -> PipelineComponents:
    """本機翻譯服務：加上保溫工作，並把兩個翻譯器包成會回報活動的版本。

    session 執行中翻譯請求停了 `translation.keep_warm_secs` 秒（例如影片長時間沒有對白），
    就送一次與開場預熱相同的請求，模型才不會被 Ollama 卸載（OpenAI 相容端點不能指定
    `keep_alive`）。遠端服務原樣回傳：不對可能付費的 API 多送請求。保溫在
    `Pipeline.start()` 啟動、pipeline 結束時停止。不需要翻譯的語言組合（例如繁中影片配
    簡中字幕）也原樣回傳。

    設定為 0 只關掉「定期」保溫：翻譯器不包裝，`keep_warm` 換成 `WarmUpOnEnable`，只在
    執行中開啟翻譯時於背景預熱一次（理由同下）。

    只顯示原文（`translation.enabled=false`）時保溫是「不動作」的（`active=False`）：
    不建立執行緒、不送任何請求；執行中開啟翻譯（`Pipeline.set_translation_enabled`）時
    才啟用，並先在背景預熱一次——這個 session 開始時沒有預熱，模型多半尚未載入。翻譯器
    在 session 開始時就得包好，活動才記得到，所以不能等到開啟翻譯時才建立。
    """
    interval = settings.translation.keep_warm_secs
    enabled = settings.translation.enabled
    # 預熱的方向與「開啟翻譯後」的 session 相同；關閉翻譯時 warm_up_request 會回傳 None
    as_enabled = (
        settings if enabled else settings.merged({"translation": {"enabled": True}})
    )
    if warm_up_request(as_enabled, mode) is None or not _is_local_server(settings):
        return components

    def ping() -> None:
        ping_translation_models(as_enabled, mode, make_translator=make_translator)

    if interval == 0:
        return replace(components, keep_warm=WarmUpOnEnable(ping, active=enabled))
    keep_warm = KeepWarm(
        ping,
        float(interval),
        clock=clock,
        active=enabled,
    )
    return replace(
        components,
        fast_translator=ActivityTranslator(components.fast_translator, keep_warm),
        final_translator=ActivityTranslator(components.final_translator, keep_warm),
        keep_warm=keep_warm,
    )


def build_session_components(settings: Settings, mode: Mode) -> PipelineComponents:
    """engine 用的元件建立：先載入 ASR，再預熱翻譯模型，兩者都完成才回傳；本機翻譯服務
    另外加上執行中的保溫（`attach_keep_warm()`）。

    session 在這段期間維持 `loading`，`running` 時第一句就能即時翻譯。刻意不並行：兩個
    行程同時在 GPU 上配置記憶體時，Ollama 載入模型前估算的可用顯示記憶體會過時，
    端到端驗收時 llama-server 因此以 CUDA 錯誤當掉；依序載入時它看到 ASR 載入後真正
    剩下的記憶體，不夠時會自己把部分層放到 CPU。

    Raises:
        同 `build_components()`（失敗時不預熱）。
    """
    components = build_components(settings, mode)
    warm_up_translation(settings, mode)
    return attach_keep_warm(components, settings, mode)
