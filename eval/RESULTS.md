# Retrieval eval results

Generated 2026-07-22 at commit `f3aa16a` — `python3 scripts/eval_retrieval.py --repos gio --backends model2vec`.

Repos evaluated: gio (12 queries).

**Skipped (rerun on a networked machine for full numbers):** backend model2vec (403 Forbidden).

| config | hit@1 | hit@5 | MRR@10 | tokens-to-task | ctx-tokens@5 | p50 ms | p95 ms |
|---|---|---|---|---|---|---|---|
| lexical (BM25) | 58% | 83% | 0.66 | 486 | 1200 | 8 | 25 |

### hit@5 by query type

| config | conceptual | cross-file | identifier |
|---|---|---|---|
| lexical (BM25) | 71% | 100% | 100% |

Methodology: every metric above is deterministic and locally reproducible — no LLM, no API keys, no cost. `tokens-to-task` estimates tokens (chars/4) an agent reads down the ranked list before the first relevant chunk; latency is cold-CLI end-to-end (index load + backend load included). Relevance labels live in `eval/golden_queries.jsonl`; external repos are pinned in `eval/repos.json`. LLM-judged answer quality is a separate, opt-in harness in `eval/promptfoo/`.
