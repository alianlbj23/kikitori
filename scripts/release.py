"""發布流程的工具：決定這次 push 要不要發布、整理各平台的安裝檔、驗證更新器簽章、產生 `latest.json`。

位於 repo 工具層（只用標準庫，Python 3.11+ 的 `tomllib`），給
`.github/workflows/release.yml` 使用；流程與設計見 `docs/specs/release-update.md`。

為什麼自己產生 `latest.json`、不用 tauri-action 的：三個平台都建置並通過冒煙測試之後，才由
唯一有 `contents: write` 的 publish job 一次建立 Release。tauri-action 要在每個建置 job 直接
上傳到 Release，建置 job 就得有寫入權限，且某個平台失敗時會留下不完整的 Release。

子指令：
  gate      依版本號、分支與既有 tag 決定 mode（publish／dry-run／skip），輸出 `key=value`
  collect   把本平台的安裝檔與更新器檔案（含 .sig）以固定名稱複製到輸出目錄
  verify    以 minisign 驗證目錄內每個 .sig（公鑰取自 tauri.conf.json 或指定檔案）
  manifest  從各平台的輸出目錄挑出發布檔案、驗證簽章格式，寫出 `latest.json`
不帶參數是互動選單。
"""

import argparse
import base64
import binascii
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal
from urllib.parse import quote

ROOT = Path(__file__).resolve().parents[1]
TAURI_CONF = Path("app") / "src-tauri" / "tauri.conf.json"

SEMVER = re.compile(
    r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
REPO_NAME = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
MINISIGN_TIMEOUT_SECS = 60
GIT_TIMEOUT_SECS = 30

Mode = Literal["publish", "dry-run", "skip"]
Platform = Literal["windows", "linux", "macos"]
PLATFORMS: tuple[Platform, ...] = ("windows", "linux", "macos")


class ReleaseError(RuntimeError):
    """發布前的檢查失敗：版本號、缺少檔案、簽章格式或驗證錯誤。"""


# ---------------------------------------------------------------- 版本號


def parse_semver(version: str) -> tuple[tuple[int, int, int], tuple[str, ...]]:
    """拆成 (major, minor, patch) 與預發布識別字；不是 SemVer 就丟 ReleaseError。"""
    match = SEMVER.match(version)
    if match is None:
        raise ReleaseError(f"{version!r} is not a SemVer version (X.Y.Z)")
    core = (int(match[1]), int(match[2]), int(match[3]))
    pre = tuple(match[4].split(".")) if match[4] else ()
    return core, pre


def compare_versions(a: str, b: str) -> int:
    """依 SemVer 2.0 的優先順序比較（忽略 build metadata）；回傳 -1、0、1。"""
    core_a, pre_a = parse_semver(a)
    core_b, pre_b = parse_semver(b)
    if core_a != core_b:
        return -1 if core_a < core_b else 1
    if pre_a == pre_b:
        return 0
    # 沒有預發布識別字的版本比有的新（1.0.0 > 1.0.0-rc.1）。
    if not pre_a:
        return 1
    if not pre_b:
        return -1
    for x, y in zip(pre_a, pre_b, strict=False):
        if x == y:
            continue
        x_num, y_num = x.isdigit(), y.isdigit()
        if x_num and y_num:
            return -1 if int(x) < int(y) else 1
        if x_num != y_num:
            # 數字識別字的優先順序低於英數識別字。
            return -1 if x_num else 1
        return -1 if x < y else 1
    return -1 if len(pre_a) < len(pre_b) else 1


def project_version(root: Path = ROOT) -> str:
    """`pyproject.toml` 的 `[project].version`（版本號的單一來源）。"""
    try:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ReleaseError(f"cannot read pyproject.toml: {exc}") from exc
    version = data.get("project", {}).get("version")
    if not isinstance(version, str):
        raise ReleaseError("pyproject.toml has no [project].version string")
    parse_semver(version)
    return version


def tauri_config(root: Path = ROOT) -> dict[str, Any]:
    """讀 `app/src-tauri/tauri.conf.json`。"""
    try:
        data = json.loads((root / TAURI_CONF).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ReleaseError(f"cannot read {TAURI_CONF.as_posix()}: {exc}") from exc
    if not isinstance(data, dict):
        raise ReleaseError(f"{TAURI_CONF.as_posix()} is not a JSON object")
    return data


def product_name(root: Path = ROOT) -> str:
    """安裝檔名稱的前綴（Tauri 以 productName 命名 bundle）。"""
    name = tauri_config(root).get("productName")
    if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", name):
        raise ReleaseError(f"unexpected productName in tauri.conf.json: {name!r}")
    return name


def config_pubkey(root: Path = ROOT) -> str:
    """tauri.conf.json 裡更新器的公鑰（base64，app 用它驗證下載的更新）。"""
    plugins = tauri_config(root).get("plugins", {})
    updater = plugins.get("updater", {}) if isinstance(plugins, dict) else {}
    pubkey = updater.get("pubkey") if isinstance(updater, dict) else None
    if not isinstance(pubkey, str) or not pubkey:
        raise ReleaseError("tauri.conf.json has no plugins.updater.pubkey")
    return pubkey


# ---------------------------------------------------------------- gate


@dataclass(frozen=True)
class GateDecision:
    """這次 push 的處理方式。"""

    mode: Mode
    version: str
    tag: str
    prerelease: bool
    reason: str

    def outputs(self) -> dict[str, str]:
        """給 `$GITHUB_OUTPUT` 的值。"""
        return {
            "mode": self.mode,
            "version": self.version,
            "tag": self.tag,
            "prerelease": "true" if self.prerelease else "false",
        }


def decide(
    version: str, ref: str, default_branch: str, existing_tags: Iterable[str]
) -> GateDecision:
    """決定 mode。

    - 預設分支：tag `v<version>` 已存在 → `skip`（版本沒變，只跑測試）；否則 `publish`，
      但新版本必須比既有的每個 `v*` release tag 都新，以免 `releases/latest` 指向舊版。
    - 其他分支（workflow 只在 commit 訊息含 `[release-dry-run]` 時呼叫）→ `dry-run`。
    """
    _, pre = parse_semver(version)
    if not default_branch or not re.fullmatch(r"[A-Za-z0-9._/-]+", default_branch):
        raise ReleaseError(f"invalid default branch name: {default_branch!r}")
    tag = f"v{version}"
    tags = {t.strip() for t in existing_tags if t.strip()}
    prerelease = bool(pre)
    if ref != f"refs/heads/{default_branch}":
        state = "exists" if tag in tags else "does not exist yet"
        return GateDecision(
            "dry-run", version, tag, prerelease, f"{ref}: dry run (tag {tag} {state})"
        )
    if tag in tags:
        return GateDecision(
            "skip",
            version,
            tag,
            prerelease,
            f"tag {tag} already exists; nothing to publish",
        )
    released = [t[1:] for t in tags if t.startswith("v") and SEMVER.match(t[1:])]
    newer = [v for v in released if compare_versions(v, version) >= 0]
    if newer:
        newest = max(newer, key=_SemverKey)
        raise ReleaseError(
            f"version {version} is not newer than existing release tag v{newest}; "
            "bump the version with scripts/sync_version.py"
        )
    return GateDecision("publish", version, tag, prerelease, f"publishing {tag}")


class _SemverKey:
    """讓 `max()` 依 SemVer 排序。"""

    def __init__(self, version: str) -> None:
        self.version = version

    def __lt__(self, other: "_SemverKey") -> bool:
        return compare_versions(self.version, other.version) < 0


# ---------------------------------------------------------------- 安裝檔


@dataclass(frozen=True)
class AssetSpec:
    """一個要發布的檔案。

    `source` 是相對於 Tauri bundle 目錄（`target/release/bundle`）的位置，`name` 是發布時的
    名稱；`updater_keys` 非空代表它也是更新器的下載檔，必須有 `.sig`，並寫進 `latest.json`
    的這些平台鍵。tauri-plugin-updater 會先找 `{os}-{arch}-{installer}`，再找 `{os}-{arch}`，
    所以 deb 安裝的使用者拿到 deb、AppImage 的拿到 AppImage。
    """

    platform: Platform
    source: str
    name: str
    updater_keys: tuple[str, ...]

    def source_path(self, product: str, version: str) -> str:
        return self.source.format(product=product, version=version)

    def asset_name(self, product: str, version: str) -> str:
        return self.name.format(product=product, version=version)


ASSETS: tuple[AssetSpec, ...] = (
    AssetSpec(
        "windows",
        "nsis/{product}_{version}_x64-setup.exe",
        "{product}_{version}_x64-setup.exe",
        ("windows-x86_64-nsis", "windows-x86_64"),
    ),
    AssetSpec(
        "linux",
        "appimage/{product}_{version}_amd64.AppImage",
        "{product}_{version}_amd64.AppImage",
        ("linux-x86_64-appimage", "linux-x86_64"),
    ),
    AssetSpec(
        "linux",
        "deb/{product}_{version}_amd64.deb",
        "{product}_{version}_amd64.deb",
        ("linux-x86_64-deb",),
    ),
    AssetSpec(
        "macos",
        "dmg/{product}_{version}_aarch64.dmg",
        "{product}_{version}_aarch64.dmg",
        (),
    ),
    # Tauri 的 macOS 更新檔名沒有版本與架構；改名讓 Release 頁面上分得清楚（簽章只涵蓋內容）。
    AssetSpec(
        "macos",
        "macos/{product}.app.tar.gz",
        "{product}_{version}_aarch64.app.tar.gz",
        ("darwin-aarch64-app", "darwin-aarch64"),
    ),
)

REQUIRED_UPDATER_KEYS = ("windows-x86_64", "linux-x86_64", "darwin-aarch64")


def assets_for(platform: str) -> list[AssetSpec]:
    """某個平台要發布的檔案。"""
    if platform not in PLATFORMS:
        raise ReleaseError(
            f"unknown platform {platform!r} (expected one of {PLATFORMS})"
        )
    return [a for a in ASSETS if a.platform == platform]


def collect(
    platform: str, bundle_dir: Path, out_dir: Path, product: str, version: str
) -> list[tuple[str, int]]:
    """把本平台的檔案（與 .sig）以發布名稱複製到 `out_dir`；回傳 (名稱, 位元組數)。"""
    copied: list[tuple[str, int]] = []
    missing: list[str] = []
    plan: list[tuple[Path, str]] = []
    for spec in assets_for(platform):
        src = bundle_dir / spec.source_path(product, version)
        name = spec.asset_name(product, version)
        if not src.is_file():
            missing.append(str(src))
            continue
        plan.append((src, name))
        if spec.updater_keys:
            sig = src.with_name(src.name + ".sig")
            if not sig.is_file():
                missing.append(str(sig))
                continue
            plan.append((sig, name + ".sig"))
    if missing:
        raise ReleaseError("missing bundle files:\n  " + "\n  ".join(missing))
    out_dir.mkdir(parents=True, exist_ok=True)
    for src, name in plan:
        dest = out_dir / name
        shutil.copy2(src, dest)
        copied.append((name, dest.stat().st_size))
    return copied


# ---------------------------------------------------------------- 簽章


def decode_minisign_text(value: str, kind: str, source: str) -> str:
    """Tauri 的公鑰與 .sig 都是「minisign 檔案內容」再做 base64；解回文字並檢查形狀。"""
    try:
        text = base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError) as exc:
        raise ReleaseError(f"{source}: {kind} is not base64-encoded text") from exc
    lines = text.splitlines()
    if len(lines) < 2 or not lines[0].startswith("untrusted comment:"):
        raise ReleaseError(f"{source}: {kind} is not in minisign format")
    return text if text.endswith("\n") else text + "\n"


def verify_signatures(
    directory: Path,
    pubkey_b64: str,
    run: Callable[..., subprocess.CompletedProcess[str]] | None = None,
    minisign: str = "minisign",
) -> list[str]:
    """以 minisign 驗證 `directory` 內每個 `<檔名>.sig`；回傳驗證過的檔名。

    發布前用 app 內建的公鑰驗證，確保 GitHub Secrets 裡的私鑰與公鑰相符——不符的話，
    已安裝的 app 會拒絕這次更新，而且不會有人察覺。
    """
    run = run or subprocess.run
    sigs = sorted(directory.rglob("*.sig"))
    if not sigs:
        raise ReleaseError(f"no .sig files under {directory}")
    pub_text = decode_minisign_text(pubkey_b64, "public key", "pubkey")
    verified: list[str] = []
    with tempfile.TemporaryDirectory() as tmp:
        pub_file = Path(tmp) / "updater.pub"
        pub_file.write_text(pub_text, encoding="utf-8")
        for sig in sigs:
            target = sig.with_name(sig.name[: -len(".sig")])
            if not target.is_file():
                raise ReleaseError(f"{sig}: signed file {target.name} is missing")
            sig_file = Path(tmp) / "current.minisig"
            sig_file.write_text(
                decode_minisign_text(
                    sig.read_text(encoding="utf-8"), "signature", str(sig)
                ),
                encoding="utf-8",
            )
            cmd = [
                minisign,
                "-V",
                "-q",
                "-p",
                str(pub_file),
                "-x",
                str(sig_file),
                "-m",
                str(target),
            ]
            try:
                completed = run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=MINISIGN_TIMEOUT_SECS,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise ReleaseError(
                    f"{minisign} not found (apt-get install minisign)"
                ) from exc
            except subprocess.TimeoutExpired as exc:
                raise ReleaseError(f"minisign timed out on {target.name}") from exc
            if completed.returncode != 0:
                detail = (completed.stderr or completed.stdout).strip()
                raise ReleaseError(
                    f"signature of {target.name} does not match the updater public key: {detail}"
                )
            verified.append(target.name)
    return verified


# ---------------------------------------------------------------- latest.json


def _find_unique(search_dir: Path, name: str) -> Path:
    found = [p for p in search_dir.rglob(name) if p.is_file()]
    if not found:
        raise ReleaseError(f"{name} not found under {search_dir}")
    if len(found) > 1:
        raise ReleaseError(f"{name} found more than once: {', '.join(map(str, found))}")
    return found[0]


def build_manifest(
    search_dir: Path,
    stage_dir: Path,
    *,
    product: str,
    version: str,
    tag: str,
    repo: str,
    notes: str,
    pub_date: datetime | None = None,
) -> dict[str, Any]:
    """從 `search_dir`（各平台 collect 的輸出）挑出全部發布檔案複製到 `stage_dir`，
    並寫出 `stage_dir/latest.json`（Tauri v2 updater 的靜態 JSON 格式）。

    `url` 指向 `releases/download/<tag>/`，而不是 `releases/latest/download/`：這樣舊的
    latest.json 永遠指向它自己那一版的檔案。
    """
    parse_semver(version)
    if tag != f"v{version}":
        raise ReleaseError(f"tag {tag!r} does not match version {version!r}")
    if not REPO_NAME.match(repo):
        raise ReleaseError(f"invalid repository name: {repo!r}")
    stage_dir.mkdir(parents=True, exist_ok=True)
    platforms: dict[str, dict[str, str]] = {}
    for spec in ASSETS:
        name = spec.asset_name(product, version)
        src = _find_unique(search_dir, name)
        shutil.copy2(src, stage_dir / name)
        if not spec.updater_keys:
            continue
        sig_src = _find_unique(search_dir, name + ".sig")
        signature = sig_src.read_text(encoding="utf-8").strip()
        decode_minisign_text(signature, "signature", str(sig_src))
        shutil.copy2(sig_src, stage_dir / (name + ".sig"))
        url = f"https://github.com/{repo}/releases/download/{quote(tag)}/{quote(name)}"
        for key in spec.updater_keys:
            platforms[key] = {"signature": signature, "url": url}
    missing = [k for k in REQUIRED_UPDATER_KEYS if k not in platforms]
    if missing:
        raise ReleaseError(f"latest.json would lack platforms: {', '.join(missing)}")
    when = (pub_date or datetime.now(UTC)).astimezone(UTC).replace(microsecond=0)
    manifest: dict[str, Any] = {
        "version": version,
        "notes": notes,
        "pub_date": when.isoformat().replace("+00:00", "Z"),
        "platforms": dict(sorted(platforms.items())),
    }
    (stage_dir / "latest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return manifest


# ---------------------------------------------------------------- 介面


def _read_tags(path: Path) -> list[str]:
    try:
        return path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ReleaseError(f"cannot read tags file {path}: {exc}") from exc


def _summary(rows: Sequence[tuple[str, int]], title: str) -> str:
    lines = [f"### {title}", "", "| file | size |", "|---|---:|"]
    lines += [f"| `{name}` | {size / 1_048_576:.1f} MiB |" for name, size in rows]
    return "\n".join(lines) + "\n"


def _append_step_summary(text: str) -> None:
    """GitHub Actions 的 job summary（沒有該環境變數就略過）。"""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(text)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="release.py", description=__doc__.splitlines()[0]
    )
    parser.add_argument("--root", type=Path, default=ROOT, help=argparse.SUPPRESS)
    sub = parser.add_subparsers(dest="command", required=True)

    gate = sub.add_parser("gate", help="decide publish / dry-run / skip")
    gate.add_argument("--ref", required=True, help="GITHUB_REF, e.g. refs/heads/main")
    gate.add_argument("--default-branch", required=True)
    gate.add_argument(
        "--tags-file", type=Path, required=True, help="one existing tag per line"
    )

    col = sub.add_parser(
        "collect", help="copy this platform's bundles with release names"
    )
    col.add_argument("--platform", choices=PLATFORMS, required=True)
    col.add_argument("--bundle-dir", type=Path, required=True)
    col.add_argument("--out", type=Path, required=True)

    ver = sub.add_parser("verify", help="verify every .sig with minisign")
    ver.add_argument("--dir", type=Path, required=True)
    key = ver.add_mutually_exclusive_group(required=True)
    key.add_argument(
        "--pubkey-file", type=Path, help="file with a base64 Tauri public key"
    )
    key.add_argument(
        "--pubkey-from-config", action="store_true", help="use tauri.conf.json's pubkey"
    )

    man = sub.add_parser("manifest", help="stage release files and write latest.json")
    man.add_argument(
        "--dir", type=Path, required=True, help="where the collected bundles are"
    )
    man.add_argument("--stage", type=Path, required=True)
    man.add_argument("--repo", required=True, help="owner/name")
    man.add_argument("--tag", required=True)
    man.add_argument("--notes", default="")
    return parser


def _stderr(message: str) -> None:
    print(message, file=sys.stderr)


def run_command(
    args: Sequence[str],
    out: Callable[[str], None] = print,
    err: Callable[[str], None] = _stderr,
) -> int:
    """執行一個子指令；回傳結束碼（檢查失敗 1、用法錯誤 2）。

    結果寫到 `out`（stdout，gate 的輸出會被導向 `$GITHUB_OUTPUT`），說明與錯誤寫到 `err`。
    """
    parser = build_parser()
    try:
        ns = parser.parse_args(list(args))
    except SystemExit as exc:
        return int(exc.code or 0)
    root: Path = ns.root
    try:
        if ns.command == "gate":
            decision = decide(
                project_version(root),
                ns.ref,
                ns.default_branch,
                _read_tags(ns.tags_file),
            )
            err(decision.reason)
            for k, v in decision.outputs().items():
                out(f"{k}={v}")
        elif ns.command == "collect":
            version = project_version(root)
            rows = collect(
                ns.platform, ns.bundle_dir, ns.out, product_name(root), version
            )
            for name, size in rows:
                out(f"{name}\t{size}")
            _append_step_summary(_summary(rows, f"{ns.platform} bundles ({version})"))
        elif ns.command == "verify":
            pubkey = (
                config_pubkey(root)
                if ns.pubkey_from_config
                else ns.pubkey_file.read_text(encoding="utf-8")
            )
            for name in verify_signatures(ns.dir, pubkey):
                out(f"signature ok: {name}")
        elif ns.command == "manifest":
            version = project_version(root)
            manifest = build_manifest(
                ns.dir,
                ns.stage,
                product=product_name(root),
                version=version,
                tag=ns.tag,
                repo=ns.repo,
                notes=ns.notes,
            )
            out(f"latest.json: version {manifest['version']}, platforms:")
            for key, entry in manifest["platforms"].items():
                out(f"  {key}: {entry['url']}")
    except (ReleaseError, OSError) as exc:
        err(f"error: {exc}")
        return 1
    return 0


def _git(args: Sequence[str], root: Path) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=root,
        capture_output=True,
        text=True,
        timeout=GIT_TIMEOUT_SECS,
        check=False,
    )
    if completed.returncode != 0:
        raise ReleaseError(f"git {' '.join(args)} failed: {completed.stderr.strip()}")
    return completed.stdout


def preview(root: Path = ROOT, out: Callable[[str], None] = print) -> int:
    """以本機 git 的分支與 tag 模擬 CI 的判斷（只讀，不連網；tag 以本機已 fetch 的為準）。"""
    try:
        version = project_version(root)
        ref = "refs/heads/" + _git(["rev-parse", "--abbrev-ref", "HEAD"], root).strip()
        tags = _git(["tag", "--list", "v*"], root).splitlines()
        decision = decide(version, ref, "main", tags)
    except (ReleaseError, OSError, subprocess.TimeoutExpired) as exc:
        out(f"error: {exc}")
        return 1
    out(f"pyproject 版本：{version}（tag {decision.tag}）")
    out(f"目前分支：{ref}")
    if decision.mode == "dry-run":
        out(
            "不在 main：push 時 commit 訊息含 [release-dry-run] 才會跑試建置，不會發布。"
        )
    else:
        out(f"合併到 main 後：{decision.mode}（{decision.reason}）")
    return 0


MENU = """
發布工具（CI 用的子指令見 --help）
  1) 預覽：目前版本合併到 main 時會不會發布
  2) 說明（子指令與參數）
  3) 離開
"""


def menu(
    root: Path = ROOT,
    input_fn: Callable[[str], str] = input,
    out: Callable[[str], None] = print,
) -> int:
    """互動選單；回傳最後一個動作的結束碼。輸入結束（EOF）視同離開。"""
    status = 0
    while True:
        out(MENU)
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return status
        if choice == "1":
            status = preview(root, out)
        elif choice == "2":
            out(build_parser().format_help())
            status = 0
        elif choice == "3":
            return status
        else:
            out(f"無效的選項：{choice!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """進入點：沒有參數時顯示選單。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return menu()
    return run_command(args)


if __name__ == "__main__":
    sys.exit(main())
