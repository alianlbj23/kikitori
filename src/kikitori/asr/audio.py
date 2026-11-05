"""辨識器輸入音訊的驗證與轉換。

位於 asr 模組的最前端：capture／VAD 交來的陣列先在這裡檢查格式，
不合格就立刻丟錯，避免把錯誤格式的資料餵進模型後得到看似合理的錯誤文字。
"""

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
_INT16_SCALE = 32768.0


def validate_audio(audio: object) -> npt.NDArray[np.int16]:
    """確認音訊是非空的一維 int16 numpy 陣列並回傳它。

    Raises:
        TypeError: 不是 numpy 陣列，或 dtype 不是 int16。
        ValueError: 不是一維，或長度為 0。
    """
    if not isinstance(audio, np.ndarray):
        raise TypeError(f"audio must be a numpy.ndarray, got {type(audio).__name__}")
    if audio.dtype != np.int16:
        raise TypeError(f"audio must be int16 PCM, got dtype {audio.dtype}")
    if audio.ndim != 1:
        raise ValueError(f"audio must be 1-D mono, got shape {audio.shape}")
    if audio.size == 0:
        raise ValueError("audio is empty")
    return audio


def to_float32(audio: npt.NDArray[np.int16]) -> npt.NDArray[np.float32]:
    """int16 PCM 轉成 [-1, 1) 的 float32；除以 32768 與 faster-whisper 解碼結果一致。"""
    return audio.astype(np.float32) / _INT16_SCALE
