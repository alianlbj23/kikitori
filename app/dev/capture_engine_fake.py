"""Captures the real engine's fake-mode answers as UI parser test data.

Role: `app/src/lib/testdata/engine-fake-nvidia.json` holds what the real engine
(`uv run kikitori engine` with `KIKITORI_ENGINE_FAKE=1`, fake 8 GB NVIDIA machine,
fake local Ollama) answers to the methods the UI parses, plus the events of a
lazy-download start (`translation_model_missing` → `models.pull_translation`
progress). The vitest parsers run against it, so a protocol change in the engine
shows up as a failing UI test instead of a broken window. Run it after the engine
protocol changes (`uv run --no-project python app/dev/capture_engine_fake.py`, or
item "重新擷取引擎測試資料" in `npm run menu`); it needs `uv` and the project
environment, and writes nothing but the JSON file.

Standard library only: it talks NDJSON to a child process started without a
shell, with a deadline on every answer, and a settings directory of its own.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
TARGET = REPO_ROOT / "app" / "src" / "lib" / "testdata" / "engine-fake-nvidia.json"
ANSWER_TIMEOUT_SECS = 120.0
EVENT_TIMEOUT_SECS = 60.0


class CaptureError(Exception):
    """The engine did not answer as expected."""


class Engine:
    """A child engine process with id-matched requests and an event queue."""

    def __init__(self, config_dir: str) -> None:
        env = {
            **os.environ,
            "KIKITORI_ENGINE_FAKE": "1",
            "KIKITORI_ENGINE_FAKE_HARDWARE": "nvidia",
            "KIKITORI_ENGINE_FAKE_TRANSLATION": "ollama",
            "KIKITORI_CONFIG_DIR": config_dir,
        }
        self._proc = subprocess.Popen(
            ["uv", "run", "kikitori", "engine"],
            cwd=REPO_ROOT,
            env=env,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        self._lines: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._events: list[dict[str, Any]] = []
        self._next_id = 1
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        assert self._proc.stdout is not None
        for raw in self._proc.stdout:
            try:
                self._lines.put(json.loads(raw.decode("utf-8")))
            except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                print(f"skipping a malformed engine line: {exc}", file=sys.stderr)
        self._lines.put(None)

    def _next_line(self, deadline: float) -> dict[str, Any]:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise CaptureError("timed out waiting for the engine")
        try:
            line = self._lines.get(timeout=remaining)
        except queue.Empty as exc:
            raise CaptureError("timed out waiting for the engine") from exc
        if line is None:
            raise CaptureError("the engine exited")
        return line

    def request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """Sends one request and returns its result; events seen meanwhile are kept."""
        request_id = self._next_id
        self._next_id += 1
        line = json.dumps({"id": request_id, "method": method, "params": params or {}})
        assert self._proc.stdin is not None
        self._proc.stdin.write(line.encode("utf-8") + b"\n")
        self._proc.stdin.flush()
        deadline = time.monotonic() + ANSWER_TIMEOUT_SECS
        while True:
            message = self._next_line(deadline)
            if "event" in message:
                self._events.append(message)
                continue
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise CaptureError(f"{method} failed: {message['error']}")
            return message["result"]

    def wait_event(self, name: str, done: Any) -> list[dict[str, Any]]:
        """Events named `name` (including earlier ones) up to the first for which `done(data)`."""
        deadline = time.monotonic() + EVENT_TIMEOUT_SECS
        seen: list[dict[str, Any]] = []
        pending = self._events
        self._events = []
        while True:
            message = pending.pop(0) if pending else self._next_line(deadline)
            if message.get("event") != name:
                continue
            seen.append(message["data"])
            if done(message["data"]):
                return seen

    def close(self) -> None:
        try:
            self.request("engine.shutdown")
        except CaptureError as exc:
            print(f"engine.shutdown: {exc}", file=sys.stderr)
        try:
            self._proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait(timeout=10)


def _without_temp_dir(value: Any, config_dir: str) -> Any:
    """Replaces the per-run settings directory with `C:/fake`, so captures diff cleanly."""
    if isinstance(value, str):
        return value.replace(config_dir, "C:/fake").replace("C:/fake\\", "C:/fake/")
    if isinstance(value, list):
        return [_without_temp_dir(v, config_dir) for v in value]
    if isinstance(value, dict):
        return {k: _without_temp_dir(v, config_dir) for k, v in value.items()}
    return value


def capture() -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="kikitori-capture-") as config_dir:
        engine = Engine(config_dir)
        try:
            out: dict[str, Any] = {
                "_comment": (
                    "Responses and events of the real engine in fake mode "
                    "(KIKITORI_ENGINE_FAKE=1, KIKITORI_ENGINE_FAKE_HARDWARE=nvidia, "
                    "KIKITORI_ENGINE_FAKE_TRANSLATION=ollama, empty KIKITORI_CONFIG_DIR, "
                    "requests in this order), written by app/dev/capture_engine_fake.py "
                    "for parser tests. Regenerate when the protocol changes."
                )
            }
            out["settings.get"] = engine.request("settings.get")
            out["hardware.detect"] = engine.request("hardware.detect")
            out["models.list"] = engine.request("models.list")
            out["settings.preview_recommendation"] = engine.request(
                "settings.preview_recommendation"
            )
            out["settings.apply_recommendation"] = engine.request(
                "settings.apply_recommendation"
            )
            out["languages.list"] = engine.request("languages.list")
            out["settings.preview_languages"] = engine.request(
                "settings.preview_languages",
                {"source_language": "ko", "target_language": "es"},
            )
            out["settings.preview_languages interpret"] = engine.request(
                "settings.preview_languages",
                {
                    "mode": "interpret",
                    "target_language": "zh-TW",
                    "partner_language": "en",
                },
            )
            out["models.validate"] = engine.request(
                "models.validate", {"settings": {"asr": {"fast_model": "medium"}}}
            )
            # A combination whose translation model is not installed on the fake
            # Ollama: session.start reports translation_model_missing, then the pull.
            engine.request(
                "settings.set",
                {
                    "translation": {
                        "fast_model": "qwen3.5:9b",
                        "final_model": "qwen3.5:9b",
                    }
                },
            )
            out["session.start"] = engine.request("session.start", {"mode": "media"})
            out["status events"] = engine.wait_event(
                "status", lambda d: d["state"] in ("error", "running")
            )
            out["models.pull_translation"] = engine.request(
                "models.pull_translation", {"model": "qwen3.5:9b"}
            )
            out["download_progress events"] = engine.wait_event(
                "download_progress", lambda d: d["state"] in ("done", "error")
            )
            out["session.set_translation"] = engine.request(
                "session.set_translation", {"enabled": False}
            )
        finally:
            engine.close()
        return _without_temp_dir(out, config_dir)


def main() -> int:
    try:
        data = capture()
    except (OSError, CaptureError) as exc:
        print(f"capture failed: {exc}", file=sys.stderr)
        return 1
    TARGET.write_text(
        json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {TARGET.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
