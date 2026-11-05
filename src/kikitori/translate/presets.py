"""翻譯模型預設組：每個已知模型的提示詞格式、取樣參數、授權與 VRAM 推薦順序。

數值來源是各模型官方 model card；推薦順序依 `experiments/translate_benchmark/`
的實測決定。hardware 模組用 `recommend_translation_models()` 挑預設模型，
UI 用 `ModelPreset` 的授權欄位顯示限制。本專案只以名稱引用使用者自行安裝的
模型，不散布權重。
"""

import math
from collections.abc import Collection
from dataclasses import dataclass

from kikitori.translate.languages import SUPPORTED_PAIRS
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
        language_pairs: 支援的（原文, 譯文）組合。
        outputs_simplified: 模型本身輸出簡體（靠 OpenCC 轉繁體）。
        supports_context: 是否把前文放進提示詞；小模型會把前文一起翻出來時設 False。
        approx_vram_gb: 權重大小（GB），推估 VRAM 用；KV cache 另計。
        license: 授權名稱。
        license_url: 授權全文網址。
        commercial_use: 授權是否允許商業用途。
        excluded_regions: 授權不適用的地區（"EU"、"UK"、"KR"）。
    """

    model: str
    label: str
    prompt_style: PromptStyle
    temperature: float
    top_p: float | None
    presence_penalty: float | None
    reasoning_effort: str | None
    language_pairs: frozenset[tuple[str, str]]
    outputs_simplified: bool
    supports_context: bool
    approx_vram_gb: float
    license: str
    license_url: str
    commercial_use: bool
    excluded_regions: tuple[str, ...] = ()


_HY_LICENSE = "Tencent HY Community License"
_HY_REGIONS = ("EU", "UK", "KR")


def _hunyuan(
    model: str, label: str, vram: float, repo: str, supports_context: bool
) -> ModelPreset:
    # model card 建議 top_k 20、top_p 0.6、repetition_penalty 1.05、temperature 0.7；
    # OpenAI 相容 API 沒有 top_k／repetition_penalty，由 Ollama 模型檔的預設值負責。
    return ModelPreset(
        model=model,
        label=label,
        prompt_style="hunyuan_mt",
        temperature=0.7,
        top_p=0.6,
        presence_penalty=None,
        reasoning_effort=None,
        language_pairs=SUPPORTED_PAIRS,
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
    # 關閉。取樣參數用 model card 的非思考模式建議值。
    return ModelPreset(
        model=model,
        label=label,
        prompt_style="generic",
        temperature=0.7,
        top_p=0.8,
        presence_penalty=1.5,
        reasoning_effort="none",
        language_pairs=SUPPORTED_PAIRS,
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
            label="Sakura-GalTransl 7B v3（非商用）",
            prompt_style="sakura",
            temperature=0.3,
            top_p=0.8,
            presence_penalty=None,
            reasoning_effort=None,
            language_pairs=frozenset({("ja", "zh-TW")}),
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
        language_pairs=SUPPORTED_PAIRS,
        outputs_simplified=False,
        supports_context=True,
        approx_vram_gb=0.0,
        license="unknown",
        license_url="",
        commercial_use=False,
    )


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


def recommend_translation_models(
    vram_budget_gb: float | None,
    installed: Collection[str] | None = None,
    allow_restricted: bool = True,
) -> TranslationModelChoice:
    """依可用 VRAM 推薦翻譯模型。

    Args:
        vram_budget_gb: 可分給翻譯模型的 VRAM（GB，已扣掉 ASR）；None 表示沒有 GPU。
        installed: 已安裝的模型名稱；None 表示不檢查。都沒裝時回傳該等級第一順位，
            讓 UI 提示下載。
        allow_restricted: False 時排除有地區限制（例如使用者在歐盟）的模型。
            非商用授權的模型一律不推薦。

    Raises:
        ValueError: VRAM 為負數或不是有限數。
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

    def pick(order: tuple[str, ...]) -> str:
        allowed = [
            m
            for m in order
            if PRESETS[m].commercial_use
            and (allow_restricted or not PRESETS[m].excluded_regions)
        ]
        if available is not None:
            for model in allowed:
                if model in available:
                    return model
        return allowed[0]

    return TranslationModelChoice(fast=pick(tier.fast), final=pick(tier.final))
