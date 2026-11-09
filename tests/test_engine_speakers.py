"""engine 的說話者標籤：`models.list` 的 `speaker_model`、下載（`kind: "speaker"`）、刪除，
以及影片翻譯模式的完整字幕帶 `voice`（`docs/specs/speaker-labels.md`）。

同行程建立 `EngineServer`（假服務，假 labeler 輪流標 1、2…），等待一律是「等到某則訊息出現」。
"""

import dataclasses
import json
import os
import tempfile
import threading
import unittest
from typing import Any
from unittest import mock

from kikitori.engine.io import MessageWriter
from kikitori.engine.server import EngineServer, subtitle_event
from kikitori.engine.services import ProgressCallback
from kikitori.engine.testing import (
    FAKE_SPEAKER_MODEL_SIZE,
    SPEECH_DEVICE,
    FakeSpeakerModel,
    fake_services,
)
from kikitori.pipeline import SubtitleUpdate
from kikitori.speakers.model import SPEAKER_MODEL_ID
from tests.test_engine_furigana import Message, _Sink


class SpeakerEngineCase(unittest.TestCase):
    installed = True

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        env = mock.patch.dict(os.environ, {"KIKITORI_CONFIG_DIR": tmp.name})
        env.start()
        self.addCleanup(env.stop)
        services = fake_services()
        self.model = services.speaker_model
        assert isinstance(self.model, FakeSpeakerModel)
        if self.installed:
            self.model.install(lambda done, total: None, threading.Event())
        self.services = services
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

    def run_speech_session(self, mode: str = "media") -> list[Message]:
        response = self.request(
            "session.start", {"mode": mode, "device_id": SPEECH_DEVICE}
        )
        session_id = int(response["result"]["session_id"])
        self.sink.wait(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["session_id"] == session_id
                and m["data"]["state"] == "idle"
            )
        )
        return self.sink.events("subtitle")

    def assertError(self, response: Message, code: str) -> None:
        self.assertIn("error", response, response)
        self.assertEqual(response["error"]["code"], code, response)


class SubtitleVoiceTest(SpeakerEngineCase):
    def test_off_by_default(self) -> None:
        subtitles = self.run_speech_session()
        self.assertTrue(subtitles)
        self.assertTrue(all("voice" not in d for d in subtitles))

    def test_media_finals_carry_the_voice(self) -> None:
        self.request("settings.set", {"speakers": {"enabled": True, "count": 3}})
        subtitles = self.run_speech_session()
        finals = [d for d in subtitles if d["is_final"] and d["original"]]
        partials = [d for d in subtitles if not d["is_final"]]
        self.assertTrue(finals and partials)
        self.assertEqual([d["voice"] for d in finals], list(range(1, len(finals) + 1)))
        self.assertTrue(all("voice" not in d for d in partials))

    def test_interpret_mode_has_no_voice(self) -> None:
        self.request("settings.set", {"speakers": {"enabled": True}})
        subtitles = self.run_speech_session("interpret")
        self.assertTrue(subtitles)
        self.assertTrue(all("voice" not in d for d in subtitles))

    def test_subtitle_event_omits_a_missing_voice(self) -> None:
        update = SubtitleUpdate(1, "a", "b", "ja", "zh-TW", True, 0, 10, voice=None)
        self.assertNotIn("voice", subtitle_event(update, 1))
        labelled = dataclasses.replace(update, voice=2)
        self.assertEqual(subtitle_event(labelled, 1)["voice"], 2)


class SpeakerModelTest(SpeakerEngineCase):
    installed = False

    def test_enabled_without_the_model_shows_plain_subtitles(self) -> None:
        self.request("settings.set", {"speakers": {"enabled": True}})
        subtitles = self.run_speech_session()
        self.assertTrue(subtitles)
        self.assertTrue(all("voice" not in d for d in subtitles))

    def test_list_download_and_delete(self) -> None:
        listed = self.request("models.list")["result"]["speaker_model"]
        self.assertEqual(listed["id"], SPEAKER_MODEL_ID)
        self.assertFalse(listed["installed"])
        self.assertIsNone(listed["path"])
        self.assertEqual(listed["download_bytes"], FAKE_SPEAKER_MODEL_SIZE)
        self.assertIn("Apache-2.0", listed["license"])

        response = self.request("models.download", {"model": SPEAKER_MODEL_ID})
        self.assertEqual(
            response["result"], {"model": SPEAKER_MODEL_ID, "started": True}
        )
        done = self.sink.wait(
            lambda m: (
                m.get("event") == "download_progress" and m["data"]["state"] == "done"
            )
        )["data"]
        self.assertEqual(done["kind"], "speaker")
        self.assertEqual(done["model"], SPEAKER_MODEL_ID)
        self.assertTrue(
            self.request("models.list")["result"]["speaker_model"]["installed"]
        )

        self.request("settings.set", {"speakers": {"enabled": True}})
        response = self.request("models.delete", {"model": SPEAKER_MODEL_ID})
        self.assertEqual(
            response["result"],
            {"model": SPEAKER_MODEL_ID, "freed_bytes": FAKE_SPEAKER_MODEL_SIZE},
        )
        # 沒有模型時一併關閉，設定與畫面才一致
        settings = self.request("settings.get")["result"]["settings"]
        self.assertFalse(settings["speakers"]["enabled"])
        self.assertFalse(
            self.request("models.list")["result"]["speaker_model"]["installed"]
        )

    def test_delete_not_downloaded(self) -> None:
        self.assertError(
            self.request("models.delete", {"model": SPEAKER_MODEL_ID}),
            "model_not_downloaded",
        )

    def test_delete_during_a_download_is_refused(self) -> None:
        started = threading.Event()
        release = threading.Event()

        def slow(progress: ProgressCallback, cancel: threading.Event) -> None:
            started.set()
            release.wait(10)

        with mock.patch.object(self.model, "install", slow):
            self.request("models.download", {"model": SPEAKER_MODEL_ID})
            self.assertTrue(started.wait(10))
            self.assertError(
                self.request("models.delete", {"model": SPEAKER_MODEL_ID}),
                "model_in_use",
            )
            release.set()
            self.sink.wait(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["state"] in ("done", "error")
                )
            )

    def test_failed_download_reports_an_error_event(self) -> None:
        def broken(progress: ProgressCallback, cancel: threading.Event) -> None:
            raise RuntimeError("simulated network failure")

        with mock.patch.object(self.model, "install", broken):
            self.request("models.download", {"model": SPEAKER_MODEL_ID})
            error = self.sink.wait(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["state"] == "error"
                )
            )["data"]
        self.assertEqual(error["kind"], "speaker")
        self.assertIn("simulated network failure", error["error"])


if __name__ == "__main__":
    unittest.main()
