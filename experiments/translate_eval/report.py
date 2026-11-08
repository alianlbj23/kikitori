"""計分輸入、錯誤統計與 Markdown 報表（專案環境）。

- `build_scoring_inputs()`：把逐句輸出組成計分單位（speech 依文件接成整段），中文兩邊
  都用 OpenCC t2s 統一成簡體，寫 `results/scoring/set_<資料集>.json` 給隔離環境的
  score.py／score_comet.py 讀。
- `error_counts()`：空輸出、錯誤語言、簡體殘留、附加說明等可疑輸出的計數。
- `render_report()`：讀計分結果與延遲，產生 `results/report.md`。
"""

import re
from typing import Any

import opencc
from common import (
    BASELINE_KEY,
    CONTRASTS,
    EVAL_SETS,
    LATENCY_DIR,
    SCORING_DIR,
    SYSTEMS,
    SYSTEMS_BY_KEY,
    output_path,
    read_json,
    read_jsonl,
    set_path,
    system_applies,
    write_json,
)
from datasets import wmt25_systems_path
from run import latency_summary

from kikitori.translate.postprocess import simplified_characters

_T2S = opencc.OpenCC("t2s.json")

# 假名（不含長音符「ー」與中點「・」，這兩個在中文譯文的外來語中也會出現）。
_KANA = re.compile(r"[ぁ-ゖゝ-ゟァ-ヺヽ-ヿｦ-ﾝ]")
_HAN = re.compile(r"[㐀-䶿一-鿿豈-﫿]")
_LATIN = re.compile(r"[A-Za-z]")
# 模型自己加的說明、標籤或提示詞回顯（正式後處理只去掉開頭的「譯文：」等標籤）。
_EXPLANATION = re.compile(
    r"(注[:：]|註[:：]|譯註|译注|翻譯[:：]|翻译[:：]|譯文[:：]|译文[:：]|原文[:：]|（註|（注"
    r"|\bNote[:：]|\(Note|\bTranslation[:：]|\bLiteral(ly)?\b|\bOr:"
    # 把提示詞本身吐出來，或回答「原文格式不對」（實測 HY-MT 1.8B 會這樣）。
    r"|Translate the following|without additional explanation|将以下文本|將以下文本"
    r"|isn.t properly formatted)",
    re.IGNORECASE,
)


def _complete_outputs(
    system_key: str, set_name: str
) -> dict[str, dict[str, Any]] | None:
    """某設定在某資料集的輸出；有句子還沒翻完就回傳 None。"""
    segments = read_json(set_path(set_name))["segments"]
    rows = {r["id"]: r for r in read_jsonl(output_path(system_key, set_name))}
    if any(s["id"] not in rows for s in segments):
        return None
    return rows


def build_scoring_inputs() -> list[str]:
    """產生計分輸入；回傳每個資料集納入的系統摘要（給選單顯示）。"""
    lines = []
    for name, eval_set in EVAL_SETS.items():
        if not set_path(name).exists():
            continue
        data = read_json(set_path(name))
        is_zh = eval_set.target.startswith("zh")
        units = [
            {
                "id": u["id"],
                "src": u["src"],
                "ref": _T2S.convert(u["ref"]) if is_zh else u["ref"],
            }
            for u in data["units"]
        ]
        systems: dict[str, list[str]] = {}
        for system in SYSTEMS:
            if not system_applies(system, eval_set):
                continue
            rows = _complete_outputs(system.key, name)
            if rows is None:
                continue
            hyps = []
            for u in data["units"]:
                text = eval_set.join.join(
                    rows[s]["output"] for s in u["seg_ids"] if rows[s]["output"]
                )
                hyps.append(_T2S.convert(text) if is_zh else text)
            systems[system.key] = hyps
        if name == "zh_speech" and wmt25_systems_path().exists():
            # 校準：WMT25 官方系統（整篇一次翻譯），key 加上 wmt25: 前綴以示區別。
            for wmt_name, docs in read_json(wmt25_systems_path()).items():
                systems[f"wmt25:{wmt_name}"] = [
                    _T2S.convert(docs[u["id"]]) for u in data["units"]
                ]
        write_json(
            SCORING_DIR / f"set_{name}.json",
            {
                "set": name,
                "target": "zh-CN" if is_zh else eval_set.target,
                "baseline": BASELINE_KEY,
                "contrasts": [
                    [a, b] for a, b in CONTRASTS if a in systems and b in systems
                ],
                "units": units,
                "systems": systems,
            },
        )
        lines.append(
            f"{name}：{len(units)} 單位，{len(systems)} 個系統（{', '.join(systems)}）"
        )
    return lines


def classify(src: str, row: dict[str, Any], target: str) -> dict[str, bool]:
    """判斷一句輸出的問題類型（啟發式，README 有定義）。"""
    out = row["output"]
    kana = len(_KANA.findall(out))
    han = len(_HAN.findall(out))
    latin = len(_LATIN.findall(out))
    flags = {
        "error": row["error"] is not None,
        "empty": not out.strip(),
        "multiline": "\n" in out and "\n" not in src,
        "explanation": bool(_EXPLANATION.search(out)) and not _EXPLANATION.search(src),
    }
    if target.startswith("zh"):
        flags["kana_any"] = kana > 0
        # 主要內容還是日文：假名占（假名＋漢字）三成以上。
        flags["wrong_lang"] = kana > 0 and kana >= 0.3 * (kana + han)
        flags["simplified"] = bool(simplified_characters(out))
        flags["too_long"] = len(out) > 3 * len(src) + 10
    else:
        flags["kana_any"] = kana > 0
        cjk = kana + han
        flags["wrong_lang"] = cjk > 0 and cjk >= 0.3 * (cjk + latin)
        flags["simplified"] = False
        flags["too_long"] = len(out) > 8 * len(src) + 20
    flags["extra"] = flags["multiline"] or flags["explanation"] or flags["too_long"]
    # 疑似把前文一起翻出來：有帶前文，且輸出長度超過原文能解釋的範圍。
    flags["ctx_leak"] = flags["too_long"] and bool(row.get("context"))
    return flags


def error_counts() -> dict[str, dict[str, dict[str, Any]]]:
    """每個（資料集, 設定）的可疑輸出計數與例子。"""
    result: dict[str, dict[str, dict[str, Any]]] = {}
    for name, eval_set in EVAL_SETS.items():
        if not set_path(name).exists():
            continue
        segments = read_json(set_path(name))["segments"]
        for system in SYSTEMS:
            if not system_applies(system, eval_set):
                continue
            rows = _complete_outputs(system.key, name)
            if rows is None:
                continue
            counts: dict[str, Any] = {"n": len(segments), "examples": {}}
            for seg in segments:
                flags = classify(seg["src"], rows[seg["id"]], eval_set.target)
                for flag, hit in flags.items():
                    counts[flag] = counts.get(flag, 0) + int(hit)
                    if hit and len(counts["examples"].setdefault(flag, [])) < 3:
                        counts["examples"][flag].append(
                            {"src": seg["src"], "out": rows[seg["id"]]["output"]}
                        )
            result.setdefault(name, {})[system.key] = counts
    return result


def _fmt_p(p: float | None) -> str:
    if p is None:
        return "—"
    return "<0.001" if p < 0.001 else f"{p:.3f}"


def _label(key: str) -> str:
    if key.startswith("wmt25:"):
        return f"*`{key}`（WMT25 官方輸出，整篇翻譯，校準用）*"
    system = SYSTEMS_BY_KEY[key]
    return f"`{key}` {system.label}"


def render_report() -> str:
    """把 sacrebleu／COMET／錯誤統計／延遲整理成 Markdown，寫入 results/report.md。"""
    sb_path = SCORING_DIR / "sacrebleu.json"
    comet_path = SCORING_DIR / "comet.json"
    sb = read_json(sb_path)["sets"] if sb_path.exists() else {}
    comet = read_json(comet_path)["sets"] if comet_path.exists() else {}
    errors = error_counts()
    parts: list[str] = []
    for name, eval_set in EVAL_SETS.items():
        if name not in sb:
            continue
        s = sb[name]
        c = comet.get(name, {})
        parts.append(f"\n### {eval_set.label}（{s['units']} 個計分單位）\n")
        parts.append(
            "| 設定 | chrF | 95% CI | BLEU | COMET | Δ chrF vs 基準 (p) | Δ COMET vs 基準 [95% CI] (p) |"
        )
        parts.append("|---|---|---|---|---|---|---|")
        order = sorted(s["scores"], key=lambda k: -s["scores"][k]["chrF"])
        base = s["baseline"]
        for key in order:
            chrf = s["scores"][key]["chrF"]
            bleu = s["scores"][key]["BLEU"]
            vb = s.get("vs_baseline", {}).get(key, {}).get("chrF", {})
            ci = vb.get("ci")
            ci_txt = f"±{ci:.2f}" if ci is not None else "—"
            if key == base:
                d_chrf = "（基準）"
            else:
                d_chrf = f"{chrf - s['scores'][base]['chrF']:+.2f} ({_fmt_p(vb.get('p_value'))})"
            cscore = c.get("scores", {}).get(key)
            c_txt = f"{cscore:.4f}" if cscore is not None else "—"
            cb = c.get("vs_baseline", {}).get(key)
            if key == c.get("baseline"):
                d_comet = "（基準）"
            elif cb:
                d_comet = (
                    f"{cb['delta']:+.4f} [{cb['ci_low']:+.4f}, {cb['ci_high']:+.4f}] "
                    f"({_fmt_p(cb['p_value'])})"
                )
            else:
                d_comet = "—"
            parts.append(
                f"| {_label(key)} | {chrf:.2f} | {ci_txt} | {bleu:.2f} | {c_txt} | {d_chrf} | {d_comet} |"
            )
        comet_contrasts = {(x["a"], x["b"]): x for x in c.get("contrasts", [])}
        if s.get("contrasts"):
            parts.append("\n成對比較（B − A；paired bootstrap 1,000 次）：\n")
            parts.append("| A | B | Δ chrF (p) | Δ BLEU (p) | Δ COMET [95% CI] (p) |")
            parts.append("|---|---|---|---|---|")
            for x in s["contrasts"]:
                cx = comet_contrasts.get((x["a"], x["b"]))
                c_txt = (
                    f"{cx['delta']:+.4f} [{cx['ci_low']:+.4f}, {cx['ci_high']:+.4f}] "
                    f"({_fmt_p(cx['p_value'])})"
                    if cx
                    else "—"
                )
                parts.append(
                    f"| `{x['a']}` | `{x['b']}` "
                    f"| {x['chrF']['delta']:+.2f} ({_fmt_p(x['chrF']['p_value'])}) "
                    f"| {x['BLEU']['delta']:+.2f} ({_fmt_p(x['BLEU']['p_value'])}) | {c_txt} |"
                )
        best = s["best"]
        if "vs_best" in s:
            parts.append(
                f"\n最佳 chrF：`{best}`；與其他設定的 paired bootstrap（chrF p 值）："
            )
            parts.append(
                "、".join(
                    f"`{k}` {_fmt_p(v.get('chrF', {}).get('p_value'))}"
                    for k, v in s["vs_best"].items()
                    if k != best
                )
            )
        if c.get("best") and c.get("vs_best"):
            parts.append(
                f"\n最佳 COMET：`{c['best']}`；與其他設定的 paired bootstrap（COMET p 值）："
            )
            parts.append(
                "、".join(
                    f"`{k}` {_fmt_p(v['p_value'])}" for k, v in c["vs_best"].items()
                )
            )
        parts.append(f"\n簽章：`{s['signature']['chrF']}`；`{s['signature']['BLEU']}`")

    parts.append("\n### 可疑輸出計數（逐句）\n")
    parts.append(
        "| 資料集 | 設定 | 句數 | 翻譯錯誤 | 空輸出 | 錯誤語言 | 含假名 | 簡體殘留 | 附加文字（多行／說明／過長） | 疑似翻出前文 |"
    )
    parts.append("|---|---|---|---|---|---|---|---|---|---|")
    for name, per_system in errors.items():
        for key, e in per_system.items():
            simp = (
                str(e["simplified"]) if EVAL_SETS[name].target.startswith("zh") else "—"
            )
            parts.append(
                f"| {name} | `{key}` | {e['n']} | {e['error']} | {e['empty']} | {e['wrong_lang']} "
                f"| {e['kana_any']} | {simp} | {e['extra']}（{e['multiline']}／{e['explanation']}／{e['too_long']}） | {e['ctx_leak']} |"
            )

    latency = sorted(LATENCY_DIR.glob("*.json"))
    if latency:
        parts.append("\n### 延遲（只量延遲模式）\n")
        parts.append(
            "| 設定 | 日期 | 句數 | 首字 P50 | 首字 P90 | 總延遲 P50 | 總延遲 P90 | 量測前 GPU |"
        )
        parts.append("|---|---|---|---|---|---|---|---|")
        for path in latency:
            s = read_json(path)["summary"]
            parts.append(
                f"| `{s['system']}` | {s['date']} | {s['ok']}/{s['n']} | {_sec(s['ttft_p50'])} "
                f"| {_sec(s['ttft_p90'])} | {_sec(s['total_p50'])} | {_sec(s['total_p90'])} | {s['gpu_before']} |"
            )
    parts.append("\n### 品質跑附帶的延遲（GPU 與其他工作共用，僅供參考）\n")
    parts.append("| 設定 | 句數 | 首字 P50 | 首字 P90 | 總延遲 P50 | 總延遲 P90 |")
    parts.append("|---|---|---|---|---|---|")

    for system in SYSTEMS:
        rows = []
        for name in EVAL_SETS:
            rows.extend(read_jsonl(output_path(system.key, name)))
        if rows:
            s = latency_summary(rows)
            parts.append(
                f"| `{system.key}` | {s['ok']}/{s['n']} | {_sec(s['ttft_p50'])} | {_sec(s['ttft_p90'])} "
                f"| {_sec(s['total_p50'])} | {_sec(s['total_p90'])} |"
            )
    text = "\n".join(parts) + "\n"
    (SCORING_DIR.parent / "report.md").write_text(text, encoding="utf-8")
    write_json(SCORING_DIR.parent / "errors.json", errors)
    return text


def _sec(value: float | None) -> str:
    return "—" if value is None else f"{value:.2f} s"
