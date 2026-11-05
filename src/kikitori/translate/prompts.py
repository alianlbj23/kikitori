"""依模型的提示詞格式組出 Chat Completions 的 messages。

翻譯專用模型（HY-MT、Sakura）是用固定範本訓練的，偏離範本品質會掉，所以照
官方 model card 逐字使用；通用模型（Qwen 等）則用 system 規則 + user 內容，
指示以譯文語言撰寫。輸入的請求必須已經過 `languages.validate_request` 正規化。
"""

from dataclasses import dataclass
from typing import Literal

from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.types import TranslationRequest

PromptStyle = Literal["generic", "hunyuan_mt", "sakura"]
Message = dict[str, str]

# HY-MT1.5 model card 語言表的「Chinese Names」欄；zh-TW 對應 zh-Hant。
_HUNYUAN_LANGUAGE_NAMES = {
    "zh-TW": "繁体中文",
    "ja": "日语",
    "en": "英语",
    "zh": "中文",
}


@dataclass(frozen=True)
class _GenericTemplate:
    system_lines: tuple[str, ...]  # 第一行的 {source} 代入原文語言名稱
    source_names: dict[str, str]
    context_header: str
    instruction: str


# 指示一律用「譯文語言」寫：實測英文指示下 qwen3.5:9b 翻日文時有 11/18 次直接輸出
# 日文，改用繁體中文指示後為 0/18（experiments/translate_benchmark/README.md）。
_GENERIC_TEMPLATES: dict[str, _GenericTemplate] = {
    "zh-TW": _GenericTemplate(
        system_lines=(
            "你是字幕翻譯員，把使用者給的{source}句子翻譯成繁體中文（台灣用語）。",
            "規則：",
            "- 只輸出一行譯文。",
            "- 簡短、自然、忠於原意與語氣。",
            "- 不要解釋、註解、引號或拼音，不要保留原文。",
            "- 前文只供參考，不要翻譯前文。",
            "- 一律使用繁體中文與台灣用語，不要用簡體字。",
        ),
        source_names={"ja": "日文", "en": "英文"},
        context_header="前文（只供參考，不要翻譯）：",
        instruction="請翻譯成繁體中文：",
    ),
    "ja": _GenericTemplate(
        system_lines=(
            "あなたは字幕翻訳者です。ユーザーが入力した{source}の文を自然な日本語に翻訳してください。",
            "ルール：",
            "- 訳文を1行だけ出力する。",
            "- 短く自然に、意味と口調に忠実に訳す。",
            "- 説明、注釈、引用符、ふりがなは付けない。原文を残さない。",
            "- 前の文は参考用なので翻訳しない。",
        ),
        source_names={"zh": "中国語"},
        context_header="前の文（参考用、翻訳しない）：",
        instruction="日本語に翻訳してください：",
    ),
    "en": _GenericTemplate(
        system_lines=(
            "You are a subtitle translator. Translate the user's {source} line into natural English.",
            "Rules:",
            "- Output only the translation as a single line.",
            "- Keep it short, natural and faithful to the meaning and tone.",
            "- No explanations, notes, romanization, or quotation marks.",
            "- Earlier lines are for reference only; do not translate them.",
        ),
        source_names={"zh": "Chinese"},
        context_header="Earlier lines (reference only, do not translate):",
        instruction="Translate into English:",
    ),
}

# Sakura-GalTransl v3 官方 system prompt（含「简体中文」：模型就是這樣訓練的，
# 繁體由後處理的 OpenCC 轉換負責）。
SAKURA_SYSTEM_PROMPT = (
    "你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成"
    "简体中文，并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，"
    "不要擅自添加原文中没有的特殊符号，也不要擅自增加或减少换行。"
)


def build_messages(style: PromptStyle, request: TranslationRequest) -> list[Message]:
    """組出要送給模型的 messages。

    Args:
        style: 提示詞格式。
        request: 已正規化的請求。

    Raises:
        InvalidRequestError: 這個格式不支援請求的語言組合（例如 Sakura 只做日翻中）。
        ValueError: 不認得的格式。
    """
    if style == "hunyuan_mt":
        return _hunyuan(request)
    if style == "sakura":
        return _sakura(request)
    if style == "generic":
        return _generic(request)
    raise ValueError(f"unknown prompt style: {style!r}")


def _hunyuan(request: TranslationRequest) -> list[Message]:
    # 官方說模型沒有預設 system prompt，範本整段放在 user 訊息。本專案的語言組合
    # 都含中文，所以用 ZH<=>XX 範本，不用英文的 XX<=>XX 範本。
    target = _HUNYUAN_LANGUAGE_NAMES[request.target_language]
    if request.context:
        content = (
            "\n".join(request.context)
            + f"\n参考上面的信息，把下面的文本翻译成{target}，"
            "注意不需要翻译上文，也不要额外解释：\n" + request.text
        )
    else:
        content = (
            f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n"
            + request.text
        )
    return [{"role": "user", "content": content}]


def _sakura(request: TranslationRequest) -> list[Message]:
    if (request.source_language, request.target_language) != ("ja", "zh-TW"):
        raise InvalidRequestError("Sakura models only translate ja -> zh-TW")
    # 官方的 [History] 是「上一輪的譯文」，而 request.context 是前文原文，格式不合，
    # 硬塞進去可能讓模型把前文也翻出來，所以不放前文；術語表目前留空。
    content = (
        "参考以下术语表（可为空，格式为src->dst #备注）：\n\n"
        "根据以上术语表的对应关系和备注，结合历史剧情和上下文，"
        "将下面的文本从日文翻译成简体中文：\n" + request.text
    )
    return [
        {"role": "system", "content": SAKURA_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _generic(request: TranslationRequest) -> list[Message]:
    template = _GENERIC_TEMPLATES[request.target_language]
    source = template.source_names[request.source_language]
    system = "\n".join(template.system_lines).format(source=source)
    user = ""
    if request.context:
        user = template.context_header + "\n" + "\n".join(request.context) + "\n\n"
    user += template.instruction + "\n" + request.text
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
