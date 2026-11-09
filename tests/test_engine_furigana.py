"""engine 的振假名：只在日文、開啟、辭典已下載時加 `ruby`；關閉時不載入辭典；結束時釋放；
下載（`kind: "furigana"`）、刪除、`session.set_furigana` 與 `models.list` 的 `furigana`。

同行程建立 `EngineServer`（假服務），等待一律是「等到某則訊息出現」。
"""

import dataclasses
import json
import os
import tempfile
import threading
import unittest
from collections.abc import Callable
from types import SimpleNamespace
from typing import Any
from unittest import mock

from kikitori.config import Settings
from kikitori.engine.furigana_service import DictionaryInUseError, FuriganaService
from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer
from kikitori.engine.services import ProgressCallback
from kikitori.engine.testing import (
    ENDLESS_DEVICE,
    FAKE_FURIGANA_SIZE,
    SPEECH_DEVICE,
    FakeFurigana,
    FakeTranscriber,
    fake_services,
)
from kikitori.furigana.dictionary import DICTIONARY_ID
from kikitori.pipeline import PipelineComponents, SubtitleUpdate
from kikitori.pipeline.types import Mode

WAIT = 10.0
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

    def events(self, name: str) -> list[Message]:
        with self._cond:
            return [m["data"] for m in self.messages if m.get("event") == name]


class FuriganaEngineCase(unittest.TestCase):
    """假 ASR 的原文改成含漢字的日文：`今日は天気1`、`今日は天気2`…"""

    installed = True

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        inner = services.build_components
        self.furigana = FakeFurigana(installed=self.installed)

        def build(settings: Settings, mode: Mode, **kwargs: Any) -> PipelineComponents:
            components = inner(settings, mode, **kwargs)
            language = "ja" if settings.source_language == "ja" else "en"
            return dataclasses.replace(
                components,
                fast_transcriber=FakeTranscriber("今日は天気", language),
                final_transcriber=FakeTranscriber("今日は天気", language),
            )

        self.services = dataclasses.replace(
            services, build_components=build, furigana=self.furigana
        )
        self.sink = _Sink()
        self.server = EngineServer(
            self.services,
            MessageWriter(self.sink),  # type: ignore[arg-type]
            version="test",
        )
        self.services.settings_store.load()
        self.addCleanup(self.server.close)
        self._next_id = 0

    def request(self, method: str, params: dict[str, Any] | None = None) -> Message:
        self._next_id += 1
        request_id = self._next_id
        message: Message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.server.handle_line(json.dumps(message).encode())
        return self.sink.wait(lambda m: m.get("id") == request_id)

    def run_speech_session(self) -> int:
        response = self.request(
            "session.start", {"mode": "media", "device_id": SPEECH_DEVICE}
        )
        session_id = int(response["result"]["session_id"])
        self.sink.wait(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == "idle"
            )
        )
        return session_id

    def set_furigana_setting(self, enabled: bool) -> None:
        response = self.request("settings.set", {"furigana": {"enabled": enabled}})
        self.assertIn("result", response, response)

    def assertError(self, response: Message, code: str) -> None:
        self.assertIn("error", response, response)
        self.assertEqual(response["error"]["code"], code, response)


class SubtitleRubyTest(FuriganaEngineCase):
    def test_off_by_default_never_loads_the_dictionary(self) -> None:
        self.run_speech_session()
        subtitles = self.sink.events("subtitle")
        self.assertTrue(subtitles)
        for data in subtitles:
            self.assertNotIn("ruby", data)
        self.assertEqual(self.furigana.opened, [])

    def test_japanese_subtitles_get_ruby_and_the_dictionary_is_released(self) -> None:
        self.set_furigana_setting(True)
        self.run_speech_session()
        subtitles = self.sink.events("subtitle")
        partials = [d for d in subtitles if not d["is_final"]]
        finals = [d for d in subtitles if d["is_final"] and d["original"]]
        # 假裝置一次送完整段音訊（不是即時），句子結束前有沒有即時字幕取決於執行緒排程
        # （CI 的 macOS 曾經一則都沒有）；即時字幕的標註由下面的 test_partials_get_ruby 確定地測
        self.assertTrue(finals)
        for data in partials + finals:
            self.assertTrue(data["original"].startswith("今日は天気"))
            self.assertEqual(data["ruby"], [[0, 2, "きょう"], [3, 5, "てんき"]])
        # 一個 session 只開一次辭典，結束（idle 之前）時已釋放
        self.assertEqual(len(self.furigana.opened), 1)
        self.assertTrue(self.furigana.opened[0].closed)
        self.assertFalse(self.server._furigana.in_use)

    def test_partials_get_ruby(self) -> None:
        # 標註不看 is_final：直接把一則即時字幕交給伺服器送出事件的那一步
        self.set_furigana_setting(True)
        self.server._furigana.begin_session(7, True)
        partial = SubtitleUpdate(1, "今日は天気", "", "ja", "zh-TW", False, 0, 16000, 2)
        self.server._on_subtitle(SimpleNamespace(id=7), partial)  # type: ignore[arg-type]
        data = self.sink.events("subtitle")[-1]
        self.assertFalse(data["is_final"])
        self.assertEqual(data["ruby"], [[0, 2, "きょう"], [3, 5, "てんき"]])
        self.server._furigana.end_session(7)

    def test_non_japanese_subtitles_have_no_ruby(self) -> None:
        self.request("settings.set", {"source_language": "en"})
        self.set_furigana_setting(True)
        self.run_speech_session()
        subtitles = self.sink.events("subtitle")
        self.assertTrue(subtitles)
        for data in subtitles:
            self.assertEqual(data["source_language"], "en")
            self.assertNotIn("ruby", data)
        self.assertEqual(self.furigana.opened, [])


class MissingDictionaryTest(FuriganaEngineCase):
    installed = False

    def test_enabled_without_dictionary_keeps_subtitles_and_warns_once(self) -> None:
        self.set_furigana_setting(True)
        with self.assertLogs("kikitori.engine.furigana_service", "WARNING") as logs:
            self.run_speech_session()
        subtitles = self.sink.events("subtitle")
        self.assertTrue(subtitles)
        for data in subtitles:
            self.assertNotIn("ruby", data)
        warnings = [
            line for line in logs.output if "dictionary is not downloaded" in line
        ]
        self.assertEqual(len(warnings), 1, logs.output)

    def test_set_furigana_requires_the_dictionary(self) -> None:
        self.assertError(
            self.request("session.set_furigana", {"enabled": True}),
            "model_not_downloaded",
        )
        settings = self.request("settings.get")["result"]["settings"]
        self.assertFalse(settings["furigana"]["enabled"])
        # 關閉不需要辭典
        result = self.request("session.set_furigana", {"enabled": False})["result"]
        self.assertFalse(result["settings"]["furigana"]["enabled"])
        self.assertIsNone(result["session_id"])

    def test_download_then_enable(self) -> None:
        listed = self.request("models.list")["result"]["furigana"]
        self.assertFalse(listed["installed"])
        self.assertFalse(listed["in_use"])
        self.assertEqual(listed["id"], DICTIONARY_ID)
        response = self.request("models.download", {"model": DICTIONARY_ID})
        self.assertEqual(response["result"], {"model": DICTIONARY_ID, "started": True})
        done = self.sink.wait(
            lambda m: (
                m.get("event") == "download_progress" and m["data"]["state"] == "done"
            )
        )["data"]
        self.assertEqual(done["kind"], "furigana")
        self.assertEqual(done["model"], DICTIONARY_ID)
        self.assertEqual(done["total_bytes"], FAKE_FURIGANA_SIZE)
        progress = self.sink.events("download_progress")
        self.assertEqual(progress[0]["downloaded_bytes"], 0)
        self.assertTrue(all(p["kind"] == "furigana" for p in progress))
        listed = self.request("models.list")["result"]["furigana"]
        self.assertTrue(listed["installed"])
        self.assertEqual(listed["disk_bytes"], FAKE_FURIGANA_SIZE)
        result = self.request("session.set_furigana", {"enabled": True})["result"]
        self.assertTrue(result["settings"]["furigana"]["enabled"])

    def test_failed_download_reports_an_error_event(self) -> None:
        def broken(progress: ProgressCallback, cancel: threading.Event) -> None:
            progress(1024, FAKE_FURIGANA_SIZE)
            raise RuntimeError("simulated network failure")

        with mock.patch.object(self.furigana, "install", broken):
            self.request("models.download", {"model": DICTIONARY_ID})
            error = self.sink.wait(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["state"] == "error"
                )
            )["data"]
        self.assertEqual(error["kind"], "furigana")
        self.assertIn("simulated network failure", error["error"])
        self.assertFalse(self.request("models.list")["result"]["furigana"]["installed"])

    def test_delete_not_downloaded(self) -> None:
        self.assertError(
            self.request("models.delete", {"model": DICTIONARY_ID}),
            "model_not_downloaded",
        )


class ToggleAndDeleteTest(FuriganaEngineCase):
    def start_endless(self) -> int:
        response = self.request(
            "session.start", {"mode": "media", "device_id": ENDLESS_DEVICE}
        )
        session_id = int(response["result"]["session_id"])
        self.sink.wait(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == "running"
            )
        )
        return session_id

    def test_invalid_params(self) -> None:
        for params in ({}, {"enabled": "yes"}, {"enabled": True, "x": 1}):
            with self.subTest(params=params):
                self.assertError(
                    self.request("session.set_furigana", params), "invalid_params"
                )

    def test_toggle_while_running_and_delete_when_released(self) -> None:
        session_id = self.start_endless()
        result = self.request("session.set_furigana", {"enabled": True})["result"]
        self.assertEqual(result["session_id"], session_id)
        # 假的「人聲」不會出現在無聲的裝置上：直接送一句日文字幕給服務
        service = self.server._furigana
        self.assertEqual(
            service.annotate(session_id, "日本語の字幕", "ja"),
            [(0, 3, "にほんご"), (4, 6, "じまく")],
        )
        self.assertTrue(self.request("models.list")["result"]["furigana"]["in_use"])
        self.assertError(
            self.request("models.delete", {"model": DICTIONARY_ID}), "model_in_use"
        )
        # 關閉：立即釋放，之後可以刪除
        self.request("session.set_furigana", {"enabled": False})
        self.assertTrue(self.furigana.opened[0].closed)
        self.assertIsNone(service.annotate(session_id, "日本語", "ja"))
        response = self.request("models.delete", {"model": DICTIONARY_ID})
        self.assertEqual(
            response["result"],
            {"model": DICTIONARY_ID, "freed_bytes": FAKE_FURIGANA_SIZE},
        )
        self.request("session.stop")

    def test_deleting_turns_furigana_off(self) -> None:
        # 執行中、振假名開著但還沒遇到日文（辭典沒開啟）：可以刪，設定一併關閉
        self.start_endless()
        self.request("session.set_furigana", {"enabled": True})
        response = self.request("models.delete", {"model": DICTIONARY_ID})
        self.assertIn("result", response, response)
        settings = self.request("settings.get")["result"]["settings"]
        self.assertFalse(settings["furigana"]["enabled"])
        self.request("session.stop")

    def test_stop_releases_the_dictionary(self) -> None:
        session_id = self.start_endless()
        self.request("session.set_furigana", {"enabled": True})
        self.server._furigana.annotate(session_id, "勉強", "ja")
        self.assertTrue(self.server._furigana.in_use)
        self.request("session.stop")
        self.assertFalse(self.server._furigana.in_use)
        self.assertTrue(self.furigana.opened[0].closed)


class FuriganaServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.provider = FakeFurigana(installed=True)
        self.service = FuriganaService(self.provider)

    def test_only_the_current_session_and_japanese(self) -> None:
        self.service.begin_session(1, True)
        self.assertIsNone(self.service.annotate(2, "今日", "ja"))
        self.assertIsNone(self.service.annotate(1, "今日", "zh"))
        self.assertEqual(self.provider.opened, [])
        self.assertEqual(self.service.annotate(1, "今日", "ja"), [(0, 2, "きょう")])
        self.assertEqual(len(self.provider.opened), 1)

    def test_disabled_does_not_load(self) -> None:
        self.service.begin_session(1, False)
        self.assertIsNone(self.service.annotate(1, "今日", "ja"))
        self.assertEqual(self.provider.opened, [])

    def test_late_subtitle_of_an_ended_session_does_not_reload(self) -> None:
        self.service.begin_session(1, True)
        self.service.annotate(1, "今日", "ja")
        self.service.end_session(1)
        self.assertTrue(self.provider.opened[0].closed)
        self.assertIsNone(self.service.annotate(1, "今日", "ja"))
        self.assertEqual(len(self.provider.opened), 1)

    def test_delete_while_open_is_refused(self) -> None:
        self.service.begin_session(1, True)
        self.service.annotate(1, "今日", "ja")
        with self.assertRaises(DictionaryInUseError):
            self.service.delete_dictionary()
        self.service.set_enabled(False)
        self.assertEqual(self.service.delete_dictionary(), FAKE_FURIGANA_SIZE)

    def test_analysis_failure_drops_ruby_but_not_the_subtitle(self) -> None:
        self.service.begin_session(1, True)
        self.service.annotate(1, "今日", "ja")
        analyzer = self.provider.opened[0]
        with (
            mock.patch.object(analyzer, "ruby", side_effect=RuntimeError("boom")),
            self.assertLogs("kikitori.engine.furigana_service", "ERROR"),
        ):
            self.assertIsNone(self.service.annotate(1, "今日", "ja"))
        self.assertTrue(analyzer.closed)
        # 同一個 session 不再重試
        self.assertIsNone(self.service.annotate(1, "今日", "ja"))
        self.assertEqual(len(self.provider.opened), 1)


if __name__ == "__main__":
    unittest.main()
