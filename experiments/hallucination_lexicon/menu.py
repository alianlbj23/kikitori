"""幻覺片語實測的互動選單（用標準庫 `input()`，不靠指令參數）。

角色：本實驗的入口（暫時性實驗，不屬於正式程式碼）。執行方式：

    uv run python -I experiments/hallucination_lexicon/menu.py

流程：產生非語音片段（含 VAD 判定）→ 逐模型辨識 → 彙整（候選片語、過濾前後的幻覺率）。
原始輸出在 `results/`（git 忽略）；人工整理後的片語表是同資料夾的 `lexicon.json`。
"""

import importlib.util
import io
import logging
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

from kikitori.asr import MODELS, default_cache_dir, download_model, is_downloaded
from kikitori.hardware.gpu_runtime import installed_runtime_dir

HERE = Path(__file__).resolve().parent
logger = logging.getLogger("hallucination_lexicon.menu")


def _load_sibling(name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，同資料夾模組改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(
        f"hallucination_lexicon_{name}", HERE / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


collect = _load_sibling("collect")
aggregate = _load_sibling("aggregate")
MODEL_CHOICES = ["small", "large-v3-turbo", "large-v3", "base"]
DEVICE_CHOICES = [("CUDA float16", "cuda", "float16"), ("CPU int8", "cpu", "int8")]


def ask_choices(labels: list[str], prompt: str, allow_all: bool = True) -> list[int]:
    """顯示編號選單，可輸入多個編號（以逗號分隔）或 a（全部）。回傳 0 起算的索引。"""
    while True:
        print()
        for number, label in enumerate(labels, start=1):
            print(f"  {number}. {label}")
        hint = "（可用逗號分隔多個；a = 全部）" if allow_all else ""
        raw = input(f"{prompt}{hint}：").strip().lower()
        if allow_all and raw == "a":
            return list(range(len(labels)))
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        if parts and all(p.isdecimal() and 1 <= int(p) <= len(labels) for p in parts):
            chosen = sorted({int(p) - 1 for p in parts})
            if allow_all or len(chosen) == 1:
                return chosen
        print(f"輸入錯誤：{raw!r}。")


def ask_int(prompt: str, default: int, low: int, high: int) -> int:
    """讀取範圍內的整數；直接按 Enter 用預設值。"""
    while True:
        raw = input(f"{prompt}（{low}–{high}，Enter = {default}）：").strip()
        if not raw:
            return default
        if raw.isdecimal() and low <= int(raw) <= high:
            return int(raw)
        print(f"輸入錯誤：{raw!r}。")


def build_segments() -> None:
    if not collect.MUSAN_DIR.is_dir():
        print(
            f"找不到 MUSAN：{collect.MUSAN_DIR}（先用 experiments/silero_reset/menu.py 下載）"
        )
        return
    existing = list(collect.RESULTS_DIR.glob("raw-*.jsonl"))
    if existing:
        print("注意：片段重新產生後，舊的辨識結果對不上，必須先刪除：")
        for path in existing:
            print(f"  {path}")
        if input("刪除這些檔案並繼續？(y/N)：").strip().lower() != "y":
            return
        for path in existing:
            path.unlink()
    pool = ask_int("MUSAN 每種、每個長度切幾段做 VAD 篩選", 400, 10, 2000)
    kept = ask_int("其中沒通過 VAD 的每種、每個長度保留幾段", 10, 1, pool)
    metas = collect.build_segments(pool, kept)
    passed = sum(m.vad_passed for m in metas)
    print(f"保留 {len(metas)} 段（VAD 通過 {passed} 段）：{collect.SEGMENTS_JSON}")


def run_models() -> None:
    if not collect.SEGMENTS_JSON.exists():
        print("還沒有片段，請先選「產生非語音片段」。")
        return
    cache = default_cache_dir()
    models = [MODEL_CHOICES[i] for i in ask_choices(MODEL_CHOICES, "要跑哪些模型")]
    _, device, compute_type = DEVICE_CHOICES[
        ask_choices([d[0] for d in DEVICE_CHOICES], "裝置", allow_all=False)[0]
    ]
    cuda_dir = installed_runtime_dir() if device == "cuda" else None
    if device == "cuda" and cuda_dir is None:
        print("找不到 app 安裝的 cuBLAS 12（%LOCALAPPDATA%\\kikitori\\gpu-runtime）。")
        return
    for model_id in models:
        spec = MODELS[model_id]
        if not is_downloaded(spec, cache):
            print(f"下載 {model_id}…")
            download_model(spec, cache)
        print(f"執行 {model_id}（{device} {compute_type}），已完成的片段會略過")
        try:
            collect.run_in_subprocess(model_id, device, compute_type, cuda_dir)
        except (RuntimeError, subprocess.SubprocessError) as exc:
            logger.exception("%s failed", model_id)
            print(f"{model_id} 失敗：{exc}")
    print(f"原始輸出：{collect.RESULTS_DIR}")


def summarize() -> None:
    path = aggregate.write_summary()
    print(path.read_text(encoding="utf-8"))
    print(f"彙整結果：{path}")


def export_to_engine() -> None:
    exporter = _load_sibling("export_lexicon")
    count = exporter.export()
    print(f"已把 {count} 個片語寫入 {exporter.LEXICON_PY}")


MENU: list[tuple[str, Callable[[], None] | None]] = [
    ("產生非語音片段（含 VAD 判定）", build_segments),
    ("逐模型辨識（強制 19 種語言 × 即時／完整解碼）", run_models),
    ("彙整：候選片語、過濾前後的幻覺率", summarize),
    ("把 lexicon.json 匯出成引擎的片語表", export_to_engine),
    ("離開", None),
]


def main() -> None:
    """執行選單迴圈，直到選「離開」或輸入結束。"""
    print(f"幻覺片語實測（模型快取：{default_cache_dir()}）")
    while True:
        try:
            index = ask_choices(
                [label for label, _ in MENU], "請選擇", allow_all=False
            )[0]
            label, action = MENU[index]
            if action is None:
                print("離開。")
                return
            action()
        except EOFError:
            print("\n輸入已結束，離開。")
            return
        except (OSError, ValueError, RuntimeError, KeyError) as exc:
            # 選單要能繼續使用：完整錯誤寫進 log，畫面上說明失敗原因
            logger.exception("「%s」失敗", label)
            print(f"「{label}」失敗：{exc}")


if __name__ == "__main__":
    # 輸出被導向檔案或管線時，Windows 會用系統字碼頁而無法輸出中文
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
