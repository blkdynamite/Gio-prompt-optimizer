#!/usr/bin/env python3
"""
PreCompact hook — fires right before Claude Code auto-compacts a long session.

Writes a Gio checkpoint to .gio/sessions/latest.md in the session's cwd so a
subsequent SessionStart hook can inject it back as additional context. Fails
silent (prints nothing on happy path) to avoid cluttering the terminal.

Protocol: receives one JSON object on stdin, exits 0 on success.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

# Add parent so we can import the sibling module without installing.
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

from distill import (  # type: ignore  # noqa: E402
    load_transcript,
    distill,
    checkpoint_path,
    DEFAULT_KEEP_LAST_TURNS,
    DEFAULT_MAX_BYTES,
)


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        # No input — nothing to do, do not block compaction.
        return 0

    tpath = payload.get("transcript_path")
    cwd = payload.get("cwd") or "."
    trigger = payload.get("trigger", "auto")
    if not tpath or not Path(tpath).exists():
        return 0

    try:
        session = load_transcript(Path(tpath))
        text = distill(session,
                       keep_last_turns=DEFAULT_KEEP_LAST_TURNS,
                       max_bytes=DEFAULT_MAX_BYTES)
        out = checkpoint_path(Path(cwd))
        out.write_text(text, encoding="utf-8")
        # also keep a timestamped copy so users can browse past sessions
        archive = out.parent / f"checkpoint-{int(time.time())}.md"
        archive.write_text(text, encoding="utf-8")
    except Exception as e:
        # Never block the compaction — fail quietly to stderr.
        print(f"[gio] precompact checkpoint skipped: {e}", file=sys.stderr)
        return 0

    # One-line visible confirmation (stdout is shown to the user in transcript
    # mode but doesn't gate the harness).
    kb = max(1, len(text) // 1024)
    print(f"🌱 gio: saved a {kb}KB session checkpoint "
          f"({'manual' if trigger == 'manual' else 'auto-compact'}) → {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
