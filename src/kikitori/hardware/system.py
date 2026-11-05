"""作業系統、CPU 與記憶體偵測（只用標準庫與 ctypes，不依賴 psutil）。

位於 hardware 模組的偵測層：`detect()` 呼叫這裡的函式。每個平台的讀取方式不同
（Windows 用 kernel32 與登錄檔、Linux 讀 /proc 與 /sys、macOS 用 sysctl 與 vm_stat），
解析部分寫成純函式，讓任一平台都能用固定文字測試其他平台的行為。
"""

import ctypes
import logging
import os
import platform
import re
import struct
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

from kikitori.hardware.types import CpuInfo, MemoryInfo, OsName

logger = logging.getLogger(__name__)

SUBPROCESS_TIMEOUT_SECS = 3.0
MAX_CPU_NAME_CHARS = 200
_MAX_PROC_FILE_BYTES = 4 * 1024 * 1024

# GetLogicalProcessorInformationEx 的 RelationProcessorCore
_RELATION_PROCESSOR_CORE = 0
_ERROR_INSUFFICIENT_BUFFER = 122


def os_name(sys_platform: str = sys.platform) -> OsName:
    """把 `sys.platform` 轉成 "windows"、"macos"、"linux" 或 "other"。"""
    if sys_platform == "win32":
        return "windows"
    if sys_platform == "darwin":
        return "macos"
    if sys_platform.startswith("linux"):
        return "linux"
    return "other"


def os_version(name: OsName) -> str:
    """給人看的作業系統版本字串。"""
    if name == "windows":
        return f"Windows {platform.release()} ({platform.version()})"
    if name == "macos":
        return f"macOS {platform.mac_ver()[0] or platform.release()}"
    if name == "linux":
        pretty = "Linux"
        try:
            pretty = platform.freedesktop_os_release().get("PRETTY_NAME", pretty)
        except OSError as exc:
            logger.info("cannot read os-release: %s", exc)
        return f"{pretty} (kernel {platform.release()})"
    return f"{platform.system()} {platform.release()}"


def _clean_cpu_name(name: object) -> str | None:
    if not isinstance(name, str):
        return None
    cleaned = " ".join(name.split())
    if not cleaned or not cleaned.isprintable():
        return None
    return cleaned[:MAX_CPU_NAME_CHARS]


def _positive_int(value: object) -> int | None:
    return value if isinstance(value, int) and value > 0 else None


# --- Linux ------------------------------------------------------------------


def parse_meminfo(text: str) -> MemoryInfo:
    """解析 /proc/meminfo（單位 kB）；MemAvailable 不存在（核心 < 3.14）時為 None。"""
    values: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r"^(\w+):\s+(\d+)\s*kB\s*$", line)
        if match:
            values[match.group(1)] = int(match.group(2)) * 1024
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    return MemoryInfo(
        total_bytes=total if total else None,
        available_bytes=available if available is not None else None,
    )


def parse_cpuinfo(text: str) -> tuple[str | None, int | None]:
    """解析 /proc/cpuinfo，回傳（型號, 實體核心數）。

    實體核心數以不重複的 (physical id, core id) 計；ARM 沒有這兩個欄位時回傳 None，
    由呼叫端改用 sysfs。
    """
    model: str | None = None
    cores: set[tuple[str, str]] = set()
    physical_id = ""
    for block in re.split(r"\n\s*\n", text):
        fields: dict[str, str] = {}
        for line in block.splitlines():
            key, sep, value = line.partition(":")
            if sep:
                fields[key.strip()] = value.strip()
        if model is None:
            # x86 為 "model name"；部分 ARM 核心提供 "Model" 或 "Hardware"
            for key in ("model name", "Model", "Hardware"):
                if key in fields:
                    model = _clean_cpu_name(fields[key])
                    break
        physical_id = fields.get("physical id", physical_id)
        if "core id" in fields:
            cores.add((physical_id, fields["core id"]))
    return model, (len(cores) or None)


def count_sysfs_cores(cpu_root: Path) -> int | None:
    """由 /sys/devices/system/cpu/cpu*/topology 計算實體核心數。"""
    pairs: set[tuple[str, str]] = set()
    try:
        entries = list(cpu_root.glob("cpu[0-9]*/topology"))
    except OSError as exc:
        logger.info("cannot list %s: %s", cpu_root, exc)
        return None
    for topology in entries:
        try:
            package = (topology / "physical_package_id").read_text().strip()
            core = (topology / "core_id").read_text().strip()
        except OSError:
            # 離線的 CPU 沒有 topology 檔，跳過即可
            continue
        pairs.add((package, core))
    return len(pairs) or None


def _read_small_text(path: Path) -> str:
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        return fh.read(_MAX_PROC_FILE_BYTES)


def _linux_cpu() -> tuple[str | None, int | None]:
    model, physical = parse_cpuinfo(_read_small_text(Path("/proc/cpuinfo")))
    if physical is None:
        physical = count_sysfs_cores(Path("/sys/devices/system/cpu"))
    return model, physical


def _linux_memory() -> MemoryInfo:
    return parse_meminfo(_read_small_text(Path("/proc/meminfo")))


# --- Windows ----------------------------------------------------------------


def count_core_records(buffer: bytes) -> int:
    """數 `GetLogicalProcessorInformationEx` 緩衝區中 RelationProcessorCore 的筆數。

    每筆開頭是 `DWORD Relationship; DWORD Size`，以 Size 前進。

    Raises:
        ValueError: 緩衝區格式不對（Size 為 0 或超出範圍）。
    """
    offset = 0
    cores = 0
    while offset < len(buffer):
        if offset + 8 > len(buffer):
            raise ValueError("truncated processor information record")
        relationship, size = struct.unpack_from("<II", buffer, offset)
        if size < 8 or offset + size > len(buffer):
            raise ValueError(f"invalid processor information record size {size}")
        if relationship == _RELATION_PROCESSOR_CORE:
            cores += 1
        offset += size
    return cores


def _windows_physical_cores() -> int | None:
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    func = kernel32.GetLogicalProcessorInformationEx
    func.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
    func.restype = ctypes.c_int
    length = ctypes.c_ulong(0)
    if func(_RELATION_PROCESSOR_CORE, None, ctypes.byref(length)):
        return None
    error = ctypes.get_last_error()  # type: ignore[attr-defined]
    if error != _ERROR_INSUFFICIENT_BUFFER or not 0 < length.value <= 1 << 20:
        raise OSError(error, "GetLogicalProcessorInformationEx size query failed")
    buffer = ctypes.create_string_buffer(length.value)
    if not func(_RELATION_PROCESSOR_CORE, buffer, ctypes.byref(length)):
        raise OSError(
            ctypes.get_last_error(),  # type: ignore[attr-defined]
            "GetLogicalProcessorInformationEx failed",
        )
    return count_core_records(buffer.raw[: length.value]) or None


def _windows_cpu_name() -> str | None:
    import winreg  # type: ignore[import-not-found,unused-ignore]

    key_path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
    with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:  # type: ignore[attr-defined,unused-ignore]
        value, _ = winreg.QueryValueEx(key, "ProcessorNameString")  # type: ignore[attr-defined,unused-ignore]
    return _clean_cpu_name(value)


def _windows_memory() -> MemoryInfo:
    class _MemoryStatusEx(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_ulong),
            ("dwMemoryLoad", ctypes.c_ulong),
            ("ullTotalPhys", ctypes.c_ulonglong),
            ("ullAvailPhys", ctypes.c_ulonglong),
            ("ullTotalPageFile", ctypes.c_ulonglong),
            ("ullAvailPageFile", ctypes.c_ulonglong),
            ("ullTotalVirtual", ctypes.c_ulonglong),
            ("ullAvailVirtual", ctypes.c_ulonglong),
            ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
        ]

    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
    if not kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        raise OSError(ctypes.get_last_error(), "GlobalMemoryStatusEx failed")  # type: ignore[attr-defined]
    return MemoryInfo(
        total_bytes=int(status.ullTotalPhys) or None,
        available_bytes=int(status.ullAvailPhys),
    )


# --- macOS ------------------------------------------------------------------

SysctlReader = Callable[[str], bytes | None]
"""以名稱讀取 sysctl 原始值；不存在時回傳 None。"""


def _libc_sysctl(name: str) -> bytes | None:
    libc = ctypes.CDLL(None, use_errno=True)
    func = libc.sysctlbyname
    func.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    func.restype = ctypes.c_int
    size = ctypes.c_size_t(0)
    if func(name.encode(), None, ctypes.byref(size), None, 0) != 0:
        return None
    if not 0 < size.value <= 4096:
        return None
    buffer = ctypes.create_string_buffer(size.value)
    if func(name.encode(), buffer, ctypes.byref(size), None, 0) != 0:
        return None
    return buffer.raw[: size.value]


def sysctl_int(read: SysctlReader, name: str) -> int | None:
    """sysctl 整數值（4 或 8 位元組、little-endian）。"""
    raw = read(name)
    if raw is None or len(raw) not in (4, 8):
        return None
    return int.from_bytes(raw, "little", signed=False)


def sysctl_str(read: SysctlReader, name: str) -> str | None:
    """sysctl 字串值（去掉結尾 NUL）。"""
    raw = read(name)
    if raw is None:
        return None
    return _clean_cpu_name(raw.split(b"\0", 1)[0].decode("utf-8", errors="replace"))


def parse_vm_stat(text: str) -> int | None:
    """由 `vm_stat` 輸出估計可用記憶體：(free + inactive + speculative) × page size。

    與「活動監視器」的「可用」不完全相同（不含可清除的壓縮記憶體），但足以判斷等級。
    """
    header = re.search(r"page size of (\d+) bytes", text)
    if header is None:
        return None
    page_size = int(header.group(1))
    pages: dict[str, int] = {}
    for line in text.splitlines():
        match = re.match(r'^"?([A-Za-z ]+?)"?:\s+(\d+)\.?\s*$', line)
        if match:
            pages[match.group(1).strip()] = int(match.group(2))
    wanted = ("Pages free", "Pages inactive", "Pages speculative")
    if "Pages free" not in pages:
        return None
    return sum(pages.get(k, 0) for k in wanted) * page_size


def _macos_available_memory(timeout: float) -> int | None:
    result = subprocess.run(
        ["/usr/bin/vm_stat"],
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
    )
    if result.returncode != 0:
        raise OSError(f"vm_stat exited with {result.returncode}")
    return parse_vm_stat(result.stdout[:65536])


def macos_apple_silicon(machine: str, read: SysctlReader) -> bool:
    """Apple Silicon 判斷：machine 為 arm64，或在 Rosetta 下（hw.optional.arm64 = 1）。"""
    if machine.lower() in ("arm64", "aarch64"):
        return True
    return sysctl_int(read, "hw.optional.arm64") == 1


# --- 對外函式 ----------------------------------------------------------------


def detect_cpu(
    name: OsName, machine: str, sysctl: SysctlReader | None = None
) -> tuple[CpuInfo, list[str]]:
    """偵測 CPU；回傳（結果, 錯誤訊息清單）。錯誤已記 log，不丟例外。"""
    errors: list[str] = []
    model: str | None = None
    physical: int | None = None
    logical = _positive_int(os.cpu_count())
    try:
        if name == "windows":
            model = _windows_cpu_name()
            physical = _windows_physical_cores()
        elif name == "linux":
            model, physical = _linux_cpu()
        elif name == "macos":
            read = sysctl or _libc_sysctl
            model = sysctl_str(read, "machdep.cpu.brand_string")
            physical = sysctl_int(read, "hw.physicalcpu")
            logical = sysctl_int(read, "hw.logicalcpu") or logical
    except (OSError, ValueError, AttributeError, ImportError) as exc:
        logger.warning("CPU detection failed: %s", exc)
        errors.append(f"cpu: {exc}")
    if physical is not None and logical is not None and physical > logical:
        physical = logical
    return (
        CpuInfo(
            model=model or _clean_cpu_name(platform.processor()),
            physical_cores=_positive_int(physical),
            logical_cores=logical,
            architecture=machine,
        ),
        errors,
    )


def detect_memory(
    name: OsName,
    sysctl: SysctlReader | None = None,
    timeout: float = SUBPROCESS_TIMEOUT_SECS,
) -> tuple[MemoryInfo, list[str]]:
    """偵測記憶體；回傳（結果, 錯誤訊息清單）。錯誤已記 log，不丟例外。"""
    try:
        if name == "windows":
            return _windows_memory(), []
        if name == "linux":
            return _linux_memory(), []
        if name == "macos":
            total = sysctl_int(sysctl or _libc_sysctl, "hw.memsize")
            available = _macos_available_memory(timeout)
            return MemoryInfo(total_bytes=total or None, available_bytes=available), []
        pages = os.sysconf("SC_PHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        if pages <= 0 or page_size <= 0:
            raise ValueError(f"sysconf returned {pages} pages of {page_size} bytes")
        return MemoryInfo(total_bytes=pages * page_size, available_bytes=None), []
    except (
        OSError,
        ValueError,
        AttributeError,
        subprocess.SubprocessError,
    ) as exc:
        logger.warning("memory detection failed: %s", exc)
        return MemoryInfo(None, None), [f"memory: {exc}"]


def detect_apple_silicon(
    name: OsName, machine: str, sysctl: SysctlReader | None = None
) -> bool:
    """macOS 上是否為 Apple Silicon；其他平台一律 False。"""
    if name != "macos":
        return False
    try:
        return macos_apple_silicon(machine, sysctl or _libc_sysctl)
    except (OSError, AttributeError) as exc:
        logger.warning("Apple Silicon detection failed: %s", exc)
        return machine.lower() in ("arm64", "aarch64")
