"""遠端運算的訊息格式（A ↔ B）：嚴格驗證的 JSON 控制訊息與二進位音訊訊框。

位於 remote 模組的協定層（docs/specs/remote-compute.md「協定」）。網路另一端送來的一切都是
不可信的輸入：每則訊息先檢查大小、型別與欄位（未知欄位一律拒絕），不合法就丟
`ProtocolViolation`，連線層據此關閉連線。這裡不碰網路，方便逐一測試錯誤路徑。

- 音訊訊框（A → B，二進位）：`>IQ` 標頭（序號 u32、起始樣本 u64）＋ 16 kHz 單聲道 int16 little-endian。
- `session.start` 帶的設定**只接受白名單**：語言、翻譯開關、術語表、原文校正、本場資訊、
  說話者標籤、切句時間，以及 ASR 模型的 id（`asr.fast_model`／`final_model`，
  docs/specs/compute-capacity.md「三」）。翻譯伺服器網址、翻譯模型、金鑰的環境變數、ASR 的
  裝置與精度、遠端設定都不能由 A 改（否則能讓 B 的引擎連到任意網址），一律用 B 自己的設定；
  ASR 模型只能是 B 的 registry 裡、而且 B 已下載的（由 `remote.server` 檢查，A 不能叫 B 下載）。
- `hello` 的 `intent`：`probe` 只查 B 的能力（`welcome.host`，見 `remote.capacity`），B 回完就
  關閉、不佔 GPU；省略＝`session`（舊版 A）。
- `welcome.host`（B 的能力）由 A 逐欄驗證（`parse_host_info`）：不合格整個當 null，連線照常。
"""

import json
import logging
import re
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal, cast

import numpy as np
import numpy.typing as npt

from kikitori.config import Settings, SettingsError
from kikitori.pipeline.types import MODES, Mode

logger = logging.getLogger(__name__)

PATH = "/kikitori/v1"
"""運算主機的 WebSocket 路徑（其他路徑一律 404）。"""

PROTOCOL_VERSION = 1
"""A 與 B 的協定版本；握手時不同就拒絕（`incompatible`）。"""

MAX_TEXT_BYTES = 512 * 1024
"""單則 JSON 訊息上限：術語表最多 500 筆、原文校正 200 筆，遠小於此。"""

SAMPLE_RATE = 16_000
MAX_AUDIO_FRAME_SAMPLES = SAMPLE_RATE  # 一則最多 1 秒；A 約每 100 ms 送一則
_AUDIO_HEADER = struct.Struct(">IQ")
_U32_MAX = 2**32 - 1
_MAX_NONCE = 2**53
_APP_VERSION = re.compile(r"^[0-9A-Za-z.+-]{1,32}$")

# A 能帶進 session 的設定（其餘一律用 B 的）。值為 None 表示整個欄位，集合表示只接受這些子欄位。
_SESSION_SETTINGS: dict[str, frozenset[str] | None] = {
    "source_language": None,
    "target_language": None,
    "partner_language": None,
    "translation": frozenset({"enabled", "session_info", "glossary"}),
    "speakers": frozenset({"enabled", "count"}),
    "media": frozenset({"pause_secs", "max_secs"}),
    "interpret": frozenset({"pause_secs", "max_secs"}),
    # 只有模型 id；裝置、精度、CUDA 函式庫位置仍用 B 的設定
    "asr": frozenset({"fast_model", "final_model"}),
}
_ASR_MODEL_KEYS = ("fast_model", "final_model")
_MODEL_ID = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
"""ASR 模型 id 的格式（與設定檔 `asr.*_model` 相同）；是否在 registry 裡由 B 判斷。"""

Intent = Literal["session", "probe"]
INTENTS: tuple[Intent, ...] = ("session", "probe")

# B 的能力（welcome.host）的上限：A 逐欄驗證，B 壞掉或惡意時不能塞進大量或怪異的資料
MAX_HOST_MODELS = 32
MAX_GPU_NAME_CHARS = 64
MAX_VRAM_MB = 1_048_576
MAX_HOST_TRANSLATION_MODEL_CHARS = 200
_COMPUTE_TYPE = re.compile(r"^[a-z0-9_]{1,32}$")
_HOST_DEVICES = ("cuda", "cpu")
_FITS = ("ok", "tight", "over")

_SERVER_EVENTS = frozenset({"subtitle", "metrics", "log"})
_STATES = frozenset({"loading", "running", "idle", "error"})
STATUS_CODES = frozenset(
    {"model_not_downloaded", "unknown_model", "load_error", "pipeline_error"}
)
"""B 的 `status error` 可能帶的 code；A 只轉換這些，其他一律當成不合法的訊息。"""
MAX_DETAIL_CHARS = 500
_MAX_STEP_CHARS = 64


class ProtocolViolation(ValueError):
    """對方送來不合法的訊息；連線層應關閉連線（不回傳細節以外的內容）。"""


# --------------------------------------------------------------------------- 音訊


@dataclass(frozen=True)
class AudioFrame:
    """一則音訊訊框。"""

    seq: int
    start_sample: int
    audio: npt.NDArray[np.int16]


def encode_audio(seq: int, start_sample: int, audio: npt.NDArray[np.int16]) -> bytes:
    """A 端：組一則音訊訊框。

    Raises:
        ValueError: 序號或起始樣本超出範圍、音訊不是一維 int16。
    """
    if not 0 <= seq <= _U32_MAX or start_sample < 0:
        raise ValueError(f"seq {seq} or start_sample {start_sample} out of range")
    if audio.ndim != 1 or audio.dtype != np.int16:
        raise ValueError(f"audio must be 1-D int16, got {audio.dtype} {audio.shape}")
    return _AUDIO_HEADER.pack(seq, start_sample) + audio.astype("<i2").tobytes()


def decode_audio(data: bytes) -> AudioFrame:
    """B 端：解一則音訊訊框（不檢查連續性，那是 session 的事）。

    Raises:
        ProtocolViolation: 太短、奇數位元組、沒有音訊或超過 1 秒。
    """
    header = _AUDIO_HEADER.size
    if len(data) <= header or (len(data) - header) % 2:
        raise ProtocolViolation(f"malformed audio frame of {len(data)} bytes")
    samples = (len(data) - header) // 2
    if samples > MAX_AUDIO_FRAME_SAMPLES:
        raise ProtocolViolation(f"audio frame of {samples} samples is too long")
    seq, start = _AUDIO_HEADER.unpack_from(data)
    audio = np.frombuffer(data, dtype="<i2", offset=header).astype(np.int16)
    return AudioFrame(seq, start, audio)


# --------------------------------------------------------------------------- A → B


@dataclass(frozen=True)
class Hello:
    """握手：A 的協定版本與 app 版本；`intent` 是 `probe` 時只查 B 的能力。"""

    protocol_version: int
    app_version: str
    intent: Intent = "session"


@dataclass(frozen=True)
class SessionStart:
    """開始一場：模式與白名單內的設定（`session_settings()` 合併進 B 的設定）。"""

    mode: Mode
    settings: dict[str, Any]


@dataclass(frozen=True)
class SessionStop:
    """結束這一場（A 的使用者按停止）。"""


@dataclass(frozen=True)
class EndAudio:
    """A 的聲音來源讀完了（例如音檔）：B 處理完剩下的音訊後回 `status idle`。"""


@dataclass(frozen=True)
class SetTranslation:
    """執行中開關翻譯（與本機的 `session.set_translation` 相同）。"""

    enabled: bool


@dataclass(frozen=True)
class SessionResume:
    """斷線後重連：接回 `session_id` 這一場，補送 `utterance_id > last_final_id` 的完整字幕。"""

    session_id: int
    last_final_id: int


@dataclass(frozen=True)
class Ping:
    """心跳；`nonce` 原樣在 `pong` 送回（A 用來量來回時間）。"""

    nonce: int


ClientMessage = (
    Hello
    | SessionStart
    | SessionStop
    | SessionResume
    | EndAudio
    | SetTranslation
    | Ping
)


def _load(text: str) -> dict[str, Any]:
    if len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ProtocolViolation("message is too large")
    try:
        data = json.loads(text)
    except (ValueError, RecursionError) as exc:  # 深層巢狀會丟 RecursionError
        raise ProtocolViolation("message is not JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("type"), str):
        raise ProtocolViolation("message must be an object with a string type")
    return data


def _is_one_of(value: object, allowed: frozenset[str] | tuple[str, ...]) -> bool:
    """`value in allowed`，但不合法的型別（list／dict 不能雜湊）不會丟 TypeError。"""
    return isinstance(value, str) and value in allowed


def _fields(data: dict[str, Any], allowed: tuple[str, ...]) -> None:
    unknown = set(data) - {"type", *allowed}
    if unknown:
        raise ProtocolViolation(f"{data['type']}: unknown field {min(unknown)!r}")


def _int(value: object, name: str, low: int, high: int) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not low <= value <= high
    ):
        raise ProtocolViolation(f"{name} must be an integer in [{low}, {high}]")
    return value


def _session_patch(value: object) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProtocolViolation("session.start settings must be an object")
    for key, sub in value.items():
        if key not in _SESSION_SETTINGS:
            raise ProtocolViolation(f"session.start may not set {key!r}")
        allowed = _SESSION_SETTINGS[key]
        if allowed is not None:
            if not isinstance(sub, dict):
                raise ProtocolViolation(f"session.start {key} must be an object")
            extra = set(sub) - allowed
            if extra:
                raise ProtocolViolation(f"session.start may not set {key}.{min(extra)}")
    for name, model in (value.get("asr") or {}).items():
        if not isinstance(model, str) or not _MODEL_ID.fullmatch(model):
            raise ProtocolViolation(f"session.start asr.{name} must be a model id")
    return value


def parse_client(text: str) -> ClientMessage:
    """B 端：解析並驗證 A 送來的一則文字訊息。

    Raises:
        ProtocolViolation: 不合法（太大、不是 JSON、未知型別或欄位、型別錯誤、設定不在白名單）。
    """
    data = _load(text)
    kind = data["type"]
    if kind == "hello":
        _fields(data, ("protocol_version", "app_version", "intent"))
        version = _int(data.get("protocol_version"), "protocol_version", 0, _U32_MAX)
        app = data.get("app_version")
        if not isinstance(app, str) or not _APP_VERSION.fullmatch(app):
            raise ProtocolViolation("app_version must be a short version string")
        intent = data.get("intent", "session")
        if not _is_one_of(intent, INTENTS):
            raise ProtocolViolation(f"intent must be one of {list(INTENTS)}")
        return Hello(version, app, cast(Intent, intent))
    if kind == "session.start":
        _fields(data, ("mode", "settings"))
        mode = data.get("mode")
        if not _is_one_of(mode, MODES):
            raise ProtocolViolation(f"mode must be one of {list(MODES)}")
        return SessionStart(mode, _session_patch(data.get("settings")))
    if kind == "session.stop":
        _fields(data, ())
        return SessionStop()
    if kind == "session.resume":
        _fields(data, ("session_id", "last_final_id"))
        return SessionResume(
            _int(data.get("session_id"), "session_id", 1, _MAX_NONCE),
            _int(data.get("last_final_id"), "last_final_id", 0, _MAX_NONCE),
        )
    if kind == "session.end_audio":
        _fields(data, ())
        return EndAudio()
    if kind == "session.set_translation":
        _fields(data, ("enabled",))
        enabled = data.get("enabled")
        if not isinstance(enabled, bool):
            raise ProtocolViolation("enabled must be true or false")
        return SetTranslation(enabled)
    if kind == "ping":
        _fields(data, ("nonce",))
        return Ping(_int(data.get("nonce"), "nonce", 0, _MAX_NONCE))
    raise ProtocolViolation(f"unknown message type {kind[:40]!r}")


def requested_models(start: SessionStart) -> dict[str, str]:
    """A 指定的 ASR 模型（`{"fast_model"?: id, "final_model"?: id}`；沒指定時空的）。

    格式已由 `parse_client` 驗證；是否在 registry 裡、B 是否已下載由 B 判斷。
    """
    asr = start.settings.get("asr") or {}
    return {key: str(asr[key]) for key in _ASR_MODEL_KEYS if key in asr}


def session_settings(base: Settings, start: SessionStart) -> Settings:
    """B 的設定加上 A 帶來的白名單設定（照本機設定的規則驗證）。

    Raises:
        ProtocolViolation: 合併後不合法（例如未知語言）。
    """
    try:
        return base.merged(start.settings)
    except SettingsError as exc:
        raise ProtocolViolation(f"session.start settings: {exc}") from exc


def client_hello(app_version: str, intent: Intent = "session") -> str:
    """A 端：握手訊息。`session` 不送 `intent`（省略＝session），舊版 B 才不會拒絕。"""
    message: dict[str, Any] = {
        "type": "hello",
        "protocol_version": PROTOCOL_VERSION,
        "app_version": app_version,
    }
    if intent != "session":
        message["intent"] = intent
    return json.dumps(message)


def client_session_start(
    mode: Mode, settings: Settings, *, fast_model: str = "", final_model: str = ""
) -> str:
    """A 端：開始一場，帶上這台電腦的白名單設定。

    Args:
        mode: session 模式。
        settings: A 的設定（只送白名單內的欄位）。
        fast_model: 要 B 用的即時辨識模型；空字串＝照 B 的設定（不送）。
        final_model: 要 B 用的完整辨識模型；同上。
    """
    full = settings.to_dict()
    patch: dict[str, Any] = {}
    for key, allowed in _SESSION_SETTINGS.items():
        if key == "asr":
            continue  # 不送 A 自己的 ASR 設定；只送使用者為遠端選的模型（下面）
        value = full[key]
        patch[key] = (
            value if allowed is None else {k: value[k] for k in sorted(allowed)}
        )
    # 都沒選時整個不送：舊版 B 不認得 `asr`，會當成不合法的設定
    asr = {
        key: model
        for key, model in (("fast_model", fast_model), ("final_model", final_model))
        if model
    }
    if asr:
        patch["asr"] = asr
    return json.dumps(
        {"type": "session.start", "mode": mode, "settings": patch}, ensure_ascii=False
    )


def client_session_stop() -> str:
    """A 端：使用者按停止。"""
    return json.dumps({"type": "session.stop"})


def client_end_audio() -> str:
    """A 端：聲音來源讀完了。"""
    return json.dumps({"type": "session.end_audio"})


def client_set_translation(enabled: bool) -> str:
    """A 端：執行中開關翻譯。"""
    return json.dumps({"type": "session.set_translation", "enabled": enabled})


def client_session_resume(session_id: int, last_final_id: int) -> str:
    """A 端：重連後接回這一場。"""
    return json.dumps(
        {
            "type": "session.resume",
            "session_id": session_id,
            "last_final_id": last_final_id,
        }
    )


def client_ping(nonce: int) -> str:
    """A 端：心跳。"""
    return json.dumps({"type": "ping", "nonce": nonce})


# --------------------------------------------------------------------------- B → A


def welcome(
    app_version: str,
    models: dict[str, str],
    host: Mapping[str, Any] | None = None,
) -> str:
    """握手成功；`models` 是 B 會用的模型（給 A 顯示），`host` 是 B 的能力（`HostInfo`）。"""
    return json.dumps(
        {
            "type": "welcome",
            "protocol_version": PROTOCOL_VERSION,
            "app_version": app_version,
            "models": models,
            "host": None if host is None else dict(host),
        },
        ensure_ascii=False,
    )


def busy(reason: str) -> str:
    """B 正在服務別人或自己在跑字幕。"""
    return json.dumps({"type": "busy", "reason": reason[:200]})


def incompatible(reason: str) -> str:
    """協定版本不同。"""
    return json.dumps(
        {
            "type": "incompatible",
            "protocol_version": PROTOCOL_VERSION,
            "reason": reason[:200],
        }
    )


def status(
    state: str, detail: str | None, code: str | None, data: dict[str, Any] | None = None
) -> str:
    """session 狀態（與本機引擎的 `status` 事件相同欄位）。"""
    return json.dumps(
        {
            "type": "status",
            "state": state,
            # 錯誤說明可能很長（例外訊息）：截斷，A 端才不會因為超過上限而斷線
            "detail": None if detail is None else detail[:MAX_DETAIL_CHARS],
            "code": code,
            "data": data,
        },
        ensure_ascii=False,
    )


def event(name: str, data: dict[str, Any]) -> str:
    """轉送 pipeline 的事件（subtitle／metrics／log），欄位與本機引擎事件相同。"""
    return json.dumps({"type": "event", "name": name, "data": data}, ensure_ascii=False)


def session_started(session_id: int) -> str:
    """B 接受了 `session.start`；`session_id` 是續接時要帶的編號。"""
    return json.dumps({"type": "session.started", "session_id": session_id})


def resumed(session_id: int, sample: int, state: str) -> str:
    """續接成功：B 實際收到 A 時間軸上 `sample` 個樣本，A 從那裡補送。"""
    return json.dumps(
        {"type": "resumed", "session_id": session_id, "sample": sample, "state": state}
    )


def resume_failed(reason: str) -> str:
    """續接失敗（保留時間已過、不是同一台電腦、編號不符）：A 改成連線中斷。"""
    return json.dumps({"type": "resume_failed", "reason": reason[:200]})


def ack(sample: int) -> str:
    """B 已收到 A 時間軸上到 `sample` 為止的音訊（斷線續傳用）。"""
    return json.dumps({"type": "ack", "sample": sample})


def pong(nonce: int) -> str:
    """心跳回應。"""
    return json.dumps({"type": "pong", "nonce": nonce})


def _check_status(data: dict[str, Any]) -> None:
    _fields(data, ("state", "detail", "code", "data"))
    if not _is_one_of(data.get("state"), _STATES):
        raise ProtocolViolation("status.state is not a known state")
    detail = data.get("detail")
    if detail is not None and (
        not isinstance(detail, str) or len(detail) > MAX_DETAIL_CHARS
    ):
        raise ProtocolViolation("status.detail must be a short string or null")
    code = data.get("code")
    if code is not None and not _is_one_of(code, STATUS_CODES):
        raise ProtocolViolation("status.code is not a known code")
    progress = data.get("data")
    if progress is None:
        return
    if not isinstance(progress, dict) or set(progress) != {"step", "done", "total"}:
        raise ProtocolViolation("status.data must be {step, done, total}")
    step = progress["step"]
    if not isinstance(step, str) or not 0 < len(step) <= _MAX_STEP_CHARS:
        raise ProtocolViolation("status.data.step must be a short string")
    total = _int(progress["total"], "status.data.total", 0, 1000)
    _int(progress["done"], "status.data.done", 0, total)


class _BadHost(ValueError):
    """`welcome.host` 的某個欄位不合格（整個 host 當 null）。"""


def _host_text(value: object, name: str, pattern: re.Pattern[str]) -> str:
    if not isinstance(value, str) or not pattern.fullmatch(value):
        raise _BadHost(f"{name} is not valid")
    return value


def _host_int(value: object, name: str, high: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= high:
        raise _BadHost(f"{name} must be an integer in [0, {high}]")
    return value


def _host_object(value: object, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise _BadHost(f"{name} must be an object")
    return value


def _host_pair(value: object, name: str) -> dict[str, str] | None:
    """`current`／`recommended`：`{fast, final}` 兩個模型 id。"""
    if value is None:
        return None
    pair = _host_object(value, name)
    return {
        key: _host_text(pair.get(key), f"{name}.{key}", _MODEL_ID)
        for key in ("fast", "final")
    }


def _host_gpu(value: object) -> dict[str, Any] | None:
    if value is None:
        return None
    gpu = _host_object(value, "gpu")
    name = gpu.get("name")
    if (
        not isinstance(name, str)
        or not 0 < len(name) <= MAX_GPU_NAME_CHARS
        or not name.isprintable()
    ):
        raise _BadHost(f"gpu.name must be 1-{MAX_GPU_NAME_CHARS} printable characters")
    return {
        "name": name,
        "vram_total_mb": _host_int(
            gpu.get("vram_total_mb"), "gpu.vram_total_mb", MAX_VRAM_MB
        ),
    }


def _host_models(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > MAX_HOST_MODELS:
        raise _BadHost(f"models must be a list of at most {MAX_HOST_MODELS}")
    models: list[dict[str, Any]] = []
    seen: set[str] = set()
    for i, raw in enumerate(value):
        entry = _host_object(raw, f"models[{i}]")
        model_id = _host_text(entry.get("id"), f"models[{i}].id", _MODEL_ID)
        if model_id in seen:
            raise _BadHost(f"models[{i}].id is repeated")
        seen.add(model_id)
        downloaded = entry.get("downloaded")
        if not isinstance(downloaded, bool):
            raise _BadHost(f"models[{i}].downloaded must be true or false")
        fit = entry.get("fit")
        if fit is not None and not _is_one_of(fit, _FITS):
            raise _BadHost(f"models[{i}].fit must be one of {list(_FITS)} or null")
        models.append(
            {
                "id": model_id,
                "downloaded": downloaded,
                "need_mb": _host_int(
                    entry.get("need_mb"), f"models[{i}].need_mb", MAX_VRAM_MB
                ),
                "fit": fit,
            }
        )
    return models


def _host_translation_model(value: object) -> str | None:
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > MAX_HOST_TRANSLATION_MODEL_CHARS
        or not value.isprintable()
    ):
        raise _BadHost("translation_model must be a short printable string")
    return value


def parse_host_info(value: object) -> dict[str, Any] | None:
    """A 端：驗證 B 送來的 `HostInfo`，只留認得的欄位；不合格（或沒有）時 None。

    不合格不中斷連線：能力只是給使用者參考的資訊，舊版或壞掉的 B 照樣能跑字幕。
    """
    if value is None:
        return None
    try:
        host = _host_object(value, "host")
        device = host.get("device")
        if not _is_one_of(device, _HOST_DEVICES):
            raise _BadHost(f"device must be one of {list(_HOST_DEVICES)}")
        current = _host_pair(host.get("current"), "current")
        if current is None:
            raise _BadHost("current is required")
        return {
            "gpu": _host_gpu(host.get("gpu")),
            "device": device,
            "compute_type": _host_text(
                host.get("compute_type"), "compute_type", _COMPUTE_TYPE
            ),
            "current": current,
            "recommended": _host_pair(host.get("recommended"), "recommended"),
            "models": _host_models(host.get("models")),
            "translation_model": _host_translation_model(host.get("translation_model")),
        }
    except _BadHost as exc:
        logger.warning("ignoring the compute host's capabilities: %s", exc)
        return None


def parse_server(text: str) -> dict[str, Any]:
    """A 端：驗證 B 送來的一則訊息的外形（事件內容再由現有的事件驗證處理）。

    Raises:
        ProtocolViolation: 不合法。
    """
    data = _load(text)
    kind = data["type"]
    if kind == "welcome":
        _int(data.get("protocol_version"), "protocol_version", 0, _U32_MAX)
        if not isinstance(data.get("models"), dict):
            raise ProtocolViolation("welcome.models must be an object")
        data["host"] = parse_host_info(data.get("host"))
    elif kind == "session.started":
        _fields(data, ("session_id",))
        _int(data.get("session_id"), "session_id", 1, _MAX_NONCE)
    elif kind == "resumed":
        _fields(data, ("session_id", "sample", "state"))
        _int(data.get("session_id"), "session_id", 1, _MAX_NONCE)
        _int(data.get("sample"), "sample", 0, 2**63 - 1)
        if not _is_one_of(data.get("state"), _STATES):
            raise ProtocolViolation("resumed.state is not a known state")
    elif kind in ("busy", "incompatible", "resume_failed"):
        if not isinstance(data.get("reason"), str):
            raise ProtocolViolation(f"{kind}.reason must be a string")
    elif kind == "status":
        _check_status(data)
    elif kind == "event":
        if not _is_one_of(data.get("name"), _SERVER_EVENTS) or not isinstance(
            data.get("data"), dict
        ):
            raise ProtocolViolation(
                "event must name subtitle, metrics or log and carry an object"
            )
    elif kind == "ack":
        _int(data.get("sample"), "sample", 0, 2**63 - 1)
    elif kind == "pong":
        _int(data.get("nonce"), "nonce", 0, _MAX_NONCE)
    else:
        raise ProtocolViolation(f"unknown message type {kind[:40]!r}")
    return data
