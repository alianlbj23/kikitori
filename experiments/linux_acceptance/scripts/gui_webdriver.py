"""Linux GUI 驗收：Xvfb 上啟動安裝好的 kikitori（deb），以 tauri-driver + WebKitWebDriver 操作。

暫時性實驗工具，不屬於正式程式碼；只用標準庫（容器裡是系統的 python3）。在 `kla-gui` 容器內
以一般使用者執行（見 `../run.sh`）：

1. 啟動 Xvfb（:99）、PulseAudio（null sink `speakers`）；寫入測試用設定（CPU、主機的 Ollama），
   模型快取連到唯讀掛載的 /models。
2. 啟動 tauri-driver，建立 WebDriver session（`tauri:options.application`）。
3. 等主視窗顯示「待命中」（真的引擎已就緒），截圖。
4. 按「開始」，播放 `anime.wav` 到 null sink；等字幕紀錄出現完整字幕；切到字幕視窗讀出字幕並截圖；
   另以 ImageMagick `import` 擷取整個 X 畫面（看沒有合成器時透明視窗的樣子）。
5. 按「停止」、結束 session。

截圖存到 /results/gui_*.png。`KIKITORI_ENGINE_FAKE=1` 時引擎用假模式（不需要模型與 Ollama）。
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DRIVER = "http://127.0.0.1:4444"
APP = os.environ.get("KLA_APP", "/usr/bin/kikitori-app")
RESULTS = Path(os.environ.get("KLA_RESULTS", "/results"))
TRACK = Path(os.environ.get("KLA_TRACK", "/data/anime.wav"))
DISPLAY = ":99"
HTTP_TIMEOUT = 60.0
SETTINGS = {
    "schema_version": 2,
    "asr": {"fast_model": "base", "final_model": "small", "device": "cpu"},
    "translation": {
        "base_url": os.environ.get(
            "KLA_OLLAMA", "http://host.docker.internal:11434/v1"
        ),
        "fast_model": "qwen3.5:4b",
        "final_model": "qwen3.5:4b",
    },
}


class WebDriverError(RuntimeError):
    """WebDriver 回傳錯誤。"""


def call(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    """送一個 W3C WebDriver 請求，回傳 `value`。"""
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(
        DRIVER + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:
            return json.loads(resp.read())["value"]
    except urllib.error.HTTPError as exc:
        raise WebDriverError(f"{method} {path}: {exc.read().decode()[:500]}") from exc


def wait(predicate: Any, what: str, timeout: float = 60.0, step: float = 0.5) -> Any:
    """等到 `predicate()` 回傳真值。"""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    raise TimeoutError(f"timed out waiting for {what}")


def background(
    cmd: list[str], log: str, env: dict[str, str] | None = None
) -> subprocess.Popen[bytes]:
    """背景啟動，輸出寫到 /tmp/<log>。"""
    out = open(f"/tmp/{log}", "wb")  # noqa: SIM115 - 行程結束前都要寫
    return subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT, env=env)


def setup() -> dict[str, str]:
    """Xvfb、PulseAudio、設定檔與模型快取；回傳給 app 用的環境變數。"""
    env = dict(os.environ, DISPLAY=DISPLAY)
    if (
        subprocess.run(
            ["xdpyinfo", "-display", DISPLAY],
            capture_output=True,
            check=False,
            timeout=10,
        ).returncode
        != 0
    ):
        background(
            ["Xvfb", DISPLAY, "-screen", "0", "1600x1000x24", "-nolisten", "tcp"],
            "xvfb.log",
        )
        wait(
            lambda: (
                subprocess.run(
                    ["xdpyinfo", "-display", DISPLAY],
                    capture_output=True,
                    check=False,
                    timeout=10,
                ).returncode
                == 0
            ),
            "Xvfb",
            timeout=15,
        )
    if (
        subprocess.run(
            ["pactl", "info"], capture_output=True, check=False, timeout=10
        ).returncode
        != 0
    ):
        subprocess.run(
            [
                "pulseaudio",
                "-D",
                "--exit-idle-time=-1",
                "--log-target=file:/tmp/pa.log",
            ],
            check=True,
            timeout=10,
        )
        wait(
            lambda: (
                subprocess.run(
                    ["pactl", "info"], capture_output=True, check=False, timeout=10
                ).returncode
                == 0
            ),
            "PulseAudio",
            15,
        )
    sinks = subprocess.run(
        ["pactl", "list", "short", "sinks"],
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout
    if "\tspeakers\t" not in sinks:
        subprocess.run(
            [
                "pactl",
                "load-module",
                "module-null-sink",
                "sink_name=speakers",
                "sink_properties=device.description=Speakers",
            ],
            check=True,
            capture_output=True,
            timeout=10,
        )
    subprocess.run(["pactl", "set-default-sink", "speakers"], check=True, timeout=10)
    home = Path.home()
    cache = home / ".cache" / "kikitori"
    cache.mkdir(parents=True, exist_ok=True)
    if not (cache / "models").exists() and Path("/models").is_dir():
        (cache / "models").symlink_to("/models")
    config = home / ".config" / "kikitori"
    config.mkdir(parents=True, exist_ok=True)
    (config / "settings.json").write_text(json.dumps(SETTINGS), encoding="utf-8")
    return env


def find(session: str, css: str) -> str:
    """找元素，回傳 element id。"""
    value = call(
        "POST", f"/session/{session}/element", {"using": "css selector", "value": css}
    )
    return next(iter(value.values()))


def text(session: str, css: str) -> str:
    """元素的可見文字（找不到時回傳空字串）。"""
    try:
        return call("GET", f"/session/{session}/element/{find(session, css)}/text")
    except WebDriverError:
        return ""


def script(session: str, source: str, *args: Any) -> Any:
    """在目前的視窗執行 JavaScript。"""
    return call(
        "POST",
        f"/session/{session}/execute/sync",
        {"script": source, "args": list(args)},
    )


def screenshot(session: str, name: str) -> Path:
    """WebDriver 視窗截圖。"""
    path = RESULTS / f"gui_{name}.png"
    path.write_bytes(base64.b64decode(call("GET", f"/session/{session}/screenshot")))
    return path


def x_screenshot(name: str, env: dict[str, str]) -> Path:
    """整個 X 畫面（含視窗管理員沒有畫的部分）。"""
    path = RESULTS / f"gui_{name}.png"
    subprocess.run(
        ["import", "-window", "root", str(path)], env=env, check=True, timeout=30
    )
    return path


def run() -> int:
    """完整流程；回傳結束碼。"""
    RESULTS.mkdir(parents=True, exist_ok=True)
    env = setup()
    fake = os.environ.get("KIKITORI_ENGINE_FAKE") == "1"
    report: dict[str, Any] = {"engine": "fake" if fake else "real", "app": APP}
    compositor = None
    if os.environ.get("KLA_COMPOSITOR") == "1":
        # 有合成器時透明視窗才會真的透明；picom 的 xrender 後端不需要 GPU。
        compositor = background(["picom", "--backend", "xrender"], "picom.log", env)
        report["compositor"] = "picom (xrender)"
    driver = background(
        [
            "tauri-driver",
            "--port",
            "4444",
            "--native-driver",
            shutil.which("WebKitWebDriver") or "WebKitWebDriver",
        ],
        "tauri-driver.log",
        env,
    )
    session = ""
    try:
        wait(lambda: _driver_up(), "tauri-driver", timeout=20)
        t0 = time.monotonic()
        caps = {
            "capabilities": {"alwaysMatch": {"tauri:options": {"application": APP}}}
        }
        session = call("POST", "/session", caps)["sessionId"]
        report["title"] = call("GET", f"/session/{session}/title")
        status = wait(
            lambda: text(session, "#status-label") == "待命中" and "待命中",
            "engine ready",
            timeout=120,
        )
        report["engine_ready_s"] = round(time.monotonic() - t0, 2)
        report["status_label"] = status
        report["device_options"] = script(
            session,
            "return [...document.querySelectorAll('#device option')].map(o => o.textContent)",
        )
        report["fonts"] = script(
            session,
            "const s = getComputedStyle(document.querySelector('.brand, h1, body'));"
            " return {family: s.fontFamily, check_ja: document.fonts.check('16px \"Noto Sans CJK JP\"')}",
        )
        screenshot(session, "main_ready")
        x_screenshot("x_main_ready", env)

        call("POST", f"/session/{session}/element/{find(session, '#start')}/click", {})
        wait(
            lambda: text(session, "#status-label") == "正在聆聽", "running", timeout=180
        )
        report["session_running_s"] = round(time.monotonic() - t0, 2)
        time.sleep(2.5)  # null sink 的 monitor 約 1.9 s 後才開始送資料
        player = subprocess.Popen(["paplay", "-d", "speakers", str(TRACK)], env=env)
        try:
            time.sleep(12)
            handles = call("GET", f"/session/{session}/window/handles")
            report["window_handles"] = len(handles)
            main_handle = call("GET", f"/session/{session}/window")
            overlay_seen: list[str] = []
            for handle in handles:
                if handle == main_handle:
                    continue
                call("POST", f"/session/{session}/window", {"handle": handle})
                if "overlay" in script(session, "return location.pathname"):
                    overlay_seen.append(text(session, "#lines"))
                    report["overlay_rect"] = call(
                        "GET", f"/session/{session}/window/rect"
                    )
                    report["overlay_background"] = script(
                        session,
                        "return [getComputedStyle(document.documentElement).backgroundColor,"
                        " getComputedStyle(document.body).backgroundColor]",
                    )
                    screenshot(session, "overlay_webdriver")
            report["overlay_lines_mid_playback"] = overlay_seen
            x_screenshot("x_during_playback", env)
            call("POST", f"/session/{session}/window", {"handle": main_handle})
            report["global_shortcut"] = check_shortcut(session, env)
            player.wait(timeout=60)
        finally:
            if player.poll() is None:
                player.kill()
        time.sleep(10)
        report["transcript"] = text(session, "#transcript")
        screenshot(session, "main_after")
        call("POST", f"/session/{session}/element/{find(session, '#start')}/click", {})
        wait(
            lambda: text(session, "#status-label") == "待命中",
            "idle after stop",
            timeout=30,
        )
        report["stopped"] = True
    finally:
        if session:
            try:
                call("DELETE", f"/session/{session}")
            except (WebDriverError, OSError) as exc:
                print(f"closing session failed: {exc}", file=sys.stderr)
        driver.terminate()
        driver.wait(timeout=10)
        if compositor is not None:
            compositor.terminate()
        (RESULTS / "gui_webdriver.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    ok = (
        bool(report.get("stopped"))
        and any(report.get("overlay_lines_mid_playback", []))
        and bool(report.get("transcript"))
    )
    print("PASS" if ok else "FAIL")
    return 0 if ok else 1


def check_shortcut(session: str, env: dict[str, str]) -> dict[str, Any]:
    """以 xdotool 送出 Ctrl+Alt+K（X11 全域快捷鍵），看「滑鼠穿透」開關有沒有切換。"""
    state = "return document.getElementById('overlay-click-through').checked"
    before = script(session, state)
    subprocess.run(["xdotool", "key", "ctrl+alt+k"], env=env, check=True, timeout=10)
    try:
        after = wait(
            lambda: script(session, state) != before and "changed", "toggle", timeout=5
        )
    except TimeoutError:
        after = "unchanged"
    result = {"before": before, "after": script(session, state), "result": after}
    if after == "changed":
        # 切回來，避免影響後面的步驟。
        subprocess.run(
            ["xdotool", "key", "ctrl+alt+k"], env=env, check=True, timeout=10
        )
    return result


def _driver_up() -> bool:
    try:
        with urllib.request.urlopen(DRIVER + "/status", timeout=2):
            return True
    except OSError:
        return False


if __name__ == "__main__":
    raise SystemExit(run())
