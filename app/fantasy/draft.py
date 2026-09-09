"""Snake draft engine, auto-pick timer, and roster assignment for Fantasy Football."""

from __future__ import annotations

import json
import random
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

from app.fantasy.db import (
    get_connection,
    row_to_draft_pick,
    row_to_league,
    row_to_player,
    row_to_roster_player,
    row_to_team,
)
from app.fantasy.models import (
    AuditLogEntry,
    DraftPick,
    DraftQueueItem,
    FantasyTeam,
    League,
    LeagueSettings,
    LeagueStatus,
    Player,
    RosterPlayer,
)


def calculate_snake_pick(draft_order: List[str], overall_pick: int) -> Tuple[int, int, str]:
    """Calculate (round_num, pick_in_round, team_id) for a 1-indexed overall pick."""
    n = len(draft_order)
    if n == 0:
        return 1, 1, ""
    round_num = ((overall_pick - 1) // n) + 1
    pick_in_round = ((overall_pick - 1) % n) + 1

    if round_num % 2 == 1:
        # Odd round: 1..N order
        team_id = draft_order[pick_in_round - 1]
    else:
        # Even round: N..1 reversed order
        team_id = draft_order[n - pick_in_round]

    return round_num, pick_in_round, team_id


def determine_roster_slot(
    player_pos: str,
    filled_slots: List[str],
    roster_slots: Optional[Dict[str, int]] = None,
) -> str:
    """Determine the optimal starter slot or BENCH for a drafted player."""
    pos = (player_pos or "").upper()
    if pos == "QB":
        if "QB" not in filled_slots:
            return "QB"
        return "BENCH"
    elif pos == "RB":
        if "RB1" not in filled_slots:
            return "RB1"
        elif "RB2" not in filled_slots:
            return "RB2"
        elif "FLEX" not in filled_slots:
            return "FLEX"
        return "BENCH"
    elif pos == "WR":
        if "WR1" not in filled_slots:
            return "WR1"
        elif "WR2" not in filled_slots:
            return "WR2"
        elif "FLEX" not in filled_slots:
            return "FLEX"
        return "BENCH"
    elif pos == "TE":
        if "TE" not in filled_slots:
            return "TE"
        elif "FLEX" not in filled_slots:
            return "FLEX"
        return "BENCH"
    elif pos == "K":
        if "K" not in filled_slots:
            return "K"
        return "BENCH"
    elif pos == "DST":
        if "DST" not in filled_slots:
            return "DST"
        return "BENCH"
    return "BENCH"


def get_draft_status(league_id: str) -> Optional[Dict[str, Any]]:
    """Return comprehensive live draft status for room view and polling."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return None

        tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
        teams = [row_to_team(r) for r in tr]
        league = row_to_league(lr, teams=teams)

        # Populate or ensure draft order
        draft_order = list(league.draft_order)
        team_map = {t.id: t for t in teams}
        if not draft_order and teams:
            draft_order = [t.id for t in teams]

        total_rounds = league.settings.total_rounds
        n_teams = len(draft_order)
        total_picks = n_teams * total_rounds if n_teams > 0 else 0
        overall = league.current_overall_pick

        # Check if clock expired and draft is active -> auto-pick!
        now = datetime.now(timezone.utc)
        if league.status == LeagueStatus.DRAFTING.value and league.settings.pick_timer_seconds > 0 and league.current_pick_deadline:
            try:
                deadline = datetime.fromisoformat(league.current_pick_deadline)
                if now >= deadline and overall <= total_picks:
                    conn.close()
                    auto_pick_if_timed_out(league_id)
                    return get_draft_status(league_id)
            except Exception:
                pass

        # Calculate on-the-clock info
        round_num, pick_in_round, clock_team_id = calculate_snake_pick(draft_order, overall)
        on_the_clock = team_map.get(clock_team_id)

        # Remaining seconds calculation
        remaining_seconds: Optional[int] = None
        if league.status == LeagueStatus.DRAFT_PAUSED.value:
            remaining_seconds = league.draft_paused_seconds or league.settings.pick_timer_seconds
        elif league.status == LeagueStatus.DRAFTING.value and league.current_pick_deadline:
            try:
                dl = datetime.fromisoformat(league.current_pick_deadline)
                remaining_seconds = max(0, int((dl - now).total_seconds()))
            except Exception:
                remaining_seconds = league.settings.pick_timer_seconds
        elif league.status == LeagueStatus.DRAFTING.value:
            remaining_seconds = league.settings.pick_timer_seconds

        # Fetch recent picks (last 15)
        pick_rows = conn.execute("""
        SELECT dp.*, p.name as player_name, p.position as player_pos, p.nfl_team, p.bye_week, p.headshot_url,
               t.name as team_name, t.manager_name
        FROM draft_picks dp
        JOIN players p ON dp.player_id = p.id
        JOIN teams t ON dp.team_id = t.id
        WHERE dp.league_id = ?
        ORDER BY dp.overall_pick DESC
        LIMIT 15;
        """, (league_id,)).fetchall()

        recent_picks = []
        for pr in pick_rows:
            recent_picks.append({
                "overall_pick": pr["overall_pick"],
                "round": pr["round"],
                "pick_number": pr["pick_number"],
                "team_id": pr["team_id"],
                "team_name": pr["team_name"],
                "manager_name": pr["manager_name"],
                "player_id": pr["player_id"],
                "player_name": pr["player_name"],
                "player_pos": pr["player_pos"],
                "nfl_team": pr["nfl_team"],
                "bye_week": pr["bye_week"],
                "headshot_url": pr["headshot_url"],
                "is_auto_pick": bool(pr["is_auto_pick"]),
            })

        # Drafted player IDs set
        drafted_rows = conn.execute("SELECT player_id FROM draft_picks WHERE league_id = ?;", (league_id,)).fetchall()
        drafted_player_ids = {r["player_id"] for r in drafted_rows}

        return {
            "league_id": league.id,
            "league_name": league.name,
            "status": league.status,
            "overall_pick": overall,
            "total_picks": total_picks,
            "total_rounds": total_rounds,
            "round": round_num,
            "pick_in_round": pick_in_round,
            "teams": [{"id": t.id, "name": t.name, "manager_name": t.manager_name} for t in teams],
            "draft_order": [team_map[tid].to_dict() for tid in draft_order if tid in team_map],
            "on_the_clock": on_the_clock.to_dict() if on_the_clock else None,
            "remaining_seconds": remaining_seconds,
            "pick_timer_seconds": league.settings.pick_timer_seconds,
            "is_paused": league.status == LeagueStatus.DRAFT_PAUSED.value,
            "is_completed": league.status in (LeagueStatus.IN_SEASON.value, LeagueStatus.COMPLETE.value),
            "recent_picks": recent_picks,
            "drafted_player_ids": list(drafted_player_ids),
        }
    finally:
        conn.close()


def start_draft(league_id: str, commissioner_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Commissioner starts the draft."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return None, "Unauthorized: invalid commissioner credentials."

            tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
            if len(tr) < 2:
                return None, "A league needs at least 2 teams to start the draft."

            league = row_to_league(lr, teams=[row_to_team(r) for r in tr])
            if league.status not in (LeagueStatus.PRE_DRAFT.value, LeagueStatus.DRAFT_PAUSED.value):
                return None, f"Cannot start draft in status '{league.status}'."

            # Ensure draft order is set
            draft_order = list(league.draft_order)
            if not draft_order or len(draft_order) != len(tr):
                draft_order = [r["id"] for r in tr]

            pick_timer = league.settings.pick_timer_seconds
            deadline = (datetime.now(timezone.utc) + timedelta(seconds=pick_timer)).isoformat() if pick_timer > 0 else None

            conn.execute("""
            UPDATE leagues
            SET status = ?, draft_order_json = ?, current_pick_deadline = ?, draft_paused_seconds = NULL
            WHERE id = ?;
            """, (LeagueStatus.DRAFTING.value, json.dumps(draft_order), deadline, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "start_draft", f"Commissioner started the 2026 NFL draft ({len(tr)} teams, {league.settings.total_rounds} rounds).", datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()

    return get_draft_status(league_id), None


def pause_draft(league_id: str, commissioner_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Commissioner pauses the draft clock."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return None, "Unauthorized: invalid commissioner credentials."

            if lr["status"] != LeagueStatus.DRAFTING.value:
                return None, "Draft is not currently in progress."

            now = datetime.now(timezone.utc)
            remaining = 60
            if lr["current_pick_deadline"]:
                try:
                    dl = datetime.fromisoformat(lr["current_pick_deadline"])
                    remaining = max(0, int((dl - now).total_seconds()))
                except Exception:
                    pass

            conn.execute("""
            UPDATE leagues
            SET status = ?, draft_paused_seconds = ?
            WHERE id = ?;
            """, (LeagueStatus.DRAFT_PAUSED.value, remaining, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "pause_draft", f"Commissioner paused the draft clock with {remaining}s remaining.", datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()

    return get_draft_status(league_id), None


def resume_draft(league_id: str, commissioner_token: str) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
    """Commissioner resumes the draft clock."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return None, "Unauthorized: invalid commissioner credentials."

            if lr["status"] != LeagueStatus.DRAFT_PAUSED.value:
                return None, "Draft is not paused."

            try:
                settings_dict = json.loads(lr["settings_json"])
            except Exception:
                settings_dict = {}
            timer = settings_dict.get("pick_timer_seconds", 60)

            remaining = lr["draft_paused_seconds"] if lr["draft_paused_seconds"] is not None else timer
            deadline = (datetime.now(timezone.utc) + timedelta(seconds=remaining)).isoformat() if timer > 0 else None

            conn.execute("""
            UPDATE leagues
            SET status = ?, current_pick_deadline = ?, draft_paused_seconds = NULL
            WHERE id = ?;
            """, (LeagueStatus.DRAFTING.value, deadline, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "resume_draft", f"Commissioner resumed the draft clock ({remaining}s remaining).", datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()

    return get_draft_status(league_id), None


def set_draft_order(
    league_id: str,
    commissioner_token: str,
    order: Optional[List[str]] = None,
    randomize: bool = False,
) -> Tuple[Optional[League], Optional[str]]:
    """Commissioner sets or randomizes draft order before drafting starts."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return None, "Unauthorized: invalid commissioner credentials."

            if lr["status"] not in (LeagueStatus.PRE_DRAFT.value, LeagueStatus.DRAFT_PAUSED.value):
                return None, "Cannot reorder draft once drafting is active."

            tr = conn.execute("SELECT * FROM teams WHERE league_id = ?;", (league_id,)).fetchall()
            valid_team_ids = [r["id"] for r in tr]

            if randomize:
                shuffled = list(valid_team_ids)
                random.shuffle(shuffled)
                draft_order = shuffled
            elif order:
                if set(order) != set(valid_team_ids):
                    return None, "Draft order must contain all league teams exactly once."
                draft_order = order
            else:
                draft_order = valid_team_ids

            conn.execute("UPDATE leagues SET draft_order_json = ? WHERE id = ?;", (json.dumps(draft_order), league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "set_draft_order", "Commissioner updated the draft order.", datetime.now(timezone.utc).isoformat()))
    finally:
        conn.close()

    from app.fantasy.service import get_league
    return get_league(league_id), None


def make_draft_pick(
    league_id: str,
    player_id: str,
    manager_token: Optional[str] = None,
    commissioner_token: Optional[str] = None,
    is_auto: bool = False,
) -> Tuple[Optional[DraftPick], Optional[str]]:
    """Execute a draft pick for the on-the-clock team."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
            if not lr:
                return None, "League not found."

            tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
            teams = [row_to_team(r) for r in tr]
            league = row_to_league(lr, teams=teams)

            if league.status != LeagueStatus.DRAFTING.value:
                return None, f"Draft is not active (status: {league.status})."

            draft_order = list(league.draft_order)
            if not draft_order:
                draft_order = [t.id for t in teams]

            n_teams = len(draft_order)
            total_rounds = league.settings.total_rounds
            total_picks = n_teams * total_rounds
            overall = league.current_overall_pick

            if overall > total_picks:
                return None, "Draft has already concluded."

            round_num, pick_in_round, clock_team_id = calculate_snake_pick(draft_order, overall)
            team_map = {t.id: t for t in teams}
            clock_team = team_map.get(clock_team_id)
            if not clock_team:
                return None, "Draft order team not found."

            # Authorization check
            is_commish = bool(commissioner_token and commissioner_token == league.commissioner_token)
            is_turn = bool(manager_token and manager_token == clock_team.manager_token)
            if not (is_turn or is_commish or is_auto):
                return None, f"Unauthorized: It is not your turn to pick (on clock: {clock_team.name})."

            # Check player existence
            pr = conn.execute("SELECT * FROM players WHERE id = ?;", (player_id,)).fetchone()
            if not pr:
                return None, "Player not found in catalog."
            player = row_to_player(pr)

            # Check if player already drafted
            already = conn.execute("SELECT id, team_id FROM draft_picks WHERE league_id = ? AND player_id = ?;", (league_id, player_id)).fetchone()
            if already:
                return None, f"Player {player.name} has already been drafted."

            # Determine roster slot
            filled_slots_rows = conn.execute("SELECT slot FROM roster_players WHERE team_id = ?;", (clock_team_id,)).fetchall()
            filled_slots = [r["slot"] for r in filled_slots_rows]
            slot = determine_roster_slot(player.position, filled_slots, league.settings.roster_slots)

            now_iso = datetime.now(timezone.utc).isoformat()
            pick_id = f"dp_{uuid.uuid4().hex[:12]}"

            conn.execute("""
            INSERT INTO draft_picks (id, league_id, round, pick_number, overall_pick, team_id, player_id, is_auto_pick, selected_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (pick_id, league_id, round_num, pick_in_round, overall, clock_team_id, player_id, 1 if is_auto else 0, now_iso))

            roster_id = f"rp_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO roster_players (id, team_id, player_id, slot, acquired_type, created_at)
            VALUES (?, ?, ?, ?, 'draft', ?);
            """, (roster_id, clock_team_id, player_id, slot, now_iso))

            # Remove player from queue if queued
            conn.execute("DELETE FROM draft_queue WHERE team_id = ? AND player_id = ?;", (clock_team_id, player_id))

            # Advance pick
            if overall >= total_picks:
                new_status = LeagueStatus.IN_SEASON.value
                deadline = None
                conn.execute("""
                UPDATE leagues
                SET status = ?, current_overall_pick = ?, current_pick_deadline = NULL, draft_paused_seconds = NULL
                WHERE id = ?;
                """, (new_status, overall + 1, league_id))

                audit_id = f"a_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
                VALUES (?, ?, ?, ?, ?, ?);
                """, (audit_id, league_id, "System", "draft_complete", f"The 2026 draft concluded! All {total_picks} picks completed. League is now IN-SEASON.", now_iso))
            else:
                pick_timer = league.settings.pick_timer_seconds
                deadline = (datetime.now(timezone.utc) + timedelta(seconds=pick_timer)).isoformat() if pick_timer > 0 else None
                conn.execute("""
                UPDATE leagues
                SET current_overall_pick = ?, current_pick_deadline = ?, draft_paused_seconds = NULL
                WHERE id = ?;
                """, (overall + 1, deadline, league_id))

            draft_pick = DraftPick(
                id=pick_id,
                league_id=league_id,
                round=round_num,
                pick_number=pick_in_round,
                overall_pick=overall,
                team_id=clock_team_id,
                player_id=player_id,
                selected_at=now_iso,
                is_auto_pick=is_auto,
                player=player,
                team_name=clock_team.name,
                manager_name=clock_team.manager_name,
            )
            return draft_pick, None
    finally:
        conn.close()


def auto_pick_if_timed_out(league_id: str) -> Tuple[bool, Optional[DraftPick]]:
    """If current pick deadline passed, pick highest priority queued player or BPA."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr or lr["status"] != LeagueStatus.DRAFTING.value:
            return False, None

        tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
        teams = [row_to_team(r) for r in tr]
        league = row_to_league(lr, teams=teams)

        draft_order = list(league.draft_order)
        if not draft_order:
            draft_order = [t.id for t in teams]

        overall = league.current_overall_pick
        total_picks = len(draft_order) * league.settings.total_rounds
        if overall > total_picks:
            return False, None

        round_num, pick_in_round, clock_team_id = calculate_snake_pick(draft_order, overall)

        # 1. Check team draft queue
        queue_row = conn.execute("""
        SELECT dq.player_id
        FROM draft_queue dq
        WHERE dq.team_id = ? AND dq.player_id NOT IN (
            SELECT player_id FROM draft_picks WHERE league_id = ?
        )
        ORDER BY dq.priority ASC
        LIMIT 1;
        """, (clock_team_id, league_id)).fetchone()

        chosen_player_id: Optional[str] = None
        if queue_row:
            chosen_player_id = queue_row["player_id"]
        else:
            # 2. Check team's unfilled starting roster slots
            filled_rows = conn.execute("SELECT slot FROM roster_players WHERE team_id = ?;", (clock_team_id,)).fetchall()
            filled_slots = {r["slot"] for r in filled_rows}

            # Strategy: if round <= 8 and no QB, prioritize QB; if no RB, prioritize RB/WR
            needed_pos = []
            if "QB" not in filled_slots:
                needed_pos.append("QB")
            if "RB1" not in filled_slots or "RB2" not in filled_slots:
                needed_pos.append("RB")
            if "WR1" not in filled_slots or "WR2" not in filled_slots:
                needed_pos.append("WR")
            if "TE" not in filled_slots:
                needed_pos.append("TE")

            # Try to pick best available player from needed positions
            if needed_pos:
                placeholders = ",".join("?" for _ in needed_pos)
                bpa_row = conn.execute(f"""
                SELECT id FROM players
                WHERE position IN ({placeholders}) AND id NOT IN (
                    SELECT player_id FROM draft_picks WHERE league_id = ?
                )
                ORDER BY adp ASC, projected_points DESC
                LIMIT 1;
                """, (*needed_pos, league_id)).fetchone()
                if bpa_row:
                    chosen_player_id = bpa_row["id"]

            # Fallback: Best Player Available regardless of position (except avoid 2nd K or 2nd DST)
            if not chosen_player_id:
                bpa_row = conn.execute("""
                SELECT id FROM players
                WHERE id NOT IN (
                    SELECT player_id FROM draft_picks WHERE league_id = ?
                )
                ORDER BY adp ASC, projected_points DESC
                LIMIT 1;
                """, (league_id,)).fetchone()
                if bpa_row:
                    chosen_player_id = bpa_row["id"]

        if not chosen_player_id:
            return False, None
    finally:
        conn.close()

    pick, err = make_draft_pick(league_id, chosen_player_id, is_auto=True)
    return (pick is not None), pick


def undo_last_pick(league_id: str, commissioner_token: str) -> Tuple[bool, Optional[str]]:
    """Commissioner undos the most recent pick."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return False, "Unauthorized: invalid commissioner credentials."

            last_pick = conn.execute("""
            SELECT dp.*, p.name as player_name
            FROM draft_picks dp
            JOIN players p ON dp.player_id = p.id
            WHERE dp.league_id = ?
            ORDER BY dp.overall_pick DESC
            LIMIT 1;
            """, (league_id,)).fetchone()

            if not last_pick:
                return False, "No picks have been made yet."

            overall = last_pick["overall_pick"]
            player_id = last_pick["player_id"]
            team_id = last_pick["team_id"]
            player_name = last_pick["player_name"]

            conn.execute("DELETE FROM draft_picks WHERE id = ?;", (last_pick["id"],))
            conn.execute("DELETE FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, player_id))

            try:
                settings_dict = json.loads(lr["settings_json"])
            except Exception:
                settings_dict = {}
            timer = settings_dict.get("pick_timer_seconds", 60)
            deadline = (datetime.now(timezone.utc) + timedelta(seconds=timer)).isoformat() if timer > 0 else None

            conn.execute("""
            UPDATE leagues
            SET status = ?, current_overall_pick = ?, current_pick_deadline = ?, draft_paused_seconds = NULL
            WHERE id = ?;
            """, (LeagueStatus.DRAFTING.value, overall, deadline, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "undo_pick", f"Commissioner undid Pick #{overall} ({player_name}).", datetime.now(timezone.utc).isoformat()))

            return True, None
    finally:
        conn.close()


def reset_draft(league_id: str, commissioner_token: str) -> Tuple[bool, Optional[str]]:
    """Commissioner resets the entire draft back to pre-draft state."""
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return False, "Unauthorized: invalid commissioner credentials."

            conn.execute("DELETE FROM draft_picks WHERE league_id = ?;", (league_id,))
            conn.execute("""
            DELETE FROM roster_players
            WHERE team_id IN (SELECT id FROM teams WHERE league_id = ?) AND acquired_type = 'draft';
            """, (league_id,))

            conn.execute("""
            UPDATE leagues
            SET status = ?, current_overall_pick = 1, current_pick_deadline = NULL, draft_paused_seconds = NULL
            WHERE id = ?;
            """, (LeagueStatus.PRE_DRAFT.value, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, "Commissioner", "reset_draft", "Commissioner reset the draft back to pre-draft.", datetime.now(timezone.utc).isoformat()))

            return True, None
    finally:
        conn.close()


def get_draft_board(league_id: str) -> Dict[str, Any]:
    """Return 2D grid matrix of the entire draft board for visualization."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return {"teams": [], "rounds": []}

        tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
        teams = [row_to_team(r) for r in tr]
        league = row_to_league(lr, teams=teams)

        draft_order = list(league.draft_order)
        if not draft_order:
            draft_order = [t.id for t in teams]

        team_map = {t.id: t for t in teams}
        ordered_teams = [team_map[tid].to_dict() for tid in draft_order if tid in team_map]

        picks = conn.execute("""
        SELECT dp.*, p.name as player_name, p.position as player_pos, p.nfl_team, p.bye_week
        FROM draft_picks dp
        JOIN players p ON dp.player_id = p.id
        WHERE dp.league_id = ?
        ORDER BY dp.overall_pick ASC;
        """, (league_id,)).fetchall()

        picks_by_overall = {}
        for p in picks:
            picks_by_overall[p["overall_pick"]] = {
                "overall_pick": p["overall_pick"],
                "round": p["round"],
                "pick_number": p["pick_number"],
                "team_id": p["team_id"],
                "player_id": p["player_id"],
                "player_name": p["player_name"],
                "player_pos": p["player_pos"],
                "nfl_team": p["nfl_team"],
                "bye_week": p["bye_week"],
                "is_auto_pick": bool(p["is_auto_pick"]),
            }

        n = len(draft_order)
        rounds_data = []
        for r in range(1, league.settings.total_rounds + 1):
            row_picks = []
            for col_idx in range(n):
                # For team col_idx in draft_order:
                # If round is odd: pick_in_round = col_idx + 1
                # If round is even: pick_in_round = n - col_idx
                if r % 2 == 1:
                    pick_in_round = col_idx + 1
                else:
                    pick_in_round = n - col_idx
                overall = ((r - 1) * n) + pick_in_round
                cell = picks_by_overall.get(overall, {
                    "overall_pick": overall,
                    "round": r,
                    "pick_number": pick_in_round,
                    "team_id": draft_order[col_idx],
                    "player_id": None,
                })
                row_picks.append(cell)
            rounds_data.append({"round_number": r, "picks": row_picks})

        return {
            "teams": ordered_teams,
            "rounds": rounds_data,
            "current_overall_pick": league.current_overall_pick,
        }
    finally:
        conn.close()


def get_team_roster(team_id: str) -> List[Dict[str, Any]]:
    """Get all players drafted or rostered on a team."""
    conn = get_connection()
    try:
        rows = conn.execute("""
        SELECT rp.*, p.name as player_name, p.position as player_pos, p.nfl_team, p.bye_week, p.projected_points, p.headshot_url
        FROM roster_players rp
        JOIN players p ON rp.player_id = p.id
        WHERE rp.team_id = ?
        ORDER BY rp.created_at ASC;
        """, (team_id,)).fetchall()

        roster = []
        for r in rows:
            roster.append({
                "id": r["id"],
                "team_id": r["team_id"],
                "player_id": r["player_id"],
                "player_name": r["player_name"],
                "position": r["player_pos"],
                "nfl_team": r["nfl_team"],
                "bye_week": r["bye_week"],
                "projected_points": r["projected_points"],
                "headshot_url": r["headshot_url"],
                "slot": r["slot"],
                "acquired_type": r["acquired_type"],
            })
        return roster
    finally:
        conn.close()


def manage_draft_queue(team_id: str, action: str, player_id: str) -> List[Dict[str, Any]]:
    """Add, remove, or list a team's draft queue."""
    conn = get_connection()
    try:
        with conn:
            if action == "add":
                count = conn.execute("SELECT COUNT(*) FROM draft_queue WHERE team_id = ?;", (team_id,)).fetchone()[0]
                qid = f"dq_{uuid.uuid4().hex[:12]}"
                now_iso = datetime.now(timezone.utc).isoformat()
                conn.execute("""
                INSERT OR IGNORE INTO draft_queue (id, team_id, player_id, priority, created_at)
                VALUES (?, ?, ?, ?, ?);
                """, (qid, team_id, player_id, count + 1, now_iso))
            elif action == "remove":
                conn.execute("DELETE FROM draft_queue WHERE team_id = ? AND player_id = ?;", (team_id, player_id))

            # Fetch updated queue
            rows = conn.execute("""
            SELECT dq.priority, p.*
            FROM draft_queue dq
            JOIN players p ON dq.player_id = p.id
            WHERE dq.team_id = ?
            ORDER BY dq.priority ASC;
            """, (team_id,)).fetchall()

            queue = []
            for r in rows:
                queue.append({
                    "priority": r["priority"],
                    "player_id": r["id"],
                    "name": r["name"],
                    "position": r["position"],
                    "nfl_team": r["nfl_team"],
                    "bye_week": r["bye_week"],
                    "adp": r["adp"],
                    "projected_points": r["projected_points"],
                })
            return queue
    finally:
        conn.close()
