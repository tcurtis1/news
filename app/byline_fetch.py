"""
Fetch a single article page and try to read its byline from JSON-LD or a
<meta name="author"> tag. High precision over high recall on purpose — no
CSS-class/text-pattern scraping, since a wrong guess here misattributes a
named person's article to someone else.

Only called from app.journalists.run_backfill(), off the request path.
"""

from __future__ import annotations

import json
import logging
import re

import httpx

from app.journalists import normalize_author

log = logging.getLogger("byline_fetch")

USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsSearch/0.2; +https://news.yoyosup.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)
_META_AUTHOR_RE = re.compile(
    r'<meta[^>]+name=["\']author["\'][^>]+content=["\']([^"\']+)["\']',
    re.IGNORECASE,
)
_META_AUTHOR_RE_ALT = re.compile(
    r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']author["\']',
    re.IGNORECASE,
)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(6.0, connect=3.0),
        headers={"User-Agent": USER_AGENT},
        follow_redirects=True,
    )


def _author_from_jsonld_value(value: object) -> str | None:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        name = value.get("name")
        return name if isinstance(name, str) else None
    if isinstance(value, list) and value:
        return _author_from_jsonld_value(value[0])
    return None


def extract_byline_from_html(html: str) -> str | None:
    if not html:
        return None
    for block in _JSONLD_RE.findall(html)[:8]:
        try:
            data = json.loads(block.strip())
        except Exception:
            continue
        top_nodes = data if isinstance(data, list) else [data]
        for node in top_nodes:
            if not isinstance(node, dict):
                continue
            # Some sites nest the real article under a top-level @graph list.
            graph = node.get("@graph")
            search_nodes = graph if isinstance(graph, list) else [node]
            for n in search_nodes:
                if not isinstance(n, dict):
                    continue
                node_type = n.get("@type") or ""
                if isinstance(node_type, list):
                    node_type = " ".join(str(t) for t in node_type)
                if "Article" not in str(node_type):
                    continue
                author = _author_from_jsonld_value(n.get("author"))
                cleaned = normalize_author(author)
                if cleaned:
                    return cleaned

    m = _META_AUTHOR_RE.search(html) or _META_AUTHOR_RE_ALT.search(html)
    if m:
        cleaned = normalize_author(m.group(1))
        if cleaned:
            return cleaned

    return None


async def fetch_byline(client: httpx.AsyncClient, url: str) -> str | None:
    try:
        r = await client.get(url)
        if r.status_code != 200:
            return None
        return extract_byline_from_html(r.text)
    except Exception as e:
        log.debug("fetch_byline failed for %s: %s", url, e)
        return None
