# kikitori 架構總覽

## 專案目標

kikitori 即時擷取電腦正在播放的聲音（或麥克風），辨識成文字、翻譯成使用者選的語言，畫面上同時顯示原文與譯文的雙語字幕。
主要情境是日語 VTuber 直播 → 繁體中文；收聽與字幕語言可以是 20 種語言的任意組合（v0.2.0 起），也可以只顯示原文。
支援 Windows、Linux、macOS；語音辨識與翻譯都在本機執行。

## 處理流程

```text
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 音訊擷取 │ → │ VAD 切句 │ → │ 語音辨識 │ → │   翻譯   │ → │ 字幕顯示 │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
 系統播放的      偵測人聲，       收聽語言        依語言路由     原文與譯文
 聲音或麥克風    切成一句一句     轉成文字        翻成字幕語言   雙語字幕（＋匯出、OBS 文字檔）
```

兩種模式：**影片翻譯**（`media`，擷取系統播放的聲音，收聽語言（或自動偵測）→ 字幕語言）與**即時口譯**（`interpret`，麥克風，我的語言 ↔ 對方的語言）。語言清單 20 種，見下方「語言清單」。

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

### 語言清單（`kikitori.languages`）

規格：`docs/specs/languages-i18n.md`。`languages/catalog.py` 是**唯一**的語言資料來源：20 種語言的清單語言碼（`zh-TW`、`zh-CN`、`ja`…）、英文名與原生名、Whisper 語言碼（繁簡中文都是 `zh`）、是否不用空白、字形轉換（zh-TW `s2twp`、zh-CN `t2s`）、由右到左，以及每個 ASR 模型對每個語言的品質等級（A ≤ 10%、B 10–20%、C > 20% 錯誤率）。設定驗證、翻譯路由、提示詞、推薦與 UI 的語言選單（經 `languages.list`）都從這裡取；不分繁簡的 `zh` 不是合法的字幕語言。

### 語音辨識（`kikitori.asr`）

規格：`docs/specs/asr.md`。CTranslate2 跑 Whisper 權重（皆 MIT），不依賴 PyTorch；不用 faster-whisper，因其必要依賴 PyAV 的 Windows wheel 內附 GPL 編碼器。log-mel 特徵、分詞器、下載器（續傳、逐檔 SHA-256）自行實作。模型第一次使用時下載到使用者快取；`model_disk_bytes()`／`delete_model()` 回報磁碟用量並安全刪除（只刪模型快取內、不跟隨連結）。GPU 需要 cuBLAS 12，由使用者同意 NVIDIA EULA 後下載（見 hardware）。

- **模型物件池**（`whisper.py` 的 `MODEL_POOL`）：CTranslate2 每建立一個 Whisper 物件並在 GPU 上推論過，銷毀後仍留下約 8 MiB 顯示記憶體（上游行為）。所以物件不銷毀，而是 `unload_model()` 後放回池中、下次同參數時 `load_model()` 取回；最多保留 4 個閒置物件（LRU）。即時與完整用同一模型、同精度時只載入一份權重（`inter_threads=2`），由借用計數決定何時歸還。
- **幻覺過濾**（`filtering.py`）：no_speech／logprob／壓縮比規則，加上 19 種 Whisper 語言各自的幻覺片語表（`hallucination_lexicon.py`，由 `experiments/hallucination_lexicon/lexicon.json` 產生）：`hard` 片語不論辨識語言都濾，`soft` 片語只在同語言且 no_speech 偏高時濾。

### 翻譯（`kikitori.translate`）

規格：`docs/specs/translate.md`。OpenAI 相容 Chat Completions 客戶端（標準庫），預設本機 Ollama；支援串流、重試、timeout，API key 只從環境變數讀。每個模型有自己的提示範本與支援的語言（`presets.py`；Qwen3.5、HY-MT 溫度 0）。

- **術語表**（`glossary.py`）：內建 53 個日語直播用語（譯文 zh-TW／zh-CN／en／ko）加上使用者詞條；只把這句原文實際出現的詞（NFKC＋casefold 比對、同位置取最長、最多 8 條）放進提示詞。
- **前文保險**（`context_guard.py` 的 `ContextFallbackTranslator`）：帶前文的譯文過長或抄出前文原句時，不帶前文重翻一次；factory 包在兩條路徑的翻譯器外面。
- **後處理**（`postprocess.py`）：去掉思考內容與特殊 token 碎片；目標 zh-TW 時只轉換含簡體專用字的片段，目標 zh-CN 時整句 `t2s`。

### 協調器（`kikitori.pipeline`）

規格：`docs/specs/pipeline-engine.md`。雙路徑：句子進行中定期用較快的模型產生**即時字幕**（只保留最新工作，落後就跳過），句子結束後用較準的模型產生**完整翻譯**取代它（一句都不丟、依序送出）。過濾 Whisper 的幻覺與非語音。

- **語言路由**（`languages.py` 的 `route()`）：依模式與辨識出的語言決定譯文語言與動作——`translate`、`copy`（同一個 Whisper 語言，只轉字形）、`unsupported`、`off`（只顯示原文）。
- **穩定前綴**（`stability.py`）：同一句連續兩則即時結果的最長共同前綴（LocalAgreement-2）放在 `stable_chars`；即時翻譯只在穩定部分變長時重送。
- **量測**（`metrics.py`）：最近約 1 秒擷取音訊的 RMS dBFS、完整路徑佇列的待處理句數、最近 20 句完整字幕延遲的中位數；引擎每秒以 `metrics` 事件送出。
- 元件的生命週期由引擎負責：session 結束時 `release_components()` 關閉 transcriber（模型回到模型池），然後才送出結束狀態。

### 設定（`kikitori.config`）

規格：`docs/specs/pipeline-engine.md`。使用者設定目錄下的 `settings.json`，嚴格驗證、`schema_version` 遷移（目前 v3：`source_language`、`translation.enabled`；v2 自動遷移）、原子寫入；不存密碼與 API key。`translation.glossary`、`translation.keep_warm_secs`、`ui` 是有預設值的新增欄位，不升 schema。`subtitle` 與 `ui`（介面語系、自動存檔、「不要再提醒」）引擎不解讀、原樣保存給 UI。

### 硬體偵測與模型組合（`kikitori.hardware`）

規格：`docs/specs/hardware.md`。偵測 CPU、RAM、NVIDIA GPU、cuBLAS、Ollama 與已安裝模型，依硬體**與語言**推薦即時／完整 ASR 與翻譯模型的組合，並驗證使用者手動選的組合。8 GB 以上的顯示卡從依品質分組的候選（`GPU_LADDERS`）中，挑能讓本機翻譯模型等級最高、精度又最高的組合（Milestone 16，依據 `experiments/vtuber_eval/`）。推薦說明、警告與驗證錯誤都是 `{code, params, message}` 訊息（`messages.py` 的 `CATALOG`），UI 依介面語系組字。GPU 執行環境（cuBLAS）由使用者觸發、從 PyPI 下載 NVIDIA 官方 wheel，不隨安裝檔散布；Ollama 的翻譯模型可由引擎代為 `POST /api/pull`（`ollama.py`）。

### 引擎（`kikitori.engine`）

規格：`docs/specs/pipeline-engine.md`。stdio NDJSON 協定伺服器，Tauri 的唯一入口（`kikitori engine`）；stdout 只寫協定訊息，日誌走 stderr 與 `log` 事件。耗時方法在背景執行緒處理。v0.2.0 新增的方法：`languages.list`、`settings.preview_languages`、`models.pull_translation`、`models.delete`、`session.set_translation`；新事件 `metrics`；`subtitle` 新增 `stable_chars`（即時）與 `latency_ms`（完整）；`protocol_version` 仍為 1（各次變更清單見規格「協定變更」）。`KIKITORI_ENGINE_FAKE=1` 為假模式（合成音訊、假模型），供介面開發與測試。直接執行 `kikitori` 會出現互動選單。

### 桌面程式（`app/`）

規格：`docs/specs/ui.md`。Tauri v2 + Vite + TypeScript。主視窗（模式、聲音來源、字幕紀錄、模型頁、設定頁）與透明置頂的雙語字幕視窗；Rust 端的引擎橋接逐行驗證引擎輸出、依 id 配對回應、引擎意外結束時自動重啟。

- **介面多語系**（`app/src/lib/i18n.ts`、`app/src/locales/`）：13 個語系字典，不用 i18n 函式庫。zh-TW 字典是基準，它的鍵定義 `MessageKey`，其他語系以 `Messages` 型別宣告，缺鍵或多鍵就是型別錯誤；測試另檢查佔位符與複數類別一致、`app/src` 沒有寫死的中日韓文字、每個引擎訊息碼都有 `engine.<code>` 字典項目。
- **字幕邏輯**（純函式、可測）：`subtitles.ts`（字幕狀態規則）、`stable.ts`（穩定／不穩定切分，不切在字素叢集中間）、`rhythm.ts`（每句最少顯示時間、閒置淡出）、`metrics.ts`（音量表、延遲、落後與沒有聲音的提醒）、`export.ts`（SRT／VTT／TXT／CSV）、`glossary.ts`（術語表檢查與 CSV）。
- **輸出**（Rust）：`output.rs` 只把前端產生的內容寫到 `<文件>/kikitori/transcripts/`（檔名白名單、原子寫入）；`obs.rs` 在專用執行緒把目前字幕寫到 `<文件>/kikitori/obs/subtitle.txt`（每秒最多 10 次、原子寫入；網頁視窗被隱藏時計時器會被節流，所以放在 Rust）。
- **全域快速鍵**：Ctrl+Alt+K（滑鼠穿透）、Ctrl+Alt+J（顯示翻譯）、Ctrl+Alt+U（清除字幕）。

### 打包、CI 與發布

規格：`docs/specs/packaging-ci.md`、`docs/specs/release-update.md`。引擎用 PyInstaller 打包成資料夾，作為 Tauri resources 一起散布；版本號以 `pyproject.toml` 為單一來源（`scripts/sync_version.py`）。GitHub Actions 在三平台跑測試；推到 main 且版本號變更時打包並發布 release，桌面程式透過 Tauri updater 自動更新。
