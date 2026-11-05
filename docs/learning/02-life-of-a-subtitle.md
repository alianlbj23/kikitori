# 02 一句字幕的一生

我們跟著一句日文台詞，從喇叭一路走到字幕視窗：

> 待って待って、今のなし！もう一回やらせて！
> → 等等等等，剛才那個不算！再給我一次機會吧！

（這句與譯文取自 `experiments/translate_benchmark/README.md` 的 `qwen3.5:9b` 實測結果。）

模式是 `media`（影片翻譯），全部用預設設定。

```text
① capture  →  ② VAD 切句  →  ③ 分成兩條路  →  ④ 辨識＋過濾  →  ⑤ 翻譯  →  ⑥ 送出事件  →  ⑦ 畫面
```

---

## ① capture：把聲音變成「16 kHz、單聲道、int16」

程式：`src/kikitori/capture/`。

影片播放器把聲音送到喇叭。kikitori 用 loopback（回送擷取）把同一份聲音錄下來：

| 平台 | 怎麼錄 | 開啟格式 |
|---|---|---|
| Windows | PyAudioWPatch 的 WASAPI loopback（`_wasapi_backend.py`） | 裝置原生格式，例如 48 kHz 立體聲 |
| Linux | soundcard 套件，PulseAudio／PipeWire 的 monitor source（`_soundcard_backend.py`） | 48 kHz |
| macOS 13+ | ScreenCaptureKit（`_screencapturekit_backend.py`） | 48 kHz、系統先降成單聲道 |

接著在 `resample.py` 的 `AudioConverter` 做三件事：

```text
48 kHz 立體聲 float
  │ downmix_to_mono()：各聲道取平均
  ▼
48 kHz 單聲道
  │ StreamingResampler：Kaiser 窗 windowed-sinc、polyphase 濾波器
  │   通帶到 7 kHz，8 kHz 以上衰減 80 dB；48→16 kHz 每個輸出樣本用 242 個 taps
  ▼
16 kHz 單聲道 float
  │ float_to_int16()：× 32768、四捨五入、截在 −32768～32767
  ▼
16 kHz 單聲道 int16（一維 numpy 陣列）→ 放進 ChunkBuffer
```

細節：

- **為什麼要自己重採樣**：讓三平台品質一致。常見的 `soxr` 是 LGPL，其他選擇要多帶原生函式庫（`docs/specs/capture.md`）。
- **WASAPI 靜音補零**：實測 Windows 在沒有聲音播放時完全不送資料。VAD 需要「靜音」才能判斷句尾，所以 loopback 超過 50 ms 沒資料就補零（`SILENCE_FILL_AFTER = 0.05`）。
- **ChunkBuffer**（`buffer.py`）最多存 5 秒。滿了就丟最舊的並記 warning。理由：字幕寧可跳過舊聲音，也不要越拖越晚。
- 這一段的延遲約 20–50 ms（`docs/specs/capture.md` 的延遲預算），相對於 0.6 秒的句尾等待可以忽略。

## ② VAD 切句：找出「一句話」的起點與終點

程式：`src/kikitori/vad/`。VAD＝Voice Activity Detection，人聲偵測。

pipeline 的 capture 執行緒每次呼叫 `source.read(0.1)`，把拿到的聲音丟進 `Segmenter.process()`（`segmenter.py`）。

### 2-1 每 32 ms 問一次模型

```text
任意長度的輸入 ──► 湊成 512 樣本（32 ms）的視窗，不足的留到下次
                     │
                     ▼
     SileroVad.probability()（silero.py）
       前面接上一個視窗最後 64 樣本 → 送進模型的是 576 樣本
       模型狀態 state 形狀 (2, 1, 128)，每次推論後存回去
                     │
                     ▼
               人聲機率 0.0～1.0
```

- 「接 64 樣本前文」很關鍵：評測中不接前文時，人聲區平均機率最高只有 0.0021，所有句子都漏抓（`experiments/silero_reset/README.md`）。
- 每個視窗推論平均 0.18 ms（同一份 README）。

### 2-2 判斷句子開始與結束（遲滯門檻）

| 狀況 | 規則 | 預設值 |
|---|---|---|
| 進入「候選」 | 機率 ≥ `threshold` | 0.5 |
| 取消候選 | 機率 < `neg_threshold` | 0.35（＝ 0.5 − 0.15） |
| 確認開始 | 候選持續 ≥ `min_speech_ms` | 250 ms |
| 句子起點 | 候選起點往前多含 `pre_roll_ms`，但不越過上一句結尾 | 200 ms |
| 開始算靜音 | 句子中機率 < 0.35 | — |
| 結束：`pause` | 連續靜音 ≥ `pause_secs` | 0.6 s（media）、0.5 s（interpret） |
| 結束：`max_length` | 句長 ≥ `max_secs` | 10 s（media）、8 s（interpret） |
| 結束：`external` | 外部呼叫 `request_end()`（預留給之後的標點模型、Smart Turn） | — |
| 結束：`flush` | 串流結束 | — |

「遲滯」的意思：開始要 ≥ 0.5，結束要 < 0.35；介於兩者之間時維持原狀，避免在門檻附近來回跳。
句尾判斷在 `endpoint.py` 的 `PauseEndpoint.decide()`。

### 2-3 輸出三種事件

```text
UtteranceStarted(start_sample)
UtteranceAudio(start_sample, audio)   ← 句子進行中每 32 ms 一個；第一個會把前置緩衝一起補上
UtteranceAudio(...)
...
UtteranceEnded(start_sample, end_sample, reason)
```

同一句的所有 `UtteranceAudio` 首尾相接，剛好是 `[start_sample, end_sample)`。

**位置用樣本數，不用時鐘**。例如 `start_sample=4480` 代表串流開頭後 4480 ÷ 16000 = 0.28 秒。
這樣不論聲音一次餵多少、餵多快，結果都一樣，測試可以完全重現。

### 2-4 實際例子（假模式引擎的輸出）

假模式的聲音是：0.5 s 靜音、1.5 s「人聲」、1.0 s 靜音、1.2 s「人聲」、1.0 s 靜音（`engine/testing.py` 的 `_speech_track()`）。第一句：

```text
人聲從 8000 開始 → 落在第 15 個視窗（7680～8192）→ 候選起點 7680
7680 − 3200（200 ms 前置緩衝）= 4480  ← start_sample
人聲在 32000 結束 → 從 32256 開始算靜音 → 41984 時已靜音 0.6 s → pause 結束
完整路徑裁掉多餘靜音：41984 − 6400（0.6 − 0.2 s）= 35584  ← final 的 end_sample
```

和實際輸出的 `"start_sample":4480`、`"end_sample":35584` 一致。

### 2-5 重置策略（ResetPolicy）

Silero 有內部狀態。長時間聽音樂或噪音後，狀態會「偏掉」，讓下一句開頭被低估。
預設 `AFTER_LONG_NON_SPEECH`：連續 2 秒沒有句子也沒有候選人聲，且最近 5 個視窗機率都 < 0.1，就重置模型（`Segmenter._apply_reset_policy()`）。
為什麼選它，見 [04-design-tradeoffs.md](04-design-tradeoffs.md)。

## ③ 分成兩條路：即時與完整

程式：`src/kikitori/pipeline/pipeline.py` 的 `_handle()` 與 `_end_utterance()`。

```text
                         ┌─ 每多 0.7 秒音訊 ─► LatestSlot（最新槽，只留 1 個）─► 即時 worker
UtteranceAudio 累積音訊 ─┤
                         └─ UtteranceEnded ──► FinalQueue（有界 64，不丟）  ─► 完整 worker
```

| | 即時路徑（partial） | 完整路徑（final） |
|---|---|---|
| 何時做 | 句子進行中，每多 `partial_interval_ms`（700 ms）的新音訊；若上一次即時辨識比 700 ms 還慢，就改成等那麼久 | 句子結束後 |
| 給什麼音訊 | 從句首到現在的全部音訊 | 整句；`pause` 結束時裁掉尾巴的靜音，只留 0.2 s（Whisper 字尾被硬切時容易漏字） |
| 排隊方式 | 新工作直接取代還沒做的舊工作；句子已結束的工作直接丟掉 | 先進先出，一句都不丟；佇列滿了 capture 會等（背壓） |
| 結果 | `is_final=false`，之後會被取代 | `is_final=true`，定稿 |

**為什麼要兩條路**：Whisper 每次都處理 30 秒的視窗，耗時幾乎和句長無關。
如果等句子說完才辨識，觀眾要等「句長 + 0.6 s + 辨識 + 翻譯」才看到字。
先用小模型出草稿，人在說話時就有字；說完再用大模型定稿。

**順序保證**：送出字幕都經過同一把鎖。某句的定稿送出後，同一句晚到的草稿一律丟掉（`Pipeline._emit()`）。

## ④ 語音辨識與過濾

程式：`src/kikitori/asr/`；接線在 `src/kikitori/pipeline/factory.py`。

### 4-1 兩種解碼設定（`asr/whisper.py`）

| | `FAST_DECODE`（即時） | `FINAL_DECODE`（完整） |
|---|---|---|
| 搜尋方式 | greedy（每步只取最可能的字，`beam_size=1`） | beam search 5 條 |
| 溫度重試 | 不重試 | 結果重複或信心太低時，用溫度 0.2、0.4 重試 |
| 最長輸出 | 128 token（避免陷入「と、と、と…」迴圈） | 不限 |
| 前文 prompt | 不帶 | media 模式帶前 3 句定稿原文（以空白串接） |
| 候選語言 | media：`ja`、`en`；interpret：`zh` 與對方語言 | 同左 |

預設模型（設定檔）：即時 `base`、完整 `small`，裝置 `auto`（有 NVIDIA GPU 且載得到 cuBLAS 就用 GPU，否則退回 CPU）。這組在沒有 GPU 的電腦也跑得動。硬體偵測後可「套用建議組合」，例如 NVIDIA 8 GB 以上改成即時 `small`、完整 `large-v3-turbo`，都在 GPU 跑。

### 4-2 幻覺過濾（`asr/filtering.py` 的 `rejection_reason()`）

Whisper 在沒有人聲時會編句子。依序檢查，命中任一條就丟掉：

| 規則 | 門檻 | 來源 |
|---|---|---|
| 去掉標點後是空字串 | — | — |
| `no_speech_probability` > 0.6 **且** `avg_logprob` < −1.0 | 0.6、−1.0 | Whisper 原本的靜音規則 |
| `no_speech_probability` > 0.8 | 0.8 | 實測：語音最高 0.43，非語音最低 0.51 |
| `avg_logprob` < −1.5（信心太低） | −1.5 | 歌聲、BGM 常見 |
| 壓縮比 > 2.4（一直重複） | 2.4 | Whisper 的重複判斷 |
| 整句等於已知幻覺句 | 清單 `DEFAULT_HALLUCINATIONS` | 例：「ご視聴ありがとうございました」「Teksting av Nicolai Winther」 |
| 整句等於「真人也常說」的短句，且 `no_speech` > 0.3 | 清單 `DEFAULT_SOFT_HALLUCINATIONS` | 例：「Thank you.」「おやすみなさい」 |

- 比對前做 NFKC、轉小寫、去標點，而且要**整句相等**，避免誤刪真的說了「ありがとう」的句子。
- `large-v3-turbo` 的 `no_speech_probability` 實測永遠是 0，前三條規則對它無效。所以完整模型是它時，pipeline 改用**即時模型對同一句的 no_speech** 來過濾；句子太短沒跑過即時辨識時，補跑一次（`Pipeline._for_filter()`）。
- **定稿被濾掉**時：如果這句已經顯示過草稿，就送一筆「原文、譯文都是空字串」的定稿，讓畫面把草稿清掉（`Pipeline._emit_cleared()`）。

## ⑤ 翻譯

程式：`src/kikitori/translate/`。

### 5-1 決定譯成什麼語言（`pipeline/languages.py` 的 `route()`）

| 模式 | 辨識出的語言 | 譯文 |
|---|---|---|
| media | 任何 | `zh-TW` |
| interpret | 中文 | 對方語言（`ja` 或 `en`） |
| interpret | 其他 | `zh-TW` |

原文本來就是譯文語言時不翻（譯文＝原文）；不支援的組合（例如偵測到韓文）譯文留空並送 warning。

### 5-2 組提示詞（`translate/prompts.py`）

一般模型（Qwen3.5 與不認得的模型）用「generic」格式，**指示用譯文語言寫**：

```text
[system]
你是字幕翻譯員，把使用者給的日文句子翻譯成繁體中文（台灣用語）。
規則：
- 只輸出一行譯文。
- 簡短、自然、忠於原意與語氣。
...
[user]
前文（只供參考，不要翻譯）：
<前幾句同語言的原文>

請翻譯成繁體中文：
待って待って、今のなし！もう一回やらせて！
```

- 為什麼用中文寫指示：英文指示下 `qwen3.5:9b` 有 11/18 次直接輸出日文，改成中文後 0/18（`experiments/translate_benchmark/README.md`）。
- HY-MT 模型用官方範本、**不帶前文**（實測 1.8B 有 11/12 次把前文一起翻出來）；Sakura 模型只做日翻中。
- 前文只放「與本句同語言」的最近幾句定稿原文（`Pipeline._context()`）。

### 5-3 送出請求（`translate/client.py`）

- 只用 Python 標準庫的 `http.client`，`POST {base_url}/chat/completions`，預設 `http://localhost:11434/v1`。
- 即時路徑用 `stream()`（SSE 串流），**每次產出到目前為止的完整譯文**，不是增量；完整路徑用 `translate()` 一次拿完。
- Qwen3.5 送 `reasoning_effort: "none"` 關掉「思考」，否則會先輸出一大段推理。
- `localhost` 先連 `127.0.0.1`：Windows 先試 IPv6 被拒要等約 2 秒，實測每個請求都慢 2.06 秒（`OpenAICompatibleTranslator._connect()`）。
- 即時路徑：不重試、讀取逾時 10 s；完整路徑：最多重試 2 次、讀取逾時 30 s（`pipeline/factory.py` 的 `create_translators()`）。
- 翻譯失敗不會讓整個 session 停掉：字幕照送，譯文空白，送一筆 warning（同樣的訊息連續只報一次）。

### 5-4 後處理（`translate/postprocess.py` 的 `finalize_translation()`）

```text
模型原始輸出
  │ strip_thinking()：去掉 <think>…</think>
  │ clean_subtitle()：去掉 <|eos|> 之類的特殊 token、「譯文：」標籤、整句外層引號
  │ to_taiwan_traditional()：有「簡體專用字」才用 OpenCC s2twp 轉台灣正體
  ▼
字幕譯文
```

「簡體專用字」＝ OpenCC 會改、又不在 Big5 字集裡的字。這樣才不會把正確的「那個傢伙」改成「傢夥」、「台灣」改成「臺灣」。

## ⑥ 送出事件：引擎 → Rust

- `engine/server.py` 的 `_on_subtitle()` 把 `SubtitleUpdate` 轉成 dict，加上 `session_id`，寫成一行 JSON。
- `engine/io.py` 的 `MessageWriter` 有鎖（多執行緒不會寫成交錯的半行），寫完立刻 flush。
- Rust 端（`app/src-tauri/src/`）：
  1. `line_reader.rs`：逐行讀，每行上限 1 MiB；超長就丟掉並讀到行尾，不會失去同步。
  2. `protocol.rs` 的 `parse_line()`：驗證 UTF-8、JSON、欄位型別；字幕文字 ≤ 10,000 字、語言碼只允許合法字元、`end_sample ≥ start_sample`。
  3. `connection.rs` 的 `dispatch()`：有 `id` 的交給等待中的請求；事件交給 sink。
  4. `lib.rs`：發出 Tauri 事件 `engine://subtitle`。

## ⑦ 畫面：草稿被定稿取代

- `app/src/lib/api.ts` 收到事件，用 `validate.ts` 的 `parseSubtitle()` 再驗一次。
- `app/src/lib/subtitles.ts` 的 `applySubtitle()` 是主視窗與字幕視窗共用的規則：
  1. 同一個 `utterance_id` 的草稿原地取代；定稿取代草稿。
  2. 定稿之後晚到的草稿不理。
  3. 原文、譯文都空白的定稿 → 移除這句。
  4. 依 `utterance_id` 排序，只留最新 N 句（字幕視窗 1–3 句、主視窗 300 句）。
  5. 舊 session 的事件丟掉。
- 字幕視窗（`app/src/overlay/overlay.ts`）：草稿加 `is-partial` 樣式（較淡、譯文白色）；定稿到達時譯文用 220 ms 轉成字幕黃（`app/src/styles/overlay.css`）。

我們的例句在畫面上大致是這樣：

```text
時間 →
[草稿] 待って待って          / 等等等等
[草稿] 待って待って、今のなし / 等等等等，剛才那個不算
                                        （句子結束，等 0.6 s 靜音）
[定稿] 待って待って、今のなし！もう一回やらせて！ / 等等等等，剛才那個不算！再給我一次機會吧！（變黃）
```

（草稿的內容是示意；實際字句取決於即時模型。）

---

## 延遲總表

### 端到端（`experiments/pipeline_smoke/README.md`）

環境：Ryzen 7 5700X，**沒有 cuBLAS，ASR 全在 CPU**；翻譯 `qwen3.5:4b`（Ollama，GPU）；33 秒、8 句的動漫音軌。

| 設定 | 第一個草稿出現（中位數／P90） | 草稿延遲（中位數／P90） | 定稿延遲（中位數／P90） |
|---|---|---|---|
| 真 ASR：即時 `base`、完整 `small`（CPU int8） | 1.63 s／2.00 s | 0.85 s／1.14 s | 2.85 s／3.16 s |
| 假 ASR（只量 VAD、排程、翻譯） | 0.80 s／0.83 s | 0.14 s／0.18 s | 0.58 s／0.59 s |

量法：「第一個草稿」從句子起點（含 200 ms 前置緩衝）算起；「定稿延遲」從說話結束處算起，包含必須等的 0.4 s 靜音。

### 拆開來看

| 環節 | 數字 | 出處 |
|---|---|---|
| 擷取 | 約 20–50 ms | `docs/specs/capture.md` 延遲預算 |
| VAD 推論 | 每 32 ms 視窗 0.18 ms | `experiments/silero_reset/README.md` |
| 句尾等待 | 0.6 s 靜音（定稿延遲裡算 0.4 s，因為保留 0.2 s 尾巴） | 設定預設值 |
| 即時辨識 `base` CPU int8 | 中位數 484 ms | `experiments/asr_benchmark/README.md` |
| 完整辨識 `small` CPU int8（beam 5） | 中位數 1305 ms | 同上 |
| 即時辨識 `small` CUDA float16 | 中位數 130 ms | 同上 |
| 完整辨識 `large-v3-turbo` CUDA float16 | 中位數 151 ms | 同上 |
| 翻譯首字（Ollama，RTX 5070） | 中位數 0.04–0.10 s | `experiments/translate_benchmark/README.md` |
| 翻譯整句（Ollama，RTX 5070） | 中位數 0.11–0.23 s | 同上 |

注意：

- 有 GPU 時的**端到端**延遲沒有量過；上表 GPU 數字是單獨跑一個模型時的值。兩個 ASR 模型與翻譯 LLM 同時搶 GPU 時會更慢（未量測）。
- CPU 被其他程式搶走時，同一設定曾量到定稿延遲中位數 16.7 秒：完整佇列會累積（`experiments/pipeline_smoke/README.md`）。之後才加上「即時辨識太慢就自動拉長間隔」。
