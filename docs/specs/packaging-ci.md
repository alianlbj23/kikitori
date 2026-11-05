# Spec: packaging-ci（引擎打包、版本同步、CI）

> 狀態：已採用（2026-11-05，Milestone 8 前半，使用者授權依建議自主實作）。
> 上位文件：`docs/specs/app-architecture.md` 的「打包」「版本號」「CI/CD」各節；本文件只補充細節。
> 與上位文件的差異（待協調者更新上位文件）：架構規格寫「one-folder，作為 Tauri `externalBin` sidecar」，但 `externalBin` 只能放單一執行檔。本文件依量測結果採用 **one-folder + Tauri `bundle.resources`**（見「sidecar 方式」）。

## 範圍

| 項目 | 位置 |
|---|---|
| PyInstaller 打包（引擎 `kikitori-engine`） | `packaging/engine/` |
| app（Tauri 執行檔、前端 bundle）的第三方授權聲明 | `packaging/app/` |
| 版本號同步 | `scripts/sync_version.py` |
| CI（測試、lint、版本檢查、三平台打包 + 冒煙測試） | `.github/workflows/test.yml` |
| Tauri 前端／Rust 的 CI、發布 | `.github/workflows/app.yml`、`.github/workflows/release.yml`（見 `release-update.md`） |

## 引擎打包（`packaging/engine/`）

| 檔案 | 角色 |
|---|---|
| `entry.py` | PyInstaller 入口腳本。不帶參數 = `kikitori engine`（stdio 引擎）；`--self-check` = 打包內容自我檢查，輸出一行 JSON（不屬於引擎協定） |
| `kikitori-engine.spec` | PyInstaller 規格：收集內容、過濾、產生授權目錄 |
| `policy.py` | 打包規則（名稱、排除的模組、禁止打包的檔名、系統提供的檔名），spec 與 build.py 共用 |
| `licenses.py` | 產生 `THIRD_PARTY_LICENSES/` |
| `license_overrides/` | wheel 沒附授權檔的套件的授權全文（`ctranslate2/`，以及 macOS 才打包的 `pyobjc-core/`、`pyobjc-framework-coremedia/`、`pyobjc-framework-libdispatch/`、`pyobjc-framework-screencapturekit/`；來源見各目錄的 `SOURCE.md`） |
| `build.py` | 建置腳本：人用的編號選單；CI 用參數或 `KIKITORI_BUILD_NONINTERACTIVE=1` |
| `smoke_test.py` | 冒煙測試（`--self-check` + 假模式協定 + 啟動時間），CI 與人工共用 |

目錄名稱 `packaging` 與 PyPI 的 `packaging` 套件同名，所以這些檔案彼此、以及測試，一律依檔案路徑載入（`importlib.util.spec_from_file_location`），不靠 `sys.path`。

### 指令

```text
uv run python packaging/engine/build.py                     互動選單
KIKITORI_BUILD_NONINTERACTIVE=1 uv run python packaging/engine/build.py   預設建置（CI）
uv run python packaging/engine/build.py --smoke             建置 + 冒煙測試
uv run python packaging/engine/build.py --no-sidecar        只輸出到 dist/
uv run python packaging/engine/build.py --onefile           one-file（externalBin 命名）
uv run python packaging/engine/smoke_test.py --binary dist/kikitori-engine/kikitori-engine.exe --self-check
uv run python packaging/engine/smoke_test.py --dev          對開發環境的引擎
```

### 輸出

| 模式 | `dist/` | 放給 Tauri（`--sidecar-dir`，預設 `app/src-tauri/binaries/`） |
|---|---|---|
| one-folder（預設） | `dist/kikitori-engine/`：`kikitori-engine[.exe]`、`_internal/`、`THIRD_PARTY_LICENSES/` | `binaries/kikitori-engine/`（整個目錄） |
| one-file | `dist/onefile/kikitori-engine[.exe]`、`dist/onefile/THIRD_PARTY_LICENSES/` | `binaries/kikitori-engine-<rust target triple>[.exe]` 與 `binaries/kikitori-engine-THIRD_PARTY_LICENSES/` |

- target triple：`--target-triple` > `KIKITORI_TARGET_TRIPLE` > `rustc -vV` 的 `host:`（15 秒 timeout）> 依平台推算（`x86_64-pc-windows-msvc`、`x86_64-unknown-linux-gnu`、`aarch64-apple-darwin`…），格式會驗證。
- `build.py` 只會刪除／覆寫自己產生的名稱（`binaries/kikitori-engine/`、`kikitori-engine-<triple>`、`kikitori-engine-THIRD_PARTY_LICENSES/`），不動該目錄的其他檔案。`/build/`、`/dist/`、`/app/src-tauri/binaries/` 已加入 `.gitignore`。
- 建置前檢查已安裝的 kikitori metadata 版本與 `pyproject.toml` 相同（不同就要求 `uv sync`），因為 `engine.info` 的版本來自打包進去的 metadata。
- 建置後再掃一次輸出目錄，出現禁止的檔案就失敗（spec 的過濾之外的最後防線）。

### 收集內容

| 內容 | 做法 | 理由 |
|---|---|---|
| kikitori 全部子模組 | `collect_submodules("kikitori")` | 很多模組在函式內延遲匯入（假模式服務、ASR 接縫…） |
| Silero ONNX 模型與授權 | `collect_data_files("kikitori")` | 以 `importlib.resources` 讀取；放在 `_internal/kikitori/vad/models/`，PyInstaller 的 resource reader 可讀 |
| kikitori metadata | `copy_metadata("kikitori")` | `engine.info` 的版本 |
| CTranslate2 原生函式庫 | `collect_dynamic_libs("ctranslate2")` | 沒有官方 hook；`__init__` 會載入套件目錄下全部 DLL |
| onnxruntime、OpenCC 資料 | pyinstaller-hooks-contrib 的 hook | — |
| Windows 音訊 | hidden import `pyaudiowpatch` | capture 以 `importlib.import_module` 載入 |
| Linux／macOS 音訊 | hidden import `soundcard`、`soundcard.pulseaudio`（Linux）／`soundcard.coreaudio`（macOS）、`_cffi_backend`；`collect_data_files("soundcard")`（cffi 讀的 `*.py.h`） | 同上；macOS 另加 `objc`、`AppKit`、`CoreMedia`、`ScreenCaptureKit`、`dispatch` |

**排除的模組**：`ctranslate2.converters`／`specs`（只給模型轉換、會拉 torch）、`tkinter`、`pipecat`、`torch`、`transformers`、`PyInstaller`。

**不打包的檔案**（`policy.py`，以檔名比對）：

| 類別 | 檔名 | 理由 |
|---|---|---|
| NVIDIA | `cudnn*`、`cublas*`、`cudart*`、`cufft*`、`curand*`、`cusolver*`、`cusparse*`、`nvrtc*`、`nvjitlink*`、`nccl*`、`nvcuda*`、`libcuda.so*`、onnxruntime 的 CUDA／TensorRT provider（含 `lib` 前綴版本） | 專有授權且體積大；cuBLAS 由使用者在 app 內下載（`hardware.md`）。CTranslate2 Windows wheel 附的 `cudnn64_9.dll` 實際被濾掉——Whisper 用不到 cuDNN（`asr.md`），`ctranslate2.dll` 的匯入表也沒有它 |
| PulseAudio | `libpulse*` | LGPL；soundcard 在執行時以 cffi 從系統載入 |
| Windows UCRT | `api-ms-win-*.dll`、`ucrtbase.dll` | Windows 10 起是作業系統的一部分；PyInstaller 會從 PATH 上任意副本收集（本機是 Windows SDK 工具目錄），結果隨機器而變 |

### 自我檢查（`kikitori-engine --self-check`）

在打包好的環境執行，每項獨立回報：`version`（metadata 在）、`silero_vad`（讀打包的模型、驗 SHA-256、推論靜音與正弦波，機率有限且靜音 < 0.5；授權檔在）、`onnxruntime`（有 CPU provider）、`ctranslate2`（CPU 支援 int8）、`opencc`（`s2twp` 把「软件」轉成「軟體」）、`audio_backend`（Windows 匯入 pyaudiowpatch；Linux 確認 soundcard 模組與標頭檔已打包，但**不匯入**——匯入就會載入 libpulse 並連線音訊伺服器；macOS 匯入 PyObjC 框架）。同一組檢查也在單元測試中以原始碼環境執行。

## sidecar 方式：one-folder + `bundle.resources`

### 量測（本機 Windows 11、Ryzen 7 5700X、2026-11-05，PyInstaller 6.22.3）

| | one-folder | one-file |
|---|---|---|
| 大小 | 170.2 MB（目錄，未壓縮） | 59.6 MB（單一執行檔，壓縮） |
| 啟動到第一筆 `status idle`（假模式，10 次中位數；`smoke_test.py --startup-runs 10`） | **0.30 s**（0.30–0.33） | **1.28 s**（1.22–1.45） |
| 每次啟動的暫存寫入 | 無 | 每次解壓約 170 MB 到 `%TEMP%\_MEI*` |
| 被強制結束（Tauri kill／當機）後 | 無殘留 | 實測：bootloader 被 kill 後子行程仍在（直到 stdin 關閉才結束），`_MEI*`（約 170 MB）**留在暫存目錄不會清掉** |

Linux（Debian bookworm 容器、x86_64）one-folder：269 MB（`du`），啟動 0.24 s，冒煙測試通過。

### 決定

採用 **one-folder**，整個目錄以 Tauri `bundle.resources` 隨安裝檔散布，由 Rust 直接啟動資源目錄裡的執行檔；不需要另外寫 launcher（Rust 端本來就負責啟動與管理子行程）。理由：啟動快 1 秒、不必每次寫 170 MB 暫存檔、強制結束不會留下暫存目錄、防毒軟體對 one-file 自解壓的誤判也較多。`build.py --onefile` 保留為備案（externalBin 命名已實作）。

### 協調者需要在 `app/` 做的事

1. `tauri.conf.json`（或目前的 `tauri.sidecar.conf.json`）改用 resources，取代 `externalBin`：
   ```json
   { "bundle": { "resources": { "binaries/kikitori-engine/": "kikitori-engine/" } } }
   ```
2. `src-tauri/src/engine.rs` 的 release 路徑改成 `app.path().resource_dir()?/kikitori-engine/kikitori-engine[EXE_SUFFIX]`，`cwd` 設為該目錄（Windows 的 resource_dir 就是安裝目錄；macOS 是 `Contents/Resources`；Linux deb／AppImage 是 `/usr/lib/<productName>/`）。
3. `cargo build` 會檢查 resources 路徑存在：CI 的 Tauri job 先跑 `build.py`（不加 `--no-sidecar`），或建立佔位目錄。
4. 在 release workflow 中確認：Linux／macOS 安裝後的執行檔保有執行權限；macOS 上 PyInstaller 已對每個 Mach-O 做 ad-hoc 簽章（Apple Silicon 必要），Tauri 對 `.app` 做簽章時不能破壞它們。可對安裝後的路徑再跑一次 `smoke_test.py --binary <path> --self-check`。

## 授權

### 建置工具

| 項目 | 版本 | 授權 | 備註 |
|---|---|---|---|
| PyInstaller | 6.22.3（2026-09-12） | GPL-2.0-or-later **附 bootloader 例外** | 例外條款允許以任何授權（含商業）散布打包結果；只有 bootloader 進入執行檔。開發依賴（`uv add --dev`） |
| pyinstaller-hooks-contrib | 2026.9 | GPL-2.0-or-later 或 Apache-2.0（hooks 本身） | PyInstaller 的依賴；hooks 只在建置時執行，不進執行檔 |
| altgraph、pefile、pywin32-ctypes、setuptools、packaging | lock 中的版本 | MIT／BSD 類 | PyInstaller 的依賴，建置時用 |

### `THIRD_PARTY_LICENSES/`

由 `licenses.py` 依「實際打包的檔案」產生：PyInstaller 分析出的每個來源檔，依各套件 RECORD 對回提供它的套件，再複製該套件 dist-info 內的授權檔與套件目錄內的 `LICENSE*`／`NOTICE*`／`ThirdPartyNotices*`（程式碼與二進位檔、macOS `.dSYM` 除錯符號內的檔案除外，例如 pyobjc-core 的測試模組 `copying.*.so`）。另外固定加入：kikitori（`LICENSE`、`src/` 內附的 `LICENSE-silero-vad`）、Python（`LICENSE.txt`，含 CPython 內附元件的條款）、PyInstaller（bootloader）。每個元件一個子目錄（保留原始相對路徑），加上 `README.txt` 與 `manifest.json`。

- 打包了卻找不到授權全文的元件 → **建置失敗**（補到 `license_overrides/<套件名>/`）。
- 對不回任何套件的檔案（系統函式庫）列在 README，已知的標註授權：Microsoft Visual C++ 執行階段（`msvcp140*`、`vcruntime140*`，Visual Studio 授權的 Distributable Code）、GCC 執行階段（`libstdc++`、`libgcc_s`、`libgomp`，GPL-3.0 附 GCC Runtime Library Exception）、`libz`（zlib）。不認得的標成 `UNKNOWN - review before release` 並在建置時印 warning。

各平台實際打包的元件（Windows 實測）：kikitori、Python 3.12.15、ctranslate2 4.8.5、numpy 2.5.4、onnxruntime 1.31.1、OpenCC 1.4.2、PyAudioWPatch 0.2.12.9、PyYAML 6.0.3、PyInstaller bootloader、VC++ 執行階段。Linux（容器實測）另有 SoundCard 0.4.6、cffi／pycparser、setuptools、packaging 等，以及系統的 libstdc++／libgcc_s／libz。

### 需要使用者知悉的授權

- **Intel OpenMP（`libiomp5md.dll`，CTranslate2 Windows wheel 內附，檔案版本 20250910 = oneAPI 2025.3）**：同版本 PyPI `intel-openmp` 的授權是 *Intel End User License Agreement for Developer Tools*（2024-08）。其 Redistributables 條款允許隨「你的產品」散布執行碼，但要求散布時的授權條款禁止對該檔案逆向工程、且不得使其成為 copyleft 的一部分。`ctranslate2.dll` 的匯入表直接依賴它，不能拿掉（除非自行建置 CTranslate2）。目前作法：照原樣散布，全文放在 `THIRD_PARTY_LICENSES/ctranslate2-4.8.5/upstream/third_party/intel_openmp/`。**建議**：發布前在 app 的授權說明（安裝畫面或「關於」）加一句「內含的 Intel 元件依其授權條款提供，不得逆向工程」；這是 CTranslate2／faster-whisper 系列桌面程式普遍的情況。`asr.md` 已記錄此項待使用者確認。
- oneMKL（靜態連結）：Intel Simplified Software License（可再散布、須附條款、禁止逆向工程），全文已附。
- NVIDIA cuDNN 載入器：不打包（見上）。

## app 的第三方授權聲明（`packaging/app/`）

引擎的 `THIRD_PARTY_LICENSES/` 只涵蓋 PyInstaller 打包的 Python 元件。Tauri 執行檔編譯進去的 Rust crate、前端 bundle 內的 npm 套件，以及 kikitori 自己的 LICENSE 另外整理成 `licenses/`，隨安裝檔散布（MIT／Apache-2.0 等要求散布時附上授權聲明；MPL-2.0 §3.2 還要求告知取得原始碼的方式）。

| 檔案 | 角色 |
|---|---|
| `packaging/app/licenses.py` | 產生與檢查 `licenses/`（只用標準庫）。不帶參數是互動選單；CI 用 `--out`、`--check-bundle` |
| `packaging/app/license_overrides/<npm 套件名>/` | npm 套件沒附授權全文時的補充檔（目前是 `@tauri-apps/plugin-opener`、`plugin-process`、`plugin-updater`：npm 套件只有 `LICENSE.spdx`；來源見各目錄的 `SOURCE.md`） |
| `app/src-tauri/about.toml` | cargo-about 設定（範圍與允許的授權） |
| `app/src-tauri/tauri.sidecar.conf.json` | `bundle.resources` 的 `"licenses/": "licenses/"`，與引擎目錄一起帶進安裝檔 |

### 範圍

- **Rust**：`cargo about generate --format json`，三個發布平台（與 `deny.toml` 相同）的 **normal 依賴**，三平台聯集（每個安裝檔都附同一份，多列無害）。排除 `build-dependencies`（只在建置機器上執行，不連結進執行檔）與 `dev-dependencies`（只給測試）。proc-macro 在依賴圖上是 normal 依賴，cargo-about 不區分它是否連結進執行檔，所以**保留**（寧可多列）：例如四個 Servo 的 MPL-2.0 crate 只經 `tauri-macros`（proc-macro）→ `tauri-codegen` → `tauri-utils` → `dom_query` 進入依賴圖，實際上可能沒有連結進執行檔，仍照列並附原始碼說明。kikitori 自己的 crate（本機路徑、`source` 為空）不列，由 `kikitori/LICENSE` 涵蓋。
- **npm**：`package-lock.json` 從根的 `dependencies`（含 `optionalDependencies`、`peerDependencies`）依 Node 的解析規則走出的遞移閉包，也就是 `@tauri-apps/api` 與三個外掛；devDependencies 不列。例外：**Vite**（把自己的 modulepreload polyfill 寫進 bundle，`dist/assets/*.js` 實測可見）與 **Rolldown**（Vite 的 bundler，某些模組型態會輸出執行期 helper；目前的 bundle 沒有，為了之後改程式不會漏掉而列入）。Vite 的 `LICENSE.md` 本身已含它內嵌依賴的授權。
- 讀 `node_modules/<套件>/package.json` 的名稱、版本與 `license`（必須與 lock 相同，不同就要求 `npm ci`），複製套件最上層的 `LICENSE*`／`COPYING*`／`NOTICE*` 等檔案（與引擎相同的檔名規則）。`*.spdx` 只是 metadata，不算全文；沒有全文時用 `license_overrides/<套件名>/`，兩邊都沒有 → **失敗**。override 目錄的 `VERSIONS` 列出已核對過上游授權檔的套件版本；安裝的版本不在其中 → **失敗**（升級套件後不會靜默沿用沒核對過的全文）。套件自己有同名授權檔時以套件的為準。
- 每個 crate 都必須至少對應到一份授權全文，否則失敗（`cargo about --fail` 也會在無法判定授權時失敗）。沒附授權檔的 crate（例如 Tauri 外掛、`windows-*`）由 cargo-about 填入該 SPDX 授權的標準全文。
- 以 MPL 授權使用的 crate（雙授權時選了別的授權的不算）必須來自 crates.io，否則失敗；每個都寫進 `MPL-2.0-SOURCE.txt`（名稱、版本、crates.io 頁面、`.crate` 下載網址、上游 repository）。

### 輸出（`app/src-tauri/licenses/`，不進 repo）

| 檔案 | 內容 |
|---|---|
| `README.txt` | 目錄說明，並指向引擎的 `kikitori-engine/THIRD_PARTY_LICENSES/` |
| `kikitori/LICENSE` | kikitori 本身（MIT，repo 根目錄的 `LICENSE`） |
| `rust/NOTICES.txt` | crate 清單（名稱、版本、採用的授權），接著每份授權全文與使用它的 crate |
| `rust/MPL-2.0-SOURCE.txt` | MPL-2.0 crate 的原始碼取得方式（2026-11-05：cssparser 0.37.0、cssparser-macros 0.7.1、dtoa-short 0.3.5、option-ext 0.2.0、selectors 0.38.0） |
| `npm/NOTICES.txt` | npm 套件清單與各自授權檔的全文 |
| `manifest.json` | 機器可讀的清單；`generator` 欄位讓程式只清空自己產生過的目錄（`--out` 指錯時不會刪掉別的東西） |

**產生而不提交**：依賴升級時提交的檔案會過期，而且沒有東西會提醒；改成每次發布建置都從 `Cargo.lock`／`package-lock.json` 當場產生，永遠與該次建置一致。開發用的 `cargo build`／`tauri dev` 不讀 `tauri.sidecar.conf.json`，所以這個目錄不存在也不影響；本機要用 sidecar 設定跑 `tauri build` 時，先執行本程式（與先跑 `build.py` 相同）。

在安裝後的位置（`<資源目錄>/licenses/`）：Windows 是安裝目錄，Linux deb／AppImage 是 `/usr/lib/kikitori/`，macOS 是 `kikitori.app/Contents/Resources/`。「關於」頁可開啟它與引擎的授權資料夾（`ui.md`）。

### cargo-about

| 項目 | 值 |
|---|---|
| 版本 | 0.9.2（2026-08-18 發布；0.9.3 發布未滿兩週，不採用）。MIT OR Apache-2.0 |
| 取得方式 | GitHub Release 的預建發行檔，核對 SHA-256（與 cargo-deny 相同的做法） |
| `x86_64-unknown-linux-musl.tar.gz` | `9099a59e820c38a68b9d65f300662a567d56562f9a10f6aa4c7e86c17c2566af` |
| `x86_64-pc-windows-msvc.tar.gz` | `1c03e5890238562497c2d89a3b75b02560af349c1fc3e713d3284f532a5cd748` |
| `aarch64-apple-darwin.tar.gz` | `ae72f0df0c399a1e96336f696fa55b1b28679fd725632eba8cf8e4568467cc3e` |

雜湊與 Release 附的 `.sha256` 相符（2026-11-05 下載核對）。本機可用同樣的發行檔，或 `cargo install --locked --features cli cargo-about@0.9.2`。

```text
uv run python packaging/app/licenses.py                                  互動選單
uv run python packaging/app/licenses.py --out app/src-tauri/licenses     產生（CI）
uv run python packaging/app/licenses.py --check-bundle <資源目錄>         檢查安裝後的兩份授權資料夾
```

`--check-bundle` 確認 `licenses/` 的每個檔案、`manifest.json` 由本程式產生、每個 MPL crate 在 `MPL-2.0-SOURCE.txt` 都有說明（逐行精確比對名稱版本與 crates.io 網址，`foo 1.0.1` 不能頂替 `foo 1.0`），以及引擎的 `kikitori-engine/THIRD_PARTY_LICENSES/README.txt` 存在。

CI：`app.yml` 的 `licenses` job 每次 push 都產生一次（確認產生得出來，結果寫進 step summary）；`release.yml` 每個平台在 `tauri build` 前產生、安裝後以 `--check-bundle` 檢查（見 `release-update.md`）。

## 版本號同步（`scripts/sync_version.py`）

單一來源 `pyproject.toml` 的 `[project].version`，必須是 SemVer（Cargo／Tauri 的要求）。

| 目標（存在才處理） | 欄位 |
|---|---|
| `app/src-tauri/tauri.conf.json` | 最上層 `version` |
| `app/src-tauri/Cargo.toml` | `[package].version` |
| `app/package.json` | 最上層 `version` |
| `app/src-tauri/Cargo.lock` | 本程式自己的 `[[package]]`（否則 `cargo --locked` 失敗） |
| `app/package-lock.json` | 最上層與 `packages[""]` 的 `version` |

- 只改版本字串，保留原本的格式與換行字元（CRLF／LF）；改完重新解析，確認版本確實是新值才寫入。
- `show`／`sync`／`check`（不一致時結束碼 1，CI 使用）／`bump patch|minor|major`（改 pyproject → 同步 → 執行 `uv lock` 更新 uv.lock，timeout 300 秒；不手動改 uv.lock）。不帶參數是互動選單。

## CI（`.github/workflows/test.yml`）

- 觸發：任何分支的 `push` 與 `pull_request`。`permissions: contents: read`；同一 ref 的舊執行會被取消。
- 第三方 action 釘在完整 commit SHA：`actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1`（v7.0.1）、`astral-sh/setup-uv@1c37ad07a6a961277cf70c0d37d6f313000f5884`（v10.3.0，選發布超過兩週的版本）。uv 固定 0.13.4。`checkout` 設 `persist-credentials: false`。
- 環境：`PYTHONUTF8=1`、`PYTHONIOENCODING=utf-8`（Windows runner 的管線預設不是 UTF-8）。

| job | 矩陣 | 步驟 |
|---|---|---|
| `python` | windows-latest、ubuntu-latest、macos-latest（arm64） | `uv sync --locked` → `unittest discover` → `ruff check` → `sync_version.py check` |
| `engine-bundle` | 同上 | `uv sync --locked` → `build.py --no-sidecar --smoke`（建置 one-folder、掃禁止檔案、`--self-check`、假模式協定測試） |
| （`app.yml`） | 同上 | Tauri 前端與 Rust 的檢查另放在 `app.yml`（見 `release-update.md`）；`test.yml` 有 `workflow_call`，供 `release.yml` 重用 |

CI runner 沒有音訊裝置、Ollama、模型與本機忽略的資料：測試以 Linux 容器（Debian bookworm、uv 管理的 Python 3.12、空的 HOME）與 Windows 上把 `APPDATA`／`LOCALAPPDATA`／`USERPROFILE`／`XDG_*` 指到空暫存目錄的方式驗證過。

## release workflow（`release.yml`，已實作，細節見 `release-update.md`）

依架構規格：push 到 main → 跑同一套測試 → 版本號與最新 release tag 不同時，三平台（實作上打包與測試平行，Release 在全部成功後才建立）：

1. `uv sync --locked`、`build.py --smoke`（放到 `app/src-tauri/binaries/kikitori-engine/`）。
2. `tauri build`（resources 帶入引擎目錄），以 `TAURI_SIGNING_PRIVATE_KEY`（GitHub Secrets）簽更新器檔案。
3. 對安裝後的引擎再跑 `smoke_test.py --binary <path> --self-check`（確認執行權限與 macOS 簽章）。
4. 建立 GitHub Release（tag = `v<pyproject 版本>`），附 `latest.json`、簽章與各平台安裝檔；`THIRD_PARTY_LICENSES` 隨引擎目錄進安裝檔，app 的 `licenses/`（Rust、npm、kikitori 的 LICENSE）在 `tauri build` 前產生、同樣以 resources 帶入。
5. 權限只在建立 release 的 job 給 `contents: write`。

## 測試

```text
uv run python -m unittest tests.test_packaging_build tests.test_packaging_licenses tests.test_sync_version tests.test_app_licenses
```

- `test_sync_version.py`：暫存目錄的假 repo（每種目標檔）、check／sync／bump、只改版本字串、CRLF 保留、錯誤路徑（非 SemVer、壞 JSON、缺欄位、Cargo.lock 沒有本程式）、`uv lock`（假 runner：成功、失敗、逾時、沒有 uv）、選單。
- `test_packaging_build.py`：禁止／系統檔名規則、target triple（參數、環境變數、rustc 輸出、rustc 缺少或逾時、格式驗證、推算表）、PyInstaller 指令、建置流程（假 PyInstaller：成功、失敗、逾時、缺執行檔、含禁止檔案）、sidecar 放置（one-folder／one-file，不動其他檔案）、選單與參數、`entry.py`（未知參數、自我檢查的錯誤彙整、原始碼環境下全部檢查通過）、對開發環境引擎跑完整冒煙測試。
- `test_packaging_licenses.py`：假 dist-info（PathDistribution）的對應、授權檔複製與相對路徑、override、缺授權、系統函式庫標註、輸出覆寫、PyInstaller 一律列入。
- `test_app_licenses.py`：暫存目錄的假 `package-lock.json`／`node_modules`：正式依賴閉包（巢狀解析、往上層找、dev 套件排除、optional／peer、缺依賴、壞 lock、link）、實際的 `app/package-lock.json` 只走到 `@tauri-apps/*`、Vite 列入、`.spdx` 不算全文、override（`VERSIONS` 必須列出安裝的版本、缺 `VERSIONS`、套件自己的同名檔優先）、`npm ci` 提示（缺檔、版本不符）、缺 `license`；假 cargo-about JSON：略過本機 crate、MPL 判定（雙授權選 MIT 時不算）、缺全文、非 crates.io 的 MPL crate、壞資料；cargo-about 指令（假 runner：成功、未安裝、壞 JSON、逾時、找不到 cargo）；輸出目錄（內容、只覆寫自己產生的目錄、缺 LICENSE）；`--check-bundle`（完整、缺檔、缺 MPL 說明、較長版本號不能頂替、他人的 manifest）、寫入失敗回報結束碼 1；選單與參數。提交的 override 都有全文與 `SOURCE.md`，`VERSIONS` 含 `package-lock.json` 鎖定的版本。
