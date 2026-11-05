# 日語自發/直播風格 ASR 與日→中/英語音翻譯的評測資料集與指標（給 kikitori 本地 benchmark）

> 調查日期：2026-11-05。來源以論文、官方資料集頁、官方 repo 為主；標示「線索」者為非一手或未核對來源，不可當證據。
> 本筆記只做調查，未修改任何程式碼。

## Q1. 日語 ASR 測試集：領域、規模、授權、取得方式、能否做小型評測子集

### Takeaway
找不到任何「VTuber／直播」專用、來自正式機構的日語語音資料集；最接近的是 YouTube 衍生語料（YODAS＝CC BY 3.0、JTubeSpeech／J-CHAT＝只給 ID 或限第 30-4 條「資訊解析」用途）和電視語料（ReazonSpeech／LaboroTVSpeech）。可以合法放進 repo 的小型子集，實際上只有 CC0／CC-BY 系的 Common Voice（但 datasheet 禁止重新散布）、FLEURS（CC-BY 4.0）、YODAS（CC BY 3.0）、TEDxJP-10K（Apache-2.0 標記）。其餘一律改成執行時由使用者下載，並加進 git-ignore。

### Cited Findings

**未找到 VTuber/直播專用語音語料**
- 以「VTuber speech dataset / livestream ASR corpus」搜尋，沒找到從 VTuber 直播建立的語音資料集。"Virtual Stars, Real Fans: Understanding the VTuber Ecosystem" 研究的是直播彈幕與場次 metadata，不是語音轉寫語料 — [arXiv 2502.01553](https://arxiv.org/pdf/2502.01553)
- 遊戲解說（最接近「遊戲直播口語」）：SMASH corpus（LREC 2020）收錄兩位日本男性解說員對格鬥遊戲做的即興第三人稱解說，有自動與人工轉寫和主題標記；頁面沒寫時數與授權 — [ACL Anthology 2020.lrec-1.809](https://aclanthology.org/2020.lrec-1.809/)
- 遊戲解說即時生成研究（LREC 2026）釋出多語 benchmark，日語部分是 64 支格鬥遊戲影片的人類口述解說與轉寫，但用途是解說生成，不是 ASR 評測 — [arXiv 2603.02655](https://arxiv.org/html/2603.02655v1)；[LREC 2026 PDF](https://aclanthology.org/2026.lrec-1.722.pdf)

**ReazonSpeech（電視廣播）**
- 資料來自地面電視串流，16 kHz FLAC＋轉寫；子集為 tiny 8.5 h / small 100 h / medium 1,000 h / large 5,000 h / all 35,000 h — [HF reazon-research/reazonspeech](https://huggingface.co/datasets/reazon-research/reazonspeech)
- 授權標為 CDLA-Sharing-1.0，但頁面另要求同意「YOU MUST AGREE THAT YOU WILL USE THE DATASET SOLELY FOR THE PURPOSE OF JAPANESE COPYRIGHT ACT ARTICLE 30-4」，且需登入 HF 並接受條款（gated） — [HF reazon-research/reazonspeech](https://huggingface.co/datasets/reazon-research/reazonspeech)
- 社群常用的 held-out 測試集是 `japanese-asr/ja_asr.reazonspeech_test`（kotoba-whisper 評測使用），另外兩個常用測試集是 `japanese-asr/ja_asr.common_voice_8_0`、`japanese-asr/ja_asr.jsut_basic5000` — [kotoba-whisper-v2.0 README](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0/blob/main/README.md)
- 參考 CER（%）：kotoba-whisper-v2.0 為 CV8 9.2 / JSUT 8.4 / ReazonSpeech test 11.6；whisper-large-v3 為 8.5 / 7.1 / 14.9 — [kotoba-whisper-v2.0 README](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0/blob/main/README.md)。v1.0 模型卡在不同 revision 的數字不一致（例如 ReazonSpeech CER 12.2 和 12.6），引用時要註明 revision — [v1.0 README（某 revision）](https://huggingface.co/kotoba-tech/kotoba-whisper-v1.0/blob/ffbf23c8324b15b20205bf90f7cf9f7ab3d6038d/README.md)

**LaboroTVSpeech／TEDxJP-10K**
- LaboroTVSpeech 是超過 2,000 小時的日本電視錄音＋字幕語料；同一篇論文指出 CSJ「主要由學術演講與模擬公開演講組成」，也就是說 CSJ 的「自發」程度比閒聊直播低 — [arXiv 2103.14736](https://arxiv.org/pdf/2103.14736)
- TEDxJP-10K 是從日語 TEDx 影片和字幕建立的 ASR 評測集，用途是做「與訓練語料無重疊」的評測；README 說明轉寫和時間戳經過人工修正，但仍可能有錯；repo 標 Apache-2.0 — [GitHub laboroai/TEDxJP-10K](https://github.com/laboroai/TEDxJP-10K)
- v1.1 約 8.85 小時、9,962 句（第三方目錄，需自行核對） — [MFA corpus catalog](https://mfa-models.readthedocs.io/en/latest/corpus/Japanese/TEDxJP-10K%20v1_1.html)

**YouTube 衍生語料**
- YODAS（ASRU 2023）：選片條件是「影片必須附 Creative Commons 授權」，並優先選有人工或自動字幕的影片；音訊轉成 mono 24 kHz；Table 1 中 manual／automatic／unlabeled 三個子集都標 CC BY 3.0；每個語言另外留 1,000 句當測試集；發佈在 HF `espnet/yodas` — [arXiv 2406.00899](https://arxiv.org/html/2406.00899v1)
- YODAS 論文的 arXiv metadata 標 CC BY 4.0，和 Table 1 的 CC BY 3.0 不同；前者較可能是論文本身的授權，資料以 Table 1／dataset card 為準 — [arXiv 2406.00899](https://arxiv.org/html/2406.00899v1)
- YODAS 論文沒有討論 YouTube 服務條款，也沒談重新散布 YouTube 內容的問題 — [arXiv 2406.00899](https://arxiv.org/html/2406.00899v1)
- YODAS v3 預印本：超過 110 萬小時、48 kHz 多聲道、147 種語言，CC BY 3.0（來自搜尋摘要，未逐字核對） — [arXiv 2609.29448](https://arxiv.org/pdf/2609.29448)
- JTubeSpeech：repo 只提供影片 ID 清單（`data/{lang}/{YYYYMM}.csv`，欄位 videoid/auto/sub/channelid），音訊要用腳本自行下載；日語有人工字幕的影片約 11 萬支（約 1 萬小時）、自動字幕約 496 萬支；repo 標 Apache-2.0（只適用於程式與清單，不代表影片內容的授權）；沒有現成的 ASR 測試子集 — [GitHub sarulab-speech/jtubespeech](https://github.com/sarulab-speech/jtubespeech)
- JTubeSpeech 論文提到 ASR 用資料超過 1,300 小時、ASV 用資料 900 小時 — [arXiv 2112.09323](https://www.arxiv.org/pdf/2112.09323)
- J-CHAT（東大猿渡研）：7.6 萬小時日語對話語音，來源是 YouTube（1.1 萬小時）和 Podcast（6.5 萬小時）；所有音訊都經 Demucs 去除 BGM；轉寫由 ASR（reazonspeech-nemo-v2）產生，不是人工；YouTube 子集的 test split 只有 1.2 小時、Podcast 子集為 1.3 小時；授權是「…NC 4.0」（PDF 抽字時斷行，推定為 CC BY-NC 4.0），限 Article 30-4「資訊解析」用途，論文明說「not for entertainment or content redistribution」，並經第三方日本律師審查、提供 opt-out — [arXiv 2407.15828](https://arxiv.org/pdf/2407.15828)；[HF sarulab-speech/J-CHAT](https://huggingface.co/datasets/sarulab-speech/J-CHAT)
- J-CHAT 論文提到 YouTube 上很多內容是純音樂或單人獨白（例如遊戲實況），所以對話比例低 — [arXiv 2407.15828](https://arxiv.org/pdf/2407.15828)

**Common Voice ja／FLEURS ja**
- Common Voice Scripted Speech 25.0 日語：725.11 小時（其中 371.92 小時通過驗證），7,813 位說話者，CC0-1.0；下載即同意不去辨識說話者身分 — [Mozilla Data Collective CV 25.0 ja](https://datacollective.mozillafoundation.org/datasets/cmn2hm68r01n4mm071qux43yu)
- CV 24.0 datasheet 明文禁止辨識說話者，也「forbidden to re-host or re-share this dataset」 — [Mozilla Data Collective CV 24.0 ja](https://datacollective.mozillafoundation.org/datasets/cmj8u3p9x00clnxxbsv97o45e)
- FLEURS 官方 HF card 授權為 cc-by-4.0（ja_jp、cmn_hans_cn 也一樣） — [HF google/fleurs](https://huggingface.co/datasets/google/fleurs)
- FLEURS 測試集筆數：ja_jp 650、cmn_hans_cn 945（第三方重新包裝的版本，需自行核對） — [HF FluidInference/fleurs-full](https://huggingface.co/datasets/FluidInference/fleurs-full/blob/main/README.md)

**JSUT／JVS（朗讀，非自發）**
- JSUT／JVS 的使用條款允許學術研究、非商業研究（含企業內研究）和個人使用；禁止重新散布，但允許在網頁上放少量檔案（JSUT-song 約 3 個、JSSS 約 5 個）；商業使用要另外洽談 TLO。各頁面對允許範圍的寫法不一致（JVS 英文版只寫 academic research） — [JVS corpus 頁](https://sites.google.com/site/shinnosuketakamichi/research-topics/jvs_corpus)；[JSUT 頁](https://sites.google.com/site/shinnosuketakamichi/publication/jsut)；[JSUT-song 頁](https://sites.google.com/site/shinnosuketakamichi/publication/jsut-song)

**CEJC／CSJ（NINJAL，付費授權）**
- CEJC：200 小時、577 段對話、約 240 萬語、1,675 位說話者，2022 年公開；分為非商業學術授權和商業授權（付費版）兩種合約範本，網路上查不到價格 — [CEJC Non-Commercial Academic 合約](https://clrd.ninjal.ac.jp/cejc/CEJC_Non_Commercial_1_Academic.pdf)；[CEJC Commercial 合約](https://clrd.ninjal.ac.jp/cejc/CEJC_Commercial_1.pdf)；[NINJAL 專案頁](https://www.ninjal.ac.jp/english/research/project-3/institute/spoken-language)

### Inferences
- **VTuber 直播條件只能「近似」**：沒有公開的 VTuber 直播語音資料集。比較實際的組合是：（a）自發口語的 YouTube 素材（YODAS ja 測試句，CC BY 3.0）；（b）高音、角色聲線：使用者本地的 anime 片段（有參考文字）；（c）BGM／音效：用 MUSAN 或合成噪音依受控 SNR 混音（見 Q3）；（d）VTuber 片段：補上人工參考轉寫後只放本地。
- **哪些可以進 repo**：FLEURS（CC-BY 4.0，需標示出處）和 YODAS（CC BY 3.0，需標示出處）的小子集在授權上允許重新散布。Common Voice 雖然是 CC0，但 Mozilla datasheet 禁止 re-host，所以應做成「執行時下載」。ReazonSpeech、J-CHAT、JTubeSpeech、JSUT/JVS、CEJC/CSJ 一律執行時由使用者下載或自備，並加進 git-ignore。
- **本地的 6 段 anime 和 6 段 VTuber 片段有著作權**：只能放在本地、加進 git-ignore，不能 commit。repo 裡只放 manifest（檔名 hash、時間段、參考文字的存放位置），不放音訊。
- ReazonSpeech test 是電視播報加對話，和直播的落差比 CV、JSUT 小，適合當「既有基準對照」，用來跟 kotoba 等公開數字核對自己的評測管線。
- J-CHAT 的轉寫是 ASR 產生的，而且音訊已經去除 BGM，所以不適合當 ASR 的金標準參考。

### Gaps
- CSJ 的確切時數、價格和最新授權條款，本次沒有從 NINJAL 官方頁核對（常見說法約 650 小時，未驗證）。
- YODAS 日語 manual／automatic 子集的確切時數只畫在圖上，沒有找到數字；HF `espnet/yodas` dataset card 也未直接核對。
- YouTube 衍生的 CC 素材在 YouTube 服務條款下能否下載、重新散布，沒有找到正式的法律分析；YODAS 論文本身也沒談。
- 「本地評測（benchmark）」是否落在日本著作權法第 30-4 條「資訊解析」的範圍內：J-CHAT 和 ReazonSpeech 都援引這一條，但它們的情境是語料散布，不是評測；本調查不構成法律意見。
- 沒有找到可信、有人工轉寫的 anime 或角色聲線 ASR 測試集（例如來自正式機構的動畫語音語料）。
- FLEURS 的確切測試筆數和 n-way parallel 設計未在官方頁逐項核對。

## Q2. 以日語為源語言的語音翻譯測試集（ja→en、ja→zh）

### Takeaway
真正有「日語語音＋人工譯文」的公開測試集，基本上只有 CoVoST 2 ja→en（約 1 小時，朗讀句）和 FLEURS（ja 語音，可透過 FLoRes 系平行句對到 en／zh 譯文來做 cascaded ST，但 n-way 對齊本次未核對）。IWSLT 近年的日語賽道都是 en→ja，不是 ja→X。沒有找到公開的 ja→zh（特別是 zh-TW）語音翻譯測試集，所以 kikitori 必須自建參考譯文。

### Cited Findings
- CoVoST 2 共 2,880 小時、21 種源語言→英語，包含 Japanese；Ja→En 測試集約 1 小時（表格四捨五入到整數小時）、37 位測試說話者；中日文的 token 數以字元計 — [arXiv 2007.10310](https://arxiv.org/pdf/2007.10310)
- CoVoST 2 的譯文另外提供下載（`covost_v2.ja_en.tsv.tar.gz`），要和 Common Voice 的 `validated.tsv` 對應；資料授權 CC0，repo 其他內容是 CC BY-NC 4.0 — [GitHub facebookresearch/covost](https://www.github.com/facebookresearch/covost)
- IWSLT 2023 的 TED 測試集是 en→ja 和 en→zh（37 和 38 場演講），源語言是英語 — [arXiv 2406.03881](https://arxiv.org/pdf/2406.03881)
- IWSLT 2025 同步語音翻譯賽道是 English→German/Japanese/Chinese；IWSLT 2026 offline 賽道測試資料是 en→de，以 COMET 加自動重新分段來排名 — [IWSLT 2025 Findings](https://aclanthology.org/2025.iwslt-1.44)；[IWSLT 2026 Offline track](https://iwslt.org/2026/offline)
- FLEURS 提供 ja_jp 和 cmn_hans_cn 的語音，授權 cc-by-4.0 — [HF google/fleurs](https://huggingface.co/datasets/google/fleurs)

### Inferences
- **ja→en**：CoVoST 2 測試集（CC0 音訊和譯文，但音訊來自 Common Voice，不可 re-host）可作為「乾淨朗讀」的下限參考，評測時執行期再下載。
- **ja→zh**：比較可行的做法是用 FLEURS ja 語音加 FLoRes 對應的 zho_Hant（繁中）或 zho_Hans 譯文。這要先核對 FLEURS 與 FLoRes-200 的句子 ID 對齊方式和 FLoRes 的授權（本次未核對）。
- **直播領域**：所有公開 ST 測試集都是朗讀或演講，和直播口語差距很大。建議對本地的 anime 和 VTuber 片段請人做 zh-TW／en 參考譯文（本地保存、不散布），並把「ASR 錯誤傳遞」和「MT 本身品質」分開評：同一份源文分別用人工參考轉寫和 ASR 輸出餵給 MT，比較兩者的分數差。
- JESC、OpenSubtitles 這類字幕語料是純文字，只能評 MT 段；而且字幕授權複雜，不建議放進 repo。

### Gaps
- 沒有找到公開的 ja→zh（尤其 zh-TW）語音翻譯測試集。
- FLEURS 和 FLoRes 的 n-way 對齊細節、FLoRes 的 zho_Hant 授權，本次未核對。
- JESC、OpenSubtitles 的授權與規模本次未調查。
- IWSLT 2025 Findings 全文未開啟逐條核對，不能排除有日語源的子任務（例如方言、低資源賽道）。

## Q3. 受控 SNR 的音樂／噪音混音協定，以及直播 BGM 合理的 SNR 範圍

### Takeaway
Kaldi 官方的 MUSAN 增強設定是事實上的標準：noise 0/5/10/15 dB（前景、每秒間隔插入）、music 5/8/10/15 dB（背景，1 首）、babble 13/15/17/20 dB（3–7 人）。廣播界以「語音對背景響度差」（SBLD）衡量：英國 DPP 和舊版 Netflix 規範要求至少 4 LU；Fraunhofer 把局部 SBLD < 0 LU 定為「關鍵（難懂）段落」；聽者偏好的差距因人而異（四分位距約 5.7 LU）。據此，直播 BGM 評測網格建議以 20/15/10/5 dB 為主、0 dB 當壓力測試。

### Cited Findings
- Kaldi `egs/voxceleb/v2/run.sh` 的 `augment_data_dir.py` 設定：noise `--fg-interval 1 --fg-snrs "15:10:5:0"`；music `--bg-snrs "15:10:8:5" --num-bg-noises "1"`；babble `--bg-snrs "20:17:15:13" --num-bg-noises "3:4:5:6:7"`；混響用 RIRS_NOISES 的 small／medium room 模擬 RIR — [kaldi voxceleb v2 run.sh](https://raw.githubusercontent.com/kaldi-asr/kaldi/master/egs/voxceleb/v2/run.sh)
- 論文對同一套設定的描述：babble 混 3–7 個 MUSAN 說話者，SNR 13–20 dB；music 5–15 dB；noise 每秒加一次，0–15 dB — [arXiv 1811.02066](https://arxiv.org/pdf/1811.02066)
- 另有以 MUSAN music／noise／babble 在 5/10/15/20 dB 做五倍增強的例子 — [arXiv 2004.01221](https://arxiv.org/pdf/2004.01221)
- Torcoli 等（Fraunhofer IIS，AES 156th，2024）：
  - SBLD（Speech-to-Background Loudness Difference）以 ITU-R BS.1770 響度計算，與 SNR、ESTOI 可懂度高度相關。
  - 英國 DPP 和舊版 Netflix 建議 SBLD 至少 4 LU。
  - EBU 建議電影內容的 LDR（節目整體響度減對白響度）低於 5 LU，但實際有時會到 15 LU 以上。
  - 他們用 −10 LU 作為關鍵 SLD 的門檻、0 LU 作為關鍵局部 SBLD 的門檻；SBLD 低於 −10 LU 的段落不算「語音活躍」。
  - 實例：某動作預告片的整體 SBLD 為 −2.9 LU，70% 語音段屬於關鍵段；另一例 SBLD 為 10.8 LU。
  - 出處：[arXiv 2405.17364](https://arxiv.org/pdf/2405.17364)
- 同文說明 LU 和 dB 等價：寬頻增益 X dB 會讓響度變化 X LU；廣播常見的整合響度目標是 −23 LUFS（EBU R128） — [arXiv 2405.17364](https://arxiv.org/pdf/2405.17364)
- 偏好的對白／背景響度差（PLD）因人差異很大，平均四分位距 5.7 LU — [arXiv 2305.19100](https://arxiv.org/abs/2305.19100)
- J-CHAT 指出 YouTube 和 Podcast 常含 BGM，並用 Demucs 去除，間接佐證網路語音素材普遍帶 BGM — [arXiv 2407.15828](https://arxiv.org/pdf/2407.15828)

### Inferences
- **建議的混音協定**（重現性優先）：
  1. 以語音的 active-speech 能量（或 BS.1770 speech-gated loudness）作為 SNR 的基準，而不是整段平均。這樣靜音比例不同的片段才能比較。
  2. BGM 用 MUSAN music（背景、全段循環）；音效用 MUSAN noise（前景、間隔插入，模擬遊戲音效）；多人或觀眾聲用 MUSAN speech 的 babble。
  3. SNR 網格：music {20, 15, 10, 5, 0} dB、noise {15, 10, 5, 0} dB（沿用 Kaldi）、babble {20, 15} dB。
  4. 固定 random seed，把每個混音條件寫進 manifest（噪音檔 ID、起始 offset、增益）。
- **直播 BGM 的現實範圍**：VTuber 主播通常會把 BGM 壓在語音之下。廣播規範的最低 SBLD 是 4 LU，偏好值因人而異，四分位距約 5.7 LU。推測一般直播落在 SBLD 約 5–20 LU，10–20 dB 是「常態」，5 dB 是「BGM 偏大／音樂歌回」，0 dB 以下相當於 Fraunhofer 定義的「關鍵段落」，只當壓力測試。這是推論，沒有直接量測直播的研究支撐。
- SBLD（K-weighted 響度差）和 SNR（未加權能量比）在音樂頻譜下會差幾 dB。報告裡要明寫用哪一個，最好兩者都記錄。

### Gaps
- 沒有找到針對 Twitch、YouTube Live 或 VTuber 直播實際 BGM 對語音比例的量測研究。
- CHiME 系列和「speech over music」專門評測的 SNR 設定本次未調查。
- MUSAN 本身的授權（一般認為是 CC BY 4.0）本次未核對。
- EBU R128、ITU-R BS.1770 原文未開啟，只引用二手整理。

## Q4. 評測指標：日語 CER 正規化、幻覺／插入率、串流延遲、MT 指標（含可在 CPU 或非 PyTorch 環境跑的選項）、zh-TW、LLM-as-judge

### Takeaway
日語 CER 沒有統一規範。kotoba 和 HF 的慣例是：Whisper 的 `BasicTextNormalizer`（NFKC，並把 Unicode M/S/P 類別換成空白，刪掉括號內文字）處理後再去掉所有空白，用 `evaluate` 的 `cer` 計算。數字、長音、漢字和假名的寫法差異都不處理，所以跨論文的數字不可直接比。非語音的幻覺要用「片段級幻覺率」（只要有輸出文字就算一次），因為沒有參考文字時無法計算 IER。串流延遲用 FTD／LTD／ATD 搭配 P50/P90，以及 partial 不穩定度。MT 方面 WMT24 的勝者是 MetricX-24-Hybrid 和 XCOMET，chrF／BLEU 明顯落後。COMET 可以用 CPU 跑（`--gpus 0`），但需要 PyTorch，應放在隔離的評測環境；CometKiwi 是 CC BY-NC-SA。

### Cited Findings

**日語 CER 正規化**
- kotoba-whisper 的評測程式：`BasicTextNormalizer`（`transformers.models.whisper.english_normalizer`）處理後 `.replace(" ", "")`，再用 `evaluate` 的 `cer` 乘以 100 — [kotoba-whisper-v2.0 README](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0/blob/main/README.md)
- Whisper `BasicTextNormalizer` 的處理步驟：
  1. 轉小寫。
  2. 刪除 `[...]`／`<...>` 和 `(...)` 內的文字。
  3. `remove_symbols`：做 NFKC，並把 Unicode 類別以 M／S／P 開頭的字元換成空白。
  4. 合併連續空白。
  - 所以全形和半形會被統一，「、。！？」和「♪」這類符號會被移除。
  - 出處：[openai/whisper normalizers/basic.py](https://raw.githubusercontent.com/openai/whisper/main/whisper/normalizers/basic.py)
- Ruby-ASR（2026 預印本）在評分前會統一數字、長音、促音、標點和假名變體，並同時報告 script-aware CER 和原始 CER；論文也指出不同的正規化方式會讓 CER 無法互相比較（來自搜尋摘要，未全文核對） — [arXiv 2609.27289](https://arxiv.org/pdf/2609.27289)

**非語音幻覺**
- Calm-Whisper 把非語音幻覺定義成片段級二元指標（只要輸出任何文字就算幻覺），因為非語音沒有參考文字，IER 無法計算；whisper-large-v3 在 UrbanSound8K 上的幻覺率是 99.97%，Conformer-CTC-large 是 13.52% — [arXiv 2505.12969](https://arxiv.org/html/2505.12969v1)
- Barański 等（2025）：40.3% 的非語音輸入會讓 Whisper 產生幻覺，論文也把「重複迴圈」（looping）列為另一類幻覺 — [arXiv 2501.11378（第三方鏡像頁）](https://arxiv.deeppaper.ai/papers/2501.11378v1)
- 後續研究用內部表徵 steering（SAE）把非語音幻覺從 72.63% 降到 14.11% — [arXiv 2606.07473](https://arxiv.org/pdf/2606.07473v1)
- 線索：Koenecke 等「Careless Whisper」（2024），英文語音中 1.4% 片段含幻覺。這個數字來自廠商部落格轉述，未核對原文 — [sonix.ai（線索，非證據）](https://sonix.ai/ai/?p=774)

**串流延遲與穩定度**
- TrimTail 對照 forced alignment 時間戳，定義 First／Last／Average Token emission Delay 和 User Sensitive Delay（last-token delay 加上 endpoint delay），並對全部測試句報告 P50 和 P90 — [arXiv 2211.00522](https://arxiv.org/pdf/2211.00522)
- Delayed-KD：每個 token 的延遲＝輸出時間戳減去真實結束時間；chunk 式模型以 chunk 結束時間當作 token 時間戳 — [arXiv 2505.22069](https://arxiv.org/pdf/2505.22069)
- Google 裝置端研究：使用者感受到的延遲主要取決於 token 發出行為和 endpointing，演算法本身的延遲影響較小；他們分開量測 first-token delay 和 finalization 時間 — [arXiv 2104.02207](https://arxiv.org/pdf/2104.02207)
- 串流穩定度：提出詞級和段級的 partial 不穩定度指標；把 partial 發出間隔從 50 ms 拉長到 200 ms，平均 partial 延遲多約 75 ms，但不穩定度下降 — [arXiv 2006.01416](https://arxiv.org/pdf/2006.01416)
- JSTAR 定義 final token 為「之後不會再改變」的 token，這對避免畫面閃爍很重要 — [arXiv 2412.15415](https://arxiv.org/pdf/2412.15415)
- 線索（廠商部落格）：建議量 capture start→first partial→last partial→final 各個里程碑，並報告 P50/P95/P99 — [Gladia（線索）](https://www.gladia.io/blog/measuring-latency-in-stt)

**MT 指標**
- WMT24 Metrics Shared Task：勝者為 MetricX-24-Hybrid（平均相關 0.721）和 XCOMET（0.719）；QE 版本分別為 0.714 和 0.695。結果確認神經學習式指標優於 BLEU、spBLEU、chrF — [WMT24 Metrics Findings](https://aclanthology.org/2024.wmt-1.2.pdf)
- 對照數字：BLEU 0.589、chrF 0.606（引自 MetaMetrics-MT 表格） — [arXiv 2411.00390](https://arxiv.org/pdf/2411.00390)
- MetricX-24 團隊坦承學習式指標無法可靠偵測漏譯、重複、缺標點、流暢但無關的譯文 — [arXiv 2410.03983](https://arxiv.org/pdf/2410.03983)
- COMET CLI 可以用 `--gpus 0` 在 CPU 上跑；預設模型 `Unbabel/wmt22-comet-da` 基於 XLM-R；`wmt23-cometkiwi-da-xl` 要先在 HF 接受授權 — [GitHub Unbabel/COMET](https://github.com/Unbabel/COMET)
- CometKiwi 是 gated 模型，授權 CC-BY-NC-SA-4.0（鏡像和轉換版標示） — [HF Unbabel/wmt22-cometkiwi-da](https://huggingface.co/Unbabel/wmt22-cometkiwi-da)；[eole-nlp 轉換版](https://huggingface.co/eole-nlp/wmt22-cometkiwi-da-eole)
- sacreBLEU 的 BLEU tokenizer 預設：中文 `zh`、日文 `ja-mecab`、韓文 `ko-mecab`，其他語言 `13a`；chrF 支援字元與詞 n-gram 階數，詞 n-gram 階數設為 2 時即 chrF++；v2.0 起預設輸出 JSON — [PyPI sacrebleu](https://pypi.org/project/sacrebleu)

**LLM-as-judge**
- GEMBA-MQM（Kocmi & Federmann，WMT 2023）用 GPT-4 搭配固定 3-shot prompt 標出 MQM 錯誤區段，不需要參考譯文；系統排名準確度達當時最佳，但作者提醒它依賴閉源模型，學術比較要謹慎 — [ACL Anthology 2023.wmt-1.64](https://aclanthology.org/2023.wmt-1.64)；[arXiv 2310.13988](https://arxiv.org/abs/2310.13988v1)
- GEMBA-MQM V2（WMT25）為了降低變異，每個句段評 10 次，排除 2σ 以外的離群值後以 reciprocal-rank 加權平均；在 WMT24 MQM 上平均相關排名第一（作者自評，非官方排名） — [WMT25 2025.wmt-1.40](https://aclanthology.org/anthology-files/pdf/wmt.real/2025.wmt-1.40.pdf)

### Inferences
- **kikitori 的 CER 正規化建議**（寫成固定版本號的正規化函式並附單元測試）：
  1. NFKC。
  2. 移除 Unicode P／S 類別（含「♪」「w」以外的符號；「ｗ」「草」這類網路用語要決定保留或移除，並寫進規則）。
  3. 移除所有空白。
  4. 另外報一個「假名化 CER」，用 pyopenjtalk 或 MeCab 把參考和輸出都轉成讀音後比較，減少漢字和假名寫法差異的影響。這需要新增依賴，要先問使用者。
  5. 數字（例如 3 和 三）至少做單向統一，或分開報告。
  6. 填充詞（えー、あの）和笑聲（www、（笑））單獨做一組規則，報告「含填充詞」和「去填充詞」兩種 CER。
- **幻覺**：用 MUSAN 純音樂、純噪音和合成噪音做「非語音段」測試集，報告片段級幻覺率，以及每分鐘非語音的插入字元數。有語音的片段另外報 CER 的 insertion 分量。
- **延遲**：桌面應用在本地跑 VAD 加 chunked 推論，至少報告以下項目，並在固定硬體上量 P50/P90：
  - first partial latency（相對於語音起點）；
  - finalization latency（相對於語音結束）；
  - RTF；
  - partial 修訂次數。
- **MT 指標分層**：
  - 主要指標：chrF（字元級，適合 zh-TW，不依賴斷詞）加 sacreBLEU 的 `--tokenize zh` BLEU 當輔助。純 Python，不需要 PyTorch，可以放進主要的評測管線。
  - 神經指標：COMET（`wmt22-comet-da`）、XCOMET 或 MetricX 放在隔離的 uv 環境或另一個 venv 跑。主程式 runtime 不能依賴 PyTorch，評測腳本則可以。
  - 參考譯文以繁體中文撰寫時，BLEU 和 chrF 對繁簡差異非常敏感。要確認 MT 輸出和參考同一字形，必要時先用 OpenCC 轉換再評，並在報告中註明。
- **LLM-as-judge**：GEMBA 原版依賴 GPT-4。若改用本地 LLM 當評審，信度未知，只能當輔助，而且應該像 V2 一樣多次取樣再聚合。

### Gaps
- `Unbabel/wmt22-comet-da`、XCOMET、MetricX-24 的確切授權，本次未核對（XCOMET 一般認為是非商業授權，未驗證）。
- 沒有找到 COMET 或 MetricX 官方的 ONNX 匯出或非 PyTorch 實作。
- sacreBLEU 的 `zh` tokenizer 是否完整涵蓋繁體字的 Unicode 區段，未核對原始碼。
- ESPnet CSJ recipe 的 scoring 正規化細節（例如是否去除 `<sp>` 和填充詞標記），本次未查證。
- 沒有找到針對日語的 kana／kanji-aware CER 標準化工具或社群共識。

## Q5. 小型 benchmark 的統計嚴謹性：bootstrap、配對顯著性檢定、樣本數

### Takeaway
ASR 用 Bisani & Ney（ICASSP 2004）的句段級 bootstrap 估計 WER/CER 信賴區間，比較兩個系統時用「配對 bootstrap 的改善機率」。若同一說話者或同一片段的句子彼此相關，應改用 blockwise（以說話者或片段為單位）bootstrap，否則信賴區間會過窄。MT 可直接用 sacreBLEU 內建的 paired bootstrap 或 approximate randomization。沒有找到權威的「最少樣本數」規範。

### Cited Findings
- Bisani & Ney 2004（ICASSP vol. 1, pp. 409–412）：用 bootstrap 估計 WER 信賴區間，不靠近似假設，結果可直接解讀成 WER；句段級 bootstrap 的標準誤和信賴區間比說話者級窄很多 — [Crossref DOI 10.1109/ICASSP.2004.1326009](https://api.crossref.org/works/10.1109%2FICASSP.2004.1326009)
- 作者的工具頁 bootlog：以 310 個句段比較兩個系統的 WER 差，給出 90% 和 99% bootstrap-t 區間，以及「probability of improvement」（poi）；並說明 NIST 的 P 值依賴高斯假設，poi 則來自 bootstrap 分布 — [bootlog（RWTH）](https://www-i6.informatik.rwth-aachen.de/~bisani/bootlog.html)
- Blockwise bootstrap（2019）：相依句子的 ASR 錯誤可能正相關，逐句 bootstrap 的信賴區間會「much narrower than what they should be」，建議改以說話者等區塊重抽樣 — [arXiv 1912.09508](https://arxiv.org/pdf/1912.09508)
- sacreBLEU 支援 paired bootstrap resampling 和 paired approximate randomization 兩種顯著性檢定 — [PyPI sacrebleu](https://pypi.org/project/sacrebleu)
- Koehn 2004「Statistical Significance Tests for Machine Translation Evaluation」是 MT paired bootstrap 的經典出處（本次未開啟全文） — [ACL Anthology W04-3250](https://aclanthology.org/W04-3250/)

### Inferences
- kikitori 的 benchmark 規模很小（目前只有 6＋6 個片段），應該以「片段」為 block 做 blockwise bootstrap（例如 1,000 次重抽樣），報告 CER 的 95% CI 和兩模型差值的 CI 或 poi，而不是只報點估計。只有 6 個 block 時 CI 會很寬；要讓結論可信，至少要把片段數增加到數十個，或在同一來源中切出更多彼此獨立的片段。這是推論，沒有找到具體的樣本數規範。
- MT 部分可直接用 sacreBLEU 的 `--paired-bs` 處理 chrF 和 BLEU。COMET 的系統比較則可以在隔離的評測環境裡自寫 paired bootstrap，以句段分數重抽樣。
- 噪音混音條件要固定 seed，並對每個 SNR 條件跑多個 seed 平均，避免某一首 BGM 的特性主導結果。

### Gaps
- 沒有找到 WMT 對小型測試集最少句段數的官方建議（WMT24／25 metrics findings 沒有逐段核對這一點）。
- sacreBLEU 顯著性檢定的確切 CLI 旗標（`--paired-bs`、`--paired-ar`、`--paired-bs-n`）沒有從官方 README 逐字核對，使用前要以安裝版本的 `--help` 確認。
- 沒有找到針對 CER（字元層級）bootstrap 是否需要不同處理的專門研究。
