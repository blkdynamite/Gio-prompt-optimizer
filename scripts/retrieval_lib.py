#!/usr/bin/env python3
"""
retrieval_lib.py — Gio's hybrid retrieval library (lexical BM25 + local embeddings).

Shared machinery behind `index.py` (build/update the index) and `retrieve.py`
(query it). Everything runs locally; nothing leaves the machine.

Design in one paragraph: source files are chunked at symbol boundaries using
the same tiered extraction `codebase_map.py` already provides (tree-sitter →
ctags → ast → regex), each chunk carrying a header (path :: symbol — signature)
that is embedded together with the body. Retrieval runs two rankers over the
same chunk store — a pure-Python Okapi BM25 (zero dependencies, always
available) and cosine over locally computed embeddings (optional extras) — and
fuses them with Reciprocal Rank Fusion. Queries that look like exact
identifiers boost the lexical side, where embeddings are weakest.

Embedding backends are pluggable and optional. The lexical path is stdlib-only
so the skill keeps working with zero installs:

    model2vec  (potion-base-8M)      ~30 MB, no torch, µs/query   [extras]
    fastembed  (bge-small-en-v1.5)   ONNX, no torch               [extras]
    st         (all-MiniLM-L6-v2)    sentence-transformers/torch  [extras-full]
    hash                             deterministic, testing only  [built-in]

Index layout (gitignored, always safe to delete and rebuild):

    .gio/index/chunks.jsonl     one chunk per line (metadata + text)
    .gio/index/bm25.json        lexical postings
    .gio/index/embeddings.npz   L2-normalized float32 matrix + chunk ids
    .gio/index/meta.json        schema, backend, per-file sha (incremental)
    .gio/index/usage.jsonl      per-query token accounting (for impact.py)
"""

from __future__ import annotations

import json
import math
import os
import re
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, asdict
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import codebase_map as cm  # noqa: E402  (sibling module, reused not duplicated)

try:  # numpy is only needed for the vector path; lexical stays stdlib-only.
    import numpy as np
except ImportError:  # pragma: no cover - exercised via BackendUnavailable
    np = None

# ---------------------------------------------------------------------------
# CONSTANTS — edit to taste. Notes inline.
# ---------------------------------------------------------------------------

SCHEMA_VERSION = 1

# Index directory, relative to the repo root. Gitignored.
INDEX_DIR = ".gio/index"
CHUNKS_FILE = "chunks.jsonl"
BM25_FILE = "bm25.json"
EMBEDDINGS_FILE = "embeddings.npz"
META_FILE = "meta.json"
USAGE_FILE = "usage.jsonl"
LOCK_FILE = "lock"

# ~512-token cap per chunk, estimated at 4 chars/token (same heuristic
# impact.py's baseline uses). Oversized symbols are split into line windows
# with overlap, the header repeated on every window.
MAX_CHUNK_CHARS = 2048
WINDOW_LINES = 60
WINDOW_OVERLAP = 10

# Gaps between symbol chunks smaller than this many lines are not worth their
# own "module" chunk (usually blank lines / a stray comment).
MODULE_GAP_MIN_LINES = 5

# Hard cap on total chunks: past this, brute-force cosine is the wrong tool
# (the seam for hnswlib, deliberately not implemented). Largest module chunks
# are dropped first and the index is marked truncated.
MAX_CHUNKS = 50_000

# Reciprocal Rank Fusion: score += weight / (RRF_K + rank). k=60 is the
# standard robust default; no tuning.
RRF_K = 60
# Queries that contain exact identifiers boost the lexical list — semantic
# search on exact symbols is where embeddings embarrass themselves.
IDENTIFIER_LEXICAL_WEIGHT = 1.5

# Okapi BM25 defaults.
BM25_K1 = 1.2
BM25_B = 0.75

# A concurrent index build holding the lock longer than this is presumed dead.
LOCK_STALE_SECONDS = 600

BACKEND_CHOICES = ("model2vec", "fastembed", "st", "hash", "none")
DEFAULT_BACKEND = "model2vec"

# ---------------------------------------------------------------------------
# Chunks
# ---------------------------------------------------------------------------


@dataclass
class Chunk:
    id: str            # "path::symbol::line_start[::part]" — stable across runs
    path: str
    symbol: str
    kind: str          # function / method / class / module / section ...
    line_start: int
    line_end: int
    header: str        # "path :: symbol (kind) L a-b — signature"
    text: str          # body only; embed header + "\n" + text
    sha: str           # file content sha (cm.short_sha) — incremental key
    lang: str
    source: str = "code"  # "code" | "decisions" | "prompt"

    def embed_text(self) -> str:
        return self.header + "\n" + self.text

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "Chunk":
        return cls(**d)


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def _first_docstring_line(lines, start_idx):
    """Best-effort first docstring line just after a def/class signature."""
    for i in range(start_idx, min(start_idx + 5, len(lines))):
        stripped = lines[i].strip()
        m = re.match(r"^[rbu]*(\"\"\"|''')(.*)$", stripped)
        if m:
            rest = m.group(2).replace(m.group(1), "").strip()
            if rest:
                return rest[:120]
            for j in range(i + 1, min(i + 3, len(lines))):
                nxt = lines[j].strip()
                if nxt and not nxt.startswith(("\"\"\"", "'''")):
                    return nxt[:120]
            return None
    return None


def _make_header(rel, name, kind, a, b, lines):
    sig = lines[a - 1].strip()[:120] if 0 < a <= len(lines) else ""
    header = f"{rel} :: {name} ({kind}) L{a}-{b} — {sig}"
    doc = _first_docstring_line(lines, a)
    if doc and doc != sig:
        header += f" — {doc}"
    return header


def _emit(chunks, rel, name, kind, a, b, lines, sha, lang):
    """Append the chunk for lines [a, b], window-splitting oversized ones."""
    a, b = max(1, a), min(len(lines), b)
    if b < a:
        return
    body = "\n".join(lines[a - 1:b])
    if not body.strip():
        return
    header = _make_header(rel, name, kind, a, b, lines)
    if len(body) <= MAX_CHUNK_CHARS:
        chunks.append(Chunk(
            id=f"{rel}::{name}::{a}", path=rel, symbol=name, kind=kind,
            line_start=a, line_end=b, header=header, text=body,
            sha=sha, lang=lang))
        return
    part = 0
    step = WINDOW_LINES - WINDOW_OVERLAP
    for wa in range(a, b + 1, step):
        wb = min(wa + WINDOW_LINES - 1, b)
        wbody = "\n".join(lines[wa - 1:wb])
        if wbody.strip():
            chunks.append(Chunk(
                id=f"{rel}::{name}::{a}::{part}", path=rel, symbol=name,
                kind=kind, line_start=wa, line_end=wb,
                header=_make_header(rel, name, kind, wa, wb, lines),
                text=wbody, sha=sha, lang=lang))
            part += 1
        if wb >= b:
            break


def chunk_file(rel, text, symbols, lang, sha):
    """Chunk one file at symbol boundaries (from codebase_map extraction).

    Rules: one chunk per function/method; a class chunk covers its header +
    docstring + attributes (methods chunk separately); point symbols (regex /
    markdown extraction, where line_end == line_start) extend to the next
    symbol; leftover gaps >= MODULE_GAP_MIN_LINES become "module" chunks so
    imports and top-level code stay findable.
    """
    lines = text.splitlines()
    if not lines:
        return []
    n = len(lines)
    syms = [s for s in symbols if s.get("kind") != "constant"]
    syms.sort(key=lambda s: (s["line_start"], -s["line_end"]))

    # Point symbols (heading/regex hits) get a real range: to the next symbol.
    for i, s in enumerate(syms):
        if s["line_end"] <= s["line_start"]:
            nxt = next((t["line_start"] for t in syms[i + 1:]
                        if t["line_start"] > s["line_start"]), n + 1)
            s = dict(s)
            s["line_end"] = max(s["line_start"], nxt - 1)
            syms[i] = s

    chunks: list[Chunk] = []
    for i, s in enumerate(syms):
        a, b = s["line_start"], min(s["line_end"], n)
        # Nested symbols (methods inside a class) chunk on their own; the
        # parent's chunk stops where the first child begins.
        child_start = next((t["line_start"] for t in syms
                            if t is not s and a < t["line_start"] <= b), None)
        if child_start is not None:
            b = child_start - 1
        _emit(chunks, rel, s["name"], s["kind"], a, b, lines, sha, lang)

    # Uncovered gaps -> module chunks (imports, top-level wiring, config).
    covered = [False] * (n + 1)
    for c in chunks:
        for ln in range(c.line_start, min(c.line_end, n) + 1):
            covered[ln] = True
    gap_start = None
    for ln in range(1, n + 2):
        in_gap = ln <= n and not covered[ln]
        if in_gap and gap_start is None:
            gap_start = ln
        elif not in_gap and gap_start is not None:
            if ln - gap_start >= MODULE_GAP_MIN_LINES:
                _emit(chunks, rel, "(module)", "module",
                      gap_start, ln - 1, lines, sha, lang)
            gap_start = None
    return chunks


def chunk_repo(root: Path, engine: str = "auto"):
    """Chunk the whole repo. Returns (chunks, file_meta, truncated).

    Reuses codebase_map's discovery + tiered symbol extraction wholesale via
    cm.build_map(); file_meta maps path -> {sha, mtime} for incremental
    indexing (same sha the codebase map stores, so both indexes agree on
    staleness).
    """
    m = cm.build_map(root, engine, cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
    chunks: list[Chunk] = []
    file_meta = {}
    for f in m["files"]:
        rel = f["path"]
        full = root / rel
        try:
            raw = full.read_bytes()
        except OSError:
            continue
        if b"\x00" in raw[:8192]:  # binary despite a source-ish extension
            continue
        text = raw.decode("utf-8", errors="ignore")
        sha = cm.short_sha(raw)
        file_meta[rel] = {"sha": sha, "mtime": f["mtime"]}
        chunks.extend(chunk_file(rel, text, f["symbols"], f["lang"], sha))

    truncated = False
    if len(chunks) > MAX_CHUNKS:
        # Drop the biggest module chunks first — they are the least precise.
        chunks.sort(key=lambda c: (c.kind == "module", len(c.text)))
        chunks = chunks[:MAX_CHUNKS]
        truncated = True
    chunks.sort(key=lambda c: (c.path, c.line_start))
    return chunks, file_meta, truncated


# ---------------------------------------------------------------------------
# Code-aware tokenizer + BM25 (stdlib-only lexical ranking)
# ---------------------------------------------------------------------------

_WORD_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]*|\d+")
_CAMEL_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z])|[A-Z]?[a-z]+|[A-Z]+|\d+")


def tokenize(text: str):
    """Lowercased tokens; identifiers additionally split into subtokens.

    "find_symbol" -> ["find_symbol", "find", "symbol"]; "HTTPServer" ->
    ["httpserver", "http", "server"] — the whole identifier stays a token so
    exact-name queries still rank highest.
    """
    toks = []
    for w in _WORD_RE.findall(text):
        toks.append(w.lower())
        sub = []
        for piece in w.split("_"):
            sub.extend(_CAMEL_RE.findall(piece))
        sub = [s.lower() for s in sub if s]
        if len(sub) > 1:
            toks.extend(sub)
    return toks


class BM25Index:
    """Okapi BM25 over chunk texts. Pure Python, no dependencies."""

    def __init__(self, k1=BM25_K1, b=BM25_B):
        self.k1, self.b = k1, b
        self.ids: list[str] = []
        self.doc_lens: list[int] = []
        self.avgdl = 0.0
        self.postings: dict[str, list] = {}  # term -> [[doc_idx, tf], ...]

    @classmethod
    def build(cls, chunks):
        idx = cls()
        for i, c in enumerate(chunks):
            # Header tokens (path + symbol + signature) weighted 3x: the
            # chunk *defining* a symbol must outrank chunks merely
            # mentioning it (tests, callers), which BM25's length
            # normalization would otherwise favor.
            toks = tokenize(c.header) * 3 + tokenize(c.text)
            idx.ids.append(c.id)
            idx.doc_lens.append(len(toks))
            tf: dict[str, int] = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            for t, n in tf.items():
                idx.postings.setdefault(t, []).append([i, n])
        idx.avgdl = (sum(idx.doc_lens) / len(idx.doc_lens)) if idx.ids else 0.0
        return idx

    def search(self, query: str, k: int = 10):
        """Return [(chunk_id, score)] best-first."""
        if not self.ids:
            return []
        n_docs = len(self.ids)
        scores: dict[int, float] = {}
        for term in set(tokenize(query)):
            plist = self.postings.get(term)
            if not plist:
                continue
            df = len(plist)
            idf = math.log(1.0 + (n_docs - df + 0.5) / (df + 0.5))
            for doc_idx, tf in plist:
                dl = self.doc_lens[doc_idx]
                denom = tf + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                scores[doc_idx] = scores.get(doc_idx, 0.0) + idf * tf * (self.k1 + 1) / denom
        best = sorted(scores.items(), key=lambda kv: -kv[1])[:k]
        return [(self.ids[i], s) for i, s in best]

    def save(self, path: Path):
        payload = {"k1": self.k1, "b": self.b, "ids": self.ids,
                   "doc_lens": self.doc_lens, "avgdl": self.avgdl,
                   "postings": self.postings}
        _atomic_write_text(path, json.dumps(payload))

    @classmethod
    def load(cls, path: Path):
        d = json.loads(path.read_text())
        idx = cls(k1=d["k1"], b=d["b"])
        idx.ids = d["ids"]
        idx.doc_lens = d["doc_lens"]
        idx.avgdl = d["avgdl"]
        idx.postings = d["postings"]
        return idx


# ---------------------------------------------------------------------------
# Embedding backends (pluggable, all optional, all local)
# ---------------------------------------------------------------------------


class BackendUnavailable(RuntimeError):
    """Raised when an embedding backend (or numpy) is not installed."""


def _require_numpy():
    if np is None:
        raise BackendUnavailable(
            "numpy is required for embeddings: "
            "pip install -r scripts/requirements-semantic.txt")


class Model2VecBackend:
    """Static embeddings: ~30 MB download, no torch, microseconds per query."""

    name = "model2vec"
    model_id = "minishlab/potion-base-8M"

    def __init__(self):
        _require_numpy()
        try:
            from model2vec import StaticModel
        except ImportError as e:
            raise BackendUnavailable(
                "model2vec not installed: "
                "pip install -r scripts/requirements-semantic.txt") from e
        self._model = StaticModel.from_pretrained(self.model_id)
        self.dim = int(np.asarray(self._model.encode(["probe"])).shape[1])

    def embed(self, texts):
        return np.asarray(self._model.encode(list(texts)), dtype=np.float32)


class FastEmbedBackend:
    """ONNX-runtime embeddings — heavier than model2vec, lighter than torch."""

    name = "fastembed"
    model_id = "BAAI/bge-small-en-v1.5"

    def __init__(self):
        _require_numpy()
        try:
            from fastembed import TextEmbedding
        except ImportError as e:
            raise BackendUnavailable(
                "fastembed not installed: pip install fastembed") from e
        self._model = TextEmbedding(model_name=self.model_id)
        self.dim = int(next(iter(self._model.embed(["probe"]))).shape[0])

    def embed(self, texts):
        return np.asarray(list(self._model.embed(list(texts))), dtype=np.float32)


class SentenceTransformersBackend:
    """The spec's reference model. Quality baseline; pulls in torch (~2 GB)."""

    name = "st"
    model_id = "sentence-transformers/all-MiniLM-L6-v2"

    def __init__(self):
        _require_numpy()
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as e:
            raise BackendUnavailable(
                "sentence-transformers not installed: "
                "pip install -r scripts/requirements-semantic-full.txt") from e
        self._model = SentenceTransformer(self.model_id)
        self.dim = int(self._model.get_sentence_embedding_dimension())

    def embed(self, texts):
        return np.asarray(
            self._model.encode(list(texts), show_progress_bar=False),
            dtype=np.float32)


class HashBackend:
    """Deterministic, dependency-free-except-numpy, NETWORK-FREE backend.

    Token-hash bag-of-words vectors: same tokens -> nearby vectors. Not a real
    semantic model — exists so the full vector/hybrid code path (indexing,
    storage, fusion, degradation) can be exercised offline in tests and CI.
    Never a sensible default for actual retrieval quality.
    """

    name = "hash"
    model_id = "builtin-token-hash"
    dim = 256

    def __init__(self):
        _require_numpy()

    def embed(self, texts):
        import hashlib
        out = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            for tok in tokenize(text):
                h = hashlib.md5(tok.encode()).digest()
                idx = int.from_bytes(h[:4], "little") % self.dim
                sign = 1.0 if h[4] % 2 else -1.0
                out[row, idx] += sign
        return out


_BACKENDS = {
    "model2vec": Model2VecBackend,
    "fastembed": FastEmbedBackend,
    "st": SentenceTransformersBackend,
    "hash": HashBackend,
}


def get_backend(name: str):
    if name not in _BACKENDS:
        raise BackendUnavailable(
            f"Unknown backend '{name}'. Choices: {', '.join(_BACKENDS)}")
    return _BACKENDS[name]()


# ---------------------------------------------------------------------------
# Vector store (brute-force cosine; the hnswlib seam if >MAX_CHUNKS ever real)
# ---------------------------------------------------------------------------


class VectorStore:
    """L2-normalized float32 matrix + chunk ids. Cosine = one matvec."""

    def __init__(self, ids, matrix):
        self.ids = list(ids)
        self.matrix = matrix

    @staticmethod
    def normalize(matrix):
        _require_numpy()
        matrix = np.asarray(matrix, dtype=np.float32)
        norms = np.linalg.norm(matrix, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return matrix / norms

    def save(self, path: Path):
        _require_numpy()
        tmp = path.with_suffix(".tmp.npz")
        np.savez(tmp, ids=np.array(self.ids, dtype=str), vectors=self.matrix)
        os.replace(tmp, path)

    @classmethod
    def load(cls, path: Path):
        _require_numpy()
        data = np.load(path, mmap_mode="r", allow_pickle=False)
        return cls([str(x) for x in data["ids"]], data["vectors"])

    def search(self, query_vec, k: int = 10):
        """Return [(chunk_id, cosine)] best-first."""
        _require_numpy()
        if not self.ids:
            return []
        q = np.asarray(query_vec, dtype=np.float32).reshape(-1)
        qn = np.linalg.norm(q)
        if qn > 0:
            q = q / qn
        scores = np.asarray(self.matrix @ q)
        k = min(k, len(self.ids))
        top = np.argpartition(-scores, k - 1)[:k]
        top = top[np.argsort(-scores[top])]
        return [(self.ids[i], float(scores[i])) for i in top]


# ---------------------------------------------------------------------------
# Fusion
# ---------------------------------------------------------------------------

_IDENTIFIER_PATTERNS = [
    re.compile(r"\b[a-z0-9]+_[a-z0-9_]+\b"),          # snake_case
    re.compile(r"\b[A-Z][a-z0-9]+(?:[A-Z][a-z0-9]*)+\b"),  # CamelCase
    re.compile(r"[\"'][^\"']+[\"']"),                  # quoted string
    re.compile(r"\b\w+\.\w+\(\)"),                     # method call foo.bar()
]


def is_identifier_query(query: str) -> bool:
    """Does the query contain an exact code identifier? Boost lexical if so."""
    return any(p.search(query) for p in _IDENTIFIER_PATTERNS)


def rrf_fuse(ranked_lists, k: int = RRF_K, weights=None):
    """Reciprocal Rank Fusion over lists of (id, score) rankings.

    Returns [(id, fused_score)] best-first. Only ranks matter; input scores
    are ignored, which is exactly why RRF needs no tuning.
    """
    weights = weights or [1.0] * len(ranked_lists)
    fused: dict[str, float] = {}
    for ranking, w in zip(ranked_lists, weights):
        for rank, (cid, _score) in enumerate(ranking):
            fused[cid] = fused.get(cid, 0.0) + w / (k + rank + 1)
    return sorted(fused.items(), key=lambda kv: -kv[1])


# ---------------------------------------------------------------------------
# Index meta, validation, locking, small IO helpers
# ---------------------------------------------------------------------------


def index_dir(root: Path) -> Path:
    return root / INDEX_DIR


def _atomic_write_text(path: Path, text: str):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def save_chunks(path: Path, chunks):
    _atomic_write_text(
        path, "".join(json.dumps(c.to_dict()) + "\n" for c in chunks))


def load_chunks(path: Path):
    chunks = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if line:
                chunks.append(Chunk.from_dict(json.loads(line)))
    return chunks


def save_meta(idx_dir: Path, meta: dict):
    _atomic_write_text(idx_dir / META_FILE, json.dumps(meta, indent=2))


def load_meta(idx_dir: Path):
    try:
        return json.loads((idx_dir / META_FILE).read_text())
    except (OSError, json.JSONDecodeError):
        return None


def validate_index(idx_dir: Path):
    """Corruption check. Returns (ok, problems, meta)."""
    problems = []
    meta = load_meta(idx_dir)
    if meta is None:
        return False, ["missing or unreadable meta.json"], None
    if meta.get("schema") != SCHEMA_VERSION:
        problems.append(f"schema mismatch (index {meta.get('schema')}, "
                        f"code {SCHEMA_VERSION})")
    chunks_path = idx_dir / CHUNKS_FILE
    try:
        with open(chunks_path) as fh:
            n_chunks = sum(1 for line in fh if line.strip())
    except OSError:
        return False, ["missing chunks.jsonl"], meta
    if n_chunks != meta.get("chunk_count"):
        problems.append(f"chunk count drift (file {n_chunks}, "
                        f"meta {meta.get('chunk_count')})")
    if not (idx_dir / BM25_FILE).is_file():
        problems.append("missing bm25.json")
    if meta.get("embeddings") == "present":
        emb_path = idx_dir / EMBEDDINGS_FILE
        if not emb_path.is_file():
            problems.append("meta says embeddings present but npz missing")
        elif np is not None:
            try:
                data = np.load(emb_path, mmap_mode="r", allow_pickle=False)
                rows, dim = data["vectors"].shape
                if rows != n_chunks:
                    problems.append(f"embedding rows ({rows}) != chunks ({n_chunks})")
                want = (meta.get("backend") or {}).get("dim")
                if want and dim != want:
                    problems.append(f"embedding dim ({dim}) != meta dim ({want})")
            except Exception as e:
                problems.append(f"unreadable embeddings.npz: {e}")
    return not problems, problems, meta


class LockHeld(RuntimeError):
    pass


@contextmanager
def acquire_lock(idx_dir: Path, stale_after: float = LOCK_STALE_SECONDS):
    """Exclusive index-writer lock. Readers never take it; writes are atomic.

    A lockfile older than `stale_after` is presumed abandoned and stolen.
    """
    idx_dir.mkdir(parents=True, exist_ok=True)
    lock_path = idx_dir / LOCK_FILE
    for _ in range(3):
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, f"{os.getpid()} {int(time.time())}".encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                age = time.time() - lock_path.stat().st_mtime
            except OSError:
                continue  # holder just released; retry
            if age > stale_after:
                lock_path.unlink(missing_ok=True)
                continue
            raise LockHeld(
                f"Another index build holds {lock_path} "
                f"(age {int(age)}s; stale after {int(stale_after)}s).")
    else:  # pragma: no cover - three straight races
        raise LockHeld(f"Could not acquire {lock_path}")
    try:
        yield
    finally:
        lock_path.unlink(missing_ok=True)


def check_index_staleness(root: Path, meta: dict, sample: int = 20):
    """Cheap staleness probe: compare a sample of file shas vs the meta.

    Full-fidelity checking is `index.py --check`; this keeps query latency low.
    """
    files = meta.get("files") or {}
    if not files:
        return False
    paths = sorted(files)
    step = max(1, len(paths) // sample)
    for rel in paths[::step][:sample]:
        full = root / rel
        try:
            if cm.short_sha(full.read_bytes()) != files[rel].get("sha"):
                return True
        except OSError:
            return True  # file listed in meta no longer readable -> stale
    return False


def log_usage(root: Path, record: dict):
    """Append one query's token accounting for impact.py (best-effort)."""
    try:
        path = index_dir(root) / USAGE_FILE
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(json.dumps(record) + "\n")
    except OSError:
        pass
