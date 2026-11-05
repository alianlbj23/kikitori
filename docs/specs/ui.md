# Spec: ui（Tauri 桌面程式）

> 狀態：Milestone 7 第一階段已實作（2026-11-05）；第二階段（2026-11-05）對齊 Milestone 6 的引擎協定（硬體偵測、推薦、組合驗證、GPU 加速元件與授權）並改用 `bundle.resources` 打包引擎。之後加上自動推薦（第一次執行自動套用、更適合的組合提示、缺少模型的一鍵下載；見「自動推薦」）。Milestone 15 第一階段（2026-11-05）：介面多語系的機制、全部介面文字抽到字典（zh-TW 基準、English），見「介面多語系」。第二階段（2026-11-05）：對齊 Milestone 12–14 的引擎協定——全部引擎訊息碼的字典、任意語言組合的語言選單與推薦預覽、開始時才下載缺少的模型、「顯示翻譯」開關與只顯示原文的字幕（見「語言選單」「開始前下載缺少的模型」「只顯示原文」）。
> 上層約定：`docs/specs/app-architecture.md`（行程架構、引擎協定、視窗、更新）與 `docs/specs/pipeline-engine.md`（協定欄位細節、設定檔結構）。本文件只補充 UI 端的設計，不改協定。

## 範圍

- `app/`：Tauri v2 + Vite + TypeScript（不用 UI 框架）。
- 兩個視窗：`main`（控制與設定）、`overlay`（雙語字幕）。
- Rust 端的引擎橋接（spawn 引擎、NDJSON、id 配對、逾時、重啟）、字幕視窗控制、全域快速鍵、自動更新。
- 開發用的假引擎 `app/dev/fake_engine.py`（故障注入、不停產生字幕；Milestone 6 與 12–14 的方法以固定的假資料模仿）、開發選單 `app/dev/launch.mjs`、圖示產生器 `app/dev/make_icon.py`、測試資料產生器 `app/dev/extract_message_codes.py`（引擎訊息碼清單）與 `app/dev/capture_engine_fake.py`（正式引擎假模式的回應）。

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

- **模式**：影片翻譯（`media`，擷取系統播放的聲音）／即時口譯（`interpret`，麥克風）。說明文字依設定顯示語言方向：影片翻譯「韓文譯成西班牙文」（收聽語言為自動偵測時「自動偵測語言，譯成…」），口譯「繁體中文與日文互譯」；關閉「顯示翻譯」時改為「…（只顯示原文）」。語言名稱由 `Intl.DisplayNames` 依介面語系產生。執行中不能切換。切換時以 `settings.set {mode, <mode>.device_id}` 記住，設定頁的語言區塊同時換成該模式的欄位。
- **聲音來源**：`devices.list` 依模式過濾（`loopback`／`microphone`）。第一個選項「預設：<裝置名稱>」代表 `device_id: null`。選擇會寫回設定檔該模式的 `device_id`；先前選的裝置不存在時提示並改用預設。
- **開始／停止**：字幕黃按鈕；執行中改成黃色外框的「停止」。開始時呼叫 `session.start {mode, device_id}`（`device_id` 一律明確傳送，`null` = 預設裝置），清空字幕紀錄，並自動顯示字幕視窗。選用的模型還沒下載時先下載、完成後自動開始（見「開始前下載缺少的模型」）；下載與自動開始進行中按鈕停用。
- **狀態列**：結合橋接狀態（引擎行程）與引擎的 `status` 事件：正在啟動引擎／待命中／正在載入模型／正在聆聽／發生錯誤，已停止／引擎意外結束，正在重新啟動／引擎無法啟動。失敗或停止時顯示原因與「重新啟動引擎」。

### 字幕紀錄頁

- 每句一列：時間碼（`start_sample / 16000`，`m:ss`）、來源語言單字標籤（日／英／中，滑鼠移上去顯示「日語譯成繁體中文」）、原文（較大）、譯文。原文與譯文都加 `dir="auto"`（阿拉伯語由右到左）。譯文是空字串時以 CSS `attr(data-empty)` 顯示字典裡的「（沒有譯文）」／「翻譯中」（第一階段寫死在 CSS 的中文移到字典）。
- 「顯示翻譯」關閉時只列原文（譯文列不顯示，`lib/subtitles.ts` 的 `lineDisplay`）；已翻譯的句子保留譯文，再打開時重新顯示。
- 即時字幕（`is_final=false`）以淡色顯示並在左側加字幕黃細條；同一個 `utterance_id` 的完整翻譯到達時原地取代。最多保留 300 句。使用者停在底部時自動捲動，往上翻時不打擾。
- 工具：顯示字幕視窗、滑鼠穿透、**顯示翻譯**三個開關，以及「清除紀錄」。開關下方說明全域快速鍵；快速鍵被其他程式占用時改為提示只能用開關。
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
- **缺少的模型**：自動套用、按「套用」之後、儲存自訂組合之後，重新讀取 `models.list`；選用的 ASR 模型（即時、完整，去重）有未下載的 → 訊息「還需要下載 large-v3-turbo（共約 1.6 GB）才能開始。」與「下載缺少的模型」（只下載這幾個，不會自動下載 GB 級的檔案）。下載中原地更新進度（「正在下載 large-v3-turbo 40%…」），失敗顯示原因與「重新下載」，完成後「模型已下載完成，可以按『開始』了。」使用者不先下載也沒關係：按「開始」時會下載缺少的模型再自動開始（見「開始前下載缺少的模型」；第二階段起取代「下載中按開始只提示等待」與 `model_not_downloaded` 時只列出缺少模型的做法）。儲存語言（即使同時改用推薦的模型）不會跳出這則訊息：選語言不下載是語言規格的要求。

#### 回應順序

背景方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`settings.preview_recommendation`）可能晚於之後送出的請求才回應。Rust 端一律以 `id` 配對（`connection.rs`），不依賴順序；前端的即時檢查以 `Latest`（遞增 token）只套用**最新一次**輸入的結果，較慢回來的舊結果直接丟棄。測試：`connection.rs` 的亂序回應單元測試、`engine.rs` 對 `fake_engine.py`（背景方法延遲 0.3 秒）確認後送出的 `settings.get` 先回應且兩邊都拿到自己的結果、`combination.test.ts` 模擬兩個重疊請求反向完成。

### 設定頁

| 區塊 | 欄位 | 寫入設定 |
|---|---|---|
| 翻譯服務 | 服務網址、API 金鑰的**環境變數名稱** | `translation.{base_url, api_key_env}` |
| 翻譯模型的授權地區 | 勾選框「我確認我不在歐盟、英國或南韓使用 kikitori，並同意遵守這些翻譯模型的授權條款。」（預設不勾） | `translation.allow_restricted` |
| 介面語言（頁面最上方、在設定表單之外） | 「跟隨系統」與 13 個語系（以各自語言的名稱列出），選擇後立即套用並儲存 | `ui.locale`（`settings.set {"ui": {"locale": …}}`） |
| 語言（獨立表單 `languages-form`，自己的「儲存語言」） | 影片翻譯：收聽語言（含「自動偵測」）＋字幕語言；即時口譯：我的語言＋對方的語言。下方是推薦組合預覽與「同時改用推薦的模型」（預設勾選）。見「語言選單」 | `source_language`、`target_language`、`partner_language`（＋勾選時推薦的 `asr`／`translation` 模型） |
| 字幕 | 「顯示翻譯」開關（改變即套用，不經「儲存設定」，見「只顯示原文」）；譯文字級（14–96 px）、背景不透明度（0–100%）、字幕位置（貼齊上緣／下緣）、同時顯示的句數（1–3），附即時預覽 | `translation.enabled`；`subtitle.{font_size, background_opacity, position, max_lines}` |
| 關於 | 程式版本、引擎版本與協定版本（假引擎會註明）、授權（`kikitori 以 MIT 授權發布`）、第三方元件聲明、第三方授權資料夾、「檢查更新」 | — |

- 翻譯模型改在模型頁的「自訂組合」選擇（第一階段在設定頁），讓同一份表單涵蓋整個組合、一起經過 `models.validate`（授權、是否在伺服器上、記憶體）。
- 授權地區說明：部分翻譯模型（例如騰訊 HY-MT）的授權不適用於歐盟、英國與南韓；kikitori 無法得知使用者所在地，所以預設不推薦。勾選後建議組合才會考慮這些模型；不勾選仍可在自訂組合手動選用，但檢查會顯示警告。改變這個勾選後，建議組合會在下次進入模型頁時重新計算。
- **關於**：顯示「本程式內含的 Intel® OpenMP 等第三方元件依其各自授權條款提供；不得對其進行逆向工程。」（Intel EULA for Developer Tools 的再散布條件，見 `packaging-ci.md`）。正式版有兩個授權資料夾，各自一行顯示完整路徑（可選取）與一個按鈕：程式本身 `<資源目錄>/licenses`（kikitori 的 LICENSE、Tauri／Rust crate 與前端 npm 套件的授權全文、MPL-2.0 crate 的原始碼取得方式；`packaging-ci.md`「app 的第三方授權聲明」）→「開啟程式的第三方授權資料夾」；語音引擎 `<資源目錄>/kikitori-engine/THIRD_PARTY_LICENSES` →「開啟引擎的第三方授權資料夾」。只顯示實際存在的資料夾；兩者都不在（開發版）時改為說明正式版的位置。路徑由 Rust 的 `app_about` 指令算出（`app_licenses`、`engine_licenses`，不存在時為 `null`）；開啟由 `open_third_party_licenses {folder: "app" | "engine"}` 完成——前端只能傳這兩個名稱之一（serde enum，其他值在反序列化時就被拒絕），路徑一律在 Rust 端決定，所以不需要給前端 opener 的路徑權限。
- 只存環境變數名稱，金鑰本身不經過 UI、不寫入任何檔案。
- 儲存前先在前端驗證（網址必須 http/https、變數名稱 `^[A-Za-z_][A-Za-z0-9_]*$`、數值範圍），錯誤顯示在欄位下方。引擎回傳 `invalid_settings` 時依訊息中的欄位路徑（例如 `translation.base_url: …`）標到對應欄位。
- 字幕外觀存在設定檔的 `subtitle` 物件（引擎原樣保存、不解讀）；儲存後同時透過 `overlay_set_style` 推給字幕視窗。
- 頁首顯示設定檔路徑（`settings.get` 的 `path`）。
- 設定檔 schema 2 的欄位都會讀取（`asr.fast_compute_type`／`final_compute_type`／`fast_cpu_threads`／`final_cpu_threads`／`cuda_library_dirs`、`translation.allow_restricted`），schema 3 另讀 `source_language`（語言碼或 `auto`）、`translation.enabled`；語言欄位只檢查格式（`^[A-Za-z]{2,3}(-[A-Za-z0-9]{1,8})?$`），清單由引擎決定。格式不對的欄位改用預設值並提示，不會擋住頁面。

### 語言選單

規格見 `languages-i18n.md`「UI」。邏輯在 `app/src/lib/languages.ts`（可測），`main.ts` 只畫畫面。

- **清單**：引擎就緒時送 `languages.list`（UI 不自己維護語言表）。選項依引擎的順序（常用在前），文字是「原生名（介面語系的名稱）」，兩者相同時只寫一次（英文介面的 English、繁中介面的繁體中文）；收聽語言的第一個選項是「自動偵測」（`auto`）。阿拉伯語選項加 `dir="auto"`。
- **篩選**：每個選單上方一個搜尋框，比對語言代碼、原生名、介面語系名稱與英文名，不分大小寫、忽略變音符號（輸入 espanol 找得到 Español）；目前選的語言一定留在選單裡。在搜尋框按 Enter 選第一個符合的語言（不送出表單）。
- **欄位依模式**：影片翻譯顯示收聽語言＋字幕語言；即時口譯顯示我的語言（同一個 `target_language`）＋對方的語言。另一模式的欄位只隱藏，值保留在表單狀態。
- **本地檢查**（`validateLanguageForm`）：代碼必須在清單內；口譯的兩個語言 Whisper 語言不能相同（繁中與簡中也算同一個）。影片翻譯允許收聽與字幕同語言（引擎只做字形轉換）。
- **預覽**：每次選擇後 250 ms 沒有新的選擇就送 `settings.preview_languages`（只帶目前模式的欄位與 `mode`），以 `Latest` 只套用最新一次的回應；引擎回 `invalid_settings` 時依欄位路徑標到對應欄位。預覽顯示：即時／完整辨識模型與翻譯模型各一列，狀態為「已安裝」「第一次使用時下載，約 X」「無法確認是否已安裝（翻譯服務沒有回應）」；不需要翻譯（同一種語言）、已關閉「顯示翻譯」、此語言組合沒有支援的翻譯模型（`translation_status=unsupported`，紅字）、翻譯服務不在本機（`remote`）各有說明；下一行是下載合計（`download_bytes`、`download_size_known`）。再下面列出與語言有關的引擎訊息（預覽的 `messages`，以及推薦裡 `asr_language_quality_low`、`translation_unsupported`、`source_language_auto` 等語言相關的 code；硬體類訊息留在模型頁）。
- **儲存**（`languagesSave`）：只送目前模式的語言欄位；影片翻譯時若新的字幕語言會與對方的語言衝突，一併把對方的語言換成清單中第一個不衝突的語言（否則之後切到口譯會被引擎拒絕）。勾選「同時改用推薦的模型」且預覽正是這組語言時，再合併預覽的 `patch`；預覽還沒回來（勾選時「儲存語言」停用到預覽回來）或失敗時只存語言並說明。改用推薦模型時與「套用建議組合」相同：重填自訂組合表單、收起「更適合的組合」提示、視為使用者的選擇（第一次執行不再自動套用）。不下載任何模型。

### 開始前下載缺少的模型

選語言不下載；按「開始」才下載（`languages-i18n.md`）。狀態機在 `app/src/lib/start-downloads.ts`（`StartDownloads`，注入引擎呼叫，可測），畫面是內容區上方的下載面板（每個模型一列：名稱、進度條、百分比與大小，下面一句說明與「重試」「取消」）。

- **開始前**：依最近一次的 `models.list` 找出選用但未下載（或下載中）的 ASR 模型，有就先下載再開始，不送 `session.start`。翻譯模型不在開始前檢查：只有引擎知道伺服器是不是 Ollama。
- **開始後引擎回報**：`status error` 的 `translation_model_missing` → 對 `data.models`（UI 只保留引擎能下載的名稱）逐一 `models.pull_translation`；`model_not_downloaded` → 重新讀取 `models.list`，下載缺少的 ASR 模型（找不到才顯示原本的錯誤說明）。引擎先檢查翻譯模型、再載入 ASR，所以兩種都缺時會依序下載兩輪。
- **進度**：`download_progress` 以 `kind`＋`model` 對應（ASR 與翻譯模型可能同名；沒有 `kind` 的舊引擎事件只對應 ASR）。翻譯模型的總量會在下載中變大（Ollama 逐層回報），第一筆是 0／0（顯示「準備下載」）。面板中的下載不再另外跳「下載完成／失敗」訊息。
- **全部完成**：自動再送一次 `session.start`（與按「開始」相同的模式與裝置），失敗照一般「無法開始」回報。同一次開始中已下載過的模型若引擎又說缺少，不再重抓，直接標成錯誤（避免無限循環）；使用者重按「開始」時重新計算。
- **失敗**：任一下載失敗（等其他下載結束後）停在錯誤說明與「重試」（只重抓失敗的）。`download_in_progress` 視為已在下載，等進度事件。
- **翻譯伺服器無法替使用者下載**（只對翻譯模型）：`pull_unsupported` →「翻譯服務 {url} 不是 Ollama…請在該服務上自行安裝 {models}，或到『模型』頁改選已安裝的模型；也可以關閉『顯示翻譯』」；`translation_server_unreachable` →「連不上翻譯服務…請先啟動 Ollama（開啟 Ollama 應用程式，或在終端機執行 ollama serve），再按『重試』」；`translation_server_auth_failed`（伺服器回 401／403）→「翻譯伺服器拒絕連線：請檢查 API 金鑰」。三者都可重試。
- **取消**：只是不再等待、不自動開始；已送出的下載在引擎中繼續。引擎重啟時面板自動取消。

### 只顯示原文

- 字幕紀錄頁工具列與設定頁「字幕」區塊各一個「顯示翻譯」開關，都綁設定 `translation.enabled`，改變即套用（`lib/translation-toggle.ts`）：session 載入中或執行中送 `session.set_translation {enabled}`（引擎存檔並立即切換 pipeline），否則 `settings.set {"translation": {"enabled": …}}`。之後送 `overlay_set_show_translation {enabled}`（以引擎回應的設定為準）。引擎失敗時開關回到原值並說明；只有字幕視窗失敗時設定仍已儲存，另外提示。
- 切換後重畫字幕紀錄、模式說明與語言預覽（預覽會說明不需要翻譯模型）。引擎就緒讀到設定時也把值推給字幕視窗。

### 訊息

內容區上方的訊息列（最多三則、可關閉）：操作失敗的原因與下一步、引擎 `log` 事件的 warning／error、`status: error` 的 `code` 對應說明（例如 `model_not_downloaded` →「模型還沒下載。請到『模型』頁下載後再開始。」，`translation_model_missing`、`translation_server_auth_failed` 也有說明）、引擎錯誤碼的說明（含 `license_not_accepted`、`gpu_runtime_unsupported`、`download_in_progress`、`pull_unsupported`、`translation_server_unreachable`、`translation_server_auth_failed`）、下載完成或失敗（開始前下載面板裡的下載除外）、GPU 加速元件安裝後的重啟、更新提示。

## 介面多語系

規格見 `languages-i18n.md`「介面多語系」。不用 i18n 函式庫，全部在 `app/src/lib/i18n.ts` 與 `app/src/locales/`。

### 字典

- `app/src/locales/<code>.ts`，一個語系一個檔案；`zh-TW.ts` 是基準，`locales/types.ts` 由它導出 `MessageKey`、`Messages`。其他語系寫成 `export const xx: Messages = {…}`，**少一個鍵或多一個鍵都是型別錯誤**（`npm run build` 失敗）。`locales/index.ts` 列出 13 個語系（`UI_LOCALES`）、各自語言的名稱（`NATIVE_NAMES`，給選單用）與已有的字典（`DICTIONARIES`；目前 zh-TW、en，其他語系之後各加一個檔案並登記）。
- 鍵是扁平的點號名稱（`status.idle`、`gpu.consentNotice`）。佔位符 `{name}`；每個語系的佔位符必須與基準相同。
- 複數：值寫成物件，鍵是 `Intl.PluralRules` 的類別（`{one, other}`；中文只有 `other`），用 `tp(key, count, params)` 取用，`{count}` 自動帶入。每個語系要提供自己語言的全部類別（例如 ru 要 one/few/many/other），不能多給不會被選到的類別。
- 不放在字典的：語言名稱（`languageName`，`Intl.DisplayNames` type `language`；zh-TW／zh-CN 對到 zh-Hant／zh-Hans 才會是「繁體中文／Traditional Chinese」）、地區名稱（`regionName`，type `region`；`UK` 視為 `GB`）、品牌「聞き取り」與 kikitori、模型與套件名稱、網址、`CPU`／`NVIDIA GPU` 等專有名詞。字幕紀錄的語言標籤：中文與日文介面取語言名稱的第一個字（日／英／中），其他介面用大寫的語言子標籤（JA／EN）。
- 字幕外觀的預覽：原文固定是日文例句，譯文例句在字典裡（`settings.previewTranslation`），跟著介面語系。

### 取用

- `t(key, params)`（一般字串）、`tp(key, count, params)`（複數）。目前語系缺字（尚未有字典的語系、或空字串）時依序退回 English、zh-TW；退回時用**字典所屬語系**的複數規則。都找不到時顯示鍵本身並記錄 error。
- 靜態 HTML 不寫任何介面文字，只有屬性：`data-i18n`（文字；複數鍵另加 `data-i18n-count`）、`data-i18n-title`、`data-i18n-placeholder`、`data-i18n-aria-label`、`data-i18n-language`（文字＝該語言代碼的名稱）。`applyStatic(root)` 依目前語系填入；未知的鍵記錄 error、保留原內容。
- `setLocale(locale)` 設定 `<html lang>`（zh-TW → `zh-Hant-TW`、zh-CN → `zh-Hans-CN`，其餘照語系代碼，`tokens.css` 的 `:lang()` 依此選字型）並呼叫所有 `onLocaleChange` 監聽者。主視窗的監聽者 `onLocaleChanged` 重跑 `applyStatic` 與所有 render 函式（狀態、裝置、字幕紀錄的標籤、模型頁、組合檢查、設定頁、關於），所以每個 render 函式都要能從 state 重畫。**已顯示的訊息列不重寫**（它們是當下的事件）。

### 語系的決定

- `resolveLocale(navigator.languages)`：依偏好順序找第一個對得到的。中文：有 `Hant` 或地區 TW／HK／MO → zh-TW，其他（含單純 `zh`、`zh-Hans`、`zh-SG`）→ zh-CN；`pt*` → pt-BR；其他只比對主要語言子標籤；都對不到 → en。
- 設定 `ui.locale`：`"system"`（預設、欄位不存在時）或 13 個語系代碼之一。讀到不認得但格式像語系的字串（較新版本存的）視為 `"system"` 且不提示；型別錯誤才列為壞欄位。儲存只送 `{"ui": {"locale": …}}`，引擎對 `ui` 深度合併，`dismissed_recommendations` 不受影響。
- 啟動：引擎要幾秒才回應 `settings.get`，所以主視窗先用 `localStorage`（`kikitori.uiLocale`，只是快取）的選擇，沒有就跟隨系統，**在第一次 render 之前**套用；讀到設定檔後以設定檔為準。使用者在引擎未就緒時改了語言：先套用並快取，引擎就緒後再寫入設定檔（不被舊值蓋回）。

### 字幕視窗

- 主視窗每次切換語系都送 `overlay_set_locale {locale}`（具體的語系，不是 `"system"`）；Rust 存在 `OverlayState`，放進快照並廣播 `overlay://state`。字幕視窗的語系＝快照的 `locale`；`null`（主視窗還沒送）或不認得的值就跟隨系統。只在語系真的改變時重新填字、`document.title` 與 `setTitle`（視窗標題，需要 `core:window:allow-set-title`）。

### 引擎傳來的文字

- `lib/messages.ts`：推薦的 `reasons`／`warnings` 與 `models.validate` 的 `errors`／`warnings` 可能是字串（舊引擎，原樣顯示）或 `{code, params, message}`。`formatEngineMessage` 查字典的 `engine.<code>`（帶入 `params`），沒有這個鍵就顯示引擎附的英文 `message`。驗證：`code` 只能是 `[a-z0-9_.]{1,100}`、文字最長 2000 字元、`params` 只保留字串與有限數字，格式不對的項目丟掉不影響其他項目。
- 第二階段起 `hardware.md`「訊息碼」的每個 code 都有 `engine.<code>`（zh-TW、en），佔位符與引擎的參數完全相同（不含格式規格，例如英文範本的 `{ram_gb:.1f}` 在字典是 `{ram_gb}`）。參數先在地化再代入（`localizeParam`）：`language`／`source_language`／`target_language` → 語言名稱（`auto` →「自動偵測」）；`path` 的 `fast_asr`／`final_asr` →「即時字幕的辨識」／「完整翻譯的辨識」；`field` 的 `translation_fast`／`translation_final` → 對應的翻譯模型名稱；`regions`（逗號分隔）→ 地區名稱；`*_gb` → 一位小數；模型、顯示卡名稱與 MiB 整數原樣。
- 清單來源：`app/dev/extract_message_codes.py` 以 `ast` 讀 `src/kikitori/hardware/messages.py` 的 `CATALOG`（不執行專案程式、只需標準庫），寫到 `app/src/lib/testdata/engine-message-codes.json`；`locales/engine-codes.test.ts` 檢查每個已登記的字典都有每個 code、佔位符與參數相同。引擎新增 code 時重跑腳本（`npm run menu` 的工具項目），測試會列出缺的鍵。
- `status`／指令錯誤的 `code`（`model_not_downloaded`、`license_not_accepted` 等）一直是 UI 自己對應，字典鍵是 `status.code.*`、`error.code.*`。

### 檢查

- 型別：每個語系的鍵與基準完全相同、複數鍵仍是複數。
- `locales/locales.test.ts`：每個已登記的字典鍵與基準相同、沒有空字串、每個鍵（含每個複數形式）的佔位符與基準相同、複數類別正好是該語系 `Intl.PluralRules` 會選到的類別；`index.html`／`overlay.html` 用到的鍵都存在。
- `locales/engine-codes.test.ts`：引擎訊息碼的字典鍵與參數（見上）。
- `lib/i18n-source.test.ts`：`app/src/**/*.ts`（排除 `src/locales/`、`*.test.ts`、`testdata/`）的字串、樣板與正規表示式字面值，以及兩個 HTML（排除註解）不得出現漢字、平假名、片假名、諺文，只允許品牌「聞き取り」。以 TypeScript 解析器區分程式與註解，所以註解仍可以寫繁體中文；`\u` 跳脫也會被抓到。
- `lib/i18n.test.ts`：`resolveLocale` 的對應表、`t` 參數代入與退回、`tp` 的複數（含退回 English 時用 English 規則）、監聽者、`Intl.DisplayNames` 的語言／地區名稱、語言標籤、`applyStatic`（以假 DOM 測各屬性與未知鍵）。`lib/messages.test.ts`：字串原樣、已知 code 翻譯、未知 code 用 `message`、驗證規則。

## 字幕視窗 `overlay`

- 設定：透明、無邊框、無陰影、置頂、不顯示在工作列、可調整大小、啟動時隱藏、顯示時不搶焦點、macOS 所有桌面空間可見（`tauri.conf.json`）。
- 內容：每句兩行，原文（字級 × 0.62，近白色）在上，譯文（設定的字級，字幕黃、粗體）在下。每行有可調不透明度的深色底板（0 = 無底板），文字另有八方向描邊與柔和陰影，無底板時在亮畫面也看得清楚。
- 即時字幕：原文與譯文都降低不透明度，譯文維持白色；完整翻譯到達時原地取代並轉成字幕黃。較舊的句子略淡，最新一句最醒目。
- **只顯示原文**（快照的 `show_translation=false`）：每句只有原文一行，原文改用譯文的字級、粗細與字幕黃（即時字幕時為白色），不留空白的第二行（`lineDisplay` 決定、`.line.is-original-only`）。
- 原文與譯文的 `<span>` 都有 `lang` 與 `dir="auto"`：阿拉伯語由右到左；無空白的語言（ja、zh、th）靠 `overflow-wrap` 換行，泰文用 `break-word`（由瀏覽器找字詞邊界）。
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
| `overlay.rs` | 字幕外觀（`deny_unknown_fields` 與範圍檢查）、穿透與可見狀態、介面語系（`locale`，`validate_locale` 只檢查格式：2–3 個字母，可接一個 2–8 字元的子標籤，最長 16 字元）、`show_translation`（「顯示翻譯」，初始 `true`）。`show_translation` 刻意不放進 `OverlayStyle`：它不是外觀、引擎存在 `translation.enabled` 而不是不透明的 `subtitle` 物件；放進 style 會讓 `settings.subtitle` 多一個與 `translation.enabled` 重複的欄位，也得放寬 `deny_unknown_fields`。改由獨立指令 `overlay_set_show_translation {enabled}` 設定並放進快照。 |
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
| `overlay://state` | `{style, click_through, visible, shortcut, shortcut_registered, locale, show_translation}`（`locale` 在主視窗送出前是 `null`；`show_translation` 在主視窗送出前是 `true`，舊版 Rust 沒有這個欄位時前端也當作 `true`） |

前端（`src/lib/validate.ts`）再驗證一次所有事件與指令結果；不合格的 payload 記錄後丟棄。指令錯誤統一為 `{kind: engine|timeout|disconnected|invalid_input, …}`，由 `describeError` 轉成介面語言的說明。

### 權限（最小權限）

Milestone 12–14 新增的指令：`languages_list`、`settings_preview_languages {languages: {mode?, source_language?, target_language?, partner_language?}}`（`deny_unknown_fields`；語言碼只檢查格式：1–16 個英數、`-`、`_`，清單由引擎決定；背景期限）、`models_pull_translation {model}`（名稱規則同引擎：1–200 字、英數與 `._:/-`、英數開頭、不含 `..`；背景期限）、`session_set_translation {enabled}`（一般期限）、`overlay_set_show_translation {enabled}`。`protocol.rs` 另外轉發 `status.data`（物件，序列化後最多 10 000 bytes，沒有時不出現）與 `download_progress.kind`（小寫字母與底線、最多 32 字元；不認得的值照樣轉發，由前端當作沒有 kind）。

`build.rs` 以 `AppManifest::commands` 為每個指令產生 `allow-*` 權限，未列在 capability 的指令一律拒絕。

| 視窗 | 權限 |
|---|---|
| `main`（`capabilities/main.json`） | 事件 listen／unlisten、讀版本號、Milestone 12–14 的五個指令、`update_check`／`update_install`（不給 `updater:default`：外掛自己的 JavaScript 指令會繞過關閉引擎的步驟）、`process:allow-restart`、`opener:allow-open-url`（scope 只有 `https://docs.nvidia.com/cuda/eula/index.html`）、全部引擎與字幕視窗指令（含 `overlay_set_locale`）、`app_about`、`open_third_party_licenses`（`engine_shutdown` 除外：只在程式結束時由 Rust 呼叫） |
| `overlay`（`capabilities/overlay.json`） | 事件 listen／unlisten、`start-dragging`、`start-resize-dragging`、`set-title`（依介面語系改視窗標題）、`overlay_get_state`、`overlay_set_visible` |

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
6. 假引擎，翻譯服務不是 Ollama（`--translation-server openai`：看「無法替你下載翻譯模型」的說明）
7. 假引擎，翻譯服務沒有開（`--translation-server offline`：看「請先啟動 Ollama」的說明）
8. 正式引擎（實際擷取聲音、載入模型）
9. 建置除錯版執行檔（`tauri build --debug --no-bundle`）
10. 工具：更新引擎訊息碼清單（`extract_message_codes.py`）
11. 工具：重新擷取引擎測試資料（`capture_engine_fake.py`：以正式引擎假模式（nvidia、假 Ollama、暫存設定目錄）依序送 `settings.get`、`hardware.detect`、`models.list`、預覽／套用推薦、`languages.list`、兩次 `settings.preview_languages`、`models.validate`、改用未安裝的翻譯模型後 `session.start`（收集到 `translation_model_missing` 的 status）、`models.pull_translation`（收集進度事件）、`session.set_translation`，暫存目錄路徑換成 `C:/fake`，寫入 `src/lib/testdata/engine-fake-nvidia.json`）

正式引擎的假模式把 GPU 加速元件裝在行程專屬的暫存目錄，所以安裝後 UI 自動重啟引擎時，新的引擎又回到「未安裝」——這是假模式的限制，真的引擎裝在使用者資料目錄、重啟後仍在。`slow-runtime` 情境（下載很慢，用來看取消）可用環境變數自行指定。

也可以直接設定環境變數：

```text
KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
npm run tauri dev
```

`fake_engine.py` 參數：`--garbage-every N`（每 N 個字幕事件後輸出一行壞 JSON）、`--crash-after SECONDS`（以結束碼 3 模擬當機）、`--translation-server {ollama,openai,offline,auth}`（假翻譯伺服器：`ollama` 可下載；`openai` 回 `pull_unsupported` 且開始時不檢查翻譯模型；`offline` 回 `translation_server_unreachable`；`auth` 回 `translation_server_auth_failed`）。Milestone 12–14 的模仿（對照 `src/kikitori/engine/testing.py`）：訊息一律 `{code, params, message}`；`languages.list` 用與 `catalog.py` 相同的 20 種語言與品質等級；推薦依語言（同一種語言 → `translation_status: not_needed`、C 級語言 → `asr_language_quality_low`）；`settings.preview_languages` 在收到請求時合併設定與驗證（錯誤立即回應），在背景回答模型、安裝狀態與下載量；`session.start` 依引擎順序先回報 `translation_model_missing`（`data.models`），再回報 `model_not_downloaded`；`models.pull_translation` 寫出回應後才開始，四段進度（`kind: "translation"`，第一筆 0／0、總量會變大），`fake-broken-pull:1b` 一半時失敗；`session.set_translation` 存檔並切換執行中的 session；ASR 與 GPU 加速元件的進度帶 `kind`。它保留下來是因為正式引擎的假模式沒有故障注入、字幕也會結束；Milestone 6 的方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`gpu-runtime` 授權閘門）只做形狀正確的固定模仿，背景方法延遲 0.3 秒、在單一執行緒依序回應，以重現「回應晚於之後的請求」。推薦與驗證的規則以正式引擎為準。

圖示：`uv run --no-project python app/dev/make_icon.py` 產生 `src-tauri/icons/source/icon.png`（藍色底、白色原文條與字幕黃譯文條，全為本專案原創），再執行 `npx tauri icon src-tauri/icons/source/icon.png`。

## 測試

| 指令（在 `app/` 或 `app/src-tauri/`） | 內容 |
|---|---|
| `npm test` | vitest：介面多語系（見「介面多語系」的「檢查」：字典完整性、佔位符、複數類別、HTML 用到的鍵、原始碼不得有 CJK 文字、語系對應、退回、引擎訊息、每個引擎訊息碼的字典鍵與參數、參數在地化）、語言選單（`languages.test.ts`：正式引擎擷取的 `languages.list`／`settings.preview_languages`、選項文字與自動偵測、篩選、本地檢查、只送目前模式的欄位、預覽是否對應目前選擇、儲存的 patch 與「同時改用推薦的模型」、衝突的對方語言、預覽各狀態的文字）、開始前下載（`start-downloads.test.ts`：全部完成才自動開始、以 kind 區分同名模型、舊引擎沒有 kind、總量變大、失敗後只重試失敗的、等其他下載結束才報失敗、`pull_unsupported`／`translation_server_unreachable`／`translation_server_auth_failed` 停下並說明、`download_in_progress`、不重抓同一次開始已下載的模型、取消、面板文字）、「顯示翻譯」（`translation-toggle.test.ts`：執行中用 `session.set_translation`、否則 `settings.set`、引擎失敗不動字幕視窗、只有字幕視窗失敗時仍保留設定、只顯示原文的行）、`status.data.models` 與 `download_progress.kind` 的驗證、字幕視窗快照的 `show_translation`、`ui.locale` 的讀取與 patch、字幕視窗快照的 `locale`、更新指令的結果與進度事件驗證、字幕狀態規則、事件與結果驗證（`hardware.detect`／`models.list`／`settings.apply_recommendation` 用正式引擎假模式實際輸出的 `src/lib/testdata/engine-fake-nvidia.json`）、設定頁與自訂組合的表單驗證與 patch、schema 2 欄位、`models.validate` 結果、儲存條件、只套用最新的檢查結果、GPU 加速元件安裝後的重啟決定、opener 權限只開放 EULA 網址、`app_about`（兩個授權資料夾路徑，可為 `null`、型別錯誤時拒絕）、格式化、自動推薦（`recommend.test.ts`：第一次執行只套用一次且先等偵測、訊息文字、不是新設定檔或使用者已存組合時不套用、引擎回 `applied: null` 視為略過、提示依 `differs` 與指紋顯示／隱藏、「不要再提醒」清單、缺少模型的提示與進度、下載中不能開始、`api.ts` 呼叫的每個指令都在 `build.rs` 與 `capabilities/main.json`） |
| `npm run build` | `tsc` 型別檢查 + `vite build` |
| `cargo test` | Milestone 12–14：翻譯模型名稱規則、`settings_preview_languages` 只送給定的欄位並拒絕未知欄位與格式錯誤的語言碼、`status.data` 轉發與大小上限、`download_progress.kind` 驗證、字幕視窗的 `show_translation`（初始 `true`、切換、不受外觀變更影響）；更新前關閉引擎（`fake_engine.py`：在 runtime worker 上同步呼叫，`engine.shutdown` 讓引擎自己結束；`--ignore-shutdown` 不回應時在期限後強制結束；沒有引擎時立即返回）、更新指令的逾時範圍與進度事件格式、協定解析與驗證、有上限的逐行讀取、id 配對（亂序回應）、逾時與晚到回應、錯誤回應、壞行略過、輸出關閉時請求失敗、指令選擇、重啟退避、參數驗證；字幕視窗的介面語系（初始為 `None`、格式檢查、拒絕時保留舊值）、正式版資源目錄的引擎路徑與覆寫、`about_info`（兩個授權資料夾各自只在存在時回報；`licenses` 與 `tauri.sidecar.conf.json` 的資源名稱一致；`LicenseFolder` 只接受 `app`／`engine`）、`models.download`／`models.validate` 的參數（授權旗標只給 `gpu-runtime`）；另外的端到端測試以真的子行程執行：`fake_engine.py`（含壞資料注入）、`fake_engine.py`（背景方法的回應晚於之後的請求，仍各自配對）、`fake_engine.py --crash-after`（自動重啟）、正式引擎假模式（`KIKITORI_ENGINE_FAKE=1`、`KIKITORI_CONFIG_DIR` 指到暫存目錄）的基本協定，以及 Milestone 6：`hardware.detect` 與一般請求並行、`models.list`、`models.validate`（候選不存檔、`invalid_settings`）、`settings.apply_recommendation`（安裝前 CPU）、`gpu-runtime` 未同意 → `license_not_accepted`（訊息含 EULA 網址、沒有下載事件）、同意 → 安裝到 `done` → 推薦改為 CUDA；Apple Silicon 情境 → `gpu_runtime_unsupported`。需要 `uv` 在 PATH。 |
| `cargo clippy --all-targets -- -D warnings` | lint |

## 各平台需要實機確認的地方

- **透明視窗**：Windows（WebView2）已確認。macOS 需要 `macOSPrivateApi: true`（已開，代表不能上架 Mac App Store）。Linux（WebKitGTK）需要合成器（compositor）：X11 容器實測（Xvfb、WebKitGTK 2.52）沒有合成器時字幕視窗是黑底不透明、會蓋住後面的視窗；開了 picom（xrender）就真的透明。Wayland 待實機確認。
- **置頂**：macOS 全螢幕播放的影片位於獨立 Space，可能蓋過字幕視窗（已設 `visibleOnAllWorkspaces`，仍需實測）；Wayland 不允許應用程式自行置頂，GNOME 上可能無效。
- **滑鼠穿透**：Windows／macOS 由系統支援；Linux X11 可用，Wayland 依合成器而定。
- **全域快速鍵**：X11 容器實測 Ctrl+Alt+K（xdotool 送出）可切換滑鼠穿透；同一個按鍵已被其他程式（例如另一個 kikitori）佔用時 UI 正確改提示用開關。Wayland 下 `global-hotkey` 可能無法註冊；macOS 可能要求輔助使用權限。
- **無邊框縮放**：右下角把手用 `startResizeDragging`；Linux 部分視窗管理員不支援。
- **字型**：macOS／Linux 會改用蘋方、Hiragino、Noto CJK。沒有任何 CJK 字型的 Linux（例如精簡安裝只有 DejaVu）整個介面會變成豆腐方塊（容器實測），所以 deb 以 Recommends 建議 `fonts-noto-cjk`；AppImage 不帶字型，靠系統安裝。
- **表單控制項**：WebKitGTK 以原生外觀畫 `<select>`、不套用 CSS 背景色；深色底上的選單要設 `color-scheme: dark`（側欄的聲音來源選單就是這樣修的）。
