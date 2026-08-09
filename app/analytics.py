"""Built-in visitor analytics for News — aggregate only, no third-party tags.

Mirrors finance/app/analytics.py + money counter ideas: visits, ref, geo,
hourly archive, path-based action events. No IPs stored.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import asyncio

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
STORE_PATH = CACHE_DIR / "analytics.json"
# Prefer dedicated analytics key; fall back to mod token so one secret works
ADMIN_KEY = (
    os.environ.get("ANALYTICS_ADMIN_KEY")
    or os.environ.get("MOD_ADMIN_TOKEN")
    or ""
).strip()
TZ_NAME = (os.environ.get("ANALYTICS_TZ") or os.environ.get("TZ") or "America/Denver").strip() or "America/Denver"

_lock = asyncio.Lock()

SKIP_PREFIXES = ("/api/", "/static/", "/admin/")
SKIP_EXACT = {"/health", "/robots.txt", "/sitemap.xml"}

BOT_UA_RE = re.compile(
    r"bot|crawler|spider|slurp|headless|preview|facebookexternalhit|"
    r"googleother|google-inspectiontool|bingpreview|petalbot|bytespider|"
    r"semrush|ahrefs|mj12bot|dotbot|dataforseo|python-requests|curl/|wget/",
    re.IGNORECASE,
)

ALLOWED_CLIENT_EVENTS = {
    "session_new",
    "session_returning",
    "mynews_topic_add",
    "mynews_topic_remove",
    "mynews_clear",
    "search_submit",
    "story_click",
    "topic_open",
    "share_click",
}

_COUNTRY_NAMES = {
    "US": "United States", "GB": "United Kingdom", "CA": "Canada", "AU": "Australia",
    "DE": "Germany", "FR": "France", "IN": "India", "BR": "Brazil", "MX": "Mexico",
    "JP": "Japan", "KR": "South Korea", "NL": "Netherlands", "ES": "Spain", "IT": "Italy",
    "XX": "Unknown", "T1": "Tor / anonymous",
}


def _empty_data() -> dict:
    return {
        "total": 0,
        "by_day": {},
        "by_page": {},
        "by_ref": {},
        "by_country": {},
        "by_state": {},
        "by_city": {},
        "by_city_us": {},
        "by_referrer": {},
        "by_day_hour": {},
        "by_day_city": {},
        "by_day_city_us": {},
        "by_event": {},
        "by_day_event": {},
        "actions": 0,
        "meaningful_actions": 0,
    }


def _load() -> dict:
    try:
        data = json.loads(STORE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return _empty_data()
    base = _empty_data()
    for k, v in data.items():
        if k in base:
            base[k] = v
    return base


def _save(data: dict) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = STORE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(data))
    tmp.replace(STORE_PATH)


def _tz():
    try:
        return ZoneInfo(TZ_NAME)
    except Exception:
        return timezone.utc


def _today() -> str:
    return datetime.now(_tz()).strftime("%Y-%m-%d")


def _hour() -> int:
    return datetime.now(_tz()).hour


def clean_ref(raw: str | None) -> str:
    ref = re.sub(r"[^a-zA-Z0-9_\-]", "", raw or "")[:40]
    return ref or "direct"


def clean_page(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9/_\-.]", "", raw)[:120] or "/"


def normalize_country(raw: str | None) -> str:
    if not raw:
        return "XX"
    c = raw.strip().upper()
    if c in ("XX", "T1"):
        return c
    return c if re.fullmatch(r"[A-Z]{2}", c) else "XX"


def normalize_state(raw: str | None) -> str:
    if not raw:
        return "Unknown"
    name = re.sub(r"[^a-zA-Z0-9 .'\-]", "", raw.strip())[:60]
    return name or "Unknown"


def normalize_city(city_raw: str | None, region_raw: str | None, region_code: str | None, country: str) -> str:
    city = re.sub(r"[^a-zA-Z0-9 .'\-]", "", (city_raw or "").strip())[:48]
    if not city:
        return "Unknown"
    code = re.sub(r"[^A-Za-z0-9]", "", (region_code or "").strip())[:8]
    region = normalize_state(region_raw)
    if country == "US":
        loc = code.upper() if code else (region if region != "Unknown" else "")
        return f"{city}, {loc}" if loc else f"{city}, US"
    if country and country not in ("XX", "T1"):
        return f"{city}, {country}"
    return city


def referrer_host(raw: str | None) -> str:
    if not raw:
        return "(none)"
    try:
        host = (urlparse(raw).hostname or "").lower()
    except ValueError:
        return "(none)"
    if not host:
        return "(none)"
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("news.yoyosup.com"):
        return "(same-site)"
    return host


def _trim(d: dict, cap: int, keep: int) -> None:
    if len(d) > cap:
        ranked = sorted(d.items(), key=lambda x: -x[1])[:keep]
        d.clear()
        d.update(ranked)


def should_track(path: str) -> bool:
    if path in SKIP_EXACT:
        return False
    return not any(path.startswith(p) for p in SKIP_PREFIXES)


def is_probable_bot(user_agent: str | None, cf_verified_bot: str | None = None) -> bool:
    """Conservatively exclude declared crawlers and browserless HTTP clients."""
    if (cf_verified_bot or "").strip().lower() in {"1", "true", "yes"}:
        return True
    ua = (user_agent or "").strip()
    return not ua or bool(BOT_UA_RE.search(ua))


def action_for_path(path: str) -> str | None:
    if path in ("/", ""):
        return "pulse_view"
    if path.startswith("/search"):
        return "search_view"
    if path in ("/my", "/mynews") or path.startswith("/my/") or path.startswith("/mynews"):
        return "mynews_view"
    if path.startswith("/topic"):
        return "topic_view"
    if path.startswith("/safety"):
        return "safety_view"
    return None


def _hour_map(data: dict, day: str) -> dict:
    raw = (data.get("by_day_hour") or {}).get(day) or {}
    if not isinstance(raw, dict):
        raw = {}
    return {str(h): int(raw.get(str(h), raw.get(h, 0)) or 0) for h in range(24)}


def _city_map(data: dict, day: str, us_only: bool = False) -> dict:
    key = "by_day_city_us" if us_only else "by_day_city"
    raw = (data.get(key) or {}).get(day) or {}
    if not isinstance(raw, dict):
        return {}
    ranked = sorted(raw.items(), key=lambda x: -int(x[1] or 0))[:40]
    return {k: int(v or 0) for k, v in ranked}


def _trim_day_city_maps(data: dict, keep_days: int = 45) -> None:
    for key in ("by_day_city", "by_day_city_us"):
        bdc = data.setdefault(key, {})
        if not isinstance(bdc, dict):
            data[key] = {}
            continue
        for day in list(bdc.keys()):
            m = bdc.get(day)
            if not isinstance(m, dict):
                del bdc[day]
                continue
            if len(m) > 80:
                bdc[day] = dict(sorted(m.items(), key=lambda x: -int(x[1] or 0))[:40])
        if len(bdc) > keep_days + 15:
            for k in sorted(bdc)[:-keep_days]:
                del bdc[k]


async def record_hit(
    path: str,
    ref: str | None,
    country_raw: str | None,
    state_raw: str | None,
    referer: str | None,
    city_raw: str | None = None,
    region_code: str | None = None,
    user_agent: str | None = None,
    cf_verified_bot: str | None = None,
) -> None:
    if not should_track(path) or is_probable_bot(user_agent, cf_verified_bot):
        return
    page = clean_page(path)
    ref = clean_ref(ref)
    country = normalize_country(country_raw)
    state = normalize_state(state_raw)
    city = normalize_city(city_raw, state if state != "Unknown" else None, region_code, country)
    ref_host = referrer_host(referer)
    event = action_for_path(path)

    async with _lock:
        data = _load()
        data["total"] += 1
        day = _today()
        hour = str(_hour())
        data["by_day"][day] = data["by_day"].get(day, 0) + 1
        day_hours = data.setdefault("by_day_hour", {})
        hm = day_hours.setdefault(day, {})
        if not isinstance(hm, dict):
            hm = {}
            day_hours[day] = hm
        hm[hour] = int(hm.get(hour, 0) or 0) + 1
        data["by_page"][page] = data["by_page"].get(page, 0) + 1
        data["by_ref"][ref] = data["by_ref"].get(ref, 0) + 1
        data["by_country"][country] = data["by_country"].get(country, 0) + 1
        data["by_state"][state] = data["by_state"].get(state, 0) + 1
        data["by_city"][city] = data["by_city"].get(city, 0) + 1
        day_cities = data.setdefault("by_day_city", {})
        dcm = day_cities.setdefault(day, {})
        if not isinstance(dcm, dict):
            dcm = {}
            day_cities[day] = dcm
        dcm[city] = int(dcm.get(city, 0) or 0) + 1
        if country == "US" and city != "Unknown":
            us = data.setdefault("by_city_us", {})
            us[city] = us.get(city, 0) + 1
            day_us = data.setdefault("by_day_city_us", {})
            dum = day_us.setdefault(day, {})
            if not isinstance(dum, dict):
                dum = {}
                day_us[day] = dum
            dum[city] = int(dum.get(city, 0) or 0) + 1
        data["by_referrer"][ref_host] = data["by_referrer"].get(ref_host, 0) + 1
        if event:
            ev = data.setdefault("by_event", {})
            ev[event] = int(ev.get(event, 0) or 0) + 1

        if len(data["by_day"]) > 120:
            for k in sorted(data["by_day"])[:-90]:
                del data["by_day"][k]
                if k in data.get("by_day_hour", {}):
                    del data["by_day_hour"][k]
                if k in data.get("by_day_city", {}):
                    del data["by_day_city"][k]
                if k in data.get("by_day_city_us", {}):
                    del data["by_day_city_us"][k]
        bdh = data.get("by_day_hour") or {}
        if len(bdh) > 60:
            for k in sorted(bdh)[:-45]:
                del bdh[k]
        _trim_day_city_maps(data, keep_days=45)
        _trim(data["by_page"], 300, 200)
        _trim(data["by_ref"], 300, 200)
        _trim(data["by_country"], 250, 200)
        _trim(data["by_state"], 250, 200)
        _trim(data["by_city"], 400, 300)
        _trim(data.setdefault("by_city_us", {}), 400, 300)
        _trim(data["by_referrer"], 400, 300)
        _trim(data.setdefault("by_event", {}), 200, 100)
        _save(data)


async def record_client_event(name: str) -> bool:
    """Record a small allowlisted browser event; never accept arbitrary labels."""
    if name not in ALLOWED_CLIENT_EVENTS:
        return False
    async with _lock:
        data = _load()
        data["meaningful_actions"] = int(data.get("meaningful_actions", 0) or 0) + 1
        events = data.setdefault("by_event", {})
        events[name] = int(events.get(name, 0) or 0) + 1
        day_events = data.setdefault("by_day_event", {}).setdefault(_today(), {})
        day_events[name] = int(day_events.get(name, 0) or 0) + 1
        if len(data["by_day_event"]) > 60:
            for day in sorted(data["by_day_event"])[:-45]:
                del data["by_day_event"][day]
        _trim(events, 200, 100)
        _save(data)
    return True


def country_label(code: str) -> str:
    name = _COUNTRY_NAMES.get(code)
    return f"{name} ({code})" if name else code


def get_stats(day: str | None = None) -> dict:
    data = _load()
    today = _today()
    week_start = str((datetime.now(_tz()) - timedelta(days=6)).date())
    days = dict(sorted(data["by_day"].items(), reverse=True)[:30])
    bdh = data.get("by_day_hour") or {}
    day_keys = sorted(bdh.keys(), reverse=True)[:14] or list(days.keys())[:14]
    by_day_hour = {d: _hour_map(data, d) for d in day_keys}
    bdc = data.get("by_day_city") or {}
    city_keys = sorted(set(list(bdh.keys()) + list(bdc.keys())), reverse=True)[:14]
    by_day_city = {d: _city_map(data, d, us_only=False) for d in city_keys}
    by_day_city_us = {d: _city_map(data, d, us_only=True) for d in city_keys}
    archive_day = day if day and re.fullmatch(r"\d{4}-\d{2}-\d{2}", day) else today
    return {
        "product": "news",
        "total": data["total"],
        "today": data["by_day"].get(today, 0),
        "week": sum(v for k, v in data["by_day"].items() if k >= week_start),
        # `actions` used to mirror every page view. Report only the clean,
        # browser-generated counter while keeping the legacy field readable.
        "actions": int(data.get("meaningful_actions", 0) or 0),
        "tz": TZ_NAME,
        "by_day": days,
        "by_page": dict(sorted(data["by_page"].items(), key=lambda x: -x[1])[:30]),
        "by_ref": dict(sorted(data["by_ref"].items(), key=lambda x: -x[1])[:30]),
        "by_country": dict(sorted(data["by_country"].items(), key=lambda x: -x[1])[:30]),
        "by_state": dict(sorted(data["by_state"].items(), key=lambda x: -x[1])[:30]),
        "by_city": dict(sorted(data.get("by_city", {}).items(), key=lambda x: -x[1])[:30]),
        "by_city_us": dict(sorted(data.get("by_city_us", {}).items(), key=lambda x: -x[1])[:30]),
        "by_referrer": dict(sorted(data["by_referrer"].items(), key=lambda x: -x[1])[:30]),
        "by_event": dict(sorted((data.get("by_event") or {}).items(), key=lambda x: -x[1])[:30]),
        "by_day_event": {
            day: dict(sorted(events.items(), key=lambda x: -x[1])[:30])
            for day, events in sorted((data.get("by_day_event") or {}).items(), reverse=True)[:30]
            if isinstance(events, dict)
        },
        "by_day_hour": by_day_hour,
        "by_day_city": by_day_city,
        "by_day_city_us": by_day_city_us,
        "archive_day": archive_day,
        "archive_hours": _hour_map(data, archive_day),
        "archive_cities": _city_map(data, archive_day, us_only=False),
        "archive_cities_us": _city_map(data, archive_day, us_only=True),
        "updated": datetime.now(timezone.utc).isoformat(),
    }
