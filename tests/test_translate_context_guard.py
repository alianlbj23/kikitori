"""前文保險：帶前文的譯文長度異常或抄出前文時，改不帶前文重翻一次。"""

import unittest
from collections.abc import Callable, Iterator

from kikitori.translate.context_guard import (
    ContextFallbackTranslator,
    looks_like_context_leak,
)
from kikitori.translate.types import TranslationRequest

CONTEXT = ("今日はホラーゲームをやります。", "怖くないもん！")
LONG = "今天要玩恐怖遊戲。我才不怕呢！我一點都不怕，真的完全不怕，大家放心看吧，今天會很好玩喔！"


class FakeTranslator:
    """依請求有無前文回不同譯文，並記錄收到的請求。"""

    def __init__(self, reply: Callable[[TranslationRequest], str]) -> None:
        self.reply = reply
        self.requests: list[TranslationRequest] = []
        self.closed = 0

    def translate(self, request: TranslationRequest) -> str:
        self.requests.append(request)
        return self.reply(request)

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        self.requests.append(request)
        text = self.reply(request)
        try:
            for end in range(5, len(text) + 5, 5):
                yield text[:end]
        finally:
            self.closed += 1


def leaky(request: TranslationRequest) -> str:
    return LONG if request.context else "我才不怕呢！"


class DetectionTest(unittest.TestCase):
    def test_long_output_with_context_is_suspicious(self) -> None:
        request = TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
        self.assertTrue(looks_like_context_leak(LONG, request))
        self.assertFalse(looks_like_context_leak("我才不怕呢！", request))

    def test_both_length_conditions_are_required(self) -> None:
        # 原文很短時，3 倍仍不到 40 字的輸出不算異常（例如「草」→「笑死我了哈哈哈」）。
        short = TranslationRequest("草", "ja", "zh-TW", CONTEXT)
        self.assertFalse(looks_like_context_leak("笑死我了哈哈哈哈哈哈", short))
        # 超過 40 字但不到原文 3 倍：長句的正常翻譯
        source = "あ" * 30
        long_source = TranslationRequest(source, "ja", "zh-TW", CONTEXT)
        self.assertFalse(looks_like_context_leak("啊" * 60, long_source))
        self.assertTrue(looks_like_context_leak("啊" * 91, long_source))

    def test_latin_targets_from_cjk_source_allow_more_characters(self) -> None:
        # 日文翻英文本來就會變長：「スパチャありがとう！めっちゃ嬉しい！」（18 字）→ 40 多個字母。
        request = TranslationRequest(
            "スパチャありがとう！めっちゃ嬉しい！", "ja", "en", CONTEXT
        )
        self.assertFalse(
            looks_like_context_leak(
                "Thanks for the Super Chat! I'm so happy, seriously!", request
            )
        )

    def test_context_copied_verbatim_is_suspicious(self) -> None:
        request = TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
        self.assertTrue(
            looks_like_context_leak("今日はホラーゲームをやります。我才不怕", request)
        )

    def test_without_context_never_suspicious(self) -> None:
        request = TranslationRequest("怖くないもん！", "ja", "zh-TW")
        self.assertFalse(looks_like_context_leak(LONG, request))


class BackgroundLeakTest(unittest.TestCase):
    """v0.3 G5：本場資訊與前文一樣可能被翻出來。"""

    def test_copied_background_is_suspicious(self) -> None:
        req = TranslationRequest(
            "行くよ", "ja", "zh-TW", background="今日はマイクラ建築配信"
        )
        self.assertTrue(looks_like_context_leak("今日はマイクラ建築配信 走吧", req))
        self.assertFalse(looks_like_context_leak("走吧", req))

    def test_background_alone_only_checks_for_copies(self) -> None:
        # 沒有前文時，長譯文本身不算外洩（長度規則只為前文設計）
        req = TranslationRequest("行くよ", "ja", "zh-TW", background="今日の配信")
        self.assertFalse(looks_like_context_leak(LONG, req))
        guard = ContextFallbackTranslator(FakeTranslator(lambda r: LONG))
        self.assertEqual("".join(list(guard.stream(req))[-1:]), LONG)

    def test_stream_retries_without_background_when_it_is_copied(self) -> None:
        bg = "今日はマイクラ建築配信"
        inner = FakeTranslator(lambda r: f"{bg} 走吧" if r.background else "走吧")
        req = TranslationRequest("行くよ", "ja", "zh-TW", background=bg)
        self.assertEqual(list(ContextFallbackTranslator(inner).stream(req))[-1], "走吧")
        self.assertEqual([r.background for r in inner.requests], [bg, ""])

    def test_retry_drops_both_context_and_background(self) -> None:
        inner = FakeTranslator(leaky)
        guard = ContextFallbackTranslator(inner)
        req = TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT, "背景")
        self.assertEqual(guard.translate(req), "我才不怕呢！")
        self.assertEqual(
            [(r.context, r.background) for r in inner.requests],
            [(CONTEXT, "背景"), ((), "")],
        )


class TranslateFallbackTest(unittest.TestCase):
    def test_retranslates_once_without_context(self) -> None:
        inner = FakeTranslator(leaky)
        guard = ContextFallbackTranslator(inner)
        out = guard.translate(
            TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
        )
        self.assertEqual(out, "我才不怕呢！")
        self.assertEqual([r.context for r in inner.requests], [CONTEXT, ()])

    def test_normal_output_is_not_retranslated(self) -> None:
        inner = FakeTranslator(lambda r: "我才不怕呢！")
        out = ContextFallbackTranslator(inner).translate(
            TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
        )
        self.assertEqual(out, "我才不怕呢！")
        self.assertEqual(len(inner.requests), 1)

    def test_only_one_retry_even_if_still_long(self) -> None:
        inner = FakeTranslator(lambda r: LONG)
        out = ContextFallbackTranslator(inner).translate(
            TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
        )
        self.assertEqual(out, LONG)
        self.assertEqual(len(inner.requests), 2)

    def test_no_context_no_retry(self) -> None:
        inner = FakeTranslator(lambda r: LONG)
        ContextFallbackTranslator(inner).translate(
            TranslationRequest("怖くないもん！", "ja", "zh-TW")
        )
        self.assertEqual(len(inner.requests), 1)


class StreamFallbackTest(unittest.TestCase):
    def test_switches_to_context_free_stream_as_soon_as_output_is_too_long(
        self,
    ) -> None:
        inner = FakeTranslator(leaky)
        parts = list(
            ContextFallbackTranslator(inner).stream(
                TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
            )
        )
        self.assertEqual(parts[-1], "我才不怕呢！")
        self.assertEqual([r.context for r in inner.requests], [CONTEXT, ()])
        # 第一個串流在變得過長時就關閉，不必等它說完
        self.assertEqual(inner.closed, 2)
        self.assertTrue(all(len(p) <= 41 for p in parts))

    def test_normal_stream_passes_through(self) -> None:
        inner = FakeTranslator(lambda r: "我才不怕呢！真的！")
        parts = list(
            ContextFallbackTranslator(inner).stream(
                TranslationRequest("怖くないもん！", "ja", "zh-TW", CONTEXT)
            )
        )
        self.assertEqual(parts, ["我才不怕呢", "我才不怕呢！真的！"])
        self.assertEqual(len(inner.requests), 1)

    def test_verbatim_context_detected_at_the_end(self) -> None:
        def echo(request: TranslationRequest) -> str:
            return CONTEXT[0] + "怕" if request.context else "怕"

        inner = FakeTranslator(echo)
        parts = list(
            ContextFallbackTranslator(inner).stream(
                TranslationRequest("怖い", "ja", "zh-TW", CONTEXT)
            )
        )
        self.assertEqual(parts[-1], "怕")
        self.assertEqual(len(inner.requests), 2)


class EagerValidationTest(unittest.TestCase):
    def test_stream_validates_when_called_like_the_wrapped_translator(self) -> None:
        # OpenAICompatibleTranslator.stream() 在呼叫時就驗證請求；包裝後也一樣，
        # 帶前文時不能延到第一次迭代才丟。
        class Rejecting(FakeTranslator):
            def stream(self, request: TranslationRequest) -> Iterator[str]:
                raise ValueError("bad request")

        guard = ContextFallbackTranslator(Rejecting(leaky))
        with self.assertRaises(ValueError):
            guard.stream(TranslationRequest("怖い", "ja", "zh-TW", CONTEXT))


if __name__ == "__main__":
    unittest.main()
