# Spec: translate（翻譯模組）

> 狀態：已採用（2026-11-05，使用者授權自主實作）。
> 上層約定：`docs/specs/app-architecture.md` 的 `translate` 一節；本文件不得與之矛盾。
> 實測結果與各項決定的依據：`experiments/translate_benchmark/README.md`。

## 前提假設

1. 對外介面完全照架構規格：`TranslationRequest`（凍結 dataclass）與 `Translator`（`typing.Protocol`），放在 `src/kikitori/translate/types.py`。
2. 翻譯後端是 OpenAI 相容的 Chat Completions API（`POST {base_url}/chat/completions`）；預設 `base_url = http://localhost:11434/v1`（本機 Ollama）。也能接其他相容服務（llama.cpp server、vLLM、LM Studio、雲端服務）。
3. API 為同步呼叫；執行緒安排留給 pipeline。翻譯器不保存跨請求的狀態（前文由呼叫端放在 `context`），同一個實例可以被多個執行緒同時使用。
4. 模型權重由使用者自行安裝（例如 `ollama pull`）；本專案只以名稱引用，不散布權重。仍記錄每個模型的授權並標出限制。
5. HTTP 只用標準庫（`http.client` + `json`）：只需要一個 POST 端點與 SSE 解析，而且標準庫能分開設定連線與讀取 timeout（`urllib` 只有一個共用 timeout），不值得為此加依賴。

## Objective

把一句原文翻成字幕用的譯文：短、自然、忠實、不解釋、不加引號。支援的語言組合（對應兩種模式）：

| 模式 | 原文 → 譯文 |
|---|---|
| `media` | `ja` → `zh-TW`、`en` → `zh-TW` |
| `interpret` | 對方說 `ja`／`en` → `zh-TW`；使用者說 `zh` → `ja` 或 `en` |

- 原文語言碼來自 ASR（ISO 639-1，中文為 `zh`）；`zh-TW` 當原文時視同 `zh`。語言碼不分大小寫，`zh_TW`、`zh-Hant` 視同 `zh-TW`。
- 目標為 `zh-TW` 時，輸出必須是繁體中文、台灣用語。
- 其他組合（例如 `ja` → `en`、`ko` → `zh-TW`）一律以 `InvalidRequestError`（同時是 `ValueError`）拒絕，不送出請求。

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
    convert_to_traditional: bool = True  # 目標為 zh-TW 時做簡轉繁（見「後處理」）

translator = OpenAICompatibleTranslator(TranslatorConfig(model="qwen3.5:9b"))
translator.translate(TranslationRequest("おはよう", "ja"))
```

- 建構時驗證設定（型別、範圍、`base_url` 必須是 http/https、有主機、沒有 query；`api_key_env` 必須是合法環境變數名稱），不合法丟 `ValueError`／`TypeError`。
- 提示詞格式與取樣參數由 `presets.py` 的 `ModelPreset` 決定：已知模型用專屬預設組（模型名稱不帶 tag 時自動補 `:latest` 再查），未知模型用通用預設組（`generic`）。也可在建構時明確傳入 `preset=`。
- **`localhost` 先連 IPv4**：Windows 上 `localhost` 先解析成 `::1`，而 Ollama 預設只聽 `127.0.0.1`；被拒的 IPv6 連線在 Windows 要約 2 秒才失敗，實測**每個請求**都因此多等 2.06 秒。所以 `http://localhost` 依序試 `127.0.0.1`、`::1`，`Host` 標頭仍寫 `localhost:<port>`。HTTPS 不改寫（避免憑證主機名稱比對失敗）。

## 請求內容

```json
{
  "model": "<model>",
  "messages": [...],
  "stream": true | false,
  "temperature": 0.7,
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

依官方 model card 的範本，**不使用 system prompt**（*"our model does not have the default system_prompt"*），只送一則 user 訊息。本專案的組合都含中文，所以用 ZH⇔XX 範本；目標語言名稱用 model card 語言表的中文名稱：`繁体中文`（zh-Hant）、`日语`、`英语`。

```text
将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：

{source_text}
```

官方另有 contextual 範本（`{context}\n参考上面的信息，把下面的文本翻译成{target_language}，注意不需要翻译上文，也不要额外解释：\n{source_text}`），`prompts.py` 有實作，但兩個 HY-MT 預設組都**關閉前文**（`supports_context=False`）：實測 1.8B 有 11/12 次把前文或指令一起輸出，7B 在實測句組中也會把前一句一起翻出來。

### `sakura`（SakuraLLM Sakura-GalTransl-7B-v3）

依官方 model card 的 v3 範本（system prompt 原文照用，含「翻译成简体中文」）。只支援 `ja → zh-TW`；模型輸出簡體，靠 OpenCC 轉成繁體。

- 官方的 `[History]` 欄位是「上一輪的**譯文**」，而本模組的 `context` 是前文**原文**，格式不合，因此不使用前文；術語表（`[Glossary]`）留空。

### `generic`（Qwen3.5 與未知模型）

system 訊息寫規則、user 訊息放前文與待翻句子，**指示一律用譯文語言撰寫**。實測英文指示下 `qwen3.5:9b` 翻日文時 11/18 次直接輸出日文（沒翻），改用繁體中文指示後 0/18。

目標 `zh-TW`（`{source}` 為「日文」或「英文」）：

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

目標 `ja`、`en` 用同樣結構的日文／英文版本（見 `prompts.py`）。沒有前文時省略前文段落。

### 前文處理

- 只取 `context` 最後 `max_context_lines`（預設 3）句；空白字串略過；預設組 `supports_context=False` 時不放前文。
- 前文只當參考，提示詞明確要求不要翻譯。

## 後處理

依序：

1. **去掉思考內容**：移除 `<think>…</think>`（也接受 `<thinking>`，不分大小寫）；只有開頭標籤而尚未結束（串流中）時，從標籤起全部丟掉；只出現結束標籤（聊天範本已預先放入開頭標籤）時，丟掉結束標籤之前的內容；結尾是不完整的開頭標籤（例如串流剛好切在 `<thi`）時先藏起來。OpenAI 相容串流中另外放在 `delta.reasoning`／`reasoning_content` 的推理內容一律不讀。
2. **整理字幕**：移除漏出來的特殊 token（`<|eos|>`、`<|extra_0|>` 等，以及結尾的 `<|` 碎片；實測 HY-MT 1.8B 會出現）；去頭尾空白；去掉開頭的「譯文：」「翻譯：」「Translation:」等標籤；整句被同一對引號（`"…"`、`「…」`、`『…』`、`“…”` 等）包住、且內部沒有同種引號時去掉外層引號。
3. **簡轉繁**（目標 `zh-TW` 且 `convert_to_traditional=True`）：
   - 先找「簡體專用字」：OpenCC `s2tw` 會改掉、且不在 Big5 字集內的漢字，另加 `么于网扰`（在 Big5 內但台灣文字幾乎只以簡體身分出現）。Big5 用來排除「才、吃、伙、台、里、后」這類兩岸通用、OpenCC 卻會改寫的字。
   - **有簡體專用字才轉**，用 OpenCC `s2twp`（台灣正體＋台灣慣用詞，例如 视频→影片、软件→軟體）。沒有就原樣回傳：實測 `s2twp` 會把正確的「那個傢伙」改成「那個傢夥」、「台灣」改成「臺灣」。
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
| `qwen3.5:9b`（Q4_K_M） | `generic`，`reasoning_effort: none` | 全部 4 組 | 是 | 非思考模式：temperature 0.7、top_p 0.8、presence_penalty 1.5（model card） | 6.6 GB | Apache-2.0 |
| `qwen3.5:4b`（Q4_K_M） | 同上 | 全部 4 組 | 是 | 同上 | 3.3 GB | Apache-2.0 |
| `hy-mt1.5-7b:latest`（Q4_K_M） | `hunyuan_mt` | 全部 4 組 | 否 | temperature 0.7、top_p 0.6（model card） | 4.6 GB | Tencent HY Community License ⚠ |
| `hy-mt1.5-1.8b:latest`（Q8_0） | `hunyuan_mt` | 全部 4 組 | 否 | 同上 | 1.9 GB | Tencent HY Community License ⚠ |
| `hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS` | `sakura` | 只有 `ja → zh-TW` | 否 | temperature 0.3、top_p 0.8（model card） | 4.3 GB | CC BY-NC-SA 4.0 ⚠ |

授權限制（UI 選模型時要顯示）：

- **Apache-2.0（Qwen3.5）**：無地區或商用限制。來源：`huggingface.co/Qwen/Qwen3.5-9B`（model card 標示 apache-2.0）、Ollama `/api/show` 的 license 欄位。
- **Tencent HY Community License（HY-MT1.5，1.8B 與 7B 相同）**：授權**不適用於歐盟、英國、南韓**（"Territory" 排除這三地，並禁止在 Territory 以外使用模型「或其輸出」）；授權版本發布當時月活躍用戶超過 1 億須另向騰訊申請；須遵守附件的 Acceptable Use Policy；鼓勵（非強制）標示 "Powered by Tencent HY"。來源：`huggingface.co/tencent/HY-MT1.5-7B` 的 `License.txt`。
- **CC BY-NC-SA 4.0（Sakura-GalTransl）**：**禁止商業用途**（model card：禁止付費翻譯介面、付費補丁、商用翻譯等）。不列入任何硬體等級的推薦，只在使用者自行選擇時使用。

`ModelPreset` 帶有 `license`、`license_url`、`commercial_use`、`excluded_regions` 欄位，供 UI 顯示與 `recommend` 過濾。

### 依 VRAM 的推薦順序（給 hardware 模組使用）

`recommend_translation_models(vram_budget_gb, installed=None, allow_restricted=True)` 回傳 `TranslationModelChoice(fast=..., final=...)`：

- `vram_budget_gb` 是**可分給翻譯模型的 VRAM**（hardware 模組先扣掉 ASR 模型），`None` 或 0 表示沒有 GPU。負數或非有限數丟 `ValueError`。
- 依等級的順序清單挑第一個已安裝的模型（`installed=None` 表示不檢查；名稱不帶 tag 視同 `:latest`）；都沒裝就回傳清單第一個（讓 UI 提示下載）。
- 非商用授權的模型一律不推薦；`allow_restricted=False` 時再排除有地區限制的模型（例如使用者在歐盟、英國、南韓）。

| 等級（可給翻譯的 VRAM） | 即時路徑（fast）順序 | 完整路徑（final）順序 |
|---|---|---|
| ≥ 7.5 GB | qwen3.5:9b → qwen3.5:4b → hy-mt1.5-7b → hy-mt1.5-1.8b | 同左 |
| 4–7.5 GB | qwen3.5:4b → hy-mt1.5-1.8b | 同左 |
| < 4 GB／沒有 GPU | hy-mt1.5-1.8b → qwen3.5:4b | 同左 |

依據（RTX 5070 實測）：GPU 上每個模型的總延遲 P90 都在 0.3 秒內，遠低於字幕需求，所以即時與完整路徑**用同一個模型**，只載入一份；Ollama 換模型要 6–18 秒，兩個模型輪流用反而更慢。品質（日文口語）Qwen3.5 9B > 4B > HY-MT 7B > HY-MT 1.8B。CPU 推論沒有實測，先選最小的 HY-MT 1.8B。實際順序以 `presets.py` 的 `RECOMMENDATION_TIERS` 為準。

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
  languages.py     → 語言碼正規化、支援組合、請求驗證
  presets.py       → ModelPreset、各模型預設組、VRAM 推薦
  prompts.py       → 依 PromptStyle 組 messages
  postprocess.py   → 去思考內容、整理字幕、簡體偵測與簡轉繁
  client.py        → TranslatorConfig、OpenAICompatibleTranslator（HTTP、SSE、重試）
tests/
  test_translate_prompts.py → 語言驗證、提示詞、預設組、後處理、VRAM 推薦
  test_translate_client.py  → 本機假伺服器：成功、串流、錯誤、逾時、重試、API key、輸入驗證
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
