# 先穩住譯文，再處理歌回與名字

**結論：** 依 2025–2026 年主流來源，kikitori 下一版最值得做的事情可以排成三層。第一層是把已經在原文做好的「穩定前綴」**延伸到譯文本身**。IWSLT 2026 同步翻譯賽道第一名的 NVIDIA NeMo，做法就是串流 ASR 加 Qwen3.5 加 LCP 前綴政策。kikitori 目前只控制「何時重翻」，不控制「譯文哪一段已經穩定」。另外再補上 Google CHI 2023（N=123）證實有效的「不讓中段改字造成換行跳動、新字淡入」。第二層是處理名字與術語：讓使用者填「本場直播資訊」，抽出實體注入提示詞。IWSLT 2026 的 Extra Context 子賽道裡幾乎所有系統都因此受益，UPV 平均 +2.75 COMET。WMT25 術語任務也證實「正確術語一定有幫助」。第三層是歌回：ADR-0003 卡住的「分類器授權」現在有解，Google YAMNet 是 Apache-2.0；但上一份報告推薦的 CED 程式碼其實是 **GPL-3.0**，要排除。真正的瓶頸仍是沒有歌回評測資料。口譯模式則缺「誰說的」標示與「請再說一次」這兩個低成本功能；譯文語音朗讀（TTS）在大廠產品裡已經普及，但要新依賴，需要先問使用者。另一方面，有幾件事證據明確指向不做：在 Whisper 前面加語音增強、改走 NPU、換成新的串流 ASR 模型、在字幕視窗上用顏色標示 ASR 信心。最後必須說清楚：**IWSLT 2024–2026 沒有任何日文來源的同步翻譯評測，也沒有公開的 VTuber 語料**。所以下面每一項的效益幅度，都要在 `experiments/vtuber_eval`、`experiments/translate_eval` 自己量過才算數。

> **證據標記**（調查日期 2026-11-08）：
>
> - 【同儕審查】：清單內的期刊或會議論文，結果仍是作者自報，除非另外註明。
> - 【官方 findings（獨立）】：IWSLT／WMT 主辦方的比較，屬於獨立評測；系統描述是參賽者自述。
> - 【公司官方，未經同行審查】：知名公司的 arXiv、技術報告或模型卡，數字都是自報。
> - 【官方文件】：產品說明與 release notes，只用來證明「某功能存在」，不證明有效。
> - 【證據不足】：只有不合格來源支持，需要自行實測；這類來源列在附錄。
>
> 工作量：S＝1–2 天，M＝3–10 天，L＝2 週以上。「現況」對照 `README.zh-TW.md` 與 `docs/specs/`。

## 這些已經做到，不再列為建議

kikitori 0.2.0 已經涵蓋不少研究與產品規範的要求，所以下表這些項目不會再出現在建議裡。最容易誤判的是穩定化：引擎在**原文**上做 LocalAgreement-2，算出 `stable_chars`；即時翻譯只在穩定前綴變長時才重送，但重送的是「穩定前綴＋尾巴」的整句原文，回來的**譯文仍可能整行改寫**，而 UI 對整句即時譯文一律淡色（`pipeline-engine.md`「穩定前綴」、`ui.md`「字幕視窗」）。所以「譯文自己的穩定前綴」是新功能，不是重複。

| 已有功能 | 對應的研究或規範 | 位置 |
|---|---|---|
| 原文穩定前綴正常顯示、尾巴淡色；穩定部分變長才重送即時翻譯 | Google 穩定化顯示的第 (3) 種呈現 | `competitive-features.md` F2 |
| 設定頁字幕外觀**即時預覽**；字級 14–96 px（超過 XAG 要求的 200%）；背景不透明度 0–100% | XAG 104 | `ui.md` 設定表、`settings.previewTranslation` |
| `idle_clear_secs=0` 永遠顯示（相當於 Apple「保持在畫面上」）；15 秒沒有聲音時提醒 | Apple Live Captions、Live Transcribe | `ui.md`「閱讀節奏」「延遲、落後與音量」 |
| 匯出完整逐字稿、OBS 文字檔 | XAG「字幕保留在 UGC、提供逐字稿」 | F1、F6 |
| 「自動偵測」模式下，每句在候選語言內做語言偵測 | Meet／Azure 的自動偵測 | `asr.md`「候選語言」 |
| 翻譯的 system prompt 固定放在最前面，前文、術語、原文放在 user 訊息 | llama.cpp prompt cache 能命中的排列 | `translate.md` |
| 日語幻覺片語表、turbo 的 no_speech 不可用時的處理、前文保險 | Barański ICASSP 2025 的黑名單方法 | `asr.md`、`translate.md` |
| CTranslate2 已固定在 4.8.5，已包含 v4.8.0 的 Intel MKL PACKED_GEMM | CT2 release notes | `pyproject.toml` |

## 譯文穩定化：IWSLT 2026 冠軍做的事，kikitori 只做了一半

即時翻譯字幕的主流做法，已經從「訓練一個同步翻譯模型」轉成「現成 LLM 加不需訓練的前綴政策」。IWSLT 2026 同步賽道中，**除了一隊之外全部是 cascade**。第一名 NeMo（NVIDIA）用 Parakeet ASR、Qwen 3.5 與 LCP 政策，在所有語向都落在 Pareto 前緣；PINCH-AST 用 **Qwen3.5-4B** 加「字元級、CJK 感知的 LCP re-translation 政策」，四個語向都有參賽。主辦方的結論還指出：延遲拉高不一定換來成比例的品質提升【官方 findings（獨立）】（[IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)）。這組設定和 kikitori 的「Whisper＋本機 Qwen3.5」幾乎同構。差別在於它們對**譯文**取前綴，kikitori 只對原文取。需要 SFT 的同步方法，例如交錯 read/write token 的 Fu et al.（[Findings of ACL 2025](https://aclanthology.org/2025.findings-acl.1045/)）和預測未來詞的 TAF（[NAACL 2025](https://aclanthology.org/2025.naacl-long.286/)），都沒辦法直接套在使用者下載的 GGUF 上，所以不列入。

UI 層的證據來自 Google 在 CHI 2023 Extended Abstracts 發表的群眾外包研究。**N=123**，每人看 20 段 YouTube 片段，比較四種呈現。結果是「穩定化＋平滑動畫（新 token 淡入）」在 6 個題項中有 **5 項顯著較佳**（p<0.01）：較不分心、較不疲勞、較好讀；單純的「信心門檻」不是最佳策略。它的關鍵規則是：**中段的新增與刪除，只有不打亂既有換行時才套用**，刻意以穩定優先於正確【同儕審查，Google 自評】（[Google Research](https://research.google/blog/modeling-and-improving-text-stability-in-live-captions)）。kikitori 有穩定前綴，但沒有這條換行保護，也沒有淡入：目前唯一的動畫是譯文轉成字幕黃的 220 ms。re-translation 本身的不穩定性，可以用推論端的啟發式（mask-k、biased beam search）降低最多約 100 倍【同儕審查，Google】（[Arivazhagan et al., ICASSP 2020](https://arxiv.org/pdf/1912.03393)）。這支持「譯文前綴預填」的方向。不過 Ollama 官方文件沒有說明是否支援用 assistant 訊息當前綴續寫（[Ollama OpenAI 相容性](https://docs.ollama.com/api/openai-compatibility)），所以要先實測。

句尾判斷是同一條延遲鏈的另一端。kikitori 目前只有 `PauseEndpoint`，但 `request_end()` 介面已經留好（`vad-segmenter.md`）。Semantic VAD 在串流 ASR 上用句尾標點取代長靜音，平均延遲降低 **53.3%**、CER 沒有明顯變差；但資料是內部資料【同儕審查，自報】（[Interspeech 2023](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.html)）。kikitori 不需要新模型：即時路徑 turbo 輸出的「。？！」本身就是句尾訊號。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 授權 | 工作量／效益 |
|---|---|---|---|---|---|---|
| S1 | **譯文字元級 LCP**：連續兩次即時譯文取最長共同前綴（中日以字元、英文以詞計），前綴正常顯示、尾巴淡色，沿用 `stability.py` 與 `stable.ts`；同時在 experiments 量 normalized erasure | IWSLT 2026 findings：NeMo 第一（LCP）、PINCH-AST（Qwen3.5-4B＋CJK 字元 LCP）【官方 findings（獨立）；語向是 en→X，沒有日文】 | 部分：原文有、譯文沒有 | 是，純邏輯，不多呼叫 LLM | — | S–M／高 |
| S2 | **避免換行跳動＋新字淡入**：即時字幕期間，已穩定部分的行數只增不減；尾巴更新若會改變換行就延後到完整字幕才套用；新字 150–250 ms 淡入，遵守 `prefers-reduced-motion` | Google CHI EA 2023，N=123，5/6 題項顯著【同儕審查】 | 沒有 | 是，純前端 | — | S／中高 |
| S3 | **即時翻譯只送穩定前綴**（尾巴不翻、只顯示原文），以「少翻一點」換「少改一點」；做成可切換的實驗 | IWSLT 2025 給 en→ja 的延遲區間（0–3.5／3.5–5 s）比 en→de 寬約 1.5 s，是主辦方的設計（[IWSLT 2025 Findings](https://aclanthology.org/2025.iwslt-1.44/)）；用在 ja→X 是推論 | 沒有（現在送整句） | 是 | — | S／中，需做 UX 測試 |
| S4 | **穩定譯文當 assistant 前綴續寫**（只用在即時路徑，完整路徑照舊整句重翻） | Arivazhagan ICASSP 2020；IWSLT 2026 的 CPII-HK 用 response prefilling【官方 findings】 | 沒有 | 未知：Ollama 文件沒寫，要先測 | — | S（測試）／中 |
| S5 | **文字句尾訊號**：即時結果以句尾標點結束、且已靜音一段短時間時，呼叫 `request_end()` 提早切句；保留原本的停頓與最長長度安全網 | Semantic VAD（Interspeech 2023，延遲 −53.3%，內部資料）；Google unified endpointing（中位延遲 −30.8%，[arXiv 2211.00786](https://arxiv.org/abs/2211.00786)）【同儕審查，自報；都不是日文直播】 | 沒有（只有停頓規則） | 是，不需新模型 | — | M／中，需實測誤切率 |

## 名字與術語：外部資訊有獨立證據，自動建表要讓使用者把關

VTuber 直播最常翻錯的是人名、組合名和遊戲名，而 2026 年最直接的新證據正好對準這件事。IWSLT 2026 的 Extra Context 子賽道給系統「這場演講的論文 PDF」。3 隊參賽，「almost all submissions show a positive impact」，**UPV 在三個語向、兩種延遲區間平均 +2.75 COMET**，增益集中在專有實體。CUHKSZ 在低延遲時只從文件抽出 named entities 注入 system prompt。主辦方也警告：幅度高度取決於整合方式。NeMo 的 en→zh 帶了 context 後，延遲飆到 **10.86 s**【官方 findings（獨立）】（[IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)）。WMT25 術語任務用「無術語／正確術語／隨機術語」做因果比較，13 隊、20 個系統。結果是**加入任何術語都提升整體品質，對頂尖系統來說正確術語比隨機術語更有用**，而且一般翻譯能力和術語準確率幾乎沒有取捨。Track 2 是英文與繁體中文互譯（4 隊）【官方 findings（獨立）】（[WMT25 Terminology Findings](https://aclanthology.org/2025.wmt-1.30/)）。WMT25 沒有測「錯的術語」，所以自動抽出的詞條不能無聲套用。

線上逐句建立專有名詞紀錄的同儕審查方法是 DelTA。它的四層記憶包含 Proper Noun Records，作者自報一致性最多 +4.58 個百分點、COMET 平均最多 +3.16。但每句都要多呼叫 LLM，而且骨幹模型大小本次沒有核對【同儕審查，自報】（[ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/285149554f6fbed6e2f9ec72d131bd23-Abstract-Conference.html)）。前文的形式也有證據可循：Sony 在 en→ja 等方向（GPT-4o、Gemma 2-27B，k=5）發現，所有 context 策略都優於不給；「前文原文＋模型自己前幾句的譯文」不需要參考譯文，就能改善敬語、代名詞與詞彙一致性【同儕審查，自報】（[Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1324.pdf)）。kikitori 目前的前文只放原文。

ASR 端的選擇比較受限。CTranslate2 的 `Whisper.generate()` **沒有 logits processor 或逐步 callback**（[CT2 文件](https://opennmt.net/CTranslate2/python/ctranslate2.models.Whisper.html)）。所以 Google 那種不需訓練、在 beam search 中依偏置詞比對前進就加分的 KMP shallow fusion（[Interspeech 2024](https://arxiv.org/pdf/2310.00178)），在 kikitori 上做不到，除非換解碼後端。能做的只有把少量詞放進 prompt。CB-Whisper 顯示「prompt 裡放**正確且相關**的實體」有幫助，但它的實體是用 KWS 偵測出來的（[Interspeech 2024](https://arxiv.org/abs/2309.09552)）；詞表超過約 1,000 詞會被稀釋（[Hou et al., Interspeech 2025](https://www.isca-archive.org/interspeech_2025/hou25_interspeech.pdf)）。兩者都沒有日文數字。

翻譯的低信心旗標目前只能用 LLM 自己的 logprob。COMET 家族的權重是 CC BY-NC-SA（[XCOMET-XL](https://huggingface.co/Unbabel/XCOMET-XL)），MetricX-25 改用 Gemma 3 12B 當骨幹【公司官方，未經同行審查】（[arXiv 2510.24707](https://arxiv.org/abs/2510.24707)），兩者都不適用。比較 12 個翻譯方向、14 種詞級 QE 指標的研究認為，無監督的不確定性訊號有「untapped potential」（[Sarti et al., EMNLP 2025](https://aclanthology.org/2025.emnlp-main.924/)）。但 WMT25 評測 findings 也提醒，segment 級品質預測仍然困難（[WMT25 Metrics Findings](https://aclanthology.org/2025.wmt-1.24/)）。Ollama v0.12.11 的 release note 說 OpenAI 相容 API 支援 logprobs（[v0.12.11](https://github.com/ollama/ollama/releases/tag/v0.12.11)），文件頁卻標未支援，兩份官方來源互相矛盾。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 授權 | 工作量／效益 |
|---|---|---|---|---|---|---|
| N1 | **「本場直播資訊」欄位**：開始前填主播名、遊戲名、連動成員，或貼上直播說明欄；用 SudachiDict 的固有名詞詞性加片假名與英數規則抽出實體，當成本場術語注入（沿用每句最多 8 條的格式） | IWSLT 2026 Extra Context：UPV +2.75 COMET，3 隊【官方 findings（獨立）】；說明欄資訊密度比論文 PDF 低很多，幅度未知 | 沒有（只有固定術語表） | 是，SudachiDict 已有（需先下載振假名辭典） | Apache-2.0（Sudachi） | M／中 |
| N2 | **字幕紀錄一鍵加入術語表**：選取原文片段 →「加入術語表」預填原文，使用者只填譯文；另加「原文校正」詞條（ASR 常見誤認 → 正式寫法），純字串替換 | 非專業者即時協作修正的負擔很重（[CHI EA 2025](https://arxiv.org/pdf/2503.15120)，使用者研究＋DHH 焦點團體）；校正表是推論 | 部分（有術語表，沒有從紀錄頁加入，也沒有原文校正） | 是 | — | S–M／中 |
| N3 | **session 自動詞表建議**：完整字幕後抽出重複出現的固有名詞，取第一次的譯法；顯示成「建議詞條」由使用者採用；優先序低於使用者詞條 | WMT25 Terminology【官方 findings】；DelTA（ICLR 2025，+4.58pp，自報） | 沒有 | 是；在閒置時呼叫 LLM | — | M／中；屬於新的使用者可見行為，要先寫 spec |
| N4 | **前文加上模型自己的前幾句譯文**（src＋hyp），A/B 量 COMET 與前文保險的觸發次數 | Sony，Findings of EMNLP 2025【同儕審查，自報；en→ja，不是 ja→X】 | 部分（只有原文前文） | 是 | — | S／中，可能增加前文外洩 |
| N5 | **術語表少量詞（≤20）放進完整路徑的 ASR prompt**，並加一條規則：輸出只等於 prompt 中的詞就丟棄 | CB-Whisper（Interspeech 2024，間接）、Hou（Interspeech 2025，稀釋）【同儕審查，沒有日文】 | 沒有（ASR prompt 只放前文原文） | 是 | — | S／中；新型幻覺的風險要實測 |
| N6 | **logprob 低信心旗標**：完整翻譯加 logprobs，低於門檻就在**字幕紀錄頁**標示，或改用另一組 prompt 重翻 | Sarti EMNLP 2025；WMT25 Metrics Findings【同儕審查／官方 findings】；Ollama 文件互相矛盾 | 沒有 | 未知，要先測 | — | S（測試）／低到中；對流暢的錯譯可能沒用 |
| N7 | 真正的 shallow-fusion 偏置（KMP／trie） | Google，Interspeech 2024【同儕審查】 | 沒有 | 否：CT2 沒有逐步解碼的掛勾 | — | L／中高；**不建議近期** |

## 歌回與 BGM：授權卡關已解，評測資料才是真瓶頸

ADR-0003 的第一個重新評估條件是「授權可商用、有 ONNX 權重的歌聲／音樂分類器」，現在可以部分達成。Google YAMNet 位於 tensorflow/models 的 `/research`，該目錄依 LICENSE 是 **Apache-2.0**。它有 3.7M 權重、521 類（含 Speech 與 Singing），首個輸出需要 ≥975 ms 音訊（[YAMNet README](https://github.com/tensorflow/models/blob/master/research/audioset/yamnet/README.md)、[LICENSE](https://raw.githubusercontent.com/tensorflow/models/master/LICENSE)）。EfficientAT 與 PANNs 的程式碼是 MIT（[EfficientAT](https://api.github.com/repos/fschmid56/EfficientAT/license)、[PANNs](https://api.github.com/repos/qiuqiangkong/audioset_tagging_cnn/license)）。上一份報告推薦的 CED 要撤回：它的程式碼授權是 **GPL-3.0**，README 沒寫權重授權（[RicherMans/CED](https://github.com/RicherMans/CED)）。這些模型都沒有官方 ONNX，要在建置時自行匯出，執行時仍然只用 onnxruntime。為什麼不能只靠 VAD：Fraunhofer 的 SR-SAD 證實，只用語音訓練的活動偵測器會把多數歌聲當成語音。SR-SAD 在多曲風測試集上「偵測語音、排除歌唱」的 AUC 是 0.919，但權重與授權未確認【同儕審查】（[WASPAA 2025](https://arxiv.org/abs/2512.09713)）。產品端的做法也一致：Pixel 11 的媒體翻譯「doesn't translate music」（[Pixel 說明](https://support.google.com/pixelphone/answer/11209263?hl=en)）；YouTube 自動配音會先過濾音樂影片（[YouTube 說明](https://support.google.com/youtube/answer/15569972?hl=en)）；Windows 即時字幕明說不偵測掌聲與音樂（[Microsoft Support](https://support.microsoft.com/en-us/windows/use-live-captions-to-better-understand-audio-b52da59c-14b8-4031-aeeb-f6a47e6055df)）；Android Expressive Captions 則把聲音標籤做成子開關（[Google](https://blog.google/products-and-platforms/platforms/android/google-android-expressive-captions/)）【官方文件】。ADR-0003 的第二個條件「有逐段標註的歌回音軌」仍然沒有達成，誤判會把說話吞掉。所以這一項放在「需先問使用者」：要新增建置期的匯出工具，也要先自建評測資料。

非語音幻覺的最強新證據是 Calm-Whisper。large-v3 在 UrbanSound8K（**8,732 段**環境音）上，只要有輸出就算幻覺，幻覺率 **99.97%**。只微調 decoder 的 3 個 attention head，用 105 小時公開的非語音資料訓練後，幻覺率降到 **15.51%**，LibriSpeech WER 只增加 0.07／0.06；整個 decoder 一起微調則 WER 變成 100%【同儕審查，自報；只驗證英語 WER，沒有釋出權重】（[Interspeech 2025](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.pdf)）。訓練可以在開發機上用 PyTorch 做，再轉成 CT2，執行時不需要 PyTorch。但 turbo 只有 4 層 decoder，「出問題的 head」位置要重新找；權重也要另外找地方發布。VAD 仍是第一道防線：AGH 讓 large-v3 跑 301,317 段非語音（刻意排除音樂），Silero 閘控把 WER 從 104.8 壓到 8.0；**1 s 片段的幻覺率 52.1%**，是高風險區【同儕審查】（[ICASSP 2025](https://arxiv.org/html/2501.11378v1)）。

前置語音增強可以確定不做。URGENT 2025 挑戰有 32 隊參賽，最佳系統只把下游 ASR 的字元正確率從 73.41% 提到 79.80%。純生成式 SE 在訓練時沒看過的**日語上只有 36.8%**，還會「幻覺出說話內容」；而且失真類型不包含競爭語音，音樂只是背景噪音的一部分【同儕審查，主辦方評測】（[Interspeech 2025](https://arxiv.org/html/2505.23212)）。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 授權 | 工作量／效益 |
|---|---|---|---|---|---|---|
| A1 | **歌唱閘門＋非語音標籤**：每個 VAD 片段跑 YAMNet／EfficientAT（CPU），Singing／Music 對 Speech 的分數比連續 N 段偏高時進入歌回狀態：顯示「♪」、原文淡色、不送翻譯；說話時立即退出；同一個分類器可以輸出（笑）、（掌聲）等子開關標籤，OBS 與匯出檔也帶上 | SR-SAD（WASPAA 2025）【同儕審查】；Pixel、YouTube、Windows、Android【官方文件】；分類器在 VTuber BGM 上的誤判率**沒有公開數據** | 沒有（ADR-0003 暫緩） | 是：建置期自行匯出 ONNX，執行時用 onnxruntime | YAMNet Apache-2.0；EfficientAT／PANNs 程式碼 MIT（權重檔授權待逐檔確認）；AudioSet 音訊的版權是共同灰色地帶 | M（另需歌回評測資料）／高 |
| A2 | **自訓 Calm 版 large-v3**（只訓練 3 個 head）→ 轉 CT2，用 `experiments/hallucination_lexicon` 的 19 語非語音框架與 vtuber_eval 驗證 | Calm-Whisper，Interspeech 2025：99.97% → 15.51%【同儕審查，只有英語】 | 沒有 | 是：訓練在開發機，執行時用 CT2 | 衍生自 OpenAI MIT 權重；訓練資料 AudioSet、DEMAND、MUSAN 的授權要確認 | L／高；權重要另外發布 |
| A3 | **每秒字元數異常規則**：字元數 ÷ VAD 語音秒數超過語言別上下限就降級顯示 | 【證據不足】只有 AGH HALAS 預印本（見附錄） | 沒有 | 是 | — | S／未知，需自測 |
| A4 | **用 CT2 `align()` 做靜音段幻覺檢查**：整句的字都擠在音訊尾端或落在 VAD 判為無聲的區間時丟棄 | CT2 官方 API 存在；faster-whisper 的 `hallucination_silence_threshold` 是同一思路（[transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)），但沒有準確度數字【證據不足】 | 沒有 | 是 | MIT | S–M／未知 |
| A5 | **限制完整路徑的溫度 fallback**（例如只在壓縮比觸發時才重試，最多一次），先從 log 量 P95／P99 完整字幕延遲 | 【證據不足】「fallback 造成延遲尖峰」只出現在 IWSLT 系統論文（附錄） | 部分（完整路徑 0.0／0.2／0.4） | 是 | — | S／低到中 |
| A6 | 前置語音增強或音源分離 | URGENT 2025【同儕審查】：生成式 SE 日語 36.8% | — | — | — | **不建議** |
| A7 | 歌詞模式：人聲分離後再辨識 | 唯一證據是 ICME workshop（附錄） | — | Demucs 官方是 PyTorch | — | **不建議** |

## 口譯模式：讓雙方看得到「誰說的、機器聽成什麼」

一對一口譯的可用性比字幕情境低得多。Ouvia 收集了超過 **1,750 次**互動（英→葡，醫療與日常場景，4 個語音翻譯系統），**只有約一半被評為可用**，不同人口群體之間有顯著差距；以問答為基礎的評估，比一般品質指標更能預測可用性【同儕審查（已接受），自評】（[EMNLP 2026](https://arxiv.org/abs/2606.06177)）。Microsoft Research 讓 23 組（46 人）用語音翻譯原型說故事，觀察到雙方會自然調整說法來補償翻譯的不足【同儕審查，背景】（[CHI 2015](https://www.microsoft.com/en-us/research/publication/effect-of-machine-translation-in-interlingual-conversation-lessons-from-a-formative-study/)）。這兩項都支持兩件事：讓說話者看到「自己被辨識成什麼」，並提供低成本的修復手段。群體對話方面，n=263 的調查顯示「看不出誰在說」是字幕最大的困難【同儕審查】（[SpeechCompass, CHI 2025](https://arxiv.org/abs/2502.08848)）。XAG 規定換說話者時要標名字，顏色不能是唯一線索（[XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)）。kikitori 在口譯模式下本來就知道每句屬於「我」還是「對方」，因為語言方向決定了它，所以標示幾乎免費。

語音輸出是 2025–2026 大廠最一致的方向。Meet、Gemini Translate、Pixel、YouTube、Apple 電話與 AirPods、Teams Interpreter、Azure Live Interpreter 都輸出語音（例如 [Apple Newsroom](https://www.apple.com/newsroom/2025/06/apple-intelligence-gets-even-more-powerful-with-new-capabilities-across-apple-devices/)、[Teams Interpreter](https://learn.microsoft.com/en-us/microsoftteams/interpreter-agent-teams)）。Google Meet 團隊說 **2–3 秒是延遲的「sweet spot」**（[Google Blog](https://blog.google/products-and-platforms/products/workspace/google-meet-langauge-translation-ai/)）；Microsoft 員工重視「調整譯音與原聲的音量比例」（[Inside Track](https://www.microsoft.com/insidetrack/blog/deploying-our-new-game-changing-interpreter-agent-in-our-meetings-at-microsoft/)）【官方文件，沒有樣本數】。這些都是對話與會議情境，所以 TTS 只應在口譯模式提高優先。本機 TTS 模型（Kokoro、Piper 等）的授權與日文、繁中品質，本次沒有用官方 model card 查證，因此列為「需先問使用者」。Smart Turn 換手模型的準確度只有廠商部落格與競品評測支持，依來源政策屬於證據不足。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 授權 | 工作量／效益 |
|---|---|---|---|---|---|---|
| I1 | **說話者文字標示＋顏色**（「你：」「對方：」），同一人連續說話不重複；OBS 與匯出檔也帶上 | SpeechCompass CHI 2025（n=263／494 調查、8 人評估）【同儕審查】；XAG 104、Netflix 規範【官方文件】 | 沒有 | 是 | — | S／中 |
| I2 | **「請再說一次」快速鍵**：在對方的字幕位置顯示預先翻好的固定提示，不需要即時翻譯 | Ouvia EMNLP 2026（>1,750 次互動，約半數不可用）；Hara & Iqbal CHI 2015（23 組）【同儕審查】；提示本身的效益是推論 | 沒有 | 是 | — | S／中 |
| I3 | **譯文語音朗讀**：只唸完整字幕，可調音量與原聲比例，TTS 跑在 CPU，以「說完到聽到」≤ 2–3 s 為驗收上限 | 7 個大廠產品；Meet 2–3 s；Teams 音量比例【官方文件，沒有樣本數】 | 沒有 | 未確認：sherpa-onnx（Apache-2.0）可以跑 ONNX TTS，但模型授權與日文、繁中品質未查證 | 待查 | M／中（口譯），影片模式低 |
| I4 | Smart Turn 當提早切句的加速訊號 | 【證據不足】只有 Daily 部落格與 LiveKit eot-bench（附錄） | 沒有（VAD 報告已建議） | 是（ONNX、BSD-2） | BSD-2 | M／未知，要用自錄的面對面語料驗證 |

## 閱讀與控制：官方規範給得出數字，使用者研究給不出

字幕排版的數字幾乎都來自官方規範，而不是實驗。Netflix 日文規範是：每秒最多 4 字、每行 13 個全形字、最多 2 行、每則至少 500 ms；**振假名只標該詞第一次出現**，兒童內容才每次都標（[Netflix Japanese Timed Text Style Guide](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)）。XAG 104 建議每行 ≤40 字元（[XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)）。kikitori 目前是固定的 `min_display_ms`、依視窗寬度自動換行、每個漢字都標讀音、原文字級固定是譯文的 ×0.62。雙語版面有一筆眼動證據：112 位中國英語學習者在四種條件下，**雙語字幕對理解的效果與只看母語字幕相同**，兩者都優於只看外語字幕或無字幕；學習者大部分時間讀母語那行（[Studies in Second Language Acquisition 2023](https://cambridge.org/core/journals/studies-in-second-language-acquisition/article/examining-the-effectiveness-of-bilingual-subtitles-for-comprehension-an-eyetracking-study/D3E6D4CDE5C5023CE085E95CA4D18ACE)）。同一團隊的字彙研究則發現，雙語字幕在「字形辨識」上不如只看外語字幕（[Language Learning 2022](https://discovery.ucl.ac.uk/10144681/7/Pellicer%20Sanchez_Incidental%20Vocabulary%20Learning%20from%20Bilingual%20Subtitled%20Viewing-%20An%20Eye-Tracking%20Study_AOP.pdf)）。這兩本是語言習得領域的主流期刊，不在指定清單內，但屬於主流。它們支持為日語學習者提供「原文為主」的版面：原文字級放大、譯文縮小。

有兩件事研究明確反對。第一是在字幕上顯示 ASR 信心：DHH 使用者雖然口頭上想知道，實際上覺得分心，偏好熟悉、不標信心的樣式（[Berke et al., ASSETS 2017](https://doi.org/10.1145/3132525.3132541)，樣本數未能核對）；Google Live Transcribe 也因為「分心、沒有提供對話價值」拿掉了信心著色（[Google Research 2019](https://research.google/blog/real-time-continuous-transcription-with-live-transcribe/)）。kikitori 的淡色代表「穩定性」，不是「信心」，維持現狀即可。第二是預設開啟情緒樣式：情緒字幕能傳達情緒，但易讀性中度下降（[Pataca et al., CHI 2023](https://digitalcommons.njit.edu/fac_pubs/1776)，8 人訪談＋16 人評估）；39 位 DHH 參與者的偏好因人而異，結論是提供自訂（[Caption Royale, CHI 2024](https://par.nsf.gov/biblio/10521039)）。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 工作量／效益 |
|---|---|---|---|---|---|
| U1 | 最短顯示改成 `max(min_display_ms, 字數 ÷ cps)`，cps 依字幕語言而定，仍保留「等待不得累積成延遲」 | Netflix 日文 4 cps【官方文件】；繁中、簡中的 cps 未查證 | 部分（固定值） | 是 | S／中 |
| U2 | 「最大行寬（em）」設定，讓長句換成兩行，而不是一條橫跨螢幕的長行 | Netflix 13 全形字、XAG ≤40 字元【官方文件】 | 沒有 | 是 | S／中 |
| U3 | 振假名選項：「全部／本場第一次出現」 | Netflix 規範【官方文件】；主流 venue 沒有振假名實驗 | 部分（全部） | 是 | S／低到中 |
| U4 | 「日語學習」版面：原文／譯文字級比例可調、可選原文在上或在下 | SSLA 2023（N=112）、Language Learning 2022 | 沒有（固定 ×0.62） | 是 | S／中（學習者） |
| U5 | 「暫停字幕」快速鍵（凍結畫面，引擎照常寫入字幕紀錄）、「重設字幕視窗位置」按鈕、按住快速鍵暫時展開最近 10 句 | Apple Live Captions 有暫停與恢復預設位置；Android 雙擊從 2 行展開到 12 行【官方文件，只證明功能存在】 | 沒有 | 是 | 各 S／中低（重設位置能解決視窗卡在拔掉的螢幕外） |
| U6 | 「BGM 太大聲」提示：分開回報 VAD 判為語音與非語音時的音量 | Live Transcribe 的雙圈噪音指示器【官方文件，背景】 | 部分（有音量表，沒有分開） | 是 | S／低到中 |
| U7 | 字幕視窗顯示 ASR 信心著色 | Berke ASSETS 2017、Google 2019 | — | — | **不建議** |
| U8 | 音量強度或情緒樣式（粗體或放大表示大喊） | Pataca CHI 2023、Caption Royale CHI 2024 | 沒有 | 是 | S／低；只做成預設關閉的選項 |

## 硬體與擷取：AMD 顯示卡是最便宜的新客群，NPU 還不是

CTranslate2 從 **v4.7.0 起正式支援 AMD GPU（ROCm HIP）**；v4.8.3–v4.8.5 修了 Windows ROCm 的卡死與 DLL 載入問題，並補上 Windows 建置指南。但 ROCm wheel 不在 PyPI，要從 GitHub Releases 取得（[CT2 Releases](https://github.com/OpenNMT/CTranslate2/releases)、[PyPI](https://pypi.org/project/ctranslate2/)）【官方文件】。kikitori 已經釘在 4.8.5，模型格式與量化選項原則上可以沿用。缺的是 AMD GPU 偵測、ROCm wheel 與 runtime 的下載流程，以及一台 AMD 實機。翻譯端的推薦也偏保守。Ollama 在安裝 Vulkan 後端時**預設開啟 Vulkan**，Windows 的 ROCm 支援卡是 RX 7000 與 W7000（[Ollama GPU](https://docs.ollama.com/gpu)），而 kikitori 目前把所有非 NVIDIA 都當 CPU，一律推薦 qwen3.5:4b。NPU 則還不適合。Qualcomm 官方的 large-v3-turbo 在 X Elite 上光 encoder 就要 **約 560 ms**，輸入固定 30 秒（[Qualcomm AI Hub](https://huggingface.co/qualcomm/Whisper-Large-V3-Turbo)）；AMD 明說「Whisper large is not supported at this time」（[AMD](https://www.amd.com/en/developer/resources/technical-articles/2025/unlocking-on-device-asr-with-whisper-on-ryzen-ai-npus.html)）；Windows ML 的可下載 EP 需要 Windows 11 24H2，DirectML 已列為 legacy（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers)）。

單一 App 擷取的官方能力比 ADR-0002 寫的時候清楚。Windows 的 process loopback 可以包含或排除指定行程樹，擷取不綁定輸出裝置，但**最低需要 Windows 10 build 20348**，實務上等於只有 Windows 11（[Microsoft Learn](https://learn.microsoft.com/en-us/windows/win32/api/audioclientactivationparams/ne-audioclientactivationparams-audioclient_activation_type)、[官方範例](https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/)）。「排除」模式剛好能在連動時排除自己的 Discord。macOS 的 ScreenCaptureKit「audio capture can only be filtered at an application level」，改用包含指定 App 的 `SCContentFilter` 就能只抓瀏覽器（[WWDC22](https://developer.apple.com/videos/play/wwdc2022/10156/)、[SCContentFilter](https://developer.apple.com/documentation/screencapturekit/sccontentfilter)）。

說話者分離與新模型目前都還不成熟。NVIDIA 的 Streaming Sortformer（[Interspeech 2025](https://arxiv.org/abs/2507.18446)）最多 4 人，主要以英文訓練（[model card](https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2)）。後繼的 Nemotron 3 Diarization 最多 8 人，OpenMDW-1.1 授權可商用，但**沒有官方 ONNX、明列語言不含日語**，評測全是會議與電話（[model card](https://huggingface.co/nvidia/Nemotron-3-Diarization)）【公司官方，自報】。不需要 PyTorch 的路線是 sherpa-onnx（Apache-2.0）加 pyannote segmentation 與 3D-Speaker 嵌入（[sherpa-onnx](https://csukuangfj.github.io/sherpa/onnx/speaker-diarization/models.html)），但那是「片段後聚類」，不是串流；DiariZen 的權重是 NC，要排除（[README](https://raw.githubusercontent.com/BUTSpeechFIT/DiariZen/main/README.md)）。新的串流 ASR 在日語上也打不過 Whisper：Nemotron 3.5 ASR Streaming 的 FLEURS ja CER 是 **11.5–13.9**（[model card](https://huggingface.co/nvidia/nemotron-3.5-asr-streaming-0.6b)），Whisper large-v3 自報 4.9（[openai/whisper README](https://github.com/openai/whisper/blob/main/README.md)）；Voxtral Realtime 要 ≥16 GB VRAM（[model card](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)）【公司官方，自報；normalizer 不同，只能粗略比較】。翻譯模型方面，**Gemma 4 E4B 已改成 Apache-2.0**，IWSLT 2026 也有隊伍拿它做同步翻譯（[model card](https://huggingface.co/google/gemma-4-E4B-it)、[IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)）；但它沒有任何 ja→zh 的翻譯分數，音訊輸入在 llama.cpp 仍標示「highly experimental」（[llama.cpp](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)）。

| # | 做什麼 | 證據 | 現況 | 限制內可行 | 授權 | 工作量／效益 |
|---|---|---|---|---|---|---|
| H1 | **AMD GPU 支援**：偵測 AMD GPU 與 VRAM，提供 ROCm 版 CT2 的一鍵下載；先在實機確認 HIP 版的 `device="cuda"` 對應 | CT2 v4.7.0–4.8.5 release notes【官方文件，沒有 Whisper 速度數字】 | 沒有（只偵測 NVIDIA） | 是；需要 AMD 實機 | MIT；ROCm runtime 的散布方式待查 | M／中高（新客群） |
| H2 | **非 NVIDIA 的翻譯推薦**：偵測到 RX 7000 以上或 Linux ROCm 清單中的卡時，不再把翻譯當 CPU | Ollama GPU 文件【官方文件】；tokens/s 沒有官方數字 | 沒有 | 是；需實機量測 | — | S（量完後）／中 |
| H3 | **macOS 只擷取單一 App**：改用 `SCContentFilter(display:including:…)`，加一個 App 下拉選單 | WWDC22、Apple 文件【官方文件】 | 沒有（ADR-0002） | 是；需要 Mac 實機 | 系統 API | S／中 |
| H4 | **Windows 11 只擷取或排除單一 App**（process loopback），Windows 10 隱藏這個選項 | Microsoft Learn 與官方 C++ 範例【官方文件】 | 沒有（ADR-0002） | 是；Python 端要寫 COM 非同步啟用（ctypes） | 系統 API | L／高；要修改 ADR-0002 |
| H5 | 片段級說話者色塊（sherpa-onnx＋3D-Speaker 嵌入，CPU 線上聚類） | sherpa-onnx 官方範例；沒有任何 BGM、角色聲線或日語評測 | 沒有 | 是，但要新依賴 | Apache-2.0（權重檔待逐檔確認） | M／中；同一人換聲線可能被拆成兩人 |
| H6 | 評測 **Gemma 4 E4B** 當翻譯模型（溫度 0 與官方的 1.0 都測） | 授權與參數來自模型卡；IWSLT 2026 en→zh 結果「mixed」 | 沒有 | 是（Ollama） | Apache-2.0 | S／未知 |
| H7 | NPU 路線、whisper.cpp 第二後端 | Qualcomm encoder 約 560 ms、固定 30 s；AMD 不支援 large | — | 要多維護多套模型格式 | — | **不建議近期** |
| H8 | 換成 Nemotron 3.5／Voxtral Realtime 串流 ASR、端到端語音翻譯（Seamless、Hibiki-Zero） | 日語 CER 明顯較差、VRAM 超出、Seamless 為 cc-by-nc-4.0（[model card](https://huggingface.co/facebook/seamless-m4t-v2-large)）、Hibiki-Zero 為 CC BY-NC-SA 且不支援日語（[ICML 2026](https://icml.cc/virtual/2026/poster/66094)） | — | 否 | 部分 NC | **不建議** |
| H9 | LiteASR 低秩 encoder（large-v3 encoder 縮小 40–50%，兩個版本的數字不一致） | [EMNLP 2025](https://preview.aclanthology.org/setup/2025.emnlp-main.169)【同儕審查，自報】 | 沒有 | CT2 能否轉換未確認 | 待查 | 追蹤 |

## 路線圖：依效益／成本排序

排序原則是：先做不需新依賴、可以寫純函式測試的項目，而且每一項都先在 experiments 建立基準（normalized erasure、COMET、片段級幻覺率、P95 延遲），再改程式。依 CLAUDE.md，跨模組或使用者可見的新行為（N1、N3、A1、I3、H4）開工前要先寫 spec。

**近期可做**（沒有新依賴，證據合格或屬於純產品細節）：

| 順位 | 項目 | 理由 |
|---|---|---|
| 1 | S1 譯文字元級 LCP＋normalized erasure 量測 | IWSLT 2026 前段系統的共同作法；UI 的淡色尾巴已經有，邏輯可以重用 `stability.py` |
| 2 | S2 避免換行跳動＋新字淡入 | N=123 的同儕審查證據；純前端 |
| 3 | I1 口譯說話者標示、I2「請再說一次」 | 資訊本來就有，S 級成本 |
| 4 | N2 字幕紀錄一鍵加入術語表＋原文校正 | 把術語表從「事前準備」變成「邊看邊累積」；不碰模型 |
| 5 | N1 本場直播資訊欄 | IWSLT 2026 的獨立證據；SudachiDict 已有 |
| 6 | U5 重設視窗位置、暫停、暫時展開歷史 | 各 S；重設位置能修掉「視窗卡在拔掉的螢幕外」的實際風險 |
| 7 | U1、U2、U3、U4 閱讀速度、行寬、振假名首次出現、學習版面 | 官方規範給得出數字，各 S |
| 8 | U6 BGM 音量提示 | S，價值中低 |

**需先實測**（先在 experiments 做 A/B，數字過關才上線）：

| 順位 | 項目 | 主要要量的東西 |
|---|---|---|
| 1 | S3 只翻穩定前綴；S4 assistant prefill | erasure、首個譯文延遲、Ollama 是否真的續寫 |
| 2 | N4 前文加入自己的譯文 | COMET、前文保險的觸發率 |
| 3 | N5 ≤20 詞的 ASR prompt | 專有名詞召回、非語音時是否把 prompt 吐出來 |
| 4 | S5 文字句尾訊號提早切句 | 完整字幕延遲與誤切率 |
| 5 | H6 Gemma 4 E4B 翻譯評測 | 陷阱句、translate_eval |
| 6 | A3、A4、A5 每秒字元數、align 靜音檢查、溫度 fallback 上限 | 都是【證據不足】，成本 S；以片段級幻覺率與 P99 延遲決定 |
| 7 | N6 logprob 旗標 | Ollama 是否回傳；logprob 與陷阱句錯誤的相關性 |
| 8 | N3 session 自動詞表建議 | 先寫 spec；抽錯率 |
| 9 | H1、H2、H3 AMD 與 macOS | 需要實機，沒有實機就不做 |
| 10 | A2 自訓 Calm-Whisper | L 級；先確認訓練資料授權與權重的發布方式 |

**需先問使用者**（新依賴、新的建置工具、新的模型散布，或要推翻 ADR）：

| 順位 | 項目 | 要決定的事 |
|---|---|---|
| 1 | A1 歌唱閘門＋非語音標籤 | 建置期 ONNX 匯出工具（TF 或 PyTorch，只在開發環境）、權重授權逐檔確認、自建歌回評測音軌；推翻 ADR-0003 |
| 2 | H4 Windows process loopback | 推翻 ADR-0002、只支援 Windows 11 |
| 3 | I3 口譯模式 TTS | 新增 TTS runtime 與模型、授權查證 |
| 4 | H5 片段級說話者色塊 | 新增 sherpa-onnx 依賴 |
| 5 | kotoba-whisper-v2.0-faster 日語選項（上一份報告） | ReazonSpeech 的資料使用條款 |
| — | 手機或第二螢幕顯示字幕 | 和 ADR-0004「不開網路埠」衝突，維持不做，除非使用者改變方向 |

**不建議**：N7 KMP shallow fusion（CT2 沒有解碼掛勾）、A6 前置語音增強、A7 Demucs 歌詞模式、U7 字幕上的信心著色、H7 NPU 與 whisper.cpp 第二後端、H8 換串流 ASR 或端到端模型。另外還有：需要 SFT 的同步翻譯（Fu et al.、TAF）、LoRA 微調（Ollama Modelfile 文件找不到 ADAPTER 指令，見 [Ollama Modelfile](https://docs.ollama.com/modelfile)）、學習型 QE（COMET 權重 NC、MetricX-25 骨幹 12B）、保留說話者聲音的語音翻譯，以及情緒標籤。

## Conclusion

這次調查把 kikitori 的下一步從「換更好的模型」拉回到「把已經有的東西接完整」。IWSLT 2026 的冠軍組合，和 kikitori 的 Whisper＋Qwen3.5 幾乎一樣；差距在於前綴政策只做了原文那一半，以及沒有把直播的外部資訊交給翻譯器。這兩件事都不需要新依賴，而且第一次有了獨立評測支持。反過來，2025–2026 年最熱門的新東西，像是原生串流 ASR、端到端語音翻譯、NPU，在日語品質、授權或 8–12 GB 預算上都還過不了 kikitori 的限制。

更深一層的啟示是：證據的瓶頸已經從「方法」移到「資料」。歌回閘門的授權問題解掉了，卻卡在沒有歌回標註；名字注入有 IWSLT 的增益數字，但那是論文 PDF，不是直播說明欄；整個 IWSLT 2024–2026 都沒有日文來源的同步翻譯。因此，在 experiments 裡持續擴充的 VTuber 評測集，加上 normalized erasure、片段級幻覺率與 P95 延遲這三把尺，才是讓上面每一項建議從「推論」變成「已驗證」的前提。它也應該和 S1 一起排進下一版。

## 附錄：依來源政策排除、不當證據的來源

| 來源 | 排除原因 | 若可用，它會說什麼 | 受影響的建議 |
|---|---|---|---|
| Jasiński、Barański 等（AGH），HALAS hallucination 偵測（[arXiv 2606.23060](https://arxiv.org/html/2606.23060)） | 非知名機構的預印本 | Whisper 原生三訊號（avg_logprob、壓縮比、no_speech）偵測幻覺的 F1 只有 23.6%；不需參考文字時，最佳單一特徵是每秒字元數（AUC 68.2%） | A3 證據不足 |
| Macháček et al., Whisper-Streaming（IJCNLP-AACL 2023 demo） | AACL 不在清單內 | LocalAgreement 讓離線 Whisper 即時化，延遲約 3.3 s | 無（kikitori 已實作 LA-2） |
| CUNI SimulStreaming、MLLP-VRAIN、NAIST（IWSLT 2025 系統論文） | IWSLT 只採 findings | AlignAtt 比 LocalAgreement 有效率；溫度 fallback 造成罕見的延遲尖峰 | A5 證據不足 |
| Javorský、Macháček、Bojar（WMT 2022 研究論文，N=14） | WMT 只採 findings；樣本小 | 閃爍對理解的影響小於翻譯品質；懂來源語的人偏好低延遲 | S3 只靠推論 |
| IWSLT 2026 系統論文：AlignAtt4LLM、Redefining SimulMT、Diet-KIT、Qwen3-Omni test-time adaptation；IWSLT 2025 Conversational SimulMT | 不是 findings | 輸出只往後長的 LLM AlignAtt；Qwen2-Audio-7B 壓到 3.98 GB；多輪對話重用 KV cache 快約 2 倍 | 無 |
| Syed et al.（IEEE ICME 2025 Workshop） | 不在清單內的 workshop | Demucs 分離後 Whisper 歌詞 WER 下降，但可能增加幻覺 | A7 不建議 |
| WhisperKit（Argmax，ICML 2025 Workshop） | 小眾 workshop、小公司 | ANE 上 turbo 壓到 0.6 GB，日語維持在 1% WER 以內 | 無 |
| Smart Turn（Daily 部落格）、LiveKit eot-bench、LiveKit turn detector | 廠商自評或競品評測 | Smart Turn 日文 96.76%；5% 誤切所需延遲從 1.6 s 降到約 1.05 s | I4 證據不足 |
| JAL-Turn（arXiv 2603.26515） | 機構未確認的預印本 | 重用 ASR encoder 的換手預測，含日語客服資料 | 無 |
| UvA-MT（WMT25 參賽論文）、Kim et al.（WMT24）、MT Summit 2025、JaSocial（LREC 2026）、JP-TL-Bench（arXiv）、Doppelganger-JC（AACL 2025）、EACL 2026 test-time scaling | 不在清單內或只有 findings 才算 | uncertainty 會高估模型自己的輸出；LLM 敬語受限；中日同形詞的 shortcut | N6 只靠 Sarti 與 WMT findings |
| Aljedaani 2024、Al Amin（W4A 2023）、Frontiers 2025、EvolveCaptions、Xiao et al.、AR 字幕 arXiv、LiveSubtitling 投影片、Verbit、W3C wiki、JoSTrans 2020、日本語教育學會誌、JALT、EDULEARN26 | venue 不在清單內、無法確認，或屬二手資料 | 遊戲玩家偏好半透明底與單行；約 2 秒延遲的滿意度較高等 | 無 |
| 個人或社群轉檔與專案：Nemotron 3.5 ONNX（codavidgarcia）、yamnet-onnx、ambernet-langid、moonshine-tiny-ja ONNX／GGUF、parakeet.cpp、WhisperLiveKit | 非原作者發布 | 各模型的 ONNX 版與 CPU 速度 | A1 必須自行匯出 |
| 新聞與部落格：Phoronix、MarkTechPost、unite.ai、mindstudio、pyannote.ai、DEV Community 等 | 媒體或廠商行銷 | Ollama Vulkan 時程、各模型介紹 | 無 |
| arXiv 2405.00966、2503.09905、2507.21522、2509.07195、2608.05759 | 機構未確認或未確認是否經同儕審查 | 量化對低資源語言的影響；token-map 推測解碼；高信心錯誤 token | 無 |
