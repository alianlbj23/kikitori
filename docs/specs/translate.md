# Spec: translate（翻譯模組）

> 狀態：已採用（2026-11-05，使用者授權自主實作）。
> 上層約定：`docs/specs/app-architecture.md` 的 `translate` 一節；本文件不得與之矛盾。
> 實測結果與各項決定的依據：`experiments/translate_benchmark/README.md`。
> Milestone 16（日語直播特化，`docs/specs/vtuber-profile.md`）：Qwen3.5／HY-MT 溫度改 0、內建直播術語表與使用者術語表、
> 前文保險（`ContextFallbackTranslator`）、特殊 token 碎片清除與選擇性繁簡轉換只轉簡體片段；依據 `experiments/translate_eval/README.md`。

## 前提假設

1. 對外介面完全照架構規格：`TranslationRequest`（凍結 dataclass）與 `Translator`（`typing.Protocol`），放在 `src/kikitori/translate/types.py`。
2. 翻譯後端是 OpenAI 相容的 Chat Completions API（`POST {base_url}/chat/completions`）；預設 `base_url = http://localhost:11434/v1`（本機 Ollama）。也能接其他相容服務（llama.cpp server、vLLM、LM Studio、雲端服務）。
3. API 為同步呼叫；執行緒安排留給 pipeline。翻譯器不保存跨請求的狀態（前文由呼叫端放在 `context`），同一個實例可以被多個執行緒同時使用。
4. 模型權重由使用者自行安裝（例如 `ollama pull`）；本專案只以名稱引用，不散布權重。仍記錄每個模型的授權並標出限制。
5. HTTP 只用標準庫（`http.client` + `json`）：只需要一個 POST 端點與 SSE 解析，而且標準庫能分開設定連線與讀取 timeout（`urllib` 只有一個共用 timeout），不值得為此加依賴。

## Objective

把一句原文翻成字幕用的譯文：短、自然、忠實、不解釋、不加引號。語言來自語言清單
（`src/kikitori/languages/catalog.py`，20 種，見 `languages-i18n.md`）：

- 原文語言是 Whisper 語言碼（中文為 `zh`），也接受清單語言碼（`zh-TW`、`zh-CN` 視同 `zh`）；請求驗證後一律換成 Whisper 語言碼。
- 譯文語言是清單語言碼，必須分繁簡：`zh` 不能當譯文語言。不分大小寫、底線視同連字號，`zh-Hant`→`zh-TW`、`zh-Hans`→`zh-CN`。
- 清單內任兩個 **Whisper 語言不同** 的語言之間都可以翻（再受模型預設組的 `languages`／`pairs` 限制）。Whisper 語言相同（例如 `zh` → `zh-TW`、`ja` → `ja`）不送翻譯模型，由 pipeline 只做字形轉換。
- 目標為 `zh-TW` 時輸出繁體中文、台灣用語；目標為 `zh-CN` 時輸出簡體中文。
- 清單外的語言、不分繁簡的譯文、同語言或模型不支援的組合一律以 `InvalidRequestError`（同時是 `ValueError`）拒絕，不送出請求。

## 介面

### 合約（照抄架構規格）

```python
@dataclass(frozen=True)
class TranslationRequest:
    text: str
    source_language: str
    target_language: str = "zh-TW"
    context: tuple[str, ...] = ()   # 前幾句原文，提升連貫性

class Translator(Protocol):
    def translate(self, request: TranslationRequest) -> str: ...
    def stream(self, request: TranslationRequest) -> Iterator[str]: ...
```

### `stream()` 的語意

架構規格寫「逐段回傳累積中的譯文片段」。本模組定義為：**每次產出的是「到目前為止的完整譯文」**（累積值，不是增量）。理由：

- 字幕視窗每次都整行取代，呼叫端不必自己串接。
- 後處理（去掉 `<think>`、去引號、簡轉繁的詞彙轉換）可能改到前面已產出的字，只有累積值能保證每次產出都正確。

只有在清理後的譯文非空且與上次不同時才產出。請求驗證在呼叫 `stream()` 時立即進行（不合法立刻丟例外）；網路請求在第一次迭代時才送出。

### 實作：`OpenAICompatibleTranslator`

```python
@dataclass(frozen=True)
class TranslatorConfig:
    model: str
    base_url: str = "http://localhost:11434/v1"
    api_key_env: str = "KIKITORI_TRANSLATE_API_KEY"
    temperature: float | None = None     # None = 用預設組（preset）的建議值
    connect_timeout: float = 3.0         # 秒
    read_timeout: float = 30.0           # 秒；送出請求後每次 socket 讀取的上限
    max_retries: int = 2                 # 暫時性錯誤的重試次數（不含第一次）
    retry_backoff: float = 0.5           # 秒；指數退避的基數
    max_tokens: int = 256                # 防止模型失控輸出長文
    max_context_lines: int = 3           # 只取最後幾句前文
    convert_script: bool = True          # 依譯文語言做字形轉換（見「後處理」）

translator = OpenAICompatibleTranslator(
    TranslatorConfig(model="qwen3.5:9b"),
    glossary=Glossary(),                 # 選用：術語表（見「術語表」）
)
translator.translate(TranslationRequest("おはよう", "ja"))
```

- 建構時驗證設定（型別、範圍、`base_url` 必須是 http/https、有主機、沒有 query；`api_key_env` 必須是合法環境變數名稱），不合法丟 `ValueError`／`TypeError`。錯誤訊息與 `repr()` 中的 `base_url` 經 `redact_url()` 遮蔽帳密（`http://user:pass@host` → `http://***@host`）：帳密不使用（金鑰只從環境變數讀），而訊息會回到 UI 與 log。
- 提示詞格式與取樣參數由 `presets.py` 的 `ModelPreset` 決定：已知模型用專屬預設組（模型名稱不帶 tag 時自動補 `:latest` 再查），未知模型用通用預設組（`generic`）。也可在建構時明確傳入 `preset=`。
- **`localhost` 先連 IPv4**：Windows 上 `localhost` 先解析成 `::1`，而 Ollama 預設只聽 `127.0.0.1`；被拒的 IPv6 連線在 Windows 要約 2 秒才失敗，實測**每個請求**都因此多等 2.06 秒。所以 `http://localhost` 依序試 `127.0.0.1`、`::1`，`Host` 標頭仍寫 `localhost:<port>`。HTTPS 不改寫（避免憑證主機名稱比對失敗）。

## 請求內容

```json
{
  "model": "<model>",
  "messages": [...],
  "stream": true | false,
  "temperature": 0,
  "top_p": 0.8,
  "presence_penalty": 1.5,
  "max_tokens": 256,
  "reasoning_effort": "none"
}
```

- `top_p`、`presence_penalty`、`reasoning_effort` 只在預設組有設定時才送。OpenAI 相容 API 沒有 `top_k`／`repetition_penalty`，由 Ollama 模型檔的預設值負責（HY-MT 的 Ollama 模型檔已設 `top_k 20`、`repeat_penalty 1.05`）。
- **關閉思考（Qwen3.5）**：Qwen3.5 預設開啟思考模式（官方 model card：*"Qwen3.5 models operate in thinking mode by default"*），且官方不支援 Qwen3 的 `/think`、`/nothink` 軟開關。Ollama 的 OpenAI 相容 API 以 `reasoning_effort` 控制思考，對「只支援開／關」的模型，`"none"` 代表關閉（Ollama 文件 *OpenAI compatibility*：*"Boolean-only models ... `"none"` requests `false`"*）。本機 `/api/show` 回報 `qwen3.5` 的 `thinking = {values: [false, true], default: true}`，屬於這一類。實測關閉後首字延遲 0.1 秒、沒有推理內容；不關時串流先送出大量 `delta.reasoning`。
- 送給非 Ollama 服務時，若對方不認得 `reasoning_effort` 而回 400，改用自訂預設組（不帶此欄位）。

## 提示詞

三種格式（`PromptStyle`）：

### `hunyuan_mt`（Tencent HY-MT1.5）

依官方 model card（[tencent/HY-MT1.5-7B](https://huggingface.co/tencent/HY-MT1.5-7B)）的範本，**不使用 system prompt**（*"our model does not have the default system_prompt"*），只送一則 user 訊息。原文是中文或譯文是 `zh-TW`／`zh-CN` 時用 ZH⇔XX 範本，目標語言名稱用語言表的中文名稱（`繁体中文`＝zh-Hant、`中文`、`日语`、`韩语`…）；其他組合用 XX⇔XX 範本，名稱用語言表的英文名稱（`Korean`、`Spanish`…）。model card 的 38 種語言涵蓋語言清單的全部 20 種。

```text
将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：

{source_text}
```

```text
Translate the following segment into {target_language}, without additional explanation.

{source_text}
```

**術語干預**（Milestone 16）：這句有術語時改用官方的術語干預範本（model card「Prompt Template for terminology
intervention」，2026-11-06 查閱）。官方範本一次一條、只有中文版，所以任何語言組合都用它與中文語言名，多條時逐行列出：

```text
参考下面的翻译：
{source_term} 翻译成 {target_term}
{source_term} 翻译成 {target_term}

将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：
{source_text}
```

官方另有 contextual 範本（`{context}\n参考上面的信息，把下面的文本翻译成{target_language}，注意不需要翻译上文，也不要额外解释：\n{source_text}`），`prompts.py` 有實作（官方只有這個中文版，任何組合帶前文時都用它），但兩個 HY-MT 預設組都**關閉前文**（`supports_context=False`）：實測 1.8B 有 11/12 次把前文或指令一起輸出，7B 在實測句組中也會把前一句一起翻出來。

### `sakura`（SakuraLLM Sakura-GalTransl-7B-v3）

依官方 model card 的 v3 範本（system prompt 原文照用，含「翻译成简体中文」）。只支援 `ja → zh-TW`、`ja → zh-CN`；模型輸出簡體，zh-TW 靠 OpenCC 轉成繁體。

- 官方的 `[History]` 欄位是「上一輪的**譯文**」，而本模組的 `context` 是前文**原文**，格式不合，因此不使用前文。
- 術語表（`[Glossary]`，Milestone 16）：官方的 GPT 字典格式，每行 `src->dst`（`#备注` 選用，本模組不用），放在
  「参考以下术语表（可为空，格式为src->dst #备注）：」與「根据以上术语表…」之間；沒有術語時是空行，與先前完全相同。
  譯法照譯文語言給（zh-TW 時是繁體），模型照抄後由後處理的選擇性繁簡轉換保留。

### `generic`（Qwen3.5 與未知模型）

system 訊息寫規則、user 訊息放前文與待翻句子。目標 `zh-TW`、`zh-CN`、`ja`、`en` 的**指示用譯文語言撰寫**：實測英文指示下 `qwen3.5:9b` 翻日文時 11/18 次直接輸出日文（沒翻），改用繁體中文指示後 0/18。其他 16 種目標語言用英文指示，並同時寫出目標語言的英文名與原生名（例如 `Korean (한국어)`）、要求只輸出該語言；輸出語言是否正確由 `experiments/translate_benchmark/` 實測確認。`{source}` 是原文語言在指示語言中的名稱（19 個 Whisper 語言都有）。
例外：**中文 → 日文改用中文寫指示**（`_PAIR_TEMPLATES`，優先於依譯文語言的範本）。中文與日文共用漢字，用日文寫指示時模型會用日文的讀法斷詞、理解同形詞：「我今天要吃豬排便當」被 qwen3.5:9b／4b、HY-MT 7B／1.8B 全部譯成「豚の排泄物」（豬｜排便｜當），「我娘」譯成「娘」、「愛面子」譯成「顔を立てる」。`experiments/kanji_false_friends/` 的陷阱句（temperature 0）：中文指示讓 qwen3.5:9b 16/24 → 22/24（不含內建詞表；加上中日同形異義詞表 24/24），一般句子 0 句沒翻譯。日翻中反過來用日文寫指示沒有改善、還多一句沒翻，所以只有這個方向。

目標 `zh-TW`（`{source}` 例如「日文」「韓文」）：

```text
[system]
你是字幕翻譯員，把使用者給的{source}句子翻譯成繁體中文（台灣用語）。
規則：
- 只輸出一行譯文。
- 簡短、自然、忠於原意與語氣。
- 不要解釋、註解、引號或拼音，不要保留原文。
- 前文只供參考，不要翻譯前文。
- 一律使用繁體中文與台灣用語，不要用簡體字。

[user]
前文（只供參考，不要翻譯）：
{context}

請翻譯成繁體中文：
{source_text}
```

目標 `zh-CN`、`ja`、`en` 用同樣結構的簡體中文／日文／英文版本，其他目標用英文版本加上目標語言名稱（見 `prompts.py`）。沒有前文時省略前文段落。

**術語**（Milestone 16）：這句有術語時，在前文之後、指示之前加一段「原文 → 譯法」（WMT 術語任務的做法），標題用指示的語言：
zh-TW「術語（原文出現時請用這些譯法）：」、zh-CN「术语（原文出现时请用这些译法）：」、ja「用語（原文に出てきたらこの訳を使う）：」、
en 與其他目標「Terminology (use these translations when the terms appear):」。

```text
術語（原文出現時請用這些譯法）：
赤スパ → 紅色超級留言
推し → 推

請翻譯成繁體中文：
赤スパありがとう！推しが尊い
```

沒有術語時提示詞與先前完全相同。

### 前文處理

- 只取 `context` 最後 `max_context_lines`（預設 3）句；空白字串略過；預設組 `supports_context=False` 時不放前文。
- 前文只當參考，提示詞明確要求不要翻譯。
- **前文保險**（Milestone 16，`context_guard.py` 的 `ContextFallbackTranslator`，pipeline 的 factory 包在兩條路徑的翻譯器外面）：
  帶前文的譯文看起來把前文也翻出來時，改**不帶前文重翻一次**（只重翻一次，結果照用）。判斷（`looks_like_context_leak()`）：
  - 長度：譯文字數 > 原文字數 × 3 **且** > 40 字。原文是中日韓（ja、zh、ko）、譯文是拼音文字時倍數用 8（日文一字常對應好幾個英文
    字母；translate_eval 的「過長」對英文也用 8 倍），否則正常的英譯會被誤判。
  - 抄出前文：任一句 ≥ 6 字的前文原句（NFKC 正規化後）完整出現在譯文裡。
  - `stream()`：每次累積的譯文都檢查，一符合任一條件（過長，或已完整抄出某句前文）就關掉原本的串流、改送不帶前文的請求（字幕不必等錯的譯文說完；異常的那一段不產出）。請求驗證與被包住的翻譯器一樣在呼叫 `stream()` 時進行。
  - 依據：translate_eval 中前文讓 ASR 片段的 COMET 顯著提高（9B +0.015、4B +0.032），所以前文要留；代價是 4B 在 social 的標籤行上
    3–4/311 次把前兩句的譯文也輸出。log 只記長度，不記原文與譯文。

## 後處理

依序：

1. **去掉思考內容**：移除 `<think>…</think>`（也接受 `<thinking>`，不分大小寫）；只有開頭標籤而尚未結束（串流中）時，從標籤起全部丟掉；只出現結束標籤（聊天範本已預先放入開頭標籤）時，丟掉結束標籤之前的內容；結尾是不完整的開頭標籤（例如串流剛好切在 `<thi`）時先藏起來。OpenAI 相容串流中另外放在 `delta.reasoning`／`reasoning_content` 的推理內容一律不讀。
2. **整理字幕**：移除漏出來的特殊 token：`<|` 在一般字幕裡不會出現，所以只要看到它就當成特殊 token 的開頭，碎片本身到第一個 `>`、
   換行、下一個 `<` 或文字結尾為止（最多 40 字），涵蓋完整的 `<|eos|>`、`<|extra_0|>` 與沒有收尾的 `<|/code>`、`<|extra_0>`、
   `<|begin_quote>`、`<| recreation |`（translate_eval：HY-MT 1.8B 在 zh_social 6–12/311 句留下這種碎片）。開頭的 token 只去掉
   token 本身；其他位置的 token **連同之後的文字一起丟掉**：模型已越過回合結尾，錯誤清單裡 token 之後幾乎都是同一句再輸出一次
   （`…嗎？<|begin_quote>…嗎？`）或憑空續寫的段落。`a < b`、`a|b` 這類一般文字不受影響；去頭尾空白；去掉開頭的「譯文：」「翻譯：」「翻訳：」「번역:」「Translation:」「Traducción:」「Перевод:」等「標籤＋冒號」；整句被同一對引號（`"…"`、`「…」`、`『…』`、`“…”` 等）包住、且內部沒有同種引號時去掉外層引號。
3. **字形轉換**（`convert_script=True`，依語言清單的 `script_conversion`）：目標 `zh-CN` 整句以 OpenCC `t2s` 轉簡體；目標 `zh-TW` 做以下選擇性簡轉繁：
   - 先找「簡體專用字」：OpenCC `s2tw` 會改掉、且不在 Big5 字集內的漢字，另加 `么于网扰`（在 Big5 內但台灣文字幾乎只以簡體身分出現）。Big5 用來排除「才、吃、伙、台、里、后」這類兩岸通用、OpenCC 卻會改寫的字。
   - **有簡體專用字才轉**，用 OpenCC `s2twp`（台灣正體＋台灣慣用詞，例如 视频→影片、软件→軟體）。沒有就原樣回傳：實測 `s2twp` 會把正確的「那個傢伙」改成「那個傢夥」、「台灣」改成「臺灣」。
   - **整句都是簡體**（沒有任何「繁體專用字」＝ OpenCC `t2s` 會改掉的漢字，例如 Sakura 的輸出）：整句 `s2twp`（維持先前行為）。
   - **繁體為主、只漏出幾個簡體字**（Milestone 16；translate_eval：HY-MT 7B 的 zh-TW 原始輸出有 4–14% 是這種句子）：只轉簡體專用字
     附近的片段——每個簡體專用字加上兩側各一個相鄰漢字（給 OpenCC 詞組比對，例如「后来」「干净」「头发」），重疊的片段合併；
     片段先 `t2s` 再 `s2twp`（「頭发」→「头发」→「頭髮」），片段裡原本就是「台」「伙」「吃」的字保留原字。句中其他字維持原樣，
     不再產生「露臺」「傢夥」「好喫」。
   - OpenCC 的 Python 綁定沒有說明執行緒安全性，轉換時加鎖。

清理後為空字串時丟 `TranslationResponseError`：模型只輸出思考或空白屬於異常，交給呼叫端決定是否略過。

## 錯誤處理

| 例外 | 時機 | 會重試 |
|---|---|---|
| `InvalidRequestError`（同時是 `ValueError`） | 空字串、過長（> 2000 字）、不支援的語言碼或組合、`context` 不是字串 tuple | 否（不送請求） |
| `TranslationConnectionError` | 無法連線、連線逾時、連線中途中斷 | 是 |
| `TranslationHTTPError(status)` | HTTP 4xx／5xx | 只有 408、429、500、502、503、504 |
| `TranslationTimeoutError` | 已送出請求後讀取逾時 | 否：即時字幕重試只會更晚 |
| `TranslationResponseError` | 非 JSON、非 UTF-8、缺欄位、串流內含 `error`、串流沒有 `[DONE]` 也沒有 `finish_reason` 就結束、清理後為空、回應過大（> 1 MB） | 否 |
| `TranslationError` | 金鑰環境變數含控制字元或非 ASCII | 否 |

- 全部繼承 `TranslationError`；HTTP 錯誤訊息帶狀態碼與伺服器訊息（優先取 `error.message`，截斷到 300 字，並把 API key 字串遮蔽成 `***`）。
- 重試：指數退避 `retry_backoff × 2^n`，上限 8 秒；有 `Retry-After`（秒數）時取兩者較大值，同樣不超過 8 秒。
- 串流：只有在**還沒產出任何片段前**（建立連線、等回應標頭）才會重試；一旦開始產出，之後的錯誤直接往上丟。非串流同理：只在拿到回應標頭前重試。
- `finish_reason == "length"`（被 `max_tokens` 截斷）時照常回傳已有內容並記一筆 warning。
- 重試記錄用 `logging` 的 warning，只含狀態碼或例外類別、次數、等待秒數；不含標頭、回應內容與金鑰。
- 實測 Ollama 0.40.14 在模型陷入重複輸出（例如一直輸出 `w`）時，偶爾以一筆 `model:""` 的空片段結束串流、沒有 `finish_reason` 也沒有 `[DONE]`；本模組視為 `TranslationResponseError`（這種輸出本來就不能用）。

## API key

- 只從環境變數讀；設定只存變數名稱（預設 `KIKITORI_TRANSLATE_API_KEY`）。
- 每次請求時才讀；有值才送 `Authorization: Bearer <key>`，沒值或只有空白就不送（Ollama 不需要）。
- 金鑰含換行等控制字元或非 ASCII 時丟 `TranslationError`（訊息不含金鑰），避免標頭注入。
- 不存進物件屬性、不出現在 `repr`、log 或例外訊息。

## 模型預設組與授權

| 預設組（Ollama 名稱） | 格式 | 語言組合 | 前文 | 取樣參數（來源） | 權重 | 授權 |
|---|---|---|---|---|---|---|
| `qwen3.5:9b`（Q4_K_M） | `generic`，`reasoning_effort: none` | 清單 20 種任意方向 | 是 | **temperature 0**（translate_eval）；top_p 0.8、presence_penalty 1.5（model card 非思考模式） | 6.6 GB | Apache-2.0 |
| `qwen3.5:4b`（Q4_K_M） | 同上 | 清單 20 種任意方向 | 是 | 同上 | 3.3 GB | Apache-2.0 |
| `hy-mt1.5-7b:latest`（Q4_K_M） | `hunyuan_mt` | 清單 20 種任意方向 | 否 | **temperature 0**（translate_eval）；top_p 0.6（model card） | 4.6 GB | Tencent HY Community License ⚠ |
| `hy-mt1.5-1.8b:latest`（Q8_0） | `hunyuan_mt` | 清單 20 種任意方向 | 否 | 同上 | 1.9 GB | Tencent HY Community License ⚠ |
| `hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS` | `sakura` | 只有 `ja → zh-TW`／`zh-CN` | 否 | temperature 0.3、top_p 0.8（model card） | 4.3 GB | CC BY-NC-SA 4.0 ⚠ |

**溫度（Milestone 16）**：Qwen3.5 與 HY-MT 一律 0（greedy）。translate_eval（有參考譯文、paired bootstrap 1,000 次）：Qwen3.5
溫度 0 讓 ja→zh 兩個資料集的 chrF 都顯著提高（speech +0.65～+0.75，p ≤ 0.002；social +2.1～+2.3，p < 0.001），social 的
COMET 也顯著提高（9B +0.0094、4B +0.0117），ja→en 沒有變差；HY-MT 的 0 與 0.7 沒有實質差別，改 0 換取可重現的輸出（同一句
重翻得到同一個譯文，字幕不跳動）。溫度 0 時 `top_p` 不起作用（只是與評測設定一致而保留）；`presence_penalty` 是對 logits 的
懲罰，greedy 時仍有作用，而評測的溫度 0 設定就帶著 1.5，所以照送。Sakura 沒有比較過，維持官方建議的 0.3。

授權限制（UI 選模型時要顯示）：

- **Apache-2.0（Qwen3.5）**：無地區或商用限制。來源：`huggingface.co/Qwen/Qwen3.5-9B`（model card 標示 apache-2.0）、Ollama `/api/show` 的 license 欄位。
- **Tencent HY Community License（HY-MT1.5，1.8B 與 7B 相同）**：授權**不適用於歐盟、英國、南韓**（"Territory" 排除這三地，並禁止在 Territory 以外使用模型「或其輸出」）；授權版本發布當時月活躍用戶超過 1 億須另向騰訊申請；須遵守附件的 Acceptable Use Policy；鼓勵（非強制）標示 "Powered by Tencent HY"。來源：`huggingface.co/tencent/HY-MT1.5-7B` 的 `License.txt`。
- **CC BY-NC-SA 4.0（Sakura-GalTransl）**：**禁止商業用途**（model card：禁止付費翻譯介面、付費補丁、商用翻譯等）。不列入任何硬體等級的推薦，只在使用者自行選擇時使用。

`ModelPreset` 帶有 `license`、`license_url`、`commercial_use`、`excluded_regions` 欄位，供 UI 顯示與 `recommend` 過濾。

語言支援：`languages`（支援的清單語言碼）＋選用的 `pairs`（只支援特定方向時的（Whisper 原文, 清單譯文）組合，例如 Sakura；其方向清單 `SAKURA_PAIRS` 定義在 `translate/languages.py`，預設組與提示詞共用同一份）。`supports(preset, source, target)` 回傳能否翻譯；語言碼不合法或 Whisper 語言相同時回傳 False。Qwen3.5 的 model card 只寫「201 languages and dialects」而沒有列表，清單的 20 種主流語言視為全部支援；HY-MT 依 model card 語言表。

## 術語表（Milestone 16，`glossary.py`）

- **為什麼**：translate_eval 的 VTuber 句中，連 Qwen3.5 9B 都把 赤スパ 譯成「紅色辣椒粉」、推し 譯成「我喜歡的人」、メン限 譯成
  "men's lineup"；HY-MT 7B 把 スパチャ 音譯成「斯帕查」。WMT 術語任務與 HY-MT 官方術語範本都是「出現才給」：提示詞短、不會讓
  模型把沒出現的詞塞進譯文。
- **內建表**（`BUILTIN_TERMS`，Python 資料，打包不需額外設定）：53 個詞條，只用於原文是日語（Whisper `ja`）、譯文是 zh-TW／zh-CN／
  en／ko；每個詞條有原文寫法變體（例如 スパチャ／スーパーチャット）與各語言譯法，沒有可靠譯法的語言不列（例如 てぇてぇ、ガチ恋、
  中の人 沒有韓文）。有平台官方名稱者用官方名稱：YouTube 繁中介面的 Super Chat＝「超級留言」、Super Sticker＝「超級貼圖」、
  會員＝「頻道會員」、會員限定＝「會員專屬」；其餘用中文／英文／韓文 VTuber 社群的通用說法（聯動、切片、歌回、斗內、業配、
  collab、clipper、최애、ㅋㅋㅋ）。容易和一般用語撞字的詞只收不會誤判的寫法：不收單獨的「草」「箱」「枠」「おつ」「メンバー」
  「案件」「耐久」「初見」，改收「草生え／大草原」「箱推し」「歌枠／雑談枠」「おつです」「メンバーシップ／メン限」「案件配信」
  「耐久配信」「初見さん／初見プレイ」。8888（拍手）特別收錄：中文的 8888 是「掰掰」，最容易被誤譯。
- **中翻日的中日同形異義詞表**（`ZH_JA_FALSE_FRIENDS`）：原文是中文（Whisper `zh`）、譯文是日文時使用，和內建表共用同一個開關（`translation.glossary.builtin`）。中日共用漢字，模型會用日文的讀法斷詞或理解同形詞（Qwen3.5 的 tokenizer 把「豬排便當」切成 豬｜排便｜當；「節目」「階段」「顏色」在日文是轉折點、樓梯、臉色）。只收中文意思穩定、而且日文同形詞意思不同的詞；不收單字（「娘」在「姑娘」「新娘」裡意思不同）；複合詞條目（「豬排便當」→「豚カツ弁当」）同時告訴模型詞的邊界；有歧義的寫法以較長的寫法擋住（「老婆婆」不是「老婆」、「佔便宜」不是「便宜」、台灣網路用語「大丈夫」不是「丈夫」）；繁簡寫法都列。依據：字典輔助提示（chain-of-dictionary，EMNLP 2024；IdiomKB，AAAI 2024）與 WMT23 術語任務的「出現才給」。涵蓋不到的詞靠上面的中文指示。
- **使用者詞條**：設定 `translation.glossary.entries`（見 `pipeline-engine.md`）；任何原文語言都適用。同一個原文寫法時使用者的
  譯法優先於內建表，指定 `target_language` 的優先於不指定的。
- **比對**（`Glossary.lookup(text, source_language, target_language)`）：原文與詞條都以 NFKC＋casefold 正規化（全形英數、半形片假名、
  大小寫統一：ｗｗｗ＝www、ｽﾊﾟﾁｬ＝スパチャ），同一位置取最長的寫法（「スパチャ読み」優先於「スパチャ」），找到的範圍不重疊；
  依出現順序、不重複，**最多 8 條**（`MAX_TERMS_PER_REQUEST`）。正規化後的比對表依（原文語言, 譯文語言）快取。
- **注入**：`OpenAICompatibleTranslator(glossary=...)` 在 `_prepare()` 查這句的術語，交給 `build_messages(style, request, terms)`；
  三種提示詞的格式見上。pipeline 的 factory 以設定建立一個 `Glossary`，兩條路徑共用。
- **評測**：見 `experiments/translate_eval/README.md`「術語表」一節（VTuber 句術語表開／關、WMT25 speech 的 COMET 不退步）。

### 依 VRAM 的推薦順序（給 hardware 模組使用）

`recommend_translation_models(vram_budget_gb, installed=None, allow_restricted=True, pairs=None)` 回傳 `TranslationModelChoice(fast=..., final=...)`：

- `vram_budget_gb` 是**可分給翻譯模型的 VRAM**（hardware 模組先扣掉 ASR 模型），`None` 或 0 表示沒有 GPU。負數或非有限數丟 `ValueError`。
- 依等級的順序清單挑第一個已安裝的模型（`installed=None` 表示不檢查；名稱不帶 tag 視同 `:latest`）；都沒裝就回傳清單第一個（讓 UI 提示下載）。
- 非商用授權的模型一律不推薦；`allow_restricted=False` 時再排除有地區限制的模型（例如使用者在歐盟、英國、南韓）。
- `pairs`（Milestone 12）：必須全部支援的（原文, 譯文）方向（`supports()`；原文可為 Whisper 或清單語言碼）。hardware 依語言選擇傳入：
  影片翻譯一個方向、口譯兩個方向、收聽語言 `auto` 時為「其他每個 Whisper 語言 → 字幕語言」。不支援的模型直接略過，順序仍依 VRAM 等級
  （不跨等級找：更大的放不下、更小的品質較差）；等級內沒有任何模型支援 → 丟 `NoSupportingModelError`（`ValueError` 子類別，`pairs`
  屬性為要求的方向），hardware 轉成 `translation_status = "unsupported"` 與訊息 `translation_unsupported`。同一個 Whisper 語言的方向
  （不需要翻譯）不應傳入（`supports()` 對它回傳 False）。
- 預設組的 `label` 只是模型名稱（例如 `Sakura-GalTransl 7B v3`），不帶任何語言的說明；「非商用」由 UI 依 `commercial_use` 以介面語系顯示。

| 等級（可給翻譯的 VRAM） | 即時路徑（fast）順序 | 完整路徑（final）順序 |
|---|---|---|
| ≥ 7.5 GB | qwen3.5:9b → qwen3.5:4b | 同左 |
| 4–7.5 GB | qwen3.5:4b | 同左 |
| < 4 GB／沒有 GPU | qwen3.5:4b（Ollama 放不下時部分層在 CPU，較慢） | 同左 |

依據（RTX 5070 實測）：GPU 上每個模型的總延遲 P90 都在 0.3 秒內，遠低於字幕需求，所以即時與完整路徑**用同一個模型**，只載入一份；Ollama 換模型要 1.5–18 秒（第一次載入，見 experiments/translate_benchmark/README.md），兩個模型輪流用反而更慢。品質（日文口語）Qwen3.5 9B > 4B > HY-MT 7B > HY-MT 1.8B。**每個等級都只推薦 Qwen3.5，不論 `allow_restricted`**（2026-11-06，依 `experiments/translate_eval`：Qwen 9B 與 HY-MT 7B COMET 同級但直播用語明顯較好；HY-MT 1.8B 每個資料集 COMET 都顯著較低且會幻覺，所以預算不到 4 GB 或沒有 GPU 也推薦 4B）。HY-MT 仍可在自訂組合手動選用（授權地區說明不變）。實際順序以 `presets.py` 的 `RECOMMENDATION_TIERS` 為準。

## Tech Stack

- Python 3.12、uv。
- HTTP：標準庫 `http.client`、`ssl`、`json`、`urllib.parse`。
- 新增依賴：`opencc==1.4.2`（Apache-2.0；BYVoid/OpenCC 官方 Python 套件，Windows／Linux／macOS 都有 wheel），用於簡體偵測與簡轉繁。

## Commands

```text
測試（本模組）：uv run python -m unittest tests.test_translate_prompts tests.test_translate_client
測試（全部）：  uv run python -m unittest discover -s tests
Lint：          uv run ruff check
實測（需本機 Ollama）：uv run python -I -X utf8 experiments/translate_benchmark/menu.py
```

## Project Structure

```text
src/kikitori/translate/
  __init__.py      → 對外匯出
  types.py         → TranslationRequest、Translator（架構合約）
  errors.py        → 例外階層
  languages.py     → 語言碼正規化、組合驗證（依語言清單）、請求驗證
  presets.py       → ModelPreset、各模型預設組、VRAM 推薦
  prompts.py       → 依 PromptStyle 組 messages
  postprocess.py   → 去思考內容、整理字幕、簡體偵測與簡轉繁
  client.py        → TranslatorConfig、OpenAICompatibleTranslator（HTTP、SSE、重試、術語注入）
  glossary.py      → 內建直播術語表、使用者詞條、Glossary.lookup()（Milestone 16）
  context_guard.py → ContextFallbackTranslator：譯文像是翻出前文時不帶前文重翻（Milestone 16）
tests/
  test_translate_prompts.py       → 語言驗證、提示詞、預設組（含溫度）、後處理（特殊 token、選擇性繁簡轉換）、VRAM 推薦
  test_translate_client.py        → 本機假伺服器：成功、串流、錯誤、逾時、重試、API key、輸入驗證、術語注入
  test_translate_glossary.py      → 內建表資料、出現才注入、最長比對、上限、使用者覆寫、各提示詞格式
  test_translate_context_guard.py → 假翻譯器：長度異常／抄出前文時重翻一次（translate 與 stream）
experiments/translate_benchmark/
  menu.py          → 互動選單：列出模型、執行實測、顯示摘要
  lines.json       → 測試句（自寫）
  results/         → 各模型的實測輸出（JSON）
  README.md        → 實測結果與結論
```

## Testing Strategy

- `unittest`，不連真實網路：`http.server.ThreadingHTTPServer` 在背景執行緒跑假的 OpenAI 相容伺服器，每個測試排好回應腳本，再檢查翻譯結果與收到的請求（路徑、標頭、內容）。
- 重試測試注入假的 `sleep`，不真的等待。
- 逾時測試把 `read_timeout` 設成 0.2～0.3 秒、伺服器延遲回應。
- 實測工具不屬於單元測試，不會在 `unittest discover` 時執行（放在 `experiments/`，檔名不是 `test_*.py`）。

## Boundaries

- **一定要做**：所有網路 I/O 設 timeout；錯誤往上丟；驗證輸入；金鑰不進 log。
- **先問**：新增依賴、改架構規格。
- **不做**：散布模型權重；在翻譯器內保存跨請求狀態；吞例外。

## Success Criteria

- 合約型別與架構規格一致；`OpenAICompatibleTranslator` 滿足 `Translator` Protocol。
- 單元測試涵蓋：成功、SSE 串流、`<think>` 去除、4xx／5xx、逾時、壞 JSON、重試、API key 標頭與不進 log、輸入驗證。
- 實測：每個已安裝模型完成全部測試句；記錄首字延遲與總延遲；目標為 zh-TW 的輸出經後處理後沒有簡體專用字。

## 已知限制

- Sakura 與 HY-MT 不使用前文（格式不合或會把前文一起翻出來）。
- 讀取逾時是「每次 socket 讀取」的上限，不是整個請求的總時間；總長度以 `max_tokens` 限制。
- 串流在第一個片段之後中斷不會重試。
- 簡體偵測以 Big5 判斷，`斗`、`范` 這類在 Big5 內且兩岸都用的字不會被當成簡體；只有大陸用語、但字是繁體的譯文（例如「視頻」）不會被轉換。
- 提示詞與推薦只在 RTX 5070（12 GB）上實測；CPU 與 Apple Silicon 未實測。
- OpenAI 相容 API 不能指定 Ollama 的 `keep_alive`，模型閒置 5 分鐘（Ollama 預設）就被卸載。本模組不處理；由 pipeline 在 session 開始時預熱、執行中每 `translation.keep_warm_secs`（預設 240）秒沒有翻譯請求就再送一次同樣的短請求（只對本機服務，見 `pipeline-engine.md`「ASR 接縫」）。沒有 session 時模型仍會被卸載，下一次 session 開始時重新預熱。
