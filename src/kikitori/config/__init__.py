"""設定模組：使用者設定的資料結構、驗證、遷移與設定檔讀寫。

位於 engine 之下：engine 啟動時讀取設定、`settings.set` 時更新，factory 依設定建立
pipeline 元件。設定檔放在各平台的使用者設定目錄（見 `paths.py`）；不存 API key。
規格見 `docs/specs/pipeline-engine.md`。
"""

from kikitori.config.paths import (
    CONFIG_DIR_ENV,
    SETTINGS_FILENAME,
    config_dir_for,
    default_settings_path,
)
from kikitori.config.settings import (
    MIGRATIONS,
    SCHEMA_VERSION,
    AsrSettings,
    ModeSettings,
    PipelineSettings,
    Settings,
    SettingsError,
    TranslationSettings,
    migrate,
)
from kikitori.config.store import (
    SettingsStore,
    load_settings,
    save_settings,
    write_json_atomic,
)

__all__ = [
    "CONFIG_DIR_ENV",
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "SETTINGS_FILENAME",
    "AsrSettings",
    "ModeSettings",
    "PipelineSettings",
    "Settings",
    "SettingsError",
    "SettingsStore",
    "TranslationSettings",
    "config_dir_for",
    "default_settings_path",
    "load_settings",
    "migrate",
    "save_settings",
    "write_json_atomic",
]
