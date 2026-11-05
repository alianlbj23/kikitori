# kikitori 架構總覽

## 專案目標

kikitori 即時擷取電腦正在播放的聲音，把日文或英文語音翻譯成繁體中文。
畫面上同時顯示原文與譯文的雙語字幕，幫助語言學習者邊聽邊對照。
支援 Windows、Linux、macOS。

## 處理流程

```text
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 音訊擷取 │ → │ VAD 切句 │ → │ 語音辨識 │ → │   翻譯   │ → │ 字幕顯示 │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
 系統播放的      偵測人聲，       日文/英文       翻譯成         原文與譯文
 聲音            切成一句一句     轉成文字        繁體中文       雙語字幕
```

兩種模式：**影片翻譯**（`media`，擷取系統播放的聲音，ja／en → zh-TW）與**即時口譯**（`interpret`，電腦麥克風，zh-TW ↔ ja 或 en）。

程式分成兩個行程：Tauri 桌面程式（`app/`，Rust + TypeScript）負責畫面、字幕視窗與自動更新；Python 引擎（`src/kikitori/`）負責上面整條聲音處理流程。兩者透過引擎的 stdin／stdout 交換一行一個 JSON（NDJSON），不開網路埠。完整契約見 `docs/specs/app-architecture.md`；給人讀的導覽見 `docs/learning/`。

## 模組說明

每個模組一段：做什麼、規格在哪。細節以各規格為準。

### VAD 切句（`kikitori.vad`）

規格：`docs/specs/vad-segmenter.md`；決策：`docs/adr/0001-vad-silero-onnx-and-reset-policy.md`。

把 16 kHz、單聲道、int16 的連續音訊切成一句一句。API 為同步呼叫：`Segmenter.process(audio)` 餵入任意長度的音訊，回傳這段音訊造成的事件；串流結束時呼叫 `flush()`。時間一律用樣本計數（以串流開頭為 0），同一段音訊不論分幾批、以多快的速度餵入，事件與位置都相同。

```text
int16 音訊（任意長度）
   │
   ▼
Segmenter ── 累積成 512 樣本（32 ms）視窗 ──► VoiceModel（預設 SileroVad）
   │                                            └─ 每視窗回傳人聲機率
   ├─ 遲滯門檻：≥ 0.5 持續 250 ms 才開始（起點往前含 200 ms）；< 0.35 才算靜音
   ├─ EndpointPolicy（預設 PauseEndpoint）：靜音 0.6 秒或句長 10 秒就結束
   ├─ request_end()：外部要求在下一個視窗結束（之後接標點模型、Smart Turn）
   └─ ResetPolicy（預設 AFTER_LONG_NON_SPEECH）：何時重置模型狀態
   │
   ▼
事件：UtteranceStarted → UtteranceAudio（每 32 ms）… → UtteranceEnded（pause / max_length / external / flush）
```

| 檔案 | 角色 |
|---|---|
| `segmenter.py` | `Segmenter`、`ResetPolicy`、三種事件 |
| `endpoint.py` | `EndpointPolicy` 介面與 `PauseEndpoint` |
| `model.py` | `VoiceModel` 介面：「512 樣本 → 機率」與 `reset()`，模型可替換 |
| `silero.py` | `SileroVad`：用 onnxruntime 跑 Silero VAD v6.2.3，前接 64 樣本前文、state `(2, 1, 128)` 跨呼叫保存，載入時驗證 SHA-256 |
| `models/` | 打包的 `silero_vad.onnx`、MIT 授權全文、版本與雜湊說明 |

- 同一句的 `UtteranceAudio` 首尾相接，合起來剛好是 `[UtteranceStarted.start_sample, UtteranceEnded.end_sample)`，語音辨識可以邊收邊處理，也可以在句子結束後一次取用。
- 依賴只有 `onnxruntime` 與 `numpy`；不使用 PyTorch、pipecat（以測試檢查）。每個視窗推論約 0.2 ms。
- 評測工具（噪音產生、混音、指標）在 `kikitori.evaluation`，評測選單與結果在 `experiments/silero_reset/`，正式執行時不會載入。

### 音訊擷取（`kikitori.capture`）

規格：`docs/specs/capture.md`。把系統播放的聲音（Windows WASAPI loopback／Linux PulseAudio monitor／macOS ScreenCaptureKit）或麥克風轉成 16 kHz 單聲道 int16，交給 VAD。重採樣用 numpy 多相 FIR，區塊邊界無爆音；擷取執行緒＋有界緩衝，滿了丟最舊的資料並計數。平台套件延遲匯入，任何平台都能 `import`。

### 語音辨識（`kikitori.asr`）

規格：`docs/specs/asr.md`。CTranslate2 跑 Whisper 權重（皆 MIT），不依賴 PyTorch；不用 faster-whisper，因其必要依賴 PyAV 的 Windows wheel 內附 GPL 編碼器。log-mel 特徵、分詞器、下載器（續傳、逐檔 SHA-256）自行實作。模型第一次使用時下載到使用者快取。GPU 需要 cuBLAS 12，由使用者同意 NVIDIA EULA 後下載（見 hardware）。

### 翻譯（`kikitori.translate`）

規格：`docs/specs/translate.md`。OpenAI 相容 Chat Completions 客戶端（標準庫），預設本機 Ollama；支援串流、重試、timeout，API key 只從環境變數讀。每個模型有自己的提示範本；只在偵測到簡體專用字時才用 OpenCC 轉台灣正體。

### 協調器（`kikitori.pipeline`）

規格：`docs/specs/pipeline-engine.md`。雙路徑：句子進行中定期用較快的模型產生**即時字幕**（只保留最新工作，落後就跳過），句子結束後用較準的模型產生**完整翻譯**取代它（一句都不丟、依序送出）。過濾 Whisper 的幻覺與非語音；依模式決定翻譯方向。

### 設定（`kikitori.config`）

規格：`docs/specs/pipeline-engine.md`。使用者設定目錄下的 `settings.json`，嚴格驗證、`schema_version` 遷移（目前 v2）、原子寫入；不存密碼與 API key。

### 硬體偵測與模型組合（`kikitori.hardware`）

規格：`docs/specs/hardware.md`。偵測 CPU、RAM、NVIDIA GPU、cuBLAS、Ollama 與已安裝模型，推薦即時／完整 ASR 與翻譯模型的組合，並驗證使用者手動選的組合。GPU 執行環境（cuBLAS）由使用者觸發、從 PyPI 下載 NVIDIA 官方 wheel，不隨安裝檔散布。

### 引擎（`kikitori.engine`）

規格：`docs/specs/pipeline-engine.md`。stdio NDJSON 協定伺服器，Tauri 的唯一入口（`kikitori engine`）；stdout 只寫協定訊息，日誌走 stderr 與 `log` 事件。耗時方法在背景執行緒處理。`KIKITORI_ENGINE_FAKE=1` 為假模式（合成音訊、假模型），供介面開發與測試。直接執行 `kikitori` 會出現互動選單。

### 桌面程式（`app/`）

規格：`docs/specs/ui.md`。Tauri v2 + Vite + TypeScript。主視窗（模式、聲音來源、字幕紀錄、模型頁、設定頁）與透明置頂的雙語字幕視窗；Rust 端的引擎橋接逐行驗證引擎輸出、依 id 配對回應、引擎意外結束時自動重啟。

### 打包、CI 與發布

規格：`docs/specs/packaging-ci.md`、`docs/specs/release-update.md`。引擎用 PyInstaller 打包成資料夾，作為 Tauri resources 一起散布；版本號以 `pyproject.toml` 為單一來源（`scripts/sync_version.py`）。GitHub Actions 在三平台跑測試；推到 main 且版本號變更時打包並發布 release，桌面程式透過 Tauri updater 自動更新。
