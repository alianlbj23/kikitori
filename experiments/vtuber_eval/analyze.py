"""計分與統計：CER、blockwise bootstrap 信賴區間、成對比較、延遲（vtuber_eval 的第 4 步）。

角色：暫時性實驗，不屬於正式程式碼。讀 `results/*.jsonl`（`evaluate.py` 的逐段輸出）
與 `data/manifest.json`，寫 `results/summary.md`、`results/summary.json`。

- CER = 總編輯距離 ÷ 參考總字數（corpus-level，與 kotoba／HF `evaluate` 的 cer 相同），
  正規化見 `textnorm.py`（有版本號）。
- 「過濾後」：被 app 的 `rejection_reason()` 丟掉的段落當作輸出空字串（使用者看不到字幕，
  等於整句刪除）；這是 app 的實際體驗，所以是主要指標。「未過濾」另列。
- 信賴區間：以 block（YODAS＝影片、FLEURS＝句子、動漫＝片段、ReazonSpeech＝單句）
  做 1000 次 blockwise bootstrap 的 2.5／97.5 百分位；成對比較用同一組重抽樣，
  報告 ΔCER 的 95% CI 與 probability of improvement（重抽樣中 A 的 CER 低於 B 的比例）。
"""

import importlib.util
import json
import sys
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

import numpy as np
import numpy.typing as npt

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"
BOOTSTRAP = 1000
BOOTSTRAP_SEED = 20261105
MUSIC_CONDITIONS = ("clean", "music20", "music15", "music10", "music5", "music0")
NOISE_CONDITIONS = ("noise10", "noise5")
DELTA_CONDITIONS = ("music10", "music5", "music0", "noise5")
# no_speech 不可靠的模型（registry 的 no_speech_reliable=False 與 kotoba 對照規格）
NO_SPEECH_UNRELIABLE = ("large-v3-turbo", "kotoba-whisper-v2.0")
NO_SPEECH_DONOR = "small_cuda_float16_fast_full"
# 敏感度分析：所有 GPU final 設定的 CER 中位數高於此值的 YODAS 段落，視為字幕對不上音訊
MISALIGNED_MEDIAN_CER = 0.5


def _load_sibling(name: str) -> ModuleType:
    # `-I` 時腳本資料夾不在 sys.path，改用檔案路徑載入
    spec = importlib.util.spec_from_file_location(
        f"vtuber_eval_{name}", HERE / f"{name}.py"
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


textnorm = _load_sibling("textnorm")


def current_rejection() -> Callable[[dict[str, Any]], str | None]:
    """以目前 app 的 `rejection_reason()` 重新判定一列結果。

    過濾模組可能正被重構；介面不相容時退回執行當下的判定並印出警告，不讓計分中斷。
    """
    from kikitori.asr import Transcript, rejection_reason

    def rescore(row: dict[str, Any]) -> str | None:
        transcript = Transcript(
            text=row["text"],
            language=row["language"],
            language_probability=1.0,
            no_speech_probability=row["no_speech"],
            avg_logprob=row["avg_logprob"],
        )
        return rejection_reason(transcript)

    try:
        rescore(
            {"text": "テスト", "language": "ja", "no_speech": 0.0, "avg_logprob": 0.0}
        )
    except TypeError as exc:
        print(f"警告：rejection_reason 介面不相容（{exc}），改用執行當下的判定")
        return lambda row: row["rejected_at_run"]
    return rescore


@dataclass
class Scored:
    """一個設定的逐段計分結果（以 id 為鍵）。"""

    label: str
    meta: dict[str, Any]
    rows: dict[str, dict[str, Any]]


def load_scored(manifest: dict[str, dict[str, Any]]) -> list[Scored]:
    """讀所有結果，計算每段的編輯距離（基本／去填充詞 × 過濾前／後）。"""
    rescore = current_rejection()
    norm_cache: dict[tuple[str, str], str] = {}

    def norm(variant: str, text: str) -> str:
        key = (variant, text)
        if key not in norm_cache:
            fn = (
                textnorm.normalize_basic
                if variant == "basic"
                else textnorm.normalize_no_fillers
            )
            norm_cache[key] = fn(text)
        return norm_cache[key]

    out: list[Scored] = []
    for path in sorted(RESULTS.glob("*.jsonl")):
        label = path.stem
        meta_path = path.with_suffix(".meta.json")
        meta = (
            json.loads(meta_path.read_text(encoding="utf-8"))
            if meta_path.exists()
            else {}
        )
        rows: dict[str, dict[str, Any]] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                print(f"略過 {path.name} 的壞行")
                continue
            item = manifest.get(row["id"])
            if item is None:
                continue  # manifest 已重建、此段不再使用
            row["rejected"] = rescore(row)
            for variant in ("basic", "nofill"):
                ref = norm(variant, item["reference"])
                hyp = norm(variant, row["text"])
                edits = textnorm.edit_distance(ref, hyp)
                row[f"{variant}_ref"] = len(ref)
                row[f"{variant}_edits_raw"] = edits
                row[f"{variant}_edits"] = len(ref) if row["rejected"] else edits
                row[f"{variant}_hyp_len"] = 0 if row["rejected"] else len(hyp)
            rows[row["id"]] = row
        out.append(Scored(label, meta, rows))

    # app 的 pipeline 在完整模型的 no_speech 不可靠時（large-v3-turbo、kotoba），
    # 改用即時模型對同一句的 no_speech 過濾（pipeline.py `_for_filter`）。
    # 這裡以 small fast（現行 ≥ 8 GB 等級的即時模型）模擬該組合，另列一個設定。
    by_label = {s.label: s for s in out}
    fast = by_label.get(NO_SPEECH_DONOR)
    for scored in list(out):
        model = scored.label.split("_cuda_")[0]
        if (
            fast is None
            or model not in NO_SPEECH_UNRELIABLE
            or "_final_" not in scored.label
            or not scored.label.endswith("_full")
        ):
            continue
        rows = {}
        for row_id, row in scored.rows.items():
            donor = fast.rows.get(row_id)
            if donor is None:
                continue
            new = {**row, "no_speech": donor["no_speech"]}
            new["rejected"] = rescore(new)
            for variant in ("basic", "nofill"):
                ref_len = new[f"{variant}_ref"]
                raw = new[f"{variant}_edits_raw"]
                new[f"{variant}_edits"] = ref_len if new["rejected"] else raw
                if new["rejected"]:
                    new[f"{variant}_hyp_len"] = 0
                else:
                    new[f"{variant}_hyp_len"] = len(norm(variant, new["text"]))
            rows[row_id] = new
        label = scored.label.removesuffix("_full") + "＋small-fast-no_speech_full"
        out.append(Scored(label, scored.meta, rows))
    return out


def _block_arrays(
    rows: list[dict[str, Any]], key: str
) -> tuple[npt.NDArray[np.float64], npt.NDArray[np.float64], list[str]]:
    edits: dict[str, float] = {}
    chars: dict[str, float] = {}
    for r in rows:
        edits[r["block"]] = edits.get(r["block"], 0.0) + r[f"{key}_edits"]
        chars[r["block"]] = chars.get(r["block"], 0.0) + r[f"{key}_ref"]
    blocks = sorted(edits)
    return (
        np.array([edits[b] for b in blocks]),
        np.array([chars[b] for b in blocks]),
        blocks,
    )


def _resample_indices(n_blocks: int) -> npt.NDArray[np.int64]:
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    return rng.integers(0, n_blocks, size=(BOOTSTRAP, n_blocks))


def cer_with_ci(rows: list[dict[str, Any]], variant: str = "basic") -> dict[str, Any]:
    """corpus CER 與 blockwise bootstrap 95% CI。"""
    edits, chars, blocks = _block_arrays(rows, variant)
    if chars.sum() == 0:
        raise ValueError("references are empty")
    idx = _resample_indices(len(blocks))
    boot = edits[idx].sum(axis=1) / np.maximum(chars[idx].sum(axis=1), 1)
    return {
        "cer": float(edits.sum() / chars.sum()),
        "lo": float(np.percentile(boot, 2.5)),
        "hi": float(np.percentile(boot, 97.5)),
        "n": len(rows),
        "blocks": len(blocks),
    }


def paired(
    a: list[dict[str, Any]], b: list[dict[str, Any]], variant: str = "basic"
) -> dict[str, Any]:
    """同一批段落上 A 與 B 的 ΔCER（A − B）95% CI 與 P(A 優於 B)。"""
    ids = sorted({r["id"] for r in a} & {r["id"] for r in b})
    if not ids:
        raise ValueError("no common items")
    by_a = {r["id"]: r for r in a}
    by_b = {r["id"]: r for r in b}
    blocks = sorted({by_a[i]["block"] for i in ids})
    pos = {blk: k for k, blk in enumerate(blocks)}
    ea = np.zeros(len(blocks))
    eb = np.zeros(len(blocks))
    n = np.zeros(len(blocks))
    for i in ids:
        k = pos[by_a[i]["block"]]
        ea[k] += by_a[i][f"{variant}_edits"]
        eb[k] += by_b[i][f"{variant}_edits"]
        n[k] += by_a[i][f"{variant}_ref"]
    idx = _resample_indices(len(blocks))
    denom = np.maximum(n[idx].sum(axis=1), 1)
    diff = (ea[idx].sum(axis=1) - eb[idx].sum(axis=1)) / denom
    return {
        "delta": float((ea.sum() - eb.sum()) / n.sum()),
        "lo": float(np.percentile(diff, 2.5)),
        "hi": float(np.percentile(diff, 97.5)),
        "poi": float(np.mean(diff < 0)),
        "n": len(ids),
        "blocks": len(blocks),
    }


def latency_stats(rows: Iterable[dict[str, Any]]) -> dict[str, float]:
    """延遲中位數、P90（ms）與 RTF（總耗時 ÷ 總長度）。"""
    rows = list(rows)
    lat = np.array([r["latency"] for r in rows]) * 1000
    secs = sum(r["seconds"] for r in rows)
    return {
        "median_ms": float(np.median(lat)),
        "p90_ms": float(np.percentile(lat, 90)),
        "rtf": float(lat.sum() / 1000 / secs),
        "n": len(rows),
    }


def select(
    scored: Scored, set_: str, condition: str, ids: set[str] | None = None
) -> list[dict[str, Any]]:
    """取出某組別與條件的列（可再限制 source_id）。"""
    return [
        r
        for r in scored.rows.values()
        if r["set"] == set_
        and r["condition"] == condition
        and (ids is None or r["source_id"] in ids)
    ]


def fmt(stat: dict[str, Any]) -> str:
    """`12.3 [10.1, 14.8]`（百分比）。"""
    return f"{stat['cer'] * 100:.1f} [{stat['lo'] * 100:.1f}, {stat['hi'] * 100:.1f}]"


def _short(label: str) -> str:
    if label.endswith("_full"):
        return label.removesuffix("_full")
    return label.removesuffix("_cpu") + "（CPU 子集）"


def analyze() -> str:
    """產生 Markdown 摘要並寫出 `results/summary.md`／`summary.json`。"""
    manifest_items = json.loads((DATA / "manifest.json").read_text(encoding="utf-8"))[
        "items"
    ]
    manifest = {i["id"]: i for i in manifest_items}
    scored = load_scored(manifest)
    if not scored:
        raise FileNotFoundError("results/ 沒有任何結果")
    lines: list[str] = [
        (
            f"正規化：{textnorm.NORMALIZER_VERSION}；去填充詞："
            f"{textnorm.FILLER_RULES_VERSION}；bootstrap {BOOTSTRAP} 次"
            f"（seed {BOOTSTRAP_SEED}）。CER 為百分比，[] 為 95% CI。"
        ),
        "",
    ]
    summary: dict[str, Any] = {"configs": {}}
    noisy_ids = {i["source_id"] for i in manifest_items if i["condition"] == "music10"}
    # 只計分跑完的設定：跑到一半的設定各條件段數不一，成對比較會失真
    n_full = len(manifest_items)
    n_cpu = sum(i["cpu_subset"] for i in manifest_items)
    incomplete = [
        s.label
        for s in scored
        if len(s.rows) < (n_full if s.label.endswith("_full") else n_cpu)
    ]
    if incomplete:
        lines.extend([f"未跑完、本次不計分：{', '.join(incomplete)}", ""])
    full = [
        s for s in scored if s.label.endswith("_full") and s.label not in incomplete
    ]
    cpu = [s for s in scored if s.label.endswith("_cpu") and s.label not in incomplete]

    # 敏感度分析用：所有 GPU final 設定的 CER 中位數過高 → 可能字幕對不上
    finals = [s for s in full if "_final_" in s.label]
    misaligned: set[str] = set()
    for item in manifest_items:
        if item["set"] != "yodas" or item["condition"] != "clean":
            continue
        cers = [
            s.rows[item["id"]]["basic_edits_raw"]
            / max(s.rows[item["id"]]["basic_ref"], 1)
            for s in finals
            if item["id"] in s.rows
        ]
        if cers and float(np.median(cers)) > MISALIGNED_MEDIAN_CER:
            misaligned.add(item["id"])
    summary["misaligned_yodas"] = sorted(misaligned)

    def main_table(group: list[Scored], title: str) -> None:
        lines.extend(
            [
                f"### {title}",
                "",
                (
                    "| 設定 | YODAS 過濾後 | YODAS 未過濾 | YODAS 去填充詞 "
                    "| YODAS 去疑似錯位 | FLEURS | ReazonSpeech | 動漫 6 段 | 語音被誤濾 |"
                ),
                "|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for s in group:
            row: dict[str, Any] = {}
            cells = [_short(s.label)]
            yodas = select(s, "yodas", "clean")
            for key, rows, variant in (
                ("yodas", yodas, "basic"),
                ("yodas_raw", yodas, "raw"),
                ("yodas_nofill", yodas, "nofill"),
                (
                    "yodas_aligned",
                    [r for r in yodas if r["id"] not in misaligned],
                    "basic",
                ),
                ("fleurs", select(s, "fleurs", "clean"), "basic"),
                ("reazon", select(s, "reazon", "clean"), "basic"),
                ("anime", select(s, "anime", "clean"), "basic"),
            ):
                if not rows:
                    cells.append("—")
                    continue
                if variant == "raw":
                    raw_rows = [
                        {**r, "basic_edits": r["basic_edits_raw"]} for r in rows
                    ]
                    stat = cer_with_ci(raw_rows, "basic")
                else:
                    stat = cer_with_ci(rows, variant)
                row[key] = stat
                cells.append(fmt(stat))
            speech = [r for r in s.rows.values() if r["set"] != "nonspeech"]
            clean_speech = [r for r in speech if r["condition"] == "clean"]
            rejected = sum(r["rejected"] is not None for r in clean_speech)
            cells.append(f"{rejected}/{len(clean_speech)}")
            row["speech_rejected"] = [rejected, len(clean_speech)]
            summary["configs"].setdefault(s.label, {}).update(row)
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    def bgm_table(group: list[Scored]) -> None:
        conds = (*MUSIC_CONDITIONS, *NOISE_CONDITIONS)
        lines.extend(
            [
                "### BGM／音效強健性（YODAS 混音子集，同一批段落；CER 過濾後）",
                "",
                "| 設定 | "
                + " | ".join(conds)
                + " | "
                + " | ".join(f"Δ {c}−clean" for c in DELTA_CONDITIONS)
                + " |",
                "|---|" + "---|" * (len(conds) + len(DELTA_CONDITIONS)),
            ]
        )
        for s in group:
            cells = [_short(s.label)]
            curve: dict[str, Any] = {}
            for cond in conds:
                rows = select(s, "yodas", cond, noisy_ids)
                if not rows:
                    cells.append("—")
                    continue
                curve[cond] = cer_with_ci(rows)
                cells.append(fmt(curve[cond]))
            clean = select(s, "yodas", "clean", noisy_ids)
            for cond in DELTA_CONDITIONS:
                mixed = select(s, "yodas", cond, noisy_ids)
                if not clean or not mixed:
                    cells.append("—")
                    continue
                # 以 source_id 配對：把混音列的 id 換成乾淨列的 id
                d = paired([{**r, "id": r["source_id"]} for r in mixed], clean)
                curve[f"delta_{cond}"] = d
                cells.append(
                    f"{d['delta'] * 100:+.1f} [{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}]"
                )
            summary["configs"].setdefault(s.label, {})["bgm"] = curve
            lines.append("| " + " | ".join(cells) + " |")
        lines.append("")

    def latency_table(group: list[Scored]) -> None:
        lines.extend(
            [
                "### 延遲、非語音幻覺",
                "",
                (
                    "| 設定 | 延遲中位 ms | P90 ms | RTF | 段數 | 非語音輸出文字（過濾後） "
                    "| 非語音輸出文字（未過濾） | 實際裝置 | GPU 已用 MB（範圍） "
                    "| GPU 使用率 %（範圍） |"
                ),
                "|---|---|---|---|---|---|---|---|---|---|",
            ]
        )
        for s in group:
            speech = [r for r in s.rows.values() if r["set"] != "nonspeech"]
            lat = latency_stats(speech)
            nonspeech = [r for r in s.rows.values() if r["set"] == "nonspeech"]
            halluc = sum(
                r["rejected"] is None and r["basic_hyp_len"] > 0 for r in nonspeech
            )
            halluc_raw = sum(
                bool(textnorm.normalize_basic(r["text"])) for r in nonspeech
            )
            snaps = [
                snap
                for run in s.meta.get("runs", [])
                for snap in run.get("gpu_snapshots", [])
                if "memory_used_mb" in snap and s.meta.get("actual_device") == "cuda"
            ]
            mem = (
                f"{min(x['memory_used_mb'] for x in snaps):.0f}–"
                f"{max(x['memory_used_mb'] for x in snaps):.0f}"
                if snaps
                else "—"
            )
            util = (
                f"{min(x['utilization'] for x in snaps):.0f}–"
                f"{max(x['utilization'] for x in snaps):.0f}"
                if snaps
                else "—"
            )
            lines.append(
                f"| {_short(s.label)} | {lat['median_ms']:.0f} | {lat['p90_ms']:.0f} "
                f"| {lat['rtf']:.3f} | {lat['n']} | {halluc}/{len(nonspeech)} "
                f"| {halluc_raw}/{len(nonspeech)} "
                f"| {s.meta.get('actual_device', '?')} {s.meta.get('actual_compute_type', '')} "
                f"| {mem} | {util} |"
            )
            summary["configs"].setdefault(s.label, {}).update(
                {
                    "latency": lat,
                    "nonspeech_hallucinated": [halluc, len(nonspeech)],
                    "nonspeech_raw_output": [halluc_raw, len(nonspeech)],
                }
            )
        lines.append("")

    def pair_table(group: list[Scored]) -> None:
        by_label = {s.label: s for s in group}
        lines.extend(
            [
                "### 成對比較（ΔCER = A − B，負值代表 A 較好；P = A 優於 B 的 bootstrap 機率）",
                "",
                "| 資料／條件 | 模式 | A | B | ΔCER [95% CI] | P(A 較好) | 段數／block |",
                "|---|---|---|---|---|---|---|",
            ]
        )
        pairs = [
            ("kotoba-whisper-v2.0", "large-v3"),
            ("large-v3-turbo", "large-v3"),
            ("kotoba-whisper-v2.0", "large-v3-turbo"),
            ("medium", "large-v3"),
            ("small", "large-v3-turbo"),
        ]
        targets = [
            ("yodas", "clean", None, "YODAS 乾淨"),
            ("yodas", "music10", noisy_ids, "YODAS music 10 dB"),
            ("yodas", "music5", noisy_ids, "YODAS music 5 dB"),
            ("fleurs", "clean", None, "FLEURS"),
            ("reazon", "clean", None, "ReazonSpeech"),
        ]
        results: list[dict[str, Any]] = []
        for set_, cond, ids, name in targets:
            for mode in ("fast", "final"):
                for a, b in pairs:
                    la = next(
                        (
                            lbl
                            for lbl in by_label
                            if lbl.startswith(f"{a}_cuda_float16_")
                            and f"_{mode}_" in lbl
                        ),
                        None,
                    )
                    lb = next(
                        (
                            lbl
                            for lbl in by_label
                            if lbl.startswith(f"{b}_cuda_float16_")
                            and f"_{mode}_" in lbl
                        ),
                        None,
                    )
                    if la is None or lb is None:
                        continue
                    ra = select(by_label[la], set_, cond, ids)
                    rb = select(by_label[lb], set_, cond, ids)
                    if not ra or not rb:
                        continue
                    d = paired(ra, rb)
                    results.append(
                        {
                            "set": set_,
                            "condition": cond,
                            "mode": mode,
                            "a": la,
                            "b": lb,
                            **d,
                        }
                    )
                    lines.append(
                        f"| {name} | {mode} | {a} | {b} "
                        f"| {d['delta'] * 100:+.1f} [{d['lo'] * 100:+.1f}, {d['hi'] * 100:+.1f}] "
                        f"| {d['poi']:.3f} | {d['n']}／{d['blocks']} |"
                    )
        # 同一模型的 int8_float16 vs float16（VRAM 不足時的退路是否掉品質）
        for la in sorted(by_label):
            if "_cuda_int8_float16_" not in la:
                continue
            lb = la.replace("_cuda_int8_float16_", "_cuda_float16_")
            if lb not in by_label:
                continue
            for set_, cond, ids, name in targets:
                ra = select(by_label[la], set_, cond, ids)
                rb = select(by_label[lb], set_, cond, ids)
                if not ra or not rb:
                    continue
                d = paired(ra, rb)
                results.append(
                    {"set": set_, "condition": cond, "mode": "—", "a": la, "b": lb, **d}
                )
                lines.append(
                    f"| {name} | — | {_short(la)} | {_short(lb)} "
                    f"| {d['delta'] * 100:+.1f} [{d['lo'] * 100:+.1f}, "
                    f"{d['hi'] * 100:+.1f}] | {d['poi']:.3f} | {d['n']}／{d['blocks']} |"
                )
        lines.append("")
        summary["pairs"] = results

    lines.append(
        f"YODAS 疑似字幕錯位（所有 GPU final 設定 CER 中位數 > {MISALIGNED_MEDIAN_CER}）："
        f"{len(misaligned)} 段，只在「去疑似錯位」欄排除。"
    )
    lines.append("")
    main_table(full, "主要結果（GPU，全集）")
    bgm_table(full)
    pair_table(full)
    latency_table(full)
    if cpu:
        main_table(cpu, "CPU int8（子集：YODAS 乾淨 150 段、FLEURS 50 段、動漫 6 段）")
        latency_table(cpu)
        # 同一子集上的 GPU 數字，方便與 CPU 對照
        cpu_ids = {i["id"] for i in manifest_items if i["cpu_subset"]}
        lines.extend(
            [
                "### 同一 CPU 子集上的 GPU 設定（對照用）",
                "",
                "| 設定 | YODAS 子集 | FLEURS 子集 |",
                "|---|---|---|",
            ]
        )
        for s in full:
            y = [r for r in select(s, "yodas", "clean") if r["id"] in cpu_ids]
            f = [r for r in select(s, "fleurs", "clean") if r["id"] in cpu_ids]
            if y and f:
                lines.append(
                    f"| {_short(s.label)} | {fmt(cer_with_ci(y))} | {fmt(cer_with_ci(f))} |"
                )
        lines.append("")

    text = "\n".join(lines)
    (RESULTS / "summary.md").write_text(text, encoding="utf-8")
    (RESULTS / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=1), encoding="utf-8"
    )
    return text


if __name__ == "__main__":
    # `-I` 會忽略 PYTHONIOENCODING；Windows 主控台預設編碼印不出中文，所以明確改成 UTF-8
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    sys.stderr.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    if sys.argv[1:] == ["--worker"]:
        print(analyze())
    else:
        print("請用選單執行：uv run python -I experiments/vtuber_eval/menu.py")
