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

## languages（語言清單）

- **作用**：20 種收聽／字幕語言的唯一資料來源。設定驗證、ASR 的辨識語言、翻譯路由、提示詞、推薦、UI 的語言選單都從這裡取。
- **主要檔案**：`src/kikitori/languages/catalog.py`（`Language`、`LANGUAGES`、`WHISPER_CODES`、各模型的辨識品質表 `_QUALITY_ROWS`）、`__init__.py`（對外匯出）。
- **對外介面**：`normalize_language(code)`（設定與字幕用的清單語言碼；接受別名，拒絕不分繁簡的 `zh`）、`to_whisper_code(code)`（→ Whisper 語言碼）、`get_language(code)`、`language_codes()`、`asr_quality(code, model)`。引擎方法 `languages.list` 把清單交給 UI。
- **測試**：`tests/test_languages.py`、`tests/test_engine_languages.py`。
- **常見改動**：加語言 → 見 [07](07-languages-and-i18n.md)「改語言相關功能時去哪裡」（要一起補幻覺片語、品質等級、預熱短句）。

## asr（語音辨識）

- **作用**：用 CTranslate2 跑 Whisper，把一句聲音轉成文字；管理模型清單、下載、磁碟用量與刪除；過濾幻覺；重複使用模型物件。
- **主要檔案**（`src/kikitori/asr/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `Transcript`、`Transcriber` 介面 |
  | `whisper.py` | `WhisperTranscriber`（用完要 `close()`）、`load_transcriber()`、`FAST_DECODE`、`FINAL_DECODE`、CUDA 失敗退回 CPU、模型物件池 `MODEL_POOL`（見 [09](09-memory-and-resource-release.md)） |
  | `features.py` | log-mel 特徵（從 faster-whisper 移植） |
  | `tokenizer.py` | Whisper 分詞器（自己實作，與 HF `tokenizers` 比對 514 個字串 0 不一致） |
  | `registry.py` | 6 個模型：固定 Hugging Face commit、每個檔案的大小與 SHA-256、資源用量、`no_speech_reliable` |
  | `download.py` | 下載、續傳、驗證、取消（只允許 https）；`model_disk_bytes()`、`delete_model()`（只刪模型快取內、不跟隨連結） |
  | `cache.py` | 使用者快取目錄（Windows `%LOCALAPPDATA%\kikitori\models`） |
  | `filtering.py` | `FilterConfig`、`rejection_reason()`；依語言分表的幻覺片語（hard 不分語言、soft 只比對辨識出的語言） |
  | `hallucination_lexicon.py` | 19 種語言的幻覺片語資料；由 `experiments/hallucination_lexicon/lexicon.json` 產生，不要手改 |

- **對外介面**：`Transcriber.transcribe(audio, language=None, prompt=None) -> Transcript`。**只有 `pipeline/factory.py` 匯入 asr 的實作**，而且在函式內才匯入，所以其他模組的測試不會載入 ctranslate2。
- **測試**：`tests/test_asr_*.py`（音訊與特徵、分詞器、registry 與快取、下載與刪除用本機 HTTP 伺服器與暫存資料夾、推論流程用假 CTranslate2 模型、模型池、過濾規則與片語表）。真模型的基準測試：`uv run python -I experiments/asr_benchmark/menu.py`；日語直播的評測：`experiments/vtuber_eval/`（見 [08](08-evaluation-methods.md)）。
- **常見改動**
  - 加幻覺句 → 改 `experiments/hallucination_lexicon/lexicon.json`（選 hard 或 soft），用該資料夾的選單第 4 項重新產生 `asr/hallucination_lexicon.py`；`tests/test_asr_hallucination_lexicon.py` 檢查兩者一致。
  - 加模型 → `registry.py` 的 `MODELS`（要填 revision、檔案大小、SHA-256、授權）；在 `languages/catalog.py` 的 `_QUALITY_ROWS` 補品質等級（測試檢查）；再更新 `hardware/recommend.py` 的推薦。
  - 調即時路徑速度 → `FAST_DECODE`。

## translate（翻譯）

- **作用**：把一句話送到 OpenAI 相容 API 翻譯，整理成一行字幕。
- **主要檔案**（`src/kikitori/translate/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `TranslationRequest`、`Translator` 介面 |
  | `client.py` | `TranslatorConfig`、`OpenAICompatibleTranslator`（HTTP、SSE 串流、重試、API key） |
  | `prompts.py` | 三種提示詞格式：`generic`（zh-TW／zh-CN／ja／en 用譯文語言寫指示，其他語言用英文）、`hunyuan_mt`、`sakura`；術語的放法 |
  | `presets.py` | 各模型的預設組（支援的語言、取樣參數、授權）、依 VRAM 的推薦順序 `RECOMMENDATION_TIERS` |
  | `glossary.py` | 術語表：內建 53 個日語直播用語＋使用者詞條；`Glossary.lookup()` 只回傳這句原文出現的詞（最多 8 條） |
  | `context_guard.py` | 前文保險 `ContextFallbackTranslator`：譯文像是翻出前文時不帶前文重翻一次 |
  | `postprocess.py` | 去思考內容、清特殊 token 碎片、整理字幕、字形轉換（zh-TW 選擇性 `s2twp`、zh-CN `t2s`） |
  | `languages.py` | 翻譯請求的語言碼正規化與方向檢查（`normalize_pair()`、Sakura 的 `SAKURA_PAIRS`） |
  | `errors.py` | `TranslationError` 及子類別 |

- **對外介面**：`Translator.translate(request) -> str`、`stream(request) -> Iterator[str]`（每次產出累積的完整譯文）。`TranslationRequest` 的原文語言是 Whisper 語言碼，譯文語言是清單語言碼（見 [07](07-languages-and-i18n.md)）。
- **測試**：`tests/test_translate_prompts.py`、`tests/test_translate_client.py`（背景執行緒跑假的 OpenAI 相容伺服器，不連網）、`tests/test_translate_glossary.py`、`tests/test_translate_context_guard.py`。實測（要本機 Ollama）：`experiments/translate_benchmark/`（速度）與 `experiments/translate_eval/`（有參考譯文的品質評測，見 [08](08-evaluation-methods.md)）。
- **常見改動**
  - 調提示詞 → `prompts.py` 的 `_GENERIC_TEMPLATES`，改完重跑翻譯評測。
  - 支援新模型 → `presets.py` 加一個 `ModelPreset`（格式、支援的語言、取樣參數、授權、是否帶前文）。
  - 加內建術語 → `glossary.py` 的內建表；只收不會和一般用語撞字的寫法（檔頭說明）。
  - API key：設定檔只存**環境變數名稱**（預設 `KIKITORI_TRANSLATE_API_KEY`），值只從環境變數讀。

## pipeline（協調器）

- **作用**：把 capture、vad、asr、translate 串起來，跑即時與完整兩條路徑，輸出 `SubtitleUpdate`。
- **主要檔案**（`src/kikitori/pipeline/`）

  | 檔案 | 角色 |
  |---|---|
  | `types.py` | `SubtitleUpdate`、`PipelineConfig`、`PipelineComponents` |
  | `pipeline.py` | `Pipeline`：三條執行緒、事件 → 工作、過濾、翻譯、送出 |
  | `scheduler.py` | `LatestSlot`（只留最新）、`FinalQueue`（有界、不丟） |
  | `languages.py` | `route()`：辨識語言 → 譯文語言與動作（translate／copy／unsupported／off）；`display_original()`：原文的字形 |
  | `stability.py` | `PartialStability`：即時字幕的穩定前綴（LocalAgreement-2）與「何時重送即時翻譯」 |
  | `metrics.py` | `LevelMeter`（音量 dBFS）、`LatencyWindow`（延遲中位數）、`PipelineMetrics` |
  | `keep_warm.py` | 本機翻譯服務的保溫（閒置時送短請求，讓 Ollama 不卸載模型） |
  | `factory.py` | 依設定建立真實元件（Silero、兩個 Whisper、兩個翻譯器＋術語表＋前文保險、過濾器）；辨識語言 `asr_languages()`；刪除模型 |
  | `filtering.py` | 測試與假模式用的簡易過濾器 |

- **對外介面**：`Pipeline(components, config, on_subtitle=…, on_status=…).start(source) / stop() / wait() / metrics() / set_translation_enabled()`；另有不開執行緒的逐步模式 `feed() / end_of_stream() / run_pending()`，給測試用。元件用完由引擎呼叫 `types.py` 的 `release_components()`。
- **測試**：`tests/test_pipeline.py`（假的 VAD、ASR、翻譯器，假時鐘，不靠 sleep；含路由、穩定前綴、量測）、`tests/test_pipeline_factory.py`、`tests/test_keep_warm.py`。端到端實測：`uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py`。
- **常見改動**
  - 草稿頻率 → 設定檔 `pipeline.partial_interval_ms`（200–5000）。
  - 前文句數 → 設定檔 `pipeline.context_lines`（0–10）。
  - 語言規則 → `languages.py`（規則見 [07](07-languages-and-i18n.md)）。
  - 即時翻譯的重送條件 → `stability.py`（見 [10](10-live-subtitle-stability-and-latency.md)）。
  - 換元件（例如加新 VAD）→ `factory.py` 的 `build_components()`。

## config（設定檔）

- **作用**：讀寫、驗證、遷移 `settings.json`。
- **主要檔案**：`src/kikitori/config/settings.py`（`Settings` 與驗證、`migrate()`）、`store.py`（原子寫入、`SettingsStore`）、`paths.py`（各平台位置）。
- **位置**：Windows `%APPDATA%\kikitori\settings.json`、macOS `~/Library/Application Support/kikitori/`、Linux `~/.config/kikitori/`。環境變數 `KIKITORI_CONFIG_DIR`（絕對路徑）可覆寫，測試都用它。
- **對外介面**：`Settings`、`SettingsStore.get() / update(partial)`、`default_settings_path()`。
- **規則**：未知欄位一律報錯；缺的欄位用預設值；`schema_version` 目前是 3，舊版自動遷移（`MIGRATIONS`：1 → 2 拆開兩條路徑的精度；2 → 3 補 `source_language: "ja"` 與 `translation.enabled: true`）；遷移只在記憶體中，下次存檔才寫回；寫檔先寫暫存檔再 `os.replace`。
- **v0.2.0 的欄位**：`source_language`（可為 `auto`）、`target_language`／`partner_language`（20 種語言任一，口譯時兩者的 Whisper 語言不能相同，`check_interpret_languages()`）、`translation.enabled`、`translation.glossary {builtin, entries}`（不升 schema：新欄位有預設值）。`subtitle` 與 `ui`（介面語系 `ui.locale`、自動存檔、「不要再提醒」）引擎不解讀，原樣保存。
- **測試**：`tests/test_config.py`。
- **常見改動**：加新設定 → `settings.py` 加欄位、驗證、`to_dict()`；有預設值的新欄位不必升版，改到既有欄位的意義才升 `SCHEMA_VERSION` 並寫遷移；前端 `app/src/lib/settings.ts` 也要跟著讀。

## hardware（硬體偵測與推薦）

- **作用**：偵測 OS、CPU、RAM、NVIDIA GPU、cuBLAS、翻譯伺服器；依硬體與語言推薦模型組合；檢查使用者自訂的組合；下載安裝 cuBLAS；替使用者在 Ollama 下載翻譯模型。
- **主要檔案**（`src/kikitori/hardware/`）

  | 檔案 | 角色 |
  |---|---|
  | `detection.py` | `detect()`：組合各項偵測，單項失敗只記在 `errors` |
  | `system.py` | CPU、RAM（只用標準庫與 `ctypes`，不加 psutil） |
  | `nvidia.py` | `nvidia-smi`（5 s 逾時）、找 cuBLAS 檔案 |
  | `translation_server.py` | `GET {base_url}/models`（2 s 逾時） |
  | `recommend.py` | `recommend()`、`validate_combination()`（純函式）；8 GB 以上的候選 `GPU_LADDERS` 與挑法 `_choose_option()` |
  | `messages.py` | 推薦說明、警告、驗證錯誤的訊息碼目錄 `CATALOG`（`{code, params, message}`，UI 依介面語系組字） |
  | `ollama.py` | Ollama 版本檢查與 `POST /api/pull` 下載翻譯模型（可取消、有逾時） |
  | `gpu_runtime.py` | `install_gpu_runtime()`：從 PyPI 下載 `nvidia-cublas-cu12`，只解出兩個 DLL 與授權 |
  | `overview.py` | `models.list` 的總覽 |
  | `types.py` | `HardwareInfo`、`ModelCombination` 等 |

- **推薦等級**：`nvidia_8gb`、`nvidia_4gb`、`nvidia_lt4gb`、`cpu`、`cpu_low`、`apple_silicon`（表見 `docs/specs/hardware.md`）。8 GB 以上：即時 `large-v3-turbo`、完整 `large-v3`，與本機翻譯模型共用顯示卡時依預算選精度（依據見 [08](08-evaluation-methods.md)）。推薦也看語言：泰語、阿拉伯語、印地語的即時路徑改用 `large-v3`；辨識品質是 C 級的語言加提示；翻譯模型要支援這組語言方向。
- **推薦怎麼到使用者手上**：第一次開 app（還沒有設定檔，`settings.get` 的 `is_new`）時 UI 自動套用推薦並說明；之後 UI 用 `settings.preview_recommendation` 問引擎「套用會不會改變設定」（`differs`），會的話提示「有更適合這台電腦的組合」。推薦怎麼變成設定只寫在 `engine/hardware_service.py`（`recommendation_patch()`），UI 的時機判斷與文字在 `app/src/lib/recommend.ts`。
- **測試**：`tests/test_hardware_*.py`（不跑真的 `nvidia-smi`、不連外網；Ollama 用重播真實回應的假伺服器）。
- **常見改動**：調推薦 → `recommend.py`；但依專案規則，應先改 `docs/specs/hardware.md`／`asr.md`／`translate.md` 的依據數據。加訊息碼 → `messages.py` 的 `CATALOG`，再更新 UI 字典（見 [07](07-languages-and-i18n.md)）。

## engine（stdio 引擎）

- **作用**：Tauri 唯一的入口。讀 stdin 的請求、分派、回應；把 pipeline 的字幕與狀態變成事件。
- **主要檔案**（`src/kikitori/engine/`）

  | 檔案 | 角色 |
  |---|---|
  | `main.py` | `run_engine()`：把 `sys.stdout` 換成 stderr，保護協定通道 |
  | `server.py` | `EngineServer`：方法表、session 生命週期（載入、metrics、結束時先釋放模型再回報）、下載、刪除模型 |
  | `protocol.py` | 請求解析、錯誤碼、`ProtocolError` |
  | `io.py` | `MessageWriter`（加鎖、flush）、有長度上限的讀行、`EventLogHandler`（logging → `log` 事件） |
  | `deferred.py` | 慢請求的背景工作執行緒（依序處理） |
  | `hardware_service.py` | 偵測快取 10 秒、推薦組合 ↔ 設定的轉換、預覽（`differs`、指紋） |
  | `services.py` | `EngineServices`：可替換的外部功能 |
  | `testing.py` | 假模式（`KIKITORI_ENGINE_FAKE=1`）：合成聲音、假 ASR／翻譯、假硬體 |

- **對外介面**：協定本身（見 [01](01-system-overview.md) 與 `docs/specs/pipeline-engine.md`）。
- **測試**：`tests/test_engine.py`（真的開子行程跑假模式，交換 NDJSON）、`tests/test_engine_hardware.py`、`tests/test_engine_languages.py`、`tests/test_engine_models.py`（下載、Ollama pull、刪除）、`tests/test_engine_session.py`、`tests/test_engine_capture_errors.py`（錯誤路徑與「不靠 gc 就釋放元件」）、`tests/test_cli.py`。
- **常見改動**：加協定方法 → `server.py` 的 `_handlers` 加一項；Rust `commands.rs` 加對應指令、`build.rs` 產生權限、`capabilities/main.json` 開權限；前端 `api.ts` 與 `validate.ts` 加呼叫與驗證；`app/dev/fake_engine.py` 也要模仿。

## evaluation（評測工具）

- **作用**：產生白噪音／粉紅噪音、把人聲和背景混音、算 VAD 指標（ROC-AUC、漏抓、開始延遲…）。只給實驗用，正式執行不載入。
- **主要檔案**：`src/kikitori/evaluation/noise.py`、`mixing.py`、`metrics.py`、`wav.py`；選單在 `experiments/silero_reset/`（`menu.py`、`musan.py` 下載 MUSAN、`evaluate.py`）。
- **測試**：`tests/test_evaluation.py`、`tests/test_menu.py`（MUSAN 下載用本機假伺服器）。
- **常見改動**：換 VAD 模型或調參數後重跑評測，更新 `experiments/silero_reset/README.md`。

## app／Rust 橋接（`app/src-tauri/src/`）

- **作用**：啟動與看管引擎、請求與回應配對、驗證事件後轉給畫面、字幕視窗控制、全域快速鍵、自動更新、寫字幕紀錄檔與 OBS 文字檔。
- **主要檔案**

  | 檔案 | 角色 |
  |---|---|
  | `engine.rs` | `resolve_engine_command()`（選擇啟動指令）、`EngineBridge::start()`（spawn、`engine.info` 握手、檢查 `protocol_version == 1`）、`RestartTracker`（自動重啟退避） |
  | `connection.rs` | `Connection::request()`：以 `id` 配對、每個請求一個期限；`dispatch()` |
  | `protocol.rs` | `parse_line()`：逐行驗證引擎輸出 |
  | `line_reader.rs` | 每行上限 1 MiB 的讀行器 |
  | `commands.rs` | 每個協定方法一個 Tauri 指令，先驗證參數，依方法給不同逾時（一般 10 s、背景方法 45 s、`session.start` 30 s） |
  | `overlay.rs` | 字幕外觀（含閱讀節奏欄位）、滑鼠穿透、可見狀態、「顯示翻譯」 |
  | `output.rs` | `transcript_save`：把前端產生的匯出內容寫到 `<文件>/kikitori/transcripts/`（檔名白名單、原子寫入）；開啟資料夾 |
  | `obs.rs` | OBS 文字檔：專用執行緒、每秒最多寫 10 次、原子寫入 `<文件>/kikitori/obs/subtitle.txt` |
  | `updater.rs` | 檢查與安裝更新（安裝前先關閉引擎） |
  | `lib.rs` | 外掛、事件轉發（`engine://subtitle`、`engine://metrics` 等）、全域快速鍵 Ctrl+Alt+K／J／U、生命週期 |

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
  | `lib/api.ts` | 呼叫 Rust 指令、訂閱事件、錯誤碼對到字典鍵（`ENGINE_CODES`） |
  | `lib/validate.ts` | 驗證所有事件與指令結果 |
  | `lib/settings.ts`、`lib/combination.ts` | 設定表單與自訂組合的驗證、儲存條件 |
  | `lib/i18n.ts`、`locales/*.ts` | 介面多語系：`t()`、`tp()`、`setLocale()`、`applyStatic()`；13 個語系字典（見 [07](07-languages-and-i18n.md)） |
  | `lib/messages.ts` | 引擎的 `{code, params, message}` 訊息 → 介面語系的句子 |
  | `lib/languages.ts` | 語言選單：選項、篩選、本地檢查、`settings.preview_languages` 的預覽 |
  | `lib/start-downloads.ts` | 按「開始」時下載缺少的辨識與翻譯模型，完成後自動開始 |
  | `lib/translation-toggle.ts`、`lib/translation-guide.ts` | 「顯示翻譯」開關；連不上翻譯服務時的引導 |
  | `lib/stable.ts`、`lib/rhythm.ts`、`lib/metrics.ts` | 穩定前綴的切分、閱讀節奏、音量與延遲（見 [10](10-live-subtitle-stability-and-latency.md)） |
  | `lib/export.ts`、`lib/session-end.ts` | 匯出 SRT／VTT／TXT／CSV；按「停止」後等最後一句再自動存檔 |
  | `lib/glossary.ts` | 術語表編輯器的檢查、CSV 匯入匯出 |
  | `lib/model-delete.ts`、`lib/diagnostics.ts` | 刪除模型的判斷；「複製診斷資訊」（遮蔽秘密） |
  | `styles/tokens.css` | 色票（藍染 `#22305C`、字幕黃 `#F2C230`…），深色模式 |

- **安全**：引擎的文字一律用 `textContent` 顯示，不用 `innerHTML`；有 CSP。
- **測試**：`npm test`（vitest，`app/src/lib/*.test.ts`、`app/src/locales/*.test.ts`）；`npm run build`（`tsc` 型別檢查 + 建置；缺翻譯的字典鍵在這裡就會失敗）。
- **常見改動**：字幕樣式 → `styles/overlay.css`；新設定欄位 → `lib/settings.ts` + `main.ts` 的表單；新介面文字 → 先加到 `locales/zh-TW.ts`，再補其他 12 個語系；引擎錯誤碼的說明 → `api.ts` 的 `ENGINE_CODES` 與字典的 `error.code.*`。
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
- **CI**：任何分支 push 與 PR；除了 `licenses` 只在 Ubuntu 跑，其他都在 Windows／Ubuntu／macOS 三平台：

  | workflow | job | 內容 |
  |---|---|---|
  | `test.yml` | `python` | `uv sync --locked` → unittest → ruff → `sync_version.py check` |
  | `test.yml` | `engine-bundle` | `build.py --no-sidecar --smoke`：建置引擎、掃禁止檔案、自我檢查、假模式協定 |
  | `app.yml` | `checks` | `npm ci` → 型別檢查 → vitest → 建置；`cargo fmt --check`、`cargo clippy -D warnings`、`cargo test` |
  | `app.yml` | `licenses` | Rust 依賴只允許寬鬆授權（`cargo-deny`，`app/src-tauri/deny.toml`）；確認第三方授權聲明產生得出來 |

  第三方 action 都釘在完整 commit SHA；權限只有 `contents: read`。
- **發布**（`.github/workflows/release.yml`，工具在 `scripts/release.py`，設計見 `docs/specs/release-update.md`）：push 到 main 且 `pyproject.toml` 的版本號還沒有 tag 時 → 重用 `test.yml`、`app.yml` → 三平台打包、安裝後冒煙測試、簽章 → `release.py manifest` 產生 `latest.json`、`release.py notes` 產生 Release 說明 → 唯一有寫入權限的 `publish` job 先建 draft Release、上傳全部檔案後才公開。其他分支的 head commit 訊息含 `[release-dry-run]` 時完整試建置，但不建立 Release。
- **自動更新**：`tauri-plugin-updater`，端點是 GitHub Releases 的 `latest.json`（`app/src-tauri/tauri.conf.json`）。公鑰在設定檔，私鑰只在本機與 GitHub Secrets。v0.1.0 起有實際發布可抓；Windows 的安裝與更新已驗收（`experiments/update_acceptance/README.md`）。
