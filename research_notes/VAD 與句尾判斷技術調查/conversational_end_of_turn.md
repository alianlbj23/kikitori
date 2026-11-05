# 對話句尾 / 換手（end-of-turn）偵測方法調查：Scenario B（面對面對話、即時口譯）

> 調查日期：2026-11-05。標記規則：
> - 「論文或官方宣稱」= 作者論文或官方 repo / model card / 官方部落格自己報的數字
> - 「有獨立評測」= 非該模型作者跑的評測（註明是誰；若評測者是競爭廠商也會註明）
> - arXiv 預印本標「尚未經同行審查」；不在使用者清單內的場合（IWSDS、LREC-COLING、IROS）會另外標出
> - 廠商官方部落格（daily.co、livekit.com）視為「官方宣稱」來源，不是獨立來源

## Q1. 各方法比較（原理、串流延遲、是否免 PyTorch、語言、噪音、授權、是否符合限制）

### Takeaway
沒有任何一個現成方法能同時滿足五項限制（低延遲串流、CPU 即時、免 PyTorch、日英中、MIT 相容授權）且已在面對面對話上驗證過。最接近的是 **Smart Turn v3.x**（音訊、ONNX、BSD-2、23 語含日英中）；但它的設計對象是「人對 AI agent」的單一使用者換手，中文準確率是它的弱項，而 LiveKit 自家 benchmark 中它的提早切斷率偏高。**LiveKit** 模型授權只能搭配 LiveKit Agents 使用；**TEN** 是 7B、要 PyTorch，也不支援日文；**VAP/MaAI** 語言與即時性都合適，但需要 PyTorch，而且權重授權不明；**Parakeet EOU** 只支援英文。

### Cited Findings

#### 1. Pipecat Smart Turn v3.x（v3.0 / v3.1 / v3.2）
- **原理（純音訊）**：Whisper Tiny encoder 後接 shallow linear classifier，共 8M 參數。直接分析原始波形，不看逐字稿；輸出這段音訊「說完了」的機率，閾值 0.5 — [HF model card](https://huggingface.co/pipecat-ai/smart-turn-v3)；[inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py)
- **輸入／context window**：16 kHz mono，最長 8 秒。超過 8 秒時從開頭截掉；不足 8 秒時在開頭補零。建議餵入「目前這一輪」的完整音訊，不需要前幾輪；也不適合太短的片段。推論期間若又有新語音進來，要對整輪重新推論 — [GitHub README](https://github.com/pipecat-ai/smart-turn)
- **串流行為**：不是逐幀串流，而是「VAD 偵測到靜音後才跑一次」，輸入是整段 turn — [GitHub README](https://github.com/pipecat-ai/smart-turn)。Pipecat 的 `SmartTurnParams` 預設 `stop_secs=3`（靜音達 3 秒就強制判定結束，也就是 fallback timeout）、`pre_speech_ms=500`、`max_duration_secs=8` — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- **CPU 推論時間（論文或官方宣稱）**：
  - v3.0（含前處理）：AWS c7a.2xlarge 12.6 ms、c8g.2xlarge 15.2 ms、t3.2xlarge 33.8 ms、c8g.medium 59.8 ms、t3.medium 94.8 ms — [Daily v3 blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
  - v3.1（8MB int8）：c7a.2xlarge 9 ms、c8g.2xlarge 20 ms、c7a.medium 37 ms、c8g.medium 57 ms，前處理另計 7–9 ms — [Daily v3.1 blog](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/)
  - README 寫「某些 CPU 上 10ms，多數雲端機器 <100ms」 — [GitHub README](https://github.com/pipecat-ai/smart-turn)
- **ONNX／是否免 PyTorch**：官方提供 ONNX，int8 版 8MB（CPU 用）、fp32 版 32MB（GPU 用） — [HF model card](https://huggingface.co/pipecat-ai/smart-turn-v3)。官方 `inference.py` 只用到 `onnxruntime` 和 `transformers.WhisperFeatureExtractor`（`return_tensors="np"`），沒有 import torch — [inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py)
- **語言**：23 種，含 Chinese、Japanese、English (UK/US) — [GitHub README](https://github.com/pipecat-ai/smart-turn)
- **各語言準確率（論文或官方宣稱，v3.0 test set）**：Japanese 96.76%（FP 2.04%／FN 1.20%，n=834）、English 94.31%（FP 2.64%／FN 3.06%，n=2,846）、Chinese 88.57%（FP 4.76%／FN 6.67%，n=945）。Chinese 在 23 種語言中排倒數第 4 — [Daily v3 blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- v3.1 English 88.3% → 94.7%（8MB）／95.6%（32MB），Spanish 86.7% → 90.1%／91.0%；其他 21 種語言「與 v3.0 相近」，沒有公布日、中的新數字（論文或官方宣稱） — [Daily v3.1 blog](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/)
- **噪音（論文或官方宣稱，無量化數字）**：v3.2 在 train/test 加入「realistic cafe/office noise」，並宣稱短語句（"yes"、"okay"）誤判減少 40%。部落格沒有公布 SNR、噪音來源，也沒有有／無噪音的對照數字 — [Daily v3.2 blog](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/)
- **有獨立評測（但評測者是競爭廠商 LiveKit）**：LiveKit eot-bench 的英文結果，SmartTurn v3.2 在 300 ms 延遲預算下 false cutoff 35.2%、600 ms 下 14.8%；要壓到 5% cutoff 需要 1051 ms。VAD baseline 分別是 55.6%／21.7%／1600 ms — [livekit/eot-bench](https://github.com/livekit/eot-bench)
- **授權**：repo 程式碼 BSD-2-Clause，README 稱「a truly open model (BSD 2-clause license)」 — [GitHub README](https://github.com/pipecat-ai/smart-turn)。HF 權重標籤也是 bsd-2-clause — [HF model card](https://huggingface.co/pipecat-ai/smart-turn-v3)
- **限制符合度**：低延遲串流 = yes（事件觸發式，~10–60 ms）；CPU 即時 = yes；免 PyTorch = yes（ONNX）；日英中 = yes（中文準確率最低）；MIT 相容 = yes（BSD-2 需保留版權聲明）

#### 2. LiveKit turn detector — 文字版 MultilingualModel（open weights，已 deprecated）
- **原理（純文字）**：Qwen2.5-0.5B-Instruct 蒸餾自 Qwen2.5-7B-Instruct。輸入是 STT 逐字稿，最多 6 輪、截到 128 tokens；輸出下一個 token 是 `<|im_end|>` 的機率，再用各語言各自的閾值判斷。看不到停頓、語調等 prosody — [HF model card](https://huggingface.co/livekit/turn-detector)
- **延遲**：每輪約 50–160 ms，磁碟 396 MB，RAM <500 MB（論文或官方宣稱） — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- **ONNX**：有，INT8 `model_q8.onnx`，跑在 CPUExecutionProvider — [HF model card](https://huggingface.co/livekit/turn-detector)
- **語言**：14 種，含 Japanese、Chinese、English — [HF model card](https://huggingface.co/livekit/turn-detector)
- **準確率（論文或官方宣稱）**：TPR 各語言都是 99.3–99.4%；TNR Japanese 88.8%、English 87.0%、Chinese 86.6% — [HF model card](https://huggingface.co/livekit/turn-detector)
- **狀態**：docs 標示「slated for removal in version 2.0」 — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- **授權（關鍵）**：LiveKit Model License 原文為「can only use them together with the LiveKit Agents framework」「not to use any LiveKit Models on a standalone basis or with any frameworks other than LiveKit Agents」，另外也禁止用模型輸出去改進其他模型 — [LICENSE](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE)
- **限制符合度**：低延遲串流 = 部分（要先等串流 ASR 出字）；CPU 即時 = yes；免 PyTorch = yes；日英中 = yes；MIT 相容 = **no**（在非 LiveKit Agents 專案中不能使用）

#### 3. LiveKit Turn Detector v1 / v1-mini（音訊版，2026）
- **原理（音訊，v1 被描述為保留 LLM backbone 再加上 audio encoder）**：直接編碼使用者音訊，不需要 STT。需要搭配 VAD，且 `min_silence_duration` ≥ 0.25 s；使用音訊模型時預設 `min_delay=0.3 s`、`max_delay=2.5 s`；模型約 1 秒內沒回應就直接 commit turn — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- v1 只能透過 LiveKit Inference（雲端）使用；v1-mini 在本機 CPU 執行，每份約 140–250 MB 記憶體，docs 沒給延遲數字 — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- **語言**：14 種，含 Japanese、Chinese、English — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- **準確率（論文或官方宣稱，自家 benchmark）**：英文 false cutoff @300 ms：v1 9.9%、v1-mini 27.8%；latency @5% cutoff：v1 543 ms、v1-mini 1070 ms — [livekit/eot-bench](https://github.com/livekit/eot-bench)
- **授權矛盾**：docs 一方面說 v1-mini「free to use in any context」，另一方面又說「LiveKit Model License used for the text turn detector and the `v1-mini` model」；而該授權限定只能搭配 LiveKit Agents — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)；[LICENSE](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE)
- **限制符合度**：v1 = 需要雲端，不符合本機 CPU 的要求；v1-mini 的低延遲 = 不明、CPU = yes、免 PyTorch = 不明、日英中 = yes、MIT 相容 = **unclear（傾向 no）**

#### 4. TEN Turn Detection（TEN-framework）
- **原理（純文字）**：以 Qwen2.5-7B 做語意分類，輸出三類：finished / unfinished / wait — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- **語言**：只有 English、Chinese；沒有 Japanese — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- **準確率（論文或官方宣稱）**：English finished 90.64%／unfinished 98.44%／wait 91%；Chinese finished 98.90%／unfinished 92.74%／wait 92% — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- **執行環境**：需要 `transformers>=4.45.0`、`torch>=2.0.0`；沒提供 ONNX，也沒給硬體需求或延遲 — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- **授權**：「Apache License 2.0 with additional restrictions」，細節要看 LICENSE（本次未取得全文） — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- **有獨立評測（尚未經同行審查）**：FireRedChat 宣稱在相近參數量下 EoT 表現優於 LiveKit，並追平 TEN 的 7B 模型 — [arXiv 2509.06502](https://arxiv.org/pdf/2509.06502)
- **限制符合度**：低延遲 = unclear；CPU 即時 = 7B 模型很可能 no；免 PyTorch = **no**；日英中 = **no**（沒有日文）；MIT 相容 = unclear

#### 5. Voice Activity Projection（VAP；Ekstedt & Skantze → Inoue et al. 京都大學）與 MaAI
- **原理（音訊，雙聲道對話）**：cross-attention Transformer 同時讀入兩位說話者的音訊，預測雙方未來的 voice activity，再從中推出換手 — [arXiv 2403.06487](https://arxiv.org/abs/2403.06487)
- **多語言（論文或官方宣稱）**：只用單一語言訓練的模型跨語言表現差；用英文、中文、日文一起訓練的 multilingual 模型，在三種語言上都與各自的單語模型相當，而且模型會學到辨識輸入語言。論文也比較了 CPC 與 MMS（wav2vec 2.0）兩種 encoder。發表於 LREC-COLING 2024（**不在使用者清單內，需標註**） — [arXiv 2403.06487](https://arxiv.org/abs/2403.06487)
- **即時性（論文或官方宣稱）**：「can operate in real-time with CPU settings, with minimal performance degradation」，論文也分析了 context 長度的影響（abstract 沒有數字）。發表於 IWSDS 2024（**workshop，不在使用者清單內**） — [arXiv 2401.04868](https://arxiv.org/abs/2401.04868)
- **噪音（論文或官方宣稱）**：noise-robust VAP 在購物中心機器人的實地實驗中「significantly reduced response latency」（abstract 沒有數字）。發表於 IROS 2025（**機器人會議，不在使用者清單內**） — [arXiv 2503.06241](https://arxiv.org/abs/2503.06241)
- **MaAI（官方即時實作）**：支援 English、Chinese、Japanese；可用雙聲道（user + system）或單聲道（只有 user）；`frame_rate` 可設定（範例用 10 / 12.5 Hz），也就是逐幀串流輸出；README 宣稱純 CPU 也能有效率地執行，但沒有 benchmark。需要 **PyTorch**，只有 Mimi encoder 版本用 onnxruntime。程式碼 MIT；trained models 依各模型自己的 README／HF 頁面；Mimi 為 CC BY 4.0；CPC 依原 CPC_audio 授權 — [maai-kyoto/maai](https://github.com/maai-kyoto/maai)
- **有獨立評測（評測者是 LiveKit，且是「silent agent」設定）**：VAP 英文 false cutoff @300 ms 46.9%、@600 ms 14.6%、latency @5% 1131 ms — [livekit/eot-bench](https://github.com/livekit/eot-bench)
- **限制符合度**：低延遲串流 = yes（逐幀）；CPU 即時 = yes（論文宣稱）；免 PyTorch = **no**（Mimi 版本部分使用 ONNX）；日英中 = yes；MIT 相容 = 程式碼 yes、權重 **unclear**

#### 6. NVIDIA Parakeet-Realtime-EOU-120m-v1（ASR 內建 EOU token）
- **原理（音訊 → ASR，輸出 `<EOU>` token）**：cache-aware streaming FastConformer（17 層）+ RNNT，共 120M 參數；ASR 延遲 80–160 ms — [HF model card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)
- **EOU 延遲（論文或官方宣稱）**：測試資料是 TTS 合成的 DialogStudio 音訊、後面補 3 秒靜音；p50 160 ms、p90 280 ms、p95 320 ms。官方沒公布 EOU 準確率或 early cutoff rate，並註明真實環境會因聲學環境與口音而不同 — [HF model card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)
- **語言**：只有英文，也不輸出標點 — [HF model card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)
- **執行環境**：官方使用 NeMo ≥2.5.3（需要 PyTorch），沒有官方 ONNX；第三方有 ONNX INT8 匯出 — [HF model card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)；[soniqo ONNX（第三方）](https://huggingface.co/soniqo/Parakeet-EOU-120M-ONNX-INT8)
- **授權**：NVIDIA Open Model License，可商用 — [HF model card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1)
- **限制符合度**：低延遲 = yes；CPU 即時 = unclear（官方只在 GPU 上測過）；免 PyTorch = no（官方）／第三方 ONNX；日英中 = **no**；MIT 相容 = unclear（非 OSI 授權，要另外確認條款）

#### 7. sherpa-onnx endpoint rules（串流 ASR + 規則）
- **原理（規則式，依據 ASR decoder 的 blank / trailing silence）**：預設三條規則：rule1 = 靜音 ≥2.4 s（就算還沒解出任何字也觸發）；rule2 = 已解出非 blank token 且靜音 ≥1.2 s；rule3 = 語句長度 ≥20 s。「non-blank token is considered as non-silence」 — [endpoint.h](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/endpoint.h)
- 本質上仍是固定靜音閾值，只是把「有沒有出字」當作語音判斷依據；沒有語意或 prosody 判斷 — [endpoint.h](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/endpoint.h)
- **限制符合度**：串流 = yes；CPU = yes；免 PyTorch = yes（ONNX）；日英中 = 取決於所選的 ASR 模型（本次未查）；授權 = 本次未驗證（見 Gaps）

#### 8. Kyutai STT（delayed streams modeling，內建 semantic VAD）
- **原理（音訊 → ASR，同時輸出「使用者是否說完」的機率）**：pause 判斷的延遲會依內容與語調調整。stt-1b-en_fr 延遲 500 ms，只支援英文、法文；semantic VAD 只在 Rust server 上提供。程式碼：Python 部分 MIT、Rust 部分 Apache；權重 CC-BY 4.0 — [Kyutai STT](https://kyutai.org/stt)；[GitHub](https://github.com/kyutai-labs/delayed-streams-modeling)；方法論文（尚未經同行審查）[arXiv 2509.08753](https://arxiv.org/html/2509.08753v2)
- **限制符合度**：串流 = yes；CPU = unclear（1B 參數）；免 PyTorch = yes（Rust server）；日英中 = **no**；MIT 相容 = yes（CC-BY 需要署名）

#### 9. 學術方法（沒有可直接採用的開源實作）
- **Google unified E2E ASR + endpointing**（Bijwadia, Chang, Li, Sainath et al.；SLT 2022，DOI 屬 SLT 2023 proceedings）：與分開的單任務模型相比，median endpoint latency 減少 120 ms（30.8%）、p90 減少 170 ms（23.0%），WER 沒有退步；continuous recognition WER 相對改善 10.6%。測試集為 voice search（論文或官方宣稱） — [arXiv 2211.00786](https://arxiv.org/abs/2211.00786)
- **Semantic VAD**（Shi et al., USTC, Interspeech 2023）：在 VAD 中加入 frame-level 標點預測與 ASR semantic loss，把 artificial endpoint 當作分類目標。在內部資料集上平均延遲降低 53.3%，CER 沒有明顯變差（論文或官方宣稱，內部資料、未見重現） — [ISCA archive](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.html)
- **Li et al. RNN-T early/late penalty endpointer**（Google，2020，早於 2023 時間範圍）：p90 latency 減少 130 ms，加上 rescoring 後 median −40 ms、p90 −160 ms（論文或官方宣稱） — [arXiv 2004.11544](https://arxiv.org/pdf/2004.11544.pdf)
- **"Less can be More"**（Sharon, Hacioglu, Stolcke et al.，2026，**尚未經同行審查**）：在同樣條件下比較 acoustic、prosodic、semantic 三種訊號，acoustic-prosodic 組合最好（utterance F1 0.93、false alarm 7.8%、median latency 400 ms）；再加入文字反而讓提早觸發變多 — [arXiv 2609.11066](https://arxiv.org/abs/2609.11066)
- **Easy-Turn**（arXiv 2509.23938，**尚未經同行審查**）：融合聲學與語言模態，自稱優於 TEN 與 Smart Turn v2（作者自評） — [arXiv 2509.23938](https://arxiv.org/pdf/2509.23938)
- **BanglaTurn**（arXiv 2609.29371，**尚未經同行審查**）：在孟加拉語上，Smart Turn v3 準確率 69.28%，作者的模型 84.33%（就 Smart Turn 而言屬於有獨立評測） — [arXiv 2609.29371](https://arxiv.org/pdf/2609.29371)

### Inferences
- 在 MIT 專案中能直接使用、而且免 PyTorch 又支援日英中的現成選項，實際上只有 **Smart Turn v3.x**。其他方法分別卡在授權（LiveKit）、語言（TEN 沒有日文；Parakeet、Kyutai 沒有日文與中文）或 PyTorch 依賴（VAP/MaAI、TEN、Parakeet 官方版）。
- VAP 是唯一以人與人對話資料（雙人）為設計對象、而且明確研究過日英中的方法，概念上最符合面對面對話。若要採用，需要自行把它匯出成 ONNX，並逐一確認權重授權。
- 現成的文字模型都是「人對 agent」設定，6 輪 context 也是以 user/assistant 交替的格式來設計；用在兩個人面對面交談、而系統不回話的口譯情境，輸入格式並不自然。

### Gaps
- TEN 的 LICENSE 全文（「additional restrictions」到底限制什麼）沒有取得。
- MaAI 各個 VAP 權重在 HF 上的授權沒有逐一查。
- Ekstedt 原始 VAP repo（ErikEkstedt/VoiceActivityProjection）的授權與原始論文（Interspeech 2022）沒有查證。
- sherpa-onnx 本身的授權、各串流模型的語言覆蓋沒有查證。
- Kyutai Moshi 等 full-duplex 模型沒有取得一手資料。依目前看到的 Kyutai STT 資訊，Kyutai 系列不支援日文與中文，對本案的參考價值可能有限。
- 找不到任何針對「面對面、多人、單支麥克風」情境的 end-of-turn 獨立評測。

## Q2. Smart Turn 是否適合 Scenario B（面對面對話）？訓練資料、各語言準確率、與 VAD 的搭配方式

### Takeaway
Smart Turn 的設計對象是「單一使用者對 voice agent」的換手判斷：在 VAD 偵測到靜音後，對整段 turn 推論一次，並以 `stop_secs` 作為 fallback timeout。訓練資料早期以 TTS 合成為主，v3.1 之後加入英文與西班牙文的人聲資料，v3.2 加入咖啡廳／辦公室噪音。官方數字中，日文表現好（96.76%），中文偏弱（88.57%），而且這些數字都不是在面對面對話上測得。你們在 Scenario A 只有 1/12 通過，Scenario B 也沒有任何公開證據支持；必須自己測。

### Cited Findings
- 訓練資料：datasets `smart-turn-data-v3.2-train/test`，音訊由 Liva AI、Midcentury、MundoAI 提供；人聲資料透過「turn training games」與直接投稿收集 — [GitHub README](https://github.com/pipecat-ai/smart-turn)
- v3.1 部落格說早期版本「relying heavily on TTS-generated data」；v3.1 新增的人聲資料只有英文與西班牙文，沒有公布時數或合成／人聲比例 — [Daily v3.1 blog](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/)
- v3.1 的 train/test 規模為 270k／31.5k 筆 — [HF model card](https://huggingface.co/pipecat-ai/smart-turn-v3)
- v2 的各語言準確率是在「unseen synthetic data」（TTS 生成）上測得：Japanese 95.38%、Chinese (Mandarin) 87.20%（論文或官方宣稱；數字來自搜尋摘要，未全文核對） — [Daily v2 blog](https://www.daily.co/blog/smart-turn-v2-faster-inference-and-13-new-languages-for-voice-ai/)
- v3.0 各語言準確率：Japanese 96.76%、English 94.31%、Chinese 88.57%（論文或官方宣稱） — [Daily v3 blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- v3.2 加入 cafe/office noise，短語句誤判減少 40%（論文或官方宣稱，無噪音條件下的分項數字） — [Daily v3.2 blog](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/)
- 與 VAD 的搭配：「designed to pair with a lightweight VAD such as Silero」，只在 VAD 偵測到靜音後才對整段 turn 推論；不適合非常短的片段 — [GitHub README](https://github.com/pipecat-ai/smart-turn)
- Pipecat 的實作：靜音累積到 `stop_secs`（預設 3 s，依樣本數計算）就強制結束 turn，這就是 fallback timeout；segment 最長 8 s — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- 有獨立評測（LiveKit eot-bench，英文、真實的人對 agent 資料，評測者為競爭廠商）：SmartTurn v3.2 false cutoff @300 ms 35.2%，VAD baseline 55.6%；latency @10% cutoff 739 ms，VAD baseline 1000 ms — [livekit/eot-bench](https://github.com/livekit/eot-bench)
- 有獨立評測（尚未經同行審查）：在孟加拉語上 Smart Turn v3 只有 69.28%，遠低於官方宣稱的數字。這顯示在官方測試集以外的資料上可能大幅退化 — [arXiv 2609.29371](https://arxiv.org/pdf/2609.29371)

### Inferences
- Scenario B 與 Smart Turn 訓練情境的差異：(1) 面對面時兩個人都會說話，換手可能由另一方插話觸發，但 Smart Turn 只看「目前這一段」音訊，不建模另一位說話者；(2) 筆電麥克風收音遠、有回響，與訓練資料的近講人聲不同；(3) 口譯不需要「回應」，真正要的是「切出可以翻譯的句子單位」，而 Smart Turn 判斷的是「整輪結束」，兩者並不相同。
- Smart Turn 的語言與資料主要來自西方廠商，中文準確率在官方表中是弱項；如果主要語言是中文，風險比較高。
- 你們在 Scenario A 的 1/12 結果，加上 BanglaTurn 與 eot-bench 這兩個非作者評測，都指向「官方 test set 準確率 ≠ 實際領域表現」。Scenario B 必須用自錄的面對面日英中語料驗證。

### Gaps
- 沒找到 v3.1／v3.2 的日文與中文新數字。官方說在 HF 的 benchmarks 資料夾（https://huggingface.co/pipecat-ai/smart-turn-v3/tree/main/benchmarks），本次沒有讀取。
- 沒找到 Smart Turn 在面對面或多人對話、遠距麥克風情境下的任何評測。
- v3.2 的噪音增強參數（SNR、噪音來源）官方沒有公開。

## Q3. Smart Turn 公開 API 以 time.monotonic() 緩衝、離線快於即時餵入時 pre-speech padding 算錯的問題

### Takeaway
已在 Pipecat 原始碼中確認：`BaseSmartTurn.append_audio` 用 `time.monotonic()` 為每個 chunk 加時間戳，pre-speech 起點與 buffer 修剪也都用 wall-clock 計算，而 `stop_secs` 與 `max_duration_secs` 則是用樣本數計算。所以離線以快於即時的速度餵入時，pre-speech 範圍會偏離預期的 500 ms。不過 smart-turn repo 本身的 `inference.py` 是無狀態函式，沒有這個問題。本次沒有找到官方 issue。

### Cited Findings
- `self._audio_buffer.append((time.monotonic(), audio_int16))`；第一個語音幀出現時記錄 `self._speech_start_time = time.monotonic()` — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- pre-speech：`effective_pre_speech_ms = pre_speech_ms + vad_start_secs*1000`，`start_time = _speech_start_time - effective_pre_speech_ms/1000`，再從 buffer 中找第一個 `t >= start_time` 的 chunk 當起點 — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- buffer 修剪：把 `t < time.monotonic() - (pre_speech + stop_secs + max_duration_secs)` 的 chunk 丟掉 — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- 靜音累積（`chunk_duration_ms = len(audio)/(sr/1000)`）與 `max_samples` 截斷則用樣本數計算，與 wall-clock 無關 — [base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py)
- 官方 `inference.py` 的 `predict_endpoint(audio_array)` 只做「保留最後 8 秒並在開頭補零 → Whisper 特徵 → ONNX」，沒有時間戳，也不保留狀態 — [inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py)

### Inferences
- 離線快速餵入時，所有 chunk 的 monotonic 時間戳都擠在很短的區間內，`start_time` 會早於幾乎所有 chunk，因此起點會落在 index 0。結果是 pre-speech 被包進「buffer 中所有先前的音訊」，而不是 500 ms；而 wall-clock 修剪幾乎不會生效。最後只剩 `max_duration_secs=8` 的樣本截斷在把關。這是依原始碼推論，沒有實際跑過驗證。
- 做離線評測時，建議繞過 `BaseSmartTurn`，直接呼叫 `inference.py` 的 `predict_endpoint`，自己用樣本索引切出「語音起點 − 500 ms ～ 目前位置」，這樣才能和即時行為對齊。

### Gaps
- 沒有搜尋 pipecat-ai/pipecat 或 pipecat-ai/smart-turn 的 GitHub issues，無法確認官方是否已知或修正。

## Q4. 文字型偵測器（LiveKit）需要串流 ASR：延遲／準確率取捨與語言覆蓋

### Takeaway
LiveKit 文字模型本身每輪約 50–160 ms，但它的前提是串流 ASR 已經給出 final 或穩定的逐字稿。總延遲 = ASR 出字延遲 + 推論時間 + VAD 最小靜音。官方 TPR 約 99.3%，TNR 86.6–88.8%（日英中），也就是約 11–13% 的「未說完」會被誤判為已說完。LiveKit 自己已轉向音訊模型，文字模型被標為 deprecated；對 MIT 專案來說，授權更是直接排除。

### Cited Findings
- 每輪延遲約 50–160 ms；需要 STT，即時模型則要另外加 STT plugin（論文或官方宣稱） — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- 「Cannot use prosody such as pauses or intonation」「Accuracy depends on the upstream STT output format」；接 audio-native realtime model 時需要額外的 STT，會增加成本與延遲 — [HF model card](https://huggingface.co/livekit/turn-detector)
- TNR：Japanese 88.8%、English 87.0%、Chinese 86.6%；TPR 99.3%（論文或官方宣稱） — [HF model card](https://huggingface.co/livekit/turn-detector)
- 端點預設：一般 `min_delay` 0.5 s／`max_delay` 3.0 s，用音訊模型時為 0.3／2.5 s — [LiveKit docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/)
- 授權只限 LiveKit Agents 使用 — [LICENSE](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE)
- TEN（文字型，7B）只支援英文與中文 — [GitHub](https://github.com/TEN-framework/ten-turn-detection)
- 尚未經同行審查：在同樣條件下的消融實驗中，加入文字反而讓提早觸發變多，而且沒有提升整體表現 — [arXiv 2609.11066](https://arxiv.org/abs/2609.11066)
- 參考：串流 ASR 本身的延遲，以 Kyutai stt-1b 為例是 500 ms 的固定延遲（論文或官方宣稱） — [Kyutai STT](https://kyutai.org/stt)

### Inferences
- 口譯本來就要跑串流 ASR，所以文字型判斷的「額外 ASR 成本」在本案不存在。但日文、中文的串流 ASR partial 結果不穩定，而且沒有空白斷詞，文字型判斷會直接受 ASR 錯誤影響。
- LiveKit 的 eot-bench 結果中，最新的音訊融合模型（v1）明顯勝過舊的文字模型路線（v1-mini 也優於 Smart Turn），代表業界趨勢是「音訊為主 + 語意 backbone」。但這是廠商自評。

### Gaps
- LiveKit 文字模型的 TNR 是在什麼資料上測的（合成或真實、是否為 agent 對話），model card 沒有說明。
- 沒找到「ASR 出字延遲 + 文字 EOU」端到端延遲的公開量測。

## Q5. 同行審查文獻：語意／韻律 endpointing 相對固定靜音閾值能降低多少延遲與誤切

### Takeaway
同行審查文獻顯示，ASR 整合或語意 endpointing 能在不犧牲 WER 的前提下，把 endpoint 延遲降低約 23–53%。不過這些實驗大多在 voice search 或內部資料集上進行，不是面對面對話。至於面對面多語對話，VAP 系列有定性結論（多語模型可行、CPU 可即時、實地測試降低回應延遲），但沒有與固定靜音閾值直接比較的數字。比較完整的固定閾值對照數據，來自廠商 benchmark 與尚未經同行審查的預印本。

### Cited Findings
- 同行審查，SLT（論文或官方宣稱）：unified E2E ASR + endpointing 相對分開的模型，median endpoint latency −120 ms（−30.8%）、p90 −170 ms（−23.0%），WER 沒有退步 — [arXiv 2211.00786](https://arxiv.org/abs/2211.00786)
- 同行審查，Interspeech 2023（論文或官方宣稱）：Semantic VAD 相對傳統 VAD（靠 trailing silence 達到上限才切）平均延遲 −53.3%，CER 沒有明顯變差；資料集為內部資料 — [ISCA archive](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.html)
- 同行審查，LREC-COLING 2024（不在清單內）：multilingual VAP 在英、中、日三種語言上與單語模型相當，且研究了 pitch 敏感度（abstract 沒有數字） — [arXiv 2403.06487](https://arxiv.org/abs/2403.06487)
- IROS 2025（不在清單內）：noise-robust VAP 在實地實驗中，相對雲端 ASR 系統顯著降低回應延遲（abstract 沒有數字） — [arXiv 2503.06241](https://arxiv.org/abs/2503.06241)
- 有獨立評測但屬廠商 benchmark（LiveKit，英文、真實的人對 agent 資料）：固定 VAD baseline 在 300 ms 時 false cutoff 55.6%，在 600 ms 時 21.7%；要壓到 5% cutoff 需要 1600 ms。最好的模型（LiveKit v1）分別是 9.9%、4.5%、543 ms — [livekit/eot-bench](https://github.com/livekit/eot-bench)
- 尚未經同行審查：acoustic + prosodic 組合達到 F1 0.93、false alarm 7.8%、median latency 400 ms，而且 prosody 是最能區分類別的特徵 — [arXiv 2609.11066](https://arxiv.org/abs/2609.11066)
- sherpa-onnx 的預設固定規則（有出字後 1.2 s、沒出字 2.4 s、20 s 強制切）可作為「固定靜音閾值」的實務基準 — [endpoint.h](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/endpoint.h)

### Inferences
- 文獻中「延遲降低 23–53%」的實驗都是 voice search 或指令式短句，面對面自由對話停頓較多，效益可能比較小。
- 依 eot-bench，固定靜音閾值若要把誤切壓到 ~5%，大約需要 1.6 s；好的模型能降到 ~0.5 s。對口譯而言，這大約是 1 秒的差距，影響明顯。但 Smart Turn v3.2 在同一個 benchmark 中要約 1 s，優勢沒有那麼大。

### Gaps
- 找不到 2023 年之後在 ICASSP、Interspeech、TASLP 等同行審查場合，針對「面對面、多語（日英中）、即時口譯」的 endpointing 與固定閾值對照研究。
- Google 2023 年之後的 endpointing 後續工作（例如 two-pass EP arbitrator）只找到二手摘要，沒有取得一手論文，因此沒有列入。
