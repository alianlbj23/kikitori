"""降混音、串流重採樣與 int16 轉換：把各後端的 float 多聲道音訊轉成管線格式。

位於 capture 後端與緩衝之間。重採樣用 Kaiser 窗 windowed-sinc 原型濾波器的
polyphase 實作，只依賴 numpy（不引入 LGPL 的 soxr，也不為一個濾波器引入 scipy）。
串流狀態跨區塊保留，任意切塊的結果與一次處理相同，區塊邊界不會有爆音。
"""

import math

import numpy as np
import numpy.typing as npt
from numpy.lib.stride_tricks import sliding_window_view

from kikitori.capture.types import SAMPLE_RATE, AudioChunk

MAX_SAMPLE_RATE = 768_000
MAX_CHANNELS = 64

# 通帶到較低端 Nyquist 的 87.5%，阻帶從 Nyquist 開始：16 kHz 輸出時是 7 kHz／8 kHz，
# 語音能量幾乎都在 7 kHz 以下，而且 Nyquist 以上完全落在阻帶，不會混疊回通帶。
_PASSBAND_FRACTION = 0.875
_DEFAULT_ATTENUATION_DB = 80.0
# 一次最多算這麼多個輸出樣本，限制大區塊（例如整個檔案）展開時的記憶體用量。
_OUTPUT_BATCH = 4096

FloatArray = npt.NDArray[np.float64]


def _validate_rate(name: str, rate: int) -> None:
    if isinstance(rate, bool) or not isinstance(rate, int):
        raise TypeError(f"{name} must be an int, got {rate!r}")
    if not 1 <= rate <= MAX_SAMPLE_RATE:
        raise ValueError(f"{name} must be in [1, {MAX_SAMPLE_RATE}], got {rate}")


class StreamingResampler:
    """把一維 float 音訊從 `in_rate` 串流重採樣到 `out_rate`。

    每次 `process()` 回傳目前輸入足以算出的所有輸出樣本；累計輸出長度
    恆為 `ceil(累計輸入長度 × out_rate / in_rate)`。輸出有約 (K−1)/2 個輸入樣本的
    群延遲（48 kHz 時約 2.5 ms），不做補償。
    """

    def __init__(
        self,
        in_rate: int,
        out_rate: int = SAMPLE_RATE,
        *,
        attenuation_db: float = _DEFAULT_ATTENUATION_DB,
    ) -> None:
        _validate_rate("in_rate", in_rate)
        _validate_rate("out_rate", out_rate)
        if not (math.isfinite(attenuation_db) and 20.0 <= attenuation_db <= 150.0):
            raise ValueError(
                f"attenuation_db must be in [20, 150], got {attenuation_db}"
            )
        self.in_rate = in_rate
        self.out_rate = out_rate
        g = math.gcd(in_rate, out_rate)
        self._up = out_rate // g
        self._down = in_rate // g
        self._passthrough = self._up == self._down
        if self._passthrough:
            return

        self._taps_rev = self._design(attenuation_db)
        taps = self._taps_rev.shape[1]
        # 以零當作開頭之前的訊號，讓串流與一次處理的結果完全一致。
        self._history: FloatArray = np.zeros(taps - 1)
        self._history_start = -(taps - 1)
        self._in_count = 0
        self._next_out = 0

    @property
    def taps_per_phase(self) -> int:
        """每個輸出樣本用到的輸入樣本數（直通時為 1）。"""
        return 1 if self._passthrough else int(self._taps_rev.shape[1])

    def _design(self, attenuation_db: float) -> FloatArray:
        """設計原型濾波器並拆成 polyphase 係數，回傳形狀 (L, K)、每列已反轉的係數。"""
        up = self._up
        nyquist_low = min(self.in_rate, self.out_rate) / 2
        pass_edge = _PASSBAND_FRACTION * nyquist_low
        width = nyquist_low - pass_edge
        cutoff = (pass_edge + nyquist_low) / 2
        fs_up = self.in_rate * up
        # Kaiser 的長度與 beta 公式（Oppenheim & Schafer, Discrete-Time Signal Processing）。
        length = (
            math.ceil((attenuation_db - 7.95) / (2.285 * 2 * math.pi * width / fs_up))
            + 1
        )
        taps = math.ceil(length / up)
        total = taps * up
        beta = 0.1102 * (attenuation_db - 8.7)
        fc = cutoff / fs_up
        n = np.arange(total) - (total - 1) / 2
        proto = 2 * fc * np.sinc(2 * fc * n) * np.kaiser(total, beta)
        # 插零升頻會把能量除以 L，所以整體增益要乘回 L，讓每個相位的直流增益約為 1。
        proto *= up / proto.sum()
        phases = proto.reshape(taps, up).T  # phases[p, k] = proto[k * L + p]
        return np.ascontiguousarray(phases[:, ::-1])

    def process(self, block: npt.ArrayLike) -> FloatArray:
        """送入一段一維音訊，回傳新產生的輸出樣本（float64）。"""
        x = np.asarray(block, dtype=np.float64)
        if x.ndim != 1:
            raise ValueError(f"resampler expects 1-D audio, got shape {x.shape}")
        if self._passthrough:
            return x.copy()

        taps = self._taps_rev.shape[1]
        buf = np.concatenate([self._history, x])
        self._in_count += len(x)
        # 輸出 n 對應的最新輸入樣本是 floor(n·M/L)；它必須已經收到。
        n_end = -(-self._in_count * self._up // self._down)
        outputs: list[FloatArray] = []
        # 沒有新輸出時 buf 可能比濾波器短，不能建立滑動視窗。
        windows = sliding_window_view(buf, taps) if n_end > self._next_out else buf[:0]
        for batch_start in range(self._next_out, n_end, _OUTPUT_BATCH):
            n = np.arange(
                batch_start, min(batch_start + _OUTPUT_BATCH, n_end), dtype=np.int64
            )
            t = n * self._down
            newest = t // self._up
            phase = t % self._up
            rows = windows[newest - (taps - 1) - self._history_start]
            outputs.append(np.einsum("ij,ij->i", rows, self._taps_rev[phase]))
        self._next_out = n_end

        keep_from = (n_end * self._down) // self._up - (taps - 1)
        drop = min(max(keep_from - self._history_start, 0), len(buf))
        self._history = buf[drop:]
        self._history_start += drop
        return np.concatenate(outputs) if outputs else np.zeros(0)


def downmix_to_mono(frames: npt.ArrayLike) -> FloatArray:
    """把 (樣本數, 聲道數) 的音訊各聲道取平均成一維；一維輸入視為單聲道。"""
    x = np.asarray(frames, dtype=np.float64)
    if x.ndim == 1:
        return x.copy()
    if x.ndim != 2 or x.shape[1] == 0:
        raise ValueError(f"expected (frames, channels) audio, got shape {x.shape}")
    return x.mean(axis=1)


def float_to_int16(samples: npt.ArrayLike) -> AudioChunk:
    """把 [-1, 1] 的 float 音訊轉成 int16：乘 32768、四捨五入、截斷；NaN 視為 0。"""
    x = np.nan_to_num(
        np.asarray(samples, dtype=np.float64), nan=0.0, posinf=1.0, neginf=-1.0
    )
    return np.clip(np.rint(x * 32768.0), -32768, 32767).astype(np.int16)


class AudioConverter:
    """後端原始音訊 → 管線格式（16 kHz 單聲道 int16）的串流轉換器。

    同一個轉換器必須依序（單一執行緒）使用，因為重採樣狀態跨區塊保留。
    """

    def __init__(
        self, in_rate: int, channels: int, out_rate: int = SAMPLE_RATE
    ) -> None:
        if isinstance(channels, bool) or not isinstance(channels, int):
            raise TypeError(f"channels must be an int, got {channels!r}")
        if not 1 <= channels <= MAX_CHANNELS:
            raise ValueError(f"channels must be in [1, {MAX_CHANNELS}], got {channels}")
        self.channels = channels
        self._resampler = StreamingResampler(in_rate, out_rate)

    @property
    def in_rate(self) -> int:
        """輸入取樣率（Hz）。"""
        return self._resampler.in_rate

    def process(self, block: npt.ArrayLike) -> AudioChunk:
        """轉換一段 (樣本數, 聲道數) 的 float 音訊；單聲道可給一維陣列。"""
        x = np.asarray(block)
        if x.ndim == 1:
            if self.channels != 1:
                raise ValueError(f"expected {self.channels} channels, got 1-D audio")
        elif x.ndim == 2:
            if x.shape[1] != self.channels:
                raise ValueError(f"expected {self.channels} channels, got {x.shape[1]}")
        else:
            raise ValueError(f"expected (frames, channels) audio, got shape {x.shape}")
        return float_to_int16(self._resampler.process(downmix_to_mono(x)))
