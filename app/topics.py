"""Topic pages — one URL for rank map + news + comments."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import unquote

from app.comments import list_comments
from app.search import run_search
from app.trends import PLATFORM_LABELS, build_trends, query_matches_title, rank_lookup


def slugify(text: str) -> str:
    """Stable URL slug from a topic title or query."""
    t = unquote(text or "").lower().strip()
    t = t.replace("#", "").replace("@", "")
    t = re.sub(r"[^\w\s-]", "", t, flags=re.UNICODE)
    t = re.sub(r"[-\s]+", "-", t).strip("-_")
    return (t[:80] or "topic").strip("-")


def unslug(slug: str) -> str:
    """Best-effort human query from a slug."""
    s = unquote(slug or "").strip().strip("/")
    s = re.sub(r"[-_]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()[:200]


def _consensus_match(q: str, trends: dict[str, Any]) -> dict[str, Any] | None:
    for c in trends.get("consensus") or []:
        if query_matches_title(q, c.get("title") or ""):
            return c
        for r in (c.get("ranks") or {}).values():
            if query_matches_title(q, r.get("title") or ""):
                return c
    return None


async def build_topic(
    slug: str, force: bool = False, geo: str | None = None
) -> dict[str, Any]:
    """
    Assemble topic page payload from slug.
    Uses slug text as the search/rank query (hyphens → spaces).
    """
    raw_slug = (slug or "").strip().strip("/")
    query = unslug(raw_slug)
    canonical = slugify(query)

    search = await run_search(query, force_trends=force, geo=geo)
    trends = await build_trends(force=False, geo=geo)
    ranks = search.get("rank_lookup") or rank_lookup(query, trends)
    consensus = _consensus_match(query, trends)
    comments = list_comments(canonical)

    # Prefer display title from consensus or best rank hit
    display = query
    if consensus and consensus.get("title"):
        display = consensus["title"]
    else:
        for plat in ("google", "bing", "x", "polymarket", "youtube", "tiktok"):
            row = (ranks.get("platforms") or {}).get(plat) or {}
            if row.get("in_top") and row.get("title"):
                display = row["title"]
                break

    return {
        "slug": canonical,
        "q": query,
        "title": display,
        "geo": trends.get("geo"),
        "place": trends.get("place"),
        "labels": PLATFORM_LABELS,
        "rank_lookup": ranks,
        "consensus": consensus,
        "hits": search.get("hits") or [],
        "tech_hits": search.get("tech_hits") or [],
        "portals": search.get("portals") or [],
        "sources_ok": search.get("sources_ok") or [],
        "day": trends.get("day"),
        "delta_vs": trends.get("delta_vs"),
        "comments": comments,
        "comment_count": len(comments),
        "disclaimer": (
            "Topic pages combine today’s rank map, free news indexes, and public comments. "
            "Not affiliated with listed platforms. Be civil — comments are public."
        ),
    }
