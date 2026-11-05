"""Ollama 偵測與 `/api/pull` 下載的測試：本機 HTTP 伺服器（只綁 127.0.0.1）模擬 Ollama。

伺服器以 chunked 編碼逐行送 NDJSON（與 Ollama 相同），可以在某一行之前停住，
用來測試讀取逾時與取消；等待一律用 threading.Event，不依賴 sleep。
"""

import http.client
import http.server
import json
import socket
import threading
import unittest
from typing import Any, ClassVar

from kikitori.hardware.ollama import (
    MAX_LAYERS,
    NotOllamaError,
    OllamaAuthError,
    OllamaUnreachableError,
    PullCancelledError,
    PullError,
    native_root,
    ollama_version,
    pull_model,
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
        if self.path != "/api/pull":
            self._json(404, b'{"error":"not found"}')
            return
        if srv.pull_status != 200:
            self._json(srv.pull_status, srv.pull_error_body, srv.extra_headers)
            return
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson")
        self.send_header("Transfer-Encoding", "chunked")
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
