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
from collections.abc import Mapping
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.config import Settings
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import ENDLESS_DEVICE, SPEECH_DEVICE, fake_services
from kikitori.remote.identity import IdentityError, load_or_create_identity
from kikitori.remote.manage import HostManageError, ProgressFn
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


HOST_INFO: dict[str, Any] = {
    "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
    "device": "cuda",
    "compute_type": "int8_float16",
    "current": {
        "fast": "base",
        "final": "small",
        "translation": {"fast": "qwen3.5:4b", "final": "qwen3.5:4b"},
    },
    "recommended": {"fast": "large-v3-turbo", "final": "large-v3"},
    "models": [
        {
            "id": "base",
            "downloaded": True,
            "need_mb": 450,
            "fit": "ok",
            "fit_as_fast": "ok",
        },
        {
            "id": "small",
            "downloaded": True,
            "need_mb": 650,
            "fit": "over",
            "fit_as_fast": "over",
        },
        {
            "id": "large-v3",
            "downloaded": False,
            "need_mb": 2140,
            "fit": "tight",
            "fit_as_fast": None,
        },
    ],
    "translation_model": "qwen3.5:4b",
    "translation_models": ["qwen3.5:4b", "qwen3.5:9b"],
}


class FakeCapacity:
    """B 的能力（`remote.capacity.HostCapacitySource`）：固定的 HostInfo 與已下載的模型。"""

    def host_info(self, *, detect: bool = True) -> dict[str, Any]:
        return HOST_INFO

    def downloaded_models(self) -> frozenset[str]:
        return frozenset({"base", "small"})

    def invalidate(self) -> None:
        pass


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
        self.manager = FakeManager()
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
            capacity=FakeCapacity(),
            manager=self.manager,
            progress_interval=0.1,
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

    def request(self, method: str, params: dict[str, Any] | None = None) -> int:
        self.next_id += 1
        message: dict[str, Any] = {"id": self.next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.engine.handle_line(json.dumps(message).encode())
        return self.next_id

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """送一個請求並等它的回應（`result` 或 `error`）。"""
        request_id = self.request(method, params)
        found: list[dict[str, Any]] = []

        def seen() -> bool:
            found[:] = [m for m in self.sink.messages if m.get("id") == request_id]
            return bool(found)

        with self.sink.cond:
            if not self.sink.cond.wait_for(seen, WAIT):
                raise AssertionError(f"no reply to {method}")
        return found[0]

    def error_code(self, method: str, params: dict[str, Any] | None = None) -> str:
        reply = self.call(method, params)
        self.assertIn("error", reply, reply)
        return str(reply["error"]["code"])

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


class HostInfoTest(EngineRemoteCase):
    """compute-capacity 四：`remote.host_info` 連上設定的運算主機查它的能力。"""

    def test_host_info_returns_the_compute_hosts_capabilities(self) -> None:
        reply = self.call("remote.host_info", {})
        self.assertEqual(reply.get("result"), {"host": HOST_INFO}, reply)
        # 只是查詢：沒有佔 B 的 GPU、沒有開始任何 session
        self.assertFalse(self.gate.held)
        self.assertIsNone(self.remote.owner_spki())
        self.assertEqual(self.sink.events("status"), [])

    def test_host_info_is_answered_while_the_host_is_busy(self) -> None:
        self.assertTrue(self.gate.acquire())  # B 正在替別台或自己跑字幕
        self.assertEqual(self.call("remote.host_info")["result"], {"host": HOST_INFO})

    def test_host_info_errors(self) -> None:
        self.assertEqual(
            self.error_code("remote.host_info", {"x": 1}), "invalid_params"
        )
        self.b_trust.remove(load_or_create_identity(compute_dir()).spki)
        self.assertEqual(self.error_code("remote.host_info"), "remote_not_paired")
        port = self.remote.port
        self.remote.stop()
        self.services.settings_store.update({"compute": {"port": port}})
        self.assertEqual(self.error_code("remote.host_info"), "remote_unreachable")
        self.services.settings_store.update({"compute": {"host": ""}})
        self.assertEqual(self.error_code("remote.host_info"), "remote_not_configured")

    def test_an_unreadable_identity_is_reported(self) -> None:
        with mock.patch(
            "kikitori.engine.remote_service.load_or_create_identity",
            side_effect=IdentityError("damaged key"),
        ):
            code = self.error_code("remote.host_info")
        self.assertEqual(code, "remote_identity_error")


class OwnModelsTest(EngineRemoteCase):
    """compute-capacity 七：遠端 session 用的一律是運算主機設定裡的模型。"""

    def test_the_session_runs_the_compute_hosts_own_models(self) -> None:
        self.request("session.start", {"mode": "media", "device_id": ENDLESS_DEVICE})
        self.sink.wait_status(1, "running")
        own = Settings().asr  # B 的設定（這裡的 RemoteServer 用預設值）
        self.assertEqual(
            self.remote.session_models(), (own.fast_model, own.final_model)
        )
        self.request("session.stop")
        self.sink.wait_status(1, "idle")


class HostSetModelsTest(EngineRemoteCase):
    """compute-capacity 七：`remote.host_set_models` 改運算主機的模型（持久、下一場生效）。"""

    def test_set_models_answers_the_new_host_info(self) -> None:
        reply = self.call(
            "remote.host_set_models",
            {
                "asr": {"final_model": "base"},
                "translation": {"fast_model": "qwen3.5:9b"},
            },
        )
        self.assertEqual(reply.get("result"), {"host": HOST_INFO}, reply)
        self.assertEqual(
            self.manager.calls,
            [("A", {"final_model": "base"}, {"fast_model": "qwen3.5:9b"})],
        )
        self.assertFalse(self.gate.held)  # 不佔運算主機的 GPU

    def test_an_over_budget_model_can_still_be_chosen(self) -> None:
        # 使用者補充：超出負荷要警告（A 的介面），但執意要選時仍然可以
        self.assertEqual(
            next(m["fit"] for m in HOST_INFO["models"] if m["id"] == "small"), "over"
        )
        reply = self.call("remote.host_set_models", {"asr": {"final_model": "small"}})
        self.assertIn("result", reply, reply)
        self.assertEqual(self.manager.calls, [("A", {"final_model": "small"}, {})])

    def test_works_while_the_compute_host_is_busy(self) -> None:
        self.assertTrue(self.gate.acquire())
        reply = self.call("remote.host_set_models", {"asr": {"final_model": "base"}})
        self.assertIn("result", reply, reply)

    def test_errors(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({}, "invalid_params"),
            ({"asr": {}}, "invalid_params"),
            ({"asr": {"device": "cuda"}}, "invalid_params"),
            ({"asr": {"final_model": "../x"}}, "invalid_params"),
            ({"translation": {"final_model": " q"}}, "invalid_params"),
            ({"asr": {"final_model": "base"}, "x": 1}, "invalid_params"),
            ({"asr": {"final_model": "huge-v9"}}, "remote_unknown_model"),
            ({"asr": {"final_model": "large-v3"}}, "remote_model_not_downloaded"),
            (
                {"translation": {"final_model": "llama3:70b"}},
                "remote_translation_model_missing",
            ),
        ]
        for params, code in cases:
            with self.subTest(params=params):
                self.assertEqual(
                    self.error_code("remote.host_set_models", params), code
                )
        good = {"asr": {"final_model": "base"}}
        self.manager.fail = HostManageError("settings_error", "disk full")
        self.assertEqual(
            self.error_code("remote.host_set_models", good), "remote_error"
        )
        self.assertEqual(self.manager.calls, [])
        port = self.remote.port
        self.remote.stop()
        self.services.settings_store.update({"compute": {"port": port}})
        self.assertEqual(
            self.error_code("remote.host_set_models", good), "remote_unreachable"
        )
        self.services.settings_store.update({"compute": {"host": ""}})
        self.assertEqual(
            self.error_code("remote.host_set_models", good), "remote_not_configured"
        )


class HostDownloadTest(EngineRemoteCase):
    """compute-capacity 七：`remote.host_download` 與 `remote` 事件 `host_download`。"""

    def host_downloads(self) -> list[dict[str, Any]]:
        return [d for d in self.sink.events("remote") if d["kind"] == "host_download"]

    def wait_for(self, condition: Any) -> None:
        with self.sink.cond:
            if not self.sink.cond.wait_for(condition, WAIT):
                raise AssertionError("condition not met")

    def test_progress_events_until_done(self) -> None:
        reply = self.call("remote.host_download", {"model": "large-v3"})
        self.assertEqual(
            reply.get("result"), {"model": "large-v3", "started": True}, reply
        )
        report = self.manager.progress["large-v3"]
        report(40, 100, "downloading", None)
        self.wait_for(
            lambda: any(d["downloaded_bytes"] == 40 for d in self.host_downloads())
        )
        report(100, 100, "done", None)
        self.wait_for(lambda: any(d["state"] == "done" for d in self.host_downloads()))
        self.assertEqual(
            self.host_downloads()[-1],
            {
                "kind": "host_download",
                "model": "large-v3",
                "downloaded_bytes": 100,
                "total_bytes": 100,
                "state": "done",
                "error": None,
            },
        )
        # 介面一定先拿到回應，之後才是進度
        first = next(
            m
            for m in self.sink.messages
            if m.get("id") == self.next_id
            or (m.get("event") == "remote" and m["data"]["kind"] == "host_download")
        )
        self.assertEqual(first.get("id"), self.next_id)

    def test_errors(self) -> None:
        cases: list[tuple[dict[str, Any], str]] = [
            ({}, "invalid_params"),
            ({"model": "../x"}, "invalid_params"),
            ({"model": "base", "url": "x"}, "invalid_params"),
            ({"model": "huge-v9"}, "remote_unknown_model"),
        ]
        for params, code in cases:
            with self.subTest(params=params):
                self.assertEqual(self.error_code("remote.host_download", params), code)
        self.manager.fail = HostManageError("download_in_progress", "already")
        self.assertEqual(
            self.error_code("remote.host_download", {"model": "large-v3"}),
            "remote_download_in_progress",
        )
        self.services.settings_store.update({"compute": {"host": ""}})
        self.assertEqual(
            self.error_code("remote.host_download", {"model": "large-v3"}),
            "remote_not_configured",
        )

    def test_a_silent_compute_host_ends_with_an_error_event(self) -> None:
        # B 很久才送一則進度：超過 A 的容忍時間就以 error 結束（B 的下載不受影響）
        self.remote._progress_interval = 30.0
        with mock.patch("kikitori.remote.client.DOWNLOAD_SILENCE_SECS", 0.5):
            self.call("remote.host_download", {"model": "large-v3"})
            self.wait_for(
                lambda: any(d["state"] == "error" for d in self.host_downloads())
            )
        self.assertIn("lost contact", self.host_downloads()[-1]["error"])
        self.manager.progress["large-v3"](100, 100, "done", None)  # 讓 B 的轉送結束


if __name__ == "__main__":
    unittest.main()
