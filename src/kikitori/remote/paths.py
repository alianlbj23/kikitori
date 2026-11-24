"""遠端運算的檔案位置：設定資料夾下的 `compute/`（不放進 settings.json、不進診斷資訊）。

位於 remote 模組的設定層（docs/specs/remote-compute.md「設定與介面」）。A 與 B 是同一套程式，
一台電腦可以同時當兩種角色，所以兩份信任清單分開放：

- `identity.crt`／`identity.key`：這台電腦自己的憑證與私鑰（`identity.load_or_create_identity`）。
- `trusted.json`：B 的角色——允許連進來的電腦。
- `servers.json`：A 的角色——配對過、願意把聲音送過去的運算主機。
"""

from pathlib import Path

from kikitori.config import default_settings_path

TRUSTED_FILENAME = "trusted.json"
SERVERS_FILENAME = "servers.json"


def compute_dir() -> Path:
    """`<設定資料夾>/compute`（不會建立）。"""
    return default_settings_path().parent / "compute"


def trusted_path() -> Path:
    """B：允許連進來的電腦。"""
    return compute_dir() / TRUSTED_FILENAME


def servers_path() -> Path:
    """A：配對過的運算主機。"""
    return compute_dir() / SERVERS_FILENAME
