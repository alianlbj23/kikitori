"""幻覺過濾的測試：每條規則的正例與反例。"""

import unittest

from kikitori.asr.filtering import (
    ANY_LANGUAGE,
    DEFAULT_HALLUCINATIONS,
    FilterConfig,
    compression_ratio,
    normalize_for_match,
    rejection_reason,
)
from kikitori.asr.types import Transcript
from kikitori.languages import WHISPER_CODES


def t(
    text: str, no_speech: float = 0.01, logprob: float = -0.3, language: str = "ja"
) -> Transcript:
    return Transcript(
        text=text,
        language=language,
        language_probability=0.9,
        no_speech_probability=no_speech,
        avg_logprob=logprob,
    )


class RejectionReasonTest(unittest.TestCase):
    def test_normal_speech_is_kept(self) -> None:
        for text in ("あなた、キンセーさんのご親戚？", "Hello there.", "我們走吧"):
            with self.subTest(text=text):
                self.assertIsNone(rejection_reason(t(text)))

    def test_empty_or_punctuation_only(self) -> None:
        for text in ("", "   ", "。", "...", "♪♪♪", "「」？！"):
            with self.subTest(text=text):
                self.assertEqual(rejection_reason(t(text)), "empty")

    def test_whisper_silence_rule_needs_both_conditions(self) -> None:
        self.assertEqual(
            rejection_reason(t("うん", no_speech=0.7, logprob=-1.2)), "no_speech"
        )
        # no_speech 高但信心足夠：保留（同 Whisper）
        self.assertIsNone(rejection_reason(t("うん", no_speech=0.7, logprob=-0.5)))
        # logprob 低但 no_speech 低：不算靜音（可能被 low_logprob 規則處理）
        self.assertIsNone(rejection_reason(t("うん", no_speech=0.1, logprob=-1.2)))

    def test_very_high_no_speech_alone(self) -> None:
        self.assertEqual(
            rejection_reason(t("うん", no_speech=0.97, logprob=-0.2)), "no_speech_high"
        )
        self.assertEqual(
            rejection_reason(t("Teksting av", no_speech=0.88, logprob=-0.5)),
            "no_speech_high",
        )
        # 本機實測語音的 no_speech 最高約 0.43，要保留
        self.assertIsNone(rejection_reason(t("うん", no_speech=0.45, logprob=-0.2)))

    def test_large_v3_silence_credit_line(self) -> None:
        self.assertEqual(
            rejection_reason(t("Teksting av Nicolai Winther", no_speech=0.1)),
            "known_hallucination",
        )

    def test_low_logprob(self) -> None:
        self.assertEqual(rejection_reason(t("らららら", logprob=-1.8)), "low_logprob")

    def test_repetition_loop(self) -> None:
        self.assertEqual(rejection_reason(t("ありがとう" * 30)), "repetitive")

    def test_known_hallucinations_match_whole_sentence_only(self) -> None:
        for text, language in (
            ("ご視聴ありがとうございました", "ja"),
            ("ご視聴ありがとうございました。", "ja"),
            ("Thanks for watching!", "en"),
            ("谢谢观看", "zh"),
        ):
            with self.subTest(text=text):
                self.assertEqual(
                    rejection_reason(t(text, language=language)), "known_hallucination"
                )
        # 字幕署名不是語音：不論 Whisper 標成哪個語言都過濾
        self.assertEqual(
            rejection_reason(t("字幕由Amara.org社区提供")), "known_hallucination"
        )
        self.assertIsNone(
            rejection_reason(t("今日もご視聴ありがとうございました、また明日"))
        )

    def test_soft_hallucination_depends_on_no_speech(self) -> None:
        self.assertIsNone(
            rejection_reason(t("Thank you.", no_speech=0.05, language="en"))
        )
        self.assertEqual(
            rejection_reason(t("Thank you.", no_speech=0.4, language="en")),
            "known_hallucination",
        )
        self.assertIsNone(rejection_reason(t("おやすみなさい", no_speech=0.05)))

    def test_hard_phrases_apply_regardless_of_detected_language(self) -> None:
        # 整句的字幕套話幾乎不可能是另一個語言的正常語音；Whisper 常把它們標成別的語言
        # （例如英文套話標成 nl、中文套話標成 ja），只查辨識語言的表就會漏網。
        for text, language in (
            ("Thanks for watching!", "nl"),
            ("Please subscribe.", "ja"),
            ("谢谢观看", "ja"),
            ("謝謝觀看", "ja"),
            ("ご視聴ありがとうございました", "zh"),
            ("ご視聴ありがとうございました", "en"),
            ("Subtitles by the Amara.org community", "nl"),
            ("字幕由Amara.org社区提供", "ja"),
        ):
            with self.subTest(text=text, language=language):
                self.assertEqual(
                    rejection_reason(t(text, language=language)), "known_hallucination"
                )

    def test_soft_phrases_apply_only_to_their_own_language(self) -> None:
        # 短句（「Thank you.」「谢谢」）真人也常說，只在辨識語言相同、no_speech 偏高時濾掉。
        for text, language in (
            ("Thank you.", "en"),
            ("you", "en"),
            ("谢谢", "zh"),
            ("ありがとうございました", "ja"),
        ):
            with self.subTest(text=text, language=language):
                self.assertEqual(
                    rejection_reason(t(text, no_speech=0.4, language=language)),
                    "known_hallucination",
                )
                self.assertIsNone(
                    rejection_reason(t(text, no_speech=0.05, language=language))
                )
        for text, language in (
            ("Thank you.", "ja"),
            ("Thank you.", "nl"),
            ("谢谢", "ja"),
            ("ありがとうございました", "zh"),
        ):
            with self.subTest(text=text, language=language):
                self.assertIsNone(
                    rejection_reason(t(text, no_speech=0.4, language=language))
                )

    def test_unknown_language_gets_hard_phrases_and_thresholds_only(self) -> None:
        self.assertEqual(
            rejection_reason(t("Thanks for watching!", language="xx")),
            "known_hallucination",
        )
        self.assertIsNone(
            rejection_reason(t("Thank you.", no_speech=0.4, language="xx"))
        )
        self.assertEqual(
            rejection_reason(t("うん", no_speech=0.97, language="xx")),
            "no_speech_high",
        )

    def test_tables_are_keyed_by_whisper_language_with_hard_and_soft(self) -> None:
        for key, entry in DEFAULT_HALLUCINATIONS.items():
            with self.subTest(key=key):
                self.assertTrue(key == ANY_LANGUAGE or key in WHISPER_CODES)
                self.assertEqual(set(entry), {"hard", "soft"})
        # 署名類幻覺不分語言：large-v3 在靜音上輸出這句挪威語時，語言標籤是候選語言之一
        # （例如 ja），只放在 "no" 表就永遠比對不到。
        self.assertIn(
            "Teksting av Nicolai Winther", DEFAULT_HALLUCINATIONS[ANY_LANGUAGE]["hard"]
        )

    def test_per_language_custom_config(self) -> None:
        config = FilterConfig(
            hallucinations={"ko": {"hard": ["구독 좋아요"], "soft": ["네"]}}
        )
        for language in ("ko", "en"):
            with self.subTest(language=language):
                self.assertEqual(
                    rejection_reason(t("구독, 좋아요!", language=language), config),
                    "known_hallucination",
                )
        self.assertEqual(
            rejection_reason(t("네.", no_speech=0.4, language="ko"), config),
            "known_hallucination",
        )
        self.assertIsNone(
            rejection_reason(t("네.", no_speech=0.4, language="ja"), config)
        )
        # 只給 hard 或只給 soft 都可以；地區碼取主語言
        config = FilterConfig(hallucinations={"ko-KR": {"soft": ["네"]}})
        self.assertEqual(
            rejection_reason(t("네", no_speech=0.4, language="ko"), config),
            "known_hallucination",
        )

    def test_language_agnostic_soft_phrases(self) -> None:
        config = FilterConfig(hallucinations={ANY_LANGUAGE: {"soft": ["Hmm."]}})
        self.assertEqual(
            rejection_reason(t("hmm", no_speech=0.4, language="de"), config),
            "known_hallucination",
        )
        self.assertIsNone(
            rejection_reason(t("hmm", no_speech=0.05, language="de"), config)
        )

    def test_custom_config(self) -> None:
        config = FilterConfig(
            min_avg_logprob=-0.2, hallucinations=frozenset({"Custom phrase!"})
        )
        self.assertEqual(rejection_reason(t("ok", logprob=-0.3), config), "low_logprob")
        self.assertEqual(
            rejection_reason(t("custom phrase", logprob=-0.1), config),
            "known_hallucination",
        )
        # 自訂清單取代預設清單（含 soft）
        self.assertIsNone(
            rejection_reason(t("ご視聴ありがとうございました", logprob=-0.1), config)
        )
        self.assertIsNone(
            rejection_reason(
                t("Thank you.", no_speech=0.4, logprob=-0.1, language="en"), config
            )
        )

    def test_invalid_phrase_tables(self) -> None:
        for table, error in (
            ("Thanks for watching!", TypeError),
            ({"ja": ["ご視聴ありがとうございました"]}, TypeError),
            ({"ja": {"hard": "ご視聴ありがとうございました"}}, TypeError),
            ({"ja": {"hrad": ["ご視聴ありがとうございました"]}}, ValueError),
            ({3: {"hard": ["x"]}}, TypeError),
        ):
            with self.subTest(table=table), self.assertRaises(error):
                FilterConfig(hallucinations=table)  # type: ignore[arg-type]

    def test_invalid_config(self) -> None:
        for kwargs in (
            {"no_speech_threshold": 1.5},
            {"hard_no_speech_threshold": -0.1},
            {"soft_no_speech_threshold": 2.0},
            {"compression_ratio_threshold": 0},
        ):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                FilterConfig(**kwargs)  # type: ignore[arg-type]


class HelperTest(unittest.TestCase):
    def test_normalize(self) -> None:
        self.assertEqual(
            normalize_for_match(" Ｔｈａｎｋｓ, for watching! "), "thanksforwatching"
        )
        self.assertEqual(
            normalize_for_match("ご視聴、ありがとう。"), "ご視聴ありがとう"
        )

    def test_compression_ratio(self) -> None:
        self.assertEqual(compression_ratio(""), 0.0)
        self.assertLess(compression_ratio("短い文"), 1.5)
        self.assertGreater(compression_ratio("ha" * 100), 2.4)


if __name__ == "__main__":
    unittest.main()
