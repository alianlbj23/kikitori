"""振假名辭典（SudachiDict core）的延後下載、驗證、刪除。

位於 furigana 模組的安裝層：使用者打開振假名時，引擎的 `models.download
{"model": "furigana-dictionary"}` 呼叫 `install_dictionary()`，從 SudachiDict 的 GitHub
Release 下載固定版本的 zip（固定網址、大小與 SHA-256），只解出辭典與授權檔（`LEGAL`、
`LICENSE-2.0.txt`）到模型快取旁的 `furigana/`。辭典不打包進安裝檔（約 69 MiB 下載、
207 MiB 解壓後），沒開振假名的使用者不必下載。選 core 不選 small 的依據見
`docs/specs/furigana.md`「辭典選擇」。

下載沿用 asr 模組的 `download_file()`（續傳、逾時、Content-Length 檢查、雜湊驗證、取消）；
解壓先寫到暫存資料夾，全部驗證通過才改名成正式資料夾（原子），中途失敗不會留下半套辭典。
"""

import hashlib
import json
import logging
import os
import shutil
import threading
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from kikitori.asr.cache import default_cache_dir
from kikitori.asr.download import (
    DownloadCancelledError,
    Fetcher,
    ModelDownloadError,
    ProgressCallback,
    download_file,
    validate_url,
)
from kikitori.asr.registry import ModelFile

logger = logging.getLogger(__name__)

DICTIONARY_ID = "furigana-dictionary"
"""`models.download`／`models.delete` 用來指定振假名辭典的 id。"""

FURIGANA_SUBDIR = "furigana"
MARKER_NAME = "kikitori-furigana.json"
DEFAULT_TIMEOUT_SECS = 30.0
_COPY_CHUNK = 1 << 20


class DictionaryError(RuntimeError):
    """辭典安裝失敗：zip 內容不符或解壓失敗。"""


class UnsafeDictionaryPathError(ValueError):
    """辭典資料夾不是快取內的普通資料夾（連結、junction、含子資料夾），拒絕刪除。"""


@dataclass(frozen=True)
class ArchiveMember:
    """要從 zip 解出的一個檔案。

    Attributes:
        archive_path: zip 內的路徑。
        filename: 解出後的檔名（直接放在辭典資料夾下）。
        size: 解壓後的大小；與 zip 目錄不符就拒絕。
        sha256: 解壓後的 SHA-256（邊解邊算，不符就拒絕）。
    """

    archive_path: str
    filename: str
    size: int
    sha256: str


@dataclass(frozen=True)
class DictionarySpec:
    """一個版本的辭典（固定網址、大小與 SHA-256）。

    Attributes:
        edition: SudachiDict 的版本別（"small"／"core"／"full"）。
        version: SudachiDict 的發佈版本（日期）。
        url: zip 的網址（只允許 https）。
        size: zip 大小。
        sha256: zip 的 SHA-256。
        dictionary: 系統辭典檔。
        notices: 授權全文與聲明（與辭典放在一起）。
        license: 授權名稱（給 UI 顯示）。
        license_url: 授權說明網址。
    """

    edition: str
    version: str
    url: str
    size: int
    sha256: str
    dictionary: ArchiveMember
    notices: tuple[ArchiveMember, ...]
    license: str
    license_url: str

    @property
    def filename(self) -> str:
        """zip 檔名（網址最後一段）。"""
        return self.url.rsplit("/", 1)[-1]

    @property
    def members(self) -> tuple[ArchiveMember, ...]:
        """所有要解出的檔案（辭典在前）。"""
        return (self.dictionary, *self.notices)

    @property
    def installed_bytes(self) -> int:
        """解出的檔案大小總和。"""
        return sum(m.size for m in self.members)

    @property
    def directory_name(self) -> str:
        """安裝資料夾名稱，例如 `sudachidict-core-20260428`（換版本不覆蓋舊的）。"""
        return f"sudachidict-{self.edition}-{self.version}"


_RELEASE = "20260428"
_ROOT = f"sudachi-dictionary-{_RELEASE}"

# 來源：https://github.com/WorksApplications/SudachiDict/releases/tag/v20260428（2026-11-06 下載，
# 大小與 SHA-256 為實測；zip 內的檔案清單與大小來自中央目錄）。SudachiPy 0.6.x 只讀得了 V0 格式的
# 辭典：v20260723 之後的版本改成 V1 格式（給 SudachiPy 0.7），且 v20260723 沒有發佈 zip，所以固定在
# 有 zip 的最後一個 V0 版本。更新時三者（SudachiPy、這裡的網址與雜湊）要一起改。
SUDACHIDICT_CORE = DictionarySpec(
    edition="core",
    version=_RELEASE,
    url=(
        "https://github.com/WorksApplications/SudachiDict/releases/download/"
        f"v{_RELEASE}/sudachi-dictionary-{_RELEASE}-core.zip"
    ),
    size=72238136,
    sha256="40c8ffc095283f07aa06cae922e7b8147bf2919ec8830567b0b3f7a7efa3239f",
    dictionary=ArchiveMember(
        f"{_ROOT}/system_core.dic",
        "system_core.dic",
        217374303,
        "6c1d5adc8a2389875713056e7b39bbcd0073d6122ffd509866e1d3a196f8608e",
    ),
    notices=(
        ArchiveMember(
            f"{_ROOT}/LEGAL",
            "LEGAL",
            6037,
            "725a8776b38e058b185e905594bc9a2437dbf3787df022fffeefedb9a84e4665",
        ),
        ArchiveMember(
            f"{_ROOT}/LICENSE-2.0.txt",
            "LICENSE-2.0.txt",
            11358,
            "cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30",
        ),
    ),
    license="Apache-2.0 (SudachiDict; includes UniDic BSD-3-Clause and NEologd notices)",
    license_url=f"https://github.com/WorksApplications/SudachiDict/blob/v{_RELEASE}/LEGAL",
)
"""正式採用的辭典。"""


@dataclass(frozen=True)
class DictionaryStatus:
    """`models.list` 的 `furigana` 物件。"""

    id: str
    installed: bool
    edition: str
    version: str
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
            "installed": self.installed,
            "edition": self.edition,
            "version": self.version,
            "download_bytes": self.download_bytes,
            "installed_bytes": self.installed_bytes,
            "disk_bytes": self.disk_bytes,
            "path": self.path,
            "license": self.license,
            "license_url": self.license_url,
        }


def default_root() -> Path:
    """辭典的根目錄：模型快取的同層 `<cache>/kikitori/furigana`（不會建立）。"""
    return default_cache_dir().parent / FURIGANA_SUBDIR


def dictionary_dir(root: Path, spec: DictionarySpec = SUDACHIDICT_CORE) -> Path:
    """某版本的安裝資料夾。"""
    return root / spec.directory_name


def dictionary_path(root: Path, spec: DictionarySpec = SUDACHIDICT_CORE) -> Path:
    """系統辭典檔的路徑（不檢查是否存在）。"""
    return dictionary_dir(root, spec) / spec.dictionary.filename


def _download_path(root: Path, spec: DictionarySpec) -> Path:
    return root / "downloads" / spec.filename


def _expected_marker(spec: DictionarySpec) -> dict[str, Any]:
    return {
        "edition": spec.edition,
        "version": spec.version,
        "zip_sha256": spec.sha256,
        "files": {m.filename: m.sha256 for m in spec.members},
    }


def is_installed(root: Path, spec: DictionarySpec = SUDACHIDICT_CORE) -> bool:
    """完成標記相符且每個檔案大小正確（不重算雜湊：解壓時已驗證，啟動時要快）。"""
    directory = dictionary_dir(root, spec)
    marker = directory / MARKER_NAME
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        logger.warning(
            "ignoring unreadable furigana dictionary marker %s: %s", marker, exc
        )
        return False
    if recorded != _expected_marker(spec):
        return False
    for member in spec.members:
        path = directory / member.filename
        try:
            if not path.is_file() or path.stat().st_size != member.size:
                return False
        except OSError as exc:
            logger.warning("cannot check %s: %s", path, exc)
            return False
    return True


def _is_link(path: Path) -> bool:
    # junction 不是 symlink（`is_symlink()` 為 False），但同樣會把刪除帶到別處
    return path.is_symlink() or path.is_junction()


def _plain_files(directory: Path) -> list[Path]:
    """資料夾內的普通檔案；有子資料夾或連結時丟 `UnsafeDictionaryPathError`。"""
    files: list[Path] = []
    for entry in sorted(directory.iterdir()):
        if _is_link(entry) or not entry.is_file():
            raise UnsafeDictionaryPathError(
                f"{entry} is not a plain file; refusing to delete through it"
            )
        files.append(entry)
    return files


def disk_bytes(root: Path, spec: DictionarySpec = SUDACHIDICT_CORE) -> int | None:
    """已安裝辭典實際佔用的位元組（含未完成下載的 zip 殘留）；沒安裝時 None。"""
    if not is_installed(root, spec):
        return None
    total = 0
    try:
        for path in _plain_files(dictionary_dir(root, spec)):
            total += path.lstat().st_size
        part = _download_path(root, spec).with_name(spec.filename + ".part")
        for leftover in (_download_path(root, spec), part):
            if leftover.is_file() and not _is_link(leftover):
                total += leftover.lstat().st_size
    except (OSError, UnsafeDictionaryPathError) as exc:
        logger.warning("cannot measure the furigana dictionary on disk: %s", exc)
        return spec.installed_bytes
    return total


def dictionary_status(
    root: Path | None = None, spec: DictionarySpec = SUDACHIDICT_CORE
) -> DictionaryStatus:
    """辭典是否已安裝、大小與授權（只看檔案，很快）。"""
    root = default_root() if root is None else root
    installed = is_installed(root, spec)
    return DictionaryStatus(
        id=DICTIONARY_ID,
        installed=installed,
        edition=spec.edition,
        version=spec.version,
        download_bytes=spec.size,
        installed_bytes=spec.installed_bytes,
        disk_bytes=disk_bytes(root, spec) if installed else None,
        path=os.fspath(dictionary_path(root, spec)) if installed else None,
        license=spec.license,
        license_url=spec.license_url,
    )


def _sha256_file(path: Path, cancel: threading.Event | None) -> str:
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(_COPY_CHUNK), b""):
            if cancel is not None and cancel.is_set():
                raise DownloadCancelledError(
                    "furigana dictionary verification cancelled"
                )
            hasher.update(block)
    return hasher.hexdigest()


def _extract(
    archive_path: Path, spec: DictionarySpec, dest: Path, cancel: threading.Event | None
) -> None:
    """只解出 `spec.members`（固定的 zip 內路徑，不會寫到 `dest` 之外），逐檔驗證大小與雜湊。"""
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise DictionaryError(f"{archive_path.name} is not a valid zip: {exc}") from exc
    with archive:
        for member in spec.members:
            try:
                info = archive.getinfo(member.archive_path)
            except KeyError:
                raise DictionaryError(
                    f"{member.archive_path} is missing from {spec.filename}"
                ) from None
            if info.file_size != member.size:
                raise DictionaryError(
                    f"{member.archive_path} is {info.file_size} bytes, expected {member.size}"
                )
            hasher = hashlib.sha256()
            written = 0
            try:
                # zipfile 讀到結尾時會檢查 CRC-32，內容損壞會丟 BadZipFile
                with (
                    archive.open(info) as src,
                    (dest / member.filename).open("wb") as out,
                ):
                    while True:
                        if cancel is not None and cancel.is_set():
                            raise DownloadCancelledError(
                                "furigana dictionary installation cancelled"
                            )
                        chunk = src.read(_COPY_CHUNK)
                        if not chunk:
                            break
                        written += len(chunk)
                        if written > member.size:
                            raise DictionaryError(
                                f"{member.archive_path} is larger than {member.size}"
                            )
                        hasher.update(chunk)
                        out.write(chunk)
            except zipfile.BadZipFile as exc:
                raise DictionaryError(
                    f"{member.archive_path} is corrupted: {exc}"
                ) from exc
            if written != member.size or hasher.hexdigest() != member.sha256:
                raise DictionaryError(
                    f"{member.archive_path} does not match the expected size and SHA-256"
                )


def install_dictionary(
    root: Path | None = None,
    *,
    spec: DictionarySpec = SUDACHIDICT_CORE,
    progress: ProgressCallback | None = None,
    cancel: threading.Event | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    fetcher: Fetcher | None = None,
) -> Path:
    """下載 zip、驗證 SHA-256、解出辭典與授權檔，回傳系統辭典檔的路徑。已安裝時直接回傳。

    中斷的下載保留 `.part` 供下次續傳；zip 的雜湊不符時刪除重下。解壓先寫到暫存資料夾，
    全部驗證通過才改名成正式資料夾；失敗時刪除暫存資料夾（已驗證的 zip 保留，下次直接解壓）。
    完成後刪掉 zip（解出的辭典已經夠大）。

    Args:
        root: 辭典根目錄；省略時用 `default_root()`。
        spec: 要安裝的辭典（測試可指定本機的假 zip）。
        progress: 下載進度 `(downloaded_bytes, total_bytes)`，以 zip 大小計。
        cancel: 被設定時在下一個區塊前停止，丟 `DownloadCancelledError`。
        timeout: 連線與每次讀取的逾時秒數。
        fetcher: HTTP 實作；省略時用 `UrllibFetcher`（只允許 https）。

    Raises:
        DictionaryError: zip 內容不符、解壓失敗。
        ModelDownloadError: 網路、HTTP、大小不符、磁碟空間不足。
        ModelIntegrityError: zip 的 SHA-256 不符（`.part` 已刪除）。
        DownloadCancelledError: 被取消。
        ValueError: 參數不合法。
    """
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    validate_url(spec.url)
    root = default_root() if root is None else root
    final_dir = dictionary_dir(root, spec)
    if is_installed(root, spec):
        if progress is not None:
            progress(spec.size, spec.size)
        return final_dir / spec.dictionary.filename

    archive_path = _download_path(root, spec)
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    # 上次已下載完成但解壓失敗時 zip 還在：重算雜湊確認後直接解壓
    if archive_path.exists() and (
        archive_path.stat().st_size != spec.size
        or _sha256_file(archive_path, cancel) != spec.sha256
    ):
        logger.warning("discarding unverified furigana dictionary %s", archive_path)
        archive_path.unlink()

    need = spec.installed_bytes
    if not archive_path.exists():
        part = archive_path.with_name(archive_path.name + ".part")
        need += spec.size - (part.stat().st_size if part.exists() else 0)
    free = shutil.disk_usage(archive_path.parent).free
    if free < need:
        raise ModelDownloadError(
            f"not enough disk space in {root}: need {need} bytes, have {free}"
        )

    if not archive_path.exists():
        logger.info("downloading the furigana dictionary from %s", spec.url)
        download_file(
            spec.url,
            archive_path,
            ModelFile(spec.filename, spec.size, spec.sha256),
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
        _extract(archive_path, spec, staging, cancel)
        (staging / MARKER_NAME).write_text(
            json.dumps(_expected_marker(spec), indent=2), encoding="utf-8"
        )
        if final_dir.exists():
            # 標記不符的舊資料夾（中斷的安裝）；只會是本函式建立的資料夾
            shutil.rmtree(final_dir)
        os.replace(staging, final_dir)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        archive_path.unlink()
        if not any(archive_path.parent.iterdir()):
            archive_path.parent.rmdir()
    except OSError as exc:
        # 安裝已完成，只是沒清掉 zip；不影響使用
        logger.warning("cannot clean up %s: %s", archive_path.parent, exc)
    if progress is not None:
        progress(spec.size, spec.size)
    logger.info("furigana dictionary installed at %s", final_dir)
    return final_dir / spec.dictionary.filename


def delete_dictionary(
    root: Path | None = None, spec: DictionarySpec = SUDACHIDICT_CORE
) -> int:
    """刪除已安裝的辭典（含未完成下載的 zip 殘留），回傳釋放的位元組數。

    呼叫端（引擎）先確認辭典沒有被開啟（Windows 上 mmap 中的檔案刪不掉）。檢查全部通過才刪：
    資料夾不是連結、解析後就在解析後的根目錄正下方、裡面只有普通檔案。先刪完成標記，中途
    失敗時辭典就不會被當成「已安裝」。

    Raises:
        FileNotFoundError: 辭典沒有安裝。
        UnsafeDictionaryPathError: 上述檢查不通過（什麼都不刪）。
        OSError: 刪除失敗（例如檔案被其他程式開著）；已刪的部分不會復原。
    """
    root = default_root() if root is None else root
    if not is_installed(root, spec):
        raise FileNotFoundError(f"the furigana dictionary is not installed in {root}")
    directory = dictionary_dir(root, spec)
    if _is_link(directory):
        raise UnsafeDictionaryPathError(f"{directory} is a link; refusing to delete it")
    if directory.resolve(strict=True).parent != root.resolve(strict=True):
        raise UnsafeDictionaryPathError(f"{directory} resolves outside {root}")
    files = _plain_files(directory)
    files.sort(key=lambda path: path.name != MARKER_NAME)
    leftovers = [
        path
        for path in (
            _download_path(root, spec),
            _download_path(root, spec).with_name(spec.filename + ".part"),
        )
        if path.is_file() and not _is_link(path)
    ]
    freed = 0
    for path in [*files, *leftovers]:
        size = path.lstat().st_size
        path.unlink()
        freed += size
    directory.rmdir()
    downloads = _download_path(root, spec).parent
    try:
        if downloads.is_dir() and not any(downloads.iterdir()):
            downloads.rmdir()
    except OSError as exc:
        logger.warning("cannot clean up %s: %s", downloads, exc)
    logger.info("deleted the furigana dictionary (%d bytes) from %s", freed, directory)
    return freed
