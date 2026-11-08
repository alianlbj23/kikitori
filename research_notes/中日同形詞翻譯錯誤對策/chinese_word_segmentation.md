# 中文斷詞（CWS）最新技術與「先斷詞再交給 LLM 翻譯」是否有效

> 調查日期：2026-11-07。情境：Whisper 輸出的繁中（台灣）字幕交給 Qwen3.5 9B/4B（Ollama）翻成日文，LLM 把「我今天要吃豬排便當」讀成「豬／排便／當」。
>
> **來源政策（依使用者指示）**：研究結論只引用 ACL、EMNLP、NAACL（main；Findings 會標明）、TACL、Computational Linguistics、IEEE/ACM TASLP、NeurIPS、ICML、ICLR、AAAI、IJCAI 與 WMT 官方 findings；知名公司（Google、Meta、Microsoft、Alibaba、Tencent、SenseTime 等）的技術報告可用，標成「公司報告（非同儕審查）」。COLING、LREC、EACL、AACL、SIGHAN、TALLIP、workshop、一般 arXiv 一律不引用；某做法只在這些地方有證據時，寫「無 top-tier 證據」。知名機構官方 GitHub／PyPI／model card 只拿來確認**事實**（授權、日期、相依套件、大小），上面的準確率一律標「官方自報」，不當品質證據。
>
> **標記**：〔top-tier〕同儕審查的頂級會議／期刊；〔公司報告〕非同儕審查；〔自報〕作者或開發者自己的數字；〔獨立〕第三方評測；〔官方頁面〕只當事實；〔本地檢查〕我在 scratchpad 實際跑過；〔推估〕沒有實測、我自己算的；〔不確定〕有疑慮。
>
> **方法**：論文 PDF 從 ACL Anthology 下載後用 `pdftotext -layout` 抽文字閱讀。雙欄排版下表格可能錯位，有錯位疑慮的數字會另外註明。套件發行日期與授權是用 `https://pypi.org/pypi/<pkg>/json` 抓的（`upload_time` 取最新版各檔案中最早的時間）。

---

## 0. 先講最重要的發現：問題出在 Qwen 的 tokenizer〔本地檢查〕

我把 `Qwen/Qwen3.5-9B` 的 `tokenizer.json`（byte-level BPE，NFC normalizer）下載到 scratchpad，用純 Python 依 merges 的排名做 BPE，看例句會被切成哪些 token。沒有改專案程式碼。

| 輸入 | Qwen3.5 token 切法 |
|---|---|
| 我今天要吃豬排便當 | 我今天 \| 要吃 \| 豬 \| **排便** \| 當 |
| 我 今天 要 吃 豬排 便當 | 我 \| ␠ \| 今天 \| ␠ \| 要 \| ␠ \| 吃 \| ␠ \| 豬 \| 排 \| ␠ \| **便當** |
| 我今天要吃猪排便当（簡體） | 我今天 \| 要吃 \| 猪 \| **排便** \| 当 |
| 乒乓球拍賣完了 | 乒乓球 \| **拍賣** \| 完了 |
| 他說的確實在理 | 他 \| 說的 \| 確 \| **實在** \| 理 |
| 結婚的和尚未結婚的 | 結婚 \| **的和** \| 尚未 \| 結婚 \| 的 |
| 研究生命起源 | 研究 \| 生命 \| 起源（正確） |

- BPE 是貪婪合併，並不理解語意。「排便」在詞表裡是一個 token，「豬排」不是（被切成 豬｜排），所以模型第一眼看到的就是「豬＋排便＋當」。手動加空白之所以有效，是因為它**強迫 token 邊界**：空白之後 `便當` 變成單一 token，`排便` 就不會出現。
- 這解釋了使用者觀察到的錯誤，但**不代表**模型每次都會翻錯。LLM 常能靠上下文修正 token 邊界錯誤；這裡只說明機制。
- 限制〔不確定〕：
  - 這是我自己寫的 BPE，沒有用 HF `tokenizers` 對照。不過純中文字串在 Qwen 的 pre-tokenizer regex 下是一整段 pre-token，所以結果應該等同官方實作。
  - Ollama 的 GGUF 是否沿用同一份 vocab／merges 沒有驗證。
  - 腳本：`C:\Users\ALIANL~1\AppData\Local\Temp\claude\467b7039c1b80ae0\2ee9bef3-0f84-4c55-b926-14d48bf165a7\scratchpad\scripts\bpe_check.py`

這個發現會影響後面的建議：**我們要的不是完整、符合某個斷詞標準的 CWS，而是「在 LLM token 跨越真實詞界的地方打斷它」。**

---

## Q1. 2023–2026 的 CWS SOTA

### Takeaway
- **有監督**的 CWS 從 2019–2020 年 BERT 化之後就到頂了：SIGHAN 2005 的 AS 約 96.6–97.0、CityU 約 97.6–98.2 F1。2023 年以後的 top-tier 論文只在小數點後面進步，主題轉向 multi-criteria、OOV recall、部署成本。
- **LLM 直接 prompt 斷詞**（zero-shot）明顯較差：Qwen1.5 系列在 AS／CityU 約 72–82 F1。LLM＋詞典的混合法（LLACA）最高約 88，仍比有監督模型低約 9–10 分，而且很慢。
- 口語、社群媒體、跨領域文字的分數會掉 3–10 分。**繁中口語、食物名稱、網路用語沒有 top-tier 的專門評測。**

### 有監督（supervised）SOTA

**Chou, Lin, Kao — Advancing Multi-Criteria CWS Through Criterion Classification and Denoising（ACL 2023 main，國立成功大學）**〔top-tier〕〔自報〕— [ACL Anthology](https://aclanthology.org/2023.acl-long.356/)
- 方法：bert-base-chinese（約 110M 參數）加上 criterion hint token，用 10 個資料集一起訓練。
- F1：AS 96.65、CityU 98.15、MSRA 98.36、PKU 96.86；Weibo（WTB）93.94。WTB 是社群媒體，比 AS／CityU 低約 3–4 分，是**領域落差的間接證據**。
- 表中引用的前人最佳：Ke et al. 2021（METASEG）AS 97.04、CityU 98.12；Huang et al. 2020（BERT+CRF）AS 97.00、CityU 97.80。這些數字是從原論文照抄的，不是重新跑的。
- OOV recall：AS 79.07、CityU 91.61（同一篇自報）。AS 的 OOV recall 只有約 79%，代表**新詞、專有名詞是主要錯誤來源**，跟字幕裡的食物名、網路用語直接相關。
- 〔不確定〕論文說前處理沿用 He et al. 2019／Chen et al. 2017，但沒寫 AS／CityU 有沒有轉成簡體再評測。

**CWSeg（ACL 2023 Industry Track，SenseTime Research）**〔ACL 的 industry track，不是 main track；屬公司研究〕〔自報〕— [ACL Anthology](https://aclanthology.org/2023.acl-industry.1/)
- 表 2：6 個資料集的平均 F1 與 CPU 延遲（每千次請求的秒數 = 每句毫秒數）。表格抽字時欄位錯位，下列對應是**依列的順序推定**〔不確定〕：
  - Jieba 80.67／0.17 ms
  - HanLP 82.34／0.33 ms
  - THULAC 88.09／0.57 ms
  - pkuseg 91.29／0.63 ms
  - BERT-CRF 96.59／12.7 ms
  - WMSeg 97.06／14.5 ms
  - CWSeg 97.65／12.9 ms
  - CPU 型號沒寫。工具版本也沒寫，HanLP 可能是 1.x。
- 跨領域實驗（表 4，留一個領域不訓練）：
  - Weibo 87.73（不用 versatile decoding 時 86.01）
  - MSR 92.26（不用時 85.26）
  - CityU 92.48
  - 結論是：**沒看過的領域會掉 4–10 分**。
- 這是少數同時列出準確率與 CPU 延遲的來源：BERT-base 等級在 CPU 上約 **13 ms／句**（句長不明）。

### 無監督與 LLM 斷詞

**Zhang et al. — Segment First or Comprehend First? Explore the Limit of Unsupervised Word Segmentation with LLMs（ACL 2025 main，武漢大學、上海交大）**〔top-tier〕〔自報〕— [ACL Anthology](https://aclanthology.org/2025.acl-long.888/)
- Zero-shot prompt 斷詞的 F1：

  | 模型 | AS | CityU | MSR | PKU |
  |---|---|---|---|---|
  | Qwen1.5-7B-Chat | 78.4* | 82.5* | 84.2 | 86.7 |
  | Qwen1.5-14B | 78.0 | 72.1 | 85.7 | 87.0 |
  | Qwen1.5-32B | 80.5 | 74.9 | 85.9 | 86.3 |
  | GPT-4 | 76.5 | 80.6 | 75.0 | 76.3 |

- \* 的意思是先用 OpenCC **轉成簡體**才評測。不轉的話，AS 只有 67.9、CityU 只有 69.1，原因是 Qwen 處理繁中時會**擅自改寫成簡體**。這是繁中的直接風險。
- LLACA（LLM＋Aho-Corasick＋n-gram）：Qwen1.5-72B 版 AS 88.3、CityU 88.1；7B 版 AS 84.8、CityU 86.5。
- 速度：Qwen1.5-14B 直接斷 MSR 測試集要 **3.65 小時**，LLACA 快很多。論文也提到 LLM 會重複或替換字（hallucination）。
- 這篇是 unsupervised 設定，所以和有監督的 97 分不是同一個比較條件。不過它說明了：**直接請 LLM 斷詞，在準確率、忠實度（會改字）、速度上都不如小型有監督模型**。

**Li et al. — Improved Unsupervised CWS Using Pre-trained Knowledge and Pseudo-labeling Transfer（EMNLP 2023 main，成大）**〔top-tier〕〔自報〕— [ACL Anthology](https://aclanthology.org/2023.emnlp-main.564/)
- 無監督 SOTA：AS 86.4、CityU 85.7。和有監督相比仍差約 10 分。

### 已排除的來源
- **LREC-COLING 2024 的 Semi-supervised BABERT（Alibaba）**：排除。場地不在允許清單，而且頁面上也沒列出 CWS 數字。
- **2024 年的 CJK 斷詞綜述（arXiv）**：排除。
- **Multi-Tiered Cantonese WS（LREC-COLING 2024）**：排除。
- **2024–2026 年針對「LLM 微調做 CWS」**：沒有找到 top-tier 論文。

### 領域穩健性小結
- 社群媒體（Weibo）：有監督模型在同領域訓練約 94；跨領域約 88（CWSeg，industry track）。
- **繁中口語、台灣食物名、網路流行語、ASR 輸出（沒有標點、有錯字）**：無 top-tier 證據。

---

## Q2. 可部署的工具與模型

表中「需 PyTorch／TF」指官方的 Python 套件。「可 ONNX」指技術上能不能轉成 ONNX Runtime 跑。日期與授權都是我在 PyPI JSON 或官方 repo 實際查到的。

| 工具 | 最新版本／日期 | 授權 | 執行期相依 | 繁中 | 準確率（官方自報，非品質證據） | 大小 | 是否可用 |
|---|---|---|---|---|---|---|---|
| **CKIP Transformers**（中研院 CKIP） | 0.3.4／2023-04-09；HF 權重最後修改約 4–6 年前 | **GPL-3.0**（程式碼與 HF 權重都是） | torch≥1.5、transformers | **原生繁中**（ASBC 訓練） | WS F1：ALBERT-tiny 96.66（4M）、ALBERT-base 97.33（11M）、BERT-tiny 96.98（12M）、BERT-base 97.60（102M） | albert-tiny `pytorch_model.bin` 15.9 MB；bert-base 407 MB | 技術上最合適（BERT 分類器可轉 ONNX，tokenizer 是逐字查 vocab）。**授權不相容**，見下文 |
| **CkipTagger** | 0.3.0／2025-07-09 | **GPL-3.0** | tf_keras（TensorFlow）、gdown | 原生繁中 | ASBC 4.0 test：WS F1 97.33；Jieba-zh_TW 89.80 | 模型壓縮檔約 2 GB | 不可用（TF 加 GPL） |
| **LTP 4**（哈工大） | ltp 4.2.14／2024-06-08；ltp-extension 0.1.13（Rust）／2024-04-04 | **商業使用須付費**（README 明載） | 深度模型要 torch＋transformers；Legacy perceptron 是 Rust | 未提及 | Base 98.7、Small 98.4、Tiny 96.8、Legacy 97.93（資料集沒寫）；Legacy 16 執行緒約 21,581 句/秒 | ext wheel 約 0.7 MB | 不可用（授權） |
| **HanLP 2.x** | 2.1.7／2026-10-25 | 程式碼 Apache-2.0；**中文預訓練模型限研究與教學**，多語模型 CC BY-NC-SA 4.0 | torch≥1.6 | 宣稱支援簡繁 | 約 96–97（表格錯位） | — | 不可用（模型授權） |
| **pkuseg**（北大） | 0.0.25／2020-06-19 | GitHub 寫 MIT；PyPI classifier 是「Other/Proprietary」〔不確定〕 | 無（Cython） | 只有簡體 | 預設模型跨資料集平均 91.29；jieba 81.61；THULAC 88.08 | wheel 約 49 MB（含模型） | 原版停更，沒有 cp312 wheel |
| **spacy-pkuseg**（Explosion 維護的 fork） | 1.0.2／**2026-10-26** | MIT | numpy、srsly | 只有簡體（要先 OpenCC t2s） | 同 pkuseg；spaCy `zh_core_web_sm` 3.8.0 在 OntoNotes 上 token_f 92.9 | wheel 約 2.5 MB；`zh_core_web_sm` 46 MB（MIT，OntoNotes 由 Explosion 取得商用授權） | **可用**（win/mac/linux 都有 cp312 wheel） |
| **THULAC**（清華） | 0.2.2／2022-11-07 | PyPI 沒標；官方歷來是「研究免費、商用須聯絡」〔不確定〕 | 無 | 簡體 | 見 pkuseg 對照表 | wheel 53 MB | 不建議（授權不明、停更） |
| **Stanza**（Stanford） | 1.15.0／2026-10-01 | Apache-2.0 | **torch≥1.13** | zh-hant（UD Chinese-GSD） | Stanza 1.5.1 在 UD 2.12：zh-hant tokens F1 92.79 | — | 不可用（PyTorch；UD-GSD 是維基文體） |
| **LAC**（百度） | 2.1.2／2021-05-25 | Apache-2.0 | PaddlePaddle | 簡體 | — | — | 不可用（Paddle、停更） |
| **jieba** | 0.42.1／2020-01-20 | MIT | 無 | 有 dict.txt.big | 跨資料集約 81–90 | — | 使用者已否決 |
| **jieba-rs／rjieba**（Rust 移植） | rjieba 0.2.2／2026-10-26 | MIT | 無（abi3 wheel） | 同 jieba 詞典 | 演算法與 jieba 相同（DAG＋HMM），準確率不會比較好 | wheel 約 3.5 MB | 維護中，但仍是 jieba 演算法 |
| **HF 上的 ONNX CWS 模型** | — | — | — | — | — | — | 找不到 CKIP WS 的官方 ONNX 版；只有第三方把 `ckiplab/bert-tiny-chinese`（基礎 LM，不是 WS）轉成 ONNX 後改標 Apache-2.0。這種**重新授權沒有效力**，不建議使用 |
| **LLM prompt 斷詞**（Qwen3.5 自己做） | — | 依模型 | Ollama | 會改寫成簡體 | 見 Q1：約 72–88 F1 | 0 | 不建議：要多一次 LLM 呼叫（數百 ms 以上）、會改字、準確率最低 |

來源（只當事實）：[ckip-transformers](https://github.com/ckiplab/ckip-transformers)、[ckiplab/albert-tiny-chinese-ws](https://huggingface.co/ckiplab/albert-tiny-chinese-ws)、[ckiptagger](https://github.com/ckiplab/ckiptagger)、[HIT-SCIR/ltp](https://github.com/HIT-SCIR/ltp)、[hankcs/HanLP](https://github.com/hankcs/HanLP)、[pkuseg-python](https://github.com/lancopku/pkuseg-python)、[spacy-pkuseg](https://github.com/explosion/spacy-pkuseg)、[spaCy zh_core_web_sm meta](https://github.com/explosion/spacy-models)、[Stanza performance](https://stanfordnlp.github.io/stanza/performance.html)、[jieba-rs](https://github.com/messense/jieba-rs)、PyPI JSON API。

### 授權重點
- **CKIP 是 GPL-3.0，連 HF 上的權重也是。** kikitori 是 MIT。就算只讓使用者自己下載權重、用 onnxruntime 跑、不 import CKIP 的程式碼，「GPL 權重算不算衍生著作」的法律解釋仍有爭議。自行轉 ONNX 後的檔案也還是 GPL。要用的話只有三條路：向 CKIP 取得另外的授權、整個專案改成 GPL 相容、或請法律顧問確認。**目前不能算符合限制。**
- **LTP 與 HanLP 的模型都禁止或限制商用。**
- 訓練資料的授權也是障礙：
  - SIGHAN 2005 的使用條款我沒查到（官網連不上）〔不確定〕。
  - ASBC 是中研院的語料庫，商用需要授權〔不確定，沒有在官方頁面確認〕。
  - UD_Chinese-GSD 是 CC BY-SA 4.0，繁中、維基文體，v2.17 在 2025-11-15 發布。授權允許商用，但規模小、文體和口語差很遠。

### 延遲與安裝大小（推估）
- **ALBERT-tiny 轉 ONNX**：4 層、hidden 312，20 字句約 0.1 GMAC。CPU 推估 **1–3 ms**。onnxruntime 已經是專案相依，只會多一個約 16 MB 的權重（int8 約 4–5 MB）〔推估〕。
- **BERT-base 等級**：CWSeg 自報 CPU 約 13 ms／句；20 字句推估 10–30 ms〔推估〕。
- **spacy-pkuseg（CRF）**：CWSeg 表中 pkuseg 約 0.6 ms／句〔自報、對應關係不確定〕。OpenCC 已經是專案相依。

---

## Q3. 先斷詞（或加上詞界資訊）再翻譯，有沒有用？

### Takeaway
**2022 年以後的 top-tier 文獻裡，找不到「把中文、日文或泰文原文先加空白斷詞，再交給 LLM 翻譯」的直接評測。**現有證據分三類：
1. NMT／SMT 時代的研究顯示，詞層級切分不一定比較好，字層級常常更好，這是舊證據。
2. 2025 年有研究顯示，指令微調過的 LLM 對「非標準的 token 切法」相當耐受。
3. 在 prompt 裡給詞典或術語提示，整體有幫助，但對高資源語言可能小幅退步。

### 細節

**Meng, Li et al. — Is Word Segmentation Necessary for Deep Learning of Chinese Representations?（ACL 2019，Shannon.AI／Stanford）**〔top-tier，但早於 2022〕〔自報〕— [ACL Anthology](https://aclanthology.org/P19-1314/)
- Seq2Seq＋attention 架構下，中→英用字當單位比用詞平均高 0.83 BLEU，英→中高 2.67–3.13 BLEU。原因是詞層級模型會遇到 OOV 與資料稀疏。
- 這是 2019 年、從頭訓練的 NMT，結論是「詞切分讓表示變差」。這跟 LLM 吃「已經切好 token、外加空白提示」的情況不同，**不能直接套用**。

**Zheng, Liu, …, Choi, Smith — Broken Tokens? Your Language Model can Secretly Handle Non-Canonical Tokenizations（NeurIPS 2025 spotlight，UW／AI2／Stanford）**〔top-tier〕〔自報〕— [arXiv 2506.19004](https://arxiv.org/abs/2506.19004)
- 指令微調模型在隨機 token 切法下，最多能保留原本 93.4% 的表現；逐字切法下保留 90.8%。
- 附錄 C.1 用 **Qwen2.5-7B-Instruct** 測中文：CMMLU 從 77.8 降到 74.2，中文 GSM 從 78.8 降到 76.8，掉約 3%。
- 意義：**強迫改變 token 邊界的傷害不大**，但也不是零。只在必要處打斷，比全句加空白安全。
- 沒有測翻譯。

**Lu et al. — Chain-of-Dictionary Prompting Elicits Translation in LLMs（EMNLP 2024 main，Microsoft／CUHK）**〔top-tier〕〔自報〕— [ACL Anthology](https://aclanthology.org/2024.emnlp-main.55/)
- 在 prompt 裡附上部分來源詞的多語詞典鏈。低資源語言大幅進步，例如英→塞爾維亞文（西里爾字母）chrF++ 從 3.08 升到 42.63。
- 但附錄表 7（英→X，GPT-3.5-turbo）中，**高資源 CJK 方向小幅退步**：zho_Hans 30.99→28.92、zho_Hant 23.83→23.80、jpn 33.95→31.96、yue_Hant 22.36→17.41。作者在 Limitations 承認少數語言會輕微退步。
- 注意這是**英文原文**加詞典，不是中文原文斷詞。

**Semenov, Zouhar, Kocmi et al. — Findings of the WMT 2023 Shared Task on MT with Terminologies**〔WMT 官方 findings〕〔獨立〕— [ACL Anthology](https://aclanthology.org/2023.wmt-1.54/)
- 包含 **中→英**。給系統逐句的術語詞典之後，各系統的 chrF 與 COMET 普遍上升。
- 但給「隨機但正確的詞對」也會上升。主辦方提醒，**任何從參考譯文洩漏出來的資訊都會讓分數變高**。也就是說，詞典提示有用，但前提是詞典本身正確。

**Haslett — Tokenization Changes Meaning in LLMs: Evidence from Chinese（Computational Linguistics 51(3), 2025，HKUST）**〔top-tier 期刊〕— [ACL Anthology](https://aclanthology.org/2025.cl-3.3/)
- 研究對象是 GPT-4／GPT-4o／Llama 3。token 與語言單位（部首、詞綴）對不齊時，模型對字義的表示會**系統性地被扭曲**。
- 它的研究對象是部首，不是詞界；但機制和第 0 節觀察到的「排便」token 一樣：token 邊界錯了，意義就被帶偏。

### 已知或可能的壞處
- **斷詞錯誤會傳遞下去**：segmenter 本身會錯（OOV recall 約 79–92%）。如果它把「豬排便當」切錯，等於明確告訴 LLM 一個錯的答案，比不給還糟。這點沒有 top-tier 的量化研究〔推估〕。
- **流暢度與分布外輸入**：全句加空白會讓 token 數大約變兩倍（空白自成一個 token，見第 0 節），也偏離訓練分布。NeurIPS 2025 的結果顯示損失約 3%，屬可接受，但沒有測翻譯。
- **譯文混入空白或簡體**：Qwen 會把繁中改寫成簡體（ACL 2025）。加了空白之後，譯文也可能殘留空白或斷詞符號，需要後處理〔推估〕。
- **日文、泰文**：2022 年以後無 top-tier 證據。

---

## Q4. 不做完整斷詞，也能達到同樣效果的替代方案

1. **只在「token 跨越詞界」的地方打斷**（第 0 節的機制）：
   - 用 Qwen 的 tokenizer 切 token，同時用 segmenter 或詞典算出詞界。
   - 只有當某個多字 token 跨越詞界（例如「排便」跨過「豬排｜便當」）時，才在那個位置插入空白。
   - 大部分句子不會被改動，盡量減少分布外輸入；錯誤也只會在少數衝突點傳遞。
   - 證據：機制來自本地檢查；「打斷 token 的代價小」有 NeurIPS 2025 支持。整體效果**無 top-tier 證據**，需要自己做 A/B 驗證。
2. **詞典或詞義提示**：在 prompt 後面附上「豬排便當＝豚カツ弁当」或「便當＝弁当」，類似 DiPMT／CoD。
   - 證據：CoD（EMNLP 2024）、WMT23 術語 findings；Meta 的 DiPMT 屬公司報告（[arXiv 2302.07856](https://arxiv.org/abs/2302.07856)）。
   - 風險：對高資源的 CJK 方向可能小幅退步（CoD 表 7）。提示應只在偵測到歧義時才加。
   - 這個做法也能直接處理本資料夾的主題「中日同形詞」，例如「手紙」「勉強」。
3. **用 ASR 自己的切分或時間戳**：**沒有用**。openai/whisper 的 `split_to_word_tokens` 對 zh／ja／th／lo／my／yue 是「只要能解碼成完整 unicode 就切一刀」，切出來的是 BPE 片段，不是詞（[whisper/tokenizer.py](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py)，官方原始碼）。
4. **直接送 token ID 指定切法**：Ollama 的 `/api/generate`／`/api/chat` 只接受字串，沒有 tokenize 或 token-array 的介面（[Ollama API docs](https://github.com/ollama/ollama/blob/main/docs/api.md)），**現有架構做不到**。llama.cpp server 似乎接受 token 陣列，但我沒有驗證〔不確定〕。
5. **改用其他分隔符**：例如「豬排·便當」或 `【豬排】【便當】`。這類寫法同樣能強迫 token 邊界，但都比空白更偏離分布，而且會漏到譯文裡。**無證據**，不建議當第一選擇。

---

## 排序建議

**結論先講**：符合全部限制（不用 PyTorch、繁中、< 30 ms、授權可用於 MIT）的選項確實存在。但**沒有任何一個是「2023 年以後、針對繁中、授權寬鬆、有釋出模型」的最新研究成果**。最新的 top-tier 有監督 CWS（ACL 2023 MCCWS）用的是 bert-base，沒有提供可商用的繁中權重；最新的 LLM 斷詞（ACL 2025）準確率和速度都不夠。所以建議改從問題的根源下手：只修 token 邊界，不追求完整斷詞。

### 第 1 名：token 邊界衝突修補（Qwen tokenizer × 斷詞器）
- **做法**：
  1. 用 OpenCC（已是相依）把句子轉成簡體，再丟給 **spacy-pkuseg**（MIT，2026-10-26 發布）斷詞，把詞界依字元位置對回原本的繁中句子。
  2. 用 Qwen 的 `tokenizer.json` 做 BPE，可以用純 Python，或在使用者同意後加 HF `tokenizers`。
  3. 只在「多字 token 跨越詞界」的地方插入空白。
- **預期準確率**：
  - pkuseg 本身：簡體跨資料集約 91 F1（官方自報）。繁中經 t2s 轉換後**沒有評測**〔不確定〕。
  - 因為只處理衝突點，錯誤影響的範圍比全句斷詞小。
  - 對翻譯的實際效果要用 `experiments/kanji_false_friends` 的句子做 A/B 測試才知道。
- **延遲**：pkuseg 約 1 ms（自報）＋ BPE < 1 ms＋ OpenCC < 1 ms，合計推估 **< 5 ms**。
- **安裝大小**：spacy-pkuseg wheel 約 2.5 MB＋ srsly＋模型。pkuseg 的 mixed 模型或 spaCy 的 `spacy_ontonotes` 模型約數十 MB，可以讓使用者下載。
- **授權**：MIT／Apache-2.0。**風險**：pkuseg 預訓練模型的授權在 PyPI 上標為 Other/Proprietary，要先確認；spaCy 的 `spacy_ontonotes` 模型是 MIT（OntoNotes 由 Explosion 取得商用授權），比較乾淨。
- **其他風險**：
  - 簡繁轉換在少數情況下不是一字對一字，會讓位置對不回去。
  - pkuseg 是 2019 年的 CRF 架構，使用者可能也會覺得「不是最新」。
  - 斷詞器錯的時候，反而會引入錯誤的切點。

### 第 2 名：CKIP ALBERT-tiny／BERT-tiny WS 轉 ONNX（技術上最好，但卡在授權）
- **預期準確率**：原生繁中，WS F1 96.66–96.98（官方自報；README 只寫「繁中語料」，測試集推定為 ASBC〔不確定〕）。是唯一以台灣繁中語料訓練的小模型。
- **延遲**：1–3 ms（推估）。
- **安裝大小**：約 16 MB（fp32），onnxruntime 已是相依。
- **授權**：**GPL-3.0（含權重），與 MIT 專案不相容**。取得 CKIP 的另外授權或法律確認之前，不能採用。
- **其他風險**：模型約 5–6 年沒更新；轉 ONNX 需要一次性的離線 PyTorch 環境；ASBC 是書面語料，口語表現沒有評測。
- 可以當第 1 名的斷詞器替換件：授權問題解決後，直接換掉 pkuseg。

### 第 3 名：只在歧義處加詞典提示（不改原文）
- **做法**：沿用第 1 名的衝突偵測。偵測到衝突時，不在原文插空白，而是在 prompt 加一行詞義提示，例如「豬排便當＝豚カツ弁当」。詞典可以自建（食物、台灣用語、中日同形詞），或用 CC-CEDICT（CC BY-SA 4.0，詞典資料本身受 share-alike 約束）。
- **預期效果**：有 top-tier 證據顯示詞典提示整體有幫助（WMT23、EMNLP 2024），但高資源 CJK 方向可能小幅退步。
- **延遲**：prompt 多幾十個 token，LLM 端增加幾 ms 到幾十 ms〔推估〕。
- **授權**：要看詞典來源。
- **其他風險**：詞典覆蓋率；提示錯了會直接帶偏譯文。

### 不建議
- **LLM 自己斷詞**：準確率約 72–88、會改成簡體、要多一次呼叫。
- **LTP、HanLP 模型**：商用受限。
- **CkipTagger、Stanza、LAC**：需要 TF／PyTorch／Paddle。
- **全句加空白**：比第 1 名更容易傳遞錯誤，也更偏離訓練分布。

### 建議的下一步（實作前）
1. 用 HF `tokenizers` 確認第 0 節的 BPE 結果，並確認 Ollama 的 qwen3.5 GGUF 用的是同一份 vocab。
2. 用 `experiments/kanji_false_friends` 的句子做三組 A/B：(a) 原文 (b) 全句加空白 (c) 只在衝突點加空白。人工比對譯文。
3. 依結果決定要不要正式採用 spacy-pkuseg（新增相依前會先問使用者）。
