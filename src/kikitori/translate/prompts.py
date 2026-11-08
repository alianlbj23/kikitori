"""依模型的提示詞格式組出 Chat Completions 的 messages。

翻譯專用模型（HY-MT、Sakura）是用固定範本訓練的，偏離範本品質會掉，所以照
官方 model card 逐字使用；通用模型（Qwen 等）則用 system 規則 + user 內容。
輸入的請求必須已經過 `languages.validate_request` 正規化：原文語言是 Whisper
語言碼（中文為 "zh"），譯文語言是語言清單的語言碼。
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from kikitori.languages import (
    LANGUAGES,
    WHISPER_CODES,
    get_language,
    whisper_language_name,
)
from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.glossary import Term
from kikitori.translate.languages import SAKURA_PAIRS
from kikitori.translate.types import TranslationRequest

PromptStyle = Literal["generic", "hunyuan_mt", "sakura"]
Message = dict[str, str]

# HY-MT1.5 model card 語言表（https://huggingface.co/tencent/HY-MT1.5-7B）只取語言清單
# 內的語言。ZH<=>XX 範本用「Chinese Names」欄、XX<=>XX 範本用「Languages」欄；
# zh-TW 對應表中的 zh-Hant（Traditional Chinese），zh-CN 對應 zh（Chinese）。
_HUNYUAN_CHINESE_NAMES = {
    "zh-CN": "中文",
    "zh-TW": "繁体中文",
    "en": "英语",
    "fr": "法语",
    "pt": "葡萄牙语",
    "es": "西班牙语",
    "ja": "日语",
    "tr": "土耳其语",
    "ru": "俄语",
    "ar": "阿拉伯语",
    "ko": "韩语",
    "th": "泰语",
    "it": "意大利语",
    "de": "德语",
    "vi": "越南语",
    "id": "印尼语",
    "hi": "印地语",
    "pl": "波兰语",
    "nl": "荷兰语",
    "uk": "乌克兰语",
}
_HUNYUAN_ENGLISH_NAMES = {
    "zh-CN": "Chinese",
    "zh-TW": "Traditional Chinese",
    "en": "English",
    "fr": "French",
    "pt": "Portuguese",
    "es": "Spanish",
    "ja": "Japanese",
    "tr": "Turkish",
    "ru": "Russian",
    "ar": "Arabic",
    "ko": "Korean",
    "th": "Thai",
    "it": "Italian",
    "de": "German",
    "vi": "Vietnamese",
    "id": "Indonesian",
    "hi": "Hindi",
    "pl": "Polish",
    "nl": "Dutch",
    "uk": "Ukrainian",
}


@dataclass(frozen=True)
class _GenericTemplate:
    system_lines: tuple[str, ...]  # 第一行的 {source} 代入原文語言名稱
    source_names: dict[str, str]
    context_header: str
    instruction: str
    terms_header: str


# 原文語言（Whisper 語言碼）在各指示語言中的名稱。
_SOURCE_NAMES_ZH_TW = {
    "ja": "日文",
    "en": "英文",
    "zh": "中文",
    "ko": "韓文",
    "es": "西班牙文",
    "fr": "法文",
    "de": "德文",
    "pt": "葡萄牙文",
    "ru": "俄文",
    "it": "義大利文",
    "ar": "阿拉伯文",
    "hi": "印地文",
    "id": "印尼文",
    "vi": "越南文",
    "th": "泰文",
    "tr": "土耳其文",
    "pl": "波蘭文",
    "nl": "荷蘭文",
    "uk": "烏克蘭文",
}
_SOURCE_NAMES_ZH_CN = {
    "ja": "日语",
    "en": "英语",
    "zh": "中文",
    "ko": "韩语",
    "es": "西班牙语",
    "fr": "法语",
    "de": "德语",
    "pt": "葡萄牙语",
    "ru": "俄语",
    "it": "意大利语",
    "ar": "阿拉伯语",
    "hi": "印地语",
    "id": "印尼语",
    "vi": "越南语",
    "th": "泰语",
    "tr": "土耳其语",
    "pl": "波兰语",
    "nl": "荷兰语",
    "uk": "乌克兰语",
}
_SOURCE_NAMES_JA = {
    "ja": "日本語",
    "en": "英語",
    "zh": "中国語",
    "ko": "韓国語",
    "es": "スペイン語",
    "fr": "フランス語",
    "de": "ドイツ語",
    "pt": "ポルトガル語",
    "ru": "ロシア語",
    "it": "イタリア語",
    "ar": "アラビア語",
    "hi": "ヒンディー語",
    "id": "インドネシア語",
    "vi": "ベトナム語",
    "th": "タイ語",
    "tr": "トルコ語",
    "pl": "ポーランド語",
    "nl": "オランダ語",
    "uk": "ウクライナ語",
}
_SOURCE_NAMES_EN = {code: whisper_language_name(code) for code in WHISPER_CODES}

_ENGLISH_TERMS_HEADER = "Terminology (use these translations when the terms appear):"

# zh-TW、zh-CN、ja、en 的指示用「譯文語言」寫：實測英文指示下 qwen3.5:9b 翻日文時有
# 11/18 次直接輸出日文，改用繁體中文指示後為 0/18（experiments/translate_benchmark/README.md）。
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
        source_names=_SOURCE_NAMES_ZH_TW,
        context_header="前文（只供參考，不要翻譯）：",
        instruction="請翻譯成繁體中文：",
        terms_header="術語（原文出現時請用這些譯法）：",
    ),
    "zh-CN": _GenericTemplate(
        system_lines=(
            "你是字幕翻译员，把用户给的{source}句子翻译成简体中文。",
            "规则：",
            "- 只输出一行译文。",
            "- 简短、自然、忠于原意与语气。",
            "- 不要解释、注释、引号或拼音，不要保留原文。",
            "- 前文只供参考，不要翻译前文。",
            "- 一律使用简体中文，不要用繁体字。",
        ),
        source_names=_SOURCE_NAMES_ZH_CN,
        context_header="前文（只供参考，不要翻译）：",
        instruction="请翻译成简体中文：",
        terms_header="术语（原文出现时请用这些译法）：",
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
        source_names=_SOURCE_NAMES_JA,
        context_header="前の文（参考用、翻訳しない）：",
        instruction="日本語に翻訳してください：",
        terms_header="用語（原文に出てきたらこの訳を使う）：",
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
        source_names=_SOURCE_NAMES_EN,
        context_header="Earlier lines (reference only, do not translate):",
        instruction="Translate into English:",
        terms_header=_ENGLISH_TERMS_HEADER,
    ),
}


def _english_template(target_code: str) -> _GenericTemplate:
    # 其他譯文語言沒有實測過用該語言寫的指示，改用英文指示，並同時寫出英文名與原生名、
    # 明確要求只輸出該語言，降低模型回到原文語言或英文的機率。
    lang = get_language(target_code)
    name = f"{lang.english_name} ({lang.native_name})"
    return _GenericTemplate(
        system_lines=(
            (
                "You are a subtitle translator. Translate the user's {source} line "
                f"into {name}."
            ),
            "Rules:",
            f"- Output only the translation as a single line, written in {name}.",
            "- Keep it short, natural and faithful to the meaning and tone.",
            "- No explanations, notes, romanization, or quotation marks.",
            "- Earlier lines are for reference only; do not translate them.",
            f"- Never answer in any language other than {lang.english_name}.",
        ),
        source_names=_SOURCE_NAMES_EN,
        context_header="Earlier lines (reference only, do not translate):",
        instruction=f"Translate into {name}:",
        terms_header=_ENGLISH_TERMS_HEADER,
    )


# 依（原文, 譯文）指定的範本，優先於上面「依譯文語言」的範本。中文與日文共用漢字：
# 用日文寫指示時，模型會用日文的讀法斷詞、理解同形詞（「豬排便當」→「豚の排泄物」、
# 「我娘」→「娘」）。用原文語言（中文）寫指示，qwen3.5:9b 的陷阱句（不含內建詞表）
# 16/24 → 22/24，一般句子沒有「沒翻譯」（experiments/kanji_false_friends）。日翻中
# 反過來用日文指示沒有改善、還多一句沒翻，所以只有這個方向。
_PAIR_TEMPLATES: dict[tuple[str, str], _GenericTemplate] = {
    ("zh", "ja"): _GenericTemplate(
        system_lines=(
            "你是字幕翻譯員。把使用者輸入的{source}句子翻成自然的日文。",
            "規則：",
            "- 只輸出一行日文譯文。",
            "- 簡短自然，忠於原意與語氣。",
            "- 不加說明、註解、引號或振假名，不保留原文。",
            "- 前文僅供參考，不要翻譯。",
        ),
        source_names={"zh": "中文"},
        context_header="前文（只供參考，不要翻譯）：",
        instruction="翻成日文：",
        terms_header="術語（原文出現時請用這些譯法）：",
    ),
}

for _lang in LANGUAGES:
    if _lang.code not in _GENERIC_TEMPLATES:
        _GENERIC_TEMPLATES[_lang.code] = _english_template(_lang.code)
del _lang

# Sakura-GalTransl v3 官方 system prompt（含「简体中文」：模型就是這樣訓練的，
# 繁體由後處理的 OpenCC 轉換負責）。
SAKURA_SYSTEM_PROMPT = (
    "你是一个视觉小说翻译模型，可以通顺地使用给定的术语表以指定的风格将日文翻译成"
    "简体中文，并联系上下文正确使用人称代词，注意不要混淆使役态和被动态的主语和宾语，"
    "不要擅自添加原文中没有的特殊符号，也不要擅自增加或减少换行。"
)


def build_messages(
    style: PromptStyle, request: TranslationRequest, terms: Sequence[Term] = ()
) -> list[Message]:
    """組出要送給模型的 messages。

    Args:
        style: 提示詞格式。
        request: 已正規化的請求。
        terms: 這句原文出現的術語（`Glossary.lookup()` 的結果）；空的時候提示詞與
            沒有術語表時完全相同。

    Raises:
        InvalidRequestError: 這個格式不支援請求的語言組合（例如 Sakura 只做日翻中）。
        ValueError: 不認得的格式。
    """
    if style == "hunyuan_mt":
        return _hunyuan(request, terms)
    if style == "sakura":
        return _sakura(request, terms)
    if style == "generic":
        return _generic(request, terms)
    raise ValueError(f"unknown prompt style: {style!r}")


def _hunyuan(request: TranslationRequest, terms: Sequence[Term]) -> list[Message]:
    # 官方說模型沒有預設 system prompt，範本整段放在 user 訊息。原文或譯文是中文時用
    # ZH<=>XX 範本（中文指示、中文語言名），其他組合用 XX<=>XX 範本（英文）。
    target_code = request.target_language
    if target_code not in _HUNYUAN_CHINESE_NAMES:
        raise InvalidRequestError(f"HY-MT does not support target {target_code!r}")
    chinese = request.source_language == "zh" or target_code in ("zh-TW", "zh-CN")
    if terms:
        # 官方術語干預範本（model card「Prompt Template for terminology intervention」，
        # 2026-11-06 查閱）只有中文版，所以任何組合都用它與中文語言名；範本一次一條，
        # 多條時逐行列出。HY-MT 的預設組不帶前文，術語與前文不會同時出現。
        target = _HUNYUAN_CHINESE_NAMES[target_code]
        lines = "\n".join(f"{t.source} 翻译成 {t.target}" for t in terms)
        content = (
            f"参考下面的翻译：\n{lines}\n\n"
            f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n"
            + request.text
        )
    elif request.context:
        # 官方只有一個（中文的）前文範本，所以任何組合帶前文時都用它。
        target = _HUNYUAN_CHINESE_NAMES[target_code]
        content = (
            "\n".join(request.context)
            + f"\n参考上面的信息，把下面的文本翻译成{target}，"
            "注意不需要翻译上文，也不要额外解释：\n" + request.text
        )
    elif chinese:
        target = _HUNYUAN_CHINESE_NAMES[target_code]
        content = (
            f"将以下文本翻译为{target}，注意只需要输出翻译后的结果，不要额外解释：\n\n"
            + request.text
        )
    else:
        target = _HUNYUAN_ENGLISH_NAMES[target_code]
        content = (
            f"Translate the following segment into {target}, "
            "without additional explanation.\n\n" + request.text
        )
    return [{"role": "user", "content": content}]


def _sakura(request: TranslationRequest, terms: Sequence[Term]) -> list[Message]:
    if (request.source_language, request.target_language) not in SAKURA_PAIRS:
        raise InvalidRequestError("Sakura models only translate ja -> zh-TW / zh-CN")
    # 模型只輸出簡體中文；zh-TW 由後處理的 OpenCC 轉成台灣正體。官方的 [History] 是「上一輪的譯文」，而 request.context 是前文原文，格式不合，
    # 硬塞進去可能讓模型把前文也翻出來，所以不放前文。術語表用官方的 GPT 字典格式
    # （model card：每行「src->dst #备注」，可為空），放在 [Glossary] 的位置；譯法照譯文
    # 語言給（zh-TW 時是繁體），模型照抄後由後處理的選擇性轉換保留。
    glossary = "\n".join(f"{t.source}->{t.target}" for t in terms)
    content = (
        "参考以下术语表（可为空，格式为src->dst #备注）：\n"
        + glossary
        + "\n根据以上术语表的对应关系和备注，结合历史剧情和上下文，"
        "将下面的文本从日文翻译成简体中文：\n" + request.text
    )
    return [
        {"role": "system", "content": SAKURA_SYSTEM_PROMPT},
        {"role": "user", "content": content},
    ]


def _generic(request: TranslationRequest, terms: Sequence[Term]) -> list[Message]:
    template = _PAIR_TEMPLATES.get((request.source_language, request.target_language))
    if template is None:
        template = _GENERIC_TEMPLATES.get(request.target_language)
    if template is None or request.source_language not in template.source_names:
        raise InvalidRequestError(
            "unsupported language pair: "
            f"{request.source_language!r} -> {request.target_language!r}"
        )
    source = template.source_names[request.source_language]
    system = "\n".join(template.system_lines).format(source=source)
    user = ""
    if request.context:
        user = template.context_header + "\n" + "\n".join(request.context) + "\n\n"
    if terms:
        # WMT 術語任務的做法：「原文 → 譯法」逐行列出，標題用與指示相同的語言。
        lines = "\n".join(f"{t.source} → {t.target}" for t in terms)
        user += template.terms_header + "\n" + lines + "\n\n"
    user += template.instruction + "\n" + request.text
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
