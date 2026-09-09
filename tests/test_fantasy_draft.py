import json
import pytest
from datetime import datetime, timezone, timedelta

from app.fantasy.db import init_db, get_connection
from app.fantasy.models import LeagueStatus
from app.fantasy.service import create_league, get_league, join_league
from app.fantasy.draft import (
    calculate_snake_pick,
    determine_roster_slot,
    get_draft_board,
    get_draft_status,
    get_team_roster,
    make_draft_pick,
    manage_draft_queue,
    pause_draft,
    reset_draft,
    resume_draft,
    set_draft_order,
    start_draft,
    undo_last_pick,
    auto_pick_if_timed_out,
)


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_draft.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_calculate_snake_pick():
    teams = ["A", "B", "C", "D"]
    # Round 1 (Odd: 1..4)
    assert calculate_snake_pick(teams, 1) == (1, 1, "A")
    assert calculate_snake_pick(teams, 2) == (1, 2, "B")
    assert calculate_snake_pick(teams, 3) == (1, 3, "C")
    assert calculate_snake_pick(teams, 4) == (1, 4, "D")

    # Round 2 (Even: 4..1)
    assert calculate_snake_pick(teams, 5) == (2, 1, "D")
    assert calculate_snake_pick(teams, 6) == (2, 2, "C")
    assert calculate_snake_pick(teams, 7) == (2, 3, "B")
    assert calculate_snake_pick(teams, 8) == (2, 4, "A")

    # Round 3 (Odd: 1..4)
    assert calculate_snake_pick(teams, 9) == (3, 1, "A")
    assert calculate_snake_pick(teams, 12) == (3, 4, "D")


def test_determine_roster_slot():
    # Test slot progression
    slots = []
    s1 = determine_roster_slot("RB", slots)
    assert s1 == "RB1"
    slots.append(s1)

    s2 = determine_roster_slot("RB", slots)
    assert s2 == "RB2"
    slots.append(s2)

    s3 = determine_roster_slot("RB", slots)
    assert s3 == "FLEX"
    slots.append(s3)

    s4 = determine_roster_slot("RB", slots)
    assert s4 == "BENCH"


def test_start_pause_resume_draft():
    league, commish_team = create_league("Gridiron 4", "Tony", "T1", max_teams=4)
    # Cannot start with only 1 team
    _, err_early = start_draft(league.id, league.commissioner_token)
    assert "at least 2 teams" in err_early

    # Join team 2
    join_league(league.invite_token, "Alice", "T2")

    # Start draft
    status, err = start_draft(league.id, league.commissioner_token)
    assert err is None
    assert status["status"] == LeagueStatus.DRAFTING.value
    assert status["overall_pick"] == 1
    assert status["remaining_seconds"] is not None
    assert status["on_the_clock"]["id"] == commish_team.id

    # Pause draft
    p_status, p_err = pause_draft(league.id, league.commissioner_token)
    assert p_err is None
    assert p_status["is_paused"] is True
    assert p_status["status"] == LeagueStatus.DRAFT_PAUSED.value

    # Resume draft
    r_status, r_err = resume_draft(league.id, league.commissioner_token)
    assert r_err is None
    assert r_status["is_paused"] is False
    assert r_status["status"] == LeagueStatus.DRAFTING.value


def test_make_draft_pick_and_slotting():
    league, commish_team = create_league("Draft League", "Tony", "Team Tony", max_teams=2)
    _, team2, _ = join_league(league.invite_token, "Bob", "Team Bob")

    # Start draft
    start_draft(league.id, league.commissioner_token)

    # Pick 1: Tony's turn, draft Christian McCaffrey (RB)
    pick1, err1 = make_draft_pick(
        league_id=league.id,
        player_id="nfl-3117251",  # CMC
        manager_token=commish_team.manager_token,
    )
    assert err1 is None
    assert pick1.overall_pick == 1
    assert pick1.round == 1
    assert pick1.pick_number == 1
    assert pick1.player.name == "Christian McCaffrey"

    # Verify CMC slotted into RB1 on Tony's roster
    roster_t1 = get_team_roster(commish_team.id)
    assert len(roster_t1) == 1
    assert roster_t1[0]["slot"] == "RB1"

    # Pick 2: Now it's Bob's turn! Tony trying to pick again fails
    _, unauth_err = make_draft_pick(
        league_id=league.id,
        player_id="nfl-4262921",  # CeeDee Lamb
        manager_token=commish_team.manager_token,
    )
    assert unauth_err is not None
    assert "Unauthorized" in unauth_err

    # Attempt to draft already drafted player (CMC) fails
    _, dup_err = make_draft_pick(
        league_id=league.id,
        player_id="nfl-3117251",
        manager_token=team2.manager_token,
    )
    assert dup_err is not None
    assert "already been drafted" in dup_err

    # Bob drafts CeeDee Lamb (WR)
    pick2, err2 = make_draft_pick(
        league_id=league.id,
        player_id="nfl-4262921",
        manager_token=team2.manager_token,
    )
    assert err2 is None
    assert pick2.overall_pick == 2
    assert pick2.team_id == team2.id

    roster_t2 = get_team_roster(team2.id)
    assert len(roster_t2) == 1
    assert roster_t2[0]["slot"] == "WR1"


def test_draft_queue_and_auto_pick():
    league, commish_team = create_league("Auto League", "Tony", "Team Tony", max_teams=2)
    join_league(league.invite_token, "Bob", "Team Bob")
    start_draft(league.id, league.commissioner_token)

    # Tony queues Josh Allen
    queue = manage_draft_queue(commish_team.id, "add", "nfl-3918298")
    assert any(q["name"] == "Josh Allen" for q in queue)

    # Force auto pick for current team
    did_pick, pick = auto_pick_if_timed_out(league.id)
    assert did_pick is True
    assert pick.player.name == "Josh Allen"
    assert pick.is_auto_pick is True

    # Check that drafted player was removed from queue
    updated_queue = manage_draft_queue(commish_team.id, "list", "")
    assert not any(q["player_id"] == "nfl-3918298" for q in updated_queue)


def test_undo_last_pick():
    league, commish_team = create_league("Undo League", "Tony", "Team Tony", max_teams=2)
    _, team2, _ = join_league(league.invite_token, "Bob", "Team Bob")
    start_draft(league.id, league.commissioner_token)

    # Pick 1
    make_draft_pick(league.id, "nfl-3117251", manager_token=commish_team.manager_token)
    assert get_draft_status(league.id)["overall_pick"] == 2

    # Undo pick 1
    ok, err = undo_last_pick(league.id, league.commissioner_token)
    assert ok is True
    assert err is None
    status = get_draft_status(league.id)
    assert status["overall_pick"] == 1
    assert len(status["recent_picks"]) == 0
    assert len(get_team_roster(commish_team.id)) == 0


def test_full_draft_completion():
    league, commish_team = create_league("Short Draft", "Tony", "Team Tony", max_teams=2)
    _, team2, _ = join_league(league.invite_token, "Bob", "Team Bob")

    # Set total_rounds to 1 (2 total picks)
    conn = get_connection()
    with conn:
        s = league.settings
        s.total_rounds = 1
        conn.execute("UPDATE leagues SET settings_json = ? WHERE id = ?;", (json.dumps(s.to_dict()), league.id))
    conn.close()

    start_draft(league.id, league.commissioner_token)

    # Pick 1 (Tony)
    make_draft_pick(league.id, "nfl-3117251", manager_token=commish_team.manager_token)

    # Pick 2 (Bob)
    pick2, _ = make_draft_pick(league.id, "nfl-4262921", manager_token=team2.manager_token)
    assert pick2.overall_pick == 2

    # Draft should now be completed and in_season
    final_status = get_draft_status(league.id)
    assert final_status["is_completed"] is True
    assert final_status["status"] == LeagueStatus.IN_SEASON.value

    # Subsequent pick rejected
    _, over_err = make_draft_pick(league.id, "nfl-3918298", is_auto=True)
    assert over_err is not None
    assert "not active" in over_err


def test_set_and_randomize_draft_order():
    league, commish_team = create_league("Order League", "Tony", "Team Tony", max_teams=2)
    _, team2, _ = join_league(league.invite_token, "Bob", "Team Bob")

    # Reorder manually
    updated, err = set_draft_order(league.id, league.commissioner_token, order=[team2.id, commish_team.id])
    assert err is None
    assert updated.draft_order == [team2.id, commish_team.id]

    # Randomize
    rand_league, r_err = set_draft_order(league.id, league.commissioner_token, randomize=True)
    assert r_err is None
    assert set(rand_league.draft_order) == {commish_team.id, team2.id}


def test_get_draft_board():
    league, commish_team = create_league("Board League", "Tony", "Team Tony", max_teams=2)
    join_league(league.invite_token, "Bob", "Team Bob")
    start_draft(league.id, league.commissioner_token)
    make_draft_pick(league.id, "nfl-3117251", manager_token=commish_team.manager_token)

    board = get_draft_board(league.id)
    assert len(board["teams"]) == 2
    assert len(board["rounds"]) == 15
    # Round 1 pick 1 has CMC
    r1_p1 = board["rounds"][0]["picks"][0]
    assert r1_p1["player_name"] == "Christian McCaffrey"
