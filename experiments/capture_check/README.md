# 手動檢查：capture（聲音擷取）

> 暫時性檢查工具，不屬於正式程式碼。用來在真實機器上確認 loopback 與麥克風真的能開啟、
> 交付 int16／16 kHz／單聲道／一維的資料。規格見 `docs/specs/capture.md`。

## 怎麼跑

```text
uv run python experiments/capture_check/check_capture.py
```

互動選單（輸入數字後按 Enter；空白或 0 離開）：

| 選項 | 動作 |
|---|---|
| 1 | 列出裝置（`*` 為預設裝置，並顯示不透明 id） |
| 2 | 從清單選一個裝置錄音 |
| 3 | 錄預設 loopback（系統聲音） |
| 4 | 錄預設麥克風 |

錄音時每 0.5 秒顯示一次音量條（dBFS），結束後印出：dtype、維度、樣本數與實際秒數、
RMS／peak、裝置原生取樣率與聲道數、因緩衝滿而丟掉的樣本數，並存成
`data/<時間>_<kind>.wav`（16 kHz 單聲道 int16；`data/` 已被 git 忽略）。

檢查重點：

- 樣本數 ≈ 秒數 × 16000（loopback 在沒有聲音時也要有全零的樣本，否則 VAD 無法判斷句尾）。
- `每次 read 的格式正確=True`、`dropped_samples=0`。
- 有聲音時音量條會動；用播放器聽存下來的 WAV，確認沒有爆音、速度與音高正常。

## 2026-11-05 Windows 11 實測（本機）

| 來源 | 裝置 | 原生格式 | 結果 |
|---|---|---|---|
| 預設 loopback | 喇叭 (EMBERTON III) [Loopback] | 48 kHz 2ch | 4 s → 64000 樣本，開啟 0.047 s；沒有播放聲音，RMS 0（全零補值正確） |
| loopback（非預設） | 喇叭 (4- Sum-16) [Loopback] | 48 kHz 2ch | 2 s → 32000 樣本，RMS 0 |
| 預設麥克風 | 麥克風 (4- Sum-16) | 48 kHz 2ch | 4 s → 63680 樣本（開頭約 20 ms 啟動延遲），RMS 0.3（−100.5 dBFS，介面上沒接麥克風） |
| 麥克風 | 耳麥式麥克風 (EMBERTON III) | 16 kHz 1ch | 3 s → 47840 樣本，RMS 11.7（−68.9 dBFS 環境噪音），peak 127 |

沒有透過喇叭播放聲音（刻意不做），所以 loopback 只驗證了「能開啟、靜音時持續交付正確格式」，
有聲音時的內容正確性要在有播放時再跑一次選項 3 確認。

## 還沒在實機驗證、要手動確認的事

### macOS 13 以上（ScreenCaptureKit + CoreAudio）

1. `uv sync` 後，從「終端機」執行本工具（權限會記在終端機 app 上）。
2. 選項 1：清單第一項應為「系統聲音（ScreenCaptureKit）」且標 `*`，其後是 CoreAudio 麥克風。
3. 播放一段影片，選項 3 錄 5 秒。第一次會跳出「螢幕與系統錄音」權限詢問 → 允許 → 依提示重開終端機再跑一次。
   預期：約 80000 樣本、RMS > 0；聽 WAV 確認音高與速度正常（驗證 48 kHz、單聲道、`CMBlockBufferCopyDataBytes` 的解碼）。
4. **暫停播放**後再跑選項 3：確認樣本數仍約為秒數 × 16000。若明顯不足，代表 ScreenCaptureKit 在靜音時不送樣本，
   需要像 WASAPI 一樣補零（屆時修改 `_screencapturekit_backend.py`）。
5. 到「系統設定 → 隱私權與安全性 → 螢幕與系統錄音」關掉終端機的權限，再跑選項 3：應顯示 `PermissionDeniedError` 與開啟權限的說明。
6. 確認執行期間 Dock 沒有多出圖示、終端機沒有大量 `stream output NOT found` 之類的系統訊息。
7. 選項 4（麥克風）：允許麥克風權限後說話，RMS 應明顯大於 0。
8. 若步驟 3 出現與 WindowServer／CGS 連線相關的錯誤，記錄完整訊息（`_ensure_window_server_connection` 只在主執行緒初始化 NSApplication）。
9. 替代方案：安裝 BlackHole，建立含 BlackHole 的「多重輸出裝置」，選項 2 選 BlackHole 錄音，確認能錄到系統聲音。
10. macOS 12（若有機器）：選項 1 不應出現 ScreenCaptureKit 項目，選項 3 應顯示需要 macOS 13 或改用 BlackHole 的訊息。

### Linux（PulseAudio，或 PipeWire + pipewire-pulse）

1. 確認 `pactl info` 可用（有 PulseAudio 協定的伺服器與 `libpulse`）。
2. 選項 1：loopback 應為各 sink 的 `Monitor of …`，`*` 標在預設 sink 的 monitor；麥克風為一般 source。
   用 `pactl set-default-sink <名稱>` 換預設輸出後再列一次，`*` 應跟著移動。
3. 播放聲音，選項 3 錄 5 秒：約 80000 樣本、RMS > 0，聽 WAV 確認正常；停止播放再錄一次，樣本數仍應約為秒數 × 16000。
4. 選項 4：說話，RMS 應明顯大於 0。
5. 錄音中拔掉 USB 音效裝置：應顯示 `DeviceLostError`。
6. 在沒有 PulseAudio 伺服器的環境（純 ALSA）：選項 1 應顯示 `BackendUnavailableError`，而不是當掉。

### Windows（本機已驗證開啟與格式，尚缺）

1. 播放影片時跑選項 3，確認 RMS > 0、WAV 內容正確，且播放中途暫停時樣本數不中斷。
2. 錄音中拔掉 USB 耳機：應顯示 `DeviceLostError`。
3. 「設定 → 隱私權與安全性 → 麥克風」關閉桌面應用程式存取後跑選項 4：應顯示附隱私權提示的 `CaptureError`。
