# 任務清單：遠端運算（一對一）

> 規格：`docs/specs/remote-compute.md`（T7 之後依 2026-11-20 設計審查重新拆分）；研究：`reports/kikitori 遠端運算架構.md`、
> `research_notes/kikitori 遠端運算架構/security_advisories_threat_model.md`（資安威脅模型）。
> 每個任務的共同驗證：`uv run python -m unittest discover -s tests`、`uv run ruff check`；動到 app 時加 `npx tsc`、`npx vitest run`、`cargo test`／`fmt`／`clippy`。
> 新模組放 `src/kikitori/remote/`（A 端客戶端與 B 端伺服器共用協定）。

## 階段 0：不碰網路的基礎

- [x] T1 `compute` 設定（mode、host、port、server_enabled）：驗證、預設、round-trip（UI 型別與解析併入 T10）
- [x] T2 `PushSource`：執行緒安全、有上限的音訊佇列（`AudioSource`），供 B 端 pipeline 讀；溢位、停止、逾時測試
- [x] T3 協定模組 `remote/protocol.py`：JSON 訊息（嚴格 schema、長度上限、未知欄位拒絕）、音訊二進位訊框（標頭 seq／start_sample、長度與對齊檢查）、版本握手；錯誤路徑測試

### 檢查點：T1–T3 全部測試通過

## 階段 1–2：加密連線與配對（一起完成才給使用者）

- [x] T4 身分與憑證 `remote/identity.py`：產生 ECDSA P-256 自簽憑證與私鑰（只有本人可讀的權限）、SPKI 指紋、載入時驗證
- [x] T5 配對 `remote/pairing.py`：兩邊比對數字（先承諾後公開的亂數＋雙方 SPKI 算出 6 位數，兩邊都確認才成功）、配對埠只開 5 分鐘且一次一個、只在兩邊確認後寫入信任清單（CVE-2024-45407 測試）、衍生憑證被拒、移除後立刻斷線
- [x] T6 B 端伺服器 `remote/server.py`：TLS 1.3、雙向驗證（未配對在 TLS 後立即拒絕）、只收私有網段來源、只接受沒有 Origin 的連線、Host 檢查、關閉壓縮、不送版本標頭、`max_size`、握手與閒置逾時、一對一 `busy`；收到 `session.start` → 以 B 的模型組合建立元件 → `Pipeline(PushSource)` → 事件送回
- [x] T7 連線底層 `remote/link.py`（兩端共用）：單一寫入執行緒＋有上限的送出佇列、看門狗（寫入卡 5 秒或 10 秒沒收到訊息就關 socket）；B 端 `server.py` 改用它（`on_subtitle`／metrics 不在 worker 上碰網路）、同一個 SPKI 的新連線取代舊連線、執行中閒置 10 秒；協定補 `status` 欄位驗證（code 白名單、detail 長度、data 形狀）
- [x] T8 A 端客戶端 `remote/client.py` `RemoteClientSession`：自己建 socket（可中斷）、不走 proxy、以 A 的信任清單（SPKI 為鍵）pin、hello／session.start、轉送 status 與事件（先驗證）、`set_translation`、有上限的停止；錯誤碼 `remote_*`
- [x] T9 引擎 A 端整合：`compute.mode == "remote"` 時 `_load_remote_session`（擷取在 A、振假名在 A、`latency_ms + RTT/2`、換成 A 的 session_id、metrics 來自 B）
- [x] T10 配對連線 `remote/pair_net.py`：配對埠（主埠＋1，WS 強化＋來源檢查、`max_size` 8 KiB）、`pair.commit/nonce/reveal(sig_A)/result/abort`、B 輸入 A 的數字、第二個連線就全部中止、A 使用者確認後才寫入；`ServerPairing` 收到 commit 後才建立
- [x] T11 無介面主機（`kikitori` 選單 →「運算主機」，不另開指令）：互動選單（提供運算、配對、已配對清單、準備模型、選監聽位址），啟動時顯示指紋

### 檢查點：127.0.0.1 兩端整合測試＋資安清單；alian_server 實機跑通一場（A 用實驗腳本＋音檔）

- [x] T12 斷線續接：B 的 session 移到 `RemoteServer` 層、保留 45 秒、最近 64 則完整字幕環狀緩衝、`session.resume {session_id, last_final_id}`／`resumed`／`resume_failed`、`seq` 每條連線重設、A 60 秒未確認緩衝與 8 倍限速補送、`status data.reconnecting`
- [x] T13 引擎方法 `remote.*`（見規格表）＋GUI 宿主的 GPU 使用權（`_remote_active`、loader／釋放中、模型操作視為使用中、shutdown 停伺服器）＋Rust 命令與 `remote` 事件驗證（只進 `main.json`）
- [x] T14 介面：A 的左側欄「運算位置」、設定頁「遠端運算」（位址、配對、數字顯示與確認、已配對主機）；B 的「提供運算給其他電腦」、輸入數字、已配對電腦、目前連線；13 種語言

## 驗收

- [x] T15 GUI：兩個 app 實例配對並跑一場（CDP）；實機：Windows（A）＋ alian_server（B，`kikitori-server`）在區網與 Tailscale 量 RTT 與延遲、拔網路 5 秒（續接）與 40 秒（`remote_lost`），寫進規格的驗收結果
