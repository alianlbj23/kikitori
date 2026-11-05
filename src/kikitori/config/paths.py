"""設定檔的平台目錄。

位於 config 模組的路徑層：決定 `settings.json` 放在哪裡。平台、環境變數與家目錄
都可注入，所以任一平台都能測試另外兩個平台的行為（寫法同 `asr/cache.py`）。
設定放在「設定」目錄而不是快取：使用者清快取時不應該遺失設定。
"""

import logging
import os
import platform
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

APP_NAME = "kikitori"
SETTINGS_FILENAME = "settings.json"
CONFIG_DIR_ENV = "KIKITORI_CONFIG_DIR"
"""覆寫設定目錄的環境變數（測試與可攜版用）；必須是絕對路徑。"""

logger = logging.getLogger(__name__)


def _absolute_env_path(
    env: Mapping[str, str], name: str, windows: bool
) -> PurePath | None:
    """讀取環境變數中的路徑；空字串或相對路徑視為未設定（XDG 規範要求忽略相對路徑）。"""
    value = env.get(name, "").strip()
    if not value:
        return None
    path: PurePath = PureWindowsPath(value) if windows else PurePosixPath(value)
    return path if path.is_absolute() else None


def config_dir_for(system: str, env: Mapping[str, str], home: PurePath) -> PurePath:
    r"""依指定平台計算設定目錄（純路徑運算）。

    - `KIKITORI_CONFIG_DIR` 為絕對路徑時直接使用；相對路徑忽略並記 warning。
    - Windows：`%APPDATA%\kikitori`，未設定時 `~\AppData\Roaming\kikitori`
    - macOS：`~/Library/Application Support/kikitori`
    - Linux 與其他：`$XDG_CONFIG_HOME/kikitori`，未設定時 `~/.config/kikitori`

    Args:
        system: `platform.system()` 的值，例如 "Windows"、"Darwin"、"Linux"。
        env: 環境變數。
        home: 家目錄。
    """
    windows = system == "Windows"
    override = _absolute_env_path(env, CONFIG_DIR_ENV, windows=windows)
    if override is not None:
        return override
    if env.get(CONFIG_DIR_ENV, "").strip():
        logger.warning("%s is not an absolute path; ignored", CONFIG_DIR_ENV)
    if windows:
        base = _absolute_env_path(env, "APPDATA", windows=True)
        if base is None:
            base = PureWindowsPath(home) / "AppData" / "Roaming"
    elif system == "Darwin":
        base = PurePosixPath(home) / "Library" / "Application Support"
    else:
        base = _absolute_env_path(env, "XDG_CONFIG_HOME", windows=False)
        if base is None:
            base = PurePosixPath(home) / ".config"
    return base / APP_NAME


def default_settings_path() -> Path:
    """目前平台的設定檔路徑（不會建立目錄）。"""
    return Path(config_dir_for(platform.system(), os.environ, Path.home())) / (
        SETTINGS_FILENAME
    )
