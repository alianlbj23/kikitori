"""engine 的語言與翻譯模型方法（同行程、假服務）：`languages.list`、
`settings.preview_languages`、`session.set_translation`、`models.pull_translation`，
以及 `session.start` 在翻譯模型缺少時的 `translation_model_missing`。

子行程的端到端版本在 `test_engine.py`；這裡需要觀察引擎內部（pipeline 的狀態、
服務被呼叫幾次），所以直接建立 `EngineServer`。等待一律是「等到某則訊息出現」。
"""

import dataclasses
import json
import logging
import os
import tempfile
import threading
import unittest
from collections.abc import Callable
from typing import Any
from unittest import mock

from kikitori.config import Settings, SettingsStore
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.services import (
    DefaultTranslationModels,
    EngineServices,
    TranslationServerState,
)
from kikitori.engine.testing import (
    ENDLESS_DEVICE,
    FAKE_BROKEN_PULL,
    FAKE_SLOW_PULL,
    FAKE_TRANSLATION_ENV,
    fake_services,
)
from kikitori.hardware.ollama import OllamaAuthError
from kikitori.hardware.types import TranslationServerInfo
from kikitori.pipeline import PipelineComponents
from kikitori.pipeline.types import Mode

WAIT = 10.0
Message = dict[str, Any]


class _Sink:
    """MessageWriter 的輸出：逐行解析，讓測試等待符合條件的訊息。"""

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

    def wait(self, predicate: Callable[[Message], bool]) -> Message:
        with self._cond:
            found: list[Message] = []

            def seen() -> bool:
                found[:] = [m for m in self.messages if predicate(m)]
                return bool(found)

            if not self._cond.wait_for(seen, WAIT):
                raise AssertionError(f"timed out; last messages: {self.messages[-5:]}")
            return found[0]


class EngineCase(unittest.TestCase):
    translation_profile = "ollama"

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(
            os.environ,
            {
                "KIKITORI_CONFIG_DIR": tmp.name,
                FAKE_TRANSLATION_ENV: self.translation_profile,
            },
        )
        env.start()
        self.addCleanup(env.stop)
        self.services = self.wrap(fake_services())
        self.sink = _Sink()
        self.server = EngineServer(
            self.services,
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
        )
        self.services.settings_store.load()
        self.addCleanup(self.server.close)
        self._next_id = 0

    def wrap(self, services: EngineServices) -> EngineServices:
        """子類別可替換服務（例如計數或讓載入停住）。"""
        return services

    def request(self, method: str, params: dict[str, Any] | None = None) -> Message:
        self._next_id += 1
        request_id = self._next_id
        message: Message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())
        return self.sink.wait(lambda m: m.get("id") == request_id)

    def event(self, name: str, where: Callable[[Message], bool]) -> Message:
        return self.sink.wait(lambda m: m.get("event") == name and where(m["data"]))[
            "data"
        ]

    def assertError(self, response: Message, code: str) -> None:
        self.assertIn("error", response, response)
        self.assertEqual(response["error"]["code"], code, response)


class LanguagesListTest(EngineCase):
    def test_lists_the_catalog(self) -> None:
        languages = self.request("languages.list")["result"]["languages"]
        self.assertEqual(len(languages), 20)
        by_code = {lang["code"]: lang for lang in languages}
        self.assertEqual(
            set(by_code["ja"]),
            {
                "code",
                "english_name",
                "native_name",
                "whisper_code",
                "rtl",
                "no_space",
                "asr_quality",
            },
        )
        self.assertEqual(by_code["zh-TW"]["whisper_code"], "zh")
        self.assertTrue(by_code["ar"]["rtl"])
        self.assertTrue(by_code["th"]["no_space"])
        self.assertEqual(by_code["vi"]["asr_quality"]["large-v3"], "C")
        self.assertEqual(by_code["ko"]["native_name"], "한국어")

    def test_rejects_params(self) -> None:
        self.assertError(self.request("languages.list", {"x": 1}), "invalid_params")


class PreviewLanguagesTest(EngineCase):
    def test_preview_does_not_write_settings_or_download(self) -> None:
        path = self.services.settings_store.path
        result = self.request(
            "settings.preview_languages",
            {"source_language": "ko", "target_language": "es"},
        )["result"]
        self.assertFalse(path.exists())
        self.assertEqual(self.services.settings_store.get(), Settings())
        self.assertEqual(result["languages"]["source_language"], "ko")
        self.assertEqual(result["settings"]["target_language"], "es")
        self.assertTrue(result["differs"])
        self.assertIn("asr", result["models"])
        self.assertTrue(result["translation_needed"])
        self.assertFalse(
            any(m.get("event") == "download_progress" for m in self.sink.messages)
        )

    def test_mode_and_aliases(self) -> None:
        result = self.request(
            "settings.preview_languages",
            {
                "mode": "interpret",
                "target_language": "zh_hant",
                "partner_language": "en",
            },
        )["result"]
        self.assertEqual(
            result["languages"],
            {
                "mode": "interpret",
                "source_language": "ja",
                "target_language": "zh-TW",
                "partner_language": "en",
            },
        )

    def test_invalid_params(self) -> None:
        self.assertError(
            self.request("settings.preview_languages", {"speed": 1}), "invalid_params"
        )
        self.assertError(
            self.request("settings.preview_languages", {"mode": "karaoke"}),
            "invalid_params",
        )
        self.assertError(
            self.request("settings.preview_languages", {"source_language": "xx"}),
            "invalid_settings",
        )
        # 口譯不能兩邊同一個 Whisper 語言
        self.assertError(
            self.request(
                "settings.preview_languages",
                {"mode": "interpret", "target_language": "ja"},
            ),
            "invalid_settings",
        )


class _CountingTranslation:
    """包住假翻譯伺服器，記錄 server_state 被呼叫幾次；`release` 清掉時讓檢查停住。"""

    def __init__(self, inner: Any) -> None:
        self.inner = inner
        self.state_calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()

    def server_state(self, settings: Settings) -> TranslationServerState:
        self.state_calls += 1
        self.entered.set()
        self.release.wait(WAIT)
        return self.inner.server_state(settings)

    def pull(self, *args: Any) -> None:
        self.inner.pull(*args)


class _GatedBuild:
    """讓 build_components 停住，模擬載入模型的數秒。"""

    def __init__(self, inner: Callable[[Settings, Mode], PipelineComponents]) -> None:
        self.inner = inner
        self.calls = 0
        self.entered = threading.Event()
        self.release = threading.Event()
        self.release.set()
        self._lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        """同時在載入的 session 數的最大值（GPU 上同時載入兩份 ASR 模型會用完顯示記憶體）。"""

    def __call__(self, settings: Settings, mode: Mode) -> PipelineComponents:
        with self._lock:
            self.calls += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
        try:
            self.entered.set()
            self.release.wait(WAIT)
            return self.inner(settings, mode)
        finally:
            with self._lock:
                self.active -= 1


class _LogWaiter(logging.Handler):
    """等到某則 log 出現（載入在背景執行緒上，assertLogs 不能等待）。"""

    def __init__(self, needle: str) -> None:
        super().__init__(logging.DEBUG)
        self.needle = needle
        self.seen = threading.Event()

    def emit(self, record: logging.LogRecord) -> None:
        if self.needle in record.getMessage():
            self.seen.set()


class SessionCase(EngineCase):
    def wrap(self, services: EngineServices) -> EngineServices:
        self.translation = _CountingTranslation(services.translation_models)
        self.build = _GatedBuild(services.build_components)
        return dataclasses.replace(
            services,
            translation_models=self.translation,  # type: ignore[arg-type]
            build_components=self.build,
        )

    def start(self, mode: str = "interpret") -> int:
        response = self.request(
            "session.start", {"mode": mode, "device_id": ENDLESS_DEVICE}
        )
        return int(response["result"]["session_id"])

    def status(self, session_id: int, state: str) -> Message:
        return self.event(
            "status", lambda d: d["session_id"] == session_id and d["state"] == state
        )


class SetTranslationTest(SessionCase):
    def test_without_a_session_only_updates_settings(self) -> None:
        result = self.request("session.set_translation", {"enabled": False})["result"]
        self.assertIsNone(result["session_id"])
        self.assertFalse(result["settings"]["translation"]["enabled"])
        # 存進設定檔
        reloaded = SettingsStore(self.services.settings_store.path)
        reloaded.load()
        self.assertFalse(reloaded.get().translation.enabled)

    def test_running_session_is_toggled(self) -> None:
        session_id = self.start()
        self.status(session_id, "running")
        result = self.request("session.set_translation", {"enabled": False})["result"]
        self.assertEqual(result["session_id"], session_id)
        session = self.server._session
        assert session is not None and session.pipeline is not None
        self.assertFalse(session.pipeline.translation_enabled)
        self.request("session.set_translation", {"enabled": True})
        self.assertTrue(session.pipeline.translation_enabled)

    def test_toggle_while_loading_applies_when_the_pipeline_is_built(self) -> None:
        self.build.release.clear()
        session_id = self.start()
        self.assertTrue(self.build.entered.wait(WAIT))
        result = self.request("session.set_translation", {"enabled": False})["result"]
        self.assertEqual(result["session_id"], session_id)
        self.build.release.set()
        self.status(session_id, "running")
        session = self.server._session
        assert session is not None and session.pipeline is not None
        self.assertFalse(session.pipeline.translation_enabled)

    def test_invalid_params(self) -> None:
        for params in (
            {},
            {"enabled": "yes"},
            {"enabled": 1},
            {"enabled": True, "x": 1},
        ):
            with self.subTest(params=params):
                self.assertError(
                    self.request("session.set_translation", params), "invalid_params"
                )


class TranslationModelMissingTest(SessionCase):
    def test_missing_model_fails_before_loading_asr(self) -> None:
        self.request("settings.set", {"translation": {"final_model": "qwen3.5:9b"}})
        session_id = self.start()
        error = self.status(session_id, "error")
        self.assertEqual(error["code"], "translation_model_missing")
        self.assertEqual(error["data"], {"models": ["qwen3.5:9b"]})
        self.assertEqual(self.build.calls, 0)
        # 下載後重試就能開始
        self.request("models.pull_translation", {"model": "qwen3.5:9b"})
        self.event(
            "download_progress",
            lambda d: d["model"] == "qwen3.5:9b" and d["state"] == "done",
        )
        retry = self.start()
        self.status(retry, "running")

    def test_translation_off_needs_no_server(self) -> None:
        self.request(
            "settings.set",
            {"translation": {"enabled": False, "final_model": "qwen3.5:9b"}},
        )
        session_id = self.start()
        running = self.status(session_id, "running")
        self.assertNotIn("data", running)
        self.assertEqual(self.translation.state_calls, 0)

    def test_same_language_media_needs_no_translation_model(self) -> None:
        self.request(
            "settings.set",
            {
                "source_language": "zh-CN",
                "translation": {"fast_model": "missing:1b"},
            },
        )
        session_id = self.start("media")
        self.status(session_id, "running")
        self.assertEqual(self.translation.state_calls, 0)


class StopWhileLoadingTest(SessionCase):
    """停止還在載入的 session：不再繼續載入，下一個 session 也不會與它同時載入。"""

    def loader(self) -> threading.Thread:
        session = self.server._session
        assert session is not None and session.loader is not None
        return session.loader

    def wait_for_log(self, needle: str) -> _LogWaiter:
        waiter = _LogWaiter(needle)
        log = logging.getLogger("kikitori.engine.server")
        log.addHandler(waiter)
        self.addCleanup(log.removeHandler, waiter)
        previous = log.level
        log.setLevel(logging.DEBUG)
        self.addCleanup(log.setLevel, previous)
        return waiter

    def test_stop_during_the_translation_check_skips_loading(self) -> None:
        self.translation.release.clear()
        session_id = self.start()
        loader = self.loader()
        self.assertTrue(self.translation.entered.wait(WAIT))
        self.request("session.stop")
        self.status(session_id, "idle")
        self.translation.release.set()
        loader.join(WAIT)
        self.assertFalse(loader.is_alive())
        self.assertEqual(self.build.calls, 0)

    def test_next_session_waits_for_the_stopped_one_to_finish_loading(self) -> None:
        self.build.release.clear()
        first = self.start()
        first_loader = self.loader()
        self.assertTrue(self.build.entered.wait(WAIT))
        self.request("session.stop")
        self.status(first, "idle")
        waiting = self.wait_for_log("waiting for session")
        second = self.start()
        self.assertTrue(waiting.seen.wait(WAIT))
        self.build.release.set()
        self.status(second, "running")
        first_loader.join(WAIT)
        self.assertEqual(self.build.calls, 2)
        self.assertEqual(self.build.max_active, 1)

    def test_waiting_session_can_be_stopped_too(self) -> None:
        self.build.release.clear()
        first = self.start()
        first_loader = self.loader()
        self.assertTrue(self.build.entered.wait(WAIT))
        self.request("session.stop")
        self.status(first, "idle")
        waiting = self.wait_for_log("waiting for session")
        second = self.start()
        second_loader = self.loader()
        self.assertTrue(waiting.seen.wait(WAIT))
        self.request("session.stop")
        self.status(second, "idle")
        self.build.release.set()
        for loader in (first_loader, second_loader):
            loader.join(WAIT)
            self.assertFalse(loader.is_alive())
        self.assertEqual(self.build.calls, 1)


class AuthFailedTest(SessionCase):
    translation_profile = "auth"

    def test_pull_reports_auth_failure(self) -> None:
        response = self.request("models.pull_translation", {"model": "qwen3.5:9b"})
        self.assertError(response, "translation_server_auth_failed")
        self.assertIn("api_key_env", response["error"]["message"])

    def test_session_starts_with_a_warning(self) -> None:
        # 無法確認模型在不在：照常開始（與連不上相同），但要留下提醒
        self.request("settings.set", {"translation": {"final_model": "qwen3.5:9b"}})
        with self.assertLogs("kikitori.engine.server", "WARNING") as logs:
            session_id = self.start()
            self.status(session_id, "running")
        self.assertTrue(any("API key" in line for line in logs.output), logs.output)


class DefaultTranslationModelsTest(unittest.TestCase):
    def test_auth_failure_is_not_reported_as_not_ollama(self) -> None:
        info = TranslationServerInfo(
            base_url="http://localhost:11434/v1",
            is_local=True,
            reachable=True,
            error="HTTP 401 from /v1/models",
        )
        with (
            mock.patch(
                "kikitori.hardware.translation_server.probe_translation_server",
                return_value=info,
            ),
            mock.patch(
                "kikitori.hardware.ollama.ollama_version",
                side_effect=OllamaAuthError("HTTP 401 from /api/version"),
            ),
        ):
            state = DefaultTranslationModels().server_state(Settings())
        self.assertTrue(state.reachable)
        self.assertTrue(state.auth_failed)
        self.assertFalse(state.is_ollama)
        self.assertIn("401", state.error or "")


class _BreakingSink(_Sink):
    """寫到含 `marker` 的訊息時失敗（模擬 stdout 在寫出回應時關閉）。"""

    def __init__(self, marker: bytes) -> None:
        super().__init__()
        self.marker = marker

    def write(self, data: bytes) -> int:
        if self.marker in data:
            raise OSError("pipe closed")
        return super().write(data)


class PullResponseLostTest(EngineCase):
    def test_download_is_unregistered_when_the_response_cannot_be_written(
        self,
    ) -> None:
        server = EngineServer(
            self.services,
            MessageWriter(_BreakingSink(b'"started":true')),  # type: ignore[arg-type]
            version="test",
        )
        self.addCleanup(server.close)
        request = {
            "id": 1,
            "method": "models.pull_translation",
            "params": {"model": "qwen3.5:9b"},
        }
        with self.assertLogs("kikitori.engine", "ERROR"):
            server.handle_line(json.dumps(request).encode())
            worker = server._deferred._thread
            assert worker is not None
            worker.join(WAIT)
        self.assertFalse(worker.is_alive())
        # 回應沒寫出去：下載執行緒不會啟動，登記也不能留著（否則永遠 download_in_progress）
        self.assertEqual(server._downloads, {})


class UnreachableServerSessionTest(SessionCase):
    translation_profile = "offline"

    def test_session_starts_when_the_server_is_unreachable(self) -> None:
        # 連不上時照常開始：原文不依賴翻譯，每句翻譯失敗只顯示原文並提醒一次
        self.request("settings.set", {"translation": {"final_model": "qwen3.5:9b"}})
        session_id = self.start()
        self.status(session_id, "running")
        self.assertEqual(self.translation.state_calls, 1)

    def test_pull_reports_unreachable(self) -> None:
        self.assertError(
            self.request("models.pull_translation", {"model": "qwen3.5:9b"}),
            "translation_server_unreachable",
        )


class NotOllamaTest(SessionCase):
    translation_profile = "openai"

    def test_pull_is_unsupported_and_session_still_starts(self) -> None:
        response = self.request("models.pull_translation", {"model": "qwen3.5:9b"})
        self.assertError(response, "pull_unsupported")
        self.assertIn("qwen3.5:9b", response["error"]["message"])
        self.request("settings.set", {"translation": {"final_model": "other:7b"}})
        self.status(self.start(), "running")


class PullTranslationTest(EngineCase):
    def test_progress_events_then_installed(self) -> None:
        response = self.request("models.pull_translation", {"model": "qwen3.5:9b"})
        self.assertEqual(response["result"], {"model": "qwen3.5:9b", "started": True})
        first = self.event("download_progress", lambda d: d["model"] == "qwen3.5:9b")
        self.assertEqual(
            (first["kind"], first["downloaded_bytes"], first["state"]),
            ("translation", 0, "downloading"),
        )
        done = self.event(
            "download_progress",
            lambda d: d["model"] == "qwen3.5:9b" and d["state"] != "downloading",
        )
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["downloaded_bytes"], done["total_bytes"])
        # 回應先於這次下載的任何事件
        index = self.sink.messages.index(response)
        progress = self.sink.messages.index(
            next(m for m in self.sink.messages if m.get("event") == "download_progress")
        )
        self.assertLess(index, progress)
        models = self.request("models.list")["result"]["translation"]["models"]
        installed = {m["name"]: m["installed"] for m in models}
        self.assertTrue(installed["qwen3.5:9b"])

    def test_failure_is_an_error_event(self) -> None:
        with self.assertLogs("kikitori.engine.server", "ERROR"):
            self.request("models.pull_translation", {"model": FAKE_BROKEN_PULL})
            failed = self.event(
                "download_progress",
                lambda d: (
                    d["model"] == FAKE_BROKEN_PULL and d["state"] != "downloading"
                ),
            )
        self.assertEqual((failed["state"], failed["kind"]), ("error", "translation"))
        self.assertIn("simulated pull failure", failed["error"])

    def test_same_model_twice_is_in_progress_and_shutdown_cancels(self) -> None:
        self.assertIn(
            "result", self.request("models.pull_translation", {"model": FAKE_SLOW_PULL})
        )
        self.assertError(
            self.request("models.pull_translation", {"model": FAKE_SLOW_PULL}),
            "download_in_progress",
        )
        with self.assertLogs("kikitori.engine.server", "ERROR"):
            self.server.close()
        cancelled = self.event(
            "download_progress",
            lambda d: d["model"] == FAKE_SLOW_PULL and d["state"] == "error",
        )
        self.assertIn("cancel", cancelled["error"])

    def test_invalid_model_names(self) -> None:
        for params in (
            {},
            {"model": ""},
            {"model": "a b"},
            {"model": 3},
            {"model": "x", "y": 1},
        ):
            with self.subTest(params=params):
                self.assertError(
                    self.request("models.pull_translation", params), "invalid_params"
                )

    def test_asr_downloads_report_their_kind(self) -> None:
        self.request("models.download", {"model": "fake-missing"})
        done = self.event(
            "download_progress",
            lambda d: d["model"] == "fake-missing" and d["state"] == "done",
        )
        self.assertEqual(done["kind"], "asr")

    def test_asr_and_translation_names_do_not_collide(self) -> None:
        # ASR 的 id 與翻譯模型名稱是不同的命名空間：同名也能同時下載
        self.request("models.pull_translation", {"model": FAKE_SLOW_PULL})
        with mock.patch.object(
            self.services.models,
            "asr_models",
            return_value=[
                dataclasses.replace(
                    self.services.models.asr_models()[0], id=FAKE_SLOW_PULL
                )
            ],
        ):
            response = self.request("models.download", {"model": FAKE_SLOW_PULL})
        self.assertIn("result", response)
        with self.assertLogs("kikitori.engine.server", "ERROR"):
            self.server.close()  # 取消還在跑的慢速下載


if __name__ == "__main__":
    unittest.main()
