"""`packaging/engine/`（policy、build、entry）的測試：不呼叫 PyInstaller，以假的子行程與暫存目錄驗證邏輯。

打包腳本不是套件的一部分（目錄名 `packaging` 也會與 PyPI 套件撞名），依檔案路徑載入。
"""

import importlib.util
import os
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
ENGINE_DIR = ROOT / "packaging" / "engine"


def load(name: str) -> ModuleType:
    """依路徑載入 `packaging/engine/<name>.py`（與 build.py 用同一個模組名稱）。"""
    module_name = f"kikitori_build_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(
        module_name, ENGINE_DIR / f"{name}.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


policy = load("policy")
build = load("build")
entry = load("entry")


def touch(path: Path, data: bytes = b"x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


class PolicyTest(unittest.TestCase):
    def test_nvidia_and_libpulse_files_are_forbidden(self) -> None:
        for name in (
            "ctranslate2/cudnn64_9.dll",
            "cublas64_12.dll",
            "cublasLt64_12.dll",
            "libcublas.so.12",
            "libcudart.so.12",
            "libcudnn_ops.so.9",
            "nvrtc64_120_0.dll",
            "onnxruntime/capi/onnxruntime_providers_cuda.dll",
            "libonnxruntime_providers_tensorrt.so",
            "libpulse.so.0",
            "libpulse-simple.so.0",
            "LIBPULSE.SO.0",
            "libcuda.so.1",
        ):
            with self.subTest(name=name):
                self.assertTrue(policy.is_forbidden(name))

    def test_regular_runtime_files_are_allowed(self) -> None:
        for name in (
            "ctranslate2/ctranslate2.dll",
            "ctranslate2/libiomp5md.dll",
            "onnxruntime/capi/onnxruntime_providers_shared.dll",
            "onnxruntime/capi/onnxruntime.dll",
            "python312.dll",
            "kikitori/vad/models/silero_vad.onnx",
            "soundcard/pulseaudio.py.h",
            "cudaext_helper.py",
        ):
            with self.subTest(name=name):
                self.assertFalse(policy.is_forbidden(name))

    def test_filter_toc_drops_forbidden_and_system_provided_files(self) -> None:
        toc = [
            ("ctranslate2\\ctranslate2.dll", "/src/a", "BINARY"),
            ("ctranslate2\\cudnn64_9.dll", "/src/b", "BINARY"),
            ("api-ms-win-crt-heap-l1-1-0.dll", "/src/c", "BINARY"),
            ("ucrtbase.dll", "/src/d", "BINARY"),
            ("vcruntime140.dll", "/src/e", "BINARY"),
        ]
        kept, removed = policy.filter_toc(toc)
        self.assertEqual(
            [e[0] for e in kept], ["ctranslate2\\ctranslate2.dll", "vcruntime140.dll"]
        )
        self.assertEqual(len(removed), 3)

    def test_find_forbidden_scans_the_whole_tree(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            touch(root / "_internal" / "numpy" / "core.pyd")
            bad = touch(root / "_internal" / "ctranslate2" / "cudnn64_9.dll")
            self.assertEqual(policy.find_forbidden(root), [bad])
            self.assertEqual(policy.find_forbidden(bad), [bad])
            self.assertEqual(
                policy.find_forbidden(root / "_internal" / "numpy" / "core.pyd"), []
            )


class TargetTripleTest(unittest.TestCase):
    @staticmethod
    def rustc(stdout: str, code: int = 0) -> mock.Mock:
        return mock.Mock(
            return_value=subprocess.CompletedProcess(["rustc"], code, stdout, "")
        )

    def test_explicit_and_env_override_rustc(self) -> None:
        run = self.rustc("host: x86_64-pc-windows-msvc\n")
        self.assertEqual(
            build.rust_target_triple("aarch64-apple-darwin", env={}, run=run),
            "aarch64-apple-darwin",
        )
        self.assertEqual(
            build.rust_target_triple(
                env={build.TARGET_TRIPLE_ENV: " x86_64-unknown-linux-gnu "}, run=run
            ),
            "x86_64-unknown-linux-gnu",
        )
        run.assert_not_called()

    def test_uses_rustc_host_with_timeout(self) -> None:
        run = self.rustc(
            "rustc 1.90.0\nbinary: rustc\nhost: aarch64-apple-darwin\nrelease: 1.90.0\n"
        )
        self.assertEqual(
            build.rust_target_triple(env={}, run=run), "aarch64-apple-darwin"
        )
        self.assertEqual(run.call_args.args[0], ["rustc", "-vV"])
        self.assertEqual(run.call_args.kwargs["timeout"], build.RUSTC_TIMEOUT_SECS)

    def test_falls_back_when_rustc_is_missing_or_fails(self) -> None:
        expected = build.fallback_target_triple()
        missing = mock.Mock(side_effect=FileNotFoundError("rustc"))
        hanging = mock.Mock(side_effect=subprocess.TimeoutExpired(["rustc"], 1))
        self.assertEqual(build.rust_target_triple(env={}, run=missing), expected)
        self.assertEqual(build.rust_target_triple(env={}, run=hanging), expected)
        self.assertEqual(
            build.rust_target_triple(env={}, run=self.rustc("", 1)), expected
        )

    def test_rejects_malformed_triples(self) -> None:
        for bad in ("windows", "x86_64; rm -rf /", "../evil-a-b"):
            with self.subTest(bad=bad), self.assertRaises(build.BuildError):
                build.rust_target_triple(bad, env={})
        with self.assertRaises(build.BuildError):
            build.rust_target_triple(env={build.TARGET_TRIPLE_ENV: "nope"})

    def test_fallback_mapping(self) -> None:
        cases = {
            ("win32", "AMD64"): "x86_64-pc-windows-msvc",
            ("win32", "ARM64"): "aarch64-pc-windows-msvc",
            ("linux", "x86_64"): "x86_64-unknown-linux-gnu",
            ("linux", "aarch64"): "aarch64-unknown-linux-gnu",
            ("darwin", "arm64"): "aarch64-apple-darwin",
            ("darwin", "x86_64"): "x86_64-apple-darwin",
        }
        for (system, machine), triple in cases.items():
            with self.subTest(system=system, machine=machine):
                self.assertEqual(build.fallback_target_triple(system, machine), triple)
        with self.assertRaises(build.BuildError):
            build.fallback_target_triple("sunos5", "sparc")

    def test_sidecar_names(self) -> None:
        self.assertEqual(
            build.sidecar_file_name("x86_64-pc-windows-msvc", "win32"),
            "kikitori-engine-x86_64-pc-windows-msvc.exe",
        )
        self.assertEqual(
            build.sidecar_file_name("aarch64-apple-darwin", "darwin"),
            "kikitori-engine-aarch64-apple-darwin",
        )


class CommandAndPathsTest(unittest.TestCase):
    def test_onefile_and_onedir_use_separate_locations(self) -> None:
        onedir = build.paths_for("onedir", Path("/d"), Path("/w"))
        onefile = build.paths_for("onefile", Path("/d"), Path("/w"))
        self.assertEqual(onedir.bundle, Path("/d/kikitori-engine"))
        self.assertEqual(
            onedir.licenses_dir, Path("/d/kikitori-engine/THIRD_PARTY_LICENSES")
        )
        self.assertEqual(onefile.dist_dir, Path("/d/onefile"))
        self.assertEqual(onefile.bundle, onefile.executable)
        self.assertNotEqual(onedir.work_dir, onefile.work_dir)

    def test_pyinstaller_command(self) -> None:
        onedir = build.pyinstaller_command(build.paths_for("onedir"), python="py")
        onefile = build.pyinstaller_command(build.paths_for("onefile"), python="py")
        self.assertEqual(onedir[:3], ["py", "-m", "PyInstaller"])
        self.assertIn(str(build.SPEC_FILE), onedir)
        self.assertNotIn("--onefile", onedir)
        self.assertEqual(onefile[-1], "--onefile")
        # 給 spec 的參數在 `--` 之後。
        self.assertLess(onedir.index("--"), onedir.index("--licenses-out"))

    def test_project_version_reads_pyproject(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            pyproject = Path(tmp) / "pyproject.toml"
            pyproject.write_text(
                '[project]\nname = "x"\nversion = "3.4.5"\n', encoding="utf-8"
            )
            self.assertEqual(build.project_version(pyproject), "3.4.5")
            pyproject.write_text('[project]\nname = "x"\n', encoding="utf-8")
            with self.assertRaises(build.BuildError):
                build.project_version(pyproject)

    def test_installed_version_mismatch_is_an_error(self) -> None:
        with (
            mock.patch.object(build.metadata, "version", return_value="0.0.1"),
            self.assertRaisesRegex(build.BuildError, "uv sync"),
        ):
            build.check_installed_version("0.1.0")
        missing = build.metadata.PackageNotFoundError("kikitori")
        with (
            mock.patch.object(build.metadata, "version", side_effect=missing),
            self.assertRaises(build.BuildError),
        ):
            build.check_installed_version("0.1.0")


class BuildFlowTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.paths = build.BuildPaths("onedir", self.tmp / "dist", self.tmp / "work")
        self.lines: list[str] = []
        version = mock.patch.object(build, "check_installed_version")
        version.start()
        self.addCleanup(version.stop)

    def fake_pyinstaller(self, files: list[str], code: int = 0) -> mock.Mock:
        def run(
            command: list[str], **kwargs: Any
        ) -> subprocess.CompletedProcess[bytes]:
            self.assertEqual(kwargs["timeout"], build.BUILD_TIMEOUT_SECS)
            for relative in files:
                touch(self.paths.bundle / relative)
            return subprocess.CompletedProcess(command, code)

        return mock.Mock(side_effect=run)

    def test_successful_build_reports_size(self) -> None:
        exe = build.executable_name()
        run = self.fake_pyinstaller([exe, "_internal/python312.dll"])
        result = build.build("onedir", paths=self.paths, run=run, out=self.lines.append)
        self.assertEqual(result.size_bytes, 2)
        self.assertEqual(result.version, build.project_version())
        self.assertIn("--distpath", run.call_args.args[0])

    def test_pyinstaller_failure_or_timeout_is_a_build_error(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "exit code 3"):
            build.build(
                "onedir",
                paths=self.paths,
                run=self.fake_pyinstaller([], 3),
                out=self.lines.append,
            )
        hanging = mock.Mock(side_effect=subprocess.TimeoutExpired(["pyinstaller"], 1))
        with self.assertRaisesRegex(build.BuildError, "timed out"):
            build.build("onedir", paths=self.paths, run=hanging, out=self.lines.append)

    def test_missing_executable_is_a_build_error(self) -> None:
        with self.assertRaisesRegex(build.BuildError, "missing"):
            build.build(
                "onedir",
                paths=self.paths,
                run=self.fake_pyinstaller(["_internal/x.dll"]),
                out=self.lines.append,
            )

    def test_forbidden_library_in_output_fails_the_build(self) -> None:
        run = self.fake_pyinstaller(
            [build.executable_name(), "_internal/cublas64_12.dll"]
        )
        with self.assertRaisesRegex(build.BuildError, "cublas64_12.dll"):
            build.build("onedir", paths=self.paths, run=run, out=self.lines.append)

    def test_run_default_reports_failure_as_exit_code(self) -> None:
        with mock.patch.object(build, "build", side_effect=build.BuildError("boom")):
            code = build.run_default(
                "onedir",
                sidecar_dir=None,
                triple=None,
                run_smoke=False,
                out=self.lines.append,
            )
        self.assertEqual(code, 1)
        self.assertEqual(self.lines, ["BUILD FAILED: boom"])


class InstallSidecarTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.target = self.tmp / "binaries"
        self.unrelated = touch(self.target / "README.md", b"keep")

    def result(self, mode: str) -> Any:
        paths = build.paths_for(mode, self.tmp / "dist", self.tmp / "work")  # type: ignore[arg-type]
        touch(paths.executable, b"exe")
        touch(paths.licenses_dir / "README.txt", b"licenses")
        if mode == "onedir":
            touch(paths.bundle / "_internal" / "python312.dll", b"dll")
        return build.BuildResult(paths, "0.1.0", 0, 0.0)

    def test_onedir_copies_the_folder_and_replaces_old_copy(self) -> None:
        stale = touch(self.target / "kikitori-engine" / "stale.txt")
        placed = build.install_sidecar(self.result("onedir"), self.target)
        self.assertEqual(placed, self.target / "kikitori-engine")
        self.assertTrue((placed / build.executable_name()).is_file())
        self.assertTrue((placed / "_internal" / "python312.dll").is_file())
        self.assertTrue((placed / "THIRD_PARTY_LICENSES" / "README.txt").is_file())
        self.assertFalse(stale.exists())
        self.assertEqual(self.unrelated.read_bytes(), b"keep")

    def test_onefile_uses_tauri_external_bin_name(self) -> None:
        placed = build.install_sidecar(
            self.result("onefile"), self.target, "x86_64-unknown-linux-gnu"
        )
        self.assertEqual(
            placed.name, build.sidecar_file_name("x86_64-unknown-linux-gnu")
        )
        self.assertEqual(placed.read_bytes(), b"exe")
        licenses = self.target / "kikitori-engine-THIRD_PARTY_LICENSES" / "README.txt"
        self.assertEqual(licenses.read_bytes(), b"licenses")
        self.assertEqual(self.unrelated.read_bytes(), b"keep")
        with self.assertRaises(ValueError):
            build.install_sidecar(self.result("onefile"), self.target, None)


def scripted(*answers: str) -> mock.Mock:
    it: Iterator[str] = iter(answers)

    def fake_input(prompt: str) -> str:
        try:
            return next(it)
        except StopIteration:
            raise EOFError from None

    return mock.Mock(side_effect=fake_input)


class EntryPointsTest(unittest.TestCase):
    def test_build_main_without_args_shows_menu_unless_noninteractive(self) -> None:
        with (
            mock.patch.object(build, "menu", return_value=0) as menu,
            mock.patch.object(build, "run_default", return_value=0) as run_default,
        ):
            self.assertEqual(build.main([], env={}), 0)
            menu.assert_called_once()
            run_default.assert_not_called()
            self.assertEqual(build.main([], env={build.NONINTERACTIVE_ENV: "1"}), 0)
            self.assertEqual(run_default.call_args.args, ("onedir",))
            self.assertEqual(
                run_default.call_args.kwargs["sidecar_dir"],
                build.DEFAULT_SIDECAR_DIR.resolve(),
            )

    def test_build_main_parses_ci_arguments(self) -> None:
        with mock.patch.object(build, "run_default", return_value=0) as run_default:
            build.main(
                [
                    "--onefile",
                    "--no-sidecar",
                    "--smoke",
                    "--target-triple",
                    "aarch64-apple-darwin",
                ],
                env={},
            )
        self.assertEqual(run_default.call_args.args, ("onefile",))
        self.assertIsNone(run_default.call_args.kwargs["sidecar_dir"])
        self.assertTrue(run_default.call_args.kwargs["run_smoke"])
        self.assertEqual(run_default.call_args.kwargs["triple"], "aarch64-apple-darwin")

    def test_build_menu_exit_and_invalid_choice(self) -> None:
        lines: list[str] = []
        self.assertEqual(build.menu(scripted("9", "6"), lines.append), 0)
        self.assertIn("無效的選項：'9'", lines)
        self.assertEqual(build.menu(scripted(), lines.append), 0)

    def test_entry_rejects_unknown_arguments(self) -> None:
        with mock.patch.object(entry.sys, "stderr") as stderr:
            self.assertEqual(entry.main(["--bogus"]), 2)
        stderr.write.assert_called_once_with(entry.USAGE)

    def test_entry_without_arguments_runs_the_engine(self) -> None:
        with mock.patch.object(entry, "_run_engine", return_value=0) as run_engine:
            self.assertEqual(entry.main([]), 0)
        run_engine.assert_called_once_with()

    def test_entry_prequantize_flag_runs_the_child(self) -> None:
        # 打包後引擎以「自己的執行檔 + 這個旗標」啟動衍生快取子行程，兩邊的字串要一致
        from kikitori.asr import prequantize_child, prequantize_process

        self.assertEqual(entry.PREQUANTIZE_FLAG, prequantize_process.PREQUANTIZE_FLAG)
        self.assertNotIn(entry.PREQUANTIZE_FLAG, entry.USAGE)  # 隱藏，不給使用者用
        with mock.patch.object(prequantize_child, "run", return_value=3) as child:
            self.assertEqual(entry.main([entry.PREQUANTIZE_FLAG]), 3)
        child.assert_called_once_with()

    def test_self_check_reports_every_failure(self) -> None:
        def fail() -> str:
            raise FileNotFoundError("model missing")

        result = entry.run_self_check(
            [("good", lambda: "fine"), ("bad", fail), ("also", lambda: "ok")]
        )
        self.assertFalse(result["ok"])
        self.assertEqual(
            [(c["name"], c["ok"]) for c in result["checks"]],
            [("good", True), ("bad", False), ("also", True)],
        )
        self.assertEqual(
            result["checks"][1]["detail"], "FileNotFoundError: model missing"
        )

    def test_self_check_passes_in_the_development_environment(self) -> None:
        # 打包前先確認同一組檢查在原始碼環境就會通過（三個平台的 CI 都會跑）。
        result = entry.run_self_check()
        failed = [c for c in result["checks"] if not c["ok"]]
        self.assertEqual(failed, [])
        self.assertFalse(result["frozen"])

    def test_audio_backend_check_for_unknown_platform_fails(self) -> None:
        with self.assertRaises(RuntimeError):
            entry._check_audio_backend("sunos5")


class SmokeHelpersTest(unittest.TestCase):
    smoke = load("smoke_test")

    def test_fake_env_isolates_config(self) -> None:
        env = self.smoke.fake_env(
            "/tmp/cfg", {"PATH": "/bin", "KIKITORI_ENGINE_FAKE": "0"}
        )
        self.assertEqual(env["KIKITORI_ENGINE_FAKE"], "1")
        self.assertEqual(env["KIKITORI_CONFIG_DIR"], "/tmp/cfg")
        self.assertEqual(env["PATH"], "/bin")

    def test_measure_startup_validates_runs(self) -> None:
        for bad in (0, 51, True, 2.5):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                self.smoke.measure_startup(["unused"], bad)

    def test_run_reports_missing_binary_as_failure(self) -> None:
        lines: list[str] = []
        missing = str(Path(tempfile.gettempdir()) / "no-such-kikitori-engine")
        self.assertEqual(self.smoke.run([missing], out=lines.append), 1)
        self.assertTrue(lines[-1].startswith("SMOKE TEST FAILED"))

    def test_smoke_main_requires_a_target(self) -> None:
        with mock.patch.object(sys, "stderr"), self.assertRaises(SystemExit):
            self.smoke.main(["--self-check"])


class DevEngineSmokeTest(unittest.TestCase):
    """對開發環境的引擎（`python -m kikitori engine`，假模式）跑完整的冒煙測試。

    CI 對建置好的執行檔跑的是同一段程式，這裡先確保它本身是對的。
    """

    def test_protocol_smoke_against_dev_engine(self) -> None:
        smoke = load("smoke_test")
        env = dict(os.environ)
        report = smoke.run_protocol(
            smoke.dev_command(), expect_version=build.project_version(), env=env
        )
        self.assertEqual(report.info["name"], "kikitori-engine")
        self.assertIn(smoke.FAKE_SPEECH_DEVICE, report.device_ids)
        self.assertGreaterEqual(report.final_subtitles, 1)
        self.assertEqual(report.exit_code, 0)


if __name__ == "__main__":
    unittest.main()
