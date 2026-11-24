"""遠端運算的 A 端客戶端：真的 TLS 連到 127.0.0.1 上的運算主機（假的 pipeline 元件）。

涵蓋 docs/specs/remote-compute.md 的「一場遠端字幕的時序」與 A 端的錯誤碼：正常的一場（字幕
欄位與本機相同、樣本位置是 A 的時間軸）、沒配對／主機憑證不符／被移除配對、忙碌、連不上、
使用者停止（之後不再有回呼）、運算主機消失（`remote_lost`），以及 B 送來不合格字幕時的過濾。
"""

import os
import tempfile
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.capture import ArraySource, CaptureError
from kikitori.config import Settings
from kikitori.engine.testing import EndlessSource, _speech_track, fake_services
from kikitori.remote import protocol as rp
from kikitori.remote.client import RemoteClientSession, RemoteError
from kikitori.remote.identity import Identity, load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.server import RemoteServer

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


class Recorder:
    """收集回呼（在客戶端的執行緒上呼叫）。"""

    def __init__(self) -> None:
        self.statuses: list[tuple[str, str | None, str | None, Any]] = []
        self.events: list[tuple[str, dict[str, Any]]] = []
        self.lock = threading.Lock()
        self.ended = threading.Event()

    def on_status(
        self, state: str, detail: str | None, code: str | None, data: Any
    ) -> None:
        with self.lock:
            self.statuses.append((state, detail, code, data))
        if state in ("idle", "error"):
            self.ended.set()

    def on_event(self, name: str, data: dict[str, Any]) -> None:
        with self.lock:
            self.events.append((name, data))

    def finals(self) -> list[dict[str, Any]]:
        with self.lock:
            return [
                d
                for n, d in self.events
                if n == "subtitle" and d["is_final"] and d["original"]
            ]

    def wait_for(self, condition: Any) -> bool:
        for _ in range(int(WAIT / 0.02)):
            if condition():
                return True
            threading.Event().wait(0.02)
        return False


class RemoteClientCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        env = mock.patch.dict(
            os.environ, {"KIKITORI_CONFIG_DIR": str(self.root / "config")}
        )
        env.start()
        self.addCleanup(env.stop)
        self.server_id = load_or_create_identity(self.root / "b")
        self.client_id = load_or_create_identity(self.root / "a")
        # B 信任 A、A 信任 B（配對完成後的狀態）
        self.trust = TrustStore(self.root / "b" / "trusted.json")
        self.trust.add(self.client_id.cert_path.read_bytes(), "A")
        self.servers = TrustStore(self.root / "a" / "servers.json")
        self.servers.add(self.server_id.cert_path.read_bytes(), "127.0.0.1")
        self.gate = Gate()
        self.server = self.start_server()

    def start_server(self) -> RemoteServer:
        server = RemoteServer(
            identity=self.server_id,
            trust=self.trust,
            settings=Settings,
            build_components=fake_services().build_components,
            acquire_gpu=self.gate.acquire,
            release_gpu=self.gate.release,
            host="127.0.0.1",
            port=0,
            app_version="0.3.1",
            metrics_interval=0.2,
        )
        server.start()
        self.addCleanup(server.stop)
        return server

    def client(
        self,
        recorder: Recorder,
        *,
        identity: Identity | None = None,
        servers: TrustStore | None = None,
        port: int | None = None,
        **kwargs: Any,
    ) -> RemoteClientSession:
        session = RemoteClientSession(
            identity=identity or self.client_id,
            servers=servers or self.servers,
            host="127.0.0.1",
            port=self.server.port if port is None else port,
            app_version="0.3.1",
            mode="media",
            settings=Settings(),
            on_status=recorder.on_status,
            on_event=recorder.on_event,
            capture_code=lambda exc: "device_lost",
            **kwargs,
        )
        self.addCleanup(session.stop, 5.0)
        return session

    def connect_error(self, session: RemoteClientSession) -> RemoteError:
        with self.assertRaises(RemoteError) as ctx:
            session.connect()
        return ctx.exception

    def wait_gpu_released(self) -> bool:
        return Recorder().wait_for(lambda: not self.gate.held)


class SessionTest(RemoteClientCase):
    def test_a_whole_session_from_a_file_like_source(self) -> None:
        audio = _speech_track()
        rec = Recorder()
        session = self.client(rec)
        session.connect()
        self.assertIn("final", session.models)
        session.start(ArraySource(audio, chunk_samples=800))
        self.assertTrue(rec.ended.wait(WAIT), rec.statuses)
        states = [s[0] for s in rec.statuses]
        self.assertEqual(states[0], "loading")
        self.assertIn("running", states)
        self.assertEqual(states[-1], "idle")
        finals = rec.finals()
        self.assertEqual(len(finals), 2, finals)
        for final in finals:
            # 只留認得的欄位：session_id 由引擎補，B 的 session 編號不外流
            self.assertNotIn("session_id", final)
            self.assertTrue(
                0 <= final["start_sample"] < final["end_sample"] <= len(audio)
            )
            self.assertIn("latency_ms", final)
        self.assertTrue(session.wait(1.0))
        self.assertTrue(self.wait_gpu_released())

    def test_stop_is_bounded_and_silences_callbacks(self) -> None:
        rec = Recorder()
        session = self.client(rec)
        session.connect()
        session.start(EndlessSource())
        self.assertTrue(
            rec.wait_for(lambda: any(s[0] == "running" for s in rec.statuses))
        )
        session.stop(5.0)
        with rec.lock:
            seen = (len(rec.statuses), len(rec.events))
        threading.Event().wait(0.5)
        with rec.lock:
            self.assertEqual((len(rec.statuses), len(rec.events)), seen)
        # 使用者停止：結束狀態由引擎自己送，客戶端不送
        self.assertNotIn("idle", [s[0] for s in rec.statuses])
        self.assertTrue(self.wait_gpu_released())

    def test_metrics_carry_the_round_trip_time(self) -> None:
        rec = Recorder()
        session = self.client(rec, heartbeat=0.1)
        session.connect()
        session.start(EndlessSource())

        def has_rtt() -> bool:
            with rec.lock:
                return any(
                    n == "metrics" and d.get("rtt_ms") is not None
                    for n, d in rec.events
                )

        self.assertTrue(rec.wait_for(has_rtt))
        self.assertIsNotNone(session.rtt_ms)

    def test_the_compute_host_disappearing_is_remote_lost(self) -> None:
        rec = Recorder()
        session = self.client(rec, dead_after=2.0, reconnect_secs=3.0)
        session.connect()
        session.start(EndlessSource())
        self.assertTrue(
            rec.wait_for(lambda: any(s[0] == "running" for s in rec.statuses))
        )
        self.server.stop()
        self.assertTrue(rec.ended.wait(WAIT))
        self.assertEqual(rec.statuses[-1][0], "error")
        self.assertEqual(rec.statuses[-1][2], "remote_lost")

    def test_a_capture_error_ends_the_session_with_its_code(self) -> None:
        class Broken(EndlessSource):
            def read(self, timeout: float) -> Any:
                raise CaptureError("unplugged")

        rec = Recorder()
        session = self.client(rec)
        session.connect()
        session.start(Broken())
        self.assertTrue(rec.ended.wait(WAIT))
        self.assertEqual(rec.statuses[-1][:3], ("error", "unplugged", "device_lost"))
        self.assertIsInstance(session.error, CaptureError)
        self.assertTrue(self.wait_gpu_released())


class ConnectErrorTest(RemoteClientCase):
    def test_no_paired_host_at_all(self) -> None:
        empty = TrustStore(self.root / "a" / "none.json")
        error = self.connect_error(self.client(Recorder(), servers=empty))
        self.assertEqual(error.code, "remote_not_paired")

    def test_a_host_with_another_key_is_refused(self) -> None:
        # 位址相同、但出示的憑證不是配對時的那張（中間人、重灌過的主機）
        other = load_or_create_identity(self.root / "other")
        servers = TrustStore(self.root / "a" / "other.json")
        servers.add(other.cert_path.read_bytes(), "127.0.0.1")
        error = self.connect_error(self.client(Recorder(), servers=servers))
        self.assertEqual(error.code, "remote_not_paired")

    def test_the_host_does_not_know_this_computer(self) -> None:
        stranger = load_or_create_identity(self.root / "x")
        error = self.connect_error(self.client(Recorder(), identity=stranger))
        self.assertEqual(error.code, "remote_not_paired")

    def test_the_host_removed_this_computer(self) -> None:
        self.trust.remove(self.client_id.spki)
        error = self.connect_error(self.client(Recorder()))
        self.assertEqual(error.code, "remote_not_paired")

    def test_busy(self) -> None:
        self.assertTrue(self.gate.acquire())  # B 自己在跑字幕
        error = self.connect_error(self.client(Recorder()))
        self.assertEqual(error.code, "remote_busy")

    def test_unreachable(self) -> None:
        port = self.server.port
        self.server.stop()
        error = self.connect_error(self.client(Recorder(), port=port))
        self.assertEqual(error.code, "remote_unreachable")

    def test_no_host_set(self) -> None:
        session = RemoteClientSession(
            identity=self.client_id,
            servers=self.servers,
            host="",
            port=1,
            app_version="0.3.1",
            mode="media",
            settings=Settings(),
            on_status=Recorder().on_status,
            on_event=Recorder().on_event,
            capture_code=str,
        )
        self.assertEqual(self.connect_error(session).code, "remote_not_configured")


class UntrustedEventsTest(RemoteClientCase):
    """B 的回傳是不可信輸入：不合格的字幕丟掉，只留認得的欄位。"""

    def check(self, data: dict[str, Any], sent: int = 16000) -> dict[str, Any] | None:
        session = self.client(Recorder())
        session._captured = sent
        return session._check_subtitle(data)

    def base(self, **overrides: Any) -> dict[str, Any]:
        data: dict[str, Any] = {
            "utterance_id": 3,
            "original": "こんにちは",
            "translation": "你好",
            "source_language": "ja",
            "target_language": "zh-TW",
            "is_final": True,
            "start_sample": 100,
            "end_sample": 8000,
            "latency_ms": 900,
            "session_id": 99,
            "voice": 2,
        }
        data.update(overrides)
        return data

    def test_a_good_final_keeps_only_known_fields(self) -> None:
        out = self.check(self.base(injected="<img onerror=x>"))
        assert out is not None
        self.assertNotIn("injected", out)
        self.assertNotIn("session_id", out)
        self.assertEqual(out["voice"], 2)

    def test_only_consecutive_bad_events_end_the_session(self) -> None:
        # 審查第 8 點：偶爾一則不合格不累積；連續太多則才中止
        session = self.client(Recorder())
        session._captured = 16000
        good = self.base()
        for i in range(100):
            session._on_remote_event("subtitle", {"original": 5})
            session._on_remote_event("subtitle", dict(good, utterance_id=i + 1))
        with self.assertRaises(rp.ProtocolViolation):
            for _ in range(25):
                session._on_remote_event("subtitle", {"original": 5})

    def test_bad_subtitles_are_dropped(self) -> None:
        for bad in (
            self.base(original=5),
            self.base(original="x" * 10_001),
            self.base(source_language="../../etc"),
            self.base(end_sample=99_999),  # 超過 A 送出的音訊
            self.base(start_sample=9000),  # 結束在開始之前
            self.base(utterance_id=True),
            self.base(voice=0),
            self.base(speaker="admin"),
            {"original": "x"},
        ):
            with self.subTest(bad=str(bad)[:60]):
                self.assertIsNone(self.check(bad))


if __name__ == "__main__":
    unittest.main()
