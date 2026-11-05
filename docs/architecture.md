# kikitori 架構總覽

## 專案目標

kikitori 即時擷取電腦正在播放的聲音，把日文或英文語音翻譯成繁體中文。
畫面上同時顯示原文與譯文的雙語字幕，幫助語言學習者邊聽邊對照。
支援 Windows、Linux、macOS。

## 處理流程

```text
┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐   ┌──────────┐
│ 音訊擷取 │ → │ VAD 切句 │ → │ 語音辨識 │ → │   翻譯   │ → │ 字幕顯示 │
└──────────┘   └──────────┘   └──────────┘   └──────────┘   └──────────┘
 系統播放的      偵測人聲，       日文/英文       翻譯成         原文與譯文
 聲音            切成一句一句     轉成文字        繁體中文       雙語字幕
```

## 模組說明

之後每完成一個模組，會在本文件補上該模組的段落。

### VAD 切句（`kikitori.vad`）

規格：`docs/specs/vad-segmenter.md`；決策：`docs/adr/0001-vad-silero-onnx-and-reset-policy.md`。

把 16 kHz、單聲道、int16 的連續音訊切成一句一句。API 為同步呼叫：`Segmenter.process(audio)` 餵入任意長度的音訊，回傳這段音訊造成的事件；串流結束時呼叫 `flush()`。時間一律用樣本計數（以串流開頭為 0），同一段音訊不論分幾批、以多快的速度餵入，事件與位置都相同。

```text
int16 音訊（任意長度）
   │
   ▼
Segmenter ── 累積成 512 樣本（32 ms）視窗 ──► VoiceModel（預設 SileroVad）
   │                                            └─ 每視窗回傳人聲機率
   ├─ 遲滯門檻：≥ 0.5 持續 250 ms 才開始（起點往前含 200 ms）；< 0.35 才算靜音
   ├─ EndpointPolicy（預設 PauseEndpoint）：靜音 0.6 秒或句長 10 秒就結束
   ├─ request_end()：外部要求在下一個視窗結束（之後接標點模型、Smart Turn）
   └─ ResetPolicy（預設 AFTER_LONG_NON_SPEECH）：何時重置模型狀態
   │
   ▼
事件：UtteranceStarted → UtteranceAudio（每 32 ms）… → UtteranceEnded（pause / max_length / external / flush）
```

| 檔案 | 角色 |
|---|---|
| `segmenter.py` | `Segmenter`、`ResetPolicy`、三種事件 |
| `endpoint.py` | `EndpointPolicy` 介面與 `PauseEndpoint` |
| `model.py` | `VoiceModel` 介面：「512 樣本 → 機率」與 `reset()`，模型可替換 |
| `silero.py` | `SileroVad`：用 onnxruntime 跑 Silero VAD v6.2.3，前接 64 樣本前文、state `(2, 1, 128)` 跨呼叫保存，載入時驗證 SHA-256 |
| `models/` | 打包的 `silero_vad.onnx`、MIT 授權全文、版本與雜湊說明 |

- 同一句的 `UtteranceAudio` 首尾相接，合起來剛好是 `[UtteranceStarted.start_sample, UtteranceEnded.end_sample)`，語音辨識可以邊收邊處理，也可以在句子結束後一次取用。
- 依賴只有 `onnxruntime` 與 `numpy`；不使用 PyTorch、pipecat（以測試檢查）。每個視窗推論約 0.2 ms。
- 評測工具（噪音產生、混音、指標）在 `kikitori.evaluation`，評測選單與結果在 `experiments/silero_reset/`，正式執行時不會載入。
