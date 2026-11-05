"""`kikitori-engine` 打包規則：名稱、排除的模組，以及絕對不能打包進去的函式庫。

位於打包層，由 `kikitori-engine.spec`（建置時過濾）與 `build.py`（建置後再掃一次輸出
目錄）共用，確保兩邊用同一份規則。只用標準庫。

不能打包的函式庫：
- NVIDIA CUDA／cuDNN 等（專有授權、體積大）：GPU 執行環境由使用者在 app 內另外下載，
  見 `docs/specs/hardware.md`。CTranslate2 的 Windows wheel 附 `cudnn64_9.dll`，
  Whisper 用不到（`docs/specs/asr.md`），建置時拿掉。
- libpulse（LGPL）：soundcard 以 cffi 在執行時從系統載入，不能隨程式散布。
"""

import fnmatch
from collections.abc import Iterable
from pathlib import Path, PurePath

APP_NAME = "kikitori-engine"
"""執行檔與 one-folder 目錄的名稱（Tauri sidecar 的基本名稱）。"""

LICENSES_DIR_NAME = "THIRD_PARTY_LICENSES"

FORBIDDEN_PATTERNS: tuple[str, ...] = (
    # NVIDIA CUDA runtime／math libraries（Windows DLL 與 Linux .so）
    "cudnn*",
    "libcudnn*",
    "cublas*",
    "libcublas*",
    "cudart*",
    "libcudart*",
    "cufft*",
    "libcufft*",
    "curand*",
    "libcurand*",
    "cusolver*",
    "libcusolver*",
    "cusparse*",
    "libcusparse*",
    "nvrtc*",
    "libnvrtc*",
    "nvjitlink*",
    "libnvjitlink*",
    "nccl*",
    "libnccl*",
    "nvcuda*",
    "libcuda.so*",
    "*onnxruntime_providers_cuda*",
    "*onnxruntime_providers_tensorrt*",
    # PulseAudio client libraries (LGPL)
    "libpulse*",
)
"""以檔名（不分大小寫）比對；任何一個符合就不能出現在打包結果裡。"""

SYSTEM_PROVIDED_PATTERNS: tuple[str, ...] = (
    # Universal C Runtime：Windows 10 起是作業系統的一部分（Tauri v2 也只支援 Windows 10+）。
    # PyInstaller 會從 PATH 上找到的任何副本收集（例如 Windows SDK 的工具目錄），
    # 結果隨建置機器而變，所以一律不打包、用系統的。
    "api-ms-win-*.dll",
    "ucrtbase.dll",
)
"""作業系統本身就有、不應打包的檔案（以檔名比對，不分大小寫）。"""

EXCLUDED_MODULES: tuple[str, ...] = (
    # 只用來轉換模型、會拉進 torch／transformers；推論不需要（ctranslate2 已改成延遲匯入）。
    "ctranslate2.converters",
    "ctranslate2.specs",
    # 引擎沒有 GUI。
    "tkinter",
    "_tkinter",
    # 只出現在實驗或開發環境，不能被意外打包。
    "pipecat",
    "torch",
    "transformers",
    "PyInstaller",
)


def _base_name(name: str | PurePath) -> str:
    # PyInstaller 在 Windows 上的 TOC 名稱用反斜線；在其他平台檢查時也要能取出檔名。
    return str(name).replace("\\", "/").rsplit("/", 1)[-1].lower()


def is_forbidden(name: str | PurePath) -> bool:
    """這個檔案（以檔名判斷）是否禁止打包。"""
    base = _base_name(name)
    return any(fnmatch.fnmatchcase(base, pattern) for pattern in FORBIDDEN_PATTERNS)


def is_system_provided(name: str | PurePath) -> bool:
    """這個檔案是否由作業系統提供、不需要打包。"""
    base = _base_name(name)
    return any(fnmatch.fnmatchcase(base, p) for p in SYSTEM_PROVIDED_PATTERNS)


def filter_toc[T: tuple[object, ...]](entries: Iterable[T]) -> tuple[list[T], list[T]]:
    """把 PyInstaller TOC（`(dest_name, src_name, typecode)`）分成保留與移除兩組。

    移除禁止打包的檔案與作業系統提供的檔案。
    """
    kept: list[T] = []
    removed: list[T] = []
    for entry in entries:
        dest = str(entry[0])
        drop = is_forbidden(dest) or is_system_provided(dest)
        (removed if drop else kept).append(entry)
    return kept, removed


def find_forbidden(root: Path) -> list[Path]:
    """列出 `root` 底下所有禁止打包的檔案（建置後的最後防線）。"""
    if root.is_file():
        return [root] if is_forbidden(root) else []
    return sorted(p for p in root.rglob("*") if p.is_file() and is_forbidden(p))
