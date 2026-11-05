# 商用與平台內建的即時字幕、即時翻譯產品比較

> 調查日期：2026-11-06。範圍：作業系統、瀏覽器、影音平台內建的功能，會議工具，商用 App 與服務，雲端語音 API（作為品質參考），以及直播與 VTuber 觀眾用的工具。
> 證據分級：**[官方]** 官方說明文件、產品頁、官方部落格；**[媒體]** 有編輯把關的科技媒體實測；**[廠商]** 廠商自己的行銷頁或部落格，只能當作「宣稱」。不使用個人部落格當證據。找不到可信來源的項目標「未證實」。
> kikitori 的現況取自 `docs/architecture.md`、`docs/specs/*.md` 與 `experiments/*/README.md`（2026-11-05 實測）。

## 0. 摘要

- 這一類產品分成四群：**系統內建**（Windows 即時字幕、Chrome、Android、Apple）、**會議工具內**（Teams、Zoom、Meet、DeepL Voice、Wordly）、**雲端訂閱 App**（Felo、Otter、Notta、VoicePing、Pocketalk、Seagull、FluentCap）、**雲端 API**（Speechmatics、Deepgram、Soniox）。
- **對 kikitori 的主要情境（看日文 VTuber 直播、譯成繁體中文），目前沒有一個產品是免費、離線、能擷取任何 App 的聲音，又能輸出繁體中文。**
  - Windows 即時字幕只能譯成英文或簡體中文，而且要 Copilot+ PC。
  - YouTube 直播的自動字幕只支援英文，所以日文直播沒有自動字幕，自動翻譯也就無從啟動。
  - Apple 的 Live Translation 只用在 FaceTime、電話與訊息，不處理一般影片。
  - 會議工具只翻譯自己 App 裡的聲音。
- **商用產品贏過 kikitori 的地方集中在「工具性」功能**：存檔與匯出、詞彙表、說話者標示、字幕樣式與無障礙、快速鍵、設定好就能用的首次體驗（不用另外裝 Ollama）、分享字幕給其他裝置。翻譯品質與延遲則沒有可以直接比較的公開數字。
- **kikitori 延遲已經達到商用的水準，但只限 GPU 組合**：在 RTX 5070 上實測，首個即時字幕的中位數 0.94 s，即時字幕延遲中位數 0.33 s，說完到完整字幕中位數 0.69 s（`experiments/e2e_acceptance/README.md`）。Speechmatics 宣稱完整結果在 1–2 s 內，Felo 宣稱 1 秒內出譯文。CPU 組合（base＋small）的完整字幕延遲中位數 2.85 s，明顯落後。
- 建議的優先順序（細節見第 3 節）：
  - **P0**：字幕紀錄存檔與匯出、詞彙表（人名與專有名詞）、即時字幕的穩定度（避免閃爍與整句重寫）、首次執行時不必先懂 Ollama。
  - **P1**：只擷取單一 App 的聲音、情境預設組（雜談、歌回、遊戲、會議）、更多快速鍵、字幕樣式與無障礙選項、歌唱段落的處理。
  - **P2**：給學習者的振假名與羅馬字、點字查詞、匯出成 Anki 卡片。這是**沒有任何即時字幕產品做到的差異化功能**。
  - **P3**：說話者標示、分享到手機、OBS 輸出。

---

## 1. 各產品重點

### 1.1 系統與平台內建

#### Windows 11 即時字幕與即時翻譯（Live Captions）
- **聲音來源**：電腦正在播放的任何聲音，與 App 無關；可以另外加入麥克風（預設關閉）。兩者都有聲音時，以播放的聲音為優先。字幕跟著「預設輸出裝置」，用耳機的人可能要先切換預設裝置。[官方] [Microsoft 支援][ms-lc]
- **語言**：
  - 字幕語言有 21 種，含 zh-TW、zh-HK、ja、ko。
  - **翻譯只能輸出英文或簡體中文**：40 多種語言可以譯成英文（含 ja、ko、zh），27 種可以譯成簡體中文（含 ja）。**不能輸出繁體中文**，也不能輸出其他語言。[官方] [Microsoft 支援][ms-lc]
- **硬體**：字幕需要 Windows 11 22H2 以上；**翻譯需要 Copilot+ PC 與 24H2 以上**。Intel、AMD 的 Copilot+ PC 剛開放時，系統主要語言必須是英文。[官方] [ms-lc]、[Windows Insider Blog][ms-insider]
- **隱私**：聲音的處理與字幕的產生都在本機完成，不需要網路；字幕不會存在本機或雲端。[官方] [ms-lc]
- **介面**：可以停駐在螢幕上緣或下緣（會佔用桌面空間），也可以用浮動視窗；有內建與自訂的字幕樣式；拉大視窗可以顯示更多行；有不雅字詞遮蔽；快速鍵 Win＋Ctrl＋L。[官方] [ms-lc]
- **限制**：只辨識說話，掌聲與音樂不辨識，歌詞也不可靠；吃資源的 App 可能讓字幕延遲或漏掉。**沒有歷史紀錄，也沒有匯出。** [官方] [ms-lc]
- **評價**：
  - Tom's Hardware 用一段作者不懂的語言影片實測，翻譯錯得很嚴重，並把它列為最沒用的 Copilot+ 獨占功能之一。[媒體] [Tom's Hardware][toms]
  - LaptopMag 在 Computex 試用時，「圖書館在哪裡」被譯成「它在哪裡」。[媒體] [LaptopMag][laptopmag]
  - Insider 版的已知問題：播放聲音時切換語言會讓即時字幕當掉。[官方] [ms-insider]

#### Google Chrome 即時字幕（Live Caption）與即時翻譯（Live Translate）
- **聲音來源**：Chrome 瀏覽器裡的影片、Podcast、遊戲、直播、視訊通話。**官方說明只寫了 Chrome 內的聲音，沒有說會擷取整個系統的聲音。** [官方] [Chrome 說明][chrome]
- **語言**：
  - 字幕語言要先下載語言包。
  - 官方範例列出的來源語言有 en、da、nl、fr、de、hi、id、it、ja、ko、no、pl、pt、es、zh-Hans、zh-Hant、sv、th、tr，前面寫「例如」，所以不一定是完整清單。
  - 翻譯可以輸出 100 多種語言。[官方] [chrome]
- **隱私**：字幕「在本機處理、不儲存、不離開裝置」；但翻譯要「把字幕送到 Google 翻譯」，也就是**文字會上傳雲端**。[官方] [chrome]
- **介面**：字幕框可以拖曳、收合或展開；字型、大小、顏色依系統的字幕偏好設定。沒有歷史紀錄。[官方] [chrome]
- **平台**：Windows、Mac、Linux、Chromebook。說明頁的翻譯語言清單放在 Chromebook Plus 那一節下。

#### Android 即時字幕與 Pixel 即時翻譯
- 即時字幕可以在 Android 10 以上的許多裝置上使用；通話字幕只有 Pixel 有。用 Tensor 晶片的 Pixel 可以在本機翻譯，其他手機多半要連網走雲端。[媒體] [Android Authority][aa-pixel]
- 目前的完整語言清單在官方說明頁找不到（媒體整理的版本是 2021–2023 年的）。**未證實。**

#### Apple 即時字幕（Live Captions）與即時翻譯（Live Translation，iOS／macOS 26 起）
- **即時字幕**：
  - 可以在電腦播放的聲音與麥克風之間切換；聲音在本機處理；只支援 Apple silicon Mac。[官方] [Apple Mac 使用手冊][apple-lc]
  - 字幕語言（feature availability 頁，2026-11 擷取）：粵語、多種英文、法文、德文、**日文**、韓文、**台灣華語**、中國大陸華語、西班牙文。**即時字幕本身不翻譯。** [官方] [apple-avail]
- **即時翻譯**：
  - 只在三個 App 裡運作：訊息的文字翻譯、FaceTime 一對一通話的翻譯字幕（顯示在即時字幕視窗）、電話的語音與文字翻譯。**不翻譯一般影片或系統聲音。**
  - 需要 Apple Intelligence；模型「完全在裝置上執行」。[官方] [Apple：Mac 的即時翻譯][apple-lt]
  - 電話與 FaceTime 的語言：簡體與繁體華語、英文、法文、德文、義大利文、**日文**、韓文、巴西葡萄牙文、西班牙文。[官方] [apple-avail]

#### YouTube 自動字幕與自動翻譯
- **一般影片**：自動字幕支援 60 多種語言（含 ja），影片上傳後不一定馬上就有字幕。[官方] [YouTube 說明][yt]
- **直播**：
  - **自動字幕只支援英文**，而且只有「一般延遲」的直播才有；超低延遲與低延遲的直播（例如手機直播）不支援。
  - 這個功能正在逐步開放給訂閱數超過 1,000 的英文頻道。
  - 直播字幕不會保留，存檔影片的字幕會重新產生，內容可能和直播時不同。[官方] [yt]
- **對 kikitori 的意義**：日文 VTuber 直播沒有 YouTube 自動字幕，所以也不能用 YouTube 的「自動翻譯」。第三方擴充功能也受這個限制：沉浸式翻譯（Immersive Translate）明說直播翻譯「需要直播有字幕」，沒字幕就無法即時翻譯。[廠商] [Immersive Translate][immersive]

#### Twitch
- 找不到 Twitch 官方「內建自動字幕」的文件。實務上是實況主用 OBS 外掛或擴充功能加上字幕，觀眾無法自己開。（只有廠商部落格的說法，**未證實**。）

### 1.2 會議工具內的翻譯字幕

| 產品 | 授權與價格 | 語言 | 重點 | 來源 |
|---|---|---|---|---|
| **Microsoft Teams** 即時翻譯字幕 | 需要 Teams Premium（**每位使用者每月 US$10，年繳**）或 Copilot 附加授權；會議召集人有授權時，所有與會者都能用 | 說明頁列出 57 種字幕語言、31 種翻譯語言；大型會議與直播活動最多可以預選 6 種翻譯語言（Premium 為 10 種） | 桌面版可以調字幕字型大小與顏色、顯示更多行、移動字幕窗格；**字幕不會保存**，要逐字稿得另外開啟轉錄；會用會議主旨、與會者名稱、附件等資訊提升準確度；不雅字詞以星號遮蔽 | [官方] [Teams 即時字幕][teams]、[Teams Premium][teams-price] |
| **Zoom** 翻譯字幕 | 2022 年推出時是付費附加功能，每位使用者每月 US$5（**目前價格未證實**）；部分方案已內含 | 2022 年推出時 9 種，加上中、日、韓的 beta；Zoom 部落格後來寫 35 種（**數字在各來源之間不一致**） | 以「說話語言＋字幕語言」為一組；雲端錄影的字幕不會翻譯 | [官方] [Zoom 公告][zoom-news]、[Zoom 部落格][zoom-blog] |
| **Google Meet** | 2025-01-22 起，翻譯字幕只開放給 Gemini 相關附加方案 | 2024 年 6 月：69 種語言、4,600 多種語言組合 | 2026-10 起推出「語音翻譯＋同步的翻譯字幕」，支援 63 種語言、**會自動偵測說話語言（包括講到一半換語言）**；基本方案每場會議上限 90 分鐘 | [官方] [Workspace Updates 2024][meet-2024]、[2026-10][meet-2026] |
| **DeepL Voice** | 沒有公開價格（訂閱制） | 宣稱 40 多種語言 | 支援 Teams、Zoom、Meet；有**詞彙表與「Spoken Terms」**（公司名稱、縮寫、產品名稱），可以打包成「style profile」由管理員強制套用；會議資料只存在記憶體，通話結束就刪除；面對面對話版在裝置上處理；ISO 27001、SOC 2 | [官方] [DeepL Voice][deepl] |
| **Wordly**（活動與會議） | 依使用量計費或年約 | 「數十種語言」、3,000 多種語言組合 | 自訂詞彙表、AI 逐字稿與摘要；觀眾掃 QR code 就能用，不需要安裝；資料不拿來訓練模型；沒有公開延遲數字 | [官方] [Wordly fact sheet][wordly] |

### 1.3 雲端訂閱的 App 與擴充功能

| 產品 | 聲音來源與平台 | 語言 | 延遲與準確度宣稱 | 價格 | 值得參考的功能 | 來源 |
|---|---|---|---|---|---|---|
| **Felo Subtitles／Felo Translator** | Chrome 擴充功能（Meet、YouTube Live）、Windows 10 以上、macOS 14.4 以上；Zoom 與 Teams 要用桌面 App | 辨識 43 種、翻譯 40 種 | [廠商]「1 秒內翻譯」、「高精度」 | 有免費版（時數與功能有限）；Felo Translator 每月 US$39.9、每年 US$239.99 | 自訂詞典（業界術語、品牌、專有名詞）；雙語字幕；YouTube 字幕可以下載成 .txt；**用連結分享即時字幕**（手機、平板不用登入）；Chrome 線上應用程式商店評分 3.3 | [廠商] [Felo Subtitles][felo]、[Felo 價格][felo-price]、[Chrome 線上應用程式商店][felo-cws] |
| **Otter.ai** | 桌面與手機 App；Zoom、Teams、Meet；面對面 | en、es、fr、de、**ja**（2025-11 推出即時日文）、zh-Hans；一次只能轉錄一種語言，講到一半換語言不會切換 | [廠商] 自稱準確度較高，沒有公開數字 | 免費版每月 300 分鐘、每次最多 30 分鐘；Pro US$16.99／月 | 說話者、摘要、搜尋 | [官方] [Otter 日文公告][otter-ja]、[支援語言][otter-lang]、[價格][otter-price] |
| **Notta** | 網頁與 App | 轉錄 58 種、即時翻譯 42 種 | — | Pro US$13.99／月；**即時翻譯另外加價**（單語每月 +US$6～10、雙語 +US$9～15，數字來自第三方整理） | 雙語轉錄 | [官方] [App Store][notta-app]；價格為第三方整理，**未證實** |
| **VoicePing** | 網頁、iOS、Android、PC 與 Mac 桌面版 | 2026-02 的更新寫 14 種（en、ja、ko、vi、zh、de、fr、pt、th、id、ms、hi、tr、my） | 「動態切塊」降低延遲，沒有數字 | 有免費方案；付費價格未公開 | 雙語模式；**Custom Phrase Training**（自訂詞彙也套用在字幕）；**匯出含說話者、時間碼與譯文的會議紀錄與字幕檔**；匯出前可以調字幕樣式 | [廠商] [VoicePing 2026-02 更新][voiceping] |
| **Pocketalk 即時口譯（ライブ通訳）** | 瀏覽器：可以選「麥克風的聲音」或「PC 與瀏覽器的聲音」（Android、iOS 不能用後者）；Windows 10 以上用 Edge 或 Chrome | 說明書沒有寫 | — | 有付費方案（金額沒有寫） | 雙向口譯；文字大小滑桿；語音朗讀；**口譯紀錄可以下載成 CSV**（保存約 1 天） | [官方] [ポケトーク ライブ通訳 説明書][pocketalk-live] |
| **Pocketalk S2**（翻譯機） | 專用硬體 | 92 種以上（61 種雙向語音） | — | US$299.95（S2 Plus US$349.95），沒有月費 | **官方明說不能離線使用** | [官方] [Pocketalk 比較][pocketalk-cmp]、[S2][pocketalk-s2] |
| **沉浸式翻譯（Immersive Translate）** | 瀏覽器擴充功能，**翻譯 YouTube 的現成字幕**（直播一定要先有字幕） | 翻譯 100 多種 | [廠商] 比原字幕慢約 1–2 s；主要語言組合「準確度約 80–90%」 | 有 Pro 方案（AI 產生字幕要 Pro） | 可以換 20 多種翻譯引擎；**字型、大小、顏色、底色透明度、位置、雙語上下順序與間距**都能調；存檔影片的字幕可以匯出 SRT／ASS | [廠商] [immersive] |
| **Seagull**（Bird Labs） | Mac、Windows、Linux，擷取系統聲音 | 「60+」或「40+」（同一頁寫法不一致） | 沒有數字；歌曲比說話難 | 免費試用 1 小時 | 浮動、置頂、可以移動與縮放的字幕；直接以 VTuber 觀眾為目標客群 | [廠商] [Seagull][seagull] |
| **FluentCap** | 擷取系統聲音，接 Deepgram、Gladia、AssemblyAI 等雲端 API（使用者自備金鑰） | 依服務商而定 | [廠商] 服務商 300–500 ms，FluentCap 本身另加 200–500 ms | 免費下載，服務商每小時約 US$0.15–0.40 | 錄音、逐字稿搜尋、標記重點、字幕位置與字級 | [廠商] [FluentCap][fluentcap] |

### 1.4 直播與 VTuber 相關

- **LocalVocal（OBS 外掛，免費、開源）**：
  - 用 Whisper.cpp 在本機辨識，翻譯可以選 CTranslate2 跑 M2M100，也可以接 DeepL、OpenAI 或本機 LLM。
  - 可以輸出 .txt 或 .srt，也能用 RTMP 把字幕送到 YouTube 或 Twitch。
  - 這是給**實況主**用的工具（掛在 OBS 的音源上），不是給觀眾的。[官方] [OBS 論壇資源頁][localvocal]
- **StreamTranslate**：給實況主的雲端服務，用 OBS、Streamlabs、XSplit 的瀏覽器來源顯示字幕，有 6 小時免費試用。[第三方目錄] [Capterra][streamtranslate]
- **LiveTL**：把聊天室裡的人工翻譯訊息挑出來，**不做語音辨識**。[官方] [App Store][livetl]
- **結論**：「觀眾端、擷取系統聲音、日文直播」這個定位上，商用的只有 Seagull 這類小廠，而且都是雲端服務、按時數收費。**沒有任何一家公開日文直播的準確度或延遲。**

### 1.5 雲端語音 API（品質與延遲的參考線）

| 服務 | 延遲宣稱 | 準確度宣稱 | 相關功能 | 價格 | 來源 |
|---|---|---|---|---|---|
| **Speechmatics** | 即時結果幾百毫秒內就開始出現；完整結果「1 秒內」，依準確度與延遲的設定最多 2 秒；延遲壓在 4 秒以下時準確度會稍微下降 | 「90% 以上」；自稱錯誤比 Microsoft 少 25%、比 Deepgram 少 70%（廠商自己的比較） | 55 種以上語言；**即時說話者分辨（預設 50 人）**；自訂詞典；可以裝在自己的機房 | 依秒或分鐘計費 | [廠商] [Speechmatics Real-Time][speechmatics] |
| **Deepgram Nova-3** | 串流約 300 ms | 自稱串流 WER 降低 54.3%（同一數字在不同頁面寫法不一致） | 多語串流（含 ja）；**關鍵詞提示 Keyterm**（+US$0.0013／分鐘）；說話者分辨（+US$0.0020／分鐘） | 串流單語每分鐘 US$0.0077、多語每分鐘 US$0.0092 | [官方] [Deepgram 價格][deepgram-price]、[Nova-3 公告][deepgram-nova3] |
| **Soniox** | 「句子還沒講完譯文就到」，沒有數字 | — | 日文與 60 多種語言互譯 | 即時每小時 US$0.18 | [廠商] [Soniox 日文翻譯][soniox] |
| **Gemini 3.5 Live Translate** | 「整段過程都只落後說話者幾秒」 | 沒有數字，只有客戶引言 | 70 多種語言、自動偵測；語音轉語音；Translate App 裡任何耳機都能用 | — | [官方] [Google Blog 2026-06-09][gemini35] |

> 這些都是廠商的宣稱，沒有日文直播（有 BGM、多人、口語）的獨立評測。kikitori 自己的 VTuber 評測（`experiments/vtuber_eval/`）是目前手上唯一可以重現的數字。

---

## 2. 比較矩陣（對照 kikitori）

符號：✅ 有；△ 有限制或部分；❌ 沒有；? 官方未說明。

| 項目 | **kikitori** | Windows 即時字幕 | Chrome 即時字幕／翻譯 | Apple 即時字幕／翻譯 | YouTube（直播） | Teams／Zoom／Meet | DeepL Voice | Felo | Otter／Notta | VoicePing | Pocketalk 即時口譯 | 沉浸式翻譯 | Seagull／FluentCap |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 系統播放的聲音（任何 App） | ✅ WASAPI loopback、PulseAudio monitor、ScreenCaptureKit | ✅（只有預設輸出裝置） | ❌ 只有 Chrome 內 | △ 即時字幕可以，翻譯不行 | ❌ 只在 YouTube 內 | ❌ 只在 App 內 | ❌ 只在會議內 | △ 擴充功能與桌面版 | ❌ 會議與錄音 | ? | ✅ PC 與瀏覽器的聲音 | ❌ 只翻現成字幕 | ✅ |
| 麥克風 | ✅ 口譯模式 | ✅ | ? | ✅ | — | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ | ❌ | △ |
| 只擷取單一 App 的聲音 | ❌ | ❌ | （本來就只有瀏覽器） | ❌ | — | — | — | ? | — | ? | ❌ | — | ❌ |
| 日文來源 | ✅ | ✅ | ✅ | ✅ 字幕；電話與 FaceTime 翻譯 | ❌ **直播只有英文** | ✅ | ✅ | ✅ | ✅ | ✅ | ? | 要有字幕 | ✅ |
| **繁體中文輸出** | ✅（OpenCC s2twp，台灣用語） | ❌ 只有英文與簡中 | ✅ 雲端 | ✅ 訊息、電話與 FaceTime 限定 | — | ✅ | ? | ? | ❌ | ✅（zh） | ? | ✅ | ? |
| 任意語言互譯 | ✅ 20 種任意組合 | ❌ | ✅ 輸出 100 多種 | △ | — | ✅ | ✅ | ✅ | △ | ✅ 14 種 | ? | ✅ | △ |
| 離線、本機處理 | ✅ 全部在本機（ASR＋Ollama） | ✅ | △ 字幕在本機，翻譯在雲端 | ✅ | ❌ | ❌ | △ 只有面對面版本機 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 價格 | **免費** | 免費，但要 Copilot+ PC | 免費 | 免費，但要 Apple Intelligence 機型 | 免費 | US$5–10／人／月或特定方案 | 洽詢 | US$39.9／月 | US$13.99–16.99／月（Notta 翻譯另外加價） | 有免費方案 | 有付費方案 | 有免費方案與 Pro | 試用後付費或按時數計費 |
| 硬體門檻 | CPU 也能跑（品質較差）；NVIDIA GPU 效果最好 | **NPU（Copilot+）** | 一般電腦 | Apple silicon | — | — | — | Win 10 以上、macOS 14.4 以上 | — | — | Win 10 以上 | — | — |
| 公開的延遲數字 | ✅ 實測：GPU 首個即時 0.94 s、說完到完整 0.69 s；CPU 1.63／2.85 s | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 「1 秒內」（廠商） | ❌ | ❌ | ❌ | 1–2 s（廠商） | 0.5–1 s（廠商） |
| 透明、置頂的雙語字幕 | ✅ 可以滑鼠穿透 | △ 浮動或停駐，單語 | △ 浮動氣泡 | △ | 播放器內 | 會議窗格內 | 會議內 | ✅ 雙語 | ❌ | ✅ 雙語 | 瀏覽器頁面 | 播放器內，雙語 | ✅ |
| 字幕樣式 | 字級 14–96 px、底色透明度、上或下、1–3 句 | 樣式主題、自訂 | 字型、大小、顏色（依系統設定） | ? | 播放器 | 字型、顏色、行數、位置 | ? | ? | — | 匯出前可調 | 文字大小 | **字型、顏色、底色、位置、雙語順序與間距** | 位置、字級 |
| 歷史紀錄與匯出 | △ 程式內最多 300 句，**不能匯出** | ❌ | ❌ | ❌ | ❌ | 轉錄另外開；Teams 不保存字幕 | ? | .txt | ✅ | ✅ 字幕檔、會議紀錄 | CSV | SRT／ASS | 逐字稿 |
| 詞彙表、自訂用語 | ❌ | ❌ | ❌ | ❌ | ❌ | Teams 用會議資訊 | ✅ | ✅ | ? | ✅ | ? | ❌ | ❌ |
| 說話者標示 | ❌（口譯模式靠語言判斷） | ❌ | ❌ | ❌ | ❌ | ✅ | ? | ? | ✅ | ✅ | ❌ | ❌ | ❌ |
| 學習者功能（只顯示原文、雙語） | ✅ 只顯示原文、雙語 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | 雙語 | ❌ | 雙語 | 可以隱藏對方語言 | 雙語 | 雙語 |
| 振假名、羅馬字、點字查詞 | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ | ❌ |
| 快速鍵 | Ctrl＋Alt＋K（滑鼠穿透） | Win＋Ctrl＋L | ? | ? | — | — | — | ? | — | ? | ? | ? | ? |
| 不雅字詞遮蔽 | ❌ | ✅ | ? | ? | ✅ | ✅ | ? | ? | ? | ? | ? | ? | ? |
| 介面語系 | 13 種 | 跟隨系統 | 跟隨系統 | 跟隨系統 | — | 多語 | 多語 | 多語 | 多語 | 多語 | 日、英等 | 多語 | 英 |

（振假名那一列：在這次查到的所有即時字幕產品的官方頁面上，都沒有找到振假名或羅馬字功能。Language Reactor 一類的學習工具有點字查詞，但只用在已經有字幕的 Netflix 和 YouTube 影片，而且找不到官方頁面的內容可以引用，所以不列入矩陣。）

---

## 3. 差距：kikitori 要贏過這些產品，需要補的功能與品質門檻

工作量：S 約 1–3 天、M 約 1–2 週、L 超過 2 週。「模組」用的是 kikitori 的套件名稱。新增依賴前都要先問使用者（CLAUDE.md）。

### P0：影響每天使用、幾乎每個商用產品都有的功能

| # | 項目 | 誰做得比較好（證據） | 具體做法與驗收標準 | 工作量 | 模組 |
|---|---|---|---|---|---|
| 1 | **字幕紀錄存檔與匯出** | Pocketalk 匯出 CSV、Felo 匯出 .txt、沉浸式翻譯匯出 SRT／ASS、VoicePing 匯出含說話者與時間碼的紀錄、LocalVocal 輸出 .srt。kikitori 每次開始都會清空，最多只保留 300 句 | <ul><li>字幕紀錄頁加「匯出」：SRT、WebVTT（原文與譯文各一軌，或雙語合併）、TXT、CSV（時間碼、語言、原文、譯文）。時間用 `start_sample`／`end_sample` 換算，引擎已經有這兩個值</li><li>可以選「每個 session 自動存到本機」（預設關閉，存在使用者資料夾，不上傳）</li><li>驗收：一小時的 session 匯出後，SRT 能在 VLC 和 mpv 正確顯示，時間誤差 ≤ 0.2 s</li></ul> | S–M | `app/`（ui、Rust 寫檔指令）；`lib/subtitles.ts` 的保留上限改成「畫面上 300 句、檔案不設限」 |
| 2 | **詞彙表（人名、專有名詞、慣用語）** | DeepL 的 Glossary＋Spoken Terms、Felo 自訂詞典、Wordly 自訂詞彙表、VoicePing Custom Phrase、Speechmatics 自訂詞典、Deepgram Keyterm。VTuber 的名字、粉絲稱呼、招呼語是辨識錯誤的大宗 | <ul><li>每個預設組有一份詞彙表 `{原文, 讀音（選填）, 譯名}`</li><li>ASR：把原文詞彙放進 Whisper 的 prompt（現在 media 模式已經會帶前文 prompt；要注意長度上限與幻覺風險）</li><li>翻譯：把有出現在句子裡的詞彙以「術語表」形式放進提示詞</li><li>驗收：在 `experiments/vtuber_eval` 加「含人名的子集」，詞彙表開與關的人名正確率差異要 ≥ 統計顯著；其他 CER 不能變差（bootstrap CI 跨 0）</li></ul> | M | `config`（schema v4）、`asr`（prompt）、`translate`（提示詞範本）、`pipeline`、`app/`（編輯介面）、`experiments/` |
| 3 | **即時字幕的穩定度（不閃爍、不整句重寫）** | Speechmatics 說明了即時結果與完整結果的分工，完整結果在 1–2 s 內送出。kikitori 每 700 ms 重新辨識整句並整句取代，看起來容易閃爍 | <ul><li>即時路徑改用「穩定前綴」：連續兩次辨識結果相同的前綴（LocalAgreement，Macháček et al. 2023, [arXiv:2307.14743][localagreement]）視為已確定，用實色顯示；後面還沒確定的部分淡色顯示</li><li>翻譯只對已確定的前綴做串流，或沿用前一版的譯文前綴</li><li>新增指標：**每句被改寫的字數比例**、**已確定文字被推翻的次數**</li><li>驗收：已確定文字被推翻的比例 ≤ 5%，首個即時字幕的延遲不能比現在差超過 0.2 s</li></ul> | M–L | `pipeline`、`asr`（可能要取 token 時間戳）、`app/src/lib/subtitles.ts`、`experiments/e2e_acceptance` |
| 4 | **首次執行不用先懂 Ollama** | Windows、Chrome、Apple 都是一個開關就能用；kikitori 要先裝 Ollama 再下載好幾 GB 的模型 | <ul><li>偵測不到 Ollama 時，提供引導精靈：官方下載連結、裝好後自動偵測、一鍵下載推薦的翻譯模型</li><li>提供「用範例音檔試跑」，先確認整條流程能動，不用等真的有聲音</li><li>聲音來源旁邊加**音量表**，避免選錯裝置卻沒有任何回應（Windows 即時字幕也提醒過字幕跟著預設輸出裝置）</li><li>評估內建一個小型的離線翻譯後備方案（要先查授權：NLLB 是 CC-BY-NC，不能商用；M2M100 是 MIT）</li><li>驗收：新使用者從安裝到看到第一句字幕 ≤ 10 分鐘（不含下載時間）</li></ul> | M | `app/`（ui）、`hardware`、`translate` |

### P1：拉開與內建功能距離的品質與工具性功能

| # | 項目 | 證據與理由 | 具體做法與驗收標準 | 工作量 | 模組 |
|---|---|---|---|---|---|
| 5 | **只擷取單一 App 的聲音** | Windows 即時字幕只能跟著預設輸出裝置；Chrome 只抓瀏覽器。看直播時常常同時開著 Discord 或遊戲，混進來會產生錯誤字幕 | <ul><li>Windows：WASAPI process loopback（`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`，可以只包含或排除某個行程樹，需要 build 20348 以上，[Microsoft Learn 範例][ms-procloop]）</li><li>macOS：ScreenCaptureKit 的聲音本來就是以 App 為單位過濾（`SCContentFilter` 包含特定的 `SCRunningApplication`，[WWDC22][wwdc22-sck]）</li><li>Linux：PipeWire／PulseAudio 的 sink-input</li><li>介面：聲音來源下拉加上「只聽某個 App」</li><li>驗收：同時播放兩個來源時，只出現指定 App 的字幕</li></ul> | L | `capture`、`engine`（`devices.list` 擴充）、`app/` |
| 6 | **情境預設組** | DeepL 的 style profile 把詞彙表與設定綁在一起。kikitori 的設定分散在模式、語言、模型、字幕各頁 | 預設組：「VTuber 雜談」「歌回」「遊戲實況」「會議口譯」「看劇學日文（只顯示原文＋振假名）」。每組包含語言、句尾判斷參數、過濾門檻、詞彙表、字幕樣式；可以從主視窗一鍵切換，也可以匯入與匯出（JSON，不含金鑰） | M | `config`、`pipeline`（各參數已經有）、`app/` |
| 7 | **快速鍵** | Windows Win＋Ctrl＋L；kikitori 只有 Ctrl＋Alt＋K | 新增：顯示或隱藏字幕、開始或停止、切換「顯示翻譯」、暫停或捲回最近 N 句、複製上一句；全部可以自訂，被其他程式占用時沿用現在的提示做法 | S | `app/src-tauri`、`app/` |
| 8 | **字幕樣式與無障礙** | Teams 能調字型、顏色、行數、位置；沉浸式翻譯能調字型、顏色、雙語順序與間距；Windows 有樣式主題與停駐模式；Chrome 依系統的字幕偏好 | <ul><li>字型（含 CJK 字型選擇）</li><li>原文字級可以單獨調整（現在固定是譯文的 0.62 倍）</li><li>原文與譯文的上下順序</li><li>高對比主題（黑底黃字、白底黑字）</li><li>停駐模式（佔用螢幕上緣或下緣，不蓋住影片）</li><li>字幕視窗可以往上捲看較早的句子</li><li>主視窗的介面字級縮放</li><li>不雅字詞遮蔽（選用）</li><li>驗收：WCAG 2.2 對比 ≥ 4.5:1 的預設主題；全部操作只用鍵盤就能完成</li></ul> | M | `app/`（overlay、settings） |
| 9 | **歌唱與 BGM 段落** | Windows 官方說明歌詞不可靠；Seagull 說歌回比較弱。kikitori 已經有音樂誤觸發 0 句的驗收與 BGM 強健性的評測 | 「歌回」預設組：偵測到持續的歌唱或音樂時顯示「♪」或「（歌唱中）」，不送去翻譯，避免幻覺；也可以選擇「仍然辨識歌詞」。驗收：MUSAN 音樂段落非空字幕維持 0 句，歌唱段落的幻覺率要有一個量測值 | S–M | `pipeline`（過濾）、`vad`、`app/` |
| 10 | **把延遲與品質目標寫成規格** | 商用的參考線：Speechmatics 完整結果 1–2 s、Deepgram 約 300 ms、Felo「1 秒內」 | 寫進 `docs/specs/pipeline-engine.md` 當作回歸門檻（每次發布都量）：<ul><li>**GPU 推薦組合**：首個即時字幕 P50 ≤ 1.0 s、P90 ≤ 1.5 s（現在 0.94／1.70）；即時字幕延遲 P90 ≤ 0.6 s（現在 0.49）；說完到完整字幕 P90 ≤ 1.2 s（現在 0.92）</li><li>**CPU 組合**：首個即時 P90 ≤ 2.0 s、完整 P90 ≤ 3.0 s（現在 2.00／3.16）</li><li>品質：YODAS 乾淨與 music 10 dB 的過濾後 CER 不能比上一版差（bootstrap CI）；簡體字 0；非語音幻覺 0</li></ul>CPU 組合是目前最明顯的弱點，要優先改善 | S（規格）＋M（CI 化） | `docs/specs`、`experiments/e2e_acceptance`、`experiments/vtuber_eval` |
| 11 | **自動偵測語言（包含講到一半換語言）** | Meet 2026-10 起自動偵測、能處理講到一半換語言；Gemini 3.5 也會自動偵測；Deepgram 有多語串流 | kikitori 已經有 `auto`。要加的是逐句偵測的穩定度：低信心度時沿用上一句的語言、在介面上標示偵測到的語言。驗收：日英混雜的 VTuber 片段語言錯判率要有量測值 | S–M | `pipeline`、`asr` |

### P2：學習者功能（沒有任何即時字幕產品做到，是差異化的重點）

| # | 項目 | 理由 | 具體做法 | 工作量 | 模組 |
|---|---|---|---|---|---|
| 12 | **振假名、羅馬字（日文原文）** | 這次調查的即時字幕產品都沒有這個功能；kikitori 的目標使用者包含日文學習者，也已經有「只顯示原文」 | <ul><li>只對完整字幕（`is_final=true`）做形態素分析，產生讀音，用 `<ruby>` 顯示，即時字幕不加（避免閃爍與耗資源）</li><li>候選套件要先查授權並問使用者：SudachiPy（Apache-2.0）＋SudachiDict，或 fugashi＋UniDic</li><li>可以選「只標超過 N 級的漢字」</li><li>驗收：完整字幕的延遲增加 ≤ 50 ms</li></ul> | M | 新子套件（例如 `kikitori.reading`）或放在 `pipeline` 的後處理；`app/`（overlay 的 ruby 排版） |
| 13 | **點字查詞與收藏** | Language Reactor 一類工具（只用在現成字幕）的核心功能；即時字幕產品都沒有 | 在字幕紀錄頁點原文的詞，顯示讀音、辭書形與釋義（JMdict，CC BY-SA 4.0，要標示出處）；可以加入收藏 | M–L | `app/`、新的辭典資料（下載時要驗證雜湊） |
| 14 | **匯出成 Anki 或單字卡** | 和第 1 項的匯出共用；學習者常用的流程 | 把收藏的句子匯出成 CSV 或 TSV（原文、讀音、譯文、時間碼、來源） | S | `app/` |

### P3：可以等之後再做，或要先確認和架構原則有沒有衝突

| # | 項目 | 說明 | 工作量 | 模組 |
|---|---|---|---|---|
| 15 | 說話者標示（多人連動、聯合直播） | Speechmatics、Deepgram、VoicePing、Otter 都有。本機的說話者分辨（例如 pyannote）會增加 GPU 負擔與依賴，授權也要先查。先從「依聲紋分 A／B 顏色」的簡化版開始 | L | 新子套件、`pipeline` |
| 16 | 把字幕分享到手機或其他裝置 | Felo 用分享連結、Wordly 用 QR code。**這會和架構原則「不開網路埠」衝突**，要另外寫 ADR；可以先做成預設關閉、只限區域網路、要 token 的唯讀頁面 | M | `app/src-tauri` |
| 17 | 給實況主的 OBS 輸出 | LocalVocal、StreamTranslate 已經做得很好。kikitori 的定位是觀眾，所以優先順序低；要做的話，用「寫入文字檔」就能讓 OBS 的文字來源讀取 | S | `app/` |
| 18 | 語音朗讀譯文（TTS） | Pocketalk、DeepL Voice、Gemini 都有，但對看直播的情境價值不高 | M | 新子套件 |

---

## 4. kikitori 已經贏的地方（要保住並寫進宣傳）

1. **日文直播 → 繁體中文，系統聲音，免費、離線**：同時滿足這四個條件的產品，這次一個都沒找到。
   - Windows 即時字幕不能輸出繁體中文。[ms-lc]
   - YouTube 直播的自動字幕只有英文。[yt]
   - Chrome 的翻譯要上傳雲端。[chrome]
   - Apple 的即時翻譯不處理一般影片。[apple-lt]
2. **任意語言互譯（20 種）**：Windows 只能輸出英文或簡體中文；Otter 只有 6 種轉錄語言，一次一種；VoicePing 14 種。
3. **隱私**：聲音和文字都不離開電腦（翻譯用本機的 Ollama）。在雲端產品裡，只有 DeepL 面對面版與 Apple 的通話翻譯是在裝置上處理。
4. **不用特殊硬體、不用訂閱**：
   - 不需要 Copilot+ PC 的 NPU，也不需要支援 Apple Intelligence 的機型。
   - 沒有 Teams US$10、Felo US$39.9 這類月費，也沒有 Meet 基本方案的 90 分鐘上限。[teams-price]、[felo-price]、[meet-2026]
5. **針對 VTuber 調校、有數字可查**：
   - 依 BGM 混音評測選模型：完整路徑在有 BGM 時，large-v3 明顯比 turbo 穩。
   - 有幻覺片語表與 no_speech 過濾。
   - 驗收時，靜音與純配樂段的誤觸發是 0 句。
   - 這些都公開在 `experiments/`。商用產品沒有一家公開日文直播的準確度。
6. **公開的實測延遲**：在 GPU 組合上已經達到或超過廠商宣稱的水準（說完到完整字幕中位數 0.69 s）。
7. **學習者導向的基礎**：雙語、只顯示原文、13 種介面語系、台灣用語的正體輸出（OpenCC s2twp）。在這個基礎上加振假名與查詞（P2），就會是同類產品裡唯一的「即時字幕學習工具」。
8. **依硬體推薦模型、需要時才下載**：使用者不用懂模型大小與 VRAM。Windows 和 Apple 是直接用硬體門檻擋掉。

## 5. 要誠實面對的弱點（宣傳時不要說過頭）

- **CPU 組合的品質與延遲**：tiny／base 在 VTuber 片段錯字多，完整字幕延遲中位數 2.85 s。商用的雲端服務在一般筆電上沒有這個問題。
- **絕對準確度沒有和競品比過**：YODAS 過濾後的 CER 23–35%，主要是因為參考字幕不是逐字稿，所以只能做相對比較。**建議在同一組 VTuber 片段上錄下 Windows 即時字幕（en 輸出）與 Chrome 即時字幕（ja 原文）的輸出，算 CER 與人工評分**，作為「比內建好」的直接證據（M，`experiments/`）。
- **要裝 Ollama**：這是首次使用最大的門檻（P0 第 4 項）。
- **Linux 的 Wayland 與 macOS 全螢幕 Space**：置頂與滑鼠穿透還沒在實機確認（`docs/specs/ui.md`）。Windows 與 Apple 的內建功能沒有這個問題。

## 6. 沒能證實或要再查的項目

- Zoom 翻譯字幕現在的價格與語言數（官方說明頁沒有抓到；各來源寫 12、35、36 種）。
- DeepL Voice 的語言數，分辨不出輸入與輸出各是多少（產品頁寫「40+」，部落格寫「17 種語音輸入、35 種字幕」）。
- Android 與 Pixel 的即時字幕和即時翻譯，目前的語言清單。
- Felo Subtitles 擴充功能的實際價格；Notta 加購項目的官方價格頁。
- UD トーク 有沒有振假名顯示（產品介紹寫「漢字かな変換」，但官方頁沒有詳細說明）。
- Language Reactor 的官方功能頁（這次抓到的是空白頁），所以學習工具只當作背景，沒有拿來當證據。
- Apple feature availability 頁在 2026-11 的標題是新版 macOS（不是 Tahoe），語言清單以那時擷取的內容為準。

---

## 來源

[ms-lc]: https://support.microsoft.com/windows/b52da59c-14b8-4031-aeeb-f6a47e6055df
[ms-insider]: https://blogs.windows.com/windows-insider/2024/12/18/releasing-real-time-translation-in-live-captions-to-more-copilot-pcs-in-the-dev-channel/
[toms]: https://www.tomshardware.com/software/windows/microsofts-copilot-pc-exclusive-features-are-a-bad-joke-even-for-ai-fans
[laptopmag]: https://laptopmag.com/software/i-broke-copilots-live-captions-feature-with-a-simple-question
[chrome]: https://support.google.com/chrome/answer/10538231?hl=en
[aa-pixel]: https://androidauthority.com/google-pixel-live-translate-3221949
[apple-lc]: https://support.apple.com/guide/mac-help/mchla0b36db8
[apple-lt]: https://support.apple.com/guide/mac-help/mchl58dfbdba/26/mac/26
[apple-avail]: https://www.apple.com/ca/macos/feature-availability
[yt]: https://support.google.com/youtube/answer/6373554?hl=en
[immersive]: https://immersivetranslate.com/en/video/youtube-live-streaming-translator/
[teams]: https://support.microsoft.com/en-us/office/use-live-captions-in-microsoft-teams-meetings-4be2d304-f675-4b57-8347-cbd000a21260
[teams-price]: https://www.microsoft.com/en-us/microsoft-teams/premium
[zoom-news]: https://news.zoom.com/bridge-the-language-gap-and-connect-people-with-zoom-translated-captions/
[zoom-blog]: https://www.zoom.com/en/blog/translated-captions
[meet-2024]: https://workspaceupdates.googleblog.com/2024/06/more-languages-for-captions-and-translated-captions-in-google-meet.html
[meet-2026]: https://workspaceupdates.googleblog.com/2026/10/real-time-speech-translation-in-google-Meet-expands-to-63-languages-with-automatic-language-detection.html
[deepl]: https://www.deepl.com/en/products/voice
[wordly]: https://www.wordly.ai/fact-sheet
[felo]: https://subtitles.felo.me/en
[felo-price]: https://felo.me/pricing
[felo-cws]: https://chromewebstore.google.com/detail/felo-subtitles-chatgpt-li/ponokiofkijoolhebggofhhibnafebna
[otter-ja]: https://otter.ai/blog/konnichiwa-otter-ai-expands-to-japanese-market-with-new-japanese-language-support-for-ai-meeting-agent
[otter-lang]: https://help.otter.ai/hc/en-us/articles/360047247414-Supported-languages
[otter-price]: https://otter.ai/pricing
[notta-app]: https://apps.apple.com/app/id1480649572
[voiceping]: https://voiceping-landingpage-prod.pages.dev/en/blog/product-update-feb-2026/
[pocketalk-live]: https://pocketalk.jp/hubfs/images/product/forbusiness/livetranslation/manual/JP/index.html
[pocketalk-cmp]: https://www.pocketalk.com/compare/
[pocketalk-s2]: https://pocketalk.com/product/pocketalk-s2-5-year-esim-white
[seagull]: https://seagull-landing-page.pages.dev/blog/watch-vtuber-streams-english
[fluentcap]: https://fluentcap-landing.pages.dev/blog/gaming-stream-live-subtitles
[localvocal]: https://obsproject.com/forum/resources/localvocal-local-live-captions-translation-on-the-go.1769
[streamtranslate]: https://www.capterra.com/p/10039113/StreamTranslate/
[livetl]: https://apps.apple.com/us/app/livetl/id1550420049
[speechmatics]: https://www.speechmatics.com/product/real-time
[deepgram-price]: https://deepgram.com/pricing
[deepgram-nova3]: https://deepgram.com/learn/introducing-nova-3-speech-to-text-api
[soniox]: https://soniox.com/speech-translation/japanese
[gemini35]: https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/
[ms-procloop]: https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/
[wwdc22-sck]: https://developer.apple.com/videos/play/wwdc2022/10155/
[localagreement]: https://arxiv.org/abs/2307.14743

- Windows 即時字幕：<https://support.microsoft.com/windows/b52da59c-14b8-4031-aeeb-f6a47e6055df>
- Windows Insider（Intel／AMD 翻譯、已知問題）：<https://blogs.windows.com/windows-insider/2024/12/18/releasing-real-time-translation-in-live-captions-to-more-copilot-pcs-in-the-dev-channel/>
- Tom's Hardware：<https://www.tomshardware.com/software/windows/microsofts-copilot-pc-exclusive-features-are-a-bad-joke-even-for-ai-fans>
- LaptopMag：<https://laptopmag.com/software/i-broke-copilots-live-captions-feature-with-a-simple-question>
- Chrome 字幕與翻譯：<https://support.google.com/chrome/answer/10538231?hl=en>
- Android Authority（Pixel Live Translate）：<https://androidauthority.com/google-pixel-live-translate-3221949>
- Apple 即時字幕（Mac）：<https://support.apple.com/guide/mac-help/mchla0b36db8>
- Apple 即時翻譯（Mac）：<https://support.apple.com/guide/mac-help/mchl58dfbdba/26/mac/26>
- Apple feature availability：<https://www.apple.com/ca/macos/feature-availability>
- YouTube 自動字幕：<https://support.google.com/youtube/answer/6373554?hl=en>
- Immersive Translate 直播翻譯：<https://immersivetranslate.com/en/video/youtube-live-streaming-translator/>
- Teams 即時字幕：<https://support.microsoft.com/en-us/office/use-live-captions-in-microsoft-teams-meetings-4be2d304-f675-4b57-8347-cbd000a21260>
- Teams Premium 價格：<https://www.microsoft.com/en-us/microsoft-teams/premium>
- Zoom 翻譯字幕公告：<https://news.zoom.com/bridge-the-language-gap-and-connect-people-with-zoom-translated-captions/>、<https://www.zoom.com/en/blog/translated-captions>
- Google Meet：<https://workspaceupdates.googleblog.com/2024/06/more-languages-for-captions-and-translated-captions-in-google-meet.html>、<https://workspaceupdates.googleblog.com/2026/10/real-time-speech-translation-in-google-Meet-expands-to-63-languages-with-automatic-language-detection.html>
- DeepL Voice：<https://www.deepl.com/en/products/voice>
- Wordly：<https://www.wordly.ai/fact-sheet>
- Felo：<https://subtitles.felo.me/en>、<https://felo.me/pricing>、<https://chromewebstore.google.com/detail/felo-subtitles-chatgpt-li/ponokiofkijoolhebggofhhibnafebna>
- Otter：<https://otter.ai/blog/konnichiwa-otter-ai-expands-to-japanese-market-with-new-japanese-language-support-for-ai-meeting-agent>、<https://help.otter.ai/hc/en-us/articles/360047247414-Supported-languages>、<https://otter.ai/pricing>
- Notta（App Store）：<https://apps.apple.com/app/id1480649572>
- VoicePing：<https://voiceping-landingpage-prod.pages.dev/en/blog/product-update-feb-2026/>
- Pocketalk：<https://pocketalk.jp/hubfs/images/product/forbusiness/livetranslation/manual/JP/index.html>、<https://www.pocketalk.com/compare/>、<https://pocketalk.com/product/pocketalk-s2-5-year-esim-white>
- Seagull：<https://seagull-landing-page.pages.dev/blog/watch-vtuber-streams-english>
- FluentCap：<https://fluentcap-landing.pages.dev/blog/gaming-stream-live-subtitles>
- LocalVocal：<https://obsproject.com/forum/resources/localvocal-local-live-captions-translation-on-the-go.1769>
- StreamTranslate（Capterra）：<https://www.capterra.com/p/10039113/StreamTranslate/>
- LiveTL：<https://apps.apple.com/us/app/livetl/id1550420049>
- Speechmatics：<https://www.speechmatics.com/product/real-time>
- Deepgram：<https://deepgram.com/pricing>、<https://deepgram.com/learn/introducing-nova-3-speech-to-text-api>
- Soniox：<https://soniox.com/speech-translation/japanese>
- Gemini 3.5 Live Translate：<https://blog.google/innovation-and-ai/models-and-research/gemini-models/gemini-live-3-5-translate/>
- Windows process loopback：<https://learn.microsoft.com/en-us/samples/microsoft/windows-classic-samples/applicationloopbackaudio-sample/>
- ScreenCaptureKit（WWDC22）：<https://developer.apple.com/videos/play/wwdc2022/10155/>
- LocalAgreement（Macháček et al., 2023）：<https://arxiv.org/abs/2307.14743>
