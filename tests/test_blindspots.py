"""
Unit and integration tests for Blindspot Radar & Bubble Popper features.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from fastapi.testclient import TestClient

from app.bias import (
    BLINDSPOT_BALANCED,
    BLINDSPOT_LEFT,
    BLINDSPOT_NONE,
    BLINDSPOT_RIGHT,
    aggregate_lean,
    calculate_blindspot,
)
import app.analytics as analytics_mod
import app.comments as comments_mod
import app.journalists as journalists_mod
import app.pulse as pulse_mod
import app.trends as trends_mod
import app.topics as topics_mod
import app.main as main_mod
from app.main import app, templates


class TestCalculateBlindspot(unittest.TestCase):
    def test_zero_and_empty_counts(self):
        res = calculate_blindspot(0, 0, 0)
        self.assertEqual(res["category"], BLINDSPOT_NONE)
        self.assertEqual(res["skew_pct"], 0.0)
        self.assertFalse(res["has_blindspot"])
        self.assertEqual(res["dominant_side"], "none")
        self.assertIsNone(res["flip_to"])
        self.assertEqual(res["total"], 0)

    def test_only_balanced_sources(self):
        res = calculate_blindspot(0, 0, 5)
        self.assertEqual(res["category"], BLINDSPOT_BALANCED)
        self.assertEqual(res["skew_pct"], 0.0)
        self.assertFalse(res["has_blindspot"])
        self.assertEqual(res["dominant_side"], "balanced")
        self.assertIsNone(res["flip_to"])
        self.assertEqual(res["balanced_count"], 5)
        self.assertEqual(res["total"], 5)

    def test_left_blindspot_conservative_heavy(self):
        # 100% conservative
        res100 = calculate_blindspot(10, 0, 0)
        self.assertEqual(res100["category"], BLINDSPOT_LEFT)
        self.assertEqual(res100["skew_pct"], 100.0)
        self.assertTrue(res100["has_blindspot"])
        self.assertEqual(res100["dominant_side"], "conservative")
        self.assertEqual(res100["flip_to"], "liberal")
        self.assertIn("Left Blindspot", res100["description"])
        self.assertIn("100%", res100["description"])

        # 80% conservative
        res80 = calculate_blindspot(8, 2, 0)
        self.assertEqual(res80["category"], BLINDSPOT_LEFT)
        self.assertEqual(res80["skew_pct"], 80.0)
        self.assertTrue(res80["has_blindspot"])
        self.assertEqual(res80["dominant_side"], "conservative")
        self.assertEqual(res80["flip_to"], "liberal")

        # 70% conservative with center
        res70 = calculate_blindspot(7, 1, 2)
        self.assertEqual(res70["category"], BLINDSPOT_LEFT)
        self.assertEqual(res70["skew_pct"], 70.0)
        self.assertTrue(res70["has_blindspot"])

    def test_right_blindspot_liberal_heavy(self):
        # 100% liberal
        res100 = calculate_blindspot(0, 10, 0)
        self.assertEqual(res100["category"], BLINDSPOT_RIGHT)
        self.assertEqual(res100["skew_pct"], 100.0)
        self.assertTrue(res100["has_blindspot"])
        self.assertEqual(res100["dominant_side"], "liberal")
        self.assertEqual(res100["flip_to"], "conservative")
        self.assertIn("Right Blindspot", res100["description"])
        self.assertIn("100%", res100["description"])

        # 80% liberal
        res80 = calculate_blindspot(2, 8, 0)
        self.assertEqual(res80["category"], BLINDSPOT_RIGHT)
        self.assertEqual(res80["skew_pct"], 80.0)
        self.assertTrue(res80["has_blindspot"])
        self.assertEqual(res80["dominant_side"], "liberal")
        self.assertEqual(res80["flip_to"], "conservative")

        # 70% liberal with center
        res70 = calculate_blindspot(1, 7, 2)
        self.assertEqual(res70["category"], BLINDSPOT_RIGHT)
        self.assertEqual(res70["skew_pct"], 70.0)
        self.assertTrue(res70["has_blindspot"])

    def test_balanced_coverage(self):
        # 50/50 split
        res = calculate_blindspot(5, 5, 2)
        self.assertEqual(res["category"], BLINDSPOT_BALANCED)
        self.assertFalse(res["has_blindspot"])
        self.assertEqual(res["dominant_side"], "balanced")
        self.assertIsNone(res["flip_to"])
        self.assertIn("Balanced", res["description"])

        # 4/4 split
        res2 = calculate_blindspot(4, 4, 0)
        self.assertEqual(res2["category"], BLINDSPOT_BALANCED)
        self.assertFalse(res2["has_blindspot"])

        # 5 vs 4 vs 1 (50% < 70% threshold)
        res3 = calculate_blindspot(5, 4, 1)
        self.assertEqual(res3["category"], BLINDSPOT_BALANCED)
        self.assertFalse(res3["has_blindspot"])

    def test_edge_cases_and_non_int(self):
        res = calculate_blindspot(-5, None, "bad")  # type: ignore
        self.assertEqual(res["category"], BLINDSPOT_NONE)
        self.assertEqual(res["skew_pct"], 0.0)
        self.assertFalse(res["has_blindspot"])


class TestAggregateLeanBlindspot(unittest.TestCase):
    def test_empty_hits(self):
        cov = aggregate_lean([])
        self.assertIn("blindspot", cov)
        self.assertEqual(cov["blindspot"]["category"], BLINDSPOT_NONE)

    def test_conservative_dominated_hits(self):
        hits = [
            {"source": "Fox News", "url": "https://foxnews.com/1"},
            {"source": "Breitbart", "url": "https://breitbart.com/2"},
            {"source": "Daily Wire", "url": "https://dailywire.com/3"},
            {"source": "National Review", "url": "https://nationalreview.com/4"},
        ]
        cov = aggregate_lean(hits)
        self.assertIn("blindspot", cov)
        bs = cov["blindspot"]
        self.assertEqual(bs["category"], BLINDSPOT_LEFT)
        self.assertEqual(bs["skew_pct"], 100.0)
        self.assertTrue(bs["has_blindspot"])
        self.assertEqual(bs["dominant_side"], "conservative")
        self.assertEqual(bs["flip_to"], "liberal")

    def test_liberal_dominated_hits(self):
        hits = [
            {"source": "MSNBC", "url": "https://msnbc.com/1"},
            {"source": "The Guardian", "url": "https://theguardian.com/2"},
            {"source": "Mother Jones", "url": "https://motherjones.com/3"},
            {"source": "Vox", "url": "https://vox.com/4"},
        ]
        cov = aggregate_lean(hits)
        self.assertIn("blindspot", cov)
        bs = cov["blindspot"]
        self.assertEqual(bs["category"], BLINDSPOT_RIGHT)
        self.assertEqual(bs["skew_pct"], 100.0)
        self.assertTrue(bs["has_blindspot"])
        self.assertEqual(bs["dominant_side"], "liberal")
        self.assertEqual(bs["flip_to"], "conservative")

    def test_mixed_hits(self):
        hits = [
            {"source": "Fox News", "url": "https://foxnews.com/1"},
            {"source": "MSNBC", "url": "https://msnbc.com/2"},
            {"source": "Reuters", "url": "https://reuters.com/3"},
            {"source": "Associated Press", "url": "https://apnews.com/4"},
        ]
        cov = aggregate_lean(hits)
        self.assertIn("blindspot", cov)
        bs = cov["blindspot"]
        self.assertEqual(bs["category"], BLINDSPOT_BALANCED)
        self.assertFalse(bs["has_blindspot"])


class TestBlindspotEndpointsAndTemplates(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)

        self._patched = [
            (analytics_mod, "CACHE_DIR", analytics_mod.CACHE_DIR),
            (analytics_mod, "STORE_PATH", analytics_mod.STORE_PATH),
            (comments_mod, "CACHE_DIR", comments_mod.CACHE_DIR),
            (comments_mod, "COMMENTS_DIR", comments_mod.COMMENTS_DIR),
            (journalists_mod, "CACHE_DIR", journalists_mod.CACHE_DIR),
            (journalists_mod, "JOURNALISTS_DIR", journalists_mod.JOURNALISTS_DIR),
            (journalists_mod, "PENDING_FILE", journalists_mod.PENDING_FILE),
            (pulse_mod, "CACHE_DIR", pulse_mod.CACHE_DIR),
            (pulse_mod, "CACHE_FILE", pulse_mod.CACHE_FILE),
            (trends_mod, "CACHE_DIR", trends_mod.CACHE_DIR),
            (trends_mod, "TRENDS_DIR", trends_mod.TRENDS_DIR),
            (trends_mod, "PREV_DIR", trends_mod.PREV_DIR),
        ]
        setattr(analytics_mod, "CACHE_DIR", tmp_path)
        setattr(analytics_mod, "STORE_PATH", tmp_path / "analytics.json")
        setattr(comments_mod, "CACHE_DIR", tmp_path)
        setattr(comments_mod, "COMMENTS_DIR", tmp_path / "comments")
        setattr(journalists_mod, "CACHE_DIR", tmp_path)
        setattr(journalists_mod, "JOURNALISTS_DIR", tmp_path / "journalists")
        setattr(journalists_mod, "PENDING_FILE", tmp_path / "journalists" / "_pending.json")
        setattr(pulse_mod, "CACHE_DIR", tmp_path)
        setattr(pulse_mod, "CACHE_FILE", tmp_path / "pulse_cache.json")
        setattr(trends_mod, "CACHE_DIR", tmp_path)
        setattr(trends_mod, "TRENDS_DIR", tmp_path / "trends")
        setattr(trends_mod, "PREV_DIR", tmp_path / "trends_yesterday")

        self._orig_topics_run_search = topics_mod.run_search
        self._orig_main_run_search = main_mod.run_search

        # Mock search with high conservative skew to trigger left blindspot
        async def fake_search_with_blindspot(q, force_trends=False, geo=None, lean=None, **kwargs):
            hits = [
                {"title": "Fox Story", "url": "https://foxnews.com/1", "source": "Fox News", "lean": "right", "lean_label": "Lean right"},
                {"title": "Breitbart Story", "url": "https://breitbart.com/2", "source": "Breitbart", "lean": "right", "lean_label": "Lean right"},
                {"title": "Daily Wire Story", "url": "https://dailywire.com/3", "source": "Daily Wire", "lean": "right", "lean_label": "Lean right"},
                {"title": "National Review", "url": "https://nationalreview.com/4", "source": "National Review", "lean": "right", "lean_label": "Lean right"},
            ]
            cov = aggregate_lean(hits)
            return {
                "hits": hits,
                "tech_hits": [],
                "portals": [],
                "sources_ok": ["Fox News", "Breitbart"],
                "coverage_lean": cov,
                "blindspot": cov.get("blindspot"),
                "lean_pref": lean or "balanced",
                "lean_pref_label": "Balanced",
                "lean_pref_tip": "tip",
                "rank_lookup": None,
                "mode": "live",
                "count": len(hits),
            }

        topics_mod.run_search = fake_search_with_blindspot
        main_mod.run_search = fake_search_with_blindspot

        self.client = TestClient(main_mod.app)

    def tearDown(self):
        topics_mod.run_search = self._orig_topics_run_search
        main_mod.run_search = self._orig_main_run_search
        for mod, attr, orig in self._patched:
            setattr(mod, attr, orig)
        self._tmp.cleanup()

    def test_lean_bar_renders_bubble_popper(self):
        t = templates.get_template("_lean_bar.html")
        html_lib = t.render(lean_pref="liberal")
        self.assertIn("bubble-popper-btn", html_lib)
        self.assertIn('data-flip-target="conservative"', html_lib)
        self.assertIn("Flip to Conservative", html_lib)

        html_con = t.render(lean_pref="conservative")
        self.assertIn("bubble-popper-btn", html_con)
        self.assertIn('data-flip-target="liberal"', html_con)
        self.assertIn("Flip to Liberal", html_con)

    def test_search_page_status_and_blindspot_radar(self):
        resp = self.client.get("/search?q=economy&lean=liberal")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("bubble-popper-btn", resp.text)
        self.assertIn("blindspot-radar", resp.text)
        self.assertIn("Blindspot Radar", resp.text)
        self.assertIn("Left Blindspot", resp.text)

    def test_topic_page_status_and_blindspot_radar(self):
        resp = self.client.get("/topic/economy?lean=conservative")
        self.assertEqual(resp.status_code, 200)
        self.assertIn("bubble-popper-btn", resp.text)
        self.assertIn("blindspot-radar", resp.text)
        self.assertIn("Blindspot Radar", resp.text)
        self.assertIn("Left Blindspot", resp.text)
