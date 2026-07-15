"""Daily Pulse — top trending topics from free public sources."""

from __future__ import annotations

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
USER_AGENT = "YoyoNewsPulse/0.1 (+https://news.yoyosup.com; meta-aggregator)"


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


async def _fetch_hacker_news(client: httpx.AsyncClient, limit: int = 25) -> list[dict]:
    """Hacker News top stories (official Firebase API, no key)."""
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
            out.append(
                {
                    "title": title,
                    "url": url,
                    "source": "Hacker News",
                    "score": int(item.get("score") or 0),
                    "comments_url": f"https://news.ycombinator.com/item?id={sid}",
                }
            )
        return out
    except Exception as e:
        log.warning("HN fetch failed: %s", e)
        return []


async def _fetch_reddit(client: httpx.AsyncClient, sub: str = "news", limit: int = 20) -> list[dict]:
    """Reddit JSON feed (no key; public)."""
    try:
        r = await client.get(
            f"https://www.reddit.com/r/{sub}/hot.json",
            params={"limit": limit},
            headers={"User-Agent": USER_AGENT},
        )
        if r.status_code != 200:
            log.warning("Reddit r/%s status %s", sub, r.status_code)
            return []
        data = r.json()
        children = (data.get("data") or {}).get("children") or []
        out: list[dict] = []
        for ch in children:
            d = ch.get("data") or {}
            title = (d.get("title") or "").strip()
            if not title or d.get("stickied"):
                continue
            permalink = d.get("permalink") or ""
            url = d.get("url") or f"https://www.reddit.com{permalink}"
            if url.startswith("/"):
                url = f"https://www.reddit.com{url}"
            out.append(
                {
                    "title": title,
                    "url": url,
                    "source": f"Reddit r/{sub}",
                    "score": int(d.get("score") or 0),
                    "comments_url": f"https://www.reddit.com{permalink}" if permalink else None,
                }
            )
        return out
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


def _merge_consensus(raw_lists: list[list[dict]]) -> list[PulseItem]:
    """
    Merge sources: same-ish titles stack score and note multi-source consensus.
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
                # Prefer non-seed URL if we already have seed
                if b["url"].startswith("https://news.google.com") and it["url"]:
                    b["url"] = it["url"]
                if not b.get("comments_url") and it.get("comments_url"):
                    b["comments_url"] = it["comments_url"]

    ranked = sorted(
        buckets.values(),
        key=lambda b: (len(b["sources"]), b["score"]),
        reverse=True,
    )[:MAX_ITEMS]

    items: list[PulseItem] = []
    for i, b in enumerate(ranked, 1):
        sources = sorted(b["sources"])
        consensus = len(sources) > 1
        summary = (
            f"Seen across {len(sources)} sources: {', '.join(sources)}"
            if consensus
            else f"From {sources[0]}"
        )
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
        hn, reddit_news, reddit_world, reddit_tech = (
            await _fetch_hacker_news(client),
            await _fetch_reddit(client, "news"),
            await _fetch_reddit(client, "worldnews"),
            await _fetch_reddit(client, "technology"),
        )

    lists = [hn, reddit_news, reddit_world, reddit_tech]
    if not any(lists):
        lists = [_fallback_items()]
        mode = "fallback"
    else:
        # Pad with fallback only if very thin
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
        "count": len(items),
        # named "stories" (not "items") so Jinja dicts don't clash with dict.items
        "stories": [it.to_dict() for it in items],
        "disclaimer": (
            "Pulse ranks stories that show up across free public feeds "
            "(Hacker News, Reddit). Not exhaustive. Bias ratings are not "
            "applied on this MVP list — coming next. Always verify sources."
        ),
    }
    _write_cache(payload)
    return payload
