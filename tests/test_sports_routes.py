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
    assert 'name="robots" content="index,follow"' in response.text
    assert "noindex" not in response.text
    assert "Share" in response.text
    assert 'data-share-line="AWY 17 @ HOM 20, 4th 4:21"' in response.text


def test_league_scoreboard_and_date_navigation(monkeypatch):
    install_fakes(monkeypatch)
    response = TestClient(main_mod.app).get("/sports/nfl?date=2026-08-19")
    assert response.status_code == 200
    assert "NFL" in response.text
    assert '/sports/nfl?date=2026-08-18' in response.text
    assert '/sports/nfl?date=2026-08-20' in response.text
    assert 'data-date-explicit="1"' in response.text
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
    assert body["events"][0]["share_line"] == "AWY 17 @ HOM 20, 4th 4:21"


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
    assert 'href="/my"' in response.text
    assert 'data-news-away="Away Team"' in response.text
    assert "/search?q=" in response.text
    assert 'name="robots" content="noindex,follow"' in response.text
    assert 'data-share-line="AWY 17 @ HOM 20, 4th 4:21"' in response.text
    assert "Share score" in response.text


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


def _rankings_payload():
    from datetime import datetime, timezone
    from app.sports import SportsPayload
    return SportsPayload(
        updated_at=datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc),
        freshness="fresh", provider_label="espn",
        data={
            "available": True, "league": "cfb", "poll": "ap", "poll_name": "AP Top 25",
            "week_label": "Preseason",
            "polls": [{"type": "ap", "name": "AP Poll"}, {"type": "usa", "name": "AFCA Coaches Poll"}],
            "teams": [{
                "current": 1, "previous_label": "NR", "trend": "new", "record": "0-0",
                "team_id": "194", "name": "Ohio State", "abbreviation": "OSU",
                "news_query": "Ohio State college football",
            }],
        },
    )


def test_college_top25_page_and_api(monkeypatch):
    install_fakes(monkeypatch)

    async def fake_rankings(league, poll=None):
        assert league == "cfb"
        return _rankings_payload()

    monkeypatch.setattr(main_mod, "get_rankings", fake_rankings)
    client = TestClient(main_mod.app)
    page = client.get("/sports/cfb/top25")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    assert "AP Top 25" in page.text
    assert "Ohio State" in page.text
    assert "rank-row" in page.text
    assert "Top 25" in page.text
    assert 'href="/sports/cfb/top25"' in page.text
    assert 'href="/search?q=Ohio%20State%20college%20football"' in page.text
    assert 'data-team-key="cfb:194"' in page.text
    assert "AFCA Coaches Poll" in page.text
    assert "Couldn’t load the AP Top 25" not in page.text
    scores = client.get("/sports/cfb")
    assert scores.status_code == 200
    assert 'href="/sports/cfb/top25"' in scores.text
    assert "Top 25" in scores.text
    nfl = client.get("/sports/nfl")
    assert 'href="/sports/nfl/top25"' not in nfl.text
    assert client.get("/sports/nfl/top25").status_code == 404
    api = client.get("/api/sports/rankings?league=cfb")
    assert api.status_code == 200
    assert api.json()["teams"][0]["name"] == "Ohio State"
    assert client.get("/api/sports/rankings?league=nfl").status_code == 404


def test_college_top25_empty_is_honest(monkeypatch):
    install_fakes(monkeypatch)
    from datetime import datetime, timezone
    from app.sports import SportsPayload

    async def fake_rankings(league, poll=None):
        return SportsPayload(
            updated_at=datetime(2026, 9, 5, 15, 0, tzinfo=timezone.utc),
            freshness="fallback", provider_label="fallback",
            data={"available": False, "league": league, "poll": "", "poll_name": "Top 25",
                  "week_label": "", "polls": [], "teams": []},
        )

    monkeypatch.setattr(main_mod, "get_rankings", fake_rankings)
    page = TestClient(main_mod.app).get("/sports/mcbb/top25")
    assert page.status_code == 200
    assert "Couldn’t load the AP Top 25" in page.text
    assert "rank-row" not in page.text


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


def test_implicit_date_nav_uses_site_timezone_not_utc(monkeypatch):
    install_fakes(monkeypatch)
    monkeypatch.setattr(main_mod, "TZ_NAME", "America/Denver")
    monkeypatch.setattr(
        main_mod,
        "_sports_now",
        lambda tz: datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc).astimezone(tz),
    )
    seen = {}
    event = sample_event()

    async def fake_scoreboard(league, target_date=None):
        seen["date"] = target_date
        return payload([event])

    monkeypatch.setattr(main_mod, "get_scoreboard", fake_scoreboard)
    response = TestClient(main_mod.app).get("/sports/nfl")
    assert response.status_code == 200
    assert seen.get("date") is None
    assert "/sports/nfl?date=2026-09-01" in response.text
    assert "/sports/nfl?date=2026-09-03" in response.text
    assert "2026-09-04" not in response.text
    assert "September 2" in response.text
    assert 'data-date-explicit="0"' in response.text


def test_date_nav_honors_timezone_cookie(monkeypatch):
    install_fakes(monkeypatch)
    monkeypatch.setattr(main_mod, "TZ_NAME", "America/Denver")
    monkeypatch.setattr(
        main_mod,
        "_sports_now",
        lambda tz: datetime(2026, 9, 3, 5, 0, tzinfo=timezone.utc).astimezone(tz),
    )
    client = TestClient(main_mod.app)
    client.cookies.set("yoyonews_tz", "Europe/London")
    response = client.get("/sports/nfl")
    assert response.status_code == 200
    assert "September 3" in response.text
    assert "/sports/nfl?date=2026-09-02" in response.text
    assert "/sports/nfl?date=2026-09-04" in response.text


def test_robots_disallow_game_pages():
    response = TestClient(main_mod.app).get("/robots.txt")
    assert response.status_code == 200
    assert "Disallow: /sports/game/" in response.text
    assert "Disallow: /admin/" in response.text


def _standings_payload():
    return SportsPayload(
        updated_at=datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc),
        freshness="fresh",
        provider_label="espn",
        data={
            "league": "mlb",
            "available": True,
            "divisions": [{
                "name": "AL East",
                "teams": [
                    {
                        "id": "10",
                        "name": "New York Yankees",
                        "abbreviation": "NYY",
                        "logo": "https://a.espncdn.com/nyy.png",
                        "wins": "82",
                        "losses": "52",
                        "ties": None,
                        "pct": ".612",
                        "gb": "-",
                        "diff": "+115",
                        "streak": "W4",
                        "l10": "7-3",
                        "points": None,
                        "clinch": "e",
                        "news_query": "NYY New York Yankees",
                    },
                ],
            }],
        },
    )


def test_standings_route_and_api(monkeypatch):
    install_fakes(monkeypatch)

    async def fake_standings(league):
        assert league == "mlb"
        return _standings_payload()

    monkeypatch.setattr(main_mod, "get_standings", fake_standings)
    client = TestClient(main_mod.app)

    # HTML route
    page = client.get("/sports/mlb/standings")
    assert page.status_code == 200
    assert page.headers.get("cache-control") == "no-store"
    assert "MLB Standings" in page.text or "Major League Baseball" in page.text
    assert "AL East" in page.text
    assert "New York Yankees" in page.text
    assert "NYY" in page.text
    assert "standings-table" in page.text
    assert "https://a.espncdn.com/nyy.png" in page.text
    assert 'data-team-key="mlb:10"' in page.text
    assert 'class="team-star"' in page.text
    assert "/sports/mlb" in page.text
    assert "/sports/mlb/standings" in page.text

    # 404 for league without standings
    assert client.get("/sports/cfb/standings").status_code == 404

    # API route
    api = client.get("/api/sports/standings?league=mlb")
    assert api.status_code == 200
    body = api.json()
    assert body["available"] is True
    assert body["divisions"][0]["name"] == "AL East"
    assert body["divisions"][0]["teams"][0]["abbreviation"] == "NYY"
    assert body["divisions"][0]["teams"][0]["logo"] == "https://a.espncdn.com/nyy.png"

    # API 404 for invalid league
    assert client.get("/api/sports/standings?league=cfb").status_code == 404


def test_live_baseball_situation_renders_diamond_and_count(monkeypatch):
    baseball_game = sample_event(EventState.IN_PROGRESS)
    baseball_game.league = "mlb"
    baseball_game.outs = 2
    baseball_game.balls = 3
    baseball_game.strikes = 2
    baseball_game.on_first = True
    baseball_game.on_second = False
    baseball_game.on_third = True
    baseball_game.batter_name = "Aaron Judge"
    baseball_game.pitcher_name = "Brayan Bello"
    baseball_game.last_play = "Judge strikes out swinging."
    install_fakes(monkeypatch, game=baseball_game)

    client = TestClient(main_mod.app)
    scoreboard = client.get("/sports/mlb")
    assert scoreboard.status_code == 200
    assert "base-diamond" in scoreboard.text
    assert "is-occupied" in scoreboard.text
    assert "2 Outs" in scoreboard.text
    assert "Count 3-2" in scoreboard.text

    detail = client.get(f"/sports/game/{baseball_game.id}")
    assert detail.status_code == 200
    assert "game-situation-bar" in detail.text
    assert "base-diamond" in detail.text
    assert "2 Outs" in detail.text
    assert "Count 3-2" in detail.text
    assert "Aaron Judge" in detail.text
    assert "Brayan Bello" in detail.text
    assert "Judge strikes out swinging." in detail.text


def test_live_football_situation_renders_red_zone_and_down_distance(monkeypatch):
    football_game = sample_event(EventState.IN_PROGRESS)
    football_game.league = "nfl"
    football_game.down_distance = "3rd & 4 at BAL 18"
    football_game.possession_text = "KC ball"
    football_game.is_red_zone = True
    football_game.last_play = "Mahomes pass to Kelce for 6 yards."
    install_fakes(monkeypatch, game=football_game)

    client = TestClient(main_mod.app)
    scoreboard = client.get("/sports/nfl")
    assert scoreboard.status_code == 200
    assert "Red Zone" in scoreboard.text
    assert "3rd &amp; 4 at BAL 18" in scoreboard.text
    assert "KC ball" in scoreboard.text

    detail = client.get(f"/sports/game/{football_game.id}")
    assert detail.status_code == 200
    assert "game-situation-bar" in detail.text
    assert "Red Zone" in detail.text
    assert "3rd &amp; 4 at BAL 18" in detail.text
    assert "KC ball" in detail.text
    assert "Mahomes pass to Kelce" in detail.text


def test_team_logos_render_in_scoreboard_and_game(monkeypatch):
    game_with_logos = sample_event()
    game_with_logos.away_team.logo = "https://a.espncdn.com/away.png"
    game_with_logos.home_team.logo = "https://a.espncdn.com/home.png"
    install_fakes(monkeypatch, game=game_with_logos)

    client = TestClient(main_mod.app)
    scoreboard = client.get("/sports/nfl")
    assert scoreboard.status_code == 200
    assert 'class="team-logo"' in scoreboard.text
    assert "https://a.espncdn.com/away.png" in scoreboard.text
    assert "https://a.espncdn.com/home.png" in scoreboard.text

    detail = client.get(f"/sports/game/{game_with_logos.id}")
    assert detail.status_code == 200
    assert 'class="team-logo-lg"' in detail.text
    assert "https://a.espncdn.com/away.png" in detail.text
    assert "https://a.espncdn.com/home.png" in detail.text


@pytest.mark.anyio
async def test_sitemap_includes_standings_routes():
    from app.seo import collect_sitemap_urls
    urls = await collect_sitemap_urls()
    locs = {u["loc"] for u in urls}
    assert "https://news.yoyosup.com/sports/mlb/standings" in locs
    assert "https://news.yoyosup.com/sports/nfl/standings" in locs
    assert "https://news.yoyosup.com/sports/nba/standings" in locs
    assert "https://news.yoyosup.com/sports/nhl/standings" in locs
    assert "https://news.yoyosup.com/sports/epl/standings" in locs

