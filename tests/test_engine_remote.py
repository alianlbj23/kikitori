"""引擎的遠端運算模式（`compute.mode == "remote"`）：A 的引擎＋127.0.0.1 上的運算主機（假元件）。

涵蓋 docs/specs/remote-compute.md「一場遠端字幕的時序」在引擎這一層的行為：介面看到的事件與
本機模式相同（session_id 是 A 的、字幕兩句、結束是 idle）、振假名在 A 標、使用者停止之後
不再有字幕、連不上與沒配對的錯誤碼、遠端模式不把 ASR 模型算成「使用中」。
"""

import dataclasses
import json
import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.config import Settings
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import ENDLESS_DEVICE, SPEECH_DEVICE, fake_services
from kikitori.remote.identity import load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.paths import compute_dir, servers_path
from kikitori.remote.server import RemoteServer

WAIT = 20.0


class Sink:
    """MessageWriter 的輸出：逐行解析並讓測試等待特定訊息。"""

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

    def events(self, name: str) -> list[dict[str, Any]]:
        with self.cond:
            return [m["data"] for m in self.messages if m.get("event") == name]

    def wait_status(self, session_id: int, *states: str) -> dict[str, Any]:
        found: list[dict[str, Any]] = []

        def seen() -> bool:
            found[:] = [
                m["data"]
                for m in self.messages
                if m.get("event") == "status"
                and m["data"].get("session_id") == session_id
                and m["data"]["state"] in states
            ]
            return bool(found)

        with self.cond:
            if not self.cond.wait_for(seen, WAIT):
                raise AssertionError(f"no {states} for session {session_id}")
        return found[0]


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


class EngineRemoteCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": str(root / "a")})
        env.start()
        self.addCleanup(env.stop)
        # A（這個引擎）的憑證放在它的設定資料夾；B 的放在別處。兩邊互相信任＝已配對。
        a_id = load_or_create_identity(compute_dir())
        b_id = load_or_create_identity(root / "b")
        self.b_trust = TrustStore(root / "b" / "trusted.json")
        self.b_trust.add(a_id.cert_path.read_bytes(), "A")
        TrustStore(servers_path()).add(b_id.cert_path.read_bytes(), "127.0.0.1")
        self.gate = Gate()
        self.remote = RemoteServer(
            identity=b_id,
            trust=self.b_trust,
            settings=Settings,
            build_components=fake_services().build_components,
            acquire_gpu=self.gate.acquire,
            release_gpu=self.gate.release,
            host="127.0.0.1",
            port=0,
            app_version="0.3.1",
            metrics_interval=0.2,
        )
        self.remote.start()
        self.addCleanup(self.remote.stop)

        services = fake_services()

        def no_local_models(*args: Any, **kwargs: Any) -> Any:
            raise AssertionError("remote mode must not load models on this computer")

        self.services = dataclasses.replace(services, build_components=no_local_models)
        self.services.settings_store.update(
            {
                "compute": {
                    "mode": "remote",
                    "host": "127.0.0.1",
                    "port": self.remote.port,
                }
            }
        )
        self.sink = Sink()
        self.engine = EngineServer(
            self.services,
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="0.3.1",
            metrics_interval=0.2,
        )
        self.addCleanup(self.engine.close)
        self.next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> None:
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.engine.handle_line(json.dumps(message).encode())

    def wait_gpu_released(self) -> bool:
        for _ in range(int(WAIT / 0.05)):
            if not self.gate.held:
                return True
            threading.Event().wait(0.05)
        return False


class RemoteSessionTest(EngineRemoteCase):
    def test_a_remote_session_looks_like_a_local_one(self) -> None:
        self.request("session.start", {"mode": "media", "device_id": SPEECH_DEVICE})
        final = self.sink.wait_status(1, "idle", "error")
        self.assertEqual(final["state"], "idle", final)
        states = [
            s["state"] for s in self.sink.events("status") if s.get("session_id") == 1
        ]
        self.assertEqual(states[0], "loading")
        self.assertIn("running", states)
        finals = [
            d for d in self.sink.events("subtitle") if d["is_final"] and d["original"]
        ]
        self.assertEqual(len(finals), 2)
        self.assertTrue(all(d["session_id"] == 1 for d in finals))
        metrics = self.sink.events("metrics")
        self.assertTrue(all(m["session_id"] == 1 for m in metrics))
        self.assertTrue(self.wait_gpu_released())

    def test_furigana_is_added_on_this_computer(self) -> None:
        with mock.patch.object(
            self.engine._furigana,
            "annotate",
            return_value=[(0, 1, "よみ")],
        ) as annotate:
            self.request("session.start", {"mode": "media", "device_id": SPEECH_DEVICE})
            self.sink.wait_status(1, "idle", "error")
        self.assertTrue(annotate.called)
        finals = [d for d in self.sink.events("subtitle") if d["is_final"]]
        self.assertTrue(finals and all(d["ruby"] == [[0, 1, "よみ"]] for d in finals))

    def test_stop_ends_with_idle_and_nothing_after_it(self) -> None:
        self.request(
            "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
        )
        self.sink.wait_status(1, "running")
        self.request("session.stop")
        self.sink.wait_status(1, "idle")
        with self.sink.cond:
            count = len(self.sink.messages)
            last = self.sink.messages[-1]
        threading.Event().wait(0.6)
        with self.sink.cond:
            after = [m for m in self.sink.messages[count:] if m.get("event") != "log"]
        self.assertEqual(after, [], f"after {last}")
        self.assertTrue(self.wait_gpu_released())
        # 停止後可以馬上開始下一場（B 的 GPU 使用權已歸還、同一台電腦重連）
        self.request(
            "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
        )
        self.sink.wait_status(2, "running")
        self.request("session.stop")
        self.sink.wait_status(2, "idle")

    def test_stop_while_connecting(self) -> None:
        # 運算主機的位址是一個不會回應的地方：使用者按停止要立刻結束
        self.services.settings_store.update({"compute": {"host": "10.255.255.1"}})
        self.request("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        self.sink.wait_status(1, "loading")
        self.request("session.stop")
        self.sink.wait_status(1, "idle")

    def test_unreachable_host(self) -> None:
        port = self.remote.port
        self.remote.stop()
        self.services.settings_store.update({"compute": {"port": port}})
        self.request("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        status = self.sink.wait_status(1, "error", "idle")
        self.assertEqual(status["code"], "remote_unreachable")

    def test_not_paired(self) -> None:
        self.b_trust.remove(load_or_create_identity(compute_dir()).spki)
        self.request("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        status = self.sink.wait_status(1, "error", "idle")
        self.assertEqual(status["code"], "remote_not_paired")

    def test_busy_host(self) -> None:
        self.assertTrue(self.gate.acquire())
        self.request("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        status = self.sink.wait_status(1, "error", "idle")
        self.assertEqual(status["code"], "remote_busy")


if __name__ == "__main__":
    unittest.main()
