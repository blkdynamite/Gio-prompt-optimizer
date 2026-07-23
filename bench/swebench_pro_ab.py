#!/usr/bin/env python3
"""
swebench_pro_ab.py — paired A/B of Claude Code with vs. without Gio on
SWE-Bench Pro, measuring accuracy lift AND token reduction.

Design: for each sampled instance, the SAME model with the SAME prompt and
turn cap runs twice on the same repo snapshot —

    arm A ("baseline"): bare repository
    arm B ("gio"):      Gio skill installed in the workspace +
                        retrieval/codebase indexes prebuilt (zero API tokens;
                        embeddings run locally)

Each run's patch goes into a predictions file the official harness
(github.com/scaleapi/SWE-bench_Pro-os) can score, and each run's token usage
is captured from Claude Code's own result JSON (falling back to the per-run
session logs, parsed with impact.py's existing aggregate()). The paired
design makes every instance its own control: accuracy lift is the paired
resolve-rate delta, token reduction is the per-instance token delta.

This script only GENERATES patches and usage data. Scoring happens with the
official harness afterwards (see bench/README.md), so the accuracy number is
third-party, not self-graded.

Usage:
    python3 bench/swebench_pro_ab.py --sample 5              # smoke (~$10-30)
    python3 bench/swebench_pro_ab.py --sample 75             # headline run
    python3 bench/swebench_pro_ab.py --dry-run               # offline plumbing
                                                             # check, no API
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import random
import shutil
import subprocess
import sys
import time
from pathlib import Path

BENCH_DIR = Path(__file__).resolve().parent
REPO_ROOT = BENCH_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))
import impact  # noqa: E402  (reused for token/cost accounting)

DATASET = "ScaleAI/SWE-bench_Pro"
ARMS = ("baseline", "gio")
DEFAULT_OUT = BENCH_DIR / "out"
DEFAULT_CACHE = Path(os.path.expanduser("~/.cache/gio-bench"))
DEFAULT_MAX_TURNS = 60

# Workspace artifacts Gio creates that must NEVER leak into the patch.
WORKSPACE_EXCLUDES = [".claude/", ".gio/", "CODEBASE_MAP.md",
                      ".codebase-map.json"]

PROMPT_TEMPLATE = """You are fixing a real reported issue in this repository.

{problem_statement}

Requirements:
- Implement a fix in the repository working tree.
- Do not modify existing tests.
- Keep the change minimal and consistent with the codebase's style.
- When you are done, make sure the working tree contains your final changes
  (do not commit).
"""

USAGE_FIELDS = ["instance_id", "arm", "model", "input_tokens", "output_tokens",
                "cache_read_tokens", "cache_write_tokens", "cost_usd",
                "num_turns", "wall_s", "index_wall_s", "patch_bytes", "error"]


# --------------------------------------------------------------------------
# Sampling
# --------------------------------------------------------------------------


def load_instances(dataset_file=None):
    """Load SWE-Bench Pro instances (HF datasets, or a JSON fixture file)."""
    if dataset_file:
        return json.loads(Path(dataset_file).read_text())
    try:
        from datasets import load_dataset
    except ImportError:
        sys.exit("pip install -r bench/requirements.txt (needs `datasets`), "
                 "or pass --dataset-file")
    return [dict(row) for row in load_dataset(DATASET, split="test")]


def language_of(inst):
    for key in ("repo_language", "language", "lang"):
        if inst.get(key):
            return str(inst[key]).lower()
    return "unknown"


def pick_sample(instances, n, seed, languages):
    """Deterministic, language-stratified sample. Same seed -> same ids."""
    if languages:
        wanted = {lang.strip().lower() for lang in languages.split(",")}
        instances = [i for i in instances if language_of(i) in wanted]
    by_lang = {}
    for inst in sorted(instances, key=lambda i: i["instance_id"]):
        by_lang.setdefault(language_of(inst), []).append(inst)
    rng = random.Random(seed)
    picked = []
    langs = sorted(by_lang)
    quota = {lang: max(1, round(n * len(by_lang[lang]) / len(instances)))
             for lang in langs}
    for lang in langs:
        pool = by_lang[lang]
        picked.extend(rng.sample(pool, min(quota[lang], len(pool))))
    picked = picked[:n]
    if len(picked) < n:  # top up from the full pool, still deterministic
        remaining = [i for i in sorted(instances,
                                       key=lambda i: i["instance_id"])
                     if i not in picked]
        picked.extend(rng.sample(remaining,
                                 min(n - len(picked), len(remaining))))
    return sorted(picked, key=lambda i: i["instance_id"])


# --------------------------------------------------------------------------
# Workspaces
# --------------------------------------------------------------------------


def run_git(args, cwd=None, check=True):
    return subprocess.run(["git"] + args, cwd=cwd, check=check,
                          capture_output=True, text=True, timeout=600)


def ensure_repo_cache(inst, cache_dir):
    """Full clone (not shallow — base_commit may be old), cached per repo."""
    repo = inst["repo"]
    if repo.startswith("/") or repo.startswith("."):
        return Path(repo).resolve()  # local fixture (dry-run)
    dest = cache_dir / "repos" / repo.replace("/", "__")
    if not (dest / ".git").is_dir():
        dest.parent.mkdir(parents=True, exist_ok=True)
        run_git(["clone", f"https://github.com/{repo}.git", str(dest)])
    return dest


def make_workspace(inst, cache_repo, work_root, arm):
    ws = work_root / f"{inst['instance_id']}--{arm}"
    if ws.exists():
        shutil.rmtree(ws)
    ws.parent.mkdir(parents=True, exist_ok=True)
    run_git(["clone", "--no-hardlinks", str(cache_repo), str(ws)])
    base = inst.get("base_commit")
    if base:
        run_git(["checkout", "--quiet", base], cwd=ws)
    # Keep Gio's artifacts out of `git diff` without touching tracked files.
    exclude = ws / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    with open(exclude, "a") as fh:
        fh.write("\n".join(WORKSPACE_EXCLUDES) + "\n")
    return ws


def install_gio(ws, backend):
    """Arm B prep: copy the skill + build indexes. Local only, no API tokens."""
    t0 = time.time()
    skill_dst = ws / ".claude" / "skills" / "gio"
    skill_dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT, skill_dst,
        ignore=shutil.ignore_patterns(".git", ".gio", "out", "workspaces",
                                      "__pycache__"))
    scripts = skill_dst / "scripts"
    subprocess.run([sys.executable, str(scripts / "codebase_map.py")],
                   cwd=ws, capture_output=True, timeout=600)
    subprocess.run([sys.executable, str(scripts / "index.py"),
                    "--backend", backend],
                   cwd=ws, capture_output=True, timeout=1800)
    return time.time() - t0


def capture_patch(ws):
    run_git(["add", "-A"], cwd=ws)
    return run_git(["diff", "--cached"], cwd=ws).stdout


# --------------------------------------------------------------------------
# Executors
# --------------------------------------------------------------------------


def claude_executor(ws, prompt, model, max_turns, run_dir):
    """Run Claude Code headless; return its result JSON (or an error dict).

    CLAUDE_CONFIG_DIR is pointed at a per-run directory so this run's session
    JSONL logs are isolated — that's what makes per-instance token accounting
    exact rather than a guess.
    """
    env = dict(os.environ, CLAUDE_CONFIG_DIR=str(run_dir))
    cmd = ["claude", "-p", prompt, "--output-format", "json",
           "--max-turns", str(max_turns), "--dangerously-skip-permissions"]
    if model:
        cmd += ["--model", model]
    try:
        proc = subprocess.run(cmd, cwd=ws, env=env, capture_output=True,
                              text=True, timeout=3600)
        return json.loads(proc.stdout.strip().splitlines()[-1])
    except subprocess.TimeoutExpired:
        return {"error": "timeout"}
    except (OSError, json.JSONDecodeError, IndexError) as e:
        return {"error": f"{type(e).__name__}: {e}"}


def dry_run_executor(ws, prompt, model, max_turns, run_dir):
    """Offline stand-in: makes a deterministic edit, returns canned usage."""
    (ws / "GIO_DRYRUN.txt").write_text("dry-run fix for:\n" + prompt[:200])
    is_gio = (ws / ".claude" / "skills" / "gio").is_dir()
    base = 40_000 if is_gio else 90_000  # pretend Gio reads less context
    return {"usage": {"input_tokens": base,
                      "output_tokens": 2_000,
                      "cache_read_input_tokens": base // 2,
                      "cache_creation_input_tokens": base // 4},
            "total_cost_usd": base / 1e6 * 5, "num_turns": 7,
            "model": model or "dry-run"}


def usage_from_result(result, run_dir):
    """Token counts from the result JSON, else from the run's session logs."""
    usage = result.get("usage") or {}
    if usage.get("input_tokens") or usage.get("output_tokens"):
        return {
            "input_tokens": usage.get("input_tokens", 0) or 0,
            "output_tokens": usage.get("output_tokens", 0) or 0,
            "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
            "cache_write_tokens":
                usage.get("cache_creation_input_tokens", 0) or 0,
            "cost_usd": result.get("total_cost_usd", 0) or 0,
        }
    totals, _ = impact.aggregate(
        impact.find_log_files(Path(run_dir) / "projects"), "all")
    summed = {"input_tokens": 0, "output_tokens": 0,
              "cache_read_tokens": 0, "cache_write_tokens": 0, "cost_usd": 0.0}
    for model, tok in totals.items():
        summed["input_tokens"] += tok["input"]
        summed["output_tokens"] += tok["output"]
        summed["cache_read_tokens"] += tok["cache_read"]
        summed["cache_write_tokens"] += tok["cache_write"]
        summed["cost_usd"] += impact.cost_usd(model, tok)
    return summed


# --------------------------------------------------------------------------
# Main sweep
# --------------------------------------------------------------------------


def already_done(out_dir):
    done = set()
    for arm in ARMS:
        path = out_dir / f"predictions_{arm}.json"
        if path.is_file():
            for rec in json.loads(path.read_text()):
                done.add((rec["instance_id"], arm))
    return done


def append_prediction(out_dir, arm, rec):
    path = out_dir / f"predictions_{arm}.json"
    records = json.loads(path.read_text()) if path.is_file() else []
    records.append(rec)
    path.write_text(json.dumps(records, indent=2))


def append_usage(out_dir, row):
    path = out_dir / "usage.csv"
    new = not path.is_file()
    with open(path, "a", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=USAGE_FIELDS)
        if new:
            writer.writeheader()
        writer.writerow(row)


def run_instance_arm(inst, arm, args, executor, out_dir):
    cache_repo = ensure_repo_cache(inst, Path(args.cache))
    ws = make_workspace(inst, cache_repo, Path(args.cache) / "workspaces", arm)
    index_wall = 0.0
    if arm == "gio":
        index_wall = install_gio(ws, args.backend)

    # Absolute: this becomes CLAUDE_CONFIG_DIR for a child process whose cwd
    # is the workspace clone — a relative path would land Claude Code's
    # config/logs inside the workspace and pollute the captured patch.
    run_dir = (out_dir / "runs" / f"{inst['instance_id']}--{arm}").resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    prompt = PROMPT_TEMPLATE.format(
        problem_statement=inst.get("problem_statement", ""))

    t0 = time.time()
    result = executor(ws, prompt, args.model, args.max_turns, run_dir)
    wall = time.time() - t0
    patch = "" if result.get("error") else capture_patch(ws)

    append_prediction(out_dir, arm, {
        "instance_id": inst["instance_id"], "patch": patch,
        "prefix": f"gio-ab-{arm}"})
    usage = usage_from_result(result, run_dir)
    model_used = (result.get("model")
                  or next(iter(result.get("modelUsage") or {}), "")
                  or args.model or "")
    append_usage(out_dir, {
        "instance_id": inst["instance_id"], "arm": arm,
        "model": model_used,
        **usage,
        "num_turns": result.get("num_turns", ""),
        "wall_s": round(wall, 1), "index_wall_s": round(index_wall, 1),
        "patch_bytes": len(patch), "error": result.get("error", "")})
    if not args.keep_workspaces:
        shutil.rmtree(ws, ignore_errors=True)
    return result.get("error")


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Paired A/B of Claude Code +/- Gio on SWE-Bench Pro.")
    p.add_argument("--sample", type=int, default=5,
                   help="Instances to run (default 5 — a smoke test)")
    p.add_argument("--seed", type=int, default=7,
                   help="Sampling seed (same seed = same instances)")
    p.add_argument("--languages", default=None,
                   help="Comma-separated language filter (e.g. python)")
    p.add_argument("--model", default=None,
                   help="Pin the model for BOTH arms (recommended)")
    p.add_argument("--max-turns", type=int, default=DEFAULT_MAX_TURNS)
    p.add_argument("--backend", default="model2vec",
                   help="Gio embedding backend for arm B (default model2vec)")
    p.add_argument("--out", default=str(DEFAULT_OUT))
    p.add_argument("--cache", default=str(DEFAULT_CACHE),
                   help="Repo clone + workspace cache")
    p.add_argument("--dataset-file",
                   help="JSON list of instances (instead of HF download)")
    p.add_argument("--keep-workspaces", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="Offline plumbing check: fixture instance, stub "
                        "executor, no API calls, no cost")
    args = p.parse_args(argv)

    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    if args.dry_run:
        executor = dry_run_executor
        sample = [{"instance_id": "gio-dryrun-1", "repo": str(REPO_ROOT),
                   "base_commit": None,
                   "problem_statement": "Dry-run: exercise the pipeline."}]
    else:
        executor = claude_executor
        instances = load_instances(args.dataset_file)
        sample = pick_sample(instances, args.sample, args.seed, args.languages)

    manifest = {"generated_at": int(time.time()), "seed": args.seed,
                "model": args.model, "max_turns": args.max_turns,
                "backend": args.backend,
                "instance_ids": [i["instance_id"] for i in sample]}
    (out_dir / "sample.json").write_text(json.dumps(manifest, indent=2))

    done = already_done(out_dir)
    print(f"Running {len(sample)} instance(s) x {len(ARMS)} arms "
          f"({len(done)} already done, skipped)")
    for n, inst in enumerate(sample, 1):
        for arm in ARMS:  # interleaved: both arms of an instance together
            if (inst["instance_id"], arm) in done:
                continue
            print(f"[{n}/{len(sample)}] {inst['instance_id']} · {arm} ...",
                  flush=True)
            try:
                err = run_instance_arm(inst, arm, args, executor, out_dir)
            except Exception as e:  # never let one instance kill the sweep
                err = f"{type(e).__name__}: {e}"
                append_usage(out_dir, {"instance_id": inst["instance_id"],
                                       "arm": arm, "model": args.model or "",
                                       "input_tokens": 0, "output_tokens": 0,
                                       "cache_read_tokens": 0,
                                       "cache_write_tokens": 0, "cost_usd": 0,
                                       "num_turns": "", "wall_s": 0,
                                       "index_wall_s": 0, "patch_bytes": 0,
                                       "error": err})
            if err:
                print(f"    error: {err}")

    print(f"\nDone. Outputs in {out_dir}/:")
    print("  predictions_baseline.json / predictions_gio.json  -> score with")
    print("  the official harness (see bench/README.md), then run")
    print("  python3 bench/report.py to join verdicts with usage.csv")
    return 0


if __name__ == "__main__":
    sys.exit(main())
