# 07 多國語言與介面多語系

v0.2.0 把「日／英 → 繁中」擴成 20 種語言任意組合，介面也從只有繁中變成 13 種語系。
這一篇講三件事：**語言碼為什麼分兩層**、**一句話怎麼決定翻成什麼**、**介面文字怎麼保證每個語系都齊全**。

規格：`docs/specs/languages-i18n.md`。以下以程式碼為準。

---

## 1. 兩種「語言」：收聽／字幕語言與介面語言

先分清楚，這兩件事完全獨立：

| | 誰決定 | 清單在哪 | 幾種 | 存在設定的哪裡 |
|---|---|---|---|---|
| 收聽語言、字幕語言（內容的語言） | 引擎 | `src/kikitori/languages/catalog.py` | 20 | `source_language`、`target_language`、`partner_language` |
| 介面語言（按鈕、說明文字） | UI | `app/src/locales/index.ts` 的 `UI_LOCALES` | 13 | `ui.locale`（引擎不解讀） |

例如：用日文介面，聽韓語、看西班牙文字幕，完全可以。
阿拉伯語可以當收聽或字幕語言，但沒有阿拉伯語介面（介面不做由右到左的版面）。

---

## 2. 語言碼分兩層

### 問題

設定和字幕需要知道「繁體還是簡體」，但 Whisper 只認得「中文」：

- 使用者選字幕語言時，`zh-TW` 和 `zh-CN` 是兩個不同的選項（字形不同）。
- Whisper 辨識出來的語言碼是 `zh`，不分繁簡。

如果整個程式只有一種語言碼，就一定有一邊會錯：不是字幕不知道該用哪種字形，就是辨識語言對不上設定。

### 做法：每個語言同時有兩個碼

`catalog.py` 的 `Language`：

| 欄位 | 例子 | 用在哪 |
|---|---|---|
| `code`（清單語言碼） | `zh-TW`、`zh-CN`、`ja` | 設定、字幕事件的 `target_language`、翻譯的譯文語言 |
| `whisper_code`（Whisper 語言碼） | `zh`、`zh`、`ja` | ASR 的指定語言與偵測範圍、字幕事件的 `source_language`、翻譯的原文語言 |

兩個轉換函式刻意設計成「寬進嚴出」不同方向：

```python
normalize_language("zh-tw")   # → "zh-TW"（不分大小寫、底線視同連字號）
normalize_language("zh-Hant") # → "zh-TW"（舊別名）
normalize_language("zh")      # 丟 UnknownLanguageError：字幕語言必須決定繁簡
to_whisper_code("zh-TW")      # → "zh"
to_whisper_code("zh")         # → "zh"（ASR 的結果也接受）
```

- `normalize_language()` 拒絕 `zh`：設定或字幕語言如果是 `zh`，就不知道輸出要轉成哪種字形。
- `to_whisper_code()` 兩種都接受：翻譯請求的原文語言可能來自 ASR（`zh`），也可能來自設定（`zh-TW`）。

其他欄位也都放在同一個地方，避免各處各寫一份：

| 欄位 | 用途 |
|---|---|
| `english_name`、`native_name` | 提示詞（「Translate into Spanish (Español)」）、語言選單 |
| `no_space` | 中、日、泰文不用空白分詞 |
| `script_conversion` | 輸出字形：zh-TW 用 OpenCC `s2twp`、zh-CN 用 `t2s` |
| `rtl` | 阿拉伯語：字幕視窗與字幕紀錄用 `dir="auto"` |
| `asr_quality` | 每個 ASR 模型對這個語言的錯誤率等級（A ≤ 10%、B 10–20%、C > 20%） |

`asr_quality` 的資料集中在 `_QUALITY_ROWS` 一張表（每列一個模型、每欄一個語言），出處是 `research_notes/VTuber 直播模型選型調查/language_coverage.md`。
等級「寧可低估」：例如越南語在 FLEURS 是 8.6% 但在 Common Voice 15 是 39.8%，取較差的，記 C。

> 為什麼清單固定 20 種、不讓使用者輸入任意語言碼？每個語言都要有名稱、字形規則、幻覺片語表、品質等級。沒有資料的語言放進來，出錯時連原因都查不到。

### 自動偵測的範圍

`WHISPER_CODES` 是 20 種清單語言涵蓋的 Whisper 語言（19 個：繁簡合併成一個 `zh`）。
收聽語言選「自動偵測」時，Whisper 只在這 19 個裡面挑（`pipeline/factory.py` 的 `asr_languages()`）：

| 模式與設定 | ASR 怎麼做 |
|---|---|
| 影片翻譯，收聽語言是具體語言 | `forced_language` 固定，不做語言偵測（短句的偵測容易誤判） |
| 影片翻譯，`auto` | `candidate_languages = WHISPER_CODES` |
| 即時口譯 | `candidate_languages = (我的語言, 對方的語言)`：靠偵測分辨誰在說話 |

所以口譯模式下兩個語言的 Whisper 碼不能一樣（繁中配簡中不行），設定驗證會擋（`config/settings.py` 的 `check_interpret_languages()`，UI 的 `validateLanguageForm()` 也先檢查一次）。

---

## 3. 一句話翻成什麼：路由

辨識出一句話之後，`pipeline/languages.py` 的 `route()` 決定「譯文是什麼語言、要不要翻」。即時與完整兩條路徑共用它，所以同一句的草稿和定稿一定譯成同一種語言。

```text
route(mode, detected, target_language, partner_language, translation_enabled)
  │
  ├─ 譯文語言：口譯模式且 detected == 我的語言 → 對方的語言；其他 → target_language
  │
  ├─ translation_enabled = False        → "off"          不翻，譯文空字串
  ├─ detected 不在 WHISPER_CODES         → "unsupported"  不翻，送一次 warning
  ├─ detected == 譯文語言的 Whisper 碼    → "copy"         不翻，原文轉成譯文語言的字形
  └─ 其他                                → "translate"    呼叫翻譯模型
```

幾個例子：

| 設定 | 辨識出 | 結果 |
|---|---|---|
| 影片翻譯 ja → zh-TW | `ja` | translate：ja → zh-TW |
| 影片翻譯 zh-CN → zh-TW（聽簡中影片看繁中） | `zh` | copy：不呼叫模型，OpenCC 轉繁 |
| 口譯，我 = zh-TW、對方 = ko | `zh`（我說話） | translate：zh → ko |
| 口譯，同上 | `ko`（對方說話） | translate：ko → zh-TW |
| 任何設定，關閉「顯示翻譯」 | 任何 | off |

`copy` 的好處：繁簡互轉不用叫 LLM，沒有延遲也不會被翻錯。

### 原文也要轉字形

Whisper 的中文輸出通常是簡體。口譯時那是使用者自己說的話，顯示成簡體很奇怪。
`display_original()` 依序找設定裡第一個「與辨識語言同 Whisper 碼」的語言，用它的字形顯示原文：口譯看（我的語言, 對方的語言），影片翻譯看（收聽語言, 字幕語言）。
只有中文有轉換規則——日文的新字體（気、駅）不是簡體，不能動。

### 翻譯模型支援哪些組合

`translate/presets.py` 的每個預設組有 `languages`（支援的清單語言）與選用的 `pairs`（只支援特定方向）。
Qwen3.5 與 HY-MT1.5 支援全部 20 種；Sakura 只有 `ja → zh-TW／zh-CN`（`translate/languages.py` 的 `SAKURA_PAIRS`）。
推薦時（`hardware/recommend.py`）會跳過不支援這組方向的模型；都不支援時回 `translation_status = "unsupported"`。

### 幻覺片語也分語言

Whisper 在沒有人聲時會「編出」字幕套話，而且每種語言的套話不同（日文「ご視聴ありがとうございました」、英文「Thanks for watching!」）。
`asr/filtering.py` 的片語表依 Whisper 語言分表，每個語言再分兩種：

- **hard**：整句字幕套話。**不論辨識出什麼語言都套用**——Whisper 常把英文套話標成 `nl`、中文套話標成 `ja`。
- **soft**：真人也會說的短句（「Thank you.」「ありがとうございました」）。只比對辨識出的那個語言，而且要 no_speech 偏高才濾，避免誤殺別的語言的正常句子。

資料來自 `experiments/hallucination_lexicon/`：對 19 種語言強制指定語言、餵確定沒有人聲的 BGM、噪音、靜音，收集重複出現的輸出。

---

## 4. 推薦與訊息碼：引擎不寫中文句子

v0.1.0 的推薦說明是引擎組好的中文句子。介面變成 13 種語言後，引擎不可能知道要用哪一種，所以改成**訊息碼**：

```json
{"code": "asr_language_quality_low", "params": {"model": "large-v3-turbo", "language": "th"}, "message": "…英文備援…"}
```

- 引擎：`src/kikitori/hardware/messages.py` 的 `CATALOG` 定義每個 code 的參數名稱、型別與英文範本；`message(code, **params)` 建立時檢查參數。
- UI：`app/src/lib/messages.ts` 用字典鍵 `engine.<code>` 組成介面語系的句子；不認得的 code（引擎比 UI 新）就顯示英文 `message`，不會出現空白。
- 兩邊怎麼對齊：`app/dev/extract_message_codes.py` 用 `ast` 讀 `messages.py`（不執行專案程式），把 code 清單寫進 `app/src/lib/testdata/engine-message-codes.json`；`app/src/locales/engine-codes.test.ts` 檢查 13 個字典對每個 code 都有項目、佔位符與參數名稱一致。引擎加了新 code 卻忘了翻譯，測試會列出缺哪些。

語言名稱不放字典：UI 用瀏覽器的 `Intl.DisplayNames` 依介面語系產生（`i18n.ts` 的 `languageName()`），`zh-TW`／`zh-CN` 特別處理成「繁體／簡體中文」而不是「中文（台灣）」。

---

## 5. 介面多語系：型別保證齊全

### 不用 i18n 函式庫

全部在 `app/src/lib/i18n.ts`（約 290 行）：

| 函式 | 作用 |
|---|---|
| `t(key, params)` | 一般文字，`{name}` 佔位符 |
| `tp(key, count, params)` | 依數量變化的文字，用 `Intl.PluralRules` 選複數類別 |
| `setLocale(locale)` | 切換語系、更新 `<html lang>`，通知每個頁面重繪 |
| `applyStatic(root)` | 填 HTML 上的 `data-i18n`、`data-i18n-title`、`data-i18n-placeholder`、`data-i18n-aria-label` |
| `resolveLocale(navigator.languages)` | 「跟隨系統」時對到最接近的語系 |

查不到時的順序：目前語系 → English → zh-TW（`chain()`）。複數規則用「實際取到字的那個字典」的語系，避免退回英文時套用俄文的複數規則。

### 型別保證：缺一個鍵就編譯失敗

關鍵在 `app/src/locales/types.ts`：

```ts
import type { zhTW } from "./zh-TW";
type Base = typeof zhTW;
export type MessageKey = keyof Base;
export type Messages = { readonly [K in MessageKey]: Base[K] extends string ? string : PluralMessage };
```

- zh-TW 字典是**基準**：它有哪些鍵，`MessageKey` 就是哪些。
- 其他 12 個字典宣告成 `Messages`：少一個鍵、多一個鍵、或該是複數物件卻寫成字串，`tsc` 就報錯，`npm run build` 過不了。
- `t()` 只接受 `StringKey`、`tp()` 只接受 `PluralKey`，用錯函式也是型別錯誤。
- `locales/index.ts` 的 `DICTIONARIES` 型別是 `Record<UiLocale, Messages>`：列了語系卻沒給字典也是型別錯誤。

型別管不到的，交給測試：

| 測試 | 檢查 |
|---|---|
| `app/src/locales/locales.test.ts` | 13 個語系、沒有空字串、每個鍵的佔位符與基準相同、複數鍵在每個語系都有該語言需要的類別（例如俄文要 one／few／many／other） |
| `app/src/lib/i18n-source.test.ts` | 用 TypeScript parser 掃 `app/src` 與兩個 HTML：字串、樣板、正規表示式字面值裡不能有中日韓文字（註解可以；品牌字「聞き取り」例外） |
| `app/src/locales/engine-codes.test.ts` | 每個引擎訊息碼都有字典項目（見上一節） |
| `app/src/lib/i18n.test.ts` | 「跟隨系統」的對應、退回規則、佔位符 |

### 跟隨系統的對應規則

`resolveLocale()` 依 `navigator.languages` 的順序找第一個對得到的：

- 中文看文字與地區：`zh-Hant`、`zh-TW`、`zh-HK`、`zh-MO` → 繁體；其他（含單純的 `zh`）→ 簡體。
- 葡萄牙語一律 → `pt-BR`（字典用巴西用詞）。
- 其他只比對主語言（`en-GB` → `en`）。
- 都對不到 → English。

---

## 6. 改語言相關功能時去哪裡

| 想做的事 | 改哪裡 | 會擋你的測試 |
|---|---|---|
| 新增一個收聽／字幕語言 | `languages/catalog.py`（`LANGUAGES`、`_LANGUAGE_ORDER`、`_QUALITY_ROWS`）；`experiments/hallucination_lexicon/lexicon.json` 補片語並重新產生 `asr/hallucination_lexicon.py`；`pipeline/factory.py` 的預熱短句；翻譯預設組的 `languages` | `tests/test_languages.py`、`test_engine_languages.py`、`test_asr_hallucination_lexicon.py`、`test_hardware_recommend.py`（每個 registry 模型都要有品質等級） |
| 新增一個介面語系 | `app/src/locales/<code>.ts`（型別宣告成 `Messages`）、`index.ts` 的 `UI_LOCALES`／`NATIVE_NAMES`／`DICTIONARIES` | `tsc`、`locales.test.ts` |
| 新增一則介面文字 | 先加到 `zh-TW.ts`，其他 12 個字典照著補 | `tsc` 會列出缺的語系 |
| 引擎新增訊息碼 | `hardware/messages.py` 的 `CATALOG` → 跑 `app/dev/extract_message_codes.py` → 13 個字典加 `engine.<code>` | `engine-codes.test.ts` |
