# Spec: 振假名（日文原文字幕的讀音）

> 狀態：**已實作**（2026-11-06）。規格與實作同日完成；使用者已同意新增依賴 SudachiPy（固定版本）、辭典**延後下載**
> （打開振假名時才下載，不打包進安裝檔），並授權寫完規格直接實作。
> 上層約定：`pipeline-engine.md`（協定、設定 schema、`download_progress`、`models.list`／`models.delete`）、`ui.md`、
> `competitive-features.md`（「先不做」表的振假名列，改為本文件）。依賴的模組：`asr.md`（下載與 SHA-256 驗證）。

## Objective

給用字幕學日語的人：日文原文字幕（即時與完整）在漢字上方標讀音（平假名），字幕紀錄與字幕視窗都顯示。只影響畫面，
匯出的字幕與 OBS 文字檔不變。沒開振假名的使用者不下載辭典、不載入分析器、不多佔記憶體。

## 決策

### SudachiPy 版本：0.6.11（不是 0.7.0）

| | 0.6.11（2026-04-13） | 0.7.0（2026-09-24） |
|---|---|---|
| 穩定性 | 穩定版 | README 自述「v0.7.* is unstable…breaking changes even between patch versions」 |
| 辭典格式 | V0（SudachiDict ≤ v20260723；最後一個有 zip 的是 v20260428） | V1（SudachiDict v20261015 起） |
| wheel（cp312） | win_amd64、macosx_11_0_arm64、macosx x86_64／universal2、manylinux2014 x86_64／aarch64 | abi3：同上 |
| API | `Dictionary(dict=<絕對路徑>).create(SplitMode.C)` | `.tokenizer()`（改名） |

實測（本機 Windows）：0.6.11 讀不了 v20261015（`Invalid header version`），0.7.0 讀不了 v20260428（`Invalid description: V0 version`）。
選穩定版 0.6.11＋V0 辭典 v20260428；兩者在 `pyproject.toml`、`furigana/dictionary.py` 一起固定，升級時三者（SudachiPy、網址、雜湊）一起改。
SudachiPy 沒有其他相依套件（`uv add sudachipy==0.6.11` 只多這一個）。不安裝 `sudachidict_*` 套件（那會把辭典打包進安裝檔）。

### 辭典：SudachiDict **core**（不是 small）

來源：WorksApplications/SudachiDict 的 GitHub Release
`https://github.com/WorksApplications/SudachiDict/releases/download/v20260428/sudachi-dictionary-20260428-core.zip`
（72 238 136 bytes，SHA-256 `40c8ffc0…a7efa3239f`；zip 內 `system_core.dic` 217 374 303 bytes、`LEGAL`、`LICENSE-2.0.txt`，三個檔各自固定 SHA-256）。

比較（`experiments/furigana_dict/compare.py`，語料 2014 句：`vtuber_eval` 的 YODAS／FLEURS／ReazonSpeech／動畫參考轉寫、
`translate_eval` 的 WMT25 ja 原文、直播用語補充句；同一個對齊演算法）：

| | small | core |
|---|---|---|
| 下載（zip） | 39.8 MiB | 68.9 MiB |
| 解壓後 | 117.3 MiB | 207.3 MiB |
| 開啟辭典 | 0.028 s | 0.035 s |
| 每句分析 | 0.13 ms | 0.15 ms |
| 漢字有標讀音 | 15654／15685 | 15656／15685 |
| 私有記憶體（開啟後） | +13.7 MiB | +27.8 MiB |
| 讀音與另一版不同的句子 | 86 句（4.3%） | |

86 句差異逐詞看（`results/diff.txt`），**core 對的占絕大多數**，而且是直播口語最常見的詞：
「私たち」small 讀 わたくし（10 次）、「日本語」にっぽん（7）、「韓国人／有名人」の人 にん（10）、「お兄ちゃん／お父さん／お姉ちゃん」
あに／ちち／あね（4／3／2）、「一度」ひとたび（3）、「水曜日」の日 ひ（4）、「腕時計／意気込み／時間切れ」漏連濁…。原因是 small 缺複合詞與
口語詞條，退回單字讀音。core 錯而 small 對的只有少數人名（林修→りお、杉田→すいた、近本→ちかもと 等 ≤ 5 處）。
多 29 MiB 下載、14 MiB 私有記憶體換常用詞讀對，選 core。full（131 MiB 下載）只多專有名詞，不值得。

### 對齊演算法（`src/kikitori/furigana/ruby.py`，純函式）

- Sudachi 以 **C 單位**（整個複合詞）分析，取 `reading_form()`（片假名）轉平假名：C 單位的讀音含連濁等整詞資訊（日本橋＝にほんばし）。
- 詞的 A 單位讀音接起來與整詞相同時，改用 A 單位逐詞標（外国人参政権 → 外国／人／参政／権 各自標），否則整詞標。
- 每個詞：切成漢字段與假名段（々〆〇ヶ 算漢字；片假名比對時轉平假名），以假名段為錨點把讀音切給漢字段：
  「食べる」→ 食＝た（送り仮名不重複）、「お腹」→ 腹＝なか、「取り扱い」→ 取＝と、扱＝あつか。
- 錨點在讀音中出現不只一處（貪婪與非貪婪切法不同）或對不上時，去掉頭尾相同的假名後**整段標**（「凄ぇ」→ すごい 標在整詞）；
  讀音不是假名（未登錄詞）、讀音超過 64 字（UI 與 Rust 的上限）、詞裡有數字或符號時**不標**；漢字與假名交錯超過 16 段的詞不逐段對齊
  （避免正規表示式回溯），直接整段標。
- 位置以 code point 計（與 `stable_chars` 相同），只標含漢字的片段，讀音為平假名（含長音「ー」）。

## 協定（`protocol_version` 維持 1；設定 schema 維持 3）

1. **`subtitle`** 新增選填 `ruby: [[start, end, "よみ"], ...]`：只在「設定開啟、辭典已下載、這句字幕的 `source_language` 是 ja
   （偵測或指定）、而且屬於目前的 session」時出現（即時與完整字幕都有，可能是空陣列＝這句沒有漢字）。`start`／`end` 是 `original` 的
   code point 位置（`[start, end)`），依序、不重疊；讀音只含平假名與「ー」。不認得的 UI 照舊（新增欄位）。
2. **`download_progress.kind`** 新增 `"furigana"`（`model` 為 `"furigana-dictionary"`）。
3. **`models.list`** 新增 `furigana`：`{id: "furigana-dictionary", installed, edition: "core", version: "20260428", download_bytes,
   installed_bytes, disk_bytes: int | null, path: str | null, license, license_url, in_use}`（`in_use`：辭典正被執行中的 session 開啟）。
4. **`models.download {"model": "furigana-dictionary"}`**：背景下載（同 ASR，回應寫出後才啟動），進度 `kind: "furigana"`。
   已安裝時立即 `done`。失敗（網路、逾時、大小或雜湊不符、zip 內容不符）→ `state: "error"`，`error` 為 `"<例外類別>: <訊息>"`。
5. **`models.delete {"model": "furigana-dictionary"}`** → `{"model", "freed_bytes"}`；下載中或辭典開啟中 → `model_in_use`
   （訊息請使用者先關閉振假名）；沒下載 → `model_not_downloaded`；路徑不安全或刪除失敗 → `internal_error`（同 ASR 模型）。
   刪除成功時若 `furigana.enabled` 為 true，一併改成 false（session 執行中、還沒遇到日文字幕時辭典沒開啟，可以刪）。
6. **新方法 `session.set_furigana {"enabled": bool}`**：存進設定 `furigana.enabled`，有 session 時立即套用；回應同
   `session.set_translation`（`settings.get` 的結果加 `session_id`）。`enabled` 不是布林或有其他參數 → `invalid_params`；
   打開時辭典沒下載 → `model_not_downloaded`（設定不變）；存檔失敗 → `settings_io_error`。

```json
{"id": 30, "method": "models.download", "params": {"model": "furigana-dictionary"}}
{"id": 30, "result": {"model": "furigana-dictionary", "started": true}}
{"event": "download_progress", "data": {"model": "furigana-dictionary", "kind": "furigana", "downloaded_bytes": 72238136, "total_bytes": 72238136, "state": "done", "error": null}}
{"id": 31, "method": "session.set_furigana", "params": {"enabled": true}}
{"event": "subtitle", "data": {"utterance_id": 5, "original": "今日は食べる", "translation": "今天要吃", "source_language": "ja", "target_language": "zh-TW", "is_final": true, "start_sample": 0, "end_sample": 32000, "latency_ms": 640, "session_id": 3, "ruby": [[0, 2, "きょう"], [3, 4, "た"]]}}
```

## 設定

`furigana: {"enabled": false}`（頂層新物件，只能有 `enabled`，布林）。缺少時用預設值，**不升 schema**（與 `translation.glossary` 相同）；
代價同其他新欄位：舊版程式讀到會報 unknown setting。`settings.set` 也能改它，但只從下一個 session 生效；UI 用 `session.set_furigana`（立即生效）。

## 引擎行為（`src/kikitori/engine/furigana_service.py`）

- `session.start` 時記下這個 session 與當時的 `furigana.enabled`；**第一句需要標的字幕**（開啟、ja、這個 session）才開啟辭典——
  從不出現日文的 session 完全不載入。開啟約 0.03 秒（mmap），在送出字幕的執行緒上做一次。
- 執行中關閉（`session.set_furigana false`）→ 立即釋放；開啟 → 下一句日文字幕載入。session 送出結束狀態（`idle`／`error`）之前釋放
  （與 ASR 模型相同的時機）；引擎結束時也釋放。已結束 session 的遲到字幕不會重新載入。
- 辭典沒下載（例如用 `settings.set` 打開、或之後被刪掉）、開啟或分析失敗：這句不帶 `ruby`，記一次 warning（同一個 session 不重複），
  字幕照常送出。辭典下載完成時重設，下一句再試。
- 分析、開啟、關閉、刪除共用一把鎖：刪除時不會有人正在開啟同一個檔案（Windows 上 mmap 中的檔案刪不掉）。

## 下載與檔案（`src/kikitori/furigana/dictionary.py`）

- 位置：模型快取旁 `<cache>/kikitori/furigana/sudachidict-core-20260428/`（Windows `%LOCALAPPDATA%\kikitori\furigana\…`），
  內含 `system_core.dic`、`LEGAL`、`LICENSE-2.0.txt`（授權全文與聲明隨辭典放在一起）與完成標記 `kikitori-furigana.json`。
- 下載沿用 `asr.download.download_file()`：只允許 https（轉址也一樣）、30 秒 socket timeout、續傳（`.part`＋Range）、Content-Length／
  Content-Range 檢查、邊下載邊算 SHA-256（不符刪 `.part`）、可取消、下載前檢查磁碟空間。
- 只解出固定的三個 zip 成員（固定 zip 內路徑，不會寫到資料夾外），逐檔驗證大小與 SHA-256；先解到 `….tmp`，全部通過才
  `os.replace` 成正式資料夾（原子），失敗刪掉暫存資料夾；完成後刪 zip。
- `is_installed()` 只比對完成標記與檔案大小（不重算雜湊，`models.list` 要快）。刪除：資料夾不是連結、解析後就在根目錄正下方、
  只有普通檔案才刪（否則 `UnsafeDictionaryPathError`，什麼都不刪），先刪完成標記；連同未完成下載的 zip 殘留一起刪。

## UI

- **設定頁**（字幕外觀區塊，「顯示翻譯」下方）：「在日文原文上標振假名」開關，立即生效（沒有 name，不跟「儲存設定」）。
  打開時若辭典沒下載：先 `models.download`，開關維持勾選並鎖住、下方顯示進度條與「正在下載振假名辭典：N%」；`done` 後重讀
  `models.list` 再 `session.set_furigana true` 並通知「已打開振假名」；失敗顯示錯誤、開關回到關閉，可再按一次重試。
  引擎不支援（`models.list` 沒有 `furigana`）時開關停用並說明。邏輯在 `app/src/lib/furigana.ts`（純函式）。
- **字幕紀錄與字幕視窗**：`app/src/lib/ruby.ts` 的 `rubyParts()` 把原文切成「普通文字」與「標讀音的詞」，再依 `stable_chars` 分成穩定
  前綴與淡化的尾巴（切點與 `splitStable` 相同、不切字素叢集）；**詞跨過切點時整個詞放到尾巴**，不會一半淡化。`segmentNodes()` 以 DOM
  呼叫建立 `<ruby>詞<rt>よみ</rt></ruby>` 與文字節點（不用 innerHTML，原文與讀音都來自引擎）。字幕紀錄只在詞、讀音或切點改變時重建
  （`rubyKey`），沒有 `ruby` 的句子走原本的純文字路徑。`rt` 不可選取（複製一句只複製原文）。
- **模型頁**：「振假名辭典」區塊：版本、未下載（約 69 MiB，在設定頁打開時下載）或「已下載，佔用 N」、授權；已下載時「刪除辭典」
  （頁面內確認，同 ASR 模型）；使用中（`in_use`）時停用並說明先關閉振假名。
- **匯出字幕**、OBS 文字檔：不變（只用 `original`／`translation`）。
- 新字串進 13 個語系（`settings.furigana*`、`furigana.*`、`error.setFurigana`、`error.deleteFurigana`）；程式碼裡沒有中日韓文字
  （`ruby.ts` 以 code point 判斷平假名）。

## Rust（`app/src-tauri`）

- `protocol.rs`：`SubtitlePayload.ruby: Option<Vec<RubySpan>>`（`RubySpan(u64, u64, String)`，序列化成陣列）。型別不對（不是陣列、
  元素不是三元組、負數）→ 整行拒絕（與其他欄位相同）；型別對但內容不合法（超出 `original` 的 code point 數、未排序或重疊、讀音空白或超過
  64 字、不是平假名）→ 只丟掉 `ruby`、保留字幕並記 warning（振假名是顯示提示）。沒有 `ruby` 時轉發也沒有。
  `download_progress.kind` 本來就接受任何小寫識別字，`furigana` 照樣轉發（加測試）。
- `commands.rs`：新指令 `session_set_furigana {enabled}`（一般期限）；`models_download`／`models_delete` 的 id 規則本來就接受
  `furigana-dictionary`。`build.rs`、`capabilities/main.json` 加上 `allow-session-set-furigana`。

## 記憶體（本機 Windows 11、Ryzen 7 5700X）

`FuriganaService`＋真實辭典，每個 session 分析 2014 句後結束，重複三次：

| | 工作集 | 私有記憶體 |
|---|---|---|
| 執行中（辭典開啟、分析過全部句子） | +105 MiB | +27.5 MiB |
| session 結束後 | +3 MiB | +0.7 MiB（三次後 +1.3 MiB：sudachipy 模組本身留在行程內，Python 無法卸載擴充模組） |

工作集的大部分是 mmap 映射的辭典頁（以檔案為後盾、可由系統隨時丟棄，不佔分頁檔）；`Dictionary.close()` 解除映射後確實歸還
（sudachi.rs 以 `memmap2` 開啟系統辭典）。第一句（含匯入 sudachipy 與開啟辭典）約 25–30 ms，之後每句約 0.2 ms。

## 授權

- SudachiPy／sudachi.rs：Apache-2.0。wheel 沒附授權檔，打包時從 `packaging/engine/license_overrides/sudachipy/` 補上
  （`LICENSE` 取自 sudachi.rs tag `v0.6.11`，另附編譯進擴充模組的 57 個 Rust crate 的授權檔，清單與來源見該目錄的 `SOURCE.md`），
  輸出到 `THIRD_PARTY_LICENSES/sudachipy-0.6.11/`。
- SudachiDict：Apache-2.0；含 UniDic（BSD-3-Clause）與 NEologd 的聲明（`LEGAL`）。不打包，隨下載放在辭典資料夾。

## 打包

- PyInstaller：`pyinstaller-hooks-contrib` 的 `hook-sudachipy.py` 收集 `sudachipy/resources/`（char.def、unk.def、rewrite.def、
  sudachi.json）與 `sudachipy.config`／`sudachipy.errors`（Rust 擴充會匯入）。`kikitori.furigana.analyzer` 在函式內匯入 sudachipy，
  PyInstaller 的靜態分析照樣找得到。
- `kikitori-engine --self-check` 新增 `sudachipy`：確認 resources 都在；這台機器已下載辭典時真的分析一句（今日は食べる → きょう、た），
  否則給一個不是辭典的檔案，確認 Rust 擴充回報 `SudachiError ... header`（擴充、設定與錯誤類別都載得起來）。CI 三個 OS 的 engine bundle
  都跑 self-check（CI 沒有辭典，走第二種）。

## Testing Strategy

- Python：`test_furigana_ruby.py`（送り仮名、接頭假名、逐段對齊、片假名、不唯一時整詞標、對不上整詞標、讀音不是假名或有數字不標、
  code point、A 單位與連濁、找不到的詞、空白）、`test_furigana_dictionary.py`（安裝與授權檔、已安裝不下載、zip 雜湊不符、成員內容不符時原子失敗、
  取消後續傳、截斷、拒絕 http、狀態、竄改、刪除與殘留、不安全的資料夾）、`test_furigana_analyzer.py`（假 sudachipy：C／A 單位、關閉、長度、錯誤）、
  `test_engine_furigana.py`（關閉時不載入、ja 才標、非 ja 不載入、即時與完整都有 `ruby`、結束時釋放、沒有辭典只記一次 warning、
  `session.set_furigana` 參數與 `model_not_downloaded`、下載事件 `kind`、下載失敗事件、`models.list`、使用中不能刪、關閉後可刪、
  停止時釋放、服務層：其他 session、遲到字幕、分析失敗）、`test_config.py`（預設、來回、缺少、驗證）。
- Rust：`protocol.rs` 的 `subtitle_ruby`（合法、空陣列、沒有時不轉發、不合法只丟 `ruby`、型別錯拒絕整行）、`download_progress_kind` 的 `furigana`。
- TS：`ruby.test.ts`（切分、與 `stable_chars` 共存、跨切點整詞、code point、不合法不畫、`<ruby>`／`<rt>` 節點與文字節點）、`furigana.test.ts`
  （開關各狀態、下載後開啟、刪除條件）、`subtitles.test.ts`、`validate.test.ts`、語系完整性與 CJK 掃描。

## Boundaries

- 只標日文原文；中文原文的注音／拼音、翻譯結果不標。
- 不做點字查詞、不讓使用者編輯讀音或加自訂辭典（之後可用 Sudachi 使用者辭典）。
- 讀音錯誤（人名、未登錄詞）不修；辭典版本固定，更新要改程式。

## 已知限制

- 人名與 VTuber 名常讀錯或不標（辭典沒有、或依一般讀音），例如實測「林修」→ りお。
- 即時字幕每次重新分析整句，前後兩則的切詞可能不同（讀音跟著原文變），穩定前綴內的詞通常不變。
- 讀音取自辭典，不另外推測：
  - 跨單位的連濁不會標出，例如「義務付け」的「付」標成 つ（應為 づ），實測 `ruby("義務付けられています")` → `[(0,2,'ぎむ'),(2,3,'つ')]`。
  - 辭典給的少見讀音會照樣標，例如「下界」→ かかい（一般讀 げかい）。
  - 正式版驗收人工抽查 10 句、56 個詞，54 個正確（`tasks/acceptance-report-v0.2.0.md` 第 7 節）。要改善可以之後加 Sudachi 使用者辭典；不在對齊演算法裡猜連濁。
