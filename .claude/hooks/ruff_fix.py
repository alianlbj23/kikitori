"""PostToolUse(Write|Edit|MultiEdit): run `ruff check --fix` and `ruff format` on edited .py files.

Must run inside the project environment (`uv run --project ...`) so the
pinned dev dependency ruff is importable via `python -m ruff`.
"""

import subprocess
import sys
from pathlib import Path

from _hook_io import emit, fail, read_payload, tool_input_str

TIMEOUT_SECONDS = 30


def run_ruff(*args: str) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            [sys.executable, "-m", "ruff", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        fail(f"ruff {' '.join(args)} timed out after {TIMEOUT_SECONDS}s")
    except OSError as exc:
        fail(f"could not start ruff: {exc}")


def main() -> None:
    payload = read_payload()
    path_str = tool_input_str(payload, "file_path")
    if path_str is None or not path_str.lower().endswith(".py"):
        return
    path = Path(path_str)
    if not path.is_file():
        fail(f"edited file not found: {path}")

    # Fix lint first (e.g. import sorting), then format the result.
    check = run_ruff("check", "--fix", "--quiet", str(path))
    if check.returncode not in (0, 1):
        fail(f"ruff check failed (exit {check.returncode}): {check.stderr.strip()}")
    fmt = run_ruff("format", "--quiet", str(path))
    if fmt.returncode != 0:
        fail(f"ruff format failed (exit {fmt.returncode}): {fmt.stderr.strip()}")

    if check.returncode == 1:
        # Remaining violations ruff could not auto-fix: feed them back to Claude.
        emit(
            {
                "decision": "block",
                "reason": f"ruff found issues it could not auto-fix in {path}:\n"
                f"{check.stdout.strip()}",
            }
        )


if __name__ == "__main__":
    main()
