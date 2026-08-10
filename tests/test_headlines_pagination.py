"""Preferred headlines pagination + conservative outlet coverage."""

from app.search import paginate_hits
from app.source_prefs import CONSERVATIVE_DOMAINS, OUTLET_RSS, PREF_CONSERVATIVE, rss_feeds_for


def test_paginate_hits_pages():
    hits = [{"title": f"t{i}", "url": f"https://ex.com/{i}"} for i in range(55)]
    p0 = paginate_hits(hits, offset=0, limit=20)
    assert p0["count"] == 20
    assert p0["has_more"] is True
    assert p0["next_offset"] == 20
    assert p0["total"] == 55

    p1 = paginate_hits(hits, offset=20, limit=20)
    assert p1["count"] == 20
    assert p1["has_more"] is True

    p2 = paginate_hits(hits, offset=40, limit=20)
    assert p2["count"] == 15
    assert p2["has_more"] is False
    assert p2["next_offset"] == 55


def test_paginate_empty_and_clamp():
    assert paginate_hits([], offset=0, limit=20)["has_more"] is False
    big = paginate_hits([{"t": 1}] * 10, offset=0, limit=999)
    assert big["limit"] == 50  # hard cap
    assert big["count"] == 10


def test_conservative_folder_outlets_present():
    needed = {
        "zerohedge.com",
        "justthenews.com",
        "babylonbee.com",
        "rsbnetwork.com",
        "x22report.com",
        "breaking911.com",
        "militarytimes.com",
        "stream.org",
        "thegatewaypundit.com",
        "breitbart.com",
        "thefederalist.com",
        "theblaze.com",
        "pjmedia.com",
        "westernjournal.com",
        "theepochtimes.com",
        "nationalreview.com",
        "dailywire.com",
        "washingtonexaminer.com",
        "washingtontimes.com",
        "townhall.com",
        "redstate.com",
        "oann.com",
    }
    missing = needed - CONSERVATIVE_DOMAINS
    assert not missing, f"missing domains: {missing}"

    feeds = rss_feeds_for(PREF_CONSERVATIVE)
    feed_domains = {d for _, d, _ in feeds}
    for d in (
        "zerohedge.com",
        "justthenews.com",
        "babylonbee.com",
        "rsbnetwork.com",
        "thegatewaypundit.com",
    ):
        assert d in feed_domains
    assert len(feeds) >= 40
    assert len(OUTLET_RSS[PREF_CONSERVATIVE]) == len(feeds)
