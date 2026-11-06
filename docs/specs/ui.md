# Spec: ui（Tauri 桌面程式）

> 狀態：Milestone 7 第一階段已實作（2026-11-05）；第二階段（2026-11-05）對齊 Milestone 6 的引擎協定（硬體偵測、推薦、組合驗證、GPU 加速元件與授權）並改用 `bundle.resources` 打包引擎。之後加上自動推薦（第一次執行自動套用、更適合的組合提示、缺少模型的一鍵下載；見「自動推薦」）。Milestone 15 第一階段（2026-11-05）：介面多語系的機制、全部介面文字抽到字典（zh-TW 基準、English），見「介面多語系」。第二階段（2026-11-05）：對齊 Milestone 12–14 的引擎協定——全部引擎訊息碼的字典、任意語言組合的語言選單與推薦預覽、開始時才下載缺少的模型、「顯示翻譯」開關與只顯示原文的字幕（見「語言選單」「開始前下載缺少的模型」「只顯示原文」）。競品比較後的補強（2026-11-06，`competitive-features.md` F1／F5／F6／F8 的 UI 與 Rust 部分）：匯出字幕紀錄與每次自動存檔、字幕視窗的閱讀節奏、OBS 文字檔輸出、翻譯服務連不上時的引導、兩個新的全域快速鍵與「複製診斷資訊」（見「匯出字幕紀錄」「閱讀節奏」「OBS 文字檔」「翻譯服務連不上時」「診斷資訊」）。
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
- **狀態列**：結合橋接狀態（引擎行程）與引擎的 `status` 事件：正在啟動引擎／待命中／正在載入模型／正在聆聽／發生錯誤，已停止／引擎意外結束，正在重新啟動／引擎無法啟動。失敗或停止時顯示原因與「重新啟動引擎」。執行中狀態下方另有小型音量表與延遲（見「延遲、落後與音量」）。

### 字幕紀錄頁

- 每句一列：時間碼（`start_sample / 16000`，`m:ss`）、來源語言單字標籤（日／英／中，滑鼠移上去顯示「日語譯成繁體中文」）、原文（較大）、譯文。原文與譯文都加 `dir="auto"`（阿拉伯語由右到左）。譯文是空字串時以 CSS `attr(data-empty)` 顯示字典裡的「（沒有譯文）」／「翻譯中」（第一階段寫死在 CSS 的中文移到字典）。
- 「顯示翻譯」關閉時只列原文（譯文列不顯示，`lib/subtitles.ts` 的 `lineDisplay`）；已翻譯的句子保留譯文，再打開時重新顯示。
- 即時字幕（`is_final=false`）在左側加字幕黃細條，原文只有**還可能改變的尾巴**是淡色、已穩定的前段（`stable_chars`）照一般亮度，譯文整句淡色（見「穩定的即時字幕」）；同一個 `utterance_id` 的完整翻譯到達時原地取代。最多保留 300 句。使用者停在底部時自動捲動，往上翻時不打擾。
- 每一列 `content-visibility: auto`（最新 3 列除外，跟著捲到底需要它們的實際高度）：畫面外的列不排版、不塑形、不繪製，切換「顯示翻譯」或介面語系時不必重排最多 300 列（v0.2.0 長時間測試中主視窗 renderer 因此每次多留約 80 MiB，見 `experiments/memory_soak/README.md`「CDP soak」）。音量表以 `transform: scaleX()` 動畫（不觸發排版），每秒的狀態列與匯出說明只寫入改變的值。
- 工具：顯示字幕視窗、滑鼠穿透、**顯示翻譯**三個開關、「清除紀錄」與「**匯出**」選單（見「匯出字幕紀錄」）。開關下方逐行說明三個全域快速鍵（滑鼠穿透、切換顯示翻譯、清除字幕）；被其他程式占用的快速鍵改為提示「已被其他程式占用，無法使用」，其餘照常（不擋其他功能）。
- 「清除紀錄」同時清掉匯出用的紀錄：清掉的內容之後不會被匯出。
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
- **語音辨識模型**：`models.list.asr` 表格——模型（使用中、`recommended_for` 的「建議：即時／完整」、`no_speech_reliable=false` 時「不判斷非人聲」並以 title 說明）、大小、速度等級、記憶體（CPU `cpu_ram_mb`／GPU `vram_mb`，未知時「未知」）、授權、狀態（下載按鈕、進度、失敗原因與「重新下載」；已下載的模型顯示磁碟用量 `disk_bytes` 與「刪除」，見「刪除模型」）。
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
| 翻譯服務 | 服務網址、API 金鑰的**環境變數名稱**；連不上時的引導（見「翻譯服務連不上時」） | `translation.{base_url, api_key_env}` |
| 翻譯模型的授權地區 | 勾選框「我確認我不在歐盟、英國或南韓使用 kikitori，並同意遵守這些翻譯模型的授權條款。」（預設不勾） | `translation.allow_restricted` |
| 介面語言（頁面最上方、在設定表單之外） | 「跟隨系統」與 13 個語系（以各自語言的名稱列出），選擇後立即套用並儲存 | `ui.locale`（`settings.set {"ui": {"locale": …}}`） |
| 語言（獨立表單 `languages-form`，自己的「儲存語言」） | 影片翻譯：收聽語言（含「自動偵測」）＋字幕語言；即時口譯：我的語言＋對方的語言。下方是推薦組合預覽與「同時改用推薦的模型」（預設勾選）。見「語言選單」 | `source_language`、`target_language`、`partner_language`（＋勾選時推薦的 `asr`／`translation` 模型） |
| 字幕 | 「顯示翻譯」開關（改變即套用，不經「儲存設定」，見「只顯示原文」）；譯文字級（14–96 px）、背景不透明度（0–100%）、字幕位置（貼齊上緣／下緣）、同時顯示的句數（1–3），附即時預覽；每句至少顯示（0–10 秒，可有小數，預設 1.5）、沒有新字幕時淡出（0–600 秒，0 = 不淡出，預設 8），見「閱讀節奏」 | `translation.enabled`；`subtitle.{font_size, background_opacity, position, max_lines, min_display_ms, idle_clear_secs}` |
| 術語表（獨立表單 `glossary-form`，自己的「儲存術語表」） | 「使用內建的直播用語表」開關；自訂詞條表格（原文、譯文、字幕語言或「全部語言」、刪除）、新增、匯入／匯出 CSV、還原。見「術語表」 | `translation.glossary.{builtin, entries}` |
| 字幕紀錄與 OBS | 「每次結束時自動存檔（SRT 與 TXT）」（預設關）與存放位置、「開啟字幕紀錄資料夾」；「輸出字幕到文字檔（給 OBS）」：不輸出（預設）／原文／譯文／原文與譯文，下方顯示檔案路徑（寫入失敗時改為錯誤原因）與「開啟 OBS 文字檔資料夾」 | `ui.auto_save_transcripts`；`subtitle.obs_output` |
| 關於 | 程式版本、引擎版本與協定版本（假引擎會註明）、授權（`kikitori 以 MIT 授權發布`）、第三方元件聲明、第三方授權資料夾、「檢查更新」、「複製診斷資訊」（見「診斷資訊」） | — |

- 翻譯模型改在模型頁的「自訂組合」選擇（第一階段在設定頁），讓同一份表單涵蓋整個組合、一起經過 `models.validate`（授權、是否在伺服器上、記憶體）。
- 授權地區說明：部分翻譯模型（例如騰訊 HY-MT）的授權不適用於歐盟、英國與南韓；kikitori 無法得知使用者所在地，所以預設不推薦。2026-11-06 起建議組合一律只用沒有地區限制的 Qwen3.5（`translate.md`「推薦」），勾選與否都不改變建議組合；勾選後，在自訂組合手動選用這些模型時檢查不再顯示授權地區的警告（`settings.regionHelp`、儲存後的 `settings.savedRegion` 都這樣說明）。
- **關於**：顯示「本程式內含的 Intel® OpenMP 等第三方元件依其各自授權條款提供；不得對其進行逆向工程。」（Intel EULA for Developer Tools 的再散布條件，見 `packaging-ci.md`）。正式版有兩個授權資料夾，各自一行顯示完整路徑（可選取）與一個按鈕：程式本身 `<資源目錄>/licenses`（kikitori 的 LICENSE、Tauri／Rust crate 與前端 npm 套件的授權全文、MPL-2.0 crate 的原始碼取得方式；`packaging-ci.md`「app 的第三方授權聲明」）→「開啟程式的第三方授權資料夾」；語音引擎 `<資源目錄>/kikitori-engine/THIRD_PARTY_LICENSES` →「開啟引擎的第三方授權資料夾」。只顯示實際存在的資料夾；兩者都不在（開發版）時改為說明正式版的位置。路徑由 Rust 的 `app_about` 指令算出（`app_licenses`、`engine_licenses`，不存在時為 `null`）；開啟由 `open_third_party_licenses {folder: "app" | "engine"}` 完成——前端只能傳這兩個名稱之一（serde enum，其他值在反序列化時就被拒絕），路徑一律在 Rust 端決定，所以不需要給前端 opener 的路徑權限。
- 只存環境變數名稱，金鑰本身不經過 UI、不寫入任何檔案。
- 儲存前先在前端驗證（網址必須 http/https、變數名稱 `^[A-Za-z_][A-Za-z0-9_]*$`、數值範圍），錯誤顯示在欄位下方。引擎回傳 `invalid_settings` 時依訊息中的欄位路徑（例如 `translation.base_url: …`）標到對應欄位。
- 字幕外觀存在設定檔的 `subtitle` 物件（引擎原樣保存、不解讀）；儲存後同時透過 `overlay_set_style` 推給字幕視窗。`subtitle.obs_output` 與外觀放在同一個物件，但**不是** `OverlayStyle` 的欄位（Rust 端 `deny_unknown_fields`）：前端讀設定時把它拆成 `Settings.obs_output`，儲存時合併回 `subtitle`，改變時另外送 `obs_set_output`。引擎就緒讀到設定時也送一次。
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
- **進度**：`download_progress` 以 `kind`＋`model` 對應（ASR 與翻譯模型可能同名；沒有 `kind` 的舊引擎事件只對應 ASR）。翻譯模型的總量會在下載中變大（Ollama 逐層回報），第一筆是 0／0（顯示「準備下載」）。面板中的下載不再另外跳「下載完成／失敗」訊息；上方「缺少的模型」訊息在面板下載它們時收起，進度只在面板顯示一次；取消後（引擎可能仍在下載）訊息回來，由面板下載完成則不再顯示「已下載完成」（`missingNoticeMode`）。
- **全部完成**：自動再送一次 `session.start`（與按「開始」相同的模式與裝置），失敗照一般「無法開始」回報。同一次開始中已下載過的模型若引擎又說缺少，不再重抓，直接標成錯誤（避免無限循環）；使用者重按「開始」時重新計算。
- **失敗**：任一下載失敗（等其他下載結束後）停在錯誤說明與「重試」（只重抓失敗的）。`download_in_progress` 視為已在下載，等進度事件。
- **翻譯伺服器無法替使用者下載**（只對翻譯模型）：`pull_unsupported` →「翻譯服務 {url} 不是 Ollama…請在該服務上自行安裝 {models}，或到『模型』頁改選已安裝的模型；也可以關閉『顯示翻譯』」；`translation_server_unreachable` →「連不上翻譯服務…請先啟動 Ollama（開啟 Ollama 應用程式，或在終端機執行 ollama serve），再按『重試』」；`translation_server_auth_failed`（伺服器回 401／403）→「翻譯伺服器拒絕連線：請檢查 API 金鑰」。三者都可重試。
- **取消**：只是不再等待、不自動開始；已送出的下載在引擎中繼續。引擎重啟時面板自動取消。

### 只顯示原文

- 字幕紀錄頁工具列與設定頁「字幕」區塊各一個「顯示翻譯」開關，都綁設定 `translation.enabled`，改變即套用（`lib/translation-toggle.ts`）：session 載入中或執行中送 `session.set_translation {enabled}`（引擎存檔並立即切換 pipeline），否則 `settings.set {"translation": {"enabled": …}}`。之後送 `overlay_set_show_translation {enabled}`（以引擎回應的設定為準）。引擎失敗時開關回到原值並說明；只有字幕視窗失敗時設定仍已儲存，另外提示。
- 切換後重畫字幕紀錄、模式說明與語言預覽（預覽會說明不需要翻譯模型）。引擎就緒讀到設定時也把值推給字幕視窗。

### 訊息

內容區上方的訊息列（最多三則、可關閉）：操作失敗的原因與下一步、引擎 `log` 事件的 warning／error、`status: error` 的 `code` 對應說明（例如 `model_not_downloaded` →「模型還沒下載。請到『模型』頁下載後再開始。」，`translation_model_missing`、`translation_server_auth_failed` 也有說明）、引擎錯誤碼的說明（含 `license_not_accepted`、`gpu_runtime_unsupported`、`download_in_progress`、`pull_unsupported`、`translation_server_unreachable`、`translation_server_auth_failed`）、下載完成或失敗（開始前下載面板裡的下載除外）、GPU 加速元件安裝後的重啟、更新提示。

- 翻譯服務連不上的引導（見「翻譯服務連不上時」）出現時，引擎關於翻譯服務失敗的英文 `log`（`keep-warm request to the translation server failed: …`、`translation failed: …`、`cannot warm up translation model …`，`isTranslationServerLog`）不再顯示成「引擎提醒」——引導已用介面語言說明同一件事；原文仍留在診斷資訊的最近訊息。引導晚一步出現時會收起先前那則。
- 訊息列固定在內容區上方；它的高度設成內容區的 `scroll-padding-top`（`--notices-h`），聚焦或捲動到的欄位不會停在訊息列底下（設定頁表單的第一列）。
- 純確認、沒有按鈕要選的訊息 8 秒後自己收起（`autoDismiss`，`lib/auto-dismiss.ts`）：模型下載完成、模型刪除、振假名辭典下載後已打開、辭典刪除。滑鼠停在訊息上或焦點在訊息裡時不收；移開（`pointerleave`、焦點移出）後重新等完整的 8 秒。錯誤、需要決定的訊息、有按鈕的訊息（例如「打開資料夾」）、更新提示都留到使用者關閉——訊息列固定在捲動區上方，確認類訊息一直留著會蓋住每一頁最上面一列。

### 匯出字幕紀錄

規格見 `competitive-features.md` F1。格式在 `app/src/lib/export.ts`（純函式、可測），Rust 只驗證檔名並寫檔。

- **內容**：主視窗另外保留整個 session 的字幕（`EXPORT_MAX_LINES` = 20 000 句，套用同一套字幕狀態規則），所以匯出不受畫面上 300 句的限制。只匯出完整字幕（`is_final`）：即時字幕還在變動。
- **選單**（字幕紀錄頁工具列的「匯出」）：內容（原文與譯文／只有原文／只有譯文）＋ SRT、VTT、TXT、CSV 四個按鈕；沒有完整句子時按鈕停用並說明。存好後的訊息附「開啟資料夾」。
- **格式**：時間碼＝`start_sample`／`end_sample` ÷ 16 000（從 session 開始算）；結束不晚於開始時給 1 秒。雙語時每個 cue 兩行（原文、譯文）；沒有譯文（還沒翻、或「顯示翻譯」關閉）時以原文代替，「只有譯文」也一樣。換行與控制字元收成一個空白（一個 cue 不會被拆開）。
  - SRT：編號、`HH:MM:SS,mmm --> …`、cue 之間空一行、CRLF；文字裡的 `-->` 改成 `->`（SRT 沒有跳脫方式）。
  - VTT：`WEBVTT` 標頭、`HH:MM:SS.mmm`、cue 編號；`&`、`<`、`>` 轉成實體（也就不會出現 `-->`）。
  - TXT：`[HH:MM:SS] 原文`，下一行譯文，句與句之間空一行，CRLF。
  - CSV：UTF-8 BOM（Excel 才不會亂碼）、標頭 `index,start,end,source_language,target_language,original,translation`（依內容省略欄位）、CRLF；RFC 4180 引號；以 `=`、`+`、`@`、Tab、CR 或「`-` 後面不是空白」開頭的欄位前面加 `'`（避免試算表當成公式執行；字幕來自任何人都能說的聲音），「- 對白」保持原樣。
  - 由右到左的文字（第一個字母是希伯來／阿拉伯等文字）在 SRT、VTT、TXT 前後加 RLM（U+200F），由左到右排版的播放器才不會把句尾標點放錯邊；CSV 不加（資料原樣）。
- **檔名**：`YYYY-MM-DD_HHMMSS_<模式>.<副檔名>`（session 開始的本地時間；沒有 session 時用現在時間）。同一個 session 再匯出同一種格式會覆寫。
- **每次自動存檔**（`ui.auto_save_transcripts`，預設關）：session 由執行中變成非執行中（按「停止」、引擎回報 `idle`／`error`、引擎重啟）時，存 SRT 與 TXT（原文與譯文）各一份，每個 session 只存一次；結束的判斷集中在 `setSessionState`。最後一句的完整字幕可能在「停止」回應之後才送到，所以按「停止」造成的結束要**等引擎回報 `idle`（或 `error`）或 2 秒，取先到者**再存（`lib/session-end.ts` 的 `SessionEndSave`，計時器注入、可測）；引擎自己回報結束或引擎已經不在時立即存；等待中又按「開始」時先存上一個 session。兩個檔案用同一份快照，新 session 開始也不會混進去。
- **Rust**（`output.rs`）：`transcript_save {fileName, contents}` 寫到 `<文件>/kikitori/transcripts/`（`PathResolver::document_dir()`，不新增 dialog／fs 外掛），回傳完整路徑。檔名規則：`<主檔名>.<srt|vtt|txt|csv>`，主檔名只能是 ASCII 英數、`_`、`-` 且以英數開頭，全長 ≤ 100，Windows 保留名稱（CON、NUL、COM1…）拒絕——分隔字元、`..`、磁碟代號、隱藏檔、結尾的點或空白都不可能出現；接上資料夾後再檢查結果正好是資料夾的直接子項目（`contained_path`）。內容 ≤ 32 MiB。先檢查檔名再建立資料夾；寫入是同資料夾的暫存檔＋`sync_all`＋`rename`（原子取代），失敗時刪暫存檔。檔案 I/O 在 `spawn_blocking`。`open_output_folder {folder: "transcripts" | "obs"}`（serde enum，路徑在 Rust 決定，資料夾不存在就先建立）以 opener 的 Rust API 開啟；`output_info` 回傳兩個位置給設定頁（沒有「文件」資料夾時為 `null`）。

### 穩定的即時字幕

規格見 `competitive-features.md` F2。切分在 `app/src/lib/stable.ts`（`splitStable`，純函式、可測）。

- 引擎在即時字幕（`is_final=false`）加 `stable_chars`：原文開頭已穩定的字數（**code point**，Python `len`），完整字幕不帶。前端在 `lib/subtitles.ts` 的 `SubtitleLine.stableChars` 保存，`lineDisplay` 回傳 `originalParts: {stable, unstable}`。
- 規則：完整字幕整句穩定；即時字幕沒有 `stable_chars`（舊引擎、或還沒有穩定的部分）整句視為不穩定，看起來和以前一樣；超過原文長度＝整句；負數視為 0、小數無條件捨去。切點換算成 UTF-16 位置（`𠮷`、emoji 不會被切成一半），而且**不切在字素叢集中間**（e＋結合重音、ZWJ 連起來的 emoji 整個算不穩定，直到整個叢集都被涵蓋）；沒有 `Intl.Segmenter` 的 WebView 退回以 code point 切。
- 繪製：字幕紀錄的原文 `<p dir="auto">` 裡兩個沒有自己 `dir` 的 `<span>`（`.utt-stable`、`.utt-unstable`），由段落的方向排列，所以阿拉伯語等由右到左的文字順序正確；只有 `.utt-unstable` 與譯文淡色。字幕視窗同樣在 `.orig` 裡放 `.stable`／`.unstable`，只淡化不穩定的尾巴。
- Rust 端把超過原文長度的 `stable_chars` 夾到原文的字元數，不合理的 `latency_ms`（超過一天）改成 `null`——輔助欄位出錯不應讓整句字幕被丟掉；型別錯誤（負數、字串）仍然整行拒絕。前端只在即時字幕讀 `stable_chars`、只在完整字幕讀 `latency_ms`。

### 振假名

規格見 `furigana.md`。設定頁「在日文原文上標振假名」開關（立即生效，`session_set_furigana`；第一次打開時先下載辭典並顯示進度，
`lib/furigana.ts`）；字幕紀錄與字幕視窗以 `lib/ruby.ts` 的 `rubyParts()`／`segmentNodes()` 畫 `<ruby>`（DOM 呼叫，不用 innerHTML），
與穩定前綴共存（跨過切點的詞整個淡化）；模型頁「振假名辭典」區塊顯示用量與刪除。匯出字幕不變。

### 術語表

規格見 `competitive-features.md` F3、資料形狀見 `vtuber-profile.md`。邏輯在 `app/src/lib/glossary.ts`（純函式、可測），`main.ts` 只畫表格。

- 設定頁的獨立表單：「使用內建的直播用語表」（`builtin`）、自訂詞條表格（每列：原文、譯文、字幕語言選單——第一項「全部語言」＝`null`，其餘是 `languages.list` 的語言；設定裡有清單沒有的代碼時照樣列出，不悄悄改掉；選單寬度依最長的語言名稱（10–18em），「English（英文）」不被截斷——與「刪除」）、「新增詞條」「匯入 CSV」「匯出 CSV」、「儲存術語表」「還原」。改動只在表格裡，按「儲存術語表」才送 `settings.set {"translation": {"glossary": {builtin, entries}}}`（引擎對物件深度合併、對清單整個取代，所以一律送整份清單）；儲存後執行中的 session 下次開始才生效。未儲存的修改在重新讀取設定時保留。
- **本地檢查**（`validateGlossary`，與引擎的 `_parse_glossary`／`GlossaryEntry` 相同的限制）：最多 500 筆；原文 1–100、譯文 1–200 個字（以 code point 計）；不能是空白、開頭結尾不能有空白、不能有換行、Tab 與其他 Python `isprintable()` 不接受的字元（控制、格式、私用、未指派、U+0020 以外的空白）；字幕語言必須在清單內（清單還沒載入時只檢查格式）；同一個字幕語言下，原文以 NFKC＋大小寫摺疊（`toUpperCase().toLowerCase()` 近似 Python `casefold`，`ς` 摺成 `σ`）＋去頭尾空白後相同就算重複，錯誤標在**後面那一列**並指出是第幾列。剛新增、還沒動過的列不顯示「請填寫」，按「儲存術語表」時才顯示。
- **引擎錯誤**：`invalid_settings` 的訊息路徑 `translation.glossary.entries[3].source` 對到第 4 列的原文欄（`glossaryErrorForEnginePath`；`entries[3]` 本身或不認得的欄位標在該列原文欄，`translation.glossary.entries`／`translation.glossary`／`.builtin` 顯示在表格下方）。之後修改那一列、或新增／刪除／匯入（列的位置變了）時清掉這個錯誤。
- **匯出 CSV**（`glossaryToCsv`）：UTF-8 BOM、標頭 `source,target,target_language`（`null` 寫空白）、CRLF、RFC 4180 引號與匯出字幕相同的公式防護（`csvField`）。寫到字幕紀錄資料夾，檔名 `glossary_YYYY-MM-DD_HHMMSS.csv`，沿用 Rust 的 `transcript_save`（檔名規則與原子寫入相同，不新增寫檔指令）；存好的訊息附「開啟資料夾」。匯出的是表格目前的內容（包含尚未儲存的修改）。
- **匯入 CSV**（`<input type="file">`，不需要外掛；上限 1 MB）：`parseGlossaryCsv` 接受有或沒有 BOM；分隔字元依第一行決定（`,`、`;` 或 Tab：許多語系的 Excel 存成分號）；引號欄位可含分隔字元、`""` 與換行；CRLF／LF／CR。第一行同時有 `source` 與 `target`（不分大小寫）就當標頭、依名稱找欄位（`target_language` 或 `language`，順序不限、多的欄位忽略），否則依序是原文、譯文、字幕語言。欄位去頭尾空白，匯出時加的 `'` 公式防護會拿掉（匯出再匯入完全相同）；字幕語言空白、`*`、`all` ＝全部語言。空行略過；缺原文或譯文的行列出行號、不匯入。匯入的詞條接在表格後面（`mergeGlossary`：與既有詞條完全相同——同一個比對鍵、字幕語言與譯文——的略過；同一詞不同譯文的保留，讓檢查標出衝突由使用者決定），顯示匯入與略過的筆數，之後一樣要按「儲存術語表」。
- 診斷資訊不列出詞條內容，只列筆數（`settingsForReport`）：詞條是使用者自己的資料。

### 延遲、落後與音量

規格見 `competitive-features.md` F4。狀態機在 `app/src/lib/metrics.ts`（時間由呼叫端傳入、可測）。

- 引擎在 session 執行中約每秒送 `metrics {session_id, level_dbfs, backlog, latency_ms_median}`；完整字幕另帶 `latency_ms`（說完 → 完整字幕送出，前端只解析、不另外顯示）。Rust 驗證 `level_dbfs` 在 −120–0、`backlog` ≤ 1 000 000、`latency_ms_median` ≤ 一天，不合格的整則丟棄（下一秒就有新的），只轉發給主視窗（`engine://metrics`）。
- 狀態列（側欄底部）：執行中顯示小型音量表（−60 dBFS 以下為空、0 dBFS 為滿，`role="meter"`，沒有聲音時改成灰色）與「延遲 1.2 秒」（中位數；還沒有完整字幕時「延遲 —」）。session 不在執行中就隱藏並清空。
- **辨識跟不上**：`backlog` > 3 時訊息「辨識跟不上…建議到『模型』頁改用較小的模型」與「前往模型頁」；降到 1 以下才收起（在 2–3 之間保持原狀，避免在門檻附近閃爍）。
- **沒有聲音**：執行中 `level_dbfs` < −60 連續 15 秒時提示檢查聲音來源；任何一次 ≥ −60 重新計時。到期時間另外設計時器檢查（`noAudioDueAt`），引擎不再送 metrics 時也會準時出現。
- 使用者按「關閉」的提示在條件解除前不再出現；新的 session 從頭開始。較舊 session 的 metrics 丟棄。

### 刪除模型

規格見 `competitive-features.md` F7。判斷在 `app/src/lib/model-delete.ts`。

- 已下載的 ASR 模型在狀態欄顯示「已下載」、磁碟用量（`disk_bytes`，舊引擎沒有時不顯示）與「刪除」。按下後在同一格展開確認：「要刪除 {model} 嗎？可釋放 {size}。」（`disk_bytes`，沒有時用下載大小），若目前設定選用它，另說明「下次開始時會重新下載」；「確定刪除」與「取消」（鍵盤焦點先放在取消）。不用 `window.confirm`：各平台 WebView 的原生對話框行為不一。
- 停用：執行中（載入中）的 session 用到它（設定的即時或完整模型）、下載中、正在刪除、引擎未就緒；按鈕的 title 說明原因。引擎才是最後判斷：`model_in_use`（session 或下載正在用）、`model_not_downloaded`、`unknown_model` 都以訊息說明，之後一律重新讀取 `models.list`。翻譯模型（Ollama）只顯示、不代刪；GPU 加速元件也不在這裡刪（Rust 的 `models_delete` 直接拒絕 `gpu-runtime`）。
- 成功：「已刪除 {model}，釋放了 {size}」（`freed_bytes`），重新讀取清單。

### 閱讀節奏

規格見 `competitive-features.md` F5。規則在 `app/src/lib/rhythm.ts`（`stepRhythm`，時間由呼叫端傳入、可測），字幕視窗只負責計時與繪製。

- 字幕視窗的字幕狀態多保留幾句（`max_lines + 8`），畫面上的句子由 `stepRhythm` 決定。
- **每句至少顯示** `min_display_ms`：新的一句要擠掉最舊的一句時，最舊那句必須已顯示這麼久（從出現、或從變成完整翻譯起算），否則新句等到那時（`wakeAt`）。等待的句子超過一個畫面（`max_lines`）就不再等，直接顯示最新的一整個畫面——等待不能累積成延遲。0 = 不等。
- 同一句的即時更新、即時→完整、引擎移除（非語音）都立即反映，不算「擠掉」。比已顯示過的句子更舊、還沒顯示的句子不再顯示。
- **淡出**：畫面上的內容 `idle_clear_secs` 秒沒有任何變化就淡出（CSS 400 ms，`prefers-reduced-motion` 時不做動畫）。淡出的句子之後只有文字改變時（例如完整翻譯晚到）才重新出現；新的句子照常顯示。0 = 不淡出。
- 新 session 與「清除字幕」快速鍵從空白開始（清除時保留 `lastFinalId`，舊句的即時字幕不會回來）。
- 兩個欄位存在 `settings.subtitle`，透過 `overlay_set_style` 送到 Rust（`OverlayStyle.min_display_ms` 0–10 000、`idle_clear_secs` 0–600；缺欄位時用預設值 1500／8，舊的主視窗送來的外觀仍然有效）。

### OBS 文字檔

規格見 `competitive-features.md` F6。在 Rust（`obs.rs`）做，而不是在網頁：引擎的字幕事件本來就經過 Rust，而主視窗與字幕視窗在最小化／隱藏時計時器會被瀏覽器節流，直播時兩個視窗常常都是隱藏的。

- 開啟（`obs_set_output {content: "off" | "original" | "translation" | "both"}`）後，把字幕視窗目前的句子寫進 `<文件>/kikitori/obs/subtitle.txt`（UTF-8、無 BOM）：每句一段，「原文與譯文」時譯文在原文下一行；沒有譯文或「顯示翻譯」關閉時以原文代替；句內換行收成空白。
- 句子的規則與字幕視窗相同（`ObsLines` 照 `lib/subtitles.ts` 實作：同一句原地取代、完整翻譯不被晚到的即時字幕蓋掉、空白的完整字幕移除該句、只留最新 `max_lines` 句、較舊的 session 丟棄），`max_lines`、`idle_clear_secs` 與「顯示翻譯」跟著 `overlay_set_style`／`overlay_set_show_translation` 更新。閱讀節奏的「每句至少顯示」不套用（OBS 每秒讀一次檔案，寫最新內容即可）。
- 寫入在專用執行緒：有變化才寫、合併連續的變化、**每秒最多 10 次**（`MIN_WRITE_INTERVAL` 100 ms），暫存檔＋rename 原子取代（OBS 不會讀到寫一半的檔案）。
- **清空**：引擎回報 `idle`／`error`（session 結束或失敗）、引擎不在就緒狀態（重啟、停止）、「清除字幕」快速鍵、`idle_clear_secs` 沒有變化、切換成「不輸出」、程式結束。
- 使用者開啟之前完全不碰磁碟（不建立資料夾或檔案）。找不到「文件」資料夾或寫入失敗時，`ObsStatus.error` 說明原因，設定頁顯示在路徑的位置；失敗只記一次 log，下一次變化再重試。
- 不開任何網路埠（OBS 瀏覽器來源需要本機埠，列在「先不做」）。

### 翻譯服務連不上時

規格見 `competitive-features.md` F8。判斷在 `app/src/lib/translation-guide.ts`。

- 依據：最近一次 `models.list.translation.server`（引擎就緒時主視窗在背景讀一次，失敗只記 log；模型頁與「重新偵測」也會更新）或 `hardware.detect` 的 `translation_server` 的 `reachable=false`，或開始前下載停在 `translation_server_unreachable`。「顯示翻譯」關閉時不顯示。
- 設定頁「翻譯服務」區塊內：「連不上翻譯服務 {url}…」與三個按鈕——「**安裝 Ollama**」（以 opener 開 `https://ollama.com/download`；只在伺服器位址是這台電腦時出現，遠端服務裝在本機沒有用）、「**只顯示原文**」（與「顯示翻譯」開關相同的流程，設 `translation.enabled=false`）、「重新偵測」（重新送 `models.list`）。
- 引導出現時另外在訊息列顯示一次（有「安裝 Ollama」「只顯示原文」），所以按「開始」之前就看得到；恢復連線或關閉翻譯時自動收起。

### 全域快速鍵

| 快速鍵 | 動作 | 處理 |
|---|---|---|
| Ctrl+Alt+K（macOS ⌘+Option+K） | 切換滑鼠穿透 | Rust 直接切換 |
| Ctrl+Alt+J（macOS ⌘+Option+J） | 切換「顯示翻譯」 | Rust 送 `shortcut://action`（`"toggle_translation"`）給主視窗，主視窗用與開關相同的流程（引擎未就緒或切換中時忽略） |
| Ctrl+Alt+U（macOS ⌘+Option+U） | 清除字幕 | Rust 清空 OBS 文字檔並廣播 `overlay://clear`，字幕視窗清空畫面；字幕紀錄不受影響 |

- 都用 `tauri-plugin-global-shortcut` 註冊；任何一個註冊失敗（被其他程式占用）只記 log、在 `OverlaySnapshot.shortcuts` 標 `registered: false`，字幕紀錄頁的說明改為「已被其他程式占用，無法使用」，不影響其他快速鍵。
- 選 J／U 而不是 T／C：GNOME 的 Ctrl+Alt+T 是開終端機、Ctrl+Alt+L 是鎖定畫面；波蘭文等鍵盤的 AltGr（= Ctrl+Alt）加 C／E／L／S／X 會打出字母。

### 診斷資訊

規格見 `competitive-features.md` F8。內容在 `app/src/lib/diagnostics.ts`（純函式、可測）。

- 「關於」的「**複製診斷資訊**」：程式版本、介面語系、user agent、橋接狀態與引擎版本（協定、平台、是否假引擎）、session 狀態、硬體偵測摘要（OS、CPU、記憶體、每張顯示卡、cuBLAS、翻譯服務與錯誤；還沒偵測時註明——進入設定頁時若沒有偵測結果會在背景送一次 `hardware.detect`）、設定檔路徑與目前設定（JSON）、最近 20 則引擎訊息（`log` 事件的 info 以上、`status` 錯誤、引擎失敗）。報告用英文：給維護者看，不是介面文字。
- **遮蔽**：設定中名稱像秘密的鍵（key、token、secret、password、credential、auth、cookie…）的值換成 `[redacted]`；`api_key_env` 只保留合法的環境變數**名稱**（不像名稱的值也遮蔽），金鑰本身 UI 從來不讀。所有文字再過一次：網址中的帳密與查詢字串、`Bearer`／`Basic` 權杖、`api_key=…` 之類的鍵值、`sk-…`、`hf_…`、`ghp_…` 等常見金鑰格式、家目錄中的使用者名稱（`C:\Users\<user>`、`/home/<user>`、`/Users/<user>`）。
- 以 `navigator.clipboard.writeText` 複製（不新增外掛；只用這個視窗已有的資料，不等引擎回應，點擊的使用者啟用狀態仍有效）。剪貼簿被拒時顯示唯讀文字框（已全選），請使用者手動複製。

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
- 即時字幕：譯文降低不透明度並維持白色；原文只淡化還可能改變的尾巴（`.unstable`，見「穩定的即時字幕」）；完整翻譯到達時原地取代並轉成字幕黃。較舊的句子略淡，最新一句最醒目。
- **只顯示原文**（快照的 `show_translation=false`）：每句只有原文一行，原文改用譯文的字級、粗細與字幕黃（即時字幕時為白色），不留空白的第二行（`lineDisplay` 決定、`.line.is-original-only`）。
- 原文與譯文的 `<span>` 都有 `lang` 與 `dir="auto"`：阿拉伯語由右到左；無空白的語言（ja、zh、th）靠 `overflow-wrap` 換行，泰文用 `break-word`（由瀏覽器找字詞邊界）。
- 句數：顯示最新 `max_lines` 句（1–3），依閱讀節奏換句與淡出（見「閱讀節奏」）；位置依設定貼齊視窗下緣或上緣。「清除字幕」快速鍵（`overlay://clear`）清空畫面。
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
| `protocol.rs` | 解析與驗證引擎每一行輸出：UTF-8、JSON 物件、`id` 為非負整數或 `null`、`result`／`error` 恰好一個、`result` 必須是物件、事件 `data` 必須是物件；`subtitle`／`status`／`download_progress`／`log`／`metrics` 依型別與範圍驗證（文字 ≤ 10 000 字、語言代碼只允許 BCP 47 字元、`end_sample ≥ start_sample`；`subtitle.stable_chars` 夾到原文字數、不合理的 `latency_ms` 改成 `null`；`metrics` 的範圍見「延遲、落後與音量」）。事件裡不認得的欄位忽略（serde 預設），不認得的事件記錄後丟棄。 |
| `line_reader.rs` | 有上限的逐行讀取（1 MiB，與引擎相同）；超長行丟棄並讀到行尾，串流不會失去同步。 |
| `connection.rs` | 傳送請求（NDJSON）、以 `id` 配對回應、每個請求一個期限（涵蓋寫入與等待）。逾時的請求從等待表移除，晚到的回應記錄後丟棄。格式錯誤的行記錄 error（附截短的內容）後略過，不影響其他訊息。輸出關閉時所有等待中的請求立即失敗。 |
| `engine.rs` | 引擎行程：選擇指令（含正式版資源目錄的路徑）、spawn（不經 shell，Windows 加 `CREATE_NO_WINDOW`）、stderr 轉到 app 日誌、`engine.info` 握手並檢查 `protocol_version == 1`、意外結束時自動重啟（1 s、2 s、4 s 退避；60 秒內超過 3 次就停在「失敗」等使用者按重新啟動）、結束程式時先送 `engine.shutdown`，3 秒內（請求與等待共用同一個期限，`shutdown_within`）沒結束就強制結束；同步版 `shutdown_blocking` 給更新器的 `on_before_exit` 用（見「自動更新」）。 |
| `commands.rs` | 每個協定方法一個 Tauri 指令，先驗證參數（模型 id 字元集與長度、裝置 id 長度、設定必須是物件且 ≤ 64 KiB、模式只能 `media`／`interpret`；`accept_license` 只能用在 `gpu-runtime`，且只有前端明確傳 `true` 才送出），再依方法給不同期限：一般 10 s、背景方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`settings.preview_recommendation`）45 s（引擎以單一工作執行緒依序處理，要算進排在前面的請求）、`models.delete`（引擎同步刪除，但多 GB 的資料夾在慢的磁碟上仍可能要幾秒）45 s、`session.start` 30 s、`session.stop` 15 s、握手 120 s（第一次 `uv run` 可能要建立虛擬環境）。另有 `engine_restart`、`bridge_status`、`app_about`、`open_third_party_licenses`、`output_info`、`transcript_save`、`open_output_folder`、`obs_set_output` 與字幕視窗指令（`overlay_set_style`／`overlay_set_show_translation` 也更新 `ObsOutput`）。 |
| `output.rs` | 使用者看得到的輸出檔：`<文件>/kikitori/{transcripts,obs}` 的位置、字幕紀錄檔名驗證與資料夾內的路徑檢查（`validate_transcript_name`、`contained_path`）、原子寫入（`write_atomic`）、`OutputFolder`（`transcripts`／`obs`）。見「匯出字幕紀錄」。 |
| `obs.rs` | OBS 文字檔：與字幕視窗相同規則的 `ObsLines`、專用寫入執行緒（節流、合併、閒置清空、結束時清空）、`ObsStatus`。見「OBS 文字檔」。 |
| `overlay.rs` | 字幕外觀（`deny_unknown_fields` 與範圍檢查；含閱讀節奏的 `min_display_ms`、`idle_clear_secs`，缺少時用預設值）、三個全域快速鍵（`ShortcutAction`、各自是否註冊成功）、穿透與可見狀態、介面語系（`locale`，`validate_locale` 只檢查格式：2–3 個字母，可接一個 2–8 字元的子標籤，最長 16 字元）、`show_translation`（「顯示翻譯」，初始 `true`）。`show_translation` 刻意不放進 `OverlayStyle`：它不是外觀、引擎存在 `translation.enabled` 而不是不透明的 `subtitle` 物件；放進 style 會讓 `settings.subtitle` 多一個與 `translation.enabled` 重複的欄位，也得放寬 `deny_unknown_fields`。改由獨立指令 `overlay_set_show_translation {enabled}` 設定並放進快照。 |
| `updater.rs` | 檢查與安裝更新的指令 `update_check`／`update_install`（tauri-plugin-updater 的 Rust API），安裝前關閉引擎（見「自動更新」）。 |
| `lib.rs` | 外掛（log、window-state、updater、process、opener、global-shortcut）、狀態（含 `ObsOutput`）、事件轉發（字幕事件同時交給 `ObsOutput`；`idle`／`error` 狀態與引擎不在就緒時清空 OBS 文字檔）、三個全域快速鍵（`run_shortcut`）、生命週期（關閉主視窗即結束程式；結束時清空 OBS 文字檔）。 |

### 引擎指令的選擇

1. 環境變數 `KIKITORI_ENGINE_CMD`：JSON 字串陣列（路徑有空白時用），或以空白分隔的指令。
2. 除錯版（`debug_assertions`）：在 repo 根目錄執行 `uv run kikitori engine`。
3. 正式版：`<資源目錄>/kikitori-engine/kikitori-engine[.exe]`（PyInstaller one-folder，以 `bundle.resources` 隨安裝檔散布；資源目錄為 Tauri `PathResolver::resource_dir()`：Windows 是安裝目錄、macOS 是 `Contents/Resources`、Linux deb／AppImage 是 `/usr/lib/<productName>/`），工作目錄是該資料夾。`KIKITORI_ENGINE_CMD`、`KIKITORI_ENGINE_CWD` 在正式版一樣可以覆寫。

`KIKITORI_ENGINE_CWD`（絕對路徑）可覆寫工作目錄。子行程繼承程式的環境變數，所以 `KIKITORI_ENGINE_FAKE`、`KIKITORI_CONFIG_DIR` 會直接傳給引擎。

### 前端收到的事件

| Tauri 事件 | 內容 |
|---|---|
| `engine://subtitle`、`engine://status`、`engine://download-progress`、`engine://log` | 已驗證的引擎事件（欄位同協定；`subtitle` 的 `stable_chars`／`latency_ms` 只在引擎有送時出現） |
| `engine://metrics` | 只送給主視窗：`{session_id, level_dbfs, backlog, latency_ms_median}`（約每秒一次） |
| `bridge://state` | `{phase: starting|ready|restarting|stopped|failed, detail, restarts, command, source, info}` |
| `shortcut://action` | 只送給主視窗：`"toggle_translation"`（全域快速鍵） |
| `overlay://clear` | 「清除字幕」快速鍵；字幕視窗清空畫面（payload `null`） |
| `overlay://state` | `{style, click_through, visible, shortcut, shortcut_registered, locale, show_translation, shortcuts}`（`shortcuts`：`[{action, keys, registered}]`，三個快速鍵；舊版 Rust 沒有時前端當作空清單）（`locale` 在主視窗送出前是 `null`；`show_translation` 在主視窗送出前是 `true`，舊版 Rust 沒有這個欄位時前端也當作 `true`） |

前端（`src/lib/validate.ts`）再驗證一次所有事件與指令結果；不合格的 payload 記錄後丟棄。指令錯誤統一為 `{kind: engine|timeout|disconnected|invalid_input, …}`，由 `describeError` 轉成介面語言的說明。

### 權限（最小權限）

Milestone 12–14 新增的指令：`languages_list`、`settings_preview_languages {languages: {mode?, source_language?, target_language?, partner_language?}}`（`deny_unknown_fields`；語言碼只檢查格式：1–16 個英數、`-`、`_`，清單由引擎決定；背景期限）、`models_pull_translation {model}`（名稱規則同引擎：1–200 字、英數與 `._:/-`、英數開頭、不含 `..`；背景期限）、`session_set_translation {enabled}`（一般期限）、`overlay_set_show_translation {enabled}`；振假名（`furigana.md`）新增 `session_set_furigana {enabled}`（一般期限），`protocol.rs` 驗證 `subtitle.ruby`（不合法只丟掉 `ruby`）。`protocol.rs` 另外轉發 `status.data`（物件，序列化後最多 10 000 bytes，沒有時不出現）與 `download_progress.kind`（小寫字母與底線、最多 32 字元；不認得的值照樣轉發，由前端當作沒有 kind）。

競品補強新增的指令：`models_delete {model}`（模型 id 規則同 `models_download`，`gpu-runtime` 直接拒絕；背景期限）。

`build.rs` 以 `AppManifest::commands` 為每個指令產生 `allow-*` 權限，未列在 capability 的指令一律拒絕。

| 視窗 | 權限 |
|---|---|
| `main`（`capabilities/main.json`） | 事件 listen／unlisten、讀版本號、Milestone 12–14 的五個指令、`update_check`／`update_install`（不給 `updater:default`：外掛自己的 JavaScript 指令會繞過關閉引擎的步驟）、`process:allow-restart`、`opener:allow-open-url`（scope 只有 `https://docs.nvidia.com/cuda/eula/index.html` 與 `https://ollama.com/download`）、全部引擎與字幕視窗指令（含 `overlay_set_locale`）、`app_about`、`open_third_party_licenses`、`output_info`、`transcript_save`、`open_output_folder`、`obs_set_output`、`models_delete`（`engine_shutdown` 除外：只在程式結束時由 Rust 呼叫） |
| `overlay`（`capabilities/overlay.json`） | 事件 listen／unlisten、`start-dragging`、`start-resize-dragging`、`set-title`（依介面語系改視窗標題）、`overlay_get_state`、`overlay_set_visible` |

opener 外掛（`tauri-plugin-opener` 2.7.2，Apache-2.0 OR MIT）只給前端開這兩個網址（NVIDIA CUDA EULA、Ollama 官方下載頁）；沒有 `allow-default-urls`、沒有 `open-path`／`reveal-item-in-dir`。兩個授權資料夾與兩個輸出資料夾由 Rust 指令以 opener 的 Rust API 開啟（前端只選 `app`／`engine`、`transcripts`／`obs`，路徑在 Rust 端決定）。`combination.test.ts` 檢查 capability 裡的 opener 權限正好是這兩個網址。

另設 CSP：`default-src 'self' ipc: http://ipc.localhost; img-src 'self' data:; style-src 'self'`；前端不使用 `innerHTML`，引擎傳來的文字一律以 `textContent` 顯示。

## 自動更新

- `tauri-plugin-updater`，端點 `https://github.com/alianlbj23/kikitori/releases/latest/download/latest.json`，公鑰已寫入 `tauri.conf.json`（對應的私鑰只在維護者本機與 GitHub Secrets，不在 repo）。
- `bundle.createUpdaterArtifacts: true`（Tauri v2 格式）。打包時需要 `TAURI_SIGNING_PRIVATE_KEY`（與密碼）環境變數，只放在本機與 GitHub Secrets。發布流程、`latest.json` 的平台鍵與 Secrets 設定見 `release-update.md`。
- macOS：`bundle.macOS.signingIdentity` 為 `"-"`（ad-hoc 簽章，不做付費簽章與公證）。
- 正式版啟動時檢查一次（15 秒逾時，失敗不打擾）；設定頁可手動檢查。流程：「kikitori x.y.z 已經推出」（有更新說明時附「看更新內容」）→「下載並安裝」→ 下載進度 →「已安裝，重新啟動後生效」→「立即重新啟動」（`relaunch`）。Windows 安裝程式使用 `passive` 模式，通常會自行關閉並重開程式。
- **更新說明**：`update_check` 回傳的 `notes`（`latest.json` 的 `notes`，即 `docs/release-notes/v<版本>.md`，見 `release-update.md`）放在更新提示裡的 `<details>`「看更新內容」，預設收合；展開後最高 `min(16rem, 40vh)`、超過就捲動（訊息列固定在內容區上方，不能把畫面推太多）。說明來自網路，一律當純文字：`lib/update-notes.ts` 的純函式 `updateNotesBlocks` 只辨識整行的結構——`#` 標題、`-`／`*`／`+` 清單項目（縮排的也攤平成同一層）、空行分段——並去掉成對的 `**`，其他 Markdown（連結、程式碼、表格、HTML）原樣顯示；`main.ts` 只以 `textContent` 建立 `p`／`ul`／`li`，不使用 `innerHTML`。`notes` 為 `null`、空白或只是預設的 `kikitori v<版本>`（沒有說明檔的版本與以前的 `latest.json`）時不顯示「看更新內容」。按「下載並安裝」後換成進度訊息，說明一併收起。測試：`update-notes.test.ts`（預設字串與空白、標題／段落／清單、CRLF 與縮排、不是標題或清單的 `#`／`-`、HTML 原樣、空的標題與項目、截斷標示）。
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
8. 假引擎，輸入一直沒有聲音（`--metrics silent`：看音量表與「沒有收到聲音」提示）
9. 假引擎，辨識跟不上（`--metrics backlog`：待處理句數每 3 秒加一、最多 6，看「建議改用較小的模型」提示）
10. 正式引擎（實際擷取聲音、載入模型）
11. 建置除錯版執行檔（`tauri build --debug --no-bundle`）
12. 工具：更新引擎訊息碼清單（`extract_message_codes.py`）
13. 工具：重新擷取引擎測試資料（`capture_engine_fake.py`：以正式引擎假模式（nvidia、假 Ollama、暫存設定目錄）依序送 `settings.get`、`hardware.detect`、`models.list`、預覽／套用推薦、`languages.list`、兩次 `settings.preview_languages`、`models.validate`、改用未安裝的翻譯模型後 `session.start`（收集到 `translation_model_missing` 的 status）、`models.pull_translation`（收集進度事件）、`session.set_translation`，暫存目錄路徑換成 `C:/fake`，寫入 `src/lib/testdata/engine-fake-nvidia.json`）

正式引擎的假模式把 GPU 加速元件裝在行程專屬的暫存目錄，所以安裝後 UI 自動重啟引擎時，新的引擎又回到「未安裝」——這是假模式的限制，真的引擎裝在使用者資料目錄、重啟後仍在。`slow-runtime` 情境（下載很慢，用來看取消）可用環境變數自行指定。

也可以直接設定環境變數：

```text
KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
npm run tauri dev
```

`fake_engine.py` 參數：`--garbage-every N`（每 N 個字幕事件後輸出一行壞 JSON）、`--crash-after SECONDS`（以結束碼 3 模擬當機）、`--translation-server {ollama,openai,offline,auth}`（假翻譯伺服器：`ollama` 可下載；`openai` 回 `pull_unsupported` 且開始時不檢查翻譯模型；`offline` 回 `translation_server_unreachable`；`auth` 回 `translation_server_auth_failed`）、`--metrics {normal,silent,backlog}`（假輸入：一般、一直低於 −60 dBFS、待處理句數超過 3）。競品補強的模仿：即時字幕帶 `stable_chars`（前一次即時字幕的長度，也就是 LocalAgreement-2 會確認的前綴），完整字幕帶 `latency_ms`；執行中每秒一則 `metrics`（session 結束的 `idle` 之後不再送）；`models.list` 的已下載模型有 `disk_bytes`；`models.delete` 依引擎的順序檢查（`unknown_model` → 執行中的 session 或下載正在用 → `model_in_use` → `model_not_downloaded`），成功回 `{model, freed_bytes}`；`settings.set` 以引擎的限制與錯誤路徑驗證 `translation.glossary`（例如 `translation.glossary.entries[1].source: duplicate of an earlier entry …`）。Milestone 12–14 的模仿（對照 `src/kikitori/engine/testing.py`）：訊息一律 `{code, params, message}`；`languages.list` 用與 `catalog.py` 相同的 20 種語言與品質等級；推薦依語言（同一種語言 → `translation_status: not_needed`、C 級語言 → `asr_language_quality_low`）；`settings.preview_languages` 在收到請求時合併設定與驗證（錯誤立即回應），在背景回答模型、安裝狀態與下載量；`session.start` 依引擎順序先回報 `translation_model_missing`（`data.models`），再回報 `model_not_downloaded`；`models.pull_translation` 寫出回應後才開始，四段進度（`kind: "translation"`，第一筆 0／0、總量會變大），`fake-broken-pull:1b` 一半時失敗；`session.set_translation` 存檔並切換執行中的 session；ASR 與 GPU 加速元件的進度帶 `kind`。它保留下來是因為正式引擎的假模式沒有故障注入、字幕也會結束；Milestone 6 的方法（`hardware.detect`、`models.list`、`models.validate`、`settings.apply_recommendation`、`gpu-runtime` 授權閘門）只做形狀正確的固定模仿，背景方法延遲 0.3 秒、在單一執行緒依序回應，以重現「回應晚於之後的請求」。推薦與驗證的規則以正式引擎為準。

圖示：`uv run --no-project python app/dev/make_icon.py` 產生 `src-tauri/icons/source/icon.png`（藍色底、白色原文條與字幕黃譯文條，全為本專案原創），再執行 `npx tauri icon src-tauri/icons/source/icon.png`。

## 測試

| 指令（在 `app/` 或 `app/src-tauri/`） | 內容 |
|---|---|
| `npm test` | vitest：競品補強（`stable.test.ts`：穩定前綴、完整字幕整句、缺欄位與非正數、超過長度、小數、code point 與 UTF-16、不切字素叢集、由右到左；`glossary.test.ts`：讀設定與舊引擎、比對鍵、每個欄位的限制、code point 長度、重複標在後一列且依字幕語言、語言清單未知時、筆數上限、整份清單的 patch、引擎錯誤路徑對到列與欄位、CSV 的 BOM／標頭／引號／公式防護與完整來回、沒有 BOM 或標頭、LF／CR、依標頭名稱找欄位、分號與 Tab、全部語言與去空白、空行與缺欄位的行號、合併時略過完全相同的列並保留衝突、匯出檔名符合 Rust 規則；`metrics.test.ts`：音量換算、未執行時不顯示、延遲、落後的門檻與遲滯、15 秒沒有聲音與到期時間、−60 dBFS 不算沒聲音、關閉後到條件解除前不再出現、新 session 重來、較舊 session 丟棄；`session-end.test.ts`：等引擎 `idle` 才存而且包含停止後才到的完整字幕、2 秒後仍未回報就存、已結束時立即存、同一次結束只存一次、開始新 session 前先存；`model-delete.test.ts`：未下載不提供、閒置時可刪選用中的模型、執行中只擋用到的模型、下載中／刪除中／引擎未就緒、釋放空間與退回下載大小、選用中的提醒；`subtitles.test.ts` 的 `stable_chars` 隨即時字幕更新、完整字幕取代後清除；`validate.test.ts` 的 `stable_chars`／`latency_ms` 只留在對應的字幕、`metrics` 與 `disk_bytes`、`models.delete` 結果；`diagnostics.test.ts` 不列出詞條內容；`messages.test.ts` 的「即時 / 完整」精度組合）、匯出（`export.test.ts`：時間碼與 16 kHz 換算、結束一定晚於開始、只匯出完整字幕、三種內容與以原文代替、SRT 編號／CRLF／`-->`、VTT 標頭與跳脫、TXT、CSV 的 BOM／標頭／引號／公式防護、由右到左文字的 RLM、檔名格式與 Rust 規則一致）、閱讀節奏（`rhythm.test.ts`：最少顯示時間、0 不等、不累積延遲、完整翻譯重新計時、原地更新與移除、淡出與喚醒時間、淡出後文字改變才回來、0 不淡出、從最後一次變化起算、不顯示更舊的句子）、診斷資訊（`diagnostics.test.ts`：網址帳密與查詢字串、權杖與金鑰格式、家目錄使用者名稱、設定中秘密鍵與 `api_key_env` 只留名稱、最近訊息上限、報告內容與不含使用者名稱）、翻譯服務引導（`translation-guide.test.ts`：本機／遠端、未偵測、關閉翻譯、開始被擋、capability 允許下載頁）、閱讀節奏與快速鍵清單與 OBS／輸出指令結果的驗證、設定頁的新欄位（秒→毫秒、範圍、`obs_output` 不進外觀）；介面多語系（見「介面多語系」的「檢查」：字典完整性、佔位符、複數類別、HTML 用到的鍵、原始碼不得有 CJK 文字、語系對應、退回、引擎訊息、每個引擎訊息碼的字典鍵與參數、參數在地化）、語言選單（`languages.test.ts`：正式引擎擷取的 `languages.list`／`settings.preview_languages`、選項文字與自動偵測、篩選、本地檢查、只送目前模式的欄位、預覽是否對應目前選擇、儲存的 patch 與「同時改用推薦的模型」、衝突的對方語言、預覽各狀態的文字）、開始前下載（`start-downloads.test.ts`：全部完成才自動開始、以 kind 區分同名模型、舊引擎沒有 kind、總量變大、失敗後只重試失敗的、等其他下載結束才報失敗、`pull_unsupported`／`translation_server_unreachable`／`translation_server_auth_failed` 停下並說明、`download_in_progress`、不重抓同一次開始已下載的模型、取消、面板文字）、「顯示翻譯」（`translation-toggle.test.ts`：執行中用 `session.set_translation`、否則 `settings.set`、引擎失敗不動字幕視窗、只有字幕視窗失敗時仍保留設定、只顯示原文的行）、`status.data.models` 與 `download_progress.kind` 的驗證、字幕視窗快照的 `show_translation`、`ui.locale` 的讀取與 patch、字幕視窗快照的 `locale`、更新指令的結果與進度事件驗證、字幕狀態規則、事件與結果驗證（`hardware.detect`／`models.list`／`settings.apply_recommendation` 用正式引擎假模式實際輸出的 `src/lib/testdata/engine-fake-nvidia.json`）、設定頁與自訂組合的表單驗證與 patch、schema 2 欄位、`models.validate` 結果、儲存條件、只套用最新的檢查結果、GPU 加速元件安裝後的重啟決定、opener 權限只開放 EULA 網址、`app_about`（兩個授權資料夾路徑，可為 `null`、型別錯誤時拒絕）、格式化、自動推薦（`recommend.test.ts`：第一次執行只套用一次且先等偵測、訊息文字、不是新設定檔或使用者已存組合時不套用、引擎回 `applied: null` 視為略過、提示依 `differs` 與指紋顯示／隱藏、「不要再提醒」清單、缺少模型的提示與進度、下載中不能開始、`api.ts` 呼叫的每個指令都在 `build.rs` 與 `capabilities/main.json`） |
| `npm run build` | `tsc` 型別檢查 + `vite build` |
| `cargo test` | 競品補強：`subtitle` 的 `stable_chars`（轉發、超過原文字數時夾住、型別錯誤拒絕）與 `latency_ms`（`null` 與不合理的值改為沒有）、沒有時轉發的 payload 不出現這兩個欄位、字幕裡不認得的欄位照樣接受、`metrics` 的欄位與範圍、`models_delete` 的參數（拒絕 `gpu-runtime`）；端到端：`fake_engine.py` 的即時字幕帶 `stable_chars`、完整字幕帶 `latency_ms`、收得到 `metrics`，`models.delete` 刪一次成功、再刪 `model_not_downloaded`、清單改成未下載且 `disk_bytes: null`。字幕紀錄檔名（接受產生的檔名；拒絕分隔字元、`..`、磁碟代號、隱藏檔、大寫或其他副檔名、結尾空白與點、非 ASCII、Windows 保留名稱、過長）、路徑一定是資料夾的直接子項目、原子寫入覆寫且不留暫存檔、過大的內容不建立任何東西、`output_info` 的位置、`OutputFolder`／`ObsContent` 只接受列舉值；OBS（與字幕視窗相同的句子規則、內容選擇與「顯示翻譯」、關閉時不碰磁碟、清除與關閉時清空、連續 200 次更新時每秒不超過 10 次寫入且最後內容正確、閒置清空、結束時清空、找不到「文件」資料夾時回報錯誤）；`OverlayStyle` 的閱讀節奏欄位（缺少時用預設值、範圍）與快速鍵清單。Milestone 12–14：翻譯模型名稱規則、`settings_preview_languages` 只送給定的欄位並拒絕未知欄位與格式錯誤的語言碼、`status.data` 轉發與大小上限、`download_progress.kind` 驗證、字幕視窗的 `show_translation`（初始 `true`、切換、不受外觀變更影響）；更新前關閉引擎（`fake_engine.py`：在 runtime worker 上同步呼叫，`engine.shutdown` 讓引擎自己結束；`--ignore-shutdown` 不回應時在期限後強制結束；沒有引擎時立即返回）、更新指令的逾時範圍與進度事件格式、協定解析與驗證、有上限的逐行讀取、id 配對（亂序回應）、逾時與晚到回應、錯誤回應、壞行略過、輸出關閉時請求失敗、指令選擇、重啟退避、參數驗證；字幕視窗的介面語系（初始為 `None`、格式檢查、拒絕時保留舊值）、正式版資源目錄的引擎路徑與覆寫、`about_info`（兩個授權資料夾各自只在存在時回報；`licenses` 與 `tauri.sidecar.conf.json` 的資源名稱一致；`LicenseFolder` 只接受 `app`／`engine`）、`models.download`／`models.validate` 的參數（授權旗標只給 `gpu-runtime`）；另外的端到端測試以真的子行程執行：`fake_engine.py`（含壞資料注入）、`fake_engine.py`（背景方法的回應晚於之後的請求，仍各自配對）、`fake_engine.py --crash-after`（自動重啟）、正式引擎假模式（`KIKITORI_ENGINE_FAKE=1`、`KIKITORI_CONFIG_DIR` 指到暫存目錄）的基本協定，以及 Milestone 6：`hardware.detect` 與一般請求並行、`models.list`、`models.validate`（候選不存檔、`invalid_settings`）、`settings.apply_recommendation`（安裝前 CPU）、`gpu-runtime` 未同意 → `license_not_accepted`（訊息含 EULA 網址、沒有下載事件）、同意 → 安裝到 `done` → 推薦改為 CUDA；Apple Silicon 情境 → `gpu_runtime_unsupported`。需要 `uv` 在 PATH。 |
| `cargo clippy --all-targets -- -D warnings` | lint |

## 各平台需要實機確認的地方

- **透明視窗**：Windows（WebView2）已確認。macOS 需要 `macOSPrivateApi: true`（已開，代表不能上架 Mac App Store）。Linux（WebKitGTK）需要合成器（compositor）：X11 容器實測（Xvfb、WebKitGTK 2.52）沒有合成器時字幕視窗是黑底不透明、會蓋住後面的視窗；開了 picom（xrender）就真的透明。Wayland 待實機確認。
- **置頂**：macOS 全螢幕播放的影片位於獨立 Space，可能蓋過字幕視窗（已設 `visibleOnAllWorkspaces`，仍需實測）；Wayland 不允許應用程式自行置頂，GNOME 上可能無效。
- **滑鼠穿透**：Windows／macOS 由系統支援；Linux X11 可用，Wayland 依合成器而定。
- **全域快速鍵**：X11 容器實測 Ctrl+Alt+K（xdotool 送出）可切換滑鼠穿透；同一個按鍵已被其他程式（例如另一個 kikitori）佔用時 UI 正確改提示用開關。Wayland 下 `global-hotkey` 可能無法註冊；macOS 可能要求輔助使用權限。
- **無邊框縮放**：右下角把手用 `startResizeDragging`；Linux 部分視窗管理員不支援。
- **字型**：macOS／Linux 會改用蘋方、Hiragino、Noto CJK。沒有任何 CJK 字型的 Linux（例如精簡安裝只有 DejaVu）整個介面會變成豆腐方塊（容器實測），所以 deb 以 Recommends 建議 `fonts-noto-cjk`；AppImage 不帶字型，靠系統安裝。
- **表單控制項**：WebKitGTK 以原生外觀畫 `<select>`、不套用 CSS 背景色；深色底上的選單要設 `color-scheme: dark`（側欄的聲音來源選單就是這樣修的）。
