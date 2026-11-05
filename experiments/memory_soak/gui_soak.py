"""7. 桌面程式長時間執行：以 WebDriver 操作正式版建置，字幕持續流動，量測 app 與 WebView2。

暫時性實驗，不屬於正式程式碼。沿用 `experiments/e2e_acceptance/gui_acceptance.py` 的
`AppSession`（tauri-driver + msedgedriver、只用標準庫的 HTTP）。工具以環境變數指定：
`KIKITORI_E2E_APP`（kikitori-app.exe，旁邊要有 `kikitori-engine/`）、`KIKITORI_E2E_TAURI_DRIVER`、
`KIKITORI_E2E_EDGEDRIVER`；找不到就略過（SKIP）並說明怎麼準備。

量測：
- app 行程樹（kikitori-app.exe、它的 WebView2 子行程 msedgewebview2.exe、kikitori-engine.exe）
  依執行檔名稱分開彙總私有記憶體與 handle 數。
- 主視窗與字幕視窗的 DOM 節點數（`document.getElementsByTagName('*').length`）、字幕紀錄的
  列數（上限 300，`app/src/main/main.ts` 的 `TRANSCRIPT_MAX_LINES`）與 JS heap。
- 期間每隔幾分鐘切換「顯示翻譯」、切換介面語系、關閉再打開字幕視窗。
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from harness import (
    DEFAULT_TRANSLATION_MODEL,
    SETTLE_SECS,
    LoopPlayer,
    Report,
    median_or_none,
    new_run_dir,
    r1,
    slope,
    speech_ja,
    with_bgm,
)
from monitor import OLLAMA_BASE, OLLAMA_NAMES, Monitor
from proxy import CountingProxy

logger = logging.getLogger("soak.gui")

TRANSCRIPT_MAX_LINES = 300
APP_PRIVATE_SLOPE_MIB_PER_MIN = 1.0
WEBVIEW_PRIVATE_SLOPE_MIB_PER_MIN = 2.0
"""WebView2（所有子行程合計）暖機後的斜率上限；字幕紀錄到 300 列之前 DOM 會正常成長，
所以只在到達上限之後的區間判定（沒到上限時只判定「與列數無關的 DOM 成長」）。"""
DOM_RESIDUAL_SLOPE_PER_MIN = 2.0
"""DOM 節點數扣掉「列數 × 每列節點數」後的斜率上限（節點／分）：與字幕列數無關的成長＝洩漏。"""
OVERLAY_DOM_RANGE = 80
"""字幕視窗最多同時顯示幾句（設定），DOM 節點數的變化範圍應該很小。"""
LOCALES = ("en", "ja", "zh-TW")

DOM_JS = """
const t = document.getElementById('transcript');
const m = (performance && performance.memory) ? performance.memory.usedJSHeapSize : null;
return {
  nodes: document.getElementsByTagName('*').length,
  lines: t ? t.querySelectorAll('li').length : null,
  overlayLines: document.querySelectorAll('#lines .line').length,
  heap: m,
  start: (document.getElementById('start') || {dataset: {}}).dataset.state || null,
};
"""


def _tools_available() -> tuple[bool, str]:
    try:
        from gui_acceptance import DEFAULT_APP, tool_path

        app = tool_path("KIKITORI_E2E_APP", DEFAULT_APP)
        tool_path("KIKITORI_E2E_TAURI_DRIVER")
        tool_path("KIKITORI_E2E_EDGEDRIVER")
    except (FileNotFoundError, ImportError) as exc:
        return False, str(exc)
    return True, str(app)


def _by_name(sample: dict[str, Any], name: str) -> tuple[float, int]:
    g = sample["groups"].get("app")
    if not g:
        return 0.0, 0
    priv = sum(
        p["private_mib"] for p in g["procs"].values() if p["name"].lower() == name
    )
    handles = sum(
        p["handles"] or 0 for p in g["procs"].values() if p["name"].lower() == name
    )
    return priv, handles


def scenario_gui(
    minutes: float = 30.0, translation_model: str = DEFAULT_TRANSLATION_MODEL
) -> int:
    """長時間執行桌面程式（預設 30 分鐘：每分鐘約 13 句，約 23 分鐘到達 300 列上限）。"""
    ok, why = _tools_available()
    if not ok:
        print(
            "SKIP: GUI soak needs a built app and WebDriver tools.\n"
            f"  reason: {why}\n"
            "  set KIKITORI_E2E_APP, KIKITORI_E2E_TAURI_DRIVER, KIKITORI_E2E_EDGEDRIVER "
            "(see experiments/e2e_acceptance/README.md and experiments/memory_soak/README.md)",
            flush=True,
        )
        return 2
    from gui_acceptance import AppSession, Run, app_pids

    run_dir = new_run_dir("s7-gui")
    report = Report("s7-gui", run_dir)
    config_dir = run_dir / "config"
    config_dir.mkdir()
    proxy = CountingProxy(OLLAMA_BASE)
    proxy.start()
    # 預先寫好設定：GPU 組合、翻譯經過計數代理（不是第一次啟動，不會自動套用推薦）
    settings = {
        "schema_version": 3,
        "mode": "media",
        "source_language": "ja",
        "target_language": "zh-TW",
        "asr": {
            "fast_model": "small",
            "final_model": "large-v3-turbo",
            "device": "cuda",
            "fast_compute_type": "float16",
            "final_compute_type": "float16",
        },
        "translation": {
            "enabled": True,
            "base_url": proxy.base_url,
            "fast_model": translation_model,
            "final_model": translation_model,
        },
    }
    (config_dir / "settings.json").write_text(json.dumps(settings, indent=2), "utf-8")
    monitor = Monitor(run_dir / "samples.ndjson")
    monitor.track_names("ollama", OLLAMA_NAMES)
    player = LoopPlayer()
    dom: list[dict[str, Any]] = []
    actions: list[dict[str, Any]] = []
    run = Run.__new__(Run)  # AppSession 只用到 run.dir（tauri-driver 的日誌位置）
    run.dir = run_dir
    run.steps = {}
    print(f"results: {run_dir}", flush=True)
    monitor.start()
    try:
        with AppSession(run, config_dir, "soak") as app:
            wd = app.wd
            pid = app_pids(app.app.name)
            if len(pid) != 1:
                raise RuntimeError(f"expected one app process, got {pid}")
            monitor.track_root("app", pid[0])
            app.wait_ready()
            main = app.window("kikitori")
            overlay = app.window("kikitori 字幕")
            wd.switch(main)
            device = app.choose_virtual_loopback()
            report.data["device"] = device
            player.start(with_bgm(speech_ja()))
            wd.click("#start")
            wd.wait(
                "running", lambda: wd.script(DOM_JS)["start"] == "running", timeout=240
            )
            t_running = monitor.now()
            monitor.mark("running")
            end = time.monotonic() + minutes * 60
            next_dom = next_toggle = next_overlay = next_locale = time.monotonic()
            next_toggle += 120
            next_overlay += 180
            next_locale += 300
            locale_i = 0
            show_translation = True
            while time.monotonic() < end:
                now = time.monotonic()
                if now >= next_dom:
                    next_dom = now + 20
                    wd.switch(main)
                    d_main = wd.script(DOM_JS)
                    wd.switch(overlay)
                    d_ov = wd.script(DOM_JS)
                    wd.switch(main)
                    rec = {
                        "t": round(monitor.now(), 1),
                        "main_nodes": d_main["nodes"],
                        "lines": d_main["lines"],
                        "main_heap": d_main["heap"],
                        "overlay_nodes": d_ov["nodes"],
                        "overlay_lines": d_ov["overlayLines"],
                        "overlay_heap": d_ov["heap"],
                    }
                    dom.append(rec)
                    if len(dom) % 3 == 1:
                        print(
                            f"  {(monitor.now() - t_running) / 60:5.1f} min: {rec}",
                            flush=True,
                        )
                if now >= next_toggle:
                    next_toggle = now + 120
                    wd.switch(main)
                    wd.click("#show-translation")
                    show_translation = not show_translation
                    actions.append(
                        {
                            "t": round(monitor.now(), 1),
                            "show_translation": show_translation,
                        }
                    )
                if now >= next_overlay:
                    next_overlay = now + 180
                    wd.switch(main)
                    wd.click("#overlay-visible")
                    time.sleep(5)
                    wd.click("#overlay-visible")
                    actions.append(
                        {"t": round(monitor.now(), 1), "overlay": "hidden+shown"}
                    )
                if now >= next_locale:
                    next_locale = now + 300
                    wd.switch(main)
                    app.show_page("settings")
                    loc = LOCALES[locale_i % len(LOCALES)]
                    locale_i += 1
                    wd.script(
                        "const s=document.getElementById('f-ui-locale');"
                        "s.value=arguments[0]; s.dispatchEvent(new Event('change'));",
                        loc,
                    )
                    time.sleep(2)
                    app.show_page("transcript")
                    actions.append({"t": round(monitor.now(), 1), "locale": loc})
                time.sleep(1)
            t_stop = monitor.now()
            wd.switch(main)
            if not show_translation:
                wd.click("#show-translation")
            wd.click("#start")
            wd.wait("idle", lambda: wd.script(DOM_JS)["start"] == "idle", timeout=60)
            player.stop()
            time.sleep(SETTLE_SECS)
            t_after = monitor.now()
            # 結束後：殘留行程在 AppSession 結束（關閉 app）後檢查
    finally:
        player.close()
        time.sleep(3)
    leftover = monitor.leftover("app")
    monitor.stop()
    proxy.stop()

    # --- 分析
    warm = t_running + max(120.0, 0.2 * (t_stop - t_running))
    samples = [s for s in monitor.samples if warm <= s["t"] <= t_stop]
    per_name: dict[str, Any] = {}
    for name in ("kikitori-app.exe", "msedgewebview2.exe", "kikitori-engine.exe"):
        pts_priv = [(s["t"] / 60, _by_name(s, name)[0]) for s in samples]
        pts_hd = [(s["t"] / 60, float(_by_name(s, name)[1])) for s in samples]
        per_name[name] = {
            "private_slope_mib_per_min": r1(slope(pts_priv), 3),
            "handles_slope_per_min": r1(slope(pts_hd), 3),
            "private_first_last": [
                r1(pts_priv[0][1]) if pts_priv else None,
                r1(pts_priv[-1][1]) if pts_priv else None,
            ],
        }
    capped = [
        d for d in dom if d["lines"] is not None and d["lines"] >= TRANSCRIPT_MAX_LINES
    ]
    lines_ok = all(
        d["lines"] is None or d["lines"] <= TRANSCRIPT_MAX_LINES for d in dom
    )
    # 每列節點數：以 DOM 節點數對列數回歸；殘差對時間的斜率＝與列數無關的成長
    xs = [(float(d["lines"] or 0), float(d["main_nodes"])) for d in dom]
    per_line = slope(xs) or 0.0
    resid = [
        (d["t"] / 60, d["main_nodes"] - per_line * (d["lines"] or 0))
        for d in dom
        if d["t"] >= warm
    ]
    resid_slope = slope(resid)
    ov_nodes = [d["overlay_nodes"] for d in dom if d["t"] >= warm]
    report.data.update(
        {
            "minutes": minutes,
            "per_process": per_name,
            "dom_samples": dom,
            "actions": actions,
            "nodes_per_line": r1(per_line, 2),
            "dom_residual_slope_per_min": r1(resid_slope, 2),
            "reached_cap_at": capped[0]["t"] if capped else None,
            "translation_requests": len(proxy.chat_requests()),
            "post_stop_rel": [round(t_stop, 1), round(t_after, 1)],
            "main_heap_median_mib": r1(
                median_or_none([d["main_heap"] / 2**20 for d in dom if d["main_heap"]])
            ),
        }
    )
    report.check(
        "transcript never exceeds 300 lines",
        lines_ok,
        f"max {max((d['lines'] or 0) for d in dom) if dom else None}",
    )
    report.check(
        f"DOM growth not explained by transcript lines ≤ {DOM_RESIDUAL_SLOPE_PER_MIN} nodes/min",
        resid_slope is not None and resid_slope <= DOM_RESIDUAL_SLOPE_PER_MIN,
        f"{r1(resid_slope, 2)} nodes/min ({r1(per_line, 1)} nodes per line)",
    )
    report.check(
        f"overlay DOM stays bounded (range ≤ {OVERLAY_DOM_RANGE})",
        bool(ov_nodes) and max(ov_nodes) - min(ov_nodes) <= OVERLAY_DOM_RANGE,
        f"{min(ov_nodes, default=None)}–{max(ov_nodes, default=None)}",
    )
    app_slope = per_name["kikitori-app.exe"]["private_slope_mib_per_min"]
    report.check(
        f"kikitori-app.exe private bytes slope ≤ {APP_PRIVATE_SLOPE_MIB_PER_MIN} MiB/min",
        app_slope is not None and app_slope <= APP_PRIVATE_SLOPE_MIB_PER_MIN,
        f"{app_slope} MiB/min",
    )
    if capped:
        cap_t = capped[0]["t"]
        wv = [
            (s["t"] / 60, _by_name(s, "msedgewebview2.exe")[0])
            for s in samples
            if s["t"] >= cap_t
        ]
        wv_slope = slope(wv)
        report.check(
            f"WebView2 private bytes slope after the 300-line cap ≤ {WEBVIEW_PRIVATE_SLOPE_MIB_PER_MIN} MiB/min",
            wv_slope is not None and wv_slope <= WEBVIEW_PRIVATE_SLOPE_MIB_PER_MIN,
            f"{r1(wv_slope, 3)} MiB/min over {len(wv)} samples",
        )
    else:
        report.note(
            "the transcript did not reach the 300-line cap; WebView2 memory growth before the cap "
            "is expected — run longer for a plateau check"
        )
    report.check(
        "no app/WebView2/engine processes left after closing",
        not leftover,
        json.dumps(leftover),
    )
    report.data["marks"] = monitor.marks
    report.save()
    return 0 if report.passed else 1
