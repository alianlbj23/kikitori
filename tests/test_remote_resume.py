"""遠端運算的斷線續接（T12，docs/specs/remote-compute.md「斷線重連」）。

- 端到端：一場進行中把連線切斷，客戶端自己重連、`session.resume`、補送音訊；完整字幕不重複、
  不漏句，結束是 idle，GPU 使用權歸還。
- 運算主機的規則：只有同一台電腦、同一個 session 編號、保留期間內才能續接；保留期間其他電腦
  是 busy；過期就結束並歸還 GPU；A 主動停止或送壞訊息不保留。
"""

import json
import os
import ssl
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

import numpy as np
from websockets.sync.client import ClientConnection, connect

from kikitori.capture import ArraySource
from kikitori.config import Settings
from kikitori.engine.testing import _speech_track, fake_services
from kikitori.remote import protocol as rp
from kikitori.remote.client import RemoteClientSession
from kikitori.remote.identity import Identity, load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.server import PATH, RemoteServer
from kikitori.remote.sockets import SafeClientConnection, shutdown_socket

WAIT = 20.0


class Gate:
    def __init__(self) -> None:
        self.held = False
        self.lock = threading.Lock()

    def acquire(self) -> bool:
        with self.lock:
            if self.held:
                return False
            self.held = True
            return True

    def release(self) -> None:
        with self.lock:
            self.held = False


def wait_for(condition: Any, timeout: float = WAIT) -> bool:
    for _ in range(int(timeout / 0.02)):
        if condition():
            return True
        threading.Event().wait(0.02)
    return False


class ResumeCase(unittest.TestCase):
    retain_secs = 5.0

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": str(root / "config")})
        env.start()
        self.addCleanup(env.stop)
        self.b = load_or_create_identity(root / "b")
        self.a = load_or_create_identity(root / "a")
        self.other = load_or_create_identity(root / "c")
        self.trust = TrustStore(root / "b" / "trusted.json")
        self.trust.add(self.a.cert_path.read_bytes(), "A")
        self.trust.add(self.other.cert_path.read_bytes(), "C")
        self.servers = TrustStore(root / "a" / "servers.json")
        self.servers.add(self.b.cert_path.read_bytes(), "127.0.0.1")
        self.gate = Gate()
        self.server = RemoteServer(
            identity=self.b,
            trust=self.trust,
            settings=Settings,
            build_components=fake_services().build_components,
            acquire_gpu=self.gate.acquire,
            release_gpu=self.gate.release,
            host="127.0.0.1",
            port=0,
            app_version="0.3.1",
            metrics_interval=0.2,
            retain_secs=self.retain_secs,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    # --- 原始連線（測運算主機的規則） ---------------------------------------------

    def raw(self, who: Identity | None = None) -> ClientConnection:
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_3
        context.check_hostname = False
        context.load_cert_chain((who or self.a).cert_path, (who or self.a).key_path)
        context.load_verify_locations(cadata=self.b.cert_path.read_text("ascii"))
        ws = connect(
            f"wss://127.0.0.1:{self.server.port}{PATH}",
            ssl=context,
            proxy=None,
            compression=None,
            open_timeout=5,
            # 關閉前等讀取執行緒結束（見 remote.sockets：Windows 的 handle 重用）
            create_connection=SafeClientConnection,
        )
        self.addCleanup(ws.close)
        ws.send(rp.client_hello("0.3.1"))
        return ws

    def recv_until(self, ws: ClientConnection, kind: str) -> dict[str, Any]:
        while True:
            message = rp.parse_server(ws.recv(timeout=WAIT))
            if message["type"] == kind:
                return message

    def start_raw_session(self) -> tuple[ClientConnection, int]:
        ws = self.raw()
        self.recv_until(ws, "welcome")
        ws.send(rp.client_session_start("media", Settings()))
        session_id = self.recv_until(ws, "session.started")["session_id"]
        self.recv_until(ws, "status")  # loading
        return ws, session_id

    def drop(self, ws: ClientConnection) -> None:
        """模擬網路斷掉：直接關 socket（不送 session.stop）。"""
        shutdown_socket(ws.socket)


class EndToEndTest(ResumeCase):
    def test_a_dropped_connection_resumes_without_losing_or_repeating_lines(
        self,
    ) -> None:
        audio = _speech_track()
        statuses: list[tuple[str, Any]] = []
        finals: list[dict[str, Any]] = []
        lock = threading.Lock()
        ended = threading.Event()

        def on_status(state: str, detail: Any, code: Any, data: Any) -> None:
            with lock:
                statuses.append((state, data))
            if state in ("idle", "error"):
                ended.set()

        def on_event(name: str, data: dict[str, Any]) -> None:
            if name == "subtitle" and data["is_final"] and data["original"]:
                with lock:
                    finals.append(data)

        session = RemoteClientSession(
            identity=self.a,
            servers=self.servers,
            host="127.0.0.1",
            port=self.server.port,
            app_version="0.3.1",
            mode="media",
            settings=Settings(),
            on_status=on_status,
            on_event=on_event,
            capture_code=str,
            dead_after=3.0,
        )
        self.addCleanup(session.stop, 5.0)
        session.connect()
        session.start(ArraySource(audio, chunk_samples=800, realtime=True))
        # 第一句完整字幕出來後、第二句說到一半時切斷
        self.assertTrue(wait_for(lambda: len(finals) >= 1))
        with session._lock:
            conn = session._conn
        assert conn is not None
        shutdown_socket(conn.connection.socket)
        self.assertTrue(ended.wait(WAIT), statuses)
        with lock:
            states = list(statuses)
            got = list(finals)
        self.assertEqual(states[-1][0], "idle", states)
        self.assertIn(("running", {"reconnecting": True}), states)
        ids = [f["utterance_id"] for f in got]
        self.assertEqual(len(ids), 2, got)
        self.assertEqual(len(set(ids)), 2, "a final was repeated")
        self.assertTrue(all(f["end_sample"] <= len(audio) for f in got))
        self.assertTrue(wait_for(lambda: not self.gate.held))


class ServerRulesTest(ResumeCase):
    def test_resume_on_a_new_connection(self) -> None:
        ws, session_id = self.start_raw_session()
        # 送滿 1 秒、等 B 確認收到（ack）才切斷：切斷時若本端還有沒讀的訊息，Windows 送 RST，
        # B 會丟掉還沒讀的音訊（真的客戶端會從 B 回報的位置補送，這裡要固定的位置）
        for seq in range(10):
            ws.send(rp.encode_audio(seq, seq * 1600, np.zeros(1600, dtype=np.int16)))
        self.assertEqual(self.recv_until(ws, "ack")["sample"], 16000)
        self.drop(ws)
        again = self.raw()
        self.recv_until(again, "welcome")
        again.send(rp.client_session_resume(session_id, 0))
        resumed = self.recv_until(again, "resumed")
        self.assertEqual(resumed["session_id"], session_id)
        self.assertEqual(resumed["sample"], 16000)
        # 新連線的序號從 0 起、起始樣本接續
        again.send(rp.encode_audio(0, 16000, np.zeros(1600, dtype=np.int16)))
        again.send(json.dumps({"type": "ping", "nonce": 1}))
        self.recv_until(again, "pong")
        again.send(json.dumps({"type": "session.stop"}))
        self.assertEqual(self.recv_until(again, "status")["state"], "idle")

    def test_a_wrong_session_id_cannot_resume(self) -> None:
        ws, session_id = self.start_raw_session()
        self.drop(ws)
        again = self.raw()
        self.recv_until(again, "welcome")
        again.send(rp.client_session_resume(session_id + 1, 0))
        self.assertEqual(
            self.recv_until(again, "resume_failed")["type"], "resume_failed"
        )

    def test_another_computer_is_busy_while_a_session_waits(self) -> None:
        ws, _ = self.start_raw_session()
        self.drop(ws)
        self.assertTrue(wait_for(lambda: self.server.peer_state() == "waiting_resume"))
        stranger = self.raw(self.other)
        self.assertEqual(self.recv_until(stranger, "busy")["type"], "busy")
        self.assertTrue(self.gate.held)

    def test_a_stop_is_not_kept_for_resuming(self) -> None:
        ws, _ = self.start_raw_session()
        ws.send(json.dumps({"type": "session.stop"}))
        self.recv_until(ws, "status")
        ws.close()
        self.assertTrue(wait_for(lambda: not self.gate.held))
        self.assertIsNone(self.server.peer_state())

    def test_disconnect_ends_a_waiting_session(self) -> None:
        ws, _ = self.start_raw_session()
        self.drop(ws)
        self.assertTrue(wait_for(lambda: self.server.peer_state() == "waiting_resume"))
        self.assertTrue(self.server.disconnect())
        self.assertTrue(wait_for(lambda: not self.gate.held))


class ExpiryTest(ResumeCase):
    retain_secs = 0.5

    def test_an_unclaimed_session_expires_and_frees_the_gpu(self) -> None:
        ws, session_id = self.start_raw_session()
        self.drop(ws)
        self.assertTrue(wait_for(lambda: not self.gate.held))
        self.assertIsNone(self.server.peer_state())
        again = self.raw()
        self.recv_until(again, "welcome")
        again.send(rp.client_session_resume(session_id, 0))
        self.assertEqual(
            self.recv_until(again, "resume_failed")["type"], "resume_failed"
        )


if __name__ == "__main__":
    unittest.main()
