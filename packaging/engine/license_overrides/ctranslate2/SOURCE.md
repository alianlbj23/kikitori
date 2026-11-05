# CTranslate2 4.8.5 授權與內含元件（補充檔）

`ctranslate2` 的 wheel 沒有附授權檔，`build.py` 打包時從這個目錄補上（輸出到
`THIRD_PARTY_LICENSES/ctranslate2-4.8.5/upstream/`）。所有檔案都是原文，未修改；
取得日期 2026-11-05。

| 檔案 | 元件 | 授權 | 來源 |
|---|---|---|---|
| `LICENSE` | CTranslate2 | MIT | <https://github.com/OpenNMT/CTranslate2/blob/v4.8.5/LICENSE> |
| `third_party/cpu_features/LICENSE` | cpu_features（靜態連結） | Apache-2.0 | google/cpu_features @ `8a494eb1e158ec2050e5f699a504fbc9b896a43b`（CTranslate2 v4.8.5 的 submodule） |
| `third_party/spdlog/LICENSE` | spdlog（靜態連結） | MIT | gabime/spdlog @ `76fb40d95455f249bd70824ecfcae7a8f0930fa3` |
| `third_party/ruy/LICENSE` | ruy（macOS／ARM 版） | Apache-2.0 | google/ruy @ `363f252289fb7a1fba1703d99196524698cb884d` |
| `third_party/cccl/LICENSE` | Thrust／CUB（CUDA 程式碼） | Apache-2.0 WITH LLVM-exception 等 | NVIDIA/cccl @ `b5fe509fd11a925f90d6495176707cc1184eed9d` |
| `third_party/cutlass/LICENSE.txt` | CUTLASS（CUDA 程式碼） | BSD-3-Clause | NVIDIA/cutlass @ `bbe579a9e3beb6ea6626d9227ec32d0dae119a49` |
| `third_party/bs_thread_pool/LICENSE.txt` | BS::thread_pool 5.1.0 | MIT | bshoshany/thread-pool @ `v5.1.0` |
| `third_party/avx_mathfun/LICENSE-header.txt` | avx_mathfun.h | MIT + zlib | CTranslate2 v4.8.5 `third_party/avx_mathfun.h` 檔頭 |
| `third_party/avx512_mathfun/*` | avx512_mathfun.h（SIMD_Utils） | BSD-2-Clause | 檔頭 + JishinMaster/simd_utils `LICENSE` |
| `third_party/neon_mathfun/LICENSE-header.txt` | neon_mathfun.h | zlib | CTranslate2 v4.8.5 `third_party/neon_mathfun.h` 檔頭 |
| `third_party/onednn/*` | oneDNN 3.1.1（靜態連結，Windows／Linux x86-64） | Apache-2.0 | oneapi-src/oneDNN @ `v3.1.1`：`LICENSE`、`THIRD-PARTY-PROGRAMS` |
| `third_party/intel_onemkl/LICENSE.txt` | Intel oneMKL（靜態連結，Windows／Linux x86-64） | Intel Simplified Software License（2022-10） | PyPI `mkl==2025.3.0` wheel 的 `LICENSE.txt` |
| `third_party/intel_openmp/*` | Intel OpenMP 執行環境 `libiomp5md.dll`（Windows wheel 內附，檔案版本 20250910 = oneAPI 2025.3） | Intel End User License Agreement for Developer Tools（2024-08），其中的 Redistributables 條款 | PyPI `intel-openmp==2025.3.0` wheel 的 `LICENSE.txt` 與 `third-party-programs.txt` |
| `third_party/libgomp/*` | GNU OpenMP `libgomp`（Linux wheel 內附） | GPL-3.0 WITH GCC-exception-3.1 | gcc-mirror/gcc @ `releases/gcc-14.2.0`：`COPYING3`、`COPYING.RUNTIME` |

依平台實際打包的部分不同：Windows 有 oneMKL、oneDNN、Intel OpenMP；Linux x86-64 有
oneMKL、oneDNN、libgomp；macOS arm64 只有 Apple Accelerate（系統框架）與 ruy。
為了簡單，三個平台都附上全部文字。

CTranslate2 Windows wheel 內附的 `cudnn64_9.dll`（NVIDIA cuDNN 載入器）**不打包**：
Whisper 用不到 cuDNN（`docs/specs/asr.md`），規則見 `packaging/engine/policy.py`。

更新 CTranslate2 版本時，重新核對 submodule commit 與 wheel 內附的二進位元件並更新本表。
