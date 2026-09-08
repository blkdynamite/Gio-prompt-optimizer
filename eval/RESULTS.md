# Retrieval eval results

Golden set expanded to **77 queries** (gio 27, click 25, express 25; every
referenced path validated against the pinned repos). The lexical baseline
below was regenerated at HEAD; the embedding rows need a re-run on a machine with
HuggingFace access (e.g. Colab) — this sandbox's proxy blocks model downloads.
The lexical row is re-verified in CI on every push via the `retrieval-eval` job in
[`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml)
(the run uploads the regenerated report as the `retrieval-report` artifact).

## Lexical baseline (77 queries)

Generated 2026-09-06 at commit `631985c` — `python3 scripts/eval_retrieval.py --backends none`
(re-run at the current HEAD; the golden set has 27 gio, 25 click, 25 express queries).

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | ctx-tokens@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| lexical (BM25) | 64% | 87% | 0.74 | 557 | 1565 | 19 | 26 |

### hit@5 by query type

| config | conceptual | cross-file | identifier |
|---|---|---|---|
| lexical (BM25) | 84% | 100% | 100% |

## To fill in the embedding rows

On Colab (or any machine with network to HuggingFace):

```bash
pip install -r scripts/requirements-semantic-full.txt
python3 scripts/eval_retrieval.py --backends model2vec,fastembed,st --out eval/RESULTS.md
```

## What the earlier 28-query run told us (superseded by the 77-query set)

The first run with real backends (28 queries) found: lexical BM25 is
remarkably strong and 5–40× faster than any embedding config; among
embedding backends **fastembed** was best and **model2vec** (the placeholder
default) weakest; and **hybrid did not beat the best single ranker** — an
inherent property of equal-weight RRF, not a bug (see
`references/fusion-analysis.md`). Those gaps were within noise at n=28, which
is why the set was expanded to 77. Re-run the command above to confirm the
direction holds on the larger set before finalizing the default backend and
the hybrid recommendation.

Methodology: every metric above is deterministic and locally reproducible — no
LLM, no API keys, no cost. `tokens-to-task` estimates tokens (chars/4) an agent
reads down the ranked list before the first relevant chunk; latency is
cold-CLI end-to-end (index load + backend load included). Relevance labels live
in `eval/golden_queries.jsonl`; external repos are pinned in `eval/repos.json`.
LLM-judged answer quality is a separate, opt-in harness in `eval/promptfoo/`.
