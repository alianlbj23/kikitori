"""術語表：出現才注入、最長比對、上限、使用者覆寫、各提示詞格式。"""

import unittest

from kikitori.languages import language_codes
from kikitori.translate.glossary import (
    BUILTIN_TARGET_LANGUAGES,
    BUILTIN_TERMS,
    MAX_TERMS_PER_REQUEST,
    ZH_JA_FALSE_FRIENDS,
    Glossary,
    GlossaryEntry,
    Term,
    match_key,
)
from kikitori.translate.prompts import build_messages
from kikitori.translate.types import TranslationRequest


class BuiltinDataTest(unittest.TestCase):
    def test_size_and_languages(self) -> None:
        self.assertGreaterEqual(len(BUILTIN_TERMS), 30)
        self.assertLessEqual(len(BUILTIN_TERMS), 60)
        for term in BUILTIN_TERMS:
            with self.subTest(sources=term.sources):
                self.assertTrue(term.sources and term.targets)
                self.assertLessEqual(set(term.targets), set(BUILTIN_TARGET_LANGUAGES))
                self.assertTrue(set(term.targets) <= set(language_codes()))
                for value in (*term.sources, *term.targets.values()):
                    self.assertEqual(value, value.strip())
                    self.assertTrue(value and value.isprintable())

    def test_no_variant_appears_twice(self) -> None:
        keys = [match_key(s) for t in BUILTIN_TERMS for s in t.sources]
        self.assertEqual(len(keys), len(set(keys)))

    def test_platform_official_names(self) -> None:
        g = Glossary()
        self.assertIn(
            Term("スパチャ", "超級留言"), g.lookup("スパチャありがとう", "ja", "zh-TW")
        )
        self.assertIn(
            Term("メンバーシップ", "頻道會員"),
            g.lookup("メンバーシップ入ってね", "ja", "zh-TW"),
        )
        self.assertIn(
            Term("スパチャ", "Super Chat"), g.lookup("スパチャありがとう", "ja", "en")
        )

    def test_ambiguous_bare_words_are_not_builtin(self) -> None:
        # 單獨的「草」「箱」「枠」在一般句子裡太常見（草原、箱、枠組み），不收。
        g = Glossary()
        for text in ["草原を走る", "箱を開けて", "枠組みを作る", "おつりをください"]:
            with self.subTest(text=text):
                self.assertEqual(g.lookup(text, "ja", "zh-TW"), ())


class ChineseToJapaneseFalseFriendsTest(unittest.TestCase):
    """中文原文 → 日文：中日同形異義詞與斷詞陷阱（experiments/kanji_false_friends）。"""

    def test_data_is_well_formed(self) -> None:
        self.assertGreaterEqual(len(ZH_JA_FALSE_FRIENDS), 15)
        self.assertLessEqual(len(ZH_JA_FALSE_FRIENDS), 60)
        keys = [match_key(s) for t in ZH_JA_FALSE_FRIENDS for s in t.sources]
        self.assertEqual(len(keys), len(set(keys)))
        for term in ZH_JA_FALSE_FRIENDS:
            with self.subTest(sources=term.sources):
                self.assertEqual(set(term.targets), {"ja"})
                # 單字太容易出現在別的詞裡（「娘」在「姑娘」「新娘」），一律不收
                self.assertTrue(all(len(s) >= 2 for s in term.sources))
                for value in (*term.sources, *term.targets.values()):
                    self.assertEqual(value, value.strip())
                    self.assertTrue(value and value.isprintable())

    def test_compound_wins_over_its_parts(self) -> None:
        # 「豬排便當」整個比對到，模型就不會切成 豬／排便／當
        g = Glossary()
        self.assertEqual(
            g.lookup("我今天要吃豬排便當", "zh", "ja"),
            (Term("豬排便當", "豚カツ弁当"),),
        )
        self.assertEqual(
            g.lookup("午餐吃猪排便当好了", "zh", "ja"),
            (Term("猪排便当", "豚カツ弁当"),),
        )
        self.assertEqual(
            g.lookup("這家的豬排很好吃", "zh", "ja"), (Term("豬排", "豚カツ"),)
        )

    def test_common_false_friends(self) -> None:
        g = Glossary()
        for text, term in [
            ("大家晚安", Term("大家", "みんな")),
            ("我每天都看新聞", Term("新聞", "ニュース")),
            ("直播結束了", Term("結束", "終わる")),
            ("放心吧", Term("放心", "安心する")),
        ]:
            with self.subTest(text=text):
                self.assertIn(term, g.lookup(text, "zh", "ja"))

    def test_longer_phrases_shadow_risky_parts(self) -> None:
        # 「老婆婆」是老太太，不能被「老婆」（妻子）吃掉
        g = Glossary()
        terms = g.lookup("老婆婆在公園散步", "zh", "ja")
        self.assertIn(Term("老婆婆", "おばあさん"), terms)
        self.assertNotIn(Term("老婆", "妻"), terms)

    def test_only_for_chinese_to_japanese_and_follows_the_builtin_switch(self) -> None:
        self.assertEqual(Glossary().lookup("我今天要吃豬排便當", "zh", "en"), ())
        self.assertEqual(Glossary().lookup("我今天要吃豬排便當", "ja", "zh-TW"), ())
        self.assertEqual(
            Glossary(builtin=False).lookup("我今天要吃豬排便當", "zh", "ja"), ()
        )

    def test_user_entries_override(self) -> None:
        g = Glossary(entries=(GlossaryEntry("大家", "皆さん", "ja"),))
        self.assertEqual(g.lookup("大家好", "zh", "ja"), (Term("大家", "皆さん"),))

    def test_ordinary_sentences_get_nothing(self) -> None:
        g = Glossary()
        for text in [
            "謝謝你們今天來看直播",
            "這個遊戲真的太難了吧",
            "我們一起去看煙火吧",
        ]:
            with self.subTest(text=text):
                self.assertEqual(g.lookup(text, "zh", "ja"), ())


class LookupTest(unittest.TestCase):
    def test_only_terms_present_in_the_source_are_returned(self) -> None:
        g = Glossary()
        self.assertEqual(g.lookup("今日はいい天気ですね。", "ja", "zh-TW"), ())
        self.assertEqual(
            g.lookup("赤スパありがとうございます！", "ja", "zh-TW"),
            (Term("赤スパ", "紅色超級留言"),),
        )

    def test_longest_match_wins_and_order_follows_the_text(self) -> None:
        g = Glossary()
        self.assertEqual(
            g.lookup(
                "切り抜き師さん、歌枠のスパチャ読みも切り抜きにして", "ja", "zh-TW"
            ),
            (
                Term("切り抜き師", "剪輯師"),
                Term("歌枠", "歌回"),
                Term("スパチャ読み", "唸超級留言"),
                Term("切り抜き", "精華剪輯"),
            ),
        )

    def test_width_and_case_insensitive(self) -> None:
        g = Glossary()
        self.assertEqual(
            g.lookup("草生えるｗｗｗ", "ja", "en"),
            (Term("草生え", "lol"), Term("www", "lol")),
        )
        self.assertEqual(
            g.lookup("ｽﾊﾟﾁｬ感謝", "ja", "ko"), (Term("スパチャ", "슈퍼챗"),)
        )
        user = Glossary([GlossaryEntry("Pekora", "佩克拉")], builtin=False)
        self.assertEqual(
            user.lookup("ＰＥＫＯＲＡ大好き", "ja", "zh-TW"),
            (Term("Pekora", "佩克拉"),),
        )

    def test_repeated_term_listed_once(self) -> None:
        g = Glossary()
        self.assertEqual(
            g.lookup("スパチャ、スパチャ、またスパチャ", "ja", "zh-TW"),
            (Term("スパチャ", "超級留言"),),
        )

    def test_cap(self) -> None:
        entries = [GlossaryEntry(f"語{i}", f"詞{i}") for i in range(20)]
        g = Glossary(entries, builtin=False)
        text = "".join(f"語{i}、" for i in range(20))
        terms = g.lookup(text, "ja", "zh-TW")
        self.assertEqual(len(terms), MAX_TERMS_PER_REQUEST)
        self.assertEqual(terms[0], Term("語0", "詞0"))

    def test_builtin_only_for_japanese_source_and_known_targets(self) -> None:
        g = Glossary()
        self.assertEqual(g.lookup("スパチャ", "en", "zh-TW"), ())
        self.assertEqual(g.lookup("スパチャ", "ja", "fr"), ())
        # 沒有可靠韓文譯法的詞條不給韓文
        self.assertEqual(g.lookup("てぇてぇ", "ja", "ko"), ())
        self.assertEqual(
            g.lookup("てぇてぇ", "ja", "zh-CN"), (Term("てぇてぇ", "太尊了"),)
        )

    def test_builtin_can_be_disabled(self) -> None:
        self.assertEqual(Glossary(builtin=False).lookup("スパチャ", "ja", "zh-TW"), ())

    def test_user_entries_override_builtin_and_specific_language_wins(self) -> None:
        g = Glossary(
            [
                GlossaryEntry("スパチャ", "SC"),
                GlossaryEntry("スパチャ", "斗內", "zh-TW"),
                GlossaryEntry("ぺこら", "佩克拉", "zh-TW"),
            ]
        )
        self.assertEqual(
            g.lookup("ぺこらにスパチャ", "ja", "zh-TW"),
            (Term("ぺこら", "佩克拉"), Term("スパチャ", "斗內")),
        )
        self.assertEqual(
            g.lookup("ぺこらにスパチャ", "ja", "en"), (Term("スパチャ", "SC"),)
        )

    def test_user_entries_apply_to_any_source_language(self) -> None:
        g = Glossary([GlossaryEntry("Hololive", "hololive")])
        self.assertEqual(
            g.lookup("Welcome to Hololive", "en", "zh-TW"),
            (Term("Hololive", "hololive"),),
        )

    def test_regex_characters_are_literal(self) -> None:
        g = Glossary([GlossaryEntry("a.b", "X"), GlossaryEntry("(c)", "Y")])
        self.assertEqual(g.lookup("axb (c)", "en", "zh-TW"), (Term("(c)", "Y"),))


class EntryValidationTest(unittest.TestCase):
    def test_rejects_bad_values(self) -> None:
        for kwargs, error in [
            ({"source": "", "target": "x"}, ValueError),
            ({"source": " ", "target": "x"}, ValueError),
            ({"source": " a", "target": "x"}, ValueError),
            ({"source": "a", "target": ""}, ValueError),
            ({"source": "a\nb", "target": "x"}, ValueError),
            ({"source": "a" * 101, "target": "x"}, ValueError),
            ({"source": "a", "target": "x" * 201}, ValueError),
            ({"source": 1, "target": "x"}, TypeError),
            ({"source": "a", "target": None}, TypeError),
            ({"source": "a", "target": "x", "target_language": 3}, TypeError),
        ]:
            with self.subTest(kwargs=kwargs), self.assertRaises(error):
                GlossaryEntry(**kwargs)  # type: ignore[arg-type]


class PromptTermsTest(unittest.TestCase):
    TERMS = (Term("赤スパ", "紅色超級留言"), Term("推し", "推"))

    def request(self, target: str = "zh-TW", context: tuple[str, ...] = ()):
        return TranslationRequest("赤スパありがとう！推しが尊い", "ja", target, context)

    def test_generic_zh_tw_terminology_block_in_traditional_chinese(self) -> None:
        user = build_messages("generic", self.request(), self.TERMS)[1]["content"]
        self.assertIn("術語", user)
        self.assertIn("赤スパ → 紅色超級留言\n推し → 推", user)
        # 術語在指示與原文之前、前文之後
        self.assertLess(user.index("術語"), user.index("請翻譯成繁體中文"))

    def test_generic_block_after_context(self) -> None:
        user = build_messages("generic", self.request(context=("前の文",)), self.TERMS)[
            1
        ]["content"]
        self.assertLess(user.index("前の文"), user.index("術語"))

    def test_generic_localized_headers(self) -> None:
        for target, header in [
            ("zh-CN", "术语"),
            ("ja", "用語"),
            ("en", "Terminology"),
            ("fr", "Terminology"),
        ]:
            with self.subTest(target=target):
                user = build_messages(
                    "generic",
                    TranslationRequest("スパチャ", "ja", target),
                    (Term("スパチャ", "X"),),
                )[1]["content"]
                self.assertIn(header, user)
                self.assertIn("スパチャ → X", user)

    def test_generic_without_terms_is_unchanged(self) -> None:
        self.assertEqual(
            build_messages("generic", self.request(), ()),
            build_messages("generic", self.request()),
        )
        self.assertNotIn(
            "術語", build_messages("generic", self.request())[1]["content"]
        )

    def test_hunyuan_uses_official_terminology_template(self) -> None:
        (message,) = build_messages("hunyuan_mt", self.request(), self.TERMS)
        self.assertEqual(
            message["content"],
            "参考下面的翻译：\n"
            "赤スパ 翻译成 紅色超級留言\n"
            "推し 翻译成 推\n\n"
            "将以下文本翻译为繁体中文，注意只需要输出翻译后的结果，不要额外解释：\n"
            "赤スパありがとう！推しが尊い",
        )

    def test_hunyuan_terminology_for_non_chinese_target_uses_chinese_name(self) -> None:
        (message,) = build_messages(
            "hunyuan_mt", self.request("en"), (Term("赤スパ", "red Super Chat"),)
        )
        self.assertTrue(message["content"].startswith("参考下面的翻译：\n"))
        self.assertIn("将以下文本翻译为英语", message["content"])

    def test_sakura_uses_gpt_dict_format(self) -> None:
        user = build_messages("sakura", self.request(), self.TERMS)[1]["content"]
        self.assertEqual(
            user,
            "参考以下术语表（可为空，格式为src->dst #备注）：\n"
            "赤スパ->紅色超級留言\n推し->推\n"
            "根据以上术语表的对应关系和备注，结合历史剧情和上下文，"
            "将下面的文本从日文翻译成简体中文：\n"
            "赤スパありがとう！推しが尊い",
        )

    def test_sakura_without_terms_is_unchanged(self) -> None:
        self.assertEqual(
            build_messages("sakura", self.request(), ()),
            build_messages("sakura", self.request()),
        )


if __name__ == "__main__":
    unittest.main()
