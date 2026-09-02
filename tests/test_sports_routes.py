from datetime import datetime, timezone

from fastapi.testclient import TestClient

import app.main as main_mod
from app.sports import Event, EventState, SportsPayload, Team


import pytest


@pytest.fixture(autouse=True)
def isolated_analytics(monkeypatch, tmp_path):
    import app.analytics as analytics_mod
    monkeypatch.setattr(analytics_mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(analytics_mod, "STORE_PATH", tmp_path / "analytics.json")


def sample_event(state=EventState.IN_PROGRESS):
    return Event(
        id="nfl_123", name="Away at Home", short_name="AWY @ HOM",
        start_time=datetime(2026, 8, 19, 23, 0, tzinfo=timezone.utc),
        state=state, clock="04:21", period=4, league="nfl", status_detail="4th 4:21",
        venue="Test Field",
        situation="2-1, 1 out",
        context_line="Week 3 · AWY 4-0 at HOM 3-0",
        tv_broadcasters=["ESPN"],
        away_team=Team(id="1", name="Away Team", abbreviation="AWY", score=17, is_home=False),
        home_team=Team(id="2", name="Home Team", abbreviation="HOM", score=20, is_home=True),
        scoring_summary=[{"clock": "4:21", "text": "Home field goal"}],
        team_stats=[{"label": "Total yards", "away": "301", "home": "322"}],
        leaders=[{"category": "Passing", "name": "Pat Example", "value": "250 YDS"}],
    )


def payload(data, freshness="fresh"):
    return SportsPayload(
        updated_at=datetime(2026, 8, 19, 23, 4, tzinfo=timezone.utc),
        freshness=freshness, provider_label="espn", data=data,
    )


def install_fakes(monkeypatch, game=None, freshness="fresh"):
    event = game or sample_event()

    async def fake_scoreboard(league, target_date=None):
        assert league in main_mod.LEAGUES
        return payload([event], freshness)

    async def fake_summary(target_date=None):
        return payload({"nfl": [event]}, freshness)

    async def fake_game(game_id):
        if game_id == "nfl_missing":
            return payload(None, "fallback")
        return payload(event, freshness)

    monkeypatch.setattr(main_mod, "get_scoreboard", fake_scoreboard)
    monkeypatch.setattr(main_mod, "get_sports_home_summary", fake_summary)
    monkeypatch.setattr(main_mod, "get_game_detail", fake_game)


def test_sports_home_and_navigation_render(monkeypatch):
    install_fakes(monkeypatch)
    response = TestClient(main_mod.app).get("/sports")
    assert response.status_code == 200
    assert "Sports scores and news" in response.text
    assert "Who" in response.text and "playing now" in response.text
    assert "data-sports-news" not in response.text
    assert "Away Team" in response.text
    assert 'href="/sports"' in response.text
    assert 'data-game-id="nfl_123"' in response.text
    assert 'id="games-live"' in response.text
    assert "live-pill" in response.text
    assert "Week 3 · AWY 4-0 at HOM 3-0" in response.text
    assert 'class="team-star"' in response.text
    assert 'data-team-key="nfl:1"' in response.text
    assert 'data-team-key="nfl:2"' in response.text


def test_league_scoreboard_and_date_navigation(monkeypatch):
    install_fakes(monkeypatch)
    response = TestClient(main_mod.app).get("/sports/nfl?date=2026-08-19")
    assert response.status_code == 200
    assert "NFL" in response.text
    assert '/sports/nfl?date=2026-08-18' in response.text
    assert '/sports/nfl?date=2026-08-20' in response.text
    assert 'aria-current="page">NFL' in response.text
    assert "data-sports-news" in response.text
    assert "More NFL news" in response.text
    assert "/search?q=NFL" in response.text


def test_scoreboard_api_is_normalized(monkeypatch):
    install_fakes(monkeypatch)
    response = TestClient(main_mod.app).get("/api/sports/scoreboard?league=nfl&date=2026-08-19")
    assert response.status_code == 200
    body = response.json()
    assert body["available"] is True
    assert body["stale"] is False
    assert body["events"][0]["state"] == "in_progress"
    assert body["events"][0]["home_score_display"] == "20"
    assert body["groups"]["live"][0]["id"] == "nfl_123"
    assert body["groups"]["final"] == []


def test_sports_headlines_api_uses_league_query(monkeypatch):
    async def fake_headlines(query, limit=8):
        assert query == "NFL"
        return {"query": query, "headlines": [{"title": "NFL news", "url": "https://example.com/n", "source": "AP"}]}

    monkeypatch.setattr(main_mod, "get_sports_headlines", fake_headlines)
    response = TestClient(main_mod.app).get("/api/sports/headlines?league=nfl")
    assert response.status_code == 200
    assert response.json()["headlines"][0]["title"] == "NFL news"
    assert TestClient(main_mod.app).get("/api/sports/headlines?league=nope").status_code == 404


def test_game_center_renders_every_optional_branch(monkeypatch):
    install_fakes(monkeypatch)
    response = TestClient(main_mod.app).get("/sports/game/nfl_123")
    assert response.status_code == 200
    assert "Scoring summary" in response.text
    assert "Home field goal" in response.text
    assert "Total yards" in response.text
    assert "Pat Example" in response.text
    assert "Test Field" in response.text
    assert "ESPN" in response.text
    assert "2-1, 1 out" in response.text
    assert "Week 3 · AWY 4-0 at HOM 3-0" in response.text
    assert 'class="team-star"' in response.text
    assert "Batting" not in response.text or "Hits" in response.text
    assert "Follow Away Team in MyNews" in response.text
    assert "Follow Home Team in MyNews" in response.text
    assert 'data-news-away="Away Team"' in response.text
    assert "/search?q=" in response.text


def test_stale_scoreboard_is_labeled(monkeypatch):
    install_fakes(monkeypatch, freshness="stale")
    response = TestClient(main_mod.app).get("/sports/nfl")
    assert response.status_code == 200
    assert "Scores may be delayed" in response.text


def test_invalid_league_date_and_game(monkeypatch):
    install_fakes(monkeypatch)
    client = TestClient(main_mod.app)
    assert client.get("/sports/not-a-league").status_code == 404
    assert client.get("/sports/nfl?date=not-a-date").status_code == 422
    assert client.get("/api/sports/scoreboard?league=nope").status_code == 404
    assert client.get("/sports/game/nfl_missing").status_code == 503


def test_empty_boxscore_headers_are_omitted(monkeypatch):
    game = sample_event()
    game.team_stats = [
        {"label": "Batting", "away": "—", "home": "—"},
        {"label": "Hits", "away": "8", "home": "11"},
    ]
    install_fakes(monkeypatch, game=game)
    response = TestClient(main_mod.app).get("/sports/game/nfl_123")
    assert "Hits" in response.text
    assert "Batting" not in response.text


def test_scheduled_game_hides_zero_as_score(monkeypatch):
    game = sample_event(EventState.SCHEDULED)
    game.home_team.score = 0
    game.away_team.score = 0
    install_fakes(monkeypatch, game=game)
    response = TestClient(main_mod.app).get("/sports/nfl")
    assert response.status_code == 200
    assert response.text.count(">—<") >= 2
