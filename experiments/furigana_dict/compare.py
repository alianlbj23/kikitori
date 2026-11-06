"""振假名辭典選型：SudachiDict small 與 core 的讀音差異、覆蓋率、載入時間與記憶體。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集。語料是其他實驗已下載的日文句子
（`experiments/vtuber_eval/data/manifest.json` 的參考轉寫、`experiments/translate_eval/data/`
的 WMT25 ja 原文、`translate_eval/vtuber_extra.json`、`translate_benchmark/lines.json`）；
辭典 zip 由這裡依正式程式碼相同的固定網址與 SHA-256 下載到 `data/`（不進 repo）。
結果寫到 `results/`（不進 repo），摘要抄進 `README.md`。

用法：uv run python -I -X utf8 experiments/furigana_dict/compare.py
（互動選單；語料位置預設為這個 repo 的 experiments/，可改成主工作目錄）
"""

import ctypes
import hashlib
import json
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
RESULTS = HERE / "results"

# 固定網址、大小與 SHA-256；core 與 src/kikitori/furigana/dictionary.py 正式採用的版本相同
DICTIONARIES = {
    "small": (
        (
            "https://github.com/WorksApplications/SudachiDict/releases/download/"
            "v20260428/sudachi-dictionary-20260428-small.zip"
        ),
        41770309,
        "20e86a8f5fbb8ff94aae38beaa2fae9e0593309f0cc65def0c61826160bfe092",
        "sudachi-dictionary-20260428/system_small.dic",
    ),
    "core": (
        (
            "https://github.com/WorksApplications/SudachiDict/releases/download/"
            "v20260428/sudachi-dictionary-20260428-core.zip"
        ),
        72238136,
        "40c8ffc095283f07aa06cae922e7b8147bf2919ec8830567b0b3f7a7efa3239f",
        "sudachi-dictionary-20260428/system_core.dic",
    ),
}


def _rss() -> tuple[int, int]:
    """(工作集, 私有記憶體) 位元組；只支援 Windows，其他平台回 (0, 0)。"""
    if sys.platform != "win32":
        return (0, 0)

    class Counters(ctypes.Structure):
        _fields_ = [
            ("cb", ctypes.c_ulong),
            ("PageFaultCount", ctypes.c_ulong),
            ("PeakWorkingSetSize", ctypes.c_size_t),
            ("WorkingSetSize", ctypes.c_size_t),
            ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPagedPoolUsage", ctypes.c_size_t),
            ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
            ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
            ("PagefileUsage", ctypes.c_size_t),
            ("PeakPagefileUsage", ctypes.c_size_t),
            ("PrivateUsage", ctypes.c_size_t),
        ]

    counters = Counters()
    counters.cb = ctypes.sizeof(Counters)
    kernel32 = ctypes.windll.kernel32
    kernel32.GetCurrentProcess.restype = ctypes.c_void_p
    ok = kernel32.K32GetProcessMemoryInfo(
        ctypes.c_void_p(kernel32.GetCurrentProcess()),
        ctypes.byref(counters),
        counters.cb,
    )
    if not ok:
        raise OSError("K32GetProcessMemoryInfo failed")
    return int(counters.WorkingSetSize), int(counters.PrivateUsage)


def fetch(edition: str) -> Path:
    """下載並驗證辭典 zip，解出 .dic；已存在就直接回傳。"""
    url, size, sha256, member = DICTIONARIES[edition]
    DATA.mkdir(exist_ok=True)
    dic = DATA / edition / Path(member).name
    if dic.exists():
        return dic
    archive = DATA / f"{edition}.zip"
    if not archive.exists():
        print(f"下載 {url}")
        with urllib.request.urlopen(url, timeout=60) as response:
            archive.write_bytes(response.read())
    data = archive.read_bytes()
    if len(data) != size or hashlib.sha256(data).hexdigest() != sha256:
        archive.unlink()
        raise RuntimeError(f"{archive.name}: 大小或 SHA-256 不符，已刪除")
    with zipfile.ZipFile(archive) as zf:
        dic.parent.mkdir(exist_ok=True)
        dic.write_bytes(zf.read(member))
    return dic


def corpus(root: Path) -> list[str]:
    """其他實驗的日文句子（去重、保留順序）。"""
    sentences: list[str] = []
    manifest = root / "vtuber_eval" / "data" / "manifest.json"
    if manifest.exists():
        items = json.loads(manifest.read_text(encoding="utf-8"))["items"]
        sentences += [i["reference"] for i in items if i.get("condition") == "clean"]
    for name in ("zh_speech.json", "zh_social.json"):
        path = root / "translate_eval" / "data" / name
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            sentences += [s["src"] for s in data["segments"]]
    for path in (
        root / "translate_eval" / "vtuber_extra.json",
        root / "translate_benchmark" / "lines.json",
    ):
        if path.exists():
            lines = json.loads(path.read_text(encoding="utf-8"))["lines"]
            sentences += [
                line["text"] for line in lines if line.get("source", "ja") == "ja"
            ]
    return [s for s in dict.fromkeys(x.strip() for x in sentences) if s]


def measure(edition: str, root: Path) -> None:
    """（子行程）一個辭典的讀音、時間與記憶體，寫到 results/<edition>.json。"""
    from kikitori.furigana.analyzer import FuriganaAnalyzer
    from kikitori.furigana.ruby import has_kanji, is_kanji

    sentences = corpus(root)
    dic = fetch(edition)
    base = _rss()
    t0 = time.perf_counter()
    analyzer = FuriganaAnalyzer(dic)
    load_s = time.perf_counter() - t0
    after_load = _rss()
    t0 = time.perf_counter()
    rubies = [analyzer.ruby(s) for s in sentences]
    analyze_s = time.perf_counter() - t0
    after_use = _rss()
    kanji = sum(sum(map(is_kanji, s)) for s in sentences)
    covered = sum(
        sum(sum(map(is_kanji, s[a:b])) for a, b, _ in r)
        for s, r in zip(sentences, rubies, strict=True)
    )
    oov = 0
    for s in sentences:
        for t in analyzer.tokens(s):
            if has_kanji(t.surface) and not t.reading:
                oov += 1
    analyzer.close()
    after_close = _rss()
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / f"{edition}.json").write_text(
        json.dumps(
            {
                "edition": edition,
                "dic_bytes": dic.stat().st_size,
                "zip_bytes": DICTIONARIES[edition][1],
                "sentences": len(sentences),
                "kanji_chars": kanji,
                "kanji_with_ruby": covered,
                "oov_kanji_tokens": oov,
                "load_s": round(load_s, 3),
                "analyze_ms_per_sentence": round(analyze_s * 1000 / len(sentences), 3),
                "rss": {
                    "base": base,
                    "after_load": after_load,
                    "after_analyzing_corpus": after_use,
                    "after_close": after_close,
                },
                "ruby": rubies,
                "text": sentences,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def compare() -> None:
    """兩個辭典的讀音差異：句數、差異清單（results/diff.txt）。"""
    small = json.loads((RESULTS / "small.json").read_text(encoding="utf-8"))
    core = json.loads((RESULTS / "core.json").read_text(encoding="utf-8"))
    lines = []
    differing = 0
    for text, a, b in zip(small["text"], small["ruby"], core["ruby"], strict=True):
        if a != b:
            differing += 1
            lines.append(text)
            lines.append(f"  small: {[(text[s:e], r) for s, e, r in a]}")
            lines.append(f"  core : {[(text[s:e], r) for s, e, r in b]}")
    (RESULTS / "diff.txt").write_text("\n".join(lines), encoding="utf-8")
    mib = 1 << 20
    for d in (small, core):
        rss = d["rss"]
        print(
            f"{d['edition']:5}: zip {d['zip_bytes'] / mib:.1f} MiB, dic {d['dic_bytes'] / mib:.1f} MiB, "
            f"載入 {d['load_s']} s, 每句 {d['analyze_ms_per_sentence']} ms, "
            f"漢字覆蓋 {d['kanji_with_ruby']}/{d['kanji_chars']}, 未登錄含漢字詞 {d['oov_kanji_tokens']}"
        )
        print(
            "       工作集／私有 MiB：基準 "
            + "、".join(
                f"{k} {v[0] / mib:.1f}/{v[1] / mib:.1f}" for k, v in rss.items()
            )
        )
    print(
        f"句數 {small['sentences']}，讀音不同 {differing} 句（{RESULTS / 'diff.txt'}）"
    )


def main() -> None:
    """互動選單。"""
    if len(sys.argv) == 3 and sys.argv[1] in DICTIONARIES:
        measure(sys.argv[1], Path(sys.argv[2]))
        return
    default_root = HERE.parent
    while True:
        print("\n1) 量測 small 與 core 並比較\n2) 只比較（用已有的 results/）\n3) 結束")
        choice = input("選擇：").strip()
        if choice == "1":
            root = input(f"語料所在的 experiments 資料夾 [{default_root}]：").strip()
            root_path = Path(root) if root else default_root
            print(f"語料 {len(corpus(root_path))} 句")
            for edition in DICTIONARIES:
                # 每個辭典在獨立的行程量測，記憶體互不影響
                subprocess.run(
                    [
                        sys.executable,
                        "-I",
                        "-X",
                        "utf8",
                        __file__,
                        edition,
                        str(root_path),
                    ],
                    check=True,
                    timeout=1800,
                )
            compare()
        elif choice == "2":
            compare()
        elif choice == "3":
            return


if __name__ == "__main__":
    main()
