"""說話者向量模型（3D-Speaker CAM++）的延後下載、狀態、驗證與刪除。

位於 speakers 模組的安裝層：使用者打開「說話者標籤」時，引擎的 `models.download
{"model": "speaker-embedding"}` 呼叫 `install_model()`，從 Hugging Face 固定 revision 下載單一
ONNX 檔（固定大小與 SHA-256）到模型快取旁的 `speakers/`。模型不打包進安裝檔，沒開這個功能的
使用者不必下載。選這個模型的依據見 `docs/specs/speaker-labels.md`。

下載沿用 asr 模組的 `download_file()`（續傳、逾時、Content-Length 檢查、雜湊驗證後才改名、取消），
所以檔案存在且大小正確就是驗證過的完整檔案；載入時 `read_verified_model()` 再算一次雜湊，
推論用的就是驗證過的那份內容。
"""

import hashlib
import logging
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kikitori.asr.cache import default_cache_dir
from kikitori.asr.download import (
    Fetcher,
    ModelDownloadError,
    ModelIntegrityError,
    ProgressCallback,
    download_file,
    validate_url,
)
from kikitori.asr.registry import ModelFile

logger = logging.getLogger(__name__)

SPEAKER_MODEL_ID = "speaker-embedding"
"""`models.download`／`models.delete` 用來指定說話者向量模型的 id。"""

SPEAKERS_SUBDIR = "speakers"
DEFAULT_TIMEOUT_SECS = 30.0


class UnsafeModelPathError(ValueError):
    """模型檔不是快取內的普通檔案（連結、junction、在根目錄之外），拒絕刪除。"""


@dataclass(frozen=True)
class SpeakerModelSpec:
    """一個固定版本的說話者向量模型。

    Attributes:
        name: 顯示名稱。
        directory: 安裝資料夾名稱（換模型或版本不覆蓋舊的）。
        url: 檔案網址（只允許 https）。
        file: 檔名、大小與 SHA-256。
        dims: 向量維度（載入時檢查模型輸出）。
        license: 授權名稱（給 UI 顯示）。
        license_url: 授權說明網址。
    """

    name: str
    directory: str
    url: str
    file: ModelFile
    dims: int
    license: str
    license_url: str


_REPO = "csukuangfj/speaker-embedding-models"
_REVISION = "0743f301363dec56491a490f6d6cbc9d67f9a3bf"
_CAMPPLUS_FILE = "3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx"

# 來源：sherpa-onnx 作者轉成 ONNX 的 3D-Speaker 模型（2026-11-09 下載；大小與 SHA-256 與 HF 的 LFS
# 紀錄一致）。原始模型 iic/speech_campplus_sv_zh_en_16k-common_advanced 在 ModelScope 標示 Apache-2.0。
CAMPPLUS_ZH_EN = SpeakerModelSpec(
    name="3D-Speaker CAM++ (zh-en, 16 kHz)",
    directory=f"campplus-zh-en-{_REVISION[:12]}",
    url=f"https://huggingface.co/{_REPO}/resolve/{_REVISION}/{_CAMPPLUS_FILE}",
    file=ModelFile(
        _CAMPPLUS_FILE,
        28281164,
        "aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2",
    ),
    dims=192,
    license="Apache-2.0 (3D-Speaker CAM++)",
    license_url=(
        "https://www.modelscope.cn/models/iic/"
        "speech_campplus_sv_zh_en_16k-common_advanced/summary"
    ),
)
"""正式採用的模型。"""


@dataclass(frozen=True)
class SpeakerModelStatus:
    """`models.list` 的 `speaker_model` 物件。"""

    id: str
    name: str
    installed: bool
    download_bytes: int
    installed_bytes: int
    disk_bytes: int | None
    path: str | None
    license: str
    license_url: str

    def to_dict(self) -> dict[str, Any]:
        """協定用的 JSON 物件。"""
        return {
            "id": self.id,
            "name": self.name,
            "installed": self.installed,
            "download_bytes": self.download_bytes,
            "installed_bytes": self.installed_bytes,
            "disk_bytes": self.disk_bytes,
            "path": self.path,
            "license": self.license,
            "license_url": self.license_url,
        }


def default_root() -> Path:
    """模型的根目錄：模型快取的同層 `<cache>/kikitori/speakers`（不會建立）。"""
    return default_cache_dir().parent / SPEAKERS_SUBDIR


def model_path(root: Path, spec: SpeakerModelSpec = CAMPPLUS_ZH_EN) -> Path:
    """模型檔的路徑（不檢查是否存在）。"""
    return root / spec.directory / spec.file.name


def _part_path(path: Path) -> Path:
    return path.with_name(path.name + ".part")


def _is_link(path: Path) -> bool:
    # junction 不是 symlink（`is_symlink()` 為 False），但同樣會把刪除帶到別處
    return path.is_symlink() or path.is_junction()


def is_installed(root: Path, spec: SpeakerModelSpec = CAMPPLUS_ZH_EN) -> bool:
    """模型檔存在且大小正確（不重算雜湊：下載時已驗證，`models.list` 要快）。"""
    path = model_path(root, spec)
    try:
        return path.is_file() and path.stat().st_size == spec.file.size
    except OSError as exc:
        logger.warning("cannot check %s: %s", path, exc)
        return False


def model_status(
    root: Path | None = None, spec: SpeakerModelSpec = CAMPPLUS_ZH_EN
) -> SpeakerModelStatus:
    """模型是否已安裝、大小與授權（只看檔案，很快）。"""
    root = default_root() if root is None else root
    installed = is_installed(root, spec)
    path = model_path(root, spec)
    # 已安裝的模型加上未完成下載的 .part（取消或中斷的下載會留下它，要看得到、刪得掉）
    disk: int | None = spec.file.size if installed else None
    part = _part_path(path)
    try:
        if part.is_file() and not _is_link(part):
            disk = (disk or 0) + part.stat().st_size
    except OSError as exc:
        logger.warning("cannot measure %s: %s", part, exc)
    return SpeakerModelStatus(
        id=SPEAKER_MODEL_ID,
        name=spec.name,
        installed=installed,
        download_bytes=spec.file.size,
        installed_bytes=spec.file.size,
        disk_bytes=disk,
        path=os.fspath(path) if installed else None,
        license=spec.license,
        license_url=spec.license_url,
    )


def install_model(
    root: Path | None = None,
    *,
    spec: SpeakerModelSpec = CAMPPLUS_ZH_EN,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    fetcher: Fetcher | None = None,
) -> Path:
    """下載並驗證模型，回傳模型檔路徑；已安裝時直接回傳。

    中斷的下載保留 `.part` 供下次續傳；雜湊不符時 `.part` 已被刪除。

    Args:
        root: 模型根目錄；省略時用 `default_root()`。
        spec: 要安裝的模型（測試可指定假的）。
        progress: 下載進度 `(downloaded_bytes, total_bytes)`。
        cancel: 被設定時在下一個區塊前停止，丟 `DownloadCancelledError`。
        timeout: 連線與每次讀取的逾時秒數。
        fetcher: HTTP 實作；省略時用 `UrllibFetcher`（只允許 https）。

    Raises:
        ModelDownloadError: 網路、HTTP、大小不符、磁碟空間不足。
        ModelIntegrityError: SHA-256 不符。
        DownloadCancelledError: 被取消。
        ValueError: 參數不合法。
    """
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    validate_url(spec.url)
    root = default_root() if root is None else root
    path = model_path(root, spec)
    if is_installed(root, spec):
        if progress is not None:
            progress(spec.file.size, spec.file.size)
        return path
    path.parent.mkdir(parents=True, exist_ok=True)
    part = _part_path(path)
    # download_file 續傳 .part；比完整檔案大的 .part 會被丟掉、從頭下載
    part_size = part.stat().st_size if part.is_file() else 0
    need = spec.file.size - (part_size if part_size <= spec.file.size else 0)
    free = shutil.disk_usage(path.parent).free
    if free < need:
        raise ModelDownloadError(
            f"not enough disk space in {root}: need {need} bytes, have {free}"
        )
    if path.exists():
        # 大小不符的舊檔（不會是 download_file 留下的：它驗證通過才改名）
        logger.warning("discarding unexpected speaker model file %s", path)
        path.unlink()
    logger.info("downloading the speaker embedding model from %s", spec.url)
    download_file(
        spec.url,
        path,
        spec.file,
        fetcher=fetcher,
        timeout=timeout,
        cancel=cancel,
        progress=progress,
    )
    logger.info("speaker embedding model installed at %s", path)
    return path


def read_verified_model(
    root: Path | None = None, spec: SpeakerModelSpec = CAMPPLUS_ZH_EN
) -> bytes:
    """讀取已安裝的模型並驗證 SHA-256，回傳內容（直接交給 onnxruntime，確保推論的就是驗證過的檔案）。

    Raises:
        FileNotFoundError: 沒有安裝。
        ModelIntegrityError: 雜湊不符（檔案被改過或損壞；刪除後重新下載）。
    """
    root = default_root() if root is None else root
    if not is_installed(root, spec):
        raise FileNotFoundError(
            f"the speaker embedding model is not installed in {root}"
        )
    data = model_path(root, spec).read_bytes()
    digest = hashlib.sha256(data).hexdigest()
    if digest != spec.file.sha256:
        raise ModelIntegrityError(
            f"{spec.file.name} has SHA-256 {digest}, expected {spec.file.sha256}"
        )
    return data


def delete_model(
    root: Path | None = None, spec: SpeakerModelSpec = CAMPPLUS_ZH_EN
) -> int:
    """刪除已安裝的模型與未完成下載的 `.part`（只有 `.part` 也可以），回傳釋放的位元組數。

    檢查全部通過才刪：資料夾與檔案都不是連結、資料夾解析後就在解析後的根目錄正下方、
    裡面只有模型檔與 `.part`。

    Raises:
        FileNotFoundError: 沒有安裝，也沒有 `.part`。
        UnsafeModelPathError: 上述檢查不通過（什麼都不刪）。
        OSError: 刪除失敗（例如檔案被其他程式開著）。
    """
    root = default_root() if root is None else root
    path = model_path(root, spec)
    if not is_installed(root, spec) and not _part_path(path).is_file():
        raise FileNotFoundError(
            f"the speaker embedding model is not installed in {root}"
        )
    directory = path.parent
    if _is_link(directory):
        raise UnsafeModelPathError(f"{directory} is a link; refusing to delete it")
    if directory.resolve(strict=True).parent != root.resolve(strict=True):
        raise UnsafeModelPathError(f"{directory} resolves outside {root}")
    allowed = {path.name, _part_path(path).name}
    entries = sorted(directory.iterdir())
    for entry in entries:
        if _is_link(entry) or not entry.is_file() or entry.name not in allowed:
            raise UnsafeModelPathError(
                f"{entry} is not a speaker model file; refusing to delete {directory}"
            )
    freed = 0
    for entry in entries:
        size = entry.lstat().st_size
        entry.unlink()
        freed += size
    directory.rmdir()
    logger.info(
        "deleted the speaker embedding model (%d bytes) from %s", freed, directory
    )
    return freed
