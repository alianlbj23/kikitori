"""振假名的對齊演算法：只標漢字、送り仮名不重複、逐段對齊、對不齊整詞標或不標。"""

import unittest

from kikitori.furigana.ruby import (
    Token,
    align,
    has_kanji,
    is_kanji,
    ruby_for_tokens,
    to_hiragana,
)


class CharacterClassTest(unittest.TestCase):
    def test_kanji_and_iteration_marks(self) -> None:
        for ch in "食腹々〆〇ヶ𠮷":
            with self.subTest(ch=ch):
                self.assertTrue(is_kanji(ch))
        for ch in "たターａA1。、":
            with self.subTest(ch=ch):
                self.assertFalse(is_kanji(ch))

    def test_has_kanji(self) -> None:
        self.assertTrue(has_kanji("お腹"))
        self.assertFalse(has_kanji("ありがとう"))

    def test_to_hiragana(self) -> None:
        self.assertEqual(to_hiragana("タベル"), "たべる")
        self.assertEqual(to_hiragana("ヴァイオリン"), "ゔぁいおりん")
        self.assertEqual(to_hiragana("スゲー"), "すげー")
        self.assertEqual(to_hiragana("abc"), "abc")


class AlignTest(unittest.TestCase):
    def test_okurigana_is_not_repeated(self) -> None:
        self.assertEqual(align("食べる", "タベル"), [(0, 1, "た")])

    def test_kana_prefix(self) -> None:
        self.assertEqual(align("お腹", "オナカ"), [(1, 2, "なか")])

    def test_all_kanji_word_is_one_span(self) -> None:
        self.assertEqual(align("日本", "ニホン"), [(0, 2, "にほん")])

    def test_kana_between_kanji_aligns_each_run(self) -> None:
        self.assertEqual(
            align("取り扱い", "トリアツカイ"), [(0, 1, "と"), (2, 3, "あつか")]
        )
        self.assertEqual(align("引き出し", "ヒキダシ"), [(0, 1, "ひ"), (2, 3, "だ")])

    def test_katakana_in_surface_matches_reading(self) -> None:
        self.assertEqual(align("ヶ月", "カゲツ"), [(0, 2, "かげつ")])
        self.assertEqual(align("駅ビル", "エキビル"), [(0, 1, "えき")])

    def test_no_kanji_gives_nothing(self) -> None:
        self.assertEqual(align("ありがとう", "アリガトウ"), [])
        self.assertEqual(align("ゲーム", "ゲーム"), [])

    def test_unusable_reading_gives_nothing(self) -> None:
        self.assertEqual(align("漢字", ""), [])
        self.assertEqual(align("漢字", "漢字"), [])
        self.assertEqual(align("鬱", "Utsu"), [])

    def test_mismatched_kana_falls_back_to_the_whole_word(self) -> None:
        # 原文的假名與讀音對不上（口語的「ぇ」）：去掉相同的頭尾後整段標
        self.assertEqual(align("凄ぇ", "スゴイ"), [(0, 2, "すごい")])

    def test_ambiguous_split_is_annotated_as_a_whole(self) -> None:
        self.assertEqual(align("木の葉", "コノハ"), [(0, 1, "こ"), (2, 3, "は")])
        # 「の」可以放在讀音的兩個位置：逐段對齊不唯一，整詞標而不是猜
        self.assertEqual(align("野の野", "ノノノノ"), [(0, 3, "のののの")])

    def test_symbols_inside_the_word_give_nothing(self) -> None:
        self.assertEqual(align("3日", "ミッカ"), [])

    def test_over_long_reading_is_not_annotated(self) -> None:
        # UI 與 Rust 會因一段超長讀音丟掉整句的 ruby：引擎端就不標這個詞
        self.assertEqual(align("漢", "ア" * 65), [])
        self.assertEqual(align("漢", "ア" * 64), [(0, 1, "あ" * 64)])

    def test_many_interleaved_runs_are_annotated_as_a_whole(self) -> None:
        # 段數太多不逐段對齊（避免正規表示式回溯），整詞標
        surface = "漢あ" * 9
        self.assertEqual(align(surface, "かあ" * 9), [(0, 17, "かあ" * 8 + "か")])

    def test_offsets_are_code_points(self) -> None:
        self.assertEqual(align("𠮷野", "ヨシノ"), [(0, 2, "よしの")])


class RubyForTokensTest(unittest.TestCase):
    def test_offsets_are_relative_to_the_text(self) -> None:
        text = "今日は食べる"
        tokens = [
            Token("今日", "キョウ"),
            Token("は", "ハ"),
            Token("食べる", "タベル"),
        ]
        self.assertEqual(
            ruby_for_tokens(text, tokens), [(0, 2, "きょう"), (3, 4, "た")]
        )

    def test_finer_parts_are_used_when_their_readings_agree(self) -> None:
        token = Token(
            "外国人参政権",
            "ガイコクジンサンセイケン",
            (
                Token("外国", "ガイコク"),
                Token("人", "ジン"),
                Token("参政", "サンセイ"),
                Token("権", "ケン"),
            ),
        )
        self.assertEqual(
            ruby_for_tokens("外国人参政権", [token]),
            [(0, 2, "がいこく"), (2, 3, "じん"), (3, 5, "さんせい"), (5, 6, "けん")],
        )

    def test_parts_with_different_readings_are_ignored(self) -> None:
        # 連濁：整詞讀「にほんばし」，拆開是「にほん」「はし」，拆開的讀音不能用
        token = Token(
            "日本橋",
            "ニホンバシ",
            (Token("日本", "ニホン"), Token("橋", "ハシ")),
        )
        self.assertEqual(ruby_for_tokens("日本橋", [token]), [(0, 3, "にほんばし")])

    def test_tokens_not_found_in_the_text_are_skipped(self) -> None:
        tokens = [Token("東京", "トウキョウ"), Token("大阪", "オオサカ")]
        self.assertEqual(ruby_for_tokens("東京と京都", tokens), [(0, 2, "とうきょう")])

    def test_spaces_between_tokens(self) -> None:
        tokens = [Token("今日", "キョウ"), Token("明日", "アシタ")]
        self.assertEqual(
            ruby_for_tokens("今日 明日", tokens), [(0, 2, "きょう"), (3, 5, "あした")]
        )

    def test_spans_are_sorted_and_do_not_overlap(self) -> None:
        tokens = [Token("東京", "トウキョウ"), Token("東京", "トウキョウ")]
        spans = ruby_for_tokens("東京東京", tokens)
        self.assertEqual(spans, [(0, 2, "とうきょう"), (2, 4, "とうきょう")])


if __name__ == "__main__":
    unittest.main()
