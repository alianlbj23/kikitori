"""Ollama 的原生 API：判斷翻譯伺服器是不是 Ollama，以及用 `/api/pull` 下載翻譯模型。

位於 hardware 模組的安裝層（與 `gpu_runtime.py` 並列）：引擎的 `models.pull_translation`
在背景執行緒呼叫 `pull_model()`，`session.start` 前以 `ollama_version()` 判斷能不能替
使用者下載缺少的翻譯模型。設定存的是 OpenAI 相容端點（`http://host:11434/v1`），
Ollama 的原生 API 在同一個主機的根路徑（`/api/...`），所以先去掉結尾的 `/v1`。

只用標準庫 `http.client`（理由同 `translation_server.py`：不經 proxy、`localhost`
先試 IPv4）。`/api/pull` 以 NDJSON 串流回報進度，每行驗證後才使用；連線與每次讀取
各有逾時，取消時從另一條執行緒關閉 socket，讓卡在讀取的請求立即結束。
"""

import http.client
import io
import json
import logging
import os
import re
import socket
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any, NamedTuple
from urllib.parse import urlsplit

from kikitori.hardware.translation_server import (
    ConnectionFactory,
    default_connection,
    read_api_key,
)

logger = logging.getLogger(__name__)

VERSION_TIMEOUT_SECS = 2.0
PULL_CONNECT_TIMEOUT_SECS = 5.0
PULL_IDLE_TIMEOUT_SECS = 120.0
"""兩行進度之間最久等多久。Ollama 下載中每秒都會回報；驗證大檔的 SHA-256 時會安靜
一陣子（數 GB 約十幾秒），所以留足餘裕。"""
MAX_LINE_BYTES = 64 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_MODEL_NAME_CHARS = 200
MAX_STATUS_CHARS = 200
MAX_VERSION_CHARS = 50
MAX_ERROR_CHARS = 300
MAX_LAYER_BYTES = 10**13
"""單一層的大小上限（10 TB）；超過就當成壞資料略過。"""
_CANCEL_POLL_SECS = 0.2

_MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]*$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")

ProgressCallback = Callable[[int, int], None]
"""`(completed_bytes, total_bytes)`：目前已知各層的合計（總量會隨新的層出現而變大）。"""


class OllamaError(RuntimeError):
    """與 Ollama 原生 API 溝通失敗。"""


class OllamaUnreachableError(OllamaError):
    """連不上翻譯伺服器（沒有開、位址錯誤、逾時）。"""


class NotOllamaError(OllamaError):
    """伺服器有回應，但不是 Ollama（沒有 `/api/version`），不能替使用者下載模型。"""


class PullError(OllamaError):
    """`/api/pull` 失敗：伺服器回報錯誤、回應不合法、中途斷線或逾時。"""


class PullCancelledError(PullError):
    """呼叫端要求取消（例如引擎結束）。"""


class NativeRoot(NamedTuple):
    """Ollama 原生 API 的位置。"""

    scheme: str
    host: str
    port: int
    prefix: str
    """路徑前綴（通常是空字串；反向代理時例如 "/ollama"）。"""


def native_root(base_url: str) -> NativeRoot:
    """OpenAI 相容的 base URL（`http://host:11434/v1`）→ Ollama 原生 API 的根。

    Raises:
        ValueError: 不是 http(s) 網址、沒有主機或連接埠不合法。
    """
    text = base_url.strip() if isinstance(base_url, str) else ""
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"invalid translation server URL {text[:200]!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid port in {text[:200]!r}") from exc
    prefix = parts.path.rstrip("/")
    prefix = prefix.removesuffix("/v1")
    return NativeRoot(
        parts.scheme,
        parts.hostname,
        port or (443 if parts.scheme == "https" else 80),
        prefix,
    )


def validate_model_name(name: object) -> str:
    """檢查要下載的模型名稱（例如 `qwen3.5:9b`、`hf.co/org/repo:tag`）。

    Ollama 的名稱只用英數與 `._:/-`；名稱會放進送給伺服器的 JSON，不會進檔案路徑或
    命令列，但仍限制字元與長度，避免把任意文字（換行、`..`）轉送出去。

    Raises:
        ValueError: 不是字串、空白、太長或含不允許的字元。
    """
    if not isinstance(name, str) or not 0 < len(name) <= MAX_MODEL_NAME_CHARS:
        raise ValueError(
            f"model name must be a string of 1-{MAX_MODEL_NAME_CHARS} characters"
        )
    if not _MODEL_NAME.match(name) or ".." in name:
        raise ValueError(f"invalid model name {name[:50]!r}")
    return name


def _clean(text: str, limit: int = MAX_ERROR_CHARS) -> str:
    """伺服器給的文字只保留可列印字元並截斷，才放進例外訊息與 log。"""
    return "".join(ch if ch.isprintable() else " " for ch in text[:limit]).strip()


def _headers(
    root: NativeRoot, api_key_env: str, env: Mapping[str, str]
) -> dict[str, str]:
    headers = {"Accept": "application/json", "User-Agent": "kikitori"}
    key = read_api_key(env, api_key_env) if api_key_env else None
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if root.host.lower() == "localhost":
        # 實際連到 127.0.0.1 或 ::1，Host 標頭仍寫使用者設定的名稱
        headers["Host"] = (
            "localhost" if root.port in (80, 443) else f"localhost:{root.port}"
        )
    return headers


def _connect(
    root: NativeRoot, timeout: float, connect: ConnectionFactory
) -> http.client.HTTPConnection:
    """建立連線（只受 `timeout` 限制）；`localhost` 依序試 127.0.0.1、::1。

    Raises:
        OllamaUnreachableError: 每個位址都連不上。
    """
    hosts = (
        ["127.0.0.1", "::1"]
        if root.scheme == "http" and root.host.lower() == "localhost"
        else [root.host]
    )
    last_error = "no address to connect"
    for host in hosts:
        conn = connect(root.scheme, host, root.port, timeout)
        try:
            conn.connect()
        except OSError as exc:
            conn.close()
            last_error = f"{type(exc).__name__}: {exc}"
            continue
        return conn
    raise OllamaUnreachableError(
        f"cannot connect to {root.host}:{root.port}: {_clean(last_error)}"
    )


def ollama_version(
    base_url: str,
    *,
    api_key_env: str = "",
    timeout: float = VERSION_TIMEOUT_SECS,
    env: Mapping[str, str] | None = None,
    connect: ConnectionFactory = default_connection,
) -> str:
    """`GET {root}/api/version`：伺服器是 Ollama 就回傳版本字串。

    Raises:
        ValueError: `base_url` 不合法。
        OllamaUnreachableError: 連不上。
        NotOllamaError: 有回應但不是 Ollama（狀態碼不是 200 或內容不對）。
    """
    root = native_root(base_url)
    env = os.environ if env is None else env
    conn = _connect(root, timeout, connect)
    try:
        conn.request(
            "GET",
            f"{root.prefix}/api/version",
            headers=_headers(root, api_key_env, env),
        )
        resp = conn.getresponse()
        body = resp.read(MAX_ERROR_BODY_BYTES + 1)
    except (OSError, http.client.HTTPException) as exc:
        raise OllamaUnreachableError(
            f"no response from {root.host}:{root.port}: {type(exc).__name__}"
        ) from exc
    finally:
        conn.close()
    if resp.status != 200 or len(body) > MAX_ERROR_BODY_BYTES:
        raise NotOllamaError(f"HTTP {resp.status} from /api/version")
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NotOllamaError("/api/version did not return JSON") from exc
    version = data.get("version") if isinstance(data, dict) else None
    if (
        not isinstance(version, str)
        or not 0 < len(version) <= MAX_VERSION_CHARS
        or not version.isprintable()
    ):
        raise NotOllamaError("/api/version has no version string")
    return version


def _http_error(resp: http.client.HTTPResponse) -> PullError:
    try:
        body = resp.read(MAX_ERROR_BODY_BYTES)
        data = json.loads(body.decode("utf-8"))
        detail = data.get("error") if isinstance(data, dict) else None
    except (OSError, http.client.HTTPException, UnicodeDecodeError, ValueError):
        detail = None
    suffix = f": {_clean(detail)}" if isinstance(detail, str) else ""
    return PullError(f"HTTP {resp.status} from /api/pull{suffix}")


class _Layers:
    """各層（blob）的進度；Ollama 對每一層分別回報 total／completed。"""

    def __init__(self) -> None:
        self._layers: dict[str, tuple[int, int]] = {}

    def update(self, data: Mapping[str, Any]) -> bool:
        """套用一行進度；欄位不合法就略過（回傳 False）。"""
        digest, total, completed = (
            data.get("digest"),
            data.get("total"),
            data.get("completed", 0),
        )
        if not (isinstance(digest, str) and _DIGEST.match(digest)):
            return False
        for value in (total, completed):
            if isinstance(value, bool) or not isinstance(value, int):
                return False
        assert isinstance(total, int) and isinstance(completed, int)
        if not 0 <= total <= MAX_LAYER_BYTES or completed < 0:
            return False
        self._layers[digest] = (min(completed, total), total)
        return True

    @property
    def totals(self) -> tuple[int, int]:
        return (
            sum(done for done, _ in self._layers.values()),
            sum(total for _, total in self._layers.values()),
        )


def _parse_line(line: bytes) -> dict[str, Any]:
    if len(line) > MAX_LINE_BYTES:
        raise PullError("progress line from /api/pull is too long")
    try:
        data = json.loads(line.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PullError("/api/pull returned a line that is not JSON") from exc
    if not isinstance(data, dict):
        raise PullError("/api/pull returned a line that is not an object")
    if "error" in data:
        error = data["error"]
        if not isinstance(error, str):
            raise PullError("/api/pull reported an error without a message")
        raise PullError(f"Ollama: {_clean(error)}")
    status = data.get("status")
    if status is not None and (
        not isinstance(status, str) or len(status) > MAX_STATUS_CHARS
    ):
        raise PullError("/api/pull returned an invalid status")
    return data


class _PollingReader(io.RawIOBase):
    """以短逾時輪詢的 socket 讀取：每 `poll` 秒檢查一次取消，累計沒收到資料超過
    `idle_timeout` 才算逾時。

    為什麼不用另一條執行緒關閉 socket：Windows 上 `shutdown()` 不會喚醒另一條執行緒中
    卡在 `recv()` 的讀取（實測），而 `makefile()` 持有參照時 `close()` 也不會真的關閉。
    為什麼不直接用 socket 逾時輪詢：`socket.makefile()` 在逾時一次後就不能再讀。
    """

    def __init__(
        self,
        sock: socket.socket,
        cancel: threading.Event,
        idle_timeout: float,
        poll: float = _CANCEL_POLL_SECS,
    ) -> None:
        super().__init__()
        self._sock = sock
        self._cancel = cancel
        self._idle_timeout = idle_timeout
        sock.settimeout(min(poll, idle_timeout))

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        deadline = time.monotonic() + self._idle_timeout
        while True:
            if self._cancel.is_set():
                raise _Cancelled
            try:
                return self._sock.recv_into(buffer)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise


class _Cancelled(Exception):
    """`_PollingReader` 發現取消；轉成 `PullCancelledError`（帶模型名稱）。"""


class _PollingSocket:
    """給 `http.client.HTTPResponse` 用的 socket 替身：它只呼叫 `makefile("rb")`，
    連線關閉時呼叫 `close()`。"""

    def __init__(
        self, sock: socket.socket, cancel: threading.Event, idle_timeout: float
    ) -> None:
        self._sock = sock
        self._cancel = cancel
        self._idle_timeout = idle_timeout

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BufferedReader:
        return io.BufferedReader(
            _PollingReader(self._sock, self._cancel, self._idle_timeout)
        )

    def close(self) -> None:
        self._sock.close()


def pull_model(
    base_url: str,
    model: str,
    *,
    progress: ProgressCallback,
    cancel: threading.Event,
    api_key_env: str = "",
    connect_timeout: float = PULL_CONNECT_TIMEOUT_SECS,
    idle_timeout: float = PULL_IDLE_TIMEOUT_SECS,
    env: Mapping[str, str] | None = None,
    connect: ConnectionFactory = default_connection,
) -> None:
    """以 Ollama 的 `POST /api/pull`（串流）下載模型，直到伺服器回報 `success`。

    已安裝的模型也會走完同樣的流程（Ollama 只檢查 manifest，很快就回報成功）。

    Args:
        base_url: 設定中的 OpenAI 相容 base URL。
        model: 模型名稱（先經 `validate_model_name()`）。
        progress: 各層合計的 `(completed, total)`；只在進度改變時呼叫。
        cancel: 被設定時中止（最多延遲約 0.2 秒），丟 `PullCancelledError`。
        api_key_env: 環境變數有值時送 `Authorization`（Ollama 本身不需要）。
        connect_timeout: 建立連線的上限（秒）。
        idle_timeout: 兩次收到資料之間的上限（秒）。
        env: 環境變數（測試用）。
        connect: 建立連線的函式（測試用）。

    Raises:
        ValueError: `base_url` 或 `model` 不合法。
        OllamaUnreachableError: 連不上。
        PullError: 伺服器回報錯誤、回應不合法、中途斷線或讀取逾時。
        PullCancelledError: 被取消。
    """
    model = validate_model_name(model)
    root = native_root(base_url)
    env = os.environ if env is None else env
    if cancel.is_set():
        raise PullCancelledError(f"pull of {model} cancelled")
    headers = _headers(root, api_key_env, env)
    headers["Content-Type"] = "application/json"
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")
    conn = _connect(root, connect_timeout, connect)
    try:
        _stream_pull(conn, root, headers, body, progress, idle_timeout, cancel)
    except _Cancelled:
        raise PullCancelledError(f"pull of {model} cancelled") from None
    finally:
        conn.close()


def _stream_pull(
    conn: http.client.HTTPConnection,
    root: NativeRoot,
    headers: dict[str, str],
    body: bytes,
    progress: ProgressCallback,
    idle_timeout: float,
    cancel: threading.Event,
) -> None:
    layers = _Layers()
    last: tuple[int, int] | None = None
    try:
        conn.request("POST", f"{root.prefix}/api/pull", body=body, headers=headers)
        sock = conn.sock
        if sock is None:
            raise PullError("the connection closed before the response")
        # 請求送出後才換成輪詢的替身：回應的讀取（含標頭）都能被取消
        conn.sock = _PollingSocket(sock, cancel, idle_timeout)  # type: ignore[assignment]
        resp = conn.getresponse()
        if resp.status != 200:
            raise _http_error(resp)
        while True:
            line = resp.readline(MAX_LINE_BYTES + 1)
            if not line:
                raise PullError("/api/pull ended before reporting success")
            if not line.strip():
                continue
            data = _parse_line(line)
            if data.get("status") == "success":
                return
            if layers.update(data) and layers.totals != last:
                last = layers.totals
                progress(*last)
    except TimeoutError as exc:
        raise PullError(
            f"no data from {root.host}:{root.port} within {idle_timeout:g} s"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise PullError(
            f"connection to {root.host}:{root.port} failed: {type(exc).__name__}"
        ) from exc
