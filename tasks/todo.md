# 任務清單：vad-segmenter

> 規格：`docs/specs/vad-segmenter.md`；計畫：`tasks/plan.md`。
> 每個任務完成後停下來給使用者確認，確認後才開始下一個。
> 每個任務的共同驗證：`uv run python -m unittest discover -s tests`、`uv run ruff check` 全部通過。

## Phase 1：基礎

### Task 1：加入依賴並打包 Silero 模型
- [x] 查 `onnxruntime`、`numpy` 當時的穩定版，給使用者確認後用 `uv add <pkg>==<version>` 加入
- [x] 從官方 tag `v6.2.3` 取得 `silero_vad.onnx`，計算 SHA-256；放入 `src/kikitori/vad/models/`，附 `LICENSE-silero-vad` 與 `README.md`（版本、來源網址、SHA-256）
- [x] `silero.py` 用 `importlib.resources` 載入模型並驗證 SHA-256；列出模型實際的輸入輸出名稱與形狀，與規格比對
- [x] 測試：打包的模型雜湊相符；內容不符的模型檔載入時丟出錯誤
- [x] `uv build` 產生的 wheel 內含 `silero_vad.onnx` 與 `LICENSE-silero-vad`
- 依賴：無
- 檔案：`pyproject.toml`、`uv.lock`（經 uv）、`src/kikitori/vad/__init__.py`、`src/kikitori/vad/silero.py`、`src/kikitori/vad/models/*`、`tests/test_silero_vad.py`
- 規模：M
- 結果：`uv add onnxruntime==1.31.1 numpy==2.5.4`；模型取自 tag v6.2.3（= commit 5cd7945），SHA-256 `1a153a22…88e3`，實際 I/O 為 `input [B,N]`、`state [2,B,128]`、`sr` 純量 → `output [B,1]`、`stateN`，與規格一致；`uv build` 的 wheel 不需改設定即含 `.onnx`、`LICENSE-silero-vad`，在隔離環境安裝後可載入；新增 `.gitattributes` 把 `*.onnx` 標為 binary；測試 19 個全過、ruff 通過。

### Task 2：SileroVad 推論
- [x] `VoiceModel` 介面（`model.py`）：`probability(window) -> float`、`reset()`
- [x] `SileroVad` 實作：只接受 512 樣本一維 int16／float32；前接 64 樣本前文（共 576）；state `(2, 1, 128)` 跨呼叫保存；`reset()` 清空前文與 state；`context_samples` 診斷參數
- [x] 測試（記錄輸入的假 ONNX session）：輸入 576 樣本、前 64 樣本等於上一視窗結尾、第一個視窗前文為零、state 串接、reset 後歸零
- [x] 測試（錯誤路徑）：長度、dtype、維度錯誤時丟 `ValueError`
- [x] 測試（真模型）：靜音、正弦波機率低於 0.5；reset 後同輸入得同輸出
- 依賴：Task 1
- 檔案：`src/kikitori/vad/model.py`、`src/kikitori/vad/silero.py`、`tests/test_silero_vad.py`
- 規模：S
- 結果：`VoiceModel` 用 `typing.Protocol`；`SileroVad` 依官方 v6.2.3 `OnnxWrapper` 實作（int16 ÷ 32768、單執行緒 session、輸出名稱 `output`/`stateN`），另檢查 float32 非有限值與模型輸出形狀；`tests/test_silero_vad.py` 23 個測試全過、ruff 通過。

### Task 3：白噪音與粉紅噪音產生器
- [x] `evaluation/noise.py`：給定長度、RMS 等級、亂數種子產生白噪音與粉紅噪音（int16，16 kHz）
- [x] 測試：同種子輸出相同；RMS 誤差在容許範圍；粉紅噪音頻譜斜率約 −3 dB/octave
- [x] 測試（真模型）：白噪音、粉紅噪音整段機率低於 0.5
- 依賴：Task 2
- 檔案：`src/kikitori/evaluation/__init__.py`、`src/kikitori/evaluation/noise.py`、`tests/test_evaluation.py`
- 規模：S
- 結果：`white_noise`／`pink_noise`（頻域乘 1/√f）／`rms_dbfs`，RMS 誤差 < 0.1 dB、粉紅斜率在 −3 ± 0.3 dB/octave；真模型在 −40／−20／−10 dBFS 的白、粉紅噪音最大機率都 < 0.5；全部 44 個測試通過、ruff 通過。

### Checkpoint 1：模型可用
- [x] 測試與 lint 全部通過
- [ ] 使用者確認（本次為自主執行，由 coordinator 於里程碑結束時一併確認）

## Phase 2：切句

### Task 4：Segmenter 基本事件與停頓結束
- [x] 事件 dataclass：`UtteranceStarted`、`UtteranceAudio`、`UtteranceEnded`
- [x] `Segmenter`：任意長度輸入累積成 512 樣本視窗；遲滯門檻（開始 0.5、結束 = 開始 − 0.15）；`min_speech_ms` 250；`pre_roll_ms` 200；`flush()`
- [x] `EndpointPolicy` 介面與 `PauseEndpoint`（`pause_secs` 0.6）
- [x] `ResetPolicy` 列舉，本任務實作 (a) `STREAM_START`；未指定時建構失敗
- [x] 測試（假模型）：開始 → 音訊更新 → pause 結束；短人聲不觸發；遲滯；`flush`；分批大小 160／512／1000 事件完全相同
- 依賴：Task 2
- 檔案：`src/kikitori/vad/segmenter.py`、`src/kikitori/vad/endpoint.py`、`src/kikitori/vad/__init__.py`、`tests/test_vad_segmenter.py`
- 規模：M
- 結果：`reset_policy` 為必填 keyword（缺少時 `TypeError`）；`neg_threshold` 預設 `max(threshold − 0.15, 0.01)`（同官方下限）；`UtteranceEnded.end_sample` 定義為該句已送出音訊的結尾（同一句的 `UtteranceAudio` 首尾相接剛好涵蓋 `[start, end)`）；前置緩衝不越過上一句結尾；`flush()` 結束句子後回到新串流（位置歸零、模型 reset）；分批 1／160／512／1000／4096 事件完全相同；全部 71 個測試通過、ruff 通過。

### Task 5：最長句長與外部提早結束
- [x] `PauseEndpoint` 加入 `max_secs`（10 秒），結束原因 `max_length`
- [x] `Segmenter.request_end()`：下一個視窗結束目前句子，原因 `external`；沒有句子時無效果
- [x] 測試：超過最長句長被切斷；`request_end` 正常與無句子時的行為
- 依賴：Task 4
- 檔案：`src/kikitori/vad/endpoint.py`、`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`
- 規模：S
- 結果：`pause` 與 `max_length` 同時成立時回傳 `pause`；切斷後人聲持續時下一句從切點接續（不重複、不遺漏）；`request_end()` 在候選期間或無句子時無效果、只作用一次、等到下一個完整視窗；全部 79 個測試通過、ruff 通過。

### Task 6：重置策略 (b)(c)
- [x] (b) `AFTER_UTTERANCE`：每句結束後重置
- [x] (c) `AFTER_LONG_NON_SPEECH`：連續非人聲超過 `reset_after_non_speech_secs`（2.0）且最近 5 視窗機率 < 0.1 時重置，之後每再經過同樣長度才再重置
- [x] 測試：三種策略各自的 `reset()` 呼叫時機與次數
- 依賴：Task 4
- 檔案：`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`
- 規模：S
- 結果：(b) 在 `pause`／`max_length`／`external` 結束的視窗後重置（`flush` 只做一次串流重置）；(c) 的「非人聲」定義為沒有進行中句子也沒有候選人聲的視窗（句尾停頓仍屬句子），計數達門檻且最近 5 視窗都 < 0.1 才重置、重置後歸零重新計；全部 91 個測試通過、ruff 通過。

### Task 7：真模型端到端檢查
- [x] 測試：`SileroVad` + `Segmenter` 處理白噪音、粉紅噪音音軌，三種重置策略都產生 0 句
- [x] 測試：`src/kikitori/` 所有模組都不匯入 torch、pipecat
- 依賴：Task 3、5、6
- 檔案：`tests/test_vad_integration.py`
- 規模：S
- 結果：30 秒「靜音 + 噪音」交錯音軌（白／粉紅 × −30／−15 dBFS × 三種策略，每批 1600 樣本 + flush）全部 0 事件；依賴邊界用 AST 掃描所有原始碼，另開行程匯入全部 `kikitori.*` 模組並建立 `SileroVad`，確認 `sys.modules` 沒有 torch／pipecat；全部 94 個測試通過、ruff 通過。

### Checkpoint 2：模組可用
- [x] 測試與 lint 全部通過
- [ ] 使用者確認（本次為自主執行，由 coordinator 於里程碑結束時一併確認）

## Phase 3：評測工具

### Task 8：評測指標
- [x] `evaluation/metrics.py`：逐格 ROC-AUC（numpy 排名法）、誤報率、漏報率、開始偵測延遲（中位數、P90）、提早切斷次數、漏抓段數
- [x] 測試：每個指標用手算的小例子驗證，含邊界情況（全為人聲、全為非人聲）
- 依賴：無（可與 Phase 2 平行）
- 檔案：`src/kikitori/evaluation/metrics.py`、`tests/test_evaluation.py`
- 規模：S
- 結果：視窗標籤以中心點判定；AUC 用 Mann–Whitney 排名法（同分取平均名次），單一類別回傳 NaN；開始延遲以「送出 `UtteranceStarted` 時的串流位置 − 正確起點」計，與前一段併成一句或漏抓的段不列入；提早切斷「超過 100 ms」為嚴格大於；`tests/test_evaluation.py` 22 個測試通過、ruff 通過。

### Task 9：混音
- [x] `evaluation/mixing.py`：「間隔填入」（只替換句間靜音）與「人聲底下疊加」（SNR 20／10／5 dB），輸出音訊與正確答案（沿用 `ground_truth.json` 格式）
- [x] 測試：間隔填入不改動人聲區間樣本；疊加後實際 SNR 誤差在容許範圍；正確答案位置不變
- 依賴：Task 3
- 檔案：`src/kikitori/evaluation/mixing.py`、`tests/test_evaluation.py`
- 規模：S
- 結果：SNR 的人聲功率只算正確區間內樣本；間隔填入以間隔內的背景 RMS 校正（也用 SNR 表示音量）；疊加若超出 int16 整條等比例縮小、不截斷；正確答案沿用單一音軌格式並加 `mix` 欄位；`segments_from_truth` 驗證外部 JSON（型別、範圍、重疊）；另加 `evaluation/wav.py`（標準庫 `wave` 讀寫並檢查 16 kHz 單聲道 16-bit）；`tests/test_evaluation.py` 32 個測試通過、ruff 通過。

### Task 10：評測選單：產生音軌與執行評測
- [x] `experiments/silero_reset/menu.py`：`input()` 編號選單，選項「產生評測音軌」「執行評測」「離開」，輸入錯誤時重新詢問
- [x] 產生音軌：用本機動漫、VTuber 音軌與噪音產生兩種版本；缺少本機音軌時提示如何產生
- [x] 執行評測：三種重置策略 × 各音軌版本，加上 `context_samples=0` 對照，輸出指標表與推論時間；人聲區間平均機率低於正常設定一半時標記「機率異常偏低」
- [x] `.gitignore` 加入 `experiments/silero_reset/data/`
- [x] 測試：用模擬的 `input()` 驅動選單流程；評測流程以小型合成音軌跑通
- 依賴：Task 7、8、9
- 檔案：`experiments/silero_reset/menu.py`、`experiments/silero_reset/evaluate.py`、`.gitignore`、`tests/test_menu.py`
- 規模：M
- 結果：原版面句間最長只有 1.5 秒，(c) 的 2 秒重置不會發生，因此另把同一批片段重排成「長間隔」版面（句間 3–4 秒）一起評測；`-I` 執行時同資料夾模組改以檔案路徑載入；選單在管線輸出時改用 UTF-8（Windows cp932 無法輸出中文）；本機實跑 36 條音軌 × 4 組設定約 28 秒，每視窗 0.149 ms；`tests/test_menu.py` 8 個測試通過、ruff 通過。

### Task 11：評測選單：下載 MUSAN
- [x] `experiments/silero_reset/musan.py`：顯示網址、大小（約 11.09 GB）、所需空間、授權，確認後才下載；`urllib` 加 timeout；HTTP Range 續傳；進度顯示；只解壓 `music/`、`noise/`；寫入出處說明檔；詢問是否刪除壓縮檔
- [x] 選單加入「下載 MUSAN」；產生音軌時若 MUSAN 已就緒，加產 MUSAN 兩種版本
- [x] 測試（本機假伺服器、小型假壓縮檔）：未確認不下載；續傳；只解壓指定資料夾；timeout 與 HTTP 錯誤
- 依賴：Task 10
- 檔案：`experiments/silero_reset/musan.py`、`experiments/silero_reset/menu.py`、`tests/test_menu.py`
- 規模：M
- 結果：實際大小以 HTTP 標頭確認為 11,086,114,085 bytes；逾時／斷線／5xx 自動續傳重試（預設 3 次），4xx 與大小不符不重試，錯誤訊息含網址與存放路徑；伺服器忽略 Range 時從頭下載；`tarfile` 串流解壓並套用 `data` 過濾器、拒絕含 `..` 的成員；下載前檢查磁碟空間；`tests/test_menu.py` 24 個測試通過；全部 314 個測試通過（含同時進行中的其他模組）。

### Checkpoint 3：評測工具可用
- [x] 測試與 lint 全部通過（本任務檔案；`ruff check` 另有 2 個 I001 來自同時進行中的 capture 模組測試檔，非本里程碑檔案）
- [ ] 使用者確認（本次為自主執行，由 coordinator 於里程碑結束時一併確認）

## Phase 4：評測與文件

### Task 12：執行評測並決定重置策略預設值
- [x] 下載 MUSAN（方式見 `tasks/plan.md` Open Questions）— 經選單確認後下載，伺服器只有 0.01–2 MB/s，約 1 小時完成；只解壓 `music/`、`noise/`（1,606 個檔案、5.3 GB）
- [x] 跑完所有音軌版本；結果寫入 `experiments/silero_reset/README.md`（84 條音軌：原始、白／粉紅噪音、MUSAN 雜音、MUSAN 有人聲音樂、MUSAN 純音樂 × 兩種版面）
- [x] 依結果設定 `ResetPolicy` 預設值，更新程式、測試與規格
- [x] 確認原始動漫、VTuber 音軌上至少一種策略抓到全部 12 段；每視窗平均推論時間 < 1 ms
- 依賴：Task 11
- 檔案：`experiments/silero_reset/README.md`、`src/kikitori/vad/segmenter.py`、`tests/test_vad_segmenter.py`、`docs/specs/vad-segmenter.md`
- 規模：S
- 結果：預設改為 `ResetPolicy.AFTER_LONG_NON_SPEECH`（測試 `test_default_reset_policy_is_after_long_non_speech` 先失敗後通過）；84 條音軌漏抓 (a) 43／(b) 49／(c) 42（共 504 段），開始延遲 P90 (a) 876／(b) 1440／(c) 820 ms；(c) 優勢在長間隔音樂背景、代價在長間隔噪音背景，屬小幅領先；原始動漫、VTuber 三種策略都 12/12；推論 0.18 ms／視窗；前文對照 84/84 判定異常偏低。

### Task 13：架構決策紀錄與架構文件
- [x] 用 `documentation-and-adrs` 寫 ADR：VAD 改用 Silero ONNX、不用 pipecat，以及重置策略的選擇
- [x] `docs/architecture.md` 補上 VAD 切句模組段落
- 依賴：Task 12
- 檔案：`docs/adr/0001-*.md`、`docs/architecture.md`
- 規模：S
- 結果：新增 `docs/adr/0001-vad-silero-onnx-and-reset-policy.md`（背景、決策、替代方案 pipecat／PyTorch／(a)／(b) 與數據、影響與限制）；`docs/architecture.md` 補上 VAD 切句段落（資料流、檔案角色、事件不變式、依賴）。

### Checkpoint 4：完成
- [x] 規格的 Success Criteria 全部達成
- [ ] 使用者 review

---

# 全應用程式里程碑（分支 `feat/full-app`）

> 架構：`docs/specs/app-architecture.md`。每個里程碑測試全過才 commit，完成時在此寫一行結果。

- [x] 里程碑 1：VAD 切句（上方 Task 1–13）
  - 結果：Silero v6.2.3 ONNX 打包進套件；預設重置策略 `AFTER_LONG_NON_SPEECH`；原始動漫／VTuber 12/12、純噪音 0 句、0.20 ms／視窗；MUSAN 只下載到 music，雜音版本待補。
  - 補充：MUSAN 之後下載完成，已用選單補跑全部 84 條音軌（含 MUSAN 雜音）；預設仍為 (c)，但領先幅度變小（漏抓 42/504 vs (a) 43/504，長間隔噪音背景 (a) 較好），README、規格、ADR 已更新。
- [x] 里程碑 2：語音辨識
  - 結果：CTranslate2（MIT）＋ Whisper 權重（MIT），不用 faster-whisper（PyAV 內附 GPL 編碼器）；RTX 5070 CUDA fp16 可用（需 cuBLAS 12），large-v3-turbo 單句 0.15 s；CPU 只有 tiny／base 跟得上即時字幕；turbo 的 no_speech 恆為 0，改靠幻覺清單與 VAD。
- [x] 里程碑 3：翻譯
  - 結果：OpenAI 相容客戶端（標準庫）、預設本機 Ollama；qwen3.5:9b 總延遲中位數 0.23 s、輸出 0/33 簡體；HY-MT 有地區限制、Sakura 非商用，皆不預設推薦。
- [x] 里程碑 4：協調器（雙路徑）與引擎協定伺服器
  - 結果：pipeline（即時最新槽＋完整有界 FIFO、依序送出）、config（嚴格驗證、原子寫入）、stdio NDJSON 引擎（全部方法與事件有子行程測試）、`kikitori` 互動選單；本機 CPU 實測即時字幕延遲中位數 0.85 s、完整字幕 2.85 s（anime.wav，base／small＋qwen3.5:4b）。
- [x] 里程碑 5：聲音擷取
  - 結果：Windows（PyAudioWPatch）loopback 與麥克風本機實測可錄；Linux（soundcard）、macOS（ScreenCaptureKit）只以假物件測試，待真機驗證。
- [x] 里程碑 6：硬體偵測與模型組合
  - 結果：hardware 模組（標準庫偵測、推薦、手動組合驗證、使用者同意 NVIDIA EULA 後下載 cuBLAS）已接上引擎（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`models.download gpu-runtime`）；設定 schema v2；本機推薦 small／large-v3-turbo CUDA float16 + qwen3.5:9b。
- [ ] 里程碑 7：Tauri 介面
  - 第一階段結果：Tauri v2 + Vite + TypeScript；主視窗（模式、裝置、字幕紀錄、模型頁、設定頁）與透明置頂字幕視窗（即時→完整原地取代、滑鼠穿透、Ctrl+Alt+K）；Rust 引擎橋接（逐行驗證、id 配對與逾時、自動重啟）；vitest 60、cargo test 44、clippy、debug build 通過；更新器公鑰已寫入。待做：sidecar 打包整合、Linux／macOS 實機確認。
- [ ] 里程碑 8：CI/CD
- [ ] 里程碑 9：自動更新
