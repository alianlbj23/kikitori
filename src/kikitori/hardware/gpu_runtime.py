"""GPU 執行環境（cuBLAS 12）的使用者觸發下載與安裝。

位於 hardware 模組的安裝層。CTranslate2 的 CUDA 推論需要 cuBLAS 12，但我們不把 NVIDIA
的檔案放進安裝檔（見 `docs/specs/hardware.md` 的授權說明）：使用者在 UI 按下按鈕後，
才從 PyPI 下載 NVIDIA 官方的 `nvidia-cublas-cu12` wheel（固定版本、網址與 SHA-256），
驗證後只解出需要的函式庫與授權檔到使用者快取目錄。下載本身沿用 asr 模組的續傳、
逾時、雜湊驗證與取消邏輯。
"""

import hashlib
import json
import logging
import os
import platform as _platform
import shutil
import sys
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kikitori.asr.cache import default_cache_dir

# PyPI 的網址格式不同於 Hugging Face，無法透過 download_model() 下載；download_file()
# 提供完全相同的續傳、Content-Range 檢查、雜湊驗證與取消邏輯，重寫一份只會讓兩邊分歧。
from kikitori.asr.download import (
    DownloadCancelledError,
    Fetcher,
    ModelDownloadError,
    ProgressCallback,
    download_file,
    validate_url,
)
from kikitori.asr.registry import ModelFile
from kikitori.hardware.types import OsName

logger = logging.getLogger(__name__)

GPU_RUNTIME_ID = "gpu-runtime"
"""`models.download` 用來下載 GPU 執行環境的 id。"""

CUBLAS_PACKAGE = "nvidia-cublas-cu12"
CUBLAS_VERSION = "12.8.4.1"
"""與 CTranslate2 4.8.5 的建置版本（CUDA 12.8）相同；本機 RTX 5070 實測可用。"""

LICENSE_NAME = "NVIDIA CUDA Toolkit End User License Agreement"
LICENSE_URL = "https://docs.nvidia.com/cuda/eula/index.html"
LICENSE_FILENAME = "License.txt"
MARKER_NAME = "kikitori-gpu-runtime.json"
RUNTIME_SUBDIR = "gpu-runtime"
DEFAULT_TIMEOUT_SECS = 30.0
_COPY_CHUNK = 1 << 20


class GpuRuntimeError(RuntimeError):
    """GPU 執行環境安裝失敗：平台不支援、wheel 內容不符或解壓失敗。"""


@dataclass(frozen=True)
class WheelMember:
    """要從 wheel 解出的一個檔案。

    Attributes:
        archive_path: wheel（zip）內的路徑。
        filename: 解出後的檔名（直接放在執行環境目錄下，不保留 wheel 內的目錄）。
        size: 解壓後的大小；與 zip 目錄不符就拒絕（防止被換成其他內容或壓縮炸彈）。
    """

    archive_path: str
    filename: str
    size: int


@dataclass(frozen=True)
class RuntimeWheel:
    """一個平台的 cuBLAS wheel（固定網址、大小與 SHA-256）。"""

    platform_tag: str
    url: str
    size: int
    sha256: str
    members: tuple[WheelMember, ...]

    @property
    def filename(self) -> str:
        """wheel 檔名（網址最後一段）。"""
        return self.url.rsplit("/", 1)[-1]

    @property
    def extracted_bytes(self) -> int:
        """解出的檔案大小總和。"""
        return sum(m.size for m in self.members)


_DIST_INFO = f"nvidia_cublas_cu12-{CUBLAS_VERSION}.dist-info"
_LICENSE_MEMBER = WheelMember(f"{_DIST_INFO}/License.txt", LICENSE_FILENAME, 59262)

# 來源：https://pypi.org/pypi/nvidia-cublas-cu12/12.8.4.1/json（2026-11-05 取得 url、size、
# digests.sha256），檔案清單與大小來自實際的 zip 中央目錄。只解出 CTranslate2 需要的
# cuBLAS／cuBLASLt 與授權全文（nvblas 與標頭檔用不到）。
# Linux aarch64 wheel 存在，但 CTranslate2 的 aarch64 wheel 只有 CPU，所以不列入。
CUBLAS_WHEELS: dict[str, RuntimeWheel] = {
    w.platform_tag: w
    for w in (
        RuntimeWheel(
            platform_tag="win_amd64",
            url=(
                "https://files.pythonhosted.org/packages/70/61/"
                "7d7b3c70186fb651d0fbd35b01dbfc8e755f69fd58f817f3d0f642df20c3/"
                "nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl"
            ),
            size=567544208,
            sha256="47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af",
            members=(
                WheelMember(
                    "nvidia/cublas/bin/cublas64_12.dll", "cublas64_12.dll", 113716224
                ),
                WheelMember(
                    "nvidia/cublas/bin/cublasLt64_12.dll",
                    "cublasLt64_12.dll",
                    674667520,
                ),
                _LICENSE_MEMBER,
            ),
        ),
        RuntimeWheel(
            platform_tag="manylinux_2_27_x86_64",
            url=(
                "https://files.pythonhosted.org/packages/dc/61/"
                "e24b560ab2e2eaeb3c839129175fb330dfcfc29e5203196e5541a4c44682/"
                "nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl"
            ),
            size=594346921,
            sha256="8ac4e771d5a348c551b2a426eda6193c19aa630236b418086020df5ba9667142",
            members=(
                WheelMember(
                    "nvidia/cublas/lib/libcublas.so.12", "libcublas.so.12", 116388640
                ),
                WheelMember(
                    "nvidia/cublas/lib/libcublasLt.so.12",
                    "libcublasLt.so.12",
                    751771728,
                ),
                _LICENSE_MEMBER,
            ),
        ),
    )
}


@dataclass(frozen=True)
class GpuRuntimeStatus:
    """GPU 執行環境的狀態（給 `models.list` 與設定頁）。"""

    supported: bool
    installed: bool
    package: str
    version: str
    path: str | None
    download_bytes: int | None
    installed_bytes: int | None
    license: str
    license_url: str

    def to_dict(self) -> dict[str, Any]:
        """JSON 化。"""
        return {
            "id": GPU_RUNTIME_ID,
            "supported": self.supported,
            "installed": self.installed,
            "package": self.package,
            "version": self.version,
            "path": self.path,
            "download_bytes": self.download_bytes,
            "installed_bytes": self.installed_bytes,
            "license": self.license,
            "license_url": self.license_url,
        }


def platform_wheel(
    os_name: OsName | None = None, machine: str | None = None
) -> RuntimeWheel | None:
    """目前（或指定）平台的 wheel；不支援時回傳 None。"""
    if os_name is None:
        from kikitori.hardware.system import os_name as _detect_os

        os_name = _detect_os(sys.platform)
    arch = (machine if machine is not None else _platform.machine()).lower()
    if arch not in ("amd64", "x86_64"):
        return None
    if os_name == "windows":
        return CUBLAS_WHEELS["win_amd64"]
    if os_name == "linux":
        return CUBLAS_WHEELS["manylinux_2_27_x86_64"]
    return None


def default_runtime_root() -> Path:
    """GPU 執行環境的根目錄：模型快取的同層 `<cache>/kikitori/gpu-runtime`。"""
    return default_cache_dir().parent / RUNTIME_SUBDIR


def runtime_dir(root: Path, version: str = CUBLAS_VERSION) -> Path:
    """某版本的安裝目錄：`<root>/cublas-<version>`。"""
    return root / f"cublas-{version}"


def _expected_marker(wheel: RuntimeWheel) -> dict[str, Any]:
    return {
        "package": CUBLAS_PACKAGE,
        "version": CUBLAS_VERSION,
        "platform_tag": wheel.platform_tag,
        "wheel_sha256": wheel.sha256,
        "files": {m.filename: m.size for m in wheel.members},
    }


def is_installed(root: Path, wheel: RuntimeWheel) -> bool:
    """完成標記相符且每個檔案大小正確（不重算雜湊，啟動時要快）。"""
    directory = runtime_dir(root)
    marker = directory / MARKER_NAME
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        logger.warning("ignoring unreadable GPU runtime marker %s: %s", marker, exc)
        return False
    if recorded != _expected_marker(wheel):
        return False
    for member in wheel.members:
        path = directory / member.filename
        try:
            if not path.is_file() or path.stat().st_size != member.size:
                return False
        except OSError as exc:
            logger.warning("cannot check %s: %s", path, exc)
            return False
    return True


def installed_runtime_dir(
    root: Path | None = None, wheel: RuntimeWheel | None = None
) -> Path | None:
    """已安裝的執行環境目錄；未安裝或平台不支援時回傳 None。"""
    wheel = wheel or platform_wheel()
    if wheel is None:
        return None
    root = default_runtime_root() if root is None else root
    return runtime_dir(root) if is_installed(root, wheel) else None


def gpu_runtime_status(
    root: Path | None = None, wheel: RuntimeWheel | None = None
) -> GpuRuntimeStatus:
    """GPU 執行環境是否支援、是否已安裝。"""
    wheel = wheel or platform_wheel()
    root = default_runtime_root() if root is None else root
    installed = wheel is not None and is_installed(root, wheel)
    return GpuRuntimeStatus(
        supported=wheel is not None,
        installed=installed,
        package=CUBLAS_PACKAGE,
        version=CUBLAS_VERSION,
        path=os.fspath(runtime_dir(root)) if installed else None,
        download_bytes=None if wheel is None else wheel.size,
        installed_bytes=None if wheel is None else wheel.extracted_bytes,
        license=LICENSE_NAME,
        license_url=LICENSE_URL,
    )


def _sha256_file(path: Path, cancel: threading.Event | None) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_COPY_CHUNK), b""):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelledError("GPU runtime verification cancelled")
            hasher.update(block)
    return hasher.hexdigest()


def _extract(
    wheel_path: Path, wheel: RuntimeWheel, dest: Path, cancel: threading.Event | None
) -> None:
    try:
        archive = zipfile.ZipFile(wheel_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise GpuRuntimeError(f"{wheel_path.name} is not a valid wheel: {exc}") from exc
    with archive:
        for member in wheel.members:
            try:
                info = archive.getinfo(member.archive_path)
            except KeyError:
                raise GpuRuntimeError(
                    f"{member.archive_path} is missing from {wheel.filename}"
                ) from None
            if info.file_size != member.size:
                raise GpuRuntimeError(
                    f"{member.archive_path} is {info.file_size} bytes, expected {member.size}"
                )
            target = dest / member.filename
            written = 0
            try:
                # zipfile 讀到結尾時會檢查 CRC-32，內容損壞會丟 BadZipFile
                with archive.open(info) as src, target.open("wb") as out:
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelledError(
                                "GPU runtime installation cancelled"
                            )
                        chunk = src.read(_COPY_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member.size:
                            raise GpuRuntimeError(
                                f"{member.archive_path} is larger than {member.size}"
                            )
                        out.write(chunk)
            except zipfile.BadZipFile as exc:
                raise GpuRuntimeError(
                    f"{member.archive_path} is corrupted: {exc}"
                ) from exc
            if written != member.size:
                raise GpuRuntimeError(
                    f"{member.archive_path} extracted {written} of {member.size} bytes"
                )


def install_gpu_runtime(
    root: Path | None = None,
    *,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    fetcher: Fetcher | None = None,
    wheel: RuntimeWheel | None = None,
) -> Path:
    """下載 cuBLAS wheel、驗證 SHA-256、解出需要的檔案，回傳安裝目錄。

    只能由使用者明確觸發（UI 按鈕，並先顯示 NVIDIA 授權）。已安裝時直接回傳。
    中斷的下載保留 `.part` 供下次續傳；雜湊不符時刪除重下。解壓先寫到暫存目錄，
    全部成功才改名成正式目錄，失敗時刪除暫存目錄，下次重新解壓（已驗證的 wheel 保留）。

    Args:
        root: 執行環境根目錄；省略時用 `default_runtime_root()`。
        progress: 下載進度 `(downloaded_bytes, total_bytes)`，以 wheel 大小計。
        cancel: 被設定時在下一個區塊前停止，丟 `DownloadCancelledError`。
        timeout: 連線與每次讀取的逾時秒數。
        fetcher: HTTP 實作；省略時用 `UrllibFetcher`（只允許 https）。
        wheel: 要安裝的 wheel；省略時依目前平台選（測試可指定本機伺服器的假 wheel）。

    Raises:
        GpuRuntimeError: 平台不支援、wheel 內容不符、解壓失敗。
        ModelDownloadError: 網路、HTTP、大小不符、磁碟空間不足。
        ModelIntegrityError: wheel 的 SHA-256 不符（`.part` 已刪除）。
        DownloadCancelledError: 被取消。
        ValueError: 參數不合法。
    """
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    wheel = wheel or platform_wheel()
    if wheel is None:
        raise GpuRuntimeError(
            "the GPU runtime is only available on Windows and Linux x86-64"
        )
    validate_url(wheel.url)
    root = default_runtime_root() if root is None else root
    final_dir = runtime_dir(root)
    if is_installed(root, wheel):
        if progress is not None:
            progress(wheel.size, wheel.size)
        return final_dir

    downloads = root / "downloads"
    downloads.mkdir(parents=True, exist_ok=True)
    wheel_path = downloads / wheel.filename
    # 上次已下載完成但解壓失敗時 wheel 還在：重算雜湊確認後直接解壓
    if wheel_path.exists() and (
        wheel_path.stat().st_size != wheel.size
        or _sha256_file(wheel_path, cancel) != wheel.sha256
    ):
        logger.warning("discarding unverified GPU runtime wheel %s", wheel_path)
        wheel_path.unlink()

    need = wheel.extracted_bytes
    if not wheel_path.exists():
        part = wheel_path.with_name(wheel_path.name + ".part")
        need += wheel.size - (part.stat().st_size if part.exists() else 0)
    free = shutil.disk_usage(downloads).free
    if free < need:
        raise ModelDownloadError(
            f"not enough disk space in {root}: need {need} bytes, have {free}"
        )

    if not wheel_path.exists():
        logger.info(
            "downloading %s %s from %s", CUBLAS_PACKAGE, CUBLAS_VERSION, wheel.url
        )
        download_file(
            wheel.url,
            wheel_path,
            ModelFile(wheel.filename, wheel.size, wheel.sha256),
            fetcher=fetcher,
            timeout=timeout,
            cancel=cancel,
            progress=progress,
        )

    staging = root / f"{final_dir.name}.tmp"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        _extract(wheel_path, wheel, staging, cancel)
        (staging / MARKER_NAME).write_text(
            json.dumps(_expected_marker(wheel), indent=2), encoding="utf-8"
        )
        if final_dir.exists():
            # 標記不符的舊目錄（中斷或不同平台）；只會是本函式建立的目錄
            shutil.rmtree(final_dir)
        os.replace(staging, final_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        wheel_path.unlink()
        if not any(downloads.iterdir()):
            downloads.rmdir()
    except OSError as exc:
        # 安裝已完成，只是沒清掉暫存的 wheel；不影響使用
        logger.warning("cannot clean up %s: %s", downloads, exc)
    if progress is not None:
        progress(wheel.size, wheel.size)
    logger.info("GPU runtime installed at %s", final_dir)
    return final_dir
