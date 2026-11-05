"""引擎協定（stdio NDJSON）的常數、請求解析與訊息格式。

位於 engine 的最外層：每一行 stdin 都是不可信的外部輸入，先在這裡驗證成
`Request`，不合法就變成帶錯誤碼的 `ProtocolError`，伺服器據此回錯誤而不會崩潰。
協定定義見 `docs/specs/app-architecture.md`，錯誤碼見 `docs/specs/pipeline-engine.md`。
"""

import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1
MAX_LINE_BYTES = 1024 * 1024
MAX_ID = 2**53 - 1
"""JavaScript 能精確表示的最大整數；Tauri 端以 JSON number 處理 id。"""

PARSE_ERROR = "parse_error"
INVALID_REQUEST = "invalid_request"
UNKNOWN_METHOD = "unknown_method"
INVALID_PARAMS = "invalid_params"
INVALID_SETTINGS = "invalid_settings"
SETTINGS_IO_ERROR = "settings_io_error"
SESSION_ACTIVE = "session_active"
DEVICE_NOT_FOUND = "device_not_found"
CAPTURE_UNAVAILABLE = "capture_unavailable"
CAPTURE_ERROR = "capture_error"
UNKNOWN_MODEL = "unknown_model"
DOWNLOAD_IN_PROGRESS = "download_in_progress"
INTERNAL_ERROR = "internal_error"


class ProtocolError(Exception):
    """要以錯誤回應回給呼叫端的錯誤。"""

    def __init__(self, code: str, message: str, request_id: int | None = None) -> None:
        """
        Args:
            code: 錯誤碼（本模組的常數）。
            message: 給人看的說明（不得含密鑰）。
            request_id: 能從請求取出 id 時填入，否則 None。
        """
        super().__init__(message)
        self.code = code
        self.message = message
        self.request_id = request_id


@dataclass(frozen=True)
class Request:
    """驗證過的請求。"""

    id: int
    method: str
    params: dict[str, Any]


def parse_request(line: bytes) -> Request:
    """把一行 stdin 解析並驗證成 `Request`。

    Raises:
        ProtocolError: `parse_error`（不是 UTF-8 JSON）或 `invalid_request`
            （不是物件、id／method／params 型別不對）。
    """
    try:
        data = json.loads(line.decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise ProtocolError(PARSE_ERROR, f"line is not valid UTF-8: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ProtocolError(PARSE_ERROR, f"line is not valid JSON: {exc}") from exc
    except RecursionError:
        raise ProtocolError(PARSE_ERROR, "JSON nested too deeply") from None
    if not isinstance(data, dict):
        raise ProtocolError(
            INVALID_REQUEST, f"request must be a JSON object, got {type(data).__name__}"
        )
    raw_id = data.get("id")
    if isinstance(raw_id, bool) or not isinstance(raw_id, int) or abs(raw_id) > MAX_ID:
        raise ProtocolError(INVALID_REQUEST, f"id must be an integer, got {raw_id!r}")
    unknown = set(data) - {"id", "method", "params"}
    if unknown:
        raise ProtocolError(
            INVALID_REQUEST, f"unknown request fields: {sorted(unknown)}", raw_id
        )
    method = data.get("method")
    if not isinstance(method, str) or not method:
        raise ProtocolError(
            INVALID_REQUEST,
            f"method must be a non-empty string, got {method!r}",
            raw_id,
        )
    params = data.get("params", {})
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise ProtocolError(
            INVALID_REQUEST,
            f"params must be an object, got {type(params).__name__}",
            raw_id,
        )
    return Request(raw_id, method, params)


def response(request_id: int, result: Mapping[str, Any]) -> dict[str, Any]:
    """成功回應。"""
    return {"id": request_id, "result": dict(result)}


def error_response(request_id: int | None, code: str, message: str) -> dict[str, Any]:
    """錯誤回應；無法取得請求 id 時 `id` 為 null。"""
    return {"id": request_id, "error": {"code": code, "message": message}}


def event(name: str, data: Mapping[str, Any]) -> dict[str, Any]:
    """事件（沒有 id）。"""
    return {"event": name, "data": dict(data)}


def reject_unknown_params(
    params: Mapping[str, Any], allowed: tuple[str, ...] = ()
) -> None:
    """有 `allowed` 以外的參數時丟 `invalid_params`。"""
    unknown = sorted(set(params) - set(allowed))
    if unknown:
        raise ProtocolError(INVALID_PARAMS, f"unknown parameters: {unknown}")
