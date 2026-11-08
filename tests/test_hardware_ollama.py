"""Ollama 偵測與 `/api/pull` 下載的測試：本機 HTTP 伺服器（只綁 127.0.0.1）模擬 Ollama。

伺服器以 chunked 編碼逐行送 NDJSON（與 Ollama 相同），可以在某一行之前停住，
用來測試讀取逾時與取消；等待一律用 threading.Event，不依賴 sleep。
"""

import gc
import http.client
import http.server
import json
import socket
import threading
import time
import unittest
from typing import Any, ClassVar
from unittest import mock

from kikitori.hardware.ollama import (
    MAX_LAYERS,
    NotOllamaError,
    OllamaAuthError,
    OllamaError,
    OllamaUnreachableError,
    PullCancelledError,
    PullError,
    UnloadError,
    _PollingSocket,
    native_root,
    ollama_version,
    pull_model,
    unload_model,
    validate_pull_model_name,
)
from kikitori.hardware.translation_server import default_connection
from kikitori.translate import redact_url

WAIT = 10.0
DIGEST_A = "sha256:" + "a" * 64
DIGEST_B = "sha256:" + "b" * 64

# 真實 Ollama 0.40.14（Windows，2026-11-05）的 `/api/pull` 回應：狀態、欄位與數值原樣，
# 只把 digest 換成固定的假值。`docs/specs/hardware.md` 記錄了同一次觀察。
REAL_DIGESTS = ["sha256:" + c * 64 for c in "123456"]
REAL_PULL_INSTALLED = [
    b'{"status":"pulling manifest"}\n',
    *(
        json.dumps(
            {
                "status": f"pulling {digest[7:19]}",
                "digest": digest,
                "total": size,
                "completed": size,
            },
            separators=(",", ":"),
        ).encode()
        + b"\n"
        for digest, size in zip(
            REAL_DIGESTS,
            (675569120, 2648593216, 13, 11343, 65, 177),
            strict=True,
        )
    ),
    b'{"status":"verifying sha256 digest"}\n',
    b'{"status":"writing manifest"}\n',
    b'{"status":"removing unused layers"}\n',
    b'{"status":"success"}\n',
]
"""`qwen3.5:4b`（已安裝）：每一層都已完成，約 1 秒就回報 success。"""
REAL_PULL_MISSING = [
    b'{"status":"pulling manifest"}\n',
    b'{"error":"pull model manifest: file does not exist"}\n',
]
"""不存在的模型：HTTP 200、串流中的最後一行是 error，之後串流結束（沒有 success）。"""
REAL_INVALID_NAME_BODY = b'{"error":"invalid model name"}'
"""名稱不合法（或缺少 model）：HTTP 400、`application/json`，不是串流。"""


def ndjson(*items: dict[str, Any]) -> list[bytes]:
    return [json.dumps(item).encode() + b"\n" for item in items]


class _Handler(http.server.BaseHTTPRequestHandler):
    server: "_Server"
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:
        pass

    def _json(
        self, status: int, body: bytes, headers: dict[str, str] | None = None
    ) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        srv = self.server
        srv.requests.append(("GET", self.path, b"", dict(self.headers)))
        if self.path != "/api/version":
            self._json(404, b'{"error":"not found"}')
        elif srv.version_stall_body:
            # 標頭先到、內容停住：模擬回應到了但內容讀不完
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", "100")
            self.end_headers()
            self.wfile.write(b'{"version"')
            self.wfile.flush()
            srv.stalled.set()
            srv.release.wait(WAIT)
        else:
            self._json(srv.version_status, srv.version_body, srv.extra_headers)

    def do_POST(self) -> None:
        srv = self.server
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        srv.requests.append(("POST", self.path, body, dict(self.headers)))
        if self.path == "/api/generate":
            if srv.unload_stall:
                srv.stalled.set()
                srv.release.wait(WAIT)
            self._json(srv.unload_status, srv.unload_body, srv.extra_headers)
            return
        if self.path != "/api/pull":
            self._json(404, b'{"error":"not found"}')
            return
        if srv.pull_status != 200:
            self._json(srv.pull_status, srv.pull_error_body, srv.extra_headers)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
        for name, value in srv.extra_headers.items():
            self.send_header(name, value)
        self.end_headers()
        try:
            for i, line in enumerate(srv.lines):
                if i == srv.stall_before:
                    srv.stalled.set()
                    srv.release.wait(WAIT)
                self.wfile.write(f"{len(line):x}\r\n".encode() + line + b"\r\n")
                self.wfile.flush()
            self.wfile.write(b"0\r\n\r\n")
        except OSError:
            pass  # 用戶端取消或逾時後關閉了連線


class _Server(http.server.ThreadingHTTPServer):
    daemon_threads = True

    def handle_error(self, request: object, client_address: object) -> None:
        # 取消與逾時的測試會由用戶端先關閉連線；伺服器端的連線重設是預期的，不印 traceback
        pass

    version_status: int
    version_body: bytes
    version_stall_body: bool
    extra_headers: dict[str, str]
    pull_status: int
    pull_error_body: bytes
    unload_status: int
    unload_body: bytes
    unload_stall: bool
    lines: list[bytes]
    stall_before: int | None
    stalled: threading.Event
    release: threading.Event
    requests: list[tuple[str, str, bytes, dict[str, str]]]


class OllamaServerTest(unittest.TestCase):
    server: ClassVar[_Server]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server(("127.0.0.1", 0), _Handler)
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        srv = self.server
        srv.version_status = 200
        srv.version_body = b'{"version":"0.12.3"}'
        srv.version_stall_body = False
        srv.extra_headers = {}
        srv.pull_status = 200
        srv.pull_error_body = b""
        srv.unload_status = 200
        # 本機 Ollama 0.40.15 實測的回應（模型有沒有載入都一樣）
        srv.unload_body = (
            b'{"model":"qwen3.5:9b","created_at":"2026-11-07T03:10:55.4857911Z",'
            b'"response":"","done":true,"done_reason":"unload"}'
        )
        srv.unload_stall = False
        srv.lines = ndjson(
            {"status": "pulling manifest"},
            {"status": "pulling a", "digest": DIGEST_A, "total": 1000, "completed": 0},
            {
                "status": "pulling a",
                "digest": DIGEST_A,
                "total": 1000,
                "completed": 600,
            },
            {"status": "pulling b", "digest": DIGEST_B, "total": 10, "completed": 10},
            {
                "status": "pulling a",
                "digest": DIGEST_A,
                "total": 1000,
                "completed": 1000,
            },
            {"status": "verifying sha256 digest"},
            {"status": "writing manifest"},
            {"status": "success"},
        )
        srv.stall_before = None
        srv.stalled = threading.Event()
        srv.release = threading.Event()
        srv.requests = []
        self.addCleanup(srv.release.set)
        self.base = f"http://127.0.0.1:{srv.server_address[1]}/v1"

    def pull(self, model: str = "qwen3.5:9b", **kwargs: Any) -> list[tuple[int, int]]:
        seen: list[tuple[int, int]] = []
        pull_model(
            kwargs.pop("base", self.base),
            model,
            progress=lambda done, total: seen.append((done, total)),
            cancel=kwargs.pop("cancel", threading.Event()),
            env={},
            **kwargs,
        )
        return seen

    def test_version_detects_ollama_at_the_native_root(self) -> None:
        self.assertEqual(ollama_version(self.base, env={}), "0.12.3")
        self.assertEqual(self.server.requests[0][1], "/api/version")

    def test_version_with_connection_close(self) -> None:
        # 會關連線的反向代理（HTTP/1.0、keepalive_timeout 0）：http.client 在讀內容之前就
        # 關閉連線物件，真正的 socket 要等內容讀完才能關（memory_soak #3）
        self.server.extra_headers = {"Connection": "close"}
        self.assertEqual(ollama_version(self.base, env={}), "0.12.3")

    def test_pull_with_connection_close(self) -> None:
        self.server.extra_headers = {"Connection": "close"}
        seen = self.pull()
        self.assertEqual(seen[-1], (1010, 1010))

    def test_other_servers_are_not_ollama(self) -> None:
        for status, body in (
            (404, b'{"error":"not found"}'),
            (200, b"<html>"),
            (200, b'{"version": 3}'),
            (200, b'{"version": "' + b"x" * 500 + b'"}'),
        ):
            with self.subTest(status=status, body=body[:20]):
                self.server.version_status = status
                self.server.version_body = body
                with self.assertRaises(NotOllamaError):
                    ollama_version(self.base, env={})

    def test_unreachable(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with self.assertRaises(OllamaUnreachableError):
            ollama_version(f"http://127.0.0.1:{port}/v1", env={}, timeout=2.0)
        with self.assertRaises(OllamaUnreachableError):
            pull_model(
                f"http://127.0.0.1:{port}/v1",
                "qwen3.5:9b",
                progress=lambda d, t: None,
                cancel=threading.Event(),
                env={},
                connect_timeout=2.0,
            )

    def test_unload_asks_the_native_root_to_drop_the_model_now(self) -> None:
        unload_model(self.base, "qwen3.5:9b", env={})
        method, path, body, _ = self.server.requests[0]
        self.assertEqual((method, path), ("POST", "/api/generate"))
        # 沒有 prompt、keep_alive 0：Ollama 只卸載，不會先載入（實測見 docs/specs/hardware.md）
        self.assertEqual(json.loads(body), {"model": "qwen3.5:9b", "keep_alive": 0})

    def test_unload_failures_carry_the_status(self) -> None:
        self.server.unload_status = 404
        self.server.unload_body = b'{"error":"model "x:1b" not found"}'
        with self.assertRaisesRegex(UnloadError, "404"):
            unload_model(self.base, "x:1b", env={})

    def test_unload_of_an_unreachable_server(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        with self.assertRaises(OllamaUnreachableError):
            unload_model(
                f"http://127.0.0.1:{port}/v1", "qwen3.5:9b", env={}, timeout=1.0
            )

    def test_unload_is_bounded_by_its_timeout(self) -> None:
        # 引擎結束時只有幾秒；伺服器不回應也不能拖住結束流程
        self.server.unload_stall = True
        started = time.monotonic()
        with self.assertRaises(OllamaUnreachableError):
            unload_model(self.base, "qwen3.5:9b", env={}, timeout=0.5)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_unload_timeout_covers_connecting_to_both_localhost_addresses(self) -> None:
        # `localhost` 依序試 127.0.0.1、::1；封包被丟掉時每個位址都會等到逾時。
        # 卸載的時限是整個請求的總和，不是每一步各自一份。
        tried: list[float] = []

        class _Stalled:
            def __init__(self, timeout: float) -> None:
                self.timeout = timeout

            def connect(self) -> None:
                tried.append(self.timeout)
                time.sleep(self.timeout)
                raise TimeoutError("timed out")

            def close(self) -> None:
                pass

        def connect(scheme: str, host: str, port: int, timeout: float) -> Any:
            return _Stalled(timeout)

        started = time.monotonic()
        with self.assertRaises(OllamaUnreachableError):
            unload_model(
                "http://localhost:11434/v1",
                "qwen3.5:9b",
                env={},
                timeout=0.4,
                connect=connect,
            )
        self.assertLess(time.monotonic() - started, 0.6)
        self.assertLessEqual(sum(tried), 0.45)

    def test_unload_sends_the_api_key_when_set(self) -> None:
        unload_model(
            self.base, "qwen3.5:9b", api_key_env="K", env={"K": "secret-token"}
        )
        self.assertEqual(
            self.server.requests[0][3].get("Authorization"), "Bearer secret-token"
        )

    def test_pull_streams_progress_summed_over_layers(self) -> None:
        seen = self.pull()
        method, path, body, _ = self.server.requests[0]
        self.assertEqual((method, path), ("POST", "/api/pull"))
        self.assertEqual(json.loads(body), {"model": "qwen3.5:9b", "stream": True})
        self.assertEqual(seen[0], (0, 1000))
        self.assertIn((600, 1000), seen)
        self.assertIn((610, 1010), seen)
        self.assertEqual(seen[-1], (1010, 1010))

    def test_error_line_fails_the_pull(self) -> None:
        self.server.lines = ndjson(
            {"status": "pulling manifest"},
            {"error": "pull model manifest: file does not exist"},
        )
        with self.assertRaises(PullError) as ctx:
            self.pull("nope:1b")
        self.assertIn("file does not exist", str(ctx.exception))

    def test_http_error_carries_the_server_message(self) -> None:
        self.server.pull_status = 500
        self.server.pull_error_body = b'{"error":"model not found"}'
        with self.assertRaises(PullError) as ctx:
            self.pull()
        self.assertIn("HTTP 500", str(ctx.exception))
        self.assertIn("model not found", str(ctx.exception))

    def test_stream_without_success_is_an_error(self) -> None:
        self.server.lines = self.server.lines[:-1]
        with self.assertRaises(PullError):
            self.pull()

    def test_invalid_lines_are_rejected(self) -> None:
        for lines in (
            [b"not json\n"],
            [b"[1, 2]\n"],
            ndjson({"status": 5}),
            ndjson({"error": {"nested": True}}),
            [b'{"status": "' + b"x" * 70_000 + b'"}\n'],
        ):
            with self.subTest(lines=lines[0][:20]):
                self.server.lines = lines
                with self.assertRaises(PullError):
                    self.pull()

    def test_bad_progress_fields_are_ignored(self) -> None:
        self.server.lines = ndjson(
            {"status": "pulling x", "digest": "md5:1", "total": 5, "completed": 5},
            {"status": "pulling a", "digest": DIGEST_A, "total": -1, "completed": 0},
            {"status": "pulling a", "digest": DIGEST_A, "total": "9", "completed": 1},
            {"status": "pulling a", "digest": DIGEST_A, "total": 10, "completed": 99},
            {"status": "success"},
        )
        self.assertEqual(self.pull(), [(10, 10)])

    def test_idle_timeout(self) -> None:
        self.server.stall_before = 2
        with self.assertRaises(PullError) as ctx:
            self.pull(idle_timeout=0.3)
        self.assertIn("no data", str(ctx.exception))

    def test_cancel_interrupts_a_stalled_read(self) -> None:
        self.server.stall_before = 2
        cancel = threading.Event()
        errors: list[BaseException] = []

        def run() -> None:
            try:
                self.pull(cancel=cancel)
            except BaseException as exc:  # noqa: BLE001 - 收集給主執行緒檢查
                errors.append(exc)

        thread = threading.Thread(target=run)
        thread.start()
        self.assertTrue(self.server.stalled.wait(WAIT))
        cancel.set()
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], PullCancelledError)

    def test_cancelled_before_start(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(PullCancelledError):
            self.pull(cancel=cancel)
        self.assertEqual(self.server.requests, [])

    def test_api_key_is_sent_only_when_set(self) -> None:
        pull_model(
            self.base,
            "qwen3.5:9b",
            progress=lambda d, t: None,
            cancel=threading.Event(),
            api_key_env="MY_KEY",
            env={"MY_KEY": "secret-token"},
        )
        headers = self.server.requests[0][3]
        self.assertEqual(headers.get("Authorization"), "Bearer secret-token")

    def test_auth_failures_are_not_classified_as_not_ollama(self) -> None:
        # 反向代理要求金鑰時 401／403 代表「無法確認」，不是「不是 Ollama」
        for status in (401, 403):
            with self.subTest(status=status):
                self.server.version_status = status
                self.server.version_body = b'{"error":"unauthorized"}'
                with self.assertRaises(OllamaAuthError) as ctx:
                    ollama_version(self.base, env={})
                self.assertNotIsInstance(ctx.exception, NotOllamaError)
                self.assertIn(str(status), str(ctx.exception))
                self.server.pull_status = status
                self.server.pull_error_body = b'{"error":"bad token"}'
                with self.assertRaises(OllamaAuthError) as pull_ctx:
                    self.pull()
                self.assertIn("bad token", str(pull_ctx.exception))

    def test_redirects_are_not_followed(self) -> None:
        self.server.extra_headers = {
            "Location": "https://user:hunter2@proxy.example/api/version"
        }
        self.server.version_status = 302
        self.server.version_body = b"{}"
        with self.assertRaises(NotOllamaError) as ctx:
            ollama_version(self.base, env={})
        message = str(ctx.exception)
        self.assertIn("redirect", message)
        self.assertIn("proxy.example", message)
        self.assertNotIn("hunter2", message)
        self.server.pull_status = 307
        with self.assertRaises(PullError) as pull_ctx:
            self.pull()
        self.assertIn("redirect", str(pull_ctx.exception))
        # 只送了一次 GET 與一次 POST：沒有跟著轉址
        self.assertEqual(
            [(m, path) for m, path, _, _ in self.server.requests],
            [("GET", "/api/version"), ("POST", "/api/pull")],
        )

    def test_version_body_that_never_finishes_is_bounded(self) -> None:
        self.server.version_stall_body = True
        with self.assertRaises(OllamaUnreachableError) as ctx:
            ollama_version(self.base, env={}, timeout=0.5)
        self.assertIn("body", str(ctx.exception))

    def test_cancel_during_connect_sends_no_request(self) -> None:
        cancel = threading.Event()

        def connect(
            scheme: str, host: str, port: int, timeout: float
        ) -> http.client.HTTPConnection:
            conn = default_connection(scheme, host, port, timeout)
            original = conn.connect

            def connect_then_cancel() -> None:
                original()
                cancel.set()  # 使用者在連線期間按下取消

            conn.connect = connect_then_cancel  # type: ignore[method-assign]
            return conn

        with self.assertRaises(PullCancelledError):
            self.pull(cancel=cancel, connect=connect)
        self.assertEqual(self.server.requests, [])

    def test_timeouts_say_which_phase_stalled(self) -> None:
        def connect(
            scheme: str, host: str, port: int, timeout: float
        ) -> http.client.HTTPConnection:
            conn = default_connection(scheme, host, port, timeout)

            def request(*args: object, **kwargs: object) -> None:
                raise TimeoutError("timed out")

            conn.request = request  # type: ignore[method-assign]
            return conn

        with self.assertRaises(PullError) as sending:
            self.pull(connect=connect)
        self.assertIn("sending the request", str(sending.exception))
        self.server.stall_before = 2
        with self.assertRaises(PullError) as idle:
            self.pull(idle_timeout=0.3)
        self.assertIn("no data", str(idle.exception))
        self.assertNotIn("sending", str(idle.exception))

    def test_too_many_layers_is_an_error(self) -> None:
        self.server.lines = ndjson(
            *(
                {
                    "status": "pulling",
                    "digest": f"sha256:{i:064x}",
                    "total": 1,
                    "completed": 0,
                }
                for i in range(MAX_LAYERS + 1)
            ),
            {"status": "success"},
        )
        with self.assertRaises(PullError) as ctx:
            self.pull()
        self.assertIn("layers", str(ctx.exception))

    def test_credentials_in_the_url_never_appear_in_errors(self) -> None:
        port = self.server.server_address[1]
        base = f"http://kiki:s3cret-pass@127.0.0.1:{port}/v1"
        self.server.version_status = 404
        with self.assertRaises(NotOllamaError) as ctx:
            ollama_version(base, env={})
        self.assertNotIn("s3cret", str(ctx.exception))
        self.server.lines = REAL_PULL_MISSING
        with self.assertRaises(PullError) as pull_ctx:
            self.pull(base=base)
        self.assertNotIn("s3cret", str(pull_ctx.exception))
        with self.assertRaises(ValueError) as bad:
            native_root("http://kiki:s3cret-pass@host:99999/v1")
        self.assertNotIn("s3cret", str(bad.exception))
        # userinfo 不會變成 Authorization，也不會出現在請求中
        for _, path, body, headers in self.server.requests:
            self.assertNotIn("s3cret", path + body.decode() + repr(headers))

    def test_real_ollama_stream_of_an_installed_model(self) -> None:
        self.server.lines = REAL_PULL_INSTALLED
        seen = self.pull("qwen3.5:4b")
        self.assertEqual(seen[0], (675569120, 675569120))
        self.assertEqual(seen[-1], (3324173934, 3324173934))
        self.assertTrue(all(done == total for done, total in seen))

    def test_real_ollama_stream_of_a_missing_model(self) -> None:
        self.server.lines = REAL_PULL_MISSING
        with self.assertRaises(PullError) as ctx:
            self.pull("kikitori-nonexistent-model:1b")
        self.assertEqual(
            str(ctx.exception), "Ollama: pull model manifest: file does not exist"
        )

    def test_real_ollama_invalid_name_response(self) -> None:
        self.server.pull_status = 400
        self.server.pull_error_body = REAL_INVALID_NAME_BODY
        with self.assertRaises(PullError) as ctx:
            self.pull()
        self.assertEqual(
            str(ctx.exception), "HTTP 400 from /api/pull: invalid model name"
        )


class _RawServer:
    """送出固定位元組（例如不合法的標頭）後關閉連線的 TCP 伺服器（只綁 127.0.0.1）。"""

    def __init__(self, reply: bytes) -> None:
        self._reply = reply
        self._listener = socket.create_server(("127.0.0.1", 0))
        self.port = self._listener.getsockname()[1]
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        while True:
            try:
                conn, _ = self._listener.accept()
            except OSError:
                return  # close() 關掉了 listener
            with conn:
                conn.settimeout(WAIT)
                try:
                    conn.recv(65536)  # 讀掉請求
                    conn.sendall(self._reply)
                except OSError:
                    pass  # 用戶端先關閉：不影響測試

    def close(self) -> None:
        self._listener.close()
        self._thread.join(WAIT)


class BrokenResponseTest(unittest.TestCase):
    """回應的標頭不合法或中途斷線：真正的 socket 要在呼叫返回前關閉，不等垃圾回收。"""

    def setUp(self) -> None:
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)
        self.sockets: list[socket.socket] = []

    def connect(
        self, scheme: str, host: str, port: int, timeout: float
    ) -> http.client.HTTPConnection:
        conn = default_connection(scheme, host, port, timeout)
        original = conn.connect

        def connect_and_record() -> None:
            original()
            assert conn.sock is not None
            self.sockets.append(conn.sock)

        conn.connect = connect_and_record  # type: ignore[method-assign]
        return conn

    def serve(self, reply: bytes) -> str:
        server = _RawServer(reply)
        self.addCleanup(server.close)
        return f"http://127.0.0.1:{server.port}/v1"

    def assert_sockets_closed(self) -> None:
        self.assertTrue(self.sockets)
        for sock in self.sockets:
            self.assertEqual(sock.fileno(), -1, "socket left open until GC")

    def test_garbage_status_line(self) -> None:
        for reply in (b"garbage\r\n\r\n", b"HTTP/1.1 200 OK\r\nContent-Le"):
            with self.subTest(reply=reply):
                self.sockets.clear()
                base = self.serve(reply)
                # 標頭中途斷線時 http.client 當成標頭結束：分類可能是「不是 Ollama」
                with self.assertRaises(OllamaError):
                    ollama_version(base, env={}, timeout=2.0, connect=self.connect)
                self.assert_sockets_closed()
                with self.assertRaises(PullError):
                    pull_model(
                        base,
                        "qwen3.5:9b",
                        progress=lambda done, total: None,
                        cancel=threading.Event(),
                        env={},
                        idle_timeout=2.0,
                        connect=self.connect,
                    )
                self.assert_sockets_closed()


class PollingSocketTest(unittest.TestCase):
    """`_PollingSocket` 的參照計數：真正的 socket 在連線與讀取器都關閉後才關。"""

    def test_abort_closes_readers_that_were_never_closed(self) -> None:
        # http.client 建立回應（makefile）之後失敗、沒有關閉回應時，計數會一直是 1
        sock = mock.Mock()
        wrapper = _PollingSocket(sock, threading.Event(), 1.0)
        reader = wrapper.makefile("rb")
        wrapper.abort()
        sock.close.assert_called_once_with()
        self.assertTrue(reader.closed)
        wrapper.close()  # 之後的關閉不會再關一次
        sock.close.assert_called_once_with()

    def test_socket_closes_after_the_last_reader(self) -> None:
        sock = mock.Mock()
        wrapper = _PollingSocket(sock, threading.Event(), 1.0)
        reader = wrapper.makefile("rb")
        wrapper.close()  # http.client 看到 Connection: close 時先關連線
        sock.close.assert_not_called()
        reader.close()
        sock.close.assert_called_once_with()
        reader.close()  # 重複關閉不會再減一次
        sock.close.assert_called_once_with()

    def test_socket_closes_immediately_without_readers(self) -> None:
        sock = mock.Mock()
        _PollingSocket(sock, threading.Event(), 1.0).close()
        sock.close.assert_called_once_with()

    def test_reader_closed_first_keeps_socket_until_connection_closes(self) -> None:
        sock = mock.Mock()
        wrapper = _PollingSocket(sock, threading.Event(), 1.0)
        wrapper.makefile("rb").close()
        sock.close.assert_not_called()
        wrapper.close()
        sock.close.assert_called_once_with()


class HelpersTest(unittest.TestCase):
    def test_native_root(self) -> None:
        self.assertEqual(
            native_root("http://localhost:11434/v1"), ("http", "localhost", 11434, "")
        )
        self.assertEqual(
            native_root("http://host:8080/ollama/v1/"),
            ("http", "host", 8080, "/ollama"),
        )
        self.assertEqual(
            native_root("https://x.example"), ("https", "x.example", 443, "")
        )
        for bad in (
            "ftp://x",
            "http://",
            "localhost:11434",
            "http://h:99999/v1",
            "http://h:0/v1",
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                native_root(bad)

    def test_model_names(self) -> None:
        for good in (
            "qwen3.5:9b",
            "hy-mt1.5-7b:latest",
            "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
            "llama3",
        ):
            self.assertEqual(validate_pull_model_name(good), good)
        for bad in (
            "",
            " qwen",
            "a b",
            "x" * 201,
            "../etc",
            "-rf",
            "a\nb",
            "qwen3.5:9b\n",
            3,
            None,
        ):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                validate_pull_model_name(bad)

    def test_redact_url(self) -> None:
        self.assertEqual(
            redact_url("http://user:pw@host:11434/v1"), "http://***@host:11434/v1"
        )
        self.assertEqual(redact_url("http://token@host/v1"), "http://***@host/v1")
        self.assertEqual(
            redact_url("http://localhost:11434/v1"), "http://localhost:11434/v1"
        )
        # 解析不了的字串也不能把 @ 之前的內容原樣帶出去
        self.assertNotIn("pw", redact_url("http://user:pw@host:bad/v1"))
        self.assertNotIn("pw", redact_url("user:pw@host"))


if __name__ == "__main__":
    unittest.main()
