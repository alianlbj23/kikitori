"""`kikitori` 指令：`kikitori engine` 給 Tauri 啟動引擎；不帶參數時顯示互動選單。

位於套件的進入點層。依使用者規則，給人用的工具做成編號選單，不靠記指令參數；
`engine` 子指令只給程式（Tauri）呼叫。
"""

import io
import sys
from collections.abc import Callable, Sequence

USAGE = "usage: kikitori [engine]\n  (no arguments: interactive menu)\n"

MENU = """
kikitori
  1) 啟動引擎（stdio NDJSON，給 Tauri 或手動測試）
  2) 列出音訊裝置
  3) 顯示設定檔路徑
  4) 離開
"""


def _run_engine() -> int:
    from kikitori.engine import run_engine

    return run_engine()


def _list_devices(out: Callable[[str], None]) -> None:
    from kikitori.capture import CaptureError, list_devices

    try:
        devices = list_devices()
    except CaptureError as exc:
        out(f"無法列出裝置：{exc}")
        return
    if not devices:
        out("沒有找到任何裝置。")
    for d in devices:
        default = "（預設）" if d.is_default else ""
        out(f"  [{d.kind}] {d.name}{default}\n      id: {d.id}")


def _show_settings_path(out: Callable[[str], None]) -> None:
    from kikitori.config import default_settings_path

    path = default_settings_path()
    state = "已存在" if path.exists() else "尚未建立，使用預設值"
    out(f"{path}（{state}）")


def menu(
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
    run_engine: Callable[[], int] = _run_engine,
) -> int:
    """互動選單；回傳結束碼。輸入結束（EOF）視同離開。"""
    while True:
        out(MENU)
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            out("")
            return 0
        if choice == "1":
            out("引擎啟動中：每行輸入一個 JSON 請求，Ctrl+Z/Ctrl+D 結束。")
            return run_engine()
        if choice == "2":
            _list_devices(out)
        elif choice == "3":
            _show_settings_path(out)
        elif choice == "4":
            return 0
        else:
            out(f"無效的選項：{choice!r}")


def _use_utf8_stdout() -> None:
    """選單一律以 UTF-8 輸出。

    stdout 不是 Windows 主控台時（Git Bash／mintty、管線），Python 用系統地區編碼：
    日文 Windows 的 cp932 沒有「啟」，選單一印就 UnicodeEncodeError；裝置名稱「耳機」
    在 UTF-8 終端機上則變成亂碼。真正的主控台本來就是 UTF-8，不受影響。
    """
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")


def main(argv: Sequence[str] | None = None) -> int:
    """解析參數並執行；回傳結束碼。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if args == ["engine"]:
        return _run_engine()
    if args:
        sys.stderr.write(USAGE)
        return 2
    _use_utf8_stdout()
    return menu()
