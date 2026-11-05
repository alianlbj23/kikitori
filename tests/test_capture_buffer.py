"""ChunkBuffer 的測試：逾時、丟棄最舊資料、錯誤傳遞與關閉後的行為。"""

import threading
import time
import unittest

import numpy as np

from kikitori.capture.buffer import ChunkBuffer
from kikitori.capture.errors import CaptureError, DeviceLostError


def chunk(*values: int) -> np.ndarray:
    return np.array(values, dtype=np.int16)


class ChunkBufferTest(unittest.TestCase):
    def test_timeout_returns_empty_int16(self) -> None:
        buf = ChunkBuffer(max_samples=100)
        start = time.monotonic()
        out = buf.get(timeout=0.05)
        self.assertGreaterEqual(time.monotonic() - start, 0.04)
        self.assertEqual(out.shape, (0,))
        self.assertEqual(out.dtype, np.int16)

    def test_get_returns_everything_queued_in_order(self) -> None:
        buf = ChunkBuffer(max_samples=100)
        buf.put(chunk(1, 2))
        buf.put(chunk(3))
        np.testing.assert_array_equal(buf.get(timeout=0), [1, 2, 3])
        self.assertEqual(buf.get(timeout=0).shape, (0,))

    def test_get_wakes_up_when_data_arrives(self) -> None:
        buf = ChunkBuffer(max_samples=100)
        timer = threading.Timer(0.05, buf.put, args=(chunk(7),))
        timer.start()
        try:
            out = buf.get(timeout=5.0)
        finally:
            timer.cancel()
        np.testing.assert_array_equal(out, [7])

    def test_overflow_drops_oldest_and_counts(self) -> None:
        buf = ChunkBuffer(max_samples=4)
        buf.put(chunk(1, 2))
        buf.put(chunk(3, 4))
        buf.put(chunk(5, 6))
        self.assertEqual(buf.dropped_samples, 2)
        np.testing.assert_array_equal(buf.get(timeout=0), [3, 4, 5, 6])

    def test_single_oversized_chunk_keeps_newest_samples(self) -> None:
        buf = ChunkBuffer(max_samples=3)
        buf.put(chunk(1, 2, 3, 4, 5))
        self.assertEqual(buf.dropped_samples, 2)
        np.testing.assert_array_equal(buf.get(timeout=0), [3, 4, 5])

    def test_partial_drop_of_oldest_chunk(self) -> None:
        buf = ChunkBuffer(max_samples=4)
        buf.put(chunk(1, 2, 3))
        buf.put(chunk(4, 5))
        self.assertEqual(buf.dropped_samples, 1)
        np.testing.assert_array_equal(buf.get(timeout=0), [2, 3, 4, 5])

    def test_empty_chunks_are_ignored(self) -> None:
        buf = ChunkBuffer(max_samples=4)
        buf.put(chunk())
        self.assertEqual(buf.queued_samples, 0)

    def test_put_rejects_wrong_format(self) -> None:
        buf = ChunkBuffer(max_samples=4)
        with self.assertRaises(ValueError):
            buf.put(np.zeros(3, dtype=np.float32))
        with self.assertRaises(ValueError):
            buf.put(np.zeros((2, 2), dtype=np.int16))

    def test_failure_is_raised_after_remaining_data(self) -> None:
        buf = ChunkBuffer(max_samples=10)
        buf.put(chunk(1))
        buf.fail(DeviceLostError("gone"))
        np.testing.assert_array_equal(buf.get(timeout=1.0), [1])
        for _ in range(2):
            with self.assertRaises(DeviceLostError):
                buf.get(timeout=1.0)

    def test_non_capture_failure_is_wrapped(self) -> None:
        buf = ChunkBuffer(max_samples=10)
        cause = OSError("boom")
        buf.fail(cause)
        with self.assertRaises(CaptureError) as ctx:
            buf.get(timeout=0)
        self.assertIs(ctx.exception.__cause__, cause)

    def test_failure_wakes_waiting_reader(self) -> None:
        buf = ChunkBuffer(max_samples=10)
        timer = threading.Timer(0.05, buf.fail, args=(DeviceLostError("gone"),))
        timer.start()
        start = time.monotonic()
        try:
            with self.assertRaises(DeviceLostError):
                buf.get(timeout=5.0)
        finally:
            timer.cancel()
        self.assertLess(time.monotonic() - start, 2.0)

    def test_closed_buffer_returns_remaining_then_empty_immediately(self) -> None:
        buf = ChunkBuffer(max_samples=10)
        buf.put(chunk(1))
        buf.close()
        buf.put(chunk(2))  # 關閉後送進來的資料不收
        np.testing.assert_array_equal(buf.get(timeout=1.0), [1])
        start = time.monotonic()
        self.assertEqual(buf.get(timeout=5.0).shape, (0,))
        self.assertLess(time.monotonic() - start, 1.0)

    def test_invalid_arguments(self) -> None:
        for bad in (0, -1):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                ChunkBuffer(max_samples=bad)
        buf = ChunkBuffer(max_samples=4)
        for bad_timeout in (-0.1, float("nan"), float("inf")):
            with self.subTest(timeout=bad_timeout), self.assertRaises(ValueError):
                buf.get(timeout=bad_timeout)


if __name__ == "__main__":
    unittest.main()
