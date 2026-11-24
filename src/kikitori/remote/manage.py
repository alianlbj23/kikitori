"""運算主機（B）被另一台電腦（A）管理模型時，實際套用的介面（docs/specs/compute-capacity.md「七」）。

位於 remote 模組，介於 `remote.server`（協定、驗證 A 的請求）與 B 的兩種宿主之間：
`RemoteServer` 檢查過模型是否在 registry、是否已下載、翻譯模型是否已安裝之後，交給
`HostManager` 寫設定或開始下載。兩種宿主用自己的方式做：

- GUI 引擎（`engine.remote_service`）：設定走引擎的設定檔，ASR 下載走既有的 `models.download`
  機制（B 的介面也收到 `download_progress`），並送 `remote` 事件 `host_changed` 給 B 的介面。
- 無介面主機（`remote.host`）：寫設定檔、直接下載，在終端機印出誰改了什麼與下載進度。

`fit`（跑不跑得動）不在這裡擋：超出負荷由 A 的介面警告，使用者執意要選仍然可以。
本模組只依賴標準函式庫，engine 與 remote.host 都能匯入它而不循環。
"""

import threading
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Literal, Protocol

DownloadState = Literal["downloading", "done", "error"]
ProgressFn = Callable[[int, int, DownloadState, str | None], None]
"""`(downloaded_bytes, total_bytes, state, error)`：下載執行緒回報進度；最後一定是 done 或 error。"""

ERROR_CODES = frozenset(
    {
        "unknown_model",
        "model_not_downloaded",
        "translation_model_missing",
        "download_in_progress",
        "settings_error",
    }
)
"""`host.error` 的 code（`remote.protocol.HOST_ERROR_CODES` 就是它）；A 只認得這幾種。"""

# 回給 A 的 `host.error.detail`：B 的例外原文可能含檔案路徑、使用者名稱，只寫進 B 的 log，
# A 只收到這些固定字句（A 的介面依 code 顯示白話說明，detail 只是補充）
SETTINGS_SAVE_DETAIL = "cannot save the compute host's settings"
SETTINGS_INVALID_DETAIL = (
    "the compute host's settings file is damaged; fix it on the compute host first"
)
DOWNLOAD_START_DETAIL = "cannot start the download on the compute host"
DOWNLOAD_FAILED_DETAIL = "the download on the compute host failed; see its log"
LIST_MODELS_DETAIL = "cannot list the downloaded models on the compute host"


class HostManageError(Exception):
    """A 的請求不能套用；`code` 原樣以 `host.error` 回給 A。"""

    def __init__(self, code: str, detail: str) -> None:
        """
        Raises:
            ValueError: `code` 不是 `ERROR_CODES` 之一（程式錯誤：A 只認得這幾種）。
        """
        if code not in ERROR_CODES:
            raise ValueError(f"unknown host error code {code!r}")
        super().__init__(detail)
        self.code = code
        self.detail = detail


class HostManager(Protocol):
    """B 的宿主實作：套用 A 要改的模型、開始下載。由連線執行緒呼叫，不能阻塞太久。"""

    def set_models(
        self, by: str, asr: Mapping[str, str], translation: Mapping[str, str]
    ) -> None:
        """寫進 B 的設定檔（下一場生效）並通知 B 的使用者。

        Args:
            by: 提出要求的電腦名稱（配對時記下的）。
            asr: 要改的 `fast_model`／`final_model`（已驗證：在 registry 且已下載）；可能是空的。
            translation: 要改的翻譯模型（已驗證：B 的翻譯伺服器已安裝）；可能是空的。

        Raises:
            HostManageError: `settings_error`（設定檔壞掉、設定不合法或寫不進去；`detail`
                是固定字句，不含路徑或例外原文）。
        """

    def start_download(self, by: str, model: str, progress: ProgressFn) -> None:
        """在背景開始下載 registry 裡的 ASR 模型後立刻返回。

        A 斷線不中斷下載（這是 B 自己的下載）。`progress` 在下載執行緒上呼叫，最後一定以
        done 或 error 結束。

        Raises:
            HostManageError: `download_in_progress`（同一個模型已在下載）、`unknown_model`、
                `settings_error`（下載執行緒開不起來；登記已撤銷）。
        """


@dataclass(frozen=True)
class DownloadSnapshot:
    """下載進度的一個快照。"""

    downloaded: int
    total: int
    state: DownloadState
    error: str | None


class ProgressRelay:
    """下載執行緒寫入最新進度，連線執行緒定期取走：不排隊，A 收得慢也不會卡住下載。"""

    def __init__(self) -> None:
        self._cond = threading.Condition()
        self._latest: DownloadSnapshot | None = None

    def update(
        self, downloaded: int, total: int, state: DownloadState, error: str | None
    ) -> None:
        """`ProgressFn`：記下最新進度；結束狀態之後的回報不理（結束只報一次）。"""
        with self._cond:
            if self._latest is not None and self._latest.state != "downloading":
                return
            self._latest = DownloadSnapshot(downloaded, total, state, error)
            if state != "downloading":
                self._cond.notify_all()

    def wait(self, timeout: float) -> DownloadSnapshot | None:
        """等到下載結束或 `timeout` 秒，回傳最新進度（還沒有任何回報時 None）。"""
        with self._cond:
            self._cond.wait_for(
                lambda: (
                    self._latest is not None and self._latest.state != "downloading"
                ),
                timeout,
            )
            return self._latest
