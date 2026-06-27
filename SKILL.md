---
name: gio
description: >-
  Gio is a vibe-coding sidekick that keeps AI-built projects lean, clean, and
  cheap. It reduces token usage (and the cost / energy / water footprint) per
  Claude Code query, keeps a decision memory so choices are tracked and never
  re-litigated, makes scalable refactor-as-you-go decisions, fixes the common
  mistakes non-technical prompts cause (spaghetti code, duplicate code,
  patch-on-patch fixes), and runs a six-phase code review before merging. Use
  when the user wants to spend fewer tokens, lower their Claude Code bill, keep
  a project clear and maintainable, track decisions, or see how much money and
  environmental impact their efficient habits have saved. Includes an impact
  calculator that reads real usage logs and reports money/energy/water/CO2
  saved at milestones, and a codebase-map generator that builds a labeled,
  regenerable index so agents jump straight to the right file and lines
  (map-then-verify) instead of re-scanning the project each task.
---

# Gio — your vibe-coding sidekick

Gio helps people building with AI — especially newer "vibe coders" — ship
projects that stay lean, clean, and cheap. Most novice prompts accidentally
burn tokens and grow spaghetti; Gio holds a higher bar automatically, even when
the prompt doesn't ask for it.

Tokens are the unit of cost, latency, **and** environmental footprint
(datacenter energy + cooling water), so trimming wasted tokens helps the wallet
and the planet at once.

Gio has six parts:

1. **Token-reduction playbook** — habits to keep each query lean (below).
2. **Impact calculator** — `scripts/impact.py`: reads real usage logs and, at
   milestones, shows money / energy / water / CO₂ saved (below).
3. **Decision memory** — track every non-trivial decision →
   `references/decisions-memory.md` (+ `templates/DECISIONS.md`).
4. **Code quality & scalable decisions** — refactor as you go and fix the
   low-hanging-fruit mistakes → `references/code-quality.md`.
5. **Code review process** — a six-phase loop before merging →
   `references/code-review.md`.
6. **Codebase map** — `scripts/codebase_map.py`: a labeled, regenerable index
   the agent consults (map-then-verify) → `references/codebase-map.md`.

Read the linked reference file when a task calls for that part; the summaries
below say when.

---

## Part 1 — The token-reduction playbook

Apply in priority order. The first group is where almost all the savings are.

### A. Search and read discipline (biggest wins)

The most expensive mistake is pulling more of the codebase into context than the
task needs. Everything read stays in context and is re-sent every turn, so a
wasteful read taxes the whole conversation.

- **Locate before you read.** Use `Grep` / `Glob` to find the exact file and
  line, then read only that. Never read a whole directory tree "to understand
  the codebase."
- **Read ranges, not whole files.** Use `Read` with `offset` / `limit`.
- **Don't re-read** what's already in context.
- **Cap and scope every search** — `head_limit` on `Grep`, `glob` / `type`
  filters — so a search can't return a wall of matches.
- **Prefer the dedicated tools over `cat`/`find`/`grep` in Bash** for tighter
  output.

### B. Delegate heavy exploration to subagents

When answering means sweeping many files, spawn an `Explore` (or
`general-purpose`) subagent. It reads in its **own** context and returns only
the conclusion, so the expensive main context stays small. Biggest multiplier on
large codebases. Fan out independent searches in parallel; give each agent the
specific files/contract to check.

### C. Context hygiene

- **`/clear` between unrelated tasks** — a fresh context is far cheaper.
- **`/compact` at natural breakpoints** in long sessions.
- **Keep `CLAUDE.md` tight** — it loads every session, so bloat taxes every query.
- **Never pipe huge output into context** — redirect long logs to a file and
  `Grep` it.

### D. Prompt and model efficiency

- **Be specific to kill round-trips** — each back-and-forth re-sends the context.
- **Use Plan mode** for complex work so exploration is deliberate.
- **Lean on prompt caching** — a stable prefix (frozen `CLAUDE.md`, unchanging
  tools) is re-served at ~10% price; don't churn early context mid-session.
- **Route trivial tasks to a cheaper model** (e.g. Haiku).

---

## Part 2 — The impact calculator

`scripts/impact.py` reads Claude Code's local usage logs (the per-message JSONL
under `~/.claude/projects/`) and reports money, energy, water, and CO₂ for that
usage — plus an estimate of how much efficient habits **saved** versus a naive
baseline — with relatable equivalents (phone charges, glasses of water, miles
not driven) and a milestone celebration.

Run it when the user asks "how much have I saved / spent / how efficient am I?",
or after a milestone.

```bash
python3 scripts/impact.py                 # last 30 days
python3 scripts/impact.py --since all      # all time
python3 scripts/impact.py --json           # machine-readable
python3 scripts/impact.py --help           # all flags
```

If no logs are found, tell the user where Claude Code writes them and that they
need at least one prior session. See `README.md` for methodology, sources, and
caveats to share with the numbers.

---

## Part 3 — Decision memory

Track every non-trivial decision in an append-only log so the same questions
aren't re-answered, context survives across sessions, and reviewers can see
*why* the code is the way it is.

- **Read it before non-trivial work**, and **record any decision worth
  remembering** (library / data-model / interface / convention / tradeoff
  choices, and anything reversed). Reference prior decisions by ID; supersede,
  never delete.
- Lives at `DECISIONS.md` (repo root) or `.claude/memory/DECISIONS.md`. Copy
  `templates/DECISIONS.md` into the repo on first use.

Full convention and entry format: **`references/decisions-memory.md`**.

---

## Part 4 — Code quality & scalable decisions

Keep the project clear and prevent spaghetti — hold this bar even when the
prompt doesn't ask for it.

- **Scalable by default**: one source of truth, stable interfaces with swappable
  internals, compose instead of copy, name for intent.
- **Refactor as you go, scoped**: leave each file cleaner; fix the shared cause,
  not each call site — without over-engineering for cases that can't happen.
- **Fix the low-hanging-fruit mistakes** non-technical prompts cause (parallel
  duplicate implementations, "just make it work" patches, missing tests, vague
  criteria, god-files, copy-paste, magic numbers, dead code). Spot the pattern,
  apply the fix, and say you did.
- **Call the optimization tooling** instead of eyeballing: `/code-review`,
  `/simplify`, `/security-review`, plus the project's type-checker, tests,
  linter, formatter. Run type-check + tests before every commit.

Full mistake→fix table and tool list: **`references/code-quality.md`**.

---

## Part 5 — Code review process

Before opening or merging a PR into a shared branch for any substantial change,
run the six-phase loop:

1. **QA Analyst** — trace new calls against their backend; verify types, access
   rules, cache/realtime wiring (parallel `Explore` agents per surface).
2. **Bug report** — prioritized P0/P1/P2 with `file:line`; note what to preserve.
3. **Root-cause analysis** — one architectural smell vs N independent bugs.
4. **Plan** — order fixes by blast radius (P0 → scalability → polish).
5. **Execute** — refactor for scalability; additive migrations; type-check + tests.
6. **Commit + push** — one commit per fix bundle documenting the root cause; log
   the decision; never push to a protected branch.

Skip for one-liners, docs, and pure dependency bumps. Full detail and checklist:
**`references/code-review.md`**.

---

## Part 6 — Codebase map

A labeled, regenerable index of the repo — module → file → purpose → key symbols
with line ranges — so you jump straight to the right place instead of
re-discovering the layout every task. It turns Part 1's "locate before you read"
from a per-task search into a one-time index lookup.

- **Read the map first** on any task in an unfamiliar or large repo, instead of
  fanning out search agents just to learn the structure. Then **map-then-verify**:
  `Read` the exact line span the map points to and confirm it still matches
  before acting — the map is a pointer, never the source of truth.
- **Regenerate when code moves** (new files, renames, refactors). `--check` reports
  drift and exits non-zero, so it works as a pre-commit / CI freshness guard.
- **Zero hard dependencies**: stdlib `ast` (Python) + Markdown headings always
  work; it auto-uses tree-sitter or universal-ctags for more languages when
  they're installed.

```bash
python3 scripts/codebase_map.py            # write CODEBASE_MAP.md + .codebase-map.json
python3 scripts/codebase_map.py --check    # report drift vs the stored map (exit 1 if stale)
python3 scripts/codebase_map.py --stdout   # print the map without writing files
python3 scripts/codebase_map.py --help     # all flags
```

Full protocol, tiers, and tuning: **`references/codebase-map.md`**.
