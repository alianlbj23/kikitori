"""`scripts/release.py` 的測試：發布判斷、整理安裝檔、簽章驗證與 `latest.json`。

腳本不是套件的一部分，依檔案路徑載入。檔案操作都在暫存目錄；minisign 以假的 runner 取代。
"""

import base64
import importlib.util
import itertools
import json
import subprocess
import sys
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "kikitori_test_release", ROOT / "scripts" / "release.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rel = _load_script()

VERSION = "1.2.3"
PUBKEY = base64.b64encode(
    b"untrusted comment: minisign public key: ABCD\nRWQfakepublickey\n"
).decode()


def _sig(label: str) -> str:
    text = (
        "untrusted comment: signature from tauri secret key\n"
        f"RUQfake{label}\n"
        "trusted comment: timestamp:0\tfile:x\n"
        "globalsig\n"
    )
    return base64.b64encode(text.encode()).decode()


def _write(path: Path, data: str | bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        path.write_bytes(data)
    else:
        path.write_text(data, encoding="utf-8")
    return path


def _fake_root(tmp: Path, version: str = VERSION) -> Path:
    _write(
        tmp / "pyproject.toml", f'[project]\nname = "kikitori"\nversion = "{version}"\n'
    )
    conf = {
        "productName": "kikitori",
        "version": version,
        "plugins": {"updater": {"pubkey": PUBKEY, "endpoints": []}},
    }
    _write(tmp / "app" / "src-tauri" / "tauri.conf.json", json.dumps(conf))
    return tmp


def _fake_bundles(bundle_dir: Path, version: str = VERSION) -> None:
    """Tauri 輸出的檔名（`target/release/bundle` 底下）。"""
    files = {
        f"nsis/kikitori_{version}_x64-setup.exe": True,
        f"appimage/kikitori_{version}_amd64.AppImage": True,
        f"deb/kikitori_{version}_amd64.deb": True,
        f"dmg/kikitori_{version}_aarch64.dmg": False,
        "macos/kikitori.app.tar.gz": True,
    }
    for rel_path, signed in files.items():
        _write(bundle_dir / rel_path, b"payload " + rel_path.encode())
        if signed:
            _write(bundle_dir / (rel_path + ".sig"), _sig(Path(rel_path).name))


class CompareVersionsTest(unittest.TestCase):
    def test_semver_precedence_chain(self) -> None:
        # SemVer 2.0 規格 §11 的例子，依序遞增。
        chain = [
            "1.0.0-alpha",
            "1.0.0-alpha.1",
            "1.0.0-alpha.beta",
            "1.0.0-beta",
            "1.0.0-beta.2",
            "1.0.0-beta.11",
            "1.0.0-rc.1",
            "1.0.0",
            "1.0.1",
            "1.1.0",
            "2.0.0",
        ]
        for lower, higher in itertools.pairwise(chain):
            with self.subTest(lower=lower, higher=higher):
                self.assertEqual(rel.compare_versions(lower, higher), -1)
                self.assertEqual(rel.compare_versions(higher, lower), 1)

    def test_equal_and_build_metadata(self) -> None:
        self.assertEqual(rel.compare_versions("1.0.0", "1.0.0"), 0)
        self.assertEqual(rel.compare_versions("1.0.0+abc", "1.0.0"), 0)
        self.assertEqual(rel.compare_versions("0.10.0", "0.9.9"), 1)

    def test_rejects_non_semver(self) -> None:
        for bad in ["1.0", "v1.0.0", "01.0.0", "1.0.0-", ""]:
            with self.subTest(bad=bad), self.assertRaises(rel.ReleaseError):
                rel.parse_semver(bad)


class DecideTest(unittest.TestCase):
    MAIN = "refs/heads/main"

    def test_publish_when_tag_is_new(self) -> None:
        d = rel.decide("0.2.0", self.MAIN, "main", ["v0.1.0", "unrelated"])
        self.assertEqual(d.mode, "publish")
        self.assertEqual(
            d.outputs(),
            {
                "mode": "publish",
                "version": "0.2.0",
                "tag": "v0.2.0",
                "prerelease": "false",
            },
        )

    def test_first_release_has_no_tags(self) -> None:
        self.assertEqual(rel.decide("0.1.0", self.MAIN, "main", []).mode, "publish")

    def test_skip_when_tag_exists(self) -> None:
        d = rel.decide("0.1.0", self.MAIN, "main", ["v0.1.0\n", ""])
        self.assertEqual(d.mode, "skip")

    def test_other_branch_is_dry_run_even_if_tag_exists(self) -> None:
        d = rel.decide("0.1.0", "refs/heads/feat/full-app", "main", ["v0.1.0"])
        self.assertEqual(d.mode, "dry-run")
        self.assertIn("exists", d.reason)

    def test_refuses_version_not_newer_than_existing_release(self) -> None:
        with self.assertRaisesRegex(
            rel.ReleaseError, "not newer than existing release tag v0.3.0"
        ):
            rel.decide("0.2.0", self.MAIN, "main", ["v0.1.0", "v0.3.0"])

    def test_prerelease_flag(self) -> None:
        d = rel.decide("0.2.0-rc.1", self.MAIN, "main", ["v0.1.0"])
        self.assertEqual((d.mode, d.prerelease), ("publish", True))
        with self.assertRaises(rel.ReleaseError):
            rel.decide("0.2.0-rc.1", self.MAIN, "main", ["v0.2.0"])

    def test_invalid_inputs(self) -> None:
        with self.assertRaises(rel.ReleaseError):
            rel.decide("1.0", self.MAIN, "main", [])
        with self.assertRaises(rel.ReleaseError):
            rel.decide("1.0.0", self.MAIN, "main; rm -rf /", [])


class CollectTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.bundle = self.tmp / "bundle"
        _fake_bundles(self.bundle)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_each_platform_copies_release_names(self) -> None:
        expected = {
            "windows": {
                "kikitori_1.2.3_x64-setup.exe",
                "kikitori_1.2.3_x64-setup.exe.sig",
            },
            "linux": {
                "kikitori_1.2.3_amd64.AppImage",
                "kikitori_1.2.3_amd64.AppImage.sig",
                "kikitori_1.2.3_amd64.deb",
                "kikitori_1.2.3_amd64.deb.sig",
            },
            "macos": {
                "kikitori_1.2.3_aarch64.dmg",
                "kikitori_1.2.3_aarch64.app.tar.gz",
                "kikitori_1.2.3_aarch64.app.tar.gz.sig",
            },
        }
        for platform, names in expected.items():
            with self.subTest(platform=platform):
                out = self.tmp / "out" / platform
                rows = rel.collect(platform, self.bundle, out, "kikitori", VERSION)
                self.assertEqual({n for n, _ in rows}, names)
                self.assertEqual({p.name for p in out.iterdir()}, names)
                self.assertTrue(all(size > 0 for _, size in rows))
        renamed = self.tmp / "out" / "macos" / "kikitori_1.2.3_aarch64.app.tar.gz"
        self.assertEqual(renamed.read_bytes(), b"payload macos/kikitori.app.tar.gz")

    def test_missing_signature_fails_without_copying(self) -> None:
        (self.bundle / "deb" / "kikitori_1.2.3_amd64.deb.sig").unlink()
        out = self.tmp / "out"
        with self.assertRaisesRegex(rel.ReleaseError, r"amd64\.deb\.sig"):
            rel.collect("linux", self.bundle, out, "kikitori", VERSION)
        self.assertFalse(out.exists())

    def test_wrong_version_is_missing(self) -> None:
        with self.assertRaisesRegex(rel.ReleaseError, "missing bundle files"):
            rel.collect("windows", self.bundle, self.tmp / "o", "kikitori", "9.9.9")

    def test_unknown_platform(self) -> None:
        with self.assertRaises(rel.ReleaseError):
            rel.collect("android", self.bundle, self.tmp / "o", "kikitori", VERSION)


class ManifestTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        bundle = self.tmp / "bundle"
        _fake_bundles(bundle)
        self.artifacts = self.tmp / "artifacts"
        for platform in rel.PLATFORMS:
            rel.collect(
                platform,
                bundle,
                self.artifacts / f"bundle-{platform}",
                "kikitori",
                VERSION,
            )
        self.stage = self.tmp / "stage"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _build(self, **overrides: Any) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "product": "kikitori",
            "version": VERSION,
            "tag": "v1.2.3",
            "repo": "alianlbj23/kikitori",
            "notes": "kikitori v1.2.3",
            "pub_date": datetime(2026, 11, 5, 8, 30, 15, 123, tzinfo=UTC),
        }
        kwargs.update(overrides)
        return rel.build_manifest(self.artifacts, self.stage, **kwargs)

    def test_tauri_v2_static_json(self) -> None:
        manifest = self._build()
        written = json.loads((self.stage / "latest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest, written)
        self.assertEqual(written["version"], "1.2.3")
        self.assertEqual(written["pub_date"], "2026-11-05T08:30:15Z")
        self.assertEqual(
            set(written["platforms"]),
            {
                "windows-x86_64",
                "windows-x86_64-nsis",
                "linux-x86_64",
                "linux-x86_64-appimage",
                "linux-x86_64-deb",
                "darwin-aarch64",
                "darwin-aarch64-app",
            },
        )
        base = "https://github.com/alianlbj23/kikitori/releases/download/v1.2.3/"
        p = written["platforms"]
        self.assertEqual(
            p["windows-x86_64"]["url"], base + "kikitori_1.2.3_x64-setup.exe"
        )
        self.assertEqual(
            p["linux-x86_64"]["url"], base + "kikitori_1.2.3_amd64.AppImage"
        )
        self.assertEqual(
            p["linux-x86_64-deb"]["url"], base + "kikitori_1.2.3_amd64.deb"
        )
        self.assertEqual(
            p["darwin-aarch64"]["url"], base + "kikitori_1.2.3_aarch64.app.tar.gz"
        )
        # signature 是 .sig 的內容本身（不是路徑或網址）。
        self.assertEqual(p["darwin-aarch64"]["signature"], _sig("kikitori.app.tar.gz"))
        self.assertEqual(
            p["linux-x86_64-deb"]["signature"], _sig("kikitori_1.2.3_amd64.deb")
        )

    def test_stages_every_release_asset(self) -> None:
        self._build()
        names = {p.name for p in self.stage.iterdir()}
        self.assertEqual(len(names), 10)  # 5 個檔案 + 4 個 .sig + latest.json
        self.assertIn("kikitori_1.2.3_aarch64.dmg", names)
        self.assertNotIn("kikitori_1.2.3_aarch64.dmg.sig", names)

    def test_missing_platform_fails(self) -> None:
        for f in (self.artifacts / "bundle-macos").iterdir():
            f.unlink()
        with self.assertRaisesRegex(rel.ReleaseError, "aarch64.dmg not found"):
            self._build()

    def test_duplicate_file_fails(self) -> None:
        dup = self.artifacts / "extra" / "kikitori_1.2.3_x64-setup.exe"
        _write(dup, b"x")
        with self.assertRaisesRegex(rel.ReleaseError, "more than once"):
            self._build()

    def test_malformed_signature_fails(self) -> None:
        _write(
            self.artifacts / "bundle-windows" / "kikitori_1.2.3_x64-setup.exe.sig",
            "not base64!",
        )
        with self.assertRaisesRegex(rel.ReleaseError, "not base64"):
            self._build()

    def test_argument_validation(self) -> None:
        with self.assertRaisesRegex(rel.ReleaseError, "does not match version"):
            self._build(tag="v9.9.9")
        with self.assertRaisesRegex(rel.ReleaseError, "invalid repository"):
            self._build(repo="evil/../x y")


class VerifySignaturesTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self._tmp.name)
        _write(self.dir / "a.exe", b"a")
        _write(self.dir / "a.exe.sig", _sig("a"))
        _write(self.dir / "sub" / "b.deb", b"b")
        _write(self.dir / "sub" / "b.deb.sig", _sig("b"))
        self.calls: list[list[str]] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _runner(self, returncode: int = 0) -> Any:
        def run(cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            self.assertIn("timeout", kwargs)
            self.calls.append(cmd)
            pub = Path(cmd[cmd.index("-p") + 1]).read_text(encoding="utf-8")
            sig = Path(cmd[cmd.index("-x") + 1]).read_text(encoding="utf-8")
            self.assertTrue(pub.startswith("untrusted comment: minisign public key"))
            self.assertTrue(sig.startswith("untrusted comment: signature from tauri"))
            return subprocess.CompletedProcess(cmd, returncode, "", "bad sig")

        return run

    def test_verifies_every_signature_with_decoded_files(self) -> None:
        names = rel.verify_signatures(self.dir, PUBKEY, run=self._runner())
        self.assertEqual(sorted(names), ["a.exe", "b.deb"])
        self.assertEqual(len(self.calls), 2)
        self.assertTrue(all(c[:3] == ["minisign", "-V", "-q"] for c in self.calls))
        self.assertTrue(any(c[-1].endswith("b.deb") for c in self.calls))

    def test_mismatch_fails(self) -> None:
        with self.assertRaisesRegex(
            rel.ReleaseError, "does not match the updater public key"
        ):
            rel.verify_signatures(self.dir, PUBKEY, run=self._runner(returncode=1))

    def test_signed_file_missing(self) -> None:
        (self.dir / "a.exe").unlink()
        with self.assertRaisesRegex(rel.ReleaseError, "a.exe is missing"):
            rel.verify_signatures(self.dir, PUBKEY, run=self._runner())

    def test_no_signatures(self) -> None:
        with (
            tempfile.TemporaryDirectory() as empty,
            self.assertRaisesRegex(rel.ReleaseError, "no .sig files"),
        ):
            rel.verify_signatures(Path(empty), PUBKEY, run=self._runner())

    def test_bad_public_key(self) -> None:
        bad = base64.b64encode(b"not a key").decode()
        with self.assertRaisesRegex(rel.ReleaseError, "minisign format"):
            rel.verify_signatures(self.dir, bad, run=self._runner())

    def test_minisign_missing_or_hanging(self) -> None:
        def missing(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            raise FileNotFoundError(cmd[0])

        def hang(cmd: list[str], **_: Any) -> subprocess.CompletedProcess[str]:
            raise subprocess.TimeoutExpired(cmd, 60)

        with self.assertRaisesRegex(rel.ReleaseError, "not found"):
            rel.verify_signatures(self.dir, PUBKEY, run=missing)
        with self.assertRaisesRegex(rel.ReleaseError, "timed out"):
            rel.verify_signatures(self.dir, PUBKEY, run=hang)


class ReleaseNotesTest(unittest.TestCase):
    """Release 頁面的說明：下載表與給未簽章程式的安裝提示。"""

    def setUp(self) -> None:
        self.notes = rel.release_notes("kikitori", VERSION, "v1.2.3")

    def test_lists_every_installer_by_its_release_name(self) -> None:
        self.assertTrue(self.notes.startswith("kikitori v1.2.3\n"))
        for name in (
            "kikitori_1.2.3_x64-setup.exe",
            "kikitori_1.2.3_amd64.AppImage",
            "kikitori_1.2.3_amd64.deb",
            "kikitori_1.2.3_aarch64.dmg",
        ):
            self.assertIn(f"`{name}`", self.notes)
        # 更新器專用的 tarball 不是給人下載的。
        self.assertNotIn("app.tar.gz", self.notes)
        self.assertIn("latest.json", self.notes)

    def test_install_tips_per_platform(self) -> None:
        tips = self.notes.split("## Installing", 1)[1]
        for fact in (
            # Windows：沒有 Authenticode 簽章
            "SmartScreen",
            "More info → Run anyway",
            # macOS：ad-hoc 簽章、沒有公證
            "Gatekeeper",
            "System Settings → Privacy & Security",
            "Open Anyway",
            "xattr -dr com.apple.quarantine /Applications/kikitori.app",
            # Linux AppImage：type2-runtime 需要 /dev/fuse 與 fusermount（fuse3），不需要 libfuse2
            "chmod +x kikitori_1.2.3_amd64.AppImage",
            "`fuse3`",
            "`/dev/fuse`",
            "`fusermount`",
            "./kikitori_1.2.3_amd64.AppImage --appimage-extract-and-run",
            "libfuse2 is not needed",
            # 精簡系統的中日文字型、deb 的安裝方式
            "`fonts-noto-cjk`",
            "sudo apt install ./kikitori_1.2.3_amd64.deb",
        ):
            self.assertIn(fact, tips)

    def test_tips_agree_with_the_spec(self) -> None:
        # 規格「給使用者的安裝說明」是同一份事實的出處；改了其中一邊就要一起改。
        spec = (ROOT / "docs" / "specs" / "release-update.md").read_text(
            encoding="utf-8"
        )
        for fact in (
            "More info → Run anyway",
            "Open Anyway",
            "xattr -dr com.apple.quarantine /Applications/kikitori.app",
            "`fuse3`",
            "--appimage-extract-and-run",
            "libfuse2 is not needed",
            "`fonts-noto-cjk`",
            "sudo apt install ./kikitori_<v>_amd64.deb",
        ):
            self.assertIn(fact, spec)
            self.assertIn(fact.replace("<v>", VERSION), self.notes)

    def test_tag_must_match_the_version(self) -> None:
        with self.assertRaisesRegex(rel.ReleaseError, "does not match"):
            rel.release_notes("kikitori", VERSION, "v1.2.4")


class VersionNotesTest(unittest.TestCase):
    """`docs/release-notes/v<版本>.md`（這一版的更新內容）放在下載表之前。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "docs" / "release-notes" / f"v{VERSION}.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _notes(self) -> str:
        return rel.release_notes("kikitori", VERSION, "v1.2.3", root=self.root)

    def test_absent_file_keeps_the_old_body(self) -> None:
        text = self._notes()
        self.assertEqual(text, rel.release_notes("kikitori", VERSION, "v1.2.3"))
        self.assertTrue(text.startswith("kikitori v1.2.3\n\n| Platform | Download |\n"))
        self.assertNotIn("## Download", text)

    def test_included_before_the_download_table(self) -> None:
        _write(
            self.path,
            "# kikitori v1.2.3\r\n\r\n本版重點：字幕更穩定。\r\n\r\n## 新功能\r\n\r\n- 術語表\r\n",
        )
        text = self._notes()
        self.assertTrue(text.startswith("kikitori v1.2.3\n\n本版重點：字幕更穩定。\n"))
        # Release 標題已是「kikitori v1.2.3」，重複的 H1 拿掉。
        self.assertNotIn("# kikitori v1.2.3", text)
        self.assertNotIn("\r", text)
        self.assertLess(text.index("- 術語表"), text.index("## Download"))
        self.assertLess(text.index("## Download"), text.index("| Platform | Download |"))
        self.assertIn("## Installing", text)

    def test_h1_without_the_version_is_kept(self) -> None:
        _write(self.path, "﻿# 大改版\n\n內容\n")
        text = self._notes()
        self.assertIn("# 大改版\n\n內容\n", text)
        self.assertNotIn("﻿", text)

    def test_rejects_invalid_files(self) -> None:
        cases: dict[str, bytes] = {
            "UTF-8": b"# v1.2.3\n\xff\xfe broken\n",
            "not text": b"binary\x00data\n",
            "empty": b"  \n\n",
            "empty after": b"# kikitori v1.2.3\n\n",
            "too large": b"a" * (rel.VERSION_NOTES_MAX_BYTES + 1),
        }
        for message, data in cases.items():
            with self.subTest(message=message):
                _write(self.path, data)
                with self.assertRaisesRegex(rel.ReleaseError, message):
                    self._notes()

    def test_directory_instead_of_file(self) -> None:
        self.path.mkdir(parents=True)
        with self.assertRaisesRegex(rel.ReleaseError, "not a regular file"):
            self._notes()

    def test_repo_release_notes_are_valid(self) -> None:
        # repo 裡每一份已寫好的更新說明都要能通過檢查，發布時才不會在 assemble 失敗。
        for path in sorted((ROOT / "docs" / "release-notes").glob("v*.md")):
            version = path.stem[1:]
            with self.subTest(file=path.name):
                text = rel.release_notes("kikitori", version, f"v{version}")
                self.assertNotIn(f"# kikitori v{version}\n", text)
                self.assertIn("## Download", text)


class ManifestNotesTest(unittest.TestCase):
    """`latest.json` 的 `notes`（App 內更新提示顯示的說明）取自這一版的更新說明。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.path = self.root / "docs" / "release-notes" / f"v{VERSION}.md"

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _notes(self, tag: str = "v1.2.3") -> str:
        return rel.manifest_notes(
            self.root, "kikitori", VERSION, tag, "alianlbj23/kikitori"
        )

    def test_absent_file_falls_back_to_the_title(self) -> None:
        self.assertEqual(self._notes(), "kikitori v1.2.3")

    def test_uses_the_version_notes_without_the_h1(self) -> None:
        # bytes：write_text 在 Windows 會把 \n 再轉成 \r\n。
        data = "\ufeff# kikitori v1.2.3\r\n\r\n本版重點\r\n\r\n- 術語表\r\n"
        _write(self.path, data.encode("utf-8"))
        self.assertEqual(self._notes(), "本版重點\n\n- 術語表")

    def test_short_notes_are_not_marked(self) -> None:
        body = "a" * rel.LATEST_NOTES_MAX_CHARS
        _write(self.path, body + "\n")
        self.assertEqual(self._notes(), body)

    def test_long_notes_are_cut_at_a_line_and_marked(self) -> None:
        line = "- " + "字" * 98  # 100 個字元
        _write(self.path, "\n".join([line] * 100) + "\n")  # 約 10,000 字元
        text = self._notes()
        url = "https://github.com/alianlbj23/kikitori/releases/tag/v1.2.3"
        body, marker = text.split("\n\n…", 1)
        self.assertIn(url, marker)
        self.assertLessEqual(len(body), rel.LATEST_NOTES_MAX_CHARS)
        # 在整行處截斷：不留半行。
        self.assertEqual(set(body.split("\n")), {line})
        self.assertGreater(len(body), rel.LATEST_NOTES_MAX_CHARS // 2)

    def test_one_long_line_is_cut_at_the_limit(self) -> None:
        _write(self.path, "b" * (rel.LATEST_NOTES_MAX_CHARS + 50))
        body = self._notes().split("\n\n…", 1)[0]
        self.assertEqual(body, "b" * rel.LATEST_NOTES_MAX_CHARS)

    def test_fits_the_apps_validation_limit_in_utf16(self) -> None:
        # app 的 validate.ts 以 UTF-16 長度檢查 notes（上限 10,000）；超過的話整個更新檢查
        # 會失敗、使用者看不到更新。BMP 以外的字元（emoji）在 UTF-16 佔 2 個單位。
        _write(self.path, "😀" * 20_000)
        text = self._notes()
        self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 10_000)

    def test_invalid_file_fails(self) -> None:
        _write(self.path, b"\xff\xfe")
        with self.assertRaisesRegex(rel.ReleaseError, "UTF-8"):
            self._notes()

    def test_tag_must_match_the_version(self) -> None:
        with self.assertRaisesRegex(rel.ReleaseError, "does not match version"):
            self._notes("v9.9.9")

    def test_repo_release_notes_fit(self) -> None:
        for path in sorted((ROOT / "docs" / "release-notes").glob("v*.md")):
            version = path.stem[1:]
            with self.subTest(file=path.name):
                text = rel.manifest_notes(
                    ROOT, "kikitori", version, f"v{version}", "alianlbj23/kikitori"
                )
                self.assertNotEqual(text, f"kikitori v{version}")
                self.assertNotIn(f"# kikitori v{version}", text)
                self.assertLessEqual(len(text.encode("utf-16-le")) // 2, 10_000)

    def test_workflow_takes_the_notes_from_the_version_file(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "release.yml").read_text(
            encoding="utf-8"
        )
        self.assertEqual(workflow.count("scripts/release.py manifest"), 1)
        # 指令本身與它的續行（以 `\` 結尾的行）。
        lines = workflow.split("scripts/release.py manifest", 1)[1].splitlines()
        call = [lines[0]]
        while call[-1].rstrip().endswith("\\"):
            call.append(lines[len(call)])
        manifest_call = "\n".join(call)
        self.assertIn("--notes-from-version", manifest_call)
        self.assertNotIn("--notes ", manifest_call)


class RepoConfigTest(unittest.TestCase):
    """repo 實際的設定要能讓更新器使用這裡產生的檔案。"""

    def test_updater_config_matches_release_layout(self) -> None:
        conf = rel.tauri_config(ROOT)
        self.assertEqual(rel.product_name(ROOT), "kikitori")
        self.assertIs(conf["bundle"]["createUpdaterArtifacts"], True)
        self.assertEqual(
            conf["plugins"]["updater"]["endpoints"],
            [
                "https://github.com/alianlbj23/kikitori/releases/latest/download/latest.json"
            ],
        )
        pub = rel.decode_minisign_text(rel.config_pubkey(ROOT), "public key", "conf")
        self.assertIn("minisign public key", pub.splitlines()[0])
        # macOS 不做付費簽章：以 ad-hoc 簽章（Apple Silicon 必要）。
        self.assertEqual(conf["bundle"]["macOS"]["signingIdentity"], "-")

    def test_deb_recommends_cjk_font(self) -> None:
        # 介面與字幕全是中日文；精簡安裝的 Debian／Ubuntu 只有 DejaVu，容器實測整個
        # 介面變成豆腐方塊。建議（不強制）安裝 Noto CJK。
        deb = rel.tauri_config(ROOT)["bundle"]["linux"]["deb"]
        self.assertIn("fonts-noto-cjk", deb["recommends"])

    def test_project_version_matches_tauri(self) -> None:
        self.assertEqual(rel.project_version(ROOT), rel.tauri_config(ROOT)["version"])


class CommandLineTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = _fake_root(Path(self._tmp.name))
        self.out: list[str] = []
        self.err: list[str] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _run(self, *args: str) -> int:
        return rel.run_command(
            ["--root", str(self.root), *args], out=self.out.append, err=self.err.append
        )

    def test_gate_prints_github_outputs(self) -> None:
        tags = _write(self.root / "tags.txt", "v1.0.0\nv1.2.2\n")
        code = self._run(
            "gate",
            "--ref",
            "refs/heads/main",
            "--default-branch",
            "main",
            "--tags-file",
            str(tags),
        )
        self.assertEqual(code, 0)
        self.assertEqual(
            self.out,
            ["mode=publish", "version=1.2.3", "tag=v1.2.3", "prerelease=false"],
        )
        self.assertEqual(self.err, ["publishing v1.2.3"])

    def test_gate_error_goes_to_stderr(self) -> None:
        tags = _write(self.root / "tags.txt", "v2.0.0\n")
        code = self._run(
            "gate",
            "--ref",
            "refs/heads/main",
            "--default-branch",
            "main",
            "--tags-file",
            str(tags),
        )
        self.assertEqual(code, 1)
        self.assertEqual(self.out, [])
        self.assertIn("not newer", self.err[0])

    def test_gate_missing_tags_file(self) -> None:
        code = self._run(
            "gate",
            "--ref",
            "refs/heads/x",
            "--default-branch",
            "main",
            "--tags-file",
            "nope.txt",
        )
        self.assertEqual(code, 1)
        self.assertIn("cannot read tags file", self.err[0])

    def test_collect_verify_manifest_pipeline(self) -> None:
        bundle = self.root / "bundle"
        _fake_bundles(bundle)
        for platform in rel.PLATFORMS:
            self.assertEqual(
                self._run(
                    "collect",
                    "--platform",
                    platform,
                    "--bundle-dir",
                    str(bundle),
                    "--out",
                    str(self.root / "artifacts" / platform),
                ),
                0,
                self.err,
            )
        ok = subprocess.CompletedProcess([], 0, "", "")
        with mock.patch.object(rel.subprocess, "run", return_value=ok) as run:
            self.assertEqual(
                self._run(
                    "verify",
                    "--dir",
                    str(self.root / "artifacts"),
                    "--pubkey-from-config",
                ),
                0,
            )
        self.assertEqual(run.call_count, 4)
        code = self._run(
            "manifest",
            "--dir",
            str(self.root / "artifacts"),
            "--stage",
            str(self.root / "stage"),
            "--repo",
            "alianlbj23/kikitori",
            "--tag",
            "v1.2.3",
        )
        self.assertEqual(code, 0, self.err)
        self.assertTrue((self.root / "stage" / "latest.json").is_file())
        self.assertIn("  darwin-aarch64: ", "\n".join(self.out))

    def _manifest(self, *extra: str) -> int:
        bundle = self.root / "bundle"
        _fake_bundles(bundle)
        for platform in rel.PLATFORMS:
            rel.collect(
                platform,
                bundle,
                self.root / "artifacts" / platform,
                "kikitori",
                VERSION,
            )
        return self._run(
            "manifest",
            "--dir",
            str(self.root / "artifacts"),
            "--stage",
            str(self.root / "stage"),
            "--repo",
            "alianlbj23/kikitori",
            "--tag",
            "v1.2.3",
            *extra,
        )

    def _latest_notes(self) -> str:
        latest = self.root / "stage" / "latest.json"
        return json.loads(latest.read_text(encoding="utf-8"))["notes"]

    def test_manifest_notes_from_version_file(self) -> None:
        _write(
            self.root / "docs" / "release-notes" / "v1.2.3.md",
            "# kikitori v1.2.3\n\n本版重點\n\n- 術語表\n",
        )
        self.assertEqual(self._manifest("--notes-from-version"), 0, self.err)
        self.assertEqual(self._latest_notes(), "本版重點\n\n- 術語表")

    def test_manifest_notes_from_version_without_file(self) -> None:
        self.assertEqual(self._manifest("--notes-from-version"), 0, self.err)
        self.assertEqual(self._latest_notes(), "kikitori v1.2.3")

    def test_manifest_explicit_notes_still_work(self) -> None:
        self.assertEqual(self._manifest("--notes", "hello"), 0, self.err)
        self.assertEqual(self._latest_notes(), "hello")

    def test_manifest_invalid_version_notes_fail(self) -> None:
        _write(self.root / "docs" / "release-notes" / "v1.2.3.md", b"\x00\x01")
        self.assertEqual(self._manifest("--notes-from-version"), 1)
        self.assertIn("NUL", self.err[-1])
        self.assertFalse((self.root / "stage" / "latest.json").exists())

    def test_manifest_notes_options_are_exclusive(self) -> None:
        with mock.patch("sys.stderr"):
            code = self._manifest("--notes", "x", "--notes-from-version")
        self.assertEqual(code, 2)

    def test_notes_writes_the_release_body(self) -> None:
        out_file = self.root / "out" / "notes.md"
        self.assertEqual(
            self._run("notes", "--tag", "v1.2.3", "--out", str(out_file)), 0, self.err
        )
        text = out_file.read_text(encoding="utf-8")
        self.assertEqual(text, rel.release_notes("kikitori", VERSION, "v1.2.3"))
        self.assertIn("## Installing", text)
        self.assertEqual(
            self._run("notes", "--tag", "v9.9.9", "--out", str(out_file)), 1
        )
        self.assertIn("does not match", self.err[-1])

    def test_notes_include_the_version_notes_from_root(self) -> None:
        _write(
            self.root / "docs" / "release-notes" / "v1.2.3.md",
            "# kikitori v1.2.3\n\n本版重點\n",
        )
        out_file = self.root / "out" / "notes.md"
        self.assertEqual(
            self._run("notes", "--tag", "v1.2.3", "--out", str(out_file)), 0, self.err
        )
        text = out_file.read_text(encoding="utf-8")
        self.assertTrue(text.startswith("kikitori v1.2.3\n\n本版重點\n"))
        self.assertIn("## Download", text)

    def test_notes_invalid_version_notes_fail(self) -> None:
        _write(self.root / "docs" / "release-notes" / "v1.2.3.md", b"\xff\xfe")
        out_file = self.root / "out" / "notes.md"
        self.assertEqual(
            self._run("notes", "--tag", "v1.2.3", "--out", str(out_file)), 1
        )
        self.assertIn("UTF-8", self.err[-1])
        self.assertFalse(out_file.exists())

    def test_usage_errors(self) -> None:
        with mock.patch("sys.stderr"):
            self.assertEqual(self._run("collect", "--platform", "android"), 2)
            self.assertEqual(self._run(), 2)

    def test_project_version_errors(self) -> None:
        _write(self.root / "pyproject.toml", "[project]\nname = 'x'\n")
        with self.assertRaisesRegex(rel.ReleaseError, "no \\[project\\].version"):
            rel.project_version(self.root)
        _write(self.root / "pyproject.toml", "not toml [")
        with self.assertRaisesRegex(rel.ReleaseError, "cannot read pyproject"):
            rel.project_version(self.root)


class MenuTest(unittest.TestCase):
    def _menu(self, answers: list[str]) -> tuple[int, str]:
        out: list[str] = []
        it = iter(answers)

        def input_fn(_: str) -> str:
            try:
                return next(it)
            except StopIteration:
                raise EOFError from None

        code = rel.menu(ROOT, input_fn=input_fn, out=out.append)
        return code, "\n".join(out)

    def test_preview_and_exit(self) -> None:
        def git(args: list[str], _root: Path) -> str:
            return "feat/x\n" if args[0] == "rev-parse" else "v0.0.1\n"

        with mock.patch.object(rel, "_git", side_effect=git):
            code, text = self._menu(["1", "3"])
        self.assertEqual(code, 0)
        self.assertIn("refs/heads/feat/x", text)
        self.assertIn("[release-dry-run]", text)

    def test_help_invalid_and_eof(self) -> None:
        code, text = self._menu(["2", "9"])
        self.assertEqual(code, 0)
        self.assertIn("manifest", text)
        self.assertIn("無效的選項", text)

    def test_preview_git_failure(self) -> None:
        with mock.patch.object(rel, "_git", side_effect=rel.ReleaseError("no git")):
            code, text = self._menu(["1"])
        self.assertEqual(code, 1)
        self.assertIn("error: no git", text)


if __name__ == "__main__":
    unittest.main()
