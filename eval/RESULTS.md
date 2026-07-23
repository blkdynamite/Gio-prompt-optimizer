# Retrieval eval results

Generated 2026-07-23 at commit `426701d` — `python3 scripts/eval_retrieval.py --backends model2vec,fastembed,st`.

Repos evaluated: click (8 queries), express (8 queries), gio (12 queries).

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | ctx-tokens@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| lexical (BM25) | 68% | 93% | 0.78 | 467 | 1614 | 38 | 46 |
| vector (model2vec) | 57% | 86% | 0.69 | 490 | 1460 | 221 | 294 |
| hybrid (model2vec) | 64% | 89% | 0.75 | 618 | 1717 | 208 | 302 |
| vector (fastembed) | 75% | 93% | 0.84 | 498 | 1518 | 294 | 416 |
| hybrid (fastembed) | 64% | 89% | 0.76 | 481 | 1611 | 295 | 395 |
| vector (st) | 57% | 93% | 0.72 | 424 | 1265 | 1518 | 1931 |
| hybrid (st) | 71% | 89% | 0.80 | 510 | 1447 | 1531 | 1857 |

### hit@5 by query type

| config | conceptual | cross-file | identifier |
|---|---|---|---|
| lexical (BM25) | 89% | 100% | 100% |
| vector (model2vec) | 79% | 100% | 100% |
| hybrid (model2vec) | 84% | 100% | 100% |
| vector (fastembed) | 89% | 100% | 100% |
| hybrid (fastembed) | 84% | 100% | 100% |
| vector (st) | 89% | 100% | 100% |
| hybrid (st) | 84% | 100% | 100% |

Methodology: every metric above is deterministic and locally reproducible — no LLM, no API keys, no cost. `tokens-to-task` estimates tokens (chars/4) an agent reads down the ranked list before the first relevant chunk; latency is cold-CLI end-to-end (index load + backend load included). Relevance labels live in `eval/golden_queries.jsonl`; external repos are pinned in `eval/repos.json`. LLM-judged answer quality is a separate, opt-in harness in `eval/promptfoo/`.

## Reading of these results (n=28 queries)

- **Lexical BM25 is the standout default**: 93% hit@5, MRR 0.78, and 5–40× faster
  than any embedding config (38 ms vs 221–1531 ms) with zero install. For a
  "lean, local" tool this is the honest default.
- **Among embedding backends, fastembed wins clearly** (vector: hit@1 75%,
  MRR 0.84 — the single best config). model2vec, the earlier placeholder
  default, is the *weakest* backend (MRR 0.69) — it should not be the default.
- **Hybrid did NOT beat the best single ranker on this set.** hybrid(fastembed)
  MRR 0.76 is below both vector(fastembed) 0.84 and lexical 0.78; only
  hybrid(st) edges lexical, and st is 40× slower. This is the expected RRF
  failure mode when one retriever dominates rather than complements the other —
  fusion averages them and drags the stronger one down. It refutes, on this
  benchmark, the "hybrid beats either alone" thesis.
- **Caveat — small sample.** 28 queries means one query flipping moves hit@5 by
  ~4 points; the gaps between the top configs are within plausible noise. A
  larger golden set (~75+) is needed before any of these differences are
  statistically defensible.
