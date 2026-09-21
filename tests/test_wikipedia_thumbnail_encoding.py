"""Regression test for the Wikipedia REST path-encoding bug in Stage 3 of
resolve_article_thumbnail: quote_plus() renders a space as "+", which is valid
in a query string but not a URL path segment, so every multi-word entity
lookup (e.g. "Paris Hilton") 404'd against Wikipedia's REST API and silently
fell through to a shorter, often wrong, single-word candidate."""

import unittest
from unittest.mock import AsyncMock

import httpx

from app.search import resolve_article_thumbnail


def _empty_bing_response(url: str) -> httpx.Response:
    # No <item> in the body -> Stage 1 (Bing News RSS) finds nothing for any
    # candidate, so resolve_article_thumbnail falls through to Stage 3.
    return httpx.Response(200, content=b"<rss></rss>", request=httpx.Request("GET", url))


class WikipediaThumbnailEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def test_multiword_entity_is_percent_encoded_not_plus_encoded(self):
        requested_urls = []

        async def fake_get(url, *args, **kwargs):
            requested_urls.append(url)
            if "wikipedia.org" in url:
                return httpx.Response(404, request=httpx.Request("GET", url))
            return _empty_bing_response(url)

        client = AsyncMock(spec=httpx.AsyncClient)
        client.get.side_effect = fake_get

        # url is a news.google.com link, so Stage 2 (direct OpenGraph fetch)
        # is skipped and we exercise Stage 1 -> Stage 3 exactly as in
        # production for Google-News-sourced hits.
        await resolve_article_thumbnail(
            "https://news.google.com/rss/articles/some-story",
            title="Paris Hilton Sent Love on Presley Gerber's Final Instagram Post",
            client=client,
        )

        wiki_urls = [u for u in requested_urls if "wikipedia.org" in u]
        self.assertTrue(wiki_urls, "expected at least one Wikipedia REST lookup")
        for u in wiki_urls:
            self.assertNotIn("+", u, f"space encoded as '+' in a REST path segment: {u}")

if __name__ == "__main__":
    unittest.main()
