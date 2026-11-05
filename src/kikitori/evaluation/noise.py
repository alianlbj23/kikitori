"""白噪音與粉紅噪音產生器（16 kHz、int16、固定亂數種子）。

評測素材之一：純噪音音軌檢查 VAD 會不會把噪音當成人聲，也用來填入句間
間隔或疊加在人聲底下。固定種子讓同一組參數每次產生相同音訊，結果可重現。
"""

import math

import numpy as np
import numpy.typing as npt

FULL_SCALE = 32768.0
MIN_DBFS = -120.0


def rms_dbfs(audio: npt.NDArray[np.int16]) -> float:
    """回傳 int16 音訊的 RMS 等級（dBFS，以 32768 為 0 dBFS）；全零時回傳 −inf。"""
    rms = math.sqrt(float(np.mean(np.square(audio, dtype=np.float64))))
    if rms == 0.0:
        return -math.inf
    return 20.0 * math.log10(rms / FULL_SCALE)


def white_noise(
    num_samples: int, level_dbfs: float, seed: int
) -> npt.NDArray[np.int16]:
    """產生高斯白噪音。

    Args:
        num_samples: 樣本數（> 0）。
        level_dbfs: 目標 RMS 等級，範圍 −120–0 dBFS。
        seed: 亂數種子。

    Raises:
        ValueError: 參數超出範圍。
    """
    _validate(num_samples, level_dbfs)
    rng = np.random.default_rng(seed)
    return _scale_to_int16(rng.standard_normal(num_samples), level_dbfs)


def pink_noise(num_samples: int, level_dbfs: float, seed: int) -> npt.NDArray[np.int16]:
    """產生粉紅噪音（功率與頻率成反比，約 −3 dB/octave）。

    在頻域把白噪音的振幅乘上 1/√f 再轉回時域；直流分量設為 0，避免
    低頻能量無限大。參數與 `white_noise` 相同。
    """
    _validate(num_samples, level_dbfs)
    rng = np.random.default_rng(seed)
    spectrum = np.fft.rfft(rng.standard_normal(num_samples))
    freqs = np.fft.rfftfreq(num_samples)
    gain = np.zeros_like(freqs)
    gain[1:] = 1.0 / np.sqrt(freqs[1:])
    shaped = np.fft.irfft(spectrum * gain, n=num_samples)
    return _scale_to_int16(shaped, level_dbfs)


def _validate(num_samples: int, level: float) -> None:
    if not isinstance(num_samples, int) or isinstance(num_samples, bool):
        raise TypeError(f"num_samples must be an int, got {num_samples!r}")
    if num_samples <= 0:
        raise ValueError(f"num_samples must be > 0, got {num_samples}")
    if not (math.isfinite(level) and MIN_DBFS <= level <= 0.0):
        raise ValueError(f"level_dbfs must be in {MIN_DBFS}..0, got {level!r}")


def _scale_to_int16(
    signal: npt.NDArray[np.float64], level: float
) -> npt.NDArray[np.int16]:
    rms = math.sqrt(float(np.mean(np.square(signal))))
    target = FULL_SCALE * 10.0 ** (level / 20.0)
    scaled = signal * (target / rms) if rms > 0.0 else signal
    # 高等級時少數峰值會超出 int16，截斷而不是溢位繞回。
    return np.clip(np.round(scaled), -32768, 32767).astype(np.int16)
