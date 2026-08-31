import unittest
from unittest.mock import patch

from app import search


class GetCachedThumbnailTests(unittest.TestCase):
    def setUp(self):
        self._saved = dict(search._IMAGE_CACHE)
        search._IMAGE_CACHE.clear()

    def tearDown(self):
        search._IMAGE_CACHE.clear()
        search._IMAGE_CACHE.update(self._saved)

    def test_missing_key_is_a_miss(self):
        self.assertIsNone(search.get_cached_thumbnail("https://example.com/a", "Some Title"))

    def test_matching_title_hits(self):
        search._IMAGE_CACHE["u"] = {"img": "https://img/a.jpg", "title_fp": search._title_fingerprint("Trump vows to hit Iran hard")}
        self.assertEqual(
            search.get_cached_thumbnail("u", "Trump vows to hit Iran hard"), "https://img/a.jpg"
        )

    def test_changed_headline_invalidates_cached_image(self):
        # Regression: Google News reuses one URL for an evolving "Live
        # Updates" story. A thumbnail cached for an earlier version of the
        # headline (e.g. tied to an unrelated "US Open" story that once
        # shared this cache_key) must not keep being served once the
        # headline has moved on -- it should look like a cache miss so the
        # caller re-resolves.
        search._IMAGE_CACHE["u"] = {"img": "https://img/us-open.jpg", "title_fp": search._title_fingerprint("US Open: order of play")}
        self.assertIsNone(
            search.get_cached_thumbnail("u", "Live Updates: Trump vows to hit Iran hard")
        )

    def test_legacy_string_entry_trusted_when_no_title_given(self):
        search._IMAGE_CACHE["u"] = "https://img/legacy.jpg"
        self.assertEqual(search.get_cached_thumbnail("u"), "https://img/legacy.jpg")

    def test_legacy_string_entry_is_a_miss_once_a_title_is_known(self):
        # Legacy entries predate title tracking, so the first lookup that
        # does have a title to check gets one re-resolution instead of
        # trusting a photo that might belong to a different headline.
        search._IMAGE_CACHE["u"] = "https://img/legacy.jpg"
        self.assertIsNone(search.get_cached_thumbnail("u", "Any headline"))


class ResolveArticleThumbnailCachingTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(search._IMAGE_CACHE)
        search._IMAGE_CACHE.clear()
        patcher = patch("app.search._save_image_cache_debounced", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        search._IMAGE_CACHE.clear()
        search._IMAGE_CACHE.update(self._saved)

    async def test_headline_change_forces_a_fresh_resolution_not_the_stale_image(self):
        url = "https://news.google.com/rss/articles/some-live-updates-story"

        search._IMAGE_CACHE[url] = {
            "img": "https://img/us-open.jpg",
            "title_fp": search._title_fingerprint("US Open: order of play"),
        }

        cache_key = url
        title = "Live Updates: Trump vows to hit Iran hard"

        # The stale entry (cached under an earlier, unrelated headline) is
        # correctly treated as a miss.
        self.assertIsNone(search.get_cached_thumbnail(cache_key, title))

        # Simulate a successful re-resolution the way any of resolve_article_
        # thumbnail's three stages would record one.
        search._IMAGE_CACHE[cache_key] = {"img": "https://img/iran-strikes.jpg", "title_fp": search._title_fingerprint(title)}

        # Now cached under the new headline, and matches on the next lookup.
        self.assertEqual(
            search.get_cached_thumbnail(url, "Live Updates: Trump vows to hit Iran hard"),
            "https://img/iran-strikes.jpg",
        )
        # A further headline update (still the same live-updates URL) once
        # again invalidates the now-stale cached image.
        self.assertIsNone(
            search.get_cached_thumbnail(url, "Live Updates: Iran and US agree to ceasefire")
        )


if __name__ == "__main__":
    unittest.main()
