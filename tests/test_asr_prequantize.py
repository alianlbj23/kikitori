"""衍生快取的子行程（`asr/prequantize_process.py`、`asr/prequantize_child.py`）。

協定與行程管理用小的假子行程（`python -c ...`）測：結果解析、stderr 轉進 log、錯誤、逾時、
取消時結束整棵行程樹。子行程的請求驗證在同一個行程裡呼叫 `prequantize_child.main()`。
本機快取有 `tiny` 時，用真的子行程指令（`python -m kikitori.asr.prequantize_child`）跑完整流程。
"""

import ctypes
import io
import json
import os
import shutil
import signal
import sys
import tempfile
import textwrap
import time
import unittest
from pathlib import Path
from typing import Any
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_asr_derived import (
    CacheLayoutCase,
    FakeProbeModel,
    _user_tiny,
)

from kikitori.asr import derived, prequantize_child
from kikitori.asr import prequantize_process as pp
from kikitori.asr.derived import (
    DerivationCancelled,
    DerivationJob,
    ModelFormatError,
    derived_key,
    find_derived,
)
from kikitori.asr.download import MARKER_NAME
from kikitori.asr.registry import ModelFile, get_model

WAIT = 15.0


def fake_child(code: str) -> list[str]:
    """以 `python -c` 執行的假子行程（讀掉一行請求後執行 `code`）。"""
    prelude = "import sys, json, time, os, subprocess\nrequest = sys.stdin.readline()\n"
    return [sys.executable, "-c", prelude + textwrap.dedent(code)]


def pid_alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        return True
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(
        0x1000, False, pid
    )  # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        kernel32.GetExitCodeProcess(ctypes.c_void_p(handle), ctypes.byref(code))
        return code.value == 259  # STILL_ACTIVE
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


class RunChildTest(unittest.TestCase):
    def test_success_forwards_stderr_with_levels_and_parses_last_line(self) -> None:
        command = fake_child(
            """
            assert json.loads(request)["action"] == "x"
            sys.stderr.write("INFO kikitori.asr.derived: hello\\n")
            sys.stderr.write("WARNING kikitori.asr.derived: careful\\n")
            sys.stderr.write("plain native output\\n")
            print("some noise")
            print(json.dumps({"ok": True, "model_bin_bytes": 5}))
            """
        )
        with self.assertLogs("kikitori.asr.prequantize_process", "INFO") as logs:
            result = pp.run_child({"action": "x"}, command=command, timeout=WAIT)
        self.assertEqual(result, {"ok": True, "model_bin_bytes": 5})
        self.assertIn(
            "INFO:kikitori.asr.prequantize_process:[prequantize] kikitori.asr.derived: hello",
            logs.output,
        )
        self.assertIn(
            "WARNING:kikitori.asr.prequantize_process:[prequantize] kikitori.asr.derived: careful",
            logs.output,
        )
        # 沒有等級前綴的行沿用上一行的等級
        self.assertIn(
            "WARNING:kikitori.asr.prequantize_process:[prequantize] plain native output",
            logs.output,
        )

    def test_reported_errors(self) -> None:
        failing = """
            print(json.dumps({"ok": False, "error_type": "%s", "error": "boom"}))
            sys.exit(1)
            """
        with self.assertRaisesRegex(ModelFormatError, "boom"):
            pp.run_child(
                {}, command=fake_child(failing % "ModelFormatError"), timeout=WAIT
            )
        with self.assertRaisesRegex(pp.PrequantizeChildError, "RuntimeError: boom"):
            pp.run_child({}, command=fake_child(failing % "RuntimeError"), timeout=WAIT)

    def test_missing_or_inconsistent_result(self) -> None:
        cases = {
            "no json": 'print("done")',
            "crash": 'sys.stderr.write("Traceback: kaboom\\n"); sys.exit(5)',
            "ok but exit 1": 'print(json.dumps({"ok": True})); sys.exit(1)',
            "not an object": "print(json.dumps([1]))",
        }
        for label, code in cases.items():
            with (
                self.subTest(label),
                self.assertLogs("kikitori.asr.prequantize_process", "DEBUG"),
                self.assertRaises(pp.PrequantizeChildError) as caught,
            ):
                # assertLogs 需要至少一筆 log：先記一筆
                pp.logger.debug("case %s", label)
                pp.run_child({}, command=fake_child(code), timeout=WAIT)
            if label == "crash":
                self.assertIn("kaboom", str(caught.exception))

    def test_timeout_kills_the_child(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "child.pid"
            code = f"open({os.fspath(pid_file)!r}, 'w').write(str(os.getpid())); time.sleep(60)"
            started = time.monotonic()
            with self.assertRaises(TimeoutError):
                pp.run_child({}, command=fake_child(code), timeout=2.0)
            self.assertLess(time.monotonic() - started, WAIT)
            self.assertFalse(pid_alive(int(pid_file.read_text())))

    def test_result_after_lots_of_stdout_noise(self) -> None:
        code = f"""
            sys.stdout.write("x" * {pp.MAX_STDOUT_BYTES * 3} + "\\n")
            print(json.dumps({{"ok": True, "model_bin_bytes": 1}}))
            """
        result = pp.run_child({}, command=fake_child(code), timeout=WAIT)
        self.assertEqual(result["model_bin_bytes"], 1)

    def test_returns_although_a_leftover_grandchild_holds_the_pipes(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild.pid"
            # 孫行程繼承 stdout／stderr 後繼續睡；子行程印出結果就結束
            code = f"""
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
                open({os.fspath(pid_file)!r}, "w").write(str(child.pid))
                print(json.dumps({{"ok": True}}), flush=True)
                """
            started = time.monotonic()
            try:
                with self.assertLogs("kikitori.asr.prequantize_process", "WARNING"):
                    result = pp.run_child({}, command=fake_child(code), timeout=WAIT)
                self.assertEqual(result, {"ok": True})
                self.assertLess(time.monotonic() - started, WAIT)
            finally:
                if pid_file.exists():
                    grandchild = int(pid_file.read_text())
                    if pid_alive(grandchild):
                        os.kill(grandchild, signal.SIGTERM)

    def test_cancel_kills_the_whole_tree(self) -> None:
        with tempfile.TemporaryDirectory() as folder:
            pid_file = Path(folder) / "grandchild.pid"
            command = fake_child(
                f"""
                child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
                open({os.fspath(pid_file)!r}, "w").write(str(child.pid))
                time.sleep(60)
                """
            )
            with self.assertRaises(DerivationCancelled):
                pp.run_child(
                    {},
                    command=command,
                    timeout=WAIT * 4,
                    should_stop=lambda: (
                        pid_file.exists() and pid_file.stat().st_size > 0
                    ),
                )
            grandchild = int(pid_file.read_text())
            deadline = time.monotonic() + WAIT
            while pid_alive(grandchild) and time.monotonic() < deadline:
                time.sleep(0.1)
            self.assertFalse(pid_alive(grandchild))

    def test_command_and_flag(self) -> None:
        self.assertEqual(pp.child_command()[1:], ["-m", pp.CHILD_MODULE])
        with mock.patch.object(sys, "frozen", True, create=True):
            self.assertEqual(pp.child_command(), [sys.executable, "--prequantize"])


class BuildInSubprocessTest(CacheLayoutCase):
    """`build_derived_in_subprocess()`：子行程以假的 runner 代替，檢查發布與清理。"""

    def runner(
        self, *, write: bool = True, result: dict[str, Any] | None = None
    ) -> Any:
        def run(request: dict[str, Any], *, should_stop: Any) -> dict[str, Any]:
            self.requests.append(request)
            tmp = Path(request["tmp"])
            self.assertTrue(tmp.is_dir())
            size = 10
            if not write:  # 子行程回報成功，但只寫了 model.bin（沒有標記與其他檔案）
                (tmp / "model.bin").write_bytes(b"x" * size)
            else:
                size = derived.prepare_derived(
                    self.job, tmp, create_model=self.factory(), storage=lambda a: a
                ).model_bin_bytes
            return result or {
                "ok": True,
                "model_bin_bytes": size,
                "convert_secs": 0.1,
                "verify_secs": 0.2,
            }

        self.requests: list[dict[str, Any]] = []
        return run

    def test_parent_publishes_after_checking_the_folder(self) -> None:
        result = pp.build_derived_in_subprocess(self.job, run=self.runner())
        self.assertEqual(result.path, find_derived(self.spec, self.cache, self.key))
        self.assertEqual(self.leftovers(), [self.key.dirname])
        (request,) = self.requests
        self.assertEqual(request["key"], self.key.as_dict())
        self.assertEqual(request["action"], "derive")
        json.dumps(request)  # 只有 JSON 型別

    def test_incomplete_folder_is_not_published(self) -> None:
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            pp.build_derived_in_subprocess(self.job, run=self.runner(write=False))
        self.assertEqual(self.leftovers(), [])

    def test_invalid_numbers_are_rejected(self) -> None:
        bad = {"ok": True, "model_bin_bytes": "10", "convert_secs": 0, "verify_secs": 0}
        with self.assertRaises(pp.PrequantizeChildError):
            pp.build_derived_in_subprocess(self.job, run=self.runner(result=bad))
        self.assertEqual(self.leftovers(), [])

    def test_cancel_and_errors_remove_the_temp_folder(self) -> None:
        for error in (
            DerivationCancelled("x"),
            TimeoutError("slow"),
            ModelFormatError("v"),
        ):

            def run(
                request: dict[str, Any], *, should_stop: Any, error: Exception = error
            ) -> Any:
                (Path(request["tmp"]) / "model.bin").write_bytes(b"partial")
                raise error

            with self.subTest(type(error).__name__), self.assertRaises(type(error)):
                pp.build_derived_in_subprocess(self.job, run=run)
            self.assertEqual(self.leftovers(), [])

    def test_existing_copy_is_reused_without_a_child(self) -> None:
        pp.build_derived_in_subprocess(self.job, run=self.runner())
        self.requests.clear()
        pp.build_derived_in_subprocess(self.job, run=self.runner())
        self.assertEqual(self.requests, [])


class ChildMainTest(CacheLayoutCase):
    """`prequantize_child.main()` 在同一個行程裡跑：請求驗證、自我測試、回報格式。"""

    def setUp(self) -> None:
        super().setUp()
        patcher = mock.patch("kikitori.asr.registry.get_model", self.get_model)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.key = derived_key(self.spec, "int8_float16")
        assert self.key is not None
        self.job = DerivationJob(self.spec, self.cache, self.key, "cpu")
        root = derived.derived_root(self.spec, self.cache)
        root.mkdir(parents=True, exist_ok=True)
        self.tmp = root / f"{derived.TMP_PREFIX}test"
        self.tmp.mkdir()

    def get_model(self, model_id: str) -> Any:
        if model_id != self.spec.id:
            raise KeyError(model_id)
        return self.spec

    def run_main(
        self, request: Any, raw: bytes | None = None
    ) -> tuple[int, dict[str, Any]]:
        data = raw if raw is not None else json.dumps(request).encode() + b"\n"
        out = io.StringIO()
        code = prequantize_child.main(io.BytesIO(data), out, watch_parent=False)
        return code, json.loads(out.getvalue().splitlines()[-1])

    def request(self, **changes: Any) -> dict[str, Any]:
        request = pp.job_request(self.job, self.tmp)
        request.update(changes)
        return request

    def test_bad_requests(self) -> None:
        other_root = Path(self._tmp.name) / f"{derived.TMP_PREFIX}outside"
        other_root.mkdir()
        cases: dict[str, tuple[Any, bytes | None]] = {
            "not json": (None, b"nope\n"),
            "no newline": (None, b"{}"),
            "version": (self.request(version=99), None),
            "action": (self.request(action="rm"), None),
            "unknown model": (self.request(model_id="whisper-9000"), None),
            "key mismatch": (
                self.request(key={**self.key.as_dict(), "revision": "x"}),
                None,
            ),
            "device": (self.request(device="tpu"), None),
            "threads": (self.request(cpu_threads=-1), None),
            "cuda dirs": (self.request(cuda_library_dirs="C:/x"), None),
            "tmp outside": (self.request(tmp=os.fspath(other_root)), None),
            "tmp with ..": (
                self.request(tmp=os.fspath(self.tmp / ".." / f"{derived.TMP_PREFIX}x")),
                None,
            ),
        }
        for label, (request, raw) in cases.items():
            with (
                self.subTest(label),
                self.assertLogs("kikitori.asr.prequantize_child", "ERROR"),
            ):
                code, result = self.run_main(request, raw)
            self.assertEqual(code, pp.EXIT_BAD_REQUEST, label)
            self.assertEqual(result["error_type"], "BadRequest")
        (self.tmp / "junk").write_bytes(b"x")
        with self.assertLogs("kikitori.asr.prequantize_child", "ERROR"):
            code, _ = self.run_main(self.request())
        self.assertEqual(code, pp.EXIT_BAD_REQUEST)  # 暫存資料夾不是空的

    def test_derive_writes_the_folder_without_publishing(self) -> None:
        real = derived.prepare_derived

        def prepare(job: DerivationJob, tmp: Path) -> Any:
            return real(
                job,
                tmp,
                create_model=lambda *a: FakeProbeModel("same"),
                storage=lambda a: a,
            )

        with mock.patch.object(derived, "prepare_derived", prepare):
            code, result = self.run_main(self.request())
        self.assertEqual((code, result["ok"]), (pp.EXIT_OK, True))
        self.assertGreater(result["model_bin_bytes"], 0)
        self.assertTrue((self.tmp / derived.DERIVED_MARKER).is_file())
        self.assertIsNone(find_derived(self.spec, self.cache, self.key))  # 沒有發布

    def test_failures_are_reported(self) -> None:
        with (
            mock.patch.object(
                derived, "prepare_derived", side_effect=ModelFormatError("bad file")
            ),
            self.assertLogs("kikitori.asr.prequantize_child", "ERROR"),
        ):
            code, result = self.run_main(self.request())
        self.assertEqual(code, pp.EXIT_FAILED)
        self.assertEqual(
            (result["error_type"], result["error"]), ("ModelFormatError", "bad file")
        )

    def test_self_test(self) -> None:
        code, result = self.run_main({"version": 1, "action": "self_test"})
        self.assertEqual((code, result["ok"]), (pp.EXIT_OK, True))


@unittest.skipIf(_user_tiny() is None, "tiny is not in the local model cache")
class RealChildTest(unittest.TestCase):
    """真的子行程（`python -m kikitori.asr.prequantize_child`）、真的 CTranslate2（tiny、CPU int8）。"""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cache = Path(self._tmp.name)
        self.spec = get_model("tiny")
        source = _user_tiny()
        assert source is not None
        target = self.cache / self.spec.id / self.spec.revision[:12]
        target.mkdir(parents=True)
        for file in [*self.spec.files, ModelFile(MARKER_NAME, 0, "")]:
            shutil.copyfile(source / file.name, target / file.name)
        key = derived_key(self.spec, "int8")
        assert key is not None
        self.key = key
        self.job = DerivationJob(self.spec, self.cache, key, "cpu", cpu_threads=2)
        self.root = derived.derived_root(self.spec, self.cache)

    def test_child_converts_verifies_and_parent_publishes(self) -> None:
        with self.assertLogs("kikitori.asr", "INFO"):
            result = pp.build_derived_in_subprocess(self.job)
        self.assertEqual(find_derived(self.spec, self.cache, self.key), result.path)
        self.assertEqual(
            sorted(p.name for p in self.root.iterdir()), [self.key.dirname]
        )

    def test_cancel_kills_the_child_and_cleans_up(self) -> None:
        seen: list[float] = []

        def stop() -> bool:
            # 子行程已開始寫 model.bin（轉換或之後的驗證中）才要求停止
            if not seen and any(self.root.glob(f"{derived.TMP_PREFIX}*/model.bin")):
                seen.append(time.monotonic())
            return bool(seen)

        with self.assertRaises(DerivationCancelled):
            pp.build_derived_in_subprocess(self.job, should_stop=stop)
        self.assertTrue(seen)
        self.assertLess(time.monotonic() - seen[0], 5.0)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_child_exits_when_the_engine_goes_away(self) -> None:
        import subprocess

        self.root.mkdir(parents=True)
        tmp = self.root / f"{derived.TMP_PREFIX}orphan"
        tmp.mkdir()
        process = subprocess.Popen(
            pp.child_command(),
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert process.stdin is not None
        request = json.dumps(pp.job_request(self.job, tmp)).encode()
        process.stdin.write(request + b"\n")
        process.stdin.close()  # 引擎「當掉」：stdin 收到 EOF
        self.assertEqual(process.wait(WAIT * 2), pp.EXIT_PARENT_GONE)


if __name__ == "__main__":
    unittest.main()
