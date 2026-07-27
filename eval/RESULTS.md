# Retrieval eval results

Generated 2026-07-23 at commit `0d17914` — `python3 scripts/eval_retrieval.py`.
Verified in CI on every push via the `retrieval-eval` job in
[`.github/workflows/benchmark.yml`](../.github/workflows/benchmark.yml)
(the run uploads this exact report as the `retrieval-report` artifact).

Repos evaluated: click (8 queries), express (8 queries), gio (12 queries).

**Skipped (rerun with the semantic extras for the embedding rows):** backend
model2vec (numpy is required for embeddings: `pip install -r scripts/requirements-semantic.txt`).

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | ctx-tokens@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| lexical (BM25) | 68% | 93% | 0.79 | 458 | 1595 | 20 | 26 |

### hit@5 by query type

| config | conceptual | cross-file | identifier |
|---|---|---|---|
| lexical (BM25) | 89% | 100% | 100% |

Methodology: every metric above is deterministic and locally reproducible — no LLM, no API keys, no cost. `tokens-to-task` estimates tokens (chars/4) an agent reads down the ranked list before the first relevant chunk; latency is cold-CLI end-to-end (index load + backend load included, and varies by machine). Relevance labels live in `eval/golden_queries.jsonl`; external repos are pinned in `eval/repos.json`. LLM-judged answer quality is a separate, opt-in harness in `eval/promptfoo/`.
