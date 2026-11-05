# 03 模組手冊

每個模組同樣五項：**作用、主要檔案、對外介面、測試在哪、常見改動會改哪裡**。
模組之間只透過 `types.py` 裡的 `typing.Protocol`（介面）與凍結 dataclass（不可修改的資料結構）互動。
改模組內部時，只要介面不變，其他模組不用動。

測試指令（全部）：

```text
uv run python -m unittest discover -s tests     # Python 測試
uv run ruff check                                # Python lint
cd app && npm test && npm run build              # 前端測試、型別檢查與建置
cd app/src-tauri && cargo test                   # Rust 測試
cd app/src-tauri && cargo clippy --all-targets -- -D warnings
```

只跑一個檔案：`uv run python -m unittest tests.test_vad_segmenter`。

---

## capture（聲音擷取）

- **作用**：從系統播放的聲音（loopback）或麥克風錄音，交出 16 kHz、單聲道、int16、一維的 numpy 陣列。
- **主要檔案**（`src/kikitori/capture/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `AudioSource` 介面、`DeviceInfo` |
  | `devices.py` | `list_devices()`、`open_source()`：依平台選後端 |
  | `_wasapi_backend.py` | Windows（PyAudioWPatch），含 loopback 靜音補零 |
  | `_soundcard_backend.py` | Linux PulseAudio／PipeWire、macOS 麥克風 |
  | `_screencapturekit_backend.py` | macOS 13+ 系統聲音 |
  | `resample.py` | 降混音、重採樣（自己用 numpy 實作）、轉 int16 |
  | `threaded.py` | `ThreadedSource`：擷取執行緒的共用骨架 |
  | `buffer.py` | `ChunkBuffer`：最多 5 秒，滿了丟最舊的 |
  | `sources.py` | 測試用 `ArraySource`、`FileSource`（讀 WAV） |
  | `errors.py` | `CaptureError` 及子類別（權限、裝置消失…） |

- **對外介面**：`AudioSource.start() / read(timeout) / stop()`；`list_devices()`；`open_source(device_id, kind)`。裝置 id 格式 `<backend>:<kind>:<原生 id>`，呼叫端不得自己解析。
- **測試**：`tests/test_capture_*.py`（buffer、devices、resample、wasapi、soundcard、screencapturekit、sources），全部用假物件，不開真裝置。實機手動檢查：`uv run python experiments/capture_check/check_capture.py`（互動選單，可錄 N 秒存成 WAV）。
- **常見改動**
  - 擷取有問題（爆音、沒聲音）→ 先跑 `check_capture.py`，再看對應平台的 `_*_backend.py`。
  - macOS 靜音時不送資料（尚未驗證）→ 照 WASAPI 的做法在 `_screencapturekit_backend.py` 補零。
  - 改緩衝長度 → `ChunkBuffer` 的上限。

## vad（切句）

- **作用**：每 32 ms 判斷有沒有人聲，把連續聲音切成句子，送出開始／音訊／結束事件。
- **主要檔案**（`src/kikitori/vad/`）

  | 檔案 | 角色 |
  |---|---|
  | `segmenter.py` | `Segmenter`、`ResetPolicy`、三種事件 |
  | `endpoint.py` | `EndpointPolicy` 介面、`PauseEndpoint`（靜音或句長上限） |
  | `model.py` | `VoiceModel` 介面：「512 樣本 → 機率」與 `reset()` |
  | `silero.py` | `SileroVad`：onnxruntime 推論、64 樣本前文、狀態保存、SHA-256 驗證 |
  | `models/` | `silero_vad.onnx`（v6.2.3）、MIT 授權、版本說明 |

- **對外介面**：`Segmenter(model, ...).process(audio) -> list[事件]`、`flush()`、`request_end()`；事件 `UtteranceStarted`、`UtteranceAudio`、`UtteranceEnded`。
- **測試**：`tests/test_vad_segmenter.py`（用假模型餵腳本化的機率，測事件邏輯與三種重置策略）、`tests/test_silero_vad.py`（前文與狀態串接、錯誤輸入、真模型）、`tests/test_vad_integration.py`（純噪音產生 0 句；`src/kikitori/` 不匯入 torch／pipecat）。評測：`uv run python -I experiments/silero_reset/menu.py`。
- **常見改動**
  - 調靈敏度 → `Segmenter` 的 `threshold`、`min_speech_ms`、`pre_roll_ms`（目前沒有開放到設定檔，pipeline 用預設值）。
  - 調句尾停頓 → 不用改程式，改設定檔的 `media.pause_secs`／`max_secs`。
  - 加新的句尾判斷（標點模型、Smart Turn）→ 實作 `EndpointPolicy`，或呼叫 `request_end()`。
  - 換 Silero 版本 → 同時更新模型檔、`silero.py` 的常數與雜湊、`models/README.md`、授權檔，並重跑評測。

## asr（語音辨識）

- **作用**：用 CTranslate2 跑 Whisper，把一句聲音轉成文字；管理模型清單、下載；過濾幻覺。
- **主要檔案**（`src/kikitori/asr/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `Transcript`、`Transcriber` 介面 |
  | `whisper.py` | `WhisperTranscriber`、`load_transcriber()`、`FAST_DECODE`、`FINAL_DECODE`、CUDA 失敗退回 CPU |
  | `features.py` | log-mel 特徵（從 faster-whisper 移植） |
  | `tokenizer.py` | Whisper 分詞器（自己實作，與 HF `tokenizers` 比對 514 個字串 0 不一致） |
  | `registry.py` | 6 個模型：固定 Hugging Face commit、每個檔案的大小與 SHA-256、資源用量、`no_speech_reliable` |
  | `download.py` | 下載、續傳、驗證、取消；只允許 https |
  | `cache.py` | 使用者快取目錄（Windows `%LOCALAPPDATA%\kikitori\models`） |
  | `filtering.py` | `FilterConfig`、`rejection_reason()`、已知幻覺句清單 |

- **對外介面**：`Transcriber.transcribe(audio, language=None, prompt=None) -> Transcript`。**只有 `pipeline/factory.py` 匯入 asr 的實作**，而且在函式內才匯入，所以其他模組的測試不會載入 ctranslate2。
- **測試**：`tests/test_asr_*.py`（音訊與特徵、分詞器、registry 與快取、下載用本機 HTTP 伺服器、推論流程用假 CTranslate2 模型、過濾規則）。真模型的基準測試：`uv run python -I experiments/asr_benchmark/menu.py`。
- **常見改動**
  - 加幻覺句 → `filtering.py` 的 `DEFAULT_HALLUCINATIONS`（或 `DEFAULT_SOFT_HALLUCINATIONS`），並在 `tests/test_asr_filtering.py` 加正反例。
  - 加模型 → `registry.py` 的 `MODELS`（要填 revision、檔案大小、SHA-256、授權）；再更新 `hardware/recommend.py` 的推薦表。
  - 調即時路徑速度 → `FAST_DECODE`。

## translate（翻譯）

- **作用**：把一句話送到 OpenAI 相容 API 翻譯，整理成一行字幕。
- **主要檔案**（`src/kikitori/translate/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `TranslationRequest`、`Translator` 介面 |
  | `client.py` | `TranslatorConfig`、`OpenAICompatibleTranslator`（HTTP、SSE 串流、重試、API key） |
  | `prompts.py` | 三種提示詞格式：`generic`、`hunyuan_mt`、`sakura` |
  | `presets.py` | 各模型的預設組、授權資訊、依 VRAM 的推薦順序 `RECOMMENDATION_TIERS` |
  | `postprocess.py` | 去思考內容、整理字幕、選擇性簡轉繁 |
  | `languages.py` | 語言碼正規化、支援的組合 `SUPPORTED_PAIRS` |
  | `errors.py` | `TranslationError` 及子類別 |

- **對外介面**：`Translator.translate(request) -> str`、`stream(request) -> Iterator[str]`（每次產出累積的完整譯文）。
- **測試**：`tests/test_translate_prompts.py`、`tests/test_translate_client.py`（背景執行緒跑假的 OpenAI 相容伺服器，不連網）。實測（要本機 Ollama）：`uv run python -I -X utf8 experiments/translate_benchmark/menu.py`。
- **常見改動**
  - 調提示詞 → `prompts.py` 的 `_GENERIC_TEMPLATES`，改完重跑翻譯實測。
  - 支援新模型 → `presets.py` 加一個 `ModelPreset`（格式、取樣參數、授權、是否帶前文）。
  - API key：設定檔只存**環境變數名稱**（預設 `KIKITORI_TRANSLATE_API_KEY`），值只從環境變數讀。

## pipeline（協調器）

- **作用**：把 capture、vad、asr、translate 串起來，跑即時與完整兩條路徑，輸出 `SubtitleUpdate`。
- **主要檔案**（`src/kikitori/pipeline/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `SubtitleUpdate`、`PipelineConfig`、`PipelineComponents` |
  | `pipeline.py` | `Pipeline`：三條執行緒、事件 → 工作、過濾、翻譯、送出 |
  | `scheduler.py` | `LatestSlot`（只留最新）、`FinalQueue`（有界、不丟） |
  | `languages.py` | `route()`：辨識語言 → 譯文語言 |
  | `factory.py` | 依設定建立真實元件（Silero、兩個 Whisper、兩個翻譯器、過濾器） |
  | `filtering.py` | 測試與假模式用的簡易過濾器 |

- **對外介面**：`Pipeline(components, config, on_subtitle=…, on_status=…).start(source) / stop() / wait()`；另有不開執行緒的逐步模式 `feed() / end_of_stream() / run_pending()`，給測試用。
- **測試**：`tests/test_pipeline.py`（假的 VAD、ASR、翻譯器，假時鐘，不靠 sleep）、`tests/test_pipeline_factory.py`。端到端實測：`uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py`。
- **常見改動**
  - 草稿頻率 → 設定檔 `pipeline.partial_interval_ms`（200–5000）。
  - 前文句數 → 設定檔 `pipeline.context_lines`（0–10）。
  - 語言規則 → `languages.py`。
  - 換元件（例如加新 VAD）→ `factory.py` 的 `build_components()`。

## config（設定檔）

- **作用**：讀寫、驗證、遷移 `settings.json`。
- **主要檔案**：`src/kikitori/config/settings.py`（`Settings` 與驗證、`migrate()`）、`store.py`（原子寫入、`SettingsStore`）、`paths.py`（各平台位置）。
- **位置**：Windows `%APPDATA%\kikitori\settings.json`、macOS `~/Library/Application Support/kikitori/`、Linux `~/.config/kikitori/`。環境變數 `KIKITORI_CONFIG_DIR`（絕對路徑）可覆寫，測試都用它。
- **對外介面**：`Settings`、`SettingsStore.get() / update(partial)`、`default_settings_path()`。
- **規則**：未知欄位一律報錯；缺的欄位用預設值；`schema_version` 目前是 2，舊版自動遷移；寫檔先寫暫存檔再 `os.replace`。
- **測試**：`tests/test_config.py`。
- **常見改動**：加新設定 → `settings.py` 加欄位、驗證、`to_dict()`；若改到既有欄位的意義，要升 `SCHEMA_VERSION` 並寫遷移；前端 `app/src/lib/settings.ts` 也要跟著讀。

## hardware（硬體偵測與推薦）

- **作用**：偵測 OS、CPU、RAM、NVIDIA GPU、cuBLAS、翻譯伺服器；推薦模型組合；檢查使用者自訂的組合；下載安裝 cuBLAS。
- **主要檔案**（`src/kikitori/hardware/`）

  | 檔案 | 角色 |
  |---|---|
  | `detection.py` | `detect()`：組合各項偵測，單項失敗只記在 `errors` |
  | `system.py` | CPU、RAM（只用標準庫與 `ctypes`，不加 psutil） |
  | `nvidia.py` | `nvidia-smi`（5 s 逾時）、找 cuBLAS 檔案 |
  | `translation_server.py` | `GET {base_url}/models`（2 s 逾時） |
  | `recommend.py` | `recommend()`、`validate_combination()`（純函式） |
  | `gpu_runtime.py` | `install_gpu_runtime()`：從 PyPI 下載 `nvidia-cublas-cu12`，只解出兩個 DLL 與授權 |
  | `overview.py` | `models.list` 的總覽 |
  | `types.py` | `HardwareInfo`、`ModelCombination` 等 |

- **推薦等級**：`nvidia_8gb`、`nvidia_4gb`、`nvidia_lt4gb`、`cpu`、`cpu_low`、`apple_silicon`（表見 `docs/specs/hardware.md`）。
- **測試**：`tests/test_hardware_*.py`（不跑真的 `nvidia-smi`、不連外網）。
- **常見改動**：調推薦 → `recommend.py`；但依專案規則，應先改 `docs/specs/asr.md`／`translate.md` 的依據數據。

## engine（stdio 引擎）

- **作用**：Tauri 唯一的入口。讀 stdin 的請求、分派、回應；把 pipeline 的字幕與狀態變成事件。
- **主要檔案**（`src/kikitori/engine/`）

  | 檔案 | 角色 |
  |---|---|
  | `main.py` | `run_engine()`：把 `sys.stdout` 換成 stderr，保護協定通道 |
  | `server.py` | `EngineServer`：方法表、session 生命週期、下載 |
  | `protocol.py` | 請求解析、錯誤碼、`ProtocolError` |
  | `io.py` | `MessageWriter`（加鎖、flush）、有長度上限的讀行、`EventLogHandler`（logging → `log` 事件） |
  | `deferred.py` | 慢請求的背景工作執行緒（依序處理） |
  | `hardware_service.py` | 偵測快取 10 秒、推薦組合 ↔ 設定的轉換 |
  | `services.py` | `EngineServices`：可替換的外部功能 |
  | `testing.py` | 假模式（`KIKITORI_ENGINE_FAKE=1`）：合成聲音、假 ASR／翻譯、假硬體 |

- **對外介面**：協定本身（見 [01](01-system-overview.md) 與 `docs/specs/pipeline-engine.md`）。
- **測試**：`tests/test_engine.py`（真的開子行程跑假模式，交換 NDJSON）、`tests/test_engine_hardware.py`、`tests/test_cli.py`。
- **常見改動**：加協定方法 → `server.py` 的 `_handlers` 加一項；Rust `commands.rs` 加對應指令、`build.rs` 產生權限、`capabilities/main.json` 開權限；前端 `api.ts` 與 `validate.ts` 加呼叫與驗證；`app/dev/fake_engine.py` 也要模仿。

## evaluation（評測工具）

- **作用**：產生白噪音／粉紅噪音、把人聲和背景混音、算 VAD 指標（ROC-AUC、漏抓、開始延遲…）。只給實驗用，正式執行不載入。
- **主要檔案**：`src/kikitori/evaluation/noise.py`、`mixing.py`、`metrics.py`、`wav.py`；選單在 `experiments/silero_reset/`（`menu.py`、`musan.py` 下載 MUSAN、`evaluate.py`）。
- **測試**：`tests/test_evaluation.py`、`tests/test_menu.py`（MUSAN 下載用本機假伺服器）。
- **常見改動**：換 VAD 模型或調參數後重跑評測，更新 `experiments/silero_reset/README.md`。

## app／Rust 橋接（`app/src-tauri/src/`）

- **作用**：啟動與看管引擎、請求與回應配對、驗證事件後轉給畫面、字幕視窗控制、全域快速鍵、自動更新。
- **主要檔案**

  | 檔案 | 角色 |
  |---|---|
  | `engine.rs` | `resolve_engine_command()`（選擇啟動指令）、`EngineBridge::start()`（spawn、`engine.info` 握手、檢查 `protocol_version == 1`）、`RestartTracker`（自動重啟退避） |
  | `connection.rs` | `Connection::request()`：以 `id` 配對、每個請求一個期限；`dispatch()` |
  | `protocol.rs` | `parse_line()`：逐行驗證引擎輸出 |
  | `line_reader.rs` | 每行上限 1 MiB 的讀行器 |
  | `commands.rs` | 每個協定方法一個 Tauri 指令，先驗證參數，依方法給不同逾時（一般 10 s、背景方法 45 s、`session.start` 30 s） |
  | `overlay.rs` | 字幕外觀、滑鼠穿透、可見狀態 |
  | `lib.rs` | 外掛、事件轉發（`engine://subtitle` 等）、快速鍵 Ctrl+Alt+K、生命週期 |

- **啟動指令的選擇**：環境變數 `KIKITORI_ENGINE_CMD` > 除錯版 `uv run kikitori engine` > 正式版 `<資源目錄>/kikitori-engine/kikitori-engine[.exe]`。
- **權限**：`capabilities/main.json`、`overlay.json`。字幕視窗只能收事件、移動、縮放、隱藏自己，不能控制引擎。
- **測試**：`cargo test`（各 `.rs` 檔內的 `#[cfg(test)]`，含真的子行程跑 `app/dev/fake_engine.py` 與正式引擎假模式；需要 `uv` 在 PATH）。
- **常見改動**：加協定方法（見 engine 一節）；調逾時 → `commands.rs` 的常數。

## app／前端（`app/src/`）

- **作用**：主視窗（模式、聲音來源、開始／停止、字幕紀錄、模型頁、設定頁）與字幕視窗。Vite + TypeScript，不用 UI 框架。
- **主要檔案**

  | 檔案 | 角色 |
  |---|---|
  | `main/main.ts` | 主視窗全部邏輯（`toggleSession()`、`subscribe()`、各頁 render） |
  | `main/updater.ts` | 自動更新流程 |
  | `overlay/overlay.ts` | 字幕視窗 |
  | `lib/subtitles.ts` | 字幕狀態規則 `applySubtitle()`（兩個視窗共用） |
  | `lib/api.ts` | 呼叫 Rust 指令、訂閱事件、錯誤碼轉中文 |
  | `lib/validate.ts` | 驗證所有事件與指令結果 |
  | `lib/settings.ts`、`lib/combination.ts` | 設定表單與自訂組合的驗證、儲存條件 |
  | `styles/tokens.css` | 色票（藍染 `#22305C`、字幕黃 `#F2C230`…），深色模式 |

- **安全**：引擎的文字一律用 `textContent` 顯示，不用 `innerHTML`；有 CSP。
- **測試**：`npm test`（vitest，`app/src/lib/*.test.ts`）；`npm run build`（`tsc` 型別檢查 + 建置）。
- **常見改動**：字幕樣式 → `styles/overlay.css`；新設定欄位 → `lib/settings.ts` + `main.ts` 的表單；錯誤訊息 → `api.ts` 的 `ENGINE_CODES`。
- **開發**：`cd app && npm install && npm run menu`（互動選單，選要接哪種引擎）。

## packaging（引擎打包）

- **作用**：用 PyInstaller 把引擎打包成 one-folder（一個資料夾：執行檔 + `_internal/` + `THIRD_PARTY_LICENSES/`），放到 `app/src-tauri/binaries/kikitori-engine/` 給 Tauri 帶進安裝檔。
- **主要檔案**（`packaging/engine/`）

  | 檔案 | 角色 |
  |---|---|
  | `build.py` | 建置腳本（互動選單；CI 用 `KIKITORI_BUILD_NONINTERACTIVE=1`） |
  | `kikitori-engine.spec` | PyInstaller 規格：收集什麼、排除什麼 |
  | `policy.py` | 禁止打包的檔名（NVIDIA 函式庫、`libpulse`、Windows UCRT） |
  | `licenses.py` | 依「實際打包的檔案」產生授權目錄；找不到授權就建置失敗 |
  | `entry.py` | 打包後的入口；`--self-check` 自我檢查 |
  | `smoke_test.py` | 冒煙測試：自我檢查 + 假模式協定 + 啟動時間 |

- **測試**：`tests/test_packaging_build.py`、`tests/test_packaging_licenses.py`。
- **常見改動**：新增依賴後打包少檔案 → `kikitori-engine.spec` 加 hidden import 或資料檔；授權找不到 → 補到 `license_overrides/<套件>/`。
- **正式打包**：`npm run tauri build -- --config src-tauri/tauri.sidecar.conf.json`（這個設定檔只放 `bundle.resources`，讓平常的 `cargo test` 不需要先建置 170 MB 的引擎）。

## CI 與發布

- **版本號**：只在 `pyproject.toml` 改。`uv run python scripts/sync_version.py`（互動選單；`check`／`sync`／`bump patch|minor|major`）同步到 `tauri.conf.json`、`Cargo.toml`、`package.json`、`Cargo.lock`、`package-lock.json`。測試：`tests/test_sync_version.py`。
- **CI**（`.github/workflows/test.yml`）：任何分支 push 與 PR，Windows／Ubuntu／macOS 三平台：

  | job | 內容 |
  |---|---|
  | `python` | `uv sync --locked` → unittest → ruff → `sync_version.py check` |
  | `engine-bundle` | `build.py --no-sidecar --smoke`：建置引擎、掃禁止檔案、自我檢查、假模式協定 |

  第三方 action 都釘在完整 commit SHA；權限只有 `contents: read`。
- **還沒有的**：前端與 Rust 的 CI job（檔案裡只有 TODO 註解）、`release.yml`。架構規格寫的發布流程是：push 到 main → 測試 → 版本號與最新 release tag 不同時三平台打包 → 建立 GitHub Release 並附 `latest.json` 與簽章。這部分可能還會變動。
- **自動更新**：`tauri-plugin-updater`，端點是 GitHub Releases 的 `latest.json`（`app/src-tauri/tauri.conf.json`）。公鑰在設定檔，私鑰只在本機與 GitHub Secrets。發布流程完成前，更新檢查找不到東西。
