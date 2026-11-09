"""一個 session 的說話者標籤：每句完整結果的音訊 → 說話者編號（1 起算）。

位於 speakers 模組的對外層：factory 在 session 開始時建立 `VoiceLabeler`（模型與分群狀態都屬於
這個 session），pipeline 的完整路徑 worker 每句呼叫一次 `label()`。只在單一 worker 執行緒上用。
"""

from collections.abc import Callable
from pathlib import Path

import numpy as np
import numpy.typing as npt

from kikitori.speakers.clustering import OnlineSpeakers
from kikitori.speakers.embedding import SpeakerEmbedder, create_session
from kikitori.speakers.model import (
    CAMPPLUS_ZH_EN,
    SpeakerModelSpec,
    read_verified_model,
)

SAMPLE_RATE = 16_000


class VoiceLabeler:
    """說話者向量加上線上分群。"""

    def __init__(
        self,
        embed: Callable[[npt.NDArray[np.int16]], npt.NDArray[np.float32]],
        speakers: OnlineSpeakers,
    ) -> None:
        """
        Args:
            embed: 一句音訊 → 正規化的說話者向量（`SpeakerEmbedder.embed`）。
            speakers: 這個 session 的分群狀態。
        """
        self._embed = embed
        self._speakers = speakers

    def label(self, audio: npt.NDArray[np.int16]) -> int | None:
        """這句是誰（1 起算）；短句且沒有夠像的人時 None。

        Raises:
            ValueError: 音訊或模型輸出不合法（見 `SpeakerEmbedder.embed`）。
            Exception: onnxruntime 推論失敗。
        """
        if len(audio) == 0:
            return None
        return self._speakers.assign(self._embed(audio), len(audio) / SAMPLE_RATE)


def load_labeler(
    count: int | None,
    *,
    root: Path | None = None,
    spec: SpeakerModelSpec = CAMPPLUS_ZH_EN,
) -> VoiceLabeler:
    """讀取並驗證已下載的模型，建立一個 session 用的 labeler。

    Args:
        count: 指定人數（2–6）；None 為自動判斷。
        root: 模型根目錄；省略時用 `model.default_root()`。
        spec: 模型。

    Raises:
        FileNotFoundError: 模型沒有下載。
        ModelIntegrityError: 模型檔的雜湊不符。
        ValueError: `count` 超出範圍或模型格式不符。
    """
    speakers = OnlineSpeakers(count)
    session = create_session(read_verified_model(root, spec))
    return VoiceLabeler(SpeakerEmbedder(session, spec.dims).embed, speakers)
