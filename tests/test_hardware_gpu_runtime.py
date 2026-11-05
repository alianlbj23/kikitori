"""GPU 執行環境安裝器的測試：本機 HTTP 伺服器（只綁 127.0.0.1）提供假的小 wheel，不連外網。"""

import hashlib
import http.server
import io
import json
import tempfile
import threading
import unittest
import zipfile
from pathlib import Path
from typing import ClassVar
from unittest import mock

from kikitori.asr.download import (
    DownloadCancelledError,
    ModelDownloadError,
    ModelIntegrityError,
)
from kikitori.hardware.gpu_runtime import (
    CUBLAS_VERSION,
    CUBLAS_WHEELS,
    MARKER_NAME,
    GpuRuntimeError,
    RuntimeWheel,
    WheelMember,
    gpu_runtime_status,
    install_gpu_runtime,
    installed_runtime_dir,
    is_installed,
    platform_wheel,
    runtime_dir,
)

LIB_A = bytes(range(256)) * 9000  # 約 2.3 MB，跨越多個 1 MiB 區塊
LIB_B = b"second library" * 100
LICENSE = b"NVIDIA EULA (fake)"


def make_wheel_bytes(extra: dict[str, bytes] | None = None) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("nvidia/cublas/lib/liba.so.12", LIB_A)
        zf.writestr("nvidia/cublas/lib/libb.so.12", LIB_B)
        zf.writestr("nvidia/cublas/include/cublas.h", b"/* header */")
        zf.writestr("pkg.dist-info/License.txt", LICENSE)
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buffer.getvalue()


WHEEL = make_wheel_bytes()
MEMBERS = (
    WheelMember("nvidia/cublas/lib/liba.so.12", "liba.so.12", len(LIB_A)),
    WheelMember("nvidia/cublas/lib/libb.so.12", "libb.so.12", len(LIB_B)),
    WheelMember("pkg.dist-info/License.txt", "License.txt", len(LICENSE)),
)


class _Handler(http.server.BaseHTTPRequestHandler):
    server: "_Server"

    def log_message(self, format: str, *args: object) -> None:
        pass  # 不讓測試輸出被伺服器 log 洗版

    def do_GET(self) -> None:
        srv = self.server
        srv.requests.append((self.path, self.headers.get("Range")))
        if self.path != "/fake.whl":
            self.send_error(404)
            return
        data = srv.payload
        start = 0
        range_header = self.headers.get("Range")
        if range_header:
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
        if srv.truncate_at is not None:
            # 宣告完整長度但只送一部分就斷線，模擬下載中斷
            self.wfile.write(body[: srv.truncate_at])
            srv.truncate_at = None
            self.close_connection = True
            return
        self.wfile.write(body)


class _Server(http.server.ThreadingHTTPServer):
    payload: bytes
    truncate_at: int | None
    requests: list[tuple[str, str | None]]


class GpuRuntimeInstallTest(unittest.TestCase):
    server: ClassVar[_Server]
    thread: ClassVar[threading.Thread]

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = _Server(("127.0.0.1", 0), _Handler)
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self) -> None:
        self.server.payload = WHEEL
        self.server.truncate_at = None
        self.server.requests = []
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def wheel(
        self,
        sha: str | None = None,
        members: tuple[WheelMember, ...] = MEMBERS,
        payload: bytes | None = None,
    ) -> RuntimeWheel:
        data = WHEEL if payload is None else payload
        return RuntimeWheel(
            platform_tag="test",
            url=f"http://127.0.0.1:{self.server.server_address[1]}/fake.whl",
            size=len(data),
            sha256=sha or hashlib.sha256(data).hexdigest(),
            members=members,
        )

    def test_install_extracts_only_needed_files(self) -> None:
        reports: list[tuple[int, int]] = []
        wheel = self.wheel()
        path = install_gpu_runtime(
            self.root, wheel=wheel, progress=lambda d, t: reports.append((d, t))
        )
        self.assertEqual(path, runtime_dir(self.root))
        self.assertEqual(
            sorted(p.name for p in path.iterdir()),
            sorted(["License.txt", "liba.so.12", "libb.so.12", MARKER_NAME]),
        )
        self.assertEqual((path / "liba.so.12").read_bytes(), LIB_A)
        self.assertEqual((path / "License.txt").read_bytes(), LICENSE)
        self.assertTrue(is_installed(self.root, wheel))
        self.assertEqual(reports[-1], (len(WHEEL), len(WHEEL)))
        # 暫存的 wheel 與 downloads 目錄已清掉
        self.assertFalse((self.root / "downloads").exists())
        marker = json.loads((path / MARKER_NAME).read_text(encoding="utf-8"))
        self.assertEqual(marker["version"], CUBLAS_VERSION)

    def test_already_installed_does_not_download(self) -> None:
        wheel = self.wheel()
        install_gpu_runtime(self.root, wheel=wheel)
        self.server.requests.clear()
        install_gpu_runtime(self.root, wheel=wheel)
        self.assertEqual(self.server.requests, [])
        self.assertEqual(
            installed_runtime_dir(self.root, wheel), runtime_dir(self.root)
        )

    def test_hash_mismatch_removes_partial_and_installs_nothing(self) -> None:
        wheel = self.wheel(sha="0" * 64)
        with self.assertRaises(ModelIntegrityError):
            install_gpu_runtime(self.root, wheel=wheel)
        self.assertFalse(runtime_dir(self.root).exists())
        self.assertEqual(list((self.root / "downloads").iterdir()), [])

    def test_member_size_mismatch_is_rejected_and_staging_removed(self) -> None:
        bad = (
            WheelMember("nvidia/cublas/lib/liba.so.12", "liba.so.12", 10),
            *MEMBERS[1:],
        )
        with self.assertRaises(GpuRuntimeError):
            install_gpu_runtime(self.root, wheel=self.wheel(members=bad))
        self.assertFalse(runtime_dir(self.root).exists())
        self.assertEqual([p.name for p in self.root.iterdir()], ["downloads"])

    def test_missing_member(self) -> None:
        missing = (
            *MEMBERS,
            WheelMember("nvidia/cublas/lib/libc.so.12", "libc.so.12", 1),
        )
        with self.assertRaises(GpuRuntimeError):
            install_gpu_runtime(self.root, wheel=self.wheel(members=missing))

    def test_retry_after_extract_failure_reuses_verified_wheel(self) -> None:
        bad = (
            WheelMember("nvidia/cublas/lib/liba.so.12", "liba.so.12", 10),
            *MEMBERS[1:],
        )
        with self.assertRaises(GpuRuntimeError):
            install_gpu_runtime(self.root, wheel=self.wheel(members=bad))
        self.server.requests.clear()
        install_gpu_runtime(self.root, wheel=self.wheel())
        self.assertEqual(self.server.requests, [])
        self.assertTrue(is_installed(self.root, self.wheel()))

    def test_not_a_zip(self) -> None:
        payload = b"not a zip file" * 10
        self.server.payload = payload
        with self.assertRaises(GpuRuntimeError):
            install_gpu_runtime(self.root, wheel=self.wheel(payload=payload))

    def test_interrupted_download_resumes(self) -> None:
        self.server.truncate_at = 1000
        wheel = self.wheel()
        with self.assertRaises(ModelDownloadError):
            install_gpu_runtime(self.root, wheel=wheel, timeout=5)
        part = self.root / "downloads" / "fake.whl.part"
        self.assertEqual(part.stat().st_size, 1000)
        install_gpu_runtime(self.root, wheel=wheel)
        self.assertEqual(self.server.requests[-1][1], "bytes=1000-")
        self.assertTrue(is_installed(self.root, wheel))

    def test_cancel(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DownloadCancelledError):
            install_gpu_runtime(self.root, wheel=self.wheel(), cancel=cancel)
        self.assertFalse(runtime_dir(self.root).exists())

    def test_rejects_plain_http_to_remote_host(self) -> None:
        wheel = RuntimeWheel("x", "http://example.com/fake.whl", 1, "0" * 64, MEMBERS)
        with self.assertRaises(ValueError):
            install_gpu_runtime(self.root, wheel=wheel)

    def test_rejects_bad_timeout(self) -> None:
        with self.assertRaises(ValueError):
            install_gpu_runtime(self.root, wheel=self.wheel(), timeout=0)

    def test_tampered_install_is_not_installed(self) -> None:
        wheel = self.wheel()
        path = install_gpu_runtime(self.root, wheel=wheel)
        (path / "libb.so.12").write_bytes(b"short")
        self.assertFalse(is_installed(self.root, wheel))
        (path / MARKER_NAME).write_text("{bad json", encoding="utf-8")
        with self.assertLogs("kikitori.hardware.gpu_runtime", "WARNING"):
            self.assertFalse(is_installed(self.root, wheel))
            # 重新安裝會覆蓋壞掉的目錄
            install_gpu_runtime(self.root, wheel=wheel)
        self.assertTrue(is_installed(self.root, wheel))


class GpuRuntimeStatusTest(unittest.TestCase):
    def test_platform_wheels(self) -> None:
        self.assertIs(platform_wheel("windows", "AMD64"), CUBLAS_WHEELS["win_amd64"])
        self.assertIs(
            platform_wheel("linux", "x86_64"), CUBLAS_WHEELS["manylinux_2_27_x86_64"]
        )
        self.assertIsNone(platform_wheel("linux", "aarch64"))
        self.assertIsNone(platform_wheel("macos", "arm64"))
        self.assertIsNone(platform_wheel("windows", "ARM64"))

    def test_pinned_wheels_are_well_formed(self) -> None:
        for wheel in CUBLAS_WHEELS.values():
            self.assertTrue(wheel.url.startswith("https://files.pythonhosted.org/"))
            self.assertIn(CUBLAS_VERSION, wheel.filename)
            self.assertRegex(wheel.sha256, r"^[0-9a-f]{64}$")
            self.assertIn("License.txt", [m.filename for m in wheel.members])
            self.assertEqual(len(wheel.members), 3)

    def test_unsupported_platform_install_fails(self) -> None:
        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch(
                "kikitori.hardware.gpu_runtime.platform_wheel", return_value=None
            ),
            self.assertRaises(GpuRuntimeError),
        ):
            install_gpu_runtime(Path(tmp))

    def test_status_not_installed(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            status = gpu_runtime_status(Path(tmp), CUBLAS_WHEELS["win_amd64"])
            self.assertTrue(status.supported)
            self.assertFalse(status.installed)
            self.assertIsNone(status.path)
            data = status.to_dict()
            self.assertEqual(data["id"], "gpu-runtime")
            self.assertEqual(data["download_bytes"], 567544208)
            self.assertIn("nvidia.com", data["license_url"])


if __name__ == "__main__":
    unittest.main()
