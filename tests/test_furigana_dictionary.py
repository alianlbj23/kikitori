"""振假名辭典的下載、驗證、刪除：假的 fetcher 提供記憶體中的小 zip，不連網。"""

import hashlib
import io
import random
import tempfile
import threading
import unittest
import zipfile
from collections.abc import Mapping
from pathlib import Path

from kikitori.asr.download import (
    DownloadCancelledError,
    ModelDownloadError,
    ModelIntegrityError,
)
from kikitori.furigana.dictionary import (
    DICTIONARY_ID,
    MARKER_NAME,
    SUDACHIDICT_CORE,
    ArchiveMember,
    DictionaryError,
    DictionarySpec,
    UnsafeDictionaryPathError,
    delete_dictionary,
    dictionary_dir,
    dictionary_path,
    dictionary_status,
    install_dictionary,
    is_installed,
)

DIC = random.Random(0).randbytes(2_500_000)  # 壓不小、跨越多個 1 MiB 區塊
LEGAL = b"LEGAL NOTICE (fake)"
LICENSE = b"Apache License 2.0 (fake)"
URL = "https://example.invalid/sudachi-dictionary-test-core.zip"


def make_zip(dic: bytes = DIC) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("d/system_core.dic", dic)
        zf.writestr("d/LEGAL", LEGAL)
        zf.writestr("d/LICENSE-2.0.txt", LICENSE)
        zf.writestr("d/README", b"not extracted")
    return buffer.getvalue()


def member(path: str, name: str, data: bytes) -> ArchiveMember:
    return ArchiveMember(path, name, len(data), hashlib.sha256(data).hexdigest())


def make_spec(
    payload: bytes, *, dic: bytes = DIC, sha: str | None = None
) -> DictionarySpec:
    return DictionarySpec(
        edition="core",
        version="test",
        url=URL,
        size=len(payload),
        sha256=sha or hashlib.sha256(payload).hexdigest(),
        dictionary=member("d/system_core.dic", "system_core.dic", dic),
        notices=(
            member("d/LEGAL", "LEGAL", LEGAL),
            member("d/LICENSE-2.0.txt", "LICENSE-2.0.txt", LICENSE),
        ),
        license="Apache-2.0",
        license_url="https://example.invalid/LEGAL",
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
    """回傳 `payload`；支援 Range（續傳）。`requests` 記下每次的 Range 標頭。"""

    def __init__(self, payload: bytes) -> None:
        self.payload = payload
        self.requests: list[str | None] = []

    def open(self, url: str, headers: Mapping[str, str], timeout: float) -> _Response:
        assert timeout > 0
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


class InstallTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = make_zip()
        self.spec = make_spec(self.payload)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def install(self, fetcher: FakeFetcher | None = None, **kwargs: object) -> Path:
        return install_dictionary(
            self.root,
            spec=self.spec,
            fetcher=fetcher or FakeFetcher(self.payload),
            **kwargs,  # type: ignore[arg-type]
        )

    def test_installs_dictionary_and_notices(self) -> None:
        reports: list[tuple[int, int]] = []
        path = self.install(progress=lambda d, t: reports.append((d, t)))
        self.assertEqual(path, dictionary_path(self.root, self.spec))
        self.assertEqual(path.read_bytes(), DIC)
        directory = dictionary_dir(self.root, self.spec)
        self.assertEqual((directory / "LEGAL").read_bytes(), LEGAL)
        self.assertEqual((directory / "LICENSE-2.0.txt").read_bytes(), LICENSE)
        self.assertFalse((directory / "README").exists())
        self.assertTrue(is_installed(self.root, self.spec))
        # zip 與暫存資料夾都清掉了
        self.assertFalse((self.root / "downloads").exists())
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), [directory.name])
        self.assertEqual(reports[-1], (len(self.payload), len(self.payload)))

    def test_already_installed_does_not_download(self) -> None:
        self.install()
        fetcher = FakeFetcher(b"")
        self.install(fetcher)
        self.assertEqual(fetcher.requests, [])

    def test_hash_mismatch_fails_and_removes_partial_download(self) -> None:
        self.spec = make_spec(self.payload, sha="0" * 64)
        with self.assertRaises(ModelIntegrityError):
            self.install()
        self.assertFalse(is_installed(self.root, self.spec))
        self.assertEqual(list((self.root / "downloads").iterdir()), [])

    def test_member_that_does_not_match_is_rejected_atomically(self) -> None:
        # zip 本身的雜湊相符（固定在 spec 裡），但裡面的辭典與預期不同
        self.spec = make_spec(self.payload, dic=DIC[:-1] + b"x")
        with self.assertRaises(DictionaryError):
            self.install()
        self.assertFalse(is_installed(self.root, self.spec))
        self.assertFalse(dictionary_dir(self.root, self.spec).exists())
        self.assertFalse(
            (self.root / f"{dictionary_dir(self.root, self.spec).name}.tmp").exists()
        )

    def test_cancel_keeps_partial_download(self) -> None:
        cancel = threading.Event()

        def progress(done: int, total: int) -> None:
            if done >= 1 << 20:
                cancel.set()

        with self.assertRaises(DownloadCancelledError):
            self.install(progress=progress, cancel=cancel)
        part = self.root / "downloads" / (self.spec.filename + ".part")
        self.assertTrue(part.exists())
        fetcher = FakeFetcher(self.payload)
        self.install(fetcher)
        self.assertTrue(
            fetcher.requests[0] and fetcher.requests[0].startswith("bytes=")
        )
        self.assertTrue(is_installed(self.root, self.spec))

    def test_truncated_response_is_an_error(self) -> None:
        with self.assertRaises(ModelDownloadError):
            install_dictionary(
                self.root, spec=self.spec, fetcher=_Truncating(self.payload)
            )
        self.assertFalse(is_installed(self.root, self.spec))

    def test_corrupted_zip_is_rejected_and_retried_cleanly(self) -> None:
        # zip 的雜湊相符（固定在 spec 裡）但不是 zip：不留下半套辭典，下次換成正確的檔案就裝得起來
        junk = b"not a zip" * 1000
        self.spec = make_spec(junk)
        with self.assertRaises(DictionaryError):
            install_dictionary(self.root, spec=self.spec, fetcher=FakeFetcher(junk))
        self.assertFalse(dictionary_dir(self.root, self.spec).exists())

    def test_stale_staging_folder_is_replaced(self) -> None:
        staging = self.root / f"{dictionary_dir(self.root, self.spec).name}.tmp"
        staging.mkdir(parents=True)
        (staging / "leftover").write_bytes(b"x")
        self.install()
        self.assertFalse(staging.exists())
        self.assertTrue(is_installed(self.root, self.spec))

    def test_refuses_plain_http(self) -> None:
        spec = DictionarySpec(
            **{**self.spec.__dict__, "url": "http://example.com/x.zip"}
        )
        with self.assertRaises(ValueError):
            install_dictionary(self.root, spec=spec, fetcher=FakeFetcher(self.payload))

    def test_status(self) -> None:
        status = dictionary_status(self.root, self.spec)
        self.assertFalse(status.installed)
        self.assertIsNone(status.disk_bytes)
        self.assertIsNone(status.path)
        self.assertEqual(status.id, DICTIONARY_ID)
        self.assertEqual(status.download_bytes, len(self.payload))
        self.install()
        status = dictionary_status(self.root, self.spec)
        self.assertTrue(status.installed)
        self.assertEqual(status.installed_bytes, len(DIC) + len(LEGAL) + len(LICENSE))
        marker = dictionary_dir(self.root, self.spec) / MARKER_NAME
        self.assertEqual(
            status.disk_bytes, status.installed_bytes + marker.stat().st_size
        )
        self.assertEqual(
            set(status.to_dict()),
            {
                "id",
                "installed",
                "edition",
                "version",
                "download_bytes",
                "installed_bytes",
                "disk_bytes",
                "path",
                "license",
                "license_url",
            },
        )

    def test_tampered_file_is_not_installed(self) -> None:
        path = self.install()
        path.write_bytes(DIC[:10])
        self.assertFalse(is_installed(self.root, self.spec))


class _Truncating(FakeFetcher):
    def open(self, url: str, headers: Mapping[str, str], timeout: float) -> _Response:
        # 宣告完整長度但只給一半
        return _Response(
            200,
            self.payload[: len(self.payload) // 2],
            {"Content-Length": str(len(self.payload))},
        )


class DeleteTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.payload = make_zip()
        self.spec = make_spec(self.payload)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_delete_frees_everything(self) -> None:
        install_dictionary(self.root, spec=self.spec, fetcher=FakeFetcher(self.payload))
        expected = dictionary_status(self.root, self.spec).disk_bytes
        freed = delete_dictionary(self.root, self.spec)
        self.assertEqual(freed, expected)
        self.assertFalse(is_installed(self.root, self.spec))
        self.assertEqual(list(self.root.iterdir()), [])

    def test_delete_not_installed(self) -> None:
        with self.assertRaises(FileNotFoundError):
            delete_dictionary(self.root, self.spec)

    def test_delete_refuses_unexpected_subfolder(self) -> None:
        install_dictionary(self.root, spec=self.spec, fetcher=FakeFetcher(self.payload))
        (dictionary_dir(self.root, self.spec) / "nested").mkdir()
        with self.assertRaises(UnsafeDictionaryPathError):
            delete_dictionary(self.root, self.spec)
        # 什麼都沒刪
        self.assertTrue(is_installed(self.root, self.spec))


class PinnedSpecTest(unittest.TestCase):
    def test_pinned_release(self) -> None:
        spec = SUDACHIDICT_CORE
        self.assertTrue(
            spec.url.startswith("https://github.com/WorksApplications/SudachiDict/")
        )
        self.assertEqual(spec.edition, "core")
        self.assertEqual(len(spec.sha256), 64)
        self.assertEqual(
            [m.filename for m in spec.members],
            ["system_core.dic", "LEGAL", "LICENSE-2.0.txt"],
        )


if __name__ == "__main__":
    unittest.main()
