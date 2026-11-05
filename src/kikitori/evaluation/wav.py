"""讀寫評測用的 16 kHz 單聲道 16-bit PCM WAV（只用標準庫 `wave`）。

評測音軌與 MUSAN 素材都是本機檔案，屬於外部輸入：讀取時檢查格式，
不符就丟 `ValueError` 並附上路徑與實際值。
"""

import wave
from pathlib import Path

import numpy as np
import numpy.typing as npt

from kikitori.vad.model import SAMPLE_RATE


def read_wav(path: Path) -> npt.NDArray[np.int16]:
    """讀取 16 kHz 單聲道 16-bit WAV，回傳一維 int16 陣列。

    Raises:
        FileNotFoundError: 檔案不存在。
        ValueError: 不是 WAV，或不是 16 kHz 單聲道 16-bit。
    """
    try:
        with wave.open(str(path), "rb") as w:
            channels, width, rate = w.getnchannels(), w.getsampwidth(), w.getframerate()
            frames = w.readframes(w.getnframes())
    except (wave.Error, EOFError) as exc:
        raise ValueError(f"{path} is not a readable WAV file: {exc}") from exc
    if (channels, width, rate) != (1, 2, SAMPLE_RATE):
        raise ValueError(
            f"{path} must be 16 kHz mono 16-bit, got {rate} Hz, "
            f"{channels} channel(s), {8 * width}-bit"
        )
    if len(frames) % 2:
        raise ValueError(f"{path} has a truncated 16-bit sample ({len(frames)} bytes)")
    return np.frombuffer(frames, dtype="<i2").astype(np.int16)


def write_wav(path: Path, audio: npt.NDArray[np.int16]) -> None:
    """把一維 int16 陣列寫成 16 kHz 單聲道 16-bit WAV。

    Raises:
        ValueError: 不是一維 int16。
    """
    if audio.ndim != 1 or audio.dtype != np.int16:
        raise ValueError(f"audio must be 1-D int16, got {audio.shape} {audio.dtype}")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.astype("<i2").tobytes())
