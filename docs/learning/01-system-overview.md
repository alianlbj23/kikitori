# 01 系統總覽

## 一句話

kikitori 聽電腦的聲音，把日文或英文即時翻成繁體中文，用浮在影片上的雙語字幕顯示。
它是桌面程式，目標平台是 Windows、Linux、macOS。

## 兩種模式

| 模式 | 程式裡的 id | 聲音來源 | 語言 | 一句話何時算結束 |
|---|---|---|---|---|
| 影片翻譯 | `media` | 電腦播放的聲音（loopback） | 日文／英文 → 繁體中文 | 靜音 0.6 秒，或句長 10 秒 |
| 即時口譯 | `interpret` | 麥克風 | 你說中文 → 譯成對方語言（日或英）；對方說日／英 → 譯成繁體中文 | 靜音 0.5 秒，或句長 8 秒 |

預設值在 `src/kikitori/config/settings.py`（`_default_media()`、`_default_interpret()`），使用者可以在設定檔改。

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

以下是真的跑一次假模式引擎得到的輸出（節錄，2026-11-05 於本機執行）：

```text
→ {"id":3,"method":"session.start","params":{"mode":"media"}}
← {"id":3,"result":{"session_id":1}}
← {"event":"status","data":{"state":"loading",...,"session_id":1}}
← {"event":"status","data":{"state":"running",...,"session_id":1}}
← {"event":"subtitle","data":{"utterance_id":1,"original":"partial1","translation":"[zh-TW] partial1",
     "is_final":false,"start_sample":4480,"end_sample":15872,...}}
← {"event":"subtitle","data":{"utterance_id":1,"original":"final1","translation":"[zh-TW] final1",
     "is_final":true,"start_sample":4480,"end_sample":35584,...}}
← {"event":"status","data":{"state":"idle",...}}
```

三種訊息：

| 種類 | 形狀 | 方向 |
|---|---|---|
| 請求 | `{"id": 數字, "method": "名稱", "params": {...}}` | app → 引擎 |
| 回應 | `{"id": 同一個數字, "result": {...}}` 或 `{"id":…, "error": {"code", "message"}}` | 引擎 → app |
| 事件 | `{"event": "名稱", "data": {...}}`（沒有 id） | 引擎 → app，隨時都可能送 |

方法共 12 個（`engine.info`、`hardware.detect`、`models.list`、`models.download`、`models.validate`、`devices.list`、`settings.get`、`settings.set`、`settings.apply_recommendation`、`session.start`、`session.stop`、`engine.shutdown`），定義在 `src/kikitori/engine/server.py` 的 `EngineServer.__init__` 裡的 `_handlers`。事件有 `subtitle`、`status`、`download_progress`、`log` 四種。完整欄位見 `docs/specs/pipeline-engine.md`。

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
  │                         小模型辨識（例如 base，greedy）→ 過濾幻覺
  │                         → 串流翻譯 → subtitle（is_final=false）
  │
  └── 句子結束 ──► 完整佇列（最多 64 句，不丟）
                       │ 完整 worker 執行緒
                       ▼
          較準的模型辨識（例如 small，beam 5，帶前文）→ 過濾幻覺
          → 帶前文翻譯 → subtitle（is_final=true，取代同一句的草稿）
  ▼
engine：把 SubtitleUpdate 加上 session_id，寫成一行 JSON 到 stdout
  ▼
Rust：逐行讀（每行上限 1 MiB）→ 驗證欄位 → 發出 Tauri 事件 engine://subtitle
  ▼
TypeScript：再驗證一次 → applySubtitle() 更新狀態
  ├─► 主視窗「字幕紀錄」頁（最多 300 句）
  └─► 字幕視窗（最新 1–3 句；草稿較淡，定稿轉成字幕黃）
```

## 每個模組一句話

Python 引擎（`src/kikitori/`）：

| 模組 | 一句話 |
|---|---|
| `capture/` | 從音效卡或麥克風抓聲音，統一成 16 kHz、單聲道、int16。 |
| `vad/` | 用 Silero VAD 判斷哪裡有人聲，把連續聲音切成一句一句。 |
| `asr/` | 用 CTranslate2 跑 Whisper，把一句聲音轉成文字，並過濾幻覺。 |
| `translate/` | 呼叫 OpenAI 相容 API（預設本機 Ollama）把一句話翻成字幕。 |
| `pipeline/` | 把上面四個串起來，跑「即時」與「完整」兩條路徑。 |
| `config/` | 讀寫、驗證使用者設定檔 `settings.json`。 |
| `hardware/` | 偵測 CPU、記憶體、NVIDIA 顯示卡，推薦模型組合；下載 cuBLAS。 |
| `engine/` | stdio NDJSON 伺服器，Tauri 唯一的入口。 |
| `evaluation/` | 評測工具（產生噪音、混音、算指標），正式執行時不載入。 |
| `cli.py` | `kikitori` 指令：不帶參數是互動選單，`kikitori engine` 啟動引擎。 |

桌面程式（`app/`）：

| 部分 | 一句話 |
|---|---|
| `app/src-tauri/src/`（Rust） | 啟動引擎、配對請求與回應、驗證並轉發事件、控制字幕視窗、全域快速鍵、自動更新。 |
| `app/src/`（TypeScript） | 主視窗與字幕視窗的畫面；字幕狀態規則在 `lib/subtitles.ts`。 |

其他：

| 部分 | 一句話 |
|---|---|
| `packaging/engine/` | 用 PyInstaller 把引擎打包成資料夾，並產生第三方授權目錄。 |
| `scripts/sync_version.py` | 版本號只在 `pyproject.toml` 改一次，同步到 app 的四個檔案。 |
| `.github/workflows/test.yml` | 三平台跑 Python 測試、lint、版本檢查、引擎打包與冒煙測試。 |
| `experiments/` | 一次性的實驗與基準測試，結果寫在各自的 README。不屬於正式程式。 |

## 執行時有哪些執行緒

知道這個，看程式時比較不會迷路。

| 行程 | 執行緒 | 做什麼 |
|---|---|---|
| 引擎 | 主執行緒 | 讀 stdin、處理快速的請求 |
| 引擎 | 背景工作執行緒（`engine/deferred.py`） | 處理慢的請求：`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation` |
| 引擎 | session 載入執行緒 | 載入模型，然後啟動 pipeline |
| 引擎 | pipeline 的 3 條：capture、即時 worker、完整 worker | 切句、兩條辨識翻譯路徑 |
| 引擎 | 擷取執行緒（WASAPI／soundcard）或系統回呼（ScreenCaptureKit） | 從裝置讀聲音 |
| 引擎 | 下載執行緒 | 模型與 GPU 元件下載 |
| app | Rust 非同步工作 | 讀引擎 stdout／stderr、等待回應、自動重啟 |

## 外部需要什麼

| 東西 | 誰提供 | 沒有會怎樣 |
|---|---|---|
| Whisper 模型 | app 內按「下載」，從 Hugging Face 下載到使用者快取資料夾（固定版本、驗 SHA-256） | `session.start` 後狀態變 `error`，代碼 `model_not_downloaded` |
| 翻譯 LLM | 使用者自己裝 Ollama 並 `ollama pull` 模型 | 字幕只有原文，譯文空白，並送出 warning |
| cuBLAS（GPU 才需要） | app 內勾選同意 NVIDIA EULA 後下載（約 570 MB） | 自動改用 CPU，比較慢 |
| Silero VAD 模型 | 已包在程式裡（`silero_vad.onnx` 約 2.3 MB，MIT） | — |

## 目前完成到哪裡

依 git 紀錄：里程碑 1–6 的引擎模組、里程碑 7 的介面、里程碑 8 前半的打包與測試 CI 都已完成。
**還沒有**：發布用的 `release.yml`（所以自動更新目前沒有東西可抓）、CI 裡的前端與 Rust 檢查、macOS／Linux 實機驗證。細節見 [06-limitations.md](06-limitations.md)。
