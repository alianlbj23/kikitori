# 開源競品比較：即時字幕／翻譯桌面工具（2026-11）

> 調查日期：2026-11-06。Stars、授權、最後 push、最新 release 一律取自 GitHub REST API（`gh api repos/<repo>`、`.../releases/latest`）當天的值；Speech Note 另查 GitLab API，模型授權查 Hugging Face API。
> 證據只用官方 repo（README、docs、原始碼）、官方 model card、論文。「（官方宣稱）」表示作者自己的數字，未經獨立驗證；「（推論）」是本文的推論。
> 本文只讀取各專案的公開資料，不複製任何程式碼。GPL／AGPL 專案（LocalVocal、Sokuji、LiveTL、LunaTranslator、Kitori、abb128/LiveCaptions）只借鑑**概念**，kikitori（MIT）不得搬用它們的程式碼。
> 模型選型（kotoba-whisper、ReazonSpeech、SenseVoice、Parakeet…）已在 `research_notes/VTuber 直播模型選型調查/` 調查過；commit 策略的學理在 `research_notes/VAD 與句尾判斷技術調查/media_sentence_segmentation.md`。本文聚焦在**應用程式功能**與**串流／顯示策略**，不重複模型 CER 比較。

## 0. kikitori 基準（比較用）

- 擷取：Windows WASAPI loopback、Linux PulseAudio／PipeWire monitor、macOS ScreenCaptureKit、麥克風；numpy 多相 FIR 重採樣到 16 kHz。
- 切句：Silero VAD v6.2.3 ONNX，遲滯門檻（0.5／0.35），`PauseEndpoint`（media 0.6 s／最長 10 s），已預留 `request_end()` 給之後的標點模型或 Smart Turn。
- 辨識：CTranslate2 Whisper（不用 PyTorch、不用 faster-whisper）。**雙路徑**：句子進行中每 `max(700 ms, 上次耗時)` 用快模型 greedy 重轉「整句到目前為止的音訊」（最新槽，落後就跳過）；句尾用準模型 beam 5、帶前文 prompt 重轉，以同一 `utterance_id` 取代。
- 幻覺：`no_speech` 門檻、硬門檻 0.8、低信心、壓縮比 > 2.4、19 語言實測幻覺片語表（hard／soft）；完整模型 `no_speech` 不可靠時借用快模型的值。
- 翻譯：OpenAI 相容 Chat Completions（預設 Ollama），每種模型有自己的提示範本（HY-MT、Sakura、generic），帶 3 句同語言前文，串流輸出，`<think>` 清理、OpenCC 選擇性簡轉繁。
- 介面：Tauri v2；透明置頂、可滑鼠穿透（Ctrl+Alt+K）的雙語字幕視窗，1–3 句；字幕紀錄頁（最多 300 句，無匯出）；只顯示原文；20 種語言任意組合；13 種介面語系；硬體偵測與推薦組合；延後下載模型；自動更新；三平台 CI。
- 沒有的：匯出、使用者自訂術語表（內建直播用語術語表正在 `docs/specs/vtuber-profile.md` 規劃中）、說話者分離、OBS 輸出、延遲指示、使用者自訂過濾規則、振假名。

## 1. 專案總覽

| 專案 | 類型 | Stars | 授權 | 最後 push | 最新 release | 與 kikitori 的關係 |
|---|---|---:|---|---|---|---|
| [SakiRinn/LiveCaptions-Translator](https://github.com/SakiRinn/LiveCaptions-Translator) | Windows 桌面（C#／WPF） | 4,029 | Apache-2.0 | 2026-08-17 | v1.7.1300.1822（2026-01-18） | 最直接的競品：系統聲音→字幕＋LLM 翻譯＋浮動字幕 |
| [ionic-bond/stream-translator-gpt](https://github.com/ionic-bond/stream-translator-gpt) | Python CLI＋Gradio WebUI | 217 | MIT | 2026-11-02 | PyPI 2026.11.3 | VTuber／直播翻譯常用工具（直播網址、loopback） |
| [kizuna-ai-lab/sokuji](https://github.com/kizuna-ai-lab/sokuji) | Electron＋瀏覽器擴充 | 1,469 | AGPL-3.0 | 2026-11-05 | v0.48.0（2026-11-04） | 雙向會議口譯（對應 kikitori `interpret`） |
| [michael-chow-arch/StreamLingo](https://github.com/michael-chow-arch/StreamLingo) | Chrome 擴充＋本機 Python | 35 | MIT | 2026-10-13 | v0.2.0 | YouTube 直播字幕＋Ollama，有公開延遲量測 |
| [ZeshiXing/Kitori](https://github.com/ZeshiXing/Kitori) | Windows 桌面 | 3 | GPL-3.0 | 2026-10-28 | v0.3.1 | **同名**（名稱取自「聞き取り」），日語學習＋振假名 |
| [jxlpzqc/TMSpeech](https://github.com/jxlpzqc/TMSpeech) | Windows 桌面（C#） | 1,501 | MIT | 2026-07-15 | v0.4.3（2025-12-15） | loopback 即時字幕（中文），自動存檔 |
| [abb128/LiveCaptions](https://github.com/abb128/LiveCaptions) | Linux GTK | 1,805 | GPL-3.0 | 2025-02-27 | — | Linux 即時字幕（只有英文） |
| [chidiwilliams/buzz](https://github.com/chidiwilliams/buzz) | 桌面（Python／Qt） | 22,436 | MIT | 2026-11-05 | v1.4.5（2026-08-23） | 離線轉寫為主，含即時錄音＋LLM 翻譯 |
| [thewh1teagle/vibe](https://github.com/thewh1teagle/vibe) | Tauri 桌面 | 8,012 | MIT | 2026-10-30 | v3.3.0（2026-10-29） | 同為 Tauri，離線轉寫為主 |
| [mkiol/dsnote](https://github.com/mkiol/dsnote)（Speech Note） | Linux Qt | 1,709 | MPL-2.0 | 2026-11-05 | v4.9.0（2026-06-28） | Linux 離線 STT／TTS／MT |
| [royshil/obs-localvocal](https://github.com/royshil/obs-localvocal)（原 locaal-ai） | OBS 外掛（C++） | 1,660 | GPL-2.0 | 2026-11-05 | 0.6.2（2026-04-21） | 直播主端字幕＋翻譯 |
| [ufal/whisper_streaming](https://github.com/ufal/whisper_streaming) | 函式庫 | 3,679 | MIT | 2025-11-12 | — | LocalAgreement-n；官方稱已被 SimulStreaming 取代 |
| [ufal/SimulStreaming](https://github.com/ufal/SimulStreaming) | 函式庫 | 698 | MIT | 2026-07-12 | 無正式版號 | AlignAtt＋LLM 翻譯串接；IWSLT 2025 |
| [QuentinFuxa/WhisperLiveKit](https://github.com/QuentinFuxa/WhisperLiveKit) | 伺服器＋Web UI | 11,218 | Apache-2.0 | 2026-11-01 | v0.2.26（2026-08-29） | 串流策略、協定欄位、diarization |
| [collabora/WhisperLive](https://github.com/collabora/WhisperLive) | 伺服器 | 4,350 | MIT | 2026-11-02 | v0.10.0（2026-09-07） | 重複輸出 commit 規則、hotwords |
| [KoljaB/RealtimeSTT](https://github.com/KoljaB/RealtimeSTT) | 函式庫 | 10,231 | MIT | 2026-10-30 | v1.1.2（2026-08-30） | 即時模型＋主模型雙路徑（與 kikitori 同構） |
| [ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp)（`examples/stream`） | C++ | 54,952 | MIT | 2026-11-05 | v1.9.5（2026-10-06） | 最簡單的滑動視窗基準 |
| [k2-fsa/sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) | 函式庫 | 15,758 | Apache-2.0 | 2026-11-05 | v2.0.1（2026-10-26） | ONNX 串流 ASR、VAD、diarization、標點 |
| [moonshine-ai/moonshine](https://github.com/moonshine-ai/moonshine) | 函式庫＋模型 | 11,418 | MIT（舊版非英語模型例外） | 2026-11-05 | v0.1.5（2026-08-24） | 串流 STT |
| [kyutai-labs/delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling)／[hibiki](https://github.com/kyutai-labs/hibiki) | 模型 | 3,051／1,536 | MIT＋Apache-2.0／Apache-2.0 | 2026-01-26／2026-09-09 | — | 原生串流 STT（en/fr）、同步語音翻譯（fr→en） |
| [mistralai/Voxtral-Mini-4B-Realtime-2602](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602) | 模型 | — | Apache-2.0 | HF 2026-03-11 | — | 少數支援日文的原生串流 ASR |
| [m-bain/whisperX](https://github.com/m-bain/whisperX) | 離線函式庫 | 25,104 | BSD-2-Clause | 2026-10-22 | v3.8.6（2026-05-25） | 對照：離線、詞級時間、diarization |
| [TransWithAI/Faster-Whisper-TransWithAI-ChickenRice](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice) | 離線批次（Windows） | 2,550 | MIT | 2026-07-05 | v1.10（2026-07-05） | 日→中直譯 Whisper、ASMR 用 VAD |
| [LiveTL/LiveTL](https://github.com/LiveTL/LiveTL) | 瀏覽器擴充 | 619 | AGPL-3.0 | 2026-10-30 | livetl-v10.0.2（2026-10-13） | VTuber 直播：篩選聊天室裡的人工翻譯 |
| [HIllya51/LunaTranslator](https://github.com/HIllya51/LunaTranslator) | Windows 桌面 | 14,024 | GPL-3.0 | 2026-11-02 | v12.1.3.8（2026-11-02） | 日文遊戲翻譯；術語表、注音、查詞、網頁輸出 |
| [ramjke/Translumo](https://github.com/ramjke/Translumo) | Windows OCR 翻譯 | 5,905 | Apache-2.0 | 2026-09-22 | v1.1.0（2026-09-22） | 對照：畫面文字（OCR） |
| [OneMoreGres/ScreenTranslator](https://github.com/OneMoreGres/ScreenTranslator) | OCR 翻譯 | 1,303 | MIT | 2026-09-02 | 3.3.0（2022-07-30） | 對照；README 自稱「almost abandoned」 |
| [niedev/RTranslator](https://github.com/niedev/RTranslator) | Android | 10,611 | Apache-2.0 | 2026-11-04 | — | 對照：手機端面對面口譯 |

找不到或不列入：Kotoba 官方 repo（[kotoba-tech/kotoba-whisper](https://github.com/kotoba-tech/kotoba-whisper)，104 stars，最後 push 2024-10-23）只有訓練與蒸餾程式，**沒有官方即時 demo**；TLNotes 找不到開源 repo。

## 2. 各專案分析

### 2.1 直接競品（電腦聲音 → 字幕＋翻譯）

**LiveCaptions-Translator**（Apache-2.0）
- 擷取／辨識：不自己做 ASR，用 UI Automation 讀 Windows 11（22H2+）內建「即時輔助字幕」的文字（`LiveCaptionsHandler.GetCaptions`，README 稱每次 10–20 ms），開麥克風選項即可做口譯。只支援 Windows 11。
- 部分結果策略（`src/Translator.cs`）：每 25 ms 輪詢全文；以句尾標點切出最後一句；最後一句太短就併入前一句。**有句尾標點就立刻送翻譯**；沒有標點時，原文變動超過 `MaxSyncInterval`（預設 3）次或停住 `MaxIdleInterval`（預設 50 次 × 25 ms ≈ 1.25 s）就送出未完成句。完整句翻譯顯示後**停 720 ms**（"choke for better visual experience"），避免下一句立刻蓋掉。
- 翻譯佇列（`TranslationTaskQueue.cs`）：任何一個任務完成時，取消並移除所有比它舊的任務——「最新完成者勝」。
- 翻譯後端：Ollama、OpenAI 相容、OpenRouter、Google、DeepL、有道、百度、MTranServer、LibreTranslate；README 建議用 LLM（擅長處理不完整句子）。可選 context-aware（帶前文）、自訂 prompt。
- UI：Fluent UI、透明無邊框 overlay（可「嵌入畫面」完全不擋操作）、句數可調、延遲顯示（`LatencyShow`，在譯文前加 `[xxxx ms]`）、暫停／繼續、一鍵複製、Log Cards（最近幾句卡片）、歷史紀錄**匯出 CSV**。
- 弱點：綁 Windows 11 與微軟的語言包；UI Automation 讀別的程式的視窗很脆弱（程式碼裡要處理 LiveCaptions 被關掉後重啟）；沒有自己的 VAD 與幻覺控制；離線翻譯要另架服務。

**stream-translator-gpt**（MIT）
- 輸入：直播／影片網址（yt-dlp，可帶 `--cookies` 看會員限定直播）、本機檔案、系統聲音（`device`）、麥克風。
- 切段：Silero VAD，**動態 VAD 門檻**與**動態無語音門檻**（預設開），盡量讓片段接近 `--target-audio-length` 5 s，`--max-audio-length` 30 s，保留 0.5 s 前綴。沒有部分結果，一段一段出。
- 辨識：Whisper／faster-whisper／SimulStreaming／HF ASR／OpenAI Transcription API；`--transcription-keywords`（關鍵字當 initial prompt）、`--transcription-context`（上一句當 prompt）。
- 翻譯：GPT／Gemini（可改 base URL）；`--translation-history-size` 預設 3（README：弱模型建議設 0）；`--translation-timeout` 10 s 超時就丟棄；`--use-json-result` 給本機模型。README 明說 prompt 可帶「主播是誰、直播在講什麼、偏好的術語」，翻譯與修正 ASR 錯誤都會更準。
- 輸出：終端機、檔案（`--output-timestamps` 產生 SRT）、Discord／Telegram／Cqhttp。
- 弱點：沒有 GUI 字幕視窗、沒有部分結果，延遲至少一個片段；預設依賴雲端 LLM API。

**Sokuji**（AGPL-3.0）
- 雙向會議口譯：**同一個 session 同時收麥克風與系統聲音**，兩個方向一起翻；可把翻譯後的語音送到虛擬麥克風給 Zoom／Meet；有字幕模式。
- 本機推論用 sherpa-onnx（WASM）、Transformers.js、WebGPU：README 列 44 個 ASR、75 個翻譯（69 個 Opus-MT 語言對＋Qwen 2.5／3／3.5、Hunyuan-MT 1.5、TranslateGemma）、137 個 TTS 模型（官方宣稱）。雲端：OpenAI、Gemini、Palabra、Doubao、Soniox、OpenAI Realtime 相容。
- 弱點（推論）：功能面很廣，品質取決於瀏覽器端小模型；AGPL。

**StreamLingo**（MIT，35 stars，早期）
- Chrome 分頁音訊 → 本機 Python（Whisper base）→ Ollama；「連續說話時翻譯已穩定的片段，不必等講者停下」。
- **公開量測**（RTX 3080、Whisper base、`qwen3.5:9b`，30 分鐘、各 670 句以上）：句末到收到譯文 P95 2.414 s（英→中）／3.172 s（中→英）；收到譯文到第一次顯示 P95 3.859 s／6.120 s（官方宣稱，單一機器）。
- 顯示規則：完整譯文依序顯示，長譯文在句子或詞的邊界分頁，**每頁至少顯示 4 秒**，相鄰短句可合併；附 `Doctor-StreamLingo.cmd` 本機診斷。
- 弱點：只支援 YouTube＋Chrome；顯示排隊時延遲會累積（README 自己承認 fast speech 會讓顯示延遲變大）。

**Kitori**（GPL-3.0，3 stars，2026-10-20 建立）
- 與 kikitori **同名同義**（README：名字取自「聞き取り」），目標族群也一樣（看日語影片學日語）。只顯示原文，翻譯交給 LunaTranslator。
- 功能：Sudachi **振假名**；「白色＝已確認、淺藍＝辨識中」**兩色顯示**，注音跟著更新、盡量減少跳動；一段時間沒有新文字就**清空**；字幕保留時間可調；**模型與下載快取占用顯示、可刪除**；本機媒體**預讀**；後端 Whisper、Kotoba-Whisper v2、Qwen3-ASR、Voxtral Mini 4B Realtime、ReazonSpeech K2 v2，或 Windows 即時輔助字幕；Whisper／Kotoba 可選幻覺詞屏蔽。
- 弱點：Windows only；安裝需下載約 5.1 GB 執行環境（官方 README）；很早期。
- 對 kikitori 的意義：**名稱撞名風險**；功能上證明「語言學習者」要的是振假名與確認／未確認分色。

**TMSpeech**（MIT）
- WASAPI loopback；sherpa-onnx 串流模型；作者在 AMD 5800U 筆電實測 CPU < 5%（作者宣稱）；結果**依日期自動存檔**到「文件／TMSpeechLogs」；**外部命令辨識器**協定：子行程 stdout 單一 `\n` 更新目前句、`\n\n` 表示本句結束，stderr 當日誌。
- 弱點：以中文為主，沒有翻譯。

**abb128/LiveCaptions**（GPL-3.0）：Linux、april-asr（LSTM transducer）純 CPU；**詞元信心淡出**（低信心的字淡色顯示）；只支援英文；2025-02 後無更新。

**Buzz**（MIT）
- 主要是檔案轉寫；即時錄音只吃麥克風（macOS 抓系統聲音要自己裝 BlackHole 等虛擬裝置，docs 有教學）。
- 即時模式「Append and correct」：重疊片段反覆轉寫，**「Hide unconfirmed」**可隱藏只出現過一次的尾巴（換取延遲）；`Queue` 會增長時要調大 transcription step；有 Presentation window。
- 翻譯：OpenAI 相容 API（含 Ollama、LM Studio），使用者自寫指示。
- 輸出：TXT／SRT／VTT 匯出；**「Enable live recording transcription export」把即時文字寫到文字檔，可給 OBS 讀**；說話者辨識（檔案）、語音分離、資料夾監看、CLI、外掛。

**Vibe**（MIT，Tauri）：離線轉寫；SRT／VTT／TXT／HTML／PDF／JSON／DOCX；系統聲音與麥克風；diarization；VAD 輔助的「穩定時間碼」模式；Ollama 摘要；自動更新。即時翻譯只有「翻成英文」。

**Speech Note**（MPL-2.0）：Linux（Flatpak）離線 STT（whisper.cpp、faster-whisper、Vosk、april-asr、Qwen3-ASR…）、TTS、MT（Bergamot）；應用程式內**模型瀏覽器**下載模型；**全域快速鍵在 Wayland 走 XDG Desktop Portal 的 GlobalShortcuts**；「插入到目前視窗」；STT 可輸出 SRT。

### 2.2 直播主端

**OBS LocalVocal**（GPL-2.0）
- whisper.cpp（可換任何 GGML 模型）＋**Silero VAD ONNX**（`data/models/silero-vad`）；部分結果；100 種語言。
- 翻譯：雲端（DeepL、Claude、AWS、Azure、Google、自訂 API）、Whisper 內建翻英、本機 NMT（CTranslate2）。
- 輸出：OBS 文字來源、**.txt／.srt（可聚合）**、**與 OBS 錄影時間碼同步**、**透過 RTMP 把字幕送到 YouTube／Twitch**；**過濾或取代任何字串**。
- 硬體：發行包分 generic／NVIDIA／AMD，CPU 後端在啟動時動態選最佳指令集，避免不支援的後端讓 OBS 崩潰；預設 CPU，GPU 要使用者自己開。
- 弱點：只能在 OBS 裡用（觀眾端不適用）；GPL。

### 2.3 串流 ASR 引擎／函式庫（策略來源）

**whisper_streaming → SimulStreaming（ÚFAL，MIT）**
- LocalAgreement-n：連續 n 次（每次多一段新音訊）的轉寫結果有共同前綴，就把前綴 commit；buffer 依 Whisper segment 或確認的句子裁切。論文：未切分長篇英文測試集上延遲 3.3 s（官方宣稱，[IJCNLP-AACL 2023 demo](https://aclanthology.org/2023.ijcnlp-demo.3.pdf)）。
- SimulStreaming：Simul-Whisper 的 AlignAtt（解碼到接近音訊尾端 `frame_threshold` 就停）；「CIF」模型判斷尾字是否完整，沒有 CIF 模型時**一律裁掉最後一個字**（常是半個字）；`--init_prompt`／`--static_init_prompt`（術語）、`--max_context_tokens`；Whisper＋EuroLLM 串接翻譯；README 稱比 whisper_streaming 快約 5 倍、IWSLT 2025 同步翻譯共享任務最佳（官方宣稱，[論文](https://aclanthology.org/2025.iwslt-1.41/)）。只有 PyTorch 後端；既有研究筆記已記錄 CPU 太慢、建議 ≥10 GB VRAM。

**WhisperLiveKit**（Apache-2.0）
- 後端：SimulStreaming（AlignAtt）、LocalAgreement、Voxtral、Qwen3-ASR causal、MLX、FunASR；VAC（Silero）；Streaming Sortformer **說話者分離**；NLLW（NLLB）**同步翻譯 200 種語言**。
- 協定（`docs/API.md`）：`lines`（已 commit 的句子，含 speaker、translation）＋`buffer_transcription`／`buffer_translation`（尚未 commit、每次覆寫）＋**`remaining_time_transcription`（還沒處理的音訊秒數，即處理落後量）**＋`translation_error`（翻譯掛了轉寫照跑）。
- 選項：`--translate-on-complete`（只翻譯被標點、靜音或串流結束確認的句子；「減少翻譯閃爍、代價是延遲」）、`--punctuation-split`、`--confidence-validation`、每個 session 的 `context`（術語、人名）。
- 工具：`wlk bench`（本機 WER／RTF）、`wlk diagnose`（把音檔餵進管線、定期探測內部狀態、產生時間軸與健康檢查）、`wlk pull/rm`（模型管理）、`wlk transcribe --format srt`。

**WhisperLive**（Collabora，MIT）
- faster-whisper／TensorRT／OpenVINO 後端；`no_speech_thresh` 0.45；`same_output_threshold` 10：**未完成的最後一段連續 10 次輸出相同就 commit**；有詞級時間時把最後一個字切出來留給下一輪重算（`_split_last_segment_at_final_word`）；`hotwords`（faster-whisper keyword boosting）；說話者欄位；翻譯執行緒（small100／M2M100）；Chrome／Firefox 擴充。

**RealtimeSTT**（MIT）
- 跟 kikitori 同構：`realtime_model_type`（預設 tiny）做即時文字、主模型做最終；`realtime_processing_pause` 0.2 s；WebRTC VAD＋Silero 雙 VAD；`post_speech_silence_duration` 0.6 s。
- **`early_transcription_on_silence`**：靜音達到指定毫秒就先跑最終辨識，若又開始說話就丟棄結果。
- **即時標點切分**（`realtime_punctuation_split_marks="sentence"`）：當「重複觀察到的穩定即時文字」與詞級時間碼都同意某個句號／問號／驚嘆號是邊界時，把左半送最終辨識、右半留作下一段；需要詞級時間碼，有短片段保護。
- **`on_realtime_transcription_stabilized`**：穩定化後的即時文字事件。
- CPU 建議：sherpa-onnx Nemotron 串流模型（只處理新影格）出即時文字＋Parakeet TDT 出最終，「比反覆重轉越來越長的 buffer 好得多」（官方宣稱；英文為主）。

**whisper.cpp `whisper-stream`**：每 `--step` 500 ms 對最近 `--length` 5 s 滑動視窗重算；`--step 0` 為 VAD 模式（簡單能量 VAD，`-vth` 0.6）；`-tdrz` 標說話者輪替。README 自稱 "naive example"。

**sherpa-onnx**（Apache-2.0）：ONNX 的串流與非串流 ASR、Silero VAD、**說話者分離**、標點、熱詞、語言辨識；官方提供「VAD＋非串流模型」的模擬串流範例（等於 kikitori 的做法）。既有筆記已確認日文 ReazonSpeech zipformer 在 sherpa-onnx 是 **offline** 模型，不是原生串流。

**原生串流模型**
- Voxtral Mini 4B Realtime 2602（Apache-2.0）：延遲可設 80 ms–2.4 s，建議 480 ms；FLEURS 平均 WER 480 ms 8.72%、960 ms 7.70%；日文 480 ms 9.59%；需 ≥16 GB GPU，建議 vLLM（官方 model card）。
- Kyutai STT（DSM）：`stt-1b-en_fr` 0.5 s 延遲＋語意 VAD、`stt-2.6b-en` 2.5 s；只有英／法。Hibiki：fr→en 同步語音翻譯。對 kikitori 的日文需求目前用不上。
- Moonshine：主打「在使用者還在說話時就做事」的串流；程式與多數模型 MIT，舊版非英語非串流模型為非商用授權（README）。

### 2.4 日文／VTuber 專用

**TransWithAI ChickenRice**（MIT）：離線批次（不是即時）。用 `chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2`（Apache-2.0，**CT2 格式**，README 稱以 5000 小時音訊訓練的日→中直譯模型）一步把日語語音變中文；轉寫版用 `TransWithAI/whisper-ja-1.5B-ct2`（HF 未標授權）；**ASMR／音聲最佳化的 VAD**（`TransWithAI/Whisper-Vad-EncDec-ASMR-onnx`，MIT）；輸出 SRT／VTT／LRC；依 GPU 型號分 CUDA 11.8／12.2／12.8、AMD ROCm 發行包。

**LiveTL**（AGPL-3.0）：不做 ASR。從 YouTube 聊天室篩出人工翻譯者的訊息（辨識 `[EN]`、`【中】` 等語言標籤，`src/js/filter.js`），自訂正規表示式過濾、TTS、選取訊息匯出、截圖匯出。代表 VTuber 圈「人工翻譯＋聊天室」的既有習慣。

**LunaTranslator**（GPL-3.0）：Galgame 翻譯（HOOK、OCR；語音只用 Windows 語音辨識）。值得借鏡的是**翻譯最佳化三層**（`docs/zh/transoptimi.md`）：翻譯前替換（可正規表示式）、**專有名詞翻譯**（LLM 介面 prompt 含 `DictWithPrompt` 時把詞條放進 prompt，傳統翻譯則先換成佔位符再換回）、翻譯結果修正；**每個遊戲有自己的詞典**並可繼承全域詞典，從 VNDB 自動帶入角色名；日語分詞＋假名注音、AnkiConnect、Yomitan；**本機網頁**（`/page/mainui` 與主視窗同步的字幕頁）。

### 2.5 對照組

- **WhisperX**（BSD-2）：離線批次，faster-whisper 批次推論「large-v2 70 倍即時」（官方宣稱）、wav2vec2 強制對齊取得詞級時間、pyannote diarization（需 HF gated 授權）、VAD 前處理減少幻覺。說明「詞級時間＋diarization」目前主要在離線場景成熟。
- **Translumo**（Apache-2.0）／**ScreenTranslator**（MIT）：OCR 路線，Translumo 同時跑多個 OCR 並用 ML 模型挑最佳結果；適合遊戲與燒錄字幕，和 kikitori 的聲音路線互補而非競爭。
- **RTranslator**（Apache-2.0）：Android 本機 NLLB＋Whisper，藍牙耳機面對面對話模式；平台不同。

## 3. 比較矩陣（vs kikitori）

符號：✓ 有；△ 部分／間接；✗ 沒有；? 官方資料未寫明。

### 3.1 擷取與辨識

| 項目 | kikitori | LiveCaptions-Translator | stream-translator-gpt | Sokuji | Buzz | LocalVocal | WhisperLiveKit | RealtimeSTT | Kitori | TMSpeech |
|---|---|---|---|---|---|---|---|---|---|---|
| 系統聲音 | ✓ 三平台原生 | △ 透過 Win11 即時輔助字幕 | ✓ loopback＋直播網址 | ✓ 桌面版／分頁 | △ macOS 需虛擬裝置 | ✓ 任何 OBS 音源 | △ 由客戶端送 | △ 外部音訊 API | ✓ Windows | ✓ Windows |
| 麥克風 | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ✓ | ? | ? |
| 麥克風＋系統聲音同時 | ✗ | △ | ✗ | ✓ | ✗ | △ 多個來源各掛濾鏡 | ✗ | ✗ | ? | ✗ |
| 本機 ASR | Whisper（CT2） | Windows 內建 | 多種＋雲端 | sherpa-onnx／WebGPU＋雲端 | Whisper 多後端 | whisper.cpp | 多種 | 多種 | 多種＋Windows | sherpa-onnx |
| 部分結果策略 | 雙路徑：每 ≥700 ms 重轉整句（greedy），句尾 beam 5 取代 | 標點觸發＋變動次數／停滯計數 | 無（片段制） | ? | 重疊片段＋隱藏未確認 | 有部分結果 | LocalAgreement／AlignAtt，committed＋buffer | 即時模型＋主模型、穩定化文字 | 已確認／辨識中兩色 | 原生串流 |
| 穩定前綴（commit）顯示 | ✗（整行淡色） | ✗ | — | ? | ✓ | ? | ✓ | ✓ | ✓ | △ |
| VAD | Silero v6 ONNX | — | Silero（動態門檻） | ? | 音量門檻 | Silero ONNX | Silero（VAC） | WebRTC＋Silero | ? | ? |
| 幻覺處理 | ✓ 語言別片語表、壓縮比、no_speech、低信心 | — | ? | ? | 音量門檻 | ✓ 使用者過濾／取代 | no_speech | faster-whisper VAD 濾波 | ✓ 幻覺詞屏蔽 | — |
| 熱詞／ASR 術語 | ✗（只有前文 prompt） | — | ✓ keywords | ? | ? | ? | ✓ context | ✓ initial prompt | ? | ? |
| 說話者分離 | ✗ | ✗ | ✗ | ✗（以來源區分方向） | ✓（檔案） | ✗ | ✓ Sortformer | ✗ | ✗ | ✗ |
| 硬體推薦／模型下載 | ✓ 偵測＋推薦＋延後下載 | — | ✗ | 模型清單 | 模型下載 | 依硬體分發行包 | `wlk pull`／`bench` | 安裝指令 | 安裝時選 | ? |

### 3.2 翻譯與輸出

| 項目 | kikitori | LiveCaptions-Translator | stream-translator-gpt | Sokuji | Buzz | LocalVocal | WhisperLiveKit | Kitori | LunaTranslator |
|---|---|---|---|---|---|---|---|---|---|
| 翻譯後端 | OpenAI 相容（預設 Ollama），模型別提示範本 | 9 種含 Ollama | GPT／Gemini | 雲端 Realtime＋本機 Opus-MT／LLM | OpenAI 相容 | 雲端＋本機 NMT | NLLB（NLLW） | ✗（交給 Luna） | 大量 |
| 前文 | ✓ 3 句同語言 | △ 可選 | ✓ 3 句 | ? | ? | ? | ? | — | ? |
| 術語表 | ✗ | ✗ | △ 寫在 prompt | ? | △ 寫在指示 | △ 後處理取代 | △ context | ✗ | ✓ 三層、每遊戲一份 |
| 部分句翻譯 | ✓ 串流、最新槽 | ✓ 最新完成者勝 | ✗ | ? | ? | ? | 可選只翻已確認句 | — | — |
| 只顯示原文 | ✓ | △ 暫停翻譯 | ✓ 不設 prompt | ? | ✓ | ✓ | ✓ | ✓（只有原文） | — |
| 透明置頂字幕 | ✓ 穿透、1–3 句 | ✓ 可嵌入畫面 | ✗ | ✓ 字幕模式 | △ Presentation window | OBS 畫面內 | Web UI | ✓ | ✓ |
| 最短顯示／閒置清空 | ✗ | △ 完整句停 720 ms | — | ? | ? | ? | ? | ✓ 閒置清空 | ? |
| 延遲／落後指示 | ✗ | ✓ 每句 ms | ✗ | ? | △ Queue | ? | ✓ remaining_time | ? | ? |
| 匯出 | ✗ | CSV | SRT／文字檔 | ? | TXT／SRT／VTT＋即時文字檔 | TXT／SRT | SRT（檔案模式） | ? | ? |
| OBS／直播整合 | ✗（可用視窗擷取） | ✗ | Discord／Telegram | 虛擬麥克風 | 即時文字檔 | 原生＋RTMP 字幕 | — | Luna | 本機網頁 |
| 振假名 | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✗ | ✓ Sudachi | ✓ |
| 平台 | Win／mac／Linux | Win11 | 跨平台（Python） | Win／mac／Linux＋擴充 | Win／mac／Linux | Win／mac／Linux | 伺服器 | Win | Win |
| 授權 | MIT | Apache-2.0 | MIT | AGPL-3.0 | MIT | GPL-2.0 | Apache-2.0 | GPL-3.0 | GPL-3.0 |

**kikitori 的相對優勢**：三平台原生 loopback（多數競品只有 Windows 或要虛擬裝置）；不依賴 PyTorch、可打包的單一桌面程式；語言別幻覺片語表（競品多半只有 no_speech 門檻或使用者手動規則）；硬體偵測＋推薦＋延後下載（只有 WhisperLiveKit 的 CLI 接近）；模型別翻譯提示範本與簡繁處理；20 語言任意組合＋13 介面語系。

**kikitori 的相對缺口**：沒有匯出、沒有術語表、即時字幕整行淡色（沒有穩定前綴）、沒有延遲指示、沒有 OBS 輸出、沒有振假名（明明目標族群是語言學習者）。

## 4. 建議 kikitori 採用的點子（依優先順序）

工作量：S ≈ 1–2 天、M ≈ 3 天–1 週、L > 1 週（含測試與文件，粗估）。標「已具備」者不需動作。新依賴、新設定 schema、協定變更、開網路埠都需先問使用者／寫 ADR（依 `CLAUDE.md`）。

### 第一優先（價值高、成本低到中）

**1. 字幕紀錄匯出（SRT／VTT／TXT／CSV），可選每次 session 自動存檔**
- 做什麼：字幕紀錄頁加「匯出」；SRT／VTT 用 `start_sample/16000`、`end_sample/16000` 當時間碼，原文與譯文可選一行或兩行；另提供「自動把每次 session 存成檔案」（TMSpeech 依日期存檔的做法）。
- 為什麼：Buzz、Vibe、LocalVocal、stream-translator-gpt、WhisperLiveKit 都有 SRT；LiveCaptions-Translator 有 CSV；TMSpeech 預設自動存檔。這是競品間最普遍、kikitori 唯一完全沒有的基本功能；語言學習者要回頭複習。
- 工作量：S–M。
- 風險：字幕紀錄只留 300 句——匯出應由引擎端或 Rust 端累積完整 session，而非前端環形緩衝；寫檔需要存檔對話框（`tauri-plugin-dialog` 是新依賴，需先問）或固定寫到使用者資料夾；時間碼是 session 起點起算，不是影片時間（要在 UI 說明）。
- 模組：`app/src/lib/subtitles.ts`、字幕紀錄頁、`app/src-tauri`（寫檔）；或 `kikitori.engine` 新方法 `transcript.export`（協定變更）。

**2. 即時字幕的「穩定前綴」：LocalAgreement-2 文字層＋只在穩定部分變動時重翻**
- 做什麼：同一句連續兩次快路徑結果的最長共同前綴（日文以字元、英文以詞為單位）視為「穩定」，overlay 以正常亮度顯示穩定部分、淡色顯示尾巴；快路徑翻譯只在穩定前綴變長時才送（可選），尾巴不翻。
- 為什麼：whisper_streaming 的 LocalAgreement-2（論文、MIT）、WhisperLive 的 `same_output_threshold`、Buzz 的「Hide unconfirmed」、WhisperLiveKit 的 `lines`／`buffer_transcription` 與 `--translate-on-complete`（「減少翻譯閃爍」）、RealtimeSTT 的 stabilized 事件、Kitori 的兩色顯示、abb128/LiveCaptions 的低信心淡出——都是在處理「部分結果跳動」。kikitori 目前整行淡色、每 700 ms 整句重寫，greedy 結果在句中常改字。純文字層比對，**不增加任何推論成本**，反而可以減少送給 Ollama 的即時翻譯請求。
- 工作量：S–M。
- 風險：`SubtitleUpdate` 要加欄位（例如 `stable_length`）＝協定變更（可做成新增的可選欄位以維持相容）；kikitori 每次重轉整句而非滑動視窗，前綴比對有效，但 greedy 偶爾整句改寫會讓穩定前綴回縮（要允許回縮或只增不減，需實驗決定）；同時影響最終取代時的動畫。
- 模組：`kikitori.pipeline`（即時 worker 保存上一次結果）、`kikitori.engine` 事件欄位、`app/src/lib/subtitles.ts`、overlay CSS。

**3. 術語表／熱詞與「直播設定檔」**
- 進行中：同日新增、尚未 commit 的 `docs/specs/vtuber-profile.md` 已規劃**內建**直播用語術語表（`translate/glossary.py`，只注入原文出現的詞、最多 8 條，generic 用「術語：A → B」、HY-MT 用官方術語範本），並寫明「使用者自訂術語表見競品調查後的規格補充」。下面是競品給「使用者自訂」部分的依據，內建表的做法與此一致。
- 做什麼：使用者維護「原文 → 譯文」詞條（主播名、作品名、梗）與一段自由描述（主播是誰、在玩什麼）。用法：(a) ASR：把詞條原文接在 Whisper prompt 前面（類似 SimulStreaming 的 `--static_init_prompt`）；(b) 翻譯：generic 範本加「術語表」段落；HY-MT 改用官方的術語干預範本（model card：「参考下面的翻译：{source_term} 翻译成 {target_term} …」）；Sakura 填官方 `[Glossary]`（`translate.md` 目前寫「留空」）；(c) 可存多份設定檔切換（LunaTranslator 每遊戲一份、可繼承全域）。
- 為什麼：stream-translator-gpt README 明說帶主播、主題、術語能讓翻譯更準並修正 ASR 錯誤；SimulStreaming 標榜 in-domain terminology；WhisperLiveKit 的 `context`、WhisperLive 的 `hotwords`、LunaTranslator 的專有名詞翻譯都是同一需求。VTuber 直播的人名、組合名、遊戲專有名詞是 ASR 與 LLM 最常錯的地方（推論）。
- 工作量：M。
- 風險：Whisper prompt 過長會讓模型把 prompt 內容「念」出來（幻覺）——需限制長度並用既有的評測選單驗證；HY-MT 預設組目前關閉前文，術語範本是否同樣會漏出指令要實測；設定檔要升 `schema_version`（遷移）。
- 模組：`kikitori.config`、`kikitori.asr`（prompt 組成）、`kikitori.translate`（`prompts.py` 三種範本）、`kikitori.pipeline`、`app` 設定頁。

**4. 處理延遲與落後指示**
- 做什麼：引擎定期回報 (a) 每句「句尾 → 完整譯文」耗時、(b) 落後量＝擷取位置 − 最後處理到的位置（秒）、(c) 跳過的即時工作數（已有 `dropped_partials`）；狀態列顯示，落後持續變大時提示改用較小模型。
- 為什麼：WhisperLiveKit 協定直接提供 `remaining_time_transcription`；LiveCaptions-Translator 可在每句前顯示 `[xxxx ms]`；Buzz 要使用者盯著 `Queue`；StreamLingo 公開 P95 延遲。kikitori 的完整佇列不丟句、capture 只留 5 秒，落後時使用者看不到原因。
- 工作量：S。
- 風險：新事件或 `status.detail` 欄位＝協定變更（相容性小）；時間要用樣本數計（遵守既有約定）。
- 模組：`kikitori.pipeline`、`kikitori.engine`（新事件 `metrics`）、`app` 狀態列。

**5. 字幕閱讀節奏：最短顯示時間、閒置自動清空**
- 做什麼：overlay 每句至少顯示 N 秒（可調）才被擠出；超過 M 秒沒有新句就淡出清空。
- 為什麼：StreamLingo 每頁至少 4 秒、長譯文分頁；Kitori 閒置清空、保留時間可調；LiveCaptions-Translator 完整句停 720 ms。kikitori 目前只保留最新 1–3 句，說話快時前一句可能一閃而過，安靜時舊字幕一直掛著。
- 工作量：S。
- 風險：最短顯示會讓顯示落後於語音（StreamLingo 自己承認），要設上限或在落後時放寬。
- 模組：`app/src/lib/subtitles.ts`（純函式、可測）、overlay 設定（`subtitle` 物件，引擎原樣保存，不需 schema 遷移）。

**6. OBS 用的即時文字檔輸出**
- 做什麼：可選把「目前字幕」（原文／譯文／雙語）原子寫入一個 .txt，OBS 的文字來源勾「從檔案讀取」；同一機制也可寫滾動式 .srt。
- 為什麼：Buzz 的「live recording transcription export」與 LocalVocal 的 .txt／.srt 輸出就是給 OBS 用；不需要開網路埠，符合 kikitori「不開網路埠」的架構原則。VTuber 剪輯、翻譯台實況都會用到（推論）。
- 工作量：S。
- 風險：頻繁寫檔（每次即時更新）要節流；路徑由使用者指定，需驗證。
- 模組：`app/src-tauri`（或 `kikitori.engine`）、設定頁。

### 第二優先（中成本，或需要實驗／決策）

**7. 長句在穩定標點處提早切句（取代 10 秒硬切）**
- 做什麼：即時路徑的穩定前綴（見 #2）中出現句末標點（。？！.?!）且持續穩定時，呼叫既有的 `Segmenter.request_end()` 讓 VAD 在下一個視窗結束本句。
- 為什麼：RealtimeSTT 的即時標點切分（重複觀察＋詞級時間碼雙重確認）、whisper_streaming 的 `buffer_trimming=sentence`、WhisperLiveKit 的 `--punctuation-split`；既有筆記也推論「已 commit 前綴才送標點判斷，避免字幕跳動」。直播常常連續講超過 10 秒，硬切會切在句中。
- 工作量：M–L。
- 風險：kikitori 沒有詞級時間碼，切點只能用「結束當下」而非精確對到標點位置，可能把下一句的開頭切進本句（需評測）；Whisper 日文標點不穩定。
- 模組：`kikitori.pipeline`、`kikitori.vad`（已預留 `request_end()`）、`experiments/`。

**8. 靜音初期就先跑完整辨識（投機式）**
- 做什麼：靜音達到例如 0.3 s 時先把目前音訊送完整路徑；若在 0.6 s 句尾門檻前又有人聲就丟棄結果。
- 為什麼：RealtimeSTT 的 `early_transcription_on_silence`。完整結果可提早約 0.3 s 出現（推論，需實測）。
- 工作量：M。
- 風險：浪費算力（CPU 使用者尤其明顯）；完整佇列「一句不丟、依序送出」的保證要重新設計；需要 VAD 對外公開「靜音開始」事件。
- 模組：`kikitori.vad`、`kikitori.pipeline`。

**9. 使用者可編輯的過濾與取代規則**
- 做什麼：使用者自訂 (a) 要濾掉的句子（擴充既有幻覺片語表）、(b) 原文取代（ASR 常錯的名字）、(c) 譯文取代。
- 為什麼：LocalVocal「Filter out or replace any part of the produced captions」；LunaTranslator 翻譯前替換、翻譯結果修正；LiveTL 自訂正規表示式。kikitori 的片語表只能改程式碼。
- 工作量：S–M。
- 風險：若開放正規表示式，Python `re` 沒有 timeout，惡意或寫錯的式子可能卡住 worker——建議先只支援純字串，或限制長度與語法；設定檔 schema 升版。
- 模組：`kikitori.asr`（`filtering.py`）、`kikitori.translate`（後處理）、`kikitori.config`、設定頁。

**10. 用本機實測校正推薦組合**
- 做什麼：第一次執行或按「重新偵測」時，用內建短音檔量測候選模型的 RTF（每次辨識耗時／音訊長度），推薦結果依實測而非只看硬體等級表。
- 為什麼：WhisperLiveKit `wlk bench`（本機 WER／RTF）；LocalVocal 在啟動時動態選可用的 CPU 後端；kikitori 已在即時路徑量測耗時並自動拉長間隔，等於已有量測基礎。
- 工作量：M。
- 風險：量測需要模型已下載（與「延後下載」衝突，只能量已下載的）；量測時間影響第一次啟動體驗。
- 模組：`kikitori.hardware`、`kikitori.asr`、模型頁。

**11. 模型磁碟管理（顯示占用、刪除）**
- 做什麼：模型頁顯示每個已下載模型的大小與總占用，提供「刪除」。
- 為什麼：Kitori 可查看模型與下載快取占用並刪除；WhisperLiveKit `wlk rm`；Speech Note 模型瀏覽器。kikitori 有下載、沒有刪除，large-v3 級模型每個 1.5 GB 以上。
- 工作量：S–M。
- 風險：刪除是破壞性操作——UI 必須二次確認、引擎只能刪自己快取目錄內的檔案（路徑驗證）、執行中使用的模型不可刪。
- 模組：`kikitori.engine`（`models.delete`）、`kikitori.asr` 下載器、模型頁。

**12. 日文原文的振假名（ふりがな）**
- 做什麼：overlay 與字幕紀錄的日文原文在漢字上方顯示讀音（`<ruby>`），可開關。
- 為什麼：kikitori 的專案目標是「幫助語言學習者邊聽邊對照」；Kitori（Sudachi）與 LunaTranslator（分詞＋假名注音、Anki、Yomitan）都把它當核心功能。
- 工作量：M。
- 風險：需要新依賴與詞典（例如 SudachiPy＋詞典，或前端 JS 分詞器）——要先問使用者並確認授權與打包大小；即時字幕每 700 ms 重寫，注音也要跟著穩定（Kitori README 特別處理「減少跳動」，可與 #2 一起做）；Whisper 漢字錯時注音也錯。
- 模組：`kikitori.engine`／新子套件（或 `app` 端）、overlay、字幕紀錄頁。

**13. OBS 瀏覽器來源或 OBS 原生字幕（CEA-608）**
- 做什麼：(a) 在 127.0.0.1 開一個只讀的字幕網頁給 OBS「瀏覽器來源」（LunaTranslator 的 `/page/mainui` 模式）；或 (b) 透過 obs-websocket v5 的 `SendStreamCaption`（「Sends CEA-608 caption text over the stream output」）把字幕送進直播串流（LocalVocal 的 RTMP 字幕同類）。
- 為什麼：瀏覽器來源可完整套用 kikitori 的字幕樣式、比 #6 的純文字漂亮；CEA-608 讓 YouTube 觀眾可自己開關字幕。
- 工作量：M。
- 風險：(a) 違反目前「不開網路埠」的架構決定，需寫 ADR（只綁 127.0.0.1、隨機 token）；(b) obs-websocket 需要密碼——依規則只能從環境變數讀；CEA-608 只支援有限字元集，對中日文的實際效果需實測（未查證）。建議先做 #6。
- 模組：`app/src-tauri`、`docs/adr/`。

**14. 診斷報告**
- 做什麼：一鍵產生不含密鑰的診斷包（引擎版本、硬體偵測、設定（遮蔽網址帳密）、最近日誌、落後統計），方便回報問題。
- 為什麼：StreamLingo 的 `Doctor` 本機診斷（不上傳）、WhisperLiveKit `wlk diagnose`。
- 工作量：S–M。
- 風險：避免把 API key、網址帳密寫進報告（kikitori 已有遮蔽網址帳密的實作可沿用）。
- 模組：`kikitori.engine`、`kikitori.hardware`、設定頁「關於」。

### 第三優先（大成本或研究性質）

**15. 麥克風＋系統聲音同時（雙向會議），依來源上色**
- 為什麼：Sokuji 的核心賣點；kikitori 的 `interpret` 目前只收麥克風，對方的聲音若從喇叭出來就收不到。依來源區分說話者，不需要 diarization 模型就能做「我／對方」兩色。
- 工作量：L。風險：兩條擷取＋兩個 Segmenter、迴授（喇叭聲被麥克風收進來）需要回音消除；ASR 算力加倍。
- 模組：`kikitori.capture`、`kikitori.pipeline`、`kikitori.engine`（`SubtitleUpdate` 加來源）、overlay。

**16. 即時路徑改用原生串流 ASR**
- 為什麼：RealtimeSTT 明確建議 CPU 上用「串流模型出即時文字＋離線模型出最終」，取代反覆重轉越來越長的 buffer（kikitori 現在的做法）；SimulStreaming 論文也指出 LocalAgreement 類重算較耗算力。
- 現況：日文原生串流選擇少——Voxtral Mini 4B Realtime（日文 FLEURS 9.59% WER @480 ms，Apache-2.0，但需 ≥16 GB GPU、建議 vLLM）；Kyutai 只有英／法；sherpa-onnx 的日文 ReazonSpeech 模型是 offline。
- 工作量：L。風險：新後端與依賴、GPU 門檻高；先列為研究項目，用既有評測工具比較。
- 模組：`kikitori.asr`（新 `Transcriber`）、`kikitori.hardware`。

**17. 說話者分離與每位說話者的顏色**
- 為什麼：WhisperLiveKit（Streaming Sortformer）、WhisperLive、Buzz、Vibe、WhisperX 都有；VTuber 聯動直播多人說話時有幫助。sherpa-onnx 有 ONNX 版 diarization，可避開 PyTorch。
- 工作量：L。風險：BGM、重疊說話下準確率未知；串流 diarization 在 CPU 上的成本；單人直播用不到。
- 模組：`kikitori.asr` 或新子套件、`kikitori.pipeline`、overlay。

**18. Wayland 全域快速鍵走 XDG Desktop Portal**
- 為什麼：kikitori 的 `ui.md` 已記錄「Wayland 下 `global-hotkey` 可能無法註冊」；Speech Note 用 XDG Desktop Portal 的 GlobalShortcuts 介面解決。
- 工作量：M。風險：Tauri 的 global-shortcut 外掛是否支援 portal 需查證（未查證）；不同桌面環境支援度不同。
- 模組：`app/src-tauri/src/lib.rs`。

**19. 評估 ChickenRice 日→中直譯模型作為「不用 LLM」的選項**
- 為什麼：模型是 CT2 格式、Apache-2.0，kikitori 的 CTranslate2 引擎理論上可直接載入（推論）；沒有 Ollama 的使用者也能看到中文。
- 工作量：M（主要是評測）。風險：只支援日→中；輸出是簡體（kikitori 已有 OpenCC）；任務 token 與解碼設定需讀 model card 確認；沒有原文可顯示（與雙語字幕的設計衝突）。
- 模組：`kikitori.asr`（registry）、`experiments/`。

### 已具備（不需動作）

| 點子 | 競品 | kikitori 現況 |
|---|---|---|
| 即時（快）＋最終（準）雙模型 | RealtimeSTT `realtime_model_type`＋主模型 | ✓ 雙路徑，可用不同模型 |
| 落後時只做最新的即時工作 | LiveCaptions-Translator「最新完成者勝」 | ✓ 最新槽、`dropped_partials`、依耗時拉長間隔 |
| 翻譯帶前文 | stream-translator-gpt `--translation-history-size 3` | ✓ `context_lines=3`，只帶同語言前文（較細） |
| 前文當 ASR prompt | stream-translator-gpt `--transcription-context`、SimulStreaming init prompt | ✓ media 模式完整路徑 |
| no_speech／幻覺過濾 | WhisperLive `no_speech_thresh`、Kitori 幻覺詞屏蔽 | ✓ 更完整（語言別片語表、壓縮比、no_speech 借用） |
| 翻譯逾時與失敗不中斷 | stream-translator-gpt `--translation-timeout`、WhisperLiveKit `translation_error` | ✓ 翻譯失敗只顯示原文＋warning |
| 透明置頂、可穿透字幕、句數可調 | LiveCaptions-Translator overlay、Kitori | ✓ |
| 只顯示原文／暫停翻譯 | LiveCaptions-Translator 暫停、Buzz | ✓ 執行中可切換 |
| 依硬體選模型、延後下載 | LocalVocal 分硬體發行包、Speech Note 模型瀏覽器 | ✓ 偵測＋推薦＋開始時自動下載 |
| 自動更新 | Vibe、LiveCaptions-Translator | ✓ tauri-plugin-updater |

### 不建議（或暫緩）

- **直接吃直播網址（yt-dlp、cookies 看會員直播）**：stream-translator-gpt 有，但 kikitori 的 loopback 已涵蓋任何播放器；處理會員內容與平台條款有灰色地帶，cookies 屬敏感資料。
- **SimulStreaming／WhisperLiveKit 直接整合**：依賴 PyTorch，與 kikitori「不用 PyTorch、可打包」的前提衝突；既有筆記已記錄 CPU 太慢。只借策略（#2、#7）。
- **語音輸出／虛擬麥克風（Sokuji）、TTS**：超出「字幕」的產品範圍。
- **OCR（Translumo、LunaTranslator）**：路線不同，可作為未來的獨立模式，但目前不列入。
- **以 Windows 即時輔助字幕當 ASR 來源**（LiveCaptions-Translator、Kitori 都有）：只限 Windows 11，靠 UI Automation 讀別的程式視窗、容易壞；kikitori 已有三平台自有 ASR。若要支援低階 Windows 機器可再評估。
- **雲端 ASR／翻譯預設**：與 kikitori 的本機優先、隱私定位相反；使用者已可把 `base_url` 指到任何 OpenAI 相容服務。

## 5. 其他觀察

- **名稱撞名**：Kitori（2026-10-20 建立，GPL-3.0）同樣以「聞き取り」命名、同樣鎖定日語學習者。目前只有 3 stars，但若 kikitori 要公開發布，建議確認名稱與搜尋可見度（非法律意見）。
- **授權**：可借概念的專案中，LocalVocal（GPL-2.0）、Sokuji／LiveTL（AGPL-3.0）、LunaTranslator／Kitori／abb128 LiveCaptions（GPL-3.0）不能搬程式碼；Speech Note（MPL-2.0）是檔案層級 copyleft。MIT／Apache-2.0／BSD 的（whisper_streaming、SimulStreaming、WhisperLive、WhisperLiveKit、RealtimeSTT、sherpa-onnx、LiveCaptions-Translator、Buzz、Vibe、WhisperX）可參考實作但仍需保留授權聲明。
- **延遲數字很少有可比性**：只有 whisper_streaming（論文 3.3 s，英文長篇）、StreamLingo（P95 2.4–3.2 s 處理延遲，單一 RTX 3080）、Voxtral Realtime（可設定 80 ms–2.4 s）公布數字，而且條件都不同。kikitori 若做 #4 並以既有 `experiments/` 測出 P50／P95，可在 README 公開，會是少數有量測的專案。

## 6. 資料缺口

- Sokuji、Kitori、TMSpeech 的 VAD 與幻覺處理細節，README 沒寫，未讀原始碼。
- LocalVocal 部分結果的穩定策略、Sokuji 字幕模式的 commit 規則，未讀原始碼。
- obs-websocket `SendStreamCaption`（CEA-608）對中日文的實際顯示效果未查證。
- Tauri global-shortcut 外掛是否支援 XDG portal 未查證。
- ChickenRice 日→中模型的評測數字只有作者說明（5000 小時訓練），沒有找到獨立評測；`TransWithAI/whisper-ja-1.5B-ct2` 在 HF 未標授權。
- Kotoba 官方沒有即時 demo；第三方 kotoba 即時 demo 未列入（非官方）。
- 各專案的資源用量只有 TMSpeech 作者自述（5800U CPU < 5%），沒有可比較的公開量測。

## 來源

- kikitori：`README.md`、`docs/architecture.md`、`docs/specs/app-architecture.md`、`docs/specs/pipeline-engine.md`、`docs/specs/ui.md`、`docs/specs/translate.md`、`docs/specs/asr.md`、`docs/specs/languages-i18n.md`
- LiveCaptions-Translator：[README](https://github.com/SakiRinn/LiveCaptions-Translator)、[`src/Translator.cs`](https://github.com/SakiRinn/LiveCaptions-Translator/blob/master/src/Translator.cs)、`src/models/TranslationTaskQueue.cs`、`src/models/Setting.cs`
- stream-translator-gpt：[README](https://github.com/ionic-bond/stream-translator-gpt)、[PyPI](https://pypi.org/project/stream-translator-gpt/)
- Sokuji：[README](https://github.com/kizuna-ai-lab/sokuji)
- StreamLingo：[README](https://github.com/michael-chow-arch/StreamLingo)
- Kitori：[README](https://github.com/ZeshiXing/Kitori)
- TMSpeech：[README](https://github.com/jxlpzqc/TMSpeech)
- abb128/LiveCaptions：[README](https://github.com/abb128/LiveCaptions)
- Buzz：[README](https://github.com/chidiwilliams/buzz)、[Live Recording](https://github.com/chidiwilliams/buzz/blob/main/docs/docs/usage/2_live_recording.md)、[Translations](https://github.com/chidiwilliams/buzz/blob/main/docs/docs/usage/3_translations.md)
- Vibe：[README](https://github.com/thewh1teagle/vibe)
- Speech Note：[README](https://github.com/mkiol/dsnote)、[GitLab](https://gitlab.com/mkiol/dsnote)
- OBS LocalVocal：[README](https://github.com/royshil/obs-localvocal)
- obs-websocket：[protocol.md `SendStreamCaption`](https://github.com/obsproject/obs-websocket/blob/master/docs/generated/protocol.md)
- whisper_streaming：[README](https://github.com/ufal/whisper_streaming)、[論文](https://aclanthology.org/2023.ijcnlp-demo.3.pdf)
- SimulStreaming：[README](https://github.com/ufal/SimulStreaming)、[IWSLT 2025 論文](https://aclanthology.org/2025.iwslt-1.41/)
- WhisperLiveKit：[README](https://github.com/QuentinFuxa/WhisperLiveKit)、[docs/API.md](https://github.com/QuentinFuxa/WhisperLiveKit/blob/main/docs/API.md)、`whisperlivekit/parse_args.py`
- WhisperLive：[README](https://github.com/collabora/WhisperLive)、[`whisper_live/backend/base.py`](https://github.com/collabora/WhisperLive/blob/main/whisper_live/backend/base.py)、`whisper_live/backend/translation_backend.py`
- RealtimeSTT：[README](https://github.com/KoljaB/RealtimeSTT)、[docs/configuration.md](https://github.com/KoljaB/RealtimeSTT/blob/master/docs/configuration.md)、[docs/realtime-punctuation-splitting.md](https://github.com/KoljaB/RealtimeSTT/blob/master/docs/realtime-punctuation-splitting.md)
- whisper.cpp：[examples/stream/README.md](https://github.com/ggml-org/whisper.cpp/blob/master/examples/stream/README.md)
- sherpa-onnx：[README](https://github.com/k2-fsa/sherpa-onnx)
- Moonshine：[README](https://github.com/moonshine-ai/moonshine)
- Kyutai：[delayed-streams-modeling](https://github.com/kyutai-labs/delayed-streams-modeling)、[hibiki](https://github.com/kyutai-labs/hibiki)
- Voxtral Realtime：[model card](https://huggingface.co/mistralai/Voxtral-Mini-4B-Realtime-2602)
- HY-MT1.5：[model card](https://huggingface.co/tencent/HY-MT1.5-7B)（術語干預範本）
- WhisperX：[README](https://github.com/m-bain/whisperX)
- TransWithAI ChickenRice：[README](https://github.com/TransWithAI/Faster-Whisper-TransWithAI-ChickenRice)、[chickenrice 模型](https://huggingface.co/chickenrice0721/whisper-large-v2-translate-zh-v0.2-st-ct2)、[ASMR VAD](https://huggingface.co/TransWithAI/Whisper-Vad-EncDec-ASMR-onnx)
- LiveTL：[README](https://github.com/LiveTL/LiveTL)、`apps/LiveTL/src/js/filter.js`
- LunaTranslator：[README](https://github.com/HIllya51/LunaTranslator)、[docs/zh/transoptimi.md](https://github.com/HIllya51/LunaTranslator/blob/main/docs/zh/transoptimi.md)、[docs/zh/apiservice.md](https://github.com/HIllya51/LunaTranslator/blob/main/docs/zh/apiservice.md)
- Translumo：[README](https://github.com/ramjke/Translumo)；ScreenTranslator：[README](https://github.com/OneMoreGres/ScreenTranslator)
- RTranslator：[README](https://github.com/niedev/RTranslator)
- Kotoba：[kotoba-tech/kotoba-whisper](https://github.com/kotoba-tech/kotoba-whisper)
