"""PreToolUse(Bash|PowerShell): block `pip install` / `uv pip install`, point to `uv add`."""

import re

from _hook_io import deny_tool, read_payload, tool_input_str

# Matches pip, pip3, pip3.12, pip.exe followed by `install`; this also covers
# `uv pip install` and `python -m pip install`.
PIP_INSTALL = re.compile(
    r"(?<![\w.-])pip(?:3(?:\.\d+)?)?(?:\.exe)?\s+install\b", re.IGNORECASE
)

REASON = (
    "This project manages dependencies with uv; `pip install` / `uv pip install` is blocked.\n"
    "Use instead:\n"
    "  uv add <package>==<version>        # runtime dependency\n"
    "  uv add --dev <package>==<version>  # dev dependency\n"
    "Per project rules, ask the user before adding any new dependency."
)


def main() -> None:
    payload = read_payload()
    command = tool_input_str(payload, "command")
    if command is not None and PIP_INSTALL.search(command):
        deny_tool(REASON)


if __name__ == "__main__":
    main()
