"""衍生檔子行程的進入點：從 stdin 讀一行 JSON 請求，轉換並驗證，stdout 最後一行回報結果。

位於 asr 模組的快取層，由 `prequantize_process.run_child()` 啟動（開發環境
`python -m kikitori.asr.prequantize_child`，打包後是引擎執行檔加 `--prequantize`）。
只寫暫存資料夾、不發布；發布、取消與失敗標記都在引擎行程（理由見 `prequantize_process`）。

請求（`action`）：
- `derive`：`prequantize_process.job_request()` 的欄位；模型以 registry 的 id 重新查出，鍵必須與
  引擎算的完全相同，暫存資料夾必須是那個模型 `derived/` 底下的 `.tmp-*` 空資料夾。
- `self_test`：不需要模型；在暫存目錄合成一個小 `model.bin` 跑一次轉換與讀檔
  （`entry.py --self-check` 用它確認打包後這條路徑能跑）。

結束碼：0 成功、1 工作失敗（stdout 有 `{"ok": false, "error_type", "error"}`）、2 請求不合法、
3 引擎不在了（stdin 收到 EOF）。log 以 `等級 名稱: 訊息` 寫到 stderr，引擎逐行轉進自己的 log。
"""

import json
import logging
import os
import struct
import sys
import tempfile
import threading
from collections.abc import Mapping
from pathlib import Path
from typing import IO, Any

import numpy as np

from kikitori.asr import derived
from kikitori.asr.prequantize_process import (
    EXIT_BAD_REQUEST,
    EXIT_FAILED,
    EXIT_OK,
    EXIT_PARENT_GONE,
    REQUEST_VERSION,
)

# 不用 __name__：以 `python -m` 啟動時它是 "__main__"，引擎的 log 裡看不出是誰
logger = logging.getLogger("kikitori.asr.prequantize_child")

MAX_REQUEST_BYTES = 1 << 16
MAX_CPU_THREADS = 256
MAX_CUDA_DIRS = 16


class BadRequest(ValueError):
    """請求的格式或內容不合法。"""


def _string(request: Mapping[str, Any], name: str, limit: int = 4096) -> str:
    value = request.get(name)
    if not isinstance(value, str) or not value or len(value) > limit or "\0" in value:
        raise BadRequest(f"{name} must be a non-empty string")
    return value


def parse_derive_request(request: Mapping[str, Any]) -> derived.DerivationJob:
    """把 `derive` 請求轉成工作；不合法時 `BadRequest`。

    Raises:
        BadRequest: 欄位型別或範圍不對、模型不在 registry、鍵與這個行程算出的不同，
            或暫存資料夾不是那個模型的 `derived/.tmp-*`。
    """
    from kikitori.asr.registry import get_model

    model_id = _string(request, "model_id", 100)
    try:
        spec = get_model(model_id)
    except KeyError:
        raise BadRequest(f"unknown model {model_id!r}") from None
    sent_key = request.get("key")
    compute_type = sent_key.get("compute_type") if isinstance(sent_key, dict) else None
    if not isinstance(compute_type, str):
        raise BadRequest("key.compute_type must be a string")
    try:
        key = derived.derived_key(spec, compute_type)
    except (TypeError, ValueError) as exc:
        raise BadRequest(f"cannot compute the key: {exc}") from exc
    if key is None or key.as_dict() != request.get("key"):
        raise BadRequest(
            f"key mismatch: engine sent {request.get('key')!r}, "
            f"this process computes {None if key is None else key.as_dict()!r}"
        )
    device = request.get("device")
    if device not in ("cpu", "cuda"):
        raise BadRequest(f"device must be cpu or cuda, got {device!r}")
    threads = request.get("cpu_threads")
    if (
        isinstance(threads, bool)
        or not isinstance(threads, int)
        or not (0 <= threads <= MAX_CPU_THREADS)
    ):
        raise BadRequest(f"cpu_threads must be in [0, {MAX_CPU_THREADS}]")
    dirs = request.get("cuda_library_dirs")
    if (
        not isinstance(dirs, list)
        or len(dirs) > MAX_CUDA_DIRS
        or not all(isinstance(d, str) and d and "\0" not in d for d in dirs)
    ):
        raise BadRequest("cuda_library_dirs must be a short list of paths")
    cache_dir = Path(_string(request, "cache_dir"))
    tmp = Path(_string(request, "tmp"))
    root = derived.derived_root(spec, cache_dir)
    _check_tmp(tmp, root)
    return derived.DerivationJob(
        spec, cache_dir, key, device, threads, tuple(str(d) for d in dirs)
    )


def _check_tmp(tmp: Path, root: Path) -> None:
    """子行程只寫入 `root`（該模型的 `derived/`）正下方、名稱 `.tmp-*` 的空資料夾。

    解析連結後比對（`..` 片段、symlink、junction 都不能把寫入帶到別處），`root` 與 `tmp`
    本身都不能是連結。

    Raises:
        BadRequest: 不符合上述條件。
    """
    problem = f"tmp must be an empty {derived.TMP_PREFIX}* folder directly in {root}"
    if ".." in tmp.parts or not tmp.name.startswith(derived.TMP_PREFIX):
        raise BadRequest(problem)
    try:
        if derived._is_link(root) or derived._is_link(tmp) or not tmp.is_dir():
            raise BadRequest(problem)
        if tmp.resolve(strict=True).parent != root.resolve(strict=True):
            raise BadRequest(problem)
        if any(tmp.iterdir()):
            raise BadRequest(f"{problem} (it is not empty)")
    except OSError as exc:
        raise BadRequest(f"{problem}: {exc}") from exc


def run_derive(job: derived.DerivationJob, tmp: Path) -> dict[str, Any]:
    """產生並驗證（不發布），回傳結果欄位。"""
    if job.device == "cuda" and job.cuda_library_dirs:
        from kikitori.asr.whisper import preload_cuda_libraries

        preload_cuda_libraries([Path(d) for d in job.cuda_library_dirs])
    result = derived.prepare_derived(job, tmp)
    return {
        "model_bin_bytes": result.model_bin_bytes,
        "convert_secs": result.convert_secs,
        "verify_secs": result.verify_secs,
    }


def run_self_test() -> dict[str, Any]:
    """合成一個小 `model.bin`（一個 float16 權重＋別名），轉換後讀回並檢查量化結果。

    Raises:
        RuntimeError: 轉換結果不對。
    """
    with tempfile.TemporaryDirectory(prefix="kikitori-prequantize-") as folder:
        source = Path(folder) / "source.bin"
        weight = np.array([[1.0, -0.5, 0.25, 0.0], [0.0, 0.0, 0.0, 0.0]], np.float16)
        with source.open("wb") as file:
            writer = derived._HashingWriter(file)
            writer.write(struct.pack("<I", derived.SUPPORTED_BINARY_VERSION))
            writer.string("WhisperSpec")
            writer.write(struct.pack("<I", derived.SUPPORTED_SPECS["WhisperSpec"]))
            writer.write(struct.pack("<I", 1))
            writer.header("decoder/embeddings/weight", weight.shape, 4, weight.nbytes)
            writer.write(weight.tobytes())
            writer.write(struct.pack("<I", 1))
            writer.string("decoder/projection/weight")
            writer.string("decoder/embeddings/weight")
        out = Path(folder) / "model.bin"
        stats = derived.write_quantized_model_bin(source, out)
        index = derived.read_model_index(out)
        names = [v.name for v in index.variables]
        if names != ["decoder/embeddings/weight", "decoder/embeddings/weight_scale"]:
            raise RuntimeError(f"unexpected variables after conversion: {names}")
        quantized = index.variables[0]
        with out.open("rb") as file:
            file.seek(quantized.offset)
            values = np.frombuffer(file.read(quantized.num_bytes), np.int8)
        if values.tolist() != [127, -64, 32, 0, 0, 0, 0, 0]:
            raise RuntimeError(f"unexpected quantized values: {values.tolist()}")
        return {"model_bin_bytes": stats.size, "convert_secs": 0.0, "verify_secs": 0.0}


def _watch_parent(stdin: IO[bytes]) -> None:
    """引擎保持 stdin 開著；讀到 EOF 表示引擎結束了（含當掉），子行程跟著結束。

    直接讀檔案描述元（`os.read`），不經過 `BufferedReader`：這條 daemon 執行緒在直譯器結束時
    仍卡在讀取中，若持有 buffered reader 的鎖，結束時會 `Fatal Python error: _enter_buffered_busy`
    （實測結束碼 0xC0000005）。
    """
    # 前提：引擎只送一行請求。readline() 可能已把之後的位元組讀進 buffer，這裡改讀 fd
    # 不會看到它們——協定改成多行時要一起改。
    try:
        fd = stdin.fileno()
        while os.read(fd, 4096):
            pass  # 引擎在請求之後不會再送東西；只等 EOF
    except OSError as exc:
        logger.warning("stdin failed: %s", exc)
    logger.warning("the engine closed stdin; stopping pre-quantization")
    os._exit(EXIT_PARENT_GONE)


def _emit(stdout: IO[str], result: Mapping[str, Any]) -> None:
    stdout.write(json.dumps(dict(result)) + "\n")
    stdout.flush()


def main(
    stdin: IO[bytes] | None = None,
    stdout: IO[str] | None = None,
    *,
    watch_parent: bool = True,
) -> int:
    """讀一行請求、執行、回報；回傳結束碼。"""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(levelname)s %(name)s: %(message)s",
    )
    source = sys.stdin.buffer if stdin is None else stdin
    out = sys.stdout if stdout is None else stdout
    try:
        line = source.readline(MAX_REQUEST_BYTES + 1)
        if not line.endswith(b"\n") or len(line) > MAX_REQUEST_BYTES:
            raise BadRequest("expected one JSON line of at most 64 KiB on stdin")
        try:
            request = json.loads(line)
        except ValueError as exc:
            raise BadRequest(f"request is not JSON: {exc}") from None
        if not isinstance(request, dict) or request.get("version") != REQUEST_VERSION:
            raise BadRequest(
                f"request must be an object with version {REQUEST_VERSION}"
            )
        action = request.get("action")
        if action == "self_test":
            result = run_self_test()
        elif action == "derive":
            job = parse_derive_request(request)
            if watch_parent:
                threading.Thread(
                    target=_watch_parent,
                    args=(source,),
                    name="watch-engine",
                    daemon=True,
                ).start()
            result = run_derive(job, Path(request["tmp"]))
        else:
            raise BadRequest(f"unknown action {action!r}")
    except BadRequest as exc:
        logger.error("bad pre-quantization request: %s", exc)
        _emit(out, {"ok": False, "error_type": "BadRequest", "error": str(exc)})
        return EXIT_BAD_REQUEST
    except Exception as exc:
        logger.exception("pre-quantization failed")
        _emit(out, {"ok": False, "error_type": type(exc).__name__, "error": str(exc)})
        return EXIT_FAILED
    _emit(out, {"ok": True, **result})
    return EXIT_OK


def run() -> int:
    """子行程的進入點（`python -m` 與打包後的 `--prequantize`）：降低優先權後執行 `main()`。"""
    if hasattr(os, "nice"):
        os.nice(10)  # Windows 由引擎以 BELOW_NORMAL_PRIORITY_CLASS 啟動
    return main()


if __name__ == "__main__":
    raise SystemExit(run())
