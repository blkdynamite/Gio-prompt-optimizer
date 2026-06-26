# Code Quality & Scalable Decisions

How to keep a codebase clear and prevent spaghetti — especially when the person
prompting isn't a software engineer. The agent should hold this bar even when
the prompt doesn't ask for it.

## Make scalable decisions by default

"Scalable" here means *cheap to change later* and *hard to break*, not
"distributed system." Concretely:

- **One source of truth.** A value, type, or rule lives in exactly one place;
  everything else references it. Duplication is the root of drift.
- **Stable interfaces, swappable internals.** Put a clear boundary (a function,
  a module) around anything likely to change, so callers don't churn when the
  implementation does.
- **Compose, don't copy.** Three similar blocks → one parameterized function.
  The second copy is the signal to extract.
- **Push complexity to the edges.** Keep the core logic simple; handle messy
  input/validation at system boundaries (user input, external APIs), and trust
  internal code.
- **Name for intent.** Names should say *why*, not *what the line does*. Good
  names remove the need for most comments.

## Refactor as you go — but scope it

Leave each file at least as clean as you found it. When a change reveals
duplication, a too-long function, or a leaky boundary, fix it **as part of the
change** rather than piling another patch on top. Two guardrails:

- **Stabilize, don't sprawl.** Prefer fixing the shared cause (a stable function
  or state shape) over patching each call site.
- **Don't over-engineer.** Don't add abstractions, config, error handling, or
  "future-proofing" for cases that can't happen yet. The simplest thing that
  scales beats a framework nobody needs. Record genuinely load-bearing
  refactors in the decision log.

## Low-hanging-fruit mistakes (and the fix)

These are the cheap wins — the things non-technical prompts routinely omit that
quietly create spaghetti. When you spot the pattern, do the fix even if it
wasn't asked for (and say you did).

| The prompt mistake | What it causes | The fix to apply |
|---|---|---|
| "Add feature X" with no "where" | A second, parallel implementation that drifts from the first | Search for existing code that does it first; extend rather than duplicate |
| "Just make it work" / "quick fix" | Patch-on-patch; the real bug stays | Fix the root cause, not the symptom (see code-review.md) |
| No mention of tests | Silent regressions on the next change | Add/adjust tests for the changed behavior; run the suite |
| Pasting whole files / huge blobs | Bloated context, slow + costly turns | Reference paths; let the agent read only the needed spans |
| Vague "make it better / nicer" | Rework when it's not what they meant | Restate concrete acceptance criteria before building |
| Accepting the first idea | Misses a simpler/cheaper option | Briefly weigh 1–2 alternatives, then recommend one |
| Files that keep growing | God-files no one can navigate | Split by responsibility once a file does too many things |
| Copy-pasted logic | Bugs fixed in one copy, not the others | Extract a shared function; single source of truth |
| Inconsistent naming/structure | Hard-to-read, hard-to-grep codebase | Adopt and follow one convention (record it in DECISIONS.md) |
| Magic numbers / hardcoded strings | Brittle, mysterious behavior | Name them as constants/config |
| Leaving dead code & TODOs | Confusion about what's live | Delete unused code; resolve or ticket TODOs |
| One giant commit | Unreviewable, un-revertable | Commit in logical units with clear messages |

## "Optimization functions" — tools to actually call

Don't hand-eyeball quality. Invoke the tooling that already exists. When working
in Claude Code, reach for:

- **`/code-review`** — find correctness bugs and reuse/simplification issues in
  the current diff (add `--fix` to apply, `--comment` to post on a PR).
- **`/simplify`** — apply reuse/simplification/efficiency cleanups to changed
  code (quality only; pair with `/code-review` for bugs).
- **`/security-review`** — security pass over pending changes on the branch.
- **The project's own checks** — type-checker (e.g. `tsc --noEmit`), test
  suite, linter, and formatter. Run them before committing, every time.
- **`Explore` subagents** — to map unfamiliar areas before changing them, so a
  change fits the existing structure instead of fighting it.

Run the type-checker + tests before any commit; run `/code-review` (or the
six-phase loop in `code-review.md`) before merging anything substantial.
