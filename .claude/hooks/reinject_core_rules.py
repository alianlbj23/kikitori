"""SessionStart(compact): re-inject the `## 核心規則` section of the project CLAUDE.md.

For SessionStart hooks, plain stdout is added to Claude's context.
"""

import os
import sys
from pathlib import Path

from _hook_io import fail, read_payload

SECTION_HEADING = "## 核心規則"


def extract_section(markdown: str, heading: str) -> str | None:
    """Return `heading` and its body, up to the next heading of the same or higher level."""
    lines = markdown.splitlines()
    try:
        start = next(i for i, line in enumerate(lines) if line.strip() == heading)
    except StopIteration:
        return None
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("# ") or lines[i].startswith("## "):
            end = i
            break
    return "\n".join(lines[start:end]).strip()


def main() -> None:
    read_payload()  # validate input even though no field is needed
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if not project_dir:
        fail("CLAUDE_PROJECT_DIR is not set")
    claude_md = Path(project_dir) / "CLAUDE.md"
    try:
        text = claude_md.read_text(encoding="utf-8")
    except OSError as exc:
        fail(f"cannot read {claude_md}: {exc}")

    section = extract_section(text, SECTION_HEADING)
    if section is None:
        fail(f"section '{SECTION_HEADING}' not found in {claude_md}")

    out = (
        "對話剛被壓縮。以下是本專案 CLAUDE.md 的核心規則，接下來的工作必須持續遵守：\n\n"
        f"{section}\n"
    )
    sys.stdout.buffer.write(out.encode("utf-8"))


if __name__ == "__main__":
    main()
