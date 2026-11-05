# Spec: 競品比較後補強的功能

> 狀態：規格（2026-11-06）。進度：F1、F5、F6、F8 的 UI 與 Rust 部分已實作（2026-11-06，細節見 `ui.md`「匯出字幕紀錄」「閱讀節奏」「OBS 文字檔」「翻譯服務連不上時」「全域快速鍵」「診斷資訊」）；F2、F3、F4、F7 的 UI 與 Rust 部分已實作（2026-11-06，`ui.md`「穩定的即時字幕」「術語表」「延遲、落後與音量」「刪除模型」），引擎部分另行實作。依據：`research_notes/競品比較/open_source_projects.md`（約 30 個開源專案）、
> `research_notes/競品比較/commercial_products.md`（商用產品）。使用者已授權依建議實作，最後驗收。

## Objective

kikitori 的核心情境（日語直播 → 繁中、系統聲音、免費、離線）目前沒有競品同時做到；但同類工具普遍有的
**匯出、穩定的即時字幕、術語表、延遲指示、閱讀節奏、首次使用引導**，kikitori 還沒有。這份規格補上這些，
並列出「先不做」與原因。

## 功能

### F1 匯出字幕紀錄（UI＋Rust）

> 進度：已實作（`app/src/lib/export.ts`、`app/src-tauri/src/output.rs` 的 `transcript_save`／`open_output_folder`／`output_info`）。只匯出完整字幕；自動存檔的設定在 `ui.auto_save_transcripts`。按「停止」後等引擎回報 `idle` 或 2 秒再自動存檔，最後一句的完整字幕才不會漏掉（`app/src/lib/session-end.ts`）。

- 字幕紀錄頁「匯出」：SRT、VTT、TXT、CSV（UTF-8 with BOM 給 Excel）。時間碼依 `start_sample/end_sample`（16 kHz）。
  雙語時 SRT／VTT 每個 cue 兩行（原文、譯文）；可選只匯出原文或只匯出譯文。
- 「每次自動存檔」（預設關）：session 結束時存 SRT＋TXT。
- 存放位置：`文件\kikitori\transcripts\YYYY-MM-DD_HHMMSS_<模式>.<ext>`（Rust 以 Tauri path API 取得文件資料夾，
  不新增 dialog／fs 外掛）；存完顯示「開啟資料夾」（既有 opener 外掛）。
- 檔名與內容由 TS 純函式產生（可測），Rust 只負責驗證路徑在 transcripts 資料夾內並寫檔。

### F2 穩定的即時字幕（引擎＋UI）

> 進度：UI 已實作（`app/src/lib/stable.ts`；字幕紀錄與字幕視窗只淡化不穩定的尾巴，`stable_chars` 以 code point 計、不切字素叢集）。Rust 轉發 `stable_chars`／`latency_ms`。

> 進度：引擎已實作（2026-11-06；`src/kikitori/pipeline/stability.py`，即時字幕的 `stable_chars`、穩定前綴變長才重送即時翻譯；細節見 `pipeline-engine.md`「穩定前綴」「協定變更（F2／F4／F7）」）。UI 另行實作。

- 同一句的連續即時辨識結果以 LocalAgreement-2（whisper_streaming 的做法）在文字層取最長共同前綴，
  `subtitle` 事件（`is_final=false`）多 `stable_chars`：原文前幾個字已穩定。
- 即時翻譯只在「穩定部分變長」或句子結束時重送（減少 Ollama 請求與譯文閃爍）；完整翻譯不變。
- UI：穩定部分正常亮度，未穩定的尾巴淡色；字幕視窗同樣處理。

### F3 術語表編輯器（UI）

> 進度：已實作（`app/src/lib/glossary.ts`；設定頁獨立表單、與引擎相同的本地檢查、引擎錯誤路徑對到列、CSV 匯入（BOM、引號、`;`／Tab 分隔）與匯出（寫到字幕紀錄資料夾，沿用 `transcript_save`））。

- 設定頁「術語表」：內建直播用語開關、使用者詞條表（原文、譯文、適用的字幕語言或「全部」），新增／刪除／匯入匯出 CSV。
  資料形狀依引擎 `translation.glossary`（`docs/specs/vtuber-profile.md`）。

### F4 延遲、落後與音量（引擎＋UI）

> 進度：UI 與 Rust 已實作（`app/src/lib/metrics.ts`；`metrics` 事件經 Rust 驗證後以 `engine://metrics` 只送主視窗；落後提示在 > 3 出現、≤ 1 才收起）。

> 進度：引擎已實作（2026-11-06；完整字幕的 `latency_ms`、每秒的 `metrics` 事件 `{session_id, level_dbfs, backlog, latency_ms_median}`；只用新事件 `metrics`，`status` 不變；細節見 `pipeline-engine.md`「量測」）。UI 另行實作。

- 引擎每句完整字幕附 `latency_ms`（語音結束 → 完整字幕送出）；`status`／新事件 `metrics` 每秒回報
  `backlog`（待處理的完整句數）與輸入音量 `level_dbfs`（RMS）。
- UI：狀態列小型音量表與延遲（中位數），落後超過 3 句時提示「辨識跟不上，建議改用較小的模型」；
  執行中 15 秒完全沒有聲音（< −60 dBFS）時提示檢查聲音來源。

### F5 字幕閱讀節奏（UI）

> 進度：已實作（`app/src/lib/rhythm.ts`；`OverlayStyle.min_display_ms`／`idle_clear_secs` 也經 Rust 驗證）。

- 字幕視窗：每句至少顯示 `min_display_ms`（預設 1500）；最後一句後 `idle_clear_secs`（預設 8，0 = 不清）自動淡出。
  加入字幕設定（`settings.subtitle`，引擎不解讀）。

### F6 OBS 文字檔輸出（Rust）

> 進度：已實作（`app/src-tauri/src/obs.rs`，設定 `subtitle.obs_output`；每秒最多寫 10 次，session 結束、閒置、清除字幕快速鍵與關閉時清空）。

- 設定「輸出字幕到文字檔（給 OBS）」：開啟後把目前字幕（原文／譯文／兩者）覆寫到
  `文件\kikitori\obs\subtitle.txt`（UTF-8，原子寫入），OBS 用「文字（GDI+）→ 從檔案讀取」。不開網路埠。

### F7 模型管理（引擎＋UI）

> 進度：UI 與 Rust 已實作（`app/src/lib/model-delete.ts`、Rust `models_delete`；頁面內確認，不用原生對話框）。

> 進度：引擎已實作（2026-11-06；`models.list` 的 `asr[].disk_bytes`、`models.delete {model}` → `{model, freed_bytes}`，錯誤碼 `unknown_model`／`model_not_downloaded`／`model_in_use`／`invalid_params`；細節見 `pipeline-engine.md`「models.delete」與 `asr.md`「磁碟用量與刪除」）。UI 另行實作。

- 引擎 `models.list` 加每個已下載模型的磁碟用量；新方法 `models.delete {model}`（只能刪 registry 內、
  不在使用中的 ASR 模型，刪前驗證路徑在模型快取內）。翻譯模型（Ollama）只顯示，不代刪。
- UI 模型頁顯示用量與「刪除」（確認對話）。

### F8 首次使用與診斷（UI）

> 進度：已實作（`app/src/lib/translation-guide.ts`、`app/src/lib/diagnostics.ts`；快速鍵 Ctrl+Alt+J 切換顯示翻譯、Ctrl+Alt+U 清除字幕）。

- 偵測不到翻譯伺服器時，設定頁與開始前提示「安裝 Ollama」（開官方下載頁）或「只顯示原文」。
- 快速鍵：新增「切換顯示翻譯」「清除字幕」（沿用既有全域快速鍵機制，衝突時只提示）。
- 「複製診斷資訊」：版本、OS、硬體偵測、設定（遮蔽 API key 環境變數名稱以外的秘密）、最近的引擎錯誤。

## 先不做（列入回報，需使用者決定）

| 項目 | 原因 |
|---|---|
| 振假名／點字查詞 | 需要日文形態素分析與辭典（例如 SudachiPy＋sudachidict，Apache-2.0，約 70 MB）＝新增依賴，依專案規則要先問 |
| 只擷取單一 App 的聲音 | Windows process loopback 需自寫 COM 介面（L、風險高）；macOS／Linux 各自不同 |
| 歌回模式（歌聲分類） | CED 等分類器授權未確認、缺歌回評測資料 |
| OBS 瀏覽器來源／網路字幕 | 要開本機網路埠，與「不開網路埠」的架構原則衝突，需 ADR |
| 說話者分離 | 現有方案多依賴 PyTorch |

## 驗證

- 每個功能的單元測試（TS 純函式、Rust 路徑驗證、Python LocalAgreement／metrics／models.delete）。
- 新字串進 13 個語系字典（型別保證齊全）。
- 端到端驗收加入：匯出四種格式、OBS 文字檔更新、穩定前綴顯示、音量表、刪除模型。
