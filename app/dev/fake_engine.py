"""Fake kikitori engine for UI development and bridge tests (standard library only).

Role: stands in for `kikitori engine` so the Tauri app can be developed and tested
without models, audio devices or a translation server. It speaks the same stdio
NDJSON protocol (docs/specs/app-architecture.md, docs/specs/pipeline-engine.md):
answers every method with plausibly shaped results and, during a session, emits
partial subtitles that grow word by word and are then replaced by a final one.

Compared with the real engine's own fake mode (`KIKITORI_ENGINE_FAKE=1`), it needs
no project environment, its subtitles never stop, and it can inject faults. The
hardware/model methods (Milestone 6 shapes) are a small fixed imitation: an 8 GB
NVIDIA machine without cuBLAS, a GPU runtime behind the NVIDIA EULA gate, and
background methods answered on one worker thread after a short delay, so later
requests can be answered first, as with the real engine.

Point the app at it with
    KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
or use `npm run menu` in app/.

Fault injection (for exercising the bridge): `--garbage-every N` writes an invalid
line after every N subtitle events; `--crash-after SECONDS` exits with status 3;
`--ignore-shutdown` never answers `engine.shutdown` (an engine stuck mid-inference).
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
import queue
import sys
import threading
import time
from collections.abc import Callable
from typing import Any

PROTOCOL_VERSION = 1
SAMPLE_RATE = 16_000
MAX_LINE_BYTES = 1024 * 1024

# Sentences written for this project (no third-party content).
MEDIA_LINES: list[tuple[str, str, str]] = [
    (
        "ja",
        "今日はちょっと寒いけど、散歩に行こうか。",
        "今天有點冷，不過要不要去散個步？",
    ),
    (
        "ja",
        "駅の前に新しいパン屋さんができたんだって。",
        "聽說車站前開了一家新的麵包店。",
    ),
    ("en", "We should leave before the rain starts.", "我們應該在下雨之前出發。"),
    (
        "ja",
        "それ、昨日も同じこと言ってなかった？",
        "那個，你昨天不是也說過一樣的話嗎？",
    ),
    ("ja", "まあ、急がなくても大丈夫だよ。", "嗯，不用急也沒關係啦。"),
    ("en", "Honestly, the second half was much better.", "老實說，後半段好看多了。"),
]
INTERPRET_LINES: list[tuple[str, str, str]] = [
    ("zh", "請問這附近有便利商店嗎？", "すみません、この近くにコンビニはありますか？"),
    (
        "ja",
        "はい、二つ目の角を右に曲がるとありますよ。",
        "有的，在第二個轉角右轉就到了。",
    ),
    (
        "zh",
        "謝謝，那大概要走幾分鐘？",
        "ありがとうございます。歩いて何分くらいですか？",
    ),
    ("ja", "五分くらいだと思います。", "我想大概五分鐘左右。"),
]

ASR_MODELS: list[dict[str, Any]] = [
    {"id": "tiny", "size_bytes": 77_000_000, "license": "MIT", "speed_tier": "fastest"},
    {"id": "base", "size_bytes": 145_000_000, "license": "MIT", "speed_tier": "fast"},
    {
        "id": "small",
        "size_bytes": 484_000_000,
        "license": "MIT",
        "speed_tier": "medium",
    },
    {
        "id": "medium",
        "size_bytes": 1_530_000_000,
        "license": "MIT",
        "speed_tier": "slow",
    },
    {
        "id": "large-v3-turbo",
        "size_bytes": 1_620_000_000,
        "license": "MIT",
        "speed_tier": "slow",
    },
    {
        "id": "large-v3",
        "size_bytes": 3_090_000_000,
        "license": "MIT",
        "speed_tier": "slowest",
    },
]

# (no_speech_reliable, cpu_ram_mb, vram_mb, vram_int8_mb), shaped like models_overview().
ASR_RESOURCES: dict[str, dict[str, Any]] = {
    model_id: {
        "no_speech_reliable": reliable,
        "cpu_ram_mb": ram,
        "vram_mb": vram,
        "vram_int8_mb": vram_int8,
    }
    for model_id, reliable, ram, vram, vram_int8 in (
        ("tiny", True, 250, 500, 400),
        ("base", True, 348, 600, 450),
        ("small", True, 950, 1000, 650),
        ("medium", True, 2300, 2400, 1400),
        ("large-v3-turbo", False, 1890, 2300, 1240),
        ("large-v3", True, 3600, 4200, 2300),
    )
}

GPU_RUNTIME_ID = "gpu-runtime"
GPU_RUNTIME_BYTES = 567_544_208
EULA_URL = "https://docs.nvidia.com/cuda/eula/index.html"
# Long enough that a request sent right after a background one is answered first.
BACKGROUND_DELAY_SECS = 0.3

TRANSLATION_MODELS: list[dict[str, Any]] = [
    {
        "name": name,
        "label": label,
        "installed": installed,
        "known": True,
        "recommended_for": ["fast", "final"] if name == "qwen3.5:4b" else [],
        "license": license_name,
        "license_url": None,
        "commercial_use": commercial,
        "excluded_regions": regions,
        "approx_vram_gb": vram,
    }
    for name, label, installed, license_name, commercial, regions, vram in (
        ("qwen3.5:4b", "Qwen3.5 4B", True, "Apache-2.0", True, [], 3.3),
        ("qwen3.5:9b", "Qwen3.5 9B", False, "Apache-2.0", True, [], 6.6),
        (
            "hy-mt1.5-7b:latest",
            "HY-MT1.5 7B",
            False,
            "Tencent HY Community License",
            True,
            ["EU", "UK", "KR"],
            4.6,
        ),
        (
            "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
            "Sakura-GalTransl 7B v3（非商用）",
            False,
            "CC BY-NC-SA 4.0",
            False,
            [],
            4.3,
        ),
    )
]
COMPUTE_TYPES = (
    "int8",
    "int8_float32",
    "int8_float16",
    "int8_bfloat16",
    "int16",
    "float16",
    "bfloat16",
    "float32",
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 2,
    "mode": "media",
    "target_language": "zh-TW",
    "partner_language": "ja",
    "media": {"pause_secs": 0.6, "max_secs": 10.0, "device_id": None},
    "interpret": {"pause_secs": 0.5, "max_secs": 8.0, "device_id": None},
    "asr": {
        "fast_model": "base",
        "final_model": "small",
        "device": "auto",
        "fast_compute_type": None,
        "final_compute_type": None,
        "fast_cpu_threads": None,
        "final_cpu_threads": None,
        "cuda_library_dirs": [],
    },
    "translation": {
        "base_url": "http://localhost:11434/v1",
        "fast_model": "qwen3.5:4b",
        "final_model": "qwen3.5:4b",
        "api_key_env": "KIKITORI_TRANSLATE_API_KEY",
        "allow_restricted": False,
    },
    "pipeline": {
        "partial_interval_ms": 700,
        "no_speech_threshold": 0.6,
        "context_lines": 3,
        "asr_prompt_context": True,
    },
    "subtitle": {},
}

DEVICES: list[dict[str, Any]] = [
    {
        "id": "fake:loopback:speakers",
        "name": "喇叭（假裝置）",
        "kind": "loopback",
        "is_default": True,
    },
    {
        "id": "fake:loopback:hdmi",
        "name": "HDMI 輸出（假裝置）",
        "kind": "loopback",
        "is_default": False,
    },
    {
        "id": "fake:microphone:builtin",
        "name": "內建麥克風（假裝置）",
        "kind": "microphone",
        "is_default": True,
    },
]


class ProtocolError(Exception):
    """Request-level error answered with `{"error": {"code", "message"}}`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class Writer:
    """Serialises protocol lines to stdout (bytes, UTF-8, one lock)."""

    def __init__(self, garbage_every: int) -> None:
        self._lock = threading.Lock()
        self._out = sys.stdout.buffer
        self._garbage_every = garbage_every
        self._subtitles = 0

    def send(self, message: dict[str, Any]) -> None:
        line = json.dumps(
            message, ensure_ascii=False, separators=(",", ":"), allow_nan=False
        )
        with self._lock:
            self._out.write(line.encode("utf-8") + b"\n")
            if message.get("event") == "subtitle" and self._garbage_every > 0:
                self._subtitles += 1
                if self._subtitles % self._garbage_every == 0:
                    # Deliberately malformed: the bridge must log and skip it.
                    self._out.write(
                        b'{"event": "subtitle", "data": {"utterance_id": -1\n'
                    )
            self._out.flush()

    def event(self, name: str, data: dict[str, Any]) -> None:
        self.send({"event": name, "data": data})


def log(message: str) -> None:
    """Diagnostics go to stderr only; stdout is reserved for the protocol."""
    print(f"[fake-engine] {message}", file=sys.stderr, flush=True)


def deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


def validate_settings(settings: dict[str, Any]) -> None:
    """A small subset of the real engine's checks, enough to exercise UI errors."""
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ProtocolError("invalid_settings", f"{min(unknown)}: unknown field")
    if settings["mode"] not in ("media", "interpret"):
        raise ProtocolError("invalid_settings", "mode: expected 'media' or 'interpret'")
    if settings["partner_language"] not in ("ja", "en"):
        raise ProtocolError(
            "invalid_settings", "partner_language: expected 'ja' or 'en'"
        )
    base_url = settings["translation"].get("base_url")
    if not isinstance(base_url, str) or not base_url.startswith(
        ("http://", "https://")
    ):
        raise ProtocolError(
            "invalid_settings", "translation.base_url: must be an http(s) URL"
        )
    env = settings["translation"].get("api_key_env")
    if (
        not isinstance(env, str)
        or not env.replace("_", "a").isalnum()
        or env[:1].isdigit()
    ):
        raise ProtocolError(
            "invalid_settings", "translation.api_key_env: not a variable name"
        )
    if not isinstance(settings["translation"].get("allow_restricted"), bool):
        raise ProtocolError(
            "invalid_settings", "translation.allow_restricted: expected a boolean"
        )
    asr = settings["asr"]
    unknown_asr = set(asr) - set(DEFAULT_SETTINGS["asr"])
    if unknown_asr:
        raise ProtocolError(
            "invalid_settings", f"asr.{min(unknown_asr)}: unknown field"
        )
    if asr["device"] not in ("auto", "cpu", "cuda"):
        raise ProtocolError(
            "invalid_settings", "asr.device: expected auto, cpu or cuda"
        )
    for role in ("fast", "final"):
        ctype = asr[f"{role}_compute_type"]
        if ctype is not None and ctype not in COMPUTE_TYPES:
            raise ProtocolError(
                "invalid_settings", f"asr.{role}_compute_type: unknown precision"
            )
        threads = asr[f"{role}_cpu_threads"]
        if threads is not None and (
            not isinstance(threads, int)
            or isinstance(threads, bool)
            or not 1 <= threads <= 256
        ):
            raise ProtocolError(
                "invalid_settings",
                f"asr.{role}_cpu_threads: expected an integer in [1, 256], got {threads!r}",
            )
    dirs = asr["cuda_library_dirs"]
    if not isinstance(dirs, list) or len(dirs) > 16:
        raise ProtocolError(
            "invalid_settings", "asr.cuda_library_dirs: at most 16 paths"
        )
    if not isinstance(settings["subtitle"], dict):
        raise ProtocolError("invalid_settings", "subtitle: expected an object")


class Session(threading.Thread):
    """Emits loading → running → subtitle stream until stopped."""

    def __init__(self, session_id: int, mode: str, writer: Writer) -> None:
        super().__init__(name=f"session-{session_id}", daemon=True)
        self.session_id = session_id
        self.mode = mode
        self.writer = writer
        self.stop_event = threading.Event()

    def status(self, state: str, detail: str | None = None) -> None:
        self.writer.event(
            "status",
            {
                "state": state,
                "detail": detail,
                "session_id": self.session_id,
                "code": None,
            },
        )

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            # Report like the real engine does (status error) instead of dying silently.
            log(f"session {self.session_id} failed: {exc!r}")
            self.status("error", f"{type(exc).__name__}: {exc}")
            raise

    def _run(self) -> None:
        self.status("loading", "載入模型中（假）")
        if self.stop_event.wait(1.0):
            self.status("idle")
            return
        self.status("running")
        lines = MEDIA_LINES if self.mode == "media" else INTERPRET_LINES
        position = SAMPLE_RATE  # first utterance starts after 1 s
        utterance_id = 0
        while not self.stop_event.is_set():
            source, original, translation = lines[utterance_id % len(lines)]
            utterance_id += 1
            start = position
            if not self._speak(utterance_id, source, original, translation, start):
                break
            position = start + SAMPLE_RATE * 3
            if self.stop_event.wait(1.2):
                break
        self.status("idle")

    def _target_language(self, source: str) -> str:
        if self.mode == "interpret" and source == "zh":
            return "ja"
        return "zh-TW"

    def _speak(
        self,
        utterance_id: int,
        source: str,
        original: str,
        translation: str,
        start: int,
    ) -> bool:
        """Partial results grow through the sentence, then the final replaces them."""
        steps = 4
        for i in range(1, steps + 1):
            if self.stop_event.wait(0.55):
                return False
            cut_o = max(1, len(original) * i // (steps + 1))
            cut_t = max(1, len(translation) * i // (steps + 1))
            self._emit(
                utterance_id,
                source,
                original[:cut_o],
                translation[:cut_t],
                False,
                start,
                i,
            )
        if self.stop_event.wait(0.6):
            return False
        self._emit(utterance_id, source, original, translation, True, start, steps + 1)
        return True

    def _emit(
        self,
        utterance_id: int,
        source: str,
        original: str,
        translation: str,
        is_final: bool,
        start: int,
        step: int,
    ) -> None:
        self.writer.event(
            "subtitle",
            {
                "utterance_id": utterance_id,
                "original": original,
                "translation": translation,
                "source_language": source,
                "target_language": self._target_language(source),
                "is_final": is_final,
                "start_sample": start,
                "end_sample": start + int(SAMPLE_RATE * 0.5 * step),
                "session_id": self.session_id,
            },
        )


class FakeEngine:
    def __init__(self, writer: Writer, ignore_shutdown: bool = False) -> None:
        self.writer = writer
        # Imitates an engine stuck mid-inference: `engine.shutdown` is never
        # answered, so the app has to fall back to killing the process.
        self.ignore_shutdown = ignore_shutdown
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.downloaded = {"tiny", "base", "small"}
        self.downloading: set[str] = set()
        self.session: Session | None = None
        self.next_session_id = 1
        self.lock = threading.Lock()
        self.after_response: list[Callable[[], None]] = []
        self.methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "engine.info": self.engine_info,
            "models.download": self.models_download,
            "devices.list": lambda _p: {"devices": DEVICES},
            "settings.get": lambda _p: self.settings_result(),
            "settings.set": self.settings_set,
            "session.start": self.session_start,
            "session.stop": self.session_stop,
            "engine.shutdown": lambda _p: {},
        }
        # Like the real engine, these run on one worker thread in arrival order
        # and answer when done, i.e. possibly after requests sent later.
        self.background: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "hardware.detect": self.hardware_detect,
            "models.list": self.models_list,
            "models.validate": self.models_validate,
            "settings.apply_recommendation": self.settings_apply_recommendation,
        }
        self.jobs: queue.Queue[tuple[int, str, dict[str, Any]] | None] = queue.Queue()
        self.worker = threading.Thread(
            target=self._work, name="background", daemon=True
        )
        self.worker.start()

    def _work(self) -> None:
        while True:
            job = self.jobs.get()
            if job is None:
                return
            request_id, method, params = job
            time.sleep(BACKGROUND_DELAY_SECS)
            try:
                result = self.background[method](params)
            except ProtocolError as exc:
                self.writer.send(
                    {
                        "id": request_id,
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )
                continue
            self.writer.send({"id": request_id, "result": result})

    def stop_worker(self) -> None:
        self.jobs.put(None)
        self.worker.join(timeout=2)

    def engine_info(self, _params: dict[str, Any]) -> dict[str, Any]:
        return {
            "name": "kikitori-engine",
            "version": "0.1.0",
            "protocol_version": PROTOCOL_VERSION,
            "platform": {"win32": "windows", "darwin": "macos"}.get(
                sys.platform, "linux"
            ),
            "machine": platform.machine(),
            "python_version": platform.python_version(),
            "fake": True,
        }

    def _runtime_installed(self) -> bool:
        return GPU_RUNTIME_ID in self.downloaded

    def recommendation(self) -> dict[str, Any]:
        """A fixed 8 GB NVIDIA machine; CPU combination until the runtime is installed."""
        if self._runtime_installed():
            fast = {"model": "small", "device": "cuda", "compute_type": "float16"}
            final = {
                "model": "large-v3-turbo",
                "device": "cuda",
                "compute_type": "float16",
            }
            threads, tier, budget = 0, "nvidia_8gb", 4.9
            warnings: list[str] = []
        else:
            fast = {"model": "base", "device": "cpu", "compute_type": "int8"}
            final = {"model": "small", "device": "cpu", "compute_type": "int8"}
            threads, tier, budget = 4, "cpu", 7.0
            warnings = [
                "cuBLAS 未安裝，GPU 加速需要先下載 GPU 執行環境（約 570 MB）；在那之前 ASR 使用 CPU"
            ]
        return {
            "tier": tier,
            "fast_asr": {**fast, "cpu_threads": threads},
            "final_asr": {**final, "cpu_threads": threads},
            "translation_fast": "qwen3.5:4b",
            "translation_final": "qwen3.5:4b",
            "translation_budget_gb": budget,
            "gpu_runtime_missing": not self._runtime_installed(),
            "reasons": ["假引擎：依假的 8 GB 顯示卡推薦"],
            "warnings": warnings,
        }

    def hardware_detect(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Shape of docs/specs/hardware.md (`HardwareInfo.to_dict()`)."""
        runtime = self._runtime_installed()
        return {
            "hardware": {
                "os": "windows",
                "os_version": "Windows 11 (10.0.26200)",
                "machine": "AMD64",
                "cpu": {
                    "model": "Fake 8-Core CPU",
                    "physical_cores": 8,
                    "logical_cores": 16,
                    "architecture": "AMD64",
                },
                "cpu_count": 16,
                "memory": {
                    "total_bytes": 32 * 1024**3,
                    "available_bytes": 16 * 1024**3,
                },
                "ram_bytes": 32 * 1024**3,
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA GeForce RTX 4060 (fake)",
                        "memory_total_mb": 8188,
                        "memory_used_mb": 900,
                        "driver_version": "560.94",
                        "compute_capability": "8.9",
                    }
                ],
                "apple_silicon": False,
                "cublas": {
                    "supported_platform": True,
                    "available": runtime,
                    "library_dirs": ["C:/fake/cublas"] if runtime else [],
                    "source": "runtime" if runtime else None,
                },
                "translation_server": {
                    "base_url": self.settings["translation"]["base_url"],
                    "is_local": True,
                    "reachable": True,
                    "models": ["qwen3.5:4b"],
                    "error": None,
                },
                "errors": [],
            },
            "recommendation": self.recommendation(),
        }

    def models_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Shape of docs/specs/hardware.md (`models_overview()`)."""
        rec = self.recommendation()
        roles = {
            rec["fast_asr"]["model"]: ["fast"],
        }
        roles.setdefault(rec["final_asr"]["model"], []).append("final")
        runtime = self._runtime_installed()
        return {
            "asr": [
                {
                    **m,
                    **ASR_RESOURCES[m["id"]],
                    "downloaded": m["id"] in self.downloaded,
                    "recommended_for": roles.get(m["id"], []),
                }
                for m in ASR_MODELS
            ],
            "gpu_runtime": {
                "id": GPU_RUNTIME_ID,
                "supported": True,
                "installed": runtime,
                "package": "nvidia-cublas-cu12",
                "version": "12.8.4.1",
                "path": "C:/fake/cublas" if runtime else None,
                "download_bytes": GPU_RUNTIME_BYTES,
                "installed_bytes": 795_000_000,
                "license": "NVIDIA CUDA Toolkit End User License Agreement",
                "license_url": EULA_URL,
                "cublas_available": runtime,
                "cublas_source": "runtime" if runtime else None,
            },
            "translation": {
                "server": {
                    "base_url": self.settings["translation"]["base_url"],
                    "is_local": True,
                    "reachable": True,
                    "error": None,
                },
                "models": TRANSLATION_MODELS,
            },
        }

    def models_validate(self, params: dict[str, Any]) -> dict[str, Any]:
        """A few of `validate_combination()`'s rules, enough to see errors and warnings."""
        candidate = params.get("settings", {})
        if not isinstance(candidate, dict):
            raise ProtocolError("invalid_params", "settings: expected an object")
        merged = deep_merge(self.settings, candidate)
        validate_settings(merged)
        asr, tr = merged["asr"], merged["translation"]
        errors: list[str] = []
        warnings: list[str] = []
        ids = {m["id"] for m in ASR_MODELS}
        for role in ("fast", "final"):
            model = asr[f"{role}_model"]
            if model not in ids:
                errors.append(f"{role}_asr.model：未知的 ASR 模型 {model!r}")
            elif model not in self.downloaded:
                warnings.append(f"{role}_asr：{model} 尚未下載")
        if asr["device"] == "cuda" and not self._runtime_installed():
            errors.append("選了 CUDA，但 cuBLAS 未安裝；請先下載 GPU 執行環境")
        for name in {tr["fast_model"], tr["final_model"]}:
            info = next((m for m in TRANSLATION_MODELS if m["name"] == name), None)
            if info is None or not info["installed"]:
                warnings.append(f"翻譯模型 {name} 不在翻譯伺服器上")
            elif info["excluded_regions"] and not tr["allow_restricted"]:
                warnings.append(f"{name} 的授權不適用於歐盟、英國、南韓")
            elif info["commercial_use"] is False:
                warnings.append(f"{name} 為非商用授權")
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def settings_apply_recommendation(self, _params: dict[str, Any]) -> dict[str, Any]:
        rec = self.recommendation()
        fast, final = rec["fast_asr"], rec["final_asr"]
        applied = {
            "asr": {
                "fast_model": fast["model"],
                "final_model": final["model"],
                "device": fast["device"],
                "fast_compute_type": fast["compute_type"],
                "final_compute_type": final["compute_type"],
                "fast_cpu_threads": fast["cpu_threads"] or None,
                "final_cpu_threads": final["cpu_threads"] or None,
            },
            "translation": {
                "fast_model": rec["translation_fast"],
                "final_model": rec["translation_final"],
            },
        }
        self.settings_set(applied)
        return {**self.settings_result(), "applied": applied, "recommendation": rec}

    def models_download(self, params: dict[str, Any]) -> dict[str, Any]:
        model = params.get("model")
        sizes = {m["id"]: int(m["size_bytes"]) for m in ASR_MODELS}
        sizes[GPU_RUNTIME_ID] = GPU_RUNTIME_BYTES
        if not isinstance(model, str) or model not in sizes:
            raise ProtocolError("unknown_model", f"unknown model {model!r}")
        accept = params.get("accept_license")
        if model == GPU_RUNTIME_ID:
            if accept is not None and not isinstance(accept, bool):
                raise ProtocolError(
                    "invalid_params", "accept_license: expected a boolean"
                )
            if accept is not True:
                raise ProtocolError(
                    "license_not_accepted",
                    f"downloading nvidia-cublas-cu12 requires accepting the NVIDIA CUDA "
                    f"Toolkit End User License Agreement: {EULA_URL}",
                )
        elif accept is not None:
            raise ProtocolError(
                "invalid_params", "accept_license only applies to gpu-runtime"
            )
        with self.lock:
            if model in self.downloading:
                raise ProtocolError(
                    "download_in_progress", f"{model} is already downloading"
                )
            self.downloading.add(model)
        thread = threading.Thread(
            target=self._download, args=(model, sizes[model]), daemon=True
        )
        self.after_response.append(thread.start)
        return {"model": model, "started": True}

    def _download(self, model: str, total: int) -> None:
        steps = 20
        for i in range(steps + 1):
            self.writer.event(
                "download_progress",
                {
                    "model": model,
                    "downloaded_bytes": total * i // steps,
                    "total_bytes": total,
                    "state": "downloading",
                    "error": None,
                },
            )
            time.sleep(0.15)
        with self.lock:
            self.downloading.discard(model)
            self.downloaded.add(model)
        self.writer.event(
            "download_progress",
            {
                "model": model,
                "downloaded_bytes": total,
                "total_bytes": total,
                "state": "done",
                "error": None,
            },
        )

    def settings_result(self) -> dict[str, Any]:
        return {
            "settings": copy.deepcopy(self.settings),
            "path": "<fake engine: in memory>",
        }

    def settings_set(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = deep_merge(self.settings, params)
        validate_settings(merged)
        self.settings = merged
        return self.settings_result()

    def session_start(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode")
        if mode not in ("media", "interpret"):
            raise ProtocolError(
                "invalid_params", "mode: expected 'media' or 'interpret'"
            )
        device_id = params.get("device_id")
        if device_id is not None and device_id not in {d["id"] for d in DEVICES}:
            raise ProtocolError("device_not_found", f"no such device: {device_id!r}")
        with self.lock:
            if self.session is not None and self.session.is_alive():
                raise ProtocolError("session_active", "a session is already running")
            session = Session(self.next_session_id, mode, self.writer)
            self.next_session_id += 1
            self.session = session
        self.after_response.append(session.start)
        return {"session_id": session.session_id}

    def session_stop(self, _params: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            session = self.session
            self.session = None
        if session is not None:
            session.stop_event.set()
            session.join(timeout=5)
        return {}

    def handle(self, raw: bytes) -> bool:
        """Handles one request line; returns False after `engine.shutdown`."""
        if not raw.strip():
            return True
        request_id: Any = None
        try:
            try:
                request = json.loads(raw.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                raise ProtocolError(
                    "parse_error", f"cannot parse request: {exc}"
                ) from exc
            if not isinstance(request, dict):
                raise ProtocolError("invalid_request", "request must be an object")
            request_id = request.get("id")
            if not isinstance(request_id, int) or isinstance(request_id, bool):
                request_id = None
                raise ProtocolError("invalid_request", "id must be an integer")
            method = request.get("method")
            params = request.get("params", {})
            if not isinstance(params, dict):
                raise ProtocolError("invalid_request", "params must be an object")
            if isinstance(method, str) and method in self.background:
                # Settings errors are answered at once, like the real engine.
                if method == "models.validate":
                    candidate = params.get("settings", {})
                    if not isinstance(candidate, dict):
                        raise ProtocolError(
                            "invalid_params", "settings: expected an object"
                        )
                    validate_settings(deep_merge(self.settings, candidate))
                self.jobs.put((request_id, method, params))
                return True
            if method == "engine.shutdown" and self.ignore_shutdown:
                log("ignoring engine.shutdown (--ignore-shutdown)")
                return True
            handler = self.methods.get(method) if isinstance(method, str) else None
            if handler is None:
                raise ProtocolError("unknown_method", f"unknown method {method!r}")
            result = handler(params)
            self.writer.send({"id": request_id, "result": result})
            for action in self.after_response:
                action()
            self.after_response.clear()
            return method != "engine.shutdown"
        except ProtocolError as exc:
            self.after_response.clear()
            self.writer.send(
                {"id": request_id, "error": {"code": exc.code, "message": exc.message}}
            )
            return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Fake kikitori engine (stdio NDJSON).")
    parser.add_argument("--garbage-every", type=int, default=0, metavar="N")
    parser.add_argument("--crash-after", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument(
        "--ignore-shutdown",
        action="store_true",
        help="never answer engine.shutdown (the app must kill the process)",
    )
    args = parser.parse_args()
    if args.garbage_every < 0 or args.crash_after < 0:
        parser.error("arguments must be non-negative")

    writer = Writer(args.garbage_every)
    engine = FakeEngine(writer, ignore_shutdown=args.ignore_shutdown)
    if args.crash_after > 0:

        def crash() -> None:
            log(f"simulated crash after {args.crash_after} s")
            sys.stderr.flush()
            # os._exit skips cleanup on purpose: it imitates a hard crash.
            os._exit(3)

        threading.Timer(args.crash_after, crash).start()

    log("fake engine started; stdout carries protocol messages only")
    writer.event(
        "status", {"state": "idle", "detail": None, "session_id": None, "code": None}
    )
    stdin = sys.stdin.buffer
    while True:
        raw = stdin.readline(MAX_LINE_BYTES + 1)
        if not raw:
            break
        if len(raw) > MAX_LINE_BYTES and not raw.endswith(b"\n"):
            # Discard the rest of the over-long line, then report it.
            while raw and not raw.endswith(b"\n"):
                raw = stdin.readline(MAX_LINE_BYTES + 1)
            writer.send(
                {
                    "id": None,
                    "error": {"code": "invalid_request", "message": "line too long"},
                }
            )
            continue
        if not engine.handle(raw):
            break
    engine.session_stop({})
    engine.stop_worker()
    log("fake engine exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
