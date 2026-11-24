# 遠端 ASR 的傳輸協定與音訊編碼（kikitori：本機擷取與顯示，遠端跑 Whisper 和 Ollama）

> 研究日期：2026-11-09。前提：本機以 16 kHz mono 擷取音訊並跑 Silero VAD，遠端以 IP 指定主機，跑 Whisper（CTranslate2）與 Ollama；上行是音訊，下行是字幕文字（每次更新數百 bytes，每秒數次）。
> 標記說明：「（未於本次重新查證）」表示該事實出自標準或常識，本次沒有抓取原文確認，寫報告時請視為次要佐證。

## 1. 傳輸協定：WebSocket、gRPC、WebRTC、QUIC/WebTransport、純 TCP

### Takeaway
主流串流 ASR 服務的「伺服器對伺服器」介面都走 TCP 上的可靠串流（Deepgram、AssemblyAI、OpenAI server 端用 WebSocket；Google 用 gRPC `StreamingRecognize`），WebRTC 只在瀏覽器或行動裝置端推薦。對 kikitori（桌面 app 連一台指定 IP 的主機，ASR 不能掉任何音訊）來說，最合適的是 **WebSocket（`wss://`）＋應用層的序號、ACK 與重送緩衝**。QUIC 能解決 TCP 的 head-of-line blocking，也支援連線遷移，可以留作第二階段的升級選項；WebRTC 會為了壓低延遲主動丟棄音訊，NAT 處理也比較複雜，這兩點都不適合 ASR。

### Cited Findings
**主流語音 API 用什麼協定**
- Deepgram 串流端點是 WebSocket `wss://api.deepgram.com/v1/listen`。超過約 10 秒沒收到音訊或 KeepAlive，連線就會以 `NET-0001` 關閉，官方建議每 3–5 秒送一次文字訊框 `{"type":"KeepAlive"}` — [Deepgram KeepAlive](https://developers.deepgram.com/docs/audio-keep-alive)
- AssemblyAI Universal Streaming 是 WebSocket `wss://streaming.assemblyai.com/v3/ws`。預設格式為 mono 16-bit PCM，也接受 `encoding=opus`（raw Opus 封包）、`ogg_opus` 和 `aac`；官方範例每次讀 4096 bytes 的 chunk。計費依連線開著的時間，與送出多少音訊無關 — [AssemblyAI Universal Streaming](https://www.assemblyai.com/docs/speech-to-text/universal-streaming)
- OpenAI Realtime API 有兩種傳輸：瀏覽器端「connects over WebRTC in the browser or WebSocket on the server」。瀏覽器或行動 client 用 ephemeral client secret 透過 WebRTC 連線；受信任的伺服器用 WebSocket — [OpenAI Realtime guide](https://developers.openai.com/api/docs/guides/realtime)
- 第三方對 OpenAI WebRTC 的批評：Luke Curley 認為 WebRTC 為了維持低延遲，會積極丟棄音訊封包；Hacker News 上也有人反駁，說 WebRTC 有參數可以在延遲和品質之間取捨。這是社群意見，不是官方資料 — [Simon Willison 對 WebRTC 爭論的整理](https://simonwillison.net/tags/webrtc/)
- Google Cloud Speech-to-Text 的串流方法是 `StreamingRecognize`（RPC 介面）。單一串流上限約 5 分鐘音訊，音訊要以接近即時的速率送出；每則訊息上限 10 MB — [Google STT quotas](https://docs.cloud.google.com/speech-to-text/quotas)。（Google 的串流 API 是 gRPC 雙向串流，這點屬業界常識，未於本次重新查證。）
- Azure Speech SDK 在本機用 GStreamer 把 MP3、OPUS/OGG、FLAC 等格式解壓，「before it's sent over the wire to the Speech service as raw PCM」。也就是說，Azure SDK 實際上傳的是 16 kHz 或 8 kHz、16-bit、mono 的 PCM，壓縮格式只是輸入端的方便功能；JavaScript、Objective-C、Swift 版 SDK 則完全不支援壓縮輸入 — [Azure compressed audio input](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-use-codec-compressed-audio-input-streams)

**協定層的特性**
- HTTP/2 與 WebSocket 都跑在 TCP 上。HTTP/2 雖然能多工，但「if a single resource fails, all other resources on that connection are held up until any missing packets are retransmitted. With QUIC, only the failing resource is affected」，這是 head-of-line blocking；gRPC 跑在 HTTP/2 上，所以同樣受影響 — [MDN WebTransport API](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport_API)
- QUIC 的串流互相獨立：「QUIC does not provide any means of ensuring ordering between bytes on different streams」（§2）。連線「not strictly bound to a single network path」，換網路或 NAT 對應改變（NAT rebinding）後仍可繼續（§1、§9）。TLS 1.3 交握整合在協定內（§7、RFC 9001），支援 0-RTT，但 0-RTT 不防重放攻擊（§5） — [RFC 9000](https://www.rfc-editor.org/rfc/rfc9000.html)
- WebTransport 建在 HTTP/3（QUIC）上，提供可靠的雙向與單向串流，以及不可靠的 datagram。MDN 標示「Baseline 2026: Newly available」（2026 年 3 月起）。底層協定 WebTransport over HTTP/3 仍是 IETF draft（draft-ietf-webtrans-http3） — [MDN WebTransport API](https://developer.mozilla.org/en-US/docs/Web/API/WebTransport_API)
- 每個 WebSocket 訊框的額外負擔（RFC 6455）：client 送往 server 的訊框必須 mask，payload 小於 126 bytes 時 header 6 bytes，126–65535 bytes 時 8 bytes（未於本次重新查證） — [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)

**Rust 與 Python 函式庫（版本與授權取自 crates.io 和 PyPI API，2026-11-09）**
| 角色 | Rust crate（版本，授權） | Python 套件（版本，授權） |
|---|---|---|
| WebSocket | `tokio-tungstenite` 0.30.0，MIT | `websockets` 17.4，BSD-3-Clause |
| gRPC | `tonic` 0.14.9，MIT | `grpcio` 1.85.1，Apache-2.0 |
| WebRTC | `webrtc`（webrtc-rs）0.21.1，MIT/Apache-2.0；`str0m` 0.25.0，MIT OR Apache-2.0 | `aiortc` 1.16.0，BSD-3-Clause |
| QUIC | `quinn` 0.11.12，MIT OR Apache-2.0 | `aioquic` 1.7.2，BSD-3-Clause |
| WebTransport | `wtransport` 0.7.2，MIT OR Apache-2.0 | `aioquic`（含 HTTP/3 與 WebTransport 的範例，屬一般認知，未於本次查證） |
— 來源：[crates.io tokio-tungstenite](https://crates.io/crates/tokio-tungstenite)、[crates.io tonic](https://crates.io/crates/tonic)、[crates.io webrtc](https://crates.io/crates/webrtc)、[crates.io str0m](https://crates.io/crates/str0m)、[crates.io quinn](https://crates.io/crates/quinn)、[crates.io wtransport](https://crates.io/crates/wtransport)、[PyPI websockets](https://pypi.org/project/websockets/)、[PyPI grpcio](https://pypi.org/project/grpcio/)、[PyPI aiortc](https://pypi.org/project/aiortc/)、[PyPI aioquic](https://pypi.org/project/aioquic/)
- 累計下載數（crates.io，同日查詢）：tonic 約 4.65 億、quinn 約 4.07 億、tokio-tungstenite 約 3.51 億、webrtc 約 921 萬、str0m 約 302 萬、wtransport 約 117 萬。可以看出 WebSocket、gRPC、QUIC 三者在 Rust 生態系的使用量比 WebRTC、WebTransport 高出兩個數量級以上 — [crates.io API](https://crates.io/api/v1/crates/tonic)

### Inferences
- **為什麼選 WebSocket**：(1) 主流 ASR 服務的 server 端串流介面幾乎都用它（Deepgram、AssemblyAI、OpenAI server 端）；(2) Rust（tokio-tungstenite）和 Python（websockets）兩端都是成熟的 MIT/BSD 套件，Python 引擎可以直接用 asyncio 寫；(3) 同一條連線可以同時傳 binary 音訊訊框和 JSON 文字訊框（字幕、控制、KeepAlive），不需要額外的 schema 工具鏈；(4) `wss://` 走標準 TLS，必要時可以放在反向代理後面。
- **gRPC 的取捨**：有型別化的 `.proto` 合約、內建 deadline 與 keepalive，Google 也在用；但它同樣是 TCP（HTTP/2），HOL 特性和 WebSocket 一樣，還要多一套 protoc 與 codegen，grpcio 在 Windows 打包時也比較重（屬推論）。如果之後需要正式的多語言 SDK，再考慮 gRPC。
- **WebRTC 不適合的原因**：設計目標是人與人通話，jitter buffer 和 NetEQ 類機制會為了低延遲丟掉或拉伸遲到的封包，PLC 會補上「假音訊」，這些對 ASR 準確度都是傷害。另外還得處理 ICE、STUN、TURN 和 DTLS-SRTP。它唯一明顯的優勢是 NAT 穿透：兩端都在 NAT 後面時，可以靠 STUN 打洞或 TURN 中繼。kikitori 的情境是「client 主動連指定 IP」，只要伺服器那端可以連入（LAN、port forwarding 或 VPN），就用不到這個優勢。
- **HOL blocking 在本案影響多大**：上行只有一條音訊流，下行是零碎的文字，兩者共用一條 TCP 連線。封包遺失時，TCP 重傳會讓整條流暫停約一個 RTT 加上 RTO。家用網際網路 RTT 約 20–80 ms，重傳停頓通常是數十到數百 ms；Whisper 本來就以數百 ms 到數秒的 chunk 為單位處理，所以這段停頓多半會被吸收。QUIC 的主要好處在於下行字幕不會被上行音訊的重傳卡住，以及 Wi-Fi 切換或 NAT rebinding 時不必重新連線。
- **Wi-Fi jitter 怎麼處理**：可靠串流只會讓資料遲到，不會讓資料遺失。server 端以「音訊樣本時間軸」驅動 ASR，不依賴到達時間，jitter 就不會影響辨識結果，只會影響字幕出現的時間。
- **重連設計（不論選哪種協定都要做）**：每段音訊帶 `seq` 和 `start_sample`；server 定期回傳 `ack_sample`；client 保留一個環形緩衝，至少存放尚未被 ACK 的音訊（例如 30 秒 PCM 約 960 KB），斷線後以指數退避重連，帶 `session_id` 與 `resume_from_sample` 補送缺漏。WebSocket 本身沒有恢復機制，這部分要在應用層實作；QUIC 的連線遷移只能涵蓋 NAT rebinding，對 server 重啟無效。
- **TLS 與 NAT**：LAN 上可以先用 `ws://` 搭配 token 驗證；跨網際網路應改用 `wss://`。自簽憑證可以採 TOFU 或 certificate pinning，或者改走 WireGuard 類 VPN 讓流量留在私有網段，此時用 `ws://` 也可以接受（屬推論）。
- **QUIC/WebTransport 作為第二階段**：quinn 和 aioquic 兩端都有成熟實作。音訊用單一 unidirectional stream 傳，字幕用另一條，就能消除跨方向的 HOL。datagram 不可靠，ASR 不應該用。

### Gaps
- 沒有找到主流來源量測「WebSocket 對 gRPC 對 QUIC 在 Wi-Fi 封包遺失下的語音串流端到端延遲」的數字。這部分需要自己做實驗，例如用 `tc netem` 或 clumsy 注入 1–5% 遺失率。
- OpenAI 官方 WebRTC 與 WebSocket 指南頁面本次回傳 404，無法引用官方「何時推薦 WebRTC」的原文。目前只確認 Realtime 總覽頁的 browser 用 WebRTC、server 用 WebSocket 的區分。
- Google 串流 API 是 gRPC，這點本次只從 RPC 介面名稱間接確認，沒有取得原文。
- webrtc-rs 的維護狀態（是否在進行 sans-IO 重寫）沒有查證。

## 2. 音訊格式：PCM、Opus、FLAC 對 ASR 錯誤率、CPU、延遲與授權的影響

### Takeaway
Opus 在 **≥16–20 kbps 的寬頻（16 kHz）語音**下，對 ASR 的 WER 影響很小。在 LibriSpeech 上用 Conformer 量測：clean 2.01%，Opus 20 kb/s 為 2.03%，12 kb/s 為 2.07%，6 kb/s 才升到 3.08%。另一份以 Amazon 遠場 LSTM 系統做的研究，16 kbps 的相對 WER 劣化是 12.6%，32 kbps 是 6.6%，模型和資料差異會造成不同結果。**目前沒有找到主流來源直接量測 Whisper 在 Opus 下的 WER**，只有一篇 2026 年的 arXiv 論文用 Vaani-Whisper-large-v3 得到「marginal degradation」的定性結論。Google 官方建議能用無損就用無損（FLAC、LINEAR16），需要有損時依序選 AMR_WB、OGG_OPUS。建議做法：**LAN 傳 PCM，網際網路傳 Opus 24–32 kbps（VOIP 模式，20 ms 訊框）**，演算法延遲約 26.5 ms，可以忽略。

### Cited Findings
**Opus 對 ASR 的量測**
- NoLACE（Amazon Web Services，Büthe、Mustafa、Valin 等）：在 LibriSpeech test-clean 上用 SpeechBrain 大型 Conformer 量測 WER。clean 2.01%；Opus 6 kb/s 為 3.08%，9 kb/s 為 2.15%，12 kb/s 為 2.07%，20 kb/s 為 2.03%。論文指出「Opus coding has a significant impact on ASR performance at very low bitrates, increasing WER by roughly one pp at 6 kb/s」，bitrate 提高後「quickly converge to the clean speech WER」。測試用的 libopus 經過修改，限制為 linear-predictive（SILK）模式和 wideband — [arXiv 2309.14521](https://arxiv.org/pdf/2309.14521)
- Amazon（Khare、Sundaram、Wu）：2 通道、16 kHz 遠場資料，LSTM 聲學模型以未壓縮音訊訓練。相對 WER 劣化為 8 kbps 202.1%（Opus 在 8 kbps 會改成 narrowband 編碼）、16 kbps 12.6%、32 kbps 6.6%、128 kbps 2.4%。VBR 與 CBR 之間「didn't show a significant difference」。用混合 bitrate 訓練可以挽回最多 50% 的劣化 — [arXiv 2002.00122](https://arxiv.org/pdf/2002.00122)
- 印度語系研究（ARTPARK／IISc，2026-06）：用 Vaani-whisper-large-v3（Hindi）和 Indic-Conformer 測試訊號層因素，結論是「Simulated Narrowband(3G) / Wideband(4G) conditions preserve accuracy close to the original 16 kHz audio, while Opus (5G) introduces only marginal degradation」。論文沒有寫 Opus 用的 bitrate，數字只出現在圖表裡。另一個值得注意的結果：用神經網路修復音訊（VoiceFixer、AudioSR）反而讓 WER 變差 — [arXiv 2606.09335](https://arxiv.org/pdf/2606.09335)
- Interspeech 2025（Tseng 等）以 Whisper-Large 的 WER 評估神經編解碼器（DAC、SpeechTokenizer、HiFi-Codec）在噪音下的穩健性，但沒有測 Opus — [Interspeech 2025 Tseng et al.](https://www.isca-archive.org/interspeech_2025/tseng25_interspeech.pdf)

**廠商建議**
- Google：「Use a lossless codec to record and transmit audio. `FLAC` or `LINEAR16` is recommended」；有損格式「data loss may affect recognition accuracy」，若必須使用，「we recommend the `AMR_WB` or `OGG_OPUS` codecs, in that preferred order」。另外建議「Capture audio with a sampling rate of 16,000 Hz or higher」並「avoid re-sampling」 — [Google STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)、[Google STT encoding](https://docs.cloud.google.com/speech-to-text/docs/encoding)
- Google 的 OGG_OPUS 和 WEBM_OPUS 取樣率必須是 8000、12000、16000、24000 或 48000 Hz 之一 — [Google STT encoding](https://docs.cloud.google.com/speech-to-text/docs/encoding)
- Deepgram Flux 的 raw 音訊支援 `linear16`、`linear32`、`mulaw`、`alaw`、`opus`、`ogg-opus`，容器格式支援 WAV、Ogg/Opus、WebM/Opus；整體可接受的格式還包括 `flac`、`amr-wb`、`speex` 等 — [Deepgram encoding](https://developers.deepgram.com/docs/encoding)

**Opus 規格：延遲、訊框、FEC、授權**
- 涵蓋範圍從 6 kbit/s narrowband 到 510 kbit/s fullband stereo，「algorithmic delays ranging from 5 ms to 65.2 ms」 — [RFC 6716 §2](https://www.rfc-editor.org/rfc/rfc6716)
- SILK（LP 層）支援 10–60 ms 訊框，「requires an additional 5 ms look-ahead for noise shaping estimation」，取樣率轉換可能再增加最多 1.5 ms。CELT（MDCT 層）支援 2.5–20 ms 訊框，需要 2.5 ms look-ahead — [RFC 6716 §2](https://www.rfc-editor.org/rfc/rfc6716)
- 寬頻語音的甜蜜點是「16-20 kbit/s for WB speech」（20 ms 訊框）；訊框可選 2.5、5、10、20、40、60 ms，一個封包最長可裝 120 ms — [RFC 6716](https://www.rfc-editor.org/rfc/rfc6716)
- 規格內建 in-band FEC（§2.1.7，LBRR：把重要訊框以較低 bitrate 重編碼後夾帶在後續封包）和 PLC（§4.4）；complexity 參數範圍 0–10 — [RFC 6716](https://www.rfc-editor.org/rfc/rfc6716)
- libopus API：`OPUS_APPLICATION_VOIP` 在語音上能給出同 bitrate 下最好的品質；`OPUS_APPLICATION_AUDIO` 適合音樂或混合內容，也適用於需要「less than 15 ms of coding delay」的應用；`OPUS_APPLICATION_RESTRICTED_LOWDELAY` 會關閉語音最佳化模式以換取稍低的延遲。小於 10 ms 的訊框無法使用 LPC 或 hybrid 模式；`max_packet` 建議設 4000 bytes — [libopus encoder API](https://opus-codec.org/docs/opus_api-1.5/group__opus__encoder.html)
- Opus 是「a totally open, royalty-free」codec，bitrate 6–510 kb/s，訊框 2.5–60 ms — [opus-codec.org](https://opus-codec.org/)。RFC 內的程式碼採 Simplified BSD License — [RFC 6716](https://www.rfc-editor.org/rfc/rfc6716)。（libopus 參考實作採 BSD-3-Clause，屬業界常識，未於本次重新查證。）
- RTP 承載 Opus 時，timestamp 一律以 48000 Hz 時脈遞增，與實際取樣率無關；`ptime` 預設 20 ms。DTX「MUST drop whole frames only」，且 RFC 建議除非頻寬非常受限，否則應連續傳送 — [RFC 7587](https://www.rfc-editor.org/rfc/rfc7587.html)

**Opus 綁定函式庫授權（crates.io／PyPI，2026-11-09）**
- Rust：`opus` 0.4.0（MIT/Apache-2.0，綁 libopus，2026-08 更新）；`audiopus` 0.2.0 和 `audiopus_sys` 0.2.2（ISC，最後更新 2021-04，已停止維護）；`opus-rs` 0.1.41（BSD-3-Clause，2026-11 更新）— [crates.io opus](https://crates.io/crates/opus)、[crates.io audiopus](https://crates.io/crates/audiopus)、[crates.io opus-rs](https://crates.io/crates/opus-rs)
- Python：`opuslib` 3.0.1（BSD，需要系統另裝 libopus）；`pyogg` 0.6.14a1（BSD-3，仍是 alpha 版）；`av`（PyAV）19.0.2（BSD-3-Clause，wheel 內附 FFmpeg，可解碼 Opus）— [PyPI opuslib](https://pypi.org/project/opuslib/)、[PyPI pyogg](https://pypi.org/project/PyOgg/)、[PyPI av](https://pypi.org/project/av/)

### Inferences
- **預設設定的演算法延遲**：SILK 或 hybrid 模式下，20 ms 訊框加上 5 ms look-ahead 和 1.5 ms 重取樣，共約 26.5 ms（從 RFC 6716 的數字推算）。相較於 Whisper 動輒數百 ms 的 chunk 和推論時間，這段延遲可以忽略；為了降低延遲改用 RESTRICTED_LOWDELAY（CELT-only）並不划算，因為會失去語音最佳化。
- **建議的 Opus 參數**：`OPUS_APPLICATION_VOIP`，16 kHz 輸入（`opus_encoder_create(16000, 1, …)` 可以直接吃 16 kHz，不需要先升到 48 kHz），bitrate 24–32 kbps VBR，20 ms 訊框，complexity 5–10。依照 NoLACE 的數字，12–20 kb/s 時 WER 只比 clean 高 0.02–0.06 個百分點；保守取 24–32 kbps 可以涵蓋 Amazon 研究中 16 kbps 劣化 12.6% 的情況。TCP 上不會掉封包，所以 FEC 和 DTX 都**關掉**。
- **PCM 與 FLAC 的取捨**：FLAC 是無損格式，Google 明確推薦，但它是 block-based 編碼，每個 block 要等滿 4096 個樣本（16 kHz 下約 256 ms）才能輸出，串流延遲會變高；對語音的壓縮率約為 PCM 的 50–70%，這是一般經驗值，本次沒有取得來源。在 LAN 上直接送 PCM 最簡單、零失真、CPU 成本為零。
- **Whisper 有沒有實測**：沒有直接量測，只能以 Conformer 和 LSTM 的研究，加上 Vaani-Whisper 的定性結論外推。Whisper 以 68 萬小時網路音訊訓練，其中本來就有大量經有損壓縮的音訊，因此 Opus 32 kbps 的影響應該很小，但這仍是推論。**建議在 kikitori 自己的評估資料上實測**：PCM 對照 Opus 16、24、32 kbps，比較 CER 和 WER。
- **CPU 成本**：Opus 的設計目標是在手機上即時進行雙向編解碼，16 kHz mono 單一串流對桌面 CPU 的負擔應遠小於 1%（推論，本次沒有取得基準測試數字）。

### Gaps
- **沒有找到主流來源（Interspeech、ICASSP）直接量測 OpenAI Whisper 在 Opus 或其他有損編碼下的 WER 或 CER 數字**，日語、中文的 CER 也沒有。
- libopus 編解碼的 CPU 時間（例如 x 倍即時、每訊框微秒數）沒有取得官方或論文數據。
- NoLACE 論文的正式發表場合（是否為 ICASSP 2024）本次沒有確認，只引用 arXiv 版。
- FLAC 對 16 kHz 語音的實際壓縮率沒有找到來源。
- libopus 本體的授權條文本次沒有直接抓取原文確認。

## 3. 頻寬在 LAN 與家用上傳頻寬下是不是問題

### Takeaway
不是瓶頸。PCM 16 kHz/16-bit/mono 為 256 kbps，加上協定負擔約 270–290 kbps，只占 FCC 寬頻基準上傳 20 Mbps 的約 1.4%，在 LAN（≥100 Mbps）上更可以忽略。Opus 24 kbps 以每 100 ms 一則訊息送出時，線上流量約 30 kbps，大約省下 9 倍。真正的考量是**行動熱點、計量網路，或上行與其他流量（例如直播推流）爭用**時的穩定性，以及 Wi-Fi 遺失後 TCP 重傳的資料量。下行字幕流量極小：數百 bytes × 每秒數次，約 2–10 kbps。

### Cited Findings
- FCC 於 2024 年 3 月把固網寬頻基準提高到 100 Mbps 下載、20 Mbps 上傳（舊基準為 25/3 Mbps），並決定不採用對稱的 100/100 基準 — [Tom's Hardware 報導](https://www.tomshardware.com/service-providers/network-providers/fcc-quadruples-requirements-for-basic-broadband-service-100mbps-download-and-20mbps-upload-are-now-the-base-standard)、[Benton Institute](https://www.benton.org/headlines/federal-communications-commission-increases-broadband-speed-benchmark)（2026 年 11 月時這個基準是否仍有效，本次沒有查證）
- Opus 寬頻語音的甜蜜點是 16–20 kbit/s — [RFC 6716](https://www.rfc-editor.org/rfc/rfc6716)
- WebSocket client 訊框的 header 為 6–8 bytes（未於本次重新查證） — [RFC 6455](https://www.rfc-editor.org/rfc/rfc6455)

### Inferences（以下數字是計算結果，不是量測）
- PCM：16000 × 16 bit = **256 kbps**，等於 32 KB/s，每小時約 115 MB。
- 每則訊息的額外負擔（推算）：IPv4 20 + TCP 20–32 + TLS 1.3 record 約 22（5 header + 16 AEAD tag + 1 content type）+ WebSocket 8，合計每個 TCP segment 約 70–82 bytes。
  - PCM 每 20 ms 一則（640 B）：每秒 50 則，負擔約 13%，總計約 **290 kbps**。
  - PCM 每 100 ms 一則（3200 B，會切成約 3 個 MSS 1448 的 segment）：負擔約 4–5%，總計約 **268 kbps**。
  - Opus 24 kbps 每 20 ms 一則（約 60 B payload）：header 比 payload 還大，總計約 **55 kbps**。
  - Opus 24 kbps 每 100 ms 一則（5 個訊框，約 300 B，加上每訊框 2 bytes 長度前綴）：總計約 **30 kbps**。
  - 作為對照，WebRTC 的 RTP 封包（IP 20 + UDP 8 + RTP 12 + SRTP tag 10）以 Opus 24 kbps、20 ms 計算，約 **44 kbps**。
- 結論：LAN 上 PCM 的成本幾乎是零，網際網路上 PCM 也只占典型上傳頻寬的 1–3%；但在 4G/5G 熱點、旅館 Wi-Fi，或同一台電腦同時在做 OBS 直播推流時，壓縮能明顯降低 TCP 擁塞和重傳的風險。
- **用 VAD 送門控音訊**：client 只在 Silero VAD 判定有語音時才送（另加 pre-roll 和 hangover），一般對話的實際占比通常更低，平均頻寬可以再降一半以上（推論）。不過 server 端如果需要上下文或自己跑 VAD，就要改成連續傳送。

### Gaps
- 沒有取得 2026 年台灣、日本等目標市場的家用上傳頻寬中位數（例如 Ookla Speedtest Global Index）。
- 上面的負擔數字是推算值，沒有實際抓封包驗證。

## 4. 串流 chunk 與訊框大小的建議

### Takeaway
codec 訊框用 **20 ms**（Opus 的標準與預設），網路訊息每 **60–100 ms 送一則**（打包 3–5 個 Opus 訊框，或 960–1600 個 PCM 樣本）。Google 官方明確建議 100 ms，認為這是延遲和效率之間的好平衡。Silero VAD 的 512-sample 視窗（16 kHz 下 32 ms）屬於本機處理單位，不需要和網路訊息對齊；網路訊息只要帶上 `start_sample`，server 就能自行切分。

### Cited Findings
- Google：「Any frame size is acceptable. Larger frames are more efficient, but add latency. A 100-millisecond frame size is recommended as a good tradeoff between latency and efficiency.」 — [Google STT best practices](https://docs.cloud.google.com/speech-to-text/docs/best-practices)
- AssemblyAI 範例每次讀 4096 bytes 的 chunk；以 16-bit 16 kHz PCM 計算約 128 ms — [AssemblyAI Universal Streaming](https://www.assemblyai.com/docs/speech-to-text/universal-streaming)
- Opus 可用的訊框為 2.5、5、10、20、40、60 ms，小於 10 ms 會失去 LPC 和 hybrid 模式；RTP 的預設 `ptime` 是 20 ms — [libopus encoder API](https://opus-codec.org/docs/opus_api-1.5/group__opus__encoder.html)、[RFC 7587](https://www.rfc-editor.org/rfc/rfc7587.html)
- Google 串流必須以接近即時的速率送出音訊 — [Google STT quotas](https://docs.cloud.google.com/speech-to-text/quotas)

### Inferences
- **為什麼選 100 ms**：大約一個家用 RTT 的量級；每秒只有 10 則訊息，header 負擔低（見第 3 節）；Whisper 的 live 推論本來就要累積數百 ms 才值得跑一次，訊息間隔再縮短也不會讓字幕更早出現。在 LAN 上若想讓 partial 更早出現，可以降到 40–60 ms。
- **20 ms、512 樣本、100 ms 怎麼對齊**：Opus 20 ms 在 16 kHz 下是 320 樣本；Silero 視窗 512 樣本是 32 ms；兩者的最小公倍數是 2560 樣本（160 ms）。實作上不需要對齊：client 端 VAD 照常以 512 樣本跑，送網路時另外以 1600 樣本（100 ms）為單位切塊，每塊帶 `start_sample`。如果 VAD 結果也要傳給 server，就附上 `vad_events[{sample, speech_prob}]`。
- **避免 Nagle 延遲**：用 TCP 傳小訊息時要設定 `TCP_NODELAY`，tokio 的 `TcpStream::set_nodelay(true)` 可以做到（屬常識，未於本次查證）。

### Gaps
- 沒有找到主流來源量測「訊息間隔對串流 Whisper 端到端延遲」的曲線。

## 5. 時脈與時間戳：讓 client 端字幕對齊、能匯出 SRT

### Takeaway
以 **client 端的「音訊樣本計數器」**（session 開始以來的 u64 樣本數，以 16 kHz 為時基）作為唯一的權威時間軸，不要用 server 的接收時間或牆上時鐘。每段音訊帶 `start_sample`；server 回傳的每則字幕都以樣本或毫秒表示，且相對於該時間軸（`start_sample`、`end_sample`）；client 再換算成 SRT 時間並對應到本機 wall clock。Deepgram 的時間戳也是這樣設計：時間跟著音訊串流走，與連線時間無關。

### Cited Findings
- Deepgram：word timings 跟隨音訊串流，不跟隨連線的 wall-clock 時間。暫停送音訊、只送 KeepAlive，之後恢復送音訊時，時間戳會從音訊停下的地方接續 — [Deepgram KeepAlive](https://developers.deepgram.com/docs/audio-keep-alive)
- RTP 承載 Opus 時，timestamp 一律以 48 kHz 時脈遞增；DTX 只能丟整個訊框，所以連續的 timestamp 差值是 120 的倍數 — [RFC 7587](https://www.rfc-editor.org/rfc/rfc7587.html)
- Opus 的編碼器 look-ahead：SILK 5 ms 加上取樣率轉換最多 1.5 ms，CELT 2.5 ms，hybrid 時 CELT 輸入會再延後 2.7 ms 以對齊兩層 — [RFC 6716 §2](https://www.rfc-editor.org/rfc/rfc6716)

### Inferences
- **協定設計（建議）**：
  - 上行 binary 訊息：`{seq: u32, start_sample: u64, n_samples: u32, codec: pcm16|opus, payload}`；如果音訊因 VAD 門控而中斷，`start_sample` 可以跳號，server 依此知道中間有靜音空白，不必傳靜音。
  - 下行 JSON：`{type: partial|final, seg_id, start_sample, end_sample, text, lang, translation?}`；同一個 `seg_id` 的 partial 會被後續訊息覆寫。
  - `ack_sample` 讓 client 能釋放重送緩衝。
- **Opus 解碼延遲的補償**：解碼後的 PCM 會比原始輸入晚編碼器的 look-ahead。可以用 `OPUS_GET_LOOKAHEAD` 取得這個樣本數，在 server 端換算時間戳時扣掉，或在 client 端統一校正。Ogg Opus 的 pre-skip 也是同樣的用途（推論，RFC 7845 本次沒有查證）。在 TCP 上不會掉封包，所以樣本數可以單純累加。
- **時鐘漂移**：只要時間軸是「樣本數」，就不需要 NTP 同步兩台機器，SRT 匯出的時間完全由 client 的擷取樣本決定。只有在需要「字幕何時顯示在畫面上」的延遲統計時，才需要記錄本機 wall clock 與樣本數的對應。系統音訊擷取裝置的實際取樣率可能和標稱的 16 kHz 略有出入，或因重取樣產生偏差；這只影響本機，與網路無關，沿用現有的本機作法即可。
- **重連後的時間軸**：用 `resume_from_sample` 續接，時間軸不重置；server 重啟後也必須接受 client 指定的 `start_sample` 作為新時間的起點。

### Gaps
- 沒有找到主流文件直接說明「遠端 ASR 時間戳應以樣本計數為權威」的通用最佳實踐，目前的依據是 Deepgram 的行為加上推論。
- RFC 7845（Ogg Opus pre-skip）本次沒有抓取原文確認。
