"""`detect()`、`hardware_report()` 與 `models_overview()` 的測試（偵測函式以假實作替換，不連網）。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kikitori import hardware
from kikitori.hardware import detection as detect_module
from kikitori.hardware import models_overview, recommend
from kikitori.hardware.nvidia import NvidiaSmiError
from kikitori.hardware.types import (
    CpuInfo,
    CublasInfo,
    GpuInfo,
    HardwareInfo,
    MemoryInfo,
    TranslationServerInfo,
)

GPU = GpuInfo(0, "NVIDIA GeForce RTX 5070", 12227, 1073, "621.01", "12.0")
SERVER = TranslationServerInfo(
    "http://localhost:11434/v1", True, True, ("qwen3.5:9b", "my-custom:7b")
)


class DetectTest(unittest.TestCase):
    def run_detect(
        self,
        *,
        gpus: object = ([GPU], []),
        smi: Path | None = Path("nvidia-smi"),
        platform_name: str = "win32",
        machine: str = "AMD64",
    ) -> HardwareInfo:
        query = (
            mock.Mock(side_effect=gpus)
            if isinstance(gpus, Exception)
            else mock.Mock(return_value=gpus)
        )
        with (
            mock.patch.object(detect_module.sys, "platform", platform_name),
            mock.patch.object(detect_module.platform, "machine", return_value=machine),
            mock.patch.object(
                detect_module.system,
                "detect_cpu",
                return_value=(CpuInfo("CPU", 8, 16, machine), []),
            ),
            mock.patch.object(
                detect_module.system,
                "detect_memory",
                return_value=(MemoryInfo(64 * 1024**3, 32 * 1024**3), []),
            ),
            mock.patch.object(detect_module.system, "os_version", return_value="v"),
            mock.patch.object(detect_module, "find_nvidia_smi", return_value=smi),
            mock.patch.object(detect_module, "query_nvidia_gpus", query),
            mock.patch.object(
                detect_module, "installed_runtime_dir", return_value=None
            ),
            mock.patch.object(
                detect_module,
                "find_cublas",
                return_value=CublasInfo(True, True, ("C:/rt",), "runtime"),
            ),
            mock.patch.object(
                detect_module, "probe_translation_server", return_value=SERVER
            ) as probe,
        ):
            info = detect_module.detect(
                base_url="http://localhost:11434/v1", api_key_env="K"
            )
        probe.assert_called_once()
        self.assertEqual(probe.call_args.args, ("http://localhost:11434/v1", "K"))
        return info

    def test_detect_combines_parts(self) -> None:
        info = self.run_detect()
        self.assertEqual(info.os, "windows")
        self.assertEqual(info.gpus, (GPU,))
        self.assertEqual(info.errors, ())
        self.assertFalse(info.apple_silicon)
        data = json.loads(json.dumps(info.to_dict()))
        self.assertEqual(data["cpu_count"], 16)
        self.assertEqual(data["ram_bytes"], 64 * 1024**3)
        self.assertEqual(data["gpus"][0]["compute_capability"], "12.0")
        self.assertEqual(data["cublas"]["library_dirs"], ["C:/rt"])

    def test_nvidia_smi_missing(self) -> None:
        info = self.run_detect(smi=None)
        self.assertEqual(info.gpus, ())
        self.assertEqual(info.errors, ())

    def test_nvidia_smi_failure_is_reported_not_raised(self) -> None:
        with self.assertLogs("kikitori.hardware.detection", "WARNING"):
            info = self.run_detect(gpus=NvidiaSmiError("timed out"))
        self.assertEqual(info.gpus, ())
        self.assertEqual(info.errors, ("gpu: timed out",))

    def test_skipped_lines_are_reported(self) -> None:
        with self.assertLogs("kikitori.hardware.detection", "WARNING"):
            info = self.run_detect(gpus=([GPU], ["ignored line"]))
        self.assertEqual(info.errors, ("gpu: ignored line",))

    def test_macos_skips_nvidia(self) -> None:
        info = self.run_detect(platform_name="darwin", machine="arm64")
        self.assertEqual(info.os, "macos")
        self.assertEqual(info.gpus, ())

    def test_hardware_report_shape(self) -> None:
        info = HardwareInfo(
            "windows",
            "v",
            "AMD64",
            CpuInfo("CPU", 8, 16, "AMD64"),
            MemoryInfo(64 * 1024**3, None),
            (GPU,),
            False,
            CublasInfo(True, True, ("C:/rt",), "runtime"),
            SERVER,
        )
        with mock.patch.object(hardware, "detect", return_value=info) as det:
            report = hardware.hardware_report(cuda_library_dirs=[Path("C:/x")])
        self.assertEqual(det.call_args.kwargs["cuda_library_dirs"], [Path("C:/x")])
        self.assertEqual(set(report), {"hardware", "recommendation"})
        self.assertEqual(report["recommendation"]["tier"], "nvidia_8gb")
        json.dumps(report)


class OverviewTest(unittest.TestCase):
    def test_overview(self) -> None:
        info = HardwareInfo(
            "windows",
            "v",
            "AMD64",
            CpuInfo("CPU", 8, 16, "AMD64"),
            MemoryInfo(64 * 1024**3, None),
            (GPU,),
            False,
            CublasInfo(True, True, ("C:/rt",), "runtime"),
            SERVER,
        )
        rec = recommend(info)
        with tempfile.TemporaryDirectory() as tmp:
            data = models_overview(
                info, rec, cache_dir=Path(tmp) / "models", runtime_root=Path(tmp) / "rt"
            )
        json.dumps(data)
        asr = {m["id"]: m for m in data["asr"]}
        self.assertEqual(asr["small"]["recommended_for"], ["fast"])
        self.assertEqual(asr["large-v3-turbo"]["recommended_for"], ["final"])
        self.assertFalse(asr["small"]["downloaded"])
        self.assertFalse(asr["large-v3-turbo"]["no_speech_reliable"])
        self.assertTrue(data["gpu_runtime"]["cublas_available"])
        self.assertEqual(data["gpu_runtime"]["cublas_source"], "runtime")
        models = {m["name"]: m for m in data["translation"]["models"]}
        self.assertTrue(models["qwen3.5:9b"]["installed"])
        self.assertEqual(models["qwen3.5:9b"]["recommended_for"], ["fast", "final"])
        self.assertFalse(models["qwen3.5:4b"]["installed"])
        sakura = models["hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS"]
        self.assertFalse(sakura["commercial_use"])
        self.assertEqual(
            models["hy-mt1.5-7b:latest"]["excluded_regions"], ["EU", "UK", "KR"]
        )
        custom = models["my-custom:7b"]
        self.assertFalse(custom["known"])
        self.assertIsNone(custom["license"])
        self.assertTrue(custom["installed"])

    def test_overview_unreachable_server(self) -> None:
        info = HardwareInfo(
            "linux",
            "v",
            "x86_64",
            CpuInfo(None, None, None, "x86_64"),
            MemoryInfo(None, None),
            (),
            False,
            CublasInfo(True, False),
            TranslationServerInfo(
                "http://localhost:11434/v1", True, False, None, "down"
            ),
        )
        with tempfile.TemporaryDirectory() as tmp:
            data = models_overview(
                info, None, cache_dir=Path(tmp), runtime_root=Path(tmp)
            )
        self.assertTrue(
            all(m["installed"] is None for m in data["translation"]["models"])
        )
        self.assertEqual(data["translation"]["server"]["error"], "down")
        self.assertTrue(all(m["recommended_for"] == [] for m in data["asr"]))


if __name__ == "__main__":
    unittest.main()
