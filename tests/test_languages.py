"""語言清單（`kikitori.languages`）的測試：清單內容、正規化與別名、Whisper 語言碼。"""

import unittest

from kikitori.languages import (
    LANGUAGES,
    WHISPER_CODES,
    Language,
    UnknownLanguageError,
    get_language,
    language_codes,
    normalize_language,
    to_whisper_code,
    whisper_language_name,
)


class CatalogTest(unittest.TestCase):
    def test_twenty_languages_in_spec_order(self) -> None:
        self.assertEqual(
            language_codes(),
            (
                "ja",
                "en",
                "zh-TW",
                "zh-CN",
                "ko",
                "es",
                "fr",
                "de",
                "pt",
                "ru",
                "it",
                "ar",
                "hi",
                "id",
                "vi",
                "th",
                "tr",
                "pl",
                "nl",
                "uk",
            ),
        )

    def test_both_chinese_variants_share_whisper_zh(self) -> None:
        self.assertEqual(get_language("zh-TW").whisper_code, "zh")
        self.assertEqual(get_language("zh-CN").whisper_code, "zh")

    def test_script_conversion_only_for_chinese(self) -> None:
        conversions = {
            lang.code: lang.script_conversion
            for lang in LANGUAGES
            if lang.script_conversion
        }
        self.assertEqual(conversions, {"zh-TW": "s2twp", "zh-CN": "t2s"})

    def test_flags(self) -> None:
        no_space = {lang.code for lang in LANGUAGES if lang.no_space}
        self.assertEqual(no_space, {"ja", "zh-TW", "zh-CN", "th"})
        self.assertEqual({lang.code for lang in LANGUAGES if lang.rtl}, {"ar"})

    def test_names_are_present(self) -> None:
        for lang in LANGUAGES:
            with self.subTest(code=lang.code):
                self.assertIsInstance(lang, Language)
                self.assertTrue(lang.english_name and lang.native_name)
        self.assertEqual(get_language("ko").native_name, "한국어")

    def test_whisper_codes_unique_in_catalog_order(self) -> None:
        self.assertEqual(WHISPER_CODES[:4], ("ja", "en", "zh", "ko"))
        self.assertEqual(len(WHISPER_CODES), 19)
        self.assertEqual(len(set(WHISPER_CODES)), 19)

    def test_whisper_language_name(self) -> None:
        self.assertEqual(whisper_language_name("zh"), "Chinese")
        self.assertEqual(whisper_language_name("ja"), "Japanese")
        with self.assertRaises(UnknownLanguageError):
            whisper_language_name("nn")


class NormalizeTest(unittest.TestCase):
    def test_catalog_codes_case_insensitive(self) -> None:
        self.assertEqual(normalize_language("JA"), "ja")
        self.assertEqual(normalize_language(" ko "), "ko")
        self.assertEqual(normalize_language("zh-tw"), "zh-TW")
        self.assertEqual(normalize_language("ZH-cn"), "zh-CN")

    def test_legacy_aliases(self) -> None:
        for alias in ("zh_TW", "zh-Hant", "zh-hant-tw", "zh_hant"):
            with self.subTest(alias=alias):
                self.assertEqual(normalize_language(alias), "zh-TW")
        for alias in ("zh_CN", "zh-Hans", "zh-hans-cn"):
            with self.subTest(alias=alias):
                self.assertEqual(normalize_language(alias), "zh-CN")

    def test_bare_zh_is_ambiguous_for_subtitles(self) -> None:
        # 字幕語言要決定繁簡；"zh" 只在 Whisper 層級（辨識結果）有意義。
        with self.assertRaises(UnknownLanguageError) as ctx:
            normalize_language("zh")
        self.assertIn("zh-TW", str(ctx.exception))

    def test_rejects_unknown_and_non_strings(self) -> None:
        for code in ("", "xx", "japanese", "nn", "x" * 50):
            with self.subTest(code=code), self.assertRaises(UnknownLanguageError):
                normalize_language(code)
        with self.assertRaises(UnknownLanguageError):
            normalize_language(None)  # type: ignore[arg-type]

    def test_error_message_is_truncated(self) -> None:
        with self.assertRaises(UnknownLanguageError) as ctx:
            normalize_language("y" * 500)
        self.assertLess(len(str(ctx.exception)), 120)

    def test_unknown_language_error_is_value_error(self) -> None:
        self.assertTrue(issubclass(UnknownLanguageError, ValueError))


class WhisperCodeTest(unittest.TestCase):
    def test_accepts_whisper_and_catalog_codes(self) -> None:
        self.assertEqual(to_whisper_code("zh"), "zh")
        self.assertEqual(to_whisper_code("zh-TW"), "zh")
        self.assertEqual(to_whisper_code("zh_hans"), "zh")
        self.assertEqual(to_whisper_code("JA"), "ja")
        self.assertEqual(to_whisper_code("uk"), "uk")

    def test_rejects_whisper_languages_outside_the_catalog(self) -> None:
        for code in ("nn", "yue", "", "zh-HK"):
            with self.subTest(code=code), self.assertRaises(UnknownLanguageError):
                to_whisper_code(code)


if __name__ == "__main__":
    unittest.main()
