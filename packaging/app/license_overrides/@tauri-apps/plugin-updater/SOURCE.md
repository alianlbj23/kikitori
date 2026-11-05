# @tauri-apps/plugin-updater 授權全文（補充檔）

npm 套件 `@tauri-apps/plugin-updater` 只附 `LICENSE.spdx`（SPDX metadata，沒有授權全文），
`packaging/app/licenses.py` 產生前端 bundle 的授權聲明時從這個目錄補上。檔案是原文，未修改；
取得日期 2026-11-05。

| 檔案 | 授權 | 來源 |
|---|---|---|
| `LICENSE_MIT` | MIT | tauri-apps/plugins-workspace @ `44293a5722efcaf27060504e63b17aeba721b40f`（tag `opener-js-v2.7.2`）`plugins/updater/LICENSE_MIT`，git blob `4d75472566358fac0eed03625b37d1837a33dfe2` |
| `LICENSE_APACHE-2.0` | Apache-2.0 | 同上 `plugins/updater/LICENSE_APACHE-2.0`，git blob `4947287f7b5ccb5d1e8b7b2d3aa5d89f322c160d` |

這兩個檔案在 `updater-js-v2.14.1`（本專案使用的版本）的 tag 上與上表完全相同（git blob 相同）。
升級套件時重新核對；若之後的 npm 套件附上了授權全文，可刪除這個目錄。
