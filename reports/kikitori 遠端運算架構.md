# 把 Whisper 與 Ollama 移到另一台電腦

**可行，而且這不是新架構。** Google、Azure、AWS、Deepgram、AssemblyAI、OpenAI 的串流語音辨識，做法都是在本機擷取音訊，經過一條長連線送到遠端 GPU，再收回 partial 與 final 結果（[Google](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-requests)、[AWS](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)、[OpenAI](https://developers.openai.com/api/docs/guides/realtime-transcription)）。開源方面，Home Assistant 的 Wyoming 和 Collabora 的 WhisperLive，已經在區網把同一套做法用在 faster-whisper（CTranslate2）上（[Wyoming](https://raw.githubusercontent.com/OHF-Voice/wyoming/master/README.md)、[WhisperLive](https://github.com/collabora/WhisperLive)）。

對 kikitori 最有效率的設計如下：

- **切分點**：本機保留擷取、Silero VAD 與字幕顯示，只在有語音時把音訊以 append-only 方式串流出去。Whisper 雙模型與 Ollama 放在同一台遠端主機。
- **傳輸**：一條 `wss://` 長連線。區網送 16 kHz int16 PCM（約 256 kbit/s），網際網路改送 Opus 24–32 kbit/s。
- **時間軸**：以 client 的樣本計數為準。
- **安全**：參考 Syncthing 和 Moonlight，用 mutual TLS 加配對碼，配對後 pin 住對方公鑰。

依推算，網路只在本機延遲上多加約一個 RTT。以本專案在 RTX 5070 上量到的 partial 0.3 s、final 0.9 s 為基準，有線或健康的 Wi-Fi 區網幾乎看不出差別，同國家用網路的 final 也約在 1.0–1.2 s。**頻寬從來不是瓶頸，真正的風險是 Wi-Fi 的尾端延遲、伺服器 GPU 被多人共用，以及使用者把伺服器直接暴露在網際網路上。** 如果本機已經有夠力的 NVIDIA GPU，遠端最多只能打平，所以這個功能應該定位為「本機 GPU 不足時的選項」，並由 App 實測後主動推薦本機或遠端。

## 雲端 STT 與開源專案已證明可行，但沒有現成伺服器能直接用

各家主流系統收斂到同一個形狀：一條持久的雙向連線，client 以固定節奏送 16 kHz mono 音訊，server 回傳「可被取代的 partial」與「不再變動的 final」。Wyoming 的 `transcript-chunk` 明確規定**每個 chunk 取代前一個，而不是附加在後面**（[Wyoming](https://raw.githubusercontent.com/OHF-Voice/wyoming/master/README.md)）。Google 則為 partial 附上 0.0–1.0 的 `stability`（[Google STT requests](https://docs.cloud.google.com/speech-to-text/docs/speech-to-text-requests)）。kikitori 目前每 0.7 s 對整句重跑一次，產生的本來就是「整句快照」，所以可以直接沿用這種「快照取代」語意。

在「不用 PyTorch、授權與 MIT 相容」的限制下，候選範圍很快就縮小了：

| 專案 | 傳輸／音訊 | VAD 位置 | 需要 PyTorch？ | 授權 | 對 kikitori 的價值 |
|---|---|---|---|---|---|
| WhisperLive | WebSocket；server 預設 float32，也接受 int16 | server（Silero） | 否（faster-whisper） | MIT | 參考 options 握手、WAIT 拒絕、batch 推論 |
| whisper_streaming | raw TCP，S16_LE | server（`--vac`） | VAC 需要 torch | MIT | 作者已宣告 2025 年起過時 |
| SimulStreaming | raw TCP，S16_LE | server | Whisper 本身就是 PyTorch | MIT | 不符限制，只參考 AlignAtt 概念 |
| wyoming-faster-whisper | TCP 上的 JSONL＋binary | 可選 server 端點偵測 | 否 | MIT | 參考事件命名與 framing |
| sherpa-onnx | WebSocket，int16 | server | 否（onnxruntime） | Apache-2.0 | Whisper 只能 non-streaming |
| NVIDIA Riva | gRPC，支援 Opus | server | — | 專有 EULA | 不可借用程式碼 |

各列依序引自 [WhisperLive](https://github.com/collabora/WhisperLive)、[whisper_streaming](https://github.com/ufal/whisper_streaming)、[SimulStreaming](https://github.com/ufal/SimulStreaming)、[wyoming-faster-whisper](https://github.com/rhasspy/wyoming-faster-whisper)、[sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/websocket/online-websocket.html)、[Riva](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html)。

關鍵差異在於：**上面這些伺服器都只有單一模型的 partial 機制，沒有一套是 kikitori 的「快模型出 partial、準模型出 final」雙模型設計。** 導入任何一套都等於放棄現有的處理管線。kikitori 的 Python 引擎本身已經包含 CTranslate2 雙模型、Silero ONNX 和 Ollama 翻譯，目前透過 stdio NDJSON 與 Tauri 溝通。比較省事也比較穩的做法，是把同一個引擎拆成兩個角色：本機那份只負責擷取、VAD 和把音訊送出去；遠端那份以無頭的 `serve` 模式跑 ASR 與翻譯。換句話說，只換傳輸層，不換管線。

文獻提供了另一個角度的支持：串流 Whisper 的延遲是**秒級**。Whisper-Streaming（LocalAgreement）在長篇語音上的延遲是 3.3 s（[arXiv:2307.14743](https://arxiv.org/abs/2307.14743)）。SimulStreaming 在 IWSLT 2025 的英文 ASR，延遲在 494 ms 到 2143 ms 之間，而且這些數字是 computationally unaware（[arXiv:2506.17077](https://arxiv.org/html/2506.17077v1)）。這代表主宰延遲的是演算法等待和 GPU 運算時間，不是網路。

## 在本機 VAD 之後切開，ASR 與翻譯必須同機

可以切的位置有五個，只有一個同時兼顧頻寬、延遲與實作成本：**client 跑 Silero VAD，只送語音段（含 200–300 ms pre-roll），ASR 和翻譯都在伺服器。**

**送特徵或 encoder 輸出的方案，對現成的 Whisper 不可行。** Whisper 的輸入是 25 ms window、10 ms stride 的 log-mel（[Whisper 論文](https://arxiv.org/abs/2212.04356)），large-v3 改成 128 bins（[model card](https://huggingface.co/openai/whisper-large-v3)）。推算下來，fp32 log-mel 每秒 32–51 KB，和 int16 PCM 的 32 KB/s 一樣大甚至更大。encoder 輸出一個 30 s 視窗約 3.8 MB，同長度的 PCM 只有 960 KB。split inference 要省頻寬，得像 ESRT 那樣專門訓練一個壓縮表示：它的上傳量比原始音訊小 5.1–10.2 倍，但用的是另外訓練的模型，不是現成的 Whisper（[arXiv 2605.28642](https://arxiv.org/abs/2605.28642)）。

**ASR 放遠端時，翻譯也要放在同一台。** 否則文字要走「伺服器 → client → Ollama」，每句多繞一個 RTT，前文脈絡也會散落在兩邊。SimulStreaming 把 ASR 和 LLM 翻譯放在同一個 server，就是這種做法的先例（[SimulStreaming](https://github.com/ufal/SimulStreaming)）。

反過來的混合模式，是「ASR 留本機、只把文字送到遠端 Ollama」。kikitori 的 `translation.base_url` 現在就能指向別台主機，所以這個模式已經可以用，適合「本機 GPU 跑得動 Whisper，但同時跑不動 LLM」的使用者。

**VAD 放 client，與業界多數做法相反，但有明確理由。** 雲端服務多半在 server 端做端點偵測，例如 Riva 建議 `stop_history` 500 ms（[Riva](https://docs.nvidia.com/deeplearning/riva/user-guide/docs/asr/asr-overview.html)）。不過 OpenAI 的 `gpt-live-transcribe` 明確不支援 server VAD，要求 client 自己做（[OpenAI](https://developers.openai.com/api/docs/guides/realtime-transcription)），可見這兩種做法都是主流。kikitori 已經在本機跑 Silero ONNX，留在 client 有三個好處：

- 靜音和 BGM 不必上傳。例如 VTuber 直播說話比例約 40% 時，平均上行流量約降為 0.4 倍（推算）。
- 句尾判斷不受網路抖動影響。
- 伺服器端不需要另外再跑一套 VAD。

代價是 VAD 切錯時伺服器無法補救，所以 pre-roll 必須在 client 的 ring buffer 裡保留。

**現在「每 0.7 s 重送整句」的做法，遠端化之前必須先改掉。** whisper_streaming 的 client 只是持續推送 raw PCM（`arecord | nc`），緩衝、處理和修剪全部在 server 做（[whisper_streaming](https://github.com/ufal/whisper_streaming)），WhisperLive 也接受「any cadence, any chunk size」的推送（[WhisperLive](https://github.com/collabora/WhisperLive)）。改成增量上傳之後，上行流量固定為 32 KB/s（PCM）或約 3 KB/s（Opus），和句子長短無關。若沿用重送整句，一句 10 s 的話累計上傳量約是增量的 7 倍（推算）。

### 網路只加一個 RTT，抖動比頻寬更值得擔心

推算的延遲模型是：partial ≈ 本機 0.3 s + 1 RTT，final ≈ 本機 0.9 s + 1 RTT。翻譯 token 以串流回傳，只有第一個 token 要付一次單程延遲。

| 環境 | 額外 RTT 依據 | 預估 final |
|---|---|---|
| 有線區網 | 約 1 ms 以下（經驗值，無量測引用） | ≈ 0.90 s |
| 健康的 5 GHz Wi-Fi | 住宅 hotspot 單程 jitter 9.2–13.7 ms（[arXiv 1608.06606](https://arxiv.org/pdf/1608.06606)） | ≈ 0.91–0.92 s |
| 壅塞或 2.4 GHz Wi-Fi | 部分校園 AP 有 10% 封包的 Wi-Fi hop 延遲超過 100 ms（[WiLy, INFOCOM'16](https://1989chenguo.github.io/Publications/WiLy-INFOCOM16.pdf)） | 尾端 ≈ 1.0–1.1 s |
| 同國家用網路 | 美國 FWA 中位數 46–53 ms（[ComSoc 轉述 Ookla](https://techblog.comsoc.org/2026/08/27/ookla-u-s-fixed-wireless-access-crosses-17-million-connections-as-adoption-shifts-from-rural-to-urban/)）；負載下中位數 66 ms（[Bajpai et al.](https://vaibhavbajpai.com/documents/papers/proceedings/fcc-networking-2022.pdf)） | ≈ 0.95–1.2 s |

和 0.5–2 s 的端點靜音與推論時間相比，這些延遲都屬於次要成本。真正會被使用者察覺的，是 TCP 在 Wi-Fi 掉封包時的重傳停頓：偶爾會停住數百 ms，所以字幕端要能丟棄序號已經過期的 partial。

另一個不明顯的成本來自 Whisper 會把輸入補零到 30 s。encoder 的成本因此和句子長短無關：在行動 NPU 上，補零後的 encoder 時間是不補零的 3.8 倍（[NPUsper, arXiv 2607.01108](https://arxiv.org/pdf/2607.01108)）。每一次 partial 都要付一次完整的 encoder 成本。這正是多人共用一張 GPU 時，延遲會隨人數線性惡化的原因。

## 用一條 wss 長連線，區網送 PCM、網際網路送 Opus

**傳輸協定選 WebSocket。** 原因有三個：

- 業界的伺服器對伺服器串流介面多半用它。Deepgram、AssemblyAI 用 WebSocket，OpenAI 的 server 端也用 WebSocket，瀏覽器才用 WebRTC（[OpenAI Realtime](https://developers.openai.com/api/docs/guides/realtime)、[AssemblyAI](https://www.assemblyai.com/docs/speech-to-text/universal-streaming)）。
- 同一條連線可以同時傳 binary 音訊訊框和 JSON 控制、字幕訊息。
- 兩端都有成熟套件：Rust `tokio-tungstenite` 0.30.0（MIT）、Python `websockets` 17.4（BSD-3-Clause）（[crates.io](https://crates.io/crates/tokio-tungstenite)、[PyPI](https://pypi.org/project/websockets/)）。

其他協定的評估如下：

- **gRPC**：同樣跑在 TCP（HTTP/2）上，head-of-line blocking 特性和 WebSocket 相同（[MDN](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport_API)），又要多一套 protoc 工具鏈。
- **QUIC**：串流彼此獨立，也支援換網路時的連線遷移（[RFC 9000](https://www.rfc-editor.org/rfc/rfc9000.html)），是合理的第二階段升級。`quinn`（MIT/Apache）和 `aioquic`（BSD-3）兩端都有實作。
- **WebRTC**：為人對人通話設計，會為了低延遲丟棄或拉伸遲到的封包。社群對 OpenAI WebRTC 的批評正是「積極丟棄音訊」（[Simon Willison 整理](https://simonwillison.net/tags/webrtc/)），這對 ASR 準確度是直接傷害。它唯一的優勢是 NAT 穿透，但 kikitori 是 client 主動連指定的 IP，用不到這個優勢。

**音訊編碼依網路環境選擇。** 區網送 int16 PCM：零失真、零 CPU 成本，256 kbit/s 在區網上可以忽略。注意不要學 WhisperLive server 預設的 float32，那會讓流量翻倍成 512 kbit/s（[WhisperLive server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)）。網際網路則改送 Opus。

Opus 對 ASR 的影響，在 bitrate 夠高時可以忽略。NoLACE 在 LibriSpeech 上用 Conformer 量測：未壓縮 WER 2.01%，Opus 20 kb/s 為 2.03%，12 kb/s 為 2.07%，要降到 6 kb/s 才明顯惡化到 3.08%（[arXiv 2309.14521](https://arxiv.org/pdf/2309.14521)）。Amazon 的遠場 LSTM 研究結果比較保守：16 kbps 時相對 WER 劣化 12.6%，32 kbps 時 6.6%（[arXiv 2002.00122](https://arxiv.org/pdf/2002.00122)）。Google 官方立場是「能無損就無損」，必須有損時依序建議 AMR_WB、OGG_OPUS（[Google best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)）。

**目前沒有任何主流來源直接量測 Whisper 在 Opus 下的 WER，日文和中文的 CER 也沒有。** 唯一相關的是一篇 2026 年的論文，對 Vaani-Whisper-large-v3 給出「marginal degradation」的定性結論（[arXiv 2606.09335](https://arxiv.org/pdf/2606.09335)）。因此建議取保守值 24–32 kbps，搭配 `OPUS_APPLICATION_VOIP` 和 20 ms 訊框（[libopus API](https://opus-codec.org/docs/opus_api-1.5/group__opus__encoder.html)）。TCP 不會掉封包，所以 FEC 和 DTX 都關掉。上線前再用 kikitori 自己的評估資料，比較 PCM 和 Opus 16、24、32 kbps 的 CER。

依 RFC 6716 推算，Opus 的演算法延遲約 26.5 ms（[RFC 6716](https://www.rfc-editor.org/rfc/rfc6716)），可以忽略。FLAC 雖然無損，但是以 block 為單位編碼，要等約 256 ms 的資料才能輸出一個 block，不適合即時串流。

**網路訊息每 100 ms 送一則。** Google 明確建議 100 ms，認為這是延遲與效率之間的好平衡（[Google best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)）；AWS 建議 50–200 ms，而且 chunk 大小要一致（[AWS](https://docs.aws.amazon.com/transcribe/latest/dg/streaming.html)）。照這個節奏推算，線上實際流量是：PCM 約 268 kbit/s，Opus 24 kbps 約 30 kbit/s。前者只占 FCC 上傳基準 20 Mbps 的約 1.4%（[Tom's Hardware](https://www.tomshardware.com/service-providers/network-providers/fcc-quadruples-requirements-for-basic-broadband-service-100mbps-download-and-20mbps-upload-are-now-the-base-standard)）。Opus 真正的價值不在省頻寬，而在行動熱點，或同一條上行同時跑 OBS 推流時，降低 TCP 壅塞與重傳的風險。另外記得設 `TCP_NODELAY`，避免 Nagle 演算法延後送出小訊息。

**時間軸以 client 的樣本計數為唯一基準。** 每則上行音訊帶 `seq`、`start_sample` 和 `n_samples`。VAD 把靜音切掉時，`start_sample` 會直接跳號，server 由此知道中間有一段空白。下行字幕也以樣本數標示起訖。Deepgram 的時間戳就是跟著音訊串流走、和連線時間無關：暫停後恢復送音訊，時間戳會從停下的地方接續（[Deepgram KeepAlive](https://developers.deepgram.com/docs/audio-keep-alive)）。這樣兩台機器不需要 NTP 對時，SRT 匯出的時間完全由本機擷取決定。

協定草案的訊息如下。上行有 `hello{protocol_version, capabilities}`、binary `audio{seq, start_sample, codec, payload}`、`segment_start`、`segment_end` 和 `ping`。下行有 `partial{seg_id, rev, text}`（同一個 `seg_id` 的新快照覆寫舊的）、`final{seg_id, start_sample, end_sample, text}`、`translation{seg_id, rev, delta}`、`ack_sample`，以及 `server_ms`、`queue_ms` 計時資訊。

## 伺服器只載一份模型，partial 佇列只留最新一筆

參考實作的共同做法是：**共用一份已載入的模型、限制同時連線數、佇列滿了就明確拒絕。** WhisperLive 預設 `max_clients` 4、單一連線最長 600 s。如果沒有指定模型，它會**為每個連線各載一份新模型**，耗用顯示記憶體和載入時間；用 `-fw`／`-trt` 指定模型時才會只載一次、全部共用。它另有 `--batch_inference`，可以把多個 session 合成一次 GPU 呼叫（[WhisperLive](https://github.com/collabora/WhisperLive)）。

CTranslate2 文件說明，同一張 GPU 上的多個 worker 會共享權重以節省記憶體，但多開 worker「may not improve the global throughput」（[CTranslate2 Parallel](https://opennmt.net/CTranslate2/parallel.html)）。所以多 client 的正解是「一份模型，加上跨 session 的短時間窗 batching」，而不是複製多份模型。

**排程要讓 final 優先、partial 可以被覆寫。** 每個 client 每 0.7 s 觸發一次 partial，而每次都要付固定的 30 s encoder 成本，所以 N 個 client 的 GPU 工作量約是 N 倍。對策有三個（設計推論）：

- 每個 session 的 partial 佇列只保留最新一筆；上一次 partial 還沒跑完，就跳過這一輪。
- final 走高優先權佇列。Triton 的 `priority_levels` 和 `max_queue_size` 就是這種機制（[Triton Batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)）。
- GPU 忙碌時自動拉長 partial 的間隔。

連線數滿了，就在握手階段回「server busy」，不要讓連線掛著等。WhisperLive 的 `{"status":"WAIT"}` 是可以參考的現成格式（[server.py](https://raw.githubusercontent.com/collabora/WhisperLive/main/whisper_live/server.py)）。

**GPU 共用時，顯示記憶體是硬限制。** faster-whisper 的 large-v2 在 fp16 時約 4.5 GB，int8 約 2.9 GB，batch=8 時更多（[faster-whisper](https://github.com/SYSTRAN/faster-whisper)）。把快模型、int8 的 large-v3、7–8B Q4 的 LLM 加上 KV cache 一起算，推估約需 10–13 GB：12 GB 的卡只服務一個 client 都很緊，16–24 GB 才穩。large-v3 和 turbo 沒有官方 VRAM 表，這個估算是用 large-v2 的數字類推。

Ollama 的預設值要調整：

- `OLLAMA_NUM_PARALLEL` 預設 1，多個 client 的翻譯會排隊；調高之後，記憶體需求會隨「平行數 × context 長度」增加。
- `OLLAMA_MAX_LOADED_MODELS` 預設每張 GPU 3 個；放不下時會卸載閒置的模型。
- `keep_alive` 預設 5 分鐘（[Ollama FAQ](https://docs.ollama.com/faq)）。

伺服器應固定 `keep_alive=-1`，並把同時載入的模型數設為 1，避免 Ollama 為了換模型而和 Whisper 搶顯示記憶體。至於 CTranslate2 和 llama.cpp 在同一張 GPU 上互相干擾到什麼程度，目前沒有公開量測，需要實測。

**營運面還有三件事。** 第一，連線的第一則訊息要交換 `protocol_version`：major 不同就拒絕連線，並提示「兩台都更新到同一版」；minor 不同則依能力旗標降級。WhisperLive 和 Ollama 都沒有這個機制，但 kikitori 的 stdio 協定已經有 `PROTOCOL_VERSION`，可以延用。第二，單一 session 的時長上限要可以設定，或改用閒置逾時，因為 WhisperLive 預設的 600 s 對直播和口譯都太短。第三，無頭部署在 Linux 用 systemd，在 Windows 用 service 或常駐系統匣。Windows service 跑在 Session 0、沒有桌面，所以配對碼要改從 CLI 或管理頁顯示。

## 參考 Syncthing 與 Moonlight 配對，不要學 Ollama 的裸埠

音訊裡可能有私人對話，伺服器的 GPU 本身也是攻擊目標。反面教材是 Ollama：它預設只綁 127.0.0.1，但 FAQ 只教使用者用 `OLLAMA_HOST=0.0.0.0` 對外開放，完全沒有驗證機制（[Ollama FAQ](https://docs.ollama.com/faq)）。結果 SentinelOne 和 Censys 在 2026 年 1 月找到**約 175,000 台主機的 11434 埠可以公開存取，而且 API 不需驗證就會回應**（[The Hacker News](https://thehackernews.com/2026/01/researchers-find-175000-publicly.html?m=0)）。kikitori 伺服器絕不能重蹈覆轍。伺服器上的 Ollama 應維持只綁 127.0.0.1，由 kikitori 伺服器在同一台機器上代為呼叫，不要再多暴露一個沒有驗證的埠。

**沒有中央帳號的消費級 App，配對方式已經收斂成同一個模式。** Syncthing 的每台裝置在第一次啟動時產生自簽憑證，device ID 就是憑證 DER 的 SHA-256。握手時雙方都出示憑證，各自比對對方的 ID，不符就斷線，實質上就是 mutual TLS 加上 pinning（[Syncthing Device IDs](https://docs.syncthing.net/dev/device-ids.html)）。Moonlight 和 Sunshine 則讓使用者在伺服器端輸入 PIN，用 PIN 推導出的金鑰做 challenge/response，證明雙方看到的是同一對憑證。配對完成後，伺服器檢查每個請求的 client 憑證（[Wolf pairing protocol](https://games-on-whales.github.io/wolf/stable/protocols/http-pairing.html)）。

套用到 kikitori，流程如下：

1. 兩端各自產生 Ed25519 或 P-256 的自簽憑證。
2. 伺服器手動進入配對模式，顯示 6–8 位的一次性配對碼，限時、限次嘗試。
3. client 輸入 IP 和配對碼。
4. 以 HMAC-SHA256 把配對碼和雙方的憑證指紋（或 TLS exporter 值）綁在一起確認，防止中間人攻擊。
5. 配對成功後雙方 pin 對方的公鑰（SPKI），之後一律以 mutual TLS 連線。
6. 伺服器提供已配對裝置清單，可以撤銷。

OWASP 一般反對 pinning，但也說明前提是「兩端都由你掌控」時才適合，並且要求 pin 公鑰而不是整張憑證，也**不要給使用者「仍要繼續」的繞過按鈕**（[OWASP Pinning Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Pinning_Cheat_Sheet.html)）。

這裡選 HMAC 綁定而不是 PAKE，是因為 SPAKE2 只是 Informational RFC，不在 Standards Track 上（[RFC 9382](https://www.rfc-editor.org/rfc/rfc9382.html)），而 Rust 的 RustCrypto PAKE 實作明說「have not yet received any formal cryptographic and security reviews」（[RustCrypto/PAKEs](https://github.com/RustCrypto/PAKEs)）。不過 HMAC 綁定仍然是自訂協定，需要經過設計審查。

Sunshine 的 CVE-2024-45407 是必須寫成測試的教訓：中間人用新的 PIN 轉送配對請求，配對雖然失敗，**攻擊者的憑證卻在配對完成前就被寫入了信任清單**（[GHSA-jqph-8cp5-g874](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-jqph-8cp5-g874)）。配對狀態機只有在完整成功之後才能寫入信任，任何失敗分支都要丟棄暫存的憑證。

**網際網路使用請導向 overlay 網路，不要叫使用者開 port forwarding。** Tailscale 以 WireGuard 建立端對端加密，可以穿越 NAT 和防火牆，不需要開 port forwarding（[Tailscale](https://tailscale.com/kb/1151/what-is-tailscale)）。使用者在 kikitori 填 100.x 的 IP 或 MagicDNS 名稱即可，kikitori 本身完全不必處理 NAT 穿越或 UPnP。伺服器預設只綁區網介面和 overlay 介面。萬一使用者仍然直接暴露，mutual TLS 也能在 TLS 層就擋掉沒配對過的 client。

要注意的是，KDE Connect 1.35.x 曾在 Tailscale 上發生 TLS 握手失敗的版本回歸（[KDE Bug 515707](https://bugs.kde.org/show_bug.cgi?id=515707)），所以 overlay 網路要列入相容性測試。

**區網探索只是輔助，手動輸入 IP 才是主要路徑。** Windows 10 起內建 `DnsServiceRegister` 可以廣播服務（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/Windns/nf-windns-dnsserviceregister)），但企業環境常把 mDNS 的防火牆規則關掉（[Microsoft Tech Community](https://techcommunity.microsoft.com/blog/networkingblog/mdns-in-the-enterprise/3275777)）。Syncthing 也提醒，本機探索沒有任何密碼學保護，任何人都能冒用 ID 廣播（[Syncthing](https://docs.syncthing.net/dev/device-ids.html)）。建議伺服器廣播 `_kikitori._tcp`，TXT 記錄放協定版本和指紋前綴，指紋前綴只當作「是不是這台」的提示，最終身分仍由配對確認。

探索要放在 Rust（Tauri）端，用 `mdns-sd`（MIT/Apache，三平台都支援，不依賴 async runtime）（[mdns-sd](https://github.com/keepsimple1/mdns-sd)）。Python 的 `python-zeroconf` 是 LGPL-2.1-or-later（[python-zeroconf](https://github.com/python-zeroconf/python-zeroconf)），打包散布時會帶來額外的授權義務。TLS 則可以完全使用 Python 標準函式庫的 `ssl`，不必新增依賴。

**斷線重連要在應用層自己做。** 這幾個開源 Whisper server 的 README 都沒有提到 session resume，雲端則靠 client 主動重開串流。Google 的 endless streaming 範例每 4–5 分鐘主動開一條新串流，先重送上一條還沒定稿的音訊，再接續新音訊，並用偏移量校正時間戳（[Google streaming](https://docs.cloud.google.com/speech-to-text/docs/transcribe-streaming-audio)）。Deepgram 則在 10 s 沒有收到音訊或 KeepAlive 時關閉連線，建議每 3–5 s 送一次 KeepAlive（[Deepgram KeepAlive](https://developers.deepgram.com/docs/audio-keep-alive)）。

kikitori 照這個模式設計：

- **心跳**：每 3–5 s 一次應用層 ping，10 s 沒有任何資料就判定斷線。Wi-Fi 切換時 TCP 可能卡住數十秒才報錯，所以應用層心跳不能省。
- **重送緩衝**：client 在記憶體裡保留 30–60 s 還沒被 `ack_sample` 確認的音訊，不寫入磁碟。
- **重連**：以指數退避重連，帶上 `session_id` 和 `resume_from_sample`；伺服器把斷線的 session 保留 30–60 s 的寬限期。
- **長時間斷線**：丟掉最舊的音訊，在字幕上標示「連線中斷 mm:ss–mm:ss」，而不是事後一次湧出大量過期字幕。
- **退回本機**：超過設定秒數還沒恢復時，可以選擇自動切回本機小模型，或只是等待重連。

## 本機已有夠力 GPU 時，遠端只能打平

遠端的上限是「和本機一樣快，再加一個 RTT」，所以以下三種情況不划算：

- client 本身就有能跑 kikitori 管線的 NVIDIA GPU，例如 8 GB 以上的 RTX 30/40/50 系列（推論門檻）。
- 伺服器 GPU 比本機慢，或同時服務多人、已經在排隊。
- 路徑是壅塞的 Wi-Fi、行動網路或跨國連線。

split computing 的綜述也指出，通道品質、網路和 edge server 負載的不穩定，會讓 offload 的延遲顯著惡化（[ACM CSUR survey](https://dl.acm.org/doi/10.1145/3527155)）。Google 則以可靠性、隱私和延遲作為把 ASR 放在裝置上的理由（[arXiv 2003.12710](https://arxiv.org/pdf/2003.12710)）。

反過來，遠端最有價值的情境有三種：client 是只有內顯或 CPU 的筆電；client 的 GPU 已經被遊戲或直播編碼占滿；或使用者想用 client 跑不動的 large-v3 加大型 LLM。在這些情境裡，遠端 GPU 會把推論時間從秒級壓到百毫秒級，總延遲反而下降。SimulStreaming 也直說 CPU「would be too slow for real-time」（[SimulStreaming](https://github.com/ufal/SimulStreaming)）。

因此 App 應該實測之後再推薦，而不是讓使用者憑感覺選。做法是：連線時做約 20 次應用層 ping 取 RTT 的中位數和 P95，加上伺服器回報的 `server_ms` 和 `queue_ms`，和 GPU 提醒或推薦模組的本機量測比較。當遠端的 P95 比本機好超過一個門檻（例如 200 ms），或本機沒有可用的 GPU 時，才推薦遠端；執行中如果品質劣化，再提示切回本機。研究上，混合式系統依部分輸出決定要不要 offload，延遲約比全部 offload 低一半（[arXiv 2311.17065](https://arxiv.org/abs/2311.17065v1)），可見「動態選擇」比「固定走遠端」更有道理。業界目前沒有公開這類自動切換的設計文件，上面的門檻是推論，需要實測校正。

## 建議的分階段實作順序

| 階段 | 範圍 | 完成條件 |
|---|---|---|
| 0. 本機先改成增量模型 | 把 pipeline 的「每 0.7 s 重送整句」改成「append-only 音訊加上 server 端句子緩衝」的介面，暫時仍在同一個 process 裡跑；建立以樣本計數為準的時間軸 | 現有測試全數通過，本機延遲沒有退步 |
| 1. 區網原型 | 引擎新增無頭 `serve` 模式；`wss` 長連線、int16 PCM、100 ms 訊息；client 端 VAD 和 `segment_start`／`segment_end`；伺服器同時跑雙模型和 Ollama（Ollama 只綁 127.0.0.1）；`protocol_version` 握手；設定頁可以填 IP:port | 有線區網上 final 和本機相差 < 50 ms；SRT 時間戳和本機模式一致 |
| 2. 安全與配對 | 兩端自簽憑證、配對碼加 HMAC 綁定指紋、pin SPKI、mutual TLS、已配對清單與撤銷；配對失敗不留下任何信任紀錄 | 錯誤 PIN 和中間人情境的測試通過；沒配對過的 client 在 TLS 層就被拒絕 |
| 3. 韌性 | 3–5 s 心跳、10 s 判定斷線、`ack_sample` 加 30–60 s 重送緩衝、`resume_from_sample`、伺服器 session 寬限期、斷線標記、可選自動退回本機 | 用 clumsy／netem 注入 1–5% 掉包和斷線，字幕能接續而且不重複 |
| 4. 量測與推薦 | ping 量測、`server_ms`／`queue_ms` 回報、和本機量測比較後推薦；執行中劣化時提示 | 推薦結果和實測延遲方向一致 |
| 5. 多 client 與 GPU 共用 | 單一模型共享、final 優先、partial 覆寫佇列、跨 session batching、`max_clients` 和 busy 拒絕、Ollama 平行數設定 | 兩到四個 client 同時連線時，final 的 P95 在可接受範圍內 |
| 6. 網際網路與探索 | Opus 24–32 kbps（先用自家評估資料比較 CER）、文件推薦 Tailscale、Rust `mdns-sd` 探索、防火牆設定引導 | Opus 的 CER 劣化在門檻內；可以透過 Tailscale 連線 |
| 7.（選做）QUIC | 以 `quinn`／`aioquic` 改走獨立串流，消除 head-of-line blocking，支援連線遷移 | 掉包情境下的尾端延遲明顯優於 WebSocket |

新增依賴（Python `websockets`、Opus 綁定、Rust `mdns-sd`、`tokio-tungstenite`）都要先經使用者同意，並固定版本。Opus 綁定目前還沒有理想選擇：`opuslib` 需要系統另外安裝 libopus，`pyogg` 仍是 alpha 版（[PyPI opuslib](https://pypi.org/project/opuslib/)、[PyPI pyogg](https://pypi.org/project/PyOgg/)）。這也是把 Opus 排到後面階段的原因之一。

## 結論

這個功能的難點不在「能不能傳」，而在三個沒有現成答案的地方。第一，**雙模型 partial/final 的串流協定**，開源界沒有前例，必須自己設計。第二，**多人共用一張 GPU 時，Whisper 30 s 補零造成的固定 encoder 成本**，會讓 partial 的負載隨人數線性成長，所以排程策略比傳輸協定更影響實際體驗。第三，**Opus 對日文、中文 Whisper 的 CER 影響**，目前沒有任何公開數據，只能自己量。

換個角度看，遠端模式也逼 kikitori 把管線改成「append-only 音訊、樣本計數時間軸、partial 可覆寫」。這套模型讓本機與遠端共用同一個介面，日後要接其他雲端 STT 或第三台翻譯主機，也只是換一個 endpoint。把階段 0 當作純本機的重構先做完，就能在不承擔任何網路風險的前提下，先拿到這個架構上的好處。
