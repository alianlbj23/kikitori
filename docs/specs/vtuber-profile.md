# Spec: 日語直播（VTuber）特化

> 狀態：**已實作**（2026-11-06，Milestone 16；端到端 VRAM 與記憶體壓力測試已在 v0.2.0 驗收完成，見「驗證」）。規格寫於同日。依據：`reports/VTuber 直播模型選型調查.md`（文獻）、
> `experiments/vtuber_eval/`（ASR 實測）、`experiments/translate_eval/`（翻譯實測）、
> `experiments/hallucination_lexicon/`（幻覺片語）。使用者已授權依建議實作，最後驗收。
> 修改：`hardware.md`（推薦組合）、`translate.md`（預設組參數、術語表、後處理）、`asr.md`（片語）。

## Objective

kikitori 的主要情境是看日語 VTuber 直播（雜談、遊戲、連動，有 BGM 與遊戲音效）。這次依
實測把推薦組合、翻譯參數與過濾調到最適合這個情境，並補上直播用語。結論都以本機可重現的
評測與統計檢定為依據，不靠模型作者自報。

## 決策（依據與數字見各實驗 README）

| 項目 | 決策 | 依據 |
|---|---|---|
| 即時辨識（GPU） | `large-v3-turbo`（原 `small`） | YODAS 口語 CER turbo 比 small 低 5.6 點（95% CI [2.9, 8.2]）；延遲中位 ~160 ms |
| 完整辨識（GPU） | `large-v3`（原 `large-v3-turbo`）；與本機翻譯 LLM 共用 8–12 GB 顯卡時 `int8_float16` | 乾淨語音兩者打平；BGM 10 dB／5 dB 時 turbo 差 4.1／5.3 點；int8_float16 品質不變（Δ −0.4 [−1.0, 0.3]） |
| CPU | 維持 base（即時）＋ small（完整） | CPU 上 turbo／large-v3 跟不上即時 |
| kotoba-whisper-v2.0 | 不提供 | YODAS 上比 large-v3 差 3.9–4.7 點；非語音時輸出「ごめん」且過濾擋不住；訓練資料條款有灰色地帶 |
| 即時辨識在 turbo 為 C 級的語言（th、ar、hi） | 即時也用 `large-v3` | 語言品質表；turbo 官方公告承認 Thai 退化 |
| 翻譯溫度 | Qwen3.5、HY-MT 一律 0 | WMT25 ja→zh chrF 全部顯著提升、social COMET 顯著提升，其他資料集無退步 |
| 前文 | 維持前兩句原文；輸出長度異常（> 3 倍原文且 > 40 字）時改不帶前文重翻一次 | 前文讓 speech COMET +.015（9B）／+.032（4B）；4B 偶爾把前文一起翻出 |
| 翻譯模型 | 依 VRAM：qwen3.5:9b（≥ 7.5 GB 可給翻譯）→ qwen3.5:4b；HY-MT 只作自選 | Qwen 9B 與 HY-MT 7B COMET 同級，但直播用語 Qwen 明顯較好；HY-MT 1.8B 顯著較差且有幻覺 |
| 直播用語 | 內建術語表：原文出現該詞時，把「詞 → 譯法」放進提示詞 | 9B 仍誤譯 赤スパ、推し 等；WMT 術語任務與 HY-MT 官方術語範本 |
| 後處理 | 清除不完整的特殊 token 碎片（`<|...`）；繁簡轉換只轉含簡體專用字的片段，避免「臺／傢夥／好喫」 | translate_eval 錯誤計數 |
| 幻覺片語 | ja hard 加「次の動画でお会いしましょう。」 | vtuber_eval 非語音輸出 |

## 內建術語表

- 檔案：`src/kikitori/translate/glossary.py`（Python 資料，打包不需額外設定）。
- 範圍：原文為日語時；譯文語言 zh-TW、zh-CN、en、ko（其他語言沒有可靠譯法時不提供，模型自行翻）。
- 每個詞條：原文（含常見寫法變體，例如 スパチャ／スーパーチャット）、各語言譯法。只收直播社群通用、
  有平台官方名稱者優先（例如 YouTube 的 Super Chat 中文介面名稱「超級留言」）。
- 注入：只放「這句原文實際出現的詞」，最多 8 條；generic 範本用「術語：A → B」行，HY-MT 用官方術語範本。
- 使用者可在設定關閉內建術語表（`translation.glossary.builtin`，預設開），並自訂詞條（`translation.glossary.entries`，最多 500 筆，
  每筆 `{source, target, target_language|null}`，同一個原文寫法時優先於內建表；設定形狀與驗證見 `pipeline-engine.md`）。
- 實作（`translate.md`「術語表」）：53 個詞條；比對以 NFKC＋casefold 正規化、同位置取最長寫法；HY-MT 用官方術語干預範本
  （「参考下面的翻译：\n{src} 翻译成 {tgt}…」），Sakura 用官方 GPT 字典格式（`src->dst`），generic 用譯文語言寫的「術語」區塊。

## 實作對照

| 決策 | 實作 | 測試 |
|---|---|---|
| 推薦組合 | `hardware/recommend.py`：`GPU_LADDERS`、`_choose_option()`；8 GB 以上 turbo／large-v3，與本機 LLM 共用時依預算選精度（12 GB：兩個 int8_float16＋qwen3.5:9b），連 4B 都放不下時即時改 small；4 GB 以下沿用先前組合；表與算式見 `hardware.md`「8 GB 以上等級的組合」 | `test_hardware_recommend.py`（16／12／11／10／8 GB、7680 MiB、遠端、不需翻譯、Pascal、7680–24576 MiB 每 256 MiB 都放得下） |
| th／ar／hi 即時也用 large-v3 | `_live_model_upgrades()`（即時模型 C 級、完整模型較好時）；同模型同精度時 `pipeline.factory` 只載入一份權重（CTranslate2 `inter_threads=2`＋`WhisperTranscriber.with_options()`），VRAM 算一份＋512 MiB | `test_hardware_recommend.py`、`test_pipeline_factory.py`、`test_asr_whisper.py` |
| 溫度 0 | `translate/presets.py`：Qwen3.5、HY-MT `temperature=0.0`，其他參數不變 | `test_translate_prompts.py`、`test_translate_client.py` |
| 前文保險 | `translate/context_guard.py`（> 3 倍且 > 40 字，CJK → 拼音文字 8 倍；或抄出 ≥ 6 字的前文原句 → 不帶前文重翻一次）；factory 包住兩條路徑 | `test_translate_context_guard.py` |
| 術語表 | `translate/glossary.py`、`prompts.py`、`client.py`、設定 `translation.glossary` | `test_translate_glossary.py`、`test_config.py`、`test_pipeline_factory.py` |
| 後處理 | `translate/postprocess.py`：`<|` 起的特殊 token 碎片（開頭只去 token，其他位置連同之後的文字丟掉）；繁體為主的句子只轉簡體字附近的片段 | `test_translate_prompts.py`（translate_eval 錯誤清單的實例） |
| 幻覺片語 | `lexicon.json` ja hard 加「次の動画でお会いしましょう。」，重新產生 `asr/hallucination_lexicon.py` | `test_asr_hallucination_lexicon.py` |

設定沒有升 schema：`translation.glossary` 是有預設值的新欄位（與 `keep_warm_secs` 相同的做法）。新訊息碼 `asr_bgm_robust_final`、
`asr_live_model_for_language`（`hardware.md`「訊息碼」）；`asr_gpu` 的 `compute_type` 在兩條路徑精度不同時是「即時 / 完整」。

## 驗證

- 單元測試：推薦組合（各 VRAM 等級、C 級語言、與翻譯共用預算）、溫度、術語注入（出現才注入、上限、各提示詞格式）、長度異常重翻、特殊 token 清除、選擇性繁簡轉換不產生「臺／傢夥／好喫」。
- 實測：`experiments/translate_eval` 以術語表開／關重跑 VTuber 句與 WMT25 speech 子集，確認直播用語改善且 COMET 不退步。
  - **結果（2026-11-06，qwen3.5:9b、溫度 0、前文＋前文保險）**：VTuber 30 句 × zh-TW／en 中 18 格譯文改變，赤スパ（紅色火花 →
    紅色超級留言／red spicy → Red Super Chat）、推し（我喜歡的人 → 推／crush → oshi）、メン限（men's lineup → members-only）、
    同接、初見さん、コラボ、歌枠、切り抜き師 都改對；1 格（「切り抜いといて」zh-TW）動詞意思變錯。WMT25 speech 726 片段只有 5 個
    出現術語：chrF −0.03（p 0.116）、BLEU −0.03（p 0.162）、COMET −0.0005 [−0.0009, −0.0001]（p 0.037，未校正多重比較）——
    統計上偵測得到但只有前文效果的 1/30，來自改變的 5 句中兩三句的措辭（例如「4000 日圓」→「四千元」）。判斷：維持術語表預設開啟。
    新程式（溫度 0 預設組、前文保險、後處理）在術語表關閉時與舊的 `q9-t0-ctx` 逐句相同。細節見 `experiments/translate_eval/README.md`
    「Milestone 16：術語表開／關」。
- 端到端：本機 12 GB 顯示卡上實際以新推薦組合跑 session，量 VRAM 是否在預算內（記憶體壓力測試一併檢查）。
  - **結果（2026-11-06，v0.2.0 驗收，`tasks/acceptance-report-v0.2.0.md`）**：turbo int8_float16 ／ large-v3 int8_float16 ＋ qwen3.5:9b，影片翻譯 session 中
    引擎 3.2–3.6 GB、Ollama 7.07 GB、整張卡最高 10.8／12.2 GB；`/api/ps` 的 `size` ＝ `size_vram`（沒有放到 CPU）。完整字幕 `latency_ms` 中位數 578 ms、
    即時字幕延遲 0.31 s；但 int8_float16 在載入時量化，按「開始」到聆聽 10.6–13.3 s（v0.1.0 約 3 s）。
  - 共用權重第二個 worker 的 512 MiB 仍是估計值（th／ar／hi 的組合沒有在這次端到端量測）。
