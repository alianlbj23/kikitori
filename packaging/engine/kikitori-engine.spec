# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 規格：把 kikitori 引擎打包成 `kikitori-engine`（主控台程式）。

位於打包層，由 `build.py` 呼叫（`python -m PyInstaller kikitori-engine.spec -- <選項>`）。
選項（`--` 之後）：
  --onefile               打成單一執行檔（預設 one-folder）
  --licenses-out <dir>    THIRD_PARTY_LICENSES 的輸出位置（必填）

收集內容與理由見 `docs/specs/packaging-ci.md`。PyInstaller 執行本檔時會注入
`Analysis`、`PYZ`、`EXE`、`COLLECT`、`SPECPATH`、`DISTPATH` 等名稱。
"""

import argparse
import importlib.util
import sys
from pathlib import Path

from PyInstaller.utils.hooks import (
    collect_data_files,
    collect_dynamic_libs,
    collect_submodules,
    copy_metadata,
)

SPEC_DIR = Path(SPECPATH)  # noqa: F821 — PyInstaller 注入
PROJECT_ROOT = SPEC_DIR.parents[1]


def _load(name):
    # 打包目錄名稱 `packaging` 會與 PyPI 的 `packaging` 套件撞名，所以依路徑載入，不走 sys.path。
    spec = importlib.util.spec_from_file_location(f"kikitori_build_{name}", SPEC_DIR / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


policy = _load("policy")
licenses = _load("licenses")

parser = argparse.ArgumentParser(prog="kikitori-engine.spec")
parser.add_argument("--onefile", action="store_true")
parser.add_argument("--licenses-out", required=True, type=Path)
options = parser.parse_args()

hiddenimports = collect_submodules("kikitori")
datas = collect_data_files("kikitori") + copy_metadata("kikitori")
binaries = collect_dynamic_libs("ctranslate2")

if sys.platform == "win32":
    # capture 以 importlib.import_module 延遲載入後端，靜態分析看不到。
    hiddenimports += ["pyaudiowpatch"]
else:
    # soundcard 用 cffi 讀自己目錄下的 *.py.h 標頭檔；libpulse／CoreAudio 在執行時從系統載入。
    hiddenimports += ["soundcard", "_cffi_backend"]
    datas += collect_data_files("soundcard")
    if sys.platform == "darwin":
        hiddenimports += ["soundcard.coreaudio", "objc", "AppKit", "CoreMedia", "ScreenCaptureKit", "dispatch"]
    else:
        hiddenimports += ["soundcard.pulseaudio"]

a = Analysis(  # noqa: F821
    [str(SPEC_DIR / "entry.py")],
    pathex=[str(PROJECT_ROOT / "src")],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=list(policy.EXCLUDED_MODULES),
    noarchive=False,
)

a.binaries, removed_binaries = policy.filter_toc(a.binaries)
a.datas, removed_datas = policy.filter_toc(a.datas)
for dest, *_ in removed_binaries + removed_datas:
    print(f"kikitori-engine.spec: not bundling {dest} (packaging/engine/policy.py)")

pyz = PYZ(a.pure)  # noqa: F821

common = dict(
    name=policy.APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    # UPX 壓縮的 DLL 常被防毒誤判，也會拖慢載入；不用。
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

if options.onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], runtime_tmpdir=None, **common)  # noqa: F821
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)  # noqa: F821
    COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=policy.APP_NAME)  # noqa: F821

sources = [entry[1] for entry in a.pure + a.binaries + a.datas if entry[1]]
report = licenses.collect(
    sources,
    project_root=PROJECT_ROOT,
    overrides_dir=SPEC_DIR / "license_overrides",
    generated_roots=[Path(workpath)],  # noqa: F821 — PyInstaller 注入
)
licenses.write(report, options.licenses_out)
for path in report.unmapped:
    if licenses.system_library_note(path) is None:
        print(f"kikitori-engine.spec: WARNING bundled file with unknown license: {path}")
if report.missing:
    raise SystemExit(
        "kikitori-engine.spec: no license text for bundled components: "
        + ", ".join(report.missing)
        + f" (add them under {SPEC_DIR / 'license_overrides'})"
    )
