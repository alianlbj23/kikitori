"""計數用的本機反向代理：引擎的翻譯請求先經過這裡再轉給 Ollama，記下每一個請求。

暫時性實驗，不屬於正式程式碼。只用標準庫。

為什麼需要：同一台機器上可能有其他實驗也在用 Ollama（例如翻譯評測），Ollama 的
`/api/ps` 到期時間與 llama-server 的記憶體會被別人的請求影響，無法分辨「引擎有沒有送
請求」。讓引擎的 `translation.base_url` 指到這個代理（127.0.0.1，引擎仍視為本機服務，
預熱與保溫照常），就能精確知道引擎在什麼時候送了什麼請求——例如關閉翻譯後是否還有
保溫請求、session 結束後是否還有請求。

只轉送請求與回應，不改內容。與 Ollama 一樣用 HTTP/1.1 keep-alive：上游有 Content-Length
就照送，否則（串流）重新以 chunked 送出，邊收邊轉，不會改變引擎看到的時序。刻意不送
`Connection: close`——引擎的 `/api/version` 檢查遇到這個標頭會誤判連不上
（見 README「發現的問題」#3）。
"""

from __future__ import annotations

import http.client
import json
import logging
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

logger = logging.getLogger("soak.proxy")

HOP_BY_HOP = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "content-length",
}
UPSTREAM_TIMEOUT = 120.0
"""連線與每次讀取的逾時；比引擎自己的逾時（預熱 60 秒）長，讓引擎先逾時。"""
CRLF = b"\r\n"


@dataclass
class ProxiedRequest:
    t: float  # monotonic
    method: str
    path: str
    model: str | None
    stream: bool | None
    max_tokens: int | None
    status: int | None = None
    secs: float | None = None
    error: str | None = None

    @property
    def is_chat(self) -> bool:
        return self.method == "POST" and self.path.endswith("/chat/completions")

    def to_dict(self, t0: float) -> dict[str, Any]:
        return {
            "t": round(self.t - t0, 3),
            "method": self.method,
            "path": self.path,
            "model": self.model,
            "stream": self.stream,
            "max_tokens": self.max_tokens,
            "status": self.status,
            "secs": None if self.secs is None else round(self.secs, 3),
            "error": self.error,
        }


class _QuietServer(ThreadingHTTPServer):
    """引擎被強制結束時客戶端連線會被重設：記 debug，不要把 traceback 印到主控台。"""

    def handle_error(self, request: Any, client_address: Any) -> None:
        exc = sys.exc_info()[1]
        if isinstance(exc, ConnectionError):
            logger.debug(
                "proxy: client %s reset the connection: %s", client_address, exc
            )
            return
        super().handle_error(request, client_address)


class CountingProxy:
    """在 127.0.0.1 的隨機埠轉送到 `upstream`（例如 http://127.0.0.1:11434）。"""

    def __init__(self, upstream: str = "http://127.0.0.1:11434") -> None:
        parts = urlsplit(upstream)
        if parts.scheme != "http" or not parts.hostname:
            raise ValueError(f"upstream must be an http:// URL, got {upstream!r}")
        self.upstream_host = parts.hostname
        self.upstream_port = parts.port or 80
        self.requests: list[ProxiedRequest] = []
        self._lock = threading.Lock()
        proxy = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, format: str, *args: Any) -> None:
                logger.debug("proxy: " + format, *args)

            def do_GET(self) -> None:
                proxy._forward(self, b"")

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                if length < 0 or length > 16 * 1024 * 1024:
                    self.send_error(413)
                    return
                proxy._forward(self, self.rfile.read(length))

            def do_HEAD(self) -> None:
                proxy._forward(self, b"")

        self._server = _QuietServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread: threading.Thread | None = None

    @property
    def base_url(self) -> str:
        """給引擎設定的 OpenAI 相容網址。"""
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> None:
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="soak-proxy", daemon=True
        )
        self._thread.start()

    def stop(self) -> None:
        self._server.shutdown()
        self._server.server_close()

    def snapshot(self) -> list[ProxiedRequest]:
        with self._lock:
            return list(self.requests)

    def chat_requests(self, t0: float = 0.0, t1: float = 1e18) -> list[ProxiedRequest]:
        """[t0, t1]（monotonic）之間送出的翻譯請求（chat/completions）。"""
        return [r for r in self.snapshot() if r.is_chat and t0 <= r.t <= t1]

    def _forward(self, handler: BaseHTTPRequestHandler, body: bytes) -> None:
        rec = ProxiedRequest(
            time.monotonic(), handler.command, handler.path, None, None, None
        )
        if body:
            try:
                payload = json.loads(body.decode("utf-8"))
                if isinstance(payload, dict):
                    rec.model = payload.get("model")
                    rec.stream = payload.get("stream")
                    rec.max_tokens = payload.get("max_tokens")
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.debug("proxy: request body is not JSON")
        with self._lock:
            self.requests.append(rec)
        headers = {
            k: v
            for k, v in handler.headers.items()
            if k.lower() not in HOP_BY_HOP and k.lower() != "host"
        }
        headers["Host"] = f"{self.upstream_host}:{self.upstream_port}"
        if body:
            headers["Content-Length"] = str(len(body))
        conn = http.client.HTTPConnection(
            self.upstream_host, self.upstream_port, timeout=UPSTREAM_TIMEOUT
        )
        try:
            conn.request(handler.command, handler.path, body or None, headers)
            resp = conn.getresponse()
            rec.status = resp.status
            handler.send_response(resp.status, resp.reason)
            for k, v in resp.getheaders():
                if k.lower() not in HOP_BY_HOP:
                    handler.send_header(k, v)
            length = resp.getheader("Content-Length")
            if handler.command == "HEAD":
                handler.send_header("Content-Length", length or "0")
                handler.end_headers()
            elif length is not None:
                data = resp.read()
                handler.send_header("Content-Length", str(len(data)))
                handler.end_headers()
                handler.wfile.write(data)
            else:
                handler.send_header("Transfer-Encoding", "chunked")
                handler.end_headers()
                while True:
                    chunk = resp.read1(65536)
                    if not chunk:
                        break
                    handler.wfile.write(b"%x" % len(chunk) + CRLF + chunk + CRLF)
                    handler.wfile.flush()
                handler.wfile.write(b"0" + CRLF + CRLF)
            handler.wfile.flush()
        except (OSError, http.client.HTTPException) as exc:
            # 引擎關閉串流（即時翻譯途中句子結束）或上游斷線：記錄，不往外拋
            rec.error = f"{type(exc).__name__}: {exc}"
            handler.close_connection = True
            logger.debug("proxy: %s %s failed: %s", handler.command, handler.path, exc)
            if rec.status is None:
                try:
                    handler.send_error(502)
                except OSError as send_exc:
                    logger.debug("proxy: cannot send 502: %s", send_exc)
        finally:
            rec.secs = time.monotonic() - rec.t
            conn.close()
