# 05 從哪裡開始

分三部分：先把東西跑起來、再照順序讀 12 個檔案、最後做幾個小練習。
行號對照 `feat/full-app` 分支目前的程式碼，之後改動可能會偏移幾行，用函式名稱搜尋即可。

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

你會看到 `status loading → running`、第 1 句的幾個草稿（`is_final:false`，原文 `partial1`、`partial2`…）、`final1`、第 2 句、最後 `status idle`。
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

`cargo test` 有端到端測試會真的啟動引擎，需要 `uv` 在 PATH。

## 2. 閱讀路線：12 個檔案

順序依照「一句字幕的一生」。每個檔案只看列出的部分即可。

| # | 檔案 | 看什麼 |
|---|---|---|
| 1 | `src/kikitori/cli.py` | `main()`（第 76 行）：`kikitori engine` 與互動選單 `menu()`（第 50 行）的分岔。最短的檔案，熱身用。 |
| 2 | `src/kikitori/engine/main.py` | `run_engine()`（第 35 行）：先保留真正的 stdout，再把 `sys.stdout` 換成 stderr；`KIKITORI_ENGINE_FAKE` 在 `_select_services()` 判斷。 |
| 3 | `src/kikitori/engine/server.py` | `_handlers`（第 134 行）：12 個方法對應哪個函式。接著看 `_session_start()`（第 492 行）與 `_load_session()`（第 539 行）：回應先送出、再在背景載入模型、建 `Pipeline`；`_on_subtitle()`（第 588 行）把字幕變成事件。 |
| 4 | `src/kikitori/pipeline/factory.py` | `build_components()`（第 244 行）：一個 session 用到的全部真實元件。`candidate_languages()`（第 51 行）、`create_translators()`（第 222 行，即時路徑不重試）。 |
| 5 | `src/kikitori/pipeline/pipeline.py` | 整個系統的心臟。依序看：`start()`（第 174 行，為什麼 capture 執行緒最後啟動）、`_capture_loop()`（第 224 行）、`_handle()`（第 322 行，何時觸發草稿）、`_end_utterance()`（第 349 行，裁尾巴）、`_run_partial()`（第 376 行）、`_run_final()`（第 438 行）、`_for_filter()`（第 473 行）、`_emit()`（第 519 行，順序保證）。 |
| 6 | `src/kikitori/pipeline/scheduler.py` | `LatestSlot`（第 27 行）與 `FinalQueue`（第 65 行）。兩個類別合起來不到 100 行，是「草稿可丟、定稿不丟」的實作。 |
| 7 | `src/kikitori/vad/segmenter.py` | `Segmenter.process()`（第 158 行）湊視窗；`_track_candidate()`（第 279 行）開始的判斷；`_advance()`（第 256 行）結束的判斷；`_apply_reset_policy()`（第 240 行）。搭配 `vad/endpoint.py` 的 `PauseEndpoint.decide()`（第 44 行）。 |
| 8 | `src/kikitori/vad/silero.py` | `SileroVad.probability()`（第 138 行）：前接 64 樣本、state 存回去。只有十幾行，但少了它 VAD 就聽不到人聲。 |
| 9 | `src/kikitori/asr/whisper.py` 與 `asr/filtering.py` | `FAST_DECODE`（第 117 行）、`FINAL_DECODE`（第 124 行）、`WhisperTranscriber.transcribe()`（第 209 行）；`filtering.py` 的 `DEFAULT_HALLUCINATIONS`（第 24 行）與 `rejection_reason()`（第 115 行）。 |
| 10 | `src/kikitori/translate/client.py` 與 `translate/prompts.py` | `OpenAICompatibleTranslator.stream()`（第 238 行）、`_connect()`（第 402 行，localhost 先試 IPv4）；`prompts.py` 的 `_GENERIC_TEMPLATES`（第 36 行起）與 `build_messages()`（第 88 行）；`translate/postprocess.py` 的 `finalize_translation()`（第 114 行）。 |
| 11 | `app/src-tauri/src/engine.rs` 與 `connection.rs` | `resolve_engine_command()`（`engine.rs` 第 121 行）；`EngineBridge::start()`（第 364 行，spawn 後先 `engine.info` 握手）；`RestartTracker`（第 182 行）；`connection.rs` 的 `dispatch()`（第 215 行）；`protocol.rs` 的 `parse_line()`（第 172 行）。 |
| 12 | `app/src/lib/subtitles.ts` 與 `app/src/overlay/overlay.ts` | `applySubtitle()`（`subtitles.ts` 第 60 行）：五條字幕規則；`overlay.ts` 的 `render()`（第 45 行）與 `init()`（第 84 行）。主視窗的對應位置是 `app/src/main/main.ts` 的 `toggleSession()`（第 305 行）與 `subscribe()`（第 1353 行）。 |

讀完 12 個檔案後，可選的延伸：

- 聲音擷取：`src/kikitori/capture/devices.py` 的 `open_source()`（第 79 行）、`capture/resample.py` 的 `AudioConverter`（第 155 行）。
- 設定檔：`src/kikitori/config/settings.py` 的 `PipelineSettings`（第 151 行）與 `_default_media()`（第 167 行）。
- 硬體推薦：`src/kikitori/hardware/recommend.py` 的 `recommend()`。

### 讀測試也是好方法

測試把「規則」寫成具體例子，常比程式本身好懂：

- `tests/test_vad_segmenter.py`：例如 `test_start_audio_then_pause_end`、`test_short_speech_does_not_start`。
- `tests/test_asr_filtering.py`：例如 `test_known_hallucinations_match_whole_sentence_only`。
- `tests/test_pipeline.py`：草稿與定稿的順序、過濾、翻譯失敗。
- `app/src/lib/subtitles.test.ts`：畫面上的字幕規則。

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

假設你發現 Whisper 在片尾音樂常輸出「おつかれさまでした」。

1. 在 `src/kikitori/asr/filtering.py` 的 `DEFAULT_SOFT_HALLUCINATIONS` 加入它。真人也常說這句，所以放 soft 清單：只有 `no_speech` > 0.3 時才過濾。
2. 在 `tests/test_asr_filtering.py` 仿照 `test_soft_hallucination_depends_on_no_speech` 加一個測試：`no_speech` 高時被濾掉、低時保留。
3. 確認：`uv run python -m unittest tests.test_asr_filtering`，再跑 `uv run ruff check`。

想一想：為什麼這句不該放進 `DEFAULT_HALLUCINATIONS`（硬清單）？

### 練習 3：跑翻譯基準測試

需要本機 Ollama，並已 `ollama pull qwen3.5:4b`（或其他預設組裡的模型）。

```text
uv run python -I -X utf8 experiments/translate_benchmark/menu.py
```

選 1 列出本機模型，選 3 只測一個模型。結果在 `experiments/translate_benchmark/results/`。
確認：首字延遲、總延遲和 `experiments/translate_benchmark/README.md` 的表格比較；看譯文有沒有簡體字。
進階：改 `translate/prompts.py` 的一條規則再跑一次，比較差異。

### 練習 4：在畫面上看「草稿 → 定稿」

```text
cd app
npm run menu      # 選 4：fake_engine.py
```

1. 按「開始」，字幕視窗會自動出現。觀察：草稿較淡、譯文白色；定稿出現時譯文轉成黃色。
2. 按 Ctrl+Alt+K（macOS ⌘+Option+K）切換滑鼠穿透，確認穿透時框線消失、點擊會落到後面的視窗。
3. 重開選單選 5（故障注入），等 25 秒看狀態列從「引擎意外結束，正在重新啟動」回到正常。
4. 對照程式：字幕規則在 `app/src/lib/subtitles.ts`，重啟退避在 `app/src-tauri/src/engine.rs` 的 `RestartTracker`。

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

它會印出每句的草稿、定稿延遲。和 [02](02-life-of-a-subtitle.md) 的延遲總表比較；如果有 NVIDIA 顯示卡且裝好 cuBLAS，這會是第一筆 GPU 端到端數據。
