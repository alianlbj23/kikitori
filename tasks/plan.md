# Implementation Plan: vad-segmenter

## Overview

依 `docs/specs/vad-segmenter.md` 實作 VAD 切句模組：直接用 `onnxruntime` 跑打包進套件的 Silero VAD v6.2.3，輸出「一句開始／音訊更新／一句結束」事件；重置策略與句尾判斷都可替換；並建立以白噪音、粉紅噪音、MUSAN 與本機動漫／VTuber 片段為素材的評測流程，用評測結果決定重置策略的預設值。

**進行方式**：一次只做一個任務，每個任務完成（測試與 lint 通過）後停下來給使用者確認，確認後才開始下一個。任務細節（驗收條件、驗證方式、檔案）在 `tasks/todo.md`。

## Architecture Decisions

- **不用 pipecat**：自己包 Silero ONNX，避免約 290 MB 依賴、私有方法覆寫、`time.monotonic()` 計時問題。
- **`VoiceModel` 介面**：`Segmenter` 只依賴「512 樣本 → 機率」與 `reset()`，模型可替換，事件邏輯可用假模型做決定性測試。
- **時間全用樣本計數**：同一段音訊不論分幾批餵入，事件與位置都相同。
- **重置策略不寫死**：(a)(b)(c) 皆可設定，評測後才決定預設值；評測前建構時必須明確指定。
- **評測程式碼放在 `src/kikitori/evaluation/`**：噪音、混音、指標同時給單元測試與評測選單使用；選單與 MUSAN 下載放在 `experiments/silero_reset/`。
- **只用標準庫與 numpy**：選單用 `input()`、下載用 `urllib`、讀寫音訊用 `wave`、ROC-AUC 用 numpy 自算，不新增其他依賴。

## Task List

### Phase 1：基礎
- [ ] Task 1：加入依賴並打包 Silero 模型（含雜湊驗證、wheel 檢查）
- [ ] Task 2：SileroVad 推論（512 + 64 前文、state 串接、reset）
- [ ] Task 3：白噪音與粉紅噪音產生器

### Checkpoint 1：模型可用
- [ ] 測試與 lint 全部通過；真模型對靜音、正弦波、白噪音、粉紅噪音的機率都低於 0.5
- [ ] 使用者確認後才進入 Phase 2

### Phase 2：切句
- [ ] Task 4：Segmenter 基本事件與停頓結束
- [ ] Task 5：最長句長與外部提早結束
- [ ] Task 6：重置策略 (b)(c)
- [ ] Task 7：真模型端到端檢查

### Checkpoint 2：模組可用
- [ ] 純噪音音軌在三種重置策略下都產生 0 句；`src/kikitori/` 不匯入 torch／pipecat
- [ ] 使用者確認後才進入 Phase 3

### Phase 3：評測工具
- [ ] Task 8：評測指標
- [ ] Task 9：混音（間隔填入、依 SNR 疊加）
- [ ] Task 10：評測選單：產生音軌與執行評測
- [ ] Task 11：評測選單：下載 MUSAN

### Checkpoint 3：評測工具可用
- [ ] 選單可在本機跑完所有音軌版本並輸出指標表
- [ ] 使用者確認後才進入 Phase 4

### Phase 4：評測與文件
- [ ] Task 12：執行評測並決定重置策略預設值
- [ ] Task 13：架構決策紀錄與架構文件

### Checkpoint 4：完成
- [ ] 規格的 Success Criteria 全部達成
- [ ] 使用者 review

## Risks and Mitigations

| 風險 | 影響 | 對策 |
|---|---|---|
| `uv_build` 不把 `.onnx` 等非 `.py` 檔打包進 wheel | 高：安裝後找不到模型 | Task 1 就用 `uv build` 實際檢查 wheel 內容，必要時調整 `pyproject.toml` 的打包設定 |
| v6.2.3 ONNX 的輸入／輸出名稱或形狀與調查不同 | 中 | Task 1 載入模型後先列出實際的輸入輸出規格，與規格不符就停下來回報 |
| `onnxruntime` 在 Windows／Python 3.12 的版本相容性 | 中 | Task 1 加依賴前先查穩定版並給使用者確認 |
| MUSAN 11.09 GB，下載久且佔磁碟 | 中：拖慢 Task 12 | 支援續傳；只解壓 `music/` 與 `noise/`；噪音版本不依賴 MUSAN，可先評測 |
| 推論時間的測試在不同機器上不穩定 | 低 | 時間只在評測報告中量測，不放進單元測試的斷言 |
| 測試需要 import `experiments/` 下的選單 | 低 | 用 `importlib` 依檔案路徑載入，不把 `experiments/` 變成套件 |

## Open Questions

- Task 12 的 MUSAN 下載（約 11 GB）要由我透過選單執行（需要你在選單中確認），還是你自己執行選單下載？
