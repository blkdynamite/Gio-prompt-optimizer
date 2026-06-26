# Code Review Process

A six-phase review loop to run **before opening or merging a PR** into a shared
branch (`develop` / `main`). It catches the class of bugs that ship otherwise —
type mismatches, uncovered access paths, missing cache/realtime wiring,
architectural smells. Not optional for substantial changes.

> This is a generalized version of a process used in production. Adapt the
> surfaces (client / server / migrations) to your stack.

## When to run it

Run the full loop after a substantial change: a new feature, a refactor
spanning more than a handful of files, or anything touching auth, access
control, payments, or data migrations.

**Skip it for:** one-line fixes, comment-only or docs changes, pure dependency
bumps with no code changes, and WIP commits inside an exploratory branch (run it
only before the final pre-merge commit).

## The six phases

**Phase 1 — QA Analyst.** Trace every new call against the thing it talks to.
Verify: types match the actual return shapes; access-control / permission rules
cover every new path; cache invalidation and realtime updates are wired where
data changes. For larger changes, spawn parallel `Explore` agents — one per
surface (client code, server/SSR, data layer / migrations) — so no surface gets
a shallow read. Give each agent the specific files and the contract to verify
against; terse prompts produce shallow reviews.

**Phase 2 — Bug report.** Synthesize findings into a prioritized list:
- **P0** — broken behavior, data loss, security/access holes.
- **P1** — wrong-but-survivable, scalability traps.
- **P2** — polish, naming, minor cleanup.

Cite `file:line` on every finding. Also call out what is **correct and must be
preserved** through any refactor, so fixes don't regress it.

**Phase 3 — Root-cause analysis.** For each bug ask: *is this N independent
issues, or one architectural smell showing up N times?* The latter is usually
true. Fixing the shared cause is cheaper than patching each symptom, and it
stops the same shape from recurring. Document the chain.

**Phase 4 — Plan.** Order fixes by blast radius: **P0 first**, then
scalability/refactor, then polish. Defer anything needing a separate data
migration unless the bug actually requires it.

**Phase 5 — Execute.** Fix in order. Refactor for scalability where it reduces
future blast radius; prefer stabilizing shared functions / state shapes over
local patches. Keep migrations additive (new file — never edit a shipped one).
Run the type-checker and the test suite before committing.

**Phase 6 — Commit + push.** One commit per fix bundle, with a body that
documents the **root cause**, not just the diff. Record any architectural
decision in `DECISIONS.md`. Push to the working branch — **do not push directly
to `develop` or `main`**.

## Default agent fan-out

For a mixed change across surfaces, the standard parallel set of `Explore`
agents is one per surface, e.g.:
- Client / UI code review.
- Server / API / SSR review.
- Data layer review (schema, access rules, idempotency, destructive ops,
  privileged-function safety).

Always give each agent the exact files to read and the contract it should verify
against.

## Quick checklist

- [ ] New calls traced to their backend; types match real shapes
- [ ] Every new access path covered by permission/access rules
- [ ] Cache invalidation / realtime updates wired
- [ ] Findings prioritized P0/P1/P2 with `file:line`
- [ ] Root cause identified (one smell vs N bugs)
- [ ] Fixes ordered by blast radius; migrations additive
- [ ] Type-check + tests green
- [ ] Commit documents root cause; decision logged; not pushed to a protected branch
