"""Trade Machine: multi-player trade proposals, review, veto, and atomic execution."""

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
    row_to_trade,
)
from app.fantasy.models import (
    AuditLogEntry,
    FantasyTeam,
    League,
    LeagueTransaction,
    Player,
    Trade,
    TradeItem,
)


def propose_trade(
    league_id: str,
    proposer_team_id: str,
    recipient_team_id: str,
    proposer_player_ids: List[str],
    recipient_player_ids: List[str],
    note: str = "",
    actor_token: str = "",
) -> Tuple[bool, str, Optional[str]]:
    """Propose a player trade between two teams."""
    if proposer_team_id == recipient_team_id:
        return False, "Cannot propose a trade to your own team.", None

    if not proposer_player_ids or not recipient_player_ids:
        return False, "Trades must involve at least one player from each team.", None

    conn = get_connection()
    try:
        now = datetime.now(timezone.utc)
        now_iso = now.isoformat()
        expires_at = (now + timedelta(days=2)).isoformat()

        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        if not lr:
            return False, "League not found.", None

        league = row_to_league(lr)
        prop_tr = conn.execute("SELECT * FROM teams WHERE id = ? AND league_id = ?;", (proposer_team_id, league_id)).fetchone()
        recip_tr = conn.execute("SELECT * FROM teams WHERE id = ? AND league_id = ?;", (recipient_team_id, league_id)).fetchone()

        if not prop_tr or not recip_tr:
            return False, "One or both teams not found in this league.", None

        proposer_team = row_to_team(prop_tr)
        recipient_team = row_to_team(recip_tr)

        if actor_token != league.commissioner_token and actor_token != proposer_team.manager_token:
            return False, "Unauthorized: Proposer manager token required.", None

        with conn:
            # Verify proposer owns all proposer_player_ids
            for pid in proposer_player_ids:
                rp = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (proposer_team_id, pid)).fetchone()
                if not rp:
                    p = conn.execute("SELECT name FROM players WHERE id = ?;", (pid,)).fetchone()
                    name = p["name"] if p else pid
                    return False, f"Your team does not own {name}.", None

            # Verify recipient owns all recipient_player_ids
            for pid in recipient_player_ids:
                rp = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (recipient_team_id, pid)).fetchone()
                if not rp:
                    p = conn.execute("SELECT name FROM players WHERE id = ?;", (pid,)).fetchone()
                    name = p["name"] if p else pid
                    return False, f"{recipient_team.name} does not own {name}.", None

            # Check locked status in current lineup
            curr_week = league.current_week or 1
            for pid in proposer_player_ids + recipient_player_ids:
                locked = conn.execute("""
                SELECT is_locked, slot FROM lineup_slots
                WHERE league_id = ? AND week = ? AND player_id = ?;
                """, (league_id, curr_week, pid)).fetchone()
                if locked and locked["is_locked"]:
                    p = conn.execute("SELECT name FROM players WHERE id = ?;", (pid,)).fetchone()
                    name = p["name"] if p else pid
                    return False, f"Cannot trade {name}: player is currently locked in an active game.", None

            # Check roster count overflow limits
            max_roster = league.settings.total_rounds or 15
            prop_count = conn.execute("SELECT COUNT(*) as c FROM roster_players WHERE team_id = ?;", (proposer_team_id,)).fetchone()["c"]
            recip_count = conn.execute("SELECT COUNT(*) as c FROM roster_players WHERE team_id = ?;", (recipient_team_id,)).fetchone()["c"]

            net_prop = prop_count - len(proposer_player_ids) + len(recipient_player_ids)
            net_recip = recip_count - len(recipient_player_ids) + len(proposer_player_ids)

            if net_prop > max_roster:
                return False, f"Trade would exceed your roster limit ({net_prop}/{max_roster}).", None
            if net_recip > max_roster:
                return False, f"Trade would exceed {recipient_team.name}'s roster limit ({net_recip}/{max_roster}).", None

            trade_id = f"tr_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO trades (id, league_id, proposer_team_id, recipient_team_id, status, note, created_at, expires_at)
            VALUES (?, ?, ?, ?, 'proposed', ?, ?, ?);
            """, (trade_id, league_id, proposer_team_id, recipient_team_id, note or "", now_iso, expires_at))

            # Insert trade items
            for pid in proposer_player_ids:
                item_id = f"ti_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO trade_items (id, trade_id, from_team_id, to_team_id, player_id)
                VALUES (?, ?, ?, ?, ?);
                """, (item_id, trade_id, proposer_team_id, recipient_team_id, pid))

            for pid in recipient_player_ids:
                item_id = f"ti_{uuid.uuid4().hex[:12]}"
                conn.execute("""
                INSERT INTO trade_items (id, trade_id, from_team_id, to_team_id, player_id)
                VALUES (?, ?, ?, ?, ?);
                """, (item_id, trade_id, recipient_team_id, proposer_team_id, pid))

            # Audit log
            desc = f"{proposer_team.name} offered a trade to {recipient_team.name} ({len(proposer_player_ids)} players for {len(recipient_player_ids)} players)."
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, 'propose_trade', ?, ?);
            """, (f"a_{uuid.uuid4().hex[:12]}", league_id, proposer_team.manager_name, desc, now_iso))

        return True, f"Trade proposal sent to {recipient_team.name}.", trade_id
    finally:
        conn.close()


def _execute_trade_atomic(conn, trade_id: str, league_id: str) -> Tuple[bool, str]:
    """Internal helper to atomically swap players between teams."""
    now_iso = datetime.now(timezone.utc).isoformat()
    trade_row = conn.execute("SELECT * FROM trades WHERE id = ?;", (trade_id,)).fetchone()
    if not trade_row:
        return False, "Trade not found."

    lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
    league = row_to_league(lr)
    curr_week = league.current_week or 1

    prop_team_id = trade_row["proposer_team_id"]
    recip_team_id = trade_row["recipient_team_id"]

    prop_tr = conn.execute("SELECT name FROM teams WHERE id = ?;", (prop_team_id,)).fetchone()
    recip_tr = conn.execute("SELECT name FROM teams WHERE id = ?;", (recip_team_id,)).fetchone()
    prop_name = prop_tr["name"] if prop_tr else "Team 1"
    recip_name = recip_tr["name"] if recip_tr else "Team 2"

    items = conn.execute("SELECT * FROM trade_items WHERE trade_id = ?;", (trade_id,)).fetchall()

    # Re-verify ownership before swap
    for item in items:
        from_tid = item["from_team_id"]
        pid = item["player_id"]
        owned = conn.execute("SELECT 1 FROM roster_players WHERE team_id = ? AND player_id = ?;", (from_tid, pid)).fetchone()
        if not owned:
            p = conn.execute("SELECT name FROM players WHERE id = ?;", (pid,)).fetchone()
            name = p["name"] if p else pid
            conn.execute("UPDATE trades SET status = 'cancelled', processed_at = ? WHERE id = ?;", (now_iso, trade_id))
            return False, f"Trade invalid: player {name} is no longer with the trading team."

    # Execute swap
    prop_received = []
    recip_received = []

    for item in items:
        from_tid = item["from_team_id"]
        to_tid = item["to_team_id"]
        pid = item["player_id"]

        p_row = conn.execute("SELECT name, position, nfl_team FROM players WHERE id = ?;", (pid,)).fetchone()
        p_desc = f"{p_row['name']} ({p_row['position']})" if p_row else pid

        if to_tid == prop_team_id:
            prop_received.append(p_desc)
        else:
            recip_received.append(p_desc)

        # Update roster ownership
        conn.execute("DELETE FROM roster_players WHERE team_id = ? AND player_id = ?;", (from_tid, pid))
        conn.execute("""
        INSERT INTO roster_players (id, team_id, player_id, slot, acquired_type, created_at)
        VALUES (?, ?, ?, 'BENCH', 'trade', ?);
        """, (f"rp_{uuid.uuid4().hex[:12]}", to_tid, pid, now_iso))

        # Update lineup slots for current week
        conn.execute("DELETE FROM lineup_slots WHERE league_id = ? AND team_id = ? AND week >= ? AND player_id = ?;", (league_id, from_tid, curr_week, pid))
        conn.execute("""
        INSERT OR IGNORE INTO lineup_slots (id, league_id, team_id, week, player_id, slot, is_starter, is_locked, created_at)
        VALUES (?, ?, ?, ?, ?, 'BENCH', 0, 0, ?);
        """, (f"ls_{uuid.uuid4().hex[:12]}", league_id, to_tid, curr_week, pid, now_iso))

    conn.execute("UPDATE trades SET status = 'processed', processed_at = ? WHERE id = ?;", (now_iso, trade_id))

    # Record in transactions
    tx_desc = f"TRADE COMPLETED: {prop_name} received {', '.join(prop_received)}; {recip_name} received {', '.join(recip_received)}."
    conn.execute("""
    INSERT INTO transactions (id, league_id, team_id, type, description, details_json, created_at)
    VALUES (?, ?, ?, 'trade', ?, ?, ?);
    """, (f"tx_{uuid.uuid4().hex[:12]}", league_id, prop_team_id, tx_desc, json.dumps({"trade_id": trade_id}), now_iso))

    return True, tx_desc


def respond_to_trade(
    league_id: str,
    trade_id: str,
    action: str,  # accept, reject, cancel, veto, approve
    actor_token: str = "",
) -> Tuple[bool, str]:
    """Accept, reject, cancel, veto, or commissioner-approve a trade."""
    conn = get_connection()
    try:
        now_iso = datetime.now(timezone.utc).isoformat()
        lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
        tr = conn.execute("SELECT * FROM trades WHERE id = ? AND league_id = ?;", (trade_id, league_id)).fetchone()
        if not lr or not tr:
            return False, "League or trade not found."

        league = row_to_league(lr)
        is_commish = (actor_token == league.commissioner_token)

        prop_team = conn.execute("SELECT manager_token, name FROM teams WHERE id = ?;", (tr["proposer_team_id"],)).fetchone()
        recip_team = conn.execute("SELECT manager_token, name FROM teams WHERE id = ?;", (tr["recipient_team_id"],)).fetchone()

        is_proposer = bool(prop_team and actor_token == prop_team["manager_token"])
        is_recipient = bool(recip_team and actor_token == recip_team["manager_token"])

        act = (action or "").lower().strip()

        with conn:
            if act == "cancel":
                if not is_proposer and not is_commish:
                    return False, "Only the proposing manager or commissioner can cancel this trade."
                if tr["status"] != "proposed":
                    return False, f"Trade is already {tr['status']} and cannot be cancelled."
                conn.execute("UPDATE trades SET status = 'cancelled', processed_at = ? WHERE id = ?;", (now_iso, trade_id))
                return True, "Trade proposal cancelled."

            elif act == "reject":
                if not is_recipient and not is_commish:
                    return False, "Only the recipient team manager can reject this trade."
                if tr["status"] != "proposed":
                    return False, f"Trade is already {tr['status']} and cannot be rejected."
                conn.execute("UPDATE trades SET status = 'rejected', processed_at = ? WHERE id = ?;", (now_iso, trade_id))
                return True, "Trade proposal rejected."

            elif act == "veto":
                if not is_commish:
                    return False, "Only the commissioner can veto a trade."
                if tr["status"] in ("processed", "vetoed", "rejected", "cancelled"):
                    return False, f"Cannot veto trade in status '{tr['status']}'."
                conn.execute("UPDATE trades SET status = 'vetoed', processed_at = ? WHERE id = ?;", (now_iso, trade_id))
                return True, "Trade was vetoed by commissioner."

            elif act == "accept":
                if not is_recipient and not is_commish:
                    return False, "Only the receiving team manager can accept this trade."
                if tr["status"] != "proposed":
                    return False, f"Trade is already {tr['status']} and cannot be accepted."

                # Check if instant trade execution applies
                review_hours = league.settings.trade_review_hours or 0
                if review_hours <= 0 or is_commish:
                    ok, msg = _execute_trade_atomic(conn, trade_id, league_id)
                    return ok, msg
                else:
                    # Place in accepted review period
                    exp = (datetime.now(timezone.utc) + timedelta(hours=review_hours)).isoformat()
                    conn.execute("UPDATE trades SET status = 'accepted', expires_at = ? WHERE id = ?;", (exp, trade_id))
                    return True, f"Trade accepted! In review period ({review_hours}h) before finalizing."

            elif act == "approve":
                if not is_commish:
                    return False, "Only the commissioner can force-approve a trade."
                if tr["status"] not in ("proposed", "accepted"):
                    return False, f"Cannot approve trade in status '{tr['status']}'."
                ok, msg = _execute_trade_atomic(conn, trade_id, league_id)
                return ok, msg

            else:
                return False, f"Unknown trade action: '{action}'."
    finally:
        conn.close()


def get_league_trades(
    league_id: str,
    team_id: Optional[str] = None,
    status: Optional[str] = None,
) -> List[Trade]:
    """Retrieve trades for a league, optionally filtered by team and status."""
    conn = get_connection()
    try:
        where_parts = ["t.league_id = ?"]
        params: List[Any] = [league_id]

        if team_id:
            where_parts.append("(t.proposer_team_id = ? OR t.recipient_team_id = ?)")
            params.extend([team_id, team_id])

        if status and status != "ALL":
            where_parts.append("t.status = ?")
            params.append(status)

        where_sql = " AND ".join(where_parts)

        trade_rows = conn.execute(f"""
        SELECT t.*
        FROM trades t
        WHERE {where_sql}
        ORDER BY t.created_at DESC;
        """, params).fetchall()

        trades = []
        for tr in trade_rows:
            trade_id = tr["id"]
            p_tr = conn.execute("SELECT * FROM teams WHERE id = ?;", (tr["proposer_team_id"],)).fetchone()
            r_tr = conn.execute("SELECT * FROM teams WHERE id = ?;", (tr["recipient_team_id"],)).fetchone()

            prop_team = row_to_team(p_tr) if p_tr else None
            recip_team = row_to_team(r_tr) if r_tr else None

            # Get trade items with player objects
            items = conn.execute("""
            SELECT ti.*, p.name as p_name, p.position as p_pos, p.nfl_team as p_nfl, p.headshot_url as p_headshot
            FROM trade_items ti
            JOIN players p ON ti.player_id = p.id
            WHERE ti.trade_id = ?;
            """, (trade_id,)).fetchall()

            proposer_sends: List[Player] = []
            recipient_sends: List[Player] = []

            for item in items:
                p = Player(
                    id=item["player_id"],
                    name=item["p_name"],
                    position=item["p_pos"],
                    nfl_team=item["p_nfl"],
                    bye_week=0,
                    headshot_url=item["p_headshot"],
                )
                if item["from_team_id"] == tr["proposer_team_id"]:
                    proposer_sends.append(p)
                else:
                    recipient_sends.append(p)

            trade = row_to_trade(
                tr,
                proposer_team=prop_team,
                recipient_team=recip_team,
                proposer_sends=proposer_sends,
                recipient_sends=recipient_sends,
            )
            trades.append(trade)
        return trades
    finally:
        conn.close()


def get_trade_details(trade_id: str) -> Optional[Trade]:
    """Retrieve full details of a specific trade."""
    conn = get_connection()
    try:
        tr = conn.execute("SELECT * FROM trades WHERE id = ?;", (trade_id,)).fetchone()
        if not tr:
            return None
        league_id = tr["league_id"]
        res = get_league_trades(league_id, status="ALL")
        return next((t for t in res if t.id == trade_id), None)
    finally:
        conn.close()
