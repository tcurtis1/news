import pytest
import asyncio
from datetime import datetime, timezone, date
from app.search import SearchHit, google_news_headlines
from app.sports import (
    Event, EventState, ProviderAdapter, STANDINGS_LEAGUES, Team, clear_cache, compact_score_line,
    get_game_detail, get_league_catalog, get_rankings, get_scoreboard, get_sports_headlines,
    get_sports_home_summary, get_standings, group_events, has_college_rankings, has_standings,
    league_news_query, overlay_scoreboard_event, parse_espn_event, parse_espn_rankings,
    parse_espn_standings, set_provider,
)

class MockProvider(ProviderAdapter):
    def __init__(self):
        self.responses = {}
        self.delays = {}
        self.errors = {}

    async def fetch(self, url: str, params=None, **kwargs):
        key = (url, frozenset(params.items()) if params else frozenset())
        if key in self.errors:
            raise self.errors[key]
        if key in self.delays:
            await asyncio.sleep(self.delays[key])
        return self.responses.get(key, {})

@pytest.fixture(autouse=True)
def setup_teardown():
    clear_cache()
    mock_provider = MockProvider()
    set_provider(mock_provider)
    yield mock_provider
    clear_cache()
    set_provider(ProviderAdapter())

def run(coro):
    return asyncio.run(coro)

def test_league_catalog():
    payload = run(get_league_catalog())
    assert payload.freshness == "fresh"
    assert "nfl" in {item["slug"] for item in payload.data}
    assert "nba" in {item["slug"] for item in payload.data}

def test_normalization_and_statuses():
    ev_data = {
        "id": "1001",
        "name": "Team A at Team B",
        "date": "2023-10-15T01:00Z",
        "status": {"type": {"name": "STATUS_SCHEDULED", "state": "pre"}},
        "competitions": [{"competitors": [
            {"homeAway": "home", "team": {"id": "1", "displayName": "Team B", "abbreviation": "TB"}},
            {"homeAway": "away", "team": {"id": "2", "displayName": "Team A", "abbreviation": "TA"}}
        ]}]
    }

    ev = parse_espn_event("nfl", ev_data)
    assert ev.id == "nfl_1001"
    assert ev.state == EventState.SCHEDULED
    assert ev.home_team.id == "1"
    assert ev.away_team.id == "2"

    ev_data["status"]["type"]["name"] = "STATUS_IN_PROGRESS"
    assert parse_espn_event("nfl", ev_data).state == EventState.IN_PROGRESS

    ev_data["status"]["type"]["name"] = "STATUS_HALFTIME"
    assert parse_espn_event("nfl", ev_data).state == EventState.HALFTIME

    ev_data["status"]["type"]["name"] = "STATUS_FINAL"
    assert parse_espn_event("nfl", ev_data).state == EventState.FINAL

    ev_data["status"]["type"]["name"] = "STATUS_POSTPONED"
    assert parse_espn_event("nfl", ev_data).state == EventState.POSTPONED

    ev_data["status"]["type"]["name"] = "STATUS_SUSPENDED"
    assert parse_espn_event("nfl", ev_data).state == EventState.SUSPENDED

    ev_data["status"]["type"]["name"] = "STATUS_CANCELED"
    assert parse_espn_event("nfl", ev_data).state == EventState.CANCELLED

    ev_data["status"]["type"]["name"] = "UNKNOWN_NEW_STATE"
    ev_data["status"]["type"]["state"] = "in"
    assert parse_espn_event("nfl", ev_data).state == EventState.IN_PROGRESS

def test_game_package_scoring_situation_and_tv():
    package = {
        "header": {
            "id": "401",
            "status": {"type": {"name": "STATUS_IN_PROGRESS", "shortDetail": "Mid 3rd"}},
            "competitions": [{
                "outs": 0,
                "broadcasts": [{"media": {"shortName": "YES"}}],
                "competitors": [
                    {"homeAway": "home", "score": "0", "hits": 1, "errors": 0, "team": {"id": "1", "displayName": "Angels", "abbreviation": "LAA"}},
                    {"homeAway": "away", "score": "4", "hits": 5, "errors": 0, "winner": False, "team": {"id": "2", "displayName": "Yankees", "abbreviation": "NYY"}},
                ],
            }],
        },
        "gameInfo": {"venue": {"fullName": "Angel Stadium"}},
        "situation": {"outs": 0, "balls": 0, "strikes": 0},
        "broadcasts": [{"media": {"shortName": "MLB.TV"}}],
        "plays": [
            {"scoringPlay": True, "text": "Jones homered to center.", "period": {"displayValue": "1st Inning"}},
            {"scoringPlay": False, "text": "Ball"},
        ],
        "boxscore": {
            "teams": [
                {"homeAway": "away", "statistics": [{"name": "batting", "stats": [{"name": "homeRuns", "displayValue": "2"}]}]},
                {"homeAway": "home", "statistics": [{"name": "batting", "stats": [{"name": "homeRuns", "displayValue": "0"}]}]},
            ],
            "players": [{
                "team": {"abbreviation": "NYY"},
                "statistics": [{
                    "labels": ["H-AB", "AB", "R", "H", "RBI"],
                    "athletes": [{"athlete": {"shortName": "S. Jones"}, "stats": ["1-2", "2", "1", "1", "2"]}],
                }],
            }],
        },
    }
    ev = parse_espn_event("mlb", package)
    assert ev.venue == "Angel Stadium"
    assert "YES" in ev.tv_broadcasters and "MLB.TV" in ev.tv_broadcasters
    assert ev.situation == "0-0, 0 outs"
    assert ev.scoring_summary[0]["text"].startswith("Jones homered")
    labels = {row["label"]: row for row in ev.team_stats}
    assert labels["Hits"]["away"] == "5"
    assert labels["Home runs"]["away"] == "2"
    assert ev.leaders[0]["name"] == "S. Jones"


def test_season_boxscore_totals_are_not_used_as_game_stats():
    package = {
        "id": "9",
        "competitions": [{"competitors": [
            {"homeAway": "home", "score": "2", "hits": 3, "errors": 0, "team": {"id": "1", "abbreviation": "HOM", "displayName": "Home"}},
            {"homeAway": "away", "score": "1", "hits": 2, "errors": 1, "team": {"id": "2", "abbreviation": "AWY", "displayName": "Away"}},
        ]}],
        "boxscore": {"teams": [
            {"homeAway": "away", "statistics": [{"name": "batting", "stats": [
                {"name": "gamesPlayed", "displayValue": "138"},
                {"name": "homeRuns", "displayValue": "143"},
            ]}]},
            {"homeAway": "home", "statistics": [{"name": "batting", "stats": [
                {"name": "gamesPlayed", "displayValue": "137"},
                {"name": "homeRuns", "displayValue": "169"},
            ]}]},
        ]},
    }
    ev = parse_espn_event("mlb", package)
    labels = {row["label"] for row in ev.team_stats}
    assert "Home runs" not in labels
    assert "Hits" in labels


def test_missing_optional_fields():
    ev_data = {"id": "1002"}
    ev = parse_espn_event("mlb", ev_data)
    assert ev.id == "mlb_1002"
    assert ev.name == "Unknown Event"
    assert ev.state == EventState.SCHEDULED
    assert ev.home_team.name == "Home"
    assert ev.away_team.name == "Away"

def test_invalid_league_date_game_id():
    with pytest.raises(ValueError):
        run(get_scoreboard("invalid_league"))

    with pytest.raises(ValueError):
        run(get_game_detail("invalid"))

    with pytest.raises(ValueError):
        run(get_game_detail("invalid_123"))

def test_provider_timeout_and_stale_cache(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/nfl/scoreboard"
    key = (url, frozenset({"xhr": "1"}.items()))

    provider.responses[key] = {"events": [{"id": "1"}]}

    payload1 = run(get_scoreboard("nfl"))
    assert payload1.freshness == "fresh"
    assert len(payload1.data) == 1

    provider.errors[key] = Exception("Network error")

    from app.sports import _app_cache
    _cache_key = f"scoreboard_nfl_current"
    _app_cache[_cache_key].timestamp -= 40

    payload2 = run(get_scoreboard("nfl"))
    assert payload2.freshness == "stale"
    assert len(payload2.data) == 1
    assert "Network error" in payload2.error

    clear_cache()
    payload3 = run(get_scoreboard("nfl"))
    assert payload3.freshness == "fallback"
    assert payload3.data == []
    assert "Network error" in payload3.error

def test_compact_score_line():
    assert compact_score_line("SF", "PIT", away_score="12", home_score="12", status="Bot 8th", started=True) == "SF 12 @ PIT 12, Bot 8th"
    assert compact_score_line("AWY", "HOM", status="Scheduled", started=False) == "AWY @ HOM, Scheduled"
    assert compact_score_line("NYY", "BOS", away_score="—", home_score="—", status="Pregame", started=False) == "NYY @ BOS, Pregame"
    assert compact_score_line("OSU", "PSU", away_score="24", home_score="14", status="Final", started=True, away_rank=5, home_rank=12) == "#5 OSU 24 @ #12 PSU 14, Final"
    assert compact_score_line("ALA", "VANDY", status="7:30 PM", started=False, away_rank=1) == "#1 ALA @ VANDY, 7:30 PM"


def test_timezone_day_boundary():
    ev_data = {
        "id": "1003",
        "date": "2023-10-15T00:30Z",
    }
    ev = parse_espn_event("nfl", ev_data)
    assert ev.start_time.tzinfo == timezone.utc
    assert ev.start_time.hour == 0
    assert ev.start_time.minute == 30

def test_get_sports_home_summary(setup_teardown):
    provider = setup_teardown
    summary = run(get_sports_home_summary())
    assert summary.freshness == "fresh"
    assert isinstance(summary.data, dict)
    assert "nfl" in summary.data
    assert "nba" in summary.data
    assert "epl" in summary.data

def test_duplicate_event_handling(setup_teardown):
    ev_data = {"id": "1001", "name": "Event A"}
    provider = setup_teardown
    url = "https://cdn.espn.com/core/nfl/scoreboard"
    params = frozenset({"xhr": "1", "dates": "20231015"}.items())
    provider.responses[(url, params)] = {
        "events": [ev_data, ev_data]
    }
    payload = run(get_scoreboard("nfl", date(2023, 10, 15)))
    assert len(payload.data) == 1
    assert payload.data[0].id == "nfl_1001"

def test_game_detail(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/nba/game"
    params = frozenset({"xhr": "1", "gameId": "555"}.items())
    provider.responses[(url, params)] = {
        "header": {
            "id": "555",
            "name": "Game 555",
            "status": {"type": {"name": "STATUS_FINAL"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "team": {"id": "10"}},
                {"homeAway": "away", "team": {"id": "11"}}
            ]}]
        }
    }

    payload = run(get_game_detail("nba_555"))
    assert payload.freshness == "fresh"
    assert payload.data.id == "nba_555"
    assert payload.data.state == EventState.FINAL
    assert payload.data.home_team.id == "10"


def test_game_detail_overlays_live_scoreboard(setup_teardown):
    provider = setup_teardown
    game_url = "https://cdn.espn.com/core/mlb/game"
    board_url = "https://cdn.espn.com/core/mlb/scoreboard"
    provider.responses[(game_url, frozenset({"xhr": "1", "gameId": "401"}.items()))] = {
        "header": {
            "id": "401",
            "status": {"type": {"name": "STATUS_IN_PROGRESS", "shortDetail": "Bot 6th"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": "11", "team": {"id": "23", "displayName": "Pirates", "abbreviation": "PIT"}},
                {"homeAway": "away", "score": "7", "team": {"id": "26", "displayName": "Giants", "abbreviation": "SF"}},
            ]}],
        }
    }
    provider.responses[(board_url, frozenset({"xhr": "1"}.items()))] = {
        "events": [{
            "id": "401",
            "status": {"type": {"name": "STATUS_IN_PROGRESS", "shortDetail": "Mid 8th"}},
            "competitions": [{"competitors": [
                {"homeAway": "home", "score": "12", "team": {"id": "23", "displayName": "Pirates", "abbreviation": "PIT"}},
                {"homeAway": "away", "score": "12", "team": {"id": "26", "displayName": "Giants", "abbreviation": "SF"}},
            ]}],
        }]
    }
    payload = run(get_game_detail("mlb_401"))
    assert payload.data.home_team.score == 12
    assert payload.data.away_team.score == 12
    assert payload.data.status_detail == "Mid 8th"


def test_overlay_scoreboard_event_copies_live_fields():
    older = _event("1", EventState.IN_PROGRESS, datetime(2026, 9, 1, 20, tzinfo=timezone.utc))
    older.home_team.score = 11
    older.away_team.score = 7
    older.status_detail = "Bot 6th"
    newer = _event("1", EventState.IN_PROGRESS, datetime(2026, 9, 1, 20, tzinfo=timezone.utc))
    newer.home_team.score = 12
    newer.away_team.score = 12
    newer.status_detail = "Mid 8th"
    newer.context_line = "SF 73-64 at PIT 61-76"
    overlay_scoreboard_event(older, newer)
    assert older.home_team.score == 12
    assert older.status_detail == "Mid 8th"
    assert older.context_line == "SF 73-64 at PIT 61-76"

def test_final_games_do_not_imply_polling():
    ev_data = {
        "id": "1001",
        "status": {"type": {"name": "STATUS_FINAL"}}
    }
    ev = parse_espn_event("nfl", ev_data)
    assert ev.state == EventState.FINAL


def _event(eid, state, start, league="mlb"):
    return Event(
        id=f"{league}_{eid}",
        name="Away at Home",
        short_name="AWY @ HOM",
        start_time=start,
        state=state,
        league=league,
        away_team=Team(id="1", name="Away", abbreviation="AWY", is_home=False, score=1),
        home_team=Team(id="2", name="Home", abbreviation="HOM", is_home=True, score=2),
    )


def test_home_mix_keeps_late_live_games():
    now = datetime(2026, 9, 1, 23, 0, tzinfo=timezone.utc)
    finals = [_event(i, EventState.FINAL, now.replace(hour=17), "mlb") for i in range(5)]
    live = _event("live", EventState.IN_PROGRESS, now.replace(hour=20), "mlb")
    grouped = group_events(finals + [live], now, window=True)
    assert [event.id for event in grouped["live"]] == ["mlb_live"]
    assert len(grouped["final"]) == 5


def test_home_mix_drops_far_future_cbb():
    now = datetime(2026, 9, 1, 18, 0, tzinfo=timezone.utc)
    nov = _event("nov", EventState.SCHEDULED, datetime(2026, 11, 15, 0, tzinfo=timezone.utc), "mcbb")
    grouped = group_events([nov], now, window=True)
    assert grouped["upcoming"] == []
    assert grouped["live"] == []


def test_context_line_uses_records_week_rank_weather_and_skips_tickets():
    ev = parse_espn_event("cfb", {
        "id": "401",
        "week": {"number": 3},
        "competitions": [{
            "notes": [
                {"headline": "Tickets available now"},
                {"headline": "SEC opener"},
            ],
            "weather": {"displayValue": "Clear, 72°F"},
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"id": "1", "displayName": "Penn State", "abbreviation": "PSU"},
                    "records": [{"type": "total", "summary": "3-0"}],
                    "curatedRank": {"current": 12},
                },
                {
                    "homeAway": "away",
                    "team": {"id": "2", "displayName": "Ohio State", "abbreviation": "OSU"},
                    "records": [{"type": "total", "summary": "4-0"}],
                    "curatedRank": {"current": 5},
                },
            ],
        }],
    })
    line = ev.context_line or ""
    assert "Week 3" in line
    assert "SEC opener" in line
    assert "ticket" not in line.lower()
    assert "#5 OSU 4-0" in line
    assert "#12 PSU 3-0" in line
    assert "Clear, 72°F" in line


def test_context_line_skips_empty_records_and_unranked():
    ev = parse_espn_event("mlb", {
        "id": "2",
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {"id": "1", "abbreviation": "HOM", "displayName": "Home", "curatedRank": {"current": 99}},
                    "records": [{"type": "total", "summary": "0-0"}],
                },
                {
                    "homeAway": "away",
                    "team": {"id": "2", "abbreviation": "AWY", "displayName": "Away"},
                    "records": [{"type": "home", "summary": "10-2"}],
                },
            ],
        }],
    })
    assert not ev.context_line


def test_home_summary_does_not_truncate_league_to_five(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/mlb/scoreboard"
    events = [{"id": str(i), "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}}} for i in range(8)]
    provider.responses[(url, frozenset({"xhr": "1"}.items()))] = {"events": events}
    summary = run(get_sports_home_summary())
    assert len(summary.data["mlb"]) == 8


def test_league_news_query_uses_plain_league_names():
    assert league_news_query("nfl") == "NFL"
    assert league_news_query("cfb") == "college football"
    assert league_news_query("nope") == ""


def _ranking_payload():
    return {
        "requestedSeason": {"week": {"displayValue": "Preseason"}},
        "rankings": [
            {
                "id": "1", "type": "ap", "name": "AP Top 25", "shortName": "AP Poll",
                "occurrence": {"displayValue": "Preseason"},
                "ranks": [
                    {"current": 1, "previous": 0, "trend": "-1", "recordSummary": "0-0",
                     "team": {"id": "194", "nickname": "Ohio State", "abbreviation": "OSU"}},
                    {"current": 2, "previous": 4, "trend": "+2", "recordSummary": "11-1",
                     "team": {"id": "251", "nickname": "Texas", "abbreviation": "TEX"}},
                    {"current": 3, "previous": 3, "trend": "0", "recordSummary": "10-2",
                     "team": {"id": "52", "nickname": "Florida State", "abbreviation": "FSU"}},
                ],
            },
            {
                "id": "2", "type": "usa", "name": "AFCA Coaches Poll", "shortName": "AFCA Coaches Poll",
                "ranks": [{"current": 1, "previous": 1, "trend": "—", "recordSummary": "0-0",
                           "team": {"id": "194", "nickname": "Ohio State", "abbreviation": "OSU"}}],
            },
            {
                "id": "20", "type": "fcs", "name": "FCS Coaches Poll", "shortName": "FCS Coaches Poll",
                "ranks": [{"current": 1, "previous": 1, "recordSummary": "0-0",
                           "team": {"id": "1", "nickname": "North Dakota State", "abbreviation": "NDSU"}}],
            },
        ],
    }


def test_parse_rankings_prefers_ap_and_skips_fcs():
    parsed = parse_espn_rankings("cfb", _ranking_payload())
    assert parsed["available"] is True
    assert parsed["poll"] == "ap"
    assert parsed["poll_name"] == "AP Top 25"
    assert parsed["week_label"] == "Preseason"
    assert [p["type"] for p in parsed["polls"]] == ["ap", "usa"]
    assert parsed["teams"][0]["name"] == "Ohio State"
    assert parsed["teams"][0]["trend"] == "new"
    assert parsed["teams"][0]["previous_label"] == "NR"
    assert parsed["teams"][1]["trend"] == "+2"
    assert parsed["teams"][2]["trend"] == "—"
    assert parsed["teams"][0]["news_query"] == "Ohio State college football"
    coaches = parse_espn_rankings("cfb", _ranking_payload(), poll="usa")
    assert coaches["poll"] == "usa"
    assert coaches["poll_name"] == "AFCA Coaches Poll"
    assert len(coaches["teams"]) == 1


def test_has_college_rankings_only_for_cfb_cbb():
    assert has_college_rankings("cfb")
    assert has_college_rankings("mcbb")
    assert has_college_rankings("wcbb")
    assert not has_college_rankings("nfl")


def test_parse_cdn_rankings_shape():
    parsed = parse_espn_rankings("cfb", {
        "weekFilters": [{"label": "Preseason", "selected": True}],
        "rankings": [{
            "id": 1, "name": "AP Top 25", "short_name": "AP Poll",
            "ranks": [{
                "rank": 1, "previous_rank": 0, "trend": "-1", "formatted_record": "0-0",
                "team_display_name": "Ohio State", "team_abbreviation": "OSU",
                "team_url": "https://www.espn.com/college-football/team/_/id/194/ohio-state-buckeyes",
            }],
        }],
    })
    assert parsed["poll"] == "ap"
    assert parsed["week_label"] == "Preseason"
    assert parsed["teams"][0]["team_id"] == "194"
    assert parsed["teams"][0]["trend"] == "new"


def test_get_rankings_uses_espn_path(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/college-football/rankings"
    provider.responses[(url, frozenset({"xhr": "1"}.items()))] = {"content": {"data": _ranking_payload()}}
    payload = run(get_rankings("cfb"))
    assert payload.freshness == "fresh"
    assert payload.data["teams"][0]["abbreviation"] == "OSU"
    with pytest.raises(ValueError):
        run(get_rankings("nfl"))


def test_google_news_headlines_skip_non_http(monkeypatch):
    async def fake_fetch(client, q, *, place=None, limit=12, score_base=1000):
        return [
            SearchHit(title="Good", url="https://a.test/x", source="AP"),
            SearchHit(title="Bad", url="javascript:alert(1)", source="x"),
        ]

    monkeypatch.setattr("app.search._fetch_google_news", fake_fetch)
    assert run(google_news_headlines("NFL")) == [
        {"title": "Good", "url": "https://a.test/x", "source": "AP"}
    ]


def test_sports_headlines_cache_and_https_only(monkeypatch):
    calls = {"n": 0}

    async def fake_google(query, limit=8):
        calls["n"] += 1
        assert query == "NFL"
        return [
            {"title": "NFL opens week 1", "url": "https://example.com/nfl", "source": "AP"},
            {"title": "Bad", "url": "javascript:alert(1)", "source": "x"},
        ]

    monkeypatch.setattr("app.search.google_news_headlines", fake_google)
    clear_cache()
    first = run(get_sports_headlines("NFL"))
    second = run(get_sports_headlines("NFL"))
    assert first == second
    assert calls["n"] == 1
    assert first["headlines"][0]["url"].startswith("https://")


def test_team_logos_parsed():
    ev_data = {
        "id": "2001",
        "name": "Team A at Team B",
        "date": "2026-09-09T01:00Z",
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}},
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "team": {
                        "id": "1",
                        "displayName": "Home Team",
                        "abbreviation": "HOM",
                        "logo": "https://a.espncdn.com/i/teamlogos/mlb/500/hom.png",
                    },
                },
                {
                    "homeAway": "away",
                    "team": {
                        "id": "2",
                        "displayName": "Away Team",
                        "abbreviation": "AWY",
                        "logos": [{"href": "https://a.espncdn.com/i/teamlogos/mlb/500/awy.png"}],
                    },
                },
            ]
        }],
    }
    ev = parse_espn_event("mlb", ev_data)
    assert ev.home_team.logo == "https://a.espncdn.com/i/teamlogos/mlb/500/hom.png"
    assert ev.away_team.logo == "https://a.espncdn.com/i/teamlogos/mlb/500/awy.png"


def test_team_ranks_and_logo_fallbacks():
    ev_data = {
        "id": "3001",
        "name": "Ohio State at Penn State",
        "date": "2026-09-09T01:00Z",
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}},
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "curatedRank": {"current": 12},
                    "team": {
                        "id": "213",
                        "displayName": "Penn State Nittany Lions",
                        "abbreviation": "PSU",
                    },
                },
                {
                    "homeAway": "away",
                    "curatedRank": {"current": 5},
                    "team": {
                        "id": "194",
                        "displayName": "Ohio State Buckeyes",
                        "abbreviation": "OSU",
                        "logo": "https://a.espncdn.com/custom_osu.png",
                    },
                },
            ]
        }],
    }
    ev = parse_espn_event("cfb", ev_data)
    assert ev.home_team.rank == 12
    assert ev.away_team.rank == 5
    # Away team has explicit logo
    assert ev.away_team.logo == "https://a.espncdn.com/custom_osu.png"
    # Home team should fall back to NCAA CDN URL
    assert ev.home_team.logo == "https://a.espncdn.com/i/teamlogos/ncaa/500/213.png"

    # Unranked team with rank 99
    ev_unranked = {
        "id": "3002",
        "competitions": [{
            "competitors": [
                {
                    "homeAway": "home",
                    "curatedRank": {"current": 99},
                    "team": {"id": "100", "abbreviation": "UNR1"},
                },
                {
                    "homeAway": "away",
                    "team": {"id": "101", "abbreviation": "UNR2"},
                },
            ]
        }],
    }
    ev2 = parse_espn_event("cfb", ev_unranked)
    assert ev2.home_team.rank is None
    assert ev2.away_team.rank is None


def test_parse_espn_rankings_includes_logo():
    raw = {
        "rankings": [{
            "type": "ap",
            "name": "AP Top 25",
            "ranks": [
                {
                    "current": 1,
                    "previous": 2,
                    "trend": "+1",
                    "recordSummary": "2-0",
                    "team": {
                        "id": "77",
                        "nickname": "Georgia Bulldogs",
                        "abbreviation": "UGA",
                        "logo": "https://a.espncdn.com/uga.png",
                    },
                },
                {
                    "current": 2,
                    "previous": 1,
                    "trend": "-1",
                    "recordSummary": "2-0",
                    "team": {
                        "id": "194",
                        "nickname": "Ohio State Buckeyes",
                        "abbreviation": "OSU",
                    },
                },
            ],
        }]
    }
    parsed = parse_espn_rankings("cfb", raw)
    assert parsed["available"] is True
    assert len(parsed["teams"]) == 2
    assert parsed["teams"][0]["current"] == 1
    assert parsed["teams"][0]["logo"] == "https://a.espncdn.com/uga.png"
    assert parsed["teams"][1]["current"] == 2
    assert parsed["teams"][1]["logo"] == "https://a.espncdn.com/i/teamlogos/ncaa/500/194.png"



def test_baseball_situation_parsed():
    ev_data = {
        "id": "2002",
        "name": "Red Sox at Yankees",
        "date": "2026-09-09T01:00Z",
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "abbreviation": "NYY"}},
                {"homeAway": "away", "team": {"id": "2", "abbreviation": "BOS"}},
            ],
            "situation": {
                "outs": 2,
                "balls": 3,
                "strikes": 2,
                "onFirst": True,
                "onSecond": False,
                "onThird": True,
                "batter": {"athlete": {"displayName": "Aaron Judge"}},
                "pitcher": {"athlete": {"displayName": "Brayan Bello"}},
                "lastPlay": {"text": "Aaron Judge strikes out swinging."},
            },
        }],
    }
    ev = parse_espn_event("mlb", ev_data)
    assert ev.outs == 2
    assert ev.balls == 3
    assert ev.strikes == 2
    assert ev.on_first is True
    assert ev.on_second is False
    assert ev.on_third is True
    assert ev.batter_name == "Aaron Judge"
    assert ev.pitcher_name == "Brayan Bello"
    assert ev.last_play == "Aaron Judge strikes out swinging."
    assert "2 outs" in ev.situation.lower() or "3-2" in ev.situation


def test_football_situation_parsed():
    ev_data = {
        "id": "2003",
        "name": "Chiefs at Ravens",
        "date": "2026-09-09T01:00Z",
        "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}},
        "competitions": [{
            "competitors": [
                {"homeAway": "home", "team": {"id": "1", "abbreviation": "BAL"}},
                {"homeAway": "away", "team": {"id": "2", "abbreviation": "KC"}},
            ],
            "situation": {
                "down": 3,
                "distance": 4,
                "downDistanceText": "3rd & 4 at BAL 18",
                "possession": "2",
                "possessionText": "KC ball",
                "isRedZone": True,
                "lastPlay": {"text": "Patrick Mahomes pass short right to Travis Kelce for 6 yards."},
            },
        }],
    }
    ev = parse_espn_event("nfl", ev_data)
    assert ev.down_distance == "3rd & 4 at BAL 18"
    assert ev.possession_text == "KC ball"
    assert ev.is_red_zone is True
    assert "Patrick Mahomes" in ev.last_play
    assert "3rd & 4" in ev.situation


def test_overlay_preserves_situation_and_logos():
    detail = Event(
        id="mlb_1", name="A at B", short_name="A @ B",
        start_time=datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc),
        state=EventState.IN_PROGRESS, clock="", period=1, league="mlb", status_detail="",
        venue="", situation="",
        away_team=Team(id="1", name="Away", abbreviation="AWY", is_home=False),
        home_team=Team(id="2", name="Home", abbreviation="HOM", is_home=True),
    )
    board = Event(
        id="mlb_1", name="A at B", short_name="A @ B",
        start_time=datetime(2026, 9, 9, 0, 0, tzinfo=timezone.utc),
        state=EventState.IN_PROGRESS, clock="", period=5, league="mlb", status_detail="Mid 5th",
        venue="", situation="1-2, 1 out",
        away_team=Team(id="1", name="Away", abbreviation="AWY", is_home=False, logo="https://awy.png", rank=7),
        home_team=Team(id="2", name="Home", abbreviation="HOM", is_home=True, logo="https://hom.png", rank=14),
        outs=1, balls=1, strikes=2, on_first=True, on_second=False, on_third=True,
        batter_name="Batter", pitcher_name="Pitcher",
    )
    overlaid = overlay_scoreboard_event(detail, board)
    assert overlaid.outs == 1
    assert overlaid.balls == 1
    assert overlaid.strikes == 2
    assert overlaid.on_first is True
    assert overlaid.on_third is True
    assert overlaid.batter_name == "Batter"
    assert overlaid.away_team.logo == "https://awy.png"
    assert overlaid.home_team.logo == "https://hom.png"
    assert overlaid.away_team.rank == 7
    assert overlaid.home_team.rank == 14


def test_parse_espn_standings_groups_and_children():
    # Test groups structure (typical MLB/NFL)
    raw_groups = {
        "content": {
            "standings": {
                "groups": [{
                    "name": "AL East",
                    "standings": {
                        "entries": [{
                            "team": {
                                "id": "10", "name": "Yankees", "displayName": "New York Yankees",
                                "abbreviation": "NYY",
                                "logo": "https://a.espncdn.com/nyy.png",
                            },
                            "stats": [
                                {"name": "wins", "displayValue": "80"},
                                {"name": "losses", "displayValue": "50"},
                                {"name": "winPercent", "displayValue": ".615"},
                                {"name": "gamesBehind", "displayValue": "-"},
                                {"name": "pointDifferential", "displayValue": "+110"},
                                {"name": "streak", "displayValue": "W3"},
                                {"name": "Last Ten Games", "displayValue": "7-3"},
                            ],
                        }],
                    },
                }],
            },
        },
    }
    parsed = parse_espn_standings("mlb", raw_groups)
    assert parsed["available"] is True
    assert len(parsed["divisions"]) == 1
    assert parsed["divisions"][0]["name"] == "AL East"
    team = parsed["divisions"][0]["teams"][0]
    assert team["abbreviation"] == "NYY"
    assert team["wins"] == "80"
    assert team["losses"] == "50"
    assert team["pct"] == ".615"
    assert team["gb"] == "-"
    assert team["diff"] == "+110"
    assert team["streak"] == "W3"
    assert team["logo"] == "https://a.espncdn.com/nyy.png"

    # Test children structure (NHL v2 API style)
    raw_children = {
        "children": [{
            "name": "Eastern Conference",
            "standings": {
                "entries": [{
                    "team": {
                        "id": "1", "name": "Bruins", "displayName": "Boston Bruins",
                        "abbreviation": "BOS",
                        "logos": [{"href": "https://a.espncdn.com/bos.png"}],
                    },
                    "stats": [
                        {"name": "wins", "displayValue": "45"},
                        {"name": "losses", "displayValue": "20"},
                        {"name": "OTLosses", "displayValue": "7"},
                        {"name": "points", "displayValue": "97"},
                    ],
                }],
            },
        }],
    }
    parsed_nhl = parse_espn_standings("nhl", raw_children)
    assert parsed_nhl["available"] is True
    assert parsed_nhl["divisions"][0]["name"] == "Eastern Conference"
    nhl_team = parsed_nhl["divisions"][0]["teams"][0]
    assert nhl_team["abbreviation"] == "BOS"
    assert nhl_team["ties"] == "7"
    assert nhl_team["points"] == "97"
    assert nhl_team["logo"] == "https://a.espncdn.com/bos.png"


def test_get_standings_uses_espn_sources(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/mlb/standings"
    provider.responses[(url, frozenset({"xhr": "1"}.items()))] = {
        "content": {
            "standings": {
                "groups": [{
                    "name": "AL East",
                    "standings": {
                        "entries": [{
                            "team": {"id": "10", "name": "Yankees", "abbreviation": "NYY"},
                            "stats": [{"name": "wins", "displayValue": "80"}, {"name": "losses", "displayValue": "50"}],
                        }],
                    },
                }],
            },
        },
    }
    payload = run(get_standings("mlb"))
    assert payload.freshness == "fresh"
    assert payload.data["divisions"][0]["teams"][0]["abbreviation"] == "NYY"
    with pytest.raises(ValueError):
        run(get_standings("cfb"))


def test_has_standings_leagues():
    for lg in ["mlb", "nfl", "nba", "nhl", "wnba", "mls", "epl"]:
        assert has_standings(lg) is True
    for lg in ["cfb", "mcbb", "wcbb"]:
        assert has_standings(lg) is False


def test_parse_espn_event_odds():
    raw_event = {
        "id": "401671800",
        "date": "2026-09-15T00:15:00Z",
        "competitions": [
            {
                "id": "401671800",
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"id": "1", "abbreviation": "KC", "displayName": "Kansas City Chiefs"},
                    },
                    {
                        "homeAway": "away",
                        "team": {"id": "2", "abbreviation": "CIN", "displayName": "Cincinnati Bengals"},
                    },
                ],
                "odds": [
                    {
                        "details": "KC -3.5",
                        "overUnder": 48.5,
                        "provider": {"displayName": "DraftKings"},
                    }
                ],
            }
        ],
    }
    event = parse_espn_event("nfl", raw_event)
    assert event.odds == "KC -3.5"
    assert event.over_under == 48.5
    assert event.odds_summary == "KC -3.5 · O/U 48.5"
    assert event.odds_provider == "DraftKings"


def test_parse_espn_event_game_odds_details_and_movement():
    raw_event = {
        "id": "401872925",
        "date": "2026-09-15T17:00:00Z",
        "competitions": [
            {
                "id": "401872925",
                "competitors": [
                    {
                        "homeAway": "home",
                        "team": {"id": "4", "abbreviation": "CIN", "displayName": "Cincinnati Bengals"},
                    },
                    {
                        "homeAway": "away",
                        "team": {"id": "27", "abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"},
                    },
                ],
                "odds": [
                    {
                        "details": "CIN -3.5",
                        "spread": -3.5,
                        "overUnder": 50.5,
                        "provider": {"displayName": "DraftKings"},
                        "pointSpread": {
                            "home": {
                                "open": {"line": "-2.5", "odds": "-110"},
                                "close": {"line": "-3.5", "odds": "-112"},
                            },
                            "away": {
                                "open": {"line": "+2.5", "odds": "-110"},
                                "close": {"line": "+3.5", "odds": "-108"},
                            },
                        },
                        "moneyline": {
                            "home": {
                                "open": {"odds": "-170"},
                                "close": {"odds": "-198"},
                            },
                            "away": {
                                "open": {"odds": "+150"},
                                "close": {"odds": "+164"},
                            },
                        },
                        "total": {
                            "over": {
                                "open": {"line": "o48.5", "odds": "-115"},
                                "close": {"line": "o50.5", "odds": "-108"},
                            },
                            "under": {
                                "open": {"line": "u48.5", "odds": "-105"},
                                "close": {"line": "u50.5", "odds": "-112"},
                            },
                        },
                        "homeTeamOdds": {"favorite": True, "moneyLine": -198},
                        "awayTeamOdds": {"favorite": False, "moneyLine": 164},
                    }
                ],
            }
        ],
    }
    event = parse_espn_event("nfl", raw_event)
    assert event.game_odds is not None
    go = event.game_odds
    assert go["provider"] == "DraftKings"
    assert go["spread"] == -3.5
    assert go["over_under"] == 50.5
    assert go["home"]["spread"] == "-3.5"
    assert go["home"]["spread_odds"] == "-112"
    assert go["home"]["open_spread"] == "-2.5"
    assert go["home"]["moneyline"] == "-198"
    assert go["home"]["open_moneyline"] == "-170"
    assert go["home"]["favorite"] is True
    assert go["away"]["spread"] == "+3.5"
    assert go["away"]["moneyline"] == "+164"
    assert go["total"]["line"] == "50.5"
    assert go["total"]["open_line"] == "48.5"
    assert go["has_movement"] is True
    assert any("Spread (CIN): -2.5 → -3.5" in m for m in go["movement"])
    assert any("Total: 48.5 → 50.5 (+2)" in m for m in go["movement"])
    assert any("ML (CIN): -170 → -198" in m for m in go["movement"])


def test_parse_espn_event_predictor():
    pkg = {
        "id": "401872925",
        "date": "2026-09-15T17:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"id": "4", "abbreviation": "CIN", "displayName": "Cincinnati Bengals"}},
                    {"homeAway": "away", "team": {"id": "27", "abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}},
                ]
            }
        ],
        "predictor": {
            "header": "Matchup Predictor",
            "homeTeam": {"id": "4", "gameProjection": "62.6"},
            "awayTeam": {"id": "27", "gameProjection": "37.4"},
        },
    }
    event = parse_espn_event("nfl", pkg)
    assert event.predictor is not None
    pred = event.predictor
    assert pred["home_projection"] == 62.6
    assert pred["away_projection"] == 37.4
    assert pred["home_display"] == "62.6%"
    assert pred["away_display"] == "37.4%"
    assert pred["favored_team_abbr"] == "CIN"
    assert pred["favored_pct_display"] == "62.6%"


def test_pickcenter_and_scoreboard_overlay():
    # Test pickcenter inside gamepackage
    pkg = {
        "id": "401872925",
        "date": "2026-09-15T17:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"id": "4", "abbreviation": "CIN", "displayName": "Cincinnati Bengals"}},
                    {"homeAway": "away", "team": {"id": "27", "abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}},
                ]
            }
        ],
        "pickcenter": [
            {
                "details": "CIN -4.0",
                "overUnder": 51.0,
                "provider": {"displayName": "ESPN BET"},
            }
        ],
    }
    detail = parse_espn_event("nfl", pkg)
    assert detail.odds == "CIN -4.0"
    assert detail.over_under == 51.0
    assert detail.odds_provider == "ESPN BET"

    # Test overlay when detail is missing odds
    detail_empty = parse_espn_event("nfl", {
        "id": "401872925",
        "date": "2026-09-15T17:00:00Z",
        "competitions": [
            {
                "competitors": [
                    {"homeAway": "home", "team": {"id": "4", "abbreviation": "CIN", "displayName": "Cincinnati Bengals"}},
                    {"homeAway": "away", "team": {"id": "27", "abbreviation": "TB", "displayName": "Tampa Bay Buccaneers"}},
                ]
            }
        ],
    })
    assert detail_empty.odds is None
    overlay_scoreboard_event(detail_empty, detail)
    assert detail_empty.odds == "CIN -4.0"
    assert detail_empty.over_under == 51.0
    assert detail_empty.odds_provider == "ESPN BET"



