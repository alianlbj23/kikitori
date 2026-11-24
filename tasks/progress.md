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

## 2026-11-09 20:41 — 發布 v0.3.0／v0.3.1、撤下 macOS、遠端運算做到 T6

**完成**
- 發布（都已合併到 main，Release 由 workflow 建置）：
  - v0.3.0（PR #3，`fbbb0ef`）：v0.3 G1–G8＋多人說話者標籤（`docs/specs/speaker-labels.md`）。
  - 發布說明改英文（PR #4，`5758a3d`）：`scripts/release.py` 下載表與安裝提示英文化；`docs/release-notes/v0.3.0.md` 改寫成英文。
  - v0.3.1（PR #5，`5aabf13`）：啟動時提醒下載 GPU 加速元件（`app/src/lib/gpu-reminder.ts`）、Ollama 太舊時說明要更新（`app/src/lib/download-error.ts`）。
  - 暫停發布 macOS（PR #6，`0b452c9`）：`RELEASED_PLATFORMS` 只有 windows、linux；`release.yml` 拿掉 macos；README 13 語平台行。
  - 已上架的 v0.3.0、v0.3.1：Release 說明重產、macOS 附件刪除、`latest.json` 拿掉 darwin（手動）。
- 修掉 CI 上的不穩定測試：振假名／說話者標籤引擎測試不再依賴即時字幕時序；OBS 閒置清空改從送出前計時（`app/src-tauri/src/obs.rs`）。
- Linux 實機（alian_server）Ollama 412：原因是舊的 Docker 容器（0.16.3）佔住 11434，新版 systemd 服務起不來；使用者已改用 0.40.15。
- 遠端運算（分支 `feat/remote-compute`，**尚未 commit**）：
  - 研究：`reports/kikitori 遠端運算架構.md`、`research_notes/kikitori 遠端運算架構/`（含資安威脅模型 `security_advisories_threat_model.md`）。
  - 規格 `docs/specs/remote-compute.md`（使用者已確認：一對一、跨網段用 Tailscale、資安參考近年事件；切分改成「A 只擷取、B 跑不改動的 Pipeline」；配對改兩邊比對數字）；任務 `tasks/remote-compute.md`。
  - 依賴（使用者同意）：`websockets==17.4`、`cryptography==50.0.2`。
  - T1 `compute` 設定（`config/settings.py`）、T2 `remote/source.py`（PushSource）、T3 `remote/protocol.py`（嚴格訊息驗證、session 設定白名單）、T4 `remote/identity.py`（P-256、CA:FALSE）、T5 `remote/pairing.py`（兩邊比對數字、配對窗口、信任清單存完整憑證）、T6 `remote/server.py`（雙向 TLS 1.3、私有網段、拒絕 Origin、關壓縮、一對一 busy、接現有 Pipeline）。
  - `engine/events.py`：`subtitle_event` 與 metrics 回報從 `engine/server.py` 搬出，本機引擎與運算主機共用。
  - 實驗：CA:FALSE 自簽憑證當信任錨的雙向 TLS（OpenSSL 3.5.9）——已配對可連、未配對與衍生憑證在握手時被拒。

**指令與結果**
- `uv run python -m unittest discover -s tests` → 通過（1759 個，1 個在 Windows 跳過的 POSIX 權限測試），連跑兩次。
- `uv run ruff check` → 通過。
- `uv run python -m unittest tests.test_remote_server` → 15 個通過（真的 TLS、127.0.0.1、假元件），連跑三次。
- `npx tsc`、`npx vitest run`（1454）、`cargo test`（96）、`cargo fmt --check`、`cargo clippy -D warnings` → v0.3.1 發布前通過。
- 各 PR 的 CI 與 v0.3.0、v0.3.1 的 release workflow → 全部通過。
- `ssh alian_server`（Tailscale 100.88.94.10）→ 可連（密碼登入，沒有金鑰）；Ubuntu 24.04、GTX 1080 Ti 11 GB、Ollama 0.40.15、沒有 uv。

**未完成 / 卡住**
- 遠端運算 T7–T12 未做：A 端客戶端、兩端整合與資安測試、引擎指令與 Rust 橋接、介面（13 語）、斷線重連、兩台實機驗收。
- 遠端運算的改動全部未 commit（使用者未要求）。
- alian_server 用密碼登入且開著 X11 轉發；建議改金鑰登入並換密碼（已提醒，使用者未決定）。部署需要先在那台安裝 uv（使用者家目錄）。
- `tasks/todo.md` 是舊的 vad-segmenter 清單，與這次工作無關，未動。

**下一步**
- T7：`remote/client.py`（`RemotePipeline`，不走 proxy、pin 運算主機憑證）＋引擎 `session.start` 在 `compute.mode == "remote"` 時改走遠端、振假名在 A 標。
- T8：127.0.0.1 兩端整合測試與威脅模型的安全測試清單。
- 之後 T9–T10（引擎指令、Tauri 指令只開給主視窗、介面），再部署到 alian_server 做實機驗收。

## 2026-11-20 12:24 — 遠端運算 T7–T15 完成：規格圖＋Fable 審查、A／B 兩端、斷線續接、介面、實機與 GUI 驗收

**完成**
- 規格圖與設計審查：`docs/specs/remote-compute.md` 加「實作設計與規格圖」（元件圖、配對時序、session 時序、A 端狀態、斷線續接、B 的兩種宿主、T13 契約）；Fable 5.2 唯讀審查 17 項發現全部套用（決定表在規格「設計審查」）。任務清單依審查重拆為 T7–T15，全部勾選。
- Python（`src/kikitori/remote/`）：`link.py`（單一寫入執行緒＋看門狗）、`client.py`（A 端 `RemoteClientSession`，含 60 秒未確認緩衝、退避重連、`session.resume`、8 倍限速補送、依 `utterance_id` 去重）、`server.py` 改寫（session 屬於伺服器、同一台電腦取代舊連線、保留 45 秒、最近 64 則完整字幕補送、`num_tickets = 0`）、`pair_net.py`（B 輸入 A 的數字＋A 確認、`sig_A` 持有私鑰證明；agent 實作）、`sockets.py`、`network.py`、`paths.py`、`host.py`（`kikitori` 選單 →「運算主機」）；`engine/remote_service.py` 與 `engine/server.py`（`remote.*` 方法、遠端 session、GPU 使用權共用）；`config` 新增 `compute.listen_host`。
- Rust（agent）：8 個 `remote_*` 指令只進 `main.json`、`engine://remote` 事件只送主視窗、`metrics.rtt_ms`；UI（agent）：`app/src/lib/remote.ts`、左側欄「運算位置」、設定頁兩個區段、配對對話框、重新連線提示與「改用這台電腦處理」、13 種語言。
- 實驗工具：`experiments/remote_compute/menu.py`（配對、實機量延遲、斷線轉送、B 本機基準）、`wav_engine.py`、`gui_cdp.mjs`。
- 修掉的根本原因：Windows socket handle 重用（讀取執行緒還活著就關 socket）、TLS 1.3 session ticket 讓兩條執行緒同時用同一個 SSL 物件（偶發握手逾時與 segfault）、B 端 `Pipeline.start/stop` 競態、結束後仍送回呼、`open_host()` 沒 `load()` 就寫入設定、Windows `monotonic` 解析度讓 RTT 變 0、B 終端機卡在已結束的配對、code-reviewer（haiku）的 10 項發現。

**指令與結果**
- `uv run python -m unittest discover -s tests` → Windows 1871 個 OK（skipped=1）；alian_server（Linux）1866 個 OK（skipped=2，那次在本機最後修正之前）。
- remote 相關 6 個測試組連跑 10 次 → 10 次全過（修掉 handle 重用與 session ticket 之後）。
- `uv run ruff check`、`ruff format --check` → 通過；`npx tsc --noEmit` → 通過；`npx vitest run` → 39 檔 1505 個通過；`cargo fmt --check`、`cargo clippy --all-targets -- -D warnings`、`cargo test` → 106 個通過。
- 實機（A＝這台 Windows、B＝alian_server GTX 1080 Ti）：端到端中位數 B 本機 1935 ms／區網 1941 ms（+6）／Tailscale 1973 ms（+38）；斷線 5 秒（無感）、15 秒（重連續接、13 句不重複）、40 秒（`remote_lost`）；GUI 驗收 `gui_cdp.mjs` ALL PASS（13 句、延遲 3 ms）。

**未完成 / 卡住**
- 全部改動尚未 commit（使用者沒有要求）。`app/src-tauri/permissions/autogenerated/*.toml` 顯示為修改，只是 tauri-build 重新產生時的換行差異。
- 小問題（不擋功能，之後再修）：遠端模式下頂端仍顯示本機的「有更適合這台電腦的組合」建議；遠端模式下說話者選單仍可能在 A 下載說話者模型（實際在 B 跑）；A 的連線埠與 B 的監聽埠共用 `compute.port`；翻譯 chunk 超過 vite 的 1024 kB 警告上限（建置照常成功）；`remote.identity_reset`（重建本機憑證）這版沒做。
- 實驗時曾誤用 `pkill -x sleep`（server 上屬於 user 的其他 sleep 也可能被結束），之後改用 PID 檔。alian_server 的設定曾被無介面主機的 bug 蓋成預設值，ASR 設定已依先前讀到的值還原（large-v3-turbo／large-v3、cuda、int8）；`subtitle`／`ui` 兩段若原本有內容已遺失。

**下一步**
- 使用者決定是否 commit／開 PR；之後修上面的小問題。

## 2026-11-20 20:45 — 遠端運算：角色三選一（白話）、五個小問題、審查修正

**完成**
- 左側欄「運算位置」改成三選一：只用這台電腦／用另一台電腦處理／讓別台電腦用這台處理（使用者要求非資工背景也看得懂）。選第三個時只有一個位址就自動用、多個時就地問「要讓哪個網路上的電腦連進來？」並標「這個網路（家裡或公司）」「Tailscale（不在同一個地方也能連）」；`local_addresses()` 有實際路由的位址時只列它們（排除 fe80::、VirtualBox／WSL 虛擬網卡），這台從 8 個減成 2 個。UI 與 Rust 由 agent 實作（`app/src/lib/remote.ts`、`main.ts`、`index.html`、`main.css`、13 個 locale、`commands.rs`）。
- 原本五個小問題：遠端模式不再顯示本機推薦組合與 GPU 元件提醒；遠端模式選說話者標籤不在 A 下載模型（無介面主機「準備模型」一律備好說話者模型）；新增 `compute.listen_port`（舊檔沿用 `port`，65535 時用預設）；vite 每個語系各自一個 chunk（無大小警告）；`remote.identity_reset`＋Rust `remote_identity_reset`＋設定頁按鈕＋無介面主機選單「6) 重建這台電腦的身分」（返回改成 7）。
- `remote.server_set {enabled: true}` 同時把 `mode` 設成 local；遠端字幕進行中回 `session_active`。
- code-reviewer（haiku）審查後修正：**`_EngineDriver` 沒讀設定檔就 `settings.apply_recommendation`，「準備模型」會把整份設定蓋成預設值**（alian_server 設定被蓋掉的真正原因，回歸測試已加）；重建身分改成先清配對清單再換身分；身分資料夾 OSError 不再讓 `remote.status`／引擎啟動失敗；有電腦正在用這台時不重建身分；引擎擋 `0.0.0.0`／`::`／公用位址當監聽位址；Rust 埠上限 65534；`_remote_active` 改名 `_serving_peer`；TS 參數方向統一。

**指令與結果**
- `uv run python -m unittest discover -s tests` → 1888 個 OK（skipped=1）；`uv run ruff check`、`ruff format --check` → 通過。
- `npx tsc --noEmit` → 通過；`npx vitest run` → 39 檔 1512 個通過；`npm run build` → 無 chunk 大小警告。
- `cargo fmt --check`、`cargo clippy --all-targets -- -D warnings` → 通過；`cargo test` → 106 個通過。
- 正式版建置後以 CDP 截圖驗證三種角色（本機、未設定的遠端、挑位址、提供運算中、停止）。

**未完成 / 卡住**
- 全部改動尚未 commit。
- 審查列為「待確認」未處理：`TrustStore._load` 讀檔 OSError 時回空清單，之後 `add()` 會覆寫掉所有配對（既有程式，不在這次範圍）；GUI 引擎與 CLI 主機同時跑時，CLI 重建身分不會通知 GUI 引擎。
- 遠端模式說話者標籤在 B 實機上還沒跑過（B 沒有模型時只會不標，字幕照常）。

**下一步**
- 使用者決定 commit／PR；之後可在 alian_server 重跑一次「準備模型」確認設定不再被蓋掉。
