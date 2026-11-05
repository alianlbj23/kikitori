"""混音：用背景素材產生評測音軌，並輸出對應的正確答案。

兩種版本（見 `docs/specs/vad-segmenter.md`「音軌版本」）：

- 間隔填入（`fill_gaps`）：只把句子之間的數位靜音換成背景素材，人聲樣本不變。
  測試背景聲會不會被當成人聲，以及重置時機的影響。
- 人聲底下疊加（`overlay`）：背景疊加在整條音軌，依 SNR 調整音量。測試有
  背景聲時能不能抓到人聲、會不會提早切斷。

兩者都不移動人聲位置，所以正確答案沿用原本 `ground_truth.json` 單一音軌的
格式，只多一個 `mix` 欄位記錄混音方式。SNR 的人聲功率只算正確人聲區間內的
樣本，避免靜音拉低平均。
"""

import copy
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import numpy as np
import numpy.typing as npt

Span = tuple[int, int]
_INT16_MAX = 32767.0


@dataclass(frozen=True)
class MixResult:
    """混音後的音軌與正確答案（`ground_truth.json` 中單一音軌的格式）。"""

    audio: npt.NDArray[np.int16]
    truth: dict[str, Any]


def segments_from_truth(track_truth: Mapping[str, Any]) -> list[Span]:
    """從 `ground_truth.json` 的單一音軌取出人聲區間並驗證。

    Raises:
        ValueError: 欄位缺少或型別不對、區間超出音軌、起訖顛倒、彼此重疊或未排序。
    """
    samples = track_truth.get("samples")
    raw = track_truth.get("segments")
    if not _is_int(samples) or samples <= 0 or not isinstance(raw, list):
        raise ValueError(
            "track truth needs int 'samples' > 0 and a 'segments' list, "
            f"got samples={samples!r}, segments={type(raw).__name__}"
        )
    spans: list[Span] = []
    for item in raw:
        start = item.get("start_sample") if isinstance(item, Mapping) else None
        end = item.get("end_sample") if isinstance(item, Mapping) else None
        if not (_is_int(start) and _is_int(end) and 0 <= start < end <= samples):
            raise ValueError(
                f"invalid segment {item!r} for a track of {samples} samples"
            )
        if spans and start < spans[-1][1]:
            raise ValueError(f"segment {item!r} overlaps or precedes {spans[-1]}")
        spans.append((start, end))
    return spans


def fill_gaps(
    track: npt.NDArray[np.int16],
    track_truth: Mapping[str, Any],
    background: npt.NDArray[np.int16],
    snr_db: float,
    label: str,
) -> MixResult:
    """把人聲區間以外的樣本換成背景素材；人聲樣本完全不變。

    背景音量依「人聲 RMS ÷ 背景 RMS = `snr_db`」設定，與 `overlay` 同一個
    基準，方便比較。背景比音軌短時循環使用。

    Raises:
        ValueError: 輸入不合法（見 `segments_from_truth`，以及長度、SNR、
            全零的人聲或背景）。
    """
    _, mask, bed = _prepare(track, track_truth, background, snr_db)
    gaps = ~mask
    # 只有間隔裡的背景會被聽到，所以以間隔內的 RMS 校正音量，SNR 才準。
    if not gaps.any():
        raise ValueError("track has no gaps between segments to fill")
    gap_rms = _rms(bed[gaps])
    if gap_rms == 0.0:
        raise ValueError("background is silent in the gaps between segments")
    bed *= _rms(bed) / gap_rms
    out = np.clip(np.round(bed), -_INT16_MAX - 1, _INT16_MAX).astype(np.int16)
    out[mask] = track[mask]
    return MixResult(out, _truth_with_mix(track_truth, "gap_fill", label, snr_db))


def overlay(
    track: npt.NDArray[np.int16],
    track_truth: Mapping[str, Any],
    background: npt.NDArray[np.int16],
    snr_db: float,
    label: str,
) -> MixResult:
    """把背景素材疊加在整條音軌上，人聲區間內的 SNR 為 `snr_db`。

    相加後若會超出 int16，整條混音一起等比例縮小（不截斷），SNR 不變。

    Raises:
        ValueError: 同 `fill_gaps`。
    """
    _, _, bed = _prepare(track, track_truth, background, snr_db)
    mixed = track.astype(np.float64) + bed
    peak = float(np.abs(mixed).max())
    if peak > _INT16_MAX:
        mixed *= _INT16_MAX / peak
    out = np.round(mixed).astype(np.int16)
    return MixResult(out, _truth_with_mix(track_truth, "overlay", label, snr_db))


def _prepare(
    track: npt.NDArray[np.int16],
    track_truth: Mapping[str, Any],
    background: npt.NDArray[np.int16],
    snr_db: float,
) -> tuple[list[Span], npt.NDArray[np.bool_], npt.NDArray[np.float64]]:
    for name, arr in (("track", track), ("background", background)):
        if not isinstance(arr, np.ndarray) or arr.ndim != 1 or arr.dtype != np.int16:
            raise ValueError(f"{name} must be a 1-D int16 array")
    if not (isinstance(snr_db, int | float) and math.isfinite(snr_db)):
        raise ValueError(f"snr_db must be a finite number, got {snr_db!r}")
    segments = segments_from_truth(track_truth)
    if len(track) != track_truth["samples"]:
        raise ValueError(
            f"track has {len(track)} samples but truth says {track_truth['samples']}"
        )
    mask = np.zeros(len(track), dtype=bool)
    for start, end in segments:
        mask[start:end] = True
    speech_rms = _rms(track[mask]) if mask.any() else 0.0
    if speech_rms == 0.0:
        raise ValueError("track has no non-zero speech inside the truth segments")
    bed = np.resize(background.astype(np.float64), len(track))
    bed_rms = _rms(bed)
    if bed_rms == 0.0:
        raise ValueError("background is silent or empty")
    bed *= speech_rms / bed_rms / 10.0 ** (snr_db / 20.0)
    return segments, mask, bed


def _truth_with_mix(
    track_truth: Mapping[str, Any], mode: str, label: str, snr_db: float
) -> dict[str, Any]:
    truth = copy.deepcopy(dict(track_truth))
    truth["mix"] = {"mode": mode, "background": label, "snr_db": float(snr_db)}
    return truth


def _rms(values: npt.NDArray[np.generic]) -> float:
    if values.size == 0:
        return 0.0
    return math.sqrt(float(np.mean(np.square(values, dtype=np.float64))))


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)
