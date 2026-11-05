"""引擎的 stdin／stdout 傳輸：以位元組讀行（有長度上限）、加鎖寫出 NDJSON。

位於 engine 的傳輸層。直接處理位元組並自行以 UTF-8 編解碼：Windows 主控台的
文字編碼可能是 cp950，不能靠 `sys.stdout` 的文字層。stdout 只寫協定訊息。
"""

import json
import logging
import threading
from collections.abc import Iterator, Mapping
from typing import Any, BinaryIO, Final

from kikitori.engine.protocol import MAX_LINE_BYTES

logger = logging.getLogger(__name__)


class ChannelClosedError(OSError):
    """stdout 已無法寫入（Tauri 端關閉了管線）。"""


class MessageWriter:
    """把協定訊息寫成一行 JSON；多執行緒共用，寫入有鎖、寫完立即 flush。"""

    def __init__(self, stream: BinaryIO) -> None:
        """
        Args:
            stream: 二進位輸出（通常是 `sys.stdout.buffer`）。
        """
        self._stream = stream
        self._lock = threading.Lock()
        self._broken = False

    @property
    def broken(self) -> bool:
        """之前的寫入是否失敗過；失敗後不再嘗試寫入。"""
        return self._broken

    def send(self, message: Mapping[str, Any]) -> None:
        """寫出一則訊息。

        Raises:
            ValueError: 訊息含 NaN／Infinity 或無法序列化的值（程式錯誤）。
            ChannelClosedError: stdout 無法寫入。
        """
        line = (
            json.dumps(
                message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
            )
            + "\n"
        ).encode("utf-8")
        with self._lock:
            if self._broken:
                raise ChannelClosedError("protocol output is closed")
            try:
                self._stream.write(line)
                self._stream.flush()
            except (OSError, ValueError) as exc:
                # ValueError：對已關閉的檔案寫入。
                self._broken = True
                logger.error("cannot write to protocol output: %s", exc)
                raise ChannelClosedError(f"protocol output is closed: {exc}") from exc


class _LineTooLong:
    def __repr__(self) -> str:
        return "LINE_TOO_LONG"


LINE_TOO_LONG: Final = _LineTooLong()
"""`read_lines()` 遇到超過上限的行時產出的標記（該行已被丟棄）。"""


def read_lines(
    stream: BinaryIO, max_bytes: int = MAX_LINE_BYTES
) -> Iterator[bytes | _LineTooLong]:
    """逐行讀取直到 EOF；超過 `max_bytes` 的行讀到行尾丟掉，改產出 `LINE_TOO_LONG`。

    產出的行不含結尾的換行。
    """
    while True:
        line = stream.readline(max_bytes + 1)
        if not line:
            return
        if len(line) > max_bytes and not line.endswith(b"\n"):
            # 把這行剩下的部分讀掉，下一行才能從正確位置開始。
            while True:
                rest = stream.readline(max_bytes + 1)
                if not rest or rest.endswith(b"\n"):
                    break
            yield LINE_TOO_LONG
            continue
        yield line.rstrip(b"\r\n")


_LEVEL_NAMES = {
    logging.DEBUG: "debug",
    logging.INFO: "info",
    logging.WARNING: "warning",
    logging.ERROR: "error",
    logging.CRITICAL: "error",
}


class EventLogHandler(logging.Handler):
    """把 logging 紀錄轉成協定的 `log` 事件（只有訊息，traceback 只寫 stderr）。

    訊息由各模組產生；翻譯模組已遮蔽金鑰，引擎自己不記錄環境變數的值。
    """

    def __init__(self, writer: MessageWriter, level: int = logging.INFO) -> None:
        """
        Args:
            writer: 協定輸出。
            level: 轉送的最低等級。
        """
        super().__init__(level)
        self._writer = writer
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        """送出 `log` 事件；stdout 已關閉時略過（錯誤已寫到 stderr）。"""
        # 寫出失敗本身也會記 log；避免在同一個執行緒裡無限遞迴。
        if self._writer.broken or getattr(self._local, "active", False):
            return
        self._local.active = True
        try:
            level = _LEVEL_NAMES.get(record.levelno, "info")
            self._writer.send(
                {
                    "event": "log",
                    "data": {"level": level, "message": record.getMessage()},
                }
            )
        except ChannelClosedError:
            return
        except Exception:  # noqa: BLE001 - logging 規定交給 handleError 處理（寫 stderr）
            self.handleError(record)
        finally:
            self._local.active = False
