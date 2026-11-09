"""即時字幕的穩定度指標：normalized erasure（NE）。

評測工具（不在執行時路徑上）：`experiments/` 的端到端驗收用它比較原文與譯文在即時字幕
更新過程中被「改掉」多少，量化 v0.3 G1（譯文穩定前綴）前後的閃爍程度。

定義依 Arivazhagan et al., "Re-translation versus Streaming for Simultaneous Translation"
（IWSLT 2020）與 "Re-translation Strategies for Long Form, Simultaneous, Spoken Language
Translation"（ICASSP 2020）：每次更新時，上一次顯示的文字中不屬於「與這次的最長共同前綴」
的字都被刪掉；把所有更新被刪掉的字數加總，再除以最終文字的長度。0 表示只會往後長、
從不改字；1 表示平均把整句重寫一次。
"""

from collections.abc import Sequence
from itertools import pairwise

from kikitori.pipeline.stability import common_prefix_length


def erased_characters(updates: Sequence[str]) -> int:
    """依序顯示 `updates` 時，總共被刪掉（改寫）的字元數。"""
    return sum(
        len(previous) - common_prefix_length(previous, current)
        for previous, current in pairwise(updates)
    )


def normalized_erasure(updates: Sequence[str]) -> float:
    """一句字幕的 normalized erasure。

    Args:
        updates: 同一句依時間順序顯示過的文字（即時字幕各則，最後一則通常是完整字幕）。

    Returns:
        被刪掉的字元總數 ÷ 最後一則的長度；最後一則為空時以 1 為分母（不除以零）。
        沒有任何更新時為 0。
    """
    if not updates:
        return 0.0
    return erased_characters(updates) / max(len(updates[-1]), 1)
