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
        # Phone-folder conservative / right-leaning outlets (2026-08)
        "zerohedge.com",
        "x22report.com",
        "rsbnetwork.com",
        "babylonbee.com",
        "justthenews.com",
        "breaking911.com",
        "militarytimes.com",
        "stripes.com",
        "stream.org",
        "thestream.org",
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
        "zero hedge",
        "zerohedge",
        "x22",
        "x22 report",
        "rsbn",
        "real america's voice",
        "babylon bee",
        "just the news",
        "breaking911",
        "breaking 911",
        "military times",
        "stars and stripes",
        "the stream",
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

# Domains used in Google News site: OR batches (highest-signal first).
# Conservative list covers the full Feedspot set so site: queries don't
# stop after the first 16 big names (which made results look "severely limited").
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
        "americanthinker.com",
        "pjmedia.com",
        "wnd.com",
        "spectator.org",
        "theamericanconservative.com",
        "powerlineblog.com",
        "judicialwatch.org",
        "heritage.org",
        "hannity.com",
        "newsbusters.org",
        "lifesitenews.com",
        "cbn.com",
        "commentary.org",
        "firstthings.com",
        "frontpagemag.com",
        "bizpacreview.com",
        "thenewamerican.com",
        "bonginoreport.com",
        "twitchy.com",
        "patriotpost.us",
        "theconservativetreehouse.com",
        "conservativereview.com",
        "charliekirk.com",
        "hughhewitt.com",
        "mrctv.org",
        "voanews.com",
        "csmonitor.com",
        "canadafreepress.com",
        "lifezette.com",
        "afn.net",
        "cnav.news",
        "thetexashorn.com",
        "newscats.org",
        "theothermccain.com",
        "dennisprager.com",
        "reason.com",
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
        "talkingpointsmemo.com",
        "newrepublic.com",
        "jacobin.com",
        "alternet.org",
        "truthout.org",
        "counterpunch.org",
        "latimes.com",
        "cbsnews.com",
        "abcnews.go.com",
        "pbs.org",
        "time.com",
        "vanityfair.com",
        "esquire.com",
        "19thnews.org",
        "prospect.org",
        "mediamatters.org",
        "therealnews.com",
        "progressive.org",
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
        "ft.com",
        "cbsnews.com",
        "abcnews.go.com",
        "cnbc.com",
        "forbes.com",
        "newsweek.com",
        "dw.com",
    ],
}

# Native outlet RSS feeds (no Feedspot proxy). Used to diversify beyond
# Google News site: ranking, which over-weights a few mega-domains.
# (name, domain, feed_url)
OUTLET_RSS: dict[str, list[tuple[str, str, str]]] = {
    PREF_CONSERVATIVE: [
        ("Fox News", "foxnews.com", "https://moxie.foxnews.com/google-publisher/latest.xml"),
        ("Fox News Politics", "foxnews.com", "https://moxie.foxnews.com/google-publisher/politics.xml"),
        ("New York Post", "nypost.com", "https://nypost.com/feed/"),
        ("Breitbart", "breitbart.com", "https://feeds.feedburner.com/breitbart"),
        ("Daily Wire", "dailywire.com", "https://www.dailywire.com/feeds/rss.xml"),
        ("Daily Caller", "dailycaller.com", "https://dailycaller.com/feed/"),
        ("National Review", "nationalreview.com", "https://www.nationalreview.com/feed/"),
        ("The Federalist", "thefederalist.com", "https://thefederalist.com/feed/"),
        ("Free Beacon", "freebeacon.com", "https://freebeacon.com/feed/"),
        ("Washington Examiner", "washingtonexaminer.com", "https://www.washingtonexaminer.com/feed/"),
        ("Washington Times", "washingtontimes.com", "https://www.washingtontimes.com/rss/headlines/news/"),
        ("The Blaze", "theblaze.com", "https://www.theblaze.com/feeds/feed.rss"),
        ("Gateway Pundit", "thegatewaypundit.com", "https://www.thegatewaypundit.com/feed/"),
        ("Daily Signal", "dailysignal.com", "https://www.dailysignal.com/feed/"),
        ("HotAir", "hotair.com", "https://hotair.com/feed"),
        ("RedState", "redstate.com", "https://redstate.com/feed/"),
        ("PJ Media", "pjmedia.com", "https://pjmedia.com/feed"),
        ("American Thinker", "americanthinker.com", "https://www.americanthinker.com/rss.xml"),
        ("Epoch Times", "theepochtimes.com", "https://www.theepochtimes.com/c-us/feed"),
        ("OANN", "oann.com", "https://www.oann.com/feed/"),
        ("Townhall", "townhall.com", "https://townhall.com/rss.xml"),
        ("Western Journal", "westernjournal.com", "https://www.westernjournal.com/feed/"),
        ("Newsmax", "newsmax.com", "https://www.newsmax.com/rss/Newsfront/16/"),
        ("The Hill", "thehill.com", "https://thehill.com/homenews/feed/"),
        ("American Spectator", "spectator.org", "https://spectator.org/feed/"),
        ("American Conservative", "theamericanconservative.com", "https://www.theamericanconservative.com/feed/"),
        ("Power Line", "powerlineblog.com", "https://www.powerlineblog.com/feed"),
        ("Judicial Watch", "judicialwatch.org", "https://www.judicialwatch.org/feed/"),
        ("WND", "wnd.com", "https://www.wnd.com/feed/"),
        ("CBN News", "cbn.com", "https://www1.cbn.com/rss-cbn-articles-cbnnews.xml"),
        ("First Things", "firstthings.com", "https://firstthings.com/feed/"),
        ("Commentary", "commentary.org", "https://www.commentary.org/feed/"),
        ("Twitchy", "twitchy.com", "https://twitchy.com/feed/"),
        ("NewsBusters", "newsbusters.org", "https://www.newsbusters.org/blog/feed"),
        ("LifeSite", "lifesitenews.com", "https://www.lifesitenews.com/feed/"),
        ("Reason", "reason.com", "https://reason.com/feed/"),
        ("WSJ Opinion", "wsj.com", "https://feeds.content.dowjones.io/public/rss/RSSOpinion"),
        ("WSJ World", "wsj.com", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews"),
        # Extra outlets from user conservative source folder (2026-08)
        ("Zero Hedge", "zerohedge.com", "https://feeds.feedburner.com/zerohedge/feed"),
        ("X22 Report", "x22report.com", "https://x22report.com/feed/"),
        ("RSBN", "rsbnetwork.com", "https://www.rsbnetwork.com/feed/"),
        ("Babylon Bee", "babylonbee.com", "https://babylonbee.com/feed"),
        ("Just the News", "justthenews.com", "https://justthenews.com/rss.xml"),
        ("Breaking911", "breaking911.com", "https://www.breaking911.com/feed/"),
        ("Military Times", "militarytimes.com", "https://www.militarytimes.com/arc/outboundfeeds/rss/?outputType=xml"),
        ("The Stream", "stream.org", "https://stream.org/feed/rss"),
    ],
    PREF_LIBERAL: [
        ("NYTimes", "nytimes.com", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
        ("NYTimes Politics", "nytimes.com", "https://rss.nytimes.com/services/xml/rss/nyt/Politics.xml"),
        ("Washington Post", "washingtonpost.com", "https://feeds.washingtonpost.com/rss/national"),
        ("CNN", "cnn.com", "http://rss.cnn.com/rss/cnn_topstories.rss"),
        ("CNN Politics", "cnn.com", "http://rss.cnn.com/rss/cnn_allpolitics.rss"),
        ("The Guardian US", "theguardian.com", "https://www.theguardian.com/us-news/rss"),
        ("NPR", "npr.org", "https://feeds.npr.org/1001/rss.xml"),
        ("Vox", "vox.com", "https://www.vox.com/rss/index.xml"),
        ("Mother Jones", "motherjones.com", "https://www.motherjones.com/feed/"),
        ("The Atlantic", "theatlantic.com", "https://www.theatlantic.com/feed/all/"),
        ("HuffPost", "huffpost.com", "https://www.huffpost.com/section/front-page/feed"),
        ("Politico", "politico.com", "https://rss.politico.com/politics-news.xml"),
        ("The Intercept", "theintercept.com", "https://theintercept.com/feed/?lang=en"),
        ("The Nation", "thenation.com", "https://www.thenation.com/feed/?post_type=article"),
        ("Daily Kos", "dailykos.com", "https://www.dailykos.com/blogs/main.rss"),
        ("ProPublica", "propublica.org", "https://www.propublica.org/feeds/propublica/main"),
        ("Slate", "slate.com", "https://slate.com/feeds/all.rss"),
        ("Daily Beast", "thedailybeast.com", "https://feeds.thedailybeast.com/rss/articles"),
        ("Axios", "axios.com", "https://api.axios.com/feed/"),
        ("Salon", "salon.com", "https://www.salon.com/feed/"),
        ("Raw Story", "rawstory.com", "https://www.rawstory.com/feeds/feed.rss"),
        ("Common Dreams", "commondreams.org", "https://www.commondreams.org/rss.xml"),
        ("Democracy Now", "democracynow.org", "https://www.democracynow.org/democracynow.rss"),
        ("Talking Points Memo", "talkingpointsmemo.com", "https://talkingpointsmemo.com/feed"),
        ("New Republic", "newrepublic.com", "https://newrepublic.com/rss.xml"),
        ("Jacobin", "jacobin.com", "https://jacobin.com/feed"),
        ("NBC News", "nbcnews.com", "https://feeds.nbcnews.com/nbcnews/public/news"),
        ("MSNBC", "msnbc.com", "https://www.msnbc.com/feeds/latest"),
    ],
    PREF_BALANCED: [
        ("Reuters", "reuters.com", "https://www.reutersagency.com/feed/?taxonomy=best-topics&post_type=best"),
        ("AP", "apnews.com", "https://rsshub.app/apnews/topics/apf-topnews"),
        ("BBC", "bbc.com", "https://feeds.bbci.co.uk/news/rss.xml"),
        ("BBC World", "bbc.com", "https://feeds.bbci.co.uk/news/world/rss.xml"),
        ("Bloomberg", "bloomberg.com", "https://feeds.bloomberg.com/politics/news.rss"),
        ("The Hill", "thehill.com", "https://thehill.com/homenews/feed/"),
        ("USA Today", "usatoday.com", "http://rssfeeds.usatoday.com/usatoday-NewsTopStories"),
        ("NPR", "npr.org", "https://feeds.npr.org/1001/rss.xml"),
        ("Christian Science Monitor", "csmonitor.com", "https://rss.csmonitor.com/feeds/all"),
        ("Al Jazeera", "aljazeera.com", "https://www.aljazeera.com/xml/rss/all.xml"),
        ("DW", "dw.com", "https://rss.dw.com/rdf/rss-en-top"),
        ("CNBC", "cnbc.com", "https://search.cnbc.com/rs/search/combinedcms/view.xml?partnerId=wrss01&id=100003114"),
        ("Axios", "axios.com", "https://api.axios.com/feed/"),
        ("WSJ World", "wsj.com", "https://feeds.content.dowjones.io/public/rss/RSSWorldNews"),
        ("NYTimes", "nytimes.com", "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"),
        ("Fox News", "foxnews.com", "https://moxie.foxnews.com/google-publisher/latest.xml"),
        ("CNN", "cnn.com", "http://rss.cnn.com/rss/cnn_topstories.rss"),
        ("Washington Post", "washingtonpost.com", "https://feeds.washingtonpost.com/rss/national"),
        ("Politico", "politico.com", "https://rss.politico.com/politics-news.xml"),
    ],
}


def google_batch_budget(pref: str | None, *, lite: bool = False) -> tuple[int, int]:
    """
    (batch_size, max_batches) for Google News site: queries.
    Conservative gets the widest crawl so mid-tier outlets aren't starved.
    """
    p = normalize_pref(pref)
    if lite:
        if p == PREF_CONSERVATIVE:
            return 6, 10  # up to 60 domains — include mid-tier + folder outlets
        if p == PREF_LIBERAL:
            return 6, 6
        return 6, 4
    # Full search
    if p == PREF_CONSERVATIVE:
        return 6, 12  # up to 72 domains — full Feedspot set + folder list
    if p == PREF_LIBERAL:
        return 6, 7
    return 6, 5


def rss_feeds_for(pref: str | None, *, limit: int | None = None) -> list[tuple[str, str, str]]:
    p = normalize_pref(pref)
    feeds = list(OUTLET_RSS.get(p) or [])
    if limit is not None:
        return feeds[:limit]
    return feeds


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


def fetch_domains_for(pref: str | None, limit: int = 60) -> list[str]:
    """Ordered domains for Google News site: queries (full list when limit high)."""
    p = normalize_pref(pref)
    ordered = list(_FETCH_PRIORITY.get(p, _FETCH_PRIORITY[PREF_BALANCED]))
    # Append any allow-list domains missing from priority so nothing is skipped
    known = set(ordered)
    for d in sorted(domains_for(p)):
        if d not in known:
            ordered.append(d)
            known.add(d)
    return ordered[:limit]


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
    batch_size: int | None = None,
    max_batches: int | None = None,
    lite: bool = False,
) -> list[str]:
    """
    Build Google News RSS queries: optional topic + (site:a OR site:b …).
    Split into batches so URLs stay reasonable. Defaults cover most of the
    preferred domain list (esp. conservative).
    """
    if batch_size is None or max_batches is None:
        bs, mb = google_batch_budget(pref, lite=lite)
        batch_size = batch_size or bs
        max_batches = max_batches or mb
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
