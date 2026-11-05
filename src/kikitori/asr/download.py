"""ASR 模型下載：續傳、進度回報、逾時、大小與 SHA-256 驗證。

位於 asr 模組的下載層：引擎的 `models.download` 方法呼叫 `download_model()`
（`models.delete` 呼叫 `delete_model()`），
把 `registry` 中固定 revision 的檔案放進使用者快取目錄；`load_transcriber()`
用 `model_dir()`／`is_downloaded()` 找到模型。網路存取透過 `Fetcher` Protocol，
測試可注入假的實作，不需連網。
"""

import hashlib
import http.client
import json
import logging
import os
import shutil
import threading
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Protocol

from kikitori.asr.registry import ModelFile, ModelSpec

logger = logging.getLogger(__name__)

HF_BASE_URL = "https://huggingface.co"
DEFAULT_TIMEOUT_SECS = 30.0
CHUNK_BYTES = 1 << 20
MARKER_NAME = "kikitori-model.json"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

ProgressCallback = Callable[[int, int], None]
"""進度回報：`(downloaded_bytes, total_bytes)`，以整個模型的所有檔案加總計。"""


class ModelDownloadError(RuntimeError):
    """下載失敗：網路錯誤、逾時、HTTP 錯誤、大小不符或磁碟空間不足。"""


class ModelIntegrityError(ModelDownloadError):
    """下載完成但 SHA-256 不符；不完整的檔案已刪除。"""


class DownloadCancelledError(ModelDownloadError):
    """呼叫端要求取消；已下載的部分保留在 `.part` 供續傳。"""


class Response(Protocol):
    """HTTP 回應的最小介面。"""

    status: int

    def header(self, name: str) -> str | None:
        """回傳回應標頭（不分大小寫），沒有時回傳 None。"""
        ...

    def read(self, size: int) -> bytes:
        """讀取最多 `size` 位元組；結尾時回傳空位元組。"""
        ...

    def close(self) -> None:
        """關閉連線。"""
        ...


class Fetcher(Protocol):
    """發出 GET 請求；`timeout` 套用在連線與每次讀取。"""

    def open(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        """開啟 URL；HTTP 錯誤狀態也以 `Response` 回傳，由呼叫端判斷。"""
        ...


def validate_url(url: str) -> None:
    """只允許 https；http 只允許本機迴路位址（測試用的本機伺服器）。

    Raises:
        ValueError: 協定或主機不被允許。
    """
    parts = urllib.parse.urlsplit(url)
    if parts.scheme == "https" and parts.hostname:
        return
    if parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS:
        return
    raise ValueError(f"refusing to download from non-https URL {url!r}")


class _UrllibResponse:
    def __init__(self, raw: http.client.HTTPResponse | urllib.error.HTTPError) -> None:
        self._raw = raw
        self.status = int(raw.status if raw.status is not None else 0)

    def header(self, name: str) -> str | None:
        return self._raw.headers.get(name)

    def read(self, size: int) -> bytes:
        return self._raw.read(size)

    def close(self) -> None:
        self._raw.close()


class _HttpsOnlyRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Hugging Face 會轉址到 CDN；轉址目標同樣要通過 `validate_url`，避免被降級成 http。"""

    def redirect_request(  # type: ignore[override]
        self,
        req: urllib.request.Request,
        fp: object,
        code: int,
        msg: str,
        headers: object,
        newurl: str,
    ) -> urllib.request.Request | None:
        try:
            validate_url(newurl)
        except ValueError as exc:
            # 轉成 URLError（OSError），讓下載流程統一包成 ModelDownloadError
            raise urllib.error.URLError(f"insecure redirect to {newurl!r}") from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)  # type: ignore[arg-type]


class UrllibFetcher:
    """以標準函式庫 `urllib` 實作的 `Fetcher`。"""

    def __init__(self) -> None:
        self._opener = urllib.request.build_opener(_HttpsOnlyRedirectHandler())

    def open(self, url: str, headers: Mapping[str, str], timeout: float) -> Response:
        """開啟 URL；4xx／5xx 也回傳 `Response`（狀態碼由呼叫端判斷）。"""
        validate_url(url)
        request = urllib.request.Request(url, headers=dict(headers), method="GET")
        try:
            return _UrllibResponse(self._opener.open(request, timeout=timeout))
        except urllib.error.HTTPError as exc:
            return _UrllibResponse(exc)


def model_dir(spec: ModelSpec, cache_dir: Path) -> Path:
    """模型在快取中的目錄：`<cache>/<id>/<revision 前 12 碼>`。"""
    return cache_dir / spec.id / spec.revision[:12]


def _expected_marker(spec: ModelSpec) -> dict[str, object]:
    return {
        "id": spec.id,
        "repo_id": spec.repo_id,
        "revision": spec.revision,
        "files": {f.name: f.sha256 for f in spec.files},
    }


def is_downloaded(spec: ModelSpec, cache_dir: Path) -> bool:
    """模型是否已完整下載並驗證過。

    只比對完成標記與檔案大小、不重算雜湊，讓啟動時列出模型很快；
    雜湊在下載時已逐檔驗證，驗證通過才會出現正式檔名。
    """
    directory = model_dir(spec, cache_dir)
    marker = directory / MARKER_NAME
    try:
        recorded = json.loads(marker.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return False
    except (OSError, ValueError) as exc:
        logger.warning("ignoring unreadable model marker %s: %s", marker, exc)
        return False
    if recorded != _expected_marker(spec):
        return False
    for file in spec.files:
        path = directory / file.name
        if not path.is_file() or path.stat().st_size != file.size:
            return False
    return True


def _file_url(base_url: str, spec: ModelSpec, file: ModelFile) -> str:
    repo = urllib.parse.quote(spec.repo_id, safe="/")
    name = urllib.parse.quote(file.name)
    return f"{base_url.rstrip('/')}/{repo}/resolve/{spec.revision}/{name}"


def _hash_existing(path: Path) -> "hashlib._Hash":
    hasher = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(CHUNK_BYTES), b""):
            hasher.update(block)
    return hasher


class _Progress:
    def __init__(
        self, callback: ProgressCallback | None, total: int, done: int
    ) -> None:
        self.callback = callback
        self.total = total
        self.done = done

    def report(self, file_offset: int, file_base: int) -> None:
        if self.callback is not None:
            self.callback(file_base + file_offset, self.total)


def _download_file(
    url: str,
    dest: Path,
    file: ModelFile,
    fetcher: Fetcher,
    timeout: float,
    cancel: threading.Event | None,
    progress: _Progress,
) -> None:
    part = dest.with_name(dest.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if offset > file.size:
        logger.warning("discarding oversized partial file %s", part)
        part.unlink()
        offset = 0
    hasher = _hash_existing(part) if offset else hashlib.sha256()
    base = progress.done

    if offset < file.size:
        headers = {"User-Agent": "kikitori"}
        if offset:
            headers["Range"] = f"bytes={offset}-"
        try:
            response = fetcher.open(url, headers, timeout)
        except (OSError, http.client.HTTPException) as exc:
            raise ModelDownloadError(f"cannot connect to {url}: {exc}") from exc
        try:
            if response.status == 206 and offset:
                content_range = response.header("Content-Range") or ""
                if not content_range.startswith(f"bytes {offset}-"):
                    raise ModelDownloadError(
                        f"unexpected Content-Range {content_range!r} for {url}"
                    )
                mode = "ab"
            elif response.status == 200:
                # 伺服器不支援續傳時從頭下載
                offset = 0
                hasher = hashlib.sha256()
                mode = "wb"
            else:
                raise ModelDownloadError(f"HTTP {response.status} for {url}")

            length = response.header("Content-Length")
            if (
                length is not None
                and length.isdigit()
                and int(length) != file.size - offset
            ):
                raise ModelDownloadError(
                    f"{url} has Content-Length {length}, expected {file.size - offset}"
                )

            progress.report(offset, base)
            with part.open(mode) as fh:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise DownloadCancelledError(
                            f"download of {file.name} cancelled"
                        )
                    try:
                        chunk = response.read(CHUNK_BYTES)
                    except (OSError, http.client.HTTPException) as exc:
                        raise ModelDownloadError(
                            f"error while reading {url}: {exc}"
                        ) from exc
                    if not chunk:
                        break
                    offset += len(chunk)
                    if offset > file.size:
                        raise ModelDownloadError(
                            f"{url} is larger than expected {file.size}"
                        )
                    fh.write(chunk)
                    hasher.update(chunk)
                    progress.report(offset, base)
        finally:
            response.close()

    if offset != file.size:
        raise ModelDownloadError(
            f"{url} ended after {offset} of {file.size} bytes; retry to resume"
        )
    digest = hasher.hexdigest()
    if digest != file.sha256:
        part.unlink(missing_ok=True)
        raise ModelIntegrityError(
            f"{file.name} has SHA-256 {digest}, expected {file.sha256}; partial file removed"
        )
    os.replace(part, dest)
    progress.done = base + file.size


def download_file(
    url: str,
    dest: Path,
    file: ModelFile,
    *,
    fetcher: Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    cancel: threading.Event | None = None,
    progress: ProgressCallback | None = None,
) -> None:
    """下載單一檔案到 `dest`，與 `download_model()` 用同一套續傳與驗證邏輯。

    給不在 Hugging Face 的檔案使用（例如 hardware 模組從 PyPI 下載 cuBLAS wheel）。
    先寫到 `<dest>.part`（已存在時以 `Range` 續傳），檢查 Content-Length／
    Content-Range、邊下載邊算 SHA-256，相符才改名成 `dest`；不符時刪掉 `.part`。

    Args:
        url: 檔案網址；只允許 https（或本機迴路的 http），轉址也一樣。
        dest: 目的檔案路徑（父目錄必須存在）。
        file: 預期的檔名、大小與 SHA-256。
        fetcher: HTTP 實作；省略時用 `UrllibFetcher`。
        timeout: 連線與每次讀取的逾時秒數。
        cancel: 被設定時在下一個區塊之前停止，丟 `DownloadCancelledError`（保留 `.part`）。
        progress: 進度回報 `(downloaded_bytes, file.size)`。

    Raises:
        ModelDownloadError: 網路、HTTP、大小不符。
        ModelIntegrityError: SHA-256 不符。
        DownloadCancelledError: 被取消。
        ValueError: 網址或逾時不合法。
    """
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    validate_url(url)
    _download_file(
        url,
        dest,
        file,
        UrllibFetcher() if fetcher is None else fetcher,
        timeout,
        cancel,
        _Progress(progress, file.size, 0),
    )


def download_model(
    spec: ModelSpec,
    cache_dir: Path,
    *,
    progress: ProgressCallback | None = None,
    fetcher: Fetcher | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECS,
    cancel: threading.Event | None = None,
    base_url: str = HF_BASE_URL,
) -> Path:
    """下載並驗證模型，回傳模型目錄；已下載過就直接回傳。

    Args:
        spec: 要下載的模型。
        cache_dir: 快取根目錄（通常是 `default_cache_dir()`）。
        progress: 進度回報 `(downloaded_bytes, total_bytes)`；每個區塊（1 MiB）回報一次。
        fetcher: HTTP 實作；省略時用 `UrllibFetcher`。
        timeout: 連線與每次讀取的逾時秒數。
        cancel: 被設定時在下一個區塊之前停止，丟 `DownloadCancelledError`。
        base_url: 下載來源；只允許 https（或本機迴路的 http）。

    Raises:
        ModelDownloadError: 網路、HTTP、大小不符、磁碟空間不足。
        ModelIntegrityError: SHA-256 不符。
        DownloadCancelledError: 被取消。
        ValueError: 參數不合法。
    """
    if not timeout > 0:
        raise ValueError(f"timeout must be positive, got {timeout}")
    validate_url(base_url)
    fetcher = UrllibFetcher() if fetcher is None else fetcher
    directory = model_dir(spec, cache_dir)
    total = spec.total_bytes
    if is_downloaded(spec, cache_dir):
        if progress is not None:
            progress(total, total)
        return directory

    directory.mkdir(parents=True, exist_ok=True)
    pending = [f for f in spec.files if not _is_complete(directory / f.name, f)]
    remaining = sum(f.size for f in pending)
    free = shutil.disk_usage(directory).free
    if free < remaining:
        raise ModelDownloadError(
            f"not enough disk space in {directory}: need {remaining} bytes, have {free}"
        )

    tracker = _Progress(progress, total, total - remaining)
    logger.info(
        "downloading ASR model %s (%d bytes) to %s", spec.id, remaining, directory
    )
    for file in pending:
        _download_file(
            _file_url(base_url, spec, file),
            directory / file.name,
            file,
            fetcher,
            timeout,
            cancel,
            tracker,
        )

    marker = directory / MARKER_NAME
    tmp = marker.with_name(MARKER_NAME + ".tmp")
    tmp.write_text(json.dumps(_expected_marker(spec), indent=2), encoding="utf-8")
    os.replace(tmp, marker)
    if progress is not None:
        progress(total, total)
    logger.info("ASR model %s ready at %s", spec.id, directory)
    return directory


def _is_complete(path: Path, file: ModelFile) -> bool:
    # 正式檔名只會在雜湊驗證通過後出現，所以大小相符即可視為完成
    return path.is_file() and path.stat().st_size == file.size


class UnsafeModelPathError(ValueError):
    """模型資料夾不是快取內的普通資料夾（連結、junction、跑出快取外），拒絕刪除。"""


def _model_root(spec: ModelSpec, cache_dir: Path) -> Path:
    """模型在快取中的根資料夾 `<cache>/<id>`（含所有 revision 與續傳殘留）。

    Raises:
        UnsafeModelPathError: id 不是單一個資料夾名稱（含分隔符號、`..`、磁碟代號）。
    """
    name = spec.id
    if (
        not name
        or name in (".", "..")
        or any(sep in name for sep in ("/", "\\", ":", "\0"))
    ):
        raise UnsafeModelPathError(f"model id {name!r} is not a plain folder name")
    return cache_dir / name


def _is_link(path: Path) -> bool:
    # junction 不是 symlink（`is_symlink()` 為 False），但同樣會把刪除帶到別處
    return path.is_symlink() or path.is_junction()


def _check_no_links(path: Path, root: Path) -> None:
    """`path` 與它到 `root`（含）之間的每一層都不是連結，否則丟 `UnsafeModelPathError`。"""
    current = path
    while True:
        if _is_link(current):
            raise UnsafeModelPathError(
                f"{current} became a link while deleting; refusing to follow it"
            )
        if current == root or current.parent == current:
            return
        current = current.parent


def _scan_tree(root: Path, *, strict: bool) -> tuple[int, list[Path], list[Path]]:
    """`root` 底下普通檔案的大小合計、檔案與資料夾（上層在前）；不跟隨連結。

    Args:
        strict: True 時遇到任何連結就丟 `UnsafeModelPathError`（刪除前的檢查）；
            False 時略過連結（只是列出用量）。
    """
    total = 0
    files: list[Path] = []
    dirs: list[Path] = []

    def fail(error: OSError) -> None:
        raise error

    for dirpath, dirnames, filenames in os.walk(root, followlinks=False, onerror=fail):
        directory = Path(dirpath)
        dirs.append(directory)
        for name in list(dirnames):
            if _is_link(directory / name):
                if strict:
                    raise UnsafeModelPathError(
                        f"{directory / name} is a link; refusing to delete through it"
                    )
                dirnames.remove(name)
        for name in filenames:
            path = directory / name
            if _is_link(path):
                if strict:
                    raise UnsafeModelPathError(
                        f"{path} is a link; refusing to delete through it"
                    )
                continue
            total += path.lstat().st_size
            files.append(path)
    return total, files, dirs


def model_disk_bytes(spec: ModelSpec, cache_dir: Path) -> int | None:
    """已下載模型在磁碟上實際佔用的位元組數；沒有下載時 None。

    算的是 `<cache>/<id>` 底下的普通檔案（含續傳用的 `.part` 與其他 revision 的殘留，
    也就是 `delete_model()` 會釋放的量），不跟隨連結。讀取失敗時記 warning 並回傳
    registry 的大小（模型確實已下載，只是量不到）。
    """
    if not is_downloaded(spec, cache_dir):
        return None
    try:
        root = _model_root(spec, cache_dir)
        return _scan_tree(root, strict=False)[0]
    except (OSError, UnsafeModelPathError) as exc:
        logger.warning("cannot measure ASR model %s on disk: %s", spec.id, exc)
        return spec.total_bytes


def delete_model(spec: ModelSpec, cache_dir: Path) -> int:
    """刪除已下載的模型（`<cache>/<id>` 整個資料夾），回傳釋放的位元組數。

    安全檢查（全部通過才刪任何東西）：id 是單一資料夾名稱；`<cache>/<id>` 不是連結或
    junction；解析後的位置就在解析後的快取資料夾正下方；整棵樹裡沒有任何連結。刪除每個
    檔案與資料夾之前，再檢查它與到 `<cache>/<id>` 為止的每一層都不是連結：掃描之後才被
    換成連結的資料夾不會被跟隨。先刪完成標記，中途失敗時模型就不會被當成「已下載」。

    剩下的限制：重新檢查與刪除之間仍有極短的空檔（Windows 的 Python 沒有以資料夾 handle
    為基準的 unlink／rmdir，無法完全消除）；要利用它，攻擊者必須已經能寫入使用者自己的
    模型快取資料夾。

    Raises:
        FileNotFoundError: 模型沒有下載（只有續傳殘留時也是；殘留留給之後的下載續傳）。
        UnsafeModelPathError: 上述檢查不通過（刪除途中才發現時，已刪的部分不會復原）。
        OSError: 刪除失敗（例如檔案被其他程式開著）；已刪的部分不會復原。
    """
    root = _model_root(spec, cache_dir)
    if not is_downloaded(spec, cache_dir):
        raise FileNotFoundError(f"ASR model {spec.id} is not downloaded to {cache_dir}")
    if _is_link(root):
        raise UnsafeModelPathError(f"{root} is a link; refusing to delete it")
    cache_real = cache_dir.resolve(strict=True)
    root_real = root.resolve(strict=True)
    if root_real.parent != cache_real:
        raise UnsafeModelPathError(
            f"{root} resolves outside the model cache {cache_real}"
        )
    total, files, dirs = _scan_tree(root, strict=True)
    marker = model_dir(spec, cache_dir) / MARKER_NAME
    files.sort(key=lambda path: path != marker)
    for path in files:
        _check_no_links(path, root)
        path.unlink()
    for directory in reversed(dirs):
        _check_no_links(directory, root)
        directory.rmdir()
    logger.info("deleted ASR model %s (%d bytes) from %s", spec.id, total, root)
    return total
