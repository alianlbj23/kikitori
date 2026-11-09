# kikitori ASR 端的改進機會：2025–2026 語音辨識進展調查

> 調查日期：2026-11-08。範圍：補 `VTuber 直播模型選型調查/asr_models.md`、`language_coverage.md` 沒有涵蓋的部分，已經寫過的內容（kotoba-whisper、ReazonSpeech、parakeet-ja、SenseVoice、Qwen3-ASR 的基本規格、Barański BoH、CB-Whisper／KG-Whisper、Hou 2025 prompt dilution、多語言 WER 表）只引用，不重複。
> 標記：**[同儕審查]** = ICASSP／Interspeech／EMNLP／ACL Anthology（IWSLT）／IEEE workshop 等正式出版；**[自報]** = 模型開發者自己的 model card／論文；**[獨立]** = 非模型作者所做；**[預印本]** = arXiv，未經同儕審查（只收錄大型公司或知名實驗室）。
> kikitori 限制（判斷「能不能用」的依據）：執行時不用 PyTorch（CTranslate2／ONNX Runtime 等）、本機、NVIDIA 8–12 GB 或 CPU、授權可商用且與 MIT 相容、模型由使用者下載。kikitori 目前的 CT2 Whisper 介面：自行組 prompt（前文 prompt 223 token）、fast 路徑 turbo greedy 每 700 ms、final 路徑 large-v3 beam 5 + 溫度 fallback (0.0, 0.2, 0.4)、過濾規則 no_speech／avg_logprob／壓縮比／依語言片語表（`docs/specs/asr.md`）。
> 工具呼叫有限，許多論文只讀到摘要或部分內文，已在 Gaps 註明。

## Q1. 專有名詞的 contextual biasing／hotwords／prompting（Whisper 系列與 CT2／ONNX 可用的方法）

### Takeaway
在 kikitori 現有的 CT2 Whisper 上，**唯一不需要訓練、立即可做的是「把少量專有名詞放進 prompt」**（faster-whisper 的 `hotwords` 本質上也只是 prompt 提示）。效果較確定的方法（KMP／trie shallow fusion、TCPGen、關鍵詞偵測＋prompt）都需要逐步解碼時改 logits 或額外訓練模組，而 **CTranslate2 Whisper 的 `generate()` 沒有 logits processor、沒有逐步 callback**，所以要做就得換解碼後端。sherpa-onnx 有現成 hotwords，但只支援 transducer 模型。

### Cited Findings
- **CTranslate2 Whisper API 的限制**：`ctranslate2.models.Whisper` 只有 `align`、`detect_language`、`encode`、`generate`、`load_model`、`unload_model`。`generate()` 參數有 `beam_size`、`patience`、`length_penalty`、`repetition_penalty`、`no_repeat_ngram_size`、`max_length`、`return_scores`、`return_logits_vocab`、`return_no_speech_prob`、`suppress_tokens`、`sampling_topk/temperature` 等；**文件沒有任何自訂 logits processor、逐步 callback 或回傳 cross-attention 的選項**（2026-11 讀取） — [CTranslate2 Whisper 文件](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)
- **faster-whisper `hotwords`**：`hotwords: Optional[str]`，docstring 原文「Hotwords/hint phrases to provide the model with. Has no effect if prefix is not None.」，傳給 `get_prompt()` 和 `initial_prompt` 一起組成 prompt（即放進 `<|startofprev|>` 區段的文字提示，不是解碼時加分）。沒有附任何準確度數字 — [faster_whisper/transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- **KMP 比對做 contextual biasing（Google，Wang et al.，Interspeech 2024，pp. 282–286，DOI 10.21437/Interspeech.2024-1349）[同儕審查]**：beam search 中，token 延伸若讓某個偏置詞的比對前進就加分，比對中斷就把分數扣回；不新增模型參數，可當 shallow fusion 或 on-the-fly rescoring，並已向量化可在 GPU／TPU 跑。摘要宣稱在偏置測試集上降低 WER，且可和 neural associative memory 疊加；本次沒取得具體數字 — [arXiv 2310.00178](https://arxiv.org/pdf/2310.00178)、[dblp](https://dblp.dagstuhl.de/rec/conf/interspeech/WangWCMSRPSPMZS24.html)
- **TCPGen 接在 Whisper 上（Cambridge，arXiv 2306.01942，2023）**：Whisper 參數不動，外掛 tree-constrained pointer generator（需要訓練該模組）；1,000 詞偏置清單下，LibriSpeech OOV WER 相對降低 30%（只讀到摘要） — [arXiv 2306.01942](https://arxiv.org/pdf/2306.01942)。屬 2023 背景資料。
- **OWSM-Biasing（arXiv 2506.09448，2025）[預印本]**：在 Whisper 式開放模型（OWSM，CMU／ESPnet 體系）加上 dynamic vocabulary，摘要稱對完全沒看過的詞也能持續改善錯誤率；需要訓練，PyTorch／ESPnet — [arXiv 2506.09448](https://arxiv.org/pdf/2506.09448)
- **sherpa-onnx hotwords（k2-fsa 官方文件）**：**只有 transducer 模型支援**（offline 與 online 皆可），「All other models don't support hotwords」，所以 CTC（parakeet-ja 的 sherpa 匯出版）和 Whisper 都不行；必須用 `modified_beam_search`；`hotwords-score` 是每個匹配 token 的全域加分，每行可用 `:N` 覆寫；部分匹配會加分、沒完成整詞就取消。建模單位只有 `cjkchar`、`bpe`、`cjkchar+bpe`，**文件沒有提到日語**；只有前後對照例子、沒有 WER，且例子中出現加分導致無關字被改錯（控→空） — [sherpa-onnx hotwords 文件](https://k2-fsa.github.io/sherpa/onnx/hotwords/index.html)
- **Microsoft VibeVoice-ASR（2026）[自報]**：model card 寫可輸入自訂 hotwords（人名、術語、背景資訊）「significantly improves accuracy on domain-specific content」；單次處理 60 分鐘、同時輸出說話者與時間戳；MIT 授權；約 7–9B 參數（來源說法不一）；Microsoft blog 宣稱支援 50+ 語言與 code-switching，但 HF metadata 只列 en、zh — [Microsoft Foundry blog](https://techcommunity.microsoft.com/t5/microsoft-foundry-blog/introducing-vibevoice-asr-longform-structured-speech-recognition/ba-p/4501276)、[HF README](https://huggingface.co/microsoft/VibeVoice-ASR/blob/main/README.md)
- **Interspeech 2025 Mandarin–English soft prompt tuning（Yang et al.）[同儕審查]**：Whisper 上 deep prompt tuning 是最有效的 SPT 方式（SEAME、ASRU2019）；需要訓練 prompt 向量 — [ISCA archive](https://www.isca-archive.org/interspeech_2025/yang25p_interspeech.html)
- 既有筆記已記錄：CB-Whisper／KG-Whisper（Interspeech 2024，用 encoder 上的 KWS 偵測實體再當 prompt）、Hou et al. Interspeech 2025（偏置清單超過約 1,000 詞會 prompt dilution）、2502.11572（fine-tune 讓 Whisper 遵循偏置指令） — 見 `asr_models.md` Q5。

### Inferences
- **機會 1（低成本、可立即 A/B）：glossary → ASR prompt**。kikitori 已有 glossary（目前只給翻譯）和 final 路徑的前文 prompt（223 token 上限）。可把「目前直播者名稱、本場遊戲名、少數固定梗」（建議 ≤ 20 詞，避開 Hou 2025 的 dilution 區間）放在前文 prompt 之前，只用於 final 路徑。CB-Whisper／KG-Whisper 的結果顯示「prompt 裡放**正確且相關**的實體」有幫助，但它們的實體是偵測出來的、不是整張詞表。風險：Whisper 在靜音／BGM 上會把 prompt 內容吐出來成為新型 hallucination，所以要配合「輸出若只等於 prompt 中的詞就丟棄」的過濾規則。成本約 0.5–1 天＋評測；影響中等（專有名詞召回），沒有日語定量證據。
- **機會 2（中成本）：ASR 後的詞典正規化**。把常見誤認（例如假名寫法、同音漢字）對應到 glossary 正式寫法，純字串處理，不碰模型。這是 sherpa 例子中「hotwords 會帶來副作用」的保守替代方案。
- **機會 3（高成本）：真正的 shallow-fusion biasing**。KMP／trie 方法本身不需訓練，但需要逐步解碼時修改 logits。CT2 Whisper 不提供這個掛勾，要做就得 (a) 自己用 ONNX Runtime 跑 Whisper decoder 的逐步解碼迴圈（kikitori 已自行處理 prompt／beam 參數，但 beam search 本身目前在 CT2 C++ 內），或 (b) 改用 sherpa-onnx transducer（例如 reazonspeech-k2-v2，字元級）並驗證 `cjkchar` 單位能否處理假名／漢字。兩者都是數週等級工作量。
- VibeVoice-ASR 的 hotwords 是目前「大廠 + MIT + 內建 hotwords」最接近需求的，但 7–9B、PyTorch，**不符合 8–12 GB 與無 PyTorch 的限制**，只能當參考。

### Gaps
- 沒有找到任何「日語、Whisper zero-shot prompt 放專有名詞」的同儕審查定量研究；也沒有 turbo（4 層 decoder）對 prompt 敏感度的研究。
- KMP 論文的具體 WER 改善數字、延遲成本未取得。
- sherpa-onnx hotwords 是否可用於日語字元級 transducer（reazonspeech-k2-v2）沒有文件說明，需要實測。
- CT2 `return_logits_vocab` 回傳的是否為逐步完整詞表 log prob（若是，可做「事後 rescoring」型偏置）文件沒寫清楚，需讀 `WhisperGenerationResult` 原始碼確認。
- arXiv 2608.05759「How to Recognize New Words: A Comparison」（2026）可能直接比較各種新詞辨識方法，但作者機構未確認、本次未讀。

## Q2. 新的串流 ASR 模型／架構（大廠、非 PyTorch runtime、日語品質、授權）

### Takeaway
2025–2026 出現了多個**原生串流**模型，其中 **NVIDIA Nemotron 3.5 ASR Streaming（0.6B，含 ja-JP，商用授權）** 和 **Mistral Voxtral Realtime（4B，含日語，Apache-2.0）** 最相關，但兩者都**沒有官方 ONNX／CT2**；在 FLEURS 日語上，Nemotron 3.5 的 CER（11.5–13.9）明顯比 Whisper large-v3（4.9）差，Voxtral Realtime 只有在 ≥ 960 ms 延遲時才接近 Whisper 等級，而且要 16 GB VRAM。Kyutai STT 只有英／法；Moonshine 日語小模型很小但授權有非商用風險。**結論：短期內 Whisper + kikitori 的雙路徑仍是最佳選擇，新模型適合當「CPU／低延遲實驗後端」追蹤。**

### Cited Findings
- **NVIDIA nemotron-3.5-asr-streaming-0.6b（HF 發布 2026-06-04）[自報]**：0.6B，prompt-conditioned cache-aware FastConformer-RNNT（24 層 encoder），40 個 language-locale 單一 checkpoint，內建標點與大小寫；**ja-JP 在 19 個 transcription-ready locale 之內**。授權 **OpenMDW-1.1**，卡片寫「ready for commercial use」。延遲由 `att_context_size` 控制，chunk 可選 80 ms／160 ms／320 ms／560 ms／1.12 s。
  - 日語 FLEURS **CER**（指定語言）：13.87／12.90／12.22／11.91／11.48（80 ms → 1.12 s）；自動偵測：14.97／13.85／13.00／12.38／11.66。
  - 執行環境：NeMo、Transformers ≥ 5.13（皆 PyTorch），以及 **NeMo-Speech.cpp（原生 C++，q8_0 GGUF）**；卡片**沒有提 ONNX、TensorRT**；沒有 hotword 功能說明。
  - — [HF model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)
  - 對照：Whisper large-v3 FLEURS ja CER 4.9、turbo 4.7（OpenAI README／turbo 公告，見 `language_coverage.md` §1.2；normalizer 不同，只能粗略比較）。
- **Mistral Voxtral Mini 4B Realtime 2602（2026-02）**：約 4B（3.4B LM + 970M 音訊 encoder），Apache-2.0；13 種語言含 ja（en、zh、hi、es、ar、fr、pt、ru、de、ja、ko、it、nl）；延遲 80–1200 ms（80 ms 一級）加 2400 ms，官方建議 480 ms。
  - FLEURS 日語（卡片標「WER」，未說明日語是否以字元計）[自報]：160 ms 19.17、240 ms 15.17、**480 ms 9.59**、960 ms 6.80、2400 ms 5.50；離線閉源的 Voxtral Mini Transcribe 2.0 為 4.14。
  - 需要 ≥ 16 GB VRAM（BF16）；runtime：vLLM（建議）、Transformers ≥ 5.2、ExecuTorch（未測）；llama.cpp 尚未支援；沒有 ONNX。
  - — [HF model card](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)、[Mistral 公告](https://mistral.ai/news/voxtral-transcribe-2/)
  - 技術報告（arXiv 2602.11298，2026-02-11，v3 2026-04-06）[預印本]：基於 Delayed Streams Modeling，新的因果音訊 encoder＋Ada RMS-Norm 做延遲條件化，摘要稱 480 ms 時「on par with Whisper」 — [arXiv 2602.11298](https://arxiv.org/abs/2602.11298)
- **Kyutai STT（Delayed Streams Modeling）**：`kyutai/stt-1b-en_fr`（約 1B，英法，延遲 0.5 s，含 semantic VAD）、`kyutai/stt-2.6b-en`（英語，延遲 2.5 s）。Runtime：Rust server（正式用途）、MLX、PyTorch（研究用）。頁面沒寫權重授權 — [Kyutai STT](https://kyutai.org/stt)；方法論文 arXiv 2509.08753 [預印本] — [arXiv](https://arxiv.org/html/2509.08753v2)。**不支援日語**。
- **Moonshine 日語（Useful Sensors／Moonshine AI，2025-09）**：`moonshine-tiny-ja` 27M 參數；FLEURS ja CER 17.87、CV17 18.3；同表 Whisper tiny 47.2／96.11、Whisper medium 11.5／29.09 [自報]。HF 卡片授權欄只寫「other」 — [HF model card](https://huggingface.co/UsefulSensors/moonshine-tiny-ja)；論文「Flavors of Moonshine」（arXiv 2509.02523）[預印本] — [arXiv](https://arxiv.org/abs/2509.02523)
  - **授權警示**：官方 GitHub README 寫模型「MIT by default too, in every language and at every size」，**例外是「the legacy non-streaming models for languages other than English」，適用非商用的 Moonshine Community License** — [moonshine-ai/moonshine](https://github.com/moonshine-ai/moonshine)。`moonshine-tiny-ja` 很可能屬於這類舊版非串流模型（推論，LICENSE 清單未讀到）。
- **Moonshine v2（arXiv 2602.12241，2026-02-12，Kudlur、King、Wang、Warden）[預印本]**：encoder 改用 sliding-window attention、無位置編碼，使 time-to-first-token 不隨語句變長而增加；摘要稱達到 full-attention 的準確度而模型小 6 倍、更快。decoder 仍是自回歸 — [arXiv 2602.12241](https://arxiv.org/html/2602.12241)。語言覆蓋（是否有日語 v2 串流模型）未確認。
- **Qwen3-ASR 更新（2026-01-29）**：除了 0.6B／1.7B ASR（見 `asr_models.md`），同時釋出 **Qwen3-ForcedAligner-0.6B**，支援 11 種語言（含 ja），可對最長 5 分鐘語音預測任意單位的時間戳；推論框架支援 vLLM 批次、串流與時間戳。沒有找到官方 ONNX／CT2 — [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)、[HF Qwen3-ASR-0.6B](https://huggingface.co/Qwen/Qwen3-ASR-0.6B)
- **Alibaba Fun-ASR-Nano-2512（2025-12）**：官方卡片的任務欄寫支援中文、英文、日語；31 語言的是另一個 `Fun-ASR-MLT-Nano-2512`（同一張卡片內文也寫 31 語言，自相矛盾）。卡片授權 Apache-2.0；官方 repo 描述有 Transformers、FunASR、vLLM、**llama.cpp** 部署路徑 — [HF Fun-ASR-Nano-2512](https://huggingface.co/FunAudioLLM/Fun-ASR-Nano-2512)、[QwenAudio/Fun-ASR](https://github.com/QwenAudio/Fun-ASR)
- **Meta Omnilingual ASR（2025-11）**：Apache-2.0，1,600+ 語言（含 `jpn_Jpan`），CTC 系列 300M／1B／3B／7B 與 LLM-ASR 7B；Meta 稱 78% 的語言 CER < 10%。本次沒找到日語 CER — [facebookresearch/omnilingual-asr](https://github.com/facebookresearch/omnilingual-asr)（數字取自搜尋摘要，未逐字核對 repo）
- **OWLS（CMU 等，arXiv 2502.10373，2025）[預印本]**：最大 9B 的 Whisper 式開放模型，ReazonSpeech CER 7.3 vs Whisper 15.1 [自報] — [arXiv 2502.10373](https://arxiv.org/pdf/2502.10373)。ESPnet／PyTorch。

### Inferences
- **機會 4（中成本、CPU 使用者）：Nemotron 3.5 streaming 當「CPU 即時路徑」候選**。0.6B、cache-aware 原生串流（不必每 700 ms 重算整句）、商用授權、有 C++／GGUF runtime。但 FLEURS ja CER 約為 Whisper large-v3 的 2.3–2.8 倍，品質大概在 Whisper small 到 medium 之間（small FLEURS ja 12.0，large-v2 時代數字），只適合當 partial 路徑，final 仍交給 Whisper。要評估 NeMo-Speech.cpp 是否為 NVIDIA 官方專案、授權為何，以及 OpenMDW-1.1 全文（Gap）。
- **Voxtral Realtime**：日語 480 ms 9.59 已比 Nemotron 好，960 ms 6.80 接近 Whisper；但 16 GB VRAM（BF16）＋vLLM，超出 8–12 GB 且要和翻譯 LLM 共用 VRAM，**目前不可用**；若日後出現官方量化或非 PyTorch runtime 再評估。
- Kyutai STT：無日語，排除。Moonshine 日語：品質不足（CER 17.87）且授權疑似非商用，排除；Moonshine v2 的 sliding-window encoder 是值得追蹤的架構方向。
- Qwen3-ForcedAligner、Fun-ASR-Nano、Omnilingual、OWLS：都沒有官方非 PyTorch runtime（Fun-ASR 的 llama.cpp 路徑是例外，值得實測）。

### Gaps
- OpenMDW-1.1 授權全文、與 MIT 的相容性未確認；NeMo-Speech.cpp 的維護者與授權未確認。
- Voxtral 卡片日語欄標「WER」，是否實際以字元計算不明，和 Whisper CER 不能確定可比。
- Nemotron 3.5、Voxtral 都沒有在自發口語、BGM、動漫聲線上的日語數字。
- Fun-ASR-Nano 的日語 CER、Omnilingual 的日語 CER 未取得。Gemma 3n 音訊、Phi-4-multimodal 的日語 ASR 本次沒有查。
- Moonshine LICENSE 中哪些日語模型屬於「legacy」未逐字確認。

## Q3. Hallucination 偵測、信心估計、word-level timestamps

### Takeaway
2025–2026 的新證據有三點：(1) **Calm-Whisper（Interspeech 2025）** 證明 large-v3 decoder 中只有 3 個 attention head 造成大部分非語音 hallucination，只微調這 3 個 head 就能把 UrbanSound8K 上的 hallucination 率從 99.97% 降到 15.51%，LibriSpeech WER 幾乎不變——微調後的權重可以轉成 CT2，執行時不需 PyTorch；(2) AGH 2026 研究顯示 **Whisper 自帶的 avg_logprob／壓縮比／no_speech 組合偵測 hallucination 很弱（F1 23.6%）**，好的方法需要 decoder 內部狀態（CT2 不提供）；(3) CT2 本身就有 `align()` 可以做 word-level timestamps，kikitori 可以低成本取得逐字時間與機率。

### Cited Findings
- **Calm-Whisper（Wang, Alhmoud, Alsahly, Alqurishi（Elm Company, KSA）, Ravanelli（Concordia／Mila），Interspeech 2025，DOI 10.21437/Interspeech.2025-201）[同儕審查]**：
  - Whisper-large-v3 在 UrbanSound8K（8,732 段 ≤ 4 s 環境音）的 hallucination 率 **99.97%**（任何非空輸出都算），Conformer-CTC-large 只有 13.52%；55.2% 的音檔被轉成「 so」，8.5% 的幻覺超過 5 個 token。
  - 逐一遮蔽 decoder 的 20 個 self-attention head：遮 #0、#1、#3、#4、#6、#8、#11、#19 會降低 hallucination；#1 單獨就降 30% 以上。同時遮 #1、#6、#11 → 24.10%，但 LibriSpeech WER 2.12→3.57（clean）、4.07→5.98（other）。
  - Calm-down fine-tuning：凍結其他參數，只訓練各 decoder 層的 head #1、#6、#11，用 AudioSet（去掉人聲）、DEMAND、MUSAN 共 105 小時非語音配空白標籤；5 epoch 後 hallucination **15.51%**，WER 2.19／4.13（只增加 0.07／0.06）；超過 5 個 token 的幻覺從 742 降到 91。整個 decoder 一起微調則 WER 變 100%。
  - 文中引用：Silero VAD v5 在 ESC-50 上只有 61% 的語句級準確率，因此 VAD 無法完全擋掉噪音。
  - 只在英語（LibriSpeech）上評估 WER；論文沒有提到釋出權重。
  - — [ISCA archive PDF](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.pdf)
- **Jasiński, Barański 等（AGH Kraków），「From Text Metrics to Model Internals: A Study of Whisper ASR Hallucination Detection」（arXiv 2606.23060，2026-06-22）[預印本；所用的 HALAS 資料集論文列為 Interspeech 2026]**：
  - 資料：HALAS，Earnings-22 音訊上 7 個 ASR 模型的人工 hallucination 標註；Whisper large-v3 的 3,611 個預測中 858 個（23.8%）是 hallucination。
  - **Whisper 原生信心訊號（avg logprob、壓縮比、no_speech）＋ Logistic Regression：F1 只有 23.6%**。
  - 不需參考文字的文字特徵（XGBoost）：F1 37.7%；單一特徵中「每秒字元數」AUC 最高（68.2%）。
  - 探測 decoder 內部狀態：單層線性 probe AUC 約 81–82%；BLSTM 看整段解碼序列 AUC 87.0–87.6%，最佳 F1 65.5%（結論段寫 62.1%，論文內不一致）。
  - LLM 判斷：Gemini 3.0 Flash few-shot 在有參考文字時 F1 58.7%，沒有參考文字時 32.8%。
  - — [arXiv 2606.23060](https://arxiv.org/html/2606.23060)
- **「Adopting Whisper for Confidence Estimation」（Aggarwal 等，ICASSP 2025）[同儕審查]**：把 Whisper fine-tune 成「輸入音訊＋假設文字 → 每個字的信心分數」；Whisper-tiny 版和同等大小的傳統 CEM 在 in-domain 相當、8 個 out-of-domain 資料集上較好；Whisper-large 版在所有資料集都明顯勝出。摘要頁沒有具體數字，也沒說明是否釋出模型 — [arXiv 2502.13446](https://arxiv.org/abs/2502.13446)
- **CT2 word-level alignment**：`Whisper.align(features, start_sequence, text_tokens, num_frames, median_filter_width=7)` 回傳 `list[WhisperAlignmentResult]` — [CTranslate2 文件](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)。faster-whisper 的 `word_timestamps` 就是以 cross-attention pattern 加 DTW 取得逐字時間，每個字附 `probability`；`hallucination_silence_threshold`（需開 word_timestamps）會在偵測到可能的 hallucination 時跳過超過門檻秒數的靜音段 — [faster_whisper/transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- **溫度 fallback 的延遲尖峰（MLLP-VRAIN，IWSLT 2025）[同儕審查]**：Whisper 的 temperature fallback 會造成罕見但明顯的延遲尖峰，只有把計算時間算進延遲時才看得到 — [arXiv 2506.18828](https://arxiv.org/pdf/2506.18828)（ACL Anthology IWSLT 2025）
- **Qwen3-ForcedAligner-0.6B（2026-01）**：獨立的時間戳模型，11 語言含 ja — [QwenLM/Qwen3-ASR](https://github.com/QwenLM/Qwen3-ASR)

### Inferences
- **機會 5（中高成本、高影響）：自製「Calm」版 large-v3／turbo**。方法只動 3 個 head、105 小時公開非語音資料、學習率 1e-6，開發機上用 PyTorch 訓練一次，轉成 CT2 後執行期不需 PyTorch，符合限制。kikitori 已有 19 語言的非語音實測框架（`experiments/hallucination_lexicon/`），可直接衡量效果。風險：(a) 論文只驗證英語 WER，日語與其他 18 語言要自己測；(b) turbo 只有 4 層 decoder，「瘋狂 head」位置要重新找；(c) 衍生權重的授權沿用 OpenAI MIT，可散布但需由使用者下載。工作量約 1–2 週（含評測）。
- **機會 6（低成本）：加「每秒字元數」規則**。AGH 研究中，不需參考文字時最有用的單一特徵是 chars/sec（AUC 68.2%），而 kikitori 現有三條規則（no_speech／logprob／壓縮比）正是 F1 只有 23.6% 的那一組。kikitori 知道每句的 VAD 起訖時間，加一條「字元數 ÷ 語音秒數 超過語言別上限（或極低）就降級顯示」的規則幾乎零成本；門檻需用 vtuber_eval 校準。
- **機會 7（低到中成本）：用 CT2 `align()` 取得逐字時間與機率**，做三件事：(a) 字幕逐字淡入／卡拉 OK 式顯示；(b) 低機率字用不同樣式標示（「可能聽錯」），比整句丟棄更溫和；(c) 若整句的字都擠在音訊最後一小段或落在 VAD 判為無聲的區間，視為 hallucination（faster-whisper `hallucination_silence_threshold` 的同一思路）。額外成本是每句多一次 decoder forced pass（比 beam search 便宜），沒有找到量測數字。
- **機會 8（低成本）**：final 路徑的溫度 fallback (0.0, 0.2, 0.4) 依 MLLP-VRAIN 的發現會造成延遲尖峰；可在 GPU 共用壓力大時改成只在壓縮比觸發時才 fallback，或限制最多一次。
- 「Adopting Whisper for Confidence Estimation」需要第二個 fine-tune 模型跑一次，能轉 CT2（Whisper 架構），但模型未確認釋出，要自己訓練，優先度低。

### Gaps
- Calm-Whisper 權重是否公開、在多語言或 turbo 上的效果，未找到。
- CT2 `WhisperAlignmentResult` 的欄位（是否含逐字機率）和 `align()` 的延遲，文件沒寫，需讀原始碼或實測。
- HALAS 只有英語（Earnings-22），chars/sec 對日語（假名／漢字密度差異大）的有效性未知。
- 「Identifying and Calibrating Overconfidence in Noisy Speech Recognition」（arXiv 2509.07195，摘要稱超過 11% 的錯誤 token 帶有高信心）作者機構未確認，未列為證據。
- 2026 activation steering 降低非語音 hallucination 的論文（既有筆記提過）仍未讀原文。

## Q4. Code-switching（日語夾英語）、快語速、高音角色聲線、笑聲、歌回

### Takeaway
這一塊**同儕審查證據最少**：沒有找到任何日英 code-switching 的 Whisper 評測，也沒有高音／角色聲線、笑聲的專門研究。歌回方面有兩個可用方向：**用 singing-robust 的語音活動偵測把歌唱段落擋在 ASR 之前**（WASPAA 2025），以及**人聲分離（Demucs）後再辨識歌詞**（IEEE ICME 2025 workshop，WER 下降但 hallucination 可能增加）。

### Cited Findings
- **Code-switching**：
  - Interspeech 2025 的 CS-ASR 基準（Nguyen & Tran）只涵蓋馬來語–英語、華語–馬來語、泰米爾語–英語，測 Whisper、MMS、SeamlessM4T [同儕審查] — [ISCA archive](https://www.isca-archive.org/interspeech_2025/nguyen25_interspeech.html)
  - Interspeech 2025 Yang et al.：Mandarin–English 用 soft prompt tuning 改善 Whisper（需訓練） — [ISCA archive](https://www.isca-archive.org/interspeech_2025/yang25p_interspeech.html)
  - OWLS（arXiv 2502.10373）[預印本] 建了含日語的合成 code-switching 評測（FLEURS 句子中把部分詞換成英語，由雙語者朗讀），但本次沒找到 Whisper 在該集合上的數字 — [arXiv 2502.10373](https://arxiv.org/pdf/2502.10373)
  - VibeVoice-ASR 宣稱「seamless codeswitching」，沒有日語數字 [自報] — [Microsoft Foundry blog](https://techcommunity.microsoft.com/t5/microsoft-foundry-blog/introducing-vibevoice-asr-longform-structured-speech-recognition/ba-p/4501276)
- **歌唱 vs 語音偵測：SR-SAD（Grundhuber 等，Fraunhofer IIS，IEEE WASPAA 2025）[同儕審查]**：一般語音活動偵測常把歌唱判成語音；SR-SAD 用控制語音／歌唱比例的訓練策略與輕量模型，在多曲風測試集上「偵測語音並排除歌唱」的 AUC 0.919 — [arXiv 2512.09713](https://arxiv.org/abs/2512.09713)、[Fraunhofer publica](https://publica.fraunhofer.de/entities/publication/28a7b324-1c32-4f73-9b02-ee0553faa21d)
- **人聲分離 + Whisper 歌詞辨識（Syed, Meresman Higgs, Cífka, Sandler，IEEE ICME 2025 Workshop，DOI 10.1109/ICMEW68306.2025.11152264）[同儕審查 workshop]**：用 Hybrid Demucs 分離人聲後，Whisper 在短篇與長篇歌詞轉寫的 WER 都下降（不需 fine-tune）；長篇時把分離結果當 vocal activity detector 切段；但分離**可能增加 hallucination**，且 Whisper 仍會漏掉非詞彙發聲（la-la 等）與和聲；同時釋出 MUSDB-ALT 資料集 — [arXiv 2506.15514](https://arxiv.org/abs/2506.15514)
- Calm-Whisper 文中：Silero VAD v5 在 ESC-50 只有 61% 語句級準確率（見 Q3） — [ISCA PDF](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.pdf)
- 既有筆記：Qwen3-ASR 在 M4Singer 歌聲上明顯優於 Whisper-large-v3（13.58 vs 5.98，非日語）；SenseVoice 可輸出 BGM／Laughter 事件標籤 — 見 `asr_models.md` Q2、Q3。

### Inferences
- **機會 9（中成本、高影響於 BGM／歌回）：「歌唱閘門」模式**。歌回時 Whisper 對歌聲的輸出大多是錯字或歌詞幻覺。可加一個 singing-vs-speech 分類器在 Silero VAD 之後：判為歌唱時改顯示「♪」或暫停字幕（或交給使用者選「歌詞模式」）。SR-SAD 是目前找到最對題的同儕審查方法，但權重／授權未確認；替代方案是 SenseVoice 的 BGM 事件（自訂授權，有風險）或自己用 kikitori 現有評測資料訓練輕量分類器（ONNX 匯出）。
- **機會 10（高成本、可選）：歌詞模式用人聲分離**。Demucs 分離能降低 Whisper 歌詞 WER，但即時性差（Demucs 需要較長視窗）、可能增加 hallucination，且 Demucs 官方是 PyTorch。只適合作為「歌回模式」的實驗功能，不適合預設。
- Code-switching：Whisper 在 `language=ja` 下常把英語詞寫成片假名，這對日語字幕通常可接受；沒有證據支持改動，建議只在 vtuber_eval 加入含英語詞的片段做監測。
- 快語速、高音聲線、笑聲：沒有可信的新研究，維持既有筆記的結論（以 kikitori 自己的 VTuber 評測集為準）。

### Gaps
- 沒有任何日英 code-switching 的 Whisper／串流模型評測（同儕審查或大廠）。
- 沒有高音／角色聲線、笑聲對 ASR 影響的 2025–2026 同儕審查研究。
- SR-SAD 是否公開權重、授權、延遲數字未確認；是否在日語歌曲上評測不明。
- Demucs 的官方 ONNX 匯出、即時延遲數字未查。

## Q5. Whisper 類模型的低延遲技巧（local agreement、attention-guided、speculative decoding、蒸餾／壓縮）

### Takeaway
在「固定 chunk 串流」設定下，**attention-guided（AlignAtt／Simul-Whisper）明顯優於 LocalAgreement**：1 s chunk 時 large-v2 的 LibriSpeech test-clean WER 3.89 vs 7.91（非串流 3.79）。但 AlignAtt 需要逐步解碼時讀 cross-attention，CT2 不提供。kikitori 可以低成本借用的是 **LocalAgreement 的「穩定前綴」顯示**，以及用 CT2 `align()` 近似 AlignAtt 的「尾端不確定字」判斷。Speculative decoding 在 CT2 上不可用；LiteASR 類的 encoder 壓縮與 CT2 轉換相容性未確認。

### Cited Findings
- **Whisper-Streaming／LocalAgreement（Macháček, Dabre, Bojar，IJCNLP-AACL 2023 demo）[同儕審查，背景]**：用 LocalAgreement 策略（連續兩次解碼一致的前綴才確認輸出）把離線 Whisper 變成即時系統，在未切段的長音訊上延遲約 3.3 s — [arXiv 2307.14743](https://arxiv.org/abs/2307.14743v2)
- **Simul-Whisper（Wang et al.，Interspeech 2024，pp. 4483–4487，DOI 10.21437/Interspeech.2024-1814）[同儕審查]**：用 cross-attention 的時間對齊決定何時停止解碼（不 fine-tune Whisper），另訓練一個 integrate-and-fire 截斷偵測模型（TDM，LibriSpeech 100 h、RTX 3090 訓練）處理 chunk 邊界被切斷的字。1 s chunk 的 LibriSpeech WER：
  - large-v2：非串流 3.79／7.38（test-clean／test-other）；LocalAgreement 7.91／11.47；Simul-Whisper(TDM) 3.89／8.89
  - medium：LocalAgreement 14.61／16.24；Simul-Whisper(TDM) 3.90／8.12
  - small：LocalAgreement 9.29／14.20；Simul-Whisper(TDM) 5.15／11.39
  - 延遲大約是 chunk 長度的 1–2 倍；LocalAgreement 的延遲較不可預測且計算量大。只評英語與 MLS 7 種歐洲語言，**沒有日語、中文**。
  - — [arXiv 2406.10052](https://arxiv.org/html/2406.10052)
- **SimulStreaming（CUNI，IWSLT 2025，ACL Anthology 2025.iwslt-1.41）[同儕審查]**：結合 Simul-Whisper 的 AlignAtt 與 Whisper-Streaming 介面，支援 large-v3、beam search、initial prompt 與上下文；Silero VAD 切段、累積到 `MinChunkSize` 才解碼。英語 ASR（ACL6060 dev）WER 從 14.22%（0.05 s chunk，延遲 494 ms）到 12.69%（2.0 s chunk，延遲 2,143 ms）；Cs→En 翻譯中 beam 5 比 greedy ChrF +1.04 且延遲沒有增加（2,285 vs 2,373 ms SLAAL）。論文說 AlignAtt 比每次從頭重算的 LocalAgreement 有效率，但沒有給倍數（「約快 5 倍」只出現在 repo 維護者的說法） — [arXiv 2506.17077](https://arxiv.org/html/2506.17077)、[ACL Anthology PDF](https://aclanthology.org/2025.iwslt-1.41.pdf)、[ufal/whisper_streaming issue #172](https://github.com/ufal/whisper_streaming/issues/172)
- **NAIST IWSLT 2025 系統 [同儕審查]**：指出 cross-attention 型決策策略（EDAtt、AlignAtt）在某些設定下有困難，他們的 StreamST 方法表現較好 — [ACL Anthology 2025.iwslt-1.39](https://aclanthology.org/2025.iwslt-1.39.pdf)
- **Distil-Whisper 當 speculative decoding 助手（Hugging Face，arXiv 2311.00430）[預印本]**：推論速度約 2 倍、主模型輸出不變、參數只多 8%；需 batch size 1 — [arXiv 2311.00430](https://arxiv.org/pdf/2311.00430)、[HF blog](https://huggingface.co/blog/asr-diarization)
- **LiteASR（Kamahori 等，EMNLP 2025）[同儕審查]**：對 Whisper encoder 做低秩近似；proceedings 摘要稱 large-v3 encoder 縮小 50% 以上、達 medium 大小但準確度更好；arXiv v2 寫約縮小 40%、執行約快 1.4 倍、準確度損失可忽略（兩版本數字不同） — [ACL Anthology 2025.emnlp-main.169](https://preview.aclanthology.org/setup/2025.emnlp-main.169)、[arXiv 2502.20583](https://arxiv.org/html/2502.20583v2)
- **原生串流模型的延遲–準確度曲線**（見 Q2）：Nemotron 3.5 ja CER 80 ms 13.87 → 1.12 s 11.48；Voxtral Realtime ja 160 ms 19.17 → 480 ms 9.59 → 2400 ms 5.50 [自報]。
- **MLLP-VRAIN（IWSLT 2025）**：溫度 fallback 造成延遲尖峰（見 Q3）。

### Inferences
- kikitori 目前的 partial 路徑是「每 700 ms 對整句重新 greedy 解碼、整行替換」，屬於沒有 agreement 的重算策略：計算量隨句長增加，字幕會閃動改字。
- **機會 11（低成本、體感影響大）：LocalAgreement-2 穩定前綴**。保留上一次 partial，和這一次取最長共同前綴，前綴用正常樣式、其餘用淡色顯示「暫定」；翻譯端只送穩定前綴（可減少翻譯 LLM 的重算）。純 UI／字串邏輯，不動模型，約 1–2 天。證據是 Whisper-Streaming 的方法本身；Simul-Whisper 表格也顯示 LocalAgreement 的品質代價主要在「固定 chunk 下強制確認」，kikitori 只用它做顯示，final 仍重新解碼，不會受影響（推論）。
- **機會 12（中成本）：用 CT2 `align()` 近似 AlignAtt 的「尾端截斷」判斷**。AlignAtt 的核心是「對齊到音訊最尾端的 token 不可靠」。CT2 不給逐步 cross-attention，但可以在 partial 解碼後呼叫 `align()` 取得每個 token 的對齊位置，把落在最後約 0.3–0.5 s 的 token 標成暫定或不顯示。這是推論出來的近似，沒有文獻直接驗證；成本是每次 partial 多一次 alignment pass。
- **機會 13（不建議）**：speculative decoding 需要 assisted generation，CT2 Whisper 不支援；kikitori 的 final 已經是 large-v3 單句，GPU 延遲 0.27–0.41 s（`asr.md`），收益有限。
- **機會 14（追蹤）**：LiteASR 若能轉 CT2（低秩層需要 converter 支援，未確認），可降低 large-v3 encoder 的 VRAM 與延遲，對「turbo + large-v3 + 翻譯 LLM 共用 8 GB」最有價值。
- 真正的 AlignAtt／cache-aware 串流需要換解碼後端（自寫 ONNX Whisper 解碼迴圈或改用 Nemotron／sherpa-onnx），應和 Q1 機會 3 一起評估，因為兩者需要相同的「逐步解碼掛勾」。

### Gaps
- Simul-Whisper／SimulStreaming 沒有日語結果；日語無空白分詞，AlignAtt 的 token 粒度效果未知。
- SimulStreaming repo 的程式授權論文沒寫；若要參考程式碼需先確認（直接依論文重寫演算法可避開）。
- CT2 `align()` 的實際延遲、能否在 partial 路徑 700 ms 預算內完成，未量測。
- LiteASR 權重與 CT2 converter 相容性未確認；兩個版本的壓縮率數字不一致。

## Q6. 綜合：改進機會總表與排除來源

### Takeaway
最值得先做的是**不換模型、只動 kikitori 自己程式**的四項：LocalAgreement 穩定前綴（#11）、chars/sec 幻覺規則（#6）、glossary 少量詞進 ASR prompt（#1）、CT2 `align()` 逐字時間／信心顯示（#7）。中期最有價值的是**自製 Calm-Whisper 權重（#5）**與**歌唱閘門（#9）**。換串流模型（Nemotron 3.5／Voxtral Realtime）目前在日語品質或硬體上都不划算。

### Cited Findings
- 各項證據見 Q1–Q5 的 Cited Findings；本節只做彙整，不引入新事實。

### Inferences

| # | 機會 | 證據（等級、日期） | 符合限制？ | 授權 | 成本 | 預期影響 |
|---|---|---|---|---|---|---|
| 1 | glossary 少量詞（≤ 20）放進 final 路徑 prompt，並過濾「只輸出 prompt 詞」的幻覺 | CB-Whisper／KG-Whisper（Interspeech 2024，同儕審查）間接；Hou 2025 dilution；無日語數字 | 是（現有 prompt 機制） | — | 低（1 天＋評測） | 中：專有名詞召回；有新型幻覺風險 |
| 2 | ASR 後詞典正規化（誤認 → glossary 正式寫法） | 推論；sherpa 文件顯示 hotwords 有副作用 | 是 | — | 低 | 中，且可控 |
| 3 | 真 shallow-fusion biasing（KMP／trie） | Google KMP（Interspeech 2024，同儕審查） | 需換解碼後端（CT2 無 logits 掛勾）或改 sherpa transducer | 方法無授權問題 | 高（數週） | 中高，但日語未驗證 |
| 4 | Nemotron 3.5 streaming 當 CPU partial 路徑 | NVIDIA 自報 2026-06：FLEURS ja CER 11.5–13.9 | 部分：NeMo-Speech.cpp（C++／GGUF）可能可用；無官方 ONNX | OpenMDW-1.1（宣稱可商用，全文待查） | 中高（新後端） | 低到中：品質不如 Whisper |
| 5 | 自製 Calm-Whisper（只微調 3 個 decoder head）→ 轉 CT2 | Calm-Whisper（Interspeech 2025，同儕審查）：99.97% → 15.51%，WER +0.07 | 是（訓練在開發機，執行用 CT2） | 衍生自 OpenAI MIT 權重 | 中高（1–2 週） | 高：非語音幻覺，需驗證多語言 |
| 6 | chars/sec 異常規則 | AGH 2026（預印本）：最佳無參考單一特徵 AUC 68.2%；原生三訊號 F1 只有 23.6% | 是 | — | 低 | 中（補現有規則的盲點） |
| 7 | CT2 `align()`：逐字時間、低信心字標示、靜音段幻覺偵測 | CT2 官方 API；faster-whisper `hallucination_silence_threshold` | 是 | MIT | 低到中 | 中：顯示品質＋過濾 |
| 8 | 限制 final 溫度 fallback | MLLP-VRAIN（IWSLT 2025，同儕審查） | 是 | — | 低 | 低到中：減少延遲尖峰 |
| 9 | 歌唱閘門（singing vs speech） | SR-SAD（WASPAA 2025，同儕審查）AUC 0.919 | 需 ONNX 分類器；SR-SAD 權重未確認 | 未確認 | 中 | 高（歌回、BGM） |
| 10 | 歌詞模式：Demucs 人聲分離再辨識 | Syed et al.（IEEE ICME 2025 workshop）：WER 降、幻覺可能增 | Demucs 官方是 PyTorch，ONNX 未確認 | Demucs MIT（未在本次逐字核對） | 高 | 中（實驗功能） |
| 11 | LocalAgreement-2 穩定前綴顯示，翻譯只送穩定部分 | Whisper-Streaming（AACL 2023，同儕審查）；Simul-Whisper 對照 | 是 | — | 低（1–2 天） | 高（字幕閃動、翻譯重算） |
| 12 | 用 `align()` 近似 AlignAtt，尾端 token 標暫定 | Simul-Whisper（Interspeech 2024）、SimulStreaming（IWSLT 2025）的原理；近似法為推論 | 是 | — | 中 | 中 |
| 13 | Speculative decoding | Distil-Whisper（預印本）約 2× | 否（CT2 不支援 assisted generation） | — | — | 不建議 |
| 14 | LiteASR 低秩 encoder | EMNLP 2025（同儕審查）：約 40–50% encoder 縮小、約 1.4× | 未確認 CT2 相容 | 待查 | 中高 | 中（VRAM 緊時） |
| 15 | Voxtral Realtime 4B | Mistral 自報 2026-02：ja 480 ms 9.59、960 ms 6.80 | 否（16 GB VRAM、vLLM／Transformers） | Apache-2.0 | — | 追蹤 |

### Gaps
- 以上所有「影響」都沒有 VTuber 直播日語的實測，應以 `experiments/vtuber_eval` 逐項 A/B。

#### 排除來源（只在非合格來源出現，不作為證據）

| 內容 | 來源 | 排除原因 |
|---|---|---|
| WhisperLiveKit（SimulStreaming／whisper_streaming 的整合即時框架） | [GitHub QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | 個人專案 |
| Nemotron 3.5 streaming 的 ONNX 匯出（宣稱 CPU 3.8× 即時、WER 差 0.0137） | [GitHub codavidgarcia](https://github.com/codavidgarcia/nemotron-3.5-asr-streaming-onnx) | 個人匯出 |
| moonshine-tiny-ja ONNX／GGUF 轉檔與其授權說法 | [wmoto-ai](https://huggingface.co/wmoto-ai/moonshine-tiny-ja-ONNX)、[tryambientai](https://huggingface.co/tryambientai/moonshine-tiny-ja-gguf) | 個人轉檔 |
| Moonshine v2 Tiny／Small／Medium 的參數與 M3 延遲（34M／123M／245M；50／148／258 ms） | [themoonlight.io 評論](https://www.themoonlight.io/review/moonshine-v2-ergodic-streaming-encoder-asr-for-latency-critical-speech-applications) | 第三方摘要網站，未與論文核對 |
| Voxtral vs Whisper 比較、Voxtral 價格解讀 | [weesperneonflow.ai](https://weesperneonflow.ai/en/blog/2026-03-31-voxtral-whisper-open-source-speech-models-comparison-2026/)、[MarkTechPost](https://www.marktechpost.com/2026/02/04/mistral-ai-launches-voxtral-transcribe-2-pairing-batch-diarization-and-open-realtime-asr-for-multilingual-production-workloads-at-scale/)、gend.co、screenapp.io | 小公司部落格／媒體 |
| Nemotron 3.5 說明文 | [mindstudio.ai](https://www.mindstudio.ai/blog/what-is-nvidia-nemotron-3-5-asr) | 小公司部落格 |
| Omnilingual ASR 各尺寸 VRAM（約 2／3／8／15 GiB） | mintlify 自動產生的 repo 文件、soniqo.audio | 非官方 |
| Moonshine 非英語模型非商用的說法 | ai.miraheze.org wiki | 次級來源（已改用官方 GitHub README） |
| VibeVoice 介紹文 | DEV Community、apidog、howaiworks、tosea | 個人／小公司部落格 |
| Whisper Turbo 在馬來–英 code-switching 22.04% → 58.67% | Deepgram 部落格轉述 | 二手轉述，未讀原論文 |
| Demucs → faster-whisper 前處理提案 | GitHub captionforge issue | 個人專案 |
| Token-map drafting speculative decoding（v3＋turbo 草稿只快 1.02×，新方法 1.27×） | arXiv 2507.21522 | 作者機構未確認的 arXiv |
| Qwen3-ASR／VibeVoice 的第三方 fine-tune 與 ONNX 版 | HF 個人 repo（Prince-1、jaykwok 等） | 個人上傳 |
