# Silero VAD 模型檔

本資料夾的檔案隨 kikitori 套件一起發布，由 `kikitori.vad.silero` 以 `importlib.resources` 讀取。

| 項目 | 內容 |
|---|---|
| 檔案 | `silero_vad.onnx`（2,327,524 bytes） |
| 版本 | Silero VAD `v6.2.3`（commit `5cd7945676eb32225748052e2e6a0580e4686a08`） |
| 來源 | <https://github.com/snakers4/silero-vad/blob/5cd7945676eb32225748052e2e6a0580e4686a08/src/silero_vad/data/silero_vad.onnx> |
| SHA-256 | `1a153a22f4509e292a94e67d6f9b85e8deb25b4988682b7e174c65279d8788e3` |
| 授權 | MIT，Copyright (c) 2020-present Silero Team；全文見 `LICENSE-silero-vad`（取自同一 tag 的 `LICENSE`，未修改） |

## 模型輸入輸出（實際載入確認）

| 名稱 | 方向 | 形狀 | 型別 |
|---|---|---|---|
| `input` | 輸入 | `[batch, samples]`（本專案固定 `[1, 576]`：64 前文 + 512） | float32 |
| `state` | 輸入 | `[2, batch, 128]` | float32 |
| `sr` | 輸入 | 純量 | int64 |
| `output` | 輸出 | `[batch, 1]`，人聲機率 | float32 |
| `stateN` | 輸出 | `[2, batch, 128]`，下一次的 `state` | float32 |

## 更新模型

換版本時同時更新：模型檔、`silero.py` 的 `MODEL_VERSION` / `MODEL_COMMIT` / `MODEL_SHA256`、本 README、`LICENSE-silero-vad`，並重跑 `experiments/silero_reset/` 的評測。
