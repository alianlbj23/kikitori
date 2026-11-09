# 音訊前端與說話者技術：kikitori 可新增或改進的機會（2025–2026）

> 調查日期：2026-11-08。範圍：kikitori（WASAPI loopback／PulseAudio monitor／ScreenCaptureKit → 16 kHz → Silero VAD v6 → faster-whisper/CTranslate2 → Ollama LLM）。
> 不重複：`research_notes/VTuber 直播模型選型調查/preprocessing_music_robustness.md`（SE／分離→Whisper 的 2022–2026 證據、Band-SCNet、HT-Demucs、RoFormer、DeepFilterNet、MossFormer2、CED 基本資料、Calm-Whisper、BoH）與 `reports/VAD 與句尾判斷技術調查.md`（Smart Turn、VAP、LiveKit、TEN、Semantic VAD、eot-bench 等）已涵蓋的項目，本文只補新項目或新事實。
> 標記：【自評】= 作者或廠商自己評自己；【獨立】= 非作者的第三方評測；【arXiv 未審查】= 未經同儕審查。
> 執行限制提醒：runtime 不可用 PyTorch；12 GB 卡上已有 Whisper turbo + large-v3（int8_float16）+ qwen3.5:9b，實測整卡最高 10.8 GB（`tasks/acceptance-report-v0.2.0.md`），所以**新模型在 GPU 上只剩約 1 GB 餘裕**，CPU 方案優先。
> 預算：約 30 次搜尋／擷取。Apple 文件頁為 JS 渲染，改讀其 JSON 端點。

## Q1. 即時語音增強／去音樂與音效：2025–2026 的新證據會改變「不加」的結論嗎？

### Takeaway
不會。2025 年 Interspeech URGENT 挑戰（Waseda、CMU、Google DeepMind、Meta 等合辦）證實：最佳 SE 系統只把下游 ASR 字元正確率從 73.41% 提到 79.80%，而純生成式 SE 在未見語言（**日語 36.8%**）上大幅崩壞並會「幻覺出語音內容」；且該挑戰的失真類型不含音樂 BGM 與競爭語音。kikitori 的主場景（BGM＋歌聲＋多人）仍沒有任何頂會證據支持在 Whisper 前加 SE。

### Cited Findings
- **URGENT 2025 挑戰（Interspeech 2025，arXiv 2505.23212，2025-05）**：失真類型為加性噪音、殘響、削波、頻寬限制、codec（MP3／OGG）、封包遺失、風噪七種；**不含競爭語音**；音樂只以 FSD50K／FMA 中的部分音樂「當背景噪音」出現 — [arXiv HTML 2505.23212](https://arxiv.org/html/2505.23212)
  - 下游 ASR 用 OWSM v3.1，指標改用 CAcc（1−CER）：未處理的含噪輸入 73.41%，第一名（判別式 T1）79.80%，純生成式 T13 67.87%（**比不處理還差**）【挑戰主辦方評測參賽者 → 對參賽者而言是獨立評測】 — 同上（Table 2）
  - 語言相依：T13 在中文 CAcc 20.1%、**日語（訓練未見）36.8%**；判別式 T1 各語言維持 75.1–86.4%；混合式 T3 只有在日語上輸給 T10 — 同上（Table 3）
  - T13「在低 SNR 時偶爾幻覺出說話內容」；DNSMOS 等非侵入式指標「即使出現幻覺也給高分」 — 同上（Sec. 4.2、4.3）
  - 32 支隊伍參賽，最佳系統為判別式，多數強隊為混合式 — [arXiv abs 2505.23212](https://arxiv.org/abs/2505.23212)
- **Whisper-AT（MIT／IBM，Interspeech 2023，背景資料）**：Whisper 的音訊表徵「不是噪音不變的」，而是會編碼背景聲音類型，Whisper 是「以噪音類型為條件」辨識語音 — [GitHub YuanGongND/whisper-at](https://github.com/YuanGongND/whisper-at)
  - 這解釋了為何前級 SE 的「去噪」不一定對 Whisper 有利：Whisper 本身已學會在噪音條件下解碼（作者論點，非直接 SE→ASR 實驗）。
- 歌詞辨識側的 2025 新事實（補充既有筆記）：QMUL/AudioShake 研究的短片段設定中，作者另提「concatenation」方法使 WER 一致下降；長片段則以分離結果當人聲活動偵測取切點 — [emergentmind 摘要 2506.15514](https://www.emergentmind.com/papers/2506.15514)（原文見既有筆記之 [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)；arXiv，機構 QMUL/AudioShake）

### Inferences
- URGENT 的結論對 kikitori 是「上限」：在 SE 的設計目標（環境噪音）上，最佳系統也只多 6.4 個百分點 CAcc，而且下游用的是 OWSM 而非 Whisper；對 Whisper large-v3（本身對噪音更強健）增益預期更小，對音樂 BGM（不在訓練分布）不可預期。
- 生成式 SE（含擴散、codec-LM 類）在日語上的崩壞與幻覺，正好和 kikitori 的主語言與主要風險（幻覺字幕）衝突 → **應明確排除生成式 SE**。
- 機會評估：

| 機會 | 證據 | 即時可行性 | 授權 | 工作量／影響 |
|---|---|---|---|---|
| 維持「不加 SE」並在文件寫明理由 | URGENT 2025、既有筆記 | — | — | S／防止日後誤加 |
| 判別式 SE 做成「麥克風口譯模式」的選配（筆電遠場、冷氣噪音） | URGENT：判別式在各語言穩定，+6.4 pt CAcc（OWSM） | 需找有 ONNX、因果、CPU 可跑的判別式模型；本輪未找到大廠官方 ONNX | 依模型 | M／小（Whisper 已強健，需自測） |
| BGM 情境的 SE | 無證據 | — | — | 不建議 |

### Gaps
- URGENT 2025 沒有用 Whisper 當下游，也沒有「乾淨語音＋音樂 BGM」條件；找不到 2025–2026 頂會針對「Whisper + 音樂背景 + SE/分離」的同儕審查研究。
- 未找到大廠官方發布、因果、ONNX 格式的判別式 SE 模型（NVIDIA Maxine 為專有 SDK、非 MIT 相容，本輪未重查）。
- URGENT 第一名系統的具體架構與是否釋出權重，本輪未查。

## Q2. 串流說話者分離（diarization）：不用 PyTorch、能在連動直播標示誰在說話嗎？

### Takeaway
2025–2026 有兩個實質進展：NVIDIA Streaming Sortformer（Interspeech 2025）與其後繼 **Nemotron 3 Diarization（2026-09-23，8 人、OpenMDW-1.1 可商用、0.32–1.04 s 延遲）**，都原生輸出逐幀多說話者機率、可處理重疊。但官方 runtime 是 NeMo/PyTorch 或 NVIDIA 的 C++/ggml（NeMo-Speech.cpp），**沒有官方 ONNX**，訓練資料**不含日語**、評測全是會議／電話，從未測過 BGM 或 VTuber。CPU 上可行、授權乾淨的路線是 sherpa-onnx（Apache-2.0）＋ pyannote segmentation-3.0（MIT）＋ 3D-Speaker／WeSpeaker 嵌入（Apache-2.0），但那是「片段後聚類」，不是真正串流。

### Cited Findings
**NVIDIA Streaming Sortformer v2／v2.1**
- 論文「Streaming Sortformer: Speaker Cache-Based Online Speaker Diarization with Arrival-Time Ordering」（Medennikov 等，NVIDIA，arXiv 2507.18446，2025-07-24，註明 **accepted to Interspeech 2025**）：以 Arrival-Order Speaker Cache 保存已出現說話者的幀級嵌入，依到達順序編號 — [arXiv 2507.18446](https://arxiv.org/abs/2507.18446)
- `diar_streaming_sortformer_4spk-v2`：**CC-BY-4.0**；117M 參數；**最多 4 人**，5 人以上退化；「主要以英文」訓練，非英語與噪音域外資料可能退化；官方 runtime 為 NeMo（PyTorch），另列 GGUF（Q8_0，147 MB）可用 NeMo-Speech.cpp；**未提 ONNX** — [HF nvidia/diar_streaming_sortformer_4spk-v2](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2)（擷取 2026-11-08）
  - 延遲設定（以 80 ms 幀為單位，延遲＝輸入緩衝，不含運算）：30.4 s（RTF 0.002）、10.0 s（0.005）、**1.04 s（RTF 0.093）**、**0.32 s（RTF 0.180）**；RTF 量測於 RTX 6000 Ada、batch 1【自評】 — 同上
  - DER（含重疊）：DIHARD III ≤4 人 1.04 s 無後處理 14.49%、0.32 s 14.64%；CALLHOME 2 人 1.04 s 7.51%、0.32 s 8.63%；DIHARD III ≥5 人 1.04 s 42.22%【自評】 — 同上
  - 輸出為 4 個 sigmoid 的逐幀說話者活動機率，同一幀可多人同時為真（原生處理重疊） — 同上
- `v2.1`（建立於 2025-10-22）：授權改為 **NVIDIA Open Model License Agreement**；約 5,000 小時訓練資料，「primarily in English」；30.4 s 設定下 AMI IHM DER 22.39 → 15.90，但 CALLHOME 5 人 19.08 → 22.35、1.04 s 的 DIHARD ≤4 人 14.49 → 15.09（變差）；頁面未提 ONNX／GGUF【自評】 — [HF v2.1](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2.1)；建立日期見 [HF API](https://huggingface.co/api/models/nvidia/diar_streaming_sortformer_4spk-v2.1)

**NVIDIA Nemotron 3 Diarization（2026-09-23）**
- 授權 **OpenMDW License Agreement v1.1**，model card 寫「ready for commercial or non-commercial use」；約 100M 參數（F32）；**最多 8 人**；逐幀 8 通道機率、依首次出現排序；評測全部含重疊語音 — [HF nvidia/Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization)（擷取 2026-11-08）
- 延遲檔位：offline 30.4 s、low（預設）1.04 s、very low 0.64 s、ultra-low 0.32 s。DER（%）：DIHARD III 12.73／13.18／13.28／13.55；CALLHOME-Part2 9.10／10.29／10.66／11.32；AMI SDM 11.14／12.80／13.06／12.95；NOTSOFAR1 SC 11.00／12.77／13.35／14.53；頁面宣稱全面優於 Sortformer v2.1【自評】 — 同上
- RTFx（Blackwell RTX PRO 5000、BF16、NeMo PyTorch、batch 1，eager／torch.compile）：1.04 s 檔 38／164、0.32 s 檔 12.5／54【自評】 — 同上
- 訓練：約 10,000 小時真實對話（含 YODAS-v2 子集）＋約 82,611 小時模擬混音；明列語言為英、中、印地、坎納達、泰盧固、孟加拉；**日語不在明列語言與評測中** — 同上
- Runtime：NeMo Framework v3.0（Python 3.12+、PyTorch）、🤗 Transformers（需從原始碼安裝）、**NeMo-Speech.cpp**（原生 C++）；支援 Ampere～Blackwell GPU，偏好 Linux；**未提 ONNX／TensorRT** — 同上
- NeMo-Speech.cpp：NVIDIA 官方、Apache-2.0 的 C++/ggml runtime，容器提供 Riva 相容 gRPC，不需 Triton 或 Python — [NGC nemo-speech.cpp](https://catalog.ngc.nvidia.com/orgs/nvidia/-/containers/nemo-speech.cpp)（搜尋摘要；容器頁未逐字擷取）
- 廠商在 VoiceArena Diarization-Bench 自報 14.72% DER（二手報導，非 model card 原文）— [unite.ai 報導](https://www.unite.ai/nvidia-releases-nemotron-3-diarization-open-weight-speaker-model/)（僅作線索）

**NVIDIA 多人 ASR（背景）**
- Multitalker Parakeet Streaming 0.6B v1：以 Sortformer 的說話者活動遮罩當側資訊，每個 instance 只轉寫一位說話者，可處理完全重疊語音，不需註冊音檔【自評】；論文 Self-Speaker Adaptation（NVIDIA，arXiv 2506.22646，2025）— [HF model card](https://huggingface.co/nvidia/multitalker-parakeet-streaming-0.6b-v1)；[arXiv 2506.22646](https://www.alphaxiv.org/abs/2506.22646)【arXiv 未審查】；ASR 只支援英文（依 Parakeet 系列，未逐字確認），對 kikitori 的日語不可用。

**pyannote（背景＋2025 更新）**
- `speaker-diarization-community-1`（2025-09 更新）：**CC-BY-4.0**；需在 HF 同意條件（gated）；組成：powerset segmentation（Interspeech 2023）＋ WeSpeaker 嵌入（ICASSP 2023）＋ VBx 聚類；相容 pyannote.audio 4.x（PyTorch）；頁面**未提串流與 ONNX**；可離線載入 — [HF pyannote/speaker-diarization-community-1](https://huggingface.co/pyannote/speaker-diarization-community-1)
  - DER（無 collar、含重疊）：AMI SDM 19.9%、DIHARD 3 20.2%、CALLHOME-2 26.7%、MSDWild 22.8%、VoxConverse 11.2%、AVA-AVD 44.6%（3.1 版依序 22.7／21.4／28.5／25.4／11.2／49.7）【自評】 — 同上
- `pyannote/segmentation-3.0` 授權 MIT（HF metadata），gated（auto）— [HF API](https://huggingface.co/api/models/pyannote/segmentation-3.0)；pyannote-audio 程式碼 MIT — [GitHub API](https://api.github.com/repos/pyannote/pyannote-audio/license)

**免 PyTorch 的 ONNX 路線**
- sherpa-onnx（k2-fsa，Apache-2.0）提供 diarization：pyannote segmentation-3.0（ONNX）＋ 3D-Speaker（CAM++、ERes2Net）／NeMo／WeSpeaker 嵌入＋快速聚類，16 kHz 單聲道 — [sherpa-onnx diarization models](https://csukuangfj.github.io/sherpa/onnx/speaker-diarization/models.html)；[HF Space k2-fsa/speaker-diarization](https://huggingface.co/spaces/k2-fsa/speaker-diarization/blob/main/model.py)；授權見 [GitHub API sherpa-onnx](https://api.github.com/repos/k2-fsa/sherpa-onnx/license)
- 3D-Speaker（阿里巴巴通義）與 WeSpeaker 程式碼皆 Apache-2.0 — [GitHub API 3D-Speaker](https://api.github.com/repos/modelscope/3D-Speaker/license)；[GitHub API wespeaker](https://api.github.com/repos/wenet-e2e/wespeaker/license)
- DiariZen（BUT，Brno）：程式碼 MIT，但**權重 CC BY-NC 4.0**，README 明言因 RAMC、MSDWild、DIHARD-3 禁止商用 — [DiariZen README](https://raw.githubusercontent.com/BUTSpeechFIT/DiariZen/main/README.md)

### Inferences
- **對 kikitori 最大的落差不是模型，而是領域**：所有 diarization 評測都是會議／電話／訪談，沒有 BGM、遊戲音效、角色聲線（VTuber 常用變聲或誇張演技）、歌聲。合理預期：BGM 中帶人聲的歌曲會被當成一個「說話者」；同一位 VTuber 換聲線可能被拆成兩人。這必須用自錄連動素材驗證。
- **GPU 預算**：Nemotron 3 Diarization 100M F32 權重約 400 MB（推算：99.2M × 4 bytes），加上 runtime 開銷，在 12 GB 組合僅剩約 1 GB 餘裕下勉強可放，但需要 NeMo-Speech.cpp（另一套 native runtime，非 ONNX）或自行匯出 ONNX（未知是否可行，Sortformer 有 FIFO＋speaker cache 狀態）。RTFx 38（1.04 s 檔、Blackwell 專業卡）顯示運算本身不是瓶頸。
- **UI 層的價值**：字幕上標「A／B／C」或顏色就能讓連動直播可讀性大增；不需要知道「是誰」，只要一致的到達順序編號（Sortformer 的 arrival-time ordering 正好提供）。
- **授權**：Sortformer v2（CC-BY-4.0，需標示）與 Nemotron 3 Diarization（OpenMDW-1.1）都允許商用；v2.1 的 NVIDIA Open Model License 需逐條確認；DiariZen 權重 NC 必須排除；pyannote community-1 CC-BY-4.0 但 gated、需 PyTorch。
- 機會評估：

| 機會 | 證據 | 即時可行性 | 授權 | 工作量／影響 |
|---|---|---|---|---|
| **A. 完整字幕後的「片段級說話者標籤」**：對每個 VAD 片段抽 3D-Speaker/WeSpeaker 嵌入（ONNX，CPU），線上增量聚類，字幕前加色塊 | sherpa-onnx 官方範例；嵌入模型為 ICASSP 2023 等 | 每片段一次小模型推論（推論：CPU 數十 ms 級，需實測）；不處理片段內換人與重疊 | Apache-2.0 | M／中（連動可讀性） |
| **B. Nemotron 3 Diarization 串流**（0.64–1.04 s 檔）於 VAD 旁並行，按幀標籤切分／標色 | Interspeech 2025 Sortformer＋NVIDIA model card【自評】 | 延遲可接受（完整字幕本身已 ~0.9 s）；需 NeMo-Speech.cpp 或自製 ONNX；GPU 餘裕緊 | OpenMDW-1.1（可商用） | L／高（但未驗證日語、BGM） |
| C. 以 diarization 切點改善 VAD 切段（換人即切） | 推論 | 同 B | 同 B | M（依附 B） |
| D. 多人重疊時的分軌 ASR（Multitalker Parakeet） | NVIDIA 自評 | 只有英文 | — | 不適用 |

### Gaps
- Nemotron 3 Diarization／Sortformer 的 ONNX 匯出是否可行、NeMo-Speech.cpp 是否正式支援 Nemotron 3 Diarization 與 Windows，model card 未寫；NGC 容器頁未逐字擷取。
- 任何 diarization 系統在日語、BGM、VTuber 聲線、歌聲上的公開評測：沒有找到。
- 3D-Speaker CAM++ 權重（ModelScope）與 sherpa-onnx 轉換版權重的授權未逐檔確認（程式碼為 Apache-2.0）。
- OpenMDW-1.1 全文未讀；與 MIT 散布的相容性需確認（kikitori 不散布權重、使用者自行下載，風險較低）。

## Q3. 歌唱／音樂偵測（切換「歌回模式」）與歌詞辨識現況

### Takeaway
ADR-0003 擋下歌回模式的第一個理由（分類器授權）可以解除，但**不是用 CED**：CED 的程式碼實為 **GPL-3.0**（權重授權未寫），應排除。授權乾淨的替代是 Google **YAMNet**（tensorflow/models 的 research 目錄為 Apache-2.0，3.7M 參數、521 類含 Speech 與 Singing）、PANNs（MIT）、EfficientAT（MIT）、Microsoft BEATs（MIT）、PaSST（Apache-2.0）。另一條零額外模型的路線是 Whisper-AT（Interspeech 2023，BSD-2）：Whisper encoder 本身就含有音訊事件資訊，但官方只支援到 large-v2、需 PyTorch。歌詞辨識方面沒找到 2025 年日語基準；Whisper 在歌曲上仍有 20%+ WER（既有筆記），所以「歌唱→標♪、不翻譯或降級顯示」仍是最有根據的產品行為。

### Cited Findings
- **CED（小米，ICASSP 2024）程式碼授權為 GPL-3.0**（GitHub 授權欄位與 API 一致），README 未寫權重授權；有 `export_onnx.py` 與 sherpa-onnx 預編譯檔 — [GitHub RicherMans/CED](https://github.com/RicherMans/CED)；[GitHub API](https://api.github.com/repos/RicherMans/CED/license)（2026-11-08 查詢）
- **YAMNet（Google）**：MobileNet-v1、AudioSet 訓練、預測 521 類（原 527 類扣掉 6 類，包含男／女性別版的 Speech 與 Singing，保留通用的 Speech、Singing）；16 kHz 單聲道、25 ms 窗／10 ms hop、64 mel；0.96 s 一個 example、50% 重疊；**3.7M 權重、每 0.96 s 69.2M 乘法**；首個輸出需 ≥975 ms 音訊；AudioSet balanced mAP 0.306；依賴 Keras 2 — [tensorflow/models YAMNet README](https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/README.md)
  - 授權：tensorflow/models 的 LICENSE 明定 `/research` 等目錄採 **Apache License 2.0**（GitHub API 顯示 NOASSERTION，因檔案開頭有目錄清單）— [tensorflow/models LICENSE](https://raw.githubusercontent.com/tensorflow/models/master/LICENSE)
  - TFLite 版有固定 15,600 樣本輸入的量化分類版；分數「未跨類別校準」，不應視為機率 — [TensorFlow Lite audio classification](https://www.tensorflow.org/lite/examples/audio_classification/overview?hl=ja)
  - ONNX：只有第三方轉換（例如 HF `andrelgomes/yamnet-onnx`，標 Speech=0、Singing=27）— [HF yamnet-onnx](https://huggingface.co/andrelgomes/yamnet-onnx)（非官方，列入排除表）
- 其他 AudioSet 分類器程式碼授權（GitHub API，2026-11-08）：PANNs `qiuqiangkong/audioset_tagging_cnn` MIT、EfficientAT（JKU）MIT、Microsoft `unilm`（含 BEATs）MIT、PaSST（JKU）Apache-2.0 — [PANNs](https://api.github.com/repos/qiuqiangkong/audioset_tagging_cnn/license)；[EfficientAT](https://api.github.com/repos/fschmid56/EfficientAT/license)；[unilm](https://api.github.com/repos/microsoft/unilm/license)；[PaSST](https://api.github.com/repos/kkoutini/PaSST/license)
- **Whisper-AT（Gong、Khurana、Karlinsky、Glass；Interspeech 2023）**：凍結 Whisper，在各層 encoder 特徵上訓練 TL-TR 做音訊標註；AudioSet mAP large-v2 41.7、small 39.8、base 37.6、tiny 36.5；宣稱額外運算 <1%（「不是所有模型」）；**程式碼與權重 BSD-2-Clause，明言歡迎商用**；表中只列到 large-v2，**未列 large-v3／turbo**；輸出時間解析度預設 10 s（可設為 0.4 s 的倍數）；實作需 PyTorch — [GitHub whisper-at](https://github.com/YuanGongND/whisper-at)
- 歌詞辨識：Jam-ALT（AudioShake，ISMIR 2024）語言為英、法、德、西，**無日語** — [HF audioshake/jam-alt](https://huggingface.co/datasets/audioshake/jam-alt/blob/main/README.md)；ISMIR 2024「Lyrics Transcription for Humans」— [ISMIR 2024 poster](https://ismir2024program.ismir.net/poster_272.html)；LyricWhiz（ISMIR 2023，Whisper＋ChatGPT）宣稱多個歌詞資料集 SOTA【自評】— [ISMIR 2023 poster](https://ismir2023program.ismir.net/poster_117.html)
- 即時歌聲偵測的經典結果（背景）：JKU 的 Lehner 等（ISMIR 2013）以 MFCC 輕量分類器達到與複雜系統相當、不需長 lookahead；EUSIPCO 2015 以 LSTM 大幅降低延遲 — [JKU ISMIR 2013](https://research.jku.at/en/publications/towards-light-weight-real-time-capable-singing-voice-detection/)；[EUSIPCO 2015](https://new.eurasip.org/Proceedings/Eusipco/Eusipco2015/papers/1570097385.pdf)

### Inferences
- **歌回模式的最小可行設計（推論）**：對每個 VAD 片段（≤10 s）跑一次 YAMNet 或 EfficientAT（CPU，數 M 參數），取「Singing／Music」對「Speech」的分數比；連續 N 段（例如 ≥3 段或 ≥15 s）判為歌唱時進入歌回狀態 → 字幕顯示「♪」或原文淡色、不送翻譯；回到說話時立即退出。時間平滑可以把「說話被誤吞」的風險壓低。YAMNet 的 0.975 s 最小輸入與 0.48 s hop 對片段級判斷綽綽有餘。
- 所有 AudioSet 分類器都必須自行匯出 ONNX（官方都沒有 ONNX 權重）；匯出是建置期工作，runtime 仍是 onnxruntime，符合「不用 PyTorch」。權重本身授權：PANNs、EfficientAT 需逐一確認權重檔授權（AudioSet 標籤為 CC-BY，影片本身版權屬 YouTube 上傳者，這是所有 AudioSet 模型的共同灰色地帶）。
- Whisper-AT 概念上最省（重用已經在跑的 encoder），但 kikitori 用 CTranslate2 的 large-v3／turbo：需要 (1) 取出各層 encoder 隱藏狀態（CTranslate2 `encode()` 只給最後一層，推論）；(2) 重新為 v3 訓練 head；工作量 L，不如獨立小分類器。
- 歌詞翻譯：沒有日語歌詞辨識基準，且 Whisper 刪和聲、刪非詞彙發聲（既有筆記）→ 建議「歌回中只顯示原文、標 ♪、不翻譯」為預設，可選擇「仍嘗試辨識」。
- 機會評估：

| 機會 | 證據 | 即時可行性 | 授權 | 工作量／影響 |
|---|---|---|---|---|
| **YAMNet／EfficientAT 片段級歌唱偵測 → 歌回模式** | Google 官方 README；ICASSP/ISMIR 經典結果 | CPU、片段級、<數十 ms（推論）；不佔 GPU | Apache-2.0（YAMNet）／MIT（EfficientAT 程式碼） | M／高（直接回應 ADR-0003；仍需歌回評測資料） |
| 同一分類器標「掌聲／笑聲／尖叫」等事件字幕（例如「（笑）」） | AudioSet 類別 | 同上 | 同上 | S（依附上項）／中 |
| Whisper-AT 式重用 encoder | Interspeech 2023 | 需重訓 v3 head、CTranslate2 不出中間層 | BSD-2 | L／中 |
| 日語歌詞辨識與翻譯 | 無日語基準 | — | — | 不建議投入 |

### Gaps
- 2025–2026 ISMIR／ICASSP 的「speech vs singing」即時分類新論文：本輪沒有找到（搜尋只回到 2013–2015 經典）。
- YAMNet、EfficientAT 在「日語說話＋BGM」「VTuber 歌回」上的誤判率：沒有公開數據，ADR-0003 的第二個理由（缺歌回評測資料）仍然成立。
- EfficientAT、PANNs 預訓練權重檔的明確授權未逐檔確認。
- 找不到 2025 年以日語為對象的歌詞辨識基準。

## Q4. 句尾／語意換手偵測：VAD 報告之後有沒有新的頂會或大廠成果？

### Takeaway
沒有找到能取代 VAD 報告結論的新頂會／大廠成果。2026 年新出現的是 arXiv 預印本（JAL-Turn，2026-03，含日語客服資料）與 LiveKit 多語 turn detector 的套件更新（含日語、但授權仍為 LiveKit Model License），兩者都不改變「Silero 停頓＋Smart Turn 加速（口譯）、停頓＋文字句尾（字幕）」的建議。

### Cited Findings
- JAL-Turn（arXiv 2603.26515，2026-03）：以 cross-attention 結合聲學與語言特徵，重用凍結 ASR encoder 使換手預測與辨識並行；宣稱在公開多語基準與「內部日語客服資料」上優於基線；註記「in progress」【arXiv 未審查、機構未確認、自評】— [arXiv 2603.26515](https://arxiv.org/abs/2603.26515)
- LiveKit turn detector 外掛 v1.5.1（2026-03）：多語模型含日語等 14 語，約 25 ms、約 400 MB RAM；仍為 LiveKit Model License — [套件頁鏡像](https://simple-repository.app.cern.ch/project/livekit-plugins-turn-detector)（小公司產品資訊，僅作線索；授權結論同 VAD 報告）
- Smart Turn v3 HF repo 最後更新 2026-01-07（VAD 報告後未見新版大改）— [HF API pipecat-ai/smart-turn-v3](https://huggingface.co/api/models/pipecat-ai/smart-turn-v3)
- 新觀察（與 Q2 交叉）：Sortformer 類串流 diarization 的「換人」事件可作為口譯模式另一種換手訊號（NVIDIA 模型卡描述逐幀說話者活動）— [HF Nemotron-3-Diarization](https://huggingface.co/nvidia/Nemotron-3-Diarization)

### Inferences
- 口譯模式（B）是兩人對話：若未來接上 diarization，「說話者從 A 變 B」本身就是最強的換手訊號，比任何單一說話者 turn 模型更直接；但 Nemotron 3 的 0.32 s 檔仍要 0.32 s＋運算，與 Smart Turn 的 ~53 ms 相比不划算，只適合當確認訊號（推論）。
- JAL-Turn 的「重用 ASR encoder」思路與 Whisper-AT 相同，在 kikitori 的 CTranslate2 架構下都受限於取不到中間層。

### Gaps
- 2026 年 Interspeech／ICASSP 已接受的 end-of-turn 論文：本輪單次搜尋未找到；建議下一輪直接查 Interspeech 2026 議程。
- JAL-Turn 作者機構、是否釋出權重未確認。

## Q5. 單一 App 擷取：Windows process loopback 與 macOS ScreenCaptureKit／Core Audio taps 的官方能力與限制

### Takeaway
官方文件確認三件事：(1) Windows `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK` 可「包含或排除」指定行程**及其子行程**的音訊、**不綁定特定輸出裝置**，但最低需求是 **Windows 10 build 20348**（一般 Windows 10 22H2 為 19045，等於實務上只有 Windows 11 可用）；(2) macOS ScreenCaptureKit 的音訊「只能以 App 為單位過濾」，kikitori 現行的 `SCContentFilter(display, excludingWindows=[])` 只要改用 `including: [SCRunningApplication]` 初始化器就能只抓瀏覽器，改動極小；(3) macOS Core Audio taps（範例要求 macOS 14.2+）可指定行程、可把行程輸出靜音後全部導向 tap，需要 `NSAudioCaptureUsageDescription` 與「系統音訊錄製」權限，且一定要掛在 aggregate device 上。

### Cited Findings
**Windows**
- `AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`：process loopback 啟用，可包含或排除指定行程與其子行程的 render 音訊；標頭 `audioclientactivationparams.h`；**最低支援用戶端 Windows 10 Build 20348** — [Microsoft Learn: AUDIOCLIENT_ACTIVATION_TYPE](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-audioclient_activation_type)
- `AUDIOCLIENT_PROCESS_LOOPBACK_PARAMS`：`TargetProcessId` 與 `ProcessLoopbackMode`；`PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE`／`..._EXCLUDE_TARGET_PROCESS_TREE` — [Microsoft Learn: PROCESS_LOOPBACK_PARAMS](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ns-audioclientactivationparams-audioclient_process_loopback_params)；[PROCESS_LOOPBACK_MODE](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-process_loopback_mode)
- 啟用入口為 `ActivateAudioInterfaceAsync` — [Microsoft Learn: ActivateAudioInterfaceAsync](https://learn.microsoft.com/en-us/windows/desktop/api/mmdeviceapi/nf-mmdeviceapi-activateaudiointerfaceasync)
- 官方範例「Application loopback audio capture」（頁面日期 2026-02-19）：「only audio from the specified process, and its children, will be captured」；可反轉為「除了該行程樹以外的所有系統音訊」；「the capture is not tied to a specific audio endpoint」；目標行程沒有 render 串流時收到靜音；**需要 Windows 10 build 20348 以上**；C++ 實作 — [Microsoft Learn sample](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)
- Rust `wasapi` crate（0.23）原始碼含 process loopback 實作（kikitori 桌面端為 Rust/Tauri，ADR-0002 已列此選項）— [docs.rs wasapi 0.23 api.rs](https://docs.rs/crate/wasapi/0.23.0/source/src/api.rs)（社群套件，非官方）

**macOS**
- WWDC22「Meet ScreenCaptureKit」：「audio capture can only be filtered at an application level」；音訊可調 sample rate 與聲道數，最高 48 kHz 立體聲；擷取需要使用者同意並記在「螢幕錄製」權限 — [Apple WWDC22 10156](https://developer.apple.com/videos/play/wwdc2022/10156/)
- `SCContentFilter` 初始化器：`init(display:including:[SCRunningApplication], exceptingWindows:)`（只包含指定 App 的視窗）、`init(display:excludingApplications:exceptingWindows:)` 等；類別可用性 macOS 12.3 — [Apple Developer: SCContentFilter](https://developer.apple.com/documentation/screencapturekit/sccontentfilter)（經 JSON 端點擷取）
- Core Audio taps（官方文章「Capturing system audio with Core Audio taps」）：範例要求「macOS 14.2 or later」（頁面 metadata 另列 macOS 26.0，兩者矛盾）；`CATapDescription.processes` 指定一個或一組行程；`AudioHardwareCreateProcessTap` 建立；「To use a tap as an input source, add it to an aggregate device」；需在 Info.plist 放 `NSAudioCaptureUsageDescription`，第一次從含 tap 的 aggregate device 錄音時系統提示「system audio recording permission」；tap 可 private；可設 `muteBehavior` 讓該行程不再播到喇叭、全部導向 tap — [Apple Developer: Capturing system audio with Core Audio taps](https://developer.apple.com/documentation/coreaudio/capturing-system-audio-with-core-audio-taps)（經 [JSON 端點](https://developer.apple.com/tutorials/data/documentation/coreaudio/capturing-system-audio-with-core-audio-taps.json) 擷取）
- Apple 開發者論壇有「tap 在系統有聲音時送出全零 buffer」回報，解法為重建 tap／aggregate device／IOProc（社群回報，非官方結論）— [Apple Developer Forums 825780](https://developer.apple.com/forums/thread/825780)

### Inferences
- **macOS 是最便宜的勝利**：kikitori 已用 ScreenCaptureKit（`capture.md`），改用「包含指定 App」的 filter 加一個 App 下拉選單，即可在 macOS 12.3+ 達成單一 App 擷取；權限與現行相同（螢幕錄製）。阻礙仍是 ADR-0002 的「沒有 Mac 實機」。
- **Core Audio taps 的額外價值**：不需要螢幕錄製權限（改為系統音訊錄製權限，使用者心理門檻較低），且不必擷取丟棄的影像幀；`capture.md` 已記為未來選項。PyObjC 綁定問題仍在（未查證 2026 狀態）。
- **Windows 的版本門檻要寫進需求**：20348 是 Windows Server 2022 的 build 號（推論，依 Windows 版本號慣例）；Windows 11 皆為 22000 以上可用；Windows 10 一般用戶端（≤19045）依官方文件不支援 → UI 要在 Windows 10 上隱藏此選項並沿用「每個 App 指定輸出裝置」替代方案。
- 「EXCLUDE_TARGET_PROCESS_TREE」模式對 kikitori 也有用：排除 Discord（連動時自己的語音通話）而不是只包含瀏覽器，適合「瀏覽器分頁很多」的使用者。
- 實作位置：ADR-0002 否決了在 Rust 端擷取（需跨行程音訊通道）。另一條路是 Python 端以 `comtypes`／ctypes 實作 COM completion handler；官方 C++ 範例可當參考規格。
- 機會評估：

| 機會 | 證據 | 可行性 | 授權 | 工作量／影響 |
|---|---|---|---|---|
| macOS：ScreenCaptureKit 改 App 過濾 | Apple WWDC22、SCContentFilter 文件 | 改動小；需 Mac 實機驗收 | Apple 系統 API | S／中 |
| Windows 11：process loopback（include／exclude） | Microsoft Learn 文件與官方範例 | build ≥20348；需寫 COM 非同步啟用；擷取不綁裝置 | 系統 API | L／高（ADR-0002 的首要使用者痛點） |
| macOS 14.2+：Core Audio taps | Apple 官方文章 | 需 aggregate device、PyObjC 綁定待查 | 系統 API | M–L／中 |

### Gaps
- Linux（PipeWire）的單一 App 擷取官方文件本輪未查。
- Windows process loopback 是否支援 `GetMixFormat`、可用的格式（官方範例自訂 PCM 格式）、以及是否在 Windows 10 21H2/22H2 上「實際可用」（社群 GStreamer commit 標題為「Allows process loopback capture on Windows 10」，與官方 20348 下限矛盾，未查證）— [GStreamer commit 鏡像](https://git.fasttube.de/FaSTTUBe/GST-Tensordecoder-ov_ep/src/commit/b75114983e85cef70da398c985e8ee7f8f68e3a1/subprojects/gst-plugins-bad/sys/wasapi2)（僅作線索）
- Core Audio taps 真正的最低 macOS 版本（14.2 vs metadata 的 26.0）需以 API reference 頁確認。

## Q6. 混語直播的語言識別（LID）：有沒有 ONNX、可商用、能逐段判斷的方案？

### Takeaway
最省的方案已經在 kikitori 的依賴裡：faster-whisper 的 `multilingual=True` 會「在每個 segment 上做語言偵測」，不需要新模型。獨立 LID 模型中，NVIDIA AmberNet（107 語、VoxLingua107 錯誤率 5.22%）是 **NGC Terms of Use**（非開源）、Meta MMS-LID 是 **CC-BY-NC-4.0**，兩者都不適合；SpeechBrain VoxLingua107 ECAPA（Apache-2.0）可用但需 PyTorch 或自行匯出 ONNX。沒有找到 2025–2026 大廠發布的「幀級、code-switching」ONNX LID。

### Cited Findings
- faster-whisper（SYSTRAN）`transcribe()` 參數 `multilingual`：「Perform language detection on every segment.」實作中每個 30 s window 在解碼前依 encoder 輸出重新偵測並更新 tokenizer 語言；`TranscriptionInfo.language` 只回報一開始的語言；逐 window 偵測不使用 `language_detection_threshold`／`language_detection_segments`（那兩個只用於 `language=None` 時的前置偵測）；`detect_language()` 回傳 (語言, 機率, 全部語言機率) — [faster-whisper transcribe.py](https://raw.githubusercontent.com/SYSTRAN/faster-whisper/master/faster_whisper/transcribe.py)（2026-11-08 擷取 master）
- NVIDIA `langid_ambernet`：VoxLingua107 訓練（107 語、6,628 小時 YouTube）、官方評估集錯誤率 **5.22%**【自評】；授權「NGC TERMS OF USE」；需微調才能支援未見語言；最新版 1.12.0（2023-04-04）— [NGC langid_ambernet](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/langid_ambernet)
  - 第三方 ONNX 移植（`surogate/ambernet-langid`）註明沒有 unknown／silence／non-speech 類別、一律輸出 107 語之一 — [HF surogate/ambernet-langid](https://huggingface.co/surogate/ambernet-langid)（非官方）
- SpeechBrain `lang-id-voxlingua107-ecapa`：Apache-2.0（HF metadata）；SpeechBrain 程式碼 Apache-2.0（PyTorch）— [HF API](https://huggingface.co/api/models/speechbrain/lang-id-voxlingua107-ecapa)；[GitHub API speechbrain](https://api.github.com/repos/speechbrain/speechbrain/license)
- Meta MMS-LID-126：**CC-BY-NC-4.0**（HF metadata）— [HF API facebook/mms-lid-126](https://huggingface.co/api/models/facebook/mms-lid-126)

### Inferences
- **立即可做（S）**：在「收聽語言＝自動偵測」時對完整字幕路徑開 `multilingual=True`，讓日英夾雜的直播（例如 EN 組成員、英語梗）逐段切換；同時把每段偵測到的語言傳給翻譯層與振假名（只對日語段標振假名）。注意：kikitori 的片段 ≤10 s，一個片段就是一個 window，所以這等於「逐 VAD 片段 LID」；句內 code-switching（一句裡日英混雜）仍不處理。
- 風險（推論）：Whisper 對短片段的 LID 不穩，誤判會導致整段被「翻譯成錯的語言」或被當成目標語言而不翻譯；應加「與使用者選定收聽語言不同時，機率需高於門檻才採用」的保護（faster-whisper 的逐 window 偵測不套門檻，需自己在外層對 `detect_language()` 結果判斷）。
- 獨立 LID 模型沒有比 Whisper 本身更好的授權／部署組合；只有在 CPU-only 的小模型（tiny/base）組合下，Whisper LID 太弱時才值得考慮 SpeechBrain ECAPA 自行匯出 ONNX（M）。
- 機會評估：

| 機會 | 證據 | 即時可行性 | 授權 | 工作量／影響 |
|---|---|---|---|---|
| **faster-whisper `multilingual=True` 逐片段 LID**＋門檻保護 | SYSTRAN 原始碼 | 每段多一次語言 token 推論（推論：毫秒級） | MIT | S／中（自動偵測模式品質） |
| SpeechBrain ECAPA VoxLingua107 → 自行 ONNX | Apache-2.0 權重 | CPU 小模型；需匯出 | Apache-2.0 | M／小 |
| AmberNet、MMS-LID | — | — | NGC ToU／CC-BY-NC | 排除 |

### Gaps
- Whisper large-v3／turbo 在 ≤10 s 日英混合片段上的 LID 準確率：沒有找到公開評測。
- 2025–2026 大廠或頂會的幀級 code-switching LID（ONNX）：本輪沒有找到。
- SpeechBrain ECAPA 能否乾淨匯出 ONNX 未驗證。

#### 排除但相關的來源（不符來源政策或授權／runtime 限制；僅作線索）

| 項目 | 排除原因 | 連結 |
|---|---|---|
| DiariZen 權重 | CC BY-NC 4.0（禁商用） | [README](https://raw.githubusercontent.com/BUTSpeechFIT/DiariZen/main/README.md) |
| CED 音訊分類器 | 程式碼 GPL-3.0、權重授權未寫 | [GitHub](https://github.com/RicherMans/CED) |
| Meta MMS-LID | CC-BY-NC-4.0 | [HF](https://huggingface.co/facebook/mms-lid-126) |
| NVIDIA AmberNet LangID | NGC Terms of Use（非開源） | [NGC](https://catalog.ngc.nvidia.com/orgs/nvidia/teams/nemo/models/langid_ambernet) |
| YAMNet／AmberNet／pyannote 社群 ONNX 轉換（andrelgomes、surogate、welcomyou 等） | 非原作者發布，權重來源需自行驗證 | [yamnet-onnx](https://huggingface.co/andrelgomes/yamnet-onnx)；[ambernet-langid](https://huggingface.co/surogate/ambernet-langid) |
| parakeet.cpp／parakeet-rs（社群 Sortformer C++／Rust 移植） | 個人專案，非 NVIDIA 官方 | [GitHub mudler/parakeet.cpp](https://github.com/mudler/parakeet.cpp)；[docs.rs parakeet-rs](https://docs.rs/crate/parakeet-rs/0.3.5/source/src/multitalker.rs) |
| pyannote.ai 部落格「pyannote vs NVIDIA NeMo」 | 競品廠商行銷 | [pyannote.ai blog](https://www.pyannote.ai/blog/pyannote-vs.-nvidia-nemo-diarization-accuracy-setup-and-production-readiness) |
| Nemotron 3 Diarization 媒體報導（unite.ai、hackernoon 等） | 二手報導 | [unite.ai](https://www.unite.ai/nvidia-releases-nemotron-3-diarization-open-weight-speaker-model/) |
| JAL-Turn | arXiv 未審查、機構未確認 | [arXiv 2603.26515](https://arxiv.org/abs/2603.26515) |
| Core Audio taps 社群範例（AudioCap、catap、recall.ai 部落格） | 個人／小公司 | [AudioCap](https://github.com/insidegui/AudioCap)；[recall.ai](https://www.recall.ai/blog/core-audio-taps) |
| LiveKit turn detector 1.5.1 | 小公司產品、LiveKit Model License | [套件頁](https://simple-repository.app.cern.ch/project/livekit-plugins-turn-detector) |
