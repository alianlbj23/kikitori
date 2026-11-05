# pyobjc-core 13.0 授權（補充檔）

macOS wheel（`cp312-macosx_10_13_universal2`）沒有附授權檔，`build.py` 打包時從這個
目錄補上。`License.txt` 是原文，未修改；取得日期 2026-11-05。

| 檔案 | 授權 | 來源 |
|---|---|---|
| `License.txt` | MIT | <https://github.com/ronaldoussoren/pyobjc/blob/v13.0/pyobjc-core/License.txt>（tag `v13.0` = `f5d40980cbb35c2437b8e7a0c91633975165d8b7`） |

授權檔提到的 libffi 是另一個產品：macOS 版 `_objc.cpython-312-darwin.so` 連結的是系統的
`/usr/lib/libffi.dylib`，wheel 裡沒有 libffi，所以不另附它的授權。
