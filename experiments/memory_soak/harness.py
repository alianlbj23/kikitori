"""記憶體壓力測試的共用部分：啟動引擎、播放測試聲音、session 操作、判定門檻與統計。

暫時性實驗，不屬於正式程式碼。沿用 `experiments/e2e_acceptance/e2e_common.py` 的 NDJSON
引擎客戶端、WAV 讀取與「只播到 Steam Streaming Speakers」的安全檢查。

安全規則（與端到端驗收相同）：
- 測試聲音只播到名稱含 Steam Streaming Speakers 的輸出（虛擬喇叭，使用者聽不到）；
  它若是系統預設輸出就拒絕播放。引擎擷取同一裝置的 loopback。
- 設定檔一律放在本次結果資料夾（`KIKITORI_CONFIG_DIR`），不碰使用者的設定。
- 翻譯請求經過本機計數代理（`proxy.py`）再到 Ollama；不卸載、不刪除任何 Ollama 模型
  （同一台機器上可能有其他實驗正在使用）。
"""

from __future__ import annotations

import json
import logging
import os
import statistics
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
E2E = ROOT / "experiments" / "e2e_acceptance"
sys.path.insert(0, str(E2E))

from e2e_common import (
    SR,
    TARGET_OUTPUT,
    TRACKS,
    EngineClient,
    EngineError,
    Float32Array,
    Received,
    find_output_device,
    read_wav,
    resample,
    silence,
)

logger = logging.getLogger("soak")

RESULTS = HERE / "results"
E2E_DATA = E2E / "data"
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
"""MUSAN 的純器樂曲（ANNOTATIONS 標 vocals=N），長時間測試時墊在人聲底下當 BGM。"""
RELEASE_ENGINE = Path(
    os.environ.get("KIKITORI_SOAK_ENGINE")
    or ROOT
    / "app"
    / "src-tauri"
    / "binaries"
    / "kikitori-engine"
    / "kikitori-engine.exe"
)
"""正式版引擎；可用環境變數 KIKITORI_SOAK_ENGINE 指到別的建置。"""
DEFAULT_TRANSLATION_MODEL = os.environ.get(
    "KIKITORI_SOAK_TRANSLATION_MODEL", "qwen3.5:4b"
)
"""翻譯模型。預設 4B：顯示記憶體較小，與同機其他實驗搶顯示記憶體時比較不會把別人的模型擠掉。"""

KEEP_WARM_LOG = "sending a keep-warm request"
WARM_UP_LOG = "warming up the translation model"

# --------------------------------------------------------------------------- 門檻
#
# 都是「每個 session 結束後、等 SETTLE_SECS 秒」的值，排除前 WARMUP_CYCLES 輪：第一次
# session 會載入 CUDA context、cuBLAS、CTranslate2 的快取配置器、Python 模組與 lru_cache，
# 這些是一次性的常駐成本，不是洩漏。

WARMUP_CYCLES = 2
SETTLE_SECS = 12.0
"""session 結束（idle）後等多久再讀「結束後」的值：pipeline.stop 的 join 逾時 5 秒、保溫
執行緒 0.5 秒、daemon 執行緒自行結束，再留幾秒給計數器（GPU 計數器本身約 1 秒一筆）。"""
POST_WINDOW_SECS = 4.0
"""「結束後」的值取這段時間內樣本的中位數，避開單一樣本的瞬間波動。"""

PRIVATE_SLOPE_MIB_PER_CYCLE = 4.0
"""引擎私有記憶體每輪成長的上限。一個 session 的物件若沒有釋放，至少留下 Silero VAD 的
onnxruntime session、兩個 transcriber 的 CPU 端結構與 5 秒擷取緩衝，合計數十 MB；4 MiB/輪
（20 輪 80 MiB）抓得到，又高於 Python 配置器碎片化的雜訊。"""
HANDLE_SLOPE_PER_CYCLE = 1.0
"""每輪 handle 成長上限：漏掉一條執行緒、一個 socket 或一個事件物件就是每輪 ≥ 1。"""
THREAD_SLOPE_PER_CYCLE = 0.25
THREAD_EXTRA_ALLOWED = 2
"""結束後的執行緒數最多比暖機後的基準（中位數）多 2、斜率 ≤ 0.25 條／輪（每 4 個 session 漏一條）。
第一次 12 輪的實測：handle 數持平（600–606）時執行緒仍在 29–32 之間跳動——Windows 的執行緒池
（WASAPI、RPC、PDH）自己會開關工作執行緒。漏掉的 Python 執行緒一定也留下一個 thread handle，
所以真正的洩漏會同時讓 handle 斜率超標。"""
GPU_SLOPE_MIB_PER_CYCLE = 16.0
"""每輪顯示記憶體（dedicated + shared）成長上限。v0.1.0 的洩漏是每個 session +2.6 GB；
CTranslate2 的快取配置器有上限（預設 max_cached_bytes 200 MB），不會造成持續的斜率。"""
GPU_RETURN_TOL_MIB = 128.0
GPU_RETURN_SECS = 10.0
"""session.stop 後 10 秒內，引擎的顯示記憶體要回到「結束後」的底（所有輪的中位數）+128 MiB。"""
GPU_RESIDUE_MAX_MIB = 1024.0
"""結束後的底比第一個 session 之前多出的量（CUDA context + 快取配置器）上限 1 GiB：小於
最小的一組模型（small + large-v3-turbo，float16 約 2.1 GB），模型若沒釋放一定超過。"""

LONG_PRIVATE_SLOPE_MIB_PER_MIN = 1.0
"""長時間 session（暖機後）私有記憶體的斜率上限。每 3 秒左右一句，每句漏 50 KB 就約
1 MiB/分；8 小時的直播會多 480 MiB，使用者感受得到。"""
LONG_HANDLE_SLOPE_PER_MIN = 2.0
LONG_THREAD_RANGE = 4
"""暖機後執行緒數的最大值 − 最小值上限（即時翻譯串流、保溫請求是短暫的執行緒）。"""
LONG_GPU_SLOPE_MIB_PER_MIN = 2.0
LONG_POST_PRIVATE_GROWTH_MIB = 64.0
"""長時間 session 結束後，私有記憶體比暖機 session 結束後多出的上限。"""


# --------------------------------------------------------------------------- 統計


def slope(points: Sequence[tuple[float, float]]) -> float | None:
    """最小平方法的斜率（y 對 x）；少於 3 點時 None。"""
    if len(points) < 3:
        return None
    xs = np.array([p[0] for p in points], dtype=np.float64)
    ys = np.array([p[1] for p in points], dtype=np.float64)
    if np.ptp(xs) == 0:
        return None
    return float(np.polyfit(xs, ys, 1)[0])


def median_or_none(values: Sequence[float]) -> float | None:
    return float(statistics.median(values)) if values else None


def r1(value: float | None, digits: int = 1) -> float | None:
    return None if value is None else round(value, digits)


@dataclass
class Verdict:
    name: str
    passed: bool
    detail: str

    def line(self) -> str:
        return f"  [{'PASS' if self.passed else 'FAIL'}] {self.name}: {self.detail}"


@dataclass
class Report:
    scenario: str
    run_dir: Path
    verdicts: list[Verdict] = field(default_factory=list)
    data: dict[str, Any] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def check(self, name: str, ok: bool, detail: str) -> None:
        v = Verdict(name, bool(ok), detail)
        self.verdicts.append(v)
        print(v.line(), flush=True)

    def note(self, text: str) -> None:
        self.notes.append(text)
        print(f"  note: {text}", flush=True)

    @property
    def passed(self) -> bool:
        return bool(self.verdicts) and all(v.passed for v in self.verdicts)

    def save(self) -> None:
        out = {
            "scenario": self.scenario,
            "passed": self.passed,
            "verdicts": [v.__dict__ for v in self.verdicts],
            "notes": self.notes,
            "data": self.data,
        }
        (self.run_dir / "summary.json").write_text(
            json.dumps(out, ensure_ascii=False, indent=2, default=str), "utf-8"
        )
        print(
            f"\n{self.scenario}: {'PASS' if self.passed else 'FAIL'}"
            f"  (details: {self.run_dir / 'summary.json'})",
            flush=True,
        )


def new_run_dir(name: str) -> Path:
    d = RESULTS / f"{time.strftime('%Y%m%d-%H%M%S')}-{name}"
    d.mkdir(parents=True, exist_ok=True)
    return d


# --------------------------------------------------------------------------- 聲音


def load_clip(path: Path) -> Float32Array:
    audio, rate = read_wav(path)
    return resample(audio, rate, SR)


def speech_ja() -> Float32Array:
    """日文動畫＋VTuber 片段（約 64 秒），句間 1.5 秒靜音。不進 repo（含台詞）。"""
    return np.concatenate(
        [
            load_clip(TRACKS / "anime.wav"),
            silence(1.5),
            load_clip(TRACKS / "vtuber.wav"),
            silence(1.5),
        ]
    ).astype(np.float32)


def speech_en() -> Float32Array:
    """本專案自己寫的英文句子（Windows TTS，端到端驗收時合成）。"""
    path = E2E_DATA / "tts_en.wav"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run experiments/e2e_acceptance/acceptance.py --batch tts"
        )
    return np.concatenate([load_clip(path), silence(1.0)]).astype(np.float32)


def speech_zh() -> Float32Array:
    path = E2E_DATA / "tts_zh.wav"
    if not path.exists():
        raise FileNotFoundError(
            f"{path} not found; run experiments/e2e_acceptance/acceptance.py --batch tts"
        )
    return np.concatenate([load_clip(path), silence(1.0)]).astype(np.float32)


def with_bgm(speech: Float32Array, gain: float = 0.25) -> Float32Array:
    """在人聲底下墊 BGM（MUSAN 器樂曲，循環到同長度）；沒有 BGM 檔時原樣回傳。"""
    if not BGM_SOURCE.exists():
        logger.warning("BGM source %s not found; using speech only", BGM_SOURCE)
        return speech
    bgm = load_clip(BGM_SOURCE)
    reps = int(np.ceil(speech.size / bgm.size))
    bed = np.tile(bgm, reps)[: speech.size] * gain
    return np.clip(speech + bed, -1.0, 1.0).astype(np.float32)


class LoopPlayer:
    """背景循環播放 16 kHz 單聲道音訊到虛擬喇叭，直到 `stop()`。"""

    def __init__(self) -> None:
        import pyaudiowpatch

        self._mod = pyaudiowpatch
        self._pa = pyaudiowpatch.PyAudio()
        self.device = find_output_device(self._mod, self._pa)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.error: str | None = None

    @property
    def playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def start(self, audio16k: Float32Array) -> None:
        self.stop()
        dev = self.device
        audio = resample(audio16k, SR, dev.rate)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        frames = np.repeat(pcm[:, None], dev.channels, axis=1)
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, args=(frames, self._stop), name="soak-player", daemon=True
        )
        self._thread.start()

    def _run(self, frames: np.ndarray, stop: threading.Event) -> None:
        dev = self.device
        block = dev.rate // 10
        stream = self._pa.open(
            format=self._mod.paInt16,
            channels=dev.channels,
            rate=dev.rate,
            output=True,
            output_device_index=dev.index,
            frames_per_buffer=block,
        )
        try:
            while not stop.is_set():
                for i in range(0, frames.shape[0], block):
                    if stop.is_set():
                        break
                    stream.write(frames[i : i + block].tobytes())
        except OSError as exc:
            self.error = f"{type(exc).__name__}: {exc}"
            logger.error("playback failed: %s", exc)
        finally:
            stream.stop_stream()
            stream.close()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(5)
            self._thread = None

    def close(self) -> None:
        self.stop()
        self._pa.terminate()


# --------------------------------------------------------------------------- 引擎


def engine_command(which: str) -> tuple[list[str], Path]:
    """`dev`：目前的原始碼（venv 的 python -m kikitori engine）；`release`：打包好的引擎。"""
    if which == "release":
        if not RELEASE_ENGINE.exists():
            raise FileNotFoundError(
                f"{RELEASE_ENGINE} not found; set KIKITORI_SOAK_ENGINE or build it with "
                "uv run python packaging/engine/build.py"
            )
        return [str(RELEASE_ENGINE)], RELEASE_ENGINE.parent
    return [sys.executable, "-m", "kikitori", "engine"], ROOT


def start_engine(
    which: str,
    run_dir: Path,
    *,
    label: str = "engine",
    extra_env: dict[str, str] | None = None,
) -> EngineClient:
    """以隔離的設定資料夾啟動引擎（不讀、不寫使用者的設定檔）。"""
    cmd, cwd = engine_command(which)
    config_dir = run_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("KIKITORI_ENGINE_FAKE")
    }
    env["KIKITORI_CONFIG_DIR"] = str(config_dir)
    env.update(extra_env or {})
    client = EngineClient(
        cmd, env=env, cwd=cwd, stderr_log=run_dir / f"{label}-stderr.log"
    )
    client.request("engine.info", timeout=120)
    return client


def find_loopback_id(client: EngineClient) -> str:
    devices = client.request("devices.list", timeout=30)["devices"]
    for d in devices:
        if d["kind"] == "loopback" and TARGET_OUTPUT in d["name"]:
            return str(d["id"])
    raise RuntimeError(f"no loopback device named {TARGET_OUTPUT!r}")


def base_settings(
    device_id: str,
    translation_base_url: str,
    translation_model: str,
    *,
    keep_warm_secs: int = 240,
    fast_model: str = "small",
    final_model: str = "large-v3-turbo",
) -> dict[str, Any]:
    """測試用的設定：GPU（float16）、影片翻譯 ja → zh-TW、翻譯經過計數代理。"""
    return {
        "mode": "media",
        "source_language": "ja",
        "target_language": "zh-TW",
        "media": {"device_id": device_id},
        "asr": {
            "fast_model": fast_model,
            "final_model": final_model,
            "device": "cuda",
            "fast_compute_type": "float16",
            "final_compute_type": "float16",
        },
        "translation": {
            "enabled": True,
            "base_url": translation_base_url,
            "fast_model": translation_model,
            "final_model": translation_model,
            "keep_warm_secs": keep_warm_secs,
        },
    }


def status_pred(
    sid: int | None, states: Sequence[str]
) -> Callable[[dict[str, Any]], bool]:
    def pred(m: dict[str, Any]) -> bool:
        return (
            m.get("event") == "status"
            and m["data"]["state"] in states
            and (sid is None or m["data"].get("session_id") in (sid, None))
        )

    return pred


@dataclass
class SessionTimes:
    sid: int
    t_request: float
    t_running: float | None = None
    t_stop: float | None = None
    t_idle: float | None = None
    end_state: str | None = None
    end_code: str | None = None
    end_detail: str | None = None
    since: int = 0


def start_session(
    client: EngineClient,
    *,
    mode: str = "media",
    device_id: str | None = None,
    timeout: float = 240.0,
    expect_running: bool = True,
) -> SessionTimes:
    """session.start 並等到 running（或 error）。"""
    since = len(client.events)
    params: dict[str, Any] = {"mode": mode}
    if device_id is not None:
        params["device_id"] = device_id
    t_req = time.monotonic()
    sid = int(client.request("session.start", params, timeout=30)["session_id"])
    st = SessionTimes(sid, t_req, since=since)
    rec = client.wait_event(
        lambda m: (
            m.get("event") == "status"
            and m["data"].get("session_id") == sid
            and m["data"]["state"] in ("running", "error", "idle")
        ),
        timeout=timeout,
        since=since,
    )
    data = rec.msg["data"]
    if data["state"] == "running":
        st.t_running = rec.t
    else:
        st.end_state = data["state"]
        st.end_code = data.get("code")
        st.end_detail = data.get("detail")
        st.t_idle = rec.t
        if expect_running:
            raise RuntimeError(f"session {sid} did not start: {data}")
    return st


def stop_session(client: EngineClient, st: SessionTimes, timeout: float = 30.0) -> None:
    st.t_stop = time.monotonic()
    client.request("session.stop", timeout=timeout)
    rec = client.wait_event(
        lambda m: (
            m.get("event") == "status"
            and m["data"].get("session_id") == st.sid
            and m["data"]["state"] in ("idle", "error")
        ),
        timeout=timeout,
        since=st.since,
    )
    st.t_idle = rec.t
    st.end_state = rec.msg["data"]["state"]
    st.end_code = rec.msg["data"].get("code")


def subtitle_counts(client: EngineClient, sid: int) -> dict[str, int]:
    finals = partials = translated = 0
    for r in list(client.events):
        m = r.msg
        if m.get("event") != "subtitle" or m["data"].get("session_id") != sid:
            continue
        if m["data"]["is_final"]:
            if m["data"]["original"].strip():
                finals += 1
                if m["data"]["translation"].strip():
                    translated += 1
        else:
            partials += 1
    return {"finals": finals, "partials": partials, "translated_finals": translated}


def log_events(
    client: EngineClient, t0: float = 0.0, t1: float = 1e18
) -> list[Received]:
    return [
        r
        for r in list(client.events)
        if r.msg.get("event") == "log" and t0 <= r.t <= t1
    ]


def warnings_in(client: EngineClient, t0: float, t1: float) -> list[str]:
    return [
        r.msg["data"]["message"][:200]
        for r in log_events(client, t0, t1)
        if r.msg["data"]["level"] in ("warning", "error")
    ]


__all__ = [
    "EngineClient",
    "EngineError",
    "Received",
]
