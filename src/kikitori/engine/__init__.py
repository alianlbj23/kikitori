"""engine：stdio NDJSON 伺服器，Tauri 程式與 Python 處理管線之間的唯一入口。

位於套件最上層，使用 config、pipeline、capture。對外：`run_engine()`（`kikitori
engine` 指令）、`EngineServer` 與可注入的 `EngineServices`。協定見
`docs/specs/app-architecture.md`，細節見 `docs/specs/pipeline-engine.md`。
"""

from kikitori.engine.main import FAKE_ENV, run_engine
from kikitori.engine.protocol import PROTOCOL_VERSION
from kikitori.engine.server import EngineServer
from kikitori.engine.services import (
    DefaultHardwareProvider,
    EngineServices,
    HardwareProvider,
    ModelCatalog,
    default_services,
)

__all__ = [
    "FAKE_ENV",
    "PROTOCOL_VERSION",
    "DefaultHardwareProvider",
    "EngineServer",
    "EngineServices",
    "HardwareProvider",
    "ModelCatalog",
    "default_services",
    "run_engine",
]
