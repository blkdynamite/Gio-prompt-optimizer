# Gio 🌱

**Your vibe-coding sidekick. Lean, clean, and easy on the planet.**

[![benchmark](https://github.com/blkdynamite/Gio-prompt-optimizer/actions/workflows/benchmark.yml/badge.svg)](https://github.com/blkdynamite/Gio-prompt-optimizer/actions/workflows/benchmark.yml)
[![license: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![plugin 0.4.0](https://img.shields.io/badge/claude%20code%20plugin-0.4.0-blue.svg)](.claude-plugin/plugin.json)

Gio is a free, open-source [Claude Code](https://claude.com/claude-code) plugin
that makes every token go further. Instead of reading whole files, it finds the
exact lines a task needs, hands big lookups to helper agents, and keeps your
context small — so a capped session gets more work done and your bill shrinks.
It also keeps your project tidy, remembers why you made each decision, and
checks the work before it ships.

```
/plugin marketplace add blkdynamite/Gio-prompt-optimizer
/plugin install gio@blkdynamite-plugins
```

**Measured, not promised:** 13.6× fewer input tokens on
[Click](https://github.com/pallets/click), 9.8× on
[FastAPI](https://github.com/fastapi/fastapi), and 45–89% fewer context tokens
on real coding tasks run with and without Gio. Details and caveats below;
everything reproduces on your machine with no API key.

Landing page source lives in [`site/`](site/) (static, deploys to Vercel as-is).

---

## Why you'll want it

Building with AI is amazing — but it's easy to:

- 💸 **Burn money** — bloated prompts and "read the whole codebase" habits cost
  real tokens.
- 🍝 **Grow spaghetti** — every "just add this" creates duplicate, tangled code.
- 🤔 **Forget decisions** — three sessions later, nobody remembers why it's built
  this way.
- 🐛 **Ship bugs** — no review step before things go live.

Gio fixes all four — and estimates the money and carbon you saved doing it.

---

## What it looks like

Ask Gio *"how much have I saved?"* and you get this:

```
============================================================
  Gio — Your Coding Impact
============================================================
  USAGE (actual)
    Spent          : $41.88
    Energy         : 1.225 kWh
    Water          : 1.16 L

  ESTIMATED SAVINGS vs a naive baseline (2.5x input)
    (modeled, not measured)
    Money          : ~$204.38
    Energy         : 1.387 kWh (~114 phone charges)
    Water          : 1.32 L (~5 glasses)
    CO2            : 0.555 kg (~1.4 miles not driven)

  ********************************************************
  MILESTONE! ~$200.00+ estimated savings
  vs a naive workflow — money likely kept in your pocket
  and a lighter footprint. An estimate, but keep it up!
  ********************************************************
```

*(Example numbers from a month of heavy use. Your numbers come from your own
Claude Code logs — nothing leaves your machine.)*

---

## Install (2 minutes)

Inside Claude Code, run:

```
/plugin marketplace add blkdynamite/Gio-prompt-optimizer
/plugin install gio@blkdynamite-plugins
```

That's it. Gio activates when it's relevant. Try: *"add a login screen"*,
*"how much have I saved?"*, *"/gio-benchmark"*, or *"review my changes before I
merge."* Update later with `/plugin marketplace update blkdynamite-plugins`.

<details>
<summary>Manual install (no plugin system)</summary>

Gio is a folder of files Claude Code reads. Put it where Claude Code looks
for skills:

```bash
# for all your projects
git clone --depth 1 https://github.com/blkdynamite/Gio-prompt-optimizer.git ~/.claude/skills/gio

# or for one project (and to share with teammates)
git clone --depth 1 https://github.com/blkdynamite/Gio-prompt-optimizer.git .claude/skills/gio
```

Two differences from the plugin install: the `/gio-benchmark` slash command is
plugin-only (run `python3 ~/.claude/skills/gio/bench/self_benchmark.py --root . --html`
instead), and where the docs say `${CLAUDE_PLUGIN_ROOT}`, use the folder you
cloned into.
</details>

---

## What Gio does

| | |
|---|---|
| 🪶 **Keeps queries lean** | Searches for the exact code instead of reading everything; uses helper agents for big lookups; keeps your context small. |
| 📊 **Shows your impact** | `python3 scripts/impact.py` — money, energy, water, and CO₂ saved, with a milestone celebration. |
| 🧠 **Remembers decisions** | Keeps a simple `DECISIONS.md` log so choices are tracked and never re-argued. |
| 🧹 **Keeps code clean** | Reuses code instead of duplicating it, fixes root causes (not patches), and tidies as it goes. |
| ✅ **Reviews before you ship** | A six-phase check for bugs, security, and architecture before anything merges. |
| 🗺️ **Maps your codebase** | Builds a labeled, regenerable index (`python3 scripts/codebase_map.py`) so it jumps straight to the right file and lines instead of re-scanning the whole project. |
| 🔎 **Finds code by meaning** | Hybrid semantic search (`python3 scripts/retrieve.py "where is login handled"`) — local embeddings + BM25 find the right code even when your words don't match the code's words, and return exact `file:line` spans to read. |
| 🎚️ **Routes work to the right model** | `python3 scripts/model_router.py` — plans on the best model you have access to, hands mechanical work to cheaper ones. Report-only, evidence-based, never blocks. |

Each part has a short guide in [`references/`](references/); the full playbook
Claude follows is in [`SKILL.md`](SKILL.md).

---

## Does it actually cut tokens? A measured demo

We ran the *same* two coding tasks against [Click](https://github.com/pallets/click)
(a real mid-size library) **twice** — once with Gio installed, once without —
using the same model (Claude Sonnet 5) both times. Every patch was applied and
functionally tested, so these are real fixes, not just cheaper transcripts.

| task *(patches verified)* | without Gio | with Gio | reduction |
|---|---|---|---|
| **A** — add a "did you mean?" hint to an error<br>*(both fixes correct)* | 1.15M ctx tokens · $0.52 · 27 turns | 636K · $0.33 · 16 turns | **−45% tokens · −37% cost** |
| **B** — guard an invalid option combination<br>*(Gio matched the spec; the plain run reinterpreted it)* | 1.37M ctx tokens · $0.87 · 28 turns | 147K · $0.11 · 4 turns | **−89% tokens · −88% cost** |

On task B, Gio reached the correct minimal fix in **4 turns for 11¢**, while the
plain run spent **28 turns and 87¢** — and drifted from the literal requirement.
("ctx tokens" = input + cached context the model processes each turn, measured
from Claude Code's own logs.)

**Read this honestly:** it's a *demonstration* (two tasks, one run each), not a
statistical benchmark — and the savings **grow with repo size**, so on Gio's own
tiny repo the gap nearly vanishes. For rigorous, third-party-scored numbers
across many issues (resolve-rate lift + token reduction with confidence
intervals), use the paired A/B harness in [`bench/`](bench/) against SWE-Bench
Pro.

---

## See your savings anytime

Inside Claude Code, just ask *"how much have I saved?"*. From a terminal, run
the calculator from wherever Gio lives (a clone of this repo, or
`~/.claude/skills/gio`):

```bash
python3 scripts/impact.py             # last 30 days
python3 scripts/impact.py --since all # all time
python3 scripts/impact.py --help      # all options
```

It reads the usage logs Claude Code already keeps on your computer. No account,
no upload, no tracking.

---

## Semantic retrieval (optional)

Lexical search fails when your words don't match the code's words — you say
"login screen", the code says `auth` and `session`. Gio's retrieval index
fixes that with **hybrid search**: a zero-install BM25 ranker plus optional
local embeddings, fused so exact identifiers still win where embeddings are
weak.

```bash
# Zero installs — lexical-ranked retrieval works out of the box:
python3 scripts/index.py --backend none
python3 scripts/retrieve.py "where are savings estimated"

# Optional: enable embeddings (lightweight, no torch, ~30 MB model):
pip install -r scripts/requirements-semantic.txt
python3 scripts/index.py
```

**Privacy:** embedding models run locally; code and queries never leave your
machine. The only network access is a one-time model download at *index*
time — never at query time, and never in `--backend none` mode.

### Measured, not asserted

Every retrieval claim is backed by a published eval
([`scripts/eval_retrieval.py`](scripts/eval_retrieval.py) over the labeled
golden set in [`eval/`](eval/)) — deterministic, free, and reproducible on
your machine and re-verified in CI on every push (full table in
[`eval/RESULTS.md`](eval/RESULTS.md); the CI run uploads it as an artifact):

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | p50 latency |
|---|---|---|---|---|---|
| lexical (BM25), zero installs | 64% | 87% | 0.74 | 557 | ~20 ms |

Measured over 77 golden queries across this repo plus two pinned external
repos (`click`, `express`); by query type, lexical hit@5 is 84% conceptual,
100% cross-file, 100% identifier. Regenerated at every release; CI fails
below 80% hit@5.

Embedding backends are opt-in because they need a one-time model download.
Compare them on your own machine with
`python3 scripts/eval_retrieval.py --backends model2vec,fastembed,st` — in
our 28-query run `fastembed` was the strongest and hybrid fusion showed no
measured benefit over the best single ranker
([why](references/fusion-analysis.md)), which is why lexical is the default. There's also an opt-in
[promptfoo](https://promptfoo.dev) harness in
[`eval/promptfoo/`](eval/promptfoo/) that grades end-to-end answer quality
with an LLM judge using your own API key.

**Benchmarked in the cloud.** All of this also runs in GitHub Actions — a free
deterministic retrieval gate on every push/PR, Promptfoo answer-quality comments
on PRs, and an on-demand SWE-Bench Pro A/B — so you don't have to run anything
locally. See [`bench/BENCHMARKING.md`](bench/BENCHMARKING.md) for how it works and
which eval tool to use for which audience.

---

## Benchmark it on your own repo

Want to see the token savings on *your* code (or hand it to a friend)? Install the
plugin, then from inside your project run:

```
/gio-benchmark
```

Or without the plugin, from a clone of this repo:

```bash
python3 bench/self_benchmark.py --root /path/to/your/repo --html
```

No API key, no labels, nothing leaves your machine. It builds Gio's index, runs a
set of probe questions, and reports how many **input tokens** Gio's targeted
retrieval feeds into context versus reading the whole files — a ratio, a token
count, and a projected dollar figure. It writes a shareable `GIO_SELF_REPORT.md`
and, with `--html`, a self-contained **savings dashboard** (`GIO_SAVINGS.html`)
you can open or screenshot. Add `--with-usage` to fold in real savings from your
own Claude Code logs.

**What to expect by repo size** (default 8 probe questions, measured 2026-09-06):

| repo | size | fewer input tokens |
|---|---|---|
| this repo | 32 files, 411 chunks | 6.0× |
| [click 8.1.7](https://github.com/pallets/click) | 75 files, ~10k LOC | 13.6× |
| [fastapi 0.115.0](https://github.com/fastapi/fastapi) | 2,122 files, 15.7k chunks | 9.8× |

Below ~2–4k LOC the whole repo is cheap to read, so absolute savings are modest
(the ratio still holds). The sweet spot is **~10k–200k LOC**.

---

## Honest about the numbers

- **What you spent and used is measured** from your real logs.
- **What you *saved* is an estimate** — it compares your usage to a "naive"
  workflow that reads whole files instead of searching. Tune it with
  `--baseline-multiplier`.
- **Energy and water per query are debated industry estimates**, not audited
  measurements — treat them as order-of-magnitude awareness. Every number and
  its source is listed (and editable) at the top of
  [`scripts/impact.py`](scripts/impact.py); methodology and source links live
  there and in this section's references.

Sources: model pricing (Anthropic), per-query energy/water
([OpenAI, Jun 2025](https://www.datacenterdynamics.com/en/news/sam-altman-chatgpt-queries-consume-034-watt-hours-of-electricity-and-0000085-gallons-of-water/)),
per-token energy ([arXiv:2505.09598](https://arxiv.org/abs/2505.09598)),
equivalents (US EPA).

---

## License

MIT — see [LICENSE](LICENSE). Use it, fork it, share it. 🌍

Gio is an independent open-source project and is not affiliated with or
endorsed by Anthropic. "Claude" and "Claude Code" are trademarks of Anthropic,
PBC. Contributions welcome — see [CONTRIBUTING.md](CONTRIBUTING.md).
