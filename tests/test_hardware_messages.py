"""引擎傳給 UI 的結構化訊息（`{code, params, message}`）與 hardware.md 訊息碼表的一致性。

UI 依 code 組出各語系的句子，所以 code 與 params 是對外介面：這裡確認每個 code 的
參數齊全、型別是基本型別，而且 `docs/specs/hardware.md` 的訊息碼表與程式完全一致
（UI 端的字典是照那張表寫的）。
"""

import json
import math
import re
import unittest
from pathlib import Path

from kikitori.hardware.messages import CATALOG, Message, message

DOC = Path(__file__).resolve().parents[1] / "docs" / "specs" / "hardware.md"
SECTION = "## 訊息碼"


def doc_table() -> dict[str, tuple[tuple[str, ...], str]]:
    """hardware.md「訊息碼」一節的表格：code → (params, English)。"""
    text = DOC.read_text(encoding="utf-8")
    start = text.index(SECTION)
    end = text.find("\n## ", start + len(SECTION))
    rows: dict[str, tuple[tuple[str, ...], str]] = {}
    for line in text[start : end if end != -1 else None].splitlines():
        if not line.startswith("| `"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split(" | ")]
        code = cells[0].strip("`")
        params = tuple(re.findall(r"`([a-z_]+)`", cells[1]))
        rows[code] = (params, cells[2])
    return rows


class MessageTest(unittest.TestCase):
    def test_render_and_to_dict(self) -> None:
        m = message("translation_model_not_installed", model="qwen3.5:9b")
        self.assertEqual(m.code, "translation_model_not_installed")
        self.assertEqual(m.params, {"model": "qwen3.5:9b"})
        data = m.to_dict()
        self.assertEqual(set(data), {"code", "params", "message"})
        self.assertIn("qwen3.5:9b", data["message"])
        self.assertEqual(str(m), data["message"])
        self.assertEqual(json.loads(json.dumps(data)), data)

    def test_float_params_are_formatted(self) -> None:
        m = message("cpu_low_ram", ram_gb=7.86)
        self.assertIn("7.9 GB", m.message)
        self.assertEqual(m.params["ram_gb"], 7.86)

    def test_equal_messages_compare_equal(self) -> None:
        self.assertEqual(
            message("asr_auto_uses_cpu", path="fast_asr"),
            message("asr_auto_uses_cpu", path="fast_asr"),
        )
        self.assertIsInstance(message("memory_unknown"), Message)

    def test_unknown_code_and_wrong_params_are_programming_errors(self) -> None:
        with self.assertRaises(ValueError):
            message("no_such_code")
        with self.assertRaises(ValueError):
            message("translation_model_not_installed")
        with self.assertRaises(ValueError):
            message("translation_model_not_installed", model="x", extra=1)

    def test_params_must_be_primitive_and_finite(self) -> None:
        for bad in ([1], None, True, math.nan, {"a": 1}):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                message("translation_model_not_installed", model=bad)  # type: ignore[arg-type]

    def test_every_template_renders_with_its_params(self) -> None:
        samples = {"str": "x", "int": 3, "float": 1.5}
        for code, spec in CATALOG.items():
            with self.subTest(code=code):
                params = {p: samples[spec.types[p]] for p in spec.params}
                self.assertTrue(message(code, **params).message)

    def test_codes_are_snake_case(self) -> None:
        for code in CATALOG:
            self.assertRegex(code, r"^[a-z][a-z0-9_]*$")


class DocTableTest(unittest.TestCase):
    def test_hardware_md_lists_every_code_with_its_params_and_text(self) -> None:
        table = doc_table()
        self.assertEqual(sorted(table), sorted(CATALOG))
        for code, spec in CATALOG.items():
            with self.subTest(code=code):
                params, english = table[code]
                self.assertEqual(params, spec.params)
                self.assertEqual(english, spec.english)


if __name__ == "__main__":
    unittest.main()
