# Spec: 多國語言（任意語言組合、介面多語系、只顯示原文）

> 狀態：規格（2026-11-05）。使用者已授權依建議實作，不逐步確認；最後由使用者驗收。
> 上層約定：`docs/specs/app-architecture.md`、`docs/specs/pipeline-engine.md`。本文件修改：`translate.md`（語言組合、提示詞）、`hardware.md`（推薦依語言）、`ui.md`（介面文字、語言選單、字幕顯示）、`pipeline-engine.md`（設定檔 v3、協定新增欄位）。

## Objective

使用者對「聽什麼語言」「字幕要什麼語言」「介面用什麼語言」各有想法，v0.1.0 只支援
日／英 → 繁中與中文 ↔ 日／英。這次要讓：

1. **任意語言組合**：收聽語言（原文）與字幕語言（譯文）都能從「各國主流語言」清單任選。
   每個語言組合都有推薦的辨識與翻譯模型；**選了不會立刻下載**，第一次真的開始字幕時
   才下載缺少的模型（顯示進度，下載完自動開始）。
2. **介面多語系**：整個介面（主視窗、字幕視窗、通知、引擎傳來的推薦說明與錯誤）都能切換語系；
   預設跟隨作業系統。
3. **只顯示原文**：字幕可以關掉譯文，只看原文（例如學語言的人）。關掉時引擎完全不翻譯，
   不需要翻譯伺服器、不佔 GPU；執行中也能即時切換。

成功的樣子：西班牙語使用者在西文介面選「韓語 → 西班牙語」，按開始後自動下載需要的模型、
開始顯示字幕；日語學習者選「日語、只顯示原文」，不裝 Ollama 也能用。

## 前提假設

1. 語言清單固定 20 種（見下表），不開放任意輸入語言碼：每個語言都要有名稱表、轉換規則、
   幻覺清單與推薦依據，無法驗證的語言不列。
2. 介面語系 13 種（見「介面多語系」），不含右到左（阿拉伯語）的版面；阿拉伯語仍可當收聽／字幕語言。
3. 中文分「繁體中文（台灣）zh-TW」「簡體中文 zh-CN」兩個字幕語言；辨識時兩者都是 Whisper 的 `zh`。
4. 介面翻譯由本專案撰寫並以測試檢查完整性（每個語系的鍵與參數都齊全）；不引入 i18n 函式庫。
5. 翻譯伺服器若是 Ollama，app 可以替使用者下載（`/api/pull`）翻譯模型；其他 OpenAI 相容伺服器
   只能提示使用者自行安裝。

## 語言清單

`src/kikitori/languages/catalog.py` 是唯一來源；UI 透過 `languages.list` 取得，不自己維護一份。

| code | 英文名 | 原生名 | Whisper | 備註 |
|---|---|---|---|---|
| ja | Japanese | 日本語 | ja | 無空白 |
| en | English | English | en | |
| zh-TW | Chinese (Traditional, Taiwan) | 繁體中文 | zh | 輸出以 OpenCC `s2twp` 轉台灣正體；無空白 |
| zh-CN | Chinese (Simplified) | 简体中文 | zh | 輸出以 OpenCC `t2s` 轉簡體；無空白 |
| ko | Korean | 한국어 | ko | |
| es | Spanish | Español | es | |
| fr | French | Français | fr | |
| de | German | Deutsch | de | |
| pt | Portuguese | Português | pt | |
| ru | Russian | Русский | ru | |
| it | Italian | Italiano | it | |
| ar | Arabic | العربية | ar | 右到左；字幕視窗以 `dir="auto"` 顯示 |
| hi | Hindi | हिन्दी | hi | |
| id | Indonesian | Bahasa Indonesia | id | |
| vi | Vietnamese | Tiếng Việt | vi | |
| th | Thai | ไทย | th | 無空白 |
| tr | Turkish | Türkçe | tr | |
| pl | Polish | Polski | pl | |
| nl | Dutch | Nederlands | nl | |
| uk | Ukrainian | Українська | uk | |

每個語言另記：
- `asr_quality`：Whisper large 系列在 FLEURS 的錯誤率等級（A ≤ 10%、B 10–20%、C > 20%），出處與數字見
  `research_notes/VTuber 直播模型選型調查/language_coverage.md`。C 級語言在推薦說明中提示「辨識品質較低」。
- 舊的語言碼別名（`zh-hant`、`zh_TW`、`zh-hans` 等）正規化到上表的 code。

## 設定檔 v3

| 欄位 | v2 | v3 | 說明 |
|---|---|---|---|
| `source_language` | 無 | `"ja"` | 影片翻譯模式的收聽語言；`"auto"` = 自動偵測（限清單內語言） |
| `target_language` | 只能 `zh-TW` | 清單內任一語言 | 影片翻譯的字幕語言；即時口譯時是「我的語言」 |
| `partner_language` | `ja`／`en` | 清單內任一語言 | 即時口譯的對方語言；口譯模式下 Whisper 語言不能和 `target_language` 相同（影片翻譯不檢查） |
| `translation.enabled` | 無 | `true` | `false` = 只顯示原文 |
| `ui.locale` | 無 | `"system"` | 介面語系（`ui` 是 UI 自己的資料，引擎不解讀） |

- 遷移 v2 → v3：補 `source_language: "ja"`、`translation.enabled: true`；其他欄位原樣保留。
- `source_language == target_language`（或兩者 Whisper 語言相同，例如 zh-CN → zh-TW）時不呼叫翻譯模型，
  只做字形轉換（繁↔簡）或原樣顯示。

## 引擎行為

### 辨識語言

- 影片翻譯、`source_language` 為具體語言：`transcribe(language=…)` 直接指定，不做語言偵測（避免短句誤判）。
- 影片翻譯、`"auto"`：偵測範圍限清單內 20 種語言的 Whisper 語言。
- 即時口譯：偵測範圍限 `{target_language, partner_language}` 的 Whisper 語言（現有 candidate_languages 機制）。

### 翻譯路由

| 模式 | 辨識出的語言 | 動作 |
|---|---|---|
| 影片翻譯 | 任何 | → `target_language` |
| 即時口譯 | 對方語言 | → `target_language` |
| 即時口譯 | 我的語言 | → `partner_language` |
| 任何 | Whisper 語言與目標相同 | 不翻譯；目標是 zh-TW／zh-CN 時做字形轉換 |
| 任何 | `translation.enabled=false` | 不翻譯（`translation` 欄位為空字串） |

### 翻譯模型與提示詞

- `ModelPreset.language_pairs` 改為 `languages`（支援的語言集合）＋選用的 `pairs`（只支援特定方向的模型，例如 Sakura 只有 ja → zh-TW／zh-CN）。
- `generic` 提示詞：zh-TW、zh-CN、ja、en 維持「指示用譯文語言寫」（v0.1.0 實測結論）；其他目標語言用英文指示並明確寫出目標語言的英文名與原生名，實測確認輸出語言正確（見「驗證」）。
- `hunyuan_mt`：中文相關用官方 ZH⇔XX 範本，其他組合用官方 XX⇔XX 範本；語言名稱依官方支援清單。
- 後處理：目標 zh-TW → `s2twp`（沿用選擇性轉換）；目標 zh-CN → `t2s`。
- 幻覺過濾：已知幻覺片語依語言分表，每個語言分 `hard`（整句字幕套話，不論辨識語言都套用：Whisper 常把它們標成別的語言）
  與 `soft`（真人也會說的短句，只對辨識出的語言套用且 no_speech 偏高才濾，避免把其他語言的正常句子濾掉）；
  各語言片語由 `experiments/hallucination_lexicon/` 在非語音素材上實測收集，英文另參考
  Barański et al.（ICASSP 2025）的 bag of hallucinations。

### 只顯示原文

- `translation.enabled=false` 時：不建立預熱與保溫、不呼叫翻譯伺服器；啟動 session 不檢查翻譯模型。
- 新方法 `session.set_translation {enabled}`：執行中切換；之後的句子依新狀態處理，已顯示的句子不補翻。
  開啟時若翻譯伺服器不可用，沿用現有「翻譯失敗只顯示原文＋提醒一次」的行為。
- 字幕視窗：只顯示原文時，原文改用譯文的字級與顏色（主要字幕），不留空白的第二行。

### 推薦與延後下載

- `hardware.recommend` 增加語言參數（影片：`source_language` → `target_language`；口譯：兩個方向）：
  - ASR：依硬體等級挑模型（現有邏輯）；收聽語言是 C 級時加提示；只支援單一語言的模型（例如日語特化模型）只在該語言被推薦。
  - 翻譯：在支援該語言組合的預設組中，依 VRAM 推薦順序挑第一個；都不支援時回傳 `translation_unsupported`。
- 推薦說明、警告、組合驗證的錯誤全部改成 `{code, params}`（不再是中文句子），UI 依語系組字；
  引擎另附英文 `message` 供紀錄檔與 CLI。
- 新方法 `models.pull_translation {model}`：翻譯伺服器是 Ollama 時以 `POST /api/pull` 下載，進度沿用
  `download_progress` 事件（多 `kind: "translation"`）；不是 Ollama 時回傳錯誤 `pull_unsupported`。
- 新方法 `languages.list`：回傳語言清單（code、英文名、原生名、Whisper 語言、asr_quality）。
- 新方法 `settings.preview_languages {source_language?, target_language?, partner_language?}`：
  回傳該語言組合的推薦組合、每個模型是否已安裝、需下載的大小；**不寫設定、不下載**。

## UI

### 語言選單（設定頁「語言」區塊）

- 收聽語言（影片翻譯）／我的語言＋對方語言（即時口譯）／字幕語言：下拉選單，選項顯示「原生名（介面語系的名稱）」，
  依常用程度排序並可輸入篩選。
- 每次選擇都呼叫 `settings.preview_languages`，在選單下方顯示「推薦組合」：辨識模型、翻譯模型，以及
  「已安裝」或「第一次使用時下載 約 X GB」。不支援的組合直接說明原因。
- 儲存語言時一併套用推薦的模型（預設勾選「同時改用推薦的模型」，可取消）。
- 開始字幕時若有缺少的模型：顯示下載進度（辨識模型＋翻譯模型），全部完成後自動開始；任一失敗則停在錯誤訊息並可重試。

### 介面多語系

- 語系：繁體中文（zh-TW）、简体中文（zh-CN）、English、日本語、한국어、Español、Français、Deutsch、Português（pt-BR 用詞）、Русский、Tiếng Việt、ไทย、Bahasa Indonesia。
- 設定頁頂端「介面語言」：預設「跟隨系統」（`navigator.language` 對應到最接近的語系，對不到用 English）。
- 機制：`app/src/lib/i18n.ts`（`t(key, params)`、`setLocale()`、`applyStatic(root)`）；字典在
  `app/src/locales/<code>.ts`，以 zh-TW 為基準型別，TypeScript 型別檢查保證每個語系鍵齊全；測試另檢查參數佔位符一致、沒有空字串。
- 靜態 HTML 用 `data-i18n`（文字）、`data-i18n-title`、`data-i18n-placeholder`、`data-i18n-aria-label`；切換語系時重繪目前頁面、更新 `<html lang>`、字幕視窗標題。
- 引擎的 `{code, params}` 在 UI 端對應到字典；未知 code 顯示引擎附的英文 `message`。

### 只顯示原文

- 字幕紀錄頁工具列與設定頁「字幕」區塊各有「顯示翻譯」開關（同一個設定 `translation.enabled`）；執行中切換即時生效。
- 字幕視窗收到設定後，原文提升為主要字幕樣式。

## Tech Stack

不新增依賴。Python 3.12（stdlib `http.client` 做 Ollama pull）、OpenCC 1.4.2（已有）、TypeScript／Vitest、Rust（只改字幕視窗樣式欄位）。

## Commands

```text
uv run python -m unittest discover -s tests
uv run ruff check
cd app && npm test && npm run build && npx tsc --noEmit
cd app/src-tauri && cargo test && cargo clippy -- -D warnings
```

## Project Structure

```text
src/kikitori/languages/         語言清單、正規化、字形轉換對應
src/kikitori/translate/         語言組合驗證、提示詞、後處理（改用語言清單）
src/kikitori/asr/filtering.py   依語言分表的幻覺片語
src/kikitori/hardware/          推薦加入語言、訊息改為 code
src/kikitori/engine/            新方法：languages.list、settings.preview_languages、models.pull_translation、session.set_translation
app/src/lib/i18n.ts             介面多語系
app/src/locales/                13 個語系字典
experiments/hallucination_lexicon/  各語言非語音幻覺片語的實測
```

## Testing Strategy

- Python unittest：語言正規化與別名、設定遷移 v2→v3、各模式路由（含同語言、繁簡轉換、關閉翻譯）、提示詞（每個目標語言都能產生、不 KeyError）、OpenCC 規則、依語言的幻覺過濾（他語言的同字句不被濾）、推薦（各語言組合、不支援組合、C 級提示、`{code, params}` 形狀）、`preview_languages` 不寫設定、Ollama pull（假伺服器：成功、進度、錯誤、逾時、非 Ollama）、`session.set_translation`（執行中切換）。
- Vitest：i18n（13 語系鍵與佔位符一致、未知 code 退回 message、跟隨系統的對應）、語言選單的預覽顯示、只顯示原文的字幕狀態。
- Rust：字幕視窗樣式新欄位的驗證。
- 實測：`experiments/translate_benchmark/` 加入非 CJK 目標語言的輸出語言檢查；GUI 端到端驗收加入「切換介面語系」「韓語 → 西班牙語延後下載」「只顯示原文、不開 Ollama」三個情境。

## Boundaries

- Always：語言相關資料只放在 `languages/catalog.py`；所有介面文字走字典；外部輸入（語言碼、模型名稱、Ollama 回應）先驗證；網路請求設 timeout。
- Ask first：新增依賴（本規格不需要）。
- Never：自動下載使用者沒有開始使用的模型；把翻譯字典或語言清單寫死在多處。

## Success Criteria

1. 20 種語言任選收聽／字幕語言，設定可存可讀，舊設定檔自動遷移。
2. 選語言只顯示推薦與下載大小，不下載；開始字幕時才下載缺少的辨識與翻譯模型（Ollama），完成後自動開始。
3. 介面 13 種語系，切換後主視窗、字幕視窗、推薦說明、錯誤訊息都換成該語系；沒有殘留的寫死中文（以測試掃描 `app/src` 與 `app/index.html`）。
4. 關閉「顯示翻譯」：字幕只顯示原文且原文為主要樣式；引擎不呼叫翻譯伺服器（測試以假伺服器計數驗證）；不開 Ollama 也能開始。
5. 全部測試、lint、型別檢查、CI 通過；GUI 端到端驗收三個新情境通過。

## Open Questions

無（依使用者授權採用本文件的建議）。
