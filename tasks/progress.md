# 進度紀錄

## 2026-11-05 09:57 — 導入 agent-skills 的 8 個 skill 並新增 /progress

**完成**
- 從 addyosmani/agent-skills（commit `301b02b`）挑出 8 個適合本專案的 skill，原樣複製到 `.claude/skills/`：
  spec-driven-development、planning-and-task-breakdown、incremental-implementation、test-driven-development、
  api-and-interface-design、source-driven-development、documentation-and-adrs、debugging-and-error-recovery
- 複製被引用的清單 `.claude/references/definition-of-done.md`
- 新增 `.claude/skills/README.md`：來源 commit、各 skill 用途、未採用的 skill 與理由、MIT 授權全文
- 新增 `.claude/skills/progress/SKILL.md`：`/progress` 指令，把進度追加到 `tasks/progress.md`、勾選 `tasks/todo.md`
- 本筆為第一次執行 `/progress`，建立本檔

**指令與結果**
- `uv run python -m unittest discover -s tests` → 通過（13 個測試）
- `uv run ruff check` → 通過（All checks passed!）
- 尚未 commit 的既有改動（本次對話之前完成）：`pyproject.toml` 新增 `experiment-vad` 依賴群組（`pipecat-ai==1.14.0`）、`uv.lock` 相應更新；`docs/`、`experiments/` 尚未進版控

**未完成 / 卡住**
- `incremental-implementation` 要求每個小步驟都 commit，與「使用者要求才 commit」的習慣衝突；目前保持原檔未改，尚未決定是否在 CLAUDE.md 補規則
- `PreCompact` hook（壓縮前提醒更新進度）未加，需改 `settings.json`，待使用者決定
- 沒有 `tasks/todo.md`，尚未有任務清單

**下一步**
- 決定是否 commit `.claude/skills/`、`.claude/references/`、`tasks/`、`docs/`、`experiments/` 與依賴改動
- 用 `spec-driven-development` 為第一個模組（例如 VAD 切句包裝層）寫規格，再用 `planning-and-task-breakdown` 產生 `tasks/todo.md`
