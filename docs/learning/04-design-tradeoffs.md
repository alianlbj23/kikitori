# 04 設計取捨

每一項都是：**選了什麼、放棄什麼、為什麼、證據在哪**。
有人提議「換成 X 比較簡單」時，先來這裡看當初為什麼沒選 X。

標「推論」的句子是本指南自己的解讀，文件裡沒有直接寫。

---

## 聲音與切句

### 1. VAD 直接用 Silero ONNX，不經 pipecat

- **選**：自己用 onnxruntime 跑 Silero VAD v6.2.3 官方 ONNX 模型（`vad/silero.py` 約 180 行）。
- **放棄**：pipecat-ai 的 `SileroVADAnalyzer`；PyTorch 版 `silero-vad`。
- **為什麼**：pipecat 多帶約 290 MB 依賴（numba、llvmlite、openai…），但 VAD 只需要 onnxruntime、numpy 和一個小模型檔；拿每 32 ms 的機率、關掉自動重置都得覆寫私有方法；時間用牆鐘算；而且每約 2 秒自動重置，實驗中造成整句漏抓。PyTorch 又比 onnxruntime 大得多。
- **證據**：`experiments/pipecat_vad/README.md`（.venv 從 39 MB → 329 MB；關掉重置後片段內平均機率 0.626 → 0.700）、`docs/adr/0001-vad-silero-onnx-and-reset-policy.md`。

### 2. 重置策略預設「長時間非人聲後才重置」

- **選**：`ResetPolicy.AFTER_LONG_NON_SPEECH`（連續 2 秒沒人聲、最近 5 個視窗機率都 < 0.1 才重置）。
- **放棄**：(a) 只在換串流時重置（官方程式的做法）；(b) 每句結束就重置（維護者說 "Ideally yes"）。
- **為什麼**：84 條評測音軌整體 (c) 稍好。優勢在「句間是長段音樂」——動漫、直播常見；代價在「句間是長段噪音」。(b) 讓下一句開頭落在模型暖機期，延遲明顯變長。
- **數字**（`experiments/silero_reset/README.md`）：

  | 策略 | 漏抓（全部 504 段） | 開始延遲 中位數／P90 | 純音樂長間隔漏抓 | 噪音長間隔漏抓（MUSAN） |
  |---|---|---|---|---|
  | (a) 換串流才重置 | 43 | 490／876 ms | 9/48 | 0/48 |
  | (b) 每句重置 | 49 | 556／1440 ms | 5/48 | 4/48 |
  | **(c) 長時間非人聲後** | **42** | **470／820 ms** | 7/48 | 2/48 |

- **注意**：領先幅度小。人聲樣本只有 12 段。之後有更多真實素材（含配樂的節目）要重跑；若實際雜訊場景較多，(a) 是合理替代。

### 3. 時間一律用樣本數，不用牆鐘

- **選**：所有音訊位置都是「從串流開頭算第幾個樣本」（16 kHz）。pipeline 只有在「量即時辨識花了多久、決定要不要拉長間隔」時看時鐘，而且時鐘可以注入。
- **放棄**：用 `time.monotonic()` 記時間（pipecat 的做法）。
- **為什麼**：離線測試或評測時，聲音餵得比即時快，牆鐘算出來的位置會錯。用樣本數時，同一段聲音不論分幾批、多快餵，事件完全相同，測試可以重現。
- **證據**：`experiments/pipecat_vad/README.md`（Smart Turn 的緩衝用 monotonic，離線會算錯）；`tests/test_vad_segmenter.py` 用 1／160／512／1000／4096 樣本分批餵，結果相同（`tasks/todo.md` Task 4 結果）。

### 4. Windows 用 PyAudioWPatch，不用 soundcard

- **選**：Windows 用 PyAudioWPatch（PortAudio 的 WASAPI loopback 版）；Linux／macOS 麥克風用 soundcard。
- **放棄**：三平台都用 soundcard；sounddevice。
- **為什麼**：soundcard 0.4.6 的 WASAPI 實作直接改系統回傳的結構，並 `assert` 混音格式；本機麥克風回報 `WAVE_FORMAT_IEEE_FLOAT` 時直接 `AssertionError`。PyAudioWPatch 經 PortAudio 協商格式，同一支麥克風正常。sounddevice 高階介面沒有 WASAPI loopback、沒有 Linux monitor、沒有 macOS 系統聲音。
- **證據**：`docs/specs/capture.md`「函式庫選擇」；`experiments/capture_check/README.md` Windows 實測。

### 5. macOS 系統聲音用 ScreenCaptureKit

- **選**：macOS 13+ 用 ScreenCaptureKit（PyObjC）。它沒有「只錄聲音」的模式，所以畫面縮到 2×2、每秒 1 幀後丟掉。
- **放棄**：要求使用者裝 BlackHole（保留為替代方案）；macOS 14.2+ 的 Core Audio process tap（不需螢幕錄製權限，但 PyObjC 沒有方便的綁定）。
- **代價**：需要「螢幕與系統錄音」權限；macOS 12 以下不能錄系統聲音。
- **證據**：`docs/specs/capture.md`「macOS」一節。**尚未在 Mac 實機驗證。**

### 6. 重採樣自己用 numpy 寫

- **選**：Kaiser 窗 windowed-sinc、polyphase，80 dB 阻帶。
- **放棄**：`soxr`（LGPL）、`samplerate`、scipy（多帶原生函式庫）。
- **證據**：`docs/specs/capture.md`：1 kHz、7 kHz 增益 0.00 dB，8 kHz −83～−85 dB；10 秒 48 kHz 音訊約 0.15 秒處理完。

## 語音辨識

### 7. 直接用 CTranslate2，不用 faster-whisper 套件

- **選**：直接呼叫 CTranslate2 4.8.5 的 Whisper；log-mel、解碼參數從 faster-whisper 移植（MIT，保留出處）；分詞器、下載自己寫。
- **放棄**：faster-whisper（原本的首選）。
- **為什麼**：授權。faster-whisper 一定會匯入 PyAV（`av`），而 PyAV 的 wheel 內含 GPL 的 `libx264`、`libx265`；它和 `tokenizers` 又經 `huggingface-hub` 帶入 `tqdm`（MPL-2.0）。我們本來就不需要它的音訊解碼和時間戳。
- **代價**：多維護分詞器與下載器；以 514 個字串和 HF `tokenizers` 比對，0 不一致；log-mel 與 faster-whisper 最大差異 0。
- **證據**：`docs/specs/asr.md`「決策」；`experiments/asr_benchmark/README.md`。

### 8. 即時與完整用兩個 ASR 實例

- **選**：即時路徑用小模型 greedy，完整路徑用較準的模型 beam 5；就算是同一個模型也載入兩份。
- **為什麼**：Whisper 每次處理固定 30 秒視窗，耗時幾乎與句長無關。CPU 上只有 `tiny`、`base` 能在 700 ms 內完成（`base` 中位數 484 ms、`small` 1419 ms）。同一個實例有鎖，兩條路徑要並行就得分開。
- **證據**：`experiments/asr_benchmark/README.md`「觀察」1–2。

### 9. CUDA 失敗後同一行程不再試

- **選**：`load_transcriber()` 記住第一次 CUDA 失敗；之後 `auto` 一律用 CPU，指定 `cuda` 就報錯要求重啟引擎。
- **為什麼**：實測失敗後再以 CUDA 載入，暖機會卡住不返回。
- **影響**：使用者裝完 GPU 元件後要重啟引擎；UI 在沒有 session、沒有其他下載時會自動重啟。「套用建議組合」寫入 `cpu` 或 `cuda`，不寫 `auto`，避免先試 CUDA 失敗。
- **證據**：`experiments/asr_benchmark/README.md`「觀察」9；`docs/specs/pipeline-engine.md`。

### 10. GPU 預設 float16

- **選**：CUDA 用 `float16`；VRAM 不夠時才用 `int8_float16`。CPU 用 `int8`。
- **證據**：RTX 5070 上 float16 每個模型都比 int8_float16 快（例如 large-v3 271 vs 405 ms），int8_float16 只省約一半 VRAM（`experiments/asr_benchmark/README.md`）。

### 11. cuBLAS 由使用者同意 EULA 後自己下載，不隨安裝檔散布

- **選**：使用者在模型頁勾「我同意 NVIDIA CUDA EULA」後，引擎從 PyPI 下載官方 `nvidia-cublas-cu12==12.8.4.1` wheel（Windows 約 567 MB），驗 SHA-256，只解出 `cublas64_12.dll`、`cublasLt64_12.dll` 與授權全文。
- **放棄**：把 cuBLAS 打包進安裝檔；要求使用者安裝整套 CUDA Toolkit。
- **為什麼**：避免約 0.8 GB 的專有授權檔案進入我們的發行物；等同使用者自己 `pip install`。按一個按鈕就能啟用 GPU。Rust 端也只有前端明確傳 `acceptLicense: true` 才帶參數，引擎沒收到 `accept_license: true` 一律拒絕。
- **打包也防呆**：`packaging/engine/policy.py` 把 `cublas*`、`cudnn*` 等 NVIDIA 檔名列為禁止打包。
- **證據**：`docs/specs/hardware.md`「GPU 執行環境」；實裝紀錄：下載約 60 秒，之後 `small` CUDA 載入 2.1 秒。

## 翻譯

### 12. HTTP 只用標準庫

- **選**：`http.client` + `json`，自己解析 SSE 串流。
- **放棄**：`openai`、`httpx`、`requests` 等套件；也不用 `urllib`。
- **為什麼**：只需要一個 POST 端點與 SSE；`http.client` 能分開設定「連線逾時」（3 s）與「讀取逾時」（30 s），`urllib` 只有一個共用逾時。不值得為此加依賴。
- **證據**：`docs/specs/translate.md`「前提假設」5。

### 13. 簡轉繁只在有「簡體專用字」時才做

- **選**：先找 OpenCC `s2tw` 會改、又不在 Big5 字集的字；有才用 `s2twp`（含台灣慣用詞，例如 视频→影片）轉整句。
- **放棄**：一律轉換。
- **為什麼**：`s2twp` 會把正確的「那個傢伙」改成「那個傢夥」、「台灣」改成「臺灣」；`s2t` 會把「才、吃、伙」改成「纔、喫、夥」。
- **證據**：`experiments/translate_benchmark/README.md`「4. 簡體字」：Sakura 原始輸出 22/22 句含簡體，轉換後 0/22。
- **限制**：只是大陸用語但字是繁體的（例如「視頻」）不會被改。

### 14. 提示詞用譯文語言寫

- **選**：譯成中文就用繁體中文寫指示，譯成日文就用日文寫。
- **證據**：英文指示下 `qwen3.5:9b` 有 11/18 次直接輸出日文；改成中文指示 0/18（`experiments/translate_benchmark/README.md`「3.」）。

### 15. 即時與完整路徑用同一個翻譯模型

- **選**：推薦組合中兩條路徑用同一個 LLM。
- **為什麼**：GPU 上每個模型整句 P90 都在 0.3 秒內；Ollama 換模型要 1.5–18 秒，輪流用反而更慢，還多佔 VRAM。
- **證據**：`experiments/translate_benchmark/README.md`「結論與推薦」；`docs/specs/translate.md`。

### 16. 有地區限制或非商用授權的模型不預設推薦

- **選**：`translation.allow_restricted` 預設 `false`；非商用模型（Sakura，CC BY-NC-SA 4.0）永遠不推薦。
- **為什麼**：騰訊 HY-MT 的授權不適用於歐盟、英國、南韓，且禁止在這些地區使用模型「或其輸出」。引擎不知道使用者在哪裡，用語系猜不可靠；推薦了不能用的授權，會讓使用者在不知情下違約。使用者在設定頁勾選「我不在歐盟、英國或南韓」後才會被推薦。手動選用仍可以，但檢查會顯示警告。
- **品質排序**（日文口語）：Qwen3.5 9B > 4B > HY-MT 7B > HY-MT 1.8B，Qwen3.5 是 Apache-2.0、沒有地區限制，所以本來就排第一。
- **證據**：`docs/specs/translate.md`「模型預設組與授權」；`translate/presets.py` 的 `recommend_translation_models()`。

### 17. `localhost` 先連 IPv4

- **選**：`http://localhost` 依序試 `127.0.0.1`、`::1`，`Host` 標頭保持 `localhost`。
- **證據**：Windows 上被拒的 IPv6 連線要約 2 秒才失敗，所有模型首字延遲都是 2.06–2.10 秒；修正後 0.04–0.10 秒（`experiments/translate_benchmark/README.md`「1.」）。

## 引擎與桌面程式

### 18. sidecar 走 stdin／stdout，不開本機 HTTP

- **選**：Tauri 與引擎之間用 stdin／stdout 傳 NDJSON。
- **放棄**：引擎開一個本機 HTTP 或 WebSocket 埠。
- **為什麼**（規格原文）：「以 stdin/stdout 交換 NDJSON，不開網路埠」（`docs/specs/app-architecture.md`）。
- **推論**：不開埠就沒有埠號衝突，也沒有其他本機程式能連進來；引擎生命週期綁在管線上——stdin 關閉（EOF）引擎就清理並結束，Tauri 當掉不會留下孤兒伺服器。代價是 stdout 必須非常乾淨：引擎啟動時就把 `sys.stdout` 換成 stderr（`engine/main.py`），任何程式庫的 `print` 都不會弄壞協定。

### 19. 引擎打包成 one-folder，用 Tauri `bundle.resources` 帶入

- **選**：PyInstaller one-folder，整個資料夾當資源放進安裝檔，Rust 直接從資源目錄啟動。
- **放棄**：Tauri `externalBin`（只能帶單一執行檔）；PyInstaller one-file。
- **數字**（`docs/specs/packaging-ci.md`，Windows 11、Ryzen 7 5700X）：

  | | one-folder | one-file |
  |---|---|---|
  | 大小 | 170.2 MB（未壓縮） | 59.6 MB |
  | 啟動到第一筆 `status idle`（10 次中位數） | **0.30 s** | 1.28 s |
  | 每次啟動的暫存寫入 | 無 | 每次解壓約 170 MB |
  | 被強制結束後 | 無殘留 | 約 170 MB 暫存目錄留著不清 |

- 另外：防毒軟體對 one-file 自解壓的誤判較多。`build.py --onefile` 保留為備案。

### 20. 正式打包的資源設定放在另一個檔案

- **選**：`bundle.resources` 只寫在 `app/src-tauri/tauri.sidecar.conf.json`，正式打包時才合併。
- **為什麼**：`cargo build` 會檢查資源路徑存在。若寫進 `tauri.conf.json`，每次 `cargo test`、`tauri dev` 都得先建置 170 MB 的引擎。分開後，忘了建置引擎的正式打包會直接失敗，而不是做出沒有引擎的安裝檔。
- **證據**：`docs/specs/ui.md`「打包與引擎資源」。

### 21. 每一層都再驗證一次

- **做法**：引擎驗證請求與設定；Rust 驗證引擎的每一行（`protocol.rs`）與前端傳來的參數（`commands.rs`）；前端再驗證事件（`validate.ts`）。
- **為什麼**（推論）：任何一層出錯（例如引擎印出半行），壞資料都會在邊界被擋下並記錄，不會讓畫面崩潰。Rust 的端到端測試會用 `fake_engine.py --garbage-every N` 故意插入壞資料。
