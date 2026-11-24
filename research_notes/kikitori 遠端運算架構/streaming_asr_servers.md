# 遠端串流 ASR 伺服器設計比較（Whisper 系為主）

> 調查日期：2026-11-09。來源以各專案 GitHub README／原始碼、雲端廠商官方文件、arXiv／ACL Anthology 論文為主。大多數 GitHub 頁面的 Releases 區塊是空的，所以多數專案無法確認確切版本號，下面會逐一註明。

## 1. 主流系統怎麼做遠端串流 ASR（傳輸、音訊格式、VAD 位置、partial/final、session、背壓、延遲）

### Takeaway
不論開源或商用，幾乎都是同一種模式：**一條長連線（WebSocket 或 gRPC 雙向串流，少數用 raw TCP）、client 持續送 16 kHz mono PCM（int16 為主，部分支援 Opus）、server 回傳 partial／final 兩種結果**。VAD／端點偵測大多放在 server 端，OpenAI 的 `gpt-live-transcribe` 則是明確要求 client 端自己做 VAD。Whisper 系開源 server（WhisperLive、whisper_streaming、SimulStreaming）的 partial 都是「重跑整段緩衝再比對」做出來的。WhisperLive 需要累積 ≥1.0 s 音訊才開始轉寫；Whisper-Streaming 論文報告的端到端延遲是 3.3 s。

### Cited Findings

**Collabora WhisperLive（MIT）**
- 傳輸用 WebSocket，另有 `--enable_rest` 可開 OpenAI 相容的 REST；授權 MIT；Releases 區塊空白，沒有版本號 — [WhisperLive README](https://github.com/collabora/WhisperLive)
- Backend 有 faster-whisper（預設）、TensorRT、OpenVINO；README 另外列出 faster-whisper 的 AMD ROCm 支援 — [README](https://github.com/collabora/WhisperLive)；[server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)
- 連線後第一個 text frame 是 JSON options，欄位有 `uid`、`language`、`task`、`model`、`use_vad`、`send_last_n_segments`（預設 10）、`no_speech_thresh`（0.45）、`clip_audio`、`same_output_threshold`（10）、`initial_prompt`、`hotwords`、`vad_parameters`、`word_timestamps`、`audio_format`，以及翻譯用的 `enable_translation`、`target_language`。驗證可用 `Authorization: Bearer`、`?token=` 或 subprotocol — [server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)
- 音訊放在 binary frame，取樣率 16 kHz。**server 端 `audio_format` 預設是 float32**，也接受 int16（除以 32768）和 uint8。結束時送 binary `b"END_OF_AUDIO"` — [server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)；README 的 client 範例預設 int16，並寫「any cadence, any chunk size」 — [README](https://github.com/collabora/WhisperLive)
- VAD 在 server 端（Silero），由 client 的 `use_vad` 開啟 — [README](https://github.com/collabora/WhisperLive)
- Segment 的欄位是 `start`／`end`（秒，字串，三位小數）、`text`、`completed`，可選 `speaker`、`words`。判斷規則：一批結果的最後一段是 partial（`completed=False`），前面的段標成 completed。partial 文字重複超過 `same_output_threshold`（10）次時強制 commit。每次送出最近 N 段（`send_last_n_segments`）加上目前的 partial — [backend/base.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/backend/base.py)
- 待處理音訊不到 **1.0 s** 就不轉寫。緩衝超過 45 s 時丟掉最舊的 30 s。`clip_audio` 開啟時，若超過 25 s 沒有有效 segment 就只留最後 5 s — [backend/base.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/backend/base.py)
- 背壓／容量：`max_clients` 預設 4，`max_connection_time` 預設 600 s。server 滿了會回 `{"uid","status":"WAIT","message":<分鐘數>}`。batch 模式另有 `batch_max_queue_wait_s`（0.5 s）、`batch_max_admissions_per_s`（5.0）。斷線訊息是 `{"uid","message":"DISCONNECT"}` — [server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)；[base.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/backend/base.py)
- client callback 有 `on_session_started`、`on_partial_transcript`、`on_committed_transcript`、`on_translation`。README 沒有公布任何延遲數字，只說「nearly-live」 — [README](https://github.com/collabora/WhisperLive)

**UFAL whisper_streaming（MIT，作者已宣告 2025 年起過時）**
- `whisper_online_server.py` 是 **raw TCP** server（`--host`/`--port`，範例 port 43001），client 範例是 `arecord | nc`。音訊是 raw PCM 16 kHz mono S16_LE。`--min-chunk-size`（秒）是處理前最少等待的時間 — [whisper_streaming README](https://github.com/ufal/whisper_streaming)
- 演算法是 LocalAgreement：連續 n 次更新（範例 n=2）對前綴的結果一致才 commit，然後在句子／segment 邊界裁掉緩衝 — [README](https://github.com/ufal/whisper_streaming)
- Backend 有 faster-whisper（最推薦）、whisper_timestamped、openai-api、mlx-whisper。VAC（`--vac`，Silero）需要 torch — [README](https://github.com/ufal/whisper_streaming)
- 輸出每行是「emission time、start ms、end ms、文字」，例如 `2691.4399 300 1380 Chairman, thank you.` — [README](https://github.com/ufal/whisper_streaming)
- 論文 "Turning Whisper into Real-Time Transcription System"（Macháček, Dabre, Bojar，IJCNLP-AACL 2023 demo）報告在未切段的長篇語音上**延遲 3.3 s** — [arXiv:2307.14743](https://arxiv.org/abs/2307.14743)
- README 寫「WhisperStreaming is becoming outdated in 2025」，改推 SimulStreaming — [README](https://github.com/ufal/whisper_streaming)

**UFAL SimulStreaming（MIT）**
- 提供 `simulstreaming_whisper_server.py` 和 `simulstreaming_translate_server.py`，都是 **raw TCP**，音訊同樣是 16 kHz mono S16_LE — [SimulStreaming README](https://github.com/ufal/SimulStreaming)
- 預設輸出 JSONL：`start`、`end`、`text`、`tokens`、`words`、`is_final`、`emission_time`。翻譯 server 的 JSONL 有 `status`（INCOMPLETE/COMPLETE）、`text`（已確認）、`unconfirmed_text`、`is_final` — [README](https://github.com/ufal/SimulStreaming)
- 演算法是 AlignAtt：用 encoder-decoder cross-attention 判斷正在解碼音訊的哪一段，attention 進入緩衝尾端的「危險區」就停下來等更多音訊。`--frame_threshold` 一個 frame 是 0.02 s（large-v3）。README 宣稱比 WhisperStreaming 快約 5 倍，但沒有給數字 — [README](https://github.com/ufal/SimulStreaming)
- README 自己說 server 模式只是 computationally aware，而且 Whisper 輸出沒有 emission time — [README](https://github.com/ufal/SimulStreaming)
- Whisper 跑在 **PyTorch** 上（改寫自 OpenAI Whisper 程式碼）；LLM 翻譯（EuroLLM-9B-Instruct）用 **CTranslate2** — [README](https://github.com/ufal/SimulStreaming)

**speaches（原 faster-whisper-server，MIT）**
- STT 由 faster-whisper 驅動，TTS 用 piper／Kokoro。API 是 OpenAI 相容，支援用 **SSE 串流轉寫結果**，模型會動態載入、閒置後卸載，可用 GPU/CPU Docker 部署；Releases 區塊空白 — [speaches GitHub](https://github.com/speaches-ai/speaches)
- Realtime API 是 OpenAI 相容的 WebSocket 介面，有 transcription-only 模式。final 事件是 `conversation.item.input_audio_transcription.completed`，partial 是 delta。頁面沒寫音訊格式和 VAD 細節 — [speaches Realtime docs](https://speaches.ai/usage/realtime-api/)

**whisper.cpp `whisper-stream`**
- 官方說它是「a naive example」，只從本機 SDL2 麥克風取音，**沒有網路／server 模式**。參數有 `--step`（例 500 ms）、`--length`（例 5000 ms），`--step 0` 是 VAD 模式 — [whisper.cpp examples/stream](https://github.com/ggml-org/whisper.cpp/tree/master/examples/stream)

**Home Assistant Wyoming 協定 + wyoming-faster-whisper**
- Wyoming 跑在 **TCP 上的 JSONL 加 binary payload**：每則訊息先是一行 JSON header（`type`、`data`、`data_length`、`payload_length`），後面接額外資料和 payload — [Wyoming README](https://raw.githubusercontent.com/OHF-Voice/wyoming/master/README.md)
- 音訊事件是 `audio-start`／`audio-chunk`／`audio-stop`，帶 `rate`、`width`、`channels`、`timestamp`，payload 是 raw PCM。串流轉寫事件是 `transcript-start`、`transcript-chunk`（**每個 chunk 取代前一個，不是附加**）、`transcript`（為了向下相容仍要送）、`transcript-stop`。VAD 事件是 `voice-started`／`voice-stopped`。能力探索用 `describe`→`info` — [Wyoming README](https://raw.githubusercontent.com/OHF-Voice/wyoming/master/README.md)
- wyoming-faster-whisper 是 MIT，預設 port 10300。**預設等 client 送 `audio-stop` 才轉寫**（批次，不是串流）。`--vad-endpointing <秒>` 讓 server 用 Silero 判斷句尾後立即送出。`--stt-library` 可選 faster-whisper、sherpa、transformers、qwen3-asr、funasr、onnx-asr，只有 `--sherpa-streaming`（Kroko 模型，de/en/es/fr）是真串流 — [wyoming-faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)

**sherpa-onnx streaming WebSocket server（Apache-2.0）**
- client 送 16 kHz、16-bit 單聲道音訊。C++ client 預設 `--samples-per-message` 8000、`--seconds-per-message` 0.2（Python 範例是 0.1）— [sherpa-onnx online websocket docs](https://k2-fsa.github.io/sherpa/onnx/websocket/online-websocket.html)
- 端點偵測在 server 端，預設三條規則：(1) 尾端靜音 2.4 s；(2) 有非靜音內容時尾端靜音 1.2 s；(3) 句長達 20 s 強制切段。效能參數有 `--max-batch-size` 5（跨 client batching）、`--num-work-threads` 3、`--num-io-threads` 1、`--loop-interval-ms` 10 — [docs](https://k2-fsa.github.io/sherpa/onnx/websocket/online-websocket.html)
- 授權 Apache-2.0，跑在 onnxruntime 上。**Whisper 只列在 non-streaming 模型**，streaming 用 Zipformer/Paraformer/CTC — [sherpa-onnx GitHub](https://github.com/k2-fsa/sherpa-onnx)

**Vosk / Kaldi server（Apache-2.0）**
- 有四種實作：WebSocket、gRPC、WebRTC、MQTT。client 範例要求 16-bit mono PCM — [Vosk server page](https://alphacephei.com/vosk/server)；[vosk-server GitHub](https://github.com/alphacep/vosk-server)

**NVIDIA Riva / Riva ASR NIM**
- 串流 ASR 在音訊進行中回傳中間結果，偵測到句尾時回 final。VAD 在 server 端，可選 Silero（推薦）或 MarbleNet，設定方式是 `enable_vad_endpointing:true`，建議 `stop_history` 500 ms。two-pass EOU 建議 `stop_history_eou` 240 ms（只支援 Parakeet/Conformer CTC）。支援 Opus 編碼串流。頁面最後更新於 2026-06-30 — [Riva ASR overview](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html)
- Riva release notes 提到 gRPC 在 port 50051，Jetson 上另有 WebSocket realtime API（port 9000）和 REST（port 9001）— 引自搜尋摘要，指向 [Riva release notes](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/public/release-notes.html)
- Riva 的 streaming client 會分別量 intermediate latency（`is_final==false`）和 final latency — 引自搜尋摘要，指向 [Riva ASR performance](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-performance.html)

**Google Cloud Speech-to-Text**
- 串流**只支援 gRPC**。第一個 `StreamingRecognizeRequest` 只放 config，之後每個 request 放音訊 bytes。`interim_results` 會回 `is_final=false` 的結果，`stability` 從 0.0 到 1.0 估計 partial 還會不會變（不是正確率）。`single_utterance` 在說話停止時自動結束。final 只保證在 client half-close 之後出現。官方建議用無損編碼（FLAC／LINEAR16），盡量以 16 kHz 擷取 — [Google STT requests](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-requests)

**Azure AI Speech**
- 連續辨識的事件是 `recognizing`（partial）、`recognized`（final）、`session_started`、`session_stopped`、`canceled`（含傳輸或協定失敗）— [Azure how-to-recognize-speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech)
- `Speech_SegmentationSilenceTimeoutMs` 範圍 100–5000 ms，典型值 500 ms。Initial silence timeout 典型值：單次 5000 ms、連續 15000 ms。`Speech_SegmentationStrategy=Semantic` 改用句末標點切段（SDK ≥1.41）。Push stream 預設格式 16-bit、16 kHz mono PCM — [Azure docs](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech)

**AWS Transcribe streaming**
- 可用 SDK、HTTP/2、WebSocket。格式有 FLAC、Ogg-Opus、PCM s16le，建議無損。**chunk 建議 50–200 ms，大小要一致**，公式 `bytes = ms/1000 × rate × 2`。靜音時也要送等量的零值 PCM。建議 16 kHz。session 有不可提高的最長時間限制（超過要開新 session）。`LimitExceededException` 要用指數退避重試 — [AWS Transcribe streaming](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)

**Deepgram（Live streaming）**
- 結果欄位 `is_final=false` 代表還在等更多資料，`speech_final` 代表語音自然結束。KeepAlive 訊息可以讓連線在沒送資料時維持不斷 — [Deepgram live streaming](https://developers.deepgram.com/docs/live-streaming-audio)
- endpointing 預設啟用，文件寫「10 milliseconds」，範例用 300 ms。`utterance_end_ms`（範例 1000）需要搭配 `interim_results` 才會送 UtteranceEnd — [Deepgram endpointing](https://developers.deepgram.com/docs/endpointing)

**AssemblyAI Universal-Streaming**
- endpoint 是 `wss://streaming.assemblyai.com/v3/ws`。預設 mono 16-bit PCM，另支援 aac、ogg_opus、raw opus。訊息有 `Begin`（`id`、`expires_at`）、`Turn`（`transcript`、`end_of_turn`、`turn_is_formatted`、words 的 `word_is_final`）、`Termination`。client 送 `{"type":"Terminate"}` 結束。session 3 小時後自動關閉 — [AssemblyAI Universal-Streaming](https://www.assemblyai.com/docs/speech-to-text/universal-streaming)

**OpenAI Realtime API（transcription）**
- server 端用 WebSocket `wss://api.openai.com/v1/realtime?intent=transcription`，瀏覽器用 WebRTC。`session.update` 設 `type:"transcription"`，音訊是 `audio/pcm` 24 kHz，用 `input_audio_buffer.append` 送 base64 chunk。`input_audio_buffer.commit` 結束一個 turn 並觸發 final — [OpenAI realtime transcription](https://developers.openai.com/api/docs/guides/realtime-transcription)
- 事件有 `...transcription.delta`（partial 增量）、`...transcription.completed`（final）、`...failed`。不同 turn 的完成順序不保證，要用 `item_id` 對應。`gpt-live-transcribe` **不支援 server_vad／semantic_vad，要 client 端自己做 VAD**。另有 `delay` 等級（minimal…xhigh）— [OpenAI docs](https://developers.openai.com/api/docs/guides/realtime-transcription)

**頻寬參考**
- AWS 公式：16 kHz × 2 bytes = 32,000 B/s ≈ 256 kbit/s（int16 PCM）— [AWS](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)
- Opus 位元率涵蓋 6 kbit/s 到 510 kbit/s，frame 2.5–60 ms — [RFC 6716](https://datatracker.ietf.org/doc/html/rfc6716)

### Inferences
- 設計收斂得很明顯：一條持久雙向連線，16 kHz mono 音訊用 20–200 ms 的固定 chunk 送出，server 回 partial（可被取代）和 final（不可變）。Wyoming 的「transcript-chunk 取代前一個」和 Google 的 `stability` 都是同一個語意：**partial 是整句的最新快照，不是增量**，對字幕 UI 比較好處理。kikitori 現在本來就是整句重跑，可以直接沿用「快照取代」語意。
- WhisperLive 的 server 預設 float32 是 512 kbit/s，比 int16 多一倍。遠端模式至少要用 int16（256 kbit/s）。走網際網路的話，用 Opus 16–32 kbit/s 可再降約 8–16 倍。不過 Google 和 AWS 都建議無損，Opus 對 Whisper 準確度的影響沒有找到官方數據。
- VAD 放哪：雲端多在 server 端，但 OpenAI `gpt-live-transcribe` 要求 client 端 VAD。kikitori 已經在本機跑 Silero VAD，**把 VAD 留在 client** 有兩個好處：靜音時不送音訊（省頻寬），句尾判斷不受網路抖動影響。代價是 AWS 那種「靜音也要送零值」的 server 不適用，但自建 server 不受這個限制。client 只需在句子開始／結束時送控制訊息（類似 OpenAI 的 `commit`、Wyoming 的 `audio-start`/`audio-stop`）。
- 背壓的做法：WhisperLive 是用 `max_clients` 加 WAIT 拒絕，batch 模式用排隊時間上限；雲端是 quota 加指數退避；Deepgram／AssemblyAI 用 KeepAlive 和 session 上限。沒有任何來源描述「server 落後時自動丟棄舊的 partial 請求」，但 kikitori 的 partial 是週期性的整句重跑，server 端只保留最新一筆 partial 請求（覆寫佇列）就能自然做到背壓。

### Gaps
- Vosk 的 WebSocket 訊息 schema（`{"config":{"sample_rate":…}}`、`{"partial":…}`／`{"text":…}`、`{"eof":1}`）在兩個官方頁面都沒寫，只存在於我的訓練知識，未驗證。
- Google 串流的單一 stream 時長上限、單一 request 大小上限，以及 Deepgram 無音訊逾時（10 s?），抓到的頁面都沒有寫。
- AWS 的 `IsPartial` 和 partial-results stabilization 細節沒有抓到原文。
- whisper.cpp 的 `examples/server` 只確認不在 stream 範例裡。依記憶它是 HTTP `/inference` 的離線端點，不是串流，但未驗證。
- Riva 的 Whisper 是否支援串流／interim、Riva 官方延遲表格，抓到的頁面都沒有數字。
- 這些開源 server 都沒有內建斷線重連後恢復 session 的機制（README 沒寫）。

## 2. 哪些不依賴 PyTorch（CTranslate2/ONNX）、授權是否 MIT 相容可商用

### Takeaway
跑 Whisper 又不需要 PyTorch 的成熟 server 有：**WhisperLive（faster-whisper/CTranslate2，MIT）、speaches（faster-whisper，MIT）、wyoming-faster-whisper（faster-whisper，MIT，另可接 onnx-asr／sherpa）**。sherpa-onnx（Apache-2.0）是純 onnxruntime，但 Whisper 只能 non-streaming。UFAL 那兩個 server 的 VAC 需要 torch，SimulStreaming 的 Whisper 本身也是 PyTorch。

### Cited Findings
- WhisperLive：MIT，預設 backend 是 faster_whisper，另有 tensorrt、openvino — [README](https://github.com/collabora/WhisperLive)；[server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)
- whisper_streaming：MIT。backend 可用 faster-whisper，但推薦的 VAC 需要 `torch torchaudio` — [README](https://github.com/ufal/whisper_streaming)
- SimulStreaming：MIT，Whisper 是 PyTorch，翻譯 LLM 是 CTranslate2，VAC 需要 torch — [README](https://github.com/ufal/SimulStreaming)
- speaches：MIT，STT 用 faster-whisper — [GitHub](https://github.com/speaches-ai/speaches)
- wyoming-faster-whisper：MIT，faster-whisper 建在 CTranslate2 上，可切換 onnx-asr、sherpa — [GitHub](https://github.com/rhasspy/wyoming-faster-whisper)
- sherpa-onnx：Apache-2.0，onnxruntime，Whisper 在 non-streaming 清單 — [GitHub](https://github.com/k2-fsa/sherpa-onnx)
- vosk-server：Apache-2.0 — [GitHub](https://github.com/alphacep/vosk-server)
- Riva：NVIDIA EULA，模型放在 NGC，屬於專有授權 — [Riva ASR overview](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html)

### Inferences
- MIT 和 Apache-2.0 都能商用，也能和 MIT 專案共存（Apache-2.0 要保留 NOTICE，並帶專利授權條款）。可以借鏡協定或程式碼的對象：WhisperLive 的 options/WAIT/END_OF_AUDIO 握手和 completed 判斷，Wyoming 的 JSONL+binary framing 和事件命名，SimulStreaming 的 JSONL（`is_final`、`emission_time`、`unconfirmed_text`）。Riva 不適合拿來借程式碼。
- kikitori 已經有 CTranslate2 雙模型和 Silero ONNX，**把現有 engine 原封不動搬到遠端**，只換傳輸層，比導入上面任何一套 server 更省事。上面這些系統都是單一模型的 partial 機制，沒有一套是 kikitori 這種「快模型出 partial、準模型出 final」的雙模型設計。

### Gaps
- 各專案確切的最新版本號和發布日期，GitHub 頁面都沒顯示（Releases 空白）。
- 沒有找到 faster-whisper／CTranslate2 本身授權的原文（依記憶都是 MIT，未在這次驗證）。

## 3. 研究文獻中串流 Whisper 的延遲預算，以及遠端 GPU 和網路 RTT 的比較

### Takeaway
文獻裡串流 Whisper 的延遲是**秒級**：LocalAgreement 3.3 s，SimulStreaming／AlignAtt 的英文 ASR 在約 0.5–2.1 s，IWSLT 的評測區間是 2 s／4 s。Simul-Whisper 也指出 Whisper 必須 padding 到 30 s，會推高 computation-aware latency。和這些相比，LAN 的 RTT（約 1 ms 量級）可以忽略；網際網路的 RTT（數十 ms 量級）也只佔一小部分。**真正主宰延遲的是演算法等待（chunk／agreement）和 GPU 運算時間**，所以換到比較強的遠端 GPU，通常會降低總延遲，而不是增加。

### Cited Findings
- Whisper-Streaming（LocalAgreement）：在未切段長篇語音上延遲 3.3 s（IJCNLP-AACL 2023 demo）— [arXiv:2307.14743](https://arxiv.org/abs/2307.14743)
- Simul-Whisper（Wang et al., INTERSPEECH 2024, pp. 4483–4487）用 cross-attention 時間對齊引導解碼，不需要 fine-tune。chunk 1 s 時平均 WER 只惡化 1.46%（絕對值），優於 LocalAgreement。作者指出輸入仍須 padding 到 30 s，會增加 computation-aware latency — [arXiv:2406.10052](https://arxiv.org/pdf/2406.10052)
- CUNI IWSLT 2025（Macháček & Polák, arXiv:2506.17077）：SimulStreaming 結合 Simul-Whisper 的 AlignAtt 和 Whisper-Streaming 的介面，可處理無限長語音。英文 ASR（ACL6060 dev）WER 14.22% 時延遲 494 ms，WER 12.69% 時延遲 2143 ms。Cs→En 翻譯 SLAAL 1763 ms／2630 ms，對應 2 s／4 s 兩個延遲區間。Whisper chunk 在 0.05–2.0 s 之間測試，`frame_threshold` 測過 4、25–35。**數字都是 computationally unaware**。論文說系統要有「feasible hardware resources」才能真正即時（Whisper large 1.6B 加 EuroLLM 9B）— [arXiv:2506.17077](https://arxiv.org/html/2506.17077v1)
- 同組 2026 年的後續工作（arXiv:2606.03948，IWSLT 2026）把方法移到 Canary-1B-v2，並稱 SimulStreaming 是 IWSLT 2025 的最佳系統 — 引自搜尋摘要，[arXiv:2606.03948](https://arxiv.org/html/2606.03948)；另有 [IWSLT 2026 ACL Anthology](https://aclanthology.org/2026.iwslt-1.22.pdf)
- SimulStreaming README：IWSLT 2025 Simultaneous Speech Translation 最佳，約比 WhisperStreaming 快 5 倍（沒有附數字）— [README](https://github.com/ufal/SimulStreaming)
- AWS：延遲取決於 chunk 大小，建議 50–200 ms — [AWS](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)
- Riva：VAD endpoint `stop_history` 建議 500 ms，two-pass EOU 240 ms — [Riva](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html)；Azure 切段靜音典型值 500 ms — [Azure](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech)

### Inferences
- 用 kikitori 的參數估算一次 partial 的延遲：等 0.7 s 週期，加上傳送一個 20–100 ms 的 chunk，加上 RTT，加上快模型推論，再加回傳數百 bytes。final 的延遲則是 VAD 句尾靜音（業界典型 240–500 ms）加上準模型推論加 RTT。LAN 下 RTT 和傳輸時間比這些小 2–3 個數量級。跨洲網際網路的 RTT 約 100–200 ms（這是一般網路常識，這次沒有抓到權威來源）仍明顯小於 500 ms 的端點靜音和秒級推論。所以遠端化的延遲成本主要是**每句多 1×RTT**，前提是 partial 不要做成同步請求-回應，而是串流推送。
- 頻寬也不是瓶頸：int16 PCM 256 kbit/s 在 LAN 幾乎可以忽略。上傳頻寬有限的家用網路，int16 PCM 就可能吃掉相當比例的上行，這時用 Opus 是有意義的。
- 遠端 GPU 如果比本機強（例如本機只有 CPU），large 模型的推論時間會從秒級降到百毫秒級，整體延遲反而下降。這也是雲端 STT 能做到低延遲的原因。但沒有文獻直接量化「遠端 GPU 減掉 RTT」的淨效益，這屬於推論。

### Gaps
- 沒有找到 SimulStreaming 和 LocalAgreement 在同一硬體上的 computation-aware 延遲對照數字，論文明說只報告 computationally unaware。
- 沒有找到權威來源量化 Opus 壓縮對 Whisper WER 的影響。
- 沒有找到權威的網際網路 RTT 統計（上面的 100–200 ms 是常識性估計）。

## 4. 整體構想（本機擷取與顯示、遠端 ASR/翻譯）是否可行、是否有前例

### Takeaway
**可行，而且已經被大量驗證**。所有雲端 STT（Google、Azure、AWS、Deepgram、AssemblyAI、OpenAI）本質上都是這個架構。Home Assistant 的 Wyoming 衛星裝置，就是「本機擷取、遠端 faster-whisper」在 LAN 上的開源實例。WhisperLive、whisper_streaming、SimulStreaming 也都是 thin client 串流 PCM 給遠端 Whisper server。沒有發現不可行的部分，只有幾個需要自行設計的地方：雙模型 partial/final 的協定、跨網際網路的加密和認證、斷線重連。

### Cited Findings
- 雲端串流 STT 都是 client 擷取音訊、透過 gRPC/WebSocket/HTTP/2 串流到遠端、回傳 partial/final — [Google](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-requests)、[Azure](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-recognize-speech)、[AWS](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)、[Deepgram](https://developers.deepgram.com/docs/live-streaming-audio)、[AssemblyAI](https://www.assemblyai.com/docs/speech-to-text/universal-streaming)、[OpenAI](https://developers.openai.com/api/docs/guides/realtime-transcription)
- Wyoming 用 TCP 傳 PCM 給遠端的 wyoming-faster-whisper（port 10300），也支援 VAD 和串流 transcript 事件 — [Wyoming](https://raw.githubusercontent.com/OHF-Voice/wyoming/master/README.md)；[wyoming-faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)
- WhisperLive 的 WebSocket server 帶 auth（Bearer/token）、容量控制（WAIT）、連線時間上限 — [server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)
- SimulStreaming 也把 ASR 加 LLM 翻譯放在遠端 TCP server — [README](https://github.com/ufal/SimulStreaming)

### Inferences
- 對 kikitori 最有效率的設計方向（綜合以上各家）：
  - 本機保留 Silero VAD，只在有語音時送 int16 PCM（LAN）或 Opus（WAN），用固定 20–100 ms chunk。
  - 用單一 WebSocket（或 gRPC bidi）長連線，同時傳 binary 音訊 frame 和 JSON 控制／結果訊息。
  - 句子開始／結束由 client 送 `utterance-start`／`utterance-end`（仿 OpenAI `commit`、Wyoming `audio-start/stop`）。
  - server 回 `partial`（快照取代，可帶 `stability`）和 `final`，每則帶 utterance id（仿 OpenAI `item_id`），翻譯結果也掛在同一個 id 上。
  - server 的 partial 佇列只保留最新一筆，達到背壓。
  - 重連時用 session id 加 utterance id 重新開始目前這一句，不保證 exactly-once（各家都沒有提供 session resume）。
  - 跨網際網路一定要 TLS（wss）加 token（WhisperLive、AssemblyAI、OpenAI 都用 header token）。
- 翻譯放在遠端（ASR 和 Ollama 同機）可以省掉一次網路往返，SimulStreaming 的 ASR+LLM 同 server 就是先例。
- 唯一的硬性限制是網路本身：WAN 上的封包遺失或抖動會讓 partial 延遲不穩。TCP（WebSocket/gRPC）的 head-of-line blocking 是各家都接受的取捨，只有 OpenAI 對瀏覽器提供 WebRTC。這次沒有找到 ASR 場景下 TCP 和 WebRTC 延遲差異的權威數據。

### Gaps
- 沒有任何開源 Whisper server 內建「快模型 partial、準模型 final」的雙模型協定，找不到直接前例，要自行設計。
- 沒有找到權威來源比較 WebSocket 和 WebRTC/QUIC 在串流 ASR 下的延遲或穩定性。
- 沒有找到各開源 server 的斷線重連或 session resume 機制文件。
