"""遠端運算的訊息格式：嚴格驗證 JSON 控制訊息與二進位音訊訊框（docs/specs/remote-compute.md）。"""

import copy
import json
import unittest
from typing import Any

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
        self.assertEqual(m.intent, "session")  # 省略＝session（舊版 A）

    def test_hello_intent(self) -> None:
        # compute-capacity 三：probe 只查能力（不佔 GPU）；session 省略不送，舊版 B 照樣接受
        self.assertNotIn("intent", json.loads(rp.client_hello("0.3.1")))
        probe = rp.parse_client(rp.client_hello("0.3.1", "probe"))
        assert isinstance(probe, rp.Hello)
        self.assertEqual(probe.intent, "probe")
        session = rp.parse_client(
            msg(type="hello", protocol_version=1, app_version="0.3.1", intent="session")
        )
        assert isinstance(session, rp.Hello)
        self.assertEqual(session.intent, "session")
        for intent in ("admin", 1, None, ["probe"]):
            with self.subTest(intent=intent), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(
                    msg(
                        type="hello",
                        protocol_version=1,
                        app_version="0.3.1",
                        intent=intent,
                    )
                )

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
            {"asr": {"device": "cuda"}},
            {"asr": {"final_compute_type": "float32"}},
            {"asr": {"cuda_library_dirs": ["/tmp/evil"]}},
            {"compute": {"server_enabled": True}},
            {"ui": {}},
            {"media": {"device_id": "x"}},
        ):
            with self.subTest(patch=patch), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(msg(type="session.start", mode="media", settings=patch))

    def test_session_start_may_pick_the_asr_models(self) -> None:
        # compute-capacity 三：A 可以指定模型（只有 id；裝置、精度仍用 B 的），是否已下載由 B 判斷
        m = rp.parse_client(
            msg(
                type="session.start",
                mode="media",
                settings={"asr": {"final_model": "large-v3"}},
            )
        )
        assert isinstance(m, rp.SessionStart)
        self.assertEqual(rp.requested_models(m), {"final_model": "large-v3"})
        merged = rp.session_settings(Settings(), m)
        self.assertEqual(merged.asr.final_model, "large-v3")
        self.assertEqual(merged.asr.fast_model, Settings().asr.fast_model)
        self.assertEqual(merged.asr.device, Settings().asr.device)
        plain = rp.parse_client(rp.client_session_start("media", Settings()))
        assert isinstance(plain, rp.SessionStart)
        self.assertEqual(rp.requested_models(plain), {})
        for bad in ({"final_model": 5}, {"fast_model": ""}, {"final_model": "x" * 101}):
            with self.subTest(bad=bad), self.assertRaises(rp.ProtocolViolation):
                rp.parse_client(
                    msg(type="session.start", mode="media", settings={"asr": bad})
                )

    def test_client_session_start_sends_only_the_chosen_models(self) -> None:
        # 空字串＝照運算主機的設定：不送 asr（舊版 B 不認得這個欄位）
        plain = json.loads(rp.client_session_start("media", Settings()))
        self.assertNotIn("asr", plain["settings"])
        one = json.loads(
            rp.client_session_start("media", Settings(), final_model="large-v3")
        )
        self.assertEqual(one["settings"]["asr"], {"final_model": "large-v3"})
        both = json.loads(
            rp.client_session_start(
                "media", Settings(), fast_model="base", final_model="large-v3"
            )
        )
        self.assertEqual(
            both["settings"]["asr"], {"fast_model": "base", "final_model": "large-v3"}
        )

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


HOST: dict[str, Any] = {
    "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264},
    "device": "cuda",
    "compute_type": "int8_float16",
    "current": {"fast": "large-v3-turbo", "final": "large-v3"},
    "recommended": {"fast": "large-v3-turbo", "final": "large-v3"},
    "models": [
        {"id": "large-v3", "downloaded": True, "need_mb": 2140, "fit": "ok"},
        {"id": "small", "downloaded": False, "need_mb": 650, "fit": None},
    ],
    "translation_model": "qwen3.5:9b",
}


def with_host(**changes: Any) -> dict[str, Any]:
    host = copy.deepcopy(HOST)
    host.update(changes)
    return host


class HostInfoTest(unittest.TestCase):
    """compute-capacity 三：A 逐欄驗證 `welcome.host`，不合格整個當 null、連線照常。"""

    def parse(self, host: object) -> object:
        # 自己組 JSON：惡意的 B 不會經過 `welcome()`
        welcome = json.loads(rp.welcome("0.3.1", {"fast": "a", "final": "b"}))
        welcome["host"] = host
        return rp.parse_server(json.dumps(welcome))["host"]

    def test_a_good_host_info_round_trips(self) -> None:
        self.assertEqual(self.parse(HOST), HOST)
        cpu = with_host(gpu=None, recommended=None, device="cpu")
        self.assertEqual(self.parse(cpu), cpu)
        # 舊版 B 沒有 host：null
        self.assertIsNone(rp.parse_server(rp.welcome("0.3.1", {}))["host"])

    def test_unknown_fields_are_dropped(self) -> None:
        extra = with_host(script="<img onerror=x>")
        extra["models"][0]["exec"] = "rm -rf /"
        parsed = self.parse(extra)
        assert isinstance(parsed, dict)
        self.assertNotIn("script", parsed)
        self.assertNotIn("exec", parsed["models"][0])

    def test_malicious_values_make_the_whole_host_null(self) -> None:
        many = [
            {"id": f"m{i}", "downloaded": True, "need_mb": 1, "fit": "ok"}
            for i in range(rp.MAX_HOST_MODELS + 1)
        ]
        model = HOST["models"][0]
        bad = (
            [],
            "host",
            with_host(gpu={"name": "x" * 65, "vram_total_mb": 1}),
            with_host(gpu={"name": "", "vram_total_mb": 1}),
            with_host(gpu={"name": "GPU", "vram_total_mb": -1}),
            with_host(gpu={"name": "GPU", "vram_total_mb": 1_048_577}),
            with_host(gpu={"name": "GPU", "vram_total_mb": True}),
            with_host(gpu={"name": "GPU", "vram_total_mb": 1.5}),
            with_host(gpu={"name": "GPU"}),
            with_host(gpu={"name": "GPU\u202e", "vram_total_mb": 1}),
            with_host(device="tpu"),
            with_host(compute_type="<script>"),
            with_host(compute_type=None),
            with_host(current={"fast": "base"}),
            with_host(current={"fast": "../../etc", "final": "base"}),
            with_host(recommended={"fast": 1, "final": "base"}),
            with_host(models=many),
            with_host(models={"id": "base"}),
            with_host(models=[dict(model, id="../x")]),
            with_host(models=[dict(model, id="large-v3\n")]),
            with_host(compute_type="int8\n"),
            with_host(models=[dict(model, fit="great")]),
            with_host(models=[dict(model, need_mb=True)]),
            with_host(models=[dict(model, need_mb=-5)]),
            with_host(models=[dict(model, downloaded="yes")]),
            with_host(models=[model, model]),  # 重複的 id
            with_host(models=["large-v3"]),
            with_host(translation_model="x" * 201),
            with_host(translation_model="qwen\x00"),
            {k: v for k, v in HOST.items() if k != "device"},
        )
        for host in bad:
            with (
                self.subTest(host=str(host)[:80]),
                self.assertLogs("kikitori.remote.protocol", "WARNING"),
            ):
                self.assertIsNone(self.parse(host))

    def test_a_bad_host_does_not_break_the_welcome(self) -> None:
        with self.assertLogs("kikitori.remote.protocol", "WARNING"):
            m = rp.parse_server(
                json.dumps(
                    {
                        "type": "welcome",
                        "protocol_version": rp.PROTOCOL_VERSION,
                        "app_version": "0.3.1",
                        "models": {},
                        "host": {"device": float("nan")},
                    }
                )
            )
        self.assertEqual(m["type"], "welcome")
        self.assertIsNone(m["host"])


if __name__ == "__main__":
    unittest.main()
