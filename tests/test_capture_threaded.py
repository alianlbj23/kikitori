"""`ThreadedSource`／`ChunkBuffer`：存起來的擷取錯誤不能讓呼叫端的 frame 一直活著。

memory_soak #1：開裝置失敗時 `start()` 重新丟出存在 source 裡的同一個例外物件，Python 把
呼叫端的 frame（`Pipeline.start`，self = pipeline → GPU 上的模型）接到它的 traceback；source
又被 traceback 參照，只能等循環垃圾回收才釋放。
"""

import gc
import unittest
import weakref
from typing import Any

import numpy as np

from kikitori.capture.buffer import ChunkBuffer
from kikitori.capture.errors import (
    CaptureError,
    DeviceLostError,
    DeviceNotFoundError,
    detached_copy,
)
from kikitori.capture.threaded import ThreadedSource


class _Big:
    """代表呼叫端 frame 裡的大物件（例如 pipeline 的 ASR 模型）。"""


def _missing_device() -> Any:
    raise DeviceNotFoundError("loopback device not found: 999999")


def _start_in_frame(source: ThreadedSource) -> "weakref.ref[_Big]":
    big = _Big()
    ref = weakref.ref(big)
    try:
        source.start()
    except DeviceNotFoundError:
        pass
    return ref


class ThreadedSourceOpenErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)

    def test_open_error_keeps_type_and_message(self) -> None:
        source = ThreadedSource(_missing_device)
        with (
            self.assertLogs("kikitori.capture.threaded", "ERROR"),
            self.assertRaisesRegex(DeviceNotFoundError, "999999"),
        ):
            source.start()

    def test_caller_frame_is_released_while_the_source_is_alive(self) -> None:
        source = ThreadedSource(_missing_device)
        with self.assertLogs("kikitori.capture.threaded", "ERROR"):
            ref = _start_in_frame(source)
        # source 還活著（呼叫端持有）；它保存的錯誤不能經由 traceback 參照呼叫端的 frame
        self.assertIsNone(ref())
        source.stop()

    def test_source_is_released_by_refcount(self) -> None:
        source = ThreadedSource(_missing_device)
        source_ref = weakref.ref(source)
        with self.assertLogs("kikitori.capture.threaded", "ERROR"):
            _start_in_frame(source)
        source.stop()
        del source
        self.assertIsNone(source_ref())


class ChunkBufferErrorTest(unittest.TestCase):
    def setUp(self) -> None:
        gc.collect()
        gc.disable()
        self.addCleanup(gc.enable)

    def test_each_get_raises_a_fresh_error(self) -> None:
        buffer = ChunkBuffer(16000)
        buffer.fail(DeviceLostError("usb unplugged"))
        raised = []
        for _ in range(2):
            with self.assertRaisesRegex(DeviceLostError, "usb unplugged") as ctx:
                buffer.get(0)
            raised.append(ctx.exception)
        self.assertIsNot(raised[0], raised[1])

    def test_reader_frame_is_released(self) -> None:
        buffer = ChunkBuffer(16000)
        buffer.fail(DeviceLostError("usb unplugged"))

        def read() -> "weakref.ref[_Big]":
            big = _Big()
            ref = weakref.ref(big)
            try:
                buffer.get(0)
            except CaptureError:
                pass
            return ref

        self.assertIsNone(read()())
        # 錯誤之前收到的資料仍先交給讀取端
        buffer2 = ChunkBuffer(16000)
        buffer2.put(np.ones(10, dtype=np.int16))
        buffer2.fail(DeviceLostError("x"))
        self.assertEqual(len(buffer2.get(0)), 10)


class DetachedCopyTest(unittest.TestCase):
    def test_copy_keeps_class_message_and_cause(self) -> None:
        cause = OSError("driver")
        try:
            raise DeviceLostError("gone") from cause
        except DeviceLostError as exc:
            original = exc
        copy = detached_copy(original)
        self.assertIsNot(copy, original)
        self.assertIs(type(copy), DeviceLostError)
        self.assertEqual(str(copy), "gone")
        self.assertIs(copy.__cause__, cause)
        self.assertIsNone(copy.__traceback__)

    def test_unusual_constructor_falls_back_to_the_base_class(self) -> None:
        class Odd(CaptureError):
            def __init__(self, a: str, b: str) -> None:
                super().__init__(f"{a}/{b}")

        copy = detached_copy(Odd("x", "y"))
        self.assertIsInstance(copy, CaptureError)
        self.assertEqual(str(copy), "x/y")


if __name__ == "__main__":
    unittest.main()
