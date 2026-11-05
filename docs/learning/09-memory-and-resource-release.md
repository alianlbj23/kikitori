# 09 記憶體與資源釋放

kikitori 每開始一次字幕就把 2–6 GB 的語音辨識模型放上顯示卡，一場直播可能開開關關幾十次。
只要「結束後沒放乾淨」，幾次之後顯示記憶體就滿了：翻譯模型（Ollama）載不進來、字幕變慢，最後當掉。

這一篇講：

1. Python 什麼時候真的釋放物件，為什麼「參照循環」對 GPU 程式特別危險；
2. CTranslate2 模型物件池：上游漏記憶體時怎麼繞過；
3. 怎麼量測「有沒有漏」；
4. 本專案在 `experiments/memory_soak/` 找到的 3 個 bug 與修法。

---

## 1. Python 怎麼釋放物件

CPython 有兩套機制：

| 機制 | 何時發生 | 對 GPU 模型的影響 |
|---|---|---|
| **參照計數** | 最後一個參照消失的**當下** | 模型立刻釋放，顯示記憶體立刻還回去 |
| **循環垃圾回收**（`gc`） | 依「新配置的 Python 物件數」觸發；最老的第 2 代很少掃 | 可能幾分鐘、甚至到程式結束都不發生 |

關鍵在於：`gc` 只看 Python 物件的**數量**，不看它背後佔了多少記憶體。一個 Python 模型物件在 `gc` 眼中只是一個小物件，它在 GPU 上的 3 GB 不會讓 `gc` 早一點跑。
所以只要模型落在一個**參照循環**裡（A 參照 B、B 又參照回 A），就要等 `gc` 第 2 代回收才會釋放——在引擎裡實際上幾乎等不到。

**原則**：GPU 上的東西一定要靠**參照計數**就能釋放，不能依賴 `gc`。

### 例外是最常見的循環來源

一個例外物件的 `__traceback__` 參照丟出它的每一層 **frame**，frame 又參照那一層的區域變數（包含 `self`）。

```text
例外 ─__traceback__→ frame(Pipeline.start) ─f_locals→ self（pipeline）─→ 元件 ─→ GPU 上的模型
```

只要這個例外又被某個「也被這些 frame 參照到的物件」保存起來，就成了循環。
本專案的修法有兩種，都在程式裡標了 `memory_soak #1`：

- **丟出新的複本**：`src/kikitori/capture/errors.py` 的 `detached_copy()` 建一個同類別、同訊息、同 `__cause__`、但沒有 traceback 的新例外。存起來、之後在別的執行緒丟出的錯誤一律丟複本（`capture/threaded.py` 的 `ThreadedSource.start()`、`capture/buffer.py` 的 `ChunkBuffer`）。
- **記錄完就清掉 traceback**：`engine/server.py` 的 `_clear_tracebacks()` 沿著 `__cause__`／`__context__` 把每個例外 `with_traceback(None)`；`Pipeline._fail()`（`pipeline/pipeline.py`）也一樣。`logger.exception()` 要在清掉**之前**呼叫，log 裡才有完整的 traceback。

另一個例子是 v0.1.0 驗收時修掉的（`0aebe58`）：session 參照 pipeline、pipeline 的回呼又參照 session。`engine/server.py` 在 session 結束時把 `session.pipeline = None` 切斷這個循環，註解說明了原因。

---

## 2. CTranslate2 模型物件池

### 問題：上游每建立一個模型就漏 8 MiB

`experiments/memory_soak/` 的隔離測試（`ct2_isolation.py`，不經 kikitori、直接用 `ctranslate2`，`small` float16，各 12 輪）：

| 做法 | 顯示記憶體／輪 | 主記憶體／輪 |
|---|---|---|
| 建立 `ctranslate2.models.Whisper(...)` 後直接 `del`（不推論） | 0.0 | +0.02 MiB |
| 建立＋一次 GPU 推論，再 `del` | **+8.0 MiB** | +7.8 MiB |
| 同上，`del` 前先 `unload_model()` | **+8.0 MiB** | +8.5 MiB |
| **只建立一次**，每輪 `load_model()` → 推論 → `unload_model()` | **0.0** | **−0.04 MiB** |

- 漏的量與模型大小無關（tiny 與 large-v3 一樣），所以不是權重沒放，而是「每個模型物件」的固定成本。
- 推測是第一次推論時在模型工作執行緒上建立的 CUDA 資源（cuBLAS handle、stream）沒有隨物件銷毀。這點**沒有直接驗證**（只有二進位檔），屬上游行為。
- 引擎每個 session 建立 2 個模型 → 每次開始字幕 +16.8 MiB 顯示記憶體、+18–21 MiB 主記憶體，永遠累積到引擎重啟。

### 做法：不銷毀物件，卸載後放回池中

最後一列就是解法：物件只建立一次、重複使用。`src/kikitori/asr/whisper.py` 的 `ModelPool`（全域實例 `MODEL_POOL`）：

```text
load_transcriber()
  └─ MODEL_POOL.acquire(key, create)
       ├─ 池中有同 key 的閒置物件 → load_model()（權重載回 GPU）→ 用它
       │     載入失敗 → 記錄、丟掉這個物件，改 create()
       └─ 沒有 → create()（建立新物件）

WhisperTranscriber.close()          （session 結束時）
  ├─ 先等進行中的推論結束（最多 2 秒；逾時就不放回，交給參照計數）
  └─ MODEL_POOL.release(key, model)
       ├─ unload_model()（權重離開 GPU）
       ├─ 卸載後 model_is_loaded 仍為 True → 不放回（見下方）
       └─ 放回池中；超過 4 個閒置物件時丟掉最久沒用的（LRU）
```

設計上的幾個決定：

- **鍵**（`ModelKey`）是建立後不能改的參數：模型資料夾的實際路徑（`realpath` + `normcase`，短檔名與大小寫寫法都對到同一個鍵）、裝置、裝置編號、精度、執行緒數。
- **池裡只放卸載過的物件**：權重不在記憶體裡，只剩執行環境（約 8 MiB 顯示記憶體與一條 CTranslate2 工作執行緒）。上限 `MAX_IDLE_MODELS = 4`：使用者實際用過的組合很少超過幾個；被丟掉的那個之後再用到，代價只是和沒有池時一樣漏一次。
- **`unload_model()` 會「默默不做」**：查 CTranslate2 4.8.5 原始碼（`python/cpp/replica_pool.h`）發現，模型還有進行中的推論、或拿不到鎖時，它直接返回而不丟例外。所以放回前檢查 `model_is_loaded`，仍是 True 就不放回——否則池裡會留著一份完整的權重。
- **兩條路徑共用一個物件時**（例如泰語的即時與完整都用 large-v3），由 `_ModelLease` 計數，兩個 transcriber 都關閉才歸還一次。
- **刪除模型前先清池**：`load_model()` 會從模型資料夾重新讀檔，所以 `models.delete` 先 `MODEL_POOL.evict_under(模型資料夾)`；引擎結束時 `MODEL_POOL.clear()`。
- CPU 上也照樣放進池：實測卸載確實釋放權重（`tiny`：675 → 527 MiB，再 5 輪載入／卸載沒有累積；`docs/specs/asr.md`「模型物件池」）。

單元測試用假的 `ctranslate2` 模組計數建立、載入、卸載的次數（`tests/test_asr_model_pool.py`），不需要 GPU。

### 釋放的順序

`engine/server.py` 送出 session 結束狀態（`idle`／`error`）的順序是：

```text
停止 metrics 執行緒 → 釋放元件（release_components，最多等 5 秒）→ 送出 idle／error
```

為什麼先釋放再回報：UI 一收到 `idle` 就可能讓使用者再按「開始」。如果舊模型還在 GPU 上，新舊兩份同時存在，12 GB 的卡就可能放不下。
釋放在另一條執行緒（`session-<id>-release`）上做，最多等 `RELEASE_TIMEOUT = 5.0` 秒：推論卡住時不能讓整個引擎卡住，逾時就記 warning、照樣回報結束；釋放完成前 `models.delete` 仍回 `model_in_use`。

---

## 3. 怎麼量「有沒有漏」

`experiments/memory_soak/` 以 stdio 協定驅動**真的**引擎（真 Whisper on GPU、真擷取、真 Ollama），反覆開始／停止，每秒取樣（`monitor.py`）。

### 量什麼

| 指標 | 怎麼取 | 為什麼是它 |
|---|---|---|
| 主記憶體 | 行程樹的 **Private Bytes**（`K32GetProcessMemoryInfo` 的 `PrivateUsage`） | 工作集（Working Set）會被系統換出而變小，看起來像釋放了 |
| 每個行程的顯示記憶體 | Windows 效能計數器 `\GPU Process Memory(pid_<PID>_*)\Dedicated Usage` ＋ `Shared Usage` | WDDM 下 `nvidia-smi` 拿不到每個行程的用量；顯示記憶體不夠時 WDDM 會把配置降到共用記憶體，只看 dedicated 會誤以為釋放了 |
| handle 數、執行緒數 | 同上 API | 漏一條執行緒、一個 socket，每輪就 ≥ 1 |
| 翻譯請求 | 本機計數代理（`proxy.py`）：引擎 → 代理 → Ollama | 確認「只顯示原文」時真的 0 個請求、保溫的間隔正確；不受同機其他 Ollama 客戶端干擾 |

### 怎麼判定

- **排除前 2 輪暖機**：CUDA context、cuBLAS、配置器快取、Python 模組匯入都只在第一次出現，算進去會誤判成漏。
- 「結束後」的值：session 送出 `idle` 後等 12 秒，取最後 4 秒的中位數。
- 看**每輪的斜率**而不是單一數字：結束後的「底」如果每輪都往上，就是漏。情境 1 的底是 239.5、255.5、271.5 … 423.5 MiB，每輪剛好 +16——這種規律直接指向「每個 session 固定漏一點」。
- 每個門檻旁邊寫了理由（`harness.py`），例如顯示記憶體 ≤ 16 MiB／輪（v0.1.0 的洩漏是每輪 2.6 GB；配置器的快取有上限，不會造成持續斜率）。看過資料後放寬的門檻也記在 README。
- **找到現象後做隔離測試**：上面那張 `ct2_isolation.py` 的表把問題縮小到「CTranslate2 本身」，排除了 kikitori 的程式碼、執行緒與配置器。

### 干擾要記錄下來

測試期間同一台機器有另一個實驗在跑 Ollama。README 的「干擾」一節逐項標出哪些數字受影響（例如長時間情境的顯示記憶體被 WDDM 降級）、哪些不受影響（引擎自己行程的計數器）。看結果時先看這一節。

---

## 4. 找到的 3 個 bug

### Bug 1：開裝置失敗的 session 把整組模型留在 GPU

- **現象**：選的聲音裝置格式正確但不存在（被拔掉、清單過時），按「開始」→ 載入模型 → 開裝置失敗 → 回報錯誤。但顯示記憶體 0 → 3024 MiB 不回落，主記憶體 522 → 4260 MiB；再試一次又多 2.5 GB。其他錯誤路徑都正常（情境 6）。
- **原因**：擷取執行緒把開裝置的錯誤存在 `self._open_error`，`start()` 再 `raise` 同一個物件。重新丟出時 Python 把目前的 frame（`Pipeline.start` → 它的 `self` → 元件 → 模型）接到例外的 traceback 上，而 source 又保存著這個例外：`source → 例外 → frame → pipeline → source` 成了循環。只用 CPU、假元件的 `repro_open_error_cycle.py` 重現：session 失敗後模型仍存活，`gc.collect()` 後才釋放。
- **修法**：第 1 節的兩招——`detached_copy()` 丟出新的複本、`_clear_tracebacks()` 記錄後清掉 traceback；再加上 session 一律在結束時釋放元件。
- **回歸測試**：`tests/test_engine_capture_errors.py` 的 `StartFailureReleasesComponentsTest`：**`gc.disable()`**，用 `weakref` 觀察元件，斷言開裝置失敗後元件只靠參照計數就被釋放。關掉 `gc` 是重點——否則測試可能剛好遇到 `gc` 而通過。

### Bug 2：每建立一個 CTranslate2 模型物件就漏約 8 MiB

- **現象**：start／stop 12 輪，每輪 +16.8 MiB 顯示記憶體、+18.4 MiB 主記憶體；換模型組合也一樣，與模型大小無關（情境 1、2）。比 v0.1.0 的每輪 2.6 GB 小兩個數量級，但會無限累積：一天開關 50 次約 0.85 GB 顯示記憶體。
- **原因**：上游行為（第 2 節的隔離測試）。
- **修法**：模型物件池（第 2 節）。
- **回歸測試**：`tests/test_asr_model_pool.py`（假 `ctranslate2`，計數建立與載入次數）。

### Bug 3：回應帶 `Connection: close` 時，Ollama 檢查誤判「連不上」

這不是漏記憶體，而是相反——**資源釋放得太早**。

- **現象**：計數代理一開始用 HTTP/1.0 + `Connection: close`，引擎的 Ollama 版本檢查就回報連不上，跳過了翻譯模型檢查（情境 6 第一次執行中止）。使用者在 Ollama 前面放了會關連線的反向代理時也會遇到。
- **原因**：`hardware/ollama.py` 為了能取消下載，用自己的 socket 替身 `_PollingSocket` 交給 `http.client`。`http.client` 看到 `Connection: close` 會在 `getresponse()` 裡就關閉連線，之後才讀內容。標準的 `socket.makefile()` 以參照計數延後真正的關閉，替身沒有，於是讀內容時 socket 已經關了（WinError 10038）。
- **修法**：`_PollingSocket` 記錄還沒關閉的讀取器；`close()` 只做標記，最後一個讀取器關閉時才真的關 socket（仿標準庫的做法）。回應沒建成時另有 `abort()` 關掉所有讀取器，不靠 `gc`。
- **回歸測試**：`tests/test_hardware_ollama.py` 的 `test_version_with_connection_close`、`test_pull_with_connection_close`，以及 socket 何時關閉的幾個測試（`test_socket_closes_after_the_last_reader` 等）。

---

## 5. 還沒確認的事

- **修正後還沒有在實機重跑壓力測試**：預期情境 1（`cycles=20`）的顯示記憶體斜率 ≈ 0（`docs/specs/asr.md`）。`experiments/memory_soak/README.md` 的數字都是修正**之前**的。
- **長時間 session 結束後多留約 180 MiB 主記憶體**（README #4）：只跑了 5 分鐘、而且受干擾，還不知道是洩漏還是快取。要在 GPU 空閒時跑完整的 20 分鐘情境 4。
- 共用權重（一個物件、兩個 worker）每個 session 的成本沒有單獨量；第二個 worker 的 512 MiB 也是估計值。
- 桌面程式本身的長時間測試（情境 7，WebDriver）沒有跑過。

---

## 6. 寫程式時的檢查清單

- 持有 GPU／大記憶體的物件：誰負責 `close()`？在**所有**結束路徑（正常停止、錯誤、載入中被停止、開裝置失敗）都會呼叫嗎？
- 例外存起來、之後再丟：丟的是新複本，還是同一個物件？記錄之後有清 traceback 嗎？
- 回呼（callback）參照了擁有它的物件嗎？結束時有切斷嗎？
- 回報「結束」之前資源已經放掉了嗎？
- 測試釋放時，記得 `gc.disable()` ＋ `weakref`，確認只靠參照計數就會釋放。
- 第三方函式庫的「卸載」API 真的卸載了嗎？讀它的原始碼或量一次（`unload_model()` 的例子）。
