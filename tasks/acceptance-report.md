# 驗收報告：feat/full-app

> 分支 `feat/full-app`（未合併到 main）。日期：2026-11-05。
> 系統怎麼運作請看 `docs/learning/`；各模組細節看 `docs/specs/`。

## 端到端驗收（2026-11-05，最終程式碼 `e5d3f68`）

| 驗收項目 | 結果 | 證據 |
|---|---|---|
| 從 GitHub 乾淨 clone 重建 | PASS | `uv sync --locked`、unittest、ruff、`npm ci`、vitest、build、`cargo test --locked`、clippy 全過 |
| Windows 引擎：真實擷取＋GPU 辨識＋Ollama 翻譯 | PASS | 影片翻譯 12/12 段、先即時後完整、0 簡體、靜音與純配樂 0 誤觸發；完整字幕在說完後 0.69 s（中位數）；口譯中↔英各 4 句正確 — `experiments/e2e_acceptance/README.md` |
| Windows 桌面程式（正式版建置，WebDriver 自動操作） | PASS | 啟動、模型頁、設定驗證與儲存、開始→字幕紀錄 8 列即時→完整、字幕視窗透明可讀、滑鼠穿透、停止；截圖逐張檢查 |
| 「關於」頁授權資料夾 | PASS | 兩個資料夾存在、按鈕開啟檔案總管 |
| Windows 安裝與自動更新（以 `e5d3f68` 重跑） | PASS | 安裝 0.1.0 → 偵測 0.1.1 → 更新前先關閉引擎（40 ms，結束碼 0）→ 安裝 → 重開為 0.1.1、設定保留；壞簽章被拒；同版本顯示「目前已是最新版本」；解除安裝乾淨 — `experiments/update_acceptance/README.md` |
| Linux（Docker：Ubuntu 22.04／24.04、Debian 12） | PASS | PulseAudio 與 PipeWire 擷取、引擎端到端（CPU）、deb 安裝、AppImage、Xvfb 下 WebDriver 操作正式版 GUI — `experiments/linux_acceptance/README.md` |
| 依電腦配置自動推薦（第一次啟動自動套用、更適合的組合提示、缺少模型一鍵下載） | PASS | 正式版建置、空設定資料夾：第一次啟動不按按鈕就套用 GPU 組合並顯示「已依這台電腦（NVIDIA GeForce RTX 5070，12 GB）套用建議組合：即時 small、完整 large-v3-turbo（GPU），翻譯 qwen3.5:9b。」；再啟動不重複套用、設定檔不變 — `experiments/e2e_acceptance/README.md` B2（`gui_acceptance.py --batch first-run`）；unittest／vitest／cargo test 涵蓋 `is_new`、`only_if_new`、`settings.preview_recommendation`、提示與缺少模型的規則 |
| CI 建置的 Windows 安裝檔（release 演練 run 42007759032，`6fb4647`）與自動推薦的兩條 UI 路徑 | PASS | 靜默安裝 0.1.0 → 引擎從安裝目錄啟動、第一次啟動自動套用 GPU 組合、影片翻譯 9 列先即時後完整、日文原文、正體譯文、字幕視窗雙語 → 靜默解除安裝乾淨（安裝目錄、HKCU、捷徑、行程）；缺少模型：建議組合缺 base 時提示「還需要下載 base（共約 148 MB）」，一鍵下載在同一則訊息顯示 0–99% 進度，完成後開始可用（使用者的模型快取未動）；更適合的組合：CPU 設定下提示 GPU 組合，「套用」寫入並收起，「不要再提醒」把指紋寫入 `settings.ui.dismissed_recommendations`，再啟動與重新偵測都不再提示 — `experiments/e2e_acceptance/README.md` B3（`gui_acceptance.py --batch installed`） |
| CI（`e5d3f68`） | PASS | test 41997011398、app 41997011314、release 演練 41997011477（三平台打包、安裝後自我檢查、簽章驗證）；deb 含 `Recommends: fonts-noto-cjk` |

### 驗收中找到並修正的問題（每項先寫重現測試）

| 問題 | commit |
|---|---|
| 每次 session 後顯示記憶體多佔約 2.6 GB，第二個 session 起即時字幕延遲 0.4 → 2 s 以上 | 0aebe58 |
| 以錯誤結束的 session 不釋放 GPU 上的模型 | d73aa31 |
| 引擎日誌與 CLI 選單的裝置名稱亂碼；Git Bash 下 `kikitori` 一啟動就當掉（cp932） | c1e8e15、9a4d6a0 |
| Ollama 冷啟動時前 5 句字幕晚 8–16 s；預熱與 ASR 同時載入造成 llama-server CUDA 錯誤 | 3738325、9a7fe9a |
| 長時間沒有對白後 Ollama 卸載模型，第一句逾時 → session 中保溫 | e5d3f68 |
| 口譯時中文原文顯示為簡體 | c052905 |
| Linux：拔掉 USB 耳機後默默改錄麥克風；無 PulseAudio 時錯誤類型不對 | e36c9c1 |
| session 中裝置消失只顯示籠統錯誤，改回報 `device_lost` | b8b6254 |
| Linux：聲音來源選單文字看不見；沒有 CJK 字型時介面全是方塊（deb 建議安裝 Noto CJK） | b3d9a96 |
| 「關於」路徑顯示 `\\?\` 前綴 | 12695c4 |
| Windows 更新時沒有先關閉引擎 | 7a6c12b |
| Release 說明缺各平台安裝提示（SmartScreen、Gatekeeper、Linux fuse3／字型） | 4104cea |
| 文件：AppImage 需要 `fuse3` 而非 libfuse2 | cd1a7ba |

## 總結

9 個里程碑都已完成並 commit。最後一次 CI（`dde4c88`）三個 workflow 全部通過：
- `test`（run 41982621380）
- `app`（run 41982621398）
- `release` 演練（run 41982621345，三平台打包、安裝後自我檢查、簽章驗證全過，依設計不發布）

| 測試 | 數量 | 結果 |
|---|---|---|
| Python unittest（Windows 本機與 CI 三平台） | 966 | 全過 |
| 前端 vitest | 106 | 全過 |
| Rust cargo test | 54 | 全過 |
| ruff、cargo fmt、clippy -D warnings、cargo-deny、版本一致檢查 | — | 全過 |

## 各里程碑

| # | 內容 | 主要數據 | commit |
|---|---|---|---|
| 1 | VAD 切句：Silero v6.2.3 ONNX 打包進套件，不用 pipecat／PyTorch | 動漫／VTuber 12/12 段抓到；純噪音 0 句；0.18 ms／視窗；84 條音軌評測後預設重置策略「長時間非人聲後重置」（漏抓 42/504，次佳 43） | 50f0e3e、1e46a79 |
| 2 | 語音辨識：CTranslate2 + Whisper（MIT），不用 faster-whisper（PyAV 內附 GPL 編碼器） | RTX 5070 CUDA fp16：turbo 0.15 s／句；CPU 只有 tiny／base 跟得上即時字幕 | 5e3477d |
| 3 | 翻譯：OpenAI 相容 API，預設本機 Ollama | qwen3.5:9b 首字 0.10 s、整句 0.23 s（中位數），簡體 0/33 | b98b0d9 |
| 4 | 協調器（即時＋完整雙路徑）、設定、stdio 引擎 | CPU（base／small）即時字幕 0.85 s、完整字幕 2.85 s | df069b6、380566c |
| 5 | 聲音擷取：三平台系統聲音與麥克風 | Windows loopback 與麥克風本機實錄成功 | bdcb1fa |
| 6 | 硬體偵測與模型組合 | 本機推薦 small／large-v3-turbo CUDA fp16 + qwen3.5:9b；cuBLAS 下載安裝並以 CUDA 載入成功 | ac9760f、541fdc6 |
| 7 | Tauri 介面：主視窗、模型頁、設定頁、透明置頂字幕視窗、兩種模式 | 依 frontend-design 指南設計 | 2d5305e、91ca0c8 |
| 8 | CI/CD：三平台測試；main 上版本號為新才打包發布 | 安裝檔：Windows 40 MB、Linux AppImage 157 MB／deb 124 MB、macOS 48 MB | 6341d0e、59f8c1c、8c99135 |
| 9 | 自動更新：Tauri updater + 簽章過的 `latest.json` | 演練時 minisign 驗簽全過 | 59f8c1c |

## 需要你處理

### 1. GitHub Secrets（第一次發布前）

到 repo 的 Settings → Secrets and variables → Actions 新增兩個 secret：

| 名稱 | 內容 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | `C:\Users\alianlbj23\.tauri\kikitori-updater.key` 的完整內容 |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | `C:\Users\alianlbj23\.tauri\kikitori-updater.key.password` 的內容（結尾不要多換行） |

設定指令見 `docs/specs/release-update.md`。這兩個檔案請另外備份；遺失私鑰後，已安裝的使用者將無法再自動更新。

### 2. 要不要刪除的本機檔案（我沒有刪）

| 項目 | 大小 | 說明 |
|---|---|---|
| `experiments/silero_reset/data/musan.tar.gz` | 11.09 GB | 已解壓出 `music/`、`noise/`，壓縮檔不再需要 |
| `experiments/silero_reset/data/musan_partial/` | — | 下載未完成時的暫存解壓 |
| Docker 容器 `kikitori-ci`、`kikitori-u26` 與 volume `kikitori-uv-cache` | — | 修 CI 時的 Linux 測試環境 |
| `app/src-tauri/target/debug/kikitori-engine/` | 約 163 MB | 建置驗證留下的引擎副本（已被 git 忽略） |
| Docker 映像 `kikitori-la-audio:local`、`kikitori-la-gui:local`、`ubuntu:22.04`、`ubuntu:24.04`、`debian:12` | — | Linux 驗收用（`experiments/linux_acceptance/run.sh` 會重用） |
| 暫存目錄的驗收建置（`%TEMP%\claude\…\scratchpad\upd`、`e2e-build`、`clean-clone` 等） | 數 GB | 驗收用的建置與工具（tauri-driver、msedgedriver、minisign），可整個刪除 |

### 3. 合併到 main

我沒有 push 或合併到 main。合併後如果版本號比既有的 tag 新，就會觸發第一次正式發布 `v0.1.0`，所以請先設好 Secrets。

## 要在 macOS 與 Linux 真機驗證

Windows 已在本機完整驗收（見上方）；Linux 已在 Docker 驗證擷取、引擎、安裝檔與 Xvfb 下的 GUI；macOS 只有 CI 的打包、ad-hoc 簽章與引擎自我檢查。下面這些仍需要真實桌面與硬體：

**macOS（Apple Silicon，macOS 13 以上）**
- [ ] 系統聲音擷取（ScreenCaptureKit）：第一次要允許螢幕錄製權限；播放時 RMS > 0；暫停播放時樣本是否持續送出（`experiments/capture_check/README.md` 有逐步說明）
- [ ] 拒絕權限時顯示正確的錯誤；麥克風擷取；用 BlackHole 當替代來源
- [ ] 安裝：DMG 被 Gatekeeper 擋下時，從「隱私權與安全性」強制打開後可正常使用
- [ ] 字幕視窗在全螢幕影片上是否仍在最上層；滑鼠穿透；全域快捷鍵（可能需要輔助使用權限）
- [ ] ASR 在 Apple Silicon 的實際延遲（推薦表那一列目前是估計值）
- [ ] 自動更新後是否需要重新授權螢幕錄製（ad-hoc 簽章每版不同）

**Linux（建議 Ubuntu 或 Fedora，X11 與 Wayland 各一）**
- [ ] PulseAudio／PipeWire monitor 擷取；換預設輸出後預設來源是否跟著改；拔除 USB 裝置時的錯誤
- [ ] 透明字幕視窗（沒有合成器時可能是黑底）、置頂、滑鼠穿透、全域快捷鍵（Wayland 可能失敗，介面有提示）
- [ ] AppImage 在沒有 libfuse2 的發行版能不能執行；deb 安裝
- [ ] 有 NVIDIA 顯示卡時，下載的 cuBLAS 能否讓 CUDA 正常運作

**Windows**
- [x] 實際打開 GUI 確認外觀與操作（WebDriver 自動操作正式版建置＋截圖檢查）
- [x] 播放中錄 loopback，確認內容正確（虛擬喇叭 Steam Streaming Speakers，辨識與翻譯正確）
- [x] 「關於」頁的兩個授權資料夾按鈕能開啟
- [x] 完整自動更新流程（本機 HTTP 端點＋拋棄式金鑰）
- [ ] 錄音中拔掉實體 USB 耳機；關閉麥克風隱私權後的提示
- [ ] 從 GitHub Release 下載時的 SmartScreen 警告與 https 下載

**三平台共同**
- [ ] macOS、Linux 的自動更新流程，以及正式 Release（GitHub 端點、正式金鑰）下的更新
- [ ] 即時口譯模式用電腦麥克風實際對話（目前沒有麥克風對話素材，Smart Turn 也還沒測）

## 已知限制

- **歌聲**：沒有任何串流 VAD 能把歌聲排除，動漫插入曲可能被當成台詞送去辨識；目前靠幻覺清單過濾。
- **large-v3-turbo** 的非語音機率永遠是 0，靠幻覺清單和即時模型的機率補救。
- **CPU 模式**：句子很密集時完整字幕的佇列會累積（實測曾到 16.7 s）。推薦組合會依核心數選較小的模型。
- **英文與中文**的辨識正確率沒有量測（本機沒有授權明確的素材）。
- **翻譯**：只有「字是繁體、用語是大陸用法」的譯文（例如「視頻」）不會被轉換。
- **cuBLAS** 只檢查檔案是否存在，不實際載入；同一個引擎行程中 CUDA 失敗過一次後就不再嘗試，需要重新啟動引擎。
- **假模式**的 GPU 元件裝在暫存目錄，重新啟動後會變回未安裝（只影響開發）。
- **Rust 依賴的授權聲明**：沒附授權檔的 crate 由 cargo-about 填入 SPDX 標準全文，MIT 的著作權行是佔位文字。

## 與原始規格不同的決定

| 原本 | 實際 | 理由 |
|---|---|---|
| ASR 用 faster-whisper | 直接用 CTranslate2 | faster-whisper 必要依賴 PyAV 內附 GPL 的 libx264／libx265 |
| Windows 擷取用 soundcard | PyAudioWPatch | soundcard 在本機麥克風上 assert 失敗 |
| 引擎用 Tauri externalBin | one-folder 打包放 resources | externalBin 只能帶單一檔案；one-file 啟動 1.28 s 對 0.30 s，且被強制結束時會殘留約 170 MB |
| 前端與 Rust 檢查放在 `test.yml` | 獨立的 `app.yml` | 兩條 CI 分開維護，`release.yml` 都能重用 |
| 先測試再打包 | 測試與打包平行，全部通過才發布 | 使用者看到的結果相同，省下約 20 分鐘 |
| 用 tauri-action 發布 | 自行產生 `latest.json` 並由單一 job 發布 | 只讓一個 job 有寫入權限，某個平台失敗時不會留下不完整的 Release |

## 依賴與授權重點

- **新增的 Python 執行依賴**：onnxruntime、numpy、ctranslate2、opencc、pyaudiowpatch（Windows）、soundcard（非 Windows）、pyobjc 系列（macOS），全部是寬鬆授權。開發依賴新增 pyinstaller（GPL 附 bootloader 例外，允許以任何授權散布打包出來的程式）。
- **需要注意、但已處理的授權**：
  - CTranslate2 的 wheel 內附 Intel MKL 與 OpenMP（Intel 授權，允許再散布）。「關於」頁已加上「不得逆向工程」的聲明。
  - cuDNN 已從打包中排除。
  - cuBLAS 由使用者同意 NVIDIA EULA 後才從 PyPI 下載，不隨安裝檔散布。
- **Rust 依賴**：5 個 MPL-2.0 的 crate（Tauri 的依賴）個別放行，並附上原始碼取得方式。
- **翻譯模型**：都是你自己用 Ollama 安裝的，我們不散布。
  - HY-MT：不適用於歐盟、英國、南韓，預設不推薦。
  - Sakura：非商用授權，不推薦。
- **第三方授權聲明**：Python、Rust、npm 三類都隨安裝檔散布，「關於」頁可以開啟。

## 建議的後續工作

- 開啟 updater 的 `requireSignedVersion`，拒絕版本號被竄改的 manifest。
- 情境 A：在 ASR 之後加上標點或句尾模型，透過 `request_end()` 提早切句。
- 情境 B：用麥克風錄製對話，實測 Smart Turn v3。
- 想讓 macOS 使用者不用手動繞過 Gatekeeper，就需要付費的 Apple Developer 簽章與公證。
