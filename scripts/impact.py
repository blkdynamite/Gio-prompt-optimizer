#!/usr/bin/env python3
"""
impact.py — Gio's impact calculator.

Reads Claude Code's local usage logs (the per-message JSONL Claude Code writes
under ~/.claude/projects/) and reports the money, energy, water, and CO2
footprint of your usage — plus an estimate of how much your efficient habits
have SAVED versus a naive baseline.

Nothing is sent anywhere. This only reads local files.

All tunable constants live in the CONSTANTS block below, each with a source.
They are estimates from public 2025-2026 figures; edit them to match data you
trust, and present the output as an estimate, not a precise measurement.

Usage:
    python3 impact.py                       # last 30 days, human-readable
    python3 impact.py --since all           # all time
    python3 impact.py --json                # machine-readable
    python3 impact.py --baseline-multiplier 2.0
    python3 impact.py --help
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from glob import glob
from pathlib import Path

# ---------------------------------------------------------------------------
# CONSTANTS — edit these to taste. Sources noted inline.
# ---------------------------------------------------------------------------

# Per-million-token pricing in USD (input, output). Cache read ~0.1x input,
# cache write ~1.25x input (5-minute TTL). Source: Anthropic pricing, 2026-06.
PRICING = {
    # model-id substring : (input_per_mtok, output_per_mtok)
    "opus":   (5.00, 25.00),
    "sonnet": (3.00, 15.00),
    "haiku":  (1.00,  5.00),
    "fable":  (10.00, 50.00),
}
DEFAULT_PRICE = PRICING["opus"]          # fallback if model unrecognized
CACHE_READ_MULT = 0.10                    # cache read ≈ 0.1x input price
CACHE_WRITE_MULT = 1.25                   # cache write ≈ 1.25x input price (5m TTL)

# Energy per token (Wh). Generation (output) dominates; prefill (input) is much
# cheaper per token. Midpoints of the 0.0001-0.002 Wh/token range reported in
# 2025 LLM-inference energy studies (e.g. arXiv:2505.09598), cross-checked
# against the ~0.3 Wh/query figure (OpenAI, Jun 2025) at ~500 output tokens.
ENERGY_WH_PER_OUTPUT_TOKEN = 0.0005
ENERGY_WH_PER_INPUT_TOKEN = 0.00005       # input/prefill ≈ 10x cheaper than output

# Water (datacenter cooling) tied to energy. The ~0.000085 gal/query figure
# (OpenAI, Jun 2025) ≈ 0.32 mL/query; at ~0.34 Wh/query that is ≈ 0.95 mL/Wh.
WATER_ML_PER_WH = 0.95

# Grid carbon intensity (g CO2 per Wh). ~400 g/kWh = 0.4 g/Wh is a rough global
# average; set this to your region's grid mix for accuracy.
CO2_G_PER_WH = 0.4

# Baseline: how many times the INPUT tokens a naive workflow would have used
# (reading whole files / the codebase instead of targeted search). This is the
# single biggest estimate in the model — keep it honest and configurable.
# 2.5 => a naive workflow reads 2.5x the input tokens you actually used.
DEFAULT_BASELINE_MULTIPLIER = 2.5

# Milestone step for the celebration (USD saved).
DEFAULT_MILESTONE_STEP = 5.0

# Relatable equivalents.
WH_PER_PHONE_CHARGE = 12.2                # EPA: ~0.0122 kWh per smartphone charge
ML_PER_GLASS_OF_WATER = 250.0
CO2_G_PER_MILE_DRIVEN = 404.0             # EPA: ~404 g CO2 / mile, avg gasoline car
WH_PER_LED_HOUR = 10.0                    # a 10W LED bulb for one hour

# ---------------------------------------------------------------------------


def price_for(model: str):
    model = (model or "").lower()
    for key, price in PRICING.items():
        if key in model:
            return price
    return DEFAULT_PRICE


def find_log_files(logs_dir: Path):
    return glob(str(logs_dir / "**" / "*.jsonl"), recursive=True)


def cutoff_seconds(since: str):
    """Return None (all time) or an epoch cutoff."""
    if since == "all":
        return None
    try:
        days = float(since)
    except ValueError:
        days = 30.0
    # Use file mtime rather than parsing every timestamp; good enough for a window.
    return days * 86400.0


def aggregate(files, since):
    """Sum token usage per model across all assistant messages in the logs."""
    import time

    window = cutoff_seconds(since)
    now = time.time()
    totals = {}  # model -> dict of token counts
    files_read = 0

    for fpath in files:
        try:
            if window is not None and (now - os.path.getmtime(fpath)) > window:
                continue
        except OSError:
            continue
        files_read += 1
        try:
            with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = rec.get("message") or {}
                    usage = msg.get("usage")
                    if not usage:
                        continue
                    model = msg.get("model") or rec.get("model") or "unknown"
                    t = totals.setdefault(
                        model,
                        {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0},
                    )
                    t["input"] += usage.get("input_tokens", 0) or 0
                    t["output"] += usage.get("output_tokens", 0) or 0
                    t["cache_read"] += usage.get("cache_read_input_tokens", 0) or 0
                    t["cache_write"] += usage.get("cache_creation_input_tokens", 0) or 0
        except OSError:
            continue

    return totals, files_read


def cost_usd(model, tok):
    inp, out = price_for(model)
    return (
        tok["input"] / 1e6 * inp
        + tok["output"] / 1e6 * out
        + tok["cache_read"] / 1e6 * inp * CACHE_READ_MULT
        + tok["cache_write"] / 1e6 * inp * CACHE_WRITE_MULT
    )


def footprint(input_tokens, output_tokens, co2_g_per_wh):
    energy_wh = (
        input_tokens * ENERGY_WH_PER_INPUT_TOKEN
        + output_tokens * ENERGY_WH_PER_OUTPUT_TOKEN
    )
    water_ml = energy_wh * WATER_ML_PER_WH
    co2_g = energy_wh * co2_g_per_wh
    return energy_wh, water_ml, co2_g


def compute(totals, baseline_multiplier, co2_g_per_wh):
    actual_cost = 0.0
    actual_in = actual_out = actual_cache_read = actual_cache_write = 0
    actual_processed_in = 0
    baseline_cost = 0.0
    baseline_in = 0

    for model, tok in totals.items():
        actual_cost += cost_usd(model, tok)
        actual_in += tok["input"]
        actual_out += tok["output"]
        actual_cache_read += tok["cache_read"]
        actual_cache_write += tok["cache_write"]

        # Total input the model actually processed this period (fresh + cached).
        processed_in = tok["input"] + tok["cache_read"] + tok["cache_write"]
        actual_processed_in += processed_in

        # Baseline: a naive workflow reads more context (whole files instead of
        # targeted spans) AND caches poorly, so it re-sends that larger context
        # at full input price every turn. Model it as the total processed input
        # scaled by the multiplier, billed with no cache discount; output is
        # assumed roughly unchanged. Comparing both against the same processed
        # base keeps cost and footprint savings consistent.
        naive_input = int(processed_in * baseline_multiplier)
        baseline_in += naive_input
        baseline_tok = {
            "input": naive_input,
            "output": tok["output"],
            "cache_read": 0,            # naive workflow caches poorly
            "cache_write": 0,
        }
        baseline_cost += cost_usd(model, baseline_tok)

    a_energy, a_water, a_co2 = footprint(actual_processed_in, actual_out, co2_g_per_wh)
    b_energy, b_water, b_co2 = footprint(baseline_in, actual_out, co2_g_per_wh)

    return {
        "actual_cost": actual_cost,
        "baseline_cost": baseline_cost,
        "saved_cost": max(0.0, baseline_cost - actual_cost),
        "actual": {
            "input_tokens": actual_in,
            "output_tokens": actual_out,
            "cache_read_tokens": actual_cache_read,
            "cache_write_tokens": actual_cache_write,
            "energy_wh": a_energy,
            "water_ml": a_water,
            "co2_g": a_co2,
        },
        "saved": {
            "energy_wh": max(0.0, b_energy - a_energy),
            "water_ml": max(0.0, b_water - a_water),
            "co2_g": max(0.0, b_co2 - a_co2),
        },
    }


def load_state(state_file: Path):
    try:
        return json.loads(state_file.read_text())
    except (OSError, json.JSONDecodeError):
        return {"last_milestone": 0.0}


def save_state(state_file: Path, state):
    try:
        state_file.parent.mkdir(parents=True, exist_ok=True)
        state_file.write_text(json.dumps(state))
    except OSError:
        pass


def fmt_money(x):
    return f"${x:,.2f}"


def human_report(r, files_read, baseline_multiplier, milestone_hit):
    a = r["actual"]
    s = r["saved"]
    lines = []
    lines.append("=" * 60)
    lines.append("  Gio — Your Coding Impact")
    lines.append("=" * 60)
    lines.append(f"  Logs scanned: {files_read} session file(s)")
    lines.append("")
    lines.append("  USAGE (actual)")
    lines.append(f"    Input tokens   : {a['input_tokens']:,}")
    lines.append(f"    Output tokens  : {a['output_tokens']:,}")
    lines.append(f"    Cache reads    : {a['cache_read_tokens']:,}")
    lines.append(f"    Spent          : {fmt_money(r['actual_cost'])}")
    lines.append(f"    Energy         : {a['energy_wh']/1000:.3f} kWh")
    lines.append(f"    Water          : {a['water_ml']/1000:.2f} L")
    lines.append(f"    CO2            : {a['co2_g']/1000:.3f} kg")
    lines.append("")
    lines.append(f"  ESTIMATED SAVINGS vs a naive baseline ({baseline_multiplier}x input)")
    lines.append("    (modeled, not measured — assumes a naive run would use")
    lines.append(f"     {baseline_multiplier}x your input tokens with no caching)")
    lines.append(f"    Money          : ~{fmt_money(r['saved_cost'])}")
    lines.append(
        f"    Energy         : {s['energy_wh']/1000:.3f} kWh "
        f"(~{s['energy_wh']/WH_PER_PHONE_CHARGE:.0f} phone charges, "
        f"~{s['energy_wh']/WH_PER_LED_HOUR:.0f} h of an LED bulb)"
    )
    lines.append(
        f"    Water          : {s['water_ml']/1000:.2f} L "
        f"(~{s['water_ml']/ML_PER_GLASS_OF_WATER:.0f} glasses)"
    )
    lines.append(
        f"    CO2            : {s['co2_g']/1000:.3f} kg "
        f"(~{s['co2_g']/CO2_G_PER_MILE_DRIVEN:.1f} miles not driven)"
    )
    if milestone_hit is not None:
        lines.append("")
        lines.append("  " + "*" * 56)
        lines.append(f"  MILESTONE! ~{fmt_money(milestone_hit)}+ estimated savings ")
        lines.append("  vs a naive workflow — money likely kept in your pocket")
        lines.append("  and a lighter footprint. An estimate, but keep it up!")
        lines.append("  " + "*" * 56)
    lines.append("")
    lines.append("  Figures are estimates from public 2025-2026 data; see")
    lines.append("  README.md for sources and methodology.")
    lines.append("=" * 60)
    return "\n".join(lines)


def main(argv=None):
    p = argparse.ArgumentParser(description="Claude Code query-optimization impact calculator")
    p.add_argument("--logs-dir", default=str(Path.home() / ".claude" / "projects"),
                   help="Directory of Claude Code JSONL logs (default: ~/.claude/projects)")
    p.add_argument("--since", default="30",
                   help="Window in days, or 'all' (default: 30)")
    p.add_argument("--baseline-multiplier", type=float, default=DEFAULT_BASELINE_MULTIPLIER,
                   help=f"Naive-workflow input multiplier (default: {DEFAULT_BASELINE_MULTIPLIER})")
    p.add_argument("--co2-g-per-wh", type=float, default=CO2_G_PER_WH,
                   help=f"Grid carbon intensity, g CO2/Wh (default: {CO2_G_PER_WH})")
    p.add_argument("--milestone-step", type=float, default=DEFAULT_MILESTONE_STEP,
                   help=f"USD-saved milestone step (default: {DEFAULT_MILESTONE_STEP})")
    p.add_argument("--state-file",
                   default=str(Path.home() / ".claude" / "query-optimization-state.json"),
                   help="Where to track cumulative milestones")
    p.add_argument("--json", action="store_true", help="Emit JSON instead of a report")
    args = p.parse_args(argv)

    logs_dir = Path(os.path.expanduser(args.logs_dir))
    if not logs_dir.exists():
        msg = (f"No Claude Code logs found at {logs_dir}.\n"
               "Claude Code writes per-session JSONL there after you use it. "
               "Run at least one session, or pass --logs-dir.")
        print(json.dumps({"error": msg}) if args.json else msg, file=sys.stderr)
        return 1

    files = find_log_files(logs_dir)
    if not files:
        msg = f"No .jsonl session logs under {logs_dir} yet."
        print(json.dumps({"error": msg}) if args.json else msg, file=sys.stderr)
        return 1

    totals, files_read = aggregate(files, args.since)
    r = compute(totals, args.baseline_multiplier, args.co2_g_per_wh)

    # Milestone tracking (cumulative saved cost across the chosen window).
    state_file = Path(os.path.expanduser(args.state_file))
    state = load_state(state_file)
    step = max(0.01, args.milestone_step)
    current_level = int(r["saved_cost"] // step) * step
    milestone_hit = None
    if current_level > state.get("last_milestone", 0.0) and current_level > 0:
        milestone_hit = current_level
        state["last_milestone"] = current_level
        save_state(state_file, state)

    if args.json:
        out = dict(r)
        out["files_read"] = files_read
        out["baseline_multiplier"] = args.baseline_multiplier
        out["milestone_hit"] = milestone_hit
        print(json.dumps(out, indent=2))
    else:
        print(human_report(r, files_read, args.baseline_multiplier, milestone_hit))
    return 0


if __name__ == "__main__":
    sys.exit(main())
