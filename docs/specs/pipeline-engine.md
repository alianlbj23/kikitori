# Spec: pipeline-engine（雙路徑協調器、設定檔、stdio 引擎）

> 狀態：已採用（2026-11-05，Milestone 4，使用者授權自主實作）；Milestone 6（2026-11-05）接上 hardware 模組，變更列在「協定變更（Milestone 6）」；
> Milestone 12–14（多國語言：依語言推薦、訊息碼、延後下載翻譯模型、只顯示原文）的變更列在「協定變更（Milestone 12–14）」。
> 競品補強 F2／F4／F7（穩定的即時字幕、延遲與音量、模型管理，2026-11-06）列在「協定變更（F2／F4／F7）」。
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
    stable_chars: int | None = None   # F2：只有即時字幕有（見「穩定前綴」）
    latency_ms: int | None = None     # F4：只有完整字幕有（見「量測」）

@dataclass(frozen=True)
class PipelineConfig:
    mode: Mode
    pause_secs: float                 # media 0.6、interpret 0.5
    max_secs: float                   # media 10、interpret 8
    target_language: str = "zh-TW"    # 字幕語言；口譯時是「我的語言」（語言清單的語言碼）
    partner_language: str = "ja"      # interpret 模式的對方語言（語言清單的語言碼）
    source_language: str = "auto"     # media 的收聽語言或 "auto"；只影響原文的字形顯示
    translation_enabled: bool = True  # False = 只顯示原文，完全不呼叫翻譯器
    partial_interval_ms: int = 700
    context_lines: int = 3            # 完整路徑帶幾句前文（翻譯 context 與 ASR prompt）
    asr_prompt_context: bool = True   # 完整路徑是否以前文原文當 ASR prompt（只在 media 模式生效）
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
    error: BaseException | None   # 讓這次執行以 error 結束的例外
    # 逐步模式（測試、離線處理；不開執行緒）
    def feed(self, audio) -> None
    def end_of_stream(self) -> None
    def run_pending(self) -> None
    # 只顯示原文（兩種模式都可用、任何執行緒）
    translation_enabled: bool
    def set_translation_enabled(self, enabled: bool) -> None
    # F4 量測（任何執行緒）
    def metrics(self) -> PipelineMetrics   # level_dbfs, backlog, latency_ms_median

def release_components(components: PipelineComponents) -> None   # pipeline/types.py：關閉 transcriber
```

- `set_translation_enabled(enabled)`（Milestone 14）：執行中開關翻譯。之後才決定路由的句子依新狀態處理（每句在路由時讀一次旗標；
  bool 的讀寫是原子的，worker 讀到舊值或新值都是合法狀態，不需要鎖）；已送出的字幕不補翻、也不清掉譯文。元件的保溫工作有
  `set_active()` 時一併切換（見「ASR 接縫」的 `attach_keep_warm()`）：關閉翻譯就不再送保溫請求，開啟時保溫工作在背景先預熱一次
  （不阻塞呼叫端）。`enabled` 不是 bool → `TypeError`。

- `on_status(state, detail)`：`running`（`start()` 後）、`idle`（正常結束或 `stop()`）、`error`（致命錯誤，detail 為訊息）；每次執行只會送出一個結束狀態。送出 `error` 前 `error` 屬性已設為該例外（`on_status` 的簽名不變；需要例外類型的呼叫端在回呼中讀這個屬性）。
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

### 穩定前綴（F2，LocalAgreement-2）

- 即時 worker 對同一句的連續兩則即時結果，在**顯示用的原文**（`strip()` + `display_original()` 的字形轉換，與 `SubtitleUpdate.original` 相同）上取
  最長共同前綴（Python 字元數），即 whisper_streaming 的 LocalAgreement-n、n = 2，在文字層比較（即時路徑每次重新辨識整句，沒有 token 對齊）。
  結果放在即時字幕的 `stable_chars`：這句的第一則為 0；永遠 ≤ `len(original)`（原文變短時就是新原文的長度以內）。被過濾掉的即時結果不算一則。
  每句重設；只記目前這一句（`pipeline/stability.py` 的 `PartialStability`，只有即時 worker 使用，不需要鎖）。
- **即時翻譯何時重送**（取代「每則即時字幕都翻譯」）：這句的第一則一定送（第二則才知道穩定前綴，不能讓第一則沒有譯文，第一個譯文的延遲與以前相同）；
  之後只在 `stable_chars` 比上一次送翻譯時**變大**、且原文與上一次送出的原文不同時才送，送的是**目前完整的原文**（穩定前綴＋尾巴），請求與以前相同
  （`stream`、同樣的前文）。不送時這則即時字幕的 `translation` 沿用這句最後一次的即時譯文（不清空、不閃爍），原文與 `stable_chars` 照常更新。
  翻譯失敗也算送過（不會每則都重試）；失敗而沒有任何譯文時 `translation` 為空字串（與以前相同）。請求數因此**不會多於以前**。
- 不翻譯的路由（`copy`／`off`／`unsupported`）照樣有 `stable_chars`；完整路徑完全不變（一定翻譯，沒有 `stable_chars`）。

### 量測（F4）

- **`latency_ms`**（完整字幕）：capture 執行緒處理 `UtteranceEnded`（VAD 判定句子結束）時以注入的 `clock` 記下時間（`Job.ended_at`），
  送出完整字幕前再讀一次，差值取整數毫秒（≥ 0）。包含排隊、完整辨識與翻譯；**不含**判定句尾所需的靜音（`pause_secs`）與擷取緩衝——
  使用者實際感受的「說完到看到」約再加 `pause_secs`。被過濾的「清除」完整結果（原文空字串）也帶 `latency_ms`，但不計入中位數。
  注入的時鐘只用來排程即時辨識與量測延遲，音訊位置仍是樣本計數。
- **`Pipeline.metrics()`** → `PipelineMetrics(level_dbfs, backlog, latency_ms_median)`（`pipeline/metrics.py`，執行緒安全）：
  - `level_dbfs`：最近約 1 秒（16000 樣本，以 100 ms 為單位）**擷取到**的音訊（`source.read()` 的結果，逐步模式為 `feed()` 的音訊）的 RMS，
    `20·log10(rms / 32768)`，限制在 [−120.0, 0.0]、取到小數一位；沒有音訊或全靜音為 −120.0。以樣本數為窗，不讀時鐘（擷取停住時保持最後的值）。
  - `backlog`：句子已結束、還在完整路徑佇列中**等待**的句數（`FinalQueue.size()`；不含正在辨識的那一句）。
  - `latency_ms_median`：最近 20 則**非空**完整字幕的 `latency_ms` 中位數（四捨五入成整數）；還沒有時 None。

### 完整音訊的尾巴

`UtteranceEnded.end_sample` 含句尾判斷用的靜音（`pause` 結束時約 `pause_secs`）。完整路徑把 `pause` 結束的音訊尾巴裁掉 `pause_secs − 0.2 s`（保留 0.2 s 給 Whisper），`SubtitleUpdate.end_sample` 也是裁過的位置（≈ 說話結束處），其他原因不裁。即時路徑的 `end_sample` 是送去辨識的音訊結尾。

### 語言規則

語言資料來自 `kikitori.languages`（20 種，見 `languages-i18n.md`）。`route()` 的規則：

| 模式 | 辨識出的語言 | 譯文語言 | 動作（`Route.action`） |
|---|---|---|---|
| `media` | 任何 | `target_language` | `translate` |
| `interpret` | 我的語言（`target_language` 的 Whisper 語言） | `partner_language` | `translate` |
| `interpret` | 其他 | `target_language` | `translate` |
| 任何 | 與譯文語言同一個 Whisper 語言（例如 `zh` → `zh-CN`） | 同上 | `copy`：不翻譯，譯文 = 原文轉成譯文語言的字形（`zh-TW` → `s2twp` 選擇性轉換、`zh-CN` → `t2s`；其他語言原樣） |
| 任何 | 不在語言清單內（例如 `nn`） | 同上 | `unsupported`：譯文空字串，送一筆 warning |
| 任何 | `translation_enabled=False` | 同上 | `off`：不呼叫翻譯器，譯文空字串，不發 warning |

- 原文的顯示字形（`languages.display_original()`；即時與完整路徑相同）：依序找設定中第一個與辨識語言同 Whisper 語言的語言，套用它的轉換——口譯為（我的語言, 對方語言），影片翻譯為（收聽語言, 字幕語言）（收聽語言為 `auto` 時只有字幕語言）。Whisper 的中文結果通常是簡體：口譯時那是使用者自己說的話；聽繁中看簡中時原文仍顯示繁體。只有中文有轉換規則——日文新字體（気、駅）不是簡體。
- 模型不支援的組合（例如 Sakura 遇到 `en`）由翻譯器以 `InvalidRequestError` 拒絕，沿用「翻譯失敗只顯示原文＋warning」。
- ASR 的語言由 factory 設定（`asr_languages()`）：media 指定收聽語言時以 `DecodeOptions.forced_language` 固定（不做語言偵測，避免短句誤判）；`auto` 時偵測範圍 `candidate_languages` 限語言清單的 Whisper 語言；interpret 的偵測範圍為（我的語言, 對方語言）的 Whisper 語言。
- `SubtitleUpdate.source_language` 是 ASR 給的 Whisper 語言碼（中文為 `zh`）；`target_language` 是譯文語言（語言清單的語言碼）。

### 前文

- 完整路徑保存最近 `context_lines` 句**已送出的完整原文**（含語言）。翻譯的 `context` 只放與本句同語言的前文（口譯模式兩種語言交錯時，另一種語言的句子會干擾提示詞）；`asr_prompt_context=True` 且為 `media` 模式時，同一批前文以空白串接當 ASR prompt；`interpret` 模式一律不帶（兩種語言交錯，前文會把語言偵測拉向上一位說話者）。
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

- `start(source)`：`source.start()`（失敗直接拋出，不進入 running），建立三條 daemon 執行緒，送 `running`。兩個 worker 先啟動、capture 最後：capture 讀完時依 worker 是否仍在執行決定要不要等，worker 未啟動就讀完的話會不等完整結果就送 `idle`。
- 來源讀完（有 `exhausted` 屬性且為 True，例如 `FileSource`／`ArraySource`）：`Segmenter.flush()` 收尾，等完整佇列處理完，送 `idle`。
- `stop(timeout)`：使用者主動中斷——設停止旗標、`source.stop()`、喚醒等待中的執行緒，丟棄尚未開始的工作，在 `timeout` 內 join；卡在模型呼叫裡的執行緒逾時後記 warning 放手（daemon），停止後它的結果不會送出。之後送 `idle`。可重複呼叫。
- 逐步模式：`feed()` 同步處理音訊並排工作；`end_of_stream()` 等同 flush；`run_pending()` 先跑最新槽（若仍有效）再依序跑全部完整工作。錯誤走與執行緒模式相同的路徑。不得與 `start()` 混用。
- **元件的釋放**（2026-11-06，memory_soak #1／#2）：pipeline 不擁有元件的生命週期；引擎在 session 結束時呼叫 `release_components()`（每個不同的 transcriber
  呼叫一次 `close()`，有的話）。真實的 Whisper transcriber 因此把 CTranslate2 模型卸載並放回模型池（`asr.md`「模型物件池」）。
  **順序**（2026-11-06 review）：停止 metrics → 釋放元件 → 送出結束狀態（`idle`／`error`）。UI 收到結束狀態就可能 `session.start`，舊模型不能
  還佔著顯示記憶體（每個 `close()` 最多等進行中的推論 2 秒）。釋放在另一條執行緒（`session-<id>-release`）上進行，最多等
  `RELEASE_TIMEOUT = 5.0` 秒；逾時記 warning（`session N is still releasing its models …`）並照樣送出結束狀態，釋放在背景繼續，期間
  `models.delete` 仍回 `model_in_use`（`_Session.releasing`）。
- **例外不能把元件留在循環裡**（memory_soak #1）：存起來、之後在別的執行緒丟出的擷取錯誤一律丟**新的複本**（`capture.errors.detached_copy()`：同類別、
  同訊息、同 `__cause__`、沒有 traceback）——`ThreadedSource.start()` 的開裝置錯誤與 `ChunkBuffer.get()` 的擷取錯誤；保存前也清掉擷取執行緒的
  traceback。否則丟出處（`Pipeline.start` → 元件 → GPU 上的模型）的 frame 會接到 source 保存的例外上，形成只能靠循環垃圾回收釋放的循環
  （實測每次「開始」約 3 GB 顯示記憶體留到引擎結束）。引擎的 `_load_session` 在記錄之後也清掉例外鏈的 traceback（`_clear_tracebacks()`），與 `Pipeline._fail()` 一致。

## config

### 位置

| 平台 | 路徑 |
|---|---|
| Windows | `%APPDATA%\kikitori\settings.json`（`APPDATA` 未設定或非絕對路徑時 `~\AppData\Roaming`） |
| macOS | `~/Library/Application Support/kikitori/settings.json` |
| Linux 與其他 | `$XDG_CONFIG_HOME/kikitori/settings.json`（未設定或相對路徑時 `~/.config`） |

- 環境變數 `KIKITORI_CONFIG_DIR`（絕對路徑）可覆寫目錄，給測試與可攜版使用；不是絕對路徑就忽略並記 warning。

### 結構（`schema_version = 3`）

```json
{
  "schema_version": 3,
  "mode": "media",
  "source_language": "ja",
  "target_language": "zh-TW",
  "partner_language": "ja",
  "media":     {"pause_secs": 0.6, "max_secs": 10.0, "device_id": null},
  "interpret": {"pause_secs": 0.5, "max_secs": 8.0,  "device_id": null},
  "asr": {
    "fast_model": "base", "final_model": "small", "device": "auto",
    "fast_compute_type": null, "final_compute_type": null,
    "fast_cpu_threads": null, "final_cpu_threads": null,
    "cuda_library_dirs": []
  },
  "translation": {
    "enabled": true,
    "base_url": "http://localhost:11434/v1",
    "fast_model": "qwen3.5:4b",
    "final_model": "qwen3.5:4b",
    "api_key_env": "KIKITORI_TRANSLATE_API_KEY",
    "allow_restricted": false,
    "keep_warm_secs": 240,
    "glossary": {
      "builtin": true,
      "entries": [
        {"source": "ぺこら", "target": "佩克拉", "target_language": "zh-TW"},
        {"source": "Hololive", "target": "hololive", "target_language": null}
      ]
    }
  },
  "pipeline": {"partial_interval_ms": 700, "no_speech_threshold": 0.6, "context_lines": 3, "asr_prompt_context": true},
  "subtitle": {},
  "ui": {}
}
```

| 欄位 | 驗證 |
|---|---|
| `mode` | `media` \| `interpret`（上次選的模式，給 UI 用） |
| `source_language` | 語言清單的語言碼或 `auto`（影片翻譯的收聽語言；`auto` = 在清單的 Whisper 語言中自動偵測）。可與字幕語言相同（只做字形轉換或原樣顯示） |
| `target_language` | 語言清單的語言碼（影片翻譯的字幕語言；口譯時是「我的語言」） |
| `partner_language` | 語言清單的語言碼。`mode` 為 `interpret` 時 Whisper 語言不能和 `target_language` 相同（口譯靠語言偵測分辨說話者；例如 zh-TW 與 zh-CN 不能配對）；影片翻譯模式不檢查（例如字幕語言 ja、預設對方語言 ja 也能儲存），切換到口譯時重新驗證。`session.start {mode: "interpret"}` 也會檢查（回 `invalid_settings`）。UI 改字幕語言時若會衝突要一併改對方語言 |
| 語言碼共通 | 不分大小寫、底線視同連字號、接受舊別名（`zh-Hant`→`zh-TW`、`zh-Hans`→`zh-CN`），存成正規化後的語言碼；`zh` 不分繁簡，不接受 |
| `*.pause_secs` | 數字，[0.1, 5.0] |
| `*.max_secs` | 數字，[1, 30]，且大於 `pause_secs` |
| `*.device_id` | `null`（預設裝置）或 1–512 字元的字串 |
| `asr.fast_model`／`final_model` | 1–100 字元，只含英數與 `._-`（不在這裡檢查 registry：設定不依賴 asr 實作；載入時才報「未知模型」） |
| `asr.device` | `auto` \| `cpu` \| `cuda`；兩條路徑共用（asr 在同一行程 CUDA 失敗一次就不再試，分開設定沒有意義；hardware 的推薦也一律兩條路徑同一裝置） |
| `asr.fast_compute_type`／`final_compute_type` | `null`（asr 依實際裝置的預設：CPU int8、CUDA float16）或 CTranslate2 精度名稱（`int8`、`int8_float32`、`int8_float16`、`int8_bfloat16`、`int16`、`float16`、`bfloat16`、`float32`） |
| `asr.fast_cpu_threads`／`final_cpu_threads` | `null`（自動：`device="cuda"` 時 0＝CTranslate2 預設；否則 `hardware.auto_cpu_threads()`＝`min(8, 實體核心 // 2)`，兩條路徑同時在 CPU 上也不超過實體核心數）或整數 [1, 256] |
| `asr.cuda_library_dirs` | 最多 16 個絕對路徑（使用者自行指定的 cuBLAS 12 目錄）；kikitori 下載的 GPU 執行環境不必列入，factory 建立元件時自動加在後面 |
| `translation.*` | 交給 `TranslatorConfig` 驗證（`base_url` 必須 http/https、模型名稱非空、`api_key_env` 是合法變數名稱）；模型名稱另限 200 字元 |
| `translation.enabled` | 布林，預設 **true**；false = 只顯示原文：pipeline 不呼叫翻譯器、factory 不預熱也不保溫 |
| `translation.allow_restricted` | 布林，預設 **false**：是否推薦有地區限制授權的翻譯模型（HY-MT 的授權不適用於歐盟、英國、南韓）。引擎不知道使用者所在地、用語系猜地區不可靠，推薦了不能用的授權會讓使用者在不知情下違約，所以預設不推薦；UI 在設定頁讓使用者確認「我不在歐盟／英國／南韓」後再設為 true。只影響 `recommend()` 與 `models.validate` 的警告，不阻止手動選用 |
| `translation.keep_warm_secs` | 整數，預設 **240**：0（關閉定期保溫；執行中開啟翻譯時仍在背景預熱一次）或 [10, 3600]。session 執行中翻譯請求停了這麼多秒，就對本機翻譯服務送一次預熱請求，讓模型不被卸載（見「ASR 接縫」的 `attach_keep_warm()`）。預設值低於 Ollama 預設的 `keep_alive` 5 分鐘；伺服器改過 `OLLAMA_KEEP_ALIVE` 時要設得比它短。遠端服務一律不送。新欄位有預設值，舊的 schema 2 檔案照樣讀得進來，所以**不升 schema** |
| `translation.glossary` | 物件，只能有 `builtin`、`entries`（Milestone 16，`translate.md`「術語表」）。缺少時用預設值 `{"builtin": true, "entries": []}`；新欄位有預設值，**不升 schema**（與 `keep_warm_secs` 相同） |
| `translation.glossary.builtin` | 布林，預設 **true**：原文是日語、譯文是 zh-TW／zh-CN／en／ko 時，使用內建的直播用語術語表（53 個詞條） |
| `translation.glossary.entries` | 陣列，最多 **500** 筆，預設 `[]`；每筆是只有 `source`、`target`、`target_language` 三個鍵的物件（`target_language` 可省略）。錯誤路徑形如 `translation.glossary.entries[3].source` |
| `entries[].source` | 字串，1–100 字元，不可有頭尾空白、換行或控制字元（原文寫法；比對時不分大小寫與全半形） |
| `entries[].target` | 字串，1–200 字元，同上的限制（譯法） |
| `entries[].target_language` | `null`／省略（所有譯文語言）或語言清單的語言碼（同語言碼共通規則，存成正規化後的碼；`zh` 不接受）。同一個原文寫法（NFKC＋不分大小寫）與同一個 `target_language` 只能有一筆（`entries[i].source: duplicate of an earlier entry`）；同寫法、不同 `target_language` 可以並存 |
| `pipeline.partial_interval_ms` | 整數，[200, 5000] |
| `pipeline.no_speech_threshold` | 數字，[0, 1] |
| `pipeline.context_lines` | 整數，[0, 10] |
| `pipeline.asr_prompt_context` | 布林 |
| `subtitle` | 任意 JSON 物件，引擎不解讀、原樣保存給 UI（字型、位置、顏色…）；巢狀 ≤ 5 層、序列化後 ≤ 16 KiB、不得有 NaN／Infinity |
| `ui` | UI 自己的狀態，限制與 `subtitle` 相同、引擎不解讀。目前只有 `dismissed_recommendations`（「不要再提醒」過的推薦指紋，最多 10 個，見 `settings.preview_recommendation`）。schema 2 之後新增、有預設值 `{}`，**不升 schema**（與 `keep_warm_secs` 相同） |

- ASR 預設為「沒有 GPU 也跑得動」的組合（`asr.md` 的 CPU ≥ 16 GB 等級：即時 `base`、完整 `small`），hardware 偵測之後推薦覆寫。
- 數字欄位不接受布林；整數欄位不接受小數。未知欄位一律報錯（打錯字要讓使用者知道）。缺少的欄位用預設值（舊檔案加新欄位時不必遷移；例如 `translation.keep_warm_secs` 是 schema 2 之後新增、沒有升版的欄位。代價是舊版程式讀到新版寫的檔案會報「unknown setting」，`SettingsStore` 會保留原檔並用預設值）。
- 錯誤訊息帶欄位路徑：`SettingsError("media.pause_secs: expected a number in [0.1, 5.0], got 'abc'")`。
- 不存 API key 本身，只存環境變數名稱。

### 讀寫

- 讀：檔案不存在 → 預設值（不寫檔）。不是 UTF-8 JSON、不是物件、驗證失敗 → `SettingsError`。檔案大於 1 MiB → `SettingsError`（避免讀進異常大的檔案）。
- **遷移**：`schema_version` 必須是正整數；小於目前版本時依序套用 `MIGRATIONS[n]`（n → n+1）；大於目前版本 → `SettingsError`（新版程式寫的檔案，不覆寫）。遷移只在記憶體中進行，檔案在下一次 `settings.set`／`settings.apply_recommendation` 時才以新版格式寫回。
  - **2 → 3**（多國語言）：補 `source_language: "ja"`、`translation.enabled: true`（已有時保留），其他欄位原樣保留；v2 的 `target_language`（zh-TW）與 `partner_language`（ja／en）在 v3 仍合法。注意 v2 的影片翻譯會在 ja／en 間自動偵測，遷移後固定聽日語；聽英文的使用者要改成 `en` 或 `auto`。
  - **1 → 2**（Milestone 6）：`asr.compute_type` 拆成 `asr.fast_compute_type` 與 `asr.final_compute_type`（v1 的值複製到兩邊）。新增的 `asr.fast_cpu_threads`、`asr.final_cpu_threads`（null）與 `translation.allow_restricted`（false）用預設值。v1 的欄位名稱在 v2 是未知欄位（`settings.set {"asr": {"compute_type": …}}` 回 `invalid_settings`）。
- 寫：先寫同目錄的暫存檔（UTF-8、縮排 2、`ensure_ascii=False`）、`flush` + `fsync`，再 `os.replace` 成正式檔名，中途失敗刪掉暫存檔並往上拋 `OSError`。
- `SettingsStore.is_new`：`load()` 時檔案不存在、且之後沒有成功的 `update()` → True（第一次執行）；檔案存在但無效 → False（那是使用者存過的設定，不自動覆寫）；`load()` 之前 → False。`update_if_new(patch)`：只在 `is_new` 時寫入，否則不動並回傳 None——檢查與寫入在同一把鎖內。
- `SettingsStore`（執行緒安全）：`load()` 讀檔；檔案無效時保留預設值並回傳警告，**不覆寫**原檔；之後第一次 `update()` 存檔前把無效檔案改名為 `settings.json.invalid`（保留給使用者檢查）。`update(partial)`：深度合併（物件遞迴合併、其他值整個取代）→ 驗證 → 存檔 → 更新記憶體；驗證失敗時檔案與記憶體都不變。

## engine（stdio NDJSON）

### 傳輸

- stdin、stdout 都以位元組處理、UTF-8 編解碼（Windows 的主控台編碼可能是 cp950，不能靠 `sys.stdout` 的文字層）。
- 每行一個 JSON；輸出 `json.dumps(ensure_ascii=False, separators=(",", ":"), allow_nan=False)` + `\n`，寫完立即 flush；寫入有鎖，多執行緒送事件不會交錯。
- 啟動時把 `sys.stdout` 換成 stderr，任何程式庫的 `print` 都不會污染協定通道；logging 一律寫 stderr。stderr 也改成 UTF-8（`errors="backslashreplace"`）：Tauri 以 UTF-8 讀它寫進程式日誌，Python 預設的地區編碼（日文 Windows 為 cp932）會讓裝置名稱等文字變成亂碼。
- 單行上限 1 MiB；超過就讀到行尾丟掉，回 `invalid_request`。
- stdout 寫入失敗（Tauri 端關閉管線）→ 記錄到 stderr 後結束引擎。

### 請求驗證與錯誤碼

| code | 情況 |
|---|---|
| `parse_error` | 不是 UTF-8 或不是 JSON（`id` 為 null） |
| `invalid_request` | 不是物件；`id` 不是整數（布林不算，絕對值 ≤ 2^53−1）；`method` 不是非空字串；`params` 不是物件或 null；有 `id`／`method`／`params` 以外的欄位；行太長 |
| `unknown_method` | 不認得的方法（架構規格） |
| `invalid_params` | 參數缺少、型別或值不對、有未知參數 |
| `invalid_settings` | `settings.set` 驗證失敗，`message` 帶欄位路徑；`session.start {mode: "interpret"}` 時設定的 `partner_language` 與 `target_language` 是同一個 Whisper 語言 |
| `settings_io_error` | 設定檔無法寫入 |
| `session_active` | 已有 session 在載入或執行中又 `session.start` |
| `device_not_found` | 裝置 id 格式錯誤或不存在 |
| `capture_unavailable` | 這台機器無法擷取（`BackendUnavailableError`） |
| `capture_error`／`permission_denied`／`device_lost` | `devices.list`／`session.start` 的其他擷取錯誤 |
| `unknown_model` | `models.download` 的模型不在清單中（ASR id 或 `gpu-runtime`）；`models.delete` 的模型不是 registry 內的 ASR 模型（含 `gpu-runtime`） |
| `model_not_downloaded` | `models.delete`：模型沒有下載完成（只有續傳殘留也算沒下載）。也是 `session.start` 的 `status error` code |
| `model_in_use` | `models.delete`：模型正被使用——執行中或載入中的 session（含已停止、載入執行緒還沒結束的），或同一個 ASR 模型的下載正在進行 |
| `download_in_progress` | 同一個模型（或 GPU 執行環境、或同名的翻譯模型）已在下載；ASR 與翻譯模型的名稱分開計算 |
| `pull_unsupported` | `models.pull_translation`：翻譯伺服器不是 Ollama（沒有 `/api/version`），無法替使用者下載；請使用者在該服務自行安裝 |
| `translation_server_unreachable` | `models.pull_translation`：連不上翻譯伺服器 |
| `translation_server_auth_failed` | `models.pull_translation`：翻譯伺服器回 401／403（金鑰錯誤或缺少，多半是要求金鑰的反向代理），無法確認是不是 Ollama；請使用者檢查 `translation.api_key_env` 指向的環境變數（不是「不是 Ollama」，不要叫使用者自行安裝模型） |
| `license_not_accepted` | `models.download {"model": "gpu-runtime"}` 沒有帶 `"accept_license": true`；`message` 含 NVIDIA EULA 網址 `https://docs.nvidia.com/cuda/eula/index.html` |
| `gpu_runtime_unsupported` | 這個平台沒有 GPU 執行環境（只有 Windows／Linux x86-64；macOS、ARM 回這個） |
| `internal_error` | 未預期的例外（traceback 寫 stderr；訊息只有例外類別與簡短說明） |

- 空行略過。不合法的行只回錯誤，不會讓引擎結束。
- 錯誤訊息、`log` 事件與回應中的翻譯伺服器網址一律遮蔽帳密（`http://***@host/...`，`kikitori.translate.redact_url()`）。

### 方法

| 方法 | 參數 | 結果 |
|---|---|---|
| `engine.info` | — | `{"name": "kikitori-engine", "version", "protocol_version": 1, "platform": "windows"\|"macos"\|"linux"\|<其他>, "machine", "python_version", "fake": bool}` |
| `hardware.detect`（背景） | — | `{"hardware": HardwareInfo, "recommendation": ModelCombination}`，形狀見下方與 `hardware.md`；一律重新偵測 |
| `models.list`（背景） | — | `{"asr": [...], "gpu_runtime": {...}, "translation": {...}}`，見下方 |
| `models.download` | `{"model": <ASR id>}` 或 `{"model": "gpu-runtime", "accept_license": true}` | `{"model", "started": true}`；在背景下載，進度以 `download_progress` 事件回報 |
| `models.validate`（背景） | `{"settings"?: 部分設定物件}` | `{"ok": bool, "errors": [訊息], "warnings": [訊息]}`（訊息為 `{code, params, message}`，見 `hardware.md`「訊息碼」） |
| `models.pull_translation`（背景） | `{"model": 翻譯模型名稱}` | `{"model", "started": true}`；翻譯伺服器是 Ollama 時在背景下載，進度以 `download_progress`（`kind: "translation"`）回報 |
| `models.delete`（F7） | `{"model": <ASR id>}` | `{"model", "freed_bytes": int}`；刪除已下載的 ASR 模型（見下方） |
| `languages.list` | — | `{"languages": [{code, english_name, native_name, whisper_code, rtl, no_space, asr_quality}]}` |
| `devices.list` | — | `{"devices": [DeviceInfo]}` |
| `settings.get` | — | `{"settings": {...}, "path": 設定檔路徑, "is_new": bool}` |
| `settings.set` | 部分設定物件（深度合併） | 同 `settings.get` |
| `settings.apply_recommendation`（背景） | `{"only_if_new"?: bool}` | `{"settings", "path", "is_new", "applied": 寫入的部分設定 \| null, "recommendation": ModelCombination}` |
| `settings.preview_recommendation`（背景） | — | `{"patch": 套用會寫入的部分設定, "recommendation": ModelCombination, "differs": bool, "fingerprint": 16 個十六進位字元}`（不存檔） |
| `settings.preview_languages`（背景） | `{"source_language"?, "target_language"?, "partner_language"?, "mode"?}` | 這組語言的推薦組合、每個模型是否已安裝與下載大小（不存檔、不下載），見下方 |
| `session.start` | `{"mode": "media"\|"interpret", "device_id"?: str \| null}` | `{"session_id": int}` |
| `session.stop` | — | `{}`（沒有 session 也回 `{}`） |
| `session.set_translation` | `{"enabled": bool}` | `{"settings", "path", "is_new", "session_id": 套用到的 session id \| null}`；存進設定 `translation.enabled`，執行中的 session 立即切換 |
| `engine.shutdown` | — | `{}`，之後結束 |

- 會啟動背景工作的方法（`session.start`、`models.download`、`models.pull_translation`）在**回應寫出之後**才啟動背景執行緒，所以 UI 一定先拿到 `session_id`（或 `started: true`），才收到該 session（或該下載）的事件。`models.pull_translation` 是背景方法：背景工作執行緒寫出回應後才啟動下載執行緒（`Deferred.after`）。
- `settings.set` 在 session 執行中也可呼叫，新設定從下一個 session 生效。
- `session.start`：先同步驗證參數與 `open_source()`（只解析 id，不開裝置）。省略 `device_id` 時用設定檔該模式的 `device_id`；`null` 是預設裝置。回應後在背景：`status loading` → **翻譯模型檢查**（見下）→ 建立元件（載入模型可能要數秒；之後預熱翻譯模型，見 factory 的 `build_session_components()`）→ 開裝置 → `status running`（本機翻譯服務在執行中另有保溫，見 `attach_keep_warm()`）。任一步失敗送 `status error`，session 結束。media 用 `loopback`、interpret 用 `microphone`（指定 `device_id` 時以 id 為準，見 `capture.md`）。
- **背景方法**（表中標「背景」）：會執行 `nvidia-smi`、連線翻譯伺服器（最久數秒），所以不在讀 stdin 的主執行緒上處理，而是交給**一條**背景工作執行緒（`engine/deferred.py`），完成時才寫出回應。
  - 背景方法之間：依收到的順序執行與回應（不會同時跑兩次偵測）。
  - 與其他方法之間：以 `id` 對應，回應可能晚於之後送出的請求（例如 `hardware.detect` 進行中送 `session.stop`，`session.stop` 先回應）。
  - 參數驗證錯誤（`invalid_params`、`models.validate` 的 `invalid_settings`）在主執行緒上立即回應；設定在**收到請求時**取出，之後的 `settings.set` 不影響這次執行。
  - 偵測結果快取 10 秒（同一組 `translation.base_url`／`api_key_env`／`asr.cuda_library_dirs`）：`models.list`、`models.validate`、`settings.apply_recommendation` 重用，`hardware.detect` 一律重新偵測並更新快取；GPU 執行環境下載結束（成功或失敗）時清掉快取。
  - `engine.shutdown`／stdin EOF：尚未開始的背景請求直接丟棄（不回應；Tauri 端在管線關閉時讓等待中的請求失敗），進行中的最多等 2 秒。
- `hardware.detect`：`hardware` 為 `HardwareInfo.to_dict()`——`os`、`os_version`、`machine`、`cpu {model, physical_cores, logical_cores, architecture}`、`cpu_count`、`memory {total_bytes, available_bytes}`、`ram_bytes`、`gpus [{index, name, memory_total_mb, memory_used_mb, driver_version, compute_capability}]`、`apple_silicon`、`cublas {supported_platform, available, library_dirs, source}`、`translation_server {base_url, is_local, reachable, models, error}`、`errors`；`recommendation` 為 `{tier, fast_asr {model, device, compute_type, cpu_threads}, final_asr {…}, translation_fast, translation_final, translation_budget_gb, gpu_runtime_missing, reasons, warnings}`。推薦依設定的 `translation.allow_restricted`。偵測的單項失敗不會讓方法失敗（見 `errors`）。
- `models.list`：`hardware.models_overview()` 的結果——`asr [{id, size_bytes, downloaded, disk_bytes, license, speed_tier, no_speech_reliable, cpu_ram_mb, vram_mb, vram_int8_mb, recommended_for: ["fast"|"final"]}]`（`disk_bytes`（F7）：`int | null`，已下載模型的資料夾 `<快取>/<id>` 實際佔用的位元組數——含其他 revision 與 `.part` 殘留，也就是 `models.delete` 會釋放的量，所以可能與 `size_bytes` 不同；沒下載時 null）、`gpu_runtime {id: "gpu-runtime", supported, installed, package, version, path, download_bytes, installed_bytes, license, license_url, cublas_available, cublas_source}`、`translation {server {base_url, is_local, reachable, error}, models [{name, label, installed, known, recommended_for, license, license_url, commercial_use, excluded_regions, approx_vram_gb}]}`——另保留第一版的 `translation.base_url`、`translation.installed`（伺服器回報的名稱或 null）、`translation.error`、`translation.presets`（只增不改）。翻譯伺服器的模型清單來自 hardware 的 `probe_translation_server()`（timeout 2 秒、`localhost` 依序試 127.0.0.1／::1）。
- `models.download`：
  - ASR：背景 daemon 執行緒呼叫 factory 的 `download_asr_model()`；`accept_license` 不適用於 ASR 模型（帶了回 `invalid_params`）。
  - **GPU 執行環境**（`"model": "gpu-runtime"`）：NVIDIA cuBLAS 12 的 wheel（約 570 MB，從 PyPI 下載，NVIDIA CUDA Toolkit EULA）。UI 必須先顯示授權說明與連結（`models.list` 的 `gpu_runtime.license`／`license_url`），使用者同意後才送 `{"model": "gpu-runtime", "accept_license": true}`；檢查順序：`accept_license` 不是布林 → `invalid_params`；平台不支援 → `gpu_runtime_unsupported`（不論是否同意，因為不會下載任何 NVIDIA 檔案）；省略或 `false` → `license_not_accepted`（訊息含 EULA 網址）。背景執行 `hardware.install_gpu_runtime()`，`download_progress` 的 `model` 為 `"gpu-runtime"`、`total_bytes` 為 wheel 大小；下載完成後還要驗證與解壓（數秒，期間沒有進度事件）才送 `done`。已安裝時立即送 `done`。
  - `engine.shutdown`／stdin EOF 時以 `cancel` 事件取消下載（GPU 執行環境會送 `state: "error"`、`error` 含 `DownloadCancelledError`，`.part` 保留供續傳）。
  - 裝好 GPU 執行環境後，下一個 session 自動用到它（factory 每次建立元件都檢查）。但若這個引擎行程先前已經嘗試過 CUDA 而失敗（例如 `device=auto` 在沒有 cuBLAS 時跑過 session），asr 在同一行程內不再嘗試 CUDA——UI 應在安裝完成後重啟引擎（沒有 session 在跑時）。
- `models.validate`：設定頁「檢查組合」。`settings` 是要檢查的部分設定，與目前設定深度合併後（**不存檔**）轉成組合交給 `hardware.validate_combination()`（`downloaded_asr` 為已下載的 ASR 模型、`allow_restricted` 與語言（`mode`、`source_language`、`target_language`、`partner_language`）取自合併後的設定）；省略時檢查目前設定。合併後的設定不合法 → `invalid_settings`（與 `settings.set` 相同）；`settings` 不是物件 → `invalid_params`。`errors` 非空時 `ok=false`（例如選 cuda 但缺 cuBLAS、未知的 ASR 模型）；是否阻擋儲存由 UI 決定。`errors`／`warnings` 是結構化訊息 `{code, params, message}`（code 與參數見 `hardware.md`「訊息碼」；`message` 為英文備援）。
- `settings.apply_recommendation`：把目前硬體的推薦組合寫進設定（深度合併並存檔，等同 `settings.set`），回應帶實際寫入的 `applied` 與 `recommendation`。對應規則：`asr.fast_model／final_model` ← 推薦模型；`asr.device` ← 推薦的裝置（`cuda`／`cpu`，不用 `auto`：推薦 CPU（例如缺 cuBLAS）時若存 auto，asr 會先試 CUDA 而失敗，之後整個行程都不再試）；`asr.*_compute_type` ← 推薦精度；`asr.*_cpu_threads` ← 推薦值，0 存成 null；`translation.fast_model／final_model` ← 推薦模型（推薦為 null，即翻譯伺服器不在本機時，不改）；`cuda_library_dirs`、`allow_restricted` 與其他欄位不動。設定檔無法寫入 → `settings_io_error`。UI 也可以只顯示 `hardware.detect` 的推薦、讓使用者修改後用 `settings.set` 存，但「套用推薦」一律用這個方法，讓轉換規則只存在引擎裡。
  - `only_if_new: true`（第一次執行的自動套用）：只在 `is_new` 時寫入（`SettingsStore.update_if_new()`，檢查與寫入同一把鎖）；設定已存過（使用者在偵測期間存了組合、切換了模式…）時不寫入，回應的 `applied` 為 `null`，`settings` 是目前設定。不是布林 → `invalid_params`。
- `languages.list`：語言清單（`languages/catalog.py`，20 種，順序固定）。每項 `{code, english_name, native_name, whisper_code, rtl, no_space, asr_quality}`；`asr_quality` 是 ASR 模型 id → `"A"`／`"B"`／`"C"`（`hardware.md`「ASR 品質等級」；A ≤ 10%、B 10–20%、C > 20% 錯誤率）。立即回應；有參數 → `invalid_params`。UI 的語言選單只用這份清單，不自己維護。
- `settings.preview_languages`（Milestone 12，背景）：語言選單每次選擇時呼叫，回答「選這組語言會用哪些模型、要下載什麼」。**不寫設定、不下載。**
  - 參數全部選填：`source_language`、`target_language`、`partner_language`（語言清單的語言碼，接受別名，`source_language` 可為 `"auto"`）、`mode`（`media`／`interpret`，省略時用設定的 `mode`）。未知參數或 `mode` 不合法 → `invalid_params`；語言碼不合法、口譯時兩個語言是同一個 Whisper 語言 → `invalid_settings`（與 `settings.set` 相同的驗證與訊息）。
  - 結果：
    ```jsonc
    {"languages": {"mode": "media", "source_language": "ko", "target_language": "es", "partner_language": "ja"},  // 正規化後
     "recommendation": ModelCombination,          // 依這組語言的推薦（含訊息碼、translation_status、languages）
     "patch": {"asr": {...}, "translation": {...}},  // 套用推薦會寫入的模型設定（同 settings.preview_recommendation）
     "settings": {...},                           // 目前設定 + 這組語言 + patch 的完整設定（預覽用）
     "differs": true,                             // settings 與目前設定不同
     "fingerprint": "3f9c0d1e2a4b5c6d",           // 同 preview_recommendation（含語言）
     "models": {
       "asr": [{"model": "base", "roles": ["fast"], "installed": true, "size_bytes": 145…, "download_bytes": 0}, …],
       "translation": [{"model": "qwen3.5:9b", "roles": ["fast", "final"], "installed": false,  // 伺服器連不上時 null
                        "size_bytes": 6600000000, "size_approximate": true, "download_bytes": 6600000000}]},
     "translation_needed": true,                  // false：只顯示原文，或收聽／字幕是同一個 Whisper 語言
     "download_bytes": 6600000000,                // 需要下載的合計（已知大小的部分）
     "download_size_known": true,                 // 有任何需要的模型大小未知時 false
     "messages": [{"code": "translation_disabled", "params": {}, "message": "…"}]}  // 預覽本身的提示
    ```
  - ASR 的大小取自模型清單（registry 的檔案大小）；翻譯模型的大小是預設組的權重大小（`approx_vram_gb` × 10⁹，約略值，`size_approximate: true`），不認得的模型為 null。`translation_needed=false` 時 `models.translation` 為空、下載合計不含翻譯模型。`installed` 為 null（伺服器連不上）時 `download_bytes` 照列大小（可能要下載）。
  - UI 儲存時：「同時改用推薦的模型」勾選 → `settings.set({...languages, ...patch})`；取消勾選 → 只存 `languages` 的欄位。
  - 與其他背景方法相同，參數在收到請求時與當時的設定合併；`differs` 比對執行當下的設定。重用 10 秒的偵測快取。
- `models.pull_translation`（Milestone 13，背景）：替使用者在 Ollama 下載翻譯模型（`hardware.md`「Ollama 下載翻譯模型」）。
  - `model`：1–200 字，只允許英數與 `._:/-`、第一個字元英數、不含 `..`，整個字串都要符合（結尾換行也不行；`validate_pull_model_name()`）（不在 `models.list` 的模型也可以，例如使用者自訂的模型）；不合法或有其他參數 → `invalid_params`（主執行緒立即回應）。
  - 背景工作：先檢查翻譯伺服器（`GET /v1/models` 與 Ollama 的 `GET /api/version`，各 2 秒逾時）。連不上 → `translation_server_unreachable`；401／403 → `translation_server_auth_failed`；不是 Ollama（含轉址）→ `pull_unsupported`（`message` 附上原因，例如 `HTTP 302 redirect from /api/version to …`）；同一個翻譯模型已在下載 → `download_in_progress`。否則回 `{"model", "started": true}`，寫出回應後才啟動下載執行緒。回應沒能寫出（stdout 關閉、結果無法序列化）時撤銷這次登記（`Deferred.unsent`），之後同一個模型不會永遠是 `download_in_progress`；`models.download` 的同步路徑同理。
  - 進度：`download_progress {"model": 名稱, "kind": "translation", "downloaded_bytes", "total_bytes", "state", "error"}`。`total_bytes` 是目前已知各層的合計，**下載途中可能變大**（Ollama 逐層回報）；第一筆是 `0／0`。`done` 時兩者相等。失敗（Ollama 回報的錯誤、送出請求逾時、120 秒沒有進度、斷線、401／403、轉址）→ `state: "error"`，`error` 為英文 `"<例外類別>: <訊息>"`（Ollama 的錯誤訊息原文，例如 `PullError: Ollama: pull model manifest: file does not exist`）。`engine.shutdown`／stdin EOF 時取消（`error` 含 `PullCancelledError`）。
  - 結束（成功或失敗）時清掉偵測快取：之後的 `models.list`、推薦、預覽看得到新模型。
- `models.delete`（F7）：刪除已下載的 ASR 模型（設定頁「刪除」，確認對話由 UI 負責）。翻譯模型（Ollama）不代刪。
  - **在主執行緒上同步執行**（不是背景方法）：`session.start`、`models.download` 也在主執行緒上登記，所以「使用中」的檢查與刪除之間不會有新的
    session 或下載開始。刪除只是幾個檔案，很快。
  - 檢查順序：參數（`model` 必須是 1–100 字元的字串、沒有其他參數，否則 `invalid_params`）→ 不在 ASR registry（含 `"gpu-runtime"`）→ `unknown_model`
    → 使用中 → `model_in_use`（訊息列出使用者，例如 `session 3`、`a download`）→ 沒有下載完成 → `model_not_downloaded`。
    「使用中」= 目前的 session（載入中或執行中）的 `asr.fast_model`／`final_model`、已停止但載入執行緒還在跑的 session（它還在讀模型檔）、
    元件尚未釋放完的 session（含已取走元件、`close()` 還在進行的），或 `models.download` 正在下載同一個 ASR 模型（下載中、尚未完成的模型也回 `model_in_use`，先於 `model_not_downloaded`）。
    session 用到的模型以 `session.start` 當時的設定為準。
  - 刪除（factory 的 `delete_asr_model()` → asr 的 `delete_model()`，見 `asr.md`「磁碟用量與刪除」）：刪除前後都丟掉模型池中該模型的閒置物件，刪整個
    `<快取>/<id>`（所有 revision、`.part` 殘留）；只在模型快取內刪：路徑解析後必須就在快取資料夾正下方，`<快取>/<id>` 或樹裡任何地方是
    symlink／junction 就整個拒絕（什麼都不刪）。路徑不安全或刪除失敗（檔案被開著）→ `internal_error`（訊息為例外類別與說明；沒有另設錯誤碼，
    協定只約定上面三個 code）。刪除中途失敗時先刪掉的是完成標記，模型之後就是「沒下載」，可重新 `models.download`。
  - 結果 `freed_bytes` = 刪掉的普通檔案大小合計（與刪除前的 `disk_bytes` 相同）。之後 `models.list` 的 `downloaded` 為 false、`disk_bytes` 為 null；
    用到它的 `session.start` 回 `status error model_not_downloaded`。
- `session.start` 的**翻譯模型檢查**（Milestone 13）：載入 ASR **之前**，若這個 session 需要翻譯（`translation.enabled=true`，且不是「影片翻譯、收聽與字幕同一個 Whisper 語言」），引擎檢查翻譯伺服器（同上，最多數秒；仍在 `loading`）。伺服器是**連得上的 Ollama** 而設定的 `translation.fast_model`／`final_model` 有任一個沒裝 → `status error`，`code: "translation_model_missing"`，`data: {"models": [缺少的模型名稱]}`（依 fast、final 順序、去重）。UI 對每個模型呼叫 `models.pull_translation`，全部 `done` 後再 `session.start`。
  - 連不上伺服器、不是 Ollama、金鑰被拒（401／403）、或拿不到模型清單時**照常開始**，不檢查：字幕的原文不依賴翻譯；使用者可能正要開 Ollama、或用其他服務（只能自行安裝模型）；這時沿用既有行為——每句翻譯失敗只顯示原文，並以 `log` warning 提醒一次（連續相同的失敗只提醒一次）。檢查本身的程式錯誤只記 log，也照常開始。**金鑰被拒**（`TranslationServerState.auth_failed`）不是「Ollama 還沒開」那種會自己好的狀況，所以另記一則 `log` warning（`the translation server rejected the API key (HTTP 401/403); …`），請使用者檢查 `translation.api_key_env`；不另送 status code（status 的 code 只用在 `error` 狀態，session 本身仍正常開始）。
  - **停止與載入**：每一步之間檢查 `session.stop`——等前一個 session（見下）、翻譯模型檢查之後、建立元件（ASR 與預熱）之後。檢查翻譯伺服器、載入 ASR 與預熱本身不能中斷，停止後做完這一步就結束，不再進行下一步（檢查期間被停止就不載入 ASR）。
  - **不會有兩個 session 同時載入**：被停止的 session 立即送出 `idle` 並釋放，UI 可以馬上 `session.start`；但它的載入執行緒可能還在 `build_components()` 裡（數秒到數十秒）。新的 session 先送 `loading`、記一則 info log（`session N: waiting for session M to finish loading`），等前一個載入執行緒結束後才開始自己的檢查與載入；等待期間也能被停止。理由：兩份 ASR 同時載入會在 GPU 上配置兩份模型（每份約 2.6 GB），預熱也會與 Ollama 搶顯示記憶體。代價是快速「停止→開始」時新的 session 要等前一個做完它目前那一步（最久是預熱的 60 秒讀取逾時）。
  - 只顯示原文（`translation.enabled=false`）時完全不連翻譯伺服器、不檢查模型、不預熱、不保溫（Milestone 14）。
- `session.set_translation`（Milestone 14）：「顯示翻譯」開關。`enabled` 必須是布林（否則 `invalid_params`）。先存進設定 `translation.enabled`（無法寫入 → `settings_io_error`，此時不切換）；有 session 時立即套用到它的 pipeline（`Pipeline.set_translation_enabled`）：之後的句子依新狀態處理，已顯示的句子不補翻。session 還在 `loading` 時記下來，pipeline 建好、開始之前套用。回應是 `settings.get` 的結果加 `session_id`（套用到的 session；沒有 session 時 null）。
  - 執行中**開啟**翻譯：該 session 開始時沒有預熱（只顯示原文時不預熱），所以本機翻譯服務會在背景先預熱一次、之後照常保溫（不阻塞回應）。`translation.keep_warm_secs = 0` 時沒有定期保溫，但開啟時的這一次預熱照樣進行（`keep_warm.WarmUpOnEnable`）。翻譯伺服器不可用時沿用「翻譯失敗只顯示原文＋提醒一次」；不做 `translation_model_missing` 檢查（session 已在執行）。
  - 執行中**關閉**翻譯：不再呼叫翻譯伺服器，也不再送保溫請求（不佔 GPU）。
- `settings.get`／`settings.set`／`settings.apply_recommendation` 的 `is_new`：`SettingsStore.is_new`（見「讀寫」）。UI 以它判斷第一次執行、自動套用推薦。
- **推薦依語言**（Milestone 12）：`hardware.detect`、`models.list`、`settings.apply_recommendation`、`settings.preview_recommendation` 的推薦都依目前設定的語言與 `mode`（`hardware.md`「語言」）。`ModelCombination` 的 `reasons`／`warnings` 是訊息物件，另有 `translation_status`（`recommended`／`remote`／`not_needed`／`unsupported`）與 `languages`（`{mode, source_language, target_language, partner_language}`）。`translation_status` 為 `not_needed`／`unsupported` 時 `translation_fast/final` 為 null，`settings.apply_recommendation` 不改翻譯模型。
- `settings.preview_recommendation`：`settings.apply_recommendation`「會」寫入什麼，不存檔。`patch` 與 `applied` 出自同一個 `recommendation_patch()`；`differs` = 合併 `patch` 後的設定 ≠ 目前設定（只有推薦涵蓋的欄位會影響）；`fingerprint` = `{"patch": patch, "languages": 推薦依據的語言}` 的正規化 JSON（鍵排序）的 SHA-256 前 16 個十六進位字元，推薦或語言改變時跟著改變，UI 以它記住「不要再提醒」（Milestone 12 起包含語言，升級後舊的「不要再提醒」會再提醒一次）。與其他背景方法不同，設定在**執行時**才讀取（預覽可能排在其他背景請求之後，`differs` 要對照 UI 收到回應時的設定）。重用 10 秒的偵測快取。

### 事件

| 事件 | 內容 |
|---|---|
| `subtitle` | `SubtitleUpdate` 的全部欄位 + `session_id`；即時字幕（`is_final=false`）另有 `stable_chars: int`、**沒有** `latency_ms`；完整字幕另有 `latency_ms: int \| null`、**沒有** `stable_chars`（F2／F4） |
| `metrics`（F4） | `{"session_id": int, "level_dbfs": float, "backlog": int, "latency_ms_median": int \| null}`，session 執行中約每秒一則（見下方） |
| `status` | `{"state": "idle"\|"loading"\|"running"\|"error", "detail": 字串 \| null, "session_id": int \| null, "code": 字串 \| null, "data"?: 物件}`（`data` 只在有內容時出現，目前只有 `translation_model_missing`） |
| `download_progress` | `{"model", "kind": "asr"\|"gpu_runtime"\|"translation", "downloaded_bytes", "total_bytes", "state": "downloading"\|"done"\|"error", "error": 字串 \| null}` |
| `log` | `{"level": "debug"\|"info"\|"warning"\|"error", "message"}` |

- 引擎啟動完成後先送一筆 `status idle`。
- `metrics`（F4）：pipeline 建好、`pipeline.start()` 之前啟動一條 daemon 執行緒（`session-<id>-metrics`），每 `METRICS_INTERVAL = 1.0` 秒讀一次
  `Pipeline.metrics()`，pipeline 為 `running` 時送出（欄位意義見「量測」）。session 送出結束狀態（`idle`／`error`，含停止、讀完、執行中錯誤、
  開裝置失敗、引擎結束）**之前**停止並 join（送出與停止共用一把鎖），所以結束狀態之後不會再有這個 session 的 `metrics`。例外：送出卡住（stdout
  管線塞滿）超過 `METRICS_JOIN_TIMEOUT = 1.0` 秒時記 warning 後不再等，那一則可能落在結束狀態之後（UI 依 `session_id` 忽略）；pipeline 已結束時執行緒也會
  自己結束（不會遺留）。載入中（`loading`）不送。`latency_ms_median` 為 null 表示還沒有完整字幕。`EngineServer(metrics_interval=…)` 可改間隔（測試用）。
- `status error` 的 `code`：`model_not_downloaded`、`unknown_model`、`translation_model_missing`（`data: {"models": [...]}`，見 `session.start`）、`device_not_found`、`permission_denied`、`device_lost`、`capture_unavailable`、`capture_error`、`pipeline_error`、`load_error`。session 執行中的錯誤依 `Pipeline.error`：擷取錯誤（`CaptureError` 子類別，例如錄音中拔掉 USB 裝置的 `DeviceLostError`）用與 `session.start` 相同的擷取 code，其他是 `pipeline_error`。明確指定 `asr.device="cuda"` 而同一行程 CUDA 曾失敗時，asr 丟出的 `RuntimeError` 訊息（要求重啟引擎）原樣放在 `load_error` 的 `detail`。
- `download_progress` 節流：每 0.25 秒或每 1% 最多一筆；開始（0）與結束（`done`／`error`）一定送。`kind`（Milestone 13 新增）分辨同名的 ASR 模型與翻譯模型：`asr`（`models.download` 的 ASR 模型）、`gpu_runtime`（`model` 為 `"gpu-runtime"`）、`translation`（`models.pull_translation`）。
- `log`：`kikitori` logger 的 INFO 以上同時寫 stderr 與 `log` 事件（訊息由各模組產生，翻譯模組已遮蔽金鑰；引擎自己不記錄設定中的環境變數值）。
- `subtitle`／`status` 帶 `session_id`，UI 可丟掉舊 session 的遲到事件。`state`、`code`、`session_id`、`error` 是相對架構規格的**新增欄位**（只增不改）。

### 結束

- `engine.shutdown`：先回 `{}`，再停 session（`stop(timeout=5)`）、取消下載，結束碼 0。
- stdin EOF：同樣清理後結束碼 0。

### 可注入的服務（測試與之後的里程碑）

`EngineServices`：`settings_store`、`list_devices`、`open_source`、`build_components(settings, mode)`、`hardware`（`HardwareProvider`：`detect(settings) -> HardwareInfo`、`models_overview(info, rec)`、`gpu_runtime_status()`、`install_gpu_runtime(progress, cancel)`）、`models`（`ModelCatalog`：`asr_models()`（`AsrModelInfo` 含 `disk_bytes`）、`download(model_id, progress, cancel)`、`delete(model_id) -> 釋放的位元組數`（F7））、`release_models`（選填：引擎結束時釋放 ASR 模型池，預設實作 `factory.release_asr_models()`）、`translation_models`（`TranslationModels`：`server_state(settings) -> TranslationServerState{reachable, is_ollama, installed, error, auth_failed}`、`pull(settings, model, progress, cancel)`；預設實作 `DefaultTranslationModels` 呼叫 `hardware.translation_server` 與 `hardware.ollama`）。

- 預設實作：真實 capture、`pipeline/factory.py`、`DefaultHardwareProvider`（直接呼叫 `kikitori.hardware`）。推薦與組合驗證是純函式，由 `engine/hardware_service.py` 直接呼叫，不經 provider。
- **假模式**：環境變數 `KIKITORI_ENGINE_FAKE=1` 時改用 `kikitori/engine/testing.py`：合成音訊（`fake:loopback:speech` 兩段「人聲」後結束、`fake:microphone:endless` 只有靜音不會結束）、依振幅判斷的 `VoiceModel`、回固定文字的 ASR 與翻譯、會回報進度的假下載。F2／F4／F7：假 ASR 的即時結果是 `partial1`、`partial2`…（同一句的 `stable_chars` 因此是 7）；`metrics` 由真的 pipeline 計算（`fake:microphone:endless` 是 −120.0 dBFS、backlog 0）；`FakeModelCatalog` 已下載的模型 `disk_bytes` = 4 MiB、`delete()` 把模型標成未下載並回傳 4 MiB（不碰磁碟），之後 `models.list` 與 `session.start`（`model_not_downloaded`）都看得到，`models.download` 可再「下載」回來。`engine.info` 的 `fake` 為 true，啟動時記 warning。只給測試與 UI 開發用。
- **假翻譯伺服器**（`FakeTranslationServer`，同時是 `translation_models`，並提供假硬體偵測結果的 `translation_server`）：已安裝 `qwen3.5:4b`；`pull` 分四段回報 4 MiB 後把模型加入已安裝清單（`models.list`、推薦、`session.start` 的檢查都看得到）。`fake-broken-pull:1b` 在一半時失敗、`fake-slow-pull:1b` 每段等 0.5 秒（測取消）。環境變數 `KIKITORI_ENGINE_FAKE_TRANSLATION` 選情境：`ollama`（預設）、`openai`（可連但不是 Ollama：`pull_unsupported`、`session.start` 不檢查）、`offline`（連不上：`translation_server_unreachable`、`session.start` 照常開始）、`auth`（回 401：`translation_server_auth_failed`、`session.start` 照常開始並記 warning）。
- **假硬體**（`FakeHardwareProvider`）：偵測結果固定，推薦／驗證／總覽走真的 hardware 程式碼；GPU 執行環境以真的 `install_gpu_runtime()` 安裝假 wheel（記憶體中的小 zip、假 fetcher，不連網）到行程專屬的暫存目錄，所以安裝後 cuBLAS 變成可用、推薦改為 GPU 等級。環境變數 `KIKITORI_ENGINE_FAKE_HARDWARE` 選情境：`nvidia`（預設：8 GB GPU、缺 cuBLAS、本機翻譯伺服器裝了 qwen3.5:4b）、`apple`（Apple Silicon，`gpu-runtime` 不支援）、`slow-runtime`（下載慢，測取消）、`broken-runtime`（下載內容雜湊不符）。

### 協定變更（Milestone 6）

給 UI（`app/`）的變更清單；`protocol_version` 維持 1——全部是新增，既有欄位的意義沒有改變，唯一的例外是設定檔 schema（第 8 點）。

1. **`hardware.detect`**：改由 hardware 模組偵測；`recommendation` 不再是 null，而是 `ModelCombination`（`tier`、`fast_asr`／`final_asr` 物件、`translation_fast`／`final`、`translation_budget_gb`、`gpu_runtime_missing`、`reasons`、`warnings`）。`hardware` 新增 `cpu`、`memory`、`cublas`、`translation_server`、`errors`；`gpus[]` 的項目是 `{index, name, memory_total_mb, memory_used_mb, driver_version, compute_capability}`（第一版的 `gpus` 一律是空陣列，沒有約定過項目形狀；UI 目前解析的 `vram_bytes` 不存在，請改讀 `memory_total_mb`）。回應改在背景執行，可能晚於之後送出的其他請求（以 `id` 對應）。
2. **`models.list`**：新增 `gpu_runtime` 物件、`translation.server`、`translation.models`；`asr[]` 新增 `no_speech_reliable`、`cpu_ram_mb`、`vram_mb`、`vram_int8_mb`、`recommended_for`。第一版的 `translation.base_url／installed／error／presets` 保留。背景執行。
3. **`models.download`**：新增 `{"model": "gpu-runtime", "accept_license": true}`；進度事件 `download_progress` 的 `model` 為 `"gpu-runtime"`。ASR 模型帶 `accept_license` 回 `invalid_params`。
4. **新方法 `models.validate`**：`{"settings"?: 部分設定}` → `{"ok", "errors", "warnings"}`（不存檔）。背景執行。
5. **新方法 `settings.apply_recommendation`**：無參數 → `{"settings", "path", "applied", "recommendation"}`（會存檔）。背景執行。
6. **新錯誤碼**：`license_not_accepted`（訊息含 `https://docs.nvidia.com/cuda/eula/index.html`）、`gpu_runtime_unsupported`。`unknown_model` 的訊息改為列出 ASR 模型與 `gpu-runtime`。
7. **建議 UI 流程**：設定頁開啟 → `hardware.detect`（顯示推薦與 `warnings`；`gpu_runtime_missing=true` 時顯示「下載 GPU 執行環境」按鈕與授權說明）→ 使用者同意 → `models.download {"model":"gpu-runtime","accept_license":true}` → `done` 後（沒有 session 在跑時）重啟引擎 → `hardware.detect` → 「套用推薦」呼叫 `settings.apply_recommendation`；手動修改時先 `models.validate {"settings": 表單}` 顯示錯誤／警告，再 `settings.set`。在設定頁提供「我不在歐盟／英國／南韓」開關對應 `translation.allow_restricted`。
8. **設定 schema 2**：`asr.compute_type` 移除，改為 `asr.fast_compute_type`／`asr.final_compute_type`；新增 `asr.fast_cpu_threads`／`asr.final_cpu_threads`（null＝自動）與 `translation.allow_restricted`（預設 false）。舊設定檔自動遷移；`settings.set` 送 `asr.compute_type` 會回 `invalid_settings`。`settings.get` 的 `schema_version` 為 2。

### 協定變更（自動推薦，2026-11-05）

全部是新增，`protocol_version` 維持 1、schema 維持 2：

1. `settings.get`／`settings.set`／`settings.apply_recommendation` 的結果新增 `"is_new": bool`。
2. `settings.apply_recommendation` 新增參數 `{"only_if_new": true}`；此時 `applied` 可能是 `null`。
3. 新方法 `settings.preview_recommendation`（背景）→ `{"patch", "recommendation", "differs", "fingerprint"}`。
4. 設定新增頂層 `ui` 物件（UI 原樣保存）。

```json
{"id": 1, "method": "settings.get"}
{"id": 1, "result": {"settings": {"schema_version": 2, "...": "...", "subtitle": {}, "ui": {}}, "path": "C:/Users/me/AppData/Roaming/kikitori/settings.json", "is_new": true}}
{"id": 2, "method": "settings.apply_recommendation", "params": {"only_if_new": true}}
{"id": 2, "result": {"settings": {"...": "..."}, "path": "...", "is_new": false, "applied": {"asr": {"fast_model": "small", "final_model": "large-v3-turbo", "device": "cuda", "fast_compute_type": "float16", "final_compute_type": "float16", "fast_cpu_threads": null, "final_cpu_threads": null}, "translation": {"fast_model": "qwen3.5:9b", "final_model": "qwen3.5:9b"}}, "recommendation": {"tier": "nvidia_8gb", "...": "..."}}}
{"id": 3, "method": "settings.preview_recommendation"}
{"id": 3, "result": {"patch": {"asr": {"...": "..."}, "translation": {"...": "..."}}, "recommendation": {"tier": "nvidia_8gb", "...": "..."}, "differs": false, "fingerprint": "3f9c0d1e2a4b5c6d"}}
{"id": 4, "method": "settings.set", "params": {"ui": {"dismissed_recommendations": ["3f9c0d1e2a4b5c6d"]}}}
```

UI 流程見 `ui.md`「自動推薦」。

### 協定變更（多國語言核心，Milestone 10）

`protocol_version` 維持 1；**設定 schema 升為 3**（見「設定檔」）：

1. 設定新增 `source_language`（預設 `"ja"`，可為 `"auto"`）與 `translation.enabled`（預設 true）；`target_language`、`partner_language` 可為語言清單內任一語言（口譯模式下 `partner_language` 的 Whisper 語言不能和 `target_language` 相同）。`settings.set` 的 patch 帶 `schema_version` 時必須是 3。
2. `subtitle` 事件的 `target_language` 可能是清單內任一語言；只顯示原文時 `translation` 為空字串。
3. 已在 Milestone 12–14 實作：`languages.list`、`settings.preview_languages`、`models.pull_translation`、`session.set_translation`、推薦依語言（見下一節）。

### 協定變更（Milestone 12–14）

`protocol_version` 維持 1；設定 schema 維持 3。給 UI（`app/`、`app/dev/fake_engine.py`）的完整清單：

1. **訊息碼（不相容的形狀變更）**：`hardware.detect`／`settings.apply_recommendation`／`settings.preview_recommendation`／`settings.preview_languages`
   的 `recommendation.reasons`／`warnings`，與 `models.validate` 的 `errors`／`warnings`，從字串陣列改為訊息物件陣列
   `[{"code": str, "params": {str: str|int|float}, "message": 英文}]`。完整的 code、參數與英文範本見 `hardware.md`「訊息碼」（UI 依那張表建字典；
   不認得的 code 顯示 `message`）。翻譯預設組的 `label` 不再帶「（非商用）」，改由 UI 依 `commercial_use` 顯示。
2. **推薦依語言**：上述推薦都依設定的 `mode` 與語言；`recommendation` 新增 `translation_status`（`recommended`／`remote`／`not_needed`／`unsupported`）與
   `languages`（物件或 null）。`fingerprint` 改為包含語言（升級後舊的「不要再提醒」會再提醒一次）。
3. **新方法** `languages.list`（立即）、`settings.preview_languages`（背景）、`models.pull_translation`（背景）、`session.set_translation`（立即）。
4. **新錯誤碼**：`pull_unsupported`、`translation_server_unreachable`、`translation_server_auth_failed`（`models.pull_translation`；後者是 2026-11-05 review 後新增：401／403 不再被當成「不是 Ollama」）；`download_in_progress` 也用於同名翻譯模型。
5. **`status` 事件**：新 code `translation_model_missing`，並新增選填欄位 `data`（此 code 時為 `{"models": [...]}`；其他 status 沒有 `data`）。
6. **`download_progress` 事件**：新增 `kind`（`asr`／`gpu_runtime`／`translation`），既有欄位不變；翻譯模型的 `total_bytes` 可能在下載中變大。
7. **只顯示原文**：`translation.enabled=false` 時 `session.start` 不連翻譯伺服器；`session.set_translation` 執行中切換並存檔。
8. **建議 UI 流程**：語言選單 → `settings.preview_languages`（顯示推薦與「已安裝／第一次使用時下載約 X GB」，`translation_status=unsupported` 時說明原因）→
   儲存 `settings.set`；開始字幕 → `session.start` → 收到 `status error` 的 `model_not_downloaded`（`models.download`）或 `translation_model_missing`
   （對 `data.models` 逐一 `models.pull_translation`；`pull_unsupported` 時請使用者自行安裝；`translation_server_auth_failed` 時請使用者檢查金鑰的環境變數）→ 全部 `done` 後自動重試 `session.start`；
   「顯示翻譯」開關 → `session.set_translation`。

```json
{"id": 7, "method": "settings.preview_languages", "params": {"source_language": "ko", "target_language": "es"}}
{"id": 8, "method": "session.start", "params": {"mode": "media"}}
{"id": 8, "result": {"session_id": 3}}
{"event": "status", "data": {"state": "loading", "detail": null, "session_id": 3, "code": null}}
{"event": "status", "data": {"state": "error", "detail": "translation model(s) not installed on the Ollama server: qwen3.5:9b; download them with models.pull_translation", "session_id": 3, "code": "translation_model_missing", "data": {"models": ["qwen3.5:9b"]}}}
{"id": 9, "method": "models.pull_translation", "params": {"model": "qwen3.5:9b"}}
{"id": 9, "result": {"model": "qwen3.5:9b", "started": true}}
{"event": "download_progress", "data": {"model": "qwen3.5:9b", "kind": "translation", "downloaded_bytes": 0, "total_bytes": 0, "state": "downloading", "error": null}}
{"event": "download_progress", "data": {"model": "qwen3.5:9b", "kind": "translation", "downloaded_bytes": 6591…, "total_bytes": 6591…, "state": "done", "error": null}}
{"id": 10, "method": "session.set_translation", "params": {"enabled": false}}
{"id": 10, "result": {"settings": {"...": "..."}, "path": "...", "is_new": false, "session_id": 4}}
```

### 協定變更（F2／F4／F7，2026-11-06）

`protocol_version` 維持 1；設定 schema 維持 3；全部是新增欄位與新方法、新事件（給 UI 的完整清單，規格見 `competitive-features.md`）：

1. **`subtitle`**：即時字幕（`is_final=false`）新增 `stable_chars: int`（原文開頭幾個字元已穩定，見「穩定前綴」）；完整字幕新增 `latency_ms: int | null`
   （VAD 判定句子結束 → 送出完整字幕的毫秒數，見「量測」）。各自只出現在一種字幕上。
2. **即時翻譯的請求變少**：穩定前綴沒有變長時不重送，即時字幕的 `translation` 沿用這句上一次的即時譯文（見「穩定前綴」）；完整字幕不變。
3. **新事件 `metrics`**：`{"session_id", "level_dbfs", "backlog", "latency_ms_median"}`，執行中約每秒一則，結束狀態之後不再送。
4. **`models.list`**：`asr[]` 新增 `disk_bytes: int | null`。
5. **新方法 `models.delete {"model"}`** → `{"model", "freed_bytes"}`；新錯誤碼 `model_in_use`，`model_not_downloaded` 也用於這個方法。

```json
{"event": "subtitle", "data": {"utterance_id": 4, "original": "今日はいい天", "translation": "今天天氣", "source_language": "ja", "target_language": "zh-TW", "is_final": false, "start_sample": 51200, "end_sample": 80000, "stable_chars": 5, "session_id": 2}}
{"event": "subtitle", "data": {"utterance_id": 4, "original": "今日はいい天気ですね", "translation": "今天天氣真好呢", "source_language": "ja", "target_language": "zh-TW", "is_final": true, "start_sample": 51200, "end_sample": 92800, "latency_ms": 840, "session_id": 2}}
{"event": "metrics", "data": {"session_id": 2, "level_dbfs": -23.4, "backlog": 0, "latency_ms_median": 910}}
{"id": 12, "method": "models.delete", "params": {"model": "medium"}}
{"id": 12, "result": {"model": "medium", "freed_bytes": 1527906378}}
{"id": 13, "method": "models.delete", "params": {"model": "small"}}
{"id": 13, "error": {"code": "model_in_use", "message": "ASR model small is in use by session 2; stop it first"}}
```

偏離：無（欄位名稱與形狀照 F2／F4／F7 的約定）。補充的決定：`stable_chars` 以 Python 字元數計；`backlog` 不含正在辨識的那一句；
`latency_ms_median` 不含被濾掉的「清除」結果；`models.delete` 的錯誤檢查順序是 `invalid_params` → `unknown_model` → `model_in_use` → `model_not_downloaded`，
路徑不安全或刪除失敗走 `internal_error`。

## 入口

- `kikitori engine`：啟動 stdio 引擎（給 Tauri；開發時 `uv run kikitori engine`）。`python -m kikitori engine` 相同。
- `kikitori`（沒有參數）：互動選單（`input()` 編號選單）——1 啟動引擎（stdio）、2 列出音訊裝置、3 顯示設定檔路徑、4 離開。
- 其他參數：用法說明寫 stderr，結束碼 2。

## ASR 接縫（`pipeline/factory.py`）

只有這個檔案匯入 `kikitori.asr`（除了 `asr.types`），而且在函式內延遲匯入：

- `create_transcriber(model_id, *, device, role, candidate_languages, forced_language=None, compute_type, cpu_threads, cuda_library_dirs) -> Transcriber`：`load_transcriber(spec, device=..., compute_type=..., cpu_threads=..., options=FAST_DECODE/FINAL_DECODE + candidate_languages + forced_language, cuda_library_dirs=..., download=False)`；模型未下載 → `FileNotFoundError`（引擎轉成 `model_not_downloaded`）。
- `asr_languages(settings, mode) -> AsrLanguages(forced_language, candidate_languages)`：見「語言規則」。
- `translation_needed(settings, mode)`：這個 session 會不會用到翻譯模型（關閉翻譯、或影片翻譯的收聽與字幕同一個 Whisper 語言時 False）；`session.start` 依它決定要不要檢查翻譯伺服器上的模型。
- `warm_up_request(settings, mode)`：預熱／保溫的請求，方向與 session 相同（口譯：對方語言 → 我的語言；影片：收聽語言 → 字幕語言，`auto` 時取完整路徑模型支援的第一個語言），短句取自 19 個 Whisper 語言各一句的表。關閉翻譯或收聽與字幕語言同一個 Whisper 語言時為 None：`warm_up_translation()` 不預熱、`attach_keep_warm()` 不加保溫。
- `asr_cuda_library_dirs(settings)`：`asr.cuda_library_dirs` 在前，再加上 `hardware.installed_runtime_dir()`（已安裝時、不重複）；檢查失敗只記 warning。
- `resolve_cpu_threads(value, device)`：設定值；null 時 `device="cuda"` 回 0，否則 `hardware.auto_cpu_threads()`。
- `no_speech_reliable(model_id)`：registry 的 `no_speech_reliable`，設定 `PipelineComponents.final_no_speech_reliable`。
- `create_transcript_filter(no_speech_threshold)`：asr 的 `rejection_reason` + `FilterConfig`。
- `asr_model_catalog()`、`download_asr_model(model_id, progress, cancel)`：asr 的 `MODELS`、`model_disk_bytes`（`disk_bytes`，同時決定 `downloaded`）、`download_model`、`default_cache_dir`。
- `delete_asr_model(model_id, cache_dir=None) -> int`（F7）：`MODEL_POOL.evict_under(<快取>/<id>)` 後 `delete_model()`，刪除後（成功或失敗）再 `evict_under` 一次（刪除期間結束的 session 可能剛把物件放回池中）；未知 → `UnknownModelError`、沒下載 → `FileNotFoundError`。
- `release_asr_models()`：引擎結束時 `MODEL_POOL.clear()`（`EngineServices.release_models`）。
- `build_session_components(settings, mode)`：engine 實際用的建立函式。先 `build_components()` 載入 ASR，再呼叫 `warm_up_translation()`，兩者都完成才回傳（session 維持 `loading`），最後 `attach_keep_warm()`。刻意不並行：兩個行程同時在 GPU 上配置記憶體時，Ollama 估算的可用顯示記憶體會過時（驗收時 llama-server 以 CUDA 錯誤當掉）。`warm_up_translation()` 只對 loopback 位址的翻譯服務送一句短翻譯（完整路徑的模型先、即時路徑的模型若不同再送；`read_timeout` 60 秒、不重試），讓 Ollama 先把模型載入——它閒置 5 分鐘就卸載模型，冷啟動 4–10 秒，不預熱時第一句的即時翻譯會逾時、前幾句的完整字幕延後 8–16 秒（2026-11-05 端到端驗收實測）。失敗只記 warning。
- `attach_keep_warm(components, settings, mode)`：**執行中的保溫**。開場預熱之後，影片長時間沒有對白時翻譯請求會停下來，Ollama 閒置 5 分鐘仍會卸載模型，下一句的即時翻譯又會逾時；OpenAI 相容端點不能指定 `keep_alive`，所以由引擎定期送請求。翻譯服務在 loopback 位址且 `translation.keep_warm_secs > 0` 時，兩個翻譯器包成 `keep_warm.ActivityTranslator`（每次 `translate`／`stream` 的開始與結束回報給 `KeepWarm.begin()`／`end()`；有請求進行中時不算閒置，所以不會和比間隔還久的翻譯並行送保溫），並在 `PipelineComponents.keep_warm` 放一個 `keep_warm.KeepWarm`：背景執行緒（daemon）在閒置滿 `keep_warm_secs` 秒時呼叫 `ping_translation_models()`——與開場預熱相同的請求（`read_timeout` 60 秒、不重試、`max_tokens` 32）——請求本身也算活動，所以安靜期間每 `keep_warm_secs` 秒一次。`Pipeline.start()` 開好來源後、啟動 worker 之前啟動（來源一下就讀完時 capture 執行緒可能在 `start()` 返回前就結束 pipeline，之後才啟動就沒人停止它）、pipeline 以任何方式結束（`stop()`、來源讀完、錯誤）時停止；`stop()` 不等進行中的請求（最多是它的讀取逾時，之後不再送）。失敗（`TranslationError`）記 warning、其他例外（程式錯誤）記 error 與 traceback，兩者共用限流：30 分鐘內重複的失敗只記 debug，恢復時記 info；都不影響 session。每次送出記一行 info（`translation idle for … s; sending a keep-warm request`）。遠端服務時原樣回傳，不包裝也不送請求。設為 0 時翻譯器不包裝、不定期送請求，`keep_warm` 換成 `keep_warm.WarmUpOnEnable`：只在執行中開啟翻譯（從關閉變成開啟）時於背景 daemon 執行緒預熱一次（同時只有一個預熱在進行；失敗只記 warning），因為以「只顯示原文」開始的 session 沒有開場預熱。逐步模式（測試、smoke）不啟動。**只顯示原文**（`translation.enabled=false`，Milestone 14）時保溫是「不動作」的（`KeepWarm(active=False)`）：不建立執行緒、不送任何請求；執行中開啟翻譯（`Pipeline.set_translation_enabled(True)` → `KeepWarm.set_active(True)`）時才建立執行緒並先在背景預熱一次（以「開啟翻譯後」的設定組請求），之後照常保溫；再關閉就停止送請求。翻譯器在 session 開始時就要包好（活動才記得到），所以不能等開啟時才建立。不需要翻譯的語言組合（影片翻譯、收聽與字幕同一個 Whisper 語言）仍原樣回傳。
- `build_components(settings, mode) -> PipelineComponents`（完整模型載入失敗時先 `close()` 已載入的即時模型，讓它回到模型池，再往上拋）：`SileroVad`、兩個 transcriber（各自的 `*_compute_type`、`*_cpu_threads`，共用 `device` 與 cuBLAS 目錄；Milestone 16：模型、精度、執行緒數都相同時只載入一次——`workers=2`、完整路徑用 `with_options()`，兩條路徑共用一份權重仍可並行，見 `hardware.md`「兩條路徑共用一份權重」）、兩個 `OpenAICompatibleTranslator`（即時路徑 `max_retries=0`、`read_timeout=10 s`：重試只會讓即時字幕更晚；完整路徑用翻譯模組預設值；Milestone 16：共用依 `translation.glossary` 建立的 `Glossary`，並各自包上 `ContextFallbackTranslator`——譯文像是翻出前文時不帶前文重翻一次，見 `translate.md`「前文處理」）、filter。

## Tech Stack

- Python 3.12、uv；只用標準庫與既有依賴（numpy、onnxruntime、opencc、capture 後端、ctranslate2 經 asr）。

## Commands

```text
測試（本模組）：uv run python -m unittest tests.test_config tests.test_pipeline tests.test_pipeline_factory tests.test_engine tests.test_engine_hardware
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
    stability.py         → 即時字幕的穩定前綴（LocalAgreement-2）與即時翻譯的重送規則（F2）
    metrics.py           → 音量（RMS dBFS）、延遲中位數、PipelineMetrics（F4）
    pipeline.py          → Pipeline
    filtering.py         → default_transcript_filter
    factory.py           → 真實元件與 ASR 接縫（延遲匯入）
  engine/
    __init__.py
    protocol.py          → 協定常數、請求解析與驗證、錯誤碼、ProtocolError
    io.py                → MessageWriter（鎖、flush）、行讀取（長度上限）、EventLogHandler（logging → log 事件）
    main.py              → run_engine()：stdio 與 logging 設定、選擇服務
    services.py          → EngineServices、HardwareProvider、ModelCatalog 與預設實作
    deferred.py          → 背景工作執行緒（耗時請求完成後才回應）
    hardware_service.py  → 偵測快取、models.list／validate、設定 ↔ 推薦組合的轉換
    server.py            → EngineServer：分派、session、下載
    testing.py           → 假模式服務（含假硬體與假 GPU 執行環境）
tests/
  test_config.py
  test_pipeline.py
  test_pipeline_factory.py → 設定 → asr／翻譯器參數（mock load_transcriber）
  test_engine.py         → 子行程 `python -m kikitori engine`（假模式）
  test_engine_hardware.py → 同行程：背景工作執行緒、偵測快取、設定與推薦組合的轉換
  test_cli.py
experiments/pipeline_smoke/
  smoke.py、README.md
```

## Testing Strategy

- config：預設值、每種驗證錯誤（型別、範圍、未知欄位、布林當數字、max ≤ pause）、round-trip、深度合併、原子寫入（替換失敗時原檔不變、沒有殘留暫存檔）、新版 schema 拒絕、三平台路徑（注入 system／env／home）、`KIKITORI_CONFIG_DIR`、無效檔案改名。
- pipeline（逐步模式、假 VoiceModel／Transcriber／Translator）：即時→完整順序與取代、最新槽丟舊工作、句子結束後丟即時工作、完整結果不丟且依序（含佇列背壓）、兩種模式的語言路由、中文不翻譯、不支援組合、非語音過濾與「清除」完整結果、前文與 ASR prompt、尾巴裁切、翻譯錯誤為 warning、ASR 錯誤為 error 狀態、完整模型 no_speech 不可靠時改用即時模型的值、即時辨識慢時自動拉長間隔（假時鐘）；執行緒模式：`ArraySource` 跑完送 `idle`、`stop()` 乾淨結束（以事件等待，不用 sleep 判斷）、worker 例外變 `error`。
- engine：子行程（假模式、`KIKITORI_CONFIG_DIR` 指到暫存目錄）交換 NDJSON：`engine.info`、`unknown_method`、壞 JSON／非物件／錯型別 id／超長行、`settings.get`／`set`（成功、驗證錯誤）、`devices.list`、`hardware.detect`、`models.list`、`models.download` 進度事件、session 產生字幕事件與 `idle`、`session.stop`、`session_active`、`engine.shutdown` 與 stdin EOF 結束碼 0、stdout 每一行都是合法 JSON；另以同行程測 `MessageWriter`、請求解析。
- 自動推薦：config 測 `is_new`（新檔、存檔後、無效檔、存檔失敗不變）、`update_if_new`、`ui` 的驗證／合併／缺少時不遷移；`test_engine_hardware.py` 測 `recommendation_preview()`（patch 與 apply 相同、套用後 `differs=false`、推薦以外欄位不影響、手動改模型後 `differs=true`、指紋穩定且隨推薦改變）；子行程測 `is_new`（重啟後仍 false）、preview 不寫檔且與 apply 的 `applied` 相同、手動修改後與 GPU 執行環境安裝後 `differs=true`、`only_if_new`（已存過不覆寫、第一次會寫、非布林 `invalid_params`）。
- engine（Milestone 6）：子行程測 `hardware.detect`（假 GPU、缺 cuBLAS 的推薦）、`models.list`（新舊欄位）、背景請求的回應順序、`gpu-runtime` 授權閘門（缺少／false／非布林、ASR 帶 `accept_license`）、安裝進度與安裝後推薦改為 GPU、雜湊不符的 `error` 事件、不支援的平台、`engine.shutdown` 取消下載與 `download_in_progress`、`models.validate`（目前設定、候選設定不存檔、參數錯誤）、`settings.apply_recommendation`（安裝前 CPU、安裝後 CUDA、存檔）、v1 設定檔遷移；同行程測背景工作執行緒（FIFO、錯誤碼、結束時丟棄）、偵測快取（假時鐘、設定改變、清除）、`allow_restricted`、設定與推薦組合的轉換。
- factory：兩條路徑各自的 `compute_type`／`cpu_threads`、cuBLAS 目錄（設定 + GPU 執行環境、不重複、檢查失敗只記 warning）、自動執行緒（mock `load_transcriber` 與 hardware）；預熱的順序（ASR → 預熱 → 保溫）、保溫只給本機服務、`keep_warm_secs = 0` 關閉、保溫請求與預熱相同且失敗時丟出帶模型名稱的 `WarmUpError`。
- keep_warm（`tests/test_keep_warm.py`，假時鐘，背景執行緒的等待注入成「推進假時鐘」，不真的睡）：閒置未滿不送、滿了才送且之後每個間隔一次、翻譯活動延後下一次、`stop()` 後不再送、失敗的 warning 限流與恢復的 info、`stop()` 立即喚醒等待且不等進行中的請求、`ActivityTranslator` 在 `translate`／`stream` 的開始與結束（含例外）回報 `begin`／`end`、請求進行中不送保溫、程式錯誤也限流。pipeline：保溫在 worker 之前啟動，`stop()`、讀完、錯誤都會停止，擷取開不起來時不啟動，啟動失敗只是 warning，逐步模式不啟動。
- Milestone 12–14：`tests/test_engine_languages.py`（同行程、假服務）測 `languages.list`、`settings.preview_languages`（不寫檔、不下載、別名與模式、參數錯誤）、`session.set_translation`（沒有 session 只存設定、執行中切換 pipeline、載入中切換在 pipeline 建好時套用、參數錯誤）、`models.pull_translation`（進度事件與 `kind`、回應先於事件、完成後 `models.list` 已安裝、失敗事件、`download_in_progress`、結束時取消、名稱驗證、ASR 與翻譯名稱不衝突、連不上與不是 Ollama 的錯誤碼）、`translation_model_missing`（載入 ASR 之前回報、下載後重試成功、關閉翻譯與同語言不檢查、連不上或不是 Ollama 時照常開始）；`test_engine.py` 以子行程測同樣的方法與事件；`test_pipeline.py` 以計數的假翻譯器測關閉時不呼叫、執行中開啟後恢復呼叫、關閉後停止、保溫工作跟著切換；`test_keep_warm.py` 測不動作的保溫（不建立執行緒、不送請求）、開啟時在背景立即預熱、重新開啟再預熱、停止後不再建立，以及 `WarmUpOnEnable`（`keep_warm_secs = 0`：start 不送請求、開啟時背景預熱一次、start 之前與 stop 之後不送、失敗只記 warning）；`test_pipeline_factory.py` 測 0 時翻譯器不包裝、開啟翻譯時仍預熱一次；`test_engine_languages.py` 另測檢查翻譯伺服器期間停止就不載入 ASR、下一個 session 等前一個載入結束（同時載入的數目最多 1）、等待中的 session 也能停止、`translation_server_auth_failed` 與金鑰被拒時照常開始並記 warning、`DefaultTranslationModels` 把 `OllamaAuthError` 對應成 `auth_failed`、回應寫不出去時撤銷翻譯模型下載的登記；`test_engine_hardware.py` 測 `Deferred.unsent`（只在成功的結果寫不出去時執行）；`test_engine_hardware.py` 測依語言的推薦、驗證、`preview_languages` 的模型與大小、指紋含語言、`Deferred.after`。
- F2／F4／F7 與 memory_soak 修正（2026-11-06）：`test_pipeline.py` 的 `StablePartialTest`（穩定前綴序列、計數的假翻譯器在閃爍序列上請求數由 5 降到 3、
  沿用譯文、相同原文不重送、新句子從 0、比較顯示用原文、不翻譯時仍有 `stable_chars`、完整字幕一定翻譯）與 `LatencyMetricsTest`（假時鐘的
  `latency_ms`、最近 20 句中位數、「清除」結果不計入、backlog、最近 1 秒的 dBFS 與上下限、執行緒模式）；`test_engine_models.py`（同行程）測
  `subtitle_event()` 的欄位、`metrics` 的形狀與間隔、停止／讀完／引擎結束／開裝置失敗後沒有遺留的 metrics 執行緒、結束狀態之後沒有 metrics、
  session 結束（停止、讀完、載入中停止）時 transcriber 各關閉一次、`disk_bytes`、`models.delete`（成功、未知、沒下載、參數錯誤、執行中／載入中
  （含停止後）／下載中為 `model_in_use`、其他模型可刪）；`test_engine.py` 以子行程測新欄位、`metrics` 事件與 `models.delete`；`test_asr_download.py`
  以暫存快取測 `model_disk_bytes`／`delete_model`（殘留、其他模型不動、連結與 junction 拒絕、id 不是單一資料夾名稱）；`test_pipeline_factory.py`
  測 `delete_asr_model`（先清模型池）、`release_asr_models`、完整模型失敗時關閉即時模型；`test_asr_model_pool.py` 以假的 `ctranslate2` 模組測模型池；
  `test_capture_threaded.py` 與 `test_engine_capture_errors.py` 以 `gc.disable()` + weakref 測開裝置失敗後元件只靠參照計數就釋放；
  `test_hardware_ollama.py` 測 `Connection: close` 的回應（`/api/version` 與 `/api/pull`）與 `_PollingSocket` 的參照計數。
- 不連網、不開真實裝置、不載入真模型。

## Boundaries

- **Always**：測試與 lint 全過；驗證所有外部輸入（stdin、設定檔、參數）；I/O 設 timeout；例外記錄並往上回報；stdout 只寫協定訊息。
- **Ask first**：新增依賴；修改 vad／translate／capture／asr 的程式碼（只修真正的 bug 並回報）；改架構規格。
- **Never**：寫死或記錄 API key；空的 `except`；在 pipeline 用牆鐘時間算音訊位置；pipeline 單元測試依賴 sleep 的時序。

## Success Criteria

1. 單元測試與 lint 全過，涵蓋上述正常與錯誤路徑，不需網路、音效卡、模型。
2. 協定表中每個方法與事件都有實作，並有子行程測試。
3. 端到端實測（anime.wav → Silero → 真 ASR → 本機 Ollama）印出字幕串流與每句的即時、完整延遲，結果寫入 `experiments/pipeline_smoke/README.md`。
