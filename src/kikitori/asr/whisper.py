"""以 CTranslate2 執行 Whisper 的 `Transcriber` 實作。

位於 asr 模組的推論層：驗證音訊 → log-mel 特徵 → CTranslate2 encode → 語言偵測 →
beam search／溫度 fallback → 解碼成文字。pipeline 透過 `load_transcriber()` 取得實例。

解碼流程與參數移植自 faster-whisper v1.2.1（MIT License, Copyright (c) 2023 SYSTRAN）
的 `WhisperModel.generate_with_fallback()`／`get_prompt()`，讓結果與 faster-whisper
一致；不直接用 faster-whisper 的原因見 `docs/specs/asr.md`。

CTranslate2 的模型物件由行程內的 `MODEL_POOL` 重複使用：每建立一個新物件並在 GPU 上推論過，
`del` 之後仍漏約 8 MiB 顯示記憶體與 8–10 MiB 主記憶體（memory_soak #2，上游行為）；
session 結束時 `WhisperTranscriber.close()` 把模型卸載（權重離開 GPU；CPU 上也會釋放 RAM，
實測見 `docs/specs/asr.md`）並放回池中，
下一個相同設定的 session 以 `load_model()` 重用同一個物件。
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
        candidate_languages: 偵測範圍：自動偵測時只在這些語言中選（例如口譯模式
            ("zh", "ja")）。None 表示不限制。
        max_new_tokens: 最多產生的 token 數；None 表示到 448 為止。
        forced_language: 固定的辨識語言（Whisper 語言碼）；設定時不做語言偵測，
            `transcribe(language=...)` 另外指定時以呼叫端為準。None 表示自動偵測。
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
    forced_language: str | None = None

    def __post_init__(self) -> None:
        if self.beam_size < 1 or self.best_of < 1:
            raise ValueError("beam_size and best_of must be >= 1")
        if not self.temperatures or any(t < 0 for t in self.temperatures):
            raise ValueError("temperatures must be a non-empty tuple of values >= 0")
        if self.candidate_languages is not None and not self.candidate_languages:
            raise ValueError("candidate_languages must be None or non-empty")
        if self.max_new_tokens is not None and self.max_new_tokens < 1:
            raise ValueError("max_new_tokens must be >= 1")
        if self.forced_language is not None and (
            not isinstance(self.forced_language, str) or not self.forced_language
        ):
            raise ValueError("forced_language must be None or a non-empty str")


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


CLOSE_TIMEOUT = 2.0
"""`close()` 等進行中的推論結束的上限（秒）；逾時就不放回模型池（不能在推論途中卸載）。"""
MAX_IDLE_MODELS = 4
"""模型池最多保留幾個卸載後的空殼物件（每個約 8 MiB 顯示記憶體與一條 CTranslate2 工作
執行緒）。實務上用過的不同組合（模型 × 裝置 × 精度 × 執行緒數）不到 10 個；超過時丟掉最久
沒用的那個——那一個之後再用到時會建立新物件，漏一次 8 MiB，與沒有池時相同。"""


@dataclass(frozen=True)
class ModelKey:
    """模型池的鍵：相同的鍵才能重用同一個 CTranslate2 物件（建立後這些參數不能改）。"""

    path: str
    """模型目錄（`realpath` + `normcase`）。"""
    device: str
    device_index: int
    compute_type: str
    intra_threads: int
    inter_threads: int

    def is_under(self, folder: Path) -> bool:
        """模型目錄是否在 `folder` 之內（或就是它）。"""
        root = _normalized(folder)
        return self.path == root or self.path.startswith(root.rstrip(os.sep) + os.sep)


def _normalized(path: Path) -> str:
    # realpath：同一個資料夾的短檔名（ALIANL~1）、連結與大小寫寫法都對應到同一個鍵
    return os.path.normcase(os.path.realpath(path))


class ModelPool:
    """行程內的 CTranslate2 模型物件池（執行緒安全）。

    只保存「已卸載」的物件：權重不在 GPU／RAM 上，只剩建立時的執行環境。取用時
    `load_model()` 把權重載回；歸還時 `unload_model()` 後放回。一個物件同時只會借給一個
    使用者（即時與完整路徑共用時由 `_ModelLease` 計數，兩邊都關閉才歸還）。
    """

    def __init__(self, max_idle: int = MAX_IDLE_MODELS) -> None:
        """
        Args:
            max_idle: 最多保留的閒置物件數；超過時丟掉最久沒用的。

        Raises:
            ValueError: `max_idle` 不是正整數。
        """
        if isinstance(max_idle, bool) or not isinstance(max_idle, int) or max_idle < 1:
            raise ValueError(f"max_idle must be a positive integer, got {max_idle!r}")
        self._max_idle = max_idle
        self._lock = threading.Lock()
        self._idle: list[tuple[ModelKey, Any]] = []
        """最舊的在前（LRU）。"""

    def acquire(self, key: ModelKey, create: Callable[[], Any]) -> Any:
        """取出 `key` 的閒置物件並 `load_model()`；沒有、或重新載入失敗時呼叫 `create()`。

        Raises:
            RuntimeError、OSError: `create()` 建立失敗。
        """
        model = None
        with self._lock:
            for index in range(len(self._idle) - 1, -1, -1):
                if self._idle[index][0] == key:
                    model = self._idle.pop(index)[1]
                    break
        if model is None:
            return create()
        # 在鎖外載入（可能要數秒）。失敗時記錄並丟掉這個物件（狀態不明，不再重用），改建立
        # 新的：結果與池中沒有物件時相同，只是多漏一次建立物件的 8 MiB。若是真的資源不足
        # （例如 VRAM），create() 會以同樣的原因失敗並往上拋。
        try:
            model.load_model()
        except Exception:
            logger.exception(
                "reloading a pooled ASR model object for %s failed; creating a new one",
                key.path,
            )
            del model
            return create()
        logger.debug("reusing a pooled ASR model object for %s", key.path)
        return model

    def release(self, key: ModelKey, model: Any) -> None:
        """卸載 `model` 並放回池中；卸載失敗或沒有卸載時記錄並丟掉它。"""
        try:
            model.unload_model()
        except Exception:
            logger.exception("unloading the ASR model failed; dropping it")
            return
        # CTranslate2 的 unload_model() 在還有進行中的 batch、或拿不到模型的鎖時直接返回
        # 而不丟例外（python/cpp/replica_pool.h）；權重仍在記憶體中，留在池裡就等於一直
        # 佔著。這時不放回池中，交給參照計數釋放。
        if getattr(model, "model_is_loaded", False) is True:
            logger.warning(
                "the ASR model %s is still loaded after unload_model(); dropping it",
                key.path,
            )
            return
        with self._lock:
            self._idle.append((key, model))
            evicted = self._idle[: -self._max_idle]
            del self._idle[: -self._max_idle]
        if evicted:
            logger.info(
                "dropping %d idle ASR model object(s) from the pool", len(evicted)
            )
        # evicted 離開作用域時才真正釋放（在鎖外，避免解構很慢時擋住其他執行緒）

    def evict_under(self, folder: Path) -> int:
        """丟掉模型目錄在 `folder` 之內的閒置物件（刪除模型之前），回傳丟掉的數量。"""
        with self._lock:
            evicted = [entry for entry in self._idle if entry[0].is_under(folder)]
            self._idle = [
                entry for entry in self._idle if not entry[0].is_under(folder)
            ]
        return len(evicted)

    def clear(self) -> int:
        """丟掉所有閒置物件（引擎結束時），回傳丟掉的數量。"""
        with self._lock:
            evicted, self._idle = self._idle, []
        return len(evicted)

    def idle_count(self) -> int:
        """目前保留的閒置物件數。"""
        with self._lock:
            return len(self._idle)


MODEL_POOL = ModelPool()
"""行程內共用的模型池（`load_transcriber()` 使用）。"""


class _ModelLease:
    """一個借出的模型物件與共用它的 transcriber 數；最後一個關閉時歸還模型池。"""

    def __init__(self, pool: ModelPool, key: ModelKey, model: Any) -> None:
        self._pool = pool
        self._key = key
        self._model = model
        self._lock = threading.Lock()
        self._users = 1
        self._reusable = True

    def retain(self) -> None:
        """多一個共用者（`with_options()`）。"""
        with self._lock:
            self._users += 1

    def release(self, *, reusable: bool) -> None:
        """一個共用者關閉；`reusable=False`（推論可能還在進行）時之後不歸還、交給參照計數。"""
        with self._lock:
            self._users -= 1
            self._reusable = self._reusable and reusable
            if self._users > 0:
                return
            model, self._model = self._model, None
            reusable = self._reusable
        if model is not None and reusable:
            self._pool.release(self._key, model)


class WhisperTranscriber:
    """CTranslate2 Whisper 的 `Transcriber` 實作。

    同一個實例的呼叫以 lock 串行化。即時與完整路徑要並行：模型不同就各載入一個；
    模型、裝置、精度都相同時，以 `load_transcriber(workers=2)` 載入一次，再用
    `with_options()` 建立共用權重的另一個實例（各自的 lock 與解碼參數）。

    用完要 `close()`：模型卸載並放回 `MODEL_POOL`（共用時兩個實例都關閉才歸還）。
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
        lease: _ModelLease | None = None,
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
            lease: 模型池的借用紀錄；`close()` 時歸還（None 表示模型不屬於池）。
        """
        self._model = model
        self._lease = lease
        self._closed = False
        self._close_lock = threading.Lock()
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
        if self.options.forced_language is not None:
            self._check_language(self.options.forced_language)

    def with_options(self, options: DecodeOptions) -> "WhisperTranscriber":
        """共用同一個已載入模型、只換解碼參數的新實例（有自己的 lock）。

        CTranslate2 官方文件（Parallel execution）：同一個裝置上的多個 worker 共用模型
        權重，計算方法釋放 GIL，可以從多個 Python 執行緒呼叫；原始碼中同一裝置的
        replica 都指向同一個 `Model` 物件。所以載入時 `inter_threads` 要等於共用的實例數，
        兩條路徑才會真的並行，而不是在同一個 worker 上排隊。

        Raises:
            ValueError: 新參數指定的語言模型不支援。
            RuntimeError: 這個實例已經 `close()`。
        """
        # 檢查與 retain() 都在 _close_lock 之內（close() 也在同一把鎖內設定旗標）：否則
        # 檢查之後、retain() 之前 close() 跑完，模型會在新實例還在用時被放回池中，
        # 甚至借給下一個 session。
        with self._close_lock:
            if self._closed:
                raise RuntimeError(f"transcriber {self.name} is closed")
            if self._lease is not None:
                self._lease.retain()
        try:
            return WhisperTranscriber(
                self._model,
                self._tokenizer,
                options,
                storage=self._storage,
                name=self.name,
                device=self.device,
                compute_type=self.compute_type,
                lease=self._lease,
            )
        except BaseException:
            # 例如新參數的語言不支援：模型本身沒問題，撤銷上面的 retain()
            if self._lease is not None:
                self._lease.release(reusable=True)
            raise

    def close(self, timeout: float = CLOSE_TIMEOUT) -> None:
        """不再使用：之後的呼叫丟 `RuntimeError`，模型卸載並放回模型池（可重複呼叫）。

        先等進行中的推論結束（最多 `timeout` 秒）：不能在推論途中卸載權重。逾時（例如推論
        卡住）時記 warning、不放回池中，模型物件交給參照計數釋放。
        """
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        acquired = self._lock.acquire(timeout=timeout)
        if acquired:
            self._lock.release()
        else:
            logger.warning(
                "ASR model %s still busy after %.1f s; not returning it to the pool",
                self.name,
                timeout,
            )
        if self._lease is not None:
            self._lease.release(reusable=acquired)

    def _discard(self) -> None:
        """關閉但不把模型放回池中（例如 GPU 上暖機失敗：物件狀態不明，交給參照計數）。"""
        with self._close_lock:
            if self._closed:
                return
            self._closed = True
        if self._lease is not None:
            self._lease.release(reusable=False)

    def _check_open(self) -> None:
        # 在 self._lock 之內呼叫：close() 設定旗標之後開始的推論一律拒絕
        if self._closed:
            raise RuntimeError(f"transcriber {self.name} is closed")

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
            self._check_open()
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
        if language is None:
            language = self.options.forced_language
        if language is not None:
            self._check_language(language)
        prompt_tokens = self._prompt_tokens(prompt)
        features = log_mel_spectrogram(to_float32(samples), self._model.n_mels)
        windows = split_windows(features)

        with self._lock:
            self._check_open()
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


MAX_WORKERS = 4


def _create_model(
    path: Path, device: str, compute_type: str, cpu_threads: int, workers: int
) -> Any:
    import ctranslate2

    # inter_threads 個 worker 在同一個裝置上共用權重（官方 Parallel execution 文件）；
    # CPU 上每個 worker 各用 intra_threads 個執行緒，所以總數與分開載入兩個模型相同。
    return ctranslate2.models.Whisper(
        os.fspath(path),
        device=device,
        compute_type=compute_type,
        intra_threads=cpu_threads,
        inter_threads=workers,
    )


def _acquire_model(
    path: Path, device: str, compute_type: str, cpu_threads: int, workers: int
) -> tuple[Any, _ModelLease]:
    """從 `MODEL_POOL` 取用（或建立）模型物件，回傳物件與借用紀錄。"""
    pool = MODEL_POOL
    key = ModelKey(
        path=_normalized(path),
        device=device,
        device_index=0,
        compute_type=compute_type,
        intra_threads=cpu_threads,
        inter_threads=workers,
    )
    model = pool.acquire(
        key, lambda: _create_model(path, device, compute_type, cpu_threads, workers)
    )
    return model, _ModelLease(pool, key, model)


def _pooled_transcriber(
    path: Path,
    tokenizer: WhisperTokenizer,
    options: DecodeOptions | None,
    *,
    name: str,
    device: Literal["cpu", "cuda"],
    compute_type: str,
    cpu_threads: int,
    workers: int,
    warmup: bool,
) -> WhisperTranscriber:
    """從模型池取得模型並建立 transcriber；之後任何一步失敗都歸還借用紀錄。

    CPU 與 CUDA 路徑共用，失敗時的處理才會一致：建構時的 `ValueError`（參數不合法，模型
    本身沒問題）放回池中；暖機失敗（GPU 函式庫或顯示記憶體的問題）不放回池中——物件可能
    處在壞掉的 CUDA 狀態，交給參照計數釋放。

    Raises:
        ValueError: 解碼參數指定的語言模型不支援。
        RuntimeError、OSError: 建立、載入或暖機失敗。
    """
    model, lease = _acquire_model(path, device, compute_type, cpu_threads, workers)
    try:
        transcriber = WhisperTranscriber(
            model,
            tokenizer,
            options,
            name=name,
            device=device,
            compute_type=compute_type,
            lease=lease,
        )
    except ValueError:
        lease.release(reusable=True)
        raise
    except BaseException:
        lease.release(reusable=False)
        raise
    if warmup:
        try:
            transcriber.warmup()
        except BaseException:
            transcriber._discard()
            raise
    return transcriber


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
    workers: int = 1,
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
        workers: 可並行的解碼數（CTranslate2 `inter_threads`）；要用 `with_options()`
            讓即時與完整路徑共用這個模型時設 2。權重只載入一份。

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
    if not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers must be in [1, {MAX_WORKERS}], got {workers}")

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
                    return _pooled_transcriber(
                        path,
                        tokenizer,
                        options,
                        name=name,
                        device="cuda",
                        compute_type=cuda_type,
                        cpu_threads=cpu_threads,
                        workers=workers,
                        warmup=True,
                    )
                except (RuntimeError, OSError) as exc:
                    _record_cuda_failure(str(exc))
                    if device == "cuda":
                        raise
                    logger.warning(
                        "CUDA unavailable for ASR model %s, using CPU: %s", name, exc
                    )

    cpu_type = compute_type or default_compute_type("cpu")
    # CPU 不暖機：沒有要提早發現的 GPU 函式庫問題，也沒有 CUDA kernel 初始化的延遲
    return _pooled_transcriber(
        path,
        tokenizer,
        options,
        name=name,
        device="cpu",
        compute_type=cpu_type,
        cpu_threads=cpu_threads,
        workers=workers,
        warmup=False,
    )
