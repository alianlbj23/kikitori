# kikitori

## 核心規則

> 對話壓縮後，`.claude/hooks/reinject_core_rules.py` 會把這一節重新注入給 Claude。保持精簡，只放最不能忘的規則。

- 依賴一律用 `uv add <pkg>==<version>`（開發依賴加 `--dev`）；禁止 `pip install` / `uv pip install`。新增依賴前先問使用者。
- 不直接編輯 `uv.lock`（用 uv 指令更新）與 `LICENSE`。
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
