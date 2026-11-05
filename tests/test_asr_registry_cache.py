"""asr 模型清單與快取目錄的測試。"""

import re
import unittest
from pathlib import PurePosixPath, PureWindowsPath

from kikitori.asr.cache import cache_dir_for
from kikitori.asr.registry import MODELS, get_model

HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
VALID_TIERS = {"fastest", "fast", "medium", "slow", "slowest"}


class RegistryTest(unittest.TestCase):
    def test_ids_match_keys(self) -> None:
        for key, spec in MODELS.items():
            self.assertEqual(key, spec.id)

    def test_specs_are_pinned_and_complete(self) -> None:
        for spec in MODELS.values():
            with self.subTest(model=spec.id):
                self.assertRegex(spec.revision, HEX40)
                names = [f.name for f in spec.files]
                self.assertEqual(len(names), len(set(names)))
                for required in ("model.bin", "config.json", "tokenizer.json"):
                    self.assertIn(required, names)
                self.assertTrue(any(n.startswith("vocabulary.") for n in names))
                for file in spec.files:
                    self.assertRegex(file.sha256, HEX64)
                    self.assertGreater(file.size, 0)
                    self.assertNotIn("/", file.name)
                    self.assertNotIn("\\", file.name)
                self.assertEqual(spec.total_bytes, sum(f.size for f in spec.files))
                self.assertIn(spec.n_mels, (80, 128))
                self.assertIn(spec.speed_tier, VALID_TIERS)
                self.assertEqual(spec.license, "MIT")

    def test_large_v3_family_uses_128_mels(self) -> None:
        for model_id in ("large-v3", "large-v3-turbo"):
            spec = get_model(model_id)
            self.assertEqual(spec.n_mels, 128)
            self.assertIn("preprocessor_config.json", [f.name for f in spec.files])

    def test_required_languages_supported(self) -> None:
        for spec in MODELS.values():
            for language in ("ja", "en", "zh"):
                self.assertTrue(spec.supports(language))

    def test_resource_estimates_are_filled(self) -> None:
        for spec in MODELS.values():
            with self.subTest(model=spec.id):
                self.assertGreater(spec.approx_cpu_ram_mb, 0)
                self.assertGreater(spec.approx_gpu_vram_int8_mb, 0)
                self.assertLess(spec.approx_gpu_vram_int8_mb, spec.approx_gpu_vram_mb)

    def test_turbo_no_speech_is_flagged_unreliable(self) -> None:
        self.assertFalse(get_model("large-v3-turbo").no_speech_reliable)
        for model_id in ("tiny", "base", "small", "medium", "large-v3"):
            self.assertTrue(get_model(model_id).no_speech_reliable)

    def test_unknown_model(self) -> None:
        with self.assertRaises(KeyError) as ctx:
            get_model("huge")
        self.assertIn("large-v3-turbo", str(ctx.exception))


class CacheDirTest(unittest.TestCase):
    def test_windows_uses_localappdata(self) -> None:
        path = cache_dir_for(
            "Windows",
            {"LOCALAPPDATA": r"D:\Users\me\AppData\Local"},
            PureWindowsPath(r"C:\Users\me"),
        )
        self.assertEqual(
            path, PureWindowsPath(r"D:\Users\me\AppData\Local\kikitori\models")
        )

    def test_windows_without_or_with_invalid_localappdata(self) -> None:
        for env in ({}, {"LOCALAPPDATA": ""}, {"LOCALAPPDATA": r"relative\dir"}):
            with self.subTest(env=env):
                path = cache_dir_for("Windows", env, PureWindowsPath(r"C:\Users\me"))
                self.assertEqual(
                    path, PureWindowsPath(r"C:\Users\me\AppData\Local\kikitori\models")
                )

    def test_macos_ignores_xdg(self) -> None:
        path = cache_dir_for(
            "Darwin", {"XDG_CACHE_HOME": "/tmp/xdg"}, PurePosixPath("/Users/me")
        )
        self.assertEqual(
            path, PurePosixPath("/Users/me/Library/Caches/kikitori/models")
        )

    def test_linux_xdg_cache_home(self) -> None:
        path = cache_dir_for(
            "Linux", {"XDG_CACHE_HOME": "/data/cache"}, PurePosixPath("/home/me")
        )
        self.assertEqual(path, PurePosixPath("/data/cache/kikitori/models"))

    def test_linux_fallback_when_xdg_missing_or_relative(self) -> None:
        for env in ({}, {"XDG_CACHE_HOME": "   "}, {"XDG_CACHE_HOME": "rel/cache"}):
            with self.subTest(env=env):
                path = cache_dir_for("Linux", env, PurePosixPath("/home/me"))
                self.assertEqual(path, PurePosixPath("/home/me/.cache/kikitori/models"))

    def test_other_unix_follows_xdg(self) -> None:
        path = cache_dir_for("FreeBSD", {}, PurePosixPath("/home/me"))
        self.assertEqual(path, PurePosixPath("/home/me/.cache/kikitori/models"))


if __name__ == "__main__":
    unittest.main()
