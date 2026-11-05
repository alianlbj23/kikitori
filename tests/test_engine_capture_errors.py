"""engine：session 執行中的擷取錯誤要以對應的 `code` 回報（同行程，假服務）。

容器實測（Linux PulseAudio）：錄音中拔掉裝置時 capture 拋 `DeviceLostError`，但引擎送出的
`status error` 是 `code=pipeline_error`，介面因此只顯示「語音處理發生錯誤」，而不是
「音訊裝置中斷了。請確認裝置連線後重新開始。」。
"""

import dataclasses
import json
import os
import tempfile
import threading
import unittest
from typing import Any
from unittest import mock

import numpy as np
import numpy.typing as npt

from kikitori.capture import AudioSource, CaptureError, DeviceKind, DeviceLostError
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import fake_services

WAIT = 10.0


class FailingSource:
    """送一點靜音後拋出指定的擷取錯誤（像錄音中被拔掉的裝置）。"""

    def __init__(self, error: CaptureError) -> None:
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
    def run_session(self, error: CaptureError) -> dict[str, Any]:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)

        def open_source(device_id: str | None, kind: DeviceKind) -> AudioSource:
            return FailingSource(error)

        sink = _Sink()
        server = EngineServer(
            dataclasses.replace(fake_services(), open_source=open_source),
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


if __name__ == "__main__":
    unittest.main()
