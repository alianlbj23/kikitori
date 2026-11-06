"""config 模組的測試：預設值、驗證、遷移、深度合併、原子寫入、平台路徑、SettingsStore。

全部在暫存目錄進行，不碰使用者真正的設定檔。
"""

import json
import os
import tempfile
import unittest
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any
from unittest import mock

from kikitori.config import (
    CONFIG_DIR_ENV,
    SCHEMA_VERSION,
    Settings,
    SettingsError,
    SettingsStore,
    config_dir_for,
    load_settings,
    migrate,
    save_settings,
    write_json_atomic,
)
from kikitori.config import store as store_module
from kikitori.translate.glossary import GlossaryEntry


def _with(path: str, value: Any) -> dict[str, Any]:
    """以預設設定為底，把 `a.b` 路徑的值換掉。"""
    data = Settings().to_dict()
    keys = path.split(".")
    target = data
    for key in keys[:-1]:
        target = target[key]
    target[keys[-1]] = value
    return data


class DefaultsTest(unittest.TestCase):
    def test_defaults_match_spec(self) -> None:
        s = Settings()
        self.assertEqual(s.schema_version, SCHEMA_VERSION)
        self.assertEqual(s.mode, "media")
        self.assertEqual(s.source_language, "ja")
        self.assertEqual(s.target_language, "zh-TW")
        self.assertEqual(s.partner_language, "ja")
        self.assertTrue(s.translation.enabled)
        self.assertEqual((s.media.pause_secs, s.media.max_secs), (0.6, 10.0))
        self.assertEqual((s.interpret.pause_secs, s.interpret.max_secs), (0.5, 8.0))
        self.assertIsNone(s.media.device_id)
        self.assertEqual(s.translation.base_url, "http://localhost:11434/v1")
        self.assertEqual(s.translation.api_key_env, "KIKITORI_TRANSLATE_API_KEY")
        self.assertEqual(s.pipeline.partial_interval_ms, 700)
        # 沒有 GPU 也跑得動的組合（asr.md 的 CPU 等級）
        self.assertEqual((s.asr.fast_model, s.asr.final_model), ("base", "small"))
        self.assertEqual(s.asr.device, "auto")
        self.assertEqual(
            (s.asr.fast_compute_type, s.asr.final_compute_type), (None, None)
        )
        self.assertEqual(
            (s.asr.fast_cpu_threads, s.asr.final_cpu_threads), (None, None)
        )
        self.assertEqual(s.asr.cuda_library_dirs, ())
        # HY-MT 的授權排除歐盟、英國、南韓；不知道使用者所在地時預設不推薦
        self.assertFalse(s.translation.allow_restricted)
        self.assertEqual(s.pipeline.no_speech_threshold, 0.6)
        self.assertEqual(s.subtitle, {})
        self.assertEqual(s.ui, {})

    def test_defaults_are_valid(self) -> None:
        self.assertEqual(Settings.from_dict(Settings().to_dict()), Settings())

    def test_mode_settings(self) -> None:
        s = Settings()
        self.assertIs(s.mode_settings("media"), s.media)
        self.assertIs(s.mode_settings("interpret"), s.interpret)
        with self.assertRaises(ValueError):
            s.mode_settings("karaoke")  # type: ignore[arg-type]

    def test_missing_fields_use_defaults(self) -> None:
        s = Settings.from_dict({"schema_version": 1, "media": {"pause_secs": 0.8}})
        self.assertEqual(s.media.pause_secs, 0.8)
        self.assertEqual(s.media.max_secs, 10.0)
        self.assertEqual(s.asr, Settings().asr)

    def test_to_dict_is_a_deep_copy(self) -> None:
        s = Settings.from_dict(_with("subtitle", {"font": {"size": 20}}))
        d = s.to_dict()
        d["subtitle"]["font"]["size"] = 99
        self.assertEqual(s.subtitle["font"]["size"], 20)


class ValidationTest(unittest.TestCase):
    def assertInvalid(self, data: object, path: str) -> None:
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict(data)
        self.assertTrue(
            str(ctx.exception).startswith(path),
            f"{ctx.exception!s} does not start with {path!r}",
        )

    def test_not_an_object(self) -> None:
        self.assertInvalid([1, 2], "settings")

    def test_unknown_top_level_key(self) -> None:
        self.assertInvalid(_with("colour", "red"), "colour")

    def test_unknown_nested_key(self) -> None:
        self.assertInvalid(_with("media.pause", 0.6), "media.pause")

    def test_section_not_object(self) -> None:
        self.assertInvalid(_with("asr", "small"), "asr")

    def test_mode_choices(self) -> None:
        self.assertInvalid(_with("mode", "karaoke"), "mode")
        self.assertInvalid(_with("partner_language", "sv"), "partner_language")
        self.assertInvalid(_with("target_language", "zh"), "target_language")
        self.assertInvalid(_with("asr.device", "tpu"), "asr.device")

    def test_number_ranges_and_types(self) -> None:
        self.assertInvalid(_with("media.pause_secs", 0.0), "media.pause_secs")
        self.assertInvalid(_with("media.pause_secs", "0.6"), "media.pause_secs")
        self.assertInvalid(_with("media.pause_secs", True), "media.pause_secs")
        self.assertInvalid(_with("media.pause_secs", float("nan")), "media.pause_secs")
        self.assertInvalid(_with("interpret.max_secs", 31), "interpret.max_secs")
        self.assertInvalid(
            _with("pipeline.no_speech_threshold", 1.5), "pipeline.no_speech_threshold"
        )

    def test_max_must_exceed_pause(self) -> None:
        data = _with("media.pause_secs", 3.0)
        data["media"]["max_secs"] = 2.0
        self.assertInvalid(data, "media.max_secs")

    def test_integer_fields_reject_floats_and_bools(self) -> None:
        self.assertInvalid(
            _with("pipeline.partial_interval_ms", 700.0), "pipeline.partial_interval_ms"
        )
        self.assertInvalid(
            _with("pipeline.partial_interval_ms", 100), "pipeline.partial_interval_ms"
        )
        self.assertInvalid(
            _with("pipeline.context_lines", True), "pipeline.context_lines"
        )

    def test_boolean_field(self) -> None:
        self.assertInvalid(
            _with("pipeline.asr_prompt_context", 1), "pipeline.asr_prompt_context"
        )

    def test_device_id(self) -> None:
        self.assertInvalid(_with("media.device_id", ""), "media.device_id")
        self.assertInvalid(_with("media.device_id", 3), "media.device_id")
        self.assertInvalid(_with("media.device_id", "x" * 513), "media.device_id")
        ok = Settings.from_dict(_with("media.device_id", "wasapi:loopback:喇叭"))
        self.assertEqual(ok.media.device_id, "wasapi:loopback:喇叭")

    def test_asr_model_ids(self) -> None:
        self.assertInvalid(_with("asr.fast_model", ""), "asr.fast_model")
        self.assertInvalid(_with("asr.final_model", "../evil"), "asr.final_model")
        self.assertInvalid(_with("asr.final_model", 3), "asr.final_model")

    def test_asr_compute_type_and_cuda_dirs(self) -> None:
        self.assertInvalid(
            _with("asr.fast_compute_type", "int4"), "asr.fast_compute_type"
        )
        self.assertInvalid(_with("asr.final_compute_type", 8), "asr.final_compute_type")
        # v1 的共用欄位在 v2 已拆成兩條路徑，直接送會被當成未知欄位
        self.assertInvalid(_with("asr.compute_type", "int8"), "asr.compute_type")
        self.assertInvalid(
            _with("asr.cuda_library_dirs", "C:/cuda"), "asr.cuda_library_dirs"
        )
        self.assertInvalid(
            _with("asr.cuda_library_dirs", ["relative/dir"]), "asr.cuda_library_dirs[0]"
        )
        self.assertInvalid(
            _with("asr.cuda_library_dirs", [3]), "asr.cuda_library_dirs[0]"
        )
        absolute = str(Path(tempfile.gettempdir()).resolve())
        s = Settings.from_dict(
            _with(
                "asr",
                {
                    "fast_compute_type": "int8_float16",
                    "final_compute_type": "float16",
                    "cuda_library_dirs": [absolute],
                },
            )
        )
        self.assertEqual(
            (s.asr.fast_compute_type, s.asr.final_compute_type),
            ("int8_float16", "float16"),
        )
        self.assertEqual(s.asr.cuda_library_dirs, (absolute,))
        self.assertEqual(s.to_dict()["asr"]["cuda_library_dirs"], [absolute])

    def test_asr_cpu_threads(self) -> None:
        for bad in (0, -1, 257, 2.0, True, "4"):
            with self.subTest(value=bad):
                self.assertInvalid(
                    _with("asr.fast_cpu_threads", bad), "asr.fast_cpu_threads"
                )
        self.assertInvalid(_with("asr.final_cpu_threads", 0), "asr.final_cpu_threads")
        s = Settings.from_dict(
            _with("asr", {"fast_cpu_threads": 4, "final_cpu_threads": None})
        )
        self.assertEqual((s.asr.fast_cpu_threads, s.asr.final_cpu_threads), (4, None))
        self.assertEqual(s.to_dict()["asr"]["fast_cpu_threads"], 4)
        self.assertIsNone(s.to_dict()["asr"]["final_cpu_threads"])

    def test_translation_allow_restricted(self) -> None:
        self.assertInvalid(
            _with("translation.allow_restricted", "yes"), "translation.allow_restricted"
        )
        s = Settings.from_dict(_with("translation.allow_restricted", True))
        self.assertTrue(s.translation.allow_restricted)
        self.assertTrue(s.to_dict()["translation"]["allow_restricted"])

    def test_translation_keep_warm_secs(self) -> None:
        # 低於 Ollama 預設 keep_alive（5 分鐘）才有用；0 關閉。
        self.assertEqual(Settings().translation.keep_warm_secs, 240)
        self.assertEqual(Settings().to_dict()["translation"]["keep_warm_secs"], 240)
        for ok in (0, 10, 3600):
            s = Settings.from_dict(_with("translation.keep_warm_secs", ok))
            self.assertEqual(s.translation.keep_warm_secs, ok)
        for bad in (True, "240", 240.5, -1, 1, 9, 3601, None):
            self.assertInvalid(
                _with("translation.keep_warm_secs", bad), "translation.keep_warm_secs"
            )

    def test_keep_warm_secs_missing_uses_default_without_migration(self) -> None:
        # 新欄位有預設值：沒有這個欄位的 schema 2 設定檔照樣讀得進來，不必升 schema。
        data = Settings().to_dict()
        del data["translation"]["keep_warm_secs"]
        s = Settings.from_dict(data)
        self.assertEqual(s.schema_version, SCHEMA_VERSION)
        self.assertEqual(s.translation.keep_warm_secs, 240)
        merged = Settings().merged({"translation": {"keep_warm_secs": 30}})
        self.assertEqual(merged.translation.keep_warm_secs, 30)

    def test_glossary_defaults_and_round_trip(self) -> None:
        s = Settings()
        self.assertTrue(s.translation.glossary.builtin)
        self.assertEqual(s.translation.glossary.entries, ())
        self.assertEqual(
            s.to_dict()["translation"]["glossary"], {"builtin": True, "entries": []}
        )
        entries = [
            {"source": "ぺこら", "target": "佩克拉", "target_language": "zh-TW"},
            {"source": "Hololive", "target": "hololive", "target_language": None},
        ]
        data = _with("translation.glossary", {"builtin": False, "entries": entries})
        s = Settings.from_dict(data)
        self.assertFalse(s.translation.glossary.builtin)
        self.assertEqual(
            s.translation.glossary.entries,
            (
                GlossaryEntry("ぺこら", "佩克拉", "zh-TW"),
                GlossaryEntry("Hololive", "hololive", None),
            ),
        )
        self.assertEqual(
            s.to_dict()["translation"]["glossary"],
            {"builtin": False, "entries": entries},
        )
        self.assertEqual(Settings.from_dict(s.to_dict()), s)

    def test_glossary_missing_uses_default_without_migration(self) -> None:
        # 新欄位有預設值：沒有 glossary 的 schema 3 設定檔照樣讀得進來，不必升 schema。
        data = Settings().to_dict()
        del data["translation"]["glossary"]
        self.assertEqual(Settings.from_dict(data), Settings())
        partial = _with("translation.glossary", {"entries": []})
        self.assertTrue(Settings.from_dict(partial).translation.glossary.builtin)
        merged = Settings().merged({"translation": {"glossary": {"builtin": False}}})
        self.assertFalse(merged.translation.glossary.builtin)

    def test_furigana_defaults_round_trip_and_missing(self) -> None:
        # 振假名：預設關閉；新欄位有預設值，沒有 furigana 的 schema 3 檔案照樣讀得進來（不升 schema）
        s = Settings()
        self.assertFalse(s.furigana.enabled)
        self.assertEqual(s.to_dict()["furigana"], {"enabled": False})
        data = s.to_dict()
        del data["furigana"]
        self.assertEqual(Settings.from_dict(data), Settings())
        on = Settings.from_dict(_with("furigana", {"enabled": True}))
        self.assertTrue(on.furigana.enabled)
        self.assertEqual(Settings.from_dict(on.to_dict()), on)
        self.assertTrue(
            Settings().merged({"furigana": {"enabled": True}}).furigana.enabled
        )

    def test_furigana_validation(self) -> None:
        for value, path in (
            ({"enabled": "yes"}, "furigana.enabled"),
            ({"enabled": 1}, "furigana.enabled"),
            ({"edition": "full"}, "furigana.edition"),
            ([], "furigana"),
        ):
            with self.subTest(value=value):
                with self.assertRaises(SettingsError) as ctx:
                    Settings.from_dict(_with("furigana", value))
                self.assertIn(path, str(ctx.exception))

    def test_glossary_entry_language_is_normalized(self) -> None:
        data = _with(
            "translation.glossary",
            {"entries": [{"source": "a", "target": "b", "target_language": "zh-tw"}]},
        )
        entry = Settings.from_dict(data).translation.glossary.entries[0]
        self.assertEqual(entry.target_language, "zh-TW")
        # target_language 可以省略（= 所有譯文語言）
        data = _with(
            "translation.glossary", {"entries": [{"source": "a", "target": "b"}]}
        )
        entry = Settings.from_dict(data).translation.glossary.entries[0]
        self.assertIsNone(entry.target_language)

    def test_glossary_validation(self) -> None:
        good = {"source": "a", "target": "b", "target_language": None}
        for glossary, path in [
            ("on", "translation.glossary"),
            ({"builtin": "yes"}, "translation.glossary.builtin"),
            ({"extra": 1}, "translation.glossary.extra"),
            ({"entries": {}}, "translation.glossary.entries"),
            ({"entries": [good] * 501}, "translation.glossary.entries"),
            ({"entries": ["a->b"]}, "translation.glossary.entries[0]"),
            (
                {"entries": [{**good, "note": "x"}]},
                "translation.glossary.entries[0].note",
            ),
            ({"entries": [{"target": "b"}]}, "translation.glossary.entries[0].source"),
            (
                {"entries": [good, {**good, "source": ""}]},
                "translation.glossary.entries[1].source",
            ),
            (
                {"entries": [{**good, "source": "a\nb"}]},
                "translation.glossary.entries[0].source",
            ),
            (
                {"entries": [{**good, "target": "x" * 201}]},
                "translation.glossary.entries[0].target",
            ),
            (
                {"entries": [{**good, "target": 5}]},
                "translation.glossary.entries[0].target",
            ),
            (
                {"entries": [{**good, "target_language": "xx"}]},
                "translation.glossary.entries[0].target_language",
            ),
            (
                {"entries": [{**good, "target_language": "zh"}]},
                "translation.glossary.entries[0].target_language",
            ),
            # 同一個原文寫法（不分大小寫與全半形）＋同一個譯文語言只能有一筆
            (
                {"entries": [good, {**good, "source": "Ａ", "target": "c"}]},
                "translation.glossary.entries[1].source",
            ),
        ]:
            with self.subTest(glossary=glossary):
                self.assertInvalid(_with("translation.glossary", glossary), path)
        # 同一個寫法、不同譯文語言可以並存
        ok = {
            "entries": [
                good,
                {**good, "target_language": "en"},
                {**good, "target_language": "ko"},
            ]
        }
        self.assertEqual(
            len(
                Settings.from_dict(
                    _with("translation.glossary", ok)
                ).translation.glossary.entries
            ),
            3,
        )
        self.assertEqual(
            len(
                Settings.from_dict(
                    _with(
                        "translation.glossary",
                        {
                            "entries": [good] * 1
                            + [{**good, "source": f"s{i}"} for i in range(499)]
                        },
                    )
                ).translation.glossary.entries
            ),
            500,
        )

    def test_translation_fields_use_translator_validation(self) -> None:
        self.assertInvalid(
            _with("translation.base_url", "ftp://example.com"), "translation.base_url"
        )
        self.assertInvalid(
            _with("translation.api_key_env", "MY-KEY"), "translation.api_key_env"
        )
        self.assertInvalid(
            _with("translation.fast_model", " "), "translation.fast_model"
        )
        self.assertInvalid(
            _with("translation.final_model", 5), "translation.final_model"
        )
        self.assertInvalid(
            _with("translation.final_model", "m" * 201), "translation.final_model"
        )

    def test_subtitle_passthrough(self) -> None:
        prefs = {"font_size": 28, "colors": ["#fff", None], "pos": {"x": 0.5}}
        s = Settings.from_dict(_with("subtitle", prefs))
        self.assertEqual(s.subtitle, prefs)

    def test_subtitle_validation(self) -> None:
        self.assertInvalid(_with("subtitle", []), "subtitle")
        self.assertInvalid(_with("subtitle", {"x": float("inf")}), "subtitle.x")
        deep: dict[str, Any] = {}
        node = deep
        for _ in range(6):
            node["a"] = {}
            node = node["a"]
        self.assertInvalid(_with("subtitle", deep), "subtitle")
        self.assertInvalid(_with("subtitle", {"blob": "x" * 20000}), "subtitle")

    def test_ui_passthrough(self) -> None:
        state = {"dismissed_recommendations": ["0123abcd", "89efcdab"], "x": {"y": 1}}
        s = Settings.from_dict(_with("ui", state))
        self.assertEqual(s.ui, state)
        self.assertEqual(s.to_dict()["ui"], state)

    def test_ui_validation(self) -> None:
        # 與 subtitle 相同的限制：物件、不得有 NaN、巢狀 ≤ 5 層、≤ 16 KiB
        self.assertInvalid(_with("ui", "dismissed"), "ui")
        self.assertInvalid(_with("ui", {"x": float("nan")}), "ui.x")
        deep: dict[str, Any] = {}
        node = deep
        for _ in range(6):
            node["a"] = {}
            node = node["a"]
        self.assertInvalid(_with("ui", deep), "ui")
        self.assertInvalid(_with("ui", {"blob": "x" * 20000}), "ui")

    def test_ui_missing_uses_default_without_migration(self) -> None:
        # schema 2 之後新增、不升版：舊的 schema 2 檔案沒有 ui 照樣讀得進來
        data = Settings().to_dict()
        del data["ui"]
        self.assertEqual(Settings.from_dict(data).ui, {})
        self.assertEqual(Settings.from_dict(data).schema_version, SCHEMA_VERSION)

    def test_any_catalog_language_for_source_target_and_partner(self) -> None:
        s = Settings.from_dict(
            {
                **_with("source_language", "ko"),
                "target_language": "es",
                "partner_language": "zh-CN",
            }
        )
        self.assertEqual(
            (s.source_language, s.target_language, s.partner_language),
            ("ko", "es", "zh-CN"),
        )
        self.assertEqual(s.to_dict()["source_language"], "ko")

    def test_source_language_auto(self) -> None:
        s = Settings.from_dict(_with("source_language", "auto"))
        self.assertEqual(s.source_language, "auto")
        # auto 只用於收聽語言；字幕語言與對方語言必須是具體語言
        self.assertInvalid(_with("target_language", "auto"), "target_language")
        self.assertInvalid(_with("partner_language", "auto"), "partner_language")

    def test_language_codes_are_validated_and_normalized(self) -> None:
        for path in ("source_language", "target_language", "partner_language"):
            for bad in ("", "xx", 3, None, "zh", "x" * 100):
                with self.subTest(path=path, value=bad):
                    self.assertInvalid(_with(path, bad), path)
        # 手改設定檔的常見寫法與舊別名正規化成清單語言碼
        s = Settings.from_dict(_with("target_language", "zh_hans"))
        self.assertEqual(s.target_language, "zh-CN")
        self.assertEqual(
            Settings.from_dict(_with("source_language", "EN")).source_language, "en"
        )

    def test_source_may_equal_target(self) -> None:
        # 只做字形轉換或原樣顯示（例如學語言的人聽中文看中文）
        s = Settings.from_dict(
            {**_with("source_language", "zh-CN"), "target_language": "zh-TW"}
        )
        self.assertEqual((s.source_language, s.target_language), ("zh-CN", "zh-TW"))

    def test_interpret_partner_whisper_language_must_differ_from_target(self) -> None:
        interpret = _with("mode", "interpret")
        self.assertInvalid(
            {**interpret, "target_language": "ja", "partner_language": "ja"},
            "partner_language",
        )
        self.assertInvalid(
            {**interpret, "target_language": "zh-CN", "partner_language": "zh-TW"},
            "partner_language",
        )
        with self.assertRaises(SettingsError):
            Settings().merged({"mode": "interpret", "partner_language": "zh-CN"})
        s = Settings().merged(
            {"mode": "interpret", "target_language": "ja", "partner_language": "en"}
        )
        self.assertEqual((s.target_language, s.partner_language), ("ja", "en"))

    def test_media_mode_accepts_partner_equal_to_target(self) -> None:
        # 影片翻譯不用對方語言；日語使用者把字幕語言設成 ja 時，預設的對方語言 ja
        # 不該讓設定無法儲存。
        s = Settings.from_dict(_with("target_language", "ja"))
        self.assertEqual(
            (s.mode, s.target_language, s.partner_language), ("media", "ja", "ja")
        )
        s = Settings().merged({"target_language": "zh-CN", "partner_language": "zh-TW"})
        self.assertEqual((s.target_language, s.partner_language), ("zh-CN", "zh-TW"))

    def test_switching_to_interpret_revalidates_the_language_pair(self) -> None:
        s = Settings().merged({"target_language": "ja"})
        with self.assertRaises(SettingsError) as ctx:
            s.merged({"mode": "interpret"})
        message = str(ctx.exception)
        self.assertTrue(message.startswith("partner_language"), message)
        self.assertIn("interpret", message)
        self.assertIn("'ja'", message)
        # 同時改對方語言就可以切換
        s = s.merged({"mode": "interpret", "partner_language": "en"})
        self.assertEqual((s.mode, s.partner_language), ("interpret", "en"))

    def test_translation_enabled(self) -> None:
        self.assertInvalid(_with("translation.enabled", 1), "translation.enabled")
        self.assertInvalid(_with("translation.enabled", None), "translation.enabled")
        s = Settings().merged({"translation": {"enabled": False}})
        self.assertFalse(s.translation.enabled)
        self.assertFalse(s.to_dict()["translation"]["enabled"])
        self.assertEqual(Settings.from_dict(s.to_dict()), s)

    def test_error_message_shows_received_value(self) -> None:
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict(_with("media.pause_secs", "abc"))
        self.assertIn("'abc'", str(ctx.exception))


class MigrationTest(unittest.TestCase):
    def test_schema_version_required_and_typed(self) -> None:
        for data, fragment in (
            ({}, "missing"),
            ({"schema_version": "1"}, "positive integer"),
            ({"schema_version": 0}, "positive integer"),
            ({"schema_version": True}, "positive integer"),
        ):
            with self.subTest(data=data), self.assertRaises(SettingsError) as ctx:
                migrate(data)
            self.assertIn(fragment, str(ctx.exception))

    def test_newer_version_is_rejected(self) -> None:
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict({"schema_version": SCHEMA_VERSION + 1})
        self.assertIn("newer", str(ctx.exception))

    def test_migrations_applied_in_order(self) -> None:
        def v1_to_v2(d: dict[str, Any]) -> dict[str, Any]:
            d = dict(d)
            d["steps"] = [*d.get("steps", []), 1]
            return d

        def v2_to_v3(d: dict[str, Any]) -> dict[str, Any]:
            d = dict(d)
            d["steps"] = [*d["steps"], 2]
            return d

        result = migrate(
            {"schema_version": 1}, migrations={1: v1_to_v2, 2: v2_to_v3}, current=3
        )
        self.assertEqual(result, {"schema_version": 3, "steps": [1, 2]})

    def test_v1_compute_type_is_split_per_path(self) -> None:
        v1 = {
            "schema_version": 1,
            "asr": {
                "fast_model": "tiny",
                "final_model": "medium",
                "device": "cuda",
                "compute_type": "int8_float16",
                "cuda_library_dirs": [],
            },
            "translation": {"fast_model": "qwen3.5:9b", "final_model": "qwen3.5:9b"},
        }
        s = Settings.from_dict(v1)
        self.assertEqual(s.schema_version, SCHEMA_VERSION)
        self.assertEqual((s.asr.fast_model, s.asr.final_model), ("tiny", "medium"))
        self.assertEqual(s.asr.device, "cuda")
        self.assertEqual(
            (s.asr.fast_compute_type, s.asr.final_compute_type),
            ("int8_float16", "int8_float16"),
        )
        self.assertEqual(
            (s.asr.fast_cpu_threads, s.asr.final_cpu_threads), (None, None)
        )
        self.assertFalse(s.translation.allow_restricted)
        self.assertEqual(s.translation.fast_model, "qwen3.5:9b")
        # 遷移不改動呼叫端的 dict
        self.assertEqual(v1["asr"]["compute_type"], "int8_float16")  # type: ignore[index]

    def test_v1_without_asr_or_with_null_compute_type(self) -> None:
        self.assertEqual(Settings.from_dict({"schema_version": 1}).asr, Settings().asr)
        s = Settings.from_dict({"schema_version": 1, "asr": {"compute_type": None}})
        self.assertEqual(s.asr, Settings().asr)

    def test_v1_invalid_values_still_fail_validation(self) -> None:
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict({"schema_version": 1, "asr": {"compute_type": "int4"}})
        self.assertTrue(str(ctx.exception).startswith("asr.fast_compute_type"))
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict({"schema_version": 1, "asr": "cuda"})
        self.assertTrue(str(ctx.exception).startswith("asr"))

    def test_v1_file_is_migrated_on_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text(
                json.dumps({"schema_version": 1, "asr": {"compute_type": "int8"}}),
                encoding="utf-8",
            )
            s = load_settings(path)
            assert s is not None
            self.assertEqual(s.asr.fast_compute_type, "int8")
            self.assertEqual(s.to_dict()["schema_version"], SCHEMA_VERSION)

    def test_v2_gets_source_language_and_translation_enabled(self) -> None:
        v2 = {
            "schema_version": 2,
            "mode": "interpret",
            "target_language": "zh-TW",
            "partner_language": "en",
            "translation": {"fast_model": "qwen3.5:9b", "keep_warm_secs": 0},
            "subtitle": {"font_size": 30},
            "ui": {"dismissed": ["x"]},
        }
        migrated = migrate(v2)
        self.assertEqual(migrated["schema_version"], 3)
        self.assertEqual(migrated["source_language"], "ja")
        self.assertIs(migrated["translation"]["enabled"], True)
        # 其他欄位原樣保留，呼叫端的 dict 不被改動
        self.assertEqual(migrated["translation"]["fast_model"], "qwen3.5:9b")
        self.assertEqual(migrated["partner_language"], "en")
        self.assertEqual(migrated["subtitle"], {"font_size": 30})
        self.assertNotIn("enabled", v2["translation"])  # type: ignore[operator]
        s = Settings.from_dict(v2)
        self.assertEqual((s.source_language, s.translation.enabled), ("ja", True))
        self.assertEqual(s.translation.keep_warm_secs, 0)

    def test_v2_without_translation_or_with_invalid_translation(self) -> None:
        self.assertEqual(
            migrate({"schema_version": 2}),
            {
                "schema_version": 3,
                "source_language": "ja",
                "translation": {"enabled": True},
            },
        )
        with self.assertRaises(SettingsError) as ctx:
            Settings.from_dict({"schema_version": 2, "translation": "ollama"})
        self.assertTrue(str(ctx.exception).startswith("translation"))

    def test_v2_keeps_existing_v3_fields(self) -> None:
        migrated = migrate(
            {
                "schema_version": 2,
                "source_language": "en",
                "translation": {"enabled": False},
            }
        )
        self.assertEqual(migrated["source_language"], "en")
        self.assertIs(migrated["translation"]["enabled"], False)

    def test_v1_migrates_all_the_way(self) -> None:
        s = Settings.from_dict({"schema_version": 1, "asr": {"compute_type": "int8"}})
        self.assertEqual(s.schema_version, 3)
        self.assertEqual(s.source_language, "ja")
        self.assertTrue(s.translation.enabled)

    def test_missing_migration_step(self) -> None:
        with self.assertRaises(SettingsError):
            migrate({"schema_version": 1}, migrations={}, current=2)


class MergeTest(unittest.TestCase):
    def test_deep_merge_keeps_siblings(self) -> None:
        s = Settings().merged({"media": {"pause_secs": 0.9}, "partner_language": "en"})
        self.assertEqual(s.media.pause_secs, 0.9)
        self.assertEqual(s.media.max_secs, 10.0)
        self.assertEqual(s.partner_language, "en")

    def test_merge_does_not_mutate_original(self) -> None:
        base = Settings()
        base.merged({"mode": "interpret"})
        self.assertEqual(base.mode, "media")

    def test_merge_validates(self) -> None:
        with self.assertRaises(SettingsError):
            Settings().merged({"media": {"pause_secs": -1}})
        with self.assertRaises(SettingsError):
            Settings().merged("nope")
        with self.assertRaises(SettingsError):
            Settings().merged({"schema_version": SCHEMA_VERSION + 1})
        with self.assertRaises(SettingsError):
            Settings().merged({"schema_version": SCHEMA_VERSION - 1})

    def test_merge_subtitle_keys(self) -> None:
        s = Settings().merged({"subtitle": {"a": 1}}).merged({"subtitle": {"b": 2}})
        self.assertEqual(s.subtitle, {"a": 1, "b": 2})

    def test_merge_ui_keys_and_replace_lists(self) -> None:
        s = (
            Settings()
            .merged({"ui": {"dismissed_recommendations": ["a"]}})
            .merged({"ui": {"other": True}})
            .merged({"ui": {"dismissed_recommendations": ["b"]}})
        )
        self.assertEqual(s.ui, {"dismissed_recommendations": ["b"], "other": True})


class FileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.dir = Path(self._tmp.name)
        self.path = self.dir / "sub" / "settings.json"

    def test_round_trip(self) -> None:
        s = Settings().merged(
            {
                "interpret": {"device_id": "wasapi:microphone:麥克風"},
                "subtitle": {"k": "值"},
            }
        )
        save_settings(s, self.path)
        self.assertEqual(load_settings(self.path), s)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("麥克風", text)  # ensure_ascii=False：使用者能直接讀

    def test_missing_file_returns_none(self) -> None:
        self.assertIsNone(load_settings(self.path))

    def test_invalid_json(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b"{not json")
        with self.assertRaises(SettingsError) as ctx:
            load_settings(self.path)
        self.assertIn(str(self.path), str(ctx.exception))

    def test_invalid_utf8(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b'{"mode": "\xff"}')
        with self.assertRaises(SettingsError):
            load_settings(self.path)

    def test_too_large(self) -> None:
        self.path.parent.mkdir(parents=True)
        self.path.write_bytes(b" " * (store_module.MAX_SETTINGS_BYTES + 1))
        with self.assertRaises(SettingsError):
            load_settings(self.path)

    def test_atomic_write_failure_keeps_original_and_cleans_up(self) -> None:
        save_settings(Settings(), self.path)
        before = self.path.read_bytes()
        with (
            mock.patch.object(store_module.os, "replace", side_effect=OSError("busy")),
            self.assertRaises(OSError),
        ):
            write_json_atomic(self.path, {"schema_version": 1, "mode": "interpret"})
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(
            sorted(p.name for p in self.path.parent.iterdir()), ["settings.json"]
        )

    def test_atomic_write_replaces_content(self) -> None:
        write_json_atomic(self.path, {"a": 1})
        write_json_atomic(self.path, {"a": 2})
        self.assertEqual(json.loads(self.path.read_text(encoding="utf-8")), {"a": 2})
        self.assertEqual(len(list(self.path.parent.iterdir())), 1)


class StoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "settings.json"

    def test_load_missing_uses_defaults_without_writing(self) -> None:
        store = SettingsStore(self.path)
        self.assertIsNone(store.load())
        self.assertEqual(store.get(), Settings())
        self.assertFalse(self.path.exists())

    def test_update_persists(self) -> None:
        store = SettingsStore(self.path)
        store.load()
        updated = store.update({"mode": "interpret"})
        self.assertEqual(updated.mode, "interpret")
        fresh = SettingsStore(self.path)
        self.assertIsNone(fresh.load())
        self.assertEqual(fresh.get().mode, "interpret")

    def test_is_new_until_first_save(self) -> None:
        store = SettingsStore(self.path)
        # load() 之前不知道檔案在不在：保守地當成「不是新的」，不會觸發自動套用
        self.assertFalse(store.is_new)
        store.load()
        self.assertTrue(store.is_new)
        with self.assertRaises(SettingsError):
            store.update({"mode": "karaoke"})
        self.assertTrue(store.is_new)
        store.update({"mode": "interpret"})
        self.assertFalse(store.is_new)
        again = SettingsStore(self.path)
        again.load()
        self.assertFalse(again.is_new)

    def test_update_if_new_only_writes_a_new_file(self) -> None:
        store = SettingsStore(self.path)
        store.load()
        first = store.update_if_new({"asr": {"device": "cuda"}})
        self.assertIsNotNone(first)
        self.assertEqual(store.get().asr.device, "cuda")
        self.assertFalse(store.is_new)
        store.update({"asr": {"device": "cpu"}})
        # 檢查與寫入在同一把鎖內：已存過的設定不會被覆寫
        self.assertIsNone(store.update_if_new({"asr": {"device": "cuda"}}))
        self.assertEqual(store.get().asr.device, "cpu")
        self.assertIn('"cpu"', self.path.read_text(encoding="utf-8"))

    def test_invalid_file_is_not_new(self) -> None:
        # 使用者存過（後來壞掉）的設定檔不算第一次執行：不自動覆寫成推薦組合
        self.path.write_text("{broken", encoding="utf-8")
        store = SettingsStore(self.path)
        with self.assertLogs("kikitori.config.store", "WARNING"):
            store.load()
        self.assertFalse(store.is_new)

    def test_failed_update_changes_nothing(self) -> None:
        store = SettingsStore(self.path)
        store.update({"mode": "interpret"})
        before = self.path.read_bytes()
        with self.assertRaises(SettingsError):
            store.update({"media": {"pause_secs": "slow"}})
        self.assertEqual(store.get().mode, "interpret")
        self.assertEqual(self.path.read_bytes(), before)

    def test_invalid_file_is_kept_and_renamed_on_first_update(self) -> None:
        self.path.write_text(
            '{"schema_version": 1, "mode": "karaoke"}', encoding="utf-8"
        )
        store = SettingsStore(self.path)
        with self.assertLogs("kikitori.config.store", "WARNING"):
            warning = store.load()
        self.assertIsNotNone(warning)
        assert warning is not None
        self.assertIn("mode", warning)
        self.assertEqual(store.get(), Settings())
        # 載入失敗不覆寫原檔
        self.assertIn("karaoke", self.path.read_text(encoding="utf-8"))
        with self.assertLogs("kikitori.config.store", "WARNING"):
            store.update({"partner_language": "en"})
        backup = self.path.with_name("settings.json.invalid")
        self.assertIn("karaoke", backup.read_text(encoding="utf-8"))
        self.assertEqual(
            load_settings(self.path), Settings().merged({"partner_language": "en"})
        )


class PathTest(unittest.TestCase):
    def test_windows_appdata(self) -> None:
        path = config_dir_for(
            "Windows",
            {"APPDATA": r"C:\Users\a\AppData\Roaming"},
            PureWindowsPath(r"C:\Users\a"),
        )
        self.assertEqual(path, PureWindowsPath(r"C:\Users\a\AppData\Roaming\kikitori"))

    def test_windows_fallback_when_appdata_missing_or_relative(self) -> None:
        for env in ({}, {"APPDATA": "relative"}):
            with self.subTest(env=env):
                path = config_dir_for("Windows", env, PureWindowsPath(r"C:\Users\a"))
                self.assertEqual(
                    path, PureWindowsPath(r"C:\Users\a\AppData\Roaming\kikitori")
                )

    def test_macos(self) -> None:
        path = config_dir_for("Darwin", {}, PurePosixPath("/Users/a"))
        self.assertEqual(
            path, PurePosixPath("/Users/a/Library/Application Support/kikitori")
        )

    def test_linux_xdg(self) -> None:
        path = config_dir_for(
            "Linux", {"XDG_CONFIG_HOME": "/cfg"}, PurePosixPath("/home/a")
        )
        self.assertEqual(path, PurePosixPath("/cfg/kikitori"))

    def test_linux_fallback(self) -> None:
        for env in ({}, {"XDG_CONFIG_HOME": "rel/path"}):
            with self.subTest(env=env):
                path = config_dir_for("Linux", env, PurePosixPath("/home/a"))
                self.assertEqual(path, PurePosixPath("/home/a/.config/kikitori"))

    def test_override_env(self) -> None:
        path = config_dir_for(
            "Linux",
            {CONFIG_DIR_ENV: "/tmp/kk", "XDG_CONFIG_HOME": "/cfg"},
            PurePosixPath("/h"),
        )
        self.assertEqual(path, PurePosixPath("/tmp/kk"))

    def test_relative_override_is_ignored(self) -> None:
        with self.assertLogs("kikitori.config.paths", "WARNING"):
            path = config_dir_for("Linux", {CONFIG_DIR_ENV: "rel"}, PurePosixPath("/h"))
        self.assertEqual(path, PurePosixPath("/h/.config/kikitori"))

    def test_default_settings_path_uses_environment(self) -> None:
        from kikitori.config import default_settings_path

        with (
            tempfile.TemporaryDirectory() as tmp,
            mock.patch.dict(os.environ, {CONFIG_DIR_ENV: tmp}),
        ):
            self.assertEqual(default_settings_path(), Path(tmp) / "settings.json")


if __name__ == "__main__":
    unittest.main()
