# kikitori 學習指南

這份指南寫給「主導這個專案、但沒有親手寫程式」的人。
目標是讓你看懂系統怎麼運作，知道要改什麼時該從哪裡下手。

內容都對照過目前 `feat/full-app` 分支的程式碼。
規格（`docs/specs/`）寫的是「打算怎麼做」，這裡寫的是「實際怎麼做」；兩者有出入時，以程式碼為準，並在文中註明。

## 檔案

| 檔案 | 內容 | 適合什麼時候讀 |
|---|---|---|
| [01-system-overview.md](01-system-overview.md) | 系統在做什麼、兩種模式、兩個行程怎麼溝通、資料流全圖 | 第一次接觸 |
| [02-life-of-a-subtitle.md](02-life-of-a-subtitle.md) | 跟著一句話從音效卡走到字幕視窗，每一步的參數與延遲 | 想懂「為什麼字幕會先淡淡出現再變黃」 |
| [03-modules.md](03-modules.md) | 每個模組的作用、主要檔案、對外介面、測試、常見改動 | 要改某個功能前 |
| [04-design-tradeoffs.md](04-design-tradeoffs.md) | 重要決定、放棄的方案與證據 | 想知道「為什麼不用 X」 |
| [05-where-to-start.md](05-where-to-start.md) | 依序打開哪些檔案、怎麼跑起來、小練習 | 準備動手 |
| [06-limitations.md](06-limitations.md) | 已知限制與還沒驗證的地方 | 規劃下一步、評估風險 |

## 建議閱讀順序

```text
第 1 天（約 1 小時）  01 系統總覽 → 02 一句字幕的一生
                      目標：能自己畫出「音效卡 → 字幕視窗」的圖

第 2 天（約 1–2 小時） 05 照著跑一次假模式引擎與 npm run menu
                      → 對照 05 的閱讀路線打開前 6 個檔案

之後（需要時查）      03 模組手冊（改功能前查該模組那一節）
                      04 設計取捨（有人提議換套件、換模型時先看）
                      06 限制（排優先順序、對外說明前看）
```

如果只有 15 分鐘：讀 01 的「資料流全圖」與 02 的「延遲總表」。

## 名詞速查

第一次出現的術語，各篇也會再用一句話解釋。

| 名詞 | 白話解釋 |
|---|---|
| loopback（回送擷取） | 錄下「電腦正在播放的聲音」，而不是麥克風 |
| 取樣率、16 kHz | 每秒取幾個聲音樣本。kikitori 內部一律 16,000 個／秒 |
| 單聲道、int16 | 只有一條聲道；每個樣本是 −32768～32767 的整數 |
| 重採樣（resample） | 把 48 kHz 之類的聲音換算成 16 kHz |
| VAD（Voice Activity Detection，人聲偵測） | 判斷「現在有沒有人在說話」，用來把聲音切成一句一句 |
| Silero VAD | 本專案用的 VAD 模型，每 32 ms 給一個「有人聲的機率」 |
| ONNX、onnxruntime | 一種通用的模型檔格式，與執行它的程式庫；不需要 PyTorch |
| ASR（Automatic Speech Recognition，語音辨識） | 把聲音轉成文字 |
| Whisper | OpenAI 釋出的語音辨識模型（MIT 授權），有 tiny 到 large-v3 多種大小 |
| CTranslate2 | 執行 Whisper 的推論引擎，速度快、不需要 PyTorch |
| 幻覺（hallucination） | Whisper 在沒有人聲時「編出」句子，例如「ご視聴ありがとうございました」 |
| LLM、Ollama | 大型語言模型；Ollama 是在自己電腦上跑 LLM 的伺服器，本專案拿它來翻譯 |
| OpenAI 相容 API | 很多伺服器（Ollama、vLLM…）都支援的同一套 HTTP 介面 `/v1/chat/completions` |
| OpenCC | 簡繁轉換程式庫 |
| 即時路徑／完整路徑（partial／final） | 句子還在說時先出的「草稿字幕」，與句子說完後重新算的「定稿字幕」 |
| Tauri | 用網頁技術做桌面程式的框架；外殼是 Rust，畫面是 HTML／TypeScript |
| sidecar（邊車行程） | 主程式旁邊另外跑的一個輔助程式。這裡指 Python 引擎 |
| stdin／stdout | 程式的標準輸入／輸出管線。Tauri 從引擎的 stdin 寫請求、從 stdout 讀回應 |
| NDJSON | 「每一行一個 JSON」的格式。一行就是一則訊息 |
| PyInstaller | 把 Python 程式與所有函式庫打包成可直接執行的資料夾 |
| CUDA、cuBLAS | NVIDIA 顯示卡的運算平台；cuBLAS 是其中的矩陣運算函式庫，GPU 跑 Whisper 需要它 |
| VRAM | 顯示卡上的記憶體 |
| EULA | 使用者授權合約（這裡指 NVIDIA CUDA EULA） |
| CI | 持續整合：每次 push 自動在 Windows／Linux／macOS 跑測試 |
| P90 | 第 90 百分位數：10 次裡最慢的那 1 次大約是這個值 |
| RTF | 即時因子：處理時間 ÷ 音訊長度；小於 1 才跟得上即時 |
| SNR | 訊雜比：人聲比背景大多少（dB）；越小越吵 |

## 數字從哪裡來

本指南的數字都來自 repo 內的紀錄，文中會標出處：

| 主題 | 出處 |
|---|---|
| VAD 重置策略評測 | `experiments/silero_reset/README.md` |
| pipecat 可行性實驗 | `experiments/pipecat_vad/README.md` |
| 語音辨識速度與品質 | `experiments/asr_benchmark/README.md` |
| 翻譯模型速度與品質 | `experiments/translate_benchmark/README.md` |
| 端到端延遲 | `experiments/pipeline_smoke/README.md` |
| 聲音擷取實測 | `experiments/capture_check/README.md`、`docs/specs/capture.md` |
| 打包大小與啟動時間 | `docs/specs/packaging-ci.md` |
| VAD 技術調查 | `reports/VAD 與句尾判斷技術調查.md` |

量測機器幾乎都是同一台：Windows 11、Ryzen 7 5700X、64 GB RAM、RTX 5070 12 GB。其他機器的數字會不同。
