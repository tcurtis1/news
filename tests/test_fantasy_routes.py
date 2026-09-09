import json
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.fantasy.db import init_db
from app.fantasy.service import create_league, get_league


@pytest.fixture(autouse=True)
def isolated_analytics(monkeypatch, tmp_path):
    import app.analytics as analytics_mod
    monkeypatch.setattr(analytics_mod, "CACHE_DIR", tmp_path)
    monkeypatch.setattr(analytics_mod, "STORE_PATH", tmp_path / "analytics.json")


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_routes.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


@pytest.fixture
def client():
    return TestClient(app)


def test_fantasy_index_empty(client):
    response = client.get("/sports/fantasy")
    assert response.status_code == 200
    assert "Fantasy Football" in response.text
    assert "Create a League" in response.text


def test_fantasy_index_with_teams(client):
    league, commish_team = create_league("Championship League", "Tony", "Iron Men", max_teams=10)
    
    # Set manager cookie
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([commish_team.manager_token]))
    response = client.get("/sports/fantasy")
    assert response.status_code == 200
    assert "Championship League" in response.text
    assert "Iron Men" in response.text
    assert "Commissioner" in response.text


def test_fantasy_create_get(client):
    response = client.get("/sports/fantasy/create")
    assert response.status_code == 200
    assert "Create a Fantasy League" in response.text
    assert "0.5 PPR" in response.text
    assert "FAAB" in response.text


def test_fantasy_create_post(client):
    response = client.post(
        "/sports/fantasy/create",
        data={
            "league_name": "Sunday Blitz",
            "manager_name": "Tony",
            "team_name": "Blitzers",
            "max_teams": 12,
            "scoring_format": "half_ppr",
            "waiver_type": "faab",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    redirect_url = response.headers["location"]
    assert "/sports/fantasy/league/" in redirect_url
    assert "yoyo_fantasy_tokens" in response.cookies


def test_fantasy_join_lifecycle(client):
    league, commish_team = create_league("Mini League", "Tony", "Team A", max_teams=2)

    # View join page with valid invite
    resp = client.get(f"/sports/fantasy/join/{league.invite_token}")
    assert resp.status_code == 200
    assert "Join Mini League" in resp.text
    assert "1 remaining" in resp.text

    # Join successfully
    resp_join = client.post(
        f"/sports/fantasy/join/{league.invite_token}",
        data={"manager_name": "Alice", "team_name": "Team Alice"},
        follow_redirects=False,
    )
    assert resp_join.status_code == 303
    assert f"/sports/fantasy/league/{league.id}" in resp_join.headers["location"]
    assert "yoyo_fantasy_tokens" in resp_join.cookies

    # Verify team joined
    updated = get_league(league.id)
    assert len(updated.teams) == 2

    # View join page when full
    resp_full = client.get(f"/sports/fantasy/join/{league.invite_token}")
    assert resp_full.status_code == 200
    assert "League is Full" in resp_full.text

    # Attempt to join full league
    resp_overflow = client.post(
        f"/sports/fantasy/join/{league.invite_token}",
        data={"manager_name": "Bob", "team_name": "Team Bob"},
        follow_redirects=False,
    )
    assert resp_overflow.status_code == 400
    assert "League is full" in resp_overflow.text

    # Invalid invite token
    resp_404 = client.get("/sports/fantasy/join/invalid_token_xyz")
    assert resp_404.status_code == 404


def test_fantasy_league_hub_and_teams(client):
    league, commish_team = create_league("Alpha League", "Tony", "Team Alpha", max_teams=10)

    # Spectator / visitor view (no cookies)
    resp = client.get(f"/sports/fantasy/league/{league.id}")
    assert resp.status_code == 200
    assert "Alpha League" in resp.text
    assert "League Hub" in resp.text

    # Commissioner view (with cookie)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([commish_team.manager_token]))
    resp_commish = client.get(f"/sports/fantasy/league/{league.id}")
    assert resp_commish.status_code == 200
    assert "Invite Managers" in resp_commish.text
    assert "You are Commissioner" in resp_commish.text

    # Teams view
    resp_teams = client.get(f"/sports/fantasy/league/{league.id}/teams")
    assert resp_teams.status_code == 200
    assert "Team Alpha" in resp_teams.text

    # Missing league
    assert client.get("/sports/fantasy/league/nonexistent_id").status_code == 404


def test_fantasy_league_settings(client):
    league, commish_team = create_league("Beta League", "Tony", "Team Beta", max_teams=10)

    # View settings
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([commish_team.manager_token]))
    resp = client.get(f"/sports/fantasy/league/{league.id}/settings")
    assert resp.status_code == 200
    assert "Beta League" in resp.text

    # Update settings as commissioner
    post_resp = client.post(
        f"/sports/fantasy/league/{league.id}/settings",
        data={
            "name": "Beta League Pro",
            "scoring_format": "full_ppr",
            "max_teams": 12,
            "waiver_type": "rolling",
            "faab_budget": 200,
        },
        follow_redirects=False,
    )
    assert post_resp.status_code == 303
    assert f"/sports/fantasy/league/{league.id}/settings?saved=1" in post_resp.headers["location"]

    updated = get_league(league.id)
    assert updated.name == "Beta League Pro"
    assert updated.settings.scoring_format == "full_ppr"
    assert updated.settings.max_teams == 12
    assert updated.settings.waiver_type == "rolling"

    # Non-commissioner update rejected
    client.cookies.clear()
    unauth_resp = client.post(
        f"/sports/fantasy/league/{league.id}/settings",
        data={
            "name": "Hacked",
            "scoring_format": "standard",
            "max_teams": 8,
            "waiver_type": "faab",
            "faab_budget": 0,
        },
    )
    assert unauth_resp.status_code == 403


def test_fantasy_players_view(client):
    resp = client.get("/sports/fantasy/players")
    assert resp.status_code == 200
    assert "NFL Fantasy Player Directory" in resp.text

    # Filter by position
    resp_qb = client.get("/sports/fantasy/players?pos=QB")
    assert resp_qb.status_code == 200
    assert "Patrick Mahomes" in resp_qb.text

    # Search query
    resp_search = client.get("/sports/fantasy/players?q=Jefferson")
    assert resp_search.status_code == 200
    assert "Justin Jefferson" in resp_search.text


def test_fantasy_api_endpoints(client):
    league, commish = create_league("Gamma League", "Tony", "Team Gamma")

    # API league detail
    resp = client.get(f"/sports/fantasy/api/league/{league.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Gamma League"
    # Commissioner and manager tokens must be masked in API
    assert data["commissioner_token"] == "***"
    assert data["teams"][0]["manager_token"] == "***"

    # API 404
    assert client.get("/sports/fantasy/api/league/unknown").status_code == 404

    # API players
    resp_players = client.get("/sports/fantasy/api/players?q=Lamar")
    assert resp_players.status_code == 200
    pdata = resp_players.json()
    assert "players" in pdata
    assert any("Lamar Jackson" in p["name"] for p in pdata["players"])
