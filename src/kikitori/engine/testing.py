"""引擎的假模式服務：不需要音效卡、模型或網路就能跑完整個協定。

給子行程測試與 UI 開發使用（環境變數 `KIKITORI_ENGINE_FAKE=1`）。合成音訊以
方波代表「人聲」，依振幅判斷的 VoiceModel 讓切句結果固定；ASR 與翻譯回傳可預測
的文字。設定檔仍用真實的 `SettingsStore`（測試以 `KIKITORI_CONFIG_DIR` 指到暫存目錄）。
"""

import threading
from collections.abc import Iterator

import numpy as np
import numpy.typing as npt

from kikitori.asr.types import Transcript
from kikitori.capture import ArraySource, DeviceInfo, DeviceKind
from kikitori.capture.errors import DeviceNotFoundError
from kikitori.capture.types import AudioSource
from kikitori.config import Settings, SettingsStore, default_settings_path
from kikitori.engine.services import (
    BasicHardwareProvider,
    EngineServices,
    ProgressCallback,
)
from kikitori.pipeline import (
    Mode,
    PipelineComponents,
    default_transcript_filter,
)
from kikitori.pipeline.factory import AsrModelInfo, UnknownModelError
from kikitori.translate.types import TranslationRequest

SAMPLE_RATE = 16000
SPEECH_DEVICE = "fake:loopback:speech"
ENDLESS_DEVICE = "fake:microphone:endless"
FAKE_MODEL_SIZE = 4 * 1024 * 1024

FAKE_INSTALLED_TRANSLATION_MODELS = ["qwen3.5:4b"]


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
    """ASR 模型：`base`、`small`、`large-v3-turbo` 已下載；`fake-missing` 未下載；`fake-broken` 下載會失敗。"""

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
                AsrModelInfo(model, FAKE_MODEL_SIZE, "MIT", "fast", downloaded)
                for model, downloaded in self._downloaded.items()
            ]

    def installed_translation_models(self, settings: Settings) -> list[str]:
        """假裝本機 Ollama 裝了 qwen3.5:4b。"""
        return list(FAKE_INSTALLED_TRANSLATION_MODELS)

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

    def check_loadable(self, model_id: str) -> None:
        """模擬 `create_transcriber()` 的錯誤：未知 → UnknownModelError，未下載 → FileNotFoundError。"""
        with self._lock:
            if model_id not in self._downloaded:
                raise UnknownModelError(f"unknown ASR model {model_id!r}")
            if not self._downloaded[model_id]:
                raise FileNotFoundError(f"ASR model {model_id} is not downloaded")


def fake_services() -> EngineServices:
    """假模式的完整服務。"""
    catalog = FakeModelCatalog()

    def build_components(settings: Settings, mode: Mode) -> PipelineComponents:
        catalog.check_loadable(settings.asr.fast_model)
        catalog.check_loadable(settings.asr.final_model)
        language = "zh" if mode == "interpret" else "ja"
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
        hardware=BasicHardwareProvider(),
        models=catalog,
        fake=True,
    )
