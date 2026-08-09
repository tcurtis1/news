import asyncio
import tempfile
import unittest
from pathlib import Path

from app import analytics


class AnalyticsTests(unittest.TestCase):
    def test_bot_detection_is_conservative(self):
        self.assertTrue(analytics.is_probable_bot("Googlebot/2.1"))
        self.assertTrue(analytics.is_probable_bot("python-requests/2.32"))
        self.assertTrue(analytics.is_probable_bot(""))
        self.assertTrue(analytics.is_probable_bot("Mozilla/5.0", "true"))
        self.assertFalse(
            analytics.is_probable_bot(
                "Mozilla/5.0 (iPhone; CPU iPhone OS 18_0 like Mac OS X) AppleWebKit/605.1.15"
            )
        )

    def test_client_events_are_allowlisted(self):
        with tempfile.TemporaryDirectory() as tmp:
            old_dir, old_path = analytics.CACHE_DIR, analytics.STORE_PATH
            analytics.CACHE_DIR = Path(tmp)
            analytics.STORE_PATH = Path(tmp) / "analytics.json"
            try:
                self.assertTrue(asyncio.run(analytics.record_client_event("session_new")))
                self.assertFalse(asyncio.run(analytics.record_client_event("<script>bad</script>")))
                stats = analytics.get_stats()
                self.assertEqual(stats["actions"], 1)
                self.assertEqual(stats["by_event"], {"session_new": 1})
                self.assertEqual(list(stats["by_day_event"].values()), [{"session_new": 1}])
            finally:
                analytics.CACHE_DIR, analytics.STORE_PATH = old_dir, old_path


if __name__ == "__main__":
    unittest.main()
