"""語言清單：收聽語言與字幕語言的唯一來源（20 種）。

位於最底層，不依賴其他 kikitori 模組：config 用它驗證設定，pipeline 用它決定翻譯路由與
ASR 的語言範圍，translate 用它驗證請求、組提示詞與決定字形轉換。規格見
`docs/specs/languages-i18n.md`。
"""

from kikitori.languages.catalog import (
    LANGUAGES,
    WHISPER_CODES,
    Language,
    UnknownLanguageError,
    get_language,
    language_codes,
    normalize_language,
    to_whisper_code,
    whisper_language_name,
)

__all__ = [
    "LANGUAGES",
    "WHISPER_CODES",
    "Language",
    "UnknownLanguageError",
    "get_language",
    "language_codes",
    "normalize_language",
    "to_whisper_code",
    "whisper_language_name",
]
