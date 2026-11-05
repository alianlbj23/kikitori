"""Silero VAD 的 ONNX 包裝：載入並驗證打包進套件的官方模型。

位於 VAD 切句模組的模型層，是 `VoiceModel` 的預設實作；`Segmenter` 只透過
`VoiceModel` 介面使用它。模型檔隨套件發布，載入時先驗證 SHA-256，
避免用到被改動或不完整的檔案。
"""

import hashlib
from collections.abc import Sequence
from importlib import resources
from pathlib import Path
from typing import Protocol

import numpy as np
import numpy.typing as npt
import onnxruntime

from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES, AudioWindow

MODEL_VERSION = "v6.2.3"
MODEL_COMMIT = "5cd7945676eb32225748052e2e6a0580e4686a08"
MODEL_SOURCE_URL = (
    "https://github.com/snakers4/silero-vad/blob/"
    f"{MODEL_COMMIT}/src/silero_vad/data/silero_vad.onnx"
)
MODEL_SHA256 = "1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3"

_PACKAGE = "kikitori.vad"
_MODEL_RESOURCE = "models/silero_vad.onnx"


class ModelIntegrityError(RuntimeError):
    """模型檔的 SHA-256 與固定值不符。"""


def read_verified_model(
    path: Path | None = None, expected_sha256: str = MODEL_SHA256
) -> bytes:
    """讀取模型檔並驗證 SHA-256，回傳模型內容。

    Args:
        path: 模型檔路徑；省略時讀取打包在套件內的 `silero_vad.onnx`。
        expected_sha256: 預期的十六進位 SHA-256。

    Raises:
        FileNotFoundError: 模型檔不存在。
        ModelIntegrityError: 雜湊不符。
    """
    if path is None:
        source = f"{_PACKAGE}/{_MODEL_RESOURCE}"
        data = resources.files(_PACKAGE).joinpath(_MODEL_RESOURCE).read_bytes()
    else:
        source = str(path)
        data = path.read_bytes()
    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise ModelIntegrityError(
            f"Silero model {source} has SHA-256 {actual}, expected {expected_sha256}"
        )
    return data


def create_session(model: bytes) -> onnxruntime.InferenceSession:
    """用已驗證的模型內容建立 CPU 推論 session。

    直接傳入位元組而非路徑，確保推論用的正是剛驗證過的內容。
    執行緒數設為 1 與官方 `OnnxWrapper` 相同：每次只算 32 ms，多執行緒的
    排程成本反而比計算本身高。
    """
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = 1
    return onnxruntime.InferenceSession(
        model, sess_options=options, providers=["CPUExecutionProvider"]
    )


CONTEXT_SAMPLES = 64  # 官方 OnnxWrapper 在 16 kHz 前接的前文長度
_STATE_SHAPE = (2, 1, 128)
_OUTPUT_NAMES = ["output", "stateN"]
_INT16_SCALE = 1.0 / 32768.0


class OnnxSession(Protocol):
    """`SileroVad` 需要的 ONNX session 介面（`onnxruntime.InferenceSession` 的子集）。"""

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, npt.NDArray[np.generic]],
    ) -> Sequence[npt.NDArray[np.generic]]: ...


class SileroVad:
    """`VoiceModel` 的 Silero VAD v6 實作，推論細節依官方 `OnnxWrapper`。

    每個 512 樣本視窗前面接上前一個視窗的最後 64 樣本再送進模型（共 576），
    模型的 `state` 每次推論後寫回。原始 ONNX 模型不會檢查這些，少了前文時
    機率會塌到約 0.001，所以由這層負責。
    """

    def __init__(
        self,
        context_samples: int = CONTEXT_SAMPLES,
        session: OnnxSession | None = None,
    ) -> None:
        """
        Args:
            context_samples: 前接的前文樣本數。只供診斷用：評測時設為 0
                重現「沒接前文」的情況；正式使用維持預設 64。
            session: 推論 session；省略時載入打包的模型（先驗證 SHA-256）。
                測試可傳入會記錄輸入的假 session。

        Raises:
            ValueError: `context_samples` 不是 0–64 的整數。
        """
        if (
            not isinstance(context_samples, int)
            or isinstance(context_samples, bool)
            or not 0 <= context_samples <= CONTEXT_SAMPLES
        ):
            raise ValueError(
                f"context_samples must be an int in 0..{CONTEXT_SAMPLES}, "
                f"got {context_samples!r}"
            )
        self._context_samples = context_samples
        self._session: OnnxSession = (
            session if session is not None else create_session(read_verified_model())
        )
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self.reset()

    def reset(self) -> None:
        """把模型 state 與前文都清成零，之後的輸入視為新串流的開頭。"""
        self._state = np.zeros(_STATE_SHAPE, dtype=np.float32)
        self._context = np.zeros(self._context_samples, dtype=np.float32)

    def probability(self, window: AudioWindow) -> float:
        """回傳一個 512 樣本視窗的人聲機率。

        Args:
            window: 一維 int16（原始 PCM）或 float32（已縮放到 −1–1）陣列。

        Raises:
            TypeError: 不是 numpy 陣列。
            ValueError: 維度、長度、dtype 不符，或 float32 含非有限值。
            RuntimeError: 模型輸出的形狀與預期不符。
        """
        samples = _to_float_window(window)
        x = np.concatenate([self._context, samples])[np.newaxis, :]
        out, state = self._session.run(
            _OUTPUT_NAMES, {"input": x, "state": self._state, "sr": self._sr}
        )
        if np.shape(out) != (1, 1) or np.shape(state) != _STATE_SHAPE:
            raise RuntimeError(
                f"unexpected Silero output shapes: output {np.shape(out)}, "
                f"state {np.shape(state)}; expected (1, 1) and {_STATE_SHAPE}"
            )
        self._state = np.asarray(state, dtype=np.float32)
        if self._context_samples:
            self._context = x[0, -self._context_samples :].copy()
        return float(np.asarray(out)[0, 0])


def _to_float_window(window: AudioWindow) -> npt.NDArray[np.float32]:
    if not isinstance(window, np.ndarray):
        raise TypeError(f"window must be a numpy array, got {type(window).__name__}")
    if window.ndim != 1:
        raise ValueError(f"window must be 1-D, got shape {window.shape}")
    if window.shape[0] != WINDOW_SAMPLES:
        raise ValueError(
            f"window must have {WINDOW_SAMPLES} samples, got {window.shape[0]}"
        )
    if window.dtype == np.int16:
        # 官方建議固定除以 32768，不要逐段正規化（會造成誤報，見 silero-vad #309）。
        return window.astype(np.float32) * np.float32(_INT16_SCALE)
    if window.dtype == np.float32:
        if not np.isfinite(window).all():
            raise ValueError("float32 window contains NaN or infinity")
        return window
    raise ValueError(f"window dtype must be int16 or float32, got {window.dtype.name}")
