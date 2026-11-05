# 端到端驗收（Windows 本機）

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05，分支 `feat/full-app`。
> 機器：Ryzen 7 5700X、64 GB、RTX 5070 12 GB（驅動 621.01）、Windows 11 26200、WebView2 156.0.4314.58、
> 螢幕縮放 125%。翻譯：本機 Ollama `qwen3.5:9b`。

## 檔案

| 檔案 | 內容 |
|---|---|
| `acceptance.py` | A：以 stdio 協定驅動**真的**引擎（開發版 `uv run kikitori engine` 或打包好的 `kikitori-engine.exe`），真擷取、真 Whisper（GPU）、真 Ollama |
| `gui_acceptance.py` | B／C：以 W3C WebDriver（tauri-driver + msedgedriver，只用標準庫的 HTTP）操作正式版建置的桌面程式 |
| `e2e_common.py` | 共用：NDJSON 客戶端、WAV、Windows TTS、播放到指定裝置 |
| `screenshots/` | 可以提交的截圖（只有本專案自己寫的句子）；`screenshots/private/` 含動畫台詞，git 忽略 |
| `data/`、`results/` | TTS 音檔、建置副本、每次執行的結果（含台詞），git 忽略 |

## 怎麼跑

```text
uv run python -X utf8 experiments/e2e_acceptance/acceptance.py        互動選單
uv run python -X utf8 experiments/e2e_acceptance/gui_acceptance.py    互動選單
```

非互動（自動化用）：`acceptance.py --batch dev|release|devices|tts`、`gui_acceptance.py --batch run`。
GUI 驗收以環境變數指定工具：`KIKITORI_E2E_APP`（`kikitori-app.exe`，resources 旁要有 `kikitori-engine/` 與 `licenses/`）、
`KIKITORI_E2E_TAURI_DRIVER`、`KIKITORI_E2E_EDGEDRIVER`；`KIKITORI_E2E_RELEASE_ENGINE` 可把 `--batch release` 指到別的引擎建置。

安全規則：
- 測試聲音只播到名稱含 **Steam Streaming Speakers** 的 WASAPI 輸出（虛擬喇叭，使用者聽不到）；它若是系統預設輸出就拒絕播放。引擎擷取同一裝置的 loopback。
- 設定檔一律放在本次結果資料夾（`KIKITORI_CONFIG_DIR`），不碰使用者的設定。
- 截圖只拍 kikitori 自己的視窗；字幕視窗先移到主視窗上方再拍（結束時移回原位）。
- 動漫台詞、`anime.wav`／`vtuber.wav` 不進 repo。中文與英文測試句是本專案自己寫的，用 Windows 內建 TTS（Microsoft Hanhan／Zira）合成。

### 正式版建置（不碰共用的 `dist/`、`binaries/`）

```text
# 引擎：呼叫 packaging/engine/build.py 的 build()，輸出到自訂目錄
build("onedir", paths=paths_for("onedir", dist_root=<dir>/dist, work_root=<dir>/work))
# app：resources 指到 experiments/e2e_acceptance/data/build/{kikitori-engine,licenses}，自己的 CARGO_TARGET_DIR
npm run tauri build -- --no-bundle --config <conf.json>
```

## 驗收標準

- 每句先有即時字幕（`is_final=false`）再有完整字幕；完整字幕依序；同一句的即時字幕不會在完整字幕之後。短於 1.0 秒的句子可能來不及觸發即時辨識（間隔 700 ms），不要求一定有即時字幕。
- 影片翻譯：原文是日文、譯文是台灣正體（Big5 沒有且 OpenCC `s2t` 會改掉的字＝簡體專用字，必須 0 個）。
- 口譯（對方英文）：使用者說的中文 → 英文譯文；英文 → 正體中文譯文；中文原文也要是正體。
- 靜音段（音軌之間 3 秒、音軌內的間隔）與純配樂段（MUSAN `music-fma-0098` 前 20 秒，無人聲）以及 `synthetic.wav`（純噪音）：**非空完整字幕 0 句**（容許值 0）。
- 真值區間（`ground_truth.json`／TTS 合成時記錄）每一段至少被一句完整字幕涵蓋（停頓短於句尾判斷時兩段會合成一句）。
- `session.stop` 後 `idle`；`engine.shutdown` 結束碼 0；stdout 每行都是 JSON。

延遲定義（牆鐘時間，`monotonic`）：
- **首個即時字幕**：第一則即時字幕送出 − 句子起點（`start_sample`，含 200 ms 前置緩衝）。
- **即時字幕延遲**：每則即時字幕送出 − 它辨識的音訊結尾。
- **完整字幕（從說完）**：完整字幕送出 − 真值區間的結尾（播放端的時間，含虛擬喇叭的輸出延遲）。只計句尾就是真值結尾的句子。
- 音訊位置換算牆鐘以 `status running` 的時刻為擷取起點；loopback 在沒有聲音時以時間補零，長時間靜音後與播放端會差 0.1–0.2 秒，所以「從說完」以播放端真值為準。

## 結果

### A. 引擎（協定）— PASS

最終一次（打包好的引擎 `kikitori-engine.exe`，`results/20261105-160523`，CPU 負載約 20%；開始前先讓 Ollama 卸載模型，重現冷啟動）：

| 步驟 | 結果 |
|---|---|
| `engine.info` | `fake: false`、協定 1 |
| `hardware.detect` | RTX 5070（12227 MB）、cuBLAS 可用（kikitori 下載的 `cublas-12.8.4.1`）、等級 `nvidia_8gb` |
| `settings.apply_recommendation` | 即時 `small`、完整 `large-v3-turbo`（CUDA float16）、翻譯 `qwen3.5:9b` |
| `models.list` | 兩個 ASR 模型已下載、翻譯模型已安裝、GPU 元件已安裝 |
| `devices.list` | 名稱是正確的 Unicode（`喇叭 (Steam Streaming Speakers) [Loopback]`、`耳機 (WH-1000XM5)` 等），與 PyAudioWPatch 直接列出的一致 |
| 影片翻譯 session | 載入 8.6 s（含 Ollama 冷啟動）；anime + vtuber 12 段真值全部涵蓋；14 句、465 則即時字幕、479 個 subtitle 事件；完整字幕依序；每句都先有即時字幕；簡體字 0；靜音與純配樂段誤觸發 0 |
| 口譯 session（對方英文） | 載入 2.8 s；6 段真值全部涵蓋；8 句；中文 → 英文 4 句、英文 → 正體 4 句；中文原文為正體；簡體字 0 |
| `session.stop` | 兩次都在 0.1 s 內 `idle` |
| `engine.shutdown` | 結束碼 0；stdout 0 行非 JSON |

| 模式 | 首個即時字幕（中位數／P90／最大） | 即時字幕延遲（中位數／P90／最大） | 完整字幕，從說完（中位數／P90／最大） |
|---|---|---|---|
| 影片翻譯（日文動畫／VTuber） | 0.94／1.70／3.02 s | 0.33／0.49／0.60 s | 0.69／0.92／0.94 s（n=7） |
| 口譯（TTS 中文、英文） | 0.86／0.89／0.91 s | 0.22／0.31／0.42 s | 0.71／0.80／0.86 s（n=6） |

開發版引擎（`uv run kikitori engine`，`results/20261105-161220`，同樣冷啟動）也全部 PASS：影片翻譯 15 句、448 則即時字幕，首個即時字幕中位數 0.98 s、即時延遲 0.37 s、完整字幕從說完 0.69 s；口譯 8 句，0.87／0.22／0.71 s。

完整字幕包含句尾判斷必須等的靜音（media 0.6 s、interpret 0.5 s，保留 0.2 s 給 Whisper）。
每句的數字（不含台詞）：

| 句 | 涵蓋的真值 | 長度 s | 即時字幕數 | 首個即時 s | 即時延遲中位數 s | 完整（從 end_sample）s | 完整（從說完）s | 語言 |
|---|---|---|---|---|---|---|---|---|
| 1 | anime#1 | 2.68 | 7 | 3.02 | 0.24 | 0.78 | 0.44 | ja→zh-TW |
| 2 | anime#2 | 3.38 | 26 | 0.87 | 0.24 | 0.82 | — | ja→zh-TW |
| 3 | anime#2、#3 | 1.36 | 8 | 0.88 | 0.22 | 0.68 | — | ja→zh-TW |
| 4 | anime#3 | 4.76 | 44 | 0.92 | 0.25 | 0.82 | 0.53 | ja→zh-TW |
| 5 | anime#4 | 0.86 | 2 | 0.94 | 0.24 | 0.88 | — | **en**→zh-TW（感嘆詞，見限制） |
| 6 | anime#4 | 3.61 | 29 | 0.88 | 0.24 | 0.76 | — | ja→zh-TW |
| 7 | anime#5 | 4.92 | 42 | 0.90 | 0.25 | 0.92 | — | ja→zh-TW |
| 8 | anime#6 | 3.22 | 20 | 0.93 | 0.26 | 0.82 | — | ja→zh-TW |
| 9 | vtuber#1 | 5.69 | 118 | 0.99 | 0.42 | 1.08 | 0.79 | ja→zh-TW |
| 10 | vtuber#2、#3 | 5.82 | 81 | 1.69 | 0.43 | 1.10 | 0.90 | ja→zh-TW |
| 11 | vtuber#4 | 1.85 | 9 | 1.69 | 0.34 | 0.92 | — | ja→zh-TW |
| 12 | vtuber#4 | 1.94 | 7 | 1.70 | 0.32 | 0.84 | 0.64 | ja→zh-TW |
| 13 | vtuber#4、#5 | 4.60 | 53 | 0.94 | 0.37 | 1.08 | 0.94 | ja→zh-TW |
| 14 | vtuber#6 | 2.62 | 19 | 0.97 | 0.31 | 0.87 | 0.69 | ja→zh-TW |
| 口譯 1–4 | tts_zh#1–#3 | 2.1–4.3 | 15–54 | 0.84–0.87 | 0.19–0.26 | 0.57–0.76 | 0.71–0.86 | zh→en |
| 口譯 5–8 | tts_en#1–#3 | 1.6–3.0 | 8–30 | 0.86–0.91 | 0.19–0.22 | 0.54–0.67 | 0.59–0.73 | en→zh-TW |

第 1 句首個即時字幕 3.0 s：前兩次即時辨識（0.7、1.4 秒的音訊）只有「うん、」被過濾規則擋掉，第三次才送出。
即時口譯模式在協定上以 `device_id` 指定虛擬喇叭的 loopback（`open_source()` 以 id 的種類為準）；介面上口譯模式只列麥克風，這是設計，不影響測試。

### B. 桌面程式（WebDriver）— PASS

正式版建置（`tauri build --no-bundle`，resources 帶入打包好的引擎與 `licenses/`），`results/20261105-160828-gui`：

| 步驟 | 結果 |
|---|---|
| 啟動 | 兩個 webview（`kikitori`、`kikitori 字幕`）；正式版引擎從資源目錄啟動；狀態列 0.3 s 內「待命中」 |
| 模型頁 | 顯示 RTX 5070、cuBLAS「可以使用（kikitori 下載的 GPU 加速元件）」、本機翻譯服務 5 個模型；建議組合 small／large-v3-turbo／qwen3.5:9b；「套用建議組合」寫入設定（`asr.device=cuda`） |
| 設定頁 | `ftp://…` 與 `1BAD-NAME` 在欄位下方顯示錯誤（「網址要以 http:// 或 https:// 開頭。」「只能用英文字母、數字和底線，且不能以數字開頭。」）、狀態「請修正標示的欄位。」、設定檔未變；改回合法值後「已儲存設定。」 |
| 影片翻譯 | 選「喇叭 (Steam Streaming Speakers) [Loopback]」→ 開始 → 4.3 s 後「正在聆聽」；播放 anime.wav：字幕紀錄 8 列，8 列都先以淡色即時字幕出現再換成完整翻譯；原文都是日文、譯文都是正體 |
| 字幕視窗 | 播放中最多同時 2 句（設定值）；看得到即時字幕的淡色樣式；`html`／`body` 背景 `rgba(0,0,0,0)` |
| 滑鼠穿透 | 開關切換後視窗樣式 `0x40118 → 0xc0138 → 0x40118`（`WS_EX_TRANSPARENT` 開／關），字幕視窗移除 `is-interactive` |
| 英文（本專案的 TTS 句子） | 4 句英文 → 正體中文 |
| 停止 | 「待命中」，按鈕回到「開始」 |
| 清理 | 結束後沒有殘留的 `kikitori-app`、`kikitori-engine`、`msedgedriver`、`tauri-driver` 行程 |

截圖（`screenshots/`，都看過）：
- `01-main-ready.png`：深色主題（跟隨系統）；左側藍色控制列、字幕黃「開始」按鈕、「待命中」狀態燈；空狀態說明。版面正常。
- `02-models.png`：「這台電腦」與「建議組合」兩區，正體中文、數字對齊，沒有溢出。
- `03-settings-invalid.png`：兩個欄位紅框、下方紅字錯誤訊息。
- `04-about.png`：版本、引擎與協定版本、MIT、Intel OpenMP 聲明、兩個授權資料夾的完整路徑（沒有 `\\?\` 前綴）與兩個按鈕。
- `05-transcript-partial.png`：最後一列是即時字幕（淡色、左側字幕黃細條），前兩列是完整翻譯。
- `06-transcript-final.png`／`09-main-stopped.png`：4 句英文與正體譯文。
- `07-overlay-webdriver.png`：字幕視窗內容（WebDriver 把透明背景畫成白色）。
- `08-overlay-screen.png`：實際螢幕擷取（`BitBlt` + `CAPTUREBLT`）：字幕視窗疊在主視窗上，背景透明、看得到後面的字幕紀錄；原文白字、譯文字幕黃粗體，各有半透明深色底板與描邊，在深色與淺色背景上都清楚；較舊的一句略淡。
- `private/`（git 忽略，含動畫台詞）：日文原文與正體譯文的字幕紀錄、字幕視窗，外觀同上。

### C. 「關於」的授權資料夾 — PASS

- 正式版建置找得到兩個資料夾（`<資源目錄>\licenses`、`<資源目錄>\kikitori-engine\THIRD_PARTY_LICENSES`），兩個按鈕都顯示。
- 按下後以 `Shell.Application` 確認出現了路徑相同的檔案總管視窗（兩個都有），測試結束時關掉。

## 驗收中找到並修正的問題

| # | 問題 | 原因 | 修正 | commit |
|---|---|---|---|---|
| 1 | 同一個引擎行程每開一次 session，顯示記憶體多約 2.6 GB（7.2 → 9.8 → 11.7 GB），第二個 session 起即時字幕延遲 0.4 s → 1.5–2.5 s | pipeline 的回呼閉包參照 `_Session`、`_Session` 又參照 pipeline，形成循環，只能等循環垃圾回收 | 結束狀態送出時切斷 `session.pipeline` | `0aebe58` |
| 2 | 程式日誌（引擎 stderr）裡裝置名稱是亂碼 | stderr 用系統地區編碼（cp932），Tauri 以 UTF-8 讀 | stderr 改成 UTF-8 | `c1e8e15` |
| 3 | Ollama 閒置卸載模型後，第一句即時翻譯逾時、前 5 句完整字幕晚 8–16 s，並跳出「翻譯失敗」 | 模型冷啟動 4–10 s | session 載入時預熱本機翻譯模型 | `3738325` |
| 4 | 預熱與 ASR 同時載入時，Ollama 的 llama-server 以 CUDA 錯誤當掉 | 兩個行程同時配置顯示記憶體，Ollama 的估算過時 | 改成 ASR 載入完才預熱 | `9a7fe9a` |
| 5 | 口譯時使用者說的中文顯示成簡體 | Whisper 的中文結果通常是簡體 | 中文原文以 `to_taiwan_traditional()` 轉成台灣正體（日文不轉） | `c052905` |
| 6 | 「關於」的路徑顯示成 `\\?\C:\…` | `resource_dir()` 經過 canonicalize | `display_path()` 去掉 verbatim 前綴 | `12695c4` |
| 7 | 在 Git Bash（cp932）執行 `uv run kikitori` 一啟動就 `UnicodeEncodeError`；裝置名稱顯示成 `���@ (WH-1000XM5)` | 選單的 stdout 用地區編碼 | 選單一律以 UTF-8 輸出 | `9a4d6a0` |

裝置名稱：協定（`devices.list`）的名稱一直是正確的 Unicode；亂碼只出現在 cp932 的文字層（選單與 stderr），已由 #2、#7 修正。

## 已知限制（未修）

- 極短的感嘆詞（0.86 s 的「ふん」）會被 Whisper 判成 `en`，介面標「英」，譯文仍正確。可在 pipeline 加「含假名就是日文」的規則，但屬於 asr／pipeline 行為變更，未做。
- Ollama 在 session 中閒置超過 5 分鐘仍會卸載模型，之後第一句要等模型載入（OpenAI 相容 API 無法指定 `keep_alive`）。（之後已改善：session 執行中翻譯請求停了 `translation.keep_warm_secs`（預設 240）秒就對本機服務再送一次預熱請求，見 `docs/specs/pipeline-engine.md`「ASR 接縫」的 `attach_keep_warm()`。）
- 自動化時 msedgedriver 會為 WebView2 開一個主控台視窗；截圖前把它縮小（只在測試時出現）。
- 驗收期間另一個代理人在同機建置 Rust 與跑安裝檔測試；延遲數字取 CPU 負載約 20% 時的那一次。CPU 被占滿時曾看到第 1 句沒有即時字幕。

## 工具（只放在 session 的暫存目錄，未加入專案依賴）

| 工具 | 版本 | 來源與驗證 |
|---|---|---|
| tauri-driver | 2.1.0（Apache-2.0 OR MIT） | crates.io，`cargo install tauri-driver --version 2.1.0 --locked --root <暫存目錄>` |
| msedgedriver | 156.0.4314.58（與 WebView2 執行環境相同） | `https://msedgedriver.microsoft.com/156.0.4314.58/edgedriver_win64.zip`，SHA-256 `2fd5acb3e5d8a5e35e0281f59d31292f261f21ba994fe025370331d522042ab5`，Authenticode 簽章有效（Microsoft Corporation） |
