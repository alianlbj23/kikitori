"""後續功能（振假名、預先量化衍生快取、下載完成事件順序）的正式版驗收：引擎協定部分。

暫時性實驗，不屬於正式程式碼。以 stdio 協定驅動打包好的 `kikitori-engine.exe`（真擷取、
真 Whisper GPU、真 Ollama），量測與判定寫到 `results/<時間>-final-<情境>/`（git 忽略）。
GUI 部分在 `final_gui_cdp.mjs`（它以 `play` 情境把日語音訊循環播到虛擬喇叭）。

情境：
- `prepare`：從 `experiments/vtuber_eval/data/` 的 FLEURS 日語片段（CC-BY 4.0）組出測試音檔
  `data/final_ja.wav` 與真值 `data/final_ja.json`。
- `derived`：衍生快取第一次（即時量化）→ 背景轉換（子行程）→ 從衍生檔載入的時間、引擎與子行程的
  私有記憶體、驗證途中按開始、`models.list` 的 `disk_bytes` 與 `models.delete`。
- `download-delete`：下載完成（`done`）後立即刪除，不得回 `model_in_use`（e1359ad）。
- `furigana-sessions`：振假名開／關交錯的 session，引擎私有記憶體與顯示記憶體不累積。
- `play`：循環播放 `data/final_ja.wav` 到虛擬喇叭（給 GUI 腳本用）。

安全規則（與 `acceptance.py` 相同）：
- 聲音只播到 Steam Streaming Speakers（`e2e_common.find_output_device` 檢查不是預設輸出）。
- 設定檔放在結果資料夾（`KIKITORI_CONFIG_DIR`）。
- 模型快取一律隔離：`LOCALAPPDATA` 指到結果資料夾，使用者正式快取裡的模型檔以**硬連結**放入
  （`derived` 情境不連 `derived/`）。硬連結的檔案引擎只讀不寫；刪除隔離快取只少一個連結，
  使用者的檔案不受影響。開始與結束時比對使用者快取的檔案清單、大小與修改時間。
- 不卸載、不刪除 Ollama 模型（只以 `keep_alive` 預先載入 qwen3.5:9b）。

執行：
  uv run python -X utf8 experiments/e2e_acceptance/final_followup.py                 互動選單
  uv run python -X utf8 experiments/e2e_acceptance/final_followup.py --batch derived trials=3
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import statistics
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SOAK = ROOT / "experiments" / "memory_soak"
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(SOAK))

import harness
import monitor
from e2e_common import (
    SR,
    EngineClient,
    EngineError,
    Float32Array,
    Player,
    read_wav,
    resample,
    silence,
    write_wav,
)

logger = logging.getLogger("final")

DATA = HERE / "data"
RESULTS = HERE / "results"
VTUBER_DATA = ROOT / "experiments" / "vtuber_eval" / "data"
AUDIO = DATA / "final_ja.wav"
AUDIO_META = DATA / "final_ja.json"
ENGINE = Path(
    os.environ.get("KIKITORI_FINAL_ENGINE")
    or HERE / "data" / "build-final" / "kikitori-engine" / "kikitori-engine.exe"
)
"""打包好的引擎（packaging/engine/build.py 的 one-folder）；可用 KIKITORI_FINAL_ENGINE 指到別的建置。"""
USER_KIKITORI = Path(os.environ["LOCALAPPDATA"]) / "kikitori"
"""使用者正式的快取根目錄（只讀：硬連結來源與前後比對）。"""
LAD_ROOT = Path(
    os.environ.get("KIKITORI_FINAL_LAD_ROOT")
    or Path(tempfile.gettempdir()) / "kikitori-final-lad"
)
"""隔離快取（當成 LOCALAPPDATA）的上層目錄：硬連結只能在同一個磁碟區，所以不放在結果資料夾。"""
RECOMMENDED_MODELS = ("large-v3-turbo", "large-v3")
OLLAMA = "http://127.0.0.1:11434"
TRANSLATION_MODEL = "qwen3.5:9b"
MIB = 1024 * 1024

READY_RE = re.compile(r"pre-quantized model ready at (.+)$")
VERIFYING_RE = re.compile(r"converted (\S+) in [\d.]+ s; verifying")
INTERRUPTED_RE = re.compile(r"pre-quantization of (\S+) .*interrupted")
FAILED_RE = re.compile(r"pre-quantization of (\S+) .*failed")
LOADING_DERIVED_RE = re.compile(r"loading pre-quantized (\S+) \(")


# --------------------------------------------------------------------------- 共用


def new_run_dir(name: str) -> Path:
    d = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}-final-{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


class Run:
    """判定與數字；結束時寫 `result.json`。"""

    def __init__(self, name: str) -> None:
        self.name = name
        self.dir = new_run_dir(name)
        self.steps: dict[str, Any] = {}
        self.data: dict[str, Any] = {}
        print(f"results: {self.dir}", flush=True)

    def check(self, name: str, ok: bool, detail: Any) -> bool:
        self.steps[name] = {"passed": bool(ok), "detail": detail}
        text = json.dumps(detail, ensure_ascii=False, default=str)
        print(
            f"  [{'PASS' if ok else 'FAIL'}] {name}: {text[:400]}",
            flush=True,
        )
        return bool(ok)

    def save(self) -> int:
        passed = all(s["passed"] for s in self.steps.values()) and bool(self.steps)
        (self.dir / "result.json").write_text(
            json.dumps(
                {
                    "scenario": self.name,
                    "passed": passed,
                    "steps": self.steps,
                    **self.data,
                },
                ensure_ascii=False,
                indent=1,
                default=str,
            ),
            "utf-8",
        )
        print(f"{self.name}: {'PASS' if passed else 'FAIL'} ({self.dir})", flush=True)
        return 0 if passed else 1


def cache_fingerprint(root: Path = USER_KIKITORI) -> dict[str, list[int]]:
    """使用者快取（模型、GPU 元件、振假名辭典）每個檔案的大小與修改時間。"""
    out: dict[str, list[int]] = {}
    for sub in ("models", "gpu-runtime", "furigana"):
        base = root / sub
        if not base.exists():
            continue
        for p in sorted(base.rglob("*")):
            if p.is_file():
                st = p.stat()
                out[str(p.relative_to(root))] = [st.st_size, st.st_mtime_ns]
    return out


def link_tree(src: Path, dst: Path) -> int:
    """把 `src` 底下的普通檔案硬連結到 `dst`（同一個磁碟區）；回傳檔案數。"""
    if src.is_symlink():
        raise RuntimeError(f"refusing to link a symlink: {src}")
    if src.is_file():
        dst.parent.mkdir(parents=True, exist_ok=True)
        os.link(src, dst)
        return 1
    count = 0
    for child in src.iterdir():
        count += link_tree(child, dst / child.name)
    return count


def isolated_cache(
    lad: Path,
    *,
    models: tuple[str, ...] = RECOMMENDED_MODELS,
    with_derived: bool,
    with_furigana: bool,
) -> dict[str, Any]:
    """在 `lad`（當成 LOCALAPPDATA）建立隔離的 kikitori 快取，檔案以硬連結放入。"""
    dst = lad / "kikitori"
    linked: dict[str, int] = {}
    for mid in models:
        for child in (USER_KIKITORI / "models" / mid).iterdir():
            if child.name == "derived" and not with_derived:
                continue
            linked[f"models/{mid}/{child.name}"] = link_tree(
                child, dst / "models" / mid / child.name
            )
    linked["gpu-runtime"] = link_tree(
        USER_KIKITORI / "gpu-runtime", dst / "gpu-runtime"
    )
    if with_furigana:
        linked["furigana"] = link_tree(USER_KIKITORI / "furigana", dst / "furigana")
    return linked


def remove_isolated(lad: Path) -> None:
    """刪除本測試建立的隔離快取（硬連結；使用者的檔案只少一個連結）。"""
    if not lad.resolve().is_relative_to(LAD_ROOT.resolve()):
        raise RuntimeError(f"refusing to delete {lad}: not under {LAD_ROOT}")
    shutil.rmtree(lad)


def tree_bytes(path: Path) -> int:
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def start_engine(run_dir: Path, label: str, lad: Path, config: Path) -> EngineClient:
    config.mkdir(parents=True, exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("KIKITORI_ENGINE_FAKE")
    }
    env["KIKITORI_CONFIG_DIR"] = str(config)
    env["LOCALAPPDATA"] = str(lad)
    client = EngineClient(
        [str(ENGINE)],
        env=env,
        cwd=ENGINE.parent,
        stderr_log=run_dir / f"{label}-stderr.log",
    )
    info = client.request("engine.info", timeout=120)
    if info.get("fake"):
        raise RuntimeError("engine reports fake=true")
    return client


def configure(client: EngineClient, *, translation: bool = True) -> dict[str, Any]:
    """推薦組合（這台：turbo／large-v3 int8_float16、qwen3.5:9b）＋影片翻譯 ja → zh-TW、虛擬喇叭。"""
    rec = client.request("settings.apply_recommendation", timeout=120)
    device = harness.find_loopback_id(client)
    settings = client.request(
        "settings.set",
        {
            "mode": "media",
            "source_language": "ja",
            "target_language": "zh-TW",
            "media": {"device_id": device},
            "translation": {"enabled": translation},
        },
    )
    asr = settings["settings"]["asr"] if "settings" in settings else settings["asr"]
    return {
        "device_id": device,
        "asr": asr,
        "recommendation": rec.get("recommendation"),
    }


def ollama_preload(model: str = TRANSLATION_MODEL, keep_alive: str = "30m") -> float:
    """讓翻譯模型先在顯示記憶體裡（每個 session 的翻譯預熱都是「熱」的，比較只差在 ASR）。"""
    body = json.dumps({"model": model, "keep_alive": keep_alive}).encode()
    req = urllib.request.Request(
        f"{OLLAMA}/api/generate", body, {"Content-Type": "application/json"}
    )
    t = time.monotonic()
    with urllib.request.urlopen(req, timeout=180) as r:
        r.read()
    return time.monotonic() - t


def ollama_ps() -> list[dict[str, Any]]:
    try:
        with urllib.request.urlopen(f"{OLLAMA}/api/ps", timeout=5) as r:
            return [
                {"name": m["name"], "size": m["size"], "size_vram": m["size_vram"]}
                for m in json.load(r).get("models", [])
            ]
    except OSError as exc:
        return [{"error": str(exc)}]


class LogTail:
    """逐行讀引擎 stderr log（EngineClient 直接寫檔），記下讀到每行的時刻。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.pos = 0
        self.buf = b""
        self.lines: list[tuple[float, str]] = []

    def poll(self) -> None:
        try:
            with self.path.open("rb") as f:
                f.seek(self.pos)
                data = f.read()
        except OSError:
            return
        if not data:
            return
        self.pos += len(data)
        self.buf += data
        *complete, self.buf = self.buf.split(b"\n")
        now = time.monotonic()
        for raw in complete:
            self.lines.append((now, raw.decode("utf-8", "replace").rstrip("\r")))

    def find(
        self, pattern: re.Pattern[str], since: int = 0
    ) -> list[tuple[int, float, re.Match[str]]]:
        self.poll()
        out = []
        for i, (t, line) in enumerate(self.lines[since:], start=since):
            m = pattern.search(line)
            if m:
                out.append((i, t, m))
        return out

    def wait(
        self,
        pattern: re.Pattern[str],
        *,
        count: int = 1,
        since: int = 0,
        timeout: float,
        fail: re.Pattern[str] | None = None,
    ) -> list[tuple[int, float, re.Match[str]]]:
        deadline = time.monotonic() + timeout
        while True:
            hits = self.find(pattern, since)
            if len(hits) >= count:
                return hits
            if fail is not None and self.find(fail, since):
                raise RuntimeError(
                    f"log reported failure: {self.find(fail, since)[0][2].group(0)}"
                )
            if time.monotonic() > deadline:
                raise TimeoutError(
                    f"{pattern.pattern!r} x{count} not in log within {timeout} s"
                )
            time.sleep(0.1)


class ProcSampler:
    """每 `interval` 秒記下引擎與它所有子孫行程的私有記憶體（MiB）。"""

    def __init__(self, root_pid: int, interval: float = 0.1) -> None:
        self.root = root_pid
        self.interval = interval
        self.samples: list[dict[str, Any]] = []
        self.children_seen: dict[int, dict[str, Any]] = {}
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="proc-sampler", daemon=True
        )

    def start(self) -> ProcSampler:
        self._thread.start()
        return self

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(5)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                procs = monitor.snapshot()
                rec: dict[str, Any] = {
                    "t": time.monotonic(),
                    "engine": None,
                    "children": {},
                }
                for pid in monitor.descendants(procs, self.root):
                    st = monitor.process_stats(procs[pid])
                    mib = None if st.private is None else st.private / MIB
                    if pid == self.root:
                        rec["engine"] = mib
                    else:
                        rec["children"][pid] = mib
                        seen = self.children_seen.setdefault(
                            pid,
                            {
                                "name": st.name,
                                "first": rec["t"],
                                "last": rec["t"],
                                "peak": 0.0,
                            },
                        )
                        seen["last"] = rec["t"]
                        if mib is not None:
                            seen["peak"] = max(seen["peak"], mib)
                self.samples.append(rec)
            except Exception:
                logger.exception("sampling failed")
            self._stop.wait(self.interval)

    def engine_values(self, t0: float, t1: float) -> list[float]:
        return [
            s["engine"]
            for s in self.samples
            if t0 <= s["t"] <= t1 and s["engine"] is not None
        ]

    def children_alive(self, t: float) -> list[int]:
        """`t` 之後第一筆樣本裡還在的子行程。"""
        for s in self.samples:
            if s["t"] >= t:
                return list(s["children"])
        return list(self.samples[-1]["children"]) if self.samples else []


def load_audio() -> tuple[Float32Array, dict[str, Any]]:
    if not AUDIO.exists():
        prepare()
    audio, rate = read_wav(AUDIO)
    return resample(audio, rate, SR), json.loads(AUDIO_META.read_text("utf-8"))


def clip(
    audio: Float32Array, meta: dict[str, Any], first: int, count: int
) -> Float32Array:
    """真值第 `first` 句起 `count` 句（前後各留 0.5 s）。"""
    segs = meta["segments"][first : first + count]
    a = max(0, segs[0]["start"] - SR // 2)
    b = min(audio.size, segs[-1]["end"] + SR // 2)
    return np.concatenate([audio[a:b], silence(2.0)]).astype(np.float32)


def timed_session(
    client: EngineClient,
    player: Player | None,
    audio: Float32Array | None,
    *,
    lines: LogTail,
    label: str,
) -> dict[str, Any]:
    """session.start → running 的時間；播放（可省略）；停止。回傳數字與這段 log 的衍生檔載入行。"""
    since_line = len(lines.lines)
    st = harness.start_session(client, timeout=300)
    load = st.t_running - st.t_request
    print(f"  {label}: session {st.sid} running after {load:.2f} s", flush=True)
    if player is not None and audio is not None:
        time.sleep(0.5)
        player.play(label, audio)
        time.sleep(2.0)
    harness.stop_session(client, st)
    counts = harness.subtitle_counts(client, st.sid)
    derived_loads = sorted(
        {m.group(1) for _, _, m in lines.find(LOADING_DERIVED_RE, since_line)}
    )
    return {
        "label": label,
        "sid": st.sid,
        "load_secs": round(load, 3),
        "stop_secs": round((st.t_idle or 0) - (st.t_stop or 0), 3),
        "end_state": st.end_state,
        "derived_loads": derived_loads,
        **counts,
        "t_request": st.t_request,
        "t_idle": st.t_idle,
    }


def derived_dirs(lad: Path) -> dict[str, list[str]]:
    out = {}
    for mid in RECOMMENDED_MODELS:
        d = lad / "kikitori" / "models" / mid / "derived"
        out[mid] = sorted(p.name for p in d.iterdir()) if d.exists() else []
    return out


# --------------------------------------------------------------------------- prepare


def prepare(clips: int = 12, gap: float = 2.5) -> int:
    """FLEURS（CC-BY 4.0）日語朗讀：6–11 秒、參考文字不重複、含漢字的片段，依檔名排序取前 `clips` 個。"""
    manifest = json.loads((VTUBER_DATA / "manifest.json").read_text("utf-8"))
    chosen: list[dict[str, Any]] = []
    refs: set[str] = set()
    for item in sorted(manifest["items"], key=lambda i: i["id"]):
        if item["set"] != "fleurs" or item["condition"] != "clean":
            continue
        if not (6.0 <= item["seconds"] <= 11.0) or item["reference"] in refs:
            continue
        if not re.search(r"[一-鿿]", item["reference"]):
            continue
        refs.add(item["reference"])
        chosen.append(item)
        if len(chosen) == clips:
            break
    parts: list[Float32Array] = [silence(1.0)]
    pos = parts[0].size
    segments = []
    for item in chosen:
        audio, rate = read_wav(VTUBER_DATA / item["wav"])
        audio = resample(audio, rate, SR)
        voiced = np.flatnonzero(np.abs(audio) > 0.01)
        if voiced.size:
            audio = audio[max(0, voiced[0] - 800) : voiced[-1] + 800]
        segments.append(
            {
                "id": item["id"],
                "reference": item["reference"],
                "start": pos,
                "end": pos + audio.size,
            }
        )
        parts += [audio, silence(gap)]
        pos += audio.size + parts[-1].size
    full = np.concatenate(parts).astype(np.float32)
    write_wav(AUDIO, full, SR)
    AUDIO_META.write_text(
        json.dumps(
            {
                "source": "FLEURS ja_jp (CC-BY 4.0) via experiments/vtuber_eval/data",
                "sample_rate": SR,
                "seconds": round(full.size / SR, 2),
                "segments": segments,
            },
            ensure_ascii=False,
            indent=1,
        ),
        "utf-8",
    )
    print(f"wrote {AUDIO} ({full.size / SR:.1f} s, {len(segments)} sentences)")
    return 0


# --------------------------------------------------------------------------- C. derived


def derived_trial(
    run: Run,
    k: int,
    player: Player,
    audio: Float32Array,
    *,
    interrupt: bool,
    list_and_delete: bool,
) -> dict[str, Any]:
    lad = LAD_ROOT / f"{run.dir.name}-t{k}"
    linked = isolated_cache(lad, with_derived=False, with_furigana=False)
    out: dict[str, Any] = {"trial": k, "interrupt": interrupt, "linked_files": linked}
    print(
        f"trial {k}{' (interrupt during verification)' if interrupt else ''}",
        flush=True,
    )
    out["ollama_preload_secs"] = round(ollama_preload(), 2)
    client = start_engine(run.dir, f"t{k}-a", lad, run.dir / f"config-t{k}")
    lines = LogTail(run.dir / f"t{k}-a-stderr.log")
    sampler = ProcSampler(client.proc.pid).start()
    try:
        out["settings"] = configure(client)
        s1 = timed_session(client, player, audio, lines=lines, label="s1-first")
        out["s1"] = s1
        if interrupt:
            # 驗證中（子行程印出 verifying）1 s 後按開始
            hits = lines.wait(VERIFYING_RE, since=0, timeout=240, fail=FAILED_RE)
            _, _t_verify, m = hits[0]
            time.sleep(1.0)
            children_before = (
                [pid for pid in sampler.samples[-1]["children"]]
                if sampler.samples
                else []
            )
            since_line = len(lines.lines)
            s_int = timed_session(
                client, player, audio, lines=lines, label="s-interrupt"
            )
            t_req = s_int["t_request"]
            gone_at = None
            for s in sampler.samples:
                if s["t"] >= t_req and not any(
                    p in s["children"] for p in children_before
                ):
                    gone_at = s["t"]
                    break
            interrupted = lines.find(INTERRUPTED_RE, since_line)
            out["interrupt_result"] = {
                "verifying_model": m.group(1),
                "children_before": children_before,
                "child_gone_after_start_secs": None
                if gone_at is None
                else round(gone_at - t_req, 3),
                "interrupted_log": [ln[2].group(0) for ln in interrupted],
                "session": s_int,
                "tmp_dirs_after": derived_dirs(lad),
            }
        # 兩個模型都要產生衍生檔（turbo 與 large-v3），之後 5 s 再量「之後」
        ready = lines.wait(READY_RE, count=2, since=0, timeout=600, fail=FAILED_RE)
        t_last_ready = ready[-1][1]
        time.sleep(6.0)
        starts = [
            t for t, line in lines.lines if "starting pre-quantization of" in line
        ]
        t_first_start = starts[0] if starts else t_last_ready
        baseline = sampler.engine_values(t_first_start - 3.0, t_first_start - 0.05)
        during = sampler.engine_values(t_first_start, t_last_ready)
        after = sampler.engine_values(t_last_ready + 3.0, t_last_ready + 6.0)
        alive_after = sampler.children_alive(t_last_ready + 3.0)
        mem = {
            "engine_before_mib": round(statistics.median(baseline), 1)
            if baseline
            else None,
            "engine_peak_during_mib": round(max(during), 1) if during else None,
            "engine_after_mib": round(statistics.median(after), 1) if after else None,
            "children": {
                str(pid): {
                    "name": c["name"],
                    "peak_private_mib": round(c["peak"], 1),
                    "seen_secs": round(c["last"] - c["first"], 2),
                }
                for pid, c in sampler.children_seen.items()
            },
            "children_alive_after": alive_after,
            "job_secs": round(t_last_ready - t_first_start, 2),
            "idle_before_start_secs": round(
                t_first_start - (s1["t_idle"] or t_first_start), 2
            )
            if not interrupt
            else None,
        }
        if mem["engine_before_mib"] is not None and mem["engine_after_mib"] is not None:
            mem["engine_delta_mib"] = round(
                mem["engine_after_mib"] - mem["engine_before_mib"], 1
            )
        out["memory"] = mem
        out["ready_lines"] = [h[2].group(1) for h in ready]
        out["derived_dirs"] = derived_dirs(lad)
        s2 = timed_session(client, player, audio, lines=lines, label="s2-derived")
        s3 = timed_session(client, None, None, lines=lines, label="s3-derived")
        out["s2"], out["s3"] = s2, s3
        out["ollama_ps"] = ollama_ps()
    finally:
        out["exit_code_a"] = client.close()
        sampler.stop()
    # 新的引擎行程：從衍生檔載入
    client_b = start_engine(run.dir, f"t{k}-b", lad, run.dir / f"config-t{k}")
    lines_b = LogTail(run.dir / f"t{k}-b-stderr.log")
    try:
        out["s4"] = timed_session(
            client_b, None, None, lines=lines_b, label="s4-new-engine-derived"
        )
        if list_and_delete:
            out["list_delete"] = list_and_delete_check(client_b, lad)
    finally:
        out["exit_code_b"] = client_b.close()
    leftovers = [
        p.pid
        for p in monitor.snapshot().values()
        if p.name.lower() == "kikitori-engine.exe"
    ]
    out["leftover_engine_processes"] = leftovers
    return out


def list_and_delete_check(client: EngineClient, lad: Path) -> dict[str, Any]:
    listing = client.request("models.list", timeout=60)
    (RESULTS / lad.name.rsplit("-t", 1)[0] / "models-list.json").write_text(
        json.dumps(listing, ensure_ascii=False, indent=1), "utf-8"
    )
    entries = {}
    for value in listing.values():
        if isinstance(value, list):
            for e in value:
                if isinstance(e, dict) and e.get("id") in RECOMMENDED_MODELS:
                    entries[e["id"]] = e
    result: dict[str, Any] = {}
    for mid in RECOMMENDED_MODELS:
        folder = lad / "kikitori" / "models" / mid
        result[mid] = {
            "disk_bytes": entries.get(mid, {}).get("disk_bytes"),
            "folder_bytes": tree_bytes(folder),
            "derived_bytes": tree_bytes(folder / "derived")
            if (folder / "derived").exists()
            else 0,
        }
    target = "large-v3-turbo"
    folder = lad / "kikitori" / "models" / target
    try:
        deleted = client.request("models.delete", {"model": target}, timeout=60)
        result["delete"] = {"result": deleted, "folder_exists_after": folder.exists()}
    except EngineError as exc:
        result["delete"] = {"error": exc.error, "folder_exists_after": folder.exists()}
    user_folder = USER_KIKITORI / "models" / target
    result["user_folder_bytes_after_delete"] = tree_bytes(user_folder)
    return result


def scenario_derived(trials: int = 3) -> int:
    run = Run("derived")
    if not ENGINE.exists():
        raise FileNotFoundError(ENGINE)
    before = cache_fingerprint()
    audio, meta = load_audio()
    short = clip(audio, meta, 0, 2)
    player = Player()
    results = []
    try:
        for k in range(1, trials + 1):
            results.append(
                derived_trial(
                    run, k, player, short, interrupt=False, list_and_delete=(k == 1)
                )
            )
            remove_isolated(LAD_ROOT / f"{run.dir.name}-t{k}")
        results.append(
            derived_trial(
                run, trials + 1, player, short, interrupt=True, list_and_delete=False
            )
        )
        remove_isolated(LAD_ROOT / f"{run.dir.name}-t{trials + 1}")
    finally:
        player.close()
        run.data["trials"] = results
        after = cache_fingerprint()
        run.data["user_cache_unchanged"] = before == after
    normal = [r for r in results if not r["interrupt"]]

    def med(key: str) -> float | None:
        vals = [r[key]["load_secs"] for r in normal if key in r]
        return round(statistics.median(vals), 3) if vals else None

    loads = {k: med(k) for k in ("s1", "s2", "s3", "s4")}
    loads_all = {
        k: [r[k]["load_secs"] for r in normal if k in r]
        for k in ("s1", "s2", "s3", "s4")
    }
    run.data["load_medians"] = loads
    run.check(
        "first session quantizes on the fly; later sessions load the derived files",
        all(r["s1"]["derived_loads"] == [] for r in normal)
        and all(
            r[k]["derived_loads"] == sorted(RECOMMENDED_MODELS)
            for r in normal
            for k in ("s2", "s3", "s4")
        ),
        {"medians": loads, "all": loads_all},
    )
    run.check(
        "every session transcribed the Japanese clip (finals > 0) and stopped idle",
        all(
            r[k]["finals"] > 0 and r[k]["end_state"] == "idle"
            for r in results
            for k in ("s1", "s2")
        ),
        {f"t{r['trial']}": {k: r[k]["finals"] for k in ("s1", "s2")} for r in results},
    )
    mems = [r["memory"] for r in normal]
    run.check(
        "engine private memory within ±20 MiB across the background conversion",
        all(
            m.get("engine_delta_mib") is not None and abs(m["engine_delta_mib"]) <= 20
            for m in mems
        ),
        [
            {
                k: m.get(k)
                for k in (
                    "engine_before_mib",
                    "engine_peak_during_mib",
                    "engine_after_mib",
                    "engine_delta_mib",
                    "job_secs",
                    "idle_before_start_secs",
                )
            }
            for m in mems
        ],
    )
    run.check(
        "conversion child processes ran and none is left afterwards",
        all(m["children"] and not m["children_alive_after"] for m in mems),
        [
            {"children": m["children"], "alive_after": m["children_alive_after"]}
            for m in mems
        ],
    )
    intr = next((r for r in results if r["interrupt"]), None)
    if intr is not None:
        ir = intr.get("interrupt_result", {})
        gone = ir.get("child_gone_after_start_secs")
        run.check(
            "session started during verification: child ended, session runs, redo completes",
            gone is not None
            and gone <= 3.0
            and ir.get("session", {}).get("end_state") == "idle"
            and ir.get("session", {}).get("finals", 0) > 0
            and bool(ir.get("interrupted_log"))
            and not any(
                n.startswith(".tmp")
                for names in ir.get("tmp_dirs_after", {}).values()
                for n in names
            )
            and intr["s2"]["derived_loads"] == sorted(RECOMMENDED_MODELS),
            {
                **{k: v for k, v in ir.items() if k != "session"},
                "session_load_secs": ir.get("session", {}).get("load_secs"),
                "after_redo": intr["derived_dirs"],
                "s2": intr["s2"]["load_secs"],
            },
        )
    ld = results[0].get("list_delete", {})
    run.check(
        "models.list disk_bytes includes derived files; models.delete removes them too",
        all(
            ld.get(m, {}).get("disk_bytes") == ld.get(m, {}).get("folder_bytes")
            and ld.get(m, {}).get("derived_bytes", 0) > 0
            for m in RECOMMENDED_MODELS
        )
        and ld.get("delete", {}).get("folder_exists_after") is False
        and ld.get("delete", {}).get("result", {}).get("freed_bytes")
        == ld.get("large-v3-turbo", {}).get("folder_bytes"),
        ld,
    )
    run.check(
        "no leftover engine processes; user model cache unchanged",
        all(not r["leftover_engine_processes"] for r in results)
        and run.data["user_cache_unchanged"],
        {
            "leftovers": [r["leftover_engine_processes"] for r in results],
            "user_cache_unchanged": run.data["user_cache_unchanged"],
            "exit_codes": [
                (r.get("exit_code_a"), r.get("exit_code_b")) for r in results
            ],
        },
    )
    return run.save()


# --------------------------------------------------------------------------- E. download → delete


def scenario_download_delete(asr_rounds: int = 5, furigana_rounds: int = 2) -> int:
    run = Run("download-delete")
    lad = LAD_ROOT / f"{run.dir.name}"
    lad.mkdir(parents=True)
    client = start_engine(run.dir, "engine", lad, run.dir / "config")
    rounds: list[dict[str, Any]] = []
    try:
        plan = [("tiny", "asr")] * asr_rounds + [
            ("furigana-dictionary", "furigana")
        ] * furigana_rounds
        for i, (model, kind) in enumerate(plan, start=1):
            since = len(client.events)
            client.request("models.download", {"model": model}, timeout=30)
            done = client.wait_event(
                lambda m, model=model: (
                    m.get("event") == "download_progress"
                    and m["data"]["model"] == model
                    and m["data"]["state"] in ("done", "error", "cancelled")
                ),
                timeout=600,
                since=since,
            )
            state = done.msg["data"]["state"]
            t_delete = time.monotonic()
            try:
                res = client.request("models.delete", {"model": model}, timeout=60)
                outcome = {"ok": True, "freed_bytes": res.get("freed_bytes")}
            except EngineError as exc:
                outcome = {
                    "ok": False,
                    "code": exc.code,
                    "message": exc.error.get("message"),
                }
            later = [
                r.msg["data"]
                for r in client.events[since:]
                if r.msg.get("event") == "download_progress" and r.t > done.t
            ]
            rec = {
                "round": i,
                "model": model,
                "kind_in_event": done.msg["data"].get("kind"),
                "state": state,
                "delete_sent_after_done_ms": round((t_delete - done.t) * 1000, 1),
                "delete": outcome,
                "progress_events_after_done": len(later),
                "installed_after": (
                    (lad / "kikitori" / "models" / "tiny").exists()
                    if kind == "asr"
                    else any((lad / "kikitori" / "furigana").glob("*"))
                    if (lad / "kikitori" / "furigana").exists()
                    else False
                ),
            }
            rounds.append(rec)
            print(f"  round {i}: {rec}", flush=True)
    finally:
        code = client.close()
        run.data["rounds"] = rounds
    run.check(
        "delete right after `done` succeeds every time (no model_in_use), nothing left",
        len(rounds) == asr_rounds + furigana_rounds
        and all(
            r["state"] == "done"
            and r["delete"]["ok"]
            and not r["installed_after"]
            and r["progress_events_after_done"] == 0
            for r in rounds
        ),
        rounds,
    )
    run.check("engine exit code 0", code == 0, code)
    remove_isolated(lad)
    return run.save()


# --------------------------------------------------------------------------- D. furigana sessions


def scenario_furigana_sessions(
    pairs: int = 3, settle: float = 12.0, plan: str = ""
) -> int:
    """`plan`：每個 session 的振假名開關（"1" 開、"0" 關，第一個是暖機）；空字串＝關＋開／關交錯 `pairs` 次。"""
    if plan and set(plan) - {"0", "1"}:
        raise ValueError(f"plan must be 0/1 characters, got {plan!r}")
    run = Run("furigana-sessions")
    lad = LAD_ROOT / run.dir.name
    before = cache_fingerprint()
    run.data["linked"] = isolated_cache(lad, with_derived=True, with_furigana=True)
    audio, meta = load_audio()
    piece = clip(audio, meta, 2, 4)
    player = Player()
    gpu = monitor.GpuCounterReader()
    gpu.start()
    gpu.wait_first()
    ollama_preload()
    client = start_engine(run.dir, "engine", lad, run.dir / "config")
    lines = LogTail(run.dir / "engine-stderr.log")
    sampler = ProcSampler(client.proc.pid, interval=0.5).start()
    sessions: list[dict[str, Any]] = []
    try:
        run.data["settings"] = configure(client)
        flags = (
            [c == "1" for c in plan]
            if plan
            else [False] + [x for _ in range(pairs) for x in (True, False)]
        )
        run.data["plan"] = "".join("1" if f else "0" for f in flags)
        for i, furigana in enumerate(flags):
            client.request("session.set_furigana", {"enabled": furigana}, timeout=30)
            since = len(client.events)
            rec = timed_session(
                client,
                player,
                piece,
                lines=lines,
                label=f"s{i}-{'on' if furigana else 'off'}",
            )
            finals = [
                r.msg["data"]
                for r in client.events[since:]
                if r.msg.get("event") == "subtitle"
                and r.msg["data"].get("session_id") == rec["sid"]
                and r.msg["data"]["is_final"]
                and r.msg["data"]["original"].strip()
            ]
            time.sleep(settle)
            t = time.monotonic()
            time.sleep(4.0)
            vals = sampler.engine_values(t, time.monotonic())
            g = gpu.latest
            pid = client.proc.pid
            rec.update(
                {
                    "furigana": furigana,
                    "warmup": i == 0,
                    "private_after_mib": round(statistics.median(vals), 1)
                    if vals
                    else None,
                    "gpu_after_mib": round(
                        ((g.dedicated.get(pid, 0) + g.shared.get(pid, 0)) / MIB), 1
                    )
                    if g
                    else None,
                    "finals_ja": sum(
                        1 for f in finals if f.get("source_language") == "ja"
                    ),
                    "finals_with_ruby": sum(1 for f in finals if "ruby" in f),
                    "ruby_examples": [
                        {"original": f["original"], "ruby": f.get("ruby")}
                        for f in finals
                        if f.get("ruby")
                    ][:3],
                }
            )
            sessions.append(
                {k: v for k, v in rec.items() if k not in ("t_request", "t_idle")}
            )
            print(
                f"  s{i} furigana={furigana}: private {rec['private_after_mib']} MiB, gpu {rec['gpu_after_mib']} MiB, "
                f"finals {len(finals)}, ruby {rec['finals_with_ruby']}",
                flush=True,
            )
    finally:
        code = client.close()
        sampler.stop()
        gpu.stop()
        player.close()
        run.data["sessions"] = sessions
        run.data["exit_code"] = code
    measured = [s for s in sessions if not s["warmup"]]
    on = [s for s in measured if s["furigana"]]
    off = [s for s in measured if not s["furigana"]]
    run.check(
        "furigana on → every Japanese final has `ruby`; off → none",
        all(s["finals_with_ruby"] == s["finals_ja"] > 0 for s in on)
        and all(s["finals_with_ruby"] == 0 for s in off),
        [
            {k: s[k] for k in ("label", "finals_ja", "finals_with_ruby")}
            for s in measured
        ],
    )
    priv = [s["private_after_mib"] for s in measured]
    gpus = [s["gpu_after_mib"] for s in measured]
    base = sessions[0]["private_after_mib"]
    run.data["private_series"] = [s["private_after_mib"] for s in sessions]
    run.data["gpu_series"] = [s["gpu_after_mib"] for s in sessions]
    run.check(
        "engine private memory after sessions does not accumulate (≤ 2 MiB sudachipy + 4 MiB noise over the warm-up)",
        max(priv) - min(priv) <= 6.0 and priv[-1] - base <= 6.0,
        {
            "warmup": base,
            "series": priv,
            "on": [s["private_after_mib"] for s in on],
            "off": [s["private_after_mib"] for s in off],
        },
    )
    run.check(
        "engine GPU memory after sessions constant",
        max(gpus) - min(gpus) <= 32.0,
        {"warmup": sessions[0]["gpu_after_mib"], "series": gpus},
    )
    run.check(
        "engine exit code 0; user cache unchanged",
        code == 0 and cache_fingerprint() == before,
        code,
    )
    remove_isolated(lad)
    return run.save()


# --------------------------------------------------------------------------- play


def scenario_play(seconds: float = 60.0) -> int:
    """循環播放 `final_ja.wav` 到虛擬喇叭 `seconds` 秒（stdin 關閉也停止）。"""
    audio, _ = load_audio()
    player = harness.LoopPlayer()
    print(
        f"playing {AUDIO.name} to {player.device.name!r} for {seconds:.0f} s",
        flush=True,
    )
    stop = threading.Event()

    def watch_stdin() -> None:
        try:
            sys.stdin.read()
        except (OSError, ValueError) as exc:
            logger.warning("stdin: %s", exc)
        stop.set()

    threading.Thread(target=watch_stdin, daemon=True).start()
    player.start(audio)
    try:
        stop.wait(seconds)
    finally:
        player.close()
    return 0 if player.error is None else 1


# --------------------------------------------------------------------------- 入口

SCENARIOS: dict[str, tuple[str, Callable[..., int], dict[str, Any]]] = {
    "prepare": ("準備日語測試音檔（FLEURS）", prepare, {"clips": 12, "gap": 2.5}),
    "derived": (
        "C. 衍生快取（第一次／背景轉換／從衍生檔載入／驗證中開始／列表與刪除）",
        scenario_derived,
        {"trials": 3},
    ),
    "download-delete": (
        "E. 下載完成後立即刪除",
        scenario_download_delete,
        {"asr_rounds": 5, "furigana_rounds": 2},
    ),
    "furigana-sessions": (
        "D. 振假名開／關交錯 session 的記憶體",
        scenario_furigana_sessions,
        {"pairs": 3, "settle": 12.0, "plan": ""},
    ),
    "play": ("循環播放日語測試音檔", scenario_play, {"seconds": 60.0}),
}


def _convert(value: str, default: Any) -> Any:
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "y")
    if isinstance(default, int):
        return int(value)
    if isinstance(default, float):
        return float(value)
    return value


def run_named(name: str, overrides: dict[str, str]) -> int:
    _, fn, defaults = SCENARIOS[name]
    unknown = set(overrides) - set(defaults)
    if unknown:
        raise SystemExit(f"unknown parameters for {name}: {sorted(unknown)}")
    kwargs = {
        k: _convert(overrides[k], v) if k in overrides else v
        for k, v in defaults.items()
    }
    return fn(**kwargs)


def menu() -> int:
    names = list(SCENARIOS)
    while True:
        print("\n後續功能的正式版驗收（引擎協定）")
        for i, n in enumerate(names, 1):
            print(f"  {i}) {SCENARIOS[n][0]}")
        print(f"  {len(names) + 1}) 離開")
        choice = input("請輸入編號：").strip()
        if choice == str(len(names) + 1):
            return 0
        if not choice.isdigit() or not 1 <= int(choice) <= len(names):
            print("無效的選項")
            continue
        name = names[int(choice) - 1]
        overrides = {}
        for key, default in SCENARIOS[name][2].items():
            answer = input(f"  {key} [{default}]：").strip()
            if answer:
                overrides[key] = answer
        return run_named(name, overrides)


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if argv[:1] == ["--batch"] and len(argv) >= 2:
        name = argv[1]
        if name not in SCENARIOS:
            raise SystemExit(f"unknown scenario {name}; choose from {list(SCENARIOS)}")
        overrides = dict(a.split("=", 1) for a in argv[2:])
        return run_named(name, overrides)
    return menu()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
