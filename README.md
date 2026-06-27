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

Gio is a folder of files Claude Code reads. Just put it where Claude Code looks
for skills.

**For all your projects:**

```bash
git clone https://github.com/<your-username>/gio.git
mkdir -p ~/.claude/skills
cp -r gio ~/.claude/skills/gio
```

**For one project (and to share with teammates):**

```bash
mkdir -p .claude/skills
cp -r /path/to/gio .claude/skills/gio
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
