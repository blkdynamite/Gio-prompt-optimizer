#!/usr/bin/env python3
"""Smoke tests for distill.py — no external deps, run with: python3 -m unittest."""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path

from distill import (
    load_transcript,
    distill,
    _files_touched,
    _user_goals,
    DEFAULT_MAX_BYTES,
)


def _event(kind: str, **fields) -> str:
    base = {
        "type": kind,
        "timestamp": "2026-10-16T00:00:00Z",
        "uuid": f"u-{time.time_ns()}",
        "sessionId": "sess-1",
        "cwd": "/tmp/x",
        "gitBranch": "main",
    }
    base.update(fields)
    return json.dumps(base)


def _msg(role: str, blocks: list[dict]) -> dict:
    return {"message": {"role": role, "content": blocks}}


class DistillTests(unittest.TestCase):

    def _write(self, lines: list[str]) -> Path:
        f = Path(tempfile.NamedTemporaryFile(delete=False, suffix=".jsonl").name)
        f.write_text("\n".join(lines), encoding="utf-8")
        return f

    def test_load_and_distill_minimal(self):
        lines = [
            _event("user", **_msg("user", [
                {"type": "text", "text": "add a login screen"}
            ])),
            _event("assistant", **_msg("assistant", [
                {"type": "text", "text": "Reading auth.py"},
                {"type": "tool_use", "id": "t1", "name": "Read",
                 "input": {"file_path": "/x/auth.py"}}
            ])),
            _event("user", **_msg("user", [
                {"type": "tool_result", "tool_use_id": "t1",
                 "content": "line1\nline2\nline3"}
            ])),
            _event("assistant", **_msg("assistant", [
                {"type": "text", "text": "Decision: use session cookies not JWT."},
                {"type": "tool_use", "id": "t2", "name": "Edit",
                 "input": {"file_path": "/x/auth.py",
                           "old_string": "a", "new_string": "b"}}
            ])),
        ]
        tpath = self._write(lines)
        session = load_transcript(tpath)
        self.assertEqual(len(session.turns), 4)
        self.assertEqual(session.cwd, "/tmp/x")
        self.assertEqual(session.git_branch, "main")

        goals = _user_goals(session)
        self.assertIn("add a login screen", goals[0])

        files = _files_touched(session)
        self.assertIn("/x/auth.py", files)
        self.assertEqual(files["/x/auth.py"], "Edit")

        out = distill(session, keep_last_turns=2, max_bytes=DEFAULT_MAX_BYTES)
        self.assertIn("add a login screen", out)
        self.assertIn("/x/auth.py", out)
        self.assertIn("Decision: use session cookies not JWT.", out)
        # Volatile tool output is compacted, not inlined verbatim.
        self.assertNotIn("line1\nline2\nline3", out)

    def test_size_cap_trims_older_log(self):
        # Build a transcript with many Read calls that would exceed max_bytes.
        lines = [_event("user", **_msg("user", [
            {"type": "text", "text": "big task"}
        ]))]
        for i in range(500):
            lines.append(_event("assistant", **_msg("assistant", [
                {"type": "tool_use", "id": f"t{i}", "name": "Read",
                 "input": {"file_path": f"/x/file_{i}.py"}}
            ])))
        tpath = self._write(lines)
        session = load_transcript(tpath)
        out = distill(session, keep_last_turns=2, max_bytes=4_000)
        self.assertLessEqual(len(out.encode("utf-8")), 4_000 + 200)
        self.assertIn("big task", out)

    def test_bad_jsonl_lines_are_skipped(self):
        lines = [
            "not json at all",
            "",
            _event("user", **_msg("user", [{"type": "text", "text": "hi"}])),
            "{\"broken\":",
        ]
        tpath = self._write(lines)
        session = load_transcript(tpath)
        self.assertEqual(len(session.turns), 1)


if __name__ == "__main__":
    unittest.main()
