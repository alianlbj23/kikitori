"""設定檔的讀寫：原子寫入、讀取驗證、執行緒安全的 `SettingsStore`。

位於 config 模組的儲存層：engine 在啟動時 `load()`，`settings.set` 時 `update()`。
寫入先寫暫存檔再 `os.replace`，程式在寫到一半時被關掉也不會留下半個檔案。
"""

import json
import logging
import os
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from kikitori.config.settings import Settings, SettingsError

MAX_SETTINGS_BYTES = 1024 * 1024
INVALID_SUFFIX = ".invalid"

logger = logging.getLogger(__name__)


def load_settings(path: Path) -> Settings | None:
    """讀取並驗證設定檔；檔案不存在時回傳 None。

    Raises:
        SettingsError: 檔案太大、不是 UTF-8 JSON 或內容不合法（訊息帶檔案路徑）。
        OSError: 檔案存在但無法讀取。
    """
    try:
        size = path.stat().st_size
    except FileNotFoundError:
        return None
    if size > MAX_SETTINGS_BYTES:
        raise SettingsError(
            f"{path}: file is {size} bytes, larger than {MAX_SETTINGS_BYTES}"
        )
    raw = path.read_bytes()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise SettingsError(f"{path}: not valid UTF-8 JSON: {exc}") from exc
    try:
        return Settings.from_dict(data)
    except SettingsError as exc:
        raise SettingsError(f"{path}: {exc}") from exc


def save_settings(settings: Settings, path: Path) -> None:
    """以原子方式寫入設定檔（必要時建立目錄）。

    Raises:
        OSError: 無法建立目錄或寫入；原本的檔案保持不變，暫存檔已刪除。
    """
    write_json_atomic(path, settings.to_dict())


def write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    """把 `data` 以 UTF-8 JSON 寫到暫存檔、fsync，再替換成 `path`。

    Raises:
        OSError: 寫入或替換失敗（暫存檔會被刪除）。
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        # 任何失敗（含 KeyboardInterrupt）都不能留下暫存檔；例外照常往上拋。
        try:
            tmp.unlink(missing_ok=True)
        except OSError as cleanup_exc:
            logger.warning("cannot remove temporary file %s: %s", tmp, cleanup_exc)
        raise


class SettingsStore:
    """目前設定的唯一持有者；`get()`／`update()` 可在多執行緒呼叫。"""

    def __init__(self, path: Path) -> None:
        """
        Args:
            path: 設定檔路徑（通常是 `default_settings_path()`）。
        """
        self.path = path
        self._lock = threading.Lock()
        self._settings = Settings()
        self._file_invalid = False

    def load(self) -> str | None:
        """從檔案載入設定。

        Returns:
            None 表示成功（或檔案不存在而使用預設值）；否則是警告訊息：檔案無效，
            目前使用預設值，原檔不會被覆寫，直到第一次 `update()` 時改名保留。
        """
        try:
            loaded = load_settings(self.path)
        except (SettingsError, OSError) as exc:
            with self._lock:
                self._settings = Settings()
                self._file_invalid = True
            message = f"settings file is invalid, using defaults: {exc}"
            logger.warning("%s", message)
            return message
        with self._lock:
            self._settings = loaded if loaded is not None else Settings()
            self._file_invalid = False
        return None

    def get(self) -> Settings:
        """回傳目前設定（不可變物件）。"""
        with self._lock:
            return self._settings

    def update(self, patch: object) -> Settings:
        """深度合併部分設定、驗證並存檔，回傳新設定。

        驗證或存檔失敗時記憶體與檔案都不變。

        Raises:
            SettingsError: 合併結果不合法。
            OSError: 存檔失敗。
        """
        with self._lock:
            updated = self._settings.merged(patch)
            if self._file_invalid:
                self._preserve_invalid_file()
            save_settings(updated, self.path)
            self._settings = updated
            self._file_invalid = False
            return updated

    def _preserve_invalid_file(self) -> None:
        # 使用者手改壞的檔案可能含有想保留的內容，改名而不是覆寫。
        backup = self.path.with_name(self.path.name + INVALID_SUFFIX)
        if self.path.exists():
            os.replace(self.path, backup)
            logger.warning("invalid settings file kept as %s", backup)
