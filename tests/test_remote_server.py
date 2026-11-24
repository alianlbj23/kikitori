"""遠端運算的運算主機：真的 TLS 連線（127.0.0.1）＋假的 pipeline 元件（docs/specs/remote-compute.md）。

涵蓋威脅模型筆記的安全測試：未配對的電腦在握手時被拒、移除配對後舊憑證失效、帶 Origin 的
（瀏覽器）連線被拒、錯誤路徑、壞 JSON、亂序音訊、第二台電腦 busy，以及正常的一場字幕。
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
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidStatus
from websockets.sync.client import ClientConnection, connect

from kikitori.config import Settings
from kikitori.engine import events
from kikitori.engine.testing import _speech_track, fake_services
from kikitori.hardware.nvidia import GpuUsage
from kikitori.pipeline import Pipeline
from kikitori.remote import protocol as rp
from kikitori.remote.identity import Identity, load_or_create_identity
from kikitori.remote.pairing import TrustStore
from kikitori.remote.server import PATH, RemoteServer, is_allowed_source
from kikitori.remote.sockets import SafeClientConnection

WAIT = 20.0


def client_context(client: Identity, server: Identity) -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    context.minimum_version = ssl.TLSVersion.TLSv1_3
    context.check_hostname = False  # 身分靠 pin 的憑證，不靠主機名稱
    context.load_cert_chain(client.cert_path, client.key_path)
    context.load_verify_locations(cadata=server.cert_path.read_text(encoding="ascii"))
    return context


class Gate:
    """GPU 使用權：一次一個。"""

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


HOST_INFO: dict[str, Any] = {
    "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
    "device": "cuda",
    "compute_type": "int8_float16",
    "current": {"fast": "base", "final": "small"},
    "recommended": {"fast": "large-v3-turbo", "final": "large-v3"},
    "models": [
        {"id": "base", "downloaded": True, "need_mb": 450, "fit": "ok"},
        {"id": "large-v3", "downloaded": False, "need_mb": 2140, "fit": "ok"},
    ],
    "translation_model": "qwen3.5:4b",
}


class FakeCapacity:
    """B 的能力（`remote.capacity.HostCapacity` 的介面）：固定的 HostInfo 與已下載的模型。"""

    def __init__(self, downloaded: set[str]) -> None:
        self.downloaded = downloaded
        self.calls = 0

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        self.calls += 1
        return HOST_INFO

    def downloaded_models(self) -> frozenset[str]:
        return frozenset(self.downloaded)


class RemoteServerCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": str(root / "config")})
        env.start()
        self.addCleanup(env.stop)
        self.server_id = load_or_create_identity(root / "b")
        self.client_id = load_or_create_identity(root / "a")
        self.stranger = load_or_create_identity(root / "x")
        self.trust = TrustStore(root / "b" / "trusted.json")
        self.trust.add(self.client_id.cert_path.read_bytes(), "A")
        # 另一台也配對過的電腦（測一對一 busy）：信任錨在伺服器啟動時載入，要先加
        self.other = load_or_create_identity(root / "c")
        self.trust.add(self.other.cert_path.read_bytes(), "C")
        self.gate = Gate()
        self.capacity = FakeCapacity({"base", "small"})
        self.server = RemoteServer(
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
            capacity=self.capacity,
        )
        self.server.start()
        self.addCleanup(self.server.stop)

    def connect(self, who: Identity | None = None, **kwargs: Any) -> ClientConnection:
        return connect(
            f"wss://127.0.0.1:{self.server.port}{kwargs.pop('path', PATH)}",
            ssl=client_context(who or self.client_id, self.server_id),
            proxy=None,
            compression=None,
            open_timeout=5,
            # 關閉前等讀取執行緒結束（見 remote.sockets：Windows 的 handle 重用）
            create_connection=SafeClientConnection,
            **kwargs,
        )

    def wait_gpu_released(self) -> bool:
        for _ in range(200):
            if not self.gate.held:
                return True
            threading.Event().wait(0.05)
        return False

    def hello(self, ws: ClientConnection) -> dict[str, Any]:
        ws.send(rp.client_hello("0.3.1"))
        return rp.parse_server(ws.recv(timeout=WAIT))


class HandshakeTest(RemoteServerCase):
    def test_paired_client_gets_welcome_with_the_servers_models(self) -> None:
        with self.connect() as ws:
            welcome = self.hello(ws)
        self.assertEqual(welcome["type"], "welcome")
        self.assertEqual(
            welcome["models"],
            {"fast": Settings().asr.fast_model, "final": Settings().asr.final_model},
        )

    def test_unpaired_computer_is_refused_in_the_tls_handshake(self) -> None:
        # TLS 1.3 的用戶端憑證在用戶端以為握手完成之後才被拒：依時機，用戶端看到的是 TLS
        # 錯誤、連線被關，或（Linux 上常見）讀 HTTP 回應時連線已關閉（InvalidMessage）
        with (
            self.assertRaises(
                (
                    ssl.SSLError,
                    ConnectionError,
                    OSError,
                    ConnectionClosed,
                    InvalidHandshake,
                )
            ),
            self.connect(self.stranger) as ws,
        ):
            self.hello(ws)

    def test_removing_the_pairing_closes_the_door_at_once(self) -> None:
        # 握手用的信任錨是啟動時載入的；握手後再比對目前的清單，移除後立刻失效
        self.trust.remove(self.client_id.spki)
        with (
            self.assertRaises((ConnectionClosed, ssl.SSLError, OSError)),
            self.connect() as ws,
        ):
            self.hello(ws)
            ws.recv(timeout=WAIT)

    def test_a_computer_paired_while_running_can_connect(self) -> None:
        # 信任錨在啟動時載入：配對成功後要通知伺服器，不必重新啟動
        late = load_or_create_identity(
            Path(self.server_id.cert_path).parent.parent / "late"
        )
        self.trust.add(late.cert_path.read_bytes(), "late")
        self.server.trust_changed()
        with self.connect(late) as ws:
            self.assertEqual(self.hello(ws)["type"], "welcome")

    def test_disconnect_kicks_the_current_computer(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
            self.assertEqual(self.server.owner_spki(), self.client_id.spki)
            self.assertTrue(self.server.disconnect())
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=WAIT)
        self.assertTrue(self.wait_gpu_released())
        self.assertIsNone(self.server.owner_spki())
        self.assertFalse(self.server.disconnect(self.other.spki))

    def test_no_session_tickets_are_sent(self) -> None:
        # TLS 1.3 的 session ticket 在握手後才送：A 的讀取執行緒處理它的同時另一條執行緒在送
        # 第一則訊息，同一個 OpenSSL SSL 物件被兩條執行緒同時使用（負載高時訊息消失、甚至
        # segfault）。運算主機不續用 TLS session，關掉 ticket 沒有副作用。
        with self.connect() as ws:
            self.hello(ws)
            sock = ws.socket
            assert isinstance(sock, ssl.SSLSocket)
            session = sock.session
            assert session is not None
            self.assertFalse(session.has_ticket)

    def test_a_browser_origin_is_refused(self) -> None:
        # 跨站 WebSocket 劫持：瀏覽器發起的連線一定帶 Origin
        with self.assertRaises(InvalidStatus) as ctx:
            self.connect(origin="https://evil.example")
        self.assertEqual(ctx.exception.response.status_code, 403)

    def test_unknown_path_is_refused(self) -> None:
        with self.assertRaises(InvalidStatus) as ctx:
            self.connect(path="/admin")
        self.assertEqual(ctx.exception.response.status_code, 404)

    def test_wrong_protocol_version_is_incompatible(self) -> None:
        with self.connect() as ws:
            ws.send(
                json.dumps(
                    {"type": "hello", "protocol_version": 99, "app_version": "9.9.9"}
                )
            )
            reply = rp.parse_server(ws.recv(timeout=WAIT))
        self.assertEqual(reply["type"], "incompatible")

    def test_malformed_messages_close_the_connection(self) -> None:
        for bad in (
            "not json",
            json.dumps({"type": "models.pull_translation", "model": "x"}),
        ):
            with self.subTest(bad=bad[:30]), self.connect() as ws:
                self.hello(ws)
                ws.send(bad)
                with self.assertRaises(ConnectionClosed):
                    ws.recv(timeout=WAIT)

    def test_a_second_computer_is_told_busy(self) -> None:
        # compute-capacity 三：B 先握手（看 hello 的 intent）才決定佔不佔 GPU，busy 在 hello 之後
        with self.connect() as first:
            self.hello(first)
            with self.connect(self.other) as second:
                reply = self.hello(second)
            # 第一台不受影響
            first.send(json.dumps({"type": "ping", "nonce": 7}))
            self.assertEqual(rp.parse_server(first.recv(timeout=WAIT))["type"], "pong")
        self.assertEqual(reply["type"], "busy")

    def test_the_same_computer_reconnecting_replaces_its_old_connection(self) -> None:
        # Wi-Fi 斷線通常沒有 FIN／RST：B 還沒發現舊連線斷了，A 就重連；不能回 busy
        with self.connect() as first:
            self.hello(first)
            with self.connect() as second:
                welcome = self.hello(second)
                self.assertEqual(welcome["type"], "welcome")
                with self.assertRaises(ConnectionClosed):
                    first.recv(timeout=WAIT)
                second.send(json.dumps({"type": "ping", "nonce": 8}))
                self.assertEqual(
                    rp.parse_server(second.recv(timeout=WAIT))["type"], "pong"
                )
        self.assertTrue(self.wait_gpu_released())

    def test_ping_pong(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
            ws.send(json.dumps({"type": "ping", "nonce": 42}))
            self.assertEqual(
                rp.parse_server(ws.recv(timeout=WAIT)), {"type": "pong", "nonce": 42}
            )


class SessionTest(RemoteServerCase):
    def run_session(
        self, ws: ClientConnection, audio: np.ndarray
    ) -> list[dict[str, Any]]:
        self.hello(ws)
        ws.send(rp.client_session_start("media", Settings()))
        chunk = 1600
        for seq, start in enumerate(range(0, len(audio), chunk)):
            ws.send(rp.encode_audio(seq, start, audio[start : start + chunk]))
        # 停止是「立刻停」（和本機按停止一樣，丟掉還沒處理的工作）：先等到完整字幕回來再停
        messages: list[dict[str, Any]] = []
        stopped = False
        while True:
            m = rp.parse_server(ws.recv(timeout=WAIT))
            messages.append(m)
            if m["type"] == "status" and m["state"] in ("idle", "error"):
                return messages
            final = (
                m["type"] == "event"
                and m["name"] == "subtitle"
                and m["data"]["is_final"]
            )
            if final and not stopped:
                ws.send(json.dumps({"type": "session.stop"}))
                stopped = True

    def test_a_session_streams_subtitles_back(self) -> None:
        audio = _speech_track()
        with self.connect() as ws:
            messages = self.run_session(ws, audio)
        states = [m["state"] for m in messages if m["type"] == "status"]
        self.assertEqual(states[0], "loading")
        self.assertIn("running", states)
        self.assertEqual(states[-1], "idle")
        finals = [
            m["data"]
            for m in messages
            if m["type"] == "event"
            and m["name"] == "subtitle"
            and m["data"]["is_final"]
            and m["data"]["original"]
        ]
        self.assertTrue(finals, "no final subtitle came back")
        # 時間軸是 A 的樣本計數：字幕的位置落在送出的音訊範圍內
        self.assertTrue(all(0 <= d["end_sample"] <= len(audio) for d in finals))
        acks = [m["sample"] for m in messages if m["type"] == "ack"]
        self.assertTrue(acks and acks == sorted(acks))
        # 伺服器在連線關閉後的收尾才歸還 GPU 使用權：等一下，不要假設已經完成
        self.assertTrue(self.wait_gpu_released())

    def test_stop_while_loading_answers_idle_at_once(self) -> None:
        # 審查第 7 點：B 還在載入模型時 A 按停止，B 要回 idle（否則 A 每次白等 2 秒）
        release = threading.Event()
        real = fake_services().build_components

        def slow(*args: Any, **kwargs: Any) -> Any:
            release.wait(WAIT)
            return real(*args, **kwargs)

        self.server._build_components = slow
        self.addCleanup(release.set)
        with self.connect() as ws:
            self.hello(ws)
            ws.send(rp.client_session_start("media", Settings()))
            ws.send(json.dumps({"type": "session.stop"}))
            states = []
            while True:
                m = rp.parse_server(ws.recv(timeout=5))
                if m["type"] == "status":
                    states.append(m["state"])
                    if m["state"] in ("idle", "error"):
                        break
        self.assertEqual(states[-1], "idle")

    def test_audio_out_of_order_closes_the_connection(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
            ws.send(rp.client_session_start("media", Settings()))
            ws.send(rp.encode_audio(0, 0, np.zeros(1600, dtype=np.int16)))
            ws.send(rp.encode_audio(5, 9999, np.zeros(1600, dtype=np.int16)))
            with self.assertRaises(ConnectionClosed):
                while True:
                    ws.recv(timeout=WAIT)

    def test_audio_before_a_session_closes_the_connection(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
            ws.send(rp.encode_audio(0, 0, np.zeros(1600, dtype=np.int16)))
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=WAIT)

    def test_session_start_cannot_reconfigure_the_server(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
            ws.send(
                json.dumps(
                    {
                        "type": "session.start",
                        "mode": "media",
                        "settings": {"translation": {"base_url": "http://evil/"}},
                    }
                )
            )
            with self.assertRaises(ConnectionClosed):
                ws.recv(timeout=WAIT)

    def test_stop_never_runs_while_the_pipeline_is_starting(self) -> None:
        # Pipeline.stop() 與 start() 不能同時進行（stop 會 join 還沒啟動的執行緒而丟
        # RuntimeError）：停止剛好落在「pipeline 建好 → start()」之間時，B 要等 start 完成
        entered, proceed = threading.Event(), threading.Event()
        overlaps: list[bool] = []
        starting = threading.Event()

        class SlowStart(Pipeline):
            def start(self, source: Any) -> None:
                starting.set()
                entered.set()
                proceed.wait(WAIT)
                try:
                    super().start(source)
                finally:
                    starting.clear()

            def stop(self, timeout: float = 5.0) -> None:
                overlaps.append(starting.is_set())
                super().stop(timeout)

        with (
            mock.patch("kikitori.remote.server.Pipeline", SlowStart),
            self.connect() as ws,
        ):
            self.hello(ws)
            ws.send(rp.client_session_start("media", Settings()))
            self.assertTrue(entered.wait(WAIT))
            ws.send(json.dumps({"type": "session.stop"}))
            threading.Event().wait(0.3)  # 讓 B 開始處理停止
            proceed.set()
            ws.send(json.dumps({"type": "ping", "nonce": 1}))
            while rp.parse_server(ws.recv(timeout=WAIT))["type"] != "pong":
                pass
        self.assertTrue(self.wait_gpu_released())
        self.assertTrue(overlaps, "the pipeline was never stopped")
        self.assertFalse(any(overlaps), "stop() ran while start() was in progress")

    def first_metrics(self) -> dict[str, Any]:
        with self.connect() as ws:
            self.hello(ws)
            ws.send(rp.client_session_start("media", Settings()))
            ws.send(rp.encode_audio(0, 0, np.zeros(1600, dtype=np.int16)))
            while True:
                m = rp.parse_server(ws.recv(timeout=WAIT))
                if m["type"] == "event" and m["name"] == "metrics":
                    ws.send(json.dumps({"type": "session.stop"}))
                    return m["data"]

    def test_metrics_carry_the_load_fields(self) -> None:
        # compute-capacity 一：B 的負荷欄位原樣送回 A；假元件不是 CUDA，所以沒有 gpu
        data = self.first_metrics()
        self.assertIn("final_busy", data)
        self.assertIn("rtf", data)
        self.assertIsNone(data["gpu"])

    def test_metrics_carry_the_gpu_usage_when_b_uses_cuda(self) -> None:
        real = fake_services().build_components

        def on_cuda(*args: Any, **kwargs: Any) -> Any:
            components = real(*args, **kwargs)
            components.final_transcriber.device = "cuda"
            return components

        self.server._build_components = on_cuda
        usage = GpuUsage("NVIDIA GeForce GTX 1080 Ti", 6144, 11264, 37)
        with (
            mock.patch.object(events, "find_nvidia_smi", return_value=Path("smi")),
            mock.patch.object(events, "sample_gpu_usage", return_value=usage),
        ):
            data = self.first_metrics()
        self.assertEqual(
            data["gpu"],
            {
                "name": "NVIDIA GeForce GTX 1080 Ti",
                "vram_used_mb": 6144,
                "vram_total_mb": 11264,
                "util_pct": 37,
            },
        )

    def test_the_gpu_is_released_after_disconnect(self) -> None:
        with self.connect() as ws:
            self.hello(ws)
        self.assertTrue(self.wait_gpu_released())


class ProbeTest(RemoteServerCase):
    """compute-capacity 三：`hello {intent: probe}` 只查能力，不佔 GPU，B 忙碌時也回答。"""

    def probe(self, who: Identity | None = None) -> tuple[dict[str, Any], int | None]:
        """送 probe，回傳 welcome 與 B 關閉連線的 close code。"""
        with self.connect(who) as ws:
            ws.send(rp.client_hello("0.3.1", "probe"))
            welcome = rp.parse_server(ws.recv(timeout=WAIT))
            with self.assertRaises(ConnectionClosed) as ctx:
                ws.recv(timeout=WAIT)
        rcvd = ctx.exception.rcvd
        return welcome, None if rcvd is None else rcvd.code

    def test_probe_answers_the_host_info_and_closes(self) -> None:
        welcome, code = self.probe()
        self.assertEqual(welcome["type"], "welcome")
        self.assertEqual(welcome["host"], HOST_INFO)
        self.assertEqual(code, 1000)
        # 沒有佔 GPU、沒有變成「正在使用這台的電腦」
        self.assertFalse(self.gate.held)
        self.assertIsNone(self.server.owner_spki())
        self.assertIsNone(self.server.peer_state())

    def test_probe_is_answered_while_another_computer_runs_a_session(self) -> None:
        with self.connect() as first:
            self.hello(first)
            first.send(rp.client_session_start("media", Settings()))
            self.assertTrue(self.gate.held)
            welcome, _ = self.probe(self.other)
            self.assertEqual(welcome["host"], HOST_INFO)
            # 正在用的那台不受影響：GPU 仍是它的，連線照常
            self.assertEqual(self.server.owner_spki(), self.client_id.spki)
            first.send(json.dumps({"type": "ping", "nonce": 3}))
            while rp.parse_server(first.recv(timeout=WAIT))["type"] != "pong":
                pass
            # 主動停止（直接斷線的話 B 會保留 session 等續接）
            first.send(json.dumps({"type": "session.stop"}))
        self.assertTrue(self.wait_gpu_released())

    def test_probe_is_answered_while_this_computer_runs_its_own_subtitles(self) -> None:
        self.assertTrue(self.gate.acquire())  # B 自己在跑字幕
        welcome, code = self.probe()
        self.assertEqual((welcome["type"], code), ("welcome", 1000))
        self.assertTrue(self.gate.held)  # 沒有被 probe 放掉

    def test_the_same_computer_probing_does_not_kick_its_session(self) -> None:
        # 同一台電腦在字幕進行中打開設定頁（又 probe 一次）：不能被當成重連而取代舊連線
        with self.connect() as first:
            self.hello(first)
            self.probe()
            first.send(json.dumps({"type": "ping", "nonce": 4}))
            while rp.parse_server(first.recv(timeout=WAIT))["type"] != "pong":
                pass

    def test_session_welcome_also_carries_the_host_info(self) -> None:
        with self.connect() as ws:
            welcome = self.hello(ws)
        self.assertEqual(welcome["host"], HOST_INFO)

    def test_a_failing_capacity_still_welcomes_without_host(self) -> None:
        def broken() -> dict[str, Any]:
            raise RuntimeError("boom")

        self.capacity.host_info = broken  # type: ignore[method-assign]
        with self.assertLogs("kikitori.remote.server", "ERROR"):
            welcome, _ = self.probe()
        self.assertEqual(welcome["type"], "welcome")
        self.assertIsNone(welcome["host"])

    def test_a_probe_with_a_wrong_version_is_incompatible(self) -> None:
        with self.connect() as ws:
            ws.send(
                json.dumps(
                    {
                        "type": "hello",
                        "protocol_version": 99,
                        "app_version": "9.9.9",
                        "intent": "probe",
                    }
                )
            )
            self.assertEqual(
                rp.parse_server(ws.recv(timeout=WAIT))["type"], "incompatible"
            )
        self.assertFalse(self.gate.held)


class AsrModelTest(RemoteServerCase):
    """compute-capacity 三：A 在 `session.start` 指定 B 的 ASR 模型（只能是 B 已下載的）。"""

    def start_with(self, asr: dict[str, Any]) -> list[dict[str, Any]]:
        """送 session.start，收到第一則 status error／running 為止。"""
        messages: list[dict[str, Any]] = []
        with self.connect() as ws:
            self.hello(ws)
            ws.send(
                json.dumps(
                    {"type": "session.start", "mode": "media", "settings": {"asr": asr}}
                )
            )
            while True:
                m = rp.parse_server(ws.recv(timeout=WAIT))
                messages.append(m)
                if m["type"] == "status" and m["state"] in ("running", "error"):
                    break
            ws.send(json.dumps({"type": "session.stop"}))
        return messages

    def test_an_unknown_model_is_refused(self) -> None:
        last = self.start_with({"final_model": "huge-v9"})[-1]
        self.assertEqual((last["state"], last["code"]), ("error", "unknown_model"))
        self.assertTrue(self.wait_gpu_released())

    def test_a_model_this_computer_has_not_downloaded_is_refused(self) -> None:
        # A 不能叫 B 下載：B 的主人自己決定要放哪些模型
        last = self.start_with({"final_model": "large-v3"})[-1]
        self.assertEqual(
            (last["state"], last["code"]), ("error", "model_not_downloaded")
        )
        self.assertIn("large-v3", last["detail"])

    def test_a_downloaded_model_is_used_for_this_session_only(self) -> None:
        loaded: list[Settings] = []
        real = fake_services().build_components

        def record(settings: Settings, *args: Any, **kwargs: Any) -> Any:
            loaded.append(settings)
            return real(settings, *args, **kwargs)

        self.server._build_components = record
        seen: list[tuple[str, ...]] = []
        with self.connect() as ws:
            self.hello(ws)
            ws.send(rp.client_session_start("media", Settings(), final_model="base"))
            while True:
                m = rp.parse_server(ws.recv(timeout=WAIT))
                if m["type"] == "status" and m["state"] == "running":
                    seen.append(self.server.session_models())
                    break
                self.assertNotEqual(m.get("state"), "error", m)
            ws.send(json.dumps({"type": "session.stop"}))
        self.assertEqual(loaded[0].asr.final_model, "base")
        # 裝置與精度仍是 B 的；B 的設定本身不變
        self.assertEqual(loaded[0].asr.device, Settings().asr.device)
        self.assertEqual(seen, [(Settings().asr.fast_model, "base")])
        self.assertTrue(self.wait_gpu_released())
        self.assertEqual(self.server.session_models(), ())


class SourceAddressTest(unittest.TestCase):
    def test_private_and_tailscale_addresses_only(self) -> None:
        for ok in (
            "127.0.0.1",
            "192.168.1.5",
            "10.0.0.2",
            "172.16.4.1",
            "100.88.94.10",
            "fd7a:115c:a1e0::1",
            "fe80::1%eth0",
            "::ffff:192.168.1.5",
        ):
            with self.subTest(ok=ok):
                self.assertTrue(is_allowed_source(ok))
        for bad in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888", "not-an-ip", ""):
            with self.subTest(bad=bad):
                self.assertFalse(is_allowed_source(bad))


if __name__ == "__main__":
    unittest.main()
