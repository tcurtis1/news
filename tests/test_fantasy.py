import pytest
import sqlite3
import tempfile
from pathlib import Path

from app.fantasy.models import (
    DEFAULT_ROSTER_SLOTS,
    FantasyTeam,
    League,
    LeagueSettings,
    LeagueStatus,
    Player,
    ScoringFormat,
)
from app.fantasy.db import DB_PATH, get_connection, init_db
from app.fantasy.service import (
    create_league,
    get_audit_log,
    get_league,
    get_league_by_invite,
    get_players,
    get_user_teams,
    join_league,
    update_league_settings,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_models_serialization():
    settings = LeagueSettings(scoring_format="full_ppr", max_teams=12)
    s_dict = settings.to_dict()
    assert s_dict["scoring_format"] == "full_ppr"
    assert s_dict["max_teams"] == 12
    assert s_dict["roster_slots"]["QB"] == 1

    loaded = LeagueSettings.from_dict(s_dict)
    assert loaded.scoring_format == "full_ppr"
    assert loaded.max_teams == 12

    p = Player(id="p1", name="Justin Jefferson", position="WR", nfl_team="MIN", bye_week=6, adp=4.0, projected_points=320.0)
    p_dict = p.to_dict()
    assert p_dict["name"] == "Justin Jefferson"
    assert p_dict["position"] == "WR"


def test_create_and_get_league():
    league, commish_team = create_league("Basement Bowl", "Tony", "Iron Curtain", max_teams=10, scoring_format="half_ppr")
    assert league.name == "Basement Bowl"
    assert league.settings.scoring_format == "half_ppr"
    assert league.settings.max_teams == 10
    assert commish_team.name == "Iron Curtain"
    assert commish_team.manager_name == "Tony"
    assert commish_team.is_commissioner is True
    assert commish_team.waiver_priority == 1
    assert commish_team.faab_balance == 100

    fetched = get_league(league.id)
    assert fetched is not None
    assert fetched.name == "Basement Bowl"
    assert len(fetched.teams) == 1
    assert fetched.teams[0].name == "Iron Curtain"


def test_join_league_and_priority():
    league, _ = create_league("Basement Bowl", "Tony", "Iron Curtain", max_teams=4)
    invite = league.invite_token

    # Manager 2 joins
    l2, team2, err2 = join_league(invite, "Alesia", "Wildcats")
    assert err2 is None
    assert team2 is not None
    assert team2.name == "Wildcats"
    assert team2.manager_name == "Alesia"
    assert team2.is_commissioner is False
    assert team2.waiver_priority == 2

    # Manager 3 joins
    l3, team3, err3 = join_league(invite, "Dave", "Destroyers")
    assert err3 is None
    assert team3.waiver_priority == 3

    # Manager 4 joins
    l4, team4, err4 = join_league(invite, "Sam", "Strikers")
    assert err4 is None
    assert team4.waiver_priority == 4

    # 5th manager fails because max_teams=4
    l5, team5, err5 = join_league(invite, "Extra", "Too Late")
    assert team5 is None
    assert "League is full" in err5


def test_update_league_settings():
    league, commish_team = create_league("Old Name", "Tony", "Team", max_teams=10)
    updated, err = update_league_settings(
        league.id,
        commissioner_token=league.commissioner_token,
        new_settings={"name": "New Name", "scoring_format": "standard", "max_teams": 12},
    )
    assert err is None
    assert updated.name == "New Name"
    assert updated.settings.scoring_format == "standard"
    assert updated.settings.max_teams == 12

    # Invalid commissioner token rejected
    _, bad_err = update_league_settings(league.id, "wrong_token", {"name": "Hacked"})
    assert bad_err is not None
    assert "Unauthorized" in bad_err


def test_get_players_search_and_filter():
    # Players were seeded in fixture
    all_players = get_players(limit=100)
    assert len(all_players) > 10

    qbs = get_players(position="QB")
    assert all(p.position == "QB" for p in qbs)
    assert any("Mahomes" in p.name for p in qbs)

    wrs = get_players(position="WR")
    assert all(p.position == "WR" for p in wrs)

    search = get_players(query="Jefferson")
    assert any("Jefferson" in p.name for p in search)


def test_user_teams_and_audit_log():
    league1, t1 = create_league("L1", "Tony", "T1")
    league2, t2 = create_league("L2", "Tony", "T2")

    my_teams = get_user_teams([t1.manager_token, t2.manager_token])
    assert len(my_teams) == 2
    assert {t["league_name"] for t in my_teams} == {"L1", "L2"}

    audit = get_audit_log(league1.id)
    assert len(audit) >= 1
    assert "Created league" in audit[0].description
