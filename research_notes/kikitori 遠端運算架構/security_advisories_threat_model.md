# 遠端運算模式：近期安全公告、威脅模型與必做清單

> 研究日期：2026-11-09。範圍：[docs/specs/remote-compute.md](../../docs/specs/remote-compute.md) 的 A（擷取＋字幕）↔ B（Whisper＋本機 Ollama）設計。
> 延續 [security_pairing_resilience.md](security_pairing_resilience.md) 與 [reports/kikitori 遠端運算架構.md](../../reports/kikitori%20遠端運算架構.md)；那兩份已涵蓋的內容（Syncthing／Moonlight 配對形狀、Sunshine CVE-2024-45407、Ollama 暴露掃描數字、OWASP pinning 立場）這裡只引用，不重述。
> 來源限 NVD／MITRE／OSV、GitHub Security Advisories（GHSA）、廠商公告、知名資安公司與 OWASP。標「二手」的項目只找到彙整站或新聞轉述，使用前要再核對。

---

## 0. 結論先講（五件事）

1. **規格裡的配對設計有一個必須改的洞。** 「用 6 位數配對碼對兩邊憑證指紋做 HMAC 互相確認」在配對當下雙方還沒 pin 住對方，主動中間人可以拿到其中一方算出的 HMAC，**離線暴力試完 10⁶ 個碼**（一般電腦不到一秒），再用正確的碼分別和 A、B 完成配對。Sunshine 的 CVE-2024-51738 就是這個類型：中間人「can brute-force the PIN offline before pairing finishes」。TLS 1.3 規格本身也寫明低熵 PSK 會被字典攻擊。對策：改用 PAKE（SPAKE2），或改成「兩邊顯示數字、使用者比對確認」的 numeric comparison；不要自己發明 HMAC 承諾流程。詳見 §3.3。
2. **自架 AI 伺服器的事故，根因幾乎都是「把管理功能（下載模型、寫檔、執行工作）開放在沒有驗證的網路介面上」**：Ollama Probllama（CVE-2024-37032）、Ray ShadowRay（CVE-2023-48022）、Langflow（CVE-2025-3248，已列 CISA KEV）、Ollama CVE-2025-63389。kikitori 的 stdio 引擎正好有 `models.pull_translation`（會呼叫 Ollama `/api/pull`，Probllama 的入口）、`models.delete`、`settings.set`。**`BackendServer` 不能沿用 stdio 的指令分派表**，只能開放 `hello`／`session.*`／`utterance.*`／音訊／`ping` 這幾種訊息。
3. **瀏覽器是打本機與區網服務的跳板**：Ollama DNS rebinding（CVE-2024-28224）、MCP Inspector（CVE-2025-49596）、Cline Kanban 與 OpenClaw 的 WebSocket 劫持。雙向 TLS 在正式埠上基本可擋住瀏覽器（瀏覽器沒有被 pin 的用戶端憑證），但**階段 1 的開發者模式（明文 `ws://`）與配對用的監聽器擋不住**，所以要另外檢查 `Origin`／`Host`、預設只綁 127.0.0.1，並且不讓 loopback 免除限速或自動信任（OpenClaw 的錯誤）。
4. **`websockets` 的預設值不適合這個用途**：預設 `compression="deflate"`、`max_size` 1 MiB、`server_header` 會洩漏版本。要設 `compression=None`、依音訊大小調小 `max_size`，並且自己做每個 session 的速率與總量上限。專案目前固定的 `websockets==17.4`、`cryptography==50.0.2`、`tauri =2.12.5` 都已包含本文列出的修補。
5. **反方向也要防：B 被入侵或是惡意的時候，會反過來攻擊 A。** Open WebUI 的 CVE-2025-64496 就是「使用者連到惡意模型伺服器 → 伺服器送來的事件在用戶端被當成程式碼執行」。A 必須把 B 回傳的字幕當成不可信輸入：做 schema 驗證、限制長度、只用 DOM 文字節點顯示（專案現有的 `app/src/lib/dom.ts` 已經不用 `innerHTML`，要保持並加測試），而且 B 不能觸發 A 的任何指令。

---

## 1. 資產、信任邊界與攻擊者

### 1.1 資產

| 資產 | 在哪裡 | 洩漏或被濫用的後果 |
|---|---|---|
| 麥克風／系統音訊（可能含私人對話） | A 擷取 → 網路 → B 記憶體 | 隱私外洩 |
| 字幕文字、術語表、原文校正、本場資訊 | A↔B | 隱私外洩；B→A 方向可能被拿來注入 |
| 兩端私鑰、已配對清單（`compute/`） | 兩端設定資料夾 | 冒充、繞過配對 |
| B 的 GPU 與 Ollama、B 的翻譯 API 金鑰（`translation.api_key_env`） | B | 算力被盜用；金鑰外洩 |
| B 的檔案系統（模型、設定） | B | 被寫檔就可能 RCE（Probllama） |
| A 的 webview IPC（主視窗能下載模型、改設定、開資料夾） | A 的 Tauri | XSS 後可呼叫主視窗指令 |

### 1.2 信任邊界

```
A: webview(main/overlay) ─IPC─ Rust ─stdio─ Python engine ──[網路：wss, mTLS]── B: Python engine ─HTTP─ Ollama(127.0.0.1)
                                                                                   └─stdio─ Rust ─IPC─ webview(main)
```

網路這一段是新的邊界，另外有兩條既有邊界因此多了新的輸入來源：A 的 webview 會顯示 B 產生的文字；B 的 Ollama 會收到經由 B 引擎轉送、源頭在 A 的文字（術語表、本場資訊）。

### 1.3 攻擊者

| 代號 | 攻擊者 | 能力 |
|---|---|---|
| T1 | 同一個區網的其他裝置（咖啡店、宿舍、公司 Wi-Fi） | 掃描連接埠、連線；可透過 ARP spoofing 等手法當主動中間人 |
| T2 | 網際網路掃描器 | 只有在使用者做了 port forwarding 或用了 Funnel 之類功能時才碰得到 |
| T3 | A 或 B 上瀏覽器開啟的惡意網頁 | 跨站 WebSocket、DNS rebinding、對 `0.0.0.0`／區網 IP 發請求 |
| T4 | 同一個 tailnet 的其他節點、共用節點 | 能直接連到 B 的 Tailscale IP |
| T5 | 惡意或被入侵的 B | 對 A 送任意訊息 |
| T6 | 已被移除配對的舊裝置 | 仍持有舊私鑰 |
| T7 | B 上的其他本機使用者或程式 | 讀取金鑰檔、連 127.0.0.1 |

---

## 2. 近期公告逐類整理：發生了什麼、根因、對 kikitori 的教訓

### 2.1 自架 AI／推論伺服器：沒有驗證，加上危險的管理端點

| 事件 | 根因 | 教訓 |
|---|---|---|
| **Ollama CVE-2024-37032「Probllama」**（Wiz，2024-06；修正 0.1.34） | `/api/pull` 從私有 registry 下載時，manifest 的 `digest` 欄位沒有驗證就拿來組檔案路徑，造成任意寫檔，再接成 RCE。Wiz 指出「Ollama does not support authentication out-of-the-box」，Docker 部署「runs with root privileges and listens on 0.0.0.0 by default」 — [Wiz](https://www.wiz.io/blog/probllama-ollama-vulnerability-cve-2024-37032) | 遠端來的字串絕不能進入檔案路徑或 Ollama 的模型名稱。**遠端不能觸發任何下載、拉模型或刪除。** |
| **Ollama CVE-2024-39720／39721／39722（＋39719）**（Oligo，2024-11） | `/api/create` 越界讀取造成 DoS；拿 `/dev/random` 當輸入耗盡資源；`/api/push` 路徑穿越可以探測檔案是否存在。另有兩個被維護者 dispute、沒有 CVE 的問題：從不可信來源 pull 造成模型投毒、push 到不可信目標造成模型被竊 — [Oligo](https://oligo.security/blog/more-models-more-probllms)、[The Hacker News](https://thehackernews.com/2024/11/critical-flaws-in-ollama-ai-framework.html) | 管理端點即使「只是讀取」也會洩漏資訊。維護者 dispute 的問題通常不會有修補，只能靠不暴露來防。 |
| **Ollama CVE-2025-63389 / GHSA-f6mr-38g8-39rg**（2025-12，CVSS 4.0 9.3） | 多個 API 端點缺少驗證，可以遠端做模型管理操作；結構化資料列出的受影響範圍到 0.13.5，**沒有列出修補版本** — [OSV](https://osv.dev/vulnerability/GHSA-f6mr-38g8-39rg) | Ollama 的設計前提就是「只給本機用」。B 上的 Ollama 必須維持只聽 127.0.0.1，而且 kikitori 不能變成它的對外代理。 |
| Ollama Windows 更新程式路徑穿越 CVE-2026-42249（二手：[The Hacker News](https://thehackernews.com/2026/05/ollama-out-of-bounds-read-vulnerability.html)） | 用 HTTP 回應標頭組出暫存路徑 | 一般使用者 B 上的 Ollama 也要持續更新；kikitori 可以在 B 的設定頁顯示 Ollama 版本。 |
| **Ray「ShadowRay」CVE-2023-48022**（Oligo，2024-03 起遭利用；2025-11 出現 ShadowRay 2.0） | Jobs API 沒有驗證，可以直接提交任意程式。廠商的立場是 Ray「is not intended for use outside of a strictly controlled network environment」，所以把它標為 disputed；2.52.0 起才提供 token 驗證 — [MITRE CVE record](https://cveawg.mitre.org/api/cve/CVE-2023-48022)、[Oligo ShadowRay](https://oligo.security/blog/shadowray-attack-ai-workloads-actively-exploited-in-the-wild)、[Oligo ShadowRay 2.0](https://oligo.security/blog/shadowray-2-0-attackers-turn-ai-against-itself-in-global-campaign-that-hijacks-ai-into-self-propagating-botnet) | 「只在信任網路使用」不是安全模型。使用者一定會把東西開到不該開的地方，而且掃描工具不會標出被 dispute 的 CVE。**kikitori 不能用「區網內」當作授權依據。** |
| **Langflow CVE-2025-3248**（CVSS 9.8；2025-05-05 列入 CISA KEV；修正 1.3.0） | `/api/v1/validate/code` 沒有驗證，而且會執行傳進來的程式碼 — [Help Net Security](https://www.helpnetsecurity.com/2025/05/06/langflow-cve-2025-3248-exploited/)、[Recorded Future](https://www.recordedfuture.com/blog/langflow-cve-2025-3248) | 不能有任何會 `eval`／`exec` 或反序列化的端點。 |
| **ComfyUI CVE-2026-68771**（CVSS 9.3，2026-07） | 內建節點對不可信檔案呼叫 `torch.load` 時沒有加 `weights_only=True`（pickle）；上傳端點又沒有驗證 — [OSV](https://osv.dev/vulnerability/CVE-2026-68771) | 協定只能用 JSON 與固定格式的二進位資料，**不能用 pickle、`marshal`、`eval`**；B 不能從網路接收任何檔案。 |
| **Gradio CVE-2024-1561**（CVSS 7.5；修正 4.13.0） | `/component_server`「improperly allows the invocation of any method on a `Component` class」，可以讀取任意檔案 — [MITRE CVE record](https://cveawg.mitre.org/api/cve/CVE-2024-1561) | 不能「用訊息裡的字串去找要呼叫的方法」（例如 `getattr(self, msg["type"])`）。分派表必須是寫死的允許清單。 |

**對 kikitori 的具體對策**

- `BackendServer` 自己定義一份**最小的訊息允許清單**：`hello`、`session.start`、`session.set_translation`、`session.stop`、`utterance.start`、`utterance.end`、二進位音訊、`ping`／`pong`、`resume`。**不能重用 `engine/server.py` 的分派表**，因為那裡有 `models.pull_translation`、`models.download`、`models.delete`、`settings.set`、`open_output_folder` 等指令。
- `session.start` 的欄位全部用允許清單驗證：語言代碼只能是 `languages.list` 裡的值，布林值必須是 bool，術語表與原文校正限制條數與每條長度，本場資訊限制字數，說話者標籤限制數值範圍。**不能接受 `translation.base_url`、模型名稱、模型路徑、`api_key_env`。** B 依自己的設定選模型（規格已寫）。如果開放 base_url，已配對但被入侵的 A 就能讓 B 把翻譯 API 金鑰送到攻擊者的伺服器（SSRF＋金鑰外洩）。
- B 的引擎呼叫 Ollama 時，模型名稱一律取自 B 的設定，不取自網路訊息。
- 遠端模式下，B 不能因為 A 的要求而下載或更新任何模型；缺模型時回 `session.started` 錯誤，讓 B 的使用者在 B 上處理。

### 2.2 瀏覽器當跳板：CSWSH、DNS rebinding、0.0.0.0 Day

| 事件 | 根因 | 教訓 |
|---|---|---|
| **Ollama CVE-2024-28224**（NCC Group，2024-04；修正 0.1.29） | DNS rebinding 讓遠端網頁可以存取本機的完整 API，能「chat with a model, delete a model, or exhaust resources」 — [OSV](https://api.osv.dev/v1/vulns/CVE-2024-28224)、[NCC Group](https://www.nccgroup.com/research/technical-advisory-ollama-dns-rebinding-attack-cve-2024-28224/)。NCC 建議所有服務（包括 localhost）都用 TLS。 | **只靠綁定 127.0.0.1 不夠**，HTTP／WebSocket 伺服器要檢查 `Host`。 |
| **MCP Inspector CVE-2025-49596**（Oligo，CVSS 9.4；修正 0.14.1） | 本機 proxy 沒有驗證，結合瀏覽器的「0.0.0.0 Day」與 CSRF，使用者只要開了惡意網頁就會被 RCE。修補內容：client 與 proxy 之間加 session token、驗證 `Host` 與 `Origin`、預設擋下 DNS rebinding 與 CSRF — [Oligo](https://www.oligo.security/blog/critical-rce-vulnerability-in-anthropic-mcp-inspector-cve-2025-49596)、[Tenable](https://www.tenable.com/blog/how-tenable-research-discovered-a-critical-remote-code-execution-vulnerability-on-anthropic) | 這正是 kikitori 階段 1「開發者模式明文連線」的風險樣板：**開發工具也會被瀏覽器打穿。** |
| **「0.0.0.0 Day」**（Oligo，2024-08） | 瀏覽器允許公開網站對 `0.0.0.0` 發請求，在 macOS／Linux 上會打到本機服務；Oligo 用它串 ShadowRay 示範攻擊 — [The Hacker News](https://thehackernews.com/2024/08/0000-day-18-year-old-browser.html)、[Help Net Security](https://www.helpnetsecurity.com/2024/08/09/0-0-0-0-day-vulnerability-affects-chrome-safari-and-firefox/) | 監聽 `0.0.0.0` 也等於讓本機瀏覽器碰得到。 |
| **Cline Kanban WebSocket 劫持**（Oasis，2026-05；修正 0.1.66；CVE-2026-44211 為二手資料） | 本機 WebSocket「accepts connections from anywhere」：沒有 Origin 檢查也沒有驗證，任何網頁都能讀取工作區串流並把指令注入 agent 終端機。文中強調 WebSocket「are not subject to the same-origin policy restrictions enforced by CORS」 — [Oasis Security](https://www.oasis.security/blog/cline-kanban-websocket-hijack) | 典型 CSWSH：瀏覽器不會替你擋 WebSocket。 |
| **OpenClaw「ClawJacked」**（Oasis，2026-02） | Origin 驗證不完整、**信任 loopback 流量、對 localhost 不限速**，三者串起來，網頁就能拿到本機 agent gateway 的已驗證控制權 — [Oasis Security](https://www.oasis.security/resources/cross-origin-websocket-exploitation-openclaw)、[CSA research note](https://labs.cloudsecurityalliance.org/research/csa-research-note-clawjacked-websocket-local-agent-hijack-20/) | **配對碼嘗試次數限制不能對 127.0.0.1 放寬，也不能因為來源是 loopback 就自動核准配對。** |
| Chrome Local Network Access（Chrome 142 起，2025-10） | 公開網站存取 loopback 或私有位址前，會先跳權限提示；WebSocket 也在管制範圍內 — [Chrome for Developers](https://developer.chrome.com/blog/local-network-access) | 這是額外的一層，但不能依賴：其他瀏覽器、舊版、使用者按了允許、企業允許清單都會讓它失效。 |

OWASP WebSocket Security Cheat Sheet 的對應建議：「Validate the `Origin` header on every handshake」，使用允許清單，不要用萬用字元或子字串比對；把所有訊息當成不可信輸入；限制 payload 大小（「typically 64KB or less」）；限速；設定閒置逾時與心跳；「Disable `permessage-deflate` unless you need it」；log 不能記錄完整訊息內容、token 或個人資料 — [OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)。

**對 kikitori 的具體對策**

- A 與 B 之間是 Python 對 Python，**正常的用戶端不會送 `Origin` 標頭**。所以伺服器設 `origins=[None]`：`websockets` 的文件說明「Include `None` in the list if the lack of an origin is acceptable」，字串則是完全比對 — [websockets server API](https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html)。這樣**所有帶 `Origin` 的瀏覽器連線都會在握手時被拒**（websockets 回 403）。這比維護允許清單更簡單，也更嚴格。
- 用 `process_request` 檢查 `Host`：只接受 IP literal（B 監聽的位址）或使用者設定的主機名稱（例如 MagicDNS 名稱）。DNS rebinding 的 `Host` 會是攻擊者的網域。在正式埠，mTLS 已經會擋掉這種連線，這一層主要是給開發者模式與配對監聽器用。
- 正式埠用雙向 TLS（`CERT_REQUIRED`）：瀏覽器沒有被 pin 的用戶端憑證，連 TLS 握手都過不了；而且 B 的憑證是自簽的，瀏覽器原本就不會信任。
- 配對監聽器只在使用者按下「允許新的電腦連線」後開 5 分鐘，同樣要檢查 Origin 與 Host，並套用嘗試次數限制，**loopback 不例外**。
- 階段 1 的明文模式：只能在開發者旗標開啟時使用，**只綁 127.0.0.1**，Origin 與 Host 檢查照做，並要求一個隨機 token（每次啟動產生，經 stdio 傳給本機前端，不放在 URL query）。正式版建置要有測試確認這個模式被移除或無法開啟。

### 2.3 配對協定：Sunshine／Moonlight 系列（2024–2026）

CVE-2024-45407（失敗的配對先把憑證寫進信任清單）前一份筆記已經寫過。之後 Sunshine 又陸續公布了幾個和 kikitori 設計直接相關的問題：

| 公告 | 根因 | 教訓 |
|---|---|---|
| **CVE-2024-51738 / GHSA-3hrw-xv8h-9499**（2025-01；Sunshine ≤ 0.23.1；修正 2025.118.151840；CVSS 4.0 7.7） | 配對協定「does not validate request order」。能觀察並修改流量的中間人「can brute-force the PIN offline before pairing finishes」，再重放前面的步驟、偽造資料，讓自己的憑證取代合法用戶端。步驟順序錯亂也能讓 Sunshine 崩潰，不需要中間人位置 — [GHSA-3hrw-xv8h-9499](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-3hrw-xv8h-9499)、[NVD](https://nvd.nist.gov/vuln/detail/CVE-2024-51738) | **短 PIN 在任何會讓攻擊者拿到「PIN 的確定性函數」的協定裡，都會被離線暴力破解。** 配對必須是嚴格的狀態機，順序不對就中止。見 §3.3。 |
| **GHSA-36ff-frg7-492f**（2026-09；v0.1.0 起所有版本；修正 v2026.906.222525；CVSS 7.5） | 使用者輸入的 PIN 沒有綁定到「他想核准的那一個」待配對請求。有多個請求同時待處理時，核准可能套用到攻擊者的 session。修正內容：核准時要帶該請求的隨機 ID、顯示用戶端名稱與來源位址、伺服器端自己驗證、拒絕重複 ID、限制待處理數量並讓未完成的 session 過期 — [GHSA-36ff-frg7-492f](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-36ff-frg7-492f) | **同一時間最多只能有一個待配對 session。** 第二個連線進來就拒絕，或是讓配對碼立刻作廢。B 的畫面要顯示「正在配對的電腦：名稱／IP」。 |
| **CVE-2026-32253 / GHSA-ph75-mgxh-mv57**（2026-05；CVSS 9.8） | 自訂的 OpenSSL 驗證 callback 把「找不到簽發者」「尚未生效」「已過期」三種錯誤當成成功，所以攻擊者用自己的 root 簽的憑證就能通過 — [GHSA-ph75-mgxh-mv57](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-ph75-mgxh-mv57) | 不要寫寬鬆的驗證 callback。Python 的 `ssl` 沒有開放驗證 callback，這反而是好事；pin 的比對一律用**完全相等**。 |
| **CVE-2026-111044 / GHSA-6jvv-jqr7-m6m3**（2026-09；CVSS 7.6） | 伺服器接受「任何能串到已配對憑證」的用戶端憑證，沒有要求是「the exact enabled paired identity」，比對時也沒有正規化 PEM 編碼。結果已經被停用的用戶端只要還持有私鑰，就能用自己簽出的衍生憑證繞過撤銷 — [GHSA-6jvv-jqr7-m6m3](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-6jvv-jqr7-m6m3) | 如果把已配對的自簽憑證當成 `cafile` 載入，**OpenSSL 會接受由它簽出的子憑證**（除非憑證標記 `CA:FALSE`）。所以：(1) 產生憑證時加 `BasicConstraints(ca=False, critical=True)`；(2) 握手後一定要對**葉憑證**的 SPKI SHA-256 做完全比對，不能只靠鏈驗證；(3) 用 DER／SPKI bytes 比對，不要比 PEM 文字。 |
| GHSA-39hj-fxvw-758m（2025-06，Critical）、GHSA-x97g-h2vp-g2c5（2025-06） | Web UI 全面性的 CSRF 可以注入指令；另有 clickjacking — [Sunshine advisories](https://github.com/LizardByte/Sunshine/security/advisories) | 配對核准、移除裝置等操作只放在 Tauri 主視窗，**不要為了無頭模式另開一個 Web 管理頁**。如果以後真的要做無頭模式，再依 OWASP 做 CSRF 防護。 |
| GHSA-c428-87f8-rrv5、GHSA-26q2-58j6-qmvv（2026-09） | 未驗證的短封包就能造成 DoS；輸入封包驗證不足導致 heap 越界寫入 | 二進位音訊幀要先驗長度再解析，而且**只有通過驗證的 session 才處理二進位幀**。 |

### 2.4 WebSocket 伺服器：`websockets` 與同類函式庫

| 公告 | 內容 | 教訓 |
|---|---|---|
| `websockets` CVE-2018-1000518（修正 5.0） | 解壓縮時沒有套用 `max_size`，造成記憶體耗盡（壓縮炸彈） — [GHSA-6g87-ff9q-v847](https://osv.dev/vulnerability/GHSA-6g87-ff9q-v847) | permessage-deflate 是 DoS 面。 |
| `websockets` 17.4（2026-10-30） | 安全修補：`recv()` 拒絕被切成超過 4096 個 frame 的訊息，先前版本「vulnerable to denial of service by memory exhaustion」（CVE 編號待定）；可以用 `WEBSOCKETS_MAX_FRAGMENTS` 調整 — [websockets changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html) | 專案已固定 `websockets==17.4`。**不要用 `recv_streaming()`**，changelog 說它不受這個上限保護。 |
| `websockets` 16.1、17.0 | 拒絕格式錯誤的分段壓縮訊息；握手標頭超過上限時回 414／431 — [changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html) | 握手階段的限制已經內建，預設值見 [websockets security](https://websockets.readthedocs.io/en/stable/topics/security.html)。 |
| `websockets` CVE-2021-33880（修正 9.1） | HTTP Basic Auth 密碼比對有時序攻擊 | 見 §2.7。 |
| 同類函式庫：aiohttp CVE-2026-54274（不完整的大 frame 造成 DoS）、Autobahn CVE-2026-77528（解壓縮炸彈）（二手：[SentinelOne](https://www.sentinelone.com/vulnerability-database/cve-2026-54274/)、[SentinelOne](https://www.sentinelone.com/vulnerability-database/cve-2026-77528/)） | 2026 年仍有新的同類 DoS | 這類問題會一直出現，所以應用層也要自己設上限，不能只靠函式庫。 |

`websockets.asyncio.server.serve()` 的預設值：`compression='deflate'`、`max_size=1048576`、`max_queue=16`、`write_limit=32768`、`open_timeout=10`、`ping_interval=20`、`ping_timeout=20`、`close_timeout=10`、`server_header='Python/x websockets/x'` — [websockets server API](https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html)。安全頁另外說明：壓縮訊息最多佔 128 MiB 記憶體（約 1000 倍放大）；每條連線約 70 KiB；建議在進到 websockets 之前就先拒絕可疑連線 — [websockets security](https://websockets.readthedocs.io/en/stable/topics/security.html)。

### 2.5 TLS 函式庫：`cryptography` 與 OpenSSL

| 公告 | 內容 | 與專案的關係 |
|---|---|---|
| `cryptography` GHSA-537c-gmf6-5ccf（2026-06，High 7.5） | PyPI wheel 內附的 OpenSSL 有漏洞，影響 `< 48.0.1` — [GHSA](https://github.com/advisories/GHSA-537c-gmf6-5ccf) | 已固定 50.0.2，不受影響 |
| `cryptography` GHSA-jwv3-5hgf-82ww（2026-07） | 重複的自簽中繼憑證會讓路徑建構呈指數成長；影響 `<= 48.0.0` | 50.0.2 已修 |
| `cryptography` GHSA-g6cj-pr64-35w5（2026-07） | PKCS#7 解密的 Bleichenbacher oracle；修正 50.0.0 | 不使用 PKCS#7，而且已修 |
| `cryptography` GHSA-r6ph-v2qm-q3c2（2026-02，High） | 只影響 SECT 曲線的子群驗證缺失；修正 46.0.5 | 使用 P-256，不受影響 — [pyca advisories](https://github.com/pyca/cryptography/security/advisories) |
| OpenSSL CVE-2024-12797（3.2–3.4） | RFC 7250 raw public keys 模式下，伺服器驗證失敗時握手不會中止，可能被中間人攻擊 — [Red Hat](https://access.redhat.com/security/cve/CVE-2024-12797)、[Ubuntu](https://ubuntu.com/security/CVE-2024-12797) | **不要用 raw public keys**，用自簽 X.509 憑證（Python `ssl` 也沒有開放 RPK） |
| OpenSSL 2026-06-09 公告（18 個 CVE） | 大多在 CMS／PKCS7／QUIC；和 TLS-over-TCP 有關的是 OCSP stapling（CVE-2026-35188）與「OCSP＋partial chain」同時開啟的組合（CVE-2026-42765） — [OpenSSL secadv 20260609](https://openssl-library.org/news/secadv/20260609.txt) | 不要開 OCSP，也不需要 `VERIFY_X509_PARTIAL_CHAIN` |

**容易忽略的一點**：TLS 連線用的是 Python 標準庫 `ssl`，它連結的是**直譯器自己的 OpenSSL**，不是 `cryptography` wheel 內附的那一份。專案用 Python 3.12，而 3.12 已進入 security-only 階段，python.org 只發 source-only 版本，3.12.10 是最後一個附二進位安裝檔的版本 — [Python 3.12.15 release page](https://www.python.org/downloads/release/python-31215/)。引擎是用 PyInstaller 打包，所以實際帶的 OpenSSL 取決於 CI 用的直譯器版本。建議在打包測試中記錄並檢查 `ssl.OPENSSL_VERSION`，並評估升到 3.13 以上（另外決定）。

### 2.6 Tailscale 安全公告：依賴 tailnet 的應用要注意什麼

完整清單見 [Tailscale security bulletins](https://tailscale.com/security-bulletins)。和「把 B 放在 tailnet 上」有關的幾則：

| 公告 | 內容 | 教訓 |
|---|---|---|
| **TS-2024-005**（2024-05；修正 1.66.0） | 區網主機可以經由 Linux exit node、subnet router、app connector 連到 tailnet 節點 | **tailnet IP 不等於身分**，不能拿來源 IP 當授權。 |
| TS-2025-004（2025-05） | 用公共 email 網域建立的 tailnet，可能讓互不相關的使用者加入同一個 tailnet | 「在同一個 tailnet」不等於「是我的電腦」。 |
| TS-2025-006（2025-10） | 對外分享的 subnet router 會遺失協定過濾 | ACL 也可能失效。 |
| TS-2025-008（2025-11；修正 1.90.8） | 沒有 statedir 的節點在 Tailnet Lock 下會跳過簽章檢查 | 同上。 |
| **TS-2026-007**（2026-07；修正 1.98.9） | Service IP 在沒有宣告的埠上，可以連到**只綁 loopback 的服務** | 對 B 上「只綁 127.0.0.1 的 Ollama」很重要：**loopback 不是絕對的邊界**。要提醒使用者更新 Tailscale，而且 kikitori 不能把 loopback 當成信任依據。 |
| TS-2026-011（2026-08；修正 1.102.3） | 有 4via6 路由權限的節點能連到 router 的 loopback 與雲端 metadata | 同上。 |
| TS-2026-014／016（2026-10；修正 1.104.4） | Tailscale SSH 轉發與 `tailscale serve` 的 Unix socket 後端可以碰到 root 才能用的 socket | 不要建議使用者用 `tailscale serve`／Funnel 轉發 kikitori 的埠；**Funnel 等於直接開到網際網路**（TS-2024-012 就是 Funnel 被拿來架釣魚頁）。 |
| TS-2025-003（2025-05） | DERP mesh 驗證用了非固定時間的秘密比對 | 見 §2.7。 |

**結論**：Tailscale 負責「連得到、加密」，kikitori 自己的 mTLS＋pin 負責「是不是那台配對過的電腦」。兩層都要有。說明文件要寫：用 Tailscale IP 或 MagicDNS 名稱直接連，不要用 Funnel；Tailscale 要保持更新。

### 2.7 時序攻擊

- `websockets` CVE-2021-33880：Basic Auth 密碼比對有時序洩漏（修正 9.1） — [changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html)。
- Tailscale TS-2025-003：DERP mesh key 用非固定時間比對，Tailscale 因此輪換了秘密 — [Tailscale bulletins](https://tailscale.com/security-bulletins)。
- Python 文件：`hmac.compare_digest` 會「avoiding content-based short circuiting behaviour」，但長度不同時仍可能洩漏型別與長度 — [Python hmac](https://docs.python.org/3/library/hmac.html)。

對策：所有 MAC、token、指紋比對都用 `hmac.compare_digest`，比對前先把兩邊轉成固定長度的 `bytes`（例如 SHA-256 digest）。嘗試次數要在**驗證之前**先檢查並遞增（同一個 asyncio 事件迴圈裡也要注意 `await` 之間的競態），作廢的碼不能再進入比對流程。改用 PAKE 之後，6 位數碼本身不會被直接比對，比較的是 MAC。

### 2.8 反方向：惡意或被入侵的 B 攻擊 A

- **Open WebUI CVE-2025-64496 / GHSA-cm35-v4vp-5xvx**（CVSS 7.3；修正 0.6.35）：前端的 SSE 處理程式把模型伺服器送來的 `execute` 事件當成程式碼，用 `new Function()` 執行，沒有任何驗證或 CSP，結果可以偷走 `localStorage` 裡的 JWT — [GHSA-cm35-v4vp-5xvx](https://github.com/open-webui/open-webui/security/advisories/GHSA-cm35-v4vp-5xvx)。
- kikitori 的對應風險：A 的主視窗擁有 `models.download`、`settings.set`、`transcript.save`、`open_output_folder` 等 IPC 權限。如果 B 回傳的字幕在 A 上能變成 HTML 或腳本，B 就能控制 A。
- 對策：A 的引擎對 B 的每則訊息做 schema 驗證（型別、字串長度、數值範圍、`utterance_id` 必須是 A 開過的），驗證過後才轉換成**現有的** `subtitle` 事件交給 Tauri。B 不能指定事件名稱，也沒有任何訊息會讓 A 執行動作（開網址、寫檔、改設定）。前端維持 `dom.ts` 只用 DOM API、不用 `innerHTML` 的作法，並加測試。

### 2.9 Tauri 端

| 公告 | 內容 | 專案狀態 |
|---|---|---|
| CVE-2024-35222 / GHSA-57fm-592m-34r7 | 遠端來源的 iframe 可以繞過 origin 檢查呼叫 IPC — [NVD](https://nvd.nist.gov/vuln/detail/CVE-2024-35222) | v2 已修 |
| CVE-2026-42184 / GHSA-7gmj-67g7-phm9（2026-05） | `is_local_url()` 只看網域的第一段，在 Windows 上 `http://app.evil.com/` 會被誤判成本機；修正 2.11.1 — [GHSA-7gmj-67g7-phm9](https://github.com/tauri-apps/tauri/security/advisories/GHSA-7gmj-67g7-phm9) | 已固定 `tauri =2.12.5`，不受影響 |
| GHSA-w28w-mhc8-qvjv（2026-09，High） | 內部的 channel fetch 指令不受 ACL 管制，而且用連號 ID，所以其他 webview 可以偷走排隊中的回應；修正 2.11.6 — [GHSA-w28w-mhc8-qvjv](https://github.com/tauri-apps/tauri/security/advisories/GHSA-w28w-mhc8-qvjv) | 2.12.5 不受影響。這則也說明了 **overlay 與 main 之間的隔離是實際存在的攻擊面**。 |

**Tauri 端該做的事**

- 新增的指令（例如 `compute_pair_start`、`compute_pair_submit`、`compute_trusted_list`、`compute_trusted_remove`、`compute_server_set_enabled`、`compute_status`）**只加進 `capabilities/main.json`**，`overlay.json` 不變。加一個測試檢查 overlay 的 capability 裡沒有任何 `compute_*`。
- 維持現在的 CSP（`default-src 'self' ipc: http://ipc.localhost; ...`），**不要為了遠端功能加 `connect-src` 到任何網路位址**。webview 不直接連 B，網路只在 Python 引擎做。不載入遠端頁面，不使用 iframe，`withGlobalTauri` 維持 `false`。
- 配對碼與主機位址在 Rust 端再驗一次（6 位數字、主機名稱或 IP 的語法、埠號 1–65535），然後才交給引擎。
- 開啟配對（B 的「允許新的電腦連線」）與移除裝置只能由主視窗的使用者操作觸發。引擎不能因為網路訊息自行開啟配對模式。
- 配對碼只顯示在 B 的 UI，不寫進 log、診斷資訊或設定檔，也不經由 overlay 事件廣播。
- 「之後」可以考慮：Tauri isolation pattern，把 IPC 呼叫集中驗證。

---

## 3. 對策設計（具體值與流程）

### 3.1 監聽與綁定

- `compute.server_enabled` 預設關閉（規格已寫）。開啟時，**預設不綁 `0.0.0.0`**，而是綁使用者選擇的介面位址。UI 列出這台電腦的私有位址（RFC 1918、Tailscale 的 100.64.0.0/10、IPv6 ULA），使用者勾選。
- 連線進來後先檢查來源 IP：如果不是私有、CGNAT 或 loopback 位址，就直接關閉並記錄。B 被 port forwarding 暴露時，這是第二道防線。可以提供進階選項關掉這項檢查，預設開啟。
- Windows 防火牆提示時，說明只勾「私人網路」。
- B 的 app 關閉時監聽一起關閉（規格：這一版需要開著 app）。

### 3.2 兩個監聽器與 TLS 設定

Python `ssl` 的限制：伺服器在 `CERT_OPTIONAL`／`CERT_REQUIRED` 模式下，「If the client chooses to send a certificate, it is verified. Any verification error immediately aborts the TLS handshake」，所以沒辦法在握手時接受一張還不認識的用戶端憑證；標準庫也沒有 TLS exporter API，`get_channel_binding` 只支援 TLS 1.2 以前的 `tls-unique` — [Python ssl](https://docs.python.org/3.12/library/ssl.html)。因此建議：

- **正式監聽器**（預設 47100）：`PROTOCOL_TLS_SERVER`，`minimum_version = TLSv1_3`，`verify_mode = CERT_REQUIRED`，`load_verify_locations` 載入已配對裝置的憑證。**握手之後**再把葉憑證的 SPKI SHA-256 和記憶體中的 pin 清單做完全比對，不符就關閉。這樣**未配對的用戶端會在 TLS 層被拒**，符合規格的驗收條件。
- **配對監聽器**（例如 47101，可以設定）：只在 B 按下「允許新的電腦連線」後開 5 分鐘。TLS 1.3，伺服器 `CERT_NONE`，A 端也用 `CERT_NONE`，但要記下 B 在握手時出示的憑證：用戶端 `getpeercert(binary_form=True)` 可以拿到 DER。A 的公鑰在 PAKE 驗證過的訊息裡傳送。配對成功、失敗 5 次、或逾時，都立刻關閉這個監聽器。
- 也可以只用一個埠，用 `sni_callback` 切換 context，但比較難測試，不建議。
- 自簽憑證：ECDSA P-256，加上 `BasicConstraints(ca=False, critical=True)`、`KeyUsage(digital_signature)`、SKI／AKI。之後如果升到 Python 3.13，`create_default_context` 會預設開 `VERIFY_X509_STRICT`，這些擴充欄位可以讓憑證通過嚴格檢查。pin 的對象是 SPKI，所以憑證到期重簽時 pin 不變。
- A 端連 B：`CERT_REQUIRED`，`cafile` 是 B 的已 pin 憑證，`check_hostname=False`（用 IP 連，而且有 pin 把關），握手後同樣做 SPKI 完全比對。**不提供「仍要繼續」。**
- 移除裝置：從 pin 清單移除，立刻關閉該裝置現有的連線，然後重建正式監聽器的 `SSLContext`（`load_verify_locations` 只能新增不能移除）。即使還沒重建，應用層的 pin 比對也已經會拒絕它。

### 3.3 配對協定（取代規格中的「配對碼 HMAC 指紋」）

**為什麼原本的設計不安全**：配對時 A 還不知道 B 的憑證，所以中間人 M 可以分別和 A、B 建立 TLS。只要協定裡任何一方送出「配對碼與公開資料的確定性函數」（例如 `HMAC(code, fp_A‖fp_B)`），M 就能離線試完 10⁶ 個碼。不論哪一方先送都一樣：如果 B 先送，M 對 B 假扮成 A 拿到 B 的 HMAC；如果 A 先送，M 對 A 假扮成 B 拿到 A 的 HMAC。試出碼之後，M 再用正確的碼和另一方完成配對。這就是 Sunshine CVE-2024-51738 的模式。RFC 8446 §2.2 也說「Deriving a shared secret from a password or other low-entropy sources is not secure... subject to dictionary attacks」 — [RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)，所以 TLS-PSK 也不是解法。用承諾（commitment）流程自己修補很容易出錯（藍牙 Passkey Entry 就是逐 bit 承諾），OWASP 也提醒不要自己寫驗證邏輯。

**選項 A（建議）：SPAKE2**。PAKE 的性質是：主動攻擊者每次連線只能猜一次，**沒辦法離線字典攻擊**（RFC 9382 的設計目標，前一份筆記已引用 [RFC 9382](https://www.rfc-editor.org/rfc/rfc9382.html)）。在 5 次上限下，攻擊者成功的機率約 5×10⁻⁶。Python 有 `spake2` 0.9（MIT，純 Python，2024-09，維護者 warner／meejah） — [PyPI spake2](https://pypi.org/project/spake2/)。magic-wormhole 就是用短碼加 SPAKE2，碼只能用一次 — [magic-wormhole docs](https://magic-wormhole.readthedocs.io/en/latest/welcome.html)。PyPI 頁面沒有提到正式稽核，而且是純 Python 的橢圓曲線運算，可能不是固定時間；但配對碼只用一次、5 分鐘後作廢，本機側通道的風險可以接受。**新增依賴要先問使用者並固定版本。**

流程草案：
1. B：使用者按下「允許新的電腦連線」→ 用 `secrets.randbelow(10**6)` 產生碼並補零到 6 位 → 開配對監聽器 → 同時只允許一個待配對 session（Sunshine GHSA-36ff 的教訓）。
2. A：連到配對監聽器，記下 B 的 SPKI（`spki_B_seen`）。
3. 交換 SPAKE2 訊息（A 用 `SPAKE2_A`，B 用 `SPAKE2_B`，身分字串固定為 `kikitori-pair-v1-client`／`-server`），雙方導出 `K`。
4. A → B：`{cert_A_der, mac_A}`，其中 `mac_A = HMAC-SHA256(K, "kikitori-pair-v1 A" ‖ spki_B_seen ‖ spki_A ‖ transcript_hash)`。
5. B 用自己的 `spki_B` 與收到的 `spki_A` 重算，用 `compare_digest` 比對。失敗就把嘗試次數加一（到 5 次就作廢碼並關閉監聽器），**不寫任何信任紀錄**。成功時，B **在這個時間點才**把 A 的 SPKI 寫入信任清單（原子寫入），然後回傳 `mac_B = HMAC(K, "... B" ‖ spki_B ‖ spki_A ‖ transcript_hash)`。
6. A 驗證 `mac_B`（用 `spki_B_seen`），成功才 pin B。
7. 中間人如果只轉送 SPAKE2 訊息而沒有猜碼，雙方會得到相同的 `K`，但 A 看到的是 M 的 SPKI，所以 B 驗證 `mac_A` 時一定失敗。如果中間人自己跑 SPAKE2，就只有一次猜測機會。

**選項 B（不加依賴）：numeric comparison**。A 連上配對監聽器後，雙方先做承諾再交換 nonce（A 先送 `H(nonce_A)`，B 送 `nonce_B`，A 再揭露 `nonce_A`），兩邊螢幕顯示 `SAS = trunc6(SHA-256(spki_A‖spki_B‖nonce_A‖nonce_B))`，使用者在 B 和 A 都按「一致」。這是藍牙 LE Numeric Comparison 的形狀，攻擊者成功的機率約 10⁻⁶。缺點是需要兩台螢幕都在眼前、兩邊都要按（規格原本只要在 A 輸入碼），而且承諾的順序仍要經過設計審查。

**選項 C（不建議）**：把碼加長到 ≥ 128 bit（例如 26 個 base32 字元），可以擋離線暴力破解，但使用者很難輸入。

### 3.4 訊息驗證與資源上限（建議值）

音訊是 16 kHz int16，1 秒 = 32,000 B；每 100 ms 一則 = 12 B 標頭 + 3,200 B。

| 項目 | 建議值 | 理由 |
|---|---|---|
| `compression` | `None` | 壓縮炸彈與 CRIME 類攻擊（OWASP、CVE-2018-1000518）；PCM 本來就不太能壓 |
| `max_size` | 64 KiB（二進位最多約 1 秒音訊）；文字訊息另外在應用層檢查 ≤ 256 KiB，依現有術語表與本場資訊的上限決定。websockets 16.0 起 `max_size` 可以分別設定訊息與 fragment 上限 | OWASP 建議 ≤ 64 KB |
| `max_queue` | 維持 16 或更小 | 背壓 |
| `server_header` | `None` 或固定字串，不帶版本 | websockets 預設會洩漏 Python 與套件版本 |
| `open_timeout` | 5 秒；TLS 握手逾時也設 5 秒（`ssl_handshake_timeout`，要以原始碼確認 `serve()` 是否把它轉傳給 `loop.create_server`） | 防 slowloris |
| `hello` 期限 | WebSocket 開啟後 5 秒內沒收到 `hello` 就關閉 | 同上 |
| 心跳 | 應用層每 3 秒 `ping`，10 秒沒回應就斷線（規格）；websockets 自己的 `ping_interval` 可以保留預設值或關掉，避免重複 | |
| 同時連線 | 已驗證的 session 最多 1 個（第二台回 `busy`）；握手中的連線最多 4 條，每個來源 IP 最多 2 條 | 避免握手階段被灌爆 |
| 音訊幀 | payload 長度 > 0、為偶數、≤ 上限；`seq` 必須嚴格遞增；`start_sample` 必須等於「目前已收到的樣本數」（或在 resume 允許的範圍內）；只有在 `utterance.start` 之後、`utterance.end` 之前才接受 | Sunshine 的越界寫入與短封包 DoS |
| 單句長度 | B 端限制每句樣本數上限（例如規格切句器上限的 2 倍） | 記憶體 |
| 速率 | 平常 ≤ 2× 即時；resume 補送時 ≤ 10× 即時，總量 ≤ 60 秒（規格的 A 端緩衝） | |
| JSON | 用 `json.loads(..., parse_constant=拒絕)` 拒絕 `NaN`／`Infinity`；捕捉 `RecursionError`／`ValueError` 後以 1007／1008 關閉；`type` 不在允許清單就關閉；多餘欄位一律拒絕 | 嚴格 schema |
| 格式錯誤處理 | 回錯誤並關閉連線，**不讓整個引擎崩潰**（沿用 stdio 的 `ProtocolError` 模式） | |

### 3.5 Session 狀態機（1:1、resume）

- 狀態：`CONNECTED` →（mTLS 與 pin 通過）→ `HELLO_OK` → `SESSION` → `CLOSED`。每個狀態只接受允許的訊息類型，其他一律關閉連線（CVE-2024-51738：順序錯就中止）。
- `busy` 只回給**已經通過 mTLS 驗證**的連線；未配對的連線在 TLS 層就被擋，看不到任何資訊。
- 階段 3 的 resume：`resume_from_sample` 必須對應 B 仍保留的 session，而且**發起 resume 的 SPKI 必須和原本 session 的相同**，不能只看 session_id。B 保留 session 30 秒，期間佔用的記憶體要計入上限。

### 3.6 金鑰與信任檔案

- 存在設定資料夾的 `compute/`（規格已寫）。Linux／macOS 用 `0600`／`0700`；Windows 依賴使用者設定檔的預設 ACL（只有該使用者與 SYSTEM／Administrators）。如果在其他位置建立，要明確設定 ACL。
- 原子寫入（寫暫存檔、fsync、rename）。信任清單**只在配對完全成功後寫入**（CVE-2024-45407）。
- 診斷匯出與 log 不包含 `compute/`（規格已寫），也不包含配對碼。

### 3.7 Logging

- 可以記錄：連線與斷線、來源 IP、對方 SPKI 指紋的前 8 個字元、TLS 或 pin 失敗、配對成功或失敗與剩餘次數、超過上限、schema 錯誤的類型。
- 不能記錄：音訊、字幕文字、術語表或本場資訊內容、配對碼、`K`、MAC、私鑰、翻譯 API 金鑰（OWASP；專案規則）。

### 3.8 Ollama 與翻譯

- B 的翻譯一律用 B 自己的 `translation.*` 設定。遠端訊息不能改 base_url、模型或金鑰。
- 「應該做」：B 的設定頁偵測 Ollama 是否也在區網介面上回應（`OLLAMA_HOST=0.0.0.0`），有的話顯示提醒；同時顯示 Ollama 版本並提醒更新（CVE-2025-63389 沒有修補，只能靠不暴露）。

---

## 4. 威脅對照表

| # | 威脅 | 攻擊者 | 對策 | 階段 | 測試 |
|---|---|---|---|---|---|
| 1 | 未配對的電腦使用 B 的運算（ShadowRay 類） | T1 T2 T4 | 正式埠 mTLS `CERT_REQUIRED`、SPKI 完全比對、來源 IP 限私有位址 | 2 | S2-07、S2-08 |
| 2 | 中間人離線暴力破解配對碼（CVE-2024-51738 類） | T1 | SPAKE2 或 numeric comparison；MAC 綁定雙方 SPKI | 2 | S2-05、S2-06 |
| 3 | 失敗的配對留下信任（CVE-2024-45407） | T1 | 只在 MAC 驗證成功後寫入 | 2 | S2-04 |
| 4 | 配對碼套用到錯的 session（GHSA-36ff） | T1 | 只允許一個待配對 session；B 顯示對方名稱與 IP | 2 | S2-09 |
| 5 | 撤銷後用衍生憑證繞過（GHSA-6jvv） | T6 | `CA:FALSE`；葉憑證 SPKI 完全比對；移除時斷線並重建 context | 2 | S2-10、S2-11 |
| 6 | 線上暴力猜配對碼（包含來自 loopback 的猜測） | T1 T3 T7 | 5 次後作廢、不自動重發、loopback 不例外、`compare_digest` | 2 | S2-02、S2-03 |
| 7 | 跨站 WebSocket 劫持 | T3 | `origins=[None]`；mTLS；開發者模式只綁 127.0.0.1 並要求 token | 1、2 | S1-05、S2-12 |
| 8 | DNS rebinding | T3 | `Host` 允許清單 | 1、2 | S1-06 |
| 9 | 遠端觸發下載、刪除或拉模型（Probllama 類） | 已配對但被入侵的 A | 最小訊息允許清單，不重用 stdio 分派表 | 1 | S1-07 |
| 10 | 經由 session 欄位做 SSRF 或外洩金鑰 | 已配對但被入侵的 A | 不接受 base_url、模型、金鑰欄位；嚴格 schema | 1 | S1-08 |
| 11 | 壓縮炸彈、超大幀、fragment 炸彈 | T1（握手前）、A | `compression=None`、`max_size`、websockets 17.4 | 1 | S1-09、S1-10 |
| 12 | slowloris、閒置連線佔住 slot | T1 | 握手與 `hello` 逾時；握手中連線數上限 | 1 | S1-11 |
| 13 | 第二台 A 搶佔 B | 另一台已配對的 A | `busy` | 1 | S1-04 |
| 14 | 音訊幀格式錯誤造成崩潰或越界 | A | 長度、`seq`、`start_sample` 驗證 | 1 | S1-12 |
| 15 | 惡意的 B 對 A 注入 HTML 或指令（Open WebUI 類） | T5 | A 端 schema 驗證、只用文字節點顯示、B 不能觸發 A 的指令 | 1 | S1-13、S1-14 |
| 16 | 用其他裝置的身分 resume session | 另一台已配對的 A | resume 綁定 SPKI | 3 | S3-02 |
| 17 | log 或診斷資訊洩漏音訊、文字、碼 | T7 | logging 規則 | 1–3 | S1-15 |
| 18 | 金鑰檔被同機其他使用者讀取 | T7 | 檔案權限與 ACL | 2 | S2-13 |
| 19 | overlay 或 XSS 呼叫配對與信任清單 IPC | T5 T3 | 新指令只放 main capability；CSP 不開 `connect-src` | 2 | S2-14 |
| 20 | 經由 Tailscale 碰到 B 的 loopback Ollama（TS-2026-007） | T4 | 說明文件要求 Tailscale ≥ 1.104.4；kikitori 不信任 loopback；Ollama 維持更新 | 文件 | — |

---

## 5. 優先清單（對應規格階段）

### 必須（Must）

**階段 1（協定與區網，開發者模式）**
- [ ] `BackendServer` 自己定義最小訊息允許清單；**不重用** stdio 的分派表；`type` 不在清單裡就關閉連線。
- [ ] `session.start` 嚴格 schema：不接受 base_url、模型、路徑、金鑰欄位；所有字串限長、清單限條數。
- [ ] 二進位音訊幀驗證長度、偶數、`seq` 遞增、`start_sample` 連續，並且只在 utterance 開啟時接受；單句樣本數有上限。
- [ ] `websockets.serve(..., compression=None, max_size=64 KiB 等級, origins=[None], server_header=None, open_timeout=5)`，加上用 `process_request` 檢查 `Host`；不用 `recv_streaming()`。
- [ ] `hello` 期限與握手中連線數上限；第二台已驗證的用戶端回 `busy`。
- [ ] JSON：拒絕 `NaN`／`Infinity`、多餘欄位、深層巢狀；格式錯誤只關閉該連線，不讓引擎崩潰。
- [ ] 不用 pickle、`eval`、`getattr` 分派；B 不接收任何檔案。
- [ ] 開發者模式的明文連線只綁 127.0.0.1，並要求每次啟動產生的 token；正式建置無法開啟（加測試）。
- [ ] A 端對 B 的訊息做 schema 驗證，只轉換成現有的 `subtitle`／`warning` 事件；前端維持不用 `innerHTML`。
- [ ] logging 規則：不記錄音訊、文字、術語表、碼、金鑰。

**階段 2（配對與 mTLS）**
- [ ] **把規格的「配對碼 HMAC 指紋」改成 SPAKE2（需要新增依賴 `spake2`，先問使用者）或 numeric comparison**，MAC 綁定雙方 SPKI 與 transcript。
- [ ] 配對狀態機：只允許一個待配對 session；順序錯就中止；5 次失敗就作廢且不自動重發；5 分鐘逾時；loopback 不例外；用 `compare_digest`。
- [ ] 信任清單只在驗證成功後原子寫入；任何失敗分支都不留下紀錄（CVE-2024-45407）。
- [ ] 正式監聽器 TLS 1.3、`CERT_REQUIRED`；握手後對葉憑證的 SPKI SHA-256 做完全比對；沒有「仍要繼續」。
- [ ] 配對監聽器只在使用者按下按鈕後開啟，結束就關閉；正式監聽器在 TLS 層拒絕未配對的連線。
- [ ] 自簽憑證：P-256、`CA:FALSE`（critical）、KeyUsage、SKI／AKI；不用 RFC 7250 raw public keys；不開 OCSP。
- [ ] 撤銷：移除 pin、立刻斷開該裝置、重建 `SSLContext`。
- [ ] 預設不綁 `0.0.0.0`，由使用者選擇介面；拒絕非私有來源 IP（可在進階設定關閉）。
- [ ] 金鑰與信任檔的權限；不放進 settings.json、診斷資訊或 log。
- [ ] Tauri：新指令只放 `main.json`；加測試確認 overlay 沒有 `compute_*`；CSP 不變；Rust 端再驗證碼、主機與埠格式。

**階段 3（重連）**
- [ ] resume 綁定原 session 的 SPKI；`resume_from_sample` 只能在 B 保留的範圍內。
- [ ] 補送有速率與總量上限；B 保留 session 時的記憶體計入上限；30 秒後釋放。
- [ ] A 的音訊緩衝只放在記憶體，不寫磁碟。

### 應該（Should）
- [ ] B 的設定頁偵測 Ollama 是否也聽在區網介面上，並顯示版本與更新提醒。
- [ ] B 在配對與連線時顯示對方的名稱、IP、指紋前綴；連線中的電腦要有「中斷連線」按鈕。
- [ ] 打包測試記錄並檢查 `ssl.OPENSSL_VERSION`，評估升級 Python 3.13 以上（3.12 已沒有官方二進位版本）。
- [ ] 說明文件：跨網路用 Tailscale IP 或 MagicDNS；不要用 port forwarding 或 Funnel；Tailscale 保持更新（≥ 1.104.4）；Windows 防火牆只勾私人網路。
- [ ] 在 CI 加 `pip-audit`（或同類工具）與 `cargo audit`，定期檢查 `websockets`、`cryptography`、`tauri` 的公告（新增工具前先問使用者）。
- [ ] 配對成功或失敗時，在 B 顯示系統通知，讓使用者知道有人嘗試配對。

### 之後（Later）
- [ ] Tauri isolation pattern。
- [ ] 多台用戶端（階段 5）時，改成每台裝置的配額與公平排程。
- [ ] 無頭或服務模式如果要做 Web 管理頁，依 OWASP 做 CSRF 與 clickjacking 防護（Sunshine GHSA-39hj、GHSA-x97g）。
- [ ] 請外部做配對協定的設計審查。

---

## 6. 要寫的安全測試

依專案規則，修 bug 前先寫能重現的測試；這些測試以 `unittest`、在 127.0.0.1 起伺服器的方式實作。

**階段 1**
- S1-01 協定版本不符 → `incompatible`，然後關閉連線。
- S1-02 第一則訊息不是 `hello`，或 5 秒內沒有送 `hello` → 關閉連線。
- S1-03 狀態錯誤（還沒 `session.start` 就送音訊；還沒 `utterance.start` 就送音訊）→ 關閉連線，引擎仍然存活。
- S1-04 第二台用戶端連線 → `busy`，第一台不受影響。
- S1-05 握手帶 `Origin: https://evil.example` → 403，沒有建立 session。
- S1-06 `Host: evil.example`（DNS rebinding）→ 拒絕。
- S1-07 送 `models.pull_translation`、`models.download`、`settings.set`、`models.delete`，以及其他任何 stdio 指令名稱 → 拒絕，而且沒有呼叫到 Ollama 或檔案系統（用 mock 確認）。
- S1-08 `session.start` 帶 `base_url`、模型名稱、`../` 路徑、超長術語表、`NaN`、多餘欄位、型別錯誤 → 拒絕。
- S1-09 文字訊息或二進位訊息超過 `max_size` → 以 1009 關閉。
- S1-10 帶 `permessage-deflate` 的用戶端 → 協商結果沒有壓縮。
- S1-11 TCP 連上後什麼都不送（slowloris）、TLS 握手做一半 → 逾時關閉；同時大量半開連線 → 超過上限的被拒，合法用戶端仍能連上。
- S1-12 二進位幀：長度 0、奇數長度、不到 12 B 標頭、`seq` 倒退或重複、`start_sample` 跳號或溢位、單句超過上限 → 關閉連線，不崩潰。
- S1-13 （A 端）B 回傳含 `<img onerror>`／`<script>` 的字幕 → DOM 裡只出現文字節點（前端測試）。
- S1-14 （A 端）B 回傳未知的 `type`、不存在的 `utterance_id`、超長文字 → 丟棄並警告，不轉給 Tauri。
- S1-15 跑完一場 session 之後掃描 log，裡面沒有字幕文字、術語表內容或音訊 bytes。
- S1-16 正式建置設定下無法開啟明文模式；開發者模式下沒有 token 會被拒。

**階段 2**
- S2-01 正確的碼 → 雙方各 pin 一筆；之後用 mTLS 連得上。
- S2-02 錯誤的碼 → 失敗，剩餘次數減一，雙方都沒有信任紀錄。
- S2-03 連錯 5 次 → 碼作廢、監聽器關閉；第 6 次即使用正確的碼也失敗；從 127.0.0.1 嘗試也一樣。
- S2-04 配對在任何一步中斷或失敗（斷線、MAC 錯誤、順序錯亂）→ 信任清單沒有變（CVE-2024-45407 的回歸測試）。
- S2-05 中間人測試：在 A 與 B 之間放一個用自己憑證重新做 TLS 的 proxy，轉送配對訊息 → 配對失敗，雙方都沒有 pin 到中間人的 SPKI。
- S2-06 離線暴力破解測試：錄下一次配對的完整 transcript（包含中間人看到的內容），用 10⁶ 個候選碼離線驗證 → 找不出碼（這個測試會證明原本的 HMAC 設計失敗、SPAKE2 設計通過）。
- S2-07 未配對的用戶端（沒有憑證，或用陌生的自簽憑證）連正式埠 → TLS 握手失敗，伺服器沒有建立任何 session 物件。
- S2-08 用已配對裝置的憑證，但換成另一把私鑰（換公鑰）→ 拒絕；A 端遇到 B 換了公鑰 → 拒絕，而且沒有繞過選項。
- S2-09 配對期間第二個用戶端也連進配對監聽器 → 被拒，或碼作廢；B 顯示的待配對對象沒有被換掉。
- S2-10 撤銷：移除裝置後，它現有的連線立刻斷開，新的連線在 TLS 或 pin 階段被拒。
- S2-11 衍生憑證：用已配對裝置的私鑰簽一張新的葉憑證來連線 → 拒絕（GHSA-6jvv 的回歸測試）。
- S2-12 瀏覽器式 CSWSH：對配對監聽器送帶 `Origin` 的握手 → 403。
- S2-13 產生的金鑰檔權限（POSIX `0600`；Windows ACL 不包含 Everyone／Users）。
- S2-14 overlay capability 裡沒有 `compute_*`，而且 overlay 視窗呼叫這些指令會失敗。
- S2-15 配對碼過期（5 分鐘，用假時鐘）→ 失敗並關閉監聽器。
- S2-16 MAC 比對使用 `hmac.compare_digest`（靜態檢查或 mock 確認），長度不對的 MAC 直接失敗。
- S2-17 來源 IP 不是私有位址 → 拒絕（用 mock peername 測試）。

**階段 3**
- S3-01 斷線後 resume → 不重複、不漏句；補送超過速率或總量上限 → 拒絕。
- S3-02 另一台已配對裝置拿別人的 session_id 來 resume → 拒絕。
- S3-03 B 保留 session 超過 30 秒 → 釋放，resume 回「session 已不存在」，A 改成開新 session。
- S3-04 心跳 10 秒沒有回應 → 雙方都判定斷線並釋放資源。

---

## 7. 要更新規格或請使用者決定的事

1. **配對協定**：規格寫的是「用配對碼對兩邊憑證指紋做 HMAC 互相確認」，建議改成 SPAKE2（需要新增依賴 `spake2==0.9`，MIT）或 numeric comparison（不用加依賴，但 UX 改成兩邊比對數字並按確認）。這是會影響 UX 與依賴的決定，要先問使用者。
2. **配對用的監聽器**：受限於 Python `ssl`，建議正式埠（mTLS）與配對埠分開。要決定是否用 47101，以及是否可以讓使用者修改。
3. **綁定介面與來源 IP 限制**：規格目前沒有寫，建議在「設定與介面」一節補上。
4. **開發者模式的明文連線**：補上「只綁 127.0.0.1、需要 token、正式建置無法開啟」。
5. **`BackendServer` 的訊息允許清單與 `session.start` schema**：補在「協定」一節，並寫明不重用 stdio 分派表。

---

## 8. 未查證或不確定的地方

- Cline 的 CVE 編號（CVE-2026-44211）、OpenClaw 的 CVE（CVE-2026-25253）、Ollama CVE-2026-42249、aiohttp CVE-2026-54274 與 Autobahn CVE-2026-77528 只找到彙整站、新聞或廠商資料庫，沒有開到 GHSA 或 NVD 原文。
- NCC Group 的 CVE-2024-28224 原文回傳 403，修補細節（是否就是 `Host` 檢查）沒有查到原文；影響內容以 OSV 為準。
- `websockets.serve()` 能否把 `ssl_handshake_timeout` 轉傳給 `loop.create_server` 要看原始碼確認。`process_request` 怎麼讀 `Host`，API 頁沒有寫清楚，要查 `Request.headers` 文件。
- `spake2` 0.9 的維護活躍度、是否支援 Python 3.12 與 3.13、是否經過稽核，PyPI 頁沒有說明；採用前要看 GitHub repo 與 magic-wormhole 的實際用法。
- OpenSSL 為什麼會接受「由一張 `CA:FALSE` 自簽憑證簽出的子憑證」，以及哪些情況會擋，這裡是依 X.509 一般規則推論，沒有實測；S2-11 會用測試確認。
- PyInstaller 打包出來的引擎實際帶哪一版 OpenSSL，沒有檢查（列在「應該」）。

---

## 參考來源

- Ollama：[Wiz Probllama](https://www.wiz.io/blog/probllama-ollama-vulnerability-cve-2024-37032)｜[Oligo More Models, More ProbLLMs](https://oligo.security/blog/more-models-more-probllms)｜[THN 2024-11](https://thehackernews.com/2024/11/critical-flaws-in-ollama-ai-framework.html)｜[OSV CVE-2024-28224](https://api.osv.dev/v1/vulns/CVE-2024-28224)｜[NCC Group advisory](https://www.nccgroup.com/research/technical-advisory-ollama-dns-rebinding-attack-cve-2024-28224/)｜[OSV GHSA-f6mr-38g8-39rg](https://osv.dev/vulnerability/GHSA-f6mr-38g8-39rg)｜[THN 2026-05](https://thehackernews.com/2026/05/ollama-out-of-bounds-read-vulnerability.html)
- Ray／Langflow／ComfyUI／Gradio／Open WebUI：[MITRE CVE-2023-48022](https://cveawg.mitre.org/api/cve/CVE-2023-48022)｜[Oligo ShadowRay](https://oligo.security/blog/shadowray-attack-ai-workloads-actively-exploited-in-the-wild)｜[Oligo ShadowRay 2.0](https://oligo.security/blog/shadowray-2-0-attackers-turn-ai-against-itself-in-global-campaign-that-hijacks-ai-into-self-propagating-botnet)｜[Help Net Security Langflow](https://www.helpnetsecurity.com/2025/05/06/langflow-cve-2025-3248-exploited/)｜[Recorded Future Langflow](https://www.recordedfuture.com/blog/langflow-cve-2025-3248)｜[OSV CVE-2026-68771](https://osv.dev/vulnerability/CVE-2026-68771)｜[MITRE CVE-2024-1561](https://cveawg.mitre.org/api/cve/CVE-2024-1561)｜[GHSA-cm35-v4vp-5xvx](https://github.com/open-webui/open-webui/security/advisories/GHSA-cm35-v4vp-5xvx)
- 瀏覽器跳板：[Oligo MCP Inspector](https://www.oligo.security/blog/critical-rce-vulnerability-in-anthropic-mcp-inspector-cve-2025-49596)｜[Tenable MCP Inspector](https://www.tenable.com/blog/how-tenable-research-discovered-a-critical-remote-code-execution-vulnerability-on-anthropic)｜[THN 0.0.0.0 Day](https://thehackernews.com/2024/08/0000-day-18-year-old-browser.html)｜[Help Net Security 0.0.0.0 Day](https://www.helpnetsecurity.com/2024/08/09/0-0-0-0-day-vulnerability-affects-chrome-safari-and-firefox/)｜[Oasis Cline](https://www.oasis.security/blog/cline-kanban-websocket-hijack)｜[Oasis OpenClaw](https://www.oasis.security/resources/cross-origin-websocket-exploitation-openclaw)｜[CSA ClawJacked](https://labs.cloudsecurityalliance.org/research/csa-research-note-clawjacked-websocket-local-agent-hijack-20/)｜[Chrome Local Network Access](https://developer.chrome.com/blog/local-network-access)｜[OWASP WebSocket Security Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/WebSocket_Security_Cheat_Sheet.html)
- Sunshine：[Advisories list](https://github.com/LizardByte/Sunshine/security/advisories)｜[GHSA-3hrw-xv8h-9499](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-3hrw-xv8h-9499)｜[NVD CVE-2024-51738](https://nvd.nist.gov/vuln/detail/CVE-2024-51738)｜[GHSA-36ff-frg7-492f](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-36ff-frg7-492f)｜[GHSA-ph75-mgxh-mv57](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-ph75-mgxh-mv57)｜[GHSA-6jvv-jqr7-m6m3](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-6jvv-jqr7-m6m3)
- websockets：[changelog](https://websockets.readthedocs.io/en/stable/project/changelog.html)｜[security](https://websockets.readthedocs.io/en/stable/topics/security.html)｜[server API](https://websockets.readthedocs.io/en/stable/reference/asyncio/server.html)｜[GHSA-6g87-ff9q-v847](https://osv.dev/vulnerability/GHSA-6g87-ff9q-v847)｜[SentinelOne CVE-2026-54274](https://www.sentinelone.com/vulnerability-database/cve-2026-54274/)｜[SentinelOne CVE-2026-77528](https://www.sentinelone.com/vulnerability-database/cve-2026-77528/)
- TLS／密碼學：[pyca advisories](https://github.com/pyca/cryptography/security/advisories)｜[GHSA-537c-gmf6-5ccf](https://github.com/advisories/GHSA-537c-gmf6-5ccf)｜[Red Hat CVE-2024-12797](https://access.redhat.com/security/cve/CVE-2024-12797)｜[Ubuntu CVE-2024-12797](https://ubuntu.com/security/CVE-2024-12797)｜[OpenSSL secadv 20260609](https://openssl-library.org/news/secadv/20260609.txt)｜[Python ssl](https://docs.python.org/3.12/library/ssl.html)｜[Python hmac](https://docs.python.org/3/library/hmac.html)｜[Python 3.12.15](https://www.python.org/downloads/release/python-31215/)｜[RFC 8446](https://www.rfc-editor.org/rfc/rfc8446.html)｜[RFC 9382](https://www.rfc-editor.org/rfc/rfc9382.html)｜[PyPI spake2](https://pypi.org/project/spake2/)｜[magic-wormhole docs](https://magic-wormhole.readthedocs.io/en/latest/welcome.html)
- Tailscale：[Security bulletins](https://tailscale.com/security-bulletins)
- Tauri：[Advisories list](https://github.com/tauri-apps/tauri/security/advisories)｜[NVD CVE-2024-35222](https://nvd.nist.gov/vuln/detail/CVE-2024-35222)｜[GHSA-7gmj-67g7-phm9](https://github.com/tauri-apps/tauri/security/advisories/GHSA-7gmj-67g7-phm9)｜[GHSA-w28w-mhc8-qvjv](https://github.com/tauri-apps/tauri/security/advisories/GHSA-w28w-mhc8-qvjv)
