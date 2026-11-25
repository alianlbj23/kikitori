"""運算主機的能力 `HostInfo`（docs/specs/compute-capacity.md「二、運算主機的能力」）。

fit 的計算（CUDA／CPU、同一個模型只算一次）、偵測失敗時仍回答、30 秒快取。硬體與「哪些模型已
下載」都是注入的假資料，不跑 nvidia-smi、不碰磁碟。
"""

import dataclasses
import unittest
from typing import Any

from kikitori.asr.registry import MODELS
from kikitori.config import Settings
from kikitori.hardware import (
    CpuInfo,
    CublasInfo,
    GpuInfo,
    HardwareInfo,
    MemoryInfo,
    TranslationServerInfo,
    recommend,
)
from kikitori.hardware.types import LanguageSelection
from kikitori.remote import capacity as c

MIB = 1024 * 1024


def hardware(
    *, vram_mb: int | None = None, ram_mb: int | None = 16384, cc: str = "6.1"
) -> HardwareInfo:
    gpus = (
        ()
        if vram_mb is None
        else (GpuInfo(0, "NVIDIA GeForce GTX 1080 Ti", vram_mb, 500, "560.94", cc),)
    )
    return HardwareInfo(
        os="linux",
        os_version="Ubuntu 24.04",
        machine="x86_64",
        cpu=CpuInfo("Fake CPU", 8, 16, "x86_64"),
        memory=MemoryInfo(
            None if ram_mb is None else ram_mb * MIB,
            None if ram_mb is None else ram_mb * MIB // 2,
        ),
        gpus=gpus,
        apple_silicon=False,
        cublas=CublasInfo(True, vram_mb is not None, ("/opt/cuda",)),
        translation_server=TranslationServerInfo(
            "http://127.0.0.1:11434/v1", True, True, ("qwen3.5:4b",)
        ),
    )


REMOTE_TRANSLATION = "http://192.168.8.20:11434/v1"


def settings(**asr: Any) -> Settings:
    # 翻譯伺服器放在別台：這些測試只看辨識模型的計算（同一台的翻譯見 TranslationShareTest）
    return Settings().merged(
        {"asr": asr, "translation": {"base_url": REMOTE_TRANSLATION}}
    )


def fits(info: dict[str, Any]) -> dict[str, Any]:
    return {m["id"]: m["fit"] for m in info["models"]}


def fits_as_fast(info: dict[str, Any]) -> dict[str, Any]:
    return {m["id"]: m["fit_as_fast"] for m in info["models"]}


class BuildHostInfoTest(unittest.TestCase):
    def test_cuda_uses_the_vram_minus_the_desktop_reserve(self) -> None:
        # 1080 Ti（11 GiB）：可用 11264 − 1024 = 10240，80% = 8192
        info = c.build_host_info(
            settings(
                device="cuda",
                fast_model="large-v3-turbo",
                final_model="large-v3",
                fast_compute_type="int8_float16",
                final_compute_type="int8_float16",
            ),
            hardware(vram_mb=11264),
            {"large-v3", "large-v3-turbo"},
        )
        self.assertEqual(
            info["gpu"], {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264}
        )
        self.assertEqual(
            (info["device"], info["compute_type"]), ("cuda", "int8_float16")
        )
        translation = Settings().translation
        self.assertEqual(
            info["current"],
            {
                "fast": "large-v3-turbo",
                "final": "large-v3",
                "translation": {
                    "fast": translation.fast_model,
                    "final": translation.final_model,
                },
            },
        )
        models = {m["id"]: m for m in info["models"]}
        self.assertEqual(list(models), list(MODELS))
        self.assertEqual(
            models["large-v3"],
            {
                "id": "large-v3",
                "downloaded": True,
                "need_mb": MODELS["large-v3"].approx_gpu_vram_int8_mb,
                "fit": "ok",
                "fit_as_fast": "ok",
            },
        )
        self.assertFalse(models["small"]["downloaded"])
        self.assertEqual(info["translation_model"], Settings().translation.final_model)

    def test_fit_levels_on_a_small_card(self) -> None:
        # 3800 MiB：可用 2776，80% = 2220.8；即時 base（float16 600）＋完整模型，CUDA context 只算一份
        info = c.build_host_info(
            settings(device="cuda", fast_model="base", final_model="small"),
            hardware(vram_mb=3800, cc="8.6"),
            set(),
        )
        self.assertEqual(info["compute_type"], "float16")  # 沒指定精度：CUDA 預設
        self.assertEqual(
            fits(info),
            {
                "tiny": "ok",  # 500 + 600 − 500 = 600
                "base": "ok",
                "small": "ok",  # 1100
                "medium": "tight",  # 2240
                "large-v3-turbo": "tight",  # 2400
                "large-v3": "over",  # 4270
            },
        )
        need = {m["id"]: m["need_mb"] for m in info["models"]}
        self.assertEqual(need["large-v3"], MODELS["large-v3"].approx_gpu_vram_mb)

    def test_the_same_model_on_both_paths_is_loaded_once(self) -> None:
        # 即時與完整同一個模型、同精度：權重只載入一份（另加第二個 worker 的工作記憶體）
        same = c.combined_need_mb(
            settings(
                device="cuda",
                fast_model="large-v3-turbo",
                fast_compute_type="int8_float16",
                final_compute_type="int8_float16",
            ),
            "cuda",
            final="large-v3-turbo",
        )
        turbo = MODELS["large-v3-turbo"].approx_gpu_vram_int8_mb
        self.assertEqual(same, turbo + 512)
        self.assertLess(same, 2 * turbo)
        # 只差在完整模型：同一台卡上 turbo＋turbo 比 turbo＋large-v3 省
        info = c.build_host_info(
            settings(
                device="cuda",
                fast_model="large-v3-turbo",
                fast_compute_type="int8_float16",
                final_compute_type="int8_float16",
            ),
            # 可用 1952、80% = 1561.6：turbo 只算一次是 1752（tight），算兩次會是 over
            hardware(vram_mb=2976),
            set(),
        )
        self.assertEqual(fits(info)["large-v3-turbo"], "tight")
        self.assertEqual(fits(info)["large-v3"], "over")

    def test_cpu_uses_70_percent_of_the_system_memory(self) -> None:
        # 6 GiB：可用 4300.8、80% = 3440.6；即時 base（348）＋完整
        info = c.build_host_info(
            settings(device="cpu", fast_model="base"),
            hardware(vram_mb=None, ram_mb=6144),
            {"base"},
        )
        self.assertIsNone(info["gpu"])
        self.assertEqual((info["device"], info["compute_type"]), ("cpu", "int8"))
        self.assertEqual(fits(info)["large-v3"], "tight")  # 3282 + 348 = 3630
        self.assertEqual(fits(info)["medium"], "ok")
        need = {m["id"]: m["need_mb"] for m in info["models"]}
        self.assertEqual(need["large-v3"], MODELS["large-v3"].approx_cpu_ram_mb)
        tiny = c.build_host_info(
            settings(device="cpu", fast_model="base"),
            hardware(vram_mb=None, ram_mb=2048),
            set(),
        )
        self.assertEqual(fits(tiny)["large-v3"], "over")

    def test_auto_follows_the_hardware(self) -> None:
        with_gpu = c.build_host_info(settings(), hardware(vram_mb=11264), set())
        self.assertEqual(with_gpu["device"], "cuda")
        without = c.build_host_info(settings(), hardware(vram_mb=None), set())
        self.assertEqual(without["device"], "cpu")
        self.assertIsNone(without["gpu"])

    def test_recommended_is_the_hardware_recommendation(self) -> None:
        s = settings()
        info = hardware(vram_mb=11264)
        rec = recommend(
            info,
            allow_restricted=s.translation.allow_restricted,
            languages=LanguageSelection(
                mode=s.mode,
                source_language=s.source_language,
                target_language=s.target_language,
                partner_language=s.partner_language,
            ),
        )
        self.assertEqual(
            c.build_host_info(s, info, set())["recommended"],
            {"fast": rec.fast_asr.model, "final": rec.final_asr.model},
        )

    def test_fit_as_fast_pairs_each_model_with_the_current_final_model(self) -> None:
        # 3800 MiB 的卡（可用 2776、80% = 2220.8）、目前即時 base＋完整 small：
        # fit 是「候選當完整＋base」，fit_as_fast 是「候選當即時＋small」
        info = c.build_host_info(
            settings(device="cuda", fast_model="base", final_model="small"),
            hardware(vram_mb=3800, cc="8.6"),
            set(),
        )
        self.assertEqual(
            fits_as_fast(info),
            {
                "tiny": "ok",  # 1000
                "base": "ok",  # 1100
                "small": "ok",  # 同一個模型只算一次：1512
                "medium": "tight",  # 2640
                "large-v3-turbo": "over",  # 2800（當完整搭 base 只是 tight）
                "large-v3": "over",
            },
        )
        self.assertEqual(fits(info)["large-v3-turbo"], "tight")

    def test_fit_as_fast_uses_the_fast_precision(self) -> None:
        # 候選當即時：用即時路徑的精度（int8 比 float16 省），不是完整路徑的
        def as_fast(fast_type: str) -> Any:
            return fits_as_fast(
                c.build_host_info(
                    settings(
                        device="cuda",
                        fast_model="base",
                        final_model="small",
                        fast_compute_type=fast_type,
                        final_compute_type="float16",
                    ),
                    hardware(vram_mb=3800, cc="8.6"),
                    set(),
                )
            )["large-v3-turbo"]

        self.assertEqual(as_fast("float16"), "over")
        self.assertIn(as_fast("int8_float16"), ("ok", "tight"))

    def test_fit_as_fast_leaves_room_for_a_local_translation_model(self) -> None:
        base = {
            "asr": {
                "device": "cuda",
                "fast_model": "base",
                "final_model": "small",
            },
        }
        local = Settings().merged(
            {
                **base,
                "translation": {
                    "base_url": "http://127.0.0.1:11434/v1",
                    "fast_model": "qwen3.5:9b",
                    "final_model": "qwen3.5:9b",
                },
            }
        )
        far = Settings().merged(
            {**base, "translation": {"base_url": REMOTE_TRANSLATION}}
        )
        card = hardware(vram_mb=8192)
        self.assertEqual(
            fits_as_fast(c.build_host_info(far, card, set()))["base"], "ok"
        )
        self.assertEqual(
            fits_as_fast(c.build_host_info(local, card, set()))["large-v3"], "over"
        )

    def test_detection_failure_still_answers(self) -> None:
        info = c.build_host_info(settings(), None, {"base"})
        self.assertIsNone(info["gpu"])
        self.assertIsNone(info["recommended"])
        self.assertEqual(info["device"], "cpu")
        # 不知道可用量：不猜跑不跑得動
        self.assertTrue(all(m["fit"] is None for m in info["models"]))
        self.assertTrue(all(m["fit_as_fast"] is None for m in info["models"]))
        self.assertTrue(
            next(m for m in info["models"] if m["id"] == "base")["downloaded"]
        )

    def test_forced_cuda_without_a_detected_gpu_has_no_fit(self) -> None:
        info = c.build_host_info(settings(device="cuda"), hardware(), set())
        self.assertEqual(info["device"], "cuda")
        self.assertTrue(all(m["fit"] is None for m in info["models"]))

    def test_changing_the_fast_model_changes_every_fit(self) -> None:
        # compute-capacity 七：fit 是「這個模型當完整辨識＋B 目前的即時模型」，A 改了即時模型
        # 之後 host.updated 的 fit 要跟著變（3800 MiB 的卡：base 搭 large-v3 吃緊、搭 turbo 超出）
        small_card = hardware(vram_mb=3800, cc="8.6")
        light = c.build_host_info(
            settings(device="cuda", fast_model="base"), small_card, set()
        )
        heavy = c.build_host_info(
            settings(device="cuda", fast_model="large-v3-turbo"), small_card, set()
        )
        self.assertNotEqual(fits(light), fits(heavy))
        self.assertEqual(fits(heavy)["large-v3"], "over")


class TranslationModelsTest(unittest.TestCase):
    """compute-capacity 七：B 的翻譯伺服器已安裝的模型（給 A 的翻譯模型選單）。"""

    def with_models(self, models: tuple[str, ...] | None) -> HardwareInfo:
        return dataclasses.replace(
            hardware(vram_mb=11264),
            translation_server=TranslationServerInfo(
                "http://127.0.0.1:11434/v1", True, models is not None, models
            ),
        )

    def test_installed_models_come_from_the_detection(self) -> None:
        info = c.build_host_info(
            settings(), self.with_models(("qwen3.5:4b", "qwen3.5:9b")), set()
        )
        self.assertEqual(info["translation_models"], ["qwen3.5:4b", "qwen3.5:9b"])

    def test_unknown_when_unreachable_or_undetected(self) -> None:
        unreachable = c.build_host_info(settings(), self.with_models(None), set())
        self.assertIsNone(unreachable["translation_models"])
        self.assertIsNone(
            c.build_host_info(settings(), None, set())["translation_models"]
        )

    def test_the_list_is_capped_and_odd_names_are_dropped(self) -> None:
        many = tuple(f"model-{i}" for i in range(100))
        info = c.build_host_info(
            settings(), self.with_models((" padded", "bad\nname", *many)), set()
        )
        self.assertEqual(len(info["translation_models"]), c.MAX_TRANSLATION_MODELS)
        self.assertEqual(info["translation_models"][0], "model-0")

    def test_only_names_the_client_accepts_are_listed(self) -> None:
        # A 的 Rust／TS 只收「英數開頭、英數與 ._:/-、不含 ..、≤ 200 字」：其他的 A 選了也存不回來
        info = c.build_host_info(
            settings(),
            self.with_models(
                (
                    "qwen3.5:4b",
                    "my model",
                    "gpt-4o@2024",
                    "a..b",
                    "-lead",
                    "通訳",
                    "x" * 201,
                    "hf.co/x/y:Q4_K_M",
                )
            ),
            set(),
        )
        self.assertEqual(info["translation_models"], ["qwen3.5:4b", "hf.co/x/y:Q4_K_M"])

    def odd_current(self, fast: str, final: str) -> dict[str, Any]:
        s = Settings().merged(
            {
                "translation": {
                    "base_url": REMOTE_TRANSLATION,
                    "fast_model": fast,
                    "final_model": final,
                }
            }
        )
        return c.build_host_info(s, self.with_models(("qwen3.5:4b",)), set())

    def test_current_translation_needs_names_the_client_accepts(self) -> None:
        good = self.odd_current("qwen3.5:4b", "hf.co/x/y:Q4_K_M")
        self.assertEqual(
            good["current"]["translation"],
            {"fast": "qwen3.5:4b", "final": "hf.co/x/y:Q4_K_M"},
        )
        self.assertEqual(good["translation_model"], "hf.co/x/y:Q4_K_M")
        # 任一個不合格：整個 current.translation 為 null（不送半組）
        odd_fast = self.odd_current("my model", "qwen3.5:4b")
        self.assertIsNone(odd_fast["current"]["translation"])
        self.assertEqual(odd_fast["translation_model"], "qwen3.5:4b")
        odd_final = self.odd_current("qwen3.5:4b", "gpt-4o@2024")
        self.assertIsNone(odd_final["current"]["translation"])
        self.assertIsNone(odd_final["translation_model"])
        # 辨識的部分不受影響
        self.assertEqual(odd_final["current"]["final"], Settings().asr.final_model)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class TranslationShareTest(unittest.TestCase):
    """翻譯模型（Ollama）在同一台電腦時與辨識共用顯示卡記憶體。"""

    def info(self, **translation: Any) -> dict[str, Any]:
        s = Settings().merged(
            {
                "asr": {
                    "fast_model": "large-v3-turbo",
                    "final_model": "large-v3",
                    "device": "cuda",
                    "fast_compute_type": "int8_float16",
                    "final_compute_type": "int8_float16",
                },
                "translation": translation,
            }
        )
        return c.build_host_info(s, hardware(vram_mb=8192), MODELS)

    def test_a_local_translation_model_takes_its_share_of_the_card(self) -> None:
        # 8 GB 卡：large-v3＋turbo 約 3.4 GB；qwen3.5:9b 約 6.6 GB 在同一張卡上就放不下
        local = self.info(
            base_url="http://localhost:11434/v1",
            fast_model="qwen3.5:9b",
            final_model="qwen3.5:9b",
        )
        remote = self.info(
            base_url=REMOTE_TRANSLATION,
            fast_model="qwen3.5:9b",
            final_model="qwen3.5:9b",
        )
        self.assertEqual(fits(remote)["large-v3"], "ok")
        self.assertEqual(fits(local)["large-v3"], "over")

    def test_the_same_translation_model_counts_once_and_two_models_both(self) -> None:
        one = self.info(
            base_url="http://127.0.0.1:11434/v1",
            fast_model="qwen3.5:4b",
            final_model="qwen3.5:4b",
        )
        two = self.info(
            base_url="http://127.0.0.1:11434/v1",
            fast_model="qwen3.5:4b",
            final_model="qwen3.5:9b",
        )
        # 4b 一份（3.3 GB）：large-v3 仍放得下；4b＋9b（9.9 GB）就超出
        self.assertIn(fits(one)["large-v3"], ("ok", "tight"))
        self.assertEqual(fits(two)["large-v3"], "over")


class HostCapacityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.clock = FakeClock()
        self.detections = 0
        self.downloaded = {"base", "small"}
        self.fail = False

        def detect(s: Settings) -> HardwareInfo:
            self.detections += 1
            if self.fail:
                raise RuntimeError("nvidia-smi exploded")
            return hardware(vram_mb=11264)

        self.capacity = c.HostCapacity(
            Settings,
            detect=detect,
            downloaded=lambda: self.downloaded,
            clock=self.clock,
        )

    def test_detection_is_cached_for_30_seconds(self) -> None:
        first = self.capacity.host_info()
        self.clock.now += 29
        self.assertEqual(self.capacity.host_info(), first)
        self.assertEqual(self.detections, 1)
        self.clock.now += 2
        self.capacity.host_info()
        self.assertEqual(self.detections, 2)
        self.capacity.invalidate()
        self.capacity.host_info()
        self.assertEqual(self.detections, 3)

    def test_starting_subtitles_never_waits_for_a_detection(self) -> None:
        # 開字幕的握手用 detect=False：沒有快取就不帶顯示卡資訊，不讓 A 多等幾秒
        cold = self.capacity.host_info(detect=False)
        self.assertEqual(self.detections, 0)
        self.assertIsNone(cold["gpu"])
        self.assertEqual(cold["current"]["final"], Settings().asr.final_model)
        warm = self.capacity.host_info()  # probe 偵測一次
        self.assertEqual(self.capacity.host_info(detect=False), warm)
        self.clock.now += 31
        self.assertIsNone(self.capacity.host_info(detect=False)["gpu"])
        self.assertEqual(self.detections, 1)

    def test_a_failed_detection_answers_without_the_gpu(self) -> None:
        self.fail = True
        with self.assertLogs("kikitori.remote.capacity", "WARNING"):
            info = self.capacity.host_info()
        self.assertIsNone(info["gpu"])
        self.assertIsNone(info["recommended"])

    def test_downloaded_models_is_always_fresh(self) -> None:
        self.assertEqual(self.capacity.downloaded_models(), {"base", "small"})
        self.downloaded = {"base"}
        self.assertEqual(self.capacity.downloaded_models(), {"base"})

    def test_an_unreadable_model_folder_counts_as_nothing_downloaded(self) -> None:
        def broken() -> set[str]:
            raise OSError("permission denied")

        capacity = c.HostCapacity(
            Settings,
            detect=lambda s: hardware(vram_mb=11264),
            downloaded=broken,
            clock=self.clock,
        )
        with self.assertLogs("kikitori.remote.capacity", "ERROR"):
            info = capacity.host_info()
        self.assertFalse(any(m["downloaded"] for m in info["models"]))


if __name__ == "__main__":
    unittest.main()
