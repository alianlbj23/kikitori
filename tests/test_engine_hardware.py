"""engine 的硬體方法（同行程）：背景工作執行緒、偵測快取、設定與推薦組合的轉換。

子行程的端到端測試在 `test_engine.py`；這裡測不容易從協定觀察的細節（快取時間、
回應順序、結束時丟棄的工作），用假時鐘與事件同步，不依賴 sleep。
"""

import dataclasses
import json
import threading
import unittest
from collections.abc import Mapping
from typing import Any

from kikitori.config import Settings
from kikitori.engine import protocol as p
from kikitori.engine.deferred import DeferredRunner
from kikitori.engine.hardware_service import (
    HardwareService,
    combination_from_settings,
    recommendation_patch,
)
from kikitori.engine.testing import FakeHardwareProvider, FakeModelCatalog
from kikitori.hardware import AsrChoice, HardwareInfo, ModelCombination

WAIT = 10.0


class _Recorder:
    """收集送出的訊息；`wait(n)` 等到收到 n 則。"""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []
        self._cond = threading.Condition()

    def send(self, message: Mapping[str, Any]) -> None:
        # 與 MessageWriter 相同的序列化規則：NaN 與無法序列化的值會丟例外
        json.dumps(message, allow_nan=False)
        with self._cond:
            self.messages.append(dict(message))
            self._cond.notify_all()

    def wait(self, count: int) -> list[dict[str, Any]]:
        with self._cond:
            if not self._cond.wait_for(lambda: len(self.messages) >= count, WAIT):
                raise AssertionError(f"only got {self.messages}")
            return list(self.messages)


class DeferredRunnerTest(unittest.TestCase):
    def test_fifo_results_and_errors(self) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        self.addCleanup(runner.close, WAIT)
        release = threading.Event()

        def slow() -> Mapping[str, Any]:
            release.wait(WAIT)
            return {"n": 1}

        def bad_params() -> Mapping[str, Any]:
            raise p.ProtocolError(p.INVALID_PARAMS, "nope")

        def crash() -> Mapping[str, Any]:
            raise RuntimeError("boom")

        runner.submit(1, "a", slow)
        runner.submit(2, "b", bad_params)
        with self.assertLogs("kikitori.engine.deferred", "ERROR"):
            runner.submit(3, "c", crash)
            # submit 不等工作完成：慢的工作還在跑時已經回到呼叫端
            self.assertEqual(out.messages, [])
            release.set()
            messages = out.wait(3)
        self.assertEqual(messages[0], {"id": 1, "result": {"n": 1}})
        self.assertEqual(messages[1]["id"], 2)
        self.assertEqual(messages[1]["error"]["code"], "invalid_params")
        self.assertEqual(messages[2]["id"], 3)
        self.assertEqual(messages[2]["error"]["code"], "internal_error")
        self.assertIn("boom", messages[2]["error"]["message"])

    def test_close_drops_pending_and_rejects_new_work(self) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        started = threading.Event()
        release = threading.Event()

        def blocking() -> Mapping[str, Any]:
            started.set()
            release.wait(WAIT)
            return {}

        runner.submit(1, "a", blocking)
        self.assertTrue(started.wait(WAIT))
        runner.submit(2, "b", lambda: {"never": True})
        release.set()
        runner.close(WAIT)
        # 進行中的工作完成並回應；尚未開始的被丟棄
        self.assertEqual(out.messages, [{"id": 1, "result": {}}])
        with self.assertRaises(RuntimeError):
            runner.submit(3, "c", dict)

    def test_unencodable_result_is_an_error_and_worker_survives(self) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        self.addCleanup(runner.close, WAIT)
        with self.assertLogs("kikitori.engine.deferred", "ERROR"):
            runner.submit(1, "a", lambda: {"x": float("nan")})
            runner.submit(2, "b", lambda: {"ok": True})
            messages = out.wait(2)
        self.assertEqual(messages[0]["id"], 1)
        self.assertEqual(messages[0]["error"]["code"], "internal_error")
        self.assertEqual(messages[1], {"id": 2, "result": {"ok": True}})

    def test_close_without_work(self) -> None:
        DeferredRunner(_Recorder().send).close(WAIT)


class _CountingProvider(FakeHardwareProvider):
    def __init__(self) -> None:
        super().__init__(FakeModelCatalog())
        self.calls = 0

    def detect(self, settings: Settings) -> HardwareInfo:
        self.calls += 1
        return super().detect(settings)


class HardwareServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.now = 100.0
        self.provider = _CountingProvider()
        self.service = HardwareService(
            self.provider, FakeModelCatalog(), cache_secs=10.0, clock=lambda: self.now
        )

    def test_report_always_detects(self) -> None:
        self.service.report(Settings())
        self.service.report(Settings())
        self.assertEqual(self.provider.calls, 2)

    def test_other_methods_reuse_recent_detection(self) -> None:
        settings = Settings()
        self.service.report(settings)
        self.service.models_list(settings)
        self.service.validate(settings)
        self.assertEqual(self.provider.calls, 1)
        self.now += 10.0
        self.service.models_list(settings)
        self.assertEqual(self.provider.calls, 2)

    def test_cache_keyed_by_detection_settings_and_invalidated(self) -> None:
        self.service.models_list(Settings())
        other = Settings().merged({"translation": {"base_url": "http://other:1/v1"}})
        self.service.models_list(other)
        self.assertEqual(self.provider.calls, 2)
        # allow_restricted 只影響推薦，不需重新偵測
        self.service.models_list(
            other.merged({"translation": {"allow_restricted": True}})
        )
        self.assertEqual(self.provider.calls, 2)
        self.service.invalidate()
        self.service.models_list(other)
        self.assertEqual(self.provider.calls, 3)

    def test_detection_overlapping_invalidate_is_not_cached(self) -> None:
        # 偵測進行中 GPU 執行環境裝好（invalidate）：這次的舊結果不能再被重用
        original = self.provider.detect

        def detect_then_invalidate(settings: Settings) -> HardwareInfo:
            info = original(settings)
            self.service.invalidate()
            return info

        self.provider.detect = detect_then_invalidate  # type: ignore[method-assign]
        self.service.models_list(Settings())
        self.provider.detect = original  # type: ignore[method-assign]
        self.service.models_list(Settings())
        self.assertEqual(self.provider.calls, 2)

    def test_recommendation_respects_allow_restricted(self) -> None:
        # 只裝了 HY-MT 時，預設（不允許地區限制）不推薦它
        info = self.provider.detect(Settings())
        restricted = dataclasses.replace(
            info,
            translation_server=dataclasses.replace(
                info.translation_server, models=("hy-mt1.5-1.8b",)
            ),
        )
        self.provider.detect = lambda settings: restricted  # type: ignore[method-assign]
        _, rec = self.service.recommendation(Settings(), fresh=True)
        self.assertEqual(rec.translation_fast, "qwen3.5:4b")
        _, rec = self.service.recommendation(
            Settings().merged({"translation": {"allow_restricted": True}}), fresh=True
        )
        self.assertEqual(rec.translation_fast, "hy-mt1.5-1.8b:latest")


class MappingTest(unittest.TestCase):
    def test_combination_from_settings(self) -> None:
        settings = Settings().merged(
            {
                "asr": {
                    "fast_model": "tiny",
                    "device": "cuda",
                    "final_compute_type": "int8_float16",
                    "final_cpu_threads": 6,
                },
                "translation": {"final_model": "qwen3.5:9b"},
            }
        )
        self.assertEqual(
            combination_from_settings(settings),
            {
                "fast_asr": {
                    "model": "tiny",
                    "device": "cuda",
                    "compute_type": None,
                    "cpu_threads": 0,
                },
                "final_asr": {
                    "model": "small",
                    "device": "cuda",
                    "compute_type": "int8_float16",
                    "cpu_threads": 6,
                },
                "translation_fast": "qwen3.5:4b",
                "translation_final": "qwen3.5:9b",
            },
        )

    def test_recommendation_patch_is_valid_settings(self) -> None:
        rec = ModelCombination(
            tier="nvidia_4gb",
            fast_asr=AsrChoice("base", "cuda", "int8_float16", 0),
            final_asr=AsrChoice("large-v3-turbo", "cuda", "int8_float16", 0),
            translation_fast="qwen3.5:4b",
            translation_final="qwen3.5:4b",
        )
        patch = recommendation_patch(rec)
        self.assertEqual(patch["asr"]["device"], "cuda")
        self.assertIsNone(patch["asr"]["fast_cpu_threads"])
        settings = Settings().merged(patch)
        self.assertEqual(settings.asr.final_model, "large-v3-turbo")
        self.assertEqual(settings.asr.final_compute_type, "int8_float16")
        self.assertEqual(settings.translation.fast_model, "qwen3.5:4b")

    def test_patch_keeps_translation_when_not_recommended(self) -> None:
        rec = ModelCombination(
            tier="cpu",
            fast_asr=AsrChoice("base", "cpu", "int8", 4),
            final_asr=AsrChoice("small", "cpu", "int8", 4),
            translation_fast=None,
            translation_final=None,
        )
        patch = recommendation_patch(rec)
        self.assertNotIn("translation", patch)
        self.assertEqual(patch["asr"]["fast_cpu_threads"], 4)
        self.assertEqual(patch["asr"]["device"], "cpu")

    def test_mixed_devices_fall_back_to_auto(self) -> None:
        rec = ModelCombination(
            tier="custom",
            fast_asr=AsrChoice("base", "cpu", "int8", 2),
            final_asr=AsrChoice("small", "cuda", "float16", 0),
            translation_fast=None,
            translation_final=None,
        )
        self.assertEqual(recommendation_patch(rec)["asr"]["device"], "auto")


if __name__ == "__main__":
    unittest.main()
