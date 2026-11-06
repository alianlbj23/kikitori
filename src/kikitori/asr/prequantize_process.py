"""在子行程裡產生衍生檔（轉換＋兩次載入驗證），引擎行程只負責排程、取消與發布。

位於 asr 模組的快取層，是 `derived.DerivedCacheWorker` 預設的產生方式：

- 為什麼要子行程：驗證要再載入兩次模型。在引擎行程裡做完一份後私有記憶體多留約 240 MiB
  （CTranslate2／CUDA 的配置器快取，量測見 `docs/specs/asr.md`），而且 CTranslate2 的載入不能
  中斷；子行程結束時作業系統收回全部記憶體，session 開始時也能直接結束它。
- 協定：引擎寫一行 JSON 請求到子行程的 stdin（之後保持開著：子行程看到 EOF 表示引擎不在了，
  自己結束），子行程以結束碼＋stdout 最後一行 JSON 回報結果；stderr 是子行程的 log，逐行轉進
  引擎的 log。子行程在暫存資料夾裡寫完、驗證、寫完成標記，**發布（rename）在引擎行程**：
  確認完成標記與檔案清單後才 rename。刪除模型（`DerivedCacheWorker.cancel_model()`）會等
  執行中的工作結束（`_running` 清除）才回傳，所以發布不會與刪除交錯；子行程隨時可能被結束，
  由引擎發布就不會有「被結束的子行程發布了一半」的狀態。
- 子行程的指令：開發環境 `python -m kikitori.asr.prequantize_child`；PyInstaller 打包後是引擎
  執行檔本身加隱藏旗標 `--prequantize`（`packaging/engine/entry.py`）。
"""

import json
import logging
import os
import re
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import IO, Any

from kikitori.asr.derived import (
    DerivationCancelled,
    DerivationJob,
    DerivationResult,
    ModelFormatError,
    StopCheck,
    _cleanup_tmp,
    _log_ready,
    _never_stop,
    _plan_folders,
    publish_derived,
)

logger = logging.getLogger(__name__)

PREQUANTIZE_FLAG = "--prequantize"
"""打包的引擎執行檔以這個旗標進入子行程模式（`entry.py` 有同一個字串，測試檢查兩者一致）。"""
CHILD_MODULE = "kikitori.asr.prequantize_child"
REQUEST_VERSION = 1

CHILD_TIMEOUT_SECS = 300.0
"""子行程的時間上限。本機 large-v3（最大的模型）在 GPU 上整個工作 18.8 s；CPU 驗證、慢磁碟或
低階 CPU 可能慢一個數量級，所以給約 16 倍。超過就結束子行程、記為失敗。"""
POLL_SECS = 0.1
"""檢查取消的間隔：session 開始後最多這麼久就結束子行程。"""
MAX_STDOUT_BYTES = 1 << 16
KILL_WAIT_SECS = 10.0
READER_JOIN_SECS = 2.0

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_BAD_REQUEST = 2
EXIT_PARENT_GONE = 3

_LEVEL_PATTERN = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL) ")


class PrequantizeChildError(RuntimeError):
    """子行程失敗：結束碼不是 0、沒有可解析的結果或逾時以外的錯誤（含子行程回報的錯誤）。"""


def child_command() -> list[str]:
    """啟動子行程的指令（不經過 shell）。"""
    if getattr(sys, "frozen", False):
        return [sys.executable, PREQUANTIZE_FLAG]
    return [sys.executable, "-m", CHILD_MODULE]


def _popen_options() -> dict[str, Any]:
    if os.name == "nt":
        # 較低的 CPU 優先權：使用者這時沒在用 ASR，但可能在用其他程式；不開主控台視窗
        flags = subprocess.BELOW_NORMAL_PRIORITY_CLASS | subprocess.CREATE_NO_WINDOW
        return {"creationflags": flags}
    # 自己的行程群組：取消時連同孫行程一起結束
    return {"start_new_session": True}


def kill_process_tree(process: "subprocess.Popen[bytes]") -> None:
    """結束子行程與它的所有子孫行程，並等它結束。

    Windows 的 venv `python.exe` 是啟動器（真正的直譯器是它的子行程），PyInstaller onefile
    也有一層啟動器，所以要結束整棵樹：Windows 用 `taskkill /T /F`，其他平台結束行程群組。
    """
    if process.poll() is None:
        try:
            if os.name == "nt":
                taskkill = Path(os.environ.get("SystemRoot", r"C:\Windows")) / (
                    "System32/taskkill.exe"
                )
                completed = subprocess.run(
                    [os.fspath(taskkill), "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=KILL_WAIT_SECS,
                    check=False,
                    creationflags=subprocess.CREATE_NO_WINDOW,
                )
                if completed.returncode != 0 and process.poll() is None:
                    logger.warning(
                        "taskkill failed (%d): %s",
                        completed.returncode,
                        completed.stderr.decode(errors="replace").strip(),
                    )
                    process.kill()
            else:
                os.killpg(process.pid, signal.SIGKILL)
        except (OSError, subprocess.SubprocessError):
            logger.exception("cannot kill the pre-quantization process tree")
            process.kill()
    try:
        process.wait(KILL_WAIT_SECS)
    except subprocess.TimeoutExpired:
        logger.error("pre-quantization process %d did not exit", process.pid)


def _reap_group(process: "subprocess.Popen[bytes]") -> None:
    """子行程正常結束後，POSIX 上順便結束它留下的子孫（同一個行程群組）。

    Windows 的行程樹在父行程結束後就查不到了（`taskkill /T` 只對活著的行程有效）；
    我們的子行程不會自己再開行程，venv 啟動器在直譯器結束時也會一起結束。
    """
    if os.name == "nt":
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return  # 群組裡已經沒有行程：正常情況
    except OSError as exc:
        logger.warning("cannot clean up the pre-quantization process group: %s", exc)


def _forward_stderr(stream: IO[bytes], tail: list[str]) -> None:
    """子行程的 log 逐行轉進引擎的 log（保留等級）；最後幾行留給錯誤訊息。"""
    level = logging.WARNING  # 沒有等級前綴的輸出（例如原生函式庫印的訊息）當成 warning
    for raw in stream:
        line = raw.decode("utf-8", errors="replace").rstrip()
        if not line:
            continue
        match = _LEVEL_PATTERN.match(line)
        if match:
            level = logging.getLevelName(match.group(1))
            line = line[match.end() :]
        logger.log(level, "[prequantize] %s", line)
        tail.append(line)
        del tail[:-20]


def _read_stdout(stream: IO[bytes], out: bytearray) -> None:
    # 只留尾端：結果是最後一行，前面若有大量雜訊（例如原生函式庫印的訊息）也不會把它擠掉
    # read1：有多少讀多少；read(n) 會等滿 n 位元組或 EOF，子孫握著管線時結果就讀不到
    for chunk in iter(lambda: stream.read1(4096), b""):
        out += chunk
        if len(out) > MAX_STDOUT_BYTES:
            del out[: len(out) - MAX_STDOUT_BYTES]


def run_child(
    request: Mapping[str, Any],
    *,
    should_stop: StopCheck = _never_stop,
    timeout: float = CHILD_TIMEOUT_SECS,
    command: Sequence[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """啟動子行程、送出請求、等結果；回傳子行程回報的結果（`ok` 為 True 的 dict）。

    Raises:
        DerivationCancelled: `should_stop()` 為 True（子行程已被結束）。
        TimeoutError: 超過 `timeout` 秒（子行程已被結束）。
        ModelFormatError: 子行程回報原始檔格式不支援。
        PrequantizeChildError: 子行程回報其他錯誤、結束碼不是 0，或輸出不是預期的 JSON。
        OSError: 無法啟動子行程。
    """
    cmd = list(child_command() if command is None else command)
    process = subprocess.Popen(
        cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=dict(os.environ if env is None else env),
        **_popen_options(),
    )
    assert process.stdin is not None
    assert process.stdout is not None
    assert process.stderr is not None
    stdout = bytearray()
    stderr_tail: list[str] = []
    readers = [
        threading.Thread(
            target=_read_stdout, args=(process.stdout, stdout), daemon=True
        ),
        threading.Thread(
            target=_forward_stderr, args=(process.stderr, stderr_tail), daemon=True
        ),
    ]
    for reader in readers:
        reader.start()
    try:
        try:
            process.stdin.write(json.dumps(dict(request)).encode("utf-8") + b"\n")
            process.stdin.flush()
        except OSError as exc:
            # 子行程一啟動就結束了（例如匯入失敗）；結束碼與 stderr 在下面回報
            logger.warning(
                "cannot send the request to the pre-quantization process: %s", exc
            )
        deadline = time.monotonic() + timeout
        while process.poll() is None:
            if should_stop():
                kill_process_tree(process)
                raise DerivationCancelled("pre-quantization process was stopped")
            if time.monotonic() > deadline:
                kill_process_tree(process)
                raise TimeoutError(
                    f"pre-quantization process did not finish within {timeout:.0f} s"
                )
            time.sleep(POLL_SECS)
    except BaseException:
        if process.poll() is None:
            kill_process_tree(process)
        raise
    finally:
        try:
            process.stdin.close()
        except OSError as exc:
            # 子行程已結束、管線已斷：沒有東西要送了，只留紀錄
            logger.debug("closing the pre-quantization stdin failed: %s", exc)
        _reap_group(process)
        for reader in readers:
            # 子行程結束、管線關閉後讀取立刻結束；等不到表示有子孫握著管線（見下）
            reader.join(READER_JOIN_SECS)
        if any(reader.is_alive() for reader in readers):
            # 還有子孫行程握著管線的寫入端：讀取執行緒卡在讀取中、持有 buffered reader 的鎖，
            # 這時 close() 會跟著卡住。放著讓 daemon 執行緒在管線關閉時自己結束。
            logger.warning(
                "pre-quantization output pipes are still open after the process exited"
            )
        else:
            process.stdout.close()
            process.stderr.close()
    return _parse_result(process.returncode, bytes(stdout), stderr_tail)


def _parse_result(
    returncode: int, stdout: bytes, stderr_tail: Sequence[str]
) -> dict[str, Any]:
    lines = [
        line for line in stdout.decode("utf-8", "replace").splitlines() if line.strip()
    ]
    result: object = None
    if lines:
        try:
            result = json.loads(lines[-1])
        except ValueError:
            result = None
    context = "; ".join(stderr_tail[-3:])
    if not isinstance(result, dict) or not isinstance(result.get("ok"), bool):
        raise PrequantizeChildError(
            f"pre-quantization process exited with {returncode} without a result"
            + (f": {context}" if context else "")
        )
    if result["ok"] and returncode == EXIT_OK:
        return dict(result)
    error_type = str(result.get("error_type", ""))[:100]
    message = str(result.get("error", ""))[:2000]
    if error_type == ModelFormatError.__name__:
        raise ModelFormatError(message)
    raise PrequantizeChildError(
        f"pre-quantization process failed (exit {returncode}): {error_type}: {message}"
    )


def job_request(job: DerivationJob, tmp: Path) -> dict[str, Any]:
    """`job` 的子行程請求（只含 JSON 型別；子行程以 registry 重新建立 spec 並比對鍵）。"""
    return {
        "version": REQUEST_VERSION,
        "action": "derive",
        "model_id": job.spec.id,
        "cache_dir": os.fspath(job.cache_dir),
        "key": job.key.as_dict(),
        "device": job.device,
        "cpu_threads": job.cpu_threads,
        "cuda_library_dirs": list(job.cuda_library_dirs),
        "tmp": os.fspath(tmp),
    }


def _result_number(result: Mapping[str, Any], name: str) -> float:
    value = result.get(name)
    if isinstance(value, bool) or not isinstance(value, int | float) or value < 0:
        raise PrequantizeChildError(
            f"pre-quantization result has an invalid {name}: {value!r}"
        )
    return float(value)


ChildRunner = Callable[..., dict[str, Any]]


def build_derived_in_subprocess(
    job: DerivationJob,
    *,
    should_stop: StopCheck = _never_stop,
    run: ChildRunner = run_child,
) -> DerivationResult:
    """在子行程裡產生並驗證衍生檔，這個行程確認完成標記後發布（`DerivedCacheWorker` 預設）。

    暫存資料夾由這個行程建立並負責清理：子行程失敗、被取消或逾時都刪掉。

    Raises:
        DerivationCancelled: `should_stop()` 為 True（子行程已被結束）。
        TimeoutError、ModelFormatError、PrequantizeChildError: 見 `run_child()`。
        RuntimeError: 子行程回報成功，但暫存資料夾不完整。
        FileNotFoundError: 原始模型沒有下載。
        OSError、UnsafeModelPathError: 建立、發布或清理資料夾失敗。
    """
    existing, tmp = _plan_folders(job)
    if existing is not None:
        return DerivationResult(existing, (existing / "model.bin").stat().st_size, 0, 0)
    tmp.mkdir()
    try:
        result = run(job_request(job, tmp), should_stop=should_stop)
        stats = DerivationResult(
            tmp,
            int(_result_number(result, "model_bin_bytes")),
            _result_number(result, "convert_secs"),
            _result_number(result, "verify_secs"),
        )
        model_bin = tmp / "model.bin"
        actual = model_bin.stat().st_size if model_bin.is_file() else None
        if actual != stats.model_bin_bytes:
            raise PrequantizeChildError(
                f"child reported {stats.model_bin_bytes} bytes but model.bin has {actual}"
            )
        if should_stop():
            raise DerivationCancelled("stopped before publishing")
        final = publish_derived(job, tmp)
        _log_ready(job, stats)
        return DerivationResult(
            final, stats.model_bin_bytes, stats.convert_secs, stats.verify_secs
        )
    finally:
        _cleanup_tmp(tmp)
