"""把版本號從 `pyproject.toml` 同步到 Tauri 程式的設定檔，或檢查它們是否一致。

位於 repo 工具層（只用標準庫）。版本號的單一來源是 `pyproject.toml` 的
`[project].version`（`docs/specs/app-architecture.md`「版本號」）。同步的目標（檔案
不存在就略過）：

| 檔案 | 欄位 |
|---|---|
| `app/src-tauri/tauri.conf.json` | 最上層 `version` |
| `app/src-tauri/Cargo.toml` | `[package].version` |
| `app/package.json` | 最上層 `version` |
| `app/src-tauri/Cargo.lock` | 本程式自己的 `[[package]]` 版本（否則 `cargo --locked` 會失敗） |
| `app/package-lock.json` | 最上層與 `packages[""]` 的 `version`（與 package.json 一致） |

只改版本字串本身，其他內容與格式原封不動；改完重新解析檔案，確認只有版本變了。

用法：
  python scripts/sync_version.py                 互動選單
  python scripts/sync_version.py show|sync|check
  python scripts/sync_version.py bump patch|minor|major   改 pyproject 後同步，並執行 `uv lock`
`check` 有任何不一致時結束碼為 1（CI 使用）。
"""

import json
import re
import shutil
import subprocess
import sys
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

ROOT = Path(__file__).resolve().parents[1]

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
"""Cargo 與 Tauri 都要求 SemVer，所以 pyproject 的版本也必須是 SemVer 形式。"""

UV_LOCK_TIMEOUT_SECS = 300

Part = Literal["patch", "minor", "major"]


class VersionError(RuntimeError):
    """版本號格式錯誤、檔案無法解析，或改寫後的內容不如預期。"""


def validate_version(version: object, source: str) -> str:
    """確認是 SemVer 字串並回傳。"""
    if not isinstance(version, str) or not SEMVER.match(version):
        raise VersionError(f"{source}: {version!r} is not a SemVer version (X.Y.Z)")
    return version


# ---------------------------------------------------------------- 讀寫各種格式


def _toml_section_span(text: str, header: str) -> tuple[int, int]:
    """`[header]` 區段內容（不含標題行）在文字中的起訖位置。"""
    # `\r?`：Windows 上 git 的 core.autocrlf 會把 TOML checkout 成 CRLF，
    # 而 `(?m)$` 只在 `\n` 之前成立。
    match = re.search(rf"(?m)^\[{re.escape(header)}\][ \t]*(?:#.*)?\r?$", text)
    if match is None:
        raise VersionError(f"no [{header}] section")
    start = match.end()
    following = re.search(r"(?m)^\[", text[start:])
    end = start + following.start() if following else len(text)
    return start, end


def replace_toml_section_version(text: str, header: str, version: str) -> str:
    """把 `[header]` 區段中 `version = "..."` 的值換成 `version`。"""
    start, end = _toml_section_span(text, header)
    section = text[start:end]
    new_section, count = re.subn(
        r'(?m)^(version[ \t]*=[ \t]*")[^"\n]*(")',
        lambda m: f"{m.group(1)}{version}{m.group(2)}",
        section,
        count=1,
    )
    if count != 1:
        raise VersionError(f'no version = "..." line in [{header}]')
    return text[:start] + new_section + text[end:]


def replace_json_top_level_version(text: str, version: str) -> str:
    """把 JSON 最上層 `"version"` 的值換掉（以縮排最淺的那一行判斷最上層）。"""
    pattern = re.compile(
        r'(?m)^(?P<indent>[ \t]*)"version"(?P<sep>\s*:\s*)"(?P<value>[^"\\\n]*)"'
    )
    matches = list(pattern.finditer(text))
    if not matches:
        raise VersionError('no "version" key')
    top = min(matches, key=lambda m: len(m.group("indent")))
    replacement = f'{top.group("indent")}"version"{top.group("sep")}"{version}"'
    return text[: top.start()] + replacement + text[top.end() :]


def replace_package_lock_root_version(text: str, version: str) -> str:
    """package-lock.json：最上層 `version` 與 `packages[""].version`。"""
    text = replace_json_top_level_version(text, version)
    pattern = re.compile(
        r'("packages"\s*:\s*\{\s*""\s*:\s*\{[^{}]*?"version"\s*:\s*")[^"\\]*(")'
    )
    text, count = pattern.subn(
        lambda m: f"{m.group(1)}{version}{m.group(2)}", text, count=1
    )
    if count != 1:
        raise VersionError('no packages[""].version entry')
    return text


def replace_cargo_lock_version(text: str, package: str, version: str) -> str:
    """Cargo.lock：`[[package]] name = "<package>"` 那一筆的 `version`。"""
    pattern = re.compile(
        rf'(\[\[package\]\]\r?\nname = "{re.escape(package)}"\r?\nversion = ")[^"\n]*(")'
    )
    text, count = pattern.subn(
        lambda m: f"{m.group(1)}{version}{m.group(2)}", text, count=1
    )
    if count != 1:
        raise VersionError(f'no [[package]] entry named "{package}"')
    return text


# ---------------------------------------------------------------- 目標檔案


@dataclass(frozen=True)
class Target:
    """一個要同步版本的檔案。"""

    relative: str
    read: Callable[[str, Path], str]
    """回傳目前的版本（`(檔案內容, root)`）。"""
    write: Callable[[str, str, Path], str]
    """回傳換成新版本後的檔案內容（`(檔案內容, 新版本, root)`）。"""


def _json(text: str, source: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise VersionError(f"{source}: invalid JSON: {exc}") from exc


def _toml(text: str, source: str) -> dict[str, Any]:
    try:
        return tomllib.loads(text)
    except tomllib.TOMLDecodeError as exc:
        raise VersionError(f"{source}: invalid TOML: {exc}") from exc


def _read_json_version(text: str, _root: Path) -> str:
    data = _json(text, "json")
    if not isinstance(data, dict):
        raise VersionError("top level is not an object")
    return str(data.get("version"))


def _read_package_lock_versions(text: str, _root: Path) -> str:
    data = _json(text, "package-lock.json")
    if not isinstance(data, dict):
        raise VersionError("top level is not an object")
    top = data.get("version")
    nested = data.get("packages", {}).get("", {}).get("version")
    # 兩處不一致時回傳兩個值，讓 check 一定判定為不一致。
    return str(top) if top == nested else f"{top} / packages[''] {nested}"


def _cargo_package_name(root: Path) -> str:
    cargo = root / "app" / "src-tauri" / "Cargo.toml"
    data = _toml(cargo.read_text(encoding="utf-8"), str(cargo))
    name = data.get("package", {}).get("name")
    if not isinstance(name, str) or not name:
        raise VersionError(f"{cargo}: [package].name is missing")
    return name


def _read_cargo_toml_version(text: str, _root: Path) -> str:
    return str(_toml(text, "Cargo.toml").get("package", {}).get("version"))


def _read_cargo_lock_version(text: str, root: Path) -> str:
    name = _cargo_package_name(root)
    for package in _toml(text, "Cargo.lock").get("package", []):
        if package.get("name") == name:
            return str(package.get("version"))
    raise VersionError(f'Cargo.lock has no package named "{name}"')


TARGETS: tuple[Target, ...] = (
    Target(
        "app/src-tauri/tauri.conf.json",
        _read_json_version,
        lambda text, version, _root: replace_json_top_level_version(text, version),
    ),
    Target(
        "app/src-tauri/Cargo.toml",
        _read_cargo_toml_version,
        lambda text, version, _root: replace_toml_section_version(
            text, "package", version
        ),
    ),
    Target(
        "app/package.json",
        _read_json_version,
        lambda text, version, _root: replace_json_top_level_version(text, version),
    ),
    Target(
        "app/src-tauri/Cargo.lock",
        _read_cargo_lock_version,
        lambda text, version, root: replace_cargo_lock_version(
            text, _cargo_package_name(root), version
        ),
    ),
    Target(
        "app/package-lock.json",
        _read_package_lock_versions,
        lambda text, version, _root: replace_package_lock_root_version(text, version),
    ),
)


# ---------------------------------------------------------------- 操作


def project_version(root: Path = ROOT) -> str:
    """`pyproject.toml` 的 `[project].version`。"""
    path = root / "pyproject.toml"
    data = _toml(path.read_text(encoding="utf-8"), str(path))
    return validate_version(data.get("project", {}).get("version"), str(path))


@dataclass(frozen=True)
class FileVersion:
    """一個目標檔案目前的版本。"""

    path: Path
    version: str


def _read_text(path: Path) -> str:
    # newline="" 保留原本的換行字元（CRLF／LF），寫回時才不會改動整個檔案。
    with path.open(encoding="utf-8", newline="") as f:
        return f.read()


def _write_text(path: Path, text: str) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        f.write(text)


def current_versions(root: Path = ROOT) -> list[FileVersion]:
    """存在的目標檔案與它們目前的版本。"""
    found: list[FileVersion] = []
    for target in TARGETS:
        path = root / target.relative
        if not path.is_file():
            continue
        try:
            version = target.read(_read_text(path), root)
        except VersionError as exc:
            raise VersionError(f"{path}: {exc}") from exc
        found.append(FileVersion(path, version))
    return found


def mismatches(root: Path = ROOT) -> list[FileVersion]:
    """版本與 pyproject 不同的目標檔案。"""
    expected = project_version(root)
    return [f for f in current_versions(root) if f.version != expected]


def sync(root: Path = ROOT) -> list[Path]:
    """把 pyproject 的版本寫進每個存在的目標檔案；回傳有改動的檔案。

    每個檔案改完會重新解析，確認版本確實變成新值；失敗時不寫入並拋 `VersionError`。
    """
    version = project_version(root)
    changed: list[Path] = []
    for target in TARGETS:
        path = root / target.relative
        if not path.is_file():
            continue
        original = _read_text(path)
        try:
            if target.read(original, root) == version:
                continue
            updated = target.write(original, version, root)
            if target.read(updated, root) != version:
                raise VersionError(f"version is still not {version} after rewriting")
        except VersionError as exc:
            raise VersionError(f"{path}: {exc}") from exc
        _write_text(path, updated)
        changed.append(path)
    return changed


def bumped(version: str, part: Part) -> str:
    """X.Y.Z 依 `part` 進位；有 pre-release／build 標記的版本不自動進位。"""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
    if match is None:
        raise VersionError(f"cannot bump {version!r}: only plain X.Y.Z is supported")
    major, minor, patch = (int(g) for g in match.groups())
    if part == "major":
        return f"{major + 1}.0.0"
    if part == "minor":
        return f"{major}.{minor + 1}.0"
    if part == "patch":
        return f"{major}.{minor}.{patch + 1}"
    raise ValueError(f"part must be patch, minor or major, got {part!r}")


def bump(root: Path, part: Part) -> str:
    """改寫 pyproject 的 `[project].version` 並同步；回傳新版本。"""
    path = root / "pyproject.toml"
    new_version = bumped(project_version(root), part)
    original = _read_text(path)
    updated = replace_toml_section_version(original, "project", new_version)
    if _toml(updated, str(path)).get("project", {}).get("version") != new_version:
        raise VersionError(f"{path}: failed to rewrite [project].version")
    _write_text(path, updated)
    sync(root)
    return new_version


def update_uv_lock(
    root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """bump 後以 `uv lock` 更新 uv.lock 中本專案的版本（不手動改 uv.lock）。"""
    uv = shutil.which("uv")
    if uv is None:
        return "uv not found; run `uv lock` yourself to update uv.lock"
    try:
        completed = run(
            [uv, "lock"],
            cwd=root,
            capture_output=True,
            text=True,
            timeout=UV_LOCK_TIMEOUT_SECS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return f"`uv lock` timed out after {UV_LOCK_TIMEOUT_SECS}s; run it yourself"
    if completed.returncode != 0:
        return f"`uv lock` failed ({completed.returncode}): {completed.stderr.strip()}"
    return "uv.lock updated"


# ---------------------------------------------------------------- 介面


def _show(root: Path, out: Callable[[str], None]) -> int:
    out(f"pyproject.toml: {project_version(root)}")
    for item in current_versions(root):
        out(f"{item.path.relative_to(root).as_posix()}: {item.version}")
    return 0


def _check(root: Path, out: Callable[[str], None]) -> int:
    expected = project_version(root)
    wrong = mismatches(root)
    for item in wrong:
        out(
            f"MISMATCH {item.path.relative_to(root).as_posix()}: {item.version} != {expected}"
        )
    if wrong:
        out("run `python scripts/sync_version.py sync` to fix")
        return 1
    out(f"all versions match {expected}")
    return 0


def _sync(root: Path, out: Callable[[str], None]) -> int:
    changed = sync(root)
    version = project_version(root)
    for path in changed:
        out(f"updated {path.relative_to(root).as_posix()} -> {version}")
    if not changed:
        out(f"already in sync ({version})")
    return 0


def _bump(root: Path, part: Part, out: Callable[[str], None]) -> int:
    version = bump(root, part)
    out(f"pyproject.toml and app files -> {version}")
    out(update_uv_lock(root))
    return 0


def run_command(
    args: Sequence[str], root: Path = ROOT, out: Callable[[str], None] = print
) -> int:
    """執行一個子指令；回傳結束碼（用法錯誤為 2）。"""
    try:
        if list(args) == ["show"]:
            return _show(root, out)
        if list(args) == ["check"]:
            return _check(root, out)
        if list(args) == ["sync"]:
            return _sync(root, out)
        if (
            len(args) == 2
            and args[0] == "bump"
            and args[1] in ("patch", "minor", "major")
        ):
            return _bump(root, args[1], out)  # type: ignore[arg-type]
    except (VersionError, OSError) as exc:
        out(f"error: {exc}")
        return 1
    out("usage: sync_version.py [show | sync | check | bump patch|minor|major]")
    return 2


MENU = """
版本號（單一來源：pyproject.toml）
  1) 顯示各檔案的版本
  2) 同步到 app 設定檔
  3) 檢查是否一致
  4) 升 patch 版本並同步
  5) 升 minor 版本並同步
  6) 升 major 版本並同步
  7) 離開
"""

_MENU_COMMANDS: dict[str, list[str]] = {
    "1": ["show"],
    "2": ["sync"],
    "3": ["check"],
    "4": ["bump", "patch"],
    "5": ["bump", "minor"],
    "6": ["bump", "major"],
}


def menu(
    root: Path = ROOT,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """互動選單；回傳最後一個指令的結束碼。輸入結束（EOF）視同離開。"""
    status = 0
    while True:
        out(MENU)
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return status
        if choice == "7":
            return status
        command = _MENU_COMMANDS.get(choice)
        if command is None:
            out(f"無效的選項：{choice!r}")
            continue
        status = run_command(command, root, out)


def main(argv: Sequence[str] | None = None) -> int:
    """進入點：沒有參數時顯示選單。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return menu()
    return run_command(args)


if __name__ == "__main__":
    raise SystemExit(main())
