"""Playoffs, Championship Bracket, and Final Standings engine."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.fantasy.db import (
    get_connection,
    row_to_league,
    row_to_matchup,
    row_to_team,
)
from app.fantasy.models import (
    FantasyTeam,
    League,
    LeagueStatus,
    Matchup,
)
from app.fantasy.matchups import ensure_lineups_for_week


def get_playoff_seedings(league_id: str) -> List[Dict[str, Any]]:
    """Calculate and persist playoff seeds 1..N based on standings and tiebreakers."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return []

        team_rows = conn.execute("SELECT * FROM teams WHERE league_id = ?;", (league_id,)).fetchall()
        teams = [row_to_team(r) for r in team_rows]
        if not teams:
            return []

        # Get regular season matchups for head-to-head tiebreaking
        reg_matchups = conn.execute(
            "SELECT * FROM matchups WHERE league_id = ? AND is_final = 1;",
            (league_id,),
        ).fetchall()

        def h2h_record(t1_id: str, t2_id: str) -> float:
            """Return t1's win pct against t2 in regular season."""
            t1_wins = 0
            t1_losses = 0
            for m in reg_matchups:
                h_id = m["home_team_id"]
                a_id = m["away_team_id"]
                if (h_id == t1_id and a_id == t2_id) or (h_id == t2_id and a_id == t1_id):
                    h_score = float(m["home_score"])
                    a_score = float(m["away_score"])
                    if h_score == a_score:
                        continue
                    if h_id == t1_id:
                        if h_score > a_score:
                            t1_wins += 1
                        else:
                            t1_losses += 1
                    else:
                        if a_score > h_score:
                            t1_wins += 1
                        else:
                            t1_losses += 1
            total = t1_wins + t1_losses
            return (t1_wins / total) if total > 0 else 0.5

        def team_sort_key(t: FantasyTeam):
            total_games = t.wins + t.losses + t.ties
            win_pct = ((t.wins + 0.5 * t.ties) / total_games) if total_games > 0 else 0.0
            return (
                round(win_pct, 4),
                round(t.points_for, 2),
                round(t.points_against, 2),
            )

        # Sort descending by primary criteria
        sorted_teams = sorted(teams, key=team_sort_key, reverse=True)

        # Apply head-to-head tiebreak for adjacent tied teams
        i = 0
        while i < len(sorted_teams) - 1:
            t1 = sorted_teams[i]
            t2 = sorted_teams[i + 1]
            if team_sort_key(t1) == team_sort_key(t2):
                h2h = h2h_record(t1.id, t2.id)
                if h2h < 0.5:
                    sorted_teams[i], sorted_teams[i + 1] = sorted_teams[i + 1], sorted_teams[i]
            i += 1

        # Persist seeds to database
        seedings: List[Dict[str, Any]] = []
        with conn:
            for seed_idx, t in enumerate(sorted_teams, start=1):
                conn.execute(
                    "UPDATE teams SET playoff_seed = ? WHERE id = ?;",
                    (seed_idx, t.id),
                )
                total_games = t.wins + t.losses + t.ties
                win_pct = ((t.wins + 0.5 * t.ties) / total_games) if total_games > 0 else 0.0
                seedings.append({
                    "seed": seed_idx,
                    "team_id": t.id,
                    "team_name": t.name,
                    "manager_name": t.manager_name,
                    "record": f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
                    "win_pct": round(win_pct, 3),
                    "points_for": round(t.points_for, 2),
                    "points_against": round(t.points_against, 2),
                    "team": t.to_dict(),
                })

        return seedings
    finally:
        conn.close()


def generate_playoff_bracket(
    league_id: str,
    commissioner_token: str,
    force_restart: bool = False,
) -> Tuple[bool, str, Dict[str, Any]]:
    """Generate playoff bracket matchups based on seeds and league settings."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found.", {}

        league = row_to_league(lr)
        if commissioner_token != league.commissioner_token:
            return False, "Unauthorized: commissioner token required.", {}

        # Check existing playoff matchups
        existing_playoffs = conn.execute(
            "SELECT COUNT(*) as c FROM matchups WHERE league_id = ? AND matchup_type != 'regular';",
            (league_id,),
        ).fetchone()["c"]

        if existing_playoffs > 0 and not force_restart:
            return False, "Playoffs have already been generated for this league.", {}

        # Calculate seedings
        seedings = get_playoff_seedings(league_id)
        num_teams = len(seedings)
        if num_teams < 2:
            return False, "At least 2 teams required to generate playoffs.", {}

        playoff_teams_setting = league.settings.playoff_teams
        if playoff_teams_setting not in (2, 4, 6):
            playoff_teams_setting = 4 if num_teams >= 4 else 2

        if playoff_teams_setting > num_teams:
            playoff_teams_setting = 4 if num_teams >= 4 else 2

        reg_weeks = league.settings.regular_season_weeks
        round1_week = reg_weeks + 1
        now_iso = datetime.now(timezone.utc).isoformat()

        created_matchups: List[Dict[str, Any]] = []

        with conn:
            # If force restart, purge previous playoff matchups
            if existing_playoffs > 0 and force_restart:
                conn.execute(
                    "DELETE FROM matchups WHERE league_id = ? AND matchup_type != 'regular';",
                    (league_id,),
                )
                conn.execute(
                    "UPDATE leagues SET status = ?, champion_team_id = NULL, second_place_team_id = NULL, third_place_team_id = NULL, sacko_team_id = NULL WHERE id = ?;",
                    (LeagueStatus.PLAYOFFS.value, league_id),
                )
                conn.execute(
                    "UPDATE teams SET final_rank = NULL WHERE league_id = ?;",
                    (league_id,),
                )

            seed_map = {s["seed"]: s for s in seedings}

            if playoff_teams_setting == 4:
                # Round 1: Semifinals (Week reg_weeks + 1)
                # Semi 1: 1 vs 4
                m1_id = f"m_semi_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_semi', 'semi_1', 1, 1, 4);
                """, (m1_id, league_id, round1_week, seed_map[1]["team_id"], seed_map[4]["team_id"], now_iso))

                # Semi 2: 2 vs 3
                m2_id = f"m_semi_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_semi', 'semi_2', 1, 2, 3);
                """, (m2_id, league_id, round1_week, seed_map[2]["team_id"], seed_map[3]["team_id"], now_iso))

                created_matchups.extend([{"id": m1_id, "slot": "semi_1"}, {"id": m2_id, "slot": "semi_2"}])

                # Consolation bracket for non-playoff teams
                if league.settings.playoff_consolation and num_teams >= 6:
                    if num_teams >= 8 and 7 in seed_map and 8 in seed_map:
                        # 5 vs 8
                        mc1_id = f"m_con_{uuid.uuid4().hex[:10]}"
                        conn.execute("""
                        INSERT INTO matchups (
                            id, league_id, week, home_team_id, away_team_id,
                            home_score, away_score, home_projected, away_projected,
                            is_final, created_at, matchup_type, bracket_slot,
                            playoff_round, home_seed, away_seed
                        ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'consolation', 'con_semi_1', 1, 5, 8);
                        """, (mc1_id, league_id, round1_week, seed_map[5]["team_id"], seed_map[8]["team_id"], now_iso))

                        # 6 vs 7
                        mc2_id = f"m_con_{uuid.uuid4().hex[:10]}"
                        conn.execute("""
                        INSERT INTO matchups (
                            id, league_id, week, home_team_id, away_team_id,
                            home_score, away_score, home_projected, away_projected,
                            is_final, created_at, matchup_type, bracket_slot,
                            playoff_round, home_seed, away_seed
                        ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'consolation', 'con_semi_2', 1, 6, 7);
                        """, (mc2_id, league_id, round1_week, seed_map[6]["team_id"], seed_map[7]["team_id"], now_iso))
                        created_matchups.extend([{"id": mc1_id, "slot": "con_semi_1"}, {"id": mc2_id, "slot": "con_semi_2"}])
                    elif 5 in seed_map and 6 in seed_map:
                        # 5 vs 6 Consolation 5th place
                        mc_id = f"m_con_{uuid.uuid4().hex[:10]}"
                        conn.execute("""
                        INSERT INTO matchups (
                            id, league_id, week, home_team_id, away_team_id,
                            home_score, away_score, home_projected, away_projected,
                            is_final, created_at, matchup_type, bracket_slot,
                            playoff_round, home_seed, away_seed
                        ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'consolation', 'con_5th', 1, 5, 6);
                        """, (mc_id, league_id, round1_week, seed_map[5]["team_id"], seed_map[6]["team_id"], now_iso))
                        created_matchups.append({"id": mc_id, "slot": "con_5th"})

            elif playoff_teams_setting == 6:
                # 6-team bracket: seeds 1 and 2 get Round 1 Byes
                # Quarterfinal 1: 4 vs 5
                m1_id = f"m_qtr_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_quarter', 'quarter_1', 1, 4, 5);
                """, (m1_id, league_id, round1_week, seed_map[4]["team_id"], seed_map[5]["team_id"], now_iso))

                # Quarterfinal 2: 3 vs 6
                m2_id = f"m_qtr_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_quarter', 'quarter_2', 1, 3, 6);
                """, (m2_id, league_id, round1_week, seed_map[3]["team_id"], seed_map[6]["team_id"], now_iso))

                created_matchups.extend([{"id": m1_id, "slot": "quarter_1"}, {"id": m2_id, "slot": "quarter_2"}])

                # Consolation for seeds 7+
                if league.settings.playoff_consolation and num_teams >= 8 and 7 in seed_map and 8 in seed_map:
                    mc_id = f"m_con_{uuid.uuid4().hex[:10]}"
                    conn.execute("""
                    INSERT INTO matchups (
                        id, league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_projected, away_projected,
                        is_final, created_at, matchup_type, bracket_slot,
                        playoff_round, home_seed, away_seed
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'consolation', 'con_semi_1', 1, 7, 8);
                    """, (mc_id, league_id, round1_week, seed_map[7]["team_id"], seed_map[8]["team_id"], now_iso))
                    created_matchups.append({"id": mc_id, "slot": "con_semi_1"})

            elif playoff_teams_setting == 2:
                # 2-team direct championship (Seed 1 vs 2)
                m_id = f"m_champ_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'championship', 'championship', 1, 1, 2);
                """, (m_id, league_id, round1_week, seed_map[1]["team_id"], seed_map[2]["team_id"], now_iso))
                created_matchups.append({"id": m_id, "slot": "championship"})

                if num_teams >= 4 and 3 in seed_map and 4 in seed_map:
                    m3_id = f"m_3rd_{uuid.uuid4().hex[:10]}"
                    conn.execute("""
                    INSERT INTO matchups (
                        id, league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_projected, away_projected,
                        is_final, created_at, matchup_type, bracket_slot,
                        playoff_round, home_seed, away_seed
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'third_place', 'third_place', 1, 3, 4);
                    """, (m3_id, league_id, round1_week, seed_map[3]["team_id"], seed_map[4]["team_id"], now_iso))
                    created_matchups.append({"id": m3_id, "slot": "third_place"})

            # Transition league status to playoffs and advance current_week if needed
            new_week = max(league.current_week, round1_week)
            conn.execute(
                "UPDATE leagues SET status = ?, current_week = ? WHERE id = ?;",
                (LeagueStatus.PLAYOFFS.value, new_week, league_id),
            )

            # Ensure lineups exist for all teams in round 1
            for s in seedings:
                ensure_lineups_for_week(league_id, round1_week, s["team_id"])

            # Audit entry & transaction wire
            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, 'Commissioner', 'generate_playoffs', ?, ?);
            """, (audit_id, league_id, f"Generated {playoff_teams_setting}-team Playoff Bracket for Week {round1_week}.", now_iso))

            tx_id = f"tx_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO transactions (id, league_id, team_id, type, description, details_json, created_at)
            VALUES (?, ?, NULL, 'commissioner', ?, ?, ?);
            """, (tx_id, league_id, f"🏆 The Playoff Bracket has been seeded! {playoff_teams_setting} teams advance to the postseason.", json.dumps({"seeds": seedings}), now_iso))

        return True, f"Successfully seeded and generated {playoff_teams_setting}-team Playoff Bracket for Week {round1_week}!", {
            "round1_week": round1_week,
            "playoff_teams": playoff_teams_setting,
            "created_matchups": created_matchups,
            "seedings": seedings,
        }
    finally:
        conn.close()


def advance_playoff_round_after_week(league_id: str, finalized_week: int) -> Tuple[bool, str]:
    """Advance winners and losers of playoff round or crown champion if finals completed."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found."
        league = row_to_league(lr)

        reg_weeks = league.settings.regular_season_weeks
        if finalized_week <= reg_weeks:
            return False, "Not a playoff week."

        playoff_matchups = conn.execute(
            "SELECT * FROM matchups WHERE league_id = ? AND week = ? AND matchup_type != 'regular';",
            (league_id, finalized_week),
        ).fetchall()

        if not playoff_matchups:
            return False, "No playoff matchups found for this week."

        now_iso = datetime.now(timezone.utc).isoformat()
        next_week = finalized_week + 1

        with conn:
            # Map winners and losers by bracket slot
            slot_results: Dict[str, Dict[str, Any]] = {}
            for mr in playoff_matchups:
                h_score = float(mr["home_score"])
                a_score = float(mr["away_score"])
                h_id = mr["home_team_id"]
                a_id = mr["away_team_id"]
                h_seed = mr["home_seed"]
                a_seed = mr["away_seed"]

                # Higher seed wins in event of exact score tie
                if h_score > a_score:
                    w_id, l_id = h_id, a_id
                    w_seed, l_seed = h_seed, a_seed
                elif a_score > h_score:
                    w_id, l_id = a_id, h_id
                    w_seed, l_seed = a_seed, h_seed
                else:
                    # Tiebreak by seed
                    if h_seed is not None and a_seed is not None and h_seed <= a_seed:
                        w_id, l_id = h_id, a_id
                        w_seed, l_seed = h_seed, a_seed
                    else:
                        w_id, l_id = a_id, h_id
                        w_seed, l_seed = a_seed, h_seed

                conn.execute(
                    "UPDATE matchups SET winner_id = ?, loser_id = ? WHERE id = ?;",
                    (w_id, l_id, mr["id"]),
                )
                slot = mr["bracket_slot"] or mr["matchup_type"]
                slot_results[slot] = {
                    "matchup_id": mr["id"],
                    "matchup_type": mr["matchup_type"],
                    "winner_id": w_id,
                    "loser_id": l_id,
                    "winner_seed": w_seed,
                    "loser_seed": l_seed,
                }

            # Check if this was the Championship Final week
            has_championship = any(mr["bracket_slot"] == "championship" or mr["matchup_type"] == "championship" for mr in playoff_matchups)

            if has_championship:
                # Crown Champion & Finalize League Season!
                champ_result = next(
                    (res for slot, res in slot_results.items() if slot == "championship" or res["matchup_type"] == "championship"),
                    None,
                )
                third_result = next(
                    (res for slot, res in slot_results.items() if slot == "third_place" or res["matchup_type"] == "third_place"),
                    None,
                )

                champ_id = champ_result["winner_id"] if champ_result else None
                runner_up_id = champ_result["loser_id"] if champ_result else None
                third_id = third_result["winner_id"] if third_result else None
                fourth_id = third_result["loser_id"] if third_result else None

                # Find Sacko (lowest seed or worst record)
                sacko_row = conn.execute(
                    "SELECT id FROM teams WHERE league_id = ? ORDER BY wins ASC, ties ASC, points_for ASC LIMIT 1;",
                    (league_id,),
                ).fetchone()
                sacko_id = sacko_row["id"] if sacko_row else None

                # Update team final ranks
                if champ_id:
                    conn.execute("UPDATE teams SET final_rank = 1 WHERE id = ?;", (champ_id,))
                if runner_up_id:
                    conn.execute("UPDATE teams SET final_rank = 2 WHERE id = ?;", (runner_up_id,))
                if third_id:
                    conn.execute("UPDATE teams SET final_rank = 3 WHERE id = ?;", (third_id,))
                if fourth_id:
                    conn.execute("UPDATE teams SET final_rank = 4 WHERE id = ?;", (fourth_id,))

                # Mark league completed
                conn.execute("""
                UPDATE leagues
                SET status = ?, champion_team_id = ?, second_place_team_id = ?, third_place_team_id = ?, sacko_team_id = ?
                WHERE id = ?;
                """, (LeagueStatus.COMPLETE.value, champ_id, runner_up_id, third_id, sacko_id, league_id))

                champ_team = conn.execute("SELECT name FROM teams WHERE id = ?;", (champ_id,)).fetchone()
                champ_name = champ_team["name"] if champ_team else "Champion"

                # Wire celebration
                tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO transactions (id, league_id, team_id, type, description, details_json, created_at)
                VALUES (?, ?, ?, 'commissioner', ?, ?, ?);
                """, (
                    tx_id, league_id, champ_id,
                    f"🏆 CHAMPION CROWNED! {champ_name} has won the Fantasy League Championship!",
                    json.dumps({
                        "champion_id": champ_id,
                        "runner_up_id": runner_up_id,
                        "third_place_id": third_id,
                        "sacko_id": sacko_id,
                    }),
                    now_iso,
                ))

                return True, f"🏆 Championship finalized! {champ_name} is the League Champion!"

            # Advance from Semifinals to Finals (4-Team or 6-Team Round 2)
            if "semi_1" in slot_results and "semi_2" in slot_results:
                s1 = slot_results["semi_1"]
                s2 = slot_results["semi_2"]

                w1, w2 = s1["winner_id"], s2["winner_id"]
                l1, l2 = s1["loser_id"], s2["loser_id"]
                ws1, ws2 = s1["winner_seed"], s2["winner_seed"]
                ls1, ls2 = s1["loser_seed"], s2["loser_seed"]

                # Ensure higher seed is home team
                if ws1 is not None and ws2 is not None and ws1 <= ws2:
                    c_home, c_away = w1, w2
                    c_hseed, c_aseed = ws1, ws2
                else:
                    c_home, c_away = w2, w1
                    c_hseed, c_aseed = ws2, ws1

                # Championship Matchup
                c_id = f"m_champ_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'championship', 'championship', 2, ?, ?);
                """, (c_id, league_id, next_week, c_home, c_away, now_iso, c_hseed, c_aseed))

                # 3rd Place Matchup
                if ls1 is not None and ls2 is not None and ls1 <= ls2:
                    t_home, t_away = l1, l2
                    t_hseed, t_aseed = ls1, ls2
                else:
                    t_home, t_away = l2, l1
                    t_hseed, t_aseed = ls2, ls1

                t_id = f"m_3rd_{uuid.uuid4().hex[:10]}"
                conn.execute("""
                INSERT INTO matchups (
                    id, league_id, week, home_team_id, away_team_id,
                    home_score, away_score, home_projected, away_projected,
                    is_final, created_at, matchup_type, bracket_slot,
                    playoff_round, home_seed, away_seed
                ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'third_place', 'third_place', 2, ?, ?);
                """, (t_id, league_id, next_week, t_home, t_away, now_iso, t_hseed, t_aseed))

                # Advance consolation winners if present
                if "con_semi_1" in slot_results and "con_semi_2" in slot_results:
                    cw1 = slot_results["con_semi_1"]["winner_id"]
                    cw2 = slot_results["con_semi_2"]["winner_id"]
                    cf_id = f"m_con_fin_{uuid.uuid4().hex[:10]}"
                    conn.execute("""
                    INSERT INTO matchups (
                        id, league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_projected, away_projected,
                        is_final, created_at, matchup_type, bracket_slot,
                        playoff_round, home_seed, away_seed
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'consolation', 'con_final', 2, NULL, NULL);
                    """, (cf_id, league_id, next_week, cw1, cw2, now_iso))

                # Populate lineups for advancing teams in next week
                for tid in (c_home, c_away, t_home, t_away):
                    ensure_lineups_for_week(league_id, next_week, tid)

                conn.execute("UPDATE leagues SET current_week = ? WHERE id = ?;", (next_week, league_id))
                return True, f"Semifinals complete! Advancing to Championship & 3rd Place game for Week {next_week}."

            # Advance from Quarterfinals to Semifinals (6-Team)
            if "quarter_1" in slot_results and "quarter_2" in slot_results:
                q1_winner = slot_results["quarter_1"]["winner_id"]
                q1_seed = slot_results["quarter_1"]["winner_seed"]
                q2_winner = slot_results["quarter_2"]["winner_id"]
                q2_seed = slot_results["quarter_2"]["winner_seed"]

                seed1_row = conn.execute("SELECT id FROM teams WHERE league_id = ? AND playoff_seed = 1;", (league_id,)).fetchone()
                seed2_row = conn.execute("SELECT id FROM teams WHERE league_id = ? AND playoff_seed = 2;", (league_id,)).fetchone()

                seed1_id = seed1_row["id"] if seed1_row else None
                seed2_id = seed2_row["id"] if seed2_row else None

                if seed1_id and seed2_id:
                    # Semi 1: Seed 1 vs lowest remaining seed (higher numeric seed)
                    if q1_seed is not None and q2_seed is not None and q1_seed > q2_seed:
                        s1_away, s1_seed = q1_winner, q1_seed
                        s2_away, s2_seed = q2_winner, q2_seed
                    else:
                        s1_away, s1_seed = q2_winner, q2_seed
                        s2_away, s2_seed = q1_winner, q1_seed

                    sm1_id = f"m_semi_{uuid.uuid4().hex[:10]}"
                    conn.execute("""
                    INSERT INTO matchups (
                        id, league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_projected, away_projected,
                        is_final, created_at, matchup_type, bracket_slot,
                        playoff_round, home_seed, away_seed
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_semi', 'semi_1', 2, 1, ?);
                    """, (sm1_id, league_id, next_week, seed1_id, s1_away, now_iso, s1_seed))

                    sm2_id = f"m_semi_{uuid.uuid4().hex[:10]}"
                    conn.execute("""
                    INSERT INTO matchups (
                        id, league_id, week, home_team_id, away_team_id,
                        home_score, away_score, home_projected, away_projected,
                        is_final, created_at, matchup_type, bracket_slot,
                        playoff_round, home_seed, away_seed
                    ) VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?, 'playoff_semi', 'semi_2', 2, 2, ?);
                    """, (sm2_id, league_id, next_week, seed2_id, s2_away, now_iso, s2_seed))

                    for tid in (seed1_id, seed2_id, s1_away, s2_away):
                        ensure_lineups_for_week(league_id, next_week, tid)

                    conn.execute("UPDATE leagues SET current_week = ? WHERE id = ?;", (next_week, league_id))
                    return True, f"Quarterfinals complete! Advancing to Semifinals for Week {next_week}."

        return True, "Playoff round complete."
    finally:
        conn.close()


def get_playoff_bracket(league_id: str) -> Dict[str, Any]:
    """Retrieve structured playoff bracket tree, seedings, and podium."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return {"error": "League not found", "is_generated": False}

        teams_rows = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC;", (league_id,)).fetchall()
        teams = [row_to_team(r) for r in teams_rows]
        team_map = {t.id: t for t in teams}

        champ = team_map.get(lr["champion_team_id"]) if "champion_team_id" in lr.keys() and lr["champion_team_id"] else None
        second = team_map.get(lr["second_place_team_id"]) if "second_place_team_id" in lr.keys() and lr["second_place_team_id"] else None
        third = team_map.get(lr["third_place_team_id"]) if "third_place_team_id" in lr.keys() and lr["third_place_team_id"] else None
        sacko = team_map.get(lr["sacko_team_id"]) if "sacko_team_id" in lr.keys() and lr["sacko_team_id"] else None

        league = row_to_league(lr, teams=teams, champion_team=champ, second_place_team=second, third_place_team=third, sacko_team=sacko)

        playoff_matchups = conn.execute(
            "SELECT * FROM matchups WHERE league_id = ? AND matchup_type != 'regular' ORDER BY week ASC, bracket_slot ASC;",
            (league_id,),
        ).fetchall()

        is_generated = len(playoff_matchups) > 0

        # Retrieve seeds
        seedings: List[Dict[str, Any]] = []
        seeded_teams = [t for t in teams if t.playoff_seed is not None]
        seeded_teams.sort(key=lambda t: t.playoff_seed or 999)

        if seeded_teams:
            for t in seeded_teams:
                total_games = t.wins + t.losses + t.ties
                win_pct = ((t.wins + 0.5 * t.ties) / total_games) if total_games > 0 else 0.0
                seedings.append({
                    "seed": t.playoff_seed,
                    "team_id": t.id,
                    "team_name": t.name,
                    "manager_name": t.manager_name,
                    "record": f"{t.wins}-{t.losses}" + (f"-{t.ties}" if t.ties else ""),
                    "win_pct": round(win_pct, 3),
                    "points_for": round(t.points_for, 2),
                    "points_against": round(t.points_against, 2),
                    "final_rank": t.final_rank,
                })
        else:
            # Fallback to calculated seeds
            calc_seeds = get_playoff_seedings(league_id)
            seedings = calc_seeds

        # Group matchups into bracket rounds
        bracket_rounds: Dict[int, List[Dict[str, Any]]] = {}
        consolation_matchups: List[Dict[str, Any]] = []

        for mr in playoff_matchups:
            m = row_to_matchup(mr, home_team=team_map.get(mr["home_team_id"]), away_team=team_map.get(mr["away_team_id"]))
            m_dict = m.to_dict()
            m_dict["home_team_name"] = m.home_team.name if m.home_team else "TBD"
            m_dict["away_team_name"] = m.away_team.name if m.away_team else "TBD"
            m_dict["home_manager_name"] = m.home_team.manager_name if m.home_team else ""
            m_dict["away_manager_name"] = m.away_team.manager_name if m.away_team else ""

            if m.matchup_type == "consolation":
                consolation_matchups.append(m_dict)
            else:
                rnd = m.playoff_round or 1
                bracket_rounds.setdefault(rnd, []).append(m_dict)

        # Convert rounds to ordered list
        round_list = []
        for r_num in sorted(bracket_rounds.keys()):
            r_matchups = bracket_rounds[r_num]
            w = r_matchups[0]["week"] if r_matchups else (league.settings.regular_season_weeks + r_num)
            r_title = "Quarterfinals" if any(m.get("matchup_type") == "playoff_quarter" for m in r_matchups) else (
                "Semifinals" if any(m.get("matchup_type") == "playoff_semi" for m in r_matchups) else (
                    "Championship & 3rd Place" if any(m.get("matchup_type") in ("championship", "third_place") for m in r_matchups) else f"Playoff Round {r_num}"
                )
            )
            round_list.append({
                "round_number": r_num,
                "title": r_title,
                "week": w,
                "matchups": r_matchups,
            })

        return {
            "league": league.to_dict(),
            "is_generated": is_generated,
            "status": league.status,
            "current_week": league.current_week,
            "regular_season_weeks": league.settings.regular_season_weeks,
            "playoff_teams": league.settings.playoff_teams,
            "seedings": seedings,
            "rounds": round_list,
            "consolation_matchups": consolation_matchups,
            "podium": {
                "champion": champ.to_dict() if champ else None,
                "runner_up": second.to_dict() if second else None,
                "third_place": third.to_dict() if third else None,
                "sacko": sacko.to_dict() if sacko else None,
            },
        }
    finally:
        conn.close()
