"""重現：開裝置失敗的 session 留下參照循環，模型要等循環垃圾回收才釋放（情境 6 的 missing_device）。

暫時性實驗，不屬於正式程式碼。只用 CPU，不載入真的模型：以一個「大物件」代表 GPU 上的
transcriber，用 weakref 觀察它什麼時候被釋放。照 `engine/server.py` `_load_session()` 的寫法：
建立 `Pipeline` → `pipeline.start(source)` 丟 `CaptureError` → 記錄後離開函式，所有區域變數消失。

- `ThreadedSource` 的擷取執行緒開裝置失敗時把例外存在 `_open_error`（也交給 `_buffer.fail()`），
  `start()` 再 `raise self._open_error`（`capture/threaded.py:126`）。重新丟出同一個例外物件時，
  Python 會把目前的 frame（`ThreadedSource.start`、`Pipeline.start`、呼叫端）接到它的 traceback 上。
- 於是 source → 例外 → traceback → `Pipeline.start` 的 frame（`self` = pipeline）→ pipeline → source
  成為循環；只有循環垃圾回收（gc 第 2 代，引擎裡很少執行）才會釋放 pipeline 與它的元件。

執行：uv run python -X utf8 experiments/memory_soak/repro_open_error_cycle.py
"""

from __future__ import annotations

import gc
import logging
import sys
import weakref
from typing import Any
from unittest import mock

from kikitori.capture.errors import CaptureError, DeviceNotFoundError
from kikitori.capture.threaded import ThreadedSource
from kikitori.pipeline.pipeline import Pipeline
from kikitori.pipeline.types import PipelineComponents, PipelineConfig


class BigModel:
    """代表 GPU 上的 ASR 模型（只看它有沒有被釋放）。"""


def failing_opener() -> Any:
    raise DeviceNotFoundError("loopback device not found: 999999")


def load_session(clear_traceback: bool) -> weakref.ref[BigModel]:
    """照 `EngineServer._load_session()` 的步驟；回傳模型的 weakref。"""
    model = BigModel()
    components = PipelineComponents(
        voice_model=mock.Mock(),
        fast_transcriber=mock.Mock(model=model),
        final_transcriber=mock.Mock(),
        fast_translator=mock.Mock(),
        final_translator=mock.Mock(),
        transcript_filter=lambda t: None,
    )
    ref = weakref.ref(model)
    del model
    config = PipelineConfig(
        mode="media",
        pause_secs=0.6,
        max_secs=10.0,
        target_language="zh-TW",
        partner_language="ja",
        source_language="ja",
    )
    pipeline = Pipeline(components, config, on_subtitle=None, on_status=None)
    source = ThreadedSource(failing_opener)
    try:
        pipeline.start(source)
    except CaptureError as exc:
        logging.getLogger("repro").info("cannot start audio capture: %s", exc)
        if clear_traceback:
            # 候選修正：與 Pipeline._fail() 相同，丟掉 traceback 才不會接上呼叫端的 frame
            exc.with_traceback(None)
    return ref


def main() -> int:
    logging.basicConfig(level=logging.CRITICAL)
    gc.disable()  # 讓結果不受「剛好跑了一次 gc」影響；引擎裡第 2 代回收也很少發生
    results = {}
    for clear in (False, True):
        ref = load_session(clear)
        alive_before_gc = ref() is not None
        gc.collect()
        alive_after_gc = ref() is not None
        results[clear] = (alive_before_gc, alive_after_gc)
        label = "with exc.with_traceback(None)" if clear else "as in server.py today  "
        print(
            f"{label}: model alive after the session failed = {alive_before_gc}; "
            f"after gc.collect() = {alive_after_gc}"
        )
    leaked = results[False] == (True, False)
    fixed = results[True] == (False, False)
    print(f"cycle reproduced: {leaked}; candidate fix frees it by refcount: {fixed}")
    return 0 if leaked else 1


if __name__ == "__main__":
    sys.exit(main())
