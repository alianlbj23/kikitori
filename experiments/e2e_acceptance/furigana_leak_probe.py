"""振假名辭典開／關循環的私有記憶體探針（找出 session 間私有記憶體成長的來源）。

暫時性實驗，不屬於正式程式碼（`tasks/acceptance-report-v0.2.0.md` 第 7 節）。不需要 GPU、聲音或引擎：
以 `kikitori.furigana.analyzer.FuriganaAnalyzer` 直接開關使用者已下載的辭典（只讀、mmap），每個循環分析
`data/final_ja.json` 的 12 句真值與它們的每個前綴（模擬即時字幕每次重新分析整句），然後關閉，量行程私有記憶體。

模式：
- `main`：每個循環都在主執行緒開、分析、關（`docs/specs/furigana.md` 量測的方式）。
- `thread`：每個循環在一條新的執行緒上開、分析、關，執行緒結束（引擎的方式：每個 session 的送字幕執行緒是新的）。
- `thread-open-only`：同 `thread`，但不分析，只開關。
- `analyze-only`：只開一次（主執行緒），每個循環在新的執行緒上分析，不關。

執行：
  uv run python -X utf8 experiments/e2e_acceptance/furigana_leak_probe.py                 互動選單
  uv run python -X utf8 experiments/e2e_acceptance/furigana_leak_probe.py --batch thread cycles=10
"""

from __future__ import annotations

import ctypes
import gc
import itertools
import json
import os
import statistics
import sys
import threading
import time
from collections.abc import Callable
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
META = HERE / "data" / "final_ja.json"
MIB = 1024 * 1024


class PROCESS_MEMORY_COUNTERS_EX(ctypes.Structure):
    _fields_ = [
        ("cb", wintypes.DWORD),
        ("PageFaultCount", wintypes.DWORD),
        ("PeakWorkingSetSize", ctypes.c_size_t),
        ("WorkingSetSize", ctypes.c_size_t),
        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPagedPoolUsage", ctypes.c_size_t),
        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
        ("PagefileUsage", ctypes.c_size_t),
        ("PeakPagefileUsage", ctypes.c_size_t),
        ("PrivateUsage", ctypes.c_size_t),
    ]


def private_mib() -> float:
    """這個行程的私有記憶體（MiB）。"""
    counters = PROCESS_MEMORY_COUNTERS_EX()
    counters.cb = ctypes.sizeof(counters)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.K32GetProcessMemoryInfo.argtypes = [
        wintypes.HANDLE,
        ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
        wintypes.DWORD,
    ]
    if not kernel32.K32GetProcessMemoryInfo(
        kernel32.GetCurrentProcess(), ctypes.byref(counters), counters.cb
    ):
        raise ctypes.WinError(ctypes.get_last_error())
    return counters.PrivateUsage / MIB


def texts() -> list[str]:
    """12 句真值與每句的所有前綴（即時字幕每次重新分析整句）。"""
    refs = [s["reference"] for s in json.loads(META.read_text("utf-8"))["segments"]]
    out: list[str] = []
    for r in refs:
        out.extend(r[:n] for n in range(2, len(r) + 1, 2))
        out.append(r)
    return out


def dictionary_path() -> Path:
    from kikitori.furigana.dictionary import default_root, is_installed
    from kikitori.furigana.dictionary import dictionary_path as path_of

    root = default_root()
    if not is_installed(root):
        raise SystemExit(
            f"no furigana dictionary under {root}; download it in the app first"
        )
    return path_of(root)


def run(mode: str, cycles: int) -> int:
    from kikitori.furigana.analyzer import FuriganaAnalyzer

    path = dictionary_path()
    sentences = texts()
    print(
        f"mode {mode}, {cycles} cycles, {len(sentences)} analyses per cycle, dictionary {path}"
    )
    shared: list[FuriganaAnalyzer] = []
    if mode == "analyze-only":
        shared.append(FuriganaAnalyzer(path))

    def cycle() -> None:
        if mode == "analyze-only":
            for t in sentences:
                shared[0].ruby(t)
            return
        analyzer = FuriganaAnalyzer(path)
        try:
            if mode != "thread-open-only":
                for t in sentences:
                    analyzer.ruby(t)
        finally:
            analyzer.close()

    def in_thread(fn: Callable[[], None]) -> None:
        th = threading.Thread(target=fn, name="probe-cycle")
        th.start()
        th.join()

    # sudachipy 模組本身（第一次匯入）不算：先匯入、量基準
    import sudachipy  # noqa: F401

    gc.collect()
    base = private_mib()
    series = [round(base, 1)]
    for i in range(cycles):
        t = time.perf_counter()
        if mode == "main":
            cycle()
        else:
            in_thread(cycle)
        gc.collect()
        time.sleep(0.5)
        series.append(round(private_mib(), 1))
        print(
            f"  cycle {i + 1}: {series[-1]:.1f} MiB ({time.perf_counter() - t:.2f} s)",
            flush=True,
        )
    for a in shared:
        a.close()
    deltas = [b - a for a, b in itertools.pairwise(series)]
    tail = deltas[len(deltas) // 2 :]
    print(
        f"{mode}: start {series[0]} → end {series[-1]} MiB (+{series[-1] - series[0]:.1f}); "
        f"per cycle median {statistics.median(deltas):.2f}, second half median {statistics.median(tail):.2f}"
    )
    print(json.dumps({"mode": mode, "pid": os.getpid(), "series": series}))
    return 0


MODES = ("main", "thread", "thread-open-only", "analyze-only")


def main(argv: list[str]) -> int:
    if argv[:1] == ["--batch"] and len(argv) >= 2 and argv[1] in MODES:
        cycles = 10
        for a in argv[2:]:
            key, _, value = a.partition("=")
            if key != "cycles":
                raise SystemExit(f"unknown parameter {key}")
            cycles = int(value)
        return run(argv[1], cycles)
    for i, m in enumerate(MODES, 1):
        print(f"  {i}) {m}")
    choice = input("mode number: ").strip()
    if not choice.isdigit() or not 1 <= int(choice) <= len(MODES):
        print("invalid choice")
        return 2
    cycles = input("cycles [10]: ").strip() or "10"
    return run(MODES[int(choice) - 1], int(cycles))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
