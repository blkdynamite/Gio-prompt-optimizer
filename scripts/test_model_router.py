#!/usr/bin/env python3
"""
Tests for model_router.py and impact.py's span-retrieval savings section.

Run:  python3 scripts/test_model_router.py
  or: python3 -m unittest discover -s scripts -p 'test_*.py'
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import impact  # noqa: E402
import model_router as mr  # noqa: E402


def fake_claude_dir(settings_model=None, log_models=(), log_age_days=1):
    """Build a temp ~/.claude with optional settings + usage logs."""
    root = Path(tempfile.mkdtemp(prefix="gio-router-test-"))
    if settings_model:
        (root / "settings.json").write_text(
            json.dumps({"model": settings_model}))
    if log_models:
        proj = root / "projects" / "some-project"
        proj.mkdir(parents=True)
        log = proj / "session.jsonl"
        log.write_text("".join(
            json.dumps({"message": {"model": m,
                                    "usage": {"input_tokens": 10}}}) + "\n"
            for m in log_models))
        old = time.time() - log_age_days * 86400
        os.utime(log, (old, old))
    return root


class TestFamilyDetection(unittest.TestCase):
    def test_api_and_bedrock_ids(self):
        self.assertEqual(mr.family_of("claude-opus-4-8"), "opus")
        self.assertEqual(
            mr.family_of("us.anthropic.claude-haiku-4-5-20251001-v1:0"),
            "haiku")
        self.assertEqual(mr.family_of("claude-fable-5"), "fable")
        self.assertIsNone(mr.family_of("gpt-4o"))
        self.assertIsNone(mr.family_of(None))


class TestGatherEvidence(unittest.TestCase):
    def test_settings_beat_nothing(self):
        claude = fake_claude_dir(settings_model="claude-opus-4-8")
        ev = mr.gather_evidence(claude, Path(tempfile.mkdtemp()), environ={})
        self.assertEqual(ev[0]["family"], "opus")
        self.assertEqual(ev[0]["strength"], "explicit")

    def test_env_var_detected(self):
        claude = fake_claude_dir()
        ev = mr.gather_evidence(claude, Path(tempfile.mkdtemp()),
                                environ={"ANTHROPIC_MODEL": "claude-sonnet-5"})
        self.assertEqual([e["family"] for e in ev], ["sonnet"])

    def test_recent_logs_detected(self):
        claude = fake_claude_dir(log_models=["claude-sonnet-5",
                                             "claude-haiku-4-5-20251001"])
        ev = mr.gather_evidence(claude, Path(tempfile.mkdtemp()), environ={})
        fams = {e["family"] for e in ev}
        self.assertEqual(fams, {"sonnet", "haiku"})
        self.assertTrue(all(e["strength"] == "recent" for e in ev))

    def test_old_logs_excluded(self):
        claude = fake_claude_dir(log_models=["claude-opus-4-8"],
                                 log_age_days=90)
        ev = mr.gather_evidence(claude, Path(tempfile.mkdtemp()), environ={})
        self.assertEqual(ev, [])


class TestRecommend(unittest.TestCase):
    def test_no_evidence_means_inherit(self):
        rec = mr.recommend([])
        self.assertIsNone(rec["best_seen"])
        self.assertEqual(set(rec["routing"].values()), {"inherit"})

    def test_best_family_wins_and_routes(self):
        claude = fake_claude_dir(settings_model="claude-opus-4-8",
                                 log_models=["claude-haiku-4-5-20251001"])
        rec = mr.recommend(
            mr.gather_evidence(claude, Path(tempfile.mkdtemp()), environ={}))
        self.assertEqual(rec["best_seen"]["family"], "opus")
        self.assertEqual(rec["confidence"], "high")
        self.assertEqual(rec["routing"]["planner"], "opus")
        self.assertEqual(rec["routing"]["explore_search"], "haiku")
        self.assertEqual(rec["routing"]["code_review"], "sonnet")

    def test_haiku_only_account_reviews_on_haiku(self):
        claude = fake_claude_dir(log_models=["claude-haiku-4-5-20251001"])
        rec = mr.recommend(
            mr.gather_evidence(claude, Path(tempfile.mkdtemp()), environ={}))
        self.assertEqual(rec["best_seen"]["family"], "haiku")
        self.assertEqual(rec["confidence"], "medium")
        # sonnet-or-best can't exceed what's in evidence
        self.assertEqual(rec["routing"]["code_review"], "haiku")


class TestImpactRetrievalSavings(unittest.TestCase):
    def repo_with_usage(self, records):
        root = Path(tempfile.mkdtemp(prefix="gio-impact-test-"))
        usage = root / ".gio" / "index" / "usage.jsonl"
        usage.parent.mkdir(parents=True)
        usage.write_text("".join(json.dumps(r) + "\n" for r in records))
        return root

    def test_no_usage_file_returns_none(self):
        self.assertIsNone(impact.retrieval_savings(
            Path(tempfile.mkdtemp()), "30"))

    def test_savings_summed_and_priced(self):
        now = int(time.time())
        root = self.repo_with_usage([
            {"when": now, "chunk_tokens": 500, "whole_file_tokens": 4000},
            {"when": now, "chunk_tokens": 300, "whole_file_tokens": 1300},
        ])
        r = impact.retrieval_savings(root, "30")
        self.assertEqual(r["queries"], 2)
        self.assertEqual(r["tokens_avoided"], 4500)
        self.assertAlmostEqual(
            r["est_cost_avoided"], 4500 / 1e6 * impact.DEFAULT_PRICE[0])

    def test_window_filters_old_queries(self):
        now = int(time.time())
        root = self.repo_with_usage([
            {"when": now - 90 * 86400, "chunk_tokens": 1,
             "whole_file_tokens": 100},
            {"when": now, "chunk_tokens": 10, "whole_file_tokens": 60},
        ])
        r = impact.retrieval_savings(root, "30")
        self.assertEqual(r["queries"], 1)
        self.assertEqual(r["tokens_avoided"], 50)
        self.assertEqual(impact.retrieval_savings(root, "all")["queries"], 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
