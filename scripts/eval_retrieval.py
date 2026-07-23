#!/usr/bin/env python3
"""
eval_retrieval.py — measure retrieval quality before claiming it works.

Runs the golden query set (eval/golden_queries.jsonl) against real repos and
reports, per configuration (lexical / vector / hybrid × embedding backend):

    hit@1, hit@5        did a relevant chunk appear at rank 1 / in the top 5
    MRR@10              mean reciprocal rank of the first relevant chunk
    tokens-to-task      chunk tokens an agent would read, in rank order,
                        before reaching the first relevant chunk (deterministic
                        proxy for "context cost to find the answer")
    ctx-tokens@5        tokens ingested by taking the whole top-5
    p50/p95 ms          end-to-end query latency (cold CLI semantics: index
                        load + backend load included, matching real usage)

External repos are cloned shallowly at a pinned ref into --repo-cache and
never vendored; offline, they are skipped with a notice and the Gio-repo
subset still runs. Nothing here calls an LLM — every number is locally
reproducible for free. (Answer-quality grading with an LLM judge lives in
eval/promptfoo/, explicitly opt-in.)

Usage:
    python3 scripts/eval_retrieval.py                        # everything
    python3 scripts/eval_retrieval.py --repos gio            # offline subset
    python3 scripts/eval_retrieval.py --backends model2vec   # one backend
    python3 scripts/eval_retrieval.py --out eval/RESULTS.md  # write report
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebase_map as cm  # noqa: E402
import index as index_cli  # noqa: E402
import retrieval_lib as rl  # noqa: E402
import retrieve as retrieve_cli  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CACHE = Path(os.path.expanduser("~/.cache/gio-eval"))
K = 10


def load_queries(repos_wanted):
    path = REPO_ROOT / "eval" / "golden_queries.jsonl"
    queries = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        q = json.loads(line)
        if repos_wanted is None or q["repo"] in repos_wanted:
            queries.append(q)
    return queries


def ensure_repo(name, spec, cache_dir):
    """Return the repo's local root, cloning at the pinned ref if needed."""
    if spec.get("url") is None:
        return REPO_ROOT
    dest = cache_dir / f"{name}-{spec['ref']}"
    if (dest / ".git").is_dir():
        return dest
    cache_dir.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(
            ["git", "clone", "--depth", "1", "--branch", spec["ref"],
             spec["url"], str(dest)],
            check=True, capture_output=True, timeout=300)
        return dest
    except (OSError, subprocess.SubprocessError) as e:
        detail = getattr(e, "stderr", b"")
        if isinstance(detail, bytes):
            detail = detail.decode(errors="ignore")[-200:]
        print(f"  skipping repo '{name}': clone failed ({detail or e})",
              file=sys.stderr)
        return None


def is_relevant(result, relevant_specs):
    for spec in relevant_specs:
        if result["path"] != spec["path"]:
            continue
        symbols = spec.get("symbols")
        if not symbols:
            return True
        if any(s.lower() in result["symbol"].lower() for s in symbols):
            return True
    return False


def eval_one(root, query_rec, mode):
    res = retrieve_cli.run_query(root, query_rec["query"], K, mode,
                                 budget_ms=60_000)
    if res.get("error"):
        return None
    ranks = [i for i, r in enumerate(res["results"], start=1)
             if is_relevant(r, query_rec["relevant"])]
    first = ranks[0] if ranks else None
    tokens_to_task = None
    if first is not None:
        tokens_to_task = sum(r["tokens"] for r in res["results"][:first])
    ctx5 = sum(r["tokens"] for r in res["results"][:5])
    return {"repo": query_rec["repo"], "type": query_rec["type"],
            "query": query_rec["query"], "first_rank": first,
            "hit1": first == 1, "hit5": first is not None and first <= 5,
            "mrr": (1.0 / first) if first else 0.0,
            "tokens_to_task": tokens_to_task, "ctx_tokens_5": ctx5,
            "ms": res["timings"]["total_ms"], "degraded": res["degraded"]}


def aggregate(records):
    n = len(records)
    if n == 0:
        return None
    latencies = sorted(r["ms"] for r in records)
    ttt = [r["tokens_to_task"] for r in records
           if r["tokens_to_task"] is not None]
    return {
        "n": n,
        "hit1": sum(r["hit1"] for r in records) / n,
        "hit5": sum(r["hit5"] for r in records) / n,
        "mrr": sum(r["mrr"] for r in records) / n,
        "tokens_to_task": round(statistics.mean(ttt)) if ttt else None,
        "ctx_tokens_5": round(statistics.mean(
            r["ctx_tokens_5"] for r in records)),
        "p50_ms": latencies[len(latencies) // 2],
        "p95_ms": latencies[min(n - 1, int(0.95 * n))],
    }


def pct(x):
    return f"{100 * x:.0f}%"


def render(configs, queries_by_repo, skipped, args):
    lines = ["# Retrieval eval results", ""]
    lines.append(f"Generated {time.strftime('%Y-%m-%d')} at commit "
                 f"`{cm.git_commit(REPO_ROOT) or 'unknown'}` — "
                 f"`python3 scripts/eval_retrieval.py"
                 + (f" --repos {','.join(args.repos)}" if args.repos else "")
                 + (f" --backends {','.join(args.backends)}"
                    if args.backends else "") + "`.")
    lines.append("")
    counts = ", ".join(f"{repo} ({len(qs)} queries)"
                       for repo, qs in sorted(queries_by_repo.items()))
    lines.append(f"Repos evaluated: {counts}.")
    if skipped:
        lines.append("")
        lines.append("**Skipped (rerun on a networked machine for full "
                     "numbers):** " + "; ".join(skipped) + ".")
    lines.append("")
    lines.append("| config | hit@1 | hit@5 | MRR@10 | tokens-to-task "
                 "| ctx-tokens@5 | p50 ms | p95 ms |")
    lines.append("|---|---|---|---|---|---|---|---|")
    for label, records in configs:
        a = aggregate(records)
        if a is None:
            continue
        lines.append(
            f"| {label} | {pct(a['hit1'])} | {pct(a['hit5'])} "
            f"| {a['mrr']:.2f} | {a['tokens_to_task'] or '—'} "
            f"| {a['ctx_tokens_5']} | {a['p50_ms']:.0f} | {a['p95_ms']:.0f} |")
    lines.append("")
    lines.append("### hit@5 by query type")
    lines.append("")
    types = sorted({r["type"] for _, records in configs for r in records})
    lines.append("| config | " + " | ".join(types) + " |")
    lines.append("|---|" + "---|" * len(types))
    for label, records in configs:
        cells = []
        for t in types:
            sub = [r for r in records if r["type"] == t]
            cells.append(pct(sum(r["hit5"] for r in sub) / len(sub))
                         if sub else "—")
        lines.append(f"| {label} | " + " | ".join(cells) + " |")
    lines.append("")
    lines.append(
        "Methodology: every metric above is deterministic and locally "
        "reproducible — no LLM, no API keys, no cost. `tokens-to-task` "
        "estimates tokens (chars/4) an agent reads down the ranked list "
        "before the first relevant chunk; latency is cold-CLI end-to-end "
        "(index load + backend load included). Relevance labels live in "
        "`eval/golden_queries.jsonl`; external repos are pinned in "
        "`eval/repos.json`. LLM-judged answer quality is a separate, "
        "opt-in harness in `eval/promptfoo/`.")
    return "\n".join(lines) + "\n"


def main(argv=None):
    p = argparse.ArgumentParser(description="Evaluate Gio's retrieval quality.")
    p.add_argument("--repos", help="Comma-separated repo names (default: all)")
    p.add_argument("--backends", default=None,
                   help="Comma-separated embedding backends to evaluate "
                        "(default: model2vec; 'none' for lexical-only)")
    p.add_argument("--repo-cache", default=str(DEFAULT_CACHE),
                   help=f"Clone cache for external repos (default {DEFAULT_CACHE})")
    p.add_argument("--out", help="Write the markdown report to this file")
    p.add_argument("--json", action="store_true", help="JSON to stdout")
    args = p.parse_args(argv)
    args.repos = args.repos.split(",") if args.repos else None
    args.backends = (args.backends.split(",") if args.backends
                     else [rl.DEFAULT_BACKEND])
    args.backends = [b for b in args.backends if b != "none"]

    repo_specs = json.loads((REPO_ROOT / "eval" / "repos.json").read_text())
    queries = load_queries(args.repos)
    cache_dir = Path(os.path.expanduser(args.repo_cache))

    skipped = []
    usable_backends = []
    for b in args.backends:
        try:
            rl.get_backend(b)
            usable_backends.append(b)
        except Exception as e:
            skipped.append(f"backend {b} ({e})")

    roots = {}
    queries_by_repo = {}
    for q in queries:
        queries_by_repo.setdefault(q["repo"], []).append(q)
    for name in sorted(queries_by_repo):
        spec = repo_specs.get(name)
        if spec is None:
            skipped.append(f"repo {name} (not in eval/repos.json)")
            continue
        root = ensure_repo(name, spec, cache_dir)
        if root is None:
            skipped.append(f"repo {name} (clone failed — offline?)")
        else:
            roots[name] = root
    queries = [q for q in queries if q["repo"] in roots]
    queries_by_repo = {k: v for k, v in queries_by_repo.items() if k in roots}

    configs = []  # (label, [record, ...])

    def run_config(label, mode):
        records = []
        for q in queries:
            rec = eval_one(roots[q["repo"]], q, mode)
            if rec is not None:
                records.append(rec)
        configs.append((label, records))
        a = aggregate(records)
        if a:
            print(f"  {label:28} hit@5 {pct(a['hit5'])}  "
                  f"MRR {a['mrr']:.2f}  p50 {a['p50_ms']:.0f}ms")

    print(f"Evaluating {len(queries)} queries over "
          f"{len(roots)} repo(s): {', '.join(sorted(roots))}")
    print("Building lexical indexes...")
    for name, root in roots.items():
        index_cli.build(root, "none", full=True, engine="auto",
                        with_decisions=False)
    run_config("lexical (BM25)", "lexical")

    for backend_name in usable_backends:
        print(f"Building {backend_name} indexes...")
        for name, root in roots.items():
            res = index_cli.build(root, backend_name, full=True,
                                  engine="auto", with_decisions=False)
            if res["embeddings"] != "present":
                skipped.append(f"backend {backend_name} on {name} "
                               f"({'; '.join(res['notices'])})")
        run_config(f"vector ({backend_name})", "vector")
        run_config(f"hybrid ({backend_name})", "hybrid")

    report = render(configs, queries_by_repo, skipped, args)
    if args.json:
        print(json.dumps({label: aggregate(records)
                          for label, records in configs}, indent=2))
    else:
        print()
        print(report)
    if args.out:
        Path(args.out).write_text(report)
        print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
