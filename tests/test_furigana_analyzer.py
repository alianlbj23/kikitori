"""Sudachi 分析器的包裝：C 單位取讀音、A 單位逐詞、關閉後不再分析、錯誤往上報。

辭典要下載（約 69 MiB），單元測試以假的 `sudachipy.Dictionary` 代替；真的辭典由打包的
`--self-check`（已下載時）與 `experiments/furigana_dict/` 驗證。
"""

import tempfile
import unittest
from pathlib import Path
from typing import Any, ClassVar
from unittest import mock

import sudachipy

from kikitori.furigana.analyzer import MAX_TEXT_CHARS, FuriganaAnalyzer


class _Morpheme:
    def __init__(self, surface: str, reading: str, parts: list["_Morpheme"]) -> None:
        self._surface = surface
        self._reading = reading
        self._parts = parts or [self]

    def surface(self) -> str:
        return self._surface

    def reading_form(self) -> str:
        return self._reading

    def split(self, mode: Any) -> list["_Morpheme"]:
        return self._parts


class _Tokenizer:
    def __init__(self, morphemes: list[_Morpheme]) -> None:
        self.morphemes = morphemes
        self.calls: list[str] = []

    def tokenize(self, text: str) -> list[_Morpheme]:
        self.calls.append(text)
        return self.morphemes


class _Dictionary:
    instances: ClassVar[list["_Dictionary"]] = []
    tokenizer: ClassVar[_Tokenizer] = _Tokenizer([])

    def __init__(self, dict: str) -> None:
        self.path = dict
        self.closed = False
        _Dictionary.instances.append(self)

    def create(self, mode: Any) -> _Tokenizer:
        return _Dictionary.tokenizer

    def close(self) -> None:
        self.closed = True


class AnalyzerTest(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dic = Path(tmp.name) / "system_core.dic"
        self.dic.write_bytes(b"x")
        _Dictionary.instances = []
        _Dictionary.tokenizer = _Tokenizer(
            [
                _Morpheme(
                    "外国人",
                    "ガイコクジン",
                    [_Morpheme("外国", "ガイコク", []), _Morpheme("人", "ジン", [])],
                ),
                _Morpheme("を", "ヲ", []),
                _Morpheme("食べる", "タベル", []),
            ]
        )
        patcher = mock.patch.object(sudachipy, "Dictionary", _Dictionary)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ruby_uses_finer_parts(self) -> None:
        analyzer = FuriganaAnalyzer(self.dic)
        self.assertEqual(_Dictionary.instances[0].path, str(self.dic))
        self.assertEqual(
            analyzer.ruby("外国人を食べる"),
            [(0, 2, "がいこく"), (2, 3, "じん"), (4, 5, "た")],
        )

    def test_close_releases_and_stops_analysis(self) -> None:
        analyzer = FuriganaAnalyzer(self.dic)
        analyzer.close()
        analyzer.close()
        self.assertTrue(_Dictionary.instances[0].closed)
        self.assertTrue(analyzer.closed)
        self.assertEqual(analyzer.ruby("外国人を食べる"), [])
        self.assertEqual(_Dictionary.tokenizer.calls, [])

    def test_too_long_or_empty_text_is_not_analyzed(self) -> None:
        analyzer = FuriganaAnalyzer(self.dic)
        self.assertEqual(analyzer.ruby(""), [])
        self.assertEqual(analyzer.ruby("あ" * (MAX_TEXT_CHARS + 1)), [])
        self.assertEqual(_Dictionary.tokenizer.calls, [])

    def test_missing_or_relative_dictionary(self) -> None:
        with self.assertRaises(FileNotFoundError):
            FuriganaAnalyzer(self.dic.with_name("missing.dic"))
        with self.assertRaises(ValueError):
            FuriganaAnalyzer(Path("relative.dic"))

    def test_sudachi_errors_are_reported(self) -> None:
        analyzer = FuriganaAnalyzer(self.dic)
        with (
            mock.patch.object(
                _Dictionary.tokenizer, "tokenize", side_effect=ValueError("bad input")
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            analyzer.ruby("外国人")
        self.assertIn("bad input", str(ctx.exception))

    def test_tokenizer_failure_closes_the_dictionary(self) -> None:
        with (
            mock.patch.object(
                _Dictionary, "create", side_effect=ValueError("bad mode")
            ),
            self.assertRaises(RuntimeError),
        ):
            FuriganaAnalyzer(self.dic)
        self.assertTrue(_Dictionary.instances[0].closed)

    def test_dictionary_open_errors_are_reported(self) -> None:
        with (
            mock.patch.object(
                _Dictionary, "__init__", side_effect=ValueError("Invalid header")
            ),
            self.assertRaises(RuntimeError) as ctx,
        ):
            FuriganaAnalyzer(self.dic)
        self.assertIn("Invalid header", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
