---
name: progress
description: 把目前的工作進度同步到 tasks/progress.md（追加一筆紀錄），並在 tasks/todo.md 勾選已完成且驗證過的任務。使用者輸入 /progress 時使用。
disable-model-invocation: true
argument-hint: "[補充說明，例如這次的重點或卡住的地方]"
---

# 同步進度到 md 檔

把這次工作的進度寫進 `tasks/progress.md`，並更新 `tasks/todo.md` 的勾選狀態。
使用者的補充說明：$ARGUMENTS

## 1. 收集事實

只記錄有證據的事，不要憑印象寫：

- `git status --short`、`git diff --stat`：目前未 commit 的改動
- `git log --oneline -10`：最近的 commit
- `date "+%Y-%m-%d %H:%M"`：紀錄時間
- 這次對話中實際跑過的指令與結果（測試、lint、實驗腳本）
- 若 `tasks/progress.md` 已存在，讀最後一筆紀錄，避免重複記錄同一件事

## 2. 追加到 `tasks/progress.md`

檔案不存在就建立，第一行為 `# 進度紀錄`。只在檔尾追加，不修改或刪除舊紀錄。格式：

```markdown
## YYYY-MM-DD HH:MM — <一句話標題>

**完成**
- <做了什麼，附相關檔案路徑>

**指令與結果**
- `<指令>` → <通過 / 失敗與錯誤摘要>

**未完成 / 卡住**
- <問題與目前判斷；沒有就寫「無」>

**下一步**
- <具體的下一個動作>
```

- 測試失敗、步驟被跳過就照實寫，不要寫「應該可以」。
- 不寫入密碼、API key、token 或 `.env` 的內容。

## 3. 更新 `tasks/todo.md`

- 檔案不存在就跳過，並提醒使用者可用 `planning-and-task-breakdown` 產生任務清單；不要自己建立。
- 只把「已完成且驗證通過」的項目從 `- [ ]` 改成 `- [x]`。
- 不新增、刪除或改寫任務內容；發現清單需要調整時，寫在 progress 的「下一步」並告訴使用者。

## 4. 回報

告訴使用者：追加了哪一筆紀錄（標題）、勾選了哪些任務、有哪些事沒能確認。
不要 commit 這些檔案，除非使用者要求。
