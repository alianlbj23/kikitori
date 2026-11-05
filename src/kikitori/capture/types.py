"""capture 模組對外的契約：`AudioSource` 介面、`DeviceInfo` 與輸出格式常數。

位於整條管線的最前端；其他模組（pipeline、engine）只透過這裡的型別與
capture 互動。定義與 `docs/specs/app-architecture.md` 的 capture 一節一致。
"""

from dataclasses import dataclass
from typing import Literal, Protocol

import numpy as np
import numpy.typing as npt

SAMPLE_RATE = 16000
"""`AudioSource.read()` 回傳的音訊取樣率（Hz）。"""

DeviceKind = Literal["loopback", "microphone"]
DEVICE_KINDS: tuple[DeviceKind, ...] = ("loopback", "microphone")

AudioChunk = npt.NDArray[np.int16]
"""int16、16 kHz、單聲道、一維的音訊片段。"""


class AudioSource(Protocol):
    """產生 16 kHz 單聲道 int16 音訊的來源。"""

    def start(self) -> None:
        """開啟裝置並開始擷取；失敗時拋出 `CaptureError` 的子類別。"""
        ...

    def read(self, timeout: float) -> AudioChunk:
        """等待最多 `timeout` 秒，回傳累積的音訊；逾時回傳空陣列。"""
        ...

    def stop(self) -> None:
        """停止擷取並釋放裝置；可重複呼叫。"""
        ...


@dataclass(frozen=True)
class DeviceInfo:
    """可供擷取的裝置。`id` 為不透明字串，只能原樣傳回 `open_source()`。"""

    id: str
    name: str
    kind: DeviceKind
    is_default: bool
