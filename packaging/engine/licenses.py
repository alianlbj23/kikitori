"""收集打包進 `kikitori-engine` 的第三方授權檔，產生 THIRD_PARTY_LICENSES 目錄。

位於打包層，由 `kikitori-engine.spec` 在 PyInstaller 分析完之後呼叫（只用標準庫）。
做法：把 PyInstaller 分析出的每個來源檔（Python 模組、原生函式庫、資料檔）對回
「提供它的已安裝套件」（依各套件 RECORD 的檔案清單），再從那些套件的 metadata
複製授權全文。這樣列出的就是「實際打包進去的東西」，不是依賴清單的猜測。

另外固定加入：
- Python 本身（`LICENSE.txt`，含 CPython 內附的 bzip2、OpenSSL 等第三方條款）。
- PyInstaller（bootloader 在執行檔裡；GPL-2.0-or-later 附 bootloader 例外條款）。
- kikitori 自己（以 editable 方式安裝時 RECORD 沒有原始碼，依原始碼目錄判斷）。

wheel 沒附授權檔的套件，從 `license_overrides/<套件名>/` 取用（取自上游同版本的
授權全文、提交進 repo）；兩邊都沒有就列為錯誤，交給呼叫端決定是否中止建置。
"""

import fnmatch
import json
import re
import shutil
import sys
import sysconfig
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from importlib import metadata
from pathlib import Path, PurePath

LICENSE_NAME = re.compile(
    r"^(licen[cs]e|copying|notice|authors|copyright|thirdpartynotices|patents)",
    re.IGNORECASE,
)
"""檔名（不分大小寫）以這些字開頭的檔案視為授權或著作權聲明。"""

PYTHON_KEY = "python"
PROJECT_KEY = "kikitori"
PYINSTALLER_DIST = "pyinstaller"

LICENSE_SUMMARY_OVERRIDES = {
    # metadata 的 classifier 只寫 GPLv2；實際條款是 GPL-2.0-or-later 加 bootloader 例外，
    # 執行檔裡只有 bootloader，例外條款允許以任何授權散布打包出來的程式。
    PYINSTALLER_DIST: "GPL-2.0-or-later WITH Bootloader-exception (only the bootloader is bundled)",
}

_GCC_RUNTIME = (
    "GCC runtime: GPL-3.0-or-later WITH GCC-exception-3.1 "
    "(texts in ctranslate2-*/upstream/third_party/libgomp/)"
)
_MSVC_RUNTIME = (
    "Microsoft Visual C++ Redistributable (Distributable Code under the Visual Studio "
    "license terms)"
)
KNOWN_SYSTEM_LIBRARIES: tuple[tuple[str, str], ...] = (
    ("msvcp140*.dll", _MSVC_RUNTIME),
    ("vcruntime140*.dll", _MSVC_RUNTIME),
    ("concrt140.dll", _MSVC_RUNTIME),
    ("libstdc++.so*", _GCC_RUNTIME),
    ("libgcc_s.so*", _GCC_RUNTIME),
    ("libgomp*.so*", _GCC_RUNTIME),
    ("libz.so*", "zlib License (Zlib)"),
)
"""PyInstaller 從建置機器收集、不屬於任何 Python 套件的常見函式庫（檔名 → 授權說明）。"""


def system_library_note(name: str) -> str | None:
    """已知系統函式庫的授權說明；不認得就回傳 None。"""
    base = PurePath(name).name.lower()
    for pattern, note in KNOWN_SYSTEM_LIBRARIES:
        if fnmatch.fnmatchcase(base, pattern):
            return note
    return None


def normalize_name(name: str) -> str:
    """PEP 503 正規化的套件名稱（小寫、`-_.` 一律換成 `-`）。"""
    return re.sub(r"[-_.]+", "-", name).lower()


@dataclass
class Component:
    """THIRD_PARTY_LICENSES 中的一個項目（一個套件、Python 本身或本專案）。"""

    key: str
    name: str
    version: str
    license: str
    files: dict[str, Path] = field(default_factory=dict)
    """輸出目錄內的相對路徑 → 來源檔。"""


@dataclass
class LicenseReport:
    """`collect()` 的結果。"""

    components: list[Component]
    unmapped: list[str]
    """對不回任何已安裝套件的來源檔（通常是作業系統的函式庫）。"""
    missing: list[str]
    """有打包進去、卻找不到授權全文的元件 key。"""


def _license_summary(meta: metadata.PackageMetadata) -> str:
    expression = meta.get("License-Expression")
    if expression:
        return expression.strip()
    classifiers = [
        c.split("::")[-1].strip()
        for c in meta.get_all("Classifier") or []
        if c.startswith("License ::")
    ]
    if classifiers:
        return "; ".join(classifiers)
    text = (meta.get("License") or "").strip()
    # 有些套件把整份授權全文塞在 License 欄位；摘要只取第一行。
    first_line = text.splitlines()[0] if text else ""
    return first_line[:120] or "UNKNOWN"


_NOT_LICENSE_SUFFIXES = frozenset(
    {".py", ".pyc", ".pyi", ".pyd", ".so", ".dylib", ".dll", ".yml", ".yaml"}
)
"""檔名帶這些副檔名的不是授權全文，即使名稱以 copying 等字開頭（例如 pyobjc-core 的
測試擴充模組 `copying.cpython-312-darwin.so`）。"""


def _is_code_or_debug_file(parts: Sequence[str]) -> bool:
    # macOS 的 `.dSYM` 是除錯符號套件，裡面的檔案都不是授權全文。
    if any(part.endswith(".dSYM") for part in parts[:-1]):
        return True
    return any(s.lower() in _NOT_LICENSE_SUFFIXES for s in PurePath(parts[-1]).suffixes)


def _dist_license_files(dist: metadata.Distribution) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for item in dist.files or ():
        parts = PurePath(str(item)).parts
        if not parts or ".." in parts:
            continue
        in_dist_info = parts[0].endswith((".dist-info", ".egg-info"))
        is_license = (
            LICENSE_NAME.match(parts[-1]) is not None
            and not _is_code_or_debug_file(parts)
        ) or (in_dist_info and len(parts) > 2 and parts[1] == "licenses")
        if not is_license:
            continue
        source = Path(str(dist.locate_file(item)))
        if not source.is_file():
            continue
        relative = parts[1:] if in_dist_info else parts
        found[PurePath(*relative).as_posix()] = source
    return found


def _override_files(overrides_dir: Path | None, key: str) -> dict[str, Path]:
    if overrides_dir is None:
        return {}
    directory = overrides_dir / key
    if not directory.is_dir():
        return {}
    return {
        p.relative_to(directory).as_posix(): p
        for p in sorted(directory.rglob("*"))
        if p.is_file()
    }


def python_license_file() -> Path | None:
    """目前直譯器的 CPython 授權檔（Windows 在安裝根目錄，POSIX 在標準庫目錄）。"""
    candidates = [
        Path(sys.base_prefix) / "LICENSE.txt",
        Path(sysconfig.get_path("stdlib")) / "LICENSE.txt",
    ]
    return next((p for p in candidates if p.is_file()), None)


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def build_file_index(
    distributions: Iterable[metadata.Distribution],
) -> dict[Path, metadata.Distribution]:
    """已安裝套件 RECORD 中每個檔案的絕對路徑 → 提供它的套件。"""
    index: dict[Path, metadata.Distribution] = {}
    for dist in distributions:
        for item in dist.files or ():
            index[_resolve(Path(str(dist.locate_file(item))))] = dist
    return index


def _is_under(path: Path, roots: Sequence[Path]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def collect(
    sources: Iterable[str | Path],
    *,
    project_root: Path,
    overrides_dir: Path | None = None,
    distributions: Iterable[metadata.Distribution] | None = None,
    python_roots: Sequence[Path] | None = None,
    python_license: Path | None = None,
    python_version: str | None = None,
    generated_roots: Sequence[Path] = (),
) -> LicenseReport:
    """把打包的來源檔對回元件，並找出各元件的授權檔。

    Args:
        sources: PyInstaller TOC 中的來源檔路徑（不存在的路徑會略過）。
        project_root: 本專案根目錄（`src/` 下的檔案歸給 kikitori，授權為根目錄的 LICENSE）。
        overrides_dir: `license_overrides/` 目錄；wheel 沒附授權檔的套件從這裡補。
        distributions: 要比對的已安裝套件；省略時用目前環境的全部套件（測試可注入）。
        python_roots: 直譯器的安裝目錄；預設 `sys.base_prefix` 與 `sys.base_exec_prefix`。
        python_license: CPython 授權檔；預設 `python_license_file()`。
        python_version: 預設目前直譯器版本。
        generated_roots: PyInstaller 的工作目錄；其中的檔案（例如由標準庫產生的
            `base_library.zip`）歸給 Python。
    """
    dists = list(metadata.distributions() if distributions is None else distributions)
    index = build_file_index(dists)
    project_src = _resolve(project_root / "src")
    roots = [
        _resolve(Path(p))
        for p in (python_roots or (sys.base_prefix, sys.base_exec_prefix))
    ]
    roots += [_resolve(Path(p)) for p in generated_roots]

    used: dict[str, metadata.Distribution] = {}
    uses_python = False
    unmapped: set[str] = set()
    for raw in sources:
        path = _resolve(Path(raw))
        if not path.is_file():
            continue
        dist = index.get(path)
        if dist is not None:
            used.setdefault(normalize_name(dist.metadata["Name"]), dist)
        elif path.is_relative_to(project_src):
            continue  # 本專案的程式碼，下面固定加入
        elif _is_under(path, roots):
            uses_python = True
        else:
            unmapped.add(path.as_posix())

    # bootloader 一定在執行檔裡，PyInstaller 的授權不論 TOC 有沒有對到都要附上。
    by_name = {normalize_name(d.metadata["Name"]): d for d in dists}
    if PYINSTALLER_DIST in by_name:
        used.setdefault(PYINSTALLER_DIST, by_name[PYINSTALLER_DIST])

    components: list[Component] = []
    missing: list[str] = []

    project_license = project_root / "LICENSE"
    project = Component(PROJECT_KEY, "kikitori", _project_version(by_name), "MIT")
    if project_license.is_file():
        project.files["LICENSE"] = project_license
    # 套件內附的資料檔若有自己的授權（例如 Silero VAD 模型的 LICENSE-silero-vad），一併附上。
    if project_src.is_dir():
        for path in sorted(project_src.rglob("*")):
            if path.is_file() and LICENSE_NAME.match(path.name):
                project.files[f"src/{path.relative_to(project_src).as_posix()}"] = path
    components.append(project)

    py_license = python_license if python_license is not None else python_license_file()
    if uses_python or python_license is not None:
        py = Component(
            PYTHON_KEY,
            "Python",
            python_version or sys.version.split()[0],
            "PSF-2.0 (with third-party notices)",
        )
        if py_license is not None and py_license.is_file():
            py.files["LICENSE.txt"] = py_license
        components.append(py)

    for key in sorted(used):
        if key == PROJECT_KEY:
            continue
        dist = used[key]
        component = Component(
            key,
            dist.metadata["Name"],
            dist.version,
            LICENSE_SUMMARY_OVERRIDES.get(key) or _license_summary(dist.metadata),
            _dist_license_files(dist),
        )
        for relative, source in _override_files(overrides_dir, key).items():
            component.files.setdefault(f"upstream/{relative}", source)
        components.append(component)

    for component in components:
        if not component.files:
            missing.append(component.key)
    return LicenseReport(components, sorted(unmapped), missing)


def _project_version(by_name: Mapping[str, metadata.Distribution]) -> str:
    dist = by_name.get(PROJECT_KEY)
    return dist.version if dist is not None else "unknown"


def write(report: LicenseReport, out_dir: Path) -> None:
    """把授權檔複製到 `out_dir`（先清空），並寫 `README.txt` 與 `manifest.json`。

    每個元件一個子目錄 `<key>-<version>/`，保留授權檔原本的相對路徑，避免同名檔互相覆蓋。
    """
    if out_dir.exists():
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True)
    manifest: list[dict[str, object]] = []
    lines = [
        "kikitori-engine 內附的第三方元件與授權",
        "Third-party components bundled in kikitori-engine",
        "",
        "每個子目錄是一個元件，內含其授權全文。",
        "",
    ]
    for component in components_sorted(report.components):
        directory = f"{component.key}-{component.version}"
        for relative, source in sorted(component.files.items()):
            target = out_dir / directory / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
        manifest.append(
            {
                "key": component.key,
                "name": component.name,
                "version": component.version,
                "license": component.license,
                "directory": directory,
                "files": sorted(component.files),
            }
        )
        lines.append(f"{component.name} {component.version}  [{component.license}]")
        lines.append(f"    {directory}/")
    if report.unmapped:
        lines += [
            "",
            "下列系統函式庫由 PyInstaller 從建置機器收集，不屬於任何 Python 套件：",
            "System libraries collected from the build machine (no Python package):",
        ]
        for path in report.unmapped:
            note = system_library_note(path) or "UNKNOWN - review before release"
            lines.append(f"    {PurePath(path).name}  [{note}]")
    (out_dir / "README.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (out_dir / "manifest.json").write_text(
        json.dumps(
            {
                "components": manifest,
                "unmapped": [
                    {"file": PurePath(p).name, "license": system_library_note(p)}
                    for p in report.unmapped
                ],
                "missing": report.missing,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


def components_sorted(components: Iterable[Component]) -> list[Component]:
    """本專案、Python 在前，其餘依名稱排序。"""
    first = {PROJECT_KEY: 0, PYTHON_KEY: 1}
    return sorted(components, key=lambda c: (first.get(c.key, 2), c.key))
