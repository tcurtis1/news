"""Daily multi-platform trends + consensus + rank lookup.

Sources: Google, Bing, YouTube, X, Polymarket, TikTok, Facebook, Instagram.
Pulled at most once per UTC day (CACHE_DIR). Force with force=True / ?force=1.
"""

from __future__ import annotations

import asyncio
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

from app.places import Place, cache_key, default_place, resolve_place

log = logging.getLogger("trends")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
# Legacy single-file cache (migrated into per-place dir on read)
CACHE_FILE = CACHE_DIR / "trends_cache.json"
PREV_FILE = CACHE_DIR / "trends_yesterday.json"
TRENDS_DIR = CACHE_DIR / "trends"
PREV_DIR = CACHE_DIR / "trends_yesterday"
CACHE_MAX_AGE_SEC = int(os.environ.get("TRENDS_CACHE_TTL", str(26 * 3600)))
USER_AGENT = (
    "Mozilla/5.0 (compatible; YoyoNewsTrends/0.5; +https://news.yoyosup.com) "
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
# Short note shown under column headers / chips (geo suffix added at runtime)
PLATFORM_NOTES = {
    "google": "Trends RSS",
    "bing": "Popular + News",
    "youtube": "Daily Top Videos",
    "x": "trends24",
    "polymarket": "24h volume · Global",
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


def _browser_headers(accept_lang: str = "en-US,en;q=0.9") -> dict[str, str]:
    return {
        "User-Agent": USER_AGENT,
        "Accept-Language": accept_lang,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    }


def _place_cache_paths(place: Place) -> tuple[Path, Path]:
    key = cache_key(place)
    return TRENDS_DIR / f"{key}.json", PREV_DIR / f"{key}.json"


def _read_json(path: Path) -> dict | None:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _legacy_migrate_default(place: Place) -> None:
    """One-time: copy old flat cache files into per-place paths for default geo."""
    if place.code != default_place().code:
        return
    today, yday = _place_cache_paths(place)
    try:
        if CACHE_FILE.exists() and not today.exists():
            TRENDS_DIR.mkdir(parents=True, exist_ok=True)
            today.write_text(CACHE_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Migrated legacy trends_cache.json → %s", today)
        if PREV_FILE.exists() and PREV_FILE.is_file() and not yday.exists():
            PREV_DIR.mkdir(parents=True, exist_ok=True)
            yday.write_text(PREV_FILE.read_text(encoding="utf-8"), encoding="utf-8")
            log.info("Migrated legacy trends_yesterday.json → %s", yday)
    except Exception as e:
        log.warning("legacy cache migrate failed: %s", e)


def _read_cache(place: Place) -> dict | None:
    try:
        _legacy_migrate_default(place)
        today, yday = _place_cache_paths(place)
        data = _read_json(today)
        if not data:
            return None
        # Geo mismatch (stale/wrong file) → miss
        if data.get("geo") and data.get("geo") != place.code:
            return None
        if data.get("day") == _utc_day() or (
            time.time() - float(data.get("fetched_at_unix", 0)) <= CACHE_MAX_AGE_SEC
        ):
            data = _ensure_derived(data, place)
            return apply_deltas(data, _read_json(yday))
        return None
    except Exception:
        return None


def _write_cache(payload: dict, place: Place) -> None:
    try:
        today, yday = _place_cache_paths(place)
        TRENDS_DIR.mkdir(parents=True, exist_ok=True)
        PREV_DIR.mkdir(parents=True, exist_ok=True)
        existing = _read_json(today)
        if (
            existing
            and existing.get("day")
            and payload.get("day")
            and existing.get("day") != payload.get("day")
        ):
            prev = {
                "day": existing.get("day"),
                "fetched_at": existing.get("fetched_at"),
                "geo": existing.get("geo") or place.code,
                "platforms": existing.get("platforms") or {},
                "consensus": existing.get("consensus") or [],
            }
            _write_json(yday, prev)
            log.info(
                "Archived trends day %s geo=%s → yesterday",
                existing.get("day"),
                place.code,
            )
        _write_json(today, payload)
        # Keep legacy paths in sync for default geo (warm scripts / ops)
        if place.code == default_place().code:
            try:
                _write_json(CACHE_FILE, payload)
            except Exception:
                pass
    except Exception as e:
        log.warning("trends cache write failed: %s", e)


def platform_notes_for(place: Place) -> dict[str, str]:
    """Column notes with geo honesty."""
    pg = place.platform_geo()
    out: dict[str, str] = {}
    local = place.short_label()
    for key, base in PLATFORM_NOTES.items():
        meta = pg.get(key) or {}
        scope = meta.get("scope") or "partial"
        code = meta.get("code") or ""
        if scope == "global":
            out[key] = base if "Global" in base else f"{base} · Global"
        elif scope == "none":
            out[key] = f"{base} · unavailable here"
        elif place.kind == "region" and scope == "country":
            out[key] = f"{base} · {place.country} country chart (not {local}-specific)"
        elif place.kind == "region" and scope == "partial":
            out[key] = f"{base} · {local} local buzz"
        elif scope == "full":
            out[key] = f"{base} · {code or place.code}"
        else:
            out[key] = f"{base} · {code or place.code}"
    return out


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


async def _fetch_google(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    try:
        r = await client.get(
            f"https://trends.google.com/trending/rss?geo={place.google_geo}",
            headers={
                **_browser_headers(place.news_hl.replace("_", "-") + ",en;q=0.8"),
                "Accept": "application/rss+xml, application/xml",
            },
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


async def _fetch_bing(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    items: list[TrendItem] = []
    seen: set[str] = set()
    headers = {
        **_browser_headers(place.news_hl.replace("_", "-") + ",en;q=0.8"),
        # Hint market; Bing may still skew US without cookies
        "Cookie": f"mkt={place.bing_market};",
    }
    is_region = place.kind == "region"
    local_name = place.short_label()

    # Regions: lead with place-local Bing News so the board actually changes vs US.
    # Countries: Popular Now first, then national/country news pad.
    if not is_region:
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
                    "mkt": place.bing_market,
                },
                headers=headers,
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
        # Country → country label; US state → "Utah" (not "United States")
        news_q = local_name if is_region else place.label
        r = await client.get(
            "https://www.bing.com/news/search",
            params={"q": news_q, "format": "RSS", "market": place.bing_market},
            headers={
                **headers,
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
                snip_base = (
                    f"Bing News · {local_name}"
                    if is_region
                    else "Bing News"
                )
                items.append(
                    TrendItem(
                        rank=len(items) + 1,
                        title=title,
                        url=link or f"https://www.bing.com/news/search?q={quote_plus(title)}",
                        platform="bing",
                        snippet=(desc[:160] + "…") if len(desc) > 160 else desc or snip_base,
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


async def _fetch_youtube(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    gl = place.youtube_gl
    try:
        body = {
            "context": {
                "client": {
                    "clientName": "WEB_MUSIC_ANALYTICS",
                    "clientVersion": "2.0",
                    "hl": "en",
                    "gl": gl,
                    "userAgent": USER_AGENT,
                    "platform": "DESKTOP",
                }
            },
            "browseId": "FEmusic_analytics_charts_home",
            "query": (
                "perspective=CHART_DETAILS"
                f"&chart_params_country_code={gl.lower()}"
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
                "Referer": f"https://charts.youtube.com/charts/TopVideos/{gl.lower()}/daily",
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


async def _fetch_x(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    if not place.x_path:
        return []
    label_geo = place.country if place.kind == "region" else place.code
    try:
        r = await client.get(
            f"https://trends24.in/{place.x_path}/",
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
                    snippet=f"Trending on X ({label_geo})",
                )
            )
            if len(items) >= MAX_PER_PLATFORM:
                break
        return items
    except Exception as e:
        log.warning("X trends failed for %s: %s", place.x_path, e)
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


async def _fetch_tiktok(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    """
    TikTok popular hashtags via Creative Center (public page → jina reader).
    Meta/TikTok block most direct JSON APIs without login; this is best-effort.
    US states: lead with local TikTok news buzz (no free state hashtag chart).
    """
    items: list[TrendItem] = []
    seen: set[str] = set()
    is_region = place.kind == "region"

    # State/region: local news-about-TikTok first so the board differs from US.
    if is_region:
        for it in await _fetch_social_news_buzz(client, "tiktok", place, limit=MAX_PER_PLATFORM):
            key = normalize_topic(it.title)
            if key in seen:
                continue
            seen.add(key)
            it.rank = len(items) + 1
            items.append(it)

    cc = place.tiktok_country
    # Country Creative Center (also pads thin region boards)
    if len(items) < TOP_N:
        try:
            r = await client.get(
                "https://r.jina.ai/https://ads.tiktok.com/business/creativecenter/"
                f"inspiration/popular/hashtag/pc/en?period=7&countryCode={cc}",
                headers={
                    "User-Agent": "YoyoNewsTrends/0.5 (+https://news.yoyosup.com)",
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
                    snip = f"{cat.strip()} · {posts} posts · {views} views"
                    if is_region:
                        snip = f"{snip} · {cc} chart"
                    items.append(
                        TrendItem(
                            rank=len(items) + 1,
                            title=title,
                            url=f"https://www.tiktok.com/tag/{quote_plus(title.lstrip('#'))}",
                            platform="tiktok",
                            snippet=snip,
                            traffic=views,
                        )
                    )
                    if len(items) >= MAX_PER_PLATFORM:
                        break
                if len(items) < TOP_N:
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

    # Country pad with national TikTok news buzz if still thin
    if not is_region and len(items) < TOP_N:
        extra = await _fetch_social_news_buzz(client, "tiktok", place, limit=TOP_N)
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
    client: httpx.AsyncClient,
    platform: str,
    place: Place,
    limit: int = MAX_PER_PLATFORM,
) -> list[TrendItem]:
    """
    Facebook / Instagram (and TikTok pad) have no free public top-10 API.
    Proxy: Google News RSS about what's viral *on* those platforms.
    Clearly labeled so we never pretend this is Meta's own ranking.
    """
    base_queries = {
        "facebook": (
            '("on Facebook" OR "Facebook post" OR "Facebook video" OR "viral on Facebook")'
        ),
        "instagram": (
            '("on Instagram" OR "Instagram Reel" OR "Instagram post" OR "viral on Instagram")'
        ),
        "tiktok": (
            '("on TikTok" OR "TikTok trend" OR "viral TikTok" OR "TikTok video")'
        ),
    }
    base = base_queries.get(platform)
    if not base:
        return []
    # US states / regions: require place name so boards actually change vs country
    if place.kind == "region":
        local = place.short_label()
        q = f"{base} {local} when:14d"
    else:
        q = f"{base} when:7d"
    portal = {
        "facebook": "https://www.facebook.com/",
        "instagram": "https://www.instagram.com/explore/",
        "tiktok": "https://www.tiktok.com/trending",
    }.get(platform, "https://news.google.com/")
    try:
        r = await client.get(
            "https://news.google.com/rss/search",
            params={
                "q": q,
                "hl": place.news_hl,
                "gl": place.news_gl,
                "ceid": place.news_ceid,
            },
            headers={
                **_browser_headers(place.news_hl.replace("_", "-") + ",en;q=0.8"),
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
            where = place.short_label() if place.kind == "region" else ""
            items.append(
                TrendItem(
                    rank=len(items) + 1,
                    title=title_clean[:180],
                    url=link,
                    platform=platform,
                    snippet=(
                        f"News buzz about {label}"
                        + (f" in {where}" if where else "")
                        + (f" · {src}" if src else "")
                        + f" (not an official {label} ranking)"
                    ),
                )
            )
            if len(items) >= limit:
                break
        # Region fallback: local headlines if platform+place query is thin
        if place.kind == "region" and len(items) < max(3, limit // 2):
            local = place.short_label()
            r2 = await client.get(
                "https://news.google.com/rss/search",
                params={
                    "q": f"{local} when:2d",
                    "hl": place.news_hl,
                    "gl": place.news_gl,
                    "ceid": place.news_ceid,
                },
                headers={
                    **_browser_headers(place.news_hl.replace("_", "-") + ",en;q=0.8"),
                    "Accept": "application/rss+xml, application/xml, text/xml, */*",
                },
            )
            if r2.status_code == 200:
                root2 = ET.fromstring(r2.content)
                for entry in root2.findall(".//item"):
                    title = (entry.findtext("title") or "").strip()
                    title_clean = re.sub(r"\s+-\s+[^-]+$", "", title).strip() or title
                    link = (entry.findtext("link") or "").strip() or portal
                    key = normalize_topic(title_clean)
                    if not title_clean or key in seen:
                        continue
                    seen.add(key)
                    src = (entry.findtext("source") or "").strip()
                    items.append(
                        TrendItem(
                            rank=len(items) + 1,
                            title=title_clean[:180],
                            url=link,
                            platform=platform,
                            snippet=(
                                f"Local {local} headline"
                                + (f" · {src}" if src else "")
                                + f" (proxy for {label})"
                            ),
                        )
                    )
                    if len(items) >= limit:
                        break
        return items
    except Exception as e:
        log.warning("%s news-buzz failed: %s", platform, e)
        return []


async def _fetch_facebook(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    return await _fetch_social_news_buzz(client, "facebook", place)


async def _fetch_instagram(client: httpx.AsyncClient, place: Place) -> list[TrendItem]:
    return await _fetch_social_news_buzz(client, "instagram", place)


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

    # Explicit hit list so every platform (incl. Polymarket) is named in the summary
    hit_bits = []
    for plat in PLATFORM_ORDER:
        row = rows.get(plat) or {}
        if row.get("in_top") and row.get("rank") is not None:
            lab = row.get("label") or PLATFORM_LABELS.get(plat, plat)
            hit_bits.append(f"{lab} #{row['rank']}")

    if hits == 0:
        summary = "Not in any platform’s top list today"
    else:
        summary = " · ".join(hit_bits)
        if in_consensus and consensus_rank is not None:
            summary += f" · consensus #{consensus_rank}"

    return {
        "q": query,
        "platforms_hit": hits,
        "best_rank": best,
        "in_consensus": in_consensus,
        "consensus_rank": consensus_rank,
        "platforms": rows,
        "hit_summary": hit_bits,
        "summary": summary,
    }


def top_slice(platforms: dict[str, list], n: int = TOP_N) -> dict[str, list]:
    return {k: (v or [])[:n] for k, v in platforms.items()}


def _find_prev_item(title: str, prev_items: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Match a title against yesterday's list (exact normalize, then fuzzy)."""
    if not title or not prev_items:
        return None
    nt = normalize_topic(title)
    for it in prev_items:
        if normalize_topic(it.get("title") or "") == nt:
            return it
    for it in prev_items:
        if titles_similar(title, it.get("title") or ""):
            return it
    return None


def _delta_fields(rank: int, prev_rank: int | None) -> dict[str, Any]:
    """Build delta badge fields. Lower rank number = higher on the list."""
    if prev_rank is None:
        return {
            "delta": "new",
            "delta_label": "NEW",
            "prev_rank": None,
            "rank_change": None,
        }
    change = int(prev_rank) - int(rank)  # + = rose (better)
    if change > 0:
        return {
            "delta": "up",
            "delta_label": f"↑{change}",
            "prev_rank": prev_rank,
            "rank_change": change,
        }
    if change < 0:
        return {
            "delta": "down",
            "delta_label": f"↓{abs(change)}",
            "prev_rank": prev_rank,
            "rank_change": change,
        }
    return {
        "delta": "same",
        "delta_label": "same",
        "prev_rank": prev_rank,
        "rank_change": 0,
    }


def apply_deltas(payload: dict[str, Any], prev: dict | None) -> dict[str, Any]:
    """
    Annotate platform items + consensus with day-over-day movement vs prev snapshot.
    """
    platforms = payload.get("platforms") or {}

    if not prev or not (prev.get("platforms") or prev.get("consensus")):
        payload["delta_vs"] = None
        payload["delta_status"] = "baseline"
        payload["delta_stats"] = {"new": 0, "up": 0, "down": 0, "same": 0, "baseline": 0}
        for items in platforms.values():
            for it in items or []:
                it["delta"] = "baseline"
                it["delta_label"] = "—"
                it["prev_rank"] = None
                it["rank_change"] = None
        for c in payload.get("consensus") or []:
            c["delta"] = "baseline"
            c["delta_label"] = "—"
            c["entered_consensus"] = False
            c["prev_rank"] = None
            c["rank_change"] = None
        payload["top10"] = top_slice(platforms, TOP_N)
        return payload

    prev_day = prev.get("day")
    payload["delta_vs"] = prev_day
    payload["delta_status"] = "ok"
    prev_plats = prev.get("platforms") or {}
    stats = {"new": 0, "up": 0, "down": 0, "same": 0}

    for plat, items in platforms.items():
        prev_items = prev_plats.get(plat) or []
        for it in items or []:
            prev_it = _find_prev_item(it.get("title") or "", prev_items)
            prev_rank = int(prev_it["rank"]) if prev_it and prev_it.get("rank") else None
            d = _delta_fields(int(it.get("rank") or 0), prev_rank)
            it.update(d)
            # Count only top-N display window for board stats
            if int(it.get("rank") or 99) <= TOP_N:
                key = d["delta"]
                if key in stats:
                    stats[key] += 1

    prev_cons = prev.get("consensus") or []
    for c in payload.get("consensus") or []:
        prev_c = _find_prev_item(c.get("title") or "", prev_cons)
        if not prev_c:
            c["delta"] = "new"
            c["delta_label"] = "NEW"
            c["entered_consensus"] = True
            c["prev_rank"] = None
            c["rank_change"] = None
        else:
            d = _delta_fields(int(c.get("rank") or 0), int(prev_c.get("rank") or 0))
            c.update(d)
            c["entered_consensus"] = False

    payload["delta_stats"] = stats
    payload["top10"] = top_slice(platforms, TOP_N)
    return payload


def _ensure_derived(data: dict[str, Any], place: Place | None = None) -> dict[str, Any]:
    """Fill consensus / top10 if missing (older cache or partial)."""
    place = place or resolve_place(data.get("geo"))
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
    notes = platform_notes_for(place)
    data["sources"] = _sources_meta(platforms, place, notes)
    data["labels"] = dict(PLATFORM_LABELS)
    data["notes"] = notes
    data["geo"] = place.code
    data["place"] = place.to_dict()
    data["coverage"] = place.coverage_summary()
    return data


def _sources_meta(
    platforms: dict[str, list],
    place: Place,
    notes: dict[str, str] | None = None,
) -> list[dict[str, Any]]:
    """Ordered source chips for the summary row (always includes known platforms)."""
    notes = notes or platform_notes_for(place)
    pg = place.platform_geo()
    out = []
    for key in PLATFORM_ORDER:
        ok = bool(platforms.get(key))
        scope = (pg.get(key) or {}).get("scope") or ""
        out.append(
            {
                "id": key,
                "label": PLATFORM_LABELS.get(key, key),
                "ok": ok,
                "count": len(platforms.get(key) or []),
                "note": notes.get(key, PLATFORM_NOTES.get(key, "")),
                "geo_scope": scope,
                "geo_code": (pg.get(key) or {}).get("code") or "",
            }
        )
    return out


async def build_trends(force: bool = False, geo: str | None = None) -> dict[str, Any]:
    place = resolve_place(geo)
    if not force:
        cached = _read_cache(place)
        if cached:
            plats = cached.get("platforms") or {}
            if all(p in plats for p in PLATFORM_ORDER):
                cached["cache"] = "hit"
                return cached

    timeout = httpx.Timeout(30.0, connect=8.0)
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
        # Parallel platform pulls — first visit to a geo is bounded by slowest source
        (
            google,
            bing,
            youtube,
            x_items,
            poly,
            tiktok,
            facebook,
            instagram,
        ) = await asyncio.gather(
            _fetch_google(client, place),
            _fetch_bing(client, place),
            _fetch_youtube(client, place),
            _fetch_x(client, place),
            _fetch_polymarket(client),
            _fetch_tiktok(client, place),
            _fetch_facebook(client, place),
            _fetch_instagram(client, place),
        )

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
    notes = platform_notes_for(place)
    coverage = place.coverage_summary()
    payload = {
        "day": _utc_day(),
        "fetched_at": _now_iso(),
        "fetched_at_unix": time.time(),
        "geo": place.code,
        "place": place.to_dict(),
        "cache": "miss",
        "refresh": "daily",
        "sources_ok": sources_ok,
        "sources": _sources_meta(platforms, place, notes),
        "labels": dict(PLATFORM_LABELS),
        "notes": notes,
        "counts": {k: len(v) for k, v in platforms.items()},
        "platforms": platforms,
        "top10": top_slice(platforms, TOP_N),
        "consensus": consensus,
        "coverage": coverage,
        "disclaimer": (
            f"Daily attention + money map for {place.label} (UTC day). "
            "Google Trends · Bing Popular/News · YouTube Top Videos · X (trends24) · "
            "Polymarket 24h volume (always global) · TikTok Creative Center hashtags · "
            "Facebook/Instagram via news-buzz proxies. "
            "US states: Google is state-level; Bing/Facebook/Instagram/TikTok use "
            "local news buzz for that state; YouTube & X stay at U.S. country charts; "
            "Polymarket is global. "
            "Consensus = topics on 2+ platform Top 10s. "
            "Deltas (NEW / ↑ / ↓) compare to yesterday’s UTC snapshot for this place. "
            "Not affiliated; not financial advice."
        ),
    }
    _write_cache(payload, place)
    _, yday = _place_cache_paths(place)
    payload = apply_deltas(payload, _read_json(yday))
    try:
        today, _ = _place_cache_paths(place)
        _write_json(today, payload)
        if place.code == default_place().code:
            _write_json(CACHE_FILE, payload)
    except Exception:
        pass
    return payload
