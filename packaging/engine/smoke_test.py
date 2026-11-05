"""`kikitori-engine` 的冒煙測試：啟動引擎、以 stdio NDJSON 走一遍協定（假模式）。

位於打包層，給 CI（建置後驗證執行檔）與人工驗證使用，只用標準庫。也可以對開發
環境的引擎（`python -m kikitori engine`）執行，單元測試就是這樣用它的。

檢查項目：
1. （可選）`--self-check`：打包內容的原生函式庫與資料檔（見 `entry.py`）。
2. 假模式（`KIKITORI_ENGINE_FAKE=1`，設定目錄指到暫存目錄）：啟動後的 `status idle`、
   `engine.info`、`devices.list`、`models.list`、`session.start` 產生 `subtitle` 事件並
   以 `status idle` 結束、`engine.shutdown` 後行程以結束碼 0 結束、stdout 每行都是 JSON。
3. （可選）啟動時間：從建立行程到收到第一筆 `status idle` 的時間，跑多次取中位數。

用法：
  python packaging/engine/smoke_test.py                    互動選單
  python packaging/engine/smoke_test.py --binary <exe> [--self-check] [--expect-version X]
  python packaging/engine/smoke_test.py --dev              對 `python -m kikitori engine`
  python packaging/engine/smoke_test.py --binary <exe> --startup-runs 5
"""

import argparse
import json
import os
import queue
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_BINARY = (
    ROOT
    / "dist"
    / "kikitori-engine"
    / ("kikitori-engine.exe" if sys.platform == "win32" else "kikitori-engine")
)

WAIT_SECS = 60.0
"""等待單一訊息的上限。CI 的 macOS runner 第一次啟動執行檔可能很慢（Gatekeeper 掃描）。"""

SELF_CHECK_TIMEOUT = 120.0
FAKE_SPEECH_DEVICE = "fake:loopback:speech"

Message = dict[str, Any]


class SmokeTestError(AssertionError):
    """冒煙測試失敗；訊息說明哪一步、收到了什麼。"""


@dataclass
class EngineClient:
    """一個引擎子行程：送請求、等回應或事件（全部有逾時）。"""

    command: Sequence[str]
    env: Mapping[str, str]
    proc: subprocess.Popen[bytes] = field(init=False)
    messages: list[Message] = field(default_factory=list, init=False)
    bad_lines: list[bytes] = field(default_factory=list, init=False)
    stderr_lines: list[bytes] = field(default_factory=list, init=False)
    started_at: float = field(init=False)

    def __post_init__(self) -> None:
        self._queue: queue.Queue[Message | None] = queue.Queue()
        self._backlog: list[Message] = []
        self._next_id = 1
        self.started_at = time.monotonic()
        self.proc = subprocess.Popen(
            list(self.command),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(self.env),
        )
        # stderr 一定要持續讀走，否則管線塞滿時引擎會卡在寫 log。
        self._readers = [
            threading.Thread(target=self._read_stdout, daemon=True),
            threading.Thread(target=self._read_stderr, daemon=True),
        ]
        for reader in self._readers:
            reader.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            try:
                message = json.loads(line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                self.bad_lines.append(line)
                continue
            if not isinstance(message, dict):
                self.bad_lines.append(line)
                continue
            self.messages.append(message)
            self._queue.put(message)
        self._queue.put(None)

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for line in self.proc.stderr:
            self.stderr_lines.append(line)

    def stderr_tail(self, lines: int = 20) -> str:
        """最後幾行 stderr（失敗訊息用）。"""
        tail = self.stderr_lines[-lines:]
        return b"".join(tail).decode("utf-8", errors="replace")

    def wait_for(
        self,
        predicate: Callable[[Message], bool],
        what: str,
        timeout: float = WAIT_SECS,
    ) -> Message:
        """等到第一筆符合條件的訊息；先前收到但沒被取走的訊息也會檢查。"""
        for index, message in enumerate(self._backlog):
            if predicate(message):
                return self._backlog.pop(index)
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise SmokeTestError(
                    f"timed out after {timeout:.0f}s waiting for {what}; "
                    f"stderr tail:\n{self.stderr_tail()}"
                )
            try:
                message = self._queue.get(timeout=remaining)
            except queue.Empty:
                continue
            if message is None:
                raise SmokeTestError(
                    f"engine stdout closed while waiting for {what} "
                    f"(exit code {self.proc.poll()}); stderr tail:\n{self.stderr_tail()}"
                )
            if predicate(message):
                return message
            self._backlog.append(message)

    def request(self, method: str, params: Mapping[str, Any] | None = None) -> Any:
        """送出請求並回傳 `result`；收到 `error` 時拋 `SmokeTestError`。"""
        request_id = self._next_id
        self._next_id += 1
        payload: Message = {"id": request_id, "method": method}
        if params is not None:
            payload["params"] = dict(params)
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(payload).encode("utf-8") + b"\n")
            self.proc.stdin.flush()
        except OSError as exc:
            raise SmokeTestError(f"cannot send {method}: {exc}") from exc
        response = self.wait_for(
            lambda m: m.get("id") == request_id, f"{method} response"
        )
        if "error" in response:
            raise SmokeTestError(f"{method} failed: {response['error']}")
        return response.get("result")

    def close(self, timeout: float = 10.0) -> int | None:
        """關閉 stdin 並等行程結束；逾時就強制結束。回傳結束碼。"""
        if self.proc.stdin is not None and not self.proc.stdin.closed:
            try:
                self.proc.stdin.close()
            except OSError as exc:
                # 引擎已結束時關閉管線會失敗；不影響結果，但記下來方便除錯。
                print(f"note: closing engine stdin failed: {exc}", file=sys.stderr)
        try:
            code: int | None = self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=timeout)
            code = None
        # 行程結束後讀取執行緒會讀到 EOF；等它們結束再關管線，避免與讀取競爭。
        for reader in self._readers:
            reader.join(timeout=timeout)
        for pipe in (self.proc.stdout, self.proc.stderr):
            if pipe is not None:
                pipe.close()
        return code


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise SmokeTestError(message)


def fake_env(config_dir: str, base: Mapping[str, str] | None = None) -> dict[str, str]:
    """假模式引擎的環境變數：設定檔寫到 `config_dir`，不碰使用者真正的設定。"""
    env = dict(os.environ if base is None else base)
    env["KIKITORI_ENGINE_FAKE"] = "1"
    env["KIKITORI_CONFIG_DIR"] = config_dir
    env["PYTHONUTF8"] = "1"
    return env


def run_self_check(binary: Path, timeout: float = SELF_CHECK_TIMEOUT) -> dict[str, Any]:
    """執行 `<binary> --self-check` 並回傳解析後的結果；任何一項失敗就拋錯。"""
    try:
        completed = subprocess.run(
            [str(binary), "--self-check"],
            capture_output=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise SmokeTestError(f"--self-check timed out after {timeout:.0f}s") from exc
    stderr = completed.stderr.decode("utf-8", errors="replace")
    try:
        result = json.loads(completed.stdout.decode("utf-8").strip().splitlines()[-1])
    except (UnicodeDecodeError, json.JSONDecodeError, IndexError) as exc:
        raise SmokeTestError(
            f"--self-check printed no JSON (exit {completed.returncode}):\n{stderr}"
        ) from exc
    if completed.returncode != 0 or not result.get("ok"):
        failed = [c for c in result.get("checks", []) if not c.get("ok")]
        raise SmokeTestError(f"--self-check failed: {failed}\n{stderr}")
    return result


@dataclass(frozen=True)
class SmokeReport:
    """一次冒煙測試的結果摘要。"""

    startup_secs: float
    info: Mapping[str, Any]
    device_ids: tuple[str, ...]
    asr_models: tuple[str, ...]
    subtitles: int
    final_subtitles: int
    exit_code: int | None


def run_protocol(
    command: Sequence[str],
    *,
    expect_version: str | None = None,
    env: Mapping[str, str] | None = None,
) -> SmokeReport:
    """在假模式下對引擎走一遍協定，失敗時拋 `SmokeTestError`。"""
    with tempfile.TemporaryDirectory(prefix="kikitori-smoke-") as config_dir:
        client = EngineClient(command, fake_env(config_dir, env))
        try:
            client.wait_for(
                lambda m: m.get("event") == "status" and m["data"]["state"] == "idle",
                "initial status idle",
            )
            startup = time.monotonic() - client.started_at

            info = client.request("engine.info")
            _require(info.get("name") == "kikitori-engine", f"engine.info name: {info}")
            _require(info.get("protocol_version") == 1, f"protocol_version: {info}")
            _require(info.get("fake") is True, f"engine is not in fake mode: {info}")
            if expect_version is not None:
                _require(
                    info.get("version") == expect_version,
                    f"engine version {info.get('version')!r} != {expect_version!r}",
                )

            devices = client.request("devices.list")["devices"]
            device_ids = tuple(d["id"] for d in devices)
            _require(FAKE_SPEECH_DEVICE in device_ids, f"devices.list: {devices}")

            models = client.request("models.list")
            asr = tuple(m["id"] for m in models["asr"])
            _require(bool(asr), f"models.list returned no ASR models: {models}")
            _require(
                "translation" in models, f"models.list lacks translation: {models}"
            )

            started = client.request(
                "session.start", {"mode": "media", "device_id": FAKE_SPEECH_DEVICE}
            )
            session_id = started["session_id"]
            client.wait_for(
                lambda m: (
                    m.get("event") == "status"
                    and m["data"]["state"] == "idle"
                    and m["data"].get("session_id") == session_id
                ),
                "session to finish (status idle)",
            )
            subtitles = [
                m["data"]
                for m in client.messages
                if m.get("event") == "subtitle"
                and m["data"].get("session_id") == session_id
            ]
            finals = [s for s in subtitles if s["is_final"]]
            # 沒有字幕時只看字幕清單無從判斷原因；附上 status／log 事件與 stderr。
            events = [m for m in client.messages if m.get("event") in ("status", "log")]
            _require(
                bool(finals),
                f"no final subtitle events; got {subtitles}\nevents: {events}\n"
                f"stderr tail:\n{client.stderr_tail()}",
            )
            _require(
                all(s["translation"] for s in finals),
                f"final subtitle without translation: {finals}",
            )
            errors = [
                m
                for m in client.messages
                if m.get("event") == "status" and m["data"]["state"] == "error"
            ]
            _require(not errors, f"engine reported errors: {errors}")

            _require(client.request("engine.shutdown") == {}, "engine.shutdown result")
            try:
                exit_code = client.proc.wait(timeout=WAIT_SECS)
            except subprocess.TimeoutExpired as exc:
                raise SmokeTestError(
                    "engine did not exit after engine.shutdown"
                ) from exc
            _require(exit_code == 0, f"engine exit code {exit_code}")
            _require(not client.bad_lines, f"non-JSON stdout lines: {client.bad_lines}")
            return SmokeReport(
                startup_secs=startup,
                info=info,
                device_ids=device_ids,
                asr_models=asr,
                subtitles=len(subtitles),
                final_subtitles=len(finals),
                exit_code=exit_code,
            )
        finally:
            client.close()


def measure_startup(command: Sequence[str], runs: int) -> list[float]:
    """量 `runs` 次「建立行程 → 第一筆 status idle」的秒數（假模式，之後送 shutdown）。"""
    if not isinstance(runs, int) or isinstance(runs, bool) or not 1 <= runs <= 50:
        raise ValueError(f"runs must be an int in 1..50, got {runs!r}")
    timings: list[float] = []
    for _ in range(runs):
        with tempfile.TemporaryDirectory(prefix="kikitori-startup-") as config_dir:
            client = EngineClient(command, fake_env(config_dir))
            try:
                client.wait_for(
                    lambda m: (
                        m.get("event") == "status" and m["data"]["state"] == "idle"
                    ),
                    "initial status idle",
                )
                timings.append(time.monotonic() - client.started_at)
                client.request("engine.shutdown")
                client.proc.wait(timeout=WAIT_SECS)
            finally:
                client.close()
    return timings


def dev_command() -> list[str]:
    """開發環境的引擎指令（目前的直譯器 + `-m kikitori engine`）。"""
    return [sys.executable, "-m", "kikitori", "engine"]


def run(
    command: Sequence[str],
    *,
    self_check_binary: Path | None = None,
    expect_version: str | None = None,
    startup_runs: int = 0,
    out: Callable[[str], None] = print,
) -> int:
    """執行冒煙測試並印出摘要；回傳結束碼（0 成功、1 失敗）。"""
    try:
        if self_check_binary is not None:
            result = run_self_check(self_check_binary)
            for item in result["checks"]:
                out(f"self-check {item['name']}: {item['detail']}")
        report = run_protocol(command, expect_version=expect_version)
        out(
            f"protocol ok: version {report.info['version']} "
            f"({report.info['platform']}/{report.info['machine']}), "
            f"{len(report.device_ids)} devices, {len(report.asr_models)} ASR models, "
            f"{report.subtitles} subtitle events ({report.final_subtitles} final), "
            f"startup {report.startup_secs:.2f}s, exit {report.exit_code}"
        )
        if startup_runs:
            timings = measure_startup(command, startup_runs)
            out(
                f"startup over {len(timings)} runs: median "
                f"{statistics.median(timings):.2f}s, min {min(timings):.2f}s, "
                f"max {max(timings):.2f}s"
            )
    except SmokeTestError as exc:
        out(f"SMOKE TEST FAILED: {exc}")
        return 1
    except OSError as exc:
        out(f"SMOKE TEST FAILED: cannot run {list(command)}: {exc}")
        return 1
    return 0


MENU = """
kikitori-engine 冒煙測試
  1) 測試建置好的執行檔（{binary}）
  2) 測試開發環境的引擎（python -m kikitori engine）
  3) 量建置好的執行檔啟動時間（5 次）
  4) 離開
"""


def menu(
    input_fn: Callable[[str], str] = input, out: Callable[[str], None] = print
) -> int:
    """互動選單（給人用）；回傳結束碼。"""
    while True:
        out(MENU.format(binary=DEFAULT_BINARY))
        try:
            choice = input_fn("請輸入編號：").strip()
        except (EOFError, KeyboardInterrupt):
            return 0
        if choice == "1":
            return run([str(DEFAULT_BINARY)], self_check_binary=DEFAULT_BINARY, out=out)
        if choice == "2":
            return run(dev_command(), out=out)
        if choice == "3":
            return run([str(DEFAULT_BINARY)], startup_runs=5, out=out)
        if choice == "4":
            return 0
        out(f"無效的選項：{choice!r}")


def main(argv: Sequence[str] | None = None) -> int:
    """命令列進入點（CI 用參數；沒有參數時顯示選單）。"""
    args_list = list(sys.argv[1:] if argv is None else argv)
    if not args_list:
        return menu()
    parser = argparse.ArgumentParser(description="kikitori-engine smoke test")
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--binary", type=Path, help="built kikitori-engine executable")
    target.add_argument(
        "--dev", action="store_true", help="run python -m kikitori engine"
    )
    parser.add_argument(
        "--self-check", action="store_true", help="also run --self-check"
    )
    parser.add_argument("--expect-version", help="fail unless engine.info reports this")
    parser.add_argument(
        "--startup-runs", type=int, default=0, help="measure startup N times"
    )
    args = parser.parse_args(args_list)
    if args.dev:
        if args.self_check:
            parser.error("--self-check needs --binary")
        command = dev_command()
        binary = None
    else:
        binary = args.binary.resolve()
        if not binary.is_file():
            parser.error(f"not a file: {binary}")
        command = [str(binary)]
    return run(
        command,
        self_check_binary=binary if args.self_check else None,
        expect_version=args.expect_version,
        startup_runs=args.startup_runs,
    )


if __name__ == "__main__":
    raise SystemExit(main())
