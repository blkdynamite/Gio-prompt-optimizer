#!/usr/bin/env python3
"""
SessionStart hook — on a new or resumed session, silently inject the most
recent Gio checkpoint (if any) as additional context so Claude starts with
the prior session's goals, files, and decisions already in mind.

Only fires on `startup` and `resume` sources (not `clear`, so clearing stays
a true clear). Only injects when the checkpoint is reasonably fresh
(default: last 7 days) and under a hard size cap.

Protocol: receives JSON on stdin, prints a JSON directive on stdout:
    {"hookSpecificOutput": {
        "hookEventName": "SessionStart",
        "additionalContext": "..."
    }}
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from distill import checkpoint_path  # type: ignore  # noqa: E402


MAX_AGE_SECONDS = 7 * 24 * 3600       # don't restore week-old checkpoints
MAX_CONTEXT_BYTES = 24_000             # defensive cap on injected context


def _wrap(text: str, trigger: str, age_hours: float) -> str:
    header = (
        f"<gio-session-checkpoint restored=\"true\" "
        f"source=\"{trigger}\" age-hours=\"{age_hours:.1f}\">\n"
        "Gio restored a lean summary of your previous session in this\n"
        "project. Treat it as background context — the user may not know\n"
        "it was injected. If they ask about earlier work, this is why you\n"
        "already know. Do not announce the restore unless asked.\n\n"
    )
    footer = "\n</gio-session-checkpoint>\n"
    return header + text + footer


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    source = payload.get("source", "startup")
    if source == "clear":
        # Honor an explicit /clear — do not resurrect state.
        return 0

    cwd = Path(payload.get("cwd") or ".")
    cp = checkpoint_path(cwd)
    if not cp.exists():
        return 0

    age = time.time() - cp.stat().st_mtime
    if age > MAX_AGE_SECONDS:
        return 0

    try:
        text = cp.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return 0
    if not text.strip():
        return 0
    if len(text.encode("utf-8")) > MAX_CONTEXT_BYTES:
        text = text.encode("utf-8")[:MAX_CONTEXT_BYTES].decode(
            "utf-8", errors="ignore"
        )

    out = {
        "hookSpecificOutput": {
            "hookEventName": "SessionStart",
            "additionalContext": _wrap(text, source, age / 3600),
        }
    }
    json.dump(out, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
