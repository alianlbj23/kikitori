# kikitori（聞き取り）

**繁體中文** | [简体中文](README.zh-CN.md) | [English](README.md) | [日本語](README.ja.md) | [한국어](README.ko.md) | [Español](README.es.md) | [Français](README.fr.md) | [Deutsch](README.de.md) | [Português (Brasil)](README.pt-BR.md) | [Русский](README.ru.md) | [Tiếng Việt](README.vi.md) | [ไทย](README.th.md) | [Bahasa Indonesia](README.id.md)

kikitori 聽你電腦正在播放的聲音（或麥克風），即時辨識成文字、翻譯成你選的語言，用浮在畫面上的雙語字幕顯示。
語音辨識（Whisper）與翻譯（本機 Ollama）都在你自己的電腦上執行：聲音與文字不會上傳到任何雲端服務。

主要情境是**看日語 VTuber 直播**（有 BGM、遊戲音效、直播用語），模型推薦與參數都依本專案的實測調整過；
20 種語言之間的其他組合也能用。

- 平台：Windows 10/11 x64（主要驗證平台）、Linux x64。macOS（Apple Silicon）還沒有在實機上測試，暫時不提供安裝檔。
- 授權：MIT（各模型另有授權，見「授權」）
- 目前版本：0.3.1（[更新內容](docs/release-notes/v0.3.1.md)）

## 功能

| 功能 | 說明 |
|---|---|
| 影片翻譯 | 擷取系統播放的聲音（Windows WASAPI loopback／Linux PulseAudio monitor／macOS ScreenCaptureKit），把收聽語言翻成字幕語言 |
| 即時口譯 | 擷取麥克風；你說「我的語言」時譯成對方的語言，對方說話時譯成你的語言 |
| 先快後準的雙路徑字幕 | 句子還在說時，先用較快的模型出「即時字幕」；說完後用較準的模型重算「完整字幕」取代它 |
| 穩定的即時字幕 | 即時字幕的原文與譯文中已經不會再變的開頭正常顯示、還在變動的尾巴淡色顯示；新出現的字淡入，整塊不會上下跳 |
| 說話者標籤 | 多人連動時在每句前標出「說話者 1、2…」，可自動判斷人數或指定 2–6 人，點標籤可以改名；口譯模式標出「你／對方」 |
| 20 種語言任意組合 | 收聽語言（可選「自動偵測」）與字幕語言從 20 種語言中任選，見「支援的語言」 |
| 13 種介面語言 | 預設跟隨作業系統，可在設定頁切換 |
| 只顯示原文 | 關掉「顯示翻譯」就只看原文：不需要 Ollama、不佔 GPU；執行中也能切換 |
| 振假名 | 在日文原文的漢字上標讀音（SudachiDict，第一次打開時才下載約 69 MiB） |
| 依硬體推薦模型 | 偵測 CPU、記憶體、NVIDIA 顯示卡與 Ollama，自動選擇模型組合；第一次按「開始」時才下載缺少的模型 |
| 術語表 | 內建 53 個日語直播用語（赤スパ、推し、メン限…）與中翻日的中日易錯詞，也可以自訂詞條、匯入／匯出 CSV |
| 原文校正與本場資訊 | 把辨識常錯的字自動改對；填寫主播名、遊戲名或直播說明欄，讓名字翻得一致 |
| 匯出字幕紀錄 | SRT、VTT、TXT、CSV；可設定每次結束時自動存檔 |
| OBS 文字檔 | 把目前字幕寫到文字檔，給 OBS「文字 (GDI+)」來源的「從檔案讀取」使用；不開網路埠 |
| 字幕視窗 | 透明、置頂、可滑鼠穿透；字級、背景、位置、句數、每句至少顯示多久、閒置多久淡出、原文大小與位置、每行寬度都可調；可暫停與回看最近字幕 |
| 狀態列 | 音量表、延遲（說完到完整字幕的中位數）；辨識跟不上、15 秒沒有聲音或背景音太大聲時提醒 |
| 模型管理 | 下載、查看磁碟用量、刪除辨識模型；替你在 Ollama 下載翻譯模型 |
| 自動更新 | 啟動時檢查 GitHub Releases，下載並驗證簽章後安裝 |

## 支援的語言

收聽語言與字幕語言（20 種，任意組合）：

| | | | | |
|---|---|---|---|---|
| 日本語 | English | 繁體中文（台灣） | 简体中文 | 한국어 |
| Español | Français | Deutsch | Português | Русский |
| Italiano | العربية | हिन्दी | Bahasa Indonesia | Tiếng Việt |
| ไทย | Türkçe | Polski | Nederlands | Українська |

- 繁體中文字幕以 OpenCC `s2twp` 轉成台灣正體與台灣用語（例如 视频 → 影片），只在譯文出現簡體字時才轉，不會把「台灣」改成「臺灣」。
- 收聽與字幕是同一種語言時不翻譯（繁中與簡中之間只做字形轉換）。
- 語音辨識的品質依語言而不同。模型對某個語言的錯誤率超過 20% 時（例如 large-v3-turbo 的泰語、阿拉伯語、印地語，large-v3 的越南語），推薦說明會提示「品質較差」；泰語、阿拉伯語、印地語在 8 GB 以上的顯示卡上即時字幕也改用 large-v3。

介面語言（13 種）：繁體中文（台灣）、简体中文、English、日本語、한국어、Español、Français、Deutsch、Português (Brasil)、Русский、Tiếng Việt、ไทย、Bahasa Indonesia。
阿拉伯語可以當收聽或字幕語言，但沒有阿拉伯語介面（介面不支援由右到左的版面）。

## 推薦組合（依硬體）

kikitori 會自動偵測並套用，下表只是讓你知道會拿到什麼。依據是本專案的評測（`experiments/vtuber_eval/`、`experiments/translate_eval/`），規則與算式見 [`docs/specs/hardware.md`](docs/specs/hardware.md)。

| 你的電腦 | 即時字幕 | 完整字幕 | 翻譯（Ollama） |
|---|---|---|---|
| NVIDIA 16 GB 以上 | large-v3-turbo（float16） | large-v3（float16） | qwen3.5:9b |
| NVIDIA 12 GB（例如 RTX 5070） | large-v3-turbo（int8_float16） | large-v3（int8_float16） | qwen3.5:9b |
| NVIDIA 8–11 GB | large-v3-turbo（顯示記憶體剛好 8 GB 下限時改 small） | large-v3（依顯示記憶體選精度） | qwen3.5:4b |
| NVIDIA 4–8 GB | base | large-v3-turbo | qwen3.5:4b |
| NVIDIA 不到 4 GB | base | small | qwen3.5:4b |
| 沒有 NVIDIA GPU，RAM ≥ 16 GB、5 核以上 | base（CPU） | small（CPU） | qwen3.5:4b |
| 沒有 NVIDIA GPU，RAM 較少或 4 核以下 | tiny（CPU） | base（CPU） | qwen3.5:4b |
| Mac（Apple Silicon） | base（CPU） | small（CPU） | 依統一記憶體大小（16 GB 為 qwen3.5:9b） |

- 精度 int8_float16 與 float16 的辨識品質實測相同（差異不顯著），只是顯示記憶體約減半；與翻譯模型共用顯示卡時，kikitori 優先讓翻譯模型用大一級的。
- 辨識模型下載量：large-v3-turbo 1.62 GB、large-v3 3.09 GB、small 487 MB、base 148 MB、tiny 78 MB。翻譯模型：qwen3.5:9b 約 6.6 GB、qwen3.5:4b 約 3.3 GB。
- NVIDIA GPU 需要 cuBLAS 12（約 570 MB）。kikitori 不隨安裝檔附帶；在模型頁同意 NVIDIA EULA 後一鍵下載。沒裝時會改用 CPU。
- 翻譯伺服器在其他電腦（遠端）時，不會自動選翻譯模型，請在模型頁自己選。
- 翻譯一律優先推薦 Qwen3.5（Apache-2.0）：顯示記憶體夠就 9B，否則 4B。顯示卡 4 GB 以下或沒有 NVIDIA GPU 時也是 qwen3.5:4b，Ollama 會把放不下的部分放到 CPU 執行，速度較慢（這種情況的翻譯延遲還沒有實測）。
- 騰訊 HY-MT 有地區限制，只在你於設定頁勾選「我不在歐盟、英國或南韓使用」後才可能被推薦；Sakura（非商用授權）永遠不會被推薦，只能手動選。
- macOS 與 Linux 的辨識速度、CPU 上的翻譯速度都還沒有實測（見「已知限制」）。

## 開始使用

1. 從 [GitHub Releases](https://github.com/alianlbj23/kikitori/releases) 下載安裝檔（各平台的安裝提示寫在 Release 頁面：未簽章的 SmartScreen／Gatekeeper 警告、Linux 的 fuse3 與字型）。
2. 要翻譯的話，安裝 [Ollama](https://ollama.com/download) 並讓它在背景執行。kikitori 偵測不到時會提示「安裝 Ollama」或改用「只顯示原文」。
3. 開啟 kikitori。第一次啟動會依硬體自動套用推薦組合。
4. 在設定頁的「語言」選收聽語言與字幕語言。選擇後只會顯示推薦組合與下載大小，**不會立刻下載**。
5. 選聲音來源，按「開始」。缺少的辨識模型與翻譯模型（Ollama 時）會先下載，完成後自動開始。

### 只顯示原文

字幕紀錄頁工具列與設定頁「字幕」區塊的「顯示翻譯」開關（或快速鍵 Ctrl+Alt+J）。關閉時：

- 引擎完全不連翻譯伺服器，不需要安裝 Ollama，也不佔用 GPU 給翻譯模型；
- 字幕視窗把原文改成主要字幕的字級與顏色；
- 執行中切換即時生效，已顯示的句子不會補翻。

### 振假名

設定頁「在日文原文上標振假名」：收聽語言是日語時，在原文的漢字上方標上平假名讀音（送り仮名不重複標）。第一次打開時下載 SudachiDict core（Apache-2.0，約 69 MiB），模型頁可以查看用量與刪除。只影響畫面，匯出的字幕不變。設計見 [`docs/specs/furigana.md`](docs/specs/furigana.md)。

### 術語表

設定頁「術語表」：

- **內建直播用語表**（預設開）：原文是日語、字幕是繁中／簡中／英文／韓文時使用，例如 スパチャ → 超級留言、メン限 → 會員專屬、切り抜き → 精華剪輯。只有這句原文實際出現的詞才放進翻譯提示詞（每句最多 8 條）。
- **中日易錯詞**（跟著內建表的開關）：原文是中文、字幕是日文時，提示中文與日文寫法相同或相似、意思不同的詞，例如 便當 → 弁当、節目 → 番組、老婆 → 妻；中翻日也改用中文寫給模型的指示，減少模型用日文的意思讀中文。
- **自訂詞條**：原文、譯文、適用的字幕語言（或「全部語言」），最多 500 筆，適用於任何收聽語言；同一個寫法時優先於內建表。比對不分大小寫與全形／半形。
- 可以匯入／匯出 CSV（UTF-8，逗號、分號或 Tab 分隔，Excel 存的檔案也能讀）。按「儲存術語表」後，下一次開始字幕時生效。

### 匯出字幕紀錄

字幕紀錄頁的「匯出」：SRT、VTT、TXT、CSV，內容可選「原文與譯文」「只有原文」「只有譯文」。只匯出完整字幕（即時字幕還在變動）。
檔案存在 `文件/kikitori/transcripts/YYYY-MM-DD_HHMMSS_<模式>.<副檔名>`，時間碼從開始字幕時算起。
設定頁可以開「每次結束時自動存檔（SRT 與 TXT）」；按「停止」後會等最後一句的完整字幕送到（最多 2 秒）再存。

### OBS 文字檔

設定頁「字幕紀錄與 OBS」→「輸出字幕到文字檔（給 OBS）」：原文、譯文或兩者。
kikitori 把字幕視窗目前的句子覆寫到 `文件/kikitori/obs/subtitle.txt`（UTF-8，原子寫入，每秒最多 10 次）；
在 OBS 新增「文字 (GDI+)」來源並勾「從檔案讀取」指到這個檔案。字幕結束、閒置淡出或清除字幕時檔案會清空。
不開任何網路埠，所以沒有用 OBS「瀏覽器」來源的做法。

### 全域快速鍵

| Windows／Linux | macOS | 動作 |
|---|---|---|
| Ctrl+Alt+K | ⌘+Option+K | 切換字幕視窗的滑鼠穿透 |
| Ctrl+Alt+J | ⌘+Option+J | 切換「顯示翻譯」 |
| Ctrl+Alt+U | ⌘+Option+U | 清除字幕視窗（與 OBS 文字檔）；字幕紀錄不受影響 |
| Ctrl+Alt+P | ⌘+Option+P | 暫停／恢復字幕視窗；字幕紀錄照常 |
| Ctrl+Alt+H | ⌘+Option+H | 在字幕視窗暫時顯示最近 10 句 |
| Ctrl+Alt+R | ⌘+Option+R | 口譯時在字幕視窗用對方的語言顯示「請再說一次」 |

被其他程式占用的快速鍵會在字幕紀錄頁標示「無法使用」，不影響其他快速鍵。Linux Wayland 可能完全無法註冊全域快速鍵。

## 隱私與離線

- 聲音、辨識結果與譯文都只在你的電腦上處理。翻譯預設送到本機的 Ollama（`http://localhost:11434/v1`）；你若把翻譯服務改成其他網址，字幕文字會送到那裡。
- 會連網的只有：下載模型（辨識模型來自 Hugging Face、翻譯模型經 Ollama、GPU 元件來自 PyPI，都是你按下開始或下載時才發生）、啟動時檢查更新（GitHub Releases）。模型都下載好之後，字幕本身可以完全離線使用。
- API 金鑰只從環境變數讀（設定裡只存變數名稱，預設 `KIKITORI_TRANSLATE_API_KEY`），不寫進任何檔案。
- 「關於」頁的「複製診斷資訊」只複製到剪貼簿，會遮蔽金鑰、網址中的帳密與使用者名稱，術語表只列筆數。
- 引擎與桌面程式之間只用標準輸入輸出溝通，不開網路埠。

## 0.3.0 新功能

摘要（完整說明與升級注意事項見 [docs/release-notes/v0.3.0.md](docs/release-notes/v0.3.0.md)；0.2.0 見 [docs/release-notes/v0.2.0.md](docs/release-notes/v0.2.0.md)）：

- 說話者標籤：多人連動時每句標出「說話者 1、2…」，可自動判斷人數或指定 2–6 人，點標籤改名、同名即合併（模型約 27 MiB，第一次選擇時下載）
- 即時譯文分成穩定與變動兩段，不再整句閃；新字淡入、字幕不上下跳
- 口譯模式標出「你／對方」，Ctrl+Alt+R 用對方的語言顯示「請再說一次」
- 原文校正規則、從字幕紀錄選字加入術語表、本場資訊（主播名、遊戲名、直播說明欄）
- 閱讀版面：原文大小與位置、每行寬度、依字數延長顯示時間、振假名只標第一次
- 新快速鍵 Ctrl+Alt+P 暫停字幕、Ctrl+Alt+H 回看最近字幕；重設字幕視窗位置；背景音太大聲提醒

## 已知限制

- **只在 Windows（RTX 5070）上完整實測**。macOS 與 Linux 的實機擷取、Apple Silicon 與 CPU 上的翻譯速度都沒有量過；Linux 只在 Docker 容器中驗收過。
- **新推薦組合只在 RTX 5070（12 GB）上端到端量測**：turbo＋large-v3（int8_float16）與 qwen3.5:9b 同時執行時整張卡最高 10.8 GB、即時字幕延遲約 0.31 秒、說完到完整字幕約 0.92 秒（中位數，見 [`tasks/acceptance-report-v0.2.0.md`](tasks/acceptance-report-v0.2.0.md)）。
- **辨識準確度的評測素材不是 VTuber**：用的是 YouTube 一般口語（YODAS）、朗讀與電視語音，加上混入的 BGM；沒有歌回、尖叫、多人連動的資料。歌聲會穿過人聲偵測，可能出現奇怪的字幕。
- **說話者標籤只在拼接的 YouTube 片段上量過**（2 人約 92–100%、3–4 人約 80–85% 標對）：沒有搶話、不到 1 秒的附和聲與同一人切換聲線的資料，真實連動可能較低；不寫進 OBS 文字檔。
- 英文以外的語言只有公開基準與非語音的幻覺測試，沒有在真實語音上實測；幻覺片語表沒有經過母語者審閱。
- 有些片尾套話（例如「チャンネル登録よろしくお願いします」）會被當成 Whisper 幻覺整句濾掉，即使直播主真的說了。
- 需要另外安裝 Ollama 才能翻譯；Ollama 以外的 OpenAI 相容伺服器只能自己安裝模型。
- 長時間執行的記憶體壓力測試在 Windows 上做過（引擎 20 分鐘 session 持平、主視窗 22 分鐘 +0.7 MiB／分）；macOS、Linux 沒有測。
- 同一時間只能有一個字幕 session；裝置被拔掉後不會自動重連。
- 不支援：點字查詞、只擷取單一 App 的聲音、歌回模式、一句之中換人或多人同時說話的分離、OBS「瀏覽器」來源（原因見 [`docs/specs/competitive-features.md`](docs/specs/competitive-features.md)「先不做」與 [`docs/adr/`](docs/adr/)）。
- 安裝檔沒有付費程式碼簽章（Windows SmartScreen、macOS Gatekeeper 會警告）。

## 授權

kikitori 本身以 MIT 授權發布（[LICENSE](LICENSE)）。第三方元件的授權全文在安裝目錄，可從「關於」頁開啟。

模型不隨安裝檔散布，由你下載時適用各自的授權：

| 模型 | 授權 | 注意 |
|---|---|---|
| Whisper（CTranslate2 轉換版） | MIT | — |
| Silero VAD（隨程式附帶） | MIT | — |
| 3D-Speaker CAM++（說話者標籤，選用時下載） | Apache-2.0 | — |
| Qwen3.5（預設翻譯模型） | Apache-2.0 | — |
| 騰訊 HY-MT1.5 | Tencent HY Community License | **不適用於歐盟、英國、南韓**；kikitori 預設不推薦，需在設定頁確認所在地區 |
| Sakura-GalTransl | CC BY-NC-SA 4.0 | **禁止商業用途**；不會被推薦，只能手動選 |
| NVIDIA cuBLAS | NVIDIA CUDA EULA | 下載前需同意 |

## 給開發者

- 架構：[docs/architecture.md](docs/architecture.md)；各模組規格：[docs/specs/](docs/specs/)；學習指南：[docs/learning/](docs/learning/README.md)
- 評測與實驗：`experiments/*/README.md`；調查報告：`reports/`

```text
uv sync                                       安裝 Python 依賴
uv run python -m unittest discover -s tests   Python 測試
uv run ruff check                             Lint
uv run kikitori                               互動選單（啟動引擎、列出音訊裝置…）
cd app && npm ci && npm run menu              桌面程式的開發選單（需要 Node 與 Rust）
cd app && npm test && npx tsc --noEmit        前端測試與型別檢查
```
