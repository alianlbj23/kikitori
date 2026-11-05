# ADR-0001：VAD 直接用 Silero ONNX（不用 pipecat），重置策略預設「長時間非人聲後重置」

## 狀態

已採用（Accepted）。里程碑 review 時由使用者確認。

## 日期

2026-11-05

## 背景

kikitori 需要把系統播放的聲音切成一句一句交給語音辨識（見 `docs/specs/vad-segmenter.md`）。可行性實驗（`experiments/pipecat_vad/README.md`）用 pipecat-ai 1.14.0 的 `SileroVADAnalyzer` 驗證過 Silero VAD 可用，但也發現：

- pipecat 帶來約 290 MB 依賴（numba、llvmlite、openai 等），VAD 真正用到的只有 onnxruntime、numpy 與 3 MB 的模型檔。
- 取得每 32 ms 的機率、關掉自動重置都必須覆寫私有方法，升級就可能壞掉。
- 時間用 `time.monotonic()` 計算，離線比即時快餵資料時位置會算錯。
- 它每約 2 秒在 QUIET 時重置 Silero 狀態；實驗中重置落在語音開頭時整句漏抓。

Silero 官方對重置時機的說法不一致：程式與 wiki 只在換串流時重置；維護者在 discussion #791 對「每句結束後重置」回答 "Ideally yes"；沒有任何官方來源建議定時重置（見 `research_notes/VAD 與句尾判斷技術調查/silero_streaming_state.md`）。另一方面，pipecat #6207 回報長時間音樂或靜音後狀態會偏移、低報下一句。

## 決策

1. **直接用 `onnxruntime` 跑 Silero VAD v6.2.3 官方 ONNX 模型**，模型檔（MIT）與授權全文打包進套件，載入時驗證 SHA-256。推論細節照官方 `OnnxWrapper`：512 樣本視窗前接 64 樣本前文、state `(2, 1, 128)` 跨呼叫保存。
2. 模型層以 `VoiceModel` 介面抽象；切句邏輯（`Segmenter`）全部用樣本計數，句尾判斷以 `EndpointPolicy` 介面替換，另提供 `request_end()` 給外部提早結束。
3. 重置策略做成 `ResetPolicy` 三選一，**預設 `AFTER_LONG_NON_SPEECH`**：連續 2 秒沒有句子也沒有候選人聲、且最近 5 個視窗機率都 < 0.1 時重置，之後每再經過 2 秒才再重置。

## 考慮過的替代方案

### 繼續用 pipecat 的 `SileroVADAnalyzer`
- 優點：現成、已有 Smart Turn 整合。
- 缺點：約 290 MB 依賴、需覆寫私有方法、牆鐘時間計算、重置策略寫死。
- 不採用：VAD 只需要 onnxruntime + numpy，自己包一層（`silero.py` 約 180 行）就能完全掌握前文、state 與時間計算。

### 用 PyTorch 版 `silero-vad` 套件
- 優點：官方 API，前文在模型內部處理。
- 缺點：PyTorch 依賴遠比 onnxruntime 大，與「輕量、跨平台」的目標不符。
- 不採用。

### 重置策略 (a) `STREAM_START`：只在換串流時重置（官方程式的做法）
- 評測中乾淨音軌與 (c) 相同；長間隔的噪音背景比 (c) 好（MUSAN 雜音漏抓 0/48 vs 2/48、白／粉紅 10/96 vs 12/96）。
- 但句間是幾秒的音樂時，狀態被帶偏、下一句開頭被低報：純音樂長間隔版面漏抓 9/48（(c) 7/48）、開始延遲 P90 1469 ms（(c) 1021 ms）；有人聲音樂 6/48（(c) 3/48）。換兩組曲目重複仍是 (a) 較差（純音樂 13/96 vs 7/96）。
- 不採用為預設：觀看媒體（情境 A）常有配樂，這正是主要使用情境；但兩者差距不大，雜訊場景為主時 (a) 是合理的替代。

### 重置策略 (b) `AFTER_UTTERANCE`：每句結束後重置（維護者 "Ideally yes"）
- 每句結束立刻重置，下一句開頭剛好落在模型暖機期間：開始延遲 P90 普遍拉長到 1.2–2.2 秒，噪音原始版面漏抓 16/96（(a)(c) 9/96）。
- 不採用。

## 影響

- 正式程式碼只依賴 `onnxruntime==1.31.1`、`numpy==2.5.4`；pipecat 只留在 `experiment-vad` 依賴群組供舊實驗重跑，並以測試確認 `src/kikitori/` 不匯入 torch／pipecat。
- 換 Silero 版本時要同時更新模型檔、常數、`models/README.md`、授權檔，並重跑 `experiments/silero_reset/` 的評測（前文對照可當回歸檢查：不接前文時機率塌到約 0.001）。
- (c) 的代價：重置剛好落在語音開頭前時，那一句的開頭會晚一點確認；長間隔的噪音背景多漏 2 段、P90 多約 0.1–0.24 秒。整體只是小幅領先（84 條音軌漏抓 42/504 vs (a) 43/504，P90 820 vs 876 ms）。樣本只有 12 段人聲、每條背景 6–8 個檔案，之後有更多真實素材（含配樂的實錄節目）時應重跑並視結果重新檢討本決策。
- 數據：`experiments/silero_reset/README.md`。
