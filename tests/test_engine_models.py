"""engine（同行程、假服務）：F4 的 `metrics` 事件與 F7 的 `models.delete`／`disk_bytes`，
以及 session 結束時釋放元件（ASR 模型放回模型池）。

子行程的端到端版本在 `test_engine.py`；這裡要觀察引擎內部的執行緒與服務呼叫，所以直接
建立 `EngineServer`。等待一律是「等到某則訊息出現」，不用 sleep 判斷時序。
"""

import dataclasses
import json
import os
import tempfile
import threading
import time
import unittest
from collections.abc import Callable
from typing import Any, ClassVar
from unittest import mock

import numpy as np

from kikitori.asr.types import Transcript
from kikitori.capture import AudioSource, DeviceKind
from kikitori.capture.errors import DeviceNotFoundError
from kikitori.config import Settings
from kikitori.engine import server as server_module
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer, _MetricsReporter, subtitle_event
from kikitori.engine.services import EngineServices, ProgressCallback
from kikitori.engine.testing import (
    ENDLESS_DEVICE,
    FAKE_MODEL_SIZE,
    SPEECH_DEVICE,
    fake_services,
)
from kikitori.pipeline import PipelineComponents, SubtitleUpdate
from kikitori.pipeline.types import Mode

WAIT = 10.0
INTERVAL = 0.02
Message = dict[str, Any]


class _Sink:
    def __init__(self) -> None:
        self.messages: list[Message] = []
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

    def wait(self, predicate: Callable[[Message], bool], count: int = 1) -> Message:
        with self._cond:
            found: list[Message] = []

            def seen() -> bool:
                found[:] = [m for m in self.messages if predicate(m)]
                return len(found) >= count

            if not self._cond.wait_for(seen, WAIT):
                raise AssertionError(f"timed out; last messages: {self.messages[-5:]}")
            return found[0]


class _ClosingTranscriber:
    """記錄 close() 次數的假 transcriber（代表放回模型池的 Whisper）。"""

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.closed = 0
        self.on_close: Callable[[], None] | None = None

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        result: Transcript = self._inner.transcribe(audio, language, prompt)
        return result

    def close(self) -> None:
        if self.on_close is not None:
            self.on_close()
        self.closed += 1


class _Build:
    """包裝假的 build_components：可停住（模擬載入數秒）、記錄建立的 transcriber。"""

    def __init__(self, inner: Callable[..., PipelineComponents]) -> None:
        self.inner = inner
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self.transcribers: list[_ClosingTranscriber] = []
        self.on_close: Callable[[], None] | None = None
        """之後建立的 transcriber 在 close() 時呼叫（觀察釋放的時機）。"""

    def __call__(
        self, settings: Settings, mode: Mode, **kwargs: Any
    ) -> PipelineComponents:
        self.entered.set()
        self.release.wait(WAIT)
        components = self.inner(settings, mode, **kwargs)
        fast = _ClosingTranscriber(components.fast_transcriber)
        final = _ClosingTranscriber(components.final_transcriber)
        fast.on_close = final.on_close = self.on_close
        self.transcribers += [fast, final]
        return dataclasses.replace(
            components, fast_transcriber=fast, final_transcriber=final
        )


class _GatedDownloads:
    """讓假的 ASR 下載停住，模擬下載進行中。"""

    def __init__(self, catalog: Any) -> None:
        self.catalog = catalog
        self.entered = threading.Event()
        self.release = threading.Event()

    def asr_models(self) -> Any:
        return self.catalog.asr_models()

    def download(
        self, model_id: str, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        self.entered.set()
        self.release.wait(WAIT)
        self.catalog.download(model_id, progress, cancel)

    def delete(self, model_id: str) -> int:
        result: int = self.catalog.delete(model_id)
        return result


class EngineCase(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        self.build = _Build(services.build_components)
        self.downloads = _GatedDownloads(services.models)
        self.addCleanup(self.downloads.release.set)
        self.services = self.wrap(
            dataclasses.replace(
                services,
                build_components=self.build,
                models=self.downloads,  # type: ignore[arg-type]
            )
        )
        self.sink = _Sink()
        self.server = EngineServer(
            self.services,
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
            metrics_interval=INTERVAL,
        )
        self.services.settings_store.load()
        self.addCleanup(self.server.close)
        self._next_id = 0

    def wrap(self, services: EngineServices) -> EngineServices:
        return services

    def request(self, method: str, params: dict[str, Any] | None = None) -> Message:
        self._next_id += 1
        request_id = self._next_id
        message: Message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())
        return self.sink.wait(lambda m: m.get("id") == request_id)

    def status(self, session_id: int, state: str) -> Message:
        return self.sink.wait(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == state
            )
        )["data"]

    def start(self, device: str = ENDLESS_DEVICE, mode: str = "interpret") -> int:
        response = self.request("session.start", {"mode": mode, "device_id": device})
        return int(response["result"]["session_id"])

    def assertError(self, response: Message, code: str) -> None:
        self.assertIn("error", response, response)
        self.assertEqual(response["error"]["code"], code, response)

    def metrics_threads(self) -> list[threading.Thread]:
        return [t for t in threading.enumerate() if t.name.endswith("-metrics")]

    def wait_no_metrics_threads(self) -> None:
        deadline = time.monotonic() + WAIT
        while self.metrics_threads():
            if time.monotonic() > deadline:
                self.fail(f"metrics threads leaked: {self.metrics_threads()}")
            time.sleep(0.01)


class SubtitleEventTest(unittest.TestCase):
    BASE: ClassVar[dict[str, Any]] = {
        "utterance_id": 3,
        "original": "こんにちは",
        "translation": "你好",
        "source_language": "ja",
        "target_language": "zh-TW",
        "start_sample": 0,
        "end_sample": 16000,
    }

    def test_partial_has_stable_chars_and_no_latency(self) -> None:
        update = SubtitleUpdate(**self.BASE, is_final=False, stable_chars=2)
        data = subtitle_event(update, 7)
        self.assertEqual(data["stable_chars"], 2)
        self.assertNotIn("latency_ms", data)
        self.assertEqual(data["session_id"], 7)

    def test_final_has_latency_and_no_stable_chars(self) -> None:
        update = SubtitleUpdate(**self.BASE, is_final=True, latency_ms=850)
        data = subtitle_event(update, 7)
        self.assertEqual(data["latency_ms"], 850)
        self.assertNotIn("stable_chars", data)

    def test_final_latency_may_be_null(self) -> None:
        data = subtitle_event(SubtitleUpdate(**self.BASE, is_final=True), 7)
        self.assertIn("latency_ms", data)
        self.assertIsNone(data["latency_ms"])

    def test_partial_without_stable_chars_reports_zero(self) -> None:
        data = subtitle_event(SubtitleUpdate(**self.BASE, is_final=False), 7)
        self.assertEqual(data["stable_chars"], 0)


class MetricsEventTest(EngineCase):
    def test_metrics_every_interval_while_running(self) -> None:
        session_id = self.start()
        self.status(session_id, "running")
        self.sink.wait(lambda m: m.get("event") == "metrics", count=3)
        metrics = [m["data"] for m in self.sink.messages if m.get("event") == "metrics"]
        for data in metrics:
            # v0.3 G8：背景音量是靜音（-120）或還沒有音訊（null）
            self.assertIn(data.pop("background_level_dbfs"), (None, -120.0))
            self.assertEqual(
                data,
                {
                    "session_id": session_id,
                    "level_dbfs": -120.0,
                    "backlog": 0,
                    "latency_ms_median": None,
                    "speech_level_dbfs": None,
                },
            )

    def test_stops_on_session_stop_without_leaking_the_thread(self) -> None:
        session_id = self.start()
        self.sink.wait(lambda m: m.get("event") == "metrics")
        self.request("session.stop")
        self.status(session_id, "idle")
        self.wait_no_metrics_threads()
        idle = next(
            i
            for i, m in enumerate(self.sink.messages)
            if m.get("event") == "status" and m["data"]["state"] == "idle"
        )
        # 結束狀態之後不會再有這個 session 的 metrics（等過好幾個間隔）
        time.sleep(INTERVAL * 5)
        self.assertEqual(
            [m for m in self.sink.messages[idle:] if m.get("event") == "metrics"], []
        )

    def test_stops_when_the_source_ends(self) -> None:
        session_id = self.start(SPEECH_DEVICE, "media")
        self.status(session_id, "idle")
        self.wait_no_metrics_threads()

    def test_stops_on_engine_close(self) -> None:
        self.start()
        self.sink.wait(lambda m: m.get("event") == "metrics")
        self.server.close()
        self.wait_no_metrics_threads()

    def test_no_reporter_when_the_session_fails_to_start(self) -> None:
        def missing(device_id: str | None, kind: DeviceKind) -> AudioSource:
            class Broken:
                def start(self) -> None:
                    raise DeviceNotFoundError("gone")

                def read(self, timeout: float) -> Any:  # pragma: no cover
                    raise AssertionError

                def stop(self) -> None:
                    pass

            return Broken()

        self.server._services = dataclasses.replace(
            self.server._services, open_source=missing
        )
        with self.assertLogs("kikitori", "ERROR"):
            session_id = self.start()
            self.status(session_id, "error")
        self.wait_no_metrics_threads()

    def test_interval_must_be_positive(self) -> None:
        for bad in (0, -1.0, float("nan"), float("inf")):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                EngineServer(
                    self.services,
                    MessageWriter(_Sink()),  # type: ignore[arg-type]
                    version="test",
                    metrics_interval=bad,
                )


class ReleaseComponentsTest(EngineCase):
    """session 結束後關閉 transcriber（真實實作會把 CTranslate2 模型放回模型池）。"""

    def test_closed_after_stop(self) -> None:
        session_id = self.start()
        self.status(session_id, "running")
        self.request("session.stop")
        self.status(session_id, "idle")
        self.assertEqual([t.closed for t in self.build.transcribers], [1, 1])

    def test_closed_after_the_source_ends(self) -> None:
        session_id = self.start(SPEECH_DEVICE, "media")
        self.status(session_id, "idle")
        self.assertEqual([t.closed for t in self.build.transcribers], [1, 1])

    def test_closed_when_stopped_while_loading(self) -> None:
        self.build.release.clear()
        session_id = self.start()
        loader = self.server._session.loader  # type: ignore[union-attr]
        assert loader is not None
        self.assertTrue(self.build.entered.wait(WAIT))
        self.request("session.stop")
        self.status(session_id, "idle")
        self.build.release.set()
        loader.join(WAIT)
        self.assertEqual([t.closed for t in self.build.transcribers], [1, 1])


class ReleaseBeforeStatusTest(EngineCase):
    """結束狀態要在元件釋放之後才送出：UI 收到 idle 就可能開始下一個 session，舊的模型
    不能還佔著顯示記憶體（close() 最多會等 2 秒）。"""

    def setUp(self) -> None:
        super().setUp()
        # close() 被呼叫時，是否已經送出過結束狀態（每個新的 EngineServer 只開一個 session）
        self.seen: list[bool] = []
        self.build.on_close = lambda: self.seen.append(self.terminal_sent())

    def terminal_sent(self) -> bool:
        return any(
            m.get("event") == "status" and m["data"]["state"] in ("idle", "error")
            for m in list(self.sink.messages)
        )

    def test_stop_releases_before_idle(self) -> None:
        session_id = self.start()
        self.status(session_id, "running")
        self.request("session.stop")
        self.status(session_id, "idle")
        self.assertEqual(self.seen, [False, False])

    def test_source_end_releases_before_idle(self) -> None:
        session_id = self.start(SPEECH_DEVICE, "media")
        self.status(session_id, "idle")
        self.assertEqual(self.seen, [False, False])

    def test_load_failure_releases_before_error(self) -> None:
        def broken(device_id: str | None, kind: DeviceKind) -> AudioSource:
            class Broken:
                def start(self) -> None:
                    raise DeviceNotFoundError("gone")

                def read(self, timeout: float) -> Any:  # pragma: no cover
                    raise AssertionError

                def stop(self) -> None:
                    pass

            return Broken()

        self.server._services = dataclasses.replace(
            self.server._services, open_source=broken
        )
        with self.assertLogs("kikitori", "ERROR"):
            session_id = self.start()
            self.status(session_id, "error")
        self.assertEqual(self.seen, [False, False])

    def test_slow_release_is_bounded_and_keeps_the_model_in_use(self) -> None:
        settings = self.services.settings_store.get()
        session_id = self.start()
        self.status(session_id, "running")
        gate = threading.Event()
        self.addCleanup(gate.set)
        self.build.transcribers[0].on_close = lambda: gate.wait(WAIT)
        with (
            mock.patch.object(server_module, "RELEASE_TIMEOUT", 0.1),
            self.assertLogs("kikitori.engine.server", "WARNING") as logs,
        ):
            self.request("session.stop")
        self.status(session_id, "idle")
        self.assertTrue(any("still releasing" in line for line in logs.output))
        # 釋放還沒做完：模型仍算使用中，不能刪
        self.assertError(
            self.request("models.delete", {"model": settings.asr.fast_model}),
            "model_in_use",
        )
        gate.set()
        deadline = time.monotonic() + WAIT
        while [t.closed for t in self.build.transcribers] != [1, 1]:
            if time.monotonic() > deadline:
                self.fail("components were never released")
            time.sleep(0.005)
        self.assertIn(
            "result",
            self.request("models.delete", {"model": settings.asr.fast_model}),
        )


class _StuckPipeline:
    """metrics 用的 pipeline 替身：一直在執行中。"""

    state = "running"

    def wait(self, timeout: float | None = None) -> bool:
        return False

    def metrics(self) -> Any:
        return mock.Mock(level_dbfs=-20.0, backlog=0, latency_ms_median=None)


class MetricsReporterStopTest(unittest.TestCase):
    def test_stop_does_not_hang_on_a_stuck_send(self) -> None:
        """stdout 管線塞滿時送出會卡住；stop() 不能因此永遠等下去（結束狀態送不出去）。"""
        entered = threading.Event()
        gate = threading.Event()
        self.addCleanup(gate.set)

        def send(name: str, data: Any) -> None:
            entered.set()
            gate.wait(WAIT)

        reporter = _MetricsReporter(1, _StuckPipeline(), send, INTERVAL)  # type: ignore[arg-type]
        reporter.start()
        self.assertTrue(entered.wait(WAIT))
        with (
            mock.patch.object(server_module, "METRICS_JOIN_TIMEOUT", 0.05),
            self.assertLogs("kikitori.engine.server", "WARNING"),
        ):
            started = time.monotonic()
            reporter.stop()
        self.assertLess(time.monotonic() - started, WAIT / 2)


class ModelsListDiskBytesTest(EngineCase):
    def test_disk_bytes_for_downloaded_models_only(self) -> None:
        asr = {m["id"]: m for m in self.request("models.list")["result"]["asr"]}
        self.assertEqual(asr["base"]["disk_bytes"], FAKE_MODEL_SIZE)
        self.assertTrue(asr["base"]["downloaded"])
        self.assertIsNone(asr["fake-missing"]["disk_bytes"])
        self.assertFalse(asr["fake-missing"]["downloaded"])


class ModelsDeleteTest(EngineCase):
    def test_delete_frees_the_model(self) -> None:
        result = self.request("models.delete", {"model": "large-v3-turbo"})["result"]
        self.assertEqual(
            result, {"model": "large-v3-turbo", "freed_bytes": FAKE_MODEL_SIZE}
        )
        asr = {m["id"]: m for m in self.request("models.list")["result"]["asr"]}
        self.assertFalse(asr["large-v3-turbo"]["downloaded"])
        self.assertIsNone(asr["large-v3-turbo"]["disk_bytes"])
        self.assertError(
            self.request("models.delete", {"model": "large-v3-turbo"}),
            "model_not_downloaded",
        )

    def test_unknown_and_not_downloaded(self) -> None:
        self.assertError(
            self.request("models.delete", {"model": "whisper-9000"}), "unknown_model"
        )
        # GPU 執行環境不是 ASR 模型：不能用這個方法刪
        self.assertError(
            self.request("models.delete", {"model": "gpu-runtime"}), "unknown_model"
        )
        self.assertError(
            self.request("models.delete", {"model": "fake-missing"}),
            "model_not_downloaded",
        )

    def test_invalid_params(self) -> None:
        for params in (
            {},
            {"model": ""},
            {"model": 3},
            {"model": None},
            {"model": "base", "force": True},
        ):
            with self.subTest(params=params):
                self.assertError(
                    self.request("models.delete", params), "invalid_params"
                )

    def test_in_use_by_a_running_session(self) -> None:
        settings = self.services.settings_store.get()
        session_id = self.start()
        self.status(session_id, "running")
        for model in (settings.asr.fast_model, settings.asr.final_model):
            with self.subTest(model=model):
                self.assertError(
                    self.request("models.delete", {"model": model}), "model_in_use"
                )
        self.request("session.stop")
        self.status(session_id, "idle")
        result = self.request("models.delete", {"model": settings.asr.fast_model})
        self.assertEqual(result["result"]["freed_bytes"], FAKE_MODEL_SIZE)

    def test_in_use_while_loading_even_after_stop(self) -> None:
        settings = self.services.settings_store.get()
        self.build.release.clear()
        session_id = self.start()
        self.assertTrue(self.build.entered.wait(WAIT))
        self.assertError(
            self.request("models.delete", {"model": settings.asr.final_model}),
            "model_in_use",
        )
        # 停止後載入執行緒仍在載入（佔著模型檔）：仍然不能刪
        self.request("session.stop")
        self.status(session_id, "idle")
        self.assertError(
            self.request("models.delete", {"model": settings.asr.final_model}),
            "model_in_use",
        )
        self.build.release.set()
        loader = self.server._last_loader
        assert loader is not None
        loader.join(WAIT)
        self.assertIn(
            "result",
            self.request("models.delete", {"model": settings.asr.final_model}),
        )

    def test_in_use_by_a_download(self) -> None:
        self.request("models.download", {"model": "large-v3-turbo"})
        self.assertTrue(self.downloads.entered.wait(WAIT))
        self.assertError(
            self.request("models.delete", {"model": "large-v3-turbo"}), "model_in_use"
        )
        # 下載中、尚未完成的模型也是「使用中」（先於 model_not_downloaded）
        self.request("models.download", {"model": "fake-missing"})
        self.assertError(
            self.request("models.delete", {"model": "fake-missing"}), "model_in_use"
        )
        self.downloads.release.set()
        self.sink.wait(
            lambda m: (
                m.get("event") == "download_progress" and m["data"]["state"] == "done"
            ),
            count=2,
        )
        self.assertIn(
            "result", self.request("models.delete", {"model": "large-v3-turbo"})
        )

    def test_download_is_unregistered_before_its_final_progress_event(self) -> None:
        # UI 收到 done／error 後立即刪除不能因為登記還沒撤銷而得到 model_in_use
        # （先送 done 再撤銷時，這個測試原本約三次失敗兩次）。
        registered_at_final: list[bool] = []
        original = self.server._send_event

        def spy(name: str, data: Any) -> None:
            if name == "download_progress" and data["state"] in ("done", "error"):
                with self.server._downloads_lock:
                    registered_at_final.append(
                        (data["kind"], data["model"]) in self.server._downloads
                    )
            original(name, data)

        with mock.patch.object(self.server, "_send_event", spy):
            self.request("models.download", {"model": "large-v3-turbo"})
            self.assertTrue(self.downloads.entered.wait(WAIT))
            self.downloads.release.set()
            self.sink.wait(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["state"] == "done"
                )
            )
        self.assertEqual(registered_at_final, [False])

    def test_other_models_can_be_deleted_during_a_session(self) -> None:
        session_id = self.start()
        self.status(session_id, "running")
        settings = self.services.settings_store.get()
        other = "large-v3-turbo"
        self.assertNotIn(other, (settings.asr.fast_model, settings.asr.final_model))
        self.assertIn("result", self.request("models.delete", {"model": other}))


if __name__ == "__main__":
    unittest.main()
