# Streaming VAD 比較：Silero VAD vs. TEN VAD、FireRedVAD、WebRTC、NeMo MarbleNet、pyannote、FSMN-VAD、sherpa-onnx、Picovoice Cobra

> 調查日期：2026-11-05。標記說明：
> - **論文或官方宣稱**：模型作者自己的 README、model card 或論文。
> - **有獨立評測**：由模型作者以外的人測量。標記中寫明評測者；若評測者是競品團隊，也標註「非中立」。
> - **尚未經同行審查**：arXiv 技術報告。
>
> 情境：kikitori 要處理兩種音源，條件是 Python 3.12、只用 CPU、不依賴 PyTorch、MIT 專案。
> - 電腦播放音訊：動畫、VTuber、YouTube，含 BGM、歌唱、多人說話。
> - 麥克風面對面對話。

## 各方法逐項比較（Objective）

### Takeaway
- **Silero VAD v6** 最均衡：MIT 授權（code 與權重同一個 repo）、官方 ONNX、16 kHz 每次 512 samples（31.25 ms）、單執行緒每 chunk < 1 ms。
- **FireRedVAD** 是 Apache-2.0 授權的 DFSMN 模型，約 0.6M 參數，10 ms frame，有 streaming 版。官方只提供 PyTorch 推論，ONNX 版本都是社群移植。
- **TEN VAD** 很輕量、延遲低，但授權是「Apache 2.0 + 額外條件」（禁止與 Agora 競爭等），不是乾淨的寬鬆授權。
- **NeMo MarbleNet** 的權重採 NVIDIA Open Model License；**Picovoice Cobra** 需要 AccessKey 和線上驗證；**pyannote** 需要 PyTorch 且不支援 streaming；**FSMN-VAD** 的權重授權資訊互相矛盾。

### Cited Findings

#### Silero VAD（snakers4/silero-vad）
- **原理**：官方 README 沒有描述架構。Version history 列出現行 `silero_vad` 約 260K 參數、支援 streaming、ONNX opset 15/16 — [Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models)
- **取樣率與 chunk**：支援 8 kHz 和 16 kHz，每個 chunk 約「30+ ms」 — [README](https://github.com/snakers4/silero-vad)。Quality Metrics 以 31.25 ms 的片段評測（16 kHz 下等於 512 samples） — [Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- **速度**（論文或官方宣稱）：單一 CPU 執行緒處理一個 chunk 少於 1 ms；ONNX 在某些條件下可快 4–5 倍；JIT 模型約 2 MB — [README](https://github.com/snakers4/silero-vad)
- **速度**（有獨立評測：TEN 團隊，非中立）：在 Intel Xeon Gold 6348 上，Silero RTF 0.0127，TEN VAD 0.0086。Silero 函式庫 2.16 MB（JIT）/ 2.22 MB（ONNX） — [TEN VAD README](https://github.com/TEN-framework/ten-vad)
- **不依賴 PyTorch 的路徑**：README 把 `torch>=1.12.0` 列為 pip 套件依賴，ONNX 是 extra（`onnxruntime>=1.16.1`）。只用 ONNX Runtime 時，README 說要自己實作 I/O、改寫 wrapper。另有 C++、Rust、Go、Java、C#、OpenVINO、ONNX Runtime Web 範例 — [README](https://github.com/snakers4/silero-vad)
- **版本**：v6.2（2025-12-10）新增 ifless ONNX 和 tinygrad 16k 模型；v6.0 於 2025-08-25 發布；v5.0 於 2024-06-27 發布 — [Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models)
- **語言**：README 宣稱訓練資料涵蓋「over 6000 languages」 — [README](https://github.com/snakers4/silero-vad)
- **授權**：LICENSE 檔為 MIT，「Copyright (c) 2020-present Silero Team」。模型權重放在同一個 repo，README 沒有另外為權重宣告授權 — [LICENSE](https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE)
  - README badge 的 alt text 寫「License: CC BY-NC 4.0」，與 MIT 標示矛盾，看起來是舊殘留 — [README](https://github.com/snakers4/silero-vad)
  - → 可直接用於 MIT 專案。

#### TEN VAD（TEN-framework/ten-vad，Agora）
- **原理**：官方沒有公開網路架構。`pitch_est.cc` 改寫自 LPCNet 的 pitch 估計（BSD-2/BSD-3），可見特徵含 pitch 類資訊 — [README](https://github.com/TEN-framework/ten-vad)
- **取樣率與 hop**：只支援 16 kHz，其他取樣率需先重採樣。hop 可設定，最佳化的值是 160 / 256 samples（10 ms / 16 ms） — [README](https://github.com/TEN-framework/ten-vad); [HF card](https://huggingface.co/TEN-framework/ten-vad)
- **延遲**（論文或官方宣稱）：宣稱能快速偵測 speech→non-speech 轉換，並說 Silero「延遲數百毫秒」，還會漏掉 6.5–7.0 s 間的短停頓。官方沒有給延遲的數值 — [README](https://github.com/TEN-framework/ten-vad)
- **RTF**（論文或官方宣稱）：AMD Ryzen 9 5900X 0.0150；Intel i7-10710U（Windows）0.0150；Xeon Gold 6348 0.0086；Apple M1 0.0160；Web（M1）0.010。函式庫大小 Linux 306 KB、Windows x64 508 KB — [README](https://github.com/TEN-framework/ten-vad)
- **Runtime**：C、Python、ONNX（需 onnxruntime ≥ 1.17.1）、JS/WASM、Java（JNI）、Go；平台含 Linux、Windows、macOS、Android、iOS。不需要 PyTorch — [README](https://github.com/TEN-framework/ten-vad)
  - Python binding 標示「optimized for Linux x64」；Python + ONNX 只列 Linux/macOS。Windows 上 Python 能否使用要另外確認。
- **語言**：沒有列出支援的語言，只有一則日本開發者用於句尾偵測的推薦語 — [README](https://github.com/TEN-framework/ten-vad)
- **授權**：見下方 TEN VAD 與 FireRedVAD 一節。sherpa-onnx 文件稱之為「modified version of the Apache License 2.0」 — [sherpa-onnx VAD docs](https://k2-fsa.github.io/sherpa/onnx/vad/index.html)

#### FireRedVAD（FireRedTeam / 小紅書 Super Intelligence Team）
- **原理**（論文或官方宣稱，尚未經同行審查）：8 個 DFSMN block 加一層 FFN，hidden 256、projection 128。
  - look-back order 20。非 streaming 版 look-ahead 20；streaming 版 look-ahead 0，完全 causal。
  - 特徵與 FireRedASR2 相同：25 ms 窗、10 ms frame shift；AED 段落明寫 80 維 log-Mel FBank + CMVN。
  - 每個模型約 0.6M 參數（float32 約 2.2 MB）。streaming 版用每層 cache 增量輸出。
  - 後處理：moving-average smoothing、threshold、限制最短語音和靜音長度的有限狀態機。
  - 來源：[arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **三種模型**：非 streaming VAD、streaming VAD、非 streaming mVAD/AED（speech / singing / music 三類，可重疊） — [HF model card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md); [GitHub FireRedASR2S](https://github.com/FireRedTeam/FireRedASR2S)
- **輸入**：16 kHz、16-bit、單聲道 PCM WAV — [HF model card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **建議參數**（官方範例）：streaming VAD 用 `speech_threshold=0.4, pad_start_frame=5, min_speech_frame=8, min_silence_frame=20`（CLI 範例用 0.3） — [HF model card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **語言**：model card 宣稱 speech/singing/music 偵測涵蓋「100+ languages」，benchmark 涵蓋 102 種語言 — [HF model card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **Runtime**：官方用法是 `fireredasr2s.fireredvad` Python 套件，走 PyTorch。官方頁面沒提 ONNX，並說 VAD 之後會以獨立 repo 開源 — [GitHub FireRedASR2S](https://github.com/FireRedTeam/FireRedASR2S)
  - 社群 ONNX 移植（**非官方**）：
    - [subthx/firered-vad-onnx](https://huggingface.co/subthx/firered-vad-onnx)（fp32/fp16）
    - [diarizeapp/firered-vad-onnx](https://huggingface.co/diarizeapp/firered-vad-onnx)
    - Rust crate [firered-vad](https://docs.rs/firered-vad)（內嵌約 2.3 MB 的 ONNX 與 CMVN）
    - eschmidbauer/fireredvad-onnx（`fireredvad_stream_vad_with_cache.onnx`）
- **延遲與 RTF**：論文和 repo 都沒有給 VAD 的延遲或 RTF 數值 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1); [GitHub](https://github.com/FireRedTeam/FireRedASR2S)
- **授權**：repo 和 HF card 都是 Apache-2.0 — [GitHub](https://github.com/FireRedTeam/FireRedASR2S); [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
  - → 與 MIT 專案相容，散布時需保留 LICENSE/NOTICE。

#### WebRTC VAD（py-webrtcvad wrapper）
- **介面**：frame 只能是 10/20/30 ms；輸入 16-bit 單聲道 PCM，取樣率 8/16/32/48 kHz；aggressiveness 0–3 — [py-webrtcvad](https://github.com/wiseman/py-webrtcvad)
- **原理**：TEN README 稱其為「pitch-based」 — [TEN VAD README](https://github.com/TEN-framework/ten-vad)
- **限制**：只輸出二元判斷，沒有連續機率，所以 FireRed 論文無法計算它的 AUC — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **Runtime**：C 實作，不需要 PyTorch。

#### NVIDIA NeMo Frame-VAD（Frame_VAD_Multilingual_MarbleNet_v2.0）
- **原理**：MarbleNet CNN，91.5K 參數。輸入為頻譜特徵，每 20 ms 輸出一個 speech 機率，16 kHz 單聲道 — [HF model card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
- **訓練資料**：中、英、法、德、俄、西六種語言，**沒有日語**。真實資料 2,600 h、合成資料 1,000 h、噪音 330 h，含咳嗽、笑聲、呼吸等非語音樣本。model card 沒提到 music — [HF model card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
- **Streaming**：model card 沒有說明 streaming 的用法或延遲 — [HF model card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
- **ONNX**：model card 附 ONNX 匯出範例和 ONNX Runtime（CPUExecutionProvider）推論範例。匯出步驟需要 NeMo 2.3.0 與 PyTorch，匯出後才能只用 onnxruntime — [HF model card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
- **授權**：權重採 NVIDIA Open Model License — [HF model card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
  - 允許商用（「Models are commercially usable」）。
  - 散布時須附上協議與「Licensed by NVIDIA Corporation under the NVIDIA Open Model License」notice。
  - 規避 guardrail 或提起侵權訴訟時，授權自動終止。
  - 來源：[NVIDIA Open Model License](https://www.nvidia.com/en-us/agreements/enterprise-software/nvidia-open-model-license/)

#### pyannote segmentation-3.0
- **原理**：輸入 10 秒、16 kHz 單聲道音訊。輸出 powerset 7 類：非語音、3 位說話者各自、兩兩重疊。可用 `VoiceActivityDetection(segmentation=model)` 做 VAD — [HF card](https://huggingface.co/pyannote/segmentation-3.0)
- **訓練資料**：AISHELL、AliMeeting、AMI、AVA-AVD、DIHARD、Ego4D、MSDWild、REPERE、VoxConverse — [HF card](https://huggingface.co/pyannote/segmentation-3.0)
- **Streaming**：model card 沒提到 streaming，以 10 秒視窗運作 — [HF card](https://huggingface.co/pyannote/segmentation-3.0)
- **Runtime**：需要 pyannote.audio（PyTorch）。下載前必須同意 gated 條款並使用 HF token — [HF card](https://huggingface.co/pyannote/segmentation-3.0)
- **授權**：MIT（gated） — [HF card](https://huggingface.co/pyannote/segmentation-3.0)

#### FunASR FSMN-VAD
- **原理**：FSMN（Feedforward Sequential Memory Network），支援 streaming 和 offline，16 kHz — [HF funasr/fsmn-vad](https://huggingface.co/funasr/fsmn-vad)
- **語言與大小**：FunASR Model Zoo 標示 zh/en、0.4M 參數 — [FunASR README](https://github.com/modelscope/FunASR)
- **Runtime**：FunASR README 提到 llama.cpp/GGUF 原生 runtime，SenseVoice binary「Built-in FSMN-VAD, no Python at runtime」，另有 `FunAudioLLM/fsmn-vad-GGUF` — [FunASR README](https://github.com/modelscope/FunASR)
- **授權**：toolkit code 是 MIT；README 說權重以各 model card 為準 — [FunASR README](https://github.com/modelscope/FunASR)
  - HF card metadata 標 apache-2.0 — [HF funasr/fsmn-vad](https://huggingface.co/funasr/fsmn-vad)
  - FunASR Model Open Source License v1.1 的條款 — [MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE)：
    - 要求標示來源與作者、保留模型名稱。
    - 禁止「denigration / malicious smearing」，違反時授權自動終止。
    - 聲明「for reference and learning purposes only」。
    - 準據法欄位是未填的佔位符。
  - **兩者矛盾，需要逐一確認實際下載來源的 card。**

#### sherpa-onnx（k2-fsa）
- **框架**：Apache-2.0。提供 C++、C、Python、Go、C# API；平台含 Linux、macOS、Windows、Android、iOS、WASM 等；以 ONNX 推論 — [sherpa-onnx GitHub](https://github.com/k2-fsa/sherpa-onnx)
- **支援的 VAD**：官方 VAD 文件只列 **silero-vad**（MIT）和 **ten-vad**（「modified version of the Apache License 2.0」），並提醒「read the license of the model before you use it」。FireRedVAD 不在 VAD 列表中 — [sherpa-onnx VAD docs](https://k2-fsa.github.io/sherpa/onnx/vad/index.html)

#### Picovoice Cobra
- **原理**：「On-device voice activity detection (VAD) powered by deep learning」，未公開架構。輸入是單聲道 16-bit PCM；frame length 與取樣率在執行時由 `handle.frame_length`、`handle.sample_rate` 取得 — [GitHub Cobra](https://github.com/Picovoice/cobra)
- **RTF**（論文或官方宣稱）：Raspberry Pi Zero 0.037；Intel Core Ultra 7 155H 0.0002 — [voice-activity-benchmark](https://github.com/Picovoice/voice-activity-benchmark)
- **授權**：GitHub repo 標 Apache-2.0，但每個 SDK 都需要 Picovoice Console 的 AccessKey — [GitHub Cobra](https://github.com/Picovoice/cobra)
  - 官方說引擎會連回伺服器驗證 AccessKey 和方案額度。Forever-Free 方案在額度內可商用 — [Picovoice pricing](https://picovoice.ai/pricing); [Picovoice FAQ](https://picovoice.ai/docs/faq/general)
  - → 引擎本體是專有的、受使用者額度限制。不適合當 MIT 開源專案的預設依賴，因為每位使用者都得自己申請 key 並連線驗證。

#### 其他 2023 年後可信的方法（研究原型，無可直接用的 streaming 模型）
- **SR-SAD**（Fraunhofer IIS / AudioLabs Erlangen，WASPAA 2025）：專門設計來在歌唱存在時偵測說話 — [arXiv 2512.09713](https://arxiv.org/html/2512.09713)
  - 架構：80 維 log-mel，16 kHz 下 hop 256；三層 BiGRU；870K 參數。
  - 作者自承 2 秒 chunk 的處理方式使它無法即時運作。
- **Karan et al.（Stellenbosch，Interspeech 2024）**：Wav2Vec2-XLS-R VAD，以 20 秒視窗處理，不適合 CPU streaming — [Interspeech 2024 paper](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)

### Inferences
- 同時滿足四個條件（官方 ONNX/C、無 PyTorch、寬鬆授權、有 streaming 模式）的只有 **Silero VAD**。
- **TEN VAD** 只在「授權」這一項有疑慮。
- **FireRedVAD** 的模型與授權都可用，但需要社群 ONNX 移植，或自行從 PyTorch 匯出（匯出只在開發時用到 torch，執行時不需要）。
- MarbleNet v2.0 沒有日語訓練資料，而且只給 ROC-AUC，對 kikitori 的日語場景證據不足。

### Gaps
- WebRTC VAD 的演算法細節（GMM、子頻帶能量等）和 WebRTC 本體的 LICENSE 文字，這次沒有從官方原始碼頁面驗證。py-webrtcvad 的 LICENSE 頁面內容也沒有取得。
- FSMN-VAD 的 streaming chunk 大小、frame shift、ONNX（funasr-onnx）路徑都沒找到官方明文。
- FireRedVAD、MarbleNet、Cobra 的演算法延遲（ms）沒有官方數字。TEN VAD 只有質性描述。
- NeMo toolkit 本身的 code 授權沒有在本次來源中確認（model card 只寫了權重授權）。

## 哪些方法有明確證據能把歌唱與 BGM 判為非語音？哪些把歌唱當成語音？

### Takeaway
- **沒有任何一個主流 streaming VAD 有公開證據證明能把「歌唱」判為非語音**。
- **FireRedVAD 的二元 VAD 在設計上明確把歌唱算成 voice（正類）**，只把純音樂、噪音、靜音當成非語音。它的非 streaming mVAD/AED 才把 speech、singing、music 分開輸出。
- Silero、TEN、MarbleNet、Cobra、WebRTC 都沒有任何關於歌唱的宣稱或測試。

### Cited Findings
- **FireRedVAD 二元 VAD 的定義**（論文或官方宣稱，尚未經同行審查）：「Voice = speech + singing」為正類，「Non-voice = music, silence, and noise」 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **FireRedVAD 的 mVAD**：另外訓練一個模型，輸出 speech、singing、music 三種事件的機率，可同時成立（範例輸出中 singing 段與 music、speech 段重疊）。官方只提供非 streaming 版本 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1); [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **FireRedVAD 的訓練資料**：「thousands of hours of human-annotated acoustic event data」，每句標註 speech、singing、music 的時間邊界 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **Silero 的評測資料不含音樂或歌唱**：Quality Metrics 用的資料集是 ESC-50（環境聲）、AliMeeting、Earnings21、MSDWild、AISHELL-4、VoxConverse、LibriParty 和私有資料集。頁面完全沒有 music 或 singing 資料集或宣稱 — [Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- **Silero 在電影音訊上的表現**（有獨立評測：Karan et al., Stellenbosch University, Interspeech 2024）：
  - 資料集 AVA-Speech 來自 160 部 YouTube 電影，標註 Clean-Speech、Speech+Music、Speech+Noise、Non-Speech 四類。
  - 該研究把「只有 Clean-Speech」當正類（Speech+Music 算負類）。在這個設定下，Silero（直接使用、未微調）的 AUC 為 dev 83.9%、test 81.9%。在 10% FPR 時，Silero 的 TPR 比微調後的 Wav2Vec2 VAD 低 62.4 個百分點（絕對值）。
  - 論文引述 Silero 在 AVA-Speech 上自報的 AUC 是 90%。
  - **注意**：因為正類定義不同，這組數字反映的是 Silero 會把 Speech+Music 判成語音，不等於「誤判純音樂」。論文也沒寫 Silero 的版本。
  - 來源：[Karan et al., Interspeech 2024](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)（DOI 10.21437/Interspeech.2024-1019）
- **MarbleNet v2.0 在 AVA-SPEECH 的表現**（論文或官方宣稱）：ROC-AUC 95.26。model card 沒有分項報告 Speech+Music 或純音樂的結果，訓練資料也沒提到 music — [HF card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
- **學界對歌唱誤判的觀察**（有同行審查：WASPAA 2025）：Fraunhofer IIS 指出「Existing SAD systems often misclassify singing as speech」。
  - 他們的 SR-SAD 在 MoisesDB 上 speech AUC 0.919，歌唱拒絕指標 AUC_SiRR 最高 0.726；rap 和 blues 仍然困難。
  - 論文沒有測 Silero、TEN 等現成 VAD。
  - 來源：[arXiv 2512.09713 / WASPAA 2025](https://arxiv.org/html/2512.09713)
- **TEN VAD 與 Cobra**：
  - TEN VAD README 沒有任何 music 或 singing 宣稱 — [TEN VAD README](https://github.com/TEN-framework/ten-vad)
  - Cobra 的 benchmark 只用 LibriSpeech test-clean 混入 DEMAND 噪音（0 dB SNR），並刻意濾除含人聲的噪音，不含音樂 — [voice-activity-benchmark](https://github.com/Picovoice/voice-activity-benchmark)

### Inferences
- 動畫、VTuber 的歌唱段落用任何 streaming VAD 都很可能被判成語音。FireRedVAD 是「設計上」會這樣，其他模型則是「沒有證據顯示不會」。
- 如果要排除歌唱，可行方向有兩個（推論，未驗證）：
  - 把 FireRedVAD 的 mVAD/AED（speech/singing/music 分離）當二階段過濾器，用在已切出的片段上。代價是非 streaming 帶來的延遲。
  - 下游以 ASR 信心度過濾。
- 純 BGM（無人聲）比較可能被拒絕：FireRedVAD 明確把 music 當非語音訓練；Silero v6 在 ESC-50 上的非語音判斷大幅進步（見下節）。但 ESC-50 是環境聲，不是音樂，所以這仍是推論。

### Gaps
- 找不到任何把 Silero、TEN VAD、FireRedVAD、MarbleNet 放在「歌唱或 BGM + 說話」條件下比較的同行審查研究或中立評測。
- 找不到日語動畫或 VTuber 音訊上的 VAD 評測。
- FireRedVAD mVAD 的 singing 和 music 偵測沒有公開的量化 benchmark。

## 各方法公布的準確度數字（來源與標記）

### Takeaway
- 各家都用自己挑選的資料集，自己宣稱勝出：FireRed 說自己贏 Silero 和 TEN，Silero wiki 說 v6 贏 FireRed 和 TEN，TEN 說自己贏 Silero。
- 唯一較中立的第三方數字是 Karan et al.（Interspeech 2024）對 Silero 的 AVA-Speech 評測。

### Cited Findings
- **FireRed 的 FLEURS-VAD-102 評測**（FireRed 自家評測；對 FireRedVAD 是「論文或官方宣稱」，對 Silero、TEN、FunASR、WebRTC 是「有獨立評測：FireRed 團隊，非中立」；尚未經同行審查）
  - 資料：102 種語言、9,443 個檔、frame-level、threshold 0.5。

    | 指標 | FireRedVAD | Silero | TEN | FunASR | WebRTC |
    |---|---|---|---|---|---|
    | AUC-ROC (%) | 99.60 | 97.99 | 97.81 | – | – |
    | F1 (%) | 97.57 | 95.95 | 95.19 | 90.91 | 52.30 |
    | FAR (%) | 2.69 | 9.41 | 15.47 | 44.03 | 2.83 |
    | MR (%) | 3.62 | 3.95 | 2.95 | 0.42 | 64.15 |

  - 測試集標示「coming soon」，尚未公開。比較的 Silero 版本沒有寫明。
  - 來源：[arXiv 2603.10420](https://arxiv.org/html/2603.10420v1); [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **Silero 的 Quality Metrics wiki**（對 Silero 是「論文或官方宣稱」；對 TEN、FireRed、WebRTC 是「有獨立評測：Silero 團隊，非中立」）
  - **ROC-AUC，31.25 ms 片段，Multi-Domain 驗證集**：

    | 模型 | AUC |
    |---|---|
    | Silero v6 | **0.97** |
    | Silero v5 | 0.96 |
    | FireRed VAD | 0.94 |
    | TenVad | 0.93 |
    | FireRed Streaming VAD | 0.93 |
    | 未具名商用 VAD | 0.93 |
    | Silero v4 | 0.91 |
    | WebRTC | 0.73 |

  - **噪音集準確度**（整段音訊判定法：連續 ≥ 100 ms 判為語音，整段就算有語音）：

    | 模型 | ESC-50 | Private noise |
    |---|---|---|
    | Silero v6 | **0.87** | **0.71** |
    | FireRed VAD | 0.60 | 0.67 |
    | Silero v5 | 0.61 | 0.44 |
    | TenVad | 0.42 | 0.47 |
    | FireRed Streaming | 0.39 | 0.49 |
    | WebRTC | 0 | 0.15 |

  - 來源：[Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- **TEN VAD**（論文或官方宣稱）：以 LibriSpeech、GigaSpeech、DNS Challenge 等資料手動標註測試集（放在 `testset` 目錄），畫出 precision-recall 曲線，宣稱勝過 Silero 和 WebRTC。內文沒有數值，只有圖，可用 `plot_pr_curves.py` 重現 — [TEN VAD README](https://github.com/TEN-framework/ten-vad)
- **MarbleNet v2.0**（論文或官方宣稱）ROC-AUC — [HF card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)：

  | 資料集 | ROC-AUC |
  |---|---|
  | VoxConverse-test | 96.65 |
  | VoxConverse-dev | 97.59 |
  | AMI-test | 96.25 |
  | Earnings21 | 97.11 |
  | AISHELL4-test | 92.27 |
  | CH109 | 94.44 |
  | AVA-SPEECH | 95.26 |

- **Picovoice Cobra**（論文或官方宣稱）：在 LibriSpeech + DEMAND（0 dB）上畫 ROC，比較 Cobra、py-webrtcvad、Silero 5.1。準確度只有圖、沒有文字數值 — [voice-activity-benchmark](https://github.com/Picovoice/voice-activity-benchmark)
- **Silero 的第三方評測**（有獨立評測：Karan et al., Interspeech 2024）：AVA-Speech（只算 clean speech 為正類）test AUC 81.9%；南非肥皂劇資料 test AUC 74.6%，10% FPR 下 TPR 28.0% — [Interspeech 2024](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)
- **pyannote segmentation-3.0、FSMN-VAD**：model card 都沒有報告 VAD 指標 — [pyannote card](https://huggingface.co/pyannote/segmentation-3.0); [fsmn-vad card](https://huggingface.co/funasr/fsmn-vad)
  - FSMN-VAD 唯一的數字來自 FireRed 的評測（F1 90.91、FAR 44.03%），FireRed 解讀為「over-prediction of speech」 — [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)

### Inferences
- FireRed 和 Silero 的排名互相矛盾。在 FireRed 的測試集上 FireRedVAD 勝出；在 Silero 的 multi-domain 與噪音集上 Silero v6 勝出。兩邊都是對自家有利的資料選擇。
- 對 kikitori 來說，最可信的做法是自建小型評測集（日語動畫、VTuber 片段，含 BGM 與歌唱），自己跑比較。
- FunASR FSMN-VAD 的 FAR 高達 44%。在 BGM 很重的場景下，它可能把大量非語音判成語音（推論，評測者非中立）。

### Gaps
- 沒有找到 2023 年後 ICASSP、Interspeech、TASLP 等場合中，把 Silero v5/v6、TEN、FireRed 放在同一個中立 benchmark 上的論文。
- FLEURS-VAD-102 尚未公開，無法重現。

## TEN VAD 與 FireRedVAD：官方宣稱 vs. Silero、benchmark、授權

### Takeaway
- **TEN VAD** 宣稱 PR 曲線勝過 Silero、延遲更低、函式庫更小，但沒有數值，只有圖。授權是 Apache 2.0 加上 Agora 的「禁止競爭、只限自家應用與其直接終端使用者」條款，**不是 OSI 標準授權**。
- **FireRedVAD** 宣稱在 FLEURS-VAD-102 上 F1 97.57 vs. Silero 95.95，FAR 2.69 vs. 9.41。授權是乾淨的 Apache-2.0。

### Cited Findings
- **TEN VAD 授權原文要點**（Copyright © 2025 Agora） — [LICENSE](https://raw.githubusercontent.com/TEN-framework/ten-vad/main/LICENSE)：
  - 「You may not Deploy the ten-vad in a way that competes with Agora's offerings」，以及「including without limitation enabling any third party to develop or deploy Applications」。
  - 可以用於商用或非商用，但限於「solely for your benefit」及「the benefit of your direct End Users」。
  - 「Derivative Works of the ten-vad remain subject to this Open Source License」。
  - LICENSE 沒有區分程式碼和模型權重，條款泛指「the ten-vad」。
  - 對照：README 寫「Apache 2.0 with additional conditions」 — [README](https://github.com/TEN-framework/ten-vad)；sherpa-onnx 稱之為「modified version of the Apache License 2.0」 — [sherpa-onnx docs](https://k2-fsa.github.io/sherpa/onnx/vad/index.html)
- **TEN VAD 對 Silero 的宣稱**（論文或官方宣稱）：
  - PR 曲線「best performance」、「superior precision」。
  - Xeon Gold 6348 上 RTF 0.0086 vs. 0.0127。
  - Linux 函式庫 306 KB vs. 2.16/2.22 MB。
  - Silero 在 speech→non-speech 轉換有「several hundred milliseconds」延遲。
  - 來源：[README](https://github.com/TEN-framework/ten-vad)
- **反方數據**（有獨立評測：Silero 團隊，非中立）：TEN VAD 的 Multi-Domain AUC 0.93，低於 Silero v6 的 0.97；ESC-50 準確度 0.42，低於 0.87 — [Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- **另一方數據**（有獨立評測：FireRed 團隊，非中立）：TEN VAD FAR 15.47%，Silero 9.41%；但 TEN 的 MR 最低（2.95%）。這顯示 TEN 偏向「多判語音」 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **FireRedVAD 對 Silero 的宣稱**（論文或官方宣稱，尚未經同行審查）：AUC-ROC 99.60 vs. 97.99；F1 97.57 vs. 95.95；FAR 2.69 vs. 9.41；MR 3.62 vs. 3.95 — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)
- **FireRed 的反方數據**（有獨立評測：Silero 團隊，非中立）：FireRed Streaming VAD 的 Multi-Domain AUC 0.93，FireRed VAD 0.94，Silero v6 0.97。ESC-50 上 FireRed Streaming 0.39、FireRed VAD 0.60、Silero v6 0.87 — [Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- **FireRedVAD 授權**：Apache-2.0（GitHub repo 與 HF model card） — [GitHub](https://github.com/FireRedTeam/FireRedASR2S); [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md)
- **作者機構**：Super Intelligence Team, Xiaohongshu Inc.（Kaituo Xu 等） — [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)

### Inferences
- **TEN VAD 對 MIT 專案的影響**：kikitori 本身仍可維持 MIT。但只要隨附或依賴 TEN VAD，這些額外條件就會傳到下游，限制轉散布與「競爭 Agora」的用途。這與 MIT 使用者對寬鬆授權的預期不符。建議至多作為選用外掛，不要預設打包，並在文件中標明。（法律判斷需使用者自行確認。）
- **FireRedVAD 對 MIT 專案的影響**：授權上可行。主要成本是官方沒有 ONNX，要選擇社群移植版本或自行匯出，並保留 Apache-2.0 的 NOTICE。
- 兩個專案都把 Silero 當主要對手，但雙方評測結果互相矛盾。實際選型應該以 kikitori 自己的資料決定。

### Gaps
- TEN VAD 的 LICENSE 條款是否曾修改過（例如早期版本），本次只看到 main 分支的現行版本，沒有查 git 歷史。
- FireRedVAD 承諾的獨立 repo 在調查時尚未看到。之後若推出官方 ONNX 版，結論會改變。

## 哪些有官方 ONNX 或非 PyTorch 推論路徑？frame 與取樣率要求？

### Takeaway
- **官方提供非 PyTorch 路徑的**：Silero（ONNX，16 kHz 下 512 samples）、TEN VAD（C/ONNX/WASM，16 kHz，hop 160/256）、WebRTC（C，10/20/30 ms）、Cobra（專有 C SDK）、FSMN-VAD（GGUF/llama.cpp）、MarbleNet（官方有 ONNX 匯出範例，但匯出需要 NeMo）。
- **FireRedVAD 只有官方 PyTorch 路徑**；pyannote 需要 PyTorch。

### Cited Findings

| 方法 | 官方非 PyTorch 路徑 | 取樣率 / frame |
|---|---|---|
| Silero VAD | ONNX（onnxruntime ≥ 1.16.1）、tinygrad（16k）、多語言範例 | 8k/16k；評測以 31.25 ms 片段 |
| TEN VAD | C、ONNX（onnxruntime ≥ 1.17.1）、WASM、Java、Go | 只支援 16 kHz；hop 160/256 samples（10/16 ms） |
| FireRedVAD | 官方無（PyTorch `fireredasr2s`）；社群 ONNX/Rust 移植 | 16 kHz 16-bit 單聲道；25 ms 窗 / 10 ms shift |
| WebRTC VAD | C（py-webrtcvad wrapper） | 8/16/32/48 kHz；frame 10/20/30 ms |
| NeMo MarbleNet v2.0 | ONNX 匯出範例 + onnxruntime CPU | 16 kHz；每 20 ms 一個輸出 |
| pyannote seg-3.0 | 無（pyannote.audio / PyTorch） | 16 kHz；10 秒視窗 |
| FSMN-VAD | GGUF/llama.cpp（`fsmn-vad.gguf`） | 16 kHz；frame 未載明 |
| Cobra | 專有 C/Python SDK（需 AccessKey） | 16-bit PCM；frame 由 API 回報 |
| sherpa-onnx | 框架本身就是 ONNX runtime，支援 silero-vad 與 ten-vad | 依模型而定 |

- 各列來源：
  - Silero：[README](https://github.com/snakers4/silero-vad); [Version history](https://github.com/snakers4/silero-vad/wiki/Version-history-and-Available-Models); [Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
  - TEN VAD：[README](https://github.com/TEN-framework/ten-vad)
  - FireRedVAD：[GitHub](https://github.com/FireRedTeam/FireRedASR2S); [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md); [arXiv](https://arxiv.org/html/2603.10420v1); [subthx ONNX](https://huggingface.co/subthx/firered-vad-onnx); [docs.rs firered-vad](https://docs.rs/firered-vad)
  - WebRTC：[py-webrtcvad](https://github.com/wiseman/py-webrtcvad)
  - MarbleNet：[HF card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0)
  - pyannote：[HF card](https://huggingface.co/pyannote/segmentation-3.0)
  - FSMN-VAD：[FunASR README](https://github.com/modelscope/FunASR)
  - Cobra：[Cobra GitHub](https://github.com/Picovoice/cobra)
  - sherpa-onnx：[sherpa-onnx docs](https://k2-fsa.github.io/sherpa/onnx/vad/index.html); [sherpa-onnx GitHub](https://github.com/k2-fsa/sherpa-onnx)

### Inferences
- **Windows + Python 3.12 + onnxruntime** 的組合下，風險最低的是 Silero ONNX（直接用，或透過 sherpa-onnx）。
- TEN VAD 的 Python 預建 binding 主要針對 Linux x64，Windows 可能要走 ONNX 或自行編譯 C 函式庫（推論，需實測）。
- 所有候選都要求 **16 kHz 單聲道**。kikitori 擷取電腦播放音訊（通常 48 kHz 立體聲）時，必須先做 downmix 和重採樣。

### Gaps
- FireRedVAD 社群 ONNX 移植與官方 PyTorch 輸出是否數值一致，沒有找到驗證。
- MarbleNet ONNX 匯出後能否以 streaming（有狀態、逐 frame）方式推論，model card 沒有說明。
