"""引擎（stdio）的記憶體與資源釋放情境 1–6：真模型（GPU）、真擷取、真 Ollama。

暫時性實驗，不屬於正式程式碼。每個情境：
1. 開一個結果資料夾（`results/<時間>-<情境>/`，git 忽略）、取樣器（每秒）、計數代理；
2. 以隔離的設定資料夾啟動引擎，設定 GPU 組合與翻譯（經過代理）；
3. 執行情境、在取樣檔中寫入事件標記；
4. 依 `harness.py` 的門檻判定 PASS／FAIL，寫 `summary.json`。

同機若有其他實驗使用 GPU／Ollama，數字會受影響：每個情境都記錄整張顯示卡的用量與
Ollama 的已載入模型，並在 summary 的 notes 標示可能的干擾。
"""

from __future__ import annotations

import itertools
import json
import logging
import re
import shutil
import time
import urllib.request
from datetime import datetime
from typing import Any, Self

import numpy as np
from harness import (
    DEFAULT_TRANSLATION_MODEL,
    GPU_RESIDUE_MAX_MIB,
    GPU_RETURN_SECS,
    GPU_RETURN_TOL_MIB,
    GPU_SLOPE_MIB_PER_CYCLE,
    HANDLE_SLOPE_PER_CYCLE,
    KEEP_WARM_LOG,
    LONG_GPU_SLOPE_MIB_PER_MIN,
    LONG_HANDLE_SLOPE_PER_MIN,
    LONG_POST_PRIVATE_GROWTH_MIB,
    LONG_PRIVATE_SLOPE_MIB_PER_MIN,
    LONG_THREAD_RANGE,
    POST_WINDOW_SECS,
    PRIVATE_SLOPE_MIB_PER_CYCLE,
    SETTLE_SECS,
    THREAD_EXTRA_ALLOWED,
    THREAD_SLOPE_PER_CYCLE,
    WARM_UP_LOG,
    WARMUP_CYCLES,
    EngineClient,
    EngineError,
    LoopPlayer,
    Report,
    SessionTimes,
    base_settings,
    find_loopback_id,
    log_events,
    median_or_none,
    new_run_dir,
    r1,
    slope,
    speech_en,
    speech_ja,
    speech_zh,
    start_engine,
    start_session,
    stop_session,
    subtitle_counts,
    warnings_in,
    with_bgm,
)
from monitor import OLLAMA_BASE, OLLAMA_NAMES, Monitor
from proxy import CountingProxy

logger = logging.getLogger("soak.scenarios")

METRICS = ("private_mib", "handles", "threads", "gpu_mib", "gpu_ded_mib", "ws_mib")


# --------------------------------------------------------------------------- 共用環境


class EngineSoak:
    """一次情境的共用環境：結果資料夾、取樣器、計數代理、播放器、引擎。"""

    def __init__(
        self,
        name: str,
        which: str = "dev",
        *,
        translation_model: str = DEFAULT_TRANSLATION_MODEL,
        keep_warm_secs: int = 240,
        extra_env: dict[str, str] | None = None,
        player: bool = True,
        engine: bool = True,
    ) -> None:
        self.name = name
        self.which = which
        self.translation_model = translation_model
        self.keep_warm_secs = keep_warm_secs
        self.extra_env = extra_env
        self.run_dir = new_run_dir(name)
        self.report = Report(name, self.run_dir)
        self.monitor = Monitor(self.run_dir / "samples.ndjson")
        self.proxy = CountingProxy(OLLAMA_BASE)
        self.player: LoopPlayer | None = None
        self.client: EngineClient | None = None
        self.device_id = ""
        self._want_player = player
        self._want_engine = engine
        self._engine_count = 0

    # --- 生命週期 -------------------------------------------------------------

    def __enter__(self) -> Self:
        print(f"results: {self.run_dir}", flush=True)
        self.monitor.track_names("ollama", OLLAMA_NAMES)
        self.monitor.start()
        self.proxy.start()
        if self._want_player:
            self.player = LoopPlayer()
            print(f"playback device: {self.player.device.name!r}", flush=True)
        if self._want_engine:
            self.start_engine()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        _tb: object,
    ) -> None:
        if exc is not None:
            logger.error("scenario %s aborted", self.name, exc_info=exc)
            self.report.check(
                "scenario ran to completion", False, f"{type(exc).__name__}: {exc}"
            )
        try:
            if self.player is not None:
                self.player.close()
            if self.client is not None:
                code = self.close_engine()
                self.report.data.setdefault("engine_exit_codes", []).append(code)
            time.sleep(2)
            leftover = self.monitor.leftover("engine")
            self.report.check(
                "no engine processes left after the run",
                not leftover,
                json.dumps(leftover),
            )
        finally:
            self.monitor.stop()
            self.proxy.stop()
            (self.run_dir / "proxy-requests.json").write_text(
                json.dumps(
                    [r.to_dict(self.monitor.t0) for r in self.proxy.snapshot()],
                    indent=1,
                ),
                "utf-8",
            )
            self.report.data["contamination"] = contamination(
                self.monitor, self.translation_model
            )
            for text in contamination_notes(self.report.data["contamination"]):
                self.report.note(text)
            self.report.data["marks"] = self.monitor.marks
            self.report.save()

    def start_engine(self, *, configure: bool = True) -> EngineClient:
        self._engine_count += 1
        label = "engine" if self._engine_count == 1 else f"engine{self._engine_count}"
        self.client = start_engine(
            self.which, self.run_dir, label=label, extra_env=self.extra_env
        )
        self.monitor.track_root("engine", self.client.proc.pid)
        self.monitor.mark("engine_started", pid=self.client.proc.pid)
        if configure:
            self.device_id = find_loopback_id(self.client)
            self.client.request(
                "settings.set",
                base_settings(
                    self.device_id,
                    self.proxy.base_url,
                    self.translation_model,
                    keep_warm_secs=self.keep_warm_secs,
                ),
            )
        return self.client

    def close_engine(self) -> int:
        assert self.client is not None
        code = self.client.close()
        self.monitor.mark("engine_closed", code=code)
        self.client = None
        return code

    @property
    def engine(self) -> EngineClient:
        assert self.client is not None
        return self.client

    # --- 量測 -----------------------------------------------------------------

    def rel(self, t: float) -> float:
        """monotonic 時間 → 取樣檔的相對秒數。"""
        return t - self.monitor.t0

    def metrics(
        self, t0: float, t1: float, label: str = "engine"
    ) -> dict[str, float | None]:
        """[t0, t1]（相對秒數）內各指標的中位數。"""
        return {
            k: r1(median_or_none([v for _, v in self.monitor.series(label, k, t0, t1)]))
            for k in METRICS
        }

    def peak(
        self, t0: float, t1: float, key: str, label: str = "engine"
    ) -> float | None:
        values = [v for _, v in self.monitor.series(label, key, t0, t1)]
        return r1(max(values)) if values else None

    def wait_idle_baseline(self, secs: float = 8.0) -> dict[str, float | None]:
        t0 = self.monitor.now()
        time.sleep(secs)
        base = self.metrics(t0 + 2, self.monitor.now())
        self.monitor.mark("baseline", **base)
        return base


# --------------------------------------------------------------------------- 干擾


def _parse_ts(text: str | None) -> float | None:
    if not text:
        return None
    m = re.match(r"(.*\.\d{1,6})\d*(.*)$", text)
    fixed = (m.group(1) + m.group(2)) if m else text
    try:
        return datetime.fromisoformat(fixed).timestamp()
    except ValueError:
        return None


def contamination(mon: Monitor, own_model: str) -> dict[str, Any]:
    """同機其他程式對 GPU／Ollama 的使用：給判讀數字時參考。"""
    used = [s["nvidia"]["used_mib"] for s in mon.samples if "nvidia" in s]
    util = [s["nvidia"]["util_pct"] for s in mon.samples if "nvidia" in s]
    others: set[str] = set()
    for s in mon.samples:
        for m in s.get("ollama_ps") or []:
            if m.get("name") and m["name"] != own_model:
                others.add(str(m["name"]))
    engine_gpu = [
        s["groups"]["engine"]["gpu_mib"]
        for s in mon.samples
        if "engine" in s["groups"] and s["groups"]["engine"]["nproc"]
    ]
    return {
        "gpu_used_mib_min": min(used, default=None),
        "gpu_used_mib_max": max(used, default=None),
        "gpu_util_pct_max": max(util, default=None),
        "gpu_util_pct_median": median_or_none(util),
        "engine_gpu_mib_max": max(engine_gpu, default=None),
        "other_ollama_models_loaded": sorted(others),
    }


def contamination_notes(c: dict[str, Any]) -> list[str]:
    notes = []
    if c["other_ollama_models_loaded"]:
        notes.append(
            "Ollama had other models loaded during the run (another experiment?): "
            + ", ".join(c["other_ollama_models_loaded"])
        )
    if c["gpu_util_pct_median"] is not None and c["gpu_util_pct_median"] > 50:
        notes.append(
            f"GPU utilisation median {c['gpu_util_pct_median']}% — something else was "
            "using the GPU; latency numbers are not representative"
        )
    if c["gpu_used_mib_max"] is not None and c["gpu_used_mib_max"] > 11000:
        notes.append(
            f"GPU memory peaked at {c['gpu_used_mib_max']} MiB of 12227 MiB; CUDA may "
            "have spilled into shared memory (gpu_mib includes shared usage)"
        )
    return notes


# --------------------------------------------------------------------------- 每輪分析


def analyze_cycles(
    soak: EngineSoak,
    cycles: list[SessionTimes],
    baseline: dict[str, float | None],
    *,
    prefix: str = "",
) -> list[dict[str, Any]]:
    """每輪結束後的值、斜率、顯示記憶體回落時間 → 判定。"""
    report = soak.report
    rows: list[dict[str, Any]] = []
    for i, st in enumerate(cycles):
        assert st.t_idle is not None
        idle = soak.rel(st.t_idle)
        post = soak.metrics(idle + SETTLE_SECS - POST_WINDOW_SECS, idle + SETTLE_SECS)
        row: dict[str, Any] = {"cycle": i, "sid": st.sid, "post": post}
        if st.t_running is not None:
            run0, run1 = soak.rel(st.t_running), soak.rel(st.t_stop or st.t_idle)
            row["load_s"] = round(st.t_running - st.t_request, 2)
            row["peak_gpu_mib"] = soak.peak(run0, run1 + 1, "gpu_mib")
            row["peak_private_mib"] = soak.peak(run0, run1 + 1, "private_mib")
            row["stop_to_idle_s"] = (
                round(st.t_idle - st.t_stop, 3) if st.t_stop is not None else None
            )
        row["idle_rel"] = round(idle, 2)
        rows.append(row)

    steady = rows[WARMUP_CYCLES:] if len(rows) > WARMUP_CYCLES + 2 else rows
    floor = median_or_none(
        [r["post"]["gpu_mib"] for r in steady if r["post"]["gpu_mib"] is not None]
    )
    for r in rows:
        r["gpu_return_s"] = None
        if floor is None:
            continue
        for t, v in soak.monitor.series(
            "engine", "gpu_mib", r["idle_rel"], r["idle_rel"] + SETTLE_SECS
        ):
            if v <= floor + GPU_RETURN_TOL_MIB:
                r["gpu_return_s"] = round(t - r["idle_rel"], 2)
                break

    def pts(key: str) -> list[tuple[float, float]]:
        return [
            (float(r["cycle"]), float(r["post"][key]))
            for r in steady
            if r["post"][key] is not None
        ]

    s_priv = slope(pts("private_mib"))
    s_handles = slope(pts("handles"))
    s_threads = slope(pts("threads"))
    s_gpu = slope(pts("gpu_mib"))
    thread_ref = median_or_none([v for _, v in pts("threads")])
    thread_max = max((v for _, v in pts("threads")), default=None)
    returns = [r["gpu_return_s"] for r in rows]
    late = [
        r["cycle"]
        for r in rows
        if r["gpu_return_s"] is None or r["gpu_return_s"] > GPU_RETURN_SECS
    ]
    residue = (
        None
        if floor is None or baseline.get("gpu_mib") is None
        else floor - float(baseline["gpu_mib"] or 0.0)
    )
    p = prefix
    report.data[f"{p}cycles"] = rows
    report.data[f"{p}baseline"] = baseline
    report.data[f"{p}slopes_per_cycle"] = {
        "private_mib": r1(s_priv, 2),
        "handles": r1(s_handles, 2),
        "threads": r1(s_threads, 3),
        "gpu_mib": r1(s_gpu, 2),
    }
    report.data[f"{p}gpu_post_floor_mib"] = r1(floor)
    first_post = rows[0]["post"] if rows else {}
    last_post = rows[-1]["post"] if rows else {}
    print(
        f"  {p}baseline (before first session): {baseline}\n"
        f"  {p}after cycle 0: {first_post}\n"
        f"  {p}after last cycle: {last_post}\n"
        f"  {p}peak GPU while running: {max((r.get('peak_gpu_mib') or 0) for r in rows):.0f} MiB",
        flush=True,
    )
    report.check(
        f"{p}private bytes slope after warm-up ≤ {PRIVATE_SLOPE_MIB_PER_CYCLE} MiB/cycle",
        s_priv is not None and s_priv <= PRIVATE_SLOPE_MIB_PER_CYCLE,
        f"{r1(s_priv, 2)} MiB/cycle over {len(steady)} cycles "
        f"({pts('private_mib')[0][1] if pts('private_mib') else None} → "
        f"{pts('private_mib')[-1][1] if pts('private_mib') else None} MiB)",
    )
    report.check(
        f"{p}handle count slope ≤ {HANDLE_SLOPE_PER_CYCLE}/cycle",
        s_handles is not None and s_handles <= HANDLE_SLOPE_PER_CYCLE,
        f"{r1(s_handles, 2)}/cycle ({[int(v) for _, v in pts('handles')]})",
    )
    report.check(
        f"{p}threads after stop return to the post-warm-up level (+{THREAD_EXTRA_ALLOWED}), "
        f"slope ≤ {THREAD_SLOPE_PER_CYCLE}",
        thread_ref is not None
        and thread_max is not None
        and thread_max <= thread_ref + THREAD_EXTRA_ALLOWED
        and (s_threads or 0.0) <= THREAD_SLOPE_PER_CYCLE,
        f"baseline {baseline.get('threads')}, post-stop {[int(v) for _, v in pts('threads')]}, "
        f"slope {r1(s_threads, 3)}",
    )
    report.check(
        f"{p}GPU memory (dedicated+shared) slope ≤ {GPU_SLOPE_MIB_PER_CYCLE} MiB/cycle",
        s_gpu is not None and s_gpu <= GPU_SLOPE_MIB_PER_CYCLE,
        f"{r1(s_gpu, 2)} MiB/cycle; post-stop {[int(v) for _, v in pts('gpu_mib')]} MiB",
    )
    report.check(
        f"{p}GPU memory back to the post-stop floor (+{GPU_RETURN_TOL_MIB:.0f} MiB) "
        f"within {GPU_RETURN_SECS:.0f} s of idle",
        not late,
        f"floor {r1(floor)} MiB; return times {returns}; late cycles {late}",
    )
    report.check(
        f"{p}post-stop GPU residue vs. before the first session ≤ {GPU_RESIDUE_MAX_MIB:.0f} MiB",
        residue is not None and residue <= GPU_RESIDUE_MAX_MIB,
        f"{r1(residue)} MiB (floor {r1(floor)} − baseline {baseline.get('gpu_mib')})",
    )
    return rows


# --------------------------------------------------------------------------- 情境 1


def scenario_start_stop(
    cycles: int = 20, session_secs: float = 12.0, which: str = "dev"
) -> int:
    """1. media 模式 start/stop × N（播放日文語音），每個 session 載入／釋放 fast + final ASR。"""
    with EngineSoak("s1-start-stop", which) as soak:
        client = soak.engine
        assert soak.player is not None
        report = soak.report
        report.data["params"] = {"cycles": cycles, "session_secs": session_secs}
        soak.player.start(speech_ja())
        baseline = soak.wait_idle_baseline()
        done: list[SessionTimes] = []
        for i in range(cycles):
            soak.monitor.mark("cycle_start", cycle=i)
            st = start_session(client, device_id=soak.device_id)
            soak.monitor.mark(
                "running",
                cycle=i,
                sid=st.sid,
                load_s=round(st.t_running - st.t_request, 2),
            )  # type: ignore[operator]
            time.sleep(session_secs)
            stop_session(client, st)
            soak.monitor.mark("idle", cycle=i, sid=st.sid)
            counts = subtitle_counts(client, st.sid)
            done.append(st)
            print(
                f"  cycle {i + 1}/{cycles}: sid {st.sid} load "
                f"{st.t_running - st.t_request:.1f} s, {counts}",  # type: ignore[operator]
                flush=True,
            )
            report.data.setdefault("subtitles", []).append(counts)
            time.sleep(SETTLE_SECS)
        soak.player.stop()
        rows = analyze_cycles(soak, done, baseline)
        flowing = sum(1 for c in report.data["subtitles"] if c["finals"] > 0)
        report.check(
            "subtitles flowed (≥ 1 final) in ≥ 80% of sessions",
            flowing >= 0.8 * cycles,
            f"{flowing}/{cycles} sessions",
        )
        report.check(
            "every session reached running and stopped to idle",
            all(st.end_state == "idle" for st in done),
            f"stop→idle s: {[r.get('stop_to_idle_s') for r in rows]}",
        )
        chat = soak.proxy.chat_requests()
        report.data["translation_requests"] = len(chat)
        return 0 if report.passed else 1


# --------------------------------------------------------------------------- 情境 2

SWITCH_CONFIGS: list[dict[str, Any]] = [
    {
        "label": "ja→zh-TW small/large-v3-turbo",
        "clip": "ja",
        "settings": {
            "source_language": "ja",
            "target_language": "zh-TW",
            "asr": {"fast_model": "small", "final_model": "large-v3-turbo"},
        },
    },
    {
        "label": "ja→zh-TW small/large-v3",
        "clip": "ja",
        "settings": {
            "source_language": "ja",
            "target_language": "zh-TW",
            "asr": {"fast_model": "small", "final_model": "large-v3"},
        },
    },
    {
        "label": "en→ko base/small",
        "clip": "en",
        "settings": {
            "source_language": "en",
            "target_language": "ko",
            "asr": {"fast_model": "base", "final_model": "small"},
        },
    },
    {
        "label": "auto→zh-TW tiny/medium",
        "clip": "mix",
        "settings": {
            "source_language": "auto",
            "target_language": "zh-TW",
            "asr": {"fast_model": "tiny", "final_model": "medium"},
        },
    },
]


def scenario_switch(
    rounds: int = 3, session_secs: float = 12.0, which: str = "dev"
) -> int:
    """2. 每個 session 換語言與模型組合，讓不同的模型對輪流載入、釋放。"""
    with EngineSoak("s2-switch", which) as soak:
        client = soak.engine
        assert soak.player is not None
        report = soak.report
        report.data["params"] = {"rounds": rounds, "session_secs": session_secs}
        clips = {
            "ja": speech_ja(),
            "en": speech_en(),
        }
        clips["mix"] = np.concatenate([speech_zh(), clips["en"], clips["ja"]])
        baseline = soak.wait_idle_baseline()
        done: list[SessionTimes] = []
        labels: list[str] = []
        for r in range(rounds):
            for cfg in SWITCH_CONFIGS:
                i = len(done)
                client.request("settings.set", cfg["settings"])
                soak.player.start(clips[cfg["clip"]])
                soak.monitor.mark("cycle_start", cycle=i, config=cfg["label"], round=r)
                st = start_session(client, device_id=soak.device_id)
                time.sleep(session_secs)
                stop_session(client, st)
                soak.monitor.mark("idle", cycle=i, sid=st.sid)
                counts = subtitle_counts(client, st.sid)
                report.data.setdefault("subtitles", []).append({cfg["label"]: counts})
                print(
                    f"  round {r + 1}/{rounds} {cfg['label']}: load "
                    f"{st.t_running - st.t_request:.1f} s, {counts}",  # type: ignore[operator]
                    flush=True,
                )
                done.append(st)
                labels.append(cfg["label"])
                time.sleep(SETTLE_SECS)
        soak.player.stop()
        rows = analyze_cycles(soak, done, baseline)
        per_cfg: dict[str, list[dict[str, Any]]] = {}
        for label, row in zip(labels, rows, strict=True):
            per_cfg.setdefault(label, []).append(
                {
                    "private_mib": row["post"]["private_mib"],
                    "gpu_mib": row["post"]["gpu_mib"],
                    "peak_gpu_mib": row.get("peak_gpu_mib"),
                    "load_s": row.get("load_s"),
                }
            )
        report.data["per_config"] = per_cfg
        if rounds >= 2:
            growth = {
                label: r1(
                    (vals[-1]["private_mib"] or 0)
                    - (vals[1 if rounds > 2 else 0]["private_mib"] or 0)
                )
                for label, vals in per_cfg.items()
            }
            report.check(
                "same config, later round: private bytes growth ≤ 2 × slope threshold",
                all(
                    g is not None
                    and g <= 2 * PRIVATE_SLOPE_MIB_PER_CYCLE * len(SWITCH_CONFIGS)
                    for g in growth.values()
                ),
                f"MiB growth per config (round 2 → last): {growth}",
            )
        return 0 if report.passed else 1


# --------------------------------------------------------------------------- 情境 3


def _expiry_series(soak: EngineSoak, model: str) -> list[tuple[float, float]]:
    out = []
    for s in soak.monitor.samples:
        for m in s.get("ollama_ps") or []:
            if m.get("name") == model:
                ts = _parse_ts(m.get("expires_at"))
                if ts is not None:
                    out.append((s["t"], ts))
    return out


def scenario_translation(
    toggles: int = 20,
    toggle_secs: float = 3.0,
    quiet_secs: float = 35.0,
    which: str = "dev",
) -> int:
    """3. 執行中切換翻譯、只顯示原文的 session、保溫與 Ollama 的到期時間。

    `translation.keep_warm_secs` 設成最小值 10 秒，安靜期間應每 10 秒一次保溫請求。
    """
    keep_warm = 10
    with EngineSoak("s3-translation", which, keep_warm_secs=keep_warm) as soak:
        client = soak.engine
        assert soak.player is not None
        report = soak.report
        proxy = soak.proxy
        report.data["params"] = {
            "toggles": toggles,
            "toggle_secs": toggle_secs,
            "quiet_secs": quiet_secs,
            "keep_warm_secs": keep_warm,
            "model": soak.translation_model,
        }
        speech = speech_ja()
        baseline = soak.wait_idle_baseline()

        # --- A：翻譯開啟的 session，執行中切換 N 次
        soak.player.start(speech)
        st = start_session(client, device_id=soak.device_id)
        soak.monitor.mark("A_running", sid=st.sid)
        time.sleep(10)
        t_toggle0 = soak.monitor.now()
        before_toggles = soak.metrics(t_toggle0 - 4, t_toggle0)
        per_toggle: list[dict[str, Any]] = []
        for k in range(toggles):
            for enabled in (False, True):
                res = client.request("session.set_translation", {"enabled": enabled})
                if res.get("session_id") != st.sid:
                    raise RuntimeError(
                        f"set_translation not applied to {st.sid}: {res}"
                    )
                time.sleep(toggle_secs)
            now = soak.monitor.now()
            m = soak.metrics(now - 2, now)
            per_toggle.append(
                {"k": k, "threads": m["threads"], "handles": m["handles"]}
            )
        t_toggle1 = soak.monitor.now()
        after_toggles = soak.metrics(t_toggle1 - 4, t_toggle1)
        report.data["toggles"] = {
            "before": before_toggles,
            "after": after_toggles,
            "per_toggle": per_toggle,
        }
        th = [p["threads"] for p in per_toggle if p["threads"] is not None]
        hd = [p["handles"] for p in per_toggle if p["handles"] is not None]
        s_th = slope([(float(i), float(v)) for i, v in enumerate(th)])
        s_hd = slope([(float(i), float(v)) for i, v in enumerate(hd)])
        report.check(
            f"translation toggled {toggles}× on/off while running: threads and handles do not grow",
            (s_th or 0) <= 0.1 and (s_hd or 0) <= 2.0,
            f"threads {th[:3]}…{th[-3:]} (slope {r1(s_th, 3)}/toggle), handles slope "
            f"{r1(s_hd, 2)}/toggle",
        )

        # --- 安靜期間（翻譯開啟）：每 keep_warm 秒一次保溫
        soak.player.stop()
        time.sleep(2)
        q0 = time.monotonic()
        soak.monitor.mark("A_quiet_on")
        time.sleep(quiet_secs)
        q1 = time.monotonic()
        pings_on = [r for r in proxy.chat_requests(q0, q1) if r.max_tokens == 32]
        all_on = proxy.chat_requests(q0, q1)
        expected_min = int(quiet_secs // keep_warm) - 2
        report.check(
            f"quiet with translation on: keep-warm requests every {keep_warm} s",
            len(pings_on) >= max(1, expected_min) and len(all_on) <= len(pings_on) + 2,
            f"{len(pings_on)} keep-warm requests in {quiet_secs:.0f} s "
            f"(expected ≥ {max(1, expected_min)}); at "
            f"{[round(r.t - q0, 1) for r in pings_on]} s",
        )

        # --- 安靜期間（翻譯關閉）：不送任何請求
        client.request("session.set_translation", {"enabled": False})
        time.sleep(1.0)
        o0 = time.monotonic()
        soak.monitor.mark("A_quiet_off")
        time.sleep(quiet_secs)
        o1 = time.monotonic()
        off_requests = [r for r in proxy.snapshot() if o0 <= r.t <= o1]
        report.check(
            "quiet with translation off: no requests to the translation server",
            not off_requests,
            f"{len(off_requests)} requests: {[(r.path, r.max_tokens) for r in off_requests][:5]}",
        )

        # --- 再開啟：立即預熱一次
        e0 = time.monotonic()
        client.request("session.set_translation", {"enabled": True})
        time.sleep(4.0)
        warm = proxy.chat_requests(e0, time.monotonic())
        report.check(
            "turning translation back on sends one warm-up request",
            1 <= len(warm) <= 2,
            f"{len(warm)} requests within 4 s ({[round(r.t - e0, 2) for r in warm]} s)",
        )

        # --- 停止：之後不再送請求
        stop_session(client, st)
        soak.monitor.mark("A_idle", sid=st.sid)
        a_idle = st.t_idle
        assert a_idle is not None
        time.sleep(max(quiet_secs, 25.0))
        after_stop = [r for r in proxy.snapshot() if r.t > a_idle + 1.0]
        report.check(
            "after session.stop: no keep-warm or translation requests",
            not after_stop,
            f"{len(after_stop)} requests after idle",
        )
        logs_after = [
            r.msg["data"]["message"][:120]
            for r in log_events(client, a_idle + 1.0)
            if KEEP_WARM_LOG in r.msg["data"]["message"]
            or WARM_UP_LOG in r.msg["data"]["message"]
        ]
        report.data["keep_warm_logs_after_stop"] = logs_after
        idle_a = soak.metrics(
            soak.rel(a_idle) + SETTLE_SECS - POST_WINDOW_SECS,
            soak.rel(a_idle) + SETTLE_SECS,
        )

        # --- B：只顯示原文的 session（設定 translation.enabled=false）
        client.request("session.set_translation", {"enabled": False})
        soak.player.start(speech)
        b0 = time.monotonic()
        st_b = start_session(client, device_id=soak.device_id)
        soak.monitor.mark("B_running", sid=st_b.sid)
        time.sleep(20)
        stop_session(client, st_b)
        soak.player.stop()
        b1 = time.monotonic()
        b_requests = [r for r in proxy.snapshot() if b0 <= r.t <= b1 + 1]
        counts_b = subtitle_counts(client, st_b.sid)
        report.check(
            "translation-disabled session: zero requests to the translation server "
            "(no model check, warm-up or keep-warm)",
            not b_requests
            and counts_b["finals"] > 0
            and counts_b["translated_finals"] == 0,
            f"{len(b_requests)} requests; subtitles {counts_b}",
        )
        time.sleep(SETTLE_SECS)
        assert st_b.t_idle is not None
        idle_b = soak.metrics(
            soak.rel(st_b.t_idle) + SETTLE_SECS - POST_WINDOW_SECS,
            soak.rel(st_b.t_idle) + SETTLE_SECS,
        )
        report.data["post_stop"] = {
            "baseline": baseline,
            "after_A": idle_a,
            "after_B": idle_b,
        }
        report.check(
            f"threads after both sessions back to the level after the first session (+{THREAD_EXTRA_ALLOWED})",
            idle_a["threads"] is not None
            and idle_b["threads"] is not None
            and idle_b["threads"] <= idle_a["threads"] + THREAD_EXTRA_ALLOWED,
            f"baseline {baseline['threads']}, after A {idle_a['threads']}, after B {idle_b['threads']}",
        )
        report.check(
            "GPU memory after B within tolerance of after A",
            idle_a["gpu_mib"] is not None
            and idle_b["gpu_mib"] is not None
            and idle_b["gpu_mib"] <= idle_a["gpu_mib"] + GPU_RETURN_TOL_MIB,
            f"baseline {baseline['gpu_mib']} MiB, after A {idle_a['gpu_mib']}, after B {idle_b['gpu_mib']}",
        )

        # --- Ollama 的到期時間（參考：同機有別人用同一個模型時會被刷新）
        series = _expiry_series(soak, soak.translation_model)
        own = proxy.chat_requests()
        refreshed_without_us = []
        # /api/ps 每 5 秒才查一次（取樣器的慢迴圈），結果掛在之後每秒的樣本上：到期時間
        # 變了的那個樣本，對應的請求最多早 5 秒＋查詢時間，所以往前看 OLLAMA_PS_LAG 秒。
        lag = 8.0
        for (_t_a, e_a), (t_b, e_b) in itertools.pairwise(series):
            if e_b - e_a > 5 and not any(
                t_b - lag <= soak.rel(r.t + (r.secs or 0)) <= t_b for r in own
            ):
                refreshed_without_us.append(round(t_b, 1))
        last_own = max((soak.rel(r.t + (r.secs or 0)) for r in own), default=None)
        last_expiry = series[-1][1] if series else None
        # 相對秒數 → 牆鐘：推算 Ollama 實際的 keep_alive（到期時間 − 最後一個請求結束）
        wall_now, rel_now = time.time(), soak.monitor.now()
        keep_alive = (
            None
            if last_own is None or last_expiry is None
            else last_expiry - (wall_now - (rel_now - last_own))
        )
        report.data["ollama"] = {
            "expiry_samples": len(series),
            "refreshed_without_our_requests_at": refreshed_without_us[:20],
            "last_expires_at_epoch": last_expiry,
            "last_own_request_rel": r1(last_own),
            "inferred_keep_alive_s": r1(keep_alive),
        }
        if refreshed_without_us:
            report.note(
                f"Ollama expiry of {soak.translation_model} was refreshed {len(refreshed_without_us)}× "
                "without a request from the engine: another client is using the same model, so "
                "Ollama-side keep-alive/unload cannot be attributed (engine-side checks via the "
                "counting proxy are unaffected)"
            )
        elif series:
            report.note(
                "Ollama expiry was only refreshed by the engine's own requests; the model "
                f"will unload ≈ {datetime.fromtimestamp(last_expiry).isoformat(timespec='seconds') if last_expiry else '?'}"
            )
        return 0 if report.passed else 1


# --------------------------------------------------------------------------- 情境 4


def scenario_long(minutes: float = 20.0, which: str = "dev") -> int:
    """4. 一個長時間 session：人聲＋BGM 循環播放；記憶體、執行緒、handle 要持平。"""
    with EngineSoak("s4-long", which) as soak:
        client = soak.engine
        assert soak.player is not None
        report = soak.report
        report.data["params"] = {"minutes": minutes}
        audio = with_bgm(speech_ja())
        baseline = soak.wait_idle_baseline()
        # 暖機 session：讓一次性的常駐成本（CUDA context 等）先發生，結束後的值才有比較基準
        soak.player.start(audio)
        warm = start_session(client, device_id=soak.device_id)
        time.sleep(20)
        stop_session(client, warm)
        time.sleep(SETTLE_SECS)
        assert warm.t_idle is not None
        w_idle = soak.rel(warm.t_idle)
        post_warm = soak.metrics(
            w_idle + SETTLE_SECS - POST_WINDOW_SECS, w_idle + SETTLE_SECS
        )

        st = start_session(client, device_id=soak.device_id)
        assert st.t_running is not None
        r0 = soak.rel(st.t_running)
        soak.monitor.mark("long_running", sid=st.sid)
        end = time.monotonic() + minutes * 60
        last_print = 0.0
        while time.monotonic() < end:
            time.sleep(5)
            if soak.player.error:
                raise RuntimeError(f"playback failed: {soak.player.error}")
            if time.monotonic() - last_print >= 60:
                last_print = time.monotonic()
                now = soak.monitor.now()
                m = soak.metrics(now - 5, now)
                c = subtitle_counts(client, st.sid)
                print(
                    f"  {(now - r0) / 60:5.1f} min: private {m['private_mib']} MiB, "
                    f"threads {m['threads']}, handles {m['handles']}, GPU {m['gpu_mib']} MiB, "
                    f"finals {c['finals']}",
                    flush=True,
                )
            if client.proc.poll() is not None:
                raise RuntimeError("engine exited during the long session")
        stop_session(client, st)
        soak.player.stop()
        assert st.t_stop is not None and st.t_idle is not None
        r1_ = soak.rel(st.t_stop)
        time.sleep(SETTLE_SECS)
        idle = soak.rel(st.t_idle)
        post = soak.metrics(idle + SETTLE_SECS - POST_WINDOW_SECS, idle + SETTLE_SECS)
        counts = subtitle_counts(client, st.sid)

        warm_s = max(60.0, 0.2 * (r1_ - r0))
        w0 = r0 + warm_s

        def per_min(key: str) -> tuple[float | None, list[tuple[float, float]]]:
            pts = [
                (t / 60.0, v) for t, v in soak.monitor.series("engine", key, w0, r1_)
            ]
            return slope(pts), pts

        s_priv, p_priv = per_min("private_mib")
        s_hd, _ = per_min("handles")
        s_gpu, p_gpu = per_min("gpu_mib")
        _, p_th = per_min("threads")
        th_vals = [v for _, v in p_th]

        def quarter_medians(pts: list[tuple[float, float]]) -> list[float | None]:
            n = len(pts)
            return [
                r1(median_or_none([v for _, v in pts[n * q // 4 : n * (q + 1) // 4]]))
                for q in range(4)
            ]

        report.data["long"] = {
            "running_minutes": round((r1_ - r0) / 60, 2),
            "warm_up_excluded_s": round(warm_s, 1),
            "subtitles": counts,
            "baseline": baseline,
            "post_warm_session": post_warm,
            "post_long_session": post,
            "private_quarters_mib": quarter_medians(p_priv),
            "gpu_quarters_mib": quarter_medians(p_gpu),
            "threads_min_max": [min(th_vals, default=None), max(th_vals, default=None)],
            "peak_private_mib": soak.peak(r0, r1_, "private_mib"),
            "peak_gpu_mib": soak.peak(r0, r1_, "gpu_mib"),
            "translation_requests": len(soak.proxy.chat_requests()),
            "warnings": warnings_in(client, st.t_running, st.t_idle)[:20],
        }
        print(
            f"  long: {json.dumps(report.data['long'], ensure_ascii=False)[:800]}",
            flush=True,
        )
        report.check(
            "subtitles kept flowing for the whole session",
            counts["finals"] >= (r1_ - r0) / 60 * 3,
            f"{counts}",
        )
        # 執行中私有記憶體會隨每句的 encode／decode 上下跳動約 ±80 MiB（5 分鐘的實測），
        # 短時間的最小平方斜率被跳動主導；改以四分位中位數判定持平：後半段兩個四分位
        # 中位數的差 ≤ 斜率門檻 × 兩者相隔的分鐘數（至少 8 MiB）。斜率照樣記錄。
        q = report.data["long"]["private_quarters_mib"]
        span_min = (r1_ - w0) / 60 / 2
        allowed = max(8.0, LONG_PRIVATE_SLOPE_MIB_PER_MIN * span_min)
        growth = None if q[1] is None or q[3] is None else q[3] - q[1]
        report.check(
            f"private bytes plateau after warm-up: median(Q4) − median(Q2) ≤ "
            f"{LONG_PRIVATE_SLOPE_MIB_PER_MIN} MiB/min × {span_min:.1f} min (≥ 8 MiB)",
            growth is not None and growth <= allowed,
            f"Q4 − Q2 = {r1(growth)} MiB (allowed {allowed:.1f}); quarter medians {q}; "
            f"least-squares slope {r1(s_priv, 3)} MiB/min (informational)",
        )
        report.check(
            f"handle count slope ≤ {LONG_HANDLE_SLOPE_PER_MIN}/min",
            s_hd is not None and s_hd <= LONG_HANDLE_SLOPE_PER_MIN,
            f"{r1(s_hd, 3)}/min",
        )
        report.check(
            f"thread count range after warm-up ≤ {LONG_THREAD_RANGE}",
            bool(th_vals) and max(th_vals) - min(th_vals) <= LONG_THREAD_RANGE,
            f"{min(th_vals, default=None)}–{max(th_vals, default=None)}",
        )
        report.check(
            f"GPU memory slope ≤ {LONG_GPU_SLOPE_MIB_PER_MIN} MiB/min",
            s_gpu is not None and s_gpu <= LONG_GPU_SLOPE_MIB_PER_MIN,
            f"{r1(s_gpu, 3)} MiB/min; quarter medians {report.data['long']['gpu_quarters_mib']}",
        )
        report.check(
            "after stop: GPU memory back to the level after the warm-up session",
            post["gpu_mib"] is not None
            and post_warm["gpu_mib"] is not None
            and post["gpu_mib"] <= post_warm["gpu_mib"] + GPU_RETURN_TOL_MIB,
            f"after warm-up session {post_warm['gpu_mib']} MiB, after long session {post['gpu_mib']} MiB",
        )
        report.check(
            f"after stop: private bytes ≤ after-warm-up + {LONG_POST_PRIVATE_GROWTH_MIB:.0f} MiB, "
            "threads back",
            post["private_mib"] is not None
            and post_warm["private_mib"] is not None
            and post["private_mib"]
            <= post_warm["private_mib"] + LONG_POST_PRIVATE_GROWTH_MIB
            and post["threads"] is not None
            and post_warm["threads"] is not None
            and post["threads"] <= post_warm["threads"] + THREAD_EXTRA_ALLOWED,
            f"private {post_warm['private_mib']} → {post['private_mib']} MiB, threads "
            f"{post_warm['threads']} → {post['threads']}",
        )
        return 0 if report.passed else 1


# --------------------------------------------------------------------------- 情境 5


def _installed_ollama_models() -> list[dict[str, Any]]:
    with urllib.request.urlopen(f"{OLLAMA_BASE}/api/tags", timeout=5) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [
        {"name": m.get("name"), "size": m.get("size"), "digest": m.get("digest")}
        for m in data.get("models", [])
        if isinstance(m, dict)
    ]


def _wait_download(
    client: EngineClient, model: str, kind: str, since: int, timeout: float
) -> dict[str, Any]:
    rec = client.wait_event(
        lambda m: (
            m.get("event") == "download_progress"
            and m["data"]["model"] == model
            and m["data"].get("kind") == kind
            and m["data"]["state"] in ("done", "error")
        ),
        timeout=timeout,
        since=since,
    )
    return dict(rec.msg["data"])


def scenario_downloads(
    repeats: int = 3, pulls: int = 5, which: str = "dev", model: str = "tiny"
) -> int:
    """5. ASR 模型下載的取消／續傳／重複下載（隔離的模型快取），與翻譯模型下載 × N。"""
    with EngineSoak("s5-downloads", which, player=False, engine=False) as soak:
        report = soak.report
        local = soak.run_dir / "localappdata"
        local.mkdir()
        cache = local / "kikitori" / "models"
        env = {"LOCALAPPDATA": str(local)}
        soak.extra_env = env
        report.data["params"] = {"repeats": repeats, "pulls": pulls, "model": model}

        # --- 取消：下載途中 engine.shutdown
        client = soak.start_engine(configure=False)
        client.request(
            "settings.set", {"translation": {"base_url": soak.proxy.base_url}}
        )
        since = len(client.events)
        client.request("models.download", {"model": model})
        try:
            prog = client.wait_event(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["model"] == model
                    and (
                        m["data"]["state"] != "downloading"
                        or (
                            m["data"]["total_bytes"]
                            and m["data"]["downloaded_bytes"]
                            >= 0.1 * m["data"]["total_bytes"]
                        )
                    )
                ),
                timeout=180,
                since=since,
            )
            at = prog.msg["data"]
        except TimeoutError:
            at = {"state": "timeout"}
        t_cancel = time.monotonic()
        code = soak.close_engine()
        exit_s = time.monotonic() - t_cancel
        cancel_events = [
            r.msg["data"]
            for r in client.events[since:]
            if r.msg.get("event") == "download_progress"
            and r.msg["data"]["state"] != "downloading"
        ]
        parts = (
            [str(p.relative_to(local)) for p in cache.rglob("*.part")]
            if cache.exists()
            else []
        )
        report.data["cancel"] = {
            "cancelled_at": at,
            "end_events": cancel_events,
            "exit_code": code,
            "exit_s": round(exit_s, 2),
            "part_files": parts,
        }
        time.sleep(2)
        left = soak.monitor.leftover("engine")
        cancelled = any("Cancel" in str(e.get("error")) for e in cancel_events)
        report.check(
            "engine.shutdown mid-download: exits 0 promptly, download cancelled, .part kept, "
            "no processes left",
            code == 0
            and exit_s < 15
            and not left
            and (cancelled or at.get("state") == "done"),
            f"exit {code} in {exit_s:.1f} s at {at.get('downloaded_bytes')}/{at.get('total_bytes')} "
            f"bytes; end events {[(e['state'], str(e.get('error'))[:60]) for e in cancel_events]}; "
            f".part {parts}; left {left}",
        )
        soak.monitor.forget("engine")

        # --- 續傳＋重複下載
        client = soak.start_engine(configure=False)
        client.request(
            "settings.set", {"translation": {"base_url": soak.proxy.base_url}}
        )
        base = soak.wait_idle_baseline(6)
        after: list[dict[str, Any]] = []
        for i in range(repeats + 1):
            if i > 0:
                target = (cache / model).resolve()
                if not str(target).startswith(str(local.resolve())):
                    raise RuntimeError(f"refusing to delete {target}")
                shutil.rmtree(target)
            since = len(client.events)
            client.request("models.download", {"model": model})
            first = client.wait_event(
                lambda m: (
                    m.get("event") == "download_progress"
                    and m["data"]["model"] == model
                ),
                timeout=60,
                since=since,
            ).msg["data"]
            end = _wait_download(client, model, "asr", since, timeout=600)
            time.sleep(4)
            now = soak.monitor.now()
            m = soak.metrics(now - 2.5, now)
            after.append(
                {
                    "i": i,
                    "first_event_bytes": first["downloaded_bytes"],
                    "state": end["state"],
                    "error": end.get("error"),
                    **{k: m[k] for k in ("threads", "handles", "private_mib")},
                }
            )
            print(f"  download {i + 1}/{repeats + 1}: {after[-1]}", flush=True)
        report.data["downloads"] = {"baseline": base, "after_each": after}
        report.check(
            "resume after cancel and repeated downloads all finish",
            all(a["state"] == "done" for a in after),
            f"{[(a['state'], a['error']) for a in after]}; .part at restart: "
            f"{report.data['cancel']['part_files']}",
        )
        th = [a["threads"] for a in after if a["threads"] is not None]
        hd = [a["handles"] for a in after if a["handles"] is not None]
        # 第一次下載（續傳）會初始化 HTTPS（SSL context、憑證存放區、DNS），handle 一次性增加；
        # 以它之後的值為基準，看之後每次下載有沒有再增加。
        ref_th = th[0] if th else None
        ref_hd = hd[0] if hd else None
        report.check(
            "no thread/handle growth across repeated downloads (reference: after the first)",
            len(th) >= 2
            and ref_th is not None
            and ref_hd is not None
            and max(th[1:]) <= ref_th + THREAD_EXTRA_ALLOWED
            and max(hd[1:]) <= ref_hd + 4,
            f"before any download: threads {base['threads']} handles {base['handles']}; "
            f"after each: threads {th}, handles {hd}",
        )

        # --- 翻譯模型下載（已安裝的最小模型；Ollama 只檢查 manifest）
        installed = sorted(
            (m for m in _installed_ollama_models() if m["size"]),
            key=lambda m: m["size"],
        )
        if pulls <= 0:
            report.note("models.pull_translation skipped (pulls=0)")
        elif not installed:
            report.note("no Ollama models installed; skipping models.pull_translation")
        else:
            pick = installed[0]
            pulls_out: list[dict[str, Any]] = []
            p_base = soak.metrics(soak.monitor.now() - 3, soak.monitor.now())
            for i in range(pulls):
                since = len(client.events)
                t0 = time.monotonic()
                client.request(
                    "models.pull_translation", {"model": pick["name"]}, timeout=60
                )
                end = _wait_download(
                    client, pick["name"], "translation", since, timeout=900
                )
                time.sleep(3)
                now = soak.monitor.now()
                m = soak.metrics(now - 2, now)
                pulls_out.append(
                    {
                        "i": i,
                        "state": end["state"],
                        "error": end.get("error"),
                        "secs": round(time.monotonic() - t0 - 3, 2),
                        **{k: m[k] for k in ("threads", "handles", "private_mib")},
                    }
                )
                print(
                    f"  pull {i + 1}/{pulls} {pick['name']}: {pulls_out[-1]}",
                    flush=True,
                )
            digest_after = next(
                (
                    m["digest"]
                    for m in _installed_ollama_models()
                    if m["name"] == pick["name"]
                ),
                None,
            )
            report.data["pulls"] = {
                "model": pick,
                "baseline": p_base,
                "after_each": pulls_out,
                "digest_unchanged": digest_after == pick["digest"],
            }
            if digest_after != pick["digest"]:
                report.note(f"{pick['name']} was updated by the pull (digest changed)")
            th = [p["threads"] for p in pulls_out if p["threads"] is not None]
            hd = [p["handles"] for p in pulls_out if p["handles"] is not None]
            report.check(
                f"models.pull_translation × {pulls} ({pick['name']}): all done, no thread/handle growth",
                all(p["state"] == "done" for p in pulls_out)
                and bool(th)
                and max(th) <= (p_base["threads"] or 0) + 1
                and (slope([(float(i), float(v)) for i, v in enumerate(hd)]) or 0)
                <= 2.0,
                f"states {[p['state'] for p in pulls_out]}; threads {p_base['threads']} → {th}; "
                f"handles {p_base['handles']} → {hd}",
            )
        return 0 if report.passed else 1


# --------------------------------------------------------------------------- 情境 6


def _expect_error(
    client: EngineClient, method: str, params: dict[str, Any], code: str
) -> str:
    try:
        client.request(method, params, timeout=30)
    except EngineError as exc:
        return "ok" if exc.code == code else f"got {exc.code}"
    return "no error"


def _engine_lifecycle(soak: EngineSoak, how: str) -> dict[str, Any]:
    """在 running 的 session 中以 shutdown／stdin EOF／強制結束關掉引擎，檢查殘留行程。"""
    client = soak.start_engine()
    assert soak.player is not None
    st = start_session(client, device_id=soak.device_id)
    time.sleep(5)
    t0 = time.monotonic()
    proc = client.proc
    if how == "shutdown":
        code: int | None = soak.close_engine()
    else:
        if how == "eof":
            assert proc.stdin is not None
            proc.stdin.close()
        else:  # kill：TerminateProcess 最上層的行程（開發版是 venv 的 python 啟動器）
            proc.kill()
        try:
            code = proc.wait(30)
        except Exception as exc:  # noqa: BLE001 - 記錄任何等待失敗，交給判定
            logger.error("engine did not exit after %s: %s", how, exc)
            code = None
        soak.client = None
        soak.monitor.mark("engine_closed", how=how, code=code)
    exit_s = time.monotonic() - t0
    time.sleep(3)
    left = soak.monitor.leftover("engine")
    killed: list[int] = []
    if left:
        # 殘留的是這次測試自己啟動的引擎行程：記錄後結束它，免得影響之後的情境
        import os
        import signal

        for p in left:
            try:
                os.kill(p["pid"], signal.SIGTERM)
                killed.append(p["pid"])
            except OSError as exc:
                logger.error("cannot terminate leftover %s: %s", p, exc)
    soak.monitor.forget("engine")
    return {
        "how": how,
        "sid": st.sid,
        "exit_code": code,
        "exit_s": round(exit_s, 2),
        "leftover": left,
        "terminated_leftover": killed,
    }


def scenario_errors(rounds: int = 3, which: str = "dev") -> int:
    """6. 錯誤路徑：不合法的裝置、啟動失敗、載入中停止、重複開始、引擎重啟；不留下行程。"""
    with EngineSoak("s6-errors", which) as soak:
        client = soak.engine
        assert soak.player is not None
        report = soak.report
        report.data["params"] = {"rounds": rounds}
        good = base_settings(
            soak.device_id, soak.proxy.base_url, soak.translation_model
        )
        soak.player.start(speech_ja())
        baseline = soak.wait_idle_baseline()
        results: list[dict[str, Any]] = []
        steps: list[dict[str, Any]] = []

        def after_step(r: int, name: str, outcome: str) -> None:
            """每一步之後等 SETTLE_SECS 秒，記錄結束後的值（找出是哪一條錯誤路徑留下資源）。"""
            time.sleep(SETTLE_SECS)
            now = soak.monitor.now()
            post = soak.metrics(now - POST_WINDOW_SECS, now)
            prev = steps[-1]["post"] if steps else baseline
            steps.append({"round": r, "step": name, "outcome": outcome, "post": post})
            soak.monitor.mark("step", round=r, step=name, outcome=outcome, **post)
            print(
                f"  round {r + 1} {name:22s} {outcome:34s} GPU {prev['gpu_mib']} -> "
                f"{post['gpu_mib']} MiB, private {prev['private_mib']} -> "
                f"{post['private_mib']} MiB, threads {post['threads']}, "
                f"handles {post['handles']}",
                flush=True,
            )

        for r in range(rounds):
            res: dict[str, Any] = {"round": r}
            # a. 格式錯誤的裝置 id：同步回 device_not_found
            res["bad_device_format"] = _expect_error(
                client,
                "session.start",
                {"mode": "media", "device_id": "not-a-device"},
                "device_not_found",
            )
            after_step(r, "bad_device_format", res["bad_device_format"])
            # b. 格式正確但不存在的裝置：載入模型後開裝置失敗 → status error
            st = start_session(
                client,
                device_id="wasapi:loopback:999999",
                expect_running=False,
                timeout=240,
            )
            if st.end_state is None:
                stop_session(client, st)
            res["missing_device"] = f"{st.end_state}/{st.end_code}"
            after_step(r, "missing_device", res["missing_device"])
            # c. 未知的 ASR 模型（即時模型先載入成功、完整模型失敗）
            client.request("settings.set", {"asr": {"final_model": "no-such-model"}})
            st = start_session(client, device_id=soak.device_id, expect_running=False)
            res["unknown_model"] = f"{st.end_state}/{st.end_code}"
            if st.end_state is None:  # 不該開始卻開始了：停掉，繼續下一項
                stop_session(client, st)
            client.request("settings.set", good)
            after_step(r, "unknown_model", res["unknown_model"])
            # d. 翻譯模型沒裝
            client.request(
                "settings.set",
                {
                    "translation": {
                        "fast_model": "kikitori-soak-missing:0b",
                        "final_model": "kikitori-soak-missing:0b",
                    }
                },
            )
            st = start_session(client, device_id=soak.device_id, expect_running=False)
            res["translation_model_missing"] = f"{st.end_state}/{st.end_code}"
            if st.end_state is None:  # 不該開始卻開始了：停掉，繼續下一項
                stop_session(client, st)
            client.request("settings.set", good)
            after_step(r, "translation_model_missing", res["translation_model_missing"])
            # e. 載入中停止（等被停止的 session 的載入執行緒做完）
            since = len(client.events)
            sid = int(
                client.request(
                    "session.start", {"mode": "media", "device_id": soak.device_id}
                )["session_id"]
            )
            time.sleep(0.5)
            client.request("session.stop")
            idle = client.wait_event(
                lambda m, sid=sid: (
                    m.get("event") == "status"
                    and m["data"].get("session_id") == sid
                    and m["data"]["state"] in ("idle", "error")
                ),
                timeout=30,
                since=since,
            )
            res["stop_while_loading"] = str(idle.msg["data"]["state"])
            marker = f"session {sid} stopped while loading"
            try:
                client.wait_event(
                    lambda m, marker=marker: (
                        m.get("event") == "log" and marker in m["data"]["message"]
                    ),
                    timeout=120,
                    since=since,
                )
                res["stop_while_loading"] += ", loader finished"
            except TimeoutError:
                res["stop_while_loading"] += ", no 'stopped while loading' log in 120 s"
            after_step(r, "stop_while_loading", res["stop_while_loading"])
            # f. 正常開始；執行中再開始：session_active；停止
            st2 = start_session(client, device_id=soak.device_id)
            res["session_active"] = _expect_error(
                client,
                "session.start",
                {"mode": "media", "device_id": soak.device_id},
                "session_active",
            )
            time.sleep(4)
            stop_session(client, st2)
            load_s = (st2.t_running or st2.t_request) - st2.t_request
            after_step(
                r,
                "normal+session_active",
                f"{res['session_active']}, load {load_s:.1f} s",
            )
            results.append(res)
        report.data["rounds"] = results
        report.data["steps"] = steps
        report.data["baseline"] = baseline
        expected = {
            "bad_device_format": "ok",
            "missing_device": "error/device_not_found",
            "unknown_model": "error/unknown_model",
            "translation_model_missing": "error/translation_model_missing",
            "session_active": "ok",
        }
        report.check(
            "error paths return the documented codes",
            all(all(res[k] == v for k, v in expected.items()) for res in results),
            json.dumps(results, ensure_ascii=False),
        )
        # 每一步之後顯示記憶體都要回到前一步的水準（容許 #1 的每個模型約 8 MiB 與量測誤差）；
        # 第一個載入模型的步驟會建立 CUDA context（一次性），從第二步開始比較。
        grew: list[str] = []
        prev_gpu = baseline["gpu_mib"] or 0.0
        prev_priv = baseline["private_mib"] or 0.0
        for i, stp in enumerate(steps):
            gpu = stp["post"]["gpu_mib"] or 0.0
            priv = stp["post"]["private_mib"] or 0.0
            if i > 1 and gpu - prev_gpu > GPU_RETURN_TOL_MIB:
                grew.append(
                    f"round {stp['round'] + 1} {stp['step']}: GPU +{gpu - prev_gpu:.0f} MiB, "
                    f"private +{priv - prev_priv:.0f} MiB"
                )
            prev_gpu, prev_priv = gpu, priv
        report.check(
            f"GPU memory after every error path returns to the previous level "
            f"(+{GPU_RETURN_TOL_MIB:.0f} MiB)",
            not grew,
            "; ".join(grew) or "all steps within tolerance",
        )
        per_round = len(steps) // max(1, rounds)
        ref = steps[per_round - 1]["post"]
        last = steps[-1]["post"]
        report.check(
            "threads/handles after the last round stay at the round-1 level",
            last["threads"] is not None
            and ref["threads"] is not None
            and last["threads"] <= ref["threads"] + THREAD_EXTRA_ALLOWED
            and (last["handles"] or 0)
            <= (ref["handles"] or 0) + 2 * HANDLE_SLOPE_PER_CYCLE * 6 * rounds,
            f"baseline {baseline}; after round 1 {ref}; after round {rounds} {last}",
        )
        soak.close_engine()
        soak.monitor.forget("engine")

        # --- 引擎重啟：shutdown、stdin EOF、強制結束（running session 中）
        life = [_engine_lifecycle(soak, how) for how in ("shutdown", "eof", "kill")]
        report.data["lifecycle"] = life
        for item in life:
            if item["how"] == "kill":
                report.check(
                    "engine killed (TerminateProcess) mid-session: no orphan processes",
                    not item["leftover"],
                    json.dumps(item),
                )
            else:
                report.check(
                    f"engine {item['how']} mid-session: exit 0 within 15 s, no orphan processes",
                    item["exit_code"] == 0
                    and item["exit_s"] < 15
                    and not item["leftover"],
                    json.dumps(item),
                )
        soak.player.stop()
        return 0 if report.passed else 1


SCENARIOS = {
    "start-stop": scenario_start_stop,
    "switch": scenario_switch,
    "translation": scenario_translation,
    "long": scenario_long,
    "downloads": scenario_downloads,
    "errors": scenario_errors,
}
