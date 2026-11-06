# 驗收報告：v0.2.0（feat/vtuber-models）

> 分支 `feat/vtuber-models`，HEAD `d6bbdaa`＋本次驗收的未 commit 修改（見「驗收中找到並修正的問題」）。日期：2026-11-06（02:59–06:15，無人值守）。
> v0.1.0 的報告見 `tasks/acceptance-report.md`；系統怎麼運作請看 `docs/learning/`，各模組細節看 `docs/specs/`。
> 後續（同日）：驗收找到的修正已依建議切分 commit（`3fb6d7f`、`d370718`）；第 6 節的 WebView2 記憶體成長已找到根因並修正（`fa6748d`），
> 「按開始到聆聽 10–13 秒」改成兩個模型並行載入（`78c79d1`），見第 6 節最後與「觀察到、沒有修的」。

## 環境

| 項目 | 值 |
|---|---|
| 機器 | AMD Ryzen 7 5700X（8 核）、64 GB、RTX 5070 12 GB（12227 MiB，WDDM，驅動 621.01）、Windows 11 Pro 26200、螢幕縮放 125% |
| WebView2 | 156.0.4314.58 |
| 工具 | uv 0.13.4、Node 24.20.0、cargo 1.99.1、Ollama 0.40.14（已安裝 qwen3.5:9b、qwen3.5:4b、hy-mt1.5-7b、hy-mt1.5-1.8b、Sakura 7B） |
| WebDriver（只放 session 暫存目錄，未加入專案依賴） | tauri-driver 2.1.0（`cargo install tauri-driver --version 2.1.0 --locked --root <暫存>`）；msedgedriver 156.0.4314.58（`https://msedgedriver.microsoft.com/156.0.4314.58/edgedriver_win64.zip`，SHA-256 `2fd5acb3e5d8a5e35e0281f59d31292f261f21ba994fe025370331d522042ab5`，Authenticode 有效，Microsoft Corporation） |
| 聲音 | 測試聲音只播到 **Steam Streaming Speakers**（虛擬喇叭），引擎擷取它的 loopback |
| 隔離 | 每個測試都用自己的 `KIKITORI_CONFIG_DIR`；需要空模型快取的情境把 `LOCALAPPDATA` 指到結果資料夾。沒有讀寫使用者的設定、`.env`、模型快取（前後比對相同）；沒有刪除任何 Ollama 模型 |
| 開始前 | `nvidia-smi`：989 MiB、沒有其他 compute 行程（沒有殘留的實驗行程，不需要結束任何行程） |
| 同時在改的 | 另一個代理人只改文件；工作目錄裡 `scripts/release.py`、`tests/test_release.py`、`docs/specs/{pipeline-engine,release-update,competitive-features}.md` 的修改**不是本次驗收做的** |

## 總覽

| 步驟 | 結果 |
|---|---|
| 0. 版本升到 0.2.0 | PASS（先修 `sync_version.py` 在 CRLF 檔案上失敗的 bug） |
| 1. 測試閘門 | PASS（修正後全部重跑仍 PASS） |
| 2. 正式版建置（引擎 one-folder＋app＋NSIS 安裝檔）與引擎自我檢查 | PASS |
| 3. 引擎端到端（dev／release）＋新推薦組合的 VRAM | PASS：引擎 3.2–3.6 GB＋Ollama 7.07 GB、整張卡最高 10.8／12.2 GB、Ollama `size == size_vram`（沒有放到 CPU） |
| 4. 記憶體壓力測試（完整規模 1–6） | 三個舊洩漏都已消失；找到並修正模型池上限（換組合時 GPU 仍漏）；情境 2 私有記憶體判定 FAIL 但 5 輪後確認持平（heap 高水位），執行緒門檻偶爾超出（暫時性） |
| 5. GUI 驗收（a–j） | 全部 PASS（i 的快速鍵以 `keybd_event` 實際送出） |
| 6. GUI 長時間（情境 7，18 分鐘） | 驗收時**部分 PASS**（主視窗 WebView2 renderer +25–31 MiB／分）；**後續已修正**：根因是字幕紀錄全部列一起重排，`content-visibility` 後 22 分鐘 CDP soak +0.67 MiB／分（門檻 1），PASS |

## 0. 版本 0.2.0

```text
uv run python scripts/sync_version.py show      → 六個檔案都是 0.1.0
uv run python scripts/sync_version.py bump minor → error: no [project] section   ← bug
（修正後）uv run python scripts/sync_version.py bump minor → pyproject.toml and app files -> 0.2.0；uv.lock updated
uv run python scripts/sync_version.py check     → all versions match 0.2.0
```

改到的版本檔：`pyproject.toml`、`uv.lock`（以 `uv lock`）、`app/package.json`、`app/package-lock.json`、`app/src-tauri/{Cargo.toml,Cargo.lock,tauri.conf.json}`。

## 1. 測試閘門

| 指令 | 第一次 | 修正後最後一次 |
|---|---|---|
| `uv run python -m unittest discover -s tests` | 1415 個 OK | **1425 個 OK** |
| `uv run ruff check`、`uv run ruff format --check src tests` | All checks passed、134 files formatted | 同左 |
| `cd app && npm test` | 23 檔 1307 個 PASS | **24 檔 1310 個 PASS**（新增 `dom.test.ts`） |
| `npx tsc --noEmit`、`npm run build` | OK | OK |
| `cd app/src-tauri && cargo test` | 88 個 OK | （Rust 沒有改動） |
| `cargo clippy --all-targets -- -D warnings`、`cargo fmt --check` | OK | — |

## 2. 正式版建置

不碰共用的 `dist/`、`app/src-tauri/binaries/`：

```text
# 引擎：packaging/engine/build.py 的 build()＋smoke()，輸出到驗收資料夾、工作目錄在暫存
build("onedir", paths=paths_for("onedir", dist_root=experiments/e2e_acceptance/data/build-v020, work_root=<暫存>/work))
# app：resources 指到 data/build-v020/{kikitori-engine,licenses}，自己的 CARGO_TARGET_DIR，關掉 updater artifacts（沒有簽章私鑰）
CARGO_TARGET_DIR=<暫存>/target npm run tauri build -- --bundles nsis --config <暫存>/tauri.e2e.conf.json
```

- 引擎：170.3 MB、PyInstaller 14–26 s；自我檢查 `version 0.2.0`、silero v6.2.3、onnxruntime 1.31.1、ctranslate2 4.8.5、opencc、pyaudiowpatch 都 OK；
  協定冒煙測試 `startup 0.31 s, exit 0`。
- app：`kikitori-app.exe` 與 NSIS 安裝檔 `kikitori_0.2.0_x64-setup.exe`（40.98 MiB）。修正後重建了三次（推薦組合、模型池、語系字典），GUI 驗收用最後一次。
- `licenses/` 沿用 v0.1.0 驗收時產生的副本（這台電腦沒有 cargo-about）；安裝檔的授權聲明內容以 CI 為準。

## 3. 引擎端到端（真擷取、真 Whisper GPU、真 Ollama qwen3.5:9b）

```text
uv run python -X utf8 experiments/e2e_acceptance/acceptance.py --batch release   （KIKITORI_E2E_RELEASE_ENGINE=data/build-v020/...）
uv run python -X utf8 experiments/e2e_acceptance/acceptance.py --batch dev
```

`acceptance.py` 新增：推薦組合檢查、每個行程的顯示記憶體（沿用 `memory_soak/monitor.py` 的 WDDM 計數器）、Ollama `/api/ps` 的 `size`／`size_vram`、
`metrics` 事件與完整字幕的 `latency_ms`、即時字幕的 `stable_chars`。

| 項目 | release（`results/20261106-031706`） | dev（`results/20261106-032008`） |
|---|---|---|
| 總結 | **PASS**（16 項） | **PASS** |
| `settings.apply_recommendation`（隔離設定） | 即時 large-v3-turbo int8_float16、完整 large-v3 int8_float16、翻譯 qwen3.5:9b（翻譯預算 8.13 GB） | 同左 |
| 影片翻譯（anime＋vtuber＋BGM＋噪音） | 12/12 段涵蓋、14 句、281 則即時字幕、依序、先即時後完整、簡體 0、靜音／配樂誤觸發 0 | 15 句、287 則 |
| 口譯（TTS 中英） | 6/6 段、8 句、方向與正體都正確 | 同左 |
| session 載入（`session.start` → running） | 影片翻譯 13.1 s（含 Ollama 冷啟動）、口譯 11.9 s | 13.3／10.6 s |
| `session.stop` → idle；結束碼；非 JSON 行 | 0.16／0.14 s；0；0 | 0.09／0.14 s；0；0 |

**VRAM（新推薦組合，影片翻譯 session 期間）**

| | release | dev |
|---|---|---|
| 引擎（dedicated＋shared）中位數／最高 | 3215.5／3567.5 MiB | 3215.5／3567.5 MiB |
| Ollama（`ollama.exe`＋`llama-server.exe`）| 7071.2 MiB | 7073.2 MiB |
| 整張卡（nvidia-smi，含桌面約 1 GB）中位數／最高 | 10527／**10815** MiB（共 12227） | 10529／10790 MiB |
| Ollama `/api/ps`（qwen3.5:9b，context 4096） | `size` 5,606,337,412 ＝ `size_vram` 5,606,337,412 → **沒有放到 CPU** | 同左 |

- 「Shared Usage」計數器：引擎 76 MiB、Ollama 662 MiB——這是 pinned（page-locked）主記憶體，CTranslate2 與 llama.cpp 本來就會配置，
  不是顯示記憶體不夠（整張卡還有約 1.4 GB 空間）。第一次執行時我把它當成判定條件而誤判 FAIL，已改成看 `size == size_vram` 與整張卡餘裕（≥ 512 MiB）。
- 估算對照：`hardware.md` 估 ASR 2880 MiB、9B 權重 6.6 GB；實測引擎 3.2–3.6 GB、Ollama 7.07 GB，合計約 10.3–10.6 GB＋桌面，在 12 GB 內。

**延遲**（中位數／P90）

| | 首個即時字幕 | 即時字幕延遲 | 完整字幕（從說完，牆鐘） | 引擎回報的 `latency_ms`（完整字幕） | `metrics.latency_ms_median`（最後一則） |
|---|---|---|---|---|---|
| 影片翻譯 release | 0.95／0.98 s | 0.31／0.44 s | 0.92／1.41 s（n=9） | **578／1073 ms**（n=14） | 578 ms |
| 口譯 release | 0.99／1.02 s | 0.33／0.42 s | 0.94／1.08 s | **539／680 ms**（n=8） | 539 ms |
| 影片翻譯 dev | 0.94／0.96 s | 0.29／0.43 s | 0.89／1.35 s | 563／1003 ms | — |
| 口譯 dev | 0.97／0.98 s | 0.31／0.40 s | 0.92／1.09 s | 524／664 ms | — |

- 每則即時字幕都有 `stable_chars`（281/281；其中 238 則 > 0），每則完整字幕都有 `latency_ms`；`metrics` 每秒一則（108 則）、`backlog` 最高 0、音量 −120 到 −19 dBFS。
- 與 v0.1.0（small／turbo f16）比：即時字幕延遲 0.33 → 0.31 s 持平；完整字幕從說完 0.69 → 0.92 s（large-v3 比 turbo 慢）；**session 載入 2.8–8.6 s → 10.6–13.3 s**。
  單獨量測載入＋暖機（同一台、GPU 閒置）：small f16 1.47 s、turbo f16 1.75 s、**turbo int8_float16 3.42 s、large-v3 f16 3.89 s、large-v3 int8_float16 6.5–6.7 s**——
  快取裡的模型是 float16，int8_float16 要在載入時量化，所以按「開始」到開始聆聽多等約 7–10 秒（見「需要你決定」）。

## 4. 記憶體壓力測試（完整規模）

```text
uv run python -X utf8 experiments/memory_soak/menu.py --batch start-stop cycles=20
uv run python -X utf8 experiments/memory_soak/menu.py --batch switch rounds=3
uv run python -X utf8 experiments/memory_soak/menu.py --batch translation toggles=20
uv run python -X utf8 experiments/memory_soak/menu.py --batch long minutes=20
uv run python -X utf8 experiments/memory_soak/menu.py --batch downloads pull_model=qwen3.5:4b pulls=5
uv run python -X utf8 experiments/memory_soak/menu.py --batch errors rounds=3
```

開發版引擎、`small`／`large-v3-turbo` float16＋`qwen3.5:4b`（情境的預設；與 README 第一次的數字可直接比較）。GPU 上沒有其他實驗。
完整數字與判定在 `experiments/memory_soak/README.md`「修正後的回歸」。

| 情境 | 結果 | 重點 |
|---|---|---|
| 1 start/stop × 20 | 洩漏全 PASS；執行緒門檻第一次 FAIL、重跑 **PASS** | 顯示記憶體 **+16.8 → +0.41／+1.47 MiB／輪**；私有 **+18.4 → +3.35／+3.08 MiB／輪**（門檻 4）；每輪 0.2–1.8 s 內回到底（239–277 MiB）；20/20 有字幕 |
| 2 換語言／模型 × 3 輪 | FAIL → 找到並修正 #5 → 顯示記憶體 PASS | 修正前顯示記憶體 +10.7 MiB／session（池每次都丟掉物件）；修正後 **+1.24**、第 4 個 session 起固定 275.5 MiB；私有記憶體仍 FAIL（見下） |
| 3 翻譯切換 × 20、只顯示原文、保溫 | **PASS** | 保溫每 10 s；關閉／停止後／只顯示原文的 session 0 個請求；重新開啟 0.02 s 預熱；兩個 session 後顯示記憶體相同 |
| 4 長時間 20 分鐘 | **PASS** | 217 句全部有譯文；私有記憶體四分位中位數 4485／4517／4485／4485 MiB；顯示記憶體 2993.5 MiB 不變；結束後 +45 MiB（門檻 64；舊 #4「+180 MiB」不成立） |
| 5 下載＋pull | **PASS** | 下載中 shutdown 0.1 s、保留 `.part`、續傳＋重複 4 次都完成、handle 持平；**qwen3.5:4b（已安裝）pull × 5** 都完成、digest 不變；**pull 途中 shutdown** 0.05 s、`PullCancelledError`、無殘留，重啟後重試完成 |
| 6 錯誤路徑 × 3、引擎重啟 | **PASS** | **開裝置失敗之後顯示記憶體 0 → 239.5 MiB**（修正前 0 → 3024）；shutdown 0.33 s、EOF 0.23 s、強制結束都沒有殘留行程 |

**三個舊問題**：(1) 開裝置失敗立即釋放 GPU — 已修正（情境 6）；(2) 每個 session 的 GPU／私有記憶體成長 — 同一組合已修正（情境 1），
換組合另有 #5（已修正）；(3) `Connection: close` — `47b0b2f` 已修正並有單元測試，計數代理下 pull 與模型檢查都正常（本次沒有再用會關連線的代理實測）。

**未定論（未修）**

- 情境 2（修正後）的私有記憶體：`rounds=3` 時斜率 12.6 MiB／session（被最後一點主導）；另跑 `rounds=5`（`20261106-050607-s2-switch`，20 個 session）：
  1489 → 1548 → 1590 → **第 12–20 個 session 1639–1646 MiB 持平**——是換不同大小的模型時 heap 的高水位，到某個值就停，不是每個 session 的洩漏
  （判定仍是 FAIL：斜率 7.7 MiB／session 用全部點計算）。顯示記憶體 255 → 275 → 307 → 311 MiB（兩次 32 MiB 的配置器跳階，斜率 3.1，PASS）。
- 執行緒：情境 1 第一次有 4 輪是 35（中位數 32、門檻 +2），之後回到 31–32、handle 持平；重跑最高 34。情境 2 重跑 35–39：池裡最多 6 個物件、
  每個有一條 CTranslate2 工作執行緒。都不像洩漏，門檻沒有改。

## 5. GUI 驗收（WebDriver，正式版建置）

```text
uv run python -X utf8 experiments/e2e_acceptance/gui_acceptance.py --batch v020-ui|v020-languages|v020-original|v020-session|first-run
（環境變數 KIKITORI_E2E_APP=<暫存>/target/release/kikitori-app.exe、KIKITORI_E2E_TAURI_DRIVER、KIKITORI_E2E_EDGEDRIVER）
```

新情境寫在 `experiments/e2e_acceptance/gui_v020.py`，由 `gui_acceptance.py` 的批次與選單 6–9 呼叫。

| | 情境 | 結果（`results/…`） | 重點 |
|---|---|---|---|
| a | 13 個介面語系 | **PASS**（`20261106-044012-v020-ui`） | 選單＝跟隨系統＋13 個；每個語系逐頁（字幕紀錄、模型、設定）與字幕視窗：空白文字 0、原始字典鍵 0（比對 zh-TW 字典的全部鍵，含 title／placeholder／aria-label）；`<html lang>` 正確（zh-Hant-TW、zh-Hans-CN、pt-BR…）；13 組導覽文字都不同；`ui.locale` 都寫入；字幕視窗標題跟著換；最後切回「跟隨系統」 |
| b | 語言選單 ko → es、開始前下載 | **PASS**（`20261106-043747-v020-languages`） | 隔離的模型快取（沒有 cuBLAS → CPU 組合）：預覽「即時 base（第一次使用時下載，約 148 MB）、完整 small（約 486 MB）、qwen3.5:9b（已安裝）、共約 634 MB」；選語言與儲存後模型資料夾仍是空的；按「開始」→ 下載面板（base、small 兩列，197 次更新）→ 107 s 後自己開始聆聽 |
| c | 只顯示原文、翻譯服務連不上 | **PASS**（`20261106-043457-v020-original`） | 翻譯服務指到只收連線不回應的本機埠：開始可用；字幕視窗每句 `is-original-only`、原文用譯文字級（32 px）、沒有譯文元素；該埠只收到啟動時的 `GET /v1/models`，**0 個翻譯請求**；執行中打開「顯示翻譯」→ 引導訊息「連不上翻譯服務…」＋「安裝 Ollama」「只顯示原文」 |
| d | 匯出與自動存檔 | **PASS**（`20261106-043358-v020-session`） | SRT（CRLF、`-->`）、VTT（`WEBVTT`）、TXT、CSV（BOM、標頭）四個檔案在 `文件\kikitori\transcripts`；停止後自動存 SRT＋TXT。測完只刪本測試建立的檔案（`文件\kikitori` 原本不存在，整個移除） |
| e | OBS 文字檔 | **PASS** | 播放中 `文件\kikitori\obs\subtitle.txt` 更新 31 次（原文＋譯文）；停止後清空 |
| f | 術語表編輯器 | **PASS**（`…-v020-ui`） | 重複原文標在第 4 列原文欄「和第 1 列重複（同一個字幕語言）」、開頭空白標在第 5 列，設定檔不變；刪掉錯的列後儲存 3 筆；匯出 CSV（BOM、`'=危険`、`'+注意` 公式防護）→ 清空 → 匯入 3 筆 → 再儲存，內容完全相同 |
| g | 刪除模型（隔離快取） | **PASS** | 「要刪除 base 嗎？可釋放 148 MB。目前設定用它做即時的辨識，下次開始時會重新下載。」→ 確定 →「已刪除 base，釋放了 148 MB。」、資料夾消失；使用者的模型快取前後相同 |
| h | 穩定前綴、音量表、延遲 | **PASS** | 字幕紀錄與字幕視窗的即時字幕都分成穩定（`Thank you for`）與淡色尾巴（` coming to`，opacity 0.5）；音量表在聆聽期間一直顯示（−120 到 −28 dBFS）；「延遲 0.5 秒」；停止後隱藏 |
| i | 全域快速鍵 | **PASS** | 以 `keybd_event` 送出：Ctrl+Alt+U 清空字幕視窗（2 → 0 句）與 OBS 檔，字幕紀錄不變；Ctrl+Alt+J 關閉再打開「顯示翻譯」，設定檔的 `translation.enabled` 跟著變 |
| j | 第一次啟動的推薦 | **PASS**（`20261106-032432-first-run`） | 空設定 0.9 s 後「已依這台電腦（NVIDIA GeForce RTX 5070，12 GB）套用建議組合：即時 large-v3-turbo、完整 large-v3（GPU），翻譯 qwen3.5:9b。」；設定 int8_float16×2；再啟動不重複套用、設定檔不變 |

截圖（都看過，只有本專案的介面文字與本專案寫的英文 TTS 句子）：`20-locale-{zh-TW,en,ja}.png`、`21-glossary.png`、`22-language-preview-ko-es.png`、
`23-start-downloads.png`、`24-model-delete-confirm.png`、`25-original-only.png`、`26-translation-guide.png`、`27-stable-prefix-and-meter.png`、
`28-export-menu.png`；`10–12` 以 v0.2.0 的推薦組合文字更新。

GUI 驗收過程中修的是**測試腳本**（不是產品）：SRT 以 `read_text` 讀會把 CRLF 轉掉；`data-state=running` 在載入模型時就成立，改等「正在聆聽」；
刪除按鈕要等模型清單重新整理；英文 TTS 的 session 改用 `source_language=en`（`ja` 時 Whisper 會把英文「翻」成日文）。

## 6. GUI 長時間（情境 7）

```text
uv run python -X utf8 experiments/memory_soak/menu.py --batch gui minutes=18            （同上環境變數）
uv run python -X utf8 experiments/memory_soak/menu.py --batch gui minutes=12 toggles=false   （診斷：不切換）
```

第一次（`20261106-044107-s7-gui`）2 分鐘時中止：WebDriver 原生點擊「顯示翻譯」被開關的樣式擋住（`element click intercepted`），改成 JS `click()`（測試腳本）。

**修正前（`20261106-044743-s7-gui`，18 分鐘，判定 PASS 但 WebView2 沒有持平）**

| 指標 | 值 |
|---|---|
| 字幕紀錄 | 最多 208 列（13 句／分，18 分鐘到不了 300 列上限） |
| 主視窗 DOM | 638 → 2253 節點，與列數無關的殘差 −0.83 節點／分（8.2 節點／列）；JS heap 中位數 4.5 MiB、不成長 |
| 字幕視窗 DOM | 18–27 節點 |
| `kikitori-app.exe` 私有記憶體 | 10.4 → 10.3 MiB（−0.008 MiB／分） |
| 引擎 | 4548 → 4550 MiB（執行中）、handle 持平 |
| **WebView2（7 個子行程合計）** | **318 → 802 MiB（+30.8 MiB／分）**；幾乎全部在主視窗的 renderer（41 → 574 MiB），其他 6 個行程持平 |
| 結束後 | 沒有殘留行程；翻譯請求 391 個 |

診斷（修正前的建置）：
- 不切換、只讓字幕流動 12 分鐘（`20261106-051544-s7-gui`）：同一個 renderer 99 → 193 MiB（約 8.5 MiB／分，134 列），**按停止後掉回 89 MiB**——是可回收的記憶體，不是永久洩漏。
- 逐項操作（暫存的 `webview_diag.py`，58–100 列時）：「顯示翻譯」切換 15 次 +18 MiB、介面語系切換 6 次 +10 MiB、字幕視窗隱藏／顯示 15 次 ≈ 0；停止後 129 → 76 MiB。
- 原因：主視窗每收到一則字幕事件（每秒數則）就重畫**全部**列，`updateLineElement()` 對沒有變的列也重新指定 `textContent`、`title`、`lang`，
  每次都產生新的文字節點；幾百列 × 每秒數次的垃圾讓 renderer 的 Blink heap 一直長，GC 才會收回。V8 heap（`performance.memory`）不含這些，所以看起來持平。

**嘗試的修正**（`app/src/lib/dom.ts` 的 `setIfChanged()`，只在值改變時寫入；`updateLineElement()` 全部改用它；`dom.test.ts` 先失敗再通過）
後重跑 18 分鐘（`20261106-054144-s7-gui`）：

| 指標 | 修正前 | 修正後 |
|---|---|---|
| 字幕紀錄最多 | 208 列 | 197 列 |
| 主視窗 DOM 殘差 | −0.83 節點／分 | −0.85 節點／分 |
| `kikitori-app.exe` | −0.008 MiB／分 | +0.095 MiB／分（9.0 → 10.2） |
| WebView2 合計 | 318 → 802 MiB（+30.8 MiB／分） | **321 → 658 MiB（+25.3 MiB／分）** |
| 主視窗 renderer | 41 → 574 MiB | 44 → 434 MiB；停止後 **118 MiB** |
| 殘留行程 | 0 | 判定 FAIL，但列出的是 `grep.exe`、`tail.exe`——是我在另一個 shell 輪詢結果的指令，PID 被重用而被算進行程樹（量測假象；app、WebView2、引擎、driver 都已結束） |

**結論：GUI 長時間 — 部分 PASS。** app 本身、DOM、字幕視窗、引擎都持平；**主視窗的 WebView2 renderer 沒有持平**（18 分鐘內 +300–500 MiB，
跳階出現在切換「顯示翻譯」／介面語系之後），停止 session 後大部分會回收（434 → 118 MiB），所以是 GC 延後回收的記憶體，不是永久洩漏；
但直播時 session 一直在跑，可能長到數 GB 才被回收。`setIfChanged()` 只降低約 18%，**根因沒有找到**。下一步建議：
(1) 跑 30 分鐘以上（到 300 列上限）看會不會自己停在某個值；(2) 用 WebView2 DevTools（Memory → heap snapshot、`chrome://tracing` 的
blink_gc／PartitionAlloc）看是哪一類物件；(3) 檢查切換翻譯時 `renderTranscript()`／`renderModeNotes()` 以外還重畫了什麼。

**後續修正（`fa6748d`，方法與數字見 `experiments/memory_soak/README.md`「CDP soak」）**

- 根因：字幕紀錄每一列（含畫面外）都參與排版與中日文塑形；切換「顯示翻譯」或介面語系時最多 300 列一起重排，這些原生記憶體
  （memory-infra 傾印裡不屬於 v8／blink_gc／malloc 任何配置器）要很久才放掉，每切換一次疊一階（+57–100 MiB）。JS heap GC 後一直 2.3 MiB、
  監聽器固定 70 個、detached 節點 0。二分：`#transcript { display:none }` 時 6 次切換 48 → 48 MiB；原本 87 → 200／247 MiB。
- 修正：`.utt` 加 `content-visibility: auto`（最新 3 列除外，跟著捲到底才不停在半途）；音量表改 `transform: scaleX()`；每秒的狀態列只寫入改變的值。
- 結果（除錯版＋假引擎，約 3.4 則即時字幕／秒，切換翻譯、字幕視窗、介面語系）：

| 執行 | 主視窗 renderer | 最後 10 分鐘斜率 |
|---|---|---|
| 修正前，12 分鐘 | 39.9 → 197.5 MiB（8 分鐘時一次 +99 MiB） | +19.1 MiB／分 |
| 修正後，22 分鐘 | 40.3 → 83.7 MiB，停止後 83.7 | **+0.67 MiB／分，PASS** |
| 修正後，每次取樣強制 GC，22 分鐘 | 40.3 → 69.9 MiB | +0.43 MiB／分，PASS |

- 未重跑：正式版建置＋真引擎的情境 7（量測用除錯版＋假引擎）。

## 驗收中找到並修正的問題（都先寫重現測試、確認失敗再修；未 commit）

| # | 問題 | 原因 | 修正 | 測試 |
|---|---|---|---|---|
| 1 | `sync_version.py bump` 在 Windows 失敗：`no [project] section` | git `core.autocrlf=true` 把 TOML checkout 成 CRLF，`(?m)$` 只在 `\n` 前成立 | 標題行的比對加 `\r?` | `tests/test_sync_version.py::test_bump_and_sync_work_on_crlf_toml_files`（含標題帶註解的 CRLF） |
| 2 | （協調者要求）推薦組合在預算不到 4 GB 或 CPU 時推薦 HY-MT 1.8B；`allow_restricted=True` 時會推薦 HY-MT | 舊的 `RECOMMENDATION_TIERS` | 每個等級只推薦 Qwen3.5（≥ 7.5 GB 9B→4B，其餘 4B），不論 `allow_restricted`；HY-MT 只能在自訂組合手動選 | `test_translate_prompts.py`（`test_hy_mt_is_never_recommended_regardless_of_allow_restricted` 等）、`test_hardware_recommend.py`（6／4 GB、CPU、Apple 8 GB、只裝 HY-MT）、`test_engine_hardware.py`；規格 `hardware.md`、`translate.md`、`ui.md` 的表與說明 |
| 3 | #2 之後設定頁「授權地區」的說明不對（「勾選後建議組合才會考慮有地區限制的模型」、儲存後「建議組合會重新計算」） | 文案沿用舊行為 | 13 個語系的 `settings.regionHelp`、`settings.savedRegion` 改成「建議組合只用 Qwen3.5；勾選只影響自訂組合的警告」；`TranslationSettings.allow_restricted` docstring | vitest（鍵與佔位符一致）；GUI (a) 13 個語系無空白、無原始鍵 |
| 4 | 換語言／模型時每個 session 仍多約 11 MiB 顯示記憶體 | 模型池 `MAX_IDLE_MODELS = 4` 小於實際用到的鍵（情境 2 有 6 個），每次歸還都丟掉最舊的物件，重建時又漏 CTranslate2 的 8 MiB | 上限改 10（丟掉的物件本來就漏掉它佔的量，保留不比丟掉貴）；`asr.md` 更新 | `tests/test_asr_model_pool.py::test_default_pool_keeps_every_model_of_a_language_switching_user`（上限 4 時建立 18 個物件而不是 6 個）；情境 2 重跑 GPU +1.24 MiB／session |
| 5 | （部分）主視窗字幕紀錄每則事件都重寫全部列的文字，WebView2 renderer 記憶體成長 | `updateLineElement()` 對沒變的值也指定 `textContent`／`title`／`lang` | `app/src/lib/dom.ts` 的 `setIfChanged()`，只寫入改變的值 | `app/src/lib/dom.test.ts`（3 個）；GUI 長時間 +30.8 → +25.3 MiB／分，**沒有解決**（見第 6 節） |

測試工具（`experiments/`）的修改：`acceptance.py`（推薦組合、VRAM、metrics 檢查）、`gui_acceptance.py`（first-run 改成新組合、批次與選單 6–9）、
新增 `gui_v020.py`、`memory_soak/scenarios.py`（情境 5 的 `pull_model` 參數，指定時必須已安裝；pull 途中關閉引擎再重試）、
`memory_soak/README.md`（修正後的回歸，引用 `47b0b2f`、`a6a93f5`）。

## 觀察到、沒有修的（給你決定）

> 後續：1 已改善（兩個模型並行載入，11.8 → 7.3 s，`78c79d1`；載入進度以 `status loading` 的 `{step, done, total}` 回報）；2、3、4 已修正（`fa6748d`）。

1. **按「開始」到聆聽要 10–13 秒**（v0.1.0 約 3 秒）：新推薦組合兩個模型都是 int8_float16，快取裡是 float16 權重，載入時量化（large-v3 6.5 s、turbo 3.4 s）。
   可選：快取另存 int8 轉換後的模型、兩個模型並行載入，或 12 GB 時即時路徑改回 turbo f16（VRAM 多約 1 GB，翻譯預算 8.13 → 7.09 GB 會降到 4B，所以目前的挑法不會選）。
2. **執行中打開翻譯、服務又連不上時**，引導訊息下面多一則英文技術訊息「引擎提醒：keep-warm request to the translation server failed: WarmUpError…」（`26-translation-guide.png`）。
   規格要求 log warning 要顯示，但對一般使用者太技術。
3. **開始前下載時有兩個進度**：下載面板之外，上方「缺少的模型」訊息也顯示「正在下載 base 0%、small 0%…」（`23-start-downloads.png`）。內容一致、不衝突。
4. 設定頁捲動時，固定在上方的訊息列會蓋住表單最上面一行（`21-glossary.png` 的「使用內建的直播用語表」）；術語表的語言選單較窄，「English（英文）」被截斷。
5. `recommend.py` 的 `_unsupported_pair()` 仍以所有商用預設組判斷「哪個方向沒有模型支援」，與只推薦 Qwen 的範圍不一致；目前 Qwen 支援全部 20 種語言，這條路徑實際走不到（code-reviewer 指出，未改）。
6. CPU 或預算不到 4 GB 時推薦的 4B 在 CPU 上的速度沒有實測（以前推薦 1.8B 也沒有）；純 CPU 時沒有「會很慢」的警告，只有 `translation_cpu` 說明。

## 仍未驗證

- macOS、Linux 真機（同 v0.1.0 清單：擷取權限、透明視窗、全域快速鍵、Wayland、AppImage）。
- **自動更新 0.1.0 → 0.2.0**：要有真的 GitHub Release（正式金鑰與端點）才能驗；本次只建了沒有 updater artifacts 的安裝檔。
- NSIS 安裝檔本身這次只建置、沒有靜默安裝測試（GUI 驗收直接執行同一次建置的 `target/release/kikitori-app.exe`，旁邊有 `kikitori-engine/` 與 `licenses/`；v0.1.0 已驗過安裝流程）。
- 錄音中拔掉實體 USB 耳機、麥克風隱私權關閉；`Connection: close` 的反向代理實測；麥克風口譯對話。

## 清理

| 項目 | 處理 |
|---|---|
| `文件\kikitori\`（匯出、自動存檔、術語表 CSV、OBS 檔） | 原本不存在；每個情境只刪本測試建立的檔案，最後整個移除（已確認不存在） |
| `experiments/e2e_acceptance/data/build-v020/`（引擎 164 MB＋licenses 副本） | **已刪除** |
| 隔離的模型快取：`results/20261106-0435*-v020-languages/localappdata`（605＋464 MB）、`memory_soak/results/20261106-040411-s5-downloads/localappdata`（75 MB） | **已刪除**（結果 JSON 保留） |
| session 暫存目錄：app 建置 `target/`（1.8 GB）、PyInstaller `work/`、tauri-driver、msedgedriver | **已刪除**；只留記錄檔與 `webview_diag.py` |
| **沒有動**：`memory_soak/results/20261106-013828-s5-downloads/localappdata`、`…-014657-…/localappdata`（各 75 MB，前一次代理人的） | 可以刪 |
| `experiments/e2e_acceptance/results/`、`experiments/memory_soak/results/` | git 忽略；報告引用的資料夾保留 |
| 行程 | 每個情境結束都確認沒有 `kikitori-app`、`kikitori-engine`、`msedgedriver`、`tauri-driver` 殘留；最後 `tasklist` 也沒有 |
| Ollama | 沒有卸載或刪除模型；`qwen3.5:4b` 留在顯示記憶體到 keep_alive 到期（約 5 分鐘）自己卸載 |

## 建議的 commit 切分（已依此 commit）

1. `scripts/sync_version.py`、`tests/test_sync_version.py`：CRLF 修正。
2. 版本 0.2.0（`pyproject.toml`、`uv.lock`、app 的五個版本檔）。
3. `src/kikitori/translate/presets.py`、`src/kikitori/hardware/recommend.py`、`src/kikitori/config/settings.py`、三個測試檔、`docs/specs/{hardware,translate,ui}.md`、13 個語系字典：只推薦 Qwen3.5。
4. `src/kikitori/asr/whisper.py`、`tests/test_asr_model_pool.py`、`docs/specs/asr.md`：模型池上限 10。
5. `app/src/lib/dom.ts`、`app/src/lib/dom.test.ts`、`app/src/main/main.ts`：字幕紀錄只寫入改變的值（效果有限，可以不收）。
6. `experiments/e2e_acceptance/*`、`experiments/memory_soak/*`、截圖、本報告。
