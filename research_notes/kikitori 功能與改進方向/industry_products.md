# 大廠即時字幕／即時翻譯產品 2025–2026 動向與 kikitori 功能差距

> 調查日期：2026-11-08。只用大廠官方來源（產品頁、說明中心、開發者文件、官方部落格、Newsroom）。新聞、評測、論壇、個人部落格一律不當證據；只有這類來源的項目放在第一節「Gaps」的「未證實／排除」表。
> 不重複 `research_notes/競品比較/commercial_products.md`（2026-11-06）與 `open_source_projects.md` 已寫的內容；這裡只補 2025–2026 的新東西、前一份沒查到的產品（Samsung、Meta、Pixel 10/11、Teams Interpreter、Azure Live Interpreter、YouTube 自動配音、Android Expressive Captions），以及 NPU 方向。
> kikitori 現況以 `README.zh-TW.md`（v0.2.0）為準：匯出、術語表、OBS 文字檔、振假名、穩定即時字幕、延遲指示都已經做了，所以前一份筆記的 P0 多數已完成，本文不再列。
> 「（推論）」是本文的推論；「（未查證）」是我的既有知識，這次沒有找到官方頁面確認。

## 問題一：2025–2026 主流產品出了什麼？（功能矩陣）

### Takeaway
2025–2026 大廠的主軸已從「翻譯字幕」轉向**語音輸出的即時口譯**（保留說話者聲音）、**自動偵測語言（含講到一半換語言）**、**耳機／眼鏡等穿戴裝置**與**可下載的離線語言包**；字幕本身的新功能只有 Android 的聲音／情緒標籤。能翻「任何 App 的系統聲音」的仍只有 Windows 即時字幕（輸出只限英文與簡中）與 Pixel 11（手機、限部分 App），沒有一家做到 kikitori 的「桌面、任何 App、20 語任意組合、全離線」。

### Cited Findings

**Google**
- Pixel 手機：文字與 App 的 Live Translate 是 Pixel 6 以上，說明頁寫「All translations take place on your device」；通話翻譯在 Pixel 10 叫「Voice Translate」、Pixel 11 叫「Live Translate」，僅限部分國家與語言、需相容 App；通話中偵測到換語言會提示切換；說明頁沒寫通話翻譯在本機還是雲端 — [Pixel Phone Help：Translate speech & text](https://support.google.com/pixelphone/answer/11209263?hl=en)
- Pixel 10 的通話翻譯「retaining the sound of the speaker's voice」（保留說話者的聲音），只限部分語言組合 — [Pixel Phone Help](https://support.google.com/pixelphone/answer/11209263?hl=en)、[Google Store：Pixel Live Translate](https://store.google.com/us/magazine/pixel-live-translate?hl=en-US)（後者這次抓不到全文，只有搜尋摘要）
- **Pixel 11（Made by Google 2026，2026-08-20 上市）的「Live Translate for media」**：即時翻譯影片與音訊、「automatically dubbing media … into your preferred language」，用「on-device generative AI models powered by our Google Tensor G6」；註腳：不是所有媒體與 App 都能用、「Translation may not be instantaneous」 — [Google Blog：7 can't-miss updates from our Pixel 11 launch](https://blog.google/products-and-platforms/devices/pixel/pixel-11-features/)
- Pixel 11 媒體翻譯：可用在 YouTube 與部分第三方 App；**「doesn't translate music」**、「works best with a clear primary speaker and low background noise」 — [Pixel Phone Help](https://support.google.com/pixelphone/answer/11209263?hl=en)
- **Android Expressive Captions**（Live Caption 的一部分）：大寫表示強度、拉長字表示強調、聲音標籤（如 [cheers and applause]）、vocal burst 標籤；**2025-12-02 新增情緒標籤 [joyful]、[sad]**；可用在直播、影片訊息、社群限時動態，靜音時也能看 — [Google Blog：New Android features, 2025-12-02](https://blog.google/products-and-platforms/platforms/android/new-android-features-december-2025/)、[Google Blog：Expressive Captions](https://blog.google/products-and-platforms/platforms/android/google-android-expressive-captions/)
- Expressive Captions 的語言：Android 14 以上為英文（美國），Android 15 以上加英文（澳、加、英）；預設開啟；Android 15 以上多 10 種聲音標籤（例如口哨、耳語）（取自說明頁搜尋摘要）— [Android Accessibility Help：Live Caption](https://support.google.com/accessibility/android/answer/9350862?hl=en)
- **Google Meet 語音翻譯**（2025-09-11 的幕後文章）：舊的「轉寫→翻譯→合成」串接延遲約 10–20 秒；團隊發現 **2–3 秒延遲是「sweet spot」**；改用「audio in, audio out」的一步式模型；以「a voice like yours」輸出；上線時語言為義、葡、德、法；承認逐字直譯會造成誤解 — [Google Blog：How Google built real-time translation for Meet](https://blog.google/products-and-platforms/products/workspace/google-meet-langauge-translation-ai/)
- Meet（2026-10）：改用 Gemini 3.5 Live Translate、63 種語言、網頁與 Android／iOS；每位與會者自己開；基本方案每場上限 90 分鐘、AI Ultra 等方案 8 小時；**不支援直播、會議錄影、CSE 加密會議、電話撥入** — [Workspace Updates 2026-10](https://workspaceupdates.googleblog.com/2026/10/real-time-speech-translation-in-google-Meet-expands-to-63-languages-with-automatic-language-detection.html)；管理員控制 — [Workspace Updates 2026-01](https://workspaceupdates.googleblog.com/2026/01/control-speech-translation-in-meet.html)
- **Gemini 3.5 Live Translate（2026-06-09）**：自動偵測 70 多種語言；「stays just a few seconds behind the speaker」；不等說話者說完就連續產生語音；保留語調、節奏、音高；Translate App 在 Android／iOS 上可接**任何耳機**；Android 新增「listening mode」（像接電話一樣把手機貼耳聽翻譯）；開發者可經 Gemini Live API 預覽；文章沒寫在本機或雲端 — [Google Blog 2026-06-09](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)
  - 衝突：同一篇寫 Meet「70+」語言，四個月後的 Workspace Updates 寫 63 種；以較新的 63 為準。
- **YouTube 自動配音（auto dubbing）**：27 種語言；8 種語言有「Expressive Speech」（模仿原說話者的音高與語調）；創作者可預覽、上傳自己的配音或關閉 — [YouTube Blog 2026-05-15](https://blog.youtube/creator-and-artist-stories/youtube-auto-dubbing-explained/)；對嘴（lip sync）仍是實驗、只開放部分頻道；**影片層級的過濾器會辨認不該配音的影片（例如音樂、無聲 vlog）** — [YouTube Blog：auto dubbing expressive speech](https://blog.youtube/news-and-events/youtube-auto-dubbing-expressive-speech/)、[YouTube Help](https://support.google.com/youtube/answer/15569972?hl=en)

**Apple**
- WWDC25（2025-06-09）宣布 Live Translation：整合在訊息、FaceTime、電話；「Apple-built models that run entirely on device」；**FaceTime 顯示翻譯字幕、同時聽到原聲；電話則把譯文唸出來**；首發語言：訊息 9 種、電話與 FaceTime 一對一通話 5 種（英、法、德、巴葡、西）；新聞稿沒有提到給第三方 App 的 Live Translation API — [Apple Newsroom 2025-06-09](https://www.apple.com/newsroom/2025/06/apple-intelligence-gets-even-more-powerful-with-new-capabilities-across-apple-devices/)
- **AirPods 即時翻譯**（2025-09 隨 iOS 26 推出，標 beta）：需 iPhone 15 Pro 以上、iOS 26、Apple Intelligence、Translate App；AirPods 4（ANC）、AirPods Pro 2 以上、AirPods Max 2；**語言模型下載後全部在 iPhone 上處理** — [Apple Support 123185](https://support.apple.com/en-us/123185)、[Apple Newsroom 2025-09](https://www.apple.com/newsroom/2025/09/new-apple-intelligence-features-are-available-today/)
- 2025-11：AirPods 即時翻譯擴展到歐盟，因 DMA 合規工程而延後；語言含英、法、德、葡、西、義、簡繁華語、日、韓 — [Apple Newsroom (IE) 2025-11](https://www.apple.com/ie/newsroom/2025/11/live-translation-on-airpods-expands-to-the-eu/)
- iOS／iPadOS 27 feature availability（2026-11 擷取）：訊息 16 種（新增丹麥、荷、挪、瑞典、土、越、繁中等）；電話與 FaceTime 及 AirPods 為 10 種（含日文、簡繁華語）— [Apple：iOS 27 Feature Availability](https://www.apple.com/ios/feature-availability/)；WWDC26（2026-06）Apple Intelligence 語言清單 — [Apple Newsroom 2026-06](https://www.apple.com/newsroom/2026/06/apple-unveils-next-generation-of-apple-intelligence-siri-ai-and-more/)
- Mac 即時字幕（macOS Tahoe 26）：只限 Apple silicon；第一次啟用要連網下載語言資料；可捲動的視窗；**說明頁沒有存檔或匯出**。iPhone 通話字幕則有「Keep Call Captions」：選 1 小時才能「Copy Captions」，選 1 分鐘不能複製、截圖、錄影 — [Apple Support：Mac 即時字幕](https://support.apple.com/guide/mac-help/get-captions-of-spoken-and-computer-audio-mchldd11f4fd/mac)、[Apple Support：iPhone 即時字幕](https://support.apple.com/guide/iphone/get-live-captions-of-spoken-audio-iphe0990f7bb/ios)

**Microsoft**
- Windows 11 即時字幕（說明頁 2026 版）：翻譯仍只輸出**英文（40 多種來源）與簡中（27 種來源）**，需 Copilot+ PC、24H2 以上；離線可用；「Generated captions are not stored anywhere」；**「Audible events such as applause or music will not be detected」**；跟著預設輸出裝置；麥克風預設關閉且只在沒有其他聲音時才字幕化 — [Microsoft Support：Live captions](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio)
- 2025-03-31：即時字幕翻譯擴展到 AMD 與 Intel 的 Copilot+ PC — [Windows Experience Blog 2025-03-31](https://blogs.windows.com/windowsexperience/2025/03/31/expanding-copilot-pc-experiences-across-amd-intel-and-snapdragon-powered-devices/)；2025-04-25：新增 27 種語言譯成簡中（搜尋摘要）— [Windows Experience Blog 2025-04-25](https://blogs.windows.com/windowsexperience/2025/04/25/copilot-pcs-are-the-most-performant-windows-pcs-ever-built-now-with-more-ai-features-that-empower-you-every-day/)
- **Teams Interpreter agent**（Learn 文件 2026-10-08 更新）：會議與 1:1 VoIP 通話的**語音對語音**口譯；9 種語言（華語、英、法、德、義、日、韓、葡、西）；可「simulate their own voice」或選預設語音（Ava、Andrew、Fable Turbo）；**每位使用者每月含 20 小時**（Copilot 授權）；流程是 Azure 的 ASR → 譯成英文文字 → MT → TTS，由 bot 把會議聲音送雲端；聲紋樣本不儲存；不支援 Teams 活動（events）、PSTN、Teams 免費版 — [Microsoft Learn：Manage Interpreter agent](https://learn.microsoft.com/en-us/microsoftteams/interpreter-agent-teams)
- **Azure Live Interpreter API**：2025 年公開預覽，**2025-11-20 GA**；在 76 種輸入語言間**連續自動偵測語言**、不必指定輸入語言；輸出只有 9 種（英、德、西、法、義、日、韓、葡、簡中）；可用個人聲音（personal voice，需另外申請）；宣稱「Ultra-low latency near human parity」（無數字）— [Tech Community 2025-11-20](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/advancing-speech-innovation-with-azure-speech-in-microsoft-foundry/4471461)、[預覽公告](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/announcing-live-interpreter-api---now-in-public-preview/4453649)、[Learn：How to translate speech](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-translate-speech)

**Samsung**
- 2026-10-14：Galaxy AI 的 Call Assist（含 Live Translate）、Note Assist、Interpreter、Transcript Assist 加入匈牙利語與馬來語，**Live Translate 達 24 種語言**（含中國大陸、香港、台灣中文與日文）；「Some of the new Galaxy AI languages can be downloaded as language packs from the Settings app」；新聞稿沒寫本機或雲端 — [Samsung Newsroom 2026-10-14](https://news.samsung.com/global/samsung-expands-language-support-across-ai-experiences-on-galaxy)
- 啟用通話翻譯時會提示下載語言；Interpreter App 預裝英、西，其他語言免費下載（較舊的支援頁）— [Samsung US：Call assist](https://www.samsung.com/us/support/answer/ANS10000935/)、[Samsung：Galaxy AI languages guide](https://www.samsung.com/pk/support/mobile-devices/galaxy-ai-languages-usage-guide/)

**Meta**
- AI 眼鏡即時翻譯（說明頁，2026-11 擷取）：連網可用 20 種語言（含日、華語、韓、泰、越、印尼）；**離線（先下載語言包）只有英、西、法、義、葡、德 6 種**；Ray-Ban Meta、Oakley Meta 的譯文以**文字顯示在手機 Meta AI App**，對方可點喇叭圖示聽譯文；Meta Ray-Ban Display 則把**字幕顯示在鏡片內**，目前只有英、法、義、西 4 種；以「你說／他們說」選語言，語音指令「Hey Meta, start live translation」— [Meta Help：How to use live translation](https://www.meta.com/help/ai-glasses/955732293123641/)
- 2026-06 版本說明：新增 14 種語言（搜尋摘要，未逐條核對）— [Meta：AI glasses release notes](https://www.meta.com/help/ai-glasses/1809764829519902/)；2025 年 v11 更新首度推出即時翻譯 — [Meta Blog：v11 update](https://www.meta.com/blog/ray-ban-meta-v11-software-update-live-ai-translation-shazam/)

**NVIDIA**
- NVIDIA Broadcast 2.3（2026-10）新增的是影像超解析、補幀、離線檔案處理、本機 MCP 控制；Studio Voice 等音訊功能是麥克風音色處理。**官方版本說明沒有字幕或翻譯功能** — [NVIDIA Broadcast Release Highlights](https://www.nvidia.com/en-us/geforce/broadcasting/broadcast-app/release-notes/)、[NVIDIA Blog：RTX Spark](https://blogs.nvidia.com/blog/local-ai-rtx-spark-laptops-software-updates/)

### 功能矩陣（2025–2026 新版本 vs kikitori）

符號：✅ 有；△ 有限制；❌ 沒有；? 官方未說明。

| 項目 | **kikitori 0.2.0** | Windows 即時字幕 | Pixel 10/11 | Android Expressive Captions | Google Meet 語音翻譯 | Gemini 3.5／Translate App | YouTube 自動配音 | Apple Live Translation／AirPods | Teams Interpreter | Azure Live Interpreter | Samsung Galaxy AI | Meta AI 眼鏡 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 任何 App 的系統聲音 | ✅ 桌面三平台 | ✅ 預設輸出裝置 | △ Pixel 11 媒體翻譯，限部分 App | ✅（Android 字幕） | ❌ 只在會議內 | ❌ 麥克風／對話 | ❌ 平台內預錄影片 | ❌ 通話、訊息、面對面 | ❌ 會議內 | API | △ 通話、面對面 | ❌ 面對面 |
| 輸出形式 | 雙語字幕 | 單語字幕 | 語音（配音）＋字幕 | 字幕 | **語音**＋字幕 | **語音** | **配音音軌** | 字幕（FaceTime）、**語音**（電話、AirPods） | **語音** | 語音＋文字 | 語音＋文字 | 手機文字／鏡片字幕 |
| 保留說話者聲音 | ❌ | ❌ | ✅（Pixel 10 通話） | — | ✅ | ✅ 語調、節奏 | ✅ 8 語 Expressive | ? | ✅ 可選 | ✅ personal voice | ? | ❌ |
| 自動偵測語言（含中途切換） | △ `auto`，逐句 | ❌ | △ 偵測後提示切換 | — | ✅ | ✅ 70+ | — | ? | ✅ 多語混合會議 | ✅ 76 語連續偵測 | ? | ❌ 先選語言 |
| 聲音／情緒標籤 | ❌（音樂段不出字幕） | ❌ 官方明說不偵測 | ? | ✅ 聲音、vocal burst、情緒 | ❌ | ❌ | — | ❌ | ❌ | ❌ | ? | ❌ |
| 離線 | ✅ 全部（下載模型後） | ✅ | ✅ 文字翻譯；媒體為本機模型 | ✅ | ❌ 雲端 | ? | ❌ | ✅ 本機 | ❌ Azure | ❌ | △ 語言包 | △ 6 語語言包 |
| 語言包／按需下載 | ✅ 依語言與硬體下載模型 | 系統語言包 | ✅ | — | — | — | — | ✅ | — | — | ✅ | ✅ |
| 繁體中文輸出 | ✅ 台灣用語 | ❌ | ? | — | ? | ? | ? | ✅ | ✅（華語） | ❌ 只有簡中 | ✅ 台灣 | ? |
| 日文來源 | ✅ | ✅ | ? | ❌ 英文限定 | ✅ 63 語內 | ✅ | ? | ✅ | ✅ | ✅ | ✅ | ✅ 連網 |
| 字幕存檔／匯出 | ✅ SRT/VTT/TXT/CSV | ❌ 不儲存 | ? | ? | ❌ 不支援錄影 | ? | — | △ iPhone 通話字幕 1 小時內可複製 | ? | — | ✅ Transcript Assist | △ App 內文字 |
| 硬體門檻 | NVIDIA GPU 最佳；CPU 可 | **Copilot+ NPU** | **Tensor G5/G6** | Android 14+ | 雲端 | 雲端 | 雲端 | **Apple Intelligence 機型** | 雲端 | 雲端 | Galaxy AI 機型 | 眼鏡＋手機 |
| 價格 | 免費 | 免費 | 手機內含 | 免費 | 方案限時 90 分／8 小時 | ? | 創作者免費 | 免費 | Copilot 授權，每月 20 小時 | 按時數計費 | 手機內含 | 眼鏡內含 |
| 官方延遲數字 | ✅ 說完到完整字幕 0.92 s（中位數，RTX 5070） | ❌ | ❌「may not be instantaneous」 | ❌ | 2–3 s 為設計目標 | 「落後幾秒」 | 離線 | ❌ | ❌「small delay」 | ❌「ultra-low」 | ❌ | ❌ |

### Inferences
- 大廠做「語音輸出」的都在雲端（Meet、Gemini、Teams、Azure），只有 Pixel 11 媒體翻譯與 Apple 是本機。本機語音輸出目前只出現在有專用 NPU／TPU 的手機上（推論）。
- 兩家大廠官方都把**音樂排除在翻譯外**（Pixel 11「doesn't translate music」、YouTube 會辨認音樂影片不配音）。這和 kikitori 把歌唱與純配樂段濾掉的做法一致，可以在文件裡引用，說明這不是 kikitori 獨有的缺陷（推論）。
- 「看外語直播」在大廠產品中仍是空白：Meet 明說不支援直播；YouTube 自動配音是預錄影片；Windows 輸出只有英、簡中。Pixel 11 媒體翻譯最接近，但只在手機、限部分 App，也沒有寫支援哪些語言。
- 大廠的「字幕」功能幾乎都沒有存檔與匯出（Windows 明說不儲存），所以 kikitori 的匯出是相對優勢，不是缺口。

### Gaps
- 各產品的官方延遲數字幾乎都沒有，只有 Google Meet 的「2–3 秒是 sweet spot」。
- Pixel 10 Voice Translate 與 Pixel 11 媒體翻譯的語言清單、通話翻譯是否在本機：說明頁沒寫，Google Store 頁這次抓不到全文。
- Chrome 即時字幕翻譯在 2025–2026 有沒有更新：沒找到官方新文章（前一份筆記的說明頁內容仍是最新）。
- Zoom、OBS 的 2025–2026 官方字幕或翻譯更新：這次沒查（時間有限）。
- Samsung Live Translate、Interpreter 哪些語言在本機、哪些要雲端：2026-10 新聞稿沒寫。
- Teams Interpreter 的實際延遲：官方只說「small delay」。

#### 未證實／排除（只有新聞或第三方，或官方頁沒寫清楚）

| 項目 | 狀況 | 處理 |
|---|---|---|
| Pixel 10 Voice Translate 在本機（Tensor G5）處理 | 只在新聞與搜尋摘要看到；官方說明頁沒寫處理位置 | 不列入矩陣的「離線」 |
| Pixel 10 上市日期（2025-08） | 依我的既有知識，這次沒抓到官方頁 | 只寫「Pixel 10」 |
| Meta 眼鏡「新增 14 種語言」 | 版本說明頁只看到搜尋摘要 | 列為搜尋摘要 |
| Windows ML 的 OpenVINO／QNN／VitisAI EP「2026-07 GA」 | 只出現在土耳其語版頁面的搜尋摘要；英文頁沒寫 GA | 不當事實引用 |
| Azure Live Interpreter 不支援部分結果（逐字翻譯） | 來自 Microsoft Q&A 論壇回答（2026-01），且早於 GA | 排除 |
| 各家翻譯品質實測（例如 Windows 即時字幕翻錯） | 只有媒體評測 | 排除（前一份筆記已列為 [媒體]） |

## 問題二：主流產品反覆出現、kikitori 沒有的功能（依優先順序）

### Takeaway
反覆出現、kikitori 沒有的功能依序為：**(1) 譯文語音輸出（TTS／配音）**、**(2) 中途換語言的自動偵測**、**(3) 聲音事件標籤**、**(4) 手機或穿戴裝置的延伸**、**(5) 保留說話者聲音**、**(6) 情緒標籤**。前三項可以在本機用 ONNX、不用 PyTorch 做到；(5)(6) 有授權、倫理與算力問題，不建議近期做。說話者標示與只擷取單一 App 在 2025–2026 的大廠消費級產品裡反而很少見。

### Cited Findings
- 語音輸出至少出現在 7 個大廠產品：Meet（[Workspace 2026-10](https://workspaceupdates.googleblog.com/2026/10/real-time-speech-translation-in-google-Meet-expands-to-63-languages-with-automatic-language-detection.html)）、Gemini 3.5 Translate App（[Google 2026-06-09](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)）、Pixel 通話與媒體（[Pixel Help](https://support.google.com/pixelphone/answer/11209263?hl=en)）、YouTube 自動配音（[YouTube Blog](https://blog.youtube/creator-and-artist-stories/youtube-auto-dubbing-explained/)）、Apple 電話與 AirPods（[Apple Newsroom 2025-06-09](https://www.apple.com/newsroom/2025/06/apple-intelligence-gets-even-more-powerful-with-new-capabilities-across-apple-devices/)、[Apple Support 123185](https://support.apple.com/en-us/123185)）、Teams Interpreter（[Learn](https://learn.microsoft.com/en-us/microsoftteams/interpreter-agent-teams)）、Azure Live Interpreter（[Tech Community](https://techcommunity.microsoft.com/blog/azure-ai-foundry-blog/advancing-speech-innovation-with-azure-speech-in-microsoft-foundry/4471461)）
- Teams 員工最重視的包含「調整 Interpreter 音量與原聲的比例」、在同一場會議聽到多位說話者被以自己的語言唸出 — [Microsoft Inside Track 2025-04-17](https://www.microsoft.com/insidetrack/blog/deploying-our-new-game-changing-interpreter-agent-in-our-meetings-at-microsoft/)
- 自動偵測（含中途切換）：Meet 2026-10、Gemini 3.5（70+ 語）、Azure Live Interpreter（76 語連續偵測）、Teams（「multi-speaker, mixed-language conversations」）、Pixel 通話（偵測後提示切換）— 來源同上
- 聲音標籤：Android Expressive Captions（[Google Blog 2025-12-02](https://blog.google/products-and-platforms/platforms/android/new-android-features-december-2025/)）；Windows 官方明說「applause or music will not be detected」（[Microsoft Support](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio)）
- 穿戴與手機：AirPods（[Apple Support 123185](https://support.apple.com/en-us/123185)）、Meta 眼鏡（[Meta Help](https://www.meta.com/help/ai-glasses/955732293123641/)）、Gemini Translate App 支援任何耳機、Android listening mode（[Google 2026-06-09](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)）
- 離線語言包：Apple（下載語言模型後全在 iPhone 上，[Apple Support 123185](https://support.apple.com/en-us/123185)）、Meta（6 語，[Meta Help](https://www.meta.com/help/ai-glasses/955732293123641/)）、Samsung（設定裡下載，[Samsung Newsroom 2026-10-14](https://news.samsung.com/global/samsung-expands-language-support-across-ai-experiences-on-galaxy)）
- 保留聲音有同意與冒充的顧慮：Teams 要說話者同意才模擬聲音，Microsoft 自己承認隱私與冒充是「legitimate concerns」— [Inside Track](https://www.microsoft.com/insidetrack/blog/deploying-our-new-game-changing-interpreter-agent-in-our-meetings-at-microsoft/)；Azure personal voice 要另外申請存取 — [Learn](https://learn.microsoft.com/en-us/azure/ai-services/speech-service/how-to-translate-speech)
- YouTube 給創作者的控制：預覽配音、上傳自己的配音、關閉 — [YouTube Blog](https://blog.youtube/news-and-events/youtube-auto-dubbing-expressive-speech/)

### 建議清單（kikitori 缺、使用者明顯重視；附本機可行性）

| # | 功能 | 證據強度 | 本機可行性（不用 PyTorch、消費級 GPU 或 CPU、MIT 相容） | 工作量 | 建議 |
|---|---|---|---|---|---|
| 1 | **譯文語音朗讀（口譯模式優先）**：把完整字幕的譯文用 TTS 唸出，可調音量與原聲比例；只唸完整字幕、不唸即時字幕 | 7 個大廠產品；Teams 員工重視音量比例 | 可行（推論）：sherpa-onnx（Apache-2.0，ONNX）支援多種 TTS 模型；Kokoro-82M 權重為 Apache-2.0、有 ONNX 版，涵蓋英、日、中、西、法、義、葡、印地（**未查證**，要先查官方 model card 與授權）。Piper 原 repo 已封存，新版 piper1 改為 GPL-3.0（**未查證**，若屬實不能用）。TTS 與 Ollama、Whisper 共用 GPU 時要排程；建議 TTS 跑 CPU | M | **最優先**。口譯模式（對話）價值最高；看直播時預設關閉 |
| 2 | **逐句自動偵測語言＋混語穩定化**：低信心沿用上一句語言、UI 標示偵測到的語言 | Meet、Gemini、Azure、Teams、Pixel 都有 | 已有 Whisper 語言偵測（`auto`），只是策略與 UI。前一份筆記 P1 #11 已列 | S–M | 次優先，延續前一份筆記 |
| 3 | **聲音事件標籤**：在字幕位置顯示「♪」「（笑）」「（掌聲）」，取代目前的「不出字幕」 | Android Expressive Captions；Windows 明說做不到 | 可行（推論）：小型音訊分類模型（例如 YAMNet 類，AudioSet 527 類）轉 ONNX 在 CPU 跑；授權要逐一查（**未查證**）。kikitori 已有 Silero VAD 與音樂過濾，可在 VAD 判定非語音、但音量高的段落才跑分類 | M | 對 VTuber 直播（BGM、笑聲、歌回）特別有用，與既有「歌唱段落處理」合併 |
| 4 | **手機或第二螢幕顯示字幕** | AirPods、Meta 眼鏡、Gemini 任何耳機：大廠把翻譯延伸到穿戴裝置 | 和「不開網路埠」的架構原則衝突，要寫 ADR；只限區域網路、要 token、預設關閉。前一份筆記 P3 #16 已列 | M | 維持 P3；等有使用者需求再做 |
| 5 | **保留說話者聲音的語音翻譯** | Meet、Gemini、Pixel、Teams、Azure、YouTube | 不建議：本機聲音複製模型多數依賴 PyTorch、授權常有非商用限制（**未查證**）；冒充與同意的倫理問題（Microsoft 自己承認）；對直播觀眾的價值低 | L | 不做，寫進「先不做」 |
| 6 | **情緒標籤**（[開心]、[難過]） | 只有 Android（2025-12） | 研究性質：需要語音情緒模型，日文直播的準確度沒有任何公開數據 | L | 不做 |
| 7 | **整段影片配音** | YouTube 自動配音 | 超出 kikitori 即時字幕定位 | L | 不做 |

### Inferences
- 前一份筆記把 TTS 列為 P3（「對看直播的情境價值不高」）。大廠在 2025–2026 幾乎全部轉向語音輸出，但都是**對話與會議**情境；看影片情境只有 Pixel 11 與 YouTube。所以 TTS 應該**只在口譯模式提高優先**，影片翻譯模式仍維持字幕為主（推論）。
- 大廠在 2025–2026 的消費級產品裡，沒有看到新的「說話者標示」或「只擷取單一 App」功能；這兩項維持前一份筆記的優先順序即可，不因本次調查而提高（推論）。
- kikitori 已經是「下載後全離線」，等同大廠的離線語言包；差別在 kikitori 依硬體選模型，而不是依語言下載。宣傳時可以直接對照 Apple、Meta 的「先下載語言包才能離線」（推論）。

### Gaps
- 本機 TTS 候選（Kokoro、Piper、sherpa-onnx 的各模型）的授權與日文、繁中品質：這次沒有查官方 model card，要另外調查。
- 音訊事件分類模型的 ONNX 版本與授權、在 BGM 混音下的準確度：沒有查。
- 沒有任何大廠公開「使用者最常用哪個功能」的排名，所以「使用者明顯重視」只能用「多少家大廠都做了」與 Microsoft 員工的質化回饋來推。

## 問題三：NPU 硬體趨勢與 kikitori 的 GPU 加速

### Takeaway
大廠的本機翻譯都綁專用 AI 晶片（Copilot+ NPU、Tensor G6 TPU、Apple Intelligence 機型），但 PC 上給第三方用的 NPU 路線仍很碎：Windows ML 用 ONNX Runtime 的 OpenVINO（Intel）、QNN（Qualcomm）、VitisAI（AMD）EP 按需下載，DirectML 已是「legacy／sustained engineering」；AMD 官方只支援 Whisper base／small／medium 上 NPU、不支援 large。對 kikitori 來說，NPU 最多適合**即時字幕的小模型**，完整字幕的 large-v3 與 Ollama 翻譯仍要靠 GPU 或 CPU。

### Cited Findings
- Windows ML（Learn 文件 2026-10-05）：內含 CPU 與 **DirectML（legacy）**；按需下載的 EP 有 MIGraphX、AMDGPU、**NvTensorRtRtx（NVIDIA RTX 30 系列以上、CUDA 12.5）**、**OpenVINO（Intel）**、**QNN（Qualcomm Snapdragon X Elite／Plus，Hexagon NPU 驅動 30.0.140.0 以上）**、**VitisAI（AMD）**、WebGPU（實驗）；需 Windows 11 24H2（build 26100）以上；AMD 建議從 MIGraphX 轉到 AMDGPU EP — [Microsoft Learn：Windows ML execution providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers)
- ONNX Runtime 官方：「DirectML is in sustained engineering」，「new feature development has moved to WinML for Windows-based ONNX Runtime deployments」— [ONNX Runtime：DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- Copilot+ PC 開發者指南 — [Microsoft Learn：Copilot+ PCs developer guide](https://learn.microsoft.com/en-us/windows/ai/npu-devices/)
- AMD（2025）：Whisper 經 Hugging Face Optimum 匯出後在 Ryzen AI NPU 上執行，支援 base、small、medium 多語版，**「Whisper large is not supported at this time」**；Ryzen AI 9 HX 370 上 Whisper Base 的 RTF：NPU 0.35、CPU 0.7（沒有 KV cache）；第一次編譯每個模型 5–15 分鐘，之後用快取 — [AMD：Unlocking On-Device ASR with Whisper on Ryzen AI NPUs](https://www.amd.com/en/developer/resources/technical-articles/2025/unlocking-on-device-asr-with-whisper-on-ryzen-ai-npus.html)
- AMD 另外維護 whisper.cpp 的 NPU 分支，目前只支援 Windows — [Ryzen AI docs：Whisper.cpp support](https://ryzenai.docs.amd.com/en/main/whisper_cpp.html)
- Intel OpenVINO GenAI 的 `WhisperPipeline` 可以指定 CPU、GPU 或 NPU，輸入為 16 kHz 音訊 — [OpenVINO docs：Inference with OpenVINO GenAI](https://docs.openvino.ai/2025/openvino-workflow-generative/inference-with-genai.html)；官方 Whisper notebook 的裝置選單排除 NPU — [OpenVINO notebook](https://docs.openvino.ai/2024/notebooks/whisper-asr-genai-with-output.html)
- 大廠綁硬體的例子：Windows 翻譯要 Copilot+ PC（[Microsoft Support](https://support.microsoft.com/en-us/accessibility/windows/use-live-captions-to-better-understand-audio)）；Pixel 11 媒體翻譯用 Tensor G6（[Google Blog](https://blog.google/products-and-platforms/devices/pixel/pixel-11-features/)）；AirPods 翻譯要 Apple Intelligence iPhone（[Apple Support 123185](https://support.apple.com/en-us/123185)）

### Inferences
- **kikitori 目前的 CTranslate2 只有 CPU 與 CUDA**（依專案現況；CTranslate2 有沒有其他後端這次沒查官方文件），要上 NPU 就得為 Whisper 另做一條 ONNX Runtime（或 OpenVINO GenAI）路線，等於多維護一個 `Transcriber` 後端。kikitori 已經用 ONNX Runtime 跑 Silero VAD，執行環境不必另外引入（推論）。
- 可行的切入點：**只把「即時字幕」的小模型（base／small）放 NPU**，讓沒有 NVIDIA GPU 的 Copilot+ 筆電得到比現在 CPU 組合（base＋small，完整字幕延遲中位數 2.85 s）更好的體驗；完整字幕維持 CPU（推論）。AMD 的 RTF 數字（base 在 NPU 0.35 vs CPU 0.7）支持「NPU 約快 2 倍、但只限小模型」。
- 第一次編譯 NPU 模型要幾分鐘（AMD 5–15 分鐘），和 kikitori「按開始才下載、要快速開始」的體驗衝突；要做成「背景預先編譯＋快取」（推論）。
- 三家 NPU EP 的驅動版本要求都很窄（例如 VitisAI 有最低與**最高**驅動版本），支援矩陣會快速膨脹；kikitori 只有一台 RTX 5070 能實測，沒有 NPU 機器就無法驗收。建議先寫 ADR 記錄「目前不支援 NPU」的理由與重新評估的條件（例如有使用者回報、或 Windows ML EP 穩定）（推論）。
- NVIDIA 方面，Windows ML 的 NvTensorRtRtx EP 是另一條 GPU 路線，但 kikitori 的 CUDA＋cuBLAS 已經能用，沒有轉換的必要（推論）。
- Ollama 翻譯模型能不能用 NPU：這次沒查到官方說明，視為不行；所以 NPU 機器上翻譯仍是 CPU，翻譯延遲仍是瓶頸（推論）。

### Gaps
- Qualcomm AI Hub 的 Whisper 模型（支援哪些尺寸、授權、日文品質）：這次沒查。
- Apple Neural Engine：ONNX Runtime CoreML EP 或 whisper.cpp Core ML 編碼器能否給 kikitori 的 macOS 版用，這次沒查官方文件。
- ONNX Runtime 的 OpenVINO／QNN／VitisAI EP 在 **Python 套件**（而非 Windows App SDK）上的可用性與授權：沒查。
- 任何 NPU 上 Whisper large-v3（或 turbo）的官方數字：沒找到；AMD 明說不支援 large。
- CTranslate2 官方的後端清單（是否已支援 ROCm 或其他）：沒查，以專案現況為準。

## 問題四：大廠公開的使用數字與使用者研究

### Takeaway
大廠幾乎沒有公開即時字幕或翻譯的使用數字。這次只找到 YouTube 自動配音的每日觀看人數、Google 翻譯整體的翻譯量，以及 Google Meet 團隊「2–3 秒延遲最好」的設計結論；Microsoft 只有員工的質化回饋。

### Cited Findings
- YouTube：截至 2025-12，**每天有超過 600 萬名觀眾看自動配音內容 10 分鐘以上**；「millions of channels」在用 — [YouTube Blog 2026-05-15](https://blog.youtube/creator-and-artist-stories/youtube-auto-dubbing-explained/)
- Google 翻譯整體：每月翻譯「over a trillion words … for billions of users」（不是單指即時翻譯）— [Google Blog 2026-06-09](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)
- 同篇：Grab 測試 Gemini 3.5 用在司機與旅客的通話，Grab 每月「over 10 million voice calls」（是 Grab 的通話量，不是翻譯使用量）— [Google Blog 2026-06-09](https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/)
- Google Meet 團隊：舊串接延遲 10–20 秒；**「two to three seconds was sort of a sweet spot」**；語言學家與語言專家參與測試；目前多半逐字直譯、可能造成誤解 — [Google Blog 2025-09-11](https://blog.google/products-and-platforms/products/workspace/google-meet-langauge-translation-ai/)
- Microsoft 內部部署 Teams Interpreter：沒有使用人數；員工引言如「I can think and speak at the speed of my first language」、「Hearing my voice speaking Japanese for the first time was surreal」；重視第一語言表達、自己的聲音、音量比例、同意與管理控制 — [Microsoft Inside Track 2025-04-17](https://www.microsoft.com/insidetrack/blog/deploying-our-new-game-changing-interpreter-agent-in-our-meetings-at-microsoft/)
- Teams Interpreter 每位使用者每月含 20 小時（是配額，不是使用數字）— [Microsoft Learn](https://learn.microsoft.com/en-us/microsoftteams/interpreter-agent-teams)

### Inferences
- Meet 的「2–3 秒」是**語音對語音**的目標；kikitori 的字幕在 GPU 上說完到完整字幕約 0.92 s，已經比這個快。若之後加 TTS，整條「說完 → 聽到譯文」的延遲可以用 2–3 秒當驗收上限（推論）。
- YouTube 600 萬名每日觀眾的數字說明「看外語影片時用機器譯的聲音」已有大眾接受度，但那是預錄影片、平台端產生；不能直接推到直播觀眾（推論）。

### Gaps
- Windows 即時字幕、Android Live Caption、Apple Live Captions、Pixel Live Translate、Samsung Live Translate、Meta 眼鏡翻譯：這次都沒有找到 2025–2026 官方使用數字。
- 沒有任何大廠公開日文直播（有 BGM、多人、口語）的字幕或翻譯準確度。
- Google Meet「2–3 秒」的使用者研究方法、樣本數：文章沒寫。
