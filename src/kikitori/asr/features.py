"""Whisper 的 log-mel 特徵（純 numpy）。

位於 asr 模組的前處理層：把 16 kHz float32 波形轉成 CTranslate2 Whisper 模型要的
`[n_mels, frames]` 特徵。演算法移植自 faster-whisper v1.2.1 的
`faster_whisper/feature_extractor.py`（MIT License, Copyright (c) 2023 SYSTRAN），
讓結果與 faster-whisper 一致，同時不必安裝它（它依賴含 GPL 元件的 PyAV）。
"""

from functools import lru_cache

import numpy as np
import numpy.typing as npt

from kikitori.asr.audio import SAMPLE_RATE

N_FFT = 400
HOP_LENGTH = 160
CHUNK_SECONDS = 30
FRAMES_PER_WINDOW = (
    CHUNK_SECONDS * SAMPLE_RATE // HOP_LENGTH
)  # 3000，encoder 固定輸入長度


@lru_cache(maxsize=4)
def mel_filters(n_mels: int, sample_rate: int = SAMPLE_RATE) -> npt.NDArray[np.float32]:
    """Slaney 式 mel 濾波器組，形狀 `[n_mels, N_FFT // 2 + 1]`。

    與 librosa `mel(htk=False, norm="slaney")` 相同，Whisper 訓練時就是用這組濾波器。
    結果會被快取，回傳的陣列設為唯讀以免被呼叫端改到。
    """
    if n_mels <= 0:
        raise ValueError(f"n_mels must be positive, got {n_mels}")
    fftfreqs = np.fft.rfftfreq(n=N_FFT, d=1.0 / sample_rate)

    # 0 Hz 到 8 kHz 在 Slaney mel 尺度上是 0 到 45.2456...
    mels = np.linspace(0.0, 45.245640471924965, n_mels + 2)
    f_sp = 200.0 / 3
    freqs = f_sp * mels
    min_log_hz = 1000.0
    min_log_mel = min_log_hz / f_sp
    logstep = np.log(6.4) / 27.0
    log_t = mels >= min_log_mel
    freqs[log_t] = min_log_hz * np.exp(logstep * (mels[log_t] - min_log_mel))

    fdiff = np.diff(freqs)
    ramps = freqs.reshape(-1, 1) - fftfreqs.reshape(1, -1)
    lower = -ramps[:-2] / fdiff[:-1, None]
    upper = ramps[2:] / fdiff[1:, None]
    weights = np.maximum(0.0, np.minimum(lower, upper))
    # Slaney 正規化：讓每個頻帶的能量大致相同
    enorm = 2.0 / (freqs[2 : n_mels + 2] - freqs[:n_mels])
    weights *= enorm[:, None]
    result = weights.astype(np.float32)
    result.setflags(write=False)
    return result


def log_mel_spectrogram(
    audio: npt.NDArray[np.float32], n_mels: int, padding: int = HOP_LENGTH
) -> npt.NDArray[np.float32]:
    """計算 Whisper 的 log-mel 特徵，形狀 `[n_mels, frames]`。

    Args:
        audio: 一維 float32 波形，16 kHz，值域約 [-1, 1]。
        n_mels: mel 頻帶數（large-v3 系列為 128，其他為 80），由模型決定。
        padding: 尾端補零的樣本數；faster-whisper 預設補 160。
    """
    if audio.ndim != 1:
        raise ValueError(f"audio must be 1-D, got shape {audio.shape}")
    waveform = audio.astype(np.float32, copy=False)
    if padding:
        waveform = np.pad(waveform, (0, padding))
    # center=True、reflect 補邊，同 torch.stft 的預設
    waveform = np.pad(waveform, (N_FFT // 2, N_FFT // 2), mode="reflect")
    n_frames = 1 + (len(waveform) - N_FFT) // HOP_LENGTH
    frames = np.lib.stride_tricks.as_strided(
        waveform,
        shape=(n_frames, N_FFT),
        strides=(HOP_LENGTH * waveform.strides[0], waveform.strides[0]),
        writeable=False,
    )
    window = np.hanning(N_FFT + 1)[:-1].astype(np.float32)
    stft = np.fft.rfft(frames * window, n=N_FFT, axis=-1).astype(np.complex64)
    # 丟掉最後一個 frame，與 Whisper 原始實作一致
    magnitudes = np.abs(stft[:-1].T) ** 2

    mel_spec = mel_filters(n_mels) @ magnitudes
    log_spec = np.log10(np.clip(mel_spec, 1e-10, None))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    return ((log_spec + 4.0) / 4.0).astype(np.float32)


def split_windows(features: npt.NDArray[np.float32]) -> list[npt.NDArray[np.float32]]:
    """把特徵切成每段剛好 3000 frame 的視窗，不足的補 0（同 faster-whisper `pad_or_trim`）。

    最後一個 frame 是補邊產生的，不算內容（faster-whisper 的 `content_frames`）。
    """
    content_frames = max(features.shape[-1] - 1, 1)
    windows = []
    for start in range(0, content_frames, FRAMES_PER_WINDOW):
        segment = features[:, start : min(start + FRAMES_PER_WINDOW, content_frames)]
        if segment.shape[-1] < FRAMES_PER_WINDOW:
            segment = np.pad(
                segment, ((0, 0), (0, FRAMES_PER_WINDOW - segment.shape[-1]))
            )
        windows.append(np.ascontiguousarray(segment, dtype=np.float32))
    return windows
