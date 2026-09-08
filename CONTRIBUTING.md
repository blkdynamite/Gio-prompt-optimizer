# Contributing to Gio

Thanks for helping. Gio is small on purpose: a `SKILL.md` playbook, a few
stdlib-only Python scripts, and evidence for every claim.

## Run it locally

```bash
git clone https://github.com/blkdynamite/Gio-prompt-optimizer.git
cd Gio-prompt-optimizer
python3 -m unittest discover -s scripts -p 'test_*.py'   # unit tests, seconds
python3 scripts/eval_retrieval.py --backends none          # retrieval eval (clones click + express once)
python3 bench/self_benchmark.py --root . --html            # token-reduction self-benchmark
claude plugin validate .                                   # manifest check
```

No dependencies are needed for any of the above. Embedding backends are
optional (`scripts/requirements-semantic.txt`).

## Ground rules

- **Numbers must be reproducible.** If a change affects retrieval, re-run
  `scripts/eval_retrieval.py` and update `eval/RESULTS.md` in the same PR. CI
  fails below hit@5 0.80.
- **Record decisions.** Anything non-trivial (a default, a tradeoff, a reversal)
  gets an entry in `DECISIONS.md` following the format at the top of that file.
  Supersede, never delete.
- **Keep the playbook lean.** `SKILL.md` loads into every session that uses
  Gio, so every added line costs tokens for every user. Put detail in
  `references/` and link to it.
- **Stdlib only** for the default path. Optional extras go behind a flag and a
  clear error message.
- **Bump the version** in both `.claude-plugin/plugin.json` and
  `.claude-plugin/marketplace.json` when behavior changes, and add a
  `CHANGELOG.md` entry. Installed users only receive updates on a bump.

## Reporting bugs

Open an issue with the command you ran, the output, and your Claude Code
version (`claude --version`). For security concerns see `SECURITY.md`.
