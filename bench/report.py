#!/usr/bin/env python3
"""
report.py — join SWE-Bench Pro verdicts with per-run token usage and produce
the paired A/B report (accuracy lift + token reduction).

Inputs:
    bench/out/usage.csv          written by swebench_pro_ab.py
    verdicts for each arm        from the official harness's output. Accepted
                                 formats (auto-detected):
                                   {"resolved": ["id1", ...]}
                                   {"id1": true, "id2": false, ...}
                                   [{"instance_id": "id1", "resolved": true}]

Output: bench/REPORT.md — per-arm resolve rate, paired accuracy delta with a
bootstrap 95% CI, token/cost deltas, and the run manifest. Verdicts are
third-party (the official harness); token counts are measured; only dollar
figures are modeled from list prices.

Usage:
    python3 bench/report.py \
        --verdicts-baseline harness_out_baseline/results.json \
        --verdicts-gio harness_out_gio/results.json
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import statistics
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent

BOOTSTRAP_ROUNDS = 2000
BOOTSTRAP_SEED = 7

TOKEN_COLS = ("input_tokens", "output_tokens", "cache_read_tokens",
              "cache_write_tokens")


def load_verdicts(path):
    """Return {instance_id: bool} from any of the accepted formats."""
    data = json.loads(Path(path).read_text())
    if isinstance(data, dict) and isinstance(data.get("resolved"), list):
        return {iid: True for iid in data["resolved"]}
    if isinstance(data, dict):
        return {str(k): bool(v) for k, v in data.items()}
    if isinstance(data, list):
        return {rec["instance_id"]: bool(rec.get("resolved"))
                for rec in data if "instance_id" in rec}
    raise ValueError(f"Unrecognized verdict format in {path}")


def load_usage(path):
    """Return {(instance_id, arm): row-with-numeric-tokens}."""
    out = {}
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            for col in TOKEN_COLS + ("patch_bytes",):
                row[col] = int(float(row.get(col) or 0))
            row["cost_usd"] = float(row.get("cost_usd") or 0)
            out[(row["instance_id"], row["arm"])] = row
    return out


def processed_tokens(row):
    """Total input the model processed (fresh + cached), matching impact.py."""
    return (row["input_tokens"] + row["cache_read_tokens"]
            + row["cache_write_tokens"])


def paired_instances(usage, v_base, v_gio):
    """Instances with both arms run cleanly; anything else is dropped from
    BOTH arms (preserves pairing) and listed in the report."""
    pairs, dropped = [], []
    for iid in sorted({i for i, _arm in usage}):
        a = usage.get((iid, "baseline"))
        b = usage.get((iid, "gio"))
        if a is None or b is None or a["error"] or b["error"]:
            dropped.append(iid)
            continue
        pairs.append({
            "instance_id": iid, "baseline": a, "gio": b,
            "resolved_baseline": bool(v_base.get(iid, False)),
            "resolved_gio": bool(v_gio.get(iid, False)),
        })
    return pairs, dropped


def bootstrap_ci(values, rounds=BOOTSTRAP_ROUNDS, seed=BOOTSTRAP_SEED):
    """Percentile bootstrap 95% CI for the mean of `values`."""
    if not values:
        return (0.0, 0.0)
    rng = random.Random(seed)
    means = []
    for _ in range(rounds):
        sample = [rng.choice(values) for _ in values]
        means.append(sum(sample) / len(sample))
    means.sort()
    return (means[int(0.025 * rounds)], means[int(0.975 * rounds)])


def build_report(pairs, dropped, manifest):
    n = len(pairs)
    if n == 0:
        return "# Gio on SWE-Bench Pro — paired A/B\n\nNo complete pairs yet.\n"

    rr_base = sum(p["resolved_baseline"] for p in pairs) / n
    rr_gio = sum(p["resolved_gio"] for p in pairs) / n
    acc_lo, acc_hi = bootstrap_ci(
        [int(p["resolved_gio"]) - int(p["resolved_baseline"]) for p in pairs])

    tok_base = [processed_tokens(p["baseline"]) for p in pairs]
    tok_gio = [processed_tokens(p["gio"]) for p in pairs]
    tok_lo, tok_hi = bootstrap_ci([b - g for b, g in zip(tok_base, tok_gio)])
    reduction = (1 - sum(tok_gio) / sum(tok_base)) if sum(tok_base) else 0.0

    out_base = statistics.mean(p["baseline"]["output_tokens"] for p in pairs)
    out_gio = statistics.mean(p["gio"]["output_tokens"] for p in pairs)
    cost_base = sum(p["baseline"]["cost_usd"] for p in pairs)
    cost_gio = sum(p["gio"]["cost_usd"] for p in pairs)
    idx_wall = statistics.mean(
        float(p["gio"]["index_wall_s"] or 0) for p in pairs)

    def per_resolved(cost, rate):
        return f"${cost / (rate * n):,.2f}" if rate else "n/a (0 resolved)"

    lines = [
        "# Gio on SWE-Bench Pro — paired A/B report", "",
        f"Generated {time.strftime('%Y-%m-%d')} · {n} paired instances "
        f"(seed {manifest.get('seed')}, model "
        f"{manifest.get('model') or 'session default'}, max_turns "
        f"{manifest.get('max_turns')})", "",
        "Verdicts come from the official SWE-Bench Pro harness "
        "(third-party); token counts are measured from Claude Code's own "
        "usage records; dollar figures are modeled from list prices.", "",
        "| metric | baseline | with Gio | delta |",
        "|---|---|---|---|",
        f"| resolve rate | {rr_base:.1%} | {rr_gio:.1%} | "
        f"**{rr_gio - rr_base:+.1%}** (95% CI {acc_lo:+.1%}..{acc_hi:+.1%}) |",
        f"| processed input tokens, mean/instance | "
        f"{statistics.mean(tok_base):,.0f} | {statistics.mean(tok_gio):,.0f} "
        f"| **{reduction:.1%} reduction** "
        f"(delta CI {tok_lo:,.0f}..{tok_hi:,.0f}) |",
        f"| output tokens, mean/instance | {out_base:,.0f} | {out_gio:,.0f} | |",
        f"| cost, total | ${cost_base:,.2f} | ${cost_gio:,.2f} | "
        f"${cost_gio - cost_base:+,.2f} |",
        f"| cost per resolved instance | {per_resolved(cost_base, rr_base)} | "
        f"{per_resolved(cost_gio, rr_gio)} | |",
        f"| Gio index build (local, zero API tokens) | — | "
        f"{idx_wall:.0f}s mean wall-clock | |",
        "",
    ]
    if dropped:
        lines += ["Dropped (incomplete pair or run error, excluded from both "
                  "arms): " + ", ".join(dropped), ""]
    lines += [
        "## Per-instance detail", "",
        "| instance | resolved (base->gio) | processed tokens (base->gio) |",
        "|---|---|---|",
    ]
    for p in pairs:
        lines.append(
            f"| {p['instance_id']} | "
            f"{'yes' if p['resolved_baseline'] else 'no'}->"
            f"{'yes' if p['resolved_gio'] else 'no'} | "
            f"{processed_tokens(p['baseline']):,}->"
            f"{processed_tokens(p['gio']):,} |")
    lines += [
        "",
        "Caveats: pass@1, one attempt per arm. The paired design isolates "
        "Gio's effect from model and prompt choice; public-repo "
        "contamination affects both arms equally. The instance sample is "
        "pinned in out/sample.json for reproducibility.",
    ]
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Build the Gio A/B report.")
    p.add_argument("--out", default=str(BENCH_DIR / "out"))
    p.add_argument("--verdicts-baseline", required=True)
    p.add_argument("--verdicts-gio", required=True)
    p.add_argument("--report", default=str(BENCH_DIR / "REPORT.md"))
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    manifest = {}
    if (out_dir / "sample.json").is_file():
        manifest = json.loads((out_dir / "sample.json").read_text())
    pairs, dropped = paired_instances(
        load_usage(out_dir / "usage.csv"),
        load_verdicts(args.verdicts_baseline),
        load_verdicts(args.verdicts_gio))
    report = build_report(pairs, dropped, manifest)
    Path(args.report).write_text(report)
    print(report)
    print(f"Wrote {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
