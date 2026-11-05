"""capture 模組的手動檢查工具：互動選單列出裝置、錄音、顯示音量並存成 WAV。

暫時性實驗工具，不屬於正式程式碼。用來在真實機器上確認 loopback 與麥克風
真的能開啟並交付正確格式（int16、16 kHz、單聲道、一維）的資料。
執行：`uv run python experiments/capture_check/check_capture.py`
"""

import logging
import math
import sys
import time
import wave
from datetime import datetime
from pathlib import Path

import numpy as np

from kikitori.capture import (
    SAMPLE_RATE,
    AudioSource,
    CaptureError,
    DeviceInfo,
    DeviceKind,
    list_devices,
    open_source,
)

DATA_DIR = Path(__file__).resolve().parent / "data"
MAX_SECONDS = 600
READ_TIMEOUT = 0.1
METER_INTERVAL = 0.5
METER_WIDTH = 40


def dbfs(samples: np.ndarray) -> float:
    """int16 音訊的 RMS（dBFS）；全零時回傳 -inf。"""
    if len(samples) == 0:
        return -math.inf
    rms = float(np.sqrt(np.mean(np.square(samples.astype(np.float64))))) / 32768.0
    return 20 * math.log10(rms) if rms > 0 else -math.inf


def meter(level_db: float) -> str:
    """把 -60..0 dBFS 畫成長條。"""
    filled = (
        0
        if math.isinf(level_db)
        else round(METER_WIDTH * min(1.0, max(0.0, (level_db + 60) / 60)))
    )
    return "#" * filled + "." * (METER_WIDTH - filled)


def ask_int(prompt: str, low: int, high: int) -> int | None:
    """讀一個 [low, high] 的整數；輸入空白或 EOF 回傳 None。"""
    while True:
        try:
            text = input(prompt).strip()
        except EOFError:
            return None
        if not text:
            return None
        if text.isdigit() and low <= int(text) <= high:
            return int(text)
        print(f"  請輸入 {low}–{high} 的整數")


def show_devices() -> list[DeviceInfo]:
    try:
        devices = list_devices()
    except CaptureError as exc:
        print(f"  無法列出裝置：{type(exc).__name__}: {exc}")
        return []
    for i, d in enumerate(devices, 1):
        mark = "*" if d.is_default else " "
        print(f"  {i:2d}. [{d.kind:10s}]{mark} {d.name}")
        print(f"      id = {d.id}")
    print("  （* = 預設裝置）")
    return devices


def record(source: AudioSource, seconds: int, label: str) -> None:
    """錄 `seconds` 秒，期間每 0.5 秒顯示音量，結束後印統計並存 WAV。"""
    parts: list[np.ndarray] = []
    shapes_ok = True
    t0 = time.monotonic()
    source.start()
    print(f"  已開啟（{time.monotonic() - t0:.3f} s），錄音 {seconds} 秒…")
    try:
        window: list[np.ndarray] = []
        next_meter = time.monotonic() + METER_INTERVAL
        start = time.monotonic()
        while time.monotonic() - start < seconds:
            chunk = source.read(timeout=READ_TIMEOUT)
            if chunk.dtype != np.int16 or chunk.ndim != 1:
                shapes_ok = False
            parts.append(chunk)
            window.append(chunk)
            if time.monotonic() >= next_meter:
                level = dbfs(np.concatenate(window))
                print(
                    f"  {time.monotonic() - start:5.1f}s |{meter(level)}| {level:6.1f} dBFS"
                )
                window = []
                next_meter += METER_INTERVAL
    finally:
        source.stop()

    audio = np.concatenate(parts) if parts else np.zeros(0, dtype=np.int16)
    peak = int(np.abs(audio.astype(np.int32)).max()) if len(audio) else 0
    rms = (
        float(np.sqrt(np.mean(np.square(audio.astype(np.float64)))))
        if len(audio)
        else 0.0
    )
    print(f"  dtype={audio.dtype} ndim={audio.ndim} 每次 read 的格式正確={shapes_ok}")
    print(
        f"  樣本數={len(audio)}（{len(audio) / SAMPLE_RATE:.3f} s，要求 {seconds} s）"
    )
    print(f"  RMS={rms:.1f}（{dbfs(audio):.1f} dBFS） peak={peak}")
    for attr in ("device_sample_rate", "device_channels", "dropped_samples"):
        if hasattr(source, attr):
            print(f"  {attr}={getattr(source, attr)}")

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    path = DATA_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{label}.wav"
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        w.writeframes(audio.astype("<i2").tobytes())
    print(f"  已存檔：{path}")


def record_menu(device_id: str | None, kind: DeviceKind) -> None:
    seconds = (
        ask_int(f"  錄幾秒？（1–{MAX_SECONDS}，Enter = 5）：", 1, MAX_SECONDS) or 5
    )
    try:
        source = open_source(device_id, kind)
        record(source, seconds, kind)
    except CaptureError as exc:
        print(f"  擷取失敗：{type(exc).__name__}: {exc}")


def choose_device() -> None:
    devices = show_devices()
    if not devices:
        return
    index = ask_int(f"  選擇裝置（1–{len(devices)}，Enter 取消）：", 1, len(devices))
    if index is None:
        return
    device = devices[index - 1]
    print(f"  使用：{device.name}")
    record_menu(device.id, device.kind)


MENU = """
=== kikitori capture 檢查 ===
  1. 列出裝置
  2. 選擇裝置錄音
  3. 錄預設 loopback（系統聲音）
  4. 錄預設麥克風
  0. 離開
"""


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="  [%(levelname)s] %(name)s: %(message)s"
    )
    print(f"Python {sys.version.split()[0]}，平台 {sys.platform}")
    while True:
        print(MENU)
        choice = ask_int("選擇：", 0, 4)
        if choice is None or choice == 0:
            return
        if choice == 1:
            show_devices()
        elif choice == 2:
            choose_device()
        elif choice == 3:
            record_menu(None, "loopback")
        elif choice == 4:
            record_menu(None, "microphone")


if __name__ == "__main__":
    main()
