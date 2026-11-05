# VTuber 直播即時字幕：日語 ASR 模型選型（不依賴 PyTorch 執行）

> 標記說明：[論文宣稱/self-reported] = 模型作者自己的 model card / 論文；[獨立評測/independent] = 非模型作者所做。arXiv 未經同儕審查者另行標註。專案內部量測（6 段動畫片段 CER）只當背景，不當來源。
> 調查日期：2026-11-05。工具呼叫數有限，部分論文只讀到摘要層級，已在 Gaps 註明。

## Q1. 候選模型清單與規格（架構、參數、CT2/ONNX、授權、基準 CER）

### Takeaway
在「不需 PyTorch」的前提下，可直接接上 kikitori 現有 CTranslate2 Whisper 引擎的只有 Whisper large-v3 / large-v3-turbo 與 kotoba-whisper v1.x/v2.0 的 `-faster` 版本；ReazonSpeech k2 v2、parakeet-tdt_ctc-0.6b-ja、SenseVoice 都有 sherpa-onnx 的 ONNX 版，標準基準上的 CER 和 kotoba-whisper 差不多或更好，但要另寫一個 sherpa-onnx 後端。kotoba-whisper v2.1/v2.2 只是 Transformers pipeline 的後處理（標點、說話者分離），會用到 torch / pyannote，對 kikitori 沒有額外價值。

### Cited Findings

**A. 可直接使用的 CTranslate2 Whisper 系列**

- **kotoba-tech/kotoba-whisper-v2.0**：完整保留 Whisper large-v3 的 encoder，decoder 只剩 2 層（由 large-v3 的第一層與最後一層初始化）。表格寫 756M 參數（safetensors metadata 顯示 0.8B）。授權 Apache-2.0。訓練資料是 ReazonSpeech `all` subset，先去掉 WER>10 的轉寫，共 7,203,957 段、平均 5 秒，訓練 8 epoch — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
- kotoba-whisper-v2.0 的 CER（已去標點）[論文宣稱/self-reported]：CommonVoice 8.0 ja test 9.2、JSUT basic5000 8.4、ReazonSpeech held-out test 11.6；同一張表的 whisper-large-v3 是 8.5 / 7.1 / 14.9。也就是說 kotoba 只在 in-domain 的 ReazonSpeech 上贏，在 CV8 和 JSUT 上輸給 large-v3 — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)；同一組數字也出現在 [japanese-asr 組織頁](https://japanese-asr-readme.static.hf.space/)（同一團隊，仍屬 self-reported）
- kotoba-whisper-v2.0 速度 [論文宣稱]：相對 large-v3 快 6.3 倍（relative latency） — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)
- kotoba-whisper-v1.0 的 CER [論文宣稱]：CV8 9.4 / JSUT 8.5 / ReazonSpeech 12.2（另一份表寫 12.60） — [japanese-asr README](https://japanese-asr-readme.static.hf.space/)、[kotoba-whisper-v1.0 card](https://huggingface.co/kotoba-tech/kotoba-whisper-v1.0)
- 注意：kotoba-whisper-v1.1 的 README 用「保留標點」的 CER，large-v3 在 ReazonSpeech 上因此變成約 20.6，不能和上面去標點的數字直接比較 — [kotoba-whisper-v1.1 README](https://huggingface.co/kotoba-tech/kotoba-whisper-v1.1/blob/main/README.md)
- **kotoba-tech/kotoba-whisper-v2.0-faster**（官方 CT2 版）：由 Kotoba 官方用 `ct2-transformers-converter` 轉換，FP16。**授權寫 MIT**（原模型是 Apache-2.0，兩者不一致，需留意）。建議參數：`language="ja"`、`chunk_length=15`、`condition_on_previous_text=False`。model card 沒有 CER，也沒有談 hallucination。附的速度表是在 Apple M2 Pro 上比較 whisper.cpp / faster-whisper / HF pipeline，其中 faster-whisper 最慢（50.3 分鐘音檔：whisper.cpp 581 s、faster-whisper 2601 s、HF 807 s），表示 CPU/Mac 上不一定比較快 — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)
- **kotoba-whisper-v2.1**：Apache-2.0，基於 v2.0，只是在 Transformers `pipeline` 後面加上 punctuators 的標點後處理。保留標點的 CER：v2.0 是 17.6 / 15.4 / 17.4，v2.1 是 17.7 / 15.4 / 17.0（CV8 / JSUT / ReazonSpeech）；去標點後的 CER 和 v2.0 相同 [論文宣稱] — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.1)
- **kotoba-whisper-v2.2**：Apache-2.0，基於 v2.0，用 pipeline 後處理加上說話者分離（diarizers + pyannote.audio，需要接受 gated 的 pyannote/segmentation-3.0 與 speaker-diarization-3.1）和標點（punctuators==0.0.5）。需要 Transformers、accelerate、torchaudio。卡片上沒有提到 CT2 版 — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2)
- **OpenAI Whisper large-v3 / large-v3-turbo**：是 kotoba 表格中的基準。本次搜尋**沒有找到** large-v3-turbo 在 JSUT / CV / ReazonSpeech 上的官方日語 CER — [搜尋結果彙整，見 japanese-asr README](https://japanese-asr-readme.static.hf.space/)。唯一找到的 turbo 日語數字來自個人模型 anime-whisper 的評測（見 Q2，屬低可信度）

**B. 只有 ONNX 版（需要新的 sherpa-onnx / onnxruntime 後端）**

- **reazon-research/reazonspeech-k2-v2**：字元級 RNN-T，encoder 是 Zipformer，159.34M 參數，用 ReazonSpeech v2.0 訓練，Apache-2.0。可處理約 30 秒以內的片段。model card 沒有 CER，也沒說明是否支援串流 — [HF model card](https://huggingface.co/reazon-research/reazonspeech-k2-v2)
- 對應的 sherpa-onnx 模型 `sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01`：列在 **offline** transducer 底下，不是串流模型；文件示範用 VAD 做模擬串流。有 int8 版：encoder 148M、decoder 2.8M、joiner 2.6M（fp32 的 encoder 是 565M）。CPU 單執行緒跑 13.4 s 範例檔：RTF 0.082（fp32）/ 0.054（int8）[獨立評測：sherpa 團隊，但只有單一範例檔] — [sherpa-onnx 文件](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html)
- **ReazonSpeech 官方模型列表**：reazonspeech-nemo-v2、reazonspeech-k2-v2、reazonspeech-espnet-v2 都是 Apache-2.0；官網的 CER 比較（JSUT-BASIC5000、CV v8.0、TEDxJP-10K）只有圖，沒有數字 — [ReazonSpeech 專案頁](https://research.reazon.jp/projects/ReazonSpeech/)；論文是 NLP2023（言語処理学会，國內非存檔性發表）[PDF](http://research.reazon.jp/_static/reazonspeech_nlp2023.pdf)
- reazonspeech-nemo-v2 的 CER [獨立評測：由 Kotoba/japanese-asr 團隊測，不是 Reazon 自己]：CV8 9.1 / JSUT 7.4 / ReazonSpeech 11.2，三項都比 kotoba-whisper-v2.0 好 — [japanese-asr README](https://japanese-asr-readme.static.hf.space/)
- **nvidia/parakeet-tdt_ctc-0.6b-ja**：Hybrid FastConformer-TDT-CTC，約 0.6B 參數，CC-BY-4.0，用 ReazonSpeech v2.0（35k 小時）訓練，**輸出帶標點**。CER（TDT / CTC，greedy，不用外部 LM）[論文宣稱]：JSUT basic5000 6.4 / 6.5、CV8 test 7.1 / 7.2、CV16.1 dev 10.1 / 10.2、CV16.1 test 13.2 / 13.3、TEDxJP-10k 9.0 / 9.1。卡片上沒有 CSJ 結果，沒提串流，也沒提 ONNX — [HF model card](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja)
- parakeet 的 sherpa-onnx 版：`sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8`（只匯出 CTC 分支），`model.int8.onnx` 625M。2 執行緒 greedy，13 s 範例：RTF 0.106。文件有內建 `sherpa-onnx-vad-microphone-offline-asr` 搭配 silero_vad.onnx 的示範；沒有 TDT 版 — [sherpa-onnx NeMo 日語頁](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/japanese.html)
- **FunAudioLLM/SenseVoiceSmall**：非自回歸的端到端模型，參數量「與 Whisper-Small 相近」（卡片沒有確切數字）。支援中、粵、英、日、韓。宣稱處理 10 s 音訊只要 70 ms，比 Whisper-Large 快約 15 倍 [論文宣稱]。可以輸出 BGM、掌聲、笑聲、哭聲、咳嗽、噴嚏等事件標籤和情緒。卡片沒有日語 CER（基準只用圖片呈現）。授權標示為「model-license」（FunASR 自訂授權，不是標準 SPDX） — [HF model card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
- SenseVoice 的 sherpa-onnx 版：`sherpa-onnx-sense-voice-zh-en-ja-ko-yue-2024-07-17`，壓縮檔約 1.1 GB，`model.onnx` 895 MB（另有 int8） — [sherpa-onnx SenseVoice export 文件](https://k2-fsa.github.io/sherpa/onnx/sense-voice/export.html)

**C. 2025–2026 新模型**

- **Qwen3-ASR（arXiv 2601.21337，Qwen Team，2026-01，未經同儕審查）**：1.7B 版（Qwen3-1.7B LLM + 300M AuT encoder）和 0.6B 版（Qwen3-0.6B + 180M AuT encoder），Apache-2.0，支援 30 種語言（含 ja），可透過動態 attention window 做串流。日語數字 [論文宣稱]（附錄表格沒有明說是 CER 還是 WER）：
  - Fleurs ja：0.6B 8.33、1.7B 5.20
  - CommonVoice ja：14.96、11.64
  - MLC-SLM ja：14.74、11.80
  - 噪音和歌聲方面（非日語）：ExtremeNoise（中文）上 Whisper-large-v3 63.17 vs Qwen3-ASR-1.7B 16.17；M4Singer 上 13.58 vs 5.98
  - TTFT 約 92–102 ms（並行數 1）
  - 來源：[arXiv HTML](https://arxiv.org/html/2601.21337)
- Qwen3-ASR 屬於「audio encoder + LLM decoder」架構，本次沒有找到官方的 CT2 或 ONNX 匯出（Gap）。
- 第三方 fine-tune 卡片（低可信度，個人上傳）在一份 800 段的混合集（anime/JSUT/Nekopara/CV）上報告 Qwen3-ASR-0.6B CER 0.1673、1.7B 0.1437 — [jaykwok/Qwen3-ASR-1.7B-JA-Galgame（friendli 鏡像）](https://friendli.ai/models/jaykwok/Qwen3-ASR-1.7B-JA-Galgame)

### Inferences
- 就標準基準（JSUT / CV8）來看，parakeet-tdt_ctc-0.6b-ja（JSUT 6.4、CV8 7.1）和 reazonspeech-nemo-v2（7.4 / 9.1）都比 kotoba-whisper-v2.0（8.4 / 9.2）好，但前者是自己報的數字，後者是第三方團隊測的。三者的訓練資料都是 ReazonSpeech，所以授權問題是同一個（見 Q4）。
- 要接上現有 CT2 引擎、成本最低的是 `kotoba-tech/kotoba-whisper-v2.0-faster`。它和 large-v3-turbo 一樣是「完整 large-v3 encoder + 很淺的 decoder」，在 8–12 GB VRAM 上 FP16/INT8 應該都放得下，但本次沒有找到 VRAM 實測數字。
- sherpa-onnx 後端（k2 Zipformer 或 parakeet CTC）不需要 PyTorch，CPU RTF 約 0.05–0.1，是很好的 CPU-only 選項。不過它們沒有 no_speech 機率，也沒有 Whisper 那種 prompt；而且是非自回歸（CTC）或 transducer，hallucination 的型態和 Whisper 不同（通常比較少憑空生成整句，但這是推論）。
- 雙路徑設計（greedy 即時 + beam-5 定稿）在 sherpa-onnx 可以用 greedy_search / modified_beam_search 對應（k2 transducer），但 parakeet 的 CTC 匯出只有 greedy。

### Gaps
- 沒有找到 large-v3-turbo 在 JSUT / CV / ReazonSpeech / CSJ 上的官方或同儕審查日語 CER。
- kotoba-whisper-v2.0-faster 的 VRAM、GPU RTF 都沒有官方數字；卡片授權（MIT）與原模型（Apache-2.0）不一致的原因不明。
- SenseVoiceSmall 的日語 CER 只有圖片，沒讀到數字；授權全文沒能取得。
- Qwen3-ASR 是否有官方 ONNX/CT2 匯出、日語數字是 CER 還是 WER，未確認。
- 沒有任何候選模型在 CSJ eval1–3 上有官方數字，parakeet 也沒有。

## Q2. 自發性語音、快語速、動漫/角色聲線、BGM/遊戲音、笑聲、填充詞、多人重疊的強健性證據

### Takeaway
專門測「VTuber/動漫聲線 + BGM」的同儕審查證據幾乎沒有。最接近的是一個個人模型（anime-whisper）在 Galgame 語音上的評測，結果顯示 large-v3 / turbo 比 kotoba-whisper-v2.0 與 ReazonSpeech NeMo 更強健；這個結果剛好和專案在動漫片段上的內部量測方向相反，需要自己再驗證。CSJ（自發性演講）方面，有 Sony 的 arXiv 論文報告 Whisper large-v3 的 CER 約 16.5–18.4%，比朗讀語料高出一倍以上。

### Cited Findings
- **anime-whisper（litagin，個人上傳，低可信度，但很常被引用）**：以 kotoba-whisper-v2.0 為基底，用 Galgame_Speech_ASR_16kHz（約 5,300 小時、373 萬段動漫風格台詞）微調，MIT 授權。在作者自有的 5 款遊戲（約 75k 段，與訓練資料 domain 相似但不重疊）上的平均 CER [個人宣稱]：anime-whisper 13.0、whisper-large-v3 16.5、**whisper-large-v3-turbo 16.9**、parakeet-tdt_ctc-0.6b-ja 18.6、kotoba-whisper-v2.0 18.8、reazonspeech-nemo-v2 23.6。評測時用 `no_repeat_ngram_size=5` — [HF model card](https://huggingface.co/litagin/anime-whisper)
- anime-whisper 卡片明確說**不要用 initial prompt**（會造成 hallucination 並明顯降低品質）。已知問題：句尾「。」幾乎都會被省略、重複母音或「！」很少輸出、髒話可能被轉成「○」、遊戲角色名會套用遊戲裡的漢字。沒有 CT2 版 — [HF model card](https://huggingface.co/litagin/anime-whisper)
- **CSJ（自發性語音）**：Sony 的 Whale 論文（arXiv 2506.01439，Kashiwagi 等人，Sony Group，未經同儕審查）根據搜尋摘要報告 Whisper large-v3 在 CSJ eval1/2/3 的 CER 約 16.5–18.4%，OWSM v3.1 約 20–30%，Whale 日語約 11.7% [獨立評測：Sony 不是 Whisper 的作者]。**PDF 內文無法解析，數字只來自搜尋摘要，未逐字確認** — [arXiv 2506.01439](https://arxiv.org/pdf/2506.01439)
- 另一篇 arXiv 2312.03668（Integrating Pre-Trained Speech and Language Models）也在 CSJ 三個 eval set 上和 Whisper large-v2/v3 比較，並指出 large-v3 優勢部分來自訓練資料量（只讀到搜尋摘要） — [arXiv 2312.03668](https://arxiv.org/pdf/2312.03668)
- **噪音/音樂**：Qwen3-ASR 技術報告 [論文宣稱] 顯示 Whisper-large-v3 在極端噪音（中文 ExtremeNoise 63.17）和歌聲（M4Singer 13.58）上明顯比 Qwen3-ASR-1.7B（16.17 / 5.98）差，但沒有日語子集 — [arXiv 2601.21337](https://arxiv.org/html/2601.21337)
- **笑聲/BGM 事件**：SenseVoiceSmall 可以輸出 BGM、Laughter、Applause 等事件標籤，可當作非語音過濾的訊號；但作者也承認它的事件分類和專門的 AED 模型（BEATS、PANN）比「有差距」 — [HF model card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
- **ReazonSpeech 的語料性質**：取自地上波電視串流的自然日語語音 — [HF dataset card](https://huggingface.co/datasets/reazon-research/reazonspeech)。是電視節目語音（含綜藝、新聞），和直播的口語相近，但不是動漫角色聲線。

### Inferences
- kotoba-whisper / ReazonSpeech 系列在「電視語音」domain（ReazonSpeech test）上優勢最大，在 out-of-domain（JSUT / CV / Galgame）上就不一定贏 large-v3。VTuber 直播介於電視綜藝和動漫聲線之間，標準基準無法代表，**應以專案自己的 VTuber 片段評測為準**。
- 專案內部量測（kotoba 在 6 段動漫片段上 CER 最低）和 anime-whisper 卡片（kotoba 比 turbo 差）互相矛盾，可能原因包括：片段 domain 不同（直播 vs Galgame 錄音室台詞）、正規化方式不同、有沒有用 VAD 切段。建議擴大內部測試集（至少數十段 VTuber 直播，包含 BGM、笑聲、多人連動）後再決定。
- 填充詞（えー、あの）：ReazonSpeech 的字幕型轉寫通常會省略填充詞（這是推論，沒找到明文），所以用它訓練的模型可能傾向「不輸出填充詞」。這對字幕而言其實是優點。

### Gaps
- 沒有找到任何同儕審查論文直接評測日語 ASR 在動漫/高音角色聲線、遊戲音效 + BGM、多人重疊（VTuber 連動）上的表現。
- Whale 論文 CSJ 表格的確切數字、是否包含 kotoba / ReazonSpeech 模型，未能確認。
- 快語速、填充詞的定量證據：沒找到。

## Q3. Whisper 在非語音/音樂上的 hallucination 與緩解方法

### Takeaway
同儕審查文獻支持「VAD 前置過濾」是最有效的單一手段：Silero VAD 能把非語音造成的大量 hallucination 幾乎消除，再加上「去循環 + 常見幻覺片語黑名單（Bag of Hallucinations）」可以進一步改善。no_speech 機率在相關論文中沒有被當成可靠的獨立手段來評估，這和專案觀察到 turbo 的 no_speech 幾乎都是 0 一致。

### Cited Findings
- **Barański et al.（AGH University of Krakow），"Investigation of Whisper ASR Hallucinations Induced by Non-Speech Audio"，ICASSP 2025**（arXiv 2501.11378，GitHub repo 名稱是 ICASSP2025_Whisper_Hallucination）[獨立評測]：
  - 測試資料：Whisper large-v3 跑 301,317 個非語音檔（AudioSet、MUSAN、UrbanSound8K、FSD50K，已去掉語音與音樂，另加白/粉紅噪音與靜音）
  - **40.3%（121,378 個）產生 hallucination**，其中 9.1% 是重複循環（looping）
  - 最常見的片語：「thank you」24.76%、「thanks for watching」10.32%、「so」3.80%、「thank you for watching」2.58%（沒有日語結果）
  - — [arXiv HTML](https://arxiv.org/html/2501.11378v1)
- 同一篇的 WER（Table VII，語音與非語音混合的兩種條件 NO || OL）：
  - 不處理：104.8 || 112.0
  - WebRTC VAD：68.3 || 75.4
  - **SileroVAD：8.0 || 10.8**
  - SileroVAD + deloop + BoH：6.5 || 9.4
  - 只用 deloop + BoH（不用 VAD）：17.1 || 21.1
  - 論文沒有評估 no_speech threshold 或 Whisper 的 avg logprob；DCCRN 降噪對 hallucination 沒有幫助
  - — [arXiv HTML](https://arxiv.org/html/2501.11378v1)
- **"Careless Whisper: Speech-to-Text Hallucination Harms"**（Koenecke、Choi、Mei、Schellmann、Sloane；ACM DOI 10.1145/3630106.3658996，即 FAccT 2024）：約 1% 的轉寫含有整段捏造的片語或句子，其中 38% 帶有明顯傷害（暴力、捏造關聯、假權威）。非語音時長比例較長的說話者（例如失語症）hallucination 比例較高 — [arXiv 2402.08021](https://arxiv.org/abs/2402.08021)
- kotoba-whisper-v2.0-faster 官方建議 `condition_on_previous_text=False`，可避免前文造成的連鎖 hallucination — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)
- anime-whisper 卡片建議用 `no_repeat_ngram_size` 5–10 或 `repetition_penalty`>1.0 抑制重複型 hallucination [個人宣稱] — [HF model card](https://huggingface.co/litagin/anime-whisper)
- sherpa-onnx 官方文件的日語 parakeet / k2 範例本身就是「Silero VAD → offline ASR」的管線 — [sherpa-onnx NeMo 日語頁](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/japanese.html)

### Inferences
- 「ご視聴ありがとうございました」對應的就是 Barański 論文中的「thanks for watching」類幻覺（YouTube 字幕訓練資料的片尾句）。kikitori 可以建立一份**日語版 Bag of Hallucinations**（ご視聴ありがとうございました、チャンネル登録お願いします、字幕 by … 等），在 final pass 後用字串比對移除。這是論文方法的直接移植，但日語清單要自己從靜音/BGM 測試中蒐集。
- 因為 Silero VAD 已經在用，剩下的 hallucination 主要來自「VAD 誤判為語音的 BGM、歌聲、笑聲」。可以考慮的強化：提高 VAD 門檻、加上能量/音樂偵測，或用 SenseVoice 的 BGM/Laughter 事件標籤當二次判斷（後者需要 ONNX 後端）。
- no_speech_prob 在 distil/turbo 這類淺 decoder 模型上不可靠，和專案量測一致；不建議把它當主要閘門，最多當輔助特徵，搭配 compression_ratio 與 avg_logprob。
- distil 系列（kotoba）是否比 large-v3 更少 hallucination：本次沒有找到日語上的證據。

### Gaps
- 沒有找到量測 large-v3-turbo 或 distil-whisper 在非語音上 hallucination 比例的同儕審查論文；也沒有日語幻覺片語的統計。
- Whisper 原論文長音訊啟發式規則（no_speech / logprob / compression ratio 門檻）本次沒有重新抓原文確認，不列為 cited finding。
- 2026 年那篇以 activation steering 降低非語音 hallucination 的論文（emergentmind 2606.07473）沒有讀原文。

## Q4. 授權：模型權重、訓練資料條款（ReazonSpeech）、商用與散布

### Takeaway
所有用 ReazonSpeech 訓練的模型，**權重本身**都用寬鬆授權（Apache-2.0 / MIT / CC-BY-4.0）。「著作権法第30条の4 に定める情報解析に限る」這項限制寫在**語料**的使用條件上，對象是資料集使用者（下載語料的人）。模型卡上沒有把這個限制延伸到模型使用者；但這是推論，不是法律意見，建議在 app 中把「使用者自行從 HF 下載」作為預設，並保留授權顯示。

### Cited Findings
- **ReazonSpeech 語料**：HF dataset card 寫 CDLA-Sharing-1.0（sidebar 顯示「other」），Disclaimer / License 段原文：「TO USE THIS DATASET, YOU MUST AGREE THAT YOU WILL USE THE DATASET SOLELY FOR THE PURPOSE OF JAPANESE COPYRIGHT ACT ARTICLE 30-4.」需要同意條款才能下載（gated）。超過 35,000 小時，來源是地上波電視串流 — [HF dataset card](https://huggingface.co/datasets/reazon-research/reazonspeech)
- ReazonSpeech 官方專案頁原文：語料「CDLA-Sharing-1.0（ただし利用目的は著作権法３０条の４に定める情報解析に限る）」；音訊處理函式庫是 Apache-2.0；reazonspeech-nemo-v2 / k2-v2 / espnet-v2 模型都是 Apache-2.0 — [ReazonSpeech 專案頁](https://research.reazon.jp/projects/ReazonSpeech/)
- reazonspeech-k2-v2 的 License 段寫「Apaceh Licence 2.0」（原文拼錯），連到 Apache 2.0 — [HF model card](https://huggingface.co/reazon-research/reazonspeech-k2-v2)
- kotoba-whisper-v2.0 / v2.1 / v2.2：Apache-2.0；kotoba-whisper-v2.0-faster：**MIT**。卡片都沒有轉述 ReazonSpeech 的資料條款 — [v2.0](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)、[v2.1](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.1)、[v2.2](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2)、[v2.0-faster](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)
- nvidia/parakeet-tdt_ctc-0.6b-ja：CC-BY-4.0（需要署名），同樣用 ReazonSpeech v2.0 訓練 — [HF model card](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja)
- Qwen3-ASR：Apache-2.0 — [arXiv 2601.21337](https://arxiv.org/html/2601.21337)
- SenseVoiceSmall：「model-license」，FunASR 自訂授權，不是 SPDX 標準授權，全文未取得 — [HF model card](https://huggingface.co/FunAudioLLM/SenseVoiceSmall)
- kotoba-whisper-v2.2 的說話者分離依賴 gated 的 pyannote 模型（要接受條款並登入 HF） — [HF model card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2)
- anime-whisper：MIT，但訓練資料（Galgame 語音）的著作權來源不明，卡片也警告它會處理 NSFW 音訊 — [HF model card](https://huggingface.co/litagin/anime-whisper)

### Inferences
- 第 30 條之 4 允許的是「不以享受著作表現為目的」的資訊解析（例如訓練模型）。ReazonSpeech 條款限制的是**語料**的用途；kikitori 只用模型權重做推論，沒有下載或散布語料，看起來落在語料條款之外。但 Reazon 沒有對「衍生模型的使用者」做明文說明，這點仍不確定。
- 如果 kotoba-whisper 先前是因為這個條款被擱置，那麼 parakeet-ja、reazonspeech-k2/nemo 也有**完全相同**的資料來源，換成它們並不能迴避問題；只有 OpenAI Whisper、Qwen3-ASR、SenseVoice 不依賴 ReazonSpeech（但它們自己的訓練資料也不公開）。
- 散布方式：app 內「一鍵從 HF 下載」（使用者直接向 HF 取得）比把權重打包進安裝檔風險更低，也能保留原始 LICENSE 與 model card。parakeet 的 CC-BY-4.0 需要在 app 中標示來源。
- kotoba-whisper-v2.0-faster 的授權是 MIT、原模型是 Apache-2.0，兩者都允許商用，但應以原模型（Apache-2.0）的 NOTICE 要求為保守基準。

### Gaps
- 沒有找到 Reazon 或 Kotoba 官方對「用 ReazonSpeech 訓練出來的模型，終端使用者是否受 30 條之 4 限制」的明文 FAQ；需要律師意見或直接詢問 Reazon。
- CDLA-Sharing-1.0 原文中「Results（成果）」的定義（模型是否屬於不必以 CDLA 分享的 Results）本次沒有抓原文逐字確認。
- SenseVoice model-license 全文、是否限制商用，未確認。

## Q5. Initial prompt / hotwords 對領域詞彙（VTuber 名、遊戲用語）的效果

### Takeaway
同儕審查證據顯示：直接把詞表塞進 Whisper prompt，效果有限而且不穩定；有效的作法通常需要額外訓練（keyword spotting + prompt、prompt-tuning、fine-tune 偏置指令）。對日語動漫 fine-tune 模型，作者甚至明確警告 initial prompt 會造成 hallucination。

### Cited Findings
- **Li et al.，"A Multitask Training Approach to Enhance Whisper with Contextual Biasing and Open-Vocabulary Keyword Spotting"（CB-Whisper，Interspeech 2024，arXiv 2309.09552）**：在 Whisper encoder 的 hidden states 上做開放詞彙的 keyword spotting，把偵測到的實體當作 decoder prompt，報告混合錯誤率與實體召回顯著改善（只讀到摘要層級） — [arXiv 2309.09552](https://arxiv.org/abs/2309.09552)
- **"Keyword-Guided Adaptation of Automatic Speech Recognition"（Interspeech 2024）**：用 Whisper encoder 上的 keyword spotter 產生 decoder prompt，分成 KG-Whisper（fine-tune decoder）和 KG-Whisper-PT（學習 prompt prefix）兩種 — [出版資訊](https://cris.iucc.ac.il/en/publications/keyword-guided-adaptation-of-automatic-speech-recognition-2/)
- **Hou et al.，Interspeech 2025**：處理偏置詞表超過約 1,000 詞時的「prompt dilution」問題 — [ISCA archive PDF](https://www.isca-archive.org/interspeech_2025/hou25_interspeech.pdf)
- **"Improving Rare-Word Recognition of Whisper in Zero-Shot Settings"（arXiv 2502.11572，2025，未經同儕審查）**：用 Common Voice 讓 Whisper 學會遵循「上下文偏置指令」，報告罕見詞的辨識改善 — [arXiv HTML](https://arxiv.org/html/2502.11572v2)
- anime-whisper 卡片 [個人宣稱]：不要設定 initial prompt，會造成 hallucination 與明顯的品質下降 — [HF model card](https://huggingface.co/litagin/anime-whisper)

### Inferences
- 上述有效方法都需要訓練（KWS 分支或 fine-tune），不適用於現成的 CT2 推論。kikitori 能做的是 faster-whisper 的 `initial_prompt` / `hotwords`，屬於未經訓練的 zero-shot prompt，效果應該在小規模 VTuber 詞表（幾十個名字）上自行 A/B 測試，並監控 hallucination 是否增加。尤其 distil（2 層 decoder）模型對 prompt 的反應可能和 large-v3 不同，這是推論，沒有找到文獻。
- 比較穩的替代方案：在 ASR 輸出後用詞典做字串正規化（例如把常見誤認轉成正確的 VTuber 名），或交給下游的 LLM 翻譯器，搭配名稱表做修正。
- sherpa-onnx 的 transducer（k2 Zipformer）支援 hotwords/contextual biasing（modified_beam_search + hotwords 檔），這是 sherpa-onnx 的已知功能，但本次沒有抓文件確認日語字元級模型的效果（Gap）。

### Gaps
- 沒有找到日語、或 distil/turbo 模型上「initial_prompt 對專有名詞召回」的定量研究。
- sherpa-onnx hotwords 在日語 reazonspeech-k2 上的效果與用法，本次沒有確認文件。

## 綜合建議（供報告撰寫者參考，屬推論）
- **GPU 主推薦**：`kotoba-tech/kotoba-whisper-v2.0-faster`（零整合成本，日語 ReazonSpeech test CER 11.6 vs large-v3 14.9 [self-reported]，速度約 6.3 倍）。搭配 Silero VAD 閘門、`condition_on_previous_text=False`、日語 BoH 黑名單。授權面：權重 Apache-2.0/MIT，ReazonSpeech 條款適用於語料而非推論（仍屬推論，需確認）。
- **GPU 次推薦 / 對照組**：`dropbox-dash/faster-whisper-large-v3-turbo` 或 `Systran/faster-whisper-large-v3`（不依賴 ReazonSpeech，在 Galgame 類語音上可能比 kotoba 強 [個人評測，低可信度]，但 hallucination 較嚴重）。
- **CPU-only 長期選項**（需要新後端）：sherpa-onnx 的 `sherpa-onnx-zipformer-ja-reazonspeech-2024-08-01`（int8 約 150 MB，RTF 約 0.05）或 `sherpa-onnx-nemo-parakeet-tdt_ctc-0.6b-ja-35000-int8`（625 MB，有標點，JSUT CER 6.4 [self-reported]）。
- 所有選擇都應該用專案自己的 VTuber 直播測試集（含 BGM、笑聲、連動）重新驗證，因為公開基準與內部動漫片段的結果方向不一致。
