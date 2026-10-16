# Changelog

All notable changes to Gio. Versions follow the `version` field in
`.claude-plugin/plugin.json`; bumping it is what delivers updates to installed
users (`/plugin marketplace update blkdynamite-plugins`).

## 0.5.0 — 2026-10-16

- **New:** session distillation that survives auto-compact. Gio now ships two
  invisible hooks (`PreCompact` and `SessionStart`) plus a manual
  `/gio-distill` command. When the harness is about to auto-compact a long
  session, Gio writes a lean markdown checkpoint to `.gio/sessions/latest.md`
  (user goals, files touched, decisions, last few turns verbatim; stale `Read`
  / `Grep` / `Bash` output dropped). When you next open Claude Code in the
  same project, Gio silently restores that checkpoint as additional context —
  no commands, no setup, no account. See
  [`scripts/distill.py`](scripts/distill.py) for the honest list of what's
  kept vs. dropped. Honors `/clear`; expires checkpoints after 7 days;
  defensively capped at 24 KB of injected context.
- **New:** `scripts/test_distill.py` with golden transcripts.

## 0.4.0 — 2026-09-06

First public release.

- **Fixed:** plugin installs could not find Gio's scripts. `SKILL.md` and the
  references now address every script via `${CLAUDE_PLUGIN_ROOT}` and explain
  the manual-install equivalent.
- **Fixed:** `bench/self_benchmark.py` now writes `GIO_SELF_REPORT.md` in the
  current directory by default, and a bare `--html` writes `GIO_SAVINGS.html`,
  matching the docs and `/gio-benchmark`.
- **Eval:** golden set expanded from 28 to 77 queries (gio 27, click 25,
  express 25); results regenerated at HEAD. Hybrid RRF documented as showing no
  measured benefit over the best single ranker (`references/fusion-analysis.md`,
  D-0008).
- **Docs:** README rewritten for first-time visitors; measured Click demo and
  self-benchmark numbers on `click` (13.6×), `fastapi` (9.8×), and this repo (6.1×).
- **CI:** unit tests run on every push and PR; pinned eval repos are cached;
  concurrent runs on the same ref are cancelled.
- **Repo:** `CONTRIBUTING.md`, `SECURITY.md`, named copyright holder, landing
  page under `site/` (live at gio-prompt-optimizer.vercel.app; notify-me form
  posts to a same-origin, rate-limited serverless endpoint that stores emails
  in Supabase; privacy page at /privacy), tag-triggered release workflow.

## 0.3.0 — 2026-07-27

- `/gio-benchmark` command and `bench/self_benchmark.py`: token reduction on any
  repo, no API key, with an HTML savings dashboard.
- Cloud benchmarking in GitHub Actions: free retrieval gate on every push,
  opt-in Promptfoo answer-quality comments, on-demand SWE-Bench Pro A/B dry run.

## 0.2.0 — 2026-07-22

- Hybrid semantic retrieval (`scripts/index.py`, `scripts/retrieve.py`): chunker,
  BM25, optional local embeddings, RRF fusion, static rank priors.
- Retrieval eval harness with golden queries and deterministic metrics.
- Model-tier routing (`scripts/model_router.py`).
- SWE-Bench Pro paired A/B harness and report generator.

## 0.1.0 — 2026-06-28

- Token-reduction playbook, impact calculator, decision memory, code-quality
  guide, six-phase code review, codebase map generator.
