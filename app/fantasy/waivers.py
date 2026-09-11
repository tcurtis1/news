"""Waiver wire, FAAB bidding, free agency, and transaction management."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.fantasy.db import (
    get_connection,
    row_to_league,
    row_to_player,
    row_to_team,
    row_to_transaction,
    row_to_waiver_claim,
)
from app.fantasy.models import (
    AuditLogEntry,
    FantasyTeam,
    League,
    LeagueStatus,
    LeagueTransaction,
    Player,
    PlayerWaiverStatus,
    WaiverClaim,
)


def get_available_players(
    league_id: str,
    query: str = "",
    position: str = "ALL",
    availability: str = "ALL",  # ALL, FA, WAIVERS
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Retrieve unowned players in a league with waiver/free-agent status."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()

        # Roster limits and owned player IDs for this league
        roster_rows = conn.execute("""
        SELECT rp.player_id
        FROM roster_players rp
        JOIN teams t ON rp.team_id = t.id
        WHERE t.league_id = ?;
        """, (league_id,)).fetchall()
        owned_pids = {r["player_id"] for r in roster_rows}

        # Active waiver status records for this league
        waiver_rows = conn.execute("""
        SELECT player_id, waiver_until
        FROM player_waiver_status
        WHERE league_id = ? AND waiver_until > ?;
        """, (league_id, now_iso)).fetchall()
        waivers_map = {r["player_id"]: r["waiver_until"] for r in waiver_rows}

        where_clauses = ["1=1"]
        params: List[Any] = []

        if owned_pids:
            placeholders = ",".join("?" for _ in owned_pids)
            where_clauses.append(f"p.id NOT IN ({placeholders})")
            params.extend(list(owned_pids))

        pos = (position or "").strip().upper()
        if pos and pos != "ALL":
            where_clauses.append("p.position = ?")
            params.append(pos)

        q = (query or "").strip()
        if q:
            where_clauses.append("(p.name LIKE ? OR p.nfl_team LIKE ?)")
            params.extend([f"%{q}%", f"%{q}%"])

        where_sql = " AND ".join(where_clauses)

        all_candidate_rows = conn.execute(f"""
        SELECT p.*
        FROM players p
        WHERE {where_sql}
        ORDER BY p.projected_points DESC, p.adp ASC;
        """, params).fetchall()

        results = []
        for r in all_candidate_rows:
            p = row_to_player(r)
            pid = p.id
            is_on_waivers = pid in waivers_map
            waiver_until = waivers_map.get(pid)

            if availability == "FA" and is_on_waivers:
                continue
            if availability == "WAIVERS" and not is_on_waivers:
                continue

            results.append({
                "player": p.to_dict(),
                "status": "WAIVERS" if is_on_waivers else "FA",
                "waiver_until": waiver_until,
            })

        total_count = len(results)
        paged = results[offset : offset + limit]

        return {
            "players": paged,
            "total_count": total_count,
            "limit": limit,
            "offset": offset,
        }
    finally:
        conn.close()


def add_drop_free_agent(
    league_id: str,
    team_id: str,
    add_player_id: str,
    drop_player_id: Optional[str] = None,
    actor_token: str = "",
) -> Tuple[bool, str]:
    """Instantly add a free agent and optionally drop a rostered player."""
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        # Validate league & team credentials
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT * FROM teams WHERE id = ? AND league_id = ?;", (team_id, league_id)).fetchone()
        if not lr or not tr:
            return False, "League or team not found."

        league = row_to_league(lr)
        team = row_to_team(tr)

        is_commish = (actor_token == league.commissioner_token)
        if not is_commish and actor_token != team.manager_token:
            return False, "Unauthorized: Invalid team manager credentials."

        # Check add player existence
        add_p_row = conn.execute("SELECT * FROM players WHERE id = ?;", (add_player_id,)).fetchone()
        if not add_p_row:
            return False, "Player to add does not exist."
        add_player = row_to_player(add_p_row)

        with conn:
            # Check if player is already owned in league
            owned_check = conn.execute("""
            SELECT rp.id, t.name as team_name
            FROM roster_players rp
            JOIN teams t ON rp.team_id = t.id
            WHERE t.league_id = ? AND rp.player_id = ?;
            """, (league_id, add_player_id)).fetchone()
            if owned_check:
                return False, f"{add_player.name} is already rostered by {owned_check['team_name']}."

            # Check if player is on waivers
            waiver_check = conn.execute("""
            SELECT waiver_until FROM player_waiver_status
            WHERE league_id = ? AND player_id = ? AND waiver_until > ?;
            """, (league_id, add_player_id, now_iso)).fetchone()
            if waiver_check:
                return False, f"{add_player.name} is on waivers until {waiver_check['waiver_until']}. Submit a waiver claim instead."

            # Current roster check
            team_roster_rows = conn.execute("SELECT * FROM roster_players WHERE team_id = ?;", (team_id,)).fetchall()
            max_roster = league.settings.total_rounds or 15

            if len(team_roster_rows) >= max_roster and not drop_player_id:
                return False, f"Roster is full ({len(team_roster_rows)}/{max_roster}). You must select a player to drop."

            drop_player: Optional[Player] = None
            if drop_player_id:
                # Check that drop_player is currently owned by team
                drop_rp = conn.execute("SELECT * FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, drop_player_id)).fetchone()
                if not drop_rp:
                    return False, "Player to drop is not on your roster."

                # Check if locked in current week's lineup
                curr_week = league.current_week or 1
                locked_check = conn.execute("""
                SELECT is_locked, slot, is_starter FROM lineup_slots
                WHERE league_id = ? AND team_id = ? AND week = ? AND player_id = ?;
                """, (league_id, team_id, curr_week, drop_player_id)).fetchone()
                if locked_check and locked_check["is_locked"]:
                    return False, "Cannot drop a player whose game has already locked this week."

                drop_p_row = conn.execute("SELECT * FROM players WHERE id = ?;", (drop_player_id,)).fetchone()
                if drop_p_row:
                    drop_player = row_to_player(drop_p_row)

                # Remove from roster_players
                conn.execute("DELETE FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, drop_player_id))
                # Remove from lineup_slots
                conn.execute("DELETE FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week >= ? AND player_id = ?;", (league_id, team_id, curr_week, drop_player_id))

                # Place dropped player on waivers for 48 hours
                waiver_until = (now + timedelta(hours=48)).isoformat()
                ws_id = f"pw_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT OR REPLACE INTO player_waiver_status (id, league_id, player_id, waiver_until, created_at)
                VALUES (?, ?, ?, ?, ?);
                """, (ws_id, league_id, drop_player_id, waiver_until, now_iso))

            # Add player to team roster
            rp_id = f"rp_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO roster_players (id, team_id, player_id, slot, acquired_type, created_at)
            VALUES (?, ?, ?, 'BENCH', 'free_agent', ?);
            """, (rp_id, team_id, add_player_id, now_iso))

            # Add player to current week lineup slot as bench
            curr_week = league.current_week or 1
            ls_id = f"ls_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT OR IGNORE INTO lineup_slots (id, league_id, team_id, week, player_id, slot, is_starter, is_locked, created_at)
            VALUES (?, ?, ?, ?, ?, 'BENCH', 0, 0, ?);
            """, (ls_id, league_id, team_id, curr_week, add_player_id, now_iso))

            # Record transaction
            tx_id = f"tx_{uuid.uuid4().hex[:12]}"
            tx_type = "add_drop" if drop_player else "free_agent_add"
            desc = f"{team.name} added {add_player.name} ({add_player.position}, {add_player.nfl_team})"
            if drop_player:
                desc += f" and dropped {drop_player.name} ({drop_player.position}, {drop_player.nfl_team})"

            details = {
                "add_player_id": add_player.id,
                "add_player_name": add_player.name,
                "add_player_pos": add_player.position,
                "drop_player_id": drop_player.id if drop_player else None,
                "drop_player_name": drop_player.name if drop_player else None,
                "drop_player_pos": drop_player.position if drop_player else None,
            }
            conn.execute("""
            INSERT INTO transactions (id, league_id, team_id, type, description, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?);
            """, (tx_id, league_id, team_id, tx_type, desc, json.dumps(details), now_iso))

            # Log audit
            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, team.manager_name, "add_drop", desc, now_iso))

        return True, f"Successfully added {add_player.name}" + (f" and dropped {drop_player.name}." if drop_player else ".")
    finally:
        conn.close()


def submit_waiver_claim(
    league_id: str,
    team_id: str,
    add_player_id: str,
    drop_player_id: Optional[str] = None,
    bid_amount: int = 0,
    priority: int = 1,
    actor_token: str = "",
) -> Tuple[bool, str]:
    """Submit a waiver claim for an off-roster player on waivers."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()

        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT * FROM teams WHERE id = ? AND league_id = ?;", (team_id, league_id)).fetchone()
        if not lr or not tr:
            return False, "League or team not found."

        league = row_to_league(lr)
        team = row_to_team(tr)

        if actor_token != league.commissioner_token and actor_token != team.manager_token:
            return False, "Unauthorized: Invalid manager credentials."

        # Verify add player exists
        add_p_row = conn.execute("SELECT * FROM players WHERE id = ?;", (add_player_id,)).fetchone()
        if not add_p_row:
            return False, "Player does not exist."
        add_player = row_to_player(add_p_row)

        # Check if already owned by this team
        my_owned = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, add_player_id)).fetchone()
        if my_owned:
            return False, f"{add_player.name} is already on your roster."

        # Check if drop player is owned by this team
        if drop_player_id:
            my_drop = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, drop_player_id)).fetchone()
            if not my_drop:
                return False, "Drop player is not on your roster."

        # Validate FAAB budget
        if league.settings.waiver_type == "faab":
            bid_amount = max(0, bid_amount)
            if bid_amount > team.faab_balance:
                return False, f"Bid of ${bid_amount} exceeds your remaining FAAB budget (${team.faab_balance})."
        else:
            bid_amount = 0

        with conn:
            # Check existing pending claim for this add_player
            existing = conn.execute("""
            SELECT id FROM waiver_claims
            WHERE league_id = ? AND team_id = ? AND add_player_id = ? AND status = 'pending';
            """, (league_id, team_id, add_player_id)).fetchone()

            claim_id = existing["id"] if existing else f"wc_{uuid.uuid4().hex[:12]}"
            if existing:
                conn.execute("""
                UPDATE waiver_claims
                SET drop_player_id = ?, bid_amount = ?, priority = ?, created_at = ?
                WHERE id = ?;
                """, (drop_player_id, bid_amount, priority, now_iso, claim_id))
            else:
                conn.execute("""
                INSERT INTO waiver_claims (id, league_id, team_id, add_player_id, drop_player_id, bid_amount, priority, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?);
                """, (claim_id, league_id, team_id, add_player_id, drop_player_id, bid_amount, priority, now_iso))

        msg = f"Waiver claim placed for {add_player.name}"
        if league.settings.waiver_type == "faab":
            msg += f" with a ${bid_amount} FAAB bid."
        else:
            msg += f" (Priority #{priority})."
        return True, msg
    finally:
        conn.close()


def cancel_waiver_claim(league_id: str, team_id: str, claim_id: str, actor_token: str) -> Tuple[bool, str]:
    """Cancel a pending waiver claim."""
    conn = get_connection()
    try:
        lr = conn.execute("SELECT commissioner_token FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT manager_token FROM teams WHERE id = ?;", (team_id,)).fetchone()
        if not lr or not tr:
            return False, "League or team not found."

        if actor_token != lr["commissioner_token"] and actor_token != tr["manager_token"]:
            return False, "Unauthorized to cancel this claim."

        with conn:
            res = conn.execute("""
            UPDATE waiver_claims
            SET status = 'cancelled'
            WHERE id = ? AND league_id = ? AND team_id = ? AND status = 'pending';
            """, (claim_id, league_id, team_id))
            if res.rowcount == 0:
                return False, "Claim not found or already resolved."
        return True, "Waiver claim cancelled."
    finally:
        conn.close()


def get_team_waiver_claims(league_id: str, team_id: str, status: str = "pending") -> List[WaiverClaim]:
    """Get waiver claims for a team."""
    conn = get_connection()
    try:
        rows = conn.execute("""
        SELECT wc.*,
               ap.name as add_name, ap.position as add_pos, ap.nfl_team as add_nfl, ap.projected_points as add_proj, ap.headshot_url as add_headshot,
               dp.name as drop_name, dp.position as drop_pos, dp.nfl_team as drop_nfl,
               t.name as team_name, t.manager_name as mgr_name
        FROM waiver_claims wc
        JOIN players ap ON wc.add_player_id = ap.id
        LEFT JOIN players dp ON wc.drop_player_id = dp.id
        JOIN teams t ON wc.team_id = t.id
        WHERE wc.league_id = ? AND wc.team_id = ? AND (? = 'ALL' OR wc.status = ?)
        ORDER BY wc.priority ASC, wc.created_at ASC;
        """, (league_id, team_id, status, status)).fetchall()

        claims = []
        for r in rows:
            add_p = Player(
                id=r["add_player_id"],
                name=r["add_name"],
                position=r["add_pos"],
                nfl_team=r["add_nfl"],
                bye_week=0,
                projected_points=r["add_proj"],
                headshot_url=r["add_headshot"],
            )
            drop_p = None
            if r["drop_player_id"]:
                drop_p = Player(
                    id=r["drop_player_id"],
                    name=r["drop_name"],
                    position=r["drop_pos"],
                    nfl_team=r["drop_nfl"],
                    bye_week=0,
                )
            c = row_to_waiver_claim(r, add_player=add_p, drop_player=drop_p)
            claims.append(c)
        return claims
    finally:
        conn.close()


def process_waivers(league_id: str, actor_token: Optional[str] = None) -> Tuple[bool, str, Dict[str, Any]]:
    """Resolve and process all pending waiver claims atomically."""
    conn = get_connection()
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()

        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found.", {}

        league = row_to_league(lr)
        if actor_token and actor_token != league.commissioner_token:
            return False, "Unauthorized: Commissioner token required.", {}

        waiver_type = league.settings.waiver_type
        curr_week = league.current_week or 1
        max_roster = league.settings.total_rounds or 15

        awarded_claims = 0
        failed_claims = 0
        processed_logs = []

        with conn:
            # Query all pending claims joined with team info
            claims_rows = conn.execute("""
            SELECT wc.*, t.waiver_priority as team_priority, t.faab_balance as team_faab, t.name as team_name, t.manager_name as team_manager
            FROM waiver_claims wc
            JOIN teams t ON wc.team_id = t.id
            WHERE wc.league_id = ? AND wc.status = 'pending'
            ORDER BY
                CASE WHEN ? = 'faab' THEN wc.bid_amount END DESC,
                wc.priority ASC,
                t.waiver_priority ASC,
                wc.created_at ASC;
            """, (league_id, waiver_type)).fetchall()

            already_awarded_players: set[str] = set()

            for cr in claims_rows:
                claim_id = cr["id"]
                team_id = cr["team_id"]
                add_pid = cr["add_player_id"]
                drop_pid = cr["drop_player_id"]
                bid_amt = cr["bid_amount"]
                team_name = cr["team_name"]

                add_p_row = conn.execute("SELECT * FROM players WHERE id = ?;", (add_pid,)).fetchone()
                add_player = row_to_player(add_p_row) if add_p_row else None
                add_name = add_player.name if add_player else add_pid

                # Check if player was already awarded in this run or owned
                if add_pid in already_awarded_players:
                    conn.execute("UPDATE waiver_claims SET status = 'failed', fail_reason = 'Outbid or player claimed by higher priority', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                    failed_claims += 1
                    continue

                owned_check = conn.execute("""
                SELECT 1 FROM roster_players rp JOIN teams t ON rp.team_id = t.id
                WHERE t.league_id = ? AND rp.player_id = ?;
                """, (league_id, add_pid)).fetchone()
                if owned_check:
                    conn.execute("UPDATE waiver_claims SET status = 'failed', fail_reason = 'Player is already on a roster', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                    failed_claims += 1
                    continue

                # Team FAAB balance re-check
                team_curr = conn.execute("SELECT faab_balance, waiver_priority FROM teams WHERE id = ?;", (team_id,)).fetchone()
                if waiver_type == "faab" and bid_amt > team_curr["faab_balance"]:
                    conn.execute("UPDATE waiver_claims SET status = 'failed', fail_reason = 'Insufficient FAAB funds at processing time', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                    failed_claims += 1
                    continue

                # Roster space check
                roster_count = conn.execute("SELECT COUNT(*) as c FROM roster_players WHERE team_id = ?;", (team_id,)).fetchone()["c"]
                drop_player = None

                if drop_pid:
                    drop_rp = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, drop_pid)).fetchone()
                    if not drop_rp and roster_count >= max_roster:
                        conn.execute("UPDATE waiver_claims SET status = 'failed', fail_reason = 'Drop player no longer on roster and roster is full', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                        failed_claims += 1
                        continue
                    if drop_rp:
                        drop_p_row = conn.execute("SELECT * FROM players WHERE id = ?;", (drop_pid,)).fetchone()
                        drop_player = row_to_player(drop_p_row) if drop_p_row else None
                elif roster_count >= max_roster:
                    conn.execute("UPDATE waiver_claims SET status = 'failed', fail_reason = 'Roster is full and no drop specified', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                    failed_claims += 1
                    continue

                # Claim succeeds! Award player
                if drop_pid and drop_player:
                    conn.execute("DELETE FROM roster_players WHERE team_id = ? AND player_id = ?;", (team_id, drop_pid))
                    conn.execute("DELETE FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week >= ? AND player_id = ?;", (league_id, team_id, curr_week, drop_pid))
                    # Put dropped player on waivers for 48 hours
                    w_until = (now + timedelta(hours=48)).isoformat()
                    conn.execute("""
                    INSERT OR REPLACE INTO player_waiver_status (id, league_id, player_id, waiver_until, created_at)
                    VALUES (?, ?, ?, ?, ?);
                    """, (f"pw_{uuid.uuid4().hex[:12]}", league_id, drop_pid, w_until, now_iso))

                # Add to team roster
                rp_id = f"rp_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO roster_players (id, team_id, player_id, slot, acquired_type, created_at)
                VALUES (?, ?, ?, 'BENCH', 'waiver', ?);
                """, (rp_id, team_id, add_pid, now_iso))

                # Add to current week lineup slots
                ls_id = f"ls_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT OR IGNORE INTO lineup_slots (id, league_id, team_id, week, player_id, slot, is_starter, is_locked, created_at)
                VALUES (?, ?, ?, ?, ?, 'BENCH', 0, 0, ?);
                """, (ls_id, league_id, team_id, curr_week, add_pid, now_iso))

                # Update team FAAB or Rolling priority
                if waiver_type == "faab":
                    conn.execute("UPDATE teams SET faab_balance = faab_balance - ? WHERE id = ?;", (bid_amt, team_id))
                else:
                    # Rolling waiver priority: move winning team to last place
                    max_pri = conn.execute("SELECT MAX(waiver_priority) as m FROM teams WHERE league_id = ?;", (league_id,)).fetchone()["m"] or 1
                    conn.execute("UPDATE teams SET waiver_priority = ? WHERE id = ?;", (max_pri + 1, team_id))
                    # Re-normalize 1..N
                    teams_ordered = conn.execute("SELECT id FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC;", (league_id,)).fetchall()
                    for idx, t_row in enumerate(teams_ordered, 1):
                        conn.execute("UPDATE teams SET waiver_priority = ? WHERE id = ?;", (idx, t_row["id"]))

                # Mark claim successful
                conn.execute("UPDATE waiver_claims SET status = 'successful', processed_at = ? WHERE id = ?;", (now_iso, claim_id))
                already_awarded_players.add(add_pid)
                awarded_claims += 1

                # Record transaction
                desc = f"{team_name} awarded {add_name}"
                if waiver_type == "faab":
                    desc += f" for ${bid_amt} FAAB"
                if drop_player:
                    desc += f", dropping {drop_player.name}"

                tx_id = f"tx_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO transactions (id, league_id, team_id, type, description, details_json, created_at)
                VALUES (?, ?, ?, 'waiver_claim', ?, ?, ?);
                """, (tx_id, league_id, team_id, desc, json.dumps({"add_player_id": add_pid, "drop_player_id": drop_pid, "bid": bid_amt}), now_iso))

                processed_logs.append(desc)

            # Purge expired player_waiver_status
            conn.execute("DELETE FROM player_waiver_status WHERE league_id = ? AND waiver_until <= ?;", (league_id, now_iso))

        summary = {
            "awarded_count": awarded_claims,
            "failed_count": failed_claims,
            "logs": processed_logs,
        }
        return True, f"Waivers processed: {awarded_claims} awarded, {failed_claims} failed.", summary
    finally:
        conn.close()


def get_league_transactions(
    league_id: str,
    transaction_type: Optional[str] = None,
    limit: int = 50,
) -> List[LeagueTransaction]:
    """Retrieve chronological transaction activity wire for a league."""
    conn = get_connection()
    try:
        where = "tx.league_id = ?"
        params: List[Any] = [league_id]
        if transaction_type and transaction_type != "ALL":
            where += " AND tx.type = ?"
            params.append(transaction_type)

        rows = conn.execute(f"""
        SELECT tx.*, t.name as team_name, t.manager_name as mgr_name
        FROM transactions tx
        LEFT JOIN teams t ON tx.team_id = t.id
        WHERE {where}
        ORDER BY tx.created_at DESC
        LIMIT ?;
        """, (*params, limit)).fetchall()

        txs = []
        for r in rows:
            team = None
            if r["team_id"]:
                team = FantasyTeam(
                    id=r["team_id"],
                    league_id=r["league_id"],
                    name=r["team_name"],
                    manager_name=r["mgr_name"],
                    manager_token="",
                )
            tx = row_to_transaction(r, team=team)
            txs.append(tx)
        return txs
    finally:
        conn.close()
