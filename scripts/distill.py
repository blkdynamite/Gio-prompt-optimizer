#!/usr/bin/env python3
"""
distill.py — turn a Claude Code transcript into a lean checkpoint.

Reads a Claude Code JSONL transcript (the per-session log Claude Code writes
under ~/.claude/projects/) and emits a markdown checkpoint that keeps what
matters (user goals, files touched, final decisions, last few turns) and
drops what doesn't (stale Read dumps, superseded Grep output, resolved
errors). The checkpoint is small enough to paste back into a fresh session
or to hand to the harness's PreCompact pass as an "additional context" seed.

Nothing is sent anywhere. Pure stdlib. Runs on Python 3.9+.

Usage:
    python3 distill.py --transcript PATH [--out PATH] [--keep-last N]
                       [--max-bytes N] [--json]
    python3 distill.py --latest                 # distill the newest session
                                                 # for the current cwd
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Any, Iterable


DEFAULT_KEEP_LAST_TURNS = 6
DEFAULT_MAX_BYTES = 24_000
TOOL_RESULT_HEAD = 240

# Tools whose output rarely matters once a later turn used it. Everything else
# is kept verbatim (Edit/Write/TodoWrite/TaskCreate/... — the actual work).
VOLATILE_TOOLS = {"Read", "Grep", "Glob", "Bash", "WebFetch", "WebSearch"}

DECISION_PATTERNS = [
    re.compile(r"(?im)^\s*(?:decision|chose|picked|going with|we['’]ll)\b.*"),
    re.compile(r"(?im)^\s*(?:don['’]t|avoid|not using)\b.*"),
]


# ---------------------------------------------------------------------------
# Transcript model
# ---------------------------------------------------------------------------

@dataclass
class Turn:
    kind: str                       # "user" | "assistant" | "summary"
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)   # {id, name, input}
    tool_results: list[dict] = field(default_factory=list) # {id, output}
    ts: str = ""
    uuid: str = ""


@dataclass
class Session:
    cwd: str = ""
    git_branch: str = ""
    session_id: str = ""
    turns: list[Turn] = field(default_factory=list)
    tool_result_by_id: dict[str, str] = field(default_factory=dict)


def load_transcript(path: Path) -> Session:
    s = Session()
    with path.open("r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except json.JSONDecodeError:
                continue
            s.cwd = ev.get("cwd") or s.cwd
            s.git_branch = ev.get("gitBranch") or s.git_branch
            s.session_id = ev.get("sessionId") or s.session_id

            kind = ev.get("type")
            if kind not in ("user", "assistant", "summary"):
                continue
            msg = ev.get("message") or {}
            content = msg.get("content") or []
            text_parts: list[str] = []
            tool_calls: list[dict] = []
            tool_results: list[dict] = []
            if isinstance(content, str):
                text_parts.append(content)
            else:
                for block in content:
                    btype = block.get("type")
                    if btype == "text":
                        text_parts.append(block.get("text") or "")
                    elif btype == "tool_use":
                        tool_calls.append({
                            "id": block.get("id", ""),
                            "name": block.get("name", ""),
                            "input": block.get("input", {}),
                        })
                    elif btype == "tool_result":
                        out = block.get("content")
                        if isinstance(out, list):
                            out = "\n".join(
                                b.get("text", "") for b in out
                                if isinstance(b, dict) and b.get("type") == "text"
                            )
                        tool_results.append({
                            "id": block.get("tool_use_id", ""),
                            "output": out or "",
                        })
            if kind == "summary":
                text_parts.append(ev.get("summary", ""))

            turn = Turn(
                kind=kind,
                text="\n".join(t for t in text_parts if t).strip(),
                tool_calls=tool_calls,
                tool_results=tool_results,
                ts=ev.get("timestamp", ""),
                uuid=ev.get("uuid", ""),
            )
            s.turns.append(turn)
            for r in tool_results:
                if r["id"]:
                    s.tool_result_by_id[r["id"]] = r["output"]
    return s


# ---------------------------------------------------------------------------
# Distillation
# ---------------------------------------------------------------------------

def _summarize_tool(name: str, inp: dict, output: str) -> str:
    """One-line summary of a volatile tool call."""
    inp = inp or {}
    if name == "Read":
        fp = inp.get("file_path") or inp.get("path") or "?"
        lines = output.count("\n")
        return f"Read {fp} ({lines} lines)"
    if name == "Grep":
        pat = inp.get("pattern", "")
        tp = inp.get("type") or inp.get("glob") or ""
        head = (output[:TOOL_RESULT_HEAD] + "…") if len(output) > TOOL_RESULT_HEAD else output
        matches = len([l for l in head.splitlines() if l.strip()])
        tail = f" filter={tp}" if tp else ""
        return f"Grep /{pat}/{tail} → ~{matches} match line(s)"
    if name == "Glob":
        return f"Glob {inp.get('pattern','?')} → {len(output.splitlines())} path(s)"
    if name == "Bash":
        cmd = inp.get("command", "")
        head = output.strip().replace("\n", " ⏎ ")[:TOOL_RESULT_HEAD]
        return f"$ {cmd[:160]}  →  {head}"
    if name in ("WebFetch", "WebSearch"):
        q = inp.get("url") or inp.get("query") or ""
        return f"{name} {q[:120]}"
    return f"{name}({', '.join(f'{k}={v!r:.40}' for k,v in list(inp.items())[:3])})"


def _files_touched(session: Session) -> "OrderedDict[str, str]":
    """Collect files written/edited (last write wins)."""
    files: "OrderedDict[str, str]" = OrderedDict()
    for turn in session.turns:
        for tc in turn.tool_calls:
            if tc["name"] in ("Edit", "Write", "NotebookEdit"):
                fp = tc["input"].get("file_path")
                if fp:
                    files[fp] = tc["name"]
    return files


def _user_goals(session: Session, limit: int = 4) -> list[str]:
    """First user message + any later direction-changing prompts."""
    goals: list[str] = []
    for turn in session.turns:
        if turn.kind != "user" or not turn.text:
            continue
        # Skip auto-generated meta messages
        if turn.text.startswith(("<", "[", "{\"")):
            continue
        goals.append(turn.text.strip())
        if len(goals) >= limit:
            break
    return goals


def _decisions(session: Session) -> list[str]:
    """Lines from assistant text that look like decisions."""
    out: list[str] = []
    seen = set()
    for turn in session.turns:
        if turn.kind != "assistant" or not turn.text:
            continue
        for pat in DECISION_PATTERNS:
            for m in pat.findall(turn.text):
                s = m.strip()
                if s and s not in seen and len(s) < 240:
                    seen.add(s)
                    out.append(s)
    return out[:12]


def distill(session: Session,
            keep_last_turns: int = DEFAULT_KEEP_LAST_TURNS,
            max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    goals = _user_goals(session)
    files = _files_touched(session)
    decisions = _decisions(session)

    total_turns = len(session.turns)
    recent = session.turns[-keep_last_turns:] if keep_last_turns > 0 else []
    older = session.turns[:-keep_last_turns] if keep_last_turns > 0 else session.turns

    # Compact log for older turns: one line per volatile tool call, verbatim
    # for substantive tool calls and assistant text that isn't recent.
    log_lines: list[str] = []
    for t in older:
        if t.kind == "assistant":
            for tc in t.tool_calls:
                if tc["name"] in VOLATILE_TOOLS:
                    out = session.tool_result_by_id.get(tc["id"], "")
                    log_lines.append("- " + _summarize_tool(tc["name"], tc["input"], out))
                elif tc["name"] in ("Edit", "Write", "NotebookEdit"):
                    log_lines.append(f"- {tc['name']} {tc['input'].get('file_path','?')}")

    parts: list[str] = []
    parts.append(f"# Gio session checkpoint")
    parts.append("")
    parts.append(f"- session: `{session.session_id or 'unknown'}`")
    parts.append(f"- cwd: `{session.cwd or '?'}`")
    if session.git_branch:
        parts.append(f"- branch: `{session.git_branch}`")
    parts.append(f"- turns: {total_turns} (last {len(recent)} kept verbatim)")
    parts.append("")

    if goals:
        parts.append("## What the user asked for")
        for g in goals:
            g1 = g.splitlines()[0][:280]
            parts.append(f"- {g1}")
        parts.append("")

    if files:
        parts.append("## Files touched")
        for fp, op in files.items():
            parts.append(f"- `{fp}` ({op})")
        parts.append("")

    if decisions:
        parts.append("## Decisions")
        for d in decisions:
            parts.append(f"- {d}")
        parts.append("")

    if log_lines:
        parts.append("## Earlier activity (compacted)")
        parts.extend(log_lines)
        parts.append("")

    if recent:
        parts.append("## Recent turns (verbatim)")
        for t in recent:
            parts.append(f"### {t.kind}")
            if t.text:
                parts.append(t.text[:2000])
            for tc in t.tool_calls:
                out = session.tool_result_by_id.get(tc["id"], "")
                if tc["name"] in VOLATILE_TOOLS:
                    parts.append(f"- tool: {_summarize_tool(tc['name'], tc['input'], out)}")
                else:
                    fp = tc["input"].get("file_path", "")
                    parts.append(f"- tool: {tc['name']} {fp}".rstrip())
            parts.append("")

    text = "\n".join(parts)

    # Enforce a size cap: trim the older-activity section first, then recent.
    while len(text.encode("utf-8")) > max_bytes and log_lines:
        log_lines.pop(0)
        text = text  # placeholder — rebuild below
        parts = _rebuild(session, goals, files, decisions, log_lines, recent)
        text = "\n".join(parts)
    return text


def _rebuild(session, goals, files, decisions, log_lines, recent) -> list[str]:
    parts: list[str] = ["# Gio session checkpoint", ""]
    parts.append(f"- session: `{session.session_id or 'unknown'}`")
    parts.append(f"- cwd: `{session.cwd or '?'}`")
    if session.git_branch:
        parts.append(f"- branch: `{session.git_branch}`")
    parts.append(f"- turns: {len(session.turns)} (last {len(recent)} kept verbatim)")
    parts.append("")
    if goals:
        parts.append("## What the user asked for")
        parts += [f"- {g.splitlines()[0][:280]}" for g in goals]
        parts.append("")
    if files:
        parts.append("## Files touched")
        parts += [f"- `{fp}` ({op})" for fp, op in files.items()]
        parts.append("")
    if decisions:
        parts.append("## Decisions")
        parts += [f"- {d}" for d in decisions]
        parts.append("")
    if log_lines:
        parts.append("## Earlier activity (compacted, trimmed)")
        parts += log_lines
        parts.append("")
    if recent:
        parts.append("## Recent turns (verbatim)")
        for t in recent:
            parts.append(f"### {t.kind}")
            if t.text:
                parts.append(t.text[:2000])
            for tc in t.tool_calls:
                out = session.tool_result_by_id.get(tc["id"], "")
                if tc["name"] in VOLATILE_TOOLS:
                    parts.append(f"- tool: {_summarize_tool(tc['name'], tc['input'], out)}")
                else:
                    fp = tc["input"].get("file_path", "")
                    parts.append(f"- tool: {tc['name']} {fp}".rstrip())
            parts.append("")
    return parts


# ---------------------------------------------------------------------------
# Transcript discovery
# ---------------------------------------------------------------------------

def _project_dir_for(cwd: Path) -> Path:
    """Claude Code's convention: ~/.claude/projects/<slug>/"""
    slug = str(cwd).replace("/", "-").replace(os.sep, "-")
    if slug.startswith("-"):
        slug = slug[1:]
    return Path.home() / ".claude" / "projects" / f"-{slug}"


def latest_transcript(cwd: Path | None = None) -> Path | None:
    cwd = (cwd or Path.cwd()).resolve()
    proj = _project_dir_for(cwd)
    if not proj.exists():
        return None
    jsonls = sorted(proj.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    return jsonls[0] if jsonls else None


def checkpoint_path(cwd: Path | None = None) -> Path:
    cwd = (cwd or Path.cwd()).resolve()
    out = cwd / ".gio" / "sessions" / "latest.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    return out


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="Distill a Claude Code transcript.")
    ap.add_argument("--transcript", type=Path, help="Path to JSONL transcript.")
    ap.add_argument("--latest", action="store_true",
                    help="Find the latest transcript for cwd under ~/.claude/projects/.")
    ap.add_argument("--out", type=Path, help="Write to this path (default: stdout).")
    ap.add_argument("--keep-last", type=int, default=DEFAULT_KEEP_LAST_TURNS)
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    ap.add_argument("--json", action="store_true",
                    help="Emit structured JSON instead of markdown.")
    args = ap.parse_args()

    tpath: Path | None = args.transcript
    if args.latest and tpath is None:
        tpath = latest_transcript()
        if tpath is None:
            print("No transcript found under ~/.claude/projects/ for this cwd.",
                  file=sys.stderr)
            return 2
    if tpath is None:
        ap.error("--transcript PATH or --latest is required")

    session = load_transcript(tpath)
    if args.json:
        payload = {
            "session_id": session.session_id,
            "cwd": session.cwd,
            "git_branch": session.git_branch,
            "turns": len(session.turns),
            "files_touched": list(_files_touched(session).keys()),
            "goals": _user_goals(session),
            "decisions": _decisions(session),
        }
        text = json.dumps(payload, indent=2)
    else:
        text = distill(session, keep_last_turns=args.keep_last,
                       max_bytes=args.max_bytes)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(text, encoding="utf-8")
        print(f"Wrote {len(text):,} chars to {args.out}", file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
