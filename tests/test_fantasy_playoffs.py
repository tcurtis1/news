import json
from starlette.testclient import TestClient
import pytest

from app.fantasy.db import init_db
from app.fantasy.matchups import finalize_week, generate_league_schedule, record_player_stats
from app.fantasy.models import LeagueStatus
from app.fantasy.playoffs import (
    advance_playoff_round_after_week,
    generate_playoff_bracket,
    get_playoff_bracket,
    get_playoff_seedings,
)
from app.fantasy.service import create_league, join_league, update_league_settings
from app.main import app


@pytest.fixture
def isolated_fantasy_db(tmp_path, monkeypatch):
    test_db = tmp_path / "test_fantasy_playoffs.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()
    return test_db


@pytest.fixture
def isolated_analytics(tmp_path, monkeypatch):
    monkeypatch.setattr("app.analytics.CACHE_DIR", tmp_path)
    monkeypatch.setattr("app.analytics.STORE_PATH", tmp_path / "analytics.json")


@pytest.fixture
def client(isolated_fantasy_db, isolated_analytics):
    return TestClient(app)


def test_playoff_seedings_and_tiebreakers(isolated_fantasy_db):
    league, commish = create_league(
        name="Seeding League",
        manager_name="Commish",
        team_name="Team Alpha",
        max_teams=4,
    )
    t2_league, t2, _ = join_league(league.invite_token, "Manager B", "Team Beta")
    t3_league, t3, _ = join_league(league.invite_token, "Manager C", "Team Gamma")
    t4_league, t4, _ = join_league(league.invite_token, "Manager D", "Team Delta")

    from app.fantasy.db import get_connection
    conn = get_connection()
    with conn:
        # Team Alpha: 10-4, 1400 PF
        conn.execute("UPDATE teams SET wins = 10, losses = 4, points_for = 1400.0 WHERE id = ?;", (commish.id,))
        # Team Beta: 10-4, 1350 PF (tied record, lower PF)
        conn.execute("UPDATE teams SET wins = 10, losses = 4, points_for = 1350.0 WHERE id = ?;", (t2.id,))
        # Team Gamma: 7-7, 1200 PF
        conn.execute("UPDATE teams SET wins = 7, losses = 7, points_for = 1200.0 WHERE id = ?;", (t3.id,))
        # Team Delta: 5-9, 1100 PF
        conn.execute("UPDATE teams SET wins = 5, losses = 9, points_for = 1100.0 WHERE id = ?;", (t4.id,))
    conn.close()

    seeds = get_playoff_seedings(league.id)
    assert len(seeds) == 4
    assert seeds[0]["team_id"] == commish.id
    assert seeds[0]["seed"] == 1
    assert seeds[1]["team_id"] == t2.id
    assert seeds[1]["seed"] == 2
    assert seeds[2]["team_id"] == t3.id
    assert seeds[2]["seed"] == 3
    assert seeds[3]["team_id"] == t4.id
    assert seeds[3]["seed"] == 4


def test_4_team_playoff_lifecycle(isolated_fantasy_db):
    league, commish = create_league(
        name="Championship League",
        manager_name="Commish",
        team_name="Team 1",
        max_teams=4,
    )
    _, t2, _ = join_league(league.invite_token, "Manager 2", "Team 2")
    _, t3, _ = join_league(league.invite_token, "Manager 3", "Team 3")
    _, t4, _ = join_league(league.invite_token, "Manager 4", "Team 4")

    # Set regular season to 2 weeks for fast test
    update_league_settings(league.id, league.commissioner_token, {
        "regular_season_weeks": 2,
        "playoff_teams": 4,
    })

    from app.fantasy.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute("UPDATE teams SET wins = 2, losses = 0, points_for = 250.0 WHERE id = ?;", (commish.id,))
        conn.execute("UPDATE teams SET wins = 1, losses = 1, points_for = 210.0 WHERE id = ?;", (t2.id,))
        conn.execute("UPDATE teams SET wins = 1, losses = 1, points_for = 190.0 WHERE id = ?;", (t3.id,))
        conn.execute("UPDATE teams SET wins = 0, losses = 2, points_for = 160.0 WHERE id = ?;", (t4.id,))
        conn.execute("UPDATE leagues SET current_week = 3 WHERE id = ?;", (league.id,))
    conn.close()

    # Generate playoff bracket for Week 3 (Semifinals)
    ok, msg, data = generate_playoff_bracket(league.id, league.commissioner_token)
    assert ok is True
    assert data["round1_week"] == 3
    assert len(data["created_matchups"]) == 2

    # Check matchups in week 3
    bracket = get_playoff_bracket(league.id)
    assert bracket["is_generated"] is True
    assert bracket["status"] == LeagueStatus.PLAYOFFS.value
    assert len(bracket["rounds"]) == 1
    semis = bracket["rounds"][0]["matchups"]
    assert len(semis) == 2

    # Semi 1: Team 1 (Seed 1) vs Team 4 (Seed 4)
    semi1 = next(m for m in semis if m["bracket_slot"] == "semi_1")
    assert semi1["home_seed"] == 1
    assert semi1["away_seed"] == 4
    assert semi1["home_team_id"] == commish.id
    assert semi1["away_team_id"] == t4.id

    # Semi 2: Team 2 (Seed 2) vs Team 3 (Seed 3)
    semi2 = next(m for m in semis if m["bracket_slot"] == "semi_2")
    assert semi2["home_seed"] == 2
    assert semi2["away_seed"] == 3
    assert semi2["home_team_id"] == t2.id
    assert semi2["away_team_id"] == t3.id

    # Simulate scores for Week 3:
    # Semi 1: Team 1 (120) beats Team 4 (90)
    # Semi 2: Team 3 (115) upsets Team 2 (110)
    conn = get_connection()
    with conn:
        conn.execute("UPDATE matchups SET home_score = 120.0, away_score = 90.0 WHERE id = ?;", (semi1["id"],))
        conn.execute("UPDATE matchups SET home_score = 110.0, away_score = 115.0 WHERE id = ?;", (semi2["id"],))
    conn.close()

    # Finalize Week 3
    ok_fin, _ = finalize_week(league.id, 3, league.commissioner_token)
    assert ok_fin is True

    # Check advancement to Week 4 (Finals)
    bracket_finals = get_playoff_bracket(league.id)
    assert len(bracket_finals["rounds"]) == 2
    finals_round = bracket_finals["rounds"][1]
    assert finals_round["week"] == 4

    champ_match = next(m for m in finals_round["matchups"] if m["bracket_slot"] == "championship")
    third_match = next(m for m in finals_round["matchups"] if m["bracket_slot"] == "third_place")

    # Championship: Team 1 (Seed 1) vs Team 3 (Seed 3)
    assert champ_match["home_team_id"] == commish.id
    assert champ_match["away_team_id"] == t3.id

    # 3rd Place: Team 2 (Seed 2) vs Team 4 (Seed 4)
    assert third_match["home_team_id"] == t2.id
    assert third_match["away_team_id"] == t4.id

    # Simulate scores for Championship Week (Week 4):
    # Championship: Team 1 (130) beats Team 3 (105) -> Team 1 is CHAMPION!
    # 3rd Place: Team 2 (112) beats Team 4 (95)
    conn = get_connection()
    with conn:
        conn.execute("UPDATE matchups SET home_score = 130.0, away_score = 105.0 WHERE id = ?;", (champ_match["id"],))
        conn.execute("UPDATE matchups SET home_score = 112.0, away_score = 95.0 WHERE id = ?;", (third_match["id"],))
    conn.close()

    # Finalize Week 4 (Championship Week)
    ok_champ, _ = finalize_week(league.id, 4, league.commissioner_token)
    assert ok_champ is True

    # Verify League is Completed and Champions Crowned
    bracket_complete = get_playoff_bracket(league.id)
    assert bracket_complete["status"] == LeagueStatus.COMPLETE.value
    assert bracket_complete["podium"]["champion"]["id"] == commish.id
    assert bracket_complete["podium"]["runner_up"]["id"] == t3.id
    assert bracket_complete["podium"]["third_place"]["id"] == t2.id
    assert bracket_complete["podium"]["sacko"]["id"] == t4.id


def test_6_team_playoffs_with_byes(isolated_fantasy_db):
    league, commish = create_league(
        name="6-Team Playoff League",
        manager_name="Commish",
        team_name="Team 1",
        max_teams=6,
    )
    _, t2, _ = join_league(league.invite_token, "M2", "Team 2")
    _, t3, _ = join_league(league.invite_token, "M3", "Team 3")
    _, t4, _ = join_league(league.invite_token, "M4", "Team 4")
    _, t5, _ = join_league(league.invite_token, "M5", "Team 5")
    _, t6, _ = join_league(league.invite_token, "M6", "Team 6")

    update_league_settings(league.id, league.commissioner_token, {
        "regular_season_weeks": 2,
        "playoff_teams": 6,
    })

    from app.fantasy.db import get_connection
    conn = get_connection()
    with conn:
        conn.execute("UPDATE teams SET wins = 6, points_for = 600 WHERE id = ?;", (commish.id,))  # Seed 1
        conn.execute("UPDATE teams SET wins = 5, points_for = 550 WHERE id = ?;", (t2.id,))        # Seed 2
        conn.execute("UPDATE teams SET wins = 4, points_for = 500 WHERE id = ?;", (t3.id,))        # Seed 3
        conn.execute("UPDATE teams SET wins = 3, points_for = 450 WHERE id = ?;", (t4.id,))        # Seed 4
        conn.execute("UPDATE teams SET wins = 2, points_for = 400 WHERE id = ?;", (t5.id,))        # Seed 5
        conn.execute("UPDATE teams SET wins = 1, points_for = 350 WHERE id = ?;", (t6.id,))        # Seed 6
        conn.execute("UPDATE leagues SET current_week = 3 WHERE id = ?;", (league.id,))
    conn.close()

    ok, msg, data = generate_playoff_bracket(league.id, league.commissioner_token)
    assert ok is True
    assert data["playoff_teams"] == 6

    bracket = get_playoff_bracket(league.id)
    round1 = bracket["rounds"][0]["matchups"]
    # Quarterfinals: 4 vs 5, and 3 vs 6
    assert len(round1) == 2
    q1 = next(m for m in round1 if m["bracket_slot"] == "quarter_1")
    q2 = next(m for m in round1 if m["bracket_slot"] == "quarter_2")
    assert q1["home_team_id"] == t4.id
    assert q1["away_team_id"] == t5.id
    assert q2["home_team_id"] == t3.id
    assert q2["away_team_id"] == t6.id

    # Simulate Quarterfinals: Team 4 wins Q1, Team 3 wins Q2
    conn = get_connection()
    with conn:
        conn.execute("UPDATE matchups SET home_score = 110, away_score = 90 WHERE id = ?;", (q1["id"],))
        conn.execute("UPDATE matchups SET home_score = 115, away_score = 85 WHERE id = ?;", (q2["id"],))
    conn.close()

    # Finalize Quarterfinals (Week 3)
    finalize_week(league.id, 3, league.commissioner_token)

    # Check Semifinals (Week 4): Seed 1 plays winner Q1 (or lower seed), Seed 2 plays winner Q2
    bracket_semis = get_playoff_bracket(league.id)
    assert len(bracket_semis["rounds"]) == 2
    semis = bracket_semis["rounds"][1]["matchups"]
    assert len(semis) == 2
    semi_home_teams = [m["home_team_id"] for m in semis]
    assert commish.id in semi_home_teams  # Seed 1
    assert t2.id in semi_home_teams       # Seed 2


def test_playoff_routes_and_api(client):
    from app.fantasy.service import create_league, join_league
    league, commish = create_league("Route Playoff League", "Commish", "Team Alpha", max_teams=4)
    join_league(league.invite_token, "User 2", "Team Beta")

    # 1. GET HTML playoffs view
    resp = client.get(f"/sports/fantasy/league/{league.id}/playoffs")
    assert resp.status_code == 200
    assert "Playoffs &amp; Championship" in resp.text or "Playoffs & Championship" in resp.text
    assert "Team Alpha" in resp.text

    # 2. GET JSON playoffs API
    api_resp = client.get(f"/sports/fantasy/api/league/{league.id}/playoffs")
    assert api_resp.status_code == 200
    data = api_resp.json()
    assert "seedings" in data
    assert "rounds" in data

    # 3. Unauthorized POST generate
    unauth_resp = client.post(f"/sports/fantasy/api/league/{league.id}/playoffs/generate")
    assert unauth_resp.status_code == 403

    # 4. Authorized POST generate (with commish cookie)
    client.cookies.set("yoyo_fantasy_tokens", json.dumps([commish.manager_token]))
    gen_resp = client.post(f"/sports/fantasy/api/league/{league.id}/playoffs/generate")
    assert gen_resp.status_code == 200
    gen_json = gen_resp.json()
    assert gen_json["success"] is True

    # 5. Check HTML view after generation
    resp_after = client.get(f"/sports/fantasy/league/{league.id}/playoffs")
    assert resp_after.status_code == 200
    assert "Championship Tournament Tree" in resp_after.text
