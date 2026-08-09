"""
Per-journalist bias estimates — bylines are looked up from RSS feeds and, when
missing, backfilled by fetching the article page's own JSON-LD/meta author tag.

Honest, transparent, and explicitly NOT a fact-check or personal characterization
of anyone — see build_journalist()'s disclaimer. Reuses app.bias's outlet-lean
map; this module never classifies an outlet itself, only rolls up outlet leans
per byline.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from app.bias import lean_for

# NOTE: app.topics.slugify/unslug are intentionally imported lazily inside the
# functions below, not at module scope — app.topics imports app.search, and
# app.search imports this module, so a top-level import here would be a
# circular import at load time.

log = logging.getLogger("journalists")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
JOURNALISTS_DIR = CACHE_DIR / "journalists"
PENDING_FILE = JOURNALISTS_DIR / "_pending.json"

MAX_SIGHTINGS = 50
MAX_PENDING = 500
MAX_ATTEMPTS = 2

RATE_LIMIT_HOURS = 24
_lock = threading.Lock()
_last_rating: dict[tuple[str, str], float] = {}  # (ip_hash, slug) -> ts, in-memory only

# Generic/organizational bylines we never want to build a "journalist" profile for.
_GENERIC_BYLINES = {
    "staff",
    "staff writer",
    "staff report",
    "staff reports",
    "wire services",
    "wire report",
    "associated press",
    "reuters staff",
    "reuters",
    "the associated press",
    "editor",
    "editors",
    "editorial staff",
    "editorial board",
    "admin",
    "administrator",
    "contributor",
    "contributors",
    "newsroom",
    "news desk",
    "guest author",
}

# lean codes for the 5-point journalist scale — distinct from bias.py's outlet codes
J_LIBERAL = "liberal"
J_LEANS_LIBERAL = "leans_liberal"
J_CENTER = "center"
J_LEANS_CONSERVATIVE = "leans_conservative"
J_CONSERVATIVE = "conservative"
J_NOT_ENOUGH_DATA = "not_enough_data"

J_LABELS = {
    J_LIBERAL: "Liberal",
    J_LEANS_LIBERAL: "Leans liberal",
    J_CENTER: "No bias / Center",
    J_LEANS_CONSERVATIVE: "Leans conservative",
    J_CONSERVATIVE: "Conservative",
    J_NOT_ENOUGH_DATA: "Not enough data",
}

J_TIPS = {
    J_LIBERAL: "Estimated from the outlets this byline has appeared on, on this site — not a fact-check or personal characterization.",
    J_LEANS_LIBERAL: "Estimated from the outlets this byline has appeared on, on this site — not a fact-check or personal characterization.",
    J_CENTER: "This byline's outlets skew mixed/center or are evenly split — not a fact-check or personal characterization.",
    J_LEANS_CONSERVATIVE: "Estimated from the outlets this byline has appeared on, on this site — not a fact-check or personal characterization.",
    J_CONSERVATIVE: "Estimated from the outlets this byline has appeared on, on this site — not a fact-check or personal characterization.",
    J_NOT_ENOUGH_DATA: "We haven't seen enough articles under this byline yet to estimate anything.",
}

# Classification thresholds. This names a real person, so the bar for the
# strongest label is higher than the outlet-level system's looser rollup:
# 3 articles all at one outlet says more about who assigned the byline than
# about anyone's politics, so that pattern earns "Leans X," not "X".
MIN_COUNTABLE = 3
MIN_FOR_FULL_LABEL = 5
FULL_THRESHOLD = 0.75
LEAN_THRESHOLD = 0.50
CENTER_DOMINANT = 0.50

RATING_CHOICES = (J_LIBERAL, J_LEANS_LIBERAL, J_CENTER, J_LEANS_CONSERVATIVE, J_CONSERVATIVE)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def slug_for(name: str) -> str:
    from app.topics import slugify

    return slugify(name)


def normalize_author(raw: str | None) -> str | None:
    """Clean a raw byline candidate into a display name, or None if unusable."""
    s = (raw or "").strip()
    if not s:
        return None
    # RSS <author> per spec: "email@x.com (Real Name)" — extract the name, else reject.
    m = re.match(r"^\s*\S+@\S+\.\S+\s*\(([^)]+)\)\s*$", s)
    if m:
        s = m.group(1).strip()
    elif "@" in s and " " not in s.split("@")[0]:
        # Looks like a bare email with no parenthesized name.
        return None
    s = re.sub(r"^\s*by\s+", "", s, flags=re.IGNORECASE)
    s = re.sub(r"\s+", " ", s).strip(" .,-")
    if not s:
        return None
    low = s.lower()
    if low in _GENERIC_BYLINES:
        return None
    # Multi-byline strings are more likely to misattribute than help — skip them.
    if "," in s or re.search(r"\band\b|&", s, flags=re.IGNORECASE):
        return None
    if not (2 <= len(s) <= 80):
        return None
    # A name should look like words, not a URL/handle/sentence.
    if "/" in s or "http" in low or s.count(" ") > 5:
        return None
    return s


def _host(url: str) -> str:
    try:
        h = (urlparse(url or "").hostname or "").lower()
        return h[4:] if h.startswith("www.") else h
    except Exception:
        return ""


def _sanitize_slug(slug: str) -> str:
    return re.sub(r"[^\w\-]", "", (slug or "").lower())[:80] or "journalist"


def _profile_path(slug: str) -> Path:
    return JOURNALISTS_DIR / f"{_sanitize_slug(slug)}.json"


def _ratings_path(slug: str) -> Path:
    return JOURNALISTS_DIR / f"{_sanitize_slug(slug)}.ratings.json"


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as e:
        log.warning("load %s failed: %s", path, e)
        return default


def _save_json(path: Path, data: Any) -> None:
    JOURNALISTS_DIR.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def load_profile(slug: str) -> dict[str, Any] | None:
    data = _load_json(_profile_path(slug), None)
    if not isinstance(data, dict):
        return None
    return data


def record_sightings(hits: list[dict[str, Any]]) -> None:
    """
    Append a sighting for each hit that already has a clean author name.
    Each hit's own url/source carries its outlet — hits passed in may span
    multiple outlets (e.g. an already-merged headline list).
    """
    for h in hits or []:
        author = normalize_author(h.get("author"))
        if not author:
            continue
        url = (h.get("url") or "").strip()
        if not url:
            continue
        domain = _host(url)
        slug = slug_for(author)
        with _lock:
            path = _profile_path(slug)
            profile = _load_json(path, None)
            if not isinstance(profile, dict):
                profile = {"slug": slug, "name": author, "sightings": []}
            sightings = profile.get("sightings") or []
            if any(s.get("url") == url for s in sightings):
                continue
            sightings.append(
                {
                    "url": url,
                    "title": h.get("title") or "",
                    "domain": domain,
                    "source": h.get("source") or domain,
                    "published": h.get("published"),
                    "seen_at": _now_iso(),
                }
            )
            # Keep newest MAX_SIGHTINGS.
            profile["sightings"] = sightings[-MAX_SIGHTINGS:]
            profile["name"] = profile.get("name") or author
            profile["updated_at"] = _now_iso()
            _save_json(path, profile)


def queue_for_backfill(hits: list[dict[str, Any]]) -> None:
    """Queue hits with no author yet for the background byline-fetch pass."""
    candidates = [h for h in (hits or []) if not normalize_author(h.get("author"))]
    if not candidates:
        return
    with _lock:
        pending = _load_json(PENDING_FILE, [])
        if not isinstance(pending, list):
            pending = []
        known_urls = {p.get("url") for p in pending}
        for h in candidates:
            url = (h.get("url") or "").strip()
            if not url or url in known_urls:
                continue
            pending.append(
                {
                    "url": url,
                    "title": h.get("title") or "",
                    "domain": _host(url),
                    "source": h.get("source") or "",
                    "published": h.get("published"),
                    "attempts": 0,
                    "queued_at": _now_iso(),
                }
            )
            known_urls.add(url)
        # Cap total queue size, dropping oldest first.
        pending = pending[-MAX_PENDING:]
        _save_json(PENDING_FILE, pending)


async def run_backfill(limit: int = 25) -> dict[str, Any]:
    """
    Pop up to `limit` pending URLs, fetch each article page for a byline,
    and record sightings for whatever we find. Runs off the request path,
    called only from the loopback-only /internal/backfill-bylines cron hook.
    """
    from app import byline_fetch  # local import: avoids a network-lib dep at module load

    with _lock:
        pending = _load_json(PENDING_FILE, [])
        if not isinstance(pending, list):
            pending = []
        batch: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        per_domain: dict[str, int] = {}
        for entry in pending:
            domain = entry.get("domain") or ""
            if len(batch) < limit and per_domain.get(domain, 0) < 2:
                batch.append(entry)
                per_domain[domain] = per_domain.get(domain, 0) + 1
            else:
                remaining.append(entry)
        _save_json(PENDING_FILE, remaining)

    processed = 0
    found = 0
    requeue: list[dict[str, Any]] = []
    async with byline_fetch.make_client() as client:
        for entry in batch:
            processed += 1
            author = None
            try:
                author = await byline_fetch.fetch_byline(client, entry["url"])
            except Exception as e:
                log.debug("byline fetch failed %s: %s", entry.get("url"), e)
            if author:
                found += 1
                record_sightings(
                    [{
                        "url": entry["url"],
                        "title": entry.get("title"),
                        "author": author,
                        "source": entry.get("source") or "",
                        "published": entry.get("published"),
                    }]
                )
            else:
                attempts = int(entry.get("attempts") or 0) + 1
                if attempts < MAX_ATTEMPTS:
                    entry["attempts"] = attempts
                    requeue.append(entry)
            await asyncio.sleep(0.05)  # brief, polite pacing between sequential fetches

    if requeue:
        with _lock:
            pending = _load_json(PENDING_FILE, [])
            if not isinstance(pending, list):
                pending = []
            pending = requeue + pending
            pending = pending[-MAX_PENDING:]
            _save_json(PENDING_FILE, pending)

    with _lock:
        queued_remaining = len(_load_json(PENDING_FILE, []) or [])
    return {"processed": processed, "found": found, "queued_remaining": queued_remaining}


def estimate_lean(slug: str) -> dict[str, Any]:
    profile = load_profile(slug)
    sightings = (profile or {}).get("sightings") or []
    counts = {"left": 0, "right": 0, "center": 0, "unclear": 0}
    for s in sightings:
        b = lean_for(source=s.get("source") or "", url=f"https://{s.get('domain') or ''}")
        counts[b["lean"]] = counts.get(b["lean"], 0) + 1

    countable = counts["left"] + counts["right"] + counts["center"]
    total = countable + counts["unclear"]

    def _result(code: str) -> dict[str, Any]:
        return {
            "journalist_lean": code,
            "journalist_lean_label": J_LABELS[code],
            "journalist_lean_tip": J_TIPS[code],
            "journalist_lean_counts": counts,
            "journalist_lean_total": total,
        }

    if countable < MIN_COUNTABLE:
        return _result(J_NOT_ENOUGH_DATA)

    left_share = counts["left"] / countable
    right_share = counts["right"] / countable
    center_share = counts["center"] / countable

    if center_share >= CENTER_DOMINANT or (left_share < LEAN_THRESHOLD and right_share < LEAN_THRESHOLD):
        return _result(J_CENTER)

    if left_share == right_share:
        return _result(J_CENTER)

    if left_share > right_share:
        share = left_share
        full, lean = J_LIBERAL, J_LEANS_LIBERAL
    else:
        share = right_share
        full, lean = J_CONSERVATIVE, J_LEANS_CONSERVATIVE

    if countable >= MIN_FOR_FULL_LABEL and share >= FULL_THRESHOLD:
        return _result(full)
    if share >= LEAN_THRESHOLD:
        return _result(lean)
    return _result(J_CENTER)


def get_ratings(slug: str) -> dict[str, Any]:
    data = _load_json(_ratings_path(slug), None)
    if not isinstance(data, dict):
        data = {"counts": {k: 0 for k in RATING_CHOICES}, "total": 0}
    return data


def add_reader_rating(slug: str, choice: str, client_ip: str = "") -> tuple[bool, str]:
    if choice not in RATING_CHOICES:
        return False, "Pick one of the options."
    ip_hash = hashlib.sha256((client_ip or "unknown").encode()).hexdigest()[:16]
    key = (ip_hash, slug)
    now = time.time()
    with _lock:
        last = _last_rating.get(key, 0)
        if now - last < RATE_LIMIT_HOURS * 3600:
            return False, "You've already rated this journalist recently."
        _last_rating[key] = now

        path = _ratings_path(slug)
        data = _load_json(path, None)
        if not isinstance(data, dict):
            data = {"counts": {k: 0 for k in RATING_CHOICES}, "total": 0}
        counts = data.get("counts") or {}
        counts[choice] = int(counts.get(choice, 0)) + 1
        data["counts"] = counts
        data["total"] = int(data.get("total", 0)) + 1
        _save_json(path, data)
    return True, "Thanks for rating."


def build_journalist(slug: str) -> dict[str, Any]:
    from app.comments import list_comments
    from app.topics import unslug

    canonical = slug_for(unslug(slug) or slug)
    profile = load_profile(canonical)
    name = (profile or {}).get("name") or (unslug(canonical) or canonical).title()
    sightings = (profile or {}).get("sightings") or []
    sightings_sorted = sorted(sightings, key=lambda s: s.get("seen_at") or "", reverse=True)

    return {
        "slug": canonical,
        "name": name,
        "sightings": sightings_sorted[:20],
        "sighting_count": len(sightings),
        **estimate_lean(canonical),
        "reader_rating": get_ratings(canonical),
        "rating_choices": [
            {"key": k, "label": J_LABELS[k]} for k in RATING_CHOICES
        ],
        "comments": list_comments(f"jrn-{canonical}"),
        "disclaimer": (
            "Estimated from the outlets this byline has appeared on, on this site — "
            "not a fact-check, not a personal characterization, and not verified against "
            "any outside directory (including Muckrack or similar journalist databases). "
            "If this page appears to combine work by more than one person who shares this "
            "byline, please tell us so we can fix it."
        ),
    }
