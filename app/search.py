"""Meta search — multi-source news + daily platform trends."""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote_plus
from xml.etree import ElementTree as ET

import httpx

from app.bias import aggregate_lean, enrich_hits
from app.places import resolve_place
from app.trends import build_trends, or_phrases, rank_lookup

log = logging.getLogger("search")

USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsSearch/0.2; +https://news.yoyosup.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_PER_SOURCE = 12
MAX_RESULTS = 40


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


async def _fetch_google_news(client: httpx.AsyncClient, q: str) -> list[SearchHit]:
    try:
        r = await client.get(
            "https://news.google.com/rss/search",
            params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out: list[SearchHit] = []
        for i, entry in enumerate(root.findall(".//item")[:MAX_PER_SOURCE]):
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
                    score=1000 - i,
                )
            )
        return out
    except Exception as e:
        log.warning("Google News search failed: %s", e)
        return []


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


async def _run_search_one(
    query: str, force_trends: bool = False, geo: str | None = None
) -> dict[str, Any]:
    """Single phrase search (words in the phrase match as a sentence / AND-ish)."""
    place = resolve_place(geo)
    portals = [p.to_dict() for p in portal_links(query)]
    timeout = httpx.Timeout(14.0, connect=6.0)
    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": USER_AGENT}, follow_redirects=True
    ) as client:
        gnews, bnews, hn, reddit = (
            await _fetch_google_news(client, query),
            await _fetch_bing_news(client, query),
            await _fetch_hn(client, query),
            await _fetch_reddit(client, query),
        )

    main_hits = _merge_hits([gnews, bnews, reddit])
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
    if gnews:
        sources_ok.append("Google News")
    if bnews:
        sources_ok.append("Bing News")
    if reddit:
        sources_ok.append("Reddit")
    if tech_hits:
        sources_ok.append("Hacker News (tech, secondary)")

    hits = enrich_hits([h.to_dict() for h in main_hits])
    tech_hit_dicts = enrich_hits([h.to_dict() for h in tech_hits])
    mode = "live" if (hits or tech_hit_dicts) else ("portals_only" if portals else "empty")
    trends = await build_trends(force=False, geo=place.code)
    ranks = rank_lookup(query, trends)
    coverage = aggregate_lean(hits)

    return {
        "q": query,
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
        "phrases": [query],
        "match_mode": "phrase",
        "trends": {
            "day": trends.get("day"),
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "consensus": trends.get("consensus") or [],
            "labels": trends.get("labels") or {},
        },
        "disclaimer": (
            f"Rank map for {place.label} = mass platforms on Daily Intersection (not Hacker News). "
            "Primary news hits: Google News, Bing News, Reddit. "
            "Hacker News is a secondary tech niche index only. "
            "Lean badges = curated outlet labels (not a truth score). "
            "Polymarket volumes are not financial advice. Verify sources."
        ),
    }


async def _run_search_or(
    phrases: list[str], force_trends: bool = False, geo: str | None = None, display_q: str = ""
) -> dict[str, Any]:
    """Run each comma-segment independently and OR the results together."""
    import asyncio

    place = resolve_place(geo)
    parts = await asyncio.gather(
        *[_run_search_one(p, force_trends=False, geo=place.code) for p in phrases]
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
    tech_hit_dicts = enrich_hits([h.to_dict() for h in tech_hits])

    sources_ok: list[str] = []
    for part in parts_list:
        for s in part.get("sources_ok") or []:
            if s not in sources_ok:
                sources_ok.append(s)

    # Portals for the combined display query
    portals = [p.to_dict() for p in portal_links(display_q or ", ".join(phrases))]
    # Rank map: OR phrases via shared rank_lookup
    trends_for_rank = await build_trends(force=False, geo=place.code)
    ranks = rank_lookup(display_q or ", ".join(phrases), trends_for_rank)
    coverage = aggregate_lean(hits)
    mode = "live" if (hits or tech_hit_dicts) else ("portals_only" if portals else "empty")
    trends = trends_for_rank

    return {
        "q": display_q or ", ".join(phrases),
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
            f"Location: {place.label}."
        ),
    }


async def run_search(
    q: str, force_trends: bool = False, geo: str | None = None
) -> dict[str, Any]:
    query = _clean_query(q)
    place = resolve_place(geo)

    # Empty query → daily platform trends dashboard
    if not query:
        trends = await build_trends(force=force_trends, geo=place.code)
        return {
            "q": "",
            "mode": "trends",
            "geo": trends.get("geo"),
            "place": trends.get("place"),
            "fetched_at": trends.get("fetched_at"),
            "count": sum((trends.get("counts") or {}).values()),
            "hits": [],
            "portals": [],
            "sources_ok": trends.get("sources_ok") or [],
            "trends": trends,
            "rank_lookup": None,
            "disclaimer": trends.get("disclaimer")
            or "Daily multi-platform trends snapshot.",
        }

    phrases = or_phrases(query)
    if len(phrases) > 1:
        return await _run_search_or(
            phrases, force_trends=force_trends, geo=place.code, display_q=query
        )
    return await _run_search_one(
        phrases[0] if phrases else query, force_trends=force_trends, geo=place.code
    )
