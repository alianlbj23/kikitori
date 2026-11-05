"""作業系統、CPU、RAM 偵測的測試：各平台的解析器以固定文字測試，平台 API 以假實作替換。"""

import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kikitori.hardware import system
from kikitori.hardware.system import (
    count_core_records,
    count_sysfs_cores,
    detect_apple_silicon,
    detect_cpu,
    detect_memory,
    macos_apple_silicon,
    os_name,
    parse_cpuinfo,
    parse_meminfo,
    parse_vm_stat,
    sysctl_int,
    sysctl_str,
)
from kikitori.hardware.types import MemoryInfo

MEMINFO = """MemTotal:       16303428 kB
MemFree:         1234567 kB
MemAvailable:    9876543 kB
Buffers:          123456 kB
"""

CPUINFO_X86 = "\n\n".join(
    f"""processor\t: {i}
vendor_id\t: AuthenticAMD
model name\t: AMD Ryzen 7 5700X 8-Core Processor
physical id\t: 0
core id\t\t: {i % 8}"""
    for i in range(16)
)

CPUINFO_TWO_SOCKETS = "\n\n".join(
    f"processor\t: {i}\nmodel name\t: Xeon\nphysical id\t: {i // 4}\ncore id\t\t: {i % 4}"
    for i in range(8)
)

CPUINFO_ARM = """processor\t: 0
BogoMIPS\t: 108.00
Features\t: fp asimd

processor\t: 1
BogoMIPS\t: 108.00
"""

VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                               10000.
Pages active:                            200000.
Pages inactive:                           30000.
Pages speculative:                         5000.
Pages wired down:                         40000.
"""


def slpi_record(relationship: int, size: int = 48) -> bytes:
    return struct.pack("<II", relationship, size) + b"\0" * (size - 8)


class FakeSysctl:
    def __init__(self, values: dict[str, bytes]) -> None:
        self.values = values

    def __call__(self, name: str) -> bytes | None:
        return self.values.get(name)


MAC_SYSCTL = FakeSysctl(
    {
        "hw.memsize": (16 * 1024**3).to_bytes(8, "little"),
        "hw.physicalcpu": (8).to_bytes(4, "little"),
        "hw.logicalcpu": (8).to_bytes(4, "little"),
        "machdep.cpu.brand_string": b"Apple M2\0",
        "hw.optional.arm64": (1).to_bytes(4, "little"),
    }
)


class OsNameTest(unittest.TestCase):
    def test_names(self) -> None:
        self.assertEqual(os_name("win32"), "windows")
        self.assertEqual(os_name("darwin"), "macos")
        self.assertEqual(os_name("linux"), "linux")
        self.assertEqual(os_name("freebsd14"), "other")

    def test_version_strings(self) -> None:
        self.assertTrue(system.os_version("windows").startswith("Windows"))
        self.assertTrue(system.os_version("macos").startswith("macOS"))
        with mock.patch(
            "platform.freedesktop_os_release", side_effect=OSError("missing")
        ):
            self.assertTrue(system.os_version("linux").startswith("Linux"))


class LinuxParsersTest(unittest.TestCase):
    def test_meminfo(self) -> None:
        info = parse_meminfo(MEMINFO)
        self.assertEqual(info.total_bytes, 16303428 * 1024)
        self.assertEqual(info.available_bytes, 9876543 * 1024)

    def test_meminfo_old_kernel_and_garbage(self) -> None:
        self.assertEqual(
            parse_meminfo("MemTotal: 1000 kB\n"), MemoryInfo(1024000, None)
        )
        self.assertEqual(parse_meminfo("garbage"), MemoryInfo(None, None))

    def test_cpuinfo_smt(self) -> None:
        model, physical = parse_cpuinfo(CPUINFO_X86)
        self.assertEqual(model, "AMD Ryzen 7 5700X 8-Core Processor")
        self.assertEqual(physical, 8)

    def test_cpuinfo_two_sockets(self) -> None:
        self.assertEqual(parse_cpuinfo(CPUINFO_TWO_SOCKETS), ("Xeon", 8))

    def test_cpuinfo_arm_without_core_ids(self) -> None:
        self.assertEqual(parse_cpuinfo(CPUINFO_ARM), (None, None))

    def test_sysfs_cores(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for cpu, core in ((0, 0), (1, 1), (2, 0), (3, 1)):
                topo = root / f"cpu{cpu}" / "topology"
                topo.mkdir(parents=True)
                (topo / "physical_package_id").write_text("0\n")
                (topo / "core_id").write_text(f"{core}\n")
            (root / "cpu4" / "topology").mkdir(parents=True)  # 離線 CPU：沒有檔案
            (root / "cpufreq").mkdir()
            self.assertEqual(count_sysfs_cores(root), 2)
            self.assertIsNone(count_sysfs_cores(root / "missing"))

    def test_detect_linux_with_fake_files(self) -> None:
        files = {"/proc/cpuinfo": CPUINFO_ARM, "/proc/meminfo": MEMINFO}

        def fake_read(path: Path) -> str:
            return files[path.as_posix()]

        with (
            mock.patch.object(system, "_read_small_text", fake_read),
            mock.patch.object(system, "count_sysfs_cores", return_value=4),
            mock.patch("os.cpu_count", return_value=4),
        ):
            cpu, errors = detect_cpu("linux", "aarch64")
            memory, mem_errors = detect_memory("linux")
        self.assertEqual(errors, [])
        self.assertEqual(cpu.physical_cores, 4)
        self.assertEqual(cpu.logical_cores, 4)
        self.assertEqual(memory.total_bytes, 16303428 * 1024)
        self.assertEqual(mem_errors, [])

    def test_detect_linux_read_failure_is_reported(self) -> None:
        with (
            mock.patch.object(
                system, "_read_small_text", side_effect=PermissionError("denied")
            ),
            self.assertLogs("kikitori.hardware.system", "WARNING"),
        ):
            memory, errors = detect_memory("linux")
        self.assertEqual(memory, MemoryInfo(None, None))
        self.assertEqual(len(errors), 1)
        self.assertIn("memory", errors[0])


class WindowsParsersTest(unittest.TestCase):
    def test_count_core_records(self) -> None:
        buffer = slpi_record(0) * 8 + slpi_record(3, 80)
        self.assertEqual(count_core_records(buffer), 8)

    def test_count_core_records_rejects_bad_sizes(self) -> None:
        with self.assertRaises(ValueError):
            count_core_records(struct.pack("<II", 0, 0))
        with self.assertRaises(ValueError):
            count_core_records(struct.pack("<II", 0, 100))
        with self.assertRaises(ValueError):
            count_core_records(b"\0\0\0")

    def test_windows_failure_is_reported(self) -> None:
        with (
            mock.patch.object(
                system, "_windows_cpu_name", side_effect=OSError("no registry")
            ),
            self.assertLogs("kikitori.hardware.system", "WARNING"),
        ):
            cpu, errors = detect_cpu("windows", "AMD64")
        self.assertIsNone(cpu.physical_cores)
        self.assertEqual(len(errors), 1)

    @unittest.skipUnless(sys.platform == "win32", "Windows API")
    def test_real_windows_api(self) -> None:
        cpu, errors = detect_cpu("windows", "AMD64")
        memory, mem_errors = detect_memory("windows")
        self.assertEqual(errors + mem_errors, [])
        self.assertGreater(cpu.physical_cores or 0, 0)
        self.assertLessEqual(cpu.physical_cores or 0, cpu.logical_cores or 0)
        self.assertGreater(memory.total_bytes or 0, 0)
        self.assertLessEqual(memory.available_bytes or 0, memory.total_bytes or 0)


class MacParsersTest(unittest.TestCase):
    def test_vm_stat(self) -> None:
        self.assertEqual(parse_vm_stat(VM_STAT), (10000 + 30000 + 5000) * 16384)
        self.assertIsNone(parse_vm_stat("nothing"))
        self.assertIsNone(parse_vm_stat("(page size of 4096 bytes)\nPages active: 1."))

    def test_sysctl_helpers(self) -> None:
        self.assertEqual(sysctl_int(MAC_SYSCTL, "hw.physicalcpu"), 8)
        self.assertEqual(sysctl_int(MAC_SYSCTL, "hw.memsize"), 16 * 1024**3)
        self.assertIsNone(sysctl_int(MAC_SYSCTL, "missing"))
        self.assertIsNone(sysctl_int(FakeSysctl({"x": b"abc"}), "x"))
        self.assertEqual(sysctl_str(MAC_SYSCTL, "machdep.cpu.brand_string"), "Apple M2")

    def test_apple_silicon(self) -> None:
        self.assertTrue(macos_apple_silicon("arm64", FakeSysctl({})))
        # Rosetta 下 machine 為 x86_64，但 hw.optional.arm64 = 1
        self.assertTrue(macos_apple_silicon("x86_64", MAC_SYSCTL))
        self.assertFalse(macos_apple_silicon("x86_64", FakeSysctl({})))
        self.assertTrue(detect_apple_silicon("macos", "arm64", FakeSysctl({})))
        self.assertFalse(detect_apple_silicon("linux", "arm64"))

    def test_detect_macos(self) -> None:
        cpu, errors = detect_cpu("macos", "arm64", sysctl=MAC_SYSCTL)
        self.assertEqual(errors, [])
        self.assertEqual(cpu.model, "Apple M2")
        self.assertEqual((cpu.physical_cores, cpu.logical_cores), (8, 8))
        with mock.patch.object(system, "_macos_available_memory", return_value=123):
            memory, mem_errors = detect_memory("macos", sysctl=MAC_SYSCTL)
        self.assertEqual(memory, MemoryInfo(16 * 1024**3, 123))
        self.assertEqual(mem_errors, [])

    def test_vm_stat_timeout_is_reported(self) -> None:
        import subprocess

        with (
            mock.patch.object(
                system,
                "_macos_available_memory",
                side_effect=subprocess.TimeoutExpired("vm_stat", 3),
            ),
            self.assertLogs("kikitori.hardware.system", "WARNING"),
        ):
            memory, errors = detect_memory("macos", sysctl=MAC_SYSCTL)
        self.assertEqual(memory, MemoryInfo(None, None))
        self.assertEqual(len(errors), 1)


if __name__ == "__main__":
    unittest.main()
