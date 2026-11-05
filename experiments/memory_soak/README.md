# 記憶體與資源釋放壓力測試（Windows 本機）

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-06，分支 `feat/vtuber-models`（`4611f4d`）。
> 測的是**當時的工作目錄**（含其他代理人尚未 commit 的修改，見 #2 的注意），不是 HEAD 的乾淨狀態。
> 機器：Ryzen 7 5700X、64 GB、RTX 5070 12 GB（WDDM）、Windows 11 26200。翻譯：本機 Ollama 0.40.14、`qwen3.5:4b`。
> **同機有另一個代理人在跑 Ollama 翻譯評測**（`experiments/translate_eval/`），數字的干擾見「干擾」。

## 檔案

| 檔案 | 內容 |
|---|---|
| `menu.py` | 互動選單（情境 1–7 與快速版；逐項詢問參數，Enter 用預設值）；`--batch <情境> [參數=值 ...]` 給自動化 |
| `scenarios.py` | 情境 1–6：以 stdio 協定驅動**真的**引擎（真 Whisper on GPU、真擷取、真 Ollama） |
| `gui_soak.py` | 情境 7：以 WebDriver 長時間操作正式版建置（沿用 `e2e_acceptance/gui_acceptance.py` 的 `AppSession`） |
| `ct2_isolation.py` | 隔離測試：不經引擎，反覆載入／推論／釋放 Whisper，找出每個 session 成長的來源（見「發現的問題」#2） |
| `repro_open_error_cycle.py` | 只用 CPU 重現「開裝置失敗的 session 留下參照循環」（見「發現的問題」#1） |
| `monitor.py` | 每秒取樣：行程樹的私有記憶體、工作集、handle、執行緒、每個行程的顯示記憶體；整張顯示卡；Ollama `/api/ps` |
| `proxy.py` | 計數用的本機反向代理：引擎 → 代理 → Ollama，記下引擎送出的每一個翻譯請求 |
| `harness.py` | 共用：引擎啟動、循環播放、session 操作、**判定門檻**（每個門檻旁邊寫了理由）、統計 |
| `results/` | 每次執行的取樣（`samples.ndjson`）、代理紀錄、引擎 stderr、`summary.json`；git 忽略 |

沿用 `experiments/e2e_acceptance/e2e_common.py` 的 NDJSON 客戶端、WAV 讀取與「只播到 Steam Streaming Speakers」的檢查；
測試聲音沿用 `experiments/pipecat_vad/data/tracks/`（anime、vtuber，不進 repo）、`e2e_acceptance/data/tts_*.wav`（本專案自己寫的 TTS 句子）
與 MUSAN `music-fma-0098`（BGM）。沒有新增任何專案依賴（只用標準庫、numpy、既有的 PyAudioWPatch）。

## 怎麼跑

```text
uv run python -X utf8 experiments/memory_soak/menu.py                 互動選單
uv run python -X utf8 experiments/memory_soak/menu.py --batch quick   1、2、3、6（中等規模）＋ 4（5 分鐘）
uv run python -X utf8 experiments/memory_soak/ct2_isolation.py        隔離測試（所有做法，各 12 輪）
```

| 選單 | 情境 | 參數（預設） | 時間（約） |
|---|---|---|---|
| 1 | start/stop × N（media、播放日文語音） | `cycles=20`、`session_secs=12` | 每輪 27 s |
| 2 | 每個 session 換語言／模型組合（4 組輪流） | `rounds=3` | 每輪 4 × 28 s |
| 3 | 執行中切換翻譯 × N、只顯示原文的 session、保溫、Ollama 到期 | `toggles=20`、`quiet_secs=35` | 6 分＋ |
| 4 | 長時間 session（人聲＋BGM 循環） | `minutes=20` | 21 分＋ |
| 5 | ASR 下載取消／續傳／重複（隔離的模型快取）、`models.pull_translation` × N | `repeats=3`、`pulls=5`、`model=tiny` | 2–5 分 |
| 6 | 錯誤路徑、引擎重啟（shutdown／stdin EOF／強制結束） | `rounds=3` | 5 分 |
| 7 | 桌面程式長時間執行（WebDriver） | `minutes=30` | 31 分＋ |

開發版引擎（目前的原始碼）用 `which=dev`（預設）；打包好的引擎用 `which=release`（`KIKITORI_SOAK_ENGINE` 可指到別的建置）。
翻譯模型預設 `qwen3.5:4b`（`KIKITORI_SOAK_TRANSLATION_MODEL` 可改）：比推薦的 9B 小，與同機其他實驗搶顯示記憶體時較不會把別人的模型擠掉。

### 情境 7（GUI）要準備的東西

需要 `KIKITORI_E2E_APP`（`kikitori-app.exe`，旁邊要有 `kikitori-engine/`）、`KIKITORI_E2E_TAURI_DRIVER`、`KIKITORI_E2E_EDGEDRIVER`；
建置與工具取得方式見 `experiments/e2e_acceptance/README.md`（tauri-driver 2.1.0、與 WebView2 同版的 msedgedriver）。
找不到時印出 `SKIP` 與原因、結束碼 2。**本次沒有跑**：這台機器目前沒有建置好的 app 與 driver（上次驗收的工具放在已清掉的暫存目錄）。
它會預先寫好設定（翻譯經過計數代理）、選虛擬喇叭的 loopback、按「開始」，播放人聲＋BGM；每 20 秒記錄主視窗與字幕視窗的 DOM
節點數、字幕紀錄列數與 JS heap，每 2 分鐘切換「顯示翻譯」、每 3 分鐘關閉再打開字幕視窗、每 5 分鐘切換介面語系（en → ja → zh-TW）。
判定：字幕紀錄 ≤ 300 列；「與列數無關」的 DOM 成長（DOM 節點數對列數回歸後的殘差）≤ 2 節點／分；字幕視窗 DOM 變化範圍 ≤ 80；
`kikitori-app.exe` 私有記憶體斜率 ≤ 1 MiB／分；到達 300 列上限之後 WebView2（所有子行程合計）斜率 ≤ 2 MiB／分；關閉後沒有殘留行程。
每分鐘約 13 句，到 300 列約要 23 分鐘，所以預設 30 分鐘。**這段程式碼沒有實際執行過**，第一次跑時要先看它的輸出。

## 安全規則

- 測試聲音只播到名稱含 **Steam Streaming Speakers** 的輸出（虛擬喇叭）；它若是系統預設輸出就拒絕播放。引擎擷取同一裝置的 loopback。
- 設定檔一律放在結果資料夾（`KIKITORI_CONFIG_DIR`），不讀、不寫使用者的設定檔；不碰 `.env`。
- 情境 5 的模型下載寫到結果資料夾裡的 `LOCALAPPDATA`（`asr/cache.py` 以 `%LOCALAPPDATA%\kikitori\models` 為快取；沒有 kikitori 專用的環境變數），
  只刪除這個資料夾底下自己下載的 `tiny`（刪除前檢查路徑在結果資料夾內）。
- 不卸載、不刪除任何 Ollama 模型。`models.pull_translation` 會對已安裝的最小模型重新 pull（Ollama 只檢查 manifest；但若 registry 上的版本更新了會被更新，
  並在 summary 註記 digest 是否改變）——同機有人在評測那個模型時用 `pulls=0` 略過（本次就是這樣）。
- 結束時檢查引擎行程樹（含 venv 的 python 啟動器與真正的 python）沒有殘留；殘留的是本測試自己啟動的引擎才會結束它，並記錄。

## 量測

`monitor.py` 每秒一筆（`results/<run>/samples.ndjson`），另外寫入事件標記（`cycle_start`、`running`、`idle`…）：

- **引擎**（行程樹的合計）：私有記憶體（Private Bytes，`K32GetProcessMemoryInfo` 的 `PrivateUsage`）、工作集、handle 數、執行緒數。
- **每個行程的顯示記憶體**：WDDM 下 `nvidia-smi` 拿不到每個行程的用量，改用效能計數器 `\GPU Process Memory(pid_<PID>_*)\Dedicated Usage`
  與 `Shared Usage`（常駐的 PowerShell `Get-Counter` 迴圈，約每秒一筆）。**判定一律看 dedicated + shared（`gpu_mib`）**：顯示記憶體不夠時
  WDDM 會把配置降到共用記憶體（本次情境 4 實際發生），只看 dedicated 會誤以為釋放了。
- 整張顯示卡（`nvidia-smi`，每 5 秒）、Ollama 的行程（`ollama.exe`、`llama-server.exe`）與 `/api/ps`（每 5 秒）。
- **引擎送給 Ollama 的請求**：`translation.base_url` 指到 `proxy.py`（127.0.0.1，引擎仍當成本機服務，預熱與保溫照常），每個請求記時間、路徑、模型、
  `stream`、`max_tokens`（保溫／預熱是 `max_tokens=32`）。不受同機其他 Ollama 客戶端影響。

## 判定門檻（`harness.py`，旁邊有完整理由）

「結束後」＝ session 送出 `idle` 後等 12 秒（pipeline.stop 的 join 逾時 5 秒、daemon 執行緒自行結束、計數器約 1 秒延遲），取最後 4 秒的中位數。
排除前 2 輪暖機（CUDA context、cuBLAS、CTranslate2 配置器、Python 模組與 `lru_cache` 都是第一次 session 才出現的一次性常駐成本）。

| 項目 | 門檻 | 理由（摘要） |
|---|---|---|
| 私有記憶體每輪斜率 | ≤ 4 MiB／輪 | 一個沒釋放的 session 至少留下 Silero VAD、兩個 transcriber 的 CPU 端結構與擷取緩衝（數十 MB）；4 MiB 高於 Python 配置器的雜訊 |
| handle 每輪斜率 | ≤ 1／輪 | 漏一條執行緒、一個 socket 或事件就是每輪 ≥ 1 |
| 執行緒 | 結束後 ≤ 暖機後中位數 + 2，斜率 ≤ 0.25／輪 | 實測 handle 持平時執行緒仍在 29–32 跳動（Windows 執行緒池）；漏掉的 Python 執行緒一定也留下 thread handle（第一次 12 輪後由 +1／0.1 放寬，見下） |
| 顯示記憶體每輪斜率 | ≤ 16 MiB／輪 | v0.1.0 的洩漏是每個 session +2.6 GB；CTranslate2 的快取配置器有上限，不會造成持續斜率 |
| 顯示記憶體回落 | `idle` 後 10 s 內回到「結束後的底」+128 MiB | 底＝暖機後各輪的中位數 |
| 結束後殘留 | 底 − 第一個 session 之前 ≤ 1 GiB | 小於最小的一組模型（small + large-v3-turbo，float16 約 2.1 GB），模型沒釋放一定超過 |
| 長時間 session | 暖機後 Q4 − Q2 中位數 ≤ 1 MiB／分 × 相隔分鐘（≥ 8 MiB）；handle ≤ 2／分；執行緒範圍 ≤ 4；顯示記憶體 ≤ 2 MiB／分 | 每 3 秒一句、每句漏 50 KB ≈ 1 MiB／分，8 小時直播 480 MiB。執行中私有記憶體每句上下跳 ±80 MiB，短時間的最小平方斜率被跳動主導，所以改看四分位中位數（斜率照樣記錄） |
| 長時間 session 結束後 | 私有記憶體 ≤ 暖機 session 結束後 + 64 MiB；顯示記憶體 ≤ + 128 MiB | |

門檻調整的紀錄（都在看過資料後、理由寫在程式碼旁）：執行緒容許 +1 → +2；長時間 session 的私有記憶體由「斜率」改為「四分位中位數」；
下載的 handle 基準由「下載前」改為「第一次下載後」（第一次 HTTPS 下載會一次性初始化 SSL／憑證存放區，305 → 344，之後持平）。

## 結果（2026-11-06，初步、中等規模，開發版引擎）

設定：`asr.device=cuda`、即時 `small`／完整 `large-v3-turbo`（float16）、影片翻譯 ja → zh-TW、翻譯 `qwen3.5:4b`（經計數代理）。

### 1. start/stop × 12 — FAIL（每個 session 漏約 17 MiB 顯示記憶體與 18 MiB 主記憶體：#2）

`results/20261106-010645-s1-start-stop`；情境執行期間另一個實驗閒置（GPU 使用率中位數 2%、沒有其他 Ollama 模型）。

| 指標 | 第一個 session 前 | 第 1 輪結束後 | 第 12 輪結束後 | 每輪斜率（第 3–12 輪） | 執行中峰值 |
|---|---|---|---|---|---|
| 私有記憶體 | 522.8 MiB | 1484.4 MiB | 1798.4 MiB | **+18.4 MiB／輪**（門檻 4） | 4536 → 4857 MiB |
| 顯示記憶體（ded+shared） | 0 | 239.5 MiB | 423.5 MiB | **+16.8 MiB／輪**（門檻 16） | 3024 → 3208 MiB |
| handle | 378 | 606 | 602 | −0.29（PASS） | |
| 執行緒 | 27 | 35 | 32 | 0.13（29–32 跳動，handle 持平；新門檻下 PASS） | |

- 顯示記憶體每輪結束後都在 **0.1–1.6 s** 內回到底（約 3 GB 的模型都有釋放）：**v0.1.0 的「每個 session +2.6 GB」沒有復發**。
- 結束後的底逐輪上升，而且非常規律：239.5、255.5、271.5、287.5 … 423.5 MiB（每輪 +16 MiB）；私有記憶體同樣規律（+17–19 MiB／輪）。
- 載入 2.5–3.3 s，`session.stop` → `idle` 0.02–0.3 s，12/12 都有字幕（每個 12 秒的 session 1–3 句完整字幕）；翻譯請求 154 個。

### 2. 換語言與模型 × 2 輪（8 個 session）— FAIL（同一個洩漏：#2）

`results/20261106-012417-s2-switch`。組合：ja→zh-TW small/large-v3-turbo、ja→zh-TW small/large-v3、en→ko base/small、auto→zh-TW tiny/medium。

| 組合 | 載入 | 執行中顯示記憶體峰值 | 結束後顯示記憶體（第 1 → 2 輪） | 結束後私有記憶體（第 1 → 2 輪） |
|---|---|---|---|---|
| small / large-v3-turbo | 6.5 / 2.7 s | 3024 / 3090 MiB | 271.5 → 305.5 | 1519 → 1589 |
| small / large-v3 | 4.5 / 4.5 s | 4864 / 5060 MiB | 255.5 → 323.5 | 1514 → 1602 |
| base / small | 1.3 / 1.3 s | 1106 / 1172 MiB | 273.5 → 339.5 | 1537 → 1630 |
| tiny / medium | 2.2 / 2.2 s | 2498 / 2438 MiB | 289.5 → 357.5 | 1574 → 1650 |

- 每輪斜率：顯示記憶體 **+16.8 MiB／session**、私有記憶體 **+21.3 MiB／session**，**與模型大小無關**（tiny/medium 與 small/large-v3 一樣）——
  是「每建立一個模型物件」的固定成本，不是權重。
- 所有組合的顯示記憶體都在 0.5–2.1 s 內回到底；handle 持平（606 → 602）；執行緒 30–31。

### 3. 翻譯切換、只顯示原文、保溫 — PASS（引擎端的行為全部正確）

`results/20261106-012809-s3-translation`（`keep_warm_secs=10`）。由計數代理判定，不受同機其他 Ollama 客戶端影響：

| 檢查 | 結果 |
|---|---|
| 執行中 `session.set_translation` 開／關 × 10 | 執行緒 44 → 37、handle 斜率 −0.58／次：沒有成長 |
| 安靜 35 s、翻譯開啟 | 保溫請求 3 次（8.1、18.3、28.5 s，間隔 10 s，`max_tokens=32`） |
| 安靜 35 s、翻譯關閉 | 0 個請求 |
| 重新開啟 | 立即 1 個預熱請求（0.0 s） |
| `session.stop` 之後 35 s | 0 個請求 |
| 只顯示原文的 session（20 s） | 0 個請求（沒有模型檢查、預熱、保溫）；6 句完整字幕、譯文全空 |
| 結束後 | 執行緒 27 → 29 → 30；顯示記憶體 273.5 → 289.5 MiB（又是 +16，#2） |

Ollama 端：`/api/ps` 的到期時間在安靜期間每 10 s 被引擎的保溫請求刷新；session 結束後不再刷新。（第一次執行時把這 3 次刷新誤判成
「別人的請求」——`/api/ps` 每 5 秒才查一次，歸屬視窗太短；已修正。）

**重跑**（`results/20261106-014301-s3-translation`，代理已改成 HTTP/1.1，引擎的翻譯模型檢查也會經過代理；同機評測進行中，GPU 使用率中位數 92%）：
全部 PASS，數字同上（保溫 9.8、20.1、30.4 s；重新開啟 0.02 s 預熱；關閉期間、停止後、只顯示原文的 session 都是 0 個請求）。
Ollama 的到期時間只被引擎自己的請求刷新，**推算 keep_alive = 300.0 s**（到期時間 − 最後一個請求結束），即 Ollama 預設的 5 分鐘：
session 結束後模型在最後一個請求的 5 分鐘後卸載，符合規格（引擎不再送保溫）。

### 4. 長時間 session（5 分鐘，短版）— 不確定（受干擾）

`results/20261106-013223-s4-long`。**第 1.5 分鐘起同機的評測恢復**（qwen3.5:9b、GPU 使用率 76–100%、顯示記憶體 11.7/12.2 GB）。

| 指標 | 值 |
|---|---|
| 字幕 | 5.0 分鐘、60 句完整字幕（全部有譯文）、1033 則即時字幕；翻譯請求 234 |
| 私有記憶體（暖機後四分位中位數） | 4506.0、4524.7、4525.3、4525.4 MiB（持平）；每句上下跳 ±80 MiB；最小平方斜率 9.9 MiB／分（被跳動主導） |
| 顯示記憶體（四分位中位數） | 3039.5、3041.5、3041.7、3041.6 MiB（0.9 MiB／分，PASS）；但 WDDM 在壓力下把部分配置降到共用記憶體（dedicated 2963 → 2325 MiB、shared 0–192 MiB） |
| handle／執行緒 | −0.5／分；35–39 條（PASS） |
| 結束後 | 顯示記憶體 289.7 MiB（暖機 session 後 239.5，+50；其中 16 是 #2） |
| 結束後私有記憶體 | **1486.1 → 1681.8 MiB（+196 MiB，門檻 64）——見「發現的問題」#4** |

### 5. 下載取消／續傳／重複 — PASS（`models.pull_translation` 未跑）

`results/20261106-014657-s5-downloads`（`repeats=3 pulls=0`，模型快取在結果資料夾的 `LOCALAPPDATA`）：

| 檢查 | 結果 |
|---|---|
| 下載 `tiny` 到 8.4/78.2 MB 時 `engine.shutdown` | 0.1 s 內結束碼 0；`download_progress error: DownloadCancelledError: download of model.bin cancelled`；保留 `model.bin.part`；沒有殘留行程 |
| 重啟引擎再下載（`.part` 還在）、再刪掉重下載 × 3 | 4 次都 `done` |
| 執行緒／handle | 下載前 26／305；每次下載後 28、28、25、25／**344、344、344、344**（第一次 HTTPS 下載一次性初始化 SSL／憑證存放區，之後持平） |

續傳是否真的從 8.4 MB 接續沒有在位元組層級驗證（第一筆進度事件依規格一律是 0）。

### 6. 錯誤路徑與引擎重啟 — FAIL（開裝置失敗的 session 把整組模型留在 GPU：#1）

`results/20261106-014821-s6-errors`（`rounds=2`，每一步之後等 12 秒量測；同機評測進行中）：

| 步驟（每輪依序） | 回應 | 第 1 輪：之後的顯示記憶體／私有記憶體 | 第 2 輪 |
|---|---|---|---|
| 格式錯誤的裝置 id | 同步 `device_not_found` | 0 → 0 MiB／522 → 522 MiB | 3064 → 3064／4372 → 4372 |
| **格式正確但不存在的裝置**（`wasapi:loopback:999999`）：載入模型後開裝置失敗 | `status error device_not_found` | **0 → 3024 MiB／522 → 4260 MiB** | **3064 → 5545 MiB／4372 → 6911 MiB** |
| 未知的 ASR 模型（即時模型先載入成功） | `status error unknown_model` | +8／+15 | +8／+15 |
| 翻譯模型沒裝（`kikitori-soak-missing:0b`） | `status error translation_model_missing` | +0／+0 | +0／+0 |
| 載入中 `session.stop`（等它的載入執行緒做完） | `idle`，`session N stopped while loading` | +16／+26 | +16／+23 |
| 正常開始＋執行中再 `session.start` | `session_active`；載入 9.6／8.5 s（GPU 被評測占滿） | +16／+71 | +16／+20 |

引擎重啟（running session 中，各一次）：`engine.shutdown` 0.22 s 結束碼 0、stdin EOF 0.27 s 結束碼 0、TerminateProcess（venv 的 python 啟動器）立即結束——
三種都**沒有殘留行程**（行程樹的每個 pid 以建立時間比對）。第一次執行（`20261106-013943`）的結果相同：shutdown 0.27 s、EOF 1.94 s、kill 都沒有殘留。

第一次執行（`results/20261106-013203-s6-errors`）中止：計數代理當時用 HTTP/1.0 + `Connection: close`，引擎的 `/api/version` 檢查因而誤判
「連不上」、略過翻譯模型檢查，`translation_model_missing` 的 session 反而開始執行，下一步得到 `session_active`。這揭露了 #3；代理已改成與 Ollama 相同的
HTTP/1.1 keep-alive。harness 也修正為：情境中途丟出例外時一定判 FAIL（第一次只剩「沒有殘留行程」一項而顯示 PASS）。

### 隔離測試（`ct2_isolation.py`，各 12 輪，`small` float16，每輪釋放後 `gc.collect()` 等 3 秒）

`results/20261106-011619-ct2-isolation`、`-012101-`、`-012255-`：

| 做法 | 顯示記憶體／輪 | 私有記憶體／輪 |
|---|---|---|
| `kikitori.asr.load_transcriber()`＋辨識，新執行緒（引擎的做法） | **+8.29 MiB** | +15.8 MiB |
| 同上，全部在主執行緒 | **+8.0 MiB** | +20.8 MiB |
| 同上，`CT2_CUDA_ALLOCATOR=cuda_malloc_async` | **+8.29 MiB** | −4.6 MiB |
| `ctranslate2.models.Whisper(...)` 後直接 `del`（不推論） | 0.0 | +0.02 |
| 同上，先 `unload_model()` | 0.0 | +0.08 |
| `ctranslate2.models.Whisper(...)`＋一次 encode（GPU）＋一步 generate，再 `del`（**不經 kikitori**） | **+8.0 MiB** | +7.8 MiB |
| 同上，`del` 前先 `unload_model()` | **+8.0 MiB** | +8.5 MiB |
| **只建立一次**模型物件，每輪 `load_model()` → 推論 → `unload_model()` | **0.0** | **−0.04** |

## 發現的問題

依嚴重程度排列。都**沒有修改** `src/`（依指示只回報）；修正建議附在各項。

### #1 開裝置失敗的 session 把整組模型留在 GPU（每次約 2.5–3 GB，直到循環垃圾回收）

- **證據**：情境 6 每一步之後量測，只有「格式正確但不存在的裝置」這一步之後顯示記憶體不回落：第 1 輪 0 → 3024 MiB（私有記憶體 522 → 4260 MiB），
  第 2 輪再 3064 → 5545 MiB（4372 → 6911 MiB），之後的正常 session 結束也只回到這個新的底，直到引擎結束（約 150 s 內都沒有被回收）。其他錯誤路徑都正常回落。
  `repro_open_error_cycle.py`（只用 CPU、假元件）重現：照 `_load_session()` 的寫法，session 失敗後「模型」仍存活，`gc.collect()` 後才釋放：

  ```text
  as in server.py today  : model alive after the session failed = True; after gc.collect() = False
  with exc.with_traceback(None): model alive after the session failed = False; after gc.collect() = False
  ```
- **原因**：`ThreadedSource` 的擷取執行緒開裝置失敗時把例外存進 `self._open_error`（也交給 `self._buffer.fail()`，`src/kikitori/capture/threaded.py:165-169`），
  `start()` 再 `raise self._open_error`（`:126`）。重新丟出同一個例外物件時，Python 把目前的 frame 接到它的 `__traceback__`：`ThreadedSource.start` →
  `Pipeline.start`（`self` = pipeline → 元件 → GPU 上的兩個模型）→ `EngineServer._load_session`（`src/kikitori/engine/server.py:829-833`）。於是
  source → 例外 → traceback → frame → pipeline → `pipeline._source` → source 成為循環，只能等 gc 第 2 代回收（引擎裡很少發生）。
  `server.py:988-993` 的註解處理過同類問題（切斷 `session.pipeline`），`Pipeline._fail()`（`src/kikitori/pipeline/pipeline.py:705-708`）也對執行中的錯誤做了
  `exc.with_traceback(None)`；**但「`pipeline.start()` 本身丟出」的路徑沒有處理**。
- **影響**：使用者選的裝置被拔掉、停用、或裝置清單過時時，每按一次「開始」就多佔約 3 GB 顯示記憶體與 2.5–3.7 GB 主記憶體；12 GB 的卡第二、三次重試就會
  擠到共用記憶體或讓 Ollama 的模型載入失敗。
- **建議修正**：`server.py:831-838` 的兩個 `except` 在記錄之後加 `exc.with_traceback(None)`（`logger.exception` 要在清掉之前呼叫），與 `Pipeline._fail()` 一致；
  更根本的是 `ThreadedSource.start()`／`ChunkBuffer` 不要重新丟出存起來的同一個例外物件（丟一個新的 `CaptureError`，`from None`，或丟出後清掉 `_open_error`
  ——但 `_buffer.fail()` 也存了一份，要一起處理）。回歸測試：把 `repro_open_error_cycle.py` 改成單元測試（`gc.disable()` + `weakref`，斷言 session 失敗後元件
  立即被釋放），先確認在目前的程式碼上失敗。之後以情境 6 驗證「missing_device」那一步之後顯示記憶體回落。

### #2 每建立一個 CTranslate2 模型物件（並在 GPU 上推論過）就漏約 8 MiB 顯示記憶體與 8–10 MiB 主記憶體

- **證據**：情境 1 每個 session +16.8 MiB 顯示記憶體／+18.4 MiB 私有記憶體（每個 session 建立 2 個模型）；情境 2 不論模型大小都一樣；
  隔離測試證明**與 kikitori 的程式碼、執行緒、配置器都無關**：純 `ctranslate2.models.Whisper(...)` + 一次推論 + `del` 就有 +8.0 MiB／輪，
  `unload_model()` 也不會釋放；不推論就不漏。推測是 CTranslate2 4.8.5 在模型的工作執行緒上第一次推論時建立的 CUDA 資源
  （cuBLAS handle／workspace、stream）在模型銷毀時沒有釋放——這點**沒有直接驗證**（只有二進位檔），上游 issue 也沒找到同樣的回報。
- **影響**：每開一次 session（含換語言、錯誤後重試）+17 MiB 顯示記憶體、+18–21 MiB 主記憶體，直到引擎重啟。一天開關 50 次約 0.85 GB 顯示記憶體
  ＋0.9–1 GB 主記憶體；12 GB 的卡同時跑 Ollama 時會更早擠到共用記憶體。比 v0.1.0 的洩漏（每次 2.6 GB）小兩個數量級，但會無限累積。
- **位置**（目前工作目錄的行號）：`src/kikitori/asr/whisper.py:465-478`（`_create_model()` 每次建立新的 `ctranslate2.models.Whisper`）、`:584-593`（`load_transcriber()` 在 CUDA 路徑每次都呼叫它並 `warmup()`）；
  每個 session 由 `src/kikitori/pipeline/factory.py:383-404`（`build_components()` 的 `create_transcriber()`）經 `src/kikitori/engine/server.py:793` 呼叫。
  注意：測試期間另一個代理人修改了 `whisper.py`（01:24）與 `factory.py`（01:44）——即時與完整是同一個模型時改成載入一次、`inter_threads=2` 共用權重。
  情境 1 與隔離測試在修改之前；之後的情境用的都是兩個不同的模型（不走共用路徑），每個 session 仍是 +16 MiB。共用路徑（一個物件、兩個 worker）
  每個 session 漏多少沒有量測——可用情境 1 搭配 `asr.fast_model = asr.final_model` 再跑一次。
- **建議修正**（隔離測試的最後一列：0.0 MiB／輪，權重確實離開 GPU，底維持 231.5 MiB）：在 `asr/whisper.py` 加一個行程內的模型物件池，
  key 為 `(模型路徑, device, compute_type, cpu_threads)`、每個 key 一串閒置物件（即時與完整可能是同一個模型）。`load_transcriber()` 有閒置物件時
  `model.load_model()` 取用，否則才建立；transcriber 不再使用時（`WhisperTranscriber.close()`，由 pipeline 結束時呼叫，或以 `weakref.finalize` 保底）
  `model.unload_model()` 後放回池中。代價：每個用過的組合保留一個空殼物件（約 8 MiB 顯示記憶體、一條 CTranslate2 工作執行緒），上限是用過的
  不同組合數（實務上 < 10）。同時建議把 `ct2_isolation.py` 的 `raw-infer` 回報給 CTranslate2 上游。
  修正後用情境 1（`cycles=20`）回歸：顯示記憶體斜率應 ≈ 0。

### #3 翻譯伺服器回應帶 `Connection: close` 時，Ollama 檢查誤判「連不上」

- **證據**：對一個合法的 HTTP/1.1 回應（`Content-Length` + `Connection: close`、內容 `{"version": "0.12.0"}`），`kikitori.hardware.ollama.ollama_version()`
  丟出 `OllamaUnreachableError: no response from 127.0.0.1:<port>: OSError`，原因 `OSError [WinError 10038]`（不是通訊端）；拿掉 `Connection: close` 就正常回傳。
- **原因**：`http.client` 在 `getresponse()` 時看到 `will_close` 會立刻 `conn.close()`，進而呼叫 `_PollingSocket.close()`
  （`src/kikitori/hardware/ollama.py:441-442`），它直接關掉真正的 socket；標準的 `socket.makefile()` 以參照計數延後關閉，這個替身沒有，
  之後 `_PollingReader.readinto()`（`:407`）讀內容就失敗。`pull_model()` 用同一個替身，應該也受影響（未實測）。
- **影響**：真正的 Ollama 用 keep-alive，不會遇到；但使用者在前面放了會關連線的反向代理（nginx `keepalive_timeout 0`、某些 HTTP/1.0 代理）時，
  `models.pull_translation` 回 `translation_server_unreachable`，`session.start` 也會略過翻譯模型檢查。
- **建議修正**：`_PollingSocket.close()` 不要立刻關閉，改為等 `_PollingReader` 被關閉時才關（仿 `socket._io_refs`：reader 的 `close()` 負責關 socket，
  替身的 `close()` 只在沒有 reader 時關）。先加一個以 `Connection: close` 伺服器重現的單元測試（上面的 10 行就是）。

### #4 長時間 session 結束後私有記憶體多留約 180 MiB（未定論）

情境 4（5 分鐘）結束後私有記憶體比暖機 session 結束後多 196 MiB（扣掉 #2 的 18 MiB 約 178 MiB），顯示記憶體多 50 MiB。執行中四分位中位數持平，
所以不像每句累積的洩漏；可能是長時間推論後 CTranslate2／CUDA 的主機端快取、Python／Windows heap 保留，或 WDDM 在顯示記憶體壓力下把配置降到
共用記憶體（本次確實發生）造成的。**需要在 GPU 沒有被其他程式占用時跑完整的 20 分鐘**（選單 4）再判斷：若結束後的增量隨 session 長度成長，就是洩漏。

## 干擾

- 情境 1–3 與隔離測試執行期間另一個實驗大致閒置（GPU 使用率中位數 1–3%、沒有其他 Ollama 模型；整張卡 1–9.7 GB）。
- 情境 4、5、6 與情境 3 的重跑執行時它恢復了（`qwen3.5:9b`、GPU 使用率中位數 76–99%、峰值 11.8/12.2 GB）：情境 4 的顯示記憶體被 WDDM 降級到共用記憶體，
  延遲與私有記憶體的跳動都不具代表性；情境 6 的載入時間（8.5–9.6 s，平常 2.5 s）與兩次「pipeline-partial／final did not stop within 5.0 s」警告
  （第一次執行，`20261106-013943`）應該也是 GPU 被占滿、推論變慢所致——值得在乾淨的環境再看一次。
- 情境 6 的 #1 與每步 +16 MiB 的 #2 都是引擎自己行程的計數器，不受干擾影響。
- 每個 `summary.json` 的 `data.contamination` 都有整張卡的用量範圍、GPU 使用率與其他已載入的 Ollama 模型；notes 會標出來。
- 引擎自己的數字（每個 pid 的計數器、計數代理的請求）不受別人影響；受影響的是「顯示記憶體是否被降級」與時序。

## 已知限制

- 情境 7 沒有跑（沒有建置與 driver）；情境 5 的 `models.pull_translation` 沒有跑（同機有人在評測那些模型）。
- 情境 4 只跑了 5 分鐘、而且受干擾；完整 20 分鐘留在選單。情境 1 只跑 12 輪、情境 2 兩輪、情境 3 切換 10 次、情境 6 兩輪（中等規模）；
  預設的完整規模留在選單。
- 情境 1、2、4 是在代理修正（#3）之前跑的：引擎的「翻譯模型有沒有裝」檢查被略過，但翻譯本身正常（請求數有記錄），不影響記憶體的結論。
- `ct2_isolation.py` 的根因（哪一個 CUDA 資源）是推測；修正方案已以 `raw-reuse` 驗證有效。
- 開發版引擎是 venv 的 python 啟動器 → 真正的 python 兩層，數字是兩者合計（啟動器約 3 條執行緒、幾 MB）。
