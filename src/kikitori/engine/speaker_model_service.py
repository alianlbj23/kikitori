"""引擎的說話者向量模型：`models.list`／`models.download`／`models.delete` 用的提供者。

位於 engine 層，介於伺服器與 `kikitori.speakers.model` 之間；測試以假的提供者取代，不碰磁碟與網路。
模型在 session 開始時整個讀進記憶體（`speakers.load_labeler`），之後不再開著檔案，所以刪除
不必等 session 結束；只有下載中不能刪。
"""

import threading
from collections.abc import Callable
from typing import Protocol

from kikitori.speakers.model import SpeakerModelStatus


class SpeakerModelProvider(Protocol):
    """說話者向量模型的狀態、下載與刪除。"""

    def status(self) -> SpeakerModelStatus:
        """是否已安裝、大小與授權（只看檔案，很快）。"""
        ...

    def install(
        self, progress: Callable[[int, int], None], cancel: threading.Event
    ) -> None:
        """下載並驗證模型；`cancel` 被設定時停止。失敗時丟例外。"""
        ...

    def delete(self) -> int:
        """刪除模型，回傳釋放的位元組數。

        Raises:
            FileNotFoundError: 沒有安裝。
            OSError、ValueError: 刪除失敗或路徑不安全。
        """
        ...


class DefaultSpeakerModelProvider:
    """真實的實作：模型快取旁的 `speakers/`。"""

    def status(self) -> SpeakerModelStatus:
        """見 `speakers.model.model_status()`。"""
        from kikitori.speakers.model import model_status

        return model_status()

    def install(
        self, progress: Callable[[int, int], None], cancel: threading.Event
    ) -> None:
        """見 `speakers.model.install_model()`。"""
        from kikitori.speakers.model import install_model

        install_model(progress=progress, cancel=cancel)

    def delete(self) -> int:
        """見 `speakers.model.delete_model()`。"""
        from kikitori.speakers.model import delete_model

        return delete_model()
