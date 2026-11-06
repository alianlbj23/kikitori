"""依設定建立真實的 pipeline 元件；也是 engine 接上 ASR 實作的唯一接縫。

位於 pipeline 與各實作模組之間：engine 只呼叫這裡的函式，不直接匯入 asr、
Silero 或翻譯器實作。`kikitori.asr` 的實作（CTranslate2 Whisper、下載器）一律在
函式內延遲匯入，所以 pipeline／engine 的測試不會載入 ctranslate2 或任何模型；
ASR 的公開 API 若改變，只需要修改本檔。
"""

import logging
import queue
import threading
import time
from collections.abc import Callable, Iterable, Sequence
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
    release_components,
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
AsrLoadStep = Literal["asr_fast", "asr_final"]
LoadStep = Literal["asr_fast", "asr_final", "translation"]
"""session 載入的步驟：即時路徑的 ASR、完整路徑的 ASR、預熱本機翻譯模型。"""
LoadProgress = Callable[[LoadStep, int, int], None]
"""載入進度回報 `(剛完成的步驟, 已完成的步驟數, 這次載入的總步驟數)`。"""

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
        # int8 系列精度從預先量化的衍生檔載入（沒有時背景產生），見 asr/derived.py
        derived_cache=True,
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

    刪除前後都把模型池中這個模型的閒置物件丟掉：它們之後 `load_model()` 會從這個資料夾
    重新讀檔。預先量化的衍生檔在 `<cache>/<id>/derived/` 底下，一起刪除；刪除前先停下這個
    模型的背景轉換（轉換途中會寫入那個資料夾）。呼叫端（引擎）負責確認沒有 session（含正在
    釋放元件的）或下載正在使用它。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        FileNotFoundError: 模型沒有下載。
        UnsafeModelPathError（ValueError）: 資料夾是連結或不在模型快取內，拒絕刪除。
        OSError: 刪除失敗（例如檔案被其他程式開著），或背景轉換沒有及時停下。
    """
    from kikitori.asr import MODEL_POOL, default_cache_dir, delete_model, get_model
    from kikitori.asr.derived import DERIVED_CACHE

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    cache = default_cache_dir() if cache_dir is None else cache_dir
    # True＝這個模型已沒有排隊或執行中的背景轉換（之後才能安全地刪資料夾）
    if not DERIVED_CACHE.cancel_model(spec.id):
        # 正在載入模型比對輸出（CTranslate2 的載入不能中斷），稍後再刪
        raise OSError(
            f"ASR model {spec.id} is still being pre-quantized in the background; "
            "try again in a moment"
        )
    evicted = MODEL_POOL.evict_under(cache / spec.id)
    try:
        return delete_model(spec, cache)
    finally:
        # 刪除期間可能有 session 結束、把這個模型的物件放回池中（引擎的「使用中」檢查涵蓋
        # 釋放中的 session，這裡是保險）。刪除失敗時資料夾也可能只剩一部分，一樣要清掉。
        evicted += MODEL_POOL.evict_under(cache / spec.id)
        if evicted:
            logger.info(
                "dropped %d pooled %s model object(s) around deleting",
                evicted,
                model_id,
            )


def release_asr_models() -> None:
    """引擎結束時停止背景的預先量化，並丟掉模型池中所有閒置的 ASR 模型物件。"""
    from kikitori.asr import MODEL_POOL
    from kikitori.asr.derived import DERIVED_CACHE

    # 先停轉換：它驗證時會載入模型；中止的轉換刪掉自己的暫存資料夾
    DERIVED_CACHE.shutdown()
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


def build_components(
    settings: Settings,
    mode: Mode,
    *,
    on_loaded: Callable[[AsrLoadStep], None] | None = None,
) -> PipelineComponents:
    """依設定建立一個 session 的全部元件（載入 Silero 與兩個 ASR 模型，可能要數秒）。

    兩條路徑的模型、精度與執行緒數都相同時（例如 th／ar／hi 的推薦兩條都用 large-v3）
    只載入一次：CTranslate2 以兩個 worker 共用同一份權重（VRAM 只算一個模型），完整路徑
    用 `with_options()` 換成自己的解碼參數，兩條路徑仍可並行。其他情況各載入一個。

    各載入一個時，`device` 為 "cuda" 或 "cpu" 就兩個模型同時載入（各一條執行緒）：
    CTranslate2 建構模型時釋放 GIL，int8 量化在 CPU 上做完才把權重搬到 GPU，所以 GPU 的
    峰值與依序載入相同（兩個模型的權重和）。RTX 5070 實測 turbo＋large-v3（int8_float16）
    依序 10.6 s → 並行 6.9 s，峰值 3235–3479 → 3427–3491 MiB（見 `docs/specs/asr.md`）。
    "auto" 仍依序載入：CUDA 失敗改用 CPU 時，第二個模型要看得到第一個的失敗紀錄
    （`asr.whisper` 同一行程內不再重試 CUDA）。

    任一個載入失敗時，另一個仍在進行的載入不能中斷（CTranslate2 的建構沒有取消機制）：
    等它結束、`close()` 放回模型池，再丟出錯誤（即時路徑的錯誤優先，與依序載入時相同）。

    Args:
        settings: 使用者設定。
        mode: session 模式。
        on_loaded: 每個 ASR 模型載入完成時，以 "asr_fast"／"asr_final" 在**呼叫端的
            執行緒**上呼叫（依完成順序；共用一個模型時兩個依序回報）。丟出例外時已載入的
            模型照樣放回模型池，例外往上拋。

    Raises:
        UnknownModelError、FileNotFoundError、RuntimeError: 見 `create_transcriber()`；
            模型不在清單中或沒下載時，在載入任何模型之前就丟出。
        ModelIntegrityError: Silero 模型檔雜湊不符。
    """
    from kikitori.vad import SileroVad

    forced_language, candidate_languages = asr_languages(settings, mode)
    fast_translator, final_translator = create_translators(settings)
    asr = settings.asr
    # 並行時另一個模型會先載入數秒才發現這個沒下載；先檢查（只讀完成標記與檔案大小）
    for model_id in dict.fromkeys((asr.fast_model, asr.final_model)):
        _require_downloaded(model_id)
    cuda_dirs = asr_cuda_library_dirs(settings)
    fast_threads = resolve_cpu_threads(asr.fast_cpu_threads, asr.device)
    final_threads = resolve_cpu_threads(asr.final_cpu_threads, asr.device)
    shared = (
        asr.fast_model == asr.final_model
        and _resolved_compute_type(asr.fast_compute_type, asr.device)
        == _resolved_compute_type(asr.final_compute_type, asr.device)
        and fast_threads == final_threads
    )
    report = on_loaded if on_loaded is not None else _ignore_step

    def load_fast() -> "WhisperTranscriber":
        return create_transcriber(
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

    def load_final() -> "WhisperTranscriber":
        return create_transcriber(
            asr.final_model,
            device=asr.device,
            role="final",
            candidate_languages=candidate_languages,
            forced_language=forced_language,
            compute_type=asr.final_compute_type,
            cpu_threads=final_threads,
            cuda_library_dirs=cuda_dirs,
        )

    if shared:
        fast_transcriber = load_fast()
        loaded: dict[AsrLoadStep, Transcriber] = {"asr_fast": fast_transcriber}
        try:
            loaded["asr_final"] = fast_transcriber.with_options(
                decode_options("final", candidate_languages, forced_language)
            )
            report("asr_fast")
            report("asr_final")
        except BaseException:
            _close_transcribers(loaded.values())
            raise
    else:
        loaded = _load_transcribers(
            (("asr_fast", load_fast), ("asr_final", load_final)),
            report,
            parallel=asr.device != "auto",
        )
    try:
        return PipelineComponents(
            voice_model=SileroVad(),
            fast_transcriber=loaded["asr_fast"],
            final_transcriber=loaded["asr_final"],
            fast_translator=fast_translator,
            final_translator=final_translator,
            transcript_filter=create_transcript_filter(
                settings.pipeline.no_speech_threshold
            ),
            final_no_speech_reliable=no_speech_reliable(asr.final_model),
        )
    except BaseException:
        _close_transcribers(loaded.values())
        raise


def _ignore_step(step: AsrLoadStep) -> None:
    """沒有指定 `on_loaded` 時的預設回報（不做事）。"""


def _require_downloaded(model_id: str, cache_dir: Path | None = None) -> None:
    """確認 ASR 模型在清單中且已下載（不載入、不重算雜湊）。

    錯誤與 `load_transcriber(download=False)` 相同，引擎照樣回報 unknown_model／
    model_not_downloaded；差別只在不必等另一個模型載入完才回報。

    Raises:
        UnknownModelError: 模型 id 不在清單中。
        FileNotFoundError: 模型尚未下載到 `cache_dir`（省略時為使用者的模型快取）。
    """
    from kikitori.asr import default_cache_dir, get_model, is_downloaded

    try:
        spec = get_model(model_id)
    except KeyError as exc:
        raise UnknownModelError(str(exc.args[0]) if exc.args else model_id) from None
    cache = default_cache_dir() if cache_dir is None else cache_dir
    if not is_downloaded(spec, cache):
        raise FileNotFoundError(f"ASR model {spec.id} is not downloaded to {cache}")


def _close_transcribers(transcribers: Iterable[Transcriber]) -> None:
    """已載入的模型放回模型池（不然這個 CTranslate2 物件被丟掉，GPU 上漏約 8 MiB）。"""
    for transcriber in dict.fromkeys(transcribers):
        close = getattr(transcriber, "close", None)
        if callable(close):
            close()


_LoadResult = tuple[AsrLoadStep, Transcriber | None, BaseException | None]


def _load_transcribers(
    jobs: Sequence[tuple[AsrLoadStep, Callable[[], Transcriber]]],
    report: Callable[[AsrLoadStep], None],
    *,
    parallel: bool,
) -> dict[AsrLoadStep, Transcriber]:
    """執行載入工作（`parallel` 時各一條執行緒），每完成一個就在呼叫端的執行緒上回報。

    回報放在呼叫端的執行緒：引擎的回報會寫 stdout，寫不出去時的例外要由載入流程
    處理，而不是讓某條載入執行緒默默結束。任何失敗（載入或回報）都等所有已開始的
    載入結束、把成功的模型關閉後才丟出；依序載入時失敗後不再開始下一個。

    Raises:
        依 `jobs` 順序第一個失敗的載入例外；載入都成功時是開不了執行緒或回報丟出的例外。
    """
    results: queue.SimpleQueue[_LoadResult] = queue.SimpleQueue()

    def run(step: AsrLoadStep, load: Callable[[], Transcriber]) -> None:
        try:
            results.put((step, load(), None))
        except BaseException as exc:  # noqa: BLE001 - 交給呼叫端的執行緒記錄、清理並往上拋
            results.put((step, None, exc))

    loaded: dict[AsrLoadStep, Transcriber] = {}
    load_errors: dict[AsrLoadStep, BaseException] = {}
    report_error: BaseException | None = None

    def collect() -> None:
        nonlocal report_error
        step, transcriber, error = results.get()
        if error is not None:
            load_errors[step] = error
            return
        assert transcriber is not None
        loaded[step] = transcriber
        if load_errors or report_error is not None:
            return
        try:
            report(step)
        except BaseException as exc:  # noqa: BLE001 - 等載入都結束、清理完才往上拋
            report_error = exc

    start_error: BaseException | None = None
    if parallel:
        started = 0
        for step, load in jobs:
            try:
                threading.Thread(
                    target=run, args=(step, load), name=f"load-{step}", daemon=True
                ).start()
            except BaseException as exc:  # noqa: BLE001 - 先收回已開始的載入再往上拋
                start_error = exc
                break
            started += 1
        for _ in range(started):
            collect()
    else:
        for step, load in jobs:
            if load_errors or report_error is not None:
                break
            run(step, load)
            collect()

    failures = [load_errors[step] for step, _ in jobs if step in load_errors]
    if start_error is not None:
        failures.append(start_error)
    if report_error is not None:
        failures.append(report_error)
    if not failures:
        return loaded
    _close_transcribers(loaded.values())
    for extra in failures[1:]:
        # 只能丟出一個；其他的失敗也記下來（例如兩個模型都因顯示記憶體不足而失敗）
        logger.error(
            "another ASR load step also failed: %s: %s",
            type(extra).__name__,
            extra,
            exc_info=extra,
        )
    raise failures[0]


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


def will_warm_up(settings: Settings, mode: Mode) -> bool:
    """`warm_up_translation()` 會不會送出預熱請求（需要翻譯模型、服務在本機）。"""
    return warm_up_request(settings, mode) is not None and _is_local_server(settings)


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
    if not will_warm_up(settings, mode):
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


def build_session_components(
    settings: Settings,
    mode: Mode,
    *,
    progress: LoadProgress | None = None,
    cancelled: threading.Event | None = None,
) -> PipelineComponents:
    """engine 用的元件建立：先載入 ASR（兩個模型並行，見 `build_components()`），再預熱
    翻譯模型，兩者都完成才回傳；本機翻譯服務另外加上執行中的保溫（`attach_keep_warm()`）。

    session 在這段期間維持 `loading`，`running` 時第一句就能即時翻譯。預熱刻意不與 ASR
    並行：兩個行程同時在 GPU 上配置記憶體時，Ollama 載入模型前估算的可用顯示記憶體會過時，
    端到端驗收時 llama-server 因此以 CUDA 錯誤當掉；依序載入時它看到 ASR 載入後真正
    剩下的記憶體，不夠時會自己把部分層放到 CPU。模型已在 Ollama 上時預熱只要約 0.1 s，
    並行也省不了多少。

    Args:
        settings: 使用者設定。
        mode: session 模式。
        progress: 每完成一步呼叫 `(步驟, 已完成數, 總數)`（在呼叫端的執行緒上）。總數是
            2（兩個 ASR）或 3（另加預熱翻譯模型，只有 `will_warm_up()` 時）。丟出例外時
            已載入的元件放回模型池，例外往上拋。
        cancelled: ASR 載入完時已被設定（session 在載入中被停止）就不預熱：預熱可能要
            等 Ollama 載入模型（最久 60 秒逾時），也會讓 Ollama 為已停止的 session 佔用
            顯示記憶體；下一個 session 也要等這個載入執行緒結束。

    Raises:
        同 `build_components()`（失敗時不預熱）。
    """
    warm = will_warm_up(settings, mode)
    total = 3 if warm else 2
    done = 0

    def report(step: LoadStep) -> None:
        nonlocal done
        done += 1
        if progress is not None:
            progress(step, done, total)

    components = build_components(settings, mode, on_loaded=report)
    try:
        if cancelled is not None and cancelled.is_set():
            logger.info("stopped while loading ASR models; skipping the warm-up")
        elif warm:
            warm_up_translation(settings, mode)
            report("translation")
        return attach_keep_warm(components, settings, mode)
    except BaseException:
        release_components(components)
        raise
