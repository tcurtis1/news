"""Matchup scheduling, weekly lineup management, scoring rollup, and week finalization."""

from __future__ import annotations

import json
import math
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.fantasy.db import (
    get_connection,
    row_to_league,
    row_to_lineup_slot,
    row_to_matchup,
    row_to_player,
    row_to_player_game_stats,
    row_to_roster_player,
    row_to_team,
)
from app.fantasy.models import (
    AuditLogEntry,
    FantasyTeam,
    League,
    LeagueStatus,
    LineupSlot,
    Matchup,
    Player,
    PlayerGameStats,
    RosterPlayer,
)
from app.fantasy.scoring import (
    calculate_player_points,
    calculate_win_probability,
    format_player_stats_summary,
)

STANDARD_STARTER_SLOTS = ["QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DST"]


def is_position_eligible(pos: str, slot: str) -> bool:
    """Check if player position is eligible for a designated roster slot."""
    p = (pos or "").upper()
    s = (slot or "").upper()
    if s == "BENCH":
        return True
    if s == "QB":
        return p == "QB"
    if s in ("RB", "RB1", "RB2"):
        return p == "RB"
    if s in ("WR", "WR1", "WR2"):
        return p == "WR"
    if s == "TE":
        return p == "TE"
    if s == "FLEX":
        return p in ("RB", "WR", "TE")
    if s == "K":
        return p == "K"
    if s == "DST":
        return p == "DST"
    return False


def generate_league_schedule(league_id: str, total_weeks: Optional[int] = None) -> List[Matchup]:
    """Generate round-robin regular season schedule for a fantasy league."""
    conn = get_connection()
    try:
        # Check if matchups already exist
        existing = conn.execute("SELECT * FROM matchups WHERE league_id = ? ORDER BY week ASC;", (league_id,)).fetchall()
        if existing:
            return [row_to_matchup(r) for r in existing]

        if total_weeks is None:
            lr = conn.execute("SELECT settings_json FROM leagues WHERE id = ?;", (league_id,)).fetchone()
            try:
                s_dict = json.loads(lr["settings_json"]) if lr and lr["settings_json"] else {}
                total_weeks = int(s_dict.get("regular_season_weeks", 14))
            except Exception:
                total_weeks = 14

        teams = conn.execute("SELECT id FROM teams WHERE league_id = ? ORDER BY created_at ASC;", (league_id,)).fetchall()
        team_ids: List[Optional[str]] = [r["id"] for r in teams]
        num_teams = len(team_ids)
        if num_teams < 2:
            return []

        # If odd number of teams, add None for BYE
        if num_teams % 2 != 0:
            team_ids.append(None)
            num_teams += 1

        half = num_teams // 2
        rounds_count = num_teams - 1
        round_pairings: List[List[Tuple[Optional[str], Optional[str]]]] = []

        # Generate standard Berger / polygon round-robin pairings
        curr_teams = list(team_ids)
        for r in range(rounds_count):
            pairings: List[Tuple[Optional[str], Optional[str]]] = []
            for i in range(half):
                t1 = curr_teams[i]
                t2 = curr_teams[num_teams - 1 - i]
                if t1 is not None and t2 is not None:
                    pairings.append((t1, t2))
            round_pairings.append(pairings)
            # Rotate all elements except first
            curr_teams = [curr_teams[0]] + [curr_teams[-1]] + curr_teams[1:-1]

        now_iso = datetime.now(timezone.utc).isoformat()
        created_matchups: List[Matchup] = []

        with conn:
            for w in range(1, total_weeks + 1):
                r_idx = (w - 1) % rounds_count
                cycle = (w - 1) // rounds_count
                pairings = round_pairings[r_idx]

                for t1, t2 in pairings:
                    if not t1 or not t2:
                        continue
                    # Alternate home and away on rematches across cycles
                    if cycle % 2 == 0:
                        home_id, away_id = t1, t2
                    else:
                        home_id, away_id = t2, t1

                    m_id = f"m_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                    INSERT INTO matchups (id, league_id, week, home_team_id, away_team_id, home_score, away_score, home_projected, away_projected, is_final, created_at)
                    VALUES (?, ?, ?, ?, ?, 0.0, 0.0, 0.0, 0.0, 0, ?);
                    """, (m_id, league_id, w, home_id, away_id, now_iso))

                    created_matchups.append(Matchup(
                        id=m_id,
                        league_id=league_id,
                        week=w,
                        home_team_id=home_id,
                        away_team_id=away_id,
                        home_score=0.0,
                        away_score=0.0,
                        home_projected=0.0,
                        away_projected=0.0,
                        is_final=False,
                        created_at=now_iso,
                    ))

        return created_matchups
    finally:
        conn.close()


def ensure_lineups_for_week(league_id: str, week: int, team_id: Optional[str] = None) -> None:
    """Ensure weekly lineup slots are populated from active rosters or previous week."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        # Find which teams need lineups for this week
        if team_id:
            teams_to_check = [team_id]
        else:
            rows = conn.execute("SELECT id FROM teams WHERE league_id = ?;", (league_id,)).fetchall()
            teams_to_check = [r["id"] for r in rows]

        with conn:
            for tid in teams_to_check:
                existing_count = conn.execute(
                    "SELECT COUNT(*) as c FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week = ?;",
                    (league_id, tid, week),
                ).fetchone()["c"]
                if existing_count > 0:
                    continue

                # Fallback source: week - 1 lineup if week > 1, else roster_players
                source_slots = []
                if week > 1:
                    prev_slots = conn.execute(
                        "SELECT player_id, slot, is_starter FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week = ?;",
                        (league_id, tid, week - 1),
                    ).fetchall()
                    if prev_slots:
                        source_slots = [(r["player_id"], r["slot"], bool(r["is_starter"])) for r in prev_slots]

                if not source_slots:
                    # Load from current roster_players
                    roster_rows = conn.execute(
                        "SELECT player_id, slot FROM roster_players WHERE team_id = ?;",
                        (tid,),
                    ).fetchall()
                    for r in roster_rows:
                        s = r["slot"]
                        is_starter = s != "BENCH"
                        source_slots.append((r["player_id"], s, is_starter))

                for pid, slot_name, is_st in source_slots:
                    slot_id = f"ls_{uuid.uuid4().hex[:12]}"
                    conn.execute("""
                    INSERT OR IGNORE INTO lineup_slots (id, league_id, team_id, week, player_id, slot, is_starter, is_locked, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, 0, ?);
                    """, (slot_id, league_id, tid, week, pid, slot_name, 1 if is_st else 0, now_iso))
    finally:
        conn.close()


def get_team_lineup(league_id: str, team_id: str, week: int) -> Dict[str, Any]:
    """Retrieve structured lineup for a team and week with stats and projections."""
    ensure_lineups_for_week(league_id, week, team_id)
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT * FROM teams WHERE id = ?;", (team_id,)).fetchone()
        if not lr or not tr:
            return {"error": "League or team not found"}

        league = row_to_league(lr)
        team = row_to_team(tr)

        slots_rows = conn.execute("""
        SELECT ls.*, p.name as p_name, p.position as p_pos, p.nfl_team as p_nfl_team,
               p.bye_week as p_bye, p.projected_points as p_proj, p.status as p_status, p.headshot_url as p_headshot
        FROM lineup_slots ls
        JOIN players p ON ls.player_id = p.id
        WHERE ls.league_id = ? AND ls.team_id = ? AND ls.week = ?
        ORDER BY ls.slot ASC, ls.created_at ASC;
        """, (league_id, team_id, week)).fetchall()

        scoring_format = league.settings.scoring_format

        starters = []
        bench = []
        starter_total_pts = 0.0
        starter_projected_pts = 0.0
        bench_total_pts = 0.0

        for r in slots_rows:
            p = Player(
                id=r["player_id"],
                name=r["p_name"],
                position=r["p_pos"],
                nfl_team=r["p_nfl_team"],
                bye_week=r["p_bye"],
                projected_points=r["p_proj"],
                status=r["p_status"],
                headshot_url=r["p_headshot"],
            )
            # Fetch game stats for week
            stat_r = conn.execute(
                "SELECT * FROM player_game_stats WHERE player_id = ? AND season = ? AND week = ?;",
                (p.id, league.season, week),
            ).fetchone()
            stats = row_to_player_game_stats(stat_r) if stat_r else None
            pts = calculate_player_points(stats, scoring_format, position=p.position)
            summary = format_player_stats_summary(stats, p.position)

            slot_item = {
                "id": r["id"],
                "slot": r["slot"],
                "player_id": p.id,
                "player": p.to_dict(),
                "is_starter": bool(r["is_starter"]),
                "is_locked": bool(r["is_locked"]),
                "points": pts,
                "projected_points": p.projected_points,
                "stats_summary": summary,
            }

            if r["is_starter"]:
                starters.append(slot_item)
                starter_total_pts += pts
                starter_projected_pts += p.projected_points
            else:
                bench.append(slot_item)
                bench_total_pts += pts

        # Sort starters in standard display order
        def slot_sort_key(item):
            s = item["slot"]
            try:
                return STANDARD_STARTER_SLOTS.index(s)
            except ValueError:
                return 99

        starters.sort(key=slot_sort_key)

        return {
            "league": league.to_dict(),
            "team": team.to_dict(),
            "week": week,
            "starters": starters,
            "bench": bench,
            "total_points": round(starter_total_pts, 2),
            "projected_points": round(starter_projected_pts, 2),
            "bench_points": round(bench_total_pts, 2),
        }
    finally:
        conn.close()


def swap_lineup_slots(
    league_id: str,
    team_id: str,
    week: int,
    slot_id_1: str,
    slot_id_2: str,
    actor_token: str,
) -> Tuple[bool, str]:
    """Swap two players between lineup slots with eligibility & lock validation."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT * FROM teams WHERE id = ?;", (team_id,)).fetchone()
        if not lr or not tr:
            return False, "League or team not found"

        league = row_to_league(lr)
        team = row_to_team(tr)

        # Authorization: must be team manager or league commissioner
        if actor_token != team.manager_token and actor_token != league.commissioner_token:
            return False, "Unauthorized to edit this team's lineup"

        r1 = conn.execute("""
        SELECT ls.*, p.position as p_pos FROM lineup_slots ls
        JOIN players p ON ls.player_id = p.id
        WHERE ls.id = ? AND ls.league_id = ? AND ls.team_id = ? AND ls.week = ?;
        """, (slot_id_1, league_id, team_id, week)).fetchone()

        r2 = conn.execute("""
        SELECT ls.*, p.position as p_pos FROM lineup_slots ls
        JOIN players p ON ls.player_id = p.id
        WHERE ls.id = ? AND ls.league_id = ? AND ls.team_id = ? AND ls.week = ?;
        """, (slot_id_2, league_id, team_id, week)).fetchone()

        if not r1 or not r2:
            return False, "One or both lineup slots were not found"

        # Check kickoff locks
        if r1["is_locked"] or r2["is_locked"]:
            return False, "Cannot swap players whose games have already started / locked"

        # Eligibility check:
        # Player 1 is moving to slot r2["slot"]
        # Player 2 is moving to slot r1["slot"]
        pos1, slot2 = r1["p_pos"], r2["slot"]
        pos2, slot1 = r2["p_pos"], r1["slot"]

        if not is_position_eligible(pos1, slot2):
            return False, f"Player 1 ({pos1}) is not eligible for slot {slot2}"
        if not is_position_eligible(pos2, slot1):
            return False, f"Player 2 ({pos2}) is not eligible for slot {slot1}"

        slot_name_1, is_st_1 = r1["slot"], r1["is_starter"]
        slot_name_2, is_st_2 = r2["slot"], r2["is_starter"]

        with conn:
            conn.execute("UPDATE lineup_slots SET slot = ?, is_starter = ? WHERE id = ?;", (slot_name_2, is_st_2, slot_id_1))
            conn.execute("UPDATE lineup_slots SET slot = ?, is_starter = ? WHERE id = ?;", (slot_name_1, is_st_1, slot_id_2))

        return True, "Lineup successfully updated"
    finally:
        conn.close()


def get_matchup_details(matchup_id: str) -> Optional[Dict[str, Any]]:
    """Return comprehensive head-to-head matchup breakdown with live scores and win probability."""
    conn = get_connection()
    try:
        mr = conn.execute("SELECT * FROM matchups WHERE id = ?;", (matchup_id,)).fetchone()
        if not mr:
            return None

        league_id = mr["league_id"]
        week = mr["week"]

        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return None
        league = row_to_league(lr)

        # Ensure both teams have lineups for this week
        ensure_lineups_for_week(league_id, week, mr["home_team_id"])
        ensure_lineups_for_week(league_id, week, mr["away_team_id"])

        home_data = get_team_lineup(league_id, mr["home_team_id"], week)
        away_data = get_team_lineup(league_id, mr["away_team_id"], week)

        if home_data.get("starters"):
            home_score = home_data.get("total_points", 0.0)
            home_proj = home_data.get("projected_points", 0.0)
        else:
            home_score = float(mr["home_score"])
            home_proj = float(mr["home_projected"]) if "home_projected" in mr.keys() else 0.0

        if away_data.get("starters"):
            away_score = away_data.get("total_points", 0.0)
            away_proj = away_data.get("projected_points", 0.0)
        else:
            away_score = float(mr["away_score"])
            away_proj = float(mr["away_projected"]) if "away_projected" in mr.keys() else 0.0

        is_final = bool(mr["is_final"])

        home_prob, away_prob = calculate_win_probability(
            home_projected_total=home_proj if not is_final else home_score,
            away_projected_total=away_proj if not is_final else away_score,
            is_final=is_final,
            home_actual=home_score,
            away_actual=away_score,
        )

        # Update cached scores in matchups table
        with conn:
            conn.execute("""
            UPDATE matchups
            SET home_score = ?, away_score = ?, home_projected = ?, away_projected = ?
            WHERE id = ?;
            """, (home_score, away_score, home_proj, away_proj, matchup_id))

        matchup_obj = row_to_matchup(mr)
        matchup_obj.home_score = home_score
        matchup_obj.away_score = away_score
        matchup_obj.home_projected = home_proj
        matchup_obj.away_projected = away_proj

        # Build position-by-position comparison list
        # Map starters by slot
        home_by_slot: Dict[str, Any] = {s["slot"]: s for s in home_data.get("starters", [])}
        away_by_slot: Dict[str, Any] = {s["slot"]: s for s in away_data.get("starters", [])}

        comparison_starters = []
        for slot_name in STANDARD_STARTER_SLOTS:
            comparison_starters.append({
                "slot": slot_name,
                "home_slot": home_by_slot.get(slot_name),
                "away_slot": away_by_slot.get(slot_name),
            })

        # All league matchups for week selector / carousel
        week_matchups_raw = conn.execute("""
        SELECT m.id, m.week, m.home_score, m.away_score, m.is_final,
               ht.name as home_name, at.name as away_name
        FROM matchups m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.league_id = ? AND m.week = ?
        ORDER BY m.created_at ASC;
        """, (league_id, week)).fetchall()

        week_matchups = [dict(r) for r in week_matchups_raw]

        return {
            "matchup": matchup_obj.to_dict(),
            "league": league.to_dict(),
            "home": home_data,
            "away": away_data,
            "comparison_starters": comparison_starters,
            "home_prob": home_prob,
            "away_prob": away_prob,
            "week_matchups": week_matchups,
        }
    finally:
        conn.close()


def get_league_matchups_for_week(league_id: str, week: int) -> List[Dict[str, Any]]:
    """Return all scoreboard matchup cards for a given league week."""
    conn = get_connection()
    try:
        rows = conn.execute("""
        SELECT m.*,
               ht.name as home_team_name, ht.manager_name as home_manager_name, ht.wins as home_wins, ht.losses as home_losses, ht.ties as home_ties,
               at.name as away_team_name, at.manager_name as away_manager_name, at.wins as away_wins, at.losses as away_losses, at.ties as away_ties
        FROM matchups m
        JOIN teams ht ON m.home_team_id = ht.id
        JOIN teams at ON m.away_team_id = at.id
        WHERE m.league_id = ? AND m.week = ?
        ORDER BY m.created_at ASC;
        """, (league_id, week)).fetchall()

        results = []
        for r in rows:
            results.append({
                "id": r["id"],
                "week": r["week"],
                "is_final": bool(r["is_final"]),
                "home_team_id": r["home_team_id"],
                "away_team_id": r["away_team_id"],
                "home_score": float(r["home_score"]),
                "away_score": float(r["away_score"]),
                "home_projected": float(r["home_projected"]),
                "away_projected": float(r["away_projected"]),
                "home_team_name": r["home_team_name"],
                "home_manager_name": r["home_manager_name"],
                "home_record": f"{r['home_wins']}-{r['home_losses']}" + (f"-{r['home_ties']}" if r["home_ties"] else ""),
                "away_team_name": r["away_team_name"],
                "away_manager_name": r["away_manager_name"],
                "away_record": f"{r['away_wins']}-{r['away_losses']}" + (f"-{r['away_ties']}" if r["away_ties"] else ""),
            })
        return results
    finally:
        conn.close()


def get_team_matchup_for_week(league_id: str, team_id: str, week: int) -> Optional[str]:
    """Return matchup_id for a given team and week."""
    conn = get_connection()
    try:
        r = conn.execute("""
        SELECT id FROM matchups
        WHERE league_id = ? AND week = ? AND (home_team_id = ? OR away_team_id = ?);
        """, (league_id, week, team_id, team_id)).fetchone()
        return r["id"] if r else None
    finally:
        conn.close()


def finalize_week(league_id: str, week: int, commissioner_token: str) -> Tuple[bool, str]:
    """Finalize all matchups for a week, update team win/loss records, and advance league week."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found"
        league = row_to_league(lr)

        if commissioner_token != league.commissioner_token:
            return False, "Only commissioner can finalize weekly matchups"

        matchup_rows = conn.execute("""
        SELECT * FROM matchups WHERE league_id = ? AND week = ?;
        """, (league_id, week)).fetchall()

        if not matchup_rows:
            return False, f"No matchups found for Week {week}"

        now_iso = datetime.now(timezone.utc).isoformat()

        # Recalculate up-to-date starter scores before entering transaction
        matchup_scores = []
        for mr in matchup_rows:
            details = get_matchup_details(mr["id"])
            if details:
                h_score = float(details["matchup"]["home_score"])
                a_score = float(details["matchup"]["away_score"])
            else:
                h_score = float(mr["home_score"])
                a_score = float(mr["away_score"])
            matchup_scores.append((mr, h_score, a_score))

        with conn:
            for mr, h_score, a_score in matchup_scores:
                home_id = mr["home_team_id"]
                away_id = mr["away_team_id"]

                # Mark matchup final
                conn.execute("UPDATE matchups SET is_final = 1, home_score = ?, away_score = ? WHERE id = ?;", (h_score, a_score, mr["id"]))

                # Update records & points for/against
                if h_score > a_score:
                    conn.execute("""
                    UPDATE teams
                    SET wins = wins + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (h_score, a_score, home_id))
                    conn.execute("""
                    UPDATE teams
                    SET losses = losses + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (a_score, h_score, away_id))
                elif a_score > h_score:
                    conn.execute("""
                    UPDATE teams
                    SET losses = losses + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (h_score, a_score, home_id))
                    conn.execute("""
                    UPDATE teams
                    SET wins = wins + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (a_score, h_score, away_id))
                else:
                    # Tie
                    conn.execute("""
                    UPDATE teams
                    SET ties = ties + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (h_score, a_score, home_id))
                    conn.execute("""
                    UPDATE teams
                    SET ties = ties + 1, points_for = points_for + ?, points_against = points_against + ?
                    WHERE id = ?;
                    """, (a_score, h_score, away_id))

            # Recalculate rolling waiver priority in reverse order of standings
            standings_teams = conn.execute("""
            SELECT id FROM teams
            WHERE league_id = ?
            ORDER BY wins DESC, ties DESC, points_for DESC;
            """, (league_id,)).fetchall()

            # Reverse order: lowest standing gets priority 1
            rev_teams = list(reversed(standings_teams))
            for prio, tr in enumerate(rev_teams, start=1):
                conn.execute("UPDATE teams SET waiver_priority = ? WHERE id = ?;", (prio, tr["id"]))

            # Advance current_week if week == league.current_week
            reg_weeks = league.settings.regular_season_weeks
            if week == league.current_week and week <= reg_weeks:
                next_week = week + 1
                conn.execute("UPDATE leagues SET current_week = ? WHERE id = ?;", (next_week, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "finalize_week", f"Finalized Week {week} matchups and updated standings.", now_iso))

        # Hook: calculate/persist seeds when regular season completes
        if week == league.settings.regular_season_weeks:
            try:
                from app.fantasy.playoffs import get_playoff_seedings
                get_playoff_seedings(league_id)
            except Exception:
                pass

        # Hook: advance playoff round if this was a playoff week
        if week > league.settings.regular_season_weeks:
            try:
                from app.fantasy.playoffs import advance_playoff_round_after_week
                advance_playoff_round_after_week(league_id, week)
            except Exception:
                pass

        return True, f"Week {week} successfully finalized! Standings updated."
    finally:
        conn.close()


def record_player_stats(
    player_id: str,
    season: int,
    week: int,
    stats_dict: Dict[str, Any],
) -> PlayerGameStats:
    """Record or update raw weekly boxscore stats for a player."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        s_id = f"ps_{uuid.uuid4().hex[:12]}"
        with conn:
            conn.execute("""
            INSERT INTO player_game_stats (
                id, player_id, season, week,
                pass_yd, pass_td, pass_int,
                rush_yd, rush_td,
                rec, rec_yd, rec_td,
                fumble_lost, two_pt, fg_made, pat_made,
                dst_sack, dst_int, dst_fumble_rec, dst_safety, dst_td, dst_points_allowed,
                raw_stats_json, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?,
                ?, ?
            )
            ON CONFLICT(player_id, season, week) DO UPDATE SET
                pass_yd = excluded.pass_yd,
                pass_td = excluded.pass_td,
                pass_int = excluded.pass_int,
                rush_yd = excluded.rush_yd,
                rush_td = excluded.rush_td,
                rec = excluded.rec,
                rec_yd = excluded.rec_yd,
                rec_td = excluded.rec_td,
                fumble_lost = excluded.fumble_lost,
                two_pt = excluded.two_pt,
                fg_made = excluded.fg_made,
                pat_made = excluded.pat_made,
                dst_sack = excluded.dst_sack,
                dst_int = excluded.dst_int,
                dst_fumble_rec = excluded.dst_fumble_rec,
                dst_safety = excluded.dst_safety,
                dst_td = excluded.dst_td,
                dst_points_allowed = excluded.dst_points_allowed,
                raw_stats_json = excluded.raw_stats_json,
                updated_at = excluded.updated_at;
            """, (
                s_id, player_id, season, week,
                int(stats_dict.get("pass_yd", 0)),
                int(stats_dict.get("pass_td", 0)),
                int(stats_dict.get("pass_int", 0)),
                int(stats_dict.get("rush_yd", 0)),
                int(stats_dict.get("rush_td", 0)),
                int(stats_dict.get("rec", 0)),
                int(stats_dict.get("rec_yd", 0)),
                int(stats_dict.get("rec_td", 0)),
                int(stats_dict.get("fumble_lost", 0)),
                int(stats_dict.get("two_pt", 0)),
                int(stats_dict.get("fg_made", 0)),
                int(stats_dict.get("pat_made", 0)),
                int(stats_dict.get("dst_sack", 0)),
                int(stats_dict.get("dst_int", 0)),
                int(stats_dict.get("dst_fumble_rec", 0)),
                int(stats_dict.get("dst_safety", 0)),
                int(stats_dict.get("dst_td", 0)),
                int(stats_dict.get("dst_points_allowed", 0)),
                json.dumps(stats_dict),
                now_iso,
            ))
            row = conn.execute(
                "SELECT * FROM player_game_stats WHERE player_id = ? AND season = ? AND week = ?;",
                (player_id, season, week),
            ).fetchone()
            return row_to_player_game_stats(row)
    finally:
        conn.close()


def simulate_week_stats(league_id: str, week: int, commissioner_token: str) -> Tuple[bool, str]:
    """Generate realistic live game stats for all players in league week for testing."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found"
        league = row_to_league(lr)
        if commissioner_token != league.commissioner_token:
            return False, "Unauthorized"

        # Get all rostered players in league
        players = conn.execute("""
        SELECT DISTINCT p.* FROM lineup_slots ls
        JOIN players p ON ls.player_id = p.id
        WHERE ls.league_id = ? AND ls.week = ?;
        """, (league_id, week)).fetchall()

        if not players:
            # Check general roster_players
            players = conn.execute("""
            SELECT DISTINCT p.* FROM roster_players rp
            JOIN teams t ON rp.team_id = t.id
            JOIN players p ON rp.player_id = p.id
            WHERE t.league_id = ?;
            """, (league_id,)).fetchall()

        rng = random.Random(f"{league_id}_{week}")
        for pr in players:
            pos = pr["position"].upper()
            st: Dict[str, Any] = {}
            if pos == "QB":
                st = {
                    "pass_yd": rng.randint(180, 340),
                    "pass_td": rng.choices([0, 1, 2, 3, 4], weights=[10, 30, 40, 15, 5])[0],
                    "pass_int": rng.choices([0, 1, 2], weights=[60, 30, 10])[0],
                    "rush_yd": rng.randint(0, 45),
                    "rush_td": rng.choices([0, 1], weights=[85, 15])[0],
                }
            elif pos == "RB":
                st = {
                    "rush_yd": rng.randint(30, 110),
                    "rush_td": rng.choices([0, 1, 2], weights=[45, 40, 15])[0],
                    "rec": rng.randint(1, 6),
                    "rec_yd": rng.randint(5, 45),
                    "rec_td": rng.choices([0, 1], weights=[85, 15])[0],
                }
            elif pos in ("WR", "TE"):
                recs = rng.randint(2, 9)
                st = {
                    "rec": recs,
                    "rec_yd": rng.randint(25, 120),
                    "rec_td": rng.choices([0, 1, 2], weights=[50, 40, 10])[0],
                }
            elif pos == "K":
                st = {
                    "fg_made": rng.randint(1, 4),
                    "pat_made": rng.randint(1, 4),
                }
            elif pos == "DST":
                st = {
                    "dst_sack": rng.randint(1, 5),
                    "dst_int": rng.choices([0, 1, 2], weights=[50, 35, 15])[0],
                    "dst_fumble_rec": rng.choices([0, 1], weights=[70, 30])[0],
                    "dst_points_allowed": rng.choice([10, 14, 17, 21, 24, 28, 31]),
                }

            record_player_stats(pr["id"], league.season, week, st)

        # Update scores in all matchups
        matchup_rows = conn.execute("SELECT id FROM matchups WHERE league_id = ? AND week = ?;", (league_id, week)).fetchall()
        for mr in matchup_rows:
            get_matchup_details(mr["id"])

        return True, f"Simulated stats recorded for Week {week}!"
    finally:
        conn.close()
