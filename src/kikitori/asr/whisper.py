"""以 CTranslate2 執行 Whisper 的 `Transcriber` 實作。

位於 asr 模組的推論層：驗證音訊 → log-mel 特徵 → CTranslate2 encode → 語言偵測 →
beam search／溫度 fallback → 解碼成文字。pipeline 透過 `load_transcriber()` 取得實例。

解碼流程與參數移植自 faster-whisper v1.2.1（MIT License, Copyright (c) 2023 SYSTRAN）
的 `WhisperModel.generate_with_fallback()`／`get_prompt()`，讓結果與 faster-whisper
一致；不直接用 faster-whisper 的原因見 `docs/specs/asr.md`。
"""

import ctypes
import logging
import os
import sys
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

import numpy as np
import numpy.typing as npt

from kikitori.asr.audio import to_float32, validate_audio
from kikitori.asr.download import (
    ProgressCallback,
    download_model,
    is_downloaded,
    model_dir,
)
from kikitori.asr.features import FRAMES_PER_WINDOW, log_mel_spectrogram, split_windows
from kikitori.asr.filtering import compression_ratio
from kikitori.asr.registry import ModelSpec, get_model
from kikitori.asr.tokenizer import WhisperTokenizer
from kikitori.asr.types import Transcript

logger = logging.getLogger(__name__)

MAX_LENGTH = 448  # Whisper decoder 的最大 token 數（含 prompt）
MAX_PROMPT_TOKENS = MAX_LENGTH // 2 - 1
REQUIRED_MODEL_FILES = ("model.bin", "config.json", "tokenizer.json")
# 不以空白分詞的語言，多個 30 s 視窗的文字直接相連
_NO_SPACE_LANGUAGES = frozenset({"ja", "zh", "yue", "th", "lo", "my", "km", "bo"})

Device = Literal["auto", "cpu", "cuda"]


class GenerationResult(Protocol):
    """`ctranslate2.models.WhisperGenerationResult` 用到的欄位。"""

    sequences_ids: list[list[int]]
    scores: list[float]
    no_speech_prob: float


class WhisperModel(Protocol):
    """`ctranslate2.models.Whisper` 用到的介面；測試以假物件實作。"""

    @property
    def is_multilingual(self) -> bool: ...

    @property
    def n_mels(self) -> int: ...

    def encode(self, features: Any, to_cpu: bool = False) -> Any: ...

    def detect_language(self, features: Any) -> list[list[tuple[str, float]]]: ...

    def generate(
        self, features: Any, prompts: list[list[int]], **kwargs: Any
    ) -> list[Any]: ...


@dataclass(frozen=True)
class DecodeOptions:
    """解碼參數。預設值同 faster-whisper，只有 `temperatures` 預設不 fallback。

    Attributes:
        beam_size: beam search 寬度；1 為 greedy（即時路徑用）。
        patience: beam search 的 patience。
        length_penalty: 長度懲罰指數。
        repetition_penalty: 重複懲罰。
        no_repeat_ngram_size: 禁止重複的 n-gram 長度（0 為不限制）。
        temperatures: 依序嘗試的溫度；0 為 beam search，> 0 為取樣。
        best_of: 取樣時的候選數。
        compression_ratio_threshold: 壓縮比高於此值就用下一個溫度重試。
        log_prob_threshold: 平均對數機率低於此值就用下一個溫度重試。
        no_speech_threshold: no_speech 高於此值且 logprob 過低時視為靜音，不重試。
        candidate_languages: 自動偵測時只在這些語言中選（例如口譯模式 ("zh", "ja")）。
        max_new_tokens: 最多產生的 token 數；None 表示到 448 為止。
    """

    beam_size: int = 5
    patience: float = 1.0
    length_penalty: float = 1.0
    repetition_penalty: float = 1.0
    no_repeat_ngram_size: int = 0
    temperatures: tuple[float, ...] = (0.0,)
    best_of: int = 5
    compression_ratio_threshold: float | None = 2.4
    log_prob_threshold: float | None = -1.0
    no_speech_threshold: float | None = 0.6
    candidate_languages: tuple[str, ...] | None = None
    max_new_tokens: int | None = None

    def __post_init__(self) -> None:
        if self.beam_size < 1 or self.best_of < 1:
            raise ValueError("beam_size and best_of must be >= 1")
        if not self.temperatures or any(t < 0 for t in self.temperatures):
            raise ValueError("temperatures must be a non-empty tuple of values >= 0")
        if self.candidate_languages is not None and not self.candidate_languages:
            raise ValueError("candidate_languages must be None or non-empty")
        if self.max_new_tokens is not None and self.max_new_tokens < 1:
            raise ValueError("max_new_tokens must be >= 1")


FAST_DECODE = DecodeOptions(beam_size=1, max_new_tokens=128)
"""即時路徑：greedy、不 fallback，延遲最低。

最多 128 個 token（10 s 日文約 70–100 個）：greedy 偶爾陷入「と、と、と…」迴圈，
上限讓這種情況的延遲不會暴增；完整路徑會用 beam search 重新辨識。
"""

FINAL_DECODE = DecodeOptions(beam_size=5, temperatures=(0.0, 0.2, 0.4))
"""完整路徑：beam 5，結果重複或信心過低時用較高溫度重試。"""


@dataclass(frozen=True)
class _Decoded:
    tokens: list[int]
    text: str
    avg_logprob: float
    no_speech_prob: float


def _default_storage(array: npt.NDArray[np.float32]) -> Any:
    import ctranslate2

    return ctranslate2.StorageView.from_array(array)


class WhisperTranscriber:
    """CTranslate2 Whisper 的 `Transcriber` 實作。

    同一個實例的呼叫以 lock 串行化；即時與完整路徑要並行就各建一個實例。
    """

    def __init__(
        self,
        model: WhisperModel,
        tokenizer: WhisperTokenizer,
        options: DecodeOptions | None = None,
        *,
        storage: Callable[[npt.NDArray[np.float32]], Any] = _default_storage,
        name: str = "whisper",
        device: str = "cpu",
        compute_type: str = "default",
    ) -> None:
        """包裝已載入的模型。一般用 `load_transcriber()` 建立。

        Args:
            model: `ctranslate2.models.Whisper` 或相容物件。
            tokenizer: 與模型同目錄的 `tokenizer.json`。
            options: 解碼參數；省略時用 `DecodeOptions()`。
            storage: numpy → 模型輸入的轉換（預設 `ctranslate2.StorageView.from_array`）。
            name: 模型名稱（log 用）。
            device: 實際使用的裝置（"cpu" 或 "cuda"）。
            compute_type: 實際使用的精度。
        """
        self._model = model
        self._tokenizer = tokenizer
        self.options = DecodeOptions() if options is None else options
        self._storage = storage
        self._lock = threading.Lock()
        self.name = name
        self.device = device
        self.compute_type = compute_type
        if self.options.candidate_languages is not None:
            for code in self.options.candidate_languages:
                self._check_language(code)

    @property
    def languages(self) -> tuple[str, ...]:
        """模型支援的語言代碼。"""
        return (
            self._tokenizer.language_codes if self._model.is_multilingual else ("en",)
        )

    def _check_language(self, language: str) -> None:
        if language not in self.languages:
            raise ValueError(
                f"model {self.name} does not support language {language!r}"
            )

    def warmup(self) -> None:
        """用全零視窗跑一次 encode 與一步 greedy 解碼。

        提早載入 GPU 函式庫（cuBLAS 缺少時在這裡就失敗，而不是第一句才失敗），
        並讓第一句不必負擔 CUDA kernel 初始化的延遲（本機實測約 0.8 s）。
        """
        zeros = np.zeros((1, self._model.n_mels, FRAMES_PER_WINDOW), dtype=np.float32)
        prompt = self._build_prompt("en", [])
        with self._lock:
            encoded = self._model.encode(self._storage(zeros), to_cpu=False)
            self._model.generate(
                encoded, [prompt], beam_size=1, max_length=len(prompt) + 1
            )

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        """辨識一句音訊，見 `kikitori.asr.types.Transcriber`。

        Raises:
            TypeError: 音訊不是 int16 numpy 陣列。
            ValueError: 音訊形狀不對或為空，或語言不支援。
            RuntimeError: CTranslate2 推論失敗（原樣往上拋）。
        """
        samples = validate_audio(audio)
        if language is not None:
            self._check_language(language)
        prompt_tokens = self._prompt_tokens(prompt)
        features = log_mel_spectrogram(to_float32(samples), self._model.n_mels)
        windows = split_windows(features)

        with self._lock:
            decoded: list[_Decoded] = []
            language_probability = 1.0
            for index, window in enumerate(windows):
                encoded = self._model.encode(
                    self._storage(window[np.newaxis]), to_cpu=False
                )
                if index == 0 and language is None:
                    language, language_probability = self._detect_language(encoded)
                assert language is not None
                prompt_ids = self._build_prompt(language, prompt_tokens)
                decoded.append(self._generate_with_fallback(encoded, prompt_ids))

        assert language is not None
        separator = "" if language in _NO_SPACE_LANGUAGES else " "
        text = separator.join(d.text for d in decoded if d.text).strip()
        weights = [len(d.tokens) + 1 for d in decoded]
        avg_logprob = sum(
            d.avg_logprob * w for d, w in zip(decoded, weights, strict=True)
        ) / sum(weights)
        return Transcript(
            text=text,
            language=language,
            language_probability=float(language_probability),
            # 任一視窗有語音就算有語音
            no_speech_probability=float(min(d.no_speech_prob for d in decoded)),
            avg_logprob=float(avg_logprob),
        )

    def _prompt_tokens(self, prompt: str | None) -> list[int]:
        if prompt is None or not prompt.strip():
            return []
        tokens = self._tokenizer.encode(" " + prompt.strip())
        return tokens[-MAX_PROMPT_TOKENS:]

    def _build_prompt(self, language: str, prompt_tokens: list[int]) -> list[int]:
        tok = self._tokenizer
        prompt: list[int] = []
        if prompt_tokens:
            prompt += [tok.sot_prev, *prompt_tokens]
        prompt.append(tok.sot)
        if self._model.is_multilingual:
            prompt += [tok.language_token(language), tok.transcribe]
        prompt.append(tok.no_timestamps)
        return prompt

    def _detect_language(self, encoded: Any) -> tuple[str, float]:
        if not self._model.is_multilingual:
            return "en", 1.0
        ranked = self._model.detect_language(encoded)[0]
        probs = {token[2:-2]: float(p) for token, p in ranked}
        candidates = self.options.candidate_languages
        if candidates is None:
            best = max(probs, key=probs.__getitem__)
            return best, probs[best]
        # 只在候選中選，機率在候選間重新正規化
        subset = {code: probs.get(code, 0.0) for code in candidates}
        total = sum(subset.values())
        best = max(subset, key=subset.__getitem__)
        return best, (subset[best] / total if total > 0 else 0.0)

    def _suppress_tokens(self) -> list[int]:
        tok = self._tokenizer
        # -1 = config.json 的 suppress_ids（非語音符號）；另外禁止輸出任務／起始類特殊 token，
        # 同 faster-whisper 的 get_suppressed_tokens
        specials = {
            tok.transcribe,
            tok.translate,
            tok.sot,
            tok.sot_prev,
            tok.sot_lm,
            tok.no_speech,
        }
        return [-1, *sorted(specials)]

    def _generate_with_fallback(self, encoded: Any, prompt: list[int]) -> _Decoded:
        opts = self.options
        max_length = MAX_LENGTH
        if opts.max_new_tokens is not None:
            max_length = len(prompt) + opts.max_new_tokens
            if max_length > MAX_LENGTH:
                raise ValueError(
                    f"prompt ({len(prompt)} tokens) + max_new_tokens exceeds {MAX_LENGTH}"
                )

        attempts: list[_Decoded] = []
        not_repetitive: list[_Decoded] = []
        for temperature in opts.temperatures:
            if temperature > 0:
                kwargs: dict[str, Any] = {
                    "beam_size": 1,
                    "num_hypotheses": opts.best_of,
                    "sampling_topk": 0,
                    "sampling_temperature": temperature,
                }
            else:
                kwargs = {"beam_size": opts.beam_size, "patience": opts.patience}
            result = self._model.generate(
                encoded,
                [prompt],
                length_penalty=opts.length_penalty,
                repetition_penalty=opts.repetition_penalty,
                no_repeat_ngram_size=opts.no_repeat_ngram_size,
                max_length=max_length,
                return_scores=True,
                return_no_speech_prob=True,
                suppress_blank=True,
                suppress_tokens=self._suppress_tokens(),
                **kwargs,
            )[0]
            tokens = list(result.sequences_ids[0])
            # CTranslate2 回傳的是除以 len^length_penalty 後的分數，還原成平均對數機率
            seq_len = len(tokens)
            cum_logprob = result.scores[0] * (seq_len**opts.length_penalty)
            decoded = _Decoded(
                tokens=tokens,
                text=self._tokenizer.decode(tokens).strip(),
                avg_logprob=cum_logprob / (seq_len + 1),
                no_speech_prob=float(result.no_speech_prob),
            )
            attempts.append(decoded)

            needs_fallback = False
            if opts.compression_ratio_threshold is not None:
                if compression_ratio(decoded.text) > opts.compression_ratio_threshold:
                    needs_fallback = True
                else:
                    not_repetitive.append(decoded)
            low_logprob = (
                opts.log_prob_threshold is not None
                and decoded.avg_logprob < opts.log_prob_threshold
            )
            if low_logprob:
                needs_fallback = True
            if (
                low_logprob
                and opts.no_speech_threshold is not None
                and decoded.no_speech_prob > opts.no_speech_threshold
            ):
                needs_fallback = False  # 靜音：重試也不會更好
            if not needs_fallback:
                return decoded
            logger.debug("decode at temperature %.1f needs fallback", temperature)
        # 全部都不理想時，挑不重複者中 logprob 最高的
        return max(not_repetitive or attempts, key=lambda d: d.avg_logprob)


def preload_cuda_libraries(directories: Sequence[Path]) -> list[Path]:
    """從指定目錄預先載入 cuBLAS，讓 CTranslate2 之後以檔名載入時直接取用。

    CTranslate2 的 wheel 執行時才以檔名載入 cuBLAS。Linux 的動態載入器只在行程
    啟動時讀 `LD_LIBRARY_PATH`，執行中改環境變數無效，所以改成先用完整路徑載入。

    Returns:
        已載入的檔案路徑。

    Raises:
        FileNotFoundError: 目錄中找不到 cuBLAS。
        OSError: 檔案存在但無法載入（例如版本或架構不符）。
    """
    if sys.platform == "win32":
        names = ("cublasLt64_12.dll", "cublas64_12.dll")
    elif sys.platform == "darwin":
        return []
    else:
        names = ("libcublasLt.so.12", "libcublas.so.12")
    loaded: list[Path] = []
    for name in names:
        candidates = [Path(d) / name for d in directories]
        path = next((p for p in candidates if p.is_file()), None)
        if path is None:
            raise FileNotFoundError(
                f"{name} not found in {', '.join(map(str, directories))}"
            )
        mode = getattr(ctypes, "RTLD_GLOBAL", 0)
        ctypes.CDLL(os.fspath(path), mode=mode)
        loaded.append(path)
    return loaded


_cuda_failure: str | None = None
_cuda_failure_lock = threading.Lock()


def _record_cuda_failure(message: str) -> None:
    global _cuda_failure
    with _cuda_failure_lock:
        _cuda_failure = message


def _validate_model_path(path: Path) -> Path:
    if not path.is_dir():
        raise FileNotFoundError(f"model directory {path} does not exist")
    missing = [name for name in REQUIRED_MODEL_FILES if not (path / name).is_file()]
    if missing:
        raise FileNotFoundError(
            f"model directory {path} is missing {', '.join(missing)}"
        )
    return path


def _create_model(path: Path, device: str, compute_type: str, cpu_threads: int) -> Any:
    import ctranslate2

    return ctranslate2.models.Whisper(
        os.fspath(path),
        device=device,
        compute_type=compute_type,
        intra_threads=cpu_threads,
        inter_threads=1,
    )


def default_compute_type(device: Literal["cpu", "cuda"]) -> str:
    """各裝置的預設精度：CPU int8；CUDA float16。

    RTX 5070 實測 float16 在每個模型都比 int8_float16 快（例如 large-v3 271 vs 405 ms），
    int8_float16 只省約一半 VRAM；VRAM 不足時由 `hardware` 模組改指定 int8_float16。
    """
    return "int8" if device == "cpu" else "float16"


def load_transcriber(
    model: str | ModelSpec | None = None,
    *,
    model_path: Path | None = None,
    cache_dir: Path | None = None,
    device: Device = "auto",
    compute_type: str | None = None,
    cpu_threads: int = 0,
    options: DecodeOptions | None = None,
    cuda_library_dirs: Sequence[Path] = (),
    download: bool = False,
    progress: ProgressCallback | None = None,
) -> WhisperTranscriber:
    """載入模型並回傳 `WhisperTranscriber`。

    Args:
        model: registry 中的模型 id 或 `ModelSpec`；與 `model_path` 二擇一。
        model_path: 使用者指定的本機 CTranslate2 Whisper 目錄（不下載、不驗雜湊）。
        cache_dir: 模型快取目錄；省略時用 `default_cache_dir()`。
        device: "auto" 時有 NVIDIA GPU 就先試 CUDA，失敗則記錄 warning 並改用 CPU。
        compute_type: 精度；省略時依裝置用 `default_compute_type()`。
        cpu_threads: CPU 執行緒數；0 表示 CTranslate2 預設值。
        options: 解碼參數。
        cuda_library_dirs: cuBLAS 所在目錄（見 `preload_cuda_libraries()`）。
        download: 模型不在快取時是否下載；False 時丟 `FileNotFoundError`。
        progress: 下載進度回報。

    Raises:
        ValueError: 參數組合不合法。
        FileNotFoundError: 模型不存在且未允許下載，或本機目錄缺檔。
        RuntimeError: 指定 `device="cuda"` 但 CUDA 無法使用，或模型載入失敗。
    """
    if (model is None) == (model_path is None):
        raise ValueError("pass exactly one of model or model_path")
    if device not in ("auto", "cpu", "cuda"):
        raise ValueError(f"device must be auto, cpu or cuda, got {device!r}")
    if cpu_threads < 0:
        raise ValueError(f"cpu_threads must be >= 0, got {cpu_threads}")

    if model_path is not None:
        path = _validate_model_path(Path(model_path))
        name = path.name
    else:
        assert model is not None
        spec = get_model(model) if isinstance(model, str) else model
        if cache_dir is None:
            from kikitori.asr.cache import default_cache_dir

            cache_dir = default_cache_dir()
        if not is_downloaded(spec, cache_dir):
            if not download:
                raise FileNotFoundError(
                    f"ASR model {spec.id} is not downloaded to {cache_dir}"
                )
            download_model(spec, cache_dir, progress=progress)
        path = model_dir(spec, cache_dir)
        name = spec.id

    tokenizer = WhisperTokenizer.from_file(path / "tokenizer.json")

    if device in ("auto", "cuda"):
        import ctranslate2

        has_gpu = ctranslate2.get_cuda_device_count() > 0
        if device == "cuda" and not has_gpu:
            raise RuntimeError(
                "device='cuda' requested but no CUDA device is available"
            )
        if has_gpu and _cuda_failure is not None:
            # 本機實測：同一行程內 CUDA 初始化失敗後再試，暖機會卡住不返回，所以不再嘗試
            if device == "cuda":
                raise RuntimeError(
                    "CUDA failed earlier in this process; restart the engine to retry: "
                    f"{_cuda_failure}"
                )
            logger.warning(
                "CUDA failed earlier in this process, using CPU for %s", name
            )
        elif has_gpu:
            try:
                if cuda_library_dirs:
                    preload_cuda_libraries(cuda_library_dirs)
            except (FileNotFoundError, OSError) as exc:
                # 還沒碰到 CUDA，不影響之後的嘗試
                if device == "cuda":
                    raise
                logger.warning("cannot preload cuBLAS for %s, using CPU: %s", name, exc)
            else:
                cuda_type = compute_type or default_compute_type("cuda")
                try:
                    transcriber = WhisperTranscriber(
                        _create_model(path, "cuda", cuda_type, cpu_threads),
                        tokenizer,
                        options,
                        name=name,
                        device="cuda",
                        compute_type=cuda_type,
                    )
                    transcriber.warmup()
                    return transcriber
                except (RuntimeError, OSError) as exc:
                    _record_cuda_failure(str(exc))
                    if device == "cuda":
                        raise
                    logger.warning(
                        "CUDA unavailable for ASR model %s, using CPU: %s", name, exc
                    )

    cpu_type = compute_type or default_compute_type("cpu")
    transcriber = WhisperTranscriber(
        _create_model(path, "cpu", cpu_type, cpu_threads),
        tokenizer,
        options,
        name=name,
        device="cpu",
        compute_type=cpu_type,
    )
    return transcriber
