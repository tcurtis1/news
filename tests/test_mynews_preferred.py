"""MyNews preferred-source headlines should never go blank for broad topics."""

from app.source_prefs import prefer_topical, topical_score


def test_politics_matches_trump_headline():
    assert topical_score("Trump taps longtime aide to be White House counsel", "Politics") > 0
    assert topical_score("Fed holds rates steady amid inflation fight", "Economy") > 0


def test_prefer_topical_pads_when_few_matches():
    hits = [
        {"title": "Trump signs border bill", "source": "Breitbart", "score": 10, "url": "https://breitbart.com/a"},
        {"title": "Yankees win again", "source": "New York Post", "score": 9, "url": "https://nypost.com/a"},
        {"title": "Recipe of the day", "source": "Fox News", "score": 8, "url": "https://foxnews.com/a"},
        {"title": "Senate race heats up", "source": "Daily Wire", "score": 7, "url": "https://dailywire.com/a"},
    ]
    out = prefer_topical(hits, "Politics", min_keep=4, pad_with_general=True)
    assert len(out) >= 3
    # Political hits first
    assert "Trump" in out[0]["title"] or "Senate" in out[0]["title"]


def test_prefer_topical_never_empty_when_pool_exists():
    hits = [
        {"title": "Sports ball score", "source": "Fox News", "score": 5, "url": "https://foxnews.com/s"},
        {"title": "Another sports story", "source": "Breitbart", "score": 4, "url": "https://breitbart.com/s"},
    ]
    out = prefer_topical(hits, "QuantumPhysicsXYZ", min_keep=4, pad_with_general=True)
    assert len(out) == 2


def test_prefer_topical_strict_mode_excludes_unrelated_stories():
    """Regression: MyNews chips (AI, Economy, ...) must not backfill with
    unrelated headlines from the shared pool — previously the same
    off-topic story could appear under multiple unrelated chips."""
    hits = [
        {"title": "Sofie Cunningham traded in offseason shakeup", "source": "ESPN", "score": 10, "url": "https://espn.com/a"},
        {"title": "New AI chip unveiled by Nvidia at conference", "source": "Reuters", "score": 5, "url": "https://reuters.com/a"},
    ]
    economy = prefer_topical(hits, "Economy", pad_with_general=False)
    ai = prefer_topical(hits, "AI", pad_with_general=False)
    assert economy == []  # no genuinely economy-related story in the pool
    assert len(ai) == 1 and "AI chip" in ai[0]["title"]


def test_topical_score_rejects_partial_word_substring():
    """Regression: the economy term "trade" must not match "traded" (a
    sports-trade headline), nor should "tax" match "taxi"."""
    assert topical_score("Sofie Cunningham traded in offseason shakeup", "Economy") == 0
    assert topical_score("Taxi drivers protest new city rules", "Economy") == 0


def test_topical_score_still_allows_simple_plurals():
    """Plural tolerance ("job" <-> "jobs") should still work after removing
    the unrestricted substring fallback."""
    assert topical_score("Jobs report beats expectations, markets rally", "Economy") > 0
