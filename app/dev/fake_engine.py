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

Milestones 12–14 (languages, lazy downloads, original-only subtitles) are
imitated the same way, mirroring `src/kikitori/engine/testing.py`: messages are
`{code, params, message}` objects, `languages.list` and
`settings.preview_languages` answer from the language table below,
`session.start` reports `translation_model_missing` (with `data.models`) before
`model_not_downloaded`, `models.pull_translation` "downloads" with
`download_progress` events of `kind: "translation"` (the total grows as Ollama's
does), and `session.set_translation` switches a running session.
`--translation-server` picks the fake translation server: `ollama` (default),
`openai` (reachable but not Ollama: `pull_unsupported`, no model check at start),
`offline` (`translation_server_unreachable`) or `auth` (rejects the API key:
`translation_server_auth_failed`). The rules are the real engine's; this is only
a shape-correct stand-in.

The competitive features (docs/specs/competitive-features.md) are imitated too:
partial subtitles carry `stable_chars` (the previous partial's length, as
LocalAgreement-2 would confirm it) and finals `latency_ms`; a `metrics` event
(`level_dbfs`, `backlog`, `latency_ms_median`) follows every second while a
session runs, with `--metrics {normal,silent,backlog}` choosing whether the
input sounds normal, stays below -60 dBFS (the "no audio" hint) or lets the
backlog grow past 3 (the "can't keep up" warning); `models.list` reports
`disk_bytes` and `models.delete` frees a downloaded ASR model unless the
running session or a download uses it (`unknown_model`, `model_in_use`,
`model_not_downloaded`, checked in the engine's order); `settings.set`
validates `translation.glossary` with the engine's limits and error paths
(`translation.glossary.entries[3].source: ...`).

Furigana (docs/specs/furigana.md) is imitated too: `models.list` has a
`furigana` object (not downloaded at start), `models.download
{"model": "furigana-dictionary"}` reports `download_progress` with
`kind: "furigana"`, `models.delete` frees it unless furigana is on in a running
session, `session.set_furigana` refuses `model_not_downloaded` until then, and
Japanese subtitles carry `ruby` from the small reading table below while it is on.

Point the app at it with
    KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
or use `npm run menu` in app/.

Fault injection (for exercising the bridge): `--garbage-every N` writes an invalid
line after every N subtitle events; `--crash-after SECONDS` exits with status 3;
`--ignore-shutdown` never answers `engine.shutdown` (an engine stuck mid-inference).
Pacing (for memory soaks at the real engine's event rate): `--partial-steps N`
partials per sentence, `--partial-interval SECONDS` apart.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import platform
import queue
import re
import statistics
import sys
import threading
import time
import unicodedata
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
# Readings of the kanji words in the canned Japanese lines: word → spans
# `(start, end, hiragana)` within the word. The real engine uses Sudachi.
FURIGANA_WORDS: dict[str, list[tuple[int, int, str]]] = {
    "今日": [(0, 2, "きょう")],
    "寒": [(0, 1, "さむ")],
    "散歩": [(0, 2, "さんぽ")],
    "行": [(0, 1, "い")],
    "駅": [(0, 1, "えき")],
    "前": [(0, 1, "まえ")],
    "新": [(0, 1, "あたら")],
    "屋": [(0, 1, "や")],
    "昨日": [(0, 2, "きのう")],
    "同": [(0, 1, "おな")],
    "言": [(0, 1, "い")],
    "急": [(0, 1, "いそ")],
    "大丈夫": [(0, 3, "だいじょうぶ")],
    "二つ目": [(0, 1, "ふた"), (2, 3, "め")],
    "角": [(0, 1, "かど")],
    "右": [(0, 1, "みぎ")],
    "曲": [(0, 1, "ま")],
    "五分": [(0, 2, "ごふん")],
    "思": [(0, 1, "おも")],
}
FURIGANA_ID = "furigana-dictionary"
FURIGANA_DOWNLOAD_BYTES = 72_238_136
FURIGANA_INSTALLED_BYTES = 217_391_698


def furigana(text: str) -> list[list[Any]]:
    """`ruby` for `text`: longest known word at each position (code points, like the engine)."""
    spans: list[list[Any]] = []
    i = 0
    words = sorted(FURIGANA_WORDS, key=len, reverse=True)
    while i < len(text):
        word = next((w for w in words if text.startswith(w, i)), None)
        if word is None:
            i += 1
            continue
        spans += [[i + a, i + b, r] for a, b, r in FURIGANA_WORDS[word]]
        i += len(word)
    return spans


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

# Subtitle language code -> Whisper language code. Mirrors the language list in
# src/kikitori/languages/catalog.py (this script is stdlib-only and cannot import it).
LANGUAGE_WHISPER: dict[str, str] = {
    "ja": "ja",
    "en": "en",
    "zh-TW": "zh",
    "zh-CN": "zh",
    "ko": "ko",
    "es": "es",
    "fr": "fr",
    "de": "de",
    "pt": "pt",
    "ru": "ru",
    "it": "it",
    "ar": "ar",
    "hi": "hi",
    "id": "id",
    "vi": "vi",
    "th": "th",
    "tr": "tr",
    "pl": "pl",
    "nl": "nl",
    "uk": "uk",
}
# The canned lines above are translated for the default languages only.
CANNED_TARGETS = ("zh-TW", "ja")

# (code, english_name, native_name, rtl, no_space) in the engine's order; mirrors
# src/kikitori/languages/catalog.py.
LANGUAGES: list[tuple[str, str, str, bool, bool]] = [
    ("ja", "Japanese", "日本語", False, True),
    ("en", "English", "English", False, False),
    ("zh-TW", "Chinese (Traditional, Taiwan)", "繁體中文", False, True),
    ("zh-CN", "Chinese (Simplified)", "简体中文", False, True),
    ("ko", "Korean", "한국어", False, False),
    ("es", "Spanish", "Español", False, False),
    ("fr", "French", "Français", False, False),
    ("de", "German", "Deutsch", False, False),
    ("pt", "Portuguese", "Português", False, False),
    ("ru", "Russian", "Русский", False, False),
    ("it", "Italian", "Italiano", False, False),
    ("ar", "Arabic", "العربية", True, False),
    ("hi", "Hindi", "हिन्दी", False, False),
    ("id", "Indonesian", "Bahasa Indonesia", False, False),
    ("vi", "Vietnamese", "Tiếng Việt", False, False),
    ("th", "Thai", "ไทย", False, True),
    ("tr", "Turkish", "Türkçe", False, False),
    ("pl", "Polish", "Polski", False, False),
    ("nl", "Dutch", "Nederlands", False, False),
    ("uk", "Ukrainian", "Українська", False, False),
]
# ASR quality grade per model, in LANGUAGES order (catalog.py `_QUALITY_ROWS`).
ASR_QUALITY: dict[str, str] = {
    "large-v3": "BAABAABAAAABBACABAAB",
    "large-v3-turbo": "BBBBAABAAAACCACCBAAB",
    "medium": "BABBBABAAAACCBCBBAAB",
    "small": "BACCBABBABACCBCCBBBB",
    "base": "CACCCACBBCBCCCCCCCCC",
    "tiny": "CBCCCBCCCCCCCCCCCCCC",
}
# Translation presets the fake knows (size in GB of weights, languages it supports).
TRANSLATION_SIZES_GB: dict[str, float] = {
    "qwen3.5:4b": 3.3,
    "qwen3.5:9b": 6.6,
    "hy-mt1.5-7b:latest": 4.6,
    "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS": 4.3,
}
# A pull of this name fails halfway, like `FAKE_BROKEN_PULL` in testing.py.
BROKEN_PULL = "fake-broken-pull:1b"
PULL_BYTES = 4 * 1024 * 1024
TRANSLATION_SERVER_PROFILES = ("ollama", "openai", "offline", "auth")
# `--metrics`: what the fake input level and backlog look like.
METRICS_PROFILES = ("normal", "silent", "backlog")
METRICS_INTERVAL_SECS = 1.0
# `--partial-steps` / `--partial-interval`: how often partials arrive. The
# defaults (4 partials, 0.55 s apart) suit UI work. The real engine sent 4299
# partials for 217 sentences in 20 minutes (~20 per sentence, ~3.6 per second;
# experiments/memory_soak/README.md, scenario 4); memory soaks of the app use
# 20 partials 0.2 s apart, ~3.4 per second with the final's pauses.
DEFAULT_PARTIAL_STEPS = 4
DEFAULT_PARTIAL_INTERVAL_SECS = 0.55
# Engine limits of `translation.glossary` (src/kikitori/translate/glossary.py).
GLOSSARY_MAX_ENTRIES = 500
GLOSSARY_MAX_SOURCE_CHARS = 100
GLOSSARY_MAX_TARGET_CHARS = 200
GLOSSARY_ENTRY_KEYS = ("source", "target", "target_language")
TRANSLATION_MODEL_NAME = re.compile(r"(?!.*\.\.)[A-Za-z0-9][A-Za-z0-9._:/-]{0,199}")


def message(code: str, english: str, **params: Any) -> dict[str, Any]:
    """A structured engine message (hardware.md「訊息碼」); `english` is the fallback template."""
    text = english.format(**params)
    return {"code": code, "params": params, "message": text}


def asr_quality(model: str, language: str) -> str | None:
    row = ASR_QUALITY.get(model)
    codes = [code for code, *_ in LANGUAGES]
    if row is None or language not in codes:
        return None
    return row[codes.index(language)]


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

# `installed` here is the starting state; FakeEngine tracks it as pulls complete.
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
            "Sakura-GalTransl 7B v3",
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
    "schema_version": 3,
    "mode": "media",
    "source_language": "ja",
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
        "enabled": True,
        "base_url": "http://localhost:11434/v1",
        "fast_model": "qwen3.5:4b",
        "final_model": "qwen3.5:4b",
        "api_key_env": "KIKITORI_TRANSLATE_API_KEY",
        "allow_restricted": False,
        "keep_warm_secs": 240,
        "glossary": {"builtin": True, "entries": []},
    },
    "pipeline": {
        "partial_interval_ms": 700,
        "no_speech_threshold": 0.6,
        "context_lines": 3,
        "asr_prompt_context": True,
    },
    "furigana": {"enabled": False},
    "subtitle": {},
    "ui": {},
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


def _invalid(message: str) -> ProtocolError:
    return ProtocolError("invalid_settings", message)


def _glossary_key(text: str) -> str:
    """The engine's matching key: NFKC + casefold + strip (`glossary.match_key`)."""
    return unicodedata.normalize("NFKC", text).casefold().strip()


def _check_glossary_text(path: str, name: str, value: object, limit: int) -> None:
    """The engine's `_check_text`, with the same error path and wording."""
    where = f"{path}.{name}"
    if not isinstance(value, str):
        raise _invalid(f"{where}: {name} must be a string, got {type(value).__name__}")
    if not value.strip():
        raise _invalid(f"{where}: {name} must not be empty")
    if value != value.strip():
        raise _invalid(f"{where}: {name} must not start or end with whitespace")
    if len(value) > limit:
        raise _invalid(f"{where}: {name} must be at most {limit} characters")
    if not value.isprintable():
        raise _invalid(
            f"{where}: {name} must not contain control characters or line breaks"
        )


def validate_glossary(glossary: object) -> None:
    """`translation.glossary` like the engine's `_parse_glossary` (limits, duplicates, paths)."""
    path = "translation.glossary"
    if not isinstance(glossary, dict):
        raise _invalid(f"{path}: expected an object")
    unknown = set(glossary) - {"builtin", "entries"}
    if unknown:
        raise _invalid(f"{path}.{min(unknown)}: unknown field")
    if not isinstance(glossary.get("builtin", True), bool):
        raise _invalid(f"{path}.builtin: expected a boolean")
    entries = glossary.get("entries", [])
    if not isinstance(entries, list) or len(entries) > GLOSSARY_MAX_ENTRIES:
        raise _invalid(
            f"{path}.entries: expected a list of at most {GLOSSARY_MAX_ENTRIES} entries"
        )
    seen: set[tuple[str, str | None]] = set()
    for i, item in enumerate(entries):
        entry_path = f"{path}.entries[{i}]"
        if not isinstance(item, dict):
            raise _invalid(
                f"{entry_path}: expected an object, got {type(item).__name__}"
            )
        extra = set(item) - set(GLOSSARY_ENTRY_KEYS)
        if extra:
            raise _invalid(f"{entry_path}.{min(extra)}: unknown field")
        for name in ("source", "target"):
            if name not in item:
                raise _invalid(f"{entry_path}.{name}: missing")
        language = item.get("target_language")
        if language is not None and language not in LANGUAGE_WHISPER:
            raise _invalid(
                f"{entry_path}.target_language: unsupported language code {language!r}"
            )
        _check_glossary_text(
            entry_path, "source", item["source"], GLOSSARY_MAX_SOURCE_CHARS
        )
        _check_glossary_text(
            entry_path, "target", item["target"], GLOSSARY_MAX_TARGET_CHARS
        )
        key = (_glossary_key(item["source"]), language)
        if not key[0]:
            raise _invalid(
                f"{entry_path}.source: source must contain at least one visible character"
            )
        if key in seen:
            raise _invalid(
                f"{entry_path}.source: duplicate of an earlier entry "
                f"({item['source']!r} for target_language {language!r})"
            )
        seen.add(key)


def validate_settings(settings: dict[str, Any]) -> None:
    """A small subset of the real engine's checks, enough to exercise UI errors."""
    unknown = set(settings) - set(DEFAULT_SETTINGS)
    if unknown:
        raise ProtocolError("invalid_settings", f"{min(unknown)}: unknown field")
    if settings["mode"] not in ("media", "interpret"):
        raise ProtocolError("invalid_settings", "mode: expected 'media' or 'interpret'")
    if settings["source_language"] != "auto" and (
        settings["source_language"] not in LANGUAGE_WHISPER
    ):
        raise ProtocolError(
            "invalid_settings", "source_language: unsupported language code"
        )
    for key in ("target_language", "partner_language"):
        if settings[key] not in LANGUAGE_WHISPER:
            raise ProtocolError("invalid_settings", f"{key}: unsupported language code")
    # Same rule as the real engine: only interpret mode needs distinct languages.
    if settings["mode"] == "interpret" and (
        LANGUAGE_WHISPER[settings["partner_language"]]
        == LANGUAGE_WHISPER[settings["target_language"]]
    ):
        raise ProtocolError(
            "invalid_settings",
            "partner_language: interpret mode needs a different language from "
            "target_language",
        )
    if not isinstance(settings["translation"].get("enabled"), bool):
        raise ProtocolError(
            "invalid_settings", "translation.enabled: expected a boolean"
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
    validate_glossary(settings["translation"].get("glossary"))
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
    furigana_settings = settings.get("furigana", {})
    if not isinstance(furigana_settings, dict) or set(furigana_settings) - {"enabled"}:
        raise ProtocolError("invalid_settings", "furigana: only 'enabled' is allowed")
    if not isinstance(furigana_settings.get("enabled", False), bool):
        raise ProtocolError(
            "invalid_settings", "furigana.enabled: expected true or false"
        )
    for key in ("subtitle", "ui"):
        if not isinstance(settings[key], dict):
            raise ProtocolError("invalid_settings", f"{key}: expected an object")


class Session(threading.Thread):
    """Emits loading → running → subtitle stream until stopped."""

    def __init__(
        self,
        session_id: int,
        mode: str,
        writer: Writer,
        settings: dict[str, Any] | None = None,
        metrics_profile: str = "normal",
        partial_steps: int = DEFAULT_PARTIAL_STEPS,
        partial_interval: float = DEFAULT_PARTIAL_INTERVAL_SECS,
    ) -> None:
        super().__init__(name=f"session-{session_id}", daemon=True)
        self.session_id = session_id
        self.mode = mode
        self.writer = writer
        self.metrics_profile = metrics_profile
        self.partial_steps = partial_steps
        self.partial_interval = partial_interval
        settings = settings or DEFAULT_SETTINGS
        # The models this session loads: `models.delete` refuses them while it runs.
        self.asr_models = frozenset(
            (settings["asr"]["fast_model"], settings["asr"]["final_model"])
        )
        # Written by the session thread, read by the metrics thread (plain
        # assignments of immutable values, so no lock is needed).
        self.speaking = False
        self.latencies: tuple[int, ...] = ()
        self.running_since = 0.0
        self.target = settings["target_language"]
        self.partner = settings["partner_language"]
        # Read by the session thread for each sentence; `session.set_translation`
        # flips it (a bool assignment, so no lock is needed).
        self.translation_enabled: bool = settings["translation"]["enabled"]
        # `session.set_furigana` flips it; ruby needs the dictionary too.
        self.furigana_enabled: bool = settings.get("furigana", {}).get("enabled", False)
        self.furigana_installed = False
        self.stop_event = threading.Event()
        # Checked before "loading" ends, like the real engine's translation-model
        # and ASR checks: (code, detail, data) of a start that must fail.
        self.start_error: tuple[str, str, dict[str, Any] | None] | None = None

    def status(
        self,
        state: str,
        detail: str | None = None,
        code: str | None = None,
        data: dict[str, Any] | None = None,
    ) -> None:
        payload: dict[str, Any] = {
            "state": state,
            "detail": detail,
            "session_id": self.session_id,
            "code": code,
        }
        if data is not None:
            payload["data"] = data
        self.writer.event("status", payload)

    def run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            # Report like the real engine does (status error) instead of dying silently.
            log(f"session {self.session_id} failed: {exc!r}")
            self.status("error", f"{type(exc).__name__}: {exc}")
            raise

    def _run(self) -> None:
        self.status("loading")
        if self.start_error is not None:
            code, detail, data = self.start_error
            if self.stop_event.wait(0.3):
                self.status("idle")
                return
            self.status("error", detail, code, data)
            return
        if self.stop_event.wait(1.0):
            self.status("idle")
            return
        self.status("running")
        self.running_since = time.monotonic()
        metrics = threading.Thread(
            target=self._report_metrics, name=f"metrics-{self.session_id}", daemon=True
        )
        metrics.start()
        try:
            self._subtitles()
        finally:
            # Like the engine: no metrics after the session's terminal status.
            self.stop_event.set()
            metrics.join(timeout=2)
        self.status("idle")

    def _subtitles(self) -> None:
        lines = MEDIA_LINES if self.mode == "media" else INTERPRET_LINES
        position = SAMPLE_RATE  # first utterance starts after 1 s
        utterance_id = 0
        while not self.stop_event.is_set():
            source, original, canned = lines[utterance_id % len(lines)]
            translation = self._translation(source, original, canned)
            utterance_id += 1
            start = position
            if not self._speak(utterance_id, source, original, translation, start):
                break
            position = start + SAMPLE_RATE * 3
            if self.stop_event.wait(1.2):
                break

    def _target_language(self, source: str) -> str:
        if self.mode == "interpret" and source == LANGUAGE_WHISPER[self.target]:
            return self.partner
        return self.target

    def _translation(self, source: str, original: str, canned: str) -> str:
        """Canned text for the default languages; a labelled echo otherwise."""
        if not self.translation_enabled:
            return ""
        target = self._target_language(source)
        if LANGUAGE_WHISPER[target] == source:
            return original
        if target in CANNED_TARGETS:
            return canned
        return f"[{target}] {original}"

    def _speak(
        self,
        utterance_id: int,
        source: str,
        original: str,
        translation: str,
        start: int,
    ) -> bool:
        """Partial results grow through the sentence, then the final replaces them.

        Each partial's `stable_chars` is the previous partial's length: the
        prefix two consecutive hypotheses agree on (LocalAgreement-2).
        """
        steps = self.partial_steps
        stable = 0
        self.speaking = True
        try:
            for i in range(1, steps + 1):
                if self.stop_event.wait(self.partial_interval):
                    return False
                cut_o = max(1, len(original) * i // (steps + 1))
                cut_t = len(translation) * i // (steps + 1)
                cut_t = max(1, cut_t) if translation else 0
                self._emit(
                    utterance_id,
                    source,
                    original[:cut_o],
                    translation[:cut_t],
                    False,
                    start,
                    i,
                    stable_chars=stable,
                )
                stable = cut_o
        finally:
            self.speaking = False
        if self.stop_event.wait(0.6):
            return False
        latency = 600 + (utterance_id * 137) % 900
        self.latencies = (*self.latencies, latency)[-10:]
        self._emit(
            utterance_id,
            source,
            original,
            translation,
            True,
            start,
            steps + 1,
            latency_ms=latency,
        )
        return True

    def _report_metrics(self) -> None:
        """One `metrics` event per second while running (see `--metrics`)."""
        tick = 0
        while not self.stop_event.wait(METRICS_INTERVAL_SECS):
            tick += 1
            if self.metrics_profile == "silent":
                level = -90.0
            elif self.speaking:
                level = -20.0 - (tick % 5) * 1.5
            else:
                level = -47.0 + (tick % 3)
            if self.metrics_profile == "backlog":
                # Grows by one every 3 s up to 6: past the warning threshold (3).
                backlog = min(6, int(time.monotonic() - self.running_since) // 3)
            else:
                backlog = 1 if self.speaking and tick % 4 == 0 else 0
            latencies = self.latencies
            self.writer.event(
                "metrics",
                {
                    "session_id": self.session_id,
                    "level_dbfs": level,
                    "backlog": backlog,
                    "latency_ms_median": int(statistics.median(latencies))
                    if latencies
                    else None,
                },
            )

    def _emit(
        self,
        utterance_id: int,
        source: str,
        original: str,
        translation: str,
        is_final: bool,
        start: int,
        step: int,
        stable_chars: int = 0,
        latency_ms: int | None = None,
    ) -> None:
        data: dict[str, Any] = {
            "utterance_id": utterance_id,
            "original": original,
            "translation": translation,
            "source_language": source,
            "target_language": self._target_language(source),
            "is_final": is_final,
            "start_sample": start,
            "end_sample": start + int(SAMPLE_RATE * 0.5 * step),
            "session_id": self.session_id,
        }
        # Like the engine: partials carry only stable_chars, finals only latency_ms.
        if is_final:
            data["latency_ms"] = latency_ms
        else:
            data["stable_chars"] = stable_chars
        if self.furigana_enabled and self.furigana_installed and source == "ja":
            data["ruby"] = furigana(original)
        self.writer.event("subtitle", data)


class FakeEngine:
    def __init__(
        self,
        writer: Writer,
        ignore_shutdown: bool = False,
        translation_server: str = "ollama",
        metrics_profile: str = "normal",
        partial_steps: int = DEFAULT_PARTIAL_STEPS,
        partial_interval: float = DEFAULT_PARTIAL_INTERVAL_SECS,
    ) -> None:
        self.writer = writer
        self.translation_server = translation_server
        self.metrics_profile = metrics_profile
        self.partial_steps = partial_steps
        self.partial_interval = partial_interval
        self.translation_installed = {
            m["name"] for m in TRANSLATION_MODELS if m["installed"]
        }
        self.pulling: set[str] = set()
        # Imitates an engine stuck mid-inference: `engine.shutdown` is never
        # answered, so the app has to fall back to killing the process.
        self.ignore_shutdown = ignore_shutdown
        self.settings = copy.deepcopy(DEFAULT_SETTINGS)
        # Settings live in memory: "first run" until the first settings.set.
        self.is_new = True
        self.downloaded = {"tiny", "base", "small"}
        self.furigana_installed = False
        self.downloading: set[str] = set()
        self.session: Session | None = None
        self.next_session_id = 1
        self.lock = threading.Lock()
        self.after_response: list[Callable[[], None]] = []
        self.after_background: list[Callable[[], None]] = []
        self.methods: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "engine.info": self.engine_info,
            "models.download": self.models_download,
            # Synchronous like the engine's: no session or download can start in between.
            "models.delete": self.models_delete,
            "devices.list": lambda _p: {"devices": DEVICES},
            "settings.get": lambda _p: self.settings_result(),
            "settings.set": self.settings_set,
            "session.start": self.session_start,
            "session.stop": self.session_stop,
            "session.set_translation": self.session_set_translation,
            "session.set_furigana": self.session_set_furigana,
            "languages.list": self.languages_list,
            "engine.shutdown": lambda _p: {},
        }
        # Like the real engine, these run on one worker thread in arrival order
        # and answer when done, i.e. possibly after requests sent later.
        self.background: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
            "hardware.detect": self.hardware_detect,
            "models.list": self.models_list,
            "models.validate": self.models_validate,
            "settings.apply_recommendation": self.settings_apply_recommendation,
            "settings.preview_recommendation": self.settings_preview_recommendation,
            "settings.preview_languages": self.settings_preview_languages,
            "models.pull_translation": self.models_pull_translation,
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
                self.after_background.clear()
                self.writer.send(
                    {
                        "id": request_id,
                        "error": {"code": exc.code, "message": exc.message},
                    }
                )
                continue
            self.writer.send({"id": request_id, "result": result})
            # Like the engine's `Deferred.after`: work that emits events starts
            # only after its answer is written.
            for action in self.after_background:
                action()
            self.after_background.clear()

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

    def _translation_needed(self, settings: dict[str, Any]) -> bool:
        """False for "original only" and for media mode with one spoken language."""
        if not settings["translation"]["enabled"]:
            return False
        if settings["mode"] == "interpret" or settings["source_language"] == "auto":
            return True
        return (
            LANGUAGE_WHISPER[settings["source_language"]]
            != LANGUAGE_WHISPER[settings["target_language"]]
        )

    def _languages(self, settings: dict[str, Any]) -> dict[str, Any]:
        return {
            key: settings[key]
            for key in (
                "mode",
                "source_language",
                "target_language",
                "partner_language",
            )
        }

    def recommendation(self, settings: dict[str, Any] | None = None) -> dict[str, Any]:
        """A fixed 8 GB NVIDIA machine; CPU combination until the runtime is installed.

        Depends on the languages like the real recommendation: same spoken
        language in media mode → no translation model; C-grade recognition of a
        listening language → a warning per language.
        """
        settings = settings or self.settings
        if self._runtime_installed():
            fast = {"model": "small", "device": "cuda", "compute_type": "float16"}
            final = {
                "model": "large-v3-turbo",
                "device": "cuda",
                "compute_type": "float16",
            }
            threads, tier, budget = 0, "nvidia_8gb", 4.9
            warnings: list[dict[str, Any]] = []
            reasons = [
                message(
                    "asr_gpu",
                    "{gpu_name} ({vram_mb} MiB VRAM): {fast_model} for live subtitles, "
                    "{final_model} for final subtitles ({compute_type})",
                    gpu_name="NVIDIA GeForce RTX 4060 (fake)",
                    vram_mb=8188,
                    fast_model="small",
                    final_model="large-v3-turbo",
                    compute_type="float16",
                )
            ]
        else:
            fast = {"model": "base", "device": "cpu", "compute_type": "int8"}
            final = {"model": "small", "device": "cpu", "compute_type": "int8"}
            threads, tier, budget = 4, "cpu", 7.0
            reasons = [
                message(
                    "asr_cpu",
                    "Speech recognition runs on the CPU (16 GB of RAM or more): "
                    "{fast_model} for live subtitles, {final_model} for final subtitles",
                    fast_model="base",
                    final_model="small",
                )
            ]
            warnings = [
                message(
                    "gpu_runtime_missing",
                    "cuBLAS is not installed: GPU acceleration needs the GPU runtime "
                    "download (about 570 MB); until then speech recognition uses the CPU",
                )
            ]
        heard = (
            [settings["target_language"], settings["partner_language"]]
            if settings["mode"] == "interpret"
            else []
            if settings["source_language"] == "auto"
            else [settings["source_language"]]
        )
        for language in heard:
            if asr_quality(final["model"], language) == "C":
                warnings.append(
                    message(
                        "asr_language_quality_low",
                        "{model} recognizes {language} poorly (error rate above 20% on "
                        "public benchmarks)",
                        model=final["model"],
                        language=language,
                    )
                )
        needed = (
            settings["mode"] == "interpret"
            or settings["source_language"] == "auto"
            or (
                LANGUAGE_WHISPER[settings["source_language"]]
                != LANGUAGE_WHISPER[settings["target_language"]]
            )
        )
        if needed:
            status, model = "recommended", "qwen3.5:4b"
            reasons.append(
                message(
                    "translation_budget_vram",
                    "About {budget_gb:.1f} GB of VRAM is left for the translation model "
                    "after speech recognition and a 1 GB reserve",
                    budget_gb=budget,
                )
            )
        else:
            status, model = "not_needed", None
            reasons.append(
                message(
                    "translation_not_needed",
                    "{source_language} and {target_language} are the same spoken "
                    "language: no translation model is needed, only script conversion",
                    source_language=settings["source_language"],
                    target_language=settings["target_language"],
                )
            )
        return {
            "tier": tier,
            "fast_asr": {**fast, "cpu_threads": threads},
            "final_asr": {**final, "cpu_threads": threads},
            "translation_fast": model,
            "translation_final": model,
            "translation_budget_gb": budget,
            "gpu_runtime_missing": not self._runtime_installed(),
            "reasons": reasons,
            "warnings": warnings,
            "translation_status": status,
            "languages": self._languages(settings),
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
                    "reachable": self._server_reachable(),
                    "models": sorted(self.translation_installed)
                    if self._server_reachable()
                    else None,
                    "error": None
                    if self._server_reachable()
                    else "fake translation server is offline",
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
                    "disk_bytes": self._disk_bytes(m["id"]),
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
            "furigana": self._furigana_status(),
            "translation": {
                "server": {
                    "base_url": self.settings["translation"]["base_url"],
                    "is_local": True,
                    "reachable": self._server_reachable(),
                    "error": None
                    if self._server_reachable()
                    else "fake translation server is offline",
                },
                "models": [
                    {
                        **m,
                        "installed": m["name"] in self.translation_installed
                        if self._server_reachable()
                        else None,
                    }
                    for m in TRANSLATION_MODELS
                ],
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
        errors: list[dict[str, Any]] = []
        warnings: list[dict[str, Any]] = []
        ids = [m["id"] for m in ASR_MODELS]
        for role in ("fast", "final"):
            model = asr[f"{role}_model"]
            path = f"{role}_asr"
            if model not in ids:
                errors.append(
                    message(
                        "asr_model_unknown",
                        "{path}: unknown speech recognition model {model} (available: {available})",
                        path=path,
                        model=model,
                        available=", ".join(ids),
                    )
                )
            elif model not in self.downloaded:
                warnings.append(
                    message(
                        "asr_model_not_downloaded",
                        "{path}: speech recognition model {model} is not downloaded yet; "
                        "it is downloaded before starting",
                        path=path,
                        model=model,
                    )
                )
        if asr["device"] == "cuda" and not self._runtime_installed():
            errors.append(
                message(
                    "asr_cuda_cublas_missing",
                    "{path}: CUDA is selected but cuBLAS is not installed; download the "
                    "GPU runtime first",
                    path="fast_asr",
                )
            )
        for name in sorted({tr["fast_model"], tr["final_model"]}):
            info = next((m for m in TRANSLATION_MODELS if m["name"] == name), None)
            if name not in self.translation_installed:
                warnings.append(
                    message(
                        "translation_model_not_on_server",
                        "The translation server does not have {model}",
                        model=name,
                    )
                )
            elif info and info["excluded_regions"] and not tr["allow_restricted"]:
                warnings.append(
                    message(
                        "translation_license_region_restricted",
                        "The license of {model} ({license}) does not apply in {regions}",
                        model=name,
                        license=info["license"],
                        regions=",".join(info["excluded_regions"]),
                    )
                )
            elif info and info["commercial_use"] is False:
                warnings.append(
                    message(
                        "translation_license_non_commercial",
                        "The license of {model} ({license}) forbids commercial use",
                        model=name,
                        license=info["license"],
                    )
                )
        return {"ok": not errors, "errors": errors, "warnings": warnings}

    def recommendation_patch(self, rec: dict[str, Any]) -> dict[str, Any]:
        fast, final = rec["fast_asr"], rec["final_asr"]
        return {
            "asr": {
                "fast_model": fast["model"],
                "final_model": final["model"],
                "device": fast["device"],
                "fast_compute_type": fast["compute_type"],
                "final_compute_type": final["compute_type"],
                "fast_cpu_threads": fast["cpu_threads"] or None,
                "final_cpu_threads": final["cpu_threads"] or None,
            },
            # Not recommended (not needed / unsupported): the models are left as they are.
            **(
                {
                    "translation": {
                        "fast_model": rec["translation_fast"],
                        "final_model": rec["translation_final"],
                    }
                }
                if rec["translation_fast"] is not None
                else {}
            ),
        }

    def settings_apply_recommendation(self, params: dict[str, Any]) -> dict[str, Any]:
        only_if_new = params.get("only_if_new", False)
        if not isinstance(only_if_new, bool):
            raise ProtocolError("invalid_params", "only_if_new must be a boolean")
        rec = self.recommendation()
        if only_if_new and not self.is_new:
            return {**self.settings_result(), "applied": None, "recommendation": rec}
        applied = self.recommendation_patch(rec)
        self.settings_set(applied)
        return {**self.settings_result(), "applied": applied, "recommendation": rec}

    def settings_preview_recommendation(
        self, _params: dict[str, Any]
    ) -> dict[str, Any]:
        rec = self.recommendation()
        patch = self.recommendation_patch(rec)
        return {
            "patch": patch,
            "recommendation": rec,
            "differs": deep_merge(self.settings, patch) != self.settings,
            "fingerprint": self._fingerprint(patch, rec["languages"]),
        }

    def _fingerprint(self, patch: dict[str, Any], languages: dict[str, Any]) -> str:
        """Like the engine: changes with the patch and with the languages."""
        text = json.dumps(
            {"patch": patch, "languages": languages},
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        )
        return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]

    def _server_reachable(self) -> bool:
        return self.translation_server != "offline"

    def languages_list(self, params: dict[str, Any]) -> dict[str, Any]:
        if params:
            raise ProtocolError("invalid_params", "languages.list takes no parameters")
        return {
            "languages": [
                {
                    "code": code,
                    "english_name": english,
                    "native_name": native,
                    "whisper_code": LANGUAGE_WHISPER[code],
                    "rtl": rtl,
                    "no_space": no_space,
                    "asr_quality": {
                        model: row[i] for model, row in ASR_QUALITY.items()
                    },
                }
                for i, (code, english, native, rtl, no_space) in enumerate(LANGUAGES)
            ]
        }

    def _preview_settings(self, params: dict[str, Any]) -> dict[str, Any]:
        """Current settings with the requested languages, validated (invalid_* errors)."""
        allowed = {"source_language", "target_language", "partner_language", "mode"}
        unknown = set(params) - allowed
        if unknown:
            raise ProtocolError("invalid_params", f"unknown parameter {min(unknown)}")
        if "mode" in params and params["mode"] not in ("media", "interpret"):
            raise ProtocolError(
                "invalid_params", "mode: expected 'media' or 'interpret'"
            )
        merged = deep_merge(self.settings, params)
        validate_settings(merged)
        return merged

    def settings_preview_languages(self, params: dict[str, Any]) -> dict[str, Any]:
        """What these languages would use and download; saves and downloads nothing."""
        settings = params.get("_settings") or self._preview_settings(params)
        rec = self.recommendation(settings)
        patch = self.recommendation_patch(rec)
        preview = deep_merge(settings, patch)
        sizes = {m["id"]: int(m["size_bytes"]) for m in ASR_MODELS}
        asr_roles: dict[str, list[str]] = {}
        for role in ("fast", "final"):
            asr_roles.setdefault(preview["asr"][f"{role}_model"], []).append(role)
        asr = [
            {
                "model": model,
                "roles": roles,
                "installed": model in self.downloaded,
                "size_bytes": sizes.get(model),
                "download_bytes": 0 if model in self.downloaded else sizes.get(model),
            }
            for model, roles in asr_roles.items()
        ]
        translation: list[dict[str, Any]] = []
        needed = self._translation_needed(settings)
        if needed:
            tr_roles: dict[str, list[str]] = {}
            for role in ("fast", "final"):
                tr_roles.setdefault(preview["translation"][f"{role}_model"], []).append(
                    role
                )
            for model, roles in tr_roles.items():
                gb = TRANSLATION_SIZES_GB.get(model)
                size = None if gb is None else int(gb * 1e9)
                installed = (
                    model in self.translation_installed
                    if self._server_reachable()
                    else None
                )
                translation.append(
                    {
                        "model": model,
                        "roles": roles,
                        "installed": installed,
                        "size_bytes": size,
                        "size_approximate": True,
                        "download_bytes": 0 if installed else size,
                    }
                )
        missing = [m for m in asr + translation if m["installed"] is not True]
        messages = (
            []
            if settings["translation"]["enabled"]
            else [
                message(
                    "translation_disabled",
                    "Translation is off: only the original text is shown and no "
                    "translation model is needed",
                )
            ]
        )
        return {
            "languages": self._languages(settings),
            "recommendation": rec,
            "patch": patch,
            "settings": preview,
            "differs": preview != self.settings,
            "fingerprint": self._fingerprint(patch, rec["languages"]),
            "models": {"asr": asr, "translation": translation},
            "translation_needed": needed,
            "download_bytes": sum(m["download_bytes"] or 0 for m in missing),
            "download_size_known": all(
                m["download_bytes"] is not None for m in missing
            ),
            "messages": messages,
        }

    def models_pull_translation(self, params: dict[str, Any]) -> dict[str, Any]:
        """Ollama `/api/pull` stand-in; errors depend on `--translation-server`."""
        model = params.get("model")
        if (
            set(params) != {"model"}
            or not isinstance(model, str)
            or not TRANSLATION_MODEL_NAME.fullmatch(model)
        ):
            raise ProtocolError(
                "invalid_params", "model: expected a translation model name"
            )
        base_url = self.settings["translation"]["base_url"]
        if self.translation_server == "offline":
            raise ProtocolError(
                "translation_server_unreachable",
                f"cannot connect to the translation server {base_url}",
            )
        if self.translation_server == "auth":
            raise ProtocolError(
                "translation_server_auth_failed",
                f"the translation server {base_url} rejected the API key (HTTP 401)",
            )
        if self.translation_server == "openai":
            raise ProtocolError(
                "pull_unsupported",
                f"the translation server {base_url} is not Ollama; install {model} there yourself",
            )
        with self.lock:
            if model in self.pulling:
                raise ProtocolError(
                    "download_in_progress", f"{model} is already downloading"
                )
            self.pulling.add(model)
        thread = threading.Thread(target=self._pull, args=(model,), daemon=True)
        self.after_background.append(thread.start)
        return {"model": model, "started": True}

    def _pull(self, model: str) -> None:
        """Four steps like testing.py; the total grows as Ollama reports more layers."""

        def event(done: int, total: int, state: str, error: str | None = None) -> None:
            self.writer.event(
                "download_progress",
                {
                    "model": model,
                    "kind": "translation",
                    "downloaded_bytes": done,
                    "total_bytes": total,
                    "state": state,
                    "error": error,
                },
            )

        event(0, 0, "downloading")
        step = PULL_BYTES // 4
        for i in range(1, 5):
            time.sleep(0.4)
            if model == BROKEN_PULL and i == 3:
                with self.lock:
                    self.pulling.discard(model)
                event(
                    step * 2,
                    PULL_BYTES,
                    "error",
                    "PullError: Ollama: simulated pull failure",
                )
                return
            event(step * i, PULL_BYTES if i > 1 else step * 2, "downloading")
        with self.lock:
            self.pulling.discard(model)
            self.translation_installed.add(model)
        event(PULL_BYTES, PULL_BYTES, "done")

    def _disk_bytes(self, model: str) -> int | None:
        """What a downloaded model takes on disk (a little over the download size)."""
        if model not in self.downloaded:
            return None
        size = next(int(m["size_bytes"]) for m in ASR_MODELS if m["id"] == model)
        return size + size // 100

    def _furigana_in_use(self) -> bool:
        session = self.session
        return bool(
            session is not None
            and session.is_alive()
            and session.furigana_enabled
            and self.furigana_installed
        )

    def _furigana_status(self) -> dict[str, Any]:
        installed = self.furigana_installed
        return {
            "id": FURIGANA_ID,
            "installed": installed,
            "edition": "core",
            "version": "20260428",
            "download_bytes": FURIGANA_DOWNLOAD_BYTES,
            "installed_bytes": FURIGANA_INSTALLED_BYTES,
            "disk_bytes": FURIGANA_INSTALLED_BYTES + 421 if installed else None,
            "path": "C:/fake/furigana/system_core.dic" if installed else None,
            "license": "Apache-2.0 (SudachiDict; includes UniDic BSD-3-Clause and NEologd notices)",
            "license_url": "https://github.com/WorksApplications/SudachiDict/blob/v20260428/LEGAL",
            "in_use": self._furigana_in_use(),
        }

    def session_set_furigana(self, params: dict[str, Any]) -> dict[str, Any]:
        """Furigana switch: saved and applied at once; needs the dictionary to turn on."""
        enabled = params.get("enabled")
        if set(params) != {"enabled"} or not isinstance(enabled, bool):
            raise ProtocolError("invalid_params", "enabled must be true or false")
        if enabled and not self.furigana_installed:
            raise ProtocolError(
                "model_not_downloaded",
                f"the furigana dictionary is not downloaded; download it with "
                f"models.download (model {FURIGANA_ID}) first",
            )
        self.settings_set({"furigana": {"enabled": enabled}})
        with self.lock:
            session = self.session
        session_id = None
        if session is not None and session.is_alive():
            session.furigana_enabled = enabled
            session_id = session.session_id
        return {**self.settings_result(), "session_id": session_id}

    def models_delete(self, params: dict[str, Any]) -> dict[str, Any]:
        """Deletes a downloaded ASR model; the engine's checks in the engine's order."""
        if set(params) - {"model"}:
            raise ProtocolError(
                "invalid_params", f"unknown parameter {min(set(params) - {'model'})!r}"
            )
        model = params.get("model")
        if not isinstance(model, str) or not model:
            raise ProtocolError("invalid_params", "model must be a non-empty string")
        if model == FURIGANA_ID:
            with self.lock:
                if FURIGANA_ID in self.downloading or self._furigana_in_use():
                    raise ProtocolError(
                        "model_in_use",
                        "the furigana dictionary is open; turn furigana off first",
                    )
                if not self.furigana_installed:
                    raise ProtocolError(
                        "model_not_downloaded",
                        "the furigana dictionary is not installed",
                    )
                self.furigana_installed = False
            return {"model": FURIGANA_ID, "freed_bytes": FURIGANA_INSTALLED_BYTES + 421}
        if model not in {m["id"] for m in ASR_MODELS}:
            raise ProtocolError("unknown_model", f"unknown ASR model {model!r}")
        with self.lock:
            session = self.session
            users = []
            if (
                session is not None
                and session.is_alive()
                and model in session.asr_models
            ):
                users.append(f"session {session.session_id}")
            if model in self.downloading:
                users.append("a download")
            if users:
                raise ProtocolError(
                    "model_in_use",
                    f"ASR model {model} is in use by {', '.join(users)}; stop it first",
                )
            freed = self._disk_bytes(model)
            if freed is None:
                raise ProtocolError(
                    "model_not_downloaded", f"ASR model {model} is not downloaded"
                )
            self.downloaded.discard(model)
        log(f"deleted {model} ({freed} bytes)")
        return {"model": model, "freed_bytes": freed}

    def models_download(self, params: dict[str, Any]) -> dict[str, Any]:
        model = params.get("model")
        sizes = {m["id"]: int(m["size_bytes"]) for m in ASR_MODELS}
        sizes[GPU_RUNTIME_ID] = GPU_RUNTIME_BYTES
        sizes[FURIGANA_ID] = FURIGANA_DOWNLOAD_BYTES
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
        kind = (
            "gpu_runtime"
            if model == GPU_RUNTIME_ID
            else "furigana"
            if model == FURIGANA_ID
            else "asr"
        )
        for i in range(steps + 1):
            self.writer.event(
                "download_progress",
                {
                    "model": model,
                    "kind": kind,
                    "downloaded_bytes": total * i // steps,
                    "total_bytes": total,
                    "state": "downloading",
                    "error": None,
                },
            )
            time.sleep(0.15)
        with self.lock:
            self.downloading.discard(model)
            if model == FURIGANA_ID:
                self.furigana_installed = True
                if self.session is not None:
                    self.session.furigana_installed = True
            else:
                self.downloaded.add(model)
        self.writer.event(
            "download_progress",
            {
                "model": model,
                "kind": kind,
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
            "is_new": self.is_new,
        }

    def settings_set(self, params: dict[str, Any]) -> dict[str, Any]:
        merged = deep_merge(self.settings, params)
        validate_settings(merged)
        self.settings = merged
        self.is_new = False
        return self.settings_result()

    def session_start(self, params: dict[str, Any]) -> dict[str, Any]:
        mode = params.get("mode")
        if mode not in ("media", "interpret"):
            raise ProtocolError(
                "invalid_params", "mode: expected 'media' or 'interpret'"
            )
        if mode == "interpret":
            # The saved settings may come from media mode, where this is allowed.
            validate_settings({**self.settings, "mode": "interpret"})
        device_id = params.get("device_id")
        if device_id is not None and device_id not in {d["id"] for d in DEVICES}:
            raise ProtocolError("device_not_found", f"no such device: {device_id!r}")
        with self.lock:
            if self.session is not None and self.session.is_alive():
                raise ProtocolError("session_active", "a session is already running")
            settings = copy.deepcopy(self.settings)
            session = Session(
                self.next_session_id,
                mode,
                self.writer,
                settings,
                metrics_profile=self.metrics_profile,
                partial_steps=self.partial_steps,
                partial_interval=self.partial_interval,
            )
            session.start_error = self._start_error({**settings, "mode": mode})
            session.furigana_installed = self.furigana_installed
            self.next_session_id += 1
            self.session = session
        self.after_response.append(session.start)
        return {"session_id": session.session_id}

    def _start_error(
        self, settings: dict[str, Any]
    ) -> tuple[str, str, dict[str, Any] | None] | None:
        """The engine's order: translation models (Ollama only), then ASR models."""
        if self._translation_needed(settings) and self.translation_server == "ollama":
            tr = settings["translation"]
            missing = list(
                dict.fromkeys(
                    m
                    for m in (tr["fast_model"], tr["final_model"])
                    if m not in self.translation_installed
                )
            )
            if missing:
                return (
                    "translation_model_missing",
                    (
                        "translation model(s) not installed on the Ollama server: "
                        f"{', '.join(missing)}; download them with models.pull_translation"
                    ),
                    {"models": missing},
                )
        for role in ("fast", "final"):
            model = settings["asr"][f"{role}_model"]
            if model not in self.downloaded:
                return (
                    "model_not_downloaded",
                    f"speech recognition model {model} is not downloaded",
                    None,
                )
        return None

    def session_set_translation(self, params: dict[str, Any]) -> dict[str, Any]:
        """The "show translation" switch: saved, and applied to a running session."""
        enabled = params.get("enabled")
        if set(params) != {"enabled"} or not isinstance(enabled, bool):
            raise ProtocolError("invalid_params", "enabled: expected a boolean")
        self.settings_set({"translation": {"enabled": enabled}})
        with self.lock:
            session = self.session
        session_id = None
        if session is not None and session.is_alive():
            session.translation_enabled = enabled
            session_id = session.session_id
        return {**self.settings_result(), "session_id": session_id}

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
                if method == "settings.preview_languages":
                    # Like the engine: parameter and settings errors are answered at
                    # once, and the settings are the ones current on receipt.
                    params = {"_settings": self._preview_settings(params)}
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
    parser.add_argument(
        "--translation-server",
        choices=TRANSLATION_SERVER_PROFILES,
        default="ollama",
        help="fake translation server: ollama (pulls work), openai (pull_unsupported), "
        "offline (translation_server_unreachable), auth (translation_server_auth_failed)",
    )
    parser.add_argument(
        "--metrics",
        choices=METRICS_PROFILES,
        default="normal",
        help="fake input: normal, silent (below -60 dBFS: the no-audio hint) or "
        "backlog (grows past 3 sentences: the can't-keep-up warning)",
    )
    parser.add_argument(
        "--partial-steps",
        type=int,
        default=DEFAULT_PARTIAL_STEPS,
        metavar="N",
        help=f"partials per sentence (default {DEFAULT_PARTIAL_STEPS}; 1-100)",
    )
    parser.add_argument(
        "--partial-interval",
        type=float,
        default=DEFAULT_PARTIAL_INTERVAL_SECS,
        metavar="SECONDS",
        help=f"seconds between partials (default {DEFAULT_PARTIAL_INTERVAL_SECS}; 0.05-5)",
    )
    args = parser.parse_args()
    if args.garbage_every < 0 or args.crash_after < 0:
        parser.error("arguments must be non-negative")
    if not 1 <= args.partial_steps <= 100:
        parser.error("--partial-steps must be between 1 and 100")
    if not 0.05 <= args.partial_interval <= 5:
        parser.error("--partial-interval must be between 0.05 and 5 seconds")

    writer = Writer(args.garbage_every)
    engine = FakeEngine(
        writer,
        ignore_shutdown=args.ignore_shutdown,
        translation_server=args.translation_server,
        metrics_profile=args.metrics,
        partial_steps=args.partial_steps,
        partial_interval=args.partial_interval,
    )
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
