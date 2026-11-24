"""遠端運算的 A 端客戶端：真的 TLS 連到 127.0.0.1 上的運算主機（假的 pipeline 元件）。

涵蓋 docs/specs/remote-compute.md 的「一場遠端字幕的時序」與 A 端的錯誤碼：正常的一場（字幕
欄位與本機相同、樣本位置是 A 的時間軸）、沒配對／主機憑證不符／被移除配對、忙碌、連不上、
使用者停止（之後不再有回呼）、運算主機消失（`remote_lost`），以及 B 送來不合格字幕時的過濾。
"""

import os
import socket
import tempfile
import threading
import time
import unittest
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.capture import ArraySource, CaptureError
from kikitori.config import Settings
from kikitori.engine.testing import EndlessSource, _speech_track, fake_services
from kikitori.remote import protocol as rp
from kikitori.remote.client import (
    HostDownload,
    RemoteClientSession,
    RemoteError,
    _check_metrics,
    probe_host_info,
    set_host_models,
)
from kikitori.remote.identity import Identity, load_or_create_identity
from kikitori.remote.manage import HostManageError, ProgressFn
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

    def start_server(self, **kwargs: Any) -> RemoteServer:
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
            **kwargs,
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


GOOD_METRICS: dict[str, Any] = {
    "level_dbfs": -20.0,
    "speech_level_dbfs": None,
    "background_level_dbfs": -50.0,
    "backlog": 1,
    "latency_ms_median": 900,
    "final_busy": 0.45,
    "rtf": 0.3,
    "gpu": {
        "name": "NVIDIA GeForce GTX 1080 Ti",
        "vram_used_mb": 6246,
        "vram_total_mb": 11264,
        "util_pct": 37,
    },
    "session_id": 99,
}


class MetricsTest(unittest.TestCase):
    """compute-capacity 一：B 的負荷欄位逐欄驗證；不合格的那一欄變 null，其餘照常顯示。"""

    def check(self, **overrides: Any) -> dict[str, Any] | None:
        data = dict(GOOD_METRICS)
        data.update(overrides)
        return _check_metrics(data)

    def test_good_load_fields_pass_and_unknown_fields_are_dropped(self) -> None:
        out = self.check(injected="<img onerror=x>")
        assert out is not None
        self.assertEqual(out["final_busy"], 0.45)
        self.assertEqual(out["rtf"], 0.3)
        self.assertEqual(out["gpu"], GOOD_METRICS["gpu"])
        self.assertNotIn("injected", out)
        self.assertNotIn("session_id", out)

    def test_old_compute_hosts_without_load_fields(self) -> None:
        data = {
            k: v
            for k, v in GOOD_METRICS.items()
            if k not in ("final_busy", "rtf", "gpu")
        }
        out = _check_metrics(data)
        assert out is not None
        self.assertEqual(
            (out["final_busy"], out["rtf"], out["gpu"]), (None, None, None)
        )

    def test_a_bad_load_field_becomes_null_only(self) -> None:
        gpu = GOOD_METRICS["gpu"]
        for field, value in (
            ("final_busy", 1.5),
            ("final_busy", -0.1),
            ("final_busy", "0.5"),
            ("final_busy", True),
            ("rtf", -1),
            ("rtf", 1000.5),
            ("rtf", float("inf")),
            ("gpu", "GTX"),
            ("gpu", dict(gpu, name="x" * 65)),
            ("gpu", dict(gpu, name="")),
            ("gpu", dict(gpu, name=5)),
            ("gpu", dict(gpu, vram_used_mb=-1)),
            ("gpu", dict(gpu, vram_total_mb=1_048_577)),
            ("gpu", dict(gpu, vram_used_mb=20000)),  # 比總量還多
            ("gpu", dict(gpu, util_pct=101)),
            ("gpu", dict(gpu, util_pct=None)),
            ("gpu", {k: v for k, v in gpu.items() if k != "name"}),
        ):
            with self.subTest(field=field, value=str(value)[:40]):
                out = self.check(**{field: value})
                assert out is not None, "the whole metrics event was dropped"
                self.assertIsNone(out[field])
                self.assertEqual(out["backlog"], 1)  # 其他欄位照常
                self.assertEqual(out["level_dbfs"], -20.0)

    def test_a_bad_existing_field_still_drops_the_event(self) -> None:
        self.assertIsNone(self.check(backlog=-1))
        self.assertIsNone(self.check(level_dbfs="loud"))


HOST_INFO: dict[str, Any] = {
    "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
    "device": "cuda",
    "compute_type": "int8_float16",
    "current": {
        "fast": "base",
        "final": "small",
        "translation": {"fast": "qwen3.5:4b", "final": "qwen3.5:4b"},
    },
    "recommended": None,
    "models": [
        {
            "id": "base",
            "downloaded": True,
            "need_mb": 450,
            "fit": "ok",
            "fit_as_fast": "tight",
        }
    ],
    "translation_model": "qwen3.5:4b",
    "translation_models": ["qwen3.5:4b"],
}


class FakeCapacity:
    """B 的能力（`remote.capacity.HostCapacitySource`）：固定的 HostInfo 與已下載的模型。"""

    def __init__(self) -> None:
        self.info: dict[str, Any] = HOST_INFO

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        return self.info

    def downloaded_models(self) -> frozenset[str]:
        return frozenset({"base", "small"})

    def invalidate(self) -> None:
        pass


class ProbeTest(RemoteClientCase):
    """compute-capacity 三、四：`probe_host_info` 只查 B 的能力，不開始字幕、不佔 GPU。"""

    def setUp(self) -> None:
        super().setUp()
        self.capacity = FakeCapacity()
        self.server.stop()
        self.server = self.start_server(capacity=self.capacity)

    def probe(self, **kwargs: Any) -> dict[str, Any] | None:
        options: dict[str, Any] = {
            "identity": self.client_id,
            "servers": self.servers,
            "host": "127.0.0.1",
            "app_version": "0.3.1",
        }
        options.update(kwargs)
        if "port" not in options:
            options["port"] = self.server.port
        return probe_host_info(**options)

    def test_probe_returns_the_validated_host_info(self) -> None:
        self.assertEqual(self.probe(), HOST_INFO)
        self.assertFalse(self.gate.held)
        self.assertIsNone(self.server.owner_spki())

    def test_probe_works_while_the_host_is_busy(self) -> None:
        self.assertTrue(self.gate.acquire())
        self.assertEqual(self.probe(), HOST_INFO)

    def test_a_malicious_host_info_is_null(self) -> None:
        self.capacity.info = dict(HOST_INFO, device="<script>")
        with self.assertLogs("kikitori.remote.protocol", "WARNING"):
            self.assertIsNone(self.probe())

    def test_probe_errors(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.probe(host="")
        self.assertEqual(ctx.exception.code, "remote_not_configured")
        empty = TrustStore(self.root / "a" / "none.json")
        with self.assertRaises(RemoteError) as ctx:
            self.probe(servers=empty)
        self.assertEqual(ctx.exception.code, "remote_not_paired")
        self.trust.remove(self.client_id.spki)
        with self.assertRaises(RemoteError) as ctx:
            self.probe()
        self.assertEqual(ctx.exception.code, "remote_not_paired")
        port = self.server.port
        self.server.stop()
        with self.assertRaises(RemoteError) as ctx:
            self.probe(port=port)
        self.assertEqual(ctx.exception.code, "remote_unreachable")

    def test_probe_is_bounded_by_its_timeout(self) -> None:
        # 只接受 TCP、什麼都不回的主機（TLS 握手卡住）：不會等超過 timeout
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen()
            began = time.monotonic()
            with self.assertRaises(RemoteError):
                self.probe(port=silent.getsockname()[1], timeout=1.0)
            self.assertLess(time.monotonic() - began, 4.0)

    def test_an_old_compute_host_that_rejects_probe_is_incompatible(self) -> None:
        # 舊版 B 不認得 hello 的 intent：以 1008 protocol violation 關閉
        real = rp.parse_client

        def old(text: str) -> Any:
            if '"intent"' in text:
                raise rp.ProtocolViolation("hello: unknown field 'intent'")
            return real(text)

        with (
            mock.patch.object(rp, "parse_client", old),
            self.assertRaises(RemoteError) as ctx,
        ):
            self.probe()
        self.assertEqual(ctx.exception.code, "remote_incompatible")


class FakeManager:
    """B 的宿主（`remote.manage.HostManager`）：記下要求；下載進度由測試回報。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, str], dict[str, str]]] = []
        self.progress: dict[str, ProgressFn] = {}
        self.fail: HostManageError | None = None

    def set_models(
        self, by: str, asr: Mapping[str, str], translation: Mapping[str, str]
    ) -> None:
        if self.fail is not None:
            raise self.fail
        self.calls.append((by, dict(asr), dict(translation)))

    def start_download(self, by: str, model: str, progress: ProgressFn) -> None:
        if self.fail is not None:
            raise self.fail
        self.progress[model] = progress


class ManageHostTest(RemoteClientCase):
    """compute-capacity 七：A 改 B 的模型、請 B 下載（錯誤碼對應、逾時、斷線）。"""

    def setUp(self) -> None:
        super().setUp()
        self.manager = FakeManager()
        self.server.stop()
        self.server = self.start_server(
            capacity=FakeCapacity(), manager=self.manager, progress_interval=0.1
        )

    def options(self, **kwargs: Any) -> dict[str, Any]:
        options: dict[str, Any] = {
            "identity": self.client_id,
            "servers": self.servers,
            "host": "127.0.0.1",
            "port": self.server.port,
            "app_version": "0.3.1",
        }
        options.update(kwargs)
        return options

    def set_models(
        self, asr: dict[str, str], translation: dict[str, str], **kwargs: Any
    ) -> dict[str, Any] | None:
        return set_host_models(
            asr=asr, translation=translation, **self.options(**kwargs)
        )

    def test_set_models_returns_the_new_host_info(self) -> None:
        host = self.set_models({"final_model": "small"}, {"final_model": "qwen3.5:4b"})
        self.assertEqual(host, HOST_INFO)
        self.assertEqual(
            self.manager.calls,
            [("A", {"final_model": "small"}, {"final_model": "qwen3.5:4b"})],
        )
        self.assertFalse(self.gate.held)

    def test_set_models_error_codes(self) -> None:
        for asr, translation, code in (
            ({"final_model": "huge-v9"}, {}, "remote_unknown_model"),
            ({"final_model": "large-v3"}, {}, "remote_model_not_downloaded"),
            ({}, {"final_model": "llama3:70b"}, "remote_translation_model_missing"),
        ):
            with self.subTest(code=code), self.assertRaises(RemoteError) as ctx:
                self.set_models(asr, translation)
            self.assertEqual(ctx.exception.code, code)
        self.manager.fail = HostManageError("settings_error", "disk full")
        with self.assertRaises(RemoteError) as ctx:
            self.set_models({"final_model": "base"}, {})
        self.assertEqual(ctx.exception.code, "remote_error")
        self.assertIn("disk full", str(ctx.exception))
        with self.assertRaises(RemoteError) as ctx:
            self.set_models({"final_model": "base"}, {}, host="")
        self.assertEqual(ctx.exception.code, "remote_not_configured")

    def test_an_old_host_that_rejects_manage_is_incompatible(self) -> None:
        real = rp.parse_client

        def old(text: str) -> Any:
            if '"manage"' in text:
                raise rp.ProtocolViolation("intent must be one of ['session', 'probe']")
            return real(text)

        with (
            mock.patch.object(rp, "parse_client", old),
            self.assertRaises(RemoteError) as ctx,
        ):
            self.set_models({"final_model": "base"}, {})
        self.assertEqual(ctx.exception.code, "remote_incompatible")

    def test_set_models_is_bounded_by_its_timeout(self) -> None:
        with socket.socket() as silent:
            silent.bind(("127.0.0.1", 0))
            silent.listen()
            began = time.monotonic()
            with self.assertRaises(RemoteError):
                self.set_models(
                    {"final_model": "base"},
                    {},
                    port=silent.getsockname()[1],
                    timeout=1.0,
                )
            self.assertLess(time.monotonic() - began, 4.0)

    def download(self, model: str = "large-v3", **kwargs: Any) -> HostDownload:
        download = HostDownload(model=model, **self.options(**kwargs))
        self.addCleanup(download.cancel)
        return download

    def test_download_progress_until_done(self) -> None:
        download = self.download()
        download.start()
        seen: list[dict[str, Any]] = []
        follower = threading.Thread(target=download.follow, args=(seen.append,))
        follower.start()
        self.assertTrue(
            RecorderLike.wait_for(lambda: "large-v3" in self.manager.progress)
        )
        report = self.manager.progress["large-v3"]
        report(30, 100, "downloading", None)
        self.assertTrue(
            RecorderLike.wait_for(
                lambda: any(p["downloaded_bytes"] == 30 for p in list(seen))
            )
        )
        report(100, 100, "done", None)
        follower.join(WAIT)
        self.assertFalse(follower.is_alive())
        self.assertEqual(
            seen[-1],
            {
                "model": "large-v3",
                "downloaded_bytes": 100,
                "total_bytes": 100,
                "state": "done",
                "error": None,
            },
        )
        self.assertTrue(all(p["state"] == "downloading" for p in seen[:-1]))

    def test_download_errors(self) -> None:
        with self.assertRaises(RemoteError) as ctx:
            self.download("huge-v9").start()
        self.assertEqual(ctx.exception.code, "remote_unknown_model")
        self.manager.fail = HostManageError("download_in_progress", "already")
        with self.assertRaises(RemoteError) as ctx:
            self.download().start()
        self.assertEqual(ctx.exception.code, "remote_download_in_progress")

    def test_a_silent_host_ends_the_download_with_an_error(self) -> None:
        # B 每秒送一則；這裡讓它很久才送（間隔大於 A 的容忍時間）
        self.server.stop()
        self.server = self.start_server(
            capacity=FakeCapacity(), manager=self.manager, progress_interval=30.0
        )
        download = self.download(silence=0.5)
        download.start()
        seen: list[dict[str, Any]] = []
        download.follow(seen.append)
        self.assertEqual(len(seen), 1)
        self.assertEqual((seen[0]["state"], seen[0]["model"]), ("error", "large-v3"))
        self.assertIn("lost contact", seen[0]["error"])
        # A 放棄不影響 B 的下載：B 的宿主照樣能回報到結束
        self.manager.progress["large-v3"](100, 100, "done", None)


class RecorderLike:
    """沒有 Recorder 的測試也要「等到條件成立」。"""

    @staticmethod
    def wait_for(condition: Any) -> bool:
        for _ in range(int(WAIT / 0.02)):
            if condition():
                return True
            threading.Event().wait(0.02)
        return False


if __name__ == "__main__":
    unittest.main()
