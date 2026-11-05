# Spec: kikitori 全應用程式架構

> 狀態：已採用（2026-11-05，使用者授權依建議自主實作）。
> 各模組的細節規格放在 `docs/specs/<module-id>.md`；本文件定義模組邊界、介面與跨模組約定，模組規格不得與本文件矛盾。

## 目標

桌面應用程式，兩種模式：

| 模式 | id | 聲音來源 | 語言 | 句尾判斷 |
|---|---|---|---|---|
| 影片翻譯（情境 A） | `media` | 系統播放的聲音（loopback） | 原文 ja／en → 譯文 zh-TW | `PauseEndpoint`（0.6 s、最長 10 s） |
| 即時口譯（情境 B） | `interpret` | 電腦麥克風 | zh-TW ↔ 對方語言（ja 或 en）；偵測到 zh 時譯成對方語言，否則譯成 zh-TW | `PauseEndpoint`（0.5 s、最長 8 s）；Smart Turn 之後再加 |

畫面：設定頁、模型選擇、浮在影片上的透明置頂雙語字幕視窗。支援 Windows、Linux、macOS。

## 行程架構

```text
┌──────────── Tauri 桌面程式（app/，Rust + TypeScript）────────────┐
│  主視窗：模式切換、設定、模型選擇      字幕視窗：透明、置頂、雙語   │
│                    │  stdin/stdout（NDJSON）                      │
└────────────────────┼─────────────────────────────────────────────┘
                     ▼
┌──────────── kikitori-engine（Python sidecar）────────────────────┐
│ capture → vad → asr → translate → pipeline（雙路徑）→ engine 事件 │
│ hardware（偵測與模型組合）  config（設定檔）                      │
└──────────────────────────────────────────────────────────────────┘
```

- **為什麼用 sidecar**：語音處理生態在 Python；Tauri 只負責 UI、視窗、更新。以 stdin/stdout 交換 NDJSON，不開網路埠。
- **打包**：引擎用 PyInstaller（one-folder）打包成各平台的 `kikitori-engine` 執行檔，作為 Tauri `externalBin` sidecar。開發時 Tauri 改用 `uv run kikitori engine` 啟動。
- **時間**：引擎內所有音訊位置用樣本計數（16 kHz），不用牆鐘時間。

## Python 套件結構（`src/kikitori/`）

| 子套件 | 角色 | 主要介面 |
|---|---|---|
| `vad/` | 切句（見 `vad-segmenter.md`） | `Segmenter`、事件 dataclass |
| `asr/` | 語音辨識 | `Transcriber` |
| `translate/` | 翻譯 | `Translator` |
| `capture/` | 聲音擷取並轉成 16 kHz 單聲道 int16 | `AudioSource`、`list_devices()` |
| `pipeline/` | 協調器：雙路徑 | `Pipeline` |
| `hardware/` | 硬體偵測、模型組合 | `detect()`、`recommend()` |
| `config/` | 設定檔讀寫與驗證 | `Settings` |
| `engine/` | stdio NDJSON 伺服器，對 Tauri 的唯一入口 | `kikitori engine` 指令 |
| `evaluation/` | 評測工具（見 VAD 規格） | — |

模組之間只透過下列介面互動；介面以 `typing.Protocol` 與凍結 dataclass 定義，放在各子套件的 `types.py`（或規格指定的檔案）。

### capture

```python
class AudioSource(Protocol):
    def start(self) -> None: ...
    def read(self, timeout: float) -> np.ndarray: ...   # int16、16 kHz、單聲道、一維；逾時回傳空陣列
    def stop(self) -> None: ...

@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    kind: Literal["loopback", "microphone"]
    is_default: bool
```

- 降混音與重採樣在 capture 內完成。
- Windows：WASAPI loopback；Linux：PulseAudio／PipeWire monitor source；macOS：ScreenCaptureKit（macOS 13+）。三平台麥克風皆支援。
- 測試用 `FileSource`（讀 WAV、可設定是否依即時速度）與 `ArraySource`。

### asr

```python
@dataclass(frozen=True)
class Transcript:
    text: str
    language: str              # ISO 639-1，例如 "ja"
    language_probability: float
    no_speech_probability: float
    avg_logprob: float

class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, language: str | None = None,
                   prompt: str | None = None) -> Transcript: ...
```

- 不依賴 PyTorch。模型於第一次使用時下載到使用者快取目錄（有進度回報、timeout、可指定本機路徑）。

### translate

```python
@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str        # 預設 "zh-TW"
    context: tuple[str, ...] = ()   # 前幾句原文，提升連貫性

class Translator(Protocol):
    def translate(self, request: TranslationRequest) -> str: ...
    def stream(self, request: TranslationRequest) -> Iterator[str]: ...   # 逐段回傳累積中的譯文片段
```

- OpenAI 相容 Chat Completions API；預設 `base_url = http://localhost:11434/v1`（本機 Ollama）。
- API key 只從環境變數讀（設定檔只存變數名稱，預設 `KIKITORI_TRANSLATE_API_KEY`；Ollama 不需要）。
- 所有請求設 timeout，錯誤往上回報，不吞例外。

### pipeline（雙路徑）

```python
@dataclass(frozen=True)
class SubtitleUpdate:
    utterance_id: int
    original: str
    translation: str
    source_language: str
    target_language: str
    is_final: bool              # False = 即時字幕（會被取代）；True = 完整翻譯（最終版）
    start_sample: int
    end_sample: int
```

- **即時路徑**：句子進行中，每隔 `partial_interval_ms`（預設 700 ms）對目前累積的音訊做一次快速辨識與翻譯，送出 `is_final=False`。落後時跳過舊的即時請求，只做最新的。
- **完整路徑**：`UtteranceEnded` 後，用完整音訊重新辨識，帶前文翻譯，送出 `is_final=True`，取代同一個 `utterance_id` 的即時字幕。
- 即時與完整路徑可使用不同模型（例如即時用較小的 ASR 模型）。
- 依 `no_speech_probability` 與空字串過濾非語音（含歌聲誤觸發造成的幻覺），門檻可設定。

### hardware

- 偵測：作業系統、CPU 核心數、RAM、NVIDIA GPU 與 VRAM（`nvidia-smi`，有 timeout）、Apple Silicon。
- `recommend()` 回傳模型組合（ASR 即時／完整模型、運算裝置與精度、翻譯模型）；使用者可在設定頁覆寫。

### config

- 設定檔：使用者設定目錄下的 `kikitori/settings.json`（Windows `%APPDATA%`、macOS `~/Library/Application Support`、Linux `$XDG_CONFIG_HOME` 或 `~/.config`）。
- 讀取時驗證型別與範圍；版本欄位供未來遷移；不存密碼與 API key。

## 引擎協定（stdio NDJSON）

每行一個 JSON 物件，UTF-8。

**請求（Tauri → 引擎）**：`{"id": <int>, "method": "<name>", "params": {...}}`
**回應**：`{"id": <int>, "result": {...}}` 或 `{"id": <int>, "error": {"code": "<code>", "message": "<訊息>"}}`
**事件（引擎 → Tauri，無 id）**：`{"event": "<name>", "data": {...}}`

| 方法 | 參數 | 結果 |
|---|---|---|
| `engine.info` | — | 版本、協定版本、平台 |
| `hardware.detect` | — | 硬體資訊與推薦組合 |
| `models.list` | — | 可選的 ASR 模型、本機 Ollama 模型、是否已下載 |
| `models.download` | `{"model": ...}` | 開始下載，進度以事件回報 |
| `devices.list` | — | `DeviceInfo` 列表 |
| `settings.get` / `settings.set` | `{...}` | 目前設定；`set` 驗證失敗回傳錯誤 |
| `session.start` | `{"mode": "media" \| "interpret", "device_id": ...}` | `{"session_id": ...}` |
| `session.stop` | — | `{}` |
| `engine.shutdown` | — | `{}`，之後引擎結束 |

| 事件 | 內容 |
|---|---|
| `subtitle` | `SubtitleUpdate` 的欄位 |
| `status` | `{"state": "idle" \| "loading" \| "running" \| "error", "detail": ...}` |
| `download_progress` | `{"model", "downloaded_bytes", "total_bytes"}` |
| `log` | `{"level", "message"}`（不含密鑰） |

- 協定版本 `protocol_version = 1`；不認得的方法回傳 `unknown_method` 錯誤。
- 引擎的日誌一律寫到 stderr 或 `log` 事件，**stdout 只寫協定訊息**。

## Tauri 程式（`app/`）

- Tauri v2 + Vite + TypeScript（不使用大型 UI 框架），介面依 frontend-design skill 的原則設計。
- 視窗：`main`（設定、模型選擇、模式切換、狀態）與 `overlay`（透明、無邊框、置頂、可切換滑鼠穿透，雙語兩行字幕，即時字幕以較淡樣式顯示、完整翻譯取代後轉為正常樣式）。
- 自動更新：`tauri-plugin-updater`，端點為 GitHub Releases 的 `latest.json`；公鑰寫在設定檔，私鑰只在本機與 GitHub Secrets。

## 版本號

單一來源：`pyproject.toml` 的 `version`。`app/src-tauri/tauri.conf.json`、`app/src-tauri/Cargo.toml`、`app/package.json` 由 `scripts/sync_version.py` 同步，CI 檢查四者一致。

## CI/CD（GitHub Actions）

- `test.yml`：任何分支 push 與 PR；Windows、Ubuntu、macOS 矩陣；Python 測試與 ruff、前端型別檢查與建置、`cargo test`／`cargo clippy`。
- `release.yml`：push 到 main；先跑同一套測試；版本號與最新 release tag 不同時才在三平台打包（PyInstaller 引擎 + Tauri bundle）並建立 GitHub Release，附更新器所需的 `latest.json` 與簽章。macOS 不做付費簽章與公證。

## 共同規則

- 遵守 `CLAUDE.md`：依賴用 `uv add <pkg>==<version>`、測試與 lint 全過、型別標註、不吞例外、外部輸入先驗證、I/O 設 timeout、密鑰只從環境變數讀。
- 新增的第三方依賴與模型都要記錄授權；有授權疑慮時停下來回報。
- 文件與使用者介面文字用繁體中文；程式識別字用英文。
