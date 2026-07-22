# Gio 🌱

**Your vibe-coding sidekick. Lean, clean, and easy on the planet.**

Gio is a [Claude Code](https://claude.com/claude-code) skill for people building
with AI — especially if you're newer to coding. It quietly does the things
experienced engineers do automatically: spend fewer tokens, keep your project
tidy, remember why you made each decision, and check the work before it ships.

Less spaghetti. A smaller bill. A lighter footprint. Good vibes.

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

**As a Claude Code plugin (easiest):** inside Claude Code, run

```
/plugin marketplace add blkdynamite/Gio-prompt-optimizer
/plugin install gio@blkdynamite-plugins
```

**Or manually** — Gio is a folder of files Claude Code reads. Just put it
where Claude Code looks for skills.

For all your projects:

```bash
git clone https://github.com/blkdynamite/Gio-prompt-optimizer.git
mkdir -p ~/.claude/skills
cp -r Gio-prompt-optimizer ~/.claude/skills/gio
```

For one project (and to share with teammates):

```bash
mkdir -p .claude/skills
cp -r /path/to/Gio-prompt-optimizer .claude/skills/gio
```

That's it. Open Claude Code and just build — Gio activates when it's relevant.
Try: *"add a login screen"*, *"how much have I saved?"*, or *"review my changes
before I merge."*

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

## See your savings anytime

```bash
python3 scripts/impact.py            # last 30 days
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
your machine. Current committed results
(full table in [`eval/RESULTS.md`](eval/RESULTS.md)):

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | p50 latency |
|---|---|---|---|---|---|
| lexical (BM25) | 50% | 83% | 0.63 | 512 | 11 ms |
| vector / hybrid (embedding backends) | *pending: run locally* | | | | |

The embedding rows need a one-time model download, so they are generated on
your machine: `python3 scripts/eval_retrieval.py --backends model2vec` (add
`,fastembed,st` to compare backends — the measured winner is the right
default for *your* repos). There's also an opt-in
[promptfoo](https://promptfoo.dev) harness in
[`eval/promptfoo/`](eval/promptfoo/) that grades end-to-end answer quality
with an LLM judge using your own API key.

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
