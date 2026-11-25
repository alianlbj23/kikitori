"""`BusyMeter`（完整辨識忙碌度）與 `RtfMeter`（即時倍率）的測試：注入假時鐘，不讀牆鐘。"""

import math
import unittest

from kikitori.pipeline.metrics import (
    BUSY_WINDOW_SECS,
    RTF_WINDOW,
    BusyMeter,
    RtfMeter,
)


class FakeClock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class BusyMeterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.meter = BusyMeter(clock=self.clock)

    def busy(self, secs: float) -> None:
        self.meter.begin()
        self.clock.now += secs
        self.meter.end()

    def test_none_before_any_final(self) -> None:
        self.assertIsNone(self.meter.ratio())

    def test_default_window_is_30_seconds(self) -> None:
        self.assertEqual(BUSY_WINDOW_SECS, 30.0)

    def test_ratio_over_the_time_since_the_first_final(self) -> None:
        # 還不滿 30 秒時以「第一次開始到現在」為分母，否則剛開始時會一直顯示很閒
        self.busy(10.0)
        self.clock.now += 10.0
        self.assertAlmostEqual(self.meter.ratio(), 0.5)

    def test_the_first_seconds_are_not_read_as_overloaded(self) -> None:
        # 分母至少 10 秒：第一句算 3 秒時讀數 0.3，不是 1.0（session 一開始就誤報「跟不上」）
        self.meter.begin()
        self.clock.now += 3.0
        self.assertAlmostEqual(self.meter.ratio(), 0.3)
        self.meter.end()
        self.clock.now += 1.0
        self.assertAlmostEqual(self.meter.ratio(), 0.3)

    def test_ratio_over_the_full_window(self) -> None:
        for _ in range(10):
            self.busy(1.5)
            self.clock.now += 1.5
        # 30 秒內一半時間在算
        self.assertAlmostEqual(self.meter.ratio(), 0.5)

    def test_old_work_leaves_the_window(self) -> None:
        self.busy(10.0)
        self.clock.now += 25.0  # 窗是最近 30 秒：10 秒的工作只剩 5 秒在窗內
        self.assertAlmostEqual(self.meter.ratio(), 5.0 / 30.0, places=3)
        self.clock.now += 60.0
        self.assertEqual(self.meter.ratio(), 0.0)

    def test_work_in_progress_counts(self) -> None:
        # 一句算很久時不能等它算完才顯示忙碌
        self.busy(1.0)
        self.clock.now += 1.0
        self.meter.begin()
        self.clock.now += 8.0
        self.assertAlmostEqual(self.meter.ratio(), 0.9)

    def test_always_busy_is_one(self) -> None:
        self.meter.begin()
        self.clock.now += 100.0
        self.assertEqual(self.meter.ratio(), 1.0)
        self.meter.end()
        self.assertEqual(self.meter.ratio(), 1.0)

    def test_end_returns_the_elapsed_seconds(self) -> None:
        self.meter.begin()
        self.clock.now += 0.75
        self.assertAlmostEqual(self.meter.end(), 0.75)

    def test_zero_elapsed_time(self) -> None:
        self.meter.begin()
        self.assertEqual(self.meter.end(), 0.0)
        self.assertEqual(self.meter.ratio(), 0.0)

    def test_clock_going_backwards_is_not_negative(self) -> None:
        self.meter.begin()
        self.clock.now -= 5.0
        self.assertEqual(self.meter.end(), 0.0)
        ratio = self.meter.ratio()
        assert ratio is not None
        self.assertTrue(0.0 <= ratio <= 1.0)

    def test_begin_twice_or_end_without_begin_is_an_error(self) -> None:
        with self.assertRaises(RuntimeError):
            self.meter.end()
        self.meter.begin()
        with self.assertRaises(RuntimeError):
            self.meter.begin()

    def test_invalid_window(self) -> None:
        for window in (0, -1.0, math.inf, math.nan, True):
            with self.subTest(window=window), self.assertRaises(ValueError):
                BusyMeter(window, clock=self.clock)  # type: ignore[arg-type]


class RtfMeterTest(unittest.TestCase):
    def test_none_without_data(self) -> None:
        self.assertIsNone(RtfMeter().median())

    def test_default_size_is_10(self) -> None:
        self.assertEqual(RTF_WINDOW, 10)

    def test_median_of_compute_over_audio_seconds(self) -> None:
        meter = RtfMeter()
        meter.add(1.0, 4.0)  # 0.25
        meter.add(3.0, 2.0)  # 1.5
        meter.add(0.5, 1.0)  # 0.5
        self.assertAlmostEqual(meter.median(), 0.5)

    def test_only_the_last_10(self) -> None:
        meter = RtfMeter()
        for _ in range(10):
            meter.add(5.0, 1.0)
        for _ in range(10):
            meter.add(0.1, 1.0)
        self.assertAlmostEqual(meter.median(), 0.1)

    def test_empty_audio_is_skipped(self) -> None:
        # 沒有音訊長度就算不出倍率；不能除以 0
        meter = RtfMeter()
        meter.add(1.0, 0.0)
        self.assertIsNone(meter.median())

    def test_invalid_values(self) -> None:
        meter = RtfMeter()
        for compute, audio in ((-1.0, 1.0), (math.nan, 1.0), (1.0, math.inf)):
            with (
                self.subTest(compute=compute, audio=audio),
                self.assertRaises(ValueError),
            ):
                meter.add(compute, audio)
        self.assertIsNone(meter.median())

    def test_invalid_size(self) -> None:
        for size in (0, -3, True, 1.5):
            with self.subTest(size=size), self.assertRaises(ValueError):
                RtfMeter(size)  # type: ignore[arg-type]


if __name__ == "__main__":
    unittest.main()
