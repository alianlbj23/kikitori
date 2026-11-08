"""日文原文字幕的振假名：對齊演算法、Sudachi 分析器與延後下載的辭典。

位於 pipeline 之外、由 engine 直接使用：引擎在送出 `subtitle` 事件前，對原文是日文的字幕
算出 `ruby`（見 `docs/specs/furigana.md`）。`ruby.py` 是不依賴 Sudachi 的純函式；
`analyzer.py` 延遲匯入 sudachipy；`dictionary.py` 負責辭典的下載、驗證、刪除。
"""

from kikitori.furigana.ruby import RubySpan, Token, align, ruby_for_tokens

__all__ = ["RubySpan", "Token", "align", "ruby_for_tokens"]
