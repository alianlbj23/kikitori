"""VTuber 直播 ASR 評測的互動選單（用標準庫 `input()`，不靠指令參數）。

角色：vtuber_eval 各步驟的入口（暫時性實驗，不屬於正式程式碼）。執行方式：

    uv run python -I experiments/vtuber_eval/menu.py

步驟：1 準備資料（隔離環境）→ 2 建立評測集 → 3 執行評測 → 4 計分與統計。
語料、混音與結果都在 git 忽略的 `data/`、`results/`；模型在使用者快取目錄。
"""

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
PREPARE_TIMEOUT_SECS = 3 * 3600
BUILD_TIMEOUT_SECS = 3 * 3600
# 隔離環境的固定版本（只用於第 1 步，不加入專案依賴）
ISOLATED_DEPS = ("pyarrow==26.0.0", "soundfile==0.14.0", "numpy==2.5.4")

GPU_MODELS = ("small", "medium", "large-v3-turbo", "large-v3", "kotoba-whisper-v2.0")
CPU_MODELS = ("base", "small")


def _load_sibling(name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，同資料夾模組改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(
        f"vtuber_eval_{name}", HERE / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


evaluate = _load_sibling("evaluate")


def ask_choices(labels: list[str], prompt: str, allow_all: bool = True) -> list[int]:
    """顯示編號選單，可輸入多個編號（逗號分隔）或 a（全部）。回傳 0 起算的索引。"""
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


def default_cublas_dir() -> Path | None:
    """kikitori 下載的 GPU 執行環境（%LOCALAPPDATA%\\kikitori\\gpu-runtime\\cublas-*）。"""
    base = os.environ.get("LOCALAPPDATA")
    if not base:
        return None
    found = sorted((Path(base) / "kikitori" / "gpu-runtime").glob("cublas-*"))
    return found[-1] if found else None


def ask_cublas_dir() -> Path:
    """詢問 cuBLAS 12 資料夾；直接按 Enter 用偵測到的預設值。"""
    default = default_cublas_dir()
    while True:
        hint = f"（Enter = {default}）" if default else ""
        raw = input(f"cuBLAS 12 函式庫所在資料夾{hint}：").strip().strip('"')
        if not raw and default is not None:
            return default
        if raw and Path(raw).is_dir():
            return Path(raw)
        print(f"找不到資料夾：{raw!r}")


def _env() -> dict[str, str]:
    return {**os.environ, "PYTHONIOENCODING": "utf-8"}


def prepare_data() -> None:
    """第 1 步：在隔離環境下載語料並切成 WAV（不改動專案依賴）。"""
    uv = shutil.which("uv")
    if uv is None:
        print("找不到 uv。")
        return
    command = [uv, "run", "--isolated", "--no-project"]
    for dep in ISOLATED_DEPS:
        command += ["--with", dep]
    command += ["python", "-I", str(HERE / "prepare.py"), "--worker"]
    proc = subprocess.run(
        command, timeout=PREPARE_TIMEOUT_SECS, check=False, env=_env(), cwd=ROOT
    )
    print("完成。" if proc.returncode == 0 else f"失敗（exit {proc.returncode}）。")


def build_sets() -> None:
    """第 2 步：影片層級語言檢查、抽樣與 MUSAN 混音。"""
    device = ("cuda", "cpu")[
        ask_choices(["CUDA", "CPU"], "語言檢查（Whisper medium）用的裝置", False)[0]
    ]
    cuda_dirs = [str(ask_cublas_dir())] if device == "cuda" else []
    lid = HERE / "data" / "lid.json"
    if lid.exists():
        print(f"沿用已有的語言檢查結果 {lid}（要重做請先手動刪除）。")
    proc = subprocess.run(
        [sys.executable, "-I", str(HERE / "build_sets.py"), "--worker"],
        input=json.dumps({"cuda_dirs": cuda_dirs, "device": device}),
        text=True,
        encoding="utf-8",
        timeout=BUILD_TIMEOUT_SECS,
        check=False,
        env=_env(),
    )
    print("完成。" if proc.returncode == 0 else f"失敗（exit {proc.returncode}）。")


def _plan_gpu(compute_type: str) -> list[Any]:
    return [
        evaluate.RunConfig(model, "cuda", compute_type, mode, "full")
        for mode in ("fast", "final")
        for model in GPU_MODELS
    ]


def _plan_cpu() -> list[Any]:
    return [
        evaluate.RunConfig(model, "cpu", "int8", mode, "cpu", 8)
        for model in CPU_MODELS
        for mode in ("fast", "final")
    ]


def run_evaluation() -> None:
    """第 3 步：選擇評測計畫，逐一在子行程執行（可中斷後續跑）。"""
    plans: list[tuple[str, Callable[[], list[Any]]]] = [
        ("GPU 全集：5 模型 × fast／final，CUDA float16", lambda: _plan_gpu("float16")),
        (
            "GPU 全集：5 模型 × fast／final，CUDA int8_float16（VRAM 不足時）",
            lambda: _plan_gpu("int8_float16"),
        ),
        ("CPU 子集：base、small × fast／final，int8、8 執行緒", _plan_cpu),
    ]
    chosen = ask_choices([p[0] for p in plans], "要跑哪些計畫")
    configs = [c for i in chosen for c in plans[i][1]()]
    picked = ask_choices([c.label for c in configs], "要跑哪些設定")
    configs = [configs[i] for i in picked]
    needs_cuda = any(c.device == "cuda" for c in configs)
    cuda_dir = ask_cublas_dir() if needs_cuda else None
    failed = []
    for config in configs:
        print(f"\n=== {config.label} ===", flush=True)
        try:
            evaluate.run_in_subprocess(config, cuda_dir)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            print(exc)
            failed.append(config.label)
    print("全部完成。" if not failed else f"失敗的設定：{failed}")


def analyze_results() -> None:
    """第 4 步：計分、bootstrap、寫 results/summary.md。"""
    analyze = _load_sibling("analyze")
    print(analyze.analyze())
    print(f"\n已寫入 {HERE / 'results' / 'summary.md'}")


def main() -> None:
    """主選單迴圈。"""
    actions: list[tuple[str, Callable[[], None]]] = [
        ("準備資料（下載 YODAS／FLEURS／ReazonSpeech，隔離環境）", prepare_data),
        ("建立評測集（語言檢查、抽樣、MUSAN 混音）", build_sets),
        ("執行評測", run_evaluation),
        ("計分與統計", analyze_results),
        ("離開", lambda: None),
    ]
    while True:
        index = ask_choices([a[0] for a in actions], "要做什麼", allow_all=False)[0]
        if index == len(actions) - 1:
            return
        actions[index][1]()


if __name__ == "__main__":
    # `-I` 會忽略 PYTHONIOENCODING；Windows 主控台預設編碼印不出中文，所以明確改成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    main()
