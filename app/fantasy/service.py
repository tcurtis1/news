"""Business service layer for Yoyo Fantasy Football."""

from __future__ import annotations

import json
import secrets
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from app.fantasy.db import get_connection, row_to_league, row_to_player, row_to_team
from app.fantasy.models import (
    AuditLogEntry,
    FantasyTeam,
    League,
    LeagueSettings,
    LeagueStatus,
    Player,
    ScoringFormat,
)


def create_league(
    name: str,
    manager_name: str,
    team_name: str,
    scoring_format: str = ScoringFormat.HALF_PPR.value,
    max_teams: int = 10,
    waiver_type: str = "faab",
    faab_budget: int = 100,
    roster_slots: Optional[Dict[str, int]] = None,
) -> Tuple[League, FantasyTeam]:
    clean_league_name = (name or "").strip() or "My Fantasy League"
    clean_manager = (manager_name or "").strip() or "Commissioner"
    clean_team = (team_name or "").strip() or f"{clean_manager}’s Team"

    if max_teams not in (2, 4, 6, 8, 10, 12, 14, 16):
        max_teams = 10

    settings = LeagueSettings(
        scoring_format=scoring_format if scoring_format in [f.value for f in ScoringFormat] else ScoringFormat.HALF_PPR.value,
        max_teams=max_teams,
        waiver_type=waiver_type if waiver_type in ("faab", "rolling") else "faab",
        faab_budget=max(0, min(1000, faab_budget)),
    )
    if roster_slots:
        settings.roster_slots.update(roster_slots)

    league_id = f"l_{uuid.uuid4().hex[:12]}"
    invite_token = secrets.token_urlsafe(12)
    commish_token = secrets.token_urlsafe(16)
    created_at = datetime.now(timezone.utc).isoformat()

    team_id = f"t_{uuid.uuid4().hex[:12]}"
    manager_token = commish_token  # Commissioner's team token matches or pairs with commish token

    commish_team = FantasyTeam(
        id=team_id,
        league_id=league_id,
        name=clean_team,
        manager_name=clean_manager,
        manager_token=manager_token,
        is_commissioner=True,
        waiver_priority=1,
        faab_balance=settings.faab_budget,
        created_at=created_at,
    )

    league = League(
        id=league_id,
        name=clean_league_name,
        season=2026,
        invite_token=invite_token,
        commissioner_token=commish_token,
        status=LeagueStatus.PRE_DRAFT.value,
        settings=settings,
        created_at=created_at,
        teams=[commish_team],
    )

    audit = AuditLogEntry(
        id=f"a_{uuid.uuid4().hex[:12]}",
        league_id=league_id,
        actor_name=clean_manager,
        action="create_league",
        description=f"Created league '{clean_league_name}' with {settings.scoring_format} scoring, {max_teams} teams.",
        created_at=created_at,
    )

    conn = get_connection()
    with conn:
        conn.execute("""
        INSERT INTO leagues (id, name, season, invite_token, commissioner_token, status, settings_json, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?);
        """, (league.id, league.name, league.season, league.invite_token, league.commissioner_token, league.status, json.dumps(settings.to_dict()), league.created_at))

        conn.execute("""
        INSERT INTO teams (id, league_id, name, manager_name, manager_token, is_commissioner, waiver_priority, faab_balance, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
        """, (commish_team.id, commish_team.league_id, commish_team.name, commish_team.manager_name, commish_team.manager_token, 1, commish_team.waiver_priority, commish_team.faab_balance, commish_team.created_at))

        conn.execute("""
        INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
        VALUES (?, ?, ?, ?, ?, ?);
        """, (audit.id, audit.league_id, audit.actor_name, audit.action, audit.description, audit.created_at))
    conn.close()

    return league, commish_team


def get_league(league_id: str) -> Optional[League]:
    conn = get_connection()
    lr = conn.execute("SELECT * FROM leagues WHERE id = ?;", (league_id,)).fetchone()
    if not lr:
        conn.close()
        return None

    tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (league_id,)).fetchall()
    teams = [row_to_team(r) for r in tr]
    champ = next((t for t in teams if t.id == lr["champion_team_id"]), None) if "champion_team_id" in lr.keys() and lr["champion_team_id"] else None
    second = next((t for t in teams if t.id == lr["second_place_team_id"]), None) if "second_place_team_id" in lr.keys() and lr["second_place_team_id"] else None
    third = next((t for t in teams if t.id == lr["third_place_team_id"]), None) if "third_place_team_id" in lr.keys() and lr["third_place_team_id"] else None
    sacko = next((t for t in teams if t.id == lr["sacko_team_id"]), None) if "sacko_team_id" in lr.keys() and lr["sacko_team_id"] else None
    league = row_to_league(lr, teams=teams, champion_team=champ, second_place_team=second, third_place_team=third, sacko_team=sacko)
    conn.close()
    return league


def get_league_by_invite(invite_token: str) -> Optional[League]:
    conn = get_connection()
    lr = conn.execute("SELECT * FROM leagues WHERE invite_token = ?;", (invite_token,)).fetchone()
    if not lr:
        conn.close()
        return None

    tr = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC, created_at ASC;", (lr["id"],)).fetchall()
    teams = [row_to_team(r) for r in tr]
    champ = next((t for t in teams if t.id == lr["champion_team_id"]), None) if "champion_team_id" in lr.keys() and lr["champion_team_id"] else None
    second = next((t for t in teams if t.id == lr["second_place_team_id"]), None) if "second_place_team_id" in lr.keys() and lr["second_place_team_id"] else None
    third = next((t for t in teams if t.id == lr["third_place_team_id"]), None) if "third_place_team_id" in lr.keys() and lr["third_place_team_id"] else None
    sacko = next((t for t in teams if t.id == lr["sacko_team_id"]), None) if "sacko_team_id" in lr.keys() and lr["sacko_team_id"] else None
    league = row_to_league(lr, teams=teams, champion_team=champ, second_place_team=second, third_place_team=third, sacko_team=sacko)
    conn.close()
    return league


def join_league(invite_token: str, manager_name: str, team_name: str) -> Tuple[Optional[League], Optional[FantasyTeam], Optional[str]]:
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE invite_token = ?;", (invite_token,)).fetchone()
            if not lr:
                return None, None, "Invalid invite link."

            league_id = lr["id"]
            try:
                settings_dict = json.loads(lr["settings_json"])
            except Exception:
                settings_dict = {}
            settings = LeagueSettings.from_dict(settings_dict)

            existing_teams = conn.execute("SELECT * FROM teams WHERE league_id = ? ORDER BY waiver_priority ASC;", (league_id,)).fetchall()
            if len(existing_teams) >= settings.max_teams:
                return None, None, f"League is full ({settings.max_teams} teams max)."

            clean_manager = (manager_name or "").strip() or f"Manager {len(existing_teams) + 1}"
            clean_team = (team_name or "").strip() or f"{clean_manager}’s Team"
            team_id = f"t_{uuid.uuid4().hex[:12]}"
            manager_token = secrets.token_urlsafe(16)
            created_at = datetime.now(timezone.utc).isoformat()
            priority = len(existing_teams) + 1

            team = FantasyTeam(
                id=team_id,
                league_id=league_id,
                name=clean_team,
                manager_name=clean_manager,
                manager_token=manager_token,
                is_commissioner=False,
                waiver_priority=priority,
                faab_balance=settings.faab_budget,
                created_at=created_at,
            )

            conn.execute("""
            INSERT INTO teams (id, league_id, name, manager_name, manager_token, is_commissioner, waiver_priority, faab_balance, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
            """, (team.id, team.league_id, team.name, team.manager_name, team.manager_token, 0, team.waiver_priority, team.faab_balance, team.created_at))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, clean_manager, "join_league", f"{clean_manager} joined league with '{clean_team}'.", created_at))
    finally:
        conn.close()

    full_league = get_league(league_id)
    return full_league, team, None


def update_league_settings(
    league_id: str,
    commissioner_token: str,
    new_settings: Dict[str, Any],
    actor_name: str = "Commissioner",
) -> Tuple[Optional[League], Optional[str]]:
    conn = get_connection()
    try:
        with conn:
            lr = conn.execute("SELECT * FROM leagues WHERE id = ? AND commissioner_token = ?;", (league_id, commissioner_token)).fetchone()
            if not lr:
                return None, "Unauthorized: invalid commissioner credentials."

            try:
                curr_dict = json.loads(lr["settings_json"])
            except Exception:
                curr_dict = {}
            curr_settings = LeagueSettings.from_dict(curr_dict)

            if "scoring_format" in new_settings and new_settings["scoring_format"] in [f.value for f in ScoringFormat]:
                curr_settings.scoring_format = new_settings["scoring_format"]
            if "max_teams" in new_settings:
                try:
                    mt = int(new_settings["max_teams"])
                    if mt in (2, 4, 6, 8, 10, 12, 14, 16):
                        curr_settings.max_teams = mt
                except Exception:
                    pass
            if "waiver_type" in new_settings and new_settings["waiver_type"] in ("faab", "rolling"):
                curr_settings.waiver_type = new_settings["waiver_type"]
            if "faab_budget" in new_settings:
                try:
                    curr_settings.faab_budget = max(0, min(1000, int(new_settings["faab_budget"])))
                except Exception:
                    pass
            if "pick_timer_seconds" in new_settings:
                try:
                    pts = int(new_settings["pick_timer_seconds"])
                    if pts in (0, 15, 30, 45, 60, 90, 120, 180):
                        curr_settings.pick_timer_seconds = pts
                except Exception:
                    pass
            if "trade_review_hours" in new_settings:
                try:
                    curr_settings.trade_review_hours = max(0, min(168, int(new_settings["trade_review_hours"])))
                except Exception:
                    pass
            if "regular_season_weeks" in new_settings:
                try:
                    rsw = int(new_settings["regular_season_weeks"])
                    if 1 <= rsw <= 16:
                        curr_settings.regular_season_weeks = rsw
                except Exception:
                    pass
            if "playoff_teams" in new_settings:
                try:
                    pt = int(new_settings["playoff_teams"])
                    if pt in (2, 4, 6):
                        curr_settings.playoff_teams = pt
                except Exception:
                    pass
            if "playoff_consolation" in new_settings:
                curr_settings.playoff_consolation = bool(new_settings["playoff_consolation"])

            settings_json = json.dumps(curr_settings.to_dict())
            league_name = (new_settings.get("name") or lr["name"]).strip()

            conn.execute("UPDATE leagues SET name = ?, settings_json = ? WHERE id = ?;", (league_name, settings_json, league_id))

            audit_id = f"a_{uuid.uuid4().hex[:12]}"
            created_at = datetime.now(timezone.utc).isoformat()
            conn.execute("""
            INSERT INTO audit_log (id, league_id, actor_name, action, description, created_at)
            VALUES (?, ?, ?, ?, ?, ?);
            """, (audit_id, league_id, actor_name, "update_settings", f"Updated league settings ({curr_settings.scoring_format}, {curr_settings.max_teams} teams, {curr_settings.waiver_type}).", created_at))
    finally:
        conn.close()
    return get_league(league_id), None


def get_user_teams(manager_tokens: List[str]) -> List[Dict[str, Any]]:
    if not manager_tokens:
        return []
    conn = get_connection()
    placeholders = ",".join("?" for _ in manager_tokens)
    query = f"""
    SELECT t.*, l.name as league_name, l.status as league_status, l.settings_json
    FROM teams t
    JOIN leagues l ON t.league_id = l.id
    WHERE t.manager_token IN ({placeholders})
    ORDER BY t.created_at DESC;
    """
    rows = conn.execute(query, manager_tokens).fetchall()
    results = []
    for r in rows:
        results.append({
            "team_id": r["id"],
            "team_name": r["name"],
            "manager_name": r["manager_name"],
            "league_id": r["league_id"],
            "league_name": r["league_name"],
            "league_status": r["league_status"],
            "is_commissioner": bool(r["is_commissioner"]),
            "record": f"{r['wins']}-{r['losses']}" + (f"-{r['ties']}" if r['ties'] else ""),
        })
    conn.close()
    return results


def get_players(query: str = "", position: str = "", limit: int = 50) -> List[Player]:
    conn = get_connection()
    clauses = []
    params: List[Any] = []
    if query.strip():
        clauses.append("name LIKE ?")
        params.append(f"%{query.strip()}%")
    if position.strip() and position.strip().upper() != "ALL":
        clauses.append("position = ?")
        params.append(position.strip().upper())

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    sql = f"SELECT * FROM players {where} ORDER BY adp ASC, projected_points DESC LIMIT ?;"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    players = [row_to_player(r) for r in rows]
    conn.close()
    return players


def get_audit_log(league_id: str, limit: int = 30) -> List[AuditLogEntry]:
    conn = get_connection()
    rows = conn.execute("""
    SELECT * FROM audit_log WHERE league_id = ? ORDER BY created_at DESC LIMIT ?;
    """, (league_id, limit)).fetchall()
    entries = [
        AuditLogEntry(
            id=r["id"],
            league_id=r["league_id"],
            actor_name=r["actor_name"],
            action=r["action"],
            description=r["description"],
            created_at=r["created_at"],
        )
        for r in rows
    ]
    conn.close()
    return entries
