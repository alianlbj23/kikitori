"""kikitori：即時語音字幕與口譯（Python 引擎）。

`main()` 是 `kikitori` 指令的進入點，邏輯在 `kikitori.cli`。
"""


def main() -> int:
    """`kikitori` 指令：`kikitori engine` 啟動引擎，不帶參數顯示互動選單。"""
    from kikitori.cli import main as cli_main

    return cli_main()
