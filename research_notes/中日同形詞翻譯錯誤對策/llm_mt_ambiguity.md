# LLM 翻譯的詞彙歧義與斷詞錯誤：文獻調查（中↔日同形詞）

調查日期：2026-11-07。範圍：2023 年以後的方法，以 2024–2026 為主。本筆記只做調查，沒有改任何程式碼。

## 0. 問題與已知事實（kikitori 自己的觀察）

- 管線：Whisper ASR → Ollama（OpenAI 相容 API，temperature 0）上的本地 LLM。
- 錯誤案例：「我今天要吃豬排便當」被 qwen3.5:9b、qwen3.5:4b、HY-MT1.5-7B、HY-MT1.5-1.8B 都翻成「今日は豚の排泄物を主食にするんだ」。模型把句子切成 豬/排便/當，再把「排便」當成日文的「排便（defecation）」。這是**斷詞歧義和同形異義詞一起出錯**。
- 自家小測試（zh→ja 18 句陷阱加 6 句對照，ja→zh 14 句陷阱加 4 句對照）：
  - 指令改用來源語（中文）寫：zh→ja 陷阱正確數 9B 從 13/18 升到 16/18，4B 從 13 升到 15；未翻譯輸出 0 次。娘、勉強、愛面子 這類**詞義型**同形詞被修正。豬排便當 仍然錯（變成「レバー弁当」）。
  - 手動在詞之間插空格（「我 今天 要 吃 豬排 便當」）：**斷詞型**錯誤修好了（豚カツ弁当），但詞義型同形詞沒有改善。
  - 用英文指令時，qwen3.5:9b 在 18 句中有 11 句沒有翻譯日文，所以之前選了用目標語寫指令。
  - presence_penalty 0 和 1.5 沒有差別。一句一句加使用者詞彙表有效，但規模撐不住。

結論：這裡其實有**兩類錯誤**，需要的對策不同。
1. **斷詞型**（豬排|便當 被切成 豬|排便|當）：要在輸入端提供詞邊界或複合詞的提示。
2. **詞義型**（娘＝母親，勉強，愛面子）：要提供詞義知識，或讓模型以來源語的角度理解。

## 1. 來源政策與標示方式

依使用者後來更新的來源政策：

- **[top-tier 同儕審查]**：ACL、EMNLP、NAACL（Findings 會另外標「Findings」）、TACL、Computational Linguistics、TASLP、NeurIPS、ICML、ICLR、AAAI、IJCAI。
- **[WMT 官方 findings]**：只採 WMT shared task 的 findings/overview 論文。
- **[公司報告，未經同儕審查]**：知名公司或實驗室的技術報告與模型卡。結果一律視為**自報（self-reported）**。
- **[模型卡：僅事實]**：只用來確認授權、大小、發布日期、支援語言，不當作品質證據。
- 每個數字都會註明是「作者自報」還是「獨立評測」。標「推估」的是我自己的推論，沒有文獻支持。

**依政策排除、沒有引用為證據的來源**（之前查到的，在此列出）：

| 來源 | 排除原因 | 若可用，它會說什麼 |
|---|---|---|
| Doppelganger-JC（Kitamura et al., IJCNLP-AACL 2025），中日跨語同形詞 benchmark | AACL 系列不在允許名單 | 提出「homograph shortcut」：模型偏向它比較熟的語言的詞義 |
| Iyer, Chen & Birch, "Towards Effective Disambiguation for MT with LLMs"（WMT23 研究論文） | WMT 只採 shared task findings | 用相似句做 ICL、在歧義語料上微調，DiBiMT 最多提升 15 分 |
| Li, Ji & Tiedemann, "Test-Time Scaling of Reasoning Models for MT"（EACL 2026） | EACL 不在允許名單 | 一般推理模型增加思考量，增益小且很快飽和；用在 post-editing 比較有效 |
| "Linguistically Informed ChatGPT Prompts to Enhance Japanese-Chinese MT"（arXiv 2303.15587） | 只有 arXiv，非知名機構 | — |
| R1-T1、ReflectMT 等純 arXiv 的推理 MT 論文 | 只有 arXiv，非知名機構 | — |

因此下面寫「沒有 top-tier 證據」的地方，代表只有上表這類被排除的來源談過。

---

## 2. 問題 1：研究 LLM 翻譯中歧義、同形詞、斷詞錯誤的論文

### 2.1 跨語同形詞（false friends），含中日

- **StingrayBench**：Cahyawijaya et al., "Thank You, Stingray: Multilingual LLMs Can Not (Yet) Disambiguate Cross-Lingual Word Senses"，**[top-tier: Findings of NAACL 2025]**，<https://aclanthology.org/2025.findings-naacl.178>（全文 <https://arxiv.org/html/2410.21573v2>）
  - 涵蓋四個語對，**其中一個是中文–日文**。zh-ja 子集共 165 筆：51 筆真同源詞、114 筆 false friends。由中日母語者建立（作者自述）。
  - 測試的模型包含 Qwen-2.5 0.5B–32B、Aya-23 8B/35B、mT0、GPT-4o mini 等。
  - 發現（作者自報）：模型偏向**較高資源語言**的詞義；在 false friends 上整體**接近隨機**；規模變大通常能改善真同源詞的理解並減少偏差。論文**沒有測任何緩解方法**，prompt 都是英文 zero-shot。
  - 我讀到的文字沒有 zh-ja 的具體數字（在圖裡），所以 zh-ja 的偏差方向**我沒有核實到數字**。
  - 對 kikitori 的意義：「中文句子裡的『排便』被讀成日文詞義」正是這篇所說的跨語詞義偏差。不過我們的案例更複雜，因為多了斷詞錯誤。
- **DiBiMT**：Martelli et al., **[top-tier: Computational Linguistics 51(2), 2025]**，<https://aclanthology.org/2025.cl-2.1/>
  - 全人工整理的詞彙歧義 MT benchmark，涵蓋 8 個語言組合（主要是 English→X）。
  - 結論（作者自述）：標準 MT 評測設定**測不出**系統的消歧能力，需要專門的測試集。
  - 它**沒有**中日方向。我只讀到摘要，具體準確率沒有核實。
  - 對 kikitori 的意義：COMET/BLEU 平均分數不能代表同形詞錯誤率，**我們自建的陷阱句測試集這種作法是對的**，應該持續擴充。

### 2.2 方法類論文中的歧義分析

- **MAPS**（He et al., **[top-tier: TACL 2024]**）在 Zh→En 詞彙歧義測試集上做了人工評測，「正確消歧」的比例從 Rerank 基線的 61.5% 升到 MAPS 的 65.5%（作者自報，人工評測，來源是 ar5iv 全文 Table 5）。換算約減少 10% 的消歧錯誤，這是我的換算。詳見 §3.3。

### 2.3 斷詞本身

- 2023 年以後，**top-tier 會議裡沒有找到**「在 LLM MT 的 prompt 中加入中文分詞，會幫助還是傷害」的直接研究。
- 背景文獻是較早的 **Li et al., "Is Word Segmentation Necessary for Deep Learning of Chinese Representations?"**，**[top-tier: ACL 2019]**，<https://aclanthology.org/P19-1314>。在當時的神經模型（含 MT）上，char-based 一致優於 word-based（作者自報）。不過這是**預訓練 LLM 出現以前**的結果，談的是模型內部表示，不是「在 prompt 裡給斷詞提示」，不能直接套用到我們的情境。
- 結論：kikitori「插空格就修好斷詞錯誤」的觀察，**目前沒有 top-tier 文獻能支持或反駁**，要靠自己 A/B 測試。

---

## 3. 問題 2：prompt 與 decoding 技術

下表的「延遲」是相對於單次翻譯（1×），只計算 LLM 呼叫次數，不含 QE 模型。

| 技術 | 主要證據 | 小模型（4–9B）證據 | 額外 LLM 呼叫 | 適合 fast path（約 0.2 s）？ | 適合 final path？ |
|---|---|---|---|---|---|
| 指令語言（來源語、目標語、英文） | ICML 2023（GLM-130B）、HY-MT 官方模板 | 弱 | 0 | ✅ | ✅ |
| 字典增強（CoD、詞表注入） | EMNLP 2024、AAAI 2024、WMT23 術語 findings | 有（7B 級） | 0（字典事先準備好） | ✅ | ✅ |
| MAPS（關鍵詞、主題、範例，再加 QE 選擇） | TACL 2024 | 有（Alpaca-7B、Vicuna-7B） | 約 6–7 次；序列執行約 14× | ❌ | 精簡版可行 |
| Self-refine / LLM post-edit（TEaR） | Findings NAACL 2025 | Mistral-7B 幾乎沒有增益 | 2 | ❌ | 效益存疑 |
| QE 重排序、MBR | EMNLP 2023 | 7B 增益大，但要 N=20–50 個樣本 | N−1 | ❌ | 小 N 版可考慮 |
| 推理、thinking 模型 | Findings ACL 2025（DRT，文學、微調）、WMT25 | 沒有 top-tier 證據顯示一般小模型開 thinking 有幫助 | 1 次，但輸出 token 暴增 | ❌ | 不建議 |
| 檢索相似範例（few-shot 選擇） | Findings ACL 2023、ACL 2023 | 舊模型 | 0（只要檢索） | 可行，但 prompt 變長 | ✅ |

### 3.1 指令語言（來源語、目標語、英文）

- **Zhang, Haddow & Birch, "Prompting LLM for MT: A Case Study"**，**[top-tier: ICML 2023]**，<https://arxiv.org/abs/2301.07069>（作者自報，GLM-130B，語言為英、德、中）
  - Zero-shot 時**英文模板平均最好**（COMET 38.78）。德文模板大幅退化（-26.15），因為德文不是 GLM 的主要預訓練語言。中文模板**在翻譯成中文時有幫助**。
  - 作者的結論是：「language-specific templates mainly work when translating into languages LLMs are pretrained on」。
  - 也記錄了 off-target 問題：輸出空白，或翻成錯誤的語言。
  - 限制：模型很舊（2022 年的 130B），**沒有日文**，也沒有測「來源語模板」這個條件。
- **Enomoto et al., "A Fair Comparison without Translationese: English vs. Target-language Instructions for Multilingual LLMs"**，**[top-tier: NAACL 2025 short]**，<https://aclanthology.org/2025.naacl-short.55>
  - 去除 translationese 之後再比較，英文指令的優勢「not overwhelming」。在詞彙簡化任務上，目標語指令往往比較好（作者自報；模型為 Llama3-8B、Qwen2-7B、Mistral-Nemo 等級；語言含 ja、zh）。
  - **它沒有測 MT 任務**，只能當旁證。
- **官方作法 [公司報告/模型卡]**：Tencent HY-MT1.5 與 Hunyuan-MT 官方模板規定，**只要語對含中文（ZH⇔XX），就用中文指令**，例如「将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：」。非中文語對才用英文。所以 zh→ja 時，官方指令其實就是**來源語指令**（<https://huggingface.co/tencent/HY-MT1.5-7B>）。Seed-X 則用英文指令，再在結尾加目標語言標籤 `<ja>`。
- **小結**：沒有任何 top-tier 研究直接在 4–9B 模型上比較 zh→ja 的「來源語指令和目標語指令」。kikitori 的自測結果（來源語指令 +2～3 題、未翻譯 0 次）與 HY-MT 官方作法一致，**值得保留**，但要清楚標示這是自測結論，樣本只有 18 句。可能的解釋是：用來源語說明可以把模型「讀原文的語言」固定在中文，減少用日文詞義去讀漢字的機會。這是**推測**，沒有文獻證據。

### 3.2 字典增強 prompt（Chain-of-Dictionary 等）

- **CoD**：Lu et al., "Chain-of-Dictionary Prompting Elicits Translation in LLMs"，**[top-tier: EMNLP 2024]**，<https://aclanthology.org/2024.emnlp-main.55/>（作者自報，主要用 GPT-3.5-Turbo，FLORES-200）
  - 作法：先抽出來源句的關鍵詞，再把它們的多語字典鏈（來源語→目標語→法、德、葡）接在 prompt 前面。作者提到，**字典條目照原句出現的順序排列比打亂好**。
  - 增益集中在**低資源語言和 X→En**。例如 X→En chrF++：zho_Hans 23.39→55.13，jpn_Jpan 22.62→49.73。但這兩個基線異常地低，可能和基線的輸出問題有關，**數字解讀要小心**。
  - **對高資源語言的 En→X 經常是負面的**：zho_Hans chrF++ 30.99→28.92，jpn_Jpan 33.95→31.96；200 個 En→X 方向中有 65 個退化。
  - 成本：推論時間最多 1.8×，prompt 長度最多 3×（作者在 limitations 自述）。
  - 小模型：BLOOM-7B 只做了 X→En，增益比 ChatGPT 小。
  - 對 kikitori 的意義：**不要把「整句關鍵詞」都塞進 prompt**，只在命中已知陷阱詞時注入少量條目，比較合理。
- **IdiomKB**：Li et al., "Translate Meanings, Not Just Words"，**[top-tier: AAAI 2024]**，<https://doi.org/10.1609/aaai.v38i17.29817>
  - 先用大模型建立多語慣用語知識庫，再把慣用語的**比喻意義**提供給 **BLOOMZ-7.1B、Alpaca-7B、InstructGPT-6.7B 這類小模型**，改善慣用語翻譯（作者自報，評測用 GPT-4 與人工）。
  - 這是「**離線建知識庫、線上 0 次額外呼叫**」的直接先例，結構和中日同形詞表很像。
- **DiPMT**：Ghazvininejad, Gonen & Zettlemoyer（Meta AI），**[公司報告，未經同儕審查，arXiv 2302.07856]**。把部分輸入詞的雙語字典譯詞放進 prompt，在低資源與 out-of-domain 情境優於基線（自報，OPT/BLOOM）。我沒有讀到具體數字。
- **WMT23 Terminology shared task**：Semenov et al., **[WMT 官方 findings]**，<https://aclanthology.org/2023.wmt-1.54>
  - 涵蓋 zh→en、en→cs、de→en。加入術語字典能提升品質，**但從參考譯文加入等量的其他資訊，效果也差不多**（findings 摘要）。
  - 意涵：字典提示的增益**有一部分可能只是「多給資訊」**，不一定代表專門的消歧效果。我們做 A/B 時，應該加一組「注入無關提示」的對照組。
- **HY-MT1.5 官方的「術語干預」模板 [模型卡]**：「参考下面的翻译：{source_term} 翻译成 {target_term}」，接著放一般翻譯指令。這是這個模型**訓練時就見過的格式**（它的 RL 有 terminology-aware reward，見 Hunyuan-MT 技術報告）。對 HY-MT 來說，這可能是最穩的注入格式，但這是**推測**，沒有獨立評測。
- **小結**：字典注入**不需要額外呼叫 LLM**，適合 fast path；在 7B 級模型上有 top-tier 證據（IdiomKB）。風險是過度注入會讓高資源方向退化（CoD 的 En→X 結果）。
  - 關鍵在於：**注入的是複合詞條目（豬排→豚カツ，便當→弁当），不是單一的同形詞**。複合詞條目本身就隱含了詞邊界，可以同時處理斷詞型錯誤。這一點是**推論**，要實測。

### 3.3 MAPS（Multi-Aspect Prompting and Selection）

- He et al., "Exploring Human-Like Translation Strategy with LLMs"，**[top-tier: TACL 2024]**，<https://aclanthology.org/2024.tacl-1.13>，程式碼 <https://github.com/zwhe99/MAPS-mt>。數字是作者自報，來自 ar5iv 全文。
- 作法：LLM 先從來源句挖出關鍵詞對譯、主題、相似範例三種知識，各自產生一個翻譯；連同基線一共約 4 個候選；最後用 QE（COMET-QE）選一個。
- 模型：text-davinci-003、**Alpaca-7B、Vicuna-7B**。共 11 個方向，包含 En↔Zh、En↔Ja，**不含 zh↔ja**。
- 7B 增益（相對於同樣使用 QE 選擇的 Rerank 基線，COMET）：Alpaca-7B +0.7～+2.8，Vicuna-7B +0.5～+2.4。
- 歧義：Zh→En 詞彙歧義測試集的人工正確率 61.5%→65.5%。
- **延遲**：序列執行時，MAPS 約 **14×**，Rerank 約 11×，Three-in-One 約 3×；平行化可以把 MAPS 的時間減少約 66%。用單一 JSON 輸出版本挖知識比較快（2.3 s 對 3.7 s／句），但約 2% 的 JSON 無效。
- **適用性**：完整 MAPS 不可能放進 fast path。**final path 可以用精簡版**：只做「關鍵詞對譯」這一種知識（作者分析指出它有助於處理歧義），只產生 2 個候選，再選一個。

### 3.4 Self-refine、LLM post-editing、self-correction

- **TEaR**（Translate, Estimate, and Refine）：Feng et al., **[top-tier: Findings of NAACL 2025]**，<https://aclanthology.org/2025.findings-naacl.218>（作者自報，資料來自 arXiv v3 全文）
  - 3 個模組（翻譯、估計、修正），推估約 3 次呼叫。
  - 主實驗用 GPT-3.5、Gemini-Pro、Claude-2。**小模型只測了 Mistral-7B**：在 50 句 WMT23 Zh-En 上，COMET 76.54→76.58（+0.04），BLEURT +0.47。
  - 可能退化：Gemini-Pro 的 Zh-En BLEU 22.17→19.84。多輪迭代反而常常變差，一輪最好。
  - 相對於 Contrastive Translate（CT）與 SCoT 有優勢（GPT-3.5，平均 COMET 各約 +0.7、+2.2）。
- **Ki & Carpuat**, "Guiding LLMs to Post-Edit MT with Error Annotations"，**[top-tier: Findings of NAACL 2024]**
  - 用 LLaMA-2 做 prompt post-editing，TER/BLEU/COMET 有改善，但「細粒度錯誤回饋」的效益不明確；要靠微調才能善用細粒度回饋（作者自報）。
- **小結**：在 4–9B 模型上，「自己檢查自己」的 **top-tier 證據顯示增益接近 0**，而且有退化風險。**不建議放在 fast path。** 放在 final path 也只有在「有外部訊號指出錯誤」時才有意義，例如規則偵測到可疑詞（見建議 R2）。

### 3.5 QE 重排序與 MBR decoding

- **Farinhas, de Souza & Martins**, "An Empirical Study of Translation Hypothesis Ensembling with LLMs"，**[top-tier: EMNLP 2023]**，<https://arxiv.org/html/2310.11430>（作者自報，WMT22 En↔De/Ru/Cs/Uk）
  - LLaMA-7B En-De COMET：greedy 77.33，CometKiwi 排序（N=50）83.04，MBR（N=50）84.06。Alpaca-7B：greedy 76.67，MBR 84.54。ChatGPT 的增益小很多（87.01→88.25）。
  - **越弱的模型受益越多**，而且候選集能減少幻覺（LLaMA-7B 在擾動下的幻覺率 EN-CS 從 10.2% 降到 2.3%，用排序法）。
  - 代價：N=20–50 個樣本要**用取樣產生**，和 kikitori 的 temperature 0 衝突；延遲也是 N 倍。
- **Tomani et al.**, "Quality-Aware Translation Models"，**[top-tier: ACL 2024]**，Google 實習研究：讓模型自己預測品質，MBR 可以把候選數大幅減少，速度快兩個數量級（作者自報）。不過這是**傳統 NMT 模型**，要重新訓練，不適用於直接拿現成 LLM 來用。
- **WMT25 findings** 統計：11 支隊伍用了 MBR，9 支單獨用了 QE。參賽系統普遍這樣做，但這些是**離線**系統。
- **QE 模型授權（關鍵風險）[模型卡：僅事實]**：
  - Unbabel **CometKiwi（wmt22-cometkiwi-da）是 CC-BY-NC-SA-4.0，禁止商用**，不能用在 kikitori（<https://huggingface.co/Unbabel/wmt22-cometkiwi-da>）。xCOMET 系列我沒有逐一核實，推測同樣是 NC，使用前要確認。
  - Google **MetricX-24 是 Apache-2.0**，hybrid 版支援 reference-free QE（`--qe`），以 mT5 為基礎，有 Large、XL、XXL 三種大小（<https://github.com/google-research/metricx>）。各版參數量我沒有核實。它需要 PyTorch/transformers 執行環境，**和 kikitori 現有的 onnxruntime 管線不同**，要評估新增依賴與 VRAM。
- **與 temperature 0 相容的變體**：不用取樣，改用**不同的 prompt 產生候選**（MAPS 的作法），例如「一般 prompt」和「加詞表提示或分詞提示的 prompt」，再用 QE 選一個。輸出仍然是確定性的。
- **未知**：QE 模型能不能可靠地抓到「豚の排泄物」這類**語意完全錯但流暢**的同形詞錯誤，**我沒有找到 top-tier 證據**。只能用我們的陷阱句測試集實測。

### 3.6 推理與 thinking 模型

- **DRT**：Wang et al.（WeChat AI, Tencent），"DRT: Deep Reasoning Translation via Long Chain-of-Thought"，**[top-tier: Findings of ACL 2025]**，<https://aclanthology.org/2025.findings-acl.351/>
  - 針對含**明喻、隱喻的文學句**，合成長 CoT 翻譯資料，再**微調** Qwen2.5、Llama-3.1。作者自報優於未微調的模型。
  - 它是「為翻譯專門訓練推理」，**不是**「把一般模型的 thinking 打開」，而且是文學領域。我沒有讀到具體數字（摘要沒有寫）。
- **WMT25 findings**（Kocmi et al., **[WMT 官方 findings]**，<https://aclanthology.org/2025.wmt-1.22>）
  - 人工評測整體最好的是 **Gemini 2.5 Pro，而且收集譯文時開著 thinking**。官方腳註寫：「it is unclear how much this contributed」。
  - 主辦方**關閉了** Qwen3-235B 的 reasoning，以避免收集錯誤。
  - 換句話說，連官方都沒辦法分離出 thinking 的貢獻。
- **Seed-X**（ByteDance，**[公司報告，未經同儕審查]**，arXiv 2507.13618）：SFT 資料裡包含語言學家寫的 CoT 標註（作者自報 CoT prompt 對 SFT 有幫助），但沒有清楚說明報告的分數是不是在推論時開了 CoT。
- **小結**：沒有 top-tier 證據顯示「4–9B 一般模型開啟 thinking」可以改善 zh↔ja 句級翻譯，而輸出 token 會多出數倍到數十倍（推估）。**fast path 和 final path 都不建議。** 被排除的 EACL 2026 論文也指向「一般推理模型增益小且很快飽和」。

### 3.7 檢索相似範例（few-shot selection）

- **Agrawal et al.**, "In-context Examples Selection for MT"，**[top-tier: Findings of ACL 2023]**，<https://aclanthology.org/2023.findings-acl.564>（Meta 共同作者）：
  - **一個雜訊範例就可能嚴重拉低品質**。
  - 以 n-gram 重疊挑選範例能穩定提升，在 4 個 out-of-domain 資料集中有 2 個勝過 kNN-MT（作者自報）。
- **Vilar et al.**, "Prompting PaLM for Translation"，**[top-tier: ACL 2023]**，Google：**範例的品質比它和輸入的詞彙、語意相似度更重要**（作者自報）。
- 對 kikitori 的意義：如果要用 few-shot，應該從**人工確認過的陷阱句對**裡，挑出和輸入**有相同陷阱詞**的那一句當範例。這其實是「詞表注入」的另一種形式，但 prompt 會變得更長。不建議作為第一步。

---

## 4. 問題 3：較新的 MT 專用開源模型（≤14B）

### 4.1 獨立評測：WMT 官方人工評測（只有 ja→zh，沒有 zh→ja）

**WMT25 ja→zh**，**[WMT 官方 findings，獨立人工評測，MQM，0 為最好，越負越差]**，<https://aclanthology.org/2025.wmt-1.22>。表格是我用 pdftotext 從 PDF 抽出來的，版面錯位，**排名只能算近似**；分數對應我已盡量核對。

| 系統 | MQM |
|---|---|
| Human（人工參考譯文） | −3.5 |
| Gemini-2.5-Pro | −4.4 |
| Algharb | −5.8 |
| Claude-4 | 約 −5.9 |
| **Shy-hunyuan-MT**（即 Hunyuan-MT-7B） | **−6.1** |
| GPT-4.1 | −6.2 |
| DeepSeek-V3 | −8.1 |
| Qwen3-235B（關閉 reasoning） | −10.9 |
| **TowerPlus-9B[M]** | **−13.3** |

- TowerPlus-9B 標 [M]：它是用通用 prompt 呼叫，不是它自己的 MT 指令，可能因此吃虧。
- 官方指出：Hunyuan-MT 在**自動評測（AutoRank）**中 31 個語對有 30 個拿第一，但**人工評測明顯較低**，可能是因為它用 XCOMET 與 GEMBA 當 RL 訓練訊號。官方同時說它仍然是**最好的 constrained 系統**。
- 評語：**7B 的 Hunyuan-MT 在 ja→zh 人工評測接近 GPT-4.1 和 Claude-4，比 Qwen3-235B 好**。這是對 7B 級 MT 專用模型最有力的獨立證據。
- 但 **zh→ja 不在 WMT25 的人工評測語對裡**。而且我們的實測顯示 HY-MT1.5 一樣會在「豬排便當」出錯，所以**換模型本身不能解決斷詞陷阱**。

**WMT24 ja→zh**，**[WMT 官方 findings]**，<https://aclanthology.org/2024.wmt-1.1>：Claude-3.5 與人工參考譯文同在第一群；Unbabel-Tower70B、Gemini-1.5-Pro、GPT-4 接著在後。我從抽出的文字裡**沒有看到 ≤14B 的開源 MT 專用模型名列前段**（Aya23 的大小在表中沒有標示）。

### 4.2 候選模型一覽

授權與規格依官方模型卡；品質數字除了 WMT 之外都是作者自報。

| 模型 | 大小 | zh、ja | 授權 [模型卡] | 與商用、MIT 專案的相容性 | 品質證據 | GGUF/Ollama |
|---|---|---|---|---|---|---|
| **Hunyuan-MT-7B / Chimera-7B**（Tencent，2025-09） | 7B | ✅（33 語） | Tencent Hunyuan Community License | ⚠️ 同 HY-MT1.5（見下） | WMT25 人工 ja→zh −6.1（獨立）；技術報告 arXiv 2509.05209（自報，沒有 zh-ja 分項） | 官方 FP8；GGUF 請見 HF |
| **HY-MT1.5-1.8B / 7B**（Tencent，2025-12-30） | 1.8B、7B | ✅ | **Tencent HY Community License**：**不適用於歐盟、英國、南韓**；月活超過 1 億要另外申請；輸出不得用於改進其他模型；散布時要附授權與 Notice | ⚠️ **地域限制**會讓「全球散布的 MIT 桌面 app」出現合規問題，需要使用者自行判斷 | 官方說是「WMT25 冠軍模型的升級版」（自報）；技術報告 arXiv 2512.24092 | ✅ 官方 GGUF（`hf.co/tencent/HY-MT1.5-7B-GGUF:Q4_K_M`） |
| **Seed-X-PPO-7B**（ByteDance Seed，2025-07） | 7B（Mistral 架構） | ✅（28 語） | **OpenMDW** | 推測屬寬鬆授權（OpenMDW 是 Linux Foundation 的寬鬆模型授權），**我沒有逐條核實**，使用前要讀 LICENSE.openmdw | 技術報告 arXiv 2507.13618（自報，**沒有 zh-ja 數字**）；建議 beam=4 | 官方只有 GPTQ/AWQ；官方**不建議用非官方量化**；沒有官方 GGUF |
| **TranslateGemma 4B/12B/27B**（Google，2026-01） | 4B、12B | 55 語（模型卡沒有逐一列出 zh、ja，**未核實**） | **Gemma Terms of Use** | 可商用，但有使用限制政策，散布衍生物要附條款；不是 OSI 授權 | 模型卡（自報）：WMT24++ COMET 4B 81.6、12B 83.5；WMT25（10 語）MQM 12B 7.94。技術報告 arXiv 2601.09012 | 社群 GGUF 未核實；**需要特殊的 chat template**（`source_lang_code`、`target_lang_code`），經 Ollama 呼叫可能要自訂 template |
| **Tower+ 9B**（Unbabel，2025-06） | 9B（Gemma-2） | ✅ | 模型卡寫 **CC-BY-NC(-SA)-4.0**（模型卡前後不一致） | ❌ **非商用** | WMT25 ja→zh 人工 −13.3（獨立，以通用 prompt 呼叫） | — |
| **GemmaX2-28-9B**（Xiaomi） | 2B、9B | ✅（28 語） | Gemma | 同 Gemma 條款 | **[top-tier: NAACL 2025]**（作者自報優於 TowerInstruct、X-ALMA，和 GPT-4-turbo 相當），我沒有看到 ja 分項 | 社群量化，未核實 |
| **X-ALMA**（JHU/Microsoft） | 13B base + 語言模組 | ja、zh 在第 6 組 | 模型卡標 MIT，但 base 是 Llama-2-13B | ⚠️ 可能還受 Llama 2 授權約束（**未核實**） | **[top-tier: ICLR 2025]**（自報）。**以英文為中心**，每組都含英文，**zh↔ja 直譯不受支援**（未核實，依架構推論） | — |
| **Qwen-MT**（Alibaba，2025-07） | — | ✅ | **只有 API，沒有開放權重** | ❌ 不能本地執行 | — | ❌ |

**模型小結**：
- 在獨立人工評測中，7B 級最強的是 Hunyuan 系列（ja→zh），但它的授權有地域限制，而且已知也會掉進豬排便當這個陷阱。
- 授權比較乾淨的候選是 **Seed-X-PPO-7B（OpenMDW）**和 **TranslateGemma-4B/12B（Gemma 條款）**。兩者在 zh↔ja 都**沒有獨立評測**，只能拿我們的陷阱句測試集來測。
- 注意 HY-MT 官方建議的 decoding 是 temperature 0.7、top_p 0.6、top_k 20、repetition_penalty 1.05，和 kikitori 的 temperature 0 不同。兩種設定對陷阱句的影響**沒有測過**。

---

## 5. 問題 4：把來源語分析（分詞、詞性、詞義）當作 LLM MT 的輸入

- **GrammaMT**，**[top-tier: ACL 2025 long]**，<https://aclanthology.org/2025.acl-long.1447/>
  - 在 prompt 中提供逐詞標註（Interlinear Glossed Text：詞素加詞義），分成 gloss-shot、chain-gloss、model-gloss 三種策略，不需要訓練。
  - 對低資源語言有幫助。作者指出**如果 gloss 是正確的**，最多可以提升 17 BLEU 以上（自報）。
  - 意涵：**增益取決於分析是否正確**；分析出錯就會把錯誤帶進翻譯。這篇以低資源語言為主，不是中日。
- **MAPS**（TACL 2024）：讓 LLM 自己先抽關鍵詞並配對譯詞，本質上就是「來源語詞彙分析」，在 7B 模型上有正面效果（見 §3.3），但要多一次 LLM 呼叫。
- **CoD**（EMNLP 2024）：關鍵詞是用 LLM 抽的；高資源的 En→X 常常退化（見 §3.2）。
- **分詞與詞性標記本身**：**沒有 top-tier 證據**。ACL 2019（Li et al.）是 LLM 出現以前的研究，結論是 char-based 比 word-based 好，不能推論到「在 prompt 中給分詞提示」。
- **綜合判斷**：
  - **有證據**的作法：「詞義或譯詞層級的提示，而且是正確的提示」（IdiomKB、GrammaMT 的 gold gloss、MAPS 的 keywords）。
  - **沒有證據**的作法：「純分詞、詞性提示」。
  - kikitori 的自測顯示，插空格可以修好斷詞型錯誤，修不好詞義型錯誤，這和上面的判斷一致：分詞資訊只能處理邊界問題。
  - 風險在分詞器本身：豬排便當 剛好是分詞器也可能切錯的例子，例如詞典裡有「排便」而沒有「豬排」時。繁體中文的分詞品質也要另外驗證。

---

## 6. 給 kikitori 的建議（依優先序）

> 「預期效益」除了有標文獻的部分，都是**推估**，要用現有的 18+6 / 14+4 陷阱句測試集驗證。驗證時建議額外加一組「注入無關提示」的對照（WMT23 術語 findings 的教訓），並且把陷阱句測試集擴充到 50 句以上，可以從 StingrayBench zh-ja 子集挑句子，但要先確認它的資料授權。

### R1（fast path，第一優先）：保留來源語指令，並在命中時自動注入「複合詞優先」的詞表提示

- **作法**：
  1. 維持中文指令（zh→ja）與日文指令（ja→zh）。
  2. 由專案維護一份**共用的** zh↔ja 易錯詞表，分兩類：
     - 複合詞錨點，例如 豬排→豚カツ、便當→弁当；
     - 詞義型 false friends，例如 娘→母親、勉強→「勉強する」以外的正確譯法、愛面子→…。
  3. 對來源句做**最長匹配，複合詞優先**。只有命中時，才用 HY-MT 官方的「参考下面的翻译：A 翻译成 B」格式（Qwen 也用同樣格式）注入 1–3 條。
  4. 和現在「使用者逐詞詞表」的差別：這是**隨產品一起發布的共用詞表**，加上匹配規則，使用者不用自己維護。
- **證據**：IdiomKB（AAAI 2024，7B 模型受益）；CoD（EMNLP 2024，有正面也有反面結果）；WMT23 術語 findings（字典有幫助）；HY-MT 的術語干預格式是官方支援的。
- **預期效益**：詞表涵蓋到的陷阱句（含 豬排便當 這種斷詞型）應該接近全部修正，因為複合詞條目隱含了詞邊界（推估）。詞表沒涵蓋的句子不會改善。
- **延遲**：0 次額外 LLM 呼叫。匹配是毫秒級；prompt 多 20–60 tokens，prefill 增加有限（推估，需要實測）。
- **風險**：
  - 涵蓋率有上限。
  - 同一個字串在不同語境的意思不同，例如「娘」在「姑娘」「娘子」裡。所以匹配要以**較長的詞優先**，並準備排除清單。
  - 注入過多會傷到流暢度。CoD 在高資源 En→X 有 65/200 個方向退化，所以每句最多注入幾條要設上限。
  - 詞表本身的維護成本。

### R2（final path）：用 2 個 prompt 變體產生候選，以規則或 QE 選擇（精簡版 MAPS）

- **作法**：
  - final 句同時產生兩個候選：A 是一般 prompt，B 是加了詞表或分詞提示的 prompt。都用 temperature 0，所以結果仍是確定性的。
  - 選擇分兩階段。
  - 第一階段（便宜）：規則偵測。例如目標語輸出出現「排泄、排便」這類詞，但來源詞表標記該位置屬於複合詞，就判定這個候選不合格。
  - 第二階段（選用）：用 **MetricX-24 hybrid 的 QE 模式（Apache-2.0）**打分。**不要用 CometKiwi 或 xCOMET**，它們是 NC 授權。
- **證據**：MAPS（TACL 2024）在 Alpaca、Vicuna-7B 上比 Rerank 多 +0.5～+2.8 COMET，Zh→En 消歧正確率 61.5→65.5%；Farinhas（EMNLP 2023）指出越弱的模型從候選選擇中受益越多，也能減少幻覺。
- **延遲**：多 1 次生成；Ollama 若能平行處理，wall-time 約增加 0–1×（推估）。加上 QE 模型推論。完整 MAPS 是 14×，這個精簡版約 2×。
- **風險**：
  - MetricX 要引入 PyTorch/transformers 和額外 VRAM，**新增依賴前要先問使用者**。
  - QE 能否抓到「流暢但語意錯」的同形詞錯誤**沒有文獻證據**，要實測。
  - 兩個候選都錯的時候沒有幫助。
  - 不建議用 4–9B 模型自己 self-refine：TEaR 在 Mistral-7B 上只有 +0.04 COMET，而且有退化案例。

### R3（實驗性，先在 final path 做 A/B）：分詞結果當「附加提示」，不改寫原文

- **作法**：用中文分詞器產生「分詞參考：我/今天/要/吃/豬排/便當」，**附加**在原句後面，不要用空格取代原句。日文來源則可以考慮形態素分析。
- **證據**：**沒有 top-tier 證據**；GrammaMT（ACL 2025）間接說明「提示正確才有用」。支持這個方向的只有自測：插空格修好了斷詞型錯誤。
- **預期效益**：修正詞表沒涵蓋的斷詞型錯誤（推估）。對詞義型錯誤沒有幫助，這點自測已經證實。
- **延遲**：分詞器在 CPU 上是毫秒級（推估），沒有額外 LLM 呼叫。
- **風險**：
  - 分詞器自己也可能切錯（繁體中文尤其要驗證），會把錯誤帶進翻譯。
  - 需要新增依賴，**要先問使用者並固定版本**。
  - 可能會讓對照句退化，所以要用 6+4 句對照組監控。

### R4（模型評估，不改管線）：把授權乾淨的 MT 專用模型加入陷阱句測試

- **作法**：在現有測試腳本中加入 **Seed-X-PPO-7B**（OpenMDW；要找可信的量化版本或自行轉換）和 **TranslateGemma-4B/12B**（Gemma 條款；要處理它的專用 chat template）。HY-MT 繼續作為參照。
- **證據**：WMT25 ja→zh 人工評測顯示 7B 的 MT 專用模型（Hunyuan-MT）可以接近 GPT-4.1；但 zh→ja **沒有獨立評測**，而 Seed-X 與 TranslateGemma 的 zh-ja 能力只有自報。
- **預期效益**：可能提升整體流暢度與 ja→zh 品質。對 豬排便當 這種斷詞陷阱**不要期待**換模型就能解決，HY-MT 已經示範失敗。
- **延遲**：跟目前的 7B、4B 相同等級。TranslateGemma-4B 有機會比 qwen3.5:4b 更快或更好，但未知。
- **風險**：
  - 授權：HY-MT 有歐盟、英國、南韓的地域排除；Tower+ 是非商用。
  - Ollama 的 template 相容性。
  - 改用 temperature 0 偏離官方建議的參數。

### 不建議

- fast path 開 thinking 或 reasoning（沒有證據支持，輸出 token 暴增）。
- 用取樣做 MBR，N=20–50（和 temperature 0、延遲預算都衝突）。
- 4–9B 模型的 self-refine 迴圈（top-tier 證據顯示增益接近 0，且有退化）。
- 改回英文指令（自測顯示 11/18 句沒有翻譯）。

---

## 7. 主要參考來源

**Top-tier 同儕審查**
- Cahyawijaya et al. 2025, StingrayBench, Findings of NAACL 2025 — <https://aclanthology.org/2025.findings-naacl.178>
- Martelli et al. 2025, DiBiMT, Computational Linguistics 51(2) — <https://aclanthology.org/2025.cl-2.1/>
- He et al. 2024, MAPS, TACL 12 — <https://aclanthology.org/2024.tacl-1.13>
- Lu et al. 2024, Chain-of-Dictionary, EMNLP 2024 — <https://aclanthology.org/2024.emnlp-main.55/>
- Li et al. 2024, IdiomKB, AAAI 2024 — <https://doi.org/10.1609/aaai.v38i17.29817>
- Feng et al. 2025, TEaR, Findings of NAACL 2025 — <https://aclanthology.org/2025.findings-naacl.218>
- Ki & Carpuat 2024, Post-Edit MT with Error Annotations, Findings of NAACL 2024
- Farinhas et al. 2023, Translation Hypothesis Ensembling with LLMs, EMNLP 2023 — <https://arxiv.org/abs/2310.11430>
- Tomani et al. 2024, Quality-Aware Translation Models, ACL 2024 — <https://arxiv.org/abs/2310.06707>
- Zhang, Haddow & Birch 2023, Prompting LLM for MT, ICML 2023 — <https://arxiv.org/abs/2301.07069>
- Enomoto et al. 2025, English vs Target-language Instructions, NAACL 2025 (short) — <https://aclanthology.org/2025.naacl-short.55>
- Agrawal et al. 2023, In-context Examples Selection for MT, Findings of ACL 2023 — <https://aclanthology.org/2023.findings-acl.564>
- Vilar et al. 2023, Prompting PaLM for Translation, ACL 2023 — <https://aclanthology.org/2023.acl-long.859>
- Wang et al. 2025, DRT, Findings of ACL 2025 — <https://aclanthology.org/2025.findings-acl.351/>
- Cui et al. 2025, GemmaX2 / Practical-scale Multilingual MT, NAACL 2025 — <https://aclanthology.org/2025.naacl-long.280>
- Xu et al. 2025, X-ALMA, ICLR 2025
- Li et al. 2019, Is Word Segmentation Necessary…, ACL 2019 — <https://aclanthology.org/P19-1314>
- GrammaMT, ACL 2025 — <https://aclanthology.org/2025.acl-long.1447/>

**WMT 官方 findings**
- Kocmi et al. 2025, Findings of WMT25 General MT — <https://aclanthology.org/2025.wmt-1.22>
- Kocmi et al. 2024, Findings of WMT24 General MT — <https://aclanthology.org/2024.wmt-1.1>
- Semenov et al. 2023, Findings of WMT23 Terminology Task — <https://aclanthology.org/2023.wmt-1.54>

**公司報告與模型卡（未經同儕審查；品質數字為自報）**
- Hunyuan-MT Technical Report (Tencent) — <https://arxiv.org/abs/2509.05209>；<https://huggingface.co/tencent/Hunyuan-MT-7B>
- HY-MT1.5 (Tencent) — <https://huggingface.co/tencent/HY-MT1.5-7B>；授權：Tencent HY Community License（HF 上的 LICENSE）
- Seed-X (ByteDance Seed) — <https://arxiv.org/abs/2507.13618>；<https://huggingface.co/ByteDance-Seed/Seed-X-PPO-7B>
- TranslateGemma (Google) — <https://huggingface.co/google/translategemma-4b-it>；<https://blog.google/innovation-and-ai/technology/developers-tools/translategemma/>
- Tower+ (Unbabel) — <https://huggingface.co/Unbabel/Tower-Plus-9B>
- DiPMT (Meta AI) — <https://arxiv.org/abs/2302.07856>
- MetricX-24 (Google) — <https://github.com/google-research/metricx>
- CometKiwi 授權 — <https://huggingface.co/Unbabel/wmt22-cometkiwi-da>
