import pytest
import asyncio
from datetime import datetime, timezone, date
from app.sports import (
    Event, EventState, ProviderAdapter, Team, clear_cache, get_game_detail,
    get_league_catalog, get_scoreboard, get_sports_home_summary, group_events,
    overlay_scoreboard_event, parse_espn_event, set_provider,
)

class MockProvider(ProviderAdapter):
    def __init__(self):
        self.responses = {}
        self.delays = {}
        self.errors = {}

    async def fetch(self, url: str, params=None):
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
    overlay_scoreboard_event(older, newer)
    assert older.home_team.score == 12
    assert older.status_detail == "Mid 8th"

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


def test_home_summary_does_not_truncate_league_to_five(setup_teardown):
    provider = setup_teardown
    url = "https://cdn.espn.com/core/mlb/scoreboard"
    events = [{"id": str(i), "status": {"type": {"name": "STATUS_IN_PROGRESS", "state": "in"}}} for i in range(8)]
    provider.responses[(url, frozenset({"xhr": "1"}.items()))] = {"events": events}
    summary = run(get_sports_home_summary())
    assert len(summary.data["mlb"]) == 8
