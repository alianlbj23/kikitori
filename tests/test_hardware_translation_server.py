"""翻譯伺服器探測的測試：本機 HTTP 伺服器（只綁 127.0.0.1）模擬 Ollama 的 `/v1/models`。"""

import http.server
import json
import socket
import threading
import unittest
from typing import ClassVar

from kikitori.hardware.translation_server import (
    MAX_MODEL_LIST_BYTES,
    ModelListError,
    is_loopback_host,
    parse_model_list,
    probe_translation_server,
)

OLLAMA_MODELS = {
    "object": "list",
    "data": [
        {"id": "qwen3.5:9b", "object": "model"},
        {"id": "hy-mt1.5-1.8b:latest", "object": "model"},
    ],
}


class _Handler(http.server.BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format: str, *args: object) -> None:
        pass  # 不讓測試輸出被伺服器 log 洗版

    def do_GET(self) -> None:
        srv = self.server
        srv.requests.append((self.path, dict(self.headers)))
        self.send_response(srv.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(srv.body)))
        self.end_headers()
        self.wfile.write(srv.body)


class _Server(http.server.ThreadingHTTPServer):
    status: int
    body: bytes
    requests: list[tuple[str, dict[str, str]]]


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


class ProbeTest(unittest.TestCase):
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
        self.server.status = 200
        self.server.body = json.dumps(OLLAMA_MODELS).encode()
        self.server.requests = []
        self.port = self.server.server_address[1]

    def test_ollama_models(self) -> None:
        info = probe_translation_server(f"http://localhost:{self.port}/v1", env={})
        self.assertTrue(info.reachable)
        self.assertTrue(info.is_local)
        self.assertEqual(info.models, ("qwen3.5:9b", "hy-mt1.5-1.8b:latest"))
        self.assertIsNone(info.error)
        path, headers = self.server.requests[0]
        self.assertEqual(path, "/v1/models")
        self.assertEqual(headers["Host"], f"localhost:{self.port}")
        self.assertNotIn("Authorization", headers)

    def test_api_key_is_sent_but_never_reported(self) -> None:
        self.server.status = 401
        env = {"MY_KEY": "sk-secret"}
        info = probe_translation_server(
            f"http://127.0.0.1:{self.port}/v1/", "MY_KEY", env=env
        )
        self.assertEqual(
            self.server.requests[0][1]["Authorization"], "Bearer sk-secret"
        )
        self.assertTrue(info.reachable)
        self.assertIsNone(info.models)
        self.assertIn("401", info.error or "")
        self.assertNotIn("sk-secret", repr(info))

    def test_invalid_api_key_characters(self) -> None:
        info = probe_translation_server(
            f"http://127.0.0.1:{self.port}/v1", "K", env={"K": "bad\nkey"}
        )
        self.assertFalse(info.reachable)
        self.assertEqual(self.server.requests, [])
        self.assertNotIn("bad", (info.error or "").replace("invalid", ""))

    def test_garbage_json(self) -> None:
        self.server.body = b"<html>not json</html>"
        info = probe_translation_server(f"http://127.0.0.1:{self.port}/v1", env={})
        self.assertTrue(info.reachable)
        self.assertIsNone(info.models)
        self.assertIn("JSON", info.error or "")

    def test_too_large(self) -> None:
        self.server.body = b" " * (MAX_MODEL_LIST_BYTES + 10)
        info = probe_translation_server(f"http://127.0.0.1:{self.port}/v1", env={})
        self.assertIn("too large", info.error or "")

    def test_unreachable(self) -> None:
        port = free_port()
        info = probe_translation_server(
            f"http://localhost:{port}/v1", env={}, timeout=0.5
        )
        self.assertFalse(info.reachable)
        self.assertTrue(info.is_local)
        self.assertIn("cannot connect", info.error or "")

    def test_invalid_base_urls(self) -> None:
        for url in ("ftp://x/v1", "not a url", "http://:99/v1", "http://h:99999/v1"):
            info = probe_translation_server(url, env={})
            self.assertFalse(info.reachable, url)
            self.assertIn("invalid base_url", info.error or "")

    def test_credentials_in_the_url_are_never_reported(self) -> None:
        # 結果會回到 UI（hardware.detect、推薦理由）並寫進 log
        for url in (
            f"http://kiki:s3cret@127.0.0.1:{self.port}/v1",
            "http://kiki:s3cret@h:99999/v1",
            f"http://kiki:s3cret@127.0.0.1:{free_port()}/v1",
        ):
            with self.subTest(url=url.split("@")[1]):
                info = probe_translation_server(url, env={}, timeout=0.5)
                self.assertNotIn("s3cret", repr(info))
                self.assertTrue(info.base_url.startswith("http://***@"))

    def test_remote_host_is_not_local(self) -> None:
        class FailingConn:
            def request(self, *a: object, **k: object) -> None:
                raise OSError("network down")

            def close(self) -> None:
                pass

        info = probe_translation_server(
            "https://api.example.com/v1",
            env={},
            connect=lambda *a: FailingConn(),  # type: ignore[arg-type,return-value]
        )
        self.assertFalse(info.is_local)
        self.assertFalse(info.reachable)


class ParseModelListTest(unittest.TestCase):
    def test_filters_invalid_items(self) -> None:
        raw = json.dumps(
            {
                "data": [
                    {"id": "ok"},
                    {"id": ""},
                    {"id": 3},
                    "x",
                    {"id": " spaced "},
                    {"id": "ok"},
                    {"id": "a" * 300},
                ]
            }
        ).encode()
        self.assertEqual(parse_model_list(raw), ("ok",))

    def test_shape_errors(self) -> None:
        for raw in (b"[]", b'{"data": {}}', b"\xff", b"{"):
            with self.assertRaises(ModelListError):
                parse_model_list(raw)

    def test_loopback(self) -> None:
        self.assertTrue(is_loopback_host("localhost"))
        self.assertTrue(is_loopback_host("127.0.0.2"))
        self.assertTrue(is_loopback_host("::1"))
        self.assertFalse(is_loopback_host("192.168.1.10"))
        self.assertFalse(is_loopback_host("example.com"))


if __name__ == "__main__":
    unittest.main()
