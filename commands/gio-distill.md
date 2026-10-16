---
description: Save a lean checkpoint of the current session so you can resume in a fresh one without losing context
argument-hint: ""
---

Save a Gio **session checkpoint** right now — a compact markdown summary of
what the user asked for, the files you've touched, the decisions you've
made, and the last few turns verbatim.

Steps:

1. Run the distiller against the current session's transcript:

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/distill.py" --latest \
     --out "./.gio/sessions/latest.md"
   ```

2. Tell the user, in one short line, that the checkpoint is saved and will
   automatically load the next time they start Claude Code in this project.
   Mention the path (`.gio/sessions/latest.md`) in case they want to peek.

3. If the user asks what gets kept vs. dropped, point them at
   `${CLAUDE_PLUGIN_ROOT}/scripts/distill.py` — the top-of-file docstring
   is the honest answer.

This is a manual escape hatch. Gio also runs this automatically via the
`PreCompact` hook when the harness is about to auto-compact, and injects
the checkpoint back on `SessionStart` — most users will never need to type
`/gio-distill`.
