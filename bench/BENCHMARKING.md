# Benchmarking Gio — and which eval tool to use

This is the shareable overview of how Gio's performance is measured and *why we
picked the tools we did* — written for both engineers and business stakeholders.
Everything here runs **in the cloud** (GitHub Actions), so there's nothing to run
on your own machine.

---

## Which eval tool? (Promptfoo vs. DeepEval vs. Braintrust)

All three can run in the cloud — an eval running inside a **GitHub Actions runner**
on every push/PR *is* "the cloud"; the compute is on GitHub's servers, not a laptop.
So "can it run in the cloud without me running anything locally?" is **yes for all
three**. The real differences are *who recognizes the tool* and *where the results
are visualized*.

| Tool | Recognition (who's impressed) | Cloud / no-local story | Fit for Gio |
|---|---|---|---|
| **Promptfoo** | **Strongest with developers & the OSS community** — the de-facto open-source LLM eval CLI. Config-driven YAML; a **native GitHub Action that comments the results table directly on the PR**. | Runs entirely inside GitHub Actions. Optional hosted `share` link. | **Already integrated** (`eval/promptfoo/`). Zero new paradigm. Free. |
| **Braintrust** | **Strongest with businesses & buyers** — a polished **hosted dashboard**, dataset versioning, and **metric-drift tracking** over time, with enterprise brand-name customers. | Most turnkey for *visualization* — results live on a hosted dashboard, nothing local. | Add-on: push headline numbers to a dashboard for exec-facing sharing. |
| **DeepEval** | Strong with **ML/Python engineers** ("pytest for LLMs"); hosted dashboard via Confident AI. | Runs in CI; hosted dashboard via Confident AI. | Would add a *second* eval paradigm next to Promptfoo — redundant here. |

### Recommendation

Because the audience is **both developers and businesses**, use two tools for two
jobs rather than forcing one to do both:

- **Promptfoo as the CI backbone** — it's already in the repo, it's free, it's the
  name developers recognize, and its GitHub Action **comments the eval on every PR**.
  This is the developer-facing credibility.
- **Braintrust as the optional business dashboard** — push the headline metrics to a
  hosted experiment so non-technical stakeholders get a clean, shareable view that
  tracks drift over time. This is the buyer-facing polish.
- **Skip DeepEval here** — it's a fine tool, but it duplicates what Promptfoo already
  provides and would add a second paradigm to maintain.

Both are wired up in [`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml);
Braintrust stays dormant until you add a `BRAINTRUST_API_KEY` secret.

---

## Try it on your own repo (zero setup)

Hand this to anyone — no API key, no labels, nothing leaves their machine. From
inside a project with the plugin installed:

```
/gio-benchmark
```

or directly: `python3 bench/self_benchmark.py --root /path/to/repo`. It builds
Gio's index, runs probe questions, and reports the **input-token reduction** —
Gio's targeted spans vs. reading the whole files — as a ratio, a token count, and
a projected dollar figure, written to a shareable `GIO_SELF_REPORT.md` (and, with
`--html`, a self-contained `GIO_SAVINGS.html` dashboard you can screenshot). Add
`--with-usage` for real savings from the user's own Claude Code logs.

**Repo size matters:** below ~2–4k LOC the whole repo is cheap to read (modest
absolute savings); the sweet spot is **~10k–200k LOC** (~5–18× fewer tokens); above
~750k–2M LOC Gio hits its 50k-chunk index cap and truncates.

---

## How Gio is benchmarked (three layers, cheapest first)

Gio makes two headline claims — **better code retrieval** and **lower token/cost**.
Three layers measure them, escalating in cost and fidelity. All are driven from CI so
you never run anything locally.

### 1. Retrieval quality — free, deterministic, every push & PR

[`scripts/eval_retrieval.py`](../scripts/eval_retrieval.py) scores hit@1, hit@5,
MRR@10, tokens-to-task, and latency over the labeled golden set in
[`eval/`](../eval/). No LLM, no API key, no cost — fully reproducible.

CI runs it through a regression gate,
[`bench/ci_gate.py`](ci_gate.py), which **fails the build if hit@5 drops below the
floor** (0.80). This is the always-on guard in the `retrieval-eval` job.

```bash
python3 bench/ci_gate.py --min-hit5 0.80    # what CI runs
python3 scripts/eval_retrieval.py           # full human-readable report
```

### 2. Answer quality — Promptfoo LLM-judge, on PRs (needs a key)

[`eval/promptfoo/`](../eval/promptfoo/) asks a model to answer each golden query using
*only* the top-5 chunks Gio retrieved, then an LLM-rubric judge grades whether the
answer names the right file/function. Better retrieval → better-grounded answers,
scored by an independent, widely-used harness.

In CI this is the `answer-quality` job: it builds the index, generates the tests, and
runs the **Promptfoo GitHub Action, which comments the results on the PR**. It runs
only when an `ANTHROPIC_API_KEY` repository secret is set (so forks and keyless runs
skip it cleanly).

### 3. End-to-end lift — SWE-Bench Pro A/B, on demand (paid)

[`bench/swebench_pro_ab.py`](swebench_pro_ab.py) runs Claude Code twice per instance —
bare vs. Gio-installed — and measures **accuracy lift** (scored by ScaleAI's official
SWE-Bench Pro harness) and **token reduction** (measured from usage records). This is
the strongest evidence and the most expensive (~$150–600 a headline run); the full
runbook is in [`bench/README.md`](README.md).

Because a real run needs Claude Code logged in and a Modal account, CI does **not**
run it automatically. The `swebench-pro` job is **manual (`workflow_dispatch`)** and
runs the free offline `--dry-run` to validate the harness wiring; the paid run is
intended for a machine set up per `bench/README.md`.

---

## Setup checklist

| To enable… | Add this repo secret | Result |
|---|---|---|
| Retrieval regression gate | *(nothing)* | Runs free on every push/PR. |
| Promptfoo PR comments | `ANTHROPIC_API_KEY` | Answer-quality table posted on each PR. |
| Braintrust dashboard | `BRAINTRUST_API_KEY` | Headline metrics pushed to a hosted dashboard. |

Secrets live in **Settings → Secrets and variables → Actions**. Nothing runs locally.
