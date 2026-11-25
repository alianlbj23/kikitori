# 用戶端與遠端 GPU 之間的切分點與預期延遲

> 研究日期：2026-11-09。範圍：kikitori 的擷取／顯示放在電腦 A，ASR＋翻譯放在電腦 B（以 IP 連線）。
> 標「推論」的數字是依引用資料自行計算或推估的，不是量測值。

## 1. 有哪些切分方式？各自的取捨？論文與業界系統怎麼建議？

### Takeaway
最好的切分點是方案 (b)：用戶端跑 Silero VAD（ONNX），只送語音段（含 pre-roll），格式是 append-only 的 16 kHz int16 PCM 或 Opus。ASR 與翻譯都放在伺服器，同一台機器。方案 (c)（送 log-mel 或 Whisper encoder 輸出）沒有好處：log-mel 和 PCM 一樣大，encoder 輸出比原始音訊大好幾倍，對現成的 Whisper 不可行。方案 (d)（只把文字送去遠端 Ollama）頻寬最低，但用戶端仍要有能跑 ASR 的 GPU。

### Cited Findings
- whisper_streaming 的伺服器模式接受 TCP 連線，輸入是原始 PCM：16 kHz、mono、S16_LE。官方範例用 `arecord | nc` 傳送。VAD/VAC 跑在伺服器端，而 `--vac` 需要 torch — [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)
- whisper_streaming 的 README 表示它在 2025 年已漸過時，由 SimulStreaming 取代。SimulStreaming 自稱「約快 5 倍」，並拿下 IWSLT 2025 Simultaneous Speech Translation 第一名。它的 Whisper 部分使用 torch，`--vac` 需要 torch，伺服器同樣是 TCP＋raw S16_LE，建議 GPU 至少 10 GB VRAM，授權為 MIT — [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)
- WhisperLive 用 WebSocket 傳輸，音訊是 mono 16 kHz PCM。用戶端 `pcm_format` 預設 int16，也可選 float32；伺服器端 `audio_format` 預設 float32。VAD 跑在伺服器（`use_vad`：「Voice Activity Detection on the server」）。後端有 faster_whisper（CTranslate2）、TensorRT、OpenVINO，授權為 MIT — [collabora/WhisperLive](https://github.com/collabora/WhisperLive)
- Opus 在 20 ms frame 下的 bitrate「sweet spot」：寬頻（WB）語音 16–20 kbit/s，窄頻（NB）8–12 kbit/s。frame 可以是 2.5/5/10/20/40/60 ms，algorithmic delay 介於 5–65.2 ms — [RFC 6716](https://datatracker.ietf.org/doc/html/rfc6716)
- Whisper 的輸入是 80 個 channel 的 log-mel spectrogram：25 ms window，10 ms stride — [Radford et al., Whisper paper](https://arxiv.org/abs/2212.04356)。large-v3 改用 128 個 mel bins — [openai/whisper-large-v3 model card](https://huggingface.co/openai/whisper-large-v3)
- Edge–cloud 語音翻譯的 split inference（ESRT，arXiv 2605.28642，2026，preprint，未見會議）：裝置上跑輕量 speech encoder＋adapter，只上傳壓縮後的 tensor。上傳量比原始音訊小 5.1 倍（ESRT）或 10.2 倍（ESRT-Lite）。這是專門設計並訓練的模型，摘要頁沒有延遲數字，也沒有比較其他切分點 — [arXiv 2605.28642](https://arxiv.org/abs/2605.28642)
- 有 survey 指出，offload 雖然能減輕裝置運算，但通道品質、網路與 edge server 負載的不穩定，可能造成顯著的執行延遲 — [Matsubara et al., Split Computing and Early Exiting survey, ACM CSUR](https://dl.acm.org/doi/10.1145/3527155)
- 混合式的做法（裝置先跑，只把超出能力的輸入 offload 到雲端，並依部分輸出決定是否 offload）：PASU 原型的端到端延遲約降為 1/2，offload 需求也減半 — [Wang & Lin, arXiv 2311.17065](https://arxiv.org/abs/2311.17065v1)。另一篇用 cache 在裝置端解決 45–90% 的輸入，平均延遲最多降 80% — [arXiv 2311.18188](https://arxiv.org/pdf/2311.18188v4.pdf)。這兩篇在搜尋結果中都沒有標明會議。
- Google 的串流 on-device E2E ASR 論文以可靠性、隱私與延遲作為在裝置端跑的理由 — [arXiv 2003.12710](https://arxiv.org/pdf/2003.12710)
- 一份舊的混合語音辨識專利提到，對遠端伺服器的 HTTP 呼叫延遲「可達 100 ms 到 500 ms」 — [US patent 8249877](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/8249877)

### Inferences
- **頻寬試算（推論）**
  - 原始 int16 PCM：16 kHz × 16 bit = 256 kbit/s，約 32 KB/s。
  - float32：512 kbit/s。WhisperLive 伺服器預設 float32，若用戶端改送 int16 可省一半。
  - Opus WB 16–20 kbit/s 約為 int16 PCM 的 1/13–1/16。
- **(a) 連續送音訊，VAD 在伺服器**
  - 優點：實作最簡單，whisper_streaming、SimulStreaming、WhisperLive 都是這種架構。
  - 缺點：靜音與 BGM 也要上傳。斷句判斷要多經一段網路，用戶端才知道一句話結束；不過音訊本來就在伺服器上，這點影響很小。
  - 頻寬：256 kbit/s（PCM），在 LAN 上可以忽略。
- **(b) VAD 在用戶端，只送語音段（建議）**
  - Silero ONNX 很小。kikitori 已經在本機跑它，不需要 PyTorch。
  - 只要 VAD 判定說話中，就以 20–32 ms 的小封包持續 append 上傳，不必等整句。這樣 partial 的節奏（每 0.7 s）和本機一樣：伺服器手上一直有到「現在」為止的音訊。
  - 句尾 0.6 s 靜音由用戶端判定，再送一個 `segment_end` 控制訊息。伺服器收到後立刻跑 final。這比伺服器自己跑 VAD 少一個「等 hangover 音訊上傳」的步驟，但差距只有幾 ms 的上傳時間。
  - 省下的頻寬取決於說話比例。例如 VTuber 直播 BGM 很多、說話比例 40% 時，平均上行約降到 0.4 倍（推論）。
  - 風險：VAD 漏切或切錯時，伺服器無法自行補救。pre-roll（例如 200–300 ms）必須在用戶端的 ring buffer 裡保留。
- **(c) 送 log-mel 或 encoder 輸出：不建議，對現成 Whisper 不可行**
  - log-mel：80 bins × 100 frame/s × 4 bytes ≈ 32 KB/s（fp32）。128 bins（large-v3）≈ 51 KB/s。這和 int16 PCM（32 KB/s）一樣大甚至更大，且比 Opus 大一個數量級，用戶端也沒省到多少運算（推論）。
  - Whisper encoder 輸出：large 的 d_model 為 1280，30 s 視窗是 1500 frames，fp16 約 1500 × 1280 × 2 ≈ 3.8 MB/視窗；同樣 30 s 的 int16 PCM 只有 960 KB（推論）。
  - 只有像 ESRT 那樣專門訓練壓縮表示的模型，split 才省頻寬。kikitori 要用現成的 Whisper/CTranslate2，所以不可行。
- **(d) ASR 留在本機，只把文字送去遠端 Ollama**
  - 頻寬最小（幾百 bytes/行），已經能用。
  - 只在用戶端 GPU 能跑 ASR、但跑不動 LLM 時有意義。延遲增加約「每個翻譯請求 1 個 RTT」，加上 token streaming 每批 1 個 one-way delay（推論）。
- **(e) ASR 在遠端時，翻譯也應放遠端，與 ASR 同機**
  - 否則文字要「伺服器 → 用戶端 → Ollama」多繞一趟。前文脈絡（previous lines）也應保存在伺服器的 session 裡。
  - 例外：翻譯由用戶端或第三台機器的 Ollama 負責時（混合部署），仍可行，但每句多 1 個 RTT（推論）。

### Gaps
- 沒有找到 2022–2026 年 Interspeech、ICASSP 或 MobiSys/MobiCom 中專門比較「ASR 音訊 vs 特徵 vs 中間 tensor」切分點延遲的論文。搜尋結果多為 arXiv preprint，會議版本無法確認。
- 沒有找到業界（Google、Microsoft、Deepgram 等）公開說明串流 ASR 用戶端 VAD 與伺服器 VAD 取捨的一手文件。

## 2. 延遲預算：網路 RTT、GPU 推論時間，遠端在 LAN 與網際網路上是否仍屬即時？

### Takeaway
網路只是在本機延遲上加約 1 個 RTT：partial 約 0.3 s，final 約 0.9 s。
- 有線 LAN 約加 <1–5 ms。
- 5 GHz Wi-Fi 通常加 2–20 ms，但壅塞 AP 的尾端可達 100 ms 以上。
- 家用網際網路中位數約 13–50 ms，負載下約 66–110 ms。

所以只要伺服器 GPU 不比本機慢，LAN 上 final 約 0.9–1.0 s，網際網路上約 1.0–1.2 s，都在 1–2 s 的即時範圍內。主要風險是 Wi-Fi 與上行負載造成的抖動和尾端延遲，不是頻寬。

### Cited Findings
- WiLy（INFOCOM 2016）：在 47 台校園 AP 上量測超過 2 TB 的資料。某些 AP 上超過 50% 的 TCP 封包 WiFi hop 延遲大於 20 ms，10% 大於 100 ms；宿舍 AP 也有 10% 的封包大於 30 ms。約一半的情況下，WiFi hop 佔整個 round-trip network latency 的 60% 以上。把 AP 移位後，中位數由 50 ms 降到 10 ms；最大延遲則可從 250 ms 降到 50 ms。量測以 2.4 GHz 使用者居多 — [WiFi Can Be the Weakest Link of Round Trip Network Latency in the Wild (INFOCOM'16)](https://1989chenguo.github.io/Publications/WiLy-INFOCOM16.pdf)
- 住宅 Wi-Fi hotspot 研究：one-way jitter 約 9.2–13.7 ms，在 VoIP 常用的「jitter ≤ 30 ms」經驗值以內 — [arXiv 1608.06606](https://arxiv.org/pdf/1608.06606)
- Ookla（新聞轉述，非一手資料）：美國 Q1 2025 的 median multi-server latency，Wi-Fi 7 相對 Wi-Fi 5/6 沒有明顯改善，Wi-Fi 4 則差很多。英國 EE 的 Wi-Fi 7 median latency 為 17 ms（2024 年底，比 Wi-Fi 6 好 12%）。十國樣本的 Wi-Fi 7 median 為 13 ms，比 Wi-Fi 6 低約 11% — [ISPreview: Ookla Wi-Fi 6 vs 7](https://www.ispreview.co.uk/index.php/2025/02/ookla-examine-wifi-6-vs-7-performance-of-ees-uk-broadband-routers.html)、[Telecompetitor: Wi-Fi 7 adoption](https://www.telecompetitor.com/one-year-in-wi-fi-7-adoption-at-2-in-u-s-report/)
- Ookla Q2 2026：T-Mobile Home Internet 的 median multi-server latency 為 46 ms，Verizon 5G Home 為 53 ms（FWA） — [IEEE ComSoc Techblog 轉述 Ookla](https://techblog.comsoc.org/2026/08/27/ookla-u-s-fixed-wireless-access-crosses-17-million-connections-as-adoption-shifts-from-rural-to-urban/)。市營光纖網路 UTOPIA 約 6–8 ms — [Community Networks 轉述 Ookla](https://communitynetworks.org/content/municipal-broadband-leaves-big-national-isps-dust-report-finds)
- FCC Measuring Broadband America 資料的學術分析：latency under load 的中位數由 107.6 ms 降到 66 ms（降 38.7%）；cable 與 fiber 表現最好，DSL 最差 — [Bajpai et al., An Eight Years Perspective on the Internet](https://vaibhavbajpai.com/documents/papers/proceedings/fcc-networking-2022.pdf)。另一分析指出 cable、FTTH 與 DSL 下行的負載延遲平均在 250 ms 以下，但 DSL 上行負載時不是 — [TMA 2024 paper](https://dl.ifip.org/db/conf/tma/tma2024/tma2024-final4.pdf)
- FCC 2023 年報告把 idle latency 與下行／上行負載下的延遲並列（Chart 7）。這裡沒有取得逐項數值 — [FCC 2023 MBA Fixed Report](https://data.fcc.gov/download/measuring-broadband-america/2023/2023-Fixed-Measuring-Broadband-America-Report.pdf)
- Whisper 把輸入補零（pad）到 30 s，所以 encoder 不管實際語音多長都付固定成本。在 mobile NPU 上，補到 30 s 的 encoder 約需 720 ms，是不補零時的 3.8 倍。補零的主要作用是抑制短輸入的幻覺 — [NPUsper, arXiv 2607.01108](https://arxiv.org/pdf/2607.01108)
- faster-whisper 的 benchmark（RTX 3070 Ti，13 分鐘音訊，large-v2，beam 5）：fp16 1m03s／4525 MB VRAM；int8 59s／2926 MB；batch_size=8 fp16 17s／6090 MB；int8 batched 16s／4500 MB — [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- whisper_streaming（LocalAgreement-2，論文摘要）在未分段的長篇轉錄測試集上報告約 3.3 s 延遲 — [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)
- 有專利文件把延遲需求分成兩類：對話式語音分析不能超過約 0.5 s 端到端延遲，句子轉錄則可接受數秒（2–4 s） — [search result: USPTO 12206552](https://image-ppubs.uspto.gov/dirsearch-public/print/downloadPdf/12206552)

### Inferences
- **延遲模型（推論）**：假設方案 (b)，音訊以小封包持續上傳，伺服器 GPU 與本機 RTX 5070 同級。
  - partial ≈ 本機 0.3 s + 上行 one-way + 下行 one-way ≈ 0.3 s + 1 RTT。
  - final ≈ 本機 0.9 s + 1 RTT。`segment_end` 訊息上行一次，final 文字與翻譯 token 下行串流。
  - 翻譯 token 是 pipeline 式回傳，只在第一個 token 付一次 one-way delay，之後不累加。
- **上傳時間可忽略（推論）**：每 0.7 s 新增的音訊，int16 為 22.4 KB，Opus 20 kbit/s 約 1.75 KB。即使上行只有 10 Mbit/s，傳輸時間也約 18 ms 或 1.4 ms。
- **各環境預估（推論）**

  | 環境 | 額外 RTT | partial | final（中位數） |
  |---|---|---|---|
  | 有線 LAN | ~0.3–1 ms（一般乙太網路經驗值，本次未找到量測引用，見 Gaps） | ≈ 0.30 s | ≈ 0.90 s |
  | 5 GHz Wi-Fi（健康） | 數 ms 到 20 ms | ≈ 0.31–0.32 s | ≈ 0.91–0.92 s |
  | 壅塞 Wi-Fi／2.4 GHz | P90 可達 100 ms 以上（WiLy） | 尾端 ≈ 0.4–0.5 s | 尾端 ≈ 1.0–1.1 s |
  | 家用網際網路，同國 | idle 中位數 ≈ 13–53 ms，負載下 ≈ 66–110 ms | ≈ 0.35–0.4 s | ≈ 0.95–1.0 s；上行被占滿時 ≈ 1.0–1.2 s |
  | 跨國 | 100–200 ms 等級（未找到引用，見 Gaps） | — | 仍可能 < 1.3 s，但 partial 的即時感會明顯下降 |

- **結論（推論）**：LAN 與同國網際網路都能維持 < 1–2 s 的互動性。主要威脅是 TCP/WebSocket 在 Wi-Fi 掉包時的 head-of-line blocking 與 retransmission，會造成偶發的數百 ms 停頓。用戶端顯示要能容忍晚到的 partial，例如丟棄 sequence 已過期的 partial。
- **伺服器 GPU 比本機差時（推論）**：GPU 推論時間會直接加進延遲。Whisper 的 30 s 補零讓 encoder 成本與句長無關，所以每次 partial 都要付一次完整 encoder 成本。

### Gaps
- 沒有取得同一間房、乙太網路 vs Wi-Fi 5/6 的 LAN 內（client → 同網段主機）RTT 一手量測。Ookla 的數字是到網際網路測速伺服器，不是 LAN 內延遲。有線 LAN 0.3–1 ms 是經驗值，未引用。
- 沒有找到 Ookla／M-Lab 依 Wi-Fi 世代的 jitter 一手數據。
- 沒有找到 RTX 5070 或 4090 上 Whisper large-v3/turbo「單句（2–10 s）encoder＋decoder 時間」的官方量測。kikitori 自己的本機量測（0.3 s／0.9 s）是目前最可靠的基準。
- FCC 2024 MBA 報告各技術的 idle latency 精確毫秒值沒有取得。

## 3. partial 每 0.7 s 重送整句音訊：該如何改成增量上傳？whisper_streaming／WhisperLive 怎麼在伺服器緩衝？

### Takeaway
改成 append-only 串流：用戶端只送新增的音訊 frame，帶 sample offset 或 sequence number。伺服器為每個 session 維護「目前句子」的音訊 buffer，partial／final 都在伺服器端以 buffer 內容觸發。現有的開源專案都是這樣做：用戶端只管推音訊，伺服器自己累積、處理、修剪 buffer。

### Cited Findings
- whisper_streaming 的處理方式：
  - 新音訊以連續 chunk 處理。
  - 確認的文字（LocalAgreement：連續 2 次更新的共同前綴）送出後，buffer 在已確認的 segment 或 sentence 邊界修剪。預設策略是 `segment`，作者認為效果較好。門檻由 `--buffer_trimming_sec` 控制。
  - `--min-chunk-size` 是處理前至少等待的秒數。若上次處理比它快，伺服器就等；否則處理到目前為止收到的全部音訊。範例使用 `--min-chunk-size 1`。
  - 用戶端只是持續推送 raw PCM（`arecord | nc`），沒有重送。
  
  — [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)
- SimulStreaming 的 AlignAtt policy 用 encoder–decoder attention 判斷目前解碼到 source 的哪一段。attention 接近 buffer 尾端就暫停解碼，等下一個 chunk 再繼續。預設模式下，若上次更新比較慢，chunk 會自動變大 — [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)
- WhisperLive 用戶端可用任意節奏與 chunk 大小推送 PCM，伺服器回傳帶 start/end 與 `completed` 旗標的 segment。README 沒有說明伺服器 buffer 的修剪細節 — [collabora/WhisperLive](https://github.com/collabora/WhisperLive)

### Inferences
- **協定草案（推論，沿用上述專案的模式）**
  - 上行：
    - `audio{seq, start_sample, pcm/opus}`，20–32 ms 一包，與 Silero 的 32 ms window 對齊。
    - `segment_start{id, preroll_samples}`
    - `segment_end{id, end_sample}`
  - 下行：
    - `partial{id, rev, text}`
    - `final{id, text}`
    - `translation{id, rev, delta}`
  - 伺服器以 `start_sample` 偵測缺漏或重複，idempotent append。
- **緩衝與上限（推論）**
  - 句子 buffer 在 `segment_end` 後清空，或只保留前句的 tail 當脈絡。
  - kikitori 的句子已由 VAD 切短，不需要 whisper_streaming 那種長 buffer 的修剪邏輯。
  - 但應設上限：句子超過約 20–25 s 就強制切句，因為 Whisper 視窗是 30 s。
- **背壓（推論）**：partial 的觸發從「用戶端每 0.7 s 送整句」改成「伺服器每 0.7 s 對 buffer 跑一次」。若上一次 partial 還沒跑完，就跳過這一輪，類似 whisper_streaming 的 computationally-aware 行為，避免排隊累積延遲。
- **頻寬（推論）**：改成增量上傳後，上行固定是 32 KB/s（PCM）或約 2.5 KB/s（Opus），與句長無關。若照目前的做法每 0.7 s 重送整句，一句 10 s 的話累積上傳約 14 次、平均 5 s 音訊，約是增量的 7 倍。

### Gaps
- whisper_streaming 的 `--buffer_trimming_sec` 預設值與 WhisperLive 伺服器的內部 buffer 實作，在本次讀到的 README 範圍內沒有記載，需要看原始碼。

## 4. 什麼時候遠端運算不划算？App 是否該自動量測並推薦本機或遠端？

### Takeaway
以下情況遠端不划算：
- 用戶端已有足以跑 kikitori 本機管線的 GPU，例如 RTX 5070 已量到 0.3 s／0.9 s，遠端最多只能持平，再加上 RTT 與抖動。
- 伺服器 GPU 比本機慢或被共用。
- 路徑是壅塞的 Wi-Fi、行動網路或跨國，RTT/jitter 大。

頻寬幾乎從來不是瓶頸，32 KB/s 以下。建議 App 實測後推薦：用 RTT 探測加伺服器回報的處理時間，和本機的量測比較。

### Cited Findings
- 有 survey 指出，通道品質、網路與 edge server 負載的不穩定會讓 offload 的延遲顯著惡化 — [ACM CSUR split computing survey](https://dl.acm.org/doi/10.1145/3527155)
- 混合式系統以部分輸出決定是否 offload，比全部 offload 的延遲約低 2 倍 — [arXiv 2311.17065](https://arxiv.org/abs/2311.17065v1)
- Google 以可靠性、隱私與延遲作為 on-device ASR 的動機 — [arXiv 2003.12710](https://arxiv.org/pdf/2003.12710)
- 壅塞 Wi-Fi 的 P90 hop 延遲可達 100 ms 以上 — [WiLy INFOCOM'16](https://1989chenguo.github.io/Publications/WiLy-INFOCOM16.pdf)；上行負載下的家用網路延遲中位數約 66–108 ms — [Bajpai et al.](https://vaibhavbajpai.com/documents/papers/proceedings/fcc-networking-2022.pdf)
- SimulStreaming 表示 CPU「would be too slow for real-time」，建議 GPU ≥ 10 GB VRAM — [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)

### Inferences
- **推薦邏輯（推論）**
  - 連線時跑約 20 次 ping/pong（WebSocket 應用層），取 RTT 的 median 與 P95。
  - 伺服器在每個 partial/final 回報 `server_ms`（GPU 推論時間）與 `queue_ms`。
  - 預估遠端 final 延遲 = `server_ms` + `queue_ms` + RTT，與本機 GPU-reminder／推薦模組的量測值比較。
  - 遠端 P95 比本機好超過一個門檻（例如 200 ms），或本機沒有可用 GPU 時，就推薦遠端。
  - 執行中持續監測，劣化時提示切回本機。
- **最適合遠端的場景（推論）**
  - 用戶端是筆電或內顯，沒有 CUDA GPU，只能用 CPU 跑小模型。
  - 用戶端要同時跑遊戲或直播編碼，GPU 已滿載。
  - 想在用戶端用不了的大模型（large-v3 加大型 LLM）。
- **不划算的場景（推論）**
  - 用戶端有 ≥ 8 GB VRAM 的 RTX 30/40/50 系列。
  - 遠端在跨國或行動網路上。
  - 伺服器同時服務多人且已排隊。
- **混合模式（推論）**：ASR 留本機、翻譯送遠端 Ollama，也就是方案 (d)。這個選項應列為「本機 GPU 不足以同時跑 Whisper 與 LLM」時的折衷。

### Gaps
- 沒有找到業界產品自動量測並切換本機／遠端 ASR 的公開設計文件。上述門檻是推論。

## 5. 伺服器 GPU 共用：兩個 Whisper 模型加 Ollama LLM，服務一個或多個用戶端

### Takeaway
一張 GPU 同時放 fast Whisper、accurate Whisper 和一個 Ollama LLM 可行，VRAM 是主要限制：
- Whisper large-v2 fp16 約 4.5 GB，int8 約 2.9 GB。
- LLM 依大小另計，且 `NUM_PARALLEL` × context 會放大 KV cache。

服務多個用戶端時，不要每個連線各載一份模型，應該共用一份模型。CTranslate2 同 device 的多個 worker 會共享權重；也可以像 WhisperLive 那樣跨 session batch。Ollama 預設一次只處理 1 個請求，要調高 `OLLAMA_NUM_PARALLEL`。

### Cited Findings
- **CTranslate2**
  - `inter_threads` 決定平行 worker 數，`device_index` 傳 list 時每個 worker 用不同 GPU。
  - 「When the workers are running on the same device, the model weights are shared to save on memory.」
  - 要平行就必須「Multiple batches should be submitted concurrently」。
  - 同 GPU 上每個 worker 各用自己的 CUDA stream，但「depending on the workload and GPU specifications this may not improve the global throughput」。
  - 計算方法會釋放 Python GIL。
  
  — [CTranslate2 Parallel docs](https://opennmt.net/CTranslate2/parallel.html)
- **faster-whisper**（MIT 授權）
  - large-v2 VRAM：fp16 4525 MB，int8 2926 MB；batch=8 時 fp16 6090 MB，int8 4500 MB。
  - `BatchedInferencePipeline` 可直接替換 `transcribe`。
  - 內建 Silero VAD 過濾，預設只移除超過 2 s 的靜音。
  
  — [SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)
- **WhisperLive**
  - `--max_clients` 預設 4，`--max_connection_time` 預設 600 s。
  - 預設每個連線載入新模型（耗 VRAM 與載入時間）。用 `-fw`／`-trt` 時是單一模型共用。
  - `--batch_inference` 會把多個 session 合成單次 GPU 呼叫，參數為 `--batch_max_size` 與 `--batch_window_ms`。
  
  — [collabora/WhisperLive](https://github.com/collabora/WhisperLive)
- **Ollama**
  - `OLLAMA_NUM_PARALLEL` 預設 1，所需記憶體隨 NUM_PARALLEL × `OLLAMA_CONTEXT_LENGTH` 成長（例如 2K context × 4 parallel = 8K）。
  - `OLLAMA_MAX_LOADED_MODELS` 預設每 GPU 3 個。
  - `OLLAMA_MAX_QUEUE` 預設 512，超過回 503。
  - 新模型放不下時，請求會排隊，並卸載閒置模型。
  - `keep_alive` 預設 5 分鐘。
  - 預設綁 127.0.0.1:11434，要對外須設 `OLLAMA_HOST=0.0.0.0:11434`。FAQ 沒有附網路暴露的安全警告。
  
  — [Ollama FAQ](https://docs.ollama.com/faq)
- SimulStreaming 建議 ≥ 10 GB VRAM，而且依賴 torch — [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)

### Inferences
- **VRAM 估算（推論）**
  - fast Whisper（small 或 turbo int8，約 1–2 GB）＋accurate Whisper（large-v3 int8，約 3 GB；fp16 約 4.5 GB）＋7–8B LLM Q4（約 5–6 GB，加 KV cache）≈ 10–13 GB。
  - 12 GB 的 RTX 5070 對單一用戶端會很緊，16–24 GB 比較穩。
  - large-v3 與 turbo 的精確 VRAM 未查到官方表，以 large-v2 的數字類推。
- **多用戶端的瓶頸（推論）**：每次 partial 都要付一次固定的 30 s encoder 成本，所以 N 個用戶端各自每 0.7 s 觸發 partial，GPU 工作量約為 N 倍。
  - 對策 1：fast 模型用一個 CTranslate2 instance，把各 session 的 partial 在短時間窗內（例如 50–100 ms）合成 batch。
  - 對策 2：final 優先於 partial（priority queue）。
  - 對策 3：忙碌時拉長 partial 間隔。
- **不要讓 Ollama 搶顯存（推論）**：設 `OLLAMA_MAX_LOADED_MODELS=1` 並固定 `keep_alive=-1`，避免 Ollama 為了載入另一個模型而卸載。LLM 與 Whisper 同時在跑時，GPU 時間會互相干擾，final 延遲可能上升；需實測。
- **安全（推論）**：Ollama 預設沒有認證，對外開放 0.0.0.0 有風險。建議只綁 LAN 介面或經由 kikitori 伺服器代理，用戶端不直接連 Ollama。

### Gaps
- 沒有找到「同一張 GPU 同時跑 CTranslate2 Whisper 與 Ollama（llama.cpp）」互相干擾的公開量測。
- CTranslate2 文件沒有直接說明同一張 GPU 上多個 replica 或 worker 是否能提升 Whisper 吞吐，只說「may not improve」。
- 本次未驗證 WhisperLive 的 Silero VAD 是否為 ONNX 版本（是否完全無 torch）。faster-whisper README 也沒有明說是否不需要 PyTorch。
