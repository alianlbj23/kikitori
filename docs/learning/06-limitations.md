# 06 已知限制與尚未驗證的地方

整理自各規格的「已知限制」段落、實驗 README、`tasks/acceptance-report.md` 與 CI 設定（對照 v0.2.0，`feat/vtuber-models`）。
分成兩類：**限制**（已知做不到或有代價）、**未驗證**（可能沒問題，但沒有人實際確認過）。

## 一眼看懂：驗證到什麼程度

「CI」表示 GitHub Actions 有這個 job（`test.yml`、`app.yml`，發布時 `release.yml` 會重用兩者）；最新一次是否全綠，請以 GitHub Actions 頁面為準。

| 項目 | Windows | Linux | macOS |
|---|---|---|---|
| Python 單元測試、lint | CI | CI | CI（arm64） |
| 前端 vitest、型別檢查、建置；Rust fmt／clippy／test | CI（`app.yml`） | CI | CI |
| 引擎打包 + 冒煙測試（假模式） | CI、本機 ✅ | CI、Docker ✅ | CI |
| 安裝檔打包、安裝後自我檢查（release 試建置） | CI ✅ | CI ✅ | CI ✅ |
| 真的擷取系統聲音（播放中） | 本機 ✅（虛擬喇叭） | Docker ✅（PulseAudio、PipeWire 的 null sink） | ❌ 未驗證 |
| 端到端（真 ASR + Ollama） | 本機 ✅：GPU（v0.1.0 組合）與 CPU | Docker ✅：CPU | ❌ |
| v0.2.0 新推薦組合的端到端（顯示記憶體、延遲） | ❌ 尚未量（里程碑 17） | ❌ | ❌ |
| 桌面程式實際操作（WebDriver） | 本機 ✅（v0.1.0 流程） | Docker + Xvfb ✅ | ❌ |
| v0.2.0 新介面流程（延後下載、切換介面語系、只顯示原文） | ❌ 只有單元測試（里程碑 17） | ❌ | ❌ |
| 安裝與自動更新 | 本機 ✅（本機 HTTP 端點＋拋棄式金鑰） | ❌ | ❌ |

出處：`.github/workflows/`、`tasks/acceptance-report.md`、`experiments/e2e_acceptance/README.md`、`experiments/linux_acceptance/README.md`、`experiments/update_acceptance/README.md`、`tasks/todo.md`（里程碑 16、17）。

## 平台與實機

- **macOS 沒有在實機跑過**：只有 CI 的打包、ad-hoc 簽章與引擎自我檢查。待確認（`tasks/acceptance-report.md`「要在 macOS 與 Linux 真機驗證」、`experiments/capture_check/README.md`）：
  - 靜音時 ScreenCaptureKit 會不會停止送樣本。若會，要像 WASAPI 一樣補零，否則 VAD 判斷不了句尾。
  - 權限記在「負責的行程」上：開發時是終端機，打包後是 kikitori.app；ad-hoc 簽章每版不同，自動更新後可能要重新授權螢幕錄製。
  - Apple Silicon 上 ASR 的速度（推薦表暫時比照「CPU、RAM ≥ 15 GiB」）。
- **Linux 只在 Docker 容器驗收過**：沒有實體桌面、沒有 NVIDIA 顯示卡、沒有 Wayland。透明視窗、置頂、滑鼠穿透、全域快速鍵、cuBLAS 下載後的 CUDA 都沒在實機確認。
- **Windows 待確認**：錄音中拔掉實體 USB 耳機；麥克風隱私權關閉時的提示；從 GitHub Release 下載時的 SmartScreen 警告。
- **裝置消失不會自動重連**，session 會進入 error（`device_lost`）。
- **即時口譯沒有用真的麥克風對話測過**（端到端用的是 TTS 合成的中文、英文）。

## 畫面（GUI）

- Windows 與 Linux（Xvfb）有 WebDriver 自動操作的紀錄，但都是 v0.1.0 的流程。v0.2.0 新增的語言選單、開始前下載、只顯示原文、介面語系切換、匯出、OBS 文字檔、術語表編輯器、音量表，只有 vitest 測純邏輯，沒有在正式版建置上操作過。`experiments/memory_soak/` 的情境 7（桌面程式長時間執行）也還沒跑。
- 各平台需要實機確認（`docs/specs/ui.md`「各平台需要實機確認的地方」）：
  - Linux：透明需要合成器，部分 Wayland／X11 可能變黑底；Wayland 不允許程式自己置頂（GNOME 可能無效）；滑鼠穿透、全域快速鍵在 Wayland 可能無法使用；部分視窗管理員不支援右下角縮放。
  - macOS：全螢幕影片在獨立 Space，可能蓋過字幕視窗；全域快速鍵可能要輔助使用權限；開了 `macOSPrivateApi`，所以不能上架 Mac App Store。
  - 字型：沒有 Noto CJK 的 Linux 會顯示方塊（deb 已把 `fonts-noto-cjk` 列為建議套件）。
- 介面語系不支援由右到左的版面（沒有阿拉伯語介面）；阿拉伯語只能當收聽或字幕語言。
- 13 個語系的字典由本專案撰寫，測試保證鍵與佔位符齊全，但沒有經過母語者審閱。

## 切句（VAD）

- **評測樣本少**：人聲只有 12 段（動漫、VTuber），每種背景 6–8 個檔案。重置策略 (c) 只是小幅領先（`experiments/silero_reset/README.md`）。
- **Silero 的已知弱點**：歌聲、高音、卡通聲。官方承認訓練資料沒有音樂（`reports/VAD 與句尾判斷技術調查.md`）。動漫正好落在這裡；歌回會直接穿過 VAD（`reports/VTuber 直播模型選型調查.md`），歌回模式列在「先不做」。
- **句尾只看停頓**：只實作 `PauseEndpoint`。標點／語意式句尾、Smart Turn 都還沒做（介面 `EndpointPolicy`、`request_end()` 已預留）。口譯模式目前也只靠停頓。
- 片段內本來就有 ≥ 0.6 秒停頓時，句子會被提早切斷；這是 `pause_secs` 的取捨。
- VAD 的參數（`threshold`、`min_speech_ms`、`pre_roll_ms`）沒有開放到設定檔。

## 語音辨識（ASR）

- **只在日語上實測**：`experiments/vtuber_eval/` 的 YODAS 口語 500 段、FLEURS、ReazonSpeech，加上 BGM 混音。其他 19 種語言的品質等級來自公開基準（`languages/catalog.py` 的 `asr_quality`），英文、中文等在真實語音上的準確度沒量。
- **評測素材不是 VTuber**：YODAS 是 CC 授權的一般 YouTube，沒有角色聲線、尖叫、多人連動、歌回；字幕不是逐字稿，所以絕對 CER 偏高，只能做相對比較（見 [08](08-evaluation-methods.md)）。
- `large-v3-turbo` 的 `no_speech_probability` 永遠是 0；完整模型是它時改用即時模型的值，加上幻覺片語表補救。v0.2.0 的 GPU 推薦完整路徑改用 `large-v3`（no_speech 可用），但 4–8 GB 等級仍是 turbo。
- **幻覺片語表**：19 種語言的片語是在非語音素材上實測收集的，但只有日文確認過不會誤濾真實語音；沒有經過母語者審閱。部分 hard 片語是直播主也可能真的說的結尾句（「チャンネル登録よろしくお願いします」），整句相等時會被濾掉。
- **兩個大模型加 Ollama 同時在一張卡上**的延遲與顯示記憶體沒量過；兩條路徑共用權重時第二個 worker 的 512 MiB 是估計值（`docs/specs/vtuber-profile.md`「驗證」）。
- 只測過 RTX 5070（Blackwell）；Turing、Ampere、Ada 世代沒測。
- 同一引擎行程裡 CUDA 失敗一次就不再試；裝好 GPU 元件後要重啟引擎（UI 會在沒有 session 時自動重啟）。
- 模型下載沒有跨行程鎖：兩個行程同時下載同一模型可能互相覆寫暫存檔（雜湊驗證會擋下壞檔）。
- 刪除模型時，「重新檢查路徑」與「刪除」之間仍有極短的空檔（Windows 的 Python 沒有以資料夾 handle 為基準的刪除）；要利用它必須已經能寫入使用者自己的模型快取（`docs/specs/asr.md`「磁碟用量與刪除」）。
- `kotoba-whisper-v2.0`（日文專用）不提供：口語評測比 large-v3 差、在純 BGM 上大量輸出「ごめん」，訓練資料的使用條款也有灰色地帶（`experiments/vtuber_eval/README.md`）。

## 翻譯

- 只在 RTX 5070 上實測；CPU 與 Apple Silicon 的翻譯延遲沒量（`experiments/translate_benchmark/README.md`）。沒有 GPU 或顯示記憶體很少時推薦的模型，速度沒有實測。
- 品質評測（`experiments/translate_eval/`）只有 ja→zh 與 ja→en 有參考譯文；其他語言組合的翻譯品質沒有量過。VTuber 用語只有 30 句自寫句做人工檢查。
- **術語表**：內建表只在原文是日語、字幕是 zh-TW／zh-CN／en／ko 時使用；評測中有 1 句因為術語讓動詞意思變錯（`docs/specs/vtuber-profile.md`「驗證」）。改了術語表要等下一次開始字幕才生效。
- **前文保險**是啟發式（長度超過原文 3 倍且 > 40 字，或抄出前文原句）：短的多餘文字抓不到；正常但很長的意譯可能被重翻一次。
- HY-MT 與 Sakura 不帶前文（會把前文一起翻出來，或格式不合）。
- 讀取逾時是「每次 socket 讀取」的上限，不是整個請求的總時間；總長度靠 `max_tokens` 256 限制。
- 串流在產出第一個片段後中斷，不會重試。
- 簡體偵測以 Big5 判斷：用字是繁體、但是大陸用語的譯文（例如「視頻」）不會被改。
- Ollama 偶爾在模型陷入重複輸出時異常結束串流。
- 翻譯伺服器在遠端時，推薦不會自動選模型，要使用者自己在模型頁選；只有 Ollama 能由 kikitori 代為下載模型。
- **Ollama 閒置卸載模型**：OpenAI 相容 API 不能指定 `keep_alive`，Ollama 預設閒置 5 分鐘就卸載。session 開始時會預熱，執行中翻譯請求停了 `translation.keep_warm_secs`（預設 240 秒）也會再送一次短請求保溫（只對本機服務，見 `docs/specs/pipeline-engine.md`）。仍然存在的情況：沒有 session 時模型照樣被卸載（下次開始時重新預熱，載入多花幾秒）；伺服器把 `OLLAMA_KEEP_ALIVE` 設得比 240 秒短時，要把 `keep_warm_secs` 調低（目前只能改設定檔或 `settings.set`，設定頁沒有這個欄位）；保溫請求會讓 GPU 上的模型一直佔著顯示記憶體，直到 session 結束後 5 分鐘。

## 端到端與效能

- **GPU 端到端只有 v0.1.0 組合的數字**：即時 `small`、完整 `large-v3-turbo`、`qwen3.5:9b`，說完到完整字幕中位數 0.69 秒（`experiments/e2e_acceptance/README.md`）。v0.2.0 的推薦（turbo＋large-v3＋9B）沒有量過。
- CPU 被其他程式搶走時，完整佇列會累積：曾量到定稿延遲中位數 16.7 秒（`experiments/pipeline_smoke/README.md`）。之後加了「即時辨識太慢就拉長間隔」，也加了狀態列的落後提示（backlog > 3），但沒有重新量化效果。
- 引擎同時只能有一個 session。

## 記憶體與資源

（細節見 [09](09-memory-and-resource-release.md)）

- `experiments/memory_soak/` 找到的問題（開裝置失敗時模型留在 GPU、每個 session 漏約 17 MiB、`Connection: close` 誤判）都已修正並有單元測試，但**修正後還沒有在實機重跑壓力測試**；README 裡的數字是修正前的。
- 長時間 session 結束後多留約 180 MiB 主記憶體（README #4）還沒有定論：只跑了 5 分鐘，而且受同機其他實驗干擾。
- 模型池保留最多 4 個卸載後的模型物件，每個約 8 MiB 顯示記憶體與一條執行緒，直到引擎結束。

## 硬體偵測與推薦

（`docs/specs/hardware.md`「已知限制與待驗證」）

- Linux 只搜尋固定目錄與 `LD_LIBRARY_PATH` 找 cuBLAS，不讀 ldconfig 快取。
- cuBLAS 只檢查檔案存在，不實際載入；版本不符要到載入模型時才發現（`auto` 會退回 CPU）。
- 多張 GPU 時以 VRAM 最大的那張估計；`CUDA_VISIBLE_DEVICES` 等設定可能讓推薦不準。
- VRAM 預算固定保留 1 GB 給桌面，不看當下其他程式實際用了多少。8 GB 以上的組合是用 registry 的量測值與翻譯模型的權重大小算出來的，單元測試檢查每 256 MiB 都放得下，但實際用量還沒在端到端驗收中量過。
- 容器的 cgroup 記憶體上限不列入。

## 打包、CI 與發布

- **發布流程已上線**（`release.yml`，v0.1.0 已發布），但：
  - 安裝檔沒有付費簽章：Windows SmartScreen、macOS Gatekeeper 都會警告（Release 頁面有各平台的繞過說明，由 `scripts/release.py notes` 產生）。
  - Release 頁面的說明是固定範本（下載表與安裝提示），**不會**自動帶入 `docs/release-notes/` 的版本說明；更新器顯示的 `latest.json` 說明也只有「kikitori v<版本>」。
  - macOS、Linux 的自動更新，以及正式 GitHub 端點與正式金鑰下的更新，都還沒實測（Windows 是用本機端點與拋棄式金鑰驗收的）。
- **授權待使用者知悉**：CTranslate2 的 Windows wheel 內附 Intel OpenMP（`libiomp5md.dll`），其授權要求「不得逆向工程」。目前照原樣散布，並在「關於」頁加上聲明（`docs/specs/packaging-ci.md`）。

## 文件本身

- 規格寫的是「打算怎麼做」，本指南以程式碼為準。撰寫時發現的出入：

  | 文件寫的 | 實際 |
  |---|---|
  | `experiments/memory_soak/README.md`：發現的問題「都沒有修改 `src/`」，列為未修 | 三個問題已在 `47b0b2f`、`a6a93f5` 修正（見 [09](09-memory-and-resource-release.md)）；實驗 README 記的是當時的狀態 |
  | `tasks/todo.md`：里程碑 16 未勾 | `docs/specs/vtuber-profile.md` 標「已實作」；未完成的是端到端 VRAM 驗收（併入里程碑 17） |
