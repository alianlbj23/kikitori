"""`textnorm.py` 的單元測試（正規化規則有版本號，改規則要同步改測試與版本）。

執行：uv run python -m unittest discover -s experiments/vtuber_eval -p "test_*.py"
"""

import importlib.util
import unittest
from pathlib import Path
from types import ModuleType


def _load() -> ModuleType:
    path = Path(__file__).resolve().parent / "textnorm.py"
    spec = importlib.util.spec_from_file_location("vtuber_eval_textnorm", path)
    if spec is None or spec.loader is None:
        raise ImportError(path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


textnorm = _load()


class BasicNormalizerTest(unittest.TestCase):
    def test_removes_punctuation_symbols_and_spaces(self) -> None:
        self.assertEqual(
            textnorm.normalize_basic("こんにちは、世界！ 今日は♪ いい天気。"),
            "こんにちは世界今日はいい天気",
        )

    def test_nfkc_folds_fullwidth_and_lowercases(self) -> None:
        self.assertEqual(textnorm.normalize_basic("ＡＢＣ１２３ｗ"), "abc123w")
        self.assertEqual(textnorm.normalize_basic("ｶﾀｶﾅ"), "カタカナ")

    def test_ascii_brackets_removed_with_content(self) -> None:
        self.assertEqual(textnorm.normalize_basic("[音楽] (笑) はい <x>"), "はい")

    def test_fullwidth_parentheses_keep_content_like_whisper(self) -> None:
        # Whisper 的括號規則在 NFKC 之前執行，全形括號只會刪掉括號本身
        self.assertEqual(textnorm.normalize_basic("はい（笑）"), "はい笑")

    def test_long_vowel_mark_is_kept(self) -> None:
        self.assertEqual(textnorm.normalize_basic("えーと、ラーメン"), "えーとラーメン")


class FillerTest(unittest.TestCase):
    def test_standalone_fillers_removed(self) -> None:
        self.assertEqual(
            textnorm.normalize_no_fillers("えー、あの、今日はですね"), "今日はですね"
        )

    def test_demonstrative_attached_is_kept(self) -> None:
        self.assertEqual(textnorm.normalize_no_fillers("あの人が来た"), "あの人が来た")

    def test_leading_elongated_filler_removed(self) -> None:
        self.assertEqual(textnorm.normalize_no_fillers("えーっと行きます"), "行きます")
        self.assertEqual(textnorm.normalize_no_fillers("うーんそうだね"), "そうだね")

    def test_backchannel_answers_are_kept(self) -> None:
        self.assertEqual(textnorm.normalize_no_fillers("うんうん"), "うんうん")
        self.assertEqual(
            textnorm.normalize_no_fillers("ええ、そうですね"), "ええそうですね"
        )

    def test_laughter_removed(self) -> None:
        self.assertEqual(textnorm.normalize_no_fillers("やばいｗｗｗ 草"), "やばい")

    def test_ascii_word_ending_with_w_kept(self) -> None:
        self.assertEqual(textnorm.normalize_no_fillers("know"), "know")


class EditDistanceTest(unittest.TestCase):
    def test_basic_cases(self) -> None:
        self.assertEqual(textnorm.edit_distance("", "abc"), 3)
        self.assertEqual(textnorm.edit_distance("abc", "abc"), 0)
        self.assertEqual(textnorm.edit_distance("kitten", "sitting"), 3)
        self.assertEqual(textnorm.edit_distance("あいう", "あう"), 1)


if __name__ == "__main__":
    unittest.main()
