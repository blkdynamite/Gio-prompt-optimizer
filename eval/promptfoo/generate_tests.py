#!/usr/bin/env python3
"""
Generate promptfoo test cases from the golden query set.

For each golden query and each retrieval mode, runs Gio's retriever, takes
the top-5 chunks, and writes a promptfoo test whose prompt asks a model to
answer using ONLY that context. promptfoo's llm-rubric then grades whether
the answer correctly locates the behavior — an external, third-party-harness
check that better retrieval produces better answers.

This is OPT-IN external validation: it needs promptfoo (Node) and an
ANTHROPIC_API_KEY, and it costs real money. The primary evidence for
retrieval quality remains scripts/eval_retrieval.py, which is deterministic
and free. Run:

    python3 eval/promptfoo/generate_tests.py          # writes tests.yaml
    cd eval/promptfoo && npx promptfoo@latest eval
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import retrieval_lib as rl  # noqa: E402
import retrieve as retrieve_cli  # noqa: E402

MODES = ["lexical", "hybrid"]
TOP_K = 5


def main():
    queries = [json.loads(line) for line in
               (REPO_ROOT / "eval" / "golden_queries.jsonl")
               .read_text().splitlines() if line.strip()]
    queries = [q for q in queries if q["repo"] == "gio"]

    if not (rl.index_dir(REPO_ROOT) / rl.CHUNKS_FILE).is_file():
        print("No index — run: python3 scripts/index.py", file=sys.stderr)
        return 1

    def span_text(r):
        lines = (REPO_ROOT / r["path"]).read_text(errors="ignore").splitlines()
        return "\n".join(lines[r["line_start"] - 1:r["line_end"]])

    tests = []
    for q in queries:
        for mode in MODES:
            res = retrieve_cli.run_query(REPO_ROOT, q["query"], TOP_K, mode,
                                         budget_ms=60_000)
            if res.get("error"):
                continue
            context = "\n\n".join(
                f"--- {r['span']} ({r['symbol']}) ---\n" + span_text(r)
                for r in res["results"])
            reference = "; ".join(
                spec["path"] + (f" ({', '.join(spec['symbols'])})"
                                if spec.get("symbols") else "")
                for spec in q["relevant"])
            tests.append({
                "description": f"[{mode}] {q['query']}",
                "vars": {"query": q["query"], "context": context,
                         "reference": reference, "mode": mode},
                "assert": [{
                    "type": "llm-rubric",
                    "value": ("The answer correctly identifies where in the "
                              "codebase this behavior lives, consistent with "
                              "the reference location(s): {{reference}}. "
                              "Grade pass only if the named file/function "
                              "matches the reference."),
                }],
            })

    out = Path(__file__).parent / "tests.yaml"
    # promptfoo reads YAML; JSON is valid YAML, so emit JSON with stdlib only.
    out.write_text(json.dumps(tests, indent=2))
    print(f"Wrote {len(tests)} tests to {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
