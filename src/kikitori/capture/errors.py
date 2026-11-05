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


def detached_copy(error: CaptureError) -> CaptureError:
    """同類別、同訊息、同 `__cause__` 的新例外物件，沒有 traceback。

    給「存起來、之後在別的執行緒丟出」的錯誤用（開裝置失敗、擷取中斷）。重新丟出同一個
    物件時 Python 會把丟出處的 frame 接到它的 traceback 上，而保存它的 source／buffer 也被
    那些 frame 參照：pipeline 與 GPU 上的模型因此要等循環垃圾回收才釋放（memory_soak #1）。
    每次丟出一份新的複本，traceback 只跟著這次的例外，處理完就以參照計數釋放。

    建構子不接受原本的 `args` 時（自訂的子類別），往上找第一個能建構的 `CaptureError`
    類別，engine 依類別對應的錯誤碼盡量保留。
    """
    for cls in type(error).__mro__:
        if not (isinstance(cls, type) and issubclass(cls, CaptureError)):
            continue
        try:
            fresh = cls(*error.args)
        except TypeError:
            continue
        break
    else:  # pragma: no cover - CaptureError 本身一定能以任何 args 建構
        fresh = CaptureError(str(error))
    fresh.__cause__ = error.__cause__
    fresh.__suppress_context__ = True
    return fresh
