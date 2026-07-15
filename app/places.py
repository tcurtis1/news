"""Supported places for geo-scoped Daily Intersection trends.

Phase 1: countries. Phase 2: US states (Google full; others fall back to country).
Free-text input is resolved only against this catalog (aliases included).
"""

from __future__ import annotations

import os
import re
from dataclasses import asdict, dataclass
from typing import Any

DEFAULT_GEO = os.environ.get("TRENDS_GEO", "US").strip().upper() or "US"

# trends24.in path segments (country pages only)
_X_PATHS: dict[str, str] = {
    "US": "united-states",
    "GB": "united-kingdom",
    "CA": "canada",
    "AU": "australia",
    "DE": "germany",
    "FR": "france",
    "IN": "india",
    "BR": "brazil",
    "JP": "japan",
    "MX": "mexico",
    "ES": "spain",
    "IT": "italy",
    "NL": "netherlands",
    "KR": "south-korea",
    "ID": "indonesia",
    "PH": "philippines",
    "TR": "turkey",
    "AR": "argentina",
    "ZA": "south-africa",
    "SE": "sweden",
    "PL": "poland",
    "IE": "ireland",
    "NZ": "new-zealand",
    "SG": "singapore",
    "AE": "united-arab-emirates",
    "SA": "saudi-arabia",
    "NG": "nigeria",
    "PK": "pakistan",
    "EG": "egypt",
    "CO": "colombia",
    "CL": "chile",
    "PT": "portugal",
    "BE": "belgium",
    "AT": "austria",
    "CH": "switzerland",
    "MY": "malaysia",
    "TH": "thailand",
    "VN": "vietnam",
    "TW": "taiwan",
    "HK": "hong-kong",
    "IL": "israel",
    "UA": "ukraine",
    "RU": "russia",
}

# Popular countries for the picker (order matters for UI)
_COUNTRY_META: list[tuple[str, str, str, str, str]] = [
    # code, label, bing_market, news_hl, news_ceid
    ("US", "United States", "en-US", "en-US", "US:en"),
    ("GB", "United Kingdom", "en-GB", "en-GB", "GB:en"),
    ("CA", "Canada", "en-CA", "en-CA", "CA:en"),
    ("AU", "Australia", "en-AU", "en-AU", "AU:en"),
    ("DE", "Germany", "de-DE", "de", "DE:de"),
    ("FR", "France", "fr-FR", "fr", "FR:fr"),
    ("IN", "India", "en-IN", "en-IN", "IN:en"),
    ("BR", "Brazil", "pt-BR", "pt-BR", "BR:pt-419"),
    ("JP", "Japan", "ja-JP", "ja", "JP:ja"),
    ("MX", "Mexico", "es-MX", "es-419", "MX:es-419"),
    ("ES", "Spain", "es-ES", "es", "ES:es"),
    ("IT", "Italy", "it-IT", "it", "IT:it"),
    ("NL", "Netherlands", "nl-NL", "nl", "NL:nl"),
    ("KR", "South Korea", "ko-KR", "ko", "KR:ko"),
    ("ID", "Indonesia", "id-ID", "id", "ID:id"),
    ("PH", "Philippines", "en-PH", "en", "PH:en"),
    ("TR", "Turkey", "tr-TR", "tr", "TR:tr"),
    ("AR", "Argentina", "es-AR", "es-419", "AR:es-419"),
    ("ZA", "South Africa", "en-ZA", "en", "ZA:en"),
    ("SE", "Sweden", "sv-SE", "sv", "SE:sv"),
    ("PL", "Poland", "pl-PL", "pl", "PL:pl"),
    ("IE", "Ireland", "en-IE", "en-IE", "IE:en"),
    ("NZ", "New Zealand", "en-NZ", "en-NZ", "NZ:en"),
    ("SG", "Singapore", "en-SG", "en-SG", "SG:en"),
    ("AE", "United Arab Emirates", "en-AE", "en", "AE:en"),
    ("SA", "Saudi Arabia", "ar-SA", "ar", "SA:ar"),
    ("NG", "Nigeria", "en-NG", "en", "NG:en"),
    ("PK", "Pakistan", "en-PK", "en", "PK:en"),
    ("EG", "Egypt", "ar-EG", "ar", "EG:ar"),
    ("CO", "Colombia", "es-CO", "es-419", "CO:es-419"),
    ("CL", "Chile", "es-CL", "es-419", "CL:es-419"),
    ("PT", "Portugal", "pt-PT", "pt-PT", "PT:pt-150"),
    ("BE", "Belgium", "fr-BE", "fr", "BE:fr"),
    ("AT", "Austria", "de-AT", "de", "AT:de"),
    ("CH", "Switzerland", "de-CH", "de", "CH:de"),
    ("MY", "Malaysia", "en-MY", "en", "MY:en"),
    ("TH", "Thailand", "th-TH", "th", "TH:th"),
    ("VN", "Vietnam", "vi-VN", "vi", "VN:vi"),
    ("TW", "Taiwan", "zh-TW", "zh-TW", "TW:zh-Hant"),
    ("HK", "Hong Kong", "zh-HK", "zh-HK", "HK:zh-Hant"),
    ("IL", "Israel", "he-IL", "he", "IL:he"),
    ("UA", "Ukraine", "uk-UA", "uk", "UA:uk"),
    ("RU", "Russia", "ru-RU", "ru", "RU:ru"),
]

_US_STATES: list[tuple[str, str]] = [
    ("AL", "Alabama"),
    ("AK", "Alaska"),
    ("AZ", "Arizona"),
    ("AR", "Arkansas"),
    ("CA", "California"),
    ("CO", "Colorado"),
    ("CT", "Connecticut"),
    ("DE", "Delaware"),
    ("FL", "Florida"),
    ("GA", "Georgia"),
    ("HI", "Hawaii"),
    ("ID", "Idaho"),
    ("IL", "Illinois"),
    ("IN", "Indiana"),
    ("IA", "Iowa"),
    ("KS", "Kansas"),
    ("KY", "Kentucky"),
    ("LA", "Louisiana"),
    ("ME", "Maine"),
    ("MD", "Maryland"),
    ("MA", "Massachusetts"),
    ("MI", "Michigan"),
    ("MN", "Minnesota"),
    ("MS", "Mississippi"),
    ("MO", "Missouri"),
    ("MT", "Montana"),
    ("NE", "Nebraska"),
    ("NV", "Nevada"),
    ("NH", "New Hampshire"),
    ("NJ", "New Jersey"),
    ("NM", "New Mexico"),
    ("NY", "New York"),
    ("NC", "North Carolina"),
    ("ND", "North Dakota"),
    ("OH", "Ohio"),
    ("OK", "Oklahoma"),
    ("OR", "Oregon"),
    ("PA", "Pennsylvania"),
    ("RI", "Rhode Island"),
    ("SC", "South Carolina"),
    ("SD", "South Dakota"),
    ("TN", "Tennessee"),
    ("TX", "Texas"),
    ("UT", "Utah"),
    ("VT", "Vermont"),
    ("VA", "Virginia"),
    ("WA", "Washington"),
    ("WV", "West Virginia"),
    ("WI", "Wisconsin"),
    ("WY", "Wyoming"),
    ("DC", "District of Columbia"),
]

# Extra free-text aliases → place code
_ALIASES: dict[str, str] = {
    "usa": "US",
    "u.s.": "US",
    "u.s.a.": "US",
    "united states of america": "US",
    "america": "US",
    "uk": "GB",
    "u.k.": "GB",
    "great britain": "GB",
    "britain": "GB",
    "england": "GB",
    "scotland": "GB",
    "wales": "GB",
    "uae": "AE",
    "korea": "KR",
    "south korea": "KR",
    "republic of korea": "KR",
    "holland": "NL",
    "brasil": "BR",
    "turkiye": "TR",
    "türkiye": "TR",
}


@dataclass(frozen=True)
class Place:
    code: str
    label: str
    kind: str  # country | region
    country: str
    google_geo: str
    youtube_gl: str
    tiktok_country: str
    x_path: str | None
    bing_market: str
    news_hl: str
    news_gl: str
    news_ceid: str

    def short_label(self) -> str:
        """Bare place name for queries: 'Utah' from 'Utah, United States'."""
        if self.kind == "region" and self.label.endswith(", United States"):
            return self.label[: -len(", United States")].strip()
        return self.label

    def platform_geo(self) -> dict[str, dict[str, Any]]:
        """Per-platform scope used for UI badges and honest coverage."""
        is_region = self.kind == "region"
        country_label = self.country
        local = self.short_label()
        return {
            "google": {
                "scope": "full",
                "code": self.google_geo,
                "label": self.label,
            },
            "youtube": {
                "scope": "country" if is_region else "full",
                "code": self.youtube_gl,
                "label": country_label if is_region else self.label,
            },
            "x": {
                "scope": ("country" if is_region else "full") if self.x_path else "none",
                "code": self.x_path or "",
                "label": country_label if is_region else self.label,
            },
            # Region: Creative Center is country-only; we bias with local news buzz.
            "tiktok": {
                "scope": "partial" if is_region else "full",
                "code": local if is_region else self.tiktok_country,
                "label": local if is_region else self.label,
            },
            # Region: Bing Popular is national; we lead with local Bing News RSS.
            "bing": {
                "scope": "partial" if is_region else "partial",
                "code": local if is_region else self.bing_market,
                "label": local if is_region else self.label,
            },
            "polymarket": {
                "scope": "global",
                "code": "global",
                "label": "Global",
            },
            "facebook": {
                "scope": "partial",
                "code": local if is_region else self.news_gl,
                "label": local if is_region else self.label,
            },
            "instagram": {
                "scope": "partial",
                "code": local if is_region else self.news_gl,
                "label": local if is_region else self.label,
            },
        }

    def coverage_summary(self) -> dict[str, Any]:
        pg = self.platform_geo()
        full = [k for k, v in pg.items() if v["scope"] == "full"]
        country = [k for k, v in pg.items() if v["scope"] == "country"]
        partial = [k for k, v in pg.items() if v["scope"] == "partial"]
        global_ = [k for k, v in pg.items() if v["scope"] == "global"]
        none = [k for k, v in pg.items() if v["scope"] == "none"]
        if self.kind == "region":
            note = (
                f"For {self.short_label()}: Google Trends is state-level. "
                "Bing, Facebook, Instagram, and TikTok boards use local news buzz "
                f"about {self.short_label()} (not official state charts — those platforms "
                "don’t publish free state Top 10s). YouTube and X stay at the U.S. country "
                "chart. Polymarket is always global."
            )
        else:
            note = (
                f"Boards for {self.label} where each platform publishes local charts. "
                "Bing/Facebook/Instagram are best-effort; Polymarket is always global."
            )
        return {
            "full": full,
            "country": country,
            "partial": partial,
            "global": global_,
            "none": none,
            "note": note,
        }

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["short_label"] = self.short_label()
        d["platform_geo"] = self.platform_geo()
        d["coverage"] = self.coverage_summary()
        return d


def _country_place(code: str, label: str, bing: str, hl: str, ceid: str) -> Place:
    code = code.upper()
    return Place(
        code=code,
        label=label,
        kind="country",
        country=code,
        google_geo=code,
        youtube_gl=code,
        tiktok_country=code,
        x_path=_X_PATHS.get(code),
        bing_market=bing,
        news_hl=hl,
        news_gl=code,
        news_ceid=ceid,
    )


def _state_place(st: str, name: str, us: Place) -> Place:
    st = st.upper()
    return Place(
        code=f"US-{st}",
        label=f"{name}, United States",
        kind="region",
        country="US",
        google_geo=f"US-{st}",
        youtube_gl="US",
        tiktok_country="US",
        x_path=us.x_path,
        bing_market=us.bing_market,
        news_hl=us.news_hl,
        news_gl=us.news_gl,
        news_ceid=us.news_ceid,
    )


def _build_catalog() -> dict[str, Place]:
    places: dict[str, Place] = {}
    for code, label, bing, hl, ceid in _COUNTRY_META:
        places[code] = _country_place(code, label, bing, hl, ceid)
    us = places["US"]
    for st, name in _US_STATES:
        p = _state_place(st, name, us)
        places[p.code] = p
    return places


PLACES: dict[str, Place] = _build_catalog()


def _norm_alias_key(s: str) -> str:
    t = (s or "").strip().lower()
    t = t.replace("’", "'")
    t = re.sub(r"[^\w\s.\-]", " ", t, flags=re.UNICODE)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def _build_alias_index() -> dict[str, str]:
    idx: dict[str, str] = dict(_ALIASES)
    for code, p in PLACES.items():
        idx[_norm_alias_key(code)] = code
        idx[_norm_alias_key(p.label)] = code
        # bare state name → US-XX
        if p.kind == "region" and p.label.endswith(", United States"):
            bare = p.label[: -len(", United States")].strip()
            idx[_norm_alias_key(bare)] = code
            # "utah, us" / "utah us"
            idx[_norm_alias_key(f"{bare} us")] = code
            idx[_norm_alias_key(f"{bare}, us")] = code
            idx[_norm_alias_key(f"{bare} usa")] = code
        # country short bits
        if p.kind == "country":
            # first word for multi-word (careful: avoid "new" alone)
            parts = p.label.split()
            if len(parts) == 1:
                idx[_norm_alias_key(parts[0])] = code
    # Prefer longer / more specific aliases already set; override ambiguous "georgia"
    # Georgia state vs country — state wins for bare "georgia" (US-centric product default)
    idx["georgia"] = "US-GA"
    # "new york" state
    idx["new york"] = "US-NY"
    idx["washington"] = "US-WA"  # state; DC is "district of columbia" / "dc"
    idx["dc"] = "US-DC"
    idx["d.c."] = "US-DC"
    idx["washington dc"] = "US-DC"
    idx["washington d.c."] = "US-DC"
    return idx


_ALIAS_INDEX: dict[str, str] = _build_alias_index()


def default_place() -> Place:
    return PLACES.get(DEFAULT_GEO) or PLACES["US"]


def resolve_place(raw: str | None) -> Place:
    """
    Resolve user/API geo input to a Place.
    Unknown → default (does not raise).
    """
    if raw is None or not str(raw).strip():
        return default_place()
    s = str(raw).strip()
    # Direct codes: US, GB, US-UT
    code = s.upper().replace(" ", "")
    if code in PLACES:
        return PLACES[code]
    # US-UT style with underscore
    code2 = code.replace("_", "-")
    if code2 in PLACES:
        return PLACES[code2]
    key = _norm_alias_key(s)
    if key in _ALIAS_INDEX:
        return PLACES[_ALIAS_INDEX[key]]
    # "US UT" / "UT USA"
    m = re.match(r"^(?:us[-\s]?)?([a-z]{2})(?:\s*,?\s*us(?:a)?)?$", key)
    if m:
        st = m.group(1).upper()
        cand = f"US-{st}"
        if cand in PLACES:
            return PLACES[cand]
    return default_place()


def list_places_for_ui() -> dict[str, Any]:
    """Payload for location picker."""
    countries = [
        {"code": c, "label": PLACES[c].label, "kind": "country"}
        for c, _, _, _, _ in _COUNTRY_META
        if c in PLACES
    ]
    states = [
        {"code": f"US-{st}", "label": name, "kind": "region"}
        for st, name in _US_STATES
        if f"US-{st}" in PLACES
    ]
    d = default_place()
    return {
        "default": d.code,
        "default_label": d.label,
        "countries": countries,
        "us_states": states,
    }


def cache_key(place: Place) -> str:
    """Filesystem-safe cache key."""
    return re.sub(r"[^A-Za-z0-9_-]", "_", place.code)
