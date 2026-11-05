# kikitori

## 核心規則

> 對話壓縮後，`.claude/hooks/reinject_core_rules.py` 會把這一節重新注入給 Claude。保持精簡，只放最不能忘的規則。

- 依賴一律用 `uv add <pkg>==<version>`（開發依賴加 `--dev`）；禁止 `pip install` / `uv pip install`。新增依賴前先問使用者。
- 不直接編輯 `uv.lock`（用 uv 指令更新）。
- 不讀取或修改 `.env`；密碼、API key、token 只從環境變數讀，不寫死、不印到 log、不 commit。
- `.py` 檔改完會自動跑 ruff；ruff 回報無法自動修的問題要修掉。
- 完成前要跑測試與 lint（`uv run python -m unittest discover -s tests`、`uv run ruff check`），全部通過才算完成；不准刪測試或跳過測試。
- 刪除檔案或資料、git push、git reset --hard、force push、改 CI/CD 前先問使用者。

## 註解與型別

- 註解說明「為什麼」，不逐行說明「做什麼」。
- 對外介面寫 docstring。
- 每個模組開頭簡述它在架構中的角色。
- 全面使用型別標註。
- 修改程式碼時，同步更新相關註解。

## Skill 使用時機

`.claude/skills/` 的 skill 依下列情境主動使用，不必等使用者點名：

- 新模組或跨多個檔案的功能開工前：`spec-driven-development` 寫規格，使用者確認後再用 `planning-and-task-breakdown` 拆任務。
- 實作任務：`incremental-implementation`；寫邏輯、修 bug、改行為：`test-driven-development`。
- 設計模組之間的介面或事件格式：`api-and-interface-design`。
- 使用第三方函式庫（pipecat、onnxruntime 等）：`source-driven-development`，以官方文件與原始碼為準。
- 測試失敗或行為不符預期：`debugging-and-error-recovery`。
- 做出架構決策（例如選用哪個 VAD）：`documentation-and-adrs` 記錄決策。
- skill 內容與本文件衝突時以本文件為準；skill 要求 commit 時先問使用者，不自動 commit。
