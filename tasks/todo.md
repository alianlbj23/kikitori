# 任務清單：vad-segmenter

> 規格：`docs/specs/vad-segmenter.md`；計畫：`tasks/plan.md`。
> 每個任務完成後停下來給使用者確認，確認後才開始下一個。
> 每個任務的共同驗證：`uv run python -m unittest discover -s tests`、`uv run ruff check` 全部通過。

## Phase 1：基礎

### Task 1：加入依賴並打包 Silero 模型
- [ ] 查 `onnxruntime`、`numpy` 當時的穩定版，給使用者確認後用 `uv add <pkg>==<version>` 加入
- [ ] 從官方 tag `v6.2.3` 取得 `silero_vad.onnx`，計算 SHA-256；放入 `src/kikitori/vad/models/`，附 `LICENSE-silero-vad` 與 `README.md`（版本、來源網址、SHA-256）
- [ ] `silero.py` 用 `importlib.resources` 載入模型並驗證 SHA-256；列出模型實際的輸入輸出名稱與形狀，與規格比對
- [ ] 測試：打包的模型雜湊相符；內容不符的模型檔載入時丟出錯誤
- [ ] `uv build` 產生的 wheel 內含 `silero_vad.onnx` 與 `LICENSE-silero-vad`
- 依賴：無
- 檔案：`pyproject.toml`、`uv.lock`（經 uv）、`src/kikitori/vad/__init__.py`、`src/kikitori/vad/silero.py`、`src/kikitori/vad/models/*`、`tests/test_silero_vad.py`
- 規模：M

### Task 2：SileroVad 推論
- [ ] `VoiceModel` 介面（`model.py`）：`probability(window) -> float`、`reset()`
- [ ] `SileroVad` 實作：只接受 512 樣本一維 int16／float32；前接 64 樣本前文（共 576）；state `(2, 1, 128)` 跨呼叫保存；`reset()` 清空前文與 state；`context_samples` 診斷參數
- [ ] 測試（記錄輸入的假 ONNX session）：輸入 576 樣本、前 64 樣本等於上一視窗結尾、第一個視窗前文為零、state 串接、reset 後歸零
- [ ] 測試（錯誤路徑）：長度、dtype、維度錯誤時丟 `ValueError`
- [ ] 測試（真模型）：靜音、正弦波機率低於 0.5；reset 後同輸入得同輸出
- 依賴：Task 1
- 檔案：`src/kikitori/vad/model.py`、`src/kikitori/vad/silero.py`、`tests/test_silero_vad.py`
- 規模：S

### Task 3：白噪音與粉紅噪音產生器
- [ ] `evaluation/noise.py`：給定長度、RMS 等級、亂數種子產生白噪音與粉紅噪音（int16，16 kHz）
- [ ] 測試：同種子輸出相同；RMS 誤差在容許範圍；粉紅噪音頻譜斜率約 −3 dB/octave
- [ ] 測試（真模型）：白噪音、粉紅噪音整段機率低於 0.5
- 依賴：Task 2
- 檔案：`src/kikitori/evaluation/__init__.py`、`src/kikitori/evaluation/noise.py`、`tests/test_evaluation.py`
- 規模：S

### Checkpoint 1：模型可用
- [ ] 測試與 lint 全部通過
- [ ] 使用者確認

## Phase 2：切句

### Task 4：Segmenter 基本事件與停頓結束
- [ ] 事件 dataclass：`UtteranceStarted`、`UtteranceAudio`、`UtteranceEnded`
- [ ] `Segmenter`：任意長度輸入累積成 512 樣本視窗；遲滯門檻（開始 0.5、結束 = 開始 − 0.15）；`min_speech_ms` 250；`pre_roll_ms` 200；`flush()`
- [ ] `EndpointPolicy` 介面與 `PauseEndpoint`（`pause_secs` 0.6）
- [ ] `ResetPolicy` 列舉，本任務實作 (a) `STREAM_START`；未指定時建構失敗
- [ ] 測試（假模型）：開始 → 音訊更新 → pause 結束；短人聲不觸發；遲滯；`flush`；分批大小 160／512／1000 事件完全相同
- 依賴：Task 2
- 檔案：`src/kikitori/vad/segmenter.py`、`src/kikitori/vad/endpoint.py`、`src/kikitori/vad/__init__.py`、`tests/test_vad_segmenter.py`
- 規模：M

### Task 5：最長句長與外部提早結束
- [ ] `PauseEndpoint` 加入 `max_secs`（10 秒），結束原因 `max_length`
- [ ] `Segmenter.request_end()`：下一個視窗結束目前句子，原因 `external`；沒有句子時無效果
- [ ] 測試：超過最長句長被切斷；`request_end` 正常與無句子時的行為
- 依賴：Task 4
- 檔案：`src/kikitori/vad/endpoint.py`、`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`
- 規模：S

### Task 6：重置策略 (b)(c)
- [ ] (b) `AFTER_UTTERANCE`：每句結束後重置
- [ ] (c) `AFTER_LONG_NON_SPEECH`：連續非人聲超過 `reset_after_non_speech_secs`（2.0）且最近 5 視窗機率 < 0.1 時重置，之後每再經過同樣長度才再重置
- [ ] 測試：三種策略各自的 `reset()` 呼叫時機與次數
- 依賴：Task 4
- 檔案：`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`
- 規模：S

### Task 7：真模型端到端檢查
- [ ] 測試：`SileroVad` + `Segmenter` 處理白噪音、粉紅噪音音軌，三種重置策略都產生 0 句
- [ ] 測試：`src/kikitori/` 所有模組都不匯入 torch、pipecat
- 依賴：Task 3、5、6
- 檔案：`tests/test_vad_integration.py`
- 規模：S

### Checkpoint 2：模組可用
- [ ] 測試與 lint 全部通過
- [ ] 使用者確認

## Phase 3：評測工具

### Task 8：評測指標
- [ ] `evaluation/metrics.py`：逐格 ROC-AUC（numpy 排名法）、誤報率、漏報率、開始偵測延遲（中位數、P90）、提早切斷次數、漏抓段數
- [ ] 測試：每個指標用手算的小例子驗證，含邊界情況（全為人聲、全為非人聲）
- 依賴：無（可與 Phase 2 平行）
- 檔案：`src/kikitori/evaluation/metrics.py`、`tests/test_evaluation.py`
- 規模：S

### Task 9：混音
- [ ] `evaluation/mixing.py`：「間隔填入」（只替換句間靜音）與「人聲底下疊加」（SNR 20／10／5 dB），輸出音訊與正確答案（沿用 `ground_truth.json` 格式）
- [ ] 測試：間隔填入不改動人聲區間樣本；疊加後實際 SNR 誤差在容許範圍；正確答案位置不變
- 依賴：Task 3
- 檔案：`src/kikitori/evaluation/mixing.py`、`tests/test_evaluation.py`
- 規模：S

### Task 10：評測選單：產生音軌與執行評測
- [ ] `experiments/silero_reset/menu.py`：`input()` 編號選單，選項「產生評測音軌」「執行評測」「離開」，輸入錯誤時重新詢問
- [ ] 產生音軌：用本機動漫、VTuber 音軌與噪音產生兩種版本；缺少本機音軌時提示如何產生
- [ ] 執行評測：三種重置策略 × 各音軌版本，加上 `context_samples=0` 對照，輸出指標表與推論時間；人聲區間平均機率低於正常設定一半時標記「機率異常偏低」
- [ ] `.gitignore` 加入 `experiments/silero_reset/data/`
- [ ] 測試：用模擬的 `input()` 驅動選單流程；評測流程以小型合成音軌跑通
- 依賴：Task 7、8、9
- 檔案：`experiments/silero_reset/menu.py`、`experiments/silero_reset/evaluate.py`、`.gitignore`、`tests/test_menu.py`
- 規模：M

### Task 11：評測選單：下載 MUSAN
- [ ] `experiments/silero_reset/musan.py`：顯示網址、大小（約 11.09 GB）、所需空間、授權，確認後才下載；`urllib` 加 timeout；HTTP Range 續傳；進度顯示；只解壓 `music/`、`noise/`；寫入出處說明檔；詢問是否刪除壓縮檔
- [ ] 選單加入「下載 MUSAN」；產生音軌時若 MUSAN 已就緒，加產 MUSAN 兩種版本
- [ ] 測試（本機假伺服器、小型假壓縮檔）：未確認不下載；續傳；只解壓指定資料夾；timeout 與 HTTP 錯誤
- 依賴：Task 10
- 檔案：`experiments/silero_reset/musan.py`、`experiments/silero_reset/menu.py`、`tests/test_menu.py`
- 規模：M

### Checkpoint 3：評測工具可用
- [ ] 測試與 lint 全部通過
- [ ] 使用者確認

## Phase 4：評測與文件

### Task 12：執行評測並決定重置策略預設值
- [ ] 下載 MUSAN（方式見 `tasks/plan.md` Open Questions）
- [ ] 跑完所有音軌版本；結果寫入 `experiments/silero_reset/README.md`
- [ ] 依結果設定 `ResetPolicy` 預設值，更新程式、測試與規格
- [ ] 確認原始動漫、VTuber 音軌上至少一種策略抓到全部 12 段；每視窗平均推論時間 < 1 ms
- 依賴：Task 11
- 檔案：`experiments/silero_reset/README.md`、`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`、`docs/specs/vad-segmenter.md`
- 規模：S

### Task 13：架構決策紀錄與架構文件
- [ ] 用 `documentation-and-adrs` 寫 ADR：VAD 改用 Silero ONNX、不用 pipecat，以及重置策略的選擇
- [ ] `docs/architecture.md` 補上 VAD 切句模組段落
- 依賴：Task 12
- 檔案：`docs/adr/0001-*.md`、`docs/architecture.md`
- 規模：S

### Checkpoint 4：完成
- [ ] 規格的 Success Criteria 全部達成
- [ ] 使用者 review
