"""v0.3 G3：「請再說一次」的固定譯文表涵蓋語言清單的每一個字幕語言。

表在前端（`app/src/locales/phrases/say-again.ts`，不呼叫翻譯），語言清單在引擎
（`kikitori.languages`）；兩邊各自改動時，這裡確保不會有語言少了一句。
"""

import re
import unittest
from pathlib import Path

from kikitori.languages import LANGUAGES

PHRASES = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "src"
    / "locales"
    / "phrases"
    / "say-again.ts"
)


class SayAgainPhrasesTest(unittest.TestCase):
    def test_every_catalog_language_has_a_phrase(self) -> None:
        text = PHRASES.read_text(encoding="utf-8")
        keys = set(
            re.findall(r'^\s+"?([a-z]{2}(?:-[A-Z]{2})?)"?:\s*"', text, re.MULTILINE)
        )
        self.assertEqual(keys, {language.code for language in LANGUAGES})


if __name__ == "__main__":
    unittest.main()
