"""說話者向量模型的輸入特徵：Kaldi 相容的 log-mel fbank（純 numpy）。

位於 speakers 模組的前處理層：把一句 16 kHz 波形轉成 3D-Speaker CAM++ 要的 `[frames, 80]`。
參數與 sherpa-onnx 2.0.0 的 `FeatureExtractorConfig` 預設相同（它用 kaldi-native-fbank 計算），
讓結果與實驗時量到的準確度一致，又不必多裝 sherpa-onnx（它會帶入第二份 onnxruntime）。
與 kaldi-native-fbank 1.22.4 的對照值寫在 `tests/test_speakers_features.py`。
"""

from functools import lru_cache

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16_000
NUM_BINS = 80
FRAME_LENGTH = 400  # 25 ms
FRAME_SHIFT = 160  # 10 ms
FFT_SIZE = 512  # 補到 2 的次方（round_to_power_of_two）
LOW_FREQ = 20.0
HIGH_FREQ = SAMPLE_RATE / 2 - 400.0  # Kaldi 的 high_freq=-400：Nyquist 往下 400 Hz
PREEMPHASIS = 0.97
_LOG_FLOOR = float(np.finfo(np.float32).eps)


def _mel(freq: npt.NDArray[np.float64] | float) -> npt.NDArray[np.float64]:
    return np.asarray(1127.0 * np.log(1.0 + np.asarray(freq) / 700.0))


@lru_cache(maxsize=1)
def mel_banks() -> npt.NDArray[np.float64]:
    """Kaldi 的三角形 mel 濾波器組，形狀 `[80, FFT_SIZE // 2 + 1]`（Nyquist 那一格恆為 0）。

    在 mel 尺度上等距、不做面積正規化（與 Whisper 的 Slaney 濾波器不同）。回傳唯讀陣列。
    """
    fft_bins = FFT_SIZE // 2
    mel_low, mel_high = float(_mel(LOW_FREQ)), float(_mel(HIGH_FREQ))
    delta = (mel_high - mel_low) / (NUM_BINS + 1)
    mels = _mel(np.arange(fft_bins) * (SAMPLE_RATE / FFT_SIZE))
    left = mel_low + np.arange(NUM_BINS)[:, None] * delta
    center = left + delta
    right = center + delta
    up = (mels - left) / (center - left)
    down = (right - mels) / (right - center)
    weights = np.maximum(0.0, np.minimum(up, down))
    weights[(mels <= left) | (mels >= right)] = 0.0
    banks = np.zeros((NUM_BINS, fft_bins + 1))
    banks[:, :fft_bins] = weights
    banks.setflags(write=False)
    return banks


@lru_cache(maxsize=1)
def _povey_window() -> npt.NDArray[np.float64]:
    n = np.arange(FRAME_LENGTH)
    window = (0.5 - 0.5 * np.cos(2 * np.pi * n / (FRAME_LENGTH - 1))) ** 0.85
    window.setflags(write=False)
    return window


def _reflect(index: npt.NDArray[np.int64], length: int) -> npt.NDArray[np.int64]:
    """Kaldi `ExtractWindow` 在 snip_edges=false 時的邊緣反射（短於一個 frame 時要反射多次）。"""
    index = index.copy()
    while True:
        low = index < 0
        high = index >= length
        if not (low.any() or high.any()):
            return index
        index[low] = -index[low] - 1
        index[high] = 2 * length - 1 - index[high]


def fbank(audio: npt.NDArray[np.float32]) -> npt.NDArray[np.float32]:
    """一句的 log-mel fbank，形狀 `[frames, 80]`；frames = round(樣本數 / 160)。

    Args:
        audio: 一維 float32 波形，16 kHz，值域 [-1, 1]（模型的 `normalize_samples=1`）。

    Raises:
        ValueError: 不是一維、或是空的。
    """
    if audio.ndim != 1:
        raise ValueError(f"audio must be 1-D, got shape {audio.shape}")
    length = len(audio)
    if length == 0:
        raise ValueError("audio is empty")
    frames = (length + FRAME_SHIFT // 2) // FRAME_SHIFT
    if frames == 0:
        raise ValueError(f"audio is too short for one frame ({length} samples)")
    # snip_edges=false：第 m 格的中心在 m*160 + 80
    starts = np.arange(frames) * FRAME_SHIFT + FRAME_SHIFT // 2 - FRAME_LENGTH // 2
    index = _reflect(starts[:, None] + np.arange(FRAME_LENGTH)[None, :], length)
    x = audio.astype(np.float64)[index]
    x -= x.mean(axis=1, keepdims=True)
    # 由後往前做 preemphasis：每格用的是前一格「原本」的值；第 0 格與自己相減
    x[:, 1:] -= PREEMPHASIS * x[:, :-1]
    x[:, 0] -= PREEMPHASIS * x[:, 0]
    x *= _povey_window()
    power = np.abs(np.fft.rfft(x, n=FFT_SIZE, axis=1)) ** 2
    energies = power @ mel_banks().T
    return np.log(np.maximum(energies, _LOG_FLOOR)).astype(np.float32)
