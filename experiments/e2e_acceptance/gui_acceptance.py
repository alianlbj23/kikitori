"""kikitori 桌面程式的端到端驗收：以 W3C WebDriver（tauri-driver + msedgedriver）操作正式版建置。

暫時性實驗，不屬於正式程式碼。只用標準庫（urllib 講 WebDriver HTTP 協定、ctypes 讀視窗樣式）
與既有依賴（播放音訊用 PyAudioWPatch）。流程：

1. 啟動 app → 狀態列到「待命中」（正式版引擎從資源目錄啟動）。
2. 模型頁：硬體（RTX 5070、cuBLAS）與建議組合 → 按「套用建議組合」。
3. 設定頁：不合法的網址與變數名稱要在欄位下方顯示錯誤、不能儲存；改回合法值後儲存成功。
4. 關於：兩個第三方授權資料夾的路徑存在，按鈕會開啟檔案總管視窗（之後關掉）。
5. 影片翻譯 + Steam Streaming Speakers 的 loopback → 開始 → 播放 anime.wav 到該裝置 →
   字幕紀錄先出現即時字幕（淡色）再換成完整翻譯；字幕視窗顯示字幕；切換滑鼠穿透
   （以 Win32 視窗樣式 WS_EX_TRANSPARENT 確認）→ 停止。
6. 再播一次本專案自己寫的英文 TTS（沒有著作權問題）拍可以提交的截圖。

另一個情境「第一次啟動」（`--batch first-run`，不播放任何聲音）：空的設定資料夾啟動 →
不按任何按鈕，app 自己偵測硬體並套用建議組合（這台電腦：GPU 組合）、顯示說明 → 關閉 →
同一個設定資料夾再啟動一次 → 不再自動套用、設定檔不變。

其他情境：
- `installed`：CI 建置的 NSIS 安裝檔（`KIKITORI_E2E_INSTALLER`）靜默安裝到暫存目錄 → 已安裝的
  程式：引擎從安裝目錄啟動、第一次啟動自動套用 GPU 組合、影片翻譯雙語字幕；接著在同一份安裝上跑
  下面兩個情境 → 靜默解除安裝並確認清乾淨（安裝目錄、HKCU Uninstall、捷徑、行程）。
- `missing-models`：app 與引擎的 `LOCALAPPDATA` 指到暫存目錄（只複製 `small`，不動使用者的
  模型快取）→ 建議組合缺 base →「下載缺少的模型」→ 同一則訊息顯示進度 → 完成 → 開始。
- `better-combo`：存成 CPU 組合 →「有更適合這台電腦的組合」→「套用」→ 再存 CPU →
  「不要再提醒」（指紋寫入 `settings.ui.dismissed_recommendations`）→ 再啟動不再提示。

設定檔放在本次結果資料夾（`KIKITORI_CONFIG_DIR`），不碰使用者的設定。截圖：含動畫台詞的放
`screenshots/private/`（git 忽略），其餘放 `screenshots/`。

執行（互動選單）：uv run python -X utf8 experiments/e2e_acceptance/gui_acceptance.py
非互動：uv run python -X utf8 experiments/e2e_acceptance/gui_acceptance.py
        --batch run|first-run|installed|missing-models|better-combo|v020-ui|v020-languages|v020-original|v020-session
工具路徑以環境變數指定：KIKITORI_E2E_APP（kikitori-app.exe）、KIKITORI_E2E_TAURI_DRIVER、
KIKITORI_E2E_EDGEDRIVER、KIKITORI_E2E_INSTALLER（`installed` 用）。
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from ctypes import wintypes
from pathlib import Path
from typing import Any, Self

from acceptance import EN_LINES, EN_VOICE, KANA, load_track, simplified_chars, tts_clip
from e2e_common import HERE, RESULTS, ROOT, TARGET_OUTPUT, Player

logger = logging.getLogger("e2e.gui")

# 安裝與解除安裝的檢查（HKCU Uninstall、行程、捷徑）沿用更新驗收的寫法
sys.path.insert(0, str(ROOT / "experiments" / "update_acceptance"))

SCREENSHOTS = HERE / "screenshots"
PRIVATE_SCREENSHOTS = SCREENSHOTS / "private"
DEFAULT_APP = ROOT / "app" / "src-tauri" / "target" / "release" / "kikitori-app.exe"
DRIVER_PORT = 4444
NATIVE_PORT = 4445
VERBATIM_PREFIX = chr(92) * 2 + "?" + chr(92)  # Windows 的 verbatim 路徑前綴
ELEMENT = (
    "element-6066-11e4-a52e-4f735466cecf"  # W3C WebDriver 規定的 web element 識別鍵
)


# --------------------------------------------------------------------------- WebDriver


class WebDriverError(RuntimeError):
    pass


class WebDriver:
    """最小的 W3C WebDriver 客戶端（只實作這個驗收用到的端點）。"""

    def __init__(self, base: str) -> None:
        self.base = base
        self.session: str | None = None

    def call(
        self, method: str, path: str, body: Any = None, timeout: float = 60
    ) -> Any:
        data = None if body is None else json.dumps(body).encode()
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise WebDriverError(
                f"{method} {path}: {exc.code} {exc.read()[:500]!r}"
            ) from exc
        return payload.get("value")

    def s(self, method: str, path: str, body: Any = None, timeout: float = 60) -> Any:
        assert self.session is not None
        return self.call(method, f"/session/{self.session}{path}", body, timeout)

    def start(self, application: Path) -> None:
        caps = {
            "capabilities": {
                "alwaysMatch": {"tauri:options": {"application": str(application)}}
            }
        }
        value = self.call("POST", "/session", caps, timeout=120)
        self.session = value["sessionId"]

    def quit(self) -> None:
        if self.session:
            try:
                self.s("DELETE", "", timeout=30)
            finally:
                self.session = None

    def find(self, css: str) -> str:
        return self.s("POST", "/element", {"using": "css selector", "value": css})[
            ELEMENT
        ]

    def find_all(self, css: str) -> list[str]:
        return [
            e[ELEMENT]
            for e in self.s(
                "POST", "/elements", {"using": "css selector", "value": css}
            )
        ]

    def click(self, css_or_id: str, *, is_id: bool = False) -> None:
        eid = css_or_id if is_id else self.find(css_or_id)
        self.s("POST", f"/element/{eid}/click", {})

    def text(self, css: str) -> str:
        return self.s("GET", f"/element/{self.find(css)}/text")

    def type(self, css: str, value: str) -> None:
        eid = self.find(css)
        self.s("POST", f"/element/{eid}/clear", {})
        self.s("POST", f"/element/{eid}/value", {"text": value})

    def script(self, source: str, *args: Any) -> Any:
        return self.s("POST", "/execute/sync", {"script": source, "args": list(args)})

    def screenshot(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(base64.b64decode(self.s("GET", "/screenshot")))

    def handles(self) -> list[str]:
        return self.s("GET", "/window/handles")

    def switch(self, handle: str) -> None:
        self.s("POST", "/window", {"handle": handle})

    def title(self) -> str:
        return self.s("GET", "/title")

    def wait(
        self, what: str, predicate: Any, timeout: float = 30, interval: float = 0.25
    ) -> Any:
        deadline = time.monotonic() + timeout
        last: Any = None
        while time.monotonic() < deadline:
            try:
                last = predicate()
            except WebDriverError as exc:
                last = exc
            if last and not isinstance(last, Exception):
                return last
            time.sleep(interval)
        raise TimeoutError(f"timed out waiting for {what}; last={last!r}")


# --------------------------------------------------------------------------- Win32


user32 = ctypes.WinDLL("user32", use_last_error=True)
# 以實際像素取得視窗位置（螢幕縮放 125% 時，不宣告的話 GetWindowRect 回傳縮放前的座標，
# 與 DPI-aware 的截圖程式對不上）。
user32.SetProcessDPIAware()
user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
GWL_EXSTYLE = -20
WS_EX_TRANSPARENT = 0x20
WS_EX_LAYERED = 0x80000


def find_windows(pid: int) -> dict[str, int]:
    """這個行程的頂層視窗：標題 → HWND。"""
    found: dict[str, int] = {}

    def cb(hwnd: int, _: int) -> bool:
        owner = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            buf = ctypes.create_unicode_buffer(256)
            user32.GetWindowTextW(hwnd, buf, 256)
            if buf.value:
                found[buf.value] = hwnd
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def ex_style(hwnd: int) -> int:
    return int(user32.GetWindowLongPtrW(hwnd, GWL_EXSTYLE))


SWP_NOSIZE = 0x1
SWP_NOZORDER = 0x4
SWP_NOACTIVATE = 0x10


def move_window(hwnd: int, x: int, y: int) -> None:
    if not user32.SetWindowPos(
        hwnd, None, x, y, 0, 0, SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
    ):
        raise ctypes.WinError(ctypes.get_last_error())


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


CAPTURE_PS = r"""
$ErrorActionPreference = 'Stop'
Add-Type -ReferencedAssemblies System.Drawing -TypeDefinition @'
using System;
using System.Drawing;
using System.Runtime.InteropServices;
public static class Shot {
  [DllImport("user32.dll")] static extern bool SetProcessDPIAware();
  [DllImport("user32.dll")] static extern IntPtr GetDC(IntPtr hwnd);
  [DllImport("user32.dll")] static extern int ReleaseDC(IntPtr hwnd, IntPtr hdc);
  [DllImport("gdi32.dll")] static extern bool BitBlt(IntPtr dst, int x, int y, int w, int h, IntPtr src, int sx, int sy, int rop);
  // SRCCOPY | CAPTUREBLT：沒有 CAPTUREBLT 時 GDI 不會拍到分層（透明）視窗，例如字幕視窗。
  public static void Save(int l, int t, int w, int h, string path) {
    SetProcessDPIAware();
    using (var bmp = new Bitmap(w, h))
    using (var g = Graphics.FromImage(bmp)) {
      IntPtr screen = GetDC(IntPtr.Zero);
      IntPtr dst = g.GetHdc();
      try { BitBlt(dst, 0, 0, w, h, screen, l, t, 0x00CC0020 | 0x40000000); }
      finally { g.ReleaseHdc(dst); ReleaseDC(IntPtr.Zero, screen); }
      bmp.Save(path, System.Drawing.Imaging.ImageFormat.Png);
    }
  }
}
'@
[Shot]::Save([int]$env:KK_L, [int]$env:KK_T, [int]$env:KK_W, [int]$env:KK_H, $env:KK_OUT)
"""


def windows_over(rect: tuple[int, int, int, int]) -> list[dict[str, Any]]:
    """與 rect 重疊、可見的頂層視窗（由上到下），用來判讀截圖裡擋住的東西。"""
    found: list[dict[str, Any]] = []

    def cb(hwnd: int, _: int) -> bool:
        if user32.IsWindowVisible(hwnd):
            r = window_rect(hwnd)
            if r[0] < rect[2] and r[2] > rect[0] and r[1] < rect[3] and r[3] > rect[1]:
                buf = ctypes.create_unicode_buffer(256)
                user32.GetWindowTextW(hwnd, buf, 256)
                cls = ctypes.create_unicode_buffer(256)
                user32.GetClassNameW(hwnd, cls, 256)
                owner = wintypes.DWORD()
                user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
                found.append(
                    {
                        "hwnd": hwnd,
                        "title": buf.value,
                        "class": cls.value,
                        "pid": owner.value,
                        "rect": r,
                    }
                )
        return True

    user32.EnumWindows(WNDENUMPROC(cb), 0)
    return found


def hide_automation_consoles(rect: tuple[int, int, int, int]) -> int:
    """縮小 msedgedriver 為 WebView2 開的主控台視窗（只在自動化測試時出現，會擋在字幕視窗後面）。"""
    count = 0
    for w in windows_over(rect):
        if w["class"] == "ConsoleWindowClass" and "msedgewebview2.exe" in w["title"]:
            user32.ShowWindow(w["hwnd"], 6)  # SW_MINIMIZE
            count += 1
    return count


def capture_screen_rect(rect: tuple[int, int, int, int], out: Path) -> None:
    """擷取螢幕上的一塊區域（只擷取 kikitori 自己的視窗範圍）。"""
    out.parent.mkdir(parents=True, exist_ok=True)
    left, top, right, bottom = rect
    env = dict(
        os.environ,
        KK_L=str(left),
        KK_T=str(top),
        KK_W=str(right - left),
        KK_H=str(bottom - top),
        KK_OUT=str(out),
    )
    subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", CAPTURE_PS],
        env=env,
        check=True,
        timeout=60,
        capture_output=True,
    )


EXPLORER_PS = r"""
$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject Shell.Application
$out = @()
foreach ($w in $shell.Windows()) {
  try { $p = $w.Document.Folder.Self.Path } catch { $p = $null }
  if ($p) {
    if ($env:KK_CLOSE -and ($env:KK_CLOSE.Split('|') -contains $p)) { $w.Quit() } else { $out += $p }
  }
}
$out | ConvertTo-Json -Compress
"""


def explorer_paths(close: list[str] | None = None) -> list[str]:
    """目前開著的檔案總管視窗路徑；`close` 列出的路徑會被關掉。"""
    env = dict(os.environ, KK_CLOSE="|".join(close or []))
    proc = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", EXPLORER_PS],
        env=env,
        check=True,
        timeout=60,
        capture_output=True,
    )
    text = proc.stdout.decode("utf-8", "replace").strip()
    if not text:
        return []
    value = json.loads(text)
    return [value] if isinstance(value, str) else list(value)


# --------------------------------------------------------------------------- 流程


ROWS_JS = """
return Array.from(document.querySelectorAll('#transcript li.utt')).map(li => ({
  partial: li.classList.contains('is-partial'),
  time: li.querySelector('.utt-time').textContent,
  lang: li.querySelector('.utt-lang').textContent,
  orig: li.querySelector('.utt-orig').textContent,
  tr: li.querySelector('.utt-tr').textContent,
}));
"""

OVERLAY_JS = """
const frame = document.getElementById('frame');
return {
  interactive: frame.classList.contains('is-interactive'),
  lines: Array.from(document.querySelectorAll('#lines .line')).map(l => ({
    partial: l.classList.contains('is-partial'),
    orig: l.querySelector('.orig').textContent,
    tr: l.querySelector('.tr').textContent,
  })),
  bodyBackground: getComputedStyle(document.body).backgroundColor,
  htmlBackground: getComputedStyle(document.documentElement).backgroundColor,
};
"""


class Run:
    def __init__(self, suffix: str = "gui") -> None:
        self.dir = RESULTS / (time.strftime("%Y%m%d-%H%M%S") + f"-{suffix}")
        self.dir.mkdir(parents=True, exist_ok=True)
        self.steps: dict[str, Any] = {}

    def check(self, name: str, ok: bool, detail: Any) -> None:
        self.steps[name] = {"passed": bool(ok), "detail": detail}
        text = json.dumps(detail, ensure_ascii=False)
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}: {text[:400]}")


def tool_path(env: str, default: Path | None = None) -> Path:
    value = os.environ.get(env)
    path = Path(value) if value else default
    if path is None or not path.is_file():
        raise FileNotFoundError(f"set {env} to an existing file (got {path})")
    return path


def wait_port(port: int, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/status", timeout=2):
                return
        except OSError:
            time.sleep(0.2)
    raise TimeoutError(f"port {port} not ready")


def app_pids(exe_name: str) -> list[int]:
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {exe_name}", "/FO", "CSV", "/NH"],
        capture_output=True,
        timeout=30,
        check=True,
    ).stdout.decode("mbcs", "replace")
    pids = []
    for line in out.splitlines():
        parts = [p.strip('"') for p in line.split('","')]
        if len(parts) > 1 and parts[0].lower() == exe_name.lower():
            pids.append(int(parts[1].strip('"')))
    return pids


def play_async(player: Player, label: str, audio: Any) -> threading.Thread:
    t = threading.Thread(target=player.play, args=(label, audio), daemon=True)
    t.start()
    return t


def watch_subtitles(
    wd: WebDriver, main: str, overlay: str, until: threading.Thread, settle: float
) -> dict[str, Any]:
    """播放期間輪詢字幕紀錄與字幕視窗；記錄每一列是否先出現過即時字幕。"""
    seen_partial: dict[int, bool] = {}
    overlay_partial = False
    overlay_max = 0
    snapshots = 0
    end_at: float | None = None
    rows: list[dict[str, Any]] = []
    shot_partial = False
    while True:
        wd.switch(main)
        rows = wd.script(ROWS_JS)
        snapshots += 1
        for i, row in enumerate(rows):
            if row["partial"]:
                seen_partial[i] = True
            else:
                seen_partial.setdefault(i, False)
        if not shot_partial and any(r["partial"] for r in rows) and len(rows) >= 3:
            wd.screenshot(PRIVATE_SCREENSHOTS / "main-transcript-partial.png")
            shot_partial = True
        wd.switch(overlay)
        ov = wd.script(OVERLAY_JS)
        overlay_max = max(overlay_max, len(ov["lines"]))
        overlay_partial = overlay_partial or any(
            line["partial"] for line in ov["lines"]
        )
        if not until.is_alive() and end_at is None:
            end_at = time.monotonic() + settle
        if end_at is not None and time.monotonic() > end_at:
            break
        time.sleep(0.15)
    wd.switch(main)
    return {
        "rows": rows,
        "seen_partial_before_final": seen_partial,
        "overlay_partial_seen": overlay_partial,
        "overlay_max_lines": overlay_max,
        "polls": snapshots,
    }


def run_gui() -> int:
    run = Run()
    app = tool_path("KIKITORI_E2E_APP", DEFAULT_APP)
    tauri_driver = tool_path("KIKITORI_E2E_TAURI_DRIVER")
    edgedriver = tool_path("KIKITORI_E2E_EDGEDRIVER")
    config_dir = run.dir / "config"
    config_dir.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith("KIKITORI_ENGINE")}
    env["KIKITORI_CONFIG_DIR"] = str(config_dir)
    log = (run.dir / "tauri-driver.log").open("wb")
    driver = subprocess.Popen(
        [
            str(tauri_driver),
            "--port",
            str(DRIVER_PORT),
            "--native-port",
            str(NATIVE_PORT),
            "--native-driver",
            str(edgedriver),
        ],
        env=env,
        stdout=log,
        stderr=subprocess.STDOUT,
        # 不要在使用者桌面上跳出主控台視窗（也會擋在字幕視窗後面被截進圖裡）。
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    wd = WebDriver(f"http://127.0.0.1:{DRIVER_PORT}")
    player = Player()
    print(f"results: {run.dir}\napp: {app}\nplayback device: {player.device.name!r}")
    opened_explorer: list[str] = []
    try:
        wait_port(DRIVER_PORT)
        wd.start(app)
        titles: dict[str, str] = {}
        deadline = time.monotonic() + 20
        while len(titles) < 2 and time.monotonic() < deadline:
            for h in wd.handles():
                if h not in titles.values():
                    wd.switch(h)
                    titles[wd.title()] = h
            time.sleep(0.5)
        main, overlay = titles.get("kikitori"), titles.get("kikitori 字幕")
        run.check("two webview windows", bool(main and overlay), titles)
        assert main and overlay
        wd.switch(main)
        pid = app_pids(app.name)
        run.check("app process", len(pid) == 1, pid)
        hwnds = find_windows(pid[0])

        # 1. 引擎就緒
        t0 = time.monotonic()
        wd.wait(
            "engine ready", lambda: wd.text("#status-label") == "待命中", timeout=120
        )
        engines = app_pids("kikitori-engine.exe")
        run.check(
            "engine status → 待命中",
            True,
            {"secs": round(time.monotonic() - t0, 1), "engine_pids": engines},
        )
        wd.screenshot(SCREENSHOTS / "01-main-ready.png")

        # 2. 模型頁
        wd.click('.nav-item[data-page="models"]')
        hw = wd.wait(
            "hardware",
            lambda: (t := wd.text("#hardware")) and "RTX 5070" in t and t,
            timeout=60,
        )
        rec = wd.wait(
            "recommendation",
            lambda: (t := wd.text("#recommendation")) and "large-v3-turbo" in t and t,
            timeout=60,
        )
        run.check(
            "models page: hardware + recommendation",
            "cuBLAS" in hw or "可以使用" in hw,
            {"hardware": hw[:600], "recommendation": rec[:600]},
        )
        wd.screenshot(SCREENSHOTS / "02-models.png")
        wd.click("#apply-recommendation")
        notice = wd.wait(
            "apply notice",
            lambda: (t := wd.text("#notices")) and "已套用建議組合" in t and t,
            timeout=60,
        )
        settings = json.loads((config_dir / "settings.json").read_text("utf-8"))
        run.check(
            "apply recommendation",
            settings["asr"]["device"] == "cuda",
            {
                "notice": notice[:200],
                "asr": settings["asr"],
                "translation": settings["translation"],
            },
        )

        # 3. 設定頁：不合法的輸入
        wd.click('.nav-item[data-page="settings"]')
        wd.wait(
            "settings form",
            lambda: wd.s("GET", f"/element/{wd.find('#f-base-url')}/property/value"),
            timeout=30,
        )
        before = (config_dir / "settings.json").read_text("utf-8")
        wd.type("#f-base-url", "ftp://example.com/v1")
        wd.type("#f-api-key-env", "1BAD-NAME")
        wd.click("#settings-save")
        status = wd.wait("validation", lambda: wd.text("#settings-status"), timeout=10)
        err_url = wd.text('p.field-error[data-error-for="base_url"]')
        err_env = wd.text('p.field-error[data-error-for="api_key_env"]')
        after = (config_dir / "settings.json").read_text("utf-8")
        run.check(
            "settings: invalid input rejected inline",
            bool(err_url and err_env) and before == after,
            {
                "status": status,
                "base_url_error": err_url,
                "api_key_env_error": err_env,
                "file_unchanged": before == after,
            },
        )
        wd.script(
            "document.getElementById('f-base-url').scrollIntoView({block: 'center'});"
        )
        wd.screenshot(SCREENSHOTS / "03-settings-invalid.png")
        wd.type("#f-base-url", "http://localhost:11434/v1")
        wd.type("#f-api-key-env", "KIKITORI_TRANSLATE_API_KEY")
        wd.click("#settings-save")
        saved = wd.wait(
            "saved",
            lambda: (
                (t := wd.text("#settings-status"))
                and "儲存" in t
                and "中" not in t
                and t
            ),
            timeout=20,
        )
        cleared = not wd.text('p.field-error[data-error-for="base_url"]')
        run.check("settings: valid input saved", cleared, {"status": saved})

        # 4. 關於：授權資料夾
        about = wd.text("#about-third-party")
        app_btn = wd.find("#open-app-licenses")
        eng_btn = wd.find("#open-engine-licenses")
        visible = [wd.s("GET", f"/element/{b}/displayed") for b in (app_btn, eng_btn)]
        expected = [
            str(app.parent / "licenses"),
            str(app.parent / "kikitori-engine" / "THIRD_PARTY_LICENSES"),
        ]
        run.check(
            "about: license folders resolve",
            all(visible)
            and all(p in about for p in expected)
            and VERBATIM_PREFIX not in about,
            {"text": about, "buttons_displayed": visible},
        )
        wd.s("POST", f"/element/{app_btn}/click", {})
        wd.screenshot(SCREENSHOTS / "04-about.png")
        for btn, path in ((app_btn, expected[0]), (eng_btn, expected[1])):
            if btn == eng_btn:
                wd.s("POST", f"/element/{btn}/click", {})
            found = wd.wait(
                f"explorer {path}",
                lambda p=path: any(
                    os.path.normcase(x) == os.path.normcase(p) for x in explorer_paths()
                ),
                timeout=20,
                interval=1,
            )
            opened_explorer.append(path)
            run.check(f"about: open button opens {Path(path).name}", bool(found), path)
        explorer_paths(close=opened_explorer)

        # 5. 影片翻譯 + Steam Streaming Speakers
        wd.click('.nav-item[data-page="transcript"]')
        wd.click('.mode[data-mode="media"]')
        options = wd.find_all("#device option")
        target = None
        for opt in options:
            if TARGET_OUTPUT in wd.s("GET", f"/element/{opt}/text"):
                target = opt
        run.check(
            "device list has the virtual loopback",
            target is not None,
            [wd.s("GET", f"/element/{o}/text") for o in options],
        )
        assert target is not None
        wd.click(target, is_id=True)
        chosen = wd.script(
            "const s=document.getElementById('device'); return s.options[s.selectedIndex].text;"
        )
        wd.click("#start")
        t0 = time.monotonic()
        wd.wait(
            "listening", lambda: wd.text("#status-label") == "正在聆聽", timeout=120
        )
        run.check(
            "start → 正在聆聽",
            True,
            {"device": chosen, "load_secs": round(time.monotonic() - t0, 1)},
        )
        time.sleep(1.0)
        overlay_hwnd = find_windows(pid[0]).get("kikitori 字幕") or hwnds.get(
            "kikitori 字幕"
        )
        # 把字幕視窗移到主視窗上，讓背景是 kikitori 自己的畫面（截圖不會拍到使用者的其他視窗）。
        # WebDriver 的 set window rect 動不了 Tauri 的視窗，改用 Win32（等同使用者拖曳）；
        # 結束時移回原位，window-state 外掛才不會記住測試用的位置。
        main_rect = window_rect(find_windows(pid[0])["kikitori"])
        if overlay_hwnd:
            overlay_home = window_rect(overlay_hwnd)
            move_window(overlay_hwnd, main_rect[0] + 140, main_rect[1] + 400)
            run.check(
                "overlay placed over the main window",
                True,
                {"main": main_rect, "overlay": window_rect(overlay_hwnd)},
            )

        anime = load_track("anime").audio
        playing = play_async(player, "anime", anime)
        watched = watch_subtitles(wd, main, overlay, playing, settle=6.0)
        rows = watched["rows"]
        finals = [r for r in rows if not r["partial"]]
        partial_first = [
            i for i, seen in watched["seen_partial_before_final"].items() if seen
        ]
        bad_tc = {
            i: simplified_chars(r["tr"])
            for i, r in enumerate(rows)
            if simplified_chars(r["tr"])
        }
        jp = sum(1 for r in finals if KANA.search(r["orig"]))
        run.check(
            "transcript: partial then final bilingual lines",
            len(finals) >= 6
            and len(partial_first) >= 4
            and not any(r["partial"] for r in rows)
            and all(r["tr"] for r in finals)
            and not bad_tc
            and jp >= len(finals) - 1,
            {
                "rows": len(rows),
                "finals": len(finals),
                "rows_seen_partial_first": len(partial_first),
                "japanese_originals": jp,
                "simplified": bad_tc,
                "polls": watched["polls"],
            },
        )
        wd.screenshot(PRIVATE_SCREENSHOTS / "main-transcript-final.png")
        wd.switch(overlay)
        ov = wd.script(OVERLAY_JS)
        wd.screenshot(PRIVATE_SCREENSHOTS / "overlay-webdriver.png")
        wd.switch(main)
        if overlay_hwnd:
            hide_automation_consoles(window_rect(overlay_hwnd))
            time.sleep(0.5)
            capture_screen_rect(
                window_rect(overlay_hwnd), PRIVATE_SCREENSHOTS / "overlay-screen.png"
            )
        run.check(
            "overlay shows subtitles",
            watched["overlay_max_lines"] >= 1 and len(ov["lines"]) >= 1,
            {
                "max_lines_seen": watched["overlay_max_lines"],
                "partial_seen": watched["overlay_partial_seen"],
                "now": len(ov["lines"]),
                "background": [ov["bodyBackground"], ov["htmlBackground"]],
            },
        )

        # 滑鼠穿透
        before_style = ex_style(overlay_hwnd) if overlay_hwnd else 0
        wd.click("#overlay-click-through")
        time.sleep(0.5)
        on_style = ex_style(overlay_hwnd) if overlay_hwnd else 0
        wd.switch(overlay)
        interactive_on = wd.script(OVERLAY_JS)["interactive"]
        wd.switch(main)
        wd.click("#overlay-click-through")
        time.sleep(0.5)
        off_style = ex_style(overlay_hwnd) if overlay_hwnd else 0
        run.check(
            "click-through toggles WS_EX_TRANSPARENT",
            not before_style & WS_EX_TRANSPARENT
            and on_style & WS_EX_TRANSPARENT
            and not off_style & WS_EX_TRANSPARENT
            and not interactive_on,
            {
                "before": hex(before_style),
                "on": hex(on_style),
                "off": hex(off_style),
                "overlay_interactive_when_on": interactive_on,
            },
        )

        # 6. 可提交的截圖：本專案自己寫的英文句子
        wd.click("#clear-transcript")
        en = tts_clip("tts_en", EN_LINES, EN_VOICE, "en")
        playing = play_async(player, "tts_en", en.audio)
        shot = False
        while playing.is_alive():
            rows = wd.script(ROWS_JS)
            if (
                not shot
                and any(r["partial"] for r in rows)
                and rows
                and not rows[0]["partial"]
            ):
                wd.screenshot(SCREENSHOTS / "05-transcript-partial.png")
                shot = True
            time.sleep(0.15)
        time.sleep(5)
        wd.screenshot(SCREENSHOTS / "06-transcript-final.png")
        rows = wd.script(ROWS_JS)
        wd.switch(overlay)
        wd.screenshot(SCREENSHOTS / "07-overlay-webdriver.png")
        wd.switch(main)
        if overlay_hwnd:
            rect = window_rect(overlay_hwnd)
            hide_automation_consoles(rect)
            time.sleep(0.5)
            capture_screen_rect(rect, SCREENSHOTS / "08-overlay-screen.png")
            run.check(
                "windows stacked over the overlay area (top first)",
                True,
                [w for w in windows_over(rect) if w["pid"] != os.getpid()][:6],
            )
        run.check(
            "english lines translated (screenshots)",
            len(rows) >= 2 and all(not r["partial"] for r in rows),
            [(r["lang"], r["orig"], r["tr"]) for r in rows],
        )

        if overlay_hwnd:
            move_window(overlay_hwnd, overlay_home[0], overlay_home[1])
        wd.click("#start")
        wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
        run.check(
            "stop → 待命中", wd.text("#start") == "開始", wd.text("#status-label")
        )
        wd.screenshot(SCREENSHOTS / "09-main-stopped.png")
    except Exception:
        logger.exception("GUI acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
        try:
            wd.screenshot(PRIVATE_SCREENSHOTS / "failure.png")
        except Exception as exc:  # noqa: BLE001 - 失敗時的截圖只是輔助
            logger.warning("failure screenshot failed: %s", exc)
    finally:
        try:
            wd.quit()
        except (WebDriverError, OSError) as exc:
            logger.warning("closing the WebDriver session failed: %s", exc)
        if opened_explorer:
            try:
                explorer_paths(close=opened_explorer)
            except (subprocess.SubprocessError, OSError) as exc:
                logger.warning("closing explorer windows failed: %s", exc)
        driver.terminate()
        try:
            driver.wait(15)
        except subprocess.TimeoutExpired:
            driver.kill()
        log.close()
        player.close()
        time.sleep(3)
        leftovers = {
            name: app_pids(name)
            for name in (
                app.name,
                "kikitori-engine.exe",
                "msedgedriver.exe",
                "tauri-driver.exe",
            )
        }
        run.check("no leftover processes", not any(leftovers.values()), leftovers)
    passed = all(s["passed"] for s in run.steps.values())
    (run.dir / "gui-results.json").write_text(
        json.dumps(run.steps, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"\nOVERALL: {'PASS' if passed else 'FAIL'}  (details: {run.dir / 'gui-results.json'})"
    )
    return 0 if passed else 1


FIRST_RUN_NOTICE = "已依這台電腦"
NOTICES_JS = """
return Array.from(document.querySelectorAll('#notices .notice')).map(n => ({
  key: n.dataset.key || null,
  tone: n.dataset.tone,
  text: n.querySelector('p').textContent,
  actions: Array.from(n.querySelectorAll('.notice-actions button')).map(b => b.textContent),
}));
"""


class AppSession:
    """一次 app 啟動：tauri-driver + WebDriver session，切到主視窗；離開時關閉並等行程結束。

    `app` 省略時用 `KIKITORI_E2E_APP`；`extra_env` 會傳給 app 與它啟動的引擎
    （例如把 `LOCALAPPDATA` 指到暫存目錄，讓引擎看到空的模型快取）。
    """

    def __init__(
        self,
        run: Run,
        config_dir: Path,
        label: str,
        *,
        app: Path | None = None,
        extra_env: dict[str, str] | None = None,
    ) -> None:
        self.app = app or tool_path("KIKITORI_E2E_APP", DEFAULT_APP)
        self.tauri_driver = tool_path("KIKITORI_E2E_TAURI_DRIVER")
        self.edgedriver = tool_path("KIKITORI_E2E_EDGEDRIVER")
        self.env = {
            k: v for k, v in os.environ.items() if not k.startswith("KIKITORI_ENGINE")
        }
        self.env["KIKITORI_CONFIG_DIR"] = str(config_dir)
        self.env.update(extra_env or {})
        self.log_path = run.dir / f"tauri-driver-{label}.log"
        self.wd = WebDriver(f"http://127.0.0.1:{DRIVER_PORT}")
        self.driver: subprocess.Popen[bytes] | None = None

    def __enter__(self) -> Self:
        self.log = self.log_path.open("wb")
        self.driver = subprocess.Popen(
            [
                str(self.tauri_driver),
                "--port",
                str(DRIVER_PORT),
                "--native-port",
                str(NATIVE_PORT),
                "--native-driver",
                str(self.edgedriver),
            ],
            env=self.env,
            stdout=self.log,
            stderr=subprocess.STDOUT,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
        wait_port(DRIVER_PORT)
        self.wd.start(self.app)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            for handle in self.wd.handles():
                self.wd.switch(handle)
                if self.wd.title() == "kikitori":
                    return self
            time.sleep(0.5)
        raise TimeoutError("main window not found")

    def __exit__(self, *_: object) -> None:
        try:
            self.wd.quit()
        except (WebDriverError, OSError) as exc:
            logger.warning("closing the WebDriver session failed: %s", exc)
        if self.driver is not None:
            self.driver.terminate()
            try:
                self.driver.wait(15)
            except subprocess.TimeoutExpired:
                self.driver.kill()
        self.log.close()
        time.sleep(3)

    def notices(self) -> list[dict[str, Any]]:
        return self.wd.script(NOTICES_JS)

    def notice(self, key: str) -> dict[str, Any] | None:
        """目前顯示的 `data-key` 訊息（沒有時 None）。"""
        return next((n for n in self.notices() if n["key"] == key), None)

    def click_notice_action(self, key: str, label: str) -> None:
        """按下 `data-key` 訊息上文字為 `label` 的按鈕。"""
        clicked = self.wd.script(
            "const n = document.querySelector(`#notices .notice[data-key='${arguments[0]}']`);"
            "const b = n && Array.from(n.querySelectorAll('.notice-actions button'))"
            ".find(x => x.textContent === arguments[1]);"
            "if (!b) return false; b.click(); return true;",
            key,
            label,
        )
        if not clicked:
            raise WebDriverError(f"no button {label!r} on notice {key!r}")

    def window(self, title: str) -> str:
        """標題為 `title` 的 webview 的 handle（會切換過去）。"""
        for handle in self.wd.handles():
            self.wd.switch(handle)
            if self.wd.title() == title:
                return handle
        raise WebDriverError(f"no webview titled {title!r}")

    def wait_ready(self, timeout: float = 120) -> None:
        self.wd.wait(
            "engine ready",
            lambda: self.wd.text("#status-label") == "待命中",
            timeout=timeout,
        )

    def show_page(self, page: str) -> None:
        self.wd.click(f'.nav-item[data-page="{page}"]')
        self.wd.wait(
            f"page {page}",
            lambda: self.wd.script(
                f"return !document.getElementById('page-{page}').hidden;"
            ),
            timeout=10,
        )

    def choose_virtual_loopback(self) -> str:
        """影片翻譯模式、聲音來源選虛擬喇叭的 loopback（測試聲音只播到那裡）。"""
        wd = self.wd
        self.show_page("transcript")
        wd.click('.mode[data-mode="media"]')
        target = wd.wait(
            "virtual loopback option",
            lambda: next(
                (
                    o
                    for o in wd.find_all("#device option")
                    if TARGET_OUTPUT in wd.s("GET", f"/element/{o}/text")
                ),
                None,
            ),
            timeout=30,
        )
        wd.click(target, is_id=True)
        return str(
            wd.script(
                "const s=document.getElementById('device');"
                "return s.options[s.selectedIndex].text;"
            )
        )


def run_first_run() -> int:
    """空的設定資料夾啟動兩次：第一次自動套用建議組合並說明，第二次不再套用。"""
    run = Run("first-run")
    config_dir = run.dir / "config"
    config_dir.mkdir()
    settings_path = config_dir / "settings.json"
    print(f"results: {run.dir}")
    run.check(
        "config dir starts empty",
        not any(config_dir.iterdir()),
        str(config_dir),
    )
    seen_first: list[dict[str, Any]] = []
    try:
        with AppSession(run, config_dir, "first") as app:
            wd = app.wd
            t0 = time.monotonic()
            wd.wait(
                "engine ready",
                lambda: wd.text("#status-label") == "待命中",
                timeout=120,
            )
            notice = wd.wait(
                "first-run notice",
                lambda: next(
                    (n for n in app.notices() if FIRST_RUN_NOTICE in n["text"]), None
                ),
                timeout=120,
            )
            secs = round(time.monotonic() - t0, 1)
            settings = json.loads(settings_path.read_text("utf-8"))
            asr, tr = settings["asr"], settings["translation"]
            run.check(
                "first launch auto-applies the GPU combination without a click",
                # v0.2.0（Milestone 16）：12 GB＋本機翻譯 → turbo int8 ／ large-v3 int8 ＋ 9B
                asr["device"] == "cuda"
                and asr["fast_model"] == "large-v3-turbo"
                and asr["final_model"] == "large-v3"
                and asr.get("fast_compute_type") == "int8_float16"
                and asr.get("final_compute_type") == "int8_float16"
                and tr["final_model"] == "qwen3.5:9b"
                and "large-v3-turbo" in notice["text"]
                and "qwen3.5:9b" in notice["text"]
                and "RTX 5070" in notice["text"]
                and asr["final_model"] in notice["text"]
                and "GPU" in notice["text"],
                {"secs": secs, "notice": notice, "asr": asr, "translation": tr},
            )
            # 讓缺模型的檢查與「有更適合的組合」檢查跑完
            time.sleep(6)
            seen_first = app.notices()
            texts = " ".join(n["text"] for n in seen_first)
            run.check(
                "no missing-model prompt (models cached) and no offer right after applying",
                "還需要下載" not in texts and "有更適合" not in texts,
                seen_first,
            )
            wd.screenshot(SCREENSHOTS / "10-first-run-notice.png")
            wd.script(
                "document.querySelector('[data-key=\"first-run\"] .notice-actions button').click();"
            )
            wd.wait(
                "models page",
                lambda: wd.script(
                    "return !document.getElementById('page-models').hidden;"
                ),
                timeout=10,
            )
            rec = wd.wait(
                "recommendation",
                lambda: (t := wd.text("#recommendation")) and "large-v3" in t and t,
                timeout=60,
            )
            combo = wd.script(
                "const f=document.getElementById('combo-form');"
                "return {fast: f.elements.fast_model.value, final: f.elements.final_model.value,"
                " device: f.elements.device.value};"
            )
            run.check(
                "review link opens the models page showing the applied combination",
                combo["device"] == "cuda" and combo["final"] == asr["final_model"],
                {"combo_form": combo, "recommendation": rec[:300]},
            )
            wd.screenshot(SCREENSHOTS / "11-first-run-models.png")
        before = settings_path.read_bytes()

        with AppSession(run, config_dir, "second") as app:
            wd = app.wd
            wd.wait(
                "engine ready",
                lambda: wd.text("#status-label") == "待命中",
                timeout=120,
            )
            # 第一次啟動在就緒後幾秒內就套用；多等一段時間確認第二次不會發生
            seen: list[dict[str, Any]] = []
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                for n in app.notices():
                    if n not in seen:
                        seen.append(n)
                time.sleep(0.5)
            after = settings_path.read_bytes()
            run.check(
                "relaunch: no second auto-apply, settings file unchanged",
                not any(FIRST_RUN_NOTICE in n["text"] for n in seen)
                and after == before,
                {"notices": seen, "file_unchanged": after == before},
            )
            run.check(
                "relaunch: no better-combination offer (already applied)",
                not any("有更適合" in n["text"] for n in seen),
                seen,
            )
            wd.screenshot(SCREENSHOTS / "12-relaunch.png")
    except Exception:
        logger.exception("first-run acceptance aborted")
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    leftovers = {
        name: app_pids(name)
        for name in (
            "kikitori-app.exe",
            "kikitori-engine.exe",
            "msedgedriver.exe",
            "tauri-driver.exe",
        )
    }
    run.check("no leftover processes", not any(leftovers.values()), leftovers)
    passed = all(step["passed"] for step in run.steps.values())
    (run.dir / "gui-results.json").write_text(
        json.dumps(run.steps, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"\nOVERALL: {'PASS' if passed else 'FAIL'}  (details: {run.dir / 'gui-results.json'})"
    )
    return 0 if passed else 1


OFFER_KEY = "recommendation-offer"
MISSING_KEY = "missing-models"
FIRST_RUN_KEY = "first-run"
OFFER_TEXT = "有更適合這台電腦的組合"


def leftover_processes(app_name: str = "kikitori-app.exe") -> dict[str, list[int]]:
    return {
        name: app_pids(name)
        for name in (
            app_name,
            "kikitori-engine.exe",
            "msedgedriver.exe",
            "tauri-driver.exe",
        )
    }


def finish(run: Run) -> int:
    passed = all(step["passed"] for step in run.steps.values())
    (run.dir / "gui-results.json").write_text(
        json.dumps(run.steps, ensure_ascii=False, indent=2), "utf-8"
    )
    print(
        f"\nOVERALL: {'PASS' if passed else 'FAIL'}  (details: {run.dir / 'gui-results.json'})"
    )
    return 0 if passed else 1


def read_settings(config_dir: Path) -> dict[str, Any]:
    return json.loads((config_dir / "settings.json").read_text("utf-8"))


def save_combo(app: AppSession, combo: dict[str, str]) -> str:
    """在模型頁的「自訂組合」填入 `combo`（表單欄位名稱 → 值）並儲存，回傳狀態文字。

    儲存要等 `models.validate` 的即時檢查完成（檢查中按下不會儲存），所以沒有出現
    「已儲存」時每秒再按一次。
    """
    wd = app.wd
    app.show_page("models")
    wd.wait(
        "combo options",
        lambda: wd.script(
            "return document.getElementById('c-fast-model').options.length > 1;"
        ),
        timeout=60,
    )
    wd.script(
        "const f = document.getElementById('combo-form');"
        "for (const [k, v] of Object.entries(arguments[0])) {"
        "  const e = f.elements[k]; e.value = v;"
        "  e.dispatchEvent(new Event('input', {bubbles: true}));"
        "  e.dispatchEvent(new Event('change', {bubbles: true}));"
        "}",
        combo,
    )

    def saved() -> str | None:
        status = wd.text("#combo-status")
        if status.startswith("已儲存"):
            return status
        if status not in ("", "儲存中"):
            raise RuntimeError(f"combo save failed: {status!r}")
        if status == "":
            wd.click("#combo-save")
        return None

    return str(wd.wait("combo saved", saved, timeout=60, interval=1.0))


def install_ci_build(installer: Path, install_dir: Path) -> dict[str, Any]:
    """靜默安裝 NSIS 安裝檔（per-user）到 `install_dir`，回傳 HKCU 的 Uninstall 項目。"""
    from update_acceptance import uninstall_entries

    # /D= 必須是最後一個參數、不能加引號（路徑不含空白）
    if " " in str(install_dir):
        raise ValueError(f"install dir must not contain spaces: {install_dir}")
    subprocess.run([str(installer), "/S", f"/D={install_dir}"], check=True, timeout=300)
    return {"entries": uninstall_entries()}


def uninstall_ci_build(install_dir: Path, before: dict[str, Any]) -> dict[str, Any]:
    """靜默解除安裝並收集清理證據（登錄、捷徑、行程、安裝目錄）。"""
    from update_acceptance import IDENTIFIER, baseline, kill_pid, processes_under

    evidence: dict[str, Any] = {}
    for p in processes_under(install_dir) if install_dir.exists() else []:
        kill_pid(p["ProcessId"], force=True)
        evidence.setdefault("killed_before_uninstall", []).append(p["ExecutablePath"])
    uninstaller = install_dir / "uninstall.exe"
    if uninstaller.is_file():
        # `_?=` 讓解除安裝程式就地執行並等它結束（否則它複製到暫存目錄後立即返回）；
        # 代價是它刪不掉自己，所以之後只剩 uninstall.exe 時才刪除整個目錄。
        proc = subprocess.run(
            [str(uninstaller), "/S", f"_?={install_dir}"], timeout=300, check=False
        )
        evidence["uninstaller_exit"] = proc.returncode
    remaining = (
        sorted(str(p.relative_to(install_dir)) for p in install_dir.rglob("*"))
        if install_dir.exists()
        else []
    )
    evidence["left_by_uninstaller"] = remaining
    if remaining in ([], ["uninstall.exe"]):
        shutil.rmtree(install_dir, ignore_errors=True)
    # 解除安裝程式在 /S 時不刪 app 資料；測試前不存在、而且只有 window-state 外掛的檔案時才移除。
    roaming = Path(os.environ["APPDATA"]) / IDENTIFIER
    if not before["roaming_app_dir"] and roaming.is_dir():
        contents = sorted(p.name for p in roaming.iterdir())
        evidence["roaming_app_dir_contents"] = contents
        if contents in ([], [".window-state.json"]):
            shutil.rmtree(roaming)
    after = baseline()
    evidence.update(
        install_dir_exists=install_dir.exists(),
        uninstall_entries=after["uninstall_entries"],
        shortcuts=after["shortcuts"],
        shortcuts_before=before["shortcuts"],
        roaming_app_dir=[before["roaming_app_dir"], after["roaming_app_dir"]],
        roaming_settings_dir=[
            before["roaming_settings_dir"],
            after["roaming_settings_dir"],
        ],
        leftover_processes=leftover_processes(),
    )
    evidence["ok"] = (
        evidence.get("uninstaller_exit") == 0
        and remaining in ([], ["uninstall.exe"])
        and not evidence["install_dir_exists"]
        and not after["uninstall_entries"]
        and after["shortcuts"] == before["shortcuts"]
        and after["roaming_app_dir"] == before["roaming_app_dir"]
        and after["roaming_settings_dir"] == before["roaming_settings_dir"]
        and not any(evidence["leftover_processes"].values())
    )
    return evidence


def scenario_installed_media(run: Run, app_exe: Path, install_dir: Path) -> None:
    """已安裝的程式：引擎從安裝目錄啟動、第一次啟動自動套用 GPU 組合、影片翻譯雙語字幕。"""
    from update_acceptance import processes_under

    config_dir = run.dir / "config-installed"
    config_dir.mkdir()
    player = Player()
    try:
        with AppSession(run, config_dir, "installed", app=app_exe) as app:
            wd = app.wd
            main = wd.s("GET", "/window")
            app.wait_ready()
            procs = processes_under(install_dir)
            apps = [p for p in procs if p["Name"].lower() == app_exe.name.lower()]
            engines = [p for p in procs if p["Name"].lower() == "kikitori-engine.exe"]
            expected_engine = install_dir / "kikitori-engine" / "kikitori-engine.exe"
            run.check(
                "installed: engine starts from the install dir (child of the app)",
                len(apps) == 1
                and len(engines) == 1
                and os.path.normcase(engines[0]["ExecutablePath"])
                == os.path.normcase(str(expected_engine))
                and engines[0]["ParentProcessId"] == apps[0]["ProcessId"],
                {
                    "app": [(p["ProcessId"], p["ExecutablePath"]) for p in apps],
                    "engine": [
                        (p["ProcessId"], p["ParentProcessId"], p["ExecutablePath"])
                        for p in engines
                    ],
                },
            )
            notice = wd.wait(
                "first-run notice",
                lambda: app.notice(FIRST_RUN_KEY),
                timeout=120,
            )
            settings = read_settings(config_dir)
            asr = settings["asr"]
            run.check(
                "installed: first-run auto-apply notice with the GPU combination",
                FIRST_RUN_NOTICE in notice["text"]
                and "GPU" in notice["text"]
                and asr["device"] == "cuda"
                and asr["final_model"] in notice["text"],
                {"notice": notice, "asr": asr, "translation": settings["translation"]},
            )
            time.sleep(4)  # 缺模型與「更適合的組合」的背景檢查
            wd.screenshot(SCREENSHOTS / "13-installed-first-run.png")
            other = [n for n in app.notices() if n["key"] != FIRST_RUN_KEY]
            run.check(
                "installed: no other prompts after first run",
                not any(
                    OFFER_TEXT in n["text"] or "還需要下載" in n["text"] for n in other
                ),
                other,
            )

            chosen = app.choose_virtual_loopback()
            wd.click("#start")
            t0 = time.monotonic()
            wd.wait(
                "listening",
                lambda: wd.text("#status-label") == "正在聆聽",
                timeout=180,
            )
            run.check(
                "installed: media mode + virtual loopback → 正在聆聽",
                TARGET_OUTPUT in chosen,
                {"device": chosen, "load_secs": round(time.monotonic() - t0, 1)},
            )
            overlay = app.window("kikitori 字幕")
            wd.switch(main)
            pid = apps[0]["ProcessId"]
            overlay_hwnd = find_windows(pid).get("kikitori 字幕")
            main_rect = window_rect(find_windows(pid)["kikitori"])
            overlay_home = window_rect(overlay_hwnd) if overlay_hwnd else None
            if overlay_hwnd:
                move_window(overlay_hwnd, main_rect[0] + 140, main_rect[1] + 400)
            playing = play_async(player, "anime", load_track("anime").audio)
            watched = watch_subtitles(wd, main, overlay, playing, settle=6.0)
            rows = watched["rows"]
            finals = [r for r in rows if not r["partial"]]
            partial_first = [
                i for i, seen in watched["seen_partial_before_final"].items() if seen
            ]
            bad_tc = {
                i: simplified_chars(r["tr"])
                for i, r in enumerate(rows)
                if simplified_chars(r["tr"])
            }
            jp = sum(1 for r in finals if KANA.search(r["orig"]))
            run.check(
                "installed: transcript partial → final bilingual (Traditional Chinese)",
                len(finals) >= 6
                and len(partial_first) >= 4
                and not any(r["partial"] for r in rows)
                and all(r["tr"] for r in finals)
                and not bad_tc
                and jp >= len(finals) - 1,
                {
                    "rows": len(rows),
                    "finals": len(finals),
                    "rows_seen_partial_first": len(partial_first),
                    "japanese_originals": jp,
                    "simplified": bad_tc,
                    "langs": [r["lang"] for r in rows],
                },
            )
            wd.screenshot(PRIVATE_SCREENSHOTS / "installed-transcript.png")
            wd.switch(overlay)
            ov = wd.script(OVERLAY_JS)
            wd.switch(main)
            ov_bad = [
                simplified_chars(line["tr"])
                for line in ov["lines"]
                if simplified_chars(line["tr"])
            ]
            if overlay_hwnd:
                hide_automation_consoles(window_rect(overlay_hwnd))
                time.sleep(0.5)
                capture_screen_rect(
                    window_rect(overlay_hwnd),
                    PRIVATE_SCREENSHOTS / "installed-overlay-screen.png",
                )
            run.check(
                "installed: overlay shows bilingual subtitles (Traditional Chinese)",
                watched["overlay_max_lines"] >= 1
                and watched["overlay_partial_seen"]
                and len(ov["lines"]) >= 1
                and all(line["tr"] for line in ov["lines"])
                and not ov_bad,
                {
                    "max_lines_seen": watched["overlay_max_lines"],
                    "partial_seen": watched["overlay_partial_seen"],
                    "now": len(ov["lines"]),
                    "simplified": ov_bad,
                },
            )
            if overlay_hwnd and overlay_home:
                move_window(overlay_hwnd, overlay_home[0], overlay_home[1])
            wd.click("#start")
            wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
            run.check(
                "installed: stop → 待命中",
                wd.text("#start") == "開始",
                wd.text("#status-label"),
            )
    finally:
        player.close()


def scenario_missing_models(run: Run, app_exe: Path | None = None) -> None:
    """建議組合選到沒下載的 ASR 模型 →「下載缺少的模型」→ 進度 → 完成後可以開始。

    不動使用者的模型快取：app 與引擎的 `LOCALAPPDATA` 指到暫存目錄（模型快取與 GPU
    加速元件都在 `%LOCALAPPDATA%\\kikitori`），只把 `small` 複製過去。沒有 cuBLAS 時
    這台電腦的建議組合是 CPU 的 base／small，所以只缺 base（約 140 MB）。
    """
    from kikitori.asr import default_cache_dir

    real_cache = default_cache_dir()
    real_before = sorted(p.name for p in real_cache.iterdir())
    local = run.dir / "localappdata"
    cache = local / "kikitori" / "models"
    shutil.copytree(real_cache / "small", cache / "small")
    config_dir = run.dir / "config-missing"
    config_dir.mkdir()
    player = Player()
    try:
        with AppSession(
            run,
            config_dir,
            "missing",
            app=app_exe,
            extra_env={"LOCALAPPDATA": str(local)},
        ) as app:
            wd = app.wd
            app.wait_ready()
            first = wd.wait(
                "first-run notice", lambda: app.notice(FIRST_RUN_KEY), timeout=120
            )
            asr = read_settings(config_dir)["asr"]
            chosen = list(dict.fromkeys([asr["fast_model"], asr["final_model"]]))
            expected_missing = [m for m in chosen if not (cache / m).exists()]
            offer = wd.wait(
                "missing-models prompt",
                lambda: (
                    (n := app.notice(MISSING_KEY)) and "還需要下載" in n["text"] and n
                ),
                timeout=60,
            )
            listed = offer["text"].split("還需要下載 ", 1)[1].split("（", 1)[0]
            run.check(
                "missing models: prompt lists exactly the chosen models that are not downloaded",
                bool(expected_missing)
                and listed.split("、") == expected_missing
                and "下載缺少的模型" in offer["actions"],
                {
                    "first_run_notice": first["text"],
                    "asr": asr,
                    "expected_missing": expected_missing,
                    "notice": offer,
                },
            )
            wd.screenshot(SCREENSHOTS / "14-missing-models-offer.png")
            app.click_notice_action(MISSING_KEY, "下載缺少的模型")
            texts: list[str] = []
            positions: set[int] = set()
            gaps: list[float] = []
            shot = False
            t0 = time.monotonic()

            def progress() -> dict[str, Any] | None:
                nonlocal shot
                state = wd.script(
                    "const ns = Array.from(document.querySelectorAll('#notices .notice'));"
                    "const i = ns.findIndex(n => n.dataset.key === arguments[0]);"
                    "return i < 0 ? null : {index: i, count: ns.length,"
                    " text: ns[i].querySelector('p').textContent,"
                    " state: ns[i].dataset.state,"
                    " actions: Array.from(ns[i].querySelectorAll('.notice-actions button')).map(b => b.textContent)};",
                    MISSING_KEY,
                )
                if state is None:
                    gaps.append(round(time.monotonic() - t0, 1))
                    return None
                positions.add(state["index"])
                if not texts or texts[-1] != state["text"]:
                    texts.append(state["text"])
                if (
                    not shot
                    and state["state"] == "downloading"
                    and "%" in state["text"]
                ):
                    wd.screenshot(SCREENSHOTS / "15-missing-models-progress.png")
                    shot = True
                if state["state"] == "failed":
                    raise RuntimeError(f"download failed: {state['text']}")
                return state if state["state"] == "done" else None

            done = wd.wait("download done", progress, timeout=900, interval=0.3)
            secs = round(time.monotonic() - t0, 1)
            percents = [
                int(m.group(1))
                for t in texts
                if t.startswith("正在下載 ") and (m := re.search(r"(\d+)%", t))
            ]
            run.check(
                "missing models: click downloads with progress in the same message, then done",
                len(percents) >= 2
                and percents == sorted(percents)
                and done["text"] == "模型已下載完成，可以按「開始」了。"
                and not gaps
                and all((cache / m).is_dir() for m in expected_missing),
                {
                    "secs": secs,
                    "texts": texts[:3] + ["…"] + texts[-3:]
                    if len(texts) > 6
                    else texts,
                    "progress_updates": len(percents),
                    "notice_positions": sorted(positions),
                    "polls_without_the_notice": gaps,
                    "done": done,
                },
            )
            wd.screenshot(SCREENSHOTS / "16-missing-models-done.png")

            device = app.choose_virtual_loopback()
            wd.click("#start")
            t0 = time.monotonic()
            wd.wait(
                "listening",
                lambda: wd.text("#status-label") == "正在聆聽",
                timeout=180,
            )
            load = round(time.monotonic() - t0, 1)
            en = tts_clip("tts_en", EN_LINES, EN_VOICE, "en")
            player.play("tts_en", en.audio)
            rows = wd.wait(
                "a final subtitle",
                lambda: (
                    (r := wd.script(ROWS_JS))
                    and any(not x["partial"] and x["tr"] for x in r)
                    and r
                ),
                timeout=60,
            )
            time.sleep(4)
            rows = wd.script(ROWS_JS)
            wd.click("#start")
            wd.wait("idle", lambda: wd.text("#status-label") == "待命中", timeout=30)
            run.check(
                "missing models: 開始 works after the download (CPU base/small)",
                any(not r["partial"] and r["tr"] for r in rows),
                {
                    "device": device,
                    "load_secs": load,
                    "rows": [(r["lang"], r["orig"], r["tr"]) for r in rows],
                },
            )
    finally:
        player.close()
    real_after = sorted(p.name for p in real_cache.iterdir())
    run.check(
        "missing models: the user's model cache was not touched",
        real_before == real_after,
        {"real_cache": str(real_cache), "models": real_after},
    )


CPU_COMBO = {
    "device": "cpu",
    "fast_model": "base",
    "final_model": "small",
    "fast_compute_type": "",
    "final_compute_type": "",
}


def scenario_better_combo(run: Run, app_exe: Path | None = None) -> None:
    """設定存成 CPU 組合、這台電腦建議 GPU →「有更適合這台電腦的組合」：套用、不要再提醒。"""
    config_dir = run.dir / "config-offer"
    config_dir.mkdir()
    # 第一次啟動自動套用 GPU 組合，再到模型頁存成 CPU 組合（使用者自己選的）
    with AppSession(run, config_dir, "offer-1", app=app_exe) as app:
        wd = app.wd
        app.wait_ready()
        wd.wait("first-run notice", lambda: app.notice(FIRST_RUN_KEY), timeout=120)
        status = save_combo(app, CPU_COMBO)
        hidden_after_save = app.notice(OFFER_KEY) is None
        app.show_page("transcript")
        app.show_page("models")  # 進入模型頁會再比較一次
        offer = wd.wait(
            "offer on models page", lambda: app.notice(OFFER_KEY), timeout=60
        )
        run.check(
            "offer: shown on the models page after saving a CPU combination",
            OFFER_TEXT in offer["text"]
            and offer["actions"] == ["套用", "不要再提醒", "關閉"]
            and read_settings(config_dir)["asr"]["device"] == "cpu"
            and hidden_after_save,
            {
                "save_status": status,
                "hidden_right_after_save": hidden_after_save,
                "notice": offer,
            },
        )

    # 再啟動：啟動時就提示；「套用」寫入 GPU 組合並收起
    with AppSession(run, config_dir, "offer-2", app=app_exe) as app:
        wd = app.wd
        app.wait_ready()
        offer = wd.wait("offer at startup", lambda: app.notice(OFFER_KEY), timeout=60)
        wd.screenshot(SCREENSHOTS / "17-better-combination-offer.png")
        app.click_notice_action(OFFER_KEY, "套用")
        wd.wait(
            "applied",
            lambda: read_settings(config_dir)["asr"]["device"] == "cuda",
            timeout=60,
        )
        time.sleep(3)
        after_apply = app.notices()
        asr = read_settings(config_dir)["asr"]
        run.check(
            "offer: shown at startup; 套用 applies the GPU combination and hides it",
            OFFER_TEXT in offer["text"]
            and asr["device"] == "cuda"
            and not any(n["key"] == OFFER_KEY for n in after_apply),
            {"offer": offer["text"], "asr": asr, "notices_after": after_apply},
        )
        wd.screenshot(SCREENSHOTS / "18-better-combination-applied.png")

        # 再存成 CPU 組合 → 模型頁再出現提示 →「不要再提醒」
        save_combo(app, CPU_COMBO)
        app.show_page("transcript")
        app.show_page("models")
        fingerprint = wd.wait(
            "offer again",
            lambda: wd.script(
                "const n = document.querySelector("
                "`#notices .notice[data-key='recommendation-offer']`);"
                "return n ? n.dataset.fingerprint : null;"
            ),
            timeout=60,
        )
        app.click_notice_action(OFFER_KEY, "不要再提醒")
        dismissed = wd.wait(
            "dismissed fingerprint saved",
            lambda: (
                read_settings(config_dir).get("ui", {}).get("dismissed_recommendations")
            ),
            timeout=30,
        )
        run.check(
            "offer: 不要再提醒 hides it and stores the fingerprint in settings.ui",
            dismissed == [fingerprint] and app.notice(OFFER_KEY) is None,
            {
                "fingerprint": fingerprint,
                "ui": read_settings(config_dir)["ui"],
                "asr_device": read_settings(config_dir)["asr"]["device"],
            },
        )
    before = (config_dir / "settings.json").read_bytes()

    # 再啟動：同一個推薦不再提示（啟動、進入模型頁、重新偵測都不會）
    with AppSession(run, config_dir, "offer-3", app=app_exe) as app:
        wd = app.wd
        app.wait_ready()
        seen: list[dict[str, Any]] = []

        def collect(seconds: float) -> None:
            deadline = time.monotonic() + seconds
            while time.monotonic() < deadline:
                for n in app.notices():
                    if n not in seen:
                        seen.append(n)
                time.sleep(0.5)

        collect(10)
        app.show_page("models")
        collect(6)
        wd.click("#models-refresh")
        collect(10)
        wd.screenshot(SCREENSHOTS / "19-better-combination-dismissed.png")
        after = (config_dir / "settings.json").read_bytes()
        run.check(
            "offer: not shown again after relaunch for the same recommendation",
            not any(OFFER_TEXT in n["text"] for n in seen) and before == after,
            {"notices": seen, "settings_unchanged": before == after},
        )


def run_installed() -> int:
    """CI 建置的安裝檔：靜默安裝 → 已安裝程式的驗收（含缺少模型與更適合組合）→ 靜默解除安裝。"""
    from update_acceptance import baseline

    run = Run("installed")
    installer = tool_path("KIKITORI_E2E_INSTALLER")
    print(f"results: {run.dir}\ninstaller: {installer}")
    before = baseline()
    if before["uninstall_entries"]:
        run.check(
            "no kikitori installed for this user before the test",
            False,
            before["uninstall_entries"],
        )
        return finish(run)
    install_dir = (
        Path(tempfile.mkdtemp(prefix="kikitori-ci-install-")).resolve() / "kikitori"
    )
    app_exe = install_dir / "kikitori-app.exe"
    try:
        entries = install_ci_build(installer, install_dir)["entries"]
        run.check(
            "silent install (per-user) registers 0.1.0 in HKCU",
            len(entries) == 1
            and entries[0].get("DisplayVersion") == "0.1.0"
            and app_exe.is_file()
            and (install_dir / "kikitori-engine" / "kikitori-engine.exe").is_file(),
            {"install_dir": str(install_dir), "entries": entries},
        )
        for name, scenario in (
            (
                "installed media",
                lambda: scenario_installed_media(run, app_exe, install_dir),
            ),
            ("missing models", lambda: scenario_missing_models(run, app_exe)),
            ("better combination", lambda: scenario_better_combo(run, app_exe)),
        ):
            try:
                scenario()
            except Exception:
                logger.exception("%s aborted", name)
                run.steps[f"{name}: aborted"] = {
                    "passed": False,
                    "detail": "exception (see log)",
                }
    finally:
        evidence = uninstall_ci_build(install_dir, before)
        run.check("silent uninstall cleans up", evidence.pop("ok"), evidence)
        shutil.rmtree(install_dir.parent, ignore_errors=True)
    return finish(run)


def run_single(name: str, scenario: Any) -> int:
    """在 `KIKITORI_E2E_APP` 的建置上執行一個情境。"""
    run = Run(name)
    print(f"results: {run.dir}")
    try:
        scenario(run)
    except Exception:
        logger.exception("%s aborted", name)
        run.steps["aborted"] = {"passed": False, "detail": "exception (see log)"}
    left = leftover_processes()
    run.check("no leftover processes", not any(left.values()), left)
    return finish(run)


MENU = """
kikitori 桌面程式驗收（WebDriver）
  1) 執行完整流程（會播放測試聲音到虛擬喇叭）
  2) 第一次啟動：空的設定資料夾自動套用建議組合，再啟動一次確認不重複套用（不播放聲音）
  3) CI 的安裝檔：靜默安裝 → 第一次啟動＋影片翻譯、缺少模型、更適合的組合 → 解除安裝
  4) 缺少模型：建議組合選到沒下載的模型 → 一鍵下載 → 開始（下載約 140 MB）
  5) 更適合的組合：存成 CPU 組合 → 提示 → 套用／不要再提醒 → 再啟動不再提示
  6) v0.2.0：13 個介面語系＋術語表編輯器（不播放聲音）
  7) v0.2.0：語言 ko → es 預覽、開始前下載（隔離的模型快取，約 0.6 GB）、刪除模型
  8) v0.2.0：只顯示原文（翻譯服務連不上）、執行中打開翻譯的引導
  9) v0.2.0：匯出、自動存檔、OBS 文字檔、穩定前綴、音量與延遲、快速鍵
  0) 離開
"""

BATCH = {
    "run": lambda: run_gui(),
    "first-run": lambda: run_first_run(),
    "installed": lambda: run_installed(),
    "missing-models": lambda: run_single("missing-models", scenario_missing_models),
    "better-combo": lambda: run_single("better-combo", scenario_better_combo),
    # v0.2.0 的情境在 gui_v020.py（延遲匯入：它會匯入本模組）
    "v020-ui": lambda: _v020("v020-ui"),
    "v020-languages": lambda: _v020("v020-languages"),
    "v020-original": lambda: _v020("v020-original"),
    "v020-session": lambda: _v020("v020-session"),
}


def _v020(name: str) -> int:
    import gui_v020

    return gui_v020.BATCH[name]()


def main(argv: list[str]) -> int:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    if len(argv) == 2 and argv[0] == "--batch" and argv[1] in BATCH:
        return BATCH[argv[1]]()
    if argv:
        print(f"usage: gui_acceptance.py [--batch {'|'.join(BATCH)}]", file=sys.stderr)
        return 2
    choices = dict(zip("123456789", BATCH.values(), strict=True))
    while True:
        print(MENU)
        try:
            choice = input("請輸入編號：").strip()
        except EOFError:
            return 0
        if choice in choices:
            return choices[choice]()
        if choice == "0":
            return 0
        print(f"無效的選項：{choice!r}")


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
