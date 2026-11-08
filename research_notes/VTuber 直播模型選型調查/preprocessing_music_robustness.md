# ASR 前處理（語音增強／音源分離）與 VAD 在 BGM、遊戲音效、歌唱直播下的表現

> 調查日期：2026-11-05。範圍：kikitori（16 kHz 系統音訊 → Silero VAD v6 → Whisper/CTranslate2 → LLM 翻譯）是否該加前處理、該怎麼調 VAD。
> 標記：[論文宣稱/self-reported] = 作者或廠商自評；[獨立評測/independent] = 非模型作者的第三方評測。arXiv 未經同儕審查者標「arXiv，未審查」。
> 調查預算有限（約 20 次搜尋／擷取），部分 PDF 無法解析，缺漏列於各節 Gaps。

## Q1. 語音增強／降噪放在 Whisper 前面，會改善還是惡化 WER/CER？

### Takeaway
對 Whisper 這類大型、以大量含噪資料訓練的 ASR，單通道語音增強「全強度」套用通常沒幫助、甚至變差；主因是增強引入的 artifact（不是殘留噪音）。有證據的改善方式是「弱化增強」或把原始訊號混回去（observation adding）。目前找不到專門針對「音樂背景」或「日語」的 Whisper+增強同儕審查研究。

### Cited Findings
- Interspeech 2022（Iwamoto 等，NTT）以正交投影把增強誤差拆成「噪音成分」與「artifact 成分」，證明 artifact 才是單通道增強使 ASR 退化的主因；提出 observation adding（OA，把縮放後的原始觀測訊號加回增強輸出），在模擬與真實錄音上皆改善 ASR [論文宣稱/self-reported] — [ISCA Archive](https://www.isca-archive.org/interspeech_2022/iwamoto22_interspeech.html)；[arXiv 2201.06685](https://arxiv.org/abs/2201.06685)
- arXiv 2607.11157（Huo、Zhang、Zhang，2026，arXiv，未審查）：VoiceBank+DEMAND、增強器 FRCRN。wav2vec 2.0 WER 由 17.13 → 10.20（α=0.85 接近全強度修正），但 Whisper 只在弱增強 α=0.25 時由 6.44 → 5.57，全強度回到 6.46（≈ 無增益）；並指出傳統增強指標變好時 Whisper WER 可能變差 [論文宣稱/self-reported] — [arXiv 2607.11157](https://arxiv.org/abs/2607.11157)
- arXiv 2512.17562（2025，arXiv，未審查）「When De-noising Hurts」：MetricGAN+ 降噪在醫療語音上使 Whisper semWER 由 6.54% 升至 9.82%（50 dB SNR 條件），作者歸因於現代 ASR 本身已具雜訊強健性、增強可能移除辨識所需聲學線索 [論文宣稱/self-reported] — [arXiv 2512.17562](https://arxiv.org/pdf/2512.17562)
- EUSIPCO 2024（arXiv 2406.12699）：直接把增強輸出餵 Whisper 效果差；加入「bridge module」整合預訓練增強與 Whisper 後，相對僅增強輸入大幅降低 WER，相對原始含噪輸入在 Whisper large-v3 上只有較小增益 [論文宣稱/self-reported] — [EUSIPCO 2024 PDF](https://eurasip.org/Proceedings/Eusipco/Eusipco2024/pdfs/0000426.pdf)；[arXiv 2406.12699](https://arxiv.org/pdf/2406.12699)
- arXiv 2603.04710（2026，arXiv，未審查，作者 Islam、Nahar、Hamid）：標題即「When Audio Separation Hurts Zero-Shot ASR: Evaluating SAM-Audio with Whisper on Bengali and English Speech」；搜尋摘要指出作者歸因於分離後訊號的聲學特性與 ASR 預訓練分布不一致。PDF 內文未能解析，具體數字未取得 — [arXiv 2603.04710](https://arxiv.org/pdf/2603.04710)
- 生成式增強可能產生「類語音 artifact」與音素混淆（構音障礙語音研究，arXiv 2508.17980，未審查） — [arXiv 2508.17980](https://arxiv.org/pdf/2508.17980)
- 反方線索（不可作為證據）：dev.to 實務文主張 Whisper 前先降噪可提升準確度，但無嚴謹基準 — [dev.to（僅作線索）](https://dev.to/stevecase430/clean-audio-before-whisper-how-noise-removal-improves-transcription-accuracy-with-code-pko)

### Inferences
- 依噪音類型區分（推論）：(a) 穩態噪音（風扇、底噪）——Whisper 本身已強健，增強多半無益；(b) 音樂——傳統 SE 模型（DeepFilterNet、RNNoise、FRCRN、MossFormer2-SE）的訓練噪音集多為環境噪音，對「有旋律、有人聲的 BGM」不是設計目標，效果不可預期；(c) 競爭語音／歌聲——SE 模型會把歌聲當語音保留，無法分辨，需要音源分離或 target-speaker extraction。
- 若真的要加 SE，證據支持的做法是「部分混回原始訊號」（OA 或 α<1 的弱化），而不是全強度輸出。這對 kikitori 很便宜：`y = enhanced + β·mixture`，β 需在自家日語直播資料上調。

### Gaps
- 找不到以「音樂背景」為噪音條件、Whisper large-v3/turbo、日語為對象的同儕審查 SE→ASR 研究。
- CHiME-7/8 DASR 的結論（多數頂尖系統以多通道 GSS 而非單通道 SE 為前端）未在本輪取得一手來源，只能列為待查。
- 2512.17562、2603.04710 的作者機構與完整表格未驗證。

## Q2. 音源分離／人聲擷取模型：ONNX、延遲、大小、SDR、授權與下游 ASR 證據

### Takeaway
唯一找到的「分離→Whisper」量化研究（QMUL + AudioShake，歌詞辨識）顯示：Hybrid Demucs 分離人聲對 Whisper WER 的改善「小且不穩定」（Jam-ALT 幾乎無變化、甚至因幻覺變差），完美 stem 才有大幅改善；分離最有用的用途是當 VAD／切段依據。分離品質最高的 RoFormer 系列沒有官方 ONNX、非因果、模型大；可即時的 Band-SCNet 品質低一截。

### Cited Findings
**下游 ASR（分離後送 Whisper）**
- Syed、Meresman Higgs、Cífka、Sandler（QMUL / AudioShake，arXiv 2506.15514，2025，具 IEEE 版權聲明但頁面未標會議）：Faster-Whisper large-v2、beam 5、5 次平均。分離模型為 Hybrid Demucs `mdx`（MUSDB18 test vocal SDR 7.97 dB）與 `mdx_extra`（8.76 dB）[論文宣稱/self-reported] — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)
  - Short-form WER（Jam-ALT / MUSDB-ALT）：原混音 20.99 / 23.59；mdx 21.17 / 23.98；mdx_extra 21.08 / 20.00；真實 vocal stem —— / 14.19 — 同上
  - Long-form WER：原混音 native 23.02 / 25.82；原混音 + RMS-VAD（以分離人聲的 RMS 切段）20.35 / 22.72；分離人聲 native 22.87 / 21.90；分離人聲 + RMS-VAD 20.72 / 20.07；stem + RMS-VAD —— / 14.98 — 同上
  - 結論：分離改善可懂度相關的 substitution，但分離 artifact 會在某些情況「增加幻覺」（mdx_extra 在 Jam-ALT 上出現大量幻覺拉高 WER）；Jam-ALT 上分離除幻覺外對 WER 幾無影響；mdx_extra 訓練資料含 MUSDB-ALT 歌曲，故其 MUSDB-ALT 成績可能高估；Whisper 對非詞彙發聲（vocables）與和聲的刪除率高，即使用真實 stem 也解決不了 — 同上
  - 「以分離結果當 VAD 切段、但把原混音送 ASR」在 Jam-ALT 拿到 20.35 WER（作者宣稱開源 SOTA），優於把分離人聲送 ASR — 同上
- Albayzin 2020（BCN2BRNO，廣播語音）：以 Demucs 只保留人聲成分可去除大量背景音樂，但初步嘗試沒有 WER 改善，最終融合系統未採用 [論文宣稱/self-reported] — [arXiv 2101.12729](https://arxiv.org/pdf/2101.12729)
- 端到端 music-mixed 語音辨識研究比較「分離前端」與「直接用語音+音樂混合資料訓練 ASR」，後者是前端分離的替代方案 — [arXiv 2008.12048](https://arxiv.org/pdf/2008.12048)

**模型品質、大小、延遲**
- Band-SCNet（Interspeech 2025）：因果、輕量；MUSDB18-HQ 平均 SDR 7.79 dB、2.59M 參數、延遲 92 ms；其比較表將 BS-RoFormer 標為非因果、延遲／RTF 無資料 [論文宣稱/self-reported] — [ISCA Archive PDF](https://www.isca-archive.org/interspeech_2025/yang25d_interspeech.pdf)
- BS-RoFormer：MUSDB18-HQ（無額外資料的小版本）平均 SDR 9.80 dB；Band-SCNet 論文表中 vocals 10.66 dB [論文宣稱/self-reported] — 同上搜尋結果；Mel-Band RoFormer 論文宣稱 vocals 優於 BS-RoFormer（某設定 11.60 vs 11.02 dB）[論文宣稱/self-reported] — [Mel-Band RoFormer arXiv 2310.01809](https://www.alphaxiv.org/abs/2310.01809)
- 「Towards Practical Real-Time Low-Latency Music Source Separation」（arXiv 2511.13146，未審查）：即時模型的演算法延遲等於窗長，表中列 23 ms 延遲的即時基線（RT-STT、HS-TasNet 家族）— [arXiv 2511.13146](https://arxiv.org/pdf/2511.13146)
- HT-Demucs（v4）為 hybrid 頻譜／波形 Transformer 模型；官方 repo `facebookresearch/demucs` 授權 MIT，已封存（read-only，約 2025-01-01），作者離開 Meta，維護轉至 `adefossez/demucs` fork — [GitHub facebookresearch/demucs](https://github.com/facebookresearch/demucs)；[awesome.ecosyste.ms](https://awesome.ecosyste.ms/projects/github.com%2Ffacebookresearch%2Fdemucs)
- 社群 ONNX 匯出（非官方）：BS-RoFormer ONNX（viperx checkpoint，自報 vocals 12.9 dB、uint8 量化約 158 MB）— [HF xycld/BS-RoFormer-ONNX](https://huggingface.co/xycld/BS-RoFormer-ONNX/blob/main/README.md)；Mel-RoFormer vocals ONNX（fp16）— [HF audiojs/mel-roformer](https://huggingface.co/audiojs/mel-roformer)。兩者皆非原作者發布，權重授權與訓練資料需逐一確認（後者頁面自承比較模型訓練資料未知）。
- 線索（非證據）：一篇 2026 部落格宣稱目前沒有開源分離模型能在 CPU 上以可接受品質即時執行，與 Band-SCNet／RT-STT 論文矛盾 — [aistemsplitter.org（僅作線索）](https://aistemsplitter.org/blog/htdemucs-vs-bs-roformer-vs-spleeter-2026-benchmark)

**語音增強模型授權／ONNX**
- DeepFilterNet：程式碼 MIT 或 Apache-2.0 雙授權（擇一）；社群 ONNX 鏡像標示 MIT OR Apache-2.0 — [GitHub Rikorose/DeepFilterNet](https://github.com/rikorose/deepfilternet)；[HF DeepFilterNet3-ONNX 鏡像](https://huggingface.co/Allinsama/soniqo__DeepFilterNet3-ONNX)
- ClearerVoice-Studio（阿里巴巴通義實驗室 / modelscope）：Apache-2.0；MossFormer2-SE-48K 模型與其 ONNX 匯出標 Apache-2.0；舊的 `alibabasglab/MossFormer2` repo 標 MIT（來源不一致）；第三方 wrapper 指出其下載的去殘響權重為 GPL-3.0 —— 需避開 — [GitHub modelscope/ClearerVoice-Studio](https://github.com/modelscope/ClearerVoice-Studio)；[HF MossFormer2-SE-48K-ONNX](https://huggingface.co/ipsilondev/MossFormer2-SE-48K-ONNX)；[GitHub alibabasglab/MossFormer2](https://github.com/alibabasglab/MossFormer2)；[crxssrazr93/clearervoice-studio](https://github.com/crxssrazr93/clearervoice-studio)

### Inferences
- 對 kikitori 而言：HT-Demucs（MIT）是授權最乾淨的高品質分離器，但沒有官方 ONNX、上游已封存；RoFormer 系列品質最好但 ONNX 全為社群匯出、權重來源與授權需逐一查；Band-SCNet 是唯一找到具「因果 + 92 ms + 2.59M 參數」數據的候選，但品質（7.79 dB 平均）低於 Hybrid Demucs mdx_extra，而 QMUL 研究顯示即使 8.76 dB 的 vocal 分離送 Whisper 也幾乎沒有穩定增益 → 低品質即時分離送 ASR 的期望值更低。
- 「分離只用來決定切段／判斷有沒有人聲，ASR 仍吃原混音」是證據最支持的用法，且可避免分離 artifact 誘發幻覺。
- VTuber 歌回的「歌聲」會被分離器保留成 vocals，分離不會把歌聲與講話分開；對 BGM 有人聲歌曲（含歌詞 BGM）的情境，分離器同樣無法分辨主播講話與 BGM 歌手。

### Gaps
- 未取得各模型官方 RTF／CPU、GPU 延遲（HT-Demucs 在 10 s 片段、onnxruntime 下的實測）；未找到 SDX23 報告中的具體數字。
- Open-Unmix、Spleeter、MDX-Net（UVR）、SCNet 原版、RNNoise、NVIDIA Maxine 的授權與 ONNX 狀態本輪未驗證。依過去認知（未驗證，需查官方 LICENSE）：Open-Unmix MIT、Spleeter MIT、RNNoise BSD-3-Clause、Silero VAD MIT；NVIDIA Maxine 為 NVIDIA 專有 SDK，非 MIT 相容；UVR 社群 MDX-Net 權重授權常不明確。
- 未找到以「遊戲音效」為干擾、或以「日語直播」為對象的分離→ASR 量測。

## Q3. 串流／因果 vs 離線：短片段（≤ 10 s）上分離品質會掉多少？

### Takeaway
高品質分離器（HT-Demucs、BS/Mel-RoFormer）是非因果、設計給整首歌的離線模型；因果／低延遲模型（Band-SCNet、HS-TasNet、RT-STT）存在且有論文數據，但 SDR 明顯較低。kikitori 已經以 VAD 切出 ≤ 10 s 片段，片段級「離線」推論在架構上可行，但延遲預算（< 100–200 ms/utterance）對大型分離器很緊。

### Cited Findings
- Band-SCNet：因果、92 ms 延遲、7.79 dB 平均 SDR、2.59M 參數；同表 BS-RoFormer 標為非因果 [論文宣稱/self-reported] — [Interspeech 2025 Band-SCNet](https://www.isca-archive.org/interspeech_2025/yang25d_interspeech.pdf)
- 即時分離模型的演算法延遲等於窗長，文中即時基線約 23 ms — [arXiv 2511.13146（未審查）](https://arxiv.org/pdf/2511.13146)
- QMUL 研究的 short-form 設定（以片段為單位送 Whisper）中，分離人聲的 WER 增益不穩定，long-form 以分離 RMS 切段才得到一致改善 — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)

### Inferences
- 因為 VAD 先切段、分離只處理片段，kikitori 不需要真正的 streaming 分離器；但若要讓「分離決定切段」（QMUL 的用法），分離就得在 VAD 之前、對連續串流執行，那就必須是因果或小 chunk 模型（Band-SCNet 類），且會整段時間持續吃 GPU/CPU。
- 依常理推估（未量測）：HT-Demucs 的預設推論以數秒為 segment 並重疊，處理 10 s 片段在消費級 GPU 上應可在數百 ms 內完成，但很可能超出 100–200 ms 目標，CPU 更慢；需實測。

### Gaps
- 找不到「以 ≤ 10 s 片段做分離 vs 整首歌做分離」的 SDR 損失量化研究。
- 找不到 HT-Demucs／RoFormer 於 onnxruntime 的官方延遲數據。

## Q4. VAD 在 BGM 與歌唱下的行為；歌聲是否被當成語音；speech/music 分類器；歌詞辨識

### Takeaway
沒有任何公開基準直接測 Silero／TEN VAD 在「語音 + 音樂」或「歌唱」條件下的表現；Silero 官方評測只涵蓋會議、電話、環境噪音（ESC-50）等。二元 VAD 一般會把歌聲判為語音（FireRedVAD 文件明言），因此歌回時 VAD 會一直開，需要額外的 speech/music/singing 分類器（例如 CED，有 ONNX 匯出腳本）才能「跳過或標記」歌唱片段。Whisper 對歌詞辨識有系統性弱點（刪除和聲、非詞彙發聲）。

### Cited Findings
- Silero VAD 官方品質指標（自評）：資料集為 ESC-50、AliMeeting、Earnings21、MSDWild、AISHELL-4、VoxConverse、LibriParty、私有噪音／語音，未包含音樂或歌唱資料集。Multi-Domain ROC-AUC：Silero v6 0.97、v5 0.96、TEN VAD 0.93、WebRTC 0.73；ESC-50（純噪音）正確率：v6 0.87、v5 0.61、TEN 0.42、WebRTC 0；私有噪音：v6 0.71、TEN 0.47、WebRTC 0.15；ROC-AUC 以 31.25 ms 區段計算 [論文宣稱/self-reported，Silero 自評，TEN 為其對手] — [silero-vad Quality Metrics wiki](https://github.com/snakers4/silero-vad/wiki/Quality-Metrics)
- TEN VAD 模型頁宣稱精確度優於 WebRTC VAD 與 Silero VAD [論文宣稱/self-reported] — [HF TEN-framework/ten-vad](https://huggingface.co/TEN-framework/ten-vad)；與上列 Silero 自評結果互相矛盾，兩者皆為自評。
- arXiv 2610.14607「Window Size Matters」（2026，未審查）：在 LibriVAD-small（含噪語音）比較 TEN、Silero、RMS 基線；TEN 對 frame 長度幾乎不敏感，Silero 只在原生 32 ms 附近與 TEN 相當、20 ms 時降至 0.63（作者認為可能是其固定 frame 協定所致）；僅測噪音，非音樂 [獨立評測/independent] — [arXiv 2610.14607](https://arxiv.org/abs/2610.14607)
- FireRedVAD（FireRedTeam）：宣稱在 FLEURS-VAD-102 上優於 Silero、TEN（列 TEN AUC-ROC 97.81、Silero 97.99），並輸出 singing / music 事件標籤；其串流模型為二元分類，文件明言「歌唱被視為正例（輸出），純音樂被拒絕」[論文宣稱/self-reported] — [GitHub FireRedTeam/FireRedVAD](https://github.com/FireRedTeam/FireRedVAD)；[docs.rs firered-vad](https://docs.rs/crate/firered-vad/0.1.0)
- silero-vad issue tracker 有 #565「Issue with silero-vad in Song Detection」，內容未能確認 — [ecosyste.ms issues 鏡像](https://issues.ecosyste.ms/hosts/GitHub/repositories/snakers4%2Fsilero-vad/issues)
- 直播音樂影片的音視訊 VAD 研究指出：音樂中的背景聲與歌聲會造成誤判，語音活動偵測器與音樂片段匹配不佳，因為歌唱與說話的發聲方式不同 — [arXiv 2010.14168](https://arxiv.org/pdf/2010.14168)
- AVA-Speech（Google，Interspeech 2018）：電影語音活動標註，分「乾淨語音／語音+音樂／語音+噪音」三種條件，是測 speech-over-music 的標準資料集 — [ISCA Archive](https://www.isca-archive.org/interspeech_2018/chaudhuri18_interspeech.html)；AVASpeech-SMAD（2021）加入 frame-level 音樂標註，SOTA 系統 music/speech F1 約 80% / 77% — [arXiv 2111.01320](https://ar5iv.arxiv.org/html/2111.01320)
- CED（小米，ICASSP 2024）：AudioSet 音訊標註；CED-Tiny 5.5M 參數 48.1 mAP、Mini 9.6M 49.0、Small 22M 49.6、Base 86M 50.0；16 kHz 輸入、64 維 Mel；repo 附 `export_onnx.py`，並提到 sherpa（k2-fsa）提供二進位 [論文宣稱/self-reported] — [GitHub RicherMans/CED](https://github.com/richermans/ced)；[arXiv 2308.11957](https://arxiv.org/pdf/2308.11957)
- 歌詞辨識：見 Q2 的 QMUL 研究 —— Whisper large-v2 在歌曲原混音上的 WER 約 21–26%，非詞彙發聲與和聲刪除率高 [論文宣稱/self-reported] — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)

### Inferences
- Silero VAD v6 是二元語音偵測器，訓練與評測資料都沒有歌唱類別；依 FireRedVAD 文件與音樂影片 VAD 研究，合理預期「歌聲 → 判為語音」「帶人聲的 BGM → 判為語音」「純器樂 BGM／遊戲音效 → 大多被拒絕但可能有誤報」。
- 對 kikitori：(1) 拉高 Silero threshold（例如 0.5 → 0.6–0.7）可減少 BGM 誤報，但會吃掉主播小聲說話，需在自家資料上掃描；(2) 對「歌唱」最可靠的處理是加一個 AudioSet 類分類器（CED-Tiny/Mini ONNX，AudioSet 本體含 Speech、Singing、Music 類別）對每個 VAD 片段打分，Singing/Music 分數高時標記為「♪」或跳過翻譯；這只多一次對 ≤ 10 s 片段的小模型推論，延遲應遠小於 Whisper。
- 想要量化而非猜測，可以用 AVA-Speech 的「語音+音樂」條件，再加上自錄的日語歌回片段，做小型內部基準。

### Gaps
- 找不到 Silero、TEN、pyannote、NeMo MarbleNet/Frame-VAD 在 AVA-Speech 「speech with music」子集上的公開數字；AVA-Speech 原始論文中的分條件 F1 未取得。
- CED 的 LICENSE 未確認（README 有 License 標題但搜尋未取得內容）；YAMNet、PANNs、BEATs 的 ONNX 與授權本輪未查。
- silero-vad #565 的內容與維護者回應未確認。
- 未找到 Whisper 在日語歌曲上的歌詞辨識評測。

## Q5. Whisper 在音樂／非語音上的幻覺與緩解

### Takeaway
Whisper 在非語音上會大量幻覺（ICASSP 2025：large-v3 對 40.3% 非語音輸入輸出文字），且幻覺可能含有害內容（FAccT 2024）；停頓越長越容易幻覺。實證有效的緩解：VAD 閘控／更好的切段、幻覺片語黑名單（bag of hallucinations）、只微調少數 decoder head（Calm-Whisper）。注意：ICASSP 2025 研究明確排除了音樂，故沒有「音樂上幻覺率」的直接數字。

### Cited Findings
- Barański 等（AGH，ICASSP 2025，DOI 10.1109/ICASSP49660.2025.10890105）：Whisper large-v3 在 301,317 段非語音音訊上，40.3% 產生非空轉寫；常見輸出為重複的致謝、字幕風格片語、填充詞、掌聲標記、擬聲詞；測 1–30 s 片段長度與「語音+非語音」混合；提出 bag of hallucinations（BoH）後處理過濾已知幻覺片語，可降低 WER。**資料集排除所有音樂**（因無法可靠區分器樂與人聲歌曲）[論文宣稱/self-reported] — [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)
- Calm-Whisper（Interspeech 2025，Wang 等，含 Mirco Ravanelli）：Whisper-large-v3 decoder 20 個 self-attention head 中只有 3 個造成 UrbanSound 上 >75% 幻覺；只以「非語音 + 空白標籤」微調這 3 個 head，非語音幻覺降低 >80%，LibriSpeech test-clean/other WER 退化 <0.1% [論文宣稱/self-reported] — [ISCA Archive](https://www.isca-archive.org/interspeech_2025/wang25b_interspeech.html)；[arXiv 2505.12969](https://arxiv.org/abs/2505.12969)
- Careless Whisper（Koenecke 等，FAccT 2024）：2023 年 4–5 月的 Whisper API 實驗中 1.4% 音訊片段出現幻覺序列，其中 38% 含明確傷害（暴力、捏造關聯、假權威）；說話停頓越長（失語症）越容易幻覺；康乃爾新聞稿指出 OpenAI 之後已改善、幻覺率下降 [獨立評測/independent] — [arXiv 2402.08021](https://arxiv.org/html/2402.08021)；[Cornell Chronicle](https://news.cornell.edu/stories/2024/06/ai-speech-text-can-hallucinate-violent-language)
- 分離 artifact 本身會誘發幻覺；切段邊界切到人聲中間被假設為幻覺成因；以 RMS-VAD 切段對所有音訊型態都降低幻覺率 — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)
- 後續工作：「Reducing Hallucinated Transcripts in Whisper via Hallucination Space Projection」（arXiv 2609.04561，未審查）— [arXiv 2609.04561](https://arxiv.org/pdf/2609.04561)

### Inferences
- kikitori 已有 VAD 閘控（Silero v6，≤ 10 s），這是文獻中最主要的緩解手段；但歌回與帶人聲 BGM 會「穿過」VAD，屬於 Whisper 最容易幻覺的區域（非典型語音、長拉音）。
- 低成本可做（不需新模型）：(1) 用 CTranslate2/faster-whisper 的 `no_speech_threshold`、`log_prob_threshold`、`compression_ratio_threshold` 與 `condition_on_previous_text=False` 過濾；(2) 建一份日語版 BoH（例如「ご視聴ありがとうございました」「チャンネル登録」之類字幕風格片語——此為依 BoH 論文類型推論的例子，需用自家資料統計）；(3) 保證切段不切在人聲中間（VAD padding）。
- Calm-Whisper 需要微調並轉回 CTranslate2，權重若由作者釋出，轉換在技術上可行，但本輪未確認權重授權與是否有 turbo/日語相容版本。

### Gaps
- 沒有找到 Whisper 在「純 BGM」「遊戲音效」「日語歌唱」上的幻覺率量測。
- Calm-Whisper 權重是否公開及授權未確認。
- faster-whisper 內建 VAD／閾值預設值對音樂片段的效果無公開評測。

## Q6. 實務建議：預設加分離、作為 BGM 選項、還是不加？

### Takeaway
證據不足以「預設」加入語音增強或音源分離：對 Whisper 而言全強度 SE 通常無益或有害，分離→Whisper 的唯一量化研究增益小且不穩，還會誘發幻覺，且高品質分離器沒有官方 ONNX、延遲超出預算。較有根據的投資順序是：VAD 閾值調校 → 幻覺過濾 → speech/singing 分類器 → （實驗性選項）分離只用於切段或弱混合。

### Cited Findings
- 單通道 SE 的 artifact 是 ASR 退化主因；把原始訊號混回可改善 — [Iwamoto 等 Interspeech 2022](https://www.isca-archive.org/interspeech_2022/iwamoto22_interspeech.html)
- Whisper 只在弱增強（α=0.25）時改善，全強度無益 — [arXiv 2607.11157（未審查）](https://arxiv.org/abs/2607.11157)
- Demucs 分離人聲送 Whisper：Jam-ALT 上 WER 無改善（20.99 → 21.08/21.17），分離 artifact 誘發幻覺；以分離訊號切段、原混音送 ASR 才有一致增益 — [arXiv 2506.15514](https://arxiv.org/html/2506.15514v1)
- 廣播語音上 Demucs 前端無 WER 改善 — [arXiv 2101.12729](https://arxiv.org/pdf/2101.12729)
- Whisper 非語音幻覺普遍（40.3%），BoH 過濾與 VAD 有效 — [arXiv 2501.11378](https://arxiv.org/abs/2501.11378)；[Calm-Whisper](https://arxiv.org/abs/2505.12969)
- 因果分離器（Band-SCNet）延遲 92 ms、平均 SDR 7.79 dB — [Interspeech 2025](https://www.isca-archive.org/interspeech_2025/yang25d_interspeech.pdf)

### Inferences
- 建議分級（推論）：
  1. **不加為預設**：維持原混音送 Whisper。
  2. **立即可做、零新模型**：在 BGM／歌回直播上掃描 Silero threshold、min_silence、speech_pad；開啟 Whisper 的 no-speech／compression-ratio 過濾；加日語 BoH 黑名單。
  3. **下一步（小模型、低延遲、授權需確認）**：CED-Tiny/Mini ONNX 對每個 VAD 片段判斷 Speech vs Singing/Music，歌唱片段標記「♪」或不翻譯——直接對應 VTuber 歌回情境，且不改變送進 Whisper 的音訊。
  4. **實驗性選項（預設關閉，「BGM 很重」時手動開）**：若要試分離，用 MIT 授權的 HT-Demucs（需自行匯出 ONNX、需實測延遲），且優先「分離只用於 VAD 判斷／切段，ASR 仍吃原混音」；若真要把分離訊號送 ASR，採弱混合 `vocals + β·mixture` 而非純 vocals。必須在自家日語直播測試集（分 BGM 音量級距、含遊戲音效與歌回）上以 CER 與幻覺率驗證後才可升級為預設。
  5. **不建議**：DeepFilterNet/RNNoise 等環境降噪器用於 BGM（不是其設計目標，且 SE artifact 對 Whisper 不利）；社群 RoFormer ONNX（權重授權與來源不明、模型大）。

### Gaps
- 缺乏任何針對日語 VTuber 直播（BGM + 遊戲音效 + 歌唱）的公開評測集；結論高度依賴英語／歌詞／合成噪音研究的外推，需以自建小型測試集驗證。
