#!/usr/bin/env python3
"""Publish Gio's retrieval metrics to a Braintrust experiment (optional).

Runs the free, deterministic retrieval eval and logs its headline metrics
(hit@1, hit@5, MRR@10, latency) to a Braintrust experiment, giving a hosted
dashboard that non-technical stakeholders can watch over time and that flags
metric drift across commits. This is a business-facing add-on to the primary,
developer-facing evidence (the Promptfoo PR comment + the deterministic eval).

Opt-in: needs `pip install braintrust` and a BRAINTRUST_API_KEY. It is a no-op
with a clear message if either is missing, so it never breaks a build.

Usage:
    export BRAINTRUST_API_KEY=...
    pip install braintrust
    python3 bench/braintrust_publish.py --project "Gio Retrieval"
"""
import argparse
import os
import subprocess
import sys
from pathlib import Path

# Reuse the eval runner + JSON extractor from the CI gate (same directory).
sys.path.insert(0, str(Path(__file__).resolve().parent))
from ci_gate import EVAL, extract_json  # noqa: E402


def run_eval() -> dict:
    cmd = [sys.executable, str(EVAL), "--json"]
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.returncode != 0:
        sys.stderr.write(proc.stderr)
        raise SystemExit(f"eval_retrieval.py exited {proc.returncode}")
    return extract_json(proc.stdout)


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--project", default="Gio Retrieval",
                   help="Braintrust project name (default: 'Gio Retrieval').")
    args = p.parse_args(argv)

    if not os.environ.get("BRAINTRUST_API_KEY"):
        print("BRAINTRUST_API_KEY not set — skipping Braintrust publish "
              "(no-op).")
        return 0
    try:
        import braintrust
    except ImportError:
        print("braintrust not installed (`pip install braintrust`) — "
              "skipping publish (no-op).")
        return 0

    results = run_eval()
    experiment = braintrust.init(project=args.project)
    for config, agg in results.items():
        if not agg:
            continue
        experiment.log(
            input={"config": config},
            output={"n": agg["n"]},
            scores={
                "hit@1": agg["hit1"],
                "hit@5": agg["hit5"],
                "mrr@10": agg["mrr"],
            },
            metadata={
                "config": config,
                "tokens_to_task": agg["tokens_to_task"],
                "ctx_tokens_5": agg["ctx_tokens_5"],
                "p50_ms": agg["p50_ms"],
                "p95_ms": agg["p95_ms"],
            },
        )
    summary = experiment.summarize()
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
