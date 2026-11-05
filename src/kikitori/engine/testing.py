"""引擎的假模式服務：不需要音效卡、模型或網路就能跑完整個協定。

給子行程測試與 UI 開發使用（環境變數 `KIKITORI_ENGINE_FAKE=1`）。合成音訊以
方波代表「人聲」，依振幅判斷的 VoiceModel 讓切句結果固定；ASR 與翻譯回傳可預測
的文字。設定檔仍用真實的 `SettingsStore`（測試以 `KIKITORI_CONFIG_DIR` 指到暫存目錄）。
"""

import dataclasses
import hashlib
import io
import logging
import os
import tempfile
import threading
import time
import zipfile
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

import numpy as np
import numpy.typing as npt

from kikitori.asr.types import Transcript
from kikitori.capture import ArraySource, DeviceInfo, DeviceKind
from kikitori.capture.errors import DeviceNotFoundError
from kikitori.capture.types import AudioSource
from kikitori.config import Settings, SettingsStore, default_settings_path
from kikitori.engine.services import (
    EngineServices,
    ProgressCallback,
    TranslationServerState,
)
from kikitori.hardware import (
    CUBLAS_VERSION,
    CpuInfo,
    CublasInfo,
    GpuInfo,
    GpuRuntimeError,
    GpuRuntimeStatus,
    HardwareInfo,
    MemoryInfo,
    ModelCombination,
    TranslationServerInfo,
    gpu_runtime_status,
    install_gpu_runtime,
    installed_runtime_dir,
    models_overview,
)
from kikitori.hardware.gpu_runtime import (
    CUBLAS_PACKAGE,
    LICENSE_NAME,
    LICENSE_URL,
    RuntimeWheel,
    WheelMember,
)
from kikitori.languages import WHISPER_CODES, get_language
from kikitori.pipeline import (
    Mode,
    PipelineComponents,
    default_transcript_filter,
)
from kikitori.pipeline.factory import AsrModelInfo, UnknownModelError
from kikitori.translate.presets import normalize_model_name
from kikitori.translate.types import TranslationRequest

SAMPLE_RATE = 16000
SPEECH_DEVICE = "fake:loopback:speech"
ENDLESS_DEVICE = "fake:microphone:endless"
FAKE_MODEL_SIZE = 4 * 1024 * 1024

FAKE_INSTALLED_TRANSLATION_MODELS = ["qwen3.5:4b"]
FAKE_PULL_SIZE = 4 * 1024 * 1024
FAKE_BROKEN_PULL = "fake-broken-pull:1b"
"""假 Ollama 下載到一半失敗的模型名稱。"""
FAKE_SLOW_PULL = "fake-slow-pull:1b"
"""假 Ollama 下載很慢的模型名稱（測試結束引擎時的取消）。"""
GIB = 1024**3

logger = logging.getLogger(__name__)


def _speech_track() -> npt.NDArray[np.int16]:
    # 0.5 s 靜音、1.5 s「人聲」、1 s 靜音、1.2 s「人聲」、1 s 靜音 → 兩句。
    def part(secs: float, value: int) -> npt.NDArray[np.int16]:
        return np.full(round(secs * SAMPLE_RATE), value, dtype=np.int16)

    return np.concatenate(
        [part(0.5, 0), part(1.5, 5000), part(1.0, 0), part(1.2, 5000), part(1.0, 0)]
    )


class AmplitudeVoiceModel:
    """振幅大於 1000 就當人聲。"""

    def probability(self, window: npt.NDArray[np.int16]) -> float:
        """方波段回傳 1.0，靜音回傳 0.0。"""
        return 1.0 if int(np.abs(window.astype(np.int32)).max()) > 1000 else 0.0

    def reset(self) -> None:
        """沒有內部狀態。"""


class FakeTranscriber:
    """回傳 `"<prefix><第幾次呼叫>"`，語言固定。"""

    def __init__(self, prefix: str, language: str) -> None:
        self._prefix = prefix
        self._language = language
        self._count = 0
        self._lock = threading.Lock()

    def transcribe(
        self, audio: np.ndarray, language: str | None = None, prompt: str | None = None
    ) -> Transcript:
        """不看音訊內容，只計數。"""
        with self._lock:
            self._count += 1
            count = self._count
        return Transcript(f"{self._prefix}{count}", self._language, 1.0, 0.01, -0.1)


class FakeTranslator:
    """回傳 `"[<譯文語言>] <原文>"`；串流分兩段產出。"""

    def translate(self, request: TranslationRequest) -> str:
        """一次回傳完整譯文。"""
        return f"[{request.target_language}] {request.text}"

    def stream(self, request: TranslationRequest) -> Iterator[str]:
        """先產出前半，再產出完整譯文。"""
        full = self.translate(request)
        yield full[: len(full) // 2]
        yield full


class EndlessSource:
    """像沒有聲音的麥克風：每次 read 等到逾時、回傳空陣列，直到 stop()。"""

    def __init__(self) -> None:
        self._stopped = threading.Event()

    def start(self) -> None:
        """沒有裝置要開。"""

    def read(self, timeout: float) -> npt.NDArray[np.int16]:
        """等待 `timeout` 秒（stop() 會提早喚醒），回傳空陣列。"""
        self._stopped.wait(timeout)
        return np.zeros(0, dtype=np.int16)

    def stop(self) -> None:
        """喚醒等待中的 read。"""
        self._stopped.set()


FAKE_DEVICES = [
    DeviceInfo(SPEECH_DEVICE, "Fake speech (two utterances)", "loopback", True),
    DeviceInfo(ENDLESS_DEVICE, "Fake silence (never ends)", "microphone", True),
]


def open_fake_source(device_id: str | None, kind: DeviceKind) -> AudioSource:
    """`None` 依類型選預設；未知 id 丟 `DeviceNotFoundError`。"""
    if device_id is None:
        device_id = SPEECH_DEVICE if kind == "loopback" else ENDLESS_DEVICE
    if device_id == SPEECH_DEVICE:
        return ArraySource(_speech_track())
    if device_id == ENDLESS_DEVICE:
        return EndlessSource()
    raise DeviceNotFoundError(f"device {device_id!r} is not available")


class FakeModelCatalog:
    """ASR 模型：`base`、`small`、`large-v3-turbo` 已下載；`fake-missing` 未下載；`fake-broken` 下載會失敗。

    已下載的模型 `disk_bytes` 是 `FAKE_MODEL_SIZE`；`delete()` 把模型標成未下載（不碰磁碟），
    之後的 `models.list` 與 `session.start`（`model_not_downloaded`）都看得到。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        defaults = Settings().asr
        # 預設設定的模型一定「已下載」，預設值改變時假模式不必跟著改。
        self._downloaded = {
            defaults.fast_model: True,
            defaults.final_model: True,
            "base": True,
            "large-v3-turbo": True,
            "fake-missing": False,
            "fake-broken": False,
        }

    def asr_models(self) -> list[AsrModelInfo]:
        """固定的模型清單。"""
        with self._lock:
            return [
                AsrModelInfo(
                    model,
                    FAKE_MODEL_SIZE,
                    "MIT",
                    "fast",
                    downloaded,
                    FAKE_MODEL_SIZE if downloaded else None,
                )
                for model, downloaded in self._downloaded.items()
            ]

    def download(
        self,
        model_id: str,
        progress: ProgressCallback,
        cancel: threading.Event,
    ) -> None:
        """分四段回報進度；`fake-broken` 在一半時失敗。"""
        step = FAKE_MODEL_SIZE // 4
        for i in range(1, 5):
            if cancel.is_set():
                raise RuntimeError("download cancelled")
            if model_id == "fake-broken" and i == 3:
                raise RuntimeError("simulated network failure")
            progress(step * i, FAKE_MODEL_SIZE)
        with self._lock:
            self._downloaded[model_id] = True

    def delete(self, model_id: str) -> int:
        """把模型標成未下載，回傳 `FAKE_MODEL_SIZE`。

        Raises:
            UnknownModelError: 不在清單中。
            FileNotFoundError: 沒有下載。
        """
        with self._lock:
            if model_id not in self._downloaded:
                raise UnknownModelError(f"unknown ASR model {model_id!r}")
            if not self._downloaded[model_id]:
                raise FileNotFoundError(f"ASR model {model_id} is not downloaded")
            self._downloaded[model_id] = False
        return FAKE_MODEL_SIZE

    def check_loadable(self, model_id: str) -> None:
        """模擬 `create_transcriber()` 的錯誤：未知 → UnknownModelError，未下載 → FileNotFoundError。"""
        with self._lock:
            if model_id not in self._downloaded:
                raise UnknownModelError(f"unknown ASR model {model_id!r}")
            if not self._downloaded[model_id]:
                raise FileNotFoundError(f"ASR model {model_id} is not downloaded")


class _FakeWheelResponse:
    """`asr.download.Response`：以小區塊回傳 wheel 內容，可設定每塊的延遲。"""

    CHUNK = 16 * 1024

    def __init__(
        self, status: int, body: bytes, headers: dict[str, str], delay: float
    ) -> None:
        self.status = status
        self._body = body
        self._headers = {k.lower(): v for k, v in headers.items()}
        self._delay = delay

    def header(self, name: str) -> str | None:
        """回應標頭（不分大小寫）。"""
        return self._headers.get(name.lower())

    def read(self, size: int) -> bytes:
        """回傳最多一個小區塊；慢速情境下每塊等待 `delay` 秒（讓取消測試來得及）。"""
        if self._delay and self._body:
            time.sleep(self._delay)
        chunk = self._body[: min(size, self.CHUNK)]
        self._body = self._body[len(chunk) :]
        return chunk

    def close(self) -> None:
        """沒有真正的連線。"""


class FakeWheelFetcher:
    """`asr.download.Fetcher`：不連網，直接回傳記憶體中的假 cuBLAS wheel（支援 Range）。"""

    def __init__(self, payload: bytes, *, corrupt: bool, delay: float) -> None:
        """
        Args:
            payload: wheel 的位元組。
            corrupt: 回傳等長的零位元組（SHA-256 不符，模擬下載內容損壞）。
            delay: 每個區塊的延遲秒數。
        """
        self._payload = bytes(len(payload)) if corrupt else payload
        self._delay = delay

    def open(
        self, url: str, headers: Mapping[str, str], timeout: float
    ) -> _FakeWheelResponse:
        """依 `Range` 標頭回傳 200 或 206。"""
        offset = 0
        range_header = headers.get("Range")
        if range_header is not None:
            offset = int(range_header.removeprefix("bytes=").rstrip("-"))
        body = self._payload[offset:]
        response_headers = {"Content-Length": str(len(body))}
        if offset:
            response_headers["Content-Range"] = (
                f"bytes {offset}-{len(self._payload) - 1}/{len(self._payload)}"
            )
        return _FakeWheelResponse(
            206 if offset else 200, body, response_headers, self._delay
        )


def _fake_wheel(library_bytes: int) -> tuple[RuntimeWheel, bytes]:
    """建立與真實 wheel 同樣結構（同名成員）的小 zip，回傳 wheel 定義與內容。"""
    members = (
        ("nvidia/cublas/bin/cublas64_12.dll", "cublas64_12.dll", b"A"),
        ("nvidia/cublas/bin/cublasLt64_12.dll", "cublasLt64_12.dll", b"B"),
        (
            f"nvidia_cublas_cu12-{CUBLAS_VERSION}.dist-info/License.txt",
            "License.txt",
            b"L",
        ),
    )
    buffer = io.BytesIO()
    wheel_members: list[WheelMember] = []
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_STORED) as archive:
        for archive_path, filename, fill in members:
            data = fill * (library_bytes if filename != "License.txt" else 64)
            archive.writestr(archive_path, data)
            wheel_members.append(WheelMember(archive_path, filename, len(data)))
    payload = buffer.getvalue()
    wheel = RuntimeWheel(
        platform_tag="fake_amd64",
        url=(
            "https://fake.invalid/packages/"
            f"nvidia_cublas_cu12-{CUBLAS_VERSION}-py3-none-fake_amd64.whl"
        ),
        size=len(payload),
        sha256=hashlib.sha256(payload).hexdigest(),
        members=tuple(wheel_members),
    )
    return wheel, payload


FAKE_TRANSLATION_ENV = "KIKITORI_ENGINE_FAKE_TRANSLATION"
"""假模式的翻譯伺服器情境（只在 `KIKITORI_ENGINE_FAKE=1` 時讀取），見 `FAKE_TRANSLATION_PROFILES`。"""
FAKE_TRANSLATION_PROFILES = ("ollama", "openai", "offline", "auth")
"""
- `ollama`（預設）：本機 Ollama，已安裝 qwen3.5:4b；`models.pull_translation` 會「下載」。
- `openai`：本機的其他 OpenAI 相容伺服器（不是 Ollama）：無法替使用者下載。
- `offline`：連不上翻譯伺服器。
- `auth`：伺服器回 401（金鑰錯誤）：`models.pull_translation` 回
  `translation_server_auth_failed`，`session.start` 照常開始並記 warning。
"""


class FakeTranslationServer:
    """假的翻譯伺服器：已安裝的模型清單與 Ollama 的 `/api/pull`（不連網）。

    `FakeHardwareProvider` 的偵測結果與 `models.pull_translation` 共用這份狀態，
    下載完成後 `models.list` 與推薦立即看得到新模型，和真實流程一樣。
    """

    def __init__(self, profile: str = "ollama") -> None:
        if profile not in FAKE_TRANSLATION_PROFILES:
            raise ValueError(f"unknown fake translation profile {profile!r}")
        self.profile = profile
        self._lock = threading.Lock()
        self._installed = list(FAKE_INSTALLED_TRANSLATION_MODELS)

    @property
    def installed(self) -> tuple[str, ...]:
        """已安裝的模型名稱。"""
        with self._lock:
            return tuple(self._installed)

    def server_info(self, base_url: str) -> TranslationServerInfo:
        """`hardware.detect` 的 `translation_server`。"""
        if self.profile == "offline":
            return TranslationServerInfo(
                base_url=base_url,
                is_local=True,
                reachable=False,
                error="cannot connect to localhost: fake server is offline",
            )
        if self.profile == "auth":
            # 真實的 probe 在 401 時也是「連得上、沒有模型清單」
            return TranslationServerInfo(
                base_url=base_url,
                is_local=True,
                reachable=True,
                error="HTTP 401 from /v1/models",
            )
        return TranslationServerInfo(
            base_url=base_url, is_local=True, reachable=True, models=self.installed
        )

    def server_state(self, settings: Settings) -> TranslationServerState:
        """見 `TranslationModels.server_state()`。"""
        if self.profile == "offline":
            return TranslationServerState(
                False, False, None, "fake translation server is offline"
            )
        if self.profile == "auth":
            return TranslationServerState(
                reachable=True,
                is_ollama=False,
                installed=None,
                error="HTTP 401 from /api/version: the server requires a valid API key",
                auth_failed=True,
            )
        return TranslationServerState(
            reachable=True,
            is_ollama=self.profile == "ollama",
            installed=self.installed,
            error=None if self.profile == "ollama" else "HTTP 404 from /api/version",
        )

    def pull(
        self,
        settings: Settings,
        model: str,
        progress: ProgressCallback,
        cancel: threading.Event,
    ) -> None:
        """分四段回報進度；`FAKE_BROKEN_PULL` 一半時失敗，`FAKE_SLOW_PULL` 每段等 0.5 秒。"""
        step = FAKE_PULL_SIZE // 4
        for i in range(1, 5):
            if cancel.wait(0.5 if model == FAKE_SLOW_PULL else 0):
                raise RuntimeError(f"pull of {model} cancelled")
            if model == FAKE_BROKEN_PULL and i == 3:
                raise RuntimeError("Ollama: simulated pull failure")
            progress(step * i, FAKE_PULL_SIZE)
        with self._lock:
            if normalize_model_name(model) not in {
                normalize_model_name(m) for m in self._installed
            }:
                self._installed.append(model)


FAKE_HARDWARE_ENV = "KIKITORI_ENGINE_FAKE_HARDWARE"
"""假模式的硬體情境（只在 `KIKITORI_ENGINE_FAKE=1` 時讀取），見 `FAKE_HARDWARE_PROFILES`。"""
FAKE_HARDWARE_PROFILES = ("nvidia", "apple", "slow-runtime", "broken-runtime")
"""
- `nvidia`（預設）：8 GB NVIDIA GPU、尚未安裝 cuBLAS、本機翻譯伺服器裝了 qwen3.5:4b。
- `apple`：Apple Silicon，沒有 GPU 執行環境（`gpu-runtime` 下載回 `gpu_runtime_unsupported`）。
- `slow-runtime`：同 nvidia，但 GPU 執行環境下載很慢（測試結束引擎時的取消）。
- `broken-runtime`：同 nvidia，但下載內容雜湊不符（測試失敗事件）。
"""


class FakeHardwareProvider:
    """假硬體：固定的偵測結果，GPU 執行環境用真的安裝流程裝到暫存目錄（假 wheel、不連網）。

    推薦、驗證與總覽都走真的 `kikitori.hardware` 程式碼，只有偵測與網路是假的；
    安裝後 cuBLAS 變成可用、推薦改為 GPU 等級，和真實流程一樣。
    """

    def __init__(
        self,
        catalog: FakeModelCatalog,
        profile: str = "nvidia",
        server: FakeTranslationServer | None = None,
    ) -> None:
        """
        Args:
            catalog: 假模式的 ASR 模型清單（`models.list` 的 ASR 部分以它為準）。
            profile: `FAKE_HARDWARE_PROFILES` 之一。
            server: 假的翻譯伺服器（偵測結果的 `translation_server` 取自它）；
                省略時是裝了 qwen3.5:4b 的本機 Ollama。
        """
        if profile not in FAKE_HARDWARE_PROFILES:
            raise ValueError(f"unknown fake hardware profile {profile!r}")
        self._catalog = catalog
        self._profile = profile
        self._server = server or FakeTranslationServer()
        # 行程結束時刪除；背景下載可能還在寫，清理失敗不影響測試結果
        self._tmp = tempfile.TemporaryDirectory(
            prefix="kikitori-fake-gpu-", ignore_cleanup_errors=True
        )
        self._root = Path(self._tmp.name)
        self._wheel: RuntimeWheel | None = None
        self._fetcher: FakeWheelFetcher | None = None
        if profile != "apple":
            slow = profile == "slow-runtime"
            self._wheel, payload = _fake_wheel(256 * 1024 if slow else 32 * 1024)
            self._fetcher = FakeWheelFetcher(
                payload, corrupt=profile == "broken-runtime", delay=0.1 if slow else 0
            )

    def detect(self, settings: Settings) -> HardwareInfo:
        """固定的硬體；cuBLAS 是否可用取決於假 GPU 執行環境是否已安裝。"""
        server = self._server.server_info(settings.translation.base_url)
        if self._wheel is None:
            return HardwareInfo(
                os="macos",
                os_version="Fake macOS 15",
                machine="arm64",
                cpu=CpuInfo("Fake M-series", 8, 8, "arm64"),
                memory=MemoryInfo(16 * GIB, 8 * GIB),
                gpus=(),
                apple_silicon=True,
                cublas=CublasInfo(supported_platform=False, available=False),
                translation_server=server,
            )
        runtime = installed_runtime_dir(self._root, self._wheel)
        cublas = (
            CublasInfo(True, True, (str(runtime),), "runtime")
            if runtime is not None
            else CublasInfo(supported_platform=True, available=False)
        )
        return HardwareInfo(
            os="windows",
            os_version="Fake Windows 11",
            machine="AMD64",
            cpu=CpuInfo("Fake 8-Core CPU", 8, 16, "AMD64"),
            memory=MemoryInfo(32 * GIB, 16 * GIB),
            gpus=(GpuInfo(0, "Fake GeForce 8GB", 8188, 600, "560.94", "8.9"),),
            apple_silicon=False,
            cublas=cublas,
            translation_server=server,
        )

    def models_overview(
        self, info: HardwareInfo, recommendation: ModelCombination
    ) -> dict[str, Any]:
        """真的 `models_overview()`，但 ASR 與 GPU 執行環境換成假模式的狀態。"""
        overview = models_overview(
            info,
            recommendation,
            cache_dir=self._root / "asr-cache",
            runtime_root=self._root,
        )
        registry = {entry["id"]: entry for entry in overview["asr"]}
        asr: list[dict[str, Any]] = []
        for model in self._catalog.asr_models():
            entry = dict(
                registry.get(
                    model.id,
                    {
                        "no_speech_reliable": None,
                        "cpu_ram_mb": None,
                        "vram_mb": None,
                        "vram_int8_mb": None,
                        "recommended_for": [],
                    },
                )
            )
            entry.update(dataclasses.asdict(model))
            asr.append(entry)
        runtime = self.gpu_runtime_status().to_dict()
        runtime["cublas_available"] = info.cublas.available
        runtime["cublas_source"] = info.cublas.source
        return {**overview, "asr": asr, "gpu_runtime": runtime}

    def gpu_runtime_status(self) -> GpuRuntimeStatus:
        """假 wheel 的安裝狀態；apple 情境為不支援。"""
        if self._wheel is None:
            return GpuRuntimeStatus(
                supported=False,
                installed=False,
                package=CUBLAS_PACKAGE,
                version=CUBLAS_VERSION,
                path=None,
                download_bytes=None,
                installed_bytes=None,
                license=LICENSE_NAME,
                license_url=LICENSE_URL,
            )
        return gpu_runtime_status(self._root, self._wheel)

    def install_gpu_runtime(
        self, progress: ProgressCallback, cancel: threading.Event
    ) -> None:
        """真的 `install_gpu_runtime()`，wheel 與 fetcher 換成假的。"""
        if self._wheel is None or self._fetcher is None:
            raise GpuRuntimeError("the fake apple profile has no GPU runtime")
        install_gpu_runtime(
            self._root,
            progress=progress,
            cancel=cancel,
            fetcher=self._fetcher,
            wheel=self._wheel,
        )


def _fake_hardware_profile() -> str:
    value = os.environ.get(FAKE_HARDWARE_ENV, "").strip() or "nvidia"
    if value not in FAKE_HARDWARE_PROFILES:
        logger.warning(
            "%s must be one of %s; using nvidia",
            FAKE_HARDWARE_ENV,
            ", ".join(FAKE_HARDWARE_PROFILES),
        )
        return "nvidia"
    return value


def _fake_translation_profile() -> str:
    value = os.environ.get(FAKE_TRANSLATION_ENV, "").strip() or "ollama"
    if value not in FAKE_TRANSLATION_PROFILES:
        logger.warning(
            "%s must be one of %s; using ollama",
            FAKE_TRANSLATION_ENV,
            ", ".join(FAKE_TRANSLATION_PROFILES),
        )
        return "ollama"
    return value


def _fake_spoken_language(settings: Settings, mode: Mode) -> str:
    """假辨識結果的語言（Whisper 語言碼），讓字幕依設定的語言組合走真的路由。

    口譯模擬使用者說自己的語言（會譯成對方語言）；影片翻譯用收聽語言，"auto" 時挑
    第一個和字幕語言不同的語言，才看得到翻譯。
    """
    target = get_language(settings.target_language).whisper_code
    if mode == "interpret":
        return target
    if settings.source_language != "auto":
        return get_language(settings.source_language).whisper_code
    return next(code for code in WHISPER_CODES if code != target)


def fake_services() -> EngineServices:
    """假模式的完整服務（硬體情境由 `KIKITORI_ENGINE_FAKE_HARDWARE`、翻譯伺服器情境由
    `KIKITORI_ENGINE_FAKE_TRANSLATION` 選擇）。"""
    catalog = FakeModelCatalog()
    server = FakeTranslationServer(_fake_translation_profile())

    def build_components(settings: Settings, mode: Mode) -> PipelineComponents:
        catalog.check_loadable(settings.asr.fast_model)
        catalog.check_loadable(settings.asr.final_model)
        language = _fake_spoken_language(settings, mode)
        translator = FakeTranslator()
        return PipelineComponents(
            voice_model=AmplitudeVoiceModel(),
            fast_transcriber=FakeTranscriber("partial", language),
            final_transcriber=FakeTranscriber("final", language),
            fast_translator=translator,
            final_translator=translator,
            transcript_filter=default_transcript_filter(
                settings.pipeline.no_speech_threshold
            ),
        )

    return EngineServices(
        settings_store=SettingsStore(default_settings_path()),
        list_devices=lambda: list(FAKE_DEVICES),
        open_source=open_fake_source,
        build_components=build_components,
        hardware=FakeHardwareProvider(catalog, _fake_hardware_profile(), server),
        models=catalog,
        translation_models=server,
        fake=True,
    )
