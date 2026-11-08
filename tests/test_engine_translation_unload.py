"""引擎結束時請本機 Ollama 卸載這次用過的翻譯模型（釋放顯示記憶體）。

Ollama 預設閒置 5 分鐘才卸載；使用者關掉 kikitori 後顯示記憶體不會馬上回來。
引擎只卸載自己在 session 用過的模型，從沒開始 session 或翻譯服務在遠端時什麼都不送。
"""

import dataclasses
import json
import logging
import os
import tempfile
import threading
import time
import unittest
from typing import Any
from unittest import mock

from kikitori.engine import server as server_module
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.testing import SPEECH_DEVICE, fake_services
from kikitori.hardware.ollama import OllamaUnreachableError, UnloadError

WAIT = 10.0


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

    def wait_idle(self, session_id: int) -> None:
        def seen() -> bool:
            return any(
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == "idle"
                for m in self.messages
            )

        with self._cond:
            if not self._cond.wait_for(seen, WAIT):
                raise AssertionError(f"session {session_id} did not end")

    def response(self, request_id: int) -> dict[str, Any]:
        for m in self.messages:
            if m.get("id") == request_id:
                return m
        raise AssertionError(f"no response to {request_id}")


class _Recorder:
    """`TranslationModels.unload` 的替身：記錄呼叫，可指定要丟的例外或要花的時間。"""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str, str, float]] = []
        self.error: Exception | None = None
        self.delay = 0.0

    def __call__(
        self, base_url: str, api_key_env: str, model: str, timeout: float
    ) -> None:
        self.calls.append((base_url, api_key_env, model, timeout))
        if self.delay:
            time.sleep(min(self.delay, timeout))
            raise OllamaUnreachableError("timed out")
        if self.error is not None:
            raise self.error


class TranslationUnloadTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        self.unload = _Recorder()
        translation = services.translation_models
        # 兩條路徑用不同模型的情境需要第二個「已安裝」的模型（假伺服器預設只有 4b）
        translation._installed.append("qwen3.5:9b")  # type: ignore[attr-defined]
        patcher = mock.patch.object(translation, "unload", self.unload, create=True)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sink = _Sink()
        self.server = EngineServer(
            dataclasses.replace(services, translation_models=translation),
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(self.server.close)
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> int:
        self._next_id += 1
        message: dict[str, Any] = {"id": self._next_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())
        return self._next_id

    def run_session(self, session_id: int = 1) -> None:
        # SPEECH_DEVICE 的音訊播完 session 就自己結束
        self.request("session.start", {"mode": "media", "device_id": SPEECH_DEVICE})
        self.sink.wait_idle(session_id)

    def set_translation(self, **fields: Any) -> None:
        rid = self.request("settings.set", {"translation": fields})
        self.assertIn("result", self.sink.response(rid))

    def test_nothing_is_unloaded_without_a_session(self) -> None:
        self.server.close()
        self.assertEqual(self.unload.calls, [])

    def test_models_used_by_a_session_are_unloaded_once_on_close(self) -> None:
        self.set_translation(fast_model="qwen3.5:4b", final_model="qwen3.5:9b")
        self.run_session(1)
        self.run_session(2)
        self.server.close()
        self.assertEqual(
            sorted(model for _, _, model, _ in self.unload.calls),
            ["qwen3.5:4b", "qwen3.5:9b"],
        )
        base_url, api_key_env, _, timeout = self.unload.calls[0]
        self.assertEqual(base_url, "http://localhost:11434/v1")
        # 金鑰本身不經過這裡，只傳環境變數的名稱（設定的預設值）
        self.assertEqual(api_key_env, "KIKITORI_TRANSLATE_API_KEY")
        self.assertGreater(timeout, 0)
        # close() 可以重複呼叫；已卸載的不再送
        self.server.close()
        self.assertEqual(len(self.unload.calls), 2)

    def test_the_same_model_for_both_paths_is_unloaded_once(self) -> None:
        self.set_translation(fast_model="qwen3.5:4b", final_model="qwen3.5:4b")
        self.run_session()
        self.server.close()
        self.assertEqual([c[2] for c in self.unload.calls], ["qwen3.5:4b"])

    def test_remote_servers_are_left_alone(self) -> None:
        # 遠端（可能付費、也可能是別人的）服務：不送任何多餘的請求
        self.set_translation(base_url="https://api.example.com/v1")
        self.run_session()
        self.server.close()
        self.assertEqual(self.unload.calls, [])

    def test_a_failed_unload_is_logged_and_does_not_stop_the_others(self) -> None:
        self.set_translation(fast_model="qwen3.5:4b", final_model="qwen3.5:9b")
        self.run_session()
        self.unload.error = OllamaUnreachableError("cannot connect to localhost")
        with self.assertLogs("kikitori.engine.server", "WARNING") as logs:
            self.server.close()
        self.assertEqual(len(self.unload.calls), 2)
        self.assertIn("cannot connect to localhost", "\n".join(logs.output))

    def test_a_server_that_declines_is_not_a_warning(self) -> None:
        # 模型沒安裝（Ollama 回 404），或本機的翻譯服務不是 Ollama：每次關閉都會這樣，
        # 不是故障，不該在日誌留 warning
        self.run_session()
        self.unload.error = UnloadError("HTTP 404 from /api/generate")
        with self.assertLogs("kikitori.engine.server", "INFO") as logs:
            self.server.close()
        self.assertIn("HTTP 404", "\n".join(logs.output))
        self.assertEqual(
            [r.getMessage() for r in logs.records if r.levelno >= logging.WARNING], []
        )

    def test_an_unexpected_error_does_not_stop_the_rest_of_close(self) -> None:
        self.set_translation(fast_model="qwen3.5:4b", final_model="qwen3.5:9b")
        self.run_session()
        self.unload.error = RuntimeError("bug in a translation backend")
        released = mock.Mock()
        with (
            mock.patch.object(
                self.server,
                "_services",
                dataclasses.replace(self.server._services, release_models=released),
            ),
            self.assertLogs("kikitori.engine.server", "ERROR"),
        ):
            self.server.close()
        self.assertEqual(len(self.unload.calls), 2)
        released.assert_called_once()

    def test_unloading_stays_within_its_budget(self) -> None:
        # app 只等引擎幾秒就強制結束；伺服器不回應時剩下的模型直接放棄
        self.set_translation(fast_model="qwen3.5:4b", final_model="qwen3.5:9b")
        self.run_session()
        self.unload.delay = 5.0
        budget = mock.patch.object(server_module, "TRANSLATION_UNLOAD_BUDGET", 0.3)
        budget.start()
        self.addCleanup(budget.stop)
        started = time.monotonic()
        with self.assertLogs("kikitori.engine.server", "WARNING"):
            self.server.close()
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertEqual(len(self.unload.calls), 1)
        self.assertLess(self.unload.calls[0][3], 0.31)


if __name__ == "__main__":
    unittest.main()
