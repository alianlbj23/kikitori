# 「世界主要語言」選單：20 種語言的 ASR／翻譯模型覆蓋與品質證據

> 調查日期：2026-11-05。對象語言（20）：en、zh-TW、zh-CN、ja、ko、es、fr、de、pt、ru、it、ar、hi、id、vi、th、tr、pl、nl、uk。
> 標記：**[自報/self-reported]**＝模型開發者自己的論文、model card 或 repo；**[第三方]**＝非開發者的研究；**[預印本]**＝arXiv、未經同儕審查。
> 方法：
> - Whisper 論文 PDF（arXiv 2212.04356）用 `pdftotext -layout` 抽字。表格欄名是旋轉文字，抽出後順序是反的；已經用「語言代碼字母序」還原欄位順序，並用 large-v2 的 Japanese 5.3、English 4.2 等已知值交叉核對。
> - OpenAI README 的 large-v3 語言分解圖和 turbo 公告圖都是 matplotlib SVG。數值標籤是從 SVG 內嵌的文字註解讀出的。README 圖上 large-v2 沒有數字標籤，只有空心外框長條，數值是用 log 座標軸刻度（2.5／5／10／20／40）把長條長度換算回來的，誤差約 ±0.1。這個換算方法拿 large-v3 長條試過，換算值和圖上的文字標籤完全一致。
> - 和既有筆記的關係：日語 ASR 細節見 `asr_models.md`，翻譯品質與授權細節見 `translation_models.md`。本檔只補「多語言覆蓋」這個面向。

---

## 1. Whisper 各語言的 WER／CER

### Takeaway
- 依 large-v3 在 FLEURS 與 Common Voice 15 兩個資料集中**較差的那個值**來分級：
  - **A 級**：en、zh-TW、ko、es、de、pt、ru、it、id、th、pl、nl
  - **B 級**：zh-CN、ja、fr、ar、hi、tr、uk
  - **vi 例外**：FLEURS 是 A 級（8.6），CV15 卻有 39.8，是 C 級
- **換成 turbo 後**：th（CV15 20.8）、ar（23.7）、hi（24.1）退到 C 級；en、ja、zh-TW、fr 從 A 或邊緣掉到 B。
- **small 和 base 退化很大**。paper 的 large-v2 時代數字顯示，base 在 FLEURS 上只有 en（8.9）、es（9.9）還在 10 以內，hi 超過 100。
- **hi 的 Whisper 訓練資料只有 12 小時**（paper Appendix E），是 20 種語言中最少的。

### 1.1 指標：WER 還是 CER？（已核對原文）
- **Whisper 論文**（Appendix C「Text Standardization」）的寫法是：「we put a space between every letter for the languages that do not use spaces to separate words, namely **Chinese, Japanese, Thai, Lao, and Burmese**, effectively measuring the character error rate instead」。
  - 所以 paper 的表格中，zh、ja、th 實際上是 CER。
  - **ko 用的是 WER**（以空白分詞）。
  - 來源：[Whisper paper arXiv 2212.04356](https://arxiv.org/abs/2212.04356)
- **OpenAI README 的 large-v3 語言分解圖**：「using WERs … or CER (character error rates, shown in *Italic*)」。圖中用斜體標示的是 **Japanese、Korean、Mandarin、Mandarin (TW)、Mandarin (CN)、Cantonese、Thai**。
  - 所以 README 圖的 **ko 是 CER**，和 paper 不同，兩邊的 ko 數字不能直接比較（paper 的 ko FLEURS 14.3 是 WER，README 是 3.1 CER）。
  - 來源：[openai/whisper README](https://github.com/openai/whisper/blob/main/README.md)，圖檔是 `f4619d66-…svg`，2023-11-06 用 matplotlib 產生
- **SenseVoice 論文**也用 CER 評估 zh、yue、ja、ko、th，並且把所有中文**轉成簡體**之後才計分 — [FunAudioLLM arXiv 2407.04051](https://arxiv.org/abs/2407.04051) §4.1

### 1.2 主表

**資料來源與欄位說明**
- **P-FL v2**：paper Table 13「WER (%) on Fleurs」，large-v2。
- **P-CV9 v2**：paper Table 11「WER (%) on CommonVoice9」，large-v2。
- **R-FL v2/v3**、**R-CV15 v2/v3**：OpenAI README 圖（FLEURS 與 Common Voice 15）。v3 讀自圖上標籤；v2 由外框長條換算。
- **T-FL／T-CV15 turbo**：[turbo 公告 discussion #2363](https://github.com/openai/whisper/discussions/2363) 的圖，標題是「WER or CER (%, beam size = 5)」。圖只收錄 large-v3 ≤ 20% 的語言（CV15 前 34 名、FLEURS 前 42 名）。同一張圖裡的 v3 值和 README 最多差 0.1。
- 以上全部是 **[自報]**，由 OpenAI 或 OpenAI 維護者（jongwook）發布。
- 「—」表示沒有列出。粗體指標＝CER。

| 語言 | 指標 (paper / README) | P-FL v2 | R-FL v2 | **R-FL v3** | **T-FL turbo** | P-CV9 v2 | R-CV15 v2 | **R-CV15 v3** | **T-CV15 turbo** | 分級 v3 | 分級 turbo |
|---|---|---|---|---|---|---|---|---|---|---|---|
| en | WER / WER | 4.2 | 4.0 | 4.1 | 4.4 | 9.4 | 9.9 | 9.3 | 10.6 | A | B（CV） |
| zh-CN | **CER** / **CER** | 14.7 | 7.5 | 7.7 | 8.3 | 26.8 | 13.1 | 12.8 | 14.3 | B | B |
| zh-TW | — / **CER** | — | — | — | — | — | 8.3 | 8.2 | 10.3 | A（只有 CV15） | B |
| ja | **CER** / **CER** | 5.3 | 5.3 | 4.9 | 4.7 | 9.1 | 12.0 | 10.3 | 12.3 | B（邊緣） | B |
| ko | WER / **CER** | 14.3 (WER) | 3.1 | 3.1 | 3.2 | — | 6.5 | 5.2 | 5.6 | A | A |
| es | WER | 3.0 | 3.1 | 2.8 | 3.1 | 5.6 | 5.9 | 4.7 | 5.5 | A | A |
| fr | WER | 8.3 | 5.6 | 5.3 | 5.8 | 13.9 | 12.4 | 10.8 | 12.1 | B（邊緣） | B |
| de | WER | 4.5 | 5.0 | 4.9 | 5.8 | 6.4 | 6.7 | 5.7 | 6.9 | A | A |
| pt | WER | 4.3 | 4.2 | 4.1 | 4.2 | 6.3 | 7.4 | 5.9 | 6.9 | A | A |
| ru | WER | 5.6 | 5.5 | 5.0 | 5.3 | 7.1 | 7.6 | 5.8 | 6.3 | A | A |
| it | WER | 4.0 | 3.6 | 3.0 | 3.2 | 7.1 | 7.1 | 5.5 | 6.3 | A | A |
| ar | WER | 16.0 | 10.9 | 9.6 | 10.2 | 53.8 | 19.0 | 15.1 | 23.7 | B | **C** |
| hi | WER | 21.5 | 21.7 | 17.0 | 18.5 | 21.9 | 21.8 | 16.9 | 24.1 | B | **C** |
| id | WER | 7.1 | 7.0 | 6.1 | 6.9 | 8.5 | 8.8 | 7.2 | 8.9 | A | A |
| vi | WER | 10.3 | 10.4 | 8.6 | 9.6 | 18.2 | 44.1 | 39.8 | —（v3 > 20，未收錄） | A(FL)/**C**(CV) | A(FL)/C?(CV) |
| th | **CER** / **CER** | 11.5 | 11.8 | 8.4 | 13.9 | 8.0 | 9.0 | 5.8 | 20.8 | A | **C**（CV） |
| tr | WER | 8.4 | 7.5 | 6.7 | 6.9 | 14.5 | 14.3 | 12.0 | 17.0 | B | B |
| pl | WER | 5.4 | 5.3 | 4.6 | 5.4 | 7.6 | 7.4 | 6.0 | 7.3 | A | A |
| nl | WER | 6.7 | 6.5 | 5.2 | 5.9 | 5.8 | 5.6 | 4.3 | 5.8 | A | A |
| uk | WER | 8.6 | 7.5 | 6.4 | 6.9 | — | 16.2 | 13.6 | 16.2 | B | B |

**分級規則**：取 FLEURS 與 CV15 中較差的值；≤10 為 A，10–20 為 B，>20 為 C。

**補充說明**
- turbo 公告的原文 [自報]：「the turbo model performs similarly to large-v2」，並且「shows larger degradation on some languages like **Thai and Cantonese**」。CV15 的數字可以印證這一點：Cantonese (CN) v3 10.8 → turbo 46.1，(HK) 16.0 → 42.9；Thai 5.8 → 20.8。公告也提到「Whisper turbo performs better on FLEURS, which consists of cleaner recordings than Common Voice」— [discussion #2363](https://github.com/openai/whisper/discussions/2363)
- large-v3 的 model card 只說「10% to 20% reduction of errors compared to Whisper large-v2」，以及「strong ASR results in ~10 languages」；訓練資料是 1M 小時弱標註加 4M 小時偽標註 [自報] — [openai/whisper-large-v3](https://huggingface.co/openai/whisper-large-v3)
- turbo 的 model card 只說「minor quality degradation」，沒有逐語言數字。架構是 decoder 從 32 層減到 4 層 — [openai/whisper-large-v3-turbo](https://huggingface.co/openai/whisper-large-v3-turbo)
- **paper 和 README 的 large-v2 數字不一致**：zh 14.7 vs 7.5、ar 16.0 vs 10.9、fr 8.3 vs 5.6。OpenAI 沒有說明 README 用了什麼評估流程或 normalizer，所以**兩組數字不要混用**。分級以 README 的 v3 和 turbo 為準。
- CV15 的「Mandarin (TW)」對應 Common Voice 的 zh-TW locale（台灣華語）。但 Whisper 只有一個 `zh` language token（`tokenizer.py`：`"zh": "chinese"`，另外 v3 新增 `"yue": "cantonese"`），**沒有官方方法控制輸出繁體或簡體**。README 也沒有說明 TW 的 CER 是否先做過字形轉換 — [tokenizer.py](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py)

### 1.3 小模型的退化（paper 給出了每個尺寸、每個語言的數字）
paper 的 Table 11（CV9）和 Table 13（FLEURS）都列出 tiny、base、small、medium、large、large-v2 六個尺寸 [自報]。下表是 FLEURS 的 WER／CER（zh、ja、th 為 CER；ko 為 WER）：

| 語言 | tiny | base | small | medium | large-v2 |
|---|---|---|---|---|---|
| en | 12.4 | 8.9 | 6.1 | 4.4 | 4.2 |
| zh | 40.5 | 34.1 | 20.8 | 12.1 | 14.7 |
| ja | 37.0 | 22.8 | 12.0 | 7.1 | 5.3 |
| ko | 36.1 | 27.8 | 19.6 | 16.4 | 14.3 |
| es | 15.9 | 9.9 | 5.6 | 3.6 | 3.0 |
| fr | 41.4 | 28.5 | 15.0 | 8.7 | 8.3 |
| de | 27.8 | 17.9 | 10.2 | 6.5 | 4.5 |
| pt | 20.1 | 13.0 | 7.3 | 5.0 | 4.3 |
| ru | 31.1 | 20.5 | 11.4 | 7.2 | 5.6 |
| it | 29.8 | 17.9 | 9.8 | 5.2 | 4.0 |
| ar | 63.4 | 48.8 | 30.6 | 20.4 | 16.0 |
| hi | 102.3 | 101.1 | 38.4 | 26.8 | 21.5 |
| id | 51.7 | 33.1 | 16.3 | 10.2 | 7.1 |
| vi | 60.0 | 40.5 | 21.2 | 12.7 | 10.3 |
| th | 58.8 | 38.2 | 21.9 | 15.4 | 11.5 |
| tr | 42.5 | 27.5 | 15.9 | 10.4 | 8.4 |
| pl | 45.6 | 30.8 | 14.7 | 8.0 | 5.4 |
| nl | 49.0 | 33.0 | 16.4 | 9.9 | 6.7 |
| uk | 51.2 | 37.7 | 19.3 | 11.6 | 8.6 |

**CV9 上更差**。small／base 的例子：ar 66.4 / 84.4、hi 43.6 / 106.5、vi 33.3 / 51.6、zh 29.4 / 44.9、ja 14.0 / 24.2、en 14.5 / 21.9（Table 11）。

**推論的分級**：
- **small**：A＝en、es、pt、it；C＝zh、ar、hi、vi、th；其餘 B。
- **base**：只有 en、es 是 A，de、pt、it 是 B，其餘都是 C。

這些是 large-v2 時代的 checkpoint；small 和 base 之後沒有新版本，所以數字仍然適用。

### 1.4 訓練資料量（解釋了語言之間的差異）
- paper 指出 WER 和訓練時數有高度相關（Figure 3，「squared correlation coefficient … 0.83」）。
- Appendix E Figure 11 列出各語言的訓練時數 [自報]：
  - 20 種語言依時數排序：en 438,218、zh 23,446、de 13,344、es 11,100、ru 9,761、fr 9,752、pt 8,573、ko 7,993、ja 7,054、tr 4,333、pl 4,278、it 2,585、nl 2,077、id 1,014、ar 739、uk 697、vi 691、th 226、**hi 12**
  - 單位是小時，是 v1／v2 的 680k 小時資料集。v3 的 5M 小時資料沒有公開逐語言統計。
  - 來源：[Whisper paper](https://arxiv.org/abs/2212.04356)

---

## 2. 翻譯模型的官方語言清單與 20 種語言的覆蓋情況

### Takeaway
- **HY-MT1.5 和 Qwen 系列都涵蓋全部 20 種語言**，而且都**明列繁體中文**：HY-MT 列為 zh-Hant「繁体中文」，Qwen3 列為「Traditional Chinese」。但兩者都沒有明說是「台灣用語」。
- **TranslateGemma** 的訓練資料（SFT）也涵蓋全部 20 種，但 **zh-TW、pt、uk 只有「英文→該語言」方向**；非英文之間的語言對只有少數幾組（例如 zh-CN↔ja），其他都是 zero-shot。
- **SakuraLLM 只做 ja→簡中**，而且**禁止商用**。
- **授權**：
  - Qwen3.5：Apache-2.0，最乾淨。
  - HY-MT1.5：**Tencent HY Community License，在 EU、UK、南韓不適用**；另有 MAU 超過 1 億需另行授權的條款。
  - TranslateGemma／Gemma 3：Gemma Terms of Use，HF repo 是 gated（需登入並同意條款）。

### 2.1 各模型的官方清單
- **Tencent HY-MT1.5（1.8B／7B）**
  - 卡片原文：「mutual translation across **33 languages** and incorporating **5 ethnic and dialect variations**」。表格列了 38 個項目：zh、en、fr、pt、es、ja、tr、ru、ar、ko、th、it、de、vi、ms、id、tl、hi、**zh-Hant（Traditional Chinese／繁体中文）**、pl、cs、nl、km、my、fa、gu、ur、te、mr、he、bn、ta、uk、bo、kk、mn、ug、yue。
  - HF metadata 寫「36 languages」，和內文對不上。
  - 官方 prompt 分兩種：ZH⇔XX 用中文 prompt「将以下文本翻译为{target_language}，注意只需要输出翻译后的结果，不要额外解释：」；XX⇔XX 用英文 prompt「Translate the following segment into {target_language}, without additional explanation.」
  - 功能：terminology intervention、contextual translation、formatted translation。
  - 來源：[tencent/HY-MT1.5-7B](https://huggingface.co/tencent/HY-MT1.5-7B)，1.8B 的 HF tags 與 7B 相同
  - 授權：repo 內的 `License.txt` 標題是「TENCENT HY COMMUNITY LICENSE AGREEMENT」（HY-MT1.5 release date 2025-12-30），原文寫「THIS LICENSE AGREEMENT DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA」，「Territory」也排除這三地。另外，若發布當時的 MAU **超過 1 億**，需要另向 Tencent 申請授權。1.8B 和 7B 的 License.txt 內容完全相同 — [HY-MT1.5-1.8B License.txt](https://huggingface.co/tencent/HY-MT1.5-1.8B/blob/main/License.txt)。**這一點補上了 `translation_models.md` Q6 的 Gap。**
  - 卡片列出的官方量化版本：FP8、GPTQ-Int4。卡片沒有列官方 GGUF。

- **Qwen3.5（4B／9B）**
  - 卡片只寫「Expanded support to **201 languages and dialects**」，**沒有列出語言清單**。
  - 唯一的翻譯數字是 WMT24++（55 種語言平均，XCOMET-XXL）：9B 72.6、4B 66.6 [自報]。沒有逐語言的數字。
  - 授權 Apache-2.0。
  - 來源：[Qwen/Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)
  - Qwen 團隊的發布文（Alibaba Cloud Community 轉載，2026-02-17）：「expanded our language and dialect support from 119 to 201」，詞表從 150k 擴大到 250k；同樣沒有清單 — [Alibaba Cloud blog 602894](https://www.alibabacloud.com/blog/602894)
  - 有明列語言的是上一代 **Qwen3**（119 種）。Sino-Tibetan 類別下寫「Chinese (Simplified Chinese, **Traditional Chinese**, Cantonese)」；en、fr、de、pt、ru、it、hi、pl、nl、uk、es、ar、id、vi、th、tr、ja、ko 也都在清單上 — [Qwen3 blog](https://qwenlm.github.io/blog/qwen3/)
  - **推論**：201 是 119 的擴充，所以 Qwen3.5 應該仍然涵蓋這 20 種語言。不過官方沒有 Qwen3.5 的清單（見 Gaps）。

- **Google TranslateGemma（4B／12B／27B，基於 Gemma 3）**
  - 卡片寫「translation tasks across **55 languages**」，但卡片上沒有清單。
  - 語言代碼必須是 ISO 639-1 兩字母代碼，或帶地區的寫法（例如 `en_US`、`en-GB`）；**chat template 遇到不支援的代碼會直接報錯**。
  - repo 是 gated，授權是 Gemma terms。
  - 4B 的 WMT24++ MetricX 為 5.32 [自報]。
  - 來源：[google/translategemma-4b-it](https://huggingface.co/google/translategemma-4b-it)
  - 技術報告 Appendix C 的 SFT 語言表 [自報，預印本]（[arXiv 2601.09012](https://arxiv.org/abs/2601.09012)）：
    - **Table 5（和英文雙向互譯）**：包含 zh-CN（Chinese）、nl、fr、de、hi、id、it、ja、ko、ar（Modern Standard Arabic）、pl、ru、es、th、tr、vi。
    - **Table 6（只有英文→該語言）**：包含 **zh-TW（Chinese (Taiwan)）**、**pt-BR、pt-PT**、**uk**。
    - **Table 7（不含英文的語言對）**：只有 9 組，例如 zh-CN–ja、cs–uk、cs–de、yue–zh-Hant（Taiwanese Mandarin）。抽字時方向箭頭遺失，所以無法確認是 ja→zh 還是 zh→ja。
  - WMT24++ 自報的分數全部是 en→X，包含 en→zh_TW、en→uk_UA、en→pt_BR 等。沒有任何 X→Y（X≠en）的評測。
- **Google Gemma 3**
  - 卡片只寫「multilingual support in **over 140 languages**」，沒有列出清單。
  - WMT24++ 分數（IT，0-shot）：4B 46.8、12B 51.6 [自報]。
  - 授權是 Gemma Terms of Use。
  - 來源：[Gemma 3 model card](https://ai.google.dev/gemma/docs/core/model_card_3)
- **SakuraLLM**
  - 官方描述是「适配轻小说/Galgame的日中翻译大模型」。system prompt 寫明「将日文翻译成**简体中文**」，**沒有提到繁體中文**。
  - 授權：模型是 **CC BY-NC-SA 4.0**，原文「所有模型与其衍生模型均禁止任何形式的商用」；程式碼是 GPL-3.0。
  - 目前版本：Qwen2.5 的 1.5B／7B／14B v1.0，以及 Qwen1.5 的 32B v0.9 等。
  - 來源：[SakuraLLM GitHub](https://github.com/SakuraLLM/SakuraLLM)

### 2.2 覆蓋矩陣
符號說明：
- ✓＝官方清單明列。
- (✓)＝只能從上一代清單或擴充敘述推得。
- en↔＝SFT 有和英文雙向互譯的資料。
- en→＝SFT 只有英文到該語言的方向。
- ✗＝不支援。

| 語言 | Qwen3.5 4B/9B | HY-MT1.5 1.8B/7B | TranslateGemma | Gemma 3 | SakuraLLM |
|---|---|---|---|---|---|
| en | (✓) | ✓ | 樞紐語言 | 無清單 | ✗ |
| zh-TW | (✓)（Qwen3 明列 Traditional Chinese） | ✓ zh-Hant（沒有說明是否為台灣用語） | **en→ 而已**（zh-TW） | 無清單 | ✗（只輸出簡中） |
| zh-CN | (✓) | ✓ zh | en↔ | 無清單 | 目標語言 ✓ |
| ja | (✓) | ✓ | en↔（另有 zh-CN–ja 配對） | 無清單 | 來源語言 ✓ |
| ko | (✓) | ✓ | en↔ | 無清單 | ✗ |
| es、fr、de、it、ru、pl、nl | (✓) | ✓ | en↔ | 無清單 | ✗ |
| pt | (✓) | ✓ | **en→ 而已**（pt-BR／pt-PT） | 無清單 | ✗ |
| ar | (✓) | ✓ | en↔（MSA） | 無清單 | ✗ |
| hi、id、vi、th、tr | (✓) | ✓ | en↔ | 無清單 | ✗ |
| uk | (✓) | ✓ | **en→ 而已**（另有 cs–uk） | 無清單 | ✗ |

**推論**
- 對「任意來源 → 任意目標」的選單來說，**HY-MT1.5 是唯一官方明列 20 種語言、而且有雙向 XX⇔XX prompt 的專用 MT 模型**。但地區授權限制必須在 UI 上警告（EU、UK、KR）。
- TranslateGemma 用在「非英文→非英文」（例如 ja→ko）或「pt／uk／zh-TW 為來源」時，都超出訓練分布。可以考慮以英文為樞紐做兩段翻譯，但延遲會加倍，而且沒有證據支持這樣做品質比較好。
- SakuraLLM 只適合當 ja→zh 的專用選項。目標選 zh-TW 時，需要再用 OpenCC 轉換。

---

## 3. Whisper 在非語音片段上的 hallucination 片語：有沒有多語言清單？

### Takeaway
**沒有找到任何同儕審查或官方來源列出非英語的 hallucination 片語。** 目前唯一的系統性清單（Barański et al., ICASSP 2025 的 Bag of Hallucinations）是**強制 language=English** 跑出來的。

### Cited Findings
- **Barański et al.，ICASSP 2025**（AGH Kraków）的實驗設定：「we use the Whisper large-v3 … **we set language to English**, temperature to zero … greedy decoding」 — [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)
  - 301,317 個非語音檔中，40.3% 產生 hallucination。最常見的片語：「thank you」、「thanks for watching」、「so」、「subtitles by the amara org community」等。BoH 的過濾條件是「English n-gram LM log prob < −10 而且出現次數 > 4」，**只適用於英語**。
  - **和 kikitori 相關的發現（Table I，200 檔子集，長度可調）**：非語音片段長 1 s 時 hallucination 率 52.1%、10 s 時 11.6%、20 s 時 27.4%、30 s 時 62.3%。也就是說，**很短和很長的非語音片段最容易產生 hallucination**。
- **Calm-Whisper**（arXiv 2505.12969，Elm／Concordia／Mila）[預印本]：只微調 3 個 decoder attention head，宣稱非語音 hallucination 減少超過 80%。訓練用的是 UrbanSound8K 和 MUSAN 的非語音資料。論文沒有提供任何語言別的片語清單。
- **Jasiński et al. 2026**（同一個 AGH 團隊，arXiv 2606.23060）[預印本]：研究 hallucination 偵測，用的是 HALAS 資料集（Interspeech 2026）。本次沒有看到任何語言別的片語清單。
- OpenAI 官方的說法只有通則：large-v3 卡片寫「the predictions may include texts that are not actually spoken in the audio input (i.e. hallucination)」；paper §6 提到「complete hallucination」和「repeat loops」。兩者都**沒有列出片語** — [whisper-large-v3](https://huggingface.co/openai/whisper-large-v3)、[paper](https://arxiv.org/abs/2212.04356)
- 社群常見的「字幕由 Amara.org 社区提供」、「ご視聴ありがとうございました」等例子，只出現在 GitHub discussion 的個人回報（[#1606](https://github.com/openai/whisper/discussions/1606)），**不符合可信來源的標準，不列入引用**。

### Inferences
- 多語言的 BoH 只能由 kikitori 自己建立：對每個語言，強制 `language=<lang>`，拿靜音、BGM、噪音跑 large-v3／turbo，蒐集出現頻率高的輸出。這是把 Barański 的方法直接套到其他語言上。
- 依 Table I 的結果，VAD 切出來的 1 s 左右短片段，正是 hallucination 的高風險區，應該優先處理。

---

## 4. 短語句的語言偵測可靠度，以及強制指定語言的效果

### Takeaway
- **Whisper 的 LID 在 1 秒左右的語句上很不可靠**：large-v2 在 Common Voice 上 1 s 的準確率只有 23.5%，2 s 是 79.9%，3 s 是 93.1%。
- 在 FLEURS 完整長度的語句上，large-v2 在 82 種有訓練資料的語言中準確率是 80.3%。
- **沒有找到直接比較「強制語言 vs 自動偵測」WER 的同儕審查研究。** 不過官方文件明確建議：已知語言時就指定。

### Cited Findings
- **Whisper paper §3.6 Table 5**：zero-shot Whisper 在 FLEURS LID 的準確率是 64.5%（102 種語言），w2v-bert-51 是 71.4%，mSLAM-CTC 是 77.7%。因為有 20 種語言 Whisper 沒有訓練資料，準確率上限是 80.4%；「On the 82 overlapping languages the best Whisper model achieves **80.3%** accuracy」[自報] — [paper](https://arxiv.org/abs/2212.04356)
- 同一篇 §3.5 提到，LID 錯誤也會污染訓練資料。例如被當成 Welsh 翻譯資料的音訊，大多其實是被誤判的英語 [自報]。
- **Shen, Lu, Kawai（NICT），「Generative Linguistic Representation for Spoken Language Identification」**（arXiv 2312.10964，2023-12）[第三方，預印本]：
  - 測試語言是 MLS 的 8 種：en、de、nl、fr、es、it、pt、pl。測試語句切成 1／2／3 s 三種長度。
  - Whisper 原生 LID 的準確率（%，依 1 s／2 s／3 s 排列）：

    | 模型 | MLS | VoxLingua107 | CommonVoice |
    |---|---|---|---|
    | **base** | 47.5／83.4／92.9 | 49.5／78.5／87.6 | **18.3**／64.4／83.2 |
    | **large-v2** | 61.7／93.5／98.0 | 68.0／90.3／95.4 | **23.5**／79.9／93.1 |

  - 論文的說法是 Whisper「performance rapidly deteriorates with shorter utterances」。
  - 論文**沒有說明** Whisper baseline 是否把候選語言限制在這 8 種。如果沒有限制，對實際 99 種語言的情況來說，這些數字反而偏樂觀。
  - 來源：[arXiv 2312.10964](https://arxiv.org/abs/2312.10964)（Table 1、Table 2）
- **官方建議指定語言**：
  - large-v3 和 turbo 的 model card 都寫「If the source audio language is known a-priori, it can be passed as an argument」。
  - openai-whisper 的 `transcribe.py` 在自動偵測時會印出「Detecting language using up to the first 30 seconds. Use `--language` to specify the language」，也就是**只用前 30 秒判斷一次語言**。
  - 來源：[transcribe.py](https://github.com/openai/whisper/blob/main/whisper/transcribe.py)、[whisper-large-v3](https://huggingface.co/openai/whisper-large-v3)
- faster-whisper 提供 `language_detection_threshold`（預設 0.5）、`language_detection_segments`（預設 1）和 `multilingual`（對每個 segment 做偵測）三個參數 — [faster_whisper/transcribe.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/transcribe.py)
- **SenseVoice 論文**的 Figure 7 是「Comparison of SenseVoice and Whisper on Common Voice, **with or without LID**」，比較有沒有給 ground-truth LID 時的表現。但數字只在圖片裡，本次沒能讀出 [自報] — [arXiv 2407.04051](https://arxiv.org/abs/2407.04051)

### Inferences
- kikitori 的使用者會在選單上**明確選擇來源語言**，所以應該一律強制 `language=<code>`，不要用自動偵測。VAD 切出來的片段常常只有 1–3 s，正好落在 LID 失準的區間。
- 如果要提供「自動偵測」選項，建議累積幾秒以上的語音再判斷，並且鎖定結果；不要每個 segment 各自偵測（會在語言之間跳來跳去）。這是推論，沒有找到實測數字支持。

---

## 5. 除了 Whisper，有 CT2 或 ONNX 版、而且在特定語言明顯更好的 ASR？

### Takeaway
- **英語**：distil-large-v3 並**沒有比 large-v3 更準**（短句 WER 9.7 vs 8.4），優勢只在速度（快 6.3 倍）。新版 **distil-large-v3.5** 在短句上和 large-v3、turbo 打平（7.08 vs 7.12 vs 7.30），速度約是 turbo 的 1.46 倍，而且有**官方 CT2 版**，可以當 en 的「快速」選項。
- **簡體中文**：**SenseVoice-Small** 和 **Paraformer-zh** 在中文基準上明顯優於 Whisper-L-V3。例如 AISHELL-1 CER 是 2.96、1.95 vs 5.14；WenetSpeech meeting 是 7.44、6.97 vs 18.87。但這是 Alibaba 自報的數字，而且需要另寫 ONNX 後端。
- 其他語言沒有找到「明顯更好」的強證據。日語見 `asr_models.md`。

### Cited Findings
- **distil-whisper/distil-large-v3** [自報]：
  - 只支援英語，756M 參數（large-v3 是 1550M），MIT 授權。
  - 相對延遲 6.3（large-v3 為 1.0）。
  - OOD WER 比較：

    | 模型 | 短句 | sequential 長音訊 | chunked 長音訊 |
    |---|---|---|---|
    | distil-large-v3 | 9.7 | 10.8 | 10.9 |
    | large-v3 | 8.4 | 10.0 | 11.0 |

  - 卡片把部分改善歸因於「lower hallucinations」和訓練時的 WER filter。
  - CT2 版：卡片沒有附 repo 連結。faster-whisper 內建的對照表是 `"distil-large-v3": "Systran/faster-distil-whisper-large-v3"`（Systran 是 faster-whisper 的維護者）。
  - 來源：[distil-large-v3 card](https://huggingface.co/distil-whisper/distil-large-v3)、[faster_whisper/utils.py](https://github.com/SYSTRAN/faster-whisper/blob/master/faster_whisper/utils.py)
- **distil-whisper/distil-large-v3.5** [自報]：
  - 只支援英語，756M 參數，MIT 授權。
  - 短句 OOD WER：distil-large-v3.5 7.08、distil-large-v3 7.53、large-v3 7.12、large-v3-turbo 7.30。
  - 長音訊 OOD WER：distil-large-v3.5 11.39、distil-large-v3 11.6、turbo 10.25（長音訊上 turbo 比較好）。
  - 相對 RTFx：distil-large-v3.5 1.46、distil-large-v3 1.44，以 turbo 為 1.0。
  - **官方 CT2 repo 是 `distil-whisper/distil-large-v3.5-ct2`**，faster-whisper 的對照表也指向它。
  - 來源：[distil-large-v3.5 card](https://huggingface.co/distil-whisper/distil-large-v3.5)
- **SenseVoice-Small／Paraformer-zh**（FunAudioLLM 技術報告 Table 6）[自報，預印本]：
  - 評分方式：zh、yue、ja、ko 用 CER，en 用 WER。中文先轉成簡體再計分。
  - 原表的列名在抽字時錯位了一列。下表已經用 Whisper-L-V3 的已知值核對過（CV en 9.39、CV ja 10.34、LibriSpeech clean 1.82，與 README 一致）。

    | 測試集 | Whisper-L-V3 | SenseVoice-S | SenseVoice-L | Paraformer-zh |
    |---|---|---|---|---|
    | AISHELL-1 | 5.14 | 2.96 | 2.09 | 1.95 |
    | AISHELL-2 ios | 4.96 | 3.80 | 3.04 | 2.85 |
    | WenetSpeech meeting | 18.87 | 7.44 | 6.73 | 6.97 |
    | WenetSpeech net | 10.48 | 7.84 | 6.01 | 6.74 |
    | CV zh-CN | 12.55 | 10.78 | 7.68 | 10.30 |
    | CV en | 9.39 | 14.71 | 9.00 | — |
    | CV ja | 10.34 | 11.96 | 9.19 | — |
    | CV ko | 5.59 | 8.28 | 5.21 | — |

  - 速度：SenseVoice-S 比 Whisper-L-V3 快 15 倍以上（A800，batch 1）。
  - 也就是說，**SenseVoice-S 只在中文和粵語上贏 Whisper，en、ja、ko 都輸。**
  - 來源：[arXiv 2407.04051](https://arxiv.org/abs/2407.04051)
  - SenseVoice 的 ONNX 版與授權（自訂 model-license）見 `asr_models.md` Q1-B。
- **NVIDIA parakeet-tdt-0.6b-v3** [自報]：
  - 支援 25 種歐洲語言（20 種中涵蓋 en、es、fr、de、pt、ru、it、pl、nl、uk），會自動偵測語言，CC-BY-4.0。
  - FLEURS WER：en 4.85、es 3.45、fr 5.15、de 5.04、pt 4.76、ru 5.51、it 3.00、pl 7.31、nl 7.48、uk 6.79。
  - 對照 README 的 large-v3 FLEURS（4.1／2.8／5.3／4.9／4.1／5.0／3.0／4.6／5.2／6.4），**沒有任何一種語言明顯更好**，而且兩邊的 normalizer 不同。
  - 卡片沒有官方 ONNX，只連到第三方的 onnx-asr Space。
  - 來源：[parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
  - → 不建議當作「準確度更好」的替代品，最多是 CPU 速度上的選項（推論）。
- kotoba-whisper（日語專用，有官方 CT2 版）：見 `asr_models.md`。它在 CV8 和 JSUT 上輸給 large-v3，只在 in-domain 的 ReazonSpeech 上贏。

---

## 給「語言選單」的整理（推論，供設計參考）
- **ASR 預設**：turbo 在 ko、es、de、pt、ru、it、id、pl、nl 上與 v3 差距很小。**th、ar、hi、vi（CV）建議推薦 large-v3，不要用 turbo**，因為 turbo 在 CV15 上掉到 C 級（vi 在 CV15 上 v3 本身就是 C 級）。zh 和 ja 用 turbo 屬於 B 級。
- **small／base 只建議用在 en、es**（加上 pt、it 用 small）。其他語言選 small 或 base 時應該顯示品質警告。
- **zh-TW 作為來源語言**：Whisper 只有一個 `zh` token，輸出字形不受控制，需要接 OpenCC。
- **翻譯預設**：Qwen3.5 是 Apache-2.0，201 種語言，最安全。HY-MT1.5 適合當「品質優先」選項，但要附上地區授權警告。TranslateGemma 只推薦在「英文來源」或「英文目標」時使用。

---

## Gaps
- **large-v3 和 turbo 沒有 OpenAI 公布的「表格」數字**。本檔的 v3 和 turbo 值是從官方 SVG 讀出的標籤；large-v2 是用長條長度換算的（±0.1）。OpenAI 沒有說明 README 和 turbo 圖用了什麼 normalizer 或解碼設定（turbo 圖只標示 beam=5），也沒有解釋為什麼 paper 的 v2 數字和 README 的 v2 數字不同（zh、ar、fr 差距最大）。
- **沒有任何官方的逐語言數字是在自發口語或直播語料上測的**。FLEURS 和 CV 都是朗讀語音，實際 VTuber 直播的品質應該會更差。
- **zh-TW 只有 CV15 的數字**，而且不清楚計分前是否做過繁簡轉換。FLEURS 只有 cmn（簡體）。
- small、base、medium 沒有 v3 世代的版本，所以也沒有 CV15 的數字。distil-large-v3 和 v3.5 沒有非英語版本。
- **Qwen3.5 的 201 種語言沒有官方清單**；qwen.ai 的部落格頁面是 JS 渲染，本次沒能讀取。對 20 種語言的覆蓋是從 Qwen3 的 119 種清單推得的。
- **TranslateGemma 卡片沒有列出 55 種語言**。報告 Table 7 的方向箭頭在抽字時遺失。WMT24++ 附錄表格在 sr 以後的列有欄位錯位，本次沒有引用那部分數字。
- 各翻譯模型都**沒有找到 20×20 語言對的逐語言評測**；HY-MT1.5 只有 Flores ZH⇔XX 和 EN⇔XX 的平均值。
- **多語言 hallucination 片語**：沒有可信來源。
- **強制語言 vs 自動偵測的 WER 消融實驗**：沒有找到同儕審查的研究。SenseVoice 論文 Figure 7 有相關比較，但只有圖片，數字沒有讀出。
- **Whisper v3 的 5M 小時資料沒有逐語言時數**。
- **SenseVoice 和 Paraformer 有沒有官方（FunASR／ModelScope）ONNX、授權全文**，本次沒有再查證。Paraformer-zh 的支援語言範圍（中文加部分英文）也沒有讀卡片確認。
