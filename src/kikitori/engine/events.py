"""引擎事件的組裝：`subtitle` 事件的 data 與定期送出的 `metrics`。

位於 engine 層，給 stdio 引擎（`server.py`）與遠端運算的運算主機（`kikitori.remote.server`）共用，
兩邊送出的事件欄位因此完全相同；A 端收到遠端的事件後照常交給介面。`metrics` 的負荷欄位
（`final_busy`、`rtf`、`gpu`）見 docs/specs/compute-capacity.md：GPU 使用量只在辨識用 CUDA 時
由 metrics 執行緒定期取樣（`GpuSampler`）。
"""

import dataclasses
import logging
import math
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from kikitori.engine.io import ChannelClosedError
from kikitori.hardware.nvidia import (
    GpuUsage,
    Runner,
    find_nvidia_smi,
    sample_gpu_usage,
)
from kikitori.hardware.system import os_name
from kikitori.pipeline import Pipeline, PipelineComponents, SubtitleUpdate
from kikitori.pipeline.metrics import PipelineMetrics

logger = logging.getLogger(__name__)

METRICS_JOIN_TIMEOUT = 1.0
GPU_SAMPLE_INTERVAL = 5.0
"""GPU 取樣的間隔（秒）：metrics 每秒一次，但 nvidia-smi 每次要開一個行程（約百毫秒），
VRAM 也不會每秒大幅變動。"""
CUDA_GPU_INDEX = 0
"""取樣哪一張 GPU：ASR 模型一律載入在 CUDA 裝置 0（`asr.whisper` 的 `device_index=0`）。
nvidia-smi 的順序是 PCI bus 順序，多張卡時不一定相同；只有一張卡（主要情境）時一定相同。"""
MAX_EVENT_RTF = 1000.0
"""`rtf` 送出前的上限：A 端驗證 0–1000，超出時整則 metrics 會被丟掉。"""
MAX_EVENT_GPU_NAME = 64
"""`gpu.name` 送出前截短到這個長度：A 端驗證 ≤ 64 字。"""

GpuUsageSource = Callable[[], GpuUsage | None]
"""回傳目前 GPU 使用量（取不到時 None）；通常是 `GpuSampler.get`。"""


def subtitle_event(update: SubtitleUpdate, session_id: int) -> dict[str, Any]:
    """`subtitle` 事件的 data：`SubtitleUpdate` 的欄位 + `session_id`。

    即時字幕帶 `stable_chars` 與 `translation_stable_chars`（沒有 `latency_ms`）；完整字幕帶
    `latency_ms`（可能是 null，沒有兩個穩定前綴）。只放有意義的那些，UI 不必猜 null 是「沒有」
    還是「0」。
    """
    data = dataclasses.asdict(update)
    if update.speaker is None:
        data.pop("speaker")  # 影片翻譯：沒有「誰說的」
    if update.voice is None:
        data.pop("voice")  # 沒開說話者標籤、即時字幕或判斷不出來
    if update.is_final:
        data.pop("stable_chars")
        data.pop("translation_stable_chars")
    else:
        data.pop("latency_ms")
        for key in ("stable_chars", "translation_stable_chars"):
            if data[key] is None:
                data[key] = 0
    data["session_id"] = session_id
    return data


def metrics_event(
    metrics: PipelineMetrics, session_id: int, gpu: GpuUsage | None
) -> dict[str, Any]:
    """`metrics` 事件的 data：pipeline 的量測 + GPU 使用量（沒有時為 null）。

    負荷欄位（`final_busy`、`rtf`、`gpu`）在這裡先收斂到 A 端驗證的範圍：運算主機送出的
    metrics 會原樣經協定轉給 A，超出範圍時 A 會丟掉整則（連音量也看不到）。
    """
    busy = metrics.final_busy
    rtf = metrics.rtf
    return {
        "session_id": session_id,
        "level_dbfs": metrics.level_dbfs,
        "backlog": metrics.backlog,
        "latency_ms_median": metrics.latency_ms_median,
        # v0.3 G8：句子進行中／之外的音量（沒有那一類時為 null）
        "speech_level_dbfs": metrics.speech_level_dbfs,
        "background_level_dbfs": metrics.background_level_dbfs,
        "final_busy": None if busy is None else round(min(max(busy, 0.0), 1.0), 3),
        "rtf": None if rtf is None else round(min(max(rtf, 0.0), MAX_EVENT_RTF), 3),
        "gpu": None
        if gpu is None
        else {
            "name": gpu.name[:MAX_EVENT_GPU_NAME],
            "vram_used_mb": gpu.vram_used_mb,
            "vram_total_mb": gpu.vram_total_mb,
            "util_pct": gpu.util_pct,
        },
    }


class GpuSampler:
    """最多每 `interval` 秒取樣一次 GPU 使用量，其餘時候回傳上一次的結果（含失敗的 None）。

    只由 metrics 執行緒呼叫，不需要鎖。取樣函式丟出例外（程式錯誤）時回傳 None：第一次記
    warning，之後只記 debug，避免每 5 秒一則 warning 淹沒 log；session 不受影響。
    """

    def __init__(
        self,
        sample: GpuUsageSource,
        *,
        interval: float = GPU_SAMPLE_INTERVAL,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """
        Args:
            sample: 實際取樣（例如綁好參數的 `sample_gpu_usage`）；可能要上百毫秒。
            interval: 兩次取樣的最短間隔（秒）。
            clock: 單調時鐘（秒）；測試注入假時鐘。

        Raises:
            ValueError: `interval` 不是有限的正數。
        """
        if (
            isinstance(interval, bool)
            or not isinstance(interval, int | float)
            or not math.isfinite(interval)
            or interval <= 0
        ):
            raise ValueError(f"interval must be a positive number, got {interval!r}")
        self._sample = sample
        self._interval = float(interval)
        self._clock = clock
        self._last_at: float | None = None
        self._last: GpuUsage | None = None
        self._warned = False

    def get(self) -> GpuUsage | None:
        """目前的 GPU 使用量；距離上次取樣不到 `interval` 秒時回傳快取。"""
        now = self._clock()
        if self._last_at is not None and now - self._last_at < self._interval:
            return self._last
        # 先記時間再取樣：取樣失敗或丟例外時，也要等一個間隔才再試
        self._last_at = now
        try:
            self._last = self._sample()
        except Exception:
            self._last = None
            if self._warned:
                logger.debug("GPU usage sampling failed again", exc_info=True)
            else:
                self._warned = True
                logger.warning("GPU usage sampling failed", exc_info=True)
        return self._last


def gpu_sampler_for(
    components: PipelineComponents,
    *,
    find: Callable[[], Path | None] | None = None,
    run: Runner = subprocess.run,
) -> GpuSampler | None:
    """這個 session 的 GPU 取樣器；辨識沒有用 CUDA，或找不到 `nvidia-smi` 時 None。

    裝置看 transcriber 的 `device`（實際載入的裝置；`asr.device == "auto"` 時也已經決定），
    任一條路徑在 CUDA 上就取樣。用 CPU 時不取樣：顯示卡的使用量與辨識無關。

    Args:
        components: session 的 pipeline 元件。
        find: 找 `nvidia-smi`（測試用）；省略時依目前平台 `find_nvidia_smi()`。
        run: 執行 `nvidia-smi` 的函式（測試用）。
    """
    devices = {
        getattr(transcriber, "device", None)
        for transcriber in (components.fast_transcriber, components.final_transcriber)
    }
    if "cuda" not in devices:
        return None
    try:
        executable = find_nvidia_smi(os_name(sys.platform)) if find is None else find()
    except OSError as exc:
        logger.warning("cannot look for nvidia-smi; no GPU usage in metrics: %s", exc)
        return None
    if executable is None:
        logger.info("nvidia-smi not found; no GPU usage in metrics")
        return None
    return GpuSampler(
        lambda: sample_gpu_usage(CUDA_GPU_INDEX, executable=executable, run=run)
    )


class MetricsReporter:
    """session 執行中每隔 `interval` 秒送一則 `metrics` 事件（一條 daemon 執行緒）。

    `stop()` 返回之後不會再送出（送出與停止共用一把鎖），所以 `status idle／error`
    之後不會再看到這個 session 的 metrics。例外：送出卡住（例如 stdout 管線塞滿）超過
    `METRICS_JOIN_TIMEOUT` 時 `stop()` 記 warning 後照樣返回——結束狀態比保證順序重要，
    那則卡住的 metrics 之後才寫出的話會落在結束狀態之後。pipeline 已結束時自己停止，即使
    有人忘了呼叫 `stop()` 也不會一直留著執行緒。
    """

    def __init__(
        self,
        session_id: int,
        pipeline: Pipeline,
        send: Callable[[str, Mapping[str, Any]], None],
        interval: float,
        *,
        gpu_usage: GpuUsageSource | None = None,
    ) -> None:
        """
        Args:
            session_id: 事件帶的 session 編號。
            pipeline: 量測來源。
            send: 送出事件（名稱, data）。
            interval: 間隔（秒）。
            gpu_usage: GPU 使用量（通常是 `gpu_sampler_for(...).get`）；None 表示 `gpu`
                一律為 null（用 CPU 或找不到 nvidia-smi）。
        """
        self._session_id = session_id
        self._pipeline = pipeline
        self._send = send
        self._interval = interval
        self._gpu_usage = gpu_usage
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(
            target=self._run, name=f"session-{session_id}-metrics", daemon=True
        )

    def start(self) -> None:
        """開始回報（`stop()` 之後呼叫也只會讓執行緒立即結束）。"""
        self._thread.start()

    def stop(self) -> None:
        """停止並等執行緒結束（合計最多約 `METRICS_JOIN_TIMEOUT` 秒；可重複呼叫）。"""
        self._stop.set()
        deadline = time.monotonic() + METRICS_JOIN_TIMEOUT
        # 等進行中的送出完成：之後不會再有 metrics。不能無限等：送出可能卡在寫 stdout，
        # 那時結束狀態也送不出去，整個 session 的結束流程跟著卡住。
        if self._lock.acquire(timeout=METRICS_JOIN_TIMEOUT):
            self._lock.release()
        else:
            logger.warning(
                "%s is stuck sending; stopping without waiting for it",
                self._thread.name,
            )
        if (
            self._thread.ident is not None
            and self._thread is not threading.current_thread()
        ):
            self._thread.join(max(0.0, deadline - time.monotonic()))
            if self._thread.is_alive():
                logger.warning("%s did not stop in time", self._thread.name)

    def _gpu(self) -> GpuUsage | None:
        if self._gpu_usage is None:
            return None
        try:
            return self._gpu_usage()
        except Exception:
            # GpuSampler 自己會接住取樣錯誤；到這裡的是程式錯誤：之後不再取樣，metrics 照送
            logger.warning("GPU usage source failed; disabling it", exc_info=True)
            self._gpu_usage = None
            return None

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            # 在鎖外取樣：nvidia-smi 最多要 2 秒，不能讓 stop() 等它（stop 只等 1 秒）。
            # 載入中、已結束時不送 metrics，也就不必取樣。
            gpu = self._gpu() if self._pipeline.state == "running" else None
            with self._lock:
                if self._stop.is_set() or self._pipeline.wait(0):
                    return
                if self._pipeline.state != "running":
                    continue
                try:
                    data = metrics_event(
                        self._pipeline.metrics(), self._session_id, gpu
                    )
                    self._send("metrics", data)
                except ChannelClosedError:
                    logger.error("metrics: protocol output closed")
                    return
                except Exception:
                    # 量測只是輔助顯示：程式錯誤記錄後停止回報，不影響 session
                    logger.exception("metrics reporting failed; stopping it")
                    return
