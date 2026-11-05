"""capture 模組的例外階層。

呼叫端（engine）依例外類別決定要回報給使用者的訊息，例如權限被拒時
提示到系統設定開啟權限、裝置消失時提示重新選擇裝置。
"""


class CaptureError(RuntimeError):
    """擷取失敗或使用狀態錯誤的共同基底類別。"""


class BackendUnavailableError(CaptureError):
    """此平台或此環境無法使用擷取後端（缺少系統函式庫、作業系統版本過舊等）。"""


class DeviceNotFoundError(CaptureError):
    """指定的裝置不存在，或沒有可用的預設裝置。"""


class PermissionDeniedError(CaptureError):
    """作業系統拒絕存取（麥克風或螢幕錄製權限）。"""


class DeviceLostError(CaptureError):
    """擷取途中裝置消失或串流被系統中止。"""
