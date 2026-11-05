"""MUSAN 語料（OpenSLR SLR17）的下載、續傳、部分解壓與本機狀態檢查。

角色：評測選單的「下載 MUSAN」選項與 `evaluate.py` 的背景素材來源（暫時性實驗，
不屬於正式程式碼）。資料只放在 `experiments/silero_reset/data/`（git 忽略）：
壓縮檔 `musan.tar.gz`，解壓後的 `musan/music/`、`musan/noise/` 與出處說明檔。

只用標準庫：`urllib` 下載（連線與讀取都有 timeout、HTTP Range 續傳、
網路錯誤自動重試），`tarfile` 以串流方式只解壓需要的兩個資料夾，並套用
`data` 過濾器防止壓縮檔內的路徑跳出目的資料夾。
"""

import datetime
import gzip
import http.client
import logging
import re
import shutil
import sys
import tarfile
import urllib.error
import urllib.request
import zlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

logger = logging.getLogger("silero_reset.musan")

ARCHIVE_NAME = "musan.tar.gz"
COMPLETE_MARKER = ".complete"
ATTRIBUTION_FILE = "ATTRIBUTION.md"
KEEP_FOLDERS = ("music", "noise")
CHUNK_BYTES = 1 << 20
_CONTENT_RANGE = re.compile(r"bytes (\d+)-(\d+)/(\d+)")


@dataclass(frozen=True)
class MusanSource:
    """下載來源。預設值取自 OpenSLR SLR17 頁面與實際的 HTTP 標頭（2026-11-05）。"""

    url: str = "https://www.openslr.org/resources/17/musan.tar.gz"
    size_bytes: int = 11_086_114_085
    # music 約 42 小時 + noise 約 6 小時，16 kHz 16-bit 約 5.6 GB，取整留餘裕。
    extracted_bytes_estimate: int = 6_000_000_000


class DownloadError(RuntimeError):
    """下載失敗；訊息包含網址與存放路徑。"""


class _Retryable(Exception):
    """可以續傳重試的網路問題（timeout、斷線、伺服器 5xx）。"""


def is_ready(musan_dir: Path) -> bool:
    """`music/`、`noise/` 都已解壓完成時回傳 True。"""
    return (
        (musan_dir / COMPLETE_MARKER).is_file()
        and (musan_dir / "music").is_dir()
        and (musan_dir / "noise").is_dir()
    )


def describe(source: MusanSource) -> str:
    """下載前顯示給使用者確認的說明。"""
    total = source.size_bytes + source.extracted_bytes_estimate
    return "\n".join(
        [
            "MUSAN（OpenSLR SLR17）：音樂、語音、雜音語料，本評測只使用 music/ 與 noise/。",
            f"  來源網址：{source.url}",
            f"  檔案大小：{_gb(source.size_bytes)}（單一壓縮檔）",
            (
                f"  所需空間：約 {_gb(total)}（壓縮檔 + 解壓 music/、noise/ 約 "
                f"{_gb(source.extracted_bytes_estimate)}；解壓後可刪除壓縮檔）"
            ),
            (
                "  授權：CC BY 4.0（Attribution 4.0 International），需標示出處；"
                "只在本機使用，不進 repo。"
            ),
        ]
    )


def download_menu(
    data_dir: Path,
    source: MusanSource | None = None,
    *,
    timeout: float = 30.0,
    attempts: int = 3,
    free_bytes: Callable[[Path], int] | None = None,
) -> bool:
    """選單的「下載 MUSAN」：確認 → 下載（可續傳）→ 解壓 → 出處說明 → 詢問刪除壓縮檔。

    Returns:
        MUSAN 是否已就緒。

    Raises:
        DownloadError: 下載失敗（重試後仍失敗，或 HTTP 錯誤）。
        ValueError: 壓縮檔損壞或含不安全的路徑。
    """
    source = source or MusanSource()
    musan_dir = data_dir / "musan"
    if is_ready(musan_dir):
        print(f"MUSAN 已就緒：{musan_dir}")
        return True
    print(describe(source))
    if not _confirm("確定要下載嗎？(y/N)："):
        print("已取消，沒有下載任何檔案。")
        return False

    data_dir.mkdir(parents=True, exist_ok=True)
    archive = data_dir / ARCHIVE_NAME
    have = (
        archive.stat().st_size
        if archive.is_file()
        else _part(archive).stat().st_size
        if _part(archive).is_file()
        else 0
    )
    needed = max(0, source.size_bytes - have) + source.extracted_bytes_estimate
    free = (free_bytes or _disk_free)(data_dir)
    if free < needed:
        print(
            f"磁碟空間不足：需要約 {_gb(needed)}，{data_dir} 所在磁碟只剩 {_gb(free)}。"
        )
        return False

    if archive.is_file() and archive.stat().st_size == source.size_bytes:
        print(f"壓縮檔已存在，略過下載：{archive}")
    else:
        download(
            source.url,
            archive,
            expected_size=source.size_bytes,
            timeout=timeout,
            attempts=attempts,
            progress=_print_progress,
        )
    print("解壓 music/ 與 noise/…")
    count = extract_subset(archive, musan_dir)
    write_attribution(musan_dir, source)
    (musan_dir / COMPLETE_MARKER).write_text(_today(), encoding="utf-8")
    print(f"完成：解壓 {count} 個檔案到 {musan_dir}")
    if _confirm(f"要刪除壓縮檔 {archive}（{_gb(source.size_bytes)}）嗎？(y/N)："):
        archive.unlink()
        print("已刪除壓縮檔。")
    return True


def download(
    url: str,
    dest: Path,
    *,
    expected_size: int | None,
    timeout: float = 30.0,
    attempts: int = 3,
    progress: Callable[[int, int | None], None] | None = None,
) -> None:
    """下載到 `dest`，中途資料存在 `dest.part`，支援 HTTP Range 續傳。

    網路逾時、斷線、伺服器 5xx 會從已下載的位置續傳重試，最多 `attempts` 次；
    HTTP 4xx 與大小不符不重試。完成後才把 `.part` 改名為 `dest`。

    Raises:
        DownloadError: 失敗；訊息包含網址與存放路徑。
    """
    if attempts < 1:
        raise ValueError(f"attempts must be >= 1, got {attempts}")
    part = _part(dest)
    for attempt in range(1, attempts + 1):
        try:
            total = _download_once(url, part, expected_size, timeout, progress)
        except _Retryable as exc:
            logger.warning(
                "下載中斷（第 %d/%d 次）：%s → %s：%s",
                attempt,
                attempts,
                url,
                part,
                exc,
            )
            last_error: Exception = exc
            continue
        except urllib.error.HTTPError as exc:
            raise DownloadError(f"HTTP {exc.code} downloading {url} to {part}") from exc
        if expected_size is not None and total != expected_size:
            part.unlink(missing_ok=True)
            raise DownloadError(
                f"{url} is {total} bytes, expected {expected_size}; removed {part}"
            )
        part.replace(dest)
        return
    raise DownloadError(
        f"download of {url} to {part} failed after {attempts} attempts: {last_error}"
    ) from last_error


def _download_once(
    url: str,
    part: Path,
    expected_size: int | None,
    timeout: float,
    progress: Callable[[int, int | None], None] | None,
) -> int:
    """執行一次請求並寫入 `part`；回傳檔案總大小。未收完時丟 `_Retryable`。"""
    offset = part.stat().st_size if part.is_file() else 0
    if expected_size is not None and offset > expected_size:
        offset = 0  # 殘檔比預期還大，一定是別的東西，重新下載
    if expected_size is not None and offset == expected_size:
        return offset
    request = urllib.request.Request(url, headers={"User-Agent": "kikitori-eval"})
    if offset:
        request.add_header("Range", f"bytes={offset}-")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            total = _resolve_total(response, offset)
            if response.status == 200:
                offset = 0  # 伺服器不支援 Range：從頭寫
            mode = "ab" if offset else "wb"
            written = offset
            with part.open(mode) as out:
                while chunk := response.read(CHUNK_BYTES):
                    out.write(chunk)
                    written += len(chunk)
                    if progress is not None:
                        progress(written, total)
    except urllib.error.HTTPError as exc:
        if exc.code >= 500:
            raise _Retryable(f"HTTP {exc.code}") from exc
        raise
    except (TimeoutError, ConnectionError, http.client.HTTPException) as exc:
        raise _Retryable(repr(exc)) from exc
    except urllib.error.URLError as exc:
        raise _Retryable(str(exc.reason)) from exc
    if total is not None and written < total:
        # http.client 在連線提早關閉時可能只回傳空資料而不丟錯，所以自己檢查長度。
        raise _Retryable(f"connection closed at {written} of {total} bytes")
    return written if total is None else total


def _resolve_total(response: http.client.HTTPResponse, offset: int) -> int | None:
    length = response.headers.get("Content-Length")
    if response.status == 206:
        match = _CONTENT_RANGE.fullmatch(response.headers.get("Content-Range", ""))
        if match is None or int(match.group(1)) != offset:
            raise _Retryable(
                f"unexpected Content-Range {response.headers.get('Content-Range')!r} "
                f"for offset {offset}"
            )
        return int(match.group(3))
    if response.status == 200:
        return int(length) if length and length.isdigit() else None
    raise _Retryable(f"unexpected HTTP status {response.status}")


def extract_subset(
    archive: Path, musan_dir: Path, keep: tuple[str, ...] = KEEP_FOLDERS
) -> int:
    """以串流方式解壓 `musan/<keep>/...` 到 `musan_dir/<keep>/...`，回傳檔案數。

    Raises:
        ValueError: 壓縮檔損壞，或成員路徑含 `..`、絕對路徑、指向外部的連結。
    """
    musan_dir.mkdir(parents=True, exist_ok=True)
    (musan_dir / COMPLETE_MARKER).unlink(missing_ok=True)
    count = 0
    try:
        # 用 gzip 模組解壓：它會檢查 CRC，並在檔案被截斷時丟 EOFError；
        # tarfile 自己的串流解壓遇到截斷只會當成正常結束。
        with (
            gzip.open(archive, "rb") as raw,
            tarfile.open(fileobj=raw, mode="r|") as tar,
        ):
            for member in tar:
                parts = PurePosixPath(member.name).parts
                if len(parts) < 3 or parts[0] != "musan" or parts[1] not in keep:
                    continue
                if ".." in parts:
                    raise ValueError(f"unsafe path in {archive}: {member.name!r}")
                renamed = member.replace(name="/".join(parts[1:]), deep=False)
                tar.extract(renamed, musan_dir, filter="data")
                count += member.isfile()
            while raw.read(CHUNK_BYTES):  # 讀完剩下的資料，才會驗證結尾與 CRC
                pass
    except (tarfile.TarError, EOFError, OSError, zlib.error) as exc:
        # 大小正確但內容損壞的壓縮檔不會被重新下載，所以明確告訴使用者怎麼處理。
        raise ValueError(
            f"cannot extract {archive} into {musan_dir}: {exc}. "
            f"The archive may be corrupt; delete {archive} and download again."
        ) from exc
    return count


def write_attribution(musan_dir: Path, source: MusanSource) -> None:
    """寫入出處說明檔（CC BY 4.0 要求標示出處）。"""
    text = f"""# MUSAN 出處說明

- 名稱：MUSAN: A Music, Speech, and Noise Corpus（OpenSLR SLR17）
- 作者：David Snyder, Guoguo Chen, Daniel Povey
- 來源：{source.url}（說明頁 https://www.openslr.org/17/）
- 授權：CC BY 4.0（https://creativecommons.org/licenses/by/4.0/）
- 引用：Snyder, D., Chen, G., & Povey, D. (2015). MUSAN: A Music, Speech, and Noise Corpus. arXiv:1510.08484.
- 本機只解壓：{", ".join(f"{k}/" for k in KEEP_FOLDERS)}；未修改檔案內容
- 下載日期：{_today()}
- 用途：kikitori 的 VAD 評測背景素材，只在本機使用，不進 repo。
"""
    (musan_dir / ATTRIBUTION_FILE).write_text(text, encoding="utf-8")


def _confirm(prompt: str) -> bool:
    return input(prompt).strip().lower() in {"y", "yes"}


def _part(dest: Path) -> Path:
    return dest.with_name(dest.name + ".part")


def _disk_free(path: Path) -> int:
    return shutil.disk_usage(path).free


def _gb(num_bytes: int) -> str:
    return f"{num_bytes / 1e9:.2f} GB"


def _today() -> str:
    return datetime.date.today().isoformat()


_last_percent = -1


def _print_progress(written: int, total: int | None) -> None:
    global _last_percent
    if total:
        percent = int(100 * written / total)
        if percent != _last_percent:
            _last_percent = percent
            print(
                f"\r  下載中 {percent:3d}%（{_gb(written)} / {_gb(total)}）",
                end="",
                file=sys.stdout,
                flush=True,
            )
        if written >= total:
            print()
