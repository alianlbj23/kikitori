# 沿用 Silero VAD，依情境切換句尾判斷

**結論先講：** 目前沒有任何一個串流 VAD 能用中立證據證明，它會把「歌聲」判成非語音。對 kikitori 來說，最站得住腳的預設仍是 **Silero VAD v6.2（ONNX、MIT）**。在授權與部署上，唯一可信的挑戰者是 **FireRedVAD（Apache-2.0）**，但它的領先只有作者自家數據支持；而且它的二元 VAD 在設計上就把歌聲算成語音。TEN VAD 沒有更好的獨立證據，授權還附帶「不得與 Agora 競爭」條款，不適合預設打包。

句尾判斷應該依情境分開處理：

- **情境 A（字幕）：**用「VAD 給候選停頓 + 串流 ASR 文字的句尾／標點訊號 + 最長長度安全網」。同行審查文獻顯示，語意或標點式切點能把端點延遲降低約 23–53%，並提升翻譯品質。
- **情境 B（口譯）：**現成工具中，只有 **Smart Turn v3.x** 同時符合 ONNX、免 PyTorch、日英中、MIT 相容授權。但它是為「人對語音助理」設計的；LiveKit（競品）的評測顯示，它只把「5% 誤切」所需延遲從 1.6 s 降到約 1.05 s；中文又是它官方數字中的弱項。因此它只適合當「提早切斷的加速訊號」，不能當唯一依據，而且必須先用自錄的面對面語料驗證。

「同一套 VAD、依情境切換句尾策略」可行。前提是 VAD 層要自己掌控 Silero 的狀態與重置。pipecat 每約 2 秒在 QUIET 時重置一次，這是 pipecat 自己的策略，不是 Silero 官方的用法：官方契約是「每個音訊串流或 session 重置一次」，每次輸入固定 512 samples，前面接上前一次的 64 samples context。

> **標記說明：** 【官方宣稱】＝模型作者的論文、README、model card 或部落格自報；【獨立評測：X】＝作者以外的 X 量測；【競品評測，非中立：X】＝由競爭廠商量測。arXiv 一律標「尚未經同行審查」。不在指定清單內的發表場所會直接標出（WASPAA、LREC-COLING、IWSDS、IROS、SIGDIAL、IJCNLP-AACL demo）。「推論」表示這份報告自己的判斷，沒有來源直接支持。

---

## 問題 1：沒有串流 VAD 能證明會拒絕歌聲，Silero v6.2 是最穩的預設

### 候選 VAD 比較表

限制條件欄位：串流／低延遲／CPU 即時／免 PyTorch／語言（日英；B 另需中）／BGM 與歌聲區分／MIT 相容授權。

| 方法 | 原理（一句話） | 情境 A | 情境 B | 串流 | 低延遲 | CPU 即時 | 免 PyTorch | 日／英／中 | 區分 BGM／歌聲 | MIT 相容 | 授權 | 來源 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| **Silero VAD v6.2** | 帶循環狀態（`(2,B,128)`）的小型網路（約 260K 參數），每 32 ms 輸出一個語音機率 | 適合（首選） | 適合（首選） | 是 | 是（32 ms 窗） | 是 | 是（官方 ONNX，context 要自己接） | 是（宣稱 6000+ 語言） | 部分：純噪音大幅改善；音樂沒進訓練，歌聲是已知弱點 | 是 | MIT（權重同 repo） | [README](https://github.com/snakers4/silero-vad)、[LICENSE](https://raw.githubusercontent.com/snakers4/silero-vad/master/LICENSE) |
| **FireRedVAD（streaming）** | 8 層 DFSMN、約 0.6M 參數、完全 causal，10 ms frame | 候選 | 候選 | 是 | 是（無官方延遲數字） | 不明（無 RTF；推論可行） | 部分：官方只有 PyTorch，需社群 ONNX 或自行匯出 | 是（宣稱 100+ 語言） | 否：「Voice = speech + singing」；純音樂算非語音 | 是 | Apache-2.0 | [arXiv 2603.10420](https://arxiv.org/html/2603.10420v1)、[HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md) |
| **FireRedVAD mVAD／AED** | 同家族非串流模型，分別輸出 speech／singing／music 機率（可重疊） | 第二階段過濾候選 | 不適合 | 否 | 否 | 不明 | 部分（同上） | 是（宣稱） | 是（設計上分開；無量化數據） | 是 | Apache-2.0 | [HF card](https://huggingface.co/FireRedTeam/FireRedVAD/blob/main/README.md) |
| **TEN VAD** | 架構未公開的輕量網路，含 LPCNet 式 pitch 特徵，hop 10／16 ms | 不明 | 可考慮（延遲低） | 是 | 是（宣稱轉換更快，無數值） | 是 | 是（C／ONNX；Windows 上的 Python binding 待確認） | 不明（未列語言） | 不明（無任何音樂或歌聲宣稱） | **否**（附加限制條款） | Apache 2.0 + Agora 附加條件 | [README](https://github.com/TEN-framework/ten-vad)、[LICENSE](https://raw.githubusercontent.com/TEN-framework/ten-vad/main/LICENSE) |
| WebRTC VAD | 傳統訊號處理（TEN 稱為 pitch-based），只輸出二元結果 | 不適合 | 只當基線 | 是 | 是（10–30 ms） | 是 | 是（C） | 與語言無關 | 否 | 不明（本次未查證） | 未查證 | [py-webrtcvad](https://github.com/wiseman/py-webrtcvad) |
| NeMo MarbleNet v2.0 | 91.5K 參數 CNN，每 20 ms 輸出一次 | 不適合 | 不明 | 不明（未說明） | 不明 | 是（推論） | 部分（匯出需要 NeMo／PyTorch） | **否**（訓練資料沒有日語） | 不明（訓練資料沒提到音樂） | 有條件（NVIDIA Open Model License，非 OSI） | NVIDIA OML | [HF card](https://huggingface.co/nvidia/Frame_VAD_Multilingual_MarbleNet_v2.0) |
| pyannote segmentation-3.0 | 以 10 s 視窗做 powerset 說話者分段 | 不適合 | 不適合 | 否 | 否 | 不明 | 否 | 不明 | 不明 | 是（gated） | MIT | [HF card](https://huggingface.co/pyannote/segmentation-3.0) |
| FunASR FSMN-VAD | FSMN、0.4M 參數 | 不適合 | 不適合 | 是 | 不明 | 是 | 是（GGUF） | 否（只有中英） | 否（FireRed 量到 FAR 44%，非中立） | 不明（授權說法互相矛盾） | MIT 程式碼／權重授權矛盾 | [FunASR](https://github.com/modelscope/FunASR)、[MODEL_LICENSE](https://github.com/modelscope/FunASR/blob/main/MODEL_LICENSE) |
| Picovoice Cobra | 專有深度學習 VAD | 不適合 | 不適合 | 是 | 是 | 是 | 是 | 不明 | 不明 | **否**（需 AccessKey 並連線驗證） | 專有引擎 | [GitHub](https://github.com/Picovoice/cobra)、[FAQ](https://picovoice.ai/docs/faq/general) |
| SR-SAD（研究原型） | 3 層 BiGRU，專門在有歌聲時偵測說話 | 不適合（目前） | 不適合 | 否（作者自承 2 s chunk 無法即時） | 否 | 不明 | 不明 | 不明 | 是（設計目標） | 不明 | 不明 | [arXiv 2512.09713](https://arxiv.org/html/2512.09713)（**WASPAA 2025，不在清單內**） |
| Spleeter 人聲分離前端（sherpa-onnx） | 先分離 vocals／accompaniment，再送進 VAD | 實驗性 | 不適合 | 不明（官方是離線 API） | 否（需要 chunk 緩衝） | 是（RTF 約 0.08） | 是（ONNX） | 與語言無關 | 部分：能去 BGM，但歌聲會留在 vocals（推論） | 不明（權重授權未查） | 未查證 | [sherpa-onnx](https://k2-fsa.github.io/sherpa/onnx/source-separation/models.html) |

### 三家互相宣稱勝出，唯一的中立數據只涵蓋 Silero

這個領域的準確度數字幾乎都是「自己測自己、自己贏」，必須逐一標明來源：

- **FireRed 的數據**【官方宣稱，尚未經同行審查；對 Silero、TEN 而言是競品評測，非中立：小紅書 FireRed Team】：在自建的 FLEURS-VAD-102（102 種語言、9,443 檔）上，FireRedVAD 的 F1 是 **97.57%**，Silero 95.95%、TEN 95.19%；誤報率（FAR）**2.69% vs. 9.41% vs. 15.47%** ([arXiv 2603.10420](https://arxiv.org/html/2603.10420v1))。這個測試集仍標示「coming soon」，沒有公開，也沒寫比較的是哪個 Silero 版本。
- **Silero 的數據**【官方宣稱；對 FireRed、TEN 而言是競品評測，非中立：Silero 團隊】：在 Silero wiki 上，排名反過來。Multi-Domain ROC-AUC 是 Silero v6 **0.97**、FireRed VAD 0.94、TEN 0.93、FireRed Streaming 0.93；純噪音集 ESC-50 的正確率是 Silero v6 **0.87**、FireRed 0.60、TEN 0.42、FireRed Streaming 0.39 ([Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics))。
- **TEN 的數據**【官方宣稱】：只有 PR 曲線圖，沒有數值。宣稱 Silero 在語音轉非語音時有「數百毫秒」延遲 ([TEN VAD README](https://github.com/TEN-framework/ten-vad))。

這三組結果互相矛盾，正好說明各家都挑了對自己有利的資料。

唯一同行審查、作者與廠商無關的數據，是 Stellenbosch 大學的 Karan et al.【**獨立評測：Karan, Jansen van Vüren, de Wet, Niesler，Interspeech 2024**】。他們在 AVA-Speech（160 部 YouTube 電影）上評測現成的 Silero，test AUC **81.9%**；在 10% FPR 下，TPR 只有 **26.5%** ([Interspeech 2024](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf))。解讀這組數字時要注意，該研究只把「Clean-Speech」當正類，Speech+Music 算負類。所以低分的意思是「**Silero 會對帶 BGM 的說話觸發**」，不是「Silero 會誤判純音樂」。對情境 A 來說，這反而是想要的行為：BGM 下的對白也要翻譯。

另有一篇比較 TEN 與 Silero 的預印本（arXiv 2610.14607）。作者機構不明，**不符合本報告的來源標準**，所以不拿來當結論依據。只附記它的方法學提醒：Silero 不用原生的 32 ms 窗時分數會崩，評測協定會大幅改變結果 ([arXiv 2610.14607](https://arxiv.org/abs/2610.14607))。

在歌聲這個核心問題上，證據一面倒，而且都是負面的：

- Silero 維護者明說：「**we did not have music in the training data**」，歌曲、高音、卡通聲是已知問題【官方宣稱】([issue #565](https://github.com/snakers4/silero-vad/issues/565#issuecomment-2453774166))。v6.0 release notes 也把「music with human voice-like instruments」和「very high pitched voices (artificial, cartoons…)」列為仍存在的問題 ([v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0))。動畫高音角色正好落在這裡；v6.2 宣稱這部分已改善，但「no metrics update」 ([v6.2 release](https://github.com/snakers4/silero-vad/releases/tag/v6.2))。
- FireRedVAD 的二元模型**刻意把歌聲當正類**，只有非串流的 mVAD 才分出 singing 類別 ([arXiv 2603.10420](https://arxiv.org/html/2603.10420v1))。
- Fraunhofer IIS 的研究【**WASPAA 2025，不在清單內**】證實，只用語音訓練的 SAD 會把大部分歌聲判成語音：純語音訓練的基線，歌聲正確拒絕率只剩 **0.03–0.43** ([arXiv 2512.09713](https://arxiv.org/html/2512.09713))。不過這篇沒有測 Silero 或 TEN。

### 情境推薦：兩個情境都用 Silero，FireRedVAD 做 A/B 對照

**情境 A 推薦 Silero VAD v6.2 為主、FireRedVAD streaming 為對照組。**

Silero 是唯一同時符合官方 ONNX、免 PyTorch、MIT、有串流模式這四項的選項。它也有唯一一份中立的電影音訊評測。從維護者說法和 Karan 2024 的結果推論，它在 BGM 下仍會對說話觸發。

FireRedVAD 的優勢是官方宣稱誤報率低，而且有 mVAD 這條「把歌聲分出去」的路，授權也是乾淨的 Apache-2.0。成本是要自己匯出 ONNX，或採用社群移植（[subthx/firered-vad-onnx](https://huggingface.co/subthx/firered-vad-onnx)），而且移植版與官方輸出是否一致沒有人驗證過。

歌聲段（OP／ED、歌回）沒有任何串流 VAD 能可靠擋下。有兩條路：

- 接受歌詞會被送去翻譯，再由下游的 ASR 信心度過濾；
- 對已切出的片段跑一次 FireRedVAD mVAD 做第二階段判斷，代價是增加一段非串流延遲（推論，未驗證）。

**情境 B 推薦 Silero VAD v6.2。**

面對面口譯的干擾主要是環境噪音，這正是 Silero v5/v6 的改善重點。官方宣稱 v6「16% less errors on noisy real-life data」([v6.0 release](https://github.com/snakers4/silero-vad/releases/tag/v6.0))，ESC-50 的數字也是 Silero 自評領先。TEN VAD 宣稱延遲更低，是 B 唯一值得一試的替代品，但沒有獨立證據顯示它比較準。授權方面，「You may not Deploy the ten-vad in a way that competes with Agora's offerings」與「Derivative Works … remain subject to this Open Source License」([LICENSE](https://raw.githubusercontent.com/TEN-framework/ten-vad/main/LICENSE)) 會傳遞給下游，所以頂多做成使用者自行安裝的選用外掛。

兩個情境用同一個 Silero 模型完全可行，差別只在參數：A 可能要較高的 `threshold`；B 要較短的 `min_silence`。重置策略也要依情境調整，見最後一節。

### 查不到或不確定的地方

| 項目 | 狀態 |
|---|---|
| 把 Silero v5/v6、TEN、FireRedVAD 放在同一個中立 benchmark 的同行審查論文 | 2023 年後沒有找到 |
| 任何 VAD 在日語動畫或 VTuber 音訊（BGM + 歌聲 + 多人）上的評測 | 沒有找到 |
| Silero、TEN、FireRed 在歌聲資料上的第三方量化結果 | 沒有找到；FireRed mVAD 的 singing／music 偵測也沒有公開 benchmark |
| FireRedVAD、MarbleNet、Cobra 的演算法延遲（ms） | 沒有官方數字；TEN 只有質性描述 |
| FireRedVAD 社群 ONNX 與官方 PyTorch 輸出是否一致 | 沒有驗證 |
| TEN VAD 的 Python binding 能否在 Windows 上使用 | README 只寫針對 Linux x64 最佳化 |
| WebRTC VAD、Spleeter 權重、FSMN-VAD 權重的授權 | 未查證或說法矛盾 |
| FLEURS-VAD-102 是否已公開 | 未確認，所以無法重現 FireRed 的數字 |

### 驗證方法

**情境 A：**沿用 `experiments/pipecat_vad/` 的「片段之間插入已知長度間隔」做法，但有一處要改：**間隔不要用數位靜音，改填已知長度的非語音素材**，例如純 BGM、歌聲、效果音、室內底噪。數位靜音會讓所有 VAD 都顯得很好，也最容易觸發 pipecat 的閘控重置（見最後一節）。

素材來源可用 [MUSAN](https://www.openslr.org/17/)（CC BY 4.0）的音樂與噪音，歌聲可取自 MoisesDB／MUSDB18-HQ（授權未查證，只在本機使用）。另外依 CSJ-anime 的做法 ([arXiv 2008.12048](https://arxiv.org/pdf/2008.12048))，把語音片段與 BGM 以 SNR 5／0／−5 dB 混音，取得「說話 + BGM」的正類。最後再用幾分鐘未經處理、人工標註的真實動畫與直播片段做對照。

要分條件計分：clean／speech+BGM／singing／music-only／SFX 各自報告。指標包括：

- frame-level AUC、TPR@FPR 5% 與 10%（同 Karan 2024 的高精確率操作點）；
- 歌聲拒絕率（參考 AUC_SiRR 的定義，見 [arXiv 2512.09713](https://arxiv.org/html/2512.09713)）；
- 每分鐘純音樂誤觸發次數；
- **整句漏抓率**；
- onset 與 offset 延遲。

每個 VAD 都要用自己的原生 chunk 並保留串流狀態：Silero 512、TEN 160 或 256、FireRed 10 ms。重置策略要當成獨立變因來測。

**情境 B：**用筆電內建麥克風錄日英中的面對面對話，再加上咖啡廳、風扇、鍵盤等噪音條件。取得正確答案的兩個方法（推論，建議做法）：

- 錄音時讓每位說話者同時戴一支近講麥克風，用近講音軌加人工校正，產生參考標註；
- 用喇叭播放已知片段與已知長度的間隔，用筆電麥克風重新錄製，開頭放一個同步 chirp 來對齊樣本位置。這樣同時有真實房間聲學，又有樣本級的正確答案。

---

## 問題 2：句尾判斷要分兩套，聲學端點模型只能當 B 的加速器

### 候選方法比較表

| 方法 | 原理（一句話） | 情境 A | 情境 B | 串流 | 低延遲 | CPU 即時 | 免 PyTorch | 日／英／中 | 噪音／BGM | MIT 相容 | 授權 | 來源 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 固定停頓規則（現行：Silero + 0.6 s + 最長長度） | 尾端靜音超過門檻就切；超過長度就強制切 | 基線 | 基線 | 是 | 否（延遲 ≥ 門檻；句中停頓會誤切） | 是 | 是 | 與語言無關 | 取決於 VAD；BGM 會讓停頓消失 | 是 | — | [eot-bench](https://github.com/livekit/eot-bench) |
| sherpa-onnx endpoint 規則 | 依 ASR 是否已出字搭配尾端靜音（預設 1.2／2.4／20 s） | 可 | 可 | 是 | 否（預設值偏長） | 是 | 是 | 取決於 ASR 模型 | 取決於 ASR | 是 | Apache-2.0（框架） | [endpoint.h](https://raw.githubusercontent.com/k2-fsa/sherpa-onnx/master/sherpa-onnx/csrc/endpoint.h)、[GitHub](https://github.com/k2-fsa/sherpa-onnx) |
| **文字句尾／標點模型**（1-800-BAD-CODE `punct_cap_seg_47_language`） | 對已 commit 的 ASR 文字預測標點與句界 | **推薦** | 輔助 | 是（在候選停頓時呼叫） | 是（ms 級，推論） | 是 | 是（ONNX + SentencePiece） | 日英中都支援，但**日文沒有指標** | 不直接受 BGM 影響，但受 ASR 錯誤影響 | 是 | Apache-2.0 | [HF card](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language) |
| sherpa-onnx 標點模型（online en CNN-BiLSTM；CT-Transformer zh-en） | 串流或離線文字標點 | 只限英／中 | 輔助（英、中） | 是／部分 | 是 | 是 | 是 | **沒有日文** | 文字端 | 不明（權重授權未寫） | 未查證 | [sherpa-onnx punct](https://k2-fsa.github.io/sherpa/onnx/punctuation/pretrained_models.html) |
| **Smart Turn v3.x** | Whisper-tiny encoder + 線性分類器，看最多 8 s 的音訊，判斷這一輪是否說完 | 不適合 | **有條件適合** | 是（VAD 靜音後才觸發） | 是（官方依機器 9–95 ms；專案實測約 53 ms） | 是 | 是（ONNX） | 是（23 語；中文最弱） | v3.2 加了咖啡廳／辦公室噪音；沒有 BGM | 是 | BSD-2-Clause | [GitHub](https://github.com/pipecat-ai/smart-turn)、[HF](https://huggingface.co/pipecat-ai/smart-turn-v3) |
| VAP／MaAI | 雙聲道 Transformer 預測雙方未來的發話活動 | 不適合 | 研究選項 | 是（逐幀） | 是 | 是（論文宣稱） | **否** | 是（日英中） | 有 noise-robust 版本 | 程式碼是；權重不明 | MIT（程式碼） | [maai](https://github.com/maai-kyoto/maai)、[arXiv 2403.06487](https://arxiv.org/abs/2403.06487) |
| LiveKit turn detector（文字版） | Qwen2.5-0.5B 預測逐字稿下一個 token 是否為 `<|im_end|>` | 不適合 | 不適合 | 部分（要等 ASR） | 部分 | 是 | 是 | 是 | 文字端 | **否**（只能搭配 LiveKit Agents 使用） | LiveKit Model License | [LICENSE](https://huggingface.co/livekit/turn-detector/blob/main/LICENSE) |
| LiveKit v1／v1-mini（音訊版） | 音訊 encoder + LLM backbone | 不適合 | 不適合 | 是 | v1 是（543 ms@5%） | v1 需要雲端；mini 在 CPU 上 | 不明 | 是 | 不明 | **否／不明**（說法矛盾） | LiveKit Model License | [docs](https://docs.livekit.io/agents/logic-structure/turns/turn-detector/) |
| TEN Turn Detection | Qwen2.5-7B 文字三分類（finished／unfinished／wait） | 不適合 | 不適合 | 部分 | 不明 | 否（7B） | **否** | **沒有日文** | 文字端 | 不明（Apache + 附加限制） | Apache 2.0 + 限制 | [GitHub](https://github.com/TEN-framework/ten-turn-detection) |
| Parakeet Realtime EOU 120M | 串流 RNNT ASR 輸出 `<EOU>` token | 不適合 | 不適合 | 是 | 是（p50 160 ms） | 不明（只在 GPU 上測） | 否（官方）／有第三方 ONNX | **只有英文** | 不明 | 不明 | NVIDIA OML | [HF card](https://huggingface.co/nvidia/parakeet_realtime_eou_120m-v1) |
| Kyutai STT semantic VAD | ASR 同時輸出「是否說完」的機率 | 不適合 | 不適合 | 是 | 部分（500 ms） | 不明（1B） | 是（Rust） | **只有英法** | 不明 | 是（CC-BY 權重） | MIT／Apache／CC-BY | [Kyutai](https://kyutai.org/stt) |
| ASR 整合式語意端點（Semantic VAD；Google 語意 segmenter） | 在串流 ASR 裡預測句尾標點或 EOS，取代等待長靜音 | 概念採用 | 概念採用 | 是 | 是 | 不明 | 沒有開源 | 中文內部資料／英文 | 不明 | 沒有開源 | — | [Interspeech 2023](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.html)、[arXiv 2305.18419](https://arxiv.org/abs/2305.18419) |
| SHAS | wav2vec2 frame 分類 + 有長度上限的 pDAC 切分 | 不適合（離線） | 不適合 | 不明 | 不明 | 否（推論） | 否 | 只有英文權重 | 沒有評測 | 程式碼是 | MIT（程式碼） | [GitHub](https://github.com/mt-upc/SHAS) |
| LocalAgreement／AlignAtt（字幕 commit 策略） | 連續幾次假設的共同前綴才 commit；或依 attention 停止解碼 | 參考 | 不適合（延遲） | 是 | 否（約 3.3 s） | LocalAgreement 取決於 backend；AlignAtt 否 | 部分／否 | 99 語 | 不明 | 是 | MIT | [whisper_streaming](https://github.com/ufal/whisper_streaming)、[SimulStreaming](https://github.com/ufal/SimulStreaming) |

### 語意端點能降延遲 23–53%，但證據都來自語音搜尋或演講

同行審查文獻確實支持「不要只看停頓長度」。以下各項都是【官方宣稱】，作者評估自己的方法：

- **Semantic VAD**（Interspeech 2023）：在串流 conformer 加一個標點 head，區分句尾與非句尾標點，遇到句尾就不必等長尾靜音。平均延遲降低 **53.3%**，CER 沒有明顯變差 ([Interspeech 2023](https://www.isca-archive.org/interspeech_2023/shi23c_interspeech.html))。但資料集是內部資料。
- **Google 的 unified ASR + endpointing**（SLT）：median endpoint latency 降低 **120 ms（30.8%）**，p90 降低 170 ms（23.0%），WER 沒有退步 ([arXiv 2211.00786](https://arxiv.org/abs/2211.00786))。
- **Google 的雙向 LM 蒸餾 segmenter**（Interspeech 2023）：在 YouTube captioning 上 WER 相對改善 3.2%，句尾延遲中位數降低 60 ms ([arXiv 2305.18419](https://arxiv.org/abs/2305.18419))。
- **McCarthy et al.**（EMNLP 2023 Findings）：用 LLM 加有限狀態約束切分 ASR 文字，在 TED 翻譯上平均 **+2.9 BLEU** ([ACL Anthology](https://aclanthology.org/2023.findings-emnlp.19))。

但也有反面證據。IWSLT 2023 的一篇獨立比較中，商用 VAD 與 SHAS 的分數相當接近【獨立評測：該文作者；表格指標名稱待核對原 PDF】([IWSLT 2023](https://aclanthology.org/2023.iwslt-1.2.pdf))。這表示一個好的 VAD 加上合併與長度規則，可能已經拿到大部分聲學切分的增益。下一步的收益應該來自「切在語意句尾」，也就是文字端，而不是更細的聲學切分（推論）。

對話換手（B）方面，唯一公開、多語（含日中英）、以「固定誤切率下的延遲」為主指標的基準是 LiveKit 的 eot-bench。它的英文結果【**競品評測，非中立：LiveKit**；資料是真實的人對 agent 語音】如下 ([eot-bench](https://github.com/livekit/eot-bench))：

| 方法 | false cutoff @300 ms | false cutoff @600 ms | 達到 5% 誤切所需延遲 |
|---|---|---|---|
| 純 VAD 停頓 | 55.6% | 21.7% | **1600 ms** |
| Smart Turn v3.2 | 35.2% | 14.8% | **1051 ms** |
| VAP | 46.9% | 14.6% | 1131 ms |
| LiveKit v1（自家，需雲端） | 9.9% | — | 543 ms |

換算成口譯延遲：純停頓規則要壓到可接受的誤切率，大約要等 1.6 s；Smart Turn 能省下約 0.5 s。

方向相反的訊號也存在。一篇尚未經同行審查、作者機構在筆記中未確認的預印本，做了三種模態的消融實驗。結果是聲學 + 韻律特徵最好（F1 0.93、中位延遲 400 ms），**加入文字反而讓提早觸發變多** ([arXiv 2609.11066](https://arxiv.org/abs/2609.11066))。這提醒我們：在 B 這種低延遲情境中，文字訊號受 ASR partial 不穩定影響，不一定比聲學訊號好。

### Smart Turn 用在情境 B：有條件適合，證據不足以當主依據

Smart Turn v3.x 是唯一同時通過授權（BSD-2）、執行環境（ONNX，官方 `inference.py` 不 import torch）、語言（23 語含日英中）三關的現成端點模型 ([GitHub](https://github.com/pipecat-ai/smart-turn))。和情境 A 相比，B 更接近它的訓練分布：都是口語對話、都是單一說話者的一輪。

以下三個理由讓它不能當唯一依據：

1. **官方數字不能代表實際表現。**v3.0 各語言準確率【官方宣稱，廠商部落格】是日文 **96.76%**、英文 94.31%、中文 **88.57%**；中文在 23 種語言中排倒數第 4 ([Daily v3 blog](https://www.daily.co/blog/announcing-smart-turn-v3-with-cpu-inference-in-just-12ms/))。測試集來自官方自己的資料；官方也承認早期版本「relying heavily on TTS-generated data」，v3.1 新增的真人資料只有英文與西班牙文 ([Daily v3.1 blog](https://www.daily.co/blog/improved-accuracy-in-smart-turn-v3-1/))。作者以外的評測都看到退化：eot-bench 中它要約 1 s 才能把誤切壓到 5%；孟加拉語預印本測到 **69.28%**【獨立評測：BanglaTurn 作者，尚未經同行審查，機構未確認】([arXiv 2609.29371](https://arxiv.org/pdf/2609.29371))；專案自己在 A 的 12 段中只有 1 段通過。
2. **任務不一樣。**Smart Turn 判斷的是「這一輪說完、換你回應」。口譯要的是「可以翻譯的句子單位」，一輪可能包含好幾句；另一方插話造成的換手，它也看不到，因為它只讀「目前這一段」音訊，不建模另一位說話者 ([GitHub](https://github.com/pipecat-ai/smart-turn))。
3. **收音條件不一樣。**筆電麥克風是遠場收音、有回響，跟訓練資料的近講人聲不同。v3.2 雖然加了咖啡廳／辦公室噪音，但沒有公開 SNR 或有無噪音的對照數字 ([Daily v3.2 blog](https://www.daily.co/blog/smart-turn-v3-2-handling-noisy-environments-and-short-responses/))。

所以建議的用法是**加速器**，而不是裁判：

- 短停頓（例如 0.3 s）時，若 Smart Turn 判定「完整」的機率很高，就提早切；
- 否則等到固定停頓上限（例如 0.8–1.0 s），並保留最長長度安全網。

這些門檻是推論的起點，要在 B 的自錄語料上掃描決定。

概念上最貼近 B 的是 VAP。它以人對人雙人對話為對象，多語版在英、中、日上都與單語模型相當【官方宣稱；**LREC-COLING 2024，不在清單內**】([arXiv 2403.06487](https://arxiv.org/abs/2403.06487))，也宣稱能在 CPU 上即時運作【**IWSDS 2024，不在清單內**】([arXiv 2401.04868](https://arxiv.org/abs/2401.04868))。但它需要 PyTorch，權重授權也沒逐一確認，只能列為研究選項。

### 情境推薦與「同一套 VAD、切換句尾判斷」的可行性

**情境 A 推薦：「VAD 候選停頓 + 文字句尾訊號 + 最長長度安全網」。**

做法如下：

- VAD 偵測到較短的候選停頓（例如 0.3–0.4 s）時，檢查已 commit 的 ASR 文字是否在句尾。句尾訊號可以來自 ASR 自己輸出的「。」「？」（推論：要先確認所選 ASR 會不會輸出標點），或來自 1-800-BAD-CODE 的 47 語標點模型（Apache-2.0、ONNX）。是句尾就切。
- 停頓很長、但文字判斷句子還沒結束時，延後切，直到最長長度。
- 達到最長長度時，在已觀察到的最佳停頓點切，而不是硬切。Silero `get_speech_timestamps` 的 `use_max_poss_sil_at_max_speech` 與 `min_silence_at_max_speech` 參數就是這個思路（依參數名推論）([utils_vad.py](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L281-L357))。

字幕對延遲的容忍度比口譯高，所以 A 可以付出等 ASR 出字的時間。這也呼應 Semantic VAD 與 McCarthy 2023 的證據方向。要注意，1-800-BAD-CODE 的作者自承它以新聞資料訓練、「unlikely to be of production quality」，而且沒有日文指標 ([HF card](https://huggingface.co/1-800-BAD-CODE/punct_cap_seg_47_language))，在動畫台詞上的效果必須實測。如果要減少字幕跳動，可以參考 Whisper-Streaming 的 LocalAgreement：只把已 commit 的前綴送進句尾判斷 ([whisper_streaming](https://github.com/ufal/whisper_streaming)，**IJCNLP-AACL 2023 demo，不在清單內**)。

Smart Turn **不建議**用在 A。專案實驗 1/12 的結果，加上它以人機對話為設計對象、model card 完全沒有提到 BGM 或非對話音訊 ([HF](https://huggingface.co/pipecat-ai/smart-turn-v3))，都支持這個判斷。

**情境 B 推薦：「Silero 停頓 + Smart Turn 加速」為主，「ASR 標點投票」為輔。**

主要路徑是兩段式門檻（見上一節）。輔助訊號特別針對中文：Smart Turn 在中文最弱，而中英文標點模型有現成選項。但根據 arXiv 2609.11066 的提醒，文字訊號只能拿來「否決提早切」或「確認切點」，不宜單獨觸發。

**同一套 VAD、依情境切換句尾判斷：可行，也建議這樣設計。**兩個情境都需要 16 kHz 單聲道；Silero 每 32 ms 窗耗時不到 1 ms（專案實測約 0.4 ms），Smart Turn 只在停頓時才跑（約 53 ms），CPU 預算綽綽有餘。

共用的 VAD 層應提供三樣東西：

- 每 32 ms 的機率與**樣本計數器**；
- 以樣本索引為準的 ring buffer，供 Smart Turn 取「語音起點前 500 ms 到目前位置」；
- 可設定的狀態重置策略。

句尾判斷做成可替換的策略物件：A 用「停頓 + 文字 + 最長長度」，B 用「停頓 + Smart Turn + 最長長度」。

兩個情境不能共用的是參數與重置策略。A 的音樂會讓 Silero 狀態漂移，pipecat 的觀察是「music strongly at either sample rate」([pipecat silero.py](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L21-L39))；B 主要是 16 kHz 室內噪音，漂移較輕。另外，pipecat 的 `BaseSmartTurn` 用 `time.monotonic()` 標記緩衝時間，離線快速餵入時，pre-speech 範圍會算錯 ([base_smart_turn.py](https://raw.githubusercontent.com/pipecat-ai/pipecat/main/src/pipecat/audio/turn/smart_turn/base_smart_turn.py))。共用層應該改用樣本索引，直接呼叫無狀態的 `predict_endpoint` ([inference.py](https://github.com/pipecat-ai/smart-turn/blob/main/inference.py))。

### 查不到或不確定的地方

| 項目 | 狀態 |
|---|---|
| Smart Turn 在面對面、多人、遠場麥克風情境的任何評測 | 沒有找到 |
| Smart Turn v3.1／v3.2 的日文、中文新數字 | 官方只說「與 v3.0 相近」；HF 上的 benchmarks 資料夾未讀 |
| 中立學術團隊對 Smart Turn、LiveKit、TEN Turn Detection 的同行審查評測 | 沒有找到；現有的都是競品評測 |
| 「VAD 候選停頓 + 文字句尾」在串流字幕翻譯上同時量化延遲與 BLEU／COMET | 沒有找到；多數論文把句尾預測整合在 ASR 裡 |
| 日文口語（動畫、直播）的標點或句界模型評測 | 沒有找到；1-800-BAD-CODE 沒有日文指標 |
| 2023 年後針對動畫、VTuber 句子切分的 Interspeech／ICASSP／ACL 論文 | 沒有找到 |
| sherpa-onnx 標點模型權重、FunASR CT-Transformer 權重的授權 | 未取得全文 |
| VAP／MaAI 權重授權、TEN Turn Detection 附加限制的全文 | 未查證 |
| 免費且授權寬鬆的日語人對人對話 turn 標註語料 | 沒有找到；CEJC、CSJ 都要付費 |

### 驗證方法

**一個關鍵修正：**現有的合成音軌在片段之間插入數位靜音，每個片段邊界都會變成一個長而乾淨的停頓。這對停頓規則來說太簡單，**無法拿來評測句尾判斷**。句尾評測必須用**自然連續**的語音，裡面要有句中換氣、猶豫停頓這類「不該切」的點。

評測協定建議採用 eot-bench 的方式 ([eot-bench](https://github.com/livekit/eot-bench))：

- 在每個 ≥100 ms 的停頓做一次因果判斷，標成 `eot`（真句尾）或 `hold`（句中停頓）；
- 主指標是「固定誤切率（5%、10%）下的延遲 P50／P90」，以及「固定延遲（300、600 ms）下的誤切率」；
- 畫出 Pareto 曲線，比較純停頓（0.3–1.2 s 掃描）、Smart Turn 兩段式、文字投票三種策略。

**情境 A：**把連續的動畫與直播片段（含 BGM）用 ASR 加人工校對產生逐字稿，依句末標點標出 `eot`，其餘停頓標 `hold`。除了上述指標，再報告「強制切斷率」與字幕單位長度分布；條件允許時，對下游翻譯算 COMET。

**情境 B：**用筆電麥克風錄的日英中對話，標註方式相同，另外分開計算「說話者換手」與「同一人句與句之間」兩類切點。[eot-bench-data](https://github.com/livekit/eot-bench)（Apache-2.0，含日中英）可以當公開的對照組，但它是人對 agent 語音，不能取代自錄語料。

評測 Smart Turn 時，要繞過 `BaseSmartTurn`，以樣本索引切出輸入，餵給 `predict_endpoint`。

---

## Silero VAD 串流狀態處理：官方契約是「每串流重置一次」，pipecat 的 2 秒重置是它自己的取捨

### 官方規定的輸入、前後文與狀態

自 v5 起，**每次呼叫必須剛好是 512 samples（16 kHz）或 256 samples（8 kHz），也就是 32 ms**。v5.0 release notes 寫「only with fixed 256 and 512 sample windows」([v5.0 release](https://github.com/snakers4/silero-vad/releases/tag/v5.0))。官方 `OnnxWrapper` 遇到其他大小會丟出 `ValueError` ([utils_vad.py L109-L115](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L109-L115))。但直接呼叫原始 ONNX 時，最多 639 samples 都不會報錯，維護者只說其他大小「not recommended … quality may suffer」([issue #750](https://github.com/snakers4/silero-vad/issues/750#issuecomment-3818116832))。所以自己寫的 wrapper 必須自行驗證長度，並把任意大小的傳輸 frame 重新緩衝成剛好 512 samples。

第二個常被漏掉的細節是**前後文（context）**。每次呼叫要在前面接上前一次輸入的最後 **64 samples**（8 kHz 時是 32），所以實際送進 ONNX 的長度是 576。context 一開始是零，每次呼叫後更新 ([utils_vad.py L117-L141](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L117-L141))。JIT 模型在內部處理這件事，ONNX 使用者要自己做。少了它，機率會塌到約 0.001；owner 也承認「most likely people will keep missing it」([issue #771](https://github.com/snakers4/silero-vad/issues/771#issuecomment-4333159230))。

循環狀態是一個 `(2, batch, 128)` 的 float32 tensor，每次呼叫都要寫回去 ([utils_vad.py L103-L107, L131-L135](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L103-L107))。`reset_states()` 會**同時把 state 歸零、清空 context**。所以重置後的第一個窗，看到的是「64 個零 + 真實音訊」，加上冷啟動的狀態。

**什麼時候重置**，官方程式碼、wiki 與維護者回答大致一致：

- 在串流或 session 開始與結束時各重置一次。`VADIterator.__call__` 從不自己重置，`get_speech_timestamps` 也只在整段音訊開始前重置一次 ([utils_vad.py L637-L689](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L637-L689))。
- owner 的回答是「The state should be reset each time the audio stream changes or ends」([discussion #572](https://github.com/snakers4/silero-vad/discussions/572))，以及「should be called when the new audio stream starts and / or the old one ends」([discussion #726](https://github.com/snakers4/silero-vad/discussions/726))。
- 唯一的例外：被問到要不要在每次 `speech_end` 後重置時，owner 回「Ideally yes」([discussion #791](https://github.com/snakers4/silero-vad/discussions/791#discussioncomment-17852352))。
- **官方從來沒有建議定時重置。**

其他官方預設：`threshold=0.5`、`neg_threshold = threshold − 0.15`、`min_silence_duration_ms=100`、`speech_pad_ms=30`。v6.2.3 的 `VADIterator` 沒有 `min_speech_duration_ms` 與 `max_speech_duration_s`；`neg_threshold` 參數只在 master 上有 ([utils_vad.py](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L591-L689))。

取樣率只支援 8 kHz 與 16 kHz。16 kHz 的整數倍會在 wrapper 中以 `x[:, ::step]` 直接抽取 ([utils_vad.py L85-L101](https://github.com/snakers4/silero-vad/blob/066409f3d9db45a30d21dfcbf9147826ffdd7052/src/silero_vad/utils_vad.py#L85-L101))。這種抽取沒有低通濾波（推論），所以 48 kHz 的系統播放音訊最好先用正規的重採樣器轉成 16 kHz 單聲道。int16 轉 float 要用固定的 `1/32768`，不要對每個 chunk 做最大值正規化 ([issue #309](https://github.com/snakers4/silero-vad/issues/309#issuecomment-1486942666))。

### 不重置會變聾，重置又傷句首：官方沒有選邊

問題在於：「整個串流都不重置」在 v6.x 上也會出事，而且證據都在官方 repo 裡。

- owner 確認有一個長串流檔案會讓 VAD「breaks」，並說「We cannot understand why this happens」([discussion #726](https://github.com/snakers4/silero-vad/discussions/726))。
- 同一串討論中，一位第三方使用者回報：v6.2、16 kHz、在噪音中跑約 2.5 分鐘後，語音分數掉到 0.0–0.4；重置後「restores detection immediately」。
- issue #765 有人重現 8 kHz 下狀態穿過靜音後的漂移，維護者至今沒有回覆 ([issue #765](https://github.com/snakers4/silero-vad/issues/765))。

pipecat 的策略也因此反覆修改：

- **v1.12.0 以前**：每 5 s 依牆鐘時間重置，理由是「memory will keep growing」。這個理由不成立，因為 state 與 context 的大小是固定的（推論）。
- **PR #6202**：改成整個串流都不重置，理由是「about 60% of the reset positions that fell inside the utterance produced a stop before it ended」([PR #6202](https://github.com/pipecat-ai/pipecat/pull/6202))。
- **PR #6207（同一天）**：又改回在明確的非語音期間重置，原因是音樂會讓後面的語音漏抓「for 20 s or more」([PR #6207](https://github.com/pipecat-ai/pipecat/pull/6207))。

v1.14.0 的條件是：距上次重置至少 2 s、狀態為 QUIET、最近 5 個窗（約 160 ms）的機率都低於 0.1 ([silero.py L245-L278](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L245-L278))。pipecat 自己公布的代價只有「about 1% of utterances were detected 200–400 ms late」。

### 對 pipecat 自動重置問題的含意

**第一，專案觀察到的「整句漏抓」是 pipecat 策略造成的，不是 Silero 的正確用法。**而且這個效果比 pipecat 或 Silero 公布的任何數字都嚴重。可能的原因（推論）有兩個：

- 實驗音軌在片段之間插入**數位靜音**。這讓「QUIET + 連續 5 窗 < 0.1」幾乎在每個間隔都成立，重置因此系統性地落在句首之前；
- 每次重置同時清掉 64 samples 的 context，句首第一個窗看到的是零加冷啟動狀態。動畫輕聲起音、VTuber 氣音這類低機率起音，會被壓到門檻以下。

真實媒體很少出現完美的數位靜音，所以實驗呈現的是**最壞情況**。但它確實證明這個機制會造成整句漏抓。

**第二，kikitori 應該自己寫一層薄薄的 ONNX wrapper，自己掌控重置策略。**pipecat 自己的 `SileroOnnxModel` 只有數十行 NumPy 加上 context 處理 ([silero.py L116-L141](https://github.com/pipecat-ai/pipecat/blob/1f8f79e357bd8d5632ac2d7ef7a8199441836a85/src/pipecat/audio/vad/silero.py#L116-L141))。這樣也能省掉 pipecat 帶來的約 290 MB 依賴，以及覆寫私有方法 `voice_confidence()` 的維護風險。

建議把重置策略當成實驗變因，至少比較四種：

| 策略 | 依據 |
|---|---|
| (a) 只在音源切換或 session 開始時重置 | 官方契約 |
| (b) 只在 `speech_end` 後重置 | owner 的「Ideally yes」 |
| (c) 只在長時間非語音（例如 ≥10 s）後重置 | #726 使用者的經驗值，非官方 |
| (d) 熱備援切換 | 推論，未驗證 |

(d) 的做法是：重置時不直接清空正在使用的模型，而是用 ring buffer 裡最近約 0.5–1 s 的真實音訊預熱一個新 instance，在 QUIET 期間再切換過去。這樣同時避開「狀態漂移」與「冷啟動加零 context」兩個問題。

情境 A 有音樂，策略 (a) 的漂移風險最大；情境 B 是 16 kHz 室內噪音，(a) 或 (b) 可能就夠。

---

## 結論

這次調查真正改變的認識是：瓶頸不在「換一個更準的 VAD」。三家主要模型在彼此的測試集上互有輸贏，而且沒有一家能可靠地區分說話與歌聲。真正決定 kikitori 表現的有兩件事：一是 **VAD 狀態怎麼管理**，二是 **句尾判斷要不要引入文字或聲學語意訊號**。專案目前最大的已知缺陷「整句漏抓」，就來自前者；它可以靠自己寫 wrapper、自己決定重置策略來根除，不需要換模型。

第二個認識是：兩個情境的最佳句尾訊號來源不同。A 能容忍延遲，最可靠的語意訊號就在 ASR 文字裡；B 對延遲敏感，需要在 ASR 出字前就能判斷的聲學訊號。而現成工具中唯一授權合格的 Smart Turn，其官方數字與外部評測差距很大。所以下一步不是導入新模型，而是先建好兩套「有真實非語音間隔與自然句中停頓」的本地評測集，把 VAD 選型、重置策略、句尾策略三個變因分開量測。這三個問題目前都沒有中立的公開證據能回答。
