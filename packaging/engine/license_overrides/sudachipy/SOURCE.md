# SudachiPy 0.6.11 授權與內含的 Rust crate（補充檔）

`sudachipy` 的 wheel 沒有附授權檔，`build.py` 打包時從這個目錄補上（輸出到
`THIRD_PARTY_LICENSES/sudachipy-0.6.11/upstream/`）。所有授權檔都是原文，未修改；取得日期 2026-11-06。

| 檔案 | 元件 | 授權 | 來源 |
|---|---|---|---|
| `LICENSE` | SudachiPy／sudachi.rs 0.6.11 | Apache-2.0 | <https://github.com/WorksApplications/sudachi.rs/blob/v0.6.11/LICENSE>（tag `v0.6.11` = `90fd6068c80c2fc3b63e0dbab0e341475bad4d8f`） |
| `third_party/<crate>-<版本>/` | 編譯進 `sudachipy.*.pyd`／`.so` 的 Rust crate（下表） | 見下表 | `https://static.crates.io/crates/<crate>/<crate>-<版本>.crate` 內的授權檔 |

crate 清單：從 sudachi.rs `v0.6.11` 的 `Cargo.lock`，以 `python/Cargo.toml`（sudachipy）為起點、`sudachi/Cargo.toml`
只取 `[dependencies]`（不含 dev-dependencies 與測試用外掛）算出的相依閉包。標「建置時」的是 proc-macro 或 build script
用的 crate，只在編譯時執行、不在二進位檔裡，為了完整一併附上。wheel 若不是以這份 lockfile 建置，版本可能略有不同。

Sudachi 的**辭典**（SudachiDict，Apache-2.0，含 UniDic 的 BSD-3-Clause 條款與 NEologd 的聲明）不打包，
使用者打開振假名時才下載，授權全文（`LICENSE-2.0.txt`）與 `LEGAL` 隨辭典放在同一個資料夾（`docs/specs/furigana.md`）。

| crate | 版本 | 授權（Cargo.toml 的 `license`） | 用途 |
|---|---|---|---|
| aho-corasick | 1.1.4 | Unlicense OR MIT | 連結 |
| autocfg | 1.0.1 | Apache-2.0 OR MIT | 建置時 |
| bit-set | 0.5.3 | MIT/Apache-2.0 | 連結 |
| bit-vec | 0.6.3 | MIT/Apache-2.0 | 連結 |
| bitflags | 2.11.0 | MIT OR Apache-2.0 | 連結 |
| cfg-if | 1.0.4 | MIT OR Apache-2.0 | 連結 |
| csv | 1.4.0 | Unlicense/MIT | 連結 |
| csv-core | 0.1.13 | Unlicense/MIT | 連結 |
| either | 1.15.0 | MIT OR Apache-2.0 | 連結 |
| equivalent | 1.0.2 | Apache-2.0 OR MIT | 連結 |
| fancy-regex | 0.13.0 | MIT | 連結 |
| hashbrown | 0.16.1 | MIT OR Apache-2.0 | 連結 |
| heck | 0.5.0 | MIT OR Apache-2.0 | 建置時 |
| indexmap | 2.13.0 | Apache-2.0 OR MIT | 連結 |
| indoc | 2.0.7 | MIT OR Apache-2.0 | 建置時 |
| itertools | 0.13.0 | MIT OR Apache-2.0 | 連結 |
| itoa | 1.0.17 | MIT OR Apache-2.0 | 連結 |
| lazy_static | 1.5.0 | MIT OR Apache-2.0 | 連結 |
| libc | 0.2.182 | MIT OR Apache-2.0 | 連結 |
| libloading | 0.8.9 | ISC | 連結 |
| memchr | 2.8.0 | Unlicense OR MIT | 連結 |
| memmap2 | 0.9.10 | MIT OR Apache-2.0 | 連結 |
| memoffset | 0.9.1 | MIT | 連結 |
| minimal-lexical | 0.2.1 | MIT/Apache-2.0 | 連結 |
| nom | 7.1.3 | MIT | 連結 |
| once_cell | 1.21.3 | MIT OR Apache-2.0 | 連結 |
| portable-atomic | 1.13.1 | Apache-2.0 OR MIT | 連結 |
| proc-macro2 | 1.0.106 | MIT OR Apache-2.0 | 建置時 |
| pyo3 | 0.27.2 | MIT OR Apache-2.0 | 連結 |
| pyo3-build-config | 0.27.2 | MIT OR Apache-2.0 | 建置時 |
| pyo3-ffi | 0.27.2 | MIT OR Apache-2.0 | 連結 |
| pyo3-macros | 0.27.2 | MIT OR Apache-2.0 | 建置時 |
| pyo3-macros-backend | 0.27.2 | MIT OR Apache-2.0 | 建置時 |
| quote | 1.0.45 | MIT OR Apache-2.0 | 建置時 |
| regex | 1.12.3 | MIT OR Apache-2.0 | 連結 |
| regex-automata | 0.4.14 | MIT OR Apache-2.0 | 連結 |
| regex-syntax | 0.8.10 | MIT OR Apache-2.0 | 連結 |
| rustversion | 1.0.22 | MIT OR Apache-2.0 | 建置時 |
| ryu | 1.0.23 | Apache-2.0 OR BSL-1.0 | 連結 |
| scopeguard | 1.2.0 | MIT OR Apache-2.0 | 連結 |
| serde | 1.0.228 | MIT OR Apache-2.0 | 連結 |
| serde_core | 1.0.228 | MIT OR Apache-2.0 | 連結 |
| serde_derive | 1.0.228 | MIT OR Apache-2.0 | 建置時 |
| serde_json | 1.0.149 | MIT OR Apache-2.0 | 連結 |
| syn | 2.0.117 | MIT OR Apache-2.0 | 建置時 |
| target-lexicon | 0.13.5 | Apache-2.0 WITH LLVM-exception | 建置時 |
| thiserror | 1.0.69 | MIT OR Apache-2.0 | 連結 |
| thiserror-impl | 1.0.69 | MIT OR Apache-2.0 | 建置時 |
| thread_local | 1.1.9 | MIT OR Apache-2.0 | 連結 |
| tinyvec | 1.10.0 | Zlib OR Apache-2.0 OR MIT | 連結 |
| tinyvec_macros | 0.1.1 | MIT OR Apache-2.0 OR Zlib | 建置時 |
| unicode-ident | 1.0.24 | (MIT OR Apache-2.0) AND Unicode-3.0 | 建置時 |
| unicode-normalization | 0.1.25 | MIT OR Apache-2.0 | 連結 |
| unindent | 0.2.4 | MIT OR Apache-2.0 | 建置時 |
| windows-link | 0.2.1 | MIT OR Apache-2.0 | 連結 |
| yada | 0.5.1 | MIT OR Apache-2.0 | 連結 |
| zmij | 1.0.21 | MIT | 連結 |

更新 SudachiPy 版本時，以新 tag 的 `Cargo.lock` 重新產生本表與 `third_party/`。
