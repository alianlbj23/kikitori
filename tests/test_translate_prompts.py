"""翻譯模組中不碰網路的部分：請求驗證、提示詞、預設組、後處理、VRAM 推薦。"""

import unittest

from kikitori.languages import WHISPER_CODES, get_language, language_codes
from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.languages import (
    ALL_LANGUAGE_CODES,
    SAKURA_PAIRS,
    normalize_language,
    validate_request,
)
from kikitori.translate.postprocess import (
    clean_subtitle,
    finalize_translation,
    simplified_characters,
    strip_thinking,
    to_taiwan_traditional,
)
from kikitori.translate.presets import (
    PRESETS,
    TranslationModelChoice,
    preset_for_model,
    recommend_translation_models,
    supports,
)
from kikitori.translate.prompts import build_messages
from kikitori.translate.types import TranslationRequest


class NormalizeLanguageTest(unittest.TestCase):
    def test_accepts_known_codes_case_insensitively(self) -> None:
        self.assertEqual(normalize_language("JA"), "ja")
        self.assertEqual(normalize_language(" en "), "en")
        self.assertEqual(normalize_language("zh_tw"), "zh-TW")
        self.assertEqual(normalize_language("zh-Hant"), "zh-TW")
        self.assertEqual(normalize_language("zh-cn"), "zh-CN")
        self.assertEqual(normalize_language("KO"), "ko")

    def test_rejects_unknown_codes(self) -> None:
        # "zh" 不分繁簡，不能當譯文語言（原文語言另由 Whisper 語言碼處理）。
        for code in ["xx", "", "japanese", "zh", "nn", "x" * 50]:
            with self.subTest(code=code), self.assertRaises(InvalidRequestError):
                normalize_language(code)

    def test_rejects_non_string(self) -> None:
        with self.assertRaises(InvalidRequestError):
            normalize_language(None)  # type: ignore[arg-type]


class ValidateRequestTest(unittest.TestCase):
    def test_normalizes_supported_request(self) -> None:
        req = validate_request(
            TranslationRequest("  おはよう ", "JA", "zh_TW", ("", " 前の文 "))
        )
        self.assertEqual(
            req, TranslationRequest("おはよう", "ja", "zh-TW", ("前の文",))
        )

    def test_traditional_chinese_source_is_treated_as_zh(self) -> None:
        req = validate_request(TranslationRequest("你好", "zh-TW", "ja"))
        self.assertEqual((req.source_language, req.target_language), ("zh", "ja"))

    def test_any_catalog_pair_with_different_whisper_languages(self) -> None:
        for source in WHISPER_CODES:
            for target in language_codes():
                if get_language(target).whisper_code == source:
                    continue
                with self.subTest(pair=(source, target)):
                    req = validate_request(TranslationRequest("x", source, target))
                    self.assertEqual(
                        (req.source_language, req.target_language), (source, target)
                    )

    def test_catalog_source_codes_become_whisper_codes(self) -> None:
        req = validate_request(TranslationRequest("你好", "zh-CN", "ko"))
        self.assertEqual((req.source_language, req.target_language), ("zh", "ko"))
        req = validate_request(TranslationRequest("hola", "ES", "zh_cn"))
        self.assertEqual((req.source_language, req.target_language), ("es", "zh-CN"))

    def test_rejects_empty_or_blank_text(self) -> None:
        for text in ["", "   ", "\n\t"]:
            with self.subTest(text=text), self.assertRaises(InvalidRequestError):
                validate_request(TranslationRequest(text, "ja"))

    def test_rejects_too_long_text(self) -> None:
        with self.assertRaises(InvalidRequestError):
            validate_request(TranslationRequest("あ" * 2001, "ja"))

    def test_rejects_same_whisper_language(self) -> None:
        # 同一個 Whisper 語言（含繁↔簡）不送翻譯模型，由 pipeline 只做字形轉換。
        for src, tgt in [
            ("zh", "zh-TW"),
            ("zh-CN", "zh-TW"),
            ("ja", "ja"),
            ("en", "en"),
        ]:
            with self.subTest(pair=(src, tgt)), self.assertRaises(InvalidRequestError):
                validate_request(TranslationRequest("x", src, tgt))

    def test_rejects_unsupported_language_code(self) -> None:
        for src, tgt in [("nn", "zh-TW"), ("ja", "zh"), ("ja", "sw"), (None, "en")]:
            with self.subTest(pair=(src, tgt)), self.assertRaises(InvalidRequestError):
                validate_request(TranslationRequest("x", src, tgt))  # type: ignore[arg-type]

    def test_respects_restricted_pairs(self) -> None:
        with self.assertRaises(InvalidRequestError):
            validate_request(
                TranslationRequest("hello", "en"), pairs=frozenset({("ja", "zh-TW")})
            )
        req = validate_request(
            TranslationRequest("はい", "ja", "zh-CN"),
            pairs=frozenset({("ja", "zh-TW"), ("ja", "zh-CN")}),
        )
        self.assertEqual(req.target_language, "zh-CN")

    def test_respects_restricted_languages(self) -> None:
        languages = frozenset({"ja", "zh-TW", "zh-CN"})
        for src, tgt in [("en", "zh-TW"), ("ja", "ko")]:
            with self.subTest(pair=(src, tgt)), self.assertRaises(InvalidRequestError):
                validate_request(TranslationRequest("x", src, tgt), languages=languages)
        # 原文 "zh" 只要清單中有任一個中文就算支援
        req = validate_request(
            TranslationRequest("你好", "zh", "ja"), languages=languages
        )
        self.assertEqual(req.source_language, "zh")

    def test_all_languages_is_the_catalog(self) -> None:
        self.assertEqual(ALL_LANGUAGE_CODES, frozenset(language_codes()))

    def test_rejects_wrong_types(self) -> None:
        bad = [
            TranslationRequest(123, "ja"),  # type: ignore[arg-type]
            TranslationRequest("x", "ja", context="前の文"),  # type: ignore[arg-type]
            TranslationRequest("x", "ja", context=(1,)),  # type: ignore[arg-type]
        ]
        for req in bad:
            with self.subTest(req=req), self.assertRaises(InvalidRequestError):
                validate_request(req)
        with self.assertRaises(InvalidRequestError):
            validate_request("おはよう")  # type: ignore[arg-type]

    def test_keeps_only_last_context_lines(self) -> None:
        req = validate_request(
            TranslationRequest("x", "ja", context=("a", "b", "c", "d")),
            max_context_lines=2,
        )
        self.assertEqual(req.context, ("c", "d"))
        req = validate_request(
            TranslationRequest("x", "ja", context=("a",)), max_context_lines=0
        )
        self.assertEqual(req.context, ())


class BuildMessagesTest(unittest.TestCase):
    def test_hunyuan_without_context_uses_official_template(self) -> None:
        msgs = build_messages("hunyuan_mt", TranslationRequest("おはよう", "ja"))
        self.assertEqual(
            msgs,
            [
                {
                    "role": "user",
                    "content": "将以下文本翻译为繁体中文，注意只需要输出翻译后的结果，"
                    "不要额外解释：\n\nおはよう",
                }
            ],
        )

    def test_hunyuan_with_context_uses_contextual_template(self) -> None:
        msgs = build_messages(
            "hunyuan_mt", TranslationRequest("行こう", "zh", "ja", ("先輩", "早く"))
        )
        self.assertEqual(len(msgs), 1)
        self.assertEqual(
            msgs[0]["content"],
            "先輩\n早く\n参考上面的信息，把下面的文本翻译成日语，"
            "注意不需要翻译上文，也不要额外解释：\n行こう",
        )

    def test_hunyuan_english_target_name(self) -> None:
        msgs = build_messages("hunyuan_mt", TranslationRequest("你好", "zh", "en"))
        self.assertIn("翻译为英语", msgs[0]["content"])

    def test_sakura_uses_official_system_prompt_and_ignores_context(self) -> None:
        msgs = build_messages(
            "sakura",
            TranslationRequest("この辺なら大丈夫かな？", "ja", context=("前",)),
        )
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        self.assertTrue(msgs[0]["content"].startswith("你是一个视觉小说翻译模型"))
        self.assertEqual(
            msgs[1]["content"],
            "参考以下术语表（可为空，格式为src->dst #备注）：\n\n"
            "根据以上术语表的对应关系和备注，结合历史剧情和上下文，"
            "将下面的文本从日文翻译成简体中文：\nこの辺なら大丈夫かな？",
        )
        self.assertNotIn("前", msgs[1]["content"].split("\n")[0])

    def test_sakura_rejects_non_japanese(self) -> None:
        with self.assertRaises(InvalidRequestError):
            build_messages("sakura", TranslationRequest("hi", "en"))

    def test_generic_zh_tw_instructions_are_in_traditional_chinese(self) -> None:
        # 實測：英文指示下 qwen3.5:9b 有 11/18 次直接輸出日文；改用譯文語言寫指示後 0/18。
        msgs = build_messages("generic", TranslationRequest("Hello there", "en"))
        self.assertEqual([m["role"] for m in msgs], ["system", "user"])
        system = msgs[0]["content"]
        for phrase in ["英文", "繁體中文", "台灣", "簡體"]:
            self.assertIn(phrase, system)
        self.assertEqual(msgs[1]["content"], "請翻譯成繁體中文：\nHello there")

    def test_generic_includes_context_marked_do_not_translate(self) -> None:
        msgs = build_messages(
            "generic", TranslationRequest("行くよ", "ja", context=("準備できた？",))
        )
        user = msgs[1]["content"]
        self.assertIn("不要翻譯", user)
        self.assertLess(user.index("準備できた？"), user.index("行くよ"))
        self.assertTrue(user.endswith("請翻譯成繁體中文：\n行くよ"))

    def test_generic_japanese_target_instructions_in_japanese(self) -> None:
        msgs = build_messages(
            "generic", TranslationRequest("你好", "zh", "ja", ("嗨",))
        )
        self.assertIn("日本語", msgs[0]["content"])
        self.assertNotIn("台灣", msgs[0]["content"])
        self.assertIn("翻訳しない", msgs[1]["content"])
        self.assertTrue(msgs[1]["content"].endswith("日本語に翻訳してください：\n你好"))

    def test_generic_english_target_instructions_in_english(self) -> None:
        msgs = build_messages(
            "generic", TranslationRequest("你好", "zh", "en", ("嗨",))
        )
        self.assertIn("English", msgs[0]["content"])
        self.assertIn("do not translate", msgs[1]["content"])
        self.assertTrue(msgs[1]["content"].endswith("Translate into English:\n你好"))

    def test_generic_zh_cn_instructions_are_in_simplified_chinese(self) -> None:
        msgs = build_messages(
            "generic", TranslationRequest("Hello", "en", "zh-CN", ("Hi",))
        )
        system = msgs[0]["content"]
        for phrase in ["英语", "简体中文", "不要用繁体字"]:
            self.assertIn(phrase, system)
        self.assertNotIn("台灣", system)
        self.assertIn("不要翻译", msgs[1]["content"])
        self.assertTrue(msgs[1]["content"].endswith("请翻译成简体中文：\nHello"))

    def test_generic_other_targets_use_english_with_both_names(self) -> None:
        msgs = build_messages("generic", TranslationRequest("おはよう", "ja", "ko"))
        system, user = msgs[0]["content"], msgs[1]["content"]
        self.assertIn("Japanese", system)
        self.assertIn("Korean (한국어)", system)
        self.assertIn("only", system)
        self.assertTrue(user.endswith("Translate into Korean (한국어):\nおはよう"))
        msgs = build_messages("generic", TranslationRequest("你好", "zh", "ar"))
        self.assertIn("Chinese", msgs[0]["content"])
        self.assertIn("Arabic (العربية)", msgs[0]["content"])

    def test_generic_source_names_in_target_language(self) -> None:
        cases = [
            (TranslationRequest("안녕", "ko", "zh-TW"), "韓文"),
            (TranslationRequest("Hola", "es", "zh-CN"), "西班牙语"),
            (TranslationRequest("Привет", "ru", "ja"), "ロシア語"),
            (TranslationRequest("Hallo", "nl", "en"), "Dutch"),
        ]
        for request, name in cases:
            with self.subTest(request=request):
                self.assertIn(name, build_messages("generic", request)[0]["content"])

    def test_every_catalog_pair_builds_prompts_for_supporting_presets(self) -> None:
        for preset in PRESETS.values():
            for source in WHISPER_CODES:
                for target in language_codes():
                    if not supports(preset, source, target):
                        continue
                    with self.subTest(model=preset.model, pair=(source, target)):
                        request = validate_request(
                            TranslationRequest("x", source, target, ("y",))
                        )
                        msgs = build_messages(preset.prompt_style, request)
                        self.assertTrue(msgs[-1]["content"].endswith("x"))

    def test_hunyuan_non_chinese_pair_uses_english_template(self) -> None:
        msgs = build_messages("hunyuan_mt", TranslationRequest("おはよう", "ja", "ko"))
        self.assertEqual(
            msgs,
            [
                {
                    "role": "user",
                    "content": "Translate the following segment into Korean, "
                    "without additional explanation.\n\nおはよう",
                }
            ],
        )

    def test_hunyuan_chinese_pairs_use_chinese_template_and_names(self) -> None:
        cases = [
            (TranslationRequest("hola", "es", "zh-CN"), "翻译为中文"),
            (TranslationRequest("hola", "es", "zh-TW"), "翻译为繁体中文"),
            (TranslationRequest("你好", "zh", "uk"), "翻译为乌克兰语"),
            (TranslationRequest("你好", "zh", "id"), "翻译为印尼语"),
        ]
        for request, phrase in cases:
            with self.subTest(request=request):
                content = build_messages("hunyuan_mt", request)[0]["content"]
                self.assertTrue(content.startswith("将以下文本翻译为"))
                self.assertIn(phrase, content)

    def test_sakura_also_translates_to_simplified(self) -> None:
        msgs = build_messages("sakura", TranslationRequest("はい", "ja", "zh-CN"))
        self.assertTrue(msgs[1]["content"].endswith("翻译成简体中文：\nはい"))
        with self.assertRaises(InvalidRequestError):
            build_messages("sakura", TranslationRequest("はい", "ja", "ko"))

    def test_sakura_prompt_and_preset_share_one_pair_list(self) -> None:
        # 提示詞與預設組必須用同一份方向清單，改一邊不會讓另一邊悄悄不一致
        sakura = preset_for_model("hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS")
        self.assertIs(sakura.pairs, SAKURA_PAIRS)
        for source, target in SAKURA_PAIRS:
            with self.subTest(pair=(source, target)):
                build_messages("sakura", TranslationRequest("はい", source, target))

    def test_unknown_style_rejected(self) -> None:
        with self.assertRaises(ValueError):
            build_messages("nope", TranslationRequest("x", "ja"))  # type: ignore[arg-type]


class StripThinkingTest(unittest.TestCase):
    def test_removes_closed_block(self) -> None:
        self.assertEqual(strip_thinking("<think>\nhmm\n</think>\n\n早安"), "\n\n早安")

    def test_removes_thinking_tag_variant_case_insensitive(self) -> None:
        self.assertEqual(strip_thinking("<Thinking>x</Thinking>早安"), "早安")

    def test_unclosed_block_drops_rest(self) -> None:
        self.assertEqual(strip_thinking("<think>still reasoning"), "")
        self.assertEqual(strip_thinking("早安<think>oops"), "早安")

    def test_orphan_closing_tag_drops_prefix(self) -> None:
        self.assertEqual(strip_thinking("reasoning...</think>早安"), "早安")

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(strip_thinking("早安"), "早安")

    def test_partial_opening_tag_at_end_is_hidden(self) -> None:
        # 串流時 "<thi" 可能是 <think> 的開頭，先不要顯示。
        self.assertEqual(strip_thinking("<thi"), "")


class CleanSubtitleTest(unittest.TestCase):
    def test_strips_whitespace_and_wrapping_quotes(self) -> None:
        cases = {
            "  早安  ": "早安",
            "「早安」": "早安",
            '"Good morning"': "Good morning",
            "“早安”": "早安",
            "『早安』": "早安",
        }
        for raw, want in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(clean_subtitle(raw), want)

    def test_keeps_inner_quotes(self) -> None:
        self.assertEqual(clean_subtitle("「走吧」他說「快點」"), "「走吧」他說「快點」")

    def test_strips_leading_labels(self) -> None:
        for raw in [
            "譯文：早安",
            "翻譯: 早安",
            "Translation: 早安",
            "译文：早安",
            "翻訳：早安",
            "번역: 早安",
            "Traducción: 早安",
            "Перевод: 早安",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(clean_subtitle(raw), "早安")

    def test_strips_leaked_special_tokens(self) -> None:
        # 實測 HY-MT 1.8B 偶爾在句尾吐出特殊 token 的碎片。
        for raw in [
            "早安<|",
            "早安<|eos|>",
            "早安<|extra_0|>",
            "<|startoftext|>早安",
            "早安<|eo",
        ]:
            with self.subTest(raw=raw):
                self.assertEqual(clean_subtitle(raw), "早安")

    def test_keeps_ordinary_angle_brackets(self) -> None:
        self.assertEqual(clean_subtitle("a < b"), "a < b")


class TraditionalConversionTest(unittest.TestCase):
    def test_converts_simplified_and_mainland_terms(self) -> None:
        self.assertEqual(to_taiwan_traditional("这个视频的软件"), "這個影片的軟體")

    def test_traditional_text_unchanged(self) -> None:
        # 實測：s2twp 會把正確的「傢伙」改成「傢夥」、「台」改成「臺」，所以沒有簡體字時
        # 完全不轉換。
        for text in [
            "這個影片很好看，我們走吧。",
            "那個傢伙才不是叛徒，這拉麵好好吃。",
            "台灣的皇后",
        ]:
            with self.subTest(text=text):
                self.assertEqual(to_taiwan_traditional(text), text)

    def test_mixed_text_is_converted(self) -> None:
        self.assertEqual(
            to_taiwan_traditional("老实說，你看到了吗？"), "老實說，你看到了嗎？"
        )

    def test_detects_only_simplified_only_characters(self) -> None:
        self.assertEqual(
            simplified_characters("这个视频才好吃"), ["这", "个", "视", "频"]
        )
        self.assertEqual(simplified_characters("那傢伙才不是，好吃，台灣"), [])
        self.assertEqual(simplified_characters("Hello, こんにちは"), [])
        self.assertEqual(simplified_characters("那么，由于"), ["么", "于"])

    def test_finalize_converts_zh_cn_to_simplified(self) -> None:
        raw = "這個軟體"
        self.assertEqual(finalize_translation(raw, "zh-CN", convert=True), "这个软体")
        self.assertEqual(finalize_translation(raw, "zh-CN", convert=False), raw)

    def test_finalize_converts_only_for_zh_tw(self) -> None:
        raw = "<think>x</think>「这个」"
        self.assertEqual(finalize_translation(raw, "zh-TW", convert=True), "這個")
        self.assertEqual(finalize_translation(raw, "zh-TW", convert=False), "这个")
        self.assertEqual(finalize_translation("「这个」", "ja", convert=True), "这个")


class PresetsTest(unittest.TestCase):
    def test_installed_models_have_presets(self) -> None:
        for name in [
            "hy-mt1.5-7b:latest",
            "hy-mt1.5-1.8b:latest",
            "qwen3.5:9b",
            "qwen3.5:4b",
            "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
        ]:
            with self.subTest(name=name):
                self.assertEqual(preset_for_model(name).model, name)

    def test_lookup_tolerates_missing_latest_tag(self) -> None:
        self.assertEqual(preset_for_model("hy-mt1.5-7b").model, "hy-mt1.5-7b:latest")

    def test_unknown_model_gets_generic_preset(self) -> None:
        preset = preset_for_model("llama3.2:3b")
        self.assertEqual(preset.model, "llama3.2:3b")
        self.assertEqual(preset.prompt_style, "generic")
        self.assertIsNone(preset.reasoning_effort)
        self.assertEqual(preset.languages, ALL_LANGUAGE_CODES)
        self.assertIsNone(preset.pairs)

    def test_qwen_disables_thinking(self) -> None:
        self.assertEqual(preset_for_model("qwen3.5:4b").reasoning_effort, "none")

    def test_context_support_follows_benchmark(self) -> None:
        # 實測：HY-MT 1.8B 用官方前文範本時 11/12 次把前文或指令一起輸出；7B 在實測
        # 句組中也重現把前一句一起翻出來，所以兩者都不帶前文。
        self.assertFalse(preset_for_model("hy-mt1.5-1.8b:latest").supports_context)
        self.assertFalse(preset_for_model("hy-mt1.5-7b:latest").supports_context)
        self.assertTrue(preset_for_model("qwen3.5:4b").supports_context)
        self.assertFalse(
            preset_for_model(
                "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
            ).supports_context
        )
        self.assertTrue(preset_for_model("unknown:1b").supports_context)

    def test_license_flags(self) -> None:
        sakura = preset_for_model("hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS")
        self.assertFalse(sakura.commercial_use)
        self.assertEqual(sakura.pairs, frozenset({("ja", "zh-TW"), ("ja", "zh-CN")}))
        hy = preset_for_model("hy-mt1.5-7b:latest")
        self.assertEqual(hy.excluded_regions, ("EU", "UK", "KR"))
        self.assertTrue(preset_for_model("qwen3.5:9b").commercial_use)
        for preset in PRESETS.values():
            self.assertTrue(
                preset.license and preset.license_url.startswith("https://")
            )


class SupportsTest(unittest.TestCase):
    def test_general_models_support_every_catalog_pair(self) -> None:
        for model in ["qwen3.5:4b", "hy-mt1.5-7b:latest", "unknown:1b"]:
            preset = preset_for_model(model)
            with self.subTest(model=model):
                self.assertTrue(supports(preset, "ko", "es"))
                self.assertTrue(supports(preset, "zh", "uk"))
                self.assertTrue(supports(preset, "ar", "zh-CN"))

    def test_sakura_only_japanese_to_chinese(self) -> None:
        sakura = preset_for_model("hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS")
        self.assertTrue(supports(sakura, "ja", "zh-TW"))
        self.assertTrue(supports(sakura, "ja", "zh-CN"))
        self.assertFalse(supports(sakura, "en", "zh-TW"))
        self.assertFalse(supports(sakura, "zh", "ja"))

    def test_same_whisper_language_or_unknown_codes_are_not_translatable(self) -> None:
        preset = preset_for_model("qwen3.5:4b")
        for source, target in [
            ("zh-CN", "zh-TW"),
            ("ja", "ja"),
            ("nn", "en"),
            ("ja", "zh"),
            ("ja", None),
        ]:
            with self.subTest(pair=(source, target)):
                self.assertFalse(supports(preset, source, target))  # type: ignore[arg-type]


class RecommendTest(unittest.TestCase):
    def test_every_tier_returns_known_models(self) -> None:
        for vram in [None, 0.0, 2.0, 4.0, 8.0, 24.0]:
            with self.subTest(vram=vram):
                choice = recommend_translation_models(vram)
                self.assertIsInstance(choice, TranslationModelChoice)
                self.assertIn(choice.fast, PRESETS)
                self.assertIn(choice.final, PRESETS)

    def test_never_recommends_non_commercial_model(self) -> None:
        for vram in [None, 2.0, 4.0, 8.0, 24.0]:
            choice = recommend_translation_models(vram)
            for model in (choice.fast, choice.final):
                self.assertTrue(PRESETS[model].commercial_use)

    def test_prefers_installed_models(self) -> None:
        choice = recommend_translation_models(24.0, installed=["qwen3.5:4b"])
        self.assertEqual(choice, TranslationModelChoice("qwen3.5:4b", "qwen3.5:4b"))

    def test_installed_names_without_tag_match(self) -> None:
        choice = recommend_translation_models(2.0, installed=["hy-mt1.5-1.8b"])
        self.assertEqual(choice.fast, "hy-mt1.5-1.8b:latest")

    def test_restricted_models_can_be_excluded(self) -> None:
        for vram in [None, 2.0, 4.0, 8.0, 24.0]:
            choice = recommend_translation_models(vram, allow_restricted=False)
            for model in (choice.fast, choice.final):
                self.assertEqual(PRESETS[model].excluded_regions, ())

    def test_bigger_budget_never_picks_smaller_final_model(self) -> None:
        small = recommend_translation_models(2.0)
        big = recommend_translation_models(24.0)
        self.assertGreaterEqual(
            PRESETS[big.final].approx_vram_gb, PRESETS[small.final].approx_vram_gb
        )

    def test_tier_choices_follow_benchmark(self) -> None:
        # GPU 上延遲都遠低於字幕需求，所以同一個模型同時負責即時與完整路徑，省 VRAM。
        self.assertEqual(
            recommend_translation_models(8.0),
            TranslationModelChoice("qwen3.5:9b", "qwen3.5:9b"),
        )
        self.assertEqual(
            recommend_translation_models(4.0),
            TranslationModelChoice("qwen3.5:4b", "qwen3.5:4b"),
        )
        self.assertEqual(
            recommend_translation_models(None),
            TranslationModelChoice("hy-mt1.5-1.8b:latest", "hy-mt1.5-1.8b:latest"),
        )
        self.assertEqual(
            recommend_translation_models(None, allow_restricted=False),
            TranslationModelChoice("qwen3.5:4b", "qwen3.5:4b"),
        )

    def test_rejects_negative_or_non_finite_budget(self) -> None:
        for bad in [-1.0, float("nan"), float("inf")]:
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                recommend_translation_models(bad)


if __name__ == "__main__":
    unittest.main()
