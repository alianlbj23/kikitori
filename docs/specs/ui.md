# Spec: ui（Tauri 桌面程式）

> 狀態：Milestone 7 第一階段已實作（2026-11-05）；第二階段（2026-11-05）對齊 Milestone 6 的引擎協定（硬體偵測、推薦、組合驗證、GPU 加速元件與授權）並改用 `bundle.resources` 打包引擎。之後加上自動推薦（第一次執行自動套用、更適合的組合提示、缺少模型的一鍵下載；見「自動推薦」）。
> 上層約定：`docs/specs/app-architecture.md`（行程架構、引擎協定、視窗、更新）與 `docs/specs/pipeline-engine.md`（協定欄位細節、設定檔結構）。本文件只補充 UI 端的設計，不改協定。

## 範圍

- `app/`：Tauri v2 + Vite + TypeScript（不用 UI 框架）。
- 兩個視窗：`main`（控制與設定）、`overlay`（雙語字幕）。
- Rust 端的引擎橋接（spawn 引擎、NDJSON、id 配對、逾時、重啟）、字幕視窗控制、全域快速鍵、自動更新。
- 開發用的假引擎 `app/dev/fake_engine.py`（故障注入、不停產生字幕；Milestone 6 的方法以固定的假資料模仿）、開發選單 `app/dev/launch.mjs`、圖示產生器 `app/dev/make_icon.py`。

## 視覺方向

依 frontend-design 指南：先從題材（「聞き取り」＝聽力練習、影片字幕）找視覺語彙，避免常見的預設樣式。

| 角色 | 色票 | 用途 |
|---|---|---|
| 藍（藍染） | `#22305C` | 左側控制列、主要按鈕；整個介面的結構色 |
| 薄藍 | `#EEF1F6` | 內容區背景（冷色紙張，不用米白） |
| 墨 | `#18203A` | 本文 |
| 鼠 | `#646D86` | 次要文字、說明 |
| 黃蘗（字幕黃） | `#F2C230` | **只用在「正在發生」的東西**：開始按鈕、正在聆聽的狀態燈、目前這句的標記、字幕視窗的譯文 |
| 紅 | `#B3344A` | 錯誤 |

- 深色模式跟隨系統（`prefers-color-scheme`），色票在 `src/styles/tokens.css` 重新定義。
- 字型：介面用系統的正體中文黑體（微軟正黑體 UI、蘋方-繁、Noto Sans CJK TC）；頁面標題與品牌字「聞き取り」用明朝體；原文依 `lang` 屬性切換日文字型（Yu Gothic、Hiragino、Noto Sans CJK JP），讓漢字顯示成正確的地區字形。時間碼用等寬數字（`tabular-nums`），不用等寬字體。
- 不用全大寫標籤、編號標記、卡片格線、箭頭按鈕；區塊之間只用留白與一條細線分隔。
- 唯一的動態：即時字幕換成完整翻譯時，譯文從白色轉成字幕黃（220 ms）；`prefers-reduced-motion` 時關閉。

## 主視窗 `main`

```text
┌──────────────┬──────────────────────────────────────────┐
│ 聞き取り      │  頁面標題（明朝）＋一行說明     [工具]    │
│ 模式          │ ───────────────────────────────────────── │
│ ▌影片翻譯     │                                          │
│  即時口譯     │  頁面內容                                 │
│ 聲音來源 [▼]  │                                          │
│ [   開始   ]  │                                          │
│ 字幕紀錄      │                                          │
│ 模型          │                                          │
│ 設定          │                                          │
│ ● 待命中      │                                          │
└──────────────┴──────────────────────────────────────────┘
```

### 左側控制列

- **模式**：影片翻譯（`media`，擷取系統播放的聲音）／即時口譯（`interpret`，麥克風）。說明文字顯示語言方向（口譯會依設定顯示「中文與日語互譯」或「中文與英語互譯」）。執行中不能切換。切換時以 `settings.set {mode, <mode>.device_id}` 記住。
- **聲音來源**：`devices.list` 依模式過濾（`loopback`／`microphone`）。第一個選項「預設：<裝置名稱>」代表 `device_id: null`。選擇會寫回設定檔該模式的 `device_id`；先前選的裝置不存在時提示並改用預設。
- **開始／停止**：字幕黃按鈕；執行中改成黃色外框的「停止」。開始時呼叫 `session.start {mode, device_id}`（`device_id` 一律明確傳送，`null` = 預設裝置），清空字幕紀錄，並自動顯示字幕視窗。
- **狀態列**：結合橋接狀態（引擎行程）與引擎的 `status` 事件：正在啟動引擎／待命中／正在載入模型／正在聆聽／發生錯誤，已停止／引擎意外結束，正在重新啟動／引擎無法啟動。失敗或停止時顯示原因與「重新啟動引擎」。

### 字幕紀錄頁

- 每句一列：時間碼（`start_sample / 16000`，`m:ss`）、來源語言單字標籤（日／英／中，滑鼠移上去顯示「日語譯成繁體中文」）、原文（較大）、譯文。
- 即時字幕（`is_final=false`）以淡色顯示並在左側加字幕黃細條；同一個 `utterance_id` 的完整翻譯到達時原地取代。最多保留 300 句。使用者停在底部時自動捲動，往上翻時不打擾。
- 工具：顯示字幕視窗、滑鼠穿透兩個開關，以及「清除紀錄」。開關下方說明全域快速鍵；快速鍵被其他程式占用時改為提示只能用開關。
- 空狀態：說明下一步（選模式與來源後按「開始」）。

### 模型頁

協定依 `pipeline-engine.md`「協定變更（Milestone 6）」。進入頁面（或按「重新偵測」）時同時送 `hardware.detect` 與 `models.list`；兩者在引擎的背景工作執行緒上依序處理，回應以 `id` 對應，可能晚於之後送出的其他請求（UI 不假設回應順序，見「回應順序」）。任一個失敗只影響自己那一塊。

- **這台電腦**：`hardware.detect.hardware`——作業系統、處理器（型號、實體核心／邏輯執行緒）、記憶體（總量與目前可用）、顯示卡（每張：名稱、顯示記憶體 `memory_total_mb`、驅動版本、運算能力；Apple Silicon 另外註明）、cuBLAS 狀態（不適用此平台／可以使用與來源：kikitori 下載的元件、自訂資料夾、系統 CUDA／未安裝）、翻譯服務（網址、本機或遠端、模型數或連線錯誤）。`errors[]` 以「偵測不完整：…」列在下方。
- **建議組合**：`recommendation` 全部欄位——硬體等級（`tier` 轉成文字，不認得的 id 原樣顯示）、即時與完整兩條辨識路徑各自的模型、裝置、精度、執行緒（0＝自動；GPU 路徑不顯示執行緒）、即時與完整翻譯模型（`null` 時說明翻譯服務不在本機、請在自訂組合選）、可給翻譯模型的記憶體（`translation_budget_gb`）、`reasons`、`warnings`（紅字）；`gpu_runtime_missing` 時提示先下載 GPU 加速元件。「**套用建議組合**」呼叫 `settings.apply_recommendation`，以回應的 `settings` 更新畫面與自訂組合、以回應的 `recommendation` 更新顯示，並告知寫入了什麼。**UI 不再自己把推薦轉成設定**（第一階段的 `recommendationPatch` 已移除），轉換規則只存在引擎。
- **GPU 加速元件**（`models.list.gpu_runtime`）：
  - `supported=false`：說明平台不支援（只有 Windows／Linux x86-64 搭配 NVIDIA 顯示卡），沒有按鈕。
  - 未安裝：依偵測結果說明現況（缺元件而改用 CPU／沒有 NVIDIA 顯示卡就不必下載），並依 hardware.md 顯示「這會從 PyPI 下載 NVIDIA cuBLAS（套件與版本，約 X MB），使用即表示同意 NVIDIA CUDA EULA」、授權網址（可選取的文字）、「閱讀授權條款」按鈕（以 opener 外掛開啟，只在引擎回報的網址正好是 `https://docs.nvidia.com/cuda/eula/index.html` 時出現）、勾選框「**我同意 NVIDIA CUDA EULA**」。勾選後「下載 GPU 加速元件」才啟用，按下才送 `models.download {"model": "gpu-runtime", "accept_license": true}`；同意只對這一次下載有效，不寫入任何檔案。Rust 端也只在前端明確傳 `acceptLicense: true` 時才帶 `accept_license`，所以 UI 出錯時引擎仍會回 `license_not_accepted`。
  - 下載中：`download_progress`（`model="gpu-runtime"`）的進度條；下載量到達總量後顯示「下載完成，正在驗證與解壓」（引擎此時不送進度）。`state: "error"` 顯示 `error` 並可重新勾選下載。
  - 錯誤碼：`license_not_accepted`、`gpu_runtime_unsupported`（同時重新讀取清單）、`download_in_progress`（不視為失敗，保留進度顯示）都有對應的繁體中文說明。
  - 安裝完成（`state: "done"`）：asr 在同一個引擎行程裡 CUDA 失敗過就不再嘗試，所以要重啟引擎。沒有 session 在跑、也沒有其他下載時**自動重新啟動引擎**（重啟後重新偵測，建議組合會改用 GPU）；否則只顯示提示與「重新啟動引擎」按鈕，不打斷使用者（規則在 `lib/combination.ts` 的 `afterGpuRuntimeInstalled`）。
  - 已安裝：顯示套件、版本、位置；`cublas_available=false` 時提示重啟引擎後重新偵測。
- **語音辨識模型**：`models.list.asr` 表格——模型（使用中、`recommended_for` 的「建議：即時／完整」、`no_speech_reliable=false` 時「不判斷非人聲」並以 title 說明）、大小、速度等級、記憶體（CPU `cpu_ram_mb`／GPU `vram_mb`，未知時「未知」）、授權、狀態（下載按鈕、進度、失敗原因與「重新下載」）。
- **翻譯模型**：伺服器狀態（`translation.server`；連不上時提示啟動 Ollama），`translation.models` 每個模型：名稱、建議用途、**「不可商用」**（`commercial_use=false`）與**「授權不適用於歐盟、英國、南韓」**（`excluded_regions`）兩種警示標籤、授權名稱（`known=false` 時「不在 kikitori 的清單中，授權請自行確認」）、約需記憶體、安裝狀態、授權網址（可選取的文字，不開啟）。第一版的 `translation.base_url／installed／error／presets` 不再讀取。
- **自訂組合**（表單）：即時／完整辨識模型（下拉，未下載的註明）、運算裝置（自動／CPU／CUDA，兩條路徑共用，`asr.device`）、即時／完整翻譯模型（輸入框，附 `translation.models` 自動完成）；收合的「**進階**」：兩條路徑各自的精度（自動＝`null`，或 CTranslate2 精度名稱）、CPU 執行緒（留空＝自動，1–256）、自行安裝的 cuBLAS 12 資料夾（每行一個絕對路徑，最多 16 個）。
  - **即時檢查**：每次輸入先做本地格式檢查（`validateCombinationForm`）；通過後等 350 ms 沒有新輸入才送 `models.validate {"settings": 表單的部分設定}`（不存檔）。結果顯示在表單下方：`errors` 紅字、`warnings` 一般文字。
  - **儲存**：`ok=false`、檢查中、本地格式錯誤、或引擎回 `invalid_settings`（訊息依欄位路徑標到對應欄位，例如 `asr.cuda_library_dirs[2]` → 資料夾欄）時「儲存組合」停用。檢查本身無法執行（逾時、引擎重啟中）時仍可儲存並說明原因——`settings.set` 一樣會驗證欄位，卡住的檢查不應鎖住使用者。規則在 `canSave()`。
  - 送出的部分設定一律包含組合的全部欄位，「自動」明確送 `null`；未儲存的修改在重新讀取清單時保留，「還原成目前設定」放棄修改。

#### 自動推薦

預設設定是安全的 CPU 組合（即時 base、完整 small），有 GPU 的使用者若沒找到「套用建議組合」就一直用慢的 CPU 模型；推薦改變（裝好 GPU 加速元件、Ollama 裝了新模型）時也沒有人告訴使用者。所以：

- **第一次執行**（`settings.get` 的 `is_new=true`，每個視窗最多一次，`lib/recommend.ts` 的 `FirstRunGate`）：等 `hardware.detect` 完成 → `settings.apply_recommendation {"only_if_new": true}` → 不阻擋操作的訊息，例如「已依這台電腦（NVIDIA GeForce RTX 5070，12 GB）套用建議組合：即時 small、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b。」附「到『模型』頁查看或修改」。缺 cuBLAS 時另說明下載 GPU 加速元件後可改用顯示卡。
  - 偵測失敗：保留預設組合並說明原因（「無法偵測這台電腦的硬體：…先使用預設的組合（即時 base、完整 small）…」）；設定檔沒寫，下次啟動會再試。套用失敗：說明原因、請使用者到模型頁確認（逾時的請求仍可能已寫入，所以不宣稱用的是預設值），並重新讀取設定。
  - **不覆寫使用者的選擇**：偵測期間使用者存了組合 → 不送套用；送出後才存的情況由引擎的 `only_if_new` 在同一把鎖內擋下（`applied: null` → 不顯示訊息）。自動套用不清掉組合表單尚未儲存的修改。
  - 之後手動修改組合不會再被自動套用（設定檔已存在，`is_new=false`）；只會出現下面的提示。
- **「有更適合這台電腦的組合」**：啟動時（第一次執行處理完之後）、引擎重啟後（例如 GPU 加速元件安裝後自動重啟）、進入模型頁與按「重新偵測」時送 `settings.preview_recommendation`。`differs=true` 且指紋不在 `settings.ui.dismissed_recommendations` 時顯示訊息（列出新組合）與「套用」（`settings.apply_recommendation`）、「不要再提醒」（指紋加入 `ui.dismissed_recommendations`，保留最近 10 個，`settings.set {"ui": {...}}`）、「關閉」（只在這個視窗不再顯示）。不同的推薦有不同指紋，會再提醒一次。套用、儲存自訂組合時立即收起，並丟掉還在路上的預覽回應（`Latest`），剛存的組合不會被舊的比較結果誤判。比較只在引擎做（`differs`），UI 不自己比對設定。
- **缺少的模型**：自動套用、按「套用」之後、儲存自訂組合之後，重新讀取 `models.list`；選用的 ASR 模型（即時、完整，去重）有未下載的 → 訊息「還需要下載 large-v3-turbo（共約 1.6 GB）才能開始。」與「下載缺少的模型」（只下載這幾個，不會自動下載 GB 級的檔案）。下載中原地更新進度（「正在下載 large-v3-turbo 40%…」），失敗顯示原因與「重新下載」，完成後「模型已下載完成，可以按『開始』了。」選用的模型還在下載時按「開始」只提示等待，不送 `session.start`；`session.start` 之後收到 `status error` 的 `model_not_downloaded` 時，同樣列出缺少的模型並提供下載（找不到缺少的模型時才顯示原本的說明）。

#### 回應順序

背景方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`settings.preview_recommendation`）可能晚於之後送出的請求才回應。Rust 端一律以 `id` 配對（`connection.rs`），不依賴順序；前端的即時檢查以 `Latest`（遞增 token）只套用**最新一次**輸入的結果，較慢回來的舊結果直接丟棄。測試：`connection.rs` 的亂序回應單元測試、`engine.rs` 對 `fake_engine.py`（背景方法延遲 0.3 秒）確認後送出的 `settings.get` 先回應且兩邊都拿到自己的結果、`combination.test.ts` 模擬兩個重疊請求反向完成。

### 設定頁

| 區塊 | 欄位 | 寫入設定 |
|---|---|---|
| 翻譯服務 | 服務網址、API 金鑰的**環境變數名稱** | `translation.{base_url, api_key_env}` |
| 翻譯模型的授權地區 | 勾選框「我確認我不在歐盟、英國或南韓使用 kikitori，並同意遵守這些翻譯模型的授權條款。」（預設不勾） | `translation.allow_restricted` |
| 語言 | 譯文語言（固定繁體中文，唯讀）、口譯對方的語言（日語／英語） | `partner_language` |
| 字幕外觀 | 譯文字級（14–96 px）、背景不透明度（0–100%）、字幕位置（貼齊上緣／下緣）、同時顯示的句數（1–3），附即時預覽 | `subtitle.{font_size, background_opacity, position, max_lines}` |
| 關於 | 程式版本、引擎版本與協定版本（假引擎會註明）、授權（`kikitori 以 MIT 授權發布`）、第三方元件聲明、第三方授權資料夾、「檢查更新」 | — |

- 翻譯模型改在模型頁的「自訂組合」選擇（第一階段在設定頁），讓同一份表單涵蓋整個組合、一起經過 `models.validate`（授權、是否在伺服器上、記憶體）。
- 授權地區說明：部分翻譯模型（例如騰訊 HY-MT）的授權不適用於歐盟、英國與南韓；kikitori 無法得知使用者所在地，所以預設不推薦。勾選後建議組合才會考慮這些模型；不勾選仍可在自訂組合手動選用，但檢查會顯示警告。改變這個勾選後，建議組合會在下次進入模型頁時重新計算。
- **關於**：顯示「本程式內含的 Intel® OpenMP 等第三方元件依其各自授權條款提供；不得對其進行逆向工程。」（Intel EULA for Developer Tools 的再散布條件，見 `packaging-ci.md`）。正式版有兩個授權資料夾，各自一行顯示完整路徑（可選取）與一個按鈕：程式本身 `<資源目錄>/licenses`（kikitori 的 LICENSE、Tauri／Rust crate 與前端 npm 套件的授權全文、MPL-2.0 crate 的原始碼取得方式；`packaging-ci.md`「app 的第三方授權聲明」）→「開啟程式的第三方授權資料夾」；語音引擎 `<資源目錄>/kikitori-engine/THIRD_PARTY_LICENSES` →「開啟引擎的第三方授權資料夾」。只顯示實際存在的資料夾；兩者都不在（開發版）時改為說明正式版的位置。路徑由 Rust 的 `app_about` 指令算出（`app_licenses`、`engine_licenses`，不存在時為 `null`）；開啟由 `open_third_party_licenses {folder: "app" | "engine"}` 完成——前端只能傳這兩個名稱之一（serde enum，其他值在反序列化時就被拒絕），路徑一律在 Rust 端決定，所以不需要給前端 opener 的路徑權限。
- 只存環境變數名稱，金鑰本身不經過 UI、不寫入任何檔案。
- 儲存前先在前端驗證（網址必須 http/https、變數名稱 `^[A-Za-z_][A-Za-z0-9_]*$`、數值範圍），錯誤顯示在欄位下方。引擎回傳 `invalid_settings` 時依訊息中的欄位路徑（例如 `translation.base_url: …`）標到對應欄位。
- 字幕外觀存在設定檔的 `subtitle` 物件（引擎原樣保存、不解讀）；儲存後同時透過 `overlay_set_style` 推給字幕視窗。
- 頁首顯示設定檔路徑（`settings.get` 的 `path`）。
- 設定檔 schema 2 的欄位都會讀取（`asr.fast_compute_type`／`final_compute_type`／`fast_cpu_threads`／`final_cpu_threads`／`cuda_library_dirs`、`translation.allow_restricted`）；格式不對的欄位改用預設值並提示，不會擋住頁面。

### 訊息

內容區上方的訊息列（最多三則、可關閉）：操作失敗的原因與下一步、引擎 `log` 事件的 warning／error、`status: error` 的 `code` 對應說明（例如 `model_not_downloaded` →「模型還沒下載。請到『模型』頁下載後再開始。」）、引擎錯誤碼的說明（含 `license_not_accepted`、`gpu_runtime_unsupported`、`download_in_progress`）、下載完成或失敗、GPU 加速元件安裝後的重啟、更新提示。

## 字幕視窗 `overlay`

- 設定：透明、無邊框、無陰影、置頂、不顯示在工作列、可調整大小、啟動時隱藏、顯示時不搶焦點、macOS 所有桌面空間可見（`tauri.conf.json`）。
- 內容：每句兩行，原文（字級 × 0.62，近白色）在上，譯文（設定的字級，字幕黃、粗體）在下。每行有可調不透明度的深色底板（0 = 無底板），文字另有八方向描邊與柔和陰影，無底板時在亮畫面也看得清楚。
- 即時字幕：原文與譯文都降低不透明度，譯文維持白色；完整翻譯到達時原地取代並轉成字幕黃。較舊的句子略淡，最新一句最醒目。
- 句數：顯示最新 `max_lines` 句（1–3）；位置依設定貼齊視窗下緣或上緣。
- 移動與縮放：非穿透模式下，滑鼠移上去會出現淡框、上方把手（「拖曳移動」與「隱藏」）、右下角縮放把手；拖曳視窗任何地方可移動（`startDragging`），拖曳右下角可縮放（`startResizeDragging`）。空視窗顯示放置提示。
- 滑鼠穿透：`set_ignore_cursor_events`。可從主視窗開關或全域快速鍵 **Ctrl+Alt+K**（macOS ⌘+Option+K）切換；穿透時隱藏所有框線與提示。程式啟動時一律為非穿透，避免使用者找不到視窗。
- 記住位置與大小：`tauri-plugin-window-state`（位置、大小、最大化；不記可見性）。
- 字幕視窗只能接收事件、移動／縮放自己、隱藏自己，不能控制引擎（見權限）。

## 字幕狀態規則（`src/lib/subtitles.ts`）

主視窗與字幕視窗共用同一套純函式：

1. 同一個 `utterance_id` 的即時字幕原地被取代；完整翻譯取代即時字幕。
2. 完整翻譯不會被晚到的即時字幕蓋掉；`utterance_id` ≤ 已完成的最大 id 的即時字幕一律忽略（被裁掉的舊句不會復活）。
3. 完整翻譯的原文與譯文都是空字串 → 移除該句（引擎判定為非語音時的「清除」）。空白的即時字幕忽略。
4. 依 `utterance_id` 排序，只保留最新 `maxLines` 句。
5. `session_id` 較舊的事件丟棄；較新的 session 從空白開始。沒有 `session_id` 的事件歸入目前 session。

## Rust 端（`app/src-tauri/src`）

| 模組 | 角色 |
|---|---|
| `protocol.rs` | 解析與驗證引擎每一行輸出：UTF-8、JSON 物件、`id` 為非負整數或 `null`、`result`／`error` 恰好一個、`result` 必須是物件、事件 `data` 必須是物件；`subtitle`／`status`／`download_progress`／`log` 依型別與範圍驗證（文字 ≤ 10 000 字、語言代碼只允許 BCP 47 字元、`end_sample ≥ start_sample`）。不認得的事件記錄後丟棄。 |
| `line_reader.rs` | 有上限的逐行讀取（1 MiB，與引擎相同）；超長行丟棄並讀到行尾，串流不會失去同步。 |
| `connection.rs` | 傳送請求（NDJSON）、以 `id` 配對回應、每個請求一個期限（涵蓋寫入與等待）。逾時的請求從等待表移除，晚到的回應記錄後丟棄。格式錯誤的行記錄 error（附截短的內容）後略過，不影響其他訊息。輸出關閉時所有等待中的請求立即失敗。 |
| `engine.rs` | 引擎行程：選擇指令（含正式版資源目錄的路徑）、spawn（不經 shell，Windows 加 `CREATE_NO_WINDOW`）、stderr 轉到 app 日誌、`engine.info` 握手並檢查 `protocol_version == 1`、意外結束時自動重啟（1 s、2 s、4 s 退避；60 秒內超過 3 次就停在「失敗」等使用者按重新啟動）、結束程式時先送 `engine.shutdown`，3 秒內（請求與等待共用同一個期限，`shutdown_within`）沒結束就強制結束；同步版 `shutdown_blocking` 給更新器的 `on_before_exit` 用（見「自動更新」）。 |
| `commands.rs` | 每個協定方法一個 Tauri 指令，先驗證參數（模型 id 字元集與長度、裝置 id 長度、設定必須是物件且 ≤ 64 KiB、模式只能 `media`／`interpret`；`accept_license` 只能用在 `gpu-runtime`，且只有前端明確傳 `true` 才送出），再依方法給不同期限：一般 10 s、背景方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`settings.preview_recommendation`）45 s（引擎以單一工作執行緒依序處理，要算進排在前面的請求）、`session.start` 30 s、`session.stop` 15 s、握手 120 s（第一次 `uv run` 可能要建立虛擬環境）。另有 `engine_restart`、`bridge_status`、`app_about`、`open_third_party_licenses` 與字幕視窗指令。 |
| `overlay.rs` | 字幕外觀（`deny_unknown_fields` 與範圍檢查）、穿透與可見狀態。 |
| `updater.rs` | 檢查與安裝更新的指令 `update_check`／`update_install`（tauri-plugin-updater 的 Rust API），安裝前關閉引擎（見「自動更新」）。 |
| `lib.rs` | 外掛（log、window-state、updater、process、opener、global-shortcut）、狀態、事件轉發、全域快速鍵、生命週期（關閉主視窗即結束程式）。 |

### 引擎指令的選擇

1. 環境變數 `KIKITORI_ENGINE_CMD`：JSON 字串陣列（路徑有空白時用），或以空白分隔的指令。
2. 除錯版（`debug_assertions`）：在 repo 根目錄執行 `uv run kikitori engine`。
3. 正式版：`<資源目錄>/kikitori-engine/kikitori-engine[.exe]`（PyInstaller one-folder，以 `bundle.resources` 隨安裝檔散布；資源目錄為 Tauri `PathResolver::resource_dir()`：Windows 是安裝目錄、macOS 是 `Contents/Resources`、Linux deb／AppImage 是 `/usr/lib/<productName>/`），工作目錄是該資料夾。`KIKITORI_ENGINE_CMD`、`KIKITORI_ENGINE_CWD` 在正式版一樣可以覆寫。

`KIKITORI_ENGINE_CWD`（絕對路徑）可覆寫工作目錄。子行程繼承程式的環境變數，所以 `KIKITORI_ENGINE_FAKE`、`KIKITORI_CONFIG_DIR` 會直接傳給引擎。

### 前端收到的事件

| Tauri 事件 | 內容 |
|---|---|
| `engine://subtitle`、`engine://status`、`engine://download-progress`、`engine://log` | 已驗證的引擎事件（欄位同協定） |
| `bridge://state` | `{phase: starting|ready|restarting|stopped|failed, detail, restarts, command, source, info}` |
| `overlay://state` | `{style, click_through, visible, shortcut, shortcut_registered}` |

前端（`src/lib/validate.ts`）再驗證一次所有事件與指令結果；不合格的 payload 記錄後丟棄。指令錯誤統一為 `{kind: engine|timeout|disconnected|invalid_input, …}`，由 `describeError` 轉成繁體中文說明。

### 權限（最小權限）

`build.rs` 以 `AppManifest::commands` 為每個指令產生 `allow-*` 權限，未列在 capability 的指令一律拒絕。

| 視窗 | 權限 |
|---|---|
| `main`（`capabilities/main.json`） | 事件 listen／unlisten、讀版本號、`update_check`／`update_install`（不給 `updater:default`：外掛自己的 JavaScript 指令會繞過關閉引擎的步驟）、`process:allow-restart`、`opener:allow-open-url`（scope 只有 `https://docs.nvidia.com/cuda/eula/index.html`）、全部引擎與字幕視窗指令、`app_about`、`open_third_party_licenses`（`engine_shutdown` 除外：只在程式結束時由 Rust 呼叫） |
| `overlay`（`capabilities/overlay.json`） | 事件 listen／unlisten、`start-dragging`、`start-resize-dragging`、`overlay_get_state`、`overlay_set_visible` |

opener 外掛（`tauri-plugin-opener` 2.7.2，Apache-2.0 OR MIT）只給前端開這一個網址；沒有 `allow-default-urls`、沒有 `open-path`／`reveal-item-in-dir`。兩個授權資料夾由 Rust 指令以 opener 的 Rust API 開啟（前端只選 `app`／`engine`，路徑在 Rust 端決定）。`combination.test.ts` 檢查 capability 裡的 opener 權限正好是這一筆。

另設 CSP：`default-src 'self' ipc: http://ipc.localhost; img-src 'self' data:; style-src 'self'`；前端不使用 `innerHTML`，引擎傳來的文字一律以 `textContent` 顯示。

## 自動更新

- `tauri-plugin-updater`，端點 `https://github.com/alianlbj23/kikitori/releases/latest/download/latest.json`，公鑰已寫入 `tauri.conf.json`（對應的私鑰只在維護者本機與 GitHub Secrets，不在 repo）。
- `bundle.createUpdaterArtifacts: true`（Tauri v2 格式）。打包時需要 `TAURI_SIGNING_PRIVATE_KEY`（與密碼）環境變數，只放在本機與 GitHub Secrets。發布流程、`latest.json` 的平台鍵與 Secrets 設定見 `release-update.md`。
- macOS：`bundle.macOS.signingIdentity` 為 `"-"`（ad-hoc 簽章，不做付費簽章與公證）。
- 正式版啟動時檢查一次（15 秒逾時，失敗不打擾）；設定頁可手動檢查。流程：「kikitori x.y.z 已經推出」→「下載並安裝」→ 下載進度 →「已安裝，重新啟動後生效」→「立即重新啟動」（`relaunch`）。Windows 安裝程式使用 `passive` 模式，通常會自行關閉並重開程式。
- **更新前關閉引擎**：Windows 上外掛在啟動 NSIS 安裝程式後直接 `std::process::exit(0)`，不會經過 `RunEvent::Exit`，`lib.rs` 結束時的 `engine.shutdown` 不會執行，引擎只會在 stdin 關閉後自己結束（實測約 50 ms，但推論中可能較慢，而安裝程式正要覆寫它的檔案）。外掛的 JavaScript 指令以固定的 `on_before_exit`（只呼叫 `cleanup_before_exit`）建立更新器，無法換掉，所以檢查與安裝改由 `updater.rs` 的 `update_check {timeoutMs}`（以 `UpdaterExt::updater_builder()` 建立更新器並掛上自己的 `on_before_exit`，找到的 `Update` 存在 Rust 端）與 `update_install {onEvent: Channel, timeoutMs}`（`download_and_install`，進度事件與外掛相同的 `Started`／`Progress`／`Finished`）執行；兩個逾時都只接受 1 秒–30 分鐘。`on_before_exit`（tauri-plugin-updater 2.14.1 只在 Windows、驗證簽章之後、啟動安裝程式之前呼叫）：`shutdown_engine_before_exit()` → `EngineBridge::shutdown_blocking(runtime, 3 s)`——送 `engine.shutdown`，3 秒內沒結束就強制結束，整體最多約 7 秒（`blocking_shutdown_bound`），失敗只記 log、更新照常進行——再呼叫外掛預設的 `cleanup_before_exit()`。之後若安裝程式啟動失敗（`update_install` 回錯誤），重新顯示主視窗（字幕視窗原本可見時也一起）並重啟引擎。同時只能有一個 `update_install`（第二次呼叫直接回錯誤）。簽章錯誤在下載階段就失敗，引擎不受影響。macOS／Linux 安裝後不會結束程式，「立即重新啟動」走 `request_restart` → `RunEvent::Exit`，原本的結束流程就會關閉引擎。

## 打包與引擎資源

- 引擎是 PyInstaller **one-folder**（`kikitori-engine[.exe]`、`_internal/`、`THIRD_PARTY_LICENSES/`），由 `uv run python packaging/engine/build.py` 放到 `src-tauri/binaries/kikitori-engine/`（已列入 `.gitignore`），以 Tauri `bundle.resources` 整個資料夾帶進安裝檔（決定與量測見 `packaging-ci.md`）。不用 `externalBin`（只能帶單一執行檔）。
- **正式打包的設定另外放**：`src-tauri/tauri.sidecar.conf.json` 只有 `{"bundle": {"resources": {"binaries/kikitori-engine/": "kikitori-engine/", "licenses/": "licenses/"}}}`（`licenses/` 由 `packaging/app/licenses.py` 產生，見 `packaging-ci.md`），打包時合併：`npm run tauri build -- --config src-tauri/tauri.sidecar.conf.json`。理由：`cargo build`（tauri-build）會檢查 resources 路徑存在；若寫進 `tauri.conf.json`，每個 `cargo test`、`cargo clippy`、`tauri dev`、除錯版建置都得先建置 170 MB 的引擎或放佔位目錄。分開之後，開發與 CI 的 Rust job 不需要引擎；正式打包若忘了先建置引擎，會在建置時就失敗，而不是做出沒有引擎的安裝檔。檔名沿用第一階段的 `tauri.sidecar.conf.json`，以免 CI／文件的引用失效。
- 正式版從資源目錄啟動引擎（見「引擎指令的選擇」），設定頁「關於」顯示並可開啟 `licenses/`（程式本身）與 `kikitori-engine/THIRD_PARTY_LICENSES`（引擎）。
- 打包後要在各平台確認：Linux／macOS 安裝後的執行檔保有執行權限、macOS 簽章不破壞 PyInstaller 的 ad-hoc 簽章（`packaging-ci.md` 的 release 步驟）。
- 版本號 `0.1.0` 與 `pyproject.toml` 一致（`package.json`、`Cargo.toml`、`tauri.conf.json`；由 `scripts/sync_version.py` 同步）。

## 開發

需要 Node 24、Rust stable（MSVC）、uv。

```text
cd app
npm install
npm run menu          # 互動選單：選擇引擎後執行 tauri dev，或建置除錯版
```

選單項目：

1. 正式引擎的假模式（`KIKITORI_ENGINE_FAKE=1 uv run kikitori engine`，合成聲音、假辨識與翻譯、假 8 GB NVIDIA 顯示卡缺 cuBLAS；GPU 加速元件以假 wheel 真的走一次安裝；建議）
2. 正式引擎的假模式，假硬體 Apple Silicon（`KIKITORI_ENGINE_FAKE_HARDWARE=apple`：GPU 加速元件不支援）
3. 正式引擎的假模式，GPU 加速元件下載失敗（`KIKITORI_ENGINE_FAKE_HARDWARE=broken-runtime`：雜湊不符）
4. `app/dev/fake_engine.py`（只用標準庫；字幕會不停產生，部分→完整的過程比較明顯）
5. 假引擎加故障注入（每 3 句插入一行壞資料、25 秒後當機，用來看重啟與錯誤處理）
6. 正式引擎（實際擷取聲音、載入模型）
7. 建置除錯版執行檔（`tauri build --debug --no-bundle`）

正式引擎的假模式把 GPU 加速元件裝在行程專屬的暫存目錄，所以安裝後 UI 自動重啟引擎時，新的引擎又回到「未安裝」——這是假模式的限制，真的引擎裝在使用者資料目錄、重啟後仍在。`slow-runtime` 情境（下載很慢，用來看取消）可用環境變數自行指定。

也可以直接設定環境變數：

```text
KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
npm run tauri dev
```

`fake_engine.py` 參數：`--garbage-every N`（每 N 個字幕事件後輸出一行壞 JSON）、`--crash-after SECONDS`（以結束碼 3 模擬當機）。它保留下來是因為正式引擎的假模式沒有故障注入、字幕也會結束；Milestone 6 的方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`gpu-runtime` 授權閘門）只做形狀正確的固定模仿，背景方法延遲 0.3 秒、在單一執行緒依序回應，以重現「回應晚於之後的請求」。推薦與驗證的規則以正式引擎為準。

圖示：`uv run --no-project python app/dev/make_icon.py` 產生 `src-tauri/icons/source/icon.png`（藍色底、白色原文條與字幕黃譯文條，全為本專案原創），再執行 `npx tauri icon src-tauri/icons/source/icon.png`。

## 測試

| 指令（在 `app/` 或 `app/src-tauri/`） | 內容 |
|---|---|
| `npm test` | vitest：更新指令的結果與進度事件驗證、字幕狀態規則、事件與結果驗證（`hardware.detect`／`models.list`／`settings.apply_recommendation` 用正式引擎假模式實際輸出的 `src/lib/testdata/engine-fake-nvidia.json`）、設定頁與自訂組合的表單驗證與 patch、schema 2 欄位、`models.validate` 結果、儲存條件、只套用最新的檢查結果、GPU 加速元件安裝後的重啟決定、opener 權限只開放 EULA 網址、`app_about`（兩個授權資料夾路徑，可為 `null`、型別錯誤時拒絕）、格式化、自動推薦（`recommend.test.ts`：第一次執行只套用一次且先等偵測、訊息文字、不是新設定檔或使用者已存組合時不套用、引擎回 `applied: null` 視為略過、提示依 `differs` 與指紋顯示／隱藏、「不要再提醒」清單、缺少模型的提示與進度、下載中不能開始、`api.ts` 呼叫的每個指令都在 `build.rs` 與 `capabilities/main.json`） |
| `npm run build` | `tsc` 型別檢查 + `vite build` |
| `cargo test` | 更新前關閉引擎（`fake_engine.py`：在 runtime worker 上同步呼叫，`engine.shutdown` 讓引擎自己結束；`--ignore-shutdown` 不回應時在期限後強制結束；沒有引擎時立即返回）、更新指令的逾時範圍與進度事件格式、協定解析與驗證、有上限的逐行讀取、id 配對（亂序回應）、逾時與晚到回應、錯誤回應、壞行略過、輸出關閉時請求失敗、指令選擇、重啟退避、參數驗證；正式版資源目錄的引擎路徑與覆寫、`about_info`（兩個授權資料夾各自只在存在時回報；`licenses` 與 `tauri.sidecar.conf.json` 的資源名稱一致；`LicenseFolder` 只接受 `app`／`engine`）、`models.download`／`models.validate` 的參數（授權旗標只給 `gpu-runtime`）；另外的端到端測試以真的子行程執行：`fake_engine.py`（含壞資料注入）、`fake_engine.py`（背景方法的回應晚於之後的請求，仍各自配對）、`fake_engine.py --crash-after`（自動重啟）、正式引擎假模式（`KIKITORI_ENGINE_FAKE=1`、`KIKITORI_CONFIG_DIR` 指到暫存目錄）的基本協定，以及 Milestone 6：`hardware.detect` 與一般請求並行、`models.list`、`models.validate`（候選不存檔、`invalid_settings`）、`settings.apply_recommendation`（安裝前 CPU）、`gpu-runtime` 未同意 → `license_not_accepted`（訊息含 EULA 網址、沒有下載事件）、同意 → 安裝到 `done` → 推薦改為 CUDA；Apple Silicon 情境 → `gpu_runtime_unsupported`。需要 `uv` 在 PATH。 |
| `cargo clippy --all-targets -- -D warnings` | lint |

## 各平台需要實機確認的地方

- **透明視窗**：Windows（WebView2）已確認。macOS 需要 `macOSPrivateApi: true`（已開，代表不能上架 Mac App Store）。Linux（WebKitGTK）需要合成器（compositor）：X11 容器實測（Xvfb、WebKitGTK 2.52）沒有合成器時字幕視窗是黑底不透明、會蓋住後面的視窗；開了 picom（xrender）就真的透明。Wayland 待實機確認。
- **置頂**：macOS 全螢幕播放的影片位於獨立 Space，可能蓋過字幕視窗（已設 `visibleOnAllWorkspaces`，仍需實測）；Wayland 不允許應用程式自行置頂，GNOME 上可能無效。
- **滑鼠穿透**：Windows／macOS 由系統支援；Linux X11 可用，Wayland 依合成器而定。
- **全域快速鍵**：X11 容器實測 Ctrl+Alt+K（xdotool 送出）可切換滑鼠穿透；同一個按鍵已被其他程式（例如另一個 kikitori）佔用時 UI 正確改提示用開關。Wayland 下 `global-hotkey` 可能無法註冊；macOS 可能要求輔助使用權限。
- **無邊框縮放**：右下角把手用 `startResizeDragging`；Linux 部分視窗管理員不支援。
- **字型**：macOS／Linux 會改用蘋方、Hiragino、Noto CJK。沒有任何 CJK 字型的 Linux（例如精簡安裝只有 DejaVu）整個介面會變成豆腐方塊（容器實測），所以 deb 以 Recommends 建議 `fonts-noto-cjk`；AppImage 不帶字型，靠系統安裝。
- **表單控制項**：WebKitGTK 以原生外觀畫 `<select>`、不套用 CSS 背景色；深色底上的選單要設 `color-scheme: dark`（側欄的聲音來源選單就是這樣修的）。
