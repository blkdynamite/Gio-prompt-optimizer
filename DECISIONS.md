# Decisions

Append-only log of non-trivial decisions for this repo. Newest at the bottom.
Read before starting architectural/data/auth/dependency/interface work; add an
entry whenever you choose between real alternatives. Supersede, don't delete.

Format per entry:

```
## D-NNNN — <one-line decision>
Date: YYYY-MM-DD  ·  Status: Accepted | Superseded by D-NNNN | Deprecated

Context: <what problem / why a decision was needed>
Decision: <what was chosen>
Alternatives: <options considered and why rejected>
Consequences: <follow-on obligations, migrations, things to keep in sync>
```

---

## D-0001 — Adopt a decision log
Date: 2026-06-26  ·  Status: Accepted

Context: Decisions were living only in people's heads and in diffs, so they got
re-litigated and silently contradicted.
Decision: Maintain this append-only `DECISIONS.md`; read it before non-trivial
work and record decisions worth remembering.
Alternatives: Scatter rationale in PR descriptions (rejected: not discoverable
later); rely on code comments (rejected: no cross-cutting view).
Consequences: Each substantive change should cite or add a decision here.

## D-0002 — Lightweight embedding backends by default; eval decides the winner
Date: 2026-07-22  ·  Status: Accepted

Context: Semantic retrieval needs an embedding model, but Gio's identity is
"lean, local, copy-to-install" and its lexical path must keep working with
zero installs.
Decision: Pluggable `EmbeddingBackend` interface with model2vec
(potion-base-8M, ~30 MB, no torch) as the shipping default, fastembed and
sentence-transformers as installable alternatives, and the documented default
subject to `scripts/eval_retrieval.py` results rather than assertion.
Alternatives: sentence-transformers/MiniLM as sole default (rejected: ~2 GB
torch chain is the wrong weight class for a token-saving skill); API-based
embeddings (rejected: breaks the everything-runs-locally promise and adds
per-query cost).
Consequences: Embeddings are strictly optional extras
(`scripts/requirements-semantic.txt`); every embedding claim in the README
must cite `eval/RESULTS.md`.

## D-0003 — Pure-Python Okapi BM25 for the lexical side of hybrid
Date: 2026-07-22  ·  Status: Accepted

Context: Reciprocal Rank Fusion needs a *ranked* lexical list; grep returns an
unranked set.
Decision: Implement Okapi BM25 (~80 lines, stdlib) in `retrieval_lib.py` over
the same chunk store the embeddings use, with a code-aware tokenizer that
splits snake_case/CamelCase into subtokens while keeping the whole identifier,
and header tokens weighted 3x so definitions outrank mentions (a bug the eval
caught).
Alternatives: `rank_bm25` dependency (rejected: needless install for 80
lines); grep-based pseudo-ranking (rejected: no principled scores to fuse).
Consequences: The lexical path is the always-available floor every
degradation route lands on; keep it dependency-free.

## D-0004 — Brute-force cosine over a numpy matrix; no vector database
Date: 2026-07-22  ·  Status: Accepted

Context: Chunk embeddings need storage and nearest-neighbor search.
Decision: L2-normalized float32 matrix in `.gio/index/embeddings.npz`,
memory-mapped, one matvec per query; cap the index at 50k chunks.
Alternatives: A vector DB server (rejected: wrong weight class for a local
skill); hnswlib now (rejected: premature — below 50k chunks brute force is
milliseconds; the `VectorStore` class is the seam if it's ever needed).
Consequences: Repos exceeding the cap are truncated with a warning; revisit
with hnswlib behind the same interface if that warning ever fires in practice.

## D-0005 — Hybrid fusion via RRF (k=60) with identifier-query lexical boost
Date: 2026-07-22  ·  Status: Accepted

Context: Lexical and vector rankings must merge without per-repo tuning, and
embeddings are weakest exactly where queries contain exact identifiers.
Decision: Reciprocal Rank Fusion with k=60, weighting the lexical list 1.5x
when the query matches identifier patterns (snake_case, CamelCase, quoted
strings, method calls). Retrieval degrades hybrid -> lexical -> ephemeral
in-memory index rather than ever blocking or erroring at the user.
Alternatives: Learned/score-normalized fusion (rejected: needs tuning data and
invites overfitting); vector-only retrieval (rejected: measurably worse on
identifier queries).
Consequences: Fusion behavior is covered by hand-computed tests; any change to
weights must re-run the eval and update `eval/RESULTS.md`.

## D-0006 — Model-tier routing is report-only evidence, never enforcement
Date: 2026-07-22  ·  Status: Accepted

Context: Routing planning to the best model and mechanical work to cheaper
models saves tokens/cost, but no API exposes what tier an account is entitled
to, and Cursor exposes nothing at all.
Decision: `scripts/model_router.py` gathers local, read-only evidence
(settings files, env vars, model ids seen in Claude Code's own usage logs) and
prints a routing recommendation with a confidence field; SKILL.md Part 7 tells
the orchestrating agent how to apply it via subagent model overrides. Unknown
evidence -> "inherit" (no override).
Alternatives: Hard-coding a model table (rejected: goes stale, wrong across
account tiers); probing the API with live calls (rejected: costs money, needs
a key, and "worked once" still isn't entitlement).
Consequences: The router must never write config or block a workflow; logs
prove models *used*, not *entitled*, and the docs must say so.

## D-0007 — Static rank priors: code > docs > tests
Date: 2026-07-22  ·  Status: Accepted

Context: Re-running the eval after the repo grew showed conceptual hit@5
dropping from 83% to 75% — test files (heavy symbol mentioners) and prose
docs (rich in query vocabulary) were outranking the implementing code.
Decision: Multiply both rankers' scores before fusion by a static prior:
0.7 for test files, 0.9 for markdown, 1.0 for code (`rank_prior` in
retrieval_lib.py). Restored hit@5 to 83% and improved vector/hybrid too.
(Percentages in this entry are as measured on the 12-query gio slice of the
original 28-query set on 2026-07-22; current numbers live in eval/RESULTS.md.)
Alternatives: Query-intent classification (rejected: complexity without
evidence it beats a constant); excluding tests entirely (rejected: sometimes
they are the answer).
Consequences: Priors are eval-tuned constants; changing them requires
re-running scripts/eval_retrieval.py and updating eval/RESULTS.md.

## D-0008 — Hybrid retrieval underperforms single-ranker; default to lexical
Date: 2026-07-23  ·  Status: Accepted (pending 77-query embedding confirmation)

Context: The first eval with real embedding backends (28 queries) showed hybrid
RRF below the better of its two inputs, contradicting the spec's "hybrid beats
either alone" thesis. We investigated bug vs noise vs design.
Decision: Confirmed via code audit + reproduction (references/fusion-analysis.md)
that rrf_fuse is correct — the shortfall is inherent to equal-weight RRF, which
rewards agreement over peak confidence and averages a dominant retriever down.
So: ship a single ranker as the default (lexical BM25 — free, fastest,
competitive; fastembed as the optional higher-quality backend), keep hybrid
available behind --mode hybrid but document it as "no measured benefit over the
best single ranker on our set." Golden set expanded from 28 to 77 queries to
tighten the estimates; re-run embeddings on Colab before finalizing.
Alternatives: Per-query fusion weighting (rejected: needs query-intent
classification, see D-0007); score-based fusion (rejected: needs tuning, see
D-0005); keep model2vec default (rejected: it was the weakest backend measured).
Consequences: Supersedes the emphasis in D-0005 on hybrid as the primary path;
D-0002's default-backend choice is pending the 77-query embedding re-run. Any
README claim about hybrid or the default backend must cite eval/RESULTS.md at
77 queries.

## D-0009 — Launch free and open; monetize later with something the files can't provide
Date: 2026-09-06  ·  Status: Accepted

Context: Gio was private with zero installs. A $5 one-time paywall on the
plugin was considered as the launch model.
Decision: Make the repo public and keep the plugin free under MIT. Use a static
landing page (site/, Vercel) as the discovery funnel, with an email list for
a future team tier (Supabase table `gio.launch_signups` behind an insert-only
RPC; Klaviyo was used for one day and dropped to keep the stack in one place). Lead every
public claim with a number that reproduces from a script in the repo
(bench/self_benchmark.py, scripts/eval_retrieval.py) and state its caveat next
to it. Address all bundled scripts via ${CLAUDE_PLUGIN_ROOT} so the plugin
install is the primary path.
Alternatives: $5 paywalled download (rejected: MIT files are freely
redistributable, checkout friction outweighs revenue at zero reach, and a
paywall on a plugin repels the developers who would share it); tip jar at
launch (deferred: no monetization links until there is an audience);
GitHub Pages hosting (rejected in favor of Vercel by the maintainer).
Consequences: Paid features must be things the files alone cannot do (hosted
team dashboard, cross-repo savings history, prebuilt indexes for monorepos).
Any number on the landing page or README must trace to a committed result
(eval/RESULTS.md, a self-benchmark run) and be refreshed when those change.
The plugin version must be bumped for every behavior change so installed users
receive it.
