# VTuber 直播翻譯評測：有參考譯文的 ja→zh-TW／ja→en（Milestone 16）

> 暫時性實驗，不屬於正式程式碼。日期：2026-11-05。分支：`feat/vtuber-models`。
> 背景：`reports/VTuber 直播模型選型調查.md`（翻譯、評測兩節）、`research_notes/VTuber 直播模型選型調查/translation_models.md`。
> 前一輪（沒有參考譯文，只看延遲與人工印象）：`experiments/translate_benchmark/README.md`。

## 目的

`translate_benchmark` 只有 22 句自寫的 VTuber 句子，沒有參考譯文，所以品質排序（Qwen3.5 9B > 4B > HY-MT 7B > 1.8B）只是人工印象。調查報告建議兩件事：

- 用**有人工參考譯文**的官方資料計分（chrF／BLEU，加上隔離環境的 COMET），並用 paired bootstrap 判斷差異是否顯著。
- 驗證兩個提示／解碼設定：**溫度 0** 對上 Qwen 官方建議的 0.7，以及**帶前文**對上不帶前文。

本實驗要回答：VTuber 設定檔裡，ja→zh-TW 與 ja→en 在各 VRAM 等級該預設哪個模型、溫度設多少、前文開或關。

## 結論

**一句話：** Qwen3.5 改用**溫度 0**（中文兩個資料集都顯著變好，沒有任何資料集變差），並**保留前文**（ASR 片段上 COMET 顯著提高，代價是偶爾把前文翻出來）；VTuber 設定檔維持 Qwen3.5（9B／4B）當預設。HY-MT1.5 7B 在有參考譯文的資料上和 Qwen3.5 9B 打平（COMET 在 social 還略勝），但人工檢查的直播用語明顯較差，加上授權有地區限制，所以仍只當選項。HY-MT1.5 1.8B 在每個資料集都顯著落後，而且在短句和非句子的輸入上會幻覺，不適合當 VTuber 設定檔的預設。

### 建議（VTuber 設定檔）

| 可給翻譯的 VRAM | ja→zh-TW | ja→en | 溫度 | 前文 |
|---|---|---|---|---|
| ≥ 7.5 GB | `qwen3.5:9b` | `qwen3.5:9b`（與 zh 共用同一個模型，避免切換） | **0** | **開**（前兩句原文） |
| 4–7.5 GB | `qwen3.5:4b` | `qwen3.5:4b` | **0** | **開** |
| < 4 GB／沒有 GPU | `qwen3.5:4b`（部分層放 CPU）優先於 `hy-mt1.5-1.8b`；CPU 延遲**未實測**，要另外量 | 同左 | 0 | 開 |
| 選項（使用者自選，地區允許時） | `hy-mt1.5-7b`（4.6 GB，可放進 4–7.5 GB 等級） | `hy-mt1.5-7b` | 0 或 0.7（沒有差別） | 關（維持現行，見 translate_benchmark） |

依據與保留：

1. **溫度 0（Qwen）**：ja→zh 兩個資料集、兩種大小，chrF 都顯著提高（speech +0.65～+0.75，p ≤ 0.002；social +2.1～+2.3，p < 0.001），COMET 在 social 也顯著（9B +0.0094、4B +0.0117）。ja→en 的 9B 沒有差別，4B 在 CoVoST chrF +1.69（p = 0.005）。沒有任何資料集變差，所以建議預設改成 0（現行預設組是 0.7，來自 Qwen 官方對一般任務的建議）。這與調查報告引用的「溫度從 0 升到 0.8，日文方向 COMET 掉 1.3–1.6」方向一致。HY-MT 的溫度 0 與 0.7 幾乎沒差別（只有 1.8B 在 social 顯著好一點），可以一併改成 0，換取可重現的輸出。
2. **前文開（Qwen）**：在 ASR 片段（speech）上效果最大：COMET 9B +0.0146、4B +0.0320（都是 p < 0.001），chrF 也顯著。social 9B 顯著、4B 不顯著；ja→en 的 BSD 對話沒有顯著差別。代價是**偶爾把前文一起翻出來**：4B 在 social 的「#視聴メモme」這類標籤行上有 3–4/311 次輸出了前兩句的譯文，9B 溫度 0.7 在 BSD 有 1/311 次；9B 溫度 0 在三個有前文的資料集都是 0 次。所以建議前文維持開啟，並在 pipeline 加一個便宜的保險（例如輸出長度遠超過原文時改用不帶前文重翻；**本實驗沒有實作**）。
3. **9B 對 4B**：social 的 COMET +0.0175、chrF +3.4（都 p < 0.001），speech 的 chrF +0.97（p < 0.001）但 COMET 只差 +0.0037（不顯著），英文都不顯著。VRAM 夠就用 9B。
4. **HY-MT1.5 7B 對 Qwen3.5 9B（溫度 0、前文）**：speech COMET 打平（+0.0005，p = 0.85），chrF 與 BLEU 反而顯著較低（−0.67、−4.0）；social COMET 顯著較高（+0.0096，p = 0.007）但 BLEU 低 5.3；英文都不顯著。也就是說，在有參考譯文的資料上兩者大致同級，指標之間互有勝負。但：
   - VTuber 句的人工檢查中，HY-MT 7B 不懂直播用語（スパチャ→「斯帕查」／"Spatza"、切り抜き師→"Uncle Sticker"、同接→「連接效果」），語氣偏書面；Qwen 9B 大多能翻對（"Super Chat"、草→「笑死人了」、同接→「同時收視人數」、わかりみが深い→「深有同感」）。
   - WMT25 主辦方指出 Hunyuan 系列用 XCOMET 等指標當 RL reward，自動指標可能高估它（調查報告）；本實驗的 COMET（wmt22-comet-da）與 XCOMET 同源，HY-MT 的 COMET 優勢要打折看。
   - HY-MT 授權不適用於歐盟、英國、南韓。
5. **HY-MT1.5 1.8B**：四個資料集的 COMET 都顯著低於 Qwen3.5 4B（−0.012～−0.033）。social 的短行或非句子輸入（「NHKドラマ10」「3話」）有 20–27/311 次輸出 HTML 表格、無限重複或憑空編出的段落；BSD 的極短句（「何？」「例えば？」）會把提示詞「Translate the following segment into English…」吐出來；zh_speech、zh_social、BSD 另有 1–3 次串流異常結束（翻譯錯誤）。若一定要用（VRAM < 4 GB），需要長度上限與重複偵測。
6. **Sakura（非商用，只當參考）**：ja→zh 的 chrF／BLEU 最高、social COMET 也最高，但 speech COMET 比 Qwen／HY-MT 低（0.739）。授權禁止商用，維持不推薦。

### 和公開系統的相對位置（校準）

WMT25 speech 上，整篇一次翻譯的官方系統 COMET：Gemini-2.5-Pro 0.826、GPT-4.1 0.818、Shy-hunyuan-MT（Hunyuan-MT-7B）0.815、Qwen3-235B 0.802、TowerPlus-9B 0.771、Qwen2.5-7B 0.767、Gemma-3-12B 0.720。本機設定逐片段翻成 zh-TW 後最好是 0.76（HY-MT 7B、Qwen3.5 9B），落在 Qwen2.5-7B 與 Gemma-3-12B 之間。差距有一部分來自條件不同（官方系統看得到整篇、直接輸出簡中；本機只看前兩句、輸出台灣用語再轉字形），不能直接當模型強弱。

## 結果

各表依 chrF 由高到低排序；「95% CI」是 chrF 的 bootstrap 信賴區間半寬；「Δ … vs 基準」是對現行正式設定 `q9-preset-ctx` 的差值與 p 值。`wmt25:` 開頭的列是校準用的官方輸出，不參與結論。由 `results/report.md`（選單 8）產生後貼入。

### WMT25 ja→zh speech（YouTube 轉寫）（59 個計分單位）

| 設定 | chrF | 95% CI | BLEU | COMET | Δ chrF vs 基準 (p) | Δ COMET vs 基準 [95% CI] (p) |
|---|---|---|---|---|---|---|
| *`wmt25:GPT-4.1`（WMT25 官方輸出，整篇翻譯，校準用）* | 27.90 | ±1.65 | 30.33 | 0.8183 | +9.36 (<0.001) | +0.0657 [+0.0558, +0.0764] (<0.001) |
| *`wmt25:Gemini-2.5-Pro`（WMT25 官方輸出，整篇翻譯，校準用）* | 27.85 | ±1.57 | 29.20 | 0.8257 | +9.31 (<0.001) | +0.0731 [+0.0616, +0.0843] (<0.001) |
| *`wmt25:Shy`（WMT25 官方輸出，整篇翻譯，校準用）* | 24.93 | ±1.53 | 24.05 | 0.8146 | +6.39 (<0.001) | +0.0619 [+0.0500, +0.0750] (<0.001) |
| *`wmt25:Qwen3-235B`（WMT25 官方輸出，整篇翻譯，校準用）* | 24.62 | ±1.54 | 25.27 | 0.8024 | +6.08 (<0.001) | +0.0498 [+0.0390, +0.0606] (<0.001) |
| *`wmt25:TowerPlus-9B`（WMT25 官方輸出，整篇翻譯，校準用）* | 21.26 | ±1.38 | 22.02 | 0.7714 | +2.72 (<0.001) | +0.0187 [+0.0091, +0.0298] (<0.001) |
| *`wmt25:Gemma-3-12B`（WMT25 官方輸出，整篇翻譯，校準用）* | 20.64 | ±1.37 | 20.34 | 0.7201 | +2.10 (<0.001) | -0.0326 [-0.0537, -0.0139] (0.002) |
| *`wmt25:Qwen2.5-7B`（WMT25 官方輸出，整篇翻譯，校準用）* | 20.45 | ±1.49 | 20.66 | 0.7666 | +1.91 (0.002) | +0.0140 [+0.0011, +0.0257] (0.029) |
| `sakura-preset` Sakura-GalTransl 7B（非商用，僅參考） | 20.39 | ±1.15 | 20.47 | 0.7392 | +1.85 (<0.001) | -0.0135 [-0.0216, -0.0055] (0.003) |
| `q9-t0-ctx` Qwen3.5 9B｜t=0｜前文 | 19.29 | ±1.04 | 18.56 | 0.7589 | +0.75 (0.002) | +0.0063 [+0.0002, +0.0133] (0.057) |
| `hy7-t0` HY-MT1.5 7B｜t=0 | 18.62 | ±1.00 | 14.56 | 0.7595 | +0.08 (0.323) | +0.0068 [-0.0011, +0.0146] (0.096) |
| `hy7-preset` HY-MT1.5 7B｜預設 t=0.7 | 18.60 | ±1.00 | 14.61 | 0.7616 | +0.06 (0.330) | +0.0090 [+0.0017, +0.0159] (0.019) |
| `q9-preset-ctx` Qwen3.5 9B｜預設 t=0.7｜前文 | 18.54 | ±0.97 | 17.44 | 0.7527 | （基準） | （基準） |
| `q9-t0-noctx` Qwen3.5 9B｜t=0｜無前文 | 18.51 | ±0.96 | 17.62 | 0.7444 | -0.03 (0.399) | -0.0083 [-0.0165, +0.0006] (0.061) |
| `q4-t0-ctx` Qwen3.5 4B｜t=0｜前文 | 18.32 | ±1.02 | 17.21 | 0.7553 | -0.22 (0.180) | +0.0026 [-0.0055, +0.0104] (0.546) |
| `q4-preset-ctx` Qwen3.5 4B｜預設 t=0.7｜前文 | 17.67 | ±0.95 | 16.37 | 0.7515 | -0.87 (0.004) | -0.0012 [-0.0102, +0.0070] (0.788) |
| `hy18-t0` HY-MT1.5 1.8B｜t=0 | 17.53 | ±0.95 | 13.81 | 0.7226 | -1.01 (0.011) | -0.0300 [-0.0394, -0.0213] (<0.001) |
| `hy18-preset` HY-MT1.5 1.8B｜預設 t=0.7 | 17.42 | ±0.93 | 13.50 | 0.7153 | -1.12 (0.006) | -0.0373 [-0.0513, -0.0256] (<0.001) |
| `q4-t0-noctx` Qwen3.5 4B｜t=0｜無前文 | 17.27 | ±0.99 | 16.26 | 0.7232 | -1.27 (0.002) | -0.0294 [-0.0439, -0.0169] (<0.001) |

成對比較（B − A；paired bootstrap 1,000 次）：

| A | B | Δ chrF (p) | Δ BLEU (p) | Δ COMET [95% CI] (p) |
|---|---|---|---|---|
| `q9-preset-ctx` | `q9-t0-ctx` | +0.75 (0.002) | +1.12 (<0.001) | +0.0063 [+0.0002, +0.0133] (0.057) |
| `q4-preset-ctx` | `q4-t0-ctx` | +0.65 (0.002) | +0.84 (0.016) | +0.0038 [-0.0022, +0.0102] (0.190) |
| `hy7-preset` | `hy7-t0` | +0.02 (0.335) | -0.05 (0.280) | -0.0021 [-0.0058, +0.0011] (0.230) |
| `hy18-preset` | `hy18-t0` | +0.11 (0.177) | +0.31 (0.135) | +0.0073 [-0.0004, +0.0179] (0.114) |
| `q9-t0-noctx` | `q9-t0-ctx` | +0.78 (0.002) | +0.94 (0.008) | +0.0146 [+0.0077, +0.0217] (<0.001) |
| `q4-t0-noctx` | `q4-t0-ctx` | +1.05 (<0.001) | +0.95 (0.053) | +0.0320 [+0.0216, +0.0459] (<0.001) |
| `q9-t0-ctx` | `hy7-t0` | -0.67 (0.035) | -4.00 (<0.001) | +0.0005 [-0.0061, +0.0072] (0.854) |
| `q4-t0-ctx` | `q9-t0-ctx` | +0.97 (<0.001) | +1.35 (0.002) | +0.0037 [-0.0040, +0.0118] (0.334) |
| `q4-t0-ctx` | `hy7-t0` | +0.30 (0.131) | -2.65 (<0.001) | +0.0042 [-0.0042, +0.0144] (0.367) |
| `hy18-t0` | `q4-t0-ctx` | +0.79 (0.013) | +3.40 (<0.001) | +0.0327 [+0.0240, +0.0416] (<0.001) |

簽章：`nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:2.6.0`；`nrefs:1|case:mixed|eff:no|tok:zh|smooth:exp|version:2.6.0`

### WMT25 ja→zh social（Mastodon 貼文）（311 個計分單位）

| 設定 | chrF | 95% CI | BLEU | COMET | Δ chrF vs 基準 (p) | Δ COMET vs 基準 [95% CI] (p) |
|---|---|---|---|---|---|---|
| `sakura-preset` Sakura-GalTransl 7B（非商用，僅參考） | 30.07 | ±1.55 | 33.08 | 0.8478 | +6.91 (<0.001) | +0.0226 [+0.0146, +0.0299] (<0.001) |
| `q9-t0-ctx` Qwen3.5 9B｜t=0｜前文 | 25.46 | ±1.48 | 28.35 | 0.8347 | +2.31 (<0.001) | +0.0094 [+0.0038, +0.0147] (<0.001) |
| `hy7-t0` HY-MT1.5 7B｜t=0 | 25.26 | ±1.35 | 23.03 | 0.8442 | +2.11 (<0.001) | +0.0190 [+0.0125, +0.0255] (<0.001) |
| `hy7-preset` HY-MT1.5 7B｜預設 t=0.7 | 24.99 | ±1.43 | 22.84 | 0.8423 | +1.83 (<0.001) | +0.0170 [+0.0101, +0.0242] (<0.001) |
| `q9-t0-noctx` Qwen3.5 9B｜t=0｜無前文 | 24.71 | ±1.50 | 27.54 | 0.8250 | +1.56 (<0.001) | -0.0003 [-0.0073, +0.0060] (0.940) |
| `hy18-t0` HY-MT1.5 1.8B｜t=0 | 23.28 | ±1.57 | 18.90 | 0.7844 | +0.13 (0.345) | -0.0409 [-0.0561, -0.0272] (<0.001) |
| `q9-preset-ctx` Qwen3.5 9B｜預設 t=0.7｜前文 | 23.15 | ±1.33 | 25.63 | 0.8253 | （基準） | （基準） |
| `hy18-preset` HY-MT1.5 1.8B｜預設 t=0.7 | 22.24 | ±1.73 | 17.01 | 0.7756 | -0.92 (0.094) | -0.0497 [-0.0669, -0.0342] (<0.001) |
| `q4-t0-ctx` Qwen3.5 4B｜t=0｜前文 | 22.07 | ±1.40 | 24.28 | 0.8171 | -1.08 (0.032) | -0.0081 [-0.0165, -0.0003] (0.052) |
| `q4-t0-noctx` Qwen3.5 4B｜t=0｜無前文 | 22.04 | ±1.38 | 24.04 | 0.8140 | -1.11 (0.012) | -0.0112 [-0.0182, -0.0046] (0.003) |
| `q4-preset-ctx` Qwen3.5 4B｜預設 t=0.7｜前文 | 19.93 | ±1.24 | 21.40 | 0.8054 | -3.22 (<0.001) | -0.0199 [-0.0284, -0.0114] (<0.001) |

成對比較（B − A；paired bootstrap 1,000 次）：

| A | B | Δ chrF (p) | Δ BLEU (p) | Δ COMET [95% CI] (p) |
|---|---|---|---|---|
| `q9-preset-ctx` | `q9-t0-ctx` | +2.31 (<0.001) | +2.72 (<0.001) | +0.0094 [+0.0038, +0.0147] (<0.001) |
| `q4-preset-ctx` | `q4-t0-ctx` | +2.14 (<0.001) | +2.88 (<0.001) | +0.0117 [+0.0061, +0.0182] (0.002) |
| `hy7-preset` | `hy7-t0` | +0.27 (0.074) | +0.19 (0.152) | +0.0019 [-0.0004, +0.0045] (0.119) |
| `hy18-preset` | `hy18-t0` | +1.04 (0.005) | +1.89 (0.014) | +0.0088 [+0.0009, +0.0176] (0.044) |
| `q9-t0-noctx` | `q9-t0-ctx` | +0.75 (0.038) | +0.81 (0.051) | +0.0097 [+0.0034, +0.0158] (0.002) |
| `q4-t0-noctx` | `q4-t0-ctx` | +0.03 (0.390) | +0.25 (0.233) | +0.0031 [-0.0038, +0.0105] (0.378) |
| `q9-t0-ctx` | `hy7-t0` | -0.20 (0.279) | -5.32 (<0.001) | +0.0096 [+0.0027, +0.0165] (0.007) |
| `q4-t0-ctx` | `q9-t0-ctx` | +3.39 (<0.001) | +4.06 (<0.001) | +0.0175 [+0.0102, +0.0252] (<0.001) |
| `q4-t0-ctx` | `hy7-t0` | +3.19 (<0.001) | -1.26 (0.081) | +0.0271 [+0.0193, +0.0350] (<0.001) |
| `hy18-t0` | `q4-t0-ctx` | -1.20 (0.037) | +5.38 (<0.001) | +0.0328 [+0.0188, +0.0478] (<0.001) |

簽章：`nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:2.6.0`；`nrefs:1|case:mixed|eff:no|tok:zh|smooth:exp|version:2.6.0`

### CoVoST 2 ja→en test（400 個計分單位）

| 設定 | chrF | 95% CI | BLEU | COMET | Δ chrF vs 基準 (p) | Δ COMET vs 基準 [95% CI] (p) |
|---|---|---|---|---|---|---|
| `hy7-t0` HY-MT1.5 7B｜t=0 | 54.18 | ±2.07 | 30.28 | 0.8858 | +0.51 (0.193) | +0.0020 [-0.0046, +0.0085] (0.512) |
| `hy7-preset` HY-MT1.5 7B｜預設 t=0.7 | 54.01 | ±2.03 | 30.32 | 0.8854 | +0.34 (0.249) | +0.0016 [-0.0048, +0.0079] (0.618) |
| `q9-preset-ctx` Qwen3.5 9B｜預設 t=0.7｜前文 | 53.67 | ±2.31 | 31.74 | 0.8838 | （基準） | （基準） |
| `q9-t0-ctx` Qwen3.5 9B｜t=0｜前文 | 53.60 | ±2.28 | 31.14 | 0.8844 | -0.06 (0.361) | +0.0006 [-0.0034, +0.0044] (0.725) |
| `q4-t0-ctx` Qwen3.5 4B｜t=0｜前文 | 52.75 | ±2.21 | 31.34 | 0.8831 | -0.91 (0.116) | -0.0007 [-0.0056, +0.0044] (0.786) |
| `hy18-t0` HY-MT1.5 1.8B｜t=0 | 51.38 | ±2.27 | 26.42 | 0.8709 | -2.29 (0.004) | -0.0129 [-0.0202, -0.0056] (0.002) |
| `hy18-preset` HY-MT1.5 1.8B｜預設 t=0.7 | 51.36 | ±2.10 | 26.39 | 0.8715 | -2.31 (0.003) | -0.0123 [-0.0187, -0.0058] (0.002) |
| `q4-preset-ctx` Qwen3.5 4B｜預設 t=0.7｜前文 | 51.06 | ±2.16 | 29.05 | 0.8785 | -2.60 (<0.001) | -0.0053 [-0.0113, +0.0005] (0.069) |

成對比較（B − A；paired bootstrap 1,000 次）：

| A | B | Δ chrF (p) | Δ BLEU (p) | Δ COMET [95% CI] (p) |
|---|---|---|---|---|
| `q9-preset-ctx` | `q9-t0-ctx` | -0.06 (0.361) | -0.60 (0.162) | +0.0006 [-0.0034, +0.0044] (0.725) |
| `q4-preset-ctx` | `q4-t0-ctx` | +1.69 (0.005) | +2.29 (0.004) | +0.0046 [-0.0000, +0.0093] (0.056) |
| `hy7-preset` | `hy7-t0` | +0.17 (0.180) | -0.04 (0.364) | +0.0004 [-0.0009, +0.0016] (0.536) |
| `hy18-preset` | `hy18-t0` | +0.02 (0.396) | +0.04 (0.372) | -0.0006 [-0.0046, +0.0031] (0.774) |
| `q9-t0-ctx` | `hy7-t0` | +0.57 (0.183) | -0.85 (0.188) | +0.0014 [-0.0054, +0.0075] (0.660) |
| `q4-t0-ctx` | `q9-t0-ctx` | +0.85 (0.113) | -0.21 (0.326) | +0.0013 [-0.0040, +0.0065] (0.621) |
| `q4-t0-ctx` | `hy7-t0` | +1.42 (0.054) | -1.06 (0.163) | +0.0027 [-0.0033, +0.0092] (0.403) |
| `hy18-t0` | `q4-t0-ctx` | +1.38 (0.045) | +4.92 (<0.001) | +0.0122 [+0.0052, +0.0188] (0.002) |

簽章：`nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:2.6.0`；`nrefs:1|case:mixed|eff:no|tok:13a|smooth:exp|version:2.6.0`

### BSD ja→en test（日文原創對話）（311 個計分單位）

| 設定 | chrF | 95% CI | BLEU | COMET | Δ chrF vs 基準 (p) | Δ COMET vs 基準 [95% CI] (p) |
|---|---|---|---|---|---|---|
| `q9-t0-noctx` Qwen3.5 9B｜t=0｜無前文 | 42.14 | ±2.07 | 17.64 | 0.8214 | +1.00 (0.064) | +0.0017 [-0.0043, +0.0077] (0.560) |
| `hy7-t0` HY-MT1.5 7B｜t=0 | 41.87 | ±1.82 | 15.70 | 0.8180 | +0.72 (0.111) | -0.0016 [-0.0083, +0.0049] (0.627) |
| `hy7-preset` HY-MT1.5 7B｜預設 t=0.7 | 41.78 | ±1.69 | 15.23 | 0.8179 | +0.63 (0.137) | -0.0018 [-0.0085, +0.0048] (0.604) |
| `q9-t0-ctx` Qwen3.5 9B｜t=0｜前文 | 41.59 | ±2.08 | 16.89 | 0.8231 | +0.45 (0.141) | +0.0035 [-0.0011, +0.0092] (0.190) |
| `q9-preset-ctx` Qwen3.5 9B｜預設 t=0.7｜前文 | 41.14 | ±2.04 | 16.42 | 0.8196 | （基準） | （基準） |
| `q4-t0-ctx` Qwen3.5 4B｜t=0｜前文 | 40.87 | ±1.99 | 16.60 | 0.8164 | -0.28 (0.233) | -0.0032 [-0.0110, +0.0045] (0.383) |
| `q4-t0-noctx` Qwen3.5 4B｜t=0｜無前文 | 40.69 | ±1.93 | 16.39 | 0.8126 | -0.46 (0.181) | -0.0070 [-0.0146, -0.0003] (0.056) |
| `q4-preset-ctx` Qwen3.5 4B｜預設 t=0.7｜前文 | 40.41 | ±2.05 | 16.34 | 0.8121 | -0.74 (0.109) | -0.0075 [-0.0165, +0.0002] (0.075) |
| `hy18-preset` HY-MT1.5 1.8B｜預設 t=0.7 | 39.31 | ±1.85 | 13.15 | 0.8045 | -1.83 (0.009) | -0.0152 [-0.0249, -0.0058] (0.005) |
| `hy18-t0` HY-MT1.5 1.8B｜t=0 | 39.11 | ±1.88 | 12.47 | 0.8033 | -2.04 (0.003) | -0.0163 [-0.0259, -0.0073] (0.003) |

成對比較（B − A；paired bootstrap 1,000 次）：

| A | B | Δ chrF (p) | Δ BLEU (p) | Δ COMET [95% CI] (p) |
|---|---|---|---|---|
| `q9-preset-ctx` | `q9-t0-ctx` | +0.45 (0.141) | +0.46 (0.193) | +0.0035 [-0.0011, +0.0092] (0.190) |
| `q4-preset-ctx` | `q4-t0-ctx` | +0.46 (0.149) | +0.26 (0.254) | +0.0043 [-0.0019, +0.0110] (0.187) |
| `hy7-preset` | `hy7-t0` | +0.09 (0.243) | +0.47 (0.053) | +0.0001 [-0.0020, +0.0029] (0.900) |
| `hy18-preset` | `hy18-t0` | -0.20 (0.218) | -0.68 (0.061) | -0.0011 [-0.0073, +0.0056] (0.722) |
| `q9-t0-noctx` | `q9-t0-ctx` | -0.55 (0.139) | -0.75 (0.119) | +0.0017 [-0.0032, +0.0067] (0.464) |
| `q4-t0-noctx` | `q4-t0-ctx` | +0.18 (0.253) | +0.21 (0.279) | +0.0038 [-0.0018, +0.0098] (0.200) |
| `q9-t0-ctx` | `hy7-t0` | +0.27 (0.250) | -1.19 (0.113) | -0.0051 [-0.0114, +0.0011] (0.113) |
| `q4-t0-ctx` | `q9-t0-ctx` | +0.73 (0.104) | +0.29 (0.256) | +0.0067 [-0.0007, +0.0140] (0.075) |
| `q4-t0-ctx` | `hy7-t0` | +1.00 (0.087) | -0.90 (0.175) | +0.0016 [-0.0057, +0.0083] (0.661) |
| `hy18-t0` | `q4-t0-ctx` | +1.76 (0.010) | +4.13 (0.002) | +0.0131 [+0.0037, +0.0235] (0.016) |

簽章：`nrefs:1|case:mixed|eff:yes|nc:6|nw:0|space:no|version:2.6.0`；`nrefs:1|case:mixed|eff:no|tok:13a|smooth:exp|version:2.6.0`

### 可疑輸出計數（逐句）

| 資料集 | 設定 | 句數 | 翻譯錯誤 | 空輸出 | 錯誤語言 | 含假名 | 簡體殘留 | 附加文字（多行／說明／過長） | 疑似翻出前文 |
|---|---|---|---|---|---|---|---|---|---|
| zh_speech | `q9-preset-ctx` | 726 | 0 | 0 | 2 | 6 | 0 | 0（0／0／0） | 0 |
| zh_speech | `q9-t0-ctx` | 726 | 0 | 0 | 2 | 4 | 0 | 0（0／0／0） | 0 |
| zh_speech | `q9-t0-noctx` | 726 | 0 | 0 | 0 | 4 | 0 | 0（0／0／0） | 0 |
| zh_speech | `q4-preset-ctx` | 726 | 0 | 0 | 3 | 6 | 0 | 0（0／0／0） | 0 |
| zh_speech | `q4-t0-ctx` | 726 | 0 | 0 | 4 | 6 | 0 | 0（0／0／0） | 0 |
| zh_speech | `q4-t0-noctx` | 726 | 0 | 0 | 6 | 10 | 0 | 1（0／0／1） | 0 |
| zh_speech | `hy7-preset` | 726 | 0 | 0 | 0 | 3 | 0 | 0（0／0／0） | 0 |
| zh_speech | `hy7-t0` | 726 | 0 | 0 | 0 | 2 | 0 | 0（0／0／0） | 0 |
| zh_speech | `hy18-preset` | 726 | 2 | 2 | 1 | 11 | 0 | 3（1／0／2） | 0 |
| zh_speech | `hy18-t0` | 726 | 1 | 1 | 1 | 9 | 0 | 0（0／0／0） | 0 |
| zh_speech | `sakura-preset` | 726 | 0 | 0 | 0 | 2 | 0 | 0（0／0／0） | 0 |
| zh_social | `q9-preset-ctx` | 311 | 0 | 0 | 3 | 12 | 0 | 0（0／0／0） | 0 |
| zh_social | `q9-t0-ctx` | 311 | 0 | 0 | 4 | 12 | 0 | 0（0／0／0） | 0 |
| zh_social | `q9-t0-noctx` | 311 | 0 | 0 | 2 | 7 | 0 | 0（0／0／0） | 0 |
| zh_social | `q4-preset-ctx` | 311 | 0 | 0 | 1 | 10 | 0 | 4（0／0／4） | 4 |
| zh_social | `q4-t0-ctx` | 311 | 0 | 0 | 1 | 6 | 0 | 3（0／0／3） | 3 |
| zh_social | `q4-t0-noctx` | 311 | 0 | 0 | 2 | 10 | 0 | 0（0／0／0） | 0 |
| zh_social | `hy7-preset` | 311 | 0 | 0 | 0 | 11 | 0 | 0（0／0／0） | 0 |
| zh_social | `hy7-t0` | 311 | 0 | 0 | 0 | 11 | 0 | 0（0／0／0） | 0 |
| zh_social | `hy18-preset` | 311 | 1 | 1 | 1 | 9 | 0 | 27（16／2／22） | 0 |
| zh_social | `hy18-t0` | 311 | 1 | 1 | 1 | 12 | 0 | 21（6／3／16） | 0 |
| zh_social | `sakura-preset` | 311 | 0 | 0 | 0 | 4 | 0 | 0（0／0／0） | 0 |
| en_covost | `q9-preset-ctx` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `q9-t0-ctx` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `q4-preset-ctx` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `q4-t0-ctx` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `hy7-preset` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `hy7-t0` | 400 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_covost | `hy18-preset` | 400 | 0 | 0 | 0 | 0 | — | 1（0／1／1） | 0 |
| en_covost | `hy18-t0` | 400 | 0 | 0 | 0 | 0 | — | 1（0／1／1） | 0 |
| en_bsd | `q9-preset-ctx` | 311 | 0 | 0 | 0 | 0 | — | 1（0／0／1） | 1 |
| en_bsd | `q9-t0-ctx` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `q9-t0-noctx` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `q4-preset-ctx` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `q4-t0-ctx` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `q4-t0-noctx` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `hy7-preset` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `hy7-t0` | 311 | 0 | 0 | 0 | 0 | — | 0（0／0／0） | 0 |
| en_bsd | `hy18-preset` | 311 | 3 | 3 | 0 | 0 | — | 2（0／2／2） | 0 |
| en_bsd | `hy18-t0` | 311 | 1 | 1 | 0 | 0 | — | 3（1／3／2） | 0 |

### 延遲（只量延遲模式；一次一個請求，GPU 仍與 ASR 實驗共用）

| 設定 | 日期 | 句數 | 首字 P50 | 首字 P90 | 總延遲 P50 | 總延遲 P90 | 量測前 GPU |
|---|---|---|---|---|---|---|---|
| `hy18-preset` | 2026-11-06 00:49 | 240/240 | 0.03 s | 0.06 s | 0.11 s | 0.21 s | NVIDIA GeForce RTX 5070, 4778 MiB, 12227 MiB, 51 % |
| `hy18-t0` | 2026-11-06 00:50 | 240/240 | 0.03 s | 0.05 s | 0.10 s | 0.19 s | NVIDIA GeForce RTX 5070, 7071 MiB, 12227 MiB, 75 % |
| `hy7-preset` | 2026-11-06 00:52 | 240/240 | 0.07 s | 0.09 s | 0.42 s | 0.82 s | NVIDIA GeForce RTX 5070, 7079 MiB, 12227 MiB, 62 % |
| `hy7-t0` | 2026-11-06 00:54 | 240/240 | 0.06 s | 0.08 s | 0.38 s | 0.82 s | NVIDIA GeForce RTX 5070, 11546 MiB, 12227 MiB, 95 % |
| `q4-preset-ctx` | 2026-11-06 00:48 | 240/240 | 0.08 s | 0.10 s | 0.17 s | 0.25 s | NVIDIA GeForce RTX 5070, 11282 MiB, 12227 MiB, 3 % |
| `q4-t0-ctx` | 2026-11-06 00:48 | 240/240 | 0.07 s | 0.10 s | 0.16 s | 0.25 s | NVIDIA GeForce RTX 5070, 9070 MiB, 12227 MiB, 76 % |
| `q4-t0-noctx` | 2026-11-06 00:49 | 180/180 | 0.07 s | 0.10 s | 0.16 s | 0.26 s | NVIDIA GeForce RTX 5070, 9076 MiB, 12227 MiB, 48 % |
| `q9-preset-ctx` | 2026-11-06 00:55 | 240/240 | 0.09 s | 0.11 s | 0.21 s | 0.33 s | NVIDIA GeForce RTX 5070, 9797 MiB, 12227 MiB, 69 % |
| `q9-t0-ctx` | 2026-11-06 00:56 | 240/240 | 0.09 s | 0.11 s | 0.21 s | 0.33 s | NVIDIA GeForce RTX 5070, 7309 MiB, 12227 MiB, 78 % |
| `q9-t0-noctx` | 2026-11-06 00:57 | 180/180 | 0.08 s | 0.10 s | 0.21 s | 0.34 s | NVIDIA GeForce RTX 5070, 7309 MiB, 12227 MiB, 72 % |
| `sakura-preset` | 2026-11-06 00:54 | 120/120 | 0.03 s | 0.04 s | 0.13 s | 0.22 s | NVIDIA GeForce RTX 5070, 7785 MiB, 12227 MiB, 95 % |

### 品質跑附帶的延遲（GPU 與其他工作共用，僅供參考）

| 設定 | 句數 | 首字 P50 | 首字 P90 | 總延遲 P50 | 總延遲 P90 |
|---|---|---|---|---|---|
| `q9-preset-ctx` | 1748/1748 | 0.59 s | 1.17 s | 0.79 s | 1.57 s |
| `q9-t0-ctx` | 1748/1748 | 0.63 s | 1.17 s | 0.78 s | 1.56 s |
| `q9-t0-noctx` | 1348/1348 | 0.74 s | 1.29 s | 0.95 s | 1.68 s |
| `q4-preset-ctx` | 1748/1748 | 0.10 s | 0.19 s | 0.25 s | 0.42 s |
| `q4-t0-ctx` | 1748/1748 | 0.35 s | 0.63 s | 0.50 s | 0.96 s |
| `q4-t0-noctx` | 1348/1348 | 0.40 s | 0.71 s | 0.58 s | 1.08 s |
| `hy7-preset` | 1748/1748 | 0.30 s | 0.64 s | 0.52 s | 1.19 s |
| `hy7-t0` | 1748/1748 | 0.29 s | 0.63 s | 0.52 s | 1.19 s |
| `hy18-preset` | 1742/1748 | 0.15 s | 0.34 s | 0.27 s | 0.64 s |
| `hy18-t0` | 1745/1748 | 0.16 s | 0.35 s | 0.26 s | 0.62 s |
| `sakura-preset` | 1037/1037 | 0.05 s | 0.10 s | 0.23 s | 0.54 s |

### VTuber 風格句（沒有參考譯文，人工檢查）

`translate_benchmark/lines.json` 的 22 句＋本目錄 `vtuber_extra.json` 的 8 句（`vx` 開頭，てぇてぇ、初見、同接、切り抜き等），同一場景依序翻譯、前文規則同上。

觀察：

- **Qwen3.5 9B（溫度 0）最懂直播用語**：草→「笑死人了哈哈哈」、同接→「同時收視人數」、わかりみが深い→「深有同感」、スパチャ→"Super Chat"；仍會錯的：赤スパ（紅色辣椒粉／"red spicy"）、推し（譯成「我喜歡的人」／"crush"，意思接近但不是「推」）、メン限（en 誤成 "men's lineup"）。溫度 0.7 的 9B 在 スパチャ（「超感謝的超級讚」）、草（「草都長出來了」）上較差。
- **Qwen3.5 4B**：日常句與戰鬥台詞正確，直播用語多半翻錯（同接→「同樂會」、てぇてぇ→「太可愛了」可接受、わかりみ→「你這人真夠深沉的」、べ、別に…→「別的不為了你」）。
- **HY-MT1.5 7B**：句子完整但偏書面直譯；直播用語幾乎全錯（スパチャ→「斯帕查」、赤スパ→「紅色噴漆」、同接→「連接效果」、切り抜き師→"Uncle Sticker"）；出現大陸用語「視頻」，整句簡轉繁時把「吃」轉成「喫」（「好喫」）。
- **Sakura（非商用參考）**：動漫語氣最貼切（推→「我的推太尊了」、てぇてぇ→「太貼貼了」），但「動画」譯成「動畫」、赤スパ→「紅色修改」。

#### zh-TW

| 原文 | `q9-preset-ctx` | `q9-t0-ctx` | `q4-t0-ctx` | `hy7-t0` | `sakura-preset` |
|---|---|---|---|---|---|
| おはようございます！今日も配信始めていきます！ | 大家早！今天也開始直播囉！ | 大家早！今天也開始直播囉！ | 早安！今天也開始直播了！ | 早上好！今天也會開始進行直播哦！ | 大家早！今天也要開始直播啦！ |
| 今日はみんなと一緒にホラーゲームやっていくよ。 | 今天會和大家一起玩恐怖遊戲喔。 | 今天要和大家一起玩恐怖遊戲喔。 | 今天我們一起來玩恐怖遊戲吧。 | 今天我要和大家一起玩恐怖遊戲。 | 今天要和大家一起玩恐怖遊戲哦。 |
| 怖くないもん…全然怖くないもん！ | 沒關係啦…一點都不怕的！ | 我才不怕呢……一點都不怕啦！ | 根本不可怕……完全一點都不怕！ | 並不可怕……真的完全不可怕！ | 才不害怕呢……我一點都不怕！ |
| えっ、マジで？それはヤバくない？ | 欸、真的嗎？這樣不會很危險嗎？ | 欸、真的嗎？那不是很危險嗎？ | 哇，真的假的？那不會很可怕吧？ | 呃，真的嗎？那不是很危險嗎？ | 誒，真的假的？那不是很糟糕嗎？ |
| 待って待って、今のなし！もう一回やらせて！ | 等等等等，剛才那個不算！再給我來一次！ | 等等等等，剛才那個不算！再讓我來一次！ | 等等等等，現在還沒結束！再讓我來一次！ | 等一下，現在的這種情況不行！再讓我試一次吧！ | 等等等等，剛才的不算！再讓我來一次！ |
| スパチャありがとう！めっちゃ嬉しい！ | 超感謝的超級讚！ | 收到超讚的贊助！超級開心！ | 感謝贊助！真的超開心！ | 斯帕查，真的非常感謝你！我太開心了！ | 謝謝大家的超留！我超開心的！ |
| 草生えるｗｗｗ | 草都長出來了ｗｗｗ | 笑死人了哈哈哈 | 長草了。 | 長出了草……wwww | 長草了www |
| 推しが尊すぎて無理… | 我喜歡的人太完美了，根本受不了…… | 我喜歡的人太帥了，完全受不了…… | 粉絲太崇拜我了，我快撐不住了…… | 我太崇拜他了，因此根本無法做到…… | 我的推太尊了，我受不了了…… |
| じゃあ、そろそろ締めていこうかな。 | 那麼，差不多該收個尾了吧。 | 那差不多該結束了吧。 | 那我們就差不多結束吧。 | 那麼，差不多該結束了吧。 | 那麼，差不多該收尾了吧。 |
| チャンネル登録と高評価、よろしくお願いします！ | 頻道訂閱與按讚，請多多指教！ | 請訂閱頻道並給予按讚，謝謝！ | 麻煩訂閱頻道並給予高評價！ | 請務必註冊該頻道並給予高度評價，非常感謝！ | 請各位訂閱頻道，以及點贊！ |
| 明日の配信は夜九時からです。お楽しみに！ | 明天的直播將在晚上九點開始，請拭目以待！ | 明天的直播將於晚上九點開始，請期待！ | 明天的直播從晚上九點開始，請期待喔！ | 明天的直播將在晚上九點開始，請大家拭目以待吧！ | 明天的直播是從晚上九點開始，敬請期待！ |
| お前、まだそんなこと言ってるのか。 | 你居然還說那種話。 | 你還在那說這種話嗎？ | 你還說這種話嗎？ | 你現在還在說這種話嗎？ | 你啊，怎麼還在說這種話啊。 |
| 俺は絶対に諦めない。仲間を守るためなら何でもする。 | 我絕對不會放棄，為了守護同伴什麼都願意做。 | 我絕不會放棄，為了守護同伴，什麼事都做得出來。 | 我絕對不會放棄，為了守護同伴，我會不惜一切代價。 | 我絕對不會放棄。為了保護我的同伴，我什麼都願意做。 | 我絕對不會放棄。只要能保護夥伴，我什麼都願意做。 |
| まさか、あいつが裏切り者だったなんて… | 誰也沒想到，他竟然是叛徒…… | 誰也沒想到，那傢伙竟然是叛徒…… | 沒想到他竟然是個叛徒…… | 難以相信，他居然是個叛徒…… | 沒想到，那傢伙居然是叛徒…… |
| 行くぞ、みんな！この戦い、俺たちの勝ちだ！ | 走囉大家！這場戰鬥，我們贏定了！ | 走囉，大家！這場戰鬥，我們贏了！ | 上吧，各位！這場戰鬥，我們必勝！ | 我們要出發了，大家！這場戰爭，我們一定會勝利！ | 大家，要上咯！這場戰鬥，是我們贏了！ |
| 先輩、ちょっといいですか？ | 學長/學姐，可以問個問題嗎？ | 前輩，可以問個問題嗎？ | 前輩，請問一下，可以嗎？ | 前輩，可以請您稍等一下嗎？ | 學姐，能打擾一下嗎？ |
| なんでそんな顔してるの？何かあった？ | 為什麼那副表情？發生什麼事了嗎？ | 為什麼擺出那種表情？發生什麼事了嗎？ | 為什麼露出那種表情？發生什麼事了嗎？ | 為什麼你會那個表情？發生什麼事了嗎？ | 為什麼露出那樣的表情？發生什麼事了？ |
| 心配しないで。私がついてるから。 | 別擔心，我會陪著你的。 | 別擔心，我會陪著你的。 | 別擔心，我會陪著你的。 | 別擔心，有我在呢。 | 別擔心，有我在呢。 |
| べ、別にあんたのためにやったわけじゃないんだからね！ | 不、不是為了你才做的啦！ | 不、不是為了你才做的啦！ | 別的不為了你才這麼做的啦！ | 喂，我可不是為了你才這樣做的啊！ | 我，我可不是為了你才這麼做的哦！ |
| ごめん、遅れちゃった。電車が止まってて。 | 抱歉，來晚了，電車延誤了。 | 對不起，來晚了，電車停了。 | 對不起，我遲到了，因為電車停了。 | 對不起，我來晚了。火車停住了，所以遲到了。 | 抱歉，我遲到了。電車停運了。 |
| いただきます！うわ、このラーメンうまっ！ | 失禮了！哇，這碗拉麵超好吃！ | 開動了！哇，這碗拉麵好好吃！ | 開動了！哇，這碗拉麵真好吃！ | 太好了！哇，這個拉麵真的好喫啊！ | 我開動了！哇，這碗拉麵真好吃！ |
| この動画のソフトの情報、後でネットで調べてみるね。 | 這段影片的軟體資訊，我稍後會在網路上查詢看看。 | 這段影片的軟體資訊，我稍後會在網路上查一下。 | 這支影片的軟體資訊，我稍後會上網查一下。 | 我會之後在網路上查一下關於這個視頻軟體的資訊。 | 等會兒我再上網查一下這部動畫的軟體資訊。 |
| 初見さんいらっしゃい！ゆっくりしていってね。 | 初次見面！請慢慢休息。 | 初次見面！請慢慢休息。 | 初次見面，歡迎光臨！請慢慢來。 | 初次見面，歡迎您！請慢慢來吧。 | 初次見面的各位，歡迎光臨！請慢慢參觀哦。 |
| 今日の同接すごくない？みんなありがとう！ | 今天的連播人數真驚人嗎？謝謝大家！ | 今天的同時收視人數真驚人，謝謝大家！ | 今天的同樂會很棒吧？謝謝大家！ | 今天的連接效果真的非常好吧？感謝大家！ | 今天的觀看人數是不是有點多啊？謝謝大家！ |
| さっきのコラボ、二人ともてぇてぇすぎる… | 剛才的合作太棒了，兩個人簡直是萌翻了… | 剛才的合體太可愛了，兩位都萌翻了…… | 剛才的搭檔，兩人實在太可愛了…… | 剛才的那次合作，他們兩個真的太強大了…… | 剛才的聯動，兩個人都太貼貼了…… |
| 赤スパありがとうございます！え、こんなにいいの？ | 紅色辣椒粉謝謝您！欸，這麼棒嗎？ | 感謝大家送來的紅色火花！咦，這麼棒嗎？ | 紅色斯巴感謝大家！咦，這麼好嗎？ | 非常感謝您的紅色噴漆！咦，真的有這麼好嗎？ | 謝謝您的紅色修改！咦，這樣可以嗎？ |
| 切り抜き師さん、今のところ切り抜いといて！ | 剪輯師啊，先幫我剪出來啦！ | 剪輯師先生，先幫我剪出來吧！ | 剪貼師先生，先幫我們剪下來吧！ | 剪貼師先生，暫時就先剪下來吧！ | 剪輯師，現在就先剪輯一下吧！ |
| メン限で歌枠やるから、メンバーの人は来てね。 | 因為要在限定成員的時段唱歌，所以成員們一定要來喔。 | 因為是限定成員的歌唱環節，所以希望各位成員都能來喔。 | 因為要限定人數唱歌，所以成員們都來吧。 | 因為只有固定成員才能參加歌唱環節，所以成員們一定要來哦。 | 我在成員極限開臺唱歌，成員們要來捧場哦。 |
| あ、ミュートになってた？ごめんごめん！ | 啊，剛才被靜音了？對不起對不起！ | 啊，剛才被靜音了嗎？對不起對不起！ | 啊，原來是靜音了？對不起對不起！ | 啊，原來它被設為靜音模式了？對不起對不起！ | 啊，變成靜音了？抱歉抱歉！ |
| それな！わかりみが深い。 | 說得對！深有同感。 | 沒錯！深有同感。 | 對啊！你這人真夠深沉的。 | 就是這個！理解得很深入呢。 | 對啊！我深有同感。 |

#### en

| 原文 | `q9-preset-ctx` | `q9-t0-ctx` | `q4-t0-ctx` | `hy7-t0` |
|---|---|---|---|---|
| おはようございます！今日も配信始めていきます！ | Good morning! The stream is starting again today! | Good morning! Let's get the stream started today! | Good morning! Let's start the broadcast today! | Good morning! We’re starting our live broadcast again today. |
| 今日はみんなと一緒にホラーゲームやっていくよ。 | Today, we'll be playing a horror game together, everyone! | Today, we're going to play a horror game together! | Today, we're going to play a horror game together. | Today, I’m going to play a horror game with everyone. |
| 怖くないもん…全然怖くないもん！ | There's nothing to be afraid of... I'm not scared at all! | I'm not scared... at all! | I'm not scared... I'm not scared at all! | It’s not scary at all… not scary at all! |
| えっ、マジで？それはヤバくない？ | What? Seriously? That's messed up, right? | What? For real? That's pretty intense, isn't it? | Wait, seriously? Isn't that dangerous? | Huh, really? Isn’t that dangerous? |
| 待って待って、今のなし！もう一回やらせて！ | Wait wait, don't delete that! Let me do it again! | Wait, wait! That doesn't count! Let me try again! | Wait wait, I don't want to stop yet! Do it again! | Wait, wait, not like this! Let me try again! |
| スパチャありがとう！めっちゃ嬉しい！ | Thanks for the Super Chat! I'm so happy about it! | Thanks for the Super Chat! I'm so happy! | Thank you for the subs! I'm so happy! | Thank you so much, Spatza! I’m really thrilled! |
| 草生えるｗｗｗ | Lol grass is growing wwww | Lmao lol | Grass is growing lol | Grass is growing... wwww |
| 推しが尊すぎて無理… | My crush is too awesome... I can't handle it... | My crush is too perfect... I can't handle it! | My idol is too respected... it's unbearable... | My bias is too strong; I can’t do it… |
| じゃあ、そろそろ締めていこうかな。 | Well, let's wrap this up soon. | Well then, let's wrap this up soon. | So, let's wrap this up soon. | Well, I guess it’s time to wrap things up. |
| チャンネル登録と高評価、よろしくお願いします！ | Please subscribe and like! | Please subscribe and like! | Please subscribe and give this video a thumbs up! | Please subscribe to the channel and give it a good rating! |
| 明日の配信は夜九時からです。お楽しみに！ | Tomorrow's stream starts at 9 PM. Stay tuned! | Tomorrow's stream starts at 9 PM. Stay tuned! | Tomorrow's stream starts at 9 PM. See you then! | The live broadcast tomorrow will start at 9 p.m. Please stay tuned! |
| お前、まだそんなこと言ってるのか。 | You're still saying that? | Are you still saying that? | You still say that? | You’re still saying such things? |
| 俺は絶対に諦めない。仲間を守るためなら何でもする。 | I'll never give up. I'll do whatever it takes to protect my comrades. | I'll never give up. I'll do whatever it takes to protect my comrades. | I will never give up. I'll do anything to protect my comrades. | I will never give up. I will do anything to protect my friends. |
| まさか、あいつが裏切り者だったなんて… | I can't believe it—that guy was the traitor... | No way... that guy was a traitor? | No way... he was a traitor... | I never would have thought that he could be a traitor... |
| 行くぞ、みんな！この戦い、俺たちの勝ちだ！ | Let's go, everyone! We've won this battle! | Let's go, everyone! We've won this battle! | Let's go, everyone! We're going to win this fight! | Let’s go, everyone! We are going to win this battle! |
| 先輩、ちょっといいですか？ | Senior, can I ask you something? | Senior, can I ask you something? | Excuse me, is this okay? | Senior, could you please wait a moment? |
| なんでそんな顔してるの？何かあった？ | Why the long face? Something wrong? | Why the long face? Something wrong? | Why the look? Something wrong? | Why are you making that face? Did something happen? |
| 心配しないで。私がついてるから。 | Don't worry, I'm right here with you. | Don't worry. I'm right here with you. | Don't worry. I'm right here with you. | Don’t worry. I’m here with you. |
| べ、別にあんたのためにやったわけじゃないんだからね！ | I-I didn't do it just for you! | I-I didn't do it for you or anything! | I-I mean, I didn't do it just for you! | I… I didn’t do it for you, anyway! |
| ごめん、遅れちゃった。電車が止まってて。 | Sorry, I'm late. The train stopped. | Sorry, I'm late. The train was stopped. | Sorry, I'm late. The train stopped. | Sorry for the delay; the train was stopped. |
| いただきます！うわ、このラーメンうまっ！ | Enjoy! Wow, this ramen is delicious! | Bon appétit! Wow, this ramen is delicious! | Let's eat! Wow, this ramen is delicious! | Thank you! Wow, this ramen is so delicious! |
| この動画のソフトの情報、後でネットで調べてみるね。 | I'll check the software info for this video online later. | I'll look up the software info for this video online later. | I'll look up the software info for this video online later. | I’ll look up more information about the software used in this video later on the internet. |
| 初見さんいらっしゃい！ゆっくりしていってね。 | Welcome to the lobby! Please take your time and relax. | Welcome! Take your time and relax. | Nice to meet you! Take your time. | Hello, newcomers! Feel free to take your time. |
| 今日の同接すごくない？みんなありがとう！ | Isn't today's stream amazing? Thanks, everyone! | Isn't today's stream amazing? Thank you all! | Today's meeting was great! Thanks everyone! | Today’s call was really great, wasn’t it? Thank you all! |
| さっきのコラボ、二人ともてぇてぇすぎる… | That collab earlier was way too cute for both of you... | That collab earlier was too much for both of you... | That last collab was way too cute for both of us... | The collaboration just now… both of those people are absolutely incredible… |
| 赤スパありがとうございます！え、こんなにいいの？ | Thanks for the spicy red sauce! Wow, it's this good? | Thanks for the red spicy! Wow, it's this good? | Thanks for the red spade! Wow, is this really that good? | Thank you so much for the red spa! Um, is it really that good? |
| 切り抜き師さん、今のところ切り抜いといて！ | Editor-in-Chief, cut it out for now! | Editor, cut it out for now! | Cut-out artist, cut it out for now! | Uncle Sticker, for now, just stick it on! |
| メン限で歌枠やるから、メンバーの人は来てね。 | We'll be hosting a singing segment for members only, so everyone's invited to come! | We're doing a song segment for the men's lineup, so members should come! | I'm doing a song slot on the member limit, so members need to come. | We’re having a singing session just for the members, so please come if you’re one of them. |
| あ、ミュートになってた？ごめんごめん！ | Oh, you were muted? Sorry, sorry! | Oh, I was muted? Sorry, sorry! | Oh, you muted me? Sorry sorry! | Oh, it must have been muted. Sorry, sorry! |
| それな！わかりみが深い。 | Totally! I get it. | That's right! I totally get it. | Got it! You're really deep. | Indeed! It’s very insightful. |

## 本實驗順帶發現的正式程式問題（未修改 `src/`，留給後續任務）

1. **特殊 token 碎片沒清乾淨**：HY-MT 1.8B 會輸出 `<|/code>`、`<|extra_0>`、`<|begin_quote>…`、`<|tagline></tagline>` 這類沒有收尾 `|>` 的碎片，`postprocess` 的正則只認 `<|…|>` 與結尾的 `<|`，所以會留在字幕上：zh_speech 1–3/726、zh_social 6–12/311（溫度 0／0.7）。
2. **HY-MT 1.8B 的幻覺與提示詞回顯**：見上表；翻譯器目前只靠 `max_tokens` 截斷（log 出現 `translation truncated by max_tokens=256`）。
3. **選擇性 `s2twp` 的副作用**：HY-MT 7B 的 zh-TW 原始輸出有 28–42 句含簡體字（4–14%），整句轉換時會連帶把「台」改成「臺」、「傢伙」改成「傢夥」、「吃」改成「喫」（VTuber 句的「好喫」）。後處理之後所有設定的簡體殘留都是 0。
4. **溫度預設**：`presets.py` 的 Qwen 溫度 0.7 建議改成 0（見上）。

## 注意事項與限制

- **延遲受 GPU 共用污染**：品質跑期間另一個 ASR 實驗佔用同一張 GPU，而且同一個模型的設定是同時送請求，所以「品質跑附帶的延遲」不能當真。「只量延遲」那張表是事後一次一個請求量的，但 ASR 實驗仍在跑（量測前 GPU 用量 4.8–11.5 GB、使用率 50–95%），開頭幾組（`q4-*`）還和本機的 COMET 計分（CPU）重疊；要乾淨的數字，等 GPU 空閒時用選單 4／5 重跑。
- 溫度 0.7 的結果是單次取樣；溫度 0 的比較沒有這個問題。
- 中文分數是「zh-TW 輸出轉簡體」對「簡中參考譯文」：字形統一了，但台灣用語（影片、軟體…）仍會被扣分，絕對值偏低；設定之間的比較不受影響。
- chrF 的 β=2 偏重召回率，輸出過長（例如 HY-MT 1.8B 的幻覺）反而可能拿到較高的 chrF；BLEU 有長度懲罰、COMET 看語意，三者不一致時以 COMET 與錯誤計數為主。
- sacrebleu 的 p 值用它內建的 paired bootstrap 算法（以差值絕對值置中後計數，最大約 0.5）；COMET 的 p 值是自寫的雙尾版本（最大 1）。兩者都是 1,000 次、種子 12345，沒有做多重比較校正：成對比較共約 40 組，p 在 0.01–0.05 之間的結果要保守看。
- speech 只有 59 篇（以文件為單位），信賴區間較寬；social 只有 12 篇貼文串，句子之間不獨立（以句為單位重抽，區間可能偏窄）。
- BSD 是商務對話、CoVoST 是朗讀句，都不是直播口語；ja→en 沒有直播風格的公開參考譯文集，VTuber 用語只靠 30 句自寫句做人工檢查（沒有參考譯文、沒有評分者一致性）。
- 「錯誤語言」「附加文字」都是啟發式：專有名詞保留假名也會算「含假名」；過長門檻抓不到短的多餘文字。
- 所有輸入是 WMT25 給的 Whisper 轉寫或人工文字，沒有經過 kikitori 自己的 ASR；ASR 錯誤傳遞的成本沒有量。

## 怎麼重跑

```text
uv run python -I -X utf8 experiments/translate_eval/menu.py
```

互動選單（專案規則：不靠記指令參數）：

| 選項 | 做什麼 |
|---|---|
| 1 準備資料 | 下載 WMT25、CoVoST 2、BSD 到 `data/`，整理成 `data/<資料集>.json`，並寫 `data/manifest.json`（來源、revision、日期） |
| 2 品質評測：全部設定 | 依模型分組，同一個模型的設定同時跑（Ollama 會平行處理），不同模型依序跑；逐句寫 `results/outputs/<設定>/<資料集>.jsonl`，中斷後再選一次會從沒翻過的句子繼續 |
| 3 品質評測：選一組設定 | 同上，只跑一組 |
| 4／5 只量延遲 | 每個資料集取前 60 句，**一次只送一個請求**，記錄量測前後的 GPU 用量；寫 `results/latency/<設定>.json`，不動品質輸出。GPU 空閒時用這個重量乾淨的延遲 |
| 6 計分：chrF／BLEU | 先在專案環境產生 `results/scoring/set_<資料集>.json`，再用隔離環境的 sacrebleu 計分並做 paired bootstrap |
| 7 計分：COMET | 隔離環境（含 PyTorch）在 CPU 上跑 `Unbabel/wmt22-comet-da`；第一次下載約 2.3 GB 到 `data/comet_model/`；逐單位分數依譯文雜湊快取 |
| 8 產生報表 | 錯誤統計＋所有表格 → `results/report.md`、`results/errors.json` |
| 9／10 VTuber 句 | 用指定設定翻譯 30 句 VTuber 風格日文（zh-TW 與 en），並排顯示 |
| 11 顯示進度 | 每組設定在每個資料集翻了幾句、錯幾句 |

隔離環境的實際指令（menu 用 `subprocess` 執行，不經過 shell）：

```text
uv run --isolated --no-project --python 3.12 --with-requirements experiments/translate_eval/requirements-score.txt python -I -X utf8 experiments/translate_eval/score.py experiments/translate_eval/results/scoring
uv run --isolated --no-project --python 3.12 --with-requirements experiments/translate_eval/requirements-comet.txt python -I -X utf8 experiments/translate_eval/score_comet.py experiments/translate_eval/results/scoring experiments/translate_eval/data/comet_model
```

`requirements-score.txt`（`sacrebleu==2.6.0`）與 `requirements-comet.txt`（`unbabel-comet==2.2.7`、`torch==2.15.0`、`setuptools==80.9.0`）都由 `uv pip compile` 解出全部遞移依賴並固定版本。sacrebleu、COMET、PyTorch **都沒有進專案依賴**（`pyproject.toml`、`uv.lock` 沒有改）。`setuptools` 要固定在 80.9.0，因為 `torchmetrics 0.10.3`（COMET 指定的版本）仍然 `import pkg_resources`，而新版 setuptools 已經移除這個模組。

`data/` 與 `results/` 由本目錄的 `.gitignore` 排除：資料集原文有著作權，不進 repo。

### 檔案

| 檔案 | 角色 |
|---|---|
| `menu.py` | 互動選單 |
| `common.py` | 路徑、資料集、受測設定（只用標準庫） |
| `datasets.py` | 下載與整理資料：逐欄驗證型別與長度，網路 I/O 都設 timeout 並重試 |
| `run.py` | 用正式的 `kikitori.translate.OpenAICompatibleTranslator` 逐句翻譯（`stream()`，同時量首字延遲與總延遲） |
| `report.py` | 計分輸入（OpenCC t2s）、可疑輸出計數、Markdown 報表 |
| `score.py` | 隔離環境：sacrebleu chrF／BLEU＋paired bootstrap |
| `score_comet.py` | 隔離環境：COMET＋paired bootstrap |
| `vtuber.py`、`vtuber_extra.json` | VTuber 句並排；`vtuber_extra.json` 是自寫的 8 句補充用語（てぇてぇ、初見、同接、切り抜き等） |

## 環境

- GPU：NVIDIA GeForce RTX 5070（12 GB），驅動 621.01。**量測期間另一個 agent 在同一張 GPU 上跑 ASR 實驗**（`experiments/vtuber_eval/`），沒有載入翻譯模型時 GPU 也已用掉 4–5.5 GB，使用率 50–99%。
- Ollama 0.40.14，OpenAI 相容 API（`http://127.0.0.1:11434/v1`），`reasoning_effort: "none"`（Qwen 關閉思考），`max_tokens` 256（正式預設）。
- 模型（Ollama digest 前 12 碼）：`qwen3.5:9b` Q4_K_M `56671c2ab938`、`qwen3.5:4b` Q4_K_M `d8b0f5e9760c`、`hy-mt1.5-7b` Q4_K_M `9069cca2b3dd`、`hy-mt1.5-1.8b` Q8_0 `ffc6393dd162`、`Sakura-GalTransl-7B-v3` IQ4_XS `66796c57ef7b`。
- OpenCC 1.4.2（專案既有依賴）。

## 資料（執行時下載，不進 repo）

| 資料集 | 方向 | 內容 | 計分單位 | 來源與授權 |
|---|---|---|---|---|
| `zh_speech` | ja→zh_CN | WMT25 General MT 的 **speech** domain：59 部 YouTube 影片的 Whisper 轉寫，依「。！？」後的空白切回 **726 個 ASR 片段**，逐片段翻譯（前文＝同一影片的前兩個片段） | **文件**（59 篇）：參考譯文是整段、沒有逐句對齊，所以把片段譯文接起來與整段參考比 | [wmt-conference/wmt25-general-mt](https://github.com/wmt-conference/wmt25-general-mt) commit `2b91010`，`refA`。官方說明 test data「can be freely used for research purposes」；原文著作權屬原作者 |
| `zh_social` | ja→zh_CN | WMT25 **social** domain（Mastodon 貼文）：13 篇中原文與參考行數相同的 12 篇，去掉空行後 **311 行** | 行 | 同上 |
| `en_covost` | ja→en | CoVoST 2 ja→en **test**（684 句；Common Voice 4 的朗讀句），以種子 12345 抽 **400 句** | 句 | 英文譯文 CC0（[CoVoST 2](https://github.com/facebookresearch/covost)）；日文句子 CC0（Common Voice）。官方 TSV 只有譯文，日文句子取自 HF [`fixie-ai/covost2`](https://huggingface.co/datasets/fixie-ai/covost2) 的轉載，並與官方 TSV **逐句比對譯文一致**、確認都在官方 test split |
| `en_bsd` | ja→en | Business Scene Dialogue **test**，只取**原文是日文**的對話情境，打亂後取整個情境直到 ≥300 句：**10 個情境、311 句** | 句 | [`ryo0634/bsd_ja_en`](https://huggingface.co/datasets/ryo0634/bsd_ja_en)，**CC BY-NC-SA 4.0**：只做內部評測，不散布 |

- WMT25 的 ja→zh 參考譯文**是公開的**（`data/wmt25-genmt.jsonl` 的 `refs.refA`），不需要退回 FLORES。WMT25 的 speech domain 原文就是 Whisper 轉寫（有錯字、斷句怪、沒有大小寫），內容是遊戲實況、Vlog、閒聊，是公開資料裡最接近直播字幕輸入的。
- ja→en 沒有同等的官方口語集（WMT25 沒有 ja→en）。CoVoST 2 是語音翻譯資料但句子是朗讀稿（偏書面），所以另加 BSD 的日文原創對話，補上「口語對話＋有文件結構（可比前文）」。
- **校準點**：WMT25 也公開了參賽系統的輸出，`zh_speech` 表格另列 7 個官方系統（`wmt25:` 開頭）。它們是**整篇文件一次翻譯、看得到全文**，條件比 kikitori 逐片段即時翻譯有利得多，只用來看「這個資料集上好的分數大約多少」，不參與結論。

## 方法

### 受測設定

全部透過正式的 `kikitori.translate` 翻譯器與預設組（`PRESETS`）送出；只改 `TranslatorConfig.temperature`，或用 `dataclasses.replace(preset, supports_context=False)` 關掉前文。譯文語言是 **zh-TW**（使用者實際看到的設定）與 **en**。

| 設定 | 模型 | 溫度 | 其他取樣參數 | 前文 |
|---|---|---|---|---|
| `q9-preset-ctx`（**現行正式設定，bootstrap 基準**） | qwen3.5:9b | 0.7 | top_p 0.8、presence_penalty 1.5 | 前兩句原文 |
| `q9-t0-ctx` | qwen3.5:9b | **0** | 同上 | 前兩句原文 |
| `q9-t0-noctx` | qwen3.5:9b | 0 | 同上 | **無** |
| `q4-preset-ctx`／`q4-t0-ctx`／`q4-t0-noctx` | qwen3.5:4b | 同上三組 | 同上 | 同上 |
| `hy7-preset`／`hy7-t0` | hy-mt1.5-7b | 0.7／0 | top_p 0.6 | 無（預設組本來就不帶） |
| `hy18-preset`／`hy18-t0` | hy-mt1.5-1.8b | 0.7／0 | top_p 0.6 | 無 |
| `sakura-preset`（**非商用，只當參考點**） | Sakura-GalTransl-7B-v3 | 0.3 | top_p 0.8 | 無；只做日翻中 |

- `en_covost` 沒有文件結構，前文永遠是空的，所以 `-noctx` 設定不重跑這個資料集。
- 溫度 0.7 是單次取樣，分數本身帶取樣變異；溫度 0 的比較不受這個影響。
- 同一個模型的設定同時送請求（Ollama 平行處理），llama.cpp 的批次運算可能讓溫度 0 的輸出與單獨執行時有極小差異；不影響設定之間的比較。

### 計分

- **字形**：zh-TW 輸出（經正式後處理，含選擇性的 OpenCC `s2twp`）與簡中參考譯文，**兩邊都用 OpenCC `t2s` 轉成簡體**再計分；否則繁簡差異會淹沒真正的品質差。`t2s` 只轉字形、不轉詞彙，台灣用語（影片／视频、軟體／软件）在簡中參考譯文前仍會被扣分。所有受測設定都輸出 zh-TW，所以這個偏差對它們一致；但和輸出簡中的 WMT25 校準系統相比會偏低。
- **chrF**：sacrebleu `CHRF()` 預設（字元 6-gram、β=2），適合不分詞的中文。**BLEU**：中文 `tokenize="zh"`，英文 `13a`。簽章附在各表下方。
- **COMET**：`Unbabel/wmt22-comet-da`（Apache-2.0），CPU、batch 16，輸入是原文＋t2s 後的譯文＋參考。
- **Paired bootstrap**：sacrebleu 內建 `PairedTest(test_type="bs")`，1,000 次、種子 12345（sacrebleu 預設）；COMET 用同樣做法自寫（`score_comet.py`，種子 12345，1,000 次）。每個設定都和基準（`q9-preset-ctx`）比，也和該資料集 chrF／COMET 最高的設定比。重抽的單位就是計分單位：`zh_speech` 是文件（相當於調查報告建議的 blockwise bootstrap），其他是句子／行。p < 0.05 視為顯著。
- **可疑輸出**（逐句，啟發式，定義在 `report.classify()`）：
  - 翻譯錯誤：翻譯器丟 `TranslationError`。空輸出：清理後是空字串。
  - 錯誤語言：zh 目標時，假名占（假名＋漢字）的 30% 以上，也就是主要內容還是日文；en 目標時，漢字＋假名占（漢字＋假名＋拉丁字母）的 30% 以上。「含假名」只要出現任何一個假名就算（不含「ー」「・」；人名或專有名詞留假名時也會算到）。
  - 簡體殘留：正式後處理之後仍有 `postprocess.simplified_characters()` 判定的簡體專用字。
  - 附加文字：輸出有換行而原文沒有；出現「注：」「譯文：」「Note:」之類說明標記；或長度超過原文能解釋的範圍（zh：大於 3×原文＋10 字；en：大於 8×原文＋20 字）。
  - 疑似翻出前文：「過長」且這句有帶前文。

