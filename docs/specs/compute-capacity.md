# 規格：運算能力與負荷（可用的模型、會不會超出負荷、負荷圖）

> 2026-11-24。使用者要求：「不然我根本不知道我連上的這台裝置我可以使用甚麼樣的模型以及會不會超出負荷；不論本地或遠端都要圖形化目前計算量是否還在範圍內」。延續 `docs/specs/remote-compute.md`；介面文字要讓非資工背景的人看得懂。

## 目標

1. **連上之前就知道運算主機（B）能做什麼**：顯示卡名稱與記憶體、B 已下載哪些辨識模型、每個模型在 B 上「跑得動／可能吃緊／超出負荷」、B 建議的組合。
2. **A 可以選 B 上的模型**：只能選 B 已下載的；預設是「照運算主機的設定」。選擇存在 A 的設定，只影響 A 送去的 session，不改 B 的設定檔。
3. **負荷圖（本機與遠端都要）**：字幕進行中，左側欄顯示目前的運算負荷：顏色條、最近 2 分鐘的折線、白話狀態（「餘裕充足」「接近上限」「跟不上」）。遠端時顯示的是 B 的負荷。
4. **無介面主機**：選單新增「選擇模型」，列出各模型、標示建議與跑得動與否、哪些已下載。

不在範圍：多台 B、自動切換模型、翻譯模型（Ollama）的負荷量測（只顯示 B 用哪個翻譯模型）。

## 名詞

- **完整辨識忙碌度 `final_busy`**：最近 30 秒內，完整路徑（`final_transcriber.transcribe`）實際在算的時間比例，0–1。接近 1 表示完整辨識一直在忙，再多一點就會排隊。
- **即時倍率 `rtf`**：最近 10 句完整辨識的「計算秒數 ÷ 音訊秒數」中位數。小於 1 才跟得上。
- **待處理句數 `backlog`**：既有欄位。

## 一、引擎：負荷量測（本機與 B 都用）

- `pipeline/metrics.py` 新增 `BusyMeter`（時間窗 30 秒；記錄每次完整辨識的開始、結束；可注入時鐘）與 `RtfMeter`（最近 10 句，中位數）。`Pipeline._run_final` 量 `final_transcriber.transcribe` 的耗時與 `job.audio` 長度（16 kHz）。
- `PipelineMetrics` 新增 `final_busy: float | None`（還沒有完整辨識時 None）、`rtf: float | None`。
- GPU 使用量：`hardware/nvidia.py` 新增 `sample_gpu_usage(index, *, run, timeout=2.0) -> GpuUsage | None`，查 `memory.used,memory.total,utilization.gpu`。只在辨識用 CUDA 時，引擎的 metrics 執行緒每 5 秒取樣一次（失敗記 debug、該欄位 null，不影響 session）。
- `metrics` 事件新增欄位（全部可為 null；舊介面忽略未知欄位）：

| 欄位 | 型別 | 說明 |
|---|---|---|
| `final_busy` | number 0–1 \| null | 完整辨識忙碌度 |
| `rtf` | number ≥ 0 \| null | 即時倍率 |
| `gpu` | `{name, vram_used_mb, vram_total_mb, util_pct}` \| null | 用 CPU 或取樣失敗時 null |

- 遠端：B 的 metrics 原樣經協定送回（`_SERVER_EVENTS` 已含 metrics），A 的 `_check_metrics` 驗證並轉送新欄位（範圍：`final_busy` 0–1、`rtf` 0–1000、`vram_*` 0–1,048,576、`util_pct` 0–100、`name` ≤ 64 字）。A 送給介面的 metrics 同時帶 `rtt_ms`。

## 二、運算主機的能力 `HostInfo`

B 端組出（`remote/capacity.py`，純函式＋注入硬體偵測，結果快取 30 秒）：

```json
{
  "gpu": {"name": "NVIDIA GeForce GTX 1080 Ti", "vram_total_mb": 11264} ,
  "device": "cuda",
  "compute_type": "int8",
  "current": {
    "fast": "large-v3-turbo", "final": "large-v3",
    "translation": {"fast": "qwen3.5:4b", "final": "qwen3.5:9b"}
  },
  "recommended": {"fast": "large-v3-turbo", "final": "large-v3"},
  "models": [
    {"id": "large-v3", "downloaded": true, "need_mb": 2140, "fit": "ok", "fit_as_fast": "tight"}
  ],
  "translation_model": "qwen3.5:9b",
  "translation_models": ["qwen3.5:4b", "qwen3.5:9b"]
}
```

- `gpu`：沒有 NVIDIA 時 null，`device` 為 `"cpu"`。`recommended`：硬體建議（`hardware.recommend`），偵測失敗時 null。
- `models`：registry 的全部 ASR 模型。`need_mb` 依 B 的裝置與精度估算：CUDA 用 `approx_gpu_vram_int8_mb` 或 `approx_gpu_vram_mb`，CPU 用 `approx_cpu_ram_mb`。
- `fit`：以「這個模型當完整辨識，搭配 B 目前的即時辨識模型」的合計需求（同一個模型只算一次，沿用 `asr_vram_need_mb`／`asr_ram_need_mb`）對照可用量：CUDA 用 `vram_total_mb − 1024`（留給桌面與 CUDA 以外的程式）再扣掉同一台電腦上的翻譯模型（翻譯伺服器在 localhost 時，即時與完整翻譯模型的權重大小，同一個只算一次；不看 B 自己的翻譯開關，因為 A 開翻譯時用的是 B 的翻譯模型），CPU 用系統記憶體的 70%。合計 ≤ 80% → `ok`；≤ 100% → `tight`；更多 → `over`。偵測失敗、不知道可用量時 `fit` 為 null（介面顯示「無法判斷負荷」）。開字幕的握手只用快取的偵測結果，不重新偵測（probe 才偵測）。
- `fit_as_fast`（七追加）：同一套公式，改成「這個模型當即時辨識，搭配 B 目前的完整辨識模型」（同一個模型只算一次、扣同一台的翻譯模型、精度用即時路徑的設定）；A 的即時模型選單用它標「跑得動／可能吃緊／超出負荷」。不知道可用量時 null。
- 翻譯欄位（七追加：`current.translation`、`translation_model`、`translation_models`）只放 A 收得下的名稱：英數開頭、其餘英數或 `._:/-`、不含 `..`、≤ 200 字元（與 A 的 Rust／TS 同一條規則，B 的 `remote.protocol.is_portable_model_name`）。`current.translation` 任一個不符合（空字串＝沒設，可以）時整組 null，`translation_model` 不符合時 null，`translation_models` 略過不符合的。A 驗證時這三欄不合格只丟該欄，不丟整個 host。
- 介面另外標「建議」（= `recommended.final`）與「目前」（= `current.final`）。

## 三、協定（A ↔ B，`remote/protocol.py`）

- `hello` 新增 `intent: "session" | "probe"`（省略＝`session`）。
- **B 的順序改成先握手、後佔 GPU**：`_handle` 先 `_handshake`，`intent == "probe"` 時送 `welcome`（含 `host`）後以 1000 關閉，**不呼叫 `_claim`、不佔 GPU、B 忙碌時也回答**；`session` 才 `_claim`（忙碌時照舊送 `busy`）。
- `welcome` 新增 `host: HostInfo`（B 端偵測失敗時仍送，`gpu`／`recommended` 為 null）。A 驗證每個欄位（字串長度、列舉、數值範圍、`models` ≤ 32 筆），不合格整個 `host` 視為 null，不中斷連線。
- `session.start` 的 `settings` 新增可選的 `asr: {fast_model?, final_model?}`（`_SESSION_SETTINGS`）。B 只接受 registry 裡的 id 且已下載，否則 `status error` code `unknown_model`／`model_not_downloaded`。裝置、精度仍用 B 的設定。

## 四、A 端引擎與設定

- 設定 `compute.remote_fast_model`、`compute.remote_final_model`：字串，空字串＝照運算主機的設定（預設）。只驗證格式（registry id 或空），是否下載由 B 判斷。
- 遠端 session 開始時，非空的這兩個欄位放進 `session.start` 的 `settings.asr`。
- 新引擎方法 `remote.host_info {}`：用設定的 `compute.host`／`compute.port`、`intent: "probe"` 連一次，回傳 `{host: HostInfo}`。錯誤碼沿用：`remote_not_configured`、`remote_unreachable`、`remote_not_paired`、`remote_incompatible`、`remote_identity_error`；逾時 10 秒。
- Tauri 指令 `remote_host_info`（只進 `main.json`，結果再驗證一次）。

## 五、介面

- **左側欄「運算負荷」**（字幕進行中才顯示；本機與遠端都有）：
  - 等級（純函式 `app/src/lib/load.ts`）：`over`＝`backlog ≥ 3` 或 `final_busy ≥ 0.9` 或 VRAM ≥ 95%；`tight`＝`backlog ≥ 2` 或 `final_busy ≥ 0.7` 或 VRAM ≥ 85%；其餘 `ok`；還沒有資料為 `unknown`。
  - 負荷百分比＝max(`final_busy`、VRAM 使用率、`backlog / 4`)，上限 100%。
  - 畫面：色條（綠／黃／紅，深淺色主題都可讀）＋最近 120 筆的 SVG 折線＋一行白話：「餘裕充足」「接近上限」「跟不上：建議改用較小的模型」。下方小字：「顯示卡記憶體 6.1／11 GB・辨識速度 0.4 倍時間」。遠端時標題後加「（運算主機）」。
- **遠端角色的運算主機資訊**（左側欄與設定頁「遠端運算」）：連線前用 `remote_host_info` 取得，顯示「GTX 1080 Ti・11 GB」，以及模型選單：「照運算主機的設定（large-v3）」＋B 已下載的模型；每個附標籤「建議」「跑得動」「可能吃緊」「超出負荷」。選「超出負荷」的模型要再確認一次。未下載的模型列在選單外的說明：「要用其他模型，請在運算主機上下載」。
- 取得失敗時用白話顯示原因（找不到運算主機、還沒配對…），不擋開始。
- 13 種語言。

## 六、無介面主機（`remote/host.py`）

- 選單新增「選擇模型」：列出 `HostInfo.models`（編號、名稱、需要的記憶體、跑得動與否、已下載、建議、目前），選了未下載的就問要不要下載，再寫入 `asr.final_model`（即時模型不變）。
- 提供運算中按 `s` 顯示目前的負荷一行（`final_busy`、VRAM）。

## 測試

- Python：`BusyMeter`／`RtfMeter`（注入時鐘）、`sample_gpu_usage` 解析與失敗、`HostInfo` 的 fit 計算（CUDA／CPU、合計只算一次）、協定新欄位的驗證（正常與惡意值）、probe 不佔 GPU 且 B 忙碌時也回答、`session.start` 的 `asr` 覆寫（未下載、未知 id）、`remote.host_info` 正常與錯誤路徑、metrics 經遠端轉送、無介面主機選擇模型。
- TS：`load.ts` 等級與百分比、HostInfo 驗證、模型選單選項、折線資料。Rust：`remote_host_info` 結果驗證。

## 決策

- 負荷以「完整辨識忙碌度」為主：即時倍率在說話稀疏時看不出壓力，待處理句數只在已經跟不上時才變大；忙碌度能提早顯示「接近上限」。
- A 選的模型只影響 A 的 session，不改 B 的設定檔：B 的主人仍決定 B 自己的預設，且 A 不能叫 B 下載東西。
- probe 不佔 GPU：查詢能力不該打斷 B 正在進行的字幕。

## 七、從用戶端管理運算主機的模型（2026-11-24 追加）

> 使用者：「更換模型我想要在 client 端設定，然後變動到 server 端」。取代第四節「A 指定這場用的模型」：`compute.remote_fast_model`／`remote_final_model` 與 `session.start` 的 `asr` 覆寫移除，改成 A 直接改 B 的設定（持久）；B 的畫面會收到通知。

**協定（A ↔ B）**：`hello.intent` 新增 `"manage"`。B 握手後不佔 GPU，處理一個請求就關閉（1000）。

| A → B | B → A |
|---|---|
| `host.set_models {asr?: {fast_model?, final_model?}, translation?: {fast_model?, final_model?}}` | `host.updated {host: HostInfo}` 或 `host.error {code, detail}` |
| `host.download {model}` | `host.download_started {model}`，接著 `host.download_progress {model, downloaded_bytes, total_bytes, state: "downloading"|"done"|"error", error}` 直到 done／error（約每秒一則），然後關閉 |

- B 驗證：ASR id 必須在 registry 且已下載（`set_models`）；`download` 只接受 registry 的 ASR id；翻譯模型必須在 B 的翻譯伺服器已安裝清單裡。`host.error.code`：`unknown_model`、`model_not_downloaded`、`translation_model_missing`、`download_in_progress`、`settings_error`。A 斷線不中斷 B 的下載（B 自己的下載）。
- `HostInfo` 新增：`current.translation: {fast, final}`、`translation_models: [string] | null`（B 的翻譯伺服器已安裝的模型，≤ 64 筆；連不上時 null）。
- B 套用：寫進 B 的設定檔（`asr.fast_model`／`asr.final_model`、`translation.fast_model`／`final_model`），下一場生效（進行中的那場不變）。GUI 引擎送 `remote` 事件 `{kind: "host_changed", by: <A 的名稱>, asr: {fast, final}, translation: {fast, final}}`，B 的介面重讀設定並顯示「〇〇 把辨識模型改成 large-v3」。ASR 下載在 GUI 引擎走既有的 `models.download`（B 的介面也看得到進度）；無介面主機直接下載並在終端機印進度。

**A 端引擎方法**（錯誤碼：既有 `remote_*`，另加 `remote_unknown_model`、`remote_model_not_downloaded`、`remote_translation_model_missing`、`remote_download_in_progress`、`remote_error`）：

- `remote.host_set_models {asr?: {...}, translation?: {...}}` → `{host: HostInfo}`。
- `remote.host_download {model}` → `{started: true}`；進度以 `remote` 事件 `{kind: "host_download", model, downloaded_bytes, total_bytes, state, error}` 送出。
- Tauri：`remote_host_set_models`、`remote_host_download`（只進 `main.json`）。

**A 的介面**：模型頁在遠端模式改成「運算主機的模型」：主機摘要、即時與完整辨識模型選單（只列 B 已下載的，附「建議／跑得動／可能吃緊／超出負荷」）、B 還沒下載的模型列在下面並附「在運算主機下載」按鈕與進度條、翻譯模型選單（B 已安裝的）。選了就套用到運算主機（「超出負荷」要再確認）。左側欄的模型選單也改成這個行為（改 B 的完整辨識模型）。

## 八、當運算主機時的畫面（2026-11-24 追加）

> 使用者：「變成 server 端時畫面 UI 應該要有什麼變化，不然沒感覺」。

- **引擎**：B 在提供運算時，每秒送一次 `remote` 事件 `{kind: "serving", peer: {name, fingerprint, state} | null, session: {started_at_ms, sentences, mode} | null, metrics: {final_busy, rtf, backlog, gpu} | null}`（狀態改變時立即送一次；沒有連線時每 5 秒）。`sentences` 是這一場送出的完整字幕句數。
- **介面**（角色是「讓別台電腦用這台處理」時）：
  - 視窗頂端常駐標示「運算主機模式」（顏色與一般模式不同）。
  - 字幕頁上方的「運算主機」面板取代空白提示：狀態圓點與大字（「等別台電腦連進來」／「正在替〇〇處理・已處理 N 句・12:34」）、這台的運算負荷（與第五節同一個元件：色條、折線、白話）、目前用的辨識與翻譯模型、按鈕「配對新電腦」「停止提供運算」。
  - 有電腦連著時「開始」按鈕停用並說明「別台電腦正在使用這台的顯示卡」。
  - 收到 `host_changed` 時顯示通知。
- 不顯示 A 的字幕內容（只顯示句數），避免運算主機的人看到別人的對話。

**超出負荷時（2026-11-24 使用者補充）**：「client 端選的模型必須是 server 端可以負荷的；超出負荷要說，使用者執意要選還是可以選」。B 不因 `fit` 拒絕 `set_models`／`download`（只檢查 registry、已下載、已安裝）。介面：選項都列出、標示醒目；選「超出負荷」時跳確認，白話說明後果（字幕可能變慢、跟不上或無法開始），按「仍要使用」才套用；「可能吃緊」不確認、在選單下方提醒；目前組合超出負荷時模型區塊持續顯示警示。

## 九、三種模式的辨識度（2026-11-24 使用者追加）

> 使用者：「變成 client 端時 UI 也改變一下，讓使用者有自覺目前是本地端、client 端、server 端」。

- 視窗頂端常駐**模式列**，顏色＋圖示＋文字三者一起（不只靠顏色，色弱也分得出來）：
  - 本機：低調，圖示（單台電腦）＋「這台電腦處理」；主色維持原本的靛藍。
  - 用戶端：天藍；圖示（兩台電腦與箭頭）＋「使用運算主機：GTX 1080 Ti・延遲 3 ms」與連線圓點（連著／重連中／未連線）。
  - 運算主機：紫；圖示（伺服器）＋「運算主機模式：等別台電腦連進來」或「正在替〇〇處理・已處理 N 句」。
- 模式主色同時用在視窗邊緣的細框、左側欄選中的角色、「開始」按鈕。模式色刻意避開負荷圖的綠、黃、紅。
- 深淺色主題都要符合對比（文字 4.5:1）；切換模式時顏色以短轉場變化（尊重 `prefers-reduced-motion`）。
