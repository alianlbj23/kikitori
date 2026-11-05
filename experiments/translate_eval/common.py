"""翻譯評測（Milestone 16）的共用定義：路徑、資料集、受測設定與 JSON 讀寫。

只用標準庫：專案環境的選單與翻譯執行器、以及隔離環境的計分腳本都能共用同一份
定義（計分腳本以 `python -I` 執行，不依賴 kikitori 套件）。
"""

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
# data/ 與 results/ 都在 .gitignore：資料集原文有著作權，不進 repo。
DATA_DIR = HERE / "data"
RESULTS_DIR = HERE / "results"
OUTPUTS_DIR = RESULTS_DIR / "outputs"
SCORING_DIR = RESULTS_DIR / "scoring"
LATENCY_DIR = RESULTS_DIR / "latency"
VTUBER_DIR = RESULTS_DIR / "vtuber"

# 計分用的固定亂數種子（抽樣與 bootstrap 都用它，結果可重現）。
SEED = 12345
BOOTSTRAP_SAMPLES = 1000

# 與正式程式相同：只帶前兩句原文當前文（pipeline 的預設行為）。
CONTEXT_LINES = 2


@dataclass(frozen=True)
class EvalSet:
    """一個評測資料集。

    Attributes:
        name: 檔名用的代號（data/<name>.json）。
        label: 表格顯示名稱。
        target: kikitori 的譯文語言碼（zh-TW 或 en）。
        has_documents: 有文件結構（可比較有無前文）。
        join: 文件級計分時，把各句譯文接起來用的分隔字串。
    """

    name: str
    label: str
    target: str
    has_documents: bool
    join: str


EVAL_SETS: dict[str, EvalSet] = {
    s.name: s
    for s in [
        EvalSet("zh_speech", "WMT25 ja→zh speech（YouTube 轉寫）", "zh-TW", True, ""),
        EvalSet("zh_social", "WMT25 ja→zh social（Mastodon 貼文）", "zh-TW", True, ""),
        EvalSet("en_covost", "CoVoST 2 ja→en test", "en", False, " "),
        EvalSet("en_bsd", "BSD ja→en test（日文原創對話）", "en", True, " "),
    ]
}


@dataclass(frozen=True)
class SystemConfig:
    """一組受測設定：模型＋溫度＋是否帶前文。

    Attributes:
        key: 結果檔名用的代號。
        model: Ollama 模型名稱（必須在 kikitori.translate.PRESETS 內）。
        temperature: None 表示用正式預設組的溫度。
        context: 是否把同一文件的前兩句原文放進 `TranslationRequest.context`。
        label: 表格顯示名稱。
        zh_only: 只支援日翻中（Sakura）。
        reference_only: 非商用授權，只當參考點，不列入推薦。
        glossary: 用正式的內建術語表（`kikitori.translate.Glossary()`；Milestone 16）。
        guard: 包上正式的前文保險（`ContextFallbackTranslator`；Milestone 16）。
    """

    key: str
    model: str
    temperature: float | None
    context: bool
    label: str
    zh_only: bool = False
    reference_only: bool = False
    glossary: bool = False
    guard: bool = False


SYSTEMS: list[SystemConfig] = [
    # 現行正式設定：Qwen 預設組（temperature 0.7、top_p 0.8、presence_penalty 1.5）＋前文。
    SystemConfig(
        "q9-preset-ctx", "qwen3.5:9b", None, True, "Qwen3.5 9B｜預設 t=0.7｜前文"
    ),
    SystemConfig("q9-t0-ctx", "qwen3.5:9b", 0.0, True, "Qwen3.5 9B｜t=0｜前文"),
    SystemConfig("q9-t0-noctx", "qwen3.5:9b", 0.0, False, "Qwen3.5 9B｜t=0｜無前文"),
    SystemConfig(
        "q4-preset-ctx", "qwen3.5:4b", None, True, "Qwen3.5 4B｜預設 t=0.7｜前文"
    ),
    SystemConfig("q4-t0-ctx", "qwen3.5:4b", 0.0, True, "Qwen3.5 4B｜t=0｜前文"),
    SystemConfig("q4-t0-noctx", "qwen3.5:4b", 0.0, False, "Qwen3.5 4B｜t=0｜無前文"),
    # HY-MT 的預設組本來就不帶前文（會把前文一起翻出來，見 translate_benchmark）。
    SystemConfig(
        "hy7-preset", "hy-mt1.5-7b:latest", None, False, "HY-MT1.5 7B｜預設 t=0.7"
    ),
    SystemConfig("hy7-t0", "hy-mt1.5-7b:latest", 0.0, False, "HY-MT1.5 7B｜t=0"),
    SystemConfig(
        "hy18-preset", "hy-mt1.5-1.8b:latest", None, False, "HY-MT1.5 1.8B｜預設 t=0.7"
    ),
    SystemConfig("hy18-t0", "hy-mt1.5-1.8b:latest", 0.0, False, "HY-MT1.5 1.8B｜t=0"),
    SystemConfig(
        "sakura-preset",
        "hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS",
        None,
        False,
        "Sakura-GalTransl 7B（非商用，僅參考）",
        zh_only=True,
        reference_only=True,
    ),
    # Milestone 16 的正式設定：預設組（Qwen 溫度已改 0）、前文、前文保險；術語表關／開。
    # 與 q9-t0-ctx 的差別只有前文保險與後處理（選擇性繁簡轉換、特殊 token），用來確認
    # 新程式沒有改變譯文；術語表的效果看 q9-m16 → q9-m16-gloss。
    SystemConfig(
        "q9-m16",
        "qwen3.5:9b",
        None,
        True,
        "Qwen3.5 9B｜M16 正式（t=0、前文、前文保險）｜術語表關",
        guard=True,
    ),
    SystemConfig(
        "q9-m16-gloss",
        "qwen3.5:9b",
        None,
        True,
        "Qwen3.5 9B｜M16 正式（t=0、前文、前文保險）｜術語表開",
        glossary=True,
        guard=True,
    ),
]
SYSTEMS_BY_KEY: dict[str, SystemConfig] = {s.key: s for s in SYSTEMS}
GLOSSARY_SUFFIX = "-gloss"


def with_glossary(system: SystemConfig) -> SystemConfig:
    """同一組設定改用內建術語表（選單的術語表開關）；key 加上 `-gloss`。"""
    if system.glossary:
        return system
    key = system.key + GLOSSARY_SUFFIX
    return SYSTEMS_BY_KEY.get(key) or replace(
        system, key=key, label=system.label + "｜術語表開", glossary=True
    )


def find_system(key: str) -> SystemConfig | None:
    """依 key 找設定；`<key>-gloss` 是選單術語表開關產生的變體。"""
    if key in SYSTEMS_BY_KEY:
        return SYSTEMS_BY_KEY[key]
    if key.endswith(GLOSSARY_SUFFIX):
        base = SYSTEMS_BY_KEY.get(key.removesuffix(GLOSSARY_SUFFIX))
        return None if base is None else with_glossary(base)
    return None


# paired bootstrap 的基準：現行正式設定。
BASELINE_KEY = "q9-preset-ctx"

# 事先決定要檢定的成對比較（A, B）：報告 B − A 的差值與 p 值。
CONTRASTS: list[tuple[str, str]] = [
    # 溫度：預設 0.7 → 0
    ("q9-preset-ctx", "q9-t0-ctx"),
    ("q4-preset-ctx", "q4-t0-ctx"),
    ("hy7-preset", "hy7-t0"),
    ("hy18-preset", "hy18-t0"),
    # 前文：無 → 有（溫度 0）
    ("q9-t0-noctx", "q9-t0-ctx"),
    ("q4-t0-noctx", "q4-t0-ctx"),
    # 模型之間（各自較好的設定）
    ("q9-t0-ctx", "hy7-t0"),
    ("q4-t0-ctx", "q9-t0-ctx"),
    ("q4-t0-ctx", "hy7-t0"),
    ("hy18-t0", "q4-t0-ctx"),
    # Milestone 16：新程式（前文保險、後處理）不改變結果；術語表關 → 開
    ("q9-t0-ctx", "q9-m16"),
    ("q9-m16", "q9-m16-gloss"),
]


def system_applies(system: SystemConfig, eval_set: EvalSet) -> bool:
    """這組設定要不要跑這個資料集。

    沒有文件結構的資料集（CoVoST）前文永遠是空的，「無前文」設定與「有前文」
    完全相同，不重複跑。
    """
    if system.zh_only and not eval_set.target.startswith("zh"):
        return False
    return eval_set.has_documents or not system.key.endswith("-noctx")


def read_json(path: Path) -> Any:
    """讀 UTF-8 JSON。"""
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, data: Any) -> None:
    """寫 UTF-8 JSON（先寫暫存檔再改名，避免中斷時留下半個檔案）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    """讀 JSONL；檔案不存在時回傳空清單。最後一行不完整（中斷）時略過該行。"""
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    lines = path.read_text(encoding="utf-8").splitlines()
    for i, line in enumerate(lines):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            if i == len(lines) - 1:
                print(f"略過 {path.name} 最後一行不完整的紀錄（上次執行中斷）")
                continue
            raise
    return rows


def set_path(name: str) -> Path:
    """資料集檔案路徑。"""
    return DATA_DIR / f"{name}.json"


def output_path(system_key: str, set_name: str) -> Path:
    """某設定在某資料集的逐句輸出（JSONL，可續跑）。"""
    return OUTPUTS_DIR / system_key / f"{set_name}.jsonl"
