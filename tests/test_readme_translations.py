"""README 的各語言版本：每個介面語言一份，結構與繁中原版（README.zh-TW.md）一致。

GitHub 首頁顯示的 README.md 是英文版（給外國使用者）；內容以繁中版為準。

介面支援 13 種語言（`app/src/locales/`），README 也提供同樣的語言。翻譯檔很容易在原版改了之後
落後，這裡檢查能機械判斷的部分：檔案齊全、語言切換列一致、標題層級與程式碼區塊的數量和原版相同、
相對連結都指到存在的檔案。內容是否忠實仍要人工確認。
"""

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCALES_DIR = ROOT / "app" / "src" / "locales"
SOURCE_LOCALE = "zh-TW"
DEFAULT_LOCALE = "en"
"""README.md（GitHub 首頁）的語言。"""

# 語言切換列的順序與顯示名稱（與介面語言選單相同）
LANGUAGE_NAMES: dict[str, str] = {
    "zh-TW": "繁體中文",
    "zh-CN": "简体中文",
    "en": "English",
    "ja": "日本語",
    "ko": "한국어",
    "es": "Español",
    "fr": "Français",
    "de": "Deutsch",
    "pt-BR": "Português (Brasil)",
    "ru": "Русский",
    "vi": "Tiếng Việt",
    "th": "ไทย",
    "id": "Bahasa Indonesia",
}

HEADING = re.compile(r"^(#{1,6}) ", re.MULTILINE)
FENCE = re.compile(r"^```", re.MULTILINE)
LINK = re.compile(r"\]\(([^)\s]+)\)")


def readme_name(locale: str) -> str:
    return "README.md" if locale == DEFAULT_LOCALE else f"README.{locale}.md"


def ui_locales() -> set[str]:
    return {
        p.stem
        for p in LOCALES_DIR.glob("*.ts")
        if p.stem not in {"index", "types"} and not p.stem.endswith(".test")
    }


def switcher(current: str) -> str:
    parts = [
        f"**{name}**" if code == current else f"[{name}]({readme_name(code)})"
        for code, name in LANGUAGE_NAMES.items()
    ]
    return " | ".join(parts)


def structure(text: str) -> tuple[list[int], int]:
    """標題層級的序列（忽略程式碼區塊內的 #）與程式碼區塊的界線數。"""
    outside = re.sub(r"^```.*?^```", "", text, flags=re.MULTILINE | re.DOTALL)
    return [len(m.group(1)) for m in HEADING.finditer(outside)], len(
        FENCE.findall(text)
    )


class ReadmeTranslationsTest(unittest.TestCase):
    def test_every_ui_language_has_a_readme(self) -> None:
        self.assertEqual(set(LANGUAGE_NAMES), ui_locales())
        for locale in LANGUAGE_NAMES:
            with self.subTest(locale=locale):
                self.assertTrue((ROOT / readme_name(locale)).is_file())

    def test_language_switcher_is_the_same_everywhere(self) -> None:
        for locale in LANGUAGE_NAMES:
            with self.subTest(locale=locale):
                text = (ROOT / readme_name(locale)).read_text(encoding="utf-8")
                self.assertEqual(text.splitlines()[2], switcher(locale))

    def test_translations_keep_the_source_structure(self) -> None:
        source = structure(
            (ROOT / readme_name(SOURCE_LOCALE)).read_text(encoding="utf-8")
        )
        for locale in LANGUAGE_NAMES:
            if locale == SOURCE_LOCALE:
                continue
            with self.subTest(locale=locale):
                text = (ROOT / readme_name(locale)).read_text(encoding="utf-8")
                self.assertEqual(structure(text), source)

    def test_relative_links_point_to_existing_files(self) -> None:
        for locale in LANGUAGE_NAMES:
            text = (ROOT / readme_name(locale)).read_text(encoding="utf-8")
            for target in LINK.findall(text):
                if re.match(r"^[a-z]+:", target) or target.startswith("#"):
                    continue
                with self.subTest(locale=locale, link=target):
                    self.assertTrue((ROOT / target.split("#")[0]).exists())

    def test_structure_ignores_hashes_inside_code_blocks(self) -> None:
        text = "# a\n\n```text\n# not a heading\n```\n\n## b\n"
        self.assertEqual(structure(text), ([1, 2], 2))


if __name__ == "__main__":
    unittest.main()
