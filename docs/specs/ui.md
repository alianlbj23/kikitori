# Spec: ui（Tauri 桌面程式）

> 狀態：Milestone 7 第一階段已實作（2026-11-05）。
> 上層約定：`docs/specs/app-architecture.md`（行程架構、引擎協定、視窗、更新）與 `docs/specs/pipeline-engine.md`（協定欄位細節、設定檔結構）。本文件只補充 UI 端的設計，不改協定。

## 範圍

- `app/`：Tauri v2 + Vite + TypeScript（不用 UI 框架）。
- 兩個視窗：`main`（控制與設定）、`overlay`（雙語字幕）。
- Rust 端的引擎橋接（spawn 引擎、NDJSON、id 配對、逾時、重啟）、字幕視窗控制、全域快速鍵、自動更新。
- 開發用的假引擎 `app/dev/fake_engine.py`、開發選單 `app/dev/launch.mjs`、圖示產生器 `app/dev/make_icon.py`。

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

- **這台電腦**：`hardware.detect` 的作業系統、處理器執行緒數、記憶體、顯示卡（或 Apple Silicon）。
- **建議組合**：形狀依 `docs/specs/hardware.md` 的 `ModelCombination.to_dict()`（`fast_asr`／`final_asr` 各含 `model`、`device`、`compute_type`、`cpu_threads`，`translation_fast`／`translation_final`，`reasons`，`warnings`，`gpu_runtime_missing`）。`recommendation` 為 `null` 時說明這一版還不會自動推薦並給出一般建議；有值時列出兩條辨識路徑與翻譯模型、原因與警告，「套用建議組合」以 `settings.set` 寫入，規則與引擎的 `recommendation_patch` 相同（兩條路徑裝置相同才寫該裝置，否則 `auto`；執行緒 0 存成 `null`；沒推薦翻譯模型就保留原設定）。設定檔有 `asr.fast_compute_type` 等 v2 欄位時才一併寫入精度與執行緒。
- **語音辨識模型**：`models.list.asr` 表格（大小、速度等級、授權、狀態，`recommended_for` 標示「建議：即時／完整」）。未下載的有「下載」按鈕；下載中顯示進度條與百分比（`download_progress` 事件），失敗顯示原因與「重新下載」，完成後重新讀取清單。下方三個選單選即時／完整模型與運算裝置（自動／CPU／CUDA），「套用」寫入 `asr`。
- **GPU 加速元件**：`models.list.gpu_runtime` 存在且 `supported`、未安裝時啟用「下載 GPU 加速元件」。旁邊依 hardware.md 的要求說明「會從 PyPI 下載 NVIDIA cuBLAS（大小），使用即表示同意 NVIDIA CUDA EULA」並附授權網址；按下後先出現「同意並下載」確認，才呼叫 `models.download {"model": "gpu-runtime"}`，進度沿用 `download_progress`。引擎沒有回報 `gpu_runtime` 時按鈕停用並註明之後提供。（UI 沒有開啟外部連結的權限，授權網址以可選取的文字顯示。）
- **翻譯模型**：翻譯服務網址、已安裝數量或連線錯誤（例如 Ollama 沒開）；模型清單附授權、不可商用、授權排除地區、是否已安裝。兩個輸入框（附自動完成）選即時／完整翻譯模型，「套用」寫入 `translation`。
- `models.list` 同時接受兩種形狀：第一版（`translation.{base_url, installed, error, presets}`）與 hardware.md（`translation.{server, models}` 加 `gpu_runtime`）。`hardware.gpus[]` 的顯示記憶體讀 `vram_bytes` 或 `memory_total_mb`。

### 設定頁

| 區塊 | 欄位 | 寫入設定 |
|---|---|---|
| 翻譯服務 | 服務網址、API 金鑰的**環境變數名稱**、即時／完整翻譯模型 | `translation.*` |
| 語言 | 譯文語言（固定繁體中文，唯讀）、口譯對方的語言（日語／英語） | `partner_language` |
| 字幕外觀 | 譯文字級（14–96 px）、背景不透明度（0–100%）、字幕位置（貼齊上緣／下緣）、同時顯示的句數（1–3），附即時預覽 | `subtitle.{font_size, background_opacity, position, max_lines}` |
| 版本與更新 | 程式版本、引擎版本與協定版本（假引擎會註明）、「檢查更新」 | — |

- 只存環境變數名稱，金鑰本身不經過 UI、不寫入任何檔案。
- 儲存前先在前端驗證（網址必須 http/https、變數名稱 `^[A-Za-z_][A-Za-z0-9_]*$`、模型名稱 1–200 字元、數值範圍），錯誤顯示在欄位下方。引擎回傳 `invalid_settings` 時依訊息中的欄位路徑（例如 `translation.base_url: …`）標到對應欄位。
- 字幕外觀存在設定檔的 `subtitle` 物件（引擎原樣保存、不解讀）；儲存後同時透過 `overlay_set_style` 推給字幕視窗。

### 訊息

內容區上方的訊息列（最多三則、可關閉）：操作失敗的原因與下一步、引擎 `log` 事件的 warning／error、`status: error` 的 `code` 對應說明（例如 `model_not_downloaded` →「模型還沒下載。請到『模型』頁下載後再開始。」）、更新提示。

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
| `engine.rs` | 引擎行程：選擇指令、spawn（不經 shell，Windows 加 `CREATE_NO_WINDOW`）、stderr 轉到 app 日誌、`engine.info` 握手並檢查 `protocol_version == 1`、意外結束時自動重啟（1 s、2 s、4 s 退避；60 秒內超過 3 次就停在「失敗」等使用者按重新啟動）、結束程式時先送 `engine.shutdown` 再於 3 秒後強制結束。 |
| `commands.rs` | 每個協定方法一個 Tauri 指令，先驗證參數（模型 id 字元集與長度、裝置 id 長度、設定必須是物件且 ≤ 64 KiB、模式只能 `media`／`interpret`），再依方法給不同期限：一般 10 s、`hardware.detect` 30 s、`models.list` 20 s、`session.start` 30 s、`session.stop` 15 s、握手 120 s（第一次 `uv run` 可能要建立虛擬環境）。另有 `engine_restart`、`bridge_status` 與字幕視窗指令。 |
| `overlay.rs` | 字幕外觀（`deny_unknown_fields` 與範圍檢查）、穿透與可見狀態。 |
| `lib.rs` | 外掛、狀態、事件轉發、全域快速鍵、生命週期（關閉主視窗即結束程式）。 |

### 引擎指令的選擇

1. 環境變數 `KIKITORI_ENGINE_CMD`：JSON 字串陣列（路徑有空白時用），或以空白分隔的指令。
2. 除錯版（`debug_assertions`）：在 repo 根目錄執行 `uv run kikitori engine`。
3. 正式版：與主程式同目錄的 `kikitori-engine[.exe]`（Tauri `externalBin` 去掉 target triple 後的檔名）。

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
| `main`（`capabilities/main.json`） | 事件 listen／unlisten、讀版本號、`updater:default`、`process:allow-restart`、全部引擎與字幕視窗指令（`engine_shutdown` 除外：只在程式結束時由 Rust 呼叫） |
| `overlay`（`capabilities/overlay.json`） | 事件 listen／unlisten、`start-dragging`、`start-resize-dragging`、`overlay_get_state`、`overlay_set_visible` |

另設 CSP：`default-src 'self' ipc: http://ipc.localhost; img-src 'self' data:; style-src 'self'`；前端不使用 `innerHTML`，引擎傳來的文字一律以 `textContent` 顯示。

## 自動更新

- `tauri-plugin-updater`，端點 `https://github.com/alianlbj23/kikitori/releases/latest/download/latest.json`，公鑰已寫入 `tauri.conf.json`（對應的私鑰只在維護者本機與 GitHub Secrets，不在 repo）。
- `bundle.createUpdaterArtifacts: true`（Tauri v2 格式）。打包時需要 `TAURI_SIGNING_PRIVATE_KEY`（與密碼）環境變數，只放在本機與 GitHub Secrets。
- 正式版啟動時檢查一次（15 秒逾時，失敗不打擾）；設定頁可手動檢查。流程：「kikitori x.y.z 已經推出」→「下載並安裝」→ 下載進度 →「已安裝，重新啟動後生效」→「立即重新啟動」（`relaunch`）。Windows 安裝程式使用 `passive` 模式，通常會自行關閉並重開程式。

## 打包與 sidecar

- `tauri.conf.json` 不含 `externalBin`，所以開發與 CI 測試不需要先有引擎執行檔。打包時合併 `src-tauri/tauri.sidecar.conf.json`：`npm run tauri build -- --config src-tauri/tauri.sidecar.conf.json`，並把 PyInstaller 產物放在 `src-tauri/binaries/kikitori-engine-<target-triple>[.exe]`（此目錄已列入 `.gitignore`）。
- PyInstaller one-folder 模式除了執行檔還有 `_internal/` 資料夾；`externalBin` 只會複製單一執行檔，release 工作流程需另外以 `bundle.resources` 帶上該資料夾，或改用 one-file。這部分在 CI/CD 里程碑決定。
- 版本號 `0.1.0` 與 `pyproject.toml` 一致（`package.json`、`Cargo.toml`、`tauri.conf.json`）。

## 開發

需要 Node 24、Rust stable（MSVC）、uv。

```text
cd app
npm install
npm run menu          # 互動選單：選擇引擎後執行 tauri dev，或建置除錯版
```

選單項目：

1. 正式引擎的假模式（`KIKITORI_ENGINE_FAKE=1 uv run kikitori engine`，合成聲音、假辨識與翻譯；建議）
2. `app/dev/fake_engine.py`（只用標準庫；字幕會不停產生，部分→完整的過程比較明顯）
3. 假引擎加故障注入（每 3 句插入一行壞資料、25 秒後當機，用來看重啟與錯誤處理）
4. 正式引擎（實際擷取聲音、載入模型）
5. 建置除錯版執行檔（`tauri build --debug --no-bundle`）

也可以直接設定環境變數：

```text
KIKITORI_ENGINE_CMD='["uv","run","--no-project","python","app/dev/fake_engine.py"]'
npm run tauri dev
```

`fake_engine.py` 參數：`--garbage-every N`（每 N 個字幕事件後輸出一行壞 JSON）、`--crash-after SECONDS`（以結束碼 3 模擬當機）。

圖示：`uv run --no-project python app/dev/make_icon.py` 產生 `src-tauri/icons/source/icon.png`（藍色底、白色原文條與字幕黃譯文條，全為本專案原創），再執行 `npx tauri icon src-tauri/icons/source/icon.png`。

## 測試

| 指令（在 `app/` 或 `app/src-tauri/`） | 內容 |
|---|---|
| `npm test` | vitest：字幕狀態規則、事件與結果驗證、設定表單驗證與 patch、格式化 |
| `npm run build` | `tsc` 型別檢查 + `vite build` |
| `cargo test` | 協定解析與驗證、有上限的逐行讀取、id 配對（亂序回應）、逾時與晚到回應、錯誤回應、壞行略過、輸出關閉時請求失敗、指令選擇、重啟退避、參數驗證；另外三個端到端測試以真的子行程執行：`fake_engine.py`（含壞資料注入）、`fake_engine.py --crash-after`（自動重啟）、正式引擎假模式（`KIKITORI_ENGINE_FAKE=1`、`KIKITORI_CONFIG_DIR` 指到暫存目錄）。需要 `uv` 在 PATH。 |
| `cargo clippy --all-targets -- -D warnings` | lint |

## 各平台需要實機確認的地方

- **透明視窗**：Windows（WebView2）已確認。macOS 需要 `macOSPrivateApi: true`（已開，代表不能上架 Mac App Store）。Linux（WebKitGTK）需要合成器（compositor）；部分 Wayland／X11 環境透明可能變黑底。
- **置頂**：macOS 全螢幕播放的影片位於獨立 Space，可能蓋過字幕視窗（已設 `visibleOnAllWorkspaces`，仍需實測）；Wayland 不允許應用程式自行置頂，GNOME 上可能無效。
- **滑鼠穿透**：Windows／macOS 由系統支援；Linux X11 可用，Wayland 依合成器而定。
- **全域快速鍵**：Wayland 下 `global-hotkey` 可能無法註冊（UI 會改提示用開關）；macOS 可能要求輔助使用權限。
- **無邊框縮放**：右下角把手用 `startResizeDragging`；Linux 部分視窗管理員不支援。
- **字型**：macOS／Linux 會改用蘋方、Hiragino、Noto CJK；沒有安裝 Noto CJK 的 Linux 可能退回其他字型。
