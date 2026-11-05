"""NVIDIA GPU 偵測（`nvidia-smi`）與 cuBLAS 12 搜尋。

位於 hardware 模組的偵測層。`nvidia-smi` 隨 NVIDIA 驅動安裝，是不需要 CUDA Toolkit
就能查 VRAM 與 compute capability 的方式；它是外部程式，所以有 timeout、輸出逐欄
驗證。cuBLAS 只檢查檔案存在（不載入），因為 CTranslate2 只在建立 CUDA 模型時才以
檔名載入它，而 `asr.load_transcriber(cuda_library_dirs=...)` 會先用完整路徑預載。
"""

import glob
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

from kikitori.hardware.types import CublasInfo, CublasSource, GpuInfo, OsName

logger = logging.getLogger(__name__)

NVIDIA_SMI_TIMEOUT_SECS = 5.0
GPU_QUERY_FIELDS = (
    "name",
    "memory.total",
    "memory.used",
    "driver_version",
    "compute_cap",
)
MAX_GPUS = 64
MAX_GPU_NAME_CHARS = 200
MAX_VRAM_MB = 10_000_000
_NA_VALUES = frozenset(
    {"[N/A]", "N/A", "[Not Supported]", "[Insufficient Permissions]"}
)

WINDOWS_CUBLAS_FILES = ("cublas64_12.dll", "cublasLt64_12.dll")
LINUX_CUBLAS_FILES = ("libcublas.so.12", "libcublasLt.so.12")

Runner = Callable[..., "subprocess.CompletedProcess[str]"]


class NvidiaSmiError(RuntimeError):
    """`nvidia-smi` 執行失敗、逾時或輸出不合法。"""


def cublas_platform_supported(name: OsName, machine: str) -> bool:
    """CTranslate2 4.8.5 的 PyPI wheel 只有 Windows／Linux x86-64 支援 CUDA。

    Linux aarch64 的 wheel 只有 CPU（官方安裝文件：Jetson 需自行編譯），macOS 沒有 CUDA。
    """
    arch = machine.lower()
    return name in ("windows", "linux") and arch in ("amd64", "x86_64")


def cublas_file_names(name: OsName) -> tuple[str, ...]:
    """CTranslate2 需要的 cuBLAS 12 檔名；不支援的平台回傳空 tuple。"""
    if name == "windows":
        return WINDOWS_CUBLAS_FILES
    if name == "linux":
        return LINUX_CUBLAS_FILES
    return ()


# --- nvidia-smi --------------------------------------------------------------


def find_nvidia_smi(
    name: OsName,
    env: Mapping[str, str] | None = None,
    which: Callable[[str], str | None] = shutil.which,
    is_file: Callable[[Path], bool] = Path.is_file,
) -> Path | None:
    """找 `nvidia-smi`：先找 PATH，再找驅動的預設安裝位置。"""
    env = os.environ if env is None else env
    found = which("nvidia-smi")
    if found:
        return Path(found)
    candidates: list[Path] = []
    if name == "windows":
        for var, rel in (
            ("SystemRoot", ("System32", "nvidia-smi.exe")),
            ("ProgramFiles", ("NVIDIA Corporation", "NVSMI", "nvidia-smi.exe")),
        ):
            base = env.get(var, "").strip()
            if base and PureWindowsPath(base).is_absolute():
                candidates.append(Path(base, *rel))
    elif name == "linux":
        candidates.append(Path("/usr/bin/nvidia-smi"))
    return next((p for p in candidates if is_file(p)), None)


def _optional_int(value: str, field: str) -> int | None:
    if value in _NA_VALUES:
        return None
    if not value.isdigit():
        raise ValueError(f"{field} is not an integer: {value!r}")
    number = int(value)
    if number > MAX_VRAM_MB:
        raise ValueError(f"{field} out of range: {number}")
    return number


def _optional_text(value: str, pattern_ok: Callable[[str], bool]) -> str | None:
    if value in _NA_VALUES or not value:
        return None
    return value if pattern_ok(value) else None


def _is_version(value: str) -> bool:
    parts = value.split(".")
    return 1 <= len(parts) <= 4 and all(p.isdigit() and len(p) <= 6 for p in parts)


def _is_compute_cap(value: str) -> bool:
    major, sep, minor = value.partition(".")
    return bool(sep) and major.isdigit() and minor.isdigit() and len(value) <= 6


def parse_nvidia_smi_csv(
    text: str, with_compute_cap: bool = True
) -> tuple[list[GpuInfo], list[str]]:
    """解析 `--format=csv,noheader,nounits` 的輸出。

    每行一張 GPU；格式不對的行跳過並回報原因。名稱放在第一欄，用 `rsplit` 取後面的
    固定欄位，名稱中萬一有逗號也不會錯位。

    Returns:
        （GPU 清單, 被跳過的行的錯誤訊息）。
    """
    expected = len(GPU_QUERY_FIELDS) if with_compute_cap else len(GPU_QUERY_FIELDS) - 1
    gpus: list[GpuInfo] = []
    errors: list[str] = []
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) > MAX_GPUS:
        errors.append(f"nvidia-smi listed {len(lines)} GPUs; only {MAX_GPUS} kept")
        lines = lines[:MAX_GPUS]
    for line in lines:
        parts = [p.strip() for p in line.rsplit(",", expected - 1)]
        try:
            if len(parts) != expected:
                raise ValueError(f"expected {expected} fields, got {len(parts)}")
            name = " ".join(parts[0].split())
            if not name or len(name) > MAX_GPU_NAME_CHARS or not name.isprintable():
                raise ValueError("invalid GPU name")
            total = _optional_int(parts[1], "memory.total")
            if total is None or total <= 0:
                raise ValueError("memory.total is missing")
            used = _optional_int(parts[2], "memory.used")
            gpus.append(
                GpuInfo(
                    index=len(gpus),
                    name=name,
                    memory_total_mb=total,
                    memory_used_mb=used,
                    driver_version=_optional_text(parts[3], _is_version),
                    compute_capability=_optional_text(parts[4], _is_compute_cap)
                    if with_compute_cap
                    else None,
                )
            )
        except ValueError as exc:
            errors.append(f"ignored nvidia-smi line {line[:120]!r}: {exc}")
    return gpus, errors


def _run_query(
    executable: Path, fields: Sequence[str], run: Runner, timeout: float
) -> "subprocess.CompletedProcess[str]":
    # 引擎是無主控台的 sidecar：不加 CREATE_NO_WINDOW 時 Windows 會為子行程彈出主控台視窗
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) if sys.platform == "win32" else 0
    return run(
        [
            os.fspath(executable),
            f"--query-gpu={','.join(fields)}",
            "--format=csv,noheader,nounits",
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
        stdin=subprocess.DEVNULL,
        creationflags=flags,
    )


def query_nvidia_gpus(
    executable: Path,
    run: Runner = subprocess.run,
    timeout: float = NVIDIA_SMI_TIMEOUT_SECS,
) -> tuple[list[GpuInfo], list[str]]:
    """執行 `nvidia-smi` 查詢 GPU。

    舊驅動（< 510）不認得 `compute_cap` 欄位，此時改用不含它的欄位重查。

    Returns:
        （GPU 清單, 被跳過的行的錯誤訊息）。

    Raises:
        NvidiaSmiError: 無法執行、逾時、結束碼非 0，或沒有任何一行可解析。
    """
    try:
        result = _run_query(executable, GPU_QUERY_FIELDS, run, timeout)
        with_cap = True
        output = (result.stdout or "") + (result.stderr or "")
        if result.returncode != 0 and "compute_cap" in output:
            result = _run_query(executable, GPU_QUERY_FIELDS[:-1], run, timeout)
            with_cap = False
    except subprocess.TimeoutExpired as exc:
        raise NvidiaSmiError(f"nvidia-smi timed out after {timeout:g} s") from exc
    except OSError as exc:
        raise NvidiaSmiError(f"cannot run nvidia-smi: {exc}") from exc
    stdout = (result.stdout or "")[:65536]
    if result.returncode != 0:
        detail = " ".join(((result.stderr or "") + " " + stdout).split())[:300]
        raise NvidiaSmiError(
            f"nvidia-smi exited with {result.returncode}: {detail or '(no output)'}"
        )
    gpus, errors = parse_nvidia_smi_csv(stdout, with_compute_cap=with_cap)
    if not gpus and stdout.strip():
        raise NvidiaSmiError("; ".join(errors) or "unrecognised nvidia-smi output")
    return gpus, errors


# --- cuBLAS ------------------------------------------------------------------


def _absolute(value: str, windows: bool) -> bool:
    path: PurePath = PureWindowsPath(value) if windows else PurePosixPath(value)
    return path.is_absolute()


def system_library_dirs(
    name: OsName,
    env: Mapping[str, str],
    glob_dirs: Callable[[str], list[str]] = glob.glob,
) -> list[Path]:
    """系統上可能放 cuBLAS 12 的目錄（依優先順序，未檢查是否存在）。

    - Windows：`CUDA_PATH_V12_*`、`CUDA_PATH` 的 `bin`、Program Files 下的
      CUDA v12.x，最後是 `PATH`（CTranslate2 以檔名載入時 Windows 也會搜尋 PATH）。
    - Linux：`LD_LIBRARY_PATH`、`/usr/local/cuda*/lib64`、發行版的函式庫目錄。
      ldconfig 快取中的其他目錄不列（需要執行外部程式）。
    """
    dirs: list[Path] = []
    windows = name == "windows"
    if windows:
        versioned = sorted(
            (k for k in env if k.upper().startswith("CUDA_PATH_V12")), reverse=True
        )
        for key in [*versioned, "CUDA_PATH"]:
            value = env.get(key, "").strip()
            if value and _absolute(value, windows=True):
                dirs.append(Path(value) / "bin")
        program_files = env.get("ProgramFiles", "").strip()
        if program_files and _absolute(program_files, windows=True):
            pattern = os.path.join(
                program_files, "NVIDIA GPU Computing Toolkit", "CUDA", "v12.*", "bin"
            )
            dirs.extend(Path(p) for p in sorted(glob_dirs(pattern), reverse=True))
        path_var = env.get("PATH", "")
    elif name == "linux":
        path_var = env.get("LD_LIBRARY_PATH", "")
    else:
        return []
    for entry in path_var.split(os.pathsep if windows else ":"):
        entry = entry.strip().strip('"')
        if entry and _absolute(entry, windows=windows):
            dirs.append(Path(entry))
    if name == "linux":
        dirs.append(Path("/usr/local/cuda/lib64"))
        dirs.extend(
            Path(p)
            for p in sorted(glob_dirs("/usr/local/cuda-12*/lib64"), reverse=True)
        )
        dirs.extend(
            Path(p)
            for p in (
                "/usr/lib/x86_64-linux-gnu",
                "/usr/lib64",
                "/usr/lib",
                "/usr/local/lib",
            )
        )
    seen: set[str] = set()
    unique: list[Path] = []
    for d in dirs:
        key = os.path.normcase(os.fspath(d))
        if key not in seen:
            seen.add(key)
            unique.append(d)
    return unique


def find_cublas(
    name: OsName,
    machine: str,
    *,
    configured_dirs: Sequence[Path] = (),
    runtime_dir: Path | None = None,
    env: Mapping[str, str] | None = None,
    is_file: Callable[[Path], bool] = Path.is_file,
    glob_dirs: Callable[[str], list[str]] = glob.glob,
) -> CublasInfo:
    """依序在設定目錄、kikitori GPU 執行環境、系統目錄中找 cuBLAS 12。

    每個必要檔案取第一個找到的位置（與 `asr.preload_cuda_libraries()` 相同的規則）。
    """
    supported = cublas_platform_supported(name, machine)
    names = cublas_file_names(name)
    if not supported or not names:
        return CublasInfo(supported_platform=False, available=False)
    env = os.environ if env is None else env
    ordered: list[tuple[Path, CublasSource]] = [
        (Path(d), "configured") for d in configured_dirs
    ]
    if runtime_dir is not None:
        ordered.append((runtime_dir, "runtime"))
    ordered.extend((d, "system") for d in system_library_dirs(name, env, glob_dirs))

    found_dirs: list[str] = []
    first_source: CublasSource | None = None
    for filename in names:
        hit = next(
            ((d, src) for d, src in ordered if _safe_is_file(is_file, d / filename)),
            None,
        )
        if hit is None:
            return CublasInfo(supported_platform=True, available=False)
        directory, source = hit
        if first_source is None:
            first_source = source
        if os.fspath(directory) not in found_dirs:
            found_dirs.append(os.fspath(directory))
    return CublasInfo(
        supported_platform=True,
        available=True,
        library_dirs=tuple(found_dirs),
        source=first_source,
    )


def _safe_is_file(is_file: Callable[[Path], bool], path: Path) -> bool:
    try:
        return is_file(path)
    except OSError as exc:
        # 權限不足或網路磁碟離線：當作不存在，但留下紀錄
        logger.info("cannot check %s: %s", path, exc)
        return False
