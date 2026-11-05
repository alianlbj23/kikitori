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
        self.assertEqual(s.target_language, "zh-TW")
        self.assertEqual(s.partner_language, "ja")
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
        self.assertInvalid(_with("partner_language", "ko"), "partner_language")
        self.assertInvalid(_with("target_language", "ja"), "target_language")
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
