# Spec: hardware（硬體偵測與模型組合）

> 狀態：已採用（2026-11-05，使用者授權依建議自主實作，不等待審核）。
> 上位文件：`docs/specs/app-architecture.md` 的 `hardware` 一節與引擎方法 `hardware.detect`、`models.list`、`models.download`；本文件不得與之矛盾。
> 依據：`docs/specs/asr.md` 的「硬體等級推薦」、`docs/specs/translate.md` 的「依 VRAM 的推薦順序」、CTranslate2 官方文件（quantization、installation）、NVIDIA CUDA Compatibility 文件。

## 前提假設

1. 硬體偵測只用標準庫與 `ctypes`（不加 psutil）；GPU 只偵測 NVIDIA（CTranslate2 只支援 CUDA）。Apple Silicon 只判斷是否為 M 系列，ASR 一律 CPU（CTranslate2 在 macOS 沒有 GPU 後端）。
2. 翻譯 LLM 由使用者自行安裝在 OpenAI 相容伺服器（預設本機 Ollama）；伺服器在本機時，LLM 與 ASR 共用同一張 GPU 的 VRAM。
3. cuBLAS 12 不隨安裝檔散布；由使用者在 UI 按鈕觸發下載（見「GPU 執行環境」）。
4. 推薦只是預設值，使用者可在設定頁覆寫；覆寫的組合由 `validate_combination()` 回報錯誤與警告，是否阻擋由 UI 決定。
5. 本模組不讀寫設定檔、不依賴 `config`／`engine`；引擎把設定值當參數傳進來（見「引擎整合」）。

## Objective

讓第一次開啟 app 的使用者不需要懂模型，就能得到「這台電腦跑得動、品質最好」的組合，並清楚知道還缺什麼（cuBLAS、Ollama、翻譯模型）。成功標準：

- `detect()` 在一般電腦上 1 秒內完成（本機實測 0.16 s），任何單項偵測失敗都不會讓整體失敗。
- `recommend()` 對每個硬體等級給出與 `asr.md`／`translate.md` 一致的組合，原因與警告為可直接顯示的繁體中文。
- 有 NVIDIA GPU 但缺 cuBLAS 的使用者按一個按鈕就能啟用 GPU，不必安裝 CUDA Toolkit。

## 對外介面（`src/kikitori/hardware/`）

| 檔案 | 角色 | 主要名稱 |
|---|---|---|
| `types.py` | 凍結 dataclass 與 `to_dict()` | `HardwareInfo`、`CpuInfo`、`MemoryInfo`、`GpuInfo`、`CublasInfo`、`TranslationServerInfo`、`AsrChoice`、`ModelCombination`、`ValidationResult` |
| `system.py` | OS、CPU、RAM（各平台） | `detect_cpu()`、`detect_memory()`、`detect_apple_silicon()`、解析器 |
| `nvidia.py` | `nvidia-smi` 與 cuBLAS 搜尋 | `query_nvidia_gpus()`、`find_cublas()`、`NvidiaSmiError` |
| `translation_server.py` | `GET {base_url}/models` | `probe_translation_server()` |
| `detection.py` | 組合上述偵測 | `detect()`、`auto_cpu_threads()`（只偵測 CPU；設定 `asr.*_cpu_threads` 為 null 時用） |
| `recommend.py` | 推薦與覆寫驗證（純函式） | `recommend()`、`validate_combination()`、`cpu_threads_per_model()` |
| `gpu_runtime.py` | cuBLAS wheel 下載安裝 | `install_gpu_runtime()`、`gpu_runtime_status()`、`installed_runtime_dir()`、`GpuRuntimeError` |
| `overview.py` | `models.list` 的總覽 | `models_overview()` |
| `__init__.py` | 對外匯出 | `hardware_report()`（`hardware.detect` 的完整結果） |

```python
def detect(*, base_url="http://localhost:11434/v1", api_key_env="KIKITORI_TRANSLATE_API_KEY",
           cuda_library_dirs: Sequence[Path] = (), runtime_root: Path | None = None,
           nvidia_smi_timeout=5.0, probe_timeout=2.0) -> HardwareInfo
def recommend(info: HardwareInfo, *, allow_restricted: bool = True) -> ModelCombination
def validate_combination(data: Mapping[str, Any], info: HardwareInfo, *,
                         downloaded_asr: Collection[str] | None = None,
                         allow_restricted: bool = True) -> ValidationResult
def models_overview(info, recommendation=None, *, cache_dir=None, runtime_root=None) -> dict
def install_gpu_runtime(root=None, *, progress=None, cancel=None, timeout=30.0,
                        fetcher=None, wheel=None) -> Path
def gpu_runtime_status(root=None, wheel=None) -> GpuRuntimeStatus
def hardware_report(*, base_url=..., api_key_env=..., cuda_library_dirs=(), allow_restricted=True) -> dict
```

## 偵測（`detect()`）

| 項目 | Windows | Linux | macOS |
|---|---|---|---|
| OS 版本 | `platform.release()`／`version()` | `/etc/os-release` 的 `PRETTY_NAME` + 核心版本 | `platform.mac_ver()` |
| CPU 型號 | 登錄檔 `HKLM\HARDWARE\DESCRIPTION\System\CentralProcessor\0\ProcessorNameString` | `/proc/cpuinfo` 的 `model name`（ARM：`Model`／`Hardware`） | `sysctlbyname("machdep.cpu.brand_string")` |
| 實體核心 | `GetLogicalProcessorInformationEx(RelationProcessorCore)` 的筆數 | `/proc/cpuinfo` 不重複的 (physical id, core id)；沒有時用 `/sys/devices/system/cpu/cpu*/topology` | `hw.physicalcpu` |
| 邏輯核心 | `os.cpu_count()` | 同左 | `hw.logicalcpu` |
| RAM 總量／可用 | `GlobalMemoryStatusEx` | `/proc/meminfo` 的 `MemTotal`／`MemAvailable` | `hw.memsize`；可用＝`vm_stat` 的 (free + inactive + speculative) × page size（timeout 3 s） |
| NVIDIA GPU | `nvidia-smi`（PATH → `%SystemRoot%\System32` → `%ProgramFiles%\NVIDIA Corporation\NVSMI`） | `nvidia-smi`（PATH → `/usr/bin`） | 不偵測 |
| Apple Silicon | — | — | `machine == arm64`，或 `hw.optional.arm64 == 1`（Rosetta 下執行） |

- **`nvidia-smi`**：`--query-gpu=name,memory.total,memory.used,driver_version,compute_cap --format=csv,noheader,nounits`，timeout 5 s，`stdin=DEVNULL`，Windows 加 `CREATE_NO_WINDOW`（引擎是無主控台的 sidecar，不加會彈出主控台視窗）。舊驅動不認得 `compute_cap` 時改用不含它的欄位重查。每行驗證：名稱可列印且 ≤ 200 字、VRAM 為 0–10⁷ MiB 的整數、`[N/A]`／`[Not Supported]` 轉為 None、版本與 compute capability 符合格式；壞行略過並記在 `errors`，全部壞掉或結束碼非 0 → `NvidiaSmiError`（被 `detect()` 轉成 `errors` 一筆）。最多 64 張。
- **cuBLAS 12**（只檢查檔案存在，不載入）：依序找 (1) `cuda_library_dirs`（`source="configured"`）、(2) kikitori 的 GPU 執行環境（`"runtime"`）、(3) 系統目錄（`"system"`）：Windows 為 `CUDA_PATH_V12_*`、`CUDA_PATH` 的 `bin`、`%ProgramFiles%\NVIDIA GPU Computing Toolkit\CUDA\v12.*\bin`、`PATH`；Linux 為 `LD_LIBRARY_PATH`、`/usr/local/cuda/lib64`、`/usr/local/cuda-12*/lib64`、`/usr/lib/x86_64-linux-gnu`、`/usr/lib64`、`/usr/lib`、`/usr/local/lib`。必要檔案：Windows `cublas64_12.dll`、`cublasLt64_12.dll`；Linux `libcublas.so.12`、`libcublasLt.so.12`。只有 Windows／Linux x86-64 視為支援（CTranslate2 的 Linux aarch64 wheel 只有 CPU，官方 installation 文件）。
- **翻譯伺服器**：`GET {base_url}/models`（`http.client`，不經 proxy；timeout 2 s）。`http://localhost` 依序試 `127.0.0.1`、`::1`（理由同 translate.md），`Host` 標頭保留 `localhost:<port>`。金鑰只在環境變數有值時送出，不出現在結果、log 或例外。回應 ≤ 1 MiB、須為 `{"data": [{"id": str}, ...]}`；不合法的項目略過（最多 1000 個、名稱 ≤ 200 字）。`is_local` 為 loopback 主機（`localhost`、127.0.0.0/8、::1）。
- **錯誤處理**：每一項失敗都記 log、寫進 `HardwareInfo.errors`（例如 `"gpu: nvidia-smi timed out after 5 s"`），欄位為 None 或空；不丟例外。

## 推薦（`recommend()`）

### 名詞與常數

| 常數 | 值 | 理由 |
|---|---|---|
| 桌面保留 VRAM | 1024 MiB | 桌面合成、瀏覽器、影片播放器 |
| CUDA context | 500 MiB | registry 的 VRAM 量測各含一份；同一行程兩個模型只算一次 |
| 8 GB 等級門檻 | 7680 MiB | 標稱 8 GB 的卡回報約 8188 MiB，容許 0.5 GiB 誤差 |
| 4 GB 等級門檻 | 3584 MiB | 同上 |
| large-v3 門檻 | 11264 MiB 且翻譯不在本機 | asr.md：「VRAM 寬裕（≥ 12 GB 且不跑本機 LLM）可改 large-v3」 |
| 最低驅動 | 525 | NVIDIA CUDA minor version compatibility：CUDA 12.x 需要 ≥ 525 |
| CPU 高 RAM 門檻 | 15 GiB | 標稱 16 GB 扣掉內顯共用後常為 15.x GiB |
| CPU 低核心 | 實體核心 ≤ 4 | asr.md |
| Apple GPU 比例 | 統一記憶體 × 0.65，且不超過「總量 − ASR − 4 GiB」 | macOS 預設讓 GPU 使用約 2/3 記憶體 |

- **主 GPU**：VRAM 最大的一張（CUDA 預設裝置順序為 FASTEST_FIRST，CTranslate2 用裝置 0，通常就是它）；多於一張時加警告。
- **GPU 能否跑 ASR**：平台支援（Windows／Linux x86-64）、驅動 ≥ 525、compute capability ≥ 7.0（float16／int8）或剛好 6.1（只支援 int8，CTranslate2 quantization 文件）；其他情況 ASR 改 CPU 並加警告。可以跑但缺 cuBLAS → ASR 改 CPU、`gpu_runtime_missing = True`、警告「cuBLAS 未安裝，GPU 加速需要先下載…」（UI 顯示下載按鈕；安裝後重新 `detect()` 就會得到 GPU 組合）。

### ASR 等級（與 asr.md 一致）

| tier | 條件 | 即時 | 完整 |
|---|---|---|---|
| `nvidia_8gb` | VRAM ≥ 7680 MiB | `small` cuda float16 | `large-v3-turbo` cuda float16（≥ 11 GiB 且翻譯不在本機時 `large-v3`） |
| `nvidia_4gb` | 3584 ≤ VRAM < 7680 | `base` cuda int8_float16 | `large-v3-turbo` cuda int8_float16 |
| `nvidia_lt4gb` | VRAM < 3584 | `base` cuda int8_float16 | `small` cuda int8_float16 |
| `cpu` | 無可用 GPU，RAM ≥ 15 GiB 且實體核心 > 4 | `base` cpu int8 | `small` cpu int8 |
| `cpu_low` | 無可用 GPU，RAM < 15 GiB、核心 ≤ 4 或 RAM 未知 | `tiny` cpu int8 | `base` cpu int8 |
| `apple_silicon` | macOS Apple Silicon | `base` cpu int8 | `small` cpu int8（未實測） |

- compute capability 6.1（Pascal）時 GPU 等級的精度一律 `int8`。
- ASR 需要的 VRAM（registry 數值，fp16 用 `approx_gpu_vram_mb`、int8 類用 `approx_gpu_vram_int8_mb`，兩個模型扣一次 context）大於「VRAM − 1024」時改用 CPU 等級並警告。
- CPU 執行緒：即時與完整同時執行，各用 `min(8, 實體核心 // 2)`（asr.md：兩者合計不超過實體核心數）；核心數未知時 0（CTranslate2 預設）。GPU 時 0。

### 翻譯模型

- 翻譯伺服器不在本機 → `translation_fast/final = None`，原因說明要在設定頁選該服務的模型。
- 可給翻譯的 VRAM：有 NVIDIA GPU 時 = 主 GPU VRAM − 1024 − ASR 在 GPU 上的用量（ASR 在 CPU 時 Ollama 仍可用整張卡，Ollama 自帶 CUDA runtime）；Apple Silicon 用上表公式；其他 → None（CPU）。
- 呼叫 `translate.recommend_translation_models(budget, installed, allow_restricted)`（非商用的 Sakura 一律不推薦；`allow_restricted=False` 排除 HY-MT）。`installed` 為伺服器回報的模型（無法取得時 None）。
- 另以 `installed=None` 算出「理想」選擇：實際選擇不同時加原因「已安裝的模型中選 X；安裝 Y 品質較好」；選中的模型沒裝 → 警告「建議的翻譯模型「X」尚未安裝」。伺服器連不上 → 警告「Ollama 是否已啟動？」。模型權重大於可用 VRAM → 警告 Ollama 會部分用 CPU。CPU 推論且 RAM < 8 GiB → 警告。

## 手動覆寫（`validate_combination()`）

輸入與 `ModelCombination.to_dict()` 同形狀（`device` 另可為 `auto`、`compute_type` 可為 null）。純函式，格式錯誤也以 `errors` 回報。

| 類別 | 檢查 |
|---|---|
| errors | 不是物件；未知的 ASR id；`device` 不是 auto／cpu／cuda；`compute_type` 不是 CTranslate2 認得的值；`cpu_threads` 不是 0–256 整數；選 cuda 但沒有 NVIDIA GPU、GPU 不支援（平台、驅動、compute capability）或缺 cuBLAS；翻譯模型名稱不是 1–200 字的可列印字串 |
| warnings | `auto` 時 GPU 不可用而會用 CPU；CPU 上用 float16 等 CPU 不支援的精度（CTranslate2 會自動轉型）；compute capability < 7.0 用 float16／int8_float16；CPU 上用 medium 以上；ASR 模型未下載；即時模型比完整模型大；兩條路徑都是 no_speech 不可用的模型；ASR + 翻譯合計超過「VRAM − 1024」；CPU 上 ASR 超過一半 RAM；翻譯模型不在伺服器上；非商用授權；地區限制（`allow_restricted=False`）；本機伺服器上即時與完整翻譯用不同模型（換模型 6–18 s） |

## GPU 執行環境（cuBLAS）

### 決策

使用者按「下載 GPU 執行環境」後，從 PyPI 下載 NVIDIA 官方 wheel `nvidia-cublas-cu12==12.8.4.1`（與 CTranslate2 4.8.5 的 CUDA 12.8 相同；本機 RTX 5070 實測可用），只解出需要的兩個函式庫與授權全文。不需要 cuDNN（Whisper 不用）。

| 平台 tag | URL | 大小 | SHA-256 |
|---|---|---|---|
| `win_amd64` | `https://files.pythonhosted.org/packages/70/61/7d7b3c70186fb651d0fbd35b01dbfc8e755f69fd58f817f3d0f642df20c3/nvidia_cublas_cu12-12.8.4.1-py3-none-win_amd64.whl` | 567,544,208 | `47e9b82132fa8d2b4944e708049229601448aaad7e6f296f630f2d1a32de35af` |
| `manylinux_2_27_x86_64` | `https://files.pythonhosted.org/packages/dc/61/e24b560ab2e2eaeb3c839129175fb330dfcfc29e5203196e5541a4c44682/nvidia_cublas_cu12-12.8.4.1-py3-none-manylinux_2_27_x86_64.whl` | 594,346,921 | `8ac4e771d5a348c551b2a426eda6193c19aa630236b418086020df5ba9667142` |
| `manylinux_2_27_aarch64`（不使用） | `.../29/99/db44d685f0e257ff0e213ade1964fc459b4a690a73293220e98feb3307cf/...aarch64.whl` | 590,537,124 | `b86f6dd8935884615a0683b663891d43781b819ac4f2ba2b0c9604676af346d0` |

來源：`https://pypi.org/pypi/nvidia-cublas-cu12/12.8.4.1/json`（2026-11-05）；wheel 內容以 HTTP Range 讀 zip 中央目錄確認。aarch64 不使用，因為 CTranslate2 的 aarch64 wheel 沒有 CUDA。

解出的檔案（大小寫死在程式中，與 zip 目錄不符就拒絕）：

| 平台 | 檔案（wheel 內路徑 → 安裝後檔名） | 大小 |
|---|---|---|
| Windows | `nvidia/cublas/bin/cublas64_12.dll` | 113,716,224 |
| | `nvidia/cublas/bin/cublasLt64_12.dll` | 674,667,520 |
| Linux x86-64 | `nvidia/cublas/lib/libcublas.so.12` | 116,388,640 |
| | `nvidia/cublas/lib/libcublasLt.so.12` | 751,771,728 |
| 兩者 | `nvidia_cublas_cu12-12.8.4.1.dist-info/License.txt` → `License.txt` | 59,262 |

### 流程

1. 位置：`<asr 快取的上一層>/gpu-runtime/cublas-12.8.4.1/`（Windows `%LOCALAPPDATA%\kikitori\gpu-runtime\...`、macOS 不支援、Linux `$XDG_CACHE_HOME/kikitori/gpu-runtime/...`）。
2. 已安裝（標記 `kikitori-gpu-runtime.json` 相符且每個檔案大小正確）→ 直接回傳。
3. 檢查磁碟空間（wheel 剩餘大小 + 解出大小）。
4. 下載到 `gpu-runtime/downloads/<wheel>.part`：沿用 `asr.download` 的公開函式 `download_file()`（https-only 含轉址、每次連線與讀取 timeout 30 s、`Range` 續傳、Content-Length／Content-Range 檢查、邊下載邊算 SHA-256、雜湊不符刪 `.part`、`threading.Event` 取消、進度 `(downloaded, total)`）。
5. 解壓到 `cublas-12.8.4.1.tmp/`：只取寫死的成員、比對 `ZipInfo.file_size`、串流複製時計數防止超量（壓縮炸彈）、zipfile 讀完自動驗 CRC-32；寫標記後 `os.replace` 成正式目錄。失敗時刪暫存目錄；已驗證的 wheel 保留，重試時重算 SHA-256 後直接解壓。
6. 成功後刪掉 wheel 與空的 `downloads/`。

### 授權（NVIDIA EULA）

- wheel 內的 `License.txt` 是 NVIDIA CUDA Toolkit EULA（PyPI 標示 "NVIDIA Proprietary Software"）。Attachment A 把 `cublas.dll`、`cublasLt.dll`、`libcublas.so`、`libcublasLt.so` 列為可隨應用程式再散布的元件（條件：應用程式有實質額外功能、元件只供該應用程式使用、散布條款與 EULA 一致等）。
- **本專案不再散布**：安裝檔與 repo 都不含 NVIDIA 檔案（避免 0.8 GB 的安裝檔與專有授權進入發行物；也符合 asr.md「把 cuBLAS 列為依賴或隨安裝檔散布要先問」）。檔案由使用者自己的電腦直接從 PyPI（NVIDIA 官方發布管道）下載，等同使用者自行 `pip install`。
- **UI 要求**：按鈕旁顯示「這會從 PyPI 下載 NVIDIA cuBLAS（約 570 MB），使用即表示同意 NVIDIA CUDA EULA」與連結 `https://docs.nvidia.com/cuda/eula/index.html`（`GpuRuntimeStatus.license_url`）；使用者確認後才呼叫 `install_gpu_runtime()`。`License.txt` 保留在安裝目錄。
- 若之後要改成隨安裝檔散布，需重新評估 Attachment A 的條件並先問使用者。

## `models.list` 總覽（`models_overview()`）

```jsonc
{
  "asr": [{"id": "small", "size_bytes": 487…, "downloaded": true, "license": "MIT", "speed_tier": "medium",
           "no_speech_reliable": true, "cpu_ram_mb": 950, "vram_mb": 1000, "vram_int8_mb": 650,
           "recommended_for": ["fast"]}, …],
  "gpu_runtime": {"id": "gpu-runtime", "supported": true, "installed": true, "package": "nvidia-cublas-cu12",
                  "version": "12.8.4.1", "path": "…", "download_bytes": 567544208, "installed_bytes": 788443006,
                  "license": "NVIDIA CUDA Toolkit End User License Agreement", "license_url": "https://docs.nvidia.com/cuda/eula/index.html",
                  "cublas_available": true, "cublas_source": "runtime"},
  "translation": {
    "server": {"base_url": "http://localhost:11434/v1", "is_local": true, "reachable": true, "error": null},
    "models": [{"name": "qwen3.5:9b", "label": "Qwen3.5 9B", "installed": true, "known": true,
                "recommended_for": ["fast", "final"], "license": "Apache-2.0", "license_url": "…",
                "commercial_use": true, "excluded_regions": [], "approx_vram_gb": 6.6}, …]
  }
}
```

`translation.models` 為 translate 預設組與伺服器上已安裝模型的聯集；`installed` 在伺服器連不上時為 null；不認得的模型 `known=false`、授權欄位為 null。

## 引擎整合（Milestone 6 已接線，協定細節見 `pipeline-engine.md`）

- `hardware.detect` → `detect(base_url=s.translation.base_url, api_key_env=s.translation.api_key_env, cuda_library_dirs=s.asr.cuda_library_dirs)` + `recommend(info, allow_restricted=s.translation.allow_restricted)`，與 `hardware_report()` 相同的 `{"hardware": HardwareInfo.to_dict(), "recommendation": ModelCombination.to_dict()}`（拆開呼叫是為了快取 `HardwareInfo` 給下面三個方法重用）；`hardware` 保留舊欄位 `cpu_count`、`ram_bytes`、`gpus`、`apple_silicon`、`os`、`os_version`、`machine`。在引擎的背景工作執行緒上執行，不佔用讀 stdin 的執行緒。
- `models.list` → `models_overview(info, recommend(info))`，另保留第一版的 `translation.base_url／installed／error／presets`。
- `models.download {"model": "gpu-runtime", "accept_license": true}`（`GPU_RUNTIME_ID`）→ `install_gpu_runtime(progress=..., cancel=...)`，進度用同一個 `download_progress` 事件（`model = "gpu-runtime"`）。沒有 `accept_license: true` 時回 `license_not_accepted`（訊息含 EULA 網址）；`engine.shutdown` 時取消。
- `models.validate` → `validate_combination(combination_from_settings(設定), info, downloaded_asr=已下載的 ASR, allow_restricted=設定)`。
- `settings.apply_recommendation` → `recommend(info)` 轉成設定（`engine/hardware_service.py` 的 `recommendation_patch()`）後存檔。
- 建立 pipeline 時（`pipeline/factory.py`）：每條路徑 `load_transcriber(..., device=s.asr.device, compute_type=s.asr.<role>_compute_type, cpu_threads=…, cuda_library_dirs=s.asr.cuda_library_dirs + [installed_runtime_dir()])`；`cpu_threads` 為 null 時用 `auto_cpu_threads()`（device=cuda 時 0）。
- 地區限制：設定 `translation.allow_restricted` 預設 **false**（引擎不知道使用者所在地，預設不推薦 HY-MT；使用者在設定頁確認不在歐盟／英國／南韓後才打開）。

## 本機實測（2026-11-05，Windows 11、Ryzen 7 5700X、64 GB、RTX 5070 12 GB、驅動 621.01、Ollama 本機）

`detect()` 0.16 s：

```json
{"os": "windows", "os_version": "Windows 11 (10.0.26200)", "machine": "AMD64",
 "cpu": {"model": "AMD Ryzen 7 5700X 8-Core Processor", "physical_cores": 8, "logical_cores": 16, "architecture": "AMD64"},
 "memory": {"total_bytes": 68641931264, "available_bytes": 36176687104},
 "gpus": [{"index": 0, "name": "NVIDIA GeForce RTX 5070", "memory_total_mb": 12227, "memory_used_mb": 5206,
           "driver_version": "621.01", "compute_capability": "12.0"}],
 "apple_silicon": false,
 "cublas": {"supported_platform": true, "available": true, "source": "runtime",
            "library_dirs": ["C:\\Users\\<user>\\AppData\\Local\\kikitori\\gpu-runtime\\cublas-12.8.4.1"]},
 "translation_server": {"base_url": "http://localhost:11434/v1", "is_local": true, "reachable": true,
   "models": ["hf.co/SakuraLLM/Sakura-GalTransl-7B-v3:IQ4_XS", "qwen3.5:9b", "qwen3.5:4b", "hy-mt1.5-7b:latest", "hy-mt1.5-1.8b:latest"], "error": null},
 "errors": []}
```

`recommend()`（cuBLAS 安裝後）：

```json
{"tier": "nvidia_8gb",
 "fast_asr": {"model": "small", "device": "cuda", "compute_type": "float16", "cpu_threads": 0},
 "final_asr": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "float16", "cpu_threads": 0},
 "translation_fast": "qwen3.5:9b", "translation_final": "qwen3.5:9b", "translation_budget_gb": 8.21,
 "gpu_runtime_missing": false,
 "reasons": ["NVIDIA GeForce RTX 5070（11.9 GB VRAM）：即時用 small、完整用 large-v3-turbo（本機實測 0.13 s＋0.15 s）",
             "large-v3-turbo 的 no_speech 不可用，以 small 的 no_speech 與 VAD 輔助過濾",
             "扣掉 ASR 與 1 GB 保留後，約 8.2 GB VRAM 可給翻譯模型",
             "即時與完整翻譯用同一個模型：GPU 上每句都在 0.3 s 內，Ollama 換模型要 6–18 s"],
 "warnings": []}
```

cuBLAS 安裝前同一台機器得到 `tier: "cpu"`（base／small CPU int8、各 4 執行緒）、`gpu_runtime_missing: true`、翻譯 `qwen3.5:9b`（10.94 GB 可用）與警告「cuBLAS 未安裝…」。

GPU 執行環境實裝：下載 567,544,208 bytes 約 60 s、SHA-256 相符，解出 `cublas64_12.dll`、`cublasLt64_12.dll`、`License.txt`；之後 `load_transcriber("small", device="cuda", compute_type="float16", cuda_library_dirs=info.cublas.library_dirs)` 2.1 s 載入（含暖機），2 s 噪音辨識 0.10 s。

## Commands

```text
測試：uv run python -m unittest discover -s tests
只跑本模組：uv run python -m unittest discover -s tests -p "test_hardware_*.py"
Lint：uv run ruff check
```

## 測試策略

`unittest`，不連外網、不執行真的 `nvidia-smi`：

- `test_hardware_system.py`：`/proc/meminfo`、`/proc/cpuinfo`（SMT、雙插槽、ARM）、sysfs topology、`GetLogicalProcessorInformationEx` 緩衝區、`vm_stat`、sysctl（假 reader，含 Rosetta）、讀取失敗回報；Windows 上另跑一次真的 API。
- `test_hardware_nvidia.py`：CSV 解析（單張、多張、`[N/A]`、壞行、名稱含逗號、超大值、超過 64 行）、假 runner（正常、逾時、找不到程式、驅動錯誤、舊驅動重查、垃圾輸出、沒有 GPU）、`nvidia-smi` 位置、cuBLAS 搜尋（設定／執行環境／PATH、分散兩目錄、缺檔、權限錯誤、不支援平台）、系統目錄清單。
- `test_hardware_translation_server.py`：本機 HTTP 伺服器（Ollama 格式、401、金鑰不外洩、壞 JSON、過大、連不上、不合法 URL）。
- `test_hardware_recommend.py`：每個等級、驅動／compute capability、多 GPU、遠端翻譯、Ollama 連不上、模型未安裝、只裝 Sakura、地區限制、覆寫驗證的每條規則。
- `test_hardware_gpu_runtime.py`：本機 HTTP 伺服器提供假的小 wheel（zip）：成功、已安裝不重下、雜湊不符、成員大小不符、缺成員、解壓失敗後重試不重下、不是 zip、中斷續傳、取消、拒絕非 https、壞掉的安裝會重裝。
- `test_hardware_detect.py`：`detect()` 組合與錯誤回報、`hardware_report()` 形狀、`models_overview()`。

## 已知限制與待驗證

- **macOS／Linux 未在實機執行**：sysctl（ctypes `sysctlbyname`）、`vm_stat`、`/proc`、sysfs、Linux 的 cuBLAS 搜尋與 `.so` 預載、`CREATE_NO_WINDOW` 以外的 subprocess 行為只有單元測試。Apple Silicon 與 CPU 等級的延遲、Apple 統一記憶體預算公式都未實測。
- Linux 只搜尋固定目錄與 `LD_LIBRARY_PATH`，不讀 ldconfig 快取；系統裝在其他目錄的 cuBLAS 會被當成沒有（但 CTranslate2 仍可能載入成功）。
- cuBLAS 只檢查檔案存在、不實際載入；版本或架構不符時要到 `load_transcriber()` 才會失敗（`auto` 會退回 CPU）。
- 多 GPU 時以 VRAM 最大的一張估計；若 `CUDA_VISIBLE_DEVICES`／`CUDA_DEVICE_ORDER` 讓 CTranslate2 用到別張，推薦可能不準。
- VRAM 預算用固定的 1 GB 保留，不看偵測當下其他程式的實際用量（`memory.used` 也包含已載入的 Ollama 模型，無法區分）。
- 容器內的 cgroup 記憶體上限不列入 RAM。
- 沒有跨行程鎖：兩個引擎同時安裝 GPU 執行環境可能互相覆寫暫存檔（雜湊與大小驗證會擋下錯誤檔案）。

## 邊界

- **一律**：外部程式與網路都設 timeout；外部輸出逐欄驗證；失敗記 log 並回報（`errors`／例外）；不在 stdout 輸出；不記錄金鑰。
- **先問**：新增依賴（目前沒有新增）；把 NVIDIA 檔案隨安裝檔散布；改變 ASR／翻譯推薦表（應先改 asr.md／translate.md 的依據）。
- **絕不**：未經使用者操作就下載 NVIDIA 檔案；解出 wheel 中未列名的檔案；推薦非商用授權的模型。
