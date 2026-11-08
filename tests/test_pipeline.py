"""pipeline 的測試：雙路徑排程、順序、語言規則、過濾、錯誤與生命週期。

全部用假元件：依振幅判斷的 VoiceModel、回固定文字的 Transcriber／Translator。
排程情境用逐步模式重現（不開執行緒）；執行緒模式的測試只用 Event 等待條件，
不用 sleep 判斷時序。
"""

import dataclasses
import logging
import threading
import unittest
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from unittest import mock

import numpy as np
import numpy.typing as npt

from kikitori.asr.types import Transcript
from kikitori.capture import ArraySource
from kikitori.capture.errors import DeviceLostError
from kikitori.pipeline import (
    Pipeline,
    PipelineComponents,
    PipelineConfig,
    SubtitleUpdate,
    default_transcript_filter,
    route,
)
from kikitori.pipeline.languages import display_original
from kikitori.translate.errors import TranslationConnectionError
from kikitori.translate.types import TranslationRequest

SR = 16000
_QUIET = logging.NullHandler()


def setUpModule() -> None:
    # 預期中的 warning 已由 on_log 檢查；避免 logging 的 lastResort 把它們印到測試輸出。
    logging.getLogger("kikitori").addHandler(_QUIET)


def tearDownModule() -> None:
    logging.getLogger("kikitori").removeHandler(_QUIET)


WAIT = 10.0  # 執行緒測試等待條件的上限；正常情況下遠小於此值


def speech(secs: float) -> npt.NDArray[np.int16]:
    return np.full(round(secs * SR), 5000, dtype=np.int16)


def silence(secs: float) -> npt.NDArray[np.int16]:
    return np.zeros(round(secs * SR), dtype=np.int16)


def track(*parts: npt.NDArray[np.int16]) -> npt.NDArray[np.int16]:
    return np.concatenate(parts)


class AmplitudeVoiceModel:
    """振幅大於 1000 就當作人聲；讓測試用簡單的方波控制切句。"""

    def probability(self, window: npt.NDArray[np.int16]) -> float:
        return 1.0 if int(np.abs(window.astype(np.int32)).max()) > 1000 else 0.0

    def reset(self) -> None:
        pass


@dataclass
class Call:
    samples: int
    prompt: str | None


class FakeTranscriber:
    """依序回傳 `script` 產生的結果；可用 gate 讓呼叫停住，模擬模型很慢。"""

    def __init__(
        self,
        name: str,
        language: str = "ja",
        script: Callable[[int], Transcript] | None = None,
    ) -> None:
        self.name = name
        self.language = language
        self.calls: list[Call] = []
        self.script = script
        self.gate: threading.Event | None = None
        self.entered = threading.Event()
        self.error: Exception | None = None

    def transcribe(
        self,
        audio: np.ndarray,
        language: str | None = None,
        prompt: str | None = None,
    ) -> Transcript:
        self.calls.append(Call(len(audio), prompt))
        self.entered.set()
        if self.gate is not None:
            self.gate.wait(WAIT)
        if self.error is not None:
            raise self.error
        if self.script is not None:
            return self.script(len(self.calls))
        return Transcript(
            text=f"{self.name}{len(self.calls)}",
            language=self.language,
            language_probability=1.0,
            no_speech_probability=0.01,
            avg_logprob=-0.2,
        )


class FakeTranslator:
    def __init__(self) -> None:
        self.requests: list[TranslationRequest] = []
        self.error: Exception | None = None

    def translate(self, request: TranslationRequest) -> str:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        return f"<{request.target_language}>{request.text}"

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        self.requests.append(request)
        if self.error is not None:
            raise self.error
        full = f"<{request.target_language}>{request.text}"
        yield full[: len(full) // 2]
        yield full


def transcript(text: str, language: str = "ja", no_speech: float = 0.01) -> Transcript:
    return Transcript(text, language, 1.0, no_speech, -0.2)


class Harness:
    """組好一個 pipeline 並記錄所有回呼。"""

    def __init__(
        self,
        mode: str = "media",
        *,
        clock: Callable[[], float] | None = None,
        final_no_speech_reliable: bool = True,
        keep_warm: "RecordingKeepWarm | None" = None,
        **config: object,
    ) -> None:
        self.fast = FakeTranscriber("fast")
        self.final = FakeTranscriber("final")
        self.fast_tr = FakeTranslator()
        self.final_tr = FakeTranslator()
        self.updates: list[SubtitleUpdate] = []
        self.statuses: list[tuple[str, str | None]] = []
        # 每次 on_status 當下的 pipeline.error（engine 在回呼中讀它決定 code）。
        self.errors_at_status: list[BaseException | None] = []
        self.logs: list[tuple[str, str]] = []
        self.on_update: Callable[[SubtitleUpdate], None] | None = None
        pause, max_secs = (0.6, 10.0) if mode == "media" else (0.5, 8.0)
        self.config = PipelineConfig(
            mode=mode,  # type: ignore[arg-type]
            pause_secs=pause,
            max_secs=max_secs,
            **config,  # type: ignore[arg-type]
        )
        self.pipeline = Pipeline(
            PipelineComponents(
                voice_model=AmplitudeVoiceModel(),
                fast_transcriber=self.fast,
                final_transcriber=self.final,
                fast_translator=self.fast_tr,
                final_translator=self.final_tr,
                transcript_filter=default_transcript_filter(0.6),
                final_no_speech_reliable=final_no_speech_reliable,
                keep_warm=keep_warm,
            ),
            self.config,
            clock=clock or (lambda: 0.0),
            on_subtitle=self._record,
            on_status=self._status,
            on_log=lambda level, m: self.logs.append((level, m)),
        )

    def _status(self, state: str, detail: str | None) -> None:
        self.statuses.append((state, detail))
        self.errors_at_status.append(self.pipeline.error)

    def _record(self, update: SubtitleUpdate) -> None:
        self.updates.append(update)
        if self.on_update is not None:
            self.on_update(update)

    def finals(self) -> list[SubtitleUpdate]:
        return [u for u in self.updates if u.is_final]

    def partials(self) -> list[SubtitleUpdate]:
        return [u for u in self.updates if not u.is_final]


# 一句 2 秒的人聲（1.0–3.0 s）：起點含 200 ms 前置緩衝，停頓 0.6 s 後結束。
ONE_UTTERANCE = track(silence(1.0), speech(2.0), silence(1.5))
UTT_START = 12672  # 候選起點所在視窗 15872 − 3200
UTT_END = 57856  # 靜音從 48128 開始，滿 9600 樣本的視窗終點


class RouteTest(unittest.TestCase):
    def test_media_translates_to_target(self) -> None:
        for lang in ("ja", "en"):
            r = route("media", lang, "zh-TW", "ja")
            self.assertEqual(
                (r.source, r.target, r.action), (lang, "zh-TW", "translate")
            )

    def test_media_chinese_is_copied(self) -> None:
        self.assertEqual(route("media", "zh", "zh-TW", "ja").action, "copy")

    def test_interpret_chinese_goes_to_partner(self) -> None:
        for partner in ("ja", "en"):
            r = route("interpret", "zh", "zh-TW", partner)
            self.assertEqual((r.target, r.action), (partner, "translate"))

    def test_interpret_other_goes_to_zh_tw(self) -> None:
        r = route("interpret", "en", "zh-TW", "ja")
        self.assertEqual((r.target, r.action), ("zh-TW", "translate"))

    def test_unsupported_language(self) -> None:
        # 不在語言清單內的 Whisper 語言（例如挪威語的幻覺）不送翻譯
        r = route("media", "nn", "zh-TW", "ja")
        self.assertEqual((r.target, r.action), ("zh-TW", "unsupported"))

    def test_language_code_normalization(self) -> None:
        self.assertEqual(route("interpret", "ZH_tw", "zh-TW", "en").target, "en")

    def test_media_any_catalog_pair(self) -> None:
        r = route("media", "ko", "es", "ja")
        self.assertEqual((r.source, r.target, r.action), ("ko", "es", "translate"))

    def test_same_whisper_language_is_copied_for_both_chinese_scripts(self) -> None:
        for target in ("zh-TW", "zh-CN"):
            with self.subTest(target=target):
                r = route("media", "zh", target, "ja")
                self.assertEqual((r.target, r.action), (target, "copy"))
        self.assertEqual(route("media", "ja", "ja", "en").action, "copy")

    def test_interpret_between_any_two_languages(self) -> None:
        # 我的語言 ja、對方語言 ko
        r = route("interpret", "ja", "ja", "ko")
        self.assertEqual((r.source, r.target, r.action), ("ja", "ko", "translate"))
        r = route("interpret", "ko", "ja", "ko")
        self.assertEqual((r.source, r.target, r.action), ("ko", "ja", "translate"))

    def test_interpret_simplified_chinese_user(self) -> None:
        r = route("interpret", "zh", "zh-CN", "en")
        self.assertEqual((r.target, r.action), ("en", "translate"))
        r = route("interpret", "en", "zh-CN", "en")
        self.assertEqual((r.target, r.action), ("zh-CN", "translate"))

    def test_translation_disabled_never_translates(self) -> None:
        for mode, detected in (("media", "ja"), ("media", "zh"), ("interpret", "zh")):
            with self.subTest(mode=mode, detected=detected):
                r = route(mode, detected, "zh-TW", "ja", translation_enabled=False)  # type: ignore[arg-type]
                self.assertEqual(r.action, "off")


class FilterTest(unittest.TestCase):
    def test_default_filter(self) -> None:
        f = default_transcript_filter(0.6)
        self.assertIsNone(f(transcript("こんにちは")))
        self.assertEqual(f(transcript("  ")), "empty")
        self.assertEqual(f(transcript("Thank you.", "en", 0.9)), "no_speech")

    def test_threshold_validation(self) -> None:
        for bad in (-0.1, 1.1, float("nan"), True):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                default_transcript_filter(bad)  # type: ignore[arg-type]


class ConfigValidationTest(unittest.TestCase):
    def test_rejects_bad_values(self) -> None:
        for kwargs in (
            {"mode": "karaoke"},
            {"pause_secs": 0},
            {"partial_interval_ms": 0},
            {"final_queue_size": 0},
            {"context_lines": -1},
            {"target_language": "zh"},
            {"target_language": "zh-tw"},  # 必須是正規化後的語言碼
            {"partner_language": "sv"},
            {"source_language": "xx"},
        ):
            params = {"mode": "media", "pause_secs": 0.6, "max_secs": 10.0, **kwargs}
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                PipelineConfig(**params)  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            PipelineConfig(
                mode="media",
                pause_secs=0.6,
                max_secs=10.0,
                translation_enabled=1,  # type: ignore[arg-type]
            )


class SteppedSchedulingTest(unittest.TestCase):
    def test_partial_then_final_replaces_it(self) -> None:
        h = Harness()
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        partials = h.partials()
        self.assertTrue(partials)
        self.assertTrue(all(u.utterance_id == 1 for u in partials))
        # 串流翻譯：每次都是累積的完整譯文
        self.assertEqual(partials[-1].translation, "<zh-TW>fast1")
        self.assertTrue(partials[-1].translation.startswith(partials[0].translation))
        self.assertEqual(partials[0].start_sample, UTT_START)

        h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
        h.pipeline.run_pending()
        self.assertFalse(h.updates[-1].is_final is False)
        final = h.updates[-1]
        self.assertEqual(
            (final.utterance_id, final.original, final.translation, final.is_final),
            (1, "final1", "<zh-TW>final1", True),
        )
        self.assertEqual(
            (final.source_language, final.target_language), ("ja", "zh-TW")
        )
        self.assertEqual(final.start_sample, UTT_START)

    def test_stale_partials_are_dropped(self) -> None:
        h = Harness()
        h.pipeline.feed(ONE_UTTERANCE[: 3 * SR])  # 三次觸發，尚未結束
        h.pipeline.run_pending()
        self.assertEqual(len(h.fast.calls), 1)
        self.assertEqual(h.pipeline.dropped_partials, 2)
        self.assertGreaterEqual(h.fast.calls[0].samples, 3 * 11200)

    def test_partial_for_ended_utterance_is_skipped(self) -> None:
        h = Harness()
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.fast.calls, [])
        self.assertEqual([u.is_final for u in h.updates], [True])

    def test_finals_in_order_none_dropped(self) -> None:
        h = Harness()
        audio = track(*(ONE_UTTERANCE for _ in range(3)))
        h.pipeline.feed(audio)
        h.pipeline.run_pending()
        self.assertEqual([u.utterance_id for u in h.finals()], [1, 2, 3])
        self.assertEqual(
            [u.original for u in h.finals()], ["final1", "final2", "final3"]
        )

    def test_trailing_pause_is_trimmed(self) -> None:
        h = Harness()
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        expected = (UTT_END - UTT_START) - round((0.6 - 0.2) * SR)
        self.assertEqual(h.final.calls[0].samples, expected)
        self.assertEqual(h.finals()[0].end_sample, UTT_START + expected)

    def test_max_length_is_not_trimmed(self) -> None:
        h = Harness()
        h.pipeline.feed(track(speech(10.5), silence(1.0)))
        h.pipeline.run_pending()
        first = h.finals()[0]
        self.assertEqual(
            first.end_sample - first.start_sample, h.final.calls[0].samples
        )
        self.assertGreaterEqual(h.final.calls[0].samples, 10 * SR)

    def test_end_of_stream_flushes_and_goes_idle(self) -> None:
        h = Harness()
        h.pipeline.feed(track(silence(1.0), speech(2.0)))
        h.pipeline.end_of_stream()
        h.pipeline.run_pending()
        self.assertEqual([u.utterance_id for u in h.finals()], [1])
        self.assertEqual(h.statuses, [("running", None), ("idle", None)])
        self.assertEqual(h.pipeline.state, "idle")

    def test_cannot_mix_modes(self) -> None:
        h = Harness()
        h.pipeline.feed(silence(0.1))
        with self.assertRaises(RuntimeError):
            h.pipeline.start(ArraySource(silence(0.1)))


class LanguageRoutingTest(unittest.TestCase):
    def run_final(self, mode: str, language: str, partner: str = "ja") -> Harness:
        h = Harness(mode, partner_language=partner)
        h.final.language = language
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        return h

    def test_media_japanese(self) -> None:
        h = self.run_final("media", "ja")
        self.assertEqual(h.final_tr.requests[0].source_language, "ja")
        self.assertEqual(h.final_tr.requests[0].target_language, "zh-TW")
        self.assertEqual(h.finals()[0].target_language, "zh-TW")

    def test_media_chinese_is_not_translated(self) -> None:
        h = self.run_final("media", "zh")
        self.assertEqual(h.final_tr.requests, [])
        self.assertEqual(h.finals()[0].translation, "final1")

    def test_interpret_chinese_to_partner(self) -> None:
        h = self.run_final("interpret", "zh", partner="en")
        req = h.final_tr.requests[0]
        self.assertEqual((req.source_language, req.target_language), ("zh", "en"))
        self.assertEqual(h.finals()[0].target_language, "en")
        self.assertEqual(h.finals()[0].translation, "<en>final1")

    def test_interpret_partner_to_zh_tw(self) -> None:
        h = self.run_final("interpret", "ja")
        self.assertEqual(h.final_tr.requests[0].target_language, "zh-TW")

    def test_chinese_original_is_shown_in_taiwan_traditional(self) -> None:
        # Whisper 的中文辨識結果通常是簡體（端到端驗收：口譯時使用者說的「請問附近有
        # 便利商店嗎」顯示成「请问附近有便利商店吗」）；使用者的語言是台灣正體。
        for mode, partner in (("interpret", "en"), ("media", "ja")):
            with self.subTest(mode=mode):
                h = Harness(mode, partner_language=partner)
                simplified = transcript("请问附近有便利商店吗", "zh")
                h.fast.script = lambda n, t=simplified: t
                h.final.script = lambda n, t=simplified: t
                h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
                h.pipeline.run_pending()
                h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
                h.pipeline.run_pending()
                self.assertTrue(h.partials())
                for update in h.updates:
                    self.assertEqual(update.original, "請問附近有便利商店嗎")
                if mode == "media":  # 中文不翻譯：譯文就是原文，也要是正體
                    self.assertEqual(h.finals()[0].translation, "請問附近有便利商店嗎")
                else:
                    self.assertEqual(
                        h.final_tr.requests[-1].text, "請問附近有便利商店嗎"
                    )

    def test_japanese_original_is_not_converted(self) -> None:
        # 日文新字體（気、駅）不是簡體字，不能被中文轉換改掉。
        h = Harness()
        h.final.script = lambda n: transcript("駅の天気が気になる", "ja")
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.finals()[0].original, "駅の天気が気になる")

    def test_media_korean_to_spanish(self) -> None:
        h = Harness("media", target_language="es", source_language="ko")
        h.final.language = "ko"
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        req = h.final_tr.requests[0]
        self.assertEqual((req.source_language, req.target_language), ("ko", "es"))
        self.assertEqual(h.finals()[0].translation, "<es>final1")
        self.assertEqual(h.finals()[0].target_language, "es")

    def test_chinese_to_simplified_target_is_converted_not_translated(self) -> None:
        h = Harness("media", target_language="zh-CN")
        h.final.script = lambda n: transcript("這個軟體很好用", "zh")
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.final_tr.requests, [])
        final = h.finals()[0]
        self.assertEqual(final.original, "这个软体很好用")
        self.assertEqual(final.translation, "这个软体很好用")

    def test_traditional_source_simplified_target_keeps_original_script(self) -> None:
        # 聽繁中、看簡中：原文照收聽語言顯示正體，譯文是簡體
        h = Harness("media", source_language="zh-TW", target_language="zh-CN")
        h.final.script = lambda n: transcript("这个视频", "zh")
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        final = h.finals()[0]
        self.assertEqual((final.original, final.translation), ("這個影片", "这个视频"))
        self.assertEqual(h.final_tr.requests, [])

    def test_interpret_partner_chinese_shown_in_partner_script(self) -> None:
        h = Harness("interpret", target_language="ja", partner_language="zh-TW")
        h.final.script = lambda n: transcript("请问附近有便利商店吗", "zh")
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.finals()[0].original, "請問附近有便利商店嗎")
        self.assertEqual(h.final_tr.requests[0].target_language, "ja")

    def test_unsupported_language_shows_original(self) -> None:
        h = self.run_final("media", "nn")
        self.assertEqual(h.final_tr.requests, [])
        self.assertEqual(
            (h.finals()[0].original, h.finals()[0].translation), ("final1", "")
        )
        self.assertEqual([lvl for lvl, _ in h.logs], ["warning"])


class TranslationDisabledTest(unittest.TestCase):
    """只顯示原文：不呼叫翻譯器（以計數的假翻譯器確認），譯文為空字串。"""

    def test_no_translator_calls_and_empty_translation(self) -> None:
        for mode, language in (("media", "ja"), ("media", "zh"), ("interpret", "zh")):
            with self.subTest(mode=mode, language=language):
                h = Harness(mode, translation_enabled=False)
                h.fast.language = h.final.language = language
                h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
                h.pipeline.run_pending()
                h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
                h.pipeline.run_pending()
                self.assertTrue(h.partials())
                self.assertEqual(h.fast_tr.requests, [])
                self.assertEqual(h.final_tr.requests, [])
                for update in h.updates:
                    self.assertEqual(update.translation, "")
                    self.assertTrue(update.original)
                # 關閉翻譯是使用者的選擇，不是錯誤：不發警告
                self.assertEqual(h.logs, [])


class TranslationToggleTest(unittest.TestCase):
    """執行中切換「顯示翻譯」：之後的句子依新狀態處理，已送出的句子不補翻。"""

    def run_utterance(self, h: Harness) -> None:
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()

    def calls(self, h: Harness) -> int:
        return len(h.fast_tr.requests) + len(h.final_tr.requests)

    def test_turning_translation_on_resumes_translator_calls(self) -> None:
        h = Harness(translation_enabled=False)
        self.assertFalse(h.pipeline.translation_enabled)
        self.run_utterance(h)
        self.assertEqual(self.calls(h), 0)
        h.pipeline.set_translation_enabled(True)
        self.assertTrue(h.pipeline.translation_enabled)
        self.run_utterance(h)
        self.assertGreater(len(h.final_tr.requests), 0)
        first, second = h.finals()
        self.assertEqual((first.utterance_id, first.translation), (1, ""))
        self.assertEqual(second.translation, "<zh-TW>final2")
        # 第一句沒有被補翻：之後沒有任何 utterance 1 的更新
        later = h.updates[h.updates.index(first) + 1 :]
        self.assertNotIn(1, [u.utterance_id for u in later])

    def test_turning_translation_off_stops_translator_calls(self) -> None:
        h = Harness()
        self.run_utterance(h)
        before = self.calls(h)
        self.assertGreater(before, 0)
        h.pipeline.set_translation_enabled(False)
        self.run_utterance(h)
        self.assertEqual(self.calls(h), before)
        self.assertEqual(h.finals()[0].translation, "<zh-TW>final1")
        self.assertEqual(h.finals()[1].translation, "")
        self.assertEqual(h.logs, [])

    def test_toggle_controls_the_keep_warm_task(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep, translation_enabled=False)
        h.pipeline.set_translation_enabled(True)
        h.pipeline.set_translation_enabled(False)
        self.assertEqual(keep.calls, ["active:True", "active:False"])

    def test_keep_warm_without_set_active_is_fine(self) -> None:
        class NoToggle:
            # 只實作 BackgroundTask 的 start／stop（沒有 set_active）：切換照樣生效
            def start(self) -> None:
                pass

            def stop(self, timeout: float = 0.5) -> None:
                pass

        h = Harness(keep_warm=NoToggle())  # type: ignore[arg-type]
        h.pipeline.set_translation_enabled(False)
        self.assertFalse(h.pipeline.translation_enabled)

    def test_rejects_non_bool(self) -> None:
        h = Harness()
        with self.assertRaises(TypeError):
            h.pipeline.set_translation_enabled(1)  # type: ignore[arg-type]

    def test_threaded_toggle_while_running(self) -> None:
        h = Harness(translation_enabled=False)
        source = StoppableSource()
        h.pipeline.start(source)
        h.pipeline.set_translation_enabled(True)
        h.pipeline.stop(timeout=WAIT)
        self.assertTrue(h.pipeline.translation_enabled)


class PartialStabilityTest(unittest.TestCase):
    """`PartialStability` 的即時譯文：只能經 `record_translation()` 改變，換句子時清空。"""

    def test_translation_is_recorded_and_reset_per_utterance(self) -> None:
        from kikitori.pipeline.stability import PartialStability

        stability = PartialStability()
        stability.observe(1, "こんに")
        self.assertEqual(stability.translation, "")
        stability.record_translation("你好")
        stability.observe(1, "こんにちは")
        self.assertEqual(stability.translation, "你好")  # 同一句：沿用，不閃爍
        stability.observe(2, "さようなら")
        self.assertEqual(stability.translation, "")
        with self.assertRaises(AttributeError):
            stability.translation = "x"  # type: ignore[misc]


class StablePartialTest(unittest.TestCase):
    """F2：LocalAgreement-2 的穩定前綴（`stable_chars`）與減少即時翻譯請求。"""

    LONG = track(silence(1.0), speech(8.0), silence(1.5))

    def run_partials(self, h: Harness, texts: list[str], language: str = "ja") -> None:
        """每次餵 0.7 s（一次觸發）就跑一次，讓每個辨識結果都被處理。"""
        h.fast.script = lambda n: transcript(texts[n - 1], language)
        step = round(0.7 * SR)
        h.pipeline.feed(self.LONG[: SR + step])
        h.pipeline.run_pending()
        offset = SR + step
        while len(h.fast.calls) < len(texts):
            h.pipeline.feed(self.LONG[offset : offset + step])
            offset += step
            h.pipeline.run_pending()
        self.offset = offset

    def last_per_call(self, h: Harness) -> list[SubtitleUpdate]:
        """每次即時辨識的最後一則即時字幕（串流翻譯會送出多則）。"""
        last: list[SubtitleUpdate] = []
        for update in h.partials():
            if (
                last
                and last[-1].original == update.original
                and (last[-1].stable_chars == update.stable_chars)
            ):
                last[-1] = update
            else:
                last.append(update)
        return last

    def test_stable_chars_is_the_common_prefix_with_the_previous_partial(self) -> None:
        h = Harness()
        texts = ["あいう", "あいえ", "あいお", "あいか", "あいかき", "あ"]
        self.run_partials(h, texts)
        updates = self.last_per_call(h)
        self.assertEqual([u.original for u in updates], texts)
        # 第一則 0；之後與上一則的最長共同前綴，且不超過本則原文的長度
        self.assertEqual([u.stable_chars for u in updates], [0, 2, 2, 2, 3, 1])
        for update in h.partials():
            assert update.stable_chars is not None
            self.assertLessEqual(update.stable_chars, len(update.original))
            self.assertIsNone(update.latency_ms)

    def test_flickering_partials_request_fewer_translations(self) -> None:
        h = Harness()
        texts = ["あいう", "あいえ", "あいお", "あいか", "あいかき"]
        self.run_partials(h, texts)
        # 以前每則都翻譯（5 次）；穩定前綴只在第 1、2（0→2）、5（2→3）則變長
        self.assertEqual(
            [r.text for r in h.fast_tr.requests], ["あいう", "あいえ", "あいかき"]
        )
        updates = self.last_per_call(h)
        # 沒有重新翻譯的即時字幕沿用上一次的譯文（不閃爍、不清空）
        self.assertEqual(
            [u.translation for u in updates],
            [
                "<zh-TW>あいう",
                "<zh-TW>あいえ",
                "<zh-TW>あいえ",
                "<zh-TW>あいえ",
                "<zh-TW>あいかき",
            ],
        )
        # 句子結束時完整路徑一定翻譯，且完整字幕沒有 stable_chars
        h.pipeline.feed(self.LONG[self.offset :])
        h.pipeline.run_pending()
        final = h.finals()[-1]
        self.assertEqual(len(h.final_tr.requests), 1)
        self.assertEqual(final.translation, "<zh-TW>final1")
        self.assertIsNone(final.stable_chars)

    def test_identical_partial_is_not_translated_again(self) -> None:
        h = Harness()
        self.run_partials(h, ["あいう", "あいう"])
        self.assertEqual([r.text for r in h.fast_tr.requests], ["あいう"])
        self.assertEqual(h.partials()[-1].stable_chars, 3)

    def test_new_utterance_starts_from_zero(self) -> None:
        h = Harness()
        h.fast.script = lambda n: transcript("同じ文")
        audio = track(ONE_UTTERANCE, ONE_UTTERANCE)
        first_end = len(ONE_UTTERANCE)
        h.pipeline.feed(audio[: 2 * SR])
        h.pipeline.run_pending()
        h.pipeline.feed(audio[2 * SR : first_end + 2 * SR])
        h.pipeline.run_pending()
        partials = h.partials()
        self.assertEqual({u.utterance_id for u in partials}, {1, 2})
        second = [u for u in partials if u.utterance_id == 2]
        self.assertEqual(second[0].stable_chars, 0)
        # 新的一句重新翻譯（不沿用上一句的譯文）
        self.assertEqual(len(h.fast_tr.requests), 2)

    def test_stable_chars_uses_the_displayed_original(self) -> None:
        # 原文先經過與顯示相同的處理（去頭尾空白、字形轉換）再比較
        h = Harness(source_language="zh-TW", target_language="ja")
        self.run_partials(h, [" 这个问题", "这个问 "], language="zh")
        updates = self.last_per_call(h)
        expected = [
            display_original(text.strip(), "zh", ("zh-TW", "ja"))
            for text in (" 这个问题", "这个问 ")
        ]
        self.assertNotEqual(expected[0], "这个问题")  # 確實轉成繁體
        self.assertEqual([u.original for u in updates], expected)
        # 比較的是顯示的原文：開頭空白與字形差異不會讓穩定前綴變成 0
        self.assertEqual(updates[-1].stable_chars, 3)

    def test_not_translating_partials_still_get_stable_chars(self) -> None:
        h = Harness(translation_enabled=False)
        self.run_partials(h, ["あいう", "あいえ"])
        self.assertEqual([u.stable_chars for u in self.last_per_call(h)], [0, 2])
        self.assertEqual(h.fast_tr.requests, [])


class LatencyMetricsTest(unittest.TestCase):
    """F4：完整字幕的 latency_ms 與 `Pipeline.metrics()`。"""

    def test_final_latency_is_measured_from_the_vad_end(self) -> None:
        now = [100.0]
        h = Harness(clock=lambda: now[0])

        def slow_final(n: int) -> Transcript:
            now[0] += 0.25  # 完整辨識 250 ms
            return transcript(f"final{n}")

        h.final.script = slow_final
        h.pipeline.feed(ONE_UTTERANCE)  # VAD 在這裡判定句子結束（時鐘 100.0）
        now[0] += 0.5  # 排隊 500 ms
        h.pipeline.run_pending()
        final = h.finals()[0]
        self.assertEqual(final.latency_ms, 750)
        self.assertEqual(h.pipeline.metrics().latency_ms_median, 750)

    def test_median_of_the_last_20_finals(self) -> None:
        now = [0.0]
        h = Harness(clock=lambda: now[0])
        delays = iter([0.1 * (i + 1) for i in range(25)])

        def final(n: int) -> Transcript:
            now[0] += next(delays)
            return transcript(f"final{n}")

        h.final.script = final
        self.assertIsNone(h.pipeline.metrics().latency_ms_median)
        for _ in range(25):
            h.pipeline.feed(ONE_UTTERANCE)
            h.pipeline.run_pending()
        latencies = [u.latency_ms for u in h.finals()]
        self.assertEqual(latencies[:3], [100, 200, 300])
        # 最後 20 句是 600…2500 ms，中位數 (1500 + 1600) / 2
        self.assertEqual(h.pipeline.metrics().latency_ms_median, 1550)

    def test_cleared_final_has_latency_but_is_not_in_the_median(self) -> None:
        h = Harness()
        h.final.script = lambda n: transcript("", no_speech=0.99)
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
        h.pipeline.run_pending()
        cleared = h.finals()[0]
        self.assertEqual((cleared.original, cleared.latency_ms), ("", 0))
        self.assertIsNone(h.pipeline.metrics().latency_ms_median)

    def test_backlog_counts_utterances_waiting_for_the_final_path(self) -> None:
        h = Harness()
        self.assertEqual(h.pipeline.metrics().backlog, 0)
        h.pipeline.feed(track(*(ONE_UTTERANCE for _ in range(3))))
        self.assertEqual(h.pipeline.metrics().backlog, 3)
        h.pipeline.run_pending()
        self.assertEqual(h.pipeline.metrics().backlog, 0)

    def test_level_dbfs_of_the_last_second(self) -> None:
        h = Harness()
        self.assertEqual(h.pipeline.metrics().level_dbfs, -120.0)
        h.pipeline.feed(silence(0.5))
        self.assertEqual(h.pipeline.metrics().level_dbfs, -120.0)
        # 振幅 5000 的方波：RMS 5000 → 20·log10(5000/32768) ≈ −16.3 dBFS
        h.pipeline.feed(speech(1.0))
        self.assertAlmostEqual(h.pipeline.metrics().level_dbfs, -16.3, places=1)
        # 只看最後約 1 秒：之後 1 秒的靜音蓋掉人聲
        h.pipeline.feed(silence(1.0))
        self.assertEqual(h.pipeline.metrics().level_dbfs, -120.0)
        # 半秒人聲、半秒靜音：功率減半（−3 dB）
        h.pipeline.feed(speech(0.5))
        self.assertAlmostEqual(h.pipeline.metrics().level_dbfs, -19.3, places=1)

    def test_level_is_capped_at_full_scale(self) -> None:
        h = Harness()
        h.pipeline.feed(np.full(SR, -32768, dtype=np.int16))
        self.assertEqual(h.pipeline.metrics().level_dbfs, 0.0)

    def test_threaded_mode_meters_the_captured_audio(self) -> None:
        h = Harness()
        h.pipeline.start(ArraySource(speech(1.0)))
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertAlmostEqual(h.pipeline.metrics().level_dbfs, -16.3, places=1)


class ContextTest(unittest.TestCase):
    def test_media_context_and_asr_prompt(self) -> None:
        h = Harness()
        h.pipeline.feed(track(ONE_UTTERANCE, ONE_UTTERANCE))
        h.pipeline.run_pending()
        self.assertEqual([c.prompt for c in h.final.calls], [None, "final1"])
        self.assertEqual(h.final_tr.requests[1].context, ("final1",))

    def test_context_limited_to_context_lines(self) -> None:
        h = Harness(context_lines=1)
        h.pipeline.feed(track(*(ONE_UTTERANCE for _ in range(3))))
        h.pipeline.run_pending()
        self.assertEqual(h.final_tr.requests[2].context, ("final2",))

    def test_no_prompt_when_disabled(self) -> None:
        h = Harness(asr_prompt_context=False)
        h.pipeline.feed(track(ONE_UTTERANCE, ONE_UTTERANCE))
        h.pipeline.run_pending()
        self.assertEqual([c.prompt for c in h.final.calls], [None, None])

    def test_interpret_context_same_language_only_and_no_prompt(self) -> None:
        h = Harness("interpret")
        langs = iter(["zh", "ja", "zh"])
        h.final.script = lambda n: transcript(f"line{n}", next(langs))
        h.pipeline.feed(track(*(ONE_UTTERANCE for _ in range(3))))
        h.pipeline.run_pending()
        self.assertEqual([c.prompt for c in h.final.calls], [None, None, None])
        self.assertEqual(h.final_tr.requests[1].context, ())
        self.assertEqual(h.final_tr.requests[2].context, ("line1",))


class NoSpeechTest(unittest.TestCase):
    def test_filtered_final_without_partial_emits_nothing(self) -> None:
        h = Harness()
        h.final.script = lambda n: transcript("Thank you.", "en", no_speech=0.95)
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.updates, [])
        self.assertEqual(h.final_tr.requests, [])

    def test_filtered_final_clears_shown_partial(self) -> None:
        h = Harness()
        h.final.script = lambda n: transcript("", "ja")
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        self.assertTrue(h.partials())
        h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
        h.pipeline.run_pending()
        last = h.updates[-1]
        self.assertEqual(
            (last.is_final, last.original, last.translation), (True, "", "")
        )

    def test_filtered_partial_is_not_emitted(self) -> None:
        h = Harness()
        h.fast.script = lambda n: transcript("   ")
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        self.assertEqual(h.updates, [])
        self.assertEqual(h.fast_tr.requests, [])


class UnreliableNoSpeechTest(unittest.TestCase):
    """完整模型的 no_speech 不可靠（例如 large-v3-turbo 恆為 0）時改用即時模型的值。"""

    def harness(self, reliable: bool) -> Harness:
        h = Harness(final_no_speech_reliable=reliable)
        h.final.script = lambda n: transcript(f"final{n}", no_speech=0.0)
        h.fast.script = lambda n: transcript(f"fast{n}", no_speech=0.9)
        return h

    def test_uses_fast_no_speech_from_partial(self) -> None:
        h = self.harness(reliable=False)
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        h.pipeline.feed(ONE_UTTERANCE[2 * SR :])
        h.pipeline.run_pending()
        self.assertEqual(h.finals(), [])
        self.assertEqual(len(h.fast.calls), 1)  # 沒有補跑

    def test_runs_fast_model_when_no_partial(self) -> None:
        h = self.harness(reliable=False)
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(h.updates, [])
        self.assertEqual([c.samples for c in h.fast.calls], [h.final.calls[0].samples])

    def test_reliable_final_ignores_fast_value(self) -> None:
        h = self.harness(reliable=True)
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual([u.original for u in h.finals()], ["final1"])
        self.assertEqual(h.fast.calls, [])


class AdaptiveIntervalTest(unittest.TestCase):
    def test_slow_partials_stretch_the_interval(self) -> None:
        now = [0.0]

        def clock() -> float:
            return now[0]

        h = Harness(clock=clock)

        def slow(n: int) -> Transcript:
            now[0] += 2.0  # 每次即時辨識花 2 秒，遠超過 700 ms 間隔
            return transcript(f"fast{n}")

        h.fast.script = slow
        audio = track(silence(1.0), speech(8.0), silence(1.5))
        h.pipeline.feed(audio[: 2 * SR])
        h.pipeline.run_pending()
        h.pipeline.feed(audio[2 * SR : 4 * SR])
        h.pipeline.run_pending()
        # 間隔被拉長到 2 秒：第二段只觸發一次，沒有被丟棄的工作
        self.assertEqual(h.pipeline.dropped_partials, 0)
        self.assertEqual(len(h.fast.calls), 2)

    def test_fast_partials_keep_configured_interval(self) -> None:
        h = Harness()
        audio = track(silence(1.0), speech(8.0), silence(1.5))
        h.pipeline.feed(audio[: 2 * SR])
        h.pipeline.run_pending()
        h.pipeline.feed(audio[2 * SR : 4 * SR])
        h.pipeline.run_pending()
        self.assertEqual(h.pipeline.dropped_partials, 2)


class ErrorTest(unittest.TestCase):
    def test_translation_error_is_a_warning_and_final_still_emitted(self) -> None:
        h = Harness()
        h.final_tr.error = TranslationConnectionError("cannot connect to 127.0.0.1")
        h.pipeline.feed(track(ONE_UTTERANCE, ONE_UTTERANCE))
        h.pipeline.run_pending()
        self.assertEqual(
            [(u.original, u.translation) for u in h.finals()],
            [("final1", ""), ("final2", "")],
        )
        self.assertEqual(len([m for lvl, m in h.logs if lvl == "warning"]), 1)
        self.assertEqual(h.pipeline.state, "running")
        # 恢復後再失敗會重新回報
        h.final_tr.error = None
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        h.final_tr.error = TranslationConnectionError("cannot connect to 127.0.0.1")
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        self.assertEqual(len([m for lvl, m in h.logs if lvl == "warning"]), 2)

    def test_partial_translation_error_shows_original(self) -> None:
        h = Harness()
        h.fast_tr.error = TranslationConnectionError("down")
        h.pipeline.feed(ONE_UTTERANCE[: 2 * SR])
        h.pipeline.run_pending()
        self.assertEqual(
            [(u.original, u.translation) for u in h.partials()], [("fast1", "")]
        )

    def test_asr_error_becomes_error_state(self) -> None:
        h = Harness()
        h.final.error = RuntimeError("CUDA out of memory")
        with self.assertLogs("kikitori.pipeline.pipeline", "ERROR"):
            h.pipeline.feed(track(ONE_UTTERANCE, ONE_UTTERANCE))
            h.pipeline.run_pending()
        self.assertEqual(h.pipeline.state, "error")
        self.assertEqual(h.statuses[-1], ("error", "RuntimeError: CUDA out of memory"))
        self.assertIs(h.pipeline.error, h.final.error)
        self.assertEqual(h.finals(), [])
        # 錯誤之後不再處理
        h.pipeline.run_pending()
        self.assertEqual(len(h.final.calls), 1)

    def test_subtitle_callback_error_is_fatal(self) -> None:
        h = Harness()

        def broken(update: SubtitleUpdate) -> None:
            raise BrokenPipeError("stdout closed")

        h.on_update = broken
        with self.assertLogs("kikitori.pipeline.pipeline", "ERROR"):
            h.pipeline.feed(ONE_UTTERANCE)
            h.pipeline.run_pending()
        self.assertEqual(h.pipeline.state, "error")


class StoppableSource:
    """像真實裝置一樣「沒資料就等到逾時」的來源；stop() 會喚醒等待。"""

    def __init__(self, fail_after_start: Exception | None = None) -> None:
        self._stopped = threading.Event()
        self.started = False
        self.stop_calls = 0
        self.error = fail_after_start

    def start(self) -> None:
        self.started = True

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        if self.error is not None:
            raise self.error
        self._stopped.wait(timeout)
        return np.zeros(0, dtype=np.int16)

    def stop(self) -> None:
        self.stop_calls += 1
        self._stopped.set()


class HeldSource:
    """送出 `audio[:hold_at]` 後停住（像沒資料的裝置），`release` 設定後才送出其餘部分。

    讓測試能確定「某件事發生時句子還沒結束」，不必依賴執行緒的啟動順序。
    """

    def __init__(
        self, audio: npt.NDArray[np.int16], hold_at: int, release: threading.Event
    ) -> None:
        self._audio = audio
        self._hold_at = hold_at
        self._release = release
        self._pos = 0

    @property
    def exhausted(self) -> bool:
        return self._pos >= len(self._audio)

    def start(self) -> None:
        pass

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        if self._pos >= self._hold_at and not self._release.wait(timeout):
            return np.zeros(0, dtype=np.int16)
        limit = len(self._audio) if self._release.is_set() else self._hold_at
        end = min(self._pos + 320, limit)
        chunk = self._audio[self._pos : end].copy()
        self._pos = end
        return chunk

    def stop(self) -> None:
        pass


class ThreadedTest(unittest.TestCase):
    def test_array_source_runs_to_idle(self) -> None:
        h = Harness()
        h.pipeline.start(ArraySource(track(ONE_UTTERANCE, ONE_UTTERANCE)))
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(h.pipeline.state, "idle")
        self.assertEqual([u.utterance_id for u in h.finals()], [1, 2])
        self.assertEqual(h.statuses, [("running", None), ("idle", None)])
        self.assertIsNone(h.pipeline.error)

    def test_idle_only_after_workers_finish_even_if_capture_runs_first(self) -> None:
        # 單核 CI 上曾發生：非即時來源一下就讀完，capture 執行緒在 start() 還沒啟動
        # 兩個 worker 前就檢查 is_alive()（未啟動 = False），於是不等完整結果就送出 idle。
        # 這裡讓 worker 一律等 capture 執行完才真正啟動，重現該排程。
        h = Harness()
        capture_finished = threading.Event()
        finals_at_idle: list[int] = []
        h.pipeline._on_status = lambda state, detail: (
            finals_at_idle.append(len(h.finals())) if state == "idle" else None
        )
        real_thread = threading.Thread

        class CaptureFirstThread(real_thread):
            def run(self) -> None:
                try:
                    super().run()
                finally:
                    if self.name == "pipeline-capture":
                        capture_finished.set()

            def start(self) -> None:
                capture = next(
                    (t for t in h.pipeline._threads if t.name == "pipeline-capture"),
                    None,
                )
                capture_started = capture is not None and (
                    capture.is_alive() or capture_finished.is_set()
                )
                if self.name != "pipeline-capture" and capture_started:
                    capture_finished.wait(WAIT)
                super().start()

        with mock.patch.object(threading, "Thread", CaptureFirstThread):
            h.pipeline.start(ArraySource(track(ONE_UTTERANCE, ONE_UTTERANCE)))
        self.assertTrue(h.pipeline.wait(WAIT))
        for thread in h.pipeline._threads:
            thread.join(WAIT)
        self.assertEqual([u.utterance_id for u in h.finals()], [1, 2])
        self.assertEqual(finals_at_idle, [2])

    def test_final_backpressure_never_drops(self) -> None:
        h = Harness(final_queue_size=1)
        h.final.gate = threading.Event()
        h.pipeline.start(ArraySource(track(*(ONE_UTTERANCE for _ in range(4)))))
        self.assertTrue(h.final.entered.wait(WAIT))
        h.final.gate.set()
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual([u.utterance_id for u in h.finals()], [1, 2, 3, 4])

    def test_partial_never_after_final_of_same_utterance(self) -> None:
        h = Harness()
        h.fast.gate = threading.Event()
        final_emitted = threading.Event()
        h.on_update = lambda u: final_emitted.set() if u.is_final else None
        # 人聲送完就停住，直到即時辨識開始：讀得比 worker 啟動還快時，句子會在
        # 即時工作被取走前就結束，即時路徑根本不會執行（macOS CI 曾發生）。
        h.pipeline.start(HeldSource(ONE_UTTERANCE, 3 * SR, h.fast.entered))
        self.assertTrue(h.fast.entered.wait(WAIT))
        self.assertTrue(final_emitted.wait(WAIT))
        h.fast.gate.set()
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual([u.is_final for u in h.updates], [True])

    def test_stop_is_clean_and_idempotent(self) -> None:
        h = Harness()
        source = StoppableSource()
        h.pipeline.start(source)
        h.pipeline.stop(timeout=WAIT)
        self.assertTrue(h.pipeline.wait(0))
        self.assertEqual(h.pipeline.state, "idle")
        self.assertEqual(h.statuses, [("running", None), ("idle", None)])
        self.assertGreaterEqual(source.stop_calls, 1)
        for thread in h.pipeline._threads:
            self.assertFalse(thread.is_alive())
        h.pipeline.stop(timeout=WAIT)
        self.assertEqual(len(h.statuses), 2)

    def test_capture_error_propagates(self) -> None:
        h = Harness()
        source = StoppableSource(fail_after_start=DeviceLostError("device unplugged"))
        with self.assertLogs("kikitori.pipeline.pipeline", "ERROR"):
            h.pipeline.start(source)
            self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(h.pipeline.state, "error")
        self.assertEqual(h.statuses[-1], ("error", "DeviceLostError: device unplugged"))
        # engine 在 on_status 回呼中依例外類型決定 status 的 code（device_lost）。
        self.assertIsInstance(h.errors_at_status[-1], DeviceLostError)
        self.assertIs(h.pipeline.error, h.errors_at_status[-1])
        # 保存的例外不帶 traceback（避免經由 frame 參照回 pipeline）。
        self.assertIsNone(h.pipeline.error.__traceback__)
        self.assertGreaterEqual(source.stop_calls, 1)

    def test_worker_error_stops_pipeline(self) -> None:
        h = Harness()
        h.final.error = RuntimeError("model crashed")
        source = StoppableSource()
        # 先讓一句完整結束，再接上永不結束的來源
        h.pipeline.start(_Chain(ArraySource(ONE_UTTERANCE), source))
        with self.assertLogs("kikitori.pipeline.pipeline", "ERROR"):
            self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(h.pipeline.state, "error")
        self.assertGreaterEqual(source.stop_calls, 1)

    def test_start_failure_does_not_run(self) -> None:
        h = Harness()

        class Failing(StoppableSource):
            def start(self) -> None:
                raise DeviceLostError("no device")

        with self.assertRaises(DeviceLostError):
            h.pipeline.start(Failing())
        self.assertEqual(h.statuses, [])


class RecordingKeepWarm:
    """記錄 pipeline 對背景保溫工作的 start／stop。"""

    def __init__(self, fail_start: bool = False) -> None:
        self.calls: list[str] = []
        self.fail_start = fail_start

    def start(self) -> None:
        self.calls.append("start")
        if self.fail_start:
            raise RuntimeError("can't start new thread")

    def stop(self, timeout: float = 0.5) -> None:
        self.calls.append("stop")

    def set_active(self, active: bool) -> None:
        self.calls.append(f"active:{active}")


class KeepWarmLifecycleTest(unittest.TestCase):
    """本機翻譯模型的保溫只在 session 執行中進行，任何結束方式都會停止。"""

    def test_started_with_the_session_and_stopped_by_stop(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep)
        h.pipeline.start(StoppableSource())
        self.assertEqual(keep.calls, ["start"])
        h.pipeline.stop(timeout=WAIT)
        self.assertEqual(keep.calls, ["start", "stop"])
        h.pipeline.stop(timeout=WAIT)
        self.assertEqual(keep.calls, ["start", "stop"])

    def test_started_before_the_workers_so_an_early_end_still_stops_it(self) -> None:
        # 來源一下就讀完時，capture 執行緒可能在 start() 返回前就結束 pipeline；保溫若在
        # 那之後才啟動，就沒有人停止它，會一直送到引擎結束。
        h = Harness()
        order: list[str] = []
        keep = RecordingKeepWarm()
        keep.start = lambda: order.append("keep_warm")  # type: ignore[method-assign]
        h.pipeline = Pipeline(
            dataclasses.replace(h.pipeline._components, keep_warm=keep),
            h.config,
            on_subtitle=lambda u: None,
        )
        real_start = threading.Thread.start

        def record_start(thread: threading.Thread) -> None:
            if thread.name.startswith("pipeline-"):
                order.append(thread.name)
            real_start(thread)

        with mock.patch.object(threading.Thread, "start", record_start):
            h.pipeline.start(ArraySource(ONE_UTTERANCE))
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(order[0], "keep_warm")

    def test_stopped_when_the_source_ends(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep)
        h.pipeline.start(ArraySource(ONE_UTTERANCE))
        self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(keep.calls, ["start", "stop"])

    def test_stopped_on_error(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep)
        source = StoppableSource(fail_after_start=DeviceLostError("device unplugged"))
        with self.assertLogs("kikitori.pipeline.pipeline", "ERROR"):
            h.pipeline.start(source)
            self.assertTrue(h.pipeline.wait(WAIT))
        self.assertEqual(keep.calls, ["start", "stop"])

    def test_not_started_when_capture_fails_to_start(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep)

        class Failing(StoppableSource):
            def start(self) -> None:
                raise DeviceLostError("no device")

        with self.assertRaises(DeviceLostError):
            h.pipeline.start(Failing())
        self.assertEqual(keep.calls, [])

    def test_start_failure_is_a_warning_not_fatal(self) -> None:
        keep = RecordingKeepWarm(fail_start=True)
        h = Harness(keep_warm=keep)
        with self.assertLogs("kikitori.pipeline.pipeline", "WARNING"):
            h.pipeline.start(StoppableSource())
        self.assertEqual(h.pipeline.state, "running")
        self.assertTrue(any(lvl == "warning" for lvl, _ in h.logs))
        h.pipeline.stop(timeout=WAIT)
        self.assertEqual(h.pipeline.state, "idle")

    def test_stepped_mode_never_starts_it(self) -> None:
        keep = RecordingKeepWarm()
        h = Harness(keep_warm=keep)
        h.pipeline.feed(ONE_UTTERANCE)
        h.pipeline.run_pending()
        h.pipeline.end_of_stream()
        self.assertEqual(keep.calls, [])


class _Chain:
    """先讀完第一個來源，再讀第二個（第二個不會結束）。"""

    def __init__(self, first: ArraySource, second: StoppableSource) -> None:
        self.first = first
        self.second = second

    def start(self) -> None:
        self.first.start()
        self.second.start()

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        chunk = self.first.read(timeout)
        return chunk if len(chunk) else self.second.read(timeout)

    def stop(self) -> None:
        self.first.stop()
        self.second.stop()


if __name__ == "__main__":
    unittest.main()
