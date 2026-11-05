# 驗收報告：feat/full-app

> 分支 `feat/full-app`（最後 commit `dde4c88`，未合併到 main）。日期：2026-11-05。
> 系統怎麼運作請看 `docs/learning/`；各模組細節看 `docs/specs/`。

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

### 3. 合併到 main

我沒有 push 或合併到 main。合併後如果版本號比既有的 tag 新，就會觸發第一次正式發布 `v0.1.0`，所以請先設好 Secrets。

## 要在 macOS 與 Linux 真機驗證

CI 只驗證了「能建置、能打包、安裝後引擎能啟動」。下面這些項目需要真實桌面與硬體：

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

**Windows（本機已驗證大部分，以下尚未做）**
- [ ] 實際打開 GUI 確認外觀與操作（子代理人只跑過建置與測試）
- [ ] 播放中錄 loopback，確認內容正確；錄音中拔掉 USB 耳機；關閉麥克風隱私權後的提示
- [ ] SmartScreen 警告；「關於」頁的兩個授權資料夾按鈕能開啟

**三平台共同**
- [ ] 完整的自動更新流程：安裝 v(n) → 發布 v(n+1) → 偵測、下載、驗簽、安裝、重新啟動後版本正確
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
