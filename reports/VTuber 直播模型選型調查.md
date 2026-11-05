# 用 Whisper 雙路徑撐起 VTuber 即時字幕

**結論：** 在「不用 PyTorch、只在本機跑、使用者自行下載、授權要能商用」這幾個限制下，ASR 的預設最穩是延續現有的 **CTranslate2 Whisper：large-v3-turbo 跑即時路徑、large-v3 跑完整路徑**。原因有三：授權是 MIT；涵蓋 20 種語言；訓練資料也沒有 ReazonSpeech 那種「限資訊解析」的條款。日文另外把 **kotoba-whisper-v2.0-faster** 列為專用的 A/B 候選。它在最接近直播口語的 ReazonSpeech 電視語料上 CER 是 11.6，large-v3 是 14.9；但在 JSUT、CommonVoice 上反而輸給 large-v3，而且這些數字都是作者自報。前處理**不要預設加降噪或音源分離**：同行審查的證據顯示，這兩者對 Whisper 的幫助很小、不穩定，甚至會引發 hallucination。比較該做的是「Silero VAD 閘控＋日文 hallucination 黑名單＋歌聲分類器」。翻譯預設用 **Qwen3.5 4B／9B（Apache-2.0）**，**HY-MT1.5** 當品質選項並加註地區授權限制（不適用於歐盟、英國、南韓）；zh-TW 走「模型輸出＋OpenCC」。這次調查最大的發現是一個證據缺口：沒有任何公開的 VTuber 直播語音資料集，沒有 ≤12B 開放模型在 ja→en 的獨立人評，也沒有任何 ja→zh-TW 基準。所以最後怎麼選，取決於 kikitori 自建的評測集；本報告最後一節給出一套可在本專案重現的評測方法。

> **標記說明：** 【論文宣稱】＝模型作者自己的論文、model card 或 repo 自報；【獨立評測】＝非模型作者的第三方量測（例如 WMT 主辦方、其他研究團隊）；arXiv 預印本一律標【未審查】。【推論】＝本報告自己的判斷，沒有來源直接支持。【專案內部】＝kikitori repo 裡的實測（程式註解或 experiments），不是公開來源，只當背景。調查日期：2026-11-05。

---

## ASR：Whisper large-v3 系列當骨幹，kotoba 與 sherpa-onnx 各補一塊

### 能接上現有 CT2 引擎的只有 Whisper 家族，ONNX 選項要另寫後端

kikitori 的 ASR 是自己寫在 CTranslate2 上的 Whisper 引擎：即時路徑用 greedy，完整路徑用 beam 5 加溫度 fallback。所以凡是 CT2 格式的 Whisper 架構模型都能「零整合」接上，ONNX 模型則需要新寫一個 sherpa-onnx／onnxruntime 後端。下表把符合「無 PyTorch 執行」的候選並列；Qwen3-ASR 雖然在噪音和歌聲上的自報數字很亮眼（ExtremeNoise 16.17 vs Whisper-large-v3 63.17），但**沒有官方 CT2／ONNX 匯出**，不符合限制，只列為觀察對象（[arXiv 2601.21337，未審查](https://arxiv.org/html/2601.21337)）。

| 模型 | 架構／格式 | 日語 CER（去標點） | 速度 | 授權 | 訓練資料 | 適合 |
|---|---|---|---|---|---|---|
| **Whisper large-v3** | CT2（Systran 轉換） | CV8 8.5／JSUT 7.1／ReazonSpeech 14.9【論文宣稱，kotoba 表】；FLEURS 4.9、CV15 10.3【論文宣稱，OpenAI】 | 基準 1× | MIT | OpenAI 未公開（1M h 弱標註＋4M h 偽標註） | 完整路徑、多語 |
| **Whisper large-v3-turbo** | CT2（dropbox-dash 轉換），decoder 32→4 層 | FLEURS 4.7、CV15 12.3【論文宣稱】；JSUT／CV8／ReazonSpeech 沒有官方數字 | 比 v3 快 | MIT | 同上 | 即時路徑、多語 |
| **kotoba-whisper-v2.0-faster** | CT2（官方），完整 v3 encoder＋2 層 decoder | CV8 9.2／JSUT 8.4／**ReazonSpeech 11.6**【論文宣稱】 | 比 v3 快 6.3×【論文宣稱】 | 卡片寫 MIT，原模型 Apache-2.0 | ReazonSpeech（720 萬段） | 只有日語 |
| **parakeet-tdt_ctc-0.6b-ja** | sherpa-onnx int8（只有 CTC 分支，625 MB） | **JSUT 6.4／CV8 7.1**／TEDxJP 9.0【論文宣稱】，輸出帶標點 | CPU 2 執行緒 RTF 0.106【獨立評測：sherpa 團隊，單一檔】 | CC-BY-4.0（需署名） | ReazonSpeech v2.0 | CPU 日語 |
| **reazonspeech-k2-v2** | sherpa-onnx Zipformer transducer，int8 約 150 MB | 卡片沒有數字 | CPU 單執行緒 RTF 0.054（int8）【獨立評測：sherpa 團隊，單一檔】 | Apache-2.0 | ReazonSpeech v2.0 | CPU 日語（輕量） |
| SenseVoiceSmall | sherpa-onnx，895 MB | CV ja 11.96（Whisper-L-V3 10.34）【論文宣稱】 | 10 s 音訊 70 ms【論文宣稱】 | FunASR 自訂 model-license | 未公開 | 不推薦（日語輸給 Whisper） |
| distil-large-v3.5-ct2 | CT2（官方） | 只支援英語 | 比 turbo 快 1.46× | MIT | — | 英語來源的即時路徑 |

各列來源：kotoba 的數字與速度見 [kotoba-whisper-v2.0 card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)，CT2 版的授權與建議參數見 [v2.0-faster card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)；parakeet 見 [model card](https://huggingface.co/nvidia/parakeet-tdt_ctc-0.6b-ja) 與 [sherpa-onnx 日語頁](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-ctc/nemo/japanese.html)；k2 見 [model card](https://huggingface.co/reazon-research/reazonspeech-k2-v2) 與 [sherpa-onnx transducer 頁](https://k2-fsa.github.io/sherpa/onnx/pretrained_models/offline-transducer/zipformer-transducer-models.html)；SenseVoice 見 [arXiv 2407.04051，未審查](https://arxiv.org/abs/2407.04051)；Whisper 的 FLEURS／CV15 見 [openai/whisper README](https://github.com/openai/whisper/blob/main/README.md) 與 [turbo 公告](https://github.com/openai/whisper/discussions/2363)；distil 見 [distil-large-v3.5 card](https://huggingface.co/distil-whisper/distil-large-v3.5)。

### 公開基準和「直播口語」之間方向相反，沒有一個模型穩贏

表上的數字有一個結構性問題：**kotoba-whisper 只在自己的訓練領域（ReazonSpeech 電視語音）贏過 large-v3，在朗讀語料 JSUT、CV8 上都輸**（[kotoba card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0)）。parakeet-ja 在 JSUT／CV8 上數字最好，但那是 NVIDIA 自報。唯一由第三方量的 ReazonSpeech 系數字是 Kotoba 團隊測的 reazonspeech-nemo-v2（CV8 9.1／JSUT 7.4／ReazonSpeech 11.2），三項都比 kotoba-v2.0 好（[japanese-asr README](https://japanese-asr-readme.static.hf.space/)），但本次沒有找到 nemo-v2 的 ONNX 版，無法直接採用。自發語音的唯一數字來自 Sony 的 Whale 論文：Whisper large-v3 在 CSJ 上 CER 約 16.5–18.4%，比朗讀語料高出一倍以上；不過這組數字只從搜尋摘要讀到，沒有逐字核對（[arXiv 2506.01439，未審查](https://arxiv.org/pdf/2506.01439)）。

更麻煩的是「動漫／角色聲線」這一軸。目前唯一的量測來自個人上傳的 anime-whisper 模型卡，它在 Galgame 台詞上量到 large-v3 16.5、turbo 16.9、parakeet-ja 18.6、kotoba-v2.0 18.8（[litagin/anime-whisper](https://huggingface.co/litagin/anime-whisper)）。這張卡**不符合本報告的來源標準**，只當待驗證的線索；而且它和【專案內部】在 6 段動畫片段上量到「kotoba CER 最低」的結果方向相反。VTuber 直播介於電視綜藝和角色聲線之間，兩種結果都有道理，誰對只能靠更大的自建測試集決定。

### 推薦：多語預設用 turbo＋v3，日語加一個 kotoba 開關，CPU 改走 sherpa-onnx

**推薦 1（GPU，所有語言的預設）：** 即時路徑用 large-v3-turbo，完整路徑用 large-v3。理由是授權乾淨（MIT），不依賴 ReazonSpeech，20 種語言都能用；在朗讀基準上，它也是日語最強的 CT2 模型。turbo 的 `no_speech` 機率不能用（【專案內部】實測恆為 0），所以過濾要靠 VAD 和下一節的後處理。

**推薦 2（日語專用選項）：** kotoba-whisper-v2.0-faster。它是唯一在電視口語領域有優勢、又能零整合接上的模型，2 層 decoder 也讓即時路徑更快。使用時照官方建議設 `condition_on_previous_text=False`，chunk 15 s（[v2.0-faster card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0-faster)）。升為日語預設的條件是：在自建 VTuber 集上，以片段為單位做 bootstrap，95% 信賴區間顯示它優於 large-v3。

**CPU-only：** CPU 上 Whisper 只能用 small 或 base，日語品質會掉到 FLEURS CER 12.0／22.8（見下方語言分級一節）。日語想在純 CPU 上維持可用品質，唯一的路是新增 sherpa-onnx 後端，接 **parakeet-tdt_ctc-0.6b-ja int8**（重準確度、會輸出標點）或 **reazonspeech-k2-v2 int8**（重輕量；transducer 也能用 modified_beam_search 做出 fast／final 雙路徑）。【推論】parakeet 的 CTC 匯出只有 greedy，所以雙路徑在它身上會退化成同一種解碼。

不推薦 kotoba-whisper v2.1／v2.2：它們只是在 Transformers pipeline 後面加上標點和 pyannote 說話者分離，需要 torch，對 CT2 推論沒有額外價值（[v2.2 card](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.2)）。初始 prompt／hotwords 能不能拉回 VTuber 名字，同行審查的證據都需要額外訓練（[CB-Whisper，Interspeech 2024](https://arxiv.org/abs/2309.09552)），用不到現成的 CT2 推論；而且有詞表超過約 1,000 詞會被「稀釋」的問題（[Hou et al., Interspeech 2025](https://www.isca-archive.org/interspeech_2025/hou25_interspeech.pdf)）。所以名稱修正交給 ASR 後的字典正規化，或下游翻譯的術語表，比較穩。

---

## 前處理：降噪與分離幫不上 Whisper，VAD 閘控才是主力

### 同行審查的證據一致指向「別把增強後的音訊直接餵給 Whisper」

把語音增強直接接在 Whisper 前面，對 Whisper 通常沒幫助。NTT 在 Interspeech 2022 證明，單通道增強讓 ASR 變差的主因是增強本身產生的 **artifact**，不是殘留噪音；把原始訊號按比例混回去（observation adding）才能改善（[Iwamoto et al., Interspeech 2022](https://www.isca-archive.org/interspeech_2022/iwamoto22_interspeech.html)）。後續研究得到一致的結論：Whisper 只在弱增強（α=0.25）時 WER 從 6.44 降到 5.57，全強度回到 6.46（[arXiv 2607.11157，未審查](https://arxiv.org/abs/2607.11157)）；EUSIPCO 2024 也發現直接餵增強輸出效果差，要另加 bridge module 才有小幅增益（[EUSIPCO 2024](https://eurasip.org/Proceedings/Eusipco/Eusipco2024/pdfs/0000426.pdf)）。

音源分離的情況也一樣。QMUL 與 AudioShake 的研究用 Hybrid Demucs 把人聲分離出來再送 Faster-Whisper，結果在 Jam-ALT 上 WER **20.99 → 21.08／21.17，沒有改善**，mdx_extra 還因為 artifact 引發大量 hallucination。真正一致的增益來自另一種用法：**用分離後的人聲 RMS 決定切段，但送進 ASR 的仍是原始混音**，Jam-ALT 長音訊 WER 從 23.02 降到 20.35（[arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)）。廣播語音上的 Demucs 前端同樣沒有帶來 WER 改善（[arXiv 2101.12729](https://arxiv.org/pdf/2101.12729)）。還有一點：分離器把「歌聲」當人聲保留，歌回和有歌詞的 BGM 都分不開。

### Whisper 對非語音的 hallucination 很嚴重，VAD 能壓掉絕大部分

真正的風險是 hallucination，而 VAD 已經是最有效的單一手段。AGH 在 ICASSP 2025 讓 large-v3 跑 301,317 段非語音音訊（**刻意排除了音樂**），**40.3% 產生文字**，其中 9.1% 是重複循環。混合語音的條件下，WER 從無處理的 104.8 降到 **Silero VAD 的 8.0**，再加上去循環和 Bag of Hallucinations 黑名單降到 6.5；只用黑名單、不用 VAD 則是 17.1。DCCRN 降噪對 hallucination 沒有幫助（[Barański et al., ICASSP 2025](https://arxiv.org/html/2501.11378v1)）。同一篇論文還指出，**1 s 的非語音片段 hallucination 率 52.1%，10 s 時 11.6%，30 s 時 62.3%**，所以 VAD 切出的極短片段是高風險區。Calm-Whisper 只微調 3 個 decoder head，就把非語音 hallucination 降低 80% 以上，LibriSpeech WER 退化不到 0.1%（[Interspeech 2025](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.html)），但權重是否公開、授權為何都未確認。

| 方法 | 類型 | 無 PyTorch | 延遲 | 授權 | 對下游 ASR 的證據 | 判定 |
|---|---|---|---|---|---|---|
| **Silero VAD v6 閘控** | 二元 VAD | 是（ONNX） | 32 ms 窗 | MIT | 非語音 WER 104.8→8.0【獨立評測】 | **必要（已有）** |
| **Whisper 後處理過濾**（黑名單、去循環、compression ratio、`condition_on_previous_text=False`） | 規則 | 是 | 約 0 | — | 8.0→6.5【獨立評測，英語黑名單】 | **推薦 1** |
| **CED-Tiny／Mini 歌聲／音樂分類** | AudioSet 標註 | 是（repo 附 `export_onnx.py`） | 小模型，【推論】遠低於 ASR | 未確認 | 沒有 ASR 下游數據；mAP 48.1／49.0【論文宣稱】 | **推薦 2（歌回）** |
| HT-Demucs 分離，只用來切段 | 音源分離 | 需自行匯出 ONNX | 非因果、未量測 | MIT（上游已封存） | 切段用法 WER 23.02→20.35【論文宣稱】 | 實驗選項，預設關 |
| Band-SCNet 因果分離 | 音源分離 | 未確認 | 92 ms | 未確認 | 沒有；SDR 7.79 dB 低於 Demucs | 不推薦 |
| BS／Mel-RoFormer 社群 ONNX | 音源分離 | 是（非官方） | 非因果、模型大 | 權重來源不明 | 沒有 | 不推薦 |
| DeepFilterNet、RNNoise、MossFormer2-SE | 語音增強 | 是 | 低 | MIT／Apache（MossFormer 的去殘響權重是 GPL-3.0） | 全強度 SE 對 Whisper 無益或有害 | 不推薦用於 BGM |

各列來源：Silero 的指標見 [Quality Metrics wiki](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)；CED 見 [GitHub RicherMans/CED](https://github.com/richermans/ced) 與 [ICASSP 2024 論文](https://arxiv.org/pdf/2308.11957)；Band-SCNet 見 [Interspeech 2025](https://www.isca-archive.org/interspeech_2025/yang25d_interspeech.pdf)；Demucs 見 [facebookresearch/demucs](https://github.com/facebookresearch/demucs)；DeepFilterNet 見 [GitHub](https://github.com/rikorose/deepfilternet)；ClearerVoice 見 [GitHub](https://github.com/modelscope/ClearerVoice-Studio)。

### 歌回會直接穿過 VAD，需要第二層判斷

歌回是 Silero 的盲區。Silero 官方評測的資料集包括會議、電話、環境噪音，**完全不含音樂或歌唱**（[Silero Quality Metrics](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)）。FireRedVAD 文件則明說二元 VAD 把「歌唱視為語音、純音樂拒絕」（[FireRedVAD](https://github.com/FireRedTeam/FireRedVAD)）。所以歌回時 VAD 會一直開著，把旋律長音送進 Whisper，而 Whisper 對歌詞本來就弱：在原混音上 WER 約 21–26%，和聲與非詞彙發聲的刪除率很高（[arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)）。

**推薦 1：** 不新增任何模型，只在現有管線上加強過濾。具體做法：在 BGM 與歌回素材上掃描 Silero 的 threshold、min_silence、speech_pad；完整路徑開啟 compression-ratio 與 avg-logprob 過濾；建立**日語版 Bag of Hallucinations**。目前沒有任何可信來源列出非英語的 hallucination 片語，Barański 的清單是強制 `language=English` 跑出來的。因此日語清單要照同一方法自行蒐集：強制 `language=ja`，拿靜音、BGM、噪音跑 large-v3／turbo，統計高頻輸出（【推論】，方法來自 [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)）。

**推薦 2：** 用 CED-Tiny 或 Mini 的 ONNX 對每個 VAD 片段打分。Singing／Music 高於門檻時標成「♪」並跳過翻譯；送進 Whisper 的音訊維持不變。

**實驗選項（預設關閉）：** HT-Demucs 只拿來決定切段，ASR 仍吃原始混音。真要把分離後的訊號送進 ASR，就用 `vocals + β·mixture` 弱混合，β 在自建資料上調。

---

## 翻譯：Qwen3.5 授權最乾淨，HY-MT 系列證據最強但有地區限制

### 獨立人評只有 WMT25 的 ja→zh，ja→en 是空白

2025–2026 年唯一同時「獨立、人工、涵蓋日文來源、又有 ≤12B 開放模型」的評測，是 WMT25 的 ja→zh（簡中）MQM。結果（越接近 0 越好）：人工參考譯文 −3.5、Gemini-2.5-Pro −4.4、Claude-4 −5.9、**Shy-hunyuan-MT（Hunyuan-MT-7B）−6.1**、GPT-4.1 −6.2，接著 **Qwen3-235B −10.9**、**TowerPlus-9B −13.3**（[WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)）。也就是說，一個 7B 的專用翻譯模型在日→中打平 GPT-4.1，Qwen 上一代的 235B 旗艦反而落後一截。但主辦方也提醒，Hunyuan 在**自動指標 AutoRank 幾乎全部第一、人評卻較低**，推測是 GRPO 拿 XCOMET 和 GEMBA 當 reward 造成的指標過擬合。所以 Hunyuan 和它的後繼 HY-MT1.5 自報的 XCOMET 領先（7B WMT25 0.6159 vs Gemini 3.0 Pro 0.5505）不能照單全收（[HY-MT1.5 技術報告，未審查](https://arxiv.org/html/2512.24092v1)）。

WMT25 **沒有 ja→en 方向**。這個方向唯一的人評來自 Google 內部：TranslateGemma 在 10 個語言對裡，**只有 Japanese→English 比原版 Gemma 3 退步**，原因是專有名詞誤譯（[TranslateGemma 技術報告，未審查](https://arxiv.org/pdf/2601.09012)）。偏偏直播滿是人名、組合名和遊戲名，這個弱點正好落在 VTuber 最敏感的地方。

| 模型 | 尺寸 | ja→zh 證據 | ja→en 證據 | 20 語言 | zh-TW | 授權 | 判定 |
|---|---|---|---|---|---|---|---|
| **Qwen3.5** | 4B／9B | 沒有日文分項；上一代 Qwen3-235B 在 WMT25 為 −10.9【獨立評測】 | 沒有 | 推得涵蓋（官方無清單） | Qwen3 明列 Traditional Chinese | **Apache-2.0** | **推薦 1（預設）** |
| **HY-MT1.5** | 1.8B／7B | 前身 Hunyuan-MT-7B 在 WMT25 為 −6.1【獨立評測】；1.5 版只有自報 XCOMET | 沒有 | 官方明列 | 明列 zh-Hant | Tencent HY Community（排除 EU／UK／KR；MAU 超過 1 億需另行授權） | **推薦 2（品質選項）** |
| TranslateGemma | 4B／12B | 沒有人評 | Google 人評：比 Gemma 3 退步【論文宣稱】 | SFT 涵蓋，但 zh-TW、pt、uk 只有 en→ 方向 | 只有 en→zh-TW | Gemma Terms（gated） | 只用於英文為來源或目標 |
| Seed-X-PPO | 7B | 沒有日文人評 | 沒有 | 28 種，日文在內 | 未說明 | OpenMDW | 備選，需自測 |
| PLaMo 2 Translate | 10B | 不支援中文 | 卡片沒有 benchmark | 只有英日 | — | PLaMo Community（年營收 10 億日圓以下免費） | ja→en 備選 |
| Tower+ | 9B | WMT25 −13.3【獨立評測】 | 沒有 | 22 種 | — | CC BY-NC-SA（二手來源） | **排除（非商用）** |
| Sakura／GalTransl | 7B–14B | 沒有獨立評測 | 不支援 | 只有 ja→簡中 | 需靠 OpenCC | **CC BY-NC-SA 4.0** | **排除（非商用）** |

各列來源：[Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)、[Qwen3 語言清單](https://qwenlm.github.io/blog/qwen3/)、[HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)、[HY-MT1.5 License.txt](https://huggingface.co/tencent/HY-MT1.5-1.8B/blob/main/License.txt)、[translategemma-4b-it](https://huggingface.co/google/translategemma-4b-it)、[Seed-X，未審查](https://www.arxiv.org/pdf/2507.13618)、[plamo-2-translate](https://huggingface.co/pfnet/plamo-2-translate)、[PLaMo 授權條文](https://scancode-licensedb.aboutcode.org/plamo-cla-2024.html)、[Tower+，未審查](https://arxiv.org/html/2506.17080v1)、[Tower-Plus-9B 授權（託管平台，二手）](https://featherless.ai/models/Unbabel/Tower-Plus-9B)、[SakuraLLM](https://github.com/SakuraLLM/SakuraLLM)。

### 推薦理由：公開證據偏向 HY-MT，授權與專案實測偏向 Qwen3.5

**推薦 1：Qwen3.5 4B（8 GB GPU）／9B（12 GB GPU）當預設。** 推薦它的理由是授權，不是品質證據。Qwen3.5 是 Apache-2.0，沒有地區限制；自報的 WMT24++ 55 語言平均 XCOMET 是 9B 72.6、4B 66.6，但沒有日文分項（[Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)）。【專案內部】的翻譯 benchmark 在 RTX 5070 上量到日文口語品質排序是 Qwen3.5 9B > 4B > HY-MT 7B > 1.8B，和 WMT25 的方向不同。可能的原因有兩個【推論】：WMT25 是文件級的簡中新聞、社群、文學語料，不是無標點的 10 秒直播句子；HY-MT 的 context 模板在專案實測中容易把前文一起輸出。Qwen3.5 預設開 thinking，翻譯時要用 `chat_template_kwargs: {"enable_thinking": false}` 關掉（[Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)）。

**推薦 2：HY-MT1.5 7B（12 GB）／1.8B（8 GB 或 CPU）當「品質選項」。** 它是唯一官方明列 20 種語言、明列 zh-Hant、又有 XX⇔XX 雙向 prompt 的專用 MT 模型，而且官方 model card 提供術語干預和 context 翻譯模板。自報延遲每句約 50 token：1.8B 0.18 s、7B 0.45 s，但硬體不明（[HY-MT1.5 技術報告](https://arxiv.org/html/2512.24092v1)）。授權原文寫「THIS LICENSE AGREEMENT DOES NOT APPLY IN THE EUROPEAN UNION, UNITED KINGDOM AND SOUTH KOREA」（[License.txt](https://huggingface.co/tencent/HY-MT1.5-1.8B/blob/main/License.txt)），所以 UI 要警告，並允許依地區排除。

### 提示與部署：前文 context、溫度 0、7B 以上用 Q4_K_M

在提示策略上，同行審查證據最清楚的一項是**「前 k 句原文＋模型自己的前幾句譯文」當 context**。Sony 在 EMNLP 2025 Findings 用 GPT-4o 和 Gemma 2-27B 做 en→ja 等方向（k=5，資料是 TED 與 OpenSubtitles），結果所有 context 策略都優於不給 context；src 加上自身 hyp 不需要參考譯文，就能改善敬語、代名詞和詞彙一致性（[Choudhary et al., 2025](https://aclanthology.org/2025.findings-emnlp.1324.pdf)）。字幕的即時路徑適合用 **re-translation**：IWSLT 2020 的研究顯示，normalized erasure < 0.2 時，re-translation 的品質不輸 streaming 方法（[Arivazhagan et al., IWSLT 2020](https://arxiv.org/pdf/2004.03643)）。

解碼方面，Llama-3.1-8B 的 temperature 從 0 升到 0.8，日文方向 COMET 掉 1.3–1.6（[Marie & Fujita，未審查](https://arxiv.org/html/2508.20893)），所以翻譯應該用接近 0 的溫度，而不是 Qwen 官方建議給一般任務的 0.7。量化方面，同一研究顯示 **GGUF Q4_K_M 是 4-bit 中損失最小的**：Qwen3-8B 的 en→ja COMET 從 85.7 只降到 85.0–85.6；但 1.7B 會從 80.4 降到 79.7，2-bit 則崩到 69.4（同上）。HY-MT1.5-1.8B 的 GPTQ Int4 也被官方自己描述為「noticeable accuracy degradation」（[HY-MT1.5 技術報告](https://arxiv.org/html/2512.24092v1)）。所以 7–9B 用 Q4_K_M；【推論】≤2B 改用 Q5_K_M 或 Q8_0。

### zh-TW：沒有任何 ja→zh-TW 基準，維持 OpenCC 並做 A/B

WMT25 的 ja→zh 目標是簡中，所以所有獨立人評都是簡中（[WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)）。HY-MT1.5 和 TranslateGemma 都能直接指定繁中，但只有 TranslateGemma 有 **en→zh_TW** 的自報分數（MetricX 12B 2.21、4B 2.77；[TranslateGemma 報告](https://arxiv.org/pdf/2601.09012)），**沒有任何模型有 ja→zh-TW 評測**。台灣本地模型 Breeze 2 和 Gemma-3-TAIDE 的重點是繁中與中英互譯，沒有日文翻譯證據（[Breeze 2，未審查](https://arxiv.org/abs/2501.13921)）。因此證據最充分的路線仍是「模型輸出中文，再用 OpenCC `s2twp` 轉成台灣用語」。HY-MT 直接輸出 zh-Hant 的版本，值得在自建集上和它做 A/B。

---

## 二十種語言：Whisper 的品質落差比翻譯模型的覆蓋落差更大

### Whisper 各語言分級：turbo 在泰語、阿拉伯語、印地語掉到 C 級

下表是 OpenAI 自報的 large-v3 與 turbo 在 FLEURS 和 Common Voice 15 上的錯誤率。數值讀自 README 和 turbo 公告的官方 SVG 標籤；zh、ja、ko、th 是 CER，其他是 WER。分級取兩個資料集中較差的值：≤10 為 A、10–20 為 B、>20 為 C（[openai/whisper README](https://github.com/openai/whisper/blob/main/README.md)、[turbo 公告 #2363](https://github.com/openai/whisper/discussions/2363)）。全部數字都是**朗讀語音**，直播口語只會更差。

| 語言 | v3 FLEURS | v3 CV15 | turbo FLEURS | turbo CV15 | 分級 v3 | 分級 turbo | small FLEURS（v2 時代） |
|---|---|---|---|---|---|---|---|
| en | 4.1 | 9.3 | 4.4 | 10.6 | A | B | 6.1 |
| zh-CN | 7.7 | 12.8 | 8.3 | 14.3 | B | B | 20.8 |
| zh-TW | — | 8.2 | — | 10.3 | A | B | — |
| ja | 4.9 | 10.3 | 4.7 | 12.3 | B（邊緣） | B | 12.0 |
| ko | 3.1 | 5.2 | 3.2 | 5.6 | A | A | 19.6（WER） |
| es | 2.8 | 4.7 | 3.1 | 5.5 | A | A | 5.6 |
| fr | 5.3 | 10.8 | 5.8 | 12.1 | B（邊緣） | B | 15.0 |
| de | 4.9 | 5.7 | 5.8 | 6.9 | A | A | 10.2 |
| pt | 4.1 | 5.9 | 4.2 | 6.9 | A | A | 7.3 |
| ru | 5.0 | 5.8 | 5.3 | 6.3 | A | A | 11.4 |
| it | 3.0 | 5.5 | 3.2 | 6.3 | A | A | 9.8 |
| ar | 9.6 | 15.1 | 10.2 | 23.7 | B | **C** | 30.6 |
| hi | 17.0 | 16.9 | 18.5 | 24.1 | B | **C** | 38.4 |
| id | 6.1 | 7.2 | 6.9 | 8.9 | A | A | 16.3 |
| vi | 8.6 | 39.8 | 9.6 | 未收錄 | **C**（CV） | **C**（CV） | 21.2 |
| th | 8.4 | 5.8 | 13.9 | 20.8 | A | **C** | 21.9 |
| tr | 6.7 | 12.0 | 6.9 | 17.0 | B | B | 15.9 |
| pl | 4.6 | 6.0 | 5.4 | 7.3 | A | A | 14.7 |
| nl | 5.2 | 4.3 | 5.9 | 5.8 | A | A | 16.4 |
| uk | 6.4 | 13.6 | 6.9 | 16.2 | B | B | 19.3 |

small 那一欄來自 Whisper 論文 Table 13（large-v2 時代；small／base 之後沒有新版本，所以數字仍適用。論文的 ko 是 WER，不能和 README 的 CER 比較；[arXiv 2212.04356](https://arxiv.org/abs/2212.04356)）。依這張表，**small 只在 en、es、pt、it 達到 A 級**，base 更只剩 en、es。這和訓練資料量高度相關：論文報告 WER 與訓練時數的 R² 為 0.83，**印地語只有 12 小時**、泰語 226 小時，日語 7,054 小時（同上）。OpenAI 也自己承認 turbo「在泰語和粵語上退化較大」（[#2363](https://github.com/openai/whisper/discussions/2363)）。

這對語言選單的意義是：**th、ar、hi、vi 的完整路徑應推薦 large-v3，不要用 turbo**；CPU-only 只用 small 的機器，除了 en、es、pt、it 之外都應該顯示品質警告【推論】。zh-TW 當來源語言時要注意，Whisper 只有一個 `zh` token，**無法控制輸出繁體或簡體**，需要接 OpenCC（[tokenizer.py](https://github.com/openai/whisper/blob/main/whisper/tokenizer.py)）。語言偵測也不能交給 Whisper：large-v2 在 Common Voice 上 **1 秒片段的 LID 準確率只有 23.5%**，2 秒 79.9%，3 秒 93.1%（[NICT，arXiv 2312.10964，未審查](https://arxiv.org/abs/2312.10964)）。VAD 切出的片段常常只有 1–3 秒，因此使用者在選單選了來源語言，就應一律強制 `language=<code>`，這也是官方 model card 的建議（[whisper-large-v3](https://huggingface.co/openai/whisper-large-v3)）。

其他語言能不能換成更好的 ASR？證據很少。英語可以用官方 CT2 版的 distil-large-v3.5 加速，短句 WER 7.08，和 large-v3 的 7.12 打平（[distil-large-v3.5](https://huggingface.co/distil-whisper/distil-large-v3.5)）。簡中方面，SenseVoice-Small 在 AISHELL-1（2.96 vs 5.14）和 WenetSpeech meeting（7.44 vs 18.87）上明顯優於 Whisper-L-V3，但這是自報數字，授權也是自訂的（[arXiv 2407.04051，未審查](https://arxiv.org/abs/2407.04051)）。parakeet-tdt-0.6b-v3 的 10 種歐語在 FLEURS 上沒有任何一種明顯勝過 large-v3（[parakeet-tdt-0.6b-v3](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)）。

### 翻譯覆蓋：只有 HY-MT1.5 官方明列全部 20 種

| 語言 | Qwen3.5 | HY-MT1.5 | TranslateGemma |
|---|---|---|---|
| en、zh-CN、ja、ko、es、fr、de、it、ru、pl、nl、ar、hi、id、vi、th、tr | 推得（依 Qwen3 的 119 語清單） | 官方明列 | 與英文雙向 |
| zh-TW | 推得（Qwen3 明列 Traditional Chinese） | 明列 zh-Hant（沒說明是否為台灣用語） | **只有 en→** |
| pt | 推得 | 明列 | **只有 en→**（pt-BR／pt-PT） |
| uk | 推得 | 明列 | **只有 en→**（另有 cs–uk） |

各欄來源：[Qwen3 blog](https://qwenlm.github.io/blog/qwen3/)、[Qwen3.5-9B card](https://huggingface.co/Qwen/Qwen3.5-9B)（只寫 201 種、沒有清單）、[HY-MT1.5-7B card](https://huggingface.co/tencent/HY-MT1.5-7B)、[TranslateGemma 報告 Appendix C](https://arxiv.org/abs/2601.09012)。TranslateGemma 的非英語語言對只有 9 組（例如 zh-CN–ja），**ja→ko、ja→es 這類「非英→非英」都在訓練分布外**；chat template 遇到不支援的語言代碼還會直接報錯（[translategemma-4b-it](https://huggingface.co/google/translategemma-4b-it)）。不過，**沒有任何翻譯模型公布 20×20 語言對的逐對分數**。HY-MT1.5 只有 Flores ZH⇔XX、EN⇔XX 的平均值，所以任意兩種語言組合的品質目前都只能自測。

---

## 授權與不確定處：ReazonSpeech 條款是最大的灰色地帶

| 元件 | 權重授權 | 商用 | MIT 相容的限制 |
|---|---|---|---|
| Whisper large-v3／turbo（CT2） | MIT | 可 | 無 |
| kotoba-whisper-v2.0-faster | CT2 卡片寫 MIT，原模型 Apache-2.0 | 可 | 保守起見照 Apache-2.0 保留 NOTICE；ReazonSpeech 資料條款見下文 |
| parakeet-tdt_ctc-0.6b-ja | CC-BY-4.0 | 可 | **需在 app 中署名**；ReazonSpeech 條款同上 |
| reazonspeech-k2-v2 | Apache-2.0 | 可 | ReazonSpeech 條款同上 |
| SenseVoiceSmall | FunASR 自訂 model-license | 未確認 | 全文未取得，不推薦 |
| Silero VAD | MIT | 可 | 無 |
| CED | **未確認** | 未確認 | 採用前要讀 LICENSE |
| HT-Demucs | MIT | 可 | 上游已封存，自行匯出 ONNX |
| Qwen3.5 | Apache-2.0 | 可 | 無 |
| HY-MT1.5 | Tencent HY Community License | 可（有限制） | **不適用於歐盟、英國、南韓**；MAU 超過 1 億需另行授權 |
| TranslateGemma | Gemma Terms（gated） | 可（受使用政策約束） | 模型授權原文本次未直接核對 |
| PLaMo 2 Translate | PLaMo Community License | 年營收 10 億日圓以下免費 | 商用需先填表 |
| Sakura／GalTransl、Tower+ | CC BY-NC-SA 4.0 | **不可** | 不相容，不應列為推薦 |

所有用 ReazonSpeech 訓練的模型（kotoba、parakeet-ja、reazonspeech-k2／nemo），權重本身都是寬鬆授權。但**語料**的使用條件寫的是「YOU WILL USE THE DATASET SOLELY FOR THE PURPOSE OF JAPANESE COPYRIGHT ACT ARTICLE 30-4」（[ReazonSpeech dataset card](https://huggingface.co/datasets/reazon-research/reazonspeech)），官方專案頁也寫「利用目的は著作権法30条の4に定める情報解析に限る」（[ReazonSpeech 專案頁](https://research.reazon.jp/projects/ReazonSpeech/)）。【推論】這個條款約束的是下載語料的人；kikitori 只拿權重做推論，看起來落在條款之外。不過 Reazon 和 Kotoba 都沒有對「衍生模型的終端使用者」做過明文說明，這點需要詢問 Reazon 或律師。還要注意，**換成 parakeet 或 k2 不能迴避這個問題**，因為它們的資料來源完全相同；不依賴 ReazonSpeech 的只有 OpenAI Whisper（訓練資料不公開）。

kikitori 由使用者自行從 Hugging Face 下載模型，這比把權重打包進安裝檔風險更低，也能保留原始 LICENSE 與 model card。此外，專案的翻譯 preset 清單裡還有一個 Sakura-GalTransl 7B，已標示「非商用」，也沒有列入自動推薦。依本報告的授權限制，應維持這個做法：只讓使用者手動選擇，並清楚標示授權。

其餘的不確定處，依對決策的影響排序：large-v3-turbo 沒有任何 JSUT、CV8、ReazonSpeech、CSJ 的日語 CER；kotoba-v2.0-faster 與 large-v3 在直播語料上誰較好，有互相矛盾的線索；Qwen3.5 的 201 種語言沒有官方清單；HY-MT1.5 和 Qwen3.5 都沒有官方的消費級 GPU VRAM 和延遲數字；Silero 在「語音＋音樂」條件下沒有任何公開數字；CED 與 Calm-Whisper 的權重授權都未確認；Whisper 論文與 README 的 large-v2 數字不一致（例如 zh 14.7 vs 7.5），所以兩組數字不能混用。

---

## 評測：在本專案重現的三層 benchmark

### 資料：公開基準只用來校準管線，真正的答案在自建的 VTuber 集

目前**沒有任何正式機構發布的 VTuber 或直播語音資料集**。最接近的是 YouTube 衍生語料和電視語料（[YODAS，ASRU 2023](https://arxiv.org/html/2406.00899v1)、[J-CHAT](https://arxiv.org/pdf/2407.15828)、[ReazonSpeech](https://huggingface.co/datasets/reazon-research/reazonspeech)）。可以合法放進 repo 的，只有 FLEURS（CC-BY-4.0）和 YODAS（CC BY 3.0）的小子集。Common Voice 雖然是 CC0，但 datasheet 禁止重新散布（[CV 24.0 ja](https://datacollective.mozillafoundation.org/datasets/cmj8u3p9x00clnxxbsv97o45e)）。J-CHAT 的轉寫由 ASR 產生，音訊也已用 Demucs 去掉 BGM，不能當金標準（[arXiv 2407.15828，未審查](https://arxiv.org/pdf/2407.15828)）。

| 層 | 資料 | 用途 | 授權與存放 |
|---|---|---|---|
| 校準 | `japanese-asr/ja_asr.reazonspeech_test`、`ja_asr.jsut_basic5000`、`ja_asr.common_voice_8_0` | 用 kotoba 的正規化重現公開 CER（例如 large-v3 14.9／7.1／8.5），確認評測管線沒問題 | 執行時下載，git-ignore；ReazonSpeech 是 gated |
| 校準 | FLEURS ja_jp 等 20 語言、TEDxJP-10K | 20 語言分級的本地複驗；TEDxJP 是不與訓練語料重疊的演講 | FLEURS 是 CC-BY-4.0；TEDxJP 的 repo 標 Apache-2.0 |
| 校準（ST） | CoVoST 2 ja→en（約 1 小時） | ja→en 的乾淨朗讀下限 | 音訊來自 CV，執行時下載 |
| **主體** | 自建 VTuber 片段（目標**至少數十段**，包含雜談、遊戲、連動、歌回），人工轉寫＋zh-TW／en 參考譯文 | 選型的決定性依據 | 有著作權，只放本地；repo 只放 manifest |
| 壓力 | 主體片段 × MUSAN 混音；MUSAN 純音樂／噪音／靜音 | BGM 強健性、非語音 hallucination | MUSAN 授權未核對 |

資料來源：[kotoba-whisper-v2.0 README](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0/blob/main/README.md)、[google/fleurs](https://huggingface.co/datasets/google/fleurs)、[TEDxJP-10K](https://github.com/laboroai/TEDxJP-10K)、[CoVoST 2](https://arxiv.org/pdf/2007.10310)。

### 混音協定：沿用 Kaldi 的 MUSAN 設定，直播 BGM 以 10–20 dB 為常態

混音沿用 Kaldi 的 MUSAN 增強設定，這是事實上的標準：noise 以前景、每秒插入一次，SNR 15／10／5／0 dB；music 以背景一首，SNR 15／10／8／5 dB；babble 混 3–7 人，SNR 20／17／15／13 dB（[kaldi voxceleb v2](https://raw.githubusercontent.com/kaldi-asr/kaldi/master/egs/voxceleb/v2/run.sh)）。廣播規範要求語音比背景至少大 4 LU；Fraunhofer 把局部 SBLD < 0 LU 定為「難懂段落」；聽者偏好的差距四分位距約 5.7 LU（[Torcoli et al., AES 2024](https://arxiv.org/pdf/2405.17364)、[arXiv 2305.19100](https://arxiv.org/abs/2305.19100)）。依此，kikitori 的 music 網格建議用 **20／15／10／5 dB 為主、0 dB 當壓力測試**；遊戲音效用 MUSAN noise 15／10／5／0 dB。【推論】直播 BGM 平常落在 10–20 dB，5 dB 相當於 BGM 偏大或歌回。

SNR 以語音活躍段的能量為基準，不用整段平均，這樣靜音比例不同的片段才能比較。每個條件固定 seed，把噪音檔 ID、offset、增益寫進 manifest，並跑多個 seed 取平均，避免單一首 BGM 主導結果。

### 指標：CER、片段級 hallucination 率、延遲分位數、chrF 加隔離的 COMET

ASR 的 CER 正規化沿用 kotoba 的做法，才能和公開數字對齊：Whisper 的 `BasicTextNormalizer`（NFKC，並移除 Unicode M／S／P 類別與括號內文字）處理後，去掉所有空白再算 CER（[kotoba README](https://huggingface.co/kotoba-tech/kotoba-whisper-v2.0/blob/main/README.md)、[normalizers/basic.py](https://raw.githubusercontent.com/openai/whisper/main/whisper/normalizers/basic.py)）。這個 normalizer 不處理數字寫法、漢字與假名差異，所以另外要報兩個版本：「含填充詞」和「去填充詞」（えー、あの、ｗ、草 各自寫成固定規則）。正規化函式要加上版本號並附單元測試。

非語音 hallucination 用 **Calm-Whisper 的片段級二元定義**：只要非語音片段輸出任何文字，就算一次 hallucination（[arXiv 2505.12969](https://arxiv.org/html/2505.12969v1)）。同時報每分鐘非語音的插入字元數，並把 1 s 與 10 s 以上的片段分開統計，以對應 Barański 量到的長度效應。

延遲以語音起點和終點為基準，量 first partial latency、finalization latency、RTF、partial 修訂次數，報告 P50／P90（[TrimTail](https://arxiv.org/pdf/2211.00522)、[partial 穩定度](https://arxiv.org/pdf/2006.01416)）。翻譯的字幕閃爍用 normalized erasure 衡量，目標 < 0.2（[Arivazhagan et al.](https://arxiv.org/pdf/2004.03643)）。

翻譯指標分兩層。主要管線用純 Python 的 **chrF 與 sacreBLEU**（中文 tokenizer `zh`、日文 `ja-mecab`；[sacrebleu](https://pypi.org/project/sacrebleu)）；計分前把輸出和參考統一成同一種字形（OpenCC），否則繁簡差異會淹沒真正的品質差。神經指標的相關性比 chrF 高得多：WMT24 中 MetricX-24-Hybrid 為 0.721、XCOMET 0.719，chrF 只有 0.606（[WMT24 Metrics](https://aclanthology.org/2024.wmt-1.2.pdf)、[arXiv 2411.00390](https://arxiv.org/pdf/2411.00390)）。但 COMET 需要 PyTorch，所以要放在**獨立的評測 venv**，不能進 runtime；CLI 支援 `--gpus 0` 在 CPU 上跑（[Unbabel/COMET](https://github.com/Unbabel/COMET)）。CometKiwi 是 CC-BY-NC-SA，只能用於內部評測，不能散布。用本地 LLM 當評審（GEMBA 類）的信度未知，只當輔助；用的話要像 GEMBA-MQM V2 那樣每段評多次再聚合（[WMT25 2025.wmt-1.40](https://aclanthology.org/anthology-files/pdf/wmt.real/2025.wmt-1.40.pdf)）。

### 程序：分開量 ASR 錯誤的傳遞，並以片段為單位做 bootstrap

翻譯要分兩次量，才能把 MT 本身的品質和 ASR 錯誤的傳遞拆開：同一批片段，**一次餵人工轉寫、一次餵 ASR 輸出**，比較兩者的 chrF／COMET 差距。這個差距就是 ASR 錯誤傳遞的成本。WMT25 已經證實，speech domain（Whisper-large-v3 轉寫）是所有語言對中最難的，ja→zh 參考譯文的 GEMBA 分數從 social 的 86.6 掉到 speech 的 72.6（[WMT25 Findings](https://aclanthology.org/2025.wmt-1.22.pdf)）。kikitori 的輸入（無標點、≤10 s）條件還更差。

統計上，同一片段內的句子彼此相關，逐句 bootstrap 會讓信賴區間過窄（[Blockwise bootstrap，arXiv 1912.09508](https://arxiv.org/pdf/1912.09508)），所以要**以片段為 block 做 1,000 次 blockwise bootstrap**，報告 CER 的 95% CI 和兩模型差值的 probability of improvement（[Bisani & Ney, ICASSP 2004](https://api.crossref.org/works/10.1109%2FICASSP.2004.1326009)）。MT 則直接用 sacreBLEU 內建的 paired bootstrap。目前只有 6＋6 段時，CI 會寬到無法分出勝負；這正是要把自建集擴充到數十段的原因。

實作上要遵守專案規則：評測腳本做成互動選單；`sacrebleu`、`jiwer`、OpenCC 等新依賴要先徵求同意，再用 `uv add <pkg>==<version> --dev` 加入；COMET 另開 venv。評測結果寫進 `experiments/`，並附上模型 revision、正規化版本和硬體資訊。

---

## Conclusion

這次調查翻轉的是「選型靠查表」的預期。在 kikitori 的限制下，每個環節的公開證據都指向領域之外：ASR 的強弱在朗讀語料與電視語料之間互換；翻譯唯一的獨立人評是簡中、文件級的新聞與文學；前處理的研究幾乎全是英語、歌詞或合成噪音，而且最權威的 hallucination 研究還刻意排除了音樂。因此，最有價值的工程投資不是換模型，而是兩件更便宜、能讓之後每次選型都有依據的事：一是自建一個數十段、有參考譯文、可以用 blockwise bootstrap 下結論的 VTuber 評測集；二是補上 Whisper 的後處理防線（日語 hallucination 黑名單和歌聲分類器）。

第二個啟示是，授權限制正在主導模型的排序，品質反而退居其次。ReazonSpeech 的 30 條之 4 條款同時影響 kotoba、parakeet、k2 三個日語最強的候選；HY-MT 的地區排除影響了證據最強的翻譯模型。Qwen3.5 和 OpenAI Whisper 成為預設，主要原因是它們「沒有附帶條件」，而不是「已證明最好」。這代表一旦 Reazon 對衍生模型的使用給出明文說明，或 Qwen3.5 的日文口語品質在自建集上被推翻，預設排序就應該重新評估；評測集正是讓這種重評變得便宜的基礎設施。
