# 即時字幕、即時翻譯字幕與雙語字幕的 HCI／無障礙研究（2023–2026）：kikitori 的 UX 改進依據

> 研究日期：2026-11-08。來源限主要同儕審查會議／期刊（CHI、ASSETS、ICASSP、WMT/EMNLP、SSLA、Language Learning、ReCALL）與大公司官方出版品／官方文件（Google、Microsoft、Apple、Netflix）。2023 年以前的研究只在沒有更新的證據時列為「背景」並標明年份。小型／無法確認 venue 的來源列在最後一節的「排除的來源」表。
> kikitori 現況對照依 `README.zh-TW.md` 與 `docs/specs/ui.md`（字幕視窗、穩定的即時字幕、閱讀節奏、振假名、延遲與音量各節）。
> 標記：【已有】kikitori 已做到；【部分】做了一部分；【沒有】尚未做。

## 問題 1：字幕穩定性 vs 延遲（re-translation 閃爍、逐步顯示、遮蔽不穩定字詞）

### Takeaway
目前最直接的證據（Google, CHI EA 2023, N=123）顯示：「只在尾端追加、不讓中段修改造成換行跳動、新字淡入」的穩定化顯示，顯著降低分心與疲勞、提高閱讀舒適度；而在即時翻譯字幕上，閃爍對理解的影響小於翻譯品質本身，且懂一點來源語言的使用者偏好低延遲勝過少閃爍（WMT 2022, N=14，樣本小）。kikitori 的「穩定前綴＋淡色尾巴」方向正確，可再補「避免版面重排」「新字淡入」與「即時譯文顯示策略可選」。

### Cited Findings
- **Liu, Zhang, Ferrer, Xu, Bahirwani, Smus, Olwal, Du（Google），CHI 2023 Extended Abstracts（2023-04；Google Research blog 2023-08）**：群眾外包使用者研究 N=123，每人看 20 段 30 秒 YouTube 片段（≥90% 語音），比較 4 種呈現：(1) 原始 ASR、(2) 信心門檻（interim 結果信心 >0.85 才顯示）、(3) 穩定化（對齊＋合併）、(4) 穩定化＋平滑動畫（捲動／新 token 淡入）。(4) 在 6 個題項中有 5 項顯著較佳（Mann-Whitney U, p<0.01）：較舒適、較好讀、較不分心、較不疲勞、較少「體驗受損」。— [Google Research blog](https://research.google/blog/modeling-and-improving-text-stability-in-live-captions)；[Google Research 出版頁](https://research.google/pubs/modeling-and-improving-text-stability-in-live-captions/)
- 同研究的穩定化規則：尾端新增直接加入；**中段的新增／刪除只有在不打亂既有換行版面時才套用（刻意以穩定性優先於正確性）**；重新辨識（專有名詞、縮寫）只有在句向量相似度 <0.85（語意真的不同）且不產生新換行時才套用；新 token 以捲動與淡入動畫出現。— [Google Research blog](https://research.google/blog/modeling-and-improving-text-stability-in-live-captions)
- 同研究提出以畫面亮度差＋DFT 計算的視覺閃爍指標，與使用者評分顯著相關（p<0.001，係數約 0.3）：分心 0.33、疲勞 0.36、體驗受損 0.31、舒適 −0.29、易讀 −0.31。值得注意：單純「信心門檻」策略不是最佳者。— [Google Research blog](https://research.google/blog/modeling-and-improving-text-stability-in-live-captions)
- **Javorský, Macháček, Bojar，WMT 2022（2022-12，背景）**：14 位使用者看 2 小時德語紀錄片／演講，配捷克語即時翻譯字幕，以連續評分（按鍵）與事實題測理解。結論：**字幕版面與閃爍對理解的影響，比機器翻譯品質本身與個人語言能力小**；與「只顯示最終譯文（無閃爍但延遲高）」相比，具中高程度來源語知識者用低延遲字幕理解較好、且偏好低延遲勝過少重譯；不懂德語者無閃爍時理解略高（34% vs 30%）但不顯著。— [ACL Anthology WMT 2022](https://aclweb.org/anthology/2022.wmt-1.9.pdf)；[arXiv 2203.02458](https://arxiv.org/pdf/2203.02458)
- **Arivazhagan et al.（Google），ICASSP 2020（背景）**：re-translation（每次對成長中的來源整句重譯）延遲低、最終品質高但輸出不穩定；以簡單的推論端啟發式（論文含 biased beam search 等）可把不穩定性降低最多約 100 倍。後續 Google 研究（Arivazhagan et al. 2020）指出「低 erasure 的 re-translation」品質不輸當時最好的 streaming 翻譯。— [arXiv 1912.03393](https://arxiv.org/pdf/1912.03393)；[Google Research](https://research.google/pubs/pub49011)；[arXiv 2004.03643](https://arxiv.org/pdf/2004.03643)
- **Thompson, Waller, …, Kushalnagar, Vogler，ASSETS 2026（已接受；arXiv 2026-09）**：美國線上調查，216 位有效 DHH 參與者、302 份回應、4,832 個資料點、70 段直播電視片段；4 種條件：電視字幕原始延遲 7–12 秒、電視字幕對齊音訊、ASR 對齊音訊、ASR 延遲 2 秒。**字幕延遲顯著影響觀看體驗（尤其 7–12 秒）**；電視字幕與 ASR 字幕評分相近；WER／ACE2／NER 等指標與 ASR 字幕評分的相關明顯弱於電視字幕（指標不是技術中立的）。— [arXiv 2609.11408](https://arxiv.org/abs/2609.11408)

### Inferences
- 【已有】穩定前綴正常、尾巴淡色；完整字幕原地取代即時字幕；即時譯文淡色（`ui.md`「穩定的即時字幕」「字幕視窗」）。這與 Google 的 (3) 類似。
- 【沒有】**避免版面重排**：Google 的關鍵規則是「中段改字若造成換行跳動就延後套用」。kikitori 的即時字幕每次更新可能改變換行位置。可行做法：即時字幕期間讓已顯示的行寬只增不減（例如以 `min-height`／保留已換行位置），或在 `stable.ts` 加「只有不改變已穩定部分的行數時才套用尾巴更新」；完整字幕到達時才允許重排。純前端，可寫成純函式測試。
- 【沒有】**新字淡入動畫**：新增的 token 以 150–250 ms 淡入（已有 `prefers-reduced-motion` 處理淡出，可沿用）。Google 的 (4) 優於 (3) 的差別就是動畫。
- 【部分】**即時譯文顯示策略**：目前即時譯文一律淡色顯示。依 Javorský 2022（N=14，需保守看待），可在設定加「即時譯文：顯示／只顯示已穩定部分對應的譯文／不顯示（只等完整譯文）」。懂一點日語的觀眾（kikitori 主要使用者）多半偏好低延遲，預設維持顯示；不懂來源語者可選「不顯示即時譯文」。Arivazhagan 的「遮住重譯結果最後 k 個 token」（mask-k 類策略）可作為「只顯示穩定部分」的實作參考（引擎端）。
- 【已有】延遲顯示（說完到完整字幕中位數）；實測完整字幕約 0.92 秒、即時字幕約 0.31 秒，遠低於 Thompson 2026 認為傷害體驗的 7–12 秒電視延遲。可在說明文件引用此研究說明「先快後準」設計的理由。
- 優先序推論：Javorský 2022 顯示翻譯品質與個人能力比版面更影響理解 → UX 改進不應排擠翻譯品質（術語表、模型）的投入。

### Gaps
- 沒有找到 2023–2026 主要 venue 針對「即時**翻譯**字幕」的閃爍／延遲大樣本使用者研究；Javorský 2022 只有 14 人。
- Google CHI EA 2023 用的是英文 ASR 字幕；對日文／中文（無空白語言、振假名）的穩定化效果沒有證據。
- 沒有找到「淡色顯示不穩定尾巴」本身（相對於隱藏尾巴）的對照研究。

## 問題 2：雙語字幕版面（理解、語言學習、位置、閱讀速度、行數；日語學習者）

### Takeaway
主要期刊的眼動研究顯示：雙語字幕對理解的幫助與只看母語字幕相同、優於只看外語字幕或無字幕；學習者大部分時間讀母語那行。對字彙學習，雙語字幕在「意義」上較好、但在「字形辨識」上不如只看外語字幕。官方字幕規範（Netflix 日文、Xbox XAG）給出可直接套用的數字：日文字幕每秒 4 字、每行 13 全形字、最多 2 行、每則至少 0.5 秒；振假名只標第一次出現。

### Cited Findings
- **Wang & Pellicer-Sánchez，Studies in Second Language Acquisition 45(4)（2023；線上 2022）**：眼動研究，112 位中國英語學習者，4 種條件（雙語字幕、英文字幕〔captions〕、中文字幕、無字幕）看英語紀錄片。**雙語字幕對理解與 L1 字幕一樣有效，兩者都優於 captions 與無字幕**；雙語組在 L1 行花的時間顯著多於 L2 行；雙語中 L2 行的處理時間顯著少於單獨的 captions；處理時間與理解沒有顯著關係。— [Cambridge Core（SSLA）](https://cambridge.org/core/journals/studies-in-second-language-acquisition/article/examining-the-effectiveness-of-bilingual-subtitles-for-comprehension-an-eyetracking-study/D3E6D4CDE5C5023CE085E95CA4D18ACE)
- **Wang & Pellicer-Sánchez，Language Learning（2022，背景）**：同樣 112 位學習者、4 種條件的附帶字彙學習研究：雙語字幕在「意義辨識」優於 captions、在「意義回想」優於 L1 字幕；但**在「字形辨識」上不如 captions**；學習者花較多時間讀 L1 行與生字的 L1 譯文。— [UCL Discovery 全文](https://discovery.ucl.ac.uk/10144681/7/Pellicer%20Sanchez_Incidental%20Vocabulary%20Learning%20from%20Bilingual%20Subtitled%20Viewing-%20An%20Eye-Tracking%20Study_AOP.pdf)
- **Lo，ReCALL 36(2)（2024）**：比較看雙語字幕影片後的「立即重複」與「間隔重複」；看雙語字幕的學習者在意義辨識與回想的字彙增益大於 L1 字幕、L2 captions 與無字幕組（樣本數未能從摘要取得）。— [Cambridge Core（ReCALL）](https://core-prod.cambridgecore.org/core/services/aop-cambridge-core/content/view/CD13EC929B3F7B7EEEFAC0E367325770/S0958344024000053a.pdf/vocabulary_learning_through_viewing_dualsubtitled_videos_immediate_repetition_versus_spaced_repetition_as_an_enhancement_strategy.pdf)
- **Kovacs & Miller，CHI 2014（背景）**：互動式「Smart Subtitles」（點字查詞、可隨時看譯文）與中英雙語字幕比較，中級中文學習者的試驗研究：Smart Subtitles 組在觀看後字彙測驗正確定義的新字超過 2 倍；觀看時間、享受度、理解程度相近。— [CHI 2014 論文 PDF](https://up.csail.mit.edu/other-pubs/chi2014-smartsubs.pdf)
- **Netflix Japanese Timed Text Style Guide（官方，2026-11 查閱）**：一般字幕每秒最多 4 字（SDH 7 字）；橫式每行最多 13 全形字（SDH 16）；最多 2 行；每則至少 500 ms；全形字、空白、標點算 1，半形算 0.5；**振假名（ruby）只標該詞第一次出現**（兒童內容才每次都標）、整個複合詞一起標、單行橫式標在上方、兩行橫式的第二行標在下方；不使用「。」「、」而以空白取代。— [Netflix Partner Help](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)
- **Xbox Accessibility Guideline 104（Microsoft 官方，2026-06 更新）**：每行避免超過 40 字元；同時最多 2 行（例外 3 行）；可放大到預設最小字級的 200%；至少一種 sans-serif 字型；背景顏色可調、背景不透明度 0–100%；放在畫面下方避免遮住重要 UI；**設定時提供即時預覽**（最好在真實情境中）。— [Microsoft Learn XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)
- **Gorman, Crabb, Armstrong，CHI 2021（背景）**：「Adaptive Subtitles」讓觀眾即時選擇哪些說話者顯示字幕，N=19：細粒度控制加上結構化的入門說明，讓觀眾能做出知情的取捨。— [University of Dundee](https://discovery.dundee.ac.uk/en/publications/adaptive-subtitles-preferences-and-trade-offs-in-real-time-media-/)

### Inferences
- 【已有】譯文在原文下方、原文字級 ×0.62、最多 1–3 句、背景不透明度可調、位置上／下、每句至少顯示 1500 ms、閒置淡出。行數上限 3 與 XAG「2 行、例外 3 行」一致。
- 【沒有】**學習模式（版面預設組）**：Wang & Pellicer-Sánchez 顯示學習者自然把注意力放在 L1 行、而 L2 行處理時間被壓縮，且雙語字幕不利字形學習 → 為日語學習者提供「原文為主」版面：原文用主字級＋振假名、譯文縮小／淡化，並提供「譯文順序：上／下」與「原文／譯文字級比例」設定。這是純前端設定（`OverlayStyle` 加欄位）。
- 【沒有】**按住快速鍵才顯示譯文（peek）**：Smart Subtitles（CHI 2014）支持「需要時才看譯文」勝過一直顯示雙語；字幕視窗是滑鼠穿透的，hover 不可行，可用全域快速鍵按住顯示（既有 Ctrl+Alt+J 是切換，可另加「暫時顯示」）。證據年代較舊，列為中低優先。
- 【沒有】**字幕紀錄頁點字查詞**：README 列為「不支援」。Smart Subtitles 的證據支持在**字幕紀錄頁**（非字幕視窗）做點詞看讀音／譯文；可重用 SudachiDict 已有的讀音。
- 【部分】**依閱讀速度決定最短顯示時間**：目前是固定 `min_display_ms`。可改成 `max(min_display_ms, 字數 / cps)`，cps 預設依字幕語言（日文 4、Netflix 繁中／簡中指南的數字需另查證），翻譯字幕以譯文字數計；仍保留「等待不得累積成延遲」的既有規則（`rhythm.ts`）。
- 【部分】**振假名只標第一次出現**：目前每個漢字都標。依 Netflix 規範，加選項「振假名：全部／每個 session 第一次出現」，可降低視覺密度；仍保留「全部」給初學者（Netflix 兒童內容也是每次都標）。
- 【沒有】**每行字數上限／換行寬度**：XAG 建議 ≤40 字元、Netflix 日文 13 全形字。kikitori 依視窗寬度自動換行；可加「最大行寬（em）」讓長句換成較短的兩行而不是一條橫跨螢幕的長行。
- 【沒有】**設定頁即時預覽**：XAG 104 明確要求。設定頁改字級／背景／行數時，在設定頁或字幕視窗顯示範例雙語句（含振假名、淡色尾巴）。

### Gaps
- 沒有找到主要 venue 中「日語學習者 × 雙語字幕」的眼動或實驗研究（找到的 Peng, Liang & Sasao 刊於日本國內期刊、Hayabusa 是 JALT 研討會發表，列入排除表）。
- 沒有找到比較「原文在上 vs 譯文在上」的對照研究；SSLA 2023 摘要未說明哪一行在上。
- 沒有找到主要 venue 的振假名（ruby）對閱讀速度／學習效果的實驗研究。
- 繁中、簡中、韓文等字幕語言的閱讀速度數字本次只從搜尋摘要看到（Netflix 韓文 12 cps、簡中 9 cps），未逐頁核對，實作前需查證。

## 問題 3：不確定性／錯誤的呈現、使用者修正、說話者標示、非語音資訊、情緒語氣

### Takeaway
「標出 ASR 低信心字詞」被一再測試但不推薦預設開啟：DHH 使用者口頭上想知道，但實際覺得分心、且沒有提升理解；Google Live Transcribe 也因此拿掉信心著色。說話者標示（換人時標名字、顏色＋文字）與非語音標籤（音樂、笑聲、掌聲）有明確需求與官方規範支持；情緒字型（顏色＋大小）能傳達情緒但略降易讀性，研究結論一致是「做成可自訂、預設關閉」。

### Cited Findings
- **Berke, Caulfield, Huenerfauth，ASSETS 2017（背景）**：研究一對一會議中即時 ASR 字幕是否、如何顯示字詞信心；DHH 使用者偏好熟悉、不顯示信心的字幕樣式，雖然對顯示信心有興趣，但擔心字形變化令人分心。（二手摘要稱先測 12 種標記樣式〔21 人〕、再比較斜體／底線／黃色粗體等 4 條件〔107 人〕；本次無法取得原文核對樣本數。）— [ACM DOI 10.1145/3132525.3132541](https://doi.org/10.1145/3132525.3132541)
- **Google Live Transcribe（Google Research blog，2019-02，背景）**：團隊測試過逐詞／逐片語的信心著色（黃＝高、綠＝中、藍＝低、白＝等待上下文的新字），研究發現「讓使用者分心、沒有提供對話價值」，最後不顯示信心，改以純文字呈現；另加「噪音指示器」：內圈顯示背景噪音底、外圈顯示講者聲音接收程度，讓使用者即時調整手機位置。與 Gallaudet University 合作（未公開樣本數）。— [Google Research blog](https://research.google/blog/real-time-continuous-transcription-with-live-transcribe/)
- **CARTGPT（Wu et al.，ASSETS 2024）**：以 LLM 結合有錯的人工 CART 字幕與 ASR 逐字稿，即時產生修正後字幕；初步評估只有 3 位 DHH 使用者。— [ASSETS 2024 論文 PDF](https://soundability.eecs.umich.edu/img/portfolio/Wu_CARTGPT_ASSETS2024.pdf)
- **非專業者即時協作修正 ASR（CHI 2025 Extended Abstracts）**：使用者研究＋DHH 焦點團體，發現即時編輯修正的工作負擔很重。— [arXiv 2503.15120](https://arxiv.org/pdf/2503.15120)
- **SpeechCompass（Dementyev, Kanevsky, Yang, Parvaix, Lai, Olwal），CHI 2025**：大型調查 n=263 顯示語音轉文字在群體對話中難用，因為看不出誰在說、從哪個方向；另一份調查 n=494 用於設計；8 位常用行動字幕的使用者在群體對話中評估 5 種視覺化：說話者分離（diarization）與方向視覺化的價值在參與者間一致。— [arXiv 2502.08848](https://arxiv.org/abs/2502.08848)
- **Haptic-Captioning（Wang, Li, Chelladurai, Dannels, Oh, Peiris），CHI 2023**：以手腕震動輔助即時字幕的說話者標示；動機是「插入名字、標示說話者」在即時字幕中難以做到（定量結果本次未取得）。— [dblp CHI 2023](https://dblp.uni-trier.de/rec/conf/chi/WangLCDOP23.html)
- **McDonnell et al.，CHI 2023**：與 DHH 與聽人混合小組共同設計視訊會議字幕工具，發現仍持續需要說話者標示與字幕自訂等基本功能（樣本數本次未取得）。— [CHI 2023 論文 PDF](https://makeabilitylab.cs.washington.edu/media/publications/McDonnell_EasierOrHarderDependingOnWhoTheHearingPersonIsCodesigningVideoconferencingToolsForSmallGroupsWithMixedHearingStatus_CHI2023.pdf)
- **XAG 104（Microsoft 官方）**：每次換說話者時在字幕行標名字，同一人連續說話不必重複（停頓超過 1–2 分鐘才重標）；可用顏色區分說話者但必須同時有文字等其他方式；字幕（captions）應包含非語音資訊，如 [音樂名稱]、[狗叫]、語氣 [sarcasm]、來源 [over the radio]；不同類型的字幕資訊（主要對話、背景對話、環境音）可分別開關。— [Microsoft Learn XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)
- **Netflix 日文規範（官方）**：說話者標示只在「畫面上看不出是誰說」時使用，用全形括號（）；不重複標示同一說話者；即興節目用客觀標示（男性）（女性），重複時加數字（男性 1）；說話者與音效同時出現時合併為（マイクの泣き声）；音效描述聲音本身而非畫面。— [Netflix Partner Help](https://partnerhelp.netflixstudios.com/hc/en-us/articles/215767517)
- **Google Expressive Captions（Google 官方 blog 2024-12；Android 說明頁）**：Live Caption 在裝置端以 AI 傳達語氣、音量、環境音與人聲：大寫表示高強度（"HAPPY BIRTHDAY!"）、拉長字表示強調（"Amaaazing!"）、標示掌聲／歡呼等環境音與嘆氣、咕噥、倒抽氣等人聲；Android 15 再加 10 種標籤（清喉嚨、吹口哨、耳語）；三個子開關可個別關閉（加樣式、顯示聲音標籤、拉長字）。— [Google blog](https://blog.google/products/android/google-android-expressive-captions)；[Android 說明](https://support.google.com/accessibility/android/answer/9350862?hl=en)
- **Pataca, Watkins, Peiris, Lee, Huenerfauth，CHI 2023**：先訪談 8 位 DHH 使用者，發現自動字幕缺乏情感深度、造成難以解讀的歧義與單調；再由 16 位 DHH 參與者比較韻律、情緒、兩者合併與一般字幕。情緒樣式（valence→紅白綠顏色、arousal→字級）在傳達情緒與強調上優於一般字幕，易讀性只有中度下降；只顯示韻律的樣式**沒有**比一般字幕更能表達強調。— [NJIT Digital Commons](https://digitalcommons.njit.edu/fac_pubs/1776)；[NSF PAR 全文](https://par.nsf.gov/servlets/purl/10420024)
- **Caption Royale（Pataca, Hassan et al.），CHI 2024**：3 個研究、共 39 位 DHH 參與者，以淘汰賽方式把 72 種情緒字幕樣式（顏色、粗細、字級等）篩到 2 種最佳；參與者偏好依使用字幕的方式而異，**提供自訂是最好的做法**。（細節來自 NSF PAR 摘要。）— [dblp CHI 2024](https://dblp1.uni-trier.de/rec/conf/chi/PatacaHTPH24.html)；[NSF PAR](https://par.nsf.gov/biblio/10521039)
- **May, Ohshiro, Dang, Sridhar, Pai, Fuentes, Lee, Cartwright，CHI 2024「Unspoken Sound」**：建立超過 71.5 萬支 YouTube 影片的資料集，分析非語音資訊（NSI）字幕的現況與歷史。（後續論文稱只有約 4% 影片有 NSI 字幕且比例下降，但該數字出自非本政策 venue，見排除表。）— [NJIT Digital Commons](https://digitalcommons.njit.edu/fac_pubs/429)
- **Windows Live Captions（Microsoft 官方說明）**：只辨識語音，音樂、掌聲與歌詞不能可靠偵測。— [Microsoft Support](https://support.microsoft.com/en-us/windows/use-live-captions-to-better-understand-audio-b52da59c-14b8-4031-aeeb-f6a47e6055df)

### Inferences
- 【沒有，且不建議預設做】**字幕視窗顯示 ASR 信心**：Berke 2017 與 Google 2019 一致指出分心大於好處；Google CHI EA 2023 也顯示「信心門檻」不是最佳顯示策略。kikitori 的淡色是「穩定性」不是「信心」，維持即可。若要做，只放在**字幕紀錄頁**、預設關閉（例如淡底線標出 Whisper 低 `avg_logprob` 的句子），方便使用者事後找錯字加入術語表。
- 【部分】**從字幕紀錄頁一鍵把錯字加入術語表**：既有術語表＋CHI EA 2025「即時編輯負擔重」→ 修正應發生在事後、低負擔的地方：在字幕紀錄頁選取原文片段 →「加入術語表」預填原文，使用者只填譯文。不必做即時協作修正。
- 【部分】**口譯模式的說話者標示**：kikitori 已知每句是「我」還是「對方」（依語言方向），可直接在兩方字幕前加文字標示＋顏色（例如「你：」「對方：」，顏色不是唯一線索，符合 XAG）。成本低、不需要 diarization。SpeechCompass（CHI 2025, n=263/494）支持「誰在說」是群體對話字幕的核心需求。
- 【沒有】**直播多人連動的說話者分離**：README 列為不支援。證據支持需求，但本機 diarization 會增加 GPU／延遲負擔；建議維持「先不做」，等口譯模式標示上線後再評估。若做，依 Netflix／XAG：只在換人時標示、用（話者 1）類客觀標示。
- 【沒有】**非語音標籤（[♪ 音樂]、[笑聲]、[掌聲]）**：kikitori 的已知限制是歌聲穿過 VAD 而產生怪字幕。依 XAG／Expressive Captions，改為顯示「（♪ 音樂／歌唱中）」標籤取代幻覺文字，對 VTuber 歌回很有價值。需要本機音訊分類模型（例如 AudioSet 類的小型 ONNX 模型）→ 新依賴，需先問使用者；可做成子開關，與 XAG「各類字幕資訊可分別開關」一致。
- 【沒有】**音量強度表現**：kikitori 已有 `level_dbfs`；Expressive Captions 用大寫表現強度，但日文／中文沒有大小寫 → 可用粗體或略放大表示「大聲喊叫」的句子。Pataca 2023 顯示情緒樣式有中度易讀性代價、Caption Royale 顯示偏好分歧 → **預設關閉、做成選項**，優先度低。
- 【已有】**噪音／音量回饋**：kikitori 狀態列音量表與「15 秒沒有聲音」提醒，對應 Live Transcribe 的噪音指示器。【部分】可再區分「人聲」與「背景」（VAD 判定為語音時的音量 vs 非語音時的音量），提示「BGM 太大聲」——Live Transcribe 的雙圈設計即此概念。

### Gaps
- 沒有找到 2023–2026 主要 venue 專門檢驗「ASR 信心顯示」的新研究；最新的仍是 2017–2019 的工作。
- 沒有找到關於「**翻譯**品質不確定性」如何在字幕上呈現的使用者研究（只有 ASR 信心的研究）。
- CHI EA 2023 Hassan et al.「emotive captions 設計空間」只在搜尋摘要出現，未取得內容。
- Haptic-Captioning、McDonnell 2023 的定量結果與樣本數本次未取得。

## 問題 4：DHH 使用者對即時字幕的需求（含遊戲與直播）

### Takeaway
DHH 研究的共同結論是：延遲要低、要能分辨說話者、要有非語音資訊、而且一定要可自訂；直播情境中「沒有即時字幕」本身是 DHH 直播主的主要障礙。kikitori 的本機低延遲字幕與 OBS 文字檔輸出直接對應這些需求，可再補非語音標籤與說話者標示讓 OBS 輸出更完整。

### Cited Findings
- **Sparkling Silence（CHI 2023）**：訪談 15 位使用多種平台的中國 DHH 直播主；主要障礙是缺乏即時字幕、手語視窗太小、手語被誤解；許多人希望直播能打破對 DHH 的刻板印象。— [arXiv 2302.14682](https://arxiv.org/abs/2302.14682)
- **Thompson et al.，ASSETS 2026**：DHH 觀眾（N=216）認為延遲顯著影響直播電視字幕體驗；常用字幕指標對 ASR 字幕的預測力較弱。— [arXiv 2609.11408](https://arxiv.org/abs/2609.11408)
- **Pataca et al. CHI 2023（N=8 訪談＋16 評估）與 Caption Royale CHI 2024（N=39）**：自動字幕缺乏情感資訊；偏好因人而異，應提供自訂。— [NJIT](https://digitalcommons.njit.edu/fac_pubs/1776)；[NSF PAR](https://par.nsf.gov/biblio/10521039)
- **SpeechCompass CHI 2025（調查 n=263、n=494）**：群體對話字幕最大困難是看不出說話者與方向。— [arXiv 2502.08848](https://arxiv.org/abs/2502.08848)
- **XAG 104（Microsoft 官方）**：字幕應在錄影、截圖與使用者生成內容（UGC）中保留，讓其他觀眾可選擇顯示；完整逐字稿應可取得；聽人在吵雜環境或關聲音遊玩時也受益。— [Microsoft Learn XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)
- **Berke et al. ASSETS 2017（背景）**：DHH 使用者偏好熟悉的字幕樣式。— [ACM DOI](https://doi.org/10.1145/3132525.3132541)

### Inferences
- 【已有】OBS 文字檔輸出讓直播主把字幕燒進直播畫面 → 符合 XAG「字幕保留在 UGC 中」與 Sparkling Silence「缺乏即時字幕」的需求。可在 README 把「DHH 觀眾／直播主」列為使用情境之一。
- 【已有】匯出完整逐字稿（SRT/VTT/TXT/CSV）→ 符合 XAG「提供完整逐字稿」。
- 【沒有】**OBS 輸出帶標籤**：若實作非語音標籤與口譯模式說話者標示，OBS 文字檔與匯出檔也應帶上（例如 SRT 中的（♪）），並可分別開關。
- 【部分】**同語言字幕（captions）模式**：「只顯示原文」已讓 kikitori 可當單語即時字幕用，對 DHH 使用者即是 Windows／Apple Live Captions 的本機替代品，且支援 Windows Live Captions 不支援的語言組合。

### Gaps
- 沒有找到主要 venue 針對「DHH 觀眾看遊戲直播（Twitch/YouTube Live）時使用第三方即時字幕」的研究；遊戲字幕偏好的 2024 調查（Aljedaani et al., 73 人）venue 無法確認，列入排除表。
- 沒有找到 DHH 使用者對「即時翻譯字幕」（跨語言）的研究。

## 問題 5：主流產品的即時字幕功能（Google、Microsoft、Apple）

### Takeaway
三家主流產品都已把「裝置端處理、可移動／可縮放視窗、字型與顏色自訂、暫停、麥克風／系統音切換、髒話過濾、可展開更多行」做成標準功能；Windows 另有「停靠在螢幕上緣／下緣並保留空間」，Apple 有「沒聲音時自動隱藏／保持在畫面上」與「恢復預設位置」，Google 有「雙擊從 2 行展開到 12 行」與「詢問是否翻譯／自動翻譯」。kikitori 已具備多數項目，缺的是暫停、恢復預設位置、快速展開歷史、停靠模式與髒話過濾。

### Cited Findings
- **Windows Live Captions（Microsoft 官方說明，2026-11 查閱）**：位置可選「螢幕上方／下方」（停靠在畫面邊緣的保留空間，不遮住其他 App）或「浮動在畫面上」；可拖大視窗顯示更多行；內建樣式與自訂樣式；過濾髒話；「包含麥克風音訊」每次啟動預設關閉，系統音與麥克風同時有聲時只辨識系統音；Copilot+ PC（24H2）可把 40+ 種語言翻成英文、27 種翻成簡中；全部裝置端處理、字幕不儲存、可離線；只辨識語音，不辨識音樂、掌聲與歌詞；快速鍵 Win+Ctrl+L。— [Microsoft Support](https://support.microsoft.com/en-us/windows/use-live-captions-to-better-understand-audio-b52da59c-14b8-4031-aeeb-f6a47e6055df)
- **Apple Live Captions（macOS 官方說明，2026-11 查閱）**：自訂字型、字級、字色、背景色；可暫停（不必關閉功能）；在系統音與麥克風間切換；Type to Speak（輸入文字讓 Mac 念出來）；可縮放、移動、從選單列「恢復預設位置」；**沒有聲音時自動隱藏，可選「保持在畫面上」**；可自訂快速鍵；裝置端處理；說明頁提醒準確度可能不一、不應用於高風險或緊急情境；未提到儲存或匯出逐字稿。— [Apple Support](https://support.apple.com/guide/mac-help/get-live-captions-of-spoken-audio-mchldd11f4fd/mac)
- **Apple FaceTime Live Captions（2022 發表報導，非官方文件）**：以圖示與名字標示說話者。— 僅見媒體報導，列入排除表。
- **Google Live Caption（Android 官方說明，2026-11 查閱）**：拖曳移動字幕框；拖出畫面下緣即隱藏並關閉；**雙擊從 2 行展開到 12 行**；拖曳下緣調整大小；隱藏／顯示髒話；顯示聲音標籤；「建議偵測到的語言」；翻譯偏好「詢問是否翻譯／自動翻譯／不翻譯」（Pixel 6+）；通話中可打字讓系統念出；全部本機處理、不儲存、不需網路；不適合多人通話、不辨識音樂。— [Android 說明](https://support.google.com/accessibility/android/answer/9350862?hl=en)
- **Google Expressive Captions（2024-12）**：見問題 3。— [Google blog](https://blog.google/products/android/google-android-expressive-captions)
- **Google Live Transcribe（2019，背景）**：不顯示信心、加噪音指示器。— [Google Research blog](https://research.google/blog/real-time-continuous-transcription-with-live-transcribe/)

### Inferences
- 【已有】透明置頂、可移動縮放、滑鼠穿透、字級／背景可調、本機處理、系統音／麥克風（兩種模式）、快速鍵、閒置淡出（≈ Apple 的自動隱藏）、匯出逐字稿（Apple 沒有，kikitori 較強）。
- 【沒有】**暫停**：Apple 有。kikitori 可加「暫停字幕」快速鍵：凍結畫面、引擎繼續辨識並寫入字幕紀錄（或真的暫停擷取，需決定語意）——適合想停下來讀完一句的學習者。
- 【沒有】**恢復預設位置**：Apple 有。kikitori 用 `tauri-plugin-window-state` 記位置，若視窗被拖到已拔掉的螢幕外會找不到；在主視窗加「重設字幕視窗位置」按鈕，低成本高價值。
- 【沒有】**快速展開歷史**：Google 雙擊 2→12 行。kikitori 字幕視窗最多 3 句；可加快速鍵暫時展開最近 N 句（例如 10 句）並暫停淡出，再按一次收回——解決「剛剛那句沒看到」而不必切到主視窗。
- 【沒有】**停靠模式**：Windows 的「上方／下方」保留螢幕空間不遮住內容。Windows 需 AppBar API（`SHAppBarMessage`），Tauri 需平台特定 Rust 程式碼；macOS／Linux 無對等機制。中低優先。
- 【沒有】**髒話過濾**：三家都有。VTuber 情境需求不明，可做成預設關閉的選項；日文髒話清單需人工整理。低優先。
- 【已有】「沒聲音」提醒；【沒有】Apple 的「保持在畫面上」對應 kikitori `idle_clear_secs=0`，已可設定，只是名稱不同——可在設定文案中說明「0 = 一直顯示」。
- 【沒有】**翻譯「詢問／自動」**：kikitori 以「收聽語言＝自動偵測」＋固定字幕語言運作；Google 的「偵測到其他語言時詢問是否翻譯」可在自動偵測模式下提示，但證據只是產品功能，非使用者研究。低優先。

### Gaps
- 三家公司都沒有公開 2023–2026 的 Live Captions 使用者研究數據（樣本數、偏好比例）；只有產品文件與發表 blog。Google 的 Text Stability（CHI EA 2023）是唯一找到的官方研究論文。
- Apple ML Research 沒有找到 Live Captions 的 UX 研究；Apple FaceTime 說話者標示只見媒體報導。
- Microsoft Teams 即時字幕／翻譯字幕的官方研究未在本次查詢。

## 問題 6：遊戲與直播情境的一眼可讀（glanceability）、疊加層設計與自訂模式

### Takeaway
遊戲字幕官方規範（XAG 104）與 DHH 研究一致：可調背景與不透明度、字級可放大到 200%、行短（≤40 字元）、最多 2 行、放在不擋重要 UI 的位置、提供即時預覽、各類字幕資訊可分別開關，以及偏好分歧所以要可自訂（Caption Royale）；細粒度控制需配上清楚的入門說明（Adaptive Subtitles）。

### Cited Findings
- **XAG 104（Microsoft 官方）**：大字預設（字幕只短暫出現，應有比選單更大的預設字級）、可放大到 200%、字距不可太緊、≤40 字元／行、最多 2 行（例外 3 行）、大小寫混用、sans-serif、可設背景色與 0–100% 不透明度、放在下方避免遮住重要 UI、提供即時預覽、預設採用平台層級的字幕設定、各類字幕資訊可分別開關、設定選項要有清楚說明（例如 The Outer Worlds 對「開／精簡／關」的說明）。— [Microsoft Learn XAG 104](https://learn.microsoft.com/en-us/gaming/accessibility/xbox-accessibility-guidelines/104)
- **Caption Royale（CHI 2024，N=39 DHH）**：偏好因使用方式而異，提供自訂最好。— [NSF PAR](https://par.nsf.gov/biblio/10521039)
- **Gorman, Crabb, Armstrong（CHI 2021，N=19，背景）**：細粒度控制＋結構化入門讓觀眾能做知情取捨。— [University of Dundee](https://discovery.dundee.ac.uk/en/publications/adaptive-subtitles-preferences-and-trade-offs-in-real-time-media-/)
- **Google Text Stability（CHI EA 2023，N=123）**：畫面閃爍量與分心、疲勞顯著相關 → 疊加層越穩定越不干擾主要內容。— [Google Research blog](https://research.google/blog/modeling-and-improving-text-stability-in-live-captions)
- **Windows Live Captions（官方）**：停靠模式不遮住其他 App；浮動模式可移動。— [Microsoft Support](https://support.microsoft.com/en-us/windows/use-live-captions-to-better-understand-audio-b52da59c-14b8-4031-aeeb-f6a47e6055df)

### Inferences
- 【已有】背景不透明度（含 0＝無底板＋描邊陰影）、字級、位置上／下、行數 1–3、最新一句最醒目、較舊句子略淡（降低周邊視覺干擾）。
- 【沒有】**設定即時預覽**（XAG 明確要求）：在設定頁改外觀時，字幕視窗（或設定頁內的模擬框）顯示範例句，包含原文、譯文、淡色尾巴、振假名。中優先、純前端。
- 【沒有】**外觀預設組**：「VTuber 觀看（預設）」「日語學習（原文為主＋振假名＋譯文縮小）」「大字／高對比（不透明底板、字級 ×1.5）」「直播疊加（給 OBS 擷取用）」。每個預設附一行說明它改了什麼（XAG 的選項說明、Gorman 的入門說明）。
- 【部分】**字型選擇**：XAG 要求至少一種 sans-serif；kikitori 目前未提供字型選擇。可提供系統字型清單或幾種 CJK 友善字型（不需新依賴）。低–中優先。
- 【部分】**字級上限**：檢查 kikitori 字級範圍是否能達預設的 200%（XAG）。
- 【沒有】**行寬上限**：見問題 2（≤40 字元／Netflix 日文 13 全形字）。

### Gaps
- 沒有找到主要 venue 的「桌面浮動字幕疊加在遊戲直播畫面上」的眼動或任務研究；直播畫面（聊天室、超級留言、遊戲 HUD）與字幕互相遮擋的最佳位置沒有直接證據（Al Amin et al. 2023 的直播新聞眼動研究 venue 較小，列入排除表）。
- 沒有找到「動態字幕位置（跟著說話者或避開畫面重點）」在桌面情境的 2023–2026 主要 venue 研究；AR 情境的研究 venue 無法確認。

### 口譯模式（雙向）補充證據
- **Ouvia（Attanasio, Savoldi, Chechelnitsky, Negri, Carpuat, Sap, Martins），EMNLP 2026（已接受）**：一對一溝通情境（英語→葡萄牙語），醫療與日常場景、4 個語音翻譯系統、3 種英語口音與 2 種性別的講者，超過 1,750 次互動：**只有約一半互動被評為可用**；不同人口群體間有顯著可用性差距；以問答（QA）為基礎的評估比一般品質指標更能預測真實可用性。— [arXiv 2606.06177](https://arxiv.org/abs/2606.06177)
- **Hara & Iqbal（Microsoft Research），CHI 2015（背景）**：23 組（46 人）以語音翻譯原型 App 進行說故事任務：對話雙方會自然調整說話與理解方式來彌補語音翻譯的不足。— [Microsoft Research](https://www.microsoft.com/en-us/research/publication/effect-of-machine-translation-in-interlingual-conversation-lessons-from-a-formative-study/)
- 推論：【部分】口譯模式應讓說話者看到「自己這句被辨識成什麼」（原文）與譯文，使其能像 Hara & Iqbal 觀察到的那樣自我調整（放慢、換說法）；Ouvia 顯示約半數互動不可用 → 加「這句沒聽懂／請再說一次」的快速鍵或按鈕，在對方畫面顯示預先翻好的「請再說一次」提示（不需即時翻譯，無額外延遲）。說話者文字標示見問題 3。

### 排除的來源（相關但不符來源政策或無法確認 venue）

| 來源 | 內容 | 排除原因 |
|---|---|---|
| Liao, Kruger & Doherty, JoSTrans 2020 | 20 位中文母語者看英語影片：雙語字幕不比單語字幕增加認知負荷 | 2023 年前、專門領域期刊 |
| Peng, Liang & Sasao（日本語教育學會誌） | 71 位中國日語學習者：日中雙語字幕與字形／意義學習成效有關 | 國內期刊、未取得全文 |
| Hayabusa, JALT 2025 | 日本 EFL 學習者雙語字幕試驗 | 研討會口頭發表 |
| EDULEARN26（2026） | 中低程度學習者雙語字幕最好、中高程度只看中文最好 | 小型研討會 |
| Aljedaani et al. 2024 | 73 位 d/Deaf 玩家：67% 偏好半透明底、59% 偏好單行 | venue 無法確認 |
| Al Amin, Hassan, Lee, Huenerfauth, W4A 2023 | 19 位 DHH 直播新聞眼動：重要畫面區域隨時間變化，字幕位置要考慮時間 | 小型會議（ACM ICPS） |
| Frontiers in Computer Science 2025（May 等後續） | 只有約 4% YouTube 影片有非語音字幕且比例下降 | 不在來源政策內 |
| Xiao et al. arXiv 2503.07970（2025） | 45 組 MT 輔助對話：讓非母語者後編輯提高自主性但降低雙方溝通表現 | venue 無法確認 |
| CLASI（ByteDance）arXiv 2407.21646 | 14 位受訪者中只有 1 位認為延遲明顯影響體驗 | 技術報告、樣本極小 |
| EvolveCaptions arXiv 2510.02181 | 12 位 DHH＋6 位聽人協作修正 ASR，1 小時內錯誤率下降 | venue 未經確認 |
| arXiv 2501.02233 AR 字幕 | 字幕靠近說話者時理解較好 | venue 無法確認 |
| Fathallah et al. arXiv 2412.00342 | LLM 修正影片 ASR 字幕，WER 約 23%→10% | 預印本 |
| Glasser et al. VRST 2019 | 18 位 DHH，混合實境中說話者指示方式 | 2023 年前（可作背景） |
| W3C Text Tracks wiki／郵件列表 | roll-up vs pop-on 偏好各半、逐字顯示的偏好 | 二手資料 |
| LiveSubtitling 研討會投影片 2023 | 27 位 DHH 體育直播：約 2 秒延遲的評論字幕滿意度高於約 4 秒的 | 投影片 |
| Verbit 廠商研究 | 延遲顯著影響對正確率的感知 | 廠商行銷資料 |
| Apple FaceTime 說話者標示（Engadget 等報導） | 以圖示與名字標示說話者 | 媒體報導，未找到 Apple 官方頁 |
