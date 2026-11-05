"""ASR 基準測試的互動選單（用標準庫 `input()`，不靠指令參數）。

角色：基準測試相關操作的入口（暫時性實驗，不屬於正式程式碼）。執行方式：

    uv run python -I experiments/asr_benchmark/menu.py

模型下載到使用者快取目錄（`kikitori.asr.default_cache_dir()`），不會放進 repo。
CUDA 量測需要 cuBLAS 12 的 DLL／.so 所在資料夾，選單會詢問。
"""

import importlib.util
import io
import json
import logging
import subprocess
import sys
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from types import ModuleType

from kikitori.asr import (
    MODELS,
    default_cache_dir,
    download_model,
    is_downloaded,
    model_dir,
)

HERE = Path(__file__).resolve().parent
logger = logging.getLogger("asr_benchmark.menu")
TOKENIZER_CHECK_TIMEOUT_SECS = 600


def _load_sibling(name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，同資料夾模組改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(
        f"asr_benchmark_{name}", HERE / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


benchmark = _load_sibling("benchmark")
ALL_MODELS = [*MODELS, *benchmark.EXTRA_MODELS]
DEVICE_CHOICES = [
    ("CPU int8", "cpu", "int8"),
    ("CUDA float16", "cuda", "float16"),
    ("CUDA int8_float16", "cuda", "int8_float16"),
]


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


def ask_existing_dir(prompt: str) -> Path:
    """讀取一個存在的資料夾路徑。"""
    while True:
        raw = input(f"{prompt}：").strip().strip('"')
        path = Path(raw)
        if raw and path.is_dir():
            return path
        print(f"找不到資料夾：{raw!r}")


def download_models() -> None:
    cache = default_cache_dir()
    labels = [
        f"{m}（{benchmark.resolve_spec(m).total_bytes / 1e9:.2f} GB"
        f"{'，已下載' if is_downloaded(benchmark.resolve_spec(m), cache) else ''}）"
        for m in ALL_MODELS
    ]
    for index in ask_choices(labels, "要下載哪些模型"):
        spec = benchmark.resolve_spec(ALL_MODELS[index])
        last = [0.0]

        def progress(
            done: int, total: int, model_id: str = spec.id, last: list[float] = last
        ) -> None:
            now = time.monotonic()
            if now - last[0] >= 2 or done == total:
                last[0] = now
                print(f"  {model_id}: {done / 1e6:.0f} / {total / 1e6:.0f} MB")

        path = download_model(spec, cache, progress=progress)
        print(f"{spec.id} 已就緒：{path}")


def run_benchmark() -> None:
    cache = default_cache_dir()
    ready = [m for m in ALL_MODELS if is_downloaded(benchmark.resolve_spec(m), cache)]
    if not ready:
        print("還沒有下載任何模型，請先選「下載模型」。")
        return
    models = [ready[i] for i in ask_choices(ready, "要測哪些模型")]
    devices = [
        DEVICE_CHOICES[i] for i in ask_choices([d[0] for d in DEVICE_CHOICES], "裝置")
    ]
    modes = [
        ("fast", "final")[i]
        for i in ask_choices(["即時（greedy）", "完整（beam 5）"], "模式")
    ]
    threads = 0
    if any(d[1] == "cpu" for d in devices):
        choice = ask_choices(
            ["CTranslate2 預設", "8 執行緒"], "CPU 執行緒數", allow_all=False
        )[0]
        threads = 8 if choice == 1 else 0
    cuda_dir = None
    if any(d[1] == "cuda" for d in devices):
        cuda_dir = ask_existing_dir(
            "cuBLAS 12 函式庫所在資料夾（cublas64_12.dll／libcublas.so.12）"
        )

    results_dir = benchmark.RESULTS_DIR
    results_dir.mkdir(exist_ok=True)
    out_path = results_dir / f"run-{datetime.now():%Y%m%d-%H%M%S}.json"
    results = []
    print(benchmark.SUMMARY_HEADER)
    for model_id in models:
        for _, device, compute_type in devices:
            for mode in modes:
                config = benchmark.RunConfig(
                    model_id,
                    device,
                    compute_type,
                    mode,
                    threads if device == "cpu" else 0,
                )
                try:
                    result = benchmark.run_in_subprocess(config, cache, cuda_dir)
                except (RuntimeError, subprocess.SubprocessError) as exc:
                    logger.exception("%s failed", config.label)
                    print(f"| {config.label} | 失敗：{str(exc).splitlines()[0]} |")
                    continue
                results.append(result)
                print(benchmark.summary_row(result))
                out_path.write_text(
                    json.dumps(results, ensure_ascii=False, indent=1), "utf-8"
                )
    print(f"詳細結果：{out_path}")


def check_tokenizer() -> None:
    cache = default_cache_dir()
    paths = [
        str(model_dir(benchmark.resolve_spec(m), cache) / "tokenizer.json")
        for m in ALL_MODELS
        if is_downloaded(benchmark.resolve_spec(m), cache)
    ]
    # 不同模型共用同一份分詞器時只比一次
    unique = list(dict.fromkeys(p for p in paths if Path(p).exists()))
    if not unique:
        print("還沒有下載任何模型。")
        return
    command = [
        "uv",
        "run",
        "--isolated",
        "--no-project",
        "--with",
        "tokenizers==0.23.3",
        "--with",
        "numpy==2.5.4",
        "python",
        "-I",
        str(HERE / "check_tokenizer.py"),
        str(HERE.parent.parent / "src"),
        *unique,
    ]
    proc = subprocess.run(command, timeout=TOKENIZER_CHECK_TIMEOUT_SECS, check=False)
    print("一致" if proc.returncode == 0 else f"有不一致（exit {proc.returncode}）")


def show_summary() -> None:
    results = benchmark.collect_results()
    if not results:
        print("還沒有結果，請先執行基準測試。")
        return
    print(benchmark.SUMMARY_HEADER)
    for result in results:
        print(benchmark.summary_row(result))


MENU: list[tuple[str, Callable[[], None] | None]] = [
    ("下載模型", download_models),
    ("執行基準測試", run_benchmark),
    ("比對分詞器（與 HF tokenizers）", check_tokenizer),
    ("彙整所有結果（以目前的過濾規則重新判定）", show_summary),
    ("離開", None),
]


def main() -> None:
    """執行選單迴圈，直到選「離開」或輸入結束。"""
    print(f"ASR 基準測試（模型快取：{default_cache_dir()}）")
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
    # 輸出被導向檔案或管線時，Windows 會用系統字碼頁（例如 cp932）而無法輸出中文
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.INFO, format="%(levelname)s %(name)s: %(message)s"
    )
    main()
