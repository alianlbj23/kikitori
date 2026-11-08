# 01 系統總覽

## 一句話

kikitori 聽電腦的聲音（或麥克風），即時辨識並翻成使用者選的語言，用浮在影片上的雙語字幕顯示。
主要情境是日語 VTuber 直播 → 繁體中文；收聽與字幕語言可以是 20 種語言的任意組合，也可以關掉翻譯、只看原文。
它是桌面程式，目標平台是 Windows、Linux、macOS。

## 兩種模式

| 模式 | 程式裡的 id | 聲音來源 | 語言（設定欄位） | 一句話何時算結束 |
|---|---|---|---|---|
| 影片翻譯 | `media` | 電腦播放的聲音（loopback） | 收聽語言 `source_language`（預設 `ja`，可選 `auto` 自動偵測）→ 字幕語言 `target_language`（預設 `zh-TW`） | 靜音 0.6 秒，或句長 10 秒 |
| 即時口譯 | `interpret` | 麥克風 | 你說「我的語言」（`target_language`）→ 譯成對方的語言（`partner_language`）；對方說話 → 譯成我的語言 | 靜音 0.5 秒，或句長 8 秒 |

語言清單 20 種（`src/kikitori/languages/catalog.py`），任意組合；語言怎麼決定、怎麼路由見 [07-languages-and-i18n.md](07-languages-and-i18n.md)。
另有「顯示翻譯」開關（設定 `translation.enabled`）：關掉時引擎完全不呼叫翻譯伺服器，只顯示原文。

預設值在 `src/kikitori/config/settings.py`（`Settings` 與 `_default_media()`、`_default_interpret()`），設定檔格式是 schema 3（`SCHEMA_VERSION`），使用者可以在設定頁或設定檔改。

## 大圖：兩個行程

程式由兩個獨立的「行程」（process，作業系統裡各自執行的程式）組成：

```text
┌──────────────── kikitori 桌面程式（app/）────────────────┐
│  Rust 外殼（Tauri）            網頁畫面（TypeScript）     │
│  - 啟動、看管引擎               - 主視窗：模式、設定、模型 │
│  - 驗證引擎送來的每一行         - 字幕視窗：透明、置頂     │
│  - 轉發事件給畫面                                         │
└──────────────┬───────────────────────────────────────────┘
               │  stdin：請求（一行一個 JSON）
               │  stdout：回應與事件（一行一個 JSON）
               ▼
┌──────────────── kikitori-engine（Python，src/kikitori/）──┐
│  擷取聲音 → 切句 → 辨識 → 翻譯 → 送出字幕事件               │
│  另外：硬體偵測、模型下載、設定檔                          │
└──────────────┬───────────────────────────────────────────┘
               │  HTTP（OpenAI 相容 API）
               ▼
        Ollama（使用者自己安裝的翻譯伺服器，預設本機 11434 埠）
```

- **為什麼分成兩個行程**：語音處理的工具都在 Python；畫面、視窗、自動更新交給 Tauri。
- **sidecar**：Python 引擎就是 Tauri 旁邊的「邊車行程」。Tauri 啟動它、看管它，它當掉時自動重開（1、2、4 秒後重試，60 秒內超過 3 次就停下來等使用者）。
- **NDJSON over stdio**：兩邊不開網路埠，只透過標準輸入輸出交換「每行一個 JSON」。引擎的 stdout 只寫協定訊息，日誌一律寫 stderr。
- **開發時**引擎用 `uv run kikitori engine` 啟動；**正式版**引擎是 PyInstaller 打包好的資料夾，放在安裝目錄的 `kikitori-engine/` 裡（`app/src-tauri/src/engine.rs` 的 `resolve_engine_command()`）。

### 協定長什麼樣子

以下是真的跑一次假模式引擎得到的輸出（節錄，2026-11-06 於本機執行；假翻譯只是在原文前加 `[zh-TW]`）：

```text
→ {"id":3,"method":"session.start","params":{"mode":"media"}}
← {"id":3,"result":{"session_id":1}}
← {"event":"status","data":{"state":"loading",...,"session_id":1}}
← {"event":"status","data":{"state":"running",...,"session_id":1}}
← {"event":"subtitle","data":{"utterance_id":1,"original":"partial1","translation":"[zh-TW] partial1",
     "source_language":"ja","target_language":"zh-TW","is_final":false,
     "start_sample":4480,"end_sample":15872,"stable_chars":0,"session_id":1}}
← {"event":"subtitle","data":{"utterance_id":1,"original":"partial2","translation":"[zh-TW] partial2",
     ...,"is_final":false,"start_sample":4480,"end_sample":27136,"stable_chars":7,"session_id":1}}
← {"event":"subtitle","data":{"utterance_id":1,"original":"final1","translation":"[zh-TW] final1",
     ...,"is_final":true,"start_sample":4480,"end_sample":35584,"latency_ms":0,"session_id":1}}
← {"event":"status","data":{"state":"idle",...}}
```

即時字幕帶 `stable_chars`（原文開頭幾個字已穩定），完整字幕帶 `latency_ms`（假模式沒有真的計算，所以是 0），見 [10](10-live-subtitle-stability-and-latency.md)。

三種訊息：

| 種類 | 形狀 | 方向 |
|---|---|---|
| 請求 | `{"id": 數字, "method": "名稱", "params": {...}}` | app → 引擎 |
| 回應 | `{"id": 同一個數字, "result": {...}}` 或 `{"id":…, "error": {"code", "message"}}` | 引擎 → app |
| 事件 | `{"event": "名稱", "data": {...}}`（沒有 id） | 引擎 → app，隨時都可能送 |

方法共 18 個，定義在 `src/kikitori/engine/server.py` 的 `EngineServer.__init__` 裡的 `_handlers`：

| 類別 | 方法 |
|---|---|
| 引擎 | `engine.info`、`engine.shutdown` |
| 硬體與模型 | `hardware.detect`、`models.list`、`models.download`、`models.validate`、`models.pull_translation`（替使用者在 Ollama 下載翻譯模型）、`models.delete`（刪除已下載的辨識模型） |
| 語言 | `languages.list`（20 種語言的清單，UI 不自己維護一份） |
| 裝置 | `devices.list` |
| 設定 | `settings.get`、`settings.set`、`settings.apply_recommendation`、`settings.preview_recommendation`、`settings.preview_languages`（選語言時預覽推薦組合與下載大小，不存檔、不下載） |
| 字幕 | `session.start`、`session.stop`、`session.set_translation`（執行中切換「顯示翻譯」） |

事件有 `subtitle`、`status`、`download_progress`、`log`、`metrics`（執行中每秒一則：音量、落後句數、延遲中位數）五種。協定版本（`protocol_version`）一直是 1：v0.2.0 的變更大多是新增欄位、方法與事件，唯一改變形狀的是推薦說明與檢查結果從中文字串改成 `{code, params, message}` 訊息物件（讓 UI 依介面語系組字）。完整欄位見 `docs/specs/pipeline-engine.md`（各次變更列在「協定變更」各節）。

## 資料流全圖：從音效卡到字幕視窗

```text
音效卡（例如 48 kHz 立體聲）
  │  capture：降成單聲道、重採樣到 16 kHz、轉 int16
  │           Windows WASAPI／Linux PulseAudio／macOS ScreenCaptureKit
  ▼
ChunkBuffer（最多存 5 秒，滿了丟最舊的）
  │  pipeline 的 capture 執行緒每次 read(0.1 秒)
  ▼
Segmenter（VAD 切句，每 512 樣本＝32 ms 問 Silero 一次「有人聲嗎？」）
  │  事件：UtteranceStarted → UtteranceAudio …（每 32 ms）→ UtteranceEnded
  │
  ├── 句子進行中，每多 0.7 秒音訊 ──► 最新槽（只留最新一個工作）
  │                                      │ 即時 worker 執行緒
  │                                      ▼
  │                         較快的模型辨識（例如 large-v3-turbo 或 base，greedy）→ 過濾幻覺
  │                         → 算穩定前綴（stable_chars）→ 依語言路由
  │                         → 穩定部分變長才重送串流翻譯 → subtitle（is_final=false）
  │
  └── 句子結束 ──► 完整佇列（最多 64 句，不丟）
                       │ 完整 worker 執行緒
                       ▼
          較準的模型辨識（例如 large-v3 或 small，beam 5，帶前文）→ 過濾幻覺
          → 依語言路由 → 帶前文與術語翻譯 → subtitle（is_final=true，帶 latency_ms，取代同一句的草稿）
  ▼
engine：把 SubtitleUpdate 加上 session_id，寫成一行 JSON 到 stdout（另外每秒一則 metrics）
  ▼
Rust：逐行讀（每行上限 1 MiB）→ 驗證欄位 → 發出 Tauri 事件 engine://subtitle（metrics 走 engine://metrics，只給主視窗）
  │                                          └─► OBS 文字檔（obs.rs，開啟時）
  ▼
TypeScript：再驗證一次 → applySubtitle() 更新狀態
  ├─► 主視窗「字幕紀錄」頁（畫面最多 300 句；另存整個 session 供匯出）
  └─► 字幕視窗（最新 1–3 句；不穩定的尾巴較淡，定稿轉成字幕黃）
```

括號裡的模型是例子：實際用哪個依硬體推薦（12 GB 顯示卡是 large-v3-turbo／large-v3；沒有 GPU 時是 base／small），見 `docs/specs/hardware.md`。

## 每個模組一句話

Python 引擎（`src/kikitori/`）：

| 模組 | 一句話 |
|---|---|
| `capture/` | 從音效卡或麥克風抓聲音，統一成 16 kHz、單聲道、int16。 |
| `vad/` | 用 Silero VAD 判斷哪裡有人聲，把連續聲音切成一句一句。 |
| `languages/` | 20 種語言的清單：語言碼、名稱、Whisper 語言碼、字形轉換、各模型的辨識品質等級。唯一的語言資料來源。 |
| `asr/` | 用 CTranslate2 跑 Whisper，把一句聲音轉成文字，並依語言過濾幻覺；模型物件放在模型池裡重複使用。 |
| `translate/` | 呼叫 OpenAI 相容 API（預設本機 Ollama）把一句話翻成字幕；術語表、前文保險、繁簡轉換。 |
| `pipeline/` | 把上面串起來，跑「即時」與「完整」兩條路徑；語言路由、穩定前綴、量測。 |
| `config/` | 讀寫、驗證使用者設定檔 `settings.json`（schema 3）。 |
| `hardware/` | 偵測 CPU、記憶體、NVIDIA 顯示卡、Ollama，依硬體與語言推薦模型組合；下載 cuBLAS、替使用者在 Ollama 下載翻譯模型。 |
| `engine/` | stdio NDJSON 伺服器，Tauri 唯一的入口。 |
| `evaluation/` | 評測工具（產生噪音、混音、算指標），正式執行時不載入。 |
| `cli.py` | `kikitori` 指令：不帶參數是互動選單，`kikitori engine` 啟動引擎。 |

桌面程式（`app/`）：

| 部分 | 一句話 |
|---|---|
| `app/src-tauri/src/`（Rust） | 啟動引擎、配對請求與回應、驗證並轉發事件、控制字幕視窗、全域快速鍵、自動更新；寫字幕紀錄檔（`output.rs`）與 OBS 文字檔（`obs.rs`）。 |
| `app/src/`（TypeScript） | 主視窗與字幕視窗的畫面；字幕狀態規則在 `lib/subtitles.ts`，其他可測的規則也在 `lib/`（穩定前綴、閱讀節奏、匯出、術語表、量測）。 |
| `app/src/lib/i18n.ts`、`app/src/locales/` | 介面多語系：13 個語系字典，zh-TW 為基準，型別保證每個語系的鍵齊全（見 07）。 |

其他：

| 部分 | 一句話 |
|---|---|
| `packaging/engine/` | 用 PyInstaller 把引擎打包成資料夾，並產生第三方授權目錄。 |
| `packaging/app/` | 發布建置時產生 app 本身（Rust crate、前端 npm 套件、kikitori 的 LICENSE）的第三方授權目錄。 |
| `scripts/sync_version.py` | 版本號只在 `pyproject.toml` 改一次，同步到 app 的四個檔案。 |
| `.github/workflows/test.yml` | 三平台跑 Python 測試、lint、版本檢查、引擎打包與冒煙測試。 |
| `.github/workflows/app.yml` | 三平台跑前端型別檢查、測試、建置，Rust 的 fmt、clippy、測試；另檢查 Rust 依賴的授權。 |
| `.github/workflows/release.yml`、`scripts/release.py` | push 到 main 且版本號還沒有 tag 時，三平台打包、簽章、產生 `latest.json` 並建立 GitHub Release（設計見 `docs/specs/release-update.md`）。 |
| `experiments/` | 一次性的實驗與基準測試，結果寫在各自的 README。不屬於正式程式。 |

## 執行時有哪些執行緒

知道這個，看程式時比較不會迷路。

| 行程 | 執行緒 | 做什麼 |
|---|---|---|
| 引擎 | 主執行緒 | 讀 stdin、處理快速的請求 |
| 引擎 | 背景工作執行緒（`engine/deferred.py`） | 處理慢的請求：`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`settings.preview_recommendation`、`settings.preview_languages`、`models.pull_translation` |
| 引擎 | session 載入執行緒 | 檢查翻譯模型、載入模型、預熱翻譯，然後啟動 pipeline |
| 引擎 | pipeline 的 3 條：capture、即時 worker、完整 worker | 切句、兩條辨識翻譯路徑 |
| 引擎 | 保溫執行緒（`pipeline/keep_warm.py`，只對本機翻譯服務） | 翻譯閒置太久時送一次短請求，讓 Ollama 不卸載模型 |
| 引擎 | `session-<id>-metrics` | 每秒送一則 `metrics` |
| 引擎 | `session-<id>-release` | session 結束時把模型卸載、放回模型池（最多等 5 秒，見 09） |
| 引擎 | 擷取執行緒（WASAPI／soundcard）或系統回呼（ScreenCaptureKit） | 從裝置讀聲音 |
| 引擎 | 下載執行緒 | 辨識模型、GPU 元件、Ollama 翻譯模型的下載 |
| app | OBS 寫檔執行緒（`obs.rs`） | 開啟 OBS 輸出時，每秒最多寫 10 次字幕文字檔 |
| app | Rust 非同步工作 | 讀引擎 stdout／stderr、等待回應、自動重啟 |

## 外部需要什麼

| 東西 | 誰提供 | 沒有會怎樣 |
|---|---|---|
| Whisper 模型 | 第一次按「開始」時自動下載缺少的模型（也可在模型頁下載），從 Hugging Face 下載到使用者快取資料夾（固定版本、驗 SHA-256） | 引擎回 `status error` 的 `model_not_downloaded`，UI 下載完自動重新開始 |
| 翻譯 LLM | 使用者自己裝 Ollama；模型可由 kikitori 代為下載（`models.pull_translation`）。關閉「顯示翻譯」時完全不需要 | 連不上：字幕只有原文、譯文空白，送一次 warning，UI 提示「安裝 Ollama」或「只顯示原文」；Ollama 開著但缺模型：`translation_model_missing`，UI 下載後自動重新開始 |
| cuBLAS（GPU 才需要） | app 內勾選同意 NVIDIA EULA 後下載（約 570 MB） | 自動改用 CPU，比較慢 |
| Silero VAD 模型 | 已包在程式裡（`silero_vad.onnx` 約 2.3 MB，MIT） | — |

## 目前完成到哪裡

依 `tasks/todo.md` 與 git 紀錄：

- **v0.1.0**（`main`，`9f8b976`）：里程碑 1–9。引擎、介面、三平台打包、測試與發布 CI、自動更新都已完成並驗收（`tasks/acceptance-report.md`）。
- **v0.2.0**（`feat/vtuber-models`）：里程碑 10–16。多國語言、介面多語系、只顯示原文、延後下載、VTuber 特化的模型與術語表、競品比較後補的功能（匯出、OBS 文字檔、穩定字幕、量測、模型刪除），以及記憶體釋放的修正。
- **還沒有**：里程碑 17 的端到端驗收（新推薦組合在 12 GB 卡上的顯示記憶體與延遲、v0.2.0 新介面流程的實機操作）、macOS 實機驗證、Linux 實體機（只驗收過 Docker 容器）。細節見 [06-limitations.md](06-limitations.md)。
