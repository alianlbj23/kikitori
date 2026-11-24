"""遠端運算 B 端的聲音來源：網路收到的音訊交給現有的 Pipeline 讀（docs/specs/remote-compute.md）。"""

import threading
import time
import unittest

import numpy as np

from kikitori.remote.source import PushSource, PushSourceOverflow


def tone(n: int, value: int = 1000) -> np.ndarray:
    return np.full(n, value, dtype=np.int16)


class PushSourceTest(unittest.TestCase):
    def test_reads_what_was_pushed_in_order(self) -> None:
        s = PushSource(max_samples=10_000)
        s.start()
        s.push(tone(100, 1))
        s.push(tone(50, 2))
        chunk = s.read(0.5)
        self.assertEqual(chunk.dtype, np.int16)
        self.assertEqual(len(chunk), 150)
        self.assertTrue(np.all(chunk[:100] == 1) and np.all(chunk[100:] == 2))
        self.assertEqual(len(s.read(0.01)), 0)  # 沒有新音訊：逾時回空陣列
        self.assertEqual(s.received_samples, 150)

    def test_read_waits_for_audio_from_another_thread(self) -> None:
        s = PushSource(max_samples=10_000)
        s.start()
        threading.Timer(0.05, lambda: s.push(tone(10))).start()
        started = time.monotonic()
        self.assertEqual(len(s.read(2.0)), 10)
        self.assertLess(time.monotonic() - started, 1.5)

    def test_close_drains_then_reports_exhausted(self) -> None:
        # Pipeline 讀到空陣列且 exhausted 為 True 時收尾最後一句並結束
        s = PushSource(max_samples=10_000)
        s.start()
        s.push(tone(30))
        s.close()
        self.assertFalse(s.exhausted)
        self.assertEqual(len(s.read(0.1)), 30)
        self.assertEqual(len(s.read(0.1)), 0)
        self.assertTrue(s.exhausted)
        with self.assertRaises(RuntimeError):
            s.push(tone(1))

    def test_stop_wakes_a_waiting_reader(self) -> None:
        s = PushSource(max_samples=10_000)
        s.start()
        threading.Timer(0.05, s.stop).start()
        started = time.monotonic()
        self.assertEqual(len(s.read(5.0)), 0)
        self.assertLess(time.monotonic() - started, 2.0)
        self.assertTrue(s.exhausted)
        s.stop()  # 可重複呼叫

    def test_overflow_is_an_error_not_silent_loss(self) -> None:
        # 丟掉音訊會讓時間軸錯位：讀的一方卡住時讓 session 失敗，而不是悄悄丟資料
        s = PushSource(max_samples=100)
        s.start()
        s.push(tone(80))
        with self.assertRaises(PushSourceOverflow):
            s.push(tone(30))
        self.assertEqual(len(s.read(0.1)), 80)

    def test_rejects_bad_audio(self) -> None:
        s = PushSource(max_samples=100)
        s.start()
        for bad in (np.zeros(10, dtype=np.float32), np.zeros((2, 5), dtype=np.int16)):
            with (
                self.subTest(dtype=bad.dtype, shape=bad.shape),
                self.assertRaises(ValueError),
            ):
                s.push(bad)
        with self.assertRaises(ValueError):
            PushSource(max_samples=0)
        s.push(np.zeros(0, dtype=np.int16))  # 空的不算錯
        self.assertEqual(s.received_samples, 0)


if __name__ == "__main__":
    unittest.main()
