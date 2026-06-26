# Decision Memory

A running, append-only log of every non-trivial decision, so the same questions
aren't re-litigated, context survives across sessions, and reviewers can see
*why* the code is the way it is. This is the project's memory.

## Where it lives

Keep one file per repo:

- Default: `DECISIONS.md` at the repo root (visible, easy to reference in PRs).
- Or, to keep it out of the way: `.claude/memory/DECISIONS.md`.

A starter template is in this skill at `templates/DECISIONS.md` — copy it into
the repo on first use.

## The rule

1. **Read it before non-trivial work.** At the start of any task that touches
   architecture, data shape, auth, dependencies, public interfaces, or anything
   spanning more than a couple of files, read `DECISIONS.md` first. Honor and
   build on prior decisions; don't silently contradict them.
2. **Record any decision worth remembering.** After you choose between real
   alternatives — a library, a data model, an API shape, a naming convention, a
   refactor boundary, a tradeoff — append an entry. If you'd have to explain the
   choice to a teammate, log it.
3. **Reference prior decisions by ID.** When a change depends on or extends an
   earlier decision, cite it (e.g. "per D-0007"). This keeps the codebase
   internally consistent.
4. **Append-only; supersede, don't delete.** When a decision changes, add a new
   entry and mark the old one `Superseded by D-XXXX`. The history is the value.

## What counts as "worth recording"

Record:
- Architecture and module boundaries; where responsibility lives.
- Data model / schema / migration choices.
- Dependency additions or removals (and why this one over alternatives).
- Public interface / API contract shapes.
- Naming and structural conventions adopted for the codebase.
- Tradeoffs accepted (performance vs simplicity, build vs buy, etc.).
- Anything reversed: what was tried, why it didn't work.

Don't record: routine edits, formatting, obvious bug fixes with one possible
fix, anything already captured by the diff itself.

## Entry format

Each entry is short and scannable. Use the next sequential ID.

```
## D-0007 — Use a single `user_profiles` table as the source of truth
Date: 2026-06-26  ·  Status: Accepted

Context: Two surfaces were reading/writing overlapping user fields, drifting
out of sync.
Decision: One canonical table; both surfaces read through one accessor.
Alternatives: Per-surface tables synced by a job (rejected: sync lag, double
writes); a view (rejected: writes still split).
Consequences: Account-affecting ops must touch the one table. Migration 018
backfills. Supersedes D-0003.
```

Status values: `Accepted`, `Superseded by D-XXXX`, `Deprecated`.

## Why this also saves tokens

A decision log is a compact, high-signal context source. Reading one short
`DECISIONS.md` entry is far cheaper than re-deriving a past choice by re-reading
the code that implements it — and it prevents the most expensive waste of all:
redoing work that was already settled.
