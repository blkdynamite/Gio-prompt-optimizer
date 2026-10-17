#!/usr/bin/env python3
"""
PreToolUse hook on `Read` — when Claude is about to read a very large file,
emit a non-blocking advisory pointing at Gio's targeted retrieval instead.

Never blocks. Never prompts. Just quietly adds a one-line note to the tool
call's additional context so Claude is more likely to use `retrieve.py` or a
targeted `offset/limit` on the next read of this file.

Protocol: receives JSON on stdin, prints a PreToolUse hook JSON on stdout.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


# Thresholds — a Read is "big enough to warn about" when EITHER applies:
WARN_BYTES = 100_000          # ~25k tokens-ish
WARN_LINES = 1_200            # Claude Code's own default limit is 2000


def _file_facts(fp: str) -> tuple[int, int] | None:
    try:
        p = Path(fp)
        size = p.stat().st_size
    except (OSError, ValueError):
        return None
    # Count lines cheaply without loading the whole file into memory.
    lines = 0
    try:
        with p.open("rb") as fh:
            for _ in fh:
                lines += 1
                if lines > WARN_LINES:
                    break
    except OSError:
        return (size, 0)
    return (size, lines)


def _advisory(fp: str, size: int, lines: int) -> str:
    kb = size // 1024
    plugin_root = os.environ.get("CLAUDE_PLUGIN_ROOT", "<gio>")
    return (
        f"[gio] about to Read {fp} ({kb} KB, ~{lines}+ lines). This is big "
        "enough that reading it whole will carry a lot of stale context for "
        "the rest of the session. If you only need a few spans, consider:\n"
        f"  python3 {plugin_root}/scripts/retrieve.py \"<your question>\"\n"
        "or a targeted Read with offset/limit. Not blocking — go ahead if "
        "you really need the full file."
    )


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    if payload.get("tool_name") != "Read":
        return 0
    tool_input = payload.get("tool_input") or {}
    fp = tool_input.get("file_path")
    if not fp:
        return 0

    # If offset/limit already set, respect it — Claude is already being lean.
    if tool_input.get("offset") is not None or tool_input.get("limit") is not None:
        return 0

    facts = _file_facts(fp)
    if facts is None:
        return 0
    size, lines = facts
    if size < WARN_BYTES and lines < WARN_LINES:
        return 0

    out = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "additionalContext": _advisory(fp, size, lines),
        }
    }
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
