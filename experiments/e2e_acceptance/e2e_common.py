"""端到端驗收的共用工具：stdio 引擎客戶端、WAV 讀寫與重採樣、播放到指定的虛擬輸出裝置。

暫時性實驗，不屬於正式程式碼。只用標準庫、numpy 與既有依賴 PyAudioWPatch（播放）。
`acceptance.py`（引擎協定驗收）與 `gui_acceptance.py`（桌面程式驗收）共用。

安全規則：播放只允許名稱含 `TARGET_OUTPUT` 的 WASAPI 輸出裝置，而且它不能是系統預設輸出
（預設輸出是使用者的耳機，測試聲音不能播到那裡）。
"""

from __future__ import annotations

import json
import logging
import os
import queue
import subprocess
import threading
import time
import wave
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
TRACKS = ROOT / "experiments" / "pipecat_vad" / "data" / "tracks"
SR = 16000
TARGET_OUTPUT = "Steam Streaming Speakers"
"""測試聲音唯一允許的輸出裝置（虛擬喇叭，使用者聽不到）。"""

logger = logging.getLogger("e2e")

Float32Array = npt.NDArray[np.float32]


# --------------------------------------------------------------------------- WAV


def read_wav(path: Path) -> tuple[Float32Array, int]:
    """讀 16-bit PCM WAV，回傳 (單聲道 float32 [-1, 1], 取樣率)。"""
    with wave.open(str(path), "rb") as w:
        if w.getsampwidth() != 2:
            raise ValueError(f"{path}: only 16-bit PCM is supported")
        channels = w.getnchannels()
        rate = w.getframerate()
        raw = w.readframes(w.getnframes())
    data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return data.astype(np.float32), rate


def write_wav(path: Path, audio: Float32Array, rate: int) -> None:
    """寫 16-bit 單聲道 WAV。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    pcm = np.clip(audio, -1.0, 1.0)
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes((pcm * 32767.0).astype("<i2").tobytes())


def resample(audio: Float32Array, rate_in: int, rate_out: int) -> Float32Array:
    """線性內插重採樣（只用在測試播放；之後 kikitori 的擷取端會再濾波降到 16 kHz）。"""
    if rate_in == rate_out or audio.size == 0:
        return audio
    n_out = round(audio.size * rate_out / rate_in)
    x_out = np.arange(n_out, dtype=np.float64) / rate_out
    x_in = np.arange(audio.size, dtype=np.float64) / rate_in
    return np.interp(x_out, x_in, audio).astype(np.float32)


def silence(seconds: float, rate: int = SR) -> Float32Array:
    return np.zeros(round(seconds * rate), dtype=np.float32)


# --------------------------------------------------------------------------- TTS


TTS_SCRIPT = r"""
$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Speech
$s = New-Object System.Speech.Synthesis.SpeechSynthesizer
$s.SelectVoice($env:KK_TTS_VOICE)
$fmt = New-Object System.Speech.AudioFormat.SpeechAudioFormatInfo(16000, [System.Speech.AudioFormat.AudioBitsPerSample]::Sixteen, [System.Speech.AudioFormat.AudioChannel]::Mono)
$s.SetOutputToWaveFile($env:KK_TTS_OUT, $fmt)
$s.Speak($env:KK_TTS_TEXT)
$s.Dispose()
"""


def synthesize(text: str, voice: str, out: Path, timeout: float = 60.0) -> None:
    """用 Windows 內建 TTS（System.Speech）把一句話合成 16 kHz WAV。

    文字以環境變數傳給 PowerShell，不經 shell、不拼進指令字串。
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, KK_TTS_VOICE=voice, KK_TTS_OUT=str(out), KK_TTS_TEXT=text)
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", TTS_SCRIPT],
        env=env,
        check=True,
        timeout=timeout,
        capture_output=True,
    )


def synthesize_lines(
    lines: Sequence[str], voice: str, out: Path, gap_secs: float = 1.5
) -> list[tuple[int, int]]:
    """合成多句並以靜音串接；回傳每句在 16 kHz 時間軸上的 (起點, 終點) 樣本。"""
    parts: list[Float32Array] = [silence(0.5)]
    segments: list[tuple[int, int]] = []
    pos = parts[0].size
    for i, line in enumerate(lines):
        tmp = out.with_name(f"{out.stem}.{i}.tmp.wav")
        synthesize(line, voice, tmp)
        audio, rate = read_wav(tmp)
        tmp.unlink()
        audio = resample(audio, rate, SR)
        # 去掉 TTS 前後的靜音，讓真值區間就是說話的範圍。
        voiced = np.flatnonzero(np.abs(audio) > 0.01)
        if voiced.size:
            audio = audio[voiced[0] : voiced[-1] + 1]
        segments.append((pos, pos + audio.size))
        parts += [audio, silence(gap_secs)]
        pos += audio.size + parts[-1].size
    write_wav(out, np.concatenate(parts), SR)
    return segments


# --------------------------------------------------------------------------- 播放


@dataclass
class OutputDevice:
    index: int
    name: str
    channels: int
    rate: int


def find_output_device(
    pa_mod: Any, pa: Any, needle: str = TARGET_OUTPUT
) -> OutputDevice:
    """找名稱含 `needle` 的 WASAPI 輸出裝置；它若是系統預設輸出就拒絕。"""
    default_name = str(pa.get_default_wasapi_device(d_out=True)["name"])
    for info in pa.get_device_info_generator_by_host_api(host_api_type=pa_mod.paWASAPI):
        if info.get("isLoopbackDevice") or int(info["maxOutputChannels"]) <= 0:
            continue
        name = str(info["name"])
        if needle in name:
            if name == default_name:
                raise RuntimeError(
                    f"{name!r} is the default output device; refusing to play test audio"
                )
            return OutputDevice(
                int(info["index"]),
                name,
                min(2, int(info["maxOutputChannels"])),
                round(float(info["defaultSampleRate"])),
            )
    raise RuntimeError(f"no WASAPI output device whose name contains {needle!r}")


@dataclass
class Playback:
    label: str
    started_at: float = 0.0  # 第一個樣本實際送出的牆鐘時間（monotonic，含輸出延遲）
    duration: float = 0.0
    done: threading.Event = field(default_factory=threading.Event)
    error: str | None = None


class Player:
    """把 16 kHz 單聲道音訊播放到 `TARGET_OUTPUT`，記錄開始時刻。"""

    def __init__(self) -> None:
        import pyaudiowpatch

        self._mod = pyaudiowpatch
        self._pa = pyaudiowpatch.PyAudio()
        self.device = find_output_device(self._mod, self._pa)

    def play(self, label: str, audio16k: Float32Array) -> Playback:
        """阻塞播放到結束；回傳開始時刻與長度。"""
        dev = self.device
        audio = resample(audio16k, SR, dev.rate)
        pcm = (np.clip(audio, -1.0, 1.0) * 32767.0).astype("<i2")
        frames = np.repeat(pcm[:, None], dev.channels, axis=1)
        block = dev.rate // 10
        stream = self._pa.open(
            format=self._mod.paInt16,
            channels=dev.channels,
            rate=dev.rate,
            output=True,
            output_device_index=dev.index,
            frames_per_buffer=block,
        )
        pb = Playback(label, duration=audio.size / dev.rate)
        try:
            latency = float(stream.get_output_latency())
            pb.started_at = time.monotonic() + latency
            for i in range(0, frames.shape[0], block):
                stream.write(frames[i : i + block].tobytes())
            # write() 回傳時最後一塊還在緩衝區；等它播完再關。
            time.sleep(latency)
        finally:
            stream.stop_stream()
            stream.close()
            pb.done.set()
        return pb

    def close(self) -> None:
        self._pa.terminate()


# --------------------------------------------------------------------------- 引擎


@dataclass
class Received:
    t: float
    msg: dict[str, Any]


class EngineError(RuntimeError):
    def __init__(self, method: str, error: dict[str, Any]) -> None:
        super().__init__(f"{method}: {error.get('code')}: {error.get('message')}")
        self.code = error.get("code")
        self.error = error


class EngineClient:
    """以 NDJSON over stdio 驅動 `kikitori engine`（或正式版的 kikitori-engine.exe）。"""

    def __init__(
        self,
        command: Sequence[str],
        *,
        env: dict[str, str],
        cwd: Path,
        stderr_log: Path,
    ) -> None:
        stderr_log.parent.mkdir(parents=True, exist_ok=True)
        self._stderr_file = stderr_log.open("wb")
        self.proc = subprocess.Popen(
            list(command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=self._stderr_file,
            cwd=str(cwd),
            env=env,
        )
        self._next_id = 1
        self._lock = threading.Lock()
        self._responses: dict[int, Received] = {}
        self._cond = threading.Condition()
        self.events: list[Received] = []
        self.bad_lines: list[bytes] = []
        self.event_queue: queue.Queue[Received] = queue.Queue()
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()

    def _read(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            t = time.monotonic()
            try:
                msg = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                logger.error("engine wrote a non-JSON line: %r", line[:200])
                self.bad_lines.append(line)
                continue
            rec = Received(t, msg)
            with self._cond:
                if "event" in msg:
                    self.events.append(rec)
                    self.event_queue.put(rec)
                elif isinstance(msg.get("id"), int):
                    self._responses[msg["id"]] = rec
                self._cond.notify_all()

    def request(
        self, method: str, params: dict[str, Any] | None = None, timeout: float = 30.0
    ) -> dict[str, Any]:
        with self._lock:
            rid = self._next_id
            self._next_id += 1
            req: dict[str, Any] = {"id": rid, "method": method}
            if params is not None:
                req["params"] = params
            assert self.proc.stdin is not None
            self.proc.stdin.write(json.dumps(req, ensure_ascii=False).encode() + b"\n")
            self.proc.stdin.flush()
        deadline = time.monotonic() + timeout
        with self._cond:
            while rid not in self._responses:
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError(f"{method}: no response within {timeout} s")
                if self.proc.poll() is not None and rid not in self._responses:
                    raise RuntimeError(
                        f"{method}: engine exited ({self.proc.returncode})"
                    )
                self._cond.wait(min(left, 0.5))
            rec = self._responses.pop(rid)
        if "error" in rec.msg:
            raise EngineError(method, rec.msg["error"])
        return rec.msg["result"]

    def wait_event(
        self,
        predicate: Callable[[dict[str, Any]], bool],
        timeout: float,
        since: int = 0,
    ) -> Received:
        """等第一個（從第 `since` 筆起）符合條件的事件。"""
        deadline = time.monotonic() + timeout
        with self._cond:
            while True:
                for rec in self.events[since:]:
                    if predicate(rec.msg):
                        return rec
                left = deadline - time.monotonic()
                if left <= 0:
                    raise TimeoutError("event not received in time")
                self._cond.wait(min(left, 0.5))

    def close(self, timeout: float = 15.0) -> int:
        """送 engine.shutdown（若還活著），等結束並回傳結束碼。"""
        if self.proc.poll() is None:
            try:
                self.request("engine.shutdown", timeout=5)
            except (TimeoutError, RuntimeError, OSError) as exc:
                logger.warning("engine.shutdown failed: %s", exc)
        try:
            code = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            logger.error("engine did not exit in %.0f s; killing it", timeout)
            self.proc.kill()
            code = self.proc.wait(timeout=5)
        self._reader.join(timeout=5)
        self._stderr_file.close()
        return code
