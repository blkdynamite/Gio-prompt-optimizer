# Semantic retrieval — full protocol

Part 6's hybrid retrieval in depth: when to use it, how it works, how it
degrades, and how its quality claims are measured. Everything runs locally;
nothing leaves the machine.

## Why hybrid, not pure vector

Lexical search fails on **vocabulary mismatch**: the user says "login screen",
the code says `auth` / `session` / `credentials`. Embeddings fix that case —
but pure vector search is often *worse* than lexical for exact identifiers
(function names, error strings), which are most code queries. So Gio runs
both over the same chunk store and fuses with **Reciprocal Rank Fusion**
(k=60, no tuning), boosting the lexical list when the query contains an
identifier (`snake_case`, `CamelCase`, a quoted string, `a.method()`).

## The decision rule for the agent

1. **Exact symbol name known** → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/codebase_map.py" --find NAME`
   (cheapest: one line out).
2. **Conceptual question** ("where is retry handled", "what parses the
   config") → `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/retrieve.py" "..."` and Read only the returned
   spans.
3. **Result looks off, or retrieval degrades** → fall back to Part 1A
   grep/glob discipline. Retrieval is a pointer, never the source of truth —
   always **map-then-verify** by Reading the span before acting.

## How it works

- **Chunking** rides on `codebase_map.py`'s tiered symbol extraction
  (tree-sitter → ctags → ast → regex): one chunk per function/method, class
  headers separate from method bodies, leftover top-level code in "module"
  chunks, markdown by section. Chunks cap at ~512 tokens; oversized symbols
  split into overlapping line windows. Every chunk embeds a **header**
  (`path :: symbol (kind) — signature — docstring line`) with its body —
  paths and symbol names carry strong signal.
- **Lexical**: pure-Python Okapi BM25 with a code-aware tokenizer
  (`find_symbol` indexes as `find_symbol`, `find`, `symbol`). Header tokens
  weigh 3× so a symbol's *definition* outranks chunks merely mentioning it.
  Works with **zero installs**.
- **Semantic**: pluggable local embedding backends —

  | backend | model | install weight | notes |
  |---|---|---|---|
  | `model2vec` (default) | potion-base-8M | ~30 MB, no torch | µs-fast static embeddings |
  | `fastembed` | bge-small-en-v1.5 | ONNX runtime | stronger, still no torch |
  | `st` | all-MiniLM-L6-v2 | torch (~2 GB) | the spec-reference baseline |
  | `hash` | built-in | numpy only | deterministic; tests/CI only, not semantic |

  The shipped default is the *eval-decided* winner among what's installed —
  run `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/eval_retrieval.py" --backends model2vec,fastembed,st` on your
  own repos to see the trade-off in numbers before switching.
- **Storage**: a numpy matrix + brute-force cosine in `.gio/index/`
  (gitignored, always safe to delete). Deliberately **no vector database** —
  wrong weight class for a local skill; below ~50k chunks one matvec is
  milliseconds. `hnswlib` behind the `VectorStore` seam is the escape hatch
  if a repo ever exceeds the cap.
- **Incremental**: files are hashed (same sha the codebase map stores); only
  changed files re-embed. `index.py --check` works as a pre-commit freshness
  guard alongside `codebase_map.py --check`.

## Degradation ladder (production behavior)

Retrieval must never block the user or print a traceback:

| situation | behavior |
|---|---|
| embeddings not installed / model download failed / offline | lexical index still builds; queries answer BM25-only, flagged `degraded` |
| index stale | answers anyway + `stale` notice; rerun `index.py` |
| index missing/corrupt, small repo | one-shot in-memory lexical index, flagged `ephemeral` |
| index missing, large repo | exit 2 with the exact build command |
| query over `--budget-ms` | vector stage skipped, lexical answer, flagged |
| concurrent index builds | exclusive lockfile; readers never blocked (writes are atomic) |
| binary / non-UTF-8 / >500 KB files | skipped at discovery, never crash |

## Evaluation (how we know it's better)

`scripts/eval_retrieval.py` runs a labeled golden query set
(`eval/golden_queries.jsonl` — identifier / conceptual / cross-file queries
over this repo plus click and express at pinned refs) and reports hit@1,
hit@5, MRR@10, **tokens-to-task** (tokens read before the first relevant
chunk), context tokens, and p50/p95 latency per configuration. Results are
committed with the command and commit hash in `eval/RESULTS.md` — measured,
not asserted, same philosophy as `impact.py`. `eval/promptfoo/` adds opt-in
LLM-judged answer grading through promptfoo with your own API key.

## Bonus commands

- `retrieve.py --decisions "task summary"` — surfaces past DECISIONS.md
  entries semantically related to the new task (decision memory that
  resurfaces itself). Decision entries are indexed automatically when
  `DECISIONS.md` exists.
- `retrieve.py --dup-check "prompt"` — reports (never auto-deletes) previous
  prompts with cosine > 0.9 similarity, catching "you asked nearly this
  before" waste. Needs embeddings.

## Privacy

Embedding models run **locally**. The only network access ever made is the
one-time model download from Hugging Face at *index* time (never at query
time). For air-gapped machines: download the model elsewhere and point
`HF_HUB_OFFLINE=1` + a local cache at it, or just use the zero-install
lexical mode (`index.py --backend none`).
