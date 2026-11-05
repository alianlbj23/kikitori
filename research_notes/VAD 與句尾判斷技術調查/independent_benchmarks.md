# 串流 VAD 與句尾（end-of-turn / endpoint）判斷：獨立評測與評測資料集調查

> 標記規則：每項效能敘述標為「論文或官方宣稱」（作者／廠商自己測自己）或「有獨立評測」（第三方，註明評測者）。由競品廠商發布的比較標為「競品評測（非中立）」。arXiv 預印本標註「尚未經同行審查」。
> 已排除：Medium、個人部落格、廠商行銷文（如 Picovoice 部落格）、HF 論壇自報結果。
> 調查日期：2026-11-05。

## Q1：2023 年後有哪些同行審查論文獨立比較多個 VAD？結果、資料集、指標為何？

### Takeaway
真正「獨立、同行審查、多 VAD 並列」的論文非常少；找到最具體的是 Interspeech 2024（Stellenbosch 大學）把 Silero 當現成基線在 AVA-Speech 上評測，但其任務定義（只把 clean speech 當正類）不利於 Silero，不能直接解讀為「Silero 在有音樂時很差」。其餘多 VAD 對比多為 2026 年 arXiv 預印本（尚未經同行審查）或模型作者自己的 README。

### Cited Findings
- **Karan et al., "A Transformer-Based Voice Activity Detector", Interspeech 2024（Stellenbosch University，與 Silero 無關）— 有獨立評測（評測者：Karan, Jansen van Vüren, de Wet, Niesler）**：
  - 資料集：AVA-Speech（160 部 YouTube 電影、約 40 小時；類別 Clean-Speech / Speech+Music / Speech+Noise / Non-Speech）及 48 集南非肥皂劇（多語、低資源）。— [ISCA PDF](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)（DOI 10.21437/Interspeech.2024-1019）
  - **關鍵任務定義**：二分類為「Clean-Speech」vs「Other」，Speech+Music 與 Speech+Noise 都算負類；Silero 為現成模型、未在 AVA 上訓練，其他兩個模型在 AVA 訓練集上訓練。— [同上](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)
  - AVA-Speech test：Wav2Vec2-XLS-R AUC 96.2%、CNN-BiLSTM 94.8%、Silero 81.9%；TPR@FPR10%：88.9% / 84.4% / 26.5%。肥皂劇 test：AUC 92.2% / 80.8% / 74.6%；TPR@FPR10% 71.6% / 39.8% / 28.0%。— [同上](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)
  - 作者自己指出：Silero 文獻上在 AVA-Speech 抽樣 4 小時約 AUC 90%，此處較低被推測是「正類只含 clean speech」所致。— [同上](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)
- **"Window Size Matters: Benchmarking Voice Activity Detectors in Noise"（arXiv 2610.14607，Kumar, Srivastava, Purwar 等，頁面未列機構；尚未經同行審查）— 有獨立評測（作者與 TEN/Silero 無已知關係，但機構未確認）**：
  - 資料：LibriVAD-small test-clean（LibriSpeech 衍生、英語），9 類噪音、6 個 SNR（-5～20 dB），2,214 段、約 7.1 小時。比較 TEN VAD、Silero VAD、RMS 能量基線。指標：frame-level ROC-AUC、AP、F1@0.5。— [arXiv](https://arxiv.org/abs/2610.14607)
  - 結果：TEN 在 16 ms hop AUC 0.917 / AP 0.968 / F1 0.913；20–50 ms 視窗 AUC 維持 0.916–0.919；Silero 僅在其原生 32 ms 輸入附近與 TEN 相當，在 20 ms 視窗掉到 0.63（作者懷疑是其固定 frame 評測協定造成——補零、子塊平均、未重置狀態——但未驗證）；RMS 約 0.75–0.77；TEN 在低 SNR babble 噪音接近隨機。— [arXiv](https://arxiv.org/abs/2610.14607)
  - 方法學提醒：全判為語音的 trivial 偵測器 F1 也有 0.854，作者認為 F1@0.5 在此資料上是弱指標。— [arXiv](https://arxiv.org/abs/2610.14607)
- **"VAD to the Bone: Ultra-Tiny Speech Activity Detection for Edge Deployment"（arXiv 2607.25870；尚未經同行審查）**：搜尋摘要指出文中警告兩種推論協定（causal vs 非因果 sliding window）的結果不可直接比較。我只看到搜尋摘要、未讀全文。— [arXiv PDF](https://arxiv.org/pdf/2607.25870)
- **S4VAD（arXiv 2609.11110，Cohen, Netzer, Habi；尚未經同行審查）**：主張 AUROC 不能反映時間反應性，提出以機率模型估計「speech onset latency」的評測方法；摘要未列具名基線（未確認是否含 Silero/TEN）。— [arXiv](https://arxiv.org/abs/2609.11110)
- **MagicNet（arXiv 2405.16797；尚未經同行審查）**：與 WebRTC、MarbleNet、CRNN 比較，作者自家模型勝出 — 屬論文或官方宣稱（作者提出新模型）。僅見搜尋摘要。— [arXiv PDF](https://arxiv.org/pdf/2405.16797)
- **官方／作者自家比較（論文或官方宣稱，非獨立）**：
  - TEN VAD README：在 LibriSpeech、GigaSpeech、DNS Challenge 音訊人工標註的測試集上，以 PR 曲線宣稱優於 WebRTC（pitch-based）與 Silero；文字中無具體數值；宣稱 Silero 在語音→非語音轉換處有「數百毫秒延遲」；RTF 例：Xeon Gold 6348 上 TEN 0.0086 vs Silero 0.0127；函式庫 306KB vs Silero ONNX 2.22MB。測試集已放在 repo `testset` 目錄。授權為「Apache 2.0 with additional conditions」（需讀 LICENSE 確認附加條件是否與 MIT 專案相容）。— [TEN VAD GitHub](https://github.com/TEN-framework/ten-vad)
  - FireRedVAD（小紅書 FireRed Team）：在自建 FLEURS-VAD-102（約 9,443 檔、102 語言、每語言約 100 檔、人工標註）上宣稱 F1 97.57% vs Silero 95.95% vs TEN 95.19% vs FunASR-VAD 90.91% vs WebRTC 52.30%；AUC 99.60 / 97.99 / 97.81；誤報率 2.69% / 9.41% / 15.47%；漏報 3.62% / 3.95% / 2.95%。測試集當時標示「coming soon」，未確認已公開。— [HF model card](https://huggingface.co/FireRedTeam/FireRedVAD)；[FireRedASR2S 論文 arXiv 2603.10420](https://arxiv.org/abs/2603.10420)；[GitHub](https://github.com/FireRedTeam/FireRedVAD)
  - Silero 官方：Multi-Domain ROC-AUC v5 0.96 → v6 0.97（來自搜尋摘要，未直接讀 Silero wiki）。— 論文或官方宣稱，來源待補（見 Gaps）。

### Inferences
- TEN vs Silero 在「英語＋加性噪音」上，唯一的第三方預印本顯示兩者在 Silero 原生 chunk 大小下 AUC 相近（約 0.92）；TEN「明顯優於 Silero」的說法目前只有 TEN 官方 README 支持。
- FireRedVAD 的大幅領先（尤其誤報率）只有作者自家數據；Silero 與 TEN 在 FLEURS-VAD-102 的 F1 差距（95.95 vs 95.19）很小，與上述第三方結果方向一致：兩者大致同級。
- Karan 2024 顯示：若應用要的是「純淨語音」（排除帶 BGM 的語音），Silero 現成模型在高精確率端很弱（TPR@FPR10% 僅 26.5%）；反過來說，這也暗示 Silero 會對 Speech+Music/Speech+Noise 段大量觸發——對 Scenario A（BGM 下仍要翻譯對白）其實可能是想要的行為。
- 評測協定（frame 大小、是否重置狀態、causal vs 非因果）會大幅改變結果；kikitori 本地評測必須對每個 VAD 使用其原生 chunk 與串流協定。

### Gaps
- 找不到 2023 年後同行審查論文同時比較 Silero、WebRTC、TEN、FireRedVAD、pyannote、MarbleNet、FSMN-VAD 中三者以上的結果。
- 找不到任何對 FireRedVAD 或 FSMN-VAD（FunASR）的獨立評測；FunASR-VAD 唯一數字來自 FireRed 自家表格。
- CEUR-WS 上一篇義大利語 VAD 研究（[CEUR-WS Vol-3878](https://ceur-ws.org/Vol-3878/111_main_long.pdf)）據搜尋摘要稱 pyannote > Silero > WebRTC，但我未讀全文；CEUR-WS 屬小型區域研討會論文集，需標記低權重。
- Silero 官方 quality metrics 頁面未直接查證（v5/v6 AUC 數字僅來自搜尋摘要）。
- 2610.14607 作者機構未確認，無法排除利益關係。

## Q2：是否有獨立評測顯示 VAD 會把歌聲當語音？背景音樂下表現如何？

### Takeaway
有一篇同行審查（IEEE WASPAA 2025，Fraunhofer IIS / AudioLabs Erlangen）直接研究「SAD 把歌聲誤判為語音」並提出 AUC_SiRR 指標；結論是只用語音訓練的模型大多會把歌聲判為語音。但該文的基線並非 Silero/TEN/WebRTC 等常用串流 VAD，因此「Silero/TEN 對歌聲的行為」目前沒有找到獨立數據。

### Cited Findings
- **Grundhuber, Halimeh, Strauß, Habets, "Robust Speech Activity Detection in the Presence of Singing Voice"，IEEE WASPAA 2025（Fraunhofer IIS & International Audio Laboratories Erlangen）— 有獨立評測（對其基線而言）**：
  - 摘要：SAD 系統常把歌聲誤標為語音；其模型在拒斥歌聲的同時語音偵測 AUC 0.919。— [arXiv 2512.09713](https://arxiv.org/abs/2512.09713)
  - 訓練：DNS Challenge 2021 語音與噪音、MUSDB18-HQ（移除 rap／說話的歌曲）；測試：EARS（語音）、MoisesDB（音樂與歌聲混音）、WHAM!（babble 等噪音）。— [arXiv HTML](https://arxiv.org/html/2512.09713)
  - 基線 AS-pVAD、STA-VAD、ResNet50 在純歌聲 MoisesDB 上的正確率（越高＝越能把歌聲當非語音）：訓練資料含 80% 語音時 0.9598 / 0.5341 / 0.8161；**純語音訓練（p_s=100%）時降到 0.1902 / 0.4342 / 0.0343**，即大多把歌聲判為語音。— [arXiv HTML](https://arxiv.org/html/2512.09713)
  - 新指標 AUC_SiRR：以語音 TPR 對歌聲拒斥率（Singing Rejection Rate＝歌聲幀的 TNR）掃門檻畫曲線取面積，完美為 1。— [arXiv HTML](https://arxiv.org/html/2512.09713)
- **AVA-Speech 的標註慣例**：資料集論文把 a cappella 與 rap 歸在 Speech+Music，歌聲並非獨立類別——因此 AVA-Speech 無法單獨衡量「歌聲誤觸發」。— [AVA-Speech arXiv 1808.00606](https://arxiv.org/pdf/1808.00606)
- 另一篇噪音穩健 VAD 研究因訓練資料無歌聲而把歌聲視為非語音（說明不同論文對「歌聲是否算語音」定義不一）。— [arXiv 2102.07445](https://arxiv.org/pdf/2102.07445)（2021，早於主要時間範圍）
- Karan 2024（Interspeech）四分類實驗中，模型會混淆 Speech+Noise 與 Speech+Music；Silero 二分類結果見 Q1。— [ISCA PDF](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)

### Inferences
- 對 Scenario A（動畫 OP/ED、VTuber 唱歌）：一般以語音資料訓練的 VAD 很可能會把歌聲當語音（WASPAA 2025 的基線行為支持此推論），所以需要在 VAD 之後另加歌聲／音樂判斷，或接受歌詞被送去翻譯。
- 「歌聲該不該算語音」是產品決策：翻譯歌詞可能有用也可能是噪音；評測時應把「speech」「speech+music」「singing」分開計分，而不是合成一個 AUC。

### Gaps
- 沒找到任何第三方對 Silero、TEN、WebRTC、FireRedVAD、pyannote 在歌聲資料上的量化評測。
- WASPAA 2025 的模型與 AUC_SiRR 評測程式是否開源未確認。
- 未找到針對日語動畫／VTuber 類素材（BGM＋多人＋歌聲）的公開 VAD 基準。

## Q3：Smart Turn / LiveKit / TEN Turn Detection / VAP 是否有作者以外的評測？

### Takeaway
有第三方數字，但幾乎都來自「競品廠商」而非中立學術單位：LiveKit 的 eot-bench（評 Smart Turn、VAP 等）與小紅書 FireRedChat（評 LiveKit、TEN）。兩者都把自家模型排第一，應視為「競品評測（非中立）」。VAP 的多語評測主要出自原作者群（京都大學 Inoue 等），找不到中立團隊對這四者的同行審查並列比較。

### Cited Findings
- **LiveKit eot-bench（LiveKit 官方 repo；對 LiveKit 自家模型＝論文或官方宣稱；對 Smart Turn / VAP＝競品評測，非中立）**：
  - 資料：真實人對 agent 的使用者話輪，音訊＋文字脈絡，每個 ≥100 ms 停頓都有標註；最後停頓標 `eot`、其餘標 `hold`；14 語言，含英、中、日。程式與資料皆 Apache-2.0。— [GitHub livekit/eot-bench](https://github.com/livekit/eot-bench)
  - 協定：在每個停頓做因果判斷，掃 threshold、action delay、timeout；主指標為 false-cutoff rate（在句中停頓觸發的比例）與 latency（使用者真正說完後的等待時間，不是推論時間）；報告固定 false-cutoff 預算下的最佳延遲與 Pareto 前緣；AUC/AP 僅診斷用。— [GitHub](https://github.com/livekit/eot-bench)
  - 英語結果：false cut-offs @300 ms — LiveKit v1 9.9%、SmartTurn v3.2 35.2%、VAP（silent agent）46.9%、VAD baseline 55.6%；latency @5% cutoff — 543 / 1051 / 1131 / 1600 ms；@10% — 295 / 739 / 749 / 1000 ms。— [GitHub](https://github.com/livekit/eot-bench)；[LiveKit leaderboard](https://livekit.com/benchmarks/eot-bench)
  - VAP 基線使用 viks66/VAP_checkpoints 的 oto.ckpt，條件 inactivity_prob > 0.5（來自搜尋摘要）。— [GitHub](https://github.com/livekit/eot-bench)
  - eot-bench 未納入 TEN Turn Detection。— [GitHub](https://github.com/livekit/eot-bench)
- **FireRedChat（arXiv 2509.06502，小紅書 FireRed Team；尚未經同行審查；對 LiveKit/TEN＝競品評測，非中立）**：在 **TEN 官方 TEN_Turn_Detection 測試集**（純文字、語意完整/不完整二分類）上：LiveKit（373M）中文 平均 70.8%（finished 92.6 / unfinished 45.3）、英文 86.2%；TEN（7B）中文 95.8%、英文 94.4%；FireRedChat（170M）中文 96.0%、英文 94.9%。Barge-in：LiveKit（Silero VAD）T90 140 ms、誤打斷率 33.4%；TEN（TEN VAD）90 ms、78.1%；FireRedChat 170 ms、10.2%。— [arXiv HTML](https://arxiv.org/html/2509.06502)
- **VAP 多語（作者群自評＝論文或官方宣稱）**：Inoue 等 "Multilingual Turn-taking Prediction Using Voice Activity Projection"（LREC-COLING 2024）以英、中、日訓練的多語 VAP 與單語模型表現相當；壓平音高對日語與中文影響大於英語。— [arXiv 2403.06487](https://arxiv.org/html/2403.06487v1)
- 其他 VAP 延伸（皆作者群／同領域，非中立對比）：Prompt-guided VAP 以約 950 小時日語對話，S/H 預測由 77.17 提升至 79.80（SIGDIAL 2025）— [ACL Anthology](https://aclanthology.org/2025.sigdial-1.9.pdf)；三人對話 VAP 僅在日語 TEIDAN 語料評測（Interspeech 2025）— [ISCA PDF](https://www.isca-archive.org/interspeech_2025/elmers25_interspeech.pdf)；IWSDS 2026 多語連續 backchannel 預測並釋出 VAP 即時執行程式 — [ACL Anthology](https://aclanthology.org/2026.iwsds-1.23.pdf)
- **Talking Turns（ICLR 2025，CMU 等）**：評測音訊基礎模型（如全雙工語音模型）的 turn-taking 行為，屬獨立學術基準，但對象不是 Smart Turn/LiveKit/TEN 這類輕量偵測器（僅搜尋摘要，未讀全文）。— [arXiv 2503.01174](https://arxiv.org/pdf/2503.01174)
- **"Less can be More: What Aspects of Speech Drive End-of-Turn Detection"（arXiv 2609.11066，Sharon, A, Hacioglu, Stolcke；尚未經同行審查）**：自家三模態分類器消融，聲學＋韻律達 utterance F1 0.93、誤報 7.8%、中位延遲 400 ms；加入文字反而增加過早判斷。摘要未提 Smart Turn/LiveKit/TEN/VAP。— [arXiv](https://arxiv.org/abs/2609.11066)

### Inferences
- 兩份競品評測結論互相矛盾的部分恰好反映測試集偏向：LiveKit 在自家 eot-bench 第一，但在 TEN 的文字測試集上中文 unfinished 只有 45.3%；每家都在自己的資料上領先。
- eot-bench 是目前唯一公開、多語（含日中英）、以「固定誤切率下的延遲」為主指標、且 Apache-2.0 可下載的 EoT 基準，即使出自 LiveKit，其**資料與協定**仍可被 kikitori 拿來做本地中立重跑。
- 注意 eot-bench 資料是「人對 agent」語音，與 Scenario A（動畫／VTuber 多人說話、有 BGM）落差很大；與 Scenario B（面對面對話）也不同（人對人會有重疊、backchannel）。
- TEN Turn Detection 是 7B 文字模型，對 CPU 即時、無 PyTorch 的限制而言實用性存疑（推論，未查證其量化版本）。

### Gaps
- 找不到中立學術團隊（非 LiveKit、TEN、Pipecat/Daily、FireRed）對 Smart Turn、LiveKit turn detector、TEN Turn Detection 的同行審查評測。
- Speko（benchmarks.speko.ai）有 200 段英語 clip 比較 Smart Turn v3.2 等，但為商業評測網站部落格、非同行審查，依限制不採用（僅供線索：[Speko](https://benchmarks.speko.ai/blog/voice-agent-turn-taking-benchmark/)）。
- 未找到 VAP 在 eot-bench 以外、由非作者群執行、與 Smart Turn 並列的評測。

## Q4：哪些評測資料集可免費在本地使用？其授權？（含日英中對話 turn-taking 資料）

### Takeaway
VAD／音樂情境：MUSAN、AVA-Speech 標註、OpenBMAT 為 CC BY 4.0，可自由用於本地測試（AVA 音訊需自 YouTube 取得）。EoT：LiveKit eot-bench-data（Apache-2.0，含日中英）是最容易取得的。對話語料方面，Switchboard/Fisher（LDC 付費）、CEJC（NINJAL 付費）都不免費；中文 MagicData-RAMC 為非商業授權，有衝突說法需確認。

### Cited Findings
- **MUSAN**（音樂、語音、噪音錄音；11 GB）：CC BY 4.0。— [OpenSLR 17](https://www.openslr.org/17/)
- **AVA-Speech**：只發布標註 CSV `ava_speech_labels_v1.csv`（160 部「仍在 YouTube 上」的影片），標註為 CC BY 4.0；媒體需自行取得。— [Google AVA download](https://sites.research.google/gr/ava/download/)；類別 Clean/Speech+Music/Speech+Noise/Non-Speech — [arXiv 1808.00606](https://arxiv.org/pdf/1808.00606)
- **OpenBMAT**（UPF MTG；27 小時以上、4 國電視廣播、1,647 段 1 分鐘片段，標註音樂與其相對響度）：Zenodo 記錄為 CC BY 4.0、Open；但較早的 UPF/MTG 公告稱只接受非營利用途，兩者衝突。Zenodo 記錄頁本身只有論文 PDF，未直接提供音訊。— [Zenodo 3452051](https://zenodo.org/records/3452051)；[UPF MTG 公告](https://www.upf.edu/web/mtg/news/-/asset_publisher/WM181VyAQipW/content/openbmat-a-new-open-dataset-for-music-detection-with-loudness-annotations)
- **歌聲／音樂相關測試集（WASPAA 2025 使用）**：MUSDB18-HQ、MoisesDB、EARS、WHAM!、DNS Challenge 2021 — [arXiv HTML 2512.09713](https://arxiv.org/html/2512.09713)（各自授權未在此查證）
- **TEN VAD testset**：在 TEN VAD repo `testset` 目錄公開。— [GitHub](https://github.com/TEN-framework/ten-vad)
- **FLEURS-VAD-102**：FireRed 宣稱將開源，未確認已釋出。— [HF](https://huggingface.co/FireRedTeam/FireRedVAD)
- **LiveKit eot-bench-data**：14 語言（含日、中、英）、Apache-2.0。— [GitHub](https://github.com/livekit/eot-bench)
- **TEN_Turn_Detection 測試集**（文字型、中英）：被 FireRedChat 使用。— [arXiv 2509.06502](https://arxiv.org/html/2509.06502)
- **Switchboard-1 Release 2（LDC97S62）**：LDC User Agreement for Non-Members，需登入查看費用（付費）。— [LDC catalog](https://catalog.ldc.upenn.edu/LDC97S62)
- **CEJC（日本語日常會話コーパス, NINJAL）**：有學術、非商業一般、商業等付費方案；商業授權費 10,000,000 日圓（未稅）、限約定用途；含音訊與影像。— [CEJC 商業授權 PDF](https://clrd.ninjal.ac.jp/cejc/CEJC_Commercial_3.pdf)；[申請表](https://chunagon.ninjal.ac.jp/offlinedataappl?target=CEJC&lang=en)；[設計論文 LREC 2022](https://aclanthology.org/2022.lrec-1.599.pdf)
- **MagicData-RAMC**（180 小時中文對話）：OpenSLR 標示 CC BY-NC-ND 4.0；IEEE DataPort 標示 CC BY 4.0，兩者衝突，保守應採非商業、禁改作。— [OpenSLR 123](https://www.openslr.org/123/)；[IEEE DataPort](https://ieee-dataport.org/documents/magicdata-ramc)；[論文 arXiv 2203.16844](https://arxiv.org/pdf/2203.16844)

### Inferences
- kikitori 本地測試的最小可行組合：AVA-Speech（電影 BGM 下語音，分類別計分）＋ MUSAN（自行混音做 SNR 掃描、含有聲樂音樂）＋ MoisesDB/MUSDB18-HQ（歌聲拒斥）＋ eot-bench-data（日中英 EoT）。評測只在本地使用，不需重新散布資料，NC 授權資料可做內部研究用但不宜進入商業產品訓練（推論，實際需法務確認）。
- 日語面對面對話的 turn-taking 資料沒有免費且授權寬鬆的選項；CEJC/CSJ 都需付費申請。

### Gaps
- Fisher（LDC2004S13 等）、CSJ、HKUST Mandarin、CALLHOME 日語/中文 的授權與費用未逐一查證（皆為 LDC/NINJAL 付費語料，屬一般知識，未驗證）。
- MUSAN 是否含「有人聲歌曲」的明確比例，OpenSLR 頁面未說明。
- 未找到免費、授權寬鬆、日語自然對話（人對人）且附 turn 標註的資料集。
- MUSDB18-HQ、MoisesDB、EARS、WHAM! 的授權未查證。

## Q5：VAD 與 endpointing 的標準指標與評測協定

### Takeaway
VAD：frame-level ROC-AUC 為最常見（AVA-Speech、Silero、FireRed、第三方預印本皆用），輔以 TPR@固定 FPR、PR 曲線、F1、誤報率／漏報率；近期研究開始補上 onset latency。Endpointing：學界（Google 等）用「中位數 / P90 endpoint latency 搭配 early cut-off 或 WER 不退步」，產業基準 eot-bench 用「固定 false-cutoff 率下的延遲」與 Pareto 前緣。未找到 DCF 用於近期 VAD 比較的例子。

### Cited Findings
- AVA-Speech 研究以分條件（clean / +noise / +music）frame-level TPR 與 AUROC 報告。— [MarbleNet arXiv 2010.13886](https://arxiv.org/pdf/2010.13886)
- AUC 對門檻選擇較不敏感，優於單一 precision/recall/F。— [arXiv 2102.07445](https://arxiv.org/pdf/2102.07445)
- TPR @ FPR 10% 作為高精確率操作點。— [Karan et al. Interspeech 2024](https://www.isca-archive.org/interspeech_2024/karan24_interspeech.pdf)
- F1@0.5 在語音比例高的資料上有誤導性（全判語音 F1 0.854）。— [arXiv 2610.14607](https://arxiv.org/abs/2610.14607)
- 不同推論協定（causal vs sliding window）結果不可混比。— [arXiv 2607.25870](https://arxiv.org/pdf/2607.25870)（僅搜尋摘要）
- AUROC 無法反映時間反應性 → onset latency 指標。— [arXiv 2609.11110](https://arxiv.org/abs/2609.11110)
- AUC_SiRR（語音 TPR vs 歌聲拒斥率）。— [arXiv 2512.09713](https://arxiv.org/html/2512.09713)
- FireRedVAD 報告 F1、AUC-ROC、False Alarm Rate、Miss Rate。— [HF](https://huggingface.co/FireRedTeam/FireRedVAD)
- Google 統一 ASR＋endpointing：中位 endpoint latency 降 120 ms（30.8%）、P90 降 170 ms（23.0%），且 WER 不退步。— [arXiv 2211.00786](https://arxiv.org/pdf/2211.00786.pdf)
- 兩段式 endpointing（2024）以 early cut-off 與 latency 的權衡曲線評測，資料含語音助理查詢、對話語音與 SLURP。— [arXiv 2401.08916（emergentmind 摘要頁）](https://www.emergentmind.com/papers/2401.08916)
- 2025 年對話式串流 ASR endpointing 論文指出既有方法多在 voice search 或 LibriSpeech 上評測，缺乏對話情境。— [arXiv 2505.17070](https://arxiv.org/html/2505.17070v1)
- eot-bench：每個 ≥100 ms 停頓做因果判斷，主指標 false-cutoff rate 與 latency（固定 5%/10% 誤切預算下的延遲、或固定 300 ms 延遲下的誤切率）；AUC/AP 僅診斷。— [GitHub](https://github.com/livekit/eot-bench)
- FireRedChat：barge-in 以 T90（ms）與誤打斷率報告；EoT 以 finished/unfinished 準確率報告。— [arXiv 2509.06502](https://arxiv.org/html/2509.06502)

### Inferences
- 建議 kikitori 本地協定：VAD 以各模型原生 chunk 做串流推論，分條件（clean / noise-SNR / BGM / singing）報 AUC、TPR@FPR 5%/10%、onset/offset 延遲；EoT 以 eot-bench 的「停頓級因果判斷」協定，報 P50/P90 endpoint latency @ 固定 early cut-off 率（例如 5%）與 Pareto 曲線。
- 「early cut-off rate」與「EP50/EP90」並無單一正式定義，報告時須寫清楚定義。

### Gaps
- 未找到近期（2023+）用 DCF 評估 VAD 的多模型比較（DCF 多見於 NIST OpenSAT 等較早評測，未在本次查證）。
- 未讀 2401.08916、2505.17070 全文，early cut-off 的精確定義未確認。
