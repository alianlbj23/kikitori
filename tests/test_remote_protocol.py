"""遠端運算的訊息格式：嚴格驗證 JSON 控制訊息與二進位音訊訊框（docs/specs/remote-compute.md）。"""

import json
import unittest

import numpy as np

from kikitori.config import Settings
from kikitori.remote import protocol as rp


def msg(**fields: object) -> str:
    return json.dumps(fields)


class AudioFrameTest(unittest.TestCase):
    def test_round_trip(self) -> None:
        audio = np.arange(-800, 800, dtype=np.int16)
        frame = rp.encode_audio(7, 48_000, audio)
        decoded = rp.decode_audio(frame)
        self.assertEqual((decoded.seq, decoded.start_sample), (7, 48_000))
        np.testing.assert_array_equal(decoded.audio, audio)
        self.assertEqual(decoded.audio.dtype, np.int16)

    def test_rejects_malformed_frames(self) -> None:
        good = rp.encode_audio(1, 0, np.zeros(160, dtype=np.int16))
        for bad in (
            b"",
            good[:5],  # 標頭不完整
            good + b"\x00",  # 奇數位元組
            rp.encode_audio(1, 0, np.zeros(0, dtype=np.int16)),  # 沒有音訊
            good[:12] + b"\x00\x00" * (rp.MAX_AUDIO_FRAME_SAMPLES + 1),  # 太長
        ):
            with self.subTest(size=len(bad)), self.assertRaises(rp.ProtocolViolation):
                rp.decode_audio(bad)
        with self.assertRaises(ValueError):
            rp.encode_audio(-1, 0, np.zeros(10, dtype=np.int16))
        with self.assertRaises(ValueError):
            rp.encode_audio(1, 0, np.zeros(10, dtype=np.float32))


class ClientMessageTest(unittest.TestCase):
    def test_hello(self) -> None:
        m = rp.parse_client(
            msg(type="hello", protocol_version=rp.PROTOCOL_VERSION, app_version="0.3.1")
        )
        self.assertIsInstance(m, rp.Hello)
        self.assertEqual(m.protocol_version, rp.PROTOCOL_VERSION)

    def test_session_start_takes_only_whitelisted_settings(self) -> None:
        patch = {
            "source_language": "ja",
            "target_language": "zh-TW",
            "translation": {
                "enabled": True,
                "session_info": "主播：テスト",
                "glossary": {"builtin": True},
            },
            "speakers": {"enabled": True, "count": 3},
            "media": {"pause_secs": 0.6},
        }
        m = rp.parse_client(msg(type="session.start", mode="media", settings=patch))
        self.assertIsInstance(m, rp.SessionStart)
        merged = rp.session_settings(Settings(), m)
        self.assertEqual(merged.target_language, "zh-TW")
        self.assertEqual(merged.translation.session_info, "主播：テスト")
        self.assertEqual(merged.speakers.count, 3)

    def test_session_start_refuses_what_would_reconfigure_the_server(self) -> None:
        # 翻譯伺服器網址、模型、金鑰環境變數、裝置、遠端設定：A 不能改 B 的這些（SSRF、任意模型）
        for patch in (
            {"translation": {"base_url": "http://169.254.169.254/"}},
            {"translation": {"api_key_env": "AWS_SECRET_ACCESS_KEY"}},
            {"translation": {"final_model": "evil:latest"}},
            {"asr": {"final_model": "large-v3"}},
            {"compute": {"server_enabled": True}},
            {"ui": {}},
            {"media": {"device_id": "x"}},
        ):
            with self.subTest(patch=patch), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(msg(type="session.start", mode="media", settings=patch))

    def test_session_start_settings_are_validated_like_local_ones(self) -> None:
        m = rp.parse_client(
            msg(
                type="session.start",
                mode="media",
                settings={"target_language": "klingon"},
            )
        )
        with self.assertRaises(rp.ProtocolViolation):
            rp.session_settings(Settings(), m)

    def test_other_client_messages(self) -> None:
        self.assertIsInstance(rp.parse_client(msg(type="session.stop")), rp.SessionStop)
        t = rp.parse_client(msg(type="session.set_translation", enabled=False))
        self.assertIsInstance(t, rp.SetTranslation)
        self.assertFalse(t.enabled)
        p = rp.parse_client(msg(type="ping", nonce=12))
        self.assertEqual(p.nonce, 12)

    def test_rejects_malformed_client_messages(self) -> None:
        for raw in (
            "not json",
            "[]",
            "{}",
            msg(type="exec", cmd="rm -rf /"),
            msg(type="hello", protocol_version="1", app_version="x"),
            msg(type="hello", protocol_version=1, app_version="x", extra=1),
            msg(type="session.start", mode="karaoke", settings={}),
            msg(type="session.start", mode="media", settings=[]),
            msg(type="session.set_translation", enabled="yes"),
            msg(type="ping", nonce=-1),
            msg(type="hello", protocol_version=1, app_version="x" * 100),
            "x" * (rp.MAX_TEXT_BYTES + 1),
        ):
            with self.subTest(raw=raw[:60]), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(raw)


class ServerMessageTest(unittest.TestCase):
    def test_round_trip_of_server_messages(self) -> None:
        cases = [
            rp.welcome("0.3.1", {"fast": "small", "final": "large-v3"}),
            rp.busy("another computer is connected"),
            rp.status(
                "loading", None, None, {"step": "asr_fast", "done": 1, "total": 2}
            ),
            rp.event(
                "subtitle", {"utterance_id": 1, "original": "a", "is_final": True}
            ),
            rp.ack(16000),
            rp.pong(12),
        ]
        for text in cases:
            with self.subTest(text=text[:40]):
                m = rp.parse_server(text)
                self.assertEqual(json.loads(text)["type"], m["type"])

    def test_rejects_unknown_or_malformed_server_messages(self) -> None:
        for raw in (
            "[]",
            msg(type="shell"),
            msg(type="ack", sample=-5),
            msg(type="event", name="x", data=1),
        ):
            with self.subTest(raw=raw), self.assertRaises(rp.ProtocolViolation):
                rp.parse_server(raw)

    def test_status_from_the_server_is_validated(self) -> None:
        # B 的回傳是不可信輸入：code 只收白名單、detail 有長度上限、data 只收載入進度
        ok = (
            msg(type="status", state="running", detail=None, code=None, data=None),
            msg(type="status", state="error", detail="x", code="model_not_downloaded"),
            msg(
                type="status",
                state="loading",
                data={"step": "asr_final", "done": 2, "total": 3},
            ),
        )
        for raw in ok:
            with self.subTest(ok=raw):
                rp.parse_server(raw)
        bad = (
            msg(type="status", state="error", code="run_shell"),
            msg(type="status", state="error", detail="x" * (rp.MAX_DETAIL_CHARS + 1)),
            msg(type="status", state="error", detail=5),
            msg(
                type="status",
                state="loading",
                data={"step": "a", "done": 3, "total": 2},
            ),
            msg(type="status", state="loading", data={"step": "a", "done": 1}),
            msg(type="status", state="loading", data={"url": "http://x"}),
            msg(type="status", state="idle", extra=1),
        )
        for raw in bad:
            with self.subTest(bad=raw), self.assertRaises(rp.ProtocolViolation):
                rp.parse_server(raw)

    def test_resume_messages(self) -> None:
        # T12：斷線續接
        parsed = rp.parse_client(rp.client_session_resume(3, 17))
        self.assertEqual(parsed, rp.SessionResume(3, 17))
        for text in (
            rp.session_started(3),
            rp.resumed(3, 160000, "running"),
            rp.resume_failed("too late"),
        ):
            with self.subTest(text=text):
                rp.parse_server(text)
        for raw in (
            msg(type="session.resume", session_id=0, last_final_id=0),
            msg(type="session.resume", session_id=1),
            msg(type="session.resume", session_id=1, last_final_id=-1, x=1),
        ):
            with self.subTest(bad=raw), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(raw)
        for raw in (
            msg(type="resumed", session_id=1, sample=-1, state="running"),
            msg(type="resumed", session_id=1, sample=5, state="sleeping"),
            msg(type="session.started"),
        ):
            with self.subTest(bad=raw), self.assertRaises(rp.ProtocolViolation):
                rp.parse_server(raw)

    def test_odd_json_values_are_protocol_violations(self) -> None:
        # 審查第 4、5 點：不可雜湊的值（list／dict）做成員判斷會丟 TypeError、深層巢狀會丟
        # RecursionError——都必須變成 ProtocolViolation（連線層據此關閉），不能變成未處理例外
        for raw in (
            msg(type="status", state=[1]),
            msg(type="status", state="error", code={"a": 1}),
            msg(type="event", name=["subtitle"], data={}),
            msg(type="resumed", session_id=1, sample=0, state={"x": 1}),
            "[" * 100_000 + "]" * 100_000,
        ):
            with self.subTest(raw=raw[:40]), self.assertRaises(rp.ProtocolViolation):
                rp.parse_server(raw)
        for raw in (
            msg(type="session.start", mode=["media"], settings={}),
            '{"type": "ping", "nonce": ' + "[" * 100_000 + "]" * 100_000 + "}",
        ):
            with self.subTest(raw=raw[:40]), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(raw)

    def test_long_details_are_truncated_when_sent(self) -> None:
        text = rp.status("error", "e" * 5000, "load_error")
        self.assertEqual(len(rp.parse_server(text)["detail"]), rp.MAX_DETAIL_CHARS)


if __name__ == "__main__":
    unittest.main()
