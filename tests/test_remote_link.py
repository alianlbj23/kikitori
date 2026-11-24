"""遠端運算的連線底層 `Link`：單一寫入執行緒、有上限的佇列、看門狗（docs/specs/remote-compute.md）。

用假的連線（記錄送出的訊息、可以讓 send 卡住）加上真的 socketpair（確認中斷時底層 socket
真的被關掉），不需要網路。
"""

import socket
import threading
import unittest
from typing import Any

from websockets.exceptions import ConnectionClosedError

from kikitori.remote.link import Link

WAIT = 10.0


class FakeConnection:
    """`Link` 用到的那一小部分 `Connection`：send、close、socket。"""

    def __init__(self) -> None:
        self.socket, self.peer = socket.socketpair()
        self.sent: list[Any] = []
        self.closed_with: tuple[int, str] | None = None
        self.block = threading.Event()  # 設定後 send 卡住，直到 unblock
        self.unblock = threading.Event()
        self.raise_closed = False
        self.got = threading.Event()

    def send(self, message: Any) -> None:
        if self.raise_closed:
            raise ConnectionClosedError(None, None)
        if self.block.is_set():
            self.unblock.wait(WAIT)
        self.sent.append(message)
        self.got.set()

    def close(self, code: int, reason: str) -> None:
        self.closed_with = (code, reason)

    def peer_sees_eof(self) -> bool:
        self.peer.settimeout(WAIT)
        try:
            return self.peer.recv(1) == b""
        except OSError:
            return True


def wait_for(condition: Any) -> bool:
    for _ in range(int(WAIT / 0.02)):
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


class LinkTest(unittest.TestCase):
    def make(self, **kwargs: Any) -> tuple[Link, FakeConnection]:
        conn = FakeConnection()
        self.addCleanup(conn.peer.close)
        self.addCleanup(conn.socket.close)
        self.addCleanup(conn.unblock.set)
        link = Link(conn, name="test", **{"max_queue": 8, **kwargs})  # type: ignore[arg-type]
        link.start()
        self.addCleanup(link.join, 5.0)
        self.addCleanup(link.abort, "test cleanup")
        return link, conn

    def test_messages_go_out_in_order_and_close_flushes_first(self) -> None:
        link, conn = self.make()
        for i in range(5):
            self.assertTrue(link.send(f"m{i}"))
        link.send(b"\x00\x01")
        link.close(1000, "bye")
        self.assertTrue(wait_for(lambda: conn.closed_with is not None))
        self.assertEqual(conn.sent, ["m0", "m1", "m2", "m3", "m4", b"\x00\x01"])
        self.assertEqual(conn.closed_with, (1000, "bye"))
        self.assertTrue(link.closed)
        self.assertIsNone(link.reason)
        self.assertFalse(link.send("late"))

    def test_a_full_queue_means_the_connection_is_broken(self) -> None:
        link, conn = self.make(max_queue=2)
        conn.block.set()
        link.send("first")  # 寫入執行緒拿走並卡在 send
        self.assertTrue(wait_for(lambda: link._writing_since is not None))
        self.assertTrue(link.send("2"))
        self.assertTrue(link.send("3"))
        self.assertFalse(link.send("4"))  # 滿了：不阻塞，直接中斷
        self.assertTrue(link.closed)
        self.assertIn("queue is full", link.reason or "")
        self.assertTrue(conn.peer_sees_eof())

    def test_the_watchdog_breaks_a_stalled_send(self) -> None:
        # Wi-Fi 斷線、對方不收：sendall 不會自己返回
        link, conn = self.make(write_stall=0.3)
        conn.block.set()
        link.send("stuck")
        self.assertTrue(wait_for(lambda: link.closed))
        self.assertIn("stalled", link.reason or "")
        self.assertTrue(conn.peer_sees_eof())

    def test_abort_from_another_thread_shuts_the_socket_at_once(self) -> None:
        link, conn = self.make()
        link.send("a")
        self.assertTrue(conn.got.wait(WAIT))
        threading.Thread(target=link.abort, args=("user stopped",)).start()
        self.assertTrue(conn.peer_sees_eof())
        self.assertTrue(wait_for(lambda: link.closed))
        self.assertEqual(link.reason, "user stopped")
        link.abort("again")  # 可重複呼叫，不改原因
        self.assertEqual(link.reason, "user stopped")
        self.assertTrue(link.join(5.0))

    def test_a_closed_connection_ends_the_writer(self) -> None:
        link, conn = self.make()
        conn.raise_closed = True
        link.send("x")
        self.assertTrue(wait_for(lambda: link.closed))
        self.assertTrue(link.join(5.0))

    def test_rejects_bad_limits(self) -> None:
        conn = FakeConnection()
        self.addCleanup(conn.peer.close)
        self.addCleanup(conn.socket.close)
        for kwargs in ({"max_queue": 0}, {"max_queue": 4, "write_stall": 0}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Link(conn, name="x", **kwargs)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
