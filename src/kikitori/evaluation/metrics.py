"""VAD 評測指標：逐格 ROC-AUC、誤報率、漏報率、開始延遲、提早切斷、漏抓。

輸入是每個 32 ms 視窗的人聲機率與 `Segmenter` 偵測到的句子，對照正確
答案（每段人聲的起訖樣本位置）。只用 numpy，不新增依賴；分母為 0 的情況
（例如全為人聲時的誤報率）回傳 NaN，由報告顯示為「—」。
"""

import math
from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES

Span = tuple[int, int]  # [start_sample, end_sample)

PREMATURE_TOLERANCE_MS = 100.0
MIN_MATCH_OVERLAP_MS = 250.0


@dataclass(frozen=True)
class Detection:
    """`Segmenter` 偵測到的一句。

    Attributes:
        start_sample: `UtteranceStarted.start_sample`（含前置緩衝）。
        end_sample: `UtteranceEnded.end_sample`。
        started_at: 送出 `UtteranceStarted` 時串流已處理到的樣本位置，
            也就是語音辨識最早能拿到這句的時間點；用來算開始延遲。
    """

    start_sample: int
    end_sample: int
    started_at: int


def frame_labels(num_windows: int, segments: Sequence[Span]) -> npt.NDArray[np.bool_]:
    """每個 512 樣本視窗是否為人聲：以視窗中心點是否落在任一正確區間內判斷。"""
    centers = np.arange(num_windows) * WINDOW_SAMPLES + WINDOW_SAMPLES // 2
    labels = np.zeros(num_windows, dtype=bool)
    for start, end in segments:
        labels |= (centers >= start) & (centers < end)
    return labels


def roc_auc(scores: npt.NDArray[np.floating], labels: npt.NDArray[np.bool_]) -> float:
    """以排名法（Mann–Whitney U）計算 ROC-AUC；同分取平均名次。

    只有單一類別時 AUC 沒有定義，回傳 NaN。

    Raises:
        ValueError: 兩個陣列長度不同。
    """
    scores = np.asarray(scores, dtype=np.float64)
    labels = np.asarray(labels, dtype=bool)
    if scores.shape != labels.shape or scores.ndim != 1:
        raise ValueError(
            f"scores and labels must be 1-D with equal length, "
            f"got {scores.shape} and {labels.shape}"
        )
    positives = int(labels.sum())
    negatives = len(labels) - positives
    if positives == 0 or negatives == 0:
        return math.nan
    ranks = _average_ranks(scores)
    rank_sum = float(ranks[labels].sum())
    return (rank_sum - positives * (positives + 1) / 2) / (positives * negatives)


def false_alarm_rate(
    probs: npt.NDArray[np.floating], labels: npt.NDArray[np.bool_], threshold: float
) -> float:
    """非人聲視窗中機率 ≥ `threshold` 的比例；沒有非人聲視窗時回傳 NaN。"""
    probs, labels = np.asarray(probs), np.asarray(labels, dtype=bool)
    negatives = ~labels
    if not negatives.any():
        return math.nan
    return float(np.mean(probs[negatives] >= threshold))


def miss_rate(
    probs: npt.NDArray[np.floating], labels: npt.NDArray[np.bool_], threshold: float
) -> float:
    """人聲視窗中機率 < `threshold` 的比例；沒有人聲視窗時回傳 NaN。"""
    probs, labels = np.asarray(probs), np.asarray(labels, dtype=bool)
    if not labels.any():
        return math.nan
    return float(np.mean(probs[labels] < threshold))


def onset_latencies_ms(
    segments: Sequence[Span], detections: Sequence[Detection]
) -> list[float]:
    """每段正確人聲從起點到對應 `UtteranceStarted` 送出的時間（ms）。

    對應的偵測 = 與該段實質重疊（至少 `min(250 ms, 段長一半)`，排除只擦到邊緣
    的誤報）的句子中最早開始的一句。以下情況不列入：沒有對應的句子（算在
    漏抓）；或對應的句子在這段開始前就已確認（與前一段併成一句，不是新的開始）。
    """
    latencies: list[float] = []
    for start, end in segments:
        needed = min(MIN_MATCH_OVERLAP_MS * SAMPLE_RATE / 1000.0, (end - start) / 2)
        candidates = [d for d in detections if _overlap((start, end), d) >= needed]
        if not candidates:
            continue
        match = min(candidates, key=lambda d: d.start_sample)
        if match.started_at < start:
            continue
        latencies.append((match.started_at - start) * 1000.0 / SAMPLE_RATE)
    return latencies


def median_and_p90(values: Sequence[float]) -> tuple[float, float]:
    """回傳中位數與第 90 百分位（線性內插）；沒有資料時回傳 (NaN, NaN)。"""
    if not values:
        return math.nan, math.nan
    median, p90 = np.percentile(np.asarray(values, dtype=np.float64), [50, 90])
    return float(median), float(p90)


def premature_cuts(segments: Sequence[Span], detections: Sequence[Detection]) -> int:
    """`UtteranceEnded` 落在某段正確人聲內部、且距該段終點超過 100 ms 的次數。"""
    tolerance = PREMATURE_TOLERANCE_MS * SAMPLE_RATE / 1000.0
    return sum(
        1
        for d in detections
        if any(
            start < d.end_sample and end - d.end_sample > tolerance
            for start, end in segments
        )
    )


def missed_segments(segments: Sequence[Span], detections: Sequence[Detection]) -> int:
    """沒有任何一句偵測與之重疊達該段一半長度的正確人聲段數。"""
    return sum(
        1
        for start, end in segments
        if not any(_overlap((start, end), d) * 2 >= end - start for d in detections)
    )


def _overlap(span: Span, detection: Detection) -> int:
    start, end = span
    return max(0, min(end, detection.end_sample) - max(start, detection.start_sample))


def _average_ranks(values: npt.NDArray[np.float64]) -> npt.NDArray[np.float64]:
    order = np.argsort(values, kind="mergesort")
    sorted_values = values[order]
    ranks = np.empty(len(values), dtype=np.float64)
    # 同分的一組給相同的平均名次（1 起算），這是排名法處理 tie 的標準做法。
    boundaries = np.flatnonzero(np.diff(sorted_values)) + 1
    starts = np.concatenate([[0], boundaries])
    ends = np.concatenate([boundaries, [len(values)]])
    for lo, hi in zip(starts, ends, strict=True):
        ranks[order[lo:hi]] = (lo + 1 + hi) / 2.0
    return ranks
