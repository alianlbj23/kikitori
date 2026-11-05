"""建置 `kikitori-engine`（PyInstaller）並放到 Tauri 取用的位置。

位於打包層，只用標準庫；PyInstaller 以 `python -m PyInstaller` 在同一個環境執行
（`uv run python packaging/engine/build.py`）。

- 給人用：不帶參數執行會顯示編號選單。
- 給 CI：設 `KIKITORI_BUILD_NONINTERACTIVE=1`（不帶參數 = 預設建置），或直接給參數：
    python packaging/engine/build.py --smoke
    python packaging/engine/build.py --onefile --sidecar-dir app/src-tauri/binaries

輸出：
- one-folder（預設）：`dist/kikitori-engine/`（執行檔、`_internal/`、`THIRD_PARTY_LICENSES/`），
  並複製到 `<sidecar-dir>/kikitori-engine/`，給 Tauri `bundle.resources` 使用。
- one-file：`dist/onefile/kikitori-engine[.exe]`，並以 Tauri `externalBin` 的命名
  `<sidecar-dir>/kikitori-engine-<rust target triple>[.exe]` 複製一份。

選擇 one-folder 的理由與量測數字見 `docs/specs/packaging-ci.md`。
"""

import argparse
import importlib.util
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import tomllib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path
from types import ModuleType
from typing import Literal

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
SPEC_FILE = HERE / "kikitori-engine.spec"
PYPROJECT = ROOT / "pyproject.toml"
DIST_DIR = ROOT / "dist"
WORK_DIR = ROOT / "build" / "pyinstaller"
DEFAULT_SIDECAR_DIR = ROOT / "app" / "src-tauri" / "binaries"

NONINTERACTIVE_ENV = "KIKITORI_BUILD_NONINTERACTIVE"
TARGET_TRIPLE_ENV = "KIKITORI_TARGET_TRIPLE"

BUILD_TIMEOUT_SECS = 45 * 60
RUSTC_TIMEOUT_SECS = 15

Mode = Literal["onedir", "onefile"]

_TRIPLE = re.compile(r"^[a-z0-9_]+(-[a-z0-9_.]+){2,3}$")


class BuildError(RuntimeError):
    """建置失敗（PyInstaller 錯誤、版本不一致、打包了禁止的函式庫…）。"""


def _sibling(name: str) -> ModuleType:
    # 目錄名稱 `packaging` 會與 PyPI 的 `packaging` 套件撞名，同目錄的模組一律依路徑載入。
    module_name = f"kikitori_build_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, HERE / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {HERE / f'{name}.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


policy = _sibling("policy")


def _say(message: str) -> None:
    # PyInstaller 的輸出直接寫到同一個終端機；先 flush，訊息順序才不會亂。
    print(message, flush=True)


# ---------------------------------------------------------------- 版本與目標平台


def project_version(pyproject: Path = PYPROJECT) -> str:
    """`pyproject.toml` 的 `[project].version`（版本號的單一來源）。"""
    with pyproject.open("rb") as f:
        data = tomllib.load(f)
    version = data.get("project", {}).get("version")
    if not isinstance(version, str) or not version:
        raise BuildError(f"{pyproject}: [project].version is missing")
    return version


def check_installed_version(expected: str) -> None:
    """確認環境中 kikitori 的 metadata 版本與 pyproject 相同。

    執行檔的 `engine.info` 版本來自打包進去的 metadata；pyproject 改了版本卻沒重新
    `uv sync` 時兩者會不同，這裡先擋下來。
    """
    try:
        installed = metadata.version("kikitori")
    except metadata.PackageNotFoundError as exc:
        raise BuildError(
            "kikitori is not installed in this environment; run `uv sync`"
        ) from exc
    if installed != expected:
        raise BuildError(
            f"installed kikitori metadata is {installed} but pyproject.toml says "
            f"{expected}; run `uv sync` first"
        )


_ARCH_ALIASES = {
    "amd64": "x86_64",
    "x86_64": "x86_64",
    "x64": "x86_64",
    "arm64": "aarch64",
    "aarch64": "aarch64",
}
_OS_SUFFIX = {
    "win32": "pc-windows-msvc",
    "linux": "unknown-linux-gnu",
    "darwin": "apple-darwin",
}


def fallback_target_triple(
    system: str = sys.platform, machine: str | None = None
) -> str:
    """沒有 rustc 時，依作業系統與 CPU 推出 Rust target triple。"""
    machine = (machine or platform.machine()).lower()
    arch = _ARCH_ALIASES.get(machine)
    suffix = _OS_SUFFIX.get(system)
    if arch is None or suffix is None:
        raise BuildError(
            f"cannot derive a Rust target triple for {system}/{machine}; "
            f"set {TARGET_TRIPLE_ENV}"
        )
    return f"{arch}-{suffix}"


def _validated_triple(value: str, source: str) -> str:
    value = value.strip()
    if not _TRIPLE.match(value):
        raise BuildError(f"{source}: {value!r} is not a Rust target triple")
    return value


def rust_target_triple(
    explicit: str | None = None,
    *,
    env: Mapping[str, str] | None = None,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> str:
    """Tauri sidecar 檔名要用的 target triple。

    順序：參數 > 環境變數 `KIKITORI_TARGET_TRIPLE` > `rustc -vV` 的 host > 依平台推算。
    與 Tauri 文件建議的作法相同（以 rustc 的 host 為準）。
    """
    if explicit:
        return _validated_triple(explicit, "--target-triple")
    env = os.environ if env is None else env
    if env.get(TARGET_TRIPLE_ENV, "").strip():
        return _validated_triple(env[TARGET_TRIPLE_ENV], TARGET_TRIPLE_ENV)
    try:
        completed = run(
            ["rustc", "-vV"],
            capture_output=True,
            text=True,
            timeout=RUSTC_TIMEOUT_SECS,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return fallback_target_triple()
    if completed.returncode == 0:
        for line in completed.stdout.splitlines():
            if line.startswith("host:"):
                return _validated_triple(line.split(":", 1)[1], "rustc -vV")
    return fallback_target_triple()


def executable_name(base: str = policy.APP_NAME, system: str = sys.platform) -> str:
    """平台上的執行檔名稱（Windows 加 `.exe`）。"""
    return f"{base}.exe" if system == "win32" else base


def sidecar_file_name(triple: str, system: str = sys.platform) -> str:
    """Tauri `externalBin` 的檔名：`kikitori-engine-<triple>[.exe]`。"""
    return executable_name(f"{policy.APP_NAME}-{triple}", system)


# ---------------------------------------------------------------- 建置


@dataclass(frozen=True)
class BuildPaths:
    """一種模式的輸出位置。"""

    mode: Mode
    dist_dir: Path
    work_dir: Path

    @property
    def executable(self) -> Path:
        """建置出的執行檔。"""
        if self.mode == "onedir":
            return self.dist_dir / policy.APP_NAME / executable_name()
        return self.dist_dir / executable_name()

    @property
    def bundle(self) -> Path:
        """要整個交給 Tauri 的東西：one-folder 是目錄，one-file 是執行檔。"""
        if self.mode == "onedir":
            return self.dist_dir / policy.APP_NAME
        return self.executable

    @property
    def licenses_dir(self) -> Path:
        """THIRD_PARTY_LICENSES 的位置（one-folder 在目錄內，one-file 在執行檔旁）。"""
        if self.mode == "onedir":
            return self.bundle / policy.LICENSES_DIR_NAME
        return self.dist_dir / policy.LICENSES_DIR_NAME


def paths_for(
    mode: Mode, dist_root: Path = DIST_DIR, work_root: Path = WORK_DIR
) -> BuildPaths:
    """預設輸出位置；one-file 放在 `dist/onefile/`，避免 Linux／macOS 上與目錄同名。"""
    if mode == "onedir":
        return BuildPaths(mode, dist_root, work_root / "onedir")
    return BuildPaths(mode, dist_root / "onefile", work_root / "onefile")


def pyinstaller_command(paths: BuildPaths, python: str = sys.executable) -> list[str]:
    """呼叫 PyInstaller 的完整指令（不經 shell）。"""
    command = [
        python,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--log-level",
        "WARN",
        "--distpath",
        str(paths.dist_dir),
        "--workpath",
        str(paths.work_dir),
        str(SPEC_FILE),
        "--",
        "--licenses-out",
        str(paths.licenses_dir),
    ]
    if paths.mode == "onefile":
        command.append("--onefile")
    return command


def tree_size(path: Path) -> int:
    """檔案或目錄的總位元組數。"""
    if path.is_file():
        return path.stat().st_size
    # PyInstaller 在 Linux／macOS 會建立指向同一檔案的符號連結，跳過才不會重複計算。
    return sum(
        p.stat().st_size
        for p in path.rglob("*")
        if p.is_file() and not p.is_symlink()
    )


@dataclass(frozen=True)
class BuildResult:
    """一次建置的結果。"""

    paths: BuildPaths
    version: str
    size_bytes: int
    seconds: float


def build(
    mode: Mode = "onedir",
    *,
    paths: BuildPaths | None = None,
    run: Callable[..., subprocess.CompletedProcess[bytes]] = subprocess.run,
    out: Callable[[str], None] = _say,
) -> BuildResult:
    """執行 PyInstaller，並檢查輸出沒有禁止打包的函式庫。

    Raises:
        BuildError: 版本不一致、PyInstaller 失敗或逾時、輸出缺執行檔或含禁止的檔案。
    """
    paths = paths or paths_for(mode)
    version = project_version()
    check_installed_version(version)
    command = pyinstaller_command(paths)
    out(f"building kikitori-engine {version} ({mode}) ...")
    started = time.monotonic()
    try:
        completed = run(command, cwd=ROOT, timeout=BUILD_TIMEOUT_SECS, check=False)
    except subprocess.TimeoutExpired as exc:
        raise BuildError(f"PyInstaller timed out after {BUILD_TIMEOUT_SECS}s") from exc
    if completed.returncode != 0:
        raise BuildError(f"PyInstaller failed with exit code {completed.returncode}")
    elapsed = time.monotonic() - started
    if not paths.executable.is_file():
        raise BuildError(f"PyInstaller finished but {paths.executable} is missing")
    forbidden = policy.find_forbidden(paths.bundle)
    if forbidden:
        listing = "\n  ".join(str(p) for p in forbidden)
        raise BuildError(f"forbidden libraries ended up in the bundle:\n  {listing}")
    size = tree_size(paths.bundle)
    out(f"built {paths.bundle} ({size / 1_000_000:.1f} MB) in {elapsed:.0f}s")
    return BuildResult(paths, version, size, elapsed)


def install_sidecar(
    result: BuildResult, target_dir: Path, triple: str | None = None
) -> Path:
    """把建置結果放到 Tauri 取用的目錄，回傳放好的路徑。

    - one-folder：整個目錄複製到 `<target_dir>/kikitori-engine/`（先移除舊的同名目錄）。
    - one-file：執行檔複製成 `<target_dir>/kikitori-engine-<triple>[.exe]`，授權目錄複製到
      `<target_dir>/kikitori-engine-THIRD_PARTY_LICENSES/`。

    只會刪除／覆寫上述這幾個名稱，`target_dir` 裡的其他檔案不動。
    """
    target_dir.mkdir(parents=True, exist_ok=True)
    paths = result.paths
    if paths.mode == "onedir":
        destination = target_dir / policy.APP_NAME
        if destination.exists():
            shutil.rmtree(destination)
        shutil.copytree(paths.bundle, destination, symlinks=True)
        return destination
    if triple is None:
        raise ValueError("a target triple is required for a one-file sidecar")
    destination = target_dir / sidecar_file_name(triple)
    shutil.copy2(paths.executable, destination)
    licenses = target_dir / f"{policy.APP_NAME}-{policy.LICENSES_DIR_NAME}"
    if licenses.exists():
        shutil.rmtree(licenses)
    shutil.copytree(paths.licenses_dir, licenses)
    return destination


def smoke(result: BuildResult, out: Callable[[str], None] = _say) -> int:
    """對建置好的執行檔跑冒煙測試（含 `--self-check`）；回傳結束碼。"""
    smoke_test = _sibling("smoke_test")
    executable = result.paths.executable
    return smoke_test.run(
        [str(executable)],
        self_check_binary=executable,
        expect_version=result.version,
        out=out,
    )


def compare_modes(out: Callable[[str], None] = _say, runs: int = 5) -> int:
    """建置兩種模式並比較大小與啟動時間（記錄在 docs/specs/packaging-ci.md）。"""
    smoke_test = _sibling("smoke_test")
    for mode in ("onedir", "onefile"):
        result = build(mode, out=out)
        timings = smoke_test.measure_startup([str(result.paths.executable)], runs)
        timings_text = ", ".join(f"{t:.2f}" for t in timings)
        out(
            f"{mode}: {result.size_bytes / 1_000_000:.1f} MB; startup to first status "
            f"idle over {runs} runs: {timings_text} s"
        )
    return 0


# ---------------------------------------------------------------- 進入點


def run_default(
    mode: Mode,
    *,
    sidecar_dir: Path | None,
    triple: str | None,
    run_smoke: bool,
    out: Callable[[str], None] = _say,
) -> int:
    """建置（+ 放到 sidecar 目錄、+ 冒煙測試）；回傳結束碼。"""
    try:
        result = build(mode, out=out)
        if sidecar_dir is not None:
            resolved_triple = rust_target_triple(triple) if mode == "onefile" else None
            placed = install_sidecar(result, sidecar_dir, resolved_triple)
            out(f"placed for Tauri: {placed}")
    except (BuildError, OSError) as exc:
        out(f"BUILD FAILED: {exc}")
        return 1
    if run_smoke:
        return smoke(result, out=out)
    return 0


MENU = """
kikitori-engine 建置
  1) 建置 one-folder，並放到 {sidecar}（預設）
  2) 建置 one-folder，並跑冒煙測試
  3) 建置 one-file（Tauri externalBin 命名）
  4) 只跑冒煙測試（使用現有的 one-folder 建置結果）
  5) 比較 one-folder 與 one-file 的大小與啟動時間
  6) 離開
"""


def menu(
    input_fn: Callable[[str], str] = input, out: Callable[[str], None] = _say
) -> int:
    """互動選單（給人用）；回傳結束碼。"""
    while True:
        out(MENU.format(sidecar=DEFAULT_SIDECAR_DIR))
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "1":
            return run_default(
                "onedir",
                sidecar_dir=DEFAULT_SIDECAR_DIR,
                triple=None,
                run_smoke=False,
                out=out,
            )
        if choice == "2":
            return run_default(
                "onedir",
                sidecar_dir=DEFAULT_SIDECAR_DIR,
                triple=None,
                run_smoke=True,
                out=out,
            )
        if choice == "3":
            return run_default(
                "onefile",
                sidecar_dir=DEFAULT_SIDECAR_DIR,
                triple=None,
                run_smoke=False,
                out=out,
            )
        if choice == "4":
            smoke_test = _sibling("smoke_test")
            executable = paths_for("onedir").executable
            return smoke_test.run(
                [str(executable)], self_check_binary=executable, out=out
            )
        if choice == "5":
            try:
                return compare_modes(out=out)
            except (BuildError, OSError) as exc:
                out(f"BUILD FAILED: {exc}")
                return 1
        if choice == "6":
            return 0
        out(f"無效的選項：{choice!r}")


def parse_args(argv: Sequence[str]) -> argparse.Namespace:
    """CI 用的參數。"""
    parser = argparse.ArgumentParser(description="Build the kikitori-engine sidecar")
    parser.add_argument(
        "--onefile", action="store_true", help="one-file build (default: one-folder)"
    )
    target = parser.add_mutually_exclusive_group()
    target.add_argument(
        "--sidecar-dir",
        type=Path,
        default=DEFAULT_SIDECAR_DIR,
        help=f"where Tauri picks the engine up (default: {DEFAULT_SIDECAR_DIR})",
    )
    target.add_argument(
        "--no-sidecar", action="store_true", help="only build into dist/"
    )
    parser.add_argument(
        "--target-triple",
        help=f"Rust target triple (default: rustc host, ${TARGET_TRIPLE_ENV})",
    )
    parser.add_argument(
        "--smoke", action="store_true", help="run the smoke test after building"
    )
    return parser.parse_args(argv)


def main(
    argv: Sequence[str] | None = None, env: Mapping[str, str] | None = None
) -> int:
    """進入點：沒有參數且不是非互動模式時顯示選單。"""
    args_list = list(sys.argv[1:] if argv is None else argv)
    env = os.environ if env is None else env
    if not args_list and env.get(NONINTERACTIVE_ENV, "") != "1":
        return menu()
    args = parse_args(args_list)
    return run_default(
        "onefile" if args.onefile else "onedir",
        sidecar_dir=None if args.no_sidecar else args.sidecar_dir.resolve(),
        triple=args.target_triple,
        run_smoke=args.smoke,
    )


if __name__ == "__main__":
    raise SystemExit(main())
