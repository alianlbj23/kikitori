"""模型下載的測試：本機 HTTP 伺服器（只綁 127.0.0.1）與假 fetcher，不連外網。"""

import hashlib
import http.server
import json
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import ClassVar
from unittest import mock

from kikitori.asr.download import (
    MARKER_NAME,
    DownloadCancelledError,
    ModelDownloadError,
    ModelIntegrityError,
    download_model,
    is_downloaded,
    model_dir,
    validate_url,
)
from kikitori.asr.registry import ModelFile, ModelSpec

REVISION = "0123456789abcdef0123456789abcdef01234567"
CONTENTS = {
    "model.bin": bytes(range(256)) * 12_000,  # 約 3 MB，跨越多個 1 MiB 區塊
    "config.json": b'{"x": 1}',
}


def make_spec(
    contents: Mapping[str, bytes] = CONTENTS, sha_override: str | None = None
) -> ModelSpec:
    files = tuple(
        ModelFile(name, len(data), sha_override or hashlib.sha256(data).hexdigest())
        for name, data in contents.items()
    )
    return ModelSpec(
        id="fake",
        repo_id="org/fake-model",
        revision=REVISION,
        files=files,
        languages=("multilingual",),
        n_mels=80,
        speed_tier="fast",
        license="MIT",
        approx_cpu_ram_mb=1,
        approx_gpu_vram_mb=1,
        approx_gpu_vram_int8_mb=1,
    )


class _Handler(http.server.BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format: str, *args: object) -> None:
        pass  # 不讓測試輸出被伺服器 log 洗版

    def do_GET(self) -> None:
        srv = self.server
        srv.requests.append((self.path, self.headers.get("Range")))
        if srv.delay_secs:
            time.sleep(srv.delay_secs)
        if srv.redirect_to is not None:
            self.send_response(302)
            self.send_header("Location", srv.redirect_to)
            self.end_headers()
            return
        name = self.path.rsplit("/", 1)[-1]
        prefix = f"/org/fake-model/resolve/{REVISION}/"
        if not self.path.startswith(prefix) or name not in srv.files:
            self.send_error(404)
            return
        data = srv.files[name]
        start = 0
        range_header = self.headers.get("Range")
        if range_header and srv.honor_range:
            start = int(range_header.removeprefix("bytes=").rstrip("-"))
            self.send_response(206)
            self.send_header(
                "Content-Range", f"bytes {start}-{len(data) - 1}/{len(data)}"
            )
        else:
            self.send_response(200)
        body = data[start:]
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server(http.server.ThreadingHTTPServer):
    def __init__(self) -> None:
        super().__init__(("127.0.0.1", 0), _Handler)
        self.files: dict[str, bytes] = dict(CONTENTS)
        self.requests: list[tuple[str, str | None]] = []
        self.honor_range = True
        self.delay_secs = 0.0
        self.redirect_to: str | None = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server_address[1]}"


class LocalServerDownloadTest(unittest.TestCase):
    server: ClassVar[_Server]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server()
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.server.requests.clear()
        self.server.files = dict(CONTENTS)
        self.server.honor_range = True
        self.server.delay_secs = 0.0
        self.server.redirect_to = None
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.spec = make_spec()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _download(self, **kwargs: object) -> Path:
        return download_model(
            self.spec, self.cache, base_url=self.server.base_url, **kwargs
        )  # type: ignore[arg-type]

    def test_fresh_download_reports_progress_and_verifies(self) -> None:
        events: list[tuple[int, int]] = []
        directory = self._download(progress=lambda d, t: events.append((d, t)))

        self.assertEqual(directory, model_dir(self.spec, self.cache))
        self.assertEqual(directory, self.cache / "fake" / REVISION[:12])
        for name, data in CONTENTS.items():
            self.assertEqual((directory / name).read_bytes(), data)
        self.assertEqual(list(directory.glob("*.part")), [])
        self.assertTrue(is_downloaded(self.spec, self.cache))
        marker = json.loads((directory / MARKER_NAME).read_text(encoding="utf-8"))
        self.assertEqual(marker["revision"], REVISION)

        total = self.spec.total_bytes
        self.assertTrue(all(t == total for _, t in events))
        done = [d for d, _ in events]
        self.assertEqual(done, sorted(done))
        self.assertEqual(events[-1], (total, total))
        self.assertGreater(len(events), 3)  # 每個 1 MiB 區塊都有回報

    def test_already_downloaded_skips_network(self) -> None:
        self._download()
        self.server.requests.clear()
        events: list[tuple[int, int]] = []
        self._download(progress=lambda d, t: events.append((d, t)))
        self.assertEqual(self.server.requests, [])
        self.assertEqual(events, [(self.spec.total_bytes, self.spec.total_bytes)])

    def test_resume_sends_range_header(self) -> None:
        directory = model_dir(self.spec, self.cache)
        directory.mkdir(parents=True)
        partial = CONTENTS["model.bin"][:1_000_000]
        (directory / "model.bin.part").write_bytes(partial)
        events: list[tuple[int, int]] = []

        self._download(progress=lambda d, t: events.append((d, t)))

        self.assertIn(
            (f"/org/fake-model/resolve/{REVISION}/model.bin", "bytes=1000000-"),
            self.server.requests,
        )
        self.assertEqual((directory / "model.bin").read_bytes(), CONTENTS["model.bin"])
        self.assertGreaterEqual(events[0][0], 1_000_000)

    def test_server_without_range_support_restarts(self) -> None:
        self.server.honor_range = False
        directory = model_dir(self.spec, self.cache)
        directory.mkdir(parents=True)
        (directory / "model.bin.part").write_bytes(
            b"\xff" * 5000
        )  # 內容錯也無妨，會從頭寫
        self._download()
        self.assertEqual((directory / "model.bin").read_bytes(), CONTENTS["model.bin"])

    def test_completed_files_are_not_downloaded_again(self) -> None:
        directory = model_dir(self.spec, self.cache)
        directory.mkdir(parents=True)
        (directory / "model.bin").write_bytes(CONTENTS["model.bin"])
        self._download()
        paths = [p for p, _ in self.server.requests]
        self.assertEqual(paths, [f"/org/fake-model/resolve/{REVISION}/config.json"])

    def test_hash_mismatch_removes_partial_file(self) -> None:
        self.server.files["config.json"] = b'{"x": 2}'  # 同長度、不同內容
        with self.assertRaises(ModelIntegrityError):
            self._download()
        directory = model_dir(self.spec, self.cache)
        self.assertFalse((directory / "config.json").exists())
        self.assertFalse((directory / "config.json.part").exists())
        self.assertFalse(is_downloaded(self.spec, self.cache))

    def test_http_404(self) -> None:
        del self.server.files["config.json"]
        with self.assertRaisesRegex(ModelDownloadError, "HTTP 404"):
            self._download()

    def test_timeout(self) -> None:
        self.server.delay_secs = 1.0
        start = time.monotonic()
        with self.assertRaises(ModelDownloadError):
            self._download(timeout=0.2)
        self.assertLess(time.monotonic() - start, 5.0)

    def test_insecure_redirect_is_refused(self) -> None:
        self.server.redirect_to = "http://example.com/model.bin"
        with self.assertRaisesRegex(ModelDownloadError, "insecure redirect"):
            self._download()

    def test_cancel_keeps_partial_file(self) -> None:
        cancel = threading.Event()

        def progress(done: int, total: int) -> None:
            if done >= 1 << 20:
                cancel.set()

        with self.assertRaises(DownloadCancelledError):
            self._download(progress=progress, cancel=cancel)
        part = model_dir(self.spec, self.cache) / "model.bin.part"
        self.assertTrue(part.exists())
        self.assertFalse(is_downloaded(self.spec, self.cache))


class _FakeResponse:
    def __init__(
        self, status: int, body: bytes, headers: Mapping[str, str] | None = None
    ) -> None:
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in (headers or {}).items()}
        self.closed = False

    def header(self, name: str) -> str | None:
        return self._headers.get(name.lower())

    def read(self, size: int) -> bytes:
        chunk, self._body = self._body[:size], self._body[size:]
        return chunk

    def close(self) -> None:
        self.closed = True


class _FakeFetcher:
    def __init__(self, responses: Mapping[str, _FakeResponse]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str], float]] = []

    def open(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> _FakeResponse:
        self.calls.append((url, dict(headers), timeout))
        return self.responses[url.rsplit("/", 1)[-1]]


class FakeFetcherDownloadTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name)
        self.spec = make_spec({"config.json": b"abcdef"})

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_uses_pinned_revision_url_and_timeout(self) -> None:
        fetcher = _FakeFetcher({"config.json": _FakeResponse(200, b"abcdef")})
        download_model(self.spec, self.cache, fetcher=fetcher, timeout=12.5)
        url, headers, timeout = fetcher.calls[0]
        self.assertEqual(
            url, f"https://huggingface.co/org/fake-model/resolve/{REVISION}/config.json"
        )
        self.assertNotIn("Range", headers)
        self.assertEqual(timeout, 12.5)
        self.assertTrue(fetcher.responses["config.json"].closed)

    def test_truncated_body_keeps_partial_for_resume(self) -> None:
        fetcher = _FakeFetcher({"config.json": _FakeResponse(200, b"abc")})
        with self.assertRaisesRegex(ModelDownloadError, "ended after 3 of 6"):
            download_model(self.spec, self.cache, fetcher=fetcher)
        part = model_dir(self.spec, self.cache) / "config.json.part"
        self.assertEqual(part.read_bytes(), b"abc")

    def test_oversized_body(self) -> None:
        fetcher = _FakeFetcher({"config.json": _FakeResponse(200, b"abcdefXYZ")})
        with self.assertRaisesRegex(ModelDownloadError, "larger than expected"):
            download_model(self.spec, self.cache, fetcher=fetcher)

    def test_content_length_mismatch(self) -> None:
        response = _FakeResponse(200, b"abcdef", {"Content-Length": "99"})
        with self.assertRaisesRegex(ModelDownloadError, "Content-Length"):
            download_model(
                self.spec, self.cache, fetcher=_FakeFetcher({"config.json": response})
            )

    def test_wrong_content_range_on_resume(self) -> None:
        directory = model_dir(self.spec, self.cache)
        directory.mkdir(parents=True)
        (directory / "config.json.part").write_bytes(b"abc")
        response = _FakeResponse(206, b"def", {"Content-Range": "bytes 0-5/6"})
        with self.assertRaisesRegex(ModelDownloadError, "Content-Range"):
            download_model(
                self.spec, self.cache, fetcher=_FakeFetcher({"config.json": response})
            )

    def test_complete_partial_file_needs_no_request(self) -> None:
        directory = model_dir(self.spec, self.cache)
        directory.mkdir(parents=True)
        (directory / "config.json.part").write_bytes(b"abcdef")
        fetcher = _FakeFetcher({})
        download_model(self.spec, self.cache, fetcher=fetcher)
        self.assertEqual(fetcher.calls, [])
        self.assertEqual((directory / "config.json").read_bytes(), b"abcdef")

    def test_connection_error_is_wrapped(self) -> None:
        class Failing:
            def open(
                self, url: str, headers: Mapping[str, str], timeout: float
            ) -> _FakeResponse:
                raise ConnectionResetError("reset by peer")

        with self.assertRaisesRegex(ModelDownloadError, "reset by peer"):
            download_model(self.spec, self.cache, fetcher=Failing())

    def test_read_timeout_is_wrapped(self) -> None:
        class Stalling(_FakeResponse):
            def read(self, size: int) -> bytes:
                raise TimeoutError("timed out")

        fetcher = _FakeFetcher({"config.json": Stalling(200, b"")})
        with self.assertRaisesRegex(ModelDownloadError, "timed out"):
            download_model(self.spec, self.cache, fetcher=fetcher)

    def test_not_enough_disk_space(self) -> None:
        usage = mock.Mock(free=3)
        with (
            mock.patch("kikitori.asr.download.shutil.disk_usage", return_value=usage),
            self.assertRaisesRegex(ModelDownloadError, "disk space"),
        ):
            download_model(self.spec, self.cache, fetcher=_FakeFetcher({}))

    def test_invalid_arguments(self) -> None:
        with self.assertRaises(ValueError):
            download_model(self.spec, self.cache, fetcher=_FakeFetcher({}), timeout=0)
        with self.assertRaises(ValueError):
            download_model(
                self.spec,
                self.cache,
                fetcher=_FakeFetcher({}),
                base_url="http://example.com",
            )

    def test_is_downloaded_detects_stale_or_damaged_cache(self) -> None:
        fetcher = _FakeFetcher({"config.json": _FakeResponse(200, b"abcdef")})
        directory = download_model(self.spec, self.cache, fetcher=fetcher)
        self.assertTrue(is_downloaded(self.spec, self.cache))

        other = make_spec({"config.json": b"abcdef"}, sha_override="0" * 64)
        self.assertFalse(is_downloaded(other, self.cache))  # 標記記錄的雜湊不同

        (directory / "config.json").write_bytes(b"abc")
        self.assertFalse(is_downloaded(self.spec, self.cache))

        (directory / MARKER_NAME).write_text("not json", encoding="utf-8")
        with self.assertLogs("kikitori.asr.download", level="WARNING"):
            self.assertFalse(is_downloaded(self.spec, self.cache))


class ValidateUrlTest(unittest.TestCase):
    def test_allowed(self) -> None:
        for url in (
            "https://huggingface.co/x",
            "http://127.0.0.1:8000/x",
            "http://localhost/x",
        ):
            validate_url(url)

    def test_rejected(self) -> None:
        for url in (
            "http://huggingface.co/x",
            "ftp://host/x",
            "file:///etc/passwd",
            "https://",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                validate_url(url)


if __name__ == "__main__":
    unittest.main()
