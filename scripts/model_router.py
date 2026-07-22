#!/usr/bin/env python3
"""
model_router.py — recommend which model tier should do which work.

Plan and orchestrate on the best model the user has access to; hand
mechanical work (parallel exploration, bulk edits, summarization) to cheaper
models. That split saves real money without hurting output quality — but no
API exposes what tier an account is *entitled* to, so this script gathers
local, read-only EVIDENCE and prints a recommendation. It never writes
config, never blocks anything, and the orchestrating agent applies it via
subagent model overrides (SKILL.md Part 7).

Evidence, strongest first:
    1. Explicit model settings — ~/.claude/settings.json and the project's
       .claude/settings.json / settings.local.json ("model" key).
    2. Environment — ANTHROPIC_MODEL, CLAUDE_CODE_SUBAGENT_MODEL.
    3. Usage logs — model ids seen in ~/.claude/projects/**/*.jsonl recently
       (reuses impact.py's log discovery). Works for Bedrock/Vertex ids too.

Honest limits (also why this is report-only):
    - Logs prove models *used*, not entitled: a downgraded plan leaves stale
      evidence; a fresh install has none.
    - Cursor (and other non-Claude-Code environments) expose nothing —
      detection returns "unknown" and the recommendation is "inherit"
      (no override), which is always safe.

Usage:
    python3 scripts/model_router.py          # human-readable recommendation
    python3 scripts/model_router.py --json   # for the orchestrating agent
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import impact  # noqa: E402  (reuses find_log_files + family matching idea)

# Higher = more capable. Substring-matched against model ids, so plain names
# ("opus"), API ids ("claude-opus-4-8"), and Bedrock/Vertex ids
# ("us.anthropic.claude-...") all resolve.
FAMILY_RANK = {"haiku": 1, "sonnet": 2, "opus": 3, "fable": 4}

# How task kinds map to tiers. "best" resolves to the strongest family in
# evidence; fixed names stay as-is; everything falls back to "inherit" when
# evidence is missing.
ROUTING_TABLE = {
    "planner": "best",
    "orchestrator": "best",
    "root_cause_analysis": "best",
    "code_review": "sonnet-or-best",
    "explore_search": "haiku",
    "mechanical_edit": "haiku",
    "summarize": "haiku",
}

LOG_WINDOW_DAYS = 30


def family_of(model_id: str):
    model_id = (model_id or "").lower()
    for family in FAMILY_RANK:
        if family in model_id:
            return family
    return None


def _settings_model(path: Path, source: str, evidence: list):
    try:
        model = json.loads(path.read_text()).get("model")
    except (OSError, json.JSONDecodeError, AttributeError):
        return
    if model and family_of(model):
        evidence.append({"model": model, "family": family_of(model),
                         "source": source, "strength": "explicit"})


def gather_evidence(claude_dir: Path, project_dir: Path, environ=None):
    environ = environ if environ is not None else os.environ
    evidence = []

    _settings_model(claude_dir / "settings.json", "user settings", evidence)
    _settings_model(project_dir / ".claude" / "settings.json",
                    "project settings", evidence)
    _settings_model(project_dir / ".claude" / "settings.local.json",
                    "project local settings", evidence)

    for var in ("ANTHROPIC_MODEL", "CLAUDE_CODE_SUBAGENT_MODEL"):
        model = environ.get(var)
        if model and family_of(model):
            evidence.append({"model": model, "family": family_of(model),
                             "source": f"env {var}", "strength": "explicit"})

    # Usage logs: model ids actually billed recently. Same files impact.py
    # reads; window by file mtime, same approximation aggregate() uses.
    logs_dir = claude_dir / "projects"
    now = time.time()
    seen = {}
    for fpath in impact.find_log_files(logs_dir):
        try:
            age_days = (now - os.path.getmtime(fpath)) / 86400.0
        except OSError:
            continue
        if age_days > LOG_WINDOW_DAYS:
            continue
        try:
            with open(fpath, encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if '"model"' not in line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    model = (rec.get("message") or {}).get("model") or ""
                    fam = family_of(model)
                    if fam:
                        prev = seen.get(fam)
                        if prev is None or age_days < prev[1]:
                            seen[fam] = (model, age_days)
        except OSError:
            continue
    for fam, (model, age_days) in seen.items():
        evidence.append({"model": model, "family": fam,
                         "source": f"usage logs (~{age_days:.0f}d ago)",
                         "strength": "recent" if age_days <= 7 else "stale"})
    return evidence


def recommend(evidence):
    best = None
    confidence = "none"
    for e in evidence:
        if best is None or FAMILY_RANK[e["family"]] > FAMILY_RANK[best["family"]]:
            best = e
    if best is not None:
        confidence = {"explicit": "high", "recent": "medium",
                      "stale": "low"}[best["strength"]]

    def resolve(rule):
        if best is None:
            return "inherit"
        if rule == "best":
            return best["family"]
        if rule == "sonnet-or-best":
            return ("sonnet" if FAMILY_RANK[best["family"]]
                    >= FAMILY_RANK["sonnet"] else best["family"])
        return rule

    return {
        "best_seen": None if best is None else
        {"family": best["family"], "model": best["model"],
         "source": best["source"]},
        "confidence": confidence,
        "evidence": evidence,
        "routing": {task: resolve(rule)
                    for task, rule in ROUTING_TABLE.items()},
        "note": ("Evidence of access, not a guarantee of entitlement — "
                 "logs show models used, not what the account allows. "
                 "'inherit' means: don't override, use the session's model."),
    }


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Report-only model-tier routing recommendation.")
    p.add_argument("--claude-dir", default=str(Path.home() / ".claude"),
                   help="Claude Code config dir (default: ~/.claude)")
    p.add_argument("--project", default=".",
                   help="Project dir to check for .claude settings")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = p.parse_args(argv)

    rec = recommend(gather_evidence(
        Path(os.path.expanduser(args.claude_dir)),
        Path(os.path.expanduser(args.project)).resolve()))

    if args.json:
        print(json.dumps(rec, indent=2))
        return 0
    if rec["best_seen"] is None:
        print("No model evidence found (fresh install, or not a Claude Code "
              "machine — e.g. Cursor). Recommendation: inherit (no overrides).")
        return 0
    b = rec["best_seen"]
    print(f"Best model in evidence: {b['family']} ({b['model']}, "
          f"via {b['source']}; confidence {rec['confidence']})")
    print("Suggested routing (apply via subagent model overrides):")
    for task, model in rec["routing"].items():
        print(f"  {task:22} -> {model}")
    print(f"\n{rec['note']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
