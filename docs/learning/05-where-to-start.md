# 05 從哪裡開始

分三部分：先把東西跑起來、再照順序讀 13 個檔案、最後做幾個小練習。
行號對照 `feat/vtuber-models` 分支（v0.2.0）的程式碼，之後改動可能會偏移幾行，用函式名稱搜尋即可。

## 1. 先跑起來

### 準備

```text
uv sync                     # 安裝 Python 依賴（依 uv.lock）
cd app && npm install       # 安裝前端依賴（需要 Node 24）
```

Rust 只有在跑桌面程式或 `cargo test` 時才需要（stable、Windows 用 MSVC）。

### 互動選單

```text
uv run kikitori
```

```text
kikitori
  1) 啟動引擎（stdio NDJSON，給 Tauri 或手動測試）
  2) 列出音訊裝置
  3) 顯示設定檔路徑
  4) 離開
```

選 2 可以看到這台電腦的 loopback 與麥克風，以及每個裝置的 id。

### 假模式引擎：不需要模型、不需要 Ollama

假模式用合成聲音（兩句「人聲」）、假辨識、假翻譯，最適合看協定長什麼樣子。
一定要把設定目錄指到暫存資料夾，否則會寫到你真正的設定檔。

PowerShell：

```powershell
$env:KIKITORI_ENGINE_FAKE = "1"
$env:KIKITORI_CONFIG_DIR = "$env:TEMP\kikitori-play"
uv run kikitori engine
```

Git Bash：

```bash
KIKITORI_ENGINE_FAKE=1 KIKITORI_CONFIG_DIR="$TEMP/kikitori-play" uv run kikitori engine
```

啟動後一行一行貼上（每行按 Enter）：

```text
{"id":1,"method":"engine.info"}
{"id":2,"method":"session.start","params":{"mode":"media"}}
```

你會看到 `status loading → running`、第 1 句的幾個草稿（`is_final:false`，原文 `partial1`、`partial2`…，帶 `stable_chars`）、`final1`（帶 `latency_ms`）、第 2 句、最後 `status idle`。
也可以試試 v0.2.0 的方法：`{"id":3,"method":"languages.list"}`（20 種語言）、`{"id":4,"method":"settings.preview_languages","params":{"source_language":"ko","target_language":"es"}}`（推薦組合與下載大小，不存檔）。
真實輸出的節錄見 [01-system-overview.md](01-system-overview.md)。
結束：輸入 `{"id":9,"method":"engine.shutdown"}`，或按 Ctrl+Z 再 Enter（Windows）／Ctrl+D（macOS、Linux）。

### 桌面程式

```text
cd app
npm run menu
```

選單（`app/dev/launch.mjs`）：

| 選項 | 用途 |
|---|---|
| 1 | 正式引擎的假模式（建議第一次用；假 8 GB NVIDIA 顯示卡、缺 cuBLAS） |
| 2 | 假模式，假硬體 Apple Silicon |
| 3 | 假模式，GPU 元件下載會失敗 |
| 4 | `app/dev/fake_engine.py`：字幕不停產生，草稿 → 定稿最明顯 |
| 5 | 假引擎加故障注入：每 3 句插一行壞資料、25 秒後當機，看自動重啟 |
| 6 | 正式引擎：真的擷取聲音、載入模型（要先下載模型、開 Ollama） |
| 7 | 建置除錯版執行檔 |

### 測試與 lint

```text
uv run python -m unittest discover -s tests
uv run ruff check
cd app && npm test && npm run build
cd app/src-tauri && cargo test && cargo clippy --all-targets -- -D warnings
```

`cargo test` 有端到端測試會真的啟動引擎，需要 `uv` 在 PATH。這些檢查在 CI 也會跑（`test.yml`、`app.yml`）。

## 2. 閱讀路線：13 個檔案

順序依照「一句字幕的一生」。每個檔案只看列出的部分即可。

| # | 檔案 | 看什麼 |
|---|---|---|
| 1 | `src/kikitori/cli.py` | `main()`（第 88 行）：`kikitori engine` 與互動選單 `menu()`（第 51 行）的分岔。最短的檔案，熱身用。 |
| 2 | `src/kikitori/engine/main.py` | `run_engine()`（第 46 行）：先保留真正的 stdout，再把 `sys.stdout` 換成 stderr；`KIKITORI_ENGINE_FAKE` 在 `_select_services()`（第 24 行）判斷。 |
| 3 | `src/kikitori/engine/server.py` | `_handlers`（第 310 行）：18 個方法對應哪個函式。接著看 `_session_start()`（第 915 行）與 `_load_session()`（第 980 行）：回應先送出、再在背景檢查翻譯模型、載入模型、建 `Pipeline`；`_on_subtitle()`（第 1110 行）把字幕變成事件；session 結束時先停 metrics、釋放模型、再送 `idle`（第 1216 行起）。 |
| 4 | `src/kikitori/pipeline/factory.py` | `build_components()`（第 405 行）：一個 session 用到的全部真實元件。`asr_languages()`（第 116 行，辨識語言怎麼決定）、`create_translators()`（第 376 行，即時路徑不重試、術語表、前文保險）。 |
| 5 | `src/kikitori/pipeline/pipeline.py` | 整個系統的心臟。依序看：`start()`（第 242 行，為什麼 capture 執行緒最後啟動）、`_capture_loop()`（第 311 行）、`_handle()`（第 411 行，何時觸發草稿）、`_end_utterance()`（第 438 行，裁尾巴）、`_run_partial()`（第 469 行，穩定前綴與何時重送翻譯）、`_run_final()`（第 562 行）、`_for_filter()`（第 595 行）、`_route()`（第 643 行）、`_emit()`（第 661 行，順序保證）。 |
| 6 | `src/kikitori/pipeline/scheduler.py` | `LatestSlot`（第 29 行）與 `FinalQueue`（第 67 行）。兩個類別合起來約 100 行，是「草稿可丟、定稿不丟」的實作。 |
| 7 | `src/kikitori/vad/segmenter.py` | `Segmenter.process()`（第 158 行）湊視窗；`_track_candidate()`（第 279 行）開始的判斷；`_advance()`（第 256 行）結束的判斷；`_apply_reset_policy()`（第 240 行）。搭配 `vad/endpoint.py` 的 `PauseEndpoint.decide()`（第 44 行）。 |
| 8 | `src/kikitori/vad/silero.py` | `SileroVad.probability()`（第 138 行）：前接 64 樣本、state 存回去。只有十幾行，但少了它 VAD 就聽不到人聲。 |
| 9 | `src/kikitori/asr/whisper.py` 與 `asr/filtering.py` | `FAST_DECODE`（第 131 行）、`FINAL_DECODE`（第 138 行）、`ModelPool`（第 187 行）、`WhisperTranscriber.transcribe()`（第 471 行）；`filtering.py` 的 `DEFAULT_HALLUCINATIONS`（第 74 行，依語言分 hard／soft）與 `rejection_reason()`（第 205 行）。 |
| 10 | `src/kikitori/languages/catalog.py` 與 `pipeline/languages.py` | `LANGUAGES`（`catalog.py` 第 117 行）：20 種語言與兩層語言碼；`route()`（`pipeline/languages.py` 第 42 行）：一句話翻成什麼語言。配合 [07](07-languages-and-i18n.md) 讀。 |
| 11 | `src/kikitori/translate/client.py` 與 `translate/prompts.py` | `OpenAICompatibleTranslator.stream()`（第 265 行）、`_connect()`（第 438 行，localhost 先試 IPv4）；`prompts.py` 的 `_GENERIC_TEMPLATES`（第 155 行起）與 `build_messages()`（第 256 行）；`translate/postprocess.py` 的 `finalize_translation()`（第 210 行）。 |
| 12 | `app/src-tauri/src/engine.rs` 與 `connection.rs` | `resolve_engine_command()`（`engine.rs` 第 144 行）；`EngineBridge::start()`（第 387 行，spawn 後先 `engine.info` 握手）；`RestartTracker`（第 205 行）；`connection.rs` 的 `dispatch()`（第 215 行）；`protocol.rs` 的 `parse_line()`（第 214 行）。 |
| 13 | `app/src/lib/subtitles.ts` 與 `app/src/overlay/overlay.ts` | `applySubtitle()`（`subtitles.ts` 第 64 行）：五條字幕規則；`overlay.ts` 的 `render()`（第 139 行）與 `init()`（第 191 行）。主視窗的對應位置是 `app/src/main/main.ts` 的 `toggleSession()`（第 708 行）與 `subscribe()`（第 3172 行）。 |

讀完這些檔案後，可選的延伸：

- 聲音擷取：`src/kikitori/capture/devices.py` 的 `open_source()`（第 78 行）、`capture/resample.py` 的 `AudioConverter`（第 155 行）。
- 設定檔：`src/kikitori/config/settings.py` 的 `PipelineSettings`（第 194 行）與 `_default_media()`（第 210 行）。
- 硬體推薦：`src/kikitori/hardware/recommend.py` 的 `recommend()` 與 `_choose_option()`。
- 介面多語系：`app/src/lib/i18n.ts` 與 `app/src/locales/types.ts`（見 [07](07-languages-and-i18n.md)）。
- 即時字幕的穩定前綴：`src/kikitori/pipeline/stability.py`（約 80 行，見 [10](10-live-subtitle-stability-and-latency.md)）。

### 讀測試也是好方法

測試把「規則」寫成具體例子，常比程式本身好懂：

- `tests/test_vad_segmenter.py`：例如 `test_start_audio_then_pause_end`、`test_short_speech_does_not_start`。
- `tests/test_asr_filtering.py`：例如 `test_known_hallucinations_match_whole_sentence_only`、`test_soft_phrases_apply_only_to_their_own_language`。
- `tests/test_pipeline.py`：草稿與定稿的順序、過濾、翻譯失敗、語言路由、穩定前綴。
- `tests/test_languages.py`：語言碼的正規化與別名。
- `app/src/lib/subtitles.test.ts`：畫面上的字幕規則；`app/src/lib/stable.test.ts`：穩定前綴怎麼切（含 emoji 與組合字元）。

## 3. 練習

每個練習都附「怎麼確認」。做完記得還原，或在分支上做。

### 練習 1：改句尾停頓，看句子怎麼被切（不改程式）

假模式的聲音兩句之間有 1.0 秒靜音。預設 `pause_secs` 是 0.6，所以切成兩句。

在假模式引擎（見上面）依序貼上：

```text
{"id":1,"method":"settings.set","params":{"media":{"pause_secs":1.5}}}
{"id":2,"method":"session.start","params":{"mode":"media"}}
```

- 預期：只有一句。`final1` 的 `end_sample` 是 83200（整段 5.2 秒），因為最後靠串流結束（`flush`）收尾，不裁尾巴。
- 對照：預設 0.6 時是兩句，`final1` 的 `end_sample` 是 35584、`final2` 是 70912。
- 本指南撰寫時實際跑過，兩種結果都如上。
- 想想看：`pause_secs` 越長，句子越完整，但定稿越晚出現。真實影片該怎麼取捨？

### 練習 2：加一句幻覺句

假設你發現 Whisper 在日語片尾音樂常輸出「おつかれさまでした」。

1. 在 `experiments/hallucination_lexicon/lexicon.json` 的 `languages.ja.soft` 加入它。真人也常說這句，所以放 **soft**：只有辨識語言是日語、而且 `no_speech` > 0.3 時才過濾。
2. 用 `uv run python -I experiments/hallucination_lexicon/menu.py` 的第 4 項重新產生 `src/kikitori/asr/hallucination_lexicon.py`（不要手改這個檔案）。
3. 在 `tests/test_asr_filtering.py` 仿照 `test_soft_hallucination_depends_on_no_speech` 加一個測試：`no_speech` 高時被濾掉、低時保留、辨識成其他語言時保留。
4. 確認：`uv run python -m unittest tests.test_asr_filtering tests.test_asr_hallucination_lexicon`，再跑 `uv run ruff check`。

想一想：為什麼這句不該放進 **hard**（不論辨識語言、不看 `no_speech` 都濾）？

### 練習 3：跑翻譯基準測試

需要本機 Ollama，並已 `ollama pull qwen3.5:4b`（或其他預設組裡的模型）。

```text
uv run python -I -X utf8 experiments/translate_benchmark/menu.py
```

選 1 列出本機模型，選 3 只測一個模型。結果在 `experiments/translate_benchmark/results/`。
確認：首字延遲、總延遲和 `experiments/translate_benchmark/README.md` 的表格比較；看譯文有沒有簡體字。
進階：改 `translate/prompts.py` 的一條規則再跑一次，比較差異。要判斷品質是否「顯著」變好，用有參考譯文的 `experiments/translate_eval/`（方法見 [08](08-evaluation-methods.md)）。

### 練習 3b：換一組語言，看推薦怎麼變（不改程式）

在假模式引擎依序貼上：

```text
{"id":1,"method":"settings.preview_languages","params":{"source_language":"th","target_language":"en"}}
{"id":2,"method":"settings.preview_languages","params":{"source_language":"zh-CN","target_language":"zh-TW"}}
```

- 第一行：收聽泰語。`recommendation.warnings` 會有 `asr_language_quality_low`（推薦的完整模型對泰語是 C 級，錯誤率 > 20%）。假硬體缺 cuBLAS，所以推薦是 CPU 組合 base／small。
- 第二行：收聽與字幕是同一個 Whisper 語言，`translation_needed` 是 false：不需要翻譯模型，只做繁簡轉換。
- 兩次都不會改設定檔、不會下載（`settings.get` 確認）。

### 練習 4：在畫面上看「草稿 → 定稿」

```text
cd app
npm run menu      # 選 4：fake_engine.py
```

1. 按「開始」，字幕視窗會自動出現。觀察：草稿較淡、譯文白色；定稿出現時譯文轉成黃色。
2. 按 Ctrl+Alt+K（macOS ⌘+Option+K）切換滑鼠穿透，確認穿透時框線消失、點擊會落到後面的視窗。再試 Ctrl+Alt+J（切換顯示翻譯：原文變成主要字幕）與 Ctrl+Alt+U（清除字幕視窗，字幕紀錄不受影響）。
3. 到設定頁最上方切換介面語言，確認主視窗與字幕視窗的文字都換掉。
4. 重開選單選 5（故障注入），等 25 秒看狀態列從「引擎意外結束，正在重新啟動」回到正常。
5. 對照程式：字幕規則在 `app/src/lib/subtitles.ts`，重啟退避在 `app/src-tauri/src/engine.rs` 的 `RestartTracker`，快速鍵在 `app/src-tauri/src/lib.rs`，介面文字在 `app/src/locales/`。

### 練習 5：錄 5 秒系統聲音

```text
uv run python experiments/capture_check/check_capture.py
```

選 3（預設 loopback），一邊播影片一邊錄。
確認：樣本數 ≈ 秒數 × 16000、`dropped_samples=0`；用播放器聽 `experiments/capture_check/data/` 裡的 WAV，速度與音高正常。
再試一次「不播放任何聲音」：樣本數應該仍然正確（WASAPI 靜音補零）。

### 練習 6（進階）：用真模型跑端到端

需要先下載 ASR 模型（app 模型頁，或 `experiments/asr_benchmark/menu.py` 選 1）、開 Ollama，並準備一段本機 WAV。

```text
uv run python -I -X utf8 experiments/pipeline_smoke/smoke.py
```

它會印出每句的草稿、定稿延遲。和 [02](02-life-of-a-subtitle.md) 的延遲總表比較。GPU 端到端目前只有 v0.1.0 組合的數字；如果有 NVIDIA 顯示卡、用 v0.2.0 的推薦組合（`large-v3-turbo`／`large-v3`）跑，就是新組合的第一筆數據。
