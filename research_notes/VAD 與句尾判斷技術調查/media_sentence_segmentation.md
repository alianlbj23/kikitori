# 連續媒體音訊的句子切分方法調查（Scenario A：電腦播放音訊 → 串流 ASR → 雙語翻譯字幕）

> 基準做法：Silero VAD + 規則（停頓 0.6 s 即斷句、超過最大長度強制切）。
> 標記規則：每項效能敘述標記為「論文或官方宣稱」或「有獨立評測（註明誰）」。arXiv 未同行審查者標「尚未經同行審查」。公司部落格（Daily）屬官方發布、帶行銷性質，另外標出。
> 本次調查約 18 次工具呼叫，部分欄位（授權、日文數據）未能逐一查證，已列入 Gaps。

---

## Q1：哪些方法有同行審查證據顯示比 pause-based VAD 切分更好？好多少？

### Takeaway
有同行審查證據的「模型式切分優於純停頓切分」主要來自離線 ST（Interspeech 2022 的 SHAS 與 NAIST 方法、EMNLP 2023 Findings 的 LLM 文字切分），幅度約 +2.7～3.2 BLEU 或可保留手動切分 95–98% 的 BLEU。不過這些都是離線、TED 類演講資料，沒有一篇在動畫／直播類媒體上評測。唯一找到的第三方 IWSLT 2023 直接比較顯示，商用 VAD 與 SHAS 差距不大。

### Cited Findings

**SHAS（Supervised Hybrid Audio Segmentation，Tsiamas et al., UPC，Interspeech 2022，背景文獻）**
- 原理：用 wav2vec 2.0 / XLS-R 特徵加上 frame-level 分類器，預測每個 frame 是否屬於「手動切分的句段」，再以機率式 Divide-and-Conquer（pDAC）在指定最大長度內切開。這是有長度約束的切分方式。— [arXiv 2202.04774](https://arxiv.org/abs/2202.04774)
- 「保留手動切分 BLEU 的 95–98%，既有最佳方法則為 87–93%」，資料為 MuST-C 與 mTEDx 共 5 個語言對；另宣稱對未見過的語言有 zero-shot 效果，但未給細節。摘要沒有寫出相對純 pause-based 的 BLEU 差。（論文或官方宣稱）— [arXiv 2202.04774](https://arxiv.org/abs/2202.04774)
- 程式碼為 MIT 授權。目前只提供英文 frame classifier 權重（Google Drive），未標權重授權；西、法、義、葡與多語 checkpoint 標示「currently unavailable」。實作依賴 fairseq 與 wav2vec 2.0（PyTorch 生態），沒有 ONNX。有「strm」串流選項，但未說明延遲。— [GitHub mt-upc/SHAS](https://github.com/mt-upc/SHAS)
- 獨立採用與評價：
  - Meta 的 SeamlessM4T 技術報告（尚未經同行審查）指出 pause-based 切段「tend to be too short because speaker pauses can extend beyond sentence boundaries」，並稱 SHAS 相對簡單 pause-based 切分有顯著增益。（有獨立評測／引述：Meta）— [arXiv 2308.11596](https://arxiv.org/pdf/2308.11596)
  - IWSLT 2024 Findings 指出 offline track 的音訊切分以 SHAS 為基礎。— [ACL Anthology 2024.iwslt-1.1](https://aclanthology.org/2024.iwslt-1.1.pdf)
  - USTC 在 IWSLT 2023 把 SHAS 當作 ASR 前的 VAD 使用。— [2023.iwslt-1.15](https://aclanthology.org/2023.iwslt-1.15.pdf)
  - AppTek 在 IWSLT 2023 字幕任務調整 SHAS 閾值，用 SHAS 0.31 產生 sentence-like units。— [2023.iwslt-1.22](https://aclanthology.org/2023.iwslt-1.22.pdf)
- 與上述不一致的證據：IWSLT 2023 的〈Evaluating Multilingual Speech Translation Under Realistic Conditions〉直接比較了商用 VAD 與 SHAS，抽出的表格數字很接近（例如 15.4/22.4 對 16.4/21.5；62.0/59.6 對 61.9/60.4，dev/test）。但表格在擷取時已亂，指標名稱無法確認。（有獨立評測，該文作者；數字待核對原 PDF）— [2023.iwslt-1.2](https://aclanthology.org/2023.iwslt-1.2.pdf)

**NAIST 二元分類切分＋VAD hybrid（Fukuda, Sudoh, Nakamura，Interspeech 2022，背景文獻，含英→日）**
- 原理：用已切分的雙語語音語料訓練「此處是否為切點」的二元分類器，並提出與 VAD 結合的 hybrid 方法。— [arXiv 2203.15479](https://arxiv.org/abs/2203.15479)
- 英→德：相對 pause-based 切分，cascade ST 的 WER 改善 9.6、BLEU +3.2；end-to-end ST 的 BLEU +2.7；hybrid 再進一步改善。實驗也涵蓋 MuST-C 英→日。（論文或官方宣稱）— [arXiv 2203.15479](https://arxiv.org/pdf/2203.15479)

**LLM＋有限狀態約束的文字切分（McCarthy et al., Google，EMNLP 2023 Findings）**
- 原理：讓 LLM 把長篇 ASR 轉寫切成可獨立翻譯的句段，解碼時用有限狀態約束防止幻覺，不需額外訓練。這是純文字端的切分方式。— [ACL Anthology 2023.findings-emnlp.19](https://aclanthology.org/2023.findings-emnlp.19)
- 相對「state-of-the-art automatic punctuation baseline」，在 9 組 TED 測試集（英→德／西／阿）上平均 +2.9 BLEU，增益完全來自切分；也能透過 prompt-tuning 或 fine-tuning 適應帶 ASR 錯誤的轉寫。（論文或官方宣稱）— [Google Research](https://research.google/pubs/long-form-speech-translation-through-segmentation-with-finite-state-decoding-constraints-on-large-language-models/)；[arXiv 2310.13678](https://arxiv.org/pdf/2310.13678)

**E2E Segmenter（Huang et al., Interspeech 2022，背景文獻）**
- 在 Conformer RNN-T 內直接預測 end-of-segment，取代獨立 VAD。在長音訊上相對 VAD baseline 的 WER 相對改善 8.5%，end-of-segment 延遲中位數降低 250 ms。（論文或官方宣稱）— [ISCA Archive huang22](https://www.isca-archive.org/interspeech_2022/huang22_interspeech.pdf)

**語意切分：雙向 LM 蒸餾（Huang, Zhang, Kumar, Chang, Sainath，Interspeech 2023）**
- 把雙向 LM（標點）知識蒸餾到串流 ASR 的 segmenter。在 YouTube captioning 任務上 WER 相對改善 3.2%，end-of-segment 延遲中位數降低 60 ms。比較對象是「以聲學停頓為 teacher 的 segmenter」，不是外部 VAD。（論文或官方宣稱）— [arXiv 2305.18419](https://arxiv.org/abs/2305.18419)；[ISCA Archive huang23b](https://www.isca-archive.org/interspeech_2023/huang23b_interspeech.pdf)

**Semantic VAD（Shi et al.，Interspeech 2023）**
- 在串流 conformer 加上標點預測 head，區分「句尾標點」（完整語意斷點）與「非句尾標點」，因此遇到句尾時不必等長尾靜音就能切。另加 ASR 輔助 loss。— [ISCA Archive shi23c](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.pdf)
- 平均延遲降低 53.3%，後端 ASR 的 CER 沒有顯著變差；評測用內部資料集。（論文或官方宣稱）— [arXiv 2305.12450](https://arxiv.org/abs/2305.12450)

### Inferences
- 「模型式切分在 ST 上比純 pause 好約 3 BLEU」的證據集中在 TED／演講類離線資料，套用到動畫、直播的程度未知。動畫對白短、多人搶話、有 BGM，pause 分布與 TED 差很多。
- IWSLT 2023 的獨立比較顯示商用 VAD 與 SHAS 差距不大，暗示一個好的 VAD 加上合併／長度規則，可能已拿到大部分增益。下一步的優化目標應是「切在語意句尾」（文字端），而不是更精細的聲學切分。
- 在串流情境下，有同行審查數據的延遲改善是 Semantic VAD 的 −53.3% 平均延遲與 Google 的 −60 ms／−250 ms 中位延遲，品質指標都是 WER／CER，不是 BLEU。

### Gaps
- 沒有找到 2023 年後在串流 ST 上「pause-based VAD vs 模型式切分」並以 BLEU／COMET 量化的同行審查比較。
- Fukuda et al. 英→日的具體 BLEU 數字未取得。
- SHAS 論文中相對純 VAD（如 WebRTC VAD）的具體 BLEU 差，在摘要中查不到，需讀全文表格。
- IWSLT 2023〈Evaluating Multilingual ST〉表格的指標名稱需核對原 PDF。

---

## Q2：「VAD 找候選停頓 ＋ 串流 ASR 輸出跑文字標點／句尾模型」是公認做法嗎？延遲與品質證據？

### Takeaway
是。學界有兩種實作：一是把標點／句尾預測整合進串流 ASR（Semantic VAD、Google 語意 segmenter），二是在 ASR 文字上另跑文字切分或標點模型（McCarthy et al. EMNLP 2023）。兩者都有同行審查證據，顯示能降低等待靜音造成的延遲，或提升 BLEU。工程上可直接取用、不依賴 PyTorch 的元件有 sherpa-onnx 的標點模型與 1-800-BAD-CODE 的 47 語 ONNX 模型。Whisper-Streaming 系列則提供「何時把文字 commit 出去」的成熟策略。

### Cited Findings

**文字端標點／句尾模型（可接在串流 ASR 後）**
- sherpa-onnx 標點模型（官方文件）：
  - `sherpa-onnx-online-punct-en-2024-08-06`：英文、CNN-BiLSTM、online，fp32 約 28 MB，int8 約 7.1 MB。來源為 Edge-Punct-Casing（arXiv 2407.13142，尚未經同行審查）。短句耗時 0.030 s（fp32）／0.013 s（int8）。（官方宣稱，機器未註明）— [sherpa-onnx punctuation pretrained models](https://k2-fsa.github.io/sherpa/onnx/punctuation/pretrained_models.html)
  - `sherpa-onnx-punct-ct-transformer-zh-en-vocab272727-2024-04-12`（int8 約 72 MB；fp32 約 281 MB）：中英、CT-Transformer、離線（offline punctuation）。轉自 ModelScope `iic/punc_ct-transformer_zh-cn-common-vocab272727`（FunASR 系列）。範例耗時 0.003–0.014 s（官方宣稱）。頁面沒有寫授權。— [同上](https://k2-fsa.github.io/sherpa/onnx/punctuation/pretrained_models.html)；[sherpa-onnx online punctuation C API](https://k2-fsa.github.io/sherpa/onnx/c-api/html/punctuation.html)
  - 這兩個都**不支援日文**。
- FunASR 預訓練模型卡寫明採用「Model License Agreement（./MODEL_LICENSE）」，HF metadata 為 `license: other`。授權內容本次未取得全文。— [HF funasr/paraformer-zh README](https://huggingface.co/funasr/paraformer-zh/raw/main/README.md)
- `1-800-BAD-CODE/punct_cap_seg_47_language`：Apache-2.0；6 層、d=512 的 Transformer，64k SentencePiece，最大長度 128；以 ONNX 發布，可用 `punctuators` 套件或直接用 onnxruntime＋SentencePiece 執行；47 語共用、不需 language tag。支援中日全形標點（。、？・），只能在預測為 full stop 的位置斷句。— [HF model card](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language)
  - 斷句 FULLSTOP F1：英文 89.29、中文 93.13，**日文沒有數據**。（官方宣稱）
  - 作者自述以新聞資料訓練，對口語可能表現不佳，「unlikely to be of production quality」，並推薦較新模型。— [同上](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language)
- 另有 xlm-roberta 版本（`xlm-roberta_punctuation_fullstop_truecase`，47 語，含句界偵測）。— [HF](https://huggingface.co/1-800-BAD-CODE/xlm-roberta_punctuation_fullstop_truecase/commits/main)

**整合式（ASR 模型直接出句尾）**
- Semantic VAD 與 Google 語意 segmenter（見 Q1）都是「標點／語意句尾」取代「長尾靜音」的同行審查實證。另有 Interspeech 2023 的 chunk-based Transformer＋CTC，直接輸出帶標點文字，宣稱 punctuation F1 與 WER 都改善（數字未取得）。— [ISCA Archive kim23](https://www.isca-archive.org/interspeech_2023/kim23_interspeech.pdf)
- 〈Streaming Punctuation〉主張跨 decoder 邊界的雙向 context 能改善最終標點；未確認發表場所，尚未經同行審查。— [arXiv 2301.03819](https://arxiv.org/pdf/2301.03819)

**Commit 策略（LocalAgreement / AlignAtt；Macháček et al., ÚFAL Charles University）**
- Whisper-Streaming（IJCNLP-AACL 2023 demo）：LocalAgreement 在連續幾次更新（每次加入新音訊）對同一前綴達成一致時才 commit。buffer 預設在 Whisper 回傳的完整 segment 處裁切；也可在確認的句尾裁切（需外部 sentence segmenter），但 README 寫「not recommended」。程式碼 MIT。VAD 選項需要 torch。— [GitHub ufal/whisper_streaming](https://github.com/ufal/whisper_streaming)；[arXiv 2307.14743](https://arxiv.org/pdf/2307.14743v2.pdf)
  - 在未切分的長篇測試集上延遲 3.3 s。（論文或官方宣稱）— [GitHub](https://github.com/ufal/whisper_streaming)
  - README 表示已逐漸被 SimulStreaming 取代。— [GitHub](https://github.com/ufal/whisper_streaming)
- SimulStreaming（2025）：結合 Simul-Whisper 的 AlignAtt policy（用 encoder-decoder attention，在接近 buffer 末端「dangerous zone」時停止解碼）與 Whisper-Streaming 介面；LocalAgreement 為較簡單的替代。MIT 授權。宣稱比 Whisper-Streaming 快約 5 倍，且為 IWSLT 2025 Simultaneous ST 最佳系統。（官方宣稱；IWSLT 排名屬共享任務評測，但為作者自述）— [GitHub ufal/SimulStreaming](https://github.com/ufal/SimulStreaming)；[CUNI IWSLT 2025 arXiv 2506.17077](https://arxiv.org/pdf/2506.17077)
  - 依賴 torch。官方明說 CPU「too slow for real-time」，建議 ≥10 GB VRAM GPU 跑 large-v3。— [GitHub](https://github.com/ufal/SimulStreaming)
  - CUNI 論文指出 LocalAgreement 每次新 chunk 都從頭重算 buffer，比 AlignAtt 耗算力。— [arXiv 2506.17077](https://arxiv.org/html/2506.17077v1)

### Inferences
- 「VAD 候選停頓＋文字句尾模型」的組合是合理的工程化版本，對應學界的 Semantic VAD／語意 segmenter 概念。具體做法：
  - 短停頓（如 0.2–0.4 s）時，若文字模型判定已到句尾就提早切；
  - 停頓長但文字模型判定句子未完成時，延後切，直到 max-length 上限。
  - 這和 SHAS 的「長度受限」思想相容：max length 保留作安全網。
- 這個「雙條件決策」本身沒有找到直接的同行審查評測（多數論文是整合在 ASR 模型內），需自行用動畫或直播資料驗證。
- LocalAgreement 的 commit 規則可以和句尾判斷解耦：已 commit 的前綴才送進標點模型，避免對尚未穩定的 hypothesis 斷句而造成字幕跳動。
- 日文目前唯一的開源 ONNX 文字端選項是 1-800-BAD-CODE 47 語模型，但沒有日文指標，且以新聞資料訓練；用在口語、動畫台詞上風險高。另一個方向是挑本身會輸出「。」的日文 ASR（例如 Whisper 系），直接用 ASR 標點當句尾訊號（推論，未查證個別模型）。

### Gaps
- FunASR MODEL_LICENSE 全文未取得，CT-Transformer 權重能否與 MIT 專案一起散布仍不明。
- sherpa-onnx 英文 online 標點模型（Edge-Punct-Casing）的權重授權，頁面沒有寫。
- 沒有找到「文字端句尾模型＋VAD」在串流字幕翻譯上同時量化延遲與 BLEU 的公開評測。
- 沒有找到日文口語（動畫、直播）標點／句界模型的同行審查評測。

---

## Q3：哪些能在 CPU 上即時執行、且不依賴 PyTorch？（逐方法約束符合度）

### Takeaway
符合「串流＋CPU 即時＋無 PyTorch＋日英＋MIT 相容」五項全滿的單一元件不存在。最接近的組合是：Silero VAD（ONNX，現有基準）＋ Smart Turn v3（ONNX、BSD-2、宣稱支援日文，作為聲學句尾輔助）＋ 1-800-BAD-CODE 47 語標點 ONNX（Apache-2.0，日文品質未知）。SHAS、SimulStreaming、Whisper-Streaming 的 VAD 路徑都依賴 PyTorch。

### Cited Findings

**Smart Turn v3（Pipecat／Daily，2025，非同行審查）**
- 原理：用 Whisper Tiny encoder 加線性分類頭（約 8M 參數），直接分析原始波形，判斷說話者這一輪是否已結束（end-of-turn）。不看轉寫文字。— [HF pipecat-ai/smart-turn-v3](https://huggingface.co/pipecat-ai/smart-turn-v3)
- BSD-2-Clause；ONNX 有 int8 約 8 MB 與 fp32 約 32 MB。— [HF](https://huggingface.co/pipecat-ai/smart-turn-v3)
- 宣稱支援 23 語、CPU 推論約 12 ms（新 CPU）、低價 AWS instance 約 60 ms。（官方宣稱；來源為公司部落格，帶行銷性質）— [Daily blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)
- v3.2 訓練資料含日文（官方 dataset card 經搜尋摘要所見）。第三方 ONNX 重包的 1,000 clip 測試集準確率約 93%（第三方宣稱，非獨立學術評測）。— [HF soniqo/Smart-Turn-v3.2-ONNX](https://huggingface.co/soniqo/Smart-Turn-v3.2-ONNX)
- Daily 承認部分語言準確率較低。社群的葡萄牙語 fine-tune 實驗發現 base model 在真實語音上誤報率高。（社群報告，可信度有限）— [Daily blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/)；[HF turn-taking-study](https://huggingface.co/marcosremar2/turn-taking-study/blob/main/README.md)
- 模型卡沒有提到 BGM 或非對話音訊。設計目標是語音助理的 turn-taking，不是媒體句子切分。— [HF](https://huggingface.co/pipecat-ai/smart-turn-v3)

**逐方法約束符合度（依上述來源整理；「unclear」表示本次未查證）**

| 方法 | 一句話原理 | 串流／附加延遲 | CPU 即時 | 無 PyTorch（ONNX 等） | 日／英（中） | BGM／唱歌／多人 | 授權（code／weights）MIT 相容 |
|---|---|---|---|---|---|---|---|
| Silero VAD + pause 規則（基準） | 語音機率＋靜音時長 | yes；延遲約等於停頓門檻 0.6 s | yes | yes（ONNX） | 語言無關 | 歌聲常被判為語音（推論） | 現有基準，本次未重查 |
| SHAS | wav2vec2 frame 分類＋pDAC 長度受限切分 | 有「strm」模式但延遲不明：unclear | unclear（XLS-R 300M 很重，推論 no） | no（fairseq/PyTorch） | 只有英文權重；ja：no | 未評測 | code MIT；weights 授權未標：unclear |
| NAIST 二元分類＋VAD hybrid | 雙語切分語料訓練切點分類器 | unclear | unclear | unclear（未找到公開權重） | 有英→日實驗 | 未評測 | unclear |
| LLM + FST 約束切分（Google） | LLM 在 ASR 文字上切出可翻譯句段 | 論文為離線長文；串流：no | no（LLM／T5-11B 級） | n/a | 英文來源 | n/a | 無開源：no |
| Semantic VAD | 串流 conformer 標點 head 偵測句尾 | yes；−53.3% 平均延遲 | unclear | 未見開源：unclear | 內部資料（推測中文） | 未評測 | unclear |
| Google 語意 segmenter／E2E segmenter | RNN-T 內預測 EOS（LM 蒸餾） | yes；−60 ms／−250 ms 中位延遲 | n/a | 無開源 | 英文 YouTube | n/a | no（無開源） |
| sherpa-onnx online punct en（CNN-BiLSTM） | 串流文字標點 | yes；ms 級 | yes（官方範例 ms 級） | yes（ONNX） | 只有英文；ja：no | 文字端，不受 BGM 直接影響 | sherpa-onnx code 授權本次未查證；weights：unclear |
| sherpa-onnx CT-Transformer zh-en | 離線文字標點 | 需對 partial 文字反覆重算 | yes | yes（ONNX） | 中英；ja：no | 文字端 | weights 屬 FunASR MODEL_LICENSE：unclear |
| 1-800-BAD-CODE pcs_47lang | 文字標點＋大小寫＋句界 | 需對 partial 文字反覆重算；128 token 上限 | yes（base 尺寸，推論） | yes（ONNX＋SentencePiece） | 中英日都支援；ja 無指標 | 文字端 | Apache-2.0：yes |
| Smart Turn v3 | Whisper-tiny encoder 聲學句尾分類 | yes；約 12 ms／次 | yes（官方宣稱） | yes（ONNX） | 宣稱 23 語含 ja | 未評測 | BSD-2：yes |
| Whisper-Streaming（LocalAgreement） | 連續 hypothesis 共同前綴才 commit | yes；約 3.3 s | 視 backend（faster-whisper 可 CPU，速度 unclear） | 主路徑 faster-whisper 為 CTranslate2；VAD 需 torch：partial | 99 語 | Whisper 對 BGM 易幻覺（未在本次查證） | MIT：yes（Whisper 權重另計） |
| SimulStreaming（AlignAtt） | 依 attention 決定何時停止解碼 | yes | no（官方：CPU too slow） | no（torch） | 99 語 | 未評測 | MIT：yes |
| Spleeter（sherpa-onnx 版）人聲分離前端 | 分離 vocals／accompaniment | 離線 API；串流：unclear | yes（RTF 約 0.07–0.08） | yes（ONNX） | 語言無關 | 專為去 BGM；但歌聲會留在 vocals（推論） | Spleeter／Deezer 權重授權本次未查證：unclear |
| UVR MDX-Net（sherpa-onnx 版） | 分離 vocals／non-vocals | 離線 | 官方 demo 標為最慢：unclear | yes（ONNX） | 語言無關 | 同上 | UVR 權重授權：unclear |

### Inferences
- 文字端模型（標點／句界）延遲極低，ms 級，但輸入是 partial 文字，需在每次 commit 或每個 VAD 候選點重跑。建議只在「VAD 偵測到候選停頓」時呼叫一次，避免持續重算。
- Smart Turn 能提供第二種、與 ASR 無關的聲學訊號。不過它是為人機對話（「我說完了，換你」）訓練的，動畫或 VTuber 常見的快速接話、獨白中途換氣，與它的目標分布不同，需實測。

### Gaps
- sherpa-onnx repo 與各 ONNX 權重的授權本次未逐一查證：一般認知為 Apache-2.0，但未在本次來源確認。
- 1-800-BAD-CODE `punctuators` 套件是否拉入 torch，模型卡未說明。
- Smart Turn 日文準確率沒有任何公開數字。
- 沒有找到上述任一元件在「動畫／直播音訊」上的獨立評測。

---

## Q4：韻律／聲學句界、日文與動畫／直播內容、BGM／歌聲處理

### Takeaway
專門針對動畫、VTuber 或遊戲音訊句子切分的同行審查研究，本次沒有找到。日文相關的同行審查切分研究主要是 NAIST 的英→日 ST 切分（2022）。BGM 處理可在 CPU 上以 sherpa-onnx 的 Spleeter ONNX 前置（官方宣稱 RTF 約 0.08），但它是離線 API，且無法把唱歌與說話分開。

### Cited Findings
- 2020 年有研究把約 30 小時日本動畫 BGM 混入 CSJ 語音（SNR 5／0／−5 dB），建出「CSJ-anime」測試集，研究 music-mixed ASR。屬 arXiv，發表場所未確認，且早於 2023。尚未經同行審查。— [arXiv 2008.12048](https://arxiv.org/pdf/2008.12048)
- Hugging Face 社群的動畫語音資料集（非同行審查）：
  - japanese-anime-speech v1 取自 visual novel 音訊，共 73,004 對、約 110 小時，偏女性聲音；— [HF joujiboi/japanese-anime-speech](https://huggingface.co/datasets/joujiboi/japanese-anime-speech)
  - v2 目標是提升 Whisper 等模型對動畫台詞的轉寫準確率。— [HF v2](https://huggingface.co/datasets/joujiboi/japanese-anime-speech-v2)
  - 授權本次未查證；可作為驗證或微調資料候選。
- 日本偶像團體歌曲語料 FruitsMusic（2024，尚未經同行審查）可作為「歌聲」干擾的測試素材候選。— [arXiv 2409.12549](https://arxiv.org/pdf/2409.12549)
- sherpa-onnx source separation（官方文件）：
  - 支援 Spleeter（vocals／accompaniment）與 UVR MDX-Net（vocals／non-vocals）；
  - Spleeter 單執行緒 CPU 在 26.1 s 音訊上 RTF 約 0.079，在 10.9 s 音訊上約 0.072（官方宣稱，硬體未註明）；
  - HF demo 把 UVR MDX 標為最慢、Spleeter 最快。— [sherpa-onnx source separation models](https://k2-fsa.github.io/sherpa/onnx/source-separation/models.html)；[HF space separate.py](https://huggingface.co/spaces/k2-fsa/source-separation/blob/main/separate.py)
- Pause-aware 自動配音（IWSLT 2024）使用 Silero VAD 取時間戳，但屬配音任務，不是 ST 切分。— [2024.iwslt-1.2](https://aclanthology.org/2024.iwslt-1.2.pdf)
- 韻律或聲學句界方面，本次找到的 2023 年後可用元件只有 Smart Turn（聲學 end-of-turn，見 Q3）。學術上的聲學 EOS（Google E2E segmenter）已被證實不如加入語意或LM 資訊的版本（Huang 2023，見 Q1）。

### Inferences
- 對 Scenario A，BGM 下 VAD 誤觸發的影響可能大於句界判斷不準：BGM 會讓 Silero 持續判為語音，導致「無停頓 → 強制 max-length 切」。可評估「Spleeter vocals 輸出 → VAD」的效果。以官方 RTF 約 0.08 推算，單核負擔可接受，但需把離線 API 改成以 chunk 送入，並承受 chunk 長度造成的延遲（推論，未經測試）。
- 唱歌（OP／ED、歌回直播）在分離後仍在 vocals 軌。要辨識「歌唱 vs 說話」需要另一個分類器（如音訊事件分類），本次未調查到 CPU／ONNX 的現成選項。

### Gaps
- 沒有找到 2023 年後針對動畫、VTuber、遊戲直播或日文口語「句子切分」的 Interspeech／ICASSP／ACL 論文。
- 沒有找到 MDX-Net ONNX 的 CPU RTF 數字。
- 沒有找到 Spleeter 在串流（chunked）模式下的品質與延遲評測。
- 沒有找到「歌唱／說話」判別模型的 ONNX CPU 選項（本次未搜尋）。
- 多說話人重疊（動畫多人搶話）對切分的影響，沒有找到相關研究。
