"""`metrics` 事件的負荷欄位（compute-capacity 一）：`final_busy`、`rtf`、`gpu` 與 GPU 取樣快取。

本機引擎與運算主機共用 `engine.events.MetricsReporter`，所以兩邊的欄位在這裡一起測；
經過真的連線轉送的部分見 test_remote_server.py。
"""

import logging
import subprocess
import threading
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

from kikitori.engine import events
from kikitori.engine.events import (
    GPU_SAMPLE_INTERVAL,
    GpuSampler,
    MetricsReporter,
    gpu_sampler_for,
    metrics_event,
)
from kikitori.hardware.nvidia import GpuUsage
from kikitori.pipeline.metrics import PipelineMetrics

WAIT = 10.0
INTERVAL = 0.01
USAGE = GpuUsage(
    name="NVIDIA GeForce GTX 1080 Ti",
    vram_used_mb=6144,
    vram_total_mb=11264,
    util_pct=37,
)


def snapshot(**fields: Any) -> PipelineMetrics:
    base: dict[str, Any] = {
        "level_dbfs": -20.0,
        "backlog": 1,
        "latency_ms_median": 800,
    }
    base.update(fields)
    return PipelineMetrics(**base)


class FakePipeline:
    state = "running"

    def __init__(self, metrics: PipelineMetrics) -> None:
        self._metrics = metrics

    def wait(self, timeout: float | None = None) -> bool:
        return False

    def metrics(self) -> PipelineMetrics:
        return self._metrics


class MetricsEventTest(unittest.TestCase):
    def test_all_fields(self) -> None:
        data = metrics_event(
            snapshot(final_busy=0.42, rtf=0.35, speech_level_dbfs=-18.0), 7, USAGE
        )
        self.assertEqual(
            data,
            {
                "session_id": 7,
                "level_dbfs": -20.0,
                "backlog": 1,
                "latency_ms_median": 800,
                "speech_level_dbfs": -18.0,
                "background_level_dbfs": None,
                "final_busy": 0.42,
                "rtf": 0.35,
                "gpu": {
                    "name": "NVIDIA GeForce GTX 1080 Ti",
                    "vram_used_mb": 6144,
                    "vram_total_mb": 11264,
                    "util_pct": 37,
                },
            },
        )

    def test_no_data_is_null(self) -> None:
        data = metrics_event(snapshot(), 7, None)
        self.assertIsNone(data["final_busy"])
        self.assertIsNone(data["rtf"])
        self.assertIsNone(data["gpu"])

    def test_values_are_kept_in_the_range_the_client_accepts(self) -> None:
        # A 端驗證 rtf ≤ 1000、name ≤ 64 字；超出時整則 metrics 會被丟掉，所以 B 端先收斂
        long_name = GpuUsage("N" * 200, 1, 2, 3)
        data = metrics_event(snapshot(final_busy=0.123456, rtf=5000.0), 1, long_name)
        self.assertEqual(data["rtf"], 1000.0)
        self.assertEqual(data["final_busy"], 0.123)
        self.assertEqual(len(data["gpu"]["name"]), 64)


class ReporterTest(unittest.TestCase):
    def run_reporter(self, gpu_usage: Any = None, count: int = 2) -> list[Any]:
        sent: list[Any] = []
        enough = threading.Event()

        def send(name: str, data: Any) -> None:
            sent.append((name, dict(data)))
            if len(sent) >= count:
                enough.set()

        reporter = MetricsReporter(
            3,
            FakePipeline(snapshot(final_busy=0.5, rtf=0.25)),  # type: ignore[arg-type]
            send,
            INTERVAL,
            gpu_usage=gpu_usage,
        )
        reporter.start()
        self.addCleanup(reporter.stop)
        self.assertTrue(enough.wait(WAIT))
        reporter.stop()
        return sent

    def test_without_gpu(self) -> None:
        name, data = self.run_reporter()[0]
        self.assertEqual(name, "metrics")
        self.assertEqual((data["final_busy"], data["rtf"]), (0.5, 0.25))
        self.assertIsNone(data["gpu"])

    def test_with_gpu(self) -> None:
        _, data = self.run_reporter(gpu_usage=lambda: USAGE)[0]
        self.assertEqual(data["gpu"]["vram_used_mb"], 6144)

    def test_gpu_failure_does_not_stop_the_metrics(self) -> None:
        def broken() -> GpuUsage | None:
            raise RuntimeError("bug in the sampler")

        with self.assertLogs("kikitori.engine.events", "WARNING"):
            sent = self.run_reporter(gpu_usage=broken, count=3)
        self.assertEqual(len([s for s in sent if s[0] == "metrics"]), len(sent))
        self.assertTrue(all(data["gpu"] is None for _, data in sent))


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class GpuSamplerTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.results: list[GpuUsage | None] = []
        self.calls = 0

    def sample(self) -> GpuUsage | None:
        self.calls += 1
        return self.results.pop(0) if self.results else USAGE

    def test_default_interval_is_5_seconds(self) -> None:
        self.assertEqual(GPU_SAMPLE_INTERVAL, 5.0)

    def test_samples_at_most_every_interval(self) -> None:
        # metrics 每秒一次，nvidia-smi 一次要上百毫秒：不能每秒都跑
        sampler = GpuSampler(self.sample, clock=self.clock)
        self.assertEqual(sampler.get(), USAGE)
        for _ in range(4):
            self.clock.now += 1.0
            self.assertEqual(sampler.get(), USAGE)
        self.assertEqual(self.calls, 1)
        self.clock.now += 1.0
        sampler.get()
        self.assertEqual(self.calls, 2)

    def test_failure_is_cached_too(self) -> None:
        self.results = [None]
        sampler = GpuSampler(self.sample, clock=self.clock)
        self.assertIsNone(sampler.get())
        self.clock.now += 1.0
        self.assertIsNone(sampler.get())
        self.assertEqual(self.calls, 1)
        self.clock.now += GPU_SAMPLE_INTERVAL
        self.assertEqual(sampler.get(), USAGE)

    def test_exception_becomes_none_and_is_logged(self) -> None:
        def broken() -> GpuUsage | None:
            self.calls += 1
            raise RuntimeError("boom")

        sampler = GpuSampler(broken, clock=self.clock)
        with self.assertLogs("kikitori.engine.events", "WARNING"):
            self.assertIsNone(sampler.get())
        self.clock.now += GPU_SAMPLE_INTERVAL
        # 同一個錯誤之後只記 debug：每 5 秒一則 warning 會淹沒 log
        with self.assertLogs("kikitori.engine.events", "DEBUG") as logs:
            self.assertIsNone(sampler.get())
        self.assertTrue(all(r.levelno == logging.DEBUG for r in logs.records))
        self.assertEqual(self.calls, 2)

    def test_invalid_interval(self) -> None:
        for interval in (0, -1.0, float("nan"), True):
            with self.subTest(interval=interval), self.assertRaises(ValueError):
                GpuSampler(self.sample, interval=interval)  # type: ignore[arg-type]


class Transcriber:
    def __init__(self, device: str | None) -> None:
        if device is not None:
            self.device = device


def components(fast: str | None, final: str | None) -> Any:
    return mock.Mock(
        fast_transcriber=Transcriber(fast), final_transcriber=Transcriber(final)
    )


class GpuSamplerForTest(unittest.TestCase):
    def test_cpu_or_unknown_device_has_no_sampler(self) -> None:
        find = mock.Mock(return_value=Path("nvidia-smi"))
        for fast, final in (("cpu", "cpu"), (None, None)):
            with self.subTest(fast=fast, final=final):
                self.assertIsNone(gpu_sampler_for(components(fast, final), find=find))
        find.assert_not_called()  # 用 CPU 時連 nvidia-smi 都不找

    def test_cuda_samples_gpu_0(self) -> None:
        run = mock.Mock(
            return_value=subprocess.CompletedProcess(
                [], 0, "NVIDIA GeForce GTX 1080 Ti, 6144, 11264, 37\n", ""
            )
        )
        sampler = gpu_sampler_for(
            components("cpu", "cuda"), find=lambda: Path("nvidia-smi"), run=run
        )
        assert sampler is not None
        self.assertEqual(sampler.get(), USAGE)
        self.assertIn("--id=0", run.call_args.args[0])

    def test_cuda_without_nvidia_smi_has_no_sampler(self) -> None:
        with self.assertLogs("kikitori.engine.events", "INFO"):
            self.assertIsNone(
                gpu_sampler_for(components("cuda", "cuda"), find=lambda: None)
            )

    def test_finding_nvidia_smi_fails(self) -> None:
        def find() -> Path | None:
            raise OSError("access denied")

        with self.assertLogs("kikitori.engine.events", "WARNING"):
            self.assertIsNone(gpu_sampler_for(components("cuda", "cuda"), find=find))

    def test_default_finder_uses_the_platform(self) -> None:
        with (
            mock.patch.object(events, "find_nvidia_smi", return_value=None) as find,
            self.assertLogs("kikitori.engine.events", "INFO"),
        ):
            gpu_sampler_for(components("cuda", "cuda"))
        find.assert_called_once()


if __name__ == "__main__":
    unittest.main()
