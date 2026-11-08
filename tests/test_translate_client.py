"""OpenAICompatibleTranslator 的測試：用本機假的 OpenAI 相容伺服器，不連外網。

每個測試把要回的內容排進 `FakeServer.script`，再檢查翻譯結果與伺服器收到的請求。
"""

import json
import logging
import os
import socket
import threading
import time
import unittest
from collections.abc import Callable
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from unittest import mock

from kikitori.translate import client as client_module
from kikitori.translate.client import OpenAICompatibleTranslator, TranslatorConfig
from kikitori.translate.errors import (
    InvalidRequestError,
    TranslationConnectionError,
    TranslationError,
    TranslationHTTPError,
    TranslationResponseError,
    TranslationTimeoutError,
)
from kikitori.translate.glossary import Glossary, GlossaryEntry
from kikitori.translate.presets import preset_for_model
from kikitori.translate.types import TranslationRequest, Translator

SAKURA = "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
KEY_ENV = "KIKITORI_TEST_TRANSLATE_KEY"
SECRET = "sk-test-very-secret-123"

Action = Callable[["_Handler"], None]


@dataclass
class RecordedRequest:
    path: str
    headers: dict[str, str]
    body: dict[str, Any]


class _Handler(BaseHTTPRequestHandler):
    server: "_Server"

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        raw = self.rfile.read(length)
        self.server.fake.requests.append(
            RecordedRequest(
                path=self.path,
                headers={k.lower(): v for k, v in self.headers.items()},
                body=json.loads(raw),
            )
        )
        script = self.server.fake.script
        action = script.pop(0) if script else error(500, "no scripted response")
        action(self)

    def log_message(self, format: str, *args: Any) -> None:
        pass  # 測試輸出保持乾淨


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    block_on_close = False
    fake: "FakeServer"


class FakeServer:
    """在背景執行緒跑的假 OpenAI 相容伺服器。"""

    def __init__(self) -> None:
        self.script: list[Action] = []
        self.requests: list[RecordedRequest] = []
        self.release = threading.Event()  # 讓延遲中的處理器在測試結束時立刻返回
        self._server = _Server(("127.0.0.1", 0), _Handler)
        self._server.fake = self
        # poll_interval 預設 0.5 秒，會讓每個測試的 shutdown 多等半秒。
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            kwargs={"poll_interval": 0.02},
            daemon=True,
        )

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self._server.server_address[1]}/v1"

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self.release.set()
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)


def json_response(
    obj: Any, status: int = 200, headers: dict[str, str] | None = None
) -> Action:
    return raw_response(json.dumps(obj).encode(), status, "application/json", headers)


def raw_response(
    body: bytes,
    status: int = 200,
    content_type: str = "application/json",
    headers: dict[str, str] | None = None,
) -> Action:
    def act(h: _Handler) -> None:
        h.send_response(status)
        h.send_header("Content-Type", content_type)
        h.send_header("Content-Length", str(len(body)))
        for k, v in (headers or {}).items():
            h.send_header(k, v)
        h.end_headers()
        h.wfile.write(body)

    return act


def error(status: int, message: str, headers: dict[str, str] | None = None) -> Action:
    return json_response({"error": {"message": message}}, status, headers)


def completion(content: str | None, finish: str = "stop") -> Action:
    return json_response(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion",
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": finish,
                }
            ],
        }
    )


def chunk(
    content: str | None = None, reasoning: str | None = None, finish: str | None = None
) -> str:
    delta: dict[str, Any] = {}
    if content is not None:
        delta["content"] = content
    if reasoning is not None:
        delta["reasoning"] = reasoning
    return json.dumps(
        {
            "id": "chatcmpl-1",
            "object": "chat.completion.chunk",
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }
    )


def sse(
    events: list[str], done: bool = True, gap: float = 0.0, raw_prefix: str = ""
) -> Action:
    """以 SSE 送出每個事件；`gap` 秒的間隔用來測讀取逾時。"""

    def act(h: _Handler) -> None:
        h.send_response(200)
        h.send_header("Content-Type", "text/event-stream")
        h.end_headers()  # HTTP/1.0、沒有 Content-Length：以關閉連線結束內容
        if raw_prefix:
            h.wfile.write(raw_prefix.encode())
        for i, event in enumerate(events):
            if i and gap and h.server.fake.release.wait(gap):
                return
            h.wfile.write(f"data: {event}\n\n".encode())
            h.wfile.flush()
        if done:
            h.wfile.write(b"data: [DONE]\n\n")

    return act


def delayed(seconds: float, then: Action) -> Action:
    def act(h: _Handler) -> None:
        if not h.server.fake.release.wait(seconds):
            then(h)

    return act


class ClientTestCase(unittest.TestCase):
    def setUp(self) -> None:
        self.server = FakeServer()
        self.server.start()
        self.addCleanup(self.server.stop)
        self.sleeps: list[float] = []
        env = mock.patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(KEY_ENV, None)

    def make(
        self, model: str = "hy-mt1.5-7b:latest", **overrides: Any
    ) -> OpenAICompatibleTranslator:
        settings: dict[str, Any] = {
            "model": model,
            "base_url": self.server.base_url,
            "api_key_env": KEY_ENV,
            "connect_timeout": 2.0,
            "read_timeout": 2.0,
            "max_retries": 2,
            "retry_backoff": 0.5,
        }
        settings.update(overrides)
        return OpenAICompatibleTranslator(
            TranslatorConfig(**settings), sleep=self.sleeps.append
        )


class TranslateTest(ClientTestCase):
    def test_satisfies_translator_protocol(self) -> None:
        translator: Translator = self.make()
        self.assertTrue(callable(translator.translate) and callable(translator.stream))

    def test_success_returns_clean_translation_and_sends_expected_request(self) -> None:
        self.server.script.append(completion("  「早安」 "))
        out = self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(out, "早安")

        (req,) = self.server.requests
        self.assertEqual(req.path, "/v1/chat/completions")
        self.assertEqual(req.headers["content-type"], "application/json")
        self.assertEqual(req.body["model"], "hy-mt1.5-7b:latest")
        self.assertIs(req.body["stream"], False)
        self.assertEqual(req.body["temperature"], 0.0)
        self.assertEqual(req.body["top_p"], 0.6)
        self.assertEqual(req.body["max_tokens"], 256)
        self.assertNotIn("reasoning_effort", req.body)
        self.assertEqual(req.body["messages"][0]["role"], "user")
        self.assertIn("おはよう", req.body["messages"][0]["content"])

    def test_qwen_request_disables_thinking(self) -> None:
        self.server.script.append(completion("早安"))
        self.make("qwen3.5:4b").translate(TranslationRequest("おはよう", "ja"))
        body = self.server.requests[0].body
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertEqual(body["presence_penalty"], 1.5)
        self.assertEqual(body["messages"][0]["role"], "system")

    def test_temperature_override(self) -> None:
        self.server.script.append(completion("早安"))
        self.make(temperature=0.1).translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(self.server.requests[0].body["temperature"], 0.1)

    def test_explicit_preset_is_used(self) -> None:
        self.server.script.append(completion("早安"))
        translator = OpenAICompatibleTranslator(
            TranslatorConfig(model="my-qwen", base_url=self.server.base_url),
            preset=preset_for_model("qwen3.5:4b"),
        )
        translator.translate(TranslationRequest("おはよう", "ja"))
        body = self.server.requests[0].body
        self.assertEqual(body["model"], "my-qwen")
        self.assertEqual(body["reasoning_effort"], "none")

    def test_glossary_terms_present_in_source_go_into_the_prompt(self) -> None:
        self.server.script.extend([completion("謝謝超級留言"), completion("早安")])
        translator = OpenAICompatibleTranslator(
            TranslatorConfig(model="qwen3.5:9b", base_url=self.server.base_url),
            glossary=Glossary([GlossaryEntry("ぺこら", "佩克拉")]),
        )
        translator.translate(TranslationRequest("スパチャありがとう", "ja"))
        translator.translate(TranslationRequest("おはよう", "ja"))
        with_terms = self.server.requests[0].body["messages"][1]["content"]
        without = self.server.requests[1].body["messages"][1]["content"]
        self.assertIn("スパチャ → 超級留言", with_terms)
        self.assertNotIn("ぺこら", with_terms)
        self.assertNotIn("術語", without)

    def test_glossary_used_by_stream_and_absent_by_default(self) -> None:
        self.server.script.extend(
            [sse([chunk("推", finish="stop")]), sse([chunk("推", finish="stop")])]
        )
        list(
            OpenAICompatibleTranslator(
                TranslatorConfig(model="qwen3.5:9b", base_url=self.server.base_url),
                glossary=Glossary(),
            ).stream(TranslationRequest("推しが尊い", "ja", "en"))
        )
        list(
            self.make("qwen3.5:9b").stream(TranslationRequest("推しが尊い", "ja", "en"))
        )
        self.assertIn(
            "推し → oshi", self.server.requests[0].body["messages"][1]["content"]
        )
        self.assertNotIn(
            "Terminology", self.server.requests[1].body["messages"][1]["content"]
        )

    def test_think_tags_are_stripped(self) -> None:
        self.server.script.append(
            completion("<think>\nThe user wants...\n</think>\n\n早安")
        )
        self.assertEqual(
            self.make().translate(TranslationRequest("おはよう", "ja")), "早安"
        )

    def test_only_thinking_is_an_error(self) -> None:
        self.server.script.append(completion("<think>never finished"))
        with self.assertRaises(TranslationResponseError):
            self.make().translate(TranslationRequest("おはよう", "ja"))

    def test_simplified_output_converted_to_taiwan_traditional(self) -> None:
        self.server.script.append(completion("这个视频不错"))
        out = self.make(SAKURA).translate(TranslationRequest("この動画いいね", "ja"))
        self.assertEqual(out, "這個影片不錯")

    def test_conversion_can_be_disabled(self) -> None:
        self.server.script.append(completion("这个"))
        out = self.make(SAKURA, convert_script=False).translate(
            TranslationRequest("これ", "ja")
        )
        self.assertEqual(out, "这个")

    def test_non_chinese_target_not_converted(self) -> None:
        self.server.script.append(completion("Let's go"))
        out = self.make().translate(TranslationRequest("走吧", "zh", "en"))
        self.assertEqual(out, "Let's go")

    def test_truncated_by_max_tokens_returns_text_and_warns(self) -> None:
        self.server.script.append(completion("早安，今天", finish="length"))
        with self.assertLogs(client_module.logger, logging.WARNING):
            out = self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(out, "早安，今天")


class MalformedResponseTest(ClientTestCase):
    def check(self, action: Action) -> None:
        self.server.script.append(action)
        with self.assertRaises(TranslationResponseError):
            self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(len(self.server.requests), 1)  # 格式錯誤不重試

    def test_not_json(self) -> None:
        self.check(raw_response(b"<html>oops</html>", content_type="text/html"))

    def test_truncated_json(self) -> None:
        self.check(raw_response(b'{"choices": [{"message": '))

    def test_not_utf8(self) -> None:
        self.check(raw_response(b"\xff\xfe\x00"))

    def test_missing_choices(self) -> None:
        self.check(json_response({"id": "x"}))

    def test_empty_choices(self) -> None:
        self.check(json_response({"choices": []}))

    def test_null_content(self) -> None:
        self.check(completion(None))

    def test_non_string_content(self) -> None:
        self.check(json_response({"choices": [{"message": {"content": 42}}]}))

    def test_json_array(self) -> None:
        self.check(json_response([1, 2]))

    def test_oversized_body(self) -> None:
        with mock.patch.object(client_module, "MAX_RESPONSE_BYTES", 64):
            self.check(completion("早安" * 100))


class HttpErrorTest(ClientTestCase):
    def test_client_error_not_retried_and_reports_status(self) -> None:
        self.server.script.append(error(404, 'model "nope" not found'))
        with self.assertRaises(TranslationHTTPError) as ctx:
            self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(ctx.exception.status, 404)
        self.assertIn("not found", str(ctx.exception))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_error_message_is_truncated(self) -> None:
        self.server.script.append(raw_response(b"x" * 5000, 400, "text/plain"))
        with self.assertRaises(TranslationHTTPError) as ctx:
            self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertLess(len(str(ctx.exception)), 400)

    def test_server_error_retried_then_succeeds(self) -> None:
        self.server.script += [error(503, "loading model"), completion("早安")]
        out = self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(out, "早安")
        self.assertEqual(len(self.server.requests), 2)
        self.assertEqual(self.sleeps, [0.5])

    def test_server_error_gives_up_after_max_retries(self) -> None:
        self.server.script += [error(500, "boom")] * 3
        with self.assertRaises(TranslationHTTPError) as ctx:
            self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(ctx.exception.status, 500)
        self.assertEqual(len(self.server.requests), 3)
        self.assertEqual(self.sleeps, [0.5, 1.0])

    def test_zero_retries(self) -> None:
        self.server.script.append(error(502, "bad gateway"))
        with self.assertRaises(TranslationHTTPError):
            self.make(max_retries=0).translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(len(self.server.requests), 1)

    def test_retry_after_header_is_respected_and_capped(self) -> None:
        self.server.script += [
            error(429, "slow down", {"Retry-After": "3"}),
            error(429, "slow down", {"Retry-After": "999"}),
            completion("早安"),
        ]
        self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(self.sleeps, [3.0, client_module.MAX_RETRY_DELAY])

    def test_not_implemented_is_not_retried(self) -> None:
        self.server.script.append(error(501, "nope"))
        with self.assertRaises(TranslationHTTPError):
            self.make().translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(len(self.server.requests), 1)


class TimeoutAndConnectionTest(ClientTestCase):
    def test_localhost_prefers_ipv4_and_keeps_host_header(self) -> None:
        # Windows 上 localhost 先解析成 ::1；伺服器只聽 IPv4 時，每次被拒要多等約 2 秒。
        port = self.server.base_url.split(":")[2].split("/")[0]
        self.server.script.append(completion("早安"))
        translator = self.make(base_url=f"http://localhost:{port}/v1")
        start = time.perf_counter()
        translator.translate(TranslationRequest("お", "ja"))
        self.assertLess(time.perf_counter() - start, 1.0)
        self.assertEqual(self.server.requests[0].headers["host"], f"localhost:{port}")

    def test_read_timeout_raises_without_retry(self) -> None:
        self.server.script.append(delayed(3.0, completion("早安")))
        with self.assertRaises(TranslationTimeoutError):
            self.make(read_timeout=0.2).translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(len(self.server.requests), 1)
        self.assertEqual(self.sleeps, [])

    def test_connection_refused_retries_then_raises(self) -> None:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        translator = self.make(
            base_url=f"http://127.0.0.1:{port}/v1", connect_timeout=0.5, max_retries=1
        )
        with self.assertRaises(TranslationConnectionError):
            translator.translate(TranslationRequest("おはよう", "ja"))
        self.assertEqual(self.sleeps, [0.5])


class StreamTest(ClientTestCase):
    def test_yields_cumulative_translation(self) -> None:
        self.server.script.append(
            sse(
                [
                    chunk(content=""),
                    chunk("早"),
                    chunk("安"),
                    chunk("！", finish="stop"),
                ]
            )
        )
        parts = list(self.make().stream(TranslationRequest("おはよう！", "ja")))
        self.assertEqual(parts, ["早", "早安", "早安！"])
        body = self.server.requests[0].body
        self.assertIs(body["stream"], True)
        self.assertEqual(self.server.requests[0].headers["accept"], "text/event-stream")

    def test_reasoning_deltas_are_ignored(self) -> None:
        self.server.script.append(
            sse(
                [
                    chunk("", reasoning="Thinking"),
                    chunk("", reasoning="..."),
                    chunk("早安"),
                ]
            )
        )
        self.assertEqual(
            list(self.make().stream(TranslationRequest("おはよう", "ja"))), ["早安"]
        )

    def test_think_tags_split_across_chunks_never_shown(self) -> None:
        pieces = ["<thi", "nk>Let me ", "think</th", "ink>\n", "早", "安"]
        self.server.script.append(sse([chunk(p) for p in pieces]))
        parts = list(self.make().stream(TranslationRequest("おはよう", "ja")))
        self.assertEqual(parts, ["早", "早安"])

    def test_stream_converts_to_traditional(self) -> None:
        self.server.script.append(sse([chunk("这个"), chunk("视频")]))
        parts = list(self.make(SAKURA).stream(TranslationRequest("この動画", "ja")))
        self.assertEqual(parts[-1], "這個影片")
        self.assertTrue(all("这" not in p for p in parts))

    def test_ignores_comments_and_extra_fields(self) -> None:
        self.server.script.append(
            sse([chunk("早安")], raw_prefix=": keep-alive\n\nevent: message\nid: 1\n\n")
        )
        self.assertEqual(
            list(self.make().stream(TranslationRequest("お", "ja"))), ["早安"]
        )

    def test_usage_chunk_without_choices_is_ignored(self) -> None:
        usage = json.dumps({"choices": [], "usage": {"total_tokens": 3}})
        self.server.script.append(sse([chunk("早安"), usage]))
        self.assertEqual(
            list(self.make().stream(TranslationRequest("お", "ja"))), ["早安"]
        )

    def test_missing_done_but_finished_is_accepted(self) -> None:
        self.server.script.append(sse([chunk("早安", finish="stop")], done=False))
        self.assertEqual(
            list(self.make().stream(TranslationRequest("お", "ja"))), ["早安"]
        )

    def test_stream_cut_off_is_an_error(self) -> None:
        self.server.script.append(sse([chunk("早")], done=False))
        with self.assertRaises(TranslationResponseError):
            list(self.make().stream(TranslationRequest("お", "ja")))

    def test_error_event_raises(self) -> None:
        self.server.script.append(
            sse([json.dumps({"error": {"message": "model crashed"}})])
        )
        with self.assertRaises(TranslationResponseError) as ctx:
            list(self.make().stream(TranslationRequest("お", "ja")))
        self.assertIn("model crashed", str(ctx.exception))

    def test_malformed_event_raises(self) -> None:
        self.server.script.append(sse(["{not json"]))
        with self.assertRaises(TranslationResponseError):
            list(self.make().stream(TranslationRequest("お", "ja")))

    def test_empty_stream_raises(self) -> None:
        self.server.script.append(sse([chunk("", reasoning="only thinking")]))
        with self.assertRaises(TranslationResponseError):
            list(self.make().stream(TranslationRequest("お", "ja")))

    def test_retries_before_first_chunk(self) -> None:
        self.server.script += [error(503, "loading"), sse([chunk("早安")])]
        self.assertEqual(
            list(self.make().stream(TranslationRequest("お", "ja"))), ["早安"]
        )
        self.assertEqual(len(self.server.requests), 2)

    def test_read_timeout_mid_stream_raises_after_partial(self) -> None:
        self.server.script.append(sse([chunk("早"), chunk("安")], gap=3.0))
        parts = self.make(read_timeout=0.3).stream(TranslationRequest("お", "ja"))
        self.assertEqual(next(parts), "早")
        with self.assertRaises(TranslationTimeoutError):
            next(parts)
        self.assertEqual(len(self.server.requests), 1)

    def test_http_error_before_stream(self) -> None:
        self.server.script.append(error(400, "bad request"))
        with self.assertRaises(TranslationHTTPError):
            list(self.make().stream(TranslationRequest("お", "ja")))

    def test_closing_early_closes_connection(self) -> None:
        self.server.script.append(sse([chunk("早"), chunk("安")], gap=3.0))
        parts = self.make().stream(TranslationRequest("お", "ja"))
        self.assertEqual(next(parts), "早")
        with mock.patch.object(
            client_module.http.client.HTTPConnection, "close", autospec=True
        ) as close:
            parts.close()  # 即時字幕過時就丟掉：不能卡住等剩下的內容
        close.assert_called_once()


class ApiKeyTest(ClientTestCase):
    def test_no_header_when_env_unset_or_blank(self) -> None:
        self.server.script += [completion("早安"), completion("早安")]
        self.make().translate(TranslationRequest("お", "ja"))
        os.environ[KEY_ENV] = "   "
        self.make().translate(TranslationRequest("お", "ja"))
        for req in self.server.requests:
            self.assertNotIn("authorization", req.headers)

    def test_bearer_header_when_env_set(self) -> None:
        os.environ[KEY_ENV] = SECRET
        self.server.script.append(completion("早安"))
        self.make().translate(TranslationRequest("お", "ja"))
        self.assertEqual(
            self.server.requests[0].headers["authorization"], f"Bearer {SECRET}"
        )

    def test_default_env_name(self) -> None:
        self.assertEqual(
            TranslatorConfig(model="m").api_key_env, "KIKITORI_TRANSLATE_API_KEY"
        )

    def test_key_read_at_request_time(self) -> None:
        translator = self.make()
        os.environ[KEY_ENV] = SECRET
        self.server.script.append(completion("早安"))
        translator.translate(TranslationRequest("お", "ja"))
        self.assertIn("authorization", self.server.requests[0].headers)

    def test_key_never_logged_or_echoed(self) -> None:
        os.environ[KEY_ENV] = SECRET
        # 伺服器把金鑰原樣放進錯誤訊息（真的有服務這樣做），重試時也會寫 log。
        echo = error(500, f"invalid key {SECRET}")
        self.server.script += [echo, echo, echo]
        translator = self.make()
        with (
            self.assertLogs(client_module.logger, logging.DEBUG) as logs,
            self.assertRaises(TranslationHTTPError) as ctx,
        ):
            translator.translate(TranslationRequest("お", "ja"))
        self.assertNotIn(SECRET, "\n".join(logs.output))
        self.assertNotIn(SECRET, str(ctx.exception))
        self.assertNotIn(SECRET, repr(translator))
        self.assertNotIn(SECRET, repr(translator.config))

    def test_key_with_control_characters_rejected(self) -> None:
        os.environ[KEY_ENV] = "abc\r\nX-Evil: 1"
        with self.assertRaises(TranslationError) as ctx:
            self.make().translate(TranslationRequest("お", "ja"))
        self.assertNotIn("abc", str(ctx.exception))
        self.assertEqual(self.server.requests, [])


class ValidationTest(ClientTestCase):
    def test_invalid_requests_never_reach_server(self) -> None:
        translator = self.make()
        bad = [
            TranslationRequest("", "ja"),
            TranslationRequest("   ", "ja"),
            TranslationRequest("hej", "sv"),  # 不在語言清單內
            TranslationRequest("おはよう", "ja", "zh"),  # 譯文要分繁簡
            TranslationRequest("你好", "zh", "zh-TW"),  # 同一個 Whisper 語言
        ]
        for req in bad:
            with self.subTest(req=req):
                with self.assertRaises(InvalidRequestError):
                    translator.translate(req)
                with self.assertRaises(InvalidRequestError):
                    translator.stream(req)  # 驗證在呼叫時就發生，不必等迭代
        self.assertEqual(self.server.requests, [])

    def test_sakura_only_accepts_japanese_to_zh_tw(self) -> None:
        with self.assertRaises(InvalidRequestError):
            self.make(SAKURA).translate(TranslationRequest("hello", "en"))
        self.assertEqual(self.server.requests, [])

    def test_context_is_trimmed_in_prompt(self) -> None:
        self.server.script.append(completion("走吧"))
        self.make("qwen3.5:4b", max_context_lines=1).translate(
            TranslationRequest("行こう", "ja", context=("前の一", "前の二"))
        )
        content = self.server.requests[0].body["messages"][1]["content"]
        self.assertIn("前の二", content)
        self.assertNotIn("前の一", content)

    def test_context_dropped_for_models_without_context_support(self) -> None:
        self.server.script.append(completion("走吧"))
        self.make("hy-mt1.5-1.8b:latest").translate(
            TranslationRequest("行こう", "ja", context=("前の文",))
        )
        content = self.server.requests[0].body["messages"][0]["content"]
        self.assertNotIn("前の文", content)
        self.assertTrue(content.startswith("将以下文本翻译为繁体中文"))


class ConfigValidationTest(unittest.TestCase):
    def test_rejects_bad_values(self) -> None:
        bad: list[dict[str, Any]] = [
            {"model": ""},
            {"model": "m\n"},
            {"model": "m", "base_url": "ftp://host/v1"},
            {"model": "m", "base_url": "http:///v1"},
            {"model": "m", "base_url": "localhost:11434"},
            {"model": "m", "base_url": "http://h:99999/v1"},
            {"model": "m", "base_url": "http://h/v1?x=1"},
            {"model": "m", "api_key_env": "1BAD"},
            {"model": "m", "api_key_env": "HAS SPACE"},
            {"model": "m", "temperature": -0.1},
            {"model": "m", "temperature": float("nan")},
            {"model": "m", "connect_timeout": 0},
            {"model": "m", "read_timeout": float("inf")},
            {"model": "m", "max_retries": -1},
            {"model": "m", "max_retries": True},
            {"model": "m", "max_retries": 1.5},
            {"model": "m", "retry_backoff": -1},
            {"model": "m", "max_tokens": 0},
            {"model": "m", "max_context_lines": -1},
            {"model": "m", "convert_script": "yes"},
        ]
        for kwargs in bad:
            with (
                self.subTest(kwargs=kwargs),
                self.assertRaises((ValueError, TypeError)),
            ):
                TranslatorConfig(**kwargs)

    def test_credentials_in_the_url_are_redacted(self) -> None:
        for url in ("http://kiki:s3cret@h:99999/v1", "ftp://kiki:s3cret@h/v1"):
            with self.subTest(url=url[:6]), self.assertRaises(ValueError) as ctx:
                TranslatorConfig(model="m", base_url=url)
            self.assertNotIn("s3cret", str(ctx.exception))
        translator = OpenAICompatibleTranslator(
            TranslatorConfig(model="m", base_url="http://kiki:s3cret@localhost:1/v1")
        )
        self.assertNotIn("s3cret", repr(translator))

    def test_accepts_https_and_custom_port(self) -> None:
        cfg = TranslatorConfig(model="m", base_url="https://api.example.com:8443/v1/")
        self.assertEqual(cfg.base_url, "https://api.example.com:8443/v1/")


if __name__ == "__main__":
    unittest.main()
