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
    language_selection,
    recommendation_patch,
    recommendation_preview,
)
from kikitori.engine.testing import FakeHardwareProvider, FakeModelCatalog
from kikitori.hardware import (
    AsrChoice,
    HardwareInfo,
    LanguageSelection,
    ModelCombination,
)

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

    def test_after_runs_once_the_success_response_is_written(self) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        self.addCleanup(runner.close, WAIT)
        order: list[str] = []
        done = threading.Event()

        def after() -> None:
            # 回應已經寫出：UI 先拿到回應，才收到之後的事件
            order.append(f"after:{len(out.messages)}")
            done.set()

        def failing() -> Mapping[str, Any]:
            raise p.ProtocolError(p.INVALID_PARAMS, "nope")

        runner.submit(1, "a", failing, lambda: order.append("never"))
        runner.submit(2, "b", lambda: {"ok": True}, after)
        self.assertTrue(done.wait(WAIT))
        self.assertEqual(order, ["after:2"])

    def test_unsent_runs_when_the_success_response_cannot_be_written(
        self,
    ) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        self.addCleanup(runner.close, WAIT)
        undone: list[int] = []
        with self.assertLogs("kikitori.engine.deferred", "ERROR"):
            # NaN 無法序列化：成功的結果寫不出去，after 不會執行
            runner.submit(
                1,
                "a",
                lambda: {"x": float("nan")},
                lambda: undone.append(-1),
                lambda: undone.append(1),
            )
            # 錯誤回應：job 沒有保留任何東西，不必撤銷
            runner.submit(
                2,
                "b",
                lambda: (_ for _ in ()).throw(p.ProtocolError(p.INVALID_PARAMS, "x")),
                None,
                lambda: undone.append(2),
            )
            # 正常寫出：執行 after，不執行 unsent
            runner.submit(
                3, "c", dict, lambda: undone.append(3), lambda: undone.append(-3)
            )
            out.wait(3)
        runner.close(WAIT)
        self.assertEqual(undone, [1, 3])

    def test_failing_after_is_logged_and_the_worker_survives(self) -> None:
        out = _Recorder()
        runner = DeferredRunner(out.send)
        self.addCleanup(runner.close, WAIT)

        def boom() -> None:
            raise RuntimeError("cannot start thread")

        with self.assertLogs("kikitori.engine.deferred", "ERROR"):
            runner.submit(1, "a", dict, boom)
            runner.submit(2, "b", lambda: {"ok": True})
            messages = out.wait(2)
        self.assertEqual(messages[1], {"id": 2, "result": {"ok": True}})


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
        # 只裝了 HY-MT 時不推薦它：預設不允許地區限制；允許了也一樣（HY-MT 只給使用者自選）
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
        self.assertEqual(rec.translation_fast, "qwen3.5:4b")


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


def _gpu_rec(final: str = "large-v3-turbo") -> ModelCombination:
    return ModelCombination(
        tier="nvidia_8gb",
        fast_asr=AsrChoice("small", "cuda", "float16", 0),
        final_asr=AsrChoice(final, "cuda", "float16", 0),
        translation_fast="qwen3.5:9b",
        translation_final="qwen3.5:9b",
    )


class PreviewTest(unittest.TestCase):
    def test_patch_is_what_apply_writes(self) -> None:
        rec = _gpu_rec()
        preview = recommendation_preview(Settings(), rec)
        self.assertEqual(preview["patch"], recommendation_patch(rec))
        self.assertEqual(preview["recommendation"], rec.to_dict())
        self.assertTrue(preview["differs"])

    def test_not_different_after_applying(self) -> None:
        rec = _gpu_rec()
        applied = Settings().merged(recommendation_patch(rec))
        self.assertFalse(recommendation_preview(applied, rec)["differs"])
        # 推薦以外的欄位（字幕外觀、ui）不影響比較
        other = applied.merged({"subtitle": {"font_size": 40}, "mode": "interpret"})
        self.assertFalse(recommendation_preview(other, rec)["differs"])

    def test_different_after_a_relevant_change(self) -> None:
        rec = _gpu_rec()
        applied = Settings().merged(recommendation_patch(rec))
        manual = applied.merged({"asr": {"final_model": "medium"}})
        self.assertTrue(recommendation_preview(manual, rec)["differs"])
        manual_tr = applied.merged({"translation": {"fast_model": "qwen3.5:4b"}})
        self.assertTrue(recommendation_preview(manual_tr, rec)["differs"])

    def test_fingerprint_identifies_the_patch(self) -> None:
        a = recommendation_preview(Settings(), _gpu_rec())["fingerprint"]
        again = recommendation_preview(
            Settings().merged({"mode": "interpret"}), _gpu_rec()
        )["fingerprint"]
        other = recommendation_preview(Settings(), _gpu_rec("large-v3"))["fingerprint"]
        self.assertRegex(a, r"^[0-9a-f]{16}$")
        # 同一個推薦（不論目前設定）同一個指紋；推薦改變就換指紋
        self.assertEqual(a, again)
        self.assertNotEqual(a, other)

    def test_result_is_json(self) -> None:
        preview = recommendation_preview(Settings(), _gpu_rec())
        self.assertEqual(json.loads(json.dumps(preview)), preview)

    def test_fingerprint_changes_with_the_languages(self) -> None:
        # 同一份 patch、不同語言：「不要再提醒」過的推薦，換語言後要能再提醒
        ja = dataclasses.replace(_gpu_rec(), languages=LanguageSelection("media", "ja"))
        ko = dataclasses.replace(_gpu_rec(), languages=LanguageSelection("media", "ko"))
        a = recommendation_preview(Settings(), ja)
        b = recommendation_preview(Settings(), ko)
        self.assertEqual(a["patch"], b["patch"])
        self.assertNotEqual(a["fingerprint"], b["fingerprint"])


class LanguageAwareServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.catalog = FakeModelCatalog()
        self.service = HardwareService(FakeHardwareProvider(self.catalog), self.catalog)

    def test_recommendation_follows_the_settings_languages(self) -> None:
        settings = Settings().merged(
            {"source_language": "zh-CN", "target_language": "zh-TW"}
        )
        _, rec = self.service.recommendation(settings)
        self.assertEqual(rec.translation_status, "not_needed")
        self.assertEqual(
            rec.languages, LanguageSelection("media", "zh-CN", "zh-TW", "ja")
        )
        interpret = Settings().merged({"mode": "interpret", "partner_language": "en"})
        _, rec = self.service.recommendation(interpret)
        self.assertEqual(rec.languages.mode if rec.languages else None, "interpret")

    def test_language_selection_from_settings(self) -> None:
        settings = Settings().merged({"source_language": "auto"})
        self.assertEqual(
            language_selection(settings),
            LanguageSelection("media", "auto", "zh-TW", "ja"),
        )
        self.assertEqual(language_selection(settings, "interpret").mode, "interpret")

    def test_validate_checks_languages(self) -> None:
        settings = Settings().merged(
            {
                "source_language": "ko",
                "translation": {
                    "fast_model": "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"
                },
            }
        )
        result = self.service.validate(settings)
        self.assertIn(
            "translation_model_language_unsupported",
            [w["code"] for w in result["warnings"]],
        )

    def test_preview_languages_reports_models_and_sizes(self) -> None:
        current = Settings()
        candidate = current.merged({"source_language": "ko", "target_language": "es"})
        preview = self.service.preview_languages(candidate, current)
        self.assertEqual(json.loads(json.dumps(preview)), preview)
        self.assertEqual(
            preview["languages"],
            {
                "mode": "media",
                "source_language": "ko",
                "target_language": "es",
                "partner_language": "ja",
            },
        )
        rec = preview["recommendation"]
        self.assertEqual(rec["languages"]["source_language"], "ko")
        asr = {m["model"]: m for m in preview["models"]["asr"]}
        self.assertEqual(asr["base"]["roles"], ["fast"])
        self.assertTrue(asr["base"]["installed"])
        self.assertEqual(asr["base"]["download_bytes"], 0)
        translation = preview["models"]["translation"]
        self.assertEqual(
            [(m["model"], m["roles"], m["installed"]) for m in translation],
            [("qwen3.5:4b", ["fast", "final"], True)],
        )
        self.assertGreater(translation[0]["size_bytes"], 0)
        self.assertTrue(preview["translation_needed"])
        self.assertEqual(preview["download_bytes"], 0)
        self.assertTrue(preview["download_size_known"])
        # 合併後的設定是「套用語言＋推薦模型」的樣子；與目前設定不同
        self.assertEqual(preview["settings"]["source_language"], "ko")
        self.assertTrue(preview["differs"])
        self.assertRegex(preview["fingerprint"], r"^[0-9a-f]{16}$")

    def test_preview_counts_missing_models(self) -> None:
        current = Settings()
        candidate = current.merged(
            {"asr": {"final_model": "fake-missing"}, "source_language": "en"}
        )
        provider = FakeHardwareProvider(self.catalog)
        service = HardwareService(provider, self.catalog)
        info = provider.detect(candidate)
        no_models = dataclasses.replace(
            info,
            translation_server=dataclasses.replace(info.translation_server, models=()),
        )
        provider.detect = lambda settings: no_models  # type: ignore[method-assign]
        preview = service.preview_languages(candidate, current)
        translation = preview["models"]["translation"][0]
        self.assertFalse(translation["installed"])
        self.assertEqual(translation["download_bytes"], translation["size_bytes"])
        self.assertEqual(preview["download_bytes"], translation["size_bytes"])

    def test_preview_with_translation_off_needs_no_translation_model(self) -> None:
        current = Settings().merged({"translation": {"enabled": False}})
        preview = self.service.preview_languages(current, current)
        self.assertFalse(preview["translation_needed"])
        self.assertIn("translation_disabled", [m["code"] for m in preview["messages"]])
        self.assertEqual(preview["download_bytes"], 0)

    def test_same_language_needs_no_translation_model(self) -> None:
        current = Settings()
        candidate = current.merged({"source_language": "zh-CN"})
        preview = self.service.preview_languages(candidate, current)
        self.assertFalse(preview["translation_needed"])
        self.assertEqual(preview["models"]["translation"], [])
        self.assertNotIn("translation", preview["patch"])


if __name__ == "__main__":
    unittest.main()
