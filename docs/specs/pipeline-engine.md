# Spec: pipeline-engine（雙路徑協調器、設定檔、stdio 引擎）

> 狀態：已採用（2026-11-05，Milestone 4，使用者授權自主實作）。
> 上層約定：`docs/specs/app-architecture.md` 的 pipeline、config、engine 協定各節；本文件不得與之矛盾，只補充架構規格沒寫死的細節。
> 依賴的模組：`vad-segmenter.md`、`translate.md`、`capture.md`、`asr.md`（只透過 `kikitori.asr.types` 與 `pipeline/factory.py` 的接縫）。

## 前提假設

1. pipeline 只依賴各模組的對外型別：`Segmenter`／`VoiceModel`（vad）、`Transcriber`／`Transcript`（asr.types）、`Translator`／`TranslationRequest`（translate）、`AudioSource`（capture）。真實元件（Silero、Whisper、OpenAI 相容翻譯器）只在 `pipeline/factory.py` 建立，ASR 實作以延遲匯入接上，測試完全不載入。
2. 音訊位置一律用樣本計數（16 kHz）。pipeline 只在「量即時辨識耗時、調整觸發間隔」時讀時鐘，且時鐘可注入，單元測試沒有時序不確定性。
3. 引擎是單一行程、單一使用者；同時最多一個 session。
4. 不新增依賴：標準庫 + 既有依賴即可。

## pipeline

### 介面

```python
Mode = Literal["media", "interpret"]

@dataclass(frozen=True)
class SubtitleUpdate:          # 照抄架構規格
    utterance_id: int
    original: str
    translation: str
    source_language: str
    target_language: str
    is_final: bool
    start_sample: int
    end_sample: int

@dataclass(frozen=True)
class PipelineConfig:
    mode: Mode
    pause_secs: float                 # media 0.6、interpret 0.5
    max_secs: float                   # media 10、interpret 8
    target_language: str = "zh-TW"
    partner_language: str = "ja"      # interpret 模式的對方語言（ja | en）
    partial_interval_ms: int = 700
    context_lines: int = 3            # 完整路徑帶幾句前文（翻譯 context 與 ASR prompt）
    asr_prompt_context: bool = True   # 完整路徑是否以前文原文當 ASR prompt
    final_queue_size: int = 64
    read_timeout: float = 0.1         # AudioSource.read 的等待上限（秒）

@dataclass(frozen=True)
class PipelineComponents:
    voice_model: VoiceModel
    fast_transcriber: Transcriber
    final_transcriber: Transcriber
    fast_translator: Translator
    final_translator: Translator
    transcript_filter: TranscriptFilter   # Callable[[Transcript], str | None]：None 保留，否則為原因
    final_no_speech_reliable: bool = True # 完整模型的 no_speech 可用嗎（large-v3-turbo 恆為 0 → False）

class Pipeline:
    def __init__(self, components, config, *, on_subtitle, on_status=None, on_log=None,
                 clock=time.monotonic): ...
    # 執行緒模式（引擎使用）
    def start(self, source: AudioSource) -> None
    def stop(self, timeout: float = 5.0) -> None
    def wait(self, timeout: float | None = None) -> bool
    state: Literal["idle", "running", "error"]
    # 逐步模式（測試、離線處理；不開執行緒）
    def feed(self, audio) -> None
    def end_of_stream(self) -> None
    def run_pending(self) -> None
```

- `on_status(state, detail)`：`running`（`start()` 後）、`idle`（正常結束或 `stop()`）、`error`（致命錯誤，detail 為訊息）；每次執行只會送出一個結束狀態。
- `on_log(level, message)`：可恢復的警告（例如翻譯失敗）。
- 回呼在 pipeline 的執行緒上被呼叫，不得回頭呼叫同一個 `Pipeline` 的方法。

### 執行緒與佇列

```text
capture 執行緒            ── read(0.1 s) → Segmenter.process → 事件
  │ UtteranceAudio：累積音訊；每 partial_interval_ms 新音訊 → 放進「最新槽」（只留一個）
  │ UtteranceEnded ：完整音訊 → 完整佇列（有界 64，阻塞放入）
  ▼
即時 worker 執行緒       ── 取最新槽 → fast ASR → 過濾 → fast 翻譯（stream）→ is_final=False
完整 worker 執行緒       ── 依序取完整佇列 → final ASR（帶前文 prompt）→ 過濾 → final 翻譯（帶前文）→ is_final=True
```

- **最新槽（即時路徑）**：放入新工作時直接取代尚未處理的舊工作（計入 `dropped_partials`）。worker 取出工作時若該句已結束（已收到 `UtteranceEnded`），丟棄：完整結果馬上就到，算力留給下一句。串流翻譯途中若該句已結束就關閉串流（`generator.close()`，翻譯器會關閉連線）；槽內出現新工作時**不**中斷翻譯——否則翻譯比間隔慢時每次都被中斷、永遠看不到譯文，而槽本身已保證最多落後一個工作。
- **觸發時機**：句子音訊（含前置緩衝）長度每超過上次觸發點 `max(partial_interval_ms, 上一次即時辨識耗時)` 就觸發一次，送出「到目前為止的全部音訊」。Whisper 每次都跑完整 30 秒的 encoder，耗時幾乎與句長無關；CPU 上比間隔慢時（例如 `small` 約 1.3 s），自動拉長間隔，避免即時路徑佔滿 CPU、拖慢完整路徑。耗時用注入的 `clock` 量測（只影響排程，不影響音訊位置；測試注入假時鐘）。
- **完整佇列不丟**：容量滿時 capture 執行緒阻塞等待（每 0.1 s 檢查停止旗標）。這就是背壓：capture 端 `ChunkBuffer` 只保留最近 5 秒，再落後就由 capture 丟最舊的音訊並記 warning（見 `capture.md`），pipeline 自己不丟完整工作。
- **順序**：完整路徑只有一個 worker，FIFO，所以完整結果依句子順序送出。送出即時與完整結果都經過同一把鎖；`utterance_id ≤ 最後已送出的完整 id` 的即時結果一律丟棄，保證同一句的即時結果不會在完整結果之後出現。
- `utterance_id` 從 1 開始，每個 session 遞增。

### 完整音訊的尾巴

`UtteranceEnded.end_sample` 含句尾判斷用的靜音（`pause` 結束時約 `pause_secs`）。完整路徑把 `pause` 結束的音訊尾巴裁掉 `pause_secs − 0.2 s`（保留 0.2 s 給 Whisper），`SubtitleUpdate.end_sample` 也是裁過的位置（≈ 說話結束處），其他原因不裁。即時路徑的 `end_sample` 是送去辨識的音訊結尾。

### 語言規則

| 模式 | 辨識出的語言 | 譯文語言 |
|---|---|---|
| `media` | 任何 | `target_language`（zh-TW） |
| `interpret` | `zh`（含 zh-TW 等） | `partner_language` |
| `interpret` | 其他 | `target_language`（zh-TW） |

- 原文語言與譯文同為中文（例如 media 模式聽到中文）→ 不翻譯，譯文 = 原文。
- 組合不在 translate 的 `SUPPORTED_PAIRS` 內（例如偵測到 `ko`）→ 譯文為空字串，送一筆 warning。
- 語言偵測的候選範圍由 factory 設定（`DecodeOptions.candidate_languages`）：media 為 `("ja", "en")`，interpret 為 `("zh", partner_language)`。
- `SubtitleUpdate.source_language` 是 ASR 給的 ISO 639-1；`target_language` 是實際譯文語言（`zh-TW`、`ja`、`en`）。

### 前文

- 完整路徑保存最近 `context_lines` 句**已送出的完整原文**（含語言）。翻譯的 `context` 只放與本句同語言的前文（口譯模式兩種語言交錯時，另一種語言的句子會干擾提示詞）；`asr_prompt_context=True` 時同一批前文以空白串接當 ASR prompt。
- 即時路徑不帶 ASR prompt（`asr.md`：即時路徑不帶 prompt），翻譯 context 與完整路徑相同。

### 非語音過濾

- 每個 `Transcript` 先經 `transcript_filter`；被拒就不翻譯。
- pipeline 內建的 `default_transcript_filter(no_speech_threshold)`：空字串／只有空白，或 `no_speech_probability > threshold`。真實元件由 factory 改用 asr 的 `rejection_reason(FilterConfig(no_speech_threshold=...))`（含幻覺句清單、硬門檻 0.8、低信心、壓縮比等規則）。
- **完整模型的 no_speech 不可靠時**（registry 的 `no_speech_reliable=False`，例如 `large-v3-turbo` 恆為 0）：過濾前把完整結果的 `no_speech_probability` 換成即時模型對同一句最後一次的值；句子太短、沒觸發過即時辨識時，在完整 worker 上用即時模型補跑一次（短句正是最常見的誤觸發）。送出的字幕不受影響。
- VAD 本身已要求人聲持續 250 ms 才開句；VAD 機率目前不另外當過濾訊號（`Segmenter` 沒有公開每句的機率，要用需改 vad 模組，留待之後評估）。
- **完整結果被濾掉**：若該句曾送出即時字幕，送一筆 `original="" translation=""` 的完整結果，讓 UI 清掉已顯示的即時字幕；沒送過就什麼都不送。

### 錯誤

| 錯誤 | 處理 |
|---|---|
| `TranslationError`（翻譯失敗、逾時、Ollama 沒開） | 可恢復：該句照送（譯文為空字串），`on_log("warning", …)`；連續相同訊息只報一次，翻譯成功後重新計 |
| 其他任何例外（ASR、capture、回呼本身） | 致命：`state="error"`、`on_status("error", 訊息)`、停止 capture 與所有 worker；不吞掉，也記錄到 logging（含 traceback） |

### 生命週期

- `start(source)`：`source.start()`（失敗直接拋出，不進入 running），建立三條 daemon 執行緒，送 `running`。
- 來源讀完（有 `exhausted` 屬性且為 True，例如 `FileSource`／`ArraySource`）：`Segmenter.flush()` 收尾，等完整佇列處理完，送 `idle`。
- `stop(timeout)`：使用者主動中斷——設停止旗標、`source.stop()`、喚醒等待中的執行緒，丟棄尚未開始的工作，在 `timeout` 內 join；卡在模型呼叫裡的執行緒逾時後記 warning 放手（daemon），停止後它的結果不會送出。之後送 `idle`。可重複呼叫。
- 逐步模式：`feed()` 同步處理音訊並排工作；`end_of_stream()` 等同 flush；`run_pending()` 先跑最新槽（若仍有效）再依序跑全部完整工作。錯誤走與執行緒模式相同的路徑。不得與 `start()` 混用。

## config

### 位置

| 平台 | 路徑 |
|---|---|
| Windows | `%APPDATA%\kikitori\settings.json`（`APPDATA` 未設定或非絕對路徑時 `~\AppData\Roaming`） |
| macOS | `~/Library/Application Support/kikitori/settings.json` |
| Linux 與其他 | `$XDG_CONFIG_HOME/kikitori/settings.json`（未設定或相對路徑時 `~/.config`） |

- 環境變數 `KIKITORI_CONFIG_DIR`（絕對路徑）可覆寫目錄，給測試與可攜版使用；不是絕對路徑就忽略並記 warning。

### 結構（`schema_version = 1`）

```json
{
  "schema_version": 1,
  "mode": "media",
  "target_language": "zh-TW",
  "partner_language": "ja",
  "media":     {"pause_secs": 0.6, "max_secs": 10.0, "device_id": null},
  "interpret": {"pause_secs": 0.5, "max_secs": 8.0,  "device_id": null},
  "asr": {"fast_model": "base", "final_model": "small", "device": "auto", "compute_type": null, "cuda_library_dirs": []},
  "translation": {
    "base_url": "http://localhost:11434/v1",
    "fast_model": "qwen3.5:4b",
    "final_model": "qwen3.5:4b",
    "api_key_env": "KIKITORI_TRANSLATE_API_KEY"
  },
  "pipeline": {"partial_interval_ms": 700, "no_speech_threshold": 0.6, "context_lines": 3, "asr_prompt_context": true},
  "subtitle": {}
}
```

| 欄位 | 驗證 |
|---|---|
| `mode` | `media` \| `interpret`（上次選的模式，給 UI 用） |
| `target_language` | 目前只有 `zh-TW` |
| `partner_language` | `ja` \| `en` |
| `*.pause_secs` | 數字，[0.1, 5.0] |
| `*.max_secs` | 數字，[1, 30]，且大於 `pause_secs` |
| `*.device_id` | `null`（預設裝置）或 1–512 字元的字串 |
| `asr.fast_model`／`final_model` | 1–100 字元，只含英數與 `._-`（不在這裡檢查 registry：設定不依賴 asr 實作；載入時才報「未知模型」） |
| `asr.device` | `auto` \| `cpu` \| `cuda` |
| `asr.compute_type` | `null`（asr 依裝置的預設：CPU int8、CUDA float16）或 CTranslate2 精度名稱（`int8`、`int8_float16`、`float16`…） |
| `asr.cuda_library_dirs` | 最多 16 個絕對路徑（cuBLAS 12 所在目錄，交給 `load_transcriber(cuda_library_dirs=…)`；hardware 里程碑填入） |
| `translation.*` | 交給 `TranslatorConfig` 驗證（`base_url` 必須 http/https、模型名稱非空、`api_key_env` 是合法變數名稱）；模型名稱另限 200 字元 |
| `pipeline.partial_interval_ms` | 整數，[200, 5000] |
| `pipeline.no_speech_threshold` | 數字，[0, 1] |
| `pipeline.context_lines` | 整數，[0, 10] |
| `pipeline.asr_prompt_context` | 布林 |
| `subtitle` | 任意 JSON 物件，引擎不解讀、原樣保存給 UI（字型、位置、顏色…）；巢狀 ≤ 5 層、序列化後 ≤ 16 KiB、不得有 NaN／Infinity |

- ASR 預設為「沒有 GPU 也跑得動」的組合（`asr.md` 的 CPU ≥ 16 GB 等級：即時 `base`、完整 `small`），hardware 偵測之後推薦覆寫。
- 數字欄位不接受布林；整數欄位不接受小數。未知欄位一律報錯（打錯字要讓使用者知道）。缺少的欄位用預設值（舊檔案加新欄位時不必遷移）。
- 錯誤訊息帶欄位路徑：`SettingsError("media.pause_secs: expected a number in [0.1, 5.0], got 'abc'")`。
- 不存 API key 本身，只存環境變數名稱。

### 讀寫

- 讀：檔案不存在 → 預設值（不寫檔）。不是 UTF-8 JSON、不是物件、驗證失敗 → `SettingsError`。檔案大於 1 MiB → `SettingsError`（避免讀進異常大的檔案）。
- **遷移**：`schema_version` 必須是正整數；小於目前版本時依序套用 `MIGRATIONS[n]`（n → n+1，目前沒有）；大於目前版本 → `SettingsError`（新版程式寫的檔案，不覆寫）。
- 寫：先寫同目錄的暫存檔（UTF-8、縮排 2、`ensure_ascii=False`）、`flush` + `fsync`，再 `os.replace` 成正式檔名，中途失敗刪掉暫存檔並往上拋 `OSError`。
- `SettingsStore`（執行緒安全）：`load()` 讀檔；檔案無效時保留預設值並回傳警告，**不覆寫**原檔；之後第一次 `update()` 存檔前把無效檔案改名為 `settings.json.invalid`（保留給使用者檢查）。`update(partial)`：深度合併（物件遞迴合併、其他值整個取代）→ 驗證 → 存檔 → 更新記憶體；驗證失敗時檔案與記憶體都不變。

## engine（stdio NDJSON）

### 傳輸

- stdin、stdout 都以位元組處理、UTF-8 編解碼（Windows 的主控台編碼可能是 cp950，不能靠 `sys.stdout` 的文字層）。
- 每行一個 JSON；輸出 `json.dumps(ensure_ascii=False, separators=(",", ":"), allow_nan=False)` + `\n`，寫完立即 flush；寫入有鎖，多執行緒送事件不會交錯。
- 啟動時把 `sys.stdout` 換成 stderr，任何程式庫的 `print` 都不會污染協定通道；logging 一律寫 stderr。
- 單行上限 1 MiB；超過就讀到行尾丟掉，回 `invalid_request`。
- stdout 寫入失敗（Tauri 端關閉管線）→ 記錄到 stderr 後結束引擎。

### 請求驗證與錯誤碼

| code | 情況 |
|---|---|
| `parse_error` | 不是 UTF-8 或不是 JSON（`id` 為 null） |
| `invalid_request` | 不是物件；`id` 不是整數（布林不算，絕對值 ≤ 2^53−1）；`method` 不是非空字串；`params` 不是物件或 null；有 `id`／`method`／`params` 以外的欄位；行太長 |
| `unknown_method` | 不認得的方法（架構規格） |
| `invalid_params` | 參數缺少、型別或值不對、有未知參數 |
| `invalid_settings` | `settings.set` 驗證失敗，`message` 帶欄位路徑 |
| `settings_io_error` | 設定檔無法寫入 |
| `session_active` | 已有 session 在載入或執行中又 `session.start` |
| `device_not_found` | 裝置 id 格式錯誤或不存在 |
| `capture_unavailable` | 這台機器無法擷取（`BackendUnavailableError`） |
| `capture_error`／`permission_denied`／`device_lost` | `devices.list`／`session.start` 的其他擷取錯誤 |
| `unknown_model` | `models.download` 的模型不在清單中 |
| `download_in_progress` | 同一個模型已在下載 |
| `internal_error` | 未預期的例外（traceback 寫 stderr；訊息只有例外類別與簡短說明） |

- 空行略過。不合法的行只回錯誤，不會讓引擎結束。

### 方法

| 方法 | 參數 | 結果 |
|---|---|---|
| `engine.info` | — | `{"name": "kikitori-engine", "version", "protocol_version": 1, "platform": "windows"\|"macos"\|"linux"\|<其他>, "machine", "python_version", "fake": bool}` |
| `hardware.detect` | — | `{"hardware": {"os", "os_version", "machine", "cpu_count", "ram_bytes", "gpus": [], "apple_silicon"}, "recommendation": null}`（之後 hardware 模組換掉 provider） |
| `models.list` | — | `{"asr": [{"id", "size_bytes", "license", "speed_tier", "downloaded"}], "translation": {"base_url", "installed": [名稱] \| null, "error": 訊息 \| null, "presets": [{"model", "label", "license", "commercial_use", "excluded_regions", "installed"}]}}` |
| `models.download` | `{"model": <ASR id>}` | `{"model", "started": true}`；在背景下載，進度以 `download_progress` 事件回報 |
| `devices.list` | — | `{"devices": [DeviceInfo]}` |
| `settings.get` | — | `{"settings": {...}, "path": 設定檔路徑}` |
| `settings.set` | 部分設定物件（深度合併） | 同 `settings.get` |
| `session.start` | `{"mode": "media"\|"interpret", "device_id"?: str \| null}` | `{"session_id": int}` |
| `session.stop` | — | `{}`（沒有 session 也回 `{}`） |
| `engine.shutdown` | — | `{}`，之後結束 |

- 會啟動背景工作的方法（`session.start`、`models.download`）在**回應寫出之後**才啟動背景執行緒，所以 UI 一定先拿到 `session_id`，才收到該 session 的事件。
- `settings.set` 在 session 執行中也可呼叫，新設定從下一個 session 生效。
- `session.start`：先同步驗證參數與 `open_source()`（只解析 id，不開裝置）。省略 `device_id` 時用設定檔該模式的 `device_id`；`null` 是預設裝置。回應後在背景：`status loading` → 建立元件（載入模型可能要數秒）→ 開裝置 → `status running`。任一步失敗送 `status error`，session 結束。media 用 `loopback`、interpret 用 `microphone`（指定 `device_id` 時以 id 為準，見 `capture.md`）。
- `models.list` 的翻譯部分呼叫 `GET {base_url}/models`（OpenAI 相容），連線與讀取 timeout 3 秒；失敗時 `installed: null` 並在 `error` 說明（不讓整個方法失敗）。`http://localhost` 改連 `127.0.0.1`（理由見 `translate.md`）。
- `models.download`：背景 daemon 執行緒呼叫 factory 的 `download_asr_model()`；`engine.shutdown` 時以 `cancel` 事件取消。

### 事件

| 事件 | 內容 |
|---|---|
| `subtitle` | `SubtitleUpdate` 的全部欄位 + `session_id` |
| `status` | `{"state": "idle"\|"loading"\|"running"\|"error", "detail": 字串 \| null, "session_id": int \| null, "code": 字串 \| null}` |
| `download_progress` | `{"model", "downloaded_bytes", "total_bytes", "state": "downloading"\|"done"\|"error", "error": 字串 \| null}` |
| `log` | `{"level": "debug"\|"info"\|"warning"\|"error", "message"}` |

- 引擎啟動完成後先送一筆 `status idle`。
- `status error` 的 `code`：`model_not_downloaded`、`unknown_model`、`device_not_found`、`permission_denied`、`device_lost`、`capture_unavailable`、`capture_error`、`pipeline_error`、`load_error`。明確指定 `asr.device="cuda"` 而同一行程 CUDA 曾失敗時，asr 丟出的 `RuntimeError` 訊息（要求重啟引擎）原樣放在 `load_error` 的 `detail`。
- `download_progress` 節流：每 0.25 秒或每 1% 最多一筆；開始（0）與結束（`done`／`error`）一定送。
- `log`：`kikitori` logger 的 INFO 以上同時寫 stderr 與 `log` 事件（訊息由各模組產生，翻譯模組已遮蔽金鑰；引擎自己不記錄設定中的環境變數值）。
- `subtitle`／`status` 帶 `session_id`，UI 可丟掉舊 session 的遲到事件。`state`、`code`、`session_id`、`error` 是相對架構規格的**新增欄位**（只增不改）。

### 結束

- `engine.shutdown`：先回 `{}`，再停 session（`stop(timeout=5)`）、取消下載，結束碼 0。
- stdin EOF：同樣清理後結束碼 0。

### 可注入的服務（測試與之後的里程碑）

`EngineServices`：`settings_store`、`list_devices`、`open_source`、`build_components(settings, mode)`、`hardware`（`HardwareProvider.detect()`）、`models`（`ModelCatalog.list()`／`download()`）。

- 預設實作：真實 capture、`pipeline/factory.py`、`BasicHardwareProvider`（標準庫：`platform`、`os.cpu_count()`、Windows `GlobalMemoryStatusEx`／POSIX `sysconf`）。
- **假模式**：環境變數 `KIKITORI_ENGINE_FAKE=1` 時改用 `kikitori/engine/testing.py`：合成音訊（`fake:loopback:speech` 兩段「人聲」後結束、`fake:microphone:endless` 只有靜音不會結束）、依振幅判斷的 `VoiceModel`、回固定文字的 ASR 與翻譯、會回報進度的假下載。`engine.info` 的 `fake` 為 true，啟動時記 warning。只給測試與 UI 開發用。

## 入口

- `kikitori engine`：啟動 stdio 引擎（給 Tauri；開發時 `uv run kikitori engine`）。`python -m kikitori engine` 相同。
- `kikitori`（沒有參數）：互動選單（`input()` 編號選單）——1 啟動引擎（stdio）、2 列出音訊裝置、3 顯示設定檔路徑、4 離開。
- 其他參數：用法說明寫 stderr，結束碼 2。

## ASR 接縫（`pipeline/factory.py`）

只有這個檔案匯入 `kikitori.asr`（除了 `asr.types`），而且在函式內延遲匯入：

- `create_transcriber(model_id, *, device, role, languages, compute_type, cuda_library_dirs) -> Transcriber`：`load_transcriber(spec, device=..., compute_type=..., options=FAST_DECODE/FINAL_DECODE + candidate_languages, cuda_library_dirs=..., download=False)`；模型未下載 → `FileNotFoundError`（引擎轉成 `model_not_downloaded`）。
- `no_speech_reliable(model_id)`：registry 的 `no_speech_reliable`，設定 `PipelineComponents.final_no_speech_reliable`。
- `create_transcript_filter(no_speech_threshold)`：asr 的 `rejection_reason` + `FilterConfig`。
- `asr_model_catalog()`、`download_asr_model(model_id, progress, cancel)`：asr 的 `MODELS`、`is_downloaded`、`download_model`、`default_cache_dir`。
- `build_components(settings, mode) -> PipelineComponents`：`SileroVad`、兩個 transcriber、兩個 `OpenAICompatibleTranslator`（即時路徑 `max_retries=0`、`read_timeout=10 s`：重試只會讓即時字幕更晚；完整路徑用翻譯模組預設值）、filter。

## Tech Stack

- Python 3.12、uv；只用標準庫與既有依賴（numpy、onnxruntime、opencc、capture 後端、ctranslate2 經 asr）。

## Commands

```text
測試（本模組）：uv run python -m unittest tests.test_config tests.test_pipeline tests.test_engine
測試（全部）：  uv run python -m unittest discover -s tests
Lint：          uv run ruff check
引擎：          uv run kikitori engine
選單：          uv run kikitori
端到端實測：    uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py
```

## Project Structure

```text
src/kikitori/
  __init__.py            → main()：轉交 cli
  __main__.py            → python -m kikitori
  cli.py                 → 子指令與互動選單
  config/
    __init__.py
    paths.py             → 平台設定目錄
    settings.py          → Settings dataclass、驗證、遷移、to_dict/from_dict
    store.py             → 原子寫入、load/save、SettingsStore
  pipeline/
    __init__.py
    types.py             → Mode、SubtitleUpdate、PipelineConfig、PipelineComponents、TranscriptFilter
    languages.py         → 語言規則（譯文語言、是否需要翻譯）
    scheduler.py         → 最新槽與有界完整佇列
    pipeline.py          → Pipeline
    filtering.py         → default_transcript_filter
    factory.py           → 真實元件與 ASR 接縫（延遲匯入）
  engine/
    __init__.py
    protocol.py          → 協定常數、請求解析與驗證、錯誤碼、ProtocolError
    io.py                → MessageWriter（鎖、flush）、行讀取（長度上限）、EventLogHandler（logging → log 事件）
    main.py              → run_engine()：stdio 與 logging 設定、選擇服務
    services.py          → EngineServices、HardwareProvider、ModelCatalog 與預設實作
    server.py            → EngineServer：分派、session、下載
    testing.py           → 假模式服務
tests/
  test_config.py
  test_pipeline.py
  test_pipeline_factory.py → 設定 → asr／翻譯器參數（mock load_transcriber）
  test_engine.py         → 子行程 `python -m kikitori engine`（假模式）
  test_cli.py
experiments/pipeline_smoke/
  smoke.py、README.md
```

## Testing Strategy

- config：預設值、每種驗證錯誤（型別、範圍、未知欄位、布林當數字、max ≤ pause）、round-trip、深度合併、原子寫入（替換失敗時原檔不變、沒有殘留暫存檔）、新版 schema 拒絕、三平台路徑（注入 system／env／home）、`KIKITORI_CONFIG_DIR`、無效檔案改名。
- pipeline（逐步模式、假 VoiceModel／Transcriber／Translator）：即時→完整順序與取代、最新槽丟舊工作、句子結束後丟即時工作、完整結果不丟且依序（含佇列背壓）、兩種模式的語言路由、中文不翻譯、不支援組合、非語音過濾與「清除」完整結果、前文與 ASR prompt、尾巴裁切、翻譯錯誤為 warning、ASR 錯誤為 error 狀態、完整模型 no_speech 不可靠時改用即時模型的值、即時辨識慢時自動拉長間隔（假時鐘）；執行緒模式：`ArraySource` 跑完送 `idle`、`stop()` 乾淨結束（以事件等待，不用 sleep 判斷）、worker 例外變 `error`。
- engine：子行程（假模式、`KIKITORI_CONFIG_DIR` 指到暫存目錄）交換 NDJSON：`engine.info`、`unknown_method`、壞 JSON／非物件／錯型別 id／超長行、`settings.get`／`set`（成功、驗證錯誤）、`devices.list`、`hardware.detect`、`models.list`、`models.download` 進度事件、session 產生字幕事件與 `idle`、`session.stop`、`session_active`、`engine.shutdown` 與 stdin EOF 結束碼 0、stdout 每一行都是合法 JSON；另以同行程測 `MessageWriter`、請求解析。
- 不連網、不開真實裝置、不載入真模型。

## Boundaries

- **Always**：測試與 lint 全過；驗證所有外部輸入（stdin、設定檔、參數）；I/O 設 timeout；例外記錄並往上回報；stdout 只寫協定訊息。
- **Ask first**：新增依賴；修改 vad／translate／capture／asr 的程式碼（只修真正的 bug 並回報）；改架構規格。
- **Never**：寫死或記錄 API key；空的 `except`；在 pipeline 用牆鐘時間算音訊位置；pipeline 單元測試依賴 sleep 的時序。

## Success Criteria

1. 單元測試與 lint 全過，涵蓋上述正常與錯誤路徑，不需網路、音效卡、模型。
2. 協定表中每個方法與事件都有實作，並有子行程測試。
3. 端到端實測（anime.wav → Silero → 真 ASR → 本機 Ollama）印出字幕串流與每句的即時、完整延遲，結果寫入 `experiments/pipeline_smoke/README.md`。
