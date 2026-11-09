"""v0.3 G4：原文校正（辨識結果的固定替換）。"""

import unittest

from kikitori.pipeline.corrections import (
    MAX_CORRECTION_CHARS,
    Correction,
    Corrections,
)


class CorrectionTest(unittest.TestCase):
    def test_entries_are_trimmed_and_validated(self) -> None:
        c = Correction("  ぺこら ", " ぺこーら ")
        self.assertEqual((c.wrong, c.right), ("ぺこら", "ぺこーら"))
        for wrong, right in (
            ("", "x"),
            ("x", ""),
            ("   ", "x"),
            ("a" * (MAX_CORRECTION_CHARS + 1), "x"),
            ("x", "a" * (MAX_CORRECTION_CHARS + 1)),
            ("a\nb", "x"),
            ("x", "y\tz"),
            ("a=>b", "x"),  # 設定頁文字框的分隔符號
            ("x", "y→z"),
            ("same", " same "),  # 去頭尾空白後完全相同：沒有意義
        ):
            with self.subTest(wrong=wrong, right=right), self.assertRaises(ValueError):
                Correction(wrong, right)
        with self.assertRaises(TypeError):
            Correction(1, "x")  # type: ignore[arg-type]


class CorrectionsTest(unittest.TestCase):
    def test_replaces_every_occurrence(self) -> None:
        fix = Corrections([Correction("ホロ ライブ", "ホロライブ")])
        self.assertEqual(
            fix.apply("ホロ ライブのホロ ライブ"), "ホロライブのホロライブ"
        )

    def test_matching_ignores_case_and_width(self) -> None:
        fix = Corrections([Correction("apex", "Apex Legends")])
        self.assertEqual(fix.apply("今日はAPEXやる"), "今日はApex Legendsやる")
        # 全形英數與半形片假名（NFKC）也算同一個寫法
        self.assertEqual(fix.apply("今日はＡＰＥＸやる"), "今日はApex Legendsやる")
        # 半形片假名（ﾊﾟ 兩個字合成一個「パ」）也對得回原文的位置
        kana = Corrections([Correction("スパ茶", "スパチャ")])
        self.assertEqual(kana.apply("ｽﾊﾟ茶ありがとう"), "スパチャありがとう")
        # 半形「ｶﾞ」與全形「ガ」是同一個寫法
        self.assertEqual(
            Corrections([Correction("ガチ", "ガチ勢")]).apply("ｶﾞﾁ"), "ガチ勢"
        )

    def test_matches_never_split_a_character_from_its_combining_mark(self) -> None:
        # 「b」後面接結合重音：比對到「ab」只是那個字的一部分，必須不替換
        fix = Corrections([Correction("ab", "X")])
        self.assertEqual(fix.apply("ab\u0301c"), "ab\u0301c")
        self.assertEqual(fix.apply("ab c"), "X c")
        # casefold 會改變長度（ß → ss）：位置仍要對回原文
        sharp = Corrections([Correction("strasse", "Straße")])
        self.assertEqual(sharp.apply("die STRAßE hier"), "die Straße hier")

    def test_duplicate_spellings_are_rejected(self) -> None:
        with self.assertRaises(ValueError):
            Corrections([Correction("Apex", "a"), Correction("APEX", "b")])

    def test_longest_match_wins_and_replacements_do_not_chain(self) -> None:
        fix = Corrections(
            [
                Correction("ぺこ", "ペコ"),
                Correction("ぺこら", "ぺこーら"),
                Correction("ペコ", "これは連鎖しない"),
            ]
        )
        self.assertEqual(fix.apply("ぺこらとぺこ"), "ぺこーらとペコ")

    def test_no_entries_or_no_match_returns_the_text(self) -> None:
        self.assertEqual(Corrections([]).apply("そのまま"), "そのまま")
        self.assertEqual(
            Corrections([Correction("x", "y")]).apply("そのまま"), "そのまま"
        )
        self.assertFalse(Corrections([]))
        self.assertTrue(Corrections([Correction("x", "y")]))

    def test_capitalisation_can_be_unified(self) -> None:
        fix = Corrections([Correction("apex", "Apex")])
        self.assertEqual(fix.apply("APEX と apex と Apex"), "Apex と Apex と Apex")

    def test_regex_metacharacters_are_literal(self) -> None:
        fix = Corrections([Correction("a.b", "OK")])
        self.assertEqual(fix.apply("a.b axb"), "OK axb")


if __name__ == "__main__":
    unittest.main()
