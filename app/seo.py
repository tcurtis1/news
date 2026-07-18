"""robots.txt + sitemap.xml for news.yoyosup.com."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from app.comments import COMMENTS_DIR
from app.topics import slugify
from app.trends import build_trends

PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com").rstrip("/")


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def list_comment_topic_slugs() -> list[str]:
    """Slugs that have a comments file (user-visited topics)."""
    try:
        COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
        out = []
        for p in sorted(COMMENTS_DIR.glob("*.json")):
            if p.name.startswith("_"):
                continue
            slug = p.stem
            if slug and slug not in out:
                out.append(slug)
        return out
    except Exception:
        return []


async def collect_sitemap_urls() -> list[dict[str, Any]]:
    """
    Core pages + today's consensus topics + topics with comments.
    """
    day = _today()
    urls: list[dict[str, Any]] = [
        {"loc": f"{PUBLIC_BASE}/", "changefreq": "hourly", "priority": "1.0", "lastmod": day},
        {"loc": f"{PUBLIC_BASE}/search", "changefreq": "hourly", "priority": "0.95", "lastmod": day},
        {"loc": f"{PUBLIC_BASE}/my", "changefreq": "weekly", "priority": "0.85", "lastmod": day},
        {"loc": f"{PUBLIC_BASE}/safety", "changefreq": "monthly", "priority": "0.4", "lastmod": day},
    ]

    seen = {u["loc"] for u in urls}
    try:
        trends = await build_trends(force=False)
        if trends.get("day"):
            day = str(trends["day"])
            for u in urls:
                if u["priority"] in ("1.0", "0.95"):
                    u["lastmod"] = day
        for c in trends.get("consensus") or []:
            title = c.get("title") or ""
            slug = slugify(title)
            if not slug or slug == "topic":
                continue
            loc = f"{PUBLIC_BASE}/topic/{slug}"
            if loc in seen:
                continue
            seen.add(loc)
            urls.append(
                {
                    "loc": loc,
                    "changefreq": "daily",
                    "priority": "0.8",
                    "lastmod": day,
                }
            )
        # Also top items from major platforms (extra topic coverage)
        for plat in ("google", "bing", "x", "polymarket"):
            for it in (trends.get("top10") or {}).get(plat) or []:
                title = it.get("title") or ""
                slug = slugify(title)
                if not slug or slug == "topic":
                    continue
                loc = f"{PUBLIC_BASE}/topic/{slug}"
                if loc in seen:
                    continue
                seen.add(loc)
                urls.append(
                    {
                        "loc": loc,
                        "changefreq": "daily",
                        "priority": "0.7",
                        "lastmod": day,
                    }
                )
    except Exception:
        pass

    for slug in list_comment_topic_slugs():
        loc = f"{PUBLIC_BASE}/topic/{slug}"
        if loc in seen:
            continue
        seen.add(loc)
        urls.append(
            {
                "loc": loc,
                "changefreq": "daily",
                "priority": "0.75",
                "lastmod": day,
            }
        )

    return urls


def render_robots_txt() -> str:
    return (
        "User-agent: *\n"
        "Allow: /\n"
        "\n"
        "# App / admin (no need to index)\n"
        "Disallow: /admin/\n"
        "Disallow: /api/\n"
        "\n"
        f"Sitemap: {PUBLIC_BASE}/sitemap.xml\n"
    )


def render_sitemap_xml(urls: list[dict[str, Any]]) -> str:
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
    ]
    for u in urls:
        loc = escape(u["loc"])
        lastmod = escape(str(u.get("lastmod") or _today()))
        changefreq = escape(str(u.get("changefreq") or "daily"))
        priority = escape(str(u.get("priority") or "0.5"))
        lines.append("  <url>")
        lines.append(f"    <loc>{loc}</loc>")
        lines.append(f"    <lastmod>{lastmod}</lastmod>")
        lines.append(f"    <changefreq>{changefreq}</changefreq>")
        lines.append(f"    <priority>{priority}</priority>")
        lines.append("  </url>")
    lines.append("</urlset>")
    lines.append("")
    return "\n".join(lines)
