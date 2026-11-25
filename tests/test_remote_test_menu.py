"""`scripts/remote_test.py`：測試當下版本的遠端運算（這台開 app、同步到運算主機、開運算主機選單）。

外部指令（git、node、ssh）全部由假的 runner 記錄，不真的執行。
"""

import importlib.util
import io
import subprocess
import sys
import tarfile
import tempfile
import unittest
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def _load() -> Any:
    spec = importlib.util.spec_from_file_location(
        "kikitori_test_remote_test", ROOT / "scripts" / "remote_test.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


rt = _load()


class Script:
    def __init__(self, *answers: str) -> None:
        self.answers = list(answers)

    def __call__(self, prompt: str) -> str:
        if not self.answers:
            raise EOFError
        return self.answers.pop(0)


class FakeRunner:
    """記錄每個指令；`git ls-files` 回傳指定的檔案清單。"""

    def __init__(self, files: Sequence[str] = (), returncode: int = 0) -> None:
        self.files = files
        self.returncode = returncode
        self.calls: list[dict[str, Any]] = []

    def __call__(self, argv: Sequence[str], **kwargs: Any) -> Any:
        self.calls.append({"argv": list(argv), **kwargs})
        stdout = b""
        if list(argv[:2]) == ["git", "ls-files"]:
            stdout = b"".join(f.encode() + b"\0" for f in self.files)
        return subprocess.CompletedProcess(list(argv), self.returncode, stdout, b"")


class Case(unittest.TestCase):
    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.config = self.tmp / "dev" / "remote_test.json"
        self.lines: list[str] = []

    def out(self, line: str) -> None:
        self.lines.append(line)

    def said(self, text: str) -> bool:
        return any(text in line for line in self.lines)


class ValidationTest(Case):
    def test_ssh_target(self) -> None:
        for ok in ("alian_server", "user@gpu-box", "gpu-box.tail1234.ts.net"):
            self.assertTrue(rt.is_ssh_target(ok), ok)
        # 開頭的 - 會被 ssh 當成選項（-oProxyCommand=…）
        for bad in ("", "-oProxyCommand=x", "a b", "host;rm", "host$(x)", "a" * 254):
            self.assertFalse(rt.is_ssh_target(bad), bad)

    def test_remote_dir(self) -> None:
        for ok in ("~/workspace/kikitori-remote", "/home/user/kikitori", "kikitori"):
            self.assertTrue(rt.is_remote_dir(ok), ok)
        for bad in ("", "-rf", "~/a b", "x;y", "$(x)", "~user/x", "a/../../etc"):
            self.assertFalse(rt.is_remote_dir(bad), bad)

    def test_remote_command_quotes_the_folder_and_finds_uv(self) -> None:
        command = rt.remote_command("~/workspace/kiki tori", "uv run kikitori")
        self.assertIn("cd \"$HOME\"/'workspace/kiki tori'", command)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', command)
        self.assertTrue(command.endswith("&& uv run kikitori"))
        self.assertIn("cd /srv/k ", rt.remote_command("/srv/k", "true"))


class ConfigTest(Case):
    def test_saved_and_read_back(self) -> None:
        rt.save_config(self.config, rt.Config("alian_server", "~/k"))
        self.assertEqual(rt.load_config(self.config), rt.Config("alian_server", "~/k"))

    def test_missing_or_damaged_file_means_not_set(self) -> None:
        self.assertEqual(rt.load_config(self.config), rt.Config("", ""))
        self.config.parent.mkdir(parents=True)
        for text in ("not json", '{"ssh": "-oX", "dir": "~/k"}', "[]"):
            self.config.write_text(text, encoding="utf-8")
            self.assertEqual(rt.load_config(self.config), rt.Config("", ""), text)


class ArchiveTest(Case):
    def test_packs_the_listed_files_and_skips_deleted_or_local_results(self) -> None:
        (self.tmp / "src").mkdir()
        (self.tmp / "src" / "a.py").write_text("print(1)", encoding="utf-8")
        results = self.tmp / "experiments" / "remote_compute" / "results"
        results.mkdir(parents=True)
        (results / "shot.png").write_bytes(b"x")
        data = rt.build_archive(
            self.tmp,
            [
                "src/a.py",
                "src/deleted.py",
                "experiments/remote_compute/results/shot.png",
            ],
        )
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
            self.assertEqual(tar.getnames(), ["src/a.py"])


class MenuTest(Case):
    def menu(self, runner: FakeRunner, *answers: str) -> int:
        return rt.menu(Script(*answers), self.out, runner, self.config, ROOT)

    def test_first_use_asks_for_the_compute_host_then_syncs(self) -> None:
        runner = FakeRunner(files=["pyproject.toml"])
        self.assertEqual(
            self.menu(runner, "2", "alian_server", "~/workspace/kikitori-remote", "5"),
            0,
        )
        self.assertEqual(
            rt.load_config(self.config),
            rt.Config("alian_server", "~/workspace/kikitori-remote"),
        )
        ssh = [c for c in runner.calls if c["argv"][0] == "ssh"]
        self.assertEqual(len(ssh), 2)  # 解壓縮、uv sync
        unpack = ssh[0]
        self.assertEqual(unpack["argv"][:3], ["ssh", "--", "alian_server"])
        self.assertIn("tar -xzf -", unpack["argv"][3])
        self.assertTrue(unpack["input"])  # tar.gz 從 stdin 送過去
        self.assertIn("uv sync", ssh[1]["argv"][3])
        self.assertTrue(all(c.get("timeout") for c in runner.calls))
        self.assertTrue(self.said("同步完成"))

    def test_a_failed_sync_is_reported(self) -> None:
        rt.save_config(self.config, rt.Config("alian_server", "~/k"))
        runner = FakeRunner(files=["pyproject.toml"], returncode=255)
        self.menu(runner, "2", "5")
        self.assertTrue(self.said("同步失敗"))
        self.assertFalse(self.said("同步完成"))

    def test_open_the_compute_host_menu_over_ssh(self) -> None:
        rt.save_config(self.config, rt.Config("alian_server", "~/k"))
        runner = FakeRunner()
        self.menu(runner, "3", "5")
        call = runner.calls[-1]
        self.assertEqual(call["argv"][:4], ["ssh", "-t", "--", "alian_server"])
        self.assertTrue(call["argv"][4].endswith("uv run kikitori"))
        self.assertTrue(self.said("4"))  # 提示在選單選「運算主機」

    def test_open_the_app_with_the_current_code(self) -> None:
        # 不依賴這台電腦裝了什麼（CI 的 Python 工作沒有 Node.js、Rust、app/node_modules）：
        # 用假的 repo 根目錄放 Tauri CLI，工具查詢也換成「都找得到」
        root = self.tmp / "repo"
        tauri = root / "app" / "node_modules" / "@tauri-apps" / "cli" / "tauri.js"
        tauri.parent.mkdir(parents=True)
        tauri.write_text("", encoding="utf-8")
        runner = FakeRunner()
        with mock.patch.object(rt.shutil, "which", lambda name: f"/usr/bin/{name}"):
            rt.menu(Script("1", "5"), self.out, runner, self.config, root)
        call = runner.calls[-1]
        self.assertEqual(call["argv"], ["node", str(tauri), "dev"])
        self.assertEqual(call["cwd"], root / "app")
        self.assertEqual(call["env"]["KIKITORI_ENGINE_CWD"], str(root))

    def test_a_missing_tauri_cli_is_explained(self) -> None:
        runner = FakeRunner()
        with mock.patch.object(rt.shutil, "which", lambda name: f"/usr/bin/{name}"):
            rt.menu(Script("1", "5"), self.out, runner, self.config, self.tmp)
        self.assertEqual(runner.calls, [])
        self.assertTrue(self.said("npm install"))

    def test_missing_build_tools_are_explained_instead_of_failing(self) -> None:
        # Linux 運算主機常沒有 Rust：直接跑 tauri dev 只會看到「cargo metadata」的英文錯誤
        runner = FakeRunner()
        with mock.patch.object(
            rt.shutil,
            "which",
            lambda name: None if name == "cargo" else "/usr/bin/" + name,
        ):
            self.menu(runner, "1", "5")
        self.assertEqual(runner.calls, [])
        self.assertTrue(self.said("Rust"))
        self.assertTrue(self.said("運算主機"))  # 說明只當運算主機不需要 app

    def test_bad_host_input_is_refused(self) -> None:
        runner = FakeRunner()
        self.menu(runner, "4", "-oProxyCommand=x", "5")
        self.assertEqual(rt.load_config(self.config), rt.Config("", ""))
        self.assertTrue(self.said("SSH"))
        self.assertEqual(runner.calls, [])

    def test_unknown_choice_and_end_of_input(self) -> None:
        self.assertEqual(self.menu(FakeRunner(), "9"), 0)
        self.assertTrue(self.said("無效"))

    def test_saved_settings_are_offered_as_defaults(self) -> None:
        rt.save_config(self.config, rt.Config("alian_server", "~/k"))
        self.menu(FakeRunner(), "4", "", "", "5")
        self.assertEqual(rt.load_config(self.config), rt.Config("alian_server", "~/k"))


if __name__ == "__main__":
    unittest.main()
