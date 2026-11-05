"""`kikitori.pipeline.keep_warm` 的測試：閒置多久才送保溫請求、失敗只記 log、執行緒的開始與停止。

時間一律用假時鐘；背景執行緒的等待也注入成「推進假時鐘」，測試不真的睡。
"""

import threading
import unittest
from collections.abc import Iterator

from kikitori.pipeline.keep_warm import ActivityTranslator, KeepWarm
from kikitori.translate import TranslationRequest, TranslationTimeoutError

WAIT = 5.0
INTERVAL = 240.0


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self._lock = threading.Lock()

    def __call__(self) -> float:
        with self._lock:
            return self.now

    def advance(self, secs: float) -> None:
        with self._lock:
            self.now += secs


class Pinger:
    """記錄每次保溫請求；`errors` 依序丟出（None 表示成功）。"""

    def __init__(self, clock: FakeClock, errors: list[Exception | None] | None = None):
        self.clock = clock
        self.at: list[float] = []
        self.errors = list(errors or [])

    def __call__(self) -> None:
        self.at.append(self.clock())
        if self.errors:
            error = self.errors.pop(0)
            if error is not None:
                raise error


class TickTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.ping = Pinger(self.clock)
        self.keep = KeepWarm(self.ping, INTERVAL, clock=self.clock)

    def test_waits_for_the_full_idle_interval(self) -> None:
        self.assertEqual(self.keep.tick(), INTERVAL)
        self.clock.advance(INTERVAL - 1)
        self.assertEqual(self.keep.tick(), 1.0)
        self.assertEqual(self.ping.at, [])

    def test_pings_after_idle_interval_then_every_interval(self) -> None:
        self.clock.advance(INTERVAL)
        self.assertEqual(self.keep.tick(), INTERVAL)
        self.assertEqual(self.ping.at, [1000.0 + INTERVAL])
        # 保溫請求本身也算活動：下一次在一個間隔之後。
        self.clock.advance(INTERVAL - 10)
        self.assertEqual(self.keep.tick(), 10.0)
        self.clock.advance(10)
        self.keep.tick()
        self.assertEqual(len(self.ping.at), 2)

    def test_translation_activity_postpones_the_ping(self) -> None:
        self.clock.advance(200)
        self.keep.touch()
        self.clock.advance(40)
        self.assertEqual(self.keep.tick(), 200.0)
        self.assertEqual(self.ping.at, [])
        self.clock.advance(200)
        self.keep.tick()
        self.assertEqual(self.ping.at, [1000.0 + 440])

    def test_no_ping_after_stop(self) -> None:
        self.keep.stop()
        self.clock.advance(INTERVAL * 3)
        self.keep.tick()
        self.assertEqual(self.ping.at, [])

    def test_invalid_interval(self) -> None:
        for bad in (0, -1.0, float("nan"), float("inf")):
            with self.assertRaises(ValueError):
                KeepWarm(self.ping, bad, clock=self.clock)


class FailureLoggingTest(unittest.TestCase):
    """伺服器沒開時每 4 分鐘都會失敗：warning 限流，不中斷、不往外拋。"""

    def test_failures_are_rate_limited_warnings_and_recovery_is_logged(self) -> None:
        clock = FakeClock()
        timeout = TranslationTimeoutError("qwen3.5:4b: no response within 60.0s")
        ping = Pinger(clock, [timeout, timeout, timeout, None])
        keep = KeepWarm(ping, INTERVAL, clock=clock, warn_interval=600.0)
        name = "kikitori.pipeline.keep_warm"
        with self.assertLogs(name, "DEBUG") as logs:
            for _ in range(4):
                clock.advance(INTERVAL)
                self.assertEqual(keep.tick(), INTERVAL)
        warnings = [r for r in logs.records if r.levelname == "WARNING"]
        # 第 1 次（t=240）warning；第 2 次（t=480）在 600 秒內只記 debug；第 3 次（t=720）
        # 距上次 warning 480 秒，仍是 debug。
        self.assertEqual(len(warnings), 1)
        self.assertIn("no response", warnings[0].getMessage())
        debug_failures = [
            r
            for r in logs.records
            if r.levelname == "DEBUG" and "no response" in r.getMessage()
        ]
        self.assertEqual(len(debug_failures), 2)
        self.assertTrue(
            any(
                r.levelname == "INFO" and "succeeded again" in r.getMessage()
                for r in logs.records
            )
        )
        self.assertEqual(len(ping.at), 4)

    def test_warning_repeats_after_the_rate_limit_window(self) -> None:
        clock = FakeClock()
        errors: list[Exception | None] = [TranslationTimeoutError("down")] * 4
        keep = KeepWarm(
            Pinger(clock, errors), INTERVAL, clock=clock, warn_interval=600.0
        )
        with self.assertLogs("kikitori.pipeline.keep_warm", "WARNING") as logs:
            for _ in range(4):  # t = 240, 480, 720, 960
                clock.advance(INTERVAL)
                keep.tick()
        self.assertEqual(len(logs.records), 2)  # 240 與 960（距 240 已 720 秒）

    def test_each_ping_is_logged_at_info(self) -> None:
        clock = FakeClock()
        keep = KeepWarm(Pinger(clock), INTERVAL, clock=clock)
        clock.advance(INTERVAL)
        with self.assertLogs("kikitori.pipeline.keep_warm", "INFO") as logs:
            keep.tick()
        self.assertIn("keep-warm", logs.output[0])


class ThreadTest(unittest.TestCase):
    def test_background_thread_pings_while_idle_until_stopped(self) -> None:
        clock = FakeClock()
        ping = Pinger(clock)
        done = threading.Event()
        holder: list[KeepWarm] = []

        def fake_wait(timeout: float) -> bool:
            # 不真的睡：推進假時鐘。兩次保溫後模擬 stop。
            if len(ping.at) >= 2:
                done.set()
                return True
            clock.advance(timeout)
            return holder[0].stopped

        keep = KeepWarm(ping, INTERVAL, clock=clock, wait=fake_wait)
        holder.append(keep)
        keep.start()
        self.assertTrue(done.wait(WAIT))
        keep.stop(timeout=WAIT)
        self.assertFalse(keep.is_running)
        self.assertEqual(ping.at, [1000.0 + INTERVAL, 1000.0 + 2 * INTERVAL])

    def test_stop_wakes_the_real_wait_immediately(self) -> None:
        keep = KeepWarm(lambda: None, 3600.0)
        keep.start()
        self.assertTrue(keep.is_running)
        keep.stop(timeout=WAIT)
        self.assertFalse(keep.is_running)
        keep.stop(timeout=WAIT)  # 可重複呼叫

    def test_stop_does_not_wait_for_an_in_flight_request(self) -> None:
        clock = FakeClock()
        entered = threading.Event()
        release = threading.Event()

        def slow_ping() -> None:
            entered.set()
            release.wait(WAIT)

        def fake_wait(timeout: float) -> bool:
            clock.advance(timeout)
            return keep.stopped

        keep = KeepWarm(slow_ping, INTERVAL, clock=clock, wait=fake_wait)
        keep.start()
        self.assertTrue(entered.wait(WAIT))
        keep.stop(timeout=0.05)
        self.assertTrue(keep.stopped)
        release.set()
        thread = keep._thread
        assert thread is not None
        thread.join(WAIT)
        self.assertFalse(thread.is_alive())

    def test_start_twice_is_an_error(self) -> None:
        keep = KeepWarm(lambda: None, 3600.0)
        keep.start()
        self.addCleanup(keep.stop, WAIT)
        with self.assertRaises(RuntimeError):
            keep.start()


class _Translator:
    def __init__(self) -> None:
        self.error: Exception | None = None

    def translate(self, request: TranslationRequest) -> str:
        if self.error is not None:
            raise self.error
        return "好"

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        yield "你"
        if self.error is not None:
            raise self.error
        yield "好"


class _Tracker:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def begin(self) -> None:
        self.calls.append("begin")

    def end(self) -> None:
        self.calls.append("end")


class ActivityTranslatorTest(unittest.TestCase):
    REQUEST = TranslationRequest("はい。", "ja", "zh-TW")

    def setUp(self) -> None:
        self.inner = _Translator()
        self.tracker = _Tracker()
        self.wrapped = ActivityTranslator(self.inner, self.tracker)

    def test_translate_is_bracketed_by_begin_and_end(self) -> None:
        self.assertEqual(self.wrapped.translate(self.REQUEST), "好")
        self.assertEqual(self.tracker.calls, ["begin", "end"])

    def test_stream_ends_when_the_stream_ends(self) -> None:
        stream = self.wrapped.stream(self.REQUEST)
        self.assertEqual(next(stream), "你")
        self.assertEqual(self.tracker.calls, ["begin"])
        self.assertEqual(list(stream), ["好"])
        self.assertEqual(self.tracker.calls, ["begin", "end"])

    def test_errors_propagate_and_still_end(self) -> None:
        self.inner.error = TranslationTimeoutError("slow")
        with self.assertRaises(TranslationTimeoutError):
            self.wrapped.translate(self.REQUEST)
        with self.assertRaises(TranslationTimeoutError):
            list(self.wrapped.stream(self.REQUEST))
        self.assertEqual(self.tracker.calls, ["begin", "end", "begin", "end"])


class InFlightTest(unittest.TestCase):
    """翻譯請求進行中（可能比間隔還久，讀取逾時 60 秒）不送保溫：模型正在用，不必並行再送。"""

    def test_no_ping_while_a_request_is_in_flight(self) -> None:
        clock = FakeClock()
        ping = Pinger(clock)
        keep = KeepWarm(ping, 10.0, clock=clock)
        keep.begin()
        clock.advance(30)
        self.assertEqual(keep.tick(), 10.0)
        self.assertEqual(ping.at, [])
        keep.end()
        clock.advance(9)
        self.assertEqual(keep.tick(), 1.0)
        clock.advance(1)
        keep.tick()
        self.assertEqual(len(ping.at), 1)

    def test_unexpected_errors_are_rate_limited_too(self) -> None:
        clock = FakeClock()
        errors: list[Exception | None] = [ValueError("bug")] * 3
        keep = KeepWarm(
            Pinger(clock, errors), INTERVAL, clock=clock, warn_interval=600.0
        )
        with self.assertLogs("kikitori.pipeline.keep_warm", "WARNING") as logs:
            for _ in range(3):  # t = 240, 480, 720
                clock.advance(INTERVAL)
                keep.tick()
        self.assertEqual([r.levelname for r in logs.records], ["ERROR"])
        self.assertIsNotNone(logs.records[0].exc_info)


if __name__ == "__main__":
    unittest.main()
