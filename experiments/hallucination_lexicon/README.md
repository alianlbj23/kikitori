# 幻覺片語實測：Whisper 在非語音上依語言輸出什麼

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05。
> 結果（`lexicon.json`）供 `kikitori.asr.filtering` 的依語言幻覺片語表使用
> （規格見 `docs/specs/languages-i18n.md`「翻譯模型與提示詞 → 幻覺過濾」）。

## 目的

Whisper 在沒有人聲的音訊上常輸出訓練資料裡的字幕套話（「ご視聴ありがとうございました」
「字幕由Amara.org社区提供」）。唯一的系統性清單是 Barański et al.（ICASSP 2025）的
Bag of Hallucinations，但只有英文（強制 `language=en`）。kikitori 支援 19 種 Whisper
辨識語言，所以用同樣的方法自己量：對每個語言強制 `language=<code>`，拿確定沒有人聲的
音訊跑 app 實際使用的模型與解碼參數，收集重複出現的輸出，整理成依語言的 `hard`／`soft` 片語表。

## 怎麼重跑

```text
uv run python -I experiments/hallucination_lexicon/menu.py
```

選單：

1. **產生非語音片段（含 VAD 判定）**：從 MUSAN 切片段、產生合成噪音，全部用 app 的 Silero VAD
   判定，挑出要送 ASR 的片段寫進 `results/`。需要先用 `experiments/silero_reset/menu.py`
   下載 MUSAN。重新產生會要求刪除舊的辨識結果（片段編號會對不上）。
2. **逐模型辨識**：選模型（small／large-v3-turbo／large-v3／base）與裝置。CUDA 使用 app
   安裝的 cuBLAS 12（`%LOCALAPPDATA%\kikitori\gpu-runtime\cublas-12.8.4.1`）；模型不在快取
   （`%LOCALAPPDATA%\kikitori\models`）時先下載。每個模型在獨立子行程執行，輸出逐列寫進
   `results/raw-<模型>.jsonl`，中斷後再選一次會從沒跑完的片段續跑。
3. **彙整**：以**目前的** `rejection_reason()` 重新判定所有原始輸出，再模擬加上 `lexicon.json`，
   產生 `results/summary.md`（各表格）與 `results/candidates.json`（每語言的候選片語）。
   調整過濾規則或片語表後只要重跑這一步。

`results/`（含 MUSAN 衍生音訊與逐句輸出）已被 git 忽略；只有人工整理的 `lexicon.json`
（只有片語文字，沒有音訊）與本文件進 repo。

## 環境

- CPU：AMD Ryzen 7 5700X；GPU：NVIDIA RTX 5070 12 GB（驅動 621.01）；Windows 11。
- Python 3.12.15、ctranslate2 4.8.5、numpy 2.5.4、onnxruntime 1.31.1；CUDA float16。
- 量測時 GPU 同時有 Ollama（qwen3.5:9b）與執行中的 kikitori 引擎；兩個模型並行跑，
  只影響速度，不影響輸出。

## 方法

### 非語音素材

片段長度 1／2／4／6／10 s（app 在 Silero VAD 之後送出 ≤ 10 s 的句子）。

| 種類 | 內容 | 每個長度 |
|---|---|---|
| silence | 數位靜音 | 1 |
| white | 高斯白噪音 −60／−45／−30 dBFS | 3 |
| pink | 粉紅噪音 −50／−35 dBFS | 2 |
| burst | 模擬遊戲音效：−60 dBFS 底噪上疊濾波噪音脈衝或掃頻音（50–400 ms、指數衰減） | 2 |
| musan-noise | MUSAN noise（free-sound、sound-bible，930 檔） | 見下 |
| musan-music | MUSAN music 中 ANNOTATIONS 標為**沒有人聲**（第 3 欄 `N`）的 419 檔 | 見下 |

MUSAN 片段隨機挑檔案與位置（固定種子 20261105），響度正規化成 RMS −40／−30／−20 dBFS
之一（不削波），原始 RMS < −70 dBFS 的位置略過。

**VAD 篩選**：每段都用 app 的 `Segmenter(SileroVad(), PauseEndpoint(0.6, 10.0))`（其餘為預設參數）
從頭餵入再 `flush()`，有產生句子就算「VAD 通過」。MUSAN 片段只有約 2% 會通過，所以先切一個大池子
（噪音、音樂各 400 段 × 5 種長度 = 4000 段）全部跑 VAD，再保留**全部 VAD 通過的**＋**每種、每個長度
10 段沒通過的**。合成片段全部保留。

### 辨識

- 模型：見結果。CUDA float16。
- 解碼：app 的 `FAST_DECODE`（greedy、最多 128 token）與 `FINAL_DECODE`（beam 5、溫度 fallback 0／0.2／0.4）。
- 語言：強制 19 個 Whisper 語言碼 `ja en zh ko es fr de pt ru it ar hi id vi th tr pl nl uk`（繁簡中文共用 `zh`），不做語言偵測。
- 每段 encode 一次，再對 19 語言 × 2 模式解碼（encoder 輸出與語言、解碼參數無關）。解碼走
  `WhisperTranscriber` 的內部方法；每次執行前抽樣比對與 `transcribe()` 的結果完全相同才繼續。
- 送 ASR 的是**整段**片段，不是 VAD 切出來的部分（見「限制」）。

### 判定

- **幻覺** = 輸出經 `normalize_for_match()`（NFKC、casefold、只留字母與數字）後非空，而且
  `rejection_reason()` 回傳 None（會變成字幕）。**幻覺率** = 幻覺數 ÷ 解碼次數。
- large-v3-turbo 的 `no_speech` 恆為 0（`no_speech_reliable=False`），與 app 相同改用推薦組合的
  即時模型（small、`FAST_DECODE`）對同一段、同一語言的 no_speech。所以「large-v3-turbo final」
  就是 app 推薦組合（即時 small＋完整 turbo）的完整路徑。
- 「目前的過濾」是彙整當下 `kikitori.asr.filtering` 的規則（已是依語言分表的版本，含既有清單）。
- **套用片語表後**：模擬 `kikitori.asr.filtering` 的依語言語意——所有語言（含不分語言的 `*`）的
  `hard` 片語取聯集，不論辨識語言，整句相等就濾掉（Whisper 對套話的語言標籤不可靠）；`soft` 片語只比對
  辨識語言與 `*` 的表，整句相等且 no_speech > `soft_no_speech_threshold`（0.3）才濾掉。
- **交叉驗證**：片語表是看著同一批資料整理的，所以另外把片段依編號奇偶分兩半，每半只套用
  「在另一半至少 2 段片段出現過」的片語，估計對沒看過的非語音還有多少效果。

### 片語的分類原則

候選 = 同一語言、正規化後相同的輸出出現在 ≥ 3 段不同的片段（跨模型、模式），而且至少 2 段
會通過目前的過濾（漏網）；原有的清單項目全部保留。再人工分類：

- **hard**（不論語言、整句相等就丟）：字幕署名／製作單位／版權（「Субтитры сделал DimaTorzok」
  「Untertitelung des ZDF, 2020」「Altyazı M.K.」「TV Gelderland 2021」）、影片片尾的「感謝收看」
  「訂閱頻道」整句（沿用既有清單把「Thank you for watching.」「ご視聴ありがとうございました」列為 hard
  的判斷），以及含人名、頻道名、年份等直播中不會整句說出的句子。因為 hard 對所有語言生效，
  另外確認正規化後沒有太短（< 4 字元）或與任一 soft 片語相同的項目。
- **soft**（辨識語言相同且 no_speech 偏高才丟）：真人也可能單獨說的短句與感嘆詞——「Gracias.」
  「Vielen Dank.」「감사합니다.」「ん」「E aí」「KONIEC!」，字幕標籤式的單字（「Musik」「MUZIEK」
  「موسيقى」「muzyka」），以及「Продолжение следует...」「¡Suscríbete!」這類直播中也可能說的話。
- 不列：只出現在 1–2 段、或從未漏網（已被 no_speech／logprob／重複判定擋下）的輸出；
  重複迴圈（較長的「आप आप आप…」）大多已被 `repetitive` 擋下，只收常見且會漏網的短重複。
- 印地語、泰語沒有找到可確定是署名或套話的句子，全部放 soft。

## 結果（2026-11-05）

### 規模

- 片段 221 段：合成 40 段（5 長度 × 8）＋ MUSAN 181 段（其中 **VAD 通過 81 段**：噪音 36、音樂 45）。
  VAD 篩選池 4000 段 MUSAN 中只有 81 段（2.0%）通過；合成片段（含 −30 dBFS 白噪音、遊戲音效脈衝）
  0/40 通過。越長越容易通過：1 s 0.5%、2 s 1.0%、4 s 2.1%、6 s 2.6%、10 s 3.9%。
- 模型：small、large-v3-turbo 跑完 221 段；large-v3 因時間只跑了 132 段（固定亂數順序的隨機子樣本，
  約 60%）；base 沒有跑。
- 解碼 21,812 次（221 段 × 19 語言 × 2 模式 × 2 模型 ＋ 132 段 × 38）。執行時間（GPU 同時跑兩個模型）：small 44 分、
  turbo 12 分、large-v3 32 分跑 132 段。small 的完整路徑最慢：非語音常觸發溫度 fallback 與長重複迴圈。
- 共用 encode 的解碼與 `transcribe()` 的抽樣比對（每個模型 2 段 × 2 語言 × 2 模式）全部一致。

### 幻覺率：套用前 → 套用後（同一批資料）→ 套用後（交叉驗證）

數值為 %，分母是該設定的全部解碼次數（片段 × 19 語言）。「large-v3-turbo final」就是 app 推薦組合的
完整路徑（no_speech 用 small 即時路徑的值）。

| 片段 | small fast | small final | large-v3-turbo fast | large-v3-turbo final | large-v3 fast | large-v3 final |
|---|---|---|---|---|---|---|
| 全部 | 8.7 → 2.3 → 2.3 | 20.9 → 6.2 → 6.5 | 31.1 → 3.4 → 3.7 | 37.9 → 4.4 → 4.8 | 40.1 → 3.4 → 4.0 | 42.1 → 3.3 → 3.7 |
| 只看 VAD 通過 | 12.8 → 5.1 → 5.2 | 30.7 → 13.5 → 14.1 | 43.1 → 7.7 → 8.3 | 52.8 → 10.3 → 11.0 | 50.5 → 7.8 → 9.1 | 53.3 → 7.9 → 8.6 |

> 「全部」的 MUSAN 部分是刻意加入 VAD 通過者的分層樣本，不代表自然分布；app 實際會送進 ASR 的是
> 「只看 VAD 通過」那一列。粗略換算回 app：非語音片段約 2.0% 通過 VAD，其中 turbo 完整路徑約 53%
> 產生字幕 → 每段非語音約 1.1% 產生假字幕；套用片語表後約 0.2%。

在 turbo final 的 VAD 通過片段上，原本漏網的 813 次輸出中，hard 片語擋下 323、soft 片語擋下 331、
剩 159 次；剩下的幾乎都是只出現一次的擬聲詞（「Ziiiib!」「¡Veeeee!」）或 MUSAN 噪音裡疑似真人的聲音
（多個語言都輸出「Stop button.」「Oh, my God.」）。small fast 漏網的 197 次中 hard 擋 92、soft 擋 26。

### 依片段長度（套用前 → 後）

全部片段：

| 長度 | small fast | small final | large-v3-turbo fast | large-v3-turbo final | large-v3 fast | large-v3 final |
|---|---|---|---|---|---|---|
| 1 s | 1.0 → 0.2 | 2.1 → 0.2 | 3.8 → 0.2 | 4.8 → 0.0 | 4.7 → 0.0 | 4.7 → 0.0 |
| 2 s | 7.6 → 2.0 | 15.6 → 6.0 | 18.6 → 2.0 | 23.7 → 3.9 | 19.1 → 2.4 | 21.7 → 3.1 |
| 4 s | 8.4 → 3.0 | 20.1 → 8.7 | 28.7 → 6.5 | 35.2 → 7.1 | 44.4 → 2.8 | 45.9 → 3.1 |
| 6 s | 10.0 → 3.3 | 25.1 → 6.9 | 38.2 → 3.4 | 46.3 → 4.3 | 50.8 → 8.1 | 53.4 → 7.3 |
| 10 s | 12.7 → 2.7 | 31.5 → 8.9 | 49.5 → 3.7 | 59.6 → 5.3 | 71.3 → 7.3 | 74.3 → 6.1 |

只看 VAD 通過（1 s 只有 4 段、2 s 8 段，數字不穩）：

| 長度 | small fast | small final | large-v3-turbo fast | large-v3-turbo final | large-v3 fast | large-v3 final |
|---|---|---|---|---|---|---|
| 1 s | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 |
| 2 s | 11.2 → 9.2 | 28.3 → 25.0 | 18.4 → 7.9 | 32.2 → 17.1 | 15.8 → 14.5 | 23.7 → 18.4 |
| 4 s | 12.4 → 6.5 | 27.6 → 17.0 | 33.7 → 14.6 | 43.3 → 17.3 | 55.9 → 6.9 | 57.1 → 7.3 |
| 6 s | 10.3 → 5.3 | 27.8 → 10.3 | 43.9 → 5.8 | 52.4 → 7.0 | 49.0 → 8.1 | 52.2 → 8.9 |
| 10 s | 16.8 → 3.9 | 38.9 → 12.6 | 59.6 → 6.3 | 70.5 → 8.3 | 66.2 → 7.5 | 69.3 → 5.3 |

與 Barański et al. 的「1 s 與很長的非語音最容易幻覺」不同，這裡 1 s 幾乎不會變成字幕、越長越多。
差異在於這裡算的是**通過過濾**的比例：1 s 的輸出多半 no_speech 很高，被既有規則擋下；
他們只量英文、large-v3、greedy、沒有過濾。

### 依素材種類（全部片段，套用前 → 後）

| 種類 | small fast | small final | large-v3-turbo fast | large-v3-turbo final | large-v3 fast | large-v3 final |
|---|---|---|---|---|---|---|
| silence | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 |
| white | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 0.0 → 0.0 | 33.8 → 0.0 | 36.1 → 0.0 |
| pink | 2.1 → 0.0 | 6.8 → 0.5 | 13.7 → 0.0 | 15.8 → 0.0 | 18.1 → 0.6 | 18.1 → 0.0 |
| burst | 22.1 → 0.5 | 38.9 → 2.6 | 67.9 → 1.1 | 71.1 → 0.5 | 42.1 → 3.0 | 41.4 → 0.8 |
| musan-noise | 10.7 → 4.5 | 29.9 → 13.0 | 42.2 → 7.9 | 54.8 → 9.9 | 28.7 → 7.7 | 30.6 → 8.0 |
| musan-music | 8.0 → 1.2 | 16.8 → 2.3 | 25.6 → 0.7 | 29.4 → 1.2 | 52.9 → 1.3 | 55.6 → 1.3 |

### 依語言（app 推薦組合：即時 small fast／完整 turbo final，套用前 → 後）

| 語言 | small fast 全部 | turbo final 全部 | small fast VAD 通過 | turbo final VAD 通過 |
|---|---|---|---|---|
| ja | 2.3 → 1.8 | 6.8 → 5.0 | 4.9 → 3.7 | 13.6 → 9.9 |
| en | 2.7 → 2.3 | 7.2 → 5.9 | 7.4 → 6.2 | 16.0 → 13.6 |
| zh | 15.4 → 1.8 | 29.0 → 5.4 | 24.7 → 4.9 | 44.4 → 12.3 |
| ko | 5.9 → 1.8 | 41.6 → 5.0 | 11.1 → 3.7 | 54.3 → 11.1 |
| es | 14.5 → 3.6 | 42.5 → 6.8 | 16.0 → 8.6 | 58.0 → 12.3 |
| fr | 6.8 → 2.7 | 37.1 → 2.7 | 7.4 → 6.2 | 53.1 → 7.4 |
| de | 4.5 → 2.7 | 43.0 → 6.3 | 6.2 → 4.9 | 61.7 → 14.8 |
| pt | 4.5 → 1.4 | 48.0 → 3.6 | 7.4 → 3.7 | 65.4 → 9.9 |
| ru | 8.6 → 2.3 | 48.0 → 4.1 | 12.3 → 3.7 | 65.4 → 11.1 |
| it | 11.3 → 0.9 | 45.2 → 4.1 | 12.3 → 2.5 | 59.3 → 11.1 |
| ar | 2.3 → 1.8 | 48.0 → 0.9 | 4.9 → 4.9 | 65.4 → 2.5 |
| hi | 2.3 → 2.3 | 47.5 → 4.1 | 4.9 → 4.9 | 64.2 → 9.9 |
| id | 29.0 → 3.2 | 47.5 → 2.3 | 38.3 → 7.4 | 64.2 → 6.2 |
| vi | 28.1 → 1.8 | 47.1 → 2.3 | 38.3 → 3.7 | 63.0 → 6.2 |
| th | 5.9 → 5.0 | 12.2 → 8.1 | 12.3 → 9.9 | 21.0 → 14.8 |
| tr | 13.1 → 1.4 | 47.5 → 3.2 | 17.3 → 3.7 | 64.2 → 8.6 |
| pl | 2.3 → 1.8 | 47.5 → 4.1 | 4.9 → 4.9 | 64.2 → 9.9 |
| nl | 4.1 → 3.2 | 26.2 → 5.0 | 9.9 → 7.4 | 40.7 → 12.3 |
| uk | 1.8 → 1.8 | 48.0 → 5.0 | 2.5 → 2.5 | 65.4 → 12.3 |

所有設定、所有語言的完整表格與每語言的候選片語在 `results/summary.md`（重跑「彙整」即產生）。

### 片語表摘要（`lexicon.json`）

括號內為「出現的片段數／其中通過目前過濾的片段數／其中 VAD 通過的片段數」（跨 3 個模型、2 種模式；
片段總數 221、VAD 通過 81）。「目前過濾」已含既有清單，所以既有項目（例如 ご視聴ありがとうございました、
you）漏網為 0。不分語言的 `*` 表沿用既有的 4 個署名（Amara.org 中英文、Teksting av Nicolai Winther）。

| 語言 | hard 數 | 主要 hard 片語 | soft 數 | 主要 soft 片語 |
|---|---|---|---|---|
| ja | 7 | ご視聴ありがとうございました（199/0/68）；チャンネル登録よろしくお願いします（24/0/10）；チャンネル登録をお願いいたします。（4/3/3） | 7 | ん（179/8/64）；んんん（43/2/17）；んんんん（27/3/9） |
| en | 3 | Thanks for watching!（15/0/5） | 6 | you（207/0/69）；Thank you.（120/0/42）；Bye.（27/10/12） |
| zh | 23 | 字幕by索兰娅（100/40/37）；优优独播剧场——YoYo Television Series Exclusive（66/26/27）；字幕志愿者 杨茜茜（47/21/19） | 6 | 嗯嗯（20/4/11）；嗯嗯嗯（16/2/7）；啊啊啊（7/1/4） |
| ko | 6 | 다음 영상에서 만나요.（157/67/53）；시청해주셔서 감사합니다.（56/34/8）；구독과 좋아요 부탁드려요!（10/4/5） | 6 | 감사합니다.（123/50/35）；아멘（17/2/9）；아 아 아 아 아（11/3/6） |
| es | 5 | Gracias por ver el video.（104/58/34）；Subtítulos por la comunidad de Amara.org（42/15/11）；¡Suscríbete al canal!（30/21/13） | 6 | Gracias.（159/77/45）；y（158/2/55）；¡Suscríbete!（60/33/16） |
| fr | 10 | Sous-titrage ST' 501（140/78/54）；Sous-titres réalisés par l'Amara.org（79/26/30）；Sous-titrage FR ?（49/20/14） | 4 | Merci.（45/10/10）；C'est parti !（28/18/7）；Merci à tous.（15/2/7） |
| de | 8 | Untertitelung des ZDF, 2020（55/30/24）；Copyright WDR 2020（48/5/16）；Untertitelung. BR 2018（33/21/14） | 7 | Vielen Dank.（142/71/38）；Bis zum nächsten Mal.（57/33/20）；Musik（34/1/16） |
| pt | 2 | Legenda por Sônia Ruberti（11/7/5）；Legenda Adriana Zanotto（8/4/4） | 10 | E aí（202/118/67）；E aí E aí（182/25/63）；O que é isso?（108/3/36） |
| ru | 11 | Субтитры сделал DimaTorzok（87/57/43）；Субтитры создавал DimaTorzok（63/34/29）；Редактор субтитров Н.Закомолдина Корректор А.Егорова（55/14/18） | 3 | Продолжение следует...（155/84/40）；Спасибо.（9/4/3）；и（4/2/4） |
| it | 3 | Sottotitoli e revisione a cura di QTSS（55/27/19）；Sottotitoli a cura di QTSS.（55/6/8）；Grazie per la visione!（9/6/2） | 4 | Grazie.（168/79/44）；Grazie a tutti.（145/82/58）；Buon appetito!（23/11/7） |
| ar | 5 | اشتركوا في القناة（146/88/60）；شكرا للمشاهدة（129/68/43）；ترجمة نانسي قنقر（107/52/22） | 4 | مرحباً（117/3/35）；شكرا（55/18/13）；موسيقى（49/30/19） |
| hi | 0 | — | 15 | झाल（164/63/54）；करते हैं（90/45/20）；आप आप आप आप आप आप（89/22/18） |
| id | 4 | Terima kasih kerana menonton!（168/57/53）；Terima kasih sudah menonton!（112/57/43）；Terima kasih telah menonton!（94/61/45） | 3 | Terima kasih.（166/84/53）；selamat menikmati（62/38/18）；Sampai jumpa!（7/3/1） |
| vi | 6 | Hãy subscribe cho kênh La La School Để không bỏ lỡ những video hấp dẫn（208/120/70）；Hãy subscribe cho kênh Ghiền Mì Gõ …（185/92/67）；Cảm ơn các bạn đã theo dõi và hẹn gặp lại.（117/55/31） | 0 | — |
| th | 0 | — | 18 | สวัสดีครับทุกคน（54/26/11）；โปรดติดตามตอนต่อไป（53/29/12）；กลับกลับ（36/1/5） |
| tr | 3 | Altyazı M.K.（203/116/71）；Abone olmayı unutmayın.（92/40/29）；İzlediğiniz için teşekkür ederim.（24/20/5） | 3 | abone ol（15/3/9）；abone ol abone ol（11/5/4）；Fırt fırt fırt（5/4/1） |
| pl | 6 | Dzięki za oglądanie!（99/67/41）；Dziękuje za oglądanie!（100/45/36）；Napisy stworzone przez społeczność Amara.org（12/7/7） | 10 | KONIEC!（139/76/56）；Cześć, cześć!（45/2/13）；Dziękuję.（41/15/6） |
| nl | 2 | TV Gelderland 2021（74/31/28）；ZANG EN MUZIEK（9/5/0） | 3 | MUZIEK（43/32/22）；Dank u wel.（4/3/1）；Vroom! Vroom! Vroom!（3/2/3） |
| uk | 3 | Дякую за перегляд!（185/113/64）；Редактор субтитров О.Голубки Корректор В.Сухи（41/5/10）；…О.Голубки Корректор В.Егорова（7/2/2） | 2 | Дякую!（163/90/49）；Наступного року!（3/2/1） |

### 其他觀察

1. **large-v3-turbo／large-v3 幾乎每段非語音都有輸出**（turbo final 非空 96%），而且每個語言都有一兩句
   「招牌」幻覺（tr「Altyazı M.K.」出現在 203/221 段、vi 兩句訂閱宣傳各約 200 段）。small 對這些片段的
   no_speech 多半偏高，所以 soft 片語（no_speech > 0.3）也擋得住 turbo 的輸出。
2. **small 的輸出比較分散**：較多長句與重複迴圈，多數被 no_speech／重複判定擋下；比較會漏網的是
   各種字幕署名（ru「Редактор субтитров …」、de「Untertitel im Auftrag des ZDF …」，人名與年份會變）。
3. 強制烏克蘭語時 small 會輸出**俄語**的字幕署名（「Редактор субтитров О.Голубки …」），支持 hard 片語
   不看語言標籤的做法。
4. **數位靜音、−60／−45 dBFS 白噪音在三個模型都 0 幻覺**（被 no_speech 擋下）；large-v3 對 −30 dBFS 白噪音
   會輸出各語言的套話，但都在片語表內。
5. 擬聲詞類（「E aí」「ん」「y」「झाल」「กลับกลับ」）在各語言都很多，只能當 soft；真實語音中也會出現。

## 結論

1. **依語言的片語表很有效**：app 推薦組合的完整路徑（turbo final）在 VAD 通過的非語音上，幻覺率由
   52.8% 降到 10.3%（交叉驗證 11.0%）；即時路徑（small fast）12.8% → 5.1%。hard 與 soft 各貢獻約一半。
2. **VAD 是第一道、也是最重要的防線**：MUSAN 非語音只有 2.0% 通過 Silero VAD，合成噪音與音效 0%。
3. **剩下的漏網是長尾**：擬聲詞與 MUSAN 噪音中疑似真人的聲音，片語表無法也不應該處理；要再降需要其他訊號
   （例如即時與完整兩個模型的輸出是否一致、語言偵測機率）。
4. 字幕署名的人名、年份、電視台每次都不同（「Untertitel im Auftrag des ZDF, 2017／für funk, 2017」），整句比對
   只能收常見的幾個；若要更完整，可考慮以「字幕／Subtitles／Sous-titres／Untertitel／Субтитры／Napisy／Legenda／
   Altyazı」開頭的前綴規則（需另評估誤濾風險，本實驗未實作）。

## 限制

- **送 ASR 的是整段片段，不是 VAD 切出的部分**；VAD 判定是每段從全新狀態開始，與 app 連續串流的狀態不同。
- MUSAN noise 的部分檔案含有遠處人聲或廣播（MUSAN README：「通常沒有可辨識的人聲」），少數「幻覺」可能是
  真的聽到的字（例如多個語言一致輸出「Stop button.」）。MUSAN music 只用 ANNOTATIONS 標為無人聲的檔案，但
  標註以整首為單位。
- 沒有遊戲實錄音效與 VTuber 直播 BGM；「burst」只是粗略近似，而且 0 段通過 VAD。
- large-v3 只跑了約 60% 的片段（隨機子樣本），base 沒跑；只有 CUDA float16，沒有 CPU int8（量化可能改變輸出）。
- **假陽性只做了有限檢查**：片語表與 `experiments/asr_benchmark` 的 190 個日文語音辨識結果、
  `experiments/translate_benchmark/lines.json` 的 196 句都沒有整句相同。其他語言沒有真實語音素材可驗證；
  soft 片語在真實語音上只要 small 的 no_speech ≤ 0.3 就不會被濾，但短句的 no_speech 本來就偏高
  （`experiments/asr_benchmark` 的日文語音最高 0.43）。
- `normalize_for_match()` 只保留 Unicode 字母（L）與數字（N）類別，會**去掉印地語、泰語等的組合記號**
  （Mn／Mc，例如 ा、ो、ั、่），所以「झाल」比對時是「झल」、「कर दो」與「कर दे」相同。這兩個語言的項目
  都放 soft 以降低風險；是否讓正規化保留 M 類別由 `kikitori.asr.filtering` 決定。
- 中文片語同時列了簡體與繁體（繁體為人工轉寫，未實測），Whisper 實際輸出以簡體為主。
- 片語表是人工依上述原則整理，未經母語者審閱。
