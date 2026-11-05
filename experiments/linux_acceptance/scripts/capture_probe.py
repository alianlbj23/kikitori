"""Linux 擷取驗收：在容器裡的 PulseAudio（null sink）上跑 kikitori 真正的 soundcard 後端。

暫時性實驗工具，不屬於正式程式碼。對應 `tasks/acceptance-report.md` 的 Linux 檢查項目與
`docs/specs/capture.md` 的 Linux 設計：預設 monitor 跟著預設 sink、16 kHz 單聲道 int16、
播放停止後仍持續交付、裝置消失與沒有伺服器時的例外。

每項檢查在獨立的子行程執行：soundcard 匯入時就連上 PulseAudio 並保留全域連線，
同一行程內殺掉伺服器或換設定會互相影響。

執行（容器內，見 `../run.sh`）：`/opt/venv/bin/python scripts/capture_probe.py`
"""

import itertools
import json
import math
import os
import shutil
import subprocess
import sys
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path

import numpy as np
import numpy.typing as npt

from kikitori.capture import (
    SAMPLE_RATE,
    AudioSource,
    BackendUnavailableError,
    CaptureError,
    DeviceLostError,
    list_devices,
    open_source,
)

DATA_DIR = Path(os.environ.get("KLA_DATA", "/data"))
RESULTS_DIR = Path(os.environ.get("KLA_RESULTS", "/results"))
CMD_TIMEOUT = 10.0
READ_TIMEOUT = 0.1
TONE_HZ = 1000.0
TONE_SECS = 3.0
SINKS = ("speakers", "headphones")
SERVER = os.environ.get("KLA_SERVER", "pulseaudio")


@dataclass
class CheckResult:
    """一項檢查的結果；`details` 是給 README 用的量測值。"""

    name: str
    ok: bool
    summary: str
    details: dict[str, object] = field(default_factory=dict)


# ---------------------------------------------------------------- PulseAudio


def run(cmd: Sequence[str], *, check: bool = True) -> str:
    """執行外部指令（不經 shell、有 timeout），回傳 stdout。"""
    proc = subprocess.run(
        list(cmd), capture_output=True, text=True, timeout=CMD_TIMEOUT, check=False
    )
    if check and proc.returncode != 0:
        raise RuntimeError(
            f"{cmd[0]} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def pactl(*args: str, check: bool = True) -> str:
    """呼叫 pactl。"""
    return run(["pactl", *args], check=check)


def server_running() -> bool:
    """PulseAudio 協定的伺服器是否可連線。"""
    return (
        subprocess.run(
            ["pactl", "info"], capture_output=True, timeout=CMD_TIMEOUT, check=False
        ).returncode
        == 0
    )


def _launch_pipewire() -> None:
    """pipewire + WirePlumber + pipewire-pulse（需要 XDG_RUNTIME_DIR 與 session D-Bus，見 run.sh）。"""
    for prog in ("pipewire", "wireplumber", "pipewire-pulse"):
        log = open(f"/tmp/{prog}.log", "ab")  # noqa: SIM115 - 背景行程一直寫到結束
        subprocess.Popen([prog], stdout=log, stderr=subprocess.STDOUT)
        time.sleep(0.5)


def start_server() -> None:
    """啟動音訊伺服器（`KLA_SERVER`：pulseaudio 或 pipewire），再加上兩個 null sink
    當「喇叭」與「耳機」。PulseAudio 用發行版預設設定（default.pa：suspend-on-idle、
    stream restore 等）。"""
    if not server_running():
        if SERVER == "pipewire":
            _launch_pipewire()
        else:
            run(
                [
                    "pulseaudio",
                    "-D",
                    "--exit-idle-time=-1",
                    "--log-target=file:/tmp/pa.log",
                ]
            )
        deadline = time.monotonic() + 10
        while not server_running():
            if time.monotonic() > deadline:
                raise RuntimeError(f"{SERVER} did not start; see /tmp/*.log")
            time.sleep(0.1)
    existing = pactl("list", "short", "sinks")
    for name in SINKS:
        if f"\t{name}\t" not in existing:
            pactl(
                "load-module",
                "module-null-sink",
                f"sink_name={name}",
                f"sink_properties=device.description={name.capitalize()}",
            )
    pactl("set-default-sink", SINKS[0])


def kill_server() -> None:
    """結束 PulseAudio 協定的伺服器（PipeWire 只結束 pipewire-pulse）。"""
    if SERVER == "pipewire":
        run(["pkill", "-x", "pipewire-pulse"], check=False)
    else:
        run(["pulseaudio", "-k"], check=False)


def stop_server() -> None:
    """結束音訊伺服器（沒有在跑也不算錯）。"""
    kill_server()
    deadline = time.monotonic() + 5
    while server_running() and time.monotonic() < deadline:
        time.sleep(0.1)


def module_id(sink_name: str) -> str:
    """找出建立某個 null sink 的模組編號。"""
    for line in pactl("list", "short", "modules").splitlines():
        parts = line.split("\t")
        if len(parts) >= 3 and f"sink_name={sink_name}" in parts[2]:
            return parts[0]
    raise RuntimeError(f"no module for sink {sink_name}")


def play(path: Path, sink: str) -> subprocess.Popen[bytes]:
    """以 paplay 把 WAV 播到指定 sink（背景）。"""
    return subprocess.Popen(
        ["paplay", "-d", sink, str(path)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
    )


# ---------------------------------------------------------------- 測試音訊


def write_wav(
    path: Path, samples: npt.NDArray[np.int16], rate: int, channels: int
) -> None:
    """寫 PCM16 WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.astype("<i2").tobytes())


def tone_file() -> Path:
    """48 kHz 立體聲 1 kHz 正弦波（−6 dBFS）。"""
    path = DATA_DIR / "tone_1k_48k_stereo.wav"
    if not path.exists():
        t = np.arange(int(48000 * TONE_SECS)) / 48000
        mono = (0.5 * 32767 * np.sin(2 * np.pi * TONE_HZ * t)).astype(np.int16)
        write_wav(path, np.repeat(mono, 2), 48000, 2)
    return path


def japanese_clip() -> Path:
    """把本機的日文動漫片段（git 忽略）轉成 44.1 kHz 立體聲 WAV，模擬一般影片播放。"""
    out = DATA_DIR / "clip_ja_44k_stereo.wav"
    src = DATA_DIR / "anime_006.mp3"
    if not out.exists():
        if not src.exists() or shutil.which("ffmpeg") is None:
            raise RuntimeError(f"need {src} and ffmpeg to build {out}")
        run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-y",
                "-i",
                str(src),
                "-ar",
                "44100",
                "-ac",
                "2",
                str(out),
            ]
        )
    return out


# ---------------------------------------------------------------- 錄音與分析


@dataclass
class Recording:
    """一次錄音：每次 `read()` 的結果與時間。"""

    chunks: list[tuple[float, npt.NDArray[np.int16]]]
    started: float
    ended: float
    format_ok: bool
    error: BaseException | None = None

    @property
    def samples(self) -> npt.NDArray[np.int16]:
        if not self.chunks:
            return np.zeros(0, dtype=np.int16)
        return np.concatenate([c for _, c in self.chunks])

    def window(self, start: float, end: float) -> npt.NDArray[np.int16]:
        """依樣本位置（秒）取一段。"""
        s = self.samples
        return s[int(start * SAMPLE_RATE) : int(end * SAMPLE_RATE)]


def record(
    source: AudioSource,
    seconds: float,
    during: Callable[[float], None] | None = None,
) -> Recording:
    """開始來源、讀 `seconds` 秒；`during(elapsed)` 每次讀完呼叫一次（用來在中途觸發事件）。
    讀取拋出 `CaptureError` 時停止並記下例外。"""
    chunks: list[tuple[float, npt.NDArray[np.int16]]] = []
    format_ok = True
    error: BaseException | None = None
    source.start()
    started = time.monotonic()
    try:
        while (now := time.monotonic()) - started < seconds:
            try:
                chunk = source.read(READ_TIMEOUT)
            except CaptureError as exc:
                error = exc
                break
            if chunk.size:
                format_ok &= chunk.dtype == np.int16 and chunk.ndim == 1
                chunks.append((now - started, chunk))
            if during is not None:
                during(now - started)
    finally:
        source.stop()
    return Recording(chunks, started, time.monotonic(), format_ok, error)


def rms(samples: npt.NDArray[np.int16]) -> float:
    """int16 樣本的 RMS（原始刻度）。"""
    if samples.size == 0:
        return 0.0
    return float(np.sqrt(np.mean(np.square(samples.astype(np.float64)))))


def peak_hz(samples: npt.NDArray[np.int16]) -> float:
    """頻譜峰值的頻率（確認重採樣後音高正確）。"""
    if samples.size < 1024:
        return 0.0
    spectrum = np.abs(
        np.fft.rfft(samples.astype(np.float64) * np.hanning(samples.size))
    )
    return float(np.argmax(spectrum) * SAMPLE_RATE / samples.size)


def max_gap(rec: Recording) -> float:
    """相鄰兩次有資料的 read 之間最長的間隔（秒）：衡量是否持續交付。"""
    times = [t for t, _ in rec.chunks]
    return max((b - a for a, b in itertools.pairwise(times)), default=math.inf)


# ---------------------------------------------------------------- 各項檢查


def check_devices() -> CheckResult:
    """列裝置；`pactl set-default-sink` 換預設輸出後，預設 loopback 要跟著換。"""
    seen: dict[str, object] = {}
    ok = True
    for sink in (*SINKS, SINKS[0]):
        pactl("set-default-sink", sink)
        devices = list_devices()
        defaults = [d.id for d in devices if d.is_default and d.kind == "loopback"]
        expected = f"soundcard:loopback:{sink}.monitor"
        resolved = getattr(open_source(None, "loopback"), "microphone", None)
        resolved_id = getattr(resolved, "id", None)
        seen[sink] = {
            "default_loopback": defaults,
            "open_source_default": resolved_id,
            "devices": [(d.kind, d.name, d.id, d.is_default) for d in devices],
        }
        ok &= defaults == [expected] and resolved_id == f"{sink}.monitor"
    mic_defaults = [
        d for d in list_devices() if d.kind == "microphone" and d.is_default
    ]
    seen["microphone_default_while_default_source_is_monitor"] = [
        d.id for d in mic_defaults
    ]
    return CheckResult(
        "devices",
        ok and not mic_defaults,
        "default loopback follows set-default-sink"
        if ok
        else "default loopback mismatch",
        seen,
    )


def check_loopback_tone() -> CheckResult:
    """預設 monitor 錄 1 kHz：播放時 RMS > 0、音高正確；停止後仍持續送出（靜音）。

    null sink 在 suspend 後第一次被錄時約 1.9 s 才送出第一筆資料（見 README），
    所以 2.5 s 後才開始播放，並以「read 的牆鐘時間」而不是樣本位置判斷播放前後。
    """
    pactl("set-default-sink", SINKS[0])
    source = open_source(None, "loopback")
    player: dict[str, subprocess.Popen[bytes]] = {}
    marks: dict[str, float] = {}

    def during(elapsed: float) -> None:
        if "p" not in player and elapsed >= 2.5:
            player["p"] = play(tone_file(), SINKS[0])
        elif "p" in player and "end" not in marks and player["p"].poll() is not None:
            marks["end"] = elapsed

    rec = record(source, 2.5 + TONE_SECS + 3.5, during)
    player["p"].wait(timeout=CMD_TIMEOUT)
    end = marks.get("end", math.inf)

    def between(lo: float, hi: float) -> npt.NDArray[np.int16]:
        parts = [c for t, c in rec.chunks if lo < t <= hi]
        return np.concatenate(parts) if parts else np.zeros(0, np.int16)

    first = rec.chunks[0][0] if rec.chunks else math.inf
    steady = between(first, rec.ended - rec.started)
    steady_secs = (rec.ended - rec.started) - first
    playing = between(3.3, 5.0)
    after = between(end + 0.5, end + 3.0)
    after_secs = min(3.0, rec.ended - rec.started - end) - 0.5
    details: dict[str, object] = {
        "first_data_after_s": round(first, 3),
        "samples": int(rec.samples.size),
        "samples_per_sec_after_first": round(steady.size / steady_secs, 1),
        "format_ok": rec.format_ok,
        "rms_playing": round(rms(playing), 1),
        "peak_hz_playing": round(peak_hz(playing), 1),
        "playback_ended_at_s": round(end, 3),
        "rms_after_stop": round(rms(after), 2),
        "samples_per_sec_after_stop": round(after.size / after_secs, 1),
        "max_gap_between_reads_s": round(max_gap(rec), 3),
        "dropped_samples": getattr(source, "dropped_samples", None),
        "error": repr(rec.error) if rec.error else None,
    }
    write_wav(RESULTS_DIR / "loopback_tone.wav", rec.samples, SAMPLE_RATE, 1)
    ok = (
        rec.error is None
        and rec.format_ok
        and abs(steady.size / steady_secs - SAMPLE_RATE) < 0.05 * SAMPLE_RATE
        and rms(playing) > 1000
        and abs(peak_hz(playing) - TONE_HZ) < 10
        and abs(after.size / after_secs - SAMPLE_RATE) < 0.05 * SAMPLE_RATE
        and rms(after) == 0
        and max_gap(rec) < 0.5
    )
    return CheckResult("loopback_tone", ok, "1 kHz via default monitor", details)


def check_loopback_speech() -> CheckResult:
    """日文片段（44.1 kHz 立體聲）經 monitor 錄下，存成 WAV 供人耳確認。"""
    clip = japanese_clip()
    with wave.open(str(clip), "rb") as w:
        clip_secs = w.getnframes() / w.getframerate()
    source = open_source(f"soundcard:loopback:{SINKS[1]}.monitor", "loopback")
    player: dict[str, subprocess.Popen[bytes]] = {}

    def during(elapsed: float) -> None:
        if "p" not in player and elapsed >= 0.3:
            player["p"] = play(clip, SINKS[1])

    rec = record(source, clip_secs + 1.5, during)
    player["p"].wait(timeout=CMD_TIMEOUT)
    out = RESULTS_DIR / "loopback_speech.wav"
    write_wav(out, rec.samples, SAMPLE_RATE, 1)
    speech = rec.window(0.5, clip_secs)
    details: dict[str, object] = {
        "clip_secs": round(clip_secs, 2),
        "samples": rec.samples.size,
        "rms_speech": round(rms(speech), 1),
        "format_ok": rec.format_ok,
        "saved": str(out),
        "error": repr(rec.error) if rec.error else None,
    }
    ok = rec.error is None and rec.format_ok and rms(speech) > 100
    return CheckResult(
        "loopback_speech", ok, "Japanese clip via explicit monitor id", details
    )


def check_microphone() -> CheckResult:
    """虛擬麥克風：把 headphones.monitor 以 module-remap-source 變成一般 source。"""
    mod = pactl(
        "load-module",
        "module-remap-source",
        f"master={SINKS[1]}.monitor",
        "source_name=virtmic",
        "source_properties=device.description=VirtualMic",
    ).strip()
    try:
        pactl("set-default-source", "virtmic")
        mics = [d for d in list_devices() if d.kind == "microphone"]
        source = open_source(None, "microphone")
        player: dict[str, subprocess.Popen[bytes]] = {}

        def during(elapsed: float) -> None:
            if "p" not in player and elapsed >= 0.3:
                player["p"] = play(tone_file(), SINKS[1])

        rec = record(source, 0.3 + TONE_SECS + 0.7, during)
        player["p"].wait(timeout=CMD_TIMEOUT)
        playing = rec.window(0.8, 2.8)
        details: dict[str, object] = {
            "microphones": [(d.id, d.name, d.is_default) for d in mics],
            "opened": getattr(getattr(source, "microphone", None), "id", None),
            "samples": rec.samples.size,
            "rms_playing": round(rms(playing), 1),
            "peak_hz_playing": round(peak_hz(playing), 1),
            "format_ok": rec.format_ok,
        }
        ok = (
            any(d.id == "soundcard:microphone:virtmic" and d.is_default for d in mics)
            and details["opened"] == "virtmic"
            and rec.format_ok
            and rms(playing) > 1000
            and abs(peak_hz(playing) - TONE_HZ) < 10
        )
        return CheckResult(
            "microphone", ok, "remapped null-sink monitor as microphone", details
        )
    finally:
        pactl("unload-module", mod, check=False)
        pactl("set-default-source", f"{SINKS[0]}.monitor", check=False)


def check_device_lost() -> CheckResult:
    """錄音中移除裝置（模擬拔掉 USB）：預期 `DeviceLostError`。

    預設 source 設成 440 Hz 的 sine source（模擬桌機的內建麥克風），若伺服器把串流
    搬過去而沒有報錯，錄到的就會是 440 Hz——這正是修正前的行為。
    """
    pactl(
        "load-module",
        "module-null-sink",
        "sink_name=usbdev",
        "sink_properties=device.description=USB",
    )
    # pipewire-pulse 沒有 module-sine-source；那時只看有沒有 DeviceLostError。
    mic_mod = pactl(
        "load-module",
        "module-sine-source",
        "source_name=builtin_mic",
        "frequency=440",
        check=False,
    ).strip()
    if mic_mod:
        pactl("set-default-source", "builtin_mic")
    try:
        source = open_source("soundcard:loopback:usbdev.monitor", "loopback")
        removed: dict[str, float] = {}

        def during(elapsed: float) -> None:
            if "t" not in removed and elapsed >= 2.5:
                pactl("unload-module", module_id("usbdev"))
                removed["t"] = elapsed

        rec = record(source, 7.0, during)
    finally:
        pactl("set-default-source", f"{SINKS[0]}.monitor", check=False)
        if mic_mod:
            pactl("unload-module", mic_mod, check=False)
    t_removed = removed.get("t", math.inf)
    after_parts = [c for t, c in rec.chunks if t > t_removed]
    after = np.concatenate(after_parts) if after_parts else np.zeros(0, np.int16)
    details: dict[str, object] = {
        "sine_mic_as_default_source": bool(mic_mod),
        "removed_at_s": round(t_removed, 3),
        "error": repr(rec.error),
        "error_after_s": round(rec.ended - rec.started - t_removed, 3),
        "samples_after_removal": int(after.size),
        "rms_after_removal": round(rms(after), 1),
        "peak_hz_after_removal": round(peak_hz(after), 1),
    }
    ok = isinstance(rec.error, DeviceLostError)
    return CheckResult(
        "device_lost",
        ok,
        "DeviceLostError after unload" if ok else "no DeviceLostError (see details)",
        details,
    )


def check_server_missing() -> CheckResult:
    """沒有伺服器（PULSE_SERVER 指到不存在的 socket）：列裝置與開來源都要是 BackendUnavailableError。"""
    results: dict[str, object] = {}
    ok = True
    for label, fn in (
        ("list_devices", lambda: list_devices()),
        ("open_source", lambda: open_source(None, "loopback")),
    ):
        try:
            fn()
            results[label] = "no error"
            ok = False
        except BackendUnavailableError as exc:
            results[label] = f"BackendUnavailableError: {exc}"
        except Exception as exc:  # noqa: BLE001 - 檢查工具：記錄非預期的例外類型
            results[label] = f"{type(exc).__name__}: {exc}"
            ok = False
    return CheckResult("server_missing", ok, "no PulseAudio server", results)


def check_server_killed() -> CheckResult:
    """錄音中結束 PulseAudio：預期 `read()` 拋 `CaptureError` 子類別而不是卡住。"""
    source = open_source(f"soundcard:loopback:{SINKS[0]}.monitor", "loopback")
    killed: dict[str, float] = {}

    def during(elapsed: float) -> None:
        if "t" not in killed and elapsed >= 1.0:
            kill_server()
            killed["t"] = elapsed

    t0 = time.monotonic()
    rec = record(source, 6.0, during)
    details: dict[str, object] = {
        "killed_at_s": round(killed.get("t", -1), 3),
        "error": repr(rec.error),
        "error_type": type(rec.error).__name__ if rec.error else None,
        "stop_returned_after_s": round(time.monotonic() - t0, 2),
        "last_data_at_s": round(rec.chunks[-1][0], 3) if rec.chunks else None,
    }
    ok = isinstance(rec.error, CaptureError)
    return CheckResult("server_killed", ok, "PulseAudio killed during capture", details)


CHECKS: dict[str, Callable[[], CheckResult]] = {
    "devices": check_devices,
    "loopback_tone": check_loopback_tone,
    "loopback_speech": check_loopback_speech,
    "microphone": check_microphone,
    "device_lost": check_device_lost,
    "server_missing": check_server_missing,
    "server_killed": check_server_killed,
}


def run_child(name: str) -> CheckResult:
    """在子行程執行一項檢查，結果以 JSON 由 stdout 最後一行傳回。"""
    env = dict(os.environ)
    if name == "server_missing":
        env["PULSE_SERVER"] = "unix:/nonexistent/pulse/native"
    else:
        start_server()
    proc = subprocess.run(
        [sys.executable, __file__, "--child", name],
        capture_output=True,
        text=True,
        timeout=120,
        env=env,
        check=False,
    )
    if name == "server_killed":
        start_server()
    lines = proc.stdout.strip().splitlines()
    try:
        data = json.loads(lines[-1])
        return CheckResult(**data)
    except (IndexError, json.JSONDecodeError, TypeError):
        return CheckResult(
            name,
            False,
            f"child exited {proc.returncode}",
            {"stderr": proc.stderr[-2000:]},
        )


def run_all(names: Sequence[str]) -> list[CheckResult]:
    """依序執行並印出結果；整份結果寫到 results/capture_probe.json。"""
    results = []
    for name in names:
        result = run_child(name)
        results.append(result)
        print(f"[{'PASS' if result.ok else 'FAIL'}] {name}: {result.summary}")
        for key, value in result.details.items():
            print(f"    {key}: {value}")
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    (RESULTS_DIR / f"capture_probe_{SERVER}.json").write_text(
        json.dumps([asdict(r) for r in results], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return results


MENU = """
Linux 擷取驗收（PulseAudio null sink）
  1) 全部檢查
{items}
  0) 離開
"""


def menu() -> int:
    """互動選單。"""
    names = list(CHECKS)
    items = "\n".join(f"  {i}) {n}" for i, n in enumerate(names, start=2))
    while True:
        print(MENU.format(items=items))
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice in ("", "0"):
            return 0
        if choice == "1":
            results = run_all(names)
            return 0 if all(r.ok for r in results) else 1
        if choice.isdigit() and 2 <= int(choice) < 2 + len(names):
            run_all([names[int(choice) - 2]])
        else:
            print(f"無效的選項：{choice!r}")


def main(argv: Sequence[str]) -> int:
    """`--child NAME` 給 `run_child` 用；`--all` 給非互動的 run.sh；否則顯示選單。"""
    if len(argv) == 2 and argv[0] == "--child" and argv[1] in CHECKS:
        result = CHECKS[argv[1]]()
        print(json.dumps(asdict(result), ensure_ascii=False, default=str))
        return 0
    if argv == ["--all"]:
        return 0 if all(r.ok for r in run_all(list(CHECKS))) else 1
    return menu()


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
