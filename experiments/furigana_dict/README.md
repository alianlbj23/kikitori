# 振假名辭典選型：SudachiDict small vs core

> 2026-11-06。結論與正式規格見 `docs/specs/furigana.md`「辭典選擇」。暫時性實驗工具，不屬於正式程式碼。

## 跑法

```bash
uv run python -I -X utf8 experiments/furigana_dict/compare.py
```

互動選單：「量測 small 與 core 並比較」會問語料所在的 `experiments/` 資料夾（預設這個 repo；`vtuber_eval/data/`、
`translate_eval/data/` 不進 repo，在 git worktree 裡要指到主工作目錄），下載兩個辭典 zip（固定網址、大小、SHA-256）到 `data/`，
每個辭典在獨立的子行程量測（記憶體互不影響），結果寫到 `results/small.json`、`results/core.json`、`results/diff.txt`（都不進 repo）。

量測用正式的 `kikitori.furigana.analyzer.FuriganaAnalyzer` 與對齊演算法，所以「讀音不同」就是使用者會看到的差異。

## 語料（2014 句，去重）

- `vtuber_eval/data/manifest.json` 的 clean 參考轉寫（YODAS 日語直播／影片、FLEURS、ReazonSpeech、動畫）。
- `translate_eval/data/zh_speech.json`、`zh_social.json` 的日文原文（WMT25）。
- `translate_eval/vtuber_extra.json`、`translate_benchmark/lines.json`（直播用語補充句）。

## 結果（Windows 11、Ryzen 7 5700X、SudachiPy 0.6.11、SudachiDict v20260428）

| | small | core |
|---|---|---|
| 下載（zip） | 39.8 MiB | 68.9 MiB |
| 解壓後 `.dic` | 117.3 MiB | 207.3 MiB |
| 開啟辭典 | 0.028 s | 0.035 s |
| 每句分析 | 0.129 ms | 0.152 ms |
| 漢字有標讀音 | 15654 / 15685 | 15656 / 15685 |
| 含漢字的未登錄詞 | 0 | 0 |
| 工作集／私有記憶體：開啟前 → 開啟後 → 分析完 → 關閉後（MiB） | 28.3/20.2 → 56.0/33.9 → 96.0/34.0 → 31.1/21.0 | 28.0/19.9 → 83.6/47.7 → 134.6/47.9 → 31.1/20.3 |

讀音不同：86 句（4.3%）。逐詞統計（同一個詞在不同句子重複計）：

| 詞 | small | core | 正確 |
|---|---|---|---|
| 私（たち） | わたくし ×10 | わたし | core |
| 〜人（韓国人、有名人…） | にん ×10 | じん | core |
| 日本（語） | にっぽん ×7 | にほん | core |
| （水曜）日 | ひ ×4 | び | core |
| （お）兄（ちゃん） | あに ×4 | にい | core |
| 一度 | ひとたび ×3 | いちど | core |
| （お）父（さん） | ちち ×3 | とう | core |
| （お）姉（ちゃん） | あね ×2 | ねえ | core |
| 自動車、自転車、八丈島、令和、内牧 | 拆成單字讀 | 整詞讀 | core |
| 腕時計、意気込み、時間切れ、背表紙、箇条書 | 漏連濁 | 連濁 | core |
| 林修、杉田、近本、米夏 | はやし おさむ、すぎた… | りお、すいた… | small（人名） |

core 錯的只有少數人名；small 錯在直播口語最常見的詞上。選 core。

## SudachiPy 版本

0.7.0 改用 V1 辭典格式（SudachiDict v20261015 起），README 自述 0.7.* 不穩定、patch 版本之間也可能不相容。實測 0.6.11 讀不了
v20261015（`Invalid header version`）、0.7.0 讀不了 v20260428（`Invalid description: V0 version`）。正式採用 0.6.11＋v20260428
（有 zip 的最後一個 V0 版本）。
