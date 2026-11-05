# Spec: capture（聲音擷取）

> 狀態：已採用（2026-11-05，Milestone 5）。上層約定見 `app-architecture.md` 的 capture 一節；本文件不得與其矛盾。

## 目標

從兩種來源擷取聲音，交給 VAD 前一律轉成 **int16、16 kHz、單聲道、一維** 的 `numpy.ndarray`：

| 來源 | `DeviceInfo.kind` | 用在哪個模式 |
|---|---|---|
| 系統播放的聲音（loopback） | `"loopback"` | `media`（影片翻譯） |
| 麥克風 | `"microphone"` | `interpret`（即時口譯） |

支援 Windows、Linux、macOS。單元測試不得開啟真實裝置，必須能在沒有音效卡的 CI 機器上通過。

## 介面（與 `app-architecture.md` 一致）

```python
class AudioSource(Protocol):
    def start(self) -> None: ...
    def read(self, timeout: float) -> np.ndarray: ...  # int16、16 kHz、單聲道、一維；逾時回傳空陣列
    def stop(self) -> None: ...

@dataclass(frozen=True)
class DeviceInfo:
    id: str
    name: str
    kind: Literal["loopback", "microphone"]
    is_default: bool

def list_devices() -> list[DeviceInfo]: ...
def open_source(device_id: str | None, kind: DeviceKind) -> AudioSource: ...
```

補充約定（契約沒寫死、由本模組決定的部分）：

- `read(timeout)`：等到有資料或逾時為止；有資料時一次回傳**目前累積的全部音訊**（串接成一個陣列），讓落後的消費者一次追上。`timeout` 必須是有限的非負數，否則 `ValueError`。
- `start()` 之前呼叫 `read()`、或重複呼叫 `start()`：`CaptureError`。`stop()` 可重複呼叫。`stop()` 之後 `read()` 先回傳尚未讀走的資料，之後立刻回傳空陣列。
- 擷取執行緒發生錯誤時，`read()` 先把已收到的資料交完，之後每次都拋出該錯誤（`CaptureError` 子類別）。
- `open_source(device_id, kind)`：`device_id` 為 `None` 時開啟該 `kind` 的預設裝置；有指定 `device_id` 時開啟該裝置，`kind` 不再影響選擇（讓 macOS 使用者能在 `media` 模式選 BlackHole 這類「輸入裝置」當系統聲音來源）。`open_source` 只建立物件，`start()` 才真的開啟裝置。
- 裝置 id 是不透明字串，格式 `<backend>:<kind>:<原生 id>`（例：`wasapi:loopback:喇叭 (EMBERTON III) [Loopback]`、`soundcard:loopback:alsa_output.pci.analog-stereo.monitor`、`screencapturekit:loopback:system`）。呼叫端只能拿 `list_devices()` 給的 id 原樣傳回，不得自行解析。
- 測試與管線用來源：`ArraySource`（已是 16 kHz int16 的陣列）、`FileSource`（WAV，可選依即時速度送出）。到結尾後 `read()` 回傳空陣列，`exhausted` 屬性為 `True`。

## 函式庫選擇

| 套件 | 版本 | 授權 | 平台標記 | 用途 |
|---|---|---|---|---|
| `PyAudioWPatch` | 0.2.12.9 | Apache-2.0（內含 PyAudio：MIT；PortAudio：MIT 式授權，靜態連結） | `sys_platform == 'win32'` | Windows WASAPI loopback + 麥克風 |
| `soundcard` | 0.4.6 | BSD-3-Clause | `sys_platform != 'win32'` | Linux PulseAudio（monitor + 麥克風）、macOS CoreAudio（麥克風） |
| `cffi`、`pycparser`（soundcard 的相依） | 2.1.1、3.12 | MIT-0、BSD-3-Clause | 同上 | soundcard 以 cffi 呼叫系統 API |
| `pyobjc-framework-ScreenCaptureKit` | 13.0 | MIT | `sys_platform == 'darwin'` | 系統聲音 |
| `pyobjc-framework-CoreMedia` | 13.0 | MIT | 同上 | 從 `CMSampleBuffer` 取出 PCM |
| `pyobjc-framework-libdispatch` | 13.0 | MIT | 同上 | 建立接收樣本的 serial dispatch queue |
| `pyobjc-framework-Cocoa`（含 `pyobjc-core`） | 13.0 | MIT | 同上 | 初始化 `NSApplication`（見下） |

- Linux 的 `libpulse` 是 LGPL-2.1，**由 soundcard 在執行期以 `dlopen` 動態載入系統上的函式庫**，不隨程式打包、不靜態連結；PyInstaller 打包時不得把 `libpulse.so` 收進去。
- **為什麼 Windows 不用 soundcard**：soundcard 0.4.6 的 WASAPI 實作直接修改 `GetMixFormat` 回傳的結構，並 `assert` 混音格式是 `WAVEFORMATEXTENSIBLE`（原始碼註解自承偏移量是「試出來的」）。實測本機預設麥克風（Sum-16 音訊介面）回報 `WAVE_FORMAT_IEEE_FLOAT`（`cbSize=0`），soundcard 直接 `AssertionError`，且就算拿掉 assert 也會寫出結構邊界。PyAudioWPatch 透過 PortAudio 協商格式，同一支麥克風可正常錄音。
- 沒選的方案：`sounddevice`（MIT，PortAudio）——0.5.x 的高階介面沒有 WASAPI loopback（NEWS：「high-level interface not yet available」），也沒有 Linux monitor source 的選擇方式與 macOS 系統聲音，仍需其他套件補齊。
- 重採樣自己用 numpy 實作：`soxr`（python-soxr）屬 LGPL；`samplerate`、scipy 都要多帶原生函式庫，為一個濾波器不值得引入。

## 各平台設計

所有後端都在 capture 內降混音並重採樣到 16 kHz，不向系統要 16 kHz：重採樣品質由我們的濾波器決定，三平台一致。WASAPI 以裝置的**原生取樣率與聲道數**開啟（完全不經系統轉換）；soundcard 與 ScreenCaptureKit 以 **48 kHz** 開啟（多數裝置原生就是 48 kHz）。

### Windows：WASAPI（PyAudioWPatch）

- 只列 WASAPI host API 的裝置（PortAudio 也會列 MME／DirectSound，同一裝置會重複出現）。
- 系統聲音：PyAudioWPatch 為每個輸出裝置列出一個 `isLoopbackDevice=True` 的輸入裝置（名稱加 `[Loopback]`）。預設 loopback = `get_default_wasapi_loopback()`（預設輸出裝置的 loopback）。
- 麥克風：`maxInputChannels > 0` 且非 loopback 的裝置；預設 = WASAPI 的預設輸入裝置。
- 原生 id 用**裝置名稱**：PortAudio 的裝置索引在裝置增減後會變；同名裝置依序加 `#2`、`#3`。開啟時在擷取執行緒內重新列舉並以名稱找回索引，**只開啟剛列舉出來的索引**——實測 PyAudioWPatch 對超出範圍的索引會直接 segfault，開啟前另以 `get_device_count()` 檢查。
- 讀取：非阻塞輪詢 `get_read_available()`，有資料就 `read(available, exception_on_overflow=False)`，沒有就睡 5 ms。
- **loopback 靜音補零**：實測沒有聲音播放時 WASAPI loopback 完全不送資料（1.5 秒 0 個 frame）。VAD 需要靜音樣本才能判斷句尾，所以 loopback 超過 50 ms 沒資料時依經過的時間補零（只推進整數樣本對應的時間，不會累積漂移）。麥克風不補零。
- 權限：Windows「隱私權設定 → 麥克風」關閉時開啟會失敗；PortAudio 只回報 `Unanticipated host error`，無法可靠分辨，所以拋 `CaptureError` 並在訊息中提示檢查隱私權設定。loopback 不需權限。

### Linux：PulseAudio／PipeWire（pipewire-pulse）

- 透過 soundcard 的 libpulse 用戶端；PipeWire 系統經 `pipewire-pulse` 相容層運作。
- 系統聲音：`device.class == "monitor"` 的 source（`isloopback=True`）。預設 loopback = `<預設 sink 名稱>.monitor`。
- 麥克風：非 monitor 的 source；預設 = 伺服器的預設 source（若預設 source 本身是 monitor，則麥克風沒有預設）。
- 以 `blocksize` 設定 fragsize（20 ms），否則 PulseAudio 預設的 fragment 會造成秒級延遲。
- monitor source 在靜音時仍持續送出（全零的）資料，不需補零。
- 裝置比對只做原生 id 完全比對；soundcard 自己的 `get_microphone` 會做名稱模糊比對，可能開錯裝置，所以不用。
- 沒有 PulseAudio 伺服器或沒有 `libpulse` → 匯入 soundcard 失敗 → `BackendUnavailableError`。soundcard 匯入時就連線，連不上伺服器時以 `assert` 檢查 context 狀態，所以 `AssertionError` 也歸為此類（容器實測）。
- **裝置移除**：PulseAudio（16.1 實測）在裝置移除時不會讓串流失敗，而是把它搬到預設 source 繼續送資料——拔掉 USB 耳機時，錄它 monitor 的串流會被搬到麥克風。所以 `open_microphone()` 建立的來源在擷取中每 0.25 秒以 `all_microphones()` 確認原生 id 還在，不在時隔 0.1 秒當場再查一次，仍不在就拋 `DeviceLostError`（避免列舉偶爾漏掉一次就結束 session，又不必多等一次 `record()`；檢查在兩次 `record()` 之間進行，所以搬走後最多混進一個讀取區塊的他處音訊）。列舉本身失敗時只記 warning、不結束——串流真的壞掉時 `record()` 會自己報錯。不用 `PA_STREAM_DONT_MOVE`：soundcard 沒有開放串流旗標，而且它會讓使用者無法在 pavucontrol 手動改來源。
- 開始錄 null sink 的 monitor 時，約 1.9 s 後才收到第一筆資料（容器內 null sink 實測；真實音效卡待實機確認），之後連續交付。

### macOS：ScreenCaptureKit（系統聲音）＋ CoreAudio（麥克風）

- 系統聲音（macOS 13+）：`SCShareableContent` 取得第一個 display，建立 `SCContentFilter(display, excludingWindows=[])`；`SCStreamConfiguration`：`capturesAudio=True`、`sampleRate=48000`、`channelCount=1`、`excludesCurrentProcessAudio=True`（避免把自己的聲音錄進去）、影像縮到 2×2、`minimumFrameInterval=1 s`（ScreenCaptureKit 沒有純音訊模式，影像只能壓到最小並丟棄）。
- 為什麼 `channelCount=1`：交由系統降混音後，就不必判斷 `CMSampleBuffer` 是交錯還是平面（non-interleaved）格式；取出的資料長度必須等於 `樣本數 × 4 位元組`，不符就報錯。
- 取資料：`CMSampleBufferGetDataBuffer` → `CMBlockBufferCopyDataBytes`（PyObjC 文件建議的方式；`CMBlockBufferGetDataPointer` 無法在 Python 使用）。
- 回呼在我們建立的 serial dispatch queue 上依序執行；同時註冊 screen 與 audio 兩種 output（screen 直接丟棄），避免系統因找不到 screen output 一直記錄錯誤。
- 在主執行緒上先呼叫 `NSApplication.sharedApplication()` 並設成 `Prohibited`（不出現在 Dock）：命令列程式沒有 WindowServer 連線時，ScreenCaptureKit 可能失敗（待 Mac 實機確認是否必要）。
- 權限：需要「螢幕與系統錄音」（Screen Recording）權限。第一次呼叫 `SCShareableContent` 時系統跳出詢問；拒絕時回傳 `SCStreamErrorUserDeclined`（-3801）→ `PermissionDeniedError`，訊息提示到「系統設定 → 隱私權與安全性 → 螢幕與系統錄音」開啟並重新啟動程式。權限掛在「負責的行程」上：打包後是 kikitori.app；開發時是啟動它的終端機。
- macOS 12 以下沒有 `capturesAudio` → 不列出系統聲音裝置；`open_source(None, "loopback")` 拋 `BackendUnavailableError`。
- 替代方案：安裝 BlackHole（或 Loopback 等虛擬裝置），把系統輸出導到它，然後在 `media` 模式直接選它（它在清單中是 `microphone`）。
- 麥克風：soundcard 的 CoreAudio 後端（`all_microphones()`，macOS 不支援 `include_loopback`），以 48 kHz 開啟（非原生取樣率時由 CoreAudio AudioConverter 轉換）。需要「麥克風」權限；權限由系統在第一次開啟時詢問（被拒時 CoreAudio 可能只送出靜音，無法可靠偵測）。
- 未來選項：macOS 14.2+ 的 Core Audio process tap 不需要螢幕錄製權限，但 PyObjC 尚無方便的綁定，先不做。

## 降混音與重採樣（`resample.py`）

- 降混音：各聲道取平均（float）。
- 重採樣：有理數比 `L/M = out/in`（約分後），Kaiser 窗 windowed-sinc 原型濾波器、polyphase 實作。
  - 通帶到 `0.875 × 較低端的 Nyquist`（16 kHz 輸出時 7 kHz），阻帶從 Nyquist（8 kHz）開始，阻帶衰減 80 dB。
  - 48 kHz → 16 kHz：L=1、M=3，每個輸出樣本 242 taps；44.1 kHz → 16 kHz：L=160、M=441，每相位 222 taps。
  - 實測：1 kHz、7 kHz 增益 0.00 dB；8 kHz −83～−85 dB；10 kHz −101～−103 dB。CPU：10 秒 48 kHz 音訊以 20 ms 區塊處理約 0.15 秒（約 1.5%）。
  - 串流狀態：保留最後 K−1 個輸入樣本與輸出位置計數，任意切塊的輸出與一次處理完全相同（測試驗證），不會在區塊邊界產生爆音。
  - 輸入輸出同取樣率時直接通過。群延遲約 (K−1)/2 個輸入樣本（48 kHz 時約 2.5 ms），不補償。
- float → int16：乘 32768、四捨五入、截在 [−32768, 32767]。

## 執行緒模型

- **WASAPI 與 soundcard 後端（拉取式，`ThreadedSource`）**：每個來源一條 daemon 擷取執行緒；裝置的解析、開啟、讀取、關閉都在這條執行緒內（PortAudio／COM／soundcard 的 recorder context manager 都要求或最適合這樣）。後端只提供一個回傳 `BlockReader` 的 context manager；`read_block()` 必須在有限時間內返回，執行緒才能及時看到停止旗標。
- **ScreenCaptureKit 後端（推送式）**：系統在 serial dispatch queue 上呼叫回呼；回呼內轉換後放入緩衝。回呼不得拋例外（會穿過 ObjC 邊界），錯誤一律記錄並轉交緩衝。
- **緩衝 `ChunkBuffer`**：以「16 kHz 樣本數」為上限（預設 5 秒）。滿了就丟最舊的資料並累計 `dropped_samples`，第一次丟棄時記一筆 warning。為什麼丟舊的：即時字幕寧可跳過舊音訊也不要延遲越積越多。
- `start()` 最多等 `open_timeout`（預設 5 秒）讓裝置開啟；逾時或開啟失敗會停止執行緒並拋錯。`stop()` 以 `join_timeout`（預設 2 秒）等待執行緒結束；若底層呼叫卡住，記錄 warning 後放手（daemon 執行緒不會阻擋程式結束）。

## 錯誤處理

| 例外（皆繼承 `CaptureError`） | 情況 |
|---|---|
| `BackendUnavailableError` | 平台不支援、匯入 soundcard／PyObjC 失敗（例如沒有 libpulse）、macOS < 13 要求系統聲音 |
| `DeviceNotFoundError` | 指定的 id 不存在、沒有預設裝置、沒有任何 loopback 裝置 |
| `PermissionDeniedError` | 螢幕錄製權限被拒（macOS `SCStreamErrorUserDeclined` −3801，含開始時與執行中被撤銷）。Windows 麥克風權限被拒只能從 PortAudio 得到 `Unanticipated host error`，歸為 `CaptureError` 並附提示 |
| `DeviceLostError` | 擷取中裝置消失：WASAPI 串流讀取（`get_read_available`／`read`）拋 `OSError`、PulseAudio stream FAILED（伺服器結束）或裝置從清單消失（串流被搬走）、ScreenCaptureKit `didStopWithError` |
| `CaptureError` | 其他擷取錯誤、使用狀態錯誤（未 start 就 read 等） |

- 輸入驗證：`kind` 必須是兩者之一；`device_id` 必須是 `None` 或非空字串；`timeout`、緩衝秒數等必須是有限正數；`ArraySource` 只接受一維 int16；`FileSource` 只接受 PCM WAV（8/16/24/32 位元整數）。
- 例外一律記錄後往上拋，不吞掉。裝置消失不自動重連——由 pipeline／engine 決定是否重開（之後可在 engine 層加入）。

## 延遲預算（loopback，48 kHz 裝置）

| 環節 | 典型延遲 |
|---|---|
| 系統緩衝（WASAPI 共享模式週期 10 ms；PulseAudio fragsize 20 ms；ScreenCaptureKit 約 10–20 ms） | 10–20 ms |
| 後端輪詢（WASAPI 每 5 ms；soundcard 依系統通知） | ≤ 5 ms |
| WASAPI loopback 靜音補零（只在沒有聲音播放時） | ≤ 50 ms |
| 重採樣群延遲 | 2.5 ms |
| 緩衝等待（取決於消費者呼叫 `read()` 的頻率） | 0–20 ms |
| **合計** | **約 20–50 ms**（相對於 0.5–0.6 s 的句尾判斷可忽略） |

## 驗證

- 單元測試（`tests/test_capture*.py`）：重採樣頻率響應（1 kHz 保留、10 kHz 衰減 > 40 dB）、切塊不變性、降混音、緩衝丟棄與錯誤傳遞、`ArraySource`／`FileSource`、裝置清單對應與錯誤路徑（以假的 PyAudioWPatch／soundcard／ScreenCaptureKit 物件）。不開啟任何真實裝置。
- 手動檢查：`experiments/capture_check/check_capture.py`（互動選單）列出裝置、錄 N 秒、顯示音量並存 WAV。
