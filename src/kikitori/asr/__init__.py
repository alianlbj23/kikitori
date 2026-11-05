"""語音辨識模組：把 VAD 切好的一句 16 kHz int16 音訊轉成文字。

位於 VAD 之後、翻譯之前。對外介面是 `Transcriber`／`Transcript`
（見 `docs/specs/app-architecture.md`）；實作為 CTranslate2 Whisper（不依賴 PyTorch），
模型清單、下載與快取、幻覺過濾見 `docs/specs/asr.md`。
"""

from kikitori.asr.audio import SAMPLE_RATE, validate_audio
from kikitori.asr.cache import default_cache_dir
from kikitori.asr.download import (
    DownloadCancelledError,
    ModelDownloadError,
    ModelIntegrityError,
    ProgressCallback,
    download_model,
    is_downloaded,
    model_dir,
)
from kikitori.asr.filtering import FilterConfig, rejection_reason
from kikitori.asr.registry import MODELS, ModelFile, ModelSpec, get_model
from kikitori.asr.types import Transcriber, Transcript
from kikitori.asr.whisper import (
    FAST_DECODE,
    FINAL_DECODE,
    DecodeOptions,
    WhisperTranscriber,
    load_transcriber,
    preload_cuda_libraries,
)

__all__ = [
    "FAST_DECODE",
    "FINAL_DECODE",
    "MODELS",
    "SAMPLE_RATE",
    "DecodeOptions",
    "DownloadCancelledError",
    "FilterConfig",
    "ModelDownloadError",
    "ModelFile",
    "ModelIntegrityError",
    "ModelSpec",
    "ProgressCallback",
    "Transcriber",
    "Transcript",
    "WhisperTranscriber",
    "default_cache_dir",
    "download_model",
    "get_model",
    "is_downloaded",
    "load_transcriber",
    "model_dir",
    "preload_cuda_libraries",
    "rejection_reason",
    "validate_audio",
]
