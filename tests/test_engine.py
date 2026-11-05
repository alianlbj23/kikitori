"""engine 的測試：以子行程執行 `python -m kikitori engine`（假模式）交換 NDJSON。

`KIKITORI_ENGINE_FAKE=1` 讓引擎用合成音訊與假的 ASR／翻譯，`KIKITORI_CONFIG_DIR`
指到暫存目錄，不碰使用者真正的設定。另以同行程測試協定解析與寫出器。
等待一律是「等到某則訊息出現」加上逾時，不依賴 sleep 的時序。
"""

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import unittest
from collections.abc import Callable
from pathlib import Path
from typing import Any

from kikitori.engine.io import (
    LINE_TOO_LONG,
    ChannelClosedError,
    MessageWriter,
    read_lines,
)
from kikitori.engine.protocol import MAX_LINE_BYTES, ProtocolError, parse_request

WAIT = 15.0

Message = dict[str, Any]


class EngineProcess:
    """一個假模式引擎子行程與它的訊息紀錄。"""

    def __init__(self, config_dir: str) -> None:
        env = dict(os.environ)
        env["KIKITORI_ENGINE_FAKE"] = "1"
        env["KIKITORI_CONFIG_DIR"] = config_dir
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "kikitori", "engine"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
        )
        self.seen: list[Message] = []
        self.bad_lines: list[bytes] = []
        self.stderr: list[bytes] = []
        self._queue: queue.Queue[Message | None] = queue.Queue()
        self._backlog: list[Message] = []
        self._next_id = 1000
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.bad_lines.append(line)
                continue
            self.seen.append(message)
            self._queue.put(message)
        self._queue.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr.append(line)

    def send_raw(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        self.proc.stdin.write(data)
        self.proc.stdin.flush()

    def send(self, message: Message) -> None:
        self.send_raw(json.dumps(message).encode("utf-8") + b"\n")

    def wait_for(
        self, predicate: Callable[[Message], bool], timeout: float = WAIT
    ) -> Message:
        """回傳第一則符合條件、且還沒被取走的訊息。"""
        for i, message in enumerate(self._backlog):
            if predicate(message):
                return self._backlog.pop(i)
        try:
            while True:
                message = self._queue.get(timeout=timeout)
                if message is None:
                    raise AssertionError(
                        f"engine exited; stderr:\n{self.stderr_text()}"
                    )
                if predicate(message):
                    return message
                self._backlog.append(message)
        except queue.Empty:
            raise AssertionError(
                f"timed out waiting for message; seen={self.seen[-10:]}\nstderr:\n"
                f"{self.stderr_text()}"
            ) from None

    def request(self, method: str, params: Message | None = None) -> Message:
        self._next_id += 1
        request_id = self._next_id
        message: Message = {"id": request_id, "method": method}
        if params is not None:
            message["params"] = params
        self.send(message)
        return self.wait_for(lambda m: m.get("id") == request_id and "event" not in m)

    def wait_event(
        self, name: str, where: Callable[[Message], bool] = lambda d: True
    ) -> Message:
        return self.wait_for(lambda m: m.get("event") == name and where(m["data"]))[
            "data"
        ]

    def stderr_text(self) -> str:
        return b"".join(self.stderr).decode("utf-8", "replace")[-4000:]

    def close(self) -> int:
        if self.proc.poll() is None:
            assert self.proc.stdin is not None
            try:
                self.proc.stdin.close()
            except OSError:
                pass  # 行程已自行結束、管線已關閉；下面的 wait 會取得結束碼
            try:
                return self.proc.wait(WAIT)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(WAIT)
                raise
        return self.proc.returncode

    def dispose(self) -> None:
        """結束行程並關閉三條管線。"""
        self.close()
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream is not None and not stream.closed:
                stream.close()


class EngineTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.config_dir = Path(self._tmp.name)
        self.engine = EngineProcess(str(self.config_dir))
        self.addCleanup(self._close)

    def _close(self) -> None:
        self.engine.dispose()
        self.assertEqual(self.engine.bad_lines, [], "stdout must only carry JSON lines")

    def assertError(self, response: Message, code: str) -> None:
        self.assertIn("error", response, response)
        self.assertEqual(response["error"]["code"], code, response)
        self.assertIsInstance(response["error"]["message"], str)


class BasicProtocolTest(EngineTestCase):
    def test_startup_status_then_info(self) -> None:
        first = self.engine.wait_for(lambda m: True)
        self.assertEqual(
            first,
            {
                "event": "status",
                "data": {
                    "state": "idle",
                    "detail": None,
                    "session_id": None,
                    "code": None,
                },
            },
        )
        result = self.engine.request("engine.info")["result"]
        self.assertEqual(result["protocol_version"], 1)
        self.assertEqual(result["name"], "kikitori-engine")
        self.assertTrue(result["fake"])
        self.assertIn(result["platform"], ("windows", "macos", "linux", sys.platform))
        self.assertIsInstance(result["version"], str)

    def test_unknown_method(self) -> None:
        self.assertError(self.engine.request("engine.fly"), "unknown_method")

    def test_malformed_lines_do_not_crash(self) -> None:
        cases: list[tuple[bytes, str, int | None]] = [
            (b"not json\n", "parse_error", None),
            (b"\xff\xfe\n", "parse_error", None),
            (b"[1, 2]\n", "invalid_request", None),
            (b'{"id": "x", "method": "engine.info"}\n', "invalid_request", None),
            (b'{"id": true, "method": "engine.info"}\n', "invalid_request", None),
            (b'{"id": 5, "method": 3}\n', "invalid_request", 5),
            (
                b'{"id": 6, "method": "engine.info", "params": []}\n',
                "invalid_request",
                6,
            ),
            (b'{"id": 7, "method": "engine.info", "extra": 1}\n', "invalid_request", 7),
        ]
        for raw, code, expected_id in cases:
            with self.subTest(raw=raw):
                self.engine.send_raw(raw)
                response = self.engine.wait_for(lambda m: "error" in m)
                self.assertEqual(response["id"], expected_id)
                self.assertError(response, code)
        self.engine.send_raw(b"\n   \n")  # 空行略過，不回應
        long_line = b'{"id": 8, "method": "' + b"a" * (MAX_LINE_BYTES + 10) + b'"}\n'
        self.engine.send_raw(long_line)
        response = self.engine.wait_for(lambda m: "error" in m)
        self.assertEqual(response["id"], None)
        self.assertError(response, "invalid_request")
        # 之後仍正常運作
        self.assertIn("result", self.engine.request("engine.info"))

    def test_unknown_params_rejected(self) -> None:
        self.assertError(
            self.engine.request("engine.info", {"verbose": True}), "invalid_params"
        )

    def test_devices_list(self) -> None:
        devices = self.engine.request("devices.list")["result"]["devices"]
        self.assertEqual(
            [(d["id"], d["kind"], d["is_default"]) for d in devices],
            [
                ("fake:loopback:speech", "loopback", True),
                ("fake:microphone:endless", "microphone", True),
            ],
        )
        self.assertTrue(all(isinstance(d["name"], str) for d in devices))

    def test_hardware_detect(self) -> None:
        result = self.engine.request("hardware.detect")["result"]
        self.assertIsNone(result["recommendation"])
        hw = result["hardware"]
        self.assertIsInstance(hw["cpu_count"], int)
        self.assertTrue(hw["ram_bytes"] is None or hw["ram_bytes"] > 0)
        self.assertEqual(hw["gpus"], [])
        self.assertIsInstance(hw["apple_silicon"], bool)

    def test_models_list(self) -> None:
        result = self.engine.request("models.list")["result"]
        asr = {m["id"]: m for m in result["asr"]}
        self.assertTrue(asr["base"]["downloaded"])
        self.assertFalse(asr["fake-missing"]["downloaded"])
        self.assertEqual(
            set(asr["base"]),
            {"id", "size_bytes", "license", "speed_tier", "downloaded"},
        )
        translation = result["translation"]
        self.assertEqual(translation["installed"], ["qwen3.5:4b"])
        self.assertIsNone(translation["error"])
        presets = {p["model"]: p for p in translation["presets"]}
        self.assertTrue(presets["qwen3.5:4b"]["installed"])
        self.assertFalse(presets["qwen3.5:9b"]["installed"])
        self.assertIn("license", presets["qwen3.5:9b"])


class SettingsProtocolTest(EngineTestCase):
    def test_get_defaults_and_path(self) -> None:
        result = self.engine.request("settings.get")["result"]
        self.assertEqual(result["settings"]["schema_version"], 1)
        self.assertEqual(result["settings"]["media"]["pause_secs"], 0.6)
        self.assertEqual(Path(result["path"]), self.config_dir / "settings.json")

    def test_set_persists_and_validates(self) -> None:
        result = self.engine.request(
            "settings.set", {"partner_language": "en", "subtitle": {"font_size": 30}}
        )["result"]
        self.assertEqual(result["settings"]["partner_language"], "en")
        saved = json.loads(
            (self.config_dir / "settings.json").read_text(encoding="utf-8")
        )
        self.assertEqual(saved["subtitle"], {"font_size": 30})

        bad = self.engine.request("settings.set", {"media": {"pause_secs": "slow"}})
        self.assertError(bad, "invalid_settings")
        self.assertIn("media.pause_secs", bad["error"]["message"])
        unknown = self.engine.request("settings.set", {"colour": "red"})
        self.assertError(unknown, "invalid_settings")
        after = self.engine.request("settings.get")["result"]["settings"]
        self.assertEqual(after["partner_language"], "en")
        self.assertEqual(after["media"]["pause_secs"], 0.6)

    def test_invalid_settings_file_is_reported(self) -> None:
        # 先關掉 setUp 的行程，寫入壞掉的設定檔再重新啟動。
        self.engine.dispose()
        (self.config_dir / "settings.json").write_text("{broken", encoding="utf-8")
        self.engine = EngineProcess(str(self.config_dir))
        log = self.engine.wait_event("log", lambda d: d["level"] == "warning")
        self.assertIn("invalid", log["message"])
        result = self.engine.request("settings.get")["result"]
        self.assertEqual(result["settings"]["mode"], "media")


class DownloadProtocolTest(EngineTestCase):
    def test_download_reports_progress_and_completes(self) -> None:
        response = self.engine.request("models.download", {"model": "fake-missing"})
        self.assertEqual(response["result"], {"model": "fake-missing", "started": True})
        first = self.engine.wait_event("download_progress")
        self.assertEqual(
            (first["downloaded_bytes"], first["state"]), (0, "downloading")
        )
        done = self.engine.wait_event(
            "download_progress", lambda d: d["state"] != "downloading"
        )
        self.assertEqual(done["state"], "done")
        self.assertEqual(done["downloaded_bytes"], done["total_bytes"])
        asr = {m["id"]: m for m in self.engine.request("models.list")["result"]["asr"]}
        self.assertTrue(asr["fake-missing"]["downloaded"])

    def test_download_failure_is_an_event(self) -> None:
        self.engine.request("models.download", {"model": "fake-broken"})
        failed = self.engine.wait_event(
            "download_progress", lambda d: d["state"] != "downloading"
        )
        self.assertEqual(failed["state"], "error")
        self.assertIn("simulated network failure", failed["error"])

    def test_download_param_errors(self) -> None:
        self.assertError(self.engine.request("models.download", {}), "invalid_params")
        self.assertError(
            self.engine.request("models.download", {"model": 3}), "invalid_params"
        )
        self.assertError(
            self.engine.request("models.download", {"model": "huge-v9"}),
            "unknown_model",
        )


class SessionProtocolTest(EngineTestCase):
    def test_media_session_streams_subtitles_then_idle(self) -> None:
        response = self.engine.request("session.start", {"mode": "media"})
        session_id = response["result"]["session_id"]
        self.engine.wait_event("status", lambda d: d["state"] == "loading")
        self.engine.wait_event("status", lambda d: d["state"] == "running")
        idle = self.engine.wait_event(
            "status", lambda d: d["state"] == "idle" and d["session_id"] == session_id
        )
        self.assertIsNone(idle["code"])
        # 回應先於這個 session 的任何事件
        index = self.engine.seen.index(response)
        loading = next(
            i
            for i, m in enumerate(self.engine.seen)
            if m.get("event") == "status" and m["data"]["state"] == "loading"
        )
        self.assertLess(index, loading)

        subtitles = [
            m["data"] for m in self.engine.seen if m.get("event") == "subtitle"
        ]
        finals = [s for s in subtitles if s["is_final"]]
        self.assertEqual([s["utterance_id"] for s in finals], [1, 2])
        self.assertEqual(finals[0]["translation"], "[zh-TW] final1")
        self.assertEqual(
            (finals[0]["source_language"], finals[0]["target_language"]),
            ("ja", "zh-TW"),
        )
        self.assertTrue(all(s["session_id"] == session_id for s in subtitles))
        expected_keys = {
            "utterance_id",
            "original",
            "translation",
            "source_language",
            "target_language",
            "is_final",
            "start_sample",
            "end_sample",
            "session_id",
        }
        self.assertTrue(all(set(s) == expected_keys for s in subtitles))
        # 同一句的即時字幕都在完整字幕之前
        for final in finals:
            final_index = subtitles.index(final)
            later = [
                s
                for s in subtitles[final_index + 1 :]
                if s["utterance_id"] == final["utterance_id"]
            ]
            self.assertEqual(later, [])

    def test_interpret_routes_chinese_to_partner(self) -> None:
        self.engine.request("settings.set", {"partner_language": "en"})
        response = self.engine.request(
            "session.start", {"mode": "interpret", "device_id": "fake:loopback:speech"}
        )
        session_id = response["result"]["session_id"]
        self.engine.wait_event(
            "status", lambda d: d["state"] == "idle" and d["session_id"] == session_id
        )
        finals = [
            m["data"]
            for m in self.engine.seen
            if m.get("event") == "subtitle" and m["data"]["is_final"]
        ]
        self.assertEqual(finals[0]["target_language"], "en")
        self.assertEqual(finals[0]["translation"], "[en] final1")

    def test_session_active_and_stop(self) -> None:
        first = self.engine.request("session.start", {"mode": "interpret"})["result"][
            "session_id"
        ]
        self.engine.wait_event("status", lambda d: d["state"] == "running")
        self.assertError(
            self.engine.request("session.start", {"mode": "media"}), "session_active"
        )
        self.assertEqual(self.engine.request("session.stop")["result"], {})
        self.engine.wait_event(
            "status", lambda d: d["state"] == "idle" and d["session_id"] == first
        )
        # 停止後可以再開新的 session；沒有 session 時 stop 也回 {}
        second = self.engine.request("session.start", {"mode": "interpret"})["result"][
            "session_id"
        ]
        self.assertEqual(second, first + 1)
        self.engine.wait_event(
            "status", lambda d: d["state"] == "running" and d["session_id"] == second
        )
        self.assertEqual(self.engine.request("session.stop")["result"], {})
        self.assertEqual(self.engine.request("session.stop")["result"], {})

    def test_start_param_errors(self) -> None:
        self.assertError(self.engine.request("session.start", {}), "invalid_params")
        self.assertError(
            self.engine.request("session.start", {"mode": "karaoke"}), "invalid_params"
        )
        self.assertError(
            self.engine.request("session.start", {"mode": "media", "device_id": ""}),
            "invalid_params",
        )
        self.assertError(
            self.engine.request(
                "session.start", {"mode": "media", "device_id": "fake:nope"}
            ),
            "device_not_found",
        )
        self.assertError(
            self.engine.request("session.start", {"mode": "media", "speed": 2}),
            "invalid_params",
        )

    def test_missing_model_is_an_error_status(self) -> None:
        self.engine.request("settings.set", {"asr": {"final_model": "fake-missing"}})
        session_id = self.engine.request("session.start", {"mode": "media"})["result"][
            "session_id"
        ]
        error = self.engine.wait_event("status", lambda d: d["state"] == "error")
        self.assertEqual(
            (error["session_id"], error["code"]), (session_id, "model_not_downloaded")
        )
        self.assertIn("fake-missing", error["detail"])
        # 失敗的 session 已釋放，可以再開
        self.engine.request("settings.set", {"asr": {"final_model": "base"}})
        self.assertIn("result", self.engine.request("session.start", {"mode": "media"}))


class LifecycleTest(EngineTestCase):
    def test_shutdown_with_running_session(self) -> None:
        self.engine.request("session.start", {"mode": "interpret"})
        self.engine.wait_event("status", lambda d: d["state"] == "running")
        self.assertEqual(self.engine.request("engine.shutdown")["result"], {})
        self.assertEqual(self.engine.proc.wait(WAIT), 0)
        self.engine.wait_event(
            "status", lambda d: d["state"] == "idle" and d["session_id"] == 1
        )

    def test_stdin_eof_exits_cleanly(self) -> None:
        self.engine.wait_event("status")
        self.assertEqual(self.engine.close(), 0)


class ParseRequestTest(unittest.TestCase):
    def test_valid(self) -> None:
        r = parse_request(
            b'{"id": 1, "method": "settings.set", "params": {"mode": "media"}}'
        )
        self.assertEqual(
            (r.id, r.method, r.params), (1, "settings.set", {"mode": "media"})
        )
        self.assertEqual(
            parse_request(b'{"id": 2, "method": "a", "params": null}').params, {}
        )

    def test_huge_id_rejected(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            parse_request(b'{"id": 9007199254740993, "method": "a"}')
        self.assertEqual(ctx.exception.code, "invalid_request")

    def test_deep_nesting(self) -> None:
        with self.assertRaises(ProtocolError) as ctx:
            parse_request(b"[" * 100000)
        self.assertEqual(ctx.exception.code, "parse_error")


class ReadLinesTest(unittest.TestCase):
    def test_lines_and_too_long(self) -> None:
        data = b"a\r\n" + b"x" * 50 + b"\nb\nlast"
        lines = list(read_lines(io.BytesIO(data), max_bytes=10))
        self.assertEqual(lines, [b"a", LINE_TOO_LONG, b"b", b"last"])


class MessageWriterTest(unittest.TestCase):
    def test_one_json_per_line_utf8(self) -> None:
        out = io.BytesIO()
        writer = MessageWriter(out)
        writer.send({"event": "subtitle", "data": {"original": "こんにちは\n"}})
        lines = out.getvalue().split(b"\n")
        self.assertEqual(lines[1], b"")
        self.assertEqual(json.loads(lines[0])["data"]["original"], "こんにちは\n")
        self.assertIn("こんにちは".encode(), lines[0])

    def test_nan_rejected(self) -> None:
        with self.assertRaises(ValueError):
            MessageWriter(io.BytesIO()).send({"x": float("nan")})

    def test_closed_stream(self) -> None:
        out = io.BytesIO()
        out.close()
        writer = MessageWriter(out)
        with (
            self.assertLogs("kikitori.engine.io", "ERROR"),
            self.assertRaises(ChannelClosedError),
        ):
            writer.send({"a": 1})
        self.assertTrue(writer.broken)
        with self.assertRaises(ChannelClosedError):
            writer.send({"a": 2})

    def test_concurrent_writes_do_not_interleave(self) -> None:
        out = io.BytesIO()
        writer = MessageWriter(out)
        payload = "字" * 2000

        def worker(n: int) -> None:
            for i in range(50):
                writer.send({"n": n, "i": i, "p": payload})

        threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(WAIT)
        lines = out.getvalue().splitlines()
        self.assertEqual(len(lines), 200)
        self.assertTrue(all(json.loads(line)["p"] == payload for line in lines))


if __name__ == "__main__":
    unittest.main()
