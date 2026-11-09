"""一句話的說話者向量：fbank → 3D-Speaker CAM++（onnxruntime CPU）→ L2 正規化。

位於 speakers 模組的推論層。前處理同 sherpa-onnx 的 3D-Speaker 路徑（模型 metadata
`feature_normalize_type=global-mean`、`normalize_samples=1`）：[-1, 1] 的波形算 fbank，
再減掉整句的時間平均。
"""

from collections.abc import Sequence
from typing import Protocol

import numpy as np
import numpy.typing as npt
import onnxruntime

from kikitori.speakers.features import NUM_BINS, fbank

_INT16_SCALE = 1.0 / 32768.0
_INPUT = "x"
INTRA_OP_THREADS = 2
"""每句只算一次（約 0.06 秒）；與 ASR 並行，不佔滿所有核心。"""


class OnnxSession(Protocol):
    """`SpeakerEmbedder` 需要的 ONNX session 介面（`onnxruntime.InferenceSession` 的子集）。"""

    def run(
        self,
        output_names: list[str] | None,
        input_feed: dict[str, npt.NDArray[np.generic]],
    ) -> Sequence[npt.NDArray[np.generic]]: ...


def create_session(model: bytes) -> onnxruntime.InferenceSession:
    """用已驗證的模型內容建立 CPU session，並檢查輸入是 `x: [N, T, 80]`。

    Raises:
        ValueError: 模型的輸入不符（不是這個模組支援的說話者向量模型）。
    """
    options = onnxruntime.SessionOptions()
    options.inter_op_num_threads = 1
    options.intra_op_num_threads = INTRA_OP_THREADS
    session = onnxruntime.InferenceSession(
        model, sess_options=options, providers=["CPUExecutionProvider"]
    )
    inputs = session.get_inputs()
    if len(inputs) != 1 or inputs[0].name != _INPUT or inputs[0].shape[-1] != NUM_BINS:
        found = [(i.name, i.shape) for i in inputs]
        raise ValueError(f"expected one input x: [N, T, {NUM_BINS}], got {found}")
    return session


class SpeakerEmbedder:
    """把一句 int16 音訊轉成正規化的說話者向量。執行緒安全（onnxruntime 的 `run` 可並行）。"""

    def __init__(self, session: OnnxSession, dims: int) -> None:
        """
        Args:
            session: `create_session()` 建立的 session（測試可注入假的）。
            dims: 預期的向量維度；模型輸出不符時 `embed()` 丟 ValueError。
        """
        if dims <= 0:
            raise ValueError(f"dims must be positive, got {dims}")
        self._session = session
        self._dims = dims

    def embed(self, audio: npt.NDArray[np.int16]) -> npt.NDArray[np.float32]:
        """一維 16 kHz int16 音訊的說話者向量（L2 = 1，float32，長度 `dims`）。

        Raises:
            ValueError: 音訊不是一維 int16、太短（不到一個 frame），或模型輸出不合法
                （維度不符、全零、含 NaN／inf）。
        """
        if audio.ndim != 1 or audio.dtype != np.int16:
            raise ValueError(
                f"audio must be 1-D int16, got {audio.dtype} with shape {audio.shape}"
            )
        features = fbank(audio.astype(np.float32) * _INT16_SCALE)
        features -= features.mean(axis=0, keepdims=True)
        outputs = self._session.run(None, {_INPUT: features[None, :, :]})
        vector = np.asarray(outputs[0], dtype=np.float32).reshape(-1)
        if vector.shape != (self._dims,):
            raise ValueError(
                f"model returned {vector.shape[0]} values, expected {self._dims}"
            )
        norm = float(np.linalg.norm(vector))
        if not np.isfinite(norm) or norm == 0.0:
            raise ValueError(f"model returned an invalid embedding (norm {norm})")
        return vector / norm
