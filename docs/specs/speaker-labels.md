# 規格：說話者標籤（多人說話時標出「誰說的」）

狀態：已實作（v0.3，2026-11-09）。實驗與數據：[experiments/speaker_diarization/README.md](../../experiments/speaker_diarization/README.md)。

## 目標

VTuber 連動、多人節目時，影片翻譯模式的每句字幕標出「說話者 1／2／3…」。使用者可以選「自動判斷人數」或「指定人數」，也可以替標籤改名。

- **使用者**：看日語多人直播的觀眾。
- **成功條件**（實驗資料，逐句標對的比例）：2 人 ≥ 0.9；3–4 人約 0.8。
  指定人數時，人數不會估錯。
- **非目標**：重疊說話、一句之中換人、同一人切換聲線、跨 session 記住同一個人。
  口譯模式已有「我／對方」，不另外分。OBS 文字檔輸出不加標籤。

## 做法（依實驗結果決定）

- **逐句**：每句的完整結果出來時，用整句音訊算一個說話者向量，再線上分群：
  和每個已知說話者的中心比餘弦相似度，最像的那個若 ≥ 門檻就歸它，否則開一個新的人。
  中心是該說話者所有向量的總和。
- **指定人數 N**：人數開到 N 就不再開新的人，之後每句都歸給最像的那個。
- **短句（< 1.0 秒）**：向量不可靠，不更新中心、不開新的人。最像的那個 ≥ 門檻，
  或指定人數且已經開滿時，標成那個人；否則不標。
- **模型**：3D-Speaker CAM++ zh-en（`3dspeaker_speech_campplus_sv_zh_en_16k-common_advanced.onnx`，
  Apache-2.0，28 MB，192 維），門檻 0.4。它最快（CPU 每句約 0.06 秒），2 人最準。
- **特徵**：Kaldi fbank，純 numpy 實作，不新增依賴。
  - 80 bins、25 ms／10 ms、povey 窗、preemph 0.97、去直流、20–7600 Hz、snip_edges=false、log 下限 float32 eps。
  - 輸入是 [-1, 1] 的波形，然後減去時間平均（global-mean）。
  - 實作與 kaldi-native-fbank 1.22.4 的最大誤差 1e-4；分群準確度與 sherpa-onnx 2.0.0 相同（見實驗 README）。
- **下載**：模型不打包，打開設定時才下載。從 Hugging Face `csukuangfj/speaker-embedding-models`
  固定 revision `0743f301363dec56491a490f6d6cbc9d67f9a3bf` 下載，驗證大小 28281164 與 SHA-256
  `aa3cfc16963a10586a9393f5035d6d6b57e98d358b347f80c2a30bf4f00ceba2`。
  沿用 `asr.download.download_file()`（續傳、逾時、雜湊、取消）。

## 介面

### 設定（`speakers`，不升 schema，缺少時用預設值）

| 欄位 | 型別 | 預設 | 說明 |
|---|---|---|---|
| `speakers.enabled` | bool | false | 影片翻譯模式是否標說話者 |
| `speakers.count` | int 2–6 或 null | null | null = 自動判斷人數；數字 = 指定人數 |

- **生效時機**：設定在 session 開始時讀取，執行中改設定要下一個 session 才生效（分群狀態屬於一個 session）。
- **模型未下載**：開了但模型沒下載時，字幕照常顯示、不帶標籤，並記一次 warning。

### 引擎協定

- **`subtitle` 事件**：多一個 `voice`（int ≥ 1），只出現在影片翻譯模式、設定開啟、
  而且這句有判斷結果的完整字幕上。即時字幕不帶。
- **`models.list`**：多一個 `speaker_model` 物件：
  `id, name, installed, download_bytes, installed_bytes, disk_bytes, path, license, license_url`。
  和 `furigana` 不同，沒有 `edition`、`version` 與 `in_use`。
- **`models.download`／`models.delete`**：`{"model": "speaker-embedding"}`。
  - 下載進度沿用 `download_progress`，`kind` 為 `"speaker"`。
  - 下載中刪除回 `model_in_use`。
  - session 執行中可以刪除：模型在 session 開始時已整個讀進記憶體，執行中的 session 不受影響。
  - 刪除後 `speakers.enabled` 一併關閉（同振假名），設定與畫面才一致。

### UI

- **設定**：設定頁的「說話者標籤」選單有「關閉／自動判斷人數／2／3／4／5／6 人」。
  打開時若模型沒下載，顯示下載按鈕與大小、授權（同振假名）。
- **標籤顯示**：主視窗逐字稿與浮動字幕在說話者改變時，句首顯示標籤 chip，
  規則同口譯模式（說話者改變時才標，顏色不是唯一線索）。
  chip 的顏色依名稱決定，所以改成同一個名字的兩個標籤會顯示成同一個人（等同合併）。
- **改名**：在主視窗逐字稿點標籤 chip 就能改名。名稱只存在這個 session，下一個 session 重新從「說話者 1」開始。
  主視窗透過 `overlay_set_speaker_names` 把名稱傳給浮動字幕。
- **匯出**：SRT、VTT、TXT 句首加上 `名稱: `，CSV 加 `speaker` 欄，與口譯模式相同。

## 模組

- **`kikitori.speakers`（新）**
  - `features.py`：fbank。
  - `embedding.py`：`SpeakerEmbedder`，onnxruntime CPU。
  - `clustering.py`：`OnlineSpeakers`。
  - `model.py`：spec、狀態、下載、刪除。
  - `labeler.py`：`VoiceLabeler`，把上面幾個接起來。
- **`pipeline`**
  - `PipelineComponents.voice_labeler`（可選）：`_run_final` 在過濾通過後呼叫 `label(audio)`。
  - `SubtitleUpdate.voice`。
  - labeler 出錯時記一次 warning，之後這個 session 不再標。
- **`engine`**：`models.*` 處理 `speaker-embedding`。
  factory 依設定與模型是否已下載建立 labeler。
  subtitle 事件只在 `voice` 有值時才帶這個欄位。
- **Rust `protocol.rs`**：驗證 `voice`（1–64，否則丟掉並 warn）。
  overlay 狀態多 `speaker_names`，以及 `overlay_set_speaker_names` 指令。
- **UI**：`types`、`validate`、`subtitles`、`speaker.ts`（`voiceLabels`、名稱與顏色）、
  overlay、main 的逐字稿與設定、`export`、13 種語系。

## 測試策略

- **Python unittest**
  - **fbank**：與 kaldi-native-fbank 產生並寫死的參考值比較，覆蓋固定訊號的前幾格與邊緣格。
  - **分群**：自動、指定人數、短句、門檻邊界。
  - **模型下載**：用假的 fetcher 測下載、雜湊錯誤、取消、刪除與連結防護。
  - **pipeline**：用假 labeler 測 voice 帶在完整字幕、不帶在即時字幕、口譯模式不標、
    labeler 例外只 warn 一次。
  - **設定**：預設值、範圍、未知欄位。
  - **engine**：`models.list`、`models.download`、`models.delete`，subtitle 事件的 `voice`。
- **Rust**：`voice` 驗證；`speaker_names` 的長度與數量限制。
- **TS**：`voiceLabels`、名稱顏色、validate、export。
- **GUI**：fake_engine 送出帶 `voice` 的字幕，CDP 檢查逐字稿與浮動字幕的標籤，以及改名後兩邊一致。

## 邊界

- **一律**：模型下載固定雜湊；所有外部輸入驗證型別與範圍；字幕永遠不因為說話者標籤失敗而停下來。
- **先問**：新增依賴；改 CI。這次沒有新增依賴：fbank 用 numpy 實作，推論用專案原有的 onnxruntime。
- **不做**：把模型打包進安裝檔、把音訊或向量寫到磁碟。

## 尚未解決

- **真實連動錄音**：需要在真實的 VTuber 連動錄音上驗證短句與重疊說話。
- **門檻**：門檻 0.4 是從 YODAS 拼接資料得到的，真實資料可能需要調整。
