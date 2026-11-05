# 驗收：Windows 安裝檔與自動更新

> 暫時性驗收工具，不屬於正式程式碼。把 `docs/specs/release-update.md`「需要實機確認」裡的
> **自動更新完整一輪**（裝 `v<n>` → 發布 `v<n+1>` → 偵測、下載、驗證、安裝、重新啟動）在本機跑完，
> 不需要真的發布 GitHub Release。

## 怎麼跑

需要：Windows 10/11 x64、uv、Node 24、Rust（MSVC）、WebView2 Runtime（版本要等於腳本裡的
`EDGEDRIVER_VERSION`，不同時改常數）。

```text
uv run python experiments/update_acceptance/update_acceptance.py            互動選單（編號）
uv run python experiments/update_acceptance/update_acceptance.py --all      全部依序執行（非互動）
uv run python experiments/update_acceptance/update_acceptance.py --steps manifest,scenario
```

選項：`--work-dir`（預設 `%TEMP%\kikitori-update-acceptance`，所有產物都在這裡，跑完可整個刪除）、
`--port`（本機更新伺服器，預設 18731）、`-v`。結果寫在 `<work-dir>\results.json`，
建置與工具的輸出在 `<work-dir>\logs\`。

| 步驟 | 內容 |
|---|---|
| `tools` | minisign 0.12（winget-pkgs 記載的 SHA-256 核對）、msedgedriver（Microsoft Authenticode 簽章與版本核對）、`cargo install tauri-driver --version =2.1.0 --locked --root <work>\tools\tauri-driver`。都裝在工作目錄，不動系統 |
| `engine` | 與 CI 相同：`KIKITORI_BUILD_NONINTERACTIVE=1 build.py --smoke`（放到 `app/src-tauri/binaries/kikitori-engine/`） |
| `build` | `tauri signer generate` 產生**拋棄式**金鑰（隨機密碼，存在工作目錄）；`npm run tauri build -- --config src-tauri/tauri.sidecar.conf.json --config <覆寫> --bundles nsis` 建置 0.1.0 與 0.1.1，`CARGO_TARGET_DIR` 在工作目錄 |
| `manifest` | `scripts/release.py collect`（Windows）→ `verify --pubkey-from-config`（minisign）→ `manifest`，再產生本機伺服器用的版本 |
| `scenario` | 安裝 0.1.0 → 啟動 → 壞簽章 → 更新到 0.1.1 → 重新啟動後檢查 → 同版本 → 解除安裝（見下） |

### 覆寫設定（不改 repo 的設定檔）

`<work>\cfg\override-<版本>.json` 以第二個 `--config` 合併（JSON merge patch）：`version`、
`plugins.updater.pubkey`（拋棄式公鑰）、`plugins.updater.endpoints`
（`http://127.0.0.1:<port>/latest.json`）、`plugins.updater.dangerousInsecureTransportProtocol: true`
（tauri-plugin-updater 2.14.1 `src/config.rs` 的名稱；沒有它，http 端點在讀設定時就被拒絕）。
`windows.installMode: passive` 沿用 `tauri.conf.json`。

### `latest.json`

`release.py` 的版本號取自 `pyproject.toml`、產品名稱與公鑰取自 `tauri.conf.json`，所以用 `--root`
指到工作目錄裡的假 repo（只有 `pyproject.toml` 版本 0.1.1，與換成拋棄式公鑰的 `tauri.conf.json`）。
`manifest` 要求三個平台都在：Linux／macOS 本機沒有實際檔案，以拋棄式金鑰簽章的佔位檔代替（只為了讓
產生器與 minisign 驗證走完；伺服器不提供它們）。

產生器輸出的 `url` 指向 GitHub（`releases/download/v0.1.1/…`）。本機伺服器提供的 `good.json`
只把兩個 Windows 鍵的 `url` 換成 `http://127.0.0.1:<port>/kikitori_0.1.1_x64-setup.exe`，簽章原樣；
`bad-signature.json` 是同一個安裝檔、但簽章來自另一把拋棄式金鑰（模擬簽錯或被竄改）。

### scenario 的檢查

驅動 UI 用 WebDriver（tauri-driver → msedgedriver → WebView2，標準庫 HTTP 客戶端），不需要在
正式程式裡加測試用的開關。程式的環境變數帶 `KIKITORI_CONFIG_DIR=<work>\config`，引擎的
`settings.json` 不碰使用者的設定。若 HKCU 已有 kikitori 的解除安裝項目（使用者裝過），腳本直接拒絕執行。

1. `kikitori_0.1.0_x64-setup.exe /S /D=<work>\install`（per-user）；HKCU Uninstall 有 0.1.0 的項目。
2. 啟動已安裝的程式：「關於」顯示 `kikitori 0.1.0`、引擎已連線；行程樹裡引擎是
   `<install>\kikitori-engine\kikitori-engine.exe` 且父行程是安裝目錄的主程式；app 日誌
   （`%LOCALAPPDATA%\io.github.alianlbj23.kikitori\logs\kikitori.log`，只讀測試期間新增的部分）
   有 `starting engine: …\kikitori-engine.exe` 與 `engine bridge Ready`。
3. 設定頁把「口譯對方的語言」改成英語並儲存（`settings.json` 的 `partner_language` = `en`）。
4. 壞簽章：手動「檢查更新」找到 0.1.1 →「下載並安裝」→ 出現「更新失敗：…」，主程式雜湊不變、
   仍是 0.1.0。
5. 正常更新：關閉程式，伺服器換成 `good.json`，重新啟動 → **啟動時的檢查**找到 0.1.1 →
   「下載並安裝」→ 程式結束、NSIS（passive）安裝並重開程式 → 新的主程式行程與引擎子行程、
   Uninstall 項目變成 0.1.1、主程式的 ProductVersion 0.1.1。
6. 關閉自動重開的程式後再以 WebDriver 啟動：「關於」`kikitori 0.1.1`、引擎啟動、設定仍是英語
   （畫面與 `settings.json`）；「檢查更新」對同版本的 manifest 顯示「目前已是最新版本。」
7. 清理（失敗也會執行）：關閉程式、`uninstall.exe /S _?=<install>`、刪除安裝目錄；確認 HKCU
   Uninstall 沒有 kikitori、捷徑與測試前相同、沒有殘留的 kikitori／driver 行程；
   `%APPDATA%\io.github.alianlbj23.kikitori` 若是這次測試才建立且只有 `.window-state.json`，一併移除。

## 2026-11-05 結果（本機 Windows 11、WebView2 156.0.4314.58）

`--all` 一次跑完，全部 PASS（`feat/full-app` 的 0b5c7c4；工具：minisign 0.12、msedgedriver
156.0.4314.58、tauri-driver 2.1.0；tauri CLI 2.12.4、tauri-plugin-updater 2.14.1）。

| 步驟 | 結果 | 證據 |
|---|---|---|
| 1 建置引擎 | PASS | `built dist\kikitori-engine (170.2 MB) in 15s`；`protocol ok: version 0.1.0 (windows/AMD64), 2 devices, 5 ASR models, 11 subtitle events (2 final), startup 0.30s` |
| 2 安裝檔 | PASS | `kikitori_0.1.0_x64-setup.exe`（42.6 MB）、`kikitori_0.1.1_x64-setup.exe`＋`.sig`（拋棄式金鑰簽章） |
| 2 manifest／簽章 | PASS | `release.py verify`（minisign）：`signature ok` × 4（Windows 安裝檔＋3 個佔位檔）；`latest.json`：`version 0.1.1`、`notes "kikitori v0.1.1"`、7 個平台鍵，Windows 的 `url` 為 `https://github.com/alianlbj23/kikitori/releases/download/v0.1.1/kikitori_0.1.1_x64-setup.exe` |
| 3 安裝 0.1.0、啟動 | PASS | Uninstall 項目 `DisplayVersion 0.1.0`、`MainBinaryName kikitori-app.exe`；「關於」`kikitori 0.1.0`、`引擎 0.1.0，協定第 1 版。`；行程樹 `install\kikitori-app.exe` → `install\kikitori-engine\kikitori-engine.exe`；日誌 `starting engine: \\?\…\install\kikitori-engine\kikitori-engine.exe`、`engine bridge Ready`；主程式 ProductVersion 0.1.0；設定「已儲存設定。」、`settings.json` `partner_language: en` |
| 6 壞簽章 | PASS | 伺服器收到 `/latest.json`、`/kikitori_0.1.1_x64-setup.exe`；畫面「更新失敗：The signature was created with a different key than the one provided」；之後仍是 `kikitori 0.1.0`、主程式雜湊不變、Uninstall 仍 0.1.0 |
| 4 更新（啟動時檢查） | PASS | 「kikitori 0.1.1 已經推出，可以下載並安裝。」→ 下載並安裝 → 幾秒內出現新的 `kikitori-app.exe` 行程（安裝程式 passive 重開）與引擎子行程；伺服器請求 `/latest.json`、安裝檔、`/latest.json`（重開後的啟動檢查）；Uninstall `DisplayVersion 0.1.1`；ProductVersion 0.1.1 |
| 5 更新後 | PASS | 「關於」`kikitori 0.1.1`、引擎從安裝目錄啟動並連線；設定仍是英語（畫面與 `settings.json`） |
| 6 同版本 | PASS | 「目前已是最新版本。」 |
| 7 清理 | PASS | 解除安裝 exit 0；安裝目錄已刪；HKCU Uninstall 沒有 kikitori；捷徑與測試前相同（都沒有）；沒有殘留的 kikitori／driver 行程；測試建立的 `%APPDATA%\io.github.alianlbj23.kikitori\.window-state.json` 已移除 |

### 觀察

- **更新時引擎的結束**：Windows 上 tauri-plugin-updater 啟動安裝程式後直接 `std::process::exit(0)`，
  不會經過 `RunEvent::Exit`，所以 `lib.rs` 的 `engine.shutdown`（3 秒後強制結束）**不會執行**；
  引擎是因為 stdin 關閉（EOF）才自己結束。量測（每 5 ms 檢查行程）：主程式結束後引擎
  **約 50 ms** 結束——閒置與假模式下 session 執行中都一樣，兩次更新都成功。NSIS 只用 Restart Manager
  檢查主程式；引擎的檔案若在覆寫時仍被佔用，安裝可能失敗（未實測）。真實模型推論中
  （大型 Whisper 在 CPU 上）引擎結束可能較慢，本機沒有驗證；若實機遇到，可在
  `tauri_plugin_updater::Builder::on_before_exit` 先關引擎。
- 安裝後的主程式叫 `kikitori-app.exe`（Cargo 的 bin 名稱；開始功能表捷徑叫 kikitori）。工作管理員裡
  看到的是 `kikitori-app`。規格沒有規定，只是記錄。
- 引擎版本來自 `pyproject.toml`，這裡只覆寫了 app 的版本，所以兩版的引擎都是 0.1.0；正式發布時
  `sync_version.py` 會讓兩者一致。
- 靜默解除安裝不刪 app 資料（`%APPDATA%\io.github.alianlbj23.kikitori` 的視窗位置、
  `%LOCALAPPDATA%\io.github.alianlbj23.kikitori` 的日誌與 WebView2 資料、`%APPDATA%\kikitori`
  的設定）；解除安裝程式的 UI 上是勾選項。更新也不動它們，所以設定會保留。
- 從本機建置的安裝檔沒有 Mark-of-the-Web，不會觸發 SmartScreen。

### 本機驗不到、要等真的 Release

- SmartScreen 對下載的未簽章安裝檔的提示；GitHub `releases/latest/download/latest.json` 的轉址與
  https 下載（這裡是 http://127.0.0.1，需要 `dangerousInsecureTransportProtocol`）。
- 正式公鑰（`tauri.conf.json`）與 GitHub Secrets 私鑰相符：由 `release.yml` assemble 的
  `verify --pubkey-from-config` 擋；這裡用拋棄式金鑰。
- Linux（AppImage／deb）與 macOS（`.app.tar.gz`）的更新安裝；佔位檔只驗證了 manifest 產生器。
- 真實模型 session 進行中按「下載並安裝」時引擎的結束時間（見上）。
