import pytest
import asyncio
from datetime import datetime, timezone, date
from app.sports import (
    get_league_catalog, get_scoreboard, get_game_detail, get_sports_home_summary,
    EventState, ProviderAdapter, set_provider, clear_cache, parse_espn_event
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

def test_final_games_do_not_imply_polling():
    ev_data = {
        "id": "1001",
        "status": {"type": {"name": "STATUS_FINAL"}}
    }
    ev = parse_espn_event("nfl", ev_data)
    assert ev.state == EventState.FINAL
