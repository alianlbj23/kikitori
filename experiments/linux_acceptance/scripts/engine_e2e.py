"""Linux 引擎端到端驗收：真的引擎（非假模式）錄 PulseAudio monitor，Silero VAD + CPU ASR + Ollama 翻譯。

暫時性實驗工具，不屬於正式程式碼。在容器內：

1. 啟動 PulseAudio 與 null sink（同 `capture_probe.py`）。
2. 以 `python -m kikitori engine` 啟動引擎（設定目錄在暫存目錄；模型快取以 XDG_CACHE_HOME 指到
   唯讀掛載的 Windows 模型快取），`settings.set` 指定 CPU 與主機的 Ollama。
3. `session.start` media 模式、指定 `<sink>.monitor`，`status running` 後用 paplay 播放
   `anime.wav`（本機、git 忽略）到該 sink。
4. 收集 `subtitle` 事件到播放結束後所有句子都有完整字幕（或逾時），`session.stop`、
   `engine.shutdown`，確認結束碼 0。

延遲以「音軌中的位置」換算牆鐘時間：音軌第一個有聲音的位置（能量門檻）對應第一句的
`start_sample`（扣掉 VAD 的 200 ms 前置緩衝），誤差約 ±50 ms。容器內 CPU 的數字不代表實機。
"""

import json
import os
import statistics
import sys
import tempfile
import time
import wave
from pathlib import Path
from typing import Any

import numpy as np
import opencc

sys.path.insert(0, str(Path(__file__).resolve().parent))
ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packaging" / "engine"))

import capture_probe as cp
from smoke_test import EngineClient, SmokeTestError

TRACK = cp.DATA_DIR / "anime.wav"
SINK = cp.SINKS[0]
OLLAMA = os.environ.get("KLA_OLLAMA", "http://host.docker.internal:11434/v1")
MODEL_CACHE = os.environ.get("KLA_MODELS", "/models")
PRE_ROLL = 0.2
SETTLE_SECS = 30.0


class TimedClient(EngineClient):
    """`EngineClient` 加上每筆訊息的收到時間（`time.monotonic()`）。"""

    def __post_init__(self) -> None:
        self.timed: list[tuple[float, dict[str, Any]]] = []
        super().__post_init__()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.bad_lines.append(line)
                continue
            if not isinstance(message, dict):
                self.bad_lines.append(line)
                continue
            self.timed.append((time.monotonic(), message))
            self.messages.append(message)
            self._queue.put(message)
        self._queue.put(None)


def first_onset(path: Path, threshold: float = 0.02) -> float:
    """音軌第一個 20 ms 框 RMS 超過門檻的位置（秒）。"""
    with wave.open(str(path), "rb") as w:
        rate = w.getframerate()
        data = np.frombuffer(w.readframes(w.getnframes()), dtype="<i2")
    frame = rate // 50
    frames = data[: len(data) // frame * frame].reshape(-1, frame).astype(np.float64)
    levels = np.sqrt(np.mean(frames**2, axis=1)) / 32768
    hits = np.nonzero(levels > threshold)[0]
    return float(hits[0] * frame / rate) if hits.size else 0.0


def engine_command() -> list[str]:
    """預設跑原始碼的引擎；`KLA_ENGINE` 指到打包好的執行檔時改跑它（例如從 deb 解出來的）。"""
    binary = os.environ.get("KLA_ENGINE")
    return [binary] if binary else [sys.executable, "-m", "kikitori", "engine"]


def engine_env(config_dir: str, cache_dir: str) -> dict[str, str]:
    """真引擎的環境：設定在暫存目錄、模型快取指到掛載的目錄。"""
    env = dict(os.environ)
    env.pop("KIKITORI_ENGINE_FAKE", None)
    env["KIKITORI_CONFIG_DIR"] = config_dir
    env["XDG_CACHE_HOME"] = cache_dir
    env["PYTHONUTF8"] = "1"
    return env


def is_traditional(text: str, converter: opencc.OpenCC) -> bool:
    """簡轉繁後不變，就當作已經是繁體。"""
    return converter.convert(text) == text


def main() -> int:
    """跑一次端到端並把結果寫到 results/engine_e2e.json。"""
    cp.start_server()
    with wave.open(str(TRACK), "rb") as w:
        track_secs = w.getnframes() / w.getframerate()
    onset = first_onset(TRACK)
    tmp = tempfile.mkdtemp(prefix="kla-engine-")
    cache = Path(tmp) / "cache"
    (cache / "kikitori").mkdir(parents=True)
    (cache / "kikitori" / "models").symlink_to(MODEL_CACHE)
    fast = os.environ.get("KLA_FAST", "base")
    final = os.environ.get("KLA_FINAL", "small")
    translator = os.environ.get("KLA_TRANSLATOR", "qwen3.5:4b")

    t_launch = time.monotonic()
    client = TimedClient(
        engine_command(),
        engine_env(str(Path(tmp) / "config"), str(cache)),
    )
    report: dict[str, Any] = {"fast": fast, "final": final, "translator": translator}
    try:
        client.wait_for(
            lambda m: m.get("event") == "status" and m["data"]["state"] == "idle",
            "first idle",
        )
        report["engine_ready_s"] = round(time.monotonic() - t_launch, 2)
        report["engine_info"] = client.request("engine.info")
        client.request(
            "settings.set",
            {
                "asr": {"fast_model": fast, "final_model": final, "device": "cpu"},
                "translation": {
                    "base_url": OLLAMA,
                    "fast_model": translator,
                    "final_model": translator,
                },
            },
        )
        models = client.request("models.list")
        report["asr_downloaded"] = {
            m["id"]: m["downloaded"] for m in models["asr"] if m["downloaded"]
        }
        report["translation_server"] = models["translation"]["server"]
        devices = client.request("devices.list")
        report["devices"] = devices
        device_id = f"soundcard:loopback:{SINK}.monitor"
        t_start = time.monotonic()
        session = client.request(
            "session.start", {"mode": "media", "device_id": device_id}
        )
        sid = session["session_id"]
        client.wait_for(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["state"] == "running"
                and m["data"]["session_id"] == sid
            ),
            "running",
            timeout=180,
        )
        report["session_load_s"] = round(time.monotonic() - t_start, 2)
        # null sink 的 monitor 開始約 1.9 s 後才有資料（capture_probe 的量測），先等它穩定。
        time.sleep(2.5)
        t_play = time.monotonic()
        player = cp.play(TRACK, SINK)
        player.wait(timeout=track_secs + 30)
        # 完整字幕的佇列在 CPU 上可能落後；等到最後一則字幕之後 SETTLE_SECS 都沒有新的。
        last = time.monotonic()
        seen = 0
        while time.monotonic() - last < SETTLE_SECS:
            time.sleep(0.5)
            count = sum(1 for m in client.messages if m.get("event") == "subtitle")
            if count != seen:
                seen, last = count, time.monotonic()
        client.request("session.stop")
        client.wait_for(
            lambda m: (
                m.get("event") == "status"
                and m["data"]["state"] == "idle"
                and m["data"]["session_id"] == sid
            ),
            "idle after stop",
        )
        client.request("engine.shutdown")
    finally:
        report["exit_code"] = client.close(timeout=20)

    subs = [m for m in client.messages if m.get("event") == "subtitle"]
    timed = client.timed
    converter = opencc.OpenCC("s2twp")
    finals = [s["data"] for s in subs if s["data"]["is_final"]]
    partials = [s["data"] for s in subs if not s["data"]["is_final"]]
    report["partials"] = len(partials)
    report["finals"] = [
        {
            "id": f["utterance_id"],
            "original": f["original"],
            "translation": f["translation"],
            "traditional": is_traditional(f["translation"], converter),
            "start_s": round(f["start_sample"] / 16000, 2),
            "end_s": round(f["end_sample"] / 16000, 2),
        }
        for f in finals
    ]
    if timed and finals:
        origin = finals[0]["start_sample"] / 16000 + PRE_ROLL - onset
        lat_final = [
            t - (t_play + f["end_sample"] / 16000 - origin)
            for t, m in timed
            if m.get("event") == "subtitle"
            for f in [m["data"]]
            if f["is_final"]
        ]
        lat_partial = [
            t - (t_play + f["end_sample"] / 16000 - origin)
            for t, m in timed
            if m.get("event") == "subtitle"
            for f in [m["data"]]
            if not f["is_final"]
        ]
        report["latency_final_s"] = {
            "median": round(statistics.median(lat_final), 2),
            "max": round(max(lat_final), 2),
        }
        if lat_partial:
            report["latency_partial_s"] = {
                "median": round(statistics.median(lat_partial), 2),
                "max": round(max(lat_partial), 2),
            }
    statuses = [
        m["data"]["state"] for m in client.messages if m.get("event") == "status"
    ]
    report["status_sequence"] = statuses
    report["errors"] = [
        m["data"]
        for m in client.messages
        if m.get("event") == "status" and m["data"]["state"] == "error"
    ]
    cp.RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (cp.RESULTS_DIR / "engine_e2e.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    for key in ("engine_ready_s", "session_load_s", "partials", "exit_code"):
        print(f"{key}: {report.get(key)}")
    for f in report["finals"]:
        print(f"  [{f['start_s']:6.2f}-{f['end_s']:6.2f}] {f['translation']}")
    print("latency_final_s:", report.get("latency_final_s"))
    print("latency_partial_s:", report.get("latency_partial_s"))
    ok = (
        report["exit_code"] == 0
        and finals
        and all(f["traditional"] and f["translation"] for f in report["finals"])
        and not report["errors"]
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def device_lost() -> int:
    """session 執行中移除 sink（模擬拔掉 USB 耳機）：預期 `status error`、`code=device_lost`。"""
    cp.start_server()
    cp.pactl("load-module", "module-null-sink", "sink_name=usbdev")
    tmp = tempfile.mkdtemp(prefix="kla-engine-")
    cache = Path(tmp) / "cache"
    (cache / "kikitori").mkdir(parents=True)
    (cache / "kikitori" / "models").symlink_to(MODEL_CACHE)
    client = TimedClient(
        engine_command(),
        engine_env(str(Path(tmp) / "config"), str(cache)),
    )
    try:
        client.wait_for(
            lambda m: m.get("event") == "status" and m["data"]["state"] == "idle",
            "first idle",
        )
        client.request(
            "settings.set",
            {
                "asr": {"fast_model": "tiny", "final_model": "tiny", "device": "cpu"},
                "translation": {"base_url": OLLAMA},
            },
        )
        sid = client.request(
            "session.start",
            {"mode": "media", "device_id": "soundcard:loopback:usbdev.monitor"},
        )["session_id"]
        client.wait_for(
            lambda m: m.get("event") == "status" and m["data"]["state"] == "running",
            "running",
            timeout=180,
        )
        time.sleep(3)
        t_removed = time.monotonic()
        cp.pactl("unload-module", cp.module_id("usbdev"))
        try:
            error: dict[str, Any] | None = client.wait_for(
                lambda m: (
                    m.get("event") == "status"
                    and m["data"]["state"] == "error"
                    and m["data"]["session_id"] == sid
                ),
                "status error",
                timeout=30,
            )["data"]
        except SmokeTestError:
            # 修正前的行為：串流被搬到別的 source，session 繼續跑、沒有任何錯誤。
            error = None
        t_error = time.monotonic() - t_removed
        client.request("engine.shutdown")
    finally:
        code = client.close(timeout=20)
    if error is None:
        print("no status error within 30 s after removing the device (stream moved)")
    else:
        print(f"status error after {t_error:.2f}s: {error}")
    print(f"exit code: {code}")
    ok = error is not None and error["code"] == "device_lost" and code == 0
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


MENU = """
Linux 引擎端到端驗收
  1) 播放 anime.wav，真的 VAD + CPU ASR + Ollama 翻譯
  2) session 中移除裝置 → device_lost
  0) 離開
"""


def menu() -> int:
    """互動選單；`--all` 依序跑兩項（給 run.sh 的非互動模式）。"""
    if sys.argv[1:] == ["--all"]:
        return max(main(), device_lost())
    while True:
        print(MENU)
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice in ("", "0"):
            return 0
        if choice == "1":
            main()
        elif choice == "2":
            device_lost()
        else:
            print(f"無效的選項：{choice!r}")


if __name__ == "__main__":
    raise SystemExit(menu())
