# 遠端運算模式：安全、配對、探索、重連與伺服器營運

> 研究日期：2026-11-09。情境：電腦 A 擷取麥克風／系統音訊並顯示字幕，電腦 B（GPU）跑 Whisper（CTranslate2）ASR 與翻譯，A↔B 以 IP 連線（LAN 或網際網路）。音訊可能含私人對話。

## 1. 加密與驗證：主流消費級 App 如何在無中央帳號下配對自己的兩台裝置？哪種最簡單又安全？

### Takeaway
主流做法收斂成同一個模式：**每台裝置首次啟動產生自簽憑證／金鑰對 → 以「指紋」或「短 PIN 配對」建立信任 → 之後以 mutual TLS 驗證並 pin 住對方公鑰**（Syncthing、Moonlight/Sunshine、KDE Connect 都是這個形狀）。對 kikitori 而言，最簡單又安全的組合是「TLS 1.3 + 雙方自簽憑證 + 配對時以一次性短碼確認指紋（或用 SPAKE2 交換）＋配對後 pin 公鑰」；純 bearer token（Home Assistant 式）較簡單，但必須跑在已驗證伺服器身分的 TLS 之上才有意義。Ollama 本身無驗證，不可作為範本。

### Cited Findings
**Syncthing（device ID = 憑證指紋）**
- 首次啟動產生金鑰對；device ID「is a direct property of the public key in use」。目前為 256-bit Ed25519（v2.0.0 前為 384-bit ECDSA，v0.12.5 前為 3072-bit RSA）；私鑰存 `key.pem`、憑證存 `cert.pem` — [Syncthing Docs: Understanding Device IDs](https://docs.syncthing.net/dev/device-ids.html)
- 憑證為自簽、不帶真實身分（serial 為 0、issuer/subject 皆為 "syncthing"），只是用來把公鑰包進標準 TLS 交握 — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)
- Device ID = 對 DER 格式憑證做 SHA-256，再 base32 編碼、去 padding、每組加 check digit、以破折號分組顯示 — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)
- 交握時「both devices present their certificates」；之後各自從收到的憑證計算對方 device ID，與設定比對，不符就斷線（等同 mutual TLS + pinning） — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)
- 信任靠設定（雙方都要加入對方 ID），探索（discovery）只負責「找到」不負責「授權」；local discovery「is not protected by cryptography」，任何裝置都能冒稱某 ID 廣播，靠交握檢查擋下冒充者 — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)
- 文件自承長 device ID 不易透過電話或簡訊交換，並提出「較短 ID + 類似藍牙的一次性 PIN」作為可能方案（非現有功能） — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)

**Moonlight / Sunshine（GameStream 協定：PIN 配對 + client 憑證）**
- 配對流程：Moonlight 送出 salt 與 client 憑證；使用者在伺服器端輸入 PIN；「PIN and SALT will be used to derive a shared AES key」；伺服器回傳其公開憑證；雙方以 AES 加密的 challenge/response 互相證明知道 PIN，並以各自私鑰簽署 secret；最後驗證 `SHA256(server_challenge + client_public_cert_signature + client_secret) == client_hash` — [Games on Whales / Wolf: HTTP pairing protocol](https://games-on-whales.github.io/wolf/stable/protocols/http-pairing.html)
- 配對後改走 HTTPS，伺服器檢查每個請求的 client 憑證：「only clients that have been successfully paired will be allowed to call the APIs」 — [Wolf docs](https://games-on-whales.github.io/wolf/stable/protocols/http-pairing.html)
- 實作陷阱：CVE-2024-45407（GHSA-jqph-8cp5-g874，CVSS 6.5，CWE-300）——MITM 攔截配對並以新 PIN + 攻擊者憑證轉送；使用者輸入原 PIN 後配對失敗，但「the certificate from the forged pairing attempt is incorrectly persisted prior to the completion of the pairing request」，攻擊者憑證因此取得存取權。影響 2024-05-27 至 2024-09-10 間的 pre-release，修正於 v2024.910.1354 — [GitHub Security Advisory GHSA-jqph-8cp5-g874](https://github.com/LizardByte/Sunshine/security/advisories/GHSA-jqph-8cp5-g874)
- Sunshine 自身安全說明表示其設計前提是 trusted networks（經搜尋摘要，未直接開啟原頁） — [Sunshine security (mintlify mirror)](https://mintlify.com/LizardByte/Sunshine/deployment/security)

**KDE Connect**
- 社群／bug tracker 描述：TLS + TOFU（trust-on-first-use）雙向驗證；主要連線與配對在 TCP 1716、探索 UDP 1716，整體埠範圍各來源說法不一（1714–1764 vs 1716–1764）— [KDE Bug 515707](https://bugs.kde.org/show_bug.cgi?id=515707)；[GSConnect wiki](https://github.com/GSConnect/gnome-shell-extension-gsconnect/wiki/Help)
- Rust 重新實作的程式碼顯示：client 送出自己的憑證，用自訂 verifier 比對已儲存的受信任裝置憑證，無儲存則標記為 untrusted — [kdeconnect-proto tls.rs (docs.rs)](https://docs.rs/crate/kdeconnect-proto/0.1.2/source/src/transport/tls.rs)
- 已知營運問題：版本 1.35.x 在 Tailscale VPN 上 TLS 交握失敗（1.34.4 正常）——顯示 overlay 網路與 TLS 實作的互動要測 — [KDE Bug 515707](https://bugs.kde.org/show_bug.cgi?id=515707)

**Home Assistant（long-lived token）**
- 使用者可在個人頁「Security」建立 long-lived access token，「valid for 10 years」，用法與 access token 相同（HTTP `Authorization: Bearer`；WebSocket 放在 auth 訊息） — [Home Assistant Auth API](https://contributors.home-assistant.io/core/internals/auth-api/)
- 可經 WebSocket `auth/long_lived_access_token` 以 `lifespan`（天）程式化建立；「Home Assistant does not store it, so it cannot be shown again」 — [Home Assistant Auth API](https://contributors.home-assistant.io/core/internals/auth-api/)
- 一般 access token 短命（範例 `expires_in: 1800`）；refresh token 90 天未用即失效；可 POST `/auth/revoke` 撤銷 refresh token 及其衍生的所有 access token；無效 token 回 HTTP 401，WebSocket 回 `auth_invalid` 並關閉連線 — [Home Assistant Auth API](https://contributors.home-assistant.io/core/internals/auth-api/)

**Ollama（反面教材）**
- 預設綁定 127.0.0.1:11434；以 `OLLAMA_HOST`（例如 `0.0.0.0:11434`）對外開放；FAQ 未提供任何驗證或存取控制機制，只給反向代理（Nginx）與 ngrok / Cloudflare Tunnel 範例；CORS 以 `OLLAMA_ORIGINS` 控制 — [Ollama FAQ](https://docs.ollama.com/faq)
- 多份掃描報告：Cisco Talos 以 Shodan 找到 1,100+ 台暴露的 Ollama，約 20% 正在提供可被未授權存取的模型 — [Cisco Blogs](https://blogs.cisco.com/security/detecting-exposed-llm-servers-shodan-case-study-on-ollama)；SentinelOne + Censys（2026-01）報告約 175,000 台主機的 11434 埠可公開存取且 API 無驗證回應 — [The Hacker News](https://thehackernews.com/2026/01/researchers-find-175000-publicly.html?m=0)；LeakIX 報告 12,269 台零驗證 — [LeakIX blog](https://blog.leakix.net/2026/02/ollama-exposed/)；CSA 研究筆記指出受測 Ollama API 中 31% 回應未授權測試 prompt — [Cloud Security Alliance](https://labs.cloudsecurityalliance.org/research/csa-research-note-exposed-ai-infrastructure-selfhosted-secur/)。（數字差異來自不同方法與日期，未逐一查證原始報告。）

**PAKE（SPAKE2 / OPAQUE）**
- SPAKE2（RFC 9382）：雙方以共享密碼導出強共享金鑰而不洩漏密碼；「compatible with any group」、「computationally efficient」、具安全證明（gap Diffie-Hellman 假設） — [RFC 9382](https://www.rfc-editor.org/rfc/rfc9382.html)
- 狀態：IRTF CFRG 發布的 Informational RFC，「not an Internet Standards Track specification」；未在 CFRG PAKE 競賽中被選中，但因 Kerberos 等已有使用而發布；SPAKE2 不支援 augmentation，需要 augmented PAKE 時指向 OPAQUE；收到的公開值不屬於群 G 時 MUST abort；隨機值須均勻且不可重用；橢圓曲線運算須 constant-time — [RFC 9382](https://www.rfc-editor.org/rfc/rfc9382.html)

**OWASP 對 pinning 的立場**
- OWASP Pinning Cheat Sheet 主要針對「公開 CA 簽發憑證」的情境：「almost no situation where you should consider pinning」；但條件是「If you don't control the client and server side of the connection, don't pin」——反過來說兩端都自己掌控時才適合 — [OWASP Pinning Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Pinning_Cheat_Sheet.html)
- 建議 pin `subjectPublicKeyInfo`（公鑰）而非整張憑證，可在憑證更新時不變；「do not give the user an option to proceed and bypass the pin」；避免自己從頭寫驗證邏輯，因「implementation mistakes are extremely likely」 — [OWASP Pinning Cheat Sheet](https://cheatsheetseries.owasp.org/cheatsheets/Pinning_Cheat_Sheet.html)

**實作支援（Rust 端）**
- rustls 支援 TLS 1.2 與 1.3（client 與 server） — [rustls GitHub](https://github.com/rustls/rustls)
- rustls 的 `rustls::client::danger::ServerCertVerifier` trait 是自訂伺服器憑證驗證的擴充點（`verify_server_cert`、`verify_tls12_signature`、`verify_tls13_signature`、`supported_verify_schemes`），並有 `requires_raw_public_keys`（RFC 7250 raw public keys）；憑證以未解析形式傳入，實作者須處理無效資料 — [docs.rs rustls ServerCertVerifier](https://docs.rs/rustls/latest/rustls/client/danger/trait.ServerCertVerifier.html)

### Inferences
- **建議方案（簡單又安全）**：
  1. B（伺服器）首次啟動產生 Ed25519 或 ECDSA P-256 自簽憑證（Syncthing 模式），A 也產生自己的 client 憑證。
  2. 配對：B 的 UI／終端顯示 6–8 位一次性配對碼（有效期數分鐘、限制嘗試次數），A 輸入 IP + 配對碼。用配對碼做 SPAKE2 交換，或較簡單的做法：以 TLS 連上後，把配對碼與 TLS exporter／雙方憑證指紋綁在一起做 HMAC 確認，防止 MITM（Moonlight 協定的核心思想即「用 PIN 證明雙方看到的是同一對憑證」）。
  3. 配對成功後，雙方 pin 對方的 SPKI 指紋，之後只以 mutual TLS 連線；pin 不符時硬失敗、不給「仍要繼續」按鈕（OWASP）。
  4. B 端提供「已配對裝置清單」與撤銷（Home Assistant 的 revoke 概念）。
- **CVE-2024-45407 的教訓**：只有在配對「完整成功」之後才把對方憑證寫入信任清單；配對狀態機必須在任何失敗分支丟棄暫存憑證。應寫測試覆蓋「錯 PIN 不得留下信任紀錄」。
- 只用 bearer token（HA 模式）+ 未驗證的 TLS，會在首次連線時被 MITM 攔走 token；token 必須搭配 pinned TLS 才安全。token 適合作為「應用層 session 身分」，憑證 pinning 負責「通道身分」。
- 不要把 Ollama 的模式（裸 HTTP、無驗證、綁 0.0.0.0）延伸到音訊伺服器；kikitori 的翻譯後端若在 B 機上，應讓 Ollama 維持綁 127.0.0.1，由 kikitori 伺服器在同機代呼叫，避免再暴露一個無驗證埠。
- Python engine 若當伺服器，Python 標準 `ssl` 模組可載入自簽憑證並要求 client 憑證（`CERT_REQUIRED`）；指紋比對需在應用層取得 `getpeercert(binary_form=True)` 後計算 SHA-256（此為一般知識推論，本次未查官方文件）。

### Gaps
- Apple Continuity（iCloud 帳號 + BLE/Wi-Fi）與 Steam Remote Play 的配對細節本次未取得一手文件；兩者都依賴中央帳號，與「無中央帳號」情境較不相符。
- KDE Connect 官方協定規格（配對時顯示的 verification key 計算方式）未找到官方頁面，僅有 bug tracker 與第三方實作。
- OPAQUE（RFC 9807）與 IETF 對「裝置配對」的專門建議未查證。
- Python 端可用的 SPAKE2 函式庫（如 `spake2` PyPI 套件）授權與維護狀態未查證。

## 2. LAN 探索：mDNS/DNS-SD vs 手動輸入 IP

### Takeaway
mDNS/DNS-SD 在三大平台都可用（Windows 10 1703+ 有原生支援與 `DnsServiceRegister` API；macOS Bonjour、Linux Avahi），但探索不提供任何安全性，只能當「方便找到 IP」的輔助；必須保留手動 IP 輸入作為主要／備援路徑，並以配對時的金鑰驗證身分。

### Cited Findings
- Syncthing：local discovery 不受密碼學保護，任何裝置都能為任何 device ID 廣播；探索只找位址、不授權，真正把關在 TLS 交握 — [Syncthing Docs](https://docs.syncthing.net/dev/device-ids.html)
- Windows 10 1703 起內建 mDNS 與基本 DNS-SD（早期版本僅用於探索網路印表機，後續版本可解析主機名） — [ctrl.blog: mDNS and DNS-SD slowly making their way into Windows 10](https://ctrl.blog/entry/windows-mdns-dnssd)
- Microsoft `DnsServiceRegister`：「register a discoverable service on this device」，最低支援 Windows 10（desktop apps only），程序結束時自動取消註冊 — [Microsoft Learn: DnsServiceRegister](https://learn.microsoft.com/en-us/windows/win32/api/Windns/nf-windns-dnsserviceregister)
- Windows Defender Firewall 有預設的「mDNS (UDP-In)」入站規則；Microsoft 建議要完全封鎖 mDNS 時停用此規則（Public/Private/Domain 三個 profile）——企業環境可能關掉 — [Microsoft Tech Community: mDNS in the Enterprise](https://techcommunity.microsoft.com/blog/networkingblog/mdns-in-the-enterprise/3275777)
- mDNS 走 UDP，可多個程序同時監聽 UDP 5353 — [Microsoft Tech Community](https://techcommunity.microsoft.com/blog/networkingblog/mdns-in-the-enterprise/3275777)
- KDE Connect 常見配對失敗原因為防火牆或 VPN；社群建議用 netcat 檢查埠是否可達 — [GSConnect wiki](https://github.com/GSConnect/gnome-shell-extension-gsconnect/wiki/Help)
- Rust `mdns-sd`：支援 macOS、Linux、Windows；「no dependency on any async runtimes」，daemon 跑在獨立執行緒 — [mdns-sd GitHub](https://github.com/keepsimple1/mdns-sd)
- Python `python-zeroconf`：純 Python（可選 Cython），相容 Bonjour 與 Avahi，CPython/PyPy 3.11+；預設使用所有網路介面；使用 dual-stack IPv6 socket，部分 BSD 不支援；非 POSIX 系統上 `InterfaceChoice.All` 等同 `Default` — [python-zeroconf GitHub](https://github.com/python-zeroconf/python-zeroconf)

### Inferences
- 建議 UX：B 端以 DNS-SD 廣播 `_kikitori._tcp`（TXT 記錄放協定版本與憑證指紋前綴，方便 A 顯示「這是你要的那台嗎」）；A 端列出找到的伺服器並提供「手動輸入 IP:port」。指紋最終仍由配對碼驗證，TXT 裡的指紋只作提示。
- 因為 Tauri/Rust 端已有，探索放在 Rust（`mdns-sd`，MIT/Apache）可避開 python-zeroconf 的 LGPL 問題（見第 6 節）。
- Windows 上伺服器第一次監聽 TCP 埠時會跳防火牆提示；應在 UI 說明只勾「私人網路」，並在設定精靈中偵測「連不上」時提示檢查防火牆／網路設定檔為 Public。
- 跨子網路、Guest Wi-Fi（AP isolation）、VPN 下 mDNS 通常無法運作，這是必須保留手動 IP 的主因（一般網路知識推論）。

### Gaps
- RFC 6762/6763 原文本次未直接擷取；Windows 11 對 mDNS 的最新變更只有低品質來源，未採用。
- Windows 首次監聽時的防火牆提示行為（以及以 Windows service 執行時是否提示）未找到官方文件。
- macOS 14+ 的「Local Network」隱私權限對非 App Store 桌面 App 的影響未查證。

## 3. 網際網路使用：直接暴露可以嗎？還是導向 VPN/overlay？

### Takeaway
不建議讓一般使用者做 port forwarding 直接暴露 ASR 伺服器：自架 AI 服務大量裸露在網路上已是有文獻記載的問題（Ollama 數萬到十多萬台）。官方建議路徑應是「跨網際網路請用 Tailscale／WireGuard 類 overlay，kikitori 本身只監聽 LAN/overlay 介面」；kikitori 自己的 mutual TLS + pinning 作為第二層防護，仍保證即使被暴露也不會被未配對者使用。

### Cited Findings
- Tailscale「enables encrypted point-to-point connections using the open source WireGuard protocol」，連線「work seamlessly across firewalls and Network Address Translation (NAT)」，且「without requiring port forwarding or complex firewall rules」，安全模型包含 end-to-end encryption — [Tailscale: What is Tailscale](https://tailscale.com/kb/1151/what-is-tailscale)
- Ollama 官方只給反向代理與 ngrok / Cloudflare Tunnel 範例，未提供驗證 — [Ollama FAQ](https://docs.ollama.com/faq)
- 暴露規模：SentinelOne + Censys 約 175,000 台 Ollama 主機 11434 埠可公開存取且 API 無驗證 — [The Hacker News](https://thehackernews.com/2026/01/researchers-find-175000-publicly.html?m=0)；Cisco 1,100+ 台 — [Cisco Blogs](https://blogs.cisco.com/security/detecting-exposed-llm-servers-shodan-case-study-on-ollama)；LeakIX 12,269 台 — [LeakIX](https://blog.leakix.net/2026/02/ollama-exposed/)。CSA 研究也記錄攻擊者針對暴露的 LLM 端點 — [Cloud Security Alliance](https://labs.cloudsecurityalliance.org/research-rb/csa-research-note-self-hosted-ai-infrastructure-exposure-202/)
- Sunshine 定位為 trusted network 使用（摘要來源） — [Sunshine security (mintlify mirror)](https://mintlify.com/LizardByte/Sunshine/deployment/security)
- KDE Connect 在 Tailscale 上曾出現版本回歸導致 TLS 交握失敗 — [KDE Bug 515707](https://bugs.kde.org/show_bug.cgi?id=515707)

### Inferences
- 建議文件策略：「LAN：直接 IP／mDNS；跨網際網路：安裝 Tailscale（或 WireGuard／ZeroTier），在 kikitori 填 Tailscale 給的 100.x IP 或 MagicDNS 名稱」。overlay 已處理 NAT 穿越與加密，kikitori 不需要做 UPnP／NAT traversal。
- 伺服器預設綁定應是「LAN 介面 + overlay 介面」或讓使用者選擇；不要預設 0.0.0.0 並鼓勵 port forwarding。若使用者仍要直接暴露，mutual TLS 讓未配對 client 在 TLS 層就被拒，比 token-over-HTTP 少很多攻擊面；另外需配對嘗試次數限制、配對模式需在 B 端手動開啟且自動逾時。
- 音訊穿越 overlay 會被 WireGuard 再加密一次，雙重加密的 CPU 成本對 16 kHz 單聲道音訊可忽略（推論，未量測）。
- 若伺服器暴露在網路上，GPU 資源本身就是攻擊者目標（報告提到轉售 GPU 算力），這也是要求驗證的直接理由。

### Gaps
- Tailscale 免費方案的裝置數／使用者數上限、DERP relay 延遲數據本次未取得官方頁面內容。
- ZeroTier、WireGuard 官方文件未擷取。
- 未找到 IETF/OWASP 針對「家用 port forwarding」的明確建議文件。

## 4. 重連與韌性：Wi-Fi 斷線後的 session 恢復、心跳間隔、斷線期間的音訊、退回本機處理；雲端串流 API 如何定義限制與恢復

### Takeaway
雲端 ASR 都有「單一串流壽命上限」與「閒置逾時」，並以 client 端主動重開串流 + 重送尚未定稿（unfinalized）的音訊來達成「無限串流」。kikitori 應採相同設計：應用層心跳每 3–5 秒、約 10 秒無回應判定斷線；client 保留一段環形緩衝（例如最近 N 秒尚未被伺服器確認定稿的音訊），重連後重送並以時間偏移校正字幕時間戳；長時間斷線則丟棄舊音訊或切換本機引擎。

### Cited Findings
- Deepgram：10 秒內沒有音訊或 KeepAlive 訊息就以 `NET-0001` 關閉連線；KeepAlive 格式 `{"type": "KeepAlive"}`，建議「every 3-5 seconds」，必須以 text frame 送出，伺服器不回應；暫停後恢復送音訊時「the timestamps will continue from where the audio left off」 — [Deepgram: Audio Keep Alive](https://developers.deepgram.com/docs/audio-keep-alive)
- Google Cloud Speech-to-Text：串流最長可開約 5 分鐘、音訊須以接近即時的速率送出，超過需採 endless streaming 教學（經搜尋摘要） — [Google Cloud: Quotas and limits](https://docs.cloud.google.com/speech-to-text/docs/quotas)
- Google 官方 endless streaming 範例：Java 設 `STREAMING_LIMIT = 290000; // ~5 minutes`、Python 設 `240000 # 4 minutes`；到時限主動關閉並開新串流，計算上一個請求中「unfinalized audio」並「resend the audio to the speech client before incoming audio」，以 bridging offset + restart count + `result_end_time` 校正跨串流時間戳；串流請求每則訊息 10 MB 上限 — [Google Cloud: Transcribe audio from streaming input](https://docs.cloud.google.com/speech-to-text/docs/transcribe-streaming-audio)
- Pipecat 的 Google STT 整合每 4 分鐘開新串流以超過單一串流限制 — [Pipecat docs: Google STT](https://docs.pipecat.ai/server/services/stt/google)
- WhisperLive 以 `--max_connection_time` 限制單一 client 連線秒數，預設 600 — [WhisperLive GitHub](https://github.com/collabora/WhisperLive)
- Triton sequence batcher：串流（stateful）請求以 correlation ID 路由到同一 model instance，並有 start/end/ready 控制訊號 — [NVIDIA Triton: Batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)

### Inferences
- **協定設計**：每段音訊帶單調遞增的 sequence number 與 capture 時間戳；伺服器回傳字幕時附「已定稿至 seq/時間 X」。client 只需保留「X 之後」的音訊。重連時 client 帶 `session_id` + `resume_from_seq`，伺服器若仍保有該 session 狀態就續用，否則開新 session，client 重送緩衝（Google endless streaming 的模式）。
- **心跳**：沿用 Deepgram 的數字——每 3–5 秒一次 ping，10 秒無任何資料判定斷線；Wi-Fi 切換時 TCP 可能卡住數十秒才報錯，因此應用層心跳不可省（推論）。
- **斷線期間音訊**：建議有上限的環形緩衝（例如 30–60 秒）；超過上限就丟棄最舊的，並在字幕上顯示「連線中斷 mm:ss–mm:ss」標記，而非事後一次湧出大量舊字幕。即時字幕的價值隨延遲快速下降，口譯模式尤其如此（推論）。隱私面：緩衝只存在記憶體，不寫磁碟。
- **退回本機**：若 A 機有本機模型（kikitori 現有模式），超過 N 秒未恢復可自動切到本機小模型並提示使用者；恢復後切回。需讓使用者在設定中選擇「自動退回本機／只等待重連」。
- **伺服器端 session 保留**：伺服器在 client 斷線後保留 session 狀態一段寬限期（例如 30–60 秒，類似 Triton 的 sequence idle timeout 概念），逾時才釋放 GPU 資源與 slot。
- QUIC（quinn）具連線遷移能力，理論上適合 Wi-Fi↔有線切換，但本次未從 quinn 官方頁確認此功能；WebSocket over TLS + 應用層 resume 已足以實作，且 Python 端生態較成熟（推論）。

### Gaps
- Microsoft Azure Speech 連續辨識的連線時長上限與重連建議未擷取。
- Deepgram 官方對「重連時緩衝音訊」的建議未找到（該頁明確未涵蓋）。
- Triton `max_sequence_idle_microseconds` 的預設值與 Direct/Oldest 策略細節在 batcher 頁未出現，需查 Stateful Models 文件或 `model_config.proto`。
- NVIDIA Riva 串流 ASR 的連線限制未查證。

## 5. 伺服器營運：無頭服務、版本相容、資源限制、多 client（排隊、per-client session）

### Takeaway
參考實作的共同做法：**限制同時 client 數、共享一份已載入模型、有界佇列滿了就明確拒絕（HTTP 503 類）、限制單一連線時長**。WhisperLive 預設最多 4 個 client、每連線 600 秒並共用單一模型；Ollama 預設每模型平行 1、佇列 512、滿了回 503；Triton 提供 max_queue_size、佇列逾時與優先權。kikitori 伺服器應以單一 GPU 模型實例 + 有界 per-client session + 協定版本交握為基礎。

### Cited Findings
- WhisperLive：`--max_clients` 預設 4；`--max_connection_time` 預設 600 秒；未指定自訂模型時「the server will instantiate a new whisper model for every client connection」，會增加 (V)RAM 且 client 須等模型載入；使用 `-trt`/`-fw` 自訂模型時只載入一次並供所有 client 重用（`--no_single_model` 可關閉）；後端支援 faster_whisper、tensorrt、openvino；MIT 授權；README 未提驗證或 TLS — [WhisperLive GitHub](https://github.com/collabora/WhisperLive)
- Ollama：`OLLAMA_NUM_PARALLEL` 預設 1（每模型同時處理請求數）、`OLLAMA_MAX_QUEUE` 預設 512、`OLLAMA_MAX_LOADED_MODELS` 預設 3×GPU 數；所需 RAM 隨 `OLLAMA_NUM_PARALLEL * OLLAMA_CONTEXT_LENGTH` 增加；佇列滿時回 HTTP 503；記憶體不足載新模型時請求排隊依序處理、閒置模型被卸載；Linux systemd 以 `[Service]` 的 `Environment` 行設定、macOS 用 `launchctl`、Windows 用使用者環境變數 — [Ollama FAQ](https://docs.ollama.com/faq)
- Triton dynamic batcher：`ModelQueuePolicy` 可設 `max_queue_size`；`timeout_action`、`default_timeout_microseconds`、`allow_timeout_override` 讓排隊過久的請求被拒絕或延後；`priority_levels` 讓高優先權請求越過低優先權，同級依序處理 — [NVIDIA Triton: Batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)
- Triton sequence batcher 用於 stateful 模型，確保同一序列的請求路由到同一 model instance — [NVIDIA Triton: Batchers](https://docs.nvidia.com/deeplearning/triton-inference-server/user-guide/docs/user_guide/batcher.html)
- Home Assistant 對無效 token 回 401、WebSocket 回 `auth_invalid` 後關閉——明確的錯誤碼讓 client 知道要重新驗證而非盲目重試 — [Home Assistant Auth API](https://contributors.home-assistant.io/core/internals/auth-api/)

### Inferences
- **併發模型**：Whisper 模型（CTranslate2）載入一次、多 session 共用（WhisperLive 的 single model 模式）；每個 client 一個 session 物件（自己的 VAD 狀態、音訊緩衝、語言設定），推論請求進入有界佇列，以 round-robin 跨 session 取 chunk 以求公平（避免某個 client 霸佔 GPU）。`max_clients` 預設可設 2–4，滿了在交握階段回「server busy（含目前人數）」錯誤碼，不要讓連線掛著。
- **協定版本**：連線第一個訊息交換 `protocol_version`（major.minor）、app 版本與能力旗標（例如支援的模型、翻譯是否可用）；major 不符直接拒絕並在 UI 顯示「請將兩台更新到同一版」，minor 不同則以能力旗標降級。WhisperLive / Ollama 文件中未見類似機制，屬設計建議。
- **無頭服務**：Linux 用 systemd unit（`Restart=on-failure`、以專用使用者執行、`Environment=` 設定，與 Ollama 相同模式）；Windows 可用 Windows service 或使用者登入時的工作排程／系統匣常駐。注意 Windows service 跑在 Session 0、沒有桌面，配對碼需改由 CLI／Web 管理頁顯示。
- **資源限制**：限制單 session 最長時長（WhisperLive 600 秒預設對直播／口譯太短，kikitori 應可設定或改為閒置逾時）、限制佇列長度與排隊逾時（Triton 模式）、VRAM 不足時拒絕新 session 而非 OOM。
- **翻譯**：B 機上的 Ollama 維持綁 127.0.0.1，由 kikitori 伺服器呼叫；`OLLAMA_NUM_PARALLEL=1` 時多 client 翻譯會排隊，要把佇列延遲納入字幕延遲預算。

### Gaps
- 未取得 systemd／Windows Service 官方文件（Microsoft `sc.exe`／Service Control Manager、systemd.service man page）的直接引用。
- NVIDIA Riva 的多 client 併發與公平性設定未查證。
- 未找到任何上述專案採用明確「協定版本交握」的官方文件。

## 6. 可重用函式庫授權（須與 MIT 相容）

### Takeaway
Rust 側 rustls、quinn、mdns-sd、RustCrypto `spake2` 都是 MIT／Apache-2.0（rustls 另加 ISC）可擇一，與 MIT 專案相容；Python 側 `python-zeroconf` 是 LGPL-2.1-or-later，雖可動態連結使用，但對以 PyInstaller 等方式打包散布的桌面 App 會帶來額外義務，建議把 mDNS 放在 Rust 端。RustCrypto `spake2` 未經正式安全審查，使用需謹慎。

### Cited Findings
- rustls：Apache-2.0、MIT、ISC 三擇一；支援 TLS 1.2/1.3 client 與 server；有 OpenSSF Best Practices 徽章 — [rustls GitHub](https://github.com/rustls/rustls)
- quinn：LICENSE-APACHE 與 LICENSE-MIT（Apache-2.0 / MIT）；預設以 rustls + ring 實作加密 — [quinn GitHub](https://github.com/quinn-rs/quinn)
- mdns-sd：Apache-2.0 或 MIT 擇一；支援 macOS/Linux/Windows；不依賴 async runtime — [mdns-sd GitHub](https://github.com/keepsimple1/mdns-sd)
- RustCrypto PAKEs（`spake2`、`srp`、`aucpace`）：Apache-2.0 或 MIT 擇一；「have not yet received any formal cryptographic and security reviews」、未做記憶體中秘密清除、「USE AT YOUR OWN RISK」 — [RustCrypto/PAKEs GitHub](https://github.com/RustCrypto/PAKEs)
- python-zeroconf：「GNU Lesser General Public License v2.1 or later (LGPL-2.1-or-later)」 — [python-zeroconf GitHub](https://github.com/python-zeroconf/python-zeroconf)
- WhisperLive：MIT（可參考其多 client 架構或程式碼） — [WhisperLive GitHub](https://github.com/collabora/WhisperLive)

### Inferences
- 因 RustCrypto `spake2` 未經審查，較保守的配對設計是「不自己做 PAKE」：用 rustls mutual TLS 建立通道後，以配對碼對雙方憑證指紋（或 TLS exporter 值）做 HMAC 確認（短驗證碼綁定通道），只用經審查的原語（HMAC-SHA256），並限制嘗試次數。這也符合 OWASP「不要自己寫驗證邏輯」的精神——但這仍是自訂協定，需要設計審查。
- 若 ASR 伺服器在 Python engine 中實作，TLS 可用 Python 標準函式庫 `ssl`（PSF 授權，與 MIT 相容），避免新增依賴；Rust 端（Tauri）作為 client 用 rustls 自訂 `ServerCertVerifier` 做 pinning。
- 新增任何依賴前依專案規則需先問使用者並固定版本。

### Gaps
- Python 端 `spake2`（PyPI）、`cryptography`、`websockets` 的授權本次未查證。
- 未確認 python-zeroconf 是否有 MIT 授權的替代 Python 套件。
- LGPL 對 PyInstaller 打包散布的具體義務屬法律解讀，本次未找權威來源。
