#!/usr/bin/env python3
"""
retrieve.py — query Gio's hybrid retrieval index. Returns file:line spans.

Hybrid = BM25 (lexical) + cosine over local embeddings, fused with Reciprocal
Rank Fusion; queries containing exact identifiers boost the lexical list.
Results are spans, not files — the agent Reads only the returned lines
(map-then-verify), which is where the token savings come from.

Degradation ladder (never block, never traceback):
    hybrid -> lexical-only        embeddings absent / backend broken / over budget
    stored index -> ephemeral     index missing on a small repo: one-shot
                                  in-memory BM25 (flagged "degraded")
    ephemeral -> exit 2 + advice  index missing on a big repo

Usage:
    python3 retrieve.py "where is retry logic handled"
    python3 retrieve.py "cost_usd cache multiplier" --k 5 --json
    python3 retrieve.py --decisions "switch the database"   # related decisions
    python3 retrieve.py --dup-check "add a login screen"    # similar past prompt
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebase_map as cm  # noqa: E402
import retrieval_lib as rl  # noqa: E402

# Index missing: repos up to this many source files get a one-shot in-memory
# lexical index instead of an error (a few seconds; flagged as degraded).
EPHEMERAL_MAX_FILES = 2000

# Total wall-clock budget per query (ms). Includes embedding-model load, which
# dominates in a cold CLI process; if the lexical stages already spent the
# budget, the vector stage is skipped and the result is flagged degraded.
DEFAULT_BUDGET_MS = 2000

DUP_THRESHOLD = 0.90
PROMPTS_FILE = "prompts.jsonl"


def _span(c):
    return (f"{c.path}:L{c.line_start}"
            if c.line_start == c.line_end
            else f"{c.path}:L{c.line_start}-L{c.line_end}")


def _elapsed_ms(t0):
    return round((time.time() - t0) * 1000, 1)


def run_query(root, query, k, mode, budget_ms):
    """Core retrieval. Returns a result dict (also used by eval_retrieval)."""
    t0 = time.time()
    timings = {}
    degraded = None
    stale = False
    idx_dir = rl.index_dir(root)

    ok, problems, meta = rl.validate_index(idx_dir)
    if not ok:
        # Missing or corrupt index -> ephemeral lexical on small repos.
        n_files = len(cm.discover_files(root))
        if n_files > EPHEMERAL_MAX_FILES:
            return {"error": f"No usable index at {idx_dir} "
                             f"({'; '.join(problems)}) and the repo is large "
                             f"({n_files} files). Build one first: "
                             "python3 scripts/index.py",
                    "exit_code": 2}
        t = time.time()
        chunks, _files, _trunc = rl.chunk_repo(root)
        bm25 = rl.BM25Index.build(chunks)
        timings["ephemeral_build_ms"] = _elapsed_ms(t)
        by_id = {c.id: c for c in chunks}
        ranked = bm25.search(query, k)
        return _format(root, query, ranked, by_id, mode="lexical",
                       degraded="ephemeral-lexical",
                       degraded_reason="; ".join(problems),
                       stale=False, timings=timings, t0=t0, k=k)

    t = time.time()
    chunks = rl.load_chunks(idx_dir / rl.CHUNKS_FILE)
    by_id = {c.id: c for c in chunks}
    bm25 = rl.BM25Index.load(idx_dir / rl.BM25_FILE)
    timings["load_ms"] = _elapsed_ms(t)

    stale = rl.check_index_staleness(root, meta)

    fetch_k = max(20, 2 * k)
    t = time.time()
    lex_ranked = bm25.search(query, fetch_k)
    timings["bm25_ms"] = _elapsed_ms(t)

    vec_ranked = []
    if mode in ("hybrid", "vector"):
        if meta.get("embeddings") != "present":
            degraded, mode = "lexical-only", "lexical"
            degraded_reason = "embeddings absent from index (see index.py notes)"
        elif _elapsed_ms(t0) > budget_ms:
            degraded, mode = "lexical-only", "lexical"
            degraded_reason = f"budget exceeded before vector stage ({budget_ms}ms)"
        else:
            try:
                t = time.time()
                backend = rl.get_backend(meta["backend"]["name"])
                timings["backend_load_ms"] = _elapsed_ms(t)
                t = time.time()
                qvec = backend.embed([query])[0]
                store = rl.VectorStore.load(idx_dir / rl.EMBEDDINGS_FILE)
                vec_ranked = store.search(qvec, fetch_k)
                timings["vector_ms"] = _elapsed_ms(t)
            except Exception as e:
                degraded, mode = "lexical-only", "lexical"
                degraded_reason = f"vector stage failed: {e}"
                vec_ranked = []
    if degraded is None:
        degraded_reason = None

    t = time.time()
    if mode == "lexical" or not vec_ranked:
        ranked = lex_ranked[:k] if mode != "vector" else vec_ranked[:k]
    elif mode == "vector":
        ranked = vec_ranked[:k]
    else:
        lex_weight = (rl.IDENTIFIER_LEXICAL_WEIGHT
                      if rl.is_identifier_query(query) else 1.0)
        ranked = rl.rrf_fuse([lex_ranked, vec_ranked],
                             weights=[lex_weight, 1.0])[:k]
    timings["fuse_ms"] = _elapsed_ms(t)

    return _format(root, query, ranked, by_id, mode=mode, degraded=degraded,
                   degraded_reason=degraded_reason, stale=stale,
                   timings=timings, t0=t0, k=k)


def _format(root, query, ranked, by_id, mode, degraded, degraded_reason,
            stale, timings, t0, k):
    results = []
    chunk_tokens = 0
    file_tokens = 0
    seen_files = set()
    for cid, score in ranked[:k]:
        c = by_id.get(cid)
        if c is None:
            continue
        chunk_tokens += rl.estimate_tokens(c.text)
        if c.path not in seen_files:
            seen_files.add(c.path)
            try:
                file_tokens += rl.estimate_tokens(
                    (root / c.path).read_text(errors="ignore"))
            except OSError:
                pass
        results.append({"span": _span(c), "path": c.path,
                        "line_start": c.line_start, "line_end": c.line_end,
                        "symbol": c.symbol, "kind": c.kind,
                        "score": round(float(score), 4), "header": c.header,
                        "source": c.source,
                        "tokens": rl.estimate_tokens(c.text)})
    timings["total_ms"] = _elapsed_ms(t0)
    return {"query": query, "mode": mode, "degraded": degraded,
            "degraded_reason": degraded_reason, "stale": stale,
            "results": results, "chunk_tokens": chunk_tokens,
            "whole_file_tokens": file_tokens, "timings": timings,
            "exit_code": 0}


def run_decisions(root, query, k):
    """Surface past DECISIONS.md entries related to the task at hand."""
    idx_dir = rl.index_dir(root)
    chunks = []
    try:
        chunks = [c for c in rl.load_chunks(idx_dir / rl.CHUNKS_FILE)
                  if c.source == "decisions"]
    except OSError:
        pass
    if not chunks:
        return {"query": query, "results": [], "exit_code": 0,
                "note": "No indexed decisions. Populate DECISIONS.md and run "
                        "python3 scripts/index.py"}
    bm25 = rl.BM25Index.build(chunks)  # tiny corpus; in-memory is instant
    by_id = {c.id: c for c in chunks}
    ranked = bm25.search(query, k)
    meta = rl.load_meta(idx_dir) or {}
    if meta.get("embeddings") == "present" and ranked is not None:
        try:
            backend = rl.get_backend(meta["backend"]["name"])
            store = rl.VectorStore.load(idx_dir / rl.EMBEDDINGS_FILE)
            decision_ids = set(by_id)
            vec = [(cid, s) for cid, s in
                   store.search(backend.embed([query])[0], 50)
                   if cid in decision_ids][:k]
            ranked = rl.rrf_fuse([ranked, vec])[:k]
        except Exception:
            pass  # lexical ranking already in hand
    results = [{"decision": by_id[cid].symbol, "span": _span(by_id[cid]),
                "title": by_id[cid].header, "score": round(float(s), 4)}
               for cid, s in ranked if cid in by_id]
    return {"query": query, "results": results, "exit_code": 0}


def run_dup_check(root, prompt):
    """Report-only near-duplicate check of a prompt vs prior prompts."""
    idx_dir = rl.index_dir(root)
    meta = rl.load_meta(idx_dir) or {}
    if meta.get("embeddings") != "present":
        return {"prompt": prompt, "duplicates": [], "exit_code": 0,
                "note": "dup-check needs embeddings (run index.py with a "
                        "semantic backend installed)"}
    try:
        backend = rl.get_backend(meta["backend"]["name"])
        qvec = backend.embed([prompt])[0]
    except Exception as e:
        return {"prompt": prompt, "duplicates": [], "exit_code": 0,
                "note": f"dup-check unavailable: {e}"}
    qn = rl.np.linalg.norm(qvec)
    qvec = qvec / qn if qn else qvec

    path = idx_dir / PROMPTS_FILE
    dups = []
    history = []
    if path.is_file():
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if line:
                    history.append(json.loads(line))
    for rec in history:
        vec = rl.np.asarray(rec["vector"], dtype=rl.np.float32)
        cos = float(vec @ qvec)
        if cos > DUP_THRESHOLD:
            dups.append({"prompt": rec["prompt"], "when": rec["when"],
                         "cosine": round(cos, 3)})
    # Append this prompt (hash-deduped) so future checks can see it.
    digest = hashlib.sha256(prompt.encode()).hexdigest()[:12]
    if all(r.get("id") != digest for r in history):
        idx_dir.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps({
                "id": digest, "prompt": prompt[:500],
                "when": time.strftime("%Y-%m-%d"),
                "vector": [round(float(x), 5) for x in qvec]}) + "\n")
    dups.sort(key=lambda d: -d["cosine"])
    return {"prompt": prompt, "duplicates": dups, "exit_code": 0}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Query Gio's hybrid retrieval index (returns file:line spans).")
    p.add_argument("query", help="Natural-language or identifier query")
    p.add_argument("--root", default=".", help="Repo root (default: cwd)")
    p.add_argument("--k", type=int, default=8, help="Results to return")
    p.add_argument("--mode", default="hybrid",
                   choices=["hybrid", "lexical", "vector"])
    p.add_argument("--budget-ms", type=int, default=DEFAULT_BUDGET_MS,
                   help="Wall-clock budget; over it the vector stage is "
                        f"skipped (default {DEFAULT_BUDGET_MS})")
    p.add_argument("--paths-only", action="store_true",
                   help="Print only file:line spans")
    p.add_argument("--decisions", action="store_true",
                   help="Search past DECISIONS.md entries instead of code")
    p.add_argument("--dup-check", action="store_true",
                   help="Report prompts semantically similar to this one")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = p.parse_args(argv)

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    if args.dup_check:
        res = run_dup_check(root, args.query)
    elif args.decisions:
        res = run_decisions(root, args.query, args.k)
    else:
        res = run_query(root, args.query, args.k, args.mode, args.budget_ms)
        if "error" not in res:
            rl.log_usage(root, {
                "when": int(time.time()),
                "query_sha": hashlib.sha256(
                    args.query.encode()).hexdigest()[:12],
                "mode": res["mode"], "degraded": res["degraded"],
                "k": args.k, "chunk_tokens": res["chunk_tokens"],
                "whole_file_tokens": res["whole_file_tokens"]})

    if args.json:
        print(json.dumps(res, indent=2))
        return res.get("exit_code", 0)

    if "error" in res:
        print(res["error"], file=sys.stderr)
        return res.get("exit_code", 2)
    if res.get("note"):
        print(f"note: {res['note']}")
    for note, active in (("stale", res.get("stale")),
                         ("degraded", res.get("degraded"))):
        if active:
            detail = res.get("degraded_reason") if note == "degraded" else \
                "index out of date — rerun python3 scripts/index.py"
            print(f"note: {note}: {detail if detail else active}")
    for r in res.get("results", []):
        if args.paths_only:
            print(r["span"])
        elif "decision" in r:
            print(f"{r['span']}  {r['score']:<8} {r['title']}")
        elif "cosine" in r:
            print(f"{r['cosine']:<6} {r['when']}  {r['prompt']}")
        else:
            print(f"{r['span']}  {r['score']:<8} {r['header']}")
    for d in res.get("duplicates", []):
        print(f"{d['cosine']:<6} {d['when']}  {d['prompt']}")
    if not (res.get("results") or res.get("duplicates")):
        print("(no matches)")
    return res.get("exit_code", 0)


if __name__ == "__main__":
    sys.exit(main())
