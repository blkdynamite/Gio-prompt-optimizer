# Benchmarking Gio on SWE-Bench Pro (paired A/B)

Measures the two headline claims with third-party scoring: **accuracy lift**
(resolve-rate delta, scored by the official harness) and **token reduction**
(measured per instance from Claude Code's own usage records). Each sampled
instance runs twice with the same model, prompt, and turn cap — bare
(baseline) and with Gio installed + indexes prebuilt — so every instance is
its own control.

## Requirements

- Claude Code CLI logged in; Python 3.11+
- `pip install -r bench/requirements.txt` (just `datasets`, for the instance list)
- The official harness: `git clone https://github.com/scaleapi/SWE-bench_Pro-os`
  and `pip install -r SWE-bench_Pro-os/requirements.txt`
- **A scoring backend** — one of:
  - **Modal (default, recommended):** `pip install modal && modal setup` —
    the harness offloads containerized test execution to Modal's cloud, so
    no local Docker is needed. Test-run compute bills to your Modal account
    (separate from API spend; Modal has a free tier).
  - **Local Docker (beta):** Docker installed, pass `--use_local_docker`;
    prebuilt per-instance images pull from `jefzda/sweap-images`.

## Steps

1. **Plumbing check (free, offline):**
   `python3 bench/swebench_pro_ab.py --dry-run`
2. **Smoke (~$10-30):**
   `python3 bench/swebench_pro_ab.py --sample 5 --model claude-sonnet-5`
3. **Headline (~$150-600 depending on model/turn cap):**
   `python3 bench/swebench_pro_ab.py --sample 75 --model claude-sonnet-5`
   Resumable — rerun the same command to continue after interruptions.
4. **Score each arm** with the official harness (from its repo; Modal by
   default, add `--use_local_docker` to run on local Docker instead):
   `python swe_bench_pro_eval.py --patch_path <gio>/bench/out/predictions_baseline.json --output_dir out_baseline --scripts_dir run_scripts --dockerhub_username jefzda`
   (repeat for `predictions_gio.json` -> `out_gio`)
5. **Report:**
   `python3 bench/report.py --verdicts-baseline <baseline results> --verdicts-gio <gio results>`
   -> `bench/REPORT.md`, ready to paste into the main README.

## Honesty notes

- Verdicts are third-party; tokens are measured; only dollar figures are
  modeled from list prices. Arm B's index build uses zero API tokens (local
  embeddings); its wall-clock is reported separately.
- pass@1, one attempt per arm. Public-repo contamination affects both arms
  equally; the paired design isolates Gio's effect.
