"""Story cards should use the picture on the article link (og:image).

Google News RSS items point at news.google.com/rss/articles/... which has no
article photo. The previous resolver skipped OpenGraph for those URLs, Bing
often had no image, and Wikipedia then matched a generic noun in the headline
("Autopsy" → Rembrandt's Anatomy Lesson on a Hayden Panettiere story).
"""

import base64
import json
import unittest
from unittest.mock import AsyncMock, patch

import httpx

from app import search
from app.search import (
    _extract_og_image,
    _google_news_article_id,
    _publisher_url_from_batchexecute,
    _publisher_url_from_gnews_token,
    _wiki_title_fits_headline,
    resolve_article_thumbnail,
)


HAYDEN_TITLE = "Autopsy shows Hayden Panettiere died from a drug overdose"
HAYDEN_GNEWS = (
    "https://news.google.com/rss/articles/"
    "CBMiW0FVX3lxTE9PaV9kSkNsQUplNUp0VnlxbEpFX0d6SU5kVGxLNWd1eVRFNHVYNWEt"
    "TXR5SDlNc1RHNkQwelVoTHZDSUVEZC1XZGljbFExeW1LSHJUQU1TQlpNSmM?oc=5"
)
BBC_URL = "https://www.bbc.com/news/articles/crn45d8dd2wdo"
BBC_OG = "https://ichef.bbci.co.uk/news/1024/branded_news/hayden.jpg"
WIKI_AUTOPSY = "https://upload.wikimedia.org/wikipedia/commons/rembrandt-anatomy.jpg"


def _html_response(url: str, html: str, content_type: str = "text/html") -> httpx.Response:
    return httpx.Response(
        200,
        text=html,
        headers={"content-type": content_type},
        request=httpx.Request("GET", url),
    )


def _empty_bing(url: str) -> httpx.Response:
    return httpx.Response(200, content=b"<rss></rss>", request=httpx.Request("GET", url))


class OgImageHelpersTests(unittest.TestCase):
    def test_extracts_property_og_image(self):
        html = f'<meta property="og:image" content="{BBC_OG}">'
        self.assertEqual(_extract_og_image(html, BBC_URL), BBC_OG)

    def test_extracts_content_before_property(self):
        html = f'<meta content="{BBC_OG}" property="og:image">'
        self.assertEqual(_extract_og_image(html, BBC_URL), BBC_OG)

    def test_resolves_relative_og_image(self):
        html = '<meta property="og:image" content="/photo.jpg">'
        self.assertEqual(
            _extract_og_image(html, "https://www.bbc.com/news/story"),
            "https://www.bbc.com/photo.jpg",
        )


class GoogleNewsUnwrapHelpersTests(unittest.TestCase):
    def test_article_id_from_rss_url(self):
        art_id = _google_news_article_id(HAYDEN_GNEWS)
        self.assertTrue(art_id)
        self.assertTrue(art_id.startswith("CBMi"))

    def test_embedded_publisher_url_in_old_token(self):
        publisher = "https://www.bbc.com/news/articles/crn45d8dd2wdo"
        raw = b'\x08\x13"' + bytes([len(publisher)]) + publisher.encode("ascii")
        token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        self.assertEqual(_publisher_url_from_gnews_token(token), publisher)

    def test_au_token_has_no_embedded_url(self):
        art_id = _google_news_article_id(HAYDEN_GNEWS)
        self.assertIsNone(_publisher_url_from_gnews_token(art_id or ""))

    def test_batchexecute_json_payload(self):
        nested = json.dumps(["garturlres", BBC_URL, 1])
        body = ")]}'\n" + json.dumps([["wrb.fr", "Fbv4je", nested, None, None, None, ""]])
        self.assertEqual(_publisher_url_from_batchexecute(body), BBC_URL)


class WikiHeadlineFitTests(unittest.TestCase):
    def test_autopsy_page_does_not_fit_person_headline(self):
        self.assertFalse(_wiki_title_fits_headline("Autopsy", HAYDEN_TITLE))

    def test_person_page_fits_when_both_names_are_in_the_headline(self):
        self.assertTrue(_wiki_title_fits_headline("Hayden Panettiere", HAYDEN_TITLE))

    def test_unrelated_two_word_page_is_rejected(self):
        self.assertFalse(_wiki_title_fits_headline("Anatomy Lesson", HAYDEN_TITLE))


class ResolveArticleThumbnailTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._saved = dict(search._IMAGE_CACHE)
        search._IMAGE_CACHE.clear()
        patcher = patch("app.search._save_image_cache_debounced", lambda: None)
        patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        search._IMAGE_CACHE.clear()
        search._IMAGE_CACHE.update(self._saved)

    async def test_google_news_uses_publisher_og_image_not_wikipedia(self):
        requested = []

        async def fake_get(url, *args, **kwargs):
            requested.append(("GET", url))
            if "news.google.com/articles/" in url:
                return _html_response(url, '<div data-n-a-ts="1" data-n-a-sg="sig"></div>')
            if "bbc.com" in url:
                return _html_response(url, f'<meta property="og:image" content="{BBC_OG}">')
            if "wikipedia.org" in url:
                return httpx.Response(
                    200,
                    json={
                        "title": "Autopsy",
                        "thumbnail": {"source": WIKI_AUTOPSY},
                    },
                    request=httpx.Request("GET", url),
                )
            if "bing.com" in url:
                return _empty_bing(url)
            return httpx.Response(404, request=httpx.Request("GET", url))

        async def fake_post(url, *args, **kwargs):
            requested.append(("POST", url))
            nested = json.dumps(["garturlres", BBC_URL, 1])
            body = ")]}'\n" + json.dumps([["wrb.fr", "Fbv4je", nested, None, None, None, ""]])
            return httpx.Response(200, text=body, request=httpx.Request("POST", url))

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = fake_get
        client.post.side_effect = fake_post

        img = await resolve_article_thumbnail(HAYDEN_GNEWS, title=HAYDEN_TITLE, client=client)
        self.assertEqual(img, BBC_OG)
        self.assertTrue(any(u.startswith("https://news.google.com/articles/") for m, u in requested if m == "GET"))
        self.assertTrue(any("batchexecute" in u for m, u in requested if m == "POST"))
        self.assertFalse(any("wikipedia.org" in u for _, u in requested))

    async def test_wikipedia_autopsy_is_not_used_when_og_and_bing_fail(self):
        async def fake_get(url, *args, **kwargs):
            if "wikipedia.org" in url:
                return httpx.Response(
                    200,
                    json={
                        "title": "Autopsy",
                        "type": "standard",
                        "thumbnail": {"source": WIKI_AUTOPSY},
                    },
                    request=httpx.Request("GET", url),
                )
            if "bing.com" in url:
                return _empty_bing(url)
            # No signature on the Google News viewer → unwrap fails
            return _html_response(url, "<html></html>")

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = fake_get
        client.post.side_effect = Exception("no batchexecute")

        img = await resolve_article_thumbnail(HAYDEN_GNEWS, title=HAYDEN_TITLE, client=client)
        self.assertIsNone(img)

    async def test_direct_publisher_url_uses_og_image_before_bing_or_wiki(self):
        requested = []

        async def fake_get(url, *args, **kwargs):
            requested.append(url)
            if "bbc.com" in url:
                return _html_response(url, f'<meta property="og:image" content="{BBC_OG}">')
            raise AssertionError(f"should not fetch fallback {url}")

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = fake_get

        img = await resolve_article_thumbnail(BBC_URL, title=HAYDEN_TITLE, client=client)
        self.assertEqual(img, BBC_OG)
        self.assertTrue(any("bbc.com" in u for u in requested))
        self.assertFalse(any("bing.com" in u or "wikipedia.org" in u for u in requested))

    async def test_embedded_gnews_token_skips_batchexecute(self):
        publisher = "https://www.bbc.com/news/articles/crn45d8dd2wdo"
        raw = b'\x08\x13"' + bytes([len(publisher)]) + publisher.encode("ascii")
        token = base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
        gnews = f"https://news.google.com/rss/articles/{token}"

        async def fake_get(url, *args, **kwargs):
            if "bbc.com" in url:
                return _html_response(url, f'<meta property="og:image" content="{BBC_OG}">')
            raise AssertionError(f"unexpected GET {url}")

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = fake_get
        client.post.side_effect = AssertionError("batchexecute should not run")

        img = await resolve_article_thumbnail(gnews, title=HAYDEN_TITLE, client=client)
        self.assertEqual(img, BBC_OG)
        client.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
