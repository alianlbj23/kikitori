"""`kikitori-engine` 執行檔的進入點（PyInstaller 打包用）。

位於打包層，只在 `kikitori-engine.spec` 中當成 PyInstaller 的入口腳本：

- 不帶參數：啟動 stdio 引擎，等同 `kikitori engine`（Tauri 以這種方式啟動 sidecar）。
- `--self-check`：在打包好的執行環境裡確認原生函式庫與資料檔都在、真的載得起來
  （Silero VAD 模型、onnxruntime、CTranslate2、OpenCC、平台音訊後端），把結果以一行
  JSON 寫到 stdout。CI 與 `smoke_test.py` 用它驗證打包內容，不屬於引擎協定。

`kikitori` 的互動選單不放進執行檔：sidecar 只給程式呼叫。
"""

import importlib
import importlib.machinery
import importlib.util
import json
import math
import sys
from collections.abc import Callable, Sequence
from importlib import resources
from pathlib import Path
from typing import Any

USAGE = "usage: kikitori-engine [--self-check]\n  (no arguments: stdio engine)\n"

SELF_CHECK_FLAG = "--self-check"

Check = Callable[[], str]
"""一項檢查：成功時回傳簡短說明，失敗時拋例外。"""


def _check_version() -> str:
    from kikitori.engine.server import engine_version

    version = engine_version()
    # 執行檔沒帶套件 metadata 時 engine_version() 會退回這個值，engine.info 就報錯版本。
    if version.endswith("+unknown"):
        raise RuntimeError("kikitori distribution metadata is missing from the bundle")
    return version


def _check_silero() -> str:
    import numpy as np

    from kikitori.vad.silero import MODEL_VERSION, SileroVad

    license_file = resources.files("kikitori.vad").joinpath("models/LICENSE-silero-vad")
    if not license_file.is_file():
        raise FileNotFoundError("models/LICENSE-silero-vad is missing from the bundle")
    # SileroVad() 讀打包的模型並驗證 SHA-256，所以這裡同時確認了資料檔與 onnxruntime。
    vad = SileroVad()
    silence = np.zeros(512, dtype=np.int16)
    t = np.arange(512, dtype=np.float32) / 16000.0
    tone = (np.sin(2 * np.pi * 220.0 * t) * 8000).astype(np.int16)
    probabilities = [vad.probability(silence) for _ in range(8)]
    probabilities += [vad.probability(tone) for _ in range(8)]
    if not all(math.isfinite(p) and 0.0 <= p <= 1.0 for p in probabilities):
        raise RuntimeError(f"Silero returned invalid probabilities: {probabilities}")
    if probabilities[7] >= 0.5:
        raise RuntimeError(f"Silero says silence is speech (p={probabilities[7]:.3f})")
    return f"silero {MODEL_VERSION}, silence p={probabilities[7]:.4f}"


def _check_onnxruntime() -> str:
    import onnxruntime

    providers = onnxruntime.get_available_providers()
    if "CPUExecutionProvider" not in providers:
        raise RuntimeError(f"CPUExecutionProvider missing: {providers}")
    return f"onnxruntime {onnxruntime.__version__}, providers {providers}"


def _check_ctranslate2() -> str:
    import ctranslate2

    compute_types = sorted(ctranslate2.get_supported_compute_types("cpu"))
    if "int8" not in compute_types:
        raise RuntimeError(f"CTranslate2 CPU compute types lack int8: {compute_types}")
    return f"ctranslate2 {ctranslate2.__version__}, cpu {compute_types}"


def _check_opencc() -> str:
    import opencc

    # 設定檔與字典是資料檔；少了任何一個，建構或轉換就會失敗。
    converted = opencc.OpenCC("s2twp.json").convert("软件")
    if converted != "軟體":
        raise RuntimeError(f"OpenCC s2twp converted '软件' to {converted!r}")
    return "opencc s2twp ok"


def _module_dir(name: str) -> Path:
    spec = importlib.util.find_spec(name)
    if spec is None or not spec.submodule_search_locations:
        raise ModuleNotFoundError(f"package {name!r} is not bundled")
    return Path(next(iter(spec.submodule_search_locations)))


def _check_soundcard(backend_module: str, header: str) -> str:
    # 不匯入 soundcard：它在匯入時就載入 libpulse 並連線 PulseAudio，CI 的 Linux runner
    # 沒有音訊伺服器。importlib.util.find_spec("soundcard.x") 也會先匯入 soundcard，
    # 所以直接用 PathFinder 在套件目錄裡找（打包後由 PyInstaller 的 path hook 查 PYZ）。
    # 只確認模組與 cffi 讀取的標頭檔都有打包進來。
    package = _module_dir("soundcard")
    fullname = f"soundcard.{backend_module}"
    if importlib.machinery.PathFinder.find_spec(fullname, [str(package)]) is None:
        raise ModuleNotFoundError(f"soundcard.{backend_module} is not bundled")
    if not (package / header).is_file():
        raise FileNotFoundError(f"soundcard/{header} is missing from the bundle")
    if importlib.util.find_spec("_cffi_backend") is None:
        raise ModuleNotFoundError("_cffi_backend (needed by soundcard) is not bundled")
    return f"soundcard.{backend_module} + {header}"


def _check_audio_backend(platform: str = sys.platform) -> str:
    if platform == "win32":
        pyaudio = importlib.import_module("pyaudiowpatch")
        return f"pyaudiowpatch {pyaudio.__version__}"
    if platform == "linux":
        return _check_soundcard("pulseaudio", "pulseaudio.py.h")
    if platform == "darwin":
        # ScreenCaptureKit 後端在函式內匯入這些模組；在 macOS 上匯入不需要權限或裝置。
        for name in ("objc", "AppKit", "CoreMedia", "ScreenCaptureKit", "dispatch"):
            importlib.import_module(name)
        return "pyobjc ScreenCaptureKit; " + _check_soundcard(
            "coreaudio", "coreaudio.py.h"
        )
    raise RuntimeError(f"no audio backend for platform {platform!r}")


CHECKS: tuple[tuple[str, Check], ...] = (
    ("version", _check_version),
    ("silero_vad", _check_silero),
    ("onnxruntime", _check_onnxruntime),
    ("ctranslate2", _check_ctranslate2),
    ("opencc", _check_opencc),
    ("audio_backend", _check_audio_backend),
)


def run_self_check(
    checks: Sequence[tuple[str, Check]] = CHECKS,
) -> dict[str, Any]:
    """執行全部檢查並回傳結果；一項失敗不影響其他項，失敗原因放在 `detail`。"""
    results: list[dict[str, Any]] = []
    for name, check in checks:
        try:
            detail = check()
            ok = True
        except Exception as exc:  # noqa: BLE001 - 每項失敗都回報，不讓第一個錯誤蓋掉其他項
            detail = f"{type(exc).__name__}: {exc}"
            ok = False
        results.append({"name": name, "ok": ok, "detail": detail})
    return {
        "ok": all(r["ok"] for r in results),
        "frozen": bool(getattr(sys, "frozen", False)),
        "platform": sys.platform,
        "checks": results,
    }


def _self_check() -> int:
    result = run_self_check()
    sys.stdout.write(json.dumps(result, ensure_ascii=False) + "\n")
    sys.stdout.flush()
    for item in result["checks"]:
        status = "ok  " if item["ok"] else "FAIL"
        sys.stderr.write(f"[{status}] {item['name']}: {item['detail']}\n")
    return 0 if result["ok"] else 1


def _run_engine() -> int:
    from kikitori.cli import main as cli_main

    return cli_main(["engine"])


def main(argv: Sequence[str] | None = None) -> int:
    """解析參數並執行；回傳結束碼（未知參數為 2）。"""
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        return _run_engine()
    if args == [SELF_CHECK_FLAG]:
        return _self_check()
    sys.stderr.write(USAGE)
    return 2


# PyInstaller 的入口腳本以 __main__ 執行；也可用 `python packaging/engine/entry.py` 在原始碼環境試跑。
if __name__ == "__main__":
    raise SystemExit(main())
