"""ASR 模型的使用者快取目錄。

位於 asr 模組的下載層：決定模型檔放在哪裡。依各平台慣例放在「快取」而不是
「設定」目錄，因為模型可以隨時重新下載，使用者清快取時刪掉也不會遺失資料。
平台、環境變數與家目錄都可注入，方便在任一平台測試另外兩個平台的行為。
"""

import os
import platform
from collections.abc import Mapping
from pathlib import Path, PurePath, PurePosixPath, PureWindowsPath

APP_NAME = "kikitori"
MODELS_SUBDIR = "models"


def _absolute_env_path(
    env: Mapping[str, str], name: str, windows: bool
) -> PurePath | None:
    """讀取環境變數中的路徑；空字串或相對路徑視為未設定（XDG 規範要求忽略相對路徑）。"""
    value = env.get(name, "").strip()
    if not value:
        return None
    path: PurePath = PureWindowsPath(value) if windows else PurePosixPath(value)
    return path if path.is_absolute() else None


def cache_dir_for(system: str, env: Mapping[str, str], home: PurePath) -> PurePath:
    r"""依指定平台計算模型快取目錄（純路徑運算，任何平台都能測試其他平台）。

    - Windows：`%LOCALAPPDATA%\kikitori\models`，未設定時 `~\AppData\Local\kikitori\models`
    - macOS：`~/Library/Caches/kikitori/models`
    - Linux 與其他：`$XDG_CACHE_HOME/kikitori/models`，未設定時 `~/.cache/kikitori/models`

    Args:
        system: `platform.system()` 的值，例如 "Windows"、"Darwin"、"Linux"。
        env: 環境變數。
        home: 家目錄。
    """
    base: PurePath | None
    if system == "Windows":
        base = _absolute_env_path(env, "LOCALAPPDATA", windows=True)
        if base is None:
            base = PureWindowsPath(home) / "AppData" / "Local"
    elif system == "Darwin":
        base = PurePosixPath(home) / "Library" / "Caches"
    else:
        base = _absolute_env_path(env, "XDG_CACHE_HOME", windows=False)
        if base is None:
            base = PurePosixPath(home) / ".cache"
    return base / APP_NAME / MODELS_SUBDIR


def default_cache_dir() -> Path:
    """目前平台的模型快取目錄（不會建立目錄）。"""
    return Path(cache_dir_for(platform.system(), os.environ, Path.home()))
