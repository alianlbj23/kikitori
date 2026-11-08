"""記憶體與資源釋放壓力測試的取樣器：每秒記錄一次行程樹的記憶體、handle、執行緒與顯示記憶體。

暫時性實驗，不屬於正式程式碼。只用標準庫（ctypes 呼叫 Win32 API）與 Windows 內建的
PowerShell（`Get-Counter`）、`nvidia-smi`。

為什麼每項指標用這個來源：
- 私有記憶體（Private Bytes＝`PrivateUsage`）、工作集、handle 數：`K32GetProcessMemoryInfo`、
  `GetProcessHandleCount`，只需 `PROCESS_QUERY_LIMITED_INFORMATION`，不會干擾被測行程。
- 執行緒數與父行程：`CreateToolhelp32Snapshot` 一次拿到所有行程，也用來找行程樹的子孫
  （開發版引擎是 venv 的 python 啟動器 → 真正的 python，兩層都算在引擎裡）。
- 每個行程的顯示記憶體：WDDM 下 `nvidia-smi` 拿不到每個行程的用量，只能用效能計數器
  `\\GPU Process Memory(pid_<PID>_*)\\Dedicated Usage`（另記 `Shared Usage`：顯示記憶體
  不夠時 CUDA 會溢出到共用記憶體，只看 dedicated 會誤以為釋放了）。`Get-Counter` 一次要
  約 1 秒，所以用一個常駐的 PowerShell 迴圈持續輸出 JSON，由背景執行緒讀取。
- 整張顯示卡的用量（給背景參考）：`nvidia-smi`，每 5 秒一次。
- Ollama：`ollama.exe`／`llama-server.exe` 行程的記憶體與顯示記憶體，加上 `/api/ps` 列出的
  已載入模型與到期時間（看保溫與卸載是否如預期）。
"""

from __future__ import annotations

import ctypes
import json
import logging
import subprocess
import threading
import time
import urllib.request
from collections.abc import Iterable
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self, TextIO

logger = logging.getLogger("soak.monitor")

MIB = 1024 * 1024

# --------------------------------------------------------------------------- Win32

_kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

TH32CS_SNAPPROCESS = 0x2
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
INVALID_HANDLE_VALUE = ctypes.c_void_p(-1).value


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_size_t),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * 260),
    ]


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


_kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
_kernel32.CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
_kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
_kernel32.OpenProcess.restype = wintypes.HANDLE
_kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
_kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
_kernel32.K32GetProcessMemoryInfo.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(PROCESS_MEMORY_COUNTERS_EX),
    wintypes.DWORD,
]
_kernel32.GetProcessHandleCount.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.DWORD),
]
_kernel32.GetProcessTimes.argtypes = [
    wintypes.HANDLE,
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
    ctypes.POINTER(wintypes.FILETIME),
]


@dataclass(frozen=True)
class ProcEntry:
    pid: int
    ppid: int
    name: str
    threads: int


def snapshot() -> dict[int, ProcEntry]:
    """目前所有行程（pid → 父行程、執行檔名稱、執行緒數）。"""
    handle = _kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if handle in (None, INVALID_HANDLE_VALUE):
        raise ctypes.WinError(ctypes.get_last_error())
    out: dict[int, ProcEntry] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = _kernel32.Process32FirstW(handle, ctypes.byref(entry))
        while ok:
            out[int(entry.th32ProcessID)] = ProcEntry(
                int(entry.th32ProcessID),
                int(entry.th32ParentProcessID),
                str(entry.szExeFile),
                int(entry.cntThreads),
            )
            ok = _kernel32.Process32NextW(handle, ctypes.byref(entry))
    finally:
        _kernel32.CloseHandle(handle)
    return out


@dataclass
class ProcStats:
    pid: int
    name: str
    threads: int
    private: int | None = None
    working_set: int | None = None
    handles: int | None = None
    created: int | None = None  # FILETIME（100 ns），判斷 pid 是否被重用


def process_stats(entry: ProcEntry) -> ProcStats:
    """一個行程的記憶體與 handle 數；行程已結束或無權限時只有執行緒數。"""
    stats = ProcStats(entry.pid, entry.name, entry.threads)
    h = _kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, entry.pid)
    if not h:
        return stats
    try:
        mem = PROCESS_MEMORY_COUNTERS_EX()
        mem.cb = ctypes.sizeof(mem)
        if _kernel32.K32GetProcessMemoryInfo(h, ctypes.byref(mem), mem.cb):
            stats.private = int(mem.PrivateUsage)
            stats.working_set = int(mem.WorkingSetSize)
        count = wintypes.DWORD()
        if _kernel32.GetProcessHandleCount(h, ctypes.byref(count)):
            stats.handles = int(count.value)
        created, exited, kernel, user = (wintypes.FILETIME() for _ in range(4))
        if _kernel32.GetProcessTimes(
            h,
            ctypes.byref(created),
            ctypes.byref(exited),
            ctypes.byref(kernel),
            ctypes.byref(user),
        ):
            stats.created = (created.dwHighDateTime << 32) | created.dwLowDateTime
    finally:
        _kernel32.CloseHandle(h)
    return stats


def descendants(procs: dict[int, ProcEntry], root: int) -> list[int]:
    """root 與它所有子孫的 pid（root 已結束時仍會找出父 pid 是它的孤兒）。"""
    children: dict[int, list[int]] = {}
    for p in procs.values():
        children.setdefault(p.ppid, []).append(p.pid)
    out: list[int] = []
    stack = [root]
    seen: set[int] = set()
    while stack:
        pid = stack.pop()
        if pid in seen:
            continue
        seen.add(pid)
        if pid in procs:
            out.append(pid)
        stack.extend(c for c in children.get(pid, []) if c != pid)
    return out


# --------------------------------------------------------------------------- GPU 計數器

GPU_COUNTER_PS = r"""
$ErrorActionPreference = 'SilentlyContinue'
$paths = @('\GPU Process Memory(*)\Dedicated Usage', '\GPU Process Memory(*)\Shared Usage', '\GPU Adapter Memory(*)\Dedicated Usage')
while ($true) {
  $set = Get-Counter -Counter $paths -ErrorAction SilentlyContinue
  if ($null -eq $set) { Start-Sleep -Milliseconds 500; continue }
  $ded = @{}; $sh = @{}; $adapter = [int64]0
  foreach ($x in $set.CounterSamples) {
    $path = $x.Path.ToLowerInvariant()
    $v = [int64]$x.CookedValue
    if ($path -like '*gpu adapter memory*') { $adapter += $v; continue }
    if ($x.InstanceName -match '^pid_(\d+)_') {
      $p = $Matches[1]
      if ($path.EndsWith('\dedicated usage')) { $ded[$p] = [int64]$ded[$p] + $v } else { $sh[$p] = [int64]$sh[$p] + $v }
    }
  }
  $line = @{ ded = $ded; shared = $sh; adapter = $adapter } | ConvertTo-Json -Compress
  [Console]::Out.WriteLine($line)
  [Console]::Out.Flush()
}
"""


@dataclass
class GpuSample:
    t: float
    dedicated: dict[int, int]
    shared: dict[int, int]
    adapter_dedicated: int


class GpuCounterReader:
    """常駐的 PowerShell `Get-Counter` 迴圈（約每秒一筆）；`latest` 是最新一筆。"""

    def __init__(self) -> None:
        self.latest: GpuSample | None = None
        self.errors = 0
        self._proc: subprocess.Popen[bytes] | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._proc = subprocess.Popen(
            [
                "powershell.exe",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                GPU_COUNTER_PS,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            stdin=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        self._thread = threading.Thread(
            target=self._read, name="gpu-counters", daemon=True
        )
        self._thread.start()

    def _read(self) -> None:
        assert self._proc is not None and self._proc.stdout is not None
        for raw in self._proc.stdout:
            try:
                data = json.loads(raw.decode("utf-8", "replace"))
                self.latest = GpuSample(
                    time.monotonic(),
                    {int(k): int(v) for k, v in (data.get("ded") or {}).items()},
                    {int(k): int(v) for k, v in (data.get("shared") or {}).items()},
                    int(data.get("adapter") or 0),
                )
            except (ValueError, TypeError, AttributeError) as exc:
                self.errors += 1
                logger.warning("bad GPU counter line %r: %s", raw[:200], exc)

    def wait_first(self, timeout: float = 20.0) -> bool:
        deadline = time.monotonic() + timeout
        while self.latest is None and time.monotonic() < deadline:
            time.sleep(0.2)
        return self.latest is not None

    def stop(self) -> None:
        if self._proc is not None and self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(10)
            except subprocess.TimeoutExpired:
                logger.error("GPU counter PowerShell did not exit; killing it")
                self._proc.kill()


def nvidia_smi_total() -> dict[str, int] | None:
    """整張顯示卡（第一張）的已用／總顯示記憶體（MiB）與使用率；失敗時 None。"""
    try:
        out = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=memory.used,memory.total,utilization.gpu",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            timeout=10,
            check=True,
        ).stdout.decode("ascii", "replace")
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning("nvidia-smi failed: %s", exc)
        return None
    first = out.strip().splitlines()[0] if out.strip() else ""
    parts = [p.strip() for p in first.split(",")]
    if len(parts) != 3 or not all(p.isdigit() for p in parts):
        logger.warning("unexpected nvidia-smi output: %r", out[:200])
        return None
    return {
        "used_mib": int(parts[0]),
        "total_mib": int(parts[1]),
        "util_pct": int(parts[2]),
    }


OLLAMA_BASE = "http://127.0.0.1:11434"


def ollama_ps(base: str = OLLAMA_BASE) -> list[dict[str, Any]] | None:
    """Ollama 目前載入的模型（名稱、顯示記憶體、到期時間）；連不上時 None。"""
    try:
        with urllib.request.urlopen(f"{base}/api/ps", timeout=3) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as exc:
        logger.debug("ollama /api/ps failed: %s", exc)
        return None
    models = data.get("models") if isinstance(data, dict) else None
    if not isinstance(models, list):
        return None
    return [
        {
            "name": m.get("name"),
            "size_vram": m.get("size_vram"),
            "expires_at": m.get("expires_at"),
        }
        for m in models
        if isinstance(m, dict)
    ]


# --------------------------------------------------------------------------- 取樣器

OLLAMA_NAMES = ("ollama.exe", "llama-server.exe", "ollama app.exe")


@dataclass
class Group:
    """要彙總的一組行程：以 root pid 的子孫，或以執行檔名稱找。"""

    label: str
    root: int | None = None
    names: tuple[str, ...] = ()
    known: dict[int, tuple[str, int | None]] = field(default_factory=dict)
    """曾經出現過的 pid → (名稱, 建立時間)，事後檢查有沒有殘留（孤兒）行程。"""


class Monitor:
    """背景每 `interval` 秒取樣一次，寫 NDJSON，也保留在記憶體給判定用。

    每筆樣本：`{"t": 相對開始的秒數, "groups": {label: 彙總}, "gpu_adapter_mib",
    "nvidia": {...}, "ollama_ps": [...]}`；`mark()` 寫入事件標記 `{"t", "mark", ...}`。
    """

    def __init__(self, out: Path, interval: float = 1.0) -> None:
        self.out_path = out
        self.interval = interval
        self.groups: dict[str, Group] = {}
        self.samples: list[dict[str, Any]] = []
        self.marks: list[dict[str, Any]] = []
        self.t0 = time.monotonic()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._gpu = GpuCounterReader()
        self._file: TextIO | None = None
        self._thread: threading.Thread | None = None
        self._nvidia: dict[str, int] | None = None
        self._ollama: list[dict[str, Any]] | None = None
        self._slow_thread: threading.Thread | None = None

    # --- 設定 -----------------------------------------------------------------

    def track_root(self, label: str, pid: int | None) -> None:
        """以 root pid 追蹤一組行程（引擎重啟時換新的 pid；None 表示暫停追蹤）。"""
        with self._lock:
            group = self.groups.setdefault(label, Group(label))
            group.root = pid

    def track_names(self, label: str, names: Iterable[str]) -> None:
        with self._lock:
            self.groups[label] = Group(label, names=tuple(n.lower() for n in names))

    def now(self) -> float:
        return time.monotonic() - self.t0

    def mark(self, name: str, **data: Any) -> dict[str, Any]:
        rec = {"t": round(self.now(), 3), "mark": name, **data}
        with self._lock:
            self.marks.append(rec)
            if self._file is not None:
                self._file.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._file.flush()
        logger.info("mark %s %s", name, data if data else "")
        return rec

    # --- 生命週期 -------------------------------------------------------------

    def start(self) -> None:
        self.out_path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.out_path.open("w", encoding="utf-8")
        self._gpu.start()
        if not self._gpu.wait_first():
            logger.warning("no GPU counter sample yet; continuing without it")
        self._thread = threading.Thread(
            target=self._loop, name="soak-monitor", daemon=True
        )
        self._thread.start()
        self._slow_thread = threading.Thread(
            target=self._slow_loop, name="soak-monitor-slow", daemon=True
        )
        self._slow_thread.start()

    def stop(self) -> None:
        self._stop.set()
        for t in (self._thread, self._slow_thread):
            if t is not None:
                t.join(15)
        self._gpu.stop()
        with self._lock:
            if self._file is not None:
                self._file.close()
                self._file = None

    def __enter__(self) -> Self:
        self.start()
        return self

    def __exit__(self, *_: object) -> None:
        self.stop()

    # --- 取樣 -----------------------------------------------------------------

    def _slow_loop(self) -> None:
        """nvidia-smi 與 Ollama /api/ps：較慢，每 5 秒一次，不擋住每秒的取樣。"""
        while not self._stop.is_set():
            self._nvidia = nvidia_smi_total()
            self._ollama = ollama_ps()
            self._stop.wait(5.0)

    def _loop(self) -> None:
        next_at = time.monotonic()
        while not self._stop.is_set():
            try:
                self.sample_once()
            except Exception:
                logger.exception("sampling failed")
            next_at += self.interval
            self._stop.wait(max(0.0, next_at - time.monotonic()))

    def sample_once(self) -> dict[str, Any]:
        procs = snapshot()
        gpu = self._gpu.latest
        rec: dict[str, Any] = {"t": round(self.now(), 3), "groups": {}}
        with self._lock:
            groups = list(self.groups.values())
        for group in groups:
            if group.root is not None:
                pids = descendants(procs, group.root)
            elif group.names:
                pids = [p.pid for p in procs.values() if p.name.lower() in group.names]
            else:
                pids = []
            rec["groups"][group.label] = self._summarize(group, procs, pids, gpu)
        if gpu is not None:
            rec["gpu_adapter_mib"] = round(gpu.adapter_dedicated / MIB, 1)
            rec["gpu_age_s"] = round(time.monotonic() - gpu.t, 2)
        if self._nvidia is not None:
            rec["nvidia"] = self._nvidia
        if self._ollama is not None:
            rec["ollama_ps"] = self._ollama
        with self._lock:
            self.samples.append(rec)
            if self._file is not None:
                self._file.write(json.dumps(rec, ensure_ascii=False) + "\n")
                self._file.flush()
        return rec

    def _summarize(
        self,
        group: Group,
        procs: dict[int, ProcEntry],
        pids: list[int],
        gpu: GpuSample | None,
    ) -> dict[str, Any]:
        total: dict[str, Any] = {
            "private_mib": 0.0,
            "ws_mib": 0.0,
            "handles": 0,
            "threads": 0,
            "gpu_ded_mib": 0.0,
            "gpu_shared_mib": 0.0,
            "gpu_mib": 0.0,
            "nproc": 0,
        }
        per: dict[str, Any] = {}
        for pid in pids:
            st = process_stats(procs[pid])
            if pid not in group.known:
                group.known[pid] = (st.name, st.created)
            ded = gpu.dedicated.get(pid, 0) if gpu else 0
            shared = gpu.shared.get(pid, 0) if gpu else 0
            total["nproc"] += 1
            total["threads"] += st.threads
            total["private_mib"] += (st.private or 0) / MIB
            total["ws_mib"] += (st.working_set or 0) / MIB
            total["handles"] += st.handles or 0
            total["gpu_ded_mib"] += ded / MIB
            total["gpu_shared_mib"] += shared / MIB
            per[str(pid)] = {
                "name": st.name,
                "private_mib": round((st.private or 0) / MIB, 1),
                "handles": st.handles,
                "threads": st.threads,
                "gpu_ded_mib": round(ded / MIB, 1),
                "gpu_shared_mib": round(shared / MIB, 1),
            }
        # 顯示記憶體不夠時 CUDA 的配置會溢出到共用記憶體：判定一律看兩者的和
        total["gpu_mib"] = total["gpu_ded_mib"] + total["gpu_shared_mib"]
        for key in (
            "private_mib",
            "ws_mib",
            "gpu_ded_mib",
            "gpu_shared_mib",
            "gpu_mib",
        ):
            total[key] = round(total[key], 1)
        total["procs"] = per
        return total

    # --- 查詢 -----------------------------------------------------------------

    def series(
        self, label: str, key: str, t0: float = 0.0, t1: float = 1e18
    ) -> list[tuple[float, float]]:
        """某組行程某項指標在 [t0, t1] 的 (t, 值)。"""
        with self._lock:
            samples = list(self.samples)
        out = []
        for s in samples:
            g = s["groups"].get(label)
            if g is None or g["nproc"] == 0 or not (t0 <= s["t"] <= t1):
                continue
            out.append((s["t"], float(g[key])))
        return out

    def leftover(self, label: str) -> list[dict[str, Any]]:
        """這組行程曾經出現過、現在還活著的行程（pid 與建立時間都相同）。"""
        with self._lock:
            group = self.groups.get(label)
            known = dict(group.known) if group else {}
        procs = snapshot()
        alive = []
        for pid, (name, created) in known.items():
            if pid in procs and procs[pid].name == name:
                now_created = process_stats(procs[pid]).created
                if created is None or now_created == created:
                    alive.append({"pid": pid, "name": name})
        return alive

    def forget(self, label: str) -> None:
        """清掉這組曾經出現過的行程記錄（下一輪重新開始收集）。"""
        with self._lock:
            group = self.groups.get(label)
            if group is not None:
                group.known.clear()
