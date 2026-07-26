"""Meta search — multi-source news + daily platform trends."""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus, urlparse
from xml.etree import ElementTree as ET

import httpx

from app.bias import aggregate_lean, enrich_hits
from app.places import resolve_place
from app.source_prefs import (
    filter_hits,
    google_batch_budget,
    normalize_pref,
    prefer_topical,
    pref_meta,
    rss_feeds_for,
    site_query_batches,
    topical_score,
)
from app.trends import build_trends, or_phrases, rank_lookup

log = logging.getLogger("search")

USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsSearch/0.2; +https://news.yoyosup.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_PER_SOURCE = 12
MAX_RESULTS = 40
MAX_LEAN_PER_BATCH = 16
# In-process RSS cache so MyNews doesn't re-hit every outlet on every card
_RSS_CACHE: dict[str, tuple[float, list["SearchHit"]]] = {}
_RSS_CACHE_TTL = 8 * 60  # seconds
_RSS_PER_FEED = 8
_RSS_CONCURRENCY = 12
# Google News 503s when we fire too many site: batches at once
_GNEWS_CONCURRENCY = 3


@dataclass
class SearchHit:
    title: str
    url: str
    source: str
    snippet: str = ""
    score: int = 0
    comments_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PortalLink:
    name: str
    url: str
    kind: str  # web | video | social | ai | news

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _clean_query(q: str) -> str:
    return re.sub(r"\s+", " ", (q or "").strip())[:200]


def portal_links(q: str) -> list[PortalLink]:
    """Primary portals = mass platforms. HN is optional niche, listed last."""
    enc = quote_plus(q)
    return [
        PortalLink("Google", f"https://www.google.com/search?q={enc}", "web"),
        PortalLink("Google News", f"https://news.google.com/search?q={enc}", "news"),
        PortalLink("Bing", f"https://www.bing.com/search?q={enc}", "web"),
        PortalLink("Bing News", f"https://www.bing.com/news/search?q={enc}", "news"),
        PortalLink("YouTube", f"https://www.youtube.com/results?search_query={enc}", "video"),
        PortalLink("X", f"https://x.com/search?q={enc}&src=typed_query", "social"),
        PortalLink("Polymarket", f"https://polymarket.com/search?_q={enc}", "news"),
        PortalLink("TikTok", f"https://www.tiktok.com/search?q={enc}", "video"),
        PortalLink("Facebook", f"https://www.facebook.com/search/top/?q={enc}", "social"),
        PortalLink("Instagram", f"https://www.instagram.com/explore/search/keyword/?q={enc}", "social"),
        PortalLink("Reddit", f"https://www.reddit.com/search/?q={enc}", "social"),
        PortalLink("Grok", f"https://grok.com/?q={enc}", "ai"),
        PortalLink("ChatGPT", f"https://chatgpt.com/?q={enc}", "ai"),
        # Niche tech index — keep available, never front of the list
        PortalLink("Hacker News (tech)", f"https://hn.algolia.com/?q={enc}", "news"),
    ]


async def _fetch_hn(client: httpx.AsyncClient, q: str) -> list[SearchHit]:
    try:
        r = await client.get(
            "https://hn.algolia.com/api/v1/search",
            params={"query": q, "tags": "story", "hitsPerPage": MAX_PER_SOURCE},
        )
        r.raise_for_status()
        hits = r.json().get("hits") or []
        out: list[SearchHit] = []
        for h in hits:
            title = (h.get("title") or h.get("story_title") or "").strip()
            if not title:
                continue
            object_id = h.get("objectID")
            url = (h.get("url") or "").strip()
            comments = (
                f"https://news.ycombinator.com/item?id={object_id}" if object_id else None
            )
            if not url:
                url = comments or f"https://hn.algolia.com/?q={quote_plus(q)}"
            points = int(h.get("points") or 0)
            num_comments = int(h.get("num_comments") or 0)
            author = h.get("author") or ""
            snippet_parts = []
            if points:
                snippet_parts.append(f"{points} pts")
            if num_comments:
                snippet_parts.append(f"{num_comments} comments")
            if author:
                snippet_parts.append(f"by {author}")
            # Strongly down-weight vs Google/Bing News so HN stays a niche supplement
            score = max(1, (points + num_comments) // 25)
            out.append(
                SearchHit(
                    title=title,
                    url=url,
                    source="Hacker News (tech)",
                    snippet=" · ".join(snippet_parts + ["niche tech index"]),
                    score=score,
                    comments_url=comments,
                )
            )
        return out
    except Exception as e:
        log.warning("HN search failed: %s", e)
        return []


_gnews_sem: asyncio.Semaphore | None = None


def _get_gnews_sem() -> asyncio.Semaphore:
    global _gnews_sem
    if _gnews_sem is None:
        _gnews_sem = asyncio.Semaphore(_GNEWS_CONCURRENCY)
    return _gnews_sem


async def _fetch_google_news(
    client: httpx.AsyncClient,
    q: str,
    *,
    limit: int = MAX_PER_SOURCE,
    score_base: int = 1000,
) -> list[SearchHit]:
    try:
        async with _get_gnews_sem():
            r = await client.get(
                "https://news.google.com/rss/search",
                params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
            )
            # Brief pause after each Google hit so parallel batches don't 503
            await asyncio.sleep(0.12)
        if r.status_code == 503:
            log.warning("Google News 503 for q=%s…", (q or "")[:60])
            return []
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out: list[SearchHit] = []
        for i, entry in enumerate(root.findall(".//item")[:limit]):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            if not title:
                continue
            source = (entry.findtext("source") or "Google News").strip()
            out.append(
                SearchHit(
                    title=title,
                    url=link or f"https://news.google.com/search?q={quote_plus(q)}",
                    source=f"Google News · {source}" if source != "Google News" else "Google News",
                    snippet="Google News",
                    score=score_base - i,
                )
            )
        return out
    except Exception as e:
        log.warning("Google News search failed: %s", e)
        return []


async def _fetch_google_news_for_pref(
    client: httpx.AsyncClient,
    topic: str,
    lean: str,
    *,
    lite: bool = False,
    max_batches: int | None = None,
) -> list[SearchHit]:
    """
    Pull headlines from preferred outlet domains via Google News site: operators.
    Batches cover most of the preferred domain list (esp. conservative).
    Concurrency is capped inside _fetch_google_news to avoid 503 storms.
    """
    bs, mb = google_batch_budget(lean, lite=lite)
    if max_batches is not None:
        mb = max_batches
    batches = site_query_batches(
        lean, topic, batch_size=bs, max_batches=mb, lite=lite
    )
    if not batches:
        return []
    results = await asyncio.gather(
        *[
            _fetch_google_news(
                client,
                bq,
                limit=MAX_LEAN_PER_BATCH,
                score_base=1200 - (i * 40),
            )
            for i, bq in enumerate(batches)
        ]
    )
    merged: list[SearchHit] = []
    for lst in results:
        merged.extend(lst)
    return merged


def _strip_html(s: str) -> str:
    return re.sub(r"<[^>]+>", "", s or "").strip()


def _parse_rss_items(
    content: bytes | str,
    *,
    source_name: str,
    limit: int = _RSS_PER_FEED,
) -> list[SearchHit]:
    """Parse RSS 2.0 / Atom loosely; tolerate messy feeds."""
    try:
        if isinstance(content, str):
            raw = content.encode("utf-8", errors="ignore")
        else:
            raw = content
        text = raw.lstrip()
        if not text.startswith(b"<?xml") and not text.startswith(b"<"):
            idx = text.find(b"<rss")
            if idx < 0:
                idx = text.find(b"<feed")
            if idx >= 0:
                text = text[idx:]
        root = ET.fromstring(text)
    except Exception as e:
        log.debug("RSS parse failed for %s: %s", source_name, e)
        return []

    out: list[SearchHit] = []
    # RSS 2.0
    items = root.findall(".//item")
    if not items:
        # Atom
        ns = {"a": "http://www.w3.org/2005/Atom"}
        items = root.findall("a:entry", ns) or root.findall(
            "{http://www.w3.org/2005/Atom}entry"
        )

    for i, entry in enumerate(items[:limit]):
        title = (
            entry.findtext("title")
            or entry.findtext("{http://www.w3.org/2005/Atom}title")
            or ""
        ).strip()
        title = _strip_html(title)
        if not title:
            continue
        link = (entry.findtext("link") or "").strip()
        if not link:
            link_el = entry.find("link")
            if link_el is not None and link_el.get("href"):
                link = link_el.get("href") or ""
            else:
                link_el = entry.find("{http://www.w3.org/2005/Atom}link")
                if link_el is not None:
                    link = link_el.get("href") or (link_el.text or "")
        link = (link or "").strip()
        if not link:
            continue
        desc = _strip_html(
            entry.findtext("description")
            or entry.findtext("{http://www.w3.org/2005/Atom}summary")
            or ""
        )
        out.append(
            SearchHit(
                title=title,
                url=link,
                source=source_name,
                snippet=(desc[:140] + "…") if len(desc) > 140 else desc,
                score=1100 - i,
            )
        )
    return out


async def _fetch_one_rss(
    client: httpx.AsyncClient,
    name: str,
    domain: str,
    feed_url: str,
) -> list[SearchHit]:
    now = time.time()
    cached = _RSS_CACHE.get(feed_url)
    if cached and (now - cached[0]) < _RSS_CACHE_TTL:
        return list(cached[1])
    try:
        r = await client.get(
            feed_url,
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/atom+xml, application/xml, text/xml, */*",
            },
        )
        if r.status_code != 200:
            log.debug("RSS %s status %s", name, r.status_code)
            return []
        hits = _parse_rss_items(r.content, source_name=name, limit=_RSS_PER_FEED)
        # Tag domain for filter matching if URL host is opaque
        for h in hits:
            if not h.url:
                continue
            host = (urlparse(h.url).hostname or "").lower().removeprefix("www.")
            if not host and domain:
                # keep as-is
                pass
        _RSS_CACHE[feed_url] = (now, hits)
        return list(hits)
    except Exception as e:
        log.debug("RSS fetch failed %s: %s", name, e)
        return []


async def _fetch_outlet_rss_for_pref(
    client: httpx.AsyncClient,
    lean: str,
    topic: str = "",
    *,
    lite: bool = False,
) -> list[SearchHit]:
    """
    Scrape native outlet RSS feeds for the preferred lean.
    Diversifies beyond Google News site: (which over-weights Fox/NYPost).
    When topic is set, keep items that match the topic; otherwise take tops.
    """
    p = normalize_pref(lean)
    # Conservative needs the widest feed set
    if lite:
        limit = 28 if p == "conservative" else (20 if p == "liberal" else 14)
    else:
        limit = 38 if p == "conservative" else (28 if p == "liberal" else 18)
    feeds = rss_feeds_for(lean, limit=limit)
    if not feeds:
        return []

    sem = asyncio.Semaphore(_RSS_CONCURRENCY)

    async def one(name: str, domain: str, url: str) -> list[SearchHit]:
        async with sem:
            return await _fetch_one_rss(client, name, domain, url)

    results = await asyncio.gather(
        *[one(n, d, u) for n, d, u in feeds],
        return_exceptions=True,
    )
    merged: list[SearchHit] = []
    for res in results:
        if isinstance(res, list):
            merged.extend(res)

    topic = (topic or "").strip()
    if topic:
        # Prefer topical titles; also check snippets for soft matches
        scored: list[tuple[int, SearchHit]] = []
        soft: list[SearchHit] = []
        for h in merged:
            s = topical_score(h.title, topic)
            if s <= 0 and h.snippet:
                s = topical_score(h.snippet, topic)
                if s > 0:
                    s = max(1, s // 2)
            if s > 0:
                h.score = h.score + s * 20
                scored.append((s, h))
            else:
                soft.append(h)
        if scored:
            scored.sort(key=lambda x: (x[0], x[1].score), reverse=True)
            out = [h for _, h in scored]
            # Pad with a light sample of other recent RSS so the feed
            # doesn't look empty when few outlets covered the topic today
            if len(out) < 8:
                out.extend(soft[: 12 - len(out)])
            return out
        # No topical matches — still return a diverse slice of recent RSS
        # (caller/Google may add more; better than zero when GNews 503s)
        return soft[:20]
    return merged


def diversify_hits(
    hits: list[SearchHit] | list[dict[str, Any]],
    *,
    limit: int = MAX_RESULTS,
    max_per_source: int = 3,
) -> list:
    """
    Round-robin by outlet so Fox/NYPost don't fill the whole list.
    Works on SearchHit or hit dicts.
    """
    if not hits:
        return []

    def src_key(h) -> str:
        if isinstance(h, SearchHit):
            s = (h.source or "").lower()
            u = (h.url or "").lower()
        else:
            s = (h.get("source") or "").lower()
            u = (h.get("url") or "").lower()
        # Prefer domain from URL
        try:
            host = (urlparse(u).hostname or "").lower().removeprefix("www.")
            if host:
                return host
        except Exception:
            pass
        # "Google News · Fox News" → fox news
        if "·" in s:
            s = s.split("·")[-1].strip()
        return s or "unknown"

    buckets: dict[str, list] = {}
    order: list[str] = []
    for h in hits:
        k = src_key(h)
        if k not in buckets:
            buckets[k] = []
            order.append(k)
        if len(buckets[k]) < max_per_source:
            buckets[k].append(h)

    out: list = []
    # Round-robin across sources
    while len(out) < limit:
        progressed = False
        for k in order:
            if buckets[k]:
                out.append(buckets[k].pop(0))
                progressed = True
                if len(out) >= limit:
                    break
        if not progressed:
            break
    return out


async def _fetch_bing_news(client: httpx.AsyncClient, q: str) -> list[SearchHit]:
    try:
        r = await client.get(
            "https://www.bing.com/news/search",
            params={"q": q, "format": "RSS", "market": "en-US"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        if r.status_code != 200 or b"<item>" not in r.content:
            log.warning("Bing news search status %s", r.status_code)
            return []
        text = r.content
        if not text.lstrip().startswith(b"<?xml") and not text.lstrip().startswith(b"<rss"):
            idx = text.find(b"<rss")
            text = text[idx:] if idx >= 0 else text
        root = ET.fromstring(text)
        out: list[SearchHit] = []
        for i, entry in enumerate(root.findall(".//item")[:MAX_PER_SOURCE]):
            title = (entry.findtext("title") or "").strip()
            link = (entry.findtext("link") or "").strip()
            if not title:
                continue
            desc = re.sub(r"<[^>]+>", "", entry.findtext("description") or "").strip()
            out.append(
                SearchHit(
                    title=title,
                    url=link or f"https://www.bing.com/news/search?q={quote_plus(q)}",
                    source="Bing News",
                    snippet=(desc[:160] + "…") if len(desc) > 160 else desc,
                    score=900 - i,
                )
            )
        return out
    except Exception as e:
        log.warning("Bing news search failed: %s", e)
        return []


def _parse_reddit_search_atom(xml_text: str, limit: int) -> list[SearchHit]:
    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    out: list[SearchHit] = []
    for i, entry in enumerate(root.findall("a:entry", ns)):
        if i >= limit:
            break
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        link_el = entry.find("a:link", ns)
        comments = (link_el.get("href") if link_el is not None else None) or None
        cat = entry.find("a:category", ns)
        sub = (cat.get("label") or cat.get("term") or "") if cat is not None else ""
        if sub.startswith("r/"):
            sub = sub[2:]
        content = entry.findtext("a:content", default="", namespaces=ns) or ""
        url = comments or "https://www.reddit.com/"
        m = re.search(r'href="(https?://[^"]+)"', content)
        if m and "reddit.com" not in m.group(1):
            url = m.group(1)
        snippet = f"r/{sub}" if sub else "Reddit search"
        out.append(
            SearchHit(
                title=title,
                url=url,
                source=f"Reddit r/{sub}" if sub else "Reddit",
                snippet=snippet,
                score=max(1, limit - i) * 10,
                comments_url=comments,
            )
        )
    return out


async def _fetch_reddit(client: httpx.AsyncClient, q: str) -> list[SearchHit]:
    try:
        r = await client.get(
            "https://www.reddit.com/search.rss",
            params={"q": q, "sort": "relevance", "t": "year"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/atom+xml, application/xml, text/xml, */*",
            },
        )
        if r.status_code != 200:
            log.warning("Reddit search RSS status %s", r.status_code)
            return []
        return _parse_reddit_search_atom(r.text, MAX_PER_SOURCE)
    except Exception as e:
        log.warning("Reddit search failed: %s", e)
        return []


def _merge_hits(lists: list[list[SearchHit]]) -> list[SearchHit]:
    seen: dict[str, SearchHit] = {}

    def key_for(h: SearchHit) -> str:
        u = (h.url or "").split("?")[0].rstrip("/").lower()
        if u and "reddit.com" not in u and "ycombinator.com" not in u:
            return f"url:{u}"
        t = re.sub(r"\W+", " ", h.title.lower()).strip()
        return f"title:{t[:80]}"

    for lst in lists:
        for h in lst:
            k = key_for(h)
            if k not in seen or h.score > seen[k].score:
                seen[k] = h

    return sorted(seen.values(), key=lambda h: h.score, reverse=True)[:MAX_RESULTS]


async def _run_search_lite(
    query: str,
    geo: str | None = None,
    lean: str | None = None,
) -> dict[str, Any]:
    """
    Fast path for MyNews cards: preferred-source headlines + rank map only.
    Wider Google site: batches + native outlet RSS (esp. conservative).
    """
    place = resolve_place(geo)
    lean_pref = normalize_pref(lean)
    meta = pref_meta(lean_pref)
    timeout = httpx.Timeout(12.0, connect=5.0)

    async def _news() -> list[SearchHit]:
        async with httpx.AsyncClient(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT},
            follow_redirects=True,
        ) as client:
            # RSS first (diverse, cached); Google site: throttled in parallel
            lean_hits, rss_hits, general = await asyncio.gather(
                _fetch_google_news_for_pref(
                    client, query, lean_pref, lite=True
                ),
                _fetch_outlet_rss_for_pref(
                    client, lean_pref, query, lite=True
                ),
                _fetch_google_news(client, query, limit=10, score_base=900),
            )
            merged = _merge_hits([rss_hits, lean_hits, general])
            return diversify_hits(merged, limit=40, max_per_source=3)

    news_hits, trends = await asyncio.gather(
        _news(),
        build_trends(force=False, geo=place.code),
    )
    hits = enrich_hits([h.to_dict() for h in news_hits])
    hits = filter_hits(hits, lean_pref, keep_unmatched=False)
    # Keep topical when possible, but don't drop the diversified feed to near-zero
    topical = prefer_topical(hits, query)
    if len(topical) >= 6:
        hits = topical
    # diversify last so score-sort doesn't re-clump Fox/NYPost at the top
    hits = diversify_hits(hits, limit=24, max_per_source=3)
    ranks = rank_lookup(query, trends)
    coverage = aggregate_lean(hits)
    sources_ok = [
        f"Preferred sources ({lean_pref})",
        "Outlet RSS",
        "Google News",
    ]

    return {
        "q": query,
        "geo": place.code,
        "place": place.to_dict(),
        "fetched_at": _now_iso(),
        "mode": "live" if hits else "empty",
        "lite": True,
        "count": len(hits),
        "hits": hits,
        "tech_hits": [],
        "portals": [],
        "sources_ok": sources_ok,
        "rank_lookup": ranks,
        "coverage_lean": coverage,
        "phrases": [query],
        "match_mode": "phrase",
        **meta,
        "trends": {
            "day": trends.get("day"),
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "consensus": [],
            "labels": trends.get("labels") or {},
        },
        "disclaimer": (
            f"MyNews lite: {meta['lean_pref_label'].lower()} sources "
            f"(Google site: + outlet RSS) + rank map. "
            "Open the topic page for full search."
        ),
    }


async def _run_search_one(
    query: str,
    force_trends: bool = False,
    geo: str | None = None,
    lean: str | None = None,
    lite: bool = False,
) -> dict[str, Any]:
    """Single phrase search (words in the phrase match as a sentence / AND-ish)."""
    if lite:
        return await _run_search_lite(query, geo=geo, lean=lean)

    place = resolve_place(geo)
    lean_pref = normalize_pref(lean)
    portals = [p.to_dict() for p in portal_links(query)]
    timeout = httpx.Timeout(16.0, connect=6.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        # All index pulls in parallel (was sequential — major latency win)
        gnews, gnews_lean, rss_hits, bnews, hn, reddit = await asyncio.gather(
            _fetch_google_news(client, query),
            _fetch_google_news_for_pref(client, query, lean_pref, lite=False),
            _fetch_outlet_rss_for_pref(client, lean_pref, query, lite=False),
            _fetch_bing_news(client, query),
            _fetch_hn(client, query),
            _fetch_reddit(client, query),
        )

    # Preferred RSS + Google site: first, then general indexes
    main_hits = _merge_hits([rss_hits, gnews_lean, gnews, bnews, reddit])
    main_hits = diversify_hits(main_hits, limit=MAX_RESULTS * 2, max_per_source=4)
    tech_hits = _merge_hits([hn])[:8]
    main_titles = {
        re.sub(r"\W+", " ", (h.title or "").lower()).strip()[:80] for h in main_hits
    }
    tech_hits = [
        h
        for h in tech_hits
        if re.sub(r"\W+", " ", (h.title or "").lower()).strip()[:80] not in main_titles
    ]

    sources_ok = []
    if gnews_lean or gnews:
        sources_ok.append("Google News")
    if gnews_lean:
        sources_ok.append(f"Preferred sources ({lean_pref})")
    if rss_hits:
        sources_ok.append(f"Outlet RSS ({len(rss_hits)} items)")
    if bnews:
        sources_ok.append("Bing News")
    if reddit:
        sources_ok.append("Reddit")
    if tech_hits:
        sources_ok.append("Hacker News (tech, secondary)")

    hits = enrich_hits([h.to_dict() for h in main_hits])
    hits = filter_hits(hits, lean_pref, keep_unmatched=False)
    topical = prefer_topical(hits, query)
    if len(topical) >= 8:
        hits = topical
    # diversify last so mega-outlets don't monopolize the list
    hits = diversify_hits(hits, limit=MAX_RESULTS, max_per_source=3)
    tech_hit_dicts = enrich_hits([h.to_dict() for h in tech_hits])
    mode = "live" if (hits or tech_hit_dicts) else ("portals_only" if portals else "empty")
    trends = await build_trends(force=False, geo=place.code)
    ranks = rank_lookup(query, trends)
    coverage = aggregate_lean(hits)
    meta = pref_meta(lean_pref)

    return {
        "q": query,
        "geo": place.code,
        "place": place.to_dict(),
        "fetched_at": _now_iso(),
        "mode": mode,
        "lite": False,
        "count": len(hits),
        "hits": hits,
        "tech_hits": tech_hit_dicts,
        "portals": portals,
        "sources_ok": sources_ok,
        "rank_lookup": ranks,
        "coverage_lean": coverage,
        "phrases": [query],
        "match_mode": "phrase",
        **meta,
        "trends": {
            "day": trends.get("day"),
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "consensus": trends.get("consensus") or [],
            "labels": trends.get("labels") or {},
        },
        "disclaimer": (
            f"Rank map for {place.label} = mass platforms on Daily Intersection (not Hacker News). "
            f"Headlines filtered for your {meta['lean_pref_label'].lower()} source preference. "
            "Lean badges = curated outlet labels (not a truth score). "
            "Polymarket volumes are not financial advice. Verify sources."
        ),
    }


async def _run_search_or(
    phrases: list[str],
    force_trends: bool = False,
    geo: str | None = None,
    display_q: str = "",
    lean: str | None = None,
) -> dict[str, Any]:
    """Run each comma-segment independently and OR the results together."""
    import asyncio

    place = resolve_place(geo)
    lean_pref = normalize_pref(lean)
    parts = await asyncio.gather(
        *[
            _run_search_one(p, force_trends=False, geo=place.code, lean=lean_pref)
            for p in phrases
        ]
    )
    parts_list = list(parts)

    # Merge headline hits (dedupe by URL)
    hit_maps: list[list[SearchHit]] = []
    tech_maps: list[list[SearchHit]] = []
    for part in parts_list:
        for key, bucket in (("hits", hit_maps), ("tech_hits", tech_maps)):
            items = []
            for h in part.get(key) or []:
                items.append(
                    SearchHit(
                        title=h.get("title") or "",
                        url=h.get("url") or "",
                        source=h.get("source") or "",
                        snippet=h.get("snippet") or "",
                        score=int(h.get("score") or 0),
                        comments_url=h.get("comments_url"),
                    )
                )
            if key == "hits":
                hit_maps.append(items)
            else:
                tech_maps.append(items)

    main_hits = _merge_hits(hit_maps)
    tech_hits = _merge_hits(tech_maps)[:8]
    hits = enrich_hits([h.to_dict() for h in main_hits])
    hits = filter_hits(hits, lean_pref, keep_unmatched=False)
    display = display_q or ", ".join(phrases)
    hits = prefer_topical(hits, display)[:MAX_RESULTS]
    hits = sorted(hits, key=lambda h: int(h.get("score") or 0), reverse=True)
    tech_hit_dicts = enrich_hits([h.to_dict() for h in tech_hits])

    sources_ok: list[str] = []
    for part in parts_list:
        for s in part.get("sources_ok") or []:
            if s not in sources_ok:
                sources_ok.append(s)

    # Portals for the combined display query
    portals = [p.to_dict() for p in portal_links(display)]
    # Rank map: OR phrases via shared rank_lookup
    trends_for_rank = await build_trends(force=False, geo=place.code)
    ranks = rank_lookup(display, trends_for_rank)
    coverage = aggregate_lean(hits)
    mode = "live" if (hits or tech_hit_dicts) else ("portals_only" if portals else "empty")
    trends = trends_for_rank
    meta = pref_meta(lean_pref)

    return {
        "q": display,
        "geo": place.code,
        "place": place.to_dict(),
        "fetched_at": _now_iso(),
        "mode": mode,
        "count": len(hits),
        "hits": hits,
        "tech_hits": tech_hit_dicts,
        "portals": portals,
        "sources_ok": sources_ok,
        "rank_lookup": ranks,
        "coverage_lean": coverage,
        "phrases": phrases,
        "match_mode": "or_phrases",
        **meta,
        "trends": {
            "day": trends.get("day"),
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "consensus": trends.get("consensus") or [],
            "labels": trends.get("labels") or {},
        },
        "disclaimer": (
            f"Comma-separated topics are ORed (each phrase searched alone; words inside a "
            f"phrase match as a sentence). Phrases: {', '.join(phrases)}. "
            f"Location: {place.label}. "
            f"Source preference: {meta['lean_pref_label']}."
        ),
    }


async def run_search(
    q: str,
    force_trends: bool = False,
    geo: str | None = None,
    lean: str | None = None,
    lite: bool = False,
) -> dict[str, Any]:
    query = _clean_query(q)
    place = resolve_place(geo)
    lean_pref = normalize_pref(lean)
    meta = pref_meta(lean_pref)

    # Empty query → daily platform trends dashboard + preferred-source headlines
    if not query:
        trends = await build_trends(force=force_trends, geo=place.code)
        timeout = httpx.Timeout(14.0, connect=6.0)
        preferred_hits: list[dict[str, Any]] = []
        try:
            async with httpx.AsyncClient(
                timeout=timeout,
                headers={"User-Agent": USER_AGENT},
                follow_redirects=True,
            ) as client:
                lean_raw, rss_raw = await asyncio.gather(
                    _fetch_google_news_for_pref(
                        client, "", lean_pref, lite=False
                    ),
                    _fetch_outlet_rss_for_pref(
                        client, lean_pref, "", lite=False
                    ),
                )
            merged = diversify_hits(
                _merge_hits([rss_raw, lean_raw]),
                limit=MAX_RESULTS * 2,
                max_per_source=3,
            )
            preferred_hits = enrich_hits([h.to_dict() for h in merged])
            preferred_hits = filter_hits(
                preferred_hits, lean_pref, keep_unmatched=False
            )
            preferred_hits = diversify_hits(
                preferred_hits, limit=MAX_RESULTS, max_per_source=2
            )
        except Exception as e:
            log.warning("Preferred headlines pull failed: %s", e)

        sources_ok = list(trends.get("sources_ok") or [])
        if preferred_hits:
            sources_ok = [
                f"Preferred sources ({lean_pref})",
                "Outlet RSS",
            ] + [
                s
                for s in sources_ok
                if s
                not in (
                    f"Preferred sources ({lean_pref})",
                    "Outlet RSS",
                )
            ]

        return {
            "q": "",
            "mode": "trends",
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "fetched_at": trends.get("fetched_at"),
            "count": sum((trends.get("counts") or {}).values()),
            "hits": preferred_hits,
            "portals": [],
            "sources_ok": sources_ok,
            "trends": trends,
            "rank_lookup": None,
            **meta,
            "disclaimer": (
                (trends.get("disclaimer") or "Daily multi-platform trends snapshot.")
                + f" Headlines below use your {meta['lean_pref_label'].lower()} source list."
            ),
        }

    phrases = or_phrases(query)
    # Lite is single-phrase only (MyNews chips are one phrase each)
    if lite:
        phrase = phrases[0] if phrases else query
        return await _run_search_one(
            phrase, force_trends=False, geo=place.code, lean=lean_pref, lite=True
        )
    if len(phrases) > 1:
        return await _run_search_or(
            phrases,
            force_trends=force_trends,
            geo=place.code,
            display_q=query,
            lean=lean_pref,
        )
    return await _run_search_one(
        phrases[0] if phrases else query,
        force_trends=force_trends,
        geo=place.code,
        lean=lean_pref,
        lite=False,
    )
