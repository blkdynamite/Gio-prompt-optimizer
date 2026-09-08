# Why hybrid didn't beat single-ranker retrieval — bug, noise, or design?

The first real embedding eval (`eval/RESULTS.md`, 28-query run) showed hybrid
retrieval performing *below* the better of its two inputs — e.g.
`hybrid(fastembed)` MRR 0.76 vs `vector(fastembed)` 0.84 and lexical 0.78.
That contradicts the original spec's "hybrid beats either alone" thesis, so we
investigated whether it's a coding bug, statistical noise, or an inherent
property of the design.

## Verdict: design (not a bug), amplified by small-sample noise.

### 1. The fusion code is correct

`rrf_fuse` (`scripts/retrieval_lib.py`) implements textbook Reciprocal Rank
Fusion: `score(d) = Σ_list weight / (k + rank_in_list + 1)`, k=60. Audited
against the RRF definition — no off-by-one, no truncation-before-fusion, both
ranked lists are populated (fetch_k = max(20, 2k)), priors applied
consistently to both lists. There is no bug in the fusion.

### 2. Equal-weight RRF rewards *agreement* over *peak confidence*

RRF deliberately ignores score magnitude and uses only rank. That is its
strength (no tuning) and, here, its weakness. A document that one strong
retriever ranks #1 but the other ranks poorly gets a *lower* fused score than
a mediocre document both retrievers rank in the middle. Reproduced with the
actual `rrf_fuse` (see the snippet below): when a strong ranker puts the
correct answer at rank 0 and a weak ranker puts it at rank 13, a distractor
sitting at rank 3 in *both* wins the fused #1 slot.

```
vector's #1 pick : GOLD  (correct)     lexical puts GOLD at rank 13
fused top-3      : ['DIST', 'GOLD', 'l0']     # correct answer demoted to #2
DIST fused score : 0.03101  >  GOLD fused score : 0.02991
```

On this benchmark the vector backend (fastembed) is meaningfully stronger than
lexical on *conceptual* queries, so equal-weight fusion averages the strong
ranker **down**. That is exactly the observed conceptual-query drop
(hybrid 84% vs lexical/vector 89% hit@5), and it is a property of the design,
not a defect.

### 3. Magnitude is within noise at n=28

The 0.06–0.08 MRR gaps sit around ~1 standard error at 28 queries, so the
*size* of the penalty is not yet reliable — only the *direction* (hybrid ≤ the
best single ranker) is consistent across backends. The expanded 77-query
golden set (`eval/golden_queries.jsonl`) exists to tighten this.

## Why we don't "fix" it by tuning the fusion

The two obvious fixes both reintroduce problems we explicitly rejected:

- **Weight toward the stronger retriever per query** — but which retriever is
  stronger depends on query type, and detecting that is the query-intent
  classification rejected in D-0007 (complexity without evidence it beats a
  constant).
- **Score-based / convex fusion instead of RRF** — needs score normalization
  and per-repo tuning, rejected in D-0005 (invites overfitting on a 28–77
  query set).

## Consequence

The eval supports shipping a **single ranker** as the default, not hybrid:
lexical BM25 (free, fastest, competitive) with `fastembed` vector as the
optional higher-quality backend. Hybrid stays available behind `--mode hybrid`
but is documented honestly as "no measured benefit over the best single ranker
on our set." Re-run the 77-query eval with embeddings before finalizing the
numbers; if hybrid still shows no gain, drop it from the recommended path.
