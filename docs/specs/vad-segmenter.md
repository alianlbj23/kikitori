# Spec: vad-segmenter（VAD 切句模組）

> 狀態：已確認（2026-11-05）。
> 依據：`reports/VAD 與句尾判斷技術調查.md`、`research_notes/VAD 與句尾判斷技術調查/`（特別是 `silero_streaming_state.md`）、`experiments/pipecat_vad/README.md`。

## 前提假設

1. 輸入一律是 16 kHz、單聲道、int16 PCM。降混音與重採樣（例如 48 kHz 立體聲 → 16 kHz 單聲道）由之後的「音訊擷取」模組負責，不在本模組範圍。
2. 先服務情境 A（觀看媒體）；情境 B（面對面口譯）之後透過同一套介面接上，本次不實作。
3. API 為同步呼叫（餵一段音訊、回傳事件列表），執行緒或 asyncio 的安排留給呼叫端。
4. 規格放在 `docs/specs/`，之後每個模組一份，檔名用模組 id。
5. pipecat 只留在 `experiment-vad` 依賴群組供舊實驗重跑，正式程式碼不使用。
6. Silero 模型檔打包進套件；之後較大的 ASR 模型另做執行時下載，不在本規格範圍。

## Objective

把連續的音訊切成「一句一句」交給語音辨識，並輸出三種事件：

| 事件 | 時機 | 內容 |
|---|---|---|
| `UtteranceStarted` | 確認開始有人聲 | 句子起點樣本位置（含前置緩衝） |
| `UtteranceAudio` | 句子進行中，每處理完一個 32 ms 視窗 | 這段新增的音訊與其樣本位置 |
| `UtteranceEnded` | 決定結束這句 | 起點、終點樣本位置、結束原因（`pause` / `max_length` / `external` / `flush`） |

要解決的已知問題（來自實驗與調查）：

- pipecat 每約 2 秒在 QUIET 時重置 Silero 狀態，實驗中造成整句漏抓 → 重置策略改為可設定，用評測決定預設值。
- pipecat 的時間計算用 `time.monotonic()`，離線比即時快餵資料時會算錯 → 全部改用樣本計數。
- pipecat 帶來約 290 MB 依賴，且需覆寫私有方法 → 直接用 `onnxruntime` 跑 Silero 官方 ONNX 模型。

### 元件

1. **`VoiceModel`（介面）**：「給一個 512 樣本視窗、回傳人聲機率」與 `reset()`。模型層可替換：`Segmenter` 只依賴這個介面，之後要換 FireRedVAD 等模型時不必改切句邏輯；測試也用假的實作餵腳本化機率。
2. **`SileroVad`**：`VoiceModel` 的預設實作，包裝 Silero VAD v6 官方 ONNX 模型。推論細節依官方 `OnnxWrapper`（見 `silero_streaming_state.md`）：
   - 每次輸入固定 **512 樣本**（16 kHz，32 ms），不是 512 就丟 `ValueError`（原始 ONNX 不會自己檢查）。
   - 在前面接上**前一個視窗的最後 64 樣本**，實際送進模型的輸入共 **576 樣本**；第一個視窗前接 64 個零。
   - 模型狀態 `state` 的形狀為 **`(2, 1, 128)`** float32，每次推論後寫回，跨呼叫保存。
   - `reset()` 同時把 state 與 64 樣本前文清成零。
   - 建構參數 `context_samples`（預設 64）只供診斷用：評測時設為 0 以重現「沒接前文」的情況。
3. **`Segmenter`**：把任意長度的輸入累積成 512 樣本視窗，以遲滯門檻判斷：
   - 機率 ≥ `threshold` 且持續達 `min_speech_ms` → 句子開始（起點往前含 `pre_roll_ms` 緩衝）。
   - 機率 < `neg_threshold` → 進入「可能結束」，交給句尾判斷器決定是否真的結束。
4. **`EndpointPolicy`（介面）**：根據目前句長與已靜音時間決定是否結束。本次只實作 `PauseEndpoint`：靜音達 `pause_secs` 或句長達 `max_secs` 就結束。之後的 Smart Turn、文字句尾判斷都實作同一個介面。
5. **外部提早結束介面**：`Segmenter.request_end()` 讓外部（例如之後 ASR 的標點模型、Smart Turn）要求「在下一個視窗結束目前這句」，事件的結束原因為 `external`。沒有進行中的句子時呼叫無效果。
6. **`ResetPolicy`（可設定，不寫死預設）**：控制何時重置模型狀態。三個選項：
   - `(a) STREAM_START`：只在換新串流時重置（官方建議的最低做法）。
   - `(b) AFTER_UTTERANCE`：除了 (a)，另外在每句結束後重置（維護者在 discussion #791 回答 "Ideally yes"）。
   - `(c) AFTER_LONG_NON_SPEECH`：除了 (a)，另外在連續非人聲超過 `reset_after_non_speech_secs`（預設 2.0 秒）且最近 5 個視窗機率都 < 0.1 時重置；之後每再經過同樣長度的非人聲才會再重置一次。
   - 用評測音軌比較三種之後再決定預設值；在那之前建構 `Segmenter` 時必須明確指定。
   - **評測結果（2026-11-05）：預設為 `AFTER_LONG_NON_SPEECH`**。理由與數據見下方「實作與評測結果」與 `experiments/silero_reset/README.md`。

### 預設參數（可調）

| 參數 | 預設 | 來源 |
|---|---|---|
| `threshold`（開始） | 0.5 | Silero 官方預設 |
| `neg_threshold`（結束） | `threshold − 0.15`（= 0.35） | Silero 官方預設；隨 `threshold` 連動 |
| `min_speech_ms` | 250 | Silero 官方預設 |
| `pre_roll_ms` | 200 | 使用者確認：避免切掉句首第一個音 |
| `pause_secs` | 0.6 | 本專案實驗：比 0.2 更接近一句字幕 |
| `max_secs` | 10.0 | 使用者確認，之後依實測調整 |
| `reset_policy` | `AFTER_LONG_NON_SPEECH` | 評測結果（2026-11-05） |
| `reset_after_non_speech_secs` | 2.0 | 僅 `AFTER_LONG_NON_SPEECH` 使用；仿 pipecat 1.14.0，評測時可調 |

### 模型打包

- **來源**：官方 tag `v6.2.3`（commit `5cd7945`）的 `src/silero_vad/data/silero_vad.onnx`，實作時下載一次並計算 SHA-256。
- **位置**：`src/kikitori/vad/models/silero_vad.onnx`，隨套件發布；用 `importlib.resources` 讀取，不依賴工作目錄。
- **版本與雜湊**：模型版本、來源網址、SHA-256 寫成程式常數，並記錄在 `models/README.md`。載入時驗證 SHA-256，不符就丟出錯誤。
- **授權**：Silero VAD 為 MIT；把其授權全文（含 "Copyright (c) 2020-present Silero Team"）放在 `models/LICENSE-silero-vad`，一起打包進套件。
- **更新模型**：換版本時同時更新檔案、常數、README 與授權檔，並重跑評測。

## 評測

### 素材

| 素材 | 用途 | 存放 | 出處與授權 |
|---|---|---|---|
| 程式產生的白噪音、粉紅噪音（固定亂數種子） | 單元測試、快速檢查 | 產生器程式碼進 repo；音訊執行時產生 | 本專案自產 |
| MUSAN 的 `music/` 與 `noise/` | 正式評測、調參數 | 只在本機，`.gitignore` 排除 | OpenSLR SLR17，CC BY 4.0；本機資料夾內放出處說明檔 |
| 動漫、VTuber 語音片段 | 正式評測的人聲來源 | 只在本機（沿用 `experiments/pipecat_vad/data/`，已被 git 忽略） | 各權利人所有，只在本機使用 |

- 不使用 OpenBMAT：Zenodo 標示 CC BY 4.0，但 UPF 較早的公告只允許非營利用途，授權說法衝突；且 Zenodo 只有論文 PDF，未直接提供音訊。
- 官方承認高音、卡通聲音、歌聲是 Silero 的弱點，所以正式評測必須包含動漫、VTuber 片段，以及 MUSAN 中含人聲演唱的音樂。

### 音軌版本

每種背景素材（白噪音、粉紅噪音、MUSAN 音樂、MUSAN 雜音）都做兩種版本：

1. **間隔填入**：把句子之間的數位靜音換成背景素材，人聲本身不變。測試「背景聲會不會被當成人聲」以及重置時機的影響。
2. **人聲底下疊加**：背景素材疊加在整條音軌（含人聲底下），SNR 取 20、10、5 dB 三個等級。測試「有背景聲時能不能抓到人聲、會不會提早切斷」。

正確答案（每段人聲的起訖樣本位置）沿用 `ground_truth.json` 的格式，混音時一併輸出。

### 指標

| 指標 | 定義 |
|---|---|
| 逐格 ROC-AUC | 每個 32 ms 視窗的人聲機率 vs. 該視窗是否在正確人聲區間內；用 numpy 以排名法計算，不新增依賴 |
| 誤報率 | 在 `threshold` 下，非人聲視窗被判為人聲的比例 |
| 漏報率 | 在 `threshold` 下，人聲視窗被判為非人聲的比例 |
| 開始偵測延遲 | 每段正確人聲的起點到對應 `UtteranceStarted` 的時間（ms），報告中位數與 P90 |
| 提早切斷次數 | `UtteranceEnded` 落在某段正確人聲內部、且距該段終點超過 100 ms 的次數 |
| 漏抓段數 | 沒有任何偵測句子與之重疊達一半長度的正確人聲段數 |
| 推論時間 | 每個 32 ms 視窗的平均推論時間 |

### 前文檢查

評測加一組「`context_samples=0`（沒接前文）」的對照：在人聲區間內的平均機率若低於正常設定的一半，判定為「機率異常偏低」並在報告中標出。這用來證明接前文的必要性，也作為之後換模型版本時的回歸檢查。

### 互動選單工具

評測相關操作集中在一個互動選單工具（`experiments/silero_reset/menu.py`），用標準庫 `input()` 做編號選單，不靠指令參數、不新增依賴。選項：

1. **下載 MUSAN**：
   - 先顯示來源網址、檔案大小（約 11.09 GB，單一壓縮檔）、所需磁碟空間、授權（CC BY 4.0），使用者確認後才開始。
   - 用標準庫 `urllib` 下載，設定連線與讀取 timeout；支援中斷後續傳（HTTP Range），顯示進度。
   - 下載完成後只解壓 `music/` 與 `noise/`，並寫入出處說明檔；完成後詢問是否刪除壓縮檔。
   - 存放於 `experiments/silero_reset/data/musan/`（已加入 `.gitignore`）。
2. **產生評測音軌**：用現有的動漫、VTuber 音軌與可用的背景素材，產生上述兩種版本；MUSAN 尚未下載時只產生噪音版本並提示。
3. **執行評測**：比較三種重置策略與前文對照，輸出指標表。
4. **離開**。

## Tech Stack

- Python 3.12，uv 管理
- 新增 runtime 依賴（使用者已同意；版本加入前查當時的穩定版並確認後固定）：`onnxruntime`、`numpy`
- 模型：Silero VAD `silero_vad.onnx`，官方 tag `v6.2.3`（commit `5cd7945`），MIT 授權，打包進套件
- 不使用：PyTorch、pipecat
- 評測工具只用標準庫（`wave`、`tarfile`、`urllib`）與 `numpy`

## Commands

```text
測試：      uv run python -m unittest discover -s tests
Lint：      uv run ruff check
評測選單：  uv run python -I experiments/silero_reset/menu.py
打包檢查：  uv build（確認 wheel 內含模型與授權檔）
```

## Project Structure

```text
src/kikitori/vad/
  __init__.py          → 對外匯出事件、Segmenter、設定
  model.py             → VoiceModel 介面
  silero.py            → SileroVad：ONNX 推論、前文、state、reset、模型雜湊驗證
  segmenter.py         → Segmenter、ResetPolicy、事件 dataclass、request_end
  endpoint.py          → EndpointPolicy 介面與 PauseEndpoint
  models/
    silero_vad.onnx    → 官方模型檔（v6.2.3）
    LICENSE-silero-vad → Silero VAD 的 MIT 授權全文
    README.md          → 模型版本、來源網址、SHA-256
src/kikitori/evaluation/
  noise.py             → 白噪音、粉紅噪音產生器（固定種子）
  mixing.py            → 間隔填入、依 SNR 疊加，輸出正確答案
  metrics.py           → ROC-AUC、誤報率、漏報率、開始延遲、提早切斷、漏抓
  wav.py               → 以標準庫 wave 讀寫 16 kHz 單聲道 16-bit WAV（檢查格式）
tests/
  test_vad_segmenter.py → 以腳本化機率測試事件邏輯、重置策略、request_end
  test_silero_vad.py    → 雜湊驗證、輸入驗證、前文與 state 的串接、合成訊號
  test_evaluation.py    → 噪音產生器、混音、指標計算
  test_menu.py          → 選單流程與 MUSAN 下載（本機假伺服器，不連外網）
experiments/silero_reset/
  menu.py              → 互動選單工具
  musan.py             → MUSAN 下載、續傳、部分解壓
  evaluate.py          → 產生音軌、跑三種重置策略與前文對照、輸出指標表
  README.md            → 評測結果與結論
  data/                → 本機資料（.gitignore）
docs/specs/vad-segmenter.md → 本文件
```

## Code Style

沿用專案規則：模組開頭說明架構角色、對外介面寫 docstring、全面型別標註、註解寫「為什麼」。

```python
"""VAD 切句：把 16 kHz 單聲道音訊切成一句一句，交給語音辨識。

位於音訊擷取之後、語音辨識之前；時間一律用樣本計數，不用牆鐘時間。
"""

from dataclasses import dataclass
from typing import Literal

EndReason = Literal["pause", "max_length", "external", "flush"]


@dataclass(frozen=True)
class UtteranceEnded:
    """一句話結束。樣本位置以串流開頭為 0。"""

    start_sample: int
    end_sample: int
    reason: EndReason
```

- 外部輸入（音訊陣列、設定值、下載回應）先驗證型別、dtype、長度與範圍，錯誤丟 `ValueError` 並附上收到的值。
- 網路 I/O 一律設 timeout，失敗時錯誤訊息包含網址與存放路徑。

## Testing Strategy

- 框架：`unittest`，放在 `tests/`；全部測試不需要網路，也不需要本機的版權音訊。
- **事件邏輯（假模型、腳本化機率）**：
  - 正常路徑：開始 → 音訊更新 → 因 `pause` 結束；句長超過 `max_secs` 時因 `max_length` 結束；`flush()` 以 `flush` 結束；`request_end()` 以 `external` 結束。
  - 短於 `min_speech_ms` 的人聲不觸發開始；`neg_threshold` 與 `threshold` 之間的機率不改變目前狀態（遲滯）。
  - 同一段音訊用不同大小分批餵入（例如每次 160、512、1000 樣本），事件與樣本位置完全相同。
  - 三種 `ResetPolicy` 各自在預期時機、預期次數呼叫 `reset()`；未指定 `ResetPolicy` 時建構失敗。
- **Silero 包裝**：
  - 前文與 state 串接（以會記錄輸入的假 ONNX session 驗證）：每次送進模型的輸入是 576 樣本、前 64 樣本等於上一個視窗的最後 64 樣本、第一個視窗前 64 樣本為零；state 為 `(2, 1, 128)` 且上一次輸出的 state 會成為下一次輸入；`reset()` 後前文與 state 皆歸零。
  - 錯誤路徑：視窗不是 512 樣本、dtype 不是 int16／float32、陣列不是一維、模型檔雜湊不符 → 丟出明確錯誤。
  - 真模型：靜音、正弦波、白噪音、粉紅噪音整段機率都低於 `threshold`；`reset()` 後同輸入得同輸出。
- **評測模組**：噪音的 RMS 與頻譜斜率（粉紅噪音約 −3 dB/octave）、混音後 SNR 誤差、各指標用手算的小例子驗證。
- **選單與下載**：用 `http.server` 在本機起假伺服器提供小型假壓縮檔，驗證確認後才下載、續傳、只解壓指定資料夾、timeout 與 HTTP 錯誤的處理；選單輸入用模擬的 `input()` 驅動。
- **真實語音與前文對照**：受版權限制不進單元測試，由評測選單在本機執行。

## Boundaries

- **Always**：跑測試與 lint 全部通過才算完成；時間計算用樣本計數；驗證外部輸入；網路 I/O 設 timeout；錯誤要 log 並往上丟。
- **Ask first**：`onnxruntime`、`numpy` 以外的新依賴；固定版本號前確認；刪除或修改 `experiments/pipecat_vad/` 與 `experiment-vad` 依賴群組；開始下載 MUSAN 前在選單中確認。
- **Never**：在 `src/kikitori/` 匯入 torch 或 pipecat；用牆鐘時間計算音訊位置；空的 `except`；把有版權的語音檔、MUSAN 資料或 Silero 以外的模型檔 commit 進 repo；使用未通過 SHA-256 驗證的模型檔；在 `ResetPolicy` 評測完成前寫死預設值。

## Success Criteria

1. 單元測試與 lint 全部通過，測試涵蓋上述正常與錯誤路徑，且不需要網路。
2. `src/kikitori/` 不匯入 torch、pipecat（以測試檢查）。
3. 白噪音、粉紅噪音的純噪音音軌在三種重置策略下都產生 0 句。
4. 評測選單能在本機產出所有音軌版本（原始、噪音間隔填入、噪音疊加、MUSAN 間隔填入、MUSAN 疊加）的指標表，包含上述全部指標與前文對照。
5. 原始動漫、VTuber 音軌上，至少一種重置策略能偵測到全部 12 段真實語音。
6. 每個視窗平均推論時間 < 1 ms（實驗中 pipecat 為 0.4 ms）。
7. 評測結果與選定的 `ResetPolicy` 預設值寫入 `experiments/silero_reset/README.md`，並更新本規格。
8. `uv build` 產生的 wheel 內含 `silero_vad.onnx` 與 `LICENSE-silero-vad`，從 wheel 安裝後不需網路即可載入模型。

## 已決定事項（2026-11-05 使用者回答）

1. 依賴：同意新增 `onnxruntime`、`numpy`。
2. 模型檔：Silero VAD v6 ONNX 打包進套件，固定版本、記錄 SHA-256、保留 MIT 授權聲明；之後較大的 ASR 模型再做自動下載。
3. 推論：512 樣本 + 前接 64 樣本（共 576），state `(2, 1, 128)` 跨呼叫保存；測試要能偵測出沒接前文時機率異常偏低。
4. 重置策略：(a)(b)(c) 三種做成可設定選項，評測後再決定預設值。
5. 門檻：開始 0.5，結束 = 開始 − 0.15。
6. `max_secs` 10 秒、`pre_roll_ms` 200 ms、實作 `UtteranceAudio` 事件。
7. 評測素材：程式產生的白噪音／粉紅噪音（進 repo）＋ MUSAN（只在本機、`.gitignore`、標註出處），兩者都做「間隔填入」與「人聲底下疊加」；另加本機動漫、VTuber 片段。
8. MUSAN 下載做成互動選單工具的選項，不靠指令參數。
9. 模型層可替換（`VoiceModel` 介面）；預留外部提早結束介面（`request_end()`）。

## 實作與評測結果（2026-11-05）

### 實作時確定的細節

- 依賴：`onnxruntime==1.31.1`、`numpy==2.5.4`（皆有 Python 3.12 的 Windows、Linux x86_64／aarch64、macOS arm64 wheel；onnxruntime 1.31 已不提供 macOS x86_64 wheel）。
- 模型：v6.2.3 的 `silero_vad.onnx`，SHA-256 `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3`。實際載入確認輸入為 `input [B, N]`、`state [2, B, 128]`、`sr`（int64 純量），輸出為 `output [B, 1]` 與 `stateN`，與本規格一致。`uv build` 不需調整設定，wheel 即含模型與授權檔。
- `SileroVad` 依官方 `OnnxWrapper`：int16 固定除以 32768、session 單執行緒；另外拒絕含 NaN／inf 的 float32 視窗，並檢查模型輸出形狀。
- `neg_threshold` 預設 `max(threshold − 0.15, 0.01)`（與官方相同的下限）。
- `UtteranceEnded.end_sample` = 這句已送出音訊的結尾：同一句的 `UtteranceAudio` 首尾相接，合起來剛好是 `[start_sample, end_sample)`。因此 `pause` 結束時包含約 `pause_secs` 的靜音尾巴。
- 前置緩衝不越過上一句的結尾，同一段音訊不會交給兩句；`max_length`／`external` 切斷後人聲持續時，下一句從切點接續。
- `request_end()` 在候選期間（人聲尚未持續到 `min_speech_ms`）也沒有效果，只作用一次。
- `flush()` = 串流結束：剩餘不足一個視窗的樣本有進行中句子時併入最後一段音訊，之後回到新串流（樣本位置歸零、模型 reset）。
- (b) 在 `pause`／`max_length`／`external` 結束後重置；(c) 的「非人聲」指沒有進行中句子、也沒有候選人聲的視窗（句尾停頓仍屬於句子）。
- 指標：視窗以中心點判斷是否在正確區間內；開始延遲 = 送出 `UtteranceStarted` 時的串流位置 − 正確起點，對應的句子是與該段實質重疊（≥ min(250 ms, 段長一半)）的句子中最早的一句，若它在該段開始前就已確認（與前一段併成一句）則不列入。
- 評測另做「長間隔」版面（同一批片段，句間 3–4 秒）：原版面最長間隔 1.5 秒，(c) 不會觸發，無法比較。MUSAN 音樂依 `ANNOTATIONS` 的 vocals 欄分成有人聲演唱與純音樂兩種背景。間隔填入的背景音量以 SNR 10 dB 計。

### 評測摘要與預設值

- 選定 **`ResetPolicy.AFTER_LONG_NON_SPEECH`** 為預設（`reset_after_non_speech_secs` 2.0）：全部 84 條音軌 AUC 0.905、漏抓 42/504（(a) 43、(b) 49）、開始延遲中位數 470 ms／P90 820 ms（(a) 490／876、(b) 556／1440）。優勢集中在「長間隔的音樂背景」（純音樂 longgap 漏抓 (a) 9/48 → (c) 7/48、P90 1469 → 1021 ms；有人聲音樂 6/48 → 3/48）；代價在「長間隔的噪音背景」（MUSAN 雜音 longgap (a) 0/48 → (c) 2/48，白／粉紅 10/96 → 12/96）；沒有長間隔時與 (a) 相同。領先幅度小，之後有更多真實素材時應重新檢討。
- 原始動漫、VTuber 音軌三種策略都抓到全部 12 段；每視窗平均推論 0.18 ms；前文對照 84/84 條音軌判定「機率異常偏低」（不接前文時人聲區平均機率 ≤ 0.0021）。
- 評測素材：MUSAN 完整下載並只解壓 `music/`、`noise/`；音樂依 vocals 標註分成有人聲與純音樂兩種背景。

## 之後的方向（暫不實作）

- **情境 A（觀看媒體）**：在 ASR 階段加入標點／句尾模型，由文字判斷句子是否完整，透過 `request_end()` 或新的 `EndpointPolicy` 接回本模組。調查中可用的候選：1-800-BAD-CODE 47 語標點模型（Apache-2.0、ONNX，但沒有日文指標）；文獻顯示語意式切點可降低延遲約 23–53%，但證據來自語音搜尋或演講類素材，需自行驗證。
- **情境 B（面對面口譯）**：用電腦麥克風錄製對話實測 Smart Turn v3（BSD-2、ONNX、支援日英中），作為「停頓尚未達上限時的提早結束訊號」，不作為唯一依據；直接呼叫 smart-turn 官方的無狀態推論函式，避開 pipecat 以 `time.monotonic()` 計算緩衝的問題。
- 這兩項都已預留介面：`EndpointPolicy` 與 `request_end()`。
