"""`kikitori engine` 的進入點：設定 stdio 與 logging，選擇服務，啟動伺服器。

位於 engine 的最外層。先把協定通道（真正的 stdout）取出，再把 `sys.stdout`
換成 stderr：任何程式庫的 `print` 都只會進 stderr，不會污染 NDJSON。
"""

import io
import logging
import os
import sys

from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.services import EngineServices, default_services

FAKE_ENV = "KIKITORI_ENGINE_FAKE"
"""設為 "1" 時使用 `testing.py` 的假服務（測試與 UI 開發用）。"""

LOG_FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

logger = logging.getLogger(__name__)


def _select_services() -> EngineServices:
    value = os.environ.get(FAKE_ENV, "").strip()
    if value == "1":
        from kikitori.engine.testing import fake_services

        logger.warning("%s=1: using fake audio, ASR and translation", FAKE_ENV)
        return fake_services()
    if value not in ("", "0"):
        logger.warning("%s must be '1' or '0'; ignored", FAKE_ENV)
    return default_services()


def _use_utf8_stderr() -> None:
    """日誌一律以 UTF-8 寫出。

    Tauri 以 UTF-8 讀 stderr 寫進程式日誌；Python 預設用系統地區編碼（例如日文 Windows
    的 cp932），裝置名稱等非 ASCII 文字會變成亂碼。
    """
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")


def run_engine() -> int:
    """啟動 stdio 引擎，直到 stdin EOF 或 `engine.shutdown`；回傳結束碼。"""
    protocol_out = sys.stdout.buffer
    protocol_in = sys.stdin.buffer
    _use_utf8_stderr()
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=LOG_FORMAT)
    server = EngineServer(_select_services(), MessageWriter(protocol_out))
    return server.serve(protocol_in)
