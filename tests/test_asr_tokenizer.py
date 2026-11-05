"""WhisperTokenizer 的測試：用手寫的小型 tokenizer.json 驗證預切、BPE 合併與編解碼。

與真正 Whisper 分詞器（HF `tokenizers`）的逐一比對放在
`experiments/asr_benchmark/check_tokenizer.py`（需要下載的模型檔）。
"""

import json
import tempfile
import unittest
from pathlib import Path
from typing import Any

from kikitori.asr.tokenizer import WhisperTokenizer, bytes_to_unicode, pretokenize

SPECIALS = [
    "<|endoftext|>",
    "<|startoftranscript|>",
    "<|en|>",
    "<|zh|>",
    "<|ja|>",
    "<|translate|>",
    "<|transcribe|>",
    "<|startoflm|>",
    "<|startofprev|>",
    "<|nocaptions|>",
    "<|notimestamps|>",
]
MERGES = [("Ġ", "t"), ("h", "e"), ("Ġt", "he"), ("l", "l"), ("ll", "o"), ("Ġ", "w")]


def make_tokenizer_data(merge_format: str = "string") -> dict[str, Any]:
    """位元組 256 個 token + 合併結果 + Whisper 特殊 token。"""
    vocab = {ch: i for i, ch in enumerate(bytes_to_unicode().values())}
    for a, b in MERGES:
        vocab.setdefault(a + b, len(vocab))
    merges: list[Any] = [f"{a} {b}" for a, b in MERGES]
    if merge_format == "pair":
        merges = [[a, b] for a, b in MERGES]
    first_special = len(vocab)
    added = [
        {"id": first_special + i, "content": token, "special": True}
        for i, token in enumerate(SPECIALS)
    ]
    return {
        "model": {"type": "BPE", "vocab": vocab, "merges": merges},
        "added_tokens": added,
    }


class PretokenizeTest(unittest.TestCase):
    def test_gpt2_rules(self) -> None:
        cases = {
            "Hello world": ["Hello", " world"],
            "I'm  here": ["I", "'m", " ", " here"],
            "don't 'S": ["don", "'t", " '", "S"],
            "a  ": ["a", "  "],
            "a\n\nb": ["a", "\n", "\n", "b"],
            "  x": [" ", " x"],
            "abc123 456": ["abc", "123", " 456"],
            "x, y!?": ["x", ",", " y", "!?"],
            "うん。アイオイ": ["うん", "。", "アイオイ"],
            "日本語 English": ["日本語", " English"],
            "": [],
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(pretokenize(text), expected)

    def test_pieces_concatenate_to_input(self) -> None:
        text = "  Mixed\t text 123,  with 「括弧」 and\n\n  newlines  "
        self.assertEqual("".join(pretokenize(text)), text)


class WhisperTokenizerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tok = WhisperTokenizer(make_tokenizer_data())

    def test_special_token_ids(self) -> None:
        base = 256 + 6  # 位元組 + 6 個合併（"Ġw" 與 "Ġt" 等互不重複）
        self.assertEqual(self.tok.eot, base)
        self.assertEqual(self.tok.sot, base + 1)
        self.assertEqual(self.tok.transcribe, base + 6)
        self.assertEqual(self.tok.sot_prev, base + 8)
        self.assertEqual(self.tok.no_speech, base + 9)  # 舊模型用 <|nocaptions|>
        self.assertEqual(self.tok.no_timestamps, base + 10)

    def test_language_codes_between_sot_and_translate(self) -> None:
        self.assertEqual(self.tok.language_codes, ("en", "zh", "ja"))
        self.assertEqual(self.tok.language_token("ja"), self.tok.sot + 3)
        with self.assertRaises(ValueError):
            self.tok.language_token("fr")

    def test_bpe_merges_by_rank(self) -> None:
        ids = self.tok.encode(" the hello")
        tokens = [self.tok._id_to_token[i] for i in ids]
        # " the" → Ġt + he → Ġthe；" hello" → Ġ h e ll o → he 先合併，ll 再合併成 llo
        self.assertEqual(tokens, ["Ġthe", "Ġ", "he", "llo"])

    def test_merges_in_pair_format(self) -> None:
        pair_tok = WhisperTokenizer(make_tokenizer_data("pair"))
        self.assertEqual(pair_tok.encode(" the hello"), self.tok.encode(" the hello"))

    def test_round_trip_any_unicode(self) -> None:
        for text in (
            " hello world",
            "うん。アイオイキンセーさん",
            "émoji 😀\n\ttab",
            " ♪♪ ",
        ):
            with self.subTest(text=text):
                self.assertEqual(self.tok.decode(self.tok.encode(text)), text)

    def test_literal_special_token_text_is_not_special(self) -> None:
        ids = self.tok.encode("<|en|>")
        self.assertTrue(all(i < self.tok.eot for i in ids))
        self.assertEqual(self.tok.decode(ids), "<|en|>")

    def test_decode_skips_special_ids(self) -> None:
        ids = [
            self.tok.sot,
            *self.tok.encode(" hi"),
            self.tok.eot,
            self.tok.no_timestamps,
        ]
        self.assertEqual(self.tok.decode(ids), " hi")

    def test_decode_invalid_utf8_is_replaced(self) -> None:
        byte_ids = {b: i for i, b in enumerate(bytes_to_unicode())}
        self.assertEqual(self.tok.decode([byte_ids[0xE3]]), "�")

    def test_from_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokenizer.json"
            path.write_text(json.dumps(make_tokenizer_data()), encoding="utf-8")
            self.assertEqual(WhisperTokenizer.from_file(path).sot, self.tok.sot)


class InvalidTokenizerTest(unittest.TestCase):
    def test_missing_special_token(self) -> None:
        data = make_tokenizer_data()
        data["added_tokens"] = [
            t for t in data["added_tokens"] if t["content"] != "<|startofprev|>"
        ]
        with self.assertRaises(ValueError):
            WhisperTokenizer(data)

    def test_not_bpe(self) -> None:
        with self.assertRaises(ValueError):
            WhisperTokenizer({"model": {"type": "WordPiece"}})

    def test_bad_merge_entry(self) -> None:
        data = make_tokenizer_data()
        data["model"]["merges"].append("onlyone")
        with self.assertRaises(ValueError):
            WhisperTokenizer(data)

    def test_non_object_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "tokenizer.json"
            path.write_text("[]", encoding="utf-8")
            with self.assertRaises(TypeError):
                WhisperTokenizer.from_file(path)


if __name__ == "__main__":
    unittest.main()
