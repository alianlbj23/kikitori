"""End-to-end tests for .claude/hooks: feed JSON on stdin, check exit code and output."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HOOKS_DIR = Path(__file__).resolve().parent.parent / ".claude" / "hooks"


def run_hook(script: str, stdin: str, env: dict[str, str] | None = None):
    return subprocess.run(
        [sys.executable, str(HOOKS_DIR / script)],
        input=stdin.encode("utf-8"),
        capture_output=True,
        timeout=60,
        env={**os.environ, **(env or {})},
        check=False,
    )


def bash_payload(command: str) -> str:
    return json.dumps({"tool_name": "Bash", "tool_input": {"command": command}})


def edit_payload(path: str, key: str = "file_path") -> str:
    return json.dumps({"tool_name": "Edit", "tool_input": {key: path}})


def decision(result) -> str | None:
    if not result.stdout.strip():
        return None
    return json.loads(result.stdout)["hookSpecificOutput"]["permissionDecision"]


class BlockPipInstallTest(unittest.TestCase):
    def test_blocks_pip_variants(self):
        for cmd in (
            "pip install requests",
            "pip3 install -U requests",
            "uv pip install requests",
            "python -m pip install requests",
            "cd src && PIP.exe   install x",
        ):
            with self.subTest(cmd=cmd):
                result = run_hook("block_pip_install.py", bash_payload(cmd))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(decision(result), "deny")
                self.assertIn(
                    "uv add",
                    json.loads(result.stdout)["hookSpecificOutput"][
                        "permissionDecisionReason"
                    ],
                )

    def test_allows_other_commands(self):
        for cmd in (
            "uv add requests==2.32.3",
            "pip list",
            "uv pip list",
            "pipx install ruff",
            "ls",
        ):
            with self.subTest(cmd=cmd):
                result = run_hook("block_pip_install.py", bash_payload(cmd))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(decision(result))

    def test_invalid_json_is_reported(self):
        result = run_hook("block_pip_install.py", "not json")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"not valid JSON", result.stderr)


class ProtectFilesTest(unittest.TestCase):
    def test_blocks_protected_files(self):
        for path in (r"D:\proj\uv.lock", "/home/u/proj/uv.lock", "/p/sub/UV.LOCK"):
            with self.subTest(path=path):
                result = run_hook("protect_files.py", edit_payload(path))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(decision(result), "deny")

    def test_allows_normal_files(self):
        for path in (
            "/p/main.py",
            "/p/LICENSE",
            r"D:\proj\License.md",
            "/p/uv.lock.bak",
            "/p/pyproject.toml",
        ):
            with self.subTest(path=path):
                result = run_hook("protect_files.py", edit_payload(path))
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIsNone(decision(result))

    def test_non_object_payload_is_reported(self):
        result = run_hook("protect_files.py", "[1, 2]")
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"expected a JSON object", result.stderr)


class RuffFixTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def write(self, name: str, content: str) -> Path:
        path = Path(self.tmp.name) / name
        path.write_text(content, encoding="utf-8")
        return path

    def test_formats_and_fixes_python_file(self):
        path = self.write("a.py", "import os\nx=1\n\n\n\ny = {  'a':1 }\n")
        result = run_hook("ruff_fix.py", edit_payload(str(path)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, b"")
        self.assertEqual(path.read_text(encoding="utf-8"), 'x = 1\n\n\ny = {"a": 1}\n')

    def test_reports_unfixable_lint_to_claude(self):
        path = self.write("b.py", "print(undefined_name)\n")
        result = run_hook("ruff_fix.py", edit_payload(str(path)))
        self.assertEqual(result.returncode, 0, result.stderr)
        out = json.loads(result.stdout)
        self.assertEqual(out["decision"], "block")
        self.assertIn("F821", out["reason"])

    def test_ignores_non_python_files(self):
        path = self.write("c.txt", "x=1\n")
        result = run_hook("ruff_fix.py", edit_payload(str(path)))
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(path.read_text(encoding="utf-8"), "x=1\n")

    def test_missing_file_is_reported(self):
        result = run_hook(
            "ruff_fix.py", edit_payload(str(Path(self.tmp.name) / "gone.py"))
        )
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"not found", result.stderr)


class ReinjectCoreRulesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env = {"CLAUDE_PROJECT_DIR": self.tmp.name}

    def test_prints_only_core_section(self):
        (Path(self.tmp.name) / "CLAUDE.md").write_text(
            "# P\n\n## 其他\nskip-a\n\n## 核心規則\n- rule 1\n### 子節\n- rule 2\n\n## 之後\nskip-b\n",
            encoding="utf-8",
        )
        result = run_hook("reinject_core_rules.py", '{"source": "compact"}', self.env)
        self.assertEqual(result.returncode, 0, result.stderr)
        out = result.stdout.decode("utf-8")
        self.assertIn("- rule 1", out)
        self.assertIn("- rule 2", out)
        self.assertNotIn("skip-a", out)
        self.assertNotIn("skip-b", out)

    def test_missing_section_is_reported(self):
        (Path(self.tmp.name) / "CLAUDE.md").write_text(
            "# P\n## 其他\n", encoding="utf-8"
        )
        result = run_hook("reinject_core_rules.py", "{}", self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn("核心規則", result.stderr.decode("utf-8"))

    def test_missing_claude_md_is_reported(self):
        result = run_hook("reinject_core_rules.py", "{}", self.env)
        self.assertEqual(result.returncode, 1)
        self.assertIn(b"cannot read", result.stderr)


if __name__ == "__main__":
    unittest.main()
