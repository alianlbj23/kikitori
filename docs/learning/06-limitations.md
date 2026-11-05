# 06 已知限制與尚未驗證的地方

整理自各規格的「已知限制」段落、實驗 README 與 CI 設定。
分成兩類：**限制**（已知做不到或有代價）、**未驗證**（可能沒問題，但沒有人實際確認過）。

## 一眼看懂：驗證到什麼程度

「CI」表示 `test.yml` 有這個 job；最新一次是否全綠，請以 GitHub Actions 頁面為準（最近幾個 commit 都在修 CI 失敗：macOS 授權檔、Linux 執行緒啟動順序）。

| 項目 | Windows | Linux | macOS |
|---|---|---|---|
| Python 單元測試、lint | CI | CI | CI（arm64） |
| 引擎打包 + 冒煙測試（假模式） | CI、本機 ✅ | CI、Debian 容器 ✅ | CI |
| 前端 vitest、Rust cargo test | 本機 ✅（CI 尚未加入） | ❌ 未跑 | ❌ 未跑 |
| 真的擷取系統聲音 | 本機：能開啟、靜音時格式正確；**播放中的內容未驗證** | ❌ 未驗證 | ❌ 未驗證 |
| 真模型辨識與翻譯 | 本機 ✅（RTX 5070） | ❌ | ❌ |
| 端到端（真 ASR + Ollama） | 本機 ✅，**只有 CPU ASR** | ❌ | ❌ |
| 透明置頂字幕視窗 | ✅ 已確認（WebView2） | ❌ | ❌ |
| 安裝檔、自動更新 | ❌ 尚無發布流程 | ❌ | ❌ |

出處：`.github/workflows/test.yml`、`docs/specs/packaging-ci.md`、`experiments/capture_check/README.md`、`experiments/pipeline_smoke/README.md`、`docs/specs/ui.md`、各 commit 訊息中的測試結果。

## 平台與實機

- **macOS、Linux 都沒有在實機跑過**。以下只有單元測試（假物件）：
  - 擷取：ScreenCaptureKit、PulseAudio／PipeWire monitor、CoreAudio 麥克風。
  - 硬體偵測：`sysctl`、`vm_stat`、`/proc`、sysfs；Linux 的 cuBLAS 搜尋與 `.so` 預載。
  - Apple Silicon 上 ASR 的速度（推薦表暫時比照「CPU、RAM ≥ 16 GB」）。
- **macOS 擷取待確認**（`experiments/capture_check/README.md` 有逐步清單）：
  - 靜音時 ScreenCaptureKit 會不會停止送樣本。若會，要像 WASAPI 一樣補零，否則 VAD 判斷不了句尾。
  - 命令列程式是否需要先初始化 `NSApplication`。
  - 權限記在「負責的行程」上：開發時是終端機，打包後是 kikitori.app。
- **Windows 擷取待確認**：播放影片時錄到的內容是否正確（目前只驗證了「能開啟、沒聲音時持續送全零」）；錄音中拔掉裝置是否回報 `DeviceLostError`；麥克風隱私權關閉時的訊息。
- **裝置消失不會自動重連**，session 會進入 error。

## 畫面（GUI）

- 只有 Windows 的透明視窗有確認紀錄（`docs/specs/ui.md`）。其他畫面行為沒有自動化的畫面測試；vitest 測的是純邏輯（字幕規則、表單驗證），不是實際畫面。
- 本指南撰寫時沒有找到「完整走過每一頁」的目視檢查紀錄，建議用 `npm run menu` 選 1 或 4 實際看一次。
- 各平台需要實機確認（`docs/specs/ui.md`「各平台需要實機確認的地方」）：
  - Linux：透明需要合成器，部分 Wayland／X11 可能變黑底；Wayland 不允許程式自己置頂（GNOME 可能無效）；滑鼠穿透、全域快速鍵在 Wayland 可能無法使用；部分視窗管理員不支援右下角縮放。
  - macOS：全螢幕影片在獨立 Space，可能蓋過字幕視窗；全域快速鍵可能要輔助使用權限；開了 `macOSPrivateApi`，所以不能上架 Mac App Store。
  - 字型：沒有 Noto CJK 的 Linux 可能退回其他字型。

## 切句（VAD）

- **評測樣本少**：人聲只有 12 段（動漫、VTuber），每種背景 6–8 個檔案。重置策略 (c) 只是小幅領先（`experiments/silero_reset/README.md`）。
- **Silero 的已知弱點**：歌聲、高音、卡通聲。官方承認訓練資料沒有音樂（`reports/VAD 與句尾判斷技術調查.md`）。動漫正好落在這裡。
- **句尾只看停頓**：只實作 `PauseEndpoint`。標點／語意式句尾、Smart Turn 都還沒做（介面 `EndpointPolicy`、`request_end()` 已預留）。口譯模式目前也只靠停頓。
- 片段內本來就有 ≥ 0.6 秒停頓時，句子會被提早切斷；這是 `pause_secs` 的取捨。
- VAD 的參數（`threshold`、`min_speech_ms`、`pre_roll_ms`）沒有開放到設定檔。

## 語音辨識（ASR）

- **只量了 12 段日文**。英文、中文的準確度與語言偵測都沒量。CER 只有 6 段，而且受片假名人名影響，只能當相對比較（`experiments/asr_benchmark/README.md`）。
- `large-v3-turbo` 的 `no_speech_probability` 永遠是 0；靠即時模型的值與幻覺清單補救。不指定語言時它會在噪音上輸出「you」「Thank you.」並通過過濾（pipeline 一定會給候選語言，所以正式流程不會遇到）。
- **即時與完整兩個模型同時跑、再加上 Ollama 搶同一張 GPU** 的延遲沒量過。`tiny`／`base`／`small` 的 VRAM 是估計值。
- 只測過 RTX 5070（Blackwell）；Turing、Ampere、Ada 世代沒測。
- 同一引擎行程裡 CUDA 失敗一次就不再試；裝好 GPU 元件後要重啟引擎。
- 模型下載沒有跨行程鎖：兩個行程同時下載同一模型可能互相覆寫暫存檔（雜湊驗證會擋下壞檔）。
- `kotoba-whisper-v2.0`（日文專用、CER 最低）因訓練資料的使用條款待確認，沒有列入。

## 翻譯

- 只在 RTX 5070 上實測；CPU 與 Apple Silicon 的翻譯延遲沒量（`experiments/translate_benchmark/README.md`）。CPU 推薦的 HY-MT 1.8B 是「先選最小的」，不是實測結果。
- HY-MT 與 Sakura 不帶前文（會把前文一起翻出來，或格式不合）。
- 讀取逾時是「每次 socket 讀取」的上限，不是整個請求的總時間；總長度靠 `max_tokens` 256 限制。
- 串流在產出第一個片段後中斷，不會重試。
- 簡體偵測以 Big5 判斷：用字是繁體、但是大陸用語的譯文（例如「視頻」）不會被改。
- Ollama 偶爾在模型陷入重複輸出時異常結束串流；HY-MT 1.8B 在 39 句中有 1 句因此失敗。
- 翻譯伺服器在遠端時，推薦不會自動選模型，要使用者自己在模型頁選。

## 端到端與效能

- **有 GPU 的端到端延遲沒量過**。唯一的端到端數據是 CPU ASR（定稿中位數 2.85 秒）。
- CPU 被其他程式搶走時，完整佇列會累積：曾量到定稿延遲中位數 16.7 秒（`experiments/pipeline_smoke/README.md`）。之後加了「即時辨識太慢就拉長間隔」，但沒有重新量化效果。
- 引擎同時只能有一個 session。

## 硬體偵測與推薦

（`docs/specs/hardware.md`「已知限制與待驗證」）

- Linux 只搜尋固定目錄與 `LD_LIBRARY_PATH` 找 cuBLAS，不讀 ldconfig 快取。
- cuBLAS 只檢查檔案存在，不實際載入；版本不符要到載入模型時才發現（`auto` 會退回 CPU）。
- 多張 GPU 時以 VRAM 最大的那張估計；`CUDA_VISIBLE_DEVICES` 等設定可能讓推薦不準。
- VRAM 預算固定保留 1 GB 給桌面，不看當下其他程式實際用了多少。
- 容器的 cgroup 記憶體上限不列入。

## 打包、CI 與發布

- **沒有發布流程**：`.github/workflows/` 只有 `test.yml`，沒有 `release.yml`。所以沒有安裝檔、沒有 GitHub Release，自動更新（端點是 Releases 的 `latest.json`）目前找不到任何東西。發布規格（`docs/specs/release-update.md`）撰寫本指南時還不存在，流程可能還會變。
- **CI 沒有檢查前端與 Rust**：`test.yml` 裡的 `app` job 還是 TODO 註解。`npm test`、`cargo test`、`cargo clippy` 目前只在本機跑過（Windows）。
- 發布時要確認（`docs/specs/packaging-ci.md`）：Linux／macOS 安裝後執行檔保有執行權限；macOS 上 Tauri 簽章不能破壞 PyInstaller 的 ad-hoc 簽章。
- macOS 不做付費簽章與公證（架構規格）。推論：使用者第一次開啟時會遇到 Gatekeeper 警告。
- **授權待使用者知悉**：CTranslate2 的 Windows wheel 內附 Intel OpenMP（`libiomp5md.dll`），其授權要求「不得逆向工程」。目前照原樣散布，並在「關於」頁加上聲明（`docs/specs/packaging-ci.md`）。

## 文件本身

- `docs/architecture.md` 只寫了 VAD 一節，其他模組沒有補上。整體架構請以 `docs/specs/app-architecture.md` 與本指南為準。
- 規格與程式碼的出入（本指南各篇以程式碼為準）：

  | 規格寫的 | 程式實際 |
  |---|---|
  | `app-architecture.md`：`test.yml` 含前端型別檢查與建置、`cargo test`／`cargo clippy` | `test.yml` 只有 `python` 與 `engine-bundle` 兩個 job，app 部分是 TODO |
  | `app-architecture.md`：有 `release.yml` | 不存在 |
  | `pipeline-engine.md`：`asr_prompt_context=True` 時以前文當 ASR prompt | 只有 `media` 模式會帶；`interpret` 模式一律不帶（`Pipeline._asr_prompt()`，理由：兩種語言交錯時 prompt 會把語言偵測拉向上一位說話者） |
  | `translate.md`：Ollama 換模型要 6–18 秒 | 實測 README 寫 1.5–18 秒（視 VRAM 是否要先卸載其他模型） |
