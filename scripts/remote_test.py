"""測試當下版本的遠端運算（互動選單）：這台開 app、把程式碼同步到運算主機、在運算主機開選單。

位於 repo 工具層（只用標準庫），給開發時實測 server／client 用，不隨 app 發布
（docs/specs/remote-compute.md）。

- 「開啟這台的 app」：`tauri dev`，介面與引擎都是 repo 裡目前的程式碼（含還沒 commit 的改動）。
- 「同步到運算主機」：`git ls-files` 列出的檔案（已追蹤＋未追蹤但沒被忽略的）打包成 tar.gz，
  經 ssh 解壓到運算主機的資料夾，再 `uv sync`。運算主機上的那份不是 git repo。
- 「在運算主機開選單」：`ssh -t` 執行 `uv run kikitori`（選 4「運算主機」）。

運算主機的 SSH 名稱（`~/.ssh/config` 的 Host 或 user@host）與資料夾只問一次，存在
`~/.kikitori-dev/remote_test.json`，不進 repo。

用法：
  uv run python -X utf8 scripts/remote_test.py
  或在 Windows 檔案總管雙擊 scripts/remote-test.cmd
"""

import io
import json
import logging
import os
import re
import shlex
import subprocess
import sys
import tarfile
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Protocol

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = Path.home() / ".kikitori-dev" / "remote_test.json"
DEFAULT_REMOTE_DIR = "~/kikitori"

# 本機的實驗結果（截圖、量測）不送到運算主機
EXCLUDED_PREFIXES = ("experiments/remote_compute/results/",)
LIST_TIMEOUT = 60.0
SYNC_TIMEOUT = 600.0  # 第一次 uv sync 要下載套件

# 開頭不能是 -（ssh 會當成選項，例如 -oProxyCommand=…）；不含空白與 shell 字元
_SSH_TARGET = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.@-]{0,252}$")
_REMOTE_DIR = re.compile(r"^(~/|/)?[A-Za-z0-9_.][A-Za-z0-9_./-]*$")

MENU = """
遠端運算測試（目前的程式碼）
  1) 開啟這台的 app
  2) 把目前的程式碼同步到運算主機
  3) 在運算主機開啟選單（選 4「運算主機」）
  4) 設定運算主機（SSH 名稱與資料夾）
  5) 離開
"""

logger = logging.getLogger("remote_test")

InputFn = Callable[[str], str]
OutFn = Callable[[str], None]


class Runner(Protocol):
    """執行外部指令（測試換成假的）。"""

    def __call__(
        self,
        argv: Sequence[str],
        *,
        cwd: Path | None = None,
        env: Mapping[str, str] | None = None,
        input: bytes | None = None,
        timeout: float | None = None,
        capture: bool = False,
    ) -> subprocess.CompletedProcess[bytes]: ...


def run(
    argv: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    input: bytes | None = None,
    timeout: float | None = None,
    capture: bool = False,
) -> subprocess.CompletedProcess[bytes]:
    """`subprocess.run`，不經 shell；`capture` 時收 stdout。"""
    return subprocess.run(
        list(argv),
        cwd=cwd,
        env=None if env is None else dict(env),
        input=input,
        timeout=timeout,
        stdout=subprocess.PIPE if capture else None,
        check=False,
    )


@dataclass(frozen=True)
class Config:
    """運算主機：`ssh` 是 SSH 名稱，`dir` 是放程式碼的資料夾；空字串表示還沒設定。"""

    ssh: str
    dir: str

    @property
    def ready(self) -> bool:
        return bool(self.ssh and self.dir)


def is_ssh_target(value: str) -> bool:
    """`~/.ssh/config` 的 Host 或 user@host；不能被 ssh 當成選項。"""
    return bool(_SSH_TARGET.match(value))


def is_remote_dir(value: str) -> bool:
    """運算主機上的資料夾：`~/…`、絕對路徑或相對家目錄；不含空白、shell 字元與 `..`。"""
    if not _REMOTE_DIR.match(value) or value.startswith("-"):
        return False
    return ".." not in value.split("/")


def _quote_dir(path: str) -> str:
    # ~ 放在引號裡不會展開，所以換成 "$HOME"
    if path.startswith("~/"):
        return '"$HOME"/' + shlex.quote(path[2:])
    return shlex.quote(path)


def remote_command(path: str, command: str) -> str:
    """在運算主機的 `path` 執行 `command`（非互動 ssh 的 PATH 沒有 ~/.local/bin，uv 在那裡）。"""
    return f'cd {_quote_dir(path)} && export PATH="$HOME/.local/bin:$PATH" && {command}'


def load_config(path: Path) -> Config:
    """讀設定；沒有檔案、讀不懂或值不合格時視為還沒設定。"""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return Config("", "")
    except (OSError, ValueError) as exc:
        logger.warning("ignoring %s: %s", path, exc)
        return Config("", "")
    if not isinstance(data, dict):
        return Config("", "")
    ssh, folder = data.get("ssh"), data.get("dir")
    if not (isinstance(ssh, str) and is_ssh_target(ssh)):
        return Config("", "")
    if not (isinstance(folder, str) and is_remote_dir(folder)):
        return Config("", "")
    return Config(ssh, folder)


def save_config(path: Path, config: Config) -> None:
    """存設定（只有 SSH 名稱與資料夾，沒有密碼）。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(config), ensure_ascii=False), encoding="utf-8")


def build_archive(root: Path, files: Sequence[str]) -> bytes:
    """把 `files`（相對 `root`、`/` 分隔）打包成 tar.gz；已刪除的與本機實驗結果略過。"""
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as tar:
        for name in files:
            if name.startswith(EXCLUDED_PREFIXES):
                continue
            path = root / name
            if not path.is_file():
                continue  # git 還列著、但工作目錄裡已刪除
            tar.add(path, arcname=name, recursive=False)
    return buffer.getvalue()


def ask_config(input_fn: InputFn, out: OutFn, current: Config) -> Config | None:
    """問 SSH 名稱與資料夾（空白沿用目前的）；不合格時說明並回傳 None。"""
    ssh = input_fn(f"運算主機的 SSH 名稱[{current.ssh}]：").strip() or current.ssh
    if not is_ssh_target(ssh):
        out(
            "SSH 名稱只能用 ~/.ssh/config 的 Host 或 user@host（英數字、. _ - @，不能以 - 開頭）。"
        )
        return None
    default_dir = current.dir or DEFAULT_REMOTE_DIR
    folder = (
        input_fn(f"運算主機上放程式碼的資料夾[{default_dir}]：").strip() or default_dir
    )
    if not is_remote_dir(folder):
        out("資料夾只能用 ~/… 或 /… 的路徑（英數字、. _ - /，不含空白與 ..）。")
        return None
    return Config(ssh, folder)


def _configured(input_fn: InputFn, out: OutFn, config_path: Path) -> Config | None:
    config = load_config(config_path)
    if config.ready:
        return config
    out("還沒設定運算主機。")
    config = ask_config(input_fn, out, config) or Config("", "")
    if not config.ready:
        return None
    save_config(config_path, config)
    return config


def open_app(runner: Runner, root: Path, out: OutFn) -> None:
    """`tauri dev`：介面與引擎都用 repo 裡目前的程式碼。關掉 app 視窗後回到選單。"""
    tauri = root / "app" / "node_modules" / "@tauri-apps" / "cli" / "tauri.js"
    if not tauri.is_file():
        out("找不到 Tauri CLI，請先在 app/ 執行 npm install。")
        return
    out("啟動中（第一次要編譯，約一兩分鐘）；關掉 app 視窗就回到這個選單。")
    env = {**os.environ, "KIKITORI_ENGINE_CWD": str(root)}
    try:
        runner(["node", str(tauri), "dev"], cwd=root / "app", env=env)
    except OSError as exc:
        logger.error("cannot start tauri dev: %s", exc)
        out(f"無法啟動（有安裝 Node.js 嗎？）：{exc}")


def sync(runner: Runner, root: Path, config: Config, out: OutFn) -> bool:
    """把目前的程式碼（含還沒 commit 的）送到運算主機並 `uv sync`；回傳是否成功。"""
    try:
        listed = runner(
            ["git", "ls-files", "-co", "--exclude-standard", "-z"],
            cwd=root,
            capture=True,
            timeout=LIST_TIMEOUT,
        )
        if listed.returncode != 0:
            out("同步失敗：git ls-files 執行失敗。")
            return False
        files = [f for f in listed.stdout.decode("utf-8").split("\0") if f]
        data = build_archive(root, files)
        out(f"送出 {len(files)} 個檔案（{len(data) / 1e6:.1f} MB）到 {config.ssh}…")
        target = _quote_dir(config.dir)
        steps = (
            (
                "解壓縮",
                f"mkdir -p {target} && " + remote_command(config.dir, "tar -xzf -"),
                data,
            ),
            ("uv sync", remote_command(config.dir, "uv sync"), None),
        )
        for label, command, payload in steps:
            done = runner(
                ["ssh", "--", config.ssh, command], input=payload, timeout=SYNC_TIMEOUT
            )
            if done.returncode != 0:
                out(f"同步失敗：{label}（ssh 結束碼 {done.returncode}）。")
                return False
    except subprocess.TimeoutExpired as exc:
        logger.error("sync timed out: %s", exc)
        out(f"同步失敗：逾時（{exc.timeout:.0f} 秒）。")
        return False
    except OSError as exc:
        logger.error("sync failed: %s", exc)
        out(f"同步失敗：{exc}")
        return False
    out(f"同步完成：{config.ssh}:{config.dir}")
    return True


def open_host_menu(runner: Runner, config: Config, out: OutFn) -> None:
    """在運算主機開 `kikitori` 選單（互動，經 ssh -t）。"""
    out("在運算主機的選單選 4「運算主機」→ 1「開始提供運算」；離開後回到這個選單。")
    try:
        runner(
            [
                "ssh",
                "-t",
                "--",
                config.ssh,
                remote_command(config.dir, "uv run kikitori"),
            ]
        )
    except OSError as exc:
        logger.error("cannot start ssh: %s", exc)
        out(f"無法執行 ssh：{exc}")


def menu(
    input_fn: InputFn,
    out: OutFn,
    runner: Runner,
    config_path: Path,
    root: Path,
) -> int:
    """互動選單；輸入結束（EOF）視同離開。"""
    try:
        while True:
            out(MENU)
            choice = input_fn("請輸入編號：").strip()
            if choice == "1":
                open_app(runner, root, out)
            elif choice in ("2", "3"):
                config = _configured(input_fn, out, config_path)
                if config is None:
                    continue
                if choice == "2":
                    sync(runner, root, config, out)
                else:
                    open_host_menu(runner, config, out)
            elif choice == "4":
                config = ask_config(input_fn, out, load_config(config_path))
                if config is not None:
                    save_config(config_path, config)
                    out(f"已設定：{config.ssh}:{config.dir}")
            elif choice == "5":
                return 0
            else:
                out(f"無效的選項：{choice!r}")
    except (EOFError, KeyboardInterrupt):
        out("")
        return 0


def main(argv: Sequence[str] | None = None) -> int:
    """進入點。"""
    del argv  # 只有互動選單
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    return menu(input, print, run, DEFAULT_CONFIG, ROOT)


if __name__ == "__main__":
    sys.exit(main())
