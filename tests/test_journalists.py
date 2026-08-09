import json
import tempfile
import unittest
from pathlib import Path

from app import journalists


class NormalizeAuthorTests(unittest.TestCase):
    def test_plain_name_passes(self):
        self.assertEqual(journalists.normalize_author("Jane Doe"), "Jane Doe")

    def test_by_prefix_stripped(self):
        self.assertEqual(journalists.normalize_author("By Jane Doe"), "Jane Doe")
        self.assertEqual(journalists.normalize_author("BY Jane Doe"), "Jane Doe")

    def test_rss_email_paren_form_extracts_name(self):
        self.assertEqual(
            journalists.normalize_author("jane@outlet.com (Jane Doe)"), "Jane Doe"
        )

    def test_bare_email_rejected(self):
        self.assertIsNone(journalists.normalize_author("jane@outlet.com"))

    def test_multi_author_rejected(self):
        self.assertIsNone(journalists.normalize_author("Jane Doe and John Smith"))
        self.assertIsNone(journalists.normalize_author("Jane Doe, John Smith"))
        self.assertIsNone(journalists.normalize_author("Jane Doe & John Smith"))

    def test_generic_bylines_rejected(self):
        self.assertIsNone(journalists.normalize_author("Staff Writer"))
        self.assertIsNone(journalists.normalize_author("Associated Press"))
        self.assertIsNone(journalists.normalize_author("Reuters Staff"))
        self.assertIsNone(journalists.normalize_author("Editorial Board"))

    def test_empty_and_none_rejected(self):
        self.assertIsNone(journalists.normalize_author(None))
        self.assertIsNone(journalists.normalize_author(""))
        self.assertIsNone(journalists.normalize_author("   "))

    def test_too_long_rejected(self):
        self.assertIsNone(journalists.normalize_author("A" * 100))

    def test_url_like_rejected(self):
        self.assertIsNone(journalists.normalize_author("https://outlet.com/staff/jane"))


class EstimateLeanTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = journalists.JOURNALISTS_DIR
        self._old_pending = journalists.PENDING_FILE
        journalists.JOURNALISTS_DIR = Path(self._tmp.name)
        journalists.PENDING_FILE = journalists.JOURNALISTS_DIR / "_pending.json"

    def tearDown(self):
        journalists.JOURNALISTS_DIR = self._old_dir
        journalists.PENDING_FILE = self._old_pending
        self._tmp.cleanup()

    def _seed(self, slug: str, domains: list[str]) -> None:
        journalists.JOURNALISTS_DIR.mkdir(parents=True, exist_ok=True)
        sightings = [
            {"url": f"https://x.test/{i}", "title": "t", "domain": d, "source": d}
            for i, d in enumerate(domains)
        ]
        path = journalists._profile_path(slug)
        path.write_text(
            json.dumps({"slug": slug, "name": "Jane Doe", "sightings": sightings}),
            encoding="utf-8",
        )

    def test_not_enough_data_below_min_countable(self):
        # nytimes.com is LEAN_LEFT in bias.py; only 2 countable sightings < MIN_COUNTABLE(3)
        self._seed("jane-doe", ["nytimes.com", "nytimes.com"])
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_NOT_ENOUGH_DATA)

    def test_leans_liberal_below_full_threshold_count(self):
        # 3 nytimes.com sightings: countable=3 < MIN_FOR_FULL_LABEL(5) -> "leans", not full
        self._seed("jane-doe", ["nytimes.com"] * 3)
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_LEANS_LIBERAL)

    def test_full_liberal_label_at_5_and_75pct(self):
        # 5 sightings, all nytimes.com -> countable=5, share=1.0 >= FULL_THRESHOLD
        self._seed("jane-doe", ["nytimes.com"] * 5)
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_LIBERAL)

    def test_leans_conservative_just_under_full_threshold(self):
        # 5 sightings, 3 foxnews.com (right) + 2 reuters.com (center):
        # countable=5, right_share=0.6 -> >=0.5 leans, <0.75 not full
        self._seed("jane-doe", ["foxnews.com"] * 3 + ["reuters.com"] * 2)
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_LEANS_CONSERVATIVE)

    def test_center_when_split_evenly(self):
        self._seed("jane-doe", ["nytimes.com", "foxnews.com", "reuters.com"])
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_CENTER)

    def test_center_when_center_dominant(self):
        self._seed(
            "jane-doe", ["reuters.com", "reuters.com", "reuters.com", "nytimes.com"]
        )
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_CENTER)

    def test_unclear_domains_excluded_from_countable(self):
        # reddit.com maps to unclear and shouldn't count toward MIN_COUNTABLE
        self._seed("jane-doe", ["nytimes.com", "nytimes.com", "reddit.com"])
        result = journalists.estimate_lean("jane-doe")
        self.assertEqual(result["journalist_lean"], journalists.J_NOT_ENOUGH_DATA)


class RecordAndQueueTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._old_dir = journalists.JOURNALISTS_DIR
        self._old_pending = journalists.PENDING_FILE
        journalists.JOURNALISTS_DIR = Path(self._tmp.name)
        journalists.PENDING_FILE = journalists.JOURNALISTS_DIR / "_pending.json"

    def tearDown(self):
        journalists.JOURNALISTS_DIR = self._old_dir
        journalists.PENDING_FILE = self._old_pending
        self._tmp.cleanup()

    def test_record_sightings_creates_profile(self):
        hits = [
            {
                "url": "https://nytimes.com/a1",
                "title": "Story",
                "source": "NYTimes",
                "author": "Jane Doe",
                "published": None,
            }
        ]
        journalists.record_sightings(hits)
        profile = journalists.load_profile("jane-doe")
        self.assertIsNotNone(profile)
        self.assertEqual(len(profile["sightings"]), 1)
        self.assertEqual(profile["sightings"][0]["domain"], "nytimes.com")

    def test_record_sightings_dedups_by_url(self):
        hit = {
            "url": "https://nytimes.com/a1",
            "title": "Story",
            "source": "NYTimes",
            "author": "Jane Doe",
        }
        journalists.record_sightings([hit])
        journalists.record_sightings([hit])
        profile = journalists.load_profile("jane-doe")
        self.assertEqual(len(profile["sightings"]), 1)

    def test_queue_for_backfill_skips_authored_hits(self):
        hits = [
            {"url": "https://a.test/1", "title": "t", "source": "A", "author": "Jane Doe"},
            {"url": "https://a.test/2", "title": "t2", "source": "A", "author": None},
        ]
        journalists.queue_for_backfill(hits)
        pending = journalists._load_json(journalists.PENDING_FILE, [])
        urls = [p["url"] for p in pending]
        self.assertNotIn("https://a.test/1", urls)
        self.assertIn("https://a.test/2", urls)


if __name__ == "__main__":
    unittest.main()
