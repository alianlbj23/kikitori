"""Whisper 的 byte-level BPE 分詞器（讀取模型目錄裡的 `tokenizer.json`）。

位於 asr 模組的推論層：把 prompt 文字編成 token id、把模型輸出的 id 解回文字，
並提供特殊 token（`<|startoftranscript|>`、語言 token 等）的 id。
自行實作而不用 Hugging Face `tokenizers`，是因為後者依賴 `huggingface-hub`，
會帶入 MPL-2.0 授權的 `tqdm`（見 `docs/specs/asr.md`）。演算法與 GPT-2／Whisper
相同：GPT-2 regex 預切 → UTF-8 位元組映射成可見字元 → 依 merges 排名合併。
"""

import itertools
import json
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

_CONTRACTIONS = ("s", "t", "re", "ve", "m", "ll", "d")  # 順序同 GPT-2 regex 的分支


@lru_cache(maxsize=1)
def bytes_to_unicode() -> dict[int, str]:
    """GPT-2 的位元組 → 可見 Unicode 字元對照表。

    BPE 詞彙表以「可見字元」表示每個位元組，避免空白與控制字元出現在 token 字串裡。
    """
    visible = (
        list(range(ord("!"), ord("~") + 1))
        + list(range(ord("¡"), ord("¬") + 1))
        + list(range(ord("®"), ord("ÿ") + 1))
    )
    mapped = visible[:]
    extra = 0
    for byte in range(256):
        if byte not in visible:
            visible.append(byte)
            mapped.append(256 + extra)
            extra += 1
    return {byte: chr(code) for byte, code in zip(visible, mapped, strict=True)}


def _is_letter(ch: str) -> bool:
    return unicodedata.category(ch).startswith("L")


def _is_number(ch: str) -> bool:
    return unicodedata.category(ch).startswith("N")


def _is_other(ch: str) -> bool:
    return not (ch.isspace() or _is_letter(ch) or _is_number(ch))


def pretokenize(text: str) -> list[str]:
    """依 GPT-2 regex 把文字切成預切片段。

    等同 `'s|'t|'re|'ve|'m|'ll|'d| ?\\p{L}+| ?\\p{N}+| ?[^\\s\\p{L}\\p{N}]+|\\s+(?!\\S)|\\s+`。
    Python 標準 `re` 不支援 `\\p{L}`，所以用 `unicodedata` 分類逐字掃描。
    """
    pieces: list[str] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch == "'":
            suffix = next((s for s in _CONTRACTIONS if text.startswith(s, i + 1)), None)
            if suffix is not None:
                pieces.append(text[i : i + 1 + len(suffix)])
                i += 1 + len(suffix)
                continue
        # ` ?X+`：只有 U+0020 能當前置空白；空白本身不屬於任何一類，所以
        # 在空白位置只可能以「空白 + 類別」的形式成立。
        body = i + 1 if ch == " " else i
        if body < n:
            for predicate in (_is_letter, _is_number, _is_other):
                if predicate(text[body]):
                    end = body
                    while end < n and predicate(text[end]):
                        end += 1
                    pieces.append(text[i:end])
                    i = end
                    break
            else:
                predicate = None
            if predicate is not None:
                continue
        # 空白：`\s+(?!\S)` 把最後一個空白留給下一個片段當前置空白；
        # 只有一個空白或已到結尾時用 `\s+` 整段吃掉。
        end = i
        while end < n and text[end].isspace():
            end += 1
        if end < n and end - i >= 2:
            end -= 1
        pieces.append(text[i:end])
        i = end
    return pieces


class WhisperTokenizer:
    """讀取 Hugging Face 格式的 `tokenizer.json`，提供編碼、解碼與特殊 token id。"""

    def __init__(self, data: dict[str, Any]) -> None:
        """從已解析的 `tokenizer.json` 內容建立分詞器。

        Raises:
            ValueError: 檔案不是 BPE 模型，或缺少 Whisper 必要的特殊 token。
            TypeError: vocab／merges 的型別不對。
        """
        model = data.get("model")
        if not isinstance(model, dict) or model.get("type") != "BPE":
            raise ValueError("tokenizer.json must contain a BPE model")
        vocab = model.get("vocab")
        merges = model.get("merges")
        if not isinstance(vocab, dict) or not isinstance(merges, list):
            raise TypeError("tokenizer.json BPE model needs 'vocab' and 'merges'")

        self._token_to_id: dict[str, int] = {str(k): int(v) for k, v in vocab.items()}
        for added in data.get("added_tokens", []):
            self._token_to_id[str(added["content"])] = int(added["id"])
        self._id_to_token = {v: k for k, v in self._token_to_id.items()}

        ranks: dict[tuple[str, str], int] = {}
        for rank, merge in enumerate(merges):
            # 舊版格式是 "a b" 字串，新版是 ["a", "b"]
            pair = merge.split(" ", 1) if isinstance(merge, str) else merge
            if len(pair) != 2:
                raise ValueError(f"invalid merge entry #{rank}: {merge!r}")
            ranks[(str(pair[0]), str(pair[1]))] = rank
        self._ranks = ranks
        self._byte_encoder = bytes_to_unicode()
        self._byte_decoder = {c: b for b, c in self._byte_encoder.items()}
        self._bpe_cache: dict[str, tuple[str, ...]] = {}

        self.eot = self._special("<|endoftext|>")
        self.sot = self._special("<|startoftranscript|>")
        self.translate = self._special("<|translate|>")
        self.transcribe = self._special("<|transcribe|>")
        self.sot_lm = self._special("<|startoflm|>")
        self.sot_prev = self._special("<|startofprev|>")
        self.no_timestamps = self._special("<|notimestamps|>")
        no_speech = self._token_to_id.get("<|nospeech|>")
        self.no_speech = (
            no_speech if no_speech is not None else self._special("<|nocaptions|>")
        )
        # 語言 token 排在 sot 與 translate 之間（tiny～large-v2 有 99 個，large-v3 加 yue 共 100 個）
        self.language_codes: tuple[str, ...] = tuple(
            self._id_to_token[i][2:-2]
            for i in range(self.sot + 1, self.translate)
            if i in self._id_to_token
        )

    @classmethod
    def from_file(cls, path: Path) -> "WhisperTokenizer":
        """從檔案讀取 `tokenizer.json`。"""
        with path.open(encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            raise TypeError(f"{path} is not a JSON object")
        return cls(data)

    def _special(self, token: str) -> int:
        token_id = self._token_to_id.get(token)
        if token_id is None:
            raise ValueError(f"tokenizer.json is missing special token {token}")
        return token_id

    def token_id(self, token: str) -> int:
        """回傳 token 字串的 id；不存在時 `KeyError`。"""
        return self._token_to_id[token]

    def language_token(self, language: str) -> int:
        """回傳語言 token（例如 `<|ja|>`）的 id；不支援的語言丟 `ValueError`。"""
        if language not in self.language_codes:
            raise ValueError(f"unsupported language {language!r}")
        return self._token_to_id[f"<|{language}|>"]

    def _bpe(self, piece: str) -> tuple[str, ...]:
        cached = self._bpe_cache.get(piece)
        if cached is not None:
            return cached
        word = tuple(piece)
        while len(word) > 1:
            ranked = [
                (self._ranks[pair], idx)
                for idx, pair in enumerate(itertools.pairwise(word))
                if pair in self._ranks
            ]
            if not ranked:
                break
            _, idx = min(ranked)
            first, second = word[idx], word[idx + 1]
            merged: list[str] = []
            j = 0
            # 同一對在字中出現多次時要一起合併，否則結果與參考實作不同
            while j < len(word):
                if j < len(word) - 1 and word[j] == first and word[j + 1] == second:
                    merged.append(first + second)
                    j += 2
                else:
                    merged.append(word[j])
                    j += 1
            word = tuple(merged)
        if len(self._bpe_cache) < 50_000:
            self._bpe_cache[piece] = word
        return word

    def encode(self, text: str) -> list[int]:
        """把一般文字編成 token id（不含特殊 token）。

        與 `tokenizers` 不同，文字裡的 `<|...|>` 不會被當成特殊 token，
        避免前文提示裡的字串變成控制 token。
        """
        ids: list[int] = []
        for piece in pretokenize(text):
            mapped = "".join(self._byte_encoder[b] for b in piece.encode("utf-8"))
            for token in self._bpe(mapped):
                token_id = self._token_to_id.get(token)
                if token_id is None:
                    raise ValueError(f"token {token!r} is not in the vocabulary")
                ids.append(token_id)
        return ids

    def decode(self, ids: list[int]) -> str:
        """把 token id 解回文字；特殊 token（id ≥ `<|endoftext|>`）略過。"""
        text = "".join(self._id_to_token.get(i, "") for i in ids if 0 <= i < self.eot)
        data = bytes(self._byte_decoder[c] for c in text if c in self._byte_decoder)
        return data.decode("utf-8", errors="replace")
