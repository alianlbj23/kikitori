"""下載並整理評測資料（執行時下載到 data/，不進 repo）。

來源：
- WMT25 General MT 的 ja→zh_CN（人工參考譯文；speech＝YouTube 影片的 ASR 轉寫，
  social＝Mastodon 貼文）。官方說明「can be freely used for research purposes」。
- CoVoST 2 ja→en test（英文譯文 CC0；日文句子來自 Common Voice 4，CC0）。官方 TSV
  只有譯文，日文句子從 Hugging Face 的 `fixie-ai/covost2` 轉載取得，並與官方 TSV
  逐句比對譯文確認一致。
- BSD（Business Scene Dialogue）ja→en test，CC BY-NC-SA 4.0：只用於內部評測、不散布；
  只取「原文是日文」的對話情境。

所有資料都是外部輸入：逐欄驗證型別與長度，不合格就丟例外而不是默默略過。
"""

import csv
import io
import json
import random
import re
import tarfile
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

from common import DATA_DIR, EVAL_SETS, SEED, set_path, write_json

RAW_DIR = DATA_DIR / "raw"
TIMEOUT = 60.0
MAX_SEGMENT_CHARS = 2000  # 與 kikitori.translate 的請求上限相同

# 固定 commit，避免上游更新後結果無法重現（2026-11-05 時 main 的最新 commit）。
WMT25_COMMIT = "2b9101072126b0508005545f1315af30e828b5d5"
WMT25_URL = (
    "https://raw.githubusercontent.com/wmt-conference/wmt25-general-mt/"
    f"{WMT25_COMMIT}/data/wmt25-genmt.jsonl"
)
COVOST_TSV_URL = "https://dl.fbaipublicfiles.com/covost/covost_v2.ja_en.tsv.tar.gz"
HF_ROWS = "https://datasets-server.huggingface.co/rows"
HF_API = "https://huggingface.co/api/datasets/"
COVOST_HF = "fixie-ai/covost2"
BSD_HF = "ryo0634/bsd_ja_en"

COVOST_SAMPLE = 400
BSD_MIN_SEGMENTS = 300

# WMT25 speech 的原文是 Whisper 轉寫，每個 ASR 片段以「。」等結尾、片段之間有空白；
# 依此切回片段，逐片段翻譯，最接近 kikitori 的即時輸入。
_SPEECH_SPLIT = re.compile(r"(?<=[。！？!?])\s+")


def _get(url: str, timeout: float = TIMEOUT, retries: int = 3) -> bytes:
    """HTTP GET；暫時性錯誤重試（指數退避），最後仍失敗就往上丟。"""
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "kikitori-eval"})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (OSError, TimeoutError) as exc:  # URLError 是 OSError 的子類別
            last = exc
            wait = 2.0 * 2**attempt
            print(f"  下載失敗（{type(exc).__name__}: {exc}），{wait:.0f} 秒後重試…")
            time.sleep(wait)
    raise RuntimeError(f"下載失敗：{url}") from last


def _text(value: object, field: str) -> str:
    """驗證外部資料的文字欄位：必須是非空字串、長度在上限內。"""
    if not isinstance(value, str):
        raise TypeError(f"{field} 不是字串：{type(value).__name__}")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} 是空字串")
    if len(value) > MAX_SEGMENT_CHARS:
        raise ValueError(f"{field} 超過 {MAX_SEGMENT_CHARS} 字")
    return value


def _hf_revision(dataset: str) -> str:
    """查 Hugging Face 資料集目前的 commit sha（記錄在 manifest，供重現）。"""
    info = json.loads(_get(HF_API + dataset))
    sha = info.get("sha")
    if not isinstance(sha, str):
        raise TypeError(f"{dataset} 的 API 回應沒有 sha")
    return sha


def _hf_rows(dataset: str, config: str, split: str) -> list[dict[str, Any]]:
    """用 datasets-server 的 /rows 分頁抓整個 split（只取文字欄位，不下載音檔）。"""
    rows: list[dict[str, Any]] = []
    offset = 0
    total: int | None = None
    while total is None or offset < total:
        query = urllib.parse.urlencode(
            {
                "dataset": dataset,
                "config": config,
                "split": split,
                "offset": offset,
                "length": 100,
            }
        )
        data = json.loads(_get(f"{HF_ROWS}?{query}"))
        if "rows" not in data:
            raise ValueError(f"{dataset} /rows 回應異常：{str(data)[:200]}")
        total = int(data["num_rows_total"])
        page = [r["row"] for r in data["rows"]]
        if not page:
            raise ValueError(f"{dataset} 在 offset {offset} 回傳空頁")
        rows.extend(page)
        offset += len(page)
        print(f"  {dataset}：{offset}/{total}")
    return rows


def _units_from_segments(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """句級資料集：每句就是一個計分單位。"""
    return [
        {
            "id": s["id"],
            "doc": s["doc"],
            "seg_ids": [s["id"]],
            "src": s["src"],
            "ref": s["ref"],
        }
        for s in segments
    ]


def prepare_wmt25() -> dict[str, Any]:
    """WMT25 ja→zh_CN：speech（文件級參考）與 social（逐行對齊的參考）。"""
    raw = RAW_DIR / "wmt25" / "wmt25-genmt.jsonl"
    if not raw.exists():
        print("下載 WMT25 General MT（約 14.5 MB）…")
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(_get(WMT25_URL, timeout=300))
    docs = []
    for line in raw.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("src_lang") == "ja" and row.get("tgt_lang") == "zh_CN":
            docs.append(row)
    if not docs:
        raise ValueError("WMT25 檔案裡沒有 ja→zh_CN")

    # speech：參考譯文是整段（沒有逐句對齊），所以逐片段翻譯後接成整段再計分。
    speech_segments: list[dict[str, Any]] = []
    speech_units: list[dict[str, Any]] = []
    for row in (d for d in docs if d["domain"] == "speech"):
        doc = _text(row["doc_id"], "doc_id").split("_#_")[-1]
        ref = _text(row["refs"]["refA"]["ref"], f"{doc} ref")
        pieces = [p for p in _SPEECH_SPLIT.split(row["src_text"].strip()) if p.strip()]
        seg_ids = []
        for i, piece in enumerate(pieces):
            seg_id = f"{doc}#{i:02d}"
            speech_segments.append(
                {"id": seg_id, "doc": doc, "src": _text(piece, seg_id), "ref": None}
            )
            seg_ids.append(seg_id)
        speech_units.append(
            {
                "id": doc,
                "doc": doc,
                "seg_ids": seg_ids,
                "src": row["src_text"].strip(),
                "ref": ref,
            }
        )

    # social：原文與參考逐行對齊（行數相同才用），去掉空行。
    social_segments: list[dict[str, Any]] = []
    skipped_docs = []
    for row in (d for d in docs if d["domain"] == "social"):
        doc = row["doc_id"].split("_#_")[-1]
        src_lines = row["src_text"].split("\n")
        ref_lines = row["refs"]["refA"]["ref"].split("\n")
        if len(src_lines) != len(ref_lines):
            skipped_docs.append(doc)
            continue
        for i, (src, ref) in enumerate(zip(src_lines, ref_lines, strict=True)):
            if not src.strip() or not ref.strip():
                continue
            seg_id = f"{doc}#{i:03d}"
            social_segments.append(
                {
                    "id": seg_id,
                    "doc": doc,
                    "src": _text(src, seg_id),
                    "ref": _text(ref, f"{seg_id} ref"),
                }
            )

    source = {
        "name": "WMT25 General MT test（ja→zh_CN，refA）",
        "url": WMT25_URL,
        "revision": WMT25_COMMIT,
        "license": "WMT25：test data 'can be freely used for research purposes'；原文著作權屬原作者，不散布",
    }
    write_json(
        set_path("zh_speech"),
        {
            "source": source,
            "note": "Whisper 轉寫依「。！？」後的空白切成片段逐句翻譯；參考譯文為整段，以文件為計分單位",
            "segments": speech_segments,
            "units": speech_units,
        },
    )
    write_json(
        set_path("zh_social"),
        {
            "source": source,
            "note": f"逐行對齊；行數不一致而略過的文件：{skipped_docs}",
            "segments": social_segments,
            "units": _units_from_segments(social_segments),
        },
    )
    return {
        "zh_speech": {"docs": len(speech_units), "segments": len(speech_segments)},
        "zh_social": {
            "docs": len({s["doc"] for s in social_segments}),
            "segments": len(social_segments),
            "skipped_docs": skipped_docs,
        },
    }


def _covost_official() -> dict[str, tuple[str, str]]:
    """官方 CoVoST 2 TSV：clip id → (split, 英文譯文)。"""
    archive = RAW_DIR / "covost" / "covost_v2.ja_en.tsv.tar.gz"
    if not archive.exists():
        print("下載 CoVoST 2 ja→en 官方 TSV…")
        archive.parent.mkdir(parents=True, exist_ok=True)
        archive.write_bytes(_get(COVOST_TSV_URL))
    with tarfile.open(archive, "r:gz") as tar:
        # 只讀指定成員到記憶體，不解壓到磁碟（避免壓縮檔內的路徑跳脫）。
        member = tar.getmember("covost_v2.ja_en.tsv")
        handle = tar.extractfile(member)
        if handle is None:
            raise ValueError("CoVoST 壓縮檔內找不到 TSV")
        text = handle.read().decode("utf-8")
    reader = csv.DictReader(io.StringIO(text), delimiter="\t", quoting=csv.QUOTE_NONE)
    out: dict[str, tuple[str, str]] = {}
    for row in reader:
        clip = row["path"].removesuffix(".mp3")
        out[clip] = (row["split"], row["translation"].strip())
    return out


def prepare_covost() -> dict[str, Any]:
    """CoVoST 2 ja→en test：固定種子抽樣 400 句（單句、沒有文件結構）。"""
    official = _covost_official()
    revision = _hf_revision(COVOST_HF)
    rows = _hf_rows(COVOST_HF, "ja_en", "test")
    segments: list[dict[str, Any]] = []
    mismatched = 0
    for row in rows:
        clip = _text(row["id"], "id")
        split, translation = official.get(clip, ("missing", ""))
        if split != "test":
            raise ValueError(f"{clip} 不在官方 test split（{split}）")
        ref = _text(row["translation"], f"{clip} translation")
        if ref != translation:
            mismatched += 1
        segments.append(
            {"id": clip, "doc": clip, "src": _text(row["sentence"], clip), "ref": ref}
        )
    if mismatched:
        raise ValueError(f"有 {mismatched} 句譯文與官方 TSV 不一致")
    official_test = sum(1 for split, _ in official.values() if split == "test")
    if len(segments) != official_test:
        raise ValueError(f"句數 {len(segments)} 與官方 test {official_test} 不同")
    sample = sorted(
        random.Random(SEED).sample(segments, COVOST_SAMPLE), key=lambda s: s["id"]
    )
    write_json(
        set_path("en_covost"),
        {
            "source": {
                "name": "CoVoST 2 ja→en test（日文句子＝Common Voice 4）",
                "url": f"{COVOST_TSV_URL}；https://huggingface.co/datasets/{COVOST_HF}",
                "revision": revision,
                "license": "譯文 CC0（CoVoST 2）；日文句子 CC0（Common Voice）",
            },
            "note": f"官方 test {official_test} 句，以種子 {SEED} 抽 {COVOST_SAMPLE} 句；譯文已與官方 TSV 逐句比對一致",
            "segments": sample,
            "units": _units_from_segments(sample),
        },
    )
    return {"en_covost": {"test_total": official_test, "segments": len(sample)}}


def prepare_bsd() -> dict[str, Any]:
    """BSD ja→en test：只取日文原創的對話情境，隨機取整個情境直到 ≥300 句。"""
    revision = _hf_revision(BSD_HF)
    rows = _hf_rows(BSD_HF, "default", "test")
    scenarios: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        if row.get("original_language") != "ja":
            continue
        scenarios.setdefault(_text(row["id"], "id"), []).append(row)
    ids = sorted(scenarios)
    random.Random(SEED).shuffle(ids)
    segments: list[dict[str, Any]] = []
    used = []
    for scenario in ids:
        if len(segments) >= BSD_MIN_SEGMENTS:
            break
        used.append(scenario)
        for row in sorted(scenarios[scenario], key=lambda r: int(r["no"])):
            seg_id = f"{scenario}#{int(row['no']):03d}"
            segments.append(
                {
                    "id": seg_id,
                    "doc": scenario,
                    "src": _text(row["ja_sentence"], seg_id),
                    "ref": _text(row["en_sentence"], f"{seg_id} ref"),
                }
            )
    write_json(
        set_path("en_bsd"),
        {
            "source": {
                "name": "Business Scene Dialogue corpus test（original_language=ja）",
                "url": f"https://huggingface.co/datasets/{BSD_HF}",
                "revision": revision,
                "license": "CC BY-NC-SA 4.0：只做內部評測，不散布",
            },
            "note": f"日文原創情境 {len(scenarios)} 個，以種子 {SEED} 打亂後取前 {len(used)} 個（整個情境）",
            "segments": segments,
            "units": _units_from_segments(segments),
        },
    )
    return {"en_bsd": {"scenarios": len(used), "segments": len(segments)}}


# 校準用：WMT25 官方釋出的系統輸出（整篇文件一次翻譯、看得到全文，條件比逐句即時翻譯
# 有利），用來對照「這個資料集上好的 chrF／COMET 大約是多少」。
WMT25_SYSTEMS = [
    "Gemini-2.5-Pro",
    "GPT-4.1",
    "Shy",  # Shy-hunyuan-MT（Hunyuan-MT-7B）
    "Qwen3-235B",
    "Qwen2.5-7B",
    "Gemma-3-12B",
    "TowerPlus-9B",
]


def wmt25_systems_path() -> Path:
    """WMT25 系統在 zh_speech 文件上的輸出（校準用）。"""
    return DATA_DIR / "zh_speech_wmt25_systems.json"


def prepare_wmt25_systems() -> dict[str, Any]:
    """下載 WMT25 系統輸出，只留 ja→zh speech 的 59 篇（每篇都要有，否則丟例外）。"""
    docs = {
        u["id"]
        for u in json.loads(set_path("zh_speech").read_text(encoding="utf-8"))["units"]
    }
    out: dict[str, dict[str, str]] = {}
    for name in WMT25_SYSTEMS:
        raw = RAW_DIR / "wmt25" / "systems" / f"{name}.jsonl"
        if not raw.exists():
            print(f"下載 WMT25 系統輸出 {name}（約 10 MB）…")
            raw.parent.mkdir(parents=True, exist_ok=True)
            url = (
                "https://raw.githubusercontent.com/wmt-conference/wmt25-general-mt/"
                f"{WMT25_COMMIT}/data/systems/{urllib.parse.quote(name)}.jsonl"
            )
            raw.write_bytes(_get(url, timeout=300))
        hyps: dict[str, str] = {}
        for line in raw.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            doc_id = row.get("doc_id", "")
            if not doc_id.startswith("ja-zh_CN_#_speech_#_"):
                continue
            doc = doc_id.split("_#_")[-1]
            hyp = row.get("hypothesis")
            if not isinstance(hyp, str):
                raise TypeError(f"{name} {doc} 的 hypothesis 不是字串")
            hyps[doc] = hyp.strip()
        missing = docs - hyps.keys()
        if missing:
            raise ValueError(f"{name} 缺少 {len(missing)} 篇 speech 文件")
        out[name] = {d: hyps[d] for d in sorted(docs)}
    write_json(wmt25_systems_path(), out)
    return {"zh_speech_wmt25_systems": list(out)}


def prepare_all() -> None:
    """下載並整理全部資料集，寫 data/manifest.json。"""
    manifest: dict[str, Any] = {"retrieved": time.strftime("%Y-%m-%d %H:%M")}
    for step in (prepare_wmt25, prepare_wmt25_systems, prepare_covost, prepare_bsd):
        manifest.update(step())
    write_json(DATA_DIR / "manifest.json", manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=1))


def missing_sets() -> list[str]:
    """還沒準備好的資料集名稱。"""
    return [name for name in EVAL_SETS if not set_path(name).exists()]
