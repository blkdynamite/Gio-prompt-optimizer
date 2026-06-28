#!/usr/bin/env python3
"""
Tests for codebase_map.py (and a couple of impact.py cost-math checks).

Stdlib `unittest` only — run with:
    python3 scripts/test_codebase_map.py
    python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import codebase_map as cm  # noqa: E402
import impact  # noqa: E402


PY_FIXTURE = '''\
"""Module purpose line one.

More detail."""

MAX_THING = 42
lowercase_var = 1


def top_level(a, b):
    return a + b


class Widget:
    def method_one(self):
        return 1

    async def method_two(self):
        return 2
'''

MD_FIXTURE = """\
# Title Heading

Some intro.

## Section A

```
# not a heading (inside fence)
```

### Subsection
"""


class TestAstExtraction(unittest.TestCase):
    def test_symbols_and_line_ranges(self):
        syms = cm.extract_ast(PY_FIXTURE)
        self.assertIsNotNone(syms)
        by_name = {s["name"]: s for s in syms}
        # top-level function, class, and its methods (dotted) are captured
        self.assertIn("top_level", by_name)
        self.assertEqual(by_name["top_level"]["kind"], "function")
        self.assertIn("Widget", by_name)
        self.assertEqual(by_name["Widget"]["kind"], "class")
        self.assertIn("Widget.method_one", by_name)
        self.assertEqual(by_name["Widget.method_one"]["kind"], "method")
        self.assertIn("Widget.method_two", by_name)
        # line ranges are sane: start <= end, and the class spans its methods
        for s in syms:
            self.assertLessEqual(s["line_start"], s["line_end"])
        self.assertLess(by_name["Widget"]["line_start"],
                        by_name["Widget.method_one"]["line_start"])

    def test_module_level_constants_captured(self):
        by_name = {s["name"]: s for s in cm.extract_ast(PY_FIXTURE)}
        self.assertIn("MAX_THING", by_name)
        self.assertEqual(by_name["MAX_THING"]["kind"], "constant")
        # lowercase module vars are not treated as navigation constants
        self.assertNotIn("lowercase_var", by_name)

    def test_purpose_is_first_docstring_line(self):
        self.assertEqual(cm.file_purpose_python(PY_FIXTURE),
                         "Module purpose line one.")

    def test_syntax_error_returns_none(self):
        self.assertIsNone(cm.extract_ast("def broken(:\n"))


class TestMarkdownExtraction(unittest.TestCase):
    def test_headings_with_levels(self):
        syms = cm.extract_markdown(MD_FIXTURE)
        kinds = {s["name"]: s["kind"] for s in syms}
        self.assertEqual(kinds.get("Title Heading"), "h1")
        self.assertEqual(kinds.get("Section A"), "h2")
        self.assertEqual(kinds.get("Subsection"), "h3")

    def test_fenced_headings_skipped(self):
        names = [s["name"] for s in cm.extract_markdown(MD_FIXTURE)]
        self.assertNotIn("not a heading (inside fence)", names)

    def test_purpose_is_first_h1(self):
        self.assertEqual(cm.markdown_purpose(MD_FIXTURE), "Title Heading")


class TestRegexFallback(unittest.TestCase):
    def test_finds_defs_across_languages(self):
        js = "export function foo() {}\nclass Bar {}\n"
        names = {s["name"] for s in cm.extract_regex(js)}
        self.assertIn("foo", names)
        self.assertIn("Bar", names)


class TestBuildAndRender(unittest.TestCase):
    def _make_repo(self):
        d = tempfile.mkdtemp()
        (Path(d) / "a.py").write_text(PY_FIXTURE)
        (Path(d) / "README.md").write_text(MD_FIXTURE)
        return Path(d)

    def test_build_map_shape(self):
        root = self._make_repo()
        m = cm.build_map(root, "auto", cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
        self.assertEqual(m["file_count"], 2)
        self.assertGreater(m["symbol_count"], 0)
        paths = {f["path"] for f in m["files"]}
        self.assertEqual(paths, {"a.py", "README.md"})
        # every file carries staleness metadata
        for f in m["files"]:
            self.assertIn("sha", f)
            self.assertIn("mtime", f)

    def test_render_md_is_nonempty_and_caps(self):
        root = self._make_repo()
        m = cm.build_map(root, "auto", 1)  # cap at 1 symbol/file
        md = cm.render_md(m, 1)
        self.assertIn("# Codebase Map", md)
        self.assertIn("a.py", md)
        self.assertIn("more", md)  # the "+N more" truncation note

    def test_output_files_excluded_from_map(self):
        root = self._make_repo()
        (root / cm.MD_FILENAME).write_text("# generated\n")
        (root / cm.JSON_FILENAME).write_text("{}")
        m = cm.build_map(root, "auto", cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
        paths = {f["path"] for f in m["files"]}
        self.assertNotIn(cm.MD_FILENAME, paths)
        self.assertNotIn(cm.JSON_FILENAME, paths)


class TestStaleness(unittest.TestCase):
    def _make_repo(self):
        d = Path(tempfile.mkdtemp())
        (d / "a.py").write_text(PY_FIXTURE)
        return d

    def test_clean_then_drift(self):
        root = self._make_repo()
        m = cm.build_map(root, "auto", cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
        (root / cm.JSON_FILENAME).write_text(json.dumps(m))

        drift, report = cm.check_staleness(root)
        self.assertFalse(drift, report)

        # change content -> changed
        (root / "a.py").write_text(PY_FIXTURE + "\n# edit\n")
        drift, report = cm.check_staleness(root)
        self.assertTrue(drift)
        self.assertIn("a.py", report["changed"])

    def test_added_and_deleted(self):
        root = self._make_repo()
        m = cm.build_map(root, "auto", cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
        (root / cm.JSON_FILENAME).write_text(json.dumps(m))

        (root / "b.py").write_text("def g():\n    return 1\n")
        os.remove(root / "a.py")
        drift, report = cm.check_staleness(root)
        self.assertTrue(drift)
        self.assertIn("b.py", report["added"])
        self.assertIn("a.py", report["deleted"])

    def test_missing_map_is_drift(self):
        root = self._make_repo()
        drift, report = cm.check_staleness(root)
        self.assertTrue(drift)
        self.assertIn("error", report)


class TestFindSymbol(unittest.TestCase):
    def _make_repo_with_map(self):
        root = Path(tempfile.mkdtemp())
        (root / "a.py").write_text(PY_FIXTURE)
        m = cm.build_map(root, "auto", cm.DEFAULT_MAX_SYMBOLS_PER_FILE)
        (root / cm.JSON_FILENAME).write_text(json.dumps(m))
        return root

    def test_exact_function_match(self):
        root = self._make_repo_with_map()
        res = cm.find_symbol(root, "top_level", limit=50)
        self.assertTrue(res["from_stored_map"])
        self.assertTrue(res["exact"])
        self.assertEqual(res["matches"][0]["path"], "a.py")
        self.assertEqual(res["matches"][0]["kind"], "function")

    def test_constant_lookup(self):
        root = self._make_repo_with_map()
        res = cm.find_symbol(root, "MAX_THING", limit=50)
        self.assertTrue(res["exact"])
        self.assertEqual(res["matches"][0]["kind"], "constant")

    def test_method_basename_match(self):
        root = self._make_repo_with_map()
        res = cm.find_symbol(root, "method_one", limit=50)
        self.assertTrue(res["exact"])
        self.assertEqual(res["matches"][0]["name"], "Widget.method_one")

    def test_substring_when_no_exact(self):
        root = self._make_repo_with_map()
        res = cm.find_symbol(root, "method", limit=50)
        self.assertFalse(res["exact"])
        self.assertEqual(res["match_count"], 2)  # method_one + method_two

    def test_miss_returns_empty(self):
        root = self._make_repo_with_map()
        res = cm.find_symbol(root, "no_such_symbol", limit=50)
        self.assertEqual(res["matches"], [])

    def test_builds_in_memory_without_stored_map(self):
        root = Path(tempfile.mkdtemp())
        (root / "a.py").write_text(PY_FIXTURE)
        res = cm.find_symbol(root, "top_level", limit=50)
        self.assertFalse(res["from_stored_map"])
        self.assertTrue(res["exact"])


class TestImpactCostMath(unittest.TestCase):
    """Guards the calculator's pricing math (the prior 'no tests' gap)."""

    def test_cost_usd_opus(self):
        tok = {"input": 1_000_000, "output": 1_000_000,
               "cache_read": 0, "cache_write": 0}
        # opus = (5, 25) per Mtok
        self.assertAlmostEqual(impact.cost_usd("claude-opus-4-8", tok), 30.0)

    def test_cache_read_is_tenth_of_input(self):
        tok = {"input": 0, "output": 0,
               "cache_read": 1_000_000, "cache_write": 0}
        # opus input 5/Mtok * 0.10 = 0.5
        self.assertAlmostEqual(impact.cost_usd("opus", tok), 0.5)

    def test_unknown_model_uses_default(self):
        self.assertEqual(impact.price_for("totally-unknown"), impact.DEFAULT_PRICE)


if __name__ == "__main__":
    unittest.main(verbosity=2)
