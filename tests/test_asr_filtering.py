"""幻覺過濾的測試：每條規則的正例與反例。"""

import unittest

from kikitori.asr.filtering import (
    FilterConfig,
    compression_ratio,
    normalize_for_match,
    rejection_reason,
)
from kikitori.asr.types import Transcript


def t(text: str, no_speech: float = 0.01, logprob: float = -0.3) -> Transcript:
    return Transcript(
        text=text,
        language="ja",
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
        for text in (
            "ご視聴ありがとうございました",
            "ご視聴ありがとうございました。",
            "Thanks for watching!",
        ):
            with self.subTest(text=text):
                self.assertEqual(rejection_reason(t(text)), "known_hallucination")
        self.assertEqual(
            rejection_reason(t("字幕由Amara.org社区提供")), "known_hallucination"
        )
        self.assertIsNone(
            rejection_reason(t("今日もご視聴ありがとうございました、また明日"))
        )

    def test_soft_hallucination_depends_on_no_speech(self) -> None:
        self.assertIsNone(rejection_reason(t("Thank you.", no_speech=0.05)))
        self.assertEqual(
            rejection_reason(t("Thank you.", no_speech=0.4)), "known_hallucination"
        )
        self.assertIsNone(rejection_reason(t("おやすみなさい", no_speech=0.05)))

    def test_custom_config(self) -> None:
        config = FilterConfig(
            min_avg_logprob=-0.2, hallucinations=frozenset({"Custom phrase!"})
        )
        self.assertEqual(rejection_reason(t("ok", logprob=-0.3), config), "low_logprob")
        self.assertEqual(
            rejection_reason(t("custom phrase", logprob=-0.1), config),
            "known_hallucination",
        )
        # 自訂清單取代預設清單
        self.assertIsNone(
            rejection_reason(t("ご視聴ありがとうございました", logprob=-0.1), config)
        )

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
