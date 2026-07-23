---
description: Benchmark Gio's token reduction on the current repo and show a shareable report
argument-hint: "[--with-usage] [--model opus|sonnet|haiku] [--questions FILE]"
---

Benchmark how many tokens Gio saves on **this** codebase and present the results.

Steps:

1. Run the self-benchmark against the user's current project (no API key needed):

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/bench/self_benchmark.py" --root . --out ./GIO_SELF_REPORT.md $ARGUMENTS
   ```

   It builds Gio's local index, runs a set of probe questions, and compares the
   tokens Gio's targeted spans feed into context vs. reading the whole files
   those spans live in.

2. Read `./GIO_SELF_REPORT.md` and summarize for the user:
   - the **overall N× token reduction** and the tokens / dollars saved,
   - a few rows of the per-question table,
   - the repo size (chunks / files) the script reported.

3. Interpret honestly:
   - If the repo is small (the script prints a "Small repo" note), tell them the
     ratio holds but **absolute** savings are modest, and that Gio shines most on
     **~10k–200k-LOC** codebases.
   - If it hit the 50k-chunk cap, say the numbers cover the indexed portion.

4. Offer the extras:
   - `--with-usage` adds **real-session savings** computed from their own Claude
     Code logs (`scripts/impact.py`), still no API key.
   - For labeled retrieval **quality** (hit@1 / hit@5 / MRR), point them at
     `scripts/eval_retrieval.py` + `eval/golden_queries.jsonl` — that needs
     ground-truth labels, so it's a separate power-user path.

Only report numbers the script actually printed — never invent or round-trip
estimates. The report file is theirs to share.
