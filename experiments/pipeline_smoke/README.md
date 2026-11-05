# pipeline 端到端實測

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05。規格：`docs/specs/pipeline-engine.md`。

## 怎麼跑

```text
uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py
```

選單：1) 真 ASR＋Ollama 翻譯、2) 假 ASR（固定文字）＋Ollama 翻譯、3) 離開。之後依序詢問音軌（預設 `experiments/pipecat_vad/data/tracks/anime.wav`，本機、git 忽略）、即時／完整 ASR 模型、裝置、翻譯模型。音軌以 `FileSource(realtime=True)` 依即時速度送出，經真的 Silero VAD 與 `Pipeline`（執行緒模式），印出字幕串流與延遲統計；詳細結果（含台詞）寫到 `results/`（git 忽略）。

## 量測定義

- 音訊位置的牆鐘時間 = `FileSource.start()` 的時刻 + 樣本數 ÷ 16000。
- **首個即時字幕**：句子起點（含 200 ms 前置緩衝）→ 第一則即時字幕送出。
- **即時字幕延遲**：每則即時字幕（含串流翻譯的每次更新）送出時 − 它所辨識音訊的結尾。
- **完整字幕延遲**：完整字幕送出時 − 說話結束處（`end_sample`，已裁掉句尾停頓、保留 0.2 s）。包含句尾判斷必須等的靜音（0.6 − 0.2 = 0.4 s）。

## 環境

Ryzen 7 5700X（8C/16T）、64 GB RAM、RTX 5070；**本機沒有 cuBLAS 12**，所以 `device=auto` 退回 CPU int8（ASR 全部在 CPU）。翻譯：本機 Ollama `qwen3.5:4b`（GPU）。音軌 `anime.wav`：33 s、8 句。

## 結果

| 設定 | 首個即時字幕（中位數／P90） | 即時字幕延遲（中位數／P90／最大） | 完整字幕延遲（中位數／P90／最大） | 略過的即時工作 |
|---|---|---|---|---|
| 真 ASR：即時 `base`、完整 `small`（CPU int8）＋ qwen3.5:4b | 1.63 s／2.00 s | 0.85 s／1.14 s／1.17 s | 2.85 s／3.16 s／3.47 s | 0 |
| 假 ASR（只量 VAD＋排程＋翻譯）＋ qwen3.5:4b | 0.80 s／0.83 s | 0.14 s／0.18 s／0.22 s | 0.58 s／0.59 s／0.62 s | 0 |

- 8 句全部產生完整字幕，順序正確；狀態 `running → idle`。
- 假 ASR 那一列是 pipeline 本身的下限：首個即時字幕 ≈ 700 ms 觸發間隔 + 翻譯首字；完整字幕 ≈ 0.4 s 句尾靜音 + 翻譯約 0.15 s。
- 真 ASR 時延遲主要來自 CPU 上的 Whisper：單獨量測 `base` 每次約 0.55 s、`small`（beam 5）約 1.9 s；兩者同時跑時約 0.8 s／2.2 s。完整字幕 ≈ 0.4 s 靜音 + 約 2.2 s 辨識 + 0.15 s 翻譯。有 cuBLAS 的 NVIDIA GPU 依 `docs/specs/asr.md` 可降到 0.13＋0.15 s 級距（本機無 cuBLAS，未量）。
- 同一設定的第一次執行（同時有其他工作佔用 CPU 時）完整字幕延遲中位數 16.7 s、即時 2.9 s，第二次 2.85 s／0.85 s。CPU 被搶時完整佇列會累積——這正是完整路徑「不丟、依序、背壓」的設計，但也說明 CPU 模式需要 hardware 里程碑依核心數選更小的模型。為此即時路徑已改成「上一次即時辨識比間隔慢時，自動把觸發間隔拉長到該耗時」，避免即時路徑把 CPU 佔滿。
