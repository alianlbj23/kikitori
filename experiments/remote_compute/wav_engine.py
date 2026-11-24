"""實驗用的引擎啟動器：真的引擎與服務，只有聲音來源換成「以即時速度播放的 WAV 檔」。

暫時性實驗工具（docs/specs/remote-compute.md「驗收」T15），給 `gui_cdp.mjs` 當 `KIKITORI_ENGINE_CMD`：
GUI 實機驗收不必真的從喇叭放出聲音，也不必另裝虛擬音訊裝置。WAV 路徑從環境變數
`KIKITORI_TEST_WAV` 讀；不設時與正式引擎完全相同。
"""

import dataclasses
import io
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from kikitori.capture import FileSource
from kikitori.engine.io import MessageWriter
from kikitori.engine.main import LOG_FORMAT
from kikitori.engine.server import EngineServer
from kikitori.engine.services import default_services


def main() -> int:
    protocol_out = sys.stdout.buffer
    protocol_in = sys.stdin.buffer
    if isinstance(sys.stderr, io.TextIOWrapper):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    sys.stdout = sys.stderr
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format=LOG_FORMAT)
    services = default_services()
    wav = os.environ.get("KIKITORI_TEST_WAV", "").strip()
    if wav:
        path = Path(wav)
        logging.getLogger(__name__).warning("audio source replaced by %s", path.name)
        services = dataclasses.replace(
            services,
            open_source=lambda device_id, kind: FileSource(path, realtime=True),
        )
    server = EngineServer(services, MessageWriter(protocol_out))
    return server.serve(protocol_in)


if __name__ == "__main__":
    sys.exit(main())
