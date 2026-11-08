"""彙整非語音辨識結果：各語言的候選幻覺片語，以及套用片語表前後的幻覺率。

角色：暫時性實驗（不屬於正式程式碼），由 `menu.py` 呼叫。讀 `collect.py` 寫在
`results/` 的原始輸出，以「目前的」`kikitori.asr.rejection_reason()` 重新判定
（調整過濾規則後不必重跑模型），再模擬加上 `lexicon.json` 的依語言片語表後的結果。

定義：
- 幻覺 = 非語音片段的輸出正規化後非空，而且通過目前的過濾（會變成字幕）。
- no_speech：`no_speech_reliable=False` 的模型（large-v3-turbo）與 app 相同，改用
  推薦組合中即時模型（small、即時解碼）對同一段、同一語言的 no_speech。
- 片語表的模擬（與 `kikitori.asr.filtering` 相同的語意）：`hard` 片語取**所有語言**
  （含不分語言的 "*"）的聯集，不論辨識語言，整句相等就濾掉；`soft` 片語只比對辨識語言
  與 "*" 的表，整句相等且 no_speech > `FilterConfig.soft_no_speech_threshold` 才濾掉。
"""

import json
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from kikitori.asr import MODELS, FilterConfig, Transcript, rejection_reason
from kikitori.asr.filtering import normalize_for_match

HERE = Path(__file__).resolve().parent
RESULTS_DIR = HERE / "results"
LEXICON_PATH = HERE / "lexicon.json"
SUMMARY_PATH = RESULTS_DIR / "summary.md"
CANDIDATES_PATH = RESULTS_DIR / "candidates.json"
NO_SPEECH_SUBSTITUTE_MODEL = "small"
# 候選片語：至少出現在這麼多段不同的非語音片段
MIN_SEGMENTS = 3
TOP_PER_LANGUAGE = 12
ANY_LANGUAGE = "*"
MODEL_ORDER = ("small", "large-v3-turbo", "large-v3", "base")
LENGTHS = (1, 2, 4, 6, 10)


@dataclass(frozen=True)
class Row:
    """一次解碼（片段 × 模型 × 模式 × 語言）的結果與判定。"""

    segment: str
    model: str
    mode: str
    language: str
    text: str
    key: str
    no_speech: float
    avg_logprob: float
    seconds: int
    kind: str
    vad_passed: bool
    baseline: str | None
    after: str | None

    @property
    def config(self) -> str:
        return f"{self.model} {self.mode}"

    @property
    def leaked_before(self) -> bool:
        return bool(self.key) and self.baseline is None

    @property
    def leaked_after(self) -> bool:
        return bool(self.key) and self.after is None


def load_lexicon(path: Path = LEXICON_PATH) -> dict[str, dict[str, frozenset[str]]]:
    """讀取片語表並正規化；不存在時回傳空表。"""
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    table: dict[str, dict[str, frozenset[str]]] = {}
    for language, entry in data["languages"].items():
        table[language] = {
            kind: frozenset(normalize_for_match(p) for p in entry.get(kind, [])) - {""}
            for kind in ("hard", "soft")
        }
    return table


def lexicon_reason(
    key: str,
    language: str,
    no_speech: float,
    lexicon: dict[str, dict[str, frozenset[str]]],
    soft_threshold: float,
) -> str | None:
    """片語表的判定；None 表示片語表不濾。

    hard 不看語言（Whisper 對套話的語言標籤不可靠），soft 只看辨識語言與 "*"。
    """
    if not key:
        return None
    if any(key in entry["hard"] for entry in lexicon.values()):
        return "lexicon_hard"
    for code in (language, ANY_LANGUAGE):
        entry = lexicon.get(code)
        if entry is not None and key in entry["soft"] and no_speech > soft_threshold:
            return "lexicon_soft"
    return None


def load_rows(
    results_dir: Path = RESULTS_DIR, lexicon_path: Path = LEXICON_PATH
) -> list[Row]:
    """讀取所有原始輸出，以目前的過濾規則與片語表判定。"""
    metas = {
        m["id"]: m
        for m in json.loads((results_dir / "segments.json").read_text("utf-8"))
    }
    raw: list[dict[str, Any]] = []
    for path in sorted(results_dir.glob("raw-*.jsonl")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                raw.append(json.loads(line))
    substitute = {
        (r["segment"], r["language"]): float(r["no_speech"])
        for r in raw
        if r["model"] == NO_SPEECH_SUBSTITUTE_MODEL and r["mode"] == "fast"
    }
    lexicon = load_lexicon(lexicon_path)
    soft_threshold = float(FilterConfig().soft_no_speech_threshold)
    rows: list[Row] = []
    for r in raw:
        no_speech = float(r["no_speech"])
        if not MODELS[r["model"]].no_speech_reliable:
            no_speech = substitute.get((r["segment"], r["language"]), no_speech)
        transcript = Transcript(
            text=r["text"],
            language=r["language"],
            language_probability=1.0,
            no_speech_probability=no_speech,
            avg_logprob=float(r["avg_logprob"]),
        )
        key = normalize_for_match(r["text"])
        baseline = rejection_reason(transcript)
        after = baseline or lexicon_reason(
            key, r["language"], no_speech, lexicon, soft_threshold
        )
        meta = metas[r["segment"]]
        rows.append(
            Row(
                segment=r["segment"],
                model=r["model"],
                mode=r["mode"],
                language=r["language"],
                text=r["text"],
                key=key,
                no_speech=no_speech,
                avg_logprob=float(r["avg_logprob"]),
                seconds=int(meta["seconds"]),
                kind=str(meta["kind"]),
                vad_passed=bool(meta["vad_passed"]),
                baseline=baseline,
                after=after,
            )
        )
    return rows


def _configs(rows: Iterable[Row]) -> list[str]:
    present = {r.config for r in rows}
    return [
        f"{m} {mode}"
        for m in MODEL_ORDER
        for mode in ("fast", "final")
        if f"{m} {mode}" in present
    ]


def _rate(rows: list[Row], leaked: Callable[[Row], bool]) -> str:
    if not rows:
        return "—"
    return f"{100 * sum(map(leaked, rows)) / len(rows):.1f}"


def _before_after(rows: list[Row]) -> str:
    if not rows:
        return "—"
    before = _rate(rows, lambda r: r.leaked_before)
    after = _rate(rows, lambda r: r.leaked_after)
    return f"{before} → {after}"


def _table(
    title_col: str,
    groups: list[tuple[str, Callable[[Row], bool]]],
    rows: list[Row],
    configs: list[str],
) -> list[str]:
    lines = [
        f"| {title_col} | " + " | ".join(configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    by_config: dict[str, list[Row]] = defaultdict(list)
    for r in rows:
        by_config[r.config].append(r)
    for label, keep in groups:
        cells = [_before_after([r for r in by_config[c] if keep(r)]) for c in configs]
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    return lines


def cross_validated_leaks(rows: list[Row]) -> dict[tuple[str, str], bool]:
    """2 折交叉驗證：片段依編號奇偶分兩半，每半只用「在另一半至少 2 段片段出現過」的片語。

    片語表是看著同一批資料整理的，直接算「套用後」會偏樂觀；這裡估計片語表對
    沒看過的非語音片段還有多少效果。回傳 {(片段, 解碼識別): 是否漏網}。
    """
    lexicon = load_lexicon()
    soft_threshold = float(FilterConfig().soft_no_speech_threshold)
    seen: dict[int, dict[tuple[str, str], set[str]]] = {0: {}, 1: {}}
    for r in rows:
        if r.key:
            fold = int(r.segment[1:]) % 2
            seen[fold].setdefault((r.language, r.key), set()).add(r.segment)
            seen[fold].setdefault((ANY_LANGUAGE, r.key), set()).add(r.segment)
    restricted: dict[int, dict[str, dict[str, frozenset[str]]]] = {}
    for fold in (0, 1):
        other = seen[1 - fold]
        restricted[fold] = {
            code: {
                "hard": frozenset(
                    k
                    for k in entry["hard"]
                    if len(other.get((ANY_LANGUAGE, k), ())) >= 2
                ),
                "soft": frozenset(
                    k
                    for k in entry["soft"]
                    if len(other.get((code, k), ()))
                    + len(
                        other.get((ANY_LANGUAGE, k), ()) if code == ANY_LANGUAGE else ()
                    )
                    >= 2
                ),
            }
            for code, entry in lexicon.items()
        }
    result: dict[tuple[str, str], bool] = {}
    for r in rows:
        fold = int(r.segment[1:]) % 2
        reason = r.baseline or lexicon_reason(
            r.key, r.language, r.no_speech, restricted[fold], soft_threshold
        )
        result[(r.segment, f"{r.config} {r.language}")] = bool(r.key) and reason is None
    return result


def candidates(rows: list[Row]) -> dict[str, list[dict[str, Any]]]:
    """每個語言出現在 ≥ `MIN_SEGMENTS` 段片段的輸出，依片段數排序。"""
    lexicon = load_lexicon()
    grouped: dict[tuple[str, str], list[Row]] = defaultdict(list)
    for r in rows:
        if r.key:
            grouped[(r.language, r.key)].append(r)
    result: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (language, key), items in grouped.items():
        segments = {r.segment for r in items}
        if len(segments) < MIN_SEGMENTS:
            continue
        entry = lexicon.get(language, {"hard": frozenset(), "soft": frozenset()})
        status = (
            "hard" if key in entry["hard"] else "soft" if key in entry["soft"] else ""
        )
        result[language].append(
            {
                "display": Counter(r.text for r in items).most_common(1)[0][0],
                "key": key,
                "segments": len(segments),
                "segments_leaked": len({r.segment for r in items if r.leaked_before}),
                "vad_segments": len({r.segment for r in items if r.vad_passed}),
                "models": sorted({r.model for r in items}),
                "rows": len(items),
                "median_no_speech": sorted(r.no_speech for r in items)[len(items) // 2],
                "lexicon": status,
            }
        )
    for items in result.values():
        items.sort(key=lambda c: (-c["segments"], c["key"]))
    return dict(result)


def write_summary(results_dir: Path = RESULTS_DIR) -> Path:
    """產生 `results/summary.md` 與 `results/candidates.json`，回傳 summary 路徑。"""
    rows = load_rows(results_dir)
    if not rows:
        raise ValueError("no results yet")
    configs = _configs(rows)
    segments = {r.segment for r in rows}
    vad_segments = {r.segment for r in rows if r.vad_passed}
    languages = list(dict.fromkeys(r.language for r in rows))
    out: list[str] = [
        f"# 非語音幻覺率（{date.today().isoformat()}）",
        "",
        (
            f"片段 {len(segments)} 段（VAD 通過 {len(vad_segments)} 段）、語言 {len(languages)} 種、"
            f"解碼 {len(rows)} 次。格式：套用片語表前 → 後（%，非語音片段輸出非空且通過過濾的比例）。"
        ),
        "",
        "各設定跑過的片段數（沒跑完的模型是隨機子樣本）："
        + "、".join(
            f"{c} {len({r.segment for r in rows if r.config == c})}" for c in configs
        ),
        "",
        "## 整體",
        "",
    ]
    out += _table(
        "片段",
        [("全部", lambda r: True), ("只看 VAD 通過", lambda r: r.vad_passed)],
        rows,
        configs,
    )
    out += ["", "## 依片段長度（全部片段）", ""]
    out += _table(
        "長度",
        [(f"{s} s", lambda r, s=s: r.seconds == s) for s in LENGTHS],
        rows,
        configs,
    )
    out += ["", "## 依片段長度（只看 VAD 通過）", ""]
    out += _table(
        "長度",
        [(f"{s} s", lambda r, s=s: r.seconds == s and r.vad_passed) for s in LENGTHS],
        rows,
        configs,
    )
    kinds = list(dict.fromkeys(r.kind for r in rows))
    out += ["", "## 依素材種類（全部片段）", ""]
    out += _table(
        "種類", [(k, lambda r, k=k: r.kind == k) for k in kinds], rows, configs
    )
    out += ["", "## 依語言（全部片段）", ""]
    out += _table(
        "語言",
        [(lang, lambda r, g=lang: r.language == g) for lang in languages],
        rows,
        configs,
    )
    out += ["", "## 依語言（只看 VAD 通過）", ""]
    out += _table(
        "語言",
        [
            (lang, lambda r, g=lang: r.language == g and r.vad_passed)
            for lang in languages
        ],
        rows,
        configs,
    )

    cv = cross_validated_leaks(rows)
    out += [
        "",
        "## 交叉驗證（片段依編號奇偶分兩半，片語只用另一半出現 ≥ 2 段的）",
        "",
        "格式：套用前 → 套用後（同一批資料）→ 套用後（交叉驗證）。",
        "",
        "| 片段 | " + " | ".join(configs) + " |",
        "|---|" + "---|" * len(configs),
    ]
    for label, keep in (
        ("全部", lambda r: True),
        ("只看 VAD 通過", lambda r: r.vad_passed),
    ):
        cells = []
        for c in configs:
            group = [r for r in rows if r.config == c and keep(r)]
            leaked_cv = sum(cv[(r.segment, f"{r.config} {r.language}")] for r in group)
            cv_rate = f"{100 * leaked_cv / len(group):.1f}" if group else "—"
            cells.append(f"{_before_after(group)} → {cv_rate}")
        out.append(f"| {label} | " + " | ".join(cells) + " |")

    found = candidates(rows)
    CANDIDATES_PATH.write_text(json.dumps(found, ensure_ascii=False, indent=1), "utf-8")
    out += [
        "",
        f"## 候選片語（每語言前 {TOP_PER_LANGUAGE} 名；出現在 ≥ {MIN_SEGMENTS} 段片段）",
        "",
        (
            "片段數 = 產生此輸出的不同片段數（跨模型、模式）；漏網 = 其中至少一次通過目前過濾的片段數；"
            "VAD = 其中 VAD 通過的片段數；表 = 目前 lexicon.json 的分類。"
        ),
    ]
    for language in languages:
        items = found.get(language, [])
        out += [
            "",
            f"### {language}",
            "",
            "| 輸出 | 片段數 | 漏網 | VAD | 模型 | no_speech 中位 | 表 |",
            "|---|---|---|---|---|---|---|",
        ]
        for c in items[:TOP_PER_LANGUAGE]:
            display = c["display"].replace("|", "\\|")
            out.append(
                f"| {display} | {c['segments']} | {c['segments_leaked']} "
                f"| {c['vad_segments']} | {', '.join(c['models'])} "
                f"| {c['median_no_speech']:.2f} | {c['lexicon']} |"
            )
    SUMMARY_PATH.write_text("\n".join(out) + "\n", "utf-8")
    return SUMMARY_PATH
