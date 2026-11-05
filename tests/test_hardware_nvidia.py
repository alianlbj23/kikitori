"""`nvidia-smi` 解析與執行（假 runner，不執行真的 nvidia-smi）、cuBLAS 搜尋的測試。"""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kikitori.hardware.nvidia import (
    GPU_QUERY_FIELDS,
    LINUX_CUBLAS_FILES,
    WINDOWS_CUBLAS_FILES,
    NvidiaSmiError,
    cublas_platform_supported,
    find_cublas,
    find_nvidia_smi,
    parse_nvidia_smi_csv,
    query_nvidia_gpus,
    system_library_dirs,
)

ONE_GPU = "NVIDIA GeForce RTX 5070, 12227, 1073, 621.01, 12.0\n"
TWO_GPUS = (
    "NVIDIA GeForce RTX 4090, 24564, 500, 560.94, 8.9\n"
    "NVIDIA GeForce GTX 1650, 4096, [N/A], 560.94, 7.5\n"
)


class FakeRunner:
    """依序回傳預先準備的結果，並記錄收到的指令。"""

    def __init__(self, *results: object) -> None:
        self.results = list(results)
        self.calls: list[list[str]] = []
        self.kwargs: list[dict[str, Any]] = []

    def __call__(
        self, args: list[str], **kwargs: Any
    ) -> "subprocess.CompletedProcess[str]":
        self.calls.append(args)
        self.kwargs.append(kwargs)
        result = self.results.pop(0)
        if isinstance(result, BaseException):
            raise result
        assert isinstance(result, tuple)
        code, out, err = result
        return subprocess.CompletedProcess(args, code, out, err)


class ParseNvidiaSmiTest(unittest.TestCase):
    def test_single_gpu(self) -> None:
        gpus, errors = parse_nvidia_smi_csv(ONE_GPU)
        self.assertEqual(errors, [])
        self.assertEqual(len(gpus), 1)
        gpu = gpus[0]
        self.assertEqual(gpu.name, "NVIDIA GeForce RTX 5070")
        self.assertEqual(gpu.memory_total_mb, 12227)
        self.assertEqual(gpu.memory_used_mb, 1073)
        self.assertEqual(gpu.driver_version, "621.01")
        self.assertEqual(gpu.compute_capability_tuple, (12, 0))
        self.assertEqual(gpu.driver_major, 621)

    def test_multiple_gpus_and_na(self) -> None:
        gpus, errors = parse_nvidia_smi_csv(TWO_GPUS)
        self.assertEqual(errors, [])
        self.assertEqual([g.index for g in gpus], [0, 1])
        self.assertIsNone(gpus[1].memory_used_mb)

    def test_garbage_lines_are_reported(self) -> None:
        text = "hello world\nX, abc, 1, 1.0, 8.6\nY, -5, 1, 1.0, 8.6\n" + ONE_GPU
        gpus, errors = parse_nvidia_smi_csv(text)
        self.assertEqual(len(gpus), 1)
        self.assertEqual(len(errors), 3)

    def test_invalid_optional_fields_become_none(self) -> None:
        gpus, _ = parse_nvidia_smi_csv("GPU, 8192, 10, weird, x.y\n")
        self.assertIsNone(gpus[0].driver_version)
        self.assertIsNone(gpus[0].compute_capability)

    def test_name_with_comma(self) -> None:
        gpus, _ = parse_nvidia_smi_csv("Odd, Name, 8192, 10, 550.1, 8.6\n")
        self.assertEqual(gpus[0].name, "Odd, Name")

    def test_without_compute_cap(self) -> None:
        gpus, errors = parse_nvidia_smi_csv(
            "GPU, 8192, 10, 470.1\n", with_compute_cap=False
        )
        self.assertEqual(errors, [])
        self.assertIsNone(gpus[0].compute_capability)

    def test_empty_and_huge_vram(self) -> None:
        self.assertEqual(parse_nvidia_smi_csv(""), ([], []))
        gpus, errors = parse_nvidia_smi_csv("GPU, 99999999999, 1, 1.0, 8.0\n")
        self.assertEqual(gpus, [])
        self.assertEqual(len(errors), 1)

    def test_too_many_lines_are_capped(self) -> None:
        gpus, errors = parse_nvidia_smi_csv(ONE_GPU * 70)
        self.assertEqual(len(gpus), 64)
        self.assertIn("only 64 kept", errors[0])


class QueryNvidiaGpusTest(unittest.TestCase):
    exe = Path("nvidia-smi")

    def test_normal(self) -> None:
        runner = FakeRunner((0, ONE_GPU, ""))
        gpus, errors = query_nvidia_gpus(self.exe, run=runner, timeout=1.5)
        self.assertEqual(len(gpus), 1)
        self.assertEqual(errors, [])
        self.assertIn(f"--query-gpu={','.join(GPU_QUERY_FIELDS)}", runner.calls[0])
        self.assertEqual(runner.kwargs[0]["timeout"], 1.5)
        self.assertIs(runner.kwargs[0]["stdin"], subprocess.DEVNULL)

    def test_timeout(self) -> None:
        runner = FakeRunner(subprocess.TimeoutExpired(["nvidia-smi"], 5))
        with self.assertRaisesRegex(NvidiaSmiError, "timed out"):
            query_nvidia_gpus(self.exe, run=runner)

    def test_missing_binary(self) -> None:
        runner = FakeRunner(FileNotFoundError(2, "not found"))
        with self.assertRaisesRegex(NvidiaSmiError, "cannot run"):
            query_nvidia_gpus(self.exe, run=runner)

    def test_driver_failure(self) -> None:
        msg = "NVIDIA-SMI has failed because it couldn't communicate with the NVIDIA driver."
        runner = FakeRunner((9, msg, ""))
        with self.assertRaisesRegex(
            NvidiaSmiError, "exited with 9: NVIDIA-SMI has failed"
        ):
            query_nvidia_gpus(self.exe, run=runner)

    def test_old_driver_retries_without_compute_cap(self) -> None:
        runner = FakeRunner(
            (2, 'Field "compute_cap" is not a valid field to query.', ""),
            (0, "Old GPU, 4096, 100, 470.82\n", ""),
        )
        gpus, _ = query_nvidia_gpus(self.exe, run=runner)
        self.assertEqual(len(runner.calls), 2)
        self.assertNotIn("compute_cap", runner.calls[1][1])
        self.assertIsNone(gpus[0].compute_capability)
        self.assertEqual(gpus[0].driver_major, 470)

    def test_garbage_output(self) -> None:
        runner = FakeRunner((0, "\x00\x01 garbage, not, csv\n", ""))
        with self.assertRaises(NvidiaSmiError):
            query_nvidia_gpus(self.exe, run=runner)

    def test_no_gpus(self) -> None:
        runner = FakeRunner((0, "", ""))
        self.assertEqual(query_nvidia_gpus(self.exe, run=runner), ([], []))


class FindNvidiaSmiTest(unittest.TestCase):
    def test_path_first(self) -> None:
        found = find_nvidia_smi("windows", {}, which=lambda _: "C:/x/nvidia-smi.exe")
        self.assertEqual(found, Path("C:/x/nvidia-smi.exe"))

    def test_windows_fallback_locations(self) -> None:
        env = {"SystemRoot": "C:\\Windows", "ProgramFiles": "relative"}
        seen: list[Path] = []

        def is_file(p: Path) -> bool:
            seen.append(p)
            return "System32" in str(p)

        found = find_nvidia_smi("windows", env, which=lambda _: None, is_file=is_file)
        self.assertIsNotNone(found)
        self.assertEqual(len(seen), 1)  # 相對路徑的 ProgramFiles 被忽略

    def test_not_found(self) -> None:
        self.assertIsNone(
            find_nvidia_smi("linux", {}, which=lambda _: None, is_file=lambda _: False)
        )
        self.assertIsNone(find_nvidia_smi("macos", {}, which=lambda _: None))


class CublasSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def make_dir(self, name: str, files: tuple[str, ...]) -> Path:
        directory = self.root / name
        directory.mkdir()
        for f in files:
            (directory / f).write_bytes(b"x")
        return directory

    def test_platform_support(self) -> None:
        self.assertTrue(cublas_platform_supported("windows", "AMD64"))
        self.assertTrue(cublas_platform_supported("linux", "x86_64"))
        self.assertFalse(cublas_platform_supported("linux", "aarch64"))
        self.assertFalse(cublas_platform_supported("macos", "arm64"))

    def test_unsupported_platform(self) -> None:
        info = find_cublas("macos", "arm64", env={})
        self.assertFalse(info.supported_platform)
        self.assertFalse(info.available)

    def test_configured_dir_wins(self) -> None:
        configured = self.make_dir("configured", WINDOWS_CUBLAS_FILES)
        runtime = self.make_dir("runtime", WINDOWS_CUBLAS_FILES)
        info = find_cublas(
            "windows",
            "AMD64",
            configured_dirs=[configured],
            runtime_dir=runtime,
            env={},
        )
        self.assertTrue(info.available)
        self.assertEqual(info.source, "configured")
        self.assertEqual(info.library_dirs, (os.fspath(configured),))

    def test_runtime_dir(self) -> None:
        runtime = self.make_dir("runtime", LINUX_CUBLAS_FILES)
        info = find_cublas(
            "linux", "x86_64", runtime_dir=runtime, env={}, glob_dirs=lambda _: []
        )
        self.assertEqual(info.source, "runtime")

    def test_system_path(self) -> None:
        sysdir = self.make_dir("cuda", WINDOWS_CUBLAS_FILES)
        info = find_cublas(
            "windows", "AMD64", env={"PATH": os.fspath(sysdir)}, glob_dirs=lambda _: []
        )
        self.assertEqual(info.source, "system")

    def test_files_split_across_dirs(self) -> None:
        a = self.make_dir("a", WINDOWS_CUBLAS_FILES[:1])
        b = self.make_dir("b", WINDOWS_CUBLAS_FILES[1:])
        info = find_cublas("windows", "AMD64", configured_dirs=[a, b], env={})
        self.assertTrue(info.available)
        self.assertEqual(info.library_dirs, (os.fspath(a), os.fspath(b)))

    def test_one_file_missing(self) -> None:
        a = self.make_dir("a", WINDOWS_CUBLAS_FILES[:1])
        info = find_cublas(
            "windows", "AMD64", configured_dirs=[a], env={}, glob_dirs=lambda _: []
        )
        self.assertTrue(info.supported_platform)
        self.assertFalse(info.available)

    def test_is_file_errors_are_treated_as_missing(self) -> None:
        def broken(_: Path) -> bool:
            raise PermissionError("denied")

        with self.assertLogs("kikitori.hardware.nvidia", "INFO"):
            info = find_cublas(
                "windows",
                "AMD64",
                configured_dirs=[self.root],
                env={},
                is_file=broken,
                glob_dirs=lambda _: [],
            )
        self.assertFalse(info.available)


class SystemLibraryDirsTest(unittest.TestCase):
    def test_windows(self) -> None:
        env = {
            "CUDA_PATH": "C:\\CUDA\\v12.4",
            "CUDA_PATH_V12_8": "C:\\CUDA\\v12.8",
            "ProgramFiles": "C:\\Program Files",
            "PATH": 'C:\\Tools;relative\\dir;"C:\\Quoted";;',
        }
        dirs = system_library_dirs(
            "windows", env, glob_dirs=lambda _: ["C:\\Program Files\\CUDA\\v12.6\\bin"]
        )
        names = [os.fspath(d) for d in dirs]
        self.assertTrue(
            names[0].endswith("v12.8\\bin") or names[0].endswith("v12.8/bin")
        )
        self.assertIn(os.fspath(Path("C:\\Tools")), names)
        self.assertIn(os.fspath(Path("C:\\Quoted")), names)
        self.assertFalse(any("relative" in n for n in names))

    def test_linux(self) -> None:
        dirs = system_library_dirs(
            "linux",
            {"LD_LIBRARY_PATH": "/opt/cuda/lib:relative"},
            glob_dirs=lambda _: ["/usr/local/cuda-12.8/lib64"],
        )
        posix = [d.as_posix() for d in dirs]
        self.assertEqual(posix[0], "/opt/cuda/lib")
        self.assertIn("/usr/local/cuda/lib64", posix)
        self.assertIn("/usr/local/cuda-12.8/lib64", posix)
        self.assertNotIn("relative", posix)

    def test_other(self) -> None:
        self.assertEqual(system_library_dirs("macos", {"PATH": "/usr/bin"}), [])


if __name__ == "__main__":
    unittest.main()
