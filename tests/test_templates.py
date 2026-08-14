"""
Render every page template with a "kitchen sink" fixture — every optional
field populated at least once (author, lean, comments, coverage_lean, etc).

Jinja filter/attribute/global errors (like `{{ x | slugify }}` when slugify
is registered as a global, not a filter) are RUNTIME errors: they only fire
when the specific branch executes with truthy data. Plain unit tests of the
Python functions feeding a template can't catch that — only rendering the
template with data that hits every branch can. This file exists because
that exact bug shipped to production once already (2026-08-09): a hit with
a real .author only reached search.html/topic.html after real RSS parsing
picked one up, well after local testing (which happened to use authorless
hits) and CI passed clean.
"""

import tempfile
import unittest
from pathlib import Path

AUTHORED_HIT = {
    "title": "Kitchen sink story",
    "url": "https://x.test/kitchen-sink",
    "source": "Test Outlet",
    "snippet": "A snippet with every field populated.",
    "score": 10,
    "comments_url": "https://x.test/kitchen-sink#comments",
    "published": "2026-08-09T00:00:00Z",
    "author": "Jane Doe",
    "lean": "left",
    "lean_label": "Lean left",
    "lean_tip": "tip",
}


class TemplateRenderTests(unittest.TestCase):
    def setUp(self):
        # Each of these modules binds its own CACHE_DIR-derived Path constants
        # at import time (`CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))`),
        # not read dynamically per-call. Setting the env var here is too late
        # once another test in the same pytest session has already imported
        # them — they must be monkeypatched directly, same as test_analytics.py
        # and test_journalists.py already do for their own modules.
        self._tmp = tempfile.TemporaryDirectory()
        tmp_path = Path(self._tmp.name)

        import app.analytics as analytics_mod
        import app.comments as comments_mod
        import app.journalists as journalists_mod
        import app.pulse as pulse_mod
        import app.trends as trends_mod
        import app.topics as topics_mod
        import app.main as main_mod

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

        async def fake_run_search(q, force_trends=False, geo=None, lean=None, **kwargs):
            return {
                "hits": [dict(AUTHORED_HIT)],
                "tech_hits": [dict(AUTHORED_HIT, comments_url="https://hn.test/1")],
                "portals": [{"name": "Google", "url": "https://x.test", "kind": "google"}],
                "sources_ok": ["google", "bing"],
                "coverage_lean": {
                    "lean": "left",
                    "lean_label": "Lean left",
                    "lean_tip": "tip",
                    "lean_counts": {"left": 1, "right": 0, "center": 0},
                    "lean_sample": 1,
                },
                "lean_pref": "balanced",
                "lean_pref_label": "Balanced",
                "lean_pref_tip": "tip",
                "rank_lookup": None,
            }

        topics_mod.run_search = fake_run_search
        main_mod.run_search = fake_run_search

        from fastapi.testclient import TestClient

        self.client = TestClient(main_mod.app)

    def tearDown(self):
        import app.topics as topics_mod
        import app.main as main_mod

        topics_mod.run_search = self._orig_topics_run_search
        main_mod.run_search = self._orig_main_run_search
        for mod, attr, orig in self._patched:
            setattr(mod, attr, orig)
        self._tmp.cleanup()

    def _assert_clean_200(self, resp, label):
        self.assertEqual(resp.status_code, 200, f"{label}: expected 200, got {resp.status_code}")
        body = resp.text
        self.assertNotIn("Internal Server Error", body, label)
        self.assertNotIn("Traceback (most recent call last)", body, label)

    def test_topic_page_renders_with_authored_hit(self):
        r = self.client.get("/topic/kitchen-sink-topic")
        self._assert_clean_200(r, "/topic/{slug}")
        self.assertIn("By Jane Doe", r.text)
        from app.topics import slugify

        self.assertIn(f"/journalist/{slugify('Jane Doe')}", r.text)
        self.assertIn('id="comment-form"', r.text)
        self.assertIn("Post comment", r.text)
        self.assertIn('name="body"', r.text)
        form_at = r.text.find('id="comment-form"')
        hits_at = r.text.find("News hits")
        self.assertGreater(form_at, 0)
        if hits_at >= 0:
            self.assertLess(form_at, hits_at, "comment form must sit above News hits")

    def test_pulse_discuss_links_go_to_comment_form(self):
        src = Path(__file__).resolve().parents[1] / "app" / "templates" / "pulse.html"
        text = src.read_text(encoding="utf-8")
        self.assertIn("Discuss here", text)
        self.assertIn("#comment-form", text)
        self.assertNotIn("Discuss topic", text)

    def test_intersection_discuss_links_go_to_comment_form(self):
        r = self.client.get("/search", params={"q": "kitchen sink"})
        self._assert_clean_200(r, "/search?q=kitchen sink")
        self.assertIn("Discuss here", r.text)
        self.assertIn("#comment-form", r.text)

    def test_search_page_renders_with_authored_hit(self):
        r = self.client.get("/search", params={"q": "kitchen sink"})
        self._assert_clean_200(r, "/search?q=...")
        self.assertIn("By Jane Doe", r.text)
        self.assertIn("ScamCheck", r.text)
        self.assertIn("Received a suspicious message about this topic?", r.text)

    def test_daily_intersection_renders(self):
        r = self.client.get("/search")
        self._assert_clean_200(r, "/search (Daily Intersection)")
        self.assertIn('getElementById("pref-headlines-more-top")', r.text)
        self.assertIn('getElementById("pref-headlines-more")', r.text)
        self.assertIn("Load next 20 stories", r.text)
        self.assertIn("data-story-link", r.text)
        self.assertIn("ScamCheck", r.text)

    def test_journalist_page_renders(self):
        from app import journalists

        journalists.record_sightings(
            [
                {
                    "url": "https://nytimes.com/kitchen-sink",
                    "title": "Kitchen sink story",
                    "source": "NYTimes",
                    "author": "Jane Doe",
                    "published": "2026-08-09T00:00:00Z",
                }
            ]
        )
        r = self.client.get("/journalist/jane-doe")
        self._assert_clean_200(r, "/journalist/{slug}")
        self.assertIn("Jane Doe", r.text)
        self.assertIn("data-story-link", r.text)

    def test_journalist_page_renders_with_no_sightings(self):
        r = self.client.get("/journalist/nobody-yet")
        self._assert_clean_200(r, "/journalist/{slug} (empty)")
        self.assertIn("Not enough data", r.text)

    def test_mynews_page_renders(self):
        r = self.client.get("/my")
        self._assert_clean_200(r, "/my")

    def test_safety_page_renders(self):
        r = self.client.get("/safety")
        self._assert_clean_200(r, "/safety")

    def test_admin_mod_renders_with_journalist_comment(self):
        import asyncio

        from app.comments import add_comment

        asyncio.run(add_comment("jrn-jane-doe", name="R", body="A comment about the byline."))
        r = self.client.get("/admin/mod", params={"token": ""})
        self._assert_clean_200(r, "/admin/mod")


if __name__ == "__main__":
    unittest.main()
