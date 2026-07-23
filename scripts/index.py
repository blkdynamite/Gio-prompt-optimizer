#!/usr/bin/env python3
"""
index.py — build or update Gio's local retrieval index (.gio/index/).

Incremental by default: only files whose content hash changed since the last
run are re-embedded (embedding is the expensive stage; chunking and BM25 are
rebuilt each run because they are cheap). Everything runs locally.

The lexical index always builds — with zero third-party installs. Embeddings
are optional: if the backend (or numpy, or the one-time model download) is
unavailable, the lexical index is still written, `embeddings` is marked
absent in meta.json, and the exit code is 0. Indexing never blocks retrieval
and retrieval never blocks the user.

Usage:
    python3 index.py                       # incremental build, default backend
    python3 index.py --backend none        # lexical-only (no installs needed)
    python3 index.py --full                # rebuild from scratch
    python3 index.py --check               # exit 1 if the index is stale
    python3 index.py --status              # index summary + validation
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebase_map as cm  # noqa: E402
import retrieval_lib as rl  # noqa: E402

EMBED_BATCH = 64

_DECISION_HEADING = re.compile(r"^##+\s+(D-\d+)\b")


def decision_chunks(root: Path):
    """Chunk DECISIONS.md entries (## D-NNNN blocks) into the same store.

    Powers `retrieve.py --decisions`: surfacing a related past decision before
    new work turns the log from a file nobody rereads into active memory.
    """
    for candidate in (root / "DECISIONS.md",
                      root / ".claude" / "memory" / "DECISIONS.md"):
        if candidate.is_file():
            break
    else:
        return []
    try:
        raw = candidate.read_bytes()
    except OSError:
        return []
    text = raw.decode("utf-8", errors="ignore")
    sha = cm.short_sha(raw)
    rel = str(candidate.relative_to(root))
    lines = text.splitlines()
    starts = [(i + 1, m.group(1)) for i, line in enumerate(lines)
              if (m := _DECISION_HEADING.match(line))]
    chunks = []
    for n, (line_start, decision_id) in enumerate(starts):
        line_end = (starts[n + 1][0] - 1) if n + 1 < len(starts) else len(lines)
        body = "\n".join(lines[line_start - 1:line_end])[:rl.MAX_CHUNK_CHARS * 2]
        title = lines[line_start - 1].lstrip("#").strip()
        chunks.append(rl.Chunk(
            id=f"{rel}::{decision_id}::{line_start}", path=rel,
            symbol=decision_id, kind="decision", line_start=line_start,
            line_end=line_end, header=f"{rel} :: {title}", text=body,
            sha=sha, lang="markdown", source="decisions"))
    return chunks


def check_stale(root: Path, meta: dict):
    """Full-fidelity staleness diff of on-disk files vs the stored meta."""
    stored = meta.get("files") or {}
    current = set(cm.discover_files(root))
    added = sorted(current - set(stored))
    deleted = sorted(set(stored) - current)
    changed = []
    for rel in sorted(current & set(stored)):
        try:
            if cm.short_sha((root / rel).read_bytes()) != stored[rel].get("sha"):
                changed.append(rel)
        except OSError:
            changed.append(rel)
    return {"added": added, "deleted": deleted, "changed": changed,
            "drift": bool(added or deleted or changed)}


def _old_vectors_by_id(idx_dir: Path):
    """Map chunk id -> (sha, vector row) from the previous index, if any."""
    emb_path = idx_dir / rl.EMBEDDINGS_FILE
    chunks_path = idx_dir / rl.CHUNKS_FILE
    if rl.np is None or not emb_path.is_file() or not chunks_path.is_file():
        return {}
    try:
        old_chunks = rl.load_chunks(chunks_path)
        store = rl.VectorStore.load(emb_path)
    except Exception:
        return {}  # unreadable previous index -> plain full re-embed
    sha_by_id = {c.id: c.sha for c in old_chunks}
    row_by_id = {cid: i for i, cid in enumerate(store.ids)}
    out = {}
    for cid, row in row_by_id.items():
        if cid in sha_by_id:
            out[cid] = (sha_by_id[cid], store.matrix[row])
    return out


def build(root: Path, backend_name: str, full: bool, engine: str,
          with_decisions: bool):
    t0 = time.time()
    idx_dir = rl.index_dir(root)
    notices = []

    chunks, file_meta, truncated = rl.chunk_repo(root, engine)
    if with_decisions:
        chunks = chunks + decision_chunks(root)
    if truncated:
        notices.append(f"chunk cap hit: index truncated to {rl.MAX_CHUNKS} "
                       "chunks (largest module chunks dropped first)")

    backend = None
    embeddings = "absent"
    backend_meta = None
    if backend_name != "none":
        try:
            backend = rl.get_backend(backend_name)
            backend_meta = {"name": backend.name, "model": backend.model_id,
                            "dim": backend.dim}
        except rl.BackendUnavailable as e:
            notices.append(f"embeddings skipped: {e}")
        except Exception as e:  # model download failure, offline, etc.
            notices.append(f"embeddings skipped ({type(e).__name__}): {e}")

    with rl.acquire_lock(idx_dir):
        old_meta = rl.load_meta(idx_dir)
        reuse = {}
        if backend is not None and not full and old_meta:
            same_backend = (old_meta.get("backend") or {}).get("model") == backend.model_id
            if same_backend and old_meta.get("embeddings") == "present":
                reuse = _old_vectors_by_id(idx_dir)

        matrix = None
        if backend is not None:
            try:
                np = rl.np
                rows = [None] * len(chunks)
                to_embed = []
                for i, c in enumerate(chunks):
                    prev = reuse.get(c.id)
                    if prev is not None and prev[0] == c.sha:
                        rows[i] = np.asarray(prev[1], dtype=np.float32)
                    else:
                        to_embed.append(i)
                for start in range(0, len(to_embed), EMBED_BATCH):
                    batch = to_embed[start:start + EMBED_BATCH]
                    vecs = backend.embed([chunks[i].embed_text() for i in batch])
                    for j, i in enumerate(batch):
                        rows[i] = vecs[j]
                matrix = rl.VectorStore.normalize(np.vstack(rows)) if rows \
                    else np.zeros((0, backend.dim), dtype=np.float32)
                embeddings = "present"
                notices.append(f"embedded {len(to_embed)} new/changed chunks, "
                               f"reused {len(chunks) - len(to_embed)}")
            except Exception as e:
                notices.append(f"embedding stage failed ({type(e).__name__}): "
                               f"{e} — lexical index still written")
                matrix, embeddings, backend_meta = None, "absent", None

        rl.save_chunks(idx_dir / rl.CHUNKS_FILE, chunks)
        rl.BM25Index.build(chunks).save(idx_dir / rl.BM25_FILE)
        if matrix is not None:
            rl.VectorStore([c.id for c in chunks], matrix).save(
                idx_dir / rl.EMBEDDINGS_FILE)
        elif full:
            (idx_dir / rl.EMBEDDINGS_FILE).unlink(missing_ok=True)

        meta = {
            "schema": rl.SCHEMA_VERSION,
            "created_at": (old_meta or {}).get("created_at") or int(time.time()),
            "updated_at": int(time.time()),
            "engine": engine,
            "backend": backend_meta,
            "embeddings": embeddings,
            "chunk_count": len(chunks),
            "truncated": truncated,
            "decisions_indexed": with_decisions,
            "files": file_meta,
        }
        rl.save_meta(idx_dir, meta)

    return {"chunks": len(chunks), "files": len(file_meta),
            "embeddings": embeddings, "backend": backend_meta,
            "elapsed_s": round(time.time() - t0, 2), "notices": notices}


def main(argv=None):
    p = argparse.ArgumentParser(
        description="Build/update Gio's local hybrid retrieval index.")
    p.add_argument("--root", default=".", help="Repo root (default: cwd)")
    p.add_argument("--backend", default=rl.DEFAULT_BACKEND,
                   choices=rl.BACKEND_CHOICES,
                   help=f"Embedding backend (default: {rl.DEFAULT_BACKEND}; "
                        "'none' = lexical-only, zero installs)")
    p.add_argument("--engine", default="auto",
                   choices=["auto", "treesitter", "ctags", "ast", "regex"],
                   help="Symbol-extraction engine (passed to codebase_map)")
    p.add_argument("--full", action="store_true",
                   help="Rebuild from scratch (no embedding reuse)")
    p.add_argument("--no-decisions", action="store_true",
                   help="Skip indexing DECISIONS.md entries")
    p.add_argument("--check", action="store_true",
                   help="Report staleness vs the stored index; exit 1 on drift")
    p.add_argument("--status", action="store_true",
                   help="Print index summary + validation")
    p.add_argument("--json", action="store_true", help="Machine-readable output")
    args = p.parse_args(argv)

    root = Path(os.path.expanduser(args.root)).resolve()
    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1
    idx_dir = rl.index_dir(root)

    if args.check or args.status:
        meta = rl.load_meta(idx_dir)
        if meta is None:
            msg = (f"No index at {idx_dir}. Build one: "
                   "python3 scripts/index.py")
            print(json.dumps({"error": msg}) if args.json else msg,
                  file=sys.stderr)
            return 1
        if args.check:
            report = check_stale(root, meta)
            if args.json:
                print(json.dumps(report, indent=2))
            elif report["drift"]:
                print("Retrieval index is STALE — rerun "
                      "`python3 scripts/index.py`:")
                for label in ("added", "changed", "deleted"):
                    for rel in report[label]:
                        print(f"  {label:8} {rel}")
            else:
                print("Retrieval index is up to date (no drift).")
            return 1 if report["drift"] else 0
        ok, problems, _ = rl.validate_index(idx_dir)
        summary = {"chunks": meta.get("chunk_count"),
                   "files": len(meta.get("files") or {}),
                   "embeddings": meta.get("embeddings"),
                   "backend": meta.get("backend"),
                   "updated_at": meta.get("updated_at"),
                   "valid": ok, "problems": problems}
        if args.json:
            print(json.dumps(summary, indent=2))
        else:
            print(f"Index: {summary['chunks']} chunks over {summary['files']} "
                  f"files; embeddings {summary['embeddings']}"
                  + (f" ({(summary['backend'] or {}).get('model')})"
                     if summary["backend"] else ""))
            print("Valid." if ok else "PROBLEMS: " + "; ".join(problems)
                  + " — rebuild with --full")
        return 0 if ok else 1

    try:
        result = build(root, args.backend, args.full, args.engine,
                       with_decisions=not args.no_decisions)
    except rl.LockHeld as e:
        print(str(e), file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Indexed {result['chunks']} chunks from {result['files']} files "
              f"in {result['elapsed_s']}s (embeddings: {result['embeddings']})")
        for note in result["notices"]:
            print(f"  note: {note}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
