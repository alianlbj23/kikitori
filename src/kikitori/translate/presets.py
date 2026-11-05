"""翻譯模型預設組：每個已知模型的提示詞格式、取樣參數、授權與 VRAM 推薦順序。

數值來源是各模型官方 model card；推薦順序依 `experiments/translate_benchmark/`
的實測決定。hardware 模組用 `recommend_translation_models()` 挑預設模型，
UI 用 `ModelPreset` 的授權欄位顯示限制。本專案只以名稱引用使用者自行安裝的
模型，不散布權重。
"""

import math
from collections.abc import Collection
from dataclasses import dataclass

from kikitori.translate.errors import InvalidRequestError
from kikitori.translate.languages import (
    ALL_LANGUAGE_CODES,
    SAKURA_PAIRS,
    normalize_pair,
)
from kikitori.translate.prompts import PromptStyle


@dataclass(frozen=True)
class ModelPreset:
    """一個翻譯模型的使用方式與授權資訊。

    Attributes:
        model: OpenAI 相容 API 的模型名稱（Ollama 的 `name:tag`）。
        label: UI 顯示名稱。
        prompt_style: 提示詞格式。
        temperature: 建議溫度。
        top_p: 建議 top_p；None 表示不送，用伺服器預設。
        presence_penalty: 建議 presence_penalty；None 表示不送。
        reasoning_effort: 送給 Ollama 的 `reasoning_effort`；"none" 關閉思考。
        languages: 支援的清單語言碼（原文與譯文都要在內）。
        outputs_simplified: 模型本身輸出簡體（靠 OpenCC 轉繁體）。
        supports_context: 是否把前文放進提示詞；小模型會把前文一起翻出來時設 False。
        approx_vram_gb: 權重大小（GB），推估 VRAM 用；KV cache 另計。
        license: 授權名稱。
        license_url: 授權全文網址。
        commercial_use: 授權是否允許商業用途。
        excluded_regions: 授權不適用的地區（"EU"、"UK"、"KR"）。
        pairs: 只支援特定方向時的（Whisper 原文, 清單譯文）組合（例如 Sakura 只做
            日 → 中）；None 表示 `languages` 內任兩個不同語言之間都可以翻。
    """

    model: str
    label: str
    prompt_style: PromptStyle
    temperature: float
    top_p: float | None
    presence_penalty: float | None
    reasoning_effort: str | None
    languages: frozenset[str]
    outputs_simplified: bool
    supports_context: bool
    approx_vram_gb: float
    license: str
    license_url: str
    commercial_use: bool
    excluded_regions: tuple[str, ...] = ()
    pairs: frozenset[tuple[str, str]] | None = None


# HY-MT1.5 model card（https://huggingface.co/tencent/HY-MT1.5-7B，2026-11-05 查閱）的
# 支援語言表共 38 種（含繁體中文、粵語等）；這裡只列語言清單內的語言，20 種全部涵蓋。
_HY_LANGUAGES = frozenset(
    {
        "zh-CN",  # Chinese
        "zh-TW",  # Traditional Chinese (zh-Hant)
        "en",
        "fr",
        "pt",
        "es",
        "ja",
        "tr",
        "ru",
        "ar",
        "ko",
        "th",
        "it",
        "de",
        "vi",
        "id",
        "hi",
        "pl",
        "nl",
        "uk",
    }
)

# Qwen3.5 model card（https://huggingface.co/Qwen/Qwen3.5-9B，2026-11-05 查閱）寫明
# 「Expanded support to 201 languages and dialects」，但沒有列出清單；本專案的 20 種都是
# 主流語言，視為全部支援。實際輸出語言是否正確由 experiments/translate_benchmark 驗證。
_QWEN_LANGUAGES = ALL_LANGUAGE_CODES

_HY_LICENSE = "Tencent HY Community License"
_HY_REGIONS = ("EU", "UK", "KR")


def _hunyuan(
    model: str, label: str, vram: float, repo: str, supports_context: bool
) -> ModelPreset:
    # model card 建議 top_k 20、top_p 0.6、repetition_penalty 1.05、temperature 0.7；
    # OpenAI 相容 API 沒有 top_k／repetition_penalty，由 Ollama 模型檔的預設值負責。
    # 溫度改 0（greedy）：experiments/translate_eval 中 HY-MT 的 0 與 0.7 在四個資料集都
    # 沒有顯著差別（1.8B 在 social 反而 0 較好），0 讓同一句永遠得到同一個譯文，字幕不會
    # 因重翻而跳動。溫度 0 時 top_p 不起作用，保留只是為了與評測設定一致。
    return ModelPreset(
        model=model,
        label=label,
        prompt_style="hunyuan_mt",
        temperature=0.0,
        top_p=0.6,
        presence_penalty=None,
        reasoning_effort=None,
        languages=_HY_LANGUAGES,
        outputs_simplified=False,
        supports_context=supports_context,
        approx_vram_gb=vram,
        license=_HY_LICENSE,
        license_url=f"https://huggingface.co/tencent/{repo}/blob/main/License.txt",
        commercial_use=True,
        excluded_regions=_HY_REGIONS,
    )


def _qwen(model: str, label: str, vram: float, repo: str) -> ModelPreset:
    # Qwen3.5 預設會思考；官方不支援 /no_think，Ollama 以 reasoning_effort="none"
    # 關閉。top_p、presence_penalty 用 model card 的非思考模式建議值；溫度不用建議的
    # 0.7 而用 0：experiments/translate_eval 中溫度 0 讓 ja→zh 兩個資料集的 chrF 都顯著
    # 提高（speech +0.7、social +2.3，9B／4B 皆然），social 的 COMET 也顯著提高，沒有任何
    # 資料集變差。溫度 0 時 top_p 不起作用；presence_penalty 是對 logits 的懲罰，greedy
    # 時仍有作用，而評測的溫度 0 設定保留了 1.5，所以照樣送出。
    return ModelPreset(
        model=model,
        label=label,
        prompt_style="generic",
        temperature=0.0,
        top_p=0.8,
        presence_penalty=1.5,
        reasoning_effort="none",
        languages=_QWEN_LANGUAGES,
        outputs_simplified=False,
        supports_context=True,
        approx_vram_gb=vram,
        license="Apache-2.0",
        license_url=f"https://huggingface.co/Qwen/{repo}/blob/main/LICENSE",
        commercial_use=True,
    )


PRESETS: dict[str, ModelPreset] = {
    p.model: p
    for p in [
        # 實測：1.8B 用官方前文範本時 11/12 次把前文或指令一起輸出；7B 在實測句組中
        # 也重現把前一句一起翻出來（ja17），所以兩者都不帶前文。
        _hunyuan("hy-mt1.5-1.8b:latest", "HY-MT1.5 1.8B", 1.9, "HY-MT1.5-1.8B", False),
        _hunyuan("hy-mt1.5-7b:latest", "HY-MT1.5 7B", 4.6, "HY-MT1.5-7B", False),
        _qwen("qwen3.5:4b", "Qwen3.5 4B", 3.3, "Qwen3.5-4B"),
        _qwen("qwen3.5:9b", "Qwen3.5 9B", 6.6, "Qwen3.5-9B"),
        ModelPreset(
            model="hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
            # 非商用標示由 UI 依 commercial_use 以介面語系顯示，名稱不帶任何語言的說明。
            label="Sakura-GalTransl 7B v3",
            prompt_style="sakura",
            temperature=0.3,
            top_p=0.8,
            presence_penalty=None,
            reasoning_effort=None,
            # 官方只訓練日翻簡中；繁中由 OpenCC 轉換，所以兩種中文都能當譯文。
            languages=frozenset({"ja", "zh-TW", "zh-CN"}),
            pairs=SAKURA_PAIRS,
            outputs_simplified=True,
            supports_context=False,  # 官方 [History] 要的是前文譯文，格式不合
            approx_vram_gb=4.3,
            license="CC BY-NC-SA 4.0",
            license_url="https://creativecommons.org/licenses/by-nc-sa/4.0/",
            commercial_use=False,
        ),
    ]
}


def normalize_model_name(name: str) -> str:
    """補上 Ollama 預設的 `:latest` 標籤，讓 "hy-mt1.5-7b" 與 "hy-mt1.5-7b:latest" 相等。"""
    name = name.strip()
    return name if ":" in name.rsplit("/", 1)[-1] else f"{name}:latest"


def preset_for_model(model: str) -> ModelPreset:
    """回傳模型的預設組；不認得的模型用通用格式（不送思考控制欄位）。"""
    preset = PRESETS.get(model) or PRESETS.get(normalize_model_name(model))
    if preset is not None:
        return preset
    return ModelPreset(
        model=model,
        label=model,
        prompt_style="generic",
        temperature=0.3,
        top_p=None,
        presence_penalty=None,
        reasoning_effort=None,
        languages=ALL_LANGUAGE_CODES,
        outputs_simplified=False,
        supports_context=True,
        approx_vram_gb=0.0,
        license="unknown",
        license_url="",
        commercial_use=False,
    )


def supports(preset: ModelPreset, source_language: str, target_language: str) -> bool:
    """這個模型能否把 `source_language` 翻成 `target_language`。

    原文可以是 Whisper 或清單語言碼，譯文是清單語言碼。語言碼不合法、或兩者 Whisper
    語言相同（不需要翻譯模型，例如 zh-CN → zh-TW）時回傳 False，不丟例外。
    """
    try:
        normalize_pair(source_language, target_language, preset.languages, preset.pairs)
    except InvalidRequestError:
        return False
    return True


@dataclass(frozen=True)
class TranslationModelChoice:
    """即時路徑（fast）與完整路徑（final）各用哪個翻譯模型。"""

    fast: str
    final: str


@dataclass(frozen=True)
class RecommendationTier:
    """可用 VRAM 至少 `min_vram_gb` 時的推薦順序（越前面越優先）。"""

    min_vram_gb: float
    fast: tuple[str, ...]
    final: tuple[str, ...]


# 由高到低；第一個符合 min_vram_gb 的等級生效。0 GB 等級也涵蓋沒有 GPU 的情況。
# 依 experiments/translate_benchmark（RTX 5070）：GPU 上每個模型的總延遲都在 0.3 秒內，
# 所以即時與完整路徑用同一個模型，只載入一份、省 VRAM；品質 Qwen3.5 9B > 4B >
# HY-MT 7B > HY-MT 1.8B（日文口語），Qwen3.5 也沒有地區限制。門檻含 KV cache 的餘裕。
# CPU 沒有實測，先選最小的 HY-MT 1.8B。
_ALL_BY_QUALITY = (
    "qwen3.5:9b",
    "qwen3.5:4b",
    "hy-mt1.5-7b:latest",
    "hy-mt1.5-1.8b:latest",
)
RECOMMENDATION_TIERS: tuple[RecommendationTier, ...] = (
    RecommendationTier(min_vram_gb=7.5, fast=_ALL_BY_QUALITY, final=_ALL_BY_QUALITY),
    RecommendationTier(
        min_vram_gb=4.0,
        fast=("qwen3.5:4b", "hy-mt1.5-1.8b:latest"),
        final=("qwen3.5:4b", "hy-mt1.5-1.8b:latest"),
    ),
    RecommendationTier(
        min_vram_gb=0.0,
        fast=("hy-mt1.5-1.8b:latest", "qwen3.5:4b"),
        final=("hy-mt1.5-1.8b:latest", "qwen3.5:4b"),
    ),
)


class NoSupportingModelError(ValueError):
    """這個 VRAM 等級的推薦模型都不支援要求的語言組合。

    Attributes:
        pairs: 要求的（原文, 譯文）組合。
    """

    def __init__(self, pairs: tuple[tuple[str, str], ...]) -> None:
        shown = ", ".join(f"{s} -> {t}" for s, t in pairs)
        super().__init__(f"no recommended translation model supports {shown}")
        self.pairs = pairs


def recommend_translation_models(
    vram_budget_gb: float | None,
    installed: Collection[str] | None = None,
    allow_restricted: bool = True,
    pairs: Collection[tuple[str, str]] | None = None,
) -> TranslationModelChoice:
    """依可用 VRAM 推薦翻譯模型。

    Args:
        vram_budget_gb: 可分給翻譯模型的 VRAM（GB，已扣掉 ASR）；None 表示沒有 GPU。
        installed: 已安裝的模型名稱；None 表示不檢查。都沒裝時回傳該等級第一順位，
            讓 UI 提示下載。
        allow_restricted: False 時排除有地區限制（例如使用者在歐盟）的模型。
            非商用授權的模型一律不推薦。
        pairs: 必須全部支援的（原文, 譯文）組合（見 `supports()`；原文可為 Whisper 或
            清單語言碼）；None 表示不看語言。口譯時兩個方向都要列入。不支援的模型直接
            略過，順序仍依 VRAM 等級（不跨等級找：更大的模型放不下，更小的品質較差）。

    Raises:
        ValueError: VRAM 為負數或不是有限數。
        NoSupportingModelError: 這個等級沒有模型支援 `pairs`。
    """
    budget = 0.0 if vram_budget_gb is None else float(vram_budget_gb)
    if not math.isfinite(budget) or budget < 0:
        raise ValueError(
            f"vram_budget_gb must be a finite number >= 0, got {vram_budget_gb!r}"
        )
    tier = next(t for t in RECOMMENDATION_TIERS if budget >= t.min_vram_gb)
    available = (
        None if installed is None else {normalize_model_name(m) for m in installed}
    )

    required = None if pairs is None else tuple(pairs)

    def pick(order: tuple[str, ...]) -> str:
        allowed = [
            m
            for m in order
            if PRESETS[m].commercial_use
            and (allow_restricted or not PRESETS[m].excluded_regions)
            and (
                required is None
                or all(supports(PRESETS[m], src, tgt) for src, tgt in required)
            )
        ]
        if not allowed:
            raise NoSupportingModelError(required or ())
        if available is not None:
            for model in allowed:
                if model in available:
                    return model
        return allowed[0]

    return TranslationModelChoice(fast=pick(tier.fast), final=pick(tier.final))
