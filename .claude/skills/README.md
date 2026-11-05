# Project skills

`progress/` 是本專案自己寫的：輸入 `/progress` 會把進度追加到 `tasks/progress.md`，並勾選 `tasks/todo.md` 中已完成的任務。

其餘 skill 原封不動複製自 [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills)
（commit `301b02b12bff98e4688e6984af6556802322bbde`，plugin 版本 0.6.15），只挑選適合本專案的部分。
`../references/definition-of-done.md` 是 `incremental-implementation` 與 `planning-and-task-breakdown` 引用的清單，一併複製。

| Skill | 用途 |
|---|---|
| spec-driven-development | 新模組開工前先寫規格 |
| planning-and-task-breakdown | 把規格拆成可驗證的小任務 |
| incremental-implementation | 一次實作一個小切片並驗證 |
| test-driven-development | red-green-refactor；修 bug 先寫重現測試 |
| api-and-interface-design | 管線各階段之間的介面與事件設計 |
| source-driven-development | 依官方文件／原始碼實作（例如 pipecat） |
| documentation-and-adrs | 記錄架構決策、補 docs/architecture.md |
| debugging-and-error-recovery | 系統化找根因 |

更新方式：clone 上游 repo，用新版的 `skills/<name>/SKILL.md` 覆蓋對應檔案，並更新上面的 commit。

未採用：git-workflow-and-versioning（會主動 commit）、constraint-driven-development 與 context-engineering
（與 CLAUDE.md／hooks 重疊）、code-review-and-quality 與 code-simplification（與既有 review 工具重疊）、
web／部署相關的 skill（目前用不到）。performance-optimization、observability-and-instrumentation、
security-and-hardening 等到處理即時延遲或接翻譯 API 時再加。

## License

MIT License

Copyright (c) 2025 Addy Osmani

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.
