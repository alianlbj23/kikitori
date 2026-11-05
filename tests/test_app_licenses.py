"""`packaging/app/licenses.py` 的測試：假的 node_modules／package-lock.json 與 cargo-about JSON。"""

import contextlib
import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from types import ModuleType
from typing import Any

ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    name = "kikitori_app_licenses"
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        name, ROOT / "packaging" / "app" / "licenses.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


lic = _load()

CRATES_IO = "registry+https://github.com/rust-lang/crates.io-index"


class FakeApp:
    """暫存的 app/：package-lock.json 與 node_modules。"""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.packages: dict[str, dict[str, Any]] = {
            "": {"name": "app", "version": "0.1.0", "dependencies": {}}
        }

    def add(
        self,
        key: str,
        version: str = "1.0.0",
        *,
        license: str | None = "MIT",
        files: dict[str, str] | None = None,
        dev: bool = False,
        lock_extra: dict[str, Any] | None = None,
        installed_version: str | None = None,
        install: bool = True,
    ) -> None:
        name = key.rsplit("node_modules/", 1)[-1]
        entry: dict[str, Any] = {"version": version, **(lock_extra or {})}
        if dev:
            entry["dev"] = True
        self.packages[key] = entry
        if not install:
            return
        directory = self.root / key
        directory.mkdir(parents=True, exist_ok=True)
        manifest: dict[str, Any] = {
            "name": name,
            "version": installed_version or version,
            "repository": {"type": "git", "url": f"https://example.com/{name}.git"},
        }
        if license is not None:
            manifest["license"] = license
        (directory / "package.json").write_text(json.dumps(manifest), encoding="utf-8")
        for file_name, text in (
            {"LICENSE": f"{name} license text"} if files is None else files
        ).items():
            (directory / file_name).write_text(text, encoding="utf-8")

    def root_deps(self, *names: str, field: str = "dependencies") -> None:
        self.packages[""].setdefault(field, {}).update({n: "*" for n in names})

    def write_lock(self) -> None:
        lock = {"name": "app", "lockfileVersion": 3, "packages": self.packages}
        (self.root / "package-lock.json").write_text(json.dumps(lock), encoding="utf-8")

    def lock(self) -> dict[str, Any]:
        return {"lockfileVersion": 3, "packages": self.packages}


def crate(name: str, version: str = "1.0.0", **extra: Any) -> dict[str, Any]:
    pkg = {
        "name": name,
        "version": version,
        "source": CRATES_IO,
        "license": "MIT",
        "repository": f"https://github.com/x/{name}",
    }
    pkg.update(extra)
    return pkg


def about_json(
    licenses: list[tuple[str, str, list[dict[str, Any]]]],
    extra_crates: Sequence[dict[str, Any]] = (),
) -> dict[str, Any]:
    """cargo-about JSON 的最小形狀：licenses[{id,name,text,used_by[{crate}]}]、crates[{package}]。"""
    seen: dict[tuple[str, str], dict[str, Any]] = {}
    for _, _, users in licenses:
        for u in users:
            seen[(u["name"], u["version"])] = u
    for c in extra_crates:
        seen[(c["name"], c["version"])] = c
    return {
        "overview": [],
        "licenses": [
            {
                "id": lid,
                "name": f"{lid} License",
                "text": f"full text of {lid}\n",
                "used_by": [{"crate": u, "path": None} for u in users],
            }
            for lid, _, users in licenses
        ],
        "crates": [{"package": c, "license": c["license"]} for c in seen.values()],
    }


class TempCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.addCleanup(self._tmp.cleanup)
        self.overrides = self.tmp / "overrides"
        self.overrides.mkdir()


class ProductionClosureTests(TempCase):
    def test_follows_dependencies_and_skips_dev_packages(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a", "b")
        app.add("node_modules/a", lock_extra={"dependencies": {"c": "^1"}})
        app.add("node_modules/b")
        app.add("node_modules/c")
        app.add("node_modules/devtool", dev=True)
        self.assertEqual(
            lic.production_closure(app.lock()),
            ["node_modules/a", "node_modules/b", "node_modules/c"],
        )

    def test_resolves_nested_copies_like_node(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a", "c")
        app.add("node_modules/a", lock_extra={"dependencies": {"c": "^2"}})
        app.add("node_modules/a/node_modules/c", "2.0.0")
        app.add("node_modules/c", "1.0.0")
        self.assertEqual(
            lic.production_closure(app.lock()),
            ["node_modules/a", "node_modules/a/node_modules/c", "node_modules/c"],
        )

    def test_nested_package_falls_back_to_parent_node_modules(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a")
        app.add("node_modules/a", lock_extra={"dependencies": {"b": "*"}})
        app.add(
            "node_modules/a/node_modules/b", lock_extra={"dependencies": {"d": "*"}}
        )
        app.add("node_modules/d")
        self.assertIn("node_modules/d", lic.production_closure(app.lock()))

    def test_optional_and_peer_dependencies(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a")
        app.root_deps("missing-optional", field="optionalDependencies")
        app.add(
            "node_modules/a",
            lock_extra={
                "peerDependencies": {"p": "*", "q": "*"},
                "peerDependenciesMeta": {"q": {"optional": True}},
            },
        )
        app.add("node_modules/p")
        self.assertEqual(
            lic.production_closure(app.lock()), ["node_modules/a", "node_modules/p"]
        )

    def test_missing_required_dependency_fails(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("ghost")
        with self.assertRaisesRegex(lic.NoticeError, "ghost"):
            lic.production_closure(app.lock())

    def test_rejects_bad_lockfiles(self) -> None:
        for bad in (
            [],
            {"lockfileVersion": 1, "packages": {"": {}}},
            {"lockfileVersion": 3},
            {"lockfileVersion": 3, "packages": {"": {}, "node_modules/x": 3}},
            {"lockfileVersion": 3, "packages": {"": {"dependencies": []}}},
        ):
            with self.subTest(bad=bad), self.assertRaises(lic.NoticeError):
                lic.production_closure(bad)

    def test_linked_packages_are_rejected(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("w")
        app.add("node_modules/w", lock_extra={"link": True, "resolved": "packages/w"})
        with self.assertRaisesRegex(lic.NoticeError, "linked"):
            lic.production_closure(app.lock())

    def test_real_lockfile_bundles_only_tauri_packages(self) -> None:
        lock = json.loads(
            (ROOT / "app" / "package-lock.json").read_text(encoding="utf-8")
        )
        keys = lic.production_closure(lock)
        self.assertTrue(keys)
        self.assertTrue(
            all(k.startswith("node_modules/@tauri-apps/") for k in keys), keys
        )
        self.assertNotIn("node_modules/@tauri-apps/cli", keys)


class NpmPackageTests(TempCase):
    def test_collects_production_packages_and_bundler_runtime(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("@scope/api")
        app.add(
            "node_modules/@scope/api",
            "2.0.0",
            license="Apache-2.0 OR MIT",
            files={
                "LICENSE-MIT": "mit",
                "LICENSE-APACHE-2.0": "apache",
                "README.md": "x",
            },
        )
        app.add("node_modules/vite", "8.0.0", dev=True)
        app.add("node_modules/typescript", "6.0.0", dev=True)
        app.write_lock()
        packages = lic.collect_npm(self.tmp, self.overrides)
        self.assertEqual([p.name for p in packages], ["@scope/api", "vite"])
        api = packages[0]
        self.assertEqual(api.version, "2.0.0")
        self.assertEqual(api.license, "Apache-2.0 OR MIT")
        self.assertEqual(api.repository, "https://example.com/@scope/api.git")
        self.assertEqual(
            [f for f, _ in api.files], ["LICENSE-APACHE-2.0", "LICENSE-MIT"]
        )
        self.assertFalse(api.override)
        self.assertIn("modulepreload", packages[1].reason)

    def test_spdx_metadata_alone_is_not_a_license_text(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("plug")
        app.add(
            "node_modules/plug", files={"LICENSE.spdx": "PackageLicenseDeclared: MIT"}
        )
        app.write_lock()
        with self.assertRaisesRegex(lic.NoticeError, "ships no license text"):
            lic.collect_npm(self.tmp, self.overrides)

    def test_override_supplies_missing_text(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("@scope/plug")
        app.add(
            "node_modules/@scope/plug",
            files={"LICENSE.spdx": "PackageLicenseDeclared: MIT"},
        )
        app.write_lock()
        override = self.overrides / "@scope" / "plug"
        override.mkdir(parents=True)
        (override / "LICENSE_MIT").write_text("upstream mit", encoding="utf-8")
        (override / "SOURCE.md").write_text("where it came from", encoding="utf-8")
        [plug] = lic.collect_npm(self.tmp, self.overrides)
        self.assertTrue(plug.override)
        self.assertEqual([f for f, _ in plug.files], ["LICENSE.spdx", "LICENSE_MIT"])

    def test_missing_package_json_asks_for_npm_ci(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a")
        app.add("node_modules/a", install=False)
        app.write_lock()
        with self.assertRaisesRegex(lic.NoticeError, "npm ci"):
            lic.collect_npm(self.tmp, self.overrides)

    def test_installed_version_must_match_the_lock(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a")
        app.add("node_modules/a", "1.0.0", installed_version="1.0.1")
        app.write_lock()
        with self.assertRaisesRegex(lic.NoticeError, "does not match"):
            lic.collect_npm(self.tmp, self.overrides)

    def test_license_field_is_required(self) -> None:
        app = FakeApp(self.tmp)
        app.root_deps("a")
        app.add("node_modules/a", license=None)
        app.write_lock()
        with self.assertRaisesRegex(lic.NoticeError, "license"):
            lic.collect_npm(self.tmp, self.overrides)

    def test_unreadable_lockfile(self) -> None:
        with self.assertRaisesRegex(lic.NoticeError, "package-lock.json"):
            lic.collect_npm(self.tmp, self.overrides)

    def test_committed_overrides_have_full_texts_and_sources(self) -> None:
        dirs = [p.parent for p in lic.OVERRIDES_DIR.rglob("SOURCE.md")]
        self.assertTrue(dirs)
        for d in dirs:
            with self.subTest(d=d):
                files = [f for f in d.iterdir() if lic.LICENSE_NAME.match(f.name)]
                self.assertTrue(lic._has_full_text(files))


class CargoAboutTests(unittest.TestCase):
    def test_parses_crates_and_skips_local_path_crates(self) -> None:
        own = crate("kikitori-app", source=None)
        data = about_json(
            [
                ("MIT", "", [crate("serde"), own]),
                ("Apache-2.0", "", [crate("serde")]),
                ("MPL-2.0", "", [crate("cssparser", "0.37.0", license="MPL-2.0")]),
            ]
        )
        rust = lic.parse_cargo_about(data)
        self.assertEqual([c.name for c in rust.crates], ["cssparser", "serde"])
        self.assertEqual([c.name for c in rust.mpl_crates], ["cssparser"])
        mpl = rust.mpl_crates[0]
        self.assertEqual(mpl.crates_io_url, "https://crates.io/crates/cssparser/0.37.0")
        self.assertEqual(
            mpl.download_url,
            "https://static.crates.io/crates/cssparser/cssparser-0.37.0.crate",
        )

    def test_dual_licensed_crate_used_under_mit_needs_no_source_statement(self) -> None:
        dual = crate("either", license="MIT OR MPL-2.0")
        rust = lic.parse_cargo_about(about_json([("MIT", "", [dual])]))
        self.assertEqual(rust.mpl_crates, ())

    def test_crate_without_license_text_fails(self) -> None:
        data = about_json([("MIT", "", [crate("a")])], extra_crates=[crate("orphan")])
        with self.assertRaisesRegex(lic.NoticeError, "orphan"):
            lic.parse_cargo_about(data)

    def test_mpl_crate_outside_crates_io_fails(self) -> None:
        git = crate("forked", license="MPL-2.0", source="git+https://example.com/f#abc")
        with self.assertRaisesRegex(lic.NoticeError, "not from crates.io"):
            lic.parse_cargo_about(about_json([("MPL-2.0", "", [git])]))

    def test_empty_license_text_fails(self) -> None:
        data = about_json([("MIT", "", [crate("a")])])
        data["licenses"][0]["text"] = "  "
        with self.assertRaisesRegex(lic.NoticeError, "no text"):
            lic.parse_cargo_about(data)

    def test_malformed_json_shapes_fail(self) -> None:
        for bad in (
            [],
            {"licenses": [], "crates": {}},
            {"licenses": {}, "crates": []},
            {"licenses": [], "crates": [{"package": {"name": 3, "source": CRATES_IO}}]},
        ):
            with self.subTest(bad=bad), self.assertRaises(lic.NoticeError):
                lic.parse_cargo_about(bad)

    def _runner(self, returncode: int = 0, stdout: str = "{}", stderr: str = ""):
        calls: list[list[str]] = []

        def run(command: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
            calls.append(command)
            self.assertIn("timeout", kwargs)
            self.assertNotIn("shell", kwargs)
            return subprocess.CompletedProcess(command, returncode, stdout, stderr)

        return run, calls

    def test_run_cargo_about_command_line(self) -> None:
        run, calls = self._runner(stdout='{"licenses": []}')
        self.assertEqual(
            lic.run_cargo_about(Path("Cargo.toml"), runner=run), {"licenses": []}
        )
        self.assertEqual(calls[0][:3], ["cargo", "about", "generate"])
        for flag in ("--locked", "--fail", "--manifest-path"):
            self.assertIn(flag, calls[0])
        self.assertEqual(calls[0][calls[0].index("--format") + 1], "json")

    def test_run_cargo_about_failures(self) -> None:
        run, _ = self._runner(returncode=101, stderr="error: no such command: `about`")
        with self.assertRaisesRegex(lic.NoticeError, "install cargo-about"):
            lic.run_cargo_about(Path("Cargo.toml"), runner=run)
        run, _ = self._runner(stdout="not json")
        with self.assertRaisesRegex(lic.NoticeError, "invalid JSON"):
            lic.run_cargo_about(Path("Cargo.toml"), runner=run)

        def timeout(command: list[str], **kwargs: Any) -> Any:
            raise subprocess.TimeoutExpired(command, kwargs["timeout"])

        with self.assertRaisesRegex(lic.NoticeError, "timed out"):
            lic.run_cargo_about(Path("Cargo.toml"), runner=timeout, timeout=1)

        def missing(command: list[str], **kwargs: Any) -> Any:
            raise FileNotFoundError("cargo")

        with self.assertRaisesRegex(lic.NoticeError, "cargo not found"):
            lic.run_cargo_about(Path("Cargo.toml"), runner=missing)


class WriteTests(TempCase):
    def _inputs(self) -> tuple[Any, list[Any], Path]:
        rust = lic.parse_cargo_about(
            about_json(
                [
                    ("MIT", "", [crate("serde")]),
                    ("MPL-2.0", "", [crate("option-ext", "0.2.0", license="MPL-2.0")]),
                ]
            )
        )
        app = FakeApp(self.tmp / "app")
        app.root_deps("@tauri-apps/api")
        app.add("node_modules/@tauri-apps/api", "2.12.4", license="Apache-2.0 OR MIT")
        app.write_lock()
        npm = lic.collect_npm(self.tmp / "app", self.overrides)
        project = self.tmp / "LICENSE"
        project.write_text("MIT License\n\nCopyright (c) kikitori\n", encoding="utf-8")
        return rust, npm, project

    def test_writes_the_combined_folder(self) -> None:
        rust, npm, project = self._inputs()
        out = self.tmp / "licenses"
        lic.write(out, rust, npm, project)
        self.assertEqual(
            sorted(
                p.relative_to(out).as_posix() for p in out.rglob("*") if p.is_file()
            ),
            [
                "README.txt",
                "kikitori/LICENSE",
                "manifest.json",
                "npm/NOTICES.txt",
                "rust/MPL-2.0-SOURCE.txt",
                "rust/NOTICES.txt",
            ],
        )
        self.assertIn(
            "Copyright (c) kikitori", (out / "kikitori" / "LICENSE").read_text("utf-8")
        )
        mpl = (out / "rust" / "MPL-2.0-SOURCE.txt").read_text("utf-8")
        self.assertIn("option-ext 0.2.0", mpl)
        self.assertIn("https://crates.io/crates/option-ext/0.2.0", mpl)
        self.assertIn("https://github.com/x/option-ext", mpl)
        notices = (out / "rust" / "NOTICES.txt").read_text("utf-8")
        self.assertIn("full text of MPL-2.0", notices)
        self.assertIn("serde 1.0.0  [MIT]", notices)
        self.assertIn(
            "@tauri-apps/api license text",
            (out / "npm" / "NOTICES.txt").read_text("utf-8"),
        )
        self.assertIn("THIRD_PARTY_LICENSES", (out / "README.txt").read_text("utf-8"))
        manifest = json.loads((out / "manifest.json").read_text("utf-8"))
        self.assertEqual(manifest["generator"], lic.GENERATOR)
        self.assertEqual([m["name"] for m in manifest["mpl_sources"]], ["option-ext"])
        self.assertEqual([m["name"] for m in manifest["npm"]], ["@tauri-apps/api"])

    def test_replaces_its_own_output_but_not_other_folders(self) -> None:
        rust, npm, project = self._inputs()
        out = self.tmp / "licenses"
        lic.write(out, rust, npm, project)
        (out / "stale.txt").write_text("old", encoding="utf-8")
        lic.write(out, rust, npm, project)
        self.assertFalse((out / "stale.txt").exists())

        foreign = self.tmp / "precious"
        foreign.mkdir()
        (foreign / "keep.txt").write_text("mine", encoding="utf-8")
        with self.assertRaisesRegex(lic.NoticeError, "refusing"):
            lic.write(foreign, rust, npm, project)
        self.assertTrue((foreign / "keep.txt").exists())

    def test_missing_project_license_fails(self) -> None:
        rust, npm, _ = self._inputs()
        with self.assertRaisesRegex(lic.NoticeError, "missing"):
            lic.write(self.tmp / "licenses", rust, npm, self.tmp / "nope")


class CheckBundleTests(TempCase):
    """安裝後的資源目錄：`licenses/` 與引擎的 THIRD_PARTY_LICENSES 都要在。"""

    _inputs = WriteTests._inputs

    def _installed(self) -> Path:
        rust, npm, project = self._inputs()
        resources = self.tmp / "installed"
        lic.write(resources / "licenses", rust, npm, project)
        engine = resources / "kikitori-engine" / "THIRD_PARTY_LICENSES"
        engine.mkdir(parents=True)
        (engine / "README.txt").write_text("engine licenses", encoding="utf-8")
        return resources

    def test_complete_bundle_passes(self) -> None:
        resources = self._installed()
        self.assertEqual(lic.check_bundle(resources), [])
        lines: list[str] = []
        self.assertEqual(lic.verify_bundle(resources, out=lines.append), 0)
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.assertEqual(lic.main(["--check-bundle", str(resources)]), 0)
        self.assertIn("license folders OK", stdout.getvalue())

    def test_missing_folders_are_reported(self) -> None:
        problems = lic.check_bundle(self.tmp / "empty")
        self.assertIn("missing licenses/rust/MPL-2.0-SOURCE.txt", problems)
        self.assertIn(
            "missing kikitori-engine/THIRD_PARTY_LICENSES/README.txt", problems
        )
        lines: list[str] = []
        self.assertEqual(lic.verify_bundle(self.tmp / "empty", out=lines.append), 1)
        self.assertTrue(all("INCOMPLETE" in line for line in lines))

    def test_mpl_statement_must_be_present(self) -> None:
        resources = self._installed()
        (resources / "licenses" / "rust" / "MPL-2.0-SOURCE.txt").write_text(
            "nothing here\n", encoding="utf-8"
        )
        self.assertEqual(
            lic.check_bundle(resources),
            ["MPL-2.0-SOURCE.txt has no source statement for option-ext 0.2.0"],
        )

    def test_foreign_manifest_is_rejected(self) -> None:
        resources = self._installed()
        (resources / "licenses" / "manifest.json").write_text("{}", encoding="utf-8")
        self.assertEqual(len(lic.check_bundle(resources)), 1)


class CliTests(TempCase):
    def test_generate_reports_failure_with_exit_code(self) -> None:
        lines: list[str] = []
        code = lic.generate(
            out_dir=self.tmp / "out",
            app_dir=self.tmp,
            cargo_about_json=self.tmp / "missing.json",
            out=lines.append,
        )
        self.assertEqual(code, 1)
        self.assertIn("LICENSE NOTICES FAILED", lines[-1])
        self.assertFalse((self.tmp / "out").exists())

    def test_generate_from_saved_cargo_about_json(self) -> None:
        app = FakeApp(self.tmp / "app")
        app.root_deps("a")
        app.add("node_modules/a")
        app.write_lock()
        about = self.tmp / "about.json"
        about.write_text(
            json.dumps(about_json([("MIT", "", [crate("serde")])])), "utf-8"
        )
        lines: list[str] = []
        code = lic.generate(
            out_dir=self.tmp / "out",
            app_dir=self.tmp / "app",
            cargo_about_json=about,
            out=lines.append,
        )
        self.assertEqual(code, 0, lines)
        self.assertIn("1 crates (0 MPL-2.0: none), 1 npm packages (a)", lines[-1])
        self.assertTrue((self.tmp / "out" / "kikitori" / "LICENSE").is_file())

    def test_menu_exits_and_rejects_unknown_choices(self) -> None:
        answers = iter(["9", "3"])
        lines: list[str] = []
        self.assertEqual(
            lic.menu(input_fn=lambda _: next(answers), out=lines.append), 0
        )
        self.assertTrue(any("無效的選項" in line for line in lines))

        def eof(_: str) -> str:
            raise EOFError

        self.assertEqual(lic.menu(input_fn=eof, out=lambda _: None), 0)

    def test_parse_args_defaults(self) -> None:
        args = lic.parse_args(["--out", "x"])
        self.assertEqual(args.out, Path("x"))
        self.assertEqual(args.app_dir, lic.DEFAULT_APP_DIR)
        self.assertIsNone(args.cargo_about_json)

    def test_main_with_arguments_does_not_prompt(self) -> None:
        stdout = io.StringIO()
        old = sys.stdout
        sys.stdout = stdout
        try:
            code = lic.main(
                [
                    "--out",
                    str(self.tmp / "o"),
                    "--app-dir",
                    str(self.tmp),
                    "--cargo-about-json",
                    str(self.tmp / "missing.json"),
                ]
            )
        finally:
            sys.stdout = old
        self.assertEqual(code, 1)
        self.assertIn("LICENSE NOTICES FAILED", stdout.getvalue())


if __name__ == "__main__":
    unittest.main()
