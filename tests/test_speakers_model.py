"""說話者向量模型的下載、狀態、驗證與刪除（假的 fetcher，不連網）。"""

import hashlib
import io
import os
import tempfile
import threading
import unittest
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from unittest import mock

from kikitori.asr.download import (
    DownloadCancelledError,
    ModelDownloadError,
    ModelIntegrityError,
)
from kikitori.asr.registry import ModelFile
from kikitori.speakers.model import (
    CAMPPLUS_ZH_EN,
    SPEAKER_MODEL_ID,
    UnsafeModelPathError,
    delete_model,
    install_model,
    is_installed,
    model_path,
    model_status,
    read_verified_model,
)

PAYLOAD = b"fake onnx model " * 4096


def make_spec(payload: bytes = PAYLOAD):
    return replace(
        CAMPPLUS_ZH_EN,
        url="https://example.invalid/model.onnx",
        file=ModelFile("model.onnx", len(payload), hashlib.sha256(payload).hexdigest()),
    )


class _Response:
    def __init__(self, status: int, body: bytes, headers: dict[str, str]) -> None:
        self.status = status
        self._body = io.BytesIO(body)
        self._headers = headers

    def header(self, name: str) -> str | None:
        return self._headers.get(name)

    def read(self, size: int) -> bytes:
        return self._body.read(size)

    def close(self) -> None:
        pass


class FakeFetcher:
    """回傳 `payload`；支援 Range（續傳）。"""

    def __init__(self, payload: bytes = PAYLOAD) -> None:
        self.payload = payload
        self.requests: list[str | None] = []

    def open(self, url: str, headers: Mapping[str, str], timeout: float) -> _Response:
        rng = headers.get("Range")
        self.requests.append(rng)
        if rng:
            start = int(rng.removeprefix("bytes=").rstrip("-"))
            body = self.payload[start:]
            return _Response(
                206,
                body,
                {
                    "Content-Range": f"bytes {start}-{len(self.payload) - 1}/{len(self.payload)}",
                    "Content-Length": str(len(body)),
                },
            )
        return _Response(200, self.payload, {"Content-Length": str(len(self.payload))})


class SpeakerModelTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "speakers"
        self.spec = make_spec()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_real_spec_is_pinned(self) -> None:
        self.assertIn(
            "/resolve/0743f301363dec56491a490f6d6cbc9d67f9a3bf/", CAMPPLUS_ZH_EN.url
        )
        self.assertEqual(CAMPPLUS_ZH_EN.file.size, 28281164)
        self.assertEqual(len(CAMPPLUS_ZH_EN.file.sha256), 64)
        self.assertTrue(CAMPPLUS_ZH_EN.url.startswith("https://"))

    def test_status_before_and_after_install(self) -> None:
        before = model_status(self.root, self.spec)
        self.assertFalse(before.installed)
        self.assertIsNone(before.path)
        self.assertIsNone(before.disk_bytes)
        self.assertEqual(before.id, SPEAKER_MODEL_ID)
        self.assertEqual(before.download_bytes, len(PAYLOAD))

        seen: list[tuple[int, int]] = []
        path = install_model(
            self.root,
            spec=self.spec,
            fetcher=FakeFetcher(),
            progress=lambda done, total: seen.append((done, total)),
        )
        self.assertEqual(path, model_path(self.root, self.spec))
        self.assertEqual(path.read_bytes(), PAYLOAD)
        self.assertEqual(seen[-1], (len(PAYLOAD), len(PAYLOAD)))
        after = model_status(self.root, self.spec).to_dict()
        self.assertTrue(after["installed"])
        self.assertEqual(after["path"], os.fspath(path))
        self.assertEqual(after["disk_bytes"], len(PAYLOAD))
        self.assertEqual(read_verified_model(self.root, self.spec), PAYLOAD)

    def test_installed_model_is_not_downloaded_again(self) -> None:
        install_model(self.root, spec=self.spec, fetcher=FakeFetcher())
        fetcher = FakeFetcher()
        install_model(self.root, spec=self.spec, fetcher=fetcher)
        self.assertEqual(fetcher.requests, [])

    def test_hash_mismatch_leaves_nothing_installed(self) -> None:
        with self.assertRaises(ModelIntegrityError):
            install_model(
                self.root, spec=self.spec, fetcher=FakeFetcher(b"x" * len(PAYLOAD))
            )
        self.assertFalse(is_installed(self.root, self.spec))
        self.assertFalse(model_path(self.root, self.spec).exists())

    def test_cancel_keeps_the_part_for_resume(self) -> None:
        cancel = threading.Event()
        cancel.set()
        with self.assertRaises(DownloadCancelledError):
            install_model(
                self.root, spec=self.spec, fetcher=FakeFetcher(), cancel=cancel
            )
        self.assertFalse(is_installed(self.root, self.spec))
        part = model_path(self.root, self.spec).with_name("model.onnx.part")
        part.write_bytes(PAYLOAD[:1000])
        fetcher = FakeFetcher()
        install_model(self.root, spec=self.spec, fetcher=fetcher)
        self.assertEqual(fetcher.requests, ["bytes=1000-"])
        self.assertEqual(read_verified_model(self.root, self.spec), PAYLOAD)

    def test_tampered_model_fails_verification(self) -> None:
        path = install_model(self.root, spec=self.spec, fetcher=FakeFetcher())
        path.write_bytes(b"y" * len(PAYLOAD))  # 大小相同、內容不同
        self.assertTrue(is_installed(self.root, self.spec))
        with self.assertRaises(ModelIntegrityError):
            read_verified_model(self.root, self.spec)

    def test_read_without_install(self) -> None:
        with self.assertRaises(FileNotFoundError):
            read_verified_model(self.root, self.spec)

    def test_rejects_non_https_and_bad_timeout(self) -> None:
        with self.assertRaises(ValueError):
            install_model(
                self.root, spec=replace(self.spec, url="http://example.com/m")
            )
        with self.assertRaises(ValueError):
            install_model(self.root, spec=self.spec, timeout=0)

    def test_delete_frees_model_and_leftovers(self) -> None:
        path = install_model(self.root, spec=self.spec, fetcher=FakeFetcher())
        path.with_name("model.onnx.part").write_bytes(b"abc")
        freed = delete_model(self.root, self.spec)
        self.assertEqual(freed, len(PAYLOAD) + 3)
        self.assertFalse(path.parent.exists())
        with self.assertRaises(FileNotFoundError):
            delete_model(self.root, self.spec)

    def test_a_leftover_part_is_reported_and_deletable(self) -> None:
        # 取消或中斷的下載留下 .part：狀態看得到大小，也刪得掉
        part = model_path(self.root, self.spec).with_name("model.onnx.part")
        part.parent.mkdir(parents=True)
        part.write_bytes(b"x" * 1000)
        status = model_status(self.root, self.spec)
        self.assertFalse(status.installed)
        self.assertEqual(status.disk_bytes, 1000)
        self.assertEqual(delete_model(self.root, self.spec), 1000)
        self.assertFalse(part.parent.exists())
        self.assertIsNone(model_status(self.root, self.spec).disk_bytes)

    def test_an_oversized_part_does_not_count_as_downloaded_space(self) -> None:
        # download_file 會丟掉比完整檔案大的 .part 從頭下載：空間檢查要算完整大小
        part = model_path(self.root, self.spec).with_name("model.onnx.part")
        part.parent.mkdir(parents=True)
        part.write_bytes(b"x" * (len(PAYLOAD) + 10))
        free = mock.Mock(free=len(PAYLOAD) - 1)
        with (
            mock.patch("shutil.disk_usage", return_value=free),
            self.assertRaises(ModelDownloadError),
        ):
            install_model(self.root, spec=self.spec, fetcher=FakeFetcher())

    def test_delete_refuses_unexpected_files(self) -> None:
        path = install_model(self.root, spec=self.spec, fetcher=FakeFetcher())
        stray = path.with_name("notes.txt")
        stray.write_text("keep me", encoding="utf-8")
        with self.assertRaises(UnsafeModelPathError):
            delete_model(self.root, self.spec)
        self.assertTrue(path.exists())
        self.assertTrue(stray.exists())


if __name__ == "__main__":
    unittest.main()
