"""逐句的線上說話者分群：每句一個向量，歸給最像的已知說話者或開一個新的人。

位於 speakers 模組的決策層，不碰音訊與模型：輸入是已正規化的說話者向量與句長，輸出是
1 起算的說話者編號。規則與門檻的依據見 `docs/specs/speaker-labels.md` 與
`experiments/speaker_diarization/README.md`（YODAS 日語拼接場景，CAM++ 門檻 0.4）。
"""

import numpy as np
import numpy.typing as npt

DEFAULT_THRESHOLD = 0.4
MIN_SECS = 1.0
"""短於這個長度的句子向量不可靠：不開新的人、不更新中心，只在夠像時標成已知的人。"""

MAX_SPEAKERS = 6
"""指定人數的上限；自動判斷時也最多開到這麼多人（避免雜訊句子一直開新的人）。"""


class OnlineSpeakers:
    """一個 session 的說話者中心。不是執行緒安全的（pipeline 只在完整路徑 worker 上用）。"""

    def __init__(
        self,
        count: int | None = None,
        *,
        threshold: float = DEFAULT_THRESHOLD,
        min_secs: float = MIN_SECS,
    ) -> None:
        """
        Args:
            count: 指定人數（2–`MAX_SPEAKERS`）；None 為自動判斷。
            threshold: 餘弦相似度門檻（-1–1）：最像的人低於它就當成新的人。
            min_secs: 句長短於它時不開新的人、不更新中心。

        Raises:
            ValueError: 參數超出範圍。
        """
        if count is not None and not 2 <= count <= MAX_SPEAKERS:
            raise ValueError(f"count must be 2-{MAX_SPEAKERS} or None, got {count}")
        if not -1.0 <= threshold <= 1.0:
            raise ValueError(f"threshold must be in [-1, 1], got {threshold}")
        if min_secs < 0:
            raise ValueError(f"min_secs must be >= 0, got {min_secs}")
        self._limit = MAX_SPEAKERS if count is None else count
        self._fixed = count is not None
        self._threshold = threshold
        self._min_secs = min_secs
        # 中心是向量總和：比較時再正規化，等同「所有句子的平均方向」
        self._centers: list[npt.NDArray[np.float32]] = []

    @property
    def speakers(self) -> int:
        """目前開了幾個人。"""
        return len(self._centers)

    def assign(self, embedding: npt.NDArray[np.float32], secs: float) -> int | None:
        """這句是誰（1 起算）；短句且沒有夠像的人時 None。

        Args:
            embedding: 一維、已正規化（L2 = 1）的說話者向量；維度必須與之前的相同。
            secs: 這句的長度（秒）。

        Raises:
            ValueError: 向量不是一維、維度不一致、或含 NaN／inf。
        """
        if embedding.ndim != 1:
            raise ValueError(f"embedding must be 1-D, got shape {embedding.shape}")
        if self._centers and embedding.shape != self._centers[0].shape:
            raise ValueError(
                f"embedding has {embedding.shape[0]} dims, expected "
                f"{self._centers[0].shape[0]}"
            )
        if not np.all(np.isfinite(embedding)):
            raise ValueError("embedding contains NaN or inf")
        short = secs < self._min_secs
        best, similarity = self._closest(embedding)
        full = len(self._centers) >= self._limit
        if best is not None and (similarity >= self._threshold or full):
            if short and not (similarity >= self._threshold or self._fixed):
                return None
            if not short:
                self._centers[best] = self._centers[best] + embedding
            return best + 1
        if short:
            return None
        self._centers.append(embedding.astype(np.float32, copy=True))
        return len(self._centers)

    def _closest(self, embedding: npt.NDArray[np.float32]) -> tuple[int | None, float]:
        if not self._centers:
            return None, -1.0
        centers = np.stack(self._centers)
        norms = np.maximum(np.linalg.norm(centers, axis=1), 1e-9)
        similarities = centers @ embedding / norms
        best = int(np.argmax(similarities))
        return best, float(similarities[best])
