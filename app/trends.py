"""Daily multi-platform trends: Google, Bing, YouTube, X.

Pulled at most once per day (UTC calendar day) and cached under CACHE_DIR.
Force refresh with force=True or ?force=1 on the API.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger("trends")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
CACHE_FILE = CACHE_DIR / "trends_cache.json"
# Once-a-day default: still valid if same UTC date, else max age fallback
CACHE_MAX_AGE_SEC = int(os.environ.get("TRENDS_CACHE_TTL", str(26 * 3600)))
GEO = os.environ.get("TRENDS_GEO", "US")
USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsTrends/0.2; +https://news.yoyosup.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_PER_PLATFORM = 20


@dataclass
class TrendItem:
    rank: int
    title: str
    url: str
    platform: str  # google | bing | youtube | x
    snippet: str = ""
    traffic: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _now_iso() -> str:
    return _now().isoformat()


def _utc_day() -> str:
    return _now().strftime("%Y-%m-%d")


def _browser_headers() -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": "en-US,en;q=0.9",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _read_cache() -> dict | None:
    try:
        if not CACHE_FILE.exists():
            return None
        data = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        if data.get("day") == _utc_day():
            return data
        # fallback: still use if within max age (server timezone drift / offline)
        age = time.time() - float(data.get("fetched_at_unix", 0))
        if age <= CACHE_MAX_AGE_SEC:
            return data
        return None
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("trends cache write failed: %s", e)


def _parse_traffic(raw: str) -> int:
    """Parse '5000+' / '200K+' style traffic into a rough int for ranking."""
    if not raw:
        return 0
    s = raw.strip().upper().replace(",", "").rstrip("+")
    mult = 1
    if s.endswith("K"):
        mult = 1_000
        s = s[:-1]
    elif s.endswith("M"):
        mult = 1_000_000
        s = s[:-1]
    try:
        return int(float(s) * mult)
    except ValueError:
        return 0


async def _fetch_google(client: httpx.AsyncClient) -> list[TrendItem]:
    """Google daily search trends via official Trends RSS."""
    try:
        r = await client.get(
            f"https://trends.google.com/trending/rss?geo={GEO}",
            headers={**_browser_headers(), "Accept": "application/rss+xml, application/xml"},
        )
        r.raise_for_status()
        # ht namespace for approx_traffic + news items
        ns = {"ht": "https://trends.google.com/trending/rss"}
        root = ET.fromstring(r.content)
        items: list[TrendItem] = []
        for i, entry in enumerate(root.findall("channel/item")[:MAX_PER_PLATFORM], 1):
            title = (entry.findtext("title") or "").strip()
            if not title:
                continue
            traffic = (
                entry.findtext("ht:approx_traffic", default="", namespaces=ns) or ""
            ).strip()
            # Prefer first related news URL when present
            news = entry.find("ht:news_item", ns)
            url = ""
            snippet = ""
            if news is not None:
                url = (news.findtext("ht:news_item_url", default="", namespaces=ns) or "").strip()
                snippet = (
                    news.findtext("ht:news_item_title", default="", namespaces=ns) or ""
                ).strip()
                src = (
                    news.findtext("ht:news_item_source", default="", namespaces=ns) or ""
                ).strip()
                if src and snippet:
                    snippet = f"{snippet} — {src}"
            if not url:
                url = f"https://www.google.com/search?q={quote_plus(title)}"
            if traffic and not snippet:
                snippet = f"~{traffic} searches"
            elif traffic:
                snippet = f"{snippet} · ~{traffic} searches" if snippet else f"~{traffic} searches"
            items.append(
                TrendItem(
                    rank=i,
                    title=title,
                    url=url,
                    platform="google",
                    snippet=snippet,
                    traffic=traffic,
                )
            )
        return items
    except Exception as e:
        log.warning("Google trends failed: %s", e)
        return []


async def _fetch_bing(client: httpx.AsyncClient) -> list[TrendItem]:
    """Bing Popular Now (homepage suggestions) + top Bing News RSS."""
    items: list[TrendItem] = []
    seen: set[str] = set()

    # 1) Popular Now searches
    try:
        r = await client.get(
            "https://www.bing.com/AS/Suggestions",
            params={
                "pt": "page.home",
                "qry": "",
                "cp": "0",
                "csr": "1",
                "msbqf": "false",
                "cvid": "yoyonews",
            },
            headers=_browser_headers(),
        )
        if r.status_code == 200:
            for row in (r.json().get("s") or []):
                q = (row.get("q") or "").strip()
                if not q or q.lower() in seen:
                    continue
                seen.add(q.lower())
                des = ""
                ext = row.get("ext") or {}
                if isinstance(ext, dict):
                    des = (ext.get("des") or "").strip()
                items.append(
                    TrendItem(
                        rank=len(items) + 1,
                        title=q,
                        url=f"https://www.bing.com/search?q={quote_plus(q)}",
                        platform="bing",
                        snippet=des or "Bing Popular Now",
                        traffic="popular",
                    )
                )
                if len(items) >= MAX_PER_PLATFORM // 2:
                    break
    except Exception as e:
        log.warning("Bing popular failed: %s", e)

    # 2) Bing News RSS for the region
    try:
        r = await client.get(
            "https://www.bing.com/news/search",
            params={"q": "United States", "format": "RSS", "market": "en-US"},
            headers={**_browser_headers(), "Accept": "application/rss+xml, application/xml, text/xml, */*"},
        )
        if r.status_code == 200 and b"<item>" in r.content:
            text = r.content
            if not text.lstrip().startswith(b"<?xml") and not text.lstrip().startswith(b"<rss"):
                idx = text.find(b"<rss")
                text = text[idx:] if idx >= 0 else text
            root = ET.fromstring(text)
            for entry in root.findall(".//item"):
                title = (entry.findtext("title") or "").strip()
                link = (entry.findtext("link") or "").strip()
                if not title or title.lower() in seen:
                    continue
                seen.add(title.lower())
                desc = re.sub(r"<[^>]+>", "", entry.findtext("description") or "").strip()
                items.append(
                    TrendItem(
                        rank=len(items) + 1,
                        title=title,
                        url=link or f"https://www.bing.com/news/search?q={quote_plus(title)}",
                        platform="bing",
                        snippet=(desc[:160] + "…") if len(desc) > 160 else desc or "Bing News",
                        traffic="news",
                    )
                )
                if len(items) >= MAX_PER_PLATFORM:
                    break
    except Exception as e:
        log.warning("Bing news RSS failed: %s", e)

    for i, it in enumerate(items, 1):
        it.rank = i
    return items[:MAX_PER_PLATFORM]


async def _fetch_youtube(client: httpx.AsyncClient) -> list[TrendItem]:
    """YouTube daily Top Videos chart (US) via charts.youtube.com innertube."""
    try:
        body = {
            "context": {
                "client": {
                    "clientName": "WEB_MUSIC_ANALYTICS",
                    "clientVersion": "2.0",
                    "hl": "en",
                    "gl": GEO,
                    "userAgent": USER_AGENT,
                    "platform": "DESKTOP",
                }
            },
            "browseId": "FEmusic_analytics_charts_home",
            "query": (
                "perspective=CHART_DETAILS"
                f"&chart_params_country_code={GEO.lower()}"
                "&chart_params_chart_type=VIDEOS"
                "&chart_params_period_type=DAILY"
            ),
        }
        r = await client.post(
            "https://charts.youtube.com/youtubei/v1/browse",
            params={"alt": "json"},
            headers={
                **_browser_headers(),
                "Content-Type": "application/json",
                "Origin": "https://charts.youtube.com",
                "Referer": f"https://charts.youtube.com/charts/TopVideos/{GEO.lower()}/daily",
            },
            json=body,
        )
        r.raise_for_status()
        data = r.json()
        raw: list[dict] = []

        def walk(o: Any) -> None:
            if isinstance(o, dict):
                if "id" in o and "title" in o and "viewCount" in o:
                    raw.append(o)
                for v in o.values():
                    walk(v)
            elif isinstance(o, list):
                for v in o:
                    walk(v)

        walk(data)
        seen: set[str] = set()
        items: list[TrendItem] = []
        for o in raw:
            vid = str(o.get("id") or "")
            title = str(o.get("title") or "").strip()
            if not vid or not title or vid in seen:
                continue
            seen.add(vid)
            artists = o.get("artists") or []
            names = []
            for a in artists:
                if isinstance(a, dict) and a.get("name"):
                    names.append(str(a["name"]))
            views = str(o.get("viewCount") or "")
            try:
                views_fmt = f"{int(views):,} views today"
            except ValueError:
                views_fmt = f"{views} views" if views else ""
            snippet = " · ".join(x for x in [", ".join(names), views_fmt] if x)
            items.append(
                TrendItem(
                    rank=len(items) + 1,
                    title=title,
                    url=f"https://www.youtube.com/watch?v={vid}",
                    platform="youtube",
                    snippet=snippet,
                    traffic=views,
                )
            )
            if len(items) >= MAX_PER_PLATFORM:
                break
        return items
    except Exception as e:
        log.warning("YouTube charts failed: %s", e)
        return []


async def _fetch_x(client: httpx.AsyncClient) -> list[TrendItem]:
    """X/Twitter US trends via trends24.in (public HTML mirror of X trends)."""
    try:
        r = await client.get(
            "https://trends24.in/united-states/",
            headers=_browser_headers(),
        )
        r.raise_for_status()
        html = r.text
        # Prefer the most recent timeline card only
        block = html
        m = re.search(r"<ol class=trend-card__list>(.*?)</ol>", html, re.S | re.I)
        if m:
            block = m.group(1)
        # trend-link anchors
        pairs = re.findall(
            r'href="https?://(?:twitter|x)\.com/search\?q=([^"]+)"[^>]*class=trend-link[^>]*>([^<]+)',
            block,
            re.I,
        )
        if not pairs:
            pairs = re.findall(
                r'class=trend-link[^>]*href="https?://(?:twitter|x)\.com/search\?q=([^"]+)"[^>]*>([^<]+)',
                block,
                re.I,
            )
        if not pairs:
            # href-only fallback from first card
            qs = re.findall(r'https?://(?:twitter|x)\.com/search\?q=([^"\']+)', block, re.I)
            pairs = [(q, unquote(q).replace("+", " ")) for q in qs]

        items: list[TrendItem] = []
        seen: set[str] = set()
        for q_enc, label in pairs:
            title = unquote(label or q_enc).replace("+", " ").strip()
            if not title or title.lower() in seen:
                continue
            seen.add(title.lower())
            items.append(
                TrendItem(
                    rank=len(items) + 1,
                    title=title,
                    url=f"https://x.com/search?q={quote_plus(title)}&src=trend",
                    platform="x",
                    snippet="Trending on X (US)",
                    traffic="",
                )
            )
            if len(items) >= MAX_PER_PLATFORM:
                break
        return items
    except Exception as e:
        log.warning("X trends failed: %s", e)
        return []


async def build_trends(force: bool = False) -> dict[str, Any]:
    if not force:
        cached = _read_cache()
        if cached:
            cached["cache"] = "hit"
            return cached

    timeout = httpx.Timeout(25.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        google = await _fetch_google(client)
        bing = await _fetch_bing(client)
        youtube = await _fetch_youtube(client)
        x_items = await _fetch_x(client)

    platforms = {
        "google": [it.to_dict() for it in google],
        "bing": [it.to_dict() for it in bing],
        "youtube": [it.to_dict() for it in youtube],
        "x": [it.to_dict() for it in x_items],
    }
    sources_ok = [name for name, lst in platforms.items() if lst]
    payload = {
        "day": _utc_day(),
        "fetched_at": _now_iso(),
        "fetched_at_unix": time.time(),
        "geo": GEO,
        "cache": "miss",
        "refresh": "daily",
        "sources_ok": sources_ok,
        "counts": {k: len(v) for k, v in platforms.items()},
        "platforms": platforms,
        "disclaimer": (
            "Daily snapshot of public trends (UTC day). "
            "Google = Trends RSS; Bing = Popular Now + News RSS; "
            "YouTube = US daily Top Videos chart; X = US trends via trends24. "
            "Not affiliated with those platforms. Volumes and ranks are approximate."
        ),
    }
    _write_cache(payload)
    return payload
