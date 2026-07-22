#!/usr/bin/env python3
"""
Tests for retrieval_lib.py / index.py / retrieve.py.

Run:  python3 scripts/test_retrieval.py
  or: python3 -m unittest discover -s scripts -p 'test_*.py'

Stdlib-only except where a test is explicitly about the vector path, which
needs numpy and is skipped cleanly when it isn't installed. No test touches
the network: the vector path is exercised through the deterministic built-in
"hash" backend.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retrieval_lib as rl  # noqa: E402
import index as index_cli  # noqa: E402
import retrieve as retrieve_cli  # noqa: E402

HAVE_NUMPY = rl.np is not None


PY_SAMPLE = '''"""Module docstring for the sample."""

import os
import sys

MAX_RETRIES = 3


def fetch_user(user_id):
    """Fetch a user record from the store."""
    return {"id": user_id}


class SessionManager:
    """Keeps login sessions alive."""

    timeout = 30

    def authenticate(self, credentials):
        """Check credentials against the store."""
        return credentials is not None

    def logout(self, user_id):
        return fetch_user(user_id)
'''


def make_repo(files):
    """Create a temp repo (no git) with the given {relpath: text} files."""
    tmp = tempfile.mkdtemp(prefix="gio-retrieval-test-")
    for rel, text in files.items():
        full = Path(tmp) / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        if isinstance(text, bytes):
            full.write_bytes(text)
        else:
            full.write_text(text)
    return Path(tmp)


class TestTokenizer(unittest.TestCase):
    def test_snake_case_keeps_whole_and_parts(self):
        toks = rl.tokenize("find_symbol")
        self.assertIn("find_symbol", toks)
        self.assertIn("find", toks)
        self.assertIn("symbol", toks)

    def test_camel_case_split(self):
        toks = rl.tokenize("HTTPServerError")
        self.assertIn("httpservererror", toks)
        self.assertIn("http", toks)
        self.assertIn("server", toks)
        self.assertIn("error", toks)

    def test_plain_words_not_duplicated(self):
        self.assertEqual(rl.tokenize("hello world"), ["hello", "world"])


class TestChunking(unittest.TestCase):
    def chunks_for(self, text):
        import codebase_map as cm
        syms = cm.extract_ast(text)
        return rl.chunk_file("pkg/sample.py", text, syms, "python", "abc123")

    def test_function_and_methods_get_own_chunks(self):
        symbols = {c.symbol for c in self.chunks_for(PY_SAMPLE)}
        self.assertIn("fetch_user", symbols)
        self.assertIn("SessionManager.authenticate", symbols)
        self.assertIn("SessionManager.logout", symbols)

    def test_class_chunk_stops_before_first_method(self):
        chunks = {c.symbol: c for c in self.chunks_for(PY_SAMPLE)}
        cls = chunks["SessionManager"]
        meth = chunks["SessionManager.authenticate"]
        self.assertLess(cls.line_end, meth.line_start)
        self.assertIn("Keeps login sessions alive", cls.text)
        self.assertNotIn("def authenticate", cls.text)

    def test_module_chunk_covers_imports(self):
        chunks = self.chunks_for(PY_SAMPLE)
        modules = [c for c in chunks if c.kind == "module"]
        self.assertTrue(modules)
        self.assertIn("import os", modules[0].text)

    def test_header_carries_path_symbol_signature(self):
        chunks = {c.symbol: c for c in self.chunks_for(PY_SAMPLE)}
        h = chunks["fetch_user"].header
        self.assertIn("pkg/sample.py", h)
        self.assertIn("fetch_user", h)
        self.assertIn("def fetch_user(user_id):", h)
        self.assertIn("Fetch a user record", h)  # docstring first line

    def test_oversized_symbol_splits_with_header_repeated(self):
        body = "def big():\n" + "\n".join(
            f"    x{i} = {i}" for i in range(400))
        import codebase_map as cm
        syms = cm.extract_ast(body)
        chunks = rl.chunk_file("big.py", body, syms, "python", "s")
        parts = [c for c in chunks if c.symbol == "big"]
        self.assertGreater(len(parts), 1)
        for c in parts:
            self.assertIn("big.py :: big", c.header)
            self.assertLessEqual(len(c.text), rl.MAX_CHUNK_CHARS + 200)
        # Consecutive windows overlap
        self.assertLess(parts[1].line_start, parts[0].line_end + 1)

    def test_point_symbols_extend_to_next(self):
        import codebase_map as cm
        md = "# Title\n\nintro text\n\n## Section A\n\nbody a\n\n## Section B\n\nbody b\n"
        syms = cm.extract_markdown(md)
        chunks = rl.chunk_file("doc.md", md, syms, "markdown", "s")
        sec_a = next(c for c in chunks if c.symbol == "Section A")
        self.assertIn("body a", sec_a.text)
        self.assertNotIn("body b", sec_a.text)

    def test_empty_file_yields_no_chunks(self):
        self.assertEqual(rl.chunk_file("e.py", "", [], "python", "s"), [])


class TestBM25(unittest.TestCase):
    def build(self):
        chunks = [
            rl.Chunk(id=f"c{i}", path=f"f{i}.py", symbol=s, kind="function",
                     line_start=1, line_end=2, header=f"f{i}.py :: {s}",
                     text=t, sha="x", lang="python")
            for i, (s, t) in enumerate([
                ("cost_usd", "def cost_usd(model, tok): return price"),
                ("authenticate", "def authenticate(credentials): check login session"),
                ("render_md", "def render_md(m): lines markdown output"),
            ])]
        return chunks, rl.BM25Index.build(chunks)

    def test_exact_identifier_ranks_first(self):
        _, idx = self.build()
        self.assertEqual(idx.search("cost_usd", 3)[0][0], "c0")

    def test_conceptual_terms_match(self):
        _, idx = self.build()
        self.assertEqual(idx.search("login session check", 3)[0][0], "c1")

    def test_no_match_returns_empty(self):
        _, idx = self.build()
        self.assertEqual(idx.search("zzz qqq", 3), [])

    def test_save_load_roundtrip(self):
        _, idx = self.build()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "bm25.json"
            idx.save(path)
            loaded = rl.BM25Index.load(path)
        self.assertEqual(loaded.search("cost_usd", 1), idx.search("cost_usd", 1))

    def test_search_speed_ceiling(self):
        # Generous perf guard: 5k synthetic chunks, one query, < 500 ms.
        chunks = [
            rl.Chunk(id=f"c{i}", path="f.py", symbol=f"fn_{i}", kind="function",
                     line_start=1, line_end=2, header=f"f.py :: fn_{i}",
                     text=f"def fn_{i}(): handles case number {i} retry logic",
                     sha="x", lang="python")
            for i in range(5000)]
        idx = rl.BM25Index.build(chunks)
        t0 = time.time()
        idx.search("retry logic for case", 10)
        self.assertLess(time.time() - t0, 0.5)


class TestFusion(unittest.TestCase):
    def test_rrf_hand_computed(self):
        fused = rl.rrf_fuse([[("a", 9), ("b", 5)], [("b", 0.9), ("c", 0.5)]],
                            k=60)
        scores = dict(fused)
        self.assertAlmostEqual(scores["a"], 1 / 61)
        self.assertAlmostEqual(scores["b"], 1 / 62 + 1 / 61)
        self.assertAlmostEqual(scores["c"], 1 / 62)
        self.assertEqual(fused[0][0], "b")  # appears in both lists -> wins

    def test_rrf_weights(self):
        fused = dict(rl.rrf_fuse([[("a", 1)], [("b", 1)]], weights=[2.0, 1.0]))
        self.assertGreater(fused["a"], fused["b"])

    def test_rank_priors(self):
        def chunk(path, lang):
            return rl.Chunk(id=path, path=path, symbol="s", kind="function",
                            line_start=1, line_end=2, header="", text="x",
                            sha="x", lang=lang)
        self.assertEqual(rl.rank_prior(chunk("src/auth.py", "python")), 1.0)
        self.assertEqual(rl.rank_prior(chunk("scripts/test_auth.py",
                                             "python")), rl.PRIOR_TEST)
        self.assertEqual(rl.rank_prior(chunk("tests/helpers.py", "python")),
                         rl.PRIOR_TEST)
        self.assertEqual(rl.rank_prior(chunk("lib/auth.spec.js",
                                             "javascript")), rl.PRIOR_TEST)
        self.assertEqual(rl.rank_prior(chunk("README.md", "markdown")),
                         rl.PRIOR_DOCS)
        by_id = {"code": chunk("a.py", "python"),
                 "test": chunk("test_a.py", "python")}
        ranked = rl.apply_priors([("test", 1.0), ("code", 0.9)], by_id)
        self.assertEqual(ranked[0][0], "code")  # 0.9 beats 1.0 * 0.7

    def test_identifier_detection(self):
        for q in ("where is cost_usd applied", "fix SessionManager please",
                  'find the string "no logs found"', "call fetch.user()"):
            self.assertTrue(rl.is_identifier_query(q), q)
        for q in ("where is the retry logic", "add a login screen",
                  "how are savings estimated"):
            self.assertFalse(rl.is_identifier_query(q), q)


@unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
class TestVectorStore(unittest.TestCase):
    def test_normalize_handles_zero_rows(self):
        m = rl.VectorStore.normalize([[0.0, 0.0], [3.0, 4.0]])
        self.assertAlmostEqual(float((m[1] ** 2).sum()), 1.0, places=5)
        self.assertEqual(float(m[0].sum()), 0.0)

    def test_save_load_search(self):
        backend = rl.HashBackend()
        texts = ["login session credentials", "markdown render output",
                 "cosine vector store"]
        matrix = rl.VectorStore.normalize(backend.embed(texts))
        store = rl.VectorStore(["a", "b", "c"], matrix)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "emb.npz"
            store.save(path)
            loaded = rl.VectorStore.load(path)
            hits = loaded.search(backend.embed(["login credentials"])[0], 2)
        self.assertEqual(hits[0][0], "a")

    def test_hash_backend_deterministic(self):
        b1, b2 = rl.HashBackend(), rl.HashBackend()
        self.assertTrue(
            (b1.embed(["same text"]) == b2.embed(["same text"])).all())


class TestIndexBuild(unittest.TestCase):
    def test_lexical_only_build_and_meta(self):
        root = make_repo({"app.py": PY_SAMPLE})
        res = index_cli.build(root, "none", full=False, engine="auto",
                              with_decisions=True)
        self.assertEqual(res["embeddings"], "absent")
        ok, problems, meta = rl.validate_index(rl.index_dir(root))
        self.assertTrue(ok, problems)
        self.assertGreater(meta["chunk_count"], 0)

    def test_unknown_backend_still_builds_lexical(self):
        root = make_repo({"app.py": PY_SAMPLE})
        res = index_cli.build(root, "model2vec-typo-nope", full=False,
                              engine="auto", with_decisions=False)
        self.assertEqual(res["embeddings"], "absent")
        self.assertTrue(any("skipped" in n for n in res["notices"]))
        ok, problems, _ = rl.validate_index(rl.index_dir(root))
        self.assertTrue(ok, problems)

    def test_empty_repo(self):
        root = make_repo({})
        res = index_cli.build(root, "none", full=False, engine="auto",
                              with_decisions=False)
        self.assertEqual(res["chunks"], 0)
        ok, problems, _ = rl.validate_index(rl.index_dir(root))
        self.assertTrue(ok, problems)

    def test_non_utf8_and_binary_files(self):
        root = make_repo({
            "app.py": PY_SAMPLE,
            "weird.py": b"def caf\xe9():\n    pass\n",       # invalid utf-8
            "fake.js": b"\x00\x01\x02binary-with-src-extension",
        })
        res = index_cli.build(root, "none", full=False, engine="auto",
                              with_decisions=False)
        chunks = rl.load_chunks(rl.index_dir(root) / rl.CHUNKS_FILE)
        paths = {c.path for c in chunks}
        self.assertIn("app.py", paths)
        self.assertNotIn("fake.js", paths)   # NUL sniff drops binaries
        self.assertGreater(res["chunks"], 0)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_incremental_reembeds_only_changed_files(self):
        root = make_repo({"a.py": PY_SAMPLE,
                          "b.py": "def solo():\n    return 1\n"})
        r1 = index_cli.build(root, "hash", False, "auto", False)
        self.assertEqual(r1["embeddings"], "present")
        # No changes -> everything reused.
        r2 = index_cli.build(root, "hash", False, "auto", False)
        note = next(n for n in r2["notices"] if "embedded" in n)
        self.assertIn("embedded 0 new/changed", note)
        # Touch one file -> only its chunks re-embed.
        (root / "b.py").write_text("def solo():\n    return 2\n")
        r3 = index_cli.build(root, "hash", False, "auto", False)
        note = next(n for n in r3["notices"] if "embedded" in n)
        embedded = int(note.split("embedded ")[1].split(" ")[0])
        b_chunks = [c for c in
                    rl.load_chunks(rl.index_dir(root) / rl.CHUNKS_FILE)
                    if c.path == "b.py"]
        self.assertEqual(embedded, len(b_chunks))
        self.assertGreater(embedded, 0)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_backend_change_forces_reembed_and_validates(self):
        root = make_repo({"a.py": PY_SAMPLE})
        index_cli.build(root, "hash", False, "auto", False)
        ok, problems, meta = rl.validate_index(rl.index_dir(root))
        self.assertTrue(ok, problems)
        self.assertEqual(meta["backend"]["name"], "hash")

    def test_check_reports_drift(self):
        root = make_repo({"a.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        meta = rl.load_meta(rl.index_dir(root))
        self.assertFalse(index_cli.check_stale(root, meta)["drift"])
        (root / "new.py").write_text("def added():\n    pass\n")
        report = index_cli.check_stale(root, meta)
        self.assertTrue(report["drift"])
        self.assertEqual(report["added"], ["new.py"])

    def test_decisions_chunked(self):
        root = make_repo({
            "a.py": PY_SAMPLE,
            "DECISIONS.md": ("# Decisions\n\n## D-0001 — Use sqlite\n\n"
                             "Context: need storage.\nDecision: sqlite.\n\n"
                             "## D-0002 — Use bcrypt for passwords\n\n"
                             "Decision: bcrypt with cost 12.\n")})
        index_cli.build(root, "none", False, "auto", with_decisions=True)
        chunks = rl.load_chunks(rl.index_dir(root) / rl.CHUNKS_FILE)
        decisions = [c for c in chunks if c.source == "decisions"]
        self.assertEqual([c.symbol for c in decisions], ["D-0001", "D-0002"])
        self.assertIn("sqlite", decisions[0].text)
        self.assertNotIn("bcrypt", decisions[0].text)


class TestRetrieveDegradation(unittest.TestCase):
    def test_lexical_query_end_to_end(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        res = retrieve_cli.run_query(root, "check login credentials", 3,
                                     "lexical", 2000)
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["results"])
        self.assertEqual(res["results"][0]["symbol"],
                         "SessionManager.authenticate")

    def test_hybrid_degrades_when_embeddings_absent(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        res = retrieve_cli.run_query(root, "login", 3, "hybrid", 2000)
        self.assertEqual(res["degraded"], "lexical-only")
        self.assertEqual(res["exit_code"], 0)

    def test_missing_index_small_repo_ephemeral(self):
        root = make_repo({"app.py": PY_SAMPLE})
        res = retrieve_cli.run_query(root, "login credentials", 3,
                                     "hybrid", 2000)
        self.assertEqual(res["degraded"], "ephemeral-lexical")
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["results"])

    def test_missing_index_large_repo_exits_2(self):
        root = make_repo({"app.py": PY_SAMPLE})
        with mock.patch.object(retrieve_cli, "EPHEMERAL_MAX_FILES", 0):
            res = retrieve_cli.run_query(root, "login", 3, "hybrid", 2000)
        self.assertEqual(res.get("exit_code"), 2)
        self.assertIn("index.py", res["error"])

    def test_corrupt_chunks_degrades(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        (rl.index_dir(root) / rl.CHUNKS_FILE).write_text("{not json\n")
        res = retrieve_cli.run_query(root, "login", 3, "hybrid", 2000)
        self.assertEqual(res["degraded"], "ephemeral-lexical")
        self.assertEqual(res["exit_code"], 0)

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_budget_exceeded_skips_vector(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "hash", False, "auto", False)
        res = retrieve_cli.run_query(root, "login", 3, "hybrid",
                                     budget_ms=-1)
        self.assertEqual(res["degraded"], "lexical-only")
        self.assertIn("budget", res["degraded_reason"])

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_hybrid_with_hash_backend(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "hash", False, "auto", False)
        res = retrieve_cli.run_query(root, "check login credentials", 3,
                                     "hybrid", 5000)
        self.assertIsNone(res["degraded"])
        self.assertEqual(res["mode"], "hybrid")
        self.assertTrue(res["results"])
        self.assertIn("vector_ms", res["timings"])

    def test_stale_index_flagged_but_answers(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        (root / "app.py").write_text(PY_SAMPLE + "\n\ndef extra():\n    pass\n")
        res = retrieve_cli.run_query(root, "login", 3, "lexical", 2000)
        self.assertTrue(res["stale"])
        self.assertEqual(res["exit_code"], 0)
        self.assertTrue(res["results"])

    def test_usage_log_written(self):
        root = make_repo({"app.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        import contextlib
        import io
        with contextlib.redirect_stdout(io.StringIO()):
            rc = retrieve_cli.main(["login", "--root", str(root), "--mode",
                                    "lexical", "--paths-only"])
        self.assertEqual(rc, 0)
        usage = (rl.index_dir(root) / rl.USAGE_FILE).read_text().strip()
        rec = json.loads(usage.splitlines()[-1])
        self.assertGreater(rec["chunk_tokens"], 0)
        self.assertGreaterEqual(rec["whole_file_tokens"], rec["chunk_tokens"])

    def test_decisions_retrieval(self):
        root = make_repo({
            "a.py": PY_SAMPLE,
            "DECISIONS.md": ("# Decisions\n\n## D-0001 — Use sqlite for "
                             "storage\n\nDecision: sqlite, no server.\n")})
        index_cli.build(root, "none", False, "auto", True)
        res = retrieve_cli.run_decisions(root, "which database do we use", 3)
        self.assertTrue(res["results"])
        self.assertEqual(res["results"][0]["decision"], "D-0001")


class TestLocking(unittest.TestCase):
    def test_second_writer_blocked(self):
        root = make_repo({"a.py": PY_SAMPLE})
        idx = rl.index_dir(root)
        with rl.acquire_lock(idx):
            with self.assertRaises(rl.LockHeld):
                with rl.acquire_lock(idx):
                    pass
        # Released on exit: can take it again.
        with rl.acquire_lock(idx):
            pass

    def test_stale_lock_stolen(self):
        root = make_repo({"a.py": PY_SAMPLE})
        idx = rl.index_dir(root)
        idx.mkdir(parents=True, exist_ok=True)
        lock = idx / rl.LOCK_FILE
        lock.write_text("999999 0")
        old = time.time() - 3600
        os.utime(lock, (old, old))
        with rl.acquire_lock(idx, stale_after=600):
            pass  # stole the abandoned lock instead of raising


class TestValidateIndex(unittest.TestCase):
    def test_chunk_count_drift_detected(self):
        root = make_repo({"a.py": PY_SAMPLE})
        index_cli.build(root, "none", False, "auto", False)
        idx = rl.index_dir(root)
        with open(idx / rl.CHUNKS_FILE, "a") as fh:
            chunks = rl.load_chunks(idx / rl.CHUNKS_FILE)
            fh.write(json.dumps(chunks[0].to_dict()) + "\n")
        ok, problems, _ = rl.validate_index(idx)
        self.assertFalse(ok)
        self.assertTrue(any("drift" in p for p in problems))

    @unittest.skipUnless(HAVE_NUMPY, "numpy not installed")
    def test_embedding_row_mismatch_detected(self):
        root = make_repo({"a.py": PY_SAMPLE})
        index_cli.build(root, "hash", False, "auto", False)
        idx = rl.index_dir(root)
        store = rl.VectorStore.load(idx / rl.EMBEDDINGS_FILE)
        short = rl.VectorStore(store.ids[:-1],
                               rl.np.asarray(store.matrix[:-1]))
        short.save(idx / rl.EMBEDDINGS_FILE)
        ok, problems, _ = rl.validate_index(idx)
        self.assertFalse(ok)
        self.assertTrue(any("rows" in p for p in problems))


if __name__ == "__main__":
    unittest.main(verbosity=2)
