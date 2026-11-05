# Spec: hardware（硬體偵測與模型組合）

> 狀態：已採用（2026-11-05，使用者授權依建議自主實作，不等待審核）。Milestone 12–14（多國語言）：推薦依語言、
> 原因／警告／驗證訊息改為結構化訊息碼（見「訊息碼」）、Ollama 下載翻譯模型（見「Ollama 下載翻譯模型」）。
> Milestone 16（日語直播特化，`docs/specs/vtuber-profile.md`）：8 GB 以上等級改為即時 large-v3-turbo＋完整 large-v3，與本機翻譯
> LLM 共用時依 VRAM 選精度（見「8 GB 以上等級的組合」）；th／ar／hi 即時也用 large-v3；同模型同精度的兩條路徑共用一份權重。
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
- `recommend()` 對每個硬體等級與語言組合給出與 `asr.md`／`translate.md` 一致的組合；原因與警告是結構化訊息
  `{code, params, message}`（見「訊息碼」），UI 依介面語系組句，`message` 是英文備援。
- 有 NVIDIA GPU 但缺 cuBLAS 的使用者按一個按鈕就能啟用 GPU，不必安裝 CUDA Toolkit。

## 對外介面（`src/kikitori/hardware/`）

| 檔案 | 角色 | 主要名稱 |
|---|---|---|
| `types.py` | 凍結 dataclass 與 `to_dict()` | `HardwareInfo`、`CpuInfo`、`MemoryInfo`、`GpuInfo`、`CublasInfo`、`TranslationServerInfo`、`LanguageSelection`、`AsrChoice`、`ModelCombination`、`ValidationResult` |
| `messages.py` | 結構化訊息與訊息碼表（唯一來源） | `Message`、`message()`、`CATALOG` |
| `ollama.py` | Ollama 原生 API：判斷是不是 Ollama、`/api/pull` 下載翻譯模型 | `ollama_version()`、`pull_model()`、`native_root()`、`validate_pull_model_name()`、`OllamaUnreachableError`、`NotOllamaError`、`OllamaAuthError`、`PullError`、`PullCancelledError` |
| `system.py` | OS、CPU、RAM（各平台） | `detect_cpu()`、`detect_memory()`、`detect_apple_silicon()`、解析器 |
| `nvidia.py` | `nvidia-smi` 與 cuBLAS 搜尋 | `query_nvidia_gpus()`、`find_cublas()`、`NvidiaSmiError` |
| `translation_server.py` | `GET {base_url}/models` | `probe_translation_server()`、`is_listed_model_name()`（寬鬆：伺服器列出的名稱，只用來顯示與比對） |
| `detection.py` | 組合上述偵測 | `detect()`、`auto_cpu_threads()`（只偵測 CPU；設定 `asr.*_cpu_threads` 為 null 時用） |
| `recommend.py` | 推薦與覆寫驗證（純函式） | `recommend()`、`validate_combination()`、`cpu_threads_per_model()` |
| `gpu_runtime.py` | cuBLAS wheel 下載安裝 | `install_gpu_runtime()`、`gpu_runtime_status()`、`installed_runtime_dir()`、`GpuRuntimeError` |
| `overview.py` | `models.list` 的總覽 | `models_overview()` |
| `__init__.py` | 對外匯出 | `hardware_report()`（`hardware.detect` 的完整結果） |

```python
def detect(*, base_url="http://localhost:11434/v1", api_key_env="KIKITORI_TRANSLATE_API_KEY",
           cuda_library_dirs: Sequence[Path] = (), runtime_root: Path | None = None,
           nvidia_smi_timeout=5.0, probe_timeout=2.0) -> HardwareInfo
def recommend(info: HardwareInfo, *, allow_restricted: bool = True,
              languages: LanguageSelection | None = None) -> ModelCombination
def validate_combination(data: Mapping[str, Any], info: HardwareInfo, *,
                         downloaded_asr: Collection[str] | None = None,
                         allow_restricted: bool = True,
                         languages: LanguageSelection | None = None) -> ValidationResult
def ollama_version(base_url, *, api_key_env="", timeout=2.0) -> str
def pull_model(base_url, model, *, progress, cancel, api_key_env="",
               connect_timeout=5.0, idle_timeout=120.0) -> None
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
| 共用權重的第二個 worker | 512 MiB（`SHARED_WORKER_EXTRA_MB`） | 兩條路徑同模型同精度時只載入一份權重（見下），第二個 CTranslate2 worker 的解碼暫存保守估計；未實測 |
| 翻譯最低等級 | 4.0 GB（`MIN_TRANSLATION_BUDGET_GB`） | qwen3.5:4b 的等級（`RECOMMENDATION_TIERS`）；更低只剩 HY-MT 1.8B，translate_eval 中每個資料集都顯著較差且會幻覺 |
| 最低驅動 | 525 | NVIDIA CUDA minor version compatibility：CUDA 12.x 需要 ≥ 525 |
| CPU 高 RAM 門檻 | 15 GiB | 標稱 16 GB 扣掉內顯共用後常為 15.x GiB |
| CPU 低核心 | 實體核心 ≤ 4 | asr.md |
| Apple GPU 比例 | 統一記憶體 × 0.65，且不超過「總量 − ASR − 4 GiB」 | macOS 預設讓 GPU 使用約 2/3 記憶體 |

- **主 GPU**：VRAM 最大的一張（CUDA 預設裝置順序為 FASTEST_FIRST，CTranslate2 用裝置 0，通常就是它）；多於一張時加警告 `multiple_gpus`。
- **GPU 能否跑 ASR**：平台支援（Windows／Linux x86-64）、驅動 ≥ 525、compute capability ≥ 7.0（float16／int8）或剛好 6.1（只支援 int8，CTranslate2 quantization 文件）；其他情況 ASR 改 CPU 並加警告（`cuda_*`）。可以跑但缺 cuBLAS → ASR 改 CPU、`gpu_runtime_missing = True`、警告 `gpu_runtime_missing`（UI 顯示下載按鈕；安裝後重新 `detect()` 就會得到 GPU 組合）。

### 語言（Milestone 12）

`languages: LanguageSelection`（`mode`、`source_language`、`target_language`、`partner_language`，皆為語言清單的語言碼，
收聽語言可為 `"auto"`）；引擎由設定組出（`engine/hardware_service.py` 的 `language_selection()`，模式取設定的 `mode`）。
`languages=None`（舊呼叫端）時不看語言：ASR 只選多語言模型、翻譯模型不檢查語言組合。

- **辨識語言**（`spoken_languages`）：影片翻譯為收聽語言（`auto` 時為空＝任何語言）；口譯為（我的語言, 對方語言）。
- **ASR 仍依硬體等級挑**：每個等級的即時／完整模型是 `ASR_CANDIDATES` 的候選清單，第一個「支援全部辨識語言」的模型
  （asr registry 的 `ModelSpec.supports()`）勝出；最後一個候選一定是多語言模型。所以只支援單一語言的模型（例如之後加入的
  日語特化模型）放在多語言模型前面時，只在辨識語言全是它支援的語言時才被推薦；`auto` 要求支援全部 19 個 Whisper 語言。
- **品質提示**：辨識語言用推薦的**完整**模型是 C 級（`languages/catalog.py` 的 `asr_quality`）→ 每個語言一則警告
  `asr_language_quality_low {model, language}`。`auto` 時不提示（不知道會聽到什麼語言）。不換模型：品質等級只是提示，
  硬體放不下更大的模型。
- **翻譯方向**（`translation_pairs`）：影片翻譯為（收聽語言 → 字幕語言）；兩者是同一個 Whisper 語言（例如 zh-CN → zh-TW）
  時為空——不需要翻譯模型；`auto` 為（清單中其他每個 Whisper 語言 → 字幕語言）。口譯為兩個方向。

### ASR 品質等級（`languages/catalog.py` 的 `asr_quality`）

出處：`research_notes/VTuber 直播模型選型調查/language_coverage.md`。A ≤ 10%、B 10–20%、C > 20%（WER；中日泰為 CER）。

| 模型 | 依據 | C 級的語言 |
|---|---|---|
| large-v3 | OpenAI README 的 FLEURS 與 Common Voice 15，取較差者 | vi（CV15 39.8） |
| large-v3-turbo | turbo 公告圖，同上 | ar、hi、vi、th |
| medium | 論文 Table 13（FLEURS）與 large-v3 等級取較差者 | ar、hi、vi |
| small | 論文 Table 13 的推論分級 | zh-TW、zh-CN、ar、hi、vi、th |
| base | 同上 | 除 en、es（A）與 de、pt、it（B）外全部 |
| tiny | 論文 Table 13 | 除 en、es（B）外全部 |

沒有資料的模型（之後新增的 registry 模型）回傳 None、不提示；`tests/test_hardware_recommend.py` 檢查 registry 的每個模型都有資料。

### ASR 等級（與 asr.md 一致）

| tier | 條件 | 即時 | 完整 |
|---|---|---|---|
| `nvidia_8gb` | VRAM ≥ 7680 MiB | `large-v3-turbo`（float16 或 int8_float16；VRAM 不夠給 4B 時 `small`） | `large-v3`（float16 或 int8_float16），見下節 |
| `nvidia_4gb` | 3584 ≤ VRAM < 7680 | `base` cuda int8_float16 | `large-v3-turbo` cuda int8_float16 |
| `nvidia_lt4gb` | VRAM < 3584 | `base` cuda int8_float16 | `small` cuda int8_float16 |
| `cpu` | 無可用 GPU，RAM ≥ 15 GiB 且實體核心 > 4 | `base` cpu int8 | `small` cpu int8 |
| `cpu_low` | 無可用 GPU，RAM < 15 GiB、核心 ≤ 4 或 RAM 未知 | `tiny` cpu int8 | `base` cpu int8 |
| `apple_silicon` | macOS Apple Silicon | `base` cpu int8 | `small` cpu int8（未實測） |

- compute capability 6.1（Pascal）時 GPU 等級的精度一律 `int8`。
- ASR 需要的 VRAM（`asr_vram_need_mb()`：registry 數值，fp16 用 `approx_gpu_vram_mb`、int8 類用 `approx_gpu_vram_int8_mb`，
  兩個模型扣一次 context；**模型與精度都相同時只算一份權重＋`SHARED_WORKER_EXTRA_MB`**）大於「VRAM − 1024」時改用 CPU 等級並警告。
  `validate_combination()` 的 `vram_overflow` 也用同一個算法。

### 8 GB 以上等級的組合（Milestone 16）

依據 `experiments/vtuber_eval/README.md`：即時 large-v3-turbo 的口語 CER 比 small 低 5.6 點（95% CI [2.9, 8.2]）；完整路徑在
BGM 10／5 dB 時 large-v3 比 turbo 好 4.1／5.3 點，乾淨語音打平；large-v3 的 int8_float16 與 float16 品質相同（Δ −0.4 [−1.0, 0.3]）。
所以候選（`GPU_LADDERS`）依品質分組，組內只差精度、越前面延遲越低（float16 較快）、VRAM 越多：

| 組 | 候選（即時／完整） | ASR VRAM（MiB） |
|---|---|---|
| 1 | turbo f16 ／ v3 f16 | 2300 + 4170 − 500 = **5970** |
| 1 | turbo f16 ／ v3 int8_float16 | 2300 + 2140 − 500 = **3940** |
| 1 | turbo int8_float16 ／ v3 int8_float16 | 1240 + 2140 − 500 = **2880** |
| 2（退路） | small int8_float16 ／ v3 int8_float16 | 650 + 2140 − 500 = **2290** |

挑法（`_choose_option()`）：

- **翻譯不在這張卡上**（遠端伺服器，或收聽與字幕語言是同一個 Whisper 語言、不需要翻譯模型）：第一個放得下的（VRAM − 1024 ≥ ASR）。
  8 GB 等級一律是 turbo f16 ／ v3 f16。
- **與本機翻譯 LLM 共用**：翻譯預算 = VRAM − 1024 − ASR（GB，取到小數 2 位），等級依 `RECOMMENDATION_TIERS`（≥ 7.5 GB 9B、
  ≥ 4.0 GB 4B、其他 HY-MT 1.8B）。依組的順序：組內放得下的候選中，「翻譯等級最高」的就是組內最省 VRAM 的那個能到的等級；
  若該等級 ≥ 4.0 GB，挑組內**第一個**達到該等級的（精度最高、延遲最低）。否則看下一組。都不到 4.0 GB 時用最省 VRAM 的候選。
  int8_float16 品質相同，所以「翻譯模型升一級」永遠優先於「ASR 用 float16」；但不會為了升級翻譯而把即時模型換成 small
  （第 2 組只在第 1 組連 4B 都放不下時才用）。
- **語言**：辨識語言用即時模型是 C 級、完整模型不是（`asr_quality`；large-v3-turbo 的 th、ar、hi）時，即時也用完整模型、同一個
  精度（候選變成 v3／v3，只算一份權重：f16 4170 + 512 = **4682**、int8_float16 2140 + 512 = **2652**），原因 `asr_live_model_for_language`。
  vi 在兩個模型都是 C，不換（只警告）。`auto` 不換。
- 4 GB 以下的等級（`nvidia_4gb`、`nvidia_lt4gb`）沿用先前的組合：放不下兩個大模型再加上 4B。

各種顯示卡（`recommend()` 實際輸出；翻譯預算＝VRAM − 1024 − ASR）：

| VRAM（MiB） | 翻譯在本機 | 即時／完整 | ASR | 翻譯預算 | 翻譯模型 |
|---|---|---|---|---|---|
| 24564（24 GB） | 是 | turbo f16 ／ v3 f16 | 5970 | 17.16 GB | qwen3.5:9b |
| 16303（16 GB） | 是 | turbo f16 ／ v3 f16 | 5970 | 9.09 GB | qwen3.5:9b |
| 12227（12 GB，RTX 5070） | 是 | turbo int8 ／ v3 int8（f16／int8 只剩 7.09 GB，放不下 9B） | 2880 | 8.13 GB | qwen3.5:9b |
| 11264（11 GB） | 是 | turbo f16 ／ v3 f16（全 int8 也只有 7.19 GB，仍是 4B） | 5970 | 4.17 GB | qwen3.5:4b |
| 10240（10 GB） | 是 | turbo f16 ／ v3 int8（兩個 f16 只剩 3.17 GB） | 3940 | 5.15 GB | qwen3.5:4b |
| 8188（8 GB） | 是 | turbo int8 ／ v3 int8 | 2880 | 4.18 GB | qwen3.5:4b |
| 7680（8 GB 等級下限） | 是 | **small** int8 ／ v3 int8（turbo int8 只剩 3.69 GB） | 2290 | 4.26 GB | qwen3.5:4b |
| 7680–24564 | 否（遠端或不需翻譯） | turbo f16 ／ v3 f16 | 5970 | — | — |
| 6144（6 GB） | 是／否 | base int8 ／ turbo int8（沿用） | 1190 | 3.84 GB | hy-mt1.5-1.8b |
| 4096（4 GB） | 是／否 | base int8 ／ turbo int8（沿用） | 1190 | 1.84 GB | hy-mt1.5-1.8b（超過預算警告） |
| 2048 | 是／否 | base int8 ／ small int8（沿用） | 600 | 0.41 GB | hy-mt1.5-1.8b |
| 12227，收聽 th／ar／hi | 是 | v3 int8 ／ v3 int8（共用權重） | 2652 | 8.35 GB | qwen3.5:9b |
| 16303，收聽 th／ar／hi | 是 | v3 f16 ／ v3 f16（共用權重） | 4682 | 10.35 GB | qwen3.5:9b |
| 7680，收聽 th／ar／hi | 是 | v3 int8 ／ v3 int8（共用權重） | 2652 | 3.91 GB | hy-mt1.5-1.8b |

（int8 ＝ int8_float16；Pascal 6.1 一律 int8：8192 MiB 時 turbo int8 ／ v3 int8、qwen3.5:4b。）LLM 的 VRAM 用預設組的
`approx_vram_gb`（權重，9B 6.6 GB、4B 3.3 GB），門檻 7.5／4.0 GB 已含 KV cache 的餘裕；`tests/test_hardware_recommend.py`
檢查 7680–24576 MiB 每 256 MiB 的推薦都滿足「ASR＋翻譯權重＋1024 ≤ VRAM」、翻譯預算 ≥ 4.0 GB，且 `validate_combination()` 不報
`vram_overflow`。實際 VRAM 由端到端驗收量測（`vtuber-profile.md`「驗證」）。

### 兩條路徑共用一份權重

即時與完整路徑的模型、精度、CPU 執行緒數都相同時（目前只有 th／ar／hi 的推薦，或使用者手動這樣設定），`pipeline.factory` 只載入
一次（`load_transcriber(workers=2)`，CTranslate2 `inter_threads=2`），完整路徑用 `WhisperTranscriber.with_options()` 換成自己的
解碼參數與 lock。依據：CTranslate2 官方文件 *Parallel execution*：「When the workers are running on the same device, the model
weights are shared to save on memory」，計算方法釋放 GIL、可從多個 Python 執行緒呼叫；原始碼 `ModelLoader::load()` 中同一裝置的
replica 都指向同一個 `Model`；`encode()` 回傳前會 `synchronize_stream`，所以一個 worker 的編碼結果交給另一個 worker 解碼是安全的
（faster-whisper 的 `num_workers` 也是同樣用法）。worker 數等於共用的路徑數，兩條路徑才會真的並行、不在同一個 worker 上排隊。
- CPU 執行緒：即時與完整同時執行，各用 `min(8, 實體核心 // 2)`（asr.md：兩者合計不超過實體核心數）；核心數未知時 0（CTranslate2 預設）。GPU 時 0。

### 翻譯模型

- 翻譯伺服器不在本機 → `translation_fast/final = None`、`translation_status = "remote"`、原因 `translation_remote`（在設定頁選該服務的模型）。不檢查語言。
- 可給翻譯的 VRAM：有 NVIDIA GPU 時 = 主 GPU VRAM − 1024 − ASR 在 GPU 上的用量（ASR 在 CPU 時 Ollama 仍可用整張卡，Ollama 自帶 CUDA runtime）；Apple Silicon 用上表公式；其他 → None（CPU）。
- 翻譯方向為空（同一個 Whisper 語言）→ `translation_status = "not_needed"`、模型為 None、原因 `translation_not_needed`（不檢查安裝、不提示下載）。
- 呼叫 `translate.recommend_translation_models(budget, installed, allow_restricted, pairs)`（非商用的 Sakura 一律不推薦；`allow_restricted=False` 排除 HY-MT）：在這個 VRAM 等級的推薦順序中，略過不支援全部方向的模型（`translate.supports()`），挑第一個。`installed` 為伺服器回報的模型（無法取得時 None）。等級內沒有任何模型支援 → `translation_status = "unsupported"`、模型為 None、警告 `translation_unsupported {source_language, target_language}`（第一個沒有任何可推薦模型支援的方向；`auto` 時 `source_language` 為 `"auto"`）。目前的預設組（Qwen3.5、HY-MT1.5）都支援 20 種語言，實際上只有之後加入特化模型時才會發生。
- 收聽語言為 `auto` → 原因 `source_language_auto`（翻譯模型依「任何語言 → 字幕語言」挑選）。
- 另以 `installed=None` 算出「理想」選擇：實際選擇不同時加原因 `translation_better_model_available`；選中的模型沒裝 → 警告 `translation_model_not_installed`（Ollama 時 UI 可用 `models.pull_translation` 下載）。伺服器連不上 → 警告 `translation_server_unreachable`。模型權重大於可用 VRAM → 警告 `translation_model_exceeds_budget`。CPU 推論且 RAM < 8 GiB → 警告 `low_ram_for_translation`。
- `ModelCombination` 另有 `translation_status`（`recommended`／`remote`／`not_needed`／`unsupported`）與 `languages`（推薦依據的語言選擇，`to_dict()` 為物件或 null）。

## 手動覆寫（`validate_combination()`）

輸入與 `ModelCombination.to_dict()` 同形狀（`device` 另可為 `auto`、`compute_type` 可為 null）。純函式，格式錯誤也以 `errors` 回報。
`errors`／`warnings` 都是結構化訊息（code 見「訊息碼」的「組合驗證」）。帶 `languages` 時另檢查：ASR 模型不支援辨識語言
（`asr_model_language_unsupported`）、完整模型對辨識語言是 C 級（`asr_language_quality_low`）、已知的翻譯模型不支援語言組合
（`translation_model_language_unsupported`；不認得的模型不檢查）。

| 類別 | 檢查 |
|---|---|
| errors | 不是物件；未知的 ASR id；`device` 不是 auto／cpu／cuda；`compute_type` 不是 CTranslate2 認得的值；`cpu_threads` 不是 0–256 整數；選 cuda 但沒有 NVIDIA GPU、GPU 不支援（平台、驅動、compute capability）或缺 cuBLAS；翻譯模型名稱不是 1–200 字的可列印字串 |
| warnings | `auto` 時 GPU 不可用而會用 CPU；CPU 上用 float16 等 CPU 不支援的精度（CTranslate2 會自動轉型）；compute capability < 7.0 用 float16／int8_float16；CPU 上用 medium 以上；ASR 模型未下載；即時模型比完整模型大；兩條路徑都是 no_speech 不可用的模型；ASR + 翻譯合計超過「VRAM − 1024」；CPU 上 ASR 超過一半 RAM；翻譯模型不在伺服器上；非商用授權；地區限制（`allow_restricted=False`）；本機伺服器上即時與完整翻譯用不同模型（換模型 1.5–18 s） |

## 訊息碼

`recommend()` 的 `reasons`／`warnings`、`validate_combination()` 的 `errors`／`warnings`，以及引擎
`settings.preview_languages` 的 `messages`，一律是結構化訊息：

```jsonc
{"code": "translation_model_not_installed", "params": {"model": "qwen3.5:9b"},
 "message": "The recommended translation model qwen3.5:9b is not installed yet"}
```

- `code`：穩定的識別碼，UI 依介面語系查字典組句；**既有 code 的意義與參數不改**，要改就新增 code。
- `params`：只有基本型別（str／int／float），單位寫在名稱裡（`vram_mb`、`need_mb` 為 MiB 整數；`ram_gb`、`budget_gb`、
  `model_gb` 為 GB 浮點數）。`path` 為 `"fast_asr"`（即時路徑）或 `"final_asr"`（完整路徑）；`field` 為 `"translation_fast"`
  或 `"translation_final"`；`language`、`source_language`、`target_language` 為語言清單的語言碼（`source_language` 可為
  `"auto"`），UI 以介面語系顯示語言名稱；`model`、`better_model`、`fast_model`、`final_model` 為模型名稱（不翻譯）。
- `message`：英文備援（下表的範本套上參數；浮點數依範本格式化），給紀錄檔、CLI 與 UI 不認得的 code。
- 唯一來源是 `src/kikitori/hardware/messages.py` 的 `CATALOG`；`tests/test_hardware_messages.py` 檢查下表與它逐字一致
  （code、參數名稱與順序、英文範本）。

**推薦：ASR（`reasons`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `asr_gpu` | `gpu_name` str, `vram_mb` int, `fast_model` str, `final_model` str, `compute_type` str | {gpu_name} ({vram_mb} MiB VRAM): {fast_model} for live subtitles, {final_model} for final subtitles ({compute_type}) | 推薦 GPU 等級的 ASR：顯示卡、即時／完整模型與精度（兩條路徑精度不同時 `compute_type` 是「即時 / 完整」，例如 `float16 / int8_float16`） |
| `asr_gpu_int8_saves_vram` | — | int8_float16 halves the VRAM used by speech recognition, leaving more for the translation model | 有路徑用 int8_float16（Pascal 除外）：省 VRAM 給翻譯模型；8 GB 以上等級品質相同（vtuber_eval） |
| `asr_final_no_speech_unreliable` | `final_model` str, `fast_model` str | {final_model} cannot tell speech from silence (no_speech); {fast_model}'s no_speech and the voice detector filter non-speech instead | 完整模型（large-v3-turbo）的 no_speech 不可用，改用即時模型的值與 VAD 過濾 |
| `asr_bgm_robust_final` | `final_model` str | {final_model} for final subtitles holds up best against background music and game sound in livestreams (kikitori's Japanese livestream evaluation) | 8 GB 以上等級的完整路徑用 large-v3 的理由（Milestone 16，vtuber_eval：BGM 下退化最小） |
| `asr_live_model_for_language` | `language` str, `fast_model` str, `model` str | {fast_model} is much less accurate for {language}, so live subtitles also use {model} (loaded once and shared by both) | 即時模型在辨識語言是 C 級、完整模型較好（large-v3-turbo 的 th、ar、hi）：即時也用完整模型，兩條路徑共用一份權重；每個語言一則 |
| `asr_large_v3_remote_translation` | `final_model` str | Translation does not run on this computer, so final subtitles use {final_model} (similar quality, reliable no_speech, about 0.33 s per sentence) | 翻譯不在這張卡上（遠端伺服器或不需要翻譯）：8 GB 以上等級兩條路徑都用 float16（Milestone 16 起本機翻譯時完整路徑也是 large-v3，只是可能用 int8_float16） |
| `asr_apple_silicon` | `fast_model` str, `final_model` str | Apple Silicon: speech recognition runs on the CPU on macOS; {fast_model} for live subtitles, {final_model} for final subtitles | Apple Silicon：ASR 在 CPU |
| `asr_apple_silicon_untested` | — | Apple Silicon latency has not been measured yet; using the same models as a CPU with 16 GB of RAM or more | Apple Silicon 延遲未實測，比照 CPU ≥ 16 GB |
| `asr_cpu` | `fast_model` str, `final_model` str | Speech recognition runs on the CPU (16 GB of RAM or more): {fast_model} for live subtitles, {final_model} for final subtitles | 沒有可用 GPU、RAM ≥ 16 GB：CPU 等級 |
| `asr_cpu_large_models_slow` | — | medium and larger models need 3.6 s or more per sentence on a CPU, too slow for live subtitles | medium 以上在 CPU 太慢 |
| `asr_cpu_low` | `fast_model` str, `final_model` str | Lightweight models keep latency and memory low: {fast_model} for live subtitles, {final_model} for final subtitles | 低階 CPU 等級的模型（前面另有 `cpu_low_ram`／`cpu_few_cores`／`cpu_info_incomplete` 說明原因） |
| `cpu_low_ram` | `ram_gb` float | {ram_gb:.1f} GB of RAM is less than 16 GB | 低階 CPU 等級的原因：RAM 不足 16 GB |
| `cpu_few_cores` | `physical_cores` int, `max_cores` int | {physical_cores} physical CPU cores (at most {max_cores}) | 低階 CPU 等級的原因：實體核心太少 |
| `cpu_info_incomplete` | — | The hardware information is incomplete | 低階 CPU 等級的原因：硬體資訊不完整 |

**推薦：ASR（`warnings`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `multiple_gpus` | `gpu_count` int, `gpu_name` str | {gpu_count} NVIDIA GPUs found; using {gpu_name}, the one with the most VRAM | 多張 GPU，以 VRAM 最大的計算 |
| `cuda_platform_unsupported` | — | CTranslate2 has no CUDA build for this platform (Windows and Linux x86-64 only); speech recognition uses the CPU | 平台沒有 CTranslate2 CUDA（macOS、ARM Linux），ASR 用 CPU |
| `cuda_driver_too_old` | `driver_version` str, `min_driver` int | NVIDIA driver {driver_version} is too old (CUDA 12 needs {min_driver} or newer); speech recognition uses the CPU until the driver is updated | NVIDIA 驅動太舊，ASR 用 CPU |
| `cuda_compute_capability_unknown` | — | Cannot read the GPU's compute capability (the driver may be too old); speech recognition uses the CPU | 讀不到 compute capability，ASR 用 CPU |
| `cuda_compute_capability_unsupported` | `gpu_name` str, `compute_capability` str | {gpu_name} (compute capability {compute_capability}) does not support CTranslate2's float16/int8 acceleration; speech recognition uses the CPU | GPU 太舊（< 7.0 且不是 6.1），ASR 用 CPU |
| `gpu_runtime_missing` | — | cuBLAS is not installed: GPU acceleration needs the GPU runtime download (about 570 MB); until then speech recognition uses the CPU | 缺 cuBLAS：UI 顯示「下載 GPU 執行環境」按鈕（`gpu_runtime_missing=true`） |
| `asr_vram_insufficient` | `need_mb` int | Not enough VRAM for the speech recognition models (about {need_mb} MiB needed); speech recognition uses the CPU | VRAM 放不下 ASR 模型，改用 CPU |
| `memory_unknown` | — | Cannot read the amount of memory; using the lightest speech recognition models | 讀不到 RAM 大小，用最輕量的組合 |
| `asr_low_quality_model` | `model` str | {model} makes noticeably more recognition errors | 低階組合的即時模型（tiny）錯字明顯較多 |
| `asr_language_quality_low` | `model` str, `language` str | {model} recognizes {language} poorly (error rate above 20% on public benchmarks) | 辨識語言用這個模型是 C 級（錯誤率 > 20%）；每個語言一則 |

**推薦：翻譯（`reasons`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `translation_remote` | `base_url` str | The translation server is not on this computer ({base_url}); choose one of its models in Settings | 翻譯伺服器不在本機：不推薦翻譯模型（`translation_status=remote`） |
| `translation_cpu` | — | No usable GPU: the translation model runs on the CPU, so the smallest model is chosen (CPU speed not measured) | 沒有可用 GPU：翻譯模型在 CPU，選最小的 |
| `translation_budget_vram` | `budget_gb` float | About {budget_gb:.1f} GB of VRAM is left for the translation model after speech recognition and a 1 GB reserve | 扣掉 ASR 與 1 GB 保留後可給翻譯模型的 VRAM |
| `translation_budget_unified` | `budget_gb` float | About {budget_gb:.1f} GB of unified memory is left for the translation model after speech recognition and the system reserve | Apple Silicon：可給翻譯模型的統一記憶體 |
| `translation_same_model` | — | Live and final translation use the same model: each sentence takes under 0.3 s on a GPU, while switching models in Ollama takes 1.5-18 s | 即時與完整翻譯用同一個模型的理由 |
| `translation_different_models` | — | Live and final translation use different models | 即時與完整翻譯用不同模型 |
| `translation_better_model_available` | `model` str, `better_model` str | Chose {model} from the installed models; installing {better_model} gives better quality | 已安裝的模型中選了 `model`；安裝 `better_model` 品質較好 |
| `translation_not_needed` | `source_language` str, `target_language` str | {source_language} and {target_language} are the same spoken language: no translation model is needed, only script conversion | 收聽與字幕是同一個 Whisper 語言：不需要翻譯模型（`translation_status=not_needed`） |
| `source_language_auto` | `target_language` str | The listening language is detected automatically: the translation model is chosen to translate any language into {target_language} | 收聽語言自動偵測：翻譯模型依「任何語言 → 字幕語言」挑選 |

**推薦：翻譯（`warnings`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `translation_server_unreachable` | `base_url` str | Cannot connect to the translation server {base_url} (is Ollama running?); installed models cannot be checked | 連不上翻譯伺服器（Ollama 沒開？） |
| `translation_model_list_unavailable` | `error` str | Cannot get the translation server's model list: {error} | 伺服器有回應但拿不到模型清單（`error` 為英文診斷） |
| `low_ram_for_translation` | — | Less than 8 GB of RAM: running the translation model and speech recognition together may not fit | RAM < 8 GB，翻譯模型與 ASR 可能放不下 |
| `translation_model_not_installed` | `model` str | The recommended translation model {model} is not installed yet | 推薦的翻譯模型還沒裝（Ollama 可用 `models.pull_translation` 下載） |
| `translation_model_exceeds_budget` | `model` str, `model_gb` float, `budget_gb` float | {model} needs about {model_gb:.1f} GB, more than the {budget_gb:.1f} GB available; Ollama will run part of it on the CPU, which is slower | 模型權重大於可用 VRAM，Ollama 會部分用 CPU |
| `translation_unsupported` | `source_language` str, `target_language` str | No recommended translation model supports {source_language} -> {target_language} | 這個 VRAM 等級沒有推薦模型支援這個語言組合（`translation_status=unsupported`）；`source_language` 可能是 "auto" |

**組合驗證（`errors`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `combination_not_object` | — | The model combination must be an object | `models.validate` 的組合不是物件 |
| `asr_path_not_object` | `path` str | {path} must be an object | `fast_asr`／`final_asr` 不是物件 |
| `asr_model_unknown` | `path` str, `model` str, `available` str | {path}: unknown speech recognition model {model} (available: {available}) | 未知的 ASR 模型（`available` 為逗號分隔的 id） |
| `asr_device_invalid` | `path` str | {path}: device must be auto, cpu or cuda | `device` 不是 auto／cpu／cuda |
| `asr_compute_type_not_string` | `path` str | {path}: compute_type must be a string or null | `compute_type` 不是字串或 null |
| `asr_cpu_threads_invalid` | `path` str | {path}: cpu_threads must be an integer from 0 to 256 | `cpu_threads` 不是 0–256 的整數 |
| `asr_cuda_no_gpu` | `path` str | {path}: CUDA is selected but no NVIDIA GPU was found | 選了 CUDA 但沒有 NVIDIA GPU |
| `asr_cuda_platform_unsupported` | `path` str | {path}: CUDA is selected but CTranslate2 has no CUDA build for this platform (Windows and Linux x86-64 only) | 選了 CUDA 但平台不支援（同 `cuda_platform_unsupported` 加 `path`） |
| `asr_cuda_driver_too_old` | `path` str, `driver_version` str, `min_driver` int | {path}: CUDA is selected but NVIDIA driver {driver_version} is too old (CUDA 12 needs {min_driver} or newer) | 選了 CUDA 但驅動太舊 |
| `asr_cuda_compute_capability_unknown` | `path` str | {path}: CUDA is selected but the GPU's compute capability is unknown (the driver may be too old) | 選了 CUDA 但讀不到 compute capability |
| `asr_cuda_compute_capability_unsupported` | `path` str, `gpu_name` str, `compute_capability` str | {path}: CUDA is selected but {gpu_name} (compute capability {compute_capability}) does not support CTranslate2's float16/int8 acceleration | 選了 CUDA 但 GPU 太舊 |
| `asr_cuda_cublas_missing` | `path` str | {path}: CUDA is selected but cuBLAS is not installed; download the GPU runtime first | 選了 CUDA 但缺 cuBLAS |
| `asr_compute_type_unknown` | `path` str, `compute_type` str, `available` str | {path}: unknown compute type {compute_type} (available: {available}) | CTranslate2 不認得的精度 |
| `translation_model_name_invalid` | `field` str, `max_chars` int | {field} must be a model name of 1-{max_chars} characters or null | 翻譯模型名稱不是 1–200 字的可列印字串 |

**組合驗證（`warnings`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `asr_auto_uses_cpu` | `path` str | {path}: auto will use the CPU (the GPU cannot run speech recognition now) | `device=auto` 時實際會用 CPU |
| `asr_compute_type_cpu_fallback` | `path` str, `compute_type` str | {path}: the CPU does not support {compute_type}; CTranslate2 will use another type (usually float32, slower) | CPU 不支援這個精度，CTranslate2 自動改用其他精度 |
| `asr_compute_type_gpu_fallback` | `path` str, `compute_type` str, `compute_capability` str | {path}: compute capability {compute_capability} does not support {compute_type}; CTranslate2 will use float32 or int8_float32 (slower, more VRAM) | GPU 不支援這個精度（compute capability < 7.0） |
| `asr_model_slow_on_cpu` | `path` str, `model` str | {path}: {model} needs 3.6 s or more per sentence on a CPU; subtitles will lag noticeably | medium 以上在 CPU 每句 3.6 s 以上 |
| `asr_model_not_downloaded` | `path` str, `model` str | {path}: speech recognition model {model} is not downloaded yet; it is downloaded before starting | ASR 模型尚未下載 |
| `asr_model_language_unsupported` | `path` str, `model` str, `language` str | {path}: {model} cannot recognize {language} | 只支援特定語言的 ASR 模型不支援這個辨識語言 |
| `asr_fast_larger_than_final` | `fast_model` str, `final_model` str | The live model {fast_model} is larger than the final model {final_model}: live subtitles may be slower than final ones | 即時模型比完整模型大 |
| `asr_no_speech_unavailable` | `fast_model` str, `final_model` str | Neither {fast_model} nor {final_model} can filter non-speech with no_speech; singing and noise are more likely to be transcribed | 兩條路徑的模型都不能用 no_speech 過濾 |
| `translation_model_not_on_server` | `model` str | The translation server does not have {model} | 翻譯伺服器上沒有這個模型 |
| `translation_model_language_unsupported` | `model` str, `source_language` str, `target_language` str | {model} does not support {source_language} -> {target_language} | 選的翻譯模型（已知的預設組）不支援這個語言組合 |
| `translation_license_non_commercial` | `model` str, `license` str | The license of {model} ({license}) forbids commercial use | 授權禁止商業用途（例如 Sakura） |
| `translation_license_region_restricted` | `model` str, `license` str, `regions` str | The license of {model} ({license}) does not apply in {regions} | 授權不適用於這些地區（`regions` 為逗號分隔的 EU、UK、KR） |
| `translation_models_differ` | — | Live and final translation use different models: the local Ollama takes 1.5-18 s each time it switches models | 本機即時與完整翻譯用不同模型（換模型很慢） |
| `vram_overflow` | `need_mb` int, `available_mb` int, `gpu_name` str | Speech recognition and translation need about {need_mb} MiB of VRAM, more than the {available_mb} MiB available on {gpu_name}; they may be slow or fail to load | ASR 與翻譯模型合計超過可用 VRAM |
| `asr_ram_over_half` | — | The speech recognition models on the CPU use more than half of the RAM | CPU 上的 ASR 模型用掉超過一半 RAM |

**語言預覽（`messages`）**

| code | params | English（`message`） | 意義 |
|---|---|---|---|
| `translation_disabled` | — | Translation is off: only the original text is shown and no translation model is needed | 只顯示原文：不需要翻譯模型（`settings.preview_languages` 的 `messages`） |

翻譯預設組的 `label` 不再帶任何語言的說明（例如 Sakura 是 `Sakura-GalTransl 7B v3`）；「非商用」等標示由 UI 依
`commercial_use`／`excluded_regions` 以介面語系顯示。

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

## Ollama 下載翻譯模型（`ollama.py`，Milestone 13）

翻譯伺服器是 Ollama 時，引擎可以替使用者下載翻譯模型（`models.pull_translation`）；其他 OpenAI 相容伺服器只能提示使用者
自行安裝。全部用標準庫 `http.client`（不經 proxy；`localhost` 依序試 127.0.0.1、::1，理由同翻譯伺服器探測）。
協定以官方文件（docs.ollama.com/api 的 pull、version、errors；2026-11-05 查閱）與本機 Ollama 0.40.14 的實測為準（見下方「實測」）。

- **原生 API 的位置**（`native_root()`）：設定存的是 OpenAI 相容端點，例如 `http://host:11434/v1`；去掉路徑結尾的 `/v1`
  就是 Ollama 原生 API 的根（反向代理的前綴保留，例如 `http://h/ollama/v1` → `/ollama/api/...`）。連接埠 0 或不合法 → `ValueError`。
- **網址中的帳密**：`http://user:pass@host/...` 的 userinfo 不使用（金鑰只從環境變數讀），而且在所有錯誤訊息、log 與回應中遮蔽成
  `***@`（`kikitori.translate.redact_url()`；`probe_translation_server()` 回報的 `base_url`、推薦理由、引擎的錯誤訊息都經過它）。
- **是不是 Ollama**（`ollama_version()`）：`GET {root}/api/version`，連線、送出請求與讀完整個回應各最多 2 s（回應的讀取是整體時限，
  不是每次 recv，伺服器一點一點送也不會拖延）。官方回應是 `{"version": "0.5.1"}`。
  - 連不上，或回應沒在時限內讀完 → `OllamaUnreachableError`（標頭到了、內容讀不完時訊息寫明「response body」）。
  - **401／403 → `OllamaAuthError`**：要求金鑰的反向代理後面可能就是 Ollama，只是無法確認；不歸類為「不是 Ollama」，
    UI 才能請使用者檢查 `translation.api_key_env`，而不是叫他自行安裝模型。
  - **3xx → `NotOllamaError`，訊息寫明轉址與目標**（目標網址也遮蔽帳密）：不跟隨轉址——轉到別的主機會把 `Authorization` 帶過去，
    而 Ollama 本身從不轉址，出現就是設定錯了（例如 http 被代理轉到 https），請使用者改成最終網址。
  - 其他狀態碼、不是 JSON、沒有 1–50 字的可列印 `version` 字串 → `NotOllamaError`（例如 LM Studio、vLLM 回 404）。只有 200 才讀內容。
- **下載**（`pull_model()`）：`POST {root}/api/pull`，body `{"model": <名稱>, "stream": true}`。回應是 NDJSON（chunked），
  每行一個物件：`{"status": "pulling manifest"}`、`{"status": "pulling <digest>", "digest": "sha256:<64 hex>", "total": N,
  "completed": M}`、`{"status": "verifying sha256 digest"}`、`{"status": "writing manifest"}`、`{"status": "success"}`，或
  `{"error": "<訊息>"}`。
  - 模型名稱（`validate_pull_model_name()`）：1–200 字，只允許英數與 `._:/-`、第一個字元是英數、不得含 `..`，整個字串都要符合
    （`re.fullmatch`：結尾的換行也不行）。比伺服器列出的名稱（`translation_server.is_listed_model_name()`：任何可列印字元，只用來
    顯示與比對「已安裝」）嚴格，因為這個名稱會轉送給伺服器。
  - 每行最多 64 KiB、必須是 JSON 物件；`status` 必須是 ≤ 200 字的字串；`error` 必須是字串（截斷 300 字、只留可列印字元後
    放進 `PullError`）。進度欄位逐一驗證：`digest` 符合 `sha256:` 格式、`total` 為 0–10¹³ 的整數、`completed` 為非負整數
    （大於 total 時以 total 計）；不合法的進度行略過（不中斷）。不同的層最多 512 個（實際模型只有數層），超過 → `PullError`。
  - 進度：各層（digest）的 completed／total 加總後回報 `(completed, total)`，只在改變時回報；新的層出現時 total 會變大。
  - 收到 `success` 才算完成；串流在那之前結束 → `PullError`。串流開始後狀態碼已是 200，官方文件說明錯誤只會以最後一行
    `{"error": …}` 出現（實測不存在的模型就是這樣）。HTTP 狀態碼不是 200 → `PullError("HTTP <code> from /api/pull: <error>")`
    （官方錯誤格式 `{"error": "…"}`，`application/json`）；401／403 → `OllamaAuthError`；3xx → `PullError`（寫明不跟隨轉址）。
  - **逾時**：連線 5 s、送出請求 5 s（`PullError("timed out sending the request …")`）；之後讀取以 0.2 s 的 socket 逾時輪詢，
    累計 120 s 沒收到任何資料 → `PullError("no data … within 120 s while waiting for progress")`（驗證數 GB 的 SHA-256 時
    Ollama 會安靜十幾秒）。兩種逾時的訊息不同，才分得出是哪一步卡住。
  - **取消**：開始前、連線後、送出請求後都檢查；連線與送出請求本身不能中斷，所以最久延遲是正在進行的那一步的逾時（連線最多
    5 s，`localhost` 依序試兩個位址時最多 10 s；送出請求最多 5 s）。之後等待回應與讀取進度時，`cancel`（`threading.Event`）
    在每次輪詢檢查，最多延遲約 0.2 s 就丟 `PullCancelledError`。連線期間被取消就不送出請求。不用「另一條執行緒
    關閉 socket」：Windows 上 `shutdown()` 不會喚醒另一條執行緒中卡在 `recv()` 的讀取（實測），而 `makefile()` 持有參照時
    `close()` 也不會真的關閉；也不能直接對 `makefile()` 用 socket 逾時，它逾時一次之後就不能再讀。所以回應改由一個輪詢的
    讀取器（`_PollingSocket`／`_PollingReader`）提供給 `http.client.HTTPResponse`。
  - 金鑰：`api_key_env` 的環境變數有值才送 `Authorization: Bearer …`（Ollama 不需要），不出現在例外或 log。
  - 已安裝的模型也可以 pull（Ollama 只檢查 manifest，很快回報 success）。

### 實測（Ollama 0.40.14，Windows 11，2026-11-05）

本機已在執行的 Ollama（`GET /api/version` → `{"version":"0.40.14"}`），只 pull 已安裝的模型與不存在的名稱，不下載也不刪除任何模型。
`tests/test_hardware_ollama.py` 的 `REAL_PULL_*` 原樣重播這些內容（只把 digest 換成假值）。

- `POST /api/pull {"model":"qwen3.5:4b","stream":true}`（已安裝）：`200`、`Content-Type: application/x-ndjson`、chunked，約 1 s：
  ```text
  {"status":"pulling manifest"}
  {"status":"pulling e56a899b1540","digest":"sha256:e56a899b…","total":675569120,"completed":675569120}
  …（共 6 層：2648593216、13、11343、65、177 bytes，每層 completed = total）
  {"status":"verifying sha256 digest"}
  {"status":"writing manifest"}
  {"status":"removing unused layers"}
  {"status":"success"}
  ```
  `pull_model()` 回報 6 次進度，最後 `(3324173934, 3324173934)`。官方文件的範例寫 `removing any unused layers`，實際是
  `removing unused layers`；程式不依賴 `status` 的文字（只認 `success`），所以不受影響。
- 不存在的模型：`200`、NDJSON 兩行，之後串流結束：`{"status":"pulling manifest"}`、`{"error":"pull model manifest: file does not exist"}`
  → `PullError("Ollama: pull model manifest: file does not exist")`，約 0.7 s。
- 名稱不合法（`"bad name!!"`）或缺少 `model`：`400`、`application/json`、`{"error":"invalid model name"}`（不是串流）
  → `PullError("HTTP 400 from /api/pull: invalid model name")`。
- 請求欄位是 `model`（官方文件；舊版的 `name` 已不用）；`stream` 省略時預設為串流，仍明確送 `true`。`/api/tags` 沒有使用
  （已安裝的模型由 OpenAI 相容的 `GET /v1/models` 取得）。

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
`label` 是模型名稱（不含任何語言的說明）；`models.pull_translation` 完成後引擎清掉偵測快取，下一次 `models.list` 的 `installed` 就會是 true。

## 引擎整合（Milestone 6 已接線，協定細節見 `pipeline-engine.md`）

- `hardware.detect` → `detect(base_url=s.translation.base_url, api_key_env=s.translation.api_key_env, cuda_library_dirs=s.asr.cuda_library_dirs)` + `recommend(info, allow_restricted=s.translation.allow_restricted)`，與 `hardware_report()` 相同的 `{"hardware": HardwareInfo.to_dict(), "recommendation": ModelCombination.to_dict()}`（拆開呼叫是為了快取 `HardwareInfo` 給下面三個方法重用）；`hardware` 保留舊欄位 `cpu_count`、`ram_bytes`、`gpus`、`apple_silicon`、`os`、`os_version`、`machine`。在引擎的背景工作執行緒上執行，不佔用讀 stdin 的執行緒。
- `models.list` → `models_overview(info, recommend(info))`，另保留第一版的 `translation.base_url／installed／error／presets`。
- `models.download {"model": "gpu-runtime", "accept_license": true}`（`GPU_RUNTIME_ID`）→ `install_gpu_runtime(progress=..., cancel=...)`，進度用同一個 `download_progress` 事件（`model = "gpu-runtime"`）。沒有 `accept_license: true` 時回 `license_not_accepted`（訊息含 EULA 網址）；`engine.shutdown` 時取消。
- 推薦一律帶語言：`HardwareService.recommendation(settings)` → `recommend(info, allow_restricted=…, languages=language_selection(settings))`，
  所以 `hardware.detect`、`models.list`、`settings.apply_recommendation`、`settings.preview_recommendation` 都依設定中的語言與 `mode`。
- `models.validate` → `validate_combination(combination_from_settings(設定), info, downloaded_asr=已下載的 ASR, allow_restricted=設定, languages=language_selection(設定))`。
- `settings.preview_languages`（Milestone 12）→ `HardwareService.preview_languages(候選設定, 目前設定)`：候選設定是目前設定合併了要預覽的語言（與
  模式）；回傳推薦、`patch`、每個模型的安裝狀態與下載大小（ASR 取 `ModelCatalog` 的大小；翻譯模型取預設組 `approx_vram_gb` × 10⁹ 的約略值，
  不認得的模型為 null）、`messages`。不存檔、不下載。協定細節見 `pipeline-engine.md`。
- `models.pull_translation`（Milestone 13）→ `EngineServices.translation_models.server_state()`（`probe_translation_server()` + `ollama_version()`）
  確認是可連的 Ollama 後，在背景執行緒呼叫 `pull_model()`；結束（成功或失敗）時清掉偵測快取。`ollama_version()` 丟
  `OllamaAuthError` 時 `server_state()` 回 `auth_failed=True`，引擎回 `translation_server_auth_failed`。
- `session.start` 的翻譯模型檢查（Milestone 13）：同一個 `server_state()`，伺服器是可連的 Ollama 而設定的翻譯模型沒裝時，session 以
  `translation_model_missing` 結束（見 `pipeline-engine.md`）。
- `settings.apply_recommendation` → `recommend(info)` 轉成設定（`engine/hardware_service.py` 的 `recommendation_patch()`）後存檔；`only_if_new` 時只在還沒有設定檔時寫入（第一次執行的自動套用）。
- `settings.preview_recommendation` → 同一個 `recommendation_patch()`，不存檔，另回 `differs`（與目前設定比較）與 `fingerprint`（`recommendation_fingerprint()`：
  `{"patch": patch, "languages": 推薦依據的語言}` 正規化 JSON 的 SHA-256 前 16 字元；換語言就換指紋，即使 patch 相同——新語言可能有新的提示，例如辨識品質較低）。
  Milestone 12 起指紋包含語言，所以升級後之前「不要再提醒」過的推薦會再提醒一次。
- **自動推薦（UI）**：預設設定是安全的 CPU 組合（base／small），所以不能只靠「套用建議組合」按鈕：第一次執行（`settings.get` 的 `is_new`）UI 等 `hardware.detect` 完成後自動 `settings.apply_recommendation {"only_if_new": true}` 並說明選了什麼；之後（啟動、GPU 執行環境安裝後引擎重啟、進入模型頁）以 `settings.preview_recommendation` 檢查，`differs` 時提示「有更適合這台電腦的組合」（可「不要再提醒」，以指紋記在設定的 `ui`）。推薦的 ASR 模型沒下載時提示一鍵下載，不自動下載。細節見 `ui.md`「自動推薦」。
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

`recommend()`（cuBLAS 安裝後；Milestone 16 之前的結果，現在同一台機器是 turbo int8 ／ large-v3 int8 ＋ qwen3.5:9b，見「8 GB 以上等級的組合」）：

```json
{"tier": "nvidia_8gb",
 "fast_asr": {"model": "small", "device": "cuda", "compute_type": "float16", "cpu_threads": 0},
 "final_asr": {"model": "large-v3-turbo", "device": "cuda", "compute_type": "float16", "cpu_threads": 0},
 "translation_fast": "qwen3.5:9b", "translation_final": "qwen3.5:9b", "translation_budget_gb": 8.21,
 "gpu_runtime_missing": false,
 "reasons": ["NVIDIA GeForce RTX 5070（11.9 GB VRAM）：即時用 small、完整用 large-v3-turbo（本機實測 0.13 s＋0.15 s）",
             "large-v3-turbo 的 no_speech 不可用，以 small 的 no_speech 與 VAD 輔助過濾",
             "扣掉 ASR 與 1 GB 保留後，約 8.2 GB VRAM 可給翻譯模型",
             "即時與完整翻譯用同一個模型：GPU 上每句都在 0.3 s 內，Ollama 換模型要 1.5–18 s"],
 "warnings": []}
```

（上面是 Milestone 12 之前的格式。之後 `reasons` 的每一項是訊息物件，例如
`{"code": "asr_gpu", "params": {"gpu_name": "NVIDIA GeForce RTX 5070", "vram_mb": 12227, "fast_model": "small", "final_model": "large-v3-turbo", "compute_type": "float16"}, "message": "…"}`、
`{"code": "translation_budget_vram", "params": {"budget_gb": 8.21}, "message": "…"}`；另有 `translation_status` 與 `languages`。）

cuBLAS 安裝前同一台機器得到 `tier: "cpu"`（base／small CPU int8、各 4 執行緒）、`gpu_runtime_missing: true`、翻譯 `qwen3.5:9b`（10.94 GB 可用）與警告 `gpu_runtime_missing`。

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
- `test_hardware_recommend.py`：每個等級、驅動／compute capability、多 GPU、遠端翻譯、Ollama 連不上、模型未安裝、只裝 Sakura、地區限制、覆寫驗證的每條規則（以訊息碼與參數斷言）；
  語言：`LanguageSelection` 的方向與辨識語言、C 級提示（含口譯兩個語言）、`auto`、同一個 Whisper 語言（`not_needed`）、不支援的語言組合（`unsupported`）、遠端伺服器、
  單一語言 ASR 模型只在該語言被選、registry 每個模型都有品質資料、驗證的語言檢查。
- `test_hardware_messages.py`：訊息的參數檢查（缺少、多餘、型別、NaN、bool）、英文範本可套用、本文件「訊息碼」表與 `CATALOG` 逐字一致。
- `test_hardware_ollama.py`：本機 HTTP 伺服器以 chunked NDJSON 模擬 Ollama：版本偵測、不是 Ollama、連不上、各層進度加總、`error` 行、HTTP 錯誤、沒有 `success`、
  不合法的行、壞掉的進度欄位被略過、讀取逾時、取消卡住的讀取、取消時不送請求、金鑰只在有值時送、`native_root()`、模型名稱驗證，
  以及 401／403 是 `OllamaAuthError`（不是「不是 Ollama」）、3xx 不跟隨且訊息寫明轉址、`/api/version` 內容讀不完有整體時限、
  連線期間取消不送請求、送出請求與等待進度的逾時訊息不同、超過 512 層、網址帳密不出現在任何訊息、實測 NDJSON 的重播（已安裝、
  不存在、名稱不合法）、`redact_url()`。
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
