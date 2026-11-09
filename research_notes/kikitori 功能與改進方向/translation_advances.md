# kikitori 翻譯端的改進方向：2024–2026 機器翻譯新進展調查

> 調查日期：2026-11-08。只談翻譯端，不重複 `research_notes/VTuber 直播模型選型調查/translation_models.md`（模型選型、WMT25 ja→zh 人評、量化、授權）與 `research_notes/中日同形詞翻譯錯誤對策/llm_mt_ambiguity.md`（同形詞、CoD、MAPS、TEaR、MBR、thinking）已經寫過的內容。
>
> **來源標記**：
> - **[IWSLT findings]**、**[WMT findings]**：官方 shared task findings，系統比較屬於**獨立評測**，但系統描述來自參賽者自述。
> - **[top-tier]**：ACL／EMNLP／NAACL（Findings 會另外註明）、ICLR、ICML。結果是作者自報。
> - **[公司報告，未經同儕審查]**：知名公司的 arXiv、技術報告或模型卡，結果是自報。
> - **[官方文件]**：產品文件或 release note，只當作功能事實。
>
> 依來源政策排除的項目列在 Q7 的「排除來源」表。
>
> **方法備註**：IWSLT 2025 findings、IWSLT 2026 findings 與 WMT25 Terminology findings 的 PDF 是下載後用 `pdftotext -layout` 抽字閱讀的。IWSLT 2025 的 en→ja MQM 表（Table 20）在雙欄排版下欄位錯位，所以**不引用它的數字**。
>
> **kikitori 現況（對照用，摘自 `docs/specs/translate.md`、`pipeline-engine.md`）**：
> - 即時字幕：ASR 文字層做 LocalAgreement-2，算出 `stable_chars`。只有穩定前綴變長時才把**整句目前的原文**重送翻譯，所以是 re-translation；**譯文本身沒有做穩定化**。
> - 前文：最多 `max_context_lines=3` 句原文，HY-MT 預設組關閉前文。
> - 術語表：只注入命中的詞條，每句最多 8 條。

---

## Q1. LLM 的同步／串流翻譯：怎樣翻不完整句子，才能少閃爍、低延遲？本機 4–9B 實際能做到什麼？

### Takeaway
IWSLT 2025–2026 的獨立評測顯示，表現最好的系統幾乎都是**「串流 ASR 加現成 LLM」的 cascade**，再搭配**不需要訓練的前綴一致性政策**（Local Agreement、LCP 及其放寬版 SoftLCP、RALCP，以及字元級、CJK 感知的 LCP）。IWSLT 2026 的第一名（NVIDIA NeMo）正是 Parakeet 加 **Qwen3.5** 加 LCP。另外有一隊用 **Qwen3.5-4B** 搭配 CJK 字元級 LCP，在四個語向都參賽。kikitori 最划算的翻譯端改進，是把已經用在 ASR 的 LocalAgreement **也套到譯文上**，再加上「穩定譯文前綴預填」。需要 SFT 的方法（interleaved read/write tokens、conversational SimulMT、TAF 預測未來詞）不適合直接拿現成 GGUF 來用。

### Cited Findings
**IWSLT 2025 Simultaneous track**（2025-07）[IWSLT findings] — [Findings of IWSLT 2025](https://aclanthology.org/2025.iwslt-1.44/)
- 語向是 en→de、en→zh、**en→ja**、cs→en。2025 年首次改成**未切段的長音訊**，並開放使用 LLM。
- 延遲分兩個區間（StreamLAAL），en→ja 的區間比其他語向寬：
  - en→de、cs→en：低 0–2 s、高 2–4 s
  - en→zh：低 0–2.5 s、高 2.5–4 s
  - **en→ja：低 0–3.5 s、高 3.5–5 s**
- 除了 CUNI 的 cs→en，所有參賽系統都用了 LLM。
- en→ja 只有 2 隊參賽：
  - 低延遲：NAIST 第一。NAIST 是 Whisper-large-v3 encoder 加 Qwen2.5-7B-Instruct、用 LoRA 微調，以 Local Agreement 轉成同步。
  - 高延遲：CUNI 第一。CUNI 是 Whisper-large-v3（AlignAtt）加 EuroLLM（Local Agreement），並試了「**前面的譯文當 context**」與「**提示 in-domain 術語**」。
- en→ja 另外由日語母語的專業口譯員用 JTF／MQM 人評，主辦方寫道人評「與自動評測結果一致」。
- OSU 的系統：Whisper-large-v2 加 Silero VAD，翻譯用 **4-bit 的 Gemma3-12B-Instruct**，搭配 conversational prompting 與「前一句當 memory」的滑動視窗；並在**字幕資料（OpenSubtitles，先用語意相似度過濾雜訊）上做 LoRA 微調**。
- UPV 用 NLLB-3.3B 加 **relaxed-agreement LCP（RALCP）**。
- findings 的結論：accented 與 non-native 語音的測試集最難。

**IWSLT 2026 Simultaneous track**（2026-07）[IWSLT findings] — [Speech Translation and Metrics in 2026: Findings of the IWSLT Campaign](https://aclanthology.org/2026.iwslt-1.39/)
- **語向沒有日文**：en→de、en→zh、en→it、cs→en。所有語向的延遲區間都是低 0–2 s、高 2–4 s，主要延遲指標改成 LongYAAL，主要品質指標是 COMET-XL。
- **NeMo（NVIDIA）排名第一**，而且在所有語向都位於 Pareto 前緣。系統是 Parakeet ASR 加 **Qwen 3.5 系列 LLM** 加 **LCP 政策**。
- **PINCH-AST** 在四個語向都參賽：Qwen3-ASR-1.7B 加 **Qwen3.5-4B** 翻譯模型，再加 SimulStream 的「**字元級、CJK 感知的 LCP re-translation 政策**」。
- **MLLP-VRAIN UPV**：Parakeet 加 Qwen 3.5，用 **SoftLCP**。SoftLCP 以 Ratcliff/Obershelp 比對找出「錨點 token」，貪婪地接受錨點之前的全部 token，比一般 LCP 更常提交輸出。UPV 在高延遲區間表現穩健，但在低延遲區間掉得最多。
- **CPII-HK**：Qwen3-Omni 加 **test-time wait-k**，用多輪對話格式（每則 user 訊息是新的音訊片段，assistant 回覆是累積譯文），再加 **response prefilling 與 KV cache**。
- **CUNI-ALIGN**：Qwen3-ASR-1.7B 加 **Gemma 4 E4B-it**，把 AlignAtt 改成適用 decoder-only LLM 的版本（離線挑選翻譯用的 self-attention heads）。
- 除了 1 隊，**全部都是 cascade**。唯一的 end-to-end 系統是 CUNI-POCKET（Canary 1B），它「計算感知延遲與非計算感知延遲的差距最大（超過 0.4 s）」。
- **YouTube 來源的 YODAS 測試集**在所有語向都造成最高的延遲，平均比其他測試集高約 1.0 s；主辦方歸因於 domain 與聲學條件不同。
- 結論還指出：高延遲**不一定**帶來成比例的品質提升，頂尖系統尤其如此。

**top-tier 論文（都需要訓練）**
- Fu et al., **Findings of ACL 2025**：建構 SiMT 的 SFT 資料，把來源與目標 token 交錯成同一序列，用特殊 token 表示不同延遲需求，讓 LLM 自己學會 read/write。作者自報在多個 SiMT benchmark 達到 SOTA、保留離線翻譯能力，而且不需額外微調就能推廣到 document-level SiMT（自報）— [aclanthology 2025.findings-acl.1045](https://aclanthology.org/2025.findings-acl.1045/)
- Ouyang et al.（NVIDIA 與 CMU），**NAACL 2025**，TAF（Translation by Anticipating Future）：用 LLM 預測接下來的來源詞再翻譯，在 4 個語向、同為 3 個詞的延遲下，比基線最多高 5 BLEU（自報）— [aclanthology 2025.naacl-long.286](https://aclanthology.org/2025.naacl-long.286/)

**本機執行的工具事實**
- Ollama 的 OpenAI 相容 API 支援的欄位有 `stream`、`temperature`、`stop`、`seed`、`reasoning_effort` 等；`n`、`logit_bias` 不支援（2026-11 查閱）[官方文件] — [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)
- 「最後一則是 assistant 訊息時當作前綴續寫」（prefill）：llama.cpp server 預設支援，有 `--no-prefill-assistant` 可以關閉。**沒有查到 Ollama 的官方文件說明**（見 Gaps）。

### Inferences
- **譯文 LocalAgreement（字元級、CJK 感知）**：kikitori 現在的即時譯文每次重翻都可能整行改寫。把連續兩次即時譯文的最長共同前綴（中日文以字元計，英文以詞計）標為「穩定譯文」，UI 沿用現有的淡色尾巴顯示，就是 IWSLT 2026 中 PINCH-AST 與 NeMo 的做法，而且**不需要新依賴、不多呼叫 LLM**。這是推論；實際的閃爍降低幅度要用 normalized erasure 量測。
- **穩定譯文前綴預填**：下一次重翻時，把「已穩定的譯文」放成 assistant 前綴讓模型續寫。這相當於 Google 2021 的 biasing 和 CPII 的 response prefilling，可以讓譯文只能往後長，同時減少要生成的 token。代價是前綴錯了就改不回來，所以 final path 應該照舊整句重翻。是否可行取決於 Ollama 是否支援 assistant prefill，**要先實測**。
- **日文來源要多留延遲預算**：IWSLT 2025 給 en→ja 的延遲區間比 de、zh 寬約 1.5 s，反映出主辦方認為語序差異大的語向需要更多等待（這是推論）。kikitori 的 ja→zh、ja→en 是 SOV 往 SVO，句末動詞與否定（〜ない、〜じゃない）常在最後才出現。fast path 可以考慮**只翻到 ASR 的穩定前綴為止**，不翻還在變的尾巴，用「少翻一點」換「少改一點」。
- **YouTube 型音訊的延遲尖峰**（IWSLT 2026 YODAS +1.0 s）支持把 VTuber 直播當成 out-of-domain，延遲與品質都要用自己的素材量測。
- **需要 SFT 的同步方法**（Fu et al.、TAF、conversational SimulMT）目前不適合 kikitori：TAF 每一步都要多次 LLM 預測，以本機 4–9B 的速度加不進 fast path（推論）。

### Gaps
- IWSLT 2025 en→ja 的 BLEU、StreamLAAL 數字與 MQM 錯誤分數（Table 20、26）抽字後欄位錯位，沒有可靠數字。
- IWSLT 2026 沒有日文的同步翻譯語向，日文來源（ja→X）的同步翻譯在 IWSLT 2024–2026 都沒有評測。
- 沒有找到 2025–2026 年同儕審查、量測「LLM re-translation 的 normalized erasure」的論文。Arivazhagan 2020 與 Google 2021 已記在前一份筆記。
- Ollama 是否支援「最後一則 assistant 訊息續寫」，沒有在官方文件找到，要在本機測試。

---

## Q2. 直播字幕的語篇與文件層級 context：前文給多少？說話者一致性、日文敬語與省略主詞、整場直播的人名一致

### Takeaway
2026 年最直接的新證據來自 IWSLT 2026 的 **Extra Context 子賽道**：給系統「這場演講的相關文件」，**幾乎所有參賽系統都受益**，而且增益主要在**專有名詞與領域詞**。UPV 在三個語向、兩種延遲區間平均 **+2.75 COMET**。kikitori 對應的做法是讓使用者填「本場直播資訊」（直播標題、VTuber 名、遊戲名、連動成員），抽出實體清單注入提示詞。至於「前文要幾句」、日文敬語與省略主詞在 ja→zh 的 LLM 研究，**2024–2026 年 top-tier 證據仍然缺乏**。

### Cited Findings
- **IWSLT 2026 Extra Context 子賽道**（2026-07）[IWSLT findings] — [IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)
  - 設定：ACL 演講附上對應的論文 PDF。
  - 3 隊參賽。「Almost all submissions show a positive impact of additional context」，只有 CUHKSZ 的 en→zh 小幅下降。
  - UPV 增益最大：三個語向、兩種延遲區間平均 **+2.75 COMET**。NeMo 加不加 context 差不多。
  - 主辦方的結論：context「has a positive impact on entity recognition and translation if used properly」，對領域詞與專有實體幫助明顯，但**幅度高度取決於整合方式**。
  - CUHKSZ 的做法：低延遲時從文件抽出**每場演講的 named entities** 注入 system prompt，高延遲時則注入完整摘要。
  - 負面案例：NeMo 的 en→zh 帶 context 後延遲高達 10.86 s，因為它常常把整句中文緩衝起來，在語音結束很久後才輸出。
- **DelTA**（Wang et al., **ICLR 2025**）[top-tier] — [ICLR 2025 proceedings](https://proceedings.iclr.cc/paper_files/paper/2025/hash/285149554f6fbed6e2f9ec72d131bd23-Abstract-Conference.html)
  - 是**線上**、逐句翻譯的文件級翻譯 agent。記憶分四層：Proper Noun Records、Bilingual Summary、Long-Term Memory、Short-Term Memory。
  - 每翻完一句，就由 LLM 元件從原文與譯文抽出關鍵資訊、更新記憶。
  - 作者自報：一致性分數最多 +4.58 個百分點，COMET 平均最多 +3.16，而且逐句翻譯所以不會漏句。
- **IWSLT 2025**：CUNI 用「前面的譯文當 context」；OSU 用「前一句的滑動 memory」；CMU 用 KV cache 串接維持 context。這三隊都在 en→de 或 en→zh 名列前段 [IWSLT findings] — [IWSLT 2025 Findings](https://aclanthology.org/2025.iwslt-1.44/)
- 日文敬語：2024–2026 的 top-tier 中，只有前一份筆記已經引用的 Sony（Findings of EMNLP 2025）在 en→ja 量測 keigo 與 formality。這次搜尋**沒有**找到 ja→zh、ja→en 方向的 LLM 敬語或省略主詞研究；找到的都是被排除的來源（見 Q7 排除表）。

### Inferences
- **「本場直播資訊」欄位（低成本、高效益）**：讓使用者在開始前填直播標題、主播名、遊戲名、常出現的人名，或貼上直播說明欄。用 SudachiDict（振假名功能已經會下載）的固有名詞詞性，加上片假名與英數規則抽出實體，作為「本場術語」注入。這對應 CUHKSZ 低延遲時的作法，屬於零額外 LLM 呼叫的版本。
  - 效益的方向有 IWSLT 2026 支持；但它的 context 是論文 PDF，VTuber 直播說明欄的資訊密度低很多，幅度要實測。
- **整場人名一致（DelTA 的精簡版）**：DelTA 每句都要多呼叫 LLM 做抽取與摘要，放不進 fast path。可以只在 final path 背景執行：同一個專有名詞**第一次的譯法**寫進 session 術語表，之後命中時注入，讓整場維持一致。這是推論，見 Q3 的風險。
- **雙向口譯模式的說話者標記**：前文可以標上「我方／對方」。理由是 IWSLT 與 DelTA 都顯示「結構化的 context」比單純堆句子有效，而日文省略主詞時，說話者身分是唯一的線索。這點**沒有直接證據**，屬於推論。
- **HY-MT 預設組維持關閉前文是對的**：IWSLT 2026 findings 明說 context 增益「取決於整合方式」，而且有系統因 context 受害（CUHKSZ en→zh 下降、NeMo 延遲爆增），和 kikitori 自己看到 HY-MT 把前文一起翻出來的現象一致。

### Gaps
- 沒有找到 2024–2026 年 top-tier 研究系統性地比較「前文要幾句」對 LLM 句級字幕翻譯的影響。被排除的 MT Summit 2025 論文只顯示 LLM 的文件級增益不一定反映在代名詞翻譯上。
- 沒有 ja→zh 或 ja→en 的 LLM 敬語、省略主詞、角色語（キャラ語尾）評測。
- DelTA 用了哪些骨幹模型、在 7–9B 模型上的增益多少，只讀到摘要，沒有核對。

---

## Q3. 從直播本身自動抽取術語、即時建立詞表：有沒有證據？

### Takeaway
**WMT25 Terminology findings（2025-11）**用「無術語／正確術語／隨機術語」三種條件做因果比較，結果是：**正確術語一致提升整體品質與術語準確率，隨機術語也有較小的增益，而且模型越強，從正確術語得到的額外好處越大**。這次還有 **en↔繁體中文**的文件級賽道。自動抽取術語方面，DelTA（ICLR 2025）的 Proper Noun Records 是「線上邊翻邊建詞表」最接近的同儕審查證據。對 kikitori 來說，**自動建表的風險在於抽錯的術語會被一再注入**，所以需要信心門檻，或讓使用者確認。

### Cited Findings
- **WMT25 Terminology Translation Task**（Semenov et al., 2025-11）[WMT findings] — [Findings of the WMT25 Terminology Translation Task: Terminology is Useful Especially for Good MTs](https://aclanthology.org/2025.wmt-1.30/)
  - 兩個賽道：
    - Track 1：句級，IT 領域，en→de/ru/es，每句 1–2 條術語。
    - Track 2：**文件級，香港金融領域，英文與繁體中文互譯**，每份文件附最多 1K 條的一對多詞典。
  - 三種模式是 No Term、Proper Term、Random Term。Random Term 是從參考譯文隨機抽詞對當詞典，用來區分「術語本身的效果」和「只是多看到部分正確譯文的效果」。
  - 主要結論：
    - 多數系統都用了 LLM。
    - 句級賽道的頂尖系統術語準確率接近完美，benchmark 已經接近飽和。
    - 與過去的預期相反，大多數系統在一般翻譯能力與術語準確率之間**幾乎沒有取捨**。
    - **加入任何術語都會提升整體品質；對頂尖系統來說，正確術語比隨機術語更有用。**
    - 文件級金融翻譯仍然不足。
  - 13 隊提交 20 個系統（Track 1），Track 2 有 4 隊。
- **DelTA**（ICLR 2025）：Proper Noun Extractor 會在每句翻完後更新 Proper Noun Records，自報一致性最多 +4.58 個百分點 [top-tier] — [ICLR 2025](https://proceedings.iclr.cc/paper_files/paper/2025/hash/285149554f6fbed6e2f9ec72d131bd23-Abstract-Conference.html)
- **IWSLT 2026**：CUHKSZ 從外部文件抽出每場的 named entities 注入；主辦方確認 context 對實體翻譯有幫助（見 Q2）[IWSLT findings] — [IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)
- **IWSLT 2025**：CUNI 試了「提示 in-domain 術語」，並拿下 en→ja 高延遲第一 [IWSLT findings] — [IWSLT 2025 Findings](https://aclanthology.org/2025.iwslt-1.44/)

### Inferences
- **session 自動詞表（建議做成「建議詞條」，由使用者一鍵採用）**：
  1. 在 final path 完成後，用 SudachiDict 抽出固有名詞，以及出現 ≥ 2 次的片假名詞或專有名詞，作為候選。
  2. 由 LLM 在**閒置時**（沒有待翻句子時）以單次 JSON 呼叫配對譯法；也可以直接取該句譯文裡的對應片段。
  3. 寫進 session 術語表，標成「自動」，優先序低於使用者詞條與內建表。
  4. UI 上讓使用者採用或刪除，並可以匯出成 CSV。
  - 理由：WMT25 顯示正確術語有用，而隨機術語也只有小增益、沒有傷害。但「錯誤的術語」（例如 ASR 把人名聽錯，或第一次就翻錯）**沒有被 WMT25 測過**，風險不明，所以不要自動無聲套用。
- **「強模型受益更多」**：kikitori 的 4B 從術語得到的好處可能比 9B 少。這是從 WMT25 標題結論外推，沒有直接量測。
- **繁中術語**：WMT25 Track 2 的 en↔zh-Hant 金融資料集（GitHub `wmt-conference/wmt25-terminology`）可以拿來測「zh-TW 目標的術語遵循率」，但它是英中語對、金融領域，和 ja→zh-TW 直播不同，只能當格式與遵循率的 sanity check。資料授權沒有確認。

### Gaps
- 沒有找到在**口語或直播**上「自動抽取術語再注入」的同儕審查評測。
- WMT25 Terminology 各系統的具體分數（COMET、術語準確率）沒有抽出來，只用了 findings 的文字結論。
- 錯誤術語（noisy glossary）對 LLM 翻譯的傷害程度，沒有找到量化研究。

---

## Q4. 本機可用、授權對商用友善的品質估計（QE），能否用來標出低信心的譯文？（不用 PyTorch）

### Takeaway
目前**沒有一個同時符合「商用授權、品質好、不用 PyTorch、消費級 GPU 跑得動」的現成 QE 模型**：
- COMET 家族的權重（CometKiwi、XCOMET-XL/XXL）都是 **CC BY-NC-SA 4.0**。
- Google 的 MetricX-25 改用 **Gemma 3 12B** 當骨幹，在本機和翻譯模型搶 VRAM 不實際。
- xCOMET-lite（278M，EMNLP 2024）的權重授權沒有確認，而且它是從 NC 授權的 XCOMET 蒸餾而來。

**零成本的替代方案**是讀 LLM 自己輸出的 token logprobs（Ollama v0.12.11 起支援），當作低信心旗標。EMNLP 2025 的研究指出這類無監督訊號「被低估」；但 WMT25 的評測 findings 也提醒，segment 級品質預測本身仍然很難。

### Cited Findings
- **XCOMET-XL**：權重授權 `cc-by-nc-sa-4.0`；COMET 程式碼本身是 Apache-2.0（2026-11 查閱）[模型卡事實] — [Unbabel/XCOMET-XL](https://huggingface.co/Unbabel/XCOMET-XL)
  - 前一份筆記已記錄 CometKiwi 是 NC 授權。
- **MetricX-25**（Google，WMT25 評測任務的參賽系統，2025-10）[公司報告，未經同儕審查] — [MetricX-25 and GemSpanEval (arXiv 2510.24707)](https://arxiv.org/abs/2510.24707)
  - 骨幹從 mT5 換成 **Gemma 3 12B**，有 Hybrid 與純 QE（25-QE）兩種模式。
  - WMT24 **ja-zh** 的 segment 級 pairwise accuracy：QE 57.21、Hybrid 57.72；MetricX-24 Hybrid 是 53.90。作者說 ja-zh 是改善最大的語對。
  - 同團隊的 GemSpanEval（錯誤片段偵測）用 Gemma 3 27B。
  - 論文頁面**沒有寫**權重的釋出與授權。
- **WMT25 Automated Translation Evaluation findings**（2025-11）[WMT findings] — [Findings of the WMT25 Shared Task on Automated Translation Evaluation Systems](https://aclanthology.org/2025.wmt-1.24/)
  - 這屆把 metrics 和 QE 合併成一個任務，副標題是「Linguistic Diversity is Challenging and References Still Help」。
  - 大型 LLM 在 system 級表現強；但在 **segment 級，有參考譯文的 baseline metric 優於 LLM**。錯誤偵測與 precision/recall 的平衡仍然困難，跨語言的穩健性是主要未解問題。
- **xCOMET-lite**（EMNLP 2024）：278M 參數，是 xCOMET-XXL 的 2.6%，保留 92.1% 的品質（自報）。程式碼、資料與模型都已公開，但頁面沒有寫授權 [top-tier] — [aclanthology 2024.emnlp-main.1223](https://aclanthology.org/2024.emnlp-main.1223)
- **Sarti, Zouhar, Nissim, Bisazza**（EMNLP 2025）：在 12 個翻譯方向比較 14 種 word-level QE 指標，包含來自模型內部的 uncertainty 與 interpretability 訊號。結論是無監督指標有「untapped potential」，監督式方法在標註不確定時有缺陷，而只用單一標註者的評測不可靠 [top-tier] — [aclanthology 2025.emnlp-main.924](https://aclanthology.org/2025.emnlp-main.924/)
- **Ollama logprobs**：v0.12.11（2025-11）的 release note 寫「Ollama's API and the OpenAI-compatible API now supports Logprobs」，原生 API 用 `"logprobs": true` 開啟，回傳每個 token 的 log probability [官方 release note] — [Ollama v0.12.11](https://github.com/ollama/ollama/releases/tag/v0.12.11)
  - **但兩份官方來源互相矛盾**：OpenAI 相容 API 的文件頁（2026-11 查閱）仍把 Logprobs 標成未支援 — [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility)

### Inferences
- **「低信心旗標」可以零成本實作**：
  - 作法：final path 請求加上 logprobs，算出譯文 token 的平均 logprob 與最低 logprob（要去除長度偏差），低於門檻就在字幕紀錄標示「可能不準」。
  - 可選的延伸：只對旗標句改用較大模型，或改用「加詞表提示的 prompt」重翻。這把前一份筆記 R2 的「兩個候選加 QE」改成只對疑似錯誤句觸發，平均延遲幾乎不增加。
  - 風險：模型對自己的流暢錯譯常常很有信心，例如「豚の排泄物」這種流暢但語意錯的輸出，logprob 可能很高。被排除的 UvA-MT（WMT25 參賽論文）也提到 uncertainty 會高估模型自己的輸出。所以**它只能當輔助旗標，不能當品質保證**，要用 kikitori 的陷阱句測試集與 translate_eval 驗證相關性。
- **MetricX-24（mT5、Apache-2.0）轉 ONNX** 仍是唯一授權乾淨的學習型 QE 選項。但 MetricX-25 的作者表示 MetricX-24 在 segment 級明顯落後 MetricX-25，而 MetricX-25 的 12B 骨幹不適合和翻譯模型共用 8–12 GB 顯示卡。**結論：先做 logprob 旗標，學習型 QE 暫緩。**
- **xCOMET-lite**：即使 repo 標示寬鬆授權，它是從 CC BY-NC-SA 的 XCOMET 蒸餾而來，衍生權重是否受 NC 約束有法律疑慮。**不建議**在沒有法律確認前使用。

### Gaps
- Ollama 的 OpenAI 相容端點是否**真的**回傳 logprobs（release note 和文件頁衝突），而且 Qwen3.5 的混合架構是否支援，要在本機實測。
- 沒有找到在 ja→zh 或 ja→en 量測「LLM logprob 與人評相關性」的同儕審查數字。
- MetricX-25 的權重是否公開、授權為何，論文頁面沒有寫。
- WMT25 評測 findings 裡 QE 指標的具體相關係數表沒有抽出來。

---

## Q5. 2025–2026 新的小型開放翻譯模型（前兩份筆記以外）與 LoRA（GGUF）輕量領域適應

### Takeaway
前兩份筆記之後，最重要的變化有兩個：
1. **Gemma 4（2026-07）改用 Apache-2.0**：Gemma 系列第一次授權乾淨。E4B 約 4.5B 有效參數，原生支援音訊與語音翻譯，IWSLT 2026 也有隊伍用它當同步翻譯模型（en→zh 結果「mixed」）。
2. **Qwen3.5 在 IWSLT 2026 有了獨立的旁證**：第一名 NeMo 與另外兩隊（PINCH-AST 用 Qwen3.5-4B，UPV）都用 Qwen3.5 翻譯。這支持 kikitori 目前的預設，但語向是 en→X，不是 ja→X。

NVIDIA 的 Riva-Translate-4B-Instruct-v2 支援 ja 與 zh-TW，但它以英文為中心，授權是 NVIDIA Open Model License。LoRA 方面，IWSLT 有「在字幕資料上做 LoRA」的使用案例，但**目前的 Ollama Modelfile 文件找不到 ADAPTER 指令**，而且沒有 VTuber 的 ja↔zh 平行語料，投入產出比低。

### Cited Findings
- **Gemma 4 E4B-it**（Google，技術報告 2026-07-02）[公司報告／模型卡] — [google/gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)；[Gemma 4 Technical Report (arXiv 2607.02770)](https://arxiv.org/abs/2607.02770)
  - 授權 **Apache 2.0**。
  - 參數：4.5B 有效、含 embeddings 共 8B；音訊 encoder 約 300M；context 128K。
  - 語言：預訓練 140 種以上，開箱支援 35 種以上。
  - 模型卡**沒有**列 WMT24++ 或日文的翻譯分數，只列 CoVoST 35.54。
  - 建議取樣：temperature 1.0、top_p 0.95、top_k 64。thinking 由 system prompt 開頭的 `<|think|>` 控制。
  - 技術報告摘要沒有提到翻譯評測。
- **IWSLT 2026**（2026-07）[IWSLT findings] — [IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)
  - NeMo（第一）用 Qwen 3.5 系列；PINCH-AST 用 **Qwen3.5-4B**；UPV 用 Qwen 3.5；CUNI-ALIGN 用 **Gemma 4 E4B-it**。
  - 規則：constrained 條件只允許「permissive license」的開放權重 LLM。
- **Riva-Translate-4B-Instruct-v2**（NVIDIA，訓練期間 2025-11 到 2026-05）[模型卡，自報] — [nvidia/Riva-Translate-4B-Instruct-v2](https://huggingface.co/nvidia/Riva-Translate-4B-Instruct-v2)
  - 支援英文與 36 種非英語，包含 ja、zh-CN、**zh-TW**。
  - 授權是 **NVIDIA Open Model License**。
  - 自報 WMT24++ 分數（BLEU／COMET-DA／XCOMET-XXL）：En→ja 23.10／0.46／0.80；ja→En 21.70／0.47／0.83；En→zh-TW 32.50／0.54／0.82。
  - prompt 用語言對標籤（例如 `en-ja`、`en-zh-tw`）；官方推薦的執行環境是 vLLM。
  - 模型卡的 model tree 寫 base 是 Mistral-NeMo-12B-Base，和 4B 的標示矛盾，頁面沒有解釋。
- **MiLMMT-46**（arXiv 2602.11961，2026-02，HF 與 GitHub 在 `xiaomi-research` 名下）：以 Gemma3 為基礎、涵蓋 46 種語言，作者自報優於 Seed-X、HY-MT-1.5、TranslateGemma。頁面沒有寫模型大小與授權 [公司報告（推測是小米），未經同儕審查] — [arXiv 2602.11961](https://arxiv.org/abs/2602.11961)
- **LoRA 的使用案例**（2025-07）[IWSLT findings] — [IWSLT 2025 Findings](https://aclanthology.org/2025.iwslt-1.44/)
  - OSU 對 4-bit 的 Gemma3-12B 在過濾過的 OpenSubtitles 字幕資料上做 LoRA，用於同步翻譯。
  - NAIST 對 Qwen2.5-7B 做 LoRA（ST 與 ASR 聯合訓練）。
  - findings 沒有把 LoRA 的效果獨立拆開比較。
- **Ollama Modelfile 文件**（2026-11 查閱）：指令表只有 FROM、PARAMETER、TEMPLATE、SYSTEM、LICENSE、MESSAGE 等，**找不到 ADAPTER 的說明** [官方文件] — [Ollama Modelfile](https://docs.ollama.com/modelfile)

### Inferences
- **Gemma 4 E4B 值得加入 kikitori 的陷阱句與 translate_eval 評測**：授權已經乾淨，大小和 qwen3.5:4b 同級。但它沒有任何 ja→zh 的翻譯證據，在 IWSLT 2026 的 en→zh 也是「mixed」。要注意它的官方取樣是 temperature 1.0，和 kikitori 的 temperature 0 不同，兩種都要測。
- **TranslateGemma**（基於 Gemma 3）的授權仍是 Gemma Terms，不會因為 Gemma 4 改授權而一起改變。這是推論，前一份筆記也已經列為待確認。
- **Riva-Translate-4B-v2**：模型卡只列 En→X 與 X→En 的分數，推測 **ja→zh-TW 可能要經過英文**（推論，模型卡沒有明說是否支援非英語對）。它的授權也不是 OSI 授權，所以不建議當預設，最多當「ja→en 候選」。
- **LoRA**：要真的做，得 (a) 自己把 LoRA 合併進基底模型、轉成 GGUF，再發布到 Ollama registry；(b) 準備 ja→zh 的直播口語平行語料（目前沒有公開資料）。成本高、證據弱，**不建議近期投入**。

### Gaps
- Gemma 4 的翻譯 benchmark（WMT24++ 等）沒有在模型卡和技術報告摘要中找到，技術報告全文沒有讀。
- Ollama 是否仍支援 ADAPTER（舊版有，現在的文件頁沒有），沒有在 release note 確認。
- MiLMMT-46 的大小、授權，以及它是否涵蓋 ja 與 zh，都沒有確認。
- 沒有找到 2025–2026 年 Mistral、Cohere、Meta、Microsoft 新的 ≤12B 開放翻譯專用模型（本次搜尋範圍內）。

---

## Q6. 端到端語音翻譯與語音 LLM（Seamless、Qwen-Omni／Audio、Gemma 3n／4、Voxtral、Hibiki）：能不能在本機不用 PyTorch 跑？能帶來什麼？

### Takeaway
**目前不值得用端到端模型取代 kikitori 的 Whisper 加 LLM cascade**：
- IWSLT 2026 除了一隊之外全是 cascade，第一名也是 cascade。
- 授權乾淨又支援日文的候選，只有 **Gemma 4 E2B/E4B**（Apache-2.0，llama.cpp 已支援音訊輸入，但官方文件標示「highly experimental」，單段音訊上限 30 s）。
- 其他候選各有致命傷：Seamless 是 **CC BY-NC**；Hibiki-Zero 是 **CC BY-NC-SA**，而且只支援 fr/es/pt/de→en；Voxtral（Apache-2.0）的官方語言清單看不到日文；Qwen3-Omni-30B-A3B 太大。

可行的實驗是：把 Gemma 4 E4B 當成「**同時看音訊與 ASR 文字的 final pass**」做 A/B，可能修正 Whisper 的同音錯誤。

### Cited Findings
- **IWSLT 2026**：除了 CUNI-POCKET（Canary 1B），所有參賽系統都是 cascade，NeMo cascade 第一。CPII-HK 和 CUHKSZ 用 Qwen3-Omni（30B-A3B）做端到端，在 en→zh 進入 Pareto 前緣 [IWSLT findings] — [IWSLT 2026 Findings](https://aclanthology.org/2026.iwslt-1.39/)
- **Gemma 4 音訊**：E2B、E4B 與 12B Unified 都有訓練語音辨識與翻譯。官方 prompt 範例是「Transcribe the following speech segment in {SOURCE_LANGUAGE}, then translate it into {TARGET_LANGUAGE}.」，先輸出轉寫，再輸出「{TARGET_LANGUAGE}: 譯文」。單段上限 **30 秒**，每秒 25 個 token。官方範例只有 Transformers [官方文件] — [Gemma audio capabilities](https://ai.google.dev/gemma/docs/capabilities/audio)；授權 Apache 2.0 — [gemma-4-E4B-it](https://huggingface.co/google/gemma-4-E4B-it)
- **llama.cpp 多模態文件**：列出 Qwen2.5-Omni（3B/7B）、Voxtral Mini 3B，以及 Gemma 4 E2B/E4B 的音訊輸入，並註明音訊「highly experimental」，品質可能較差（2026-11 搜尋摘要；未逐行核對原文）[官方文件] — [llama.cpp docs/multimodal.md](https://github.com/ggml-org/llama.cpp/blob/master/docs/multimodal.md)
- **Voxtral**（Mistral，2025-07）：Mini 與 Small 都是 **Apache 2.0**。自報 Voxtral Small 在 FLEURS 語音翻譯的所有評測組合都是 SOTA。官方公布的 ASR 語言是英、西、法、葡、印地、德、荷、義，**看不到日文**（來自二手摘要，未在原文核實）[公司報告，未經同儕審查] — [Voxtral (arXiv 2507.13264)](https://arxiv.org/abs/2507.13264)
- **Hibiki-Zero**（Kyutai，2026-02，ICML 2026 oral）[top-tier] — [ICML 2026](https://icml.cc/virtual/2026/poster/66094)；[模型卡](https://huggingface.co/kyutai/hibiki-zero-3b-pytorch-bf16)
  - 3B，用 GRPO 降低延遲，不需要詞級對齊資料。
  - 只支援 fr/es/pt/de → en。
  - 權重授權 **CC BY-NC-SA 4.0**，官方提供的是 PyTorch 權重。
- **SeamlessM4T v2 large**（Meta）：2.3B，授權 **cc-by-nc-4.0**。語言表含日文來源與中文目標 [模型卡事實] — [facebook/seamless-m4t-v2-large](https://huggingface.co/facebook/seamless-m4t-v2-large)
- **Qwen3-ASR**（Alibaba，2026-01）[公司報告，未經同儕審查] — [Qwen3-ASR Technical Report (arXiv 2601.21337)](https://arxiv.org/abs/2601.21337)
  - Apache 2.0，有 0.6B 與 1.7B 兩種大小，支援 52 種語言與方言的 LID。
  - 1.7B 的日文錯誤率：CommonVoice-ja 11.64、FLEURS-ja 5.20。
  - 支援串流，並附 ForcedAligner（日文平均偏移 42.4 ms）。
  - 推論環境是 vLLM 與 Transformers，沒有提到 ONNX 或 GGUF。
  - IWSLT 2026 有兩隊用它當 ASR。
- **Diet-KIT**（IWSLT 2026 研究論文；見排除表）：Qwen2-Audio-7B 壓到 3.98 GB，COMET 只小幅下降。

### Inferences
- **端到端的潛在好處**有兩個，都還沒有經過證實：
  1. 模型能直接聽到語氣、笑聲、強調。
  2. 可以修正 Whisper 的同音字與專有名詞錯誤。
  - 但 IWSLT 2026 的結果顯示 cascade 仍然領先；而且 kikitori 已經對 Whisper 做了大量 VTuber 專屬調整（幻覺過濾、BGM），換掉的風險很大。
- **建議的最小實驗**：
  - 作法：在 final path 把該句音訊（不超過 30 s）、Whisper 文字與前文一起送給 Gemma 4 E4B（經 llama.cpp 或 Ollama，不用 PyTorch），要求「參考音訊修正原文，再翻譯」，和現在的 qwen3.5 純文字 final 做 A/B。
  - 成本：音訊每秒 25 token，10 s 句子約 250 token 的 prefill，延遲增加有限（推估）。
  - 主要風險：llama.cpp 的音訊支援是 experimental；Ollama 是否支援 gemma4 的音訊輸入，只有非官方來源。
- **ASR 換成 Qwen3-ASR** 屬於 ASR 範圍，不在本調查內。它沒有 ONNX/GGUF，不符合 kikitori 不用 PyTorch 的限制，只能列入觀察。

### Gaps
- Gemma 4 E4B 在日文 ASR 與語音翻譯（ja→zh、ja→en）上的分數沒有找到。
- Ollama 官方是否支援 gemma4 的音訊輸入：只找到鏡像的 changelog（v0.33.3）和使用者回報，相互矛盾，見排除表。
- Voxtral 是否支援日文，沒有在原始技術報告核實。
- Qwen2.5-Omni 與 Qwen3-Omni 的授權，以及在消費級 GPU 上的日文語音翻譯品質，沒有查證。

---

## Q7. 綜合：可加入 kikitori 的翻譯端改進機會（證據、可行性、授權、成本與效益）

### Takeaway
依「證據強度 × 低成本」排序：
1. **譯文的 LocalAgreement 與穩定前綴預填**（減少閃爍）
2. **「本場直播資訊」實體注入**
3. **logprob 低信心旗標**
4. **session 自動詞表建議**
5. 評測 **Gemma 4 E4B**（Apache-2.0）
6. 實驗性的**音訊輔助 final pass**

LoRA、需要 SFT 的同步方法、學習型 QE，以及非商用的端到端模型，都不建議近期投入。

### Cited Findings
（表中的證據都已經在 Q1–Q6 附上來源，這裡只做彙整。效益與成本是推估。）

| # | 機會 | 證據（日期；類型） | kikitori 可行性 | 授權與依賴 | 成本 | 預期效益 |
|---|---|---|---|---|---|---|
| T1 | 即時**譯文**做字元級、CJK 感知的 LocalAgreement 或 LCP，穩定譯文正常顯示、尾巴淡色 | IWSLT 2026：NeMo（第一，LCP）、PINCH-AST（Qwen3.5-4B 加 CJK 字元 LCP）、UPV（SoftLCP）；IWSLT 2025：CUNI（LA，en→ja 高延遲第一）[findings，獨立] | 高：純邏輯，可以重用 `stability.py`，UI 已經有淡色尾巴 | 無新依賴 | 低 | 中到高（譯文閃爍明顯減少；要量測 normalized erasure） |
| T2 | 下一次重翻時以穩定譯文當 **assistant 前綴（prefill）** | CPII-HK 的 response prefilling（IWSLT 2026 findings）；Google 2021 biasing（前一份筆記） | 中：要先確認 Ollama 支援 prefill；只用在 fast path | 無 | 低到中 | 中（譯文只會往後長，生成的 token 也變少） |
| T3 | fast path 只翻 ASR 的**穩定前綴**，不翻尾巴（或尾巴不翻譯、只顯示原文） | IWSLT 2025 給 en→ja 較寬的延遲區間（主辦方設計）；推論 | 高 | 無 | 低 | 中（少改寫；即時譯文會比較短，要做 UX 測試） |
| T4 | 「**本場直播資訊**」欄位：抽出實體注入提示詞 | IWSLT 2026 Extra Context：幾乎全部系統受益，UPV 平均 +2.75 COMET，增益在實體翻譯 [findings] | 高：SudachiDict 已經存在；沿用術語注入格式 | 無新依賴 | 低到中 | 中（人名、遊戲名；幅度依資訊密度而定） |
| T5 | **session 自動詞表**（固有名詞的第一次譯法），以「建議詞條」呈現 | WMT25 Terminology（2025-11，正確術語有用、強模型受益更多）[findings]；DelTA（ICLR 2025，一致性最多 +4.58pp，自報） | 中：在背景閒置時呼叫 LLM，或從譯文對齊；要做 UI | 無 | 中 | 中（整場一致；抽錯會被一再注入，所以要讓使用者確認） |
| T6 | **logprob 低信心旗標**，旗標句改用大模型或不同 prompt 重翻 | Sarti et al.（EMNLP 2025：無監督訊號有潛力）；WMT25 評測 findings（segment 級 QE 仍然困難）；Ollama v0.12.11 logprobs | 中：release note 和文件頁衝突，要實測 | 無 | 低到中 | 低到中（只是輔助；對流暢錯譯可能無效） |
| T7 | 評測 **Gemma 4 E4B** 當翻譯模型 | Apache-2.0（2026-07）；IWSLT 2026 CUNI-ALIGN 有使用（en→zh mixed） | 高：Ollama 或 GGUF | Apache-2.0 | 低（只是評測） | 未知 |
| T8 | 音訊輔助的 final pass（Gemma 4 E4B 同時看音訊與 ASR 文字） | Gemma 官方音訊文件；llama.cpp 音訊 experimental；IWSLT 2026 cascade 仍領先 | 低到中 | Apache-2.0；要確認 runtime | 高 | 未知（可能修正同音錯誤） |
| T9 | 雙向口譯模式的前文標上說話者 | 沒有直接證據（推論，見 Q2） | 高 | 無 | 低 | 低到中 |
| T10 | 把提示詞中固定的部分（規則、術語）放前面、會變的部分放後面，讓伺服器重用 prompt prefix cache | 多輪對話加 KV cache 重用的設計（IWSLT 2026 CPII、CUHKSZ） | 高 | 無 | 低 | 低到中（降低 prefill 延遲；llama.cpp 的 cache 行為要實測） |
| ✗ | 需要 SFT 的同步方法（Fu et al. ACL Findings 2025、TAF NAACL 2025）、LoRA 微調、MetricX-25 或 xCOMET QE、Seamless、Hibiki-Zero | 見 Q1、Q4–Q6 | 低 | NC 授權、需要 PyTorch、要訓練資料 | 高 | — |

### Inferences
- T1 到 T4 都**不需要新依賴、不改模型**，可以照「先量測，再改」的順序做：
  1. 先在 `experiments/` 用錄下的即時字幕序列算 normalized erasure 與 COMET，當作基準。
  2. 再依序加入 T1、T2、T3。
- T5 和 T6 涉及 UI 或新的使用者可見行為，屬於「影響其他模組」的變更，開工前應先寫 spec（依 CLAUDE.md 的規則）。

### Gaps
**排除來源**（依來源政策沒有當作證據；列出它們若可用會說什麼）：

| 來源 | 排除原因 | 若可用，它會說什麼 |
|---|---|---|
| Wang et al., Conversational SimulMT（IWSLT 2025 研究論文，Monash／MBZUAI）[aclanthology 2025.iwslt-1.8](https://aclanthology.org/2025.iwslt-1.8/) | 不是 IWSLT findings | source 與 target 交錯成多輪對話，可以重用 KV cache，比 offline prompt 快約 2 倍、品質相當（Llama2-7b-chat，需要 SFT） |
| Fuxa & Macháček, AlignAtt4LLM（IWSLT 2026 系統論文）[aclanthology 2026.iwslt-1.32](https://aclanthology.org/2026.iwslt-1.32/) | 不是 findings | 把 AlignAtt 改成適用 decoder-only LLM（Gemma 4 E4B），輸出只往後長（append-only），作者認為實際部署偏好這種方式以避免 re-translation 閃爍；en→de/it 優於 baseline，en→zh 結果 mixed |
| Zhang, Yang, Nakamura, Redefining Machine Simultaneous Interpretation（IWSLT 2026 研究論文）[aclanthology 2026.iwslt-1.2](https://aclanthology.org/2026.iwslt-1.2/) | 不是 findings | 給 LLM 增加 Sentence_Cut、Drop、Partial_Summarization、Pronominalization 等動作，在 ACL60/60 的 en→zh/de/**ja** 上改善語意指標並降低延遲 |
| Xing et al., Test-Time Adaptation of Qwen3-Omni（IWSLT 2026 系統論文）[aclanthology 2026.iwslt-1.27](https://aclanthology.org/2026.iwslt-1.27/) | 不是 findings（findings 已經摘要它的方法） | 停頓切段加 wait-k、多輪對話、response prefilling、KV cache，不用訓練就勝過 cascade baseline |
| Liu, Koneru, Niehues, Diet-KIT（IWSLT 2026）[aclanthology 2026.iwslt-1.21](https://aclanthology.org/2026.iwslt-1.21/) | 不是 findings | Qwen2-Audio-7B 經 HQQ／AWQ 壓到 3.98 GB，COMET 只小幅下降 |
| Wu & Monz, UvA-MT（WMT25 評測任務參賽論文）[aclanthology 2025.wmt-1.72](https://aclanthology.org/2025.wmt-1.72.pdf) | WMT 只採 findings | Tower、Gemma-3 的 uncertainty 可以當 segment 級 QE，校準後更好；但會高估模型自己的輸出 |
| Mohammed & Niculae（MT Summit 2025）[aclanthology 2025.mtsummit-1.10](https://aclanthology.org/2025.mtsummit-1.10) | 不在允許名單 | 9 個 LLM 在文件級的增益不反映在代名詞翻譯上 |
| JP-TL-Bench（arXiv 2601.00223） | 只有 arXiv，非知名機構 | 日英翻譯的 LLM 成對評測；en→ja 常出現 keigo 誤用 |
| JaSocial（LREC 2026） | 不在允許名單 | LLM 在依社會地位調整日文敬語上有限制 |
| Kim et al., Efficient Terminology Integration（WMT24 參賽論文） | WMT 只採 findings | 用 trie 從訓練資料抽詞表再微調，在 WMT24 專利任務拿最高分 |
| Ouyang et al., CMU IWSLT 2025 系統（arXiv 2506.13143） | 系統論文；findings 已經摘要 | Wav2Vec2 加 Qwen2.5-7B 的串流系統 |
| Ollama v0.33.3 gemma4 音訊輸入（鏡像的 changelog 與 fork 的 commit） | 不是官方來源，而且有使用者回報失敗 | Ollama 支援 gemma4 的 WAV 與 input_audio 輸入，以及 `/v1/audio/transcriptions` |
| themoonlight.io、pulse.bot、MarkTechPost 等二手摘要 | 部落格或聚合站 | — |

**整體缺口**：
- 所有建議都沒有 VTuber 直播語料上的量測，日文來源的同步翻譯在 IWSLT 2024–2026 都沒有評測。
- T1 到 T3 的效益必須用 kikitori 自己的 erasure 與 COMET 實驗確認。
- Ollama 的 prefill、logprobs、ADAPTER、gemma4 音訊這四個功能，官方文件都不完整或互相矛盾，需要在本機逐一驗證。
