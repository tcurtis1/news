"""Daily multi-platform trends + consensus + rank lookup.

Sources: Google, Bing, YouTube, X, Polymarket, TikTok, Facebook, Instagram.
Pulled at most once per UTC day (CACHE_DIR). Force with force=True / ?force=1.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus, unquote
from xml.etree import ElementTree as ET

import httpx

log = logging.getLogger("trends")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
CACHE_FILE = CACHE_DIR / "trends_cache.json"
CACHE_MAX_AGE_SEC = int(os.environ.get("TRENDS_CACHE_TTL", str(26 * 3600)))
GEO = os.environ.get("TRENDS_GEO", "US")
USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsTrends/0.4; +https://news.yoyosup.com) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
MAX_PER_PLATFORM = 20
TOP_N = 10  # display / consensus window
PLATFORM_ORDER = (
    "google",
    "bing",
    "youtube",
    "x",
    "polymarket",
    "tiktok",
    "facebook",
    "instagram",
)
PLATFORM_LABELS = {
    "google": "Google",
    "bing": "Bing",
    "youtube": "YouTube",
    "x": "X",
    "polymarket": "Polymarket",
    "tiktok": "TikTok",
    "facebook": "Facebook",
    "instagram": "Instagram",
}
# Short note shown under column headers / chips
PLATFORM_NOTES = {
    "google": "Trends RSS",
    "bing": "Popular + News",
    "youtube": "Daily Top Videos",
    "x": "US trends",
    "polymarket": "24h volume",
    "tiktok": "Creative Center",
    "facebook": "News buzz proxy",
    "instagram": "News buzz proxy",
}
STOPWORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "in",
        "on",
        "to",
        "for",
        "vs",
        "versus",
        "will",
        "win",
        "wins",
        "with",
        "from",
        "this",
        "that",
        "into",
        "over",
        "after",
        "before",
        "more",
        "markets",
        "news",
        "today",
        "2024",
        "2025",
        "2026",
        "2027",
        "2028",
    }
)


@dataclass
class TrendItem:
    rank: int
    title: str
    url: str
    platform: str
    snippet: str = ""
    traffic: str = ""
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if not d.get("extra"):
            d.pop("extra", None)
        return d


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
        # Rebuild derived fields if older cache missing them
        if data.get("day") == _utc_day() or (
            time.time() - float(data.get("fetched_at_unix", 0)) <= CACHE_MAX_AGE_SEC
        ):
            return _ensure_derived(data)
        return None
    except Exception:
        return None


def _write_cache(payload: dict) -> None:
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        log.warning("trends cache write failed: %s", e)


def _fmt_money(n: float | int | str | None) -> str:
    try:
        v = float(n or 0)
    except (TypeError, ValueError):
        return ""
    if v >= 1_000_000_000:
        return f"${v / 1_000_000_000:.1f}B"
    if v >= 1_000_000:
        return f"${v / 1_000_000:.1f}M"
    if v >= 1_000:
        return f"${v / 1_000:.0f}K"
    return f"${v:.0f}"


def normalize_topic(title: str) -> str:
    t = (title or "").lower().strip()
    t = t.replace("#", " ")
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def topic_tokens(title: str) -> set[str]:
    return {
        p
        for p in re.findall(r"[a-z0-9]+", normalize_topic(title))
        if len(p) >= 3 and p not in STOPWORDS
    }


def _compact(title: str) -> str:
    return re.sub(r"[^a-z0-9]", "", normalize_topic(title))


def titles_similar(a: str, b: str) -> bool:
    """Loose match across platforms (subset / jaccard / shared multi-token)."""
    na, nb = normalize_topic(a), normalize_topic(b)
    if not na or not nb:
        return False
    if na == nb:
        return True
    # hashtag-style: #AllStarGame ↔ All Star Game
    ca, cb = _compact(a), _compact(b)
    if ca and cb and (ca == cb or (len(ca) >= 8 and (ca in cb or cb in ca))):
        return True
    # substring only when shorter side is a real phrase (avoid "golden" ⊂ "golden boot…")
    shorter, longer = (na, nb) if len(na) <= len(nb) else (nb, na)
    if len(shorter) >= 8 and shorter in longer:
        return True
    ta, tb = topic_tokens(a), topic_tokens(b)
    if not ta or not tb:
        return False
    inter = ta & tb
    if not inter:
        return False
    # full token subset only if the smaller set has 2+ tokens (or equal single-token titles)
    if ta <= tb or tb <= ta:
        smaller = ta if len(ta) <= len(tb) else tb
        if len(smaller) >= 2:
            return True
        if len(ta) == 1 and len(tb) == 1:
            return True
    j = len(inter) / len(ta | tb)
    if j >= 0.5:
        return True
    if len(inter) >= 2 and j >= 0.35:
        return True
    return False


def query_matches_title(q: str, title: str) -> bool:
    nq, nt = normalize_topic(q), normalize_topic(title)
    if not nq or not nt:
        return False
    if nq == nt or nq in nt or nt in nq:
        return True
    cq, ct = _compact(q), _compact(title)
    if cq and ct and (cq == ct or (len(cq) >= 4 and cq in ct)):
        return True
    tq, tt = topic_tokens(q), topic_tokens(title)
    if not tq:
        return nq in nt
    if tq <= tt:
        return True
    inter = tq & tt
    if not inter:
        return False
    return len(inter) / len(tq) >= 0.6


# ── fetchers ──────────────────────────────────────────────────────────────


async def _fetch_google(client: httpx.AsyncClient) -> list[TrendItem]:
    try:
        r = await client.get(
            f"https://trends.google.com/trending/rss?geo={GEO}",
            headers={**_browser_headers(), "Accept": "application/rss+xml, application/xml"},
        )
        r.raise_for_status()
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
            if traffic:
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
    items: list[TrendItem] = []
    seen: set[str] = set()
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
            for row in r.json().get("s") or []:
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

    try:
        r = await client.get(
            "https://www.bing.com/news/search",
            params={"q": "United States", "format": "RSS", "market": "en-US"},
            headers={
                **_browser_headers(),
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
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
            names = [
                str(a["name"]) for a in artists if isinstance(a, dict) and a.get("name")
            ]
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
    try:
        r = await client.get(
            "https://trends24.in/united-states/",
            headers=_browser_headers(),
        )
        r.raise_for_status()
        html = r.text
        block = html
        m = re.search(r"<ol class=trend-card__list>(.*?)</ol>", html, re.S | re.I)
        if m:
            block = m.group(1)
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
                )
            )
            if len(items) >= MAX_PER_PLATFORM:
                break
        return items
    except Exception as e:
        log.warning("X trends failed: %s", e)
        return []


async def _fetch_polymarket(client: httpx.AsyncClient) -> list[TrendItem]:
    """Top prediction markets by 24h volume (public Gamma API, no key)."""
    try:
        r = await client.get(
            "https://gamma-api.polymarket.com/events",
            params={
                "limit": str(MAX_PER_PLATFORM),
                "active": "true",
                "closed": "false",
                "order": "volume24hr",
                "ascending": "false",
            },
            headers={**_browser_headers(), "Accept": "application/json"},
        )
        r.raise_for_status()
        data = r.json()
        if not isinstance(data, list):
            return []
        items: list[TrendItem] = []
        for i, ev in enumerate(data[:MAX_PER_PLATFORM], 1):
            title = (ev.get("title") or "").strip()
            slug = (ev.get("slug") or "").strip()
            if not title:
                continue
            vol24 = ev.get("volume24hr")
            vol = ev.get("volume")
            liq = ev.get("liquidity")
            url = f"https://polymarket.com/event/{slug}" if slug else "https://polymarket.com/"
            parts = []
            if vol24 is not None:
                parts.append(f"{_fmt_money(vol24)} 24h vol")
            if vol is not None:
                parts.append(f"{_fmt_money(vol)} total")
            if liq is not None:
                parts.append(f"{_fmt_money(liq)} liq")
            items.append(
                TrendItem(
                    rank=i,
                    title=title,
                    url=url,
                    platform="polymarket",
                    snippet=" · ".join(parts) or "Polymarket",
                    traffic=str(vol24 or vol or ""),
                    extra={
                        "volume24hr": vol24,
                        "volume": vol,
                        "liquidity": liq,
                        "slug": slug,
                    },
                )
            )
        return items
    except Exception as e:
        log.warning("Polymarket fetch failed: %s", e)
        return []


async def _fetch_tiktok(client: httpx.AsyncClient) -> list[TrendItem]:
    """
    TikTok popular hashtags via Creative Center (public page → jina reader).
    Meta/TikTok block most direct JSON APIs without login; this is best-effort.
    """
    items: list[TrendItem] = []
    seen: set[str] = set()
    try:
        r = await client.get(
            "https://r.jina.ai/https://ads.tiktok.com/business/creativecenter/"
            "inspiration/popular/hashtag/pc/en?period=7&countryCode=US",
            headers={
                "User-Agent": "YoyoNewsTrends/0.4 (+https://news.yoyosup.com)",
                "Accept": "text/plain",
            },
        )
        if r.status_code == 200 and r.text:
            text = r.text
            # Ranked blocks: #tag, category, N Posts, N Views
            blocks = re.findall(
                r"(#[\w]+)\s*\n([^\n]+)\s*\n([\d.,]+[KMB]?)\s*Posts\s*\n([\d.,]+[KMB]?)\s*Views",
                text,
                re.I,
            )
            for tag, cat, posts, views in blocks:
                key = tag.lower()
                if key in seen:
                    continue
                seen.add(key)
                title = tag if tag.startswith("#") else f"#{tag}"
                items.append(
                    TrendItem(
                        rank=len(items) + 1,
                        title=title,
                        url=f"https://www.tiktok.com/tag/{quote_plus(title.lstrip('#'))}",
                        platform="tiktok",
                        snippet=f"{cat.strip()} · {posts} posts · {views} views",
                        traffic=views,
                    )
                )
                if len(items) >= MAX_PER_PLATFORM:
                    break
            if not items:
                # fallback: ordered hashtags in doc
                for tag in re.findall(r"(?m)^(#[\w]+)\s*$", text):
                    key = tag.lower()
                    if key in seen:
                        continue
                    seen.add(key)
                    items.append(
                        TrendItem(
                            rank=len(items) + 1,
                            title=tag,
                            url=f"https://www.tiktok.com/tag/{quote_plus(tag.lstrip('#'))}",
                            platform="tiktok",
                            snippet="TikTok Creative Center",
                        )
                    )
                    if len(items) >= TOP_N:
                        break
    except Exception as e:
        log.warning("TikTok Creative Center failed: %s", e)

    # Pad with news-buzz about TikTok if Creative Center is thin
    if len(items) < TOP_N:
        extra = await _fetch_social_news_buzz(client, "tiktok", limit=TOP_N)
        for it in extra:
            key = normalize_topic(it.title)
            if key in seen:
                continue
            seen.add(key)
            it.rank = len(items) + 1
            items.append(it)
            if len(items) >= MAX_PER_PLATFORM:
                break

    for i, it in enumerate(items, 1):
        it.rank = i
    return items[:MAX_PER_PLATFORM]


async def _fetch_social_news_buzz(
    client: httpx.AsyncClient, platform: str, limit: int = MAX_PER_PLATFORM
) -> list[TrendItem]:
    """
    Facebook / Instagram (and TikTok pad) have no free public top-10 API.
    Proxy: Google News RSS about what's viral *on* those platforms.
    Clearly labeled so we never pretend this is Meta's own ranking.
    """
    queries = {
        "facebook": (
            '("on Facebook" OR "Facebook post" OR "Facebook video" OR "viral on Facebook") '
            "when:7d"
        ),
        "instagram": (
            '("on Instagram" OR "Instagram Reel" OR "Instagram post" OR "viral on Instagram") '
            "when:7d"
        ),
        "tiktok": (
            '("on TikTok" OR "TikTok trend" OR "viral TikTok" OR "TikTok video") when:7d'
        ),
    }
    q = queries.get(platform)
    if not q:
        return []
    portal = {
        "facebook": "https://www.facebook.com/",
        "instagram": "https://www.instagram.com/explore/",
        "tiktok": "https://www.tiktok.com/trending",
    }.get(platform, "https://news.google.com/")
    try:
        r = await client.get(
            "https://news.google.com/rss/search",
            params={"q": q, "hl": "en-US", "gl": "US", "ceid": "US:en"},
            headers={
                **_browser_headers(),
                "Accept": "application/rss+xml, application/xml, text/xml, */*",
            },
        )
        r.raise_for_status()
        root = ET.fromstring(r.content)
        items: list[TrendItem] = []
        seen: set[str] = set()
        for entry in root.findall(".//item"):
            title = (entry.findtext("title") or "").strip()
            # strip " - Source" suffix common in Google News
            title_clean = re.sub(r"\s+-\s+[^-]+$", "", title).strip() or title
            link = (entry.findtext("link") or "").strip() or portal
            key = normalize_topic(title_clean)
            if not title_clean or key in seen:
                continue
            # skip pure site chrome
            if title_clean.lower() in {"groups", "facebook", "instagram", "tiktok"}:
                continue
            if "facebook.com" in title_clean.lower() and len(title_clean) < 24:
                continue
            seen.add(key)
            src = (entry.findtext("source") or "").strip()
            label = PLATFORM_LABELS.get(platform, platform)
            items.append(
                TrendItem(
                    rank=len(items) + 1,
                    title=title_clean[:180],
                    url=link,
                    platform=platform,
                    snippet=(
                        f"News buzz about {label}"
                        + (f" · {src}" if src else "")
                        + f" (not an official {label} ranking)"
                    ),
                )
            )
            if len(items) >= limit:
                break
        return items
    except Exception as e:
        log.warning("%s news-buzz failed: %s", platform, e)
        return []


async def _fetch_facebook(client: httpx.AsyncClient) -> list[TrendItem]:
    return await _fetch_social_news_buzz(client, "facebook")


async def _fetch_instagram(client: httpx.AsyncClient) -> list[TrendItem]:
    return await _fetch_social_news_buzz(client, "instagram")


# ── consensus + rank lookup ────────────────────────────────────────────────


def build_consensus(
    platforms: dict[str, list[dict[str, Any]]], top_n: int = TOP_N
) -> list[dict[str, Any]]:
    """
    Topics that appear on 2+ platforms (within each platform's top_n window).
    Ranked by platform count, then average rank quality.
    """
    # Collect top_n items only for consensus (keeps signal tight)
    pool: list[dict[str, Any]] = []
    for plat, items in platforms.items():
        for it in (items or [])[:top_n]:
            row = dict(it)
            row["platform"] = plat
            pool.append(row)

    parent = list(range(len(pool)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i: int, j: int) -> None:
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[rj] = ri

    for i in range(len(pool)):
        for j in range(i + 1, len(pool)):
            if pool[i]["platform"] == pool[j]["platform"]:
                continue
            if titles_similar(pool[i].get("title") or "", pool[j].get("title") or ""):
                union(i, j)

    clusters: dict[int, list[int]] = {}
    for i in range(len(pool)):
        clusters.setdefault(find(i), []).append(i)

    consensus: list[dict[str, Any]] = []
    for idxs in clusters.values():
        members = [pool[i] for i in idxs]
        plats = {m["platform"] for m in members}
        if len(plats) < 2:
            continue
        # Prefer shortest readable title as label; fallback to first
        label = sorted(members, key=lambda m: len(m.get("title") or ""))[0].get("title") or ""
        # Prefer social/search titles over long news headlines when similar length
        for pref in PLATFORM_ORDER:
            for m in members:
                if m["platform"] == pref and titles_similar(m.get("title") or "", label):
                    label = m["title"]
                    break
        ranks: dict[str, dict[str, Any]] = {}
        for m in members:
            p = m["platform"]
            prev = ranks.get(p)
            if not prev or int(m.get("rank") or 99) < int(prev.get("rank") or 99):
                ranks[p] = {
                    "rank": m.get("rank"),
                    "title": m.get("title"),
                    "url": m.get("url"),
                    "snippet": m.get("snippet") or "",
                    "label": PLATFORM_LABELS.get(p, p),
                }
        avg_rank = sum(int(r["rank"] or 99) for r in ranks.values()) / max(len(ranks), 1)
        best_url = ""
        for pref in PLATFORM_ORDER:
            if pref in ranks and ranks[pref].get("url"):
                best_url = ranks[pref]["url"]
                break
        if not best_url:
            best_url = next(iter(ranks.values())).get("url") or ""
        consensus.append(
            {
                "title": label,
                "url": best_url,
                "platform_count": len(ranks),
                "platforms": sorted(
                    ranks.keys(),
                    key=lambda p: PLATFORM_ORDER.index(p) if p in PLATFORM_ORDER else 99,
                ),
                "ranks": ranks,
                "avg_rank": round(avg_rank, 2),
                "score": len(ranks) * 100 - avg_rank,
            }
        )

    consensus.sort(key=lambda c: (-c["platform_count"], c["avg_rank"], c["title"].lower()))
    out = []
    for i, c in enumerate(consensus[:TOP_N], 1):
        c["rank"] = i
        out.append(c)
    return out


def rank_lookup(q: str, trends: dict[str, Any]) -> dict[str, Any]:
    """Where does q sit on each platform's daily list?"""
    query = re.sub(r"\s+", " ", (q or "").strip())[:200]
    platforms = trends.get("platforms") or {}
    rows: dict[str, Any] = {}
    hits = 0
    best: int | None = None

    for plat in PLATFORM_ORDER:
        items = platforms.get(plat) or []
        match = None
        for it in items:
            if query_matches_title(query, it.get("title") or ""):
                match = it
                break
        if match:
            hits += 1
            rnk = int(match.get("rank") or 0) or None
            if rnk and (best is None or rnk < best):
                best = rnk
            rows[plat] = {
                "in_top": True,
                "rank": match.get("rank"),
                "title": match.get("title"),
                "url": match.get("url"),
                "snippet": match.get("snippet") or "",
                "label": PLATFORM_LABELS.get(plat, plat),
            }
        else:
            rows[plat] = {
                "in_top": False,
                "rank": None,
                "title": None,
                "url": None,
                "snippet": "",
                "label": PLATFORM_LABELS.get(plat, plat),
            }

    # Also note if in consensus
    in_consensus = False
    consensus_rank = None
    for c in trends.get("consensus") or []:
        if query_matches_title(query, c.get("title") or ""):
            in_consensus = True
            consensus_rank = c.get("rank")
            break
        for r in (c.get("ranks") or {}).values():
            if query_matches_title(query, r.get("title") or ""):
                in_consensus = True
                consensus_rank = c.get("rank")
                break
        if in_consensus:
            break

    return {
        "q": query,
        "platforms_hit": hits,
        "best_rank": best,
        "in_consensus": in_consensus,
        "consensus_rank": consensus_rank,
        "platforms": rows,
        "summary": (
            f"On {hits} platform{'s' if hits != 1 else ''}"
            + (f" · best rank #{best}" if best else "")
            + (f" · consensus #{consensus_rank}" if in_consensus else "")
        ),
    }


def top_slice(platforms: dict[str, list], n: int = TOP_N) -> dict[str, list]:
    return {k: (v or [])[:n] for k, v in platforms.items()}


def _ensure_derived(data: dict[str, Any]) -> dict[str, Any]:
    """Fill consensus / top10 if missing (older cache or partial)."""
    platforms = data.get("platforms") or {}
    # Ensure all keys exist
    for p in PLATFORM_ORDER:
        platforms.setdefault(p, [])
    data["platforms"] = platforms
    if "consensus" not in data or data.get("consensus") is None:
        data["consensus"] = build_consensus(platforms, TOP_N)
    data["top10"] = top_slice(platforms, TOP_N)
    data["counts"] = {k: len(v or []) for k, v in platforms.items()}
    data["sources_ok"] = [k for k in PLATFORM_ORDER if platforms.get(k)]
    data["sources"] = _sources_meta(platforms)
    data["labels"] = dict(PLATFORM_LABELS)
    data["notes"] = dict(PLATFORM_NOTES)
    return data


def _sources_meta(platforms: dict[str, list]) -> list[dict[str, Any]]:
    """Ordered source chips for the summary row (always includes known platforms)."""
    out = []
    for key in PLATFORM_ORDER:
        ok = bool(platforms.get(key))
        out.append(
            {
                "id": key,
                "label": PLATFORM_LABELS.get(key, key),
                "ok": ok,
                "count": len(platforms.get(key) or []),
                "note": PLATFORM_NOTES.get(key, ""),
            }
        )
    return out


async def build_trends(force: bool = False) -> dict[str, Any]:
    if not force:
        cached = _read_cache()
        if cached:
            # Old cache without new platforms → refresh once
            plats = cached.get("platforms") or {}
            if all(p in plats for p in PLATFORM_ORDER):
                cached["cache"] = "hit"
                return cached

    timeout = httpx.Timeout(30.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        google = await _fetch_google(client)
        bing = await _fetch_bing(client)
        youtube = await _fetch_youtube(client)
        x_items = await _fetch_x(client)
        poly = await _fetch_polymarket(client)
        tiktok = await _fetch_tiktok(client)
        facebook = await _fetch_facebook(client)
        instagram = await _fetch_instagram(client)

    platforms = {
        "google": [it.to_dict() for it in google],
        "bing": [it.to_dict() for it in bing],
        "youtube": [it.to_dict() for it in youtube],
        "x": [it.to_dict() for it in x_items],
        "polymarket": [it.to_dict() for it in poly],
        "tiktok": [it.to_dict() for it in tiktok],
        "facebook": [it.to_dict() for it in facebook],
        "instagram": [it.to_dict() for it in instagram],
    }
    consensus = build_consensus(platforms, TOP_N)
    sources_ok = [name for name in PLATFORM_ORDER if platforms.get(name)]
    payload = {
        "day": _utc_day(),
        "fetched_at": _now_iso(),
        "fetched_at_unix": time.time(),
        "geo": GEO,
        "cache": "miss",
        "refresh": "daily",
        "sources_ok": sources_ok,
        "sources": _sources_meta(platforms),
        "labels": dict(PLATFORM_LABELS),
        "notes": dict(PLATFORM_NOTES),
        "counts": {k: len(v) for k, v in platforms.items()},
        "platforms": platforms,
        "top10": top_slice(platforms, TOP_N),
        "consensus": consensus,
        "disclaimer": (
            "Daily attention + money map (UTC day). "
            "Google Trends · Bing Popular/News · YouTube Top Videos · X (trends24) · "
            "Polymarket 24h volume · TikTok Creative Center hashtags · "
            "Facebook/Instagram via news-buzz proxies (Meta has no free public top-10 API). "
            "Consensus = topics on 2+ platform Top 10s. Not affiliated; not financial advice."
        ),
    }
    _write_cache(payload)
    return payload
