# Spec: asr（語音辨識模組）

> 狀態：已採用（2026-11-05，使用者授權自主實作，不等待審核）。
> 上位文件：`docs/specs/app-architecture.md` 的 `asr` 一節；本文件不得與之矛盾。
> 依據：`reports/VAD 與句尾判斷技術調查.md`、`research_notes/VAD 與句尾判斷技術調查/media_sentence_segmentation.md`、`experiments/asr_benchmark/README.md`（本機實測）。

## 前提假設

1. 輸入是 VAD 切好的一句（一般 ≤ 10 s），格式為一維 int16、16 kHz、單聲道 numpy 陣列；超過 30 s 的音訊也要能處理（逐 30 s 視窗辨識後串接）。
2. 辨識結果只需要整句文字，不需要時間戳記或逐字時間（字幕的位置由 VAD 的樣本計數決定）。
3. 即時路徑與完整路徑由 pipeline 分別建立兩個 `Transcriber`（可用不同模型）；`asr` 本身不知道「即時」或「完整」。
4. 硬體偵測與「該用哪個模型」由 `hardware` 模組決定；本模組只提供模型清單、下載與推薦表的依據數據。
5. 不依賴 PyTorch；所有依賴與模型權重都要是寬鬆授權（MIT／BSD／Apache／ISC）。

## 決策：直接使用 CTranslate2，不使用 faster-whisper 套件

**引擎**：[CTranslate2](https://github.com/OpenNMT/CTranslate2) 4.8.5（MIT）的 `ctranslate2.models.Whisper`，搭配 CTranslate2 格式的 Whisper 權重（MIT）。
log-mel 特徵、解碼參數與 avg_logprob 計算移植自 [faster-whisper](https://github.com/SYSTRAN/faster-whisper) v1.2.1（MIT，保留出處註明）；分詞器、模型下載自行實作。

**為什麼不直接用 faster-whisper（原本的首選）**：

| 問題 | 證據 |
|---|---|
| `faster-whisper` 硬依賴 `av>=11`（PyAV），且 `faster_whisper/__init__.py` 匯入時就載入 `av` | PyPI `requires_dist`；原始碼 `faster_whisper/audio.py` |
| PyAV 19.0.2 的 wheel 內含 **`libx264-165.dll`、`libx265.dll`（GPL-2.0+）**，FFmpeg 以 `--enable-libx264 --enable-libx265` 建置。雖然 FFmpeg 自報 "LGPL version 3 or later"，實際一起散布的是 GPL 函式庫 | 實際解開 `av-19.0.2-cp312-abi3-win_amd64.whl`；`av._core.library_meta`；[pyav-ffmpeg `scripts/build-ffmpeg.py`](https://github.com/PyAV-Org/pyav-ffmpeg/blob/main/scripts/build-ffmpeg.py) |
| `faster-whisper` 與 `tokenizers` 都依賴 `huggingface-hub`，後者帶入 `tqdm`（**MPL-2.0 AND MIT**，不在允許清單） | PyPI metadata；[tqdm LICENCE](https://github.com/tqdm/tqdm/blob/master/LICENCE) |
| 我們不需要它的音訊解碼（capture 已給 16 kHz int16）、也不需要時間戳記與長音訊切段 | 本規格前提 1、2 |

改用 CTranslate2 後，新增的執行期依賴只有 `ctranslate2==4.8.5`（它只依賴 `numpy` 與已在 lock 中的 `pyyaml`）。

### 依賴與授權

| 項目 | 版本 | 授權 | 備註 |
|---|---|---|---|
| `ctranslate2` | 4.8.5 | MIT | 見下方「wheel 內附的二進位元件」 |
| `pyyaml`（ctranslate2 的依賴） | 6.0.3 | MIT | 已在 lock 中 |
| `numpy` | 2.5.4 | BSD-3-Clause | 已是專案依賴 |
| Whisper 權重（tiny／base／small／medium／large-v3／large-v3-turbo） | — | MIT | [openai/whisper README](https://github.com/openai/whisper#license)：「code and model weights are released under the MIT License」 |
| CTranslate2 轉換版（Systran、dropbox-dash） | 見模型清單 | MIT | 各 HF repo 的 `license: mit` |
| faster-whisper 移植的程式片段 | v1.2.1 | MIT | 在 `features.py`、`whisper.py` 註明出處 |

**wheel 內附的二進位元件（需使用者知悉）**：

| 平台 wheel | 內附 | 授權 |
|---|---|---|
| Windows x86-64 | `ctranslate2.dll` 靜態連結 Intel oneMKL 與 oneDNN；`libiomp5md.dll`（Intel OpenMP）；`cudnn64_9.dll`（cuDNN 載入器，Whisper 用不到） | oneMKL／Intel OpenMP：Intel Simplified Software License（可再散布、無 copyleft）；oneDNN：Apache-2.0；cuDNN：NVIDIA cuDNN SLA（可再散布） |
| Linux x86-64 | 靜態連結 oneMKL、oneDNN；`libgomp`（GCC runtime，GPL-3.0 + Runtime Library Exception） | 同上；libgomp 的例外條款允許隨非 GPL 程式散布 |
| macOS arm64 | 只有 `libctranslate2.dylib`（Apple Accelerate + Ruy） | 系統框架／Apache-2.0 |

這些是隨 MIT 套件一起散布的「可再散布但非 OSI」二進位元件，與 `onnxruntime` 等常見套件同類；本模組照常使用，並在最終報告中請使用者確認。

### CUDA（NVIDIA GPU）

- CTranslate2 4.8.5 的 Windows／Linux wheel 以 **CUDA 12.8** 建置（`CUDA_ARCH_LIST="Common"`、`CUDA_DYNAMIC_LOADING=ON`、`WITH_CUDNN=OFF`），執行時才動態載入 cuBLAS。
- **需要額外的函式庫只有 cuBLAS 12**：Windows `cublas64_12.dll`、`cublasLt64_12.dll`；Linux `libcublas.so.12`、`libcublasLt.so.12`。Whisper 不需要 cuDNN。
- 本機實測（RTX 5070，Blackwell，compute capability 12.0，驅動 621.01）：只把 `nvidia-cublas-cu12==12.8.4.1` wheel 內的兩個 DLL 加進 `PATH`，`float16`、`int8_float16`、`int8` 都能推論；沒有 cuBLAS 時錯誤為 `RuntimeError: Library cublas64_12.dll is not found or cannot be loaded`。
- cuBLAS 屬 NVIDIA 專有授權（CUDA EULA 附件 A 允許隨應用程式再散布），**不列為專案依賴**。如何取得（使用者自行安裝 CUDA Toolkit 12.x、或 app 經使用者同意後下載 `nvidia-cublas-cu12` wheel 取出 DLL）留給 `hardware` 里程碑決定；本模組提供 `cuda_library_dirs` 參數讓呼叫端把 DLL 所在目錄加進搜尋路徑。
- `device="auto"` 時：有 CUDA 裝置就先試 CUDA 並做一次暖機推論（encode + 一步 greedy）；載入 cuBLAS 失敗就記錄 warning 並退回 CPU。明確指定 `device="cuda"` 時失敗直接丟錯。
- **同一行程內 CUDA 初始化失敗後不再嘗試**：本機實測失敗後再以 CUDA 載入時暖機會卡住不返回，所以 `load_transcriber()` 記住第一次失敗，之後 `auto` 一律用 CPU、`cuda` 丟 `RuntimeError` 要求重啟引擎。`cuda_library_dirs` 找不到 cuBLAS（還沒碰到 CUDA）不算失敗。
- **CUDA 預設精度 `float16`**：RTX 5070 上 float16 在每個模型都比 int8_float16 快（例如 large-v3 271 vs 405 ms），int8_float16 只省約一半 VRAM；VRAM 不足時由 `hardware` 改指定 `int8_float16`。CPU 預設 `int8`。
- macOS 沒有 CUDA／Metal 後端，一律走 CPU（Apple Silicon 用 Accelerate）。

### 替代方案比較

| 方案 | 授權 | 不靠 PyTorch | GPU | 結論 |
|---|---|---|---|---|
| faster-whisper 1.2.1 | MIT，但依賴 `av`（wheel 內含 GPL 的 x264／x265）與 `tqdm`（MPL-2.0） | 是 | CUDA（需 cuBLAS） | **不採用**：授權不符 |
| **CTranslate2 4.8.5 直接呼叫** | MIT（wheel 附 Intel／NVIDIA 可再散布元件） | 是 | CUDA（需 cuBLAS）；本機 RTX 5070 實測可用 | **採用**：與 faster-whisper 同引擎、同權重、同速度 |
| sherpa-onnx 2.0.1 + Whisper ONNX | Apache-2.0；Whisper ONNX 權重 MIT | 是 | PyPI wheel 為 CPU 版，CUDA 需另裝特製 wheel | 備案：Whisper 解碼只有 greedy、長句較慢；SenseVoice 等模型權重授權不明 |
| whisper.cpp（pywhispercpp 1.5.1） | MIT | 是 | PyPI wheel 只有 CPU；CUDA／Metal 需自行編譯 | 備案：若之後要 macOS Metal 加速再評估 |

## 對外介面（`src/kikitori/asr/`）

```python
# types.py（與 app-architecture.md 完全一致）
@dataclass(frozen=True)
class Transcript:
    text: str
    language: str              # ISO 639-1，例如 "ja"
    language_probability: float
    no_speech_probability: float
    avg_logprob: float

class Transcriber(Protocol):
    def transcribe(self, audio: np.ndarray, language: str | None = None,
                   prompt: str | None = None) -> Transcript: ...
```

| 檔案 | 角色 | 主要名稱 |
|---|---|---|
| `types.py` | 跨模組介面 | `Transcript`、`Transcriber` |
| `audio.py` | 輸入驗證 | `validate_audio()`、`SAMPLE_RATE` |
| `features.py` | Whisper log-mel 特徵（numpy） | `log_mel_spectrogram()` |
| `tokenizer.py` | Whisper byte-level BPE（讀 `tokenizer.json`） | `WhisperTokenizer` |
| `registry.py` | 可選模型清單（固定 revision 與 SHA-256、資源用量、`no_speech_reliable`） | `ModelSpec`、`MODELS`、`get_model()` |
| `cache.py` | 使用者快取目錄 | `default_cache_dir()` |
| `download.py` | 下載、續傳、驗證 | `download_model()`、`download_file()`（單檔，hardware 下載 cuBLAS wheel 用）、`is_downloaded()`、`model_dir()`、`ModelDownloadError` |
| `derived.py` | 預先量化的衍生快取（int8 權重另存一份，載入免量化） | `derived_key()`、`find_derived()`、`build_derived()`、`write_quantized_model_bin()`、`read_model_index()`、`DerivedCacheWorker`、`DERIVED_CACHE`、`ASR_ACTIVITY` |
| `whisper.py` | CTranslate2 推論、模型物件池 | `WhisperTranscriber`（含 `close()`）、`DecodeOptions`、`FAST_DECODE`、`FINAL_DECODE`、`load_transcriber()`（`derived_cache=True` 時用衍生快取）、`preload_cuda_libraries()`、`ModelPool`、`MODEL_POOL` |
| `filtering.py` | 幻覺過濾 | `FilterConfig`、`rejection_reason()` |
| `hallucination_lexicon.py` | 依語言的幻覺片語資料（`lexicon.json` 的 Python 版本） | `HALLUCINATION_LEXICON` |

### 行為規則

- **輸入驗證**：必須是 `np.ndarray`、`dtype == int16`、一維、長度 > 0；否則 `ValueError`（型別錯誤為 `TypeError`）。int16 ÷ 32768 轉 float32（同 faster-whisper `decode_audio`）。
- **language**：`None` 時自動偵測；否則必須是 Whisper 支援的 ISO 639-1 代碼（例如 `"ja"`、`"en"`、`"zh"`），不支援就 `ValueError`。指定語言時 `language_probability = 1.0`（同 faster-whisper）。
- **候選語言**：`DecodeOptions.candidate_languages`（例如口譯模式 `("zh", "ja")`）把偵測限制在候選中，機率在候選間重新正規化。偵測只看第一個 30 s 視窗。
- **固定語言**：`DecodeOptions.language`（影片翻譯指定收聽語言時由 factory 設定）讓每次 `transcribe()` 都用這個語言、不做偵測；呼叫端另外傳 `language` 時以呼叫端為準。建構時檢查模型支援，不支援就 `ValueError`。
- **prompt**：前文提示（前幾句原文）。編碼 `" " + prompt.strip()`，只保留最後 223 個 token（`448 // 2 - 1`），放在 `<|startofprev|>` 之後；空白字串視為沒有。
- **解碼**：`<|startoftranscript|><|lang|><|transcribe|><|notimestamps|>`；預設 beam 5、`suppress_blank`、`suppress_tokens=[-1]` 加上 task／sot 類特殊 token（同 faster-whisper `get_suppressed_tokens`）。溫度 fallback 依 `DecodeOptions.temperatures`（預設只有 0.0，避免即時路徑耗時倍增）；觸發條件同 faster-whisper：壓縮比 > 2.4 或 avg_logprob < −1.0，但「no_speech > 0.6 且 avg_logprob < −1.0」視為靜音不 fallback。
- **預設組合**：`FAST_DECODE`（greedy、`max_new_tokens=128`，避免 greedy 陷入重複迴圈時延遲暴增）與 `FINAL_DECODE`（beam 5、溫度 0.0／0.2／0.4）。
- **延遲特性**：encoder 固定處理 30 s 視窗，每次呼叫的成本幾乎與句長無關（見基準測試）。
- **avg_logprob**：`score × len^length_penalty / (len + 1)`（同 faster-whisper）。
- **長於 30 s**：特徵切成 3000 frame 視窗，各自辨識後串接；ja／zh 直接相連，其他語言以空白相連。`avg_logprob` 依 token 數加權平均，`no_speech_probability` 取各視窗最小值（任一視窗有語音就算有語音）。
- **執行緒**：同一個 `WhisperTranscriber` 的呼叫以 lock 串行化；即時與完整路徑各用一個實例才能並行。
- **關閉與模型物件池**（2026-11-06，memory_soak #2）：見下方「模型物件池」。
- **錯誤**：模型推論錯誤原樣往上拋（不吞）；不支援的 device／compute_type 由 CTranslate2 丟出 `ValueError`。

## 模型清單（registry）

所有模型都固定 Hugging Face commit，下載後逐檔驗證大小與 SHA-256。數值來自 `experiments/asr_benchmark/README.md`（Ryzen 7 5700X、RTX 5070）：CPU RAM 為 CPU int8 時整個行程的峰值（含 Python 約 0.2 GB）；VRAM 為載入＋暖機後的整卡用量增量（含 CUDA context 約 0.5 GB；tiny／base／small 量測受 Ollama 干擾，為估計值）；延遲為一句日文（1.8–7 s）的中位數，模型已暖機。

| id | HF repo @ revision | 檔案總量 | mel | CPU RAM | VRAM fp16／int8_fp16 | CPU int8 延遲（final） | CUDA fp16 延遲（final） | 速度等級 | no_speech 可用 | 授權 |
|---|---|---|---|---|---|---|---|---|---|---|
| `tiny` | `Systran/faster-whisper-tiny` @ `d90ca5f` | 78 MB | 80 | 0.23 GB | ≈0.5／0.4 GB | 0.24 s | 0.06 s | fastest | 是 | MIT |
| `base` | `Systran/faster-whisper-base` @ `ebe41f7` | 148 MB | 80 | 0.35 GB | ≈0.6／0.45 GB | 0.47 s | 0.08 s | fast | 是 | MIT |
| `small` | `Systran/faster-whisper-small` @ `536b066` | 487 MB | 80 | 0.95 GB | ≈1.0／0.65 GB | 1.3 s | 0.13 s | medium | 是 | MIT |
| `medium` | `Systran/faster-whisper-medium` @ `08e178d` | 1.53 GB | 80 | 1.75 GB | 2.1／1.2 GB | 3.6 s | 0.26 s | slow | 是 | MIT |
| `large-v3-turbo` | `dropbox-dash/faster-whisper-large-v3-turbo` @ `0a363e9` | 1.62 GB | 128 | 1.9 GB | 2.3／1.2 GB | 5.2 s | 0.15 s | slow | **否**（實測恆為 0） | MIT |
| `large-v3` | `Systran/faster-whisper-large-v3` @ `edaa852` | 3.09 GB | 128 | 3.3 GB | 4.2／2.1 GB | 6.4 s | 0.33 s | slowest | 是 | MIT |

全部支援 Whisper 的 99 種語言（large-v3 系列 100 種，多 `yue`），包含 ja、en、zh。品質（12 段日文人工比對）：large-v3-turbo ≈ large-v3 > medium > small > base > tiny。

- `large-v3-turbo` 原本由 `mobiuslabsgmbh/faster-whisper-large-v3-turbo` 發布（faster-whisper 內建對照表仍指向它），該 repo 已轉址到 `dropbox-dash/`；`model.bin` 雜湊與 `deepdml/faster-whisper-large-v3-turbo-ct2` 相同。
- 未列入：`*.en` 系列（只有英文）、`distil-large-v3`（只有英文）、`kotoba-whisper-v2.0-faster`（只有日文；權重 Apache-2.0／轉換版 MIT，但訓練資料 ReazonSpeech 有使用條款，待使用者確認後再考慮；基準測試中當作參考對照）。

## 下載與快取

- **快取目錄**（`default_cache_dir()`）：
  - Windows：`%LOCALAPPDATA%\kikitori\models`（未設定時 `~\AppData\Local\kikitori\models`）
  - macOS：`~/Library/Caches/kikitori/models`
  - Linux 與其他：`$XDG_CACHE_HOME/kikitori/models`（未設定或不是絕對路徑時 `~/.cache/kikitori/models`，依 XDG 規範）
  - 環境變數值先驗證（非空、絕對路徑），不合格就用預設值。
- **目錄配置**：`<cache>/<model id>/<revision 前 12 碼>/`，換 revision 不會覆蓋舊檔；完成驗證後寫入 `kikitori-model.json`（id、repo、revision、各檔 SHA-256）作為完成標記。`is_downloaded()` 檢查標記內容與每個檔案大小（不重算雜湊，啟動快）。
- **下載**：`https://huggingface.co/<repo>/resolve/<revision>/<file>`，只允許 https；每檔先寫 `<file>.part`，邊下載邊算 SHA-256，大小與雜湊都相符才 `os.replace` 成正式檔名。
- **續傳**：`.part` 已存在時送 `Range: bytes=<n>-`；伺服器回 206 就接著寫，回 200 就從頭寫。
- **進度回報**：`progress(downloaded_bytes, total_bytes)`，以整個模型（所有檔案加總）計，對應引擎事件 `download_progress`；每 1 MiB 至少回報一次，結束時回報一次 `total == downloaded`。
- **timeout**：每次連線與每次讀取都有 socket timeout（預設 30 s）；逾時、HTTP 錯誤、大小或雜湊不符都丟 `ModelDownloadError`（保留原因），`.part` 留著供續傳（雜湊不符時刪除）。
- **取消**：可傳入 `threading.Event`，每個區塊之間檢查，被設定就丟 `DownloadCancelledError`。
- **本機路徑**：`load_transcriber(model_path=...)` 直接使用使用者指定的 CTranslate2 Whisper 目錄（必須有 `model.bin`、`config.json`、`tokenizer.json`），不下載、不驗雜湊。
- **可替換的 fetcher**：下載透過 `Fetcher` Protocol（預設用 `urllib`），測試注入假 fetcher 或本機 HTTP 伺服器，不連網。
- **磁碟用量與刪除**（F7，`models.list` 的 `disk_bytes`、`models.delete`）：`model_disk_bytes(spec, cache)` 回傳 `<cache>/<id>`
  底下普通檔案的大小合計（含其他 revision、`.part` 殘留與 `derived/` 底下的預先量化衍生檔，不跟隨連結），沒有下載完成（`is_downloaded()` 為 False）時 None。
  `delete_model(spec, cache)` 刪掉整個 `<cache>/<id>`，回傳釋放的位元組數；全部檢查通過才刪任何東西：id 是單一資料夾名稱
  （沒有分隔符號、`..`、磁碟代號）、`<cache>/<id>` 不是 symlink／junction、解析後就在解析後的快取資料夾正下方、整棵樹裡沒有任何
  連結（違反時 `UnsafeModelPathError`，`ValueError` 的子類別）。沒有下載完成時 `FileNotFoundError`（只有 `.part` 殘留時也是：留給
  之後的下載續傳）。先刪完成標記，中途失敗（`OSError`，例如檔案被其他程式開著）時模型不會被當成已下載。
  刪除每個檔案與資料夾之前再檢查它到 `<cache>/<id>` 的每一層都不是連結（掃描後才被換成連結的資料夾不跟隨，丟 `UnsafeModelPathError`，
  已刪的部分不復原）。剩下的限制：重新檢查與刪除之間仍有極短的空檔——Windows 的 Python 沒有以資料夾 handle 為基準的 unlink／rmdir；
  要利用它必須已經能寫入使用者自己的模型快取。

## 模型物件池（`whisper.py`）

**問題**（`experiments/memory_soak/README.md`「發現的問題」#2，2026-11-06，RTX 5070、CTranslate2 4.8.5）：每建立一個
`ctranslate2.models.Whisper` 並在 GPU 上推論過，`del` 之後仍留下約 8 MiB 顯示記憶體與 8–10 MiB 主記憶體，`unload_model()`
再 `del` 也一樣；引擎每個 session 建立 2 個模型，情境 1（start/stop × 12）每個 session +16.8 MiB 顯示記憶體／+18–21 MiB 私有記憶體，
與模型大小無關。隔離測試 `ct2_isolation.py`（各 12 輪，`small` float16）：

| 做法 | 顯示記憶體／輪 | 私有記憶體／輪 |
|---|---|---|
| `Whisper(...)` 後直接 `del`（不推論） | 0.0 | +0.02 MiB |
| `Whisper(...)` + 一次 encode + 一步 generate，再 `del` | +8.0 MiB | +7.8 MiB |
| 同上，`del` 前先 `unload_model()` | +8.0 MiB | +8.5 MiB |
| **只建立一次**，每輪 `load_model()` → 推論 → `unload_model()` | **0.0** | **−0.04 MiB** |

推測是第一次推論時在模型工作執行緒上建立的 CUDA 資源（cuBLAS handle／workspace、stream）沒有隨模型銷毀釋放；未直接驗證，屬上游行為。

**做法**：行程內的 `MODEL_POOL`（`ModelPool`，執行緒安全）保存**已卸載**的模型物件，鍵為
`ModelKey(模型目錄 realpath+normcase, device, device_index, compute_type, intra_threads, inter_threads)`——這些是建立後不能改的參數。

- `load_transcriber()` 先向池子取同鍵的閒置物件並 `load_model()`（權重載回原裝置）；沒有才建立新的。重用的物件 `load_model()` 失敗時
  記錄（`logger.exception`）並丟掉它，改建立新的物件；建立也失敗時例外照常往上（CUDA 路徑仍依既有規則記錄 CUDA 失敗、`auto` 改用 CPU）。
- 取得模型之後的失敗（CPU 與 CUDA 路徑相同，`_pooled_transcriber()`）：建構時的 `ValueError`（例如模型不支援 `forced_language`）→
  模型放回池中再往上拋（不算 CUDA 失敗）；CUDA 暖機失敗（`RuntimeError`／`OSError`）→ 歸還借用紀錄但**不**放回池中（物件可能處在壞掉的
  CUDA 狀態，交給參照計數），再依既有規則往上拋或改用 CPU。
- `with_options()` 的「未關閉」檢查與 `retain()` 在 `close()` 設定旗標的同一把鎖（`_close_lock`）內：不會在新實例還在用時把模型放回池中。
- `WhisperTranscriber.close(timeout=2.0)`：之後的 `transcribe()`／`warmup()`／`with_options()` 丟 `RuntimeError`；先等進行中的推論結束
  （不能在推論途中卸載），再 `unload_model()` 並放回池中。逾時（推論卡住）時記 warning、不放回，物件交給參照計數（漏一次 8 MiB）。可重複呼叫。
- 即時與完整共用一個物件（`workers=2` + `with_options()`）時以借用計數（`_ModelLease`）處理：兩個實例都關閉才歸還一次。
- 上限 `MAX_IDLE_MODELS = 10` 個閒置物件（每個約 8 MiB 顯示記憶體與一條 CTranslate2 工作執行緒），超過時丟掉最久沒用的（LRU）——那個組合
  之後再用到時建立新物件，代價與沒有池時相同。丟掉的物件在 CTranslate2 中本來就漏掉它佔的量，所以保留不比丟掉貴；原本的 4 在 v0.2.0 驗收的
  memory_soak 情境 2（4 組語言／模型輪流＝6 個鍵）造成每個 session 仍多約 11 MiB 顯示記憶體，2026-11-06 改為 10。卸載失敗的物件記錄後丟掉；`unload_model()` 返回後 `model_is_loaded` 仍為 True 的物件也丟掉
  （見下方 CTranslate2 語意：模型忙碌時它直接返回、不丟例外）。
- 誰呼叫 `close()`：引擎在 session 結束（停止、讀完、錯誤、載入中被停止、開裝置失敗）時以 `pipeline.types.release_components()` 關閉兩個
  transcriber；`factory.build_components()` 在任一模型載入失敗（或載入進度回報失敗）時關閉已載入的模型（見「載入時間」）。`models.delete` 先以 `MODEL_POOL.evict_under(模型資料夾)`
  丟掉該模型的閒置物件（之後 `load_model()` 會從那個資料夾讀檔）；引擎結束時 `MODEL_POOL.clear()`。
- 驗證：單元測試以假的 `ctranslate2` 模組計數建立／載入／卸載（`tests/test_asr_model_pool.py`）；實機回歸用 memory_soak 情境 1（`cycles=20`），
  預期顯示記憶體斜率 ≈ 0（尚未在修正後實測）。
- **CTranslate2 的 `unload_model()`／`load_model()` 語意**（4.8.5 原始碼 `python/cpp/replica_pool.h` 與官方 Python API 文件，2026-11-06 查證）：
  `unload_model(to_cpu=False)` 把模型從 replica pool 拆下並丟掉 CPU 快取，CUDA 上另外清掉配置器快取；`to_cpu=True` 在 CPU 裝置上是 no-op。
  **還有進行中的 batch，或拿不到模型的鎖（其他執行緒正在用）時直接返回、不丟例外**——所以池子在卸載後檢查 `model_is_loaded`。
  `load_model(keep_cache=False)` 沒有 CPU 快取時以原本的 loader 從模型資料夾重新讀檔（所以刪除模型前要先清掉池中的物件）。
  CPU 實機確認（`tiny` float32、Windows、私有記憶體）：載入後 675 MiB → `unload_model()` 後 527 MiB（權重約 148 MiB 釋放）→ `load_model()`
  後 677 MiB；再 5 輪卸載／載入／推論後 676 MiB，沒有累積。卸載後呼叫 `encode()` 丟 `RuntimeError: No model replica is available`。
  所以 CPU 物件也照樣放進池中：卸載確實釋放權重，閒置物件只留執行環境。CPU 上每建立一個新物件是否也會漏記憶體沒有另外量過。

## 載入時間（v0.2.x，2026-11-06）

v0.2.0 的推薦組合（即時 `large-v3-turbo`、完整 `large-v3`，都是 CUDA `int8_float16`）讓按「開始」到聆聽從 v0.1.0 的約 3 s
變成 10.6–13.3 s。快取裡的權重是 float16，`int8_float16` 要在**載入時量化**；模型池的重用也省不了這段：`load_model()`
沒有 CPU 快取時重新讀檔、重新量化（同一個引擎的第二個 session 仍要 10.8 s，新引擎 11.8 s）。

**CTranslate2 的載入流程**（4.8.5 原始碼，2026-11-06 查證）：
- `python/cpp/replica_pool.h`：`ReplicaPoolHelper` 的建構子一開始就 `pybind11::gil_scoped_release`；`python/cpp/whisper.cc` 的
  `load_model`／`unload_model` 以 `py::call_guard<py::gil_scoped_release>()` 綁定。兩條 Python 執行緒可以同時載入。
- `src/models/model.cc`：`Model::load()` 先把所有變數讀進 CPU 記憶體，`set_compute_type()` → `ensure_dtype()` 在 **CPU** 上把 float16
  轉 float32 再量化成 int8（`cpu::quantize_s8`，`parallel_for`），之後 `set_device()` 才把結果搬到 GPU。GPU 上不會同時有 float16
  與 int8 兩份，峰值就是量化後的權重。載入路徑沒有跨模型的全域鎖（只有記錄系統設定一次的 `std::once_flag`）；每個模型物件
  有自己的 `ReplicaPoolHelper` 與 `std::shared_mutex`，互不阻擋。

**並行載入**（`factory.build_components()`）：`device` 為 "cuda" 或 "cpu"、兩條路徑各載入一個模型時，各用一條執行緒同時載入。
實測（每次新行程，依序／並行交錯各 5 次，RTX 5070、Ryzen 7 5700X、模型已在快取，載入＋暖機）：

| | 合計中位數（範圍） | turbo／large-v3 各自 | GPU 用量增加（nvidia-smi 每 50 ms 的最大值） |
|---|---|---|---|
| 依序 | **10.63 s**（9.87–11.76） | 3.6–4.4 s／6.3–7.4 s | 3235–3479 MiB |
| 並行 | **6.86 s**（6.72–7.22） | 4.1–4.7 s／6.7–7.2 s | 3427–3491 MiB |

- CTranslate2 沒有在內部把兩個載入串行化：並行時 large-v3 幾乎不變慢、turbo 慢約 0.5 s（兩者搶 CPU 量化）。
- 峰值不超過兩個模型的和（差異在量測雜訊內）：兩個載入是同一組模型，只是時間重疊；權重在 CPU 量化完才配置 GPU 記憶體。
  `hardware` 的 VRAM 預算不變。
- `device="auto"` 仍依序：CUDA 失敗改用 CPU 時，第二個模型要看得到第一個的失敗紀錄（同一行程不再重試 CUDA，見「行為規則」）。
- 兩條路徑共用一個模型（`workers=2`）時本來就只載入一次。
- 失敗處理：任一個失敗時另一個載入不能中斷（CTranslate2 的建構沒有取消機制），等它結束、`close()` 放回模型池，再丟出錯誤
  （即時路徑的錯誤優先，其他失敗記 error log）。模型不在清單或沒下載時，在載入任何模型之前就丟出（`factory._require_downloaded()`，
  錯誤與 `load_transcriber(download=False)` 相同）。

**預先量化**：見下一節「預先量化的衍生快取」——推薦組合按「開始」到 `running` 7.00 → 2.42 s。
- 另一個可行但沒做的選項：`unload_model(to_cpu=True)` ＋ `load_model(keep_cache=True)` 讓模型池保留量化後的 CPU 副本（同一個引擎的
  下一個 session 免量化），代價是閒置時多佔約 1.5（large-v3）＋0.8（turbo）GB 主記憶體，與目前「閒置物件只留執行環境」的設計衝突；
  衍生快取已涵蓋這個收益（連新引擎也受益），不需要。

## 預先量化的衍生快取（`derived.py`，2026-11-06）

int8 系列精度（`int8`、`int8_float16`、`int8_float32`、`int8_bfloat16`）載入快取裡的 float16 權重時，CTranslate2 每次都在 CPU 上量化
（見上一節）。衍生快取把量化後的權重另存一份，之後從它載入；原始下載的檔案不動，registry 的 SHA-256 仍只驗證原始檔。

### 格式依據（source-driven，CTranslate2 4.8.5）

CTranslate2 沒有讀取既有 `model.bin` 的官方 API，所以自己解析；每條規則都對應到官方原始碼：

| 項目 | 依據 | 實作 |
|---|---|---|
| 檔案結構 | 讀：C++ [`src/models/model.cc`](https://github.com/OpenNMT/CTranslate2/blob/v4.8.5/src/models/model.cc) `Model::load()`（`consume<T>()`、binary version ≥ 4 的 `type_id`＋`num_bytes`、≥ 3 的別名表）；寫：Python `ctranslate2/specs/model_spec.py` `ModelSpec._serialize()` | `read_model_index()`：little-endian `u32 version`、字串（`u16` 長度含結尾 `\0`）、`u32 revision`、`u32 變數數`，每個變數「名稱、`u8 rank`、`u32 × rank`、`u8 dtype`、`u32 bytes`、資料」，最後 `u32 別名數`＋（別名, 目標）。只接受 binary version 6、`WhisperSpec` revision 3（`whisper.cc` 的 `current_spec_revision()`）；每個欄位檢查範圍（變數／別名 ≤ 100000、rank ≤ 8、維度 > 0、dtype 0–5、`bytes == 元素數 × 大小`、不超出檔尾、檔尾沒有多餘資料、名稱非空且是 UTF-8），只讀標頭、資料留在檔案裡 |
| 哪些變數量化 | `model.cc` `Model::set_compute_type()`：`is_quantizable()` ＝ 名稱以 `weight` 結尾（`WhisperModel` 沒有改寫）；名稱含 `conv` 的在 CUDA 與有 oneDNN 的 CPU 上**不量化**、改成浮點精度；其他浮點變數轉成 float 精度、`*_scale` 不轉 | `is_quantized_at_load()`：`weight` 結尾、不含 `conv`、不是純量、float16／float32。conv 權重與其他變數原樣複製（執行時照原本規則轉換；沒有 oneDNN 的 CPU，例如 macOS，會在載入時量化這兩個小權重，結果與從原始檔載入相同） |
| 量化規則 | `model.cc` `ensure_dtype()` → `to_float32()` ＋ `ops::Quantize`（`round_before_cast` ＝ binary version ≥ 5，`model.h`）→ [`src/cpu/kernels.cc`](https://github.com/OpenNMT/CTranslate2/blob/v4.8.5/src/cpu/kernels.cc) `quantize_s8_row()`：每列 `scale = 127 / amax`（amax 為 0 時 1）、`round(x × scale)` 後轉 int8，scale 形狀 `{列數}`（[`src/ops/quantize.cc`](https://github.com/OpenNMT/CTranslate2/blob/v4.8.5/src/ops/quantize.cc)） | `quantize_int8()`：float32 計算、`np.rint`（就近取偶，同 `Vec::round`）、每 2048 列一塊。**不用** Python 轉換器的 `LayerSpec._quantize()`：它會量化 conv、把極小的 amax 夾到 `127 / FLT_MAX`，與執行時的結果不同 |
| 別名 | `model.cc`：別名在量化之後登記，並自動加上 `<alias>_scale → <target>_scale` | 別名表原樣複製（Whisper 只有 `decoder/projection/weight → decoder/embeddings/weight`） |
| 寫檔 | `_serialize()`：依名稱排序寫出變數，量化的變數另有 float32 `<name>_scale` | `write_quantized_model_bin()` 逐個變數讀、量化、寫出，與 `_serialize()` 逐位元組相同（測試以官方函式產生對照檔）。**不直接呼叫 `_serialize()`**：打包的引擎排除了 `ctranslate2.specs`（`packaging/engine/policy.py`，它會帶入 torch），而且它要先把所有變數放進記憶體 |

### 鍵與資料夾

- `<cache>/<id>/derived/<model.bin SHA-256 前 16 碼>-<compute_type>-ct2-<CTranslate2 版本>-f<格式版本>/`，內含量化後的 `model.bin`、
  原樣複製的 `config.json`／`tokenizer.json`／詞彙表等（registry 列的其他檔案），以及完成標記 `kikitori-derived.json`
  （完整的鍵：格式版本、模型 id、revision、來源 SHA-256、精度、CTranslate2 版本；各檔大小；衍生 `model.bin` 的 SHA-256；驗證用的裝置）。
- **與原本草案的差異**：草案是 `<cache>/derived/<id>/…`。改放在 `<cache>/<id>/derived/` 底下，理由：`model_disk_bytes()`（`models.list` 的
  `disk_bytes`）與 `delete_model()`（`models.delete`）本來就以 `<cache>/<id>` 整棵樹為單位，衍生檔自動算進用量、一起刪除，**協定不用改**；
  連結檢查與「只刪快取內」的安全規則也沿用。revision 資料夾名稱是 12 碼 hex，不會與 `derived` 衝突。
- 鍵依精度區分（同一個模型 CPU `int8` 與 CUDA `int8_float16` 的權重位元組其實相同，但驗證是在各自的裝置上做的，分開存比較單純；
  兩者都用時多佔一份磁碟）。
- `find_derived()`：完成標記的鍵與目前完全相同、列出的每個檔案都在且大小相符才算可用（同 `is_downloaded()`，不重算雜湊，啟動快）；
  任一不符就當作不存在。資料夾是連結時也不用。
- 失敗標記 `<鍵>.failed.json`（原因與時間）：有標記的鍵不使用也不重做；鍵改變（例如升級 CTranslate2、改格式版本）時自然重試。
  使用者要強制重試可刪掉這個檔案。

### 產生時機（不在「開始」的關鍵路徑上）

- `load_transcriber(derived_cache=True)`（`factory.create_transcriber()` 一律傳 True）：精度是 int8 系列、模型來自 registry 時——
  有失敗標記 → 照常從原始檔載入；有可用的衍生檔 → 從它載入；否則從原始檔載入（即時量化，與以前相同），**載入成功後**才把這個鍵排入
  `DERIVED_CACHE`（載入失敗，例如 CUDA 不能用，轉換也沒有意義）。`model_path`（使用者自己的資料夾）不適用。
- `DerivedCacheWorker`：一條 daemon 執行緒依序處理。`ASR_ACTIVITY` 計算使用中的模型：`load_transcriber()` 載入期間 +1（借用紀錄要載入完才
  建立，session 一開始載入轉換就要停）、`_ModelLease` 存在期間 +1（最後一個共用者關閉時 −1；`close()` 逾時時也照樣減少，計數不代表推論中），
  所有 ASR 模型都關閉、**連續閒置 15 s**（`IDLE_SECS`）才開始，所以實際上是在 session 結束後做；每轉換一個變數、每個驗證步驟之間檢查，
  有 session 開始（模型被借出）就中止、刪掉暫存、放回佇列，下次閒置時重做，不算失敗。
- **量測依據**（RTX 5070、Ryzen 7 5700X，turbo＋large-v3 int8_float16 已載入，每輪即時＋完整各一次，30 輪）：在背景同時轉換 large-v3 時，
  即時路徑延遲中位數 126 → 130 ms（p90 133 → 141）、完整路徑 286 → 303 ms（p90 314 → 331）；轉換本身 10.7 s（單獨時 11.4 s）。
  只看轉換，在 session 中做也可以接受；但正確性驗證要**再載入兩次模型**（原始檔的即時量化約 7 s CPU、一個模型的顯示記憶體，12 GB 卡上
  與 Ollama 共用時可能不夠），所以整個工作都等閒置再做，被打斷的代價只是重做。
- 剩下的重疊：驗證時 CTranslate2 的模型載入（large-v3 原始檔約 7 s）與 `generate` 不能中斷，session 若剛好在這時開始，要與它同時載入
  （CPU 量化與顯示記憶體都多一份，最多約 7 s）；實測 small 在驗證中開始的 session 1.39 s 照常開始。
- 引擎結束（`factory.release_asr_models()`）先 `DERIVED_CACHE.shutdown()`：下一個檢查點中止、刪暫存（最多等 5 s；驗證中的模型載入
  可能更久，daemon 執行緒隨行程結束，留下的暫存資料夾由下一行說明的清理處理）。`shutdown()` 之後同一個行程不再接受新工作。行程被強制結束時留下的
  `.tmp-*` 暫存資料夾，在下一次轉換前刪掉超過 1 小時的（`STALE_TMP_SECS`；較新的可能是另一個行程正在寫）。

### 正確性把關與原子性

1. 在 `derived/.tmp-<鍵>-<pid>-<亂數>/` 寫入：`model.bin`（`open("xb")`、寫完 `fsync`）＋複製其他檔案（各自 `fsync`，斷電後不會留下大小對
   但內容是零的檔案——`find_derived()` 只比大小）。標記裡的 `model_bin_sha256` 只供診斷，載入時不重算（1.5 GB 約 1–2 s，抵消一部分收益）。
2. 驗證（`_verify()`）：在 session 用的裝置與精度上**依序**（一次只佔一個模型的顯示記憶體）載入原始檔（即時量化）與暫存的衍生檔，
   對固定輸入（`PROBE_SEED` 的亂數 log-mel，1×n_mels×3000）跑 `encode(to_cpu=True)` 與 greedy 解碼 24 個 token（含 `no_speech` 機率）。
   encode 只用到 encoder，解碼用到 decoder 與詞嵌入（logits 經過整個詞嵌入矩陣），兩者合起來涵蓋所有量化的權重。
   **encoder 輸出逐位元組相同、token 序列相同、分數與 no_speech 機率的位元相同**才算通過（不是數值近似）。
3. 寫完成標記（暫存檔＋`os.replace`）後，把暫存資料夾 `rename` 成正式名稱；目標已被另一個行程發布時用它的。正式名稱的資料夾存在但
   不可用（標記壞掉）時先刪掉再做。
4. 任何錯誤（格式不支援、輸出不同、載入失敗、I/O）→ `logger.exception`、寫失敗標記、刪暫存；原始檔照常使用。被取消（session 開始、
   刪除模型、引擎結束）不寫失敗標記。**例外**：失敗當下有 ASR 模型在載入或使用（例如 session 開始載入，驗證時顯示記憶體不足）時不標記，
   放回佇列、閒置時重做——那不是這個鍵的問題；閒置時仍失敗才標記。
5. 載入衍生檔失敗時（`whisper._acquire_model()`）：記錄後改從原始檔載入；**原始檔成功**才寫失敗標記（兩者都失敗代表環境問題，例如顯示
   記憶體不足，原始檔的錯誤照常往上拋，不標記）。暖機失敗沿用 CUDA 失敗的既有規則，不標記。
6. 實測：tiny（CPU int8、CUDA int8_float16）、small、medium、large-v3-turbo、large-v3（CUDA int8_float16）全部逐位元相同；把一個量化值
   改 1 的衍生檔被驗證擋下（`tests/test_asr_derived.py::RealModelTest`）。

### 刪除與模型池

- `models.delete` → `factory.delete_asr_model()` 先 `DERIVED_CACHE.cancel_model(id)`：丟掉排隊中的工作、等正在做的停下（最多 20 s；
  驗證時 CTranslate2 的載入不能中斷，large-v3 原始檔約 7 s）。等不到就丟 `OSError`（引擎回 `internal_error`，稍後再刪）。之後照舊
  `evict_under(<cache>/<id>)` 並刪整個 `<cache>/<id>`（衍生檔在其中）。「使用中」的保護（session、下載）不變。
- 從衍生檔載入的物件在模型池中的鍵是衍生資料夾（`ModelKey.path`），`evict_under(<cache>/<id>)` 一樣涵蓋。
- 驗證時建立的兩個 CTranslate2 物件不經過模型池（暫存資料夾 rename 後路徑就失效），用完就丟——每產生一份衍生檔漏一次 memory_soak #2
  的量（實測同一行程連續 4 次，每次私有記憶體 +19.5 MiB）；每個鍵一生只產生一次。

### 量測（RTX 5070 12 GB、Ryzen 7 5700X、模型已在 OS 檔案快取，2026-11-06）

| | 原始檔（即時量化） | 衍生檔 |
|---|---|---|
| large-v3 單獨載入＋暖機（新行程，交錯各 5 次，中位數） | 6.80 s（6.67–6.95） | **2.53 s**（2.51–2.58） |
| large-v3-turbo 單獨 | 3.75 s（3.68–3.85） | **1.69 s**（1.62–2.94） |
| 引擎 `session.start` → `running`，新引擎的第一個 session（「冷」，交錯各 5 次） | 7.00 s（6.98–7.31） | **2.42 s**（2.41–2.58） |
| 同一個引擎的第二個 session（「熱」，模型物件從池中 `load_model()`） | 7.50 s（7.44–7.62） | **2.09 s**（2.02–2.11） |
| `model.bin` 大小 | large-v3 2944 MiB／turbo 1543 MiB | 1485／781 MiB |

- 引擎量測：推薦組合（即時 turbo、完整 large-v3，CUDA `int8_float16`），**翻譯關閉**（只量 ASR；有翻譯時另加預熱）；「原始檔」以
  失敗標記讓引擎忽略衍生檔。「冷」是新的引擎行程，不是冷的磁碟快取（模型檔都已在 OS 檔案快取裡）。腳本在 scratchpad，未收進 repo。
- 轉換（`write_quantized_model_bin()`，單獨）：large-v3 11.4 s、turbo 5.1 s；私有記憶體只多約 210 MiB（每次一個變數，最大的是
  51866×1280 的詞嵌入），結束後回到原值。
- 整個工作（轉換＋驗證）：large-v3 18.8 s（9.7＋9.1）、turbo 10.6 s（5.0＋5.6）、medium 9.0 s、small 2.9 s。驗證時載入原始檔的峰值與
  一般 session 載入相同（引擎內 medium：私有記憶體 1389 → 峰值 2844 MiB；large-v3 獨立行程 1592 → 4590 MiB）。
- 引擎實測流程（medium、small）：第一個 session 照常（medium 3.84 s）→ 停止後 15.1 s 開始轉換 → 完成 → 下一個 session 從衍生檔載入
  （1.25 s）。轉換途中開始 session：在驗證步驟中止（`interrupted (stopped during verification)`），那個 session 照常開始
  （1.39 s），停止後 15 s 重做並完成。
- 引擎內做完一份衍生檔後 2 s，私有記憶體比開始前高約 240 MiB（medium、small 都是）；獨立行程連續做 4 次只每次 +19.5 MiB，
  沒有隨次數累積。前者的來源沒有查明（可能是 CTranslate2／CUDA 的配置器快取），未修。
- 磁碟：每個 int8 系列的鍵多一份約原始 `model.bin` 一半的檔案（large-v3 +1.5 GB、turbo +0.8 GB），算進 `disk_bytes`。

### 限制

- 舊的鍵（升級 CTranslate2 或格式版本之後）不自動清掉，佔用的空間顯示在 `disk_bytes`，`models.delete` 會一起刪；要自動清理再另外做。
- 第一個 session（以及升級後的第一個 session）仍要即時量化；衍生檔在 session 結束、閒置 15 s 後才產生。
- 只在本機 Windows（RTX 5070、AVX2）實測；macOS／Linux、其他 CPU 指令集（AVX-512 的 `quantize_s8` 路徑）的逐位元一致性由各自機器上的
  驗證步驟把關（不一致就標記失敗、繼續用原始檔），沒有實機確認。
- 格式改版（binary version ≠ 6 或 spec revision 改變）時轉換會失敗並標記，要跟著更新 `derived.py`；這不是 CTranslate2 承諾的介面。

## 幻覺過濾（`filtering.py`）

pipeline 對每個 `Transcript` 呼叫 `rejection_reason(transcript, config)`，回傳 `None` 表示保留，否則回傳原因字串（供 log）。規則依序：

| 規則 | 預設 | 依據 |
|---|---|---|
| 空字串或只有標點／空白 | — | 架構規格 |
| `no_speech_probability > no_speech_threshold` **且** `avg_logprob < log_prob_threshold` | 0.6、−1.0 | Whisper／faster-whisper 的靜音判斷 |
| `no_speech_probability > hard_no_speech_threshold`（不看 logprob） | 0.8 | 本機實測：語音片段 no_speech 最高 0.43，非語音最低 0.51；large-v3 在靜音上輸出「Teksting av Nicolai Winther」時為 0.86 |
| `avg_logprob < min_avg_logprob` | −1.5 | 信心過低（歌聲、BGM 常見） |
| 壓縮比 > `compression_ratio_threshold` | 2.4 | Whisper 的重複迴圈判斷 |
| 正規化後完全等於已知幻覺句 | 19 語言實測片語表（可擴充） | Whisper 在無語音時的常見輸出（`experiments/hallucination_lexicon/`） |

- 已知幻覺句清單**依 Whisper 語言碼分表**（`DEFAULT_HALLUCINATIONS`，`{語言碼: {"hard": frozenset, "soft": frozenset}}`），由 `asr/hallucination_lexicon.py` 的 `HALLUCINATION_LEXICON` 建立。那是 `experiments/hallucination_lexicon/lexicon.json`（Milestone 11：small／large-v3-turbo／large-v3 對強制 19 個語言、確定沒有人聲的 MUSAN 噪音與無人聲音樂、靜音、合成噪音與音效的輸出，人工分類；19 語言 + `"*"` 共 229 個片語；Milestone 16 加入 vtuber_eval 在純 BGM 上通過過濾的 ja hard「次の動画でお会いしましょう。」）的 Python 版本——用 Python 資料而不是 JSON 資源，PyInstaller 隨匯入自動收進去，`packaging/engine/build.py` 不必另列資料檔。`tests/test_asr_hallucination_lexicon.py` 檢查兩者逐字一致，更新時改 `lexicon.json` 並重新產生該檔案。實測效果（README）：推薦組合的完整路徑（turbo final）在 VAD 通過的非語音上，幻覺率 52.8% → 10.3%（交叉驗證 11.0%）；即時路徑（small fast）12.8% → 5.1%。NFKC、去標點、小寫後**整句相等**才算：
  - **hard**（整句字幕套話，例如日文「ご視聴ありがとうございました」「チャンネル登録よろしくお願いします」、英文「Thanks for watching!」「Please subscribe.」、中文「谢谢观看」「请不吝点赞 订阅 转发 打赏支持明镜与点点栏目」）：**所有語言的 hard 片語不論 `Transcript.language` 都套用**。這些句子幾乎不可能是另一個語言的正常語音，而 Whisper 常把它們標成別的語言（英文套話標成 `nl`、中文套話標成 `ja`）。
  - **soft**（真人也常單獨說的短句，例如「Thank you.」「you」「ありがとうございました」「おやすみなさい」「谢谢」）：只比對 `Transcript.language` 那一張表加上不分語言的 `ANY_LANGUAGE`（`"*"`）表，而且 `no_speech_probability > soft_no_speech_threshold`（0.3）時才過濾，避免把其他語言的正常句子濾掉。
- `"*"` 表放字幕署名（Amara.org 的英／中文署名、large-v3 在靜音上輸出的挪威語署名「Teksting av Nicolai Winther」，其語言標籤不會是 `no`）。所有 hard 片語正規化後至少 4 字、且不與任何 soft 片語相同（測試檢查）。印地語、泰語沒有找到可確定是套話的句子，全部是 soft；越南語全部是 hard（訂閱宣傳與署名）。新增語言的資料改 `lexicon.json` 並重新產生 `hallucination_lexicon.py`；`FilterConfig(hallucinations=...)` 也接受單一集合（視為不分語言的 hard 片語），hard／soft 以外的鍵視為錯誤。
- **正規化**（`normalize_for_match()`）：NFKC、casefold，只留 Unicode 字母（L）與數字（N）——**天城文（U+0900–097F）與泰文（U+0E00–0E7F）的組合記號（Mn／Mc）例外保留**。這兩種文字的母音與聲調記號是字的一部分，去掉會讓不同的詞變成同一個片語（「करते हैं」與「करती है」、「कर दो」與「कर दे」都會相同，soft 片語就會誤濾別的句子）。以實測的原始輸出比對：保留後印地語片語的命中由 761 次變 760 次、泰語 386 次不變，幾乎不影響效果。阿拉伯文的母音符號（harakat）可有可無、Whisper 的輸出時有時無，照舊忽略；拉丁字母的重音經 NFKC 已合成成字母，不受影響。
- 片語表的限制（見實驗 README）：只檢查過日文（`experiments/asr_benchmark` 的 190 個語音辨識結果、`experiments/translate_benchmark` 的台詞）沒有整句相同；其他語言沒有真實語音素材，測試另以每個語言數句一般句子（含與片語相近的句子）確認不會被濾。部分 hard 片語是直播主也可能真的說的結尾句（「チャンネル登録よろしくお願いします」「다음 영상에서 만나요.」「Merci d'avoir regardé cette vidéo !」），整句相等時會被濾掉——沿用既有清單把片尾套話列為 hard 的判斷，未經母語者審閱；繁體中文片語是人工轉寫、未實測。
- **large-v3-turbo 的 no_speech 不可用**（`ModelSpec.no_speech_reliable = False`）：實測靜音、噪音也是 0.00，前三條規則對它無效。實測在指定語言（`ja`）或候選語言（`("zh", "ja")`）時，turbo 對非語音輸出「ご視聴ありがとうございました」而被清單擋下；只有完全不指定語言時會輸出「you」「Thank you.」漏網。因此：(1) VAD 是第一道防線；(2) pipeline 用 turbo 當完整模型時應指定語言或候選語言；(3) 建議 pipeline 以即時路徑模型（no_speech 可用）對同一句的結果作為輔助判斷。
- 所有門檻都在 `FilterConfig`，由設定頁覆寫。

## 即時與完整模型的分工

| 路徑 | 目標 | 預設解碼 | 模型選擇原則 |
|---|---|---|---|
| 即時（`is_final=False`，每 700 ms） | 單次延遲 < 間隔，落後就跳過 | `FAST_DECODE`：greedy、無溫度 fallback、最多 128 token、不帶 prompt | 單次延遲明顯低於 700 ms（CPU 上只有 tiny、base 做得到） |
| 完整（`UtteranceEnded` 後） | 準確 | `FINAL_DECODE`：beam 5、溫度 (0.0, 0.2, 0.4)、帶前文 prompt | 在硬體預算內品質最高、單句延遲可接受（字幕情境 < 約 2 s） |

兩者可以是同一個模型（例如 th／ar／hi 的推薦兩條都用 `large-v3`）。模型、精度、執行緒數都相同時，pipeline 只載入一次
（`load_transcriber(workers=2)` → CTranslate2 `inter_threads=2`，同一裝置的 worker 共用權重），完整路徑用
`WhisperTranscriber.with_options()` 換成自己的解碼參數與 lock，兩條路徑仍可並行；依據見 `hardware.md`「兩條路徑共用一份權重」。

## 硬體等級推薦（供 `hardware` 模組使用）

依 `experiments/asr_benchmark/README.md`（2026-11-05，Ryzen 7 5700X、RTX 5070）。延遲為單句、模型已暖機、單獨執行時的中位數；即時與完整同時執行、或與翻譯 LLM 共用 GPU 時會更慢（未量測）。

| 等級 | 判斷條件（建議） | 即時模型 | 完整模型 | 依據（本機延遲／資源） | 備註 |
|---|---|---|---|---|---|
| NVIDIA ≥ 8 GB VRAM（Milestone 16） | 偵測到 NVIDIA GPU、VRAM ≥ 8 GB、cuBLAS 可載入 | `large-v3-turbo` CUDA float16 或 int8_float16 | `large-v3` CUDA float16 或 int8_float16 | 延遲中位約 0.16 s（turbo greedy，GPU 共用下）＋0.27–0.41 s（large-v3 f16／int8 單獨執行）；VRAM 2.9–6.0 GB | `experiments/vtuber_eval`：YODAS 口語 CER turbo 比 small 低 5.6 點；BGM 10／5 dB 時 large-v3 完整路徑比 turbo 好 4.1／5.3 點；int8_float16 品質不變。精度依與本機翻譯 LLM 共用時的 VRAM 預算決定，連 4B 都放不下時即時改 `small`（見 `hardware.md`「8 GB 以上等級的組合」）。th／ar／hi 即時也用 `large-v3`（turbo 是 C 級），共用一份權重。large-v3 的 no_speech 可用 |
| NVIDIA 4–8 GB VRAM | 同上，4 GB ≤ VRAM < 8 GB | `base` CUDA int8_float16 | `large-v3-turbo` CUDA int8_float16 | 0.07–0.19 s＋0.17 s；VRAM 約 0.45＋1.25 GB | 留空間給翻譯 LLM；int8_float16 比 float16 慢 5–50% 但省一半 VRAM |
| NVIDIA < 4 GB VRAM | VRAM < 4 GB | `base` CUDA int8_float16 | `small` CUDA int8_float16 | 0.07–0.19 s＋0.18 s；VRAM 約 0.45＋0.65 GB | 若翻譯 LLM 也在 GPU，VRAM 可能仍不足，改用 CPU 等級 |
| CPU、RAM ≥ 15 GiB 且實體核心 > 4（`cpu`） | 沒有可用 NVIDIA GPU（或 cuBLAS 不可載入），RAM ≥ 15 GiB（標稱 16 GB 扣掉內顯共用後常為 15.x GiB）且實體核心 > 4 | `base` CPU int8 | `small` CPU int8 | 0.45 s＋1.3 s；RAM 約 0.35＋0.95 GB | 8 核以上 CPU 實測。`medium`（3.6 s、RTF 0.9）與 `large-v3-turbo`（5.1 s、RTF 1.2）在 CPU 上太慢，不建議 |
| CPU、RAM < 15 GiB 或實體核心 ≤ 4（`cpu_low`） | 同上，RAM < 15 GiB、實體核心 ≤ 4，或 RAM 偵測不到 | `tiny` CPU int8 | `base` CPU int8 | 0.23 s＋0.47 s；RAM 約 0.23＋0.35 GB | 品質明顯下降（tiny 在 VTuber 片段錯字多）；核心少時延遲會高於本機數值（未量測） |
| Apple Silicon | macOS arm64 | `base` CPU int8 | `small` CPU int8 | **未量測**（CTranslate2 在 macOS 只有 CPU：Accelerate + Ruy） | 暫比照 `cpu` 等級（RAM ≥ 15 GiB 且實體核心 > 4）；需在 M 系列實機跑 `experiments/asr_benchmark` 後修正 |

- `cpu_threads`：小模型用預設值即可；small 以上設為實體核心數（上限 8）約快 8%。即時與完整模型同時在 CPU 執行時，建議兩者合計不超過實體核心數（例如 4＋4）。
- 語言：所有建議模型都是多語模型，語言清單的 20 種語言共用；影片翻譯指定收聽語言時以 `DecodeOptions(forced_language=...)` 固定，`auto` 時以 `candidate_languages` 限在語言清單的 Whisper 語言；口譯模式限在（我的語言, 對方語言），例如 `("zh", "ja")`。
- 依語言的品質（Milestone 12）：每個 registry 模型對每個語言的錯誤率等級（A／B／C）記在 `languages/catalog.py` 的 `asr_quality`
  （出處與表格見 `hardware.md`「ASR 品質等級」）。推薦**不因語言換模型**（硬體放不下更大的模型），只在辨識語言用推薦的完整模型是 C 級
  （錯誤率 > 20%，例如 turbo 的泰語、阿拉伯語、印地語、越南語；small 的中文）時提示。只支援單一語言的模型（`ModelSpec.languages`
  不是 `("multilingual",)`，例如之後加入的日語特化模型）由 `ModelSpec.supports()` 判斷，只在辨識語言全在它支援的範圍內才會被推薦
  （`hardware/recommend.py` 的 `ASR_CANDIDATES`）。新增 registry 模型時要一併在 `asr_quality` 補上等級（測試檢查）。

## 測試策略

- 框架：`unittest`；檔案 `tests/test_asr_*.py`。
- 單元測試不連網、不下載模型：
  - 輸入驗證（dtype、維度、空陣列、非 ndarray）。
  - log-mel：形狀、靜音值、與獨立計算（直接用 numpy FFT）的數值一致。
  - 分詞器：用手寫的小型 `tokenizer.json` 測 BPE 合併、byte-level 編解碼、特殊 token、GPT-2 pre-tokenize 規則（含日文、空白、縮寫）。
  - 推論流程：注入假的 CTranslate2 模型，檢查 prompt 組成、語言偵測與候選語言、avg_logprob 公式、溫度 fallback、30 s 以上切窗、lock、CUDA 失敗退回 CPU 與「同一行程不再重試 CUDA」。
  - registry：id 唯一、revision 為 40 碼 hex、SHA-256 為 64 碼 hex、必要檔案齊全。
  - 快取目錄：模擬 Windows／macOS／Linux 與環境變數（含不合法值）。
  - 下載：本機 `http.server`（只綁 127.0.0.1）與假 fetcher；進度回報、續傳、HTTP 錯誤、逾時、雜湊不符、取消、非 https 拒絕。
  - 衍生快取（`test_asr_derived.py`）：以官方 `ModelSpec._serialize()` 產生的檔案對照讀寫格式（輸出逐位元組相同），量化對照逐元素的參考實作
    （全零列、就近取偶、分塊）；格式錯誤的各種檔案；鍵、完成／失敗標記、竄改偵測、原子發布（失敗或取消不留暫存）、背景工作的閒置等待、
    session 打斷後重排、取消與關閉；`load_transcriber(derived_cache=True)` 的載入選擇與失敗回退（假的 `ctranslate2`）。本機快取有 `tiny`
    時以真的 CTranslate2（CPU int8）確認衍生檔與即時量化逐位元相同、改錯一個值會被擋下（沒有時 skip）。
  - 幻覺過濾：每條規則的正反例；片語表（`test_asr_hallucination_lexicon.py`）：與 `lexicon.json` 一致、保留既有項目、每個 hard 片語在 19 個語言標籤下都被濾、soft 只在同語言且 no_speech 高於門檻時濾、各語言一般句子與翻譯評測台詞在任何語言標籤下都保留、本機有 `experiments/asr_benchmark/results` 時真實日語辨識結果不被片語濾掉（沒有資料時 skip）、天城文與泰文保留組合記號而阿拉伯文照舊忽略。
- 真模型檢查放在 `experiments/asr_benchmark/`（含與 HF `tokenizers` 對照分詞結果），不放進單元測試。
- 指令：`uv run python -m unittest discover -s tests`、`uv run ruff check`。

## 已知限制與待驗證

- Milestone 16：兩條路徑共用權重時第二個 worker 的 VRAM 以 512 MiB 估計（`hardware.SHARED_WORKER_EXTRA_MB`），沒有單獨量測；
  新推薦組合（turbo int8 ／ large-v3 int8 ＋ qwen3.5:9b）在 12 GB 卡上的實際 VRAM 由端到端驗收量測（`vtuber-profile.md`）。

- 只量了 12 段日文（動漫、VTuber）；英文、中文的準確度與語言偵測未量測。CER 樣本只有 6 段且受片假名人名影響。
- macOS（Apple Silicon，Accelerate）與 Linux 未在實機執行：快取目錄只有單元測試；Linux 的 cuBLAS 預載（`ctypes.CDLL` + `RTLD_GLOBAL`）未實測。
- 即時與完整兩個模型同時執行、以及與 Ollama 共用 GPU 時的延遲未量測；tiny／base／small 的 VRAM 為估計值。
- 其他 NVIDIA 世代（Turing、Ampere、Ada）未測；CTranslate2 以 CUDA 12.8 `Common` 架構建置，Blackwell 實測可用。
- 下載沒有跨行程鎖：兩個行程同時下載同一模型可能互相覆寫 `.part`（最後雜湊驗證會擋下錯誤檔案）。
- 分詞器的 `\s` 判斷用 Python `str.isspace()`，與 HF `tokenizers` 在少數控制字元（U+001C–U+001F）上可能不同；514 個樣本實測一致。

## 邊界

- **一律**：固定 revision 與 SHA-256；網路 I/O 設 timeout；錯誤往上拋並 log；不在 stdout 輸出（stdout 留給引擎協定）。
- **先問**：新增依賴；把 cuBLAS 或其他 NVIDIA 函式庫列為依賴或隨安裝檔散布；加入只支援單一語言的模型。
- **絕不**：依賴 PyTorch；把模型檔放進 repo；使用 `huggingface_hub`、`faster-whisper`、`av`。
