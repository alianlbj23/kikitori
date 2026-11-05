"""kikitori 引擎端到端驗收：真擷取（虛擬喇叭的 loopback）、真 Whisper（GPU）、真 Ollama。

暫時性實驗，不屬於正式程式碼。以 stdio NDJSON 協定驅動引擎（開發版 `uv run kikitori engine`
或打包好的 `kikitori-engine.exe`），把測試音訊播放到虛擬輸出裝置 Steam Streaming Speakers
（使用者聽不到），引擎從同一個裝置的 loopback 擷取，收集字幕事件並依「驗收標準」判定。

設定檔一律放在本次結果資料夾（`KIKITORI_CONFIG_DIR`），不碰使用者真正的設定檔。

執行（互動選單）：uv run python -X utf8 experiments/e2e_acceptance/acceptance.py
非互動（給自動化）：uv run python -X utf8 experiments/e2e_acceptance/acceptance.py --batch dev
  可用的批次名稱：dev、release、devices、tts
結果（含台詞，git 忽略）寫到 `results/<時間>/`；畫面上只印統計數字。
"""

from __future__ import annotations

import json
import logging
import os
import re
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
from e2e_common import (
    DATA,
    RESULTS,
    ROOT,
    SR,
    TARGET_OUTPUT,
    TRACKS,
    EngineClient,
    Playback,
    Player,
    read_wav,
    resample,
    synthesize_lines,
)

logger = logging.getLogger("e2e.acceptance")

RELEASE_ENGINE = Path(
    os.environ.get("KIKITORI_E2E_RELEASE_ENGINE")
    or ROOT
    / "app"
    / "src-tauri"
    / "binaries"
    / "kikitori-engine"
    / "kikitori-engine.exe"
)
"""正式版引擎（打包好的 one-folder）；可用環境變數 KIKITORI_E2E_RELEASE_ENGINE 指到別的建置。"""
BGM_SOURCE = (
    ROOT
    / "experiments"
    / "silero_reset"
    / "data"
    / "musan"
    / "music"
    / "fma"
    / "music-fma-0098.wav"
)
"""MUSAN 的純器樂曲（ANNOTATIONS 標 vocals=N），只取前 20 秒當「只有配樂」的段落。"""

# 本專案自己寫的測試句（不是任何影片的台詞），用 Windows 內建 TTS 合成。
ZH_LINES = (
    "今天天氣很好，我們下午去公園散步吧。",
    "請問附近有便利商店嗎？",
    "這個週末你有空嗎？我想請你吃晚餐。",
)
EN_LINES = (
    "Thank you for coming today. Let's start the meeting.",
    "Where is the nearest train station?",
    "I think we should finish this project by Friday.",
)
ZH_VOICE = "Microsoft Hanhan Desktop"
EN_VOICE = "Microsoft Zira Desktop"

GAP_BETWEEN_TRACKS = 3.0
SETTLE_SECS = 8.0
"""最後一段播完後再等多久讓完整字幕送完。"""
MATCH_TOLERANCE = 0.3
"""字幕與真值區間比對時兩邊各放寬的秒數（含擷取時間軸的對齊誤差與句尾保留的 0.2 s）。"""
MIN_PARTIAL_UTTERANCE_SECS = 1.0
"""短於這個長度的句子可能來不及觸發即時辨識（間隔 700 ms），不要求一定要有即時字幕。"""
FALSE_FINALS_TOLERANCE = {"silence": 0, "bgm": 0}
"""靜音與純配樂段落允許的非空完整字幕數。"""

KANA = re.compile(r"[぀-ヿ]")
CJK = re.compile(r"[一-鿿]")
LATIN = re.compile(r"[A-Za-z]")


# --------------------------------------------------------------------------- 場景


@dataclass
class Clip:
    label: str
    kind: str  # "speech" | "bgm" | "noise"
    audio: np.ndarray
    segments: list[tuple[int, int]] = field(default_factory=list)  # 16 kHz 真值
    language: str | None = None


def load_track(name: str) -> Clip:
    audio, rate = read_wav(TRACKS / f"{name}.wav")
    if rate != SR:
        audio = resample(audio, rate, SR)
    gt = json.loads((TRACKS / "ground_truth.json").read_text(encoding="utf-8"))
    segs = [
        (s["start_sample"], s["end_sample"]) for s in gt["tracks"][name]["segments"]
    ]
    return Clip(
        name, "speech" if segs else "noise", audio, segs, "ja" if segs else None
    )


def load_bgm(seconds: float = 20.0) -> Clip | None:
    if not BGM_SOURCE.exists():
        logger.warning(
            "BGM source %s not found; skipping the music-only section", BGM_SOURCE
        )
        return None
    audio, rate = read_wav(BGM_SOURCE)
    audio = resample(audio[: int(seconds * rate)], rate, SR)
    return Clip("bgm", "bgm", audio)


def tts_clip(label: str, lines: tuple[str, ...], voice: str, language: str) -> Clip:
    path = DATA / f"{label}.wav"
    meta = path.with_suffix(".json")
    if (
        path.exists()
        and meta.exists()
        and json.loads(meta.read_text("utf-8"))["lines"] == list(lines)
    ):
        segments = [tuple(s) for s in json.loads(meta.read_text("utf-8"))["segments"]]
    else:
        segments = synthesize_lines(lines, voice, path)
        meta.write_text(
            json.dumps({"lines": list(lines), "segments": segments}), "utf-8"
        )
    audio, _ = read_wav(path)
    return Clip(
        label, "speech", audio, [(int(a), int(b)) for a, b in segments], language
    )


# --------------------------------------------------------------------------- 判定


@dataclass
class Utterance:
    uid: int
    partials: list[tuple[float, dict[str, Any]]] = field(default_factory=list)
    final: tuple[float, dict[str, Any]] | None = None
    partial_after_final: int = 0


def collect_utterances(events: list[Any], session_id: int) -> dict[int, Utterance]:
    utts: dict[int, Utterance] = {}
    for rec in events:
        msg = rec.msg
        if (
            msg.get("event") != "subtitle"
            or msg["data"].get("session_id") != session_id
        ):
            continue
        d = msg["data"]
        u = utts.setdefault(d["utterance_id"], Utterance(d["utterance_id"]))
        if d["is_final"]:
            u.final = (rec.t, d)
        elif u.final is not None:
            u.partial_after_final += 1
        else:
            u.partials.append((rec.t, d))
    return utts


def simplified_chars(text: str) -> list[str]:
    """回傳 text 中的簡體專用字：Big5（cp950）沒有、而且 OpenCC s2t 會改掉的漢字。

    只用 s2t 判斷會誤報「傢伙」的「伙」這類繁簡共用字，所以先以 Big5 篩選。
    """
    import opencc

    conv = opencc.OpenCC("s2t")
    bad = []
    for c in text:
        if not CJK.match(c):
            continue
        try:
            c.encode("cp950")
        except UnicodeEncodeError:
            if conv.convert(c) != c:
                bad.append(c)
    return bad


def median(values: list[float]) -> float | None:
    return round(statistics.median(values), 3) if values else None


def p90(values: list[float]) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(values, 90)), 3)


def analyze_session(
    events: list[Any],
    session_id: int,
    t_capture0: float,
    plays: list[tuple[Clip, Playback]],
    mode: str,
) -> dict[str, Any]:
    """依驗收標準判定一個 session 的字幕事件。"""

    def wall(sample: int) -> float:
        return t_capture0 + sample / SR

    utts = collect_utterances(events, session_id)
    windows = []  # (clip, playback, [ (gt_start_wall, gt_end_wall) ])
    for clip, pb in plays:
        gts = [
            (pb.started_at + a / SR, pb.started_at + b / SR) for a, b in clip.segments
        ]
        windows.append((clip, pb, gts))

    report: dict[str, Any] = {"utterances": [], "checks": {}}
    # 原始事件（相對擷取起點的秒數）與每段播放的起點，給事後分析用。
    report["timeline"] = {
        "plays": [
            {
                "label": c.label,
                "start": round(pb.started_at - t_capture0, 3),
                "secs": round(pb.duration, 2),
            }
            for c, pb in plays
        ],
        "subtitles": [
            {"t": round(r.t - t_capture0, 3), **r.msg["data"]}
            for r in events
            if r.msg.get("event") == "subtitle"
            and r.msg["data"].get("session_id") == session_id
        ],
    }
    false_finals = {"silence": 0, "bgm": 0}
    covered: dict[tuple[str, int], int] = {}
    order_ok = True
    last_final_t = -1.0
    last_start = -1
    partial_then_final_fail = []
    simplified_fail = []
    language_fail = []
    label_mismatch: list[dict[str, Any]] = []
    first_partial_lat: list[float] = []
    partial_lag: list[float] = []
    final_lat: list[float] = []
    final_lat_gt: list[float] = []
    align: list[float] = []

    for uid in sorted(utts):
        u = utts[uid]
        if u.final is None:
            report["utterances"].append(
                {"uid": uid, "final": None, "partials": len(u.partials)}
            )
            continue
        t_final, fd = u.final
        empty = not fd["original"].strip()
        ws, we = wall(fd["start_sample"]), wall(fd["end_sample"])
        if t_final < last_final_t or fd["start_sample"] < last_start:
            order_ok = False
        last_final_t, last_start = t_final, fd["start_sample"]
        # 比對真值
        # 一句可能涵蓋好幾個真值區間（區間之間的停頓短於句尾判斷時 VAD 會合成一句）。
        matches = [
            (clip, i, gs, ge)
            for clip, pb, gts in windows
            for i, (gs, ge) in enumerate(gts)
            if ws < ge + MATCH_TOLERANCE and we > gs - MATCH_TOLERANCE
        ]
        match = matches[-1] if matches else None
        in_bgm = any(
            clip.kind in ("bgm", "noise")
            and ws < pb.started_at + pb.duration
            and we > pb.started_at
            for clip, pb, _ in windows
        )
        entry: dict[str, Any] = {
            "uid": uid,
            "start_s": round(fd["start_sample"] / SR, 2),
            "end_s": round(fd["end_sample"] / SR, 2),
            "duration_s": round((fd["end_sample"] - fd["start_sample"]) / SR, 2),
            "partials": len(u.partials),
            "partial_after_final": u.partial_after_final,
            "empty_final": empty,
            "source_language": fd["source_language"],
            "target_language": fd["target_language"],
            "original": fd["original"],
            "translation": fd["translation"],
            "matched": [f"{m[0].label}#{m[1] + 1}" for m in matches],
        }
        report["utterances"].append(entry)
        if empty:
            continue  # 引擎判定非語音後送的「清除」
        if match is None:
            false_finals["bgm" if in_bgm else "silence"] += 1
            continue
        clip, _, _, ge = match
        for m in matches:
            covered[(m[0].label, m[1])] = covered.get((m[0].label, m[1]), 0) + 1

        # 即時 → 完整
        duration = (fd["end_sample"] - fd["start_sample"]) / SR
        if u.partial_after_final or (
            duration >= MIN_PARTIAL_UTTERANCE_SECS and not u.partials
        ):
            partial_then_final_fail.append(uid)
        # 語言與文字
        expected_src = clip.language
        if mode == "media":
            # 驗收標準是「原文是日文」；ASR 給的語言標籤另外記錄（極短的感嘆詞可能被判成 en）。
            ok_lang = (
                bool(KANA.search(fd["original"]) or CJK.search(fd["original"]))
                and fd["target_language"] == "zh-TW"
                and bool(CJK.search(fd["translation"]))
            )
            if fd["source_language"] != "ja":
                label_mismatch.append(
                    {
                        "uid": uid,
                        "label": fd["source_language"],
                        "secs": round(duration, 2),
                    }
                )
        elif expected_src == "zh":
            ok_lang = (
                fd["source_language"] == "zh"
                and fd["target_language"] == "en"
                and len(LATIN.findall(fd["translation"])) >= 5
                and not CJK.search(fd["translation"])
            )
        else:
            ok_lang = (
                fd["source_language"] == "en"
                and fd["target_language"] == "zh-TW"
                and bool(CJK.search(fd["translation"]))
            )
        if not ok_lang:
            language_fail.append(uid)
        # 使用者的語言是台灣正體：譯成 zh-TW 的譯文、以及口譯時使用者自己說的中文原文都要是正體。
        shown = fd["translation"] if fd["target_language"] == "zh-TW" else ""
        if fd["source_language"] == "zh":
            shown += fd["original"]
        if shown:
            bad = simplified_chars(shown)
            if bad:
                simplified_fail.append({"uid": uid, "chars": bad})
                entry["simplified_chars"] = bad
        # 延遲
        if u.partials:
            t_first, pd = u.partials[0]
            entry["first_partial_s"] = round(t_first - wall(pd["start_sample"]), 3)
            first_partial_lat.append(entry["first_partial_s"])
            lags = [t - wall(d["end_sample"]) for t, d in u.partials]
            entry["partial_lag_median_s"] = median(lags)
            partial_lag.extend(lags)
        entry["final_latency_s"] = round(t_final - we, 3)
        final_lat.append(entry["final_latency_s"])
        # 句子結尾就是真值區間的結尾時（不是被 VAD 切成多句的中間句），另算「從真正說完」起的延遲。
        if abs(we - 0.2 - ge) <= 0.6:
            entry["final_latency_from_gt_end_s"] = round(t_final - ge, 3)
            final_lat_gt.append(entry["final_latency_from_gt_end_s"])
            align.append(we - ge)

    missing = [
        f"{clip.label}#{i + 1}"
        for clip, _, gts in windows
        if clip.kind == "speech"
        for i in range(len(gts))
        if (clip.label, i) not in covered
    ]
    report["checks"] = {
        "segments_total": sum(len(c.segments) for c, _ in plays if c.kind == "speech"),
        "segments_missing": missing,
        "finals_in_order": order_ok,
        "partial_then_final_failures": partial_then_final_fail,
        "language_failures": language_fail,
        "language_label_mismatches": label_mismatch,
        "simplified_failures": simplified_fail,
        "false_finals": false_finals,
        "false_finals_tolerance": FALSE_FINALS_TOLERANCE,
    }
    report["latency"] = {
        "first_partial_from_utterance_start": {
            "median": median(first_partial_lat),
            "p90": p90(first_partial_lat),
            "max": max(first_partial_lat, default=None),
        },
        "partial_lag": {
            "median": median(partial_lag),
            "p90": p90(partial_lag),
            "max": round(max(partial_lag), 3) if partial_lag else None,
        },
        "final_from_end_sample": {
            "median": median(final_lat),
            "p90": p90(final_lat),
            "max": max(final_lat, default=None),
        },
        "final_from_ground_truth_end": {
            "median": median(final_lat_gt),
            "p90": p90(final_lat_gt),
            "max": max(final_lat_gt, default=None),
            "n": len(final_lat_gt),
        },
        "capture_alignment_end_sample_minus_gt_end": median(align),
    }
    report["counts"] = {
        "utterances": len(utts),
        "nonempty_finals": sum(
            1 for u in utts.values() if u.final and u.final[1]["original"].strip()
        ),
        "empty_finals": sum(
            1 for u in utts.values() if u.final and not u.final[1]["original"].strip()
        ),
        "partials": sum(len(u.partials) for u in utts.values()),
        "subtitle_events": sum(
            1
            for r in events
            if r.msg.get("event") == "subtitle"
            and r.msg["data"].get("session_id") == session_id
        ),
    }
    c = report["checks"]
    report["passed"] = (
        not c["segments_missing"]
        and c["finals_in_order"]
        and not c["partial_then_final_failures"]
        and not c["language_failures"]
        and not c["simplified_failures"]
        and all(false_finals[k] <= FALSE_FINALS_TOLERANCE[k] for k in false_finals)
    )
    return report


# --------------------------------------------------------------------------- 執行


def unload_ollama_model(model: str, base: str = "http://127.0.0.1:11434") -> str:
    """請本機 Ollama 卸載模型（keep_alive=0），重現使用者閒置 5 分鐘後的冷啟動。"""
    import urllib.request

    body = json.dumps({"model": model, "keep_alive": 0}).encode()
    req = urllib.request.Request(
        f"{base}/api/generate", body, {"Content-Type": "application/json"}
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8")).get("done_reason", "")


def engine_command(which: str) -> tuple[list[str], Path]:
    if which == "release":
        if not RELEASE_ENGINE.exists():
            raise FileNotFoundError(
                f"{RELEASE_ENGINE} not found; build it with uv run python packaging/engine/build.py"
            )
        return [str(RELEASE_ENGINE)], RELEASE_ENGINE.parent
    return ["uv", "run", "kikitori", "engine"], ROOT


def start_engine(which: str, run_dir: Path) -> EngineClient:
    cmd, cwd = engine_command(which)
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("KIKITORI_ENGINE_FAKE")
    }
    env["KIKITORI_CONFIG_DIR"] = str(config_dir)
    return EngineClient(cmd, env=env, cwd=cwd, stderr_log=run_dir / "engine-stderr.log")


def check(results: dict[str, Any], name: str, ok: bool, detail: Any) -> None:
    results["steps"][name] = {"passed": bool(ok), "detail": detail}
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {summarize(detail)}")


def summarize(detail: Any) -> str:
    text = json.dumps(detail, ensure_ascii=False)
    return text if len(text) <= 300 else text[:300] + "…"


def device_names_check(client: EngineClient) -> tuple[bool, dict[str, Any], str | None]:
    """devices.list 的名稱要與 PyAudioWPatch 直接列出的 Unicode 名稱一致，且沒有替代字元。"""
    import pyaudiowpatch

    devices = client.request("devices.list")["devices"]
    pa = pyaudiowpatch.PyAudio()
    try:
        native = {
            str(i["name"])
            for i in pa.get_device_info_generator_by_host_api(
                host_api_type=pyaudiowpatch.paWASAPI
            )
        }
    finally:
        pa.terminate()
    problems = []
    for d in devices:
        base = re.sub(r"#\d+$", "", d["name"])
        if "�" in d["name"] or base not in native:
            problems.append(d["name"])
    target = [
        d for d in devices if d["kind"] == "loopback" and TARGET_OUTPUT in d["name"]
    ]
    detail = {
        "count": len(devices),
        "names_escaped": [ascii(d["name"]) for d in devices],
        "problems": problems,
        "target_loopback": target[0]["id"] if target else None,
    }
    return (
        (not problems and bool(target)),
        detail,
        (target[0]["id"] if target else None),
    )


def run_session(
    client: EngineClient,
    player: Player,
    mode: str,
    device_id: str,
    clips: list[Clip],
) -> tuple[int, float, list[tuple[Clip, Playback]], dict[str, Any]]:
    since = len(client.events)
    sid = client.request(
        "session.start", {"mode": mode, "device_id": device_id}, timeout=30
    )["session_id"]
    t_req = time.monotonic()
    running = client.wait_event(
        lambda m: (
            m.get("event") == "status"
            and m["data"].get("session_id") == sid
            and m["data"]["state"] in ("running", "error")
        ),
        timeout=180,
        since=since,
    )
    if running.msg["data"]["state"] != "running":
        raise RuntimeError(f"session failed to start: {running.msg['data']}")
    load_secs = running.t - t_req
    print(f"  session {sid} running after {load_secs:.1f} s (model load)")
    time.sleep(1.0)
    plays: list[tuple[Clip, Playback]] = []
    for clip in clips:
        print(
            f"  playing {clip.label} ({clip.audio.size / SR:.1f} s) to {player.device.name!r}"
        )
        plays.append((clip, player.play(clip.label, clip.audio)))
        time.sleep(GAP_BETWEEN_TRACKS)
    time.sleep(SETTLE_SECS)
    t_stop = time.monotonic()
    client.request("session.stop", timeout=20)
    idle = client.wait_event(
        lambda m: (
            m.get("event") == "status"
            and m["data"]["state"] in ("idle", "error")
            and m["data"].get("session_id") in (sid, None)
        ),
        timeout=20,
        since=since,
    )
    stop_detail = {
        "state": idle.msg["data"]["state"],
        "secs": round(idle.t - t_stop, 3),
        "load_secs": round(load_secs, 2),
    }
    return sid, running.t, plays, stop_detail


def full_acceptance(which: str) -> int:
    run_dir = RESULTS / time.strftime("%Y%m%d-%H%M%S")
    run_dir.mkdir(parents=True, exist_ok=True)
    results: dict[str, Any] = {"engine": which, "steps": {}, "sessions": {}}
    print(f"results: {run_dir}")
    client = start_engine(which, run_dir)
    player = Player()
    print(f"playback device: {player.device.name!r} (index {player.device.index})")
    exit_code: int | None = None
    try:
        info = client.request("engine.info", timeout=120)
        check(
            results,
            "engine.info",
            info.get("fake") is False and info.get("protocol_version") == 1,
            info,
        )

        hw = client.request("hardware.detect", timeout=90)
        gpus = hw["hardware"].get("gpus", [])
        rec = hw["recommendation"]
        ok = (
            any("RTX 5070" in g["name"] for g in gpus)
            and hw["hardware"]["cublas"]["available"] is True
            and rec["tier"] == "nvidia_8gb"
        )
        check(
            results,
            "hardware.detect",
            ok,
            {
                "gpus": [(g["name"], g["memory_total_mb"]) for g in gpus],
                "cublas": hw["hardware"]["cublas"],
                "tier": rec["tier"],
                "fast_asr": rec["fast_asr"],
                "final_asr": rec["final_asr"],
                "translation": (rec["translation_fast"], rec["translation_final"]),
                "warnings": rec.get("warnings"),
            },
        )

        applied = client.request("settings.apply_recommendation", timeout=90)
        asr = applied["settings"]["asr"]
        check(
            results,
            "settings.apply_recommendation",
            asr["device"] == "cuda",
            applied["applied"],
        )

        models = client.request("models.list", timeout=90)
        downloaded = {m["id"] for m in models["asr"] if m["downloaded"]}
        needed = {asr["fast_model"], asr["final_model"]}
        tr = applied["settings"]["translation"]
        installed = {
            m["name"] for m in models["translation"]["models"] if m["installed"]
        }
        check(
            results,
            "models.list",
            needed <= downloaded
            and tr["final_model"] in installed
            and models["gpu_runtime"]["installed"],
            {
                "needed_asr": sorted(needed),
                "downloaded_asr": sorted(downloaded),
                "translation": tr["final_model"],
                "gpu_runtime_installed": models["gpu_runtime"]["installed"],
            },
        )

        ok, detail, device_id = device_names_check(client)
        check(results, "devices.list (Unicode names)", ok, detail)
        if device_id is None:
            raise RuntimeError(f"{TARGET_OUTPUT} loopback not found")

        # --- 影片翻譯（先卸載翻譯模型：最壞情況的冷啟動）
        reason = unload_ollama_model(tr["final_model"])
        check(
            results,
            "Ollama model unloaded before media session",
            reason == "unload",
            reason,
        )
        clips = [load_track("anime"), load_track("vtuber")]
        bgm = load_bgm()
        if bgm:
            clips.append(bgm)
        clips.append(load_track("synthetic"))
        sid, t0, plays, stop = run_session(client, player, "media", device_id, clips)
        rep = analyze_session(client.events, sid, t0, plays, "media")
        results["sessions"]["media"] = rep
        check(results, "media session.stop → idle", stop["state"] == "idle", stop)
        check(
            results,
            "media subtitles",
            rep["passed"],
            {"counts": rep["counts"], "checks": rep["checks"]},
        )
        print(f"    latency: {summarize(rep['latency'])}")

        # --- 即時口譯（對方說英文）
        client.request("settings.set", {"partner_language": "en"})
        zh = tts_clip("tts_zh", ZH_LINES, ZH_VOICE, "zh")
        en = tts_clip("tts_en", EN_LINES, EN_VOICE, "en")
        sid, t0, plays, stop = run_session(
            client, player, "interpret", device_id, [zh, en]
        )
        rep = analyze_session(client.events, sid, t0, plays, "interpret")
        results["sessions"]["interpret"] = rep
        check(results, "interpret session.stop → idle", stop["state"] == "idle", stop)
        check(
            results,
            "interpret subtitles",
            rep["passed"],
            {"counts": rep["counts"], "checks": rep["checks"]},
        )
        print(f"    latency: {summarize(rep['latency'])}")
    except Exception:
        logger.exception("acceptance run aborted")
        results["aborted"] = True
    finally:
        exit_code = client.close()
        player.close()
    check(results, "engine.shutdown exit code", exit_code == 0, exit_code)
    check(
        results,
        "stdout lines are valid JSON",
        not client.bad_lines,
        len(client.bad_lines),
    )
    statuses = [r.msg["data"] for r in client.events if r.msg.get("event") == "status"]
    results["status_events"] = statuses
    results["log_events"] = [
        r.msg["data"] for r in client.events if r.msg.get("event") == "log"
    ]
    passed = not results.get("aborted") and all(
        s["passed"] for s in results["steps"].values()
    )
    results["passed"] = passed
    (run_dir / "results.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"\nOVERALL: {'PASS' if passed else 'FAIL'}  (details: {run_dir / 'results.json'})"
    )
    return 0 if passed else 1


def devices_only() -> int:
    run_dir = RESULTS / (time.strftime("%Y%m%d-%H%M%S") + "-devices")
    client = start_engine("dev", run_dir)
    try:
        ok, detail, _ = device_names_check(client)
    finally:
        code = client.close()
    print(json.dumps(detail, ensure_ascii=False, indent=2))
    print(f"devices: {'PASS' if ok else 'FAIL'}; engine exit {code}")
    return 0 if ok and code == 0 else 1


def tts_only() -> int:
    for clip in (
        tts_clip("tts_zh", ZH_LINES, ZH_VOICE, "zh"),
        tts_clip("tts_en", EN_LINES, EN_VOICE, "en"),
    ):
        print(f"{clip.label}: {clip.audio.size / SR:.1f} s, segments {clip.segments}")
    return 0


BATCH = {
    "dev": lambda: full_acceptance("dev"),
    "release": lambda: full_acceptance("release"),
    "devices": devices_only,
    "tts": tts_only,
}

MENU = """
kikitori 端到端驗收
  1) 完整驗收（開發版引擎：uv run kikitori engine）
  2) 完整驗收（正式版引擎：app/src-tauri/binaries/kikitori-engine）
  3) 只檢查裝置名稱（devices.list 與 PyAudioWPatch 比對）
  4) 只合成 TTS 測試音檔
  5) 離開
"""


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if len(argv) == 2 and argv[0] == "--batch" and argv[1] in BATCH:
        return BATCH[argv[1]]()
    if argv:
        print(f"usage: acceptance.py [--batch {'|'.join(BATCH)}]", file=sys.stderr)
        return 2
    choices = {"1": "dev", "2": "release", "3": "devices", "4": "tts"}
    while True:
        print(MENU)
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice == "5":
            return 0
        if choice in choices:
            return BATCH[choices[choice]]()
        print(f"無效的選項：{choice!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
