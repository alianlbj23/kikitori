"""引擎的 `remote.*` 方法（docs/specs/remote-compute.md「T13 契約」）。

一個行程只能有一個設定資料夾，所以每個測試只跑一個引擎，另一台電腦用 `kikitori.remote` 直接扮演：

- 這台是運算主機（B）：開伺服器、開配對窗口、輸入 A 的數字；A 連進來跑一場時，本機
  `session.start` 回 `session_active`、刪除使用中的模型回 `model_in_use`；斷開、移除配對。
- 這台是 A：連到 B 的配對埠、確認、記下 B；錯誤路徑。
"""

import dataclasses
import json
import os
import queue
import socket
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.capture import ArraySource
from kikitori.config import Settings
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import ENDLESS_DEVICE, _speech_track, fake_services
from kikitori.remote import pair_net as pn
from kikitori.remote.client import RemoteClientSession
from kikitori.remote.identity import load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.paths import compute_dir, servers_path

WAIT = 20.0


def free_port_pair() -> int:
    """回傳一個主埠，它與「主埠＋1」（配對埠）目前都沒人用。"""
    for _ in range(50):
        with socket.socket() as a:
            a.bind(("127.0.0.1", 0))
            port = int(a.getsockname()[1])
        if port >= 65534:
            continue
        try:
            with socket.socket() as b:
                b.bind(("127.0.0.1", port + 1))
        except OSError:
            continue
        return port
    raise RuntimeError("no free port pair")


class Sink:
    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self.cond = threading.Condition()
        self.buffer = b""

    def write(self, data: bytes) -> int:
        with self.cond:
            self.buffer += data
            *lines, self.buffer = self.buffer.split(b"\n")
            self.messages.extend(json.loads(line) for line in lines if line)
            self.cond.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    def wait(self, predicate: Any) -> dict[str, Any]:
        found: list[dict[str, Any]] = []

        def seen() -> bool:
            found[:] = [m for m in self.messages if predicate(m)]
            return bool(found)

        with self.cond:
            if not self.cond.wait_for(seen, WAIT):
                raise AssertionError("message not seen")
        return found[0]

    def remote_event(self, **match: Any) -> dict[str, Any]:
        return self.wait(
            lambda m: (
                m.get("event") == "remote"
                and all(m["data"].get(k) == v for k, v in match.items())
            )
        )["data"]


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(
            os.environ, {"KIKITORI_CONFIG_DIR": str(self.root / "me")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.services = fake_services()
        self.services.settings_store.load()
        self.sink = Sink()
        self.engine = EngineServer(
            dataclasses.replace(self.services),
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="0.3.1",
            metrics_interval=0.2,
        )
        self.addCleanup(self.engine.close)
        self.next_id = 0

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """送一個請求並等它的回應（`result` 或 `error`）。"""
        self.next_id += 1
        request_id = self.next_id
        message: dict[str, Any] = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.engine.handle_line(json.dumps(message).encode())
        return self.sink.wait(lambda m: m.get("id") == request_id)

    def ok(self, method: str, params: dict[str, Any] | None = None) -> Any:
        reply = self.call(method, params)
        self.assertIn("result", reply, reply)
        return reply["result"]

    def error(self, method: str, params: dict[str, Any] | None = None) -> str:
        reply = self.call(method, params)
        self.assertIn("error", reply, reply)
        return str(reply["error"]["code"])


class ComputeHostTest(EngineCase):
    """這台引擎是運算主機（B）。"""

    def start_server(self) -> int:
        port = free_port_pair()
        status = self.ok(
            "remote.server_set",
            {"enabled": True, "listen_host": "127.0.0.1", "port": port},
        )
        self.assertTrue(status["server"]["listening"])
        self.sink.remote_event(kind="server", listening=True)
        return port

    def pair_a(self, port: int) -> Any:
        """開配對窗口，讓另一台（A）來配對，在這台輸入 A 顯示的數字。"""
        a_id = load_or_create_identity(self.root / "a")
        a_servers = TrustStore(self.root / "a" / "servers.json")
        self.ok("remote.pair_open")
        self.sink.remote_event(kind="pairing", role="server", state="waiting")
        a_updates: queue.Queue[pn.PairingUpdate] = queue.Queue()
        client = pn.PairingClient(
            identity=a_id,
            trust=a_servers,
            name="Living room PC",
            host="127.0.0.1",
            port=pn.pairing_port(port),
            on_update=a_updates.put,
        )
        client.start()
        while (shown := a_updates.get(timeout=WAIT)).state != "show_digits":
            pass
        asking = self.sink.remote_event(kind="pairing", state="enter_digits")
        self.assertEqual(asking["peer_name"], "Living room PC")
        self.assertIsNone(asking["digits"])  # B 不顯示自己的數字
        assert shown.digits is not None
        # 全形與空白也可以（日文輸入法）
        wide = "".join(chr(ord(c) + 0xFEE0) for c in shown.digits)
        self.ok("remote.pair_confirm", {"digits": wide[:3] + " " + wide[3:]})
        self.sink.remote_event(kind="pairing", role="server", state="done")
        while a_updates.get(timeout=WAIT).state != "confirm":
            pass
        client.confirm(True)
        self.assertTrue(client.join(WAIT))
        return a_id, a_servers

    def test_status_before_anything(self) -> None:
        status = self.ok("remote.status")
        self.assertIsNotNone(status["identity"]["fingerprint"])
        self.assertFalse(status["server"]["listening"])
        self.assertEqual((status["peers"], status["servers"]), ([], []))
        self.assertIsNone(status["pairing"])

    def test_enabling_needs_a_listen_address(self) -> None:
        self.assertEqual(
            self.error("remote.server_set", {"enabled": True}), "remote_no_listen_host"
        )
        self.assertEqual(self.error("remote.pair_open"), "remote_no_listen_host")
        self.assertEqual(
            self.error("remote.server_set", {"enabled": "yes"}), "invalid_params"
        )
        self.assertEqual(
            self.error(
                "remote.server_set", {"enabled": True, "listen_host": "gpu-box"}
            ),
            "invalid_settings",
        )

    def test_port_in_use(self) -> None:
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            port = busy.getsockname()[1]
            code = self.error(
                "remote.server_set",
                {"enabled": True, "listen_host": "127.0.0.1", "port": port},
            )
        self.assertEqual(code, "remote_port_in_use")
        self.assertIsNotNone(self.ok("remote.status")["server"]["error"])

    def test_pair_then_serve_a_session_and_share_the_gpu(self) -> None:
        port = self.start_server()
        a_id, a_servers = self.pair_a(port)
        status = self.ok("remote.status")
        self.assertEqual([p["name"] for p in status["peers"]], ["Living room PC"])
        self.assertIsNone(status["pairing"])
        # A 連進來跑一場（剛配對完，不必重新啟動伺服器）
        ended = threading.Event()
        session = RemoteClientSession(
            identity=a_id,
            servers=a_servers,
            host="127.0.0.1",
            port=port,
            app_version="0.3.1",
            mode="media",
            settings=Settings(),
            on_status=lambda state, *_: (
                ended.set() if state in ("idle", "error") else None
            ),
            on_event=lambda *_: None,
            capture_code=str,
        )
        self.addCleanup(session.stop, 5.0)
        session.connect()
        session.start(ArraySource(_speech_track(), chunk_samples=800, realtime=True))
        self.sink.wait(
            lambda m: (
                m.get("event") == "remote"
                and (m["data"].get("peer") or {}).get("state") == "connected"
            )
        )
        # 同一張 GPU：替 A 運算時這台不能自己開始字幕，A 用的模型也不能刪
        self.assertEqual(
            self.error("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE}),
            "session_active",
        )
        model = self.services.settings_store.get().asr.final_model
        self.assertEqual(self.error("models.delete", {"model": model}), "model_in_use")
        # 有電腦正在用這台：不重建身分（會把它斷掉），要先斷開或等它結束
        self.assertEqual(self.error("remote.identity_reset"), "session_active")
        self.assertEqual(
            self.ok("remote.status")["server"]["peer"]["name"], "Living room PC"
        )
        self.assertTrue(ended.wait(WAIT))
        session.stop(5.0)
        # A 離開後 GPU 歸還：這台可以自己開始（收拾是非同步的，重試一下）
        for _ in range(int(WAIT / 0.05)):
            reply = self.call(
                "session.start", {"mode": "media", "device_id": ENDLESS_DEVICE}
            )
            if "result" in reply:
                break
            threading.Event().wait(0.05)
        self.assertIn("result", reply, reply)
        self.ok("session.stop")

    def test_server_port_is_the_listen_port_and_serving_computes_here(self) -> None:
        # 連別台用 compute.port、被別台連用 compute.listen_port；當運算主機時這台自己用就是本機運算
        self.ok(
            "settings.set",
            {"compute": {"mode": "remote", "host": "gpu-box", "port": 50000}},
        )
        port = self.start_server()
        compute = self.services.settings_store.get().compute
        self.assertEqual(
            (compute.port, compute.listen_port, compute.mode), (50000, port, "local")
        )
        self.assertEqual(self.ok("remote.status")["server"]["port"], port)
        # 關掉提供運算不改 mode
        self.ok("remote.server_set", {"enabled": False})
        self.assertEqual(self.services.settings_store.get().compute.mode, "local")

    def test_identity_reset_forgets_every_pairing(self) -> None:
        port = self.start_server()
        a_id, _ = self.pair_a(port)
        # 這台當 A 時配對過的運算主機（直接寫清單）
        TrustStore(servers_path()).add(a_id.cert_path.read_bytes(), "GPU box")
        before = self.ok("remote.status")
        self.assertEqual((len(before["peers"]), len(before["servers"])), (1, 1))
        self.assertEqual(
            self.error("remote.identity_reset", {"keep": True}), "invalid_params"
        )
        status = self.ok("remote.identity_reset")
        self.assertNotEqual(
            status["identity"]["fingerprint"], before["identity"]["fingerprint"]
        )
        self.assertEqual((status["peers"], status["servers"]), ([], []))
        self.assertTrue(status["server"]["listening"])  # 原本開著：用新身分繼續監聽
        self.sink.remote_event(kind="server", listening=True)

    def test_identity_reset_repairs_a_damaged_identity(self) -> None:
        self.ok("remote.status")
        (compute_dir() / "identity.crt").write_text("garbage", encoding="utf-8")
        with mock.patch.object(self.engine._remote, "_identity", None):
            damaged = self.ok("remote.status")
        self.assertIsNone(damaged["identity"])
        self.assertIsNotNone(damaged["identity_error"])
        status = self.ok("remote.identity_reset")
        self.assertIsNotNone(status["identity"])
        self.assertIsNone(status["identity_error"])
        self.assertFalse(status["server"]["listening"])

    def test_identity_reset_keeps_the_identity_when_the_lists_cannot_be_cleared(
        self,
    ) -> None:
        # 先清配對清單、再換身分：清單清不掉時身分還是原本的，可以安全重試
        before = self.ok("remote.status")["identity"]["fingerprint"]
        with mock.patch.object(
            TrustStore, "clear", side_effect=PermissionError("locked")
        ):
            self.assertEqual(self.error("remote.identity_reset"), "settings_io_error")
        self.assertEqual(self.ok("remote.status")["identity"]["fingerprint"], before)

    def test_an_unreadable_identity_folder_does_not_break_the_engine(self) -> None:
        # OSError（權限、防毒鎖檔）與損毀一樣回報在 identity_error，不讓 remote.status 或
        # 引擎啟動失敗（否則連本機字幕都不能用，也走不到「重建身分」）
        self.ok(
            "settings.set",
            {"compute": {"listen_host": "127.0.0.1", "server_enabled": True}},
        )
        with mock.patch(
            "kikitori.engine.remote_service.load_or_create_identity",
            side_effect=PermissionError("access denied"),
        ):
            status = self.ok("remote.status")
            self.engine._remote.start_if_enabled()
        self.assertIsNone(status["identity"])
        self.assertIn("access denied", status["identity_error"])
        self.assertFalse(self.ok("remote.status")["server"]["listening"])

    def test_listen_address_must_be_private_or_tailscale(self) -> None:
        # 威脅模型：不綁所有介面、不綁公用位址（引擎自己擋，不只靠介面）
        for host in ("0.0.0.0", "::", "8.8.8.8"):
            with self.subTest(host=host):
                self.assertEqual(
                    self.error(
                        "remote.server_set", {"enabled": True, "listen_host": host}
                    ),
                    "invalid_params",
                )
        self.assertEqual(self.services.settings_store.get().compute.listen_host, "")

    def test_serving_cannot_start_during_a_remote_session(self) -> None:
        # 遠端字幕進行中把 mode 改成 local 會讓畫面與正在跑的 session 不一致
        self.ok("settings.set", {"compute": {"mode": "remote", "host": "gpu-box"}})
        with mock.patch.object(self.engine, "_session", object()):
            self.assertEqual(
                self.error(
                    "remote.server_set", {"enabled": True, "listen_host": "127.0.0.1"}
                ),
                "session_active",
            )
        self.assertEqual(self.services.settings_store.get().compute.mode, "remote")

    def test_identity_reset_waits_for_the_session_to_end(self) -> None:
        self.ok("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        self.assertEqual(self.error("remote.identity_reset"), "session_active")
        self.ok("session.stop")

    def test_disconnect_and_unpair(self) -> None:
        port = self.start_server()
        a_id, _ = self.pair_a(port)
        self.assertEqual(self.ok("remote.disconnect"), {"disconnected": False})
        spki = a_id.spki.hex()
        status = self.ok("remote.unpair", {"spki": spki, "role": "peer"})
        self.assertEqual(status["peers"], [])
        self.assertEqual(
            self.error("remote.unpair", {"spki": spki, "role": "peer"}),
            "remote_not_found",
        )
        self.assertEqual(
            self.error("remote.unpair", {"spki": "zz", "role": "peer"}),
            "invalid_params",
        )
        self.assertEqual(
            self.error("remote.unpair", {"spki": spki, "role": "admin"}),
            "invalid_params",
        )
        status = self.ok("remote.server_set", {"enabled": False})
        self.assertFalse(status["server"]["listening"])
        self.assertFalse(self.services.settings_store.get().compute.server_enabled)

    def test_cancel_pairing_and_busy(self) -> None:
        self.start_server()
        self.ok("remote.pair_open")
        self.assertEqual(self.error("remote.pair_open"), "remote_pairing_busy")
        self.assertEqual(
            self.error("remote.pair_confirm", {"digits": "12"}), "invalid_params"
        )
        self.ok("remote.pair_cancel")
        failed = self.sink.remote_event(kind="pairing", state="failed")
        self.assertEqual(failed["code"], "cancelled")
        self.assertEqual(
            self.error("remote.pair_confirm", {"digits": "123456"}),
            "remote_pairing_closed",
        )


class ClientSideTest(EngineCase):
    """這台引擎是 A：去和另一台（B）配對。"""

    def test_pair_with_a_compute_host(self) -> None:
        main_port = free_port_pair()
        b_id = load_or_create_identity(self.root / "b")
        b_trust = TrustStore(self.root / "b" / "trusted.json")
        b_updates: queue.Queue[pn.PairingUpdate] = queue.Queue()
        server = pn.PairingServer(
            identity=b_id,
            trust=b_trust,
            name="alian-server",
            host="127.0.0.1",
            port=pn.pairing_port(main_port),
            on_update=b_updates.put,
        )
        server.open()
        self.addCleanup(server.cancel)
        self.ok("remote.pair", {"host": "127.0.0.1", "port": main_port})
        shown = self.sink.remote_event(
            kind="pairing", role="client", state="show_digits"
        )
        self.assertRegex(shown["digits"], r"^\d{6}$")
        self.assertEqual(shown["peer_name"], "alian-server")
        while b_updates.get(timeout=WAIT).state != "enter_digits":
            pass
        server.submit_digits(shown["digits"])
        self.sink.remote_event(kind="pairing", role="client", state="confirm")
        self.ok("remote.pair_confirm", {"same": True})
        self.sink.remote_event(kind="pairing", role="client", state="done")
        servers = self.ok("remote.status")["servers"]
        self.assertEqual(len(servers), 1)
        self.assertEqual(servers[0]["spki"], b_id.spki.hex())
        self.assertEqual(
            self.ok("remote.unpair", {"spki": b_id.spki.hex(), "role": "server"})[
                "servers"
            ],
            [],
        )

    def test_bad_requests(self) -> None:
        self.assertEqual(self.error("remote.pair", {"host": ""}), "invalid_params")
        self.assertEqual(
            self.error("remote.pair", {"host": "x", "port": 80}), "invalid_params"
        )
        self.assertEqual(
            self.error("remote.pair_confirm", {"same": True}), "remote_pairing_closed"
        )
        self.assertEqual(
            self.error("remote.pair_confirm", {"same": True, "digits": "1"}),
            "invalid_params",
        )
        self.assertEqual(self.error("remote.status", {"x": 1}), "invalid_params")

    def test_nobody_listening(self) -> None:
        self.ok("remote.pair", {"host": "127.0.0.1", "port": free_port_pair()})
        failed = self.sink.remote_event(kind="pairing", role="client", state="failed")
        self.assertIn(failed["code"], ("pairing_closed", "unreachable"))


if __name__ == "__main__":
    unittest.main()
