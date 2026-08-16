"""
Outlet leaning badges for Yoyosup News.

Honest, transparent labeling — NOT a truth score or endorsement.
v1 uses a curated map of well-known outlets (common US coverage patterns).
Unknown sources → lean=unclear.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

# lean codes used in CSS + API
LEAN_LEFT = "left"
LEAN_RIGHT = "right"
LEAN_CENTER = "center"
LEAN_UNCLEAR = "unclear"

LABELS = {
    LEAN_LEFT: "Lean left",
    LEAN_RIGHT: "Lean right",
    LEAN_CENTER: "Mixed / center",
    LEAN_UNCLEAR: "Unclear",
}

TIPS = {
    LEAN_LEFT: "Outlet often covers politics with a liberal-leaning framing in public media-bias surveys. Not a fact-check of this story.",
    LEAN_RIGHT: "Outlet often covers politics with a conservative-leaning framing in public media-bias surveys. Not a fact-check of this story.",
    LEAN_CENTER: "Outlet often aims for mixed/center coverage in public media-bias surveys. Still verify claims yourself.",
    LEAN_UNCLEAR: "We don’t have a reliable outlet label for this source yet.",
}

# domain suffix or exact source name fragment → lean
# Keep conservative: only high-recognition outlets; prefer under-labeling.
_DOMAIN_LEAN: dict[str, str] = {
    # Left-leaning (common survey placements)
    "nytimes.com": LEAN_LEFT,
    "washingtonpost.com": LEAN_LEFT,
    "theguardian.com": LEAN_LEFT,
    "cnn.com": LEAN_LEFT,
    "msnbc.com": LEAN_LEFT,
    "huffpost.com": LEAN_LEFT,
    "huffingtonpost.com": LEAN_LEFT,
    "vox.com": LEAN_LEFT,
    "slate.com": LEAN_LEFT,
    "motherjones.com": LEAN_LEFT,
    "theatlantic.com": LEAN_LEFT,
    "newyorker.com": LEAN_LEFT,
    "politico.com": LEAN_LEFT,
    "nbcnews.com": LEAN_LEFT,
    "abcnews.go.com": LEAN_LEFT,
    "cbsnews.com": LEAN_LEFT,
    "npr.org": LEAN_LEFT,
    "pbs.org": LEAN_LEFT,
    "propublica.org": LEAN_LEFT,
    "axios.com": LEAN_LEFT,
    "time.com": LEAN_LEFT,
    "latimes.com": LEAN_LEFT,
    "theintercept.com": LEAN_LEFT,
    "dailybeast.com": LEAN_LEFT,
    "rawstory.com": LEAN_LEFT,
    "salon.com": LEAN_LEFT,
    "buzzfeednews.com": LEAN_LEFT,
    "msn.com": LEAN_CENTER,
    # Right-leaning
    "foxnews.com": LEAN_RIGHT,
    "nypost.com": LEAN_RIGHT,
    "wsj.com": LEAN_CENTER,  # news often center-right; treat mixed
    "nationalreview.com": LEAN_RIGHT,
    "thefederalist.com": LEAN_RIGHT,
    "breitbart.com": LEAN_RIGHT,
    "dailywire.com": LEAN_RIGHT,
    "dailycaller.com": LEAN_RIGHT,
    "theblaze.com": LEAN_RIGHT,
    "oann.com": LEAN_RIGHT,
    "newsmax.com": LEAN_RIGHT,
    "washingtontimes.com": LEAN_RIGHT,
    "theepochtimes.com": LEAN_RIGHT,
    "townhall.com": LEAN_RIGHT,
    "reason.com": LEAN_RIGHT,  # libertarian-right spectrum
    "freebeacon.com": LEAN_RIGHT,
    "spectator.org": LEAN_RIGHT,
    "foxbusiness.com": LEAN_RIGHT,
    # Center / wire / business
    "reuters.com": LEAN_CENTER,
    "apnews.com": LEAN_CENTER,
    "associatedpress.com": LEAN_CENTER,
    "bbc.com": LEAN_CENTER,
    "bbc.co.uk": LEAN_CENTER,
    "bloomberg.com": LEAN_CENTER,
    "ft.com": LEAN_CENTER,
    "economist.com": LEAN_CENTER,
    "usatoday.com": LEAN_CENTER,
    "csmonitor.com": LEAN_CENTER,
    "thehill.com": LEAN_CENTER,
    "newsweek.com": LEAN_CENTER,
    "cnbc.com": LEAN_CENTER,
    "marketwatch.com": LEAN_CENTER,
    "forbes.com": LEAN_CENTER,
    "businessinsider.com": LEAN_CENTER,
    "techcrunch.com": LEAN_CENTER,
    "theverge.com": LEAN_CENTER,
    "wired.com": LEAN_CENTER,
    "arstechnica.com": LEAN_CENTER,
    "nature.com": LEAN_CENTER,
    "sciencemag.org": LEAN_CENTER,
    "aljazeera.com": LEAN_CENTER,
    "dw.com": LEAN_CENTER,
    "npr.org": LEAN_LEFT,
}

# Google News source name → lean when domain is googlenews / redirect
_NAME_LEAN: dict[str, str] = {
    "associated press": LEAN_CENTER,
    "ap": LEAN_CENTER,
    "reuters": LEAN_CENTER,
    "bbc": LEAN_CENTER,
    "bbc news": LEAN_CENTER,
    "the new york times": LEAN_LEFT,
    "new york times": LEAN_LEFT,
    "nytimes": LEAN_LEFT,
    "washington post": LEAN_LEFT,
    "the washington post": LEAN_LEFT,
    "the guardian": LEAN_LEFT,
    "cnn": LEAN_LEFT,
    "msnbc": LEAN_LEFT,
    "npr": LEAN_LEFT,
    "fox news": LEAN_RIGHT,
    "new york post": LEAN_RIGHT,
    "wall street journal": LEAN_CENTER,
    "the wall street journal": LEAN_CENTER,
    "politico": LEAN_LEFT,
    "the hill": LEAN_CENTER,
    "usa today": LEAN_CENTER,
    "bloomberg": LEAN_CENTER,
    "abc news": LEAN_LEFT,
    "nbc news": LEAN_LEFT,
    "cbs news": LEAN_LEFT,
    "huffpost": LEAN_LEFT,
    "breitbart": LEAN_RIGHT,
    "daily wire": LEAN_RIGHT,
    "newsmax": LEAN_RIGHT,
    "the atlantic": LEAN_LEFT,
    "national review": LEAN_RIGHT,
    "axios": LEAN_LEFT,
    "the daily beast": LEAN_LEFT,
}


def _host(url: str) -> str:
    try:
        h = (urlparse(url or "").hostname or "").lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


def _match_domain(host: str) -> str | None:
    if not host:
        return None
    if host in _DOMAIN_LEAN:
        return _DOMAIN_LEAN[host]
    # suffix match (e.g. edition.cnn.com)
    for dom, lean in _DOMAIN_LEAN.items():
        if host == dom or host.endswith("." + dom):
            return lean
    return None


def _match_name(source: str) -> str | None:
    s = re.sub(r"\s+", " ", (source or "").lower()).strip()
    # "Google News · Reuters" → try after bullet
    if "·" in s:
        s = s.split("·")[-1].strip()
    if s in _NAME_LEAN:
        return _NAME_LEAN[s]
    for name, lean in _NAME_LEAN.items():
        if name in s or s in name:
            return lean
    return None


def lean_for(source: str = "", url: str = "") -> dict[str, str]:
    """Return lean badge fields for a news hit."""
    lean = _match_domain(_host(url)) or _match_name(source) or LEAN_UNCLEAR
    # Aggregators / social rarely map to left/right outlet framing
    host = _host(url)
    src_l = (source or "").lower()
    if any(
        x in host or x in src_l
        for x in (
            "reddit.com",
            "ycombinator.com",
            "news.google.com",
            "bing.com",
            "youtube.com",
            "twitter.com",
            "x.com",
            "tiktok.com",
            "facebook.com",
            "instagram.com",
            "polymarket.com",
        )
    ):
        if lean == LEAN_UNCLEAR or "reddit" in src_l or "hacker news" in src_l:
            lean = LEAN_UNCLEAR
    return {
        "lean": lean,
        "lean_label": LABELS[lean],
        "lean_tip": TIPS[lean],
    }


def enrich_hit(hit: dict[str, Any]) -> dict[str, Any]:
    b = lean_for(hit.get("source") or "", hit.get("url") or "")
    out = dict(hit)
    out.update(b)
    return out


def enrich_hits(hits: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [enrich_hit(h) for h in (hits or [])]


# Blindspot categories
BLINDSPOT_LEFT = "left_blindspot"
BLINDSPOT_RIGHT = "right_blindspot"
BLINDSPOT_BALANCED = "balanced"
BLINDSPOT_NONE = "none"


def calculate_blindspot(
    conservative_count: int, liberal_count: int, balanced_count: int = 0
) -> dict[str, Any]:
    """
    Calculate blindspot detection comparing coverage distribution across
    conservative, liberal, and balanced sources.

    Returns:
        {
            "category": "left_blindspot" | "right_blindspot" | "balanced" | "none",
            "skew_pct": float (0.0 to 100.0),
            "description": str,
            "has_blindspot": bool,
            "dominant_side": "conservative" | "liberal" | "balanced" | "none",
            "flip_to": "conservative" | "liberal" | None,
            "conservative_count": int,
            "liberal_count": int,
            "balanced_count": int,
            "total": int,
        }
    """
    try:
        conservative = max(0, int(conservative_count or 0))
    except (TypeError, ValueError):
        conservative = 0
    try:
        liberal = max(0, int(liberal_count or 0))
    except (TypeError, ValueError):
        liberal = 0
    try:
        balanced = max(0, int(balanced_count or 0))
    except (TypeError, ValueError):
        balanced = 0

    total = conservative + liberal + balanced
    partisan_total = conservative + liberal

    if total == 0:
        return {
            "category": BLINDSPOT_NONE,
            "skew_pct": 0.0,
            "description": "Not enough labeled sources to detect coverage blindspots.",
            "has_blindspot": False,
            "dominant_side": "none",
            "flip_to": None,
            "conservative_count": 0,
            "liberal_count": 0,
            "balanced_count": 0,
            "total": 0,
        }

    # Only center/balanced sources
    if partisan_total == 0 and balanced > 0:
        return {
            "category": BLINDSPOT_BALANCED,
            "skew_pct": 0.0,
            "description": f"Coverage is from {balanced} center/balanced source(s) with no partisan skew.",
            "has_blindspot": False,
            "dominant_side": "balanced",
            "flip_to": None,
            "conservative_count": 0,
            "liberal_count": 0,
            "balanced_count": balanced,
            "total": total,
        }

    con_pct = (conservative / total) * 100.0
    lib_pct = (liberal / total) * 100.0
    con_partisan_pct = (conservative / partisan_total) * 100.0 if partisan_total > 0 else 0.0
    lib_partisan_pct = (liberal / partisan_total) * 100.0 if partisan_total > 0 else 0.0

    # Blindspot condition: >= 70% one-sided coverage
    if (con_pct >= 70.0 or (con_partisan_pct >= 70.0 and conservative >= 2 and con_pct >= 50.0)) and conservative > liberal:
        skew_pct = round(con_pct if con_pct >= 70.0 else con_partisan_pct, 1)
        desc = (
            f"Left Blindspot: {skew_pct:.0f}% of coverage is from right-leaning / conservative sources. "
            "Liberal readers may not see this story in their regular news diet."
        )
        return {
            "category": BLINDSPOT_LEFT,
            "skew_pct": skew_pct,
            "description": desc,
            "has_blindspot": True,
            "dominant_side": "conservative",
            "flip_to": "liberal",
            "conservative_count": conservative,
            "liberal_count": liberal,
            "balanced_count": balanced,
            "total": total,
        }
    elif (lib_pct >= 70.0 or (lib_partisan_pct >= 70.0 and liberal >= 2 and lib_pct >= 50.0)) and liberal > conservative:
        skew_pct = round(lib_pct if lib_pct >= 70.0 else lib_partisan_pct, 1)
        desc = (
            f"Right Blindspot: {skew_pct:.0f}% of coverage is from left-leaning / liberal sources. "
            "Conservative readers may not see this story in their regular news diet."
        )
        return {
            "category": BLINDSPOT_RIGHT,
            "skew_pct": skew_pct,
            "description": desc,
            "has_blindspot": True,
            "dominant_side": "liberal",
            "flip_to": "conservative",
            "conservative_count": conservative,
            "liberal_count": liberal,
            "balanced_count": balanced,
            "total": total,
        }
    else:
        max_pct = round(max(con_pct, lib_pct, (balanced / total) * 100.0), 1)
        desc = (
            f"Balanced coverage: Story is covered across viewpoints "
            f"({conservative} conservative, {liberal} liberal, {balanced} center)."
        )
        return {
            "category": BLINDSPOT_BALANCED,
            "skew_pct": max_pct,
            "description": desc,
            "has_blindspot": False,
            "dominant_side": "balanced",
            "flip_to": None,
            "conservative_count": conservative,
            "liberal_count": liberal,
            "balanced_count": balanced,
            "total": total,
        }


def aggregate_lean(hits: list[dict[str, Any]] | None) -> dict[str, Any]:
    """
    Topic / query-level badge from known outlet labels among hits.
    Majority of left/right/center; ties or empty → unclear/mixed.
    """
    counts = {LEAN_LEFT: 0, LEAN_RIGHT: 0, LEAN_CENTER: 0}
    for h in hits or []:
        lean = h.get("lean") or lean_for(h.get("source") or "", h.get("url") or "").get(
            "lean"
        )
        if lean in counts:
            counts[lean] += 1
    total_known = sum(counts.values())
    blindspot = calculate_blindspot(
        conservative_count=counts[LEAN_RIGHT],
        liberal_count=counts[LEAN_LEFT],
        balanced_count=counts[LEAN_CENTER],
    )
    if total_known == 0:
        return {
            "lean": LEAN_UNCLEAR,
            "lean_label": LABELS[LEAN_UNCLEAR],
            "lean_tip": (
                "Not enough labeled outlets in today’s hits to estimate coverage lean."
            ),
            "lean_counts": counts,
            "lean_sample": 0,
            "blindspot": blindspot,
        }

    # If both sides strong → mixed
    if counts[LEAN_LEFT] >= 2 and counts[LEAN_RIGHT] >= 2:
        return {
            "lean": LEAN_CENTER,
            "lean_label": "Mixed coverage",
            "lean_tip": (
                f"Labeled outlets on this topic: left {counts[LEAN_LEFT]}, "
                f"right {counts[LEAN_RIGHT]}, center {counts[LEAN_CENTER]}. "
                "Coverage looks mixed — not a single lean."
            ),
            "lean_counts": counts,
            "lean_sample": total_known,
            "blindspot": blindspot,
        }

    winner = max(counts, key=lambda k: counts[k])
    if counts[winner] == 0:
        winner = LEAN_UNCLEAR
    # Close race between left and right
    sorted_counts = sorted(counts.values(), reverse=True)
    if (
        sorted_counts[0] == sorted_counts[1]
        and sorted_counts[0] > 0
        and winner in (LEAN_LEFT, LEAN_RIGHT)
    ):
        return {
            "lean": LEAN_CENTER,
            "lean_label": "Mixed coverage",
            "lean_tip": (
                f"Labeled outlets: left {counts[LEAN_LEFT]}, right {counts[LEAN_RIGHT]}, "
                f"center {counts[LEAN_CENTER]}. Split lean — treat as mixed."
            ),
            "lean_counts": counts,
            "lean_sample": total_known,
            "blindspot": blindspot,
        }

    tip = TIPS.get(winner, TIPS[LEAN_UNCLEAR])
    tip = (
        f"Based on {total_known} labeled outlet(s) in news hits "
        f"(L{counts[LEAN_LEFT]}/C{counts[LEAN_CENTER]}/R{counts[LEAN_RIGHT]}). {tip}"
    )
    return {
        "lean": winner,
        "lean_label": LABELS[winner],
        "lean_tip": tip,
        "lean_counts": counts,
        "lean_sample": total_known,
        "blindspot": blindspot,
    }
