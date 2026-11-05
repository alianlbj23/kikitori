"""Fake kikitori engine for UI development and bridge tests (standard library only).

Role: stands in for `kikitori engine` so the Tauri app can be developed and tested
without models, audio devices or a translation server. It speaks the same stdio
NDJSON protocol (docs/specs/app-architecture.md, docs/specs/pipeline-engine.md):
answers every method with plausibly shaped results and, during a session, emits
partial subtitles that grow word by word and are then replaced by a final one.

Point the app at it with
    KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
or use `npm run menu` in app/.

Fault injection (for exercising the bridge): `--garbage-every N` writes an invalid
line after every N subtitle events; `--crash-after SECONDS` exits with status 3.
"""

from __future__ import annotations

import argparse
import copy
import json
import os
import platform
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

GPU_RUNTIME_ID = "gpu-runtime"
GPU_RUNTIME_BYTES = 567_544_208

DEFAULT_SETTINGS: dict[str, Any] = {
    "schema_version": 2,
    "mode": "media",
    "target_language": "zh-TW",
    "partner_language": "ja",
    "media": {"pause_secs": 0.6, "max_secs": 10.0, "device_id": None},
    "interpret": {"pause_secs": 0.5, "max_secs": 8.0, "device_id": None},
    "asr": {
        "fast_model": "base",
        "final_model": "large-v3-turbo",
        "device": "auto",
        "fast_compute_type": None,
        "final_compute_type": None,
        "fast_cpu_threads": None,
        "final_cpu_threads": None,
    },
    "translation": {
        "base_url": "http://localhost:11434/v1",
        "fast_model": "qwen3.5:4b",
        "final_model": "qwen3.5:4b",
        "api_key_env": "KIKITORI_TRANSLATE_API_KEY",
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
    def __init__(self, writer: Writer) -> None:
        self.writer = writer
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        self.downloaded = {"tiny", "base"}
        self.downloading: set[str] = set()
        self.session: Session | None = None
        self.next_session_id = 1
        self.lock = threading.Lock()
        self.after_response: list[Callable[[], None]] = []
        self.methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "engine.info": self.engine_info,
            "hardware.detect": self.hardware_detect,
            "models.list": self.models_list,
            "models.download": self.models_download,
            "devices.list": lambda _p: {"devices": DEVICES},
            "settings.get": lambda _p: self.settings_result(),
            "settings.set": self.settings_set,
            "session.start": self.session_start,
            "session.stop": self.session_stop,
            "engine.shutdown": lambda _p: {},
        }

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

    def hardware_detect(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Shape of docs/specs/hardware.md (`hardware_report()`), before cuBLAS."""
        runtime = GPU_RUNTIME_ID in self.downloaded
        device = "cuda" if runtime else "cpu"
        compute = "float16" if runtime else "int8"
        threads = 0 if runtime else 4
        return {
            "hardware": {
                "os": "windows",
                "os_version": "Windows 11 (10.0.26200)",
                "machine": "AMD64",
                "cpu_count": 16,
                "ram_bytes": 32 * 1024**3,
                "gpus": [
                    {
                        "index": 0,
                        "name": "NVIDIA GeForce RTX 4060 (fake)",
                        "memory_total_mb": 8188,
                        "memory_used_mb": 900,
                    }
                ],
                "apple_silicon": False,
            },
            "recommendation": {
                "tier": "nvidia_8gb" if runtime else "cpu",
                "fast_asr": {
                    "model": "base",
                    "device": device,
                    "compute_type": compute,
                    "cpu_threads": threads,
                },
                "final_asr": {
                    "model": "large-v3-turbo" if runtime else "small",
                    "device": device,
                    "compute_type": compute,
                    "cpu_threads": threads,
                },
                "translation_fast": "qwen3.5:4b",
                "translation_final": "qwen3.5:4b",
                "gpu_runtime_missing": not runtime,
                "reasons": ["假引擎：依假的 8 GB 顯示卡推薦"],
                "warnings": []
                if runtime
                else ["cuBLAS 未安裝，GPU 加速需要先下載 GPU 加速元件"],
            },
        }

    def models_list(self, _params: dict[str, Any]) -> dict[str, Any]:
        """Shape of docs/specs/hardware.md (`models_overview()`)."""
        installed = {"qwen3.5:4b"}
        return {
            "asr": [
                {
                    **m,
                    "downloaded": m["id"] in self.downloaded,
                    "recommended_for": ["fast"] if m["id"] == "base" else [],
                }
                for m in ASR_MODELS
            ],
            "gpu_runtime": {
                "id": GPU_RUNTIME_ID,
                "supported": True,
                "installed": GPU_RUNTIME_ID in self.downloaded,
                "package": "nvidia-cublas-cu12",
                "download_bytes": GPU_RUNTIME_BYTES,
                "license": "NVIDIA CUDA Toolkit End User License Agreement",
                "license_url": "https://docs.nvidia.com/cuda/eula/index.html",
            },
            "translation": {
                "server": {
                    "base_url": self.settings["translation"]["base_url"],
                    "is_local": True,
                    "reachable": True,
                    "error": None,
                },
                "models": [
                    {
                        "name": name,
                        "label": label,
                        "installed": name in installed,
                        "known": True,
                        "recommended_for": ["fast", "final"]
                        if name == "qwen3.5:4b"
                        else [],
                        "license": "Apache-2.0",
                        "license_url": None,
                        "commercial_use": True,
                        "excluded_regions": [],
                    }
                    for name, label in (
                        ("qwen3.5:4b", "Qwen3.5 4B"),
                        ("qwen3.5:9b", "Qwen3.5 9B"),
                    )
                ],
            },
        }

    def models_download(self, params: dict[str, Any]) -> dict[str, Any]:
        model = params.get("model")
        sizes = {m["id"]: int(m["size_bytes"]) for m in ASR_MODELS}
        sizes[GPU_RUNTIME_ID] = GPU_RUNTIME_BYTES
        if not isinstance(model, str) or model not in sizes:
            raise ProtocolError("unknown_model", f"unknown model {model!r}")
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
    args = parser.parse_args()
    if args.garbage_every < 0 or args.crash_after < 0:
        parser.error("arguments must be non-negative")

    writer = Writer(args.garbage_every)
    engine = FakeEngine(writer)
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
    log("fake engine exiting")
    return 0


if __name__ == "__main__":
    sys.exit(main())
