# 裝置端推論加速與效率選項（2025–2026）：擴大 kikitori 硬體支援、降低延遲與記憶體

> 調查日期：2026-11-08。來源僅限大公司或主要專案的官方文件／release notes／官方部落格，以及主要同儕審查會議（含 ISCA Interspeech、ICML workshop）。
> kikitori 現況（[README.zh-TW.md](../../README.zh-TW.md)、[docs/specs/hardware.md](../../docs/specs/hardware.md)）：ASR 用 CTranslate2/faster-whisper，GPU 只偵測 NVIDIA（「CTranslate2 只支援 CUDA」的前提假設）；Apple Silicon 一律 CPU（base／small）；沒有 NVIDIA 時 CPU 等級為 base／small 或 tiny／base；翻譯用本機 Ollama（qwen3.5:9b／4b）。12 GB RTX 5070 上 turbo＋large-v3（int8_float16）＋qwen3.5:9b 同跑整卡最高 10.8 GB、即時字幕延遲約 0.31 s、說完到完整字幕約 0.92 s。
> 注意：本文件中「v4.7.0 03 Feb」這類 GitHub release 日期，頁面未顯示年份者，以前後版本推定年份並標註「推定」。

## 1. 非 NVIDIA GPU 上的 Whisper：CTranslate2 ROCm、ONNX Runtime EP／Windows ML、OpenVINO GenAI、whisper.cpp、Apple MLX／Core ML

### Takeaway
最大的新事實是 **CTranslate2 自 v4.7.0（推定 2026-02-03）正式支援 AMD GPU（ROCm HIP）**，之後 v4.8.3–v4.8.5（2026-10～11）陸續修正 Windows ROCm 並補上 Windows ROCm/HIP 建置指南——這讓 kikitori 有機會在不換 runtime 的前提下支援 AMD 顯示卡，但 ROCm wheel 不在 PyPI、要從 GitHub Releases 取得，且 Ollama 官方文件在 Windows 上只把 RX 7000／W7000 列為 ROCm 支援卡。Intel GPU、Apple GPU、跨廠牌 Vulkan 則 CTranslate2 都沒有後端，可行路線是換 runtime（whisper.cpp 的 Vulkan／Metal／OpenVINO／Core ML，或 OpenVINO GenAI、ONNX Runtime＋Windows ML EP），代價是要另外維護一條 ASR 實作與模型格式。

### Cited Findings

**CTranslate2（現行 runtime）**
- v4.7.0（頁面日期 03 Feb，前一版 v4.6.3 為 2026-01-06，故推定 2026-02-03）：「Introduce AMD GPU support with ROCm HIP」；同版也有「Enable multiple of 16 padding for INT8 Tensor Cores」與「Assume less about whisper vocab」。 — [OpenNMT/CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- v4.7.2（2026-05-18/19）：「Update source ROCM version from 7.2 to 7.2.1」。 — [CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- v4.8.0（2026-06-06）：「Enables PACKED_GEMM by default for Intel MKL to improve Intel CPU performance」（對 kikitori 的 CPU 路徑可能直接有益，release notes 未附數字）。 — [CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- v4.8.3（13 Oct，推定 2026）：「Fix Windows ROCm Translator hangs and DLL loading」、「Add local CUDA and ROCm release validation with CI wheels」；v4.8.4（25 Oct）：「Use the caching allocator by default on HIP」；v4.8.5（2026-11-02）：「Add a Windows ROCm/HIP build guide and document AMD GPU support」。 — [CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)；v4.8.5 日期由 [PyPI ctranslate2](https://pypi.org/project/ctranslate2/) 確認為 2026-11-02
- PyPI 頁：「If you have an AMD ROCm GPU, we provide specific Python wheels on the releases page」——ROCm wheel 不在 PyPI 的檔案清單中；PyPI wheel 涵蓋 macOS 11+（ARM64／x86-64）、Linux、Windows x86-64，Python 3.9–3.15。CPU 後端列有 Apple Accelerate，**沒有提到 Metal**；量化支援 FP16、BF16、INT16、INT8、「AWQ quantization (INT4)」；授權 MIT。 — [PyPI ctranslate2](https://pypi.org/project/ctranslate2/)（2026-11-08 讀取）
- v4.6.3（2026-01-06）：支援 CUDA 12.8；「Conv1d pure CUDA implementation」移除 cuDNN 必要依賴；修正 Qwen 2.5/3、Phi3 的 AWQ。 — [CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- 在 v4.6.3 到 v4.8.5 的 release notes 中沒有出現 Metal／Apple GPU、Intel GPU 或 Vulkan 後端。 — [CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- AMD 官方 ROCm 部落格曾介紹 ROCm fork（github.com/ROCm/CTranslate2），但該文以 v3.23.0 為對象，已被上游原生支援取代（推論：已過時）。 — [AMD ROCm Blogs: CTranslate2 on AMD GPUs](https://rocm.blogs.amd.com/artificial-intelligence/ctranslate2/README.html)

**ONNX Runtime 執行提供者（EP）與 Windows ML**
- Windows ML 於 2025-09-23 GA；EP 由 Windows 依硬體動態下載，App 不需自帶。 — [Windows Developer Blog, 2025-09-23](https://blogs.windows.com/windowsdeveloper/2025/09/23/windows-ml-is-generally-available-empowering-developers-to-scale-local-ai-across-windows-devices)
- Microsoft Learn（ms.date 2026-10-05，更新 2026-11-06）：Windows ML 內建 CPU 與 **DirectML（標為 legacy）**；可下載的 EP 需 **Windows 11 24H2（build 26100）以上**。Windows ML 2.x 可用：MIGraphX（AMD，將退役，建議改 `AMDGPUExecutionProvider`）、AMD GPU EP（RDNA 3 以上、驅動 25.10.13.09+、需 Microsoft.Windows.AI.MachineLearning 2.5.96+）、NvTensorRtRtx（RTX 30 以上）、OpenVINO（Intel）、QNN（Snapdragon X Elite／Plus，NPU 驅動 30.0.140.0+）、VitisAI（AMD NPU，Adrenalin 25.6.3–25.9.1）、WebGPU（實驗性，任何 DX12 GPU）。MIGraphX「not supported for GenAI scenarios today」。發布形式為 NuGet（Microsoft.WindowsAppSDK.ML／Microsoft.Windows.AI.MachineLearning）。 — [Microsoft Learn: Windows ML execution providers](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers)
- ONNX Runtime DirectML EP 文件：DirectML「in sustained engineering」，仍受支援但新功能開發已轉往 WinML；使用 DirectML 1.15.2，支援到 opset 20，需 DX12 裝置。 — [ONNX Runtime: DirectML EP](https://onnxruntime.ai/docs/execution-providers/DirectML-ExecutionProvider.html)
- Qualcomm 說明 IHV EP 以獨立 MSIX 元件發布（例如 QNN EP），可獨立於 Windows ML runtime 更新。 — [Qualcomm Developer Blog, 2025-09](https://www.qualcomm.com/developer/blog/2025/09/accelerate-ai-apps-windowsml-on-snapdragon-x-elite-devices)

**Intel OpenVINO GenAI**
- OpenVINO 2024.6（2024-12-18）起 GenAI API 支援以 NPU 為目標，WhisperPipeline 支援 whisper-base／medium／small；2026.0 移除已棄用的 stateless Whisper decoder（「Please use a stateful model」）。 — [OpenVINO Release Notes](https://docs.openvino.ai/2025/about-openvino/release-notes-openvino.html)；[OpenVINO 2024 Release Notes](https://docs.openvino.ai/2024/about-openvino/release-notes-openvino.html)
- OpenVINO GenAI 語音辨識頁：以 `optimum-cli export openvino` 匯出 Whisper，INT8 靜態量化用 `--quant-mode int8 --dataset librispeech --num-samples 32`；支援片段與字級 timestamp、語言自動偵測或指定；範例裝置為 CPU、GPU；`ASRPerfMetrics` 回報 encoder／decoder 時間；頁面未列效能數字、未提 INT4。另支援 Qwen3-ASR、SenseVoiceSmall。 — [OpenVINO GenAI: Speech Recognition](https://openvinotoolkit.github.io/openvino.genai/docs/use-cases/speech-recognition/)

**whisper.cpp（ggml）**
- 後端：Metal（Apple Silicon 全 GPU）、Core ML（encoder 跑在 ANE，「more than x3 faster compared with CPU-only」，首次執行需編譯）、Vulkan（跨廠牌 GPU）、OpenVINO（encoder 在 x86 CPU 與 Intel 內顯／獨顯，首次編譯後快取）、CUDA、HIP/ROCm、Ascend、Moore Threads，另列 AMD Ryzen AI NPU（VitisAI）與 ANEForge（「about 2x faster than the Core ML encoder from tiny to medium」）；支援整數量化（例 Q5_0）；授權 MIT；記憶體 large 約 3.9 GB。 — [ggml-org/whisper.cpp README](https://github.com/ggml-org/whisper.cpp)（2026-11-08 讀取）
- 最新 v1.9.5（06 Oct，推定 2026）：同步 ggml v0.26.0、OpenVINO 後端更新到 2026.4.1；v1.9.4（11 Sep）：Vulkan 針對 Strix Halo 調整、Metal 4.0 tensor API（M5+）、「OpenVINO: … whisper.cpp support, Qwen3.5 on NPU」；v1.9.3：Vulkan 對 Intel Xe coopmat 調整；v1.9.2（04 Aug）：公開內建 VAD 片段、VAD 開啟時 token timestamp 對回原始時間；v1.9.0（17 Jun）：支援 NVIDIA Parakeet。release notes 均未附效能數字。 — [whisper.cpp Releases](https://github.com/ggml-org/whisper.cpp/releases)

**Apple（MLX／Core ML／ANE）**
- WhisperKit（Argmax，ICML 2025 On-Device Learning for Foundational Models Workshop，arXiv 2507.10860，2025-07）：在 M3 Max 的 ANE 上重寫 large-v3-turbo；audio encoder 612 → 218 ms（d750 變體，−65%）；decoder 前向 8.4 → 4.6 ms（stateful model）；每字 hypothesis 延遲約 0.45 s；OD-MBP 混合位元 palettization 使模型 1.6 GB → 0.6 GB，LibriSpeech WER 1.93 → 1.96、CommonVoice17-en 12.13 → 13.03；在 22 語的 CommonVoice17 子集上，針對 5 語（含日語）微調後**日語維持在原模型 1% WER 以內**。 — [arXiv 2507.10860（WhisperKit）](https://arxiv.org/html/2507.10860v1)
- `mlx-whisper`（PyPI）存在且 checkpoint 多在 Hugging Face 的 mlx-community；我沒有找到 Apple 官方（ml-explore）發布的速度或準確度數字。 — [PyPI mlx-whisper](https://pypi.org/project/mlx-whisper/)

**比較表（ASR runtime，對 kikitori 的影響）**

| 選項 | 涵蓋硬體 | 官方狀態（日期） | 公開數據 | 授權 | 整合成本 | 主要風險 |
|---|---|---|---|---|---|---|
| CTranslate2 ROCm（同一 runtime） | AMD GPU | v4.7.0 起正式（推定 2026-02）；Windows 修正 2026-10～11 | 未公布 Whisper 數字 | MIT | 低：模型格式不變；需改硬體偵測（AMD GPU、VRAM）、另裝 ROCm wheel 與 ROCm runtime | wheel 不在 PyPI；Windows 支援剛成熟；faster-whisper 是否無痛使用 HIP 版未確認 |
| whisper.cpp | Vulkan（AMD／Intel／NVIDIA）、Metal、Core ML、OpenVINO、HIP | 活躍（v1.9.5，推定 2026-10） | 官方只有 Core ML「>3×」、ANEForge「~2×」等相對值 | MIT | 高：第二套 ASR 後端、GGML 模型格式、Python 綁定需自行選擇 | 雙路徑／no_speech／幻覺濾除等行為需重新驗證 |
| OpenVINO GenAI WhisperPipeline | Intel CPU／iGPU／dGPU／NPU | 2024.6 起支援 NPU；2026.0 只支援 stateful | 官方頁無數字 | Apache-2.0（OpenVINO 一般授權；本次未逐頁確認） | 中高：OpenVINO IR 模型、匯出流程需 optimum（匯出時需 PyTorch，執行時不需——推論） | Intel 限定 |
| ONNX Runtime＋Windows ML EP | AMD／Intel／Qualcomm NPU、GPU | GA 2025-09-23；EP 清單 2026-10 | 無官方 Whisper 數字 | MIT（ORT）；各 EP 另有授權 | 高：Whisper 需 ONNX 匯出、自寫解碼迴圈；Windows ML 以 NuGet／WinAppSDK 發布，Python 端用法未確認 | 只限 Win11 24H2+；DirectML 已 legacy |
| Apple Core ML／ANE（WhisperKit 類） | Apple Silicon | 學術＋開源（2025-07） | 見上（M3 Max） | 本次未確認 WhisperKit 授權 | 高：Swift 生態，與 Python 引擎整合困難 | 只限 macOS |

### Inferences
- 對 kikitori 而言，**AMD 獨顯是「最便宜的新硬體支援」**：CTranslate2 模型（Hugging Face 上的 CTranslate2 轉換版）與量化選項（int8_float16 等）原則上沿用，只需擴充 `hardware/` 的 GPU 偵測與 GPU runtime 下載流程（類似現在的 cuBLAS 一鍵下載）。但要先在實機驗證 faster-whisper 是否能把 `device="cuda"` 對應到 HIP 版，以及 ROCm runtime 的散布方式與授權。
- Intel GPU／Apple GPU 沒有 CTranslate2 後端，只能靠換 runtime；whisper.cpp 一次涵蓋 Vulkan＋Metal＋OpenVINO＋Core ML，覆蓋面最大，但等於第二套 ASR，kikitori 的「先快後準雙路徑」、no_speech、幻覺過濾與 benchmark 都要重做。
- DirectML 已被 Microsoft 標為 legacy／sustained engineering，新整合不宜押在 DirectML 上；Windows ML 的 EP 下載機制需要 Win11 24H2+，而 kikitori 目前支援 Windows 10/11，兩者不完全重疊。
- CTranslate2 v4.8.0 的 Intel MKL PACKED_GEMM 預設開啟，升級 CTranslate2 可能就能改善現有 CPU 等級的延遲，值得在現有 benchmark 上重量一次（官方未給數字）。

### Gaps
- 找不到 CTranslate2 ROCm 在 Whisper 上的官方速度數字，也找不到官方支援的 AMD GPU 清單（gfx 架構）與 Windows ROCm wheel 的確切檔名／ROCm 版本需求；GitHub release 頁的部分日期沒有年份。
- 未確認 faster-whisper 官方是否宣告支援 CTranslate2 ROCm 版。
- whisper.cpp、OpenVINO GenAI、ONNX Runtime 三者都沒有官方發布的 Whisper large-v3／turbo 日語 CER 對照；「日語準確度對等」只有 WhisperKit（ANE，量化＋微調後日語在 1% 以內）一筆學術數據。
- ONNX Runtime 的 ROCm EP、CoreML EP、WebGPU EP 對 Whisper 的狀態本次未逐一查證；Windows ML 在 Python 中的使用方式未查到官方說明。
- Apple MLX 沒有找到 Apple 官方的 Whisper 數據。

## 2. NPU（Copilot+ PC：Qualcomm／Intel／AMD）能否跑 Whisper 級 ASR、延遲多少

### Takeaway
三家都有官方路線，但都有大幅限制：**Qualcomm** 有官方編譯好的 large-v3-turbo（float，encoder 約 560 ms／X Elite、約 250 ms／X2 Elite，固定 30 秒輸入）；**AMD Ryzen AI** 只支援到 medium（large 不支援）、只把 encoder 放 NPU、只限 Windows；**Intel** OpenVINO GenAI 在 NPU 上官方列 base／small／medium。對 kikitori 的即時字幕而言，encoder 固定 30 秒窗與數百 ms 的 encoder 延遲，比目前 RTX 5070 上 0.31 s 的即時延遲慢，NPU 較適合作為「沒有獨顯的筆電省電路徑」而非主路徑。

### Cited Findings
- Qualcomm AI Hub 的 Whisper-Large-V3-Turbo（float，NPU）：Snapdragon X Elite encoder 560–563 ms、decoder 8.5–8.8 ms；X2 Elite encoder 252–258 ms、decoder 5.1–5.5 ms（PRECOMPILED_QNN_ONNX／QNN_CONTEXT_BINARY／VOICE_AI 三種 runtime）；「Time to the first token is the encoder's latency」；輸入固定 128×3000（30 秒），最大解碼 200 tokens；模型標示 Apache-2.0。 — [Hugging Face: qualcomm/Whisper-Large-V3-Turbo](https://huggingface.co/qualcomm/Whisper-Large-V3-Turbo)（2026-11-08 讀取）
- Qualcomm 的 Whisper-Small（float）：X Elite encoder 約 116 ms、decoder 約 10.5 ms；X2 Elite encoder 約 48 ms。 — [Hugging Face: qualcomm/Whisper-Small](https://huggingface.co/qualcomm/Whisper-Small)
- Windows ML 的 QNN EP 需 Snapdragon X Elite／Plus 與 Hexagon NPU 驅動 30.0.140.0+；VitisAI EP（AMD NPU）需 Adrenalin 25.6.3（NPU 驅動 32.00.0203.280）至 25.9.1。 — [Microsoft Learn: Windows ML EPs](https://learn.microsoft.com/en-us/windows/ai/new-windows-ml/supported-execution-providers)
- AMD Ryzen AI：1.1 有 Whisper base 早期存取；1.5 是「first release that supports ASR models on NPU」；1.7 列 whisper.cpp 整合。AMD 技術文章（2025）：支援 Whisper base／small／medium 多語版，「Whisper large exceeds the practical limits of current NPU hardware and is not supported at this time」，KV cache 尚未納入。 — [Ryzen AI Release Notes](https://ryzenai.docs.amd.com/en/latest/relnotes.md)；[AMD: Unlocking On-Device ASR with Whisper on Ryzen AI NPUs (2025)](https://www.amd.com/en/developer/resources/technical-articles/2025/unlocking-on-device-asr-with-whisper-on-ryzen-ai-npus.html)（本次直接讀取逾時，內容取自搜尋摘要，請以原文為準）
- Ryzen AI whisper.cpp 文件：Ryzen AI 300 系列上「Whisper can fully offload the encoder to the NPU」；「NPU acceleration is currently supported on Windows only, with Linux support planned」；未附數字。 — [Ryzen AI: Whisper.cpp support](https://ryzenai.docs.amd.com/en/latest/whisper_cpp.md)
- Intel：OpenVINO 2024.6（2024-12-18）WhisperPipeline 支援 NPU（base／medium／small）；2025.3 NPU 新增 Qwen3-1.7B／4B／8B 與 NF4-FP16 壓縮模型。 — [OpenVINO Release Notes](https://docs.openvino.ai/2025/about-openvino/release-notes-openvino.html)
- Apple ANE（同屬 NPU 類）：WhisperKit 在 M3 Max ANE 上 large-v3-turbo 每字 hypothesis 約 0.45 s。 — [arXiv 2507.10860](https://arxiv.org/html/2507.10860v1)

### Inferences
- Qualcomm 是唯一官方提供 large-v3-turbo NPU 版的廠商，但 X Elite 上光 encoder 就約 0.56 s、且固定 30 秒窗：kikitori 每 0.x 秒重算一次即時字幕的作法在 NPU 上每次都要付完整 encoder 成本，即時字幕延遲會明顯高於現在的 NVIDIA 路徑；X2 Elite（約 0.25 s）才接近可用。
- AMD NPU 只到 medium，對日語 VTuber 情境（kikitori 評測選定 turbo／large-v3）準確度可能不足，且 AMD 的 large 不支援；較實際的 AMD 加速仍是 Radeon 獨顯＋CTranslate2 ROCm，或 Ryzen AI APU 的 iGPU（Vulkan／ROCm）。
- NPU 路線的模型格式（QNN context binary、VitisAI `.rai`、OpenVINO IR）各家不同，同時支援三家等於維護三套模型與 runtime，成本高。

### Gaps
- 三家都沒有官方公布的「NPU 上 Whisper 日語 CER」或端到端（含解碼、多 token）延遲；Qualcomm 數字是單次 encoder／單 token decoder 的託管裝置剖析值。
- AMD Ryzen AI NPU 的 Whisper 實際延遲數字未取得（官方文章讀取逾時）；Intel NPU 的 Whisper 延遲沒有官方數字。
- Qualcomm AI Hub 匯出資產本身的授權條款頁面未明示（只標 Apache-2.0 與原始 Whisper 授權連結）。

## 3. LLM 端（Ollama／llama.cpp）：Vulkan／ROCm／Metal／Intel、speculative decoding、KV-cache 量化、prompt caching

### Takeaway
Ollama 已涵蓋 NVIDIA（CC 5.0+）、AMD（ROCm v7；Windows 只列 RX 7000／W7000）、Apple Metal，並且 **Vulkan 在安裝該後端時預設開啟**，Intel 與舊 AMD 卡走 Vulkan——所以 kikitori 的翻譯端在非 NVIDIA 硬體上「已經」可以被 GPU 加速，kikitori 的推薦邏輯（非 NVIDIA → 一律 qwen3.5:4b 視為 CPU）比實際保守。降低延遲／記憶體的可用槓桿：Flash Attention＋KV cache q8_0（約省一半 KV 記憶體，但 Ollama 提醒 Qwen 類高 GQA 模型精度影響較大）、llama.cpp 的 prompt cache（`cache_prompt` 預設 true）。Ollama 的 speculative decoding 在 2026-10 的 v0.40.x 才開始支援 draft 模型，主要在 MLX 引擎，對 GGUF／Windows 的適用性未確認。

### Cited Findings
- Ollama GPU 文件：NVIDIA compute capability 5.0+、驅動 550+；AMD Linux 需 ROCm v7 驅動，列 RX 9000／7000／6000、Radeon PRO、Ryzen AI、Instinct（gfx1030、gfx1100–1102、gfx1150、gfx1151、gfx1200、gfx1201）；**Windows 只列 RX 7000（7900 XTX～7600）與 Radeon PRO W7000**，需 ROCm v7／HIP7 驅動；「Vulkan is enabled by default when the backend is installed」，可用 `OLLAMA_VULKAN=0` 關閉；Apple 經 Metal 加速；Intel 只有 Vulkan 段落中的 Linux 驅動連結。 — [Ollama Docs: GPU](https://docs.ollama.com/gpu)（無頁面日期，2026-11-08 讀取）
- Ollama FAQ：Flash Attention 可隨 context 成長顯著降低記憶體（`OLLAMA_FLASH_ATTENTION=1` 強制開啟）；KV cache 量化需 Flash Attention，`q8_0`「approximately 1/2 the memory of f16 with a very small loss in precision」、`q4_0` 約 1/4、「small-medium loss」；「Models that have a high GQA count (e.g. Qwen2) may see a larger impact on precision」；此設定是**全域**的（所有模型同一種）；預設 context 4096；`OLLAMA_NUM_PARALLEL` 預設 1，RAM 隨 `NUM_PARALLEL × CONTEXT_LENGTH` 增加；模型預設保留 5 分鐘。FAQ 未提 prompt caching。 — [Ollama Docs: FAQ](https://docs.ollama.com/faq)（2026-11-08 讀取）
- Ollama 最新 v0.40.15（2026-11-03）；v0.40.7（10-18）「More draft models now work with speculative decoding」；v0.40.12（10-27）「Speculative decoding now supports DFlash2 draft models on MLX」、Apple silicon prompt processing 加速；v0.40.15 的資產含 ROCm 與 MLX Linux 版。release notes 未提 Vulkan、KV cache 量化或 prompt caching。 — [Ollama Releases](https://github.com/ollama/ollama/releases)
- llama.cpp server：`cache_prompt`「Re-use KV cache from a previous request if possible」，預設 `true`（但 batch 大小不同時 logits 不保證逐位元相同，可能不完全確定性）；`--cache-reuse`（KV shifting 重用，預設 0 關閉）；`--slot-save-path`；speculative decoding：`-md/--model-draft`、`--spec-draft-n-max`（預設 3），以及 `--spec-type` 的 `draft-simple`、`ngram-simple`、`ngram-map-k`、`ngram-mod`、`ngram-cache` 等不需 draft 模型的 n-gram 方式；`--cache-type-k/v` 可為 f32、f16、bf16、q8_0、q4_0、q4_1、iq4_nl、q5_0、q5_1（預設 f16）；`-fa` 預設 auto。 — [llama.cpp server README](https://github.com/ggml-org/llama.cpp/blob/master/tools/server/README.md)（2026-11-08 讀取）
- llama.cpp 後端：CUDA、HIP（AMD）、Vulkan、SYCL（Intel GPU）、Metal、OpenCL（Adreno）、Hexagon（Snapdragon）、OpenVINO（進行中，Intel CPU／GPU／NPU）、WebGPU、CANN、MUSA 等；1.5–8 bit 整數量化；MIT。 — [ggml-org/llama.cpp README](https://github.com/ggml-org/llama.cpp)
- 同屬官方：OpenVINO 2025.3 在 Intel NPU 上支援 Qwen3-1.7B／4B／8B。 — [OpenVINO Release Notes](https://docs.openvino.ai/2025/about-openvino/release-notes-openvino.html)
- kikitori 自身實測（非外部來源，供對照）：本機伺服器上即時與完整翻譯用不同模型時換模型 1.5–18 s。 — [docs/specs/hardware.md](../../docs/specs/hardware.md)

### Inferences
- **prompt caching 對 kikitori 是低成本高報酬**：每句請求的固定 system prompt 若放在最前面、術語表（每句最多 8 條、內容隨句變動）與原文放在後面，llama.cpp 的前綴重用才能命中；若術語表插在 system prompt 中間，每句都會讓快取在該處失效。這屬於提示詞排列的調整，不需新依賴。Ollama 是否把 `cache_prompt` 行為暴露或預設開啟，官方文件沒說，需實測（例如比對同一 prompt 連續兩次的 prompt eval 時間）。
- KV cache q8_0 對 kikitori 的效益有限：每句翻譯的 context 很短（system prompt＋術語＋一句），KV cache 本身不大；主要記憶體是權重（9B 約 6.6 GB）。而且 `OLLAMA_KV_CACHE_TYPE` 是使用者 Ollama 伺服器的全域環境變數，kikitori 無法逐請求控制，Qwen 類模型又被官方點名精度影響較大，不建議預設推薦。
- speculative decoding 對短句（譯文常 10–40 tokens）的效益本來就受限於 draft 模型的接受率；Ollama 新支援主要在 MLX（Apple），對 Windows／GGUF 使用者目前不可依賴。n-gram 類（不需 draft 模型）在翻譯任務上重複片段少，預期收益低（推論，無官方數字）。
- kikitori 的推薦邏輯可以考慮：偵測到 AMD（RX 7000+ Windows、Linux ROCm 清單）或 Intel Arc 時，不再把翻譯預算當 CPU；但 Vulkan／ROCm 下 qwen3.5 的實際 tokens/s 官方沒公布，需在實機量測後才調整推薦表。

### Gaps
- 沒有找到 Ollama 或 llama.cpp 官方公布的「Vulkan vs ROCm vs CUDA」或「prompt cache 對首 token 延遲」的數字。
- 無法從官方來源確認 Vulkan 何時成為 Ollama 官方二進位的預設（只有無日期的官方文件與新聞報導，新聞已排除）。
- Ollama 是否在 `/v1/chat/completions` 自動重用前綴 KV cache，官方文件未說明。
- Ollama 對 GGUF 模型（非 MLX）的 speculative decoding 支援範圍未確認。

## 4. Whisper 量化進展（int8／int4、weight-only）與準確度，特別是日語

### Takeaway
同儕審查證據顯示 **weight-only 低位元（W4A16）幾乎不傷 WER，但把 activation 降到 8 bit 會多 1–3% 絕對 WER**（Interspeech 2026，僅英文 tiny／base）；WhisperKit 的混合位元 palettization 在 large-v3-turbo 上把模型壓到約 0.6 GB，日語維持在 1% WER 內（ICML 2025 workshop）。但沒有找到針對日語 CER、large-v3／turbo、CTranslate2 int8 的同儕審查測量；kikitori 自己的評測（int8_float16 與 float16 差異不顯著）仍是目前最貼近的證據。CTranslate2 的 INT4 只有 AWQ（主要為 LLM），未見 Whisper 適用說明。

### Cited Findings
- Choi 等人〈Systematic PTQ Study of Integer and Floating-Point Formats for On-Device Whisper ASR〉（INTERSPEECH 2026）：Whisper tiny.en／base.en，80+ 種組合（INT8/4/3、FP8、FP4、NVFP4、MXFP4）；activation 從 16 bit 降到 8 bit 增加約 1–3% 絕對 WER，INT16 與 FP16 activation 表現相同；4 bit 中 NVFP4 W4A16 距全精度 0.07% 以內、壓縮 6.4×；MXFP4 在標準 PTQ 下失效。 — [Interspeech 2026 論文頁（papers.cool 索引 ISCA 條目）](https://papers.cool/venue/choi26@interspeech_2026@ISCA)
- WhisperKit（ICML 2025 workshop）：OD-MBP（低位元 palettization，離群權重保留 float16 稀疏）使 large-v3-turbo 1.6 GB → 0.6 GB；LibriSpeech 1.93 → 1.96、Earnings22 11.55 → 12.35、CommonVoice17-en 12.13 → 13.03；日語在微調後維持原模型 1% WER 以內。 — [arXiv 2507.10860](https://arxiv.org/html/2507.10860v1)
- CTranslate2 支援 FP16、BF16、INT16、INT8 與 AWQ（INT4）；v4.7.0 啟用 INT8 Tensor Core 的 16 倍數 padding。 — [PyPI ctranslate2](https://pypi.org/project/ctranslate2/)；[CTranslate2 Releases](https://github.com/OpenNMT/CTranslate2/releases)
- whisper.cpp 支援 ggml 整數量化（例 Q5_0）；llama.cpp 系列支援 1.5–8 bit。 — [whisper.cpp README](https://github.com/ggml-org/whisper.cpp)；[llama.cpp README](https://github.com/ggml-org/llama.cpp)
- kikitori 自身評測：int8_float16 與 float16 辨識品質差異不顯著、顯示記憶體約減半。 — [README.zh-TW.md](../../README.zh-TW.md)

### Inferences
- kikitori 已經用 int8_float16（weight int8、activation float16，屬 W8A16），與 Interspeech 2026 的結論一致：瓶頸不在 weight 位元而在 activation；進一步降 VRAM 的方向是 weight-only 4 bit，而非全 int8（CPU 上的 `int8` 是 W8A8，可能較傷準確度——推論，未驗證日語）。
- 但 CTranslate2 目前沒有可用於 Whisper 的 W4A16 路徑（AWQ 僅見於 LLM 修正記錄），要拿到 4 bit Whisper 需換 runtime（whisper.cpp Q4/Q5、Core ML palettization），並重跑 kikitori 的日語評測。
- 已有研究指出低資源語言受量化影響較大（arXiv 2405.00966，未確認同儕審查狀態，見排除表），對 kikitori 的泰語、阿拉伯語、印地語等「品質較差」語言，量化應逐語言驗證。

### Gaps
- 沒有找到同儕審查或官方的「Whisper large-v3／turbo 量化後日語 CER」數據。
- 沒有找到 CTranslate2 官方對 Whisper int8 vs float16 的 WER 對照。
- Interspeech 2026 論文只取得摘要（經索引站），完整表格未讀；ISCA Archive 原頁未直接讀取。

## 5. 筆電的能耗與散熱（僅官方來源）

### Takeaway
官方或同儕審查來源中能直接引用的能耗數據極少：唯一具體數字是 WhisperKit 在 Apple ANE 上 decoder 每次前向從 1.5 W 降到 0.3 W（−75%）。Microsoft、Qualcomm、AMD、Intel 的官方文件都以「NPU 省電」作為定位，但本次沒有取得官方公布的 Whisper 功耗或電池續航量測。

### Cited Findings
- WhisperKit：text decoder 每次前向能耗降低 75%，ANE 上 1.5 W → 0.3 W；論文沒有 ANE vs GPU 的直接能耗比較。 — [arXiv 2507.10860](https://arxiv.org/html/2507.10860v1)
- Windows ML 的設計讓 App 可在 NPU／GPU／CPU 間由系統選 EP（官方 GA 公告的定位）。 — [Windows Developer Blog, 2025-09-23](https://blogs.windows.com/windowsdeveloper/2025/09/23/windows-ml-is-generally-available-empowering-developers-to-scale-local-ai-across-windows-devices)
- Qualcomm 的 Whisper-Large-V3-Turbo NPU 剖析中，QNN context binary 版 encoder 峰值記憶體約 1 MB、decoder 33 MB（X Elite PRECOMPILED_QNN_ONNX 版 encoder 則 1674 MB），顯示 runtime 格式對記憶體影響很大。 — [Hugging Face: qualcomm/Whisper-Large-V3-Turbo](https://huggingface.co/qualcomm/Whisper-Large-V3-Turbo)

### Inferences
- 對筆電使用者，kikitori 目前的「先快後準」每句跑兩個模型、即時路徑反覆重算，是耗電大戶；在沒有獨顯的筆電上，若改用 NPU 跑完整路徑或降低即時重算頻率，可能比換更快的 GPU runtime 更省電——但沒有官方數字支持，需要用 OS 的電力量測自行驗證。
- ASR 與 LLM 同時佔用同一顆 GPU（或 iGPU 共享記憶體）時，散熱降頻會同時拖慢兩者；在 APU／iGPU 上，ASR 放 NPU、LLM 放 iGPU 是概念上合理的分工（推論）。

### Gaps
- 沒有找到 Microsoft／Qualcomm／AMD／Intel／Apple 官方公布的「Whisper 在 NPU vs GPU vs CPU 上的功耗或續航」數據。
- 沒有找到官方對長時間連續推論的散熱降頻（thermal throttling）說明。

### 排除但相關的來源（不符來源政策，只列不引用）

| 項目 | 排除原因 |
|---|---|
| Phoronix：Ollama 實驗性 Vulkan（v0.12.6-rc0，2025-10）、v0.12.11 `OLLAMA_VULKAN=1`；OpenVINO 2025.0、Ryzen AI 1.8 報導 | 新聞網站 |
| AlternativeTo／webscraft：Ollama 0.30 預設開啟 Vulkan | 新聞／個人網站 |
| news.lavx.hu：whisper.cpp 1.8.3 在 AMD／Intel 內顯上 12× 加速 | 新聞聚合；whisper.cpp 官方 release notes 未找到此數字 |
| arXiv 2503.09905（Andreyev, 2025-03）：whisper.cpp INT4/5/8 在 LibriSpeech 延遲 −19%、大小 −45% | arXiv 預印本，未確認同儕審查；僅英文 |
| arXiv 2405.00966：LLM.int8() 量化 Whisper，低資源語言受影響較大 | 未確認同儕審查狀態 |
| github.com/davidguttman/whisper-rocm、wyoming-faster-whisper-rocm、likelovewant/ollama-for-amd | 個人專案／非官方 fork |
| mac-whisper-speedtest（M4 上 MLX 等比較） | 個人 benchmark |
| CTranslate2 Windows ROCm PR #2041 | 未合併 PR（其內容後來由 v4.8.5 官方文件取代） |
| dasroot.net、kunalganglani.com 等調校教學 | 個人部落格 |
