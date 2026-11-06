"""Sudachi 形態素分析器：日文原文 → 振假名（`ruby.py` 的 `RubySpan`）。

位於 furigana 模組的分析層，由引擎的 `FuriganaService` 在需要時建立、不需要時 `close()`。
sudachipy 只在建立分析器時才匯入（沒開振假名的使用者完全不載入 Rust 擴充與辭典）。

辭典以 mmap 開啟（sudachi.rs 的 `Storage::File`），`close()` 之後釋放對應；實測見
`docs/specs/furigana.md`「記憶體」。分析器用 C 單位（整個複合詞）取讀音，再以 A 單位逐詞
標註（讀音接得起來時，見 `ruby.Token.parts`）：C 單位的讀音含連濁等整詞資訊，比較準。
"""

import logging
import threading
from pathlib import Path
from typing import Any

from kikitori.furigana.ruby import RubySpan, Token, ruby_for_tokens

logger = logging.getLogger(__name__)

MAX_TEXT_CHARS = 2000
"""超過這個長度的原文不分析（字幕一句不會這麼長；避免異常輸入讓分析拖住字幕送出）。"""


class FuriganaAnalyzer:
    """以 Sudachi 辭典產生振假名。執行緒安全（分析與關閉共用一把鎖）。"""

    def __init__(self, dictionary_path: Path) -> None:
        """開啟辭典（約 0.1–0.5 秒）。

        Args:
            dictionary_path: Sudachi 的 `system_*.dic`（絕對路徑）。

        Raises:
            ImportError: sudachipy 無法載入。
            FileNotFoundError: 辭典不存在。
            RuntimeError: sudachipy 無法開啟辭典（格式或版本不符，`SudachiError`）。
        """
        if not dictionary_path.is_absolute():
            raise ValueError(f"dictionary path must be absolute: {dictionary_path}")
        if not dictionary_path.is_file():
            raise FileNotFoundError(f"Sudachi dictionary not found: {dictionary_path}")
        from sudachipy import Dictionary, SplitMode

        self._split_a: Any = SplitMode.A
        self._lock = threading.Lock()
        try:
            # dict= 絕對路徑：用這個系統辭典，其餘設定（char.def 等）用 sudachipy 內附的預設值
            self._dictionary: Any = Dictionary(dict=str(dictionary_path))
        except Exception as exc:
            raise RuntimeError(
                f"cannot open Sudachi dictionary {dictionary_path}: {exc}"
            ) from exc
        try:
            self._tokenizer: Any = self._dictionary.create(SplitMode.C)
        except Exception as exc:
            # 辭典已映射：不能等垃圾回收才解除
            self._dictionary.close()
            raise RuntimeError(
                f"cannot create a Sudachi tokenizer for {dictionary_path}: {exc}"
            ) from exc
        self._closed = False

    def tokens(self, text: str) -> list[Token]:
        """分析 `text`，回傳 C 單位的詞（含 A 單位的切分）。關閉後回傳空串列。"""
        with self._lock:
            if self._closed:
                return []
            result: list[Token] = []
            for m in self._tokenizer.tokenize(text):
                parts = m.split(self._split_a)
                result.append(
                    Token(
                        m.surface(),
                        m.reading_form(),
                        tuple(Token(p.surface(), p.reading_form()) for p in parts)
                        if len(parts) > 1
                        else (),
                    )
                )
            return result

    def ruby(self, text: str) -> list[RubySpan]:
        """`text` 的振假名；空字串、太長或已關閉時回傳空串列。

        Raises:
            RuntimeError: Sudachi 分析失敗（`SudachiError`）。
        """
        if not text:
            return []
        if len(text) > MAX_TEXT_CHARS:
            logger.warning(
                "not adding furigana to a %d-character subtitle (limit %d)",
                len(text),
                MAX_TEXT_CHARS,
            )
            return []
        try:
            tokens = self.tokens(text)
        except Exception as exc:
            raise RuntimeError(f"Sudachi failed to analyze text: {exc}") from exc
        return ruby_for_tokens(text, tokens)

    @property
    def closed(self) -> bool:
        """是否已關閉。"""
        return self._closed

    def close(self) -> None:
        """釋放辭典（解除 mmap）。可重複呼叫；之後 `ruby()` 回傳空串列。"""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            tokenizer, dictionary = self._tokenizer, self._dictionary
            self._tokenizer = self._dictionary = None
        del tokenizer
        try:
            dictionary.close()
        except Exception:
            # 關閉失敗只影響記憶體何時歸還（物件被回收時也會釋放），不影響字幕
            logger.exception("closing the Sudachi dictionary failed")
