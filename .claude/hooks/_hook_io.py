"""Shared helpers for Claude Code hook scripts (stdlib only)."""

import json
import sys
from typing import Any, NoReturn


def fail(message: str) -> NoReturn:
    """Report a hook malfunction (exit 1: non-blocking, stderr shown to the user, UTF-8)."""
    sys.stderr.buffer.write(f"[hook error] {message}\n".encode())
    sys.stderr.buffer.flush()
    sys.exit(1)


def read_payload() -> dict[str, Any]:
    """Read and validate the JSON object Claude Code sends on stdin."""
    raw = sys.stdin.buffer.read().decode("utf-8")
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        fail(f"stdin is not valid JSON: {exc}")
    if not isinstance(payload, dict):
        fail(f"expected a JSON object on stdin, got {type(payload).__name__}")
    return payload


def tool_input_str(payload: dict[str, Any], *keys: str) -> str | None:
    """Return the first string value among tool_input[key] for the given keys."""
    tool_input = payload.get("tool_input")
    if not isinstance(tool_input, dict):
        return None
    for key in keys:
        value = tool_input.get(key)
        if isinstance(value, str) and value:
            return value
    return None


def emit(obj: dict[str, Any]) -> None:
    """Write a JSON response for Claude Code to stdout (UTF-8 regardless of console codepage)."""
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8"))
    sys.stdout.buffer.flush()


def deny_tool(reason: str) -> None:
    """PreToolUse: block the tool call; the reason is shown to Claude."""
    emit(
        {
            "hookSpecificOutput": {
                "hookEventName": "PreToolUse",
                "permissionDecision": "deny",
                "permissionDecisionReason": reason,
            }
        }
    )
