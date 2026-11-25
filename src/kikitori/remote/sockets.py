"""遠端運算的 socket 收尾：中斷連線與「先等讀取執行緒結束才關閉」的 websockets 連線類別。

位於 remote 模組的最底層（不依賴 engine），主埠（`remote.server`／`remote.client`）與配對埠
（`remote.pair_net`）共用。

為什麼要等讀取執行緒：websockets 的 `close_socket()` 會在它的讀取執行緒還活著時直接關閉
socket（逾時、送出失敗、握手被拒）。那條執行緒可能正要進入阻塞的 `SSL_read`，而 OpenSSL 用的
是建立時記下的 handle；Windows 會立刻把同一個 handle 值配給下一個新 socket，那條執行緒就悄悄
讀走別條連線的資料（2026-11-20 實測：下一條連線的 HTTP 升級請求憑空消失、5 秒後握手逾時）。
先 shutdown，讀取執行緒幾毫秒內就讀到 EOF 結束，之後才真的關閉。
"""

import logging
import socket
import threading

from websockets.sync.client import ClientConnection
from websockets.sync.connection import Connection
from websockets.sync.server import ServerConnection

logger = logging.getLogger(__name__)

READER_JOIN_SECS = 5.0
"""關閉前等讀取執行緒結束的上限（shutdown 後通常幾毫秒內就結束）。"""


def shutdown_socket(sock: socket.socket) -> None:
    """關掉底層連線的讀寫（不釋放 handle）：卡在 `sendall`／`recv` 的執行緒會立刻收到錯誤。

    用 `socket.socket.shutdown`（基底類別）而不是 `SSLSocket.shutdown`：後者會清掉
    `_sslobj`，另一條執行緒正在用它時會出現難以預料的錯誤。
    """
    try:
        socket.socket.shutdown(sock, socket.SHUT_RDWR)
    except OSError:
        pass  # 還沒連上或已經關閉：目的（不能再讀寫）已達成


def join_reader_before_close(connection: Connection) -> None:
    """別的執行緒要關閉 socket 前，先 shutdown 並等 websockets 的讀取執行緒結束。"""
    reader = connection.recv_events_thread
    if reader is threading.current_thread() or not reader.is_alive():
        return
    shutdown_socket(connection.socket)
    reader.join(READER_JOIN_SECS)
    if reader.is_alive():
        logger.warning("a connection reader did not stop before closing")


class SafeServerConnection(ServerConnection):
    """關閉 socket 前先等讀取執行緒結束（`serve(create_connection=...)`）。"""

    def close_socket(self) -> None:
        join_reader_before_close(self)
        super().close_socket()


class SafeClientConnection(ClientConnection):
    """關閉 socket 前先等讀取執行緒結束（`connect(create_connection=...)`）。"""

    def close_socket(self) -> None:
        join_reader_before_close(self)
        super().close_socket()
