"""探測 OpenAI 相容翻譯伺服器（預設本機 Ollama）是否可連、裝了哪些模型。

位於 hardware 模組的偵測層：`detect()` 呼叫 `probe_translation_server()`，推薦時
用模型清單判斷建議的翻譯模型是否已安裝。用 `http.client` 而不是 `urllib`，理由同
translate 模組：不經過系統 proxy（本機 Ollama 不該走 proxy），並且能對 `localhost`
先試 IPv4（Windows 上 `localhost` 先解析成 ::1，Ollama 只聽 127.0.0.1，每次會白等約 2 秒）。
"""

import http.client
import ipaddress
import json
import logging
import os
import ssl
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

from kikitori.hardware.types import TranslationServerInfo

logger = logging.getLogger(__name__)

PROBE_TIMEOUT_SECS = 2.0
MAX_MODEL_LIST_BYTES = 1024 * 1024
MAX_MODELS = 1000
MAX_MODEL_NAME_CHARS = 200

ConnectionFactory = Callable[[str, str, int, float], http.client.HTTPConnection]
"""`(scheme, host, port, timeout)` → 尚未連線的 HTTP(S) 連線；測試可替換。"""


def default_connection(
    scheme: str, host: str, port: int, timeout: float
) -> http.client.HTTPConnection:
    """建立（尚未連線的）HTTP 或 HTTPS 連線；HTTPS 驗證憑證。"""
    if scheme == "https":
        return http.client.HTTPSConnection(
            host, port, timeout=timeout, context=ssl.create_default_context()
        )
    return http.client.HTTPConnection(host, port, timeout=timeout)


class ModelListError(ValueError):
    """`GET /models` 的回應不合法（太大、不是 JSON、格式不對）。"""


def is_loopback_host(host: str) -> bool:
    """`localhost`、127.0.0.0/8 或 ::1。"""
    if host.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def _valid_model_name(name: object) -> bool:
    return (
        isinstance(name, str)
        and 0 < len(name) <= MAX_MODEL_NAME_CHARS
        and name.isprintable()
        and name.strip() == name
    )


def parse_model_list(raw: bytes) -> tuple[str, ...]:
    """解析 OpenAI 相容 `GET /models` 的回應（`{"data": [{"id": ...}, ...]}`）。

    不合法的項目略過；整體格式不對時丟 `ModelListError`。
    """
    if len(raw) > MAX_MODEL_LIST_BYTES:
        raise ModelListError("model list response is too large")
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ModelListError(f"model list is not valid JSON: {exc}") from exc
    items = data.get("data") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ModelListError("model list has no 'data' array")
    names: list[str] = []
    for item in items[:MAX_MODELS]:
        name = item.get("id") if isinstance(item, dict) else None
        if _valid_model_name(name) and name not in names:
            names.append(name)
    return tuple(names)


def read_api_key(env: Mapping[str, str], api_key_env: str) -> str | None:
    """環境變數中的 API 金鑰（去頭尾空白）；沒設定時 None。

    Raises:
        ValueError: 金鑰含非 ASCII 或控制字元（不能放進 HTTP 標頭）。
    """
    key = env.get(api_key_env, "").strip()
    if not key:
        return None
    if not key.isascii() or not key.isprintable():
        raise ValueError(f"environment variable {api_key_env} has invalid characters")
    return key


def probe_translation_server(
    base_url: str,
    api_key_env: str = "",
    *,
    timeout: float = PROBE_TIMEOUT_SECS,
    env: Mapping[str, str] | None = None,
    connect: ConnectionFactory = default_connection,
) -> TranslationServerInfo:
    """`GET {base_url}/models`，回傳伺服器狀態；任何錯誤都記在 `error`，不丟例外。

    Args:
        base_url: OpenAI 相容 API 的 base URL，例如 `http://localhost:11434/v1`。
        api_key_env: 讀金鑰的環境變數名稱；有值才送 `Authorization`（Ollama 不需要）。
        timeout: 連線與讀取的逾時秒數。
        env: 環境變數（測試用）。
        connect: 建立連線的函式（測試用）。
    """
    env = os.environ if env is None else env
    text = base_url.strip() if isinstance(base_url, str) else ""
    parts = urlsplit(text)
    try:
        port = parts.port
    except ValueError:
        port = None
        parts = urlsplit("")
    host = parts.hostname or ""
    if parts.scheme not in ("http", "https") or not host:
        return TranslationServerInfo(
            base_url=text,
            is_local=False,
            reachable=False,
            error=f"invalid base_url {text[:200]!r}",
        )
    local = is_loopback_host(host)
    port = port or (443 if parts.scheme == "https" else 80)
    path = parts.path.rstrip("/") + "/models"
    hosts = (
        ["127.0.0.1", "::1"]
        if parts.scheme == "http" and host.lower() == "localhost"
        else [host]
    )

    try:
        key = read_api_key(env, api_key_env) if api_key_env else None
    except ValueError as exc:
        return TranslationServerInfo(text, local, reachable=False, error=str(exc))
    headers = {"Accept": "application/json", "User-Agent": "kikitori"}
    if key:
        headers["Authorization"] = f"Bearer {key}"
    if host.lower() == "localhost":
        headers["Host"] = "localhost" if port in (80, 443) else f"localhost:{port}"

    last_error = "no address to connect"
    for candidate in hosts:
        conn = connect(parts.scheme, candidate, port, timeout)
        try:
            conn.request("GET", path, headers=headers)
            resp = conn.getresponse()
            body = resp.read(MAX_MODEL_LIST_BYTES + 1)
        except (OSError, http.client.HTTPException) as exc:
            # 連不上就試下一個位址（只有 localhost 有第二個）；訊息不含標頭與金鑰
            last_error = f"cannot connect to {host}:{port}: {type(exc).__name__}: {exc}"
            continue
        finally:
            conn.close()
        if resp.status != 200:
            logger.info("translation server %s returned HTTP %d", host, resp.status)
            return TranslationServerInfo(
                text, local, reachable=True, error=f"HTTP {resp.status} from {path}"
            )
        try:
            models = parse_model_list(body)
        except ValueError as exc:
            return TranslationServerInfo(text, local, reachable=True, error=str(exc))
        return TranslationServerInfo(text, local, reachable=True, models=models)
    logger.info("translation server unreachable: %s", last_error)
    return TranslationServerInfo(text, local, reachable=False, error=last_error)
