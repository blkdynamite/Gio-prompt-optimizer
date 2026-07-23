#!/usr/bin/env python3
"""CI regression gate for Gio's retrieval quality.

Runs the free, deterministic retrieval eval (scripts/eval_retrieval.py, lexical
BM25 path — no API keys, no cost) and fails the build if a headline metric
regresses below a floor. This is the always-on guard that runs on every push and
pull request; the LLM-judged answer-quality eval (eval/promptfoo/) and the paid
SWE-Bench Pro A/B (bench/swebench_pro_ab.py) are separate, opt-in layers.

Usage:
    python3 bench/ci_gate.py                     # default floor: hit@5 >= 0.80
    python3 bench/ci_gate.py --min-hit5 0.85
    python3 bench/ci_gate.py --config "lexical (BM25)" --report out.md

Exit codes: 0 = metric at or above floor; 1 = regression or eval produced no
result for the requested config.
"""
import argparse
import json
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
EVAL = REPO_ROOT / "scripts" / "eval_retrieval.py"


def extract_json(stdout: str) -> dict:
    """Pull the trailing JSON object out of the eval's mixed stdout.

    eval_retrieval.py --json prints progress lines first, then a single
    indent=2 JSON object whose opening brace sits alone on its own line.
    """
    decoder = json.JSONDecoder()
    idx = 0
    while True:
        brace = stdout.find("{", idx)
        if brace == -1:
            raise ValueError("no JSON object found in eval output")
        try:
            # raw_decode stops at the end of the object, ignoring any trailing
            # progress lines (e.g. eval's "Wrote <path>" from --out).
            obj, _ = decoder.raw_decode(stdout, brace)
            return obj
        except json.JSONDecodeError:
            idx = brace + 1


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--min-hit5", type=float, default=0.80,
                   help="Fail if hit@5 for --config drops below this (default 0.80).")
    p.add_argument("--config", default="lexical (BM25)",
                   help="Which eval config's metrics to gate on.")
    p.add_argument("--report", default=None,
                   help="Also write the human-readable markdown report here.")
    args = p.parse_args(argv)

    cmd = [sys.executable, str(EVAL), "--json"]
    if args.report:
        cmd += ["--out", args.report]
    print(f"$ {' '.join(cmd)}", flush=True)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    sys.stdout.write(proc.stdout)
    if proc.stderr:
        sys.stderr.write(proc.stderr)
    if proc.returncode != 0:
        print(f"::error::eval_retrieval.py exited {proc.returncode}", flush=True)
        return proc.returncode

    try:
        results = extract_json(proc.stdout)
    except ValueError as e:
        print(f"::error::{e}", flush=True)
        return 1

    agg = results.get(args.config)
    if not agg:
        print(f"::error::no results for config {args.config!r} "
              f"(got: {', '.join(results) or 'none'})", flush=True)
        return 1

    hit5 = agg["hit5"]
    ok = hit5 >= args.min_hit5
    verb = "PASS" if ok else "FAIL"
    print(f"\n{verb}: {args.config} hit@5 = {hit5:.3f} "
          f"(floor {args.min_hit5:.2f}, n={agg['n']}, MRR {agg['mrr']:.2f})",
          flush=True)
    if not ok:
        print(f"::error::retrieval regression: hit@5 {hit5:.3f} "
              f"below floor {args.min_hit5:.2f}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
