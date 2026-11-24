"""引擎事件的組裝：`subtitle` 事件的 data 與定期送出的 `metrics`。

位於 engine 層，給 stdio 引擎（`server.py`）與遠端運算的運算主機（`kikitori.remote.server`）共用，
兩邊送出的事件欄位因此完全相同；A 端收到遠端的事件後照常交給介面。
"""

import dataclasses
import logging
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from kikitori.engine.io import ChannelClosedError
from kikitori.pipeline import Pipeline, SubtitleUpdate

logger = logging.getLogger(__name__)

METRICS_JOIN_TIMEOUT = 1.0


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
    ) -> None:
        self._session_id = session_id
        self._pipeline = pipeline
        self._send = send
        self._interval = interval
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

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            with self._lock:
                if self._stop.is_set() or self._pipeline.wait(0):
                    return
                if self._pipeline.state != "running":
                    continue
                metrics = self._pipeline.metrics()
                try:
                    self._send(
                        "metrics",
                        {
                            "session_id": self._session_id,
                            "level_dbfs": metrics.level_dbfs,
                            "backlog": metrics.backlog,
                            "latency_ms_median": metrics.latency_ms_median,
                            # v0.3 G8：句子進行中／之外的音量（沒有那一類時為 null）
                            "speech_level_dbfs": metrics.speech_level_dbfs,
                            "background_level_dbfs": metrics.background_level_dbfs,
                        },
                    )
                except ChannelClosedError:
                    logger.error("metrics: protocol output closed")
                    return
                except Exception:
                    # 量測只是輔助顯示：程式錯誤記錄後停止回報，不影響 session
                    logger.exception("metrics reporting failed; stopping it")
                    return
