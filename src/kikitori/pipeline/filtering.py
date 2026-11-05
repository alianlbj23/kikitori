"""pipeline 內建的非語音過濾：空字串與 `no_speech_probability` 門檻。

位於 pipeline 的決策層。真實元件由 factory 改用 asr 的 `rejection_reason()`
（含幻覺句、壓縮比等規則）；這裡的版本只依賴 `Transcript`，給測試與
沒有 asr 實作時使用。
"""

import math

from kikitori.asr.types import Transcript
from kikitori.pipeline.types import TranscriptFilter


def default_transcript_filter(no_speech_threshold: float) -> TranscriptFilter:
    """建立過濾器：文字為空白，或 `no_speech_probability > no_speech_threshold` 時丟掉。

    Raises:
        ValueError: 門檻不在 [0, 1]。
    """
    if (
        isinstance(no_speech_threshold, bool)
        or not isinstance(no_speech_threshold, int | float)
        or not math.isfinite(no_speech_threshold)
        or not 0.0 <= no_speech_threshold <= 1.0
    ):
        raise ValueError(
            f"no_speech_threshold must be in [0, 1], got {no_speech_threshold!r}"
        )

    def reject(transcript: Transcript) -> str | None:
        if not transcript.text.strip():
            return "empty"
        if transcript.no_speech_probability > no_speech_threshold:
            return "no_speech"
        return None

    return reject
