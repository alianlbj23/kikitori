# 08 評測方法：怎麼確定「A 比 B 好」

v0.2.0 的推薦組合（GPU 即時 large-v3-turbo、完整 large-v3、翻譯溫度 0、術語表）都不是看模型作者的宣傳決定的，而是在本機重跑評測、做統計檢定後才改。
這一篇用兩個實驗當例子，說明評測的每個步驟為什麼這樣做：

- `experiments/vtuber_eval/`：日語語音辨識（ASR），指標是 CER。
- `experiments/translate_eval/`：日 → 中／英翻譯，指標是 chrF、BLEU、COMET。

方法的出處是 `reports/VTuber 直播模型選型調查.md`「評測」一節。兩個實驗都不是正式程式，結果寫在各自的 README。

---

## 1. 為什麼不能直接看公開數字

- **公開基準和直播口語方向相反**：kotoba-whisper-v2.0 在它的訓練領域（電視語音 ReazonSpeech）贏 large-v3，但在 YouTube 口語上反而差 3.9–4.7 個百分點（`vtuber_eval` 結論第 3 點）。只看公開數字，會把它設成日語預設。
- **自動指標可能被「訓練」過**：WMT25 主辦方指出 Hunyuan 系列用 XCOMET 類指標當強化學習的獎勵，所以 HY-MT 的 COMET 分數要打折看（`translate_eval` 結論第 4 點）。
- **差一兩個百分點可能只是運氣**：評測集只有幾百句，換一批句子結果就可能翻轉。所以每個數字都要附信賴區間，比較兩個模型時要做成對檢定。

---

## 2. 資料：主體、校準、壓力測試

`vtuber_eval` 的資料分三種用途（README「資料」）：

| 用途 | 資料 | 為什麼需要 |
|---|---|---|
| **主體**（下結論用） | YODAS `ja000`：CC 授權的 YouTube 影片與上傳者字幕，500 段、72 支影片 | 最接近直播口語的公開資料 |
| **校準**（檢查管線沒算錯） | FLEURS（朗讀）、ReazonSpeech（電視） | 有公開的 CER 可以對照（見第 5 節） |
| **壓力測試** | YODAS 200 段混入 MUSAN 的無人聲 BGM（SNR 20／15／10／5／0 dB）與前景音效；另有 50 段純 BGM／音效 | 直播幾乎都有 BGM；純非語音用來量幻覺 |

幾個容易忽略的細節：

- **語言檢查在影片層級做**：YODAS 有些是英語影片配日文翻譯字幕。用 Whisper medium 對每支影片隨機 6 段做語言偵測，整支影片保留或排除——不在句子層級挑，否則等於「挑模型辨識得好的句子」，評測會偏向那個模型。
- **混音的 SNR 只算人聲部分**：語音功率只算 Silero VAD 判為人聲的 32 ms 視窗，背景算整段。否則停頓多的句子看起來 SNR 比較低。
- **五個 SNR 用同一段 BGM**：每段固定一首 BGM 與起點，所以「10 dB 比乾淨差多少」是同一句話、同一段音樂的成對比較，不會混進「這次剛好抽到比較吵的音樂」。
- **抽樣固定 seed**（20261105），重跑完全一樣。

---

## 3. CER：字元錯誤率與正規化

日文沒有空白分詞，所以用 **CER**（Character Error Rate）：

```text
CER = 所有句子的編輯距離總和 ÷ 所有參考文字的字數總和
```

是 **corpus-level**（先加總再相除），不是每句 CER 的平均——後者會讓「參考只有 2 個字、錯了 1 個」的短句佔 50% 的權重。

### 正規化：先把「不算錯的差異」拿掉

辨識結果「今日は、いい天気！」與參考「今日はいい天気」應該算全對。`experiments/vtuber_eval/textnorm.py` 的 `normalize_basic()`（版本號 `whisper-basic-nospace-v1`）逐字重現 OpenAI Whisper 的 `BasicTextNormalizer()`，再去掉所有空白：

1. 小寫；刪掉 ASCII 的 `[…]`、`<…>`、`(…)` 括號內文字
2. NFKC（全形英數 → 半形、半形片假名 → 全形）
3. Unicode 的標記（M）、符號（S）、標點（P）換成空白
4. 去掉所有空白

為什麼要「逐字重現」別人的正規化、連它的怪處也照抄（括號規則在 NFKC 之前、只認 ASCII 括號，所以全形「（笑）」只刪括號、留下「笑」）？
因為 kotoba-whisper 公開的 CER 就是這樣算的（`normalizer(text).replace(" ", "")`）。正規化一樣，第 5 節的校準才有意義。正規化有**版本號**，結果表標明用的是哪一版。

另有一個變體 `ja-fillers-v1`（`normalize_no_fillers()`）：刪掉「えー」「あの」「ｗｗｗ」等填充詞再算。結果幾乎沒差（Whisper 本身就會省略填充詞），所以只當參考欄。

### 過濾後的 CER

ASR 輸出會先經過 app 的幻覺過濾（`kikitori.asr.rejection_reason()`）。被濾掉的句子在使用者畫面上就是**沒有字幕**，所以計分時當成「輸出空字串」＝整句刪除。
**主要指標是過濾後 CER**：它同時懲罰「辨識錯」和「過濾太兇把真話丟掉」。例如 medium 在日文語音上 no_speech 常落在 0.82–0.90，被硬門檻 0.8 丟掉約 9% 的句子，過濾後 CER 從 24.5 變 32.8（README 結論第 7 點）。

---

## 4. 信賴區間：blockwise bootstrap

### bootstrap 是什麼

只有一份評測集，但想知道「換一批類似的句子，CER 會落在哪個範圍」。bootstrap 的做法：

1. 從評測集中**有放回地**抽出同樣數量的樣本，算一次 CER。
2. 重複 1,000 次，得到 1,000 個 CER。
3. 取第 2.5 與第 97.5 百分位，就是 95% 信賴區間。

### 為什麼要以「影片」為單位（block）

同一支影片的句子不是獨立的：同一位說話者、同樣的收音、同樣的字幕風格。如果逐句抽樣，等於假裝有 500 個獨立樣本，區間會窄得不合理。
所以 YODAS 以**影片**為 block：每次抽 72 支影片（有放回），把抽到的影片的所有句子拿來算 CER（`experiments/vtuber_eval/analyze.py` 的 `cer_with_ci()`）：

```python
idx = rng.integers(0, n_blocks, size=(1000, n_blocks))       # 每一列是一次重抽的 block 編號
boot = edits[idx].sum(axis=1) / chars[idx].sum(axis=1)       # 每次重抽的 corpus CER
lo, hi = np.percentile(boot, [2.5, 97.5])
```

FLEURS 以「句子 id」為 block（同一句有多位朗讀者），ReazonSpeech 沒有節目 id 只能逐句（README 註明區間可能偏窄）。

### 成對比較

比較模型 A 與 B 時，**兩者用同一組重抽索引**（`paired()`）：每次重抽算 ΔCER = CER(A) − CER(B)。

- **ΔCER 的 95% CI** 不含 0 → 差異顯著。
- **probability of improvement**（Bisani & Ney 2004）：1,000 次重抽中 A 的 CER 比 B 低的比例。

為什麼要成對：兩個模型面對的是同一批難句。各自算區間再看有沒有重疊，會把「這批句子本來就難」的變異算兩次，結果太保守。

### 讀一行結果

`vtuber_eval` README 的成對比較表：

| 資料／條件 | 模式 | A | B | ΔCER [95% CI] | P(A 較好) |
|---|---|---|---|---|---|
| YODAS 乾淨 | fast | small | large-v3-turbo | +5.6 [+2.9, +8.2] | 0.000 |
| YODAS 乾淨 | final | large-v3-turbo | large-v3 | +0.7 [−1.0, +2.5] | 0.208 |
| YODAS music 5 dB | final | large-v3-turbo | large-v3 | +5.3 [+0.8, +10.1] | 0.011 |

- 第一行：small 比 turbo 多錯 5.6 個百分點，區間整個在 0 以上，1,000 次中 small 沒有一次贏 → 即時路徑改用 turbo。
- 第二行：乾淨語音上 turbo 與 large-v3 的差異區間跨過 0 → **打平**，不能說誰好。
- 第三行：有 BGM 時 large-v3 明顯較穩 → 直播幾乎都有 BGM，完整路徑改用 large-v3。

這三行就是 `hardware/recommend.py` 的 `GPU_LADDERS` 為什麼長那樣（`docs/specs/hardware.md`「8 GB 以上等級的組合」）。

---

## 5. 校準：確認管線沒算錯

自己的評測管線也可能有 bug（正規化寫錯、音訊取樣率不對）。所以先在有公開數字的資料上重現：

| 資料 | 本專案 | 公開數字 |
|---|---|---|
| ReazonSpeech，large-v3 | 14.5 [12.2, 17.0] | 14.9（kotoba README） |
| ReazonSpeech，kotoba final | 12.2 [10.2, 14.3] | 11.6 |
| FLEURS，large-v3 | 4.4 [3.6, 5.1] | 4.9（OpenAI） |
| FLEURS，large-v3-turbo | 4.8 [4.0, 5.6] | 4.7 |

都在誤差範圍內，才相信 YODAS 上的結果。

---

## 6. 翻譯：chrF、BLEU、COMET

翻譯沒有唯一正解，需要**人工參考譯文**。`translate_eval` 用的資料（README「資料」）：WMT25 ja→zh speech（YouTube 轉寫，59 篇、726 片段）與 social（Mastodon 貼文）、CoVoST 2 ja→en、BSD ja→en（商務對話）。

### 三個指標各看什麼

| 指標 | 怎麼算 | 適合 | 弱點 |
|---|---|---|---|
| **chrF** | 字元 6-gram 的 F 分數（β=2，偏重召回） | 不分詞的中文 | 輸出過長反而可能得分高 |
| **BLEU** | 詞 n-gram 精確率＋長度懲罰（中文 `tokenize="zh"`） | 與文獻比較 | 對同義改寫很嚴苛 |
| **COMET**（`Unbabel/wmt22-comet-da`） | 神經網路依原文、譯文、參考譯文打分 | 看語意 | 可能被針對性訓練過（見第 1 節） |

三者不一致時，README 以 **COMET 與可疑輸出計數**為主。

### 字形要先統一

kikitori 輸出台灣正體，WMT 的參考譯文是簡體中文。直接比的話，繁簡差異會淹沒真正的品質差。所以**兩邊都用 OpenCC `t2s` 轉成簡體再計分**。
`t2s` 只轉字形不轉詞彙，台灣用語（影片 vs 视频）仍會被扣分——所有受測設定都輸出 zh-TW，所以這個偏差對它們一樣，比較仍然公平，但絕對分數偏低。

### paired bootstrap 與 p 值

和 CER 一樣是成對重抽：同一組索引下算兩個設定的分數差。

- chrF、BLEU 用 sacrebleu 內建的 `PairedTest(test_type="bs")`。
- COMET 自己寫（`experiments/translate_eval/score_comet.py` 的 `paired_bootstrap()`）：

  ```python
  idx = rng.integers(0, n, size=(1000, n))
  deltas = (other - base)[idx].mean(axis=1)          # 每次重抽的平均差
  observed = (other - base).mean()
  p = (1 + sum(|deltas − observed| ≥ |observed|)) / 1001   # 把分布平移到以 0 為中心
  ```

  意思是：「如果真正的差是 0，看到這麼大差異的機率有多高」。
- 重抽單位：speech 以**文件**為單位（相當於 blockwise），其他以句子為單位。

### 讀結果時的保留

README「注意事項與限制」列了幾點，值得記住：

- **多重比較**：成對比較約 40 組都沒有校正。p 在 0.01–0.05 之間的結果要保守看（20 組裡平均就會有 1 組 p < 0.05 是運氣）。
- **溫度 0.7 是單次取樣**，分數本身帶隨機性；溫度 0 的比較沒有這個問題。
- social 的貼文串只有 12 篇、以句為單位重抽，句子之間不獨立，區間可能偏窄。

### 例子：術語表該不該預設開啟

`translate_eval` README「Milestone 16：術語表開／關」：

- VTuber 風格句 30 句（沒有參考譯文，人工檢查）：術語表讓 赤スパ、推し、メン限 等直播用語都翻對，1 格動詞意思變錯。
- WMT25 speech 726 片段只有 5 個出現術語：COMET −0.0005 [−0.0009, −0.0001]，p = 0.037（未校正）。

統計上偵測得到差異，但大小只有前文效果（+0.0146）的 1/30，而且來自改變的幾句措辭。判斷：**效果小到可以忽略、直播用語的改善明顯 → 預設開啟**。
這個例子說明「顯著」不等於「重要」：要同時看效果大小與它影響的是什麼。

### 數不到的東西用計數補

分數抓不到「輸出完全壞掉」的少數句子，所以另外逐句分類（`report.classify()`）：翻譯錯誤、空輸出、錯誤語言、簡體殘留、附加文字、疑似翻出前文。
這些計數直接變成正式程式的修正（README「本實驗順帶發現的正式程式問題」）：特殊 token 碎片清除、選擇性繁簡轉換、前文保險（`translate/context_guard.py`）。

---

## 7. 從評測到程式碼

| 決策 | 證據 | 程式 |
|---|---|---|
| GPU 即時用 large-v3-turbo | small vs turbo Δ +5.6 [+2.9, +8.2] | `hardware/recommend.py` `GPU_LADDERS` |
| GPU 完整用 large-v3，可用 int8_float16 | BGM 5 dB turbo vs v3 Δ +5.3；int8 vs f16 Δ −0.4 [−1.0, +0.3] | 同上 |
| 不提供 kotoba-whisper | YODAS 較差、純 BGM 上 46–50/50 輸出「ごめん」 | 不在 `asr/registry.py` |
| 翻譯溫度 0 | ja→zh chrF 全部顯著提高，沒有資料集變差 | `translate/presets.py` |
| 前文保留＋前文保險 | speech COMET +0.0146（9B）／+0.0320（4B）；4B 偶爾翻出前文 | `translate/context_guard.py` |
| 術語表預設開 | 見上 | `translate/glossary.py`、設定 `translation.glossary.builtin` |
| ja hard 片語加「次の動画でお会いしましょう。」 | 純 BGM 上通過過濾的輸出 | `experiments/hallucination_lexicon/lexicon.json` → `asr/hallucination_lexicon.py` |

---

## 8. 這些評測沒回答的問題

- **YODAS 不是 VTuber**：沒有角色聲線、尖叫、多人連動、歌回。YODAS 字幕也不是逐字稿（17% 的段落疑似對不上），所以絕對 CER 偏高（23–35%），只能做相對比較。
- **延遲在共用 GPU 下量**（同時有其他實驗在跑 Ollama），只能比相對快慢；絕對延遲以 `experiments/asr_benchmark/` 為準。
- 翻譯的輸入是 WMT 給的轉寫，沒有經過 kikitori 自己的 ASR：ASR 錯誤傳到翻譯的影響沒量。
- 新推薦組合在 12 GB 卡上與翻譯模型同時執行的端到端延遲與顯示記憶體還沒量（`docs/specs/vtuber-profile.md`「驗證」）。

## 怎麼重跑

```text
uv run python -I experiments/vtuber_eval/menu.py      準備資料 → 建立評測集 → 執行 → 計分
uv run python -I experiments/translate_eval/menu.py
```

改了過濾規則之後，vtuber_eval 只要重跑「計分」（第 4 步）就會以新規則重新判定，不必重跑模型。
