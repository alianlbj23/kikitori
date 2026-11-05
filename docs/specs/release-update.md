# Spec: release-update（Tauri CI、發布流程、自動更新）

> 狀態：已採用（2026-11-05，Milestone 8 後半＋Milestone 9，使用者授權依建議自主實作）。
> 上位文件：`docs/specs/app-architecture.md` 的「版本號」「CI/CD」；引擎打包見 `packaging-ci.md`，app 端更新流程見 `ui.md`「自動更新」。本文件補充 CI、發布與更新器的細節。

## 範圍

| 項目 | 位置 |
|---|---|
| Tauri 程式的檢查（前端、Rust、授權） | `.github/workflows/app.yml` |
| 發布（gate、測試、三平台打包、簽章驗證、Release） | `.github/workflows/release.yml` |
| 發布判斷、整理安裝檔、驗證簽章、產生 `latest.json` | `scripts/release.py`（測試：`tests/test_release.py`） |
| Rust 依賴授權規則 | `app/src-tauri/deny.toml` |
| app 的第三方授權聲明（Rust、npm、kikitori 的 LICENSE） | `packaging/app/licenses.py`、`app/src-tauri/about.toml`（見 `packaging-ci.md`） |
| 版本號同步 | `scripts/sync_version.py`（見 `packaging-ci.md`） |

## 與上位文件的差異

- 架構規格把前端與 Rust 檢查寫在 `test.yml`；實作放在獨立的 `app.yml`（觸發條件相同：任何分支的 push 與 PR），`test.yml` 只留 Python 與引擎打包。兩者都有 `workflow_call`，`release.yml` 重用它們。
- 架構規格寫「先跑測試，通過才打包」。實作讓三平台打包與測試**平行**執行（省下約 20 分鐘），但建立 Release 的 job 需要測試、打包、簽章驗證全部成功——沒有通過測試的版本不會發布，結果相同。

## CI：`app.yml`

| job | 矩陣 | 步驟 |
|---|---|---|
| `checks` | windows-latest、ubuntu-latest、macos-latest | Linux 先裝 Tauri v2 依賴（WebKitGTK 4.1 等，依 v2.tauri.app 的清單）→ `npm ci` → `npm run typecheck` → `npm test`（vitest）→ `npm run build` → `cargo fmt --check` → `cargo clippy --locked --all-targets -- -D warnings` → `cargo test --locked` |
| `licenses` | ubuntu-latest | 下載 cargo-deny 0.20.2 發行檔（核對 SHA-256）→ `cargo-deny --locked check licenses` → 下載 cargo-about 0.9.2 發行檔（核對 SHA-256）→ `npm ci` → `packaging/app/licenses.py --out`（確認 app 的第三方授權聲明產生得出來，README 與 MPL 原始碼說明寫進 step summary） |

- `cargo test` 的端到端測試以真的子行程執行引擎（`uv run kikitori engine`、`app/dev/fake_engine.py`），所以 job 先裝 uv 並 `uv sync --locked`；不略過這些測試。
- 前端要先 `npm run build`：`tauri::generate_context!` 在編譯時嵌入 `app/dist`。
- Rust 固定 1.99.1（與本機相同；新版 clippy 的 lint 不會讓 CI 無預警失敗），Node 固定 24.20.0，uv 0.13.4。升級時三個 workflow 一起改。
- 不用 `cargo-deny-action`：它在執行時才建置 Docker 映像、下載 cargo-deny 時不驗雜湊。

### 授權檢查（`deny.toml`）

只看三個發布平台（`x86_64-pc-windows-msvc`、`x86_64-unknown-linux-gnu`、`aarch64-apple-darwin`）。允許：MIT、Apache-2.0、Apache-2.0 WITH LLVM-exception、BSD-3-Clause、ISC、Zlib、Unicode-3.0——都是實際遇到的；雙授權（例如 `MIT OR Unlicense`）以其中已允許的一項滿足。

例外（2026-11-05 實測，只放行單一 crate，不把 MPL-2.0 加進 allow）：

| crate | 來源 | 授權 | 理由 |
|---|---|---|---|
| `cssparser`、`cssparser-macros`、`dtoa-short`、`selectors` | tauri-utils → dom_query（Servo 專案） | MPL-2.0 | 檔案層級 copyleft，只約束這些 crate 自己的原始檔；原樣使用、原始碼在 crates.io 公開。Tauri 本身的依賴，無法移除 |
| `option-ext` | tauri → dirs → dirs-sys | MPL-2.0 | 同上 |

本機執行（cargo-deny 0.20.2 發行檔解壓後）：`cd app/src-tauri && cargo-deny --locked check licenses`。

### 散布時的授權聲明

MIT／Apache-2.0／MPL-2.0 等授權要求散布執行檔時附上授權聲明，MPL-2.0 §3.2 還要告知取得原始碼的方式。引擎有自己的 `THIRD_PARTY_LICENSES/`；Rust crate、前端 npm 套件與 kikitori 自己的 LICENSE 由 `packaging/app/licenses.py`（cargo-about 0.9.2 ＋ `package-lock.json`）在每次發布建置時產生成 `licenses/`，以 `bundle.resources` 放進安裝檔，`rust/MPL-2.0-SOURCE.txt` 列出上表五個 MPL-2.0 crate 的 crates.io 與上游原始碼位置。範圍、輸出與工具雜湊見 `packaging-ci.md`「app 的第三方授權聲明」；「關於」頁可開啟兩個資料夾（`ui.md`）。新增 MPL 例外時不必改產生程式：以 MPL 使用的 crate 會自動列入原始碼說明（只接受 crates.io 來源，否則產生失敗）。

## 發布：`release.yml`

### 觸發與模式

`on: push`（只限分支，tag 的 push 不觸發）。gate job 只在下列情況執行，其餘 push 整個 workflow 被略過（不佔 runner）：

| 情況 | mode | 結果 |
|---|---|---|
| 預設分支（main），tag `v<版本>` 還不存在 | `publish` | 測試＋三平台打包 → 建立 GitHub Release |
| main，tag 已存在（版本沒變） | `skip` | 什麼都不做（`test.yml`／`app.yml` 本身已在這次 push 執行） |
| 其他分支，head commit 訊息含 `[release-dry-run]` | `dry-run` | 測試＋三平台打包＋簽章驗證＋產生 `latest.json`，只上傳成 workflow artifacts |

- 版本號取自 `pyproject.toml` 的 `[project].version`；gate 先跑 `sync_version.py check`（tauri.conf.json、Cargo.toml、package.json、Cargo.lock、package-lock.json 都必須相同）。
- publish 時新版本必須比既有每個 `v*` tag 都新（SemVer 比較），以免 `releases/latest` 指向舊版。版本有預發布識別字（`1.2.0-rc.1`）時建立 prerelease：它不會成為 latest，已安裝的 app 不會更新到它。
- `workflow_dispatch` 只對預設分支上的 workflow 有效，所以試建置用 commit 訊息觸發。一次 push 多個 commit 時只看最後一個（`head_commit`）。

### job

```text
gate ──┬─ test（重用 test.yml）──────────────────────────────┐
       ├─ app（重用 app.yml）────────────────────────────────┤
       └─ build ×3 ── assemble（驗簽章、latest.json）─────────┴─ publish（只在 publish 模式）
```

| job | 權限 | 內容 |
|---|---|---|
| `gate` | contents: read | 版本一致性、列出既有 tag、`release.py gate` 輸出 mode／version／tag／prerelease |
| `test`、`app` | contents: read | 與 push／PR 時相同的測試（skip 時不跑） |
| `build`（矩陣） | contents: read | 見下表 |
| `assemble` | contents: read | 下載三平台 artifacts → minisign 驗證每個 `.sig` → `release.py manifest` 產生 `latest.json`、整理發布檔案 → 上傳 `release-files` artifact |
| `publish` | **contents: write**（唯一） | 下載 `release-files` → 先建 draft Release 並上傳全部檔案 → 改成正式並標為 latest（避免使用者在上傳途中拿到缺檔的 `latest.json`） |

第三方 action 一律釘在完整 commit SHA（都選發布超過兩週的版本）：

| action | 版本 | SHA |
|---|---|---|
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| astral-sh/setup-uv | v10.3.0 | `1c37ad07a6a961277cf70c0d37d6f313000f5884` |
| actions/setup-node | v7.1.0 | `949feb2413d6458794dcd2491c4babbbce0c15c1` |
| Swatinem/rust-cache | v2.9.3 | `d1527c8f67ef9af357290475d4af4b0fab8f12e0` |
| actions/upload-artifact | v7.0.2 | `cf430e030ddbb5b0abf93d22962f4752f3646cd9` |
| actions/download-artifact | v8.0.2 | `9000827ccba6bdab643e8b6fd33ac0654aef8333` |

不用 `tauri-apps/tauri-action`：它在每個建置 job 直接上傳到 Release，建置 job 就得有寫入權限，某個平台失敗時也會留下不完整的 Release。`latest.json` 改由 `scripts/release.py` 依 Tauri v2 的靜態 JSON 格式產生（見下）。

### build（每個平台）

| 平台 | runner | `--bundles` | 發布的檔案 |
|---|---|---|---|
| Windows x64 | windows-latest | `nsis` | `kikitori_<v>_x64-setup.exe`＋`.sig` |
| Linux x64 | ubuntu-22.04 | `appimage,deb` | `kikitori_<v>_amd64.AppImage`＋`.sig`、`kikitori_<v>_amd64.deb`＋`.sig` |
| macOS arm64 | macos-latest | `app,dmg` | `kikitori_<v>_aarch64.dmg`、`kikitori_<v>_aarch64.app.tar.gz`＋`.sig`（Tauri 輸出 `kikitori.app.tar.gz`，改名加上版本與架構） |

Linux 在 ubuntu-22.04 建置：AppImage／deb 依賴建置機的 glibc，舊版 Ubuntu 能涵蓋較多發行版。

步驟：`uv sync --locked` → `build.py --smoke`（建置引擎、冒煙測試、放到 `app/src-tauri/binaries/kikitori-engine/`）→ `npm ci` → 下載該平台的 cargo-about 0.9.2 發行檔（核對 matrix 裡的 SHA-256）→ `packaging/app/licenses.py --out app/src-tauri/licenses`（app 的第三方授權聲明）→ 準備更新器簽章金鑰 → `npm run tauri build -- --config src-tauri/tauri.sidecar.conf.json --bundles …`（引擎與 `licenses/` 以 `bundle.resources` 帶入）→ Linux 若 Tauri 沒有簽 deb，以 `tauri signer sign --app-version <v>` 補簽 → 安裝後冒煙測試 → `release.py collect` 以固定名稱整理 → 上傳 `bundle-<平台>` artifact。

安裝後冒煙測試（`smoke_test.py --binary <路徑> --self-check --expect-version <v>`，對打包**之後**的引擎；同一個資源目錄再跑 `packaging/app/licenses.py --check-bundle <資源目錄>`，確認 `licenses/` 與 `kikitori-engine/THIRD_PARTY_LICENSES/` 都在安裝檔裡、每個 MPL crate 都有原始碼說明）：

| 平台 | 做法 |
|---|---|
| Windows | NSIS 安裝檔 `/S /D=<暫存目錄>` 靜默安裝，對安裝目錄的 `kikitori-engine\kikitori-engine.exe` 測試；授權資料夾以安裝目錄為資源目錄 |
| Linux | `apt-get install ./kikitori_<v>_amd64.deb`，檢查 `/usr/lib/kikitori/kikitori-engine/kikitori-engine` 有執行權限並測試；AppImage 以 `--appimage-extract` 解開，對 `squashfs-root/usr/lib/kikitori/kikitori-engine/` 同樣檢查；授權資料夾以 `/usr/lib/kikitori`、`squashfs-root/usr/lib/kikitori` 為資源目錄 |
| macOS | 對 `.app`、掛載的 DMG 內的 `.app`、更新用 tarball 解開的 `.app` 各做：`codesign --verify --deep --strict`、引擎執行權限、引擎本身的 `codesign --verify --strict`、冒煙測試、授權資料夾（`Contents/Resources`） |

macOS 不做付費簽章與公證：`tauri.conf.json` 的 `bundle.macOS.signingIdentity` 設為 `"-"`（ad-hoc 簽章；Apple Silicon 上所有程式碼都必須有簽章）。PyInstaller 已對引擎的每個 Mach-O 做 ad-hoc 簽章，上面的 codesign 檢查確認 Tauri 簽 `.app` 時沒有破壞它們。

### 更新器簽章金鑰

| 模式 | 金鑰 |
|---|---|
| publish | GitHub Secrets 的 `TAURI_SIGNING_PRIVATE_KEY`、`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`；沒設定時 build 立即失敗並說明 |
| dry-run | 每個平台當場以 `tauri signer generate` 產生拋棄式金鑰（隨機密碼，已遮罩），**不讀任何 secret**；公鑰隨 artifact 上傳（`updater-pubkey.txt`）供 assemble 驗證 |

assemble 在 publish 模式以 `tauri.conf.json` 的公鑰（app 內建、用來驗證更新的那一把）驗證每個 `.sig`：Secrets 裡的私鑰若與公鑰不符，已安裝的 app 會拒絕更新而且沒人會發現，所以在發布前擋下。試建置改用各平台的拋棄式公鑰驗證，同一套流程都會走過。

## 自動更新（Milestone 9）

### `latest.json`

端點（`tauri.conf.json` 的 `plugins.updater.endpoints`）：`https://github.com/alianlbj23/kikitori/releases/latest/download/latest.json`，也就是最新一個非 prerelease 的 Release 裡的 `latest.json`。格式（Tauri v2 靜態 JSON）：

```json
{
  "version": "0.2.0",
  "notes": "kikitori v0.2.0",
  "pub_date": "2026-11-05T08:30:15Z",
  "platforms": {
    "darwin-aarch64":        { "signature": "<.sig 的內容>", "url": "https://github.com/alianlbj23/kikitori/releases/download/v0.2.0/kikitori_0.2.0_aarch64.app.tar.gz" },
    "darwin-aarch64-app":    { "...": "同上" },
    "linux-x86_64":          { "signature": "…", "url": "…/kikitori_0.2.0_amd64.AppImage" },
    "linux-x86_64-appimage": { "...": "同上" },
    "linux-x86_64-deb":      { "signature": "…", "url": "…/kikitori_0.2.0_amd64.deb" },
    "windows-x86_64":        { "signature": "…", "url": "…/kikitori_0.2.0_x64-setup.exe" },
    "windows-x86_64-nsis":   { "...": "同上" }
  }
}
```

- tauri-plugin-updater（2.14.1）先找 `{os}-{arch}-{installer}`，再找 `{os}-{arch}`；installer 是打包時寫進執行檔的類型（`nsis`、`appimage`、`deb`、`app`）。所以 deb 安裝的使用者拿到 `.deb`（外掛以 pkexec／圖形化 sudo 執行 `dpkg -i`），AppImage 使用者拿到 AppImage。少了 `linux-x86_64-deb` 的話，deb 使用者會下載到 AppImage 而安裝失敗。
- `signature` 是 `.sig` 檔的內容本身（不是路徑或網址）。
- `url` 指向 `releases/download/v<版本>/`（不是 `latest/download/`），舊的 `latest.json` 永遠指向它自己那一版的檔案。
- `createUpdaterArtifacts: true`（v2 格式）：Windows 直接用 NSIS 安裝檔、Linux 直接用 AppImage，macOS 是 `.app.tar.gz`。`tauri build` 會把版本號寫進簽章的 trusted comment（`requireSignedVersion` 可據此拒絕版本號被竄改的 manifest；目前未開啟，deb 補簽時也帶 `--app-version`，所以之後可以開）。
- `release.py manifest` 檢查三個必要平台都在、每個檔案恰好出現一次、簽章是 base64 的 minisign 格式。

### app 端流程（`app/src/main/updater.ts`，見 `ui.md`）

正式版啟動時檢查一次（15 秒逾時，失敗不打擾）；設定頁「關於」可手動檢查。找到新版 →「下載並安裝」→ 下載進度 → 外掛以內建公鑰驗證簽章 → 安裝 →「立即重新啟動」（`relaunch`）。檢查與安裝經由 app 自己的 `update_check`／`update_install` 指令（`app/src-tauri/src/updater.rs`）呼叫外掛的 Rust API，而不是外掛的 JavaScript API：只有這樣才能換掉 `on_before_exit`，在 Windows 安裝程式覆寫檔案前先關閉引擎（細節見 `ui.md`「自動更新」）。

| 平台 | 安裝方式 |
|---|---|
| Windows | `on_before_exit`：送 `engine.shutdown`，3 秒內沒結束就強制結束引擎（外掛隨後直接 `std::process::exit`，不經過 `RunEvent::Exit`）→ 執行新的 NSIS 安裝檔（`installMode: passive`：只顯示進度，通常會自行關閉並重開程式） |
| macOS | 解開 `.app.tar.gz`，以 rename 替換目前的 `.app`。經由更新器下載的檔案不帶隔離屬性，所以不會再出現 Gatekeeper 警告 |
| Linux AppImage | 以新的 AppImage 取代目前檔案（需要對該檔案所在目錄有寫入權限） |
| Linux deb | `pkexec dpkg -i`（或 zenity／kdialog 詢問密碼、最後退回終端機 sudo） |

## 發布流程（維護者）

1. 在功能分支升版：`uv run python scripts/sync_version.py`（互動選單）選「升 patch／minor／major 版本並同步」。它改 `pyproject.toml`、同步 app 的設定檔與 lock 檔，並執行 `uv lock`。
2. （建議）在同一分支 push 一個訊息含 `[release-dry-run]` 的 commit，確認三平台試建置成功；到該次 workflow run 的 artifacts 下載安裝檔試裝。
3. 合併到 main。`release.yml` 會跑測試與三平台打包，全部成功後建立 Release `v<版本>`（附安裝檔、`.sig`、`latest.json`）。
4. 到 Release 頁面確認檔案齊全；已安裝的 app 下次啟動就會看到更新。

失敗時：修正後再 push 到 main 即可（tag 還沒建立，gate 仍判斷為 publish）。若 publish 在上傳途中失敗留下 draft Release，先到 GitHub 刪除該 draft 再重跑（publish 遇到同名 Release 會停下，不會覆蓋）。

試建置：

```text
git commit --allow-empty -m "試建置 [release-dry-run]"
git push
```

### GitHub Secrets（Settings → Secrets and variables → Actions → Repository secrets）

| 名稱 | 內容 |
|---|---|
| `TAURI_SIGNING_PRIVATE_KEY` | 私鑰檔 `C:\Users\alianlbj23\.tauri\kikitori-updater.key` 的**完整內容**（一行 base64） |
| `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` | `C:\Users\alianlbj23\.tauri\kikitori-updater.key.password` 的內容（不要多出結尾換行） |

可用 GitHub CLI 從檔案設定，內容不會出現在畫面或 shell 歷史（PowerShell）。私鑰用 cmd 的重新導向原樣送進 stdin（PowerShell 5.1 的管線會在結尾多加換行）；密碼去掉結尾換行後以參數傳入：

```text
cmd /c "gh secret set TAURI_SIGNING_PRIVATE_KEY < C:\Users\alianlbj23\.tauri\kikitori-updater.key"
gh secret set TAURI_SIGNING_PRIVATE_KEY_PASSWORD --body (Get-Content -Raw C:\Users\alianlbj23\.tauri\kikitori-updater.key.password).TrimEnd("`r", "`n")
```

也可以在 GitHub 網頁直接貼上檔案內容。

私鑰與密碼只放在維護者本機與 GitHub Secrets，不進 repo、不印到 log。遺失私鑰就無法再發布已安裝的 app 接受的更新（只能請使用者手動重裝帶新公鑰的版本），請另外備份。密碼錯誤時 `tauri build` 會失敗；私鑰與 app 公鑰不符時 assemble 的驗證會失敗——兩者都發生在建立 Release 之前。

## 給使用者的安裝說明（未簽章程式）

- **macOS**：沒有 Apple 開發者簽章與公證（只有 ad-hoc 簽章），從瀏覽器下載的 DMG 第一次開啟會被 Gatekeeper 擋下。把 kikitori 拖到「應用程式」後開啟一次，再到「系統設定 → 隱私權與安全性」，在下方找到 kikitori 按「強制打開」（macOS 15 起不能再用右鍵「打開」略過）。進階使用者也可以執行 `xattr -dr com.apple.quarantine /Applications/kikitori.app`。之後的自動更新不會再出現這個警告。若出現「已損毀，無法打開」而不是「無法驗證開發者」，代表簽章有問題，請回報。
- **Windows**：安裝檔沒有 Authenticode 簽章，SmartScreen 會顯示「Windows 已保護您的電腦」，選「其他資訊 → 仍要執行」。
- **Linux**：AppImage 要先 `chmod +x`。它用的是 AppImage type2-runtime（靜態連結 libfuse3），**不需要 libfuse2**，但要有 `/dev/fuse` 與 `fusermount`／`fusermount3`（Debian／Ubuntu 套件 `fuse3`，桌面版通常已安裝）。缺少時會顯示「Cannot mount AppImage, please check your FUSE setup.」，可安裝 `fuse3`，或不掛載直接執行：`./kikitori_<v>_amd64.AppImage --appimage-extract-and-run`（或設環境變數 `APPIMAGE_EXTRACT_AND_RUN=1`，每次啟動會先解壓到 `/tmp`，較慢）。介面與字幕需要中日文字型；沒有 Noto CJK 的精簡系統請安裝 `fonts-noto-cjk`（deb 已列為 Recommends）。deb 以 `sudo apt install ./kikitori_<v>_amd64.deb` 安裝。（2026-11-05 容器實測，見 `experiments/linux_acceptance/README.md`。）

## 需要實機確認

- 三平台從 Release 下載安裝、啟動、引擎啟動（正式資源目錄路徑）、「關於」頁的兩個授權資料夾按鈕實際在檔案管理員開啟（`licenses/` 與 `kikitori-engine/THIRD_PARTY_LICENSES/`；CI 只檢查檔案在安裝檔內）。
- 自動更新的完整一輪：裝 `v<n>` → 發布 `v<n+1>` → app 偵測、下載、驗證、安裝、重新啟動後版本正確。第一個 Release 之後才能測（需要兩個版本）。
- macOS：下載的 DMG 在 Gatekeeper 的實際提示（應是「無法驗證開發者」而非「已損毀」）、強制打開後能正常執行、自動更新替換 `.app` 後引擎仍可執行（ad-hoc 簽章、執行權限）、螢幕錄製權限在更新後是否要重新授權（ad-hoc 簽章的程式每版簽章不同，TCC 可能視為新程式）。
- Linux：Wayland 桌面；deb 更新時 pkexec 的提示；AppImage 自我更新需要對檔案所在目錄的寫入權限。（沒有 libfuse2／fusermount 時 AppImage 的行為已在容器確認，見上方安裝說明。）
- Windows：SmartScreen 提示、passive 模式更新後自動重開、安裝在使用者目錄（currentUser）時的更新權限；真實模型推論中按「下載並安裝」時，`on_before_exit` 關閉引擎的實際耗時（單元測試以 `fake_engine.py` 驗證流程與期限）。
