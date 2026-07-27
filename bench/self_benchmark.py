#!/usr/bin/env python3
"""self_benchmark.py — measure Gio's token reduction on ANY repo, zero setup.

Point this at a codebase and it reports how many tokens Gio's targeted retrieval
feeds into context versus the naive baseline of reading the whole file(s) the
answer lives in — Gio's headline "spend fewer tokens" claim, measured on *your*
code. No API key, no labels, no network (beyond whatever you already cloned):
the lexical (BM25) path runs entirely locally.

For each probe question it builds Gio's index, retrieves the top-k spans, and
compares:
  - Gio cost      = tokens of the retrieved spans          (what an agent reads)
  - naive cost    = tokens of the whole files those spans came from
Both counts come straight from retrieve.run_query (chunk_tokens /
whole_file_tokens), using the same char/4 estimate as the rest of Gio.

Usage:
    python3 bench/self_benchmark.py                     # this repo
    python3 bench/self_benchmark.py --root /path/to/repo
    python3 bench/self_benchmark.py --questions my_questions.txt   # one per line
    python3 bench/self_benchmark.py --with-usage        # + real-session savings
    python3 bench/self_benchmark.py --model opus        # pricing for projected $

Retrieval *quality* (hit@k) needs labeled golden queries and is a separate,
power-user path — see scripts/eval_retrieval.py and eval/golden_queries.jsonl.
"""
import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import index as index_cli  # noqa: E402
import retrieve as retrieve_cli  # noqa: E402
import impact  # noqa: E402

# Generic probe questions — phrased to hit common concerns in most codebases.
# The token-reduction ratio measures span-vs-whole-file compression, so it is
# robust to exact phrasing; supply --questions for repo-specific queries.
DEFAULT_QUESTIONS = [
    "where is the main entry point",
    "how is configuration loaded",
    "where are errors handled",
    "how is authentication or access control handled",
    "where is input data validated",
    "how are results formatted or rendered",
    "where is logging set up",
    "how are external services or APIs called",
]

SMALL_REPO_CHUNKS = 150  # below this the whole repo is cheap to read anyway


def human(n: int) -> str:
    return f"{n:,}"


def esc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# Theme-aware CSS. Palette from the data-viz reference instance: Gio = blue
# (categorical slot 1), naive = orange (slot 2) — a CVD-safe adjacent pair.
# Every bar is direct-labeled so identity never rests on color alone.
_CSS = """
:root {
  --plane: #f9f9f7; --surface: #fcfcfb; --ink: #0b0b0b; --ink2: #52514e;
  --muted: #898781; --hair: #e1e0d9; --ring: rgba(11,11,11,.10);
  --gio: #2a78d6; --naive: #eb6834; --good: #006300;
}
@media (prefers-color-scheme: dark) {
  :root {
    --plane: #0d0d0d; --surface: #1a1a19; --ink: #fff; --ink2: #c3c2b7;
    --muted: #898781; --hair: #2c2c2a; --ring: rgba(255,255,255,.10);
    --gio: #3987e5; --naive: #d95926; --good: #0ca30c;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--plane); color: var(--ink);
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif; line-height: 1.5;
}
.wrap { max-width: 820px; margin: 0 auto; padding: 40px 24px 56px; }
.card {
  background: var(--surface); border: 1px solid var(--ring); border-radius: 14px;
  padding: 28px; margin-bottom: 20px;
}
.brand { color: var(--ink2); font-weight: 600; letter-spacing: .01em; margin: 0 0 4px; }
.sub { color: var(--muted); font-size: 14px; margin: 0; }
.hero { display: flex; align-items: baseline; gap: 14px; margin: 8px 0 2px; }
.hero .num { font-size: 68px; font-weight: 700; line-height: 1; color: var(--gio); }
.hero .cap { font-size: 17px; color: var(--ink2); max-width: 380px; }
.tiles { display: grid; grid-template-columns: repeat(3, 1fr); gap: 14px; }
.tile { background: var(--surface); border: 1px solid var(--ring); border-radius: 12px; padding: 18px; }
.tile .v { font-size: 30px; font-weight: 700; }
.tile .v.good { color: var(--good); }
.tile .l { color: var(--muted); font-size: 13px; margin-top: 4px; }
h2 { font-size: 15px; text-transform: uppercase; letter-spacing: .04em; color: var(--ink2); margin: 0 0 16px; }
.legend { display: flex; gap: 18px; margin-bottom: 18px; font-size: 13px; color: var(--ink2); }
.legend span { display: inline-flex; align-items: center; gap: 7px; }
.sw { width: 12px; height: 12px; border-radius: 3px; display: inline-block; }
.sw.gio { background: var(--gio); } .sw.naive { background: var(--naive); }
.q { margin-bottom: 16px; }
.q .ql { font-size: 13px; color: var(--ink2); margin-bottom: 6px; }
.track { display: grid; gap: 5px; }
.bar { height: 18px; border-radius: 4px; display: flex; align-items: center;
  justify-content: flex-end; min-width: 44px; }
.bar.gio { background: var(--gio); } .bar.naive { background: var(--naive); }
.bar .t { color: #fff; font-size: 11px; font-weight: 600; padding-right: 7px;
  font-variant-numeric: tabular-nums; }
.note { color: var(--muted); font-size: 13px; margin-top: 6px; }
.foot { color: var(--muted); font-size: 12px; margin-top: 22px; }
"""


def render_html(root, chunks, files, rows, gio_total, naive_total, saved,
                overall, saved_usd, model, n_questions, truncated, small):
    maxv = max([n for _, _, n, _ in rows] + [1])
    bars = []
    for q, gio, naive, ratio in rows:
        if not gio and not naive:
            continue
        gw = max(gio / maxv * 100, 4)
        nw = max(naive / maxv * 100, 4)
        bars.append(
            f'<div class="q"><div class="ql">{esc(q)}</div>'
            f'<div class="track">'
            f'<div class="bar gio" style="width:{gw:.1f}%" '
            f'title="Gio: {human(gio)} tokens"><span class="t">{human(gio)}</span></div>'
            f'<div class="bar naive" style="width:{nw:.1f}%" '
            f'title="Whole files: {human(naive)} tokens"><span class="t">{human(naive)}</span></div>'
            f'</div></div>')

    banner = ""
    if truncated:
        banner = ('<p class="note">⚠️ This repo hit Gio’s 50k-chunk '
                  'index cap — numbers cover the indexed portion.</p>')
    elif small:
        banner = ('<p class="note">ℹ️ Small repo — the whole thing is '
                  'cheap to read, so absolute savings are modest here. Gio shines '
                  'most on ~10k–200k-LOC codebases.</p>')

    overall_s = f"{overall:.1f}×" if overall else "—"
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Gio savings — {esc(root.name)}</title>
<style>{_CSS}</style></head>
<body><div class="wrap">
  <div class="card">
    <p class="brand">Gio \U0001f331 — token savings</p>
    <p class="sub">Repo <code>{esc(root.name)}</code> · {human(chunks)} chunks · {human(files)} files · {n_questions} probe questions</p>
    <div class="hero"><div class="num">{overall_s}</div>
      <div class="cap">fewer input tokens than reading the whole files an answer lives in</div></div>
    {banner}
  </div>
  <div class="tiles">
    <div class="tile"><div class="v good">{human(saved)}</div><div class="l">input tokens saved (this run)</div></div>
    <div class="tile"><div class="v good">${saved_usd:.2f}</div><div class="l">saved at {esc(model)} input pricing</div></div>
    <div class="tile"><div class="v">{overall_s}</div><div class="l">more questions in the same budget</div></div>
  </div>
  <div class="card">
    <h2>Per question — tokens read</h2>
    <div class="legend">
      <span><i class="sw gio"></i>Gio (targeted spans)</span>
      <span><i class="sw naive"></i>Reading whole files</span>
    </div>
    {''.join(bars)}
    <p class="foot">Gio fed {human(gio_total)} tokens total vs {human(naive_total)} to read whole files.
    Deterministic, local, char/4 token estimate — no API key. Retrieval quality (hit@k) is a
    separate labeled eval (scripts/eval_retrieval.py).</p>
  </div>
</div></body></html>
"""


def main(argv=None):
    p = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--root", default=".",
                   help="Repo to benchmark (default: current directory).")
    p.add_argument("--questions",
                   help="File of probe questions, one per line "
                        "(default: a built-in generic set).")
    p.add_argument("--k", type=int, default=8,
                   help="Spans retrieved per question (default 8).")
    p.add_argument("--model", default="sonnet",
                   help="Model id for the projected-$ estimate (default sonnet).")
    p.add_argument("--with-usage", action="store_true",
                   help="Also run scripts/impact.py for real-session savings.")
    p.add_argument("--out", default=str(REPO_ROOT / "bench" / "SELF_REPORT.md"),
                   help="Where to write the shareable markdown report.")
    p.add_argument("--html",
                   help="Also write a self-contained HTML savings dashboard here.")
    args = p.parse_args(argv)

    root = Path(args.root).expanduser().resolve()
    if not root.is_dir():
        sys.exit(f"not a directory: {root}")

    questions = DEFAULT_QUESTIONS
    if args.questions:
        questions = [ln.strip() for ln in
                     Path(args.questions).read_text().splitlines() if ln.strip()]
    if not questions:
        sys.exit("no questions to run")

    print(f"Indexing {root} ...")
    build = index_cli.build(root, "none", full=True, engine="auto",
                            with_decisions=False)
    chunks, files = build["chunks"], build["files"]
    print(f"Indexed {human(chunks)} chunks from {human(files)} files.")

    rows = []
    gio_total = naive_total = 0
    for q in questions:
        res = retrieve_cli.run_query(root, q, args.k, "lexical", budget_ms=60_000)
        if res.get("error") or not res.get("results"):
            rows.append((q, 0, 0, None))
            continue
        gio = res["chunk_tokens"]
        naive = res["whole_file_tokens"]
        gio_total += gio
        naive_total += naive
        ratio = (naive / gio) if gio else None
        rows.append((q, gio, naive, ratio))
        r = f"{ratio:.1f}x" if ratio else "—"
        print(f"  {r:>6}  gio {human(gio):>7}  naive {human(naive):>8}  {q}")

    saved = naive_total - gio_total
    overall = (naive_total / gio_total) if gio_total else None
    in_price, _out = impact.price_for(args.model)
    saved_usd = saved / 1_000_000 * in_price  # per one pass of these questions

    # ---- report ----
    lines = ["# Gio self-benchmark — token reduction on your repo", ""]
    lines.append(f"Repo: `{root}` — {human(chunks)} chunks, {human(files)} files.")
    if build.get("truncated"):
        lines.append("")
        lines.append("> ⚠️ This repo hit Gio's 50k-chunk index cap — it was "
                     "truncated. Numbers cover the indexed portion.")
    elif chunks < SMALL_REPO_CHUNKS:
        lines.append("")
        lines.append(f"> ℹ️ Small repo ({human(chunks)} chunks). The whole thing "
                     "fits in context cheaply, so absolute savings are modest — "
                     "the ratio still holds, but Gio shines most on ~10k–200k-LOC "
                     "codebases.")
    lines.append("")
    lines.append(f"**Overall: {overall:.1f}× fewer input tokens**" if overall
                 else "**Overall: no retrievable results**")
    lines.append("")
    lines.append(f"- Gio retrieval fed **{human(gio_total)}** tokens across "
                 f"{len(questions)} questions.")
    lines.append(f"- Reading the whole files instead would be "
                 f"**{human(naive_total)}** tokens.")
    lines.append(f"- Saved **{human(saved)}** input tokens "
                 f"≈ **${saved_usd:.2f}** at {args.model} input pricing "
                 f"(one pass; multiply by how often you'd ask).")
    lines.append("")
    lines.append("| ratio | gio tokens | naive tokens | question |")
    lines.append("|---|---|---|---|")
    for q, gio, naive, ratio in rows:
        r = f"{ratio:.1f}x" if ratio else "—"
        lines.append(f"| {r} | {human(gio)} | {human(naive)} | {q} |")
    lines.append("")
    lines.append("_Gio cost = tokens of the retrieved spans; naive cost = tokens "
                 "of the whole files those spans live in (a naive agent opens "
                 "whole files instead of targeted spans). Deterministic, local, "
                 "char/4 token estimate — no API key. Retrieval quality (hit@k) "
                 "is a separate labeled eval; see scripts/eval_retrieval.py._")

    if args.with_usage:
        lines.append("")
        lines.append("## Real-session savings (from your Claude Code logs)")
        lines.append("")
        proc = subprocess.run(
            [sys.executable, str(REPO_ROOT / "scripts" / "impact.py")],
            capture_output=True, text=True)
        out = (proc.stdout or proc.stderr).strip()
        lines.append("```")
        lines.append(out)
        lines.append("```")

    report = "\n".join(lines) + "\n"
    Path(args.out).write_text(report)

    if args.html:
        html = render_html(root, chunks, files, rows, gio_total, naive_total,
                           saved, overall, saved_usd, args.model, len(questions),
                           bool(build.get("truncated")), chunks < SMALL_REPO_CHUNKS)
        Path(args.html).write_text(html)
        print(f"Wrote {args.html}")

    print()
    if overall:
        print(f"Overall: {overall:.1f}x fewer input tokens "
              f"(saved {human(saved)} tokens ≈ ${saved_usd:.2f}).")
    print(f"Wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
