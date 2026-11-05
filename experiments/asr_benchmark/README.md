# ASR 基準測試：CTranslate2 Whisper 在本機的速度與準確度

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05；ctranslate2 4.8.5、Python 3.12。
> 結果用於 `docs/specs/asr.md` 的模型清單與硬體等級推薦表。

## 怎麼重跑

```text
uv run python -I experiments/asr_benchmark/menu.py
```

選單：

1. **下載模型**：下載到使用者快取目錄（Windows `%LOCALAPPDATA%\kikitori\models`），不進 repo。
2. **執行基準測試**：選模型、裝置（CPU int8／CUDA float16／CUDA int8_float16）、模式（即時 greedy／完整 beam 5）、CPU 執行緒數。每個設定在獨立子行程執行（峰值記憶體只反映該模型）。CUDA 需要 cuBLAS 12 的資料夾（見下方）。詳細結果（含每段辨識文字）寫在 `results/`（已被 git 忽略，因為含版權音訊的台詞）。
3. **比對分詞器**：以隔離環境安裝 HF `tokenizers==0.23.3`（不加入專案依賴），把 `kikitori.asr.tokenizer` 與官方實作逐一比對。
4. **彙整所有結果**：讀取 `results/` 全部結果，同一設定取最新一次，並用目前的 `rejection_reason()` 重新判定過濾欄位（調整門檻後不必重跑模型）。

## 環境

- CPU：AMD Ryzen 7 5700X（8C/16T），RAM 64 GB；GPU：NVIDIA RTX 5070 12 GB（Blackwell，compute capability 12.0），驅動 621.01；Windows 11。
- cuBLAS：`nvidia-cublas-cu12==12.8.4.1` wheel 內的 `cublas64_12.dll`、`cublasLt64_12.dll`（只解開到暫存資料夾，未安裝進專案）。
- 素材：`experiments/pipecat_vad/data/`（本機、git 忽略）。動漫 6 段（有參考文字，可算 CER）＋ VTuber 6 段（沒有參考文字，只算速度）＋ 非語音 2 段（合成噪音音軌 6 s、數位靜音 2 s）。語音片段 1.8–7.0 s，共約 54 s。
- 量測方式：
  - **RTF** = 語音片段辨識總耗時 ÷ 總長度（越小越快；< 1 才跟得上即時）。
  - **延遲**：單段 `transcribe()` 的牆鐘時間（模型已暖機）。
  - **CER**：動漫 6 段，NFKC、去標點與空白後的字元錯誤率。參考文字把人名寫成片假名（「アイオイキンセー」「トーサン」），模型常寫成漢字，所以 CER 偏高、只適合**相對比較**。
  - **ja 偵測率**：不指定語言時偵測為日文的比例（12 段語音）。
  - **非語音被濾掉**：2 段非語音經 `rejection_reason()` 被判定為幻覺的數量；**語音被誤濾**：12 段語音中被誤判的數量。
  - **峰值 RSS**：子行程的 PeakWorkingSetSize（含 Python、numpy 約 100 MB 基底）。
  - **VRAM 增量**：載入＋暖機前後的整卡用量差（Windows WDDM 下 nvidia-smi 不提供各行程用量；同時有其他程式使用 GPU 時會有誤差）。

## 結果（2026-11-05）

### 分詞器

`kikitori.asr.tokenizer` 與 HF `tokenizers` 0.23.3 比對 514 個字串（固定樣本 + 500 個隨機混合日文、英文、空白、標點、emoji）：tiny～medium 共用的分詞器、large-v3、large-v3-turbo、kotoba-whisper-v2.0 的 `tokenizer.json` **全部 0 不一致**（編碼 id 與解碼文字都相同）。log-mel 特徵與 faster-whisper 1.2.1 的 `FeatureExtractor` 在 80／128 mel、5 種長度下最大差異為 0。

### 下載

用 `kikitori.asr.download_model()` 實際下載 6 個 registry 模型與 kotoba 對照模型（共約 8.5 GB），全部通過大小與 SHA-256 驗證；約 8–11 MB/s。

### 速度、準確度、記憶體

CPU 一律 int8、8 執行緒（`cpu_threads=8`）；「fast」= `FAST_DECODE`（greedy），「final」= `FINAL_DECODE`（beam 5、溫度 fallback）。語言固定 `ja`；「ja 偵測率」另以不指定語言再跑一次統計。過濾欄位以最終版 `FilterConfig()` 重新判定。

| 設定 | 載入 s | RTF | 延遲中位 ms | 延遲最大 ms | CER（動漫） | ja 偵測率 | 非語音被濾掉 | 語音被誤濾 | 峰值 RSS MB | VRAM 增量 MB（載入後） |
|---|---|---|---|---|---|---|---|---|---|---|
| tiny cpu int8 t8 fast | 0.3 | 0.063 | 226 | 625 | 0.536 | 1.00 | 2/2 | 1 | 233 | — |
| tiny cpu int8 t8 final | 0.3 | 0.056 | 235 | 267 | 0.417 | 1.00 | 2/2 | 0 | 233 | — |
| tiny cuda float16 fast | 0.9 | 0.011 | 46 | 65 | 0.440 | 1.00 | 2/2 | 0 | 691 | -3784 |
| tiny cuda float16 final | 0.9 | 0.016 | 58 | 133 | 0.345 | 1.00 | 2/2 | 0 | 715 | 424 |
| tiny cuda int8_float16 fast | 1.2 | 0.021 | 80 | 170 | 0.452 | 1.00 | 2/2 | 0 | 707 | 509 |
| tiny cuda int8_float16 final | 1.2 | 0.034 | 161 | 233 | 0.369 | 1.00 | 2/2 | 0 | 708 | 253 |
| base cpu int8 t8 fast | 0.5 | 0.116 | 484 | 550 | 0.429 | 1.00 | 2/2 | 0 | 330 | — |
| base cpu int8 t8 final | 0.4 | 0.110 | 466 | 517 | 0.262 | 1.00 | 2/2 | 0 | 348 | — |
| base cuda float16 fast | 1.1 | 0.016 | 72 | 104 | 0.417 | 1.00 | 2/2 | 0 | 790 | 100 |
| base cuda float16 final | 1.0 | 0.019 | 77 | 115 | 0.274 | 1.00 | 2/2 | 0 | 841 | 315 |
| base cuda int8_float16 fast | 1.1 | 0.019 | 69 | 126 | 0.417 | 1.00 | 2/2 | 0 | 700 | 283 |
| base cuda int8_float16 final | 1.4 | 0.046 | 185 | 300 | 0.321 | 1.00 | 2/2 | 0 | 702 | 283 |
| small cpu int8 t8 fast | 1.2 | 0.340 | 1419 | 1579 | 0.393 | 1.00 | 2/2 | 0 | 706 | — |
| small cpu int8 t8 final | 1.0 | 0.318 | 1305 | 1609 | 0.357 | 1.00 | 2/2 | 0 | 950 | — |
| small cuda float16 fast | 1.6 | 0.031 | 130 | 210 | 0.393 | 1.00 | 2/2 | 0 | 818 | 216 |
| small cuda float16 final | 1.4 | 0.031 | 125 | 191 | 0.310 | 1.00 | 2/2 | 0 | 818 | 668 |
| small cuda int8_float16 fast | 1.8 | 0.044 | 189 | 251 | 0.393 | 1.00 | 2/2 | 0 | 966 | 580 |
| small cuda int8_float16 final | 1.7 | 0.046 | 180 | 243 | 0.357 | 1.00 | 2/2 | 0 | 966 | 475 |
| medium cpu int8 t8 fast | 3.0 | 0.886 | 3559 | 4185 | 0.286 | 1.00 | 2/2 | 0 | 1754 | — |
| medium cpu int8 t8 final | 2.7 | 0.911 | 3616 | 4196 | 0.214 | 1.00 | 2/2 | 0 | 1754 | — |
| medium cuda float16 fast | 2.5 | 0.058 | 230 | 410 | 0.286 | 1.00 | 2/2 | 0 | 1814 | 2139 |
| medium cuda float16 final | 2.5 | 0.067 | 258 | 508 | 0.202 | 1.00 | 2/2 | 0 | 1815 | 2145 |
| medium cuda int8_float16 fast | 3.4 | 0.069 | 268 | 491 | 0.274 | 1.00 | 2/2 | 0 | 2013 | 1184 |
| medium cuda int8_float16 final | 3.3 | 0.079 | 322 | 546 | 0.202 | 1.00 | 2/2 | 0 | 2013 | 1180 |
| large-v3-turbo cpu int8 t8 fast | 3.0 | 1.241 | 5125 | 5375 | 0.321 | 1.00 | 0/2 | 0 | 1890 | — |
| large-v3-turbo cpu int8 t8 final | 3.0 | 1.247 | 5185 | 5302 | 0.202 | 1.00 | 0/2 | 0 | 1890 | — |
| large-v3-turbo cuda float16 fast | 2.4 | 0.035 | 145 | 180 | 0.238 | 1.00 | 0/2 | 0 | 1899 | 2299 |
| large-v3-turbo cuda float16 final | 2.4 | 0.036 | 151 | 171 | 0.190 | 1.00 | 0/2 | 0 | 1899 | 2299 |
| large-v3-turbo cuda int8_float16 fast | 3.5 | 0.038 | 155 | 192 | 0.238 | 1.00 | 0/2 | 0 | 2150 | 1242 |
| large-v3-turbo cuda int8_float16 final | 3.5 | 0.039 | 169 | 216 | 0.202 | 1.00 | 0/2 | 0 | 2150 | 1243 |
| large-v3 cpu int8 t8 fast | 5.3 | 1.513 | 6171 | 7092 | 0.286 | 1.00 | 2/2 | 0 | 3282 | — |
| large-v3 cpu int8 t8 final | 5.1 | 1.568 | 6445 | 7272 | 0.238 | 1.00 | 2/2 | 0 | 3282 | — |
| large-v3 cuda float16 fast | 4.0 | 0.069 | 271 | 426 | 0.274 | 1.00 | 2/2 | 0 | 3301 | 4059 |
| large-v3 cuda float16 final | 3.9 | 0.080 | 332 | 502 | 0.238 | 1.00 | 2/2 | 0 | 3302 | 4169 |
| large-v3 cuda int8_float16 fast | 6.0 | 0.098 | 405 | 626 | 0.286 | 1.00 | 2/2 | 0 | 3542 | 2139 |
| large-v3 cuda int8_float16 final | 6.0 | 0.110 | 422 | 659 | 0.238 | 1.00 | 2/2 | 0 | 3542 | 2139 |
| kotoba-whisper-v2.0 cpu int8 t8 fast | 2.5 | 1.184 | 4877 | 5007 | 0.179 | 1.00 | 1/2 | 0 | 1786 | — |
| kotoba-whisper-v2.0 cpu int8 t8 final | 2.5 | 1.191 | 4907 | 5021 | 0.179 | 1.00 | 0/2 | 0 | 1786 | — |
| kotoba-whisper-v2.0 cuda float16 fast | 2.3 | 0.031 | 128 | 156 | 0.202 | 1.00 | 1/2 | 0 | 1799 | 2170 |
| kotoba-whisper-v2.0 cuda float16 final | 2.3 | 0.032 | 130 | 161 | 0.202 | 1.00 | 0/2 | 0 | 1799 | 2171 |
| kotoba-whisper-v2.0 cuda int8_float16 fast | 3.3 | 0.031 | 123 | 155 | 0.202 | 1.00 | 1/2 | 0 | 2045 | 1179 |
| kotoba-whisper-v2.0 cuda int8_float16 final | 3.4 | 0.034 | 140 | 170 | 0.202 | 1.00 | 0/2 | 0 | 2045 | 1181 |

> 這批 fast 結果是在 `FAST_DECODE` 加上 `max_new_tokens=128` 之前量的；唯一受影響的是 tiny fast 在 vtuber_000 陷入「と、と、と…」迴圈（延遲最大 625 ms，被判為 repetitive 而算作「語音被誤濾」），加上上限後這類情況的延遲會被截斷。
> tiny／base／small 的 VRAM 欄位受同時執行的 Ollama 干擾（出現負值），不可採信。

**CPU 預設執行緒（`cpu_threads=0`）對照**：tiny 221 ms、base 440 ms、small 1548 ms（8 執行緒分別為 226／484／1419 ms）。小模型差異在誤差內，small 以上 8 執行緒約快 8%。

### 觀察

1. **延遲幾乎與句長無關**：Whisper encoder 固定處理 30 s 視窗（不足補零），每次呼叫的成本大致固定。CPU int8：tiny ≈ 0.23 s、base ≈ 0.45 s、small ≈ 1.3–1.5 s、medium ≈ 3.6 s、large-v3-turbo ≈ 5.1 s、large-v3 ≈ 6.2–6.4 s。
2. **即時路徑（每 700 ms 一次）在 CPU 上只有 tiny、base 跟得上**；small 以上每次都超過 700 ms，pipeline 會一直跳過舊請求。
3. **large-v3-turbo 在 CPU 比 medium 還慢**（encoder 與 large-v3 相同，32 層），只適合 GPU。
4. **CUDA：float16 在每個模型都比 int8_float16 快**（RTX 5070），int8_float16 只省約一半 VRAM。所以 CUDA 預設 float16。
5. **品質**（看 12 段辨識文字）：large-v3-turbo ≈ large-v3 > medium > small > base > tiny。例如 vtuber_003 只有 turbo／large-v3 聽出「シンジ君なら大丈夫」，vtuber_002 small 以下把「初号機」聽錯。動漫 CER 受片假名人名影響（參考文字「キンセー」、模型多寫「金星」），樣本只有 6 段，只能當相對參考。
6. **large-v3-turbo 的 `no_speech_probability` 在靜音與噪音上也是 0.00**（其他模型 0.5–0.9），Whisper 的靜音規則對它無效；不指定語言時噪音輸出「you」「Thank you.」而通過過濾。指定 `ja` 或候選語言 `("zh", "ja")` 時，turbo 在三種非語音（合成噪音、數位靜音、−30 dB 白噪音）上都輸出「ご視聴ありがとうございました」，被已知幻覺清單擋下。kotoba-whisper-v2.0 同樣偏低（0.11）。
7. **large-v3 在數位靜音上輸出挪威語「Teksting av Nicolai Winther」**（no_speech 0.86），已加入幻覺清單；硬門檻由 0.95 調為 0.8（語音片段 no_speech 最高 0.43，非語音最低 0.51）。
8. 所有模型的 ja 偵測率都是 12/12；非語音的偵測結果多為 `en`／`nn`，語言機率 0.25–0.86，不能當過濾依據。
9. **同一行程內 CUDA 初始化失敗後再試會卡住**：沒有 cuBLAS 時 `device="auto"` 正確退回 CPU，但同一行程之後再以 CUDA 載入時暖機不返回。`load_transcriber()` 因此記住失敗、之後一律用 CPU（明確 `device="cuda"` 時丟錯要求重啟引擎）。
10. kotoba-whisper-v2.0（只支援日文）CER 最低（0.18–0.20），速度與 turbo 相近，但 no_speech 不可靠，且訓練資料授權待確認，暫不列入 registry。

### 沒有量到的

- 英文、中文語音（本機沒有授權明確的素材）；只確認 12 段日文。
- 即時與完整兩個模型同時執行時的互相干擾，以及與翻譯 LLM（Ollama）同時佔用 GPU 時的延遲。
- tiny／base／small 的 VRAM（見上方註記）；registry 改用「權重大小 + 約 0.5 GB CUDA context」估計。
- macOS（Apple Silicon、Accelerate）與 Linux 實機。
