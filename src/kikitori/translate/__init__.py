"""翻譯模組：把 ASR 的一句原文翻成字幕用的譯文。

位於語音辨識之後、pipeline 之前。pipeline 只依賴 `Translator` 與
`TranslationRequest`；預設實作 `OpenAICompatibleTranslator` 透過 OpenAI 相容
Chat Completions API（預設本機 Ollama）翻譯。hardware 模組用
`recommend_translation_models()` 依 VRAM 挑模型。規格見 `docs/specs/translate.md`。
"""

from kikitori.translate.client import (
    DEFAULT_API_KEY_ENV,
    DEFAULT_BASE_URL,
    OpenAICompatibleTranslator,
    TranslatorConfig,
)
from kikitori.translate.errors import (
    InvalidRequestError,
    TranslationConnectionError,
    TranslationError,
    TranslationHTTPError,
    TranslationResponseError,
    TranslationTimeoutError,
)
from kikitori.translate.languages import ALL_LANGUAGE_CODES
from kikitori.translate.presets import (
    PRESETS,
    RECOMMENDATION_TIERS,
    ModelPreset,
    NoSupportingModelError,
    RecommendationTier,
    TranslationModelChoice,
    preset_for_model,
    recommend_translation_models,
    supports,
)
from kikitori.translate.types import TranslationRequest, Translator

__all__ = [
    "ALL_LANGUAGE_CODES",
    "DEFAULT_API_KEY_ENV",
    "DEFAULT_BASE_URL",
    "PRESETS",
    "RECOMMENDATION_TIERS",
    "InvalidRequestError",
    "ModelPreset",
    "NoSupportingModelError",
    "OpenAICompatibleTranslator",
    "RecommendationTier",
    "TranslationConnectionError",
    "TranslationError",
    "TranslationHTTPError",
    "TranslationModelChoice",
    "TranslationRequest",
    "TranslationResponseError",
    "TranslationTimeoutError",
    "Translator",
    "TranslatorConfig",
    "preset_for_model",
    "recommend_translation_models",
    "supports",
]
