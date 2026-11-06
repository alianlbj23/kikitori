# ADR-0002：暫不做「只擷取單一 App 的聲音」

## 狀態

已採用（Accepted）。2026-11-06 使用者授權依建議決定。

## 日期

2026-11-06

## 背景

看直播時常同時開著 Discord 或遊戲。它們的聲音會混進系統聲音，產生錯誤字幕。競品比較把「只擷取單一 App 的聲音」列為 P1（`research_notes/競品比較/commercial_products.md` 第 5 項）。

三個平台的做法各不相同：

- **Windows**：WASAPI process loopback（`AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK`，需要 build 20348 以上）。
  - 要用 `ActivateAudioInterfaceAsync`，並自己實作 `IActivateAudioInterfaceCompletionHandler` COM 物件。
  - 列出「正在發聲的 App」還要另外列舉 `IAudioSessionManager2`。
  - 目前的後端 `pyaudiowpatch` 不支援以上任何一項。
- **macOS**：ScreenCaptureKit 本身就能用 `SCContentFilter` 只包含特定 App。程式改動小，但團隊沒有 Mac 實機能驗證。
- **Linux**：要接 PipeWire／PulseAudio 的 sink-input，`soundcard` 不支援。

## 決策

這一版不做。擷取仍以輸出裝置為單位（`docs/specs/capture.md`）。

## 理由

- **Windows（主要平台）**：要用 ctypes 手寫 COM vtable 和非同步啟用流程。
  - 錯誤會讓引擎在擷取執行緒裡直接當掉，而不是丟出可處理的例外。
  - 擷取是整條流程的最上游，風險和收益不成比例。
- **其他兩個平台**：沒有實機。做了也只能靠 CI 編譯，無法驗收，等於三個平台行為不一致。
- **已有替代做法**：Windows 可以為每個 App 指定輸出裝置，把 Discord 或遊戲指定到另一個裝置。kikitori 擷取瀏覽器使用的那個裝置，就能避開混音。
  - Windows 11：「設定 → 系統 → 音效 → 音量混音程式」。
  - Windows 10：「應用程式音量與裝置喜好設定」。

## 考慮過的替代方案

- **只做 macOS**（ScreenCaptureKit 改動小）：無法實機驗證；而且平台差異會讓介面要分平台顯示。不採用。
- **在 Rust 端用 `wasapi` crate 擷取，再把音訊送給引擎**：要新增跨行程的音訊通道。這是協定與架構層級的改動，比本功能本身更大。不採用。

## 重新考慮的條件

符合任一項就重新評估：

- 有 Mac／Linux 實機可驗收。
- 出現維護中、支援 process loopback 的 Python 套件（並經使用者同意新增依賴）。
- 使用者回報混音是主要的錯誤字幕來源。
