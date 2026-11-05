# VTuber 直播 ASR 評測：日文模型、BGM 強健性與硬體等級推薦

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05；kikitori 分支 `feat/vtuber-models`（Milestone 16 的 ASR 部分）。
> 方法依 `reports/VTuber 直播模型選型調查.md` 的「評測」一節：資料分層、MUSAN 混音協定、kotoba 的 CER 正規化、blockwise bootstrap。

## 目的

替「VTuber 直播」設定檔決定日文 ASR 的推薦組合：即時路徑（`FAST_DECODE`，greedy）與完整路徑（`FINAL_DECODE`，beam 5）各用哪個模型，分 8–12 GB GPU 與 CPU 兩個等級；kotoba-whisper-v2.0 要不要提供、要不要當日文預設；以及 BGM 音量對每個模型的傷害有多大。

## 結論（給 VTuber 直播設定檔）

數字是過濾後 CER（%），[] 為以影片為 block 的 95% bootstrap CI；P 為 bootstrap 中「A 優於 B」的比例。完整表格在下方「結果」。

| 硬體 | 即時路徑（greedy） | 完整路徑（beam 5） | 依據 |
|---|---|---|---|
| NVIDIA，VRAM 夠放兩個大模型（≥ 12 GB 且翻譯 LLM 不在同一張卡，或 LLM ≤ 4 GB） | **large-v3-turbo** CUDA float16 | **large-v3** CUDA float16 | 約 2.3＋4.2 GB |
| NVIDIA 8–12 GB，與本機翻譯 LLM 共用 | **large-v3-turbo** CUDA float16 | **large-v3** CUDA int8_float16 | 約 2.3＋2.1 GB；int8_float16 與 float16 品質相同（見第 2 點）；large-v3 int8_float16 final 單獨執行約 0.42 s（asr_benchmark），本評測在 GPU 滿載時量到中位 1.2 s |
| CPU（RAM ≥ 16 GB、8 核） | **base** CPU int8 | **small** CPU int8 | 與現行推薦相同 |

1. **即時路徑改用 large-v3-turbo，不用 small**（現行 ≥ 8 GB 等級推薦的是 small）。YODAS 乾淨：turbo 24.7 [19.0, 31.7] vs small 30.3 [24.5, 36.8]，Δ +5.6 [+2.9, +8.2]（small 較好的機率 0.000）；10 dB BGM 下差距仍有 +4.8 [+0.7, +8.3]。turbo 與 large-v3 的 greedy 在乾淨語音上差異不顯著（Δ +1.3 [−0.8, +3.5]，P(turbo 較好)＝0.12），但延遲中位數 161 ms vs 402 ms（共用 GPU 下量的）。
2. **完整路徑在有 BGM 時 large-v3 明顯較穩，乾淨時與 turbo 打平**。乾淨：turbo final vs large-v3 final Δ +0.7 [−1.0, +2.5]（不顯著）；music 10 dB：+4.1 [+0.0, +8.2]（P(turbo 較好)＝0.025）；music 5 dB：+5.3 [+0.8, +10.1]（0.011）。large-v3 final 的 BGM 退化是所有設定中最小的（10 dB +0.4 [−1.3, +2.2]、5 dB +1.8 [−0.1, +4.0]、0 dB +6.7 [+3.0, +11.1]），turbo final 則是 +3.6／+6.2／+8.6。large-v3 final 改用 int8_float16（VRAM 約減半）品質沒有實質差異：YODAS 乾淨 Δ −0.4 [−1.0, +0.3]、music 10 dB +0.9 [−0.0, +2.0]、FLEURS +0.1、ReazonSpeech +0.3（int8−float16）。直播幾乎都有 BGM，所以完整路徑推薦 large-v3；VRAM 放不下時退回 turbo（乾淨語音品質不輸）。turbo 的 no_speech 恆為 0，app 會改用即時模型的 no_speech 過濾；以 small fast 的 no_speech 模擬現行組合（表中「＋small-fast-no_speech」列），turbo final 的 YODAS 過濾後 CER 為 25.0 [19.1, 32.2]、誤濾 18/1006、非語音幻覺 0/50。large-v3 的 no_speech 可用，不需要替換。
3. **kotoba-whisper-v2.0 不提供、也不當日文預設**：
   - YODAS（YouTube 口語）上**比 large-v3 差**：fast Δ +3.9 [+1.4, +6.5]（P(kotoba 較好)＝0.001）、final Δ +4.7 [+2.3, +7.2]（0.000）；也比 turbo 差（fast +2.7、final +4.0）。FLEURS 上同樣較差（final +2.2 [+1.4, +3.3]）。
   - 只在它的訓練領域 ReazonSpeech（電視）贏 large-v3（final −2.2 [−3.9, −0.8]），但同一資料上 **turbo final 更好**（10.9 vs 12.2，Δ kotoba−turbo +1.3 [−0.2, +3.0]）。
   - **非語音幻覺嚴重**：純 BGM／音效片段中有 46/50（fast）、50/50（final）輸出文字且通過 app 的過濾，絕大多數是「ごめん」（no_speech 約 0.08–0.2、avg_logprob 約 −0.4，現行規則擋不到）。即使照 app 的規則改用 small fast 的 no_speech 過濾，仍有 17/50 通過。VTuber 直播的空檔幾乎都是 BGM，這會變成持續的假字幕。
   - 報告裡「在自建 VTuber 集上 95% CI 優於 large-v3 才升為預設」的條件**沒有成立，而且方向相反**。
4. **BGM 退化曲線**（YODAS 混音子集 200 段，同一批段落成對比較；Δ 為百分點）：20／15 dB 幾乎無損（大模型 ≤ +2）；10 dB：large-v3 final +0.4、turbo fast +1.6、small +2.4–2.6、medium/kotoba +3–4；5 dB：+1.8（large-v3 final）到 +6.2（turbo final）；0 dB（BGM 與語音一樣大，相當於歌回或音量沒調好）：+6.0 到 +14.4，小模型（small、medium）掉最多。前景音效（遊戲音效代理）5 dB 的傷害與 music 5 dB 相近（+2.9 到 +7.9）。BGM 也讓過濾多丟句子：turbo fast 被丟的語音從 8/200（乾淨）升到 19/200（0 dB）。
5. **CPU 維持 base（即時）＋ small（完整）**。CPU 子集上 small final 28.8 [22.2, 36.3]、base final 34.7 [27.9, 42.8]、base fast 37.8 [30.7, 45.8]；small 單句 1.6 s（與 GPU 評測同時執行，偏高），跟不上 700 ms 的即時節奏；base 0.66 s 勉強（asr_benchmark 單獨執行為 0.48 s）。品質明顯低於 GPU（同一子集 turbo fast 23.4）；CPU 上要日文品質只能等 sherpa-onnx 後端（報告的推薦，未在此測）。
6. **校準通過**：ReazonSpeech large-v3 final 14.5 [12.2, 17.0]（kotoba 公開 14.9）、kotoba final 12.2 [10.2, 14.3]（公開 11.6）；FLEURS large-v3 final 4.4 [3.6, 5.1]（OpenAI 公開 4.9）、turbo 4.8 [4.0, 5.6]（公開 4.7）。正規化與管線與公開數字對得上。
7. **給過濾規則的發現**（`kikitori.asr.filtering` 由其他人重構中，僅供參考）：
   - **medium 的 no_speech 在日文語音上常落在 0.82–0.90**，被 `hard_no_speech_threshold=0.8` 丟掉 86–99／1006 段乾淨語音（約 9–10%），過濾後 CER 從 24.5 變 32.8。VTuber 設定檔不要用 medium；若要用，門檻需依模型調整。
   - 通過過濾的非語音幻覺（50 段純 BGM／音效）：large-v3 fast「チャンネル登録をお願いいたします。」×2、「次の動画でお会いしましょう。」；turbo final「おやすみなさい」；kotoba final「ごめん」×44、「ごめんね」「ごっこ」；CPU base final「JR東日本E233系電車」×2——前兩者可列入黑名單候選；「ごめん」是常用語，只能當 soft 片語（配合 no_speech 門檻），但 kotoba 的 no_speech 偏低，soft 規則也擋不到。
   - 真實語音被辨識成「ご視聴ありがとうございました」（參考文字沒有這句）的段數（只算乾淨條件的 1006 段語音）：turbo fast 10、turbo final 7、large-v3 final 5、large-v3 fast 2、small／medium 各 1、kotoba 0。多半出現在合成語音旁白的影片；黑名單會擋下，但那句字幕就遺失了。

## 怎麼重跑

```text
uv run python -I experiments/vtuber_eval/menu.py
```

選單依序：

1. **準備資料**：以**隔離環境**（`uv run --isolated --no-project --with pyarrow==26.0.0 --with soundfile==0.14.0 --with numpy==2.5.4`，不改專案依賴）執行 `prepare.py`：下載固定 revision 的語料、驗證 SHA-256、切成 16 kHz 單聲道 PCM16 WAV，寫 `data/candidates.json`。隔離環境實際裝的套件：cffi 2.1.1、numpy 2.5.4、pyarrow 26.0.0、pycparser 3.12、soundfile 0.14.0、typing_extensions 4.16.0。
2. **建立評測集**（`build_sets.py`）：YODAS 影片層級語言檢查（結果快取在 `data/lid.json`）、抽樣、MUSAN 混音，寫 `data/manifest.json`。MUSAN 沿用 `experiments/silero_reset/data/musan/`（沒有的話先在 silero_reset 的選單下載）。
3. **執行評測**（`evaluate.py`）：選計畫（GPU 全集 float16／int8_float16、CPU 子集）與設定；每個設定在獨立子行程執行，逐段附加到 `results/<label>.jsonl`，中斷後重跑會跳過已完成的段落。CUDA 需要 cuBLAS 12 資料夾（預設偵測 `%LOCALAPPDATA%\kikitori\gpu-runtime\cublas-*`）。
4. **計分與統計**（`analyze.py`）：以**目前**的 `rejection_reason()` 重新判定過濾，算 CER、bootstrap、成對比較、延遲，寫 `results/summary.md`、`summary.json`。

正規化的單元測試：`uv run python -m unittest discover -s experiments/vtuber_eval -p "test_*.py"`。

`data/`、`results/` 由本資料夾的 `.gitignore` 排除：裡面有版權音訊與字幕，只放本機。

## 環境

- CPU：AMD Ryzen 7 5700X（8C/16T），RAM 64 GB；GPU：NVIDIA RTX 5070 12 GB（WDDM，驅動 621.01）；Windows 11 Pro。
- Python 3.12.15、ctranslate2 4.8.5、numpy 2.5.4、onnxruntime 1.31.1（Silero VAD v6.2.3）；uv 0.13.4。
- cuBLAS：kikitori 下載的 GPU 執行環境 `cublas-12.8.4.1`。
- 模型：kikitori registry 的 small、medium、large-v3-turbo、large-v3（revision 見 `kikitori.asr.registry`），與 `experiments/asr_benchmark/benchmark.py` 的對照規格 kotoba-whisper-v2.0-faster（revision `f44edd35…`）。
- **GPU 與其他程式共用**：評測全程有另一個實驗在跑 Ollama（qwen3.5:9b，約 5.6 GB VRAM）與 kikitori-engine，整卡已用 7–11 GB、使用率 35–80%。各設定的 GPU 用量與使用率快照記在 `results/*.meta.json`（每 200 段一次），摘要見延遲表。所以**延遲是「共用 GPU」下的數字，絕對值比 `experiments/asr_benchmark` 單獨量的慢，只適合模型之間相對比較**；段落順序以固定 seed 打亂，讓各條件均勻承受干擾。CPU 子集與 GPU 評測同時執行（CPU 延遲也偏高）。

## 資料

| 組別 | 來源 | 授權 | 使用量 | block（bootstrap 單位） |
|---|---|---|---|---|
| **YODAS**（主體） | [espnet/yodas](https://huggingface.co/datasets/espnet/yodas) `ja000`（人工上傳字幕子集）第 0 片，revision `0cad6a0e` | CC BY 3.0（來源皆為 CC 授權的 YouTube 影片） | 500 段、72 支影片、37.9 分鐘（中位 4.5 s） | 影片 |
| FLEURS（乾淨朗讀校準） | [google/fleurs](https://huggingface.co/datasets/google/fleurs) `ja_jp` test，revision `70bb2e84` | CC BY 4.0 | 200 段（≤ 25 s）、165 個句子、42.2 分鐘 | 句子 id（同句多位朗讀者） |
| ReazonSpeech test（電視語音校準） | [japanese-asr/ja_asr.reazonspeech_test](https://huggingface.co/datasets/japanese-asr/ja_asr.reazonspeech_test)（kotoba 的評測鏡像，未設 gated）第 0 片，revision `dd08bfb9` | 原語料條款：只限日本著作權法第 30 條之 4 的資訊解析；本評測屬於解析用途，只在本機、不散布 | 300 段、31.4 分鐘 | 單句（資料沒有節目 id） |
| 動漫 6 段 | `experiments/pipecat_vad/data/`（沿用 asr_benchmark 的切法與參考文字） | 版權素材，只在本機 | 6 段 | 片段 |
| 非語音 | MUSAN 無人聲音樂 30 段、前景音效 20 段，各 6 s | CC BY 4.0（見 `experiments/silero_reset/data/musan/ATTRIBUTION.md`） | 50 段 | 片段 |

沒用的：Common Voice 8（朗讀，FLEURS 已涵蓋，且是 48 kHz MP3 需重新取樣）、JSUT（授權限非商用研究）、任何需要登入的資料集。

### YODAS 的過濾與抽樣（與模型無關的部分在前）

YODAS 的「manual」只代表字幕由上傳者提供，不保證逐字、也不保證對齊（[dataset card](https://huggingface.co/datasets/espnet/yodas)）。第 0 片共 9,157 段、149 支影片，依序：

1. **字幕規則**（`prepare.py` 的 `yodas_reject_reason()`）：長度 1–10 s（543 段不符）；含 ♪／音楽／BGM／網址（85）；含任何括號註記，例如［拍手］（271）；正規化後少於 3 字（233）；含諺文；沒有假名（279）；日文字元（假名、漢字、數字）比例 < 0.8（243）；字速（正規化後字數 ÷ 秒）不在 2–14 字／s（422，多半是字幕時間對不上）。剩 109 支影片，每支最多隨機留 40 段候選，共 3,004 段。
2. **影片層級語言檢查**（`build_sets.py`）：每支影片隨機 6 段，用 Whisper medium **不指定語言**辨識；6 段中至少 5 段判為 `ja` 才保留整支影片（不足 6 段的影片要全部是 `ja`）。72／109 支保留；排除的多半是英語、韓語影片配日文字幕（翻譯字幕，不是逐字稿）。只在影片層級做，避免「挑模型辨識得好的句子」造成偏差。
3. **抽樣**：每支影片取相同上限 c（讓總數 ≤ 500 的最大 c），再從還有剩的影片各補 1 段到 500 段——影片越多 bootstrap 越有意義，也不讓長片主導。每支影片 1–8 段。
4. **混音子集**：同樣方式從 500 段中取 200 段（72 支影片）做 BGM／音效條件。**CPU 子集**：YODAS 150 段＋FLEURS 50 段＋動漫 6 段＋非語音 50 段。

seed 固定為 20261105，所有抽樣可重現。

### 混音協定

- **BGM**：MUSAN music 中 ANNOTATIONS 標為無人聲（第 3 欄 `N`）的**流行類**（fma、jamendo、rfm；共 269 首，以 electronica、rock、jazz 為主；rfm 多為 Kevin MacLeod 的免版稅配樂，正是直播常用的 BGM 類型），不用古典類。SNR 20／15／10／5／0 dB。
- **遊戲音效代理**：MUSAN noise 中**前景**音效（排除 free-sound 的「Background noises」清單 37 個環境音，剩 893 個），SNR 10／5 dB，連續播放（檔案太短就循環）。
- **SNR 基準**：語音功率只算 Silero VAD v6（kikitori.vad）判為語音（機率 ≥ 0.5）的 32 ms 視窗；背景功率算整段。這樣停頓比例不同的句子才能比較。
- 每段固定一首 BGM 與 offset，**五個 SNR 共用同一段 BGM**，SNR 曲線因此是成對比較；seed 由「段落 id＋種類」決定。混音後若削波就整體縮小（SNR 不變）。噪音檔、offset、是否循環、增益、縮放、語音活躍段 RMS 都寫在 `manifest.json` 的 `mix`。
- 非語音片段的音量 = YODAS 語音活躍段 RMS 中位數 − 10 dB（相當於 10 dB SNR 的 BGM 音量）。
- 報告建議的多 seed 平均沒有做：改以「每段各自隨機挑一首」讓 200 段分散到約 200 首不同 BGM，用段落層級的變異取代 seed 層級的平均。

## 方法

- **解碼**：一律用 app 的 `FAST_DECODE`（greedy、`max_new_tokens=128`）或 `FINAL_DECODE`（beam 5、溫度 0／0.2／0.4 fallback），語言固定 `ja`。kotoba 官方建議的 `chunk_length=15`、`condition_on_previous_text=False` 對「單句 ≤ 10 s、每句獨立辨識」的 kikitori 用法沒有作用（沒有跨視窗前文），所以所有模型參數相同。
- **過濾**：app 目前的 `kikitori.asr.rejection_reason()`（`FilterConfig()` 預設）。完整模型的 no_speech 不可靠時（large-v3-turbo、kotoba），app 的 pipeline 會改用即時模型對同一句的 no_speech（`pipeline.py` 的 `_for_filter`）；`analyze.py` 以 small fast 的逐句 no_speech 另列「＋small-fast-no_speech」設定模擬，模型原本的列則用自己的 no_speech。計分時重新判定，被丟掉的句子當作輸出空字串（使用者看不到字幕＝整句刪除）。**主要指標是過濾後 CER**，未過濾 CER 另列。
- **CER 正規化**（`textnorm.py`，版本 `whisper-basic-nospace-v1`）：逐字重現 OpenAI Whisper 的 `BasicTextNormalizer()`——小寫、刪 ASCII `[]`／`<>`／`()` 括號內文字、NFKC、把 Unicode M／S／P 類別換成空白——再去掉所有空白；與 kotoba README 評測程式 `normalizer(text).replace(" ", "")` 相同。注意 Whisper 的括號規則在 NFKC 之前、只認 ASCII 括號，所以全形「（笑）」只會刪掉括號、留下「笑」；照原樣重現不修正。CER = 總編輯距離 ÷ 參考總字數（corpus-level）。
- **去填充詞變體**（版本 `ja-fillers-v1`）：BasicTextNormalizer 後、去空白前，逐「片段」（原本被標點或空白隔開的部分）套用：(1) 整個片段都是填充詞就刪——えー／えっと／えーと／ええと／あー／あの(ー)／その(ー)／うーん／んー／まあ／なんか／ｗ／草／笑；「あの」「その」只在獨立成段時刪，黏著的「あの人」是指示詞不刪。(2) 片段開頭的拉長音填充詞刪掉（えー、えーっと、あのー、うーん、んー、あー）。(3) 非 ASCII 片段結尾的 w 串（ｗｗｗ）刪掉。參考與辨識結果都套用。單元測試在 `test_textnorm.py`。
- **統計**：以 block（YODAS＝影片）做 1,000 次 blockwise bootstrap（seed 20261105），報告 CER 的 2.5／97.5 百分位；成對比較在同一組重抽樣上算 ΔCER＝A−B 的 95% CI 與 probability of improvement（重抽樣中 A 的 CER 較低的比例；Bisani & Ney 2004）。
- **敏感度分析**：「去疑似錯位」欄排除所有 GPU final 設定 CER 中位數 > 50% 的 YODAS 段落（字幕可能對不上音訊）。這條規則依賴模型輸出，只當敏感度檢查，不是主要數字。
- **非語音幻覺**：Calm-Whisper 的片段級二元定義——非語音片段過濾後仍輸出任何文字就算一次。
- **延遲**：單段 `transcribe()` 牆鐘時間（模型已暖機），中位數與 P90；RTF＝總耗時 ÷ 總音訊長度。

## 結果

以下由 `analyze.py` 產生（`results/summary.md` 原樣貼上；`＋small-fast-no_speech` 的說明見「方法」）。延遲表的 GPU 欄是評測期間整卡的已用記憶體與使用率（含其他程式），不是單一模型的用量。

正規化：whisper-basic-nospace-v1；去填充詞：ja-fillers-v1；bootstrap 1000 次（seed 20261105）。CER 為百分比，[] 為 95% CI。

YODAS 疑似字幕錯位（所有 GPU final 設定 CER 中位數 > 0.5）：85 段，只在「去疑似錯位」欄排除。

### 主要結果（GPU，全集）

| 設定 | YODAS 過濾後 | YODAS 未過濾 | YODAS 去填充詞 | YODAS 去疑似錯位 | FLEURS | ReazonSpeech | 動漫 6 段 | 語音被誤濾 |
|---|---|---|---|---|---|---|---|---|
| kotoba-whisper-v2.0_cuda_float16_fast | 27.4 [22.3, 33.6] | 27.0 [22.0, 33.1] | 27.4 [22.4, 33.6] | 18.4 [15.6, 21.1] | 9.8 [7.7, 12.3] | 14.0 [11.8, 16.4] | 20.2 [7.7, 33.3] | 4/1006 |
| kotoba-whisper-v2.0_cuda_float16_final | 28.0 [22.8, 34.1] | 27.6 [22.6, 33.7] | 28.0 [22.8, 34.1] | 19.0 [16.1, 21.9] | 6.6 [5.4, 7.9] | 12.2 [10.2, 14.3] | 20.2 [7.7, 33.3] | 3/1006 |
| large-v3-turbo_cuda_float16_fast | 24.7 [19.0, 31.7] | 25.6 [19.6, 32.8] | 24.7 [18.9, 31.7] | 14.6 [11.7, 17.7] | 8.5 [6.5, 10.9] | 14.3 [11.8, 17.1] | 23.8 [11.5, 37.2] | 17/1006 |
| large-v3-turbo_cuda_float16_final | 23.9 [18.4, 31.0] | 23.9 [18.3, 30.8] | 24.0 [18.4, 31.0] | 13.6 [11.2, 16.1] | 4.8 [4.0, 5.6] | 10.9 [9.1, 12.8] | 19.0 [6.3, 33.0] | 8/1006 |
| large-v3_cuda_float16_fast | 23.4 [17.5, 30.3] | 23.2 [17.4, 30.3] | 23.4 [17.5, 30.2] | 13.1 [10.8, 15.7] | 8.3 [6.4, 10.7] | 16.5 [14.0, 19.1] | 27.4 [11.0, 46.0] | 20/1006 |
| large-v3_cuda_float16_final | 23.2 [17.5, 29.9] | 22.7 [16.9, 29.4] | 23.2 [17.5, 29.9] | 12.9 [10.6, 15.5] | 4.4 [3.6, 5.1] | 14.5 [12.2, 17.0] | 23.8 [9.8, 38.0] | 19/1006 |
| large-v3_cuda_int8_float16_final | 22.8 [17.0, 29.5] | 22.5 [16.8, 29.4] | 22.8 [17.0, 29.5] | 12.5 [10.3, 15.0] | 4.5 [3.7, 5.3] | 14.8 [12.4, 17.3] | 23.8 [9.8, 38.0] | 19/1006 |
| medium_cuda_float16_fast | 34.5 [27.9, 41.2] | 27.3 [21.7, 33.6] | 34.4 [27.8, 41.2] | 25.7 [20.9, 30.7] | 11.1 [9.1, 13.6] | 27.3 [23.8, 31.1] | 28.6 [11.0, 47.5] | 99/1006 |
| medium_cuda_float16_final | 32.8 [26.4, 39.8] | 24.5 [19.0, 31.1] | 32.7 [26.3, 39.8] | 23.8 [19.0, 28.8] | 7.1 [6.0, 8.2] | 25.1 [21.6, 28.8] | 20.2 [7.7, 33.0] | 86/1006 |
| small_cuda_float16_fast | 30.3 [24.5, 36.8] | 29.9 [24.3, 36.6] | 30.3 [24.5, 36.8] | 20.7 [17.6, 23.7] | 16.2 [13.8, 18.8] | 29.5 [26.3, 32.8] | 39.3 [23.4, 56.8] | 31/1006 |
| small_cuda_float16_final | 29.3 [23.7, 35.7] | 27.9 [22.6, 34.1] | 29.2 [23.7, 35.7] | 19.6 [16.5, 22.6] | 11.8 [10.4, 13.4] | 25.7 [22.9, 28.9] | 31.0 [21.1, 43.0] | 22/1006 |
| kotoba-whisper-v2.0_cuda_float16_final＋small-fast-no_speech | 29.1 [23.5, 35.3] | 27.6 [22.6, 33.7] | 29.1 [23.5, 35.4] | 20.2 [16.9, 23.7] | 6.6 [5.4, 7.9] | 12.4 [10.3, 14.4] | 20.2 [7.7, 33.3] | 13/1006 |
| large-v3-turbo_cuda_float16_final＋small-fast-no_speech | 25.0 [19.1, 32.2] | 23.9 [18.3, 30.8] | 25.0 [19.2, 32.2] | 14.8 [12.2, 17.4] | 4.8 [4.0, 5.6] | 11.0 [9.1, 12.9] | 19.0 [6.3, 33.0] | 18/1006 |

### BGM／音效強健性（YODAS 混音子集，同一批段落；CER 過濾後）

| 設定 | clean | music20 | music15 | music10 | music5 | music0 | noise10 | noise5 | Δ music10−clean | Δ music5−clean | Δ music0−clean | Δ noise5−clean |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| kotoba-whisper-v2.0_cuda_float16_fast | 27.4 [21.9, 33.9] | 28.3 [22.7, 34.4] | 29.2 [23.6, 35.4] | 31.1 [25.0, 37.9] | 32.0 [26.0, 38.5] | 36.2 [30.0, 42.5] | 30.2 [24.4, 36.8] | 32.1 [26.2, 38.6] | +3.7 [+1.4, +6.4] | +4.6 [+2.1, +7.6] | +8.8 [+5.9, +12.1] | +4.7 [+2.3, +7.3] |
| kotoba-whisper-v2.0_cuda_float16_final | 28.5 [22.8, 35.0] | 29.0 [23.3, 35.6] | 30.3 [24.4, 37.0] | 31.6 [25.2, 38.7] | 31.9 [26.0, 38.3] | 34.5 [28.7, 40.3] | 30.0 [24.0, 36.6] | 31.5 [25.7, 38.1] | +3.1 [+1.4, +5.1] | +3.4 [+1.4, +5.6] | +6.0 [+3.4, +8.9] | +3.0 [+1.1, +5.1] |
| large-v3-turbo_cuda_float16_fast | 26.9 [20.8, 34.2] | 28.2 [21.9, 35.7] | 27.4 [21.1, 34.9] | 28.5 [22.1, 36.4] | 30.1 [23.4, 37.5] | 33.9 [27.3, 41.1] | 28.8 [22.5, 35.8] | 30.9 [24.2, 38.3] | +1.6 [+0.2, +3.3] | +3.1 [+0.9, +5.5] | +7.0 [+3.5, +11.0] | +4.0 [+1.2, +6.8] |
| large-v3-turbo_cuda_float16_final | 26.4 [20.2, 34.0] | 28.4 [21.8, 36.3] | 29.2 [22.4, 37.1] | 30.0 [23.0, 37.7] | 32.6 [24.8, 40.9] | 35.0 [27.7, 43.1] | 28.2 [21.7, 35.6] | 30.8 [24.0, 38.5] | +3.6 [+0.8, +6.5] | +6.2 [+2.5, +10.2] | +8.6 [+4.6, +13.2] | +4.4 [+1.2, +7.3] |
| large-v3_cuda_float16_fast | 25.3 [18.9, 32.8] | 26.0 [20.0, 33.5] | 25.4 [19.4, 32.7] | 26.6 [20.5, 33.7] | 29.0 [22.5, 36.2] | 33.4 [27.0, 40.2] | 27.9 [21.3, 35.3] | 29.9 [23.7, 37.4] | +1.2 [-0.5, +3.1] | +3.6 [+1.2, +6.4] | +8.1 [+4.2, +12.2] | +4.5 [+1.8, +7.8] |
| large-v3_cuda_float16_final | 25.5 [19.2, 33.0] | 25.5 [19.4, 33.2] | 25.7 [19.5, 32.8] | 25.9 [19.7, 33.1] | 27.4 [21.3, 34.7] | 32.3 [25.8, 39.6] | 26.4 [20.1, 33.7] | 28.5 [21.6, 35.9] | +0.4 [-1.3, +2.2] | +1.8 [-0.1, +4.0] | +6.7 [+3.0, +11.1] | +2.9 [+0.4, +5.7] |
| large-v3_cuda_int8_float16_final | 24.5 [18.5, 31.6] | 25.5 [19.4, 33.0] | 25.9 [19.6, 33.3] | 26.9 [20.9, 33.9] | 27.7 [21.6, 34.9] | 34.1 [27.4, 41.8] | 26.3 [20.1, 33.5] | 27.9 [21.8, 34.9] | +2.3 [+1.0, +3.9] | +3.2 [+1.0, +5.4] | +9.6 [+5.4, +14.7] | +3.4 [+1.2, +5.7] |
| medium_cuda_float16_fast | 35.3 [27.8, 43.8] | 36.6 [29.7, 44.6] | 35.5 [28.3, 43.1] | 38.8 [31.8, 45.8] | 40.3 [33.3, 47.1] | 48.5 [40.9, 55.5] | 41.2 [33.8, 48.9] | 43.2 [35.2, 51.8] | +3.4 [+0.4, +7.0] | +5.0 [+0.7, +9.2] | +13.2 [+7.7, +18.9] | +7.8 [+4.2, +11.9] |
| medium_cuda_float16_final | 33.5 [26.0, 41.6] | 34.8 [27.6, 42.6] | 33.9 [26.8, 41.4] | 37.3 [30.2, 44.8] | 39.2 [31.8, 46.6] | 47.9 [39.9, 55.2] | 39.6 [31.7, 47.5] | 41.4 [33.8, 49.5] | +3.8 [+1.1, +6.9] | +5.7 [+2.1, +9.3] | +14.4 [+9.9, +19.5] | +7.9 [+4.7, +11.5] |
| small_cuda_float16_fast | 30.7 [24.6, 37.6] | 31.0 [25.3, 37.7] | 31.0 [25.3, 37.4] | 33.3 [27.6, 39.5] | 35.7 [29.8, 41.7] | 42.6 [36.4, 48.9] | 35.0 [28.8, 41.9] | 36.3 [30.8, 42.9] | +2.6 [+0.8, +4.4] | +5.0 [+2.4, +7.6] | +11.9 [+8.5, +15.8] | +5.6 [+2.4, +9.0] |
| small_cuda_float16_final | 29.7 [23.8, 36.3] | 30.3 [24.5, 36.7] | 31.0 [25.2, 37.5] | 32.1 [26.1, 38.6] | 35.4 [29.7, 41.9] | 42.5 [35.7, 49.3] | 33.0 [27.1, 39.5] | 35.6 [29.8, 42.0] | +2.4 [+0.7, +4.2] | +5.7 [+3.6, +8.0] | +12.8 [+9.6, +16.4] | +5.9 [+3.5, +8.7] |
| kotoba-whisper-v2.0_cuda_float16_final＋small-fast-no_speech | 30.4 [24.2, 37.3] | 30.2 [24.3, 37.1] | 31.6 [25.4, 38.6] | 33.2 [26.6, 40.3] | 33.7 [27.5, 40.1] | 36.4 [30.0, 42.8] | 31.6 [25.3, 38.3] | 32.7 [26.8, 39.3] | +2.8 [+1.3, +4.3] | +3.3 [+1.6, +5.4] | +6.0 [+3.6, +8.8] | +2.3 [+0.8, +4.2] |
| large-v3-turbo_cuda_float16_final＋small-fast-no_speech | 27.9 [21.6, 35.3] | 29.7 [23.0, 37.4] | 30.4 [23.3, 38.4] | 31.7 [24.5, 39.8] | 34.7 [26.4, 43.2] | 36.9 [29.1, 45.1] | 29.9 [23.1, 37.3] | 32.1 [24.9, 39.4] | +3.8 [+1.0, +7.1] | +6.7 [+3.2, +10.4] | +8.9 [+5.0, +13.1] | +4.1 [+1.1, +7.0] |

### 成對比較（ΔCER = A − B，負值代表 A 較好；P = A 優於 B 的 bootstrap 機率）

| 資料／條件 | 模式 | A | B | ΔCER [95% CI] | P(A 較好) | 段數／block |
|---|---|---|---|---|---|---|
| YODAS 乾淨 | fast | kotoba-whisper-v2.0 | large-v3 | +3.9 [+1.4, +6.5] | 0.001 | 500／72 |
| YODAS 乾淨 | fast | large-v3-turbo | large-v3 | +1.3 [-0.8, +3.5] | 0.123 | 500／72 |
| YODAS 乾淨 | fast | kotoba-whisper-v2.0 | large-v3-turbo | +2.7 [+0.3, +5.1] | 0.019 | 500／72 |
| YODAS 乾淨 | fast | medium | large-v3 | +11.0 [+7.2, +14.9] | 0.000 | 500／72 |
| YODAS 乾淨 | fast | small | large-v3-turbo | +5.6 [+2.9, +8.2] | 0.000 | 500／72 |
| YODAS 乾淨 | final | kotoba-whisper-v2.0 | large-v3 | +4.7 [+2.3, +7.2] | 0.000 | 500／72 |
| YODAS 乾淨 | final | large-v3-turbo | large-v3 | +0.7 [-1.0, +2.5] | 0.208 | 500／72 |
| YODAS 乾淨 | final | kotoba-whisper-v2.0 | large-v3-turbo | +4.0 [+1.8, +6.1] | 0.001 | 500／72 |
| YODAS 乾淨 | final | medium | large-v3 | +9.6 [+5.7, +13.4] | 0.000 | 500／72 |
| YODAS 乾淨 | final | small | large-v3-turbo | +5.3 [+3.0, +7.6] | 0.000 | 500／72 |
| YODAS music 10 dB | fast | kotoba-whisper-v2.0 | large-v3 | +4.5 [+0.8, +8.0] | 0.011 | 200／72 |
| YODAS music 10 dB | fast | large-v3-turbo | large-v3 | +2.0 [-1.0, +5.3] | 0.105 | 200／72 |
| YODAS music 10 dB | fast | kotoba-whisper-v2.0 | large-v3-turbo | +2.5 [-1.1, +5.7] | 0.089 | 200／72 |
| YODAS music 10 dB | fast | medium | large-v3 | +12.2 [+7.1, +17.2] | 0.000 | 200／72 |
| YODAS music 10 dB | fast | small | large-v3-turbo | +4.8 [+0.7, +8.3] | 0.015 | 200／72 |
| YODAS music 10 dB | final | kotoba-whisper-v2.0 | large-v3 | +5.7 [+1.9, +9.2] | 0.002 | 200／72 |
| YODAS music 10 dB | final | large-v3-turbo | large-v3 | +4.1 [+0.0, +8.2] | 0.025 | 200／72 |
| YODAS music 10 dB | final | kotoba-whisper-v2.0 | large-v3-turbo | +1.6 [-1.9, +4.7] | 0.160 | 200／72 |
| YODAS music 10 dB | final | medium | large-v3 | +11.4 [+6.9, +16.5] | 0.000 | 200／72 |
| YODAS music 10 dB | final | small | large-v3-turbo | +2.2 [-2.4, +6.0] | 0.129 | 200／72 |
| YODAS music 5 dB | fast | kotoba-whisper-v2.0 | large-v3 | +3.1 [-0.4, +6.3] | 0.036 | 200／72 |
| YODAS music 5 dB | fast | large-v3-turbo | large-v3 | +1.1 [-2.3, +4.1] | 0.247 | 200／72 |
| YODAS music 5 dB | fast | kotoba-whisper-v2.0 | large-v3-turbo | +2.0 [-1.4, +5.4] | 0.144 | 200／72 |
| YODAS music 5 dB | fast | medium | large-v3 | +11.4 [+6.2, +16.3] | 0.000 | 200／72 |
| YODAS music 5 dB | fast | small | large-v3-turbo | +5.6 [+1.4, +9.6] | 0.008 | 200／72 |
| YODAS music 5 dB | final | kotoba-whisper-v2.0 | large-v3 | +4.6 [+1.0, +8.0] | 0.008 | 200／72 |
| YODAS music 5 dB | final | large-v3-turbo | large-v3 | +5.3 [+0.8, +10.1] | 0.011 | 200／72 |
| YODAS music 5 dB | final | kotoba-whisper-v2.0 | large-v3-turbo | -0.7 [-5.0, +3.2] | 0.613 | 200／72 |
| YODAS music 5 dB | final | medium | large-v3 | +11.9 [+6.8, +16.7] | 0.000 | 200／72 |
| YODAS music 5 dB | final | small | large-v3-turbo | +2.8 [-2.3, +7.1] | 0.110 | 200／72 |
| FLEURS | fast | kotoba-whisper-v2.0 | large-v3 | +1.4 [+0.7, +2.2] | 0.000 | 200／165 |
| FLEURS | fast | large-v3-turbo | large-v3 | +0.2 [-0.3, +0.7] | 0.266 | 200／165 |
| FLEURS | fast | kotoba-whisper-v2.0 | large-v3-turbo | +1.3 [+0.5, +2.0] | 0.000 | 200／165 |
| FLEURS | fast | medium | large-v3 | +2.8 [+2.1, +3.5] | 0.000 | 200／165 |
| FLEURS | fast | small | large-v3-turbo | +7.7 [+6.6, +9.0] | 0.000 | 200／165 |
| FLEURS | final | kotoba-whisper-v2.0 | large-v3 | +2.2 [+1.4, +3.3] | 0.000 | 200／165 |
| FLEURS | final | large-v3-turbo | large-v3 | +0.4 [-0.0, +0.9] | 0.035 | 200／165 |
| FLEURS | final | kotoba-whisper-v2.0 | large-v3-turbo | +1.8 [+1.0, +2.7] | 0.000 | 200／165 |
| FLEURS | final | medium | large-v3 | +2.7 [+2.0, +3.5] | 0.000 | 200／165 |
| FLEURS | final | small | large-v3-turbo | +7.1 [+5.8, +8.3] | 0.000 | 200／165 |
| ReazonSpeech | fast | kotoba-whisper-v2.0 | large-v3 | -2.5 [-3.9, -1.2] | 1.000 | 300／300 |
| ReazonSpeech | fast | large-v3-turbo | large-v3 | -2.3 [-4.2, -0.1] | 0.981 | 300／300 |
| ReazonSpeech | fast | kotoba-whisper-v2.0 | large-v3-turbo | -0.2 [-1.9, +1.5] | 0.605 | 300／300 |
| ReazonSpeech | fast | medium | large-v3 | +10.8 [+8.0, +13.8] | 0.000 | 300／300 |
| ReazonSpeech | fast | small | large-v3-turbo | +15.3 [+12.3, +18.4] | 0.000 | 300／300 |
| ReazonSpeech | final | kotoba-whisper-v2.0 | large-v3 | -2.2 [-3.9, -0.8] | 0.997 | 300／300 |
| ReazonSpeech | final | large-v3-turbo | large-v3 | -3.6 [-5.2, -2.1] | 1.000 | 300／300 |
| ReazonSpeech | final | kotoba-whisper-v2.0 | large-v3-turbo | +1.3 [-0.2, +3.0] | 0.038 | 300／300 |
| ReazonSpeech | final | medium | large-v3 | +10.6 [+8.0, +13.6] | 0.000 | 300／300 |
| ReazonSpeech | final | small | large-v3-turbo | +14.9 [+12.6, +17.0] | 0.000 | 300／300 |
| YODAS 乾淨 | — | large-v3_cuda_int8_float16_final | large-v3_cuda_float16_final | -0.4 [-1.0, +0.3] | 0.873 | 500／72 |
| YODAS music 10 dB | — | large-v3_cuda_int8_float16_final | large-v3_cuda_float16_final | +0.9 [-0.0, +2.0] | 0.032 | 200／72 |
| YODAS music 5 dB | — | large-v3_cuda_int8_float16_final | large-v3_cuda_float16_final | +0.3 [-1.0, +1.7] | 0.311 | 200／72 |
| FLEURS | — | large-v3_cuda_int8_float16_final | large-v3_cuda_float16_final | +0.1 [-0.1, +0.3] | 0.107 | 200／165 |
| ReazonSpeech | — | large-v3_cuda_int8_float16_final | large-v3_cuda_float16_final | +0.3 [-0.1, +0.8] | 0.068 | 300／300 |

### 延遲、非語音幻覺

| 設定 | 延遲中位 ms | P90 ms | RTF | 段數 | 非語音輸出文字（過濾後） | 非語音輸出文字（未過濾） | 實際裝置 | GPU 已用 MB（範圍） | GPU 使用率 %（範圍） |
|---|---|---|---|---|---|---|---|---|---|
| kotoba-whisper-v2.0_cuda_float16_fast | 141 | 168 | 0.026 | 2406 | 46/50 | 50/50 | cuda float16 | 3376–3492 | 42–99 |
| kotoba-whisper-v2.0_cuda_float16_final | 252 | 360 | 0.049 | 2406 | 50/50 | 50/50 | cuda float16 | 7142–7438 | 57–99 |
| large-v3-turbo_cuda_float16_fast | 161 | 202 | 0.030 | 2406 | 2/50 | 50/50 | cuda float16 | 3522–3624 | 56–99 |
| large-v3-turbo_cuda_float16_final | 170 | 215 | 0.032 | 2406 | 2/50 | 50/50 | cuda float16 | 3539–3604 | 61–99 |
| large-v3_cuda_float16_fast | 402 | 641 | 0.077 | 2406 | 1/50 | 50/50 | cuda float16 | 5280–5368 | 22–97 |
| large-v3_cuda_float16_final | 521 | 1138 | 0.115 | 2406 | 0/50 | 50/50 | cuda float16 | 5308–9223 | 24–94 |
| large-v3_cuda_int8_float16_final | 1210 | 2256 | 0.247 | 2406 | 1/50 | 50/50 | cuda int8_float16 | 5658–11302 | 68–93 |
| medium_cuda_float16_fast | 526 | 1102 | 0.113 | 2406 | 0/50 | 50/50 | cuda float16 | 3389–9856 | 23–85 |
| medium_cuda_float16_final | 347 | 594 | 0.073 | 2406 | 1/50 | 50/50 | cuda float16 | 3375–3438 | 18–60 |
| small_cuda_float16_fast | 332 | 610 | 0.066 | 2406 | 1/50 | 50/50 | cuda float16 | 2991–7446 | 68–88 |
| small_cuda_float16_final | 170 | 295 | 0.036 | 2406 | 0/50 | 50/50 | cuda float16 | 1988–2429 | 19–52 |
| kotoba-whisper-v2.0_cuda_float16_final＋small-fast-no_speech | 252 | 360 | 0.049 | 2406 | 17/50 | 50/50 | cuda float16 | 7142–7438 | 57–99 |
| large-v3-turbo_cuda_float16_final＋small-fast-no_speech | 170 | 215 | 0.032 | 2406 | 0/50 | 50/50 | cuda float16 | 3539–3604 | 61–99 |

### CPU int8（子集：YODAS 乾淨 150 段、FLEURS 50 段、動漫 6 段）

| 設定 | YODAS 過濾後 | YODAS 未過濾 | YODAS 去填充詞 | YODAS 去疑似錯位 | FLEURS | ReazonSpeech | 動漫 6 段 | 語音被誤濾 |
|---|---|---|---|---|---|---|---|---|
| base_cpu_int8_t8_fast（CPU 子集） | 37.8 [30.7, 45.8] | 43.7 [33.6, 56.6] | 37.8 [30.7, 45.8] | 28.2 [23.8, 32.4] | 29.7 [25.5, 34.9] | — | 42.9 [21.5, 62.2] | 5/206 |
| base_cpu_int8_t8_final（CPU 子集） | 34.7 [27.9, 42.8] | 34.9 [28.2, 42.8] | 34.7 [27.9, 42.8] | 25.1 [21.2, 29.3] | 25.2 [21.5, 29.4] | — | 26.2 [11.2, 41.1] | 2/206 |
| small_cpu_int8_t8_fast（CPU 子集） | 30.2 [23.9, 37.3] | 29.1 [22.9, 36.0] | 30.2 [23.9, 37.3] | 21.6 [18.0, 25.4] | 16.9 [11.9, 22.6] | — | 39.3 [23.4, 56.8] | 4/206 |
| small_cpu_int8_t8_final（CPU 子集） | 28.8 [22.2, 36.3] | 27.8 [21.2, 35.0] | 28.8 [22.2, 36.3] | 19.3 [15.9, 22.9] | 12.6 [10.0, 15.2] | — | 35.7 [23.2, 50.0] | 4/206 |

### 延遲、非語音幻覺

| 設定 | 延遲中位 ms | P90 ms | RTF | 段數 | 非語音輸出文字（過濾後） | 非語音輸出文字（未過濾） | 實際裝置 | GPU 已用 MB（範圍） | GPU 使用率 %（範圍） |
|---|---|---|---|---|---|---|---|---|---|
| base_cpu_int8_t8_fast（CPU 子集） | 663 | 863 | 0.108 | 206 | 1/50 | 50/50 | cpu int8 | — | — |
| base_cpu_int8_t8_final（CPU 子集） | 685 | 944 | 0.120 | 206 | 4/50 | 50/50 | cpu int8 | — | — |
| small_cpu_int8_t8_fast（CPU 子集） | 1641 | 1991 | 0.268 | 206 | 1/50 | 50/50 | cpu int8 | — | — |
| small_cpu_int8_t8_final（CPU 子集） | 1724 | 2170 | 0.283 | 206 | 2/50 | 50/50 | cpu int8 | — | — |

### 同一 CPU 子集上的 GPU 設定（對照用）

| 設定 | YODAS 子集 | FLEURS 子集 |
|---|---|---|
| kotoba-whisper-v2.0_cuda_float16_fast | 24.5 [19.2, 30.3] | 10.3 [5.6, 15.8] |
| kotoba-whisper-v2.0_cuda_float16_final | 25.5 [19.9, 31.5] | 7.9 [4.9, 11.5] |
| large-v3-turbo_cuda_float16_fast | 23.4 [17.3, 30.6] | 9.1 [4.9, 14.6] |
| large-v3-turbo_cuda_float16_final | 22.6 [16.8, 29.5] | 5.4 [3.9, 7.2] |
| large-v3_cuda_float16_fast | 23.1 [17.2, 30.4] | 8.5 [4.7, 13.7] |
| large-v3_cuda_float16_final | 23.5 [17.5, 30.7] | 4.9 [3.4, 6.4] |
| large-v3_cuda_int8_float16_final | 21.9 [15.9, 28.9] | 4.9 [3.4, 6.5] |
| medium_cuda_float16_fast | 33.8 [26.1, 41.6] | 10.9 [6.5, 17.1] |
| medium_cuda_float16_final | 32.5 [24.9, 40.5] | 8.0 [5.6, 10.7] |
| small_cuda_float16_fast | 28.8 [22.6, 35.7] | 16.3 [11.7, 21.6] |
| small_cuda_float16_final | 26.4 [20.7, 32.8] | 12.5 [9.9, 15.1] |
| kotoba-whisper-v2.0_cuda_float16_final＋small-fast-no_speech | 26.3 [20.2, 32.7] | 7.9 [4.9, 11.5] |
| large-v3-turbo_cuda_float16_final＋small-fast-no_speech | 22.9 [16.8, 29.7] | 5.4 [3.9, 7.2] |

## 限制與注意事項

- **YODAS 字幕不是逐字稿**：500 段中有 85 段（17%）所有 GPU final 設定的 CER 中位數都 > 50%——字幕是摘要、改寫，或切段時間對不上音訊（音訊裡的話比字幕多或少）。這讓**絕對 CER 偏高**（乾淨語音 23–35%），但所有模型面對同一批參考文字、比較是成對的，相對排序可信；「去疑似錯位」欄排除這 85 段後絕對值降到 12.5–25.7%，**排序不變**。
- **YODAS 不是 VTuber**：是 CC 授權的一般 YouTube（vlog、教學、料理、新聞評論；有些影片用合成語音旁白，未排除）。沒有角色聲線、尖叫、多人連動、歌回。報告所說「自建 VTuber 集」仍是決定性依據；這裡是最接近的公開代理。動漫 6 段的 CI 太寬（±15 個百分點），不能下結論。
- **去填充詞變體幾乎沒有差別**：YODAS 字幕與 Whisper 輸出都很少寫出えー、あの（500 段參考文字只有 2 段受影響）——Whisper 本身就會省略填充詞，這份資料測不出填充詞處理的差異。
- **延遲在共用 GPU 下量測**：同時有 Ollama（qwen3.5:9b）與 kikitori-engine 佔用 GPU，CPU 子集也與部分 GPU 設定同時執行。small／medium 的延遲受干擾最明顯（例如 small fast 332 ms 反而比 small final 170 ms 慢），**小模型之間的延遲排序不可信**；大模型（turbo vs large-v3）差距約 2.5 倍、方向與 `experiments/asr_benchmark` 單獨量測一致。絕對延遲請以 asr_benchmark 為準。
- **BGM 素材**：MUSAN 的無人聲西洋器樂；沒有 J-POP、動畫歌、有人聲的 BGM 或歌回。每段只配一首 BGM、單一 seed（以 200 段各自隨機配樂分散，未做多 seed 平均）。
- **ReazonSpeech 沒有節目 id**，只能逐句 bootstrap，CI 可能偏窄；授權限於資訊解析，只當校準。
- **非語音只有 50 段**（30 BGM＋20 音效），幻覺率的區間很寬；但 kotoba 的 46–50/50 與其他模型的 0–4/50 差距大到不受影響。
- 過濾規則以評測當下的 `rejection_reason()`（`FilterConfig()` 預設）為準；`kikitori.asr.filtering` 正被重構，規則改了之後重跑選單第 4 步即可重新計分（不必重跑模型）。
- 沒有測：即時與完整兩個模型同時執行的互相干擾、turbo／kotoba 的 int8_float16、turbo／kotoba 在 CPU、sherpa-onnx 系列（parakeet-ja、reazonspeech-k2）、FLEURS 以外語言、Linux／macOS。

