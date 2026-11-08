"""VTuber 直播翻譯評測（Milestone 16）的互動選單。

暫時性實驗工具，不屬於正式程式碼，也不會被 unittest 收集。用正式的
`kikitori.translate` 翻譯器跑 WMT25 ja→zh、CoVoST 2／BSD ja→en，計分在隔離環境
（`uv run --isolated`）進行，sacrebleu 與 COMET（PyTorch）都不進專案依賴。

用法：uv run python -I -X utf8 experiments/translate_eval/menu.py
"""

import os
import shutil
import subprocess
import sys
import urllib.error
from pathlib import Path

# `python -I` 不會把腳本所在目錄放進 sys.path；這裡只加入本目錄（自己寫的模組），
# 下載的資料放在 data/ 子目錄，不會被當成模組載入。
sys.path.insert(0, str(Path(__file__).resolve().parent))

import datasets
import report
import run
import vtuber
from common import (
    DATA_DIR,
    EVAL_SETS,
    HERE,
    SCORING_DIR,
    SYSTEMS,
    SYSTEMS_BY_KEY,
    SystemConfig,
    output_path,
    read_json,
    read_jsonl,
    set_path,
    system_applies,
    with_glossary,
)

from kikitori.translate import TranslationError

# 品質評測的執行順序：小模型先跑，VRAM 需求最大的 Qwen3.5 9B 最後
# （GPU 與其他工作共用時，9B 最容易被 Ollama 部分放到 CPU）。
RUN_ORDER = [
    "q4-preset-ctx",
    "q4-t0-ctx",
    "q4-t0-noctx",
    "hy18-preset",
    "hy18-t0",
    "hy7-preset",
    "hy7-t0",
    "sakura-preset",
    "q9-preset-ctx",
    "q9-t0-ctx",
    "q9-t0-noctx",
    "q9-m16",
    "q9-m16-gloss",
]
SCORE_TIMEOUT = 30 * 60  # 秒；sacrebleu 計分加 bootstrap
COMET_TIMEOUT = 6 * 60 * 60  # 秒；CPU 上跑 COMET 很慢，第一次還要下載模型


def choose(prompt: str, options: list[str]) -> int | None:
    """顯示編號選單，回傳選到的索引；輸入 0 或空白回傳 None。"""
    for i, option in enumerate(options, 1):
        print(f"  {i}) {option}")
    print("  0) 返回")
    while True:
        answer = input(prompt).strip()
        if answer in ("", "0"):
            return None
        if answer.isdigit() and 1 <= int(answer) <= len(options):
            return int(answer) - 1
        print("請輸入選單上的編號。")


def choose_system() -> str | None:
    """選一組受測設定；回傳 key。"""
    index = choose("設定：", [f"{s.key}　{s.label}" for s in SYSTEMS])
    return None if index is None else SYSTEMS[index].key


def picked_system(key: str, glossary: bool) -> SystemConfig:
    """選到的設定；術語表開關打開時換成用內建術語表的變體（key 加 `-gloss`）。"""
    system = SYSTEMS_BY_KEY[key]
    return with_glossary(system) if glossary else system


def choose_sets(sets: list[str]) -> list[str]:
    """要跑的資料集：全部或其中一個。"""
    index = choose("資料集：", ["全部", *sets])
    if index is None:
        return []
    return sets if index == 0 else [sets[index - 1]]


def require_data() -> bool:
    """資料集還沒下載時提示先準備資料。"""
    missing = datasets.missing_sets()
    if missing:
        print(f"缺少資料集：{', '.join(missing)}；請先選「準備資料」。")
        return False
    return True


def isolated(requirements: str, script: str, *args: str, timeout: float) -> None:
    """在暫時的隔離環境執行計分腳本（依賴由 requirements 檔固定版本）。"""
    uv = shutil.which("uv")
    if uv is None:
        print("找不到 uv。")
        return
    cmd = [
        uv,
        "run",
        "--isolated",
        "--no-project",
        "--python",
        "3.12",
        "--with-requirements",
        str(HERE / requirements),
        "python",
        "-I",
        "-X",
        "utf8",
        str(HERE / script),
        *args,
    ]
    # Hugging Face 的快取（COMET 用的 xlm-roberta tokenizer）也放進 git-ignore 的 data/。
    env = {**os.environ, "HF_HOME": str(DATA_DIR / "hf_cache")}
    try:
        subprocess.run(cmd, cwd=HERE, env=env, timeout=timeout, check=True)
    except subprocess.CalledProcessError as exc:
        print(f"{script} 失敗（exit {exc.returncode}），見上方訊息。")
    except subprocess.TimeoutExpired:
        print(f"{script} 超過 {timeout / 60:.0f} 分鐘，已中止。")


def show_progress() -> None:
    """每組設定在每個資料集翻了幾句。"""
    header = "| 設定 | " + " | ".join(EVAL_SETS) + " |"
    print(header)
    print("|---" * (len(EVAL_SETS) + 1) + "|")
    totals = {
        name: len(read_json(set_path(name))["segments"])
        for name in EVAL_SETS
        if set_path(name).exists()
    }
    for system in SYSTEMS:
        cells = []
        for name, eval_set in EVAL_SETS.items():
            if name not in totals:
                cells.append("無資料")
            elif not system_applies(system, eval_set):
                cells.append("不適用")
            else:
                rows = read_jsonl(output_path(system.key, name))
                errors = sum(1 for r in rows if r.get("error"))
                cells.append(f"{len(rows)}/{totals[name]}（錯 {errors}）")
        print(f"| {system.key} | " + " | ".join(cells) + " |")


def main() -> None:
    actions = [
        "準備資料（下載 WMT25／CoVoST 2／BSD 到 data/）",
        "品質評測：全部設定（可續跑；同模型的設定平行跑）",
        "品質評測：選一組設定",
        "只量延遲：全部設定（GPU 空閒時用）",
        "只量延遲：選一組設定",
        "計分：chrF／BLEU＋paired bootstrap（隔離環境 sacrebleu）",
        "計分：COMET wmt22-comet-da（隔離環境，CPU）",
        "產生報表（錯誤統計＋表格 → results/report.md）",
        "VTuber 句：選一組設定翻譯",
        "VTuber 句：顯示並排表格",
        "顯示進度",
        "切換術語表（影響「選一組設定」與「VTuber 句」）",
    ]
    sets = list(EVAL_SETS)
    glossary = False
    while True:
        print("\nVTuber 直播翻譯評測")
        print(f"術語表：{'開（內建直播用語）' if glossary else '關'}")
        picked = choose("選擇：", actions)
        if picked is None:
            return
        try:
            if picked == 0:
                datasets.prepare_all()
            elif picked == 1 and require_data():
                # 同一個模型的設定同時跑（Ollama 平行處理），不同模型依序跑。
                run.run_quality_grouped(RUN_ORDER, sets)
            elif picked == 2 and require_data():
                key = choose_system()
                if key is not None:
                    names = choose_sets(sets)
                    if names:
                        run.run_quality(picked_system(key, glossary), names)
            elif picked in (3, 4) and require_data():
                keys = RUN_ORDER if picked == 3 else [choose_system()]
                for key in keys:
                    if key is not None:
                        s = run.run_latency(SYSTEMS_BY_KEY[key], sets)
                        print(
                            f"{key}：首字 P50 {s['ttft_p50']:.2f}s P90 {s['ttft_p90']:.2f}s，"
                            f"總延遲 P50 {s['total_p50']:.2f}s P90 {s['total_p90']:.2f}s"
                        )
            elif picked == 5 and require_data():
                for line in report.build_scoring_inputs():
                    print(line)
                isolated(
                    "requirements-score.txt",
                    "score.py",
                    str(SCORING_DIR),
                    timeout=SCORE_TIMEOUT,
                )
            elif picked == 6 and require_data():
                for line in report.build_scoring_inputs():
                    print(line)
                isolated(
                    "requirements-comet.txt",
                    "score_comet.py",
                    str(SCORING_DIR),
                    str(DATA_DIR / "comet_model"),
                    timeout=COMET_TIMEOUT,
                )
            elif picked == 7 and require_data():
                print(report.render_report())
            elif picked == 8:
                key = choose_system()
                if key is not None:
                    vtuber.run_vtuber(picked_system(key, glossary))
            elif picked == 9:
                for target in ("zh-TW", "en"):
                    print(f"\n#### {target}\n")
                    print(vtuber.render_table(target))
            elif picked == 10:
                show_progress()
            elif picked == 11:
                glossary = not glossary
        except (urllib.error.URLError, TimeoutError, OSError, TranslationError) as exc:
            print(f"失敗：{type(exc).__name__}: {exc}")


if __name__ == "__main__":
    main()
