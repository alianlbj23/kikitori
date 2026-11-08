# 10 即時字幕的穩定性與延遲

[02 一句字幕的一生](02-life-of-a-subtitle.md) 講過雙路徑：句子還在說時，每隔約 0.7 秒用較快的模型把「到目前為止的整句音訊」重新辨識一次，送出即時字幕；說完後用較準的模型算完整字幕取代它。
v0.2.0 在這條路上加了兩件事：

1. **穩定前綴**：告訴 UI 即時字幕的哪一段已經不會再變，並減少即時翻譯的請求（`stable_chars`）。
2. **量測**：每句完整字幕的延遲、輸入音量、落後幾句（`latency_ms` 與 `metrics` 事件）。

規格：`docs/specs/competitive-features.md` F2、F4；`docs/specs/pipeline-engine.md`「穩定前綴」「量測」；UI 端 `docs/specs/ui.md`「穩定的即時字幕」「延遲、落後與音量」。

---

## 1. 問題：即時字幕會閃

即時路徑每次都重新辨識整句，所以每一則的**尾巴**常常和上一則不同：

```text
0.7 s  今日は
1.4 s  今日はいい転機          ← 「てんき」先猜成「転機」
2.1 s  今日はいい天気で        ← 下一則改正
2.8 s  今日はいい天気ですね
```

v0.1.0 的做法是每一則都整句換掉、每一則都送翻譯。結果：

- 原文整行跳動，眼睛不知道哪部分可以先讀；
- 譯文也跟著一直重來，還佔著翻譯伺服器（Ollama 同時要處理完整路徑的翻譯）。

---

## 2. LocalAgreement-2

### 概念

whisper_streaming（Macháček et al., 2023）的 **LocalAgreement-n**：連續 n 次辨識結果的**最長共同前綴**視為已確定。直覺是：兩次獨立辨識都同意的開頭，通常不會再改。

kikitori 取 n = 2，而且只用來**顯示與節流翻譯**，不像 whisper_streaming 那樣用它決定「哪段音訊可以丟掉」：完整路徑照樣把整句重新辨識一次，所以就算穩定前綴判斷錯了，定稿也不受影響。

在**文字層**比較（Python 字元數），因為即時路徑每次重新辨識整句，沒有 token 對齊可用；比較的是「顯示用的原文」（`strip()` 加上字形轉換，與 `SubtitleUpdate.original` 相同）。

### 程式

全部在 `src/kikitori/pipeline/stability.py`（約 80 行）的 `PartialStability`，只有即時 worker 使用，所以不需要鎖：

| 方法 | 做什麼 |
|---|---|
| `observe(utterance_id, original)` | 回傳這則的 `stable_chars`：與同一句上一則的最長共同前綴長度。換句子時重設（新句子的第一則是 0） |
| `should_translate(original, stable)` | 這則要不要重送翻譯 |
| `mark_requested(original, stable)` | 記下送出請求時的原文與穩定長度（失敗也算送過） |
| `translation`／`record_translation()` | 這句最後一次的即時譯文；不重送時沿用 |

接在 `pipeline/pipeline.py` 的即時 worker 裡：辨識 → 過濾 → `observe()` → 路由 → 要翻譯時先問 `should_translate()`。

### 什麼時候重送翻譯

```python
def should_translate(self, original, stable):
    if self._requested_stable is None:      # 這句的第一則：一定送
        return True
    return stable > self._requested_stable and original != self._requested_text
```

- **第一則一定送**：穩定前綴要等第二則才知道，不能讓第一則沒有譯文（第一個譯文的延遲與以前相同）。
- 之後只在**穩定部分比上次送翻譯時變長**、且原文不同時才送；送的是**目前完整的原文**（穩定前綴＋尾巴）。
- 不送時，這則即時字幕的原文與 `stable_chars` 照常更新，譯文沿用上一次——不清空、不閃。

用上面的例子走一次：

| 則 | 原文 | 與上一則的共同前綴 → `stable_chars` | 上次送翻譯時的穩定長度 | 送翻譯？ |
|---|---|---|---|---|
| 1 | 今日は | （第一則）0 | — | 送 |
| 2 | 今日はいい転機 | 今日は → 3 | 0 | 送（3 > 0） |
| 3 | 今日はいい天気で | 今日はいい → 5 | 3 | 送（5 > 3） |
| 4 | 今日はいい天気ですね | 今日はいい天気で → 8 | 5 | 送（8 > 5） |

如果第 4 則是「今日はいい天気」（又改回去），共同前綴是 7（今日はいい天気）> 5，仍會送；但若某一則只是尾巴在兩個猜測之間來回、前綴沒有變長，就不送。

保證：

- **請求數不會多於以前**（以前是每則都送）。
- 翻譯失敗也算送過，不會每一則都重試；失敗而沒有任何譯文時 `translation` 是空字串（與以前相同）。
- 不翻譯的路由（`copy`、`off`、`unsupported`）照樣有 `stable_chars`。
- 完整字幕完全不變：一定翻譯、沒有 `stable_chars`。
- `stable_chars` 可能**變小**（Whisper 改寫了前面的字），這是允許的；永遠不超過原文長度。

自己試：假模式引擎的即時結果是 `partial1`、`partial2`…，所以同一句第二則起 `stable_chars` 是 7（`partial` 七個字），見 `docs/specs/pipeline-engine.md`「可注入的服務」。實際跑一次（2026-11-06，節錄；假翻譯是在原文前加 `[zh-TW]`）：

```text
original     stable_chars   translation
partial1     0              [zh-TW] partial1     ← 第一則：一定送
partial2     7              [zh-TW] partial2     ← 7 > 0：重送
partial3     7              [zh-TW] partial2     ← 7 沒有比 7 大：不送，沿用上一個譯文
final1       （無）          [zh-TW] final1       ← 完整字幕照常翻譯
```

（串流翻譯時同一則可能先送出一個譯文還不完整的事件，例如 `[zh-TW] ` 再接 `[zh-TW] partial2`，上表只列最後一個。）

### UI 怎麼畫

`app/src/lib/stable.ts` 的 `splitStable(text, stableChars, isFinal)` 把原文切成 `{stable, unstable}`，字幕紀錄與字幕視窗只把 `unstable` 淡化。要注意三件事：

- **單位不同**：`stable_chars` 是 code point（Python `len`），JavaScript 字串用 UTF-16 索引。直接 `text.slice(0, stable_chars)` 會把 `𠮷`、emoji 切成一半。所以逐個字算 code point，再換算成切點。
- **不切在字素叢集中間**：e＋結合重音、用 ZWJ 連起來的 emoji 要整個算穩定或整個算不穩定，否則會被畫成兩個 `<span>` 而破掉。用 `Intl.Segmenter`；沒有它的舊 WebKitGTK 退回以 code point 切。
- **由右到左的文字**：兩個 `<span>` 都沒有自己的 `dir`，由父元素 `dir="auto"` 排列，所以阿拉伯語的順序正確。

Rust 端（`app/src-tauri/src/protocol.rs` 的驗證）把超過原文長度的 `stable_chars` 夾到原文長度——輔助欄位出錯不應該讓整句字幕被丟掉；但型別錯誤（負數、字串）仍整行拒絕。

---

## 3. 延遲：`latency_ms`

### 定義

每句完整字幕帶 `latency_ms`（`pipeline/pipeline.py` 的 `_latency_ms()`）：

```text
capture 執行緒處理 UtteranceEnded 的那一刻（VAD 判定句子結束，Job.ended_at）
  → 完整 worker 送出完整字幕的那一刻
```

包含：在完整佇列裡排隊、完整辨識、翻譯。
**不包含**：判定句尾所需的靜音（影片翻譯 `pause_secs` 預設 0.6 秒）與擷取緩衝。所以使用者實際感受的「說完到看到」大約再加 `pause_secs`。

時間用注入的 `clock`（測試可以注入假時鐘），只用來排程與量測；音訊位置一律是樣本計數，不受時鐘影響。

被過濾掉的「清除」結果（原文空字串，用來讓 UI 收掉已顯示的即時字幕）也帶 `latency_ms`，但不算進中位數。

---

## 4. `metrics` 事件：音量、落後、延遲中位數

`Pipeline.metrics()` 回傳 `PipelineMetrics`（`src/kikitori/pipeline/metrics.py`），引擎每秒送一則：

```json
{"event": "metrics", "data": {"session_id": 2, "level_dbfs": -23.4, "backlog": 0, "latency_ms_median": 910}}
```

| 欄位 | 怎麼算 | 為什麼這樣算 |
|---|---|---|
| `level_dbfs` | 最近約 1 秒（16 000 樣本）**擷取到的**音訊的 RMS：`20·log10(rms / 32768)`，限制在 [−120, 0]、取到小數一位（`LevelMeter`） | 以**樣本數**為窗、不讀時鐘：擷取停住時保持最後的值。音訊切成 100 ms（1600 樣本）的小塊累加，擷取區塊很大時也能只看最近 1 秒。平方和用 float64：int16 的平方在 int32 會溢位 |
| `backlog` | 句子已結束、還在完整佇列裡**等待**的句數（不含正在辨識的那一句） | 完整路徑一句都不丟（佇列 64），所以「跟不上」會表現成 backlog 一直長 |
| `latency_ms_median` | 最近 20 則非空完整字幕 `latency_ms` 的中位數；還沒有時 null（`LatencyWindow`） | 中位數不被偶爾一句很慢的翻譯拉高 |

引擎端的細節（`docs/specs/pipeline-engine.md`「事件」）：

- 一條 daemon 執行緒（`session-<id>-metrics`）每 `METRICS_INTERVAL = 1.0` 秒讀一次，只在 pipeline 為 `running` 時送；載入中不送。
- session 送出結束狀態（`idle`／`error`）**之前**先停止並 join 這條執行緒，所以結束狀態之後不會再有這個 session 的 `metrics`（送出卡住超過 1 秒的極端情況除外，UI 依 `session_id` 丟掉）。

### UI 怎麼用

`app/src/lib/metrics.ts`（時間由呼叫端傳入，可測）：

| 常數 | 值 | 用途 |
|---|---|---|
| `METER_FLOOR_DBFS` | −60 | 音量表 −60 dBFS 以下為空、0 為滿 |
| `NO_AUDIO_DBFS`、`NO_AUDIO_MS` | −60、15 000 | 執行中連續 15 秒都 < −60 dBFS → 提示檢查聲音來源 |
| `BACKLOG_WARN_ABOVE`、`BACKLOG_CLEAR_AT` | 3、1 | backlog > 3 出現「辨識跟不上，建議改用較小的模型」，降到 ≤ 1 才收起 |

落後提示用兩個門檻（遲滯）：只用一個門檻的話，backlog 在 3 附近上下跳時提示會一直閃。
「沒有聲音」另外設計時器（`noAudioDueAt()`），引擎不再送 `metrics` 時也會準時出現。
Rust 驗證範圍（`level_dbfs` 在 −120–0、`backlog` ≤ 1 000 000、中位數 ≤ 一天），不合格的整則丟棄（下一秒就有新的），並且只轉發給主視窗。

---

## 5. 和其他延遲控制的關係

穩定前綴與量測都是「看得見」的部分；真正控制延遲的機制在 v0.1.0 就有（見 02）：

- **最新槽**：即時路徑只留最新一個工作，落後就跳過，不會越積越多。
- **觸發間隔自動拉長**：每次觸發的間隔是 `max(partial_interval_ms, 上一次即時辨識耗時)`，即時模型太慢時不會佔滿 CPU／GPU 而拖慢完整路徑。
- **完整佇列不丟**：一句都不丟，所以落後只能靠換小模型解決——這正是 `backlog` 提示要使用者做的事。

---

## 6. 限制

- 穩定前綴是文字層的比較：Whisper 把前面的假名改成漢字時，前綴會縮短；兩次都輸出同一個幻覺時也會被當成「穩定」（完整路徑的過濾會把它清掉）。
- `latency_ms` 不含句尾靜音；要和「說完到看到」的實測（`experiments/e2e_acceptance/`）比時要加上 `pause_secs`。
- v0.2.0 的新推薦組合（turbo＋large-v3＋qwen3.5:9b）在同一張 12 GB 卡上的實際延遲還沒有端到端量過；目前唯一的 GPU 端到端數字（說完到完整字幕中位數 0.69 秒）是 0.1.0 組合的。
- 每秒一則的 `metrics` 只送主視窗，字幕視窗與 OBS 文字檔看不到音量與延遲。
