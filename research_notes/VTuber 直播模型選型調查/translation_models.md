# VTuber 直播翻譯模型選型：日→繁中（zh-TW）／日→英 本地 MT 模型與提示策略

> 調查日期：2026-11-05。標記說明：**[論文宣稱/self-reported]**＝模型開發者自己的報告或 model card；**[獨立評測/independent]**＝WMT 等第三方主辦的評測；**[未經同儕審查]**＝arXiv 預印本。
> 方法備註：WMT25 findings、Seed-X、TranslateGemma、Sony EMNLP 2025 這幾篇 PDF 是下載後用 `pdftotext -layout` 抽出文字再讀的。WMT25 排名表在雙欄排版下「名次欄」錯位，**分數與系統名稱的對應有在兩張表（全部系統／受限系統）之間交叉核對**，但名次區間（rank range）不可靠，所以下文只列分數順序，不列名次區間。

---

## Q1. 2025–2026 年哪些開放權重模型在 ja→zh、ja→en 上有最強的品質證據？

### Takeaway
目前唯一有**獨立人工評測**、而且直接涵蓋 ja→zh 的開放權重小模型是 **Tencent Hunyuan-MT-7B**（WMT25 的 `Shy-hunyuan-MT`）：在 WMT25 ja→zh 的 MQM 人評裡，它跟 GPT-4.1、Claude-4 分數相近，明顯優於 Qwen3-235B 和 TowerPlus-9B。後繼的 HY-MT1.5（1.8B/7B）只有自報的自動指標。Qwen3.5 4B/9B 與 TranslateGemma（4B/12B/27B）在 ja↔zh 都**沒有**獨立人評；TranslateGemma 在 ja→en 的人評甚至比原版 Gemma 3 退步。

### Cited Findings

**WMT25 General MT（獨立評測，test set 來自 2025 年新收集的資料，四個 domain：news／social／speech／literary）**
- WMT25 的 ja→zh（日→簡中）人評用的是 **MQM**（en→ko 也是），其他語言對用 ESA [獨立評測] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- ja→zh 的 MQM 分數，數值越接近 0 越好，依分數排序：Human 參考譯文 −3.5、Gemini-2.5-Pro −4.4、Algharb −5.8、Claude-4 −5.9、**Shy-hunyuan-MT −6.1**、GPT-4.1 −6.2、Wenyiil −6.9、CommandA-WMT −7.7、DeepSeek-V3 −8.1、Kaze-MT −8.6、Mistral-Medium −10.0、In2x −10.0、**Qwen3-235B −10.9**、GemTrans −10.9、NTTSU −11.3、Yolu −12.6、**TowerPlus-9B −13.3**、IRB-MT −13.9、Laniqo −18.3 [獨立評測，WMT25 ja→zh，MQM] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- en→ja 的 ESA 分數，數值越高越好：Human 89.2、Gemini-2.5-Pro 85.8、Algharb 85.7、Mistral-Medium 84.8、Wenyiil 84.4、GPT-4.1 83.7、CommandA-WMT 82.2、**Shy-hunyuan-MT 79.9**、DeepSeek-V3 79.3、Claude-4 79.3、UvA-MT 79.3、ONLINE-B 78.0、In2x 77.8、GemTrans 76.2、KIKIS 76.2、Systran（EuroLLM-9B 微調）75.6、NTTSU 73.3、Yolu 72.6、Laniqo 67.8 [獨立評測，WMT25 en→ja，ESA] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- WMT25 **沒有 ja→en 方向**（來源語言只有 cs、en、ja，而 ja 只翻成 zh）；所以 ja→en 沒有 WMT25 的獨立人評 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- 主辦單位另外加入的 baseline 系統包括 AyaExpanse-8B/32B、Gemma-3-12B/27B、Llama-3.1-8B、Mistral-7B、Qwen2.5-7B、Qwen3-235B、TowerPlus-9B/72B、NLLB-200 3.3B、EuroLLM、CommandR7B 等；但 ja→zh 有進人評的開放權重模型只有 Qwen3-235B、TowerPlus-9B、DeepSeek-V3 和參賽隊伍的系統，Gemma-3、Llama、Aya、Qwen 小模型都沒有在 ja→zh 被人評 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- Findings 的結論包括：Gemini 2.5 Pro 在 16 個語言對中有 14 個進入頭名集群；受限組（≤20B、開放權重）最強的是 Shy-hunyuan-MT，在 11 個語言對進入受限組頭名集群。另外，Shy-hunyuan-MT 在 **AutoRank（自動指標）幾乎全部第一**，但人評排名較低；主辦方推測原因是它的 GRPO 用了 XCOMET-XXL 和 GEMBA（DeepSeek-V3）當 reward，等於對指標過擬合 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- 也有 Gemma-3-12B、NLLB 輸出「FAILED」之類明顯失敗案例的紀錄 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

**Tencent Hunyuan-MT-7B / HY-MT1.5（1.8B、7B）**
- Hunyuan-MT 技術報告與 model card 宣稱在 WMT25「31 個參賽語言類別中 30 個第一」[論文宣稱；但從 WMT25 Findings 看，這是 **AutoRank 自動排名**，人評上 Gemini-2.5-Pro 等閉源模型仍領先] — [Hunyuan-MT Technical Report (arXiv 2509.05209)](https://arxiv.org/abs/2509.05209)；[Hunyuan-MT-7B README](https://huggingface.co/tencent/Hunyuan-MT-7B/blob/main/README.md)；對照 [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- Hunyuan-MT-Chimera-7B 是兩階段 ensemble：先用 Hunyuan-MT-7B 產生多個候選，再由 Chimera 融合成一個輸出（延遲至少是單次的數倍，不適合即時字幕）— [Hunyuan-MT-7B README](https://huggingface.co/tencent/Hunyuan-MT-7B/blob/main/README.md)
- HY-MT1.5 技術報告（2025-12-30，arXiv 2512.24092）[論文宣稱，未經同儕審查]：1.8B 和 7B 分別建立在 HY-1.8B-Base 與 HY-7B-Base 上；訓練流程是 continued pretraining → SFT → 用 7B 當老師做 on-policy distillation → GRPO RL — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- 自報的 XCOMET-XXL 分數：
  - **1.8B**：Flores-200 ZH⇔XX 0.8361、EN⇔XX 0.8942、WMT25 0.5308
  - **7B**：ZH⇔XX 0.8690、EN⇔XX 0.9093、WMT25 0.6159
  - 對照的 Gemini 3.0 Pro：ZH⇔XX 0.8982、WMT25 0.5505
  - 報告宣稱 7B 在 WMT25 上 XCOMET 超過 Gemini 3.0 Pro。這屬於自動指標，WMT25 Findings 已經指出 Hunyuan 有對 XCOMET 過擬合的疑慮
  - 報告**沒有**列出日文專屬的分數

  [論文宣稱] — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- 人工評測（0–4 分，只有 ZH⇒EN / EN⇒ZH）：HY-MT1.5-1.8B 平均 2.74，Microsoft Translator 2.69 [論文宣稱] — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)

**Qwen3.5（4B/9B，目前 kikitori 的預設）**
- Qwen3.5-9B model card 自報 **WMT24++（55 種語言平均，XCOMET-XXL）72.6**，沒有日文單獨的分數，也沒有 Flores [論文宣稱] — [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B)
- 架構是 Gated DeltaNet 與 Gated Attention 的混合，內建 vision encoder，宣稱支援 201 種語言與方言，原生 context 長度 262,144 — [Qwen3.5-9B model card](https://huggingface.co/Qwen/Qwen3.5-9B)
- 上一代 Qwen3-235B 在 WMT25 ja→zh 人評得 −10.9 MQM，排在 Hunyuan-MT-7B（−6.1）之後 [獨立評測] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

**TranslateGemma（Google，2026-01，基於 Gemma 3 的 4B/12B/27B）**
- 訓練分兩階段：SFT（合成資料加人工平行語料）→ RL（reward 用 MetricX-QE、AutoMQM 等 ensemble）[論文宣稱，未經同儕審查] — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- WMT24++ 55 個語言對的平均 MetricX（越低越好）：27B 由 4.04 降到 3.09、12B 由 4.86 降到 3.60、4B 的 baseline 是 6.97；二手摘要說 4B 改善 23.6%，換算約 5.3，但這是推算值，沒有在原文中讀到[論文宣稱] — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- **en→ja_JP 的 MetricX**：TranslateGemma 27B／12B／4B 為 3.53／3.82／4.44；Gemma 3 27B／12B／4B 為 4.11／4.30／5.09 [論文宣稱，WMT24++] — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- **人工 MQM 評測（WMT25 的 literary／news／social domain，不含 speech）**：10 個語言對中，**Japanese→English 是唯一 TranslateGemma 比 Gemma 3 退步的**，原因是專有名詞（named entity）誤譯，其他錯誤類別有改善。表中 ja→en 的數字是 13.4／15.7／11.6，但抽字後欄位對齊不明，只能確定「退步」這個文字結論 [論文宣稱；人評由 Google 內部執行] — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- 官方建議的 prompt 是：「You are a professional {source_lang} ({src_lang_code}) to {target_lang} ({tgt_lang_code}) translator… Produce only the {target_lang} translation… Please translate the following {source_lang} text into {target_lang}:\n\n\n{text}」，並說明用其他 prompt 可能偏離訓練分布 — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- 訓練資料的語言對分布圖中，Japanese-Chinese 占一部分（圖中出現 9.3% 的標示，但抽字結果無法確定是哪一類資料的比例）— [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)

**ByteDance Seed-X（7B，2025-07）**
- 涵蓋 28 種語言，日文（ja）在內；家族包含 Instruct、PPO（官方推薦）和 RM 三個版本 [論文宣稱，未經同儕審查] — [Seed-X (arXiv 2507.13618)](https://www.arxiv.org/pdf/2507.13618)；[Seed-X-PPO-7B card](https://huggingface.co/ByteDance-Seed/Seed-X-PPO-7B)
- 自報 Flores-200 與 WMT25 dev 的自動指標（XCOMET-XL、BLEURT）跟 Tier-1 超大模型相當；人評（0–4 分）的 Seed-X-Challenge 平均 3.73，Gemini-2.5-Pro 3.66、Claude-3.5-Sonnet 3.68。但人評只涵蓋 **zh/en ⇄ es/de/fr/ru/pt/it/ar**，**沒有日文** [論文宣稱] — [Seed-X](https://www.arxiv.org/pdf/2507.13618)
- Seed-X 沒有參加 WMT25 人評，所以沒有獨立評測證據 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

**Unbabel Tower+（2B/9B/72B，2025-06）**
- 論文把它定位成兼顧翻譯與通用能力的模型 [論文宣稱] — [Tower+ (arXiv 2506.17080)](https://arxiv.org/html/2506.17080v1)
- TowerPlus-9B 在 WMT25 ja→zh 人評得 −13.3 MQM，在有人評的系統中屬後段 [獨立評測] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

**PLaMo 2 Translate（Preferred Networks，10B）**
- 由 plamo-2-8b 微調而來，**只支援英日互譯**，沒有中文；model card 也沒有列 benchmark — [pfnet/plamo-2-translate](https://huggingface.co/pfnet/plamo-2-translate)

**Cohere Aya Expanse / Command**
- CommandA-WMT 在 WMT25 ja→zh 得 −7.7 MQM、en→ja 得 82.2 ESA [獨立評測]。但 Command A 是 111B，消費級 GPU 跑不動；AyaExpanse-8B 在 ja→zh 沒有人評 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

### Inferences
- 以「獨立人評」加上「能塞進 8–12 GB」為條件，**Hunyuan-MT-7B 或 HY-MT1.5-7B** 對 ja→zh 的證據最強。不過 WMT25 的人評是 document-level，而且資料是 news／social／literary／speech，不是 VTuber 口語；Hunyuan 對 XCOMET 過擬合的疑慮也代表自動指標上的領先不能全信。
- Qwen3.5 小模型在日文方向**沒有可引用的獨立評測**。推測理由：它的上一代 Qwen3-235B 在 ja→zh 人評中也只是中段。目前把它當預設的主要理由是授權乾淨（Apache-2.0），而不是品質證據。
- 在 ja→en 這個方向，整個調查都找不到 2025–2026 年針對 ≤12B 開放模型的獨立人評。TranslateGemma 的 ja→en 退步（專有名詞誤譯）對 VTuber 直播特別敏感，因為直播充滿人名、遊戲名、梗。

### Gaps
- WMT24 的 ja→zh、en→ja 人評結果沒有抓到（受工具呼叫次數限制）。
- IWSLT 2025/2026 有沒有日文相關的 offline 或 simultaneous 賽道結果，沒有查證。
- LLM-jp（NII）、Swallow（Science Tokyo/AIST）、Sarashina（SB Intuitions）、Llama 3.x、Mistral 小模型、Gemma 3n、NLLB-200、MADLAD-400 在 ja→zh 或 ja→en 的 2025–2026 證據都沒有查到可引用的來源；只知道 Llama-3.1-8B、Mistral-7B、NLLB 有參加 WMT25 自動評測，但在 ja→zh 沒有人評。
- 主辦方在 WMT25 ja→zh 為 Qwen3、Gemma、Llama 小模型計算的 AutoRank 分數，從 PDF 抽出的表格對齊不可靠，沒有採用。

---

## Q2. 口語、對話、字幕翻譯的品質，以及對 ASR 錯誤的穩健性有什麼證據？

### Takeaway
WMT25 給了直接相關的獨立證據：**speech domain（YouTube 影片加上 Whisper-large-v3 ASR）在所有語言對都是最難的**，原因可能是 ASR 錯誤；而且 ja→zh 的 speech 分數明顯低於 social。專門針對 slang／internet 用語的評測，目前只有 Seed-X 的自建 Challenge set（不含日文）。專門針對 VTuber 或日文直播口語的 MT 評測，沒有找到。

### Cited Findings
- WMT25 speech domain 的資料來源：從 CC 授權的 YouTube 影片（紀錄片、教學、訪談、vlog、運動、表演等）隨機截取 30–50 秒片段，用 **Whisper-large-v3** 轉錄。日文的 Whisper 輸出「幾乎從不加標點」，所以主辦方另外跑了一個標點還原模型；參賽者拿到的是影片加上 ASR 文字稿 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- 主辦方用 GEMBA-ESA（Command A 與 GPT-4.1 的平均）幫**人類參考譯文**打分，結果 JA-ZH 在 Literary 79.9、News 82.7、Social 86.6、**Speech 72.6**；EN-JA 則是 Speech 80.0、Social 84.1 [獨立評測，自動 LLM-judge] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- 各語言對最佳系統分 domain 的人評，以 en→ja 為例：Literary 98.4、**Speech 86.3**、Social 92.1、News 88.9。Findings 的結論是「speech domain 得分最低，最具挑戰性，很可能是因為依賴 ASR 產生的文字」，而且與 WMT24 的趨勢一致 [獨立評測] — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- Findings 也指出，從六個 test suite 的分析看，SOTA 系統對「非標準輸入」的穩健性仍不足 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)
- Seed-X 自建的 Challenge test set 包含 internet slang、流行語、成語、專有名詞，涵蓋正式與口語兩種風格，但來源只有中文或英文，目標是 7 種歐語與阿語，**不含日文** [論文宣稱] — [Seed-X](https://www.arxiv.org/pdf/2507.13618)
- 日英商務對話（BSD corpus）上的 context-aware NMT 研究（MT Summit 2023，不是 LLM）：加入前文、場景與說話者資訊，可以提升 BLEU 和 COMET [同儕審查] — [Honda et al., Context-aware NMT for En-Ja Business Scene Dialogues](https://arxiv.org/pdf/2311.11976)
- Sony 的 EMNLP 2025 Findings 用 OpenSubtitles2018 子集（對話）和 TED（獨白）評測 **en→ja** 的 LLM（GPT-4o、Gemma 2-27B）：所有 context 策略都優於無 context [同儕審查] — [Choudhary et al., Findings of EMNLP 2025](https://aclanthology.org/2025.findings-emnlp.1324.pdf)
- WMT25 的參考文獻引用了 JESC（Japanese-English Subtitle Corpus）與 JParaCrawl，作為可用的訓練資源 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

### Inferences
- kikitori 的輸入是 Whisper 輸出、無標點、≤10 秒的句子，比 WMT25 的 speech domain 條件更差（WMT25 有標點還原，片段 30–50 秒、上下文更多）。可以預期 speech domain 的落差在 kikitori 會更明顯。這支持兩個做法：(a) 把前幾句的原文或譯文當 context；(b) final pass 可以合併多句、補標點後再翻。
- 目前沒有任何開放模型有「日文口語／直播」的專門證據，選型只能用 WMT25 ja→zh 的整體人評當代理指標，再加上 kikitori 自建的小型 VTuber 測試集來驗證。

### Gaps
- 沒有找到 IWSLT 有 Japanese 的 dialect／speech／simultaneous 賽道 2024–2026 年的結果。
- 沒有找到 ASR 錯誤注入（noisy input）對 ja→zh LLM MT 影響的量化研究。
- VTuber 或日文直播用語（草、てぇてぇ、スパチャ等）沒有任何公開 MT 評測集。

---

## Q3. 哪些 prompting 策略有已發表的證據？（前文 context、術語表、few-shot、敬語／填充詞、不完整句、re-translation 與 incremental）

### Takeaway
「**前幾句原文加上模型自己的前幾句譯文**當 context」有同儕審查的證據（en→ja，含字幕對話），可以提升 COMET 和語篇一致性。術語注入與 context 翻譯在 HY-MT1.5 有**官方訓練過的 prompt 模板**。直播字幕適合用 **re-translation**（每次重翻整句），並控制 erasure／flicker：Arivazhagan et al.（IWSLT 2020）顯示低 erasure 的 re-translation 品質不輸 streaming。

### Cited Findings

**前文 context**
- Sony（EMNLP 2025 Findings）比較了多種 context 策略：前 k 句原文（src）、模型自己的前幾句譯文（hyp）、參考譯文（ref）以及它們的組合，k=5，模型是 GPT-4o 和 Gemma 2-27B，語言對 en→ja/fr/de，資料來自 TED 和 OpenSubtitles。結論是「所有 context 策略一般都優於 no-ctx baseline」，光給 src context 就穩定優於 baseline；**src 加上自身 hyp** 不需要 gold reference 就能改善 formality、代名詞、詞彙一致性 [同儕審查] — [Choudhary et al., 2025](https://aclanthology.org/2025.findings-emnlp.1324.pdf)
- WMT24 Chat 翻譯 shared task：Unbabel+IT 用 context-augmented instruction 加上 context-aware metric 做 decoding；另一隊（Sung et al.）用對話摘要加 dialogue history 做 en↔ko [同儕審查，WMT24] — [Pombal et al., WMT24](https://aclanthology.org/2024.wmt-1.100)；[Sung et al., WMT24](https://preview.aclanthology.org/setup/2024.wmt-1.102)
- CUNI 的 IWSLT 2025 系統（Whisper 加 AlignAtt 加 EuroLLM 的 cascade，含 en→zh、en→ja）在 cs→en 方向使用**前面的譯文當 context** [同儕審查，IWSLT 2025] — [CUNI IWSLT 2025](https://aclanthology.org/2025.iwslt-1.41/)
- EACL 2026 的 demo：LLM 以 5 個詞的片段做即時翻譯，並用前幾輪對話當 context；介面以淡出高亮的方式降低 flicker 的視覺干擾 — [EACL 2026 demo](https://aclanthology.org/2026.eacl-demo.40.pdf)

**術語／context／格式 prompt（官方模板）**
- HY-MT1.5 model card 提供以下官方模板 [論文宣稱]：
  - 術語干預：在翻譯指令前加一行「參考下面的翻譯：{src_term} 翻譯成 {tgt_term}」
  - context 翻譯：把 context 放在原文前，要求「只翻譯原文、不要翻 context、不解釋」
  - 格式保留：`<source>` 與 `<sn>` 標籤
  - 基本指令：中文方向用「將以下文本翻譯為{target_language}，注意只需要輸出翻譯後的結果，不要額外解釋：」，非中文方向用對應的英文指令

  — [HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)；[HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- 報告舉例說明術語干預的效果（「混元珠」翻成 Chaos Pearl）和 context 翻譯的效果（有劇集 context 時，pilot 翻成「試播集」而不是「飛行員」）[論文宣稱] — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- HY-MT1.5 建議的推論參數：top_k 20、top_p 0.6、repetition_penalty 1.05、temperature 0.7；沒有預設 system prompt — [HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)
- TranslateGemma 建議**沿用訓練時的固定 prompt**（見 Q1）— [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- Seed-X 用語言 tag（`<EN>`、`<ZH>`、`<JA>` 等）當 delimiter，並做了消融實驗 [論文宣稱] — [Seed-X](https://www.arxiv.org/pdf/2507.13618)

**解碼參數**
- Llama-3.1-8B-Instruct 的 temperature 從 0.0 升到 0.8，ja 方向的 COMET 下降約 1.3–1.6；top_p 從 1.0 改成 0.95 的影響在 ±0.2 以內 [未經同儕審查] — [Marie & Fujita, arXiv 2508.20893](https://arxiv.org/html/2508.20893)
- Qwen3.5 預設**開啟 thinking**，要用 `chat_template_kwargs: {"enable_thinking": false}` 關閉，而且不支援 Qwen3 的 `/think`、`/nothink` 開關；非 thinking 模式的一般任務建議 temperature 0.7、top_p 0.8、top_k 20、presence_penalty 1.5 — [Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)

**Re-translation 與 streaming（live 字幕）**
- Arivazhagan, Cherry, Macherey, Foster（IWSLT 2020）：低 erasure（normalized erasure < 0.2）的 re-translation，品質與當時最好的 streaming 方法相當甚至更好；沒有 erasure 時，re-translation 退化成 wait-k [同儕審查] — [Re-translation versus Streaming for Simultaneous Translation](https://arxiv.org/pdf/2004.03643)
- Google Research（2021）在 Google Translate 的 live speech translation 用 masking（暫不顯示句尾最後 k 個詞）與 biasing（讓新譯文偏向沿用前一次譯文）來穩定輸出。IWSLT 2018 en→de 上 erasure 從 2.1 降到 0.1，BLEU 只從 20.4 降到 20.2 [公司技術部落格，非同儕審查] — [Google AI Blog: Stabilizing Live Speech Translation](https://ai.googleblog.com/2021/01/stabilizing-live-speech-translation-in.html)

### Inferences
- **fast pass**：建議用 re-translation，可以加上「句尾 masking」或「新譯文以前一次譯文為前綴偏置」（LLM 可以用 prefix 或 assistant prefill 實作）來降低字幕跳動。**final pass**：改用 src 加上自身前幾句 hyp 當 context（k 約 3–5），對應 Sony 2025 的最佳無參考策略。
- **術語表**：VTuber 名、組合名、遊戲名可以用 HY-MT1.5 官方的「參考下面的翻譯」模板注入；用 Qwen3.5 時可以仿照這個格式，但 Qwen3.5 沒有針對這種模板訓練過的證據。
- **temperature**：翻譯建議設 0 或接近 0（對 ja 方向約有 1.3–1.6 COMET 的差距）。這與 Qwen 官方「一般任務 0.7」的建議衝突，翻譯場景以 MT 研究的數據為準比較合理。
- **few-shot、敬語、填充詞（えー、あの）、不完整句**：沒有找到 2024–2026 年專門針對 LLM 的已發表量化證據，只能靠自建測試集決定。例如在 system prompt 要求「刪除填充詞」「不完整句照原樣保留未完成語氣」，再用 A/B 驗證。

### Gaps
- WMT 術語 shared task（WMT23/WMT25 terminology）的結果沒有查證。
- 填充詞處理、敬語轉譯、few-shot 示例對 ja→zh LLM MT 效果的已發表研究沒有找到。
- 沒有找到 LLM re-translation 加上 prefix 強制（forced prefix）在日文來源上的 flicker 實測。

---

## Q4. 量化（Q4／Q5／Q8 GGUF）對 MT 品質的影響

### Takeaway
目前最直接的研究是 Marie & Fujita（NICT 合作，2025-08，未經同儕審查）：**GGUF Q4_K_M 是所有 4-bit PTQ 方法中損失最小的**，對日文這種高資源語言、8B 等級模型幾乎無損（COMET 掉不到 1 點）；但 **1.7B 等級的小模型和 2-bit 會明顯退化**。HY-MT1.5 自報 Int4（GPTQ）會「明顯退化」，FP8 則幾乎無損。Q5／Q8 GGUF 在 MT 上沒有找到量測。

### Cited Findings
- Marie（kaitchup）與 Fujita（NICT）的 "The Uneven Impact of Post-Training Quantization in Machine Translation"（arXiv 2508.20893，2025-08，未經同儕審查）[未經同儕審查] — [arXiv 2508.20893](https://arxiv.org/html/2508.20893)：
  - 評測設定：Qwen3-1.7B／8B／32B、Llama-3.1-8B／3.3-70B 在 WMT24++ 上的 COMET；比較 AWQ、BnB NF4、**GGUF Q4_K_M／Q2_K**（用 WikiText imatrix）、AutoRound
  - **en→ja**：Qwen3-8B 由 85.7 降到 85.0–85.6（4-bit）；Qwen3-1.7B 由 80.4 降到 78.2–79.7（4-bit），GGUF 為 79.7
  - 2-bit GGUF Q2_K：Qwen3-8B 降到 83.7（約 −2.0），Qwen3-1.7B 降到 69.4（約 −11）
  - **ja→en**：Qwen3-1.7B 由 79.3 降到 78.4–79.2（4-bit）
  - Llama-3.1-8B 的 ja→en 在 4-bit 約掉 1.5 COMET
  - 結論：4-bit 下 GGUF 損失最小，AWQ、BnB 損失最大；低資源語言（印度語系、Zulu）退化最嚴重；日文、法文接近全精度。所有結果都是 greedy decoding、單次執行；**沒有 8-bit 結果**
- HY-MT1.5-1.8B 的量化結果（GPTQ，XCOMET-XXL）[論文宣稱]：
  - 原始：ZH⇔XX 0.8361、EN⇔XX 0.8942
  - FP8（W8A8C8）：0.8379、0.8905
  - **Int4：0.8060、0.8665**，報告自稱「noticeable accuracy degradation」
  - 2-bit 版本用 QAT，宣稱即將釋出

  — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)

### Inferences
- 對 7B–9B 模型來說，Q4_K_M GGUF 對日文方向的品質損失預期很小，**可以當預設**。對 1.8B／2B 等級的小模型，量化損失比例更大（HY-MT1.5-1.8B 的 GPTQ Int4 約 −0.03 XCOMET），建議用 Q5_K_M 或 Q8_0。這是根據「越小的模型對量化越敏感」的趨勢推論，並沒有直接量測。
- HY-MT1.5 的 Int4 退化是 GPTQ，不是 GGUF k-quant；依 Marie & Fujita 的結果，GGUF 可能比 GPTQ 損失小，但沒有直接比較。

### Gaps
- 沒有找到 Q5_K_M、Q6_K、Q8_0 GGUF 在 MT 上的同儕審查量測。
- 沒有找到 ja→zh 方向的量化影響量測（Marie & Fujita 沒有評測中文）。

---

## Q5. 各候選模型在 Q4_K_M、消費級 GPU 上的延遲與 VRAM

### Takeaway
官方延遲數字只有 HY-MT1.5（每句約 50 token：1.8B 0.18 s、7B 0.45 s，硬體與量化方式在抽取內容中沒有寫明）。其他模型都沒有官方的消費級 GPU 延遲或 VRAM 數據，**這是主要缺口**。

### Cited Findings
- HY-MT1.5 用 100 句約 50 token 的中文翻成英文測試，平均回應時間 **1.8B 0.18 s、7B 0.45 s** [論文宣稱；硬體在抽取內容中未說明] — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- HY-MT1.5 官方釋出 FP8 和 GPTQ-Int4 版本，model card 沒有提到官方 GGUF — [HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)
- Ollama 官方 library 有 `qwen3.5`（4B／9B 標示為 256K context、Text+Image 輸入）— [Ollama qwen3.5](https://ollama.com/library/qwen3.5)
- PLaMo 2 Translate 在 Ollama 上有社群上傳的 GGUF（`mitmul/plamo-2-translate`，例如 Q3_K），不是 PFN 官方 — [Ollama mitmul/plamo-2-translate](https://ollama.com/mitmul/plamo-2-translate:Q3_K/blobs/c71cb68290c2)
- Hunyuan-MT-Chimera-7B 在 Ollama 上也有社群上傳的 Q8 版本 — [Ollama SimonPu/Hunyuan-MT-Chimera-7B](https://ollama.com/SimonPu/Hunyuan-MT-Chimera-7B:Q8/blobs/4cd6b7965235)

### Inferences
以下是未經引用的估算，請以實測為準：
- **權重大小**：Q4_K_M 每個參數約 0.55–0.6 bytes，所以 4B 約 2.5–3 GB、7B 約 4.3–4.7 GB、9B 約 5.5–6 GB，再加上 KV cache（短句、context ≤ 2K 時很小）和 runtime overhead 約 0.5–1 GB。
- **Qwen3.5 的額外負擔**：Qwen3.5 的 vision encoder 會額外占空間，Ollama 版本是否會載入 vision 權重需要實測。另外 Qwen3.5 的混合架構（Gated DeltaNet）在 llama.cpp 的效能成熟度也需要實測。
- **與 Whisper 共存**：在 12 GB GPU 上，Whisper（1–2.5 GB）加上 7B–9B 的 Q4_K_M 可以放得下；在 8 GB GPU 上建議用 4B 或 HY-MT1.5-1.8B。
- **延遲**：每句 20–40 token 的輸出、decode 50–100 tok/s 時約 0.3–0.8 s，可以達到 0.5–1.5 s 的目標；CPU 上只有 1.8B／2B 等級才可能接近這個目標。

### Gaps
- Qwen3.5、TranslateGemma、Seed-X、Tower+ 都沒有找到官方的消費級 GPU tokens/s 或 VRAM 數據。
- HY-MT1.5 延遲測試用的硬體沒有在抽取內容中查到。

---

## Q6. 授權細節（SPDX／名稱、商用、地區限制、AUP）

### Takeaway
**授權最乾淨的是 Qwen3.5（Apache-2.0）**。Hunyuan 系列採用 Tencent Hunyuan Community License，多數來源指出**不適用於 EU、UK、南韓**。Sakura／GalTransl 與 Tower+ 是 **CC BY-NC-SA 4.0（禁止商用）**。PLaMo 是 PLaMo Community License（年營收 10 億日圓以下免費）。Seed-X 是 OpenMDW。Breeze 2 是 Llama 3.2 Community License。TranslateGemma 和 TAIDE 的模型授權沒有在本次來源中直接確認。

### Cited Findings
- **Qwen3.5-9B**：`apache-2.0` — [Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)
- **Hunyuan-MT-7B**：TENCENT HUNYUAN COMMUNITY LICENSE AGREEMENT；GIGAZINE 報導這個授權限制 EU、UK、南韓地區使用 — [GIGAZINE](https://gigazine.net/gsc_news/en/20250902-tencent-hunyuan-mt-7b)
  - 其他 Hunyuan 7B 系列的 LICENSE 文字把 "Territory" 定義為全球但排除 EU、UK、南韓 — [Hunyuan-7B-Instruct-AWQ-Int4 LICENSE commit](https://huggingface.co/tencent/Hunyuan-7B-Instruct-AWQ-Int4/commit/37e090cb053e5be3d8abc695f5d1b47c1b42b32a)
  - **相互矛盾的來源**：innfactory.ai 宣稱某次正式版已解除這個限制，但沒有說明是哪個模型，也無法與 Tencent 原文核對 — [innfactory.ai](https://innfactory.ai/en/ai-models/tencent-hunyuan/)
- **HY-MT1.5-7B**：model card 頁面本身**沒有顯示 license 欄位**，必須直接看 repo 裡的 LICENSE 檔，本次沒有讀到 — [HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)
- **Sakura／GalTransl**：`cc-by-nc-sa-4.0`；GalTransl v1 說明禁止商用，例如付費翻譯介面或付費補丁 — [Sakura-GalTransl-14B-v3.8 README](https://huggingface.co/SakuraLLM/Sakura-GalTransl-14B-v3.8/blob/cd49dfd28bda41ecee902459f866647e3770a63b/README.md)；[GalTransl-v1](https://huggingface.co/SakuraLLM/GalTransl-v1)
- **Tower-Plus-9B**：託管平台列為 `cc-by-nc-sa-4.0`，宣稱支援 22 種語言，含日文。這是二手來源，沒有對照官方 card — [Featherless: Tower-Plus-9B](https://featherless.ai/models/Unbabel/Tower-Plus-9B)
- **Seed-X**：OpenMDW 授權（GitHub 的 LICENSE.openmdw）— [Seed-X-RM-7B card](https://huggingface.co/ByteDance-Seed/Seed-X-RM-7B)；[ComfyUI Wiki 新聞](https://comfyui-wiki.com/en/news/2025-07-24-bytedance-seed-x-7b-open-source-model)
- **PLaMo 2 Translate**：PLaMo Community License，商用需要先填表聯絡 PFN — [pfnet/plamo-2-translate](https://huggingface.co/pfnet/plamo-2-translate)
  - 授權條文的營收上限是使用者或關係企業年營收 **10 億日圓** — [ScanCode LicenseDB: plamo-cla-2024](https://scancode-licensedb.aboutcode.org/plamo-cla-2024.html)
  - 日本媒體報導：年營收未滿 10 億日圓的企業與個人可以免費在自家環境使用，含商用 — [窓の杜](https://forest.watch.impress.co.jp/docs/news/2017742.html)
- **Breeze 2（MediaTek Research）**：Llama 3.2 Community License（早期版本寫的是 Llama 3）— [Breeze 2 paper (arXiv 2501.13921)](https://arxiv.org/abs/2501.13921)；[Llama-Breeze2-3B-Instruct README](https://huggingface.co/MediaTek-Research/Llama-Breeze2-3B-Instruct/blame/7a30c78c796b8f47cefa0a780eda99854e8a834b/README.md)
- **TranslateGemma**：arXiv 頁面上的 CC BY 4.0 是**論文**的授權，不是模型授權 — [TranslateGemma arXiv](https://arxiv.org/abs/2601.09012)
- **WMT25 的受限組規則**：要求最終模型 ≤20B 參數且以開源授權發布（例如 Apache、MIT）— [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

### Inferences
- TranslateGemma 建立在 Gemma 3 上，**很可能**沿用 Gemma Terms of Use（含 Prohibited Use Policy），但本次沒有直接核對 model card，需要確認。
- 如果 kikitori 要在 EU、UK、KR 散布或讓這些地區的使用者使用，Hunyuan 和 HY-MT 應繼續標示警告。Sakura 和 Tower+ 只能標示「非商用」。PLaMo 只支援英日，與 zh-TW 需求無關，但可以當 ja→en 的候選。

### Gaps
- HY-MT1.5 的 LICENSE 原文（地區排除條款是否仍在）沒有讀到。
- TranslateGemma 和 Gemma-3-TAIDE 的模型授權原文沒有讀到。
- Llama 3.x 授權的 700M MAU 條款、Gemma Terms 的 AUP 原文沒有在本次直接引用。

---

## Q7. 繁體中文（台灣用語）：有沒有模型能原生輸出台灣繁中？ja→zh-TW 的證據與授權？

### Takeaway
在主流 MT 專用模型中，**HY-MT1.5 和 TranslateGemma 把 zh-Hant／zh-TW 列為支援的目標**。TranslateGemma 在 WMT24++ 有 **en→zh_TW** 的自報分數，但**沒有任何模型有 ja→zh-TW 的已發表評測**。台灣本地模型（Breeze 2、Gemma-3-TAIDE）的訓練重點是繁中，以及中英互譯或辦公室任務，沒有日文翻譯的證據。

### Cited Findings
- HY-MT1.5-7B 的語言表包含 **Traditional Chinese（zh-Hant）與 Cantonese（yue）**。表頭寫 36 種，內文寫 33 種加 5 種方言／變體，數字不一致 — [HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)
- HY-MT1.5 技術報告本身沒有提到繁中的評測結果 — [HY-MT1.5 Technical Report](https://arxiv.org/html/2512.24092v1)
- TranslateGemma 的語言清單含 Chinese (Taiwan) (zh-TW) 與 Taiwanese Mandarin (zh-Hant)。WMT24++ 的 **en→zh_TW MetricX**：TranslateGemma 12B 2.21、4B 2.77；Gemma 3 對應值 2.80、3.62。27B 那一欄在抽字時有一欄錯位，暫不引用 [論文宣稱] — [TranslateGemma Technical Report](https://arxiv.org/pdf/2601.09012)
- Breeze 2（MediaTek Research）是以 Llama 3.2 3B 和 Llama 3.1 8B 為骨幹的繁中模型，有 vision 和 function calling；授權是 Llama 3.2 Community License — [Breeze 2 paper](https://arxiv.org/abs/2501.13921)
- Gemma-3-TAIDE-12B-Chat（2025-08 上架 Hugging Face）：以 Gemma-3-12b-pt 為基礎，用繁中資料做 continued pretraining，指令微調的任務包含摘要、寫信、**中英互譯**；沒有提到日文 — [TechNews 科技新報](https://technews.tw/2025/08/27/gemma-3-taide-series-launched/)
- WMT25 的 ja→zh 是**簡體中文**目標，所以所有 ja→zh 的獨立人評都是簡中 — [WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)

### Inferences
- 在 ja→zh 品質證據最強的模型（Hunyuan 系列）都以簡中為主的情況下，「**輸出簡中，再用 OpenCC `s2twp`（含台灣詞彙轉換）**」仍然是證據最充分的路線。HY-MT1.5 和 TranslateGemma 可以直接指定 zh-Hant／zh-TW 目標，但品質沒有被驗證，值得在自建測試集上與「簡中加 OpenCC」做 A/B 比較。
- Breeze 2 和 TAIDE 不建議作為 ja→zh-TW 翻譯模型，因為沒有日文翻譯證據，授權也是 Llama 或 Gemma 系的條款。但它們可以考慮當「繁中潤稿」的第二階段，代價是延遲加倍。

### Gaps
- 沒有找到任何 ja→zh-TW（或 ja→zh-Hant）的公開 benchmark 結果。
- Taiwan-LLM（Yen-Ting Lin）2025–2026 年的狀態與授權沒有查證。
- TAIDE 的授權原文沒有讀到。
