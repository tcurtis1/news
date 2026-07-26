"""
Source lean preference — Conservative / Balanced / Liberal.

Populates news hits from outlet allow-lists (not a truth score).

Conservative domains curated from Feedspot’s public list:
  https://news.feedspot.com/conservative_news_websites/

Liberal domains curated from Feedspot’s liberal political list:
  https://bloggers.feedspot.com/liberal_political_blogs/
  plus well-known progressive news outlets that appear on that class of lists.

Balanced = wire/center outlets + a light mix of both sides so readers still
see competing coverage without a pure free-for-all.

Preference is saved client-side (localStorage) and as a cookie for SSR,
same pattern as geo. No account required.
"""

from __future__ import annotations

import re
from typing import Any, Iterable
from urllib.parse import urlparse

PREF_CONSERVATIVE = "conservative"
PREF_BALANCED = "balanced"
PREF_LIBERAL = "liberal"
PREF_DEFAULT = PREF_BALANCED

VALID_PREFS = (PREF_CONSERVATIVE, PREF_BALANCED, PREF_LIBERAL)

LABELS = {
    PREF_CONSERVATIVE: "Conservative",
    PREF_BALANCED: "Balanced",
    PREF_LIBERAL: "Liberal",
}

TIPS = {
    PREF_CONSERVATIVE: (
        "Headlines prefer outlets on Feedspot’s conservative news list "
        "(Fox, WSJ, NY Post, Newsmax, Breitbart, Daily Wire, and peers). "
        "Not an endorsement — switch anytime."
    ),
    PREF_BALANCED: (
        "Headlines prefer wire/center outlets (Reuters, AP, BBC, Bloomberg, "
        "The Hill, …) plus a light mix of left- and right-leaning sources."
    ),
    PREF_LIBERAL: (
        "Headlines prefer progressive / liberal outlets (Mother Jones, Vox, "
        "MSNBC, CNN Politics, The Nation, Intercept, and peers from liberal "
        "political lists). Not an endorsement — switch anytime."
    ),
}

# Attribution (shown in UI / API meta)
SOURCE_LIST_URLS = {
    PREF_CONSERVATIVE: "https://news.feedspot.com/conservative_news_websites/",
    PREF_LIBERAL: "https://bloggers.feedspot.com/liberal_political_blogs/",
    PREF_BALANCED: "https://news.feedspot.com/unbiased_news_websites/",
}

# ── Domain allow-lists (lowercase, no www.) ───────────────────────────
# Feedspot Top Conservative News Websites (scraped 2026-07)
CONSERVATIVE_DOMAINS: frozenset[str] = frozenset(
    {
        "foxnews.com",
        "wsj.com",
        "nypost.com",
        "newsmax.com",
        "breitbart.com",
        "hannity.com",
        "voanews.com",
        "dailywire.com",
        "judicialwatch.org",
        "oann.com",
        "heritage.org",
        "theepochtimes.com",
        "theblaze.com",
        "dailycaller.com",
        "nationalreview.com",
        "thegatewaypundit.com",
        "washingtonexaminer.com",
        "townhall.com",
        "charliekirk.com",
        "theothermccain.com",
        "csmonitor.com",
        "thefederalist.com",
        "newsbusters.org",
        "hughhewitt.com",
        "americanthinker.com",
        "dennisprager.com",
        "frontpagemag.com",
        "theamericanconservative.com",
        "firstthings.com",
        "dailysignal.com",
        "freebeacon.com",
        "twitchy.com",
        "lifesitenews.com",
        "mrctv.org",
        "spectator.org",
        "washingtontimes.com",
        "pjmedia.com",
        "thenewamerican.com",
        "bizpacreview.com",
        "hotair.com",
        "powerlineblog.com",
        "bonginoreport.com",
        "canadafreepress.com",
        "commentary.org",
        "lifezette.com",
        "wnd.com",
        "thehill.com",
        "cbn.com",
        "patriotpost.us",
        "redstate.com",
        "politico.com",
        "theconservativetreehouse.com",
        "conservativereview.com",
        "westernjournal.com",
        "afn.net",
        "newscats.org",
        "thetexashorn.com",
        "cnav.news",
        "foxbusiness.com",
        "reason.com",
    }
)

# Display / source-name fragments for Google News labels (no URL yet)
CONSERVATIVE_NAMES: frozenset[str] = frozenset(
    {
        "fox news",
        "foxnews",
        "wall street journal",
        "new york post",
        "newsmax",
        "breitbart",
        "daily wire",
        "the daily wire",
        "oann",
        "one america news",
        "epoch times",
        "the blaze",
        "daily caller",
        "national review",
        "gateway pundit",
        "washington examiner",
        "townhall",
        "the federalist",
        "american thinker",
        "daily signal",
        "free beacon",
        "washington times",
        "pj media",
        "hot air",
        "redstate",
        "western journal",
        "sean hannity",
        "heritage foundation",
    }
)

# Feedspot liberal political blogs + progressive news outlets
LIBERAL_DOMAINS: frozenset[str] = frozenset(
    {
        "motherjones.com",
        "commondreams.org",
        "vox.com",
        "dailykos.com",
        "thenation.com",
        "propublica.org",
        "democracynow.org",
        "theintercept.com",
        "prospect.org",
        "truthout.org",
        "talkingpointsmemo.com",
        "19thnews.org",
        "americanprogress.org",
        "democracyjournal.org",
        "slate.com",
        "theatlantic.com",
        "newyorker.com",
        "time.com",
        "axios.com",
        "msnbc.com",
        "cnn.com",
        "edition.cnn.com",
        "vanityfair.com",
        "thedailybeast.com",
        "huffpost.com",
        "huffingtonpost.com",
        "salon.com",
        "rawstory.com",
        "crooksandliars.com",
        "fair.org",
        "mediamatters.org",
        "gregpalast.com",
        "popular.info",
        "whowhatwhy.org",
        "therealnews.com",
        "counterpunch.org",
        "newstatesman.com",
        "naacp.org",
        "aclu.org",
        "moveon.org",
        "front.moveon.org",
        "dataforprogress.org",
        "inequality.org",
        "freepress.net",
        "peoplespolicyproject.org",
        "nationofchange.org",
        "alternet.org",
        "truthdig.com",
        "nakedcapitalism.com",
        "emptywheel.net",
        "esquire.com",
        "newrepublic.com",
        "progressive.org",
        "lincolnproject.us",
        "novaramedia.com",
        "leftfootforward.org",
        "lawyersgunsmoneyblog.com",
        "balloon-juice.com",
        "nytimes.com",
        "washingtonpost.com",
        "theguardian.com",
        "nbcnews.com",
        "abcnews.go.com",
        "cbsnews.com",
        "npr.org",
        "pbs.org",
        "latimes.com",
        "politico.com",
        "buzzfeednews.com",
        "jacobin.com",
        "currentaffairs.org",
    }
)

LIBERAL_NAMES: frozenset[str] = frozenset(
    {
        "mother jones",
        "common dreams",
        "vox",
        "daily kos",
        "the nation",
        "propublica",
        "democracy now",
        "the intercept",
        "truthout",
        "talking points memo",
        "msnbc",
        "cnn",
        "huffpost",
        "huffington post",
        "salon",
        "raw story",
        "the daily beast",
        "new york times",
        "nytimes",
        "washington post",
        "the guardian",
        "npr",
        "pbs",
        "the atlantic",
        "new yorker",
        "slate",
        "axios",
        "politico",
        "nbc news",
        "abc news",
        "cbs news",
        "new republic",
        "jacobin",
    }
)

# Wire / center + light bipartisan mix for "Balanced"
CENTER_DOMAINS: frozenset[str] = frozenset(
    {
        "reuters.com",
        "apnews.com",
        "associatedpress.com",
        "bbc.com",
        "bbc.co.uk",
        "bloomberg.com",
        "ft.com",
        "economist.com",
        "usatoday.com",
        "csmonitor.com",
        "thehill.com",
        "newsweek.com",
        "cnbc.com",
        "marketwatch.com",
        "forbes.com",
        "businessinsider.com",
        "aljazeera.com",
        "dw.com",
        "c-span.org",
        "pewresearch.org",
        "newsnationnow.com",
        "allsides.com",
        "axios.com",
        "wsj.com",
        "npr.org",
        "pbs.org",
        "voanews.com",
    }
)

# Balanced allow-list = center + flagship left + flagship right
BALANCED_DOMAINS: frozenset[str] = CENTER_DOMAINS | frozenset(
    {
        # right flagships
        "foxnews.com",
        "nypost.com",
        "washingtonexaminer.com",
        "nationalreview.com",
        "washingtontimes.com",
        # left flagships
        "nytimes.com",
        "washingtonpost.com",
        "theguardian.com",
        "cnn.com",
        "edition.cnn.com",
        "nbcnews.com",
        "abcnews.go.com",
        "cbsnews.com",
        "theatlantic.com",
        "politico.com",
    }
)

BALANCED_NAMES: frozenset[str] = frozenset(
    {
        "reuters",
        "associated press",
        "ap",
        "bbc",
        "bbc news",
        "bloomberg",
        "the hill",
        "usa today",
        "wall street journal",
        "christian science monitor",
        "news nation",
        "newsnation",
        "npr",
        "pbs",
        "fox news",
        "new york times",
        "washington post",
        "cnn",
        "politico",
        "the guardian",
    }
)

CENTER_NAMES: frozenset[str] = frozenset(
    {
        "reuters",
        "associated press",
        "ap",
        "bbc",
        "bbc news",
        "bloomberg",
        "the hill",
        "usa today",
        "financial times",
        "the economist",
        "christian science monitor",
        "news nation",
        "newsnation",
        "c-span",
        "al jazeera",
        "deutsche welle",
    }
)

# Domains used in Google News site: OR batches (highest-signal first)
_FETCH_PRIORITY: dict[str, list[str]] = {
    PREF_CONSERVATIVE: [
        "foxnews.com",
        "wsj.com",
        "nypost.com",
        "newsmax.com",
        "breitbart.com",
        "dailywire.com",
        "dailycaller.com",
        "nationalreview.com",
        "washingtonexaminer.com",
        "washingtontimes.com",
        "theblaze.com",
        "theepochtimes.com",
        "thefederalist.com",
        "freebeacon.com",
        "townhall.com",
        "thegatewaypundit.com",
        "oann.com",
        "foxbusiness.com",
        "hotair.com",
        "redstate.com",
        "westernjournal.com",
        "dailysignal.com",
        "thehill.com",
        "politico.com",
    ],
    PREF_LIBERAL: [
        "nytimes.com",
        "washingtonpost.com",
        "cnn.com",
        "msnbc.com",
        "npr.org",
        "theguardian.com",
        "vox.com",
        "motherjones.com",
        "theatlantic.com",
        "huffpost.com",
        "politico.com",
        "nbcnews.com",
        "theintercept.com",
        "thenation.com",
        "dailykos.com",
        "propublica.org",
        "slate.com",
        "thedailybeast.com",
        "axios.com",
        "newyorker.com",
        "salon.com",
        "rawstory.com",
        "commondreams.org",
        "democracynow.org",
    ],
    PREF_BALANCED: [
        "reuters.com",
        "apnews.com",
        "bbc.com",
        "bloomberg.com",
        "thehill.com",
        "wsj.com",
        "usatoday.com",
        "npr.org",
        "pbs.org",
        "csmonitor.com",
        "newsnationnow.com",
        "axios.com",
        "nytimes.com",
        "washingtonpost.com",
        "foxnews.com",
        "cnn.com",
        "nypost.com",
        "politico.com",
        "theguardian.com",
        "nbcnews.com",
        "aljazeera.com",
        "economist.com",
    ],
}


def normalize_pref(raw: str | None) -> str:
    """Map user/cookie/query values to a valid pref (default balanced)."""
    s = (raw or "").strip().lower()
    aliases = {
        "conservative": PREF_CONSERVATIVE,
        "right": PREF_CONSERVATIVE,
        "right-leaning": PREF_CONSERVATIVE,
        "c": PREF_CONSERVATIVE,
        "balanced": PREF_BALANCED,
        "center": PREF_BALANCED,
        "centre": PREF_BALANCED,
        "mixed": PREF_BALANCED,
        "neutral": PREF_BALANCED,
        "b": PREF_BALANCED,
        "liberal": PREF_LIBERAL,
        "progressive": PREF_LIBERAL,
        "left": PREF_LIBERAL,
        "left-leaning": PREF_LIBERAL,
        "l": PREF_LIBERAL,
    }
    return aliases.get(s, PREF_DEFAULT)


def pref_meta(pref: str | None) -> dict[str, Any]:
    p = normalize_pref(pref)
    return {
        "lean_pref": p,
        "lean_pref_label": LABELS[p],
        "lean_pref_tip": TIPS[p],
        "lean_pref_source_list": SOURCE_LIST_URLS.get(p),
        "lean_pref_options": [
            {"id": k, "label": LABELS[k], "tip": TIPS[k]} for k in VALID_PREFS
        ],
    }


def domains_for(pref: str | None) -> frozenset[str]:
    p = normalize_pref(pref)
    if p == PREF_CONSERVATIVE:
        return CONSERVATIVE_DOMAINS
    if p == PREF_LIBERAL:
        return LIBERAL_DOMAINS
    return BALANCED_DOMAINS


def names_for(pref: str | None) -> frozenset[str]:
    p = normalize_pref(pref)
    if p == PREF_CONSERVATIVE:
        return CONSERVATIVE_NAMES
    if p == PREF_LIBERAL:
        return LIBERAL_NAMES
    return BALANCED_NAMES | CENTER_NAMES


def fetch_domains_for(pref: str | None, limit: int = 24) -> list[str]:
    """Ordered domains for Google News site: queries."""
    p = normalize_pref(pref)
    return list(_FETCH_PRIORITY.get(p, _FETCH_PRIORITY[PREF_BALANCED])[:limit])


def _host(url: str) -> str:
    try:
        h = (urlparse(url or "").hostname or "").lower()
        if h.startswith("www."):
            h = h[4:]
        return h
    except Exception:
        return ""


def domain_matches(host: str, domains: Iterable[str]) -> bool:
    if not host:
        return False
    host = host.lower().removeprefix("www.")
    for dom in domains:
        d = dom.lower().removeprefix("www.")
        if host == d or host.endswith("." + d):
            return True
    return False


def name_matches(source: str, names: Iterable[str]) -> bool:
    s = re.sub(r"\s+", " ", (source or "").lower()).strip()
    if "·" in s:
        s = s.split("·")[-1].strip()
    if not s:
        return False
    for name in names:
        n = name.lower()
        if n == s or n in s or s in n:
            return True
    return False


def hit_matches_pref(
    source: str = "",
    url: str = "",
    pref: str | None = None,
) -> bool:
    """True if this hit belongs on the preferred source list."""
    domains = domains_for(pref)
    names = names_for(pref)
    host = _host(url)
    if domain_matches(host, domains):
        return True
    if name_matches(source, names):
        return True
    return False


def filter_hits(
    hits: list[dict[str, Any]] | None,
    pref: str | None,
    *,
    keep_unmatched: bool = False,
) -> list[dict[str, Any]]:
    """
    Prefer preferred-source hits.
    If keep_unmatched, append non-matches after matches (rare fallback).
    """
    if not hits:
        return []
    p = normalize_pref(pref)
    matched: list[dict[str, Any]] = []
    other: list[dict[str, Any]] = []
    for h in hits:
        ok = hit_matches_pref(h.get("source") or "", h.get("url") or "", p)
        out = dict(h)
        out["source_pref_match"] = ok
        if ok:
            # Slight score boost so preferred stay on top after re-sort
            try:
                out["score"] = int(out.get("score") or 0) + 200
            except (TypeError, ValueError):
                pass
            matched.append(out)
        else:
            other.append(out)
    if keep_unmatched and len(matched) < 4:
        return matched + other
    return matched if matched else other  # never return empty if we had hits


def _topic_for_google(topic: str) -> str:
    """
    Shape the free-text topic for Google News.
    Quote short / multi-word phrases so 'Fed' doesn't match 'Fed-up'.
    """
    topic = re.sub(r"\s+", " ", (topic or "").strip())[:120]
    if not topic:
        return ""
    # Already has operators — leave alone
    if re.search(r'\b(site:|when:|OR|AND)\b', topic) or '"' in topic:
        return topic
    # Single short token or multi-word phrase → quote for tighter match
    if " " in topic or len(topic) <= 12:
        return f'"{topic}"'
    return topic


def site_query_batches(
    pref: str | None,
    topic: str = "",
    *,
    batch_size: int = 8,
    max_batches: int = 3,
) -> list[str]:
    """
    Build Google News RSS queries: optional topic + (site:a OR site:b …).
    Split into batches so URLs stay reasonable.
    """
    domains = fetch_domains_for(pref, limit=batch_size * max_batches)
    topic_q = _topic_for_google(topic)
    batches: list[str] = []
    for i in range(0, len(domains), batch_size):
        chunk = domains[i : i + batch_size]
        if not chunk:
            break
        site_clause = " OR ".join(f"site:{d}" for d in chunk)
        if topic_q:
            q = f"{topic_q} ({site_clause})"
        else:
            # Top stories from preferred outlets (no topic)
            q = f"({site_clause})"
        batches.append(q)
        if len(batches) >= max_batches:
            break
    return batches


_STOP_TOKENS = frozenset(
    {
        "the",
        "a",
        "an",
        "and",
        "or",
        "of",
        "to",
        "in",
        "on",
        "for",
        "with",
        "at",
        "from",
        "by",
        "is",
        "are",
        "was",
        "were",
        "be",
        "as",
        "it",
        "this",
        "that",
        "news",
    }
)


def _title_words(title: str, *, lower: bool = False) -> set[str]:
    """Split title into words; hyphenated compounds stay whole (Fed-up ≠ Fed)."""
    words = re.findall(r"[A-Za-z0-9]+(?:-[A-Za-z0-9]+)*", title or "")
    if lower:
        return {w.lower() for w in words}
    return set(words)


def topical_score(title: str, query: str) -> int:
    """Rough relevance: how many query tokens appear in the title."""
    raw = re.sub(r"\s+", " ", (query or "").strip())
    if not raw:
        return 1
    title = title or ""
    title_l = title.lower()
    q = raw.lower()
    words_l = _title_words(title, lower=True)
    words_cs = _title_words(title, lower=False)

    # Exact multi-word phrase
    if " " in q and q in title_l:
        return 100
    # Single-token exact: case-sensitive when the query looks like an acronym (Fed, AI)
    if " " not in raw and len(raw) <= 3 and any(c.isupper() for c in raw):
        if raw in words_cs:
            return 100
    elif q in words_l:
        return 100

    raw_tokens = re.findall(r"[A-Za-z0-9]{2,}", raw)
    tokens = [t for t in raw_tokens if t.lower() not in _STOP_TOKENS]
    if not tokens:
        return 1
    hits = 0
    for t in tokens:
        # Short capitalized tokens (Fed): case-sensitive whole-word only
        if len(t) <= 3 and any(c.isupper() for c in t):
            if t in words_cs:
                hits += 1
            elif t.lower() == "fed" and (
                "fomc" in words_l
                or ("federal" in words_l and "reserve" in words_l)
                or re.search(r"\b(interest rates?|rate cut|rate hike)\b", title_l)
            ):
                hits += 1
        elif t.lower() in words_l:
            hits += 1
    return hits


def prefer_topical(
    hits: list[dict[str, Any]] | None,
    query: str,
    *,
    min_keep: int = 3,
) -> list[dict[str, Any]]:
    """
    Prefer hits whose titles mention the query.
    If we have any topical matches, return only those (never pad with
    crossword / homepage noise). Only fall back to unfiltered when nothing
    matched the topic at all.
    """
    if not hits:
        return []
    q = (query or "").strip()
    if not q:
        return list(hits)
    scored: list[tuple[int, dict[str, Any]]] = []
    for h in hits:
        s = topical_score(h.get("title") or "", q)
        out = dict(h)
        try:
            out["score"] = int(out.get("score") or 0) + (s * 15)
        except (TypeError, ValueError):
            pass
        out["topic_score"] = s
        scored.append((s, out))
    scored.sort(key=lambda pair: (pair[0], int(pair[1].get("score") or 0)), reverse=True)
    matched = [h for s, h in scored if s > 0]
    rest = [h for s, h in scored if s == 0]
    if matched:
        return matched
    # Nothing topical — return rest so the UI still has something
    return rest if rest else [h for _, h in scored]


def list_catalog(pref: str | None = None) -> dict[str, Any]:
    """API helper for UI / debugging."""
    if pref:
        p = normalize_pref(pref)
        return {
            "pref": p,
            "label": LABELS[p],
            "tip": TIPS[p],
            "source_list_url": SOURCE_LIST_URLS.get(p),
            "domain_count": len(domains_for(p)),
            "fetch_domains": fetch_domains_for(p),
            "sample_domains": sorted(domains_for(p))[:40],
        }
    return {
        "default": PREF_DEFAULT,
        "options": [pref_meta(p) for p in VALID_PREFS],
        "counts": {
            PREF_CONSERVATIVE: len(CONSERVATIVE_DOMAINS),
            PREF_BALANCED: len(BALANCED_DOMAINS),
            PREF_LIBERAL: len(LIBERAL_DOMAINS),
        },
    }
