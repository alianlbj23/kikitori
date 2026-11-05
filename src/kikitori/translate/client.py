"""OpenAI 相容 Chat Completions 翻譯器：`Translator` 的預設實作。

pipeline 的即時路徑用 `stream()` 邊收邊更新字幕，完整路徑用 `translate()`。
預設連本機 Ollama（`http://localhost:11434/v1`），也能接其他相容服務。

HTTP 只用標準庫 `http.client`：只有一個 POST 端點與 SSE 解析，而且它能分開
設定連線 timeout 與讀取 timeout（`urllib` 只有一個共用的 timeout）。
"""

import http.client
import json
import logging
import math
import os
import re
import ssl
import time
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from kikitori.translate.errors import (
    TranslationConnectionError,
    TranslationError,
    TranslationHTTPError,
    TranslationResponseError,
    TranslationTimeoutError,
)
from kikitori.translate.languages import DEFAULT_MAX_CONTEXT_LINES, validate_request
from kikitori.translate.postprocess import finalize_translation
from kikitori.translate.presets import ModelPreset, preset_for_model
from kikitori.translate.prompts import build_messages
from kikitori.translate.types import TranslationRequest

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "http://localhost:11434/v1"
DEFAULT_API_KEY_ENV = "KIKITORI_TRANSLATE_API_KEY"

MAX_RESPONSE_BYTES = 1_000_000
"""非串流回應與單一 SSE 行的大小上限；一句字幕的回應遠小於此。"""
MAX_ERROR_DETAIL_CHARS = 300
MAX_RETRY_DELAY = 8.0
"""重試等待上限（秒）：再等下去字幕早就過時了。"""

# 408／429 與閘道類 5xx 通常稍等就好（例如 Ollama 正在載入模型）；501 等不會。
_RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
_ENV_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_USER_AGENT = "kikitori-translate"


def _check_positive(name: str, value: object, upper: float) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or not 0 < value <= upper
    ):
        raise ValueError(f"{name} must be a number in (0, {upper}], got {value!r}")


def _check_int(name: str, value: object, lower: int, upper: int) -> None:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not lower <= value <= upper
    ):
        raise ValueError(f"{name} must be an int in [{lower}, {upper}], got {value!r}")


@dataclass(frozen=True)
class TranslatorConfig:
    """翻譯器設定（可存進設定檔；不含金鑰本身）。

    Attributes:
        model: 模型名稱（Ollama 的 `name:tag`）。
        base_url: OpenAI 相容 API 的根網址，請求送到 `{base_url}/chat/completions`。
        api_key_env: 存 API key 的環境變數名稱；變數沒設定就不送 Authorization。
        temperature: None 表示用模型預設組的建議值。
        connect_timeout: 建立連線的上限（秒）。
        read_timeout: 送出請求後每次讀取的上限（秒）。
        max_retries: 暫時性錯誤的重試次數（不含第一次）。
        retry_backoff: 指數退避的基數（秒）。
        max_tokens: 回應長度上限，避免模型失控輸出長文。
        max_context_lines: 前文最多帶幾句。
        convert_to_traditional: 目標為 zh-TW 時是否以 OpenCC 轉成台灣正體。

    Raises:
        ValueError: 欄位值超出範圍或格式不對（數值欄位型別不對也算）。
        TypeError: `base_url` 不是字串或 `convert_to_traditional` 不是 bool。
    """

    model: str
    base_url: str = DEFAULT_BASE_URL
    api_key_env: str = DEFAULT_API_KEY_ENV
    temperature: float | None = None
    connect_timeout: float = 3.0
    read_timeout: float = 30.0
    max_retries: int = 2
    retry_backoff: float = 0.5
    max_tokens: int = 256
    max_context_lines: int = DEFAULT_MAX_CONTEXT_LINES
    convert_to_traditional: bool = True

    def __post_init__(self) -> None:
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("model must be a non-empty str")
        if not self.model.isprintable():
            raise ValueError("model must not contain control characters")
        _parse_base_url(self.base_url)
        if not isinstance(self.api_key_env, str) or not _ENV_NAME.match(
            self.api_key_env
        ):
            raise ValueError(
                f"api_key_env is not a valid variable name: {self.api_key_env!r}"
            )
        if self.temperature is not None and (
            isinstance(self.temperature, bool)
            or not isinstance(self.temperature, (int, float))
            or not math.isfinite(self.temperature)
            or not 0 <= self.temperature <= 2
        ):
            raise ValueError(f"temperature must be in [0, 2], got {self.temperature!r}")
        _check_positive("connect_timeout", self.connect_timeout, 600)
        _check_positive("read_timeout", self.read_timeout, 600)
        _check_int("max_retries", self.max_retries, 0, 10)
        if (
            isinstance(self.retry_backoff, bool)
            or not isinstance(self.retry_backoff, (int, float))
            or not math.isfinite(self.retry_backoff)
            or not 0 <= self.retry_backoff <= 60
        ):
            raise ValueError(
                f"retry_backoff must be in [0, 60], got {self.retry_backoff!r}"
            )
        _check_int("max_tokens", self.max_tokens, 1, 8192)
        _check_int("max_context_lines", self.max_context_lines, 0, 20)
        if not isinstance(self.convert_to_traditional, bool):
            raise TypeError("convert_to_traditional must be bool")


@dataclass(frozen=True)
class _Endpoint:
    https: bool
    host: str
    port: int
    path: str


def _parse_base_url(base_url: object) -> _Endpoint:
    if not isinstance(base_url, str):
        raise TypeError("base_url must be str")
    parts = urlsplit(base_url.strip())
    if parts.scheme not in ("http", "https"):
        raise ValueError(
            f"base_url must start with http:// or https://, got {base_url!r}"
        )
    if parts.query or parts.fragment:
        raise ValueError("base_url must not contain a query or fragment")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(f"base_url has an invalid port: {base_url!r}") from exc
    if not parts.hostname:
        raise ValueError(f"base_url has no host: {base_url!r}")
    https = parts.scheme == "https"
    return _Endpoint(
        https=https,
        host=parts.hostname,
        port=port or (443 if https else 80),
        path=parts.path.rstrip("/") + "/chat/completions",
    )


class _RetryableHTTPError(TranslationHTTPError):
    """可重試的 HTTP 錯誤；帶伺服器建議的等待秒數。"""

    def __init__(self, status: int, message: str, retry_after: float | None) -> None:
        super().__init__(status, message)
        self.retry_after = retry_after


class OpenAICompatibleTranslator:
    """透過 OpenAI 相容 Chat Completions API 翻譯，實作 `Translator`。

    不保存跨請求狀態，可以被多個執行緒同時使用。
    """

    def __init__(
        self,
        config: TranslatorConfig,
        preset: ModelPreset | None = None,
        *,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        """
        Args:
            config: 連線與生成設定。
            preset: 提示詞格式與取樣參數；None 時依 `config.model` 查預設組，
                不認得的模型用通用格式。
            sleep: 重試等待用的函式；測試時換成不真的等待的版本。
        """
        self.config = config
        self.preset = preset or preset_for_model(config.model)
        self._endpoint = _parse_base_url(config.base_url)
        self._sleep = sleep

    def __repr__(self) -> str:
        return f"OpenAICompatibleTranslator(model={self.config.model!r}, base_url={self.config.base_url!r})"

    def translate(self, request: TranslationRequest) -> str:
        """送出非串流請求，回傳整理後的完整譯文。

        Raises:
            InvalidRequestError: 請求不合法（不會送出網路請求）。
            TranslationError: 連線、逾時、HTTP 錯誤或回應格式錯誤（見 `errors`）。
        """
        prepared = self._prepare(request)
        req = prepared[0]
        conn, resp = self._open(self._payload(prepared, stream=False), stream=False)
        try:
            raw = self._read_all(resp)
        finally:
            conn.close()
        content, finish = _parse_completion(raw)
        if finish == "length":
            logger.warning(
                "translation truncated by max_tokens=%d", self.config.max_tokens
            )
        text = finalize_translation(
            content, req.target_language, self.config.convert_to_traditional
        )
        if not text:
            raise TranslationResponseError("model returned an empty translation")
        return text

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        """以 SSE 串流翻譯；每次產出「到目前為止的完整譯文」（已整理）。

        請求驗證在呼叫時立即進行；網路請求在第一次迭代時才送出。只有在產出第一個
        片段之前才會重試。中途不要了（例如即時字幕已過時）就呼叫產生器的
        `close()`，連線會隨之關閉。

        Raises:
            InvalidRequestError: 請求不合法（呼叫時立即丟出）。
            TranslationError: 迭代時的連線、逾時、HTTP 錯誤或回應格式錯誤。
        """
        return self._stream(self._prepare(request))

    def _prepare(
        self, request: TranslationRequest
    ) -> tuple[TranslationRequest, list[dict[str, str]]]:
        context_lines = (
            self.config.max_context_lines if self.preset.supports_context else 0
        )
        req = validate_request(request, self.preset.language_pairs, context_lines)
        return req, build_messages(self.preset.prompt_style, req)

    def _payload(
        self, prepared: tuple[TranslationRequest, list[dict[str, str]]], stream: bool
    ) -> bytes:
        _, messages = prepared
        temperature = self.config.temperature
        payload: dict[str, Any] = {
            "model": self.config.model,
            "messages": messages,
            "stream": stream,
            "temperature": self.preset.temperature
            if temperature is None
            else temperature,
            "max_tokens": self.config.max_tokens,
        }
        if self.preset.top_p is not None:
            payload["top_p"] = self.preset.top_p
        if self.preset.presence_penalty is not None:
            payload["presence_penalty"] = self.preset.presence_penalty
        if self.preset.reasoning_effort is not None:
            payload["reasoning_effort"] = self.preset.reasoning_effort
        return json.dumps(payload, ensure_ascii=False).encode("utf-8")

    def _stream(
        self, prepared: tuple[TranslationRequest, list[dict[str, str]]]
    ) -> Iterator[str]:
        req, _ = prepared
        conn, resp = self._open(self._payload(prepared, stream=True), stream=True)
        try:
            raw = ""
            last = ""
            finish: str | None = None
            done = False
            for data in _iter_sse(resp):
                if data == "[DONE]":
                    done = True
                    break
                content, chunk_finish = _parse_chunk(data, self._redact)
                finish = chunk_finish or finish
                if not content:
                    continue
                raw += content
                if len(raw) > MAX_RESPONSE_BYTES:
                    raise TranslationResponseError("streamed response too large")
                text = finalize_translation(
                    raw, req.target_language, self.config.convert_to_traditional
                )
                if text and text != last:
                    last = text
                    yield text
            if not done and finish is None:
                raise TranslationResponseError(
                    "stream ended before the response was complete"
                )
            if finish == "length":
                logger.warning(
                    "translation truncated by max_tokens=%d", self.config.max_tokens
                )
            if not last:
                raise TranslationResponseError("model returned an empty translation")
        finally:
            conn.close()

    # --- HTTP ---------------------------------------------------------------

    def _open(
        self, body: bytes, stream: bool
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        """送出請求並回傳 2xx 回應；暫時性錯誤依設定重試。"""
        attempt = 0
        while True:
            try:
                return self._open_once(body, stream)
            except (TranslationConnectionError, _RetryableHTTPError) as exc:
                if attempt >= self.config.max_retries:
                    raise
                delay = min(self.config.retry_backoff * (2**attempt), MAX_RETRY_DELAY)
                retry_after = getattr(exc, "retry_after", None)
                if retry_after is not None:
                    delay = min(max(delay, retry_after), MAX_RETRY_DELAY)
                attempt += 1
                # 只記狀態碼或例外類別：錯誤內容來自伺服器（可能夾帶金鑰），不放進 log。
                logger.warning(
                    "translation request failed (%s); retry %d/%d in %.2fs",
                    f"HTTP {exc.status}"
                    if isinstance(exc, TranslationHTTPError)
                    else type(exc).__name__,
                    attempt,
                    self.config.max_retries,
                    delay,
                )
                self._sleep(delay)

    def _open_once(
        self, body: bytes, stream: bool
    ) -> tuple[http.client.HTTPConnection, http.client.HTTPResponse]:
        ep = self._endpoint
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream" if stream else "application/json",
            "User-Agent": _USER_AGENT,
        }
        key = self._api_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"

        target = f"{ep.host}:{ep.port}"
        conn = self._connect()
        if not ep.https and ep.host.lower() == "localhost":
            # 實際連到的是 127.0.0.1 或 ::1，Host 標頭仍要寫使用者設定的名稱。
            headers["Host"] = "localhost" if ep.port == 80 else f"localhost:{ep.port}"

        # 連線已建立，之後每次讀取改用 read_timeout（模型生成比建立連線慢得多）。
        if conn.sock is not None:
            conn.sock.settimeout(self.config.read_timeout)
        try:
            conn.request("POST", ep.path, body=body, headers=headers)
            resp = conn.getresponse()
        except TimeoutError as exc:
            conn.close()
            raise TranslationTimeoutError(
                f"no response from {target} within {self.config.read_timeout}s"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise TranslationConnectionError(
                f"connection to {target} failed: {type(exc).__name__}"
            ) from exc

        if 200 <= resp.status < 300:
            return conn, resp
        try:
            detail = self._describe_error(resp.read(64 * 1024))
        except (OSError, http.client.HTTPException) as exc:
            detail = f"(error body unreadable: {type(exc).__name__})"
        finally:
            conn.close()
        if resp.status in _RETRYABLE_STATUS:
            raise _RetryableHTTPError(
                resp.status, detail, _parse_retry_after(resp.getheader("Retry-After"))
            )
        raise TranslationHTTPError(resp.status, detail)

    def _connect(self) -> http.client.HTTPConnection:
        """建立連線（只受 connect_timeout 限制）。"""
        ep = self._endpoint
        if ep.https:
            hosts = [ep.host]
        elif ep.host.lower() == "localhost":
            # Windows 上 localhost 先解析成 ::1，而 Ollama 預設只聽 127.0.0.1；
            # 被拒的 IPv6 連線在 Windows 要約 2 秒才失敗，每個請求都多等 2 秒（實測）。
            # 所以先試 IPv4。HTTPS 不改寫，以免憑證主機名稱比對失敗。
            hosts = ["127.0.0.1", "::1"]
        else:
            hosts = [ep.host]

        last_error: OSError | None = None
        for host in hosts:
            conn: http.client.HTTPConnection
            if ep.https:
                conn = http.client.HTTPSConnection(
                    host,
                    ep.port,
                    timeout=self.config.connect_timeout,
                    context=ssl.create_default_context(),
                )
            else:
                conn = http.client.HTTPConnection(
                    host, ep.port, timeout=self.config.connect_timeout
                )
            try:
                conn.connect()
            except OSError as exc:  # 含 TimeoutError（連線逾時）
                conn.close()
                last_error = exc
                continue
            return conn
        target = f"{ep.host}:{ep.port}"
        if isinstance(last_error, TimeoutError):
            raise TranslationConnectionError(
                f"connecting to {target} timed out"
            ) from last_error
        raise TranslationConnectionError(
            f"cannot connect to {target}: {type(last_error).__name__}"
        ) from last_error

    def _read_all(self, resp: http.client.HTTPResponse) -> bytes:
        try:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
        except TimeoutError as exc:
            raise TranslationTimeoutError(
                "timed out while reading the response"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TranslationConnectionError(
                f"connection lost while reading: {type(exc).__name__}"
            ) from exc
        if len(data) > MAX_RESPONSE_BYTES:
            raise TranslationResponseError("response too large")
        return data

    # --- 金鑰 ---------------------------------------------------------------

    def _api_key(self) -> str | None:
        # 每次請求才讀，讓使用者改了環境變數不必重建翻譯器；金鑰不存進屬性。
        key = os.environ.get(self.config.api_key_env, "").strip()
        if not key:
            return None
        if not key.isprintable() or not key.isascii():
            raise TranslationError(
                f"environment variable {self.config.api_key_env} contains invalid characters"
            )
        return key

    def _redact(self, text: str) -> str:
        key = os.environ.get(self.config.api_key_env, "").strip()
        return text.replace(key, "***") if key else text

    def _describe_error(self, body: bytes) -> str:
        text = body.decode("utf-8", errors="replace")
        try:
            obj = json.loads(text)
        except ValueError:
            obj = None
        if isinstance(obj, dict):
            err = obj.get("error")
            if isinstance(err, dict) and isinstance(err.get("message"), str):
                text = err["message"]
            elif isinstance(err, str):
                text = err
        text = " ".join(self._redact(text).split())
        if len(text) > MAX_ERROR_DETAIL_CHARS:
            text = text[:MAX_ERROR_DETAIL_CHARS] + "…"
        return text or "(empty body)"


def _parse_retry_after(value: str | None) -> float | None:
    # 只接受秒數；HTTP 日期格式在本機服務幾乎不會出現，忽略即可（改用退避秒數）。
    if value is None:
        return None
    try:
        seconds = float(value.strip())
    except ValueError:
        return None
    return seconds if math.isfinite(seconds) and seconds >= 0 else None


def _parse_completion(raw: bytes) -> tuple[str, str | None]:
    try:
        obj = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise TranslationResponseError(
            f"response is not valid JSON: {type(exc).__name__}"
        ) from exc
    choice = _first_choice(obj)
    if choice is None:
        raise TranslationResponseError("response has no choices")
    message = choice.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        raise TranslationResponseError("response has no message content")
    finish = choice.get("finish_reason")
    return content, finish if isinstance(finish, str) else None


def _parse_chunk(data: str, redact: Callable[[str], str]) -> tuple[str, str | None]:
    try:
        obj = json.loads(data)
    except ValueError as exc:
        raise TranslationResponseError("stream event is not valid JSON") from exc
    if isinstance(obj, dict) and "error" in obj:
        err = obj["error"]
        message = err.get("message") if isinstance(err, dict) else err
        raise TranslationResponseError(
            f"server reported an error: {redact(str(message))[:MAX_ERROR_DETAIL_CHARS]}"
        )
    if not isinstance(obj, dict) or not isinstance(obj.get("choices"), list):
        raise TranslationResponseError("stream event has no choices")
    choice = _first_choice(obj)
    if choice is None:
        return "", None  # 例如 stream_options.include_usage 的最後一筆只有 usage
    delta = choice.get("delta")
    # delta.reasoning／reasoning_content 是思考內容，刻意不讀。
    content = delta.get("content") if isinstance(delta, dict) else None
    if content is not None and not isinstance(content, str):
        raise TranslationResponseError("stream event content is not a string")
    finish = choice.get("finish_reason")
    return content or "", finish if isinstance(finish, str) else None


def _first_choice(obj: object) -> dict[str, Any] | None:
    if not isinstance(obj, dict):
        raise TranslationResponseError("response is not a JSON object")
    choices = obj.get("choices")
    if not isinstance(choices, list):
        raise TranslationResponseError("response has no choices")
    if not choices:
        return None
    if not isinstance(choices[0], dict):
        raise TranslationResponseError("response choice is not an object")
    return choices[0]


def _iter_sse(resp: http.client.HTTPResponse) -> Iterator[str]:
    """依 SSE 規則逐一產出每個事件的 data 內容（多行 data 以換行連接）。"""
    data_lines: list[str] = []
    while True:
        try:
            line_bytes = resp.readline(MAX_RESPONSE_BYTES + 1)
        except TimeoutError as exc:
            raise TranslationTimeoutError(
                "timed out while streaming the response"
            ) from exc
        except (OSError, http.client.HTTPException) as exc:
            raise TranslationConnectionError(
                f"connection lost while streaming: {type(exc).__name__}"
            ) from exc
        if not line_bytes:
            if data_lines:
                yield "\n".join(data_lines)
            return
        if len(line_bytes) > MAX_RESPONSE_BYTES:
            raise TranslationResponseError("stream line too long")
        try:
            line = line_bytes.decode("utf-8").rstrip("\r\n")
        except UnicodeDecodeError as exc:
            raise TranslationResponseError("stream is not valid UTF-8") from exc
        if not line:
            if data_lines:
                yield "\n".join(data_lines)
                data_lines = []
            continue
        if line.startswith(":"):
            continue  # SSE 註解（keep-alive）
        field, _, value = line.partition(":")
        if field == "data":
            data_lines.append(value.removeprefix(" "))
        # event、id、retry 欄位對 Chat Completions 沒有意義，略過。
