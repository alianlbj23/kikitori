"""Silero 重置策略評測的互動選單（用標準庫 `input()`，不靠指令參數）。

角色：評測相關操作的入口（暫時性實驗，不屬於正式程式碼）。執行方式：

    uv run python -I experiments/silero_reset/menu.py

`-I` 讓目前資料夾與腳本資料夾都不在 `sys.path`，避免讀取下載檔案時載入
同名的惡意模組；同資料夾的 `evaluate.py` 因此改用檔案路徑載入。
"""

import importlib.util
import io
import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
logger = logging.getLogger("silero_reset.menu")


def _load_sibling(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        f"silero_reset_{name}", HERE / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = _load_sibling("evaluate")
musan = _load_sibling("musan")


@dataclass(frozen=True)
class Context:
    """選單動作需要的設定；測試時換成暫存資料夾與本機假伺服器。"""

    paths: Any
    musan_source: Any


def download_musan(ctx: Context) -> None:
    musan.download_menu(ctx.paths.data_dir, ctx.musan_source)


def generate_tracks(ctx: Context) -> None:
    print("產生評測音軌…")
    evaluate.generate_tracks(ctx.paths)


def run_evaluation(ctx: Context) -> None:
    print("執行評測（三種重置策略 + 前文對照）…")
    evaluate.run_evaluation(ctx.paths)


MENU: list[tuple[str, Callable[[Context], None] | None]] = [
    ("下載 MUSAN", download_musan),
    ("產生評測音軌", generate_tracks),
    ("執行評測", run_evaluation),
    ("離開", None),
]


def ask_choice(labels: list[str]) -> int:
    """顯示編號選單並讀取選擇；輸入錯誤時重新詢問。回傳 0 起算的索引。"""
    while True:
        print()
        for number, label in enumerate(labels, start=1):
            print(f"  {number}. {label}")
        raw = input("請輸入編號：").strip()
        if raw.isdecimal() and 1 <= int(raw) <= len(labels):
            return int(raw) - 1
        print(f"輸入錯誤：{raw!r}。請輸入 1–{len(labels)} 的編號。")


def main(paths: Any = None, musan_source: Any = None) -> None:
    """執行選單迴圈，直到選「離開」或輸入結束。

    Args:
        paths: `evaluate.Paths`；省略時用預設的本機資料夾。
        musan_source: `musan.MusanSource`；省略時用 OpenSLR 官方網址。
    """
    ctx = Context(paths or evaluate.Paths(), musan_source or musan.MusanSource())
    print("Silero VAD 重置策略評測")
    while True:
        try:
            label, action = MENU[ask_choice([label for label, _ in MENU])]
        except EOFError:
            print("\n輸入已結束，離開。")
            return
        if action is None:
            print("離開。")
            return
        try:
            action(ctx)
        except EOFError:
            print("\n輸入已結束，離開。")
            return
        except (OSError, ValueError, RuntimeError) as exc:
            # 選單要能繼續使用：完整錯誤寫進 log，畫面上說明失敗原因。
            logger.exception("「%s」失敗", label)
            print(f"「{label}」失敗：{exc}")


if __name__ == "__main__":
    # 輸出被導向檔案或管線時，Windows 會用系統字碼頁（例如 cp932）而無法輸出中文。
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
