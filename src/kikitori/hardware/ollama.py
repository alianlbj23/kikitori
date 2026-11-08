"""Ollama 的原生 API：判斷翻譯伺服器是不是 Ollama、用 `/api/pull` 下載翻譯模型，
以及引擎結束時用 `/api/generate`（`keep_alive: 0`）卸載用過的模型。

位於 hardware 模組的安裝層（與 `gpu_runtime.py` 並列）：引擎的 `models.pull_translation`
在背景執行緒呼叫 `pull_model()`，`session.start` 前以 `ollama_version()` 判斷能不能替
使用者下載缺少的翻譯模型。設定存的是 OpenAI 相容端點（`http://host:11434/v1`），
Ollama 的原生 API 在同一個主機的根路徑（`/api/...`），所以先去掉結尾的 `/v1`。

只用標準庫 `http.client`（理由同 `translation_server.py`：不經 proxy、`localhost`
先試 IPv4）。`/api/pull` 以 NDJSON 串流回報進度，每行驗證後才使用；連線與每次讀取
各有逾時，讀取以短逾時輪詢，取消時最多約 0.2 秒就結束。不跟隨轉址（3xx）：轉址到
別的主機會把 `Authorization` 帶過去，而 Ollama 本身從不轉址，出現就是設定錯了。

協定以官方文件（docs.ollama.com/api：pull、version、generate、errors）與本機 Ollama 0.40.14（卸載為 0.40.15）的
實測為準，實測的串流內容記錄在 `docs/specs/hardware.md`。
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
    MAX_MODEL_NAME_CHARS,
    ConnectionFactory,
    default_connection,
    read_api_key,
)
from kikitori.translate.client import redact_url

logger = logging.getLogger(__name__)

VERSION_TIMEOUT_SECS = 2.0
UNLOAD_TIMEOUT_SECS = 1.0
"""`unload_model` 直接呼叫時的總時限（秒）；實測 45 ms。引擎結束時改傳它剩下的時間（`EngineServer.close()`）。"""
PULL_CONNECT_TIMEOUT_SECS = 5.0
PULL_IDLE_TIMEOUT_SECS = 120.0
"""兩行進度之間最久等多久。Ollama 下載中每秒都會回報；驗證大檔的 SHA-256 時會安靜
一陣子（數 GB 約十幾秒），所以留足餘裕。"""
MAX_LINE_BYTES = 64 * 1024
MAX_ERROR_BODY_BYTES = 64 * 1024
MAX_STATUS_CHARS = 200
MAX_VERSION_CHARS = 50
MAX_ERROR_CHARS = 300
MAX_LAYER_BYTES = 10**13
"""單一層的大小上限（10 TB）；超過就當成壞資料略過。"""
MAX_LAYERS = 512
"""一個模型最多幾層（blob）。實際的模型只有數層（實測 qwen3.5:4b 6 層）；不設上限的話，
有問題的伺服器可以送無數個不同的 digest，讓進度表無限長大、每行的加總越來越慢。"""
_CANCEL_POLL_SECS = 0.2
AUTH_STATUSES = frozenset({401, 403})
"""金鑰錯誤或缺少（反向代理常見）：伺服器可能就是 Ollama，只是無法確認。"""

_MODEL_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]*")
_DIGEST = re.compile(r"sha256:[0-9a-f]{64}")

ProgressCallback = Callable[[int, int], None]
"""`(completed_bytes, total_bytes)`：目前已知各層的合計（總量會隨新的層出現而變大）。"""


class OllamaError(RuntimeError):
    """與 Ollama 原生 API 溝通失敗。"""


class OllamaUnreachableError(OllamaError):
    """連不上翻譯伺服器（沒有開、位址錯誤、逾時，或回應沒在時限內讀完）。"""


class NotOllamaError(OllamaError):
    """伺服器有回應，但不是 Ollama（沒有 `/api/version`、內容不對或要求轉址），
    不能替使用者下載模型。"""


class OllamaAuthError(OllamaError):
    """伺服器回 401／403：金鑰錯誤或缺少。

    不歸類為 `NotOllamaError`：前面多半是要求金鑰的反向代理，後面可能就是 Ollama，
    只是現在無法確認；請使用者檢查 `translation.api_key_env` 指向的環境變數。
    """


class PullError(OllamaError):
    """`/api/pull` 失敗：伺服器回報錯誤、回應不合法、中途斷線或逾時。"""


class UnloadError(OllamaError):
    """`/api/generate` 的卸載請求被拒絕（不是 Ollama、模型不存在等）。"""


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

    網址中的帳密（`user:pass@`）不使用，也不會出現在錯誤訊息中。

    Raises:
        ValueError: 不是 http(s) 網址、沒有主機或連接埠不合法。
    """
    text = base_url.strip() if isinstance(base_url, str) else ""
    shown = redact_url(text)[:200]
    parts = urlsplit(text)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise ValueError(f"invalid translation server URL {shown!r}")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"invalid port in {shown!r}") from exc
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    elif port == 0:
        # urlsplit 接受 0，但 0 不是能連線的連接埠；不能悄悄換成預設值
        raise ValueError(f"invalid port in {shown!r}")
    prefix = parts.path.rstrip("/")
    prefix = prefix.removesuffix("/v1")
    return NativeRoot(parts.scheme, parts.hostname, port, prefix)


def validate_pull_model_name(name: object) -> str:
    """檢查要送給 `/api/pull` 下載的模型名稱（例如 `qwen3.5:9b`、`hf.co/org/repo:tag`）。

    比伺服器列出的名稱（`translation_server.is_listed_model_name()`，只用來顯示與比對）
    嚴格：這個名稱會由引擎轉送給伺服器、要求它下載。Ollama 的名稱只用英數與 `._:/-`；
    名稱只放進 JSON，不會進檔案路徑或命令列，但仍限制字元與長度，避免把任意文字
    （換行、`..`）轉送出去。整個字串都要符合（`fullmatch`：結尾的換行也不行）。

    Raises:
        ValueError: 不是字串、空白、太長或含不允許的字元。
    """
    if not isinstance(name, str) or not 0 < len(name) <= MAX_MODEL_NAME_CHARS:
        raise ValueError(
            f"model name must be a string of 1-{MAX_MODEL_NAME_CHARS} characters"
        )
    if not _MODEL_NAME.fullmatch(name) or ".." in name:
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
    root: NativeRoot,
    timeout: float,
    connect: ConnectionFactory,
    deadline: float | None = None,
) -> http.client.HTTPConnection:
    """建立連線；`localhost` 依序試 127.0.0.1、::1。

    每個位址最多 `timeout` 秒；有 `deadline`（`time.monotonic()` 的絕對時間）時，所有位址
    加起來也不超過它。

    Raises:
        OllamaUnreachableError: 每個位址都連不上，或時間用完。
    """
    hosts = (
        ["127.0.0.1", "::1"]
        if root.scheme == "http" and root.host.lower() == "localhost"
        else [root.host]
    )
    last_error = "no address to connect"
    for host in hosts:
        limit = timeout
        if deadline is not None:
            limit = min(timeout, deadline - time.monotonic())
            if limit <= 0:
                last_error = "timed out"
                break
        conn = connect(root.scheme, host, root.port, limit)
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


def _redirect_message(resp: http.client.HTTPResponse, path: str) -> str:
    location = resp.getheader("Location") or ""
    target = f" to {_clean(redact_url(location), 200)}" if location else ""
    return (
        f"HTTP {resp.status} redirect from {path}{target}; redirects are not "
        "followed, set the translation server URL to the final address"
    )


class _Status(NamedTuple):
    code: int
    redirect_message: str | None
    """3xx 時的錯誤說明（不跟隨轉址）；其他狀態為 None。"""


def _short_request(
    root: NativeRoot,
    method: str,
    api_path: str,
    headers: dict[str, str],
    payload: Mapping[str, Any] | None,
    timeout: float,
    connect: ConnectionFactory,
    *,
    total: bool = False,
) -> tuple[_Status, bytes]:
    """送一個小請求並在時限內讀完回應；回傳狀態與內容（只有 200 才讀內容）。

    預設連線、送出請求與讀完整個回應（標頭與內容）各自最多 `timeout` 秒；回應讀取的
    時限是整體的（不是每次 recv），伺服器一點一點送也不會無限拖延。`total=True` 時
    `timeout` 是整個請求（含 `localhost` 的兩個位址）的總時限，給只剩幾秒可用的呼叫端。

    Raises:
        OllamaUnreachableError: 連不上、沒回應，或回應沒在時限內讀完。
    """
    path = f"{root.prefix}{api_path}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers = {**headers, "Content-Type": "application/json"}
    deadline = time.monotonic() + timeout if total else None
    conn = _connect(root, timeout, connect, deadline)
    resp: http.client.HTTPResponse | None = None
    try:
        conn.request(method, path, body=body, headers=headers)
        sock = conn.sock
        if sock is None:
            raise OllamaUnreachableError(
                f"{root.host}:{root.port} closed the connection before responding"
            )
        read_deadline = time.monotonic() + timeout if deadline is None else deadline
        polling = _PollingSocket(
            sock,
            threading.Event(),
            max(0.001, read_deadline - time.monotonic()),
            deadline=read_deadline,
        )
        conn.sock = polling  # type: ignore[assignment]
        try:
            resp = conn.getresponse()
        except BaseException:
            polling.abort()  # 沒有回應物件會關閉讀取器（見 `_PollingSocket`）
            raise
        # 判斷只需要 200 的內容；其他狀態不讀內容，也就不會卡在讀取
        data = resp.read(MAX_ERROR_BODY_BYTES + 1) if resp.status == 200 else b""
        redirect = (
            _redirect_message(resp, api_path) if 300 <= resp.status < 400 else None
        )
        return _Status(resp.status, redirect), data
    except TimeoutError as exc:
        phase = (
            "a response" if resp is None else "the response body (the headers arrived)"
        )
        raise OllamaUnreachableError(
            f"timed out waiting for {phase} of {api_path} from "
            f"{root.host}:{root.port} after {timeout:g} s"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise OllamaUnreachableError(
            f"no response from {root.host}:{root.port}: {type(exc).__name__}"
        ) from exc
    finally:
        # `Connection: close` 時回應擁有 socket（見 `_PollingSocket`），要一併關閉
        if resp is not None:
            resp.close()
        conn.close()


def ollama_version(
    base_url: str,
    *,
    api_key_env: str = "",
    timeout: float = VERSION_TIMEOUT_SECS,
    env: Mapping[str, str] | None = None,
    connect: ConnectionFactory = default_connection,
) -> str:
    """`GET {root}/api/version`：伺服器是 Ollama 就回傳版本字串。

    連線、送出請求與讀完整個回應（標頭與內容）各自最多 `timeout` 秒；回應讀取的
    時限是整體的（不是每次 recv），伺服器一點一點送也不會無限拖延。

    Raises:
        ValueError: `base_url` 不合法，或金鑰環境變數含不合法的字元。
        OllamaUnreachableError: 連不上，或回應沒在時限內讀完（無法確認）。
        OllamaAuthError: 401／403（無法確認，見類別說明）。
        NotOllamaError: 有回應但不是 Ollama（其他狀態碼、轉址或內容不對）。
    """
    root = native_root(base_url)
    env = os.environ if env is None else env
    headers = _headers(root, api_key_env, env)
    status, body = _short_request(
        root, "GET", "/api/version", headers, None, timeout, connect
    )
    if status.code in AUTH_STATUSES:
        raise OllamaAuthError(
            f"HTTP {status.code} from /api/version: the server requires a valid "
            "API key (check the environment variable named in translation.api_key_env)"
        )
    if status.redirect_message is not None:
        raise NotOllamaError(status.redirect_message)
    if status.code != 200 or len(body) > MAX_ERROR_BODY_BYTES:
        raise NotOllamaError(f"HTTP {status.code} from /api/version")
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


def unload_model(
    base_url: str,
    model: str,
    *,
    api_key_env: str = "",
    timeout: float = UNLOAD_TIMEOUT_SECS,
    env: Mapping[str, str] | None = None,
    connect: ConnectionFactory = default_connection,
) -> None:
    """`POST {root}/api/generate {"model", "keep_alive": 0}`：請 Ollama 立刻卸載模型。

    官方文件的卸載方式（docs.ollama.com/api：generate 的 "Unload a model"）。沒有
    prompt 時只卸載、不會先載入；模型沒載入時同樣立即回 200（本機 0.40.15 實測）。
    引擎結束時用它釋放顯示記憶體，否則 Ollama 預設閒置 5 分鐘才卸載。`timeout` 是整個
    請求的總時限（連線、送出、讀回應加起來），引擎傳入它剩下的時間。

    Raises:
        ValueError: `base_url` 不合法，或金鑰環境變數含不合法的字元。
        OllamaUnreachableError: 連不上或沒在 `timeout` 內回應。
        OllamaAuthError: 401／403。
        UnloadError: 其他非 200 回應（例如不是 Ollama、模型不存在）。
    """
    root = native_root(base_url)
    env = os.environ if env is None else env
    headers = _headers(root, api_key_env, env)
    status, _ = _short_request(
        root,
        "POST",
        "/api/generate",
        headers,
        {"model": model, "keep_alive": 0},
        timeout,
        connect,
        total=True,
    )
    if status.code in AUTH_STATUSES:
        raise OllamaAuthError(f"HTTP {status.code} from /api/generate")
    if status.redirect_message is not None:
        raise UnloadError(status.redirect_message)
    if status.code != 200:
        raise UnloadError(f"HTTP {status.code} from /api/generate")


def _http_error(resp: http.client.HTTPResponse) -> OllamaError:
    """`/api/pull` 的非 200 回應 → 例外；官方格式是 `{"error": "..."}`（JSON，不是串流）。"""
    if 300 <= resp.status < 400:
        return PullError(_redirect_message(resp, "/api/pull"))
    try:
        body = resp.read(MAX_ERROR_BODY_BYTES)
        data = json.loads(body.decode("utf-8"))
        detail = data.get("error") if isinstance(data, dict) else None
    except (OSError, http.client.HTTPException, UnicodeDecodeError, ValueError):
        detail = None
    suffix = f": {_clean(detail)}" if isinstance(detail, str) else ""
    if resp.status in AUTH_STATUSES:
        return OllamaAuthError(
            f"HTTP {resp.status} from /api/pull{suffix} (the server requires a valid "
            "API key; check the environment variable named in translation.api_key_env)"
        )
    return PullError(f"HTTP {resp.status} from /api/pull{suffix}")


class _Layers:
    """各層（blob）的進度；Ollama 對每一層分別回報 total／completed。"""

    def __init__(self, max_layers: int = MAX_LAYERS) -> None:
        self._layers: dict[str, tuple[int, int]] = {}
        self._max_layers = max_layers

    def update(self, data: Mapping[str, Any]) -> bool:
        """套用一行進度；欄位不合法就略過（回傳 False）。

        Raises:
            PullError: 不同的層超過 `max_layers` 個。
        """
        digest, total, completed = (
            data.get("digest"),
            data.get("total"),
            data.get("completed", 0),
        )
        if not (isinstance(digest, str) and _DIGEST.fullmatch(digest)):
            return False
        for value in (total, completed):
            if isinstance(value, bool) or not isinstance(value, int):
                return False
        assert isinstance(total, int) and isinstance(completed, int)
        if not 0 <= total <= MAX_LAYER_BYTES or completed < 0:
            return False
        if digest not in self._layers and len(self._layers) >= self._max_layers:
            raise PullError(
                f"/api/pull reported more than {self._max_layers} layers; "
                "refusing to track more"
            )
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
    # 串流開始後狀態碼已經是 200，錯誤只會以最後一行 {"error": "..."} 出現（官方文件）
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
    `idle_timeout`（或超過整體的 `deadline`）才算逾時。

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
        deadline: float | None = None,
        on_close: Callable[[], None] | None = None,
    ) -> None:
        super().__init__()
        self._sock = sock
        self._on_close = on_close
        self._cancel = cancel
        self._idle_timeout = idle_timeout
        self._deadline = deadline
        sock.settimeout(min(poll, idle_timeout))

    def readable(self) -> bool:
        return True

    def readinto(self, buffer: Any) -> int:
        deadline = time.monotonic() + self._idle_timeout
        if self._deadline is not None:
            deadline = min(deadline, self._deadline)
        while True:
            if self._cancel.is_set():
                raise _Cancelled
            try:
                return self._sock.recv_into(buffer)
            except TimeoutError:
                if time.monotonic() >= deadline:
                    raise

    def close(self) -> None:
        if not self.closed:
            on_close, self._on_close = self._on_close, None
            super().close()
            if on_close is not None:
                on_close()


class _Cancelled(Exception):
    """`_PollingReader` 發現取消；轉成 `PullCancelledError`（帶模型名稱）。"""


class _PollingSocket:
    """給 `http.client.HTTPResponse` 用的 socket 替身：它只呼叫 `makefile("rb")`，
    連線關閉時呼叫 `close()`。

    與 `socket.makefile()` 相同，以參照計數延後關閉：回應帶 `Connection: close` 時
    `http.client` 在 `getresponse()` 裡就呼叫連線的 `close()`，之後才讀內容；立刻關掉
    真正的 socket 會讓讀內容失敗（WinError 10038），被誤判成「連不上」。所以 `close()`
    只標記，等最後一個讀取器也關閉時才關閉 socket。

    `getresponse()` 失敗（標頭不合法、中途斷線）時沒有回應物件可以關閉：CPython 3.12 的
    `getresponse()` 自己會關閉建到一半的回應，但這是實作細節，所以錯誤路徑另外呼叫
    `abort()`，不靠它也不等垃圾回收。
    """

    def __init__(
        self,
        sock: socket.socket,
        cancel: threading.Event,
        idle_timeout: float,
        *,
        deadline: float | None = None,
    ) -> None:
        self._sock = sock
        self._cancel = cancel
        self._idle_timeout = idle_timeout
        self._deadline = deadline
        self._lock = threading.Lock()
        self._readers: list[_PollingReader] = []
        """還沒關閉的讀取器（`abort()` 要能關閉它們）。"""
        self._closed = False
        self._sock_closed = False

    def makefile(self, mode: str, *args: object, **kwargs: object) -> io.BufferedReader:
        reader = _PollingReader(
            self._sock,
            self._cancel,
            self._idle_timeout,
            deadline=self._deadline,
            on_close=lambda: self._reader_closed(reader),
        )
        with self._lock:
            self._readers.append(reader)
        return io.BufferedReader(reader)

    def _reader_closed(self, reader: "_PollingReader") -> None:
        with self._lock:
            self._readers.remove(reader)
            close_now = self._closed and not self._readers
        if close_now:
            self._close_socket()

    def close(self) -> None:
        with self._lock:
            self._closed = True
            close_now = not self._readers
        if close_now:
            self._close_socket()

    def abort(self) -> None:
        """關閉所有讀取器與 socket（回應沒建成、沒有人會關閉讀取器時；可重複呼叫）。"""
        with self._lock:
            self._closed = True
            readers = list(self._readers)
        for reader in readers:
            reader.close()  # 最後一個關閉時經 _reader_closed 關閉 socket
        self._close_socket()

    def _close_socket(self) -> None:
        with self._lock:
            if self._sock_closed:
                return
            self._sock_closed = True
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
    """以 Ollama 的 `POST /api/pull`（`{"model", "stream": true}`）下載模型，直到伺服器
    回報 `{"status": "success"}`。

    已安裝的模型也會走完同樣的流程（Ollama 只檢查 manifest，實測約 1 秒就回報成功）。

    Args:
        base_url: 設定中的 OpenAI 相容 base URL。
        model: 模型名稱（先經 `validate_pull_model_name()`）。
        progress: 各層合計的 `(completed, total)`；只在進度改變時呼叫。
        cancel: 被設定時中止並丟 `PullCancelledError`。開始前、連線後、送出請求後
            都會檢查；連線與送出請求本身不能中斷，所以最久的延遲是「正在進行的那一步」
            的逾時：連線最多 `connect_timeout`（`localhost` 依序試兩個位址時最多兩倍），
            送出請求最多 `connect_timeout`；等待回應與讀取進度時約 0.2 秒。
        api_key_env: 環境變數有值時送 `Authorization`（Ollama 本身不需要）。
        connect_timeout: 建立連線與送出請求各自的上限（秒）。
        idle_timeout: 送出請求後，兩次收到資料之間的上限（秒）。
        env: 環境變數（測試用）。
        connect: 建立連線的函式（測試用）。

    Raises:
        ValueError: `base_url`、`model` 或金鑰環境變數不合法。
        OllamaUnreachableError: 連不上。
        OllamaAuthError: 401／403（金鑰錯誤或缺少）。
        PullError: 伺服器回報錯誤、轉址、回應不合法、中途斷線或逾時。
        PullCancelledError: 被取消。
    """
    model = validate_pull_model_name(model)
    root = native_root(base_url)
    env = os.environ if env is None else env

    def check_cancel() -> None:
        if cancel.is_set():
            raise PullCancelledError(f"pull of {model} cancelled")

    check_cancel()
    headers = _headers(root, api_key_env, env)
    headers["Content-Type"] = "application/json"
    body = json.dumps({"model": model, "stream": True}).encode("utf-8")
    conn = _connect(root, connect_timeout, connect)
    try:
        # 連線可能花掉數秒（connect_timeout）；這段期間被取消就不要再送出請求
        check_cancel()
        _send_pull(conn, root, headers, body, connect_timeout)
        check_cancel()
        _stream_pull(conn, root, progress, idle_timeout, cancel)
    except _Cancelled:
        raise PullCancelledError(f"pull of {model} cancelled") from None
    finally:
        conn.close()


def _send_pull(
    conn: http.client.HTTPConnection,
    root: NativeRoot,
    headers: dict[str, str],
    body: bytes,
    timeout: float,
) -> None:
    """送出請求（socket 逾時仍是 `timeout`）。

    Raises:
        PullError: 逾時或連線中斷（訊息說明是「送出請求」這一步）。
    """
    try:
        conn.request("POST", f"{root.prefix}/api/pull", body=body, headers=headers)
    except TimeoutError as exc:
        raise PullError(
            f"timed out sending the request to {root.host}:{root.port} "
            f"after {timeout:g} s"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise PullError(
            f"sending the request to {root.host}:{root.port} failed: "
            f"{type(exc).__name__}"
        ) from exc


def _stream_pull(
    conn: http.client.HTTPConnection,
    root: NativeRoot,
    progress: ProgressCallback,
    idle_timeout: float,
    cancel: threading.Event,
) -> None:
    layers = _Layers()
    last: tuple[int, int] | None = None
    sock = conn.sock
    if sock is None:
        raise PullError("the connection closed before the response")
    # 請求送出後才換成輪詢的替身：回應的讀取（含標頭）都能被取消
    polling = _PollingSocket(sock, cancel, idle_timeout)
    conn.sock = polling  # type: ignore[assignment]
    resp: http.client.HTTPResponse | None = None
    try:
        try:
            resp = conn.getresponse()
        except BaseException:
            polling.abort()  # 沒有回應物件會關閉讀取器（見 `_PollingSocket`）
            raise
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
            f"no data from {root.host}:{root.port} within {idle_timeout:g} s "
            "while waiting for progress"
        ) from exc
    except (OSError, http.client.HTTPException) as exc:
        raise PullError(
            f"connection to {root.host}:{root.port} failed: {type(exc).__name__}"
        ) from exc
    finally:
        # `Connection: close` 時連線把 socket 交給回應，`conn.close()` 不再關它；
        # 在這裡關閉回應，socket 才不會等到垃圾回收（見 `_PollingSocket`）
        if resp is not None:
            resp.close()
