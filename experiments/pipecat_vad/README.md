# 可行性驗證：只用 pipecat-ai 的 VAD 元件

> 暫時性實驗，不屬於正式程式碼。日期：2026-10-28，pipecat-ai 1.14.0，Python 3.12。

## 怎麼重跑

```text
uv sync --group experiment-vad
uv run --group experiment-vad python    experiments/pipecat_vad/fetch_samples.py
uv run --group experiment-vad python -I experiments/pipecat_vad/build_tracks.py
uv run --group experiment-vad python -I experiments/pipecat_vad/probe_vad.py
uv run --group experiment-vad python -I experiments/pipecat_vad/probe_smart_turn.py
```

- 測試語音：Hugging Face `joujiboi/japanese-anime-speech`（動漫配音）與
  `Elite35P-Server/EliteVoiceProject`（hololive 櫻巫女直播），各 6 段。
  權利屬原權利人，只在本機使用，`data/` 已被 git 忽略。
- 音軌 = 片段之間插入已知長度的靜音，所以知道每段的正確樣本位置。

## 結果

### 1. 依賴
- 新增 47 個套件，`.venv` 從 39 MB → 329 MB（+約 290 MB）。**沒有 torch / transformers**。
- 最大宗：llvmlite 117 MB（numba 的依賴）、onnxruntime 43 MB、numpy 43 MB、numba 14 MB、openai 13 MB。
  VAD 真正用到的只有 onnxruntime + numpy + 模型檔（Silero 3 MB、Smart Turn v3 9 MB，都內建在套件裡）。

### 2. VAD 狀態
- 每次餵 512 樣本（32 ms）呼叫 `analyze_audio()`，四種狀態都會出現；合成的靜音 / 正弦波 / 雜訊全程 QUIET。
- 速度約 0.4 ms / 32 ms 區塊。
- 預設 `stop_secs=0.2` 會在句中換氣處切開（6 段片段被切成 15 段 / 8 段）；改成 0.6 s 比較接近字幕的一句。

### 3. 每 32 ms 的人聲機率
- 可以取得：繼承 `SileroVADAnalyzer` 覆寫 `voice_confidence()`，記下原本那次推論的值（不能自己再呼叫一次，會讓 RNN 狀態多推進一步）。
- **發現問題**：pipecat 每約 2 秒、在 QUIET 時會自動重置 Silero 的 RNN 狀態。重置剛好落在語音開頭或句中停頓時，
  之後 1～2 秒機率被壓低，**整句漏抓**（`stop_secs=0.6` 時漏掉 2 段）。
  關掉自動重置後全部抓回，片段內外的平均機率差距也最大：

  | 設定 | 動漫 片段內 / 外 | VTuber 片段內 / 外 |
  |---|---|---|
  | 預設（有重置） | 0.626 / 0.005 | 0.801 / 0.008 |
  | stop 0.6（有重置） | 0.580 / 0.004 | 0.790 / 0.008 |
  | 不重置 | **0.700 / 0.002** | **0.825 / 0.007** |

### 4. Smart Turn v3（本機）
- 可以單獨使用：`LocalSmartTurnAnalyzerV3` 不依賴管線，公開 API（`append_audio` + `analyze_end_of_turn`）
  和直接呼叫 `_predict_endpoint()` 結果一致，CPU 約 53 ms / 次。
- **但判斷不可靠**：12 段中只有 1 段做到「完整句 > 0.5 且切斷句 ≤ 0.5」。樣本少，
  且它是為「人跟語音助理對話」的輪替訓練的，跟動漫演技、直播口語不同。
- 公開 API 的緩衝用 `time.monotonic()` 標時間，離線（比即時快）餵資料時「語音前保留多少」會算錯。

## 結論與後續（摘要）
- 可行：VAD 狀態與 32 ms 機率都拿得到，速度充裕。
- 需要一層包裝把狀態轉成「一句開始 / 緩衝更新 / 一句結束」事件，並自己維護樣本計數器。
- 主要風險：依賴過重、要覆寫 pipecat 的私有方法、自動重置會漏抓、Smart Turn 不適合當主要依據。
