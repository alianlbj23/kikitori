"""kikitori 記憶體與資源釋放壓力測試的互動選單。

暫時性實驗，不屬於正式程式碼。選單列出情境 1–7，選好後逐項詢問參數（直接按 Enter
用預設值），再執行並印出 PASS／FAIL 與數字；詳細結果寫到 `results/<時間>-<情境>/`（git 忽略）。

執行（互動選單）：uv run python -X utf8 experiments/memory_soak/menu.py
非互動（給自動化）：uv run python -X utf8 experiments/memory_soak/menu.py --batch <情境> [參數=值 ...]
  例如 --batch start-stop cycles=12、--batch long minutes=5
"""

from __future__ import annotations

import inspect
import logging
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scenarios import SCENARIOS

logger = logging.getLogger("soak.menu")


def _gui(minutes: float = 15.0) -> int:
    # 延遲匯入：GUI 驗收的模組在匯入時就宣告 DPI 感知，只在需要時載入
    from gui_soak import scenario_gui

    return scenario_gui(minutes=minutes)


ALL: dict[str, Callable[..., int]] = {**SCENARIOS, "gui": _gui}

ENTRIES: list[tuple[str, str, dict[str, Any]]] = [
    ("start-stop", "1) start/stop × N（media、播放語音；ASR 每次載入／釋放）", {}),
    ("switch", "2) 每個 session 換語言與模型組合", {}),
    ("translation", "3) 執行中切換翻譯、只顯示原文、保溫與 Ollama 到期", {}),
    ("long", "4) 長時間 session（人聲＋BGM 循環）", {}),
    ("downloads", "5) 模型下載：取消／續傳／重複；翻譯模型下載 × N", {}),
    ("errors", "6) 錯誤路徑與引擎重啟（不留下行程）", {}),
    ("gui", "7) 桌面程式長時間執行（WebDriver；需要 tauri-driver 與建置）", {}),
    ("quick", "8) 快速版：1、2、3、6（中等規模）＋ 4（5 分鐘）", {}),
]

QUICK: list[tuple[str, dict[str, Any]]] = [
    ("start-stop", {"cycles": 12}),
    ("switch", {"rounds": 2}),
    ("translation", {"toggles": 10}),
    ("errors", {"rounds": 2}),
    ("long", {"minutes": 5.0}),
]


def _convert(value: str, default: Any) -> Any:
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "y")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def _ask_params(func: Callable[..., int]) -> dict[str, Any]:
    """逐項詢問參數；Enter 用預設值。"""
    params: dict[str, Any] = {}
    for name, p in inspect.signature(func).parameters.items():
        if p.default is inspect.Parameter.empty:
            continue
        while True:
            try:
                raw = input(f"  {name}（預設 {p.default}）：").strip()
            except EOFError:
                raw = ""
            if not raw:
                break
            try:
                params[name] = _convert(raw, p.default)
                break
            except ValueError:
                print(f"  不是有效的值：{raw!r}")
    return params


def run_quick() -> int:
    worst = 0
    for name, params in QUICK:
        print(f"\n=== {name} {params} ===", flush=True)
        try:
            code = ALL[name](**params)
        except Exception:
            logger.exception("scenario %s aborted", name)
            code = 1
        worst = max(worst, code)
    return worst


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if argv and argv[0] == "--batch":
        if len(argv) < 2 or argv[1] not in (*ALL, "quick"):
            print(
                f"usage: menu.py --batch {'|'.join((*ALL, 'quick'))} [name=value ...]",
                file=sys.stderr,
            )
            return 2
        if argv[1] == "quick":
            return run_quick()
        func = ALL[argv[1]]
        sig = inspect.signature(func).parameters
        params: dict[str, Any] = {}
        for item in argv[2:]:
            key, _, raw = item.partition("=")
            if key not in sig or not raw:
                print(
                    f"unknown or empty parameter {item!r}; available: {list(sig)}",
                    file=sys.stderr,
                )
                return 2
            params[key] = _convert(raw, sig[key].default)
        return func(**params)
    if argv:
        print("usage: menu.py [--batch <scenario> [name=value ...]]", file=sys.stderr)
        return 2
    while True:
        print("\nkikitori 記憶體與資源釋放壓力測試")
        for _, label, _ in ENTRIES:
            print(f"  {label}")
        print("  9) 離開")
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice == "9":
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(ENTRIES):
            print(f"無效的選項：{choice!r}")
            continue
        name = ENTRIES[int(choice) - 1][0]
        if name == "quick":
            return run_quick()
        func = ALL[name]
        return func(**_ask_params(func))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
