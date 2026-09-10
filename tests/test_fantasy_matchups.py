"""Unit and integration tests for fantasy matchups, weekly schedules, lineup management, and scoring."""

import pytest
from app.fantasy.db import init_db, get_connection
from app.fantasy.models import PlayerGameStats, ScoringFormat
from app.fantasy.scoring import (
    calculate_dst_points_allowed_score,
    calculate_player_points,
    calculate_win_probability,
    format_player_stats_summary,
    get_scoring_rules,
)
from app.fantasy.matchups import (
    ensure_lineups_for_week,
    finalize_week,
    generate_league_schedule,
    get_league_matchups_for_week,
    get_matchup_details,
    get_team_lineup,
    is_position_eligible,
    record_player_stats,
    simulate_week_stats,
    swap_lineup_slots,
)
from app.fantasy.service import create_league, join_league


@pytest.fixture(autouse=True)
def isolated_db(monkeypatch, tmp_path):
    test_db = tmp_path / "test_fantasy_matchups.db"
    monkeypatch.setattr("app.fantasy.db.DB_PATH", test_db)
    init_db()


def test_scoring_rules_and_formats():
    half = get_scoring_rules("half_ppr")
    full = get_scoring_rules("full_ppr")
    std = get_scoring_rules("standard")

    assert half["rec"] == 0.5
    assert full["rec"] == 1.0
    assert std["rec"] == 0.0
    assert half["pass_td"] == 4.0
    assert half["rush_td"] == 6.0


def test_calculate_player_points():
    # QB: 250 yds (10 pts), 2 td (8 pts), 1 int (-2 pts), 20 rush yds (2 pts) = 18.0 pts
    qb_stats = PlayerGameStats(
        id="s1", player_id="p1", season=2026, week=1,
        pass_yd=250, pass_td=2, pass_int=1, rush_yd=20, rush_td=0,
    )
    assert calculate_player_points(qb_stats, "half_ppr", position="QB") == 18.0

    # RB: 80 rush yds (8 pts), 1 rush td (6 pts), 4 rec (2 pts in half-ppr), 30 rec yds (3 pts) = 19.0 pts
    rb_stats = PlayerGameStats(
        id="s2", player_id="p2", season=2026, week=1,
        rush_yd=80, rush_td=1, rec=4, rec_yd=30,
    )
    assert calculate_player_points(rb_stats, "half_ppr", position="RB") == 19.0
    assert calculate_player_points(rb_stats, "full_ppr", position="RB") == 21.0
    assert calculate_player_points(rb_stats, "standard", position="RB") == 17.0

    # Kicker: 2 FG (6 pts), 3 XP (3 pts) = 9.0 pts
    k_stats = PlayerGameStats(
        id="s3", player_id="p3", season=2026, week=1,
        fg_made=2, pat_made=3,
    )
    assert calculate_player_points(k_stats, "half_ppr", position="K") == 9.0

    # DST: 3 sacks (3 pts), 2 INT (4 pts), 1 fumble rec (2 pts), 10 pts allowed bracket (4 pts) = 13.0 pts
    dst_stats = PlayerGameStats(
        id="s4", player_id="p4", season=2026, week=1,
        dst_sack=3, dst_int=2, dst_fumble_rec=1, dst_points_allowed=10,
    )
    assert calculate_player_points(dst_stats, "half_ppr", position="DST") == 13.0


def test_dst_points_allowed_brackets():
    rules = get_scoring_rules("half_ppr")
    assert calculate_dst_points_allowed_score(0, rules) == 10.0
    assert calculate_dst_points_allowed_score(3, rules) == 7.0
    assert calculate_dst_points_allowed_score(10, rules) == 4.0
    assert calculate_dst_points_allowed_score(17, rules) == 1.0
    assert calculate_dst_points_allowed_score(24, rules) == 0.0
    assert calculate_dst_points_allowed_score(31, rules) == -1.0
    assert calculate_dst_points_allowed_score(42, rules) == -4.0


def test_format_player_stats_summary():
    qb_stats = PlayerGameStats(
        id="s1", player_id="p1", season=2026, week=1,
        pass_yd=284, pass_td=2, pass_int=1,
    )
    summary = format_player_stats_summary(qb_stats, "QB")
    assert "284 YDS" in summary
    assert "2 TD" in summary
    assert "1 INT" in summary

    rb_stats = PlayerGameStats(
        id="s2", player_id="p2", season=2026, week=1,
        rush_yd=85, rush_td=1, rec=3, rec_yd=24,
    )
    rb_summary = format_player_stats_summary(rb_stats, "RB")
    assert "85 RUSH" in rb_summary
    assert "1 TD" in rb_summary
    assert "3 REC" in rb_summary


def test_calculate_win_probability():
    # Final game
    hp, ap = calculate_win_probability(100.0, 90.0, is_final=True, home_actual=105.0, away_actual=95.0)
    assert hp == 100
    assert ap == 0

    # In progress / projection
    hp2, ap2 = calculate_win_probability(home_projected_total=130.0, away_projected_total=100.0)
    assert hp2 > 80
    assert hp2 + ap2 == 100


def test_is_position_eligible():
    assert is_position_eligible("QB", "QB") is True
    assert is_position_eligible("RB", "QB") is False
    assert is_position_eligible("RB", "RB1") is True
    assert is_position_eligible("RB", "RB2") is True
    assert is_position_eligible("RB", "FLEX") is True
    assert is_position_eligible("WR", "FLEX") is True
    assert is_position_eligible("TE", "FLEX") is True
    assert is_position_eligible("QB", "FLEX") is False
    assert is_position_eligible("K", "K") is True
    assert is_position_eligible("DST", "DST") is True
    assert is_position_eligible("QB", "BENCH") is True
    assert is_position_eligible("WR", "BENCH") is True


def test_generate_league_schedule_two_teams():
    league, t1 = create_league("Heads Up League", "Tony", "Iron Men", max_teams=2)
    _, t2, _ = join_league(league.invite_token, "Alesia", "Alesia Stars")

    matchups = generate_league_schedule(league.id, total_weeks=14)
    assert len(matchups) == 14

    weeks = [m.week for m in matchups]
    assert weeks == list(range(1, 15))

    # Alternate home and away
    assert matchups[0].home_team_id == t1.id
    assert matchups[0].away_team_id == t2.id
    assert matchups[1].home_team_id == t2.id
    assert matchups[1].away_team_id == t1.id

    # Idempotence: calling again returns existing schedule
    again = generate_league_schedule(league.id, total_weeks=14)
    assert len(again) == 14


def test_generate_league_schedule_four_teams():
    league, t1 = create_league("Four Team League", "Tony", "Iron Men", max_teams=4)
    _, t2, _ = join_league(league.invite_token, "Alesia", "Stars")
    _, t3, _ = join_league(league.invite_token, "Bob", "Bulldogs")
    _, t4, _ = join_league(league.invite_token, "Carol", "Cougars")

    matchups = generate_league_schedule(league.id, total_weeks=14)
    # 4 teams = 2 games per week * 14 weeks = 28 matchups
    assert len(matchups) == 28

    w1_matchups = get_league_matchups_for_week(league.id, 1)
    assert len(w1_matchups) == 2


def test_lineup_initialization_and_swapping():
    conn = get_connection()
    league, t1 = create_league("Lineup League", "Tony", "Team Tony", max_teams=2)
    _, t2, _ = join_league(league.invite_token, "Alesia", "Team Alesia")

    # Manually insert 2 roster players for Team Tony
    p_qb = conn.execute("SELECT id FROM players WHERE position = 'QB' LIMIT 1;").fetchone()["id"]
    p_rb1 = conn.execute("SELECT id FROM players WHERE position = 'RB' LIMIT 1;").fetchone()["id"]
    p_rb2 = conn.execute("SELECT id FROM players WHERE position = 'RB' LIMIT 1 OFFSET 1;").fetchone()["id"]

    with conn:
        conn.execute("INSERT INTO roster_players (id, team_id, player_id, slot, created_at) VALUES ('rp1', ?, ?, 'QB', '2026-09-01');", (t1.id, p_qb))
        conn.execute("INSERT INTO roster_players (id, team_id, player_id, slot, created_at) VALUES ('rp2', ?, ?, 'RB1', '2026-09-01');", (t1.id, p_rb1))
        conn.execute("INSERT INTO roster_players (id, team_id, player_id, slot, created_at) VALUES ('rp3', ?, ?, 'BENCH', '2026-09-01');", (t1.id, p_rb2))

    # Initialize week 1 lineups
    ensure_lineups_for_week(league.id, 1, t1.id)

    lineup = get_team_lineup(league.id, t1.id, 1)
    starters = lineup["starters"]
    bench = lineup["bench"]

    assert len(starters) == 2  # QB and RB1
    assert len(bench) == 1     # RB2 on BENCH

    rb1_slot = next(s for s in starters if s["slot"] == "RB1")
    bench_slot = bench[0]

    # Swap RB1 and BENCH RB2
    ok, msg = swap_lineup_slots(league.id, t1.id, 1, rb1_slot["id"], bench_slot["id"], t1.manager_token)
    assert ok is True

    # Check updated lineup
    lineup_after = get_team_lineup(league.id, t1.id, 1)
    new_rb1 = next(s for s in lineup_after["starters"] if s["slot"] == "RB1")
    assert new_rb1["player_id"] == p_rb2

    # Ineligible swap attempt: QB into RB1 slot should fail
    qb_slot = next(s for s in lineup_after["starters"] if s["slot"] == "QB")
    bad_ok, bad_msg = swap_lineup_slots(league.id, t1.id, 1, qb_slot["id"], new_rb1["id"], t1.manager_token)
    assert bad_ok is False
    assert "not eligible" in bad_msg


def test_matchup_details_and_week_finalization():
    conn = get_connection()
    league, t1 = create_league("Champ League", "Tony", "Iron Men", max_teams=2)
    _, t2, _ = join_league(league.invite_token, "Alesia", "Wildcats")

    # Give each team a QB
    p1 = conn.execute("SELECT id FROM players WHERE position = 'QB' LIMIT 1;").fetchone()["id"]
    p2 = conn.execute("SELECT id FROM players WHERE position = 'QB' LIMIT 1 OFFSET 1;").fetchone()["id"]

    with conn:
        conn.execute("INSERT INTO roster_players (id, team_id, player_id, slot, created_at) VALUES ('rp1', ?, ?, 'QB', '2026-09-01');", (t1.id, p1))
        conn.execute("INSERT INTO roster_players (id, team_id, player_id, slot, created_at) VALUES ('rp2', ?, ?, 'QB', '2026-09-01');", (t2.id, p2))

    generate_league_schedule(league.id, total_weeks=14)
    ensure_lineups_for_week(league.id, 1)

    w1_matchups = get_league_matchups_for_week(league.id, 1)
    m_id = w1_matchups[0]["id"]

    # Record stats: Tony's QB throws 3 TDs (300 yds, 3 td = 24 pts)
    record_player_stats(p1, league.season, 1, {"pass_yd": 300, "pass_td": 3})
    # Alesia's QB throws 1 TD (180 yds, 1 td = 11.2 pts)
    record_player_stats(p2, league.season, 1, {"pass_yd": 180, "pass_td": 1})

    details = get_matchup_details(m_id)
    assert details is not None
    assert details["home"]["total_points"] == 24.0
    assert details["away"]["total_points"] == 11.2

    # Commissioner finalizes Week 1
    ok, msg = finalize_week(league.id, 1, league.commissioner_token)
    assert ok is True

    # Verify team standings updated
    t1_row = conn.execute("SELECT * FROM teams WHERE id = ?;", (t1.id,)).fetchone()
    t2_row = conn.execute("SELECT * FROM teams WHERE id = ?;", (t2.id,)).fetchone()

    assert t1_row["wins"] == 1
    assert t1_row["losses"] == 0
    assert t1_row["points_for"] == 24.0
    assert t1_row["points_against"] == 11.2

    assert t2_row["wins"] == 0
    assert t2_row["losses"] == 1
    assert t2_row["points_for"] == 11.2
    assert t2_row["points_against"] == 24.0

    # League advanced to week 2
    l_row = conn.execute("SELECT current_week FROM leagues WHERE id = ?;", (league.id,)).fetchone()
    assert l_row["current_week"] == 2


def test_full_draft_to_matchups_and_standings_lifecycle():
    from app.fantasy.draft import start_draft, make_draft_pick
    from app.fantasy.models import LeagueStatus

    # Create 2 team league with 2 rounds
    league, t1 = create_league("Sprint3 League", "Tony", "Iron Men", max_teams=2)
    _, t2, _ = join_league(league.invite_token, "Alesia", "Wildcats")

    conn = get_connection()
    with conn:
        conn.execute("UPDATE leagues SET settings_json = json_set(settings_json, '$.total_rounds', 2) WHERE id = ?;", (league.id,))

    # Start draft
    start_draft(league.id, league.commissioner_token)

    # 2 teams x 2 rounds = 4 picks
    p_ids = [r["id"] for r in conn.execute("SELECT id FROM players LIMIT 4;").fetchall()]

    for i, pid in enumerate(p_ids, start=1):
        pick, err = make_draft_pick(league.id, pid, commissioner_token=league.commissioner_token)
        assert err is None
        assert pick is not None

    # Verify league status is now IN_SEASON
    lr = conn.execute("SELECT status FROM leagues WHERE id = ?;", (league.id,)).fetchone()
    assert lr["status"] == LeagueStatus.IN_SEASON.value

    # Verify schedule generated automatically
    matchup_rows = conn.execute("SELECT * FROM matchups WHERE league_id = ?;", (league.id,)).fetchall()
    assert len(matchup_rows) == 14

    # Verify week 1 lineups exist for both teams
    slots_t1 = conn.execute("SELECT * FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week = 1;", (league.id, t1.id)).fetchall()
    slots_t2 = conn.execute("SELECT * FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week = 1;", (league.id, t2.id)).fetchall()
    assert len(slots_t1) == 2
    assert len(slots_t2) == 2

    # Simulate week 1 stats
    ok, msg = simulate_week_stats(league.id, 1, league.commissioner_token)
    assert ok is True

    # Finalize week 1
    f_ok, f_msg = finalize_week(league.id, 1, league.commissioner_token)
    assert f_ok is True

    # Check week 2 lineup carries forward seamlessly
    ensure_lineups_for_week(league.id, 2, t1.id)
    slots_t1_w2 = conn.execute("SELECT * FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week = 2;", (league.id, t1.id)).fetchall()
    assert len(slots_t1_w2) == 2
    # Player IDs match
    assert {r["player_id"] for r in slots_t1} == {r["player_id"] for r in slots_t1_w2}

