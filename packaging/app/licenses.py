"""產生 kikitori 桌面程式（Tauri 執行檔與前端 bundle）的第三方授權聲明。

位於打包層，與 `packaging/engine/licenses.py`（引擎的 THIRD_PARTY_LICENSES）互補：引擎目錄
只涵蓋 PyInstaller 打包的 Python 元件，這裡涵蓋 app 本身。只用標準庫；release workflow 在
`tauri build` 之前執行，輸出 `app/src-tauri/licenses/`（不進 repo），由
`tauri.sidecar.conf.json` 的 `bundle.resources` 放進安裝檔；「關於」頁可開啟它。

內容：
- Rust：`cargo about generate --format json`（設定在 `app/src-tauri/about.toml`）列出三個發布
  平台的 normal 依賴與授權全文。本機路徑的 crate（`source` 為空，也就是 kikitori 自己）略過，
  由 kikitori 的 LICENSE 涵蓋。MPL-2.0 的 crate 另寫一份取得原始碼的說明（MPL-2.0 §3.2）。
- npm：從 `package-lock.json` 由 `dependencies` 走出的正式依賴（不含 devDependencies），
  讀 `node_modules/<套件>/package.json` 與授權檔。另外加入會把自己的執行期程式碼寫進 bundle
  的建置工具（Vite 的 modulepreload polyfill）。套件沒附授權全文時從
  `license_overrides/<套件名>/` 取用（取自上游同版本、提交進 repo）；兩邊都沒有就失敗。
- kikitori 自己的 LICENSE。
"""

import argparse
import json
import re
import shutil
import subprocess
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
DEFAULT_APP_DIR = ROOT / "app"
DEFAULT_MANIFEST = DEFAULT_APP_DIR / "src-tauri" / "Cargo.toml"
DEFAULT_OUT = DEFAULT_APP_DIR / "src-tauri" / "licenses"
DEFAULT_PROJECT_LICENSE = ROOT / "LICENSE"
OVERRIDES_DIR = HERE / "license_overrides"

CARGO_ABOUT_TIMEOUT = 900
"""秒。第一次執行要下載全部 crate 的原始碼。"""

MANIFEST_NAME = "manifest.json"
GENERATOR = "packaging/app/licenses.py"
"""寫進 manifest；清空輸出目錄前用它確認那是本程式產生的目錄。"""

CRATES_IO_SOURCE = "registry+https://github.com/rust-lang/crates.io-index"

LICENSE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice|authors|copyright|thirdpartynotices|patents)",
    re.IGNORECASE,
)
"""檔名（不分大小寫）以這些字開頭的檔案視為授權或著作權聲明（與引擎相同的規則）。"""

METADATA_ONLY_SUFFIXES = (".spdx",)
"""只有 metadata、沒有授權全文的檔案（例如 Tauri 套件的 LICENSE.spdx）；會複製，但不算全文。"""

BUNDLER_RUNTIME: Mapping[str, str] = {
    "vite": "Vite injects its modulepreload polyfill into the frontend bundle",
    "rolldown": "Rolldown (Vite's bundler) may emit runtime helpers into the bundle",
}
"""devDependencies 中會把自己的程式碼寫進 bundle 的建置工具（套件名 → 理由）。"""

_SAFE_NPM_NAME = re.compile(r"^(@[a-z0-9][a-z0-9._~-]*/)?[a-z0-9][a-z0-9._~-]*$")


class NoticeError(Exception):
    """無法產生完整授權聲明（缺授權全文、資料格式不對、工具失敗）。"""


@dataclass(frozen=True)
class CrateRef:
    """cargo-about 列出的一個 crate。"""

    name: str
    version: str
    license: str
    repository: str | None
    source: str

    @property
    def crates_io_url(self) -> str:
        return f"https://crates.io/crates/{self.name}/{self.version}"

    @property
    def download_url(self) -> str:
        return f"https://static.crates.io/crates/{self.name}/{self.name}-{self.version}.crate"


@dataclass(frozen=True)
class LicenseText:
    """一份授權全文與使用它的 crate。"""

    id: str
    name: str
    text: str
    crates: tuple[CrateRef, ...]


@dataclass(frozen=True)
class RustNotices:
    crates: tuple[CrateRef, ...]
    licenses: tuple[LicenseText, ...]

    @property
    def mpl_crates(self) -> tuple[CrateRef, ...]:
        """以 MPL 授權使用的 crate（雙授權時選了別的授權的不算）。"""
        found = {
            (c.name, c.version): c
            for lic in self.licenses
            if lic.id.startswith("MPL-")
            for c in lic.crates
        }
        return tuple(found[k] for k in sorted(found))


@dataclass(frozen=True)
class NpmPackage:
    """bundle 裡的一個 npm 套件與它的授權檔（相對名稱 → 來源檔）。"""

    name: str
    version: str
    license: str
    repository: str | None
    reason: str
    files: tuple[tuple[str, Path], ...]
    override: bool


# ---------------------------------------------------------------- Rust (cargo-about)


def run_cargo_about(
    manifest_path: Path,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    timeout: float = CARGO_ABOUT_TIMEOUT,
) -> Any:
    """執行 `cargo about generate --format json`，回傳解析後的 JSON。"""
    command = [
        "cargo",
        "about",
        "generate",
        "--locked",
        "--fail",
        "--format",
        "json",
        "--manifest-path",
        str(manifest_path),
    ]
    try:
        done = runner(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=timeout,
            check=False,
        )
    except FileNotFoundError as exc:
        raise NoticeError(f"cargo not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise NoticeError(f"cargo about timed out after {timeout:.0f} s") from exc
    if done.returncode != 0:
        tail = (done.stderr or "").strip()[-2000:]
        hint = (
            " (install cargo-about, see docs/specs/packaging-ci.md)"
            if "no such command" in tail
            else ""
        )
        raise NoticeError(f"cargo about failed ({done.returncode}){hint}: {tail}")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise NoticeError(f"cargo about printed invalid JSON: {exc}") from exc


def _str(value: Any, what: str) -> str:
    if not isinstance(value, str) or not value:
        raise NoticeError(f"cargo-about JSON: {what} must be a non-empty string")
    return value


def _opt_str(value: Any, what: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise NoticeError(f"cargo-about JSON: {what} must be a string or null")
    return value or None


def _list(value: Any, what: str) -> list[Any]:
    if not isinstance(value, list):
        raise NoticeError(f"cargo-about JSON: {what} must be a list")
    return value


def _dict(value: Any, what: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise NoticeError(f"cargo-about JSON: {what} must be an object")
    return value


def _crate(package: Any, what: str) -> CrateRef | None:
    """cargo metadata 的 package → CrateRef；本機路徑的 crate（kikitori 自己）回傳 None。"""
    pkg = _dict(package, what)
    source = _opt_str(pkg.get("source"), f"{what}.source")
    if source is None:
        return None
    return CrateRef(
        name=_str(pkg.get("name"), f"{what}.name"),
        version=_str(pkg.get("version"), f"{what}.version"),
        license=_opt_str(pkg.get("license"), f"{what}.license") or "",
        repository=_opt_str(pkg.get("repository"), f"{what}.repository"),
        source=source,
    )


def parse_cargo_about(data: Any) -> RustNotices:
    """驗證並整理 cargo-about 的 JSON。每個第三方 crate 都必須有至少一份授權全文。"""
    root = _dict(data, "root")
    crates: dict[tuple[str, str], CrateRef] = {}
    for i, entry in enumerate(_list(root.get("crates"), "crates")):
        crate = _crate(
            _dict(entry, f"crates[{i}]").get("package"), f"crates[{i}].package"
        )
        if crate is not None:
            crates[(crate.name, crate.version)] = crate

    licenses: list[LicenseText] = []
    covered: set[tuple[str, str]] = set()
    for i, entry in enumerate(_list(root.get("licenses"), "licenses")):
        lic = _dict(entry, f"licenses[{i}]")
        users: dict[tuple[str, str], CrateRef] = {}
        for j, use in enumerate(_list(lic.get("used_by"), f"licenses[{i}].used_by")):
            what = f"licenses[{i}].used_by[{j}]"
            crate = _crate(_dict(use, what).get("crate"), f"{what}.crate")
            if crate is not None:
                users[(crate.name, crate.version)] = crate
        if not users:
            continue
        text = lic.get("text")
        if not isinstance(text, str) or not text.strip():
            raise NoticeError(f"cargo-about JSON: licenses[{i}] has no text")
        licenses.append(
            LicenseText(
                id=_str(lic.get("id"), f"licenses[{i}].id"),
                name=_str(lic.get("name"), f"licenses[{i}].name"),
                text=text,
                crates=tuple(users[k] for k in sorted(users)),
            )
        )
        covered.update(users)

    missing = sorted(set(crates) - covered)
    if missing:
        names = ", ".join(f"{n} {v}" for n, v in missing)
        raise NoticeError(f"no license text for crate(s): {names}")
    for crate in (
        c for lic in licenses if lic.id.startswith("MPL-") for c in lic.crates
    ):
        # MPL-2.0 §3.2：要能說明原始碼在哪裡；目前只支援 crates.io 上的 crate。
        if crate.source != CRATES_IO_SOURCE:
            raise NoticeError(
                f"MPL crate {crate.name} {crate.version} is not from crates.io ({crate.source}); "
                "add a source statement for it"
            )
    return RustNotices(
        crates=tuple(crates[k] for k in sorted(crates)), licenses=tuple(licenses)
    )


# ---------------------------------------------------------------- npm


def _lock_packages(lock: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(lock, dict) or lock.get("lockfileVersion") not in (2, 3):
        raise NoticeError("package-lock.json: lockfileVersion 2 or 3 is required")
    packages = lock.get("packages")
    if not isinstance(packages, dict) or not isinstance(packages.get(""), dict):
        raise NoticeError('package-lock.json: missing packages[""]')
    for key, value in packages.items():
        if not isinstance(value, dict):
            raise NoticeError(f"package-lock.json: packages[{key!r}] must be an object")
    return packages


def _resolve(packages: Mapping[str, Any], from_key: str, name: str) -> str | None:
    """Node 的模組解析：從 `from_key` 的 node_modules 一路往上層找 `name`。"""
    base = from_key
    while True:
        candidate = f"{base}/node_modules/{name}" if base else f"node_modules/{name}"
        if candidate in packages:
            return candidate
        if not base:
            return None
        cut = base.rfind("node_modules/")
        base = base[:cut].rstrip("/") if cut > 0 else ""


def _deps(entry: Mapping[str, Any], field: str, key: str) -> dict[str, Any]:
    value = entry.get(field, {})
    if not isinstance(value, dict):
        raise NoticeError(f"package-lock.json: {key}.{field} must be an object")
    return value


def production_closure(lock: Any) -> list[str]:
    """正式依賴（`dependencies`、`optionalDependencies`、`peerDependencies`）的遞移閉包，
    回傳 lock 的 `packages` 鍵（例如 `node_modules/@tauri-apps/api`），已排序。"""
    packages = _lock_packages(lock)
    found: set[str] = set()
    pending = [""]
    while pending:
        key = pending.pop()
        entry = packages[key]
        if entry.get("link"):
            raise NoticeError(
                f"package-lock.json: linked package {key!r} is not supported"
            )
        wanted = [(name, False) for name in _deps(entry, "dependencies", key)] + [
            (name, True) for name in _deps(entry, "optionalDependencies", key)
        ]
        peer_meta = _deps(entry, "peerDependenciesMeta", key)
        for name in _deps(entry, "peerDependencies", key):
            meta = peer_meta.get(name)
            wanted.append(
                (name, isinstance(meta, dict) and meta.get("optional") is True)
            )
        for name, optional in wanted:
            target = _resolve(packages, key, name)
            if target is None:
                if optional:
                    continue
                raise NoticeError(
                    f"package-lock.json: {name!r} (needed by {key or 'root'}) not found"
                )
            if target not in found:
                found.add(target)
                pending.append(target)
    return sorted(found)


def _license_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.iterdir() if p.is_file() and LICENSE_NAME.match(p.name)
    )


def _has_full_text(files: Iterable[Path]) -> bool:
    return any(not f.name.lower().endswith(METADATA_ONLY_SUFFIXES) for f in files)


def _repository(manifest: Mapping[str, Any]) -> str | None:
    repo = manifest.get("repository")
    if isinstance(repo, dict):
        repo = repo.get("url")
    return repo if isinstance(repo, str) and repo else None


def read_npm_package(
    app_dir: Path,
    lock_key: str,
    lock_entry: Mapping[str, Any],
    reason: str,
    overrides_dir: Path = OVERRIDES_DIR,
) -> NpmPackage:
    """讀取已安裝套件的 package.json 與授權檔；與 lock 不符或缺授權全文就失敗。"""
    directory = app_dir / lock_key
    manifest_path = directory / "package.json"
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise NoticeError(
            f"{manifest_path} is missing; run `npm ci` in app/ first"
        ) from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise NoticeError(f"cannot read {manifest_path}: {exc}") from exc
    if not isinstance(manifest, dict):
        raise NoticeError(f"{manifest_path} must contain an object")
    name, version = manifest.get("name"), manifest.get("version")
    if not isinstance(name, str) or not _SAFE_NPM_NAME.match(name):
        raise NoticeError(f"{manifest_path}: invalid name {name!r}")
    expected_name = lock_key.rsplit("node_modules/", 1)[-1]
    if (
        name != expected_name
        or not isinstance(version, str)
        or version != lock_entry.get("version")
    ):
        raise NoticeError(
            f"{manifest_path}: installed {name}@{version} does not match package-lock.json "
            f"({expected_name}@{lock_entry.get('version')}); run `npm ci` in app/"
        )
    license_expr = manifest.get("license")
    if not isinstance(license_expr, str) or not license_expr.strip():
        raise NoticeError(
            f"{name}@{version}: package.json has no SPDX `license` string"
        )

    files: dict[str, Path] = {f.name: f for f in _license_files(directory)}
    override_dir = overrides_dir / name
    override_files = _license_files(override_dir)
    if override_files:
        _check_override_version(override_dir, name, version)
    for f in override_files:
        files.setdefault(f.name, f)
    if not _has_full_text(files.values()):
        raise NoticeError(
            f"{name}@{version} ships no license text; add it to {override_dir}"
        )
    return NpmPackage(
        name=name,
        version=version,
        license=license_expr.strip(),
        repository=_repository(manifest),
        reason=reason,
        files=tuple(sorted(files.items())),
        override=any(path.parent == override_dir for path in files.values()),
    )


OVERRIDE_VERSIONS = "VERSIONS"
"""override 目錄中列出「已核對過授權全文」的套件版本（一行一個，`#` 開頭為註解）。"""


def _check_override_version(override_dir: Path, name: str, version: str) -> None:
    """升級套件後不沿用舊版核對過的全文：版本不在 VERSIONS 裡就失敗，要求重新核對上游。"""
    try:
        lines = (override_dir / OVERRIDE_VERSIONS).read_text(encoding="utf-8")
    except OSError as exc:
        raise NoticeError(
            f"{override_dir} has no readable {OVERRIDE_VERSIONS}: {exc}"
        ) from exc
    verified = {
        line.strip()
        for line in lines.splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }
    if version not in verified:
        raise NoticeError(
            f"license override for {name} was verified for {sorted(verified)}, not {version}; "
            f"re-check the upstream license files and add {version} to "
            f"{override_dir / OVERRIDE_VERSIONS}"
        )


def collect_npm(app_dir: Path, overrides_dir: Path = OVERRIDES_DIR) -> list[NpmPackage]:
    """bundle 裡的 npm 套件：正式依賴的閉包，加上會寫入執行期程式碼的建置工具。"""
    lock_path = app_dir / "package-lock.json"
    try:
        lock = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise NoticeError(f"cannot read {lock_path}: {exc}") from exc
    packages = _lock_packages(lock)
    chosen = {key: "production dependency" for key in production_closure(lock)}
    for name, reason in BUNDLER_RUNTIME.items():
        key = _resolve(packages, "", name)
        if key is not None:
            chosen.setdefault(key, reason)
    return [
        read_npm_package(app_dir, key, packages[key], reason, overrides_dir)
        for key, reason in sorted(chosen.items())
    ]


# ---------------------------------------------------------------- output


_RULE = "=" * 78


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="latin-1")


def render_rust(rust: RustNotices) -> str:
    lines = [
        "Third-party Rust crates compiled into the kikitori desktop app",
        "kikitori 桌面程式編譯進去的第三方 Rust crate 與授權全文",
        "",
        "Generated by cargo-about (app/src-tauri/about.toml) for the release targets",
        "x86_64-pc-windows-msvc, x86_64-unknown-linux-gnu and aarch64-apple-darwin.",
        "Where a crate offers a choice of licenses, the license listed is the one",
        "under which kikitori uses it. Crates that do not ship a license file are",
        "listed with the standard text of their declared license.",
        "Source code for MPL-2.0 crates: see MPL-2.0-SOURCE.txt.",
        "",
        "Crates:",
    ]
    licenses_of: dict[tuple[str, str], list[str]] = {}
    for lic in rust.licenses:
        for c in lic.crates:
            ids = licenses_of.setdefault((c.name, c.version), [])
            if lic.id not in ids:
                ids.append(lic.id)
    for c in rust.crates:
        lines.append(
            f"  {c.name} {c.version}  [{' AND '.join(licenses_of[(c.name, c.version)])}]"
        )
    for lic in rust.licenses:
        lines += ["", _RULE, f"{lic.name} ({lic.id})", "Used by:"]
        lines += [
            f"  {c.name} {c.version}  {c.repository or c.crates_io_url}"
            for c in lic.crates
        ]
        lines += [_RULE, "", lic.text.strip("\n")]
    return "\n".join(lines) + "\n"


def render_mpl_sources(rust: RustNotices) -> str:
    lines = [
        "Source code availability for MPL-2.0 components (MPL-2.0 section 3.2)",
        "MPL-2.0 元件的原始碼取得方式",
        "",
        "The kikitori desktop app includes the following crates, which are",
        "distributed under the Mozilla Public License 2.0, in executable form.",
        "kikitori uses them unmodified. Their complete Source Code Form is available",
        "free of charge from crates.io (the exact published package of each version)",
        "and from the upstream repository listed below. The full license text is in",
        "NOTICES.txt in this folder and at https://mozilla.org/MPL/2.0/.",
        "",
    ]
    for c in rust.mpl_crates:
        lines += [
            f"{c.name} {c.version}",
            f"  License:    {c.license or 'MPL-2.0'}",
            f"  crates.io:  {c.crates_io_url}",
            f"  Download:   {c.download_url}",
            f"  Repository: {c.repository or '(not declared; use the crates.io package)'}",
            "",
        ]
    if not rust.mpl_crates:
        lines.append("(none in this build)")
    return "\n".join(lines).rstrip("\n") + "\n"


def render_npm(packages: Sequence[NpmPackage]) -> str:
    lines = [
        "Third-party npm packages included in the kikitori frontend bundle",
        "kikitori 前端 bundle 內含的第三方 npm 套件與授權全文",
        "",
        "Packages:",
    ]
    for p in packages:
        lines.append(f"  {p.name} {p.version}  [{p.license}]  ({p.reason})")
    for p in packages:
        lines += ["", _RULE, f"{p.name} {p.version}  [{p.license}]"]
        if p.repository:
            lines.append(f"Repository: {p.repository}")
        if p.override:
            lines.append(
                "The npm package does not include its license text; the text below is "
                "taken from the upstream repository."
            )
        lines.append(_RULE)
        for file_name, source in p.files:
            lines += ["", f"--- {file_name} ---", "", _read_text(source).strip("\n")]
    return "\n".join(lines) + "\n"


def render_readme(rust: RustNotices, npm: Sequence[NpmPackage]) -> str:
    return "\n".join(
        [
            "kikitori desktop app: licenses and third-party notices",
            "kikitori 桌面程式的授權與第三方元件聲明",
            "",
            "kikitori/LICENSE         kikitori itself (MIT)",
            f"rust/NOTICES.txt         {len(rust.crates)} Rust crates compiled into the app",
            (
                "rust/MPL-2.0-SOURCE.txt  where to get the source of the "
                f"{len(rust.mpl_crates)} MPL-2.0 crates"
            ),
            f"npm/NOTICES.txt          {len(npm)} npm packages in the frontend bundle",
            "",
            "The speech engine (kikitori-engine) ships its own third-party licenses in",
            "kikitori-engine/THIRD_PARTY_LICENSES/ next to this folder",
            "(on macOS: Contents/Resources/kikitori-engine/THIRD_PARTY_LICENSES/).",
            "語音引擎的第三方授權在旁邊的 kikitori-engine/THIRD_PARTY_LICENSES/ 資料夾。",
            "",
        ]
    )


def _ensure_replaceable(out_dir: Path) -> None:
    """只清空本程式產生過的目錄（或空目錄），避免 `--out` 指錯時刪掉別的東西。"""
    if not out_dir.exists():
        return
    if not out_dir.is_dir():
        raise NoticeError(f"{out_dir} exists and is not a directory")
    if not any(out_dir.iterdir()):
        return
    try:
        manifest = json.loads((out_dir / MANIFEST_NAME).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        manifest = None
    if not isinstance(manifest, dict) or manifest.get("generator") != GENERATOR:
        raise NoticeError(
            f"{out_dir} is not empty and was not generated by {GENERATOR}; refusing to replace it"
        )


def write(
    out_dir: Path,
    rust: RustNotices,
    npm: Sequence[NpmPackage],
    project_license: Path = DEFAULT_PROJECT_LICENSE,
) -> None:
    """寫出整個授權目錄（先清空舊的輸出）。"""
    if not project_license.is_file():
        raise NoticeError(f"{project_license} is missing")
    _ensure_replaceable(out_dir)
    if out_dir.exists():
        shutil.rmtree(out_dir)
    (out_dir / "kikitori").mkdir(parents=True)
    (out_dir / "rust").mkdir()
    (out_dir / "npm").mkdir()
    shutil.copyfile(project_license, out_dir / "kikitori" / "LICENSE")
    texts = {
        "README.txt": render_readme(rust, npm),
        "rust/NOTICES.txt": render_rust(rust),
        "rust/MPL-2.0-SOURCE.txt": render_mpl_sources(rust),
        "npm/NOTICES.txt": render_npm(npm),
    }
    for relative, content in texts.items():
        (out_dir / relative).write_text(content, encoding="utf-8", newline="\n")
    manifest = {
        "generator": GENERATOR,
        "rust": [
            {
                "name": c.name,
                "version": c.version,
                "license": c.license,
                "repository": c.repository,
            }
            for c in rust.crates
        ],
        "mpl_sources": [
            {
                "name": c.name,
                "version": c.version,
                "crates_io": c.crates_io_url,
                "download": c.download_url,
                "repository": c.repository,
            }
            for c in rust.mpl_crates
        ],
        "npm": [
            {
                "name": p.name,
                "version": p.version,
                "license": p.license,
                "reason": p.reason,
                "files": [f for f, _ in p.files],
                "override": p.override,
            }
            for p in npm
        ],
    }
    (out_dir / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def generate(
    out_dir: Path = DEFAULT_OUT,
    app_dir: Path = DEFAULT_APP_DIR,
    manifest_path: Path = DEFAULT_MANIFEST,
    cargo_about_json: Path | None = None,
    out: Callable[[str], None] = print,
) -> int:
    """產生授權目錄；回傳結束碼（失敗時印出原因、回傳 1）。"""
    try:
        if cargo_about_json is None:
            out("running cargo about (Rust dependencies)...")
            data = run_cargo_about(manifest_path)
        else:
            try:
                data = json.loads(cargo_about_json.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                raise NoticeError(f"cannot read {cargo_about_json}: {exc}") from exc
        rust = parse_cargo_about(data)
        npm = collect_npm(app_dir)
        write(out_dir, rust, npm)
    except NoticeError as exc:
        out(f"LICENSE NOTICES FAILED: {exc}")
        return 1
    except OSError as exc:
        # 寫入輸出目錄失敗（檔案被鎖、權限、磁碟）：可能留下不完整的目錄，再跑一次會整個重寫。
        out(f"LICENSE NOTICES FAILED: cannot write {out_dir}: {exc}")
        return 1
    out(
        f"wrote {out_dir}: {len(rust.crates)} crates "
        f"({len(rust.mpl_crates)} MPL-2.0: {', '.join(c.name for c in rust.mpl_crates) or 'none'}), "
        f"{len(npm)} npm packages ({', '.join(p.name for p in npm)})"
    )
    return 0


BUNDLED_FILES = (
    "README.txt",
    "kikitori/LICENSE",
    "rust/NOTICES.txt",
    "rust/MPL-2.0-SOURCE.txt",
    "npm/NOTICES.txt",
    MANIFEST_NAME,
)
APP_LICENSES_RESOURCE = "licenses"
"""資源目錄中的資料夾名稱（`tauri.sidecar.conf.json`、Rust 的 `APP_LICENSES_DIR`）。"""
ENGINE_LICENSES_README = Path("kikitori-engine", "THIRD_PARTY_LICENSES", "README.txt")


def check_bundle(resource_dir: Path) -> list[str]:
    """檢查安裝後的資源目錄裡兩份授權資料夾都在、MPL crate 都有原始碼說明；回傳問題清單。"""
    folder = resource_dir / APP_LICENSES_RESOURCE
    problems = [
        f"missing {APP_LICENSES_RESOURCE}/{name}"
        for name in BUNDLED_FILES
        if not (folder / name).is_file()
    ]
    if not (resource_dir / ENGINE_LICENSES_README).is_file():
        problems.append(f"missing {ENGINE_LICENSES_README.as_posix()}")
    if problems:
        return problems
    try:
        manifest = json.loads((folder / MANIFEST_NAME).read_text(encoding="utf-8"))
        mpl_text = (folder / "rust" / "MPL-2.0-SOURCE.txt").read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return [f"cannot read the bundled notices: {exc}"]
    if not isinstance(manifest, dict) or manifest.get("generator") != GENERATOR:
        return [f"{MANIFEST_NAME} was not written by {GENERATOR}"]
    for key in ("rust", "npm"):
        if not isinstance(manifest.get(key), list) or not manifest[key]:
            problems.append(f"{MANIFEST_NAME}: no {key} entries")
    mpl = manifest.get("mpl_sources")
    if not isinstance(mpl, list):
        return problems + [f"{MANIFEST_NAME}: mpl_sources must be a list"]
    statements = _mpl_statements(mpl_text)
    for entry in mpl:
        if not isinstance(entry, dict):
            problems.append(f"{MANIFEST_NAME}: bad mpl_sources entry {entry!r}")
            continue
        heading = f"{entry.get('name')} {entry.get('version')}"
        # 逐行精確比對：子字串比對會讓 `foo 1.0` 被 `foo 1.0.1` 的說明蓋過。
        if f"crates.io:  {entry.get('crates_io')}" not in statements.get(heading, ()):
            problems.append(f"MPL-2.0-SOURCE.txt has no source statement for {heading}")
    return problems


def _mpl_statements(text: str) -> dict[str, set[str]]:
    """`MPL-2.0-SOURCE.txt` 的說明區塊：`<名稱> <版本>` 那一行 → 其下縮排各行（去掉縮排）。"""
    blocks: dict[str, set[str]] = {}
    current: set[str] | None = None
    for line in text.splitlines():
        if line.startswith("  ") and current is not None:
            current.add(line.strip())
        elif line.strip():
            current = blocks.setdefault(line.strip(), set())
        else:
            current = None
    return blocks


def verify_bundle(resource_dir: Path, out: Callable[[str], None] = print) -> int:
    """`check_bundle` 的 CLI 版本；回傳結束碼。"""
    problems = check_bundle(resource_dir)
    for problem in problems:
        out(f"BUNDLED NOTICES INCOMPLETE: {problem}")
    if problems:
        return 1
    out(f"license folders OK in {resource_dir}")
    return 0


def list_npm(
    app_dir: Path = DEFAULT_APP_DIR, out: Callable[[str], None] = print
) -> int:
    """只列出會收錄的 npm 套件（不寫檔）。"""
    try:
        packages = collect_npm(app_dir)
    except NoticeError as exc:
        out(f"LICENSE NOTICES FAILED: {exc}")
        return 1
    for p in packages:
        note = "  (license text from license_overrides/)" if p.override else ""
        out(f"{p.name} {p.version}  [{p.license}]  {p.reason}{note}")
    return 0


# ---------------------------------------------------------------- CLI

MENU = """
kikitori app 第三方授權聲明
  1) 產生授權目錄到 {out}（需要 cargo-about 與 app/node_modules）
  2) 只列出前端 bundle 會收錄的 npm 套件
  3) 離開
"""


def menu(
    input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print
) -> int:
    """互動選單（給人用）；回傳結束碼。"""
    while True:
        out(MENU.format(out=DEFAULT_OUT))
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "1":
            return generate(out=out)
        if choice == "2":
            return list_npm(out=out)
        if choice == "3":
            return 0
        out(f"無效的選項：{choice!r}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CI 用的參數。"""
    parser = argparse.ArgumentParser(
        description="Generate the app's third-party notices"
    )
    parser.add_argument(
        "--out", type=Path, default=DEFAULT_OUT, help=f"default: {DEFAULT_OUT}"
    )
    parser.add_argument(
        "--app-dir", type=Path, default=DEFAULT_APP_DIR, help="frontend project"
    )
    parser.add_argument(
        "--manifest-path",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Cargo.toml of the app",
    )
    parser.add_argument(
        "--cargo-about-json",
        type=Path,
        help="use this `cargo about generate --format json` output instead of running it",
    )
    parser.add_argument(
        "--check-bundle",
        type=Path,
        metavar="RESOURCE_DIR",
        help="only verify the license folders inside an installed app's resource directory",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    """進入點：沒有參數時顯示選單。"""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return menu()
    args = parse_args(args_list)
    if args.check_bundle is not None:
        return verify_bundle(args.check_bundle)
    return generate(
        out_dir=args.out.resolve(),
        app_dir=args.app_dir.resolve(),
        manifest_path=args.manifest_path.resolve(),
        cargo_about_json=args.cargo_about_json,
    )


if __name__ == "__main__":
    raise SystemExit(main())
