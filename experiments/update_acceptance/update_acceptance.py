"""Windows 安裝檔與自動更新的端到端驗收（暫時性實驗，不屬於正式程式碼）。

位於 experiments 層：把 `docs/specs/release-update.md`「需要實機確認」的自動更新一輪在本機跑完——
建置引擎 → 以拋棄式金鑰簽章建置 0.1.0 與 0.1.1 的 NSIS 安裝檔（版本、更新器公鑰與端點以
`--config` 覆寫，不改 repo 的設定）→ `scripts/release.py collect／verify／manifest` 產生
`latest.json` → 本機 HTTP 伺服器提供 manifest 與安裝檔 → 靜默安裝 0.1.0 → 以 WebDriver
（tauri-driver ＋ msedgedriver）操作已安裝的程式：檢查版本與引擎、改設定、壞簽章被拒、
啟動時找到 0.1.1 → 下載並安裝 → 重新啟動後是 0.1.1、引擎啟動、設定保留、同版本顯示
「目前已是最新版本。」→ 靜默解除安裝並確認沒有殘留。

只用標準庫（WebDriver 用 urllib）。不讀任何正式的簽章私鑰：金鑰在工作目錄當場產生。
執行：`uv run python experiments/update_acceptance/update_acceptance.py`（互動選單），
或 `--all`／`--steps tools,engine,build,manifest,scenario`（非互動）。結果見同目錄 README.md。
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.server
import io
import json
import logging
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
import winreg
import zipfile
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parents[2]
APP_DIR = ROOT / "app"
PRODUCT = "kikitori"
OLD_VERSION = "0.1.0"
NEW_VERSION = "0.1.1"
IDENTIFIER = "io.github.alianlbj23.kikitori"
MAIN_BINARY = "kikitori-app.exe"

# 釘住的工具版本（下載後核對雜湊或 Authenticode 簽章）。
MINISIGN_URL = "https://github.com/jedisct1/minisign/releases/download/0.12/minisign-0.12-win64.zip"
MINISIGN_SHA256 = "37b600344e20c19314b2e82813db2bfdcc408b77b876f7727889dbd46d539479"
"""winget-pkgs 的 jedisct1.minisign 0.12 manifest 記載的 SHA-256。"""
EDGEDRIVER_VERSION = "156.0.4314.58"
"""必須與本機 WebView2 Runtime 的版本相同。"""
EDGEDRIVER_URL = (
    f"https://msedgedriver.microsoft.com/{EDGEDRIVER_VERSION}/edgedriver_win64.zip"
)
TAURI_DRIVER_VERSION = "2.1.0"

DEFAULT_PORT = 18731
DRIVER_PORT = 4544
NATIVE_DRIVER_PORT = 4545

BUILD_TIMEOUT = 3600
INSTALL_TIMEOUT = 300
HTTP_TIMEOUT = 30
SESSION_TIMEOUT = 120
UI_TIMEOUT = 60
UPDATE_TIMEOUT = 300

ELEMENT_KEY = "element-6066-11e4-a52e-4f735466cecf"
UNINSTALL_KEY = r"Software\Microsoft\Windows\CurrentVersion\Uninstall"
CREATE_NO_WINDOW = 0x0800_0000

logger = logging.getLogger("update_acceptance")


class AcceptanceError(RuntimeError):
    """某個驗收步驟失敗（訊息附上證據）。"""


# --------------------------------------------------------------------------- 工作目錄


@dataclass
class Work:
    """工作目錄的配置；全部檔案都在 `root` 底下，跑完可整個刪除。"""

    root: Path
    port: int = DEFAULT_PORT
    results: list[dict[str, Any]] = field(default_factory=list)

    @property
    def keys(self) -> Path:
        return self.root / "keys"

    @property
    def tools(self) -> Path:
        return self.root / "tools"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def installers(self) -> Path:
        return self.root / "installers"

    @property
    def install_dir(self) -> Path:
        return self.root / "install"

    @property
    def config_dir(self) -> Path:
        """`KIKITORI_CONFIG_DIR`：引擎的 settings.json 放這裡，不碰使用者的設定。"""
        return self.root / "config"

    @property
    def serve_dir(self) -> Path:
        return self.root / "serve"

    @property
    def minisign(self) -> Path:
        return self.tools / "minisign" / "minisign-win64" / "x86_64" / "minisign.exe"

    @property
    def edgedriver(self) -> Path:
        return self.tools / "msedgedriver" / "msedgedriver.exe"

    @property
    def tauri_driver(self) -> Path:
        return self.tools / "tauri-driver" / "bin" / "tauri-driver.exe"

    @property
    def app_exe(self) -> Path:
        """安裝後的主程式。Tauri 以 Cargo 的 bin 名稱（`kikitori-app`）命名，不是 productName。"""
        return self.install_dir / MAIN_BINARY

    def installer(self, version: str) -> Path:
        return self.installers / f"{PRODUCT}_{version}_x64-setup.exe"

    def record(self, step: str, ok: bool, evidence: dict[str, Any]) -> None:
        """記錄一個步驟的結果，同時寫進 `results.json`。"""
        self.results.append(
            {"step": step, "result": "PASS" if ok else "FAIL", **evidence}
        )
        logger.info("%s: %s %s", step, "PASS" if ok else "FAIL", evidence)
        (self.root / "results.json").write_text(
            json.dumps(self.results, ensure_ascii=False, indent=2), encoding="utf-8"
        )


def run(
    cmd: Sequence[str | Path],
    *,
    timeout: float,
    log: Path | None = None,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """執行外部程式（不經 shell）；失敗時丟 AcceptanceError 並附上輸出尾端。"""
    args = [str(c) for c in cmd]
    logger.debug("run: %s", args)
    try:
        done = subprocess.run(
            args,
            cwd=cwd,
            env=env,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AcceptanceError(f"{args[0]} failed to run: {exc}") from exc
    if log is not None:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(
            done.stdout + "\n--- stderr ---\n" + done.stderr, encoding="utf-8"
        )
    if done.returncode != 0:
        tail = (done.stdout + done.stderr)[-3000:]
        raise AcceptanceError(f"{args[0]} exited with {done.returncode}:\n{tail}")
    return done


def tool_env(work: Work) -> dict[str, str]:
    """Rust、Node 與 minisign 在 PATH 上的環境（cargo／npm 的位置依本機安裝）。"""
    env = dict(os.environ)
    extra = [str(work.minisign.parent)]
    cargo_bin = Path.home() / ".cargo" / "bin"
    if cargo_bin.is_dir():
        extra.append(str(cargo_bin))
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def npm() -> str:
    found = shutil.which("npm.cmd") or shutil.which("npm")
    if not found:
        raise AcceptanceError("npm not found on PATH (Node 24 is required)")
    return found


def download(url: str, dest: Path) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    req = urllib.request.Request(url, headers={"User-Agent": "kikitori-acceptance"})
    try:
        with urllib.request.urlopen(req, timeout=180) as resp, dest.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
    except (urllib.error.URLError, OSError) as exc:
        raise AcceptanceError(f"download of {url} failed: {exc}") from exc


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def powershell_json(script: str, timeout: float = 60) -> Any:
    """執行固定的 PowerShell 查詢（沒有外部輸入）並解析 JSON 輸出。"""
    done = run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
        timeout=timeout,
    )
    text = done.stdout.strip()
    return json.loads(text) if text else None


# --------------------------------------------------------------------------- 步驟：工具


def step_tools(work: Work) -> None:
    """下載 minisign（核對 SHA-256）與 msedgedriver（核對 Authenticode 與版本），安裝 tauri-driver。"""
    evidence: dict[str, Any] = {}
    if not work.minisign.is_file():
        zip_path = work.root / "dl" / "minisign.zip"
        download(MINISIGN_URL, zip_path)
        if sha256(zip_path) != MINISIGN_SHA256:
            raise AcceptanceError(f"minisign checksum mismatch: {sha256(zip_path)}")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work.tools / "minisign")
    evidence["minisign"] = run([work.minisign, "-v"], timeout=30).stdout.strip()

    webview = webview2_version()
    if webview != EDGEDRIVER_VERSION:
        raise AcceptanceError(
            f"WebView2 Runtime is {webview}; update EDGEDRIVER_VERSION to match"
        )
    if not work.edgedriver.is_file():
        zip_path = work.root / "dl" / "edgedriver_win64.zip"
        download(EDGEDRIVER_URL, zip_path)
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(work.tools / "msedgedriver")
    sig = powershell_json(
        "Get-AuthenticodeSignature -LiteralPath '"
        + str(work.edgedriver)
        + "' | Select-Object @{n='status';e={[string]$_.Status}},"
        "@{n='subject';e={$_.SignerCertificate.Subject}} | ConvertTo-Json"
    )
    if sig["status"] != "Valid" or "O=Microsoft Corporation" not in sig["subject"]:
        raise AcceptanceError(f"msedgedriver signature is not valid: {sig}")
    evidence["msedgedriver"] = run(
        [work.edgedriver, "--version"], timeout=30
    ).stdout.strip()
    evidence["webview2"] = webview

    if not work.tauri_driver.is_file():
        env = tool_env(work)
        env["CARGO_TARGET_DIR"] = str(work.root / "target-driver")
        run(
            [
                "cargo",
                "install",
                "tauri-driver",
                "--version",
                f"={TAURI_DRIVER_VERSION}",
                "--locked",
                "--root",
                work.tools / "tauri-driver",
            ],
            timeout=BUILD_TIMEOUT,
            log=work.logs / "tauri-driver-install.log",
            env=env,
        )
    evidence["tauri-driver"] = f"{TAURI_DRIVER_VERSION} ({work.tauri_driver})"
    work.record("tools", True, evidence)


def webview2_version() -> str:
    path = r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        with contextlib.suppress(OSError), winreg.OpenKey(hive, path) as key:
            return str(winreg.QueryValueEx(key, "pv")[0])
    raise AcceptanceError("WebView2 Runtime version not found in the registry")


# --------------------------------------------------------------------------- 步驟：引擎與安裝檔


def step_engine(work: Work) -> None:
    """與 CI 相同：`build.py --smoke`，引擎放到 app/src-tauri/binaries/kikitori-engine/。"""
    env = dict(os.environ, KIKITORI_BUILD_NONINTERACTIVE="1")
    done = run(
        ["uv", "run", "python", "packaging/engine/build.py", "--smoke"],
        cwd=ROOT,
        env=env,
        timeout=BUILD_TIMEOUT,
        log=work.logs / "engine-build.log",
    )
    lines = [
        ln for ln in done.stdout.splitlines() if ln.startswith(("built", "protocol ok"))
    ]
    work.record("1 engine build", True, {"output": lines})


def generate_key(work: Work, name: str) -> tuple[Path, str]:
    """以 `tauri signer generate` 產生拋棄式金鑰（隨機密碼）；回傳 (私鑰路徑, 密碼)。"""
    key = work.keys / f"{name}.key"
    pw_file = work.keys / f"{name}.pw"
    if not key.is_file():
        work.keys.mkdir(parents=True, exist_ok=True)
        password = secrets.token_hex(24)
        pw_file.write_text(password, encoding="utf-8")
        run(
            [
                npm(),
                "exec",
                "--",
                "tauri",
                "signer",
                "generate",
                "--ci",
                "--password",
                password,
                "--write-keys",
                key,
            ],
            cwd=APP_DIR,
            timeout=120,
        )
    return key, pw_file.read_text(encoding="utf-8")


def override_config(work: Work, version: str) -> Path:
    """版本、更新器公鑰與本機端點的覆寫（以 `--config` 合併，不改 repo 的設定）。

    `dangerousInsecureTransportProtocol` 是 tauri-plugin-updater 2.14.1 的設定名稱
    （`src/config.rs`）；只有 http://127.0.0.1 的測試端點需要它。
    """
    pub = (work.keys / "throwaway.key.pub").read_text(encoding="utf-8").strip()
    cfg = {
        "version": version,
        "plugins": {
            "updater": {
                "pubkey": pub,
                "endpoints": [f"http://127.0.0.1:{work.port}/latest.json"],
                "dangerousInsecureTransportProtocol": True,
            }
        },
    }
    path = work.root / "cfg" / f"override-{version}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    return path


def step_build(work: Work) -> None:
    """建置兩個版本的 NSIS 安裝檔（與 release.yml 相同的指令，另加覆寫設定）。"""
    key, password = generate_key(work, "throwaway")
    env = tool_env(work)
    env["CARGO_TARGET_DIR"] = str(work.root / "target")
    env["TAURI_SIGNING_PRIVATE_KEY"] = key.read_text(encoding="utf-8")
    env["TAURI_SIGNING_PRIVATE_KEY_PASSWORD"] = password
    work.installers.mkdir(parents=True, exist_ok=True)
    built: dict[str, Any] = {}
    for version in (OLD_VERSION, NEW_VERSION):
        cfg = override_config(work, version)
        run(
            [
                npm(),
                "run",
                "tauri",
                "build",
                "--",
                "--config",
                "src-tauri/tauri.sidecar.conf.json",
                "--config",
                cfg,
                "--bundles",
                "nsis",
            ],
            cwd=APP_DIR,
            env=env,
            timeout=BUILD_TIMEOUT,
            log=work.logs / f"tauri-build-{version}.log",
        )
        nsis = work.root / "target" / "release" / "bundle" / "nsis"
        for suffix in ("", ".sig"):
            name = f"{PRODUCT}_{version}_x64-setup.exe{suffix}"
            shutil.copy2(nsis / name, work.installers / name)
        built[version] = (
            f"{work.installer(version)} ({work.installer(version).stat().st_size} bytes)"
        )
    work.record("2 build installers", True, built)


# --------------------------------------------------------------------------- 步驟：latest.json


def tauri_sign(work: Work, key_name: str, target: Path) -> None:
    key, password = generate_key(work, key_name)
    run(
        [
            npm(),
            "exec",
            "--",
            "tauri",
            "signer",
            "sign",
            "--private-key-path",
            key,
            "--password",
            password,
            target,
        ],
        cwd=APP_DIR,
        timeout=120,
    )


def release_py(work: Work, *args: str | Path) -> str:
    done = run(
        ["uv", "run", "python", "scripts/release.py", *args],
        cwd=ROOT,
        env=tool_env(work),
        timeout=300,
    )
    return done.stdout


def step_manifest(work: Work) -> None:
    """以 `release.py collect／verify／manifest` 產生 0.1.1 的 latest.json，再做本機版本。

    release.py 的版本號來自 `pyproject.toml`、產品名稱與公鑰來自 tauri.conf.json：用 `--root`
    指到一個只有這兩個檔案的假 repo（版本 0.1.1、公鑰換成拋棄式公鑰）。manifest 要求三個平台
    都在；Linux 與 macOS 在本機沒有實際檔案，以拋棄式金鑰簽章的佔位檔代替（只為了讓產生器
    走完，伺服器不提供它們）。
    """
    fake = work.root / "manifest-root"
    (fake / "app" / "src-tauri").mkdir(parents=True, exist_ok=True)
    (fake / "pyproject.toml").write_text(
        f'[project]\nname = "kikitori"\nversion = "{NEW_VERSION}"\n', encoding="utf-8"
    )
    conf = json.loads(
        (APP_DIR / "src-tauri" / "tauri.conf.json").read_text(encoding="utf-8")
    )
    pub = (work.keys / "throwaway.key.pub").read_text(encoding="utf-8").strip()
    conf["plugins"]["updater"]["pubkey"] = pub
    (fake / "app" / "src-tauri" / "tauri.conf.json").write_text(
        json.dumps(conf, indent=2), encoding="utf-8"
    )

    collected = work.root / "release-in"
    shutil.rmtree(collected, ignore_errors=True)
    out = release_py(
        work,
        "--root",
        fake,
        "collect",
        "--platform",
        "windows",
        "--bundle-dir",
        work.root / "target" / "release" / "bundle",
        "--out",
        collected / "bundle-windows",
    )
    placeholders = {
        "bundle-linux": [
            f"{PRODUCT}_{NEW_VERSION}_amd64.AppImage",
            f"{PRODUCT}_{NEW_VERSION}_amd64.deb",
        ],
        "bundle-macos": [
            f"{PRODUCT}_{NEW_VERSION}_aarch64.dmg",
            f"{PRODUCT}_{NEW_VERSION}_aarch64.app.tar.gz",
        ],
    }
    for folder, names in placeholders.items():
        for name in names:
            path = collected / folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                f"placeholder for {name} (local acceptance test)\n", encoding="utf-8"
            )
            if not name.endswith(".dmg"):
                tauri_sign(work, "throwaway", path)

    verified = release_py(
        work, "--root", fake, "verify", "--dir", collected, "--pubkey-from-config"
    )
    stage = work.root / "release-out"
    shutil.rmtree(stage, ignore_errors=True)
    manifest_out = release_py(
        work,
        "--root",
        fake,
        "manifest",
        "--dir",
        collected,
        "--stage",
        stage,
        "--repo",
        "alianlbj23/kikitori",
        "--tag",
        f"v{NEW_VERSION}",
        "--notes",
        f"kikitori v{NEW_VERSION}",
    )
    manifest = json.loads((stage / "latest.json").read_text(encoding="utf-8"))
    write_served_manifests(work, manifest)
    work.record(
        "2 manifest + signatures",
        True,
        {
            "collect": out.strip().splitlines(),
            "verify (minisign)": verified.strip().splitlines(),
            "manifest": manifest_out.strip().splitlines(),
            "latest.json (signatures omitted)": redact(manifest),
        },
    )


def redact(manifest: dict[str, Any]) -> dict[str, Any]:
    """證據用：簽章只留前 16 個字元。"""
    copy = json.loads(json.dumps(manifest))
    for entry in copy.get("platforms", {}).values():
        entry["signature"] = entry["signature"][:16] + "…"
    return copy


def write_served_manifests(work: Work, manifest: dict[str, Any]) -> None:
    """本機伺服器提供的 manifest：Windows 的 url 換成本機位址，其餘不變。

    - `good.json`：release.py 的輸出，只改 url（簽章原樣）。
    - `bad-signature.json`：同一個安裝檔，但簽章來自另一把金鑰（模擬被竄改或簽錯）。
    """
    work.serve_dir.mkdir(parents=True, exist_ok=True)
    name = work.installer(NEW_VERSION).name
    shutil.copy2(work.installer(NEW_VERSION), work.serve_dir / name)
    good = json.loads(json.dumps(manifest))
    for key in ("windows-x86_64", "windows-x86_64-nsis"):
        good["platforms"][key]["url"] = f"http://127.0.0.1:{work.port}/{name}"
    (work.serve_dir / "good.json").write_text(
        json.dumps(good, indent=2), encoding="utf-8"
    )

    wrong = work.root / "wrong-key" / name
    wrong.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(work.installer(NEW_VERSION), wrong)
    tauri_sign(work, "wrong", wrong)
    bad_sig = wrong.with_name(name + ".sig").read_text(encoding="utf-8").strip()
    bad = json.loads(json.dumps(good))
    for key in ("windows-x86_64", "windows-x86_64-nsis"):
        bad["platforms"][key]["signature"] = bad_sig
    (work.serve_dir / "bad-signature.json").write_text(
        json.dumps(bad, indent=2), encoding="utf-8"
    )


# --------------------------------------------------------------------------- 本機伺服器


class UpdateServer:
    """提供 `/latest.json`（可切換內容）與安裝檔；記錄每個請求。只聽 127.0.0.1。"""

    def __init__(self, directory: Path, port: int) -> None:
        self.directory = directory
        self.manifest: bytes | None = None
        self.requests: list[str] = []
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                server.requests.append(self.path)
                if self.path == "/latest.json":
                    body = server.manifest
                    if body is None:
                        self.send_error(404)
                        return
                    self._send(body, "application/json")
                    return
                name = self.path.lstrip("/")
                target = server.directory / name
                if "/" in name or "\\" in name or ".." in name or not target.is_file():
                    self.send_error(404)
                    return
                self._send(target.read_bytes(), "application/octet-stream")

            def _send(self, body: bytes, ctype: str) -> None:
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: Any) -> None:
                logger.debug("http: " + format, *args)

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def serve(self, manifest_name: str | None) -> None:
        """切換 `/latest.json` 的內容（None = 404）。"""
        self.manifest = (
            None
            if manifest_name is None
            else (self.directory / manifest_name).read_bytes()
        )
        self.requests.clear()

    def __enter__(self) -> Self:
        self.thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()


# --------------------------------------------------------------------------- WebDriver


class WebDriver:
    """W3C WebDriver 的最小客戶端（tauri-driver → msedgedriver → WebView2）。"""

    def __init__(self, port: int) -> None:
        self.base = f"http://127.0.0.1:{port}"
        self.session: str | None = None

    def _call(
        self, method: str, path: str, body: Any = None, timeout: float = HTTP_TIMEOUT
    ) -> Any:
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            self.base + path,
            data=data,
            method=method,
            headers={"Content-Type": "application/json; charset=utf-8"},
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:1000]
            raise AcceptanceError(
                f"WebDriver {method} {path}: HTTP {exc.code} {detail}"
            ) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise AcceptanceError(f"WebDriver {method} {path}: {exc}") from exc
        return payload.get("value")

    def start(self, application: Path) -> None:
        caps = {
            "capabilities": {
                "alwaysMatch": {"tauri:options": {"application": str(application)}}
            }
        }
        value = self._call("POST", "/session", caps, timeout=SESSION_TIMEOUT)
        self.session = value["sessionId"]

    def quit(self) -> None:
        if self.session is None:
            return
        try:
            self._call("DELETE", f"/session/{self.session}")
        except AcceptanceError as exc:
            # 程式已經自己結束（例如更新時）是預期內的。
            logger.info("deleting the WebDriver session: %s", exc)
        self.session = None

    def js(self, script: str, *args: Any) -> Any:
        return self._call(
            "POST",
            f"/session/{self.session}/execute/sync",
            {"script": script, "args": list(args)},
        )

    def click_xpath(self, xpath: str) -> None:
        el = self._call(
            "POST",
            f"/session/{self.session}/element",
            {"using": "xpath", "value": xpath},
        )
        self._call(
            "POST", f"/session/{self.session}/element/{el[ELEMENT_KEY]}/click", {}
        )

    def text(self, css: str) -> str:
        return str(
            self.js(
                "const e = document.querySelector(arguments[0]); return e ? e.textContent : null;",
                css,
            )
            or ""
        )

    def notices(self) -> list[str]:
        return list(
            self.js(
                "return Array.from(document.querySelectorAll('#notices .notice')).map(n => n.dataset.text);"
            )
            or []
        )


def wait_for(
    what: str,
    probe: Callable[[], Any],
    timeout: float = UI_TIMEOUT,
    interval: float = 0.5,
) -> Any:
    """反覆呼叫 `probe` 直到回傳真值；逾時丟 AcceptanceError（附最後一次的值）。"""
    deadline = time.monotonic() + timeout
    last: Any = None
    while time.monotonic() < deadline:
        try:
            last = probe()
        except AcceptanceError as exc:
            last = exc
        if last and not isinstance(last, Exception):
            return last
        time.sleep(interval)
    raise AcceptanceError(f"timed out waiting for {what} (last: {last!r})")


@contextlib.contextmanager
def drivers(work: Work) -> Iterator[WebDriver]:
    """啟動 tauri-driver（它再啟動 msedgedriver）；結束時關閉。"""
    log = (work.logs / "tauri-driver.log").open("a", encoding="utf-8")
    env = dict(os.environ, KIKITORI_CONFIG_DIR=str(work.config_dir))
    proc = subprocess.Popen(
        [
            str(work.tauri_driver),
            "--port",
            str(DRIVER_PORT),
            "--native-port",
            str(NATIVE_DRIVER_PORT),
            "--native-driver",
            str(work.edgedriver),
        ],
        stdout=log,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=CREATE_NO_WINDOW,
    )
    try:
        driver = WebDriver(DRIVER_PORT)
        wait_for("tauri-driver", lambda: _port_open(DRIVER_PORT), timeout=30)
        yield driver
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()
        # tauri-driver 被終止時不一定會帶走 msedgedriver。
        for p in processes_under(work.edgedriver.parent):
            kill_pid(p["ProcessId"], force=True)


def _port_open(port: int) -> bool:
    import socket

    with socket.socket() as s:
        s.settimeout(1)
        return s.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- 行程與登錄


def all_processes() -> list[dict[str, Any]]:
    data = powershell_json(
        "Get-CimInstance Win32_Process | Select-Object ProcessId,ParentProcessId,Name,"
        "ExecutablePath,CommandLine,CreationDate | ConvertTo-Json -Depth 2 -Compress"
    )
    return data if isinstance(data, list) else [data]


def processes_under(directory: Path) -> list[dict[str, Any]]:
    """執行檔位於 `directory`（含子目錄）的行程。"""
    prefix = str(directory.resolve()).lower() + os.sep
    return [
        p
        for p in all_processes()
        if str(p.get("ExecutablePath") or "").lower().startswith(prefix)
    ]


def kill_pid(pid: int, *, force: bool) -> None:
    """`force=False` 送 WM_CLOSE（主視窗關閉 → 程式正常結束、先關引擎）。"""
    args = ["taskkill", "/PID", str(pid)] + (["/F", "/T"] if force else [])
    subprocess.run(args, capture_output=True, timeout=30, check=False)


def app_tree(work: Work) -> dict[str, Any]:
    """已安裝的 kikitori.exe 與它的子行程（引擎）。"""
    procs = all_processes()
    exe = str(work.app_exe.resolve()).lower()
    apps = [p for p in procs if str(p.get("ExecutablePath") or "").lower() == exe]
    engines = [
        p
        for p in procs
        if p["ParentProcessId"] in {a["ProcessId"] for a in apps}
        and str(p.get("Name", "")).lower() == "kikitori-engine.exe"
    ]
    return {
        "app": [(a["ProcessId"], a["ExecutablePath"]) for a in apps],
        "engine": [(e["ProcessId"], e["ExecutablePath"]) for e in engines],
    }


def uninstall_entries() -> list[dict[str, str]]:
    """HKCU 的 Uninstall 底下名稱或 DisplayName 含 kikitori 的項目。"""
    found: list[dict[str, str]] = []
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, UNINSTALL_KEY) as root:
        i = 0
        while True:
            try:
                sub = winreg.EnumKey(root, i)
            except OSError:
                break
            i += 1
            with winreg.OpenKey(root, sub) as key:
                values: dict[str, str] = {"key": sub}
                for name in (
                    "DisplayName",
                    "DisplayVersion",
                    "InstallLocation",
                    "UninstallString",
                    "MainBinaryName",
                ):
                    with contextlib.suppress(OSError):
                        values[name] = str(winreg.QueryValueEx(key, name)[0])
            if (
                PRODUCT in sub.lower()
                or PRODUCT in values.get("DisplayName", "").lower()
            ):
                found.append(values)
    return found


def exe_version(path: Path) -> str:
    data = powershell_json(
        "(Get-Item -LiteralPath '" + str(path) + "').VersionInfo | "
        "Select-Object ProductVersion,FileVersion | ConvertTo-Json -Compress"
    )
    return f"ProductVersion={data['ProductVersion']} FileVersion={data['FileVersion']}"


def app_log() -> Path:
    return Path(os.environ["LOCALAPPDATA"]) / IDENTIFIER / "logs" / f"{PRODUCT}.log"


def read_log_since(offset: int) -> list[str]:
    path = app_log()
    if not path.is_file():
        return []
    with path.open("rb") as fh:
        fh.seek(offset)
        return fh.read().decode("utf-8", "replace").splitlines()


def log_size() -> int:
    path = app_log()
    return path.stat().st_size if path.is_file() else 0


# --------------------------------------------------------------------------- 情境


def baseline() -> dict[str, Any]:
    """測試前的狀態（解除安裝後比對；不動測試前就存在的東西）。"""
    roaming = Path(os.environ["APPDATA"])
    desktop = Path.home() / "Desktop"
    start_menu = roaming / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    return {
        "uninstall_entries": uninstall_entries(),
        "shortcuts": sorted(
            str(p) for d in (desktop, start_menu) for p in d.glob(f"{PRODUCT}*")
        ),
        "roaming_app_dir": (roaming / IDENTIFIER).exists(),
        "roaming_settings_dir": (roaming / PRODUCT).exists(),
    }


def open_settings(driver: WebDriver) -> None:
    driver.click_xpath("//button[@data-page='settings']")
    wait_for(
        "settings page",
        lambda: driver.js("return !document.getElementById('page-settings').hidden;"),
    )


def check_running_app(
    work: Work, driver: WebDriver, version: str, log_offset: int
) -> dict[str, Any]:
    """「關於」的版本、引擎連線、行程樹與 app 日誌。"""
    open_settings(driver)
    about = wait_for(
        f"about version {version}",
        lambda: (t := driver.text("#about-version")) == f"kikitori {version}" and t,
    )
    engine_text = wait_for(
        "engine connected",
        lambda: (t := driver.text("#about-engine")).startswith("引擎 ") and t,
        timeout=120,
    )
    tree = wait_for(
        "engine process", lambda: (t := app_tree(work))["engine"] and t, timeout=30
    )
    expected_engine = str(
        (work.install_dir / "kikitori-engine" / "kikitori-engine.exe").resolve()
    ).lower()
    if any(str(path).lower() != expected_engine for _, path in tree["engine"]):
        raise AcceptanceError(f"engine is not the installed one: {tree}")
    log_lines = [
        ln
        for ln in read_log_since(log_offset)
        if "starting engine" in ln or "engine bridge Ready" in ln
    ]
    if not any("starting engine" in ln and "kikitori-engine" in ln for ln in log_lines):
        raise AcceptanceError(
            f"app log has no 'starting engine' line for the bundled engine: {log_lines}"
        )
    return {
        "about": about,
        "engine": engine_text,
        "processes": tree,
        "app_log": log_lines[-4:],
        "exe": exe_version(work.app_exe),
    }


def close_app(work: Work) -> None:
    """正常關閉已安裝的程式（WM_CLOSE），等引擎一起結束；逾時才強制。"""
    for pid, _ in app_tree(work)["app"]:
        kill_pid(pid, force=False)
    try:
        wait_for("app exit", lambda: not processes_under(work.install_dir), timeout=30)
    except AcceptanceError:
        for p in processes_under(work.install_dir):
            kill_pid(p["ProcessId"], force=True)
        raise


def set_partner_language(driver: WebDriver, value: str) -> str:
    open_settings(driver)
    wait_for(
        "settings loaded",
        lambda: driver.js("return document.getElementById('f-partner').value;"),
    )
    driver.js(
        "const s = document.getElementById('f-partner'); s.value = arguments[0];"
        "s.dispatchEvent(new Event('input', {bubbles: true}));"
        "s.dispatchEvent(new Event('change', {bubbles: true}));",
        value,
    )
    driver.click_xpath("//button[@id='settings-save']")
    return str(
        wait_for(
            "settings saved",
            lambda: (t := driver.text("#settings-status")).startswith("已儲存") and t,
        )
    )


def step_scenario(work: Work) -> None:
    """步驟 3–7：安裝、啟動、負面測試、更新、重新啟動後檢查、解除安裝。"""
    for path in (
        work.installer(OLD_VERSION),
        work.serve_dir / "good.json",
        work.tauri_driver,
    ):
        if not path.exists():
            raise AcceptanceError(
                f"{path} is missing; run the tools/build/manifest steps first"
            )
    before = baseline()
    if before["uninstall_entries"]:
        raise AcceptanceError(
            f"kikitori is already installed for this user: {before['uninstall_entries']}; "
            "refusing to touch an existing installation"
        )
    work.config_dir.mkdir(parents=True, exist_ok=True)
    try:
        with UpdateServer(work.serve_dir, work.port) as server:
            _scenario(work, server)
    finally:
        cleanup(work, before)


def _scenario(work: Work, server: UpdateServer) -> None:
    # 3. 安裝 0.1.0（per-user，靜默，指定目錄）並啟動。
    run(
        [work.installer(OLD_VERSION), "/S", f"/D={work.install_dir}"],
        timeout=INSTALL_TIMEOUT,
    )
    entries = uninstall_entries()
    if not any(e.get("DisplayVersion") == OLD_VERSION for e in entries):
        raise AcceptanceError(f"no uninstall entry for {OLD_VERSION}: {entries}")
    server.serve(None)
    with drivers(work) as driver:
        offset = log_size()
        driver.start(work.app_exe)
        try:
            evidence = check_running_app(work, driver, OLD_VERSION, offset)
            evidence["uninstall_entry"] = entries
            evidence["settings_saved"] = set_partner_language(driver, "en")
            settings_file = work.config_dir / "settings.json"
            evidence["settings.json partner_language"] = json.loads(
                settings_file.read_text(encoding="utf-8")
            ).get("partner_language", "?")
            work.record("3 install 0.1.0 + launch", True, evidence)

            # 6a. 壞簽章：找得到 0.1.1，但下載後驗證失敗，不安裝。
            server.serve("bad-signature.json")
            exe_hash = sha256(work.app_exe)
            driver.click_xpath("//button[@id='check-update']")
            wait_for(
                "update offer",
                lambda: any(
                    f"kikitori {NEW_VERSION} 已經推出" in n for n in driver.notices()
                ),
            )
            driver.click_xpath(
                "//div[@id='notices']//button[normalize-space()='下載並安裝']"
            )
            failure = wait_for(
                "bad signature rejected",
                lambda: next(
                    (n for n in driver.notices() if n.startswith("更新失敗")), None
                ),
                timeout=UPDATE_TIMEOUT,
            )
            still = check_running_app(work, driver, OLD_VERSION, offset)
            if sha256(work.app_exe) != exe_hash:
                raise AcceptanceError("kikitori.exe changed after a rejected update")
            work.record(
                "6 negative: bad signature",
                True,
                {
                    "notice": failure,
                    "requests": list(server.requests),
                    "after": still["about"],
                    "uninstall_entry": [
                        e.get("DisplayVersion") for e in uninstall_entries()
                    ],
                },
            )
        finally:
            driver.quit()
            close_app(work)

        # 4. 更新：啟動時的檢查找到 0.1.1 → 下載並安裝 → 安裝程式（passive）重開程式。
        server.serve("good.json")
        offset = log_size()
        driver.start(work.app_exe)
        try:
            offer = wait_for(
                "startup update offer",
                lambda: next(
                    (
                        n
                        for n in driver.notices()
                        if f"kikitori {NEW_VERSION} 已經推出" in n
                    ),
                    None,
                ),
            )
            old_app = {pid for pid, _ in app_tree(work)["app"]}
            driver.click_xpath(
                "//div[@id='notices']//button[normalize-space()='下載並安裝']"
            )
            relaunched = wait_for(
                "updated app relaunched",
                lambda: (
                    (t := app_tree(work))["app"]
                    and not ({pid for pid, _ in t["app"]} & old_app)
                    and t
                ),
                timeout=UPDATE_TIMEOUT,
            )
            entries = wait_for(
                "uninstall entry 0.1.1",
                lambda: (
                    (e := uninstall_entries())
                    and all(x.get("DisplayVersion") == NEW_VERSION for x in e)
                    and e
                ),
            )
            engine_after = wait_for(
                "engine of the relaunched app",
                lambda: (t := app_tree(work))["engine"] and t,
                timeout=60,
            )
        finally:
            driver.quit()
        log_lines = [
            ln
            for ln in read_log_since(offset)
            if "updater" in ln or "starting engine" in ln or "engine bridge" in ln
        ]
        work.record(
            "4 update via startup check",
            True,
            {
                "offer": offer,
                "requests": list(server.requests),
                "relaunched": relaunched,
                "engine": engine_after,
                "uninstall_entry": entries,
                "exe": exe_version(work.app_exe),
                "app_log": log_lines[-8:],
            },
        )
        close_app(work)

        # 5. 0.1.1：版本、引擎、設定保留；6b. 同版本 →「目前已是最新版本。」
        offset = log_size()
        driver.start(work.app_exe)
        try:
            evidence = check_running_app(work, driver, NEW_VERSION, offset)
            partner = driver.js("return document.getElementById('f-partner').value;")
            stored = json.loads(
                (work.config_dir / "settings.json").read_text(encoding="utf-8")
            )
            if partner != "en" or stored.get("partner_language") != "en":
                raise AcceptanceError(
                    f"settings were not kept: UI {partner}, file {stored}"
                )
            evidence["partner_language (UI, file)"] = [partner, "en"]
            work.record("5 after update", True, evidence)

            server.serve("good.json")
            driver.click_xpath("//button[@id='check-update']")
            status = wait_for(
                "up to date",
                lambda: (
                    (t := driver.text("#update-status")) not in ("", "檢查中") and t
                ),
            )
            if status != "目前已是最新版本。":
                raise AcceptanceError(f"same-version check said {status!r}")
            work.record(
                "6 negative: same version",
                True,
                {"status": status, "requests": list(server.requests)},
            )
        finally:
            driver.quit()
            close_app(work)


def cleanup(work: Work, before: dict[str, Any]) -> None:
    """解除安裝（靜默）、刪除安裝目錄，確認登錄與捷徑沒有殘留。"""
    for p in processes_under(work.install_dir) if work.install_dir.exists() else []:
        kill_pid(p["ProcessId"], force=True)
    uninstaller = work.install_dir / "uninstall.exe"
    evidence: dict[str, Any] = {}
    if uninstaller.is_file():
        # `_?=` 讓 NSIS 解除安裝程式就地執行並等它結束（否則它會複製到暫存目錄後立即返回）。
        run([uninstaller, "/S", f"_?={work.install_dir}"], timeout=INSTALL_TIMEOUT)
        evidence["uninstaller"] = "exit 0"
    shutil.rmtree(work.install_dir, ignore_errors=True)
    # 解除安裝程式在 /S 時不刪 app 資料（UI 上是勾選項）。測試前不存在、而且只有
    # window-state 外掛的檔案時才移除，恢復測試前的狀態；其他內容不動，留在證據裡。
    roaming = Path(os.environ["APPDATA"]) / IDENTIFIER
    if not before["roaming_app_dir"] and roaming.is_dir():
        contents = sorted(p.name for p in roaming.iterdir())
        evidence["roaming_app_dir contents"] = contents
        if contents in ([], [".window-state.json"]):
            shutil.rmtree(roaming)
    evidence["leftover processes"] = [
        (p["ProcessId"], p["ExecutablePath"])
        for d in (work.install_dir, work.tools)
        for p in processes_under(d)
    ]
    after = baseline()
    evidence["install_dir_exists"] = work.install_dir.exists()
    evidence["uninstall_entries"] = after["uninstall_entries"]
    evidence["shortcuts"] = after["shortcuts"]
    evidence["roaming_app_dir (before, after)"] = [
        before["roaming_app_dir"],
        after["roaming_app_dir"],
    ]
    evidence["roaming_settings_dir (before, after)"] = [
        before["roaming_settings_dir"],
        after["roaming_settings_dir"],
    ]
    ok = (
        not after["uninstall_entries"]
        and not evidence["install_dir_exists"]
        and after["shortcuts"] == before["shortcuts"]
        and after["roaming_settings_dir"] == before["roaming_settings_dir"]
        and after["roaming_app_dir"] == before["roaming_app_dir"]
        and not evidence["leftover processes"]
    )
    work.record("7 cleanup", ok, evidence)


# --------------------------------------------------------------------------- 介面

STEPS: dict[str, tuple[str, Callable[[Work], None]]] = {
    "tools": ("下載／安裝驗收工具（minisign、msedgedriver、tauri-driver）", step_tools),
    "engine": ("建置引擎（build.py --smoke）", step_engine),
    "build": ("建置 0.1.0 與 0.1.1 的 NSIS 安裝檔（拋棄式金鑰）", step_build),
    "manifest": ("產生 latest.json 並以 minisign 驗證簽章", step_manifest),
    "scenario": ("安裝 → 更新 → 負面測試 → 解除安裝", step_scenario),
}


def run_steps(work: Work, names: Sequence[str]) -> int:
    work.root.mkdir(parents=True, exist_ok=True)
    for name in names:
        label, fn = STEPS[name]
        logger.info("== %s: %s", name, label)
        try:
            fn(work)
        except AcceptanceError as exc:
            work.record(name, False, {"error": str(exc)})
            return 1
        except Exception as exc:
            # 腳本本身的錯誤也記成 FAIL（附 traceback），不讓結果檔停在上一步。
            logger.exception("step %s crashed", name)
            work.record(name, False, {"error": f"{type(exc).__name__}: {exc}"})
            return 1
    failed = [r for r in work.results if r["result"] != "PASS"]
    print(json.dumps(work.results, ensure_ascii=False, indent=2))
    return 1 if failed else 0


def menu(work: Work) -> int:
    names = list(STEPS)
    status = 0
    while True:
        print(f"\n更新驗收（工作目錄 {work.root}）")
        for i, name in enumerate(names, 1):
            print(f"  {i}) {STEPS[name][0]}")
        print(f"  {len(names) + 1}) 全部依序執行")
        print("  0) 離開")
        try:
            choice = input("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return status
        if choice in ("", "0"):
            return status
        if choice == str(len(names) + 1):
            status = run_steps(work, names)
        elif choice.isdigit() and 1 <= int(choice) <= len(names):
            status = run_steps(work, [names[int(choice) - 1]])
        else:
            print(f"無效的選項：{choice!r}")


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None
    )
    parser.add_argument(
        "--work-dir",
        type=Path,
        default=Path(tempfile.gettempdir()) / "kikitori-update-acceptance",
    )
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument(
        "--all", action="store_true", help="run every step (non-interactive)"
    )
    parser.add_argument("--steps", help=f"comma-separated subset of {','.join(STEPS)}")
    parser.add_argument("-v", "--verbose", action="store_true")
    ns = parser.parse_args(argv)
    # 主控台可能是 cp932／cp950：結果含中文，一律以 UTF-8 輸出。
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8")
    logging.basicConfig(
        level=logging.DEBUG if ns.verbose else logging.INFO,
        format="%(asctime)s %(message)s",
    )
    if sys.platform != "win32":
        print("Windows only", file=sys.stderr)
        return 2
    if not 1024 <= ns.port <= 65535:
        parser.error("--port must be 1024..65535")
    work = Work(ns.work_dir.resolve(), ns.port)
    if ns.all:
        return run_steps(work, list(STEPS))
    if ns.steps:
        names = [s.strip() for s in ns.steps.split(",") if s.strip()]
        unknown = [s for s in names if s not in STEPS]
        if unknown:
            parser.error(f"unknown steps: {unknown}")
        return run_steps(work, names)
    return menu(work)


if __name__ == "__main__":
    sys.exit(main())
