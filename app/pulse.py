"""Daily Pulse — top trending topics from free public sources."""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import httpx

log = logging.getLogger("pulse")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
CACHE_FILE = CACHE_DIR / "pulse_cache.json"
CACHE_TTL_SEC = int(os.environ.get("PULSE_CACHE_TTL", str(30 * 60)))  # 30 min
MAX_ITEMS = 20
USER_AGENT = "YoyoNewsPulse/0.2 (+https://news.yoyosup.com; meta-aggregator)"
# HN is high-signal but niche — cap volume and down-weight raw points vs broader feeds
HN_MAX_ITEMS = 12
HN_SCORE_SCALE = 0.18
GOOGLE_NEWS_BASE = 480


@dataclass
class PulseItem:
    rank: int
    title: str
    url: str
    source: str
    score: int = 0
    comments_url: str | None = None
    summary: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_title(title: str) -> str:
    t = title.lower().strip()
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t


def _title_key(title: str) -> str:
    return hashlib.sha1(_normalize_title(title).encode()).hexdigest()[:12]


async def _fetch_hacker_news(
    client: httpx.AsyncClient, limit: int = HN_MAX_ITEMS
) -> list[dict]:
    """Hacker News top stories — tech/curious signal, not mass media."""
    try:
        ids_r = await client.get("https://hacker-news.firebaseio.com/v0/topstories.json")
        ids_r.raise_for_status()
        ids = ids_r.json()[:limit]
        out: list[dict] = []
        for sid in ids:
            r = await client.get(f"https://hacker-news.firebaseio.com/v0/item/{sid}.json")
            if r.status_code != 200:
                continue
            item = r.json() or {}
            title = (item.get("title") or "").strip()
            if not title:
                continue
            url = item.get("url") or f"https://news.ycombinator.com/item?id={sid}"
            raw = int(item.get("score") or 0)
            # Down-weight so a 800-point HN thread doesn't dominate Google/Reddit
            score = max(1, int(raw * HN_SCORE_SCALE))
            out.append(
                {
                    "title": title,
                    "url": url,
                    "source": "Hacker News",
                    "score": score,
                    "comments_url": f"https://news.ycombinator.com/item?id={sid}",
                    "lane": "tech",
                }
            )
        return out
    except Exception as e:
        log.warning("HN fetch failed: %s", e)
        return []


async def _fetch_google_news_top(client: httpx.AsyncClient, limit: int = 20) -> list[dict]:
    """US Google News top RSS — broad mainstream headlines."""
    import xml.etree.ElementTree as ET

    try:
        r = await client.get(
            "https://news.google.com/rss",
            params={"hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={
                "User-Agent": USER_AGENT,
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        out: list[dict] = []
        for i, entry in enumerate(root.findall(".//item")[:limit]):
            title = (entry.findtext("title") or "").strip()
            # Google News often "Headline - Publisher"
            title = re.sub(r"\s+-\s+[^-]+$", "", title).strip() or title
            link = (entry.findtext("link") or "").strip()
            if not title:
                continue
            src = (entry.findtext("source") or "Google News").strip()
            out.append(
                {
                    "title": title,
                    "url": link or f"https://news.google.com/search?q={quote_plus(title)}",
                    "source": f"Google News" + (f" · {src}" if src and src != "Google News" else ""),
                    "score": GOOGLE_NEWS_BASE - i * 12,
                    "comments_url": None,
                    "lane": "mainstream",
                }
            )
        return out
    except Exception as e:
        log.warning("Google News top failed: %s", e)
        return []


def _parse_reddit_atom(xml_text: str, sub: str, limit: int) -> list[dict]:
    """Parse Reddit Atom RSS (JSON endpoints are often blocked without OAuth)."""
    import xml.etree.ElementTree as ET

    ns = {"a": "http://www.w3.org/2005/Atom"}
    root = ET.fromstring(xml_text)
    out: list[dict] = []
    for i, entry in enumerate(root.findall("a:entry", ns)):
        if i >= limit:
            break
        title = (entry.findtext("a:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        link_el = entry.find("a:link", ns)
        comments = (link_el.get("href") if link_el is not None else None) or None
        # Prefer external content link when Reddit embeds one in content HTML
        url = comments or f"https://www.reddit.com/r/{sub}/"
        content = entry.findtext("a:content", default="", namespaces=ns) or ""
        # Atom content often has: <a href="EXTERNAL">[link]</a>
        m = re.search(r'href="(https?://[^"]+)"', content)
        if m and "reddit.com" not in m.group(1):
            url = m.group(1)
        out.append(
            {
                "title": title,
                "url": url,
                "source": f"Reddit r/{sub}",
                # RSS has no score; rank by feed order (hot)
                "score": max(1, limit - i) * 10,
                "comments_url": comments,
            }
        )
    return out


async def _fetch_reddit(client: httpx.AsyncClient, sub: str = "news", limit: int = 20) -> list[dict]:
    """Reddit public hot feed via Atom RSS (no key)."""
    try:
        r = await client.get(
            f"https://www.reddit.com/r/{sub}/.rss",
            headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/xml, text/xml"},
        )
        if r.status_code != 200:
            log.warning("Reddit r/%s RSS status %s", sub, r.status_code)
            return []
        return _parse_reddit_atom(r.text, sub, limit)
    except Exception as e:
        log.warning("Reddit r/%s failed: %s", sub, e)
        return []


def _fallback_items() -> list[dict]:
    """Seed list when all external fetches fail (still show a working UI)."""
    topics = [
        "AI regulation and safety debates",
        "Major markets and tech earnings",
        "Cybersecurity breaches in the news",
        "Climate and extreme weather updates",
        "Elections and policy headlines",
        "Space and science breakthroughs",
        "Health and public-health stories",
        "Sports championships and transfers",
        "Streaming and entertainment releases",
        "Open-source and developer tools",
    ]
    out = []
    for i, t in enumerate(topics, 1):
        q = quote_plus(t)
        out.append(
            {
                "title": t,
                "url": f"https://news.google.com/search?q={q}",
                "source": "Seed (offline)",
                "score": 100 - i,
                "comments_url": None,
            }
        )
    return out


def _source_breadth(sources: set[str]) -> int:
    """How 'broad' is this story? Mainstream > social > tech-only."""
    text = " ".join(sources).lower()
    score = 0
    if "google news" in text:
        score += 3
    if "reddit r/news" in text or "reddit r/worldnews" in text:
        score += 2
    if "reddit r/technology" in text:
        score += 1
    if "hacker news" in text:
        score += 1  # counts for consensus, not as mass media
    # Pure HN-only stories get breadth 1 only
    if sources == {"Hacker News"}:
        return 0
    return score


def _merge_consensus(raw_lists: list[list[dict]]) -> list[PulseItem]:
    """
    Merge sources: multi-source + broader feeds rank above niche-only HN heat.
    """
    buckets: dict[str, dict] = {}
    for lst in raw_lists:
        for it in lst:
            key = _title_key(it["title"])
            if key not in buckets:
                buckets[key] = {
                    "title": it["title"],
                    "url": it["url"],
                    "sources": {it["source"]},
                    "score": int(it.get("score") or 0),
                    "comments_url": it.get("comments_url"),
                }
            else:
                b = buckets[key]
                b["sources"].add(it["source"])
                b["score"] += int(it.get("score") or 0)
                # Prefer article URL over seed / HN item pages when we get a better one
                if b["url"].startswith("https://news.google.com") and it["url"]:
                    b["url"] = it["url"]
                if "ycombinator.com/item" in b["url"] and it["url"] and "ycombinator.com" not in it["url"]:
                    b["url"] = it["url"]
                if not b.get("comments_url") and it.get("comments_url"):
                    b["comments_url"] = it["comments_url"]

    ranked = sorted(
        buckets.values(),
        key=lambda b: (
            len(b["sources"]),
            _source_breadth(b["sources"]),
            b["score"],
        ),
        reverse=True,
    )[:MAX_ITEMS]

    items: list[PulseItem] = []
    for i, b in enumerate(ranked, 1):
        sources = sorted(b["sources"])
        consensus = len(sources) > 1
        breadth = _source_breadth(b["sources"])
        if consensus:
            summary = f"Seen across {len(sources)} sources: {', '.join(sources)}"
        elif sources == ["Hacker News"]:
            summary = "From Hacker News (tech / curious — not mass-media ranking)"
        else:
            summary = f"From {sources[0]}"
        items.append(
            PulseItem(
                rank=i,
                title=b["title"],
                url=b["url"],
                source=" · ".join(sources),
                score=b["score"],
                comments_url=b.get("comments_url"),
                summary=summary,
            )
        )
    return items


def _read_cache() -> dict | None:
    try:
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if time.time() - float(data.get("fetched_at_unix", 0)) > CACHE_TTL_SEC:
            return None
        # normalize legacy key
        if "stories" not in data and "items" in data:
            data["stories"] = data.pop("items")
        return data
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("cache write failed: %s", e)


async def build_pulse(force: bool = False) -> dict:
    if not force:
        cached = _read_cache()
        if cached:
            return cached

    timeout = httpx.Timeout(12.0, connect=6.0)
    async with httpx.AsyncClient(timeout=timeout, headers={"User-Agent": USER_AGENT}) as client:
        gnews = await _fetch_google_news_top(client)
        # Reddit first (broader), HN last and down-weighted
        reddit_lists: list[list[dict]] = []
        for sub in ("worldnews", "news", "technology"):
            reddit_lists.append(await _fetch_reddit(client, sub))
            await asyncio.sleep(0.75)
        hn = await _fetch_hacker_news(client)

    lists = [gnews, *reddit_lists, hn]
    if not any(lists):
        lists = [_fallback_items()]
        mode = "fallback"
    else:
        total = sum(len(x) for x in lists)
        if total < 5:
            lists.append(_fallback_items())
            mode = "mixed"
        else:
            mode = "live"

    items = _merge_consensus(lists)
    payload = {
        "fetched_at": _now_iso(),
        "fetched_at_unix": time.time(),
        "mode": mode,
        "lane": "curious",
        "count": len(items),
        # named "stories" (not "items") so Jinja dicts don't clash with dict.items
        "stories": [it.to_dict() for it in items],
        "disclaimer": (
            "Pulse is a curious-consensus feed: Google News (broad) + Reddit "
            "world/news/tech + a lighter Hacker News signal (tech niche, "
            "down-weighted so it doesn’t dominate). For mass attention across "
            "Google/Bing/X/YouTube/etc., use Daily Intersection. Not exhaustive."
        ),
    }
    _write_cache(payload)
    return payload
