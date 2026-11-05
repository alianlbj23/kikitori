# Linux 驗收（Docker 容器）

> 暫時性實驗工具，不屬於正式程式碼。日期：2026-11-05。在 Windows 11 的 Docker Desktop（WSL2 後端，16 執行緒）
> 上以 Linux 容器盡量驗證 `tasks/acceptance-report.md` 的 Linux 項目；需要實體桌面的部分列在最後。
> 相關規格：`docs/specs/capture.md`、`packaging-ci.md`、`release-update.md`、`ui.md`。

## 怎麼跑

```text
bash experiments/linux_acceptance/run.sh
```

互動選單（Windows 用 Git Bash）：1) 建置映像 → 2) 用 `gh` 下載最新成功的 release 演練（`[release-dry-run]`）的
`bundle-linux` → 3)～11) 各項檢查 → 12) 移除本工具建立的容器（`kla-audio`、`kla-pw`、`kla-gui`）。
本機音軌（`anime.wav`、`anime_006.mp3`，版權屬原作者）與下載的安裝檔放 `data/`，結果與截圖寫到 `results/`，兩者都被 git 忽略。

| 檔案 | 內容 |
|---|---|
| `docker/audio.Dockerfile` | ubuntu:24.04 + PulseAudio 16.1 + PipeWire 1.0.5（pipewire-pulse、WirePlumber）+ uv 0.13.4；repo 唯讀掛在 `/src`，虛擬環境在容器內 |
| `docker/gui.Dockerfile` | ubuntu:24.04 + Xvfb + WebKitWebDriver（webkit2gtk-driver 2.52.6）+ tauri-driver 2.0.6（`cargo install --locked`）+ picom + xdotool |
| `scripts/capture_probe.py` | 擷取：真的 soundcard 後端錄 null sink 的 monitor（每項在獨立子行程） |
| `scripts/engine_e2e.py` | 引擎端到端：真的引擎（原始碼或 `KLA_ENGINE` 指定的打包執行檔）＋ paplay 播放 |
| `scripts/gui_webdriver.py` | GUI：只用標準庫的 W3C WebDriver 用戶端，操作安裝好的 app |

需要：主機的 Ollama（`qwen3.5:4b`，容器以 `host.docker.internal:11434` 連線）與已下載的 ASR 模型
（`KLA_MODELS`，預設是本機 kikitori 的模型快取，唯讀掛載）。

## 結果總覽

| 項目 | 結果 |
|---|---|
| 1. 擷取（PulseAudio 16.1、PipeWire 1.0.5） | **PASS**（修正兩個 bug 之後） |
| 2. 引擎端到端（CPU、真的 VAD／ASR、主機 Ollama） | **PASS**（原始碼與 deb 內的打包引擎都過） |
| 3. 安裝檔（deb、AppImage） | **PASS**；AppImage 的 FUSE 需求與文件不符（已修正文件） |
| 4. GUI（Xvfb + WebDriver，真的引擎） | **PASS**；修正聲音來源選單看不見文字、deb 加 Recommends CJK 字型 |

## 1. 擷取

`capture_probe.py --all`，兩個 null sink（`speakers`、`headphones`）。PulseAudio 用發行版預設的 `default.pa`
（含 suspend-on-idle）；PipeWire 在 session D-Bus 內啟動 pipewire、WirePlumber、pipewire-pulse。

| 檢查 | PulseAudio | PipeWire |
|---|---|---|
| 預設 loopback 跟著 `pactl set-default-sink`（`list_devices` 的 `is_default` 與 `open_source(None, "loopback")`） | PASS | PASS |
| 預設 source 是 monitor 時麥克風沒有預設 | PASS | PASS |
| 1 kHz（48 kHz 立體聲）經預設 monitor：int16、一維；RMS 11585；頻譜峰值 1000.0 Hz | PASS | PASS |
| 每秒樣本數（第一筆資料之後）／停止播放後 | 15725／16000（RMS 0） | 15933／16000（RMS 0） |
| 第一筆資料的延遲 | **1.9 s**（null sink 從 suspend 恢復；之後連續） | 0.0 s |
| read 之間最長間隔 | 0.10 s | 0.02 s |
| 日文片段（44.1 kHz 立體聲）經指定 id 的 monitor，存成 `results/loopback_speech.wav` | PASS（RMS 2782） | PASS |
| 虛擬麥克風（`module-remap-source`）當預設麥克風 | PASS（1 kHz，RMS 11584） | PASS |
| 錄音中移除裝置 → `DeviceLostError` | 修正前 **FAIL**，修正後 PASS（2.1 s；搬到 sine source 後下一個區塊要 2 s 才來，混進 320 個樣本） | 修正前 **FAIL**，修正後 PASS（0.36 s） |
| 沒有伺服器（`PULSE_SERVER` 指到不存在的 socket）→ `BackendUnavailableError` | 修正前 **FAIL**（`AssertionError`），修正後 PASS | PASS |
| 錄音中結束伺服器 → `DeviceLostError`（stream FAILED），`stop()` 2 s 內返回 | PASS | PASS（結束 pipewire-pulse） |

找到並修正的 bug（commit `e36c9c1`）：

- **裝置移除時串流被搬走、不報錯**：PulseAudio 與 PipeWire 都會把被移除裝置上的錄音串流搬到預設 source 繼續送資料。
  實測把預設 source 設成 440 Hz 的 sine source（模擬內建麥克風），拔掉「USB」sink 後 media 模式錄到的是 440 Hz、
  RMS 11581——也就是拔掉 USB 耳機後會默默改錄麥克風。修正：`open_microphone()` 建立的來源每 0.25 s 以
  `all_microphones()` 確認裝置還在，不在時隔 0.1 s 再查一次，仍不在就拋 `DeviceLostError`；列舉本身失敗只記 warning
  （依 code review 的建議補上，commit `d73aa31`）。
- **沒有 PulseAudio 伺服器時拋 `AssertionError`**：soundcard 匯入時就連線並以 `assert` 檢查 context；改為
  `BackendUnavailableError`。

## 2. 引擎端到端

`engine_e2e.py`：`settings.set`（`asr.device=cpu`、即時 `base`／完整 `small`、翻譯 `qwen3.5:4b` @ 主機 Ollama）
→ `session.start` media、`soundcard:loopback:speakers.monitor` → `status running` 後以 paplay 播 `anime.wav`（33 s、8 句）
→ 最後一則字幕後 30 s 沒有新字幕 → `session.stop` → `engine.shutdown`。

| | 原始碼引擎 | deb 內的打包引擎 |
|---|---|---|
| 啟動到 `status idle` | 1.08 s | 0.28 s |
| `session.start` 到 `running`（載入模型） | 3.44 s | 3.33 s |
| 即時字幕事件 | 125 | 141 |
| 完整字幕 | 8／8，全部是繁體（`s2twp` 不變），狀態 `idle → loading → running → idle` | 8／8，全部繁體 |
| 即時字幕延遲（中位數／最大） | 0.77 s／0.96 s | 0.84 s／2.03 s |
| 完整字幕延遲（中位數／最大） | 2.46 s／4.30 s | 2.74 s／4.71 s |
| 結束碼 | 0 | 0 |

延遲以音軌第一個有聲音的位置對齊第一句的 `start_sample`（扣 200 ms 前置緩衝），誤差約 ±50 ms。
容器內 CPU（WSL2，與主機上其他工作共用）**不代表實機**；數字與 Windows 本機的 CPU 量測（即時 0.85 s、完整 2.85 s）同一量級。

翻譯範例（原始碼引擎）：「你是金星先生的親戚嗎？」「我猜應該是這樣被叫的。」「難道這是失憶症嗎？」

`engine_e2e.py` 選項 2：session 執行中移除裝置 → 修正前 deb 內的引擎 30 s 內**沒有任何錯誤**（串流被搬走，持續錄音）；
修正 capture 後送出 `status error`，但 `code` 是 `pipeline_error`（介面只說「語音處理發生錯誤」）。
修正（commit `b8b6254`、`d73aa31`）：`Pipeline.error` 保存結束時的例外（清掉 traceback，避免以 error 結束的 session 元件要等循環垃圾回收才釋放），引擎把 `CaptureError` 子類別對應到擷取的 code →
`code=device_lost`（移除後 0.06–0.32 s），介面顯示「音訊裝置中斷了。請確認裝置連線後重新開始。」。

## 3. 安裝檔（v0.1.0 release 演練，run 41982621345）

| 檢查 | 結果 |
|---|---|
| `apt install ./kikitori_0.1.0_amd64.deb`：ubuntu 24.04、ubuntu 22.04、debian 12 | PASS（依賴 `libwebkit2gtk-4.1-0, libgtk-3-0`；`libpulse0` 經 WebKitGTK 的相依一併裝上，三者都有） |
| 檔案：`/usr/bin/kikitori-app`、`/usr/lib/kikitori/kikitori-engine/`、`/usr/lib/kikitori/licenses/`、`.desktop`、圖示 | PASS，兩個執行檔都是 `-rwxr-xr-x` |
| `smoke_test.py --binary /usr/lib/kikitori/kikitori-engine/kikitori-engine --self-check`（三個發行版） | PASS（啟動 0.23–0.25 s） |
| `licenses.py --check-bundle /usr/lib/kikitori` | PASS |
| deb 內的打包引擎錄真的 PulseAudio monitor、ASR、翻譯（上面第 2 節） | PASS |
| AppImage `--appimage-extract`：`squashfs-root/usr/lib/kikitori/...` 的 smoke test 與 check-bundle | PASS |
| AppImage 直接執行 | 見下 |

AppImage 用的是 **type2-runtime**（`--appimage-version`：`AppImage/type2-runtime@fedfad5`，靜態連結 libfuse3），
**不需要 libfuse2**；需要的是 `/dev/fuse` 與 `fusermount`／`fusermount3`：

| 環境（一般使用者） | 結果 |
|---|---|
| 沒有 `/dev/fuse`（一般容器） | `fuse: device not found` → 「Cannot mount AppImage, please check your FUSE setup.」，結束碼 127 |
| 有 `/dev/fuse`，沒有 fusermount、沒有 libfuse2 | `No suitable fusermount binary found on the $PATH` → 同上 |
| 只裝 `libfuse2t64`（沒有 fusermount） | 同上（libfuse2 沒有幫助） |
| 只裝 `fuse3`（沒有 libfuse2） | 掛載成功 |
| `APPIMAGE_EXTRACT_AND_RUN=1`（或 `--appimage-extract-and-run`），沒有 FUSE | GUI 測試全部通過（引擎就緒 4.05 s，含解壓） |

`docs/specs/release-update.md` 原本寫「需要 FUSE 2（libfuse2／libfuse2t64）」，已改成實際需求與替代方式。
**建議**（CI 檔案，未修改）：`release.yml` 產生的 Release 說明加一行 Linux 說明（`chmod +x`、需要 `fuse3`，否則用
`--appimage-extract-and-run`；精簡系統請裝 `fonts-noto-cjk`）。

## 4. GUI（Xvfb、沒有視窗管理員）

`gui_webdriver.py`：tauri-driver 2.0.6 + WebKitWebDriver 2.52.6 可以直接操作**正式版**（release build）的 deb app，
不需要除錯版。引擎是**真的引擎**（deb 內的打包引擎、CPU、主機 Ollama），不是假模式。

| 檢查 | 結果 |
|---|---|
| 主視窗出現、標題 `kikitori`；引擎連線（狀態列「待命中」） | PASS（1.3 s） |
| 聲音來源清單列出 PulseAudio monitor，預設項目「預設：Monitor of Null Output」 | PASS |
| 按「開始」→「正在聆聽」（載入模型 5 s）→ 播放 `anime.wav` | PASS |
| WebDriver 看到兩個視窗；字幕視窗 `overlay.html` 收到字幕（原文＋譯文） | PASS |
| 字幕紀錄 8 句、原文日文＋繁中譯文；按「停止」→「待命中」 | PASS |
| AppImage（`APPIMAGE_EXTRACT_AND_RUN=1`）同一流程 | PASS |
| 全域快捷鍵 Ctrl+Alt+K（xdotool，X11）切換「滑鼠穿透」 | PASS；另一個 kikitori 佔用同一按鍵時，介面正確改提示「已被其他程式占用」 |

截圖（`results/`，已逐張檢視）：

- **CJK 字型**：容器只有 DejaVu 時整個介面是豆腐方塊（`gui_direct_root.png`）；裝 `fonts-noto-cjk` 後正常
  （`gui_direct_root_cjk.png`，介面是 Noto Sans CJK TC，標題「聞き取り」是 Noto Serif CJK）。
  → deb 加上 `Recommends: fonts-noto-cjk`（commit `b3d9a96`；桌面版 Ubuntu 已內建，不強制）。AppImage 不帶字型。
- **聲音來源選單看不見文字**：側欄的 `<select>` 被 WebKitGTK 以原生淺色外觀繪製、忽略 CSS 背景色，淺色字在白底上
  幾乎看不見。以 WebDriver 注入樣式比較（`select_compare.png`：原樣／`color-scheme: dark`／`appearance: none`），
  採用 `color-scheme: dark`（保留原生箭頭）。commit `b3d9a96`。（執行中選單停用時是灰底，看得到。）
- **透明字幕視窗**：沒有合成器時字幕視窗是**黑底不透明**，蓋住後面的主視窗（`gui_x_during_playback.png`）；
  開 picom（xrender）後真的透明，主視窗的標題從字幕後面透出來（`gui-compositor/` 或 `compositor/gui_x_during_playback.png`）。
  WebDriver 讀到的 `html`／`body` 背景都是 `rgba(0, 0, 0, 0)`，所以黑底來自沒有合成器，不是 CSS。
- 沒有視窗管理員時字幕視窗在 (0, 0)，置頂與位置要在真的桌面看。

## 需要實體 Linux 桌面的項目

- **Wayland**（GNOME／KDE）：透明、置頂（Wayland 不允許應用程式自行置頂）、滑鼠穿透、全域快捷鍵（`global-hotkey` 可能無法註冊）、無邊框縮放。
- **X11 + 真的視窗管理員**：字幕視窗置頂、在全螢幕影片上的行為、滑鼠穿透時點擊真的穿過、右下角縮放把手。
- **真的音效卡**：monitor 開始錄音時第一筆資料的延遲（null sink 是 1.9 s）、USB 耳機／麥克風實際拔除（容器以卸載 null sink 模擬）、藍牙裝置切換 profile。
- **NVIDIA CUDA**：下載的 cuBLAS 能否讓 CTranslate2 用 CUDA（容器沒有 GPU）。
- **deb 更新**：pkexec 的提示與 `dpkg -i`；AppImage 自我更新的寫入權限（需要兩個版本的 Release）。
- 實機 CPU 的延遲（容器數字只供參考）。
