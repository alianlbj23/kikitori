"""翻譯模組的例外階層。

pipeline 只需要接 `TranslationError`；需要區分時再看子類別（例如逾時就略過
這句即時字幕、HTTP 401 就提示使用者檢查 API key）。
"""


class TranslationError(Exception):
    """翻譯失敗的共同基底。"""


class InvalidRequestError(TranslationError, ValueError):
    """請求不合法（空字串、過長、不支援的語言），不會送出網路請求。"""


class TranslationConnectionError(TranslationError):
    """無法連線、連線逾時或連線中途斷掉。屬暫時性錯誤，會重試。"""


class TranslationTimeoutError(TranslationError):
    """請求已送出，但等待回應時讀取逾時。"""


class TranslationHTTPError(TranslationError):
    """伺服器回傳 HTTP 4xx／5xx。"""

    def __init__(self, status: int, message: str) -> None:
        """
        Args:
            status: HTTP 狀態碼。
            message: 給人看的說明（已截斷並遮蔽金鑰）。
        """
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class TranslationResponseError(TranslationError):
    """回應格式不對：不是 JSON、缺欄位、串流異常結束或清理後為空。"""
