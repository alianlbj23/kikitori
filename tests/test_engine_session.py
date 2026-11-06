"""engine 的 session 生命週期（同行程）：session 結束後要釋放它的 pipeline 元件。

真實元件持有 GPU 上的 ASR 模型（每個 session 約 2.6 GB VRAM）；若結束的 session 仍被
參照、只能等循環垃圾回收，同一個引擎行程開幾次 session 就會把顯示記憶體用完，之後的
session 越來越慢（2026-11-05 端到端驗收實測）。這裡以假服務與 weakref 檢查：關掉循環
垃圾回收時，元件在 session 結束後仍會被釋放（只靠參照計數）。
"""

import dataclasses
import gc
import json
import os
import tempfile
import threading
import time
import unittest
import weakref
from typing import Any
from unittest import mock

from kikitori.config import Settings
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import ENDLESS_DEVICE, SPEECH_DEVICE, fake_services
from kikitori.pipeline import PipelineComponents
from kikitori.pipeline.types import Mode

WAIT = 10.0


class _Sink:
    """MessageWriter 的輸出：逐行解析並讓測試等待特定訊息。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._cond = threading.Condition()
        self._buffer = b""

    def write(self, data: bytes) -> int:
        with self._cond:
            self._buffer += data
            *lines, self._buffer = self._buffer.split(b"\n")
            self.messages.extend(json.loads(line) for line in lines if line)
            self._cond.notify_all()
        return len(data)

    def flush(self) -> None:
        pass

    def wait_status(self, session_id: int, state: str) -> None:
        def seen() -> bool:
            return any(
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == state
                for m in self.messages
            )

        with self._cond:
            if not self._cond.wait_for(seen, WAIT):
                raise AssertionError(f"no {state} for session {session_id}")


class SessionReleaseTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        self.built: list[weakref.ref[Any]] = []
        original = services.build_components

        def build(settings: Settings, mode: Mode, **kwargs: Any) -> PipelineComponents:
            components = original(settings, mode, **kwargs)
            self.built.append(weakref.ref(components.final_transcriber))
            return components

        self.sink = _Sink()
        self.server = EngineServer(
            dataclasses.replace(services, build_components=build),
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(self.server.close)
        # 只靠參照計數：循環垃圾回收關掉，避免它碰巧在測試中途回收而掩蓋問題。
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())

    def assert_released(self, index: int) -> None:
        deadline = time.monotonic() + WAIT
        while self.built[index]() is not None:
            if time.monotonic() > deadline:
                referrers = [
                    type(r).__name__ for r in gc.get_referrers(self.built[index]())
                ]
                self.fail(f"components of session {index + 1} still alive: {referrers}")
            time.sleep(0.02)

    def test_stopped_sessions_release_their_components(self) -> None:
        for session_id in (1, 2):
            self.request(
                "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
            )
            self.sink.wait_status(session_id, "running")
            self.request("session.stop")
            self.sink.wait_status(session_id, "idle")
            self.assert_released(session_id - 1)

    def test_session_that_ends_by_itself_releases_its_components(self) -> None:
        self.request("session.start", {"mode": "media", "device_id": SPEECH_DEVICE})
        self.sink.wait_status(1, "idle")
        self.assert_released(0)


class LoadingProgressTest(unittest.TestCase):
    """載入進度：`status loading` 帶 `data: {step, done, total}`；停止的旗標交給建立函式。"""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        self.services = fake_services()
        self.sink = _Sink()
        self._next_id = 0

    def start_server(self, build: Any) -> None:
        self.server = EngineServer(
            dataclasses.replace(self.services, build_components=build),
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(self.server.close)

    def request(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())

    def statuses(self, session_id: int) -> list[dict[str, Any]]:
        return [
            m["data"]
            for m in list(self.sink.messages)
            if m.get("event") == "status" and m["data"]["session_id"] == session_id
        ]

    def test_each_finished_step_is_a_loading_status_with_data(self) -> None:
        self.start_server(self.services.build_components)
        self.request(
            "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
        )
        self.sink.wait_status(1, "running")
        statuses = self.statuses(1)
        # 第一則 loading 與之前完全相同（沒有 data），舊的 UI 不受影響
        self.assertEqual(
            statuses[0],
            {"state": "loading", "detail": None, "session_id": 1, "code": None},
        )
        self.assertEqual(
            [s.get("data") for s in statuses[1:3]],
            [
                {"step": "asr_fast", "done": 1, "total": 2},
                {"step": "asr_final", "done": 2, "total": 2},
            ],
        )
        self.assertEqual([s["state"] for s in statuses], ["loading"] * 3 + ["running"])
        self.request("session.stop")
        self.sink.wait_status(1, "idle")

    def test_stop_sets_the_flag_and_later_progress_is_dropped(self) -> None:
        entered = threading.Event()
        release = threading.Event()
        flags: list[threading.Event] = []
        original = self.services.build_components

        def build(settings: Settings, mode: Mode, **kwargs: Any) -> PipelineComponents:
            flags.append(kwargs["cancelled"])
            entered.set()
            release.wait(WAIT)
            # 載入中被停止：建立函式之後才回報的進度不能再送（session 已送出 idle）
            return original(settings, mode, **kwargs)

        self.start_server(build)
        self.request(
            "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
        )
        self.assertTrue(entered.wait(WAIT))
        self.request("session.stop")
        self.sink.wait_status(1, "idle")
        self.assertTrue(flags[0].is_set())
        release.set()
        loader = self.server._last_loader
        assert loader is not None
        loader.join(WAIT)
        self.assertFalse(loader.is_alive())
        states = [(s["state"], s.get("data")) for s in self.statuses(1)]
        self.assertEqual(states, [("loading", None), ("idle", None)])

    def test_progress_being_sent_when_stopped_comes_before_idle(self) -> None:
        # 送進度與停止同時發生：已通過「還沒結束」檢查的 loading 必須在 idle 之前寫出，
        # 否則 UI 在 idle 之後又看到 loading（2026-11-06 review）。
        sending = threading.Event()
        proceed = threading.Event()
        self.start_server(self.services.build_components)
        send = self.server._send_status

        def slow_send(state: str, *args: Any, **kwargs: Any) -> None:
            data = args[3] if len(args) > 3 else kwargs.get("data")
            if state == "loading" and data is not None and not sending.is_set():
                sending.set()
                proceed.wait(WAIT)
            send(state, *args, **kwargs)

        self.server._send_status = slow_send  # type: ignore[method-assign]
        self.request(
            "session.start", {"mode": "interpret", "device_id": ENDLESS_DEVICE}
        )
        self.assertTrue(sending.wait(WAIT))
        stopper = threading.Thread(target=self.request, args=("session.stop",))
        stopper.start()
        time.sleep(0.2)  # 讓 stop 有機會先送出 idle（修正前就會這樣）
        proceed.set()
        stopper.join(WAIT)
        self.sink.wait_status(1, "idle")
        loader = self.server._last_loader
        assert loader is not None
        loader.join(WAIT)
        states = [s["state"] for s in self.statuses(1)]
        # stop 等寫到一半的 loading 寫完才送 idle（期間載入執行緒可能又送了下一步的進度）
        self.assertGreaterEqual(states.count("loading"), 2, states)
        self.assertEqual(states[-1], "idle", states)
        self.assertEqual(states.count("idle"), 1, states)


if __name__ == "__main__":
    unittest.main()
