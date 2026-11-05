"""VAD 切句：把連續的 16 kHz 單聲道 int16 音訊切成一句一句，輸出事件。

位於音訊擷取之後、語音辨識之前。輸入可以是任意長度，內部累積成 512 樣本
視窗交給 `VoiceModel`，再以遲滯門檻與 `EndpointPolicy` 決定句子的起訖。
時間一律用樣本計數（以串流開頭為 0），不用牆鐘時間，所以同一段音訊不論
分幾批、以多快的速度餵入，事件與位置都相同。
"""

import math
from collections import deque
from dataclasses import dataclass, field
from enum import Enum

import numpy as np
import numpy.typing as npt

from kikitori.vad.endpoint import EndpointPolicy, EndReason, PauseEndpoint
from kikitori.vad.model import SAMPLE_RATE, WINDOW_SAMPLES, VoiceModel

_SAMPLES_PER_MS = SAMPLE_RATE // 1000


@dataclass(frozen=True)
class UtteranceStarted:
    """確認開始有人聲。`start_sample` 已往前含前置緩衝。"""

    start_sample: int


@dataclass(frozen=True, eq=False)
class UtteranceAudio:
    """句子進行中新增的音訊，涵蓋 `[start_sample, start_sample + len(audio))`。

    同一句的所有 `UtteranceAudio` 首尾相接，合起來剛好是
    `[UtteranceStarted.start_sample, UtteranceEnded.end_sample)`。
    `audio` 是唯讀副本，呼叫端可以直接保存。
    """

    start_sample: int
    audio: npt.NDArray[np.int16] = field(repr=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, UtteranceAudio):
            return NotImplemented
        return self.start_sample == other.start_sample and np.array_equal(
            self.audio, other.audio
        )

    __hash__ = None  # type: ignore[assignment]  # 含可變陣列，不可雜湊


@dataclass(frozen=True)
class UtteranceEnded:
    """一句話結束。樣本位置以串流開頭為 0，`end_sample` 不含。"""

    start_sample: int
    end_sample: int
    reason: EndReason


SegmentEvent = UtteranceStarted | UtteranceAudio | UtteranceEnded


class ResetPolicy(Enum):
    """何時重置模型的內部狀態（Silero 的 state 與前文）。預設為 `AFTER_LONG_NON_SPEECH`。"""

    STREAM_START = "stream_start"
    """(a) 只在換新串流時重置（建構時與 `flush()` 後），官方建議的最低做法。"""

    AFTER_UTTERANCE = "after_utterance"
    """(b) 除了 (a)，每句結束（`flush` 以外的原因）後也重置。

    Silero 維護者在 discussion #791 對「每次 speech_end 後是否重置」回答 "Ideally yes"。
    """

    AFTER_LONG_NON_SPEECH = "after_long_non_speech"
    """(c) 除了 (a)，連續非人聲達 `reset_after_non_speech_secs` 且最近 5 個視窗
    機率都 < 0.1 時重置；之後每再經過同樣長度的非人聲才再重置。仿 pipecat 1.14.0。

    「非人聲」指沒有進行中的句子、也沒有候選人聲的視窗；句尾停頓仍屬於句子。
    """


# AFTER_LONG_NON_SPEECH 的「確實安靜」條件，與 pipecat 1.14.0 相同。
_QUIET_WINDOWS = 5
_QUIET_PROBABILITY = 0.1


class Segmenter:
    """把音訊切成句子，輸出 `UtteranceStarted` / `UtteranceAudio` / `UtteranceEnded`。

    API 為同步呼叫：`process()` 餵一段音訊、回傳這段音訊造成的事件；執行緒
    或 asyncio 的安排留給呼叫端。
    """

    def __init__(
        self,
        model: VoiceModel,
        *,
        reset_policy: ResetPolicy = ResetPolicy.AFTER_LONG_NON_SPEECH,
        endpoint: EndpointPolicy | None = None,
        threshold: float = 0.5,
        neg_threshold: float | None = None,
        min_speech_ms: float = 250.0,
        pre_roll_ms: float = 200.0,
        reset_after_non_speech_secs: float = 2.0,
    ) -> None:
        """
        Args:
            model: 逐視窗輸出人聲機率的模型。
            reset_policy: 何時重置模型狀態；預設 `AFTER_LONG_NON_SPEECH`。評測中它在
                長間隔的音樂背景下漏抓最少、開始延遲最短，其他情況與 (a) 相當
                （見 experiments/silero_reset/README.md）。
            endpoint: 句尾判斷；預設 `PauseEndpoint()`（靜音 0.6 秒、最長 10 秒）。
            threshold: 機率 ≥ 此值視為人聲（Silero 官方預設 0.5）。
            neg_threshold: 句子進行中機率 < 此值才算靜音；預設
                `max(threshold − 0.15, 0.01)`，與 Silero 官方相同。
            min_speech_ms: 人聲持續這麼久才確認句子開始。
            pre_roll_ms: 句子起點往前多含的音訊，避免切掉第一個音。
            reset_after_non_speech_secs: 僅 `AFTER_LONG_NON_SPEECH` 使用的重置間隔。

        Raises:
            TypeError: `reset_policy` 不是 `ResetPolicy`。
            ValueError: 參數超出範圍。
        """
        if not isinstance(reset_policy, ResetPolicy):
            raise TypeError(f"reset_policy must be a ResetPolicy, got {reset_policy!r}")
        if not (_is_finite(threshold) and 0.0 < threshold <= 1.0):
            raise ValueError(f"threshold must be in (0, 1], got {threshold!r}")
        if neg_threshold is None:
            neg_threshold = max(threshold - 0.15, 0.01)
        if not (_is_finite(neg_threshold) and 0.0 < neg_threshold < threshold):
            raise ValueError(
                f"neg_threshold must be in (0, threshold={threshold}), "
                f"got {neg_threshold!r}"
            )
        self._model = model
        self._reset_policy = reset_policy
        self._endpoint: EndpointPolicy = (
            endpoint if endpoint is not None else PauseEndpoint()
        )
        self._threshold = threshold
        self._neg_threshold = neg_threshold
        self._min_speech = _ms_to_samples("min_speech_ms", min_speech_ms)
        self._pre_roll = _ms_to_samples("pre_roll_ms", pre_roll_ms)
        if not (
            _is_finite(reset_after_non_speech_secs) and reset_after_non_speech_secs > 0
        ):
            raise ValueError(
                "reset_after_non_speech_secs must be a positive finite number, "
                f"got {reset_after_non_speech_secs!r}"
            )
        self._reset_after_samples = round(reset_after_non_speech_secs * SAMPLE_RATE)
        # 確認開始時要回補「前置緩衝 + 候選期間」的音訊，所以至少保留這麼多。
        self._history_capacity = self._pre_roll + self._min_speech + 2 * WINDOW_SAMPLES
        self._start_stream()

    def process(self, audio: npt.NDArray[np.int16]) -> list[SegmentEvent]:
        """餵入一段音訊（任意長度），回傳這段音訊造成的事件。

        Args:
            audio: 一維 int16 陣列，16 kHz 單聲道，接續上一次呼叫的結尾。

        Raises:
            TypeError: 不是 numpy 陣列。
            ValueError: 不是一維 int16。
        """
        if not isinstance(audio, np.ndarray):
            raise TypeError(f"audio must be a numpy array, got {type(audio).__name__}")
        if audio.ndim != 1 or audio.dtype != np.int16:
            raise ValueError(
                f"audio must be 1-D int16, got shape {audio.shape} dtype {audio.dtype}"
            )
        buffer = np.concatenate([self._pending, audio])
        count = len(buffer) // WINDOW_SAMPLES
        events: list[SegmentEvent] = []
        for i in range(count):
            window = buffer[i * WINDOW_SAMPLES : (i + 1) * WINDOW_SAMPLES]
            events += self._process_window(window)
        self._pending = buffer[count * WINDOW_SAMPLES :].copy()
        return events

    def request_end(self) -> None:
        """要求在下一個完整視窗處理完時結束目前這句，結束原因為 `external`。

        給之後的標點模型、Smart Turn 等外部判斷使用。沒有進行中的句子
        （包含人聲尚未持續到 `min_speech_ms` 的候選期間）時沒有效果，
        也不會延續到之後的句子。
        """
        if self._utterance_start is not None:
            self._end_requested = True

    def flush(self) -> list[SegmentEvent]:
        """串流結束：以 `flush` 結束進行中的句子，然後回到新串流的初始狀態。

        不足一個視窗的剩餘樣本無法送進模型；有進行中的句子時併入最後一段
        `UtteranceAudio`，否則捨棄。之後樣本位置從 0 重新計算。
        """
        events: list[SegmentEvent] = []
        if self._utterance_start is not None:
            leftover = self._pending
            if len(leftover):
                events.append(_audio_event(self._position, leftover))
            events.append(self._end("flush", self._position + len(leftover)))
        self._start_stream()
        return events

    def _start_stream(self) -> None:
        self._model.reset()
        self._pending = np.zeros(0, dtype=np.int16)
        self._position = 0
        self._history = np.zeros(0, dtype=np.int16)
        self._history_start = 0
        self._candidate_start: int | None = None
        self._utterance_start: int | None = None
        self._silence_start: int | None = None
        self._last_end = 0
        self._end_requested = False
        self._non_speech_samples = 0
        self._recent_probs: deque[float] = deque(maxlen=_QUIET_WINDOWS)

    def _process_window(self, window: npt.NDArray[np.int16]) -> list[SegmentEvent]:
        start = self._position
        end = start + WINDOW_SAMPLES
        self._position = end
        self._remember(window)
        prob = self._model.probability(window)
        self._recent_probs.append(prob)

        idle_before = self._utterance_start is None
        events = self._advance(prob, start, end, window)
        idle = (
            idle_before
            and self._utterance_start is None
            and self._candidate_start is None
        )
        self._apply_reset_policy(events, idle)
        return events

    def _apply_reset_policy(self, events: list[SegmentEvent], idle: bool) -> None:
        # 重置發生在這個視窗推論之後，所以影響的是下一個視窗。
        if self._reset_policy is ResetPolicy.AFTER_UTTERANCE:
            if any(isinstance(e, UtteranceEnded) for e in events):
                self._model.reset()
        elif self._reset_policy is ResetPolicy.AFTER_LONG_NON_SPEECH:
            self._non_speech_samples = (
                self._non_speech_samples + WINDOW_SAMPLES if idle else 0
            )
            quiet = len(self._recent_probs) == _QUIET_WINDOWS and all(
                p < _QUIET_PROBABILITY for p in self._recent_probs
            )
            if self._non_speech_samples >= self._reset_after_samples and quiet:
                self._model.reset()
                self._non_speech_samples = 0

    def _advance(
        self, prob: float, start: int, end: int, window: npt.NDArray[np.int16]
    ) -> list[SegmentEvent]:
        events: list[SegmentEvent] = []
        if self._utterance_start is None:
            self._track_candidate(prob, start, end, events)
            return events

        events.append(_audio_event(start, window))
        if prob >= self._threshold:
            self._silence_start = None
        elif prob < self._neg_threshold and self._silence_start is None:
            self._silence_start = start
        silence = 0 if self._silence_start is None else end - self._silence_start
        reason: EndReason | None = (
            "external"
            if self._end_requested
            else self._endpoint.decide(end - self._utterance_start, silence)
        )
        if reason is not None:
            events.append(self._end(reason, end))
        return events

    def _track_candidate(
        self, prob: float, start: int, end: int, events: list[SegmentEvent]
    ) -> None:
        # 遲滯：候選期間介於 neg_threshold 與 threshold 之間的機率不打斷候選。
        if self._candidate_start is None:
            if prob >= self._threshold:
                self._candidate_start = start
        elif prob < self._neg_threshold:
            self._candidate_start = None
        if (
            self._candidate_start is None
            or end - self._candidate_start < self._min_speech
        ):
            return
        # 前置緩衝不越過串流開頭與上一句的結尾，同一段音訊不會交給兩句。
        utterance_start = max(self._candidate_start - self._pre_roll, self._last_end)
        self._utterance_start = utterance_start
        self._candidate_start = None
        self._silence_start = None
        offset = utterance_start - self._history_start
        events.append(UtteranceStarted(utterance_start))
        events.append(_audio_event(utterance_start, self._history[offset:]))

    def _end(self, reason: EndReason, end: int) -> UtteranceEnded:
        if self._utterance_start is None:
            raise RuntimeError("no utterance in progress")
        event = UtteranceEnded(self._utterance_start, end, reason)
        self._utterance_start = None
        self._silence_start = None
        self._end_requested = False
        self._last_end = end
        return event

    def _remember(self, window: npt.NDArray[np.int16]) -> None:
        history = np.concatenate([self._history, window])
        drop = max(0, len(history) - self._history_capacity)
        self._history = history[drop:]
        self._history_start += drop


def _audio_event(start: int, samples: npt.NDArray[np.int16]) -> UtteranceAudio:
    audio = samples.copy()
    audio.setflags(write=False)
    return UtteranceAudio(start, audio)


def _is_finite(value: object) -> bool:
    return isinstance(value, int | float) and math.isfinite(value)


def _ms_to_samples(name: str, ms: float) -> int:
    if not (_is_finite(ms) and ms >= 0):
        raise ValueError(f"{name} must be a finite number >= 0, got {ms!r}")
    return round(ms * _SAMPLES_PER_MS)
