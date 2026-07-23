#!/usr/bin/env python3
"""self_benchmark.py — measure Gio's token reduction on ANY repo, zero setup.

Point this at a codebase and it reports how many tokens Gio's targeted retrieval
feeds into context versus the naive baseline of reading the whole file(s) the
answer lives in — Gio's headline "spend fewer tokens" claim, measured on *your*
code. No API key, no labels, no network (beyond whatever you already cloned):
the lexical (BM25) path runs entirely locally.

For each probe question it builds Gio's index, retrieves the top-k spans, and
compares:
  - Gio cost      = tokens of the retrieved spans          (what an agent reads)
  - naive cost    = tokens of the whole files those spans came from
Both counts come straight from retrieve.run_query (chunk_tokens /
whole_file_tokens), using the same char/4 estimate as the rest of Gio.

Usage:
    python3 bench/self_benchmark.py                     # this repo
    python3 bench/self_benchmark.py --root /path/to/repo
    python3 bench/self_benchmark.py --questions my_questions.txt   # one per line
    python3 bench/self_benchmark.py --with-usage        # + real-session savings
    python3 bench/self_benchmark.py --model opus        # pricing for projected $

Retrieval *quality* (hit@k) needs labeled golden queries and is a separate,
power-user path — see scripts/eval_retrieval.py and eval/golden_queries.jsonl.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import index as index_cli  # noqa: E402
import retrieve as retrieve_cli  # noqa: E402
import impact  # noqa: E402

# Generic probe questions — phrased to hit common concerns in most codebases.
# The token-reduction ratio measures span-vs-whole-file compression, so it is
# robust to exact phrasing; supply --questions for repo-specific queries.
DEFAULT_QUESTIONS = [
    "where is the main entry point",
    "how is configuration loaded",
    "where are errors handled",
    "how is authentication or access control handled",
    "where is input data validated",
    "how are results formatted or rendered",
    "where is logging set up",
    "how are external services or APIs called",
]

SMALL_REPO_CHUNKS = 150  # below this the whole repo is cheap to read anyway


def human(n: int) -> str:
    return f"{n:,}"


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".",
                   help="Repo to benchmark (default: current directory).")
    p.add_argument("--questions",
                   help="File of probe questions, one per line "
                        "(default: a built-in generic set).")
    p.add_argument("--k", type=int, default=8,
                   help="Spans retrieved per question (default 8).")
    p.add_argument("--model", default="sonnet",
                   help="Model id for the projected-$ estimate (default sonnet).")
    p.add_argument("--with-usage", action="store_true",
                   help="Also run scripts/impact.py for real-session savings.")
    p.add_argument("--out", default=str(REPO_ROOT / "bench" / "SELF_REPORT.md"),
                   help="Where to write the shareable markdown report.")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    questions = DEFAULT_QUESTIONS
    if args.questions:
        questions = [ln.strip() for ln in
                     Path(args.questions).read_text().splitlines() if ln.strip()]
    if not questions:
        sys.exit("no questions to run")

    print(f"Indexing {root} ...")
    build = index_cli.build(root, "none", full=True, engine="auto",
                            with_decisions=False)
    chunks, files = build["chunks"], build["files"]
    print(f"Indexed {human(chunks)} chunks from {human(files)} files.")

    rows = []
    gio_total = naive_total = 0
    for q in questions:
        res = retrieve_cli.run_query(root, q, args.k, "lexical", budget_ms=60_000)
        if res.get("error") or not res.get("results"):
            rows.append((q, 0, 0, None))
            continue
        gio = res["chunk_tokens"]
        naive = res["whole_file_tokens"]
        gio_total += gio
        naive_total += naive
        ratio = (naive / gio) if gio else None
        rows.append((q, gio, naive, ratio))
        r = f"{ratio:.1f}x" if ratio else "—"
        print(f"  {r:>6}  gio {human(gio):>7}  naive {human(naive):>8}  {q}")

    saved = naive_total - gio_total
    overall = (naive_total / gio_total) if gio_total else None
    in_price, _out = impact.price_for(args.model)
    saved_usd = saved / 1_000_000 * in_price  # per one pass of these questions

    # ---- report ----
    lines = ["# Gio self-benchmark — token reduction on your repo", ""]
    lines.append(f"Repo: `{root}` — {human(chunks)} chunks, {human(files)} files.")
    if build.get("truncated"):
        lines.append("")
        lines.append("> ⚠️ This repo hit Gio's 50k-chunk index cap — it was "
                     "truncated. Numbers cover the indexed portion.")
    elif chunks < SMALL_REPO_CHUNKS:
        lines.append("")
        lines.append(f"> ℹ️ Small repo ({human(chunks)} chunks). The whole thing "
                     "fits in context cheaply, so absolute savings are modest — "
                     "the ratio still holds, but Gio shines most on ~10k–200k-LOC "
                     "codebases.")
    lines.append("")
    lines.append(f"**Overall: {overall:.1f}× fewer input tokens**" if overall
                 else "**Overall: no retrievable results**")
    lines.append("")
    lines.append(f"- Gio retrieval fed **{human(gio_total)}** tokens across "
                 f"{len(questions)} questions.")
    lines.append(f"- Reading the whole files instead would be "
                 f"**{human(naive_total)}** tokens.")
    lines.append(f"- Saved **{human(saved)}** input tokens "
                 f"≈ **${saved_usd:.2f}** at {args.model} input pricing "
                 f"(one pass; multiply by how often you'd ask).")
    lines.append("")
    lines.append("| ratio | gio tokens | naive tokens | question |")
    lines.append("|---|---|---|---|")
    for q, gio, naive, ratio in rows:
        r = f"{ratio:.1f}x" if ratio else "—"
        lines.append(f"| {r} | {human(gio)} | {human(naive)} | {q} |")
    lines.append("")
    lines.append("_Gio cost = tokens of the retrieved spans; naive cost = tokens "
                 "of the whole files those spans live in (a naive agent opens "
                 "whole files instead of targeted spans). Deterministic, local, "
                 "char/4 token estimate — no API key. Retrieval quality (hit@k) "
                 "is a separate labeled eval; see scripts/eval_retrieval.py._")

    if args.with_usage:
        lines.append("")
        lines.append("## Real-session savings (from your Claude Code logs)")
        lines.append("")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "impact.py")],
            capture_output=True, text=True)
        out = (proc.stdout or proc.stderr).strip()
        lines.append("```")
        lines.append(out)
        lines.append("```")

    report = "\n".join(lines) + "\n"
    Path(args.out).write_text(report)
    print()
    if overall:
        print(f"Overall: {overall:.1f}x fewer input tokens "
              f"(saved {human(saved)} tokens ≈ ${saved_usd:.2f}).")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
