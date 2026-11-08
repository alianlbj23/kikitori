"""engine：session 執行中的擷取錯誤要以對應的 `code` 回報（同行程，假服務）。

容器實測（Linux PulseAudio）：錄音中拔掉裝置時 capture 拋 `DeviceLostError`，但引擎送出的
`status error` 是 `code=pipeline_error`，介面因此只顯示「語音處理發生錯誤」，而不是
「音訊裝置中斷了。請確認裝置連線後重新開始。」。
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

import numpy as np
import numpy.typing as npt

from kikitori.capture import (
    AudioSource,
    CaptureError,
    DeviceKind,
    DeviceLostError,
    DeviceNotFoundError,
    PermissionDeniedError,
)
from kikitori.capture.threaded import ThreadedSource
from kikitori.config import Settings
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import fake_services
from kikitori.pipeline import PipelineComponents
from kikitori.pipeline.types import Mode

WAIT = 10.0


class FailingSource:
    """送一點靜音後拋出指定的錯誤（像錄音中被拔掉的裝置）。"""

    def __init__(self, error: Exception) -> None:
        self._error = error
        self._reads = 0

    def start(self) -> None:
        """沒有裝置要開。"""

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        self._reads += 1
        if self._reads > 3:
            raise self._error
        return np.zeros(1600, dtype=np.int16)

    def stop(self) -> None:
        """沒有裝置要關。"""


class _Sink:
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

    def wait_terminal(self, session_id: int) -> dict[str, Any]:
        def terminal() -> dict[str, Any] | None:
            for m in self.messages:
                if (
                    m.get("event") == "status"
                    and m["data"]["session_id"] == session_id
                    and m["data"]["state"] in ("idle", "error")
                ):
                    return m["data"]
            return None

        with self._cond:
            if not self._cond.wait_for(lambda: terminal() is not None, WAIT):
                raise AssertionError(f"session {session_id} did not end")
            result = terminal()
            assert result is not None
            return result


class MidSessionCaptureErrorTest(unittest.TestCase):
    def run_session(self, error: Exception) -> dict[str, Any]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)

        def open_source(device_id: str | None, kind: DeviceKind) -> AudioSource:
            return FailingSource(error)

        services = fake_services()
        original = services.build_components
        self.built: list[weakref.ref[Any]] = []

        def build(settings: Settings, mode: Mode, **kwargs: Any) -> PipelineComponents:
            components = original(settings, mode, **kwargs)
            self.built.append(weakref.ref(components.final_transcriber))
            return components

        sink = _Sink()
        server = EngineServer(
            dataclasses.replace(
                services, open_source=open_source, build_components=build
            ),
            MessageWriter(sink),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(server.close)
        with self.assertLogs("kikitori", "ERROR"):
            server.handle_line(
                json.dumps(
                    {"id": 1, "method": "session.start", "params": {"mode": "media"}}
                ).encode()
            )
            return sink.wait_terminal(1)

    def test_device_lost_while_running(self) -> None:
        status = self.run_session(DeviceLostError("audio device disappeared: usb"))
        self.assertEqual(status["state"], "error")
        self.assertEqual(status["code"], "device_lost")
        self.assertIn("audio device disappeared", status["detail"])

    def test_other_capture_error_while_running(self) -> None:
        status = self.run_session(CaptureError("stream broke"))
        self.assertEqual(status["code"], "capture_error")

    def test_permission_revoked_while_running(self) -> None:
        status = self.run_session(PermissionDeniedError("screen recording revoked"))
        self.assertEqual(status["code"], "permission_denied")

    def test_non_capture_error_is_pipeline_error(self) -> None:
        status = self.run_session(RuntimeError("boom"))
        self.assertEqual(status["code"], "pipeline_error")

    def test_failed_session_releases_its_components(self) -> None:
        # 以 error 結束的 session 也要只靠參照計數就釋放元件（GPU 上的 ASR 模型）：
        # 保存的例外不能經由 traceback 參照回 pipeline。
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)
        self.run_session(DeviceLostError("audio device disappeared: usb"))
        deadline = time.monotonic() + WAIT
        while self.built[0]() is not None:
            if time.monotonic() > deadline:
                referrers = [
                    type(r).__name__ for r in gc.get_referrers(self.built[0]())
                ]
                self.fail(f"components of the failed session still alive: {referrers}")
            time.sleep(0.02)


def _missing_device() -> Any:
    raise DeviceNotFoundError("loopback device not found: 999999")


class StartFailureReleasesComponentsTest(unittest.TestCase):
    """開裝置失敗（`pipeline.start()` 丟出）的 session 也要只靠參照計數就釋放元件。

    memory_soak #1：`ThreadedSource.start()` 重新丟出存起來的同一個例外物件，traceback
    接上 `Pipeline.start` 的 frame，形成 source → 例外 → frame → pipeline → 元件的循環，
    整組模型（約 3 GB 顯示記憶體）留到循環垃圾回收（引擎裡幾乎不會發生）。
    """

    def test_components_are_freed_without_gc(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        original = services.build_components
        built: list[weakref.ref[Any]] = []

        def build(settings: Settings, mode: Mode, **kwargs: Any) -> PipelineComponents:
            components = original(settings, mode, **kwargs)
            built.append(weakref.ref(components.fast_transcriber))
            return components

        def open_source(device_id: str | None, kind: DeviceKind) -> AudioSource:
            return ThreadedSource(_missing_device)

        sink = _Sink()
        server = EngineServer(
            dataclasses.replace(
                services, open_source=open_source, build_components=build
            ),
            MessageWriter(sink),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(server.close)
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)
        with self.assertLogs("kikitori", "ERROR"):
            server.handle_line(
                json.dumps(
                    {"id": 1, "method": "session.start", "params": {"mode": "media"}}
                ).encode()
            )
            status = sink.wait_terminal(1)
        self.assertEqual(status["code"], "device_not_found")
        deadline = time.monotonic() + WAIT
        while built[0]() is not None:
            if time.monotonic() > deadline:
                referrers = [type(r).__name__ for r in gc.get_referrers(built[0]())]
                self.fail(f"components of the failed session still alive: {referrers}")
            time.sleep(0.02)


if __name__ == "__main__":
    unittest.main()
