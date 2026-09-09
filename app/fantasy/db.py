"""SQLite storage and schema migrations for Yoyo Fantasy Football."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.fantasy.models import AuditLogEntry, FantasyTeam, League, LeagueSettings, Player
from app.fantasy.players import load_seed_players

_DEFAULT_DB_DIR = Path(os.environ.get("CACHE_DIR", str(Path(__file__).resolve().parent.parent.parent / "data")))
DB_PATH = Path(os.environ.get("FANTASY_DB_PATH", str(_DEFAULT_DB_DIR / "fantasy.db")))

_lock = threading.Lock()


def get_connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=15.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL;")
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db(conn: Optional[sqlite3.Connection] = None) -> None:
    close_at_end = False
    if conn is None:
        conn = get_connection()
        close_at_end = True

    with _lock:
        with conn:
            conn.execute("""
            CREATE TABLE IF NOT EXISTS leagues (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                season INTEGER NOT NULL,
                invite_token TEXT UNIQUE NOT NULL,
                commissioner_token TEXT NOT NULL,
                status TEXT NOT NULL,
                settings_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leagues_invite ON leagues(invite_token);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_leagues_commish ON leagues(commissioner_token);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS teams (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                name TEXT NOT NULL,
                manager_name TEXT NOT NULL,
                manager_token TEXT NOT NULL,
                is_commissioner INTEGER NOT NULL DEFAULT 0,
                waiver_priority INTEGER NOT NULL DEFAULT 1,
                faab_balance INTEGER NOT NULL DEFAULT 100,
                wins INTEGER NOT NULL DEFAULT 0,
                losses INTEGER NOT NULL DEFAULT 0,
                ties INTEGER NOT NULL DEFAULT 0,
                points_for REAL NOT NULL DEFAULT 0.0,
                points_against REAL NOT NULL DEFAULT 0.0,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_league ON teams(league_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_teams_token ON teams(manager_token);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS players (
                id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                position TEXT NOT NULL,
                nfl_team TEXT NOT NULL,
                bye_week INTEGER NOT NULL,
                adp REAL NOT NULL DEFAULT 999.0,
                projected_points REAL NOT NULL DEFAULT 0.0,
                status TEXT NOT NULL DEFAULT 'ACT',
                headshot_url TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_players_pos ON players(position);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS audit_log (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                actor_name TEXT NOT NULL,
                action TEXT NOT NULL,
                description TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_league ON audit_log(league_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS draft_picks (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                round INTEGER NOT NULL,
                pick_number INTEGER NOT NULL,
                overall_pick INTEGER NOT NULL,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                is_auto_pick INTEGER NOT NULL DEFAULT 0,
                selected_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_draft_picks_league ON draft_picks(league_id, overall_pick);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_picks_unique_player ON draft_picks(league_id, player_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS roster_players (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                slot TEXT NOT NULL,
                acquired_type TEXT NOT NULL DEFAULT 'draft',
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_roster_players_team ON roster_players(team_id);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_roster_player_unique ON roster_players(team_id, player_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS draft_queue (
                id TEXT PRIMARY KEY,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                priority INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_draft_queue_team ON draft_queue(team_id, priority);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_draft_queue_unique ON draft_queue(team_id, player_id);")

            # Column migrations for leagues table
            for col, ctype in [
                ("draft_order_json", "TEXT DEFAULT '[]'"),
                ("current_overall_pick", "INTEGER NOT NULL DEFAULT 1"),
                ("current_pick_deadline", "TEXT"),
                ("draft_paused_seconds", "INTEGER"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE leagues ADD COLUMN {col} {ctype};")
                except sqlite3.OperationalError:
                    pass

            # Seed or expand player catalog
            players = load_seed_players()
            for p in players:
                conn.execute("""
                INSERT OR REPLACE INTO players (id, name, position, nfl_team, bye_week, adp, projected_points, status, headshot_url)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?);
                """, (p.id, p.name, p.position, p.nfl_team, p.bye_week, p.adp, p.projected_points, p.status, p.headshot_url))

    if close_at_end:
        conn.close()


def row_to_team(r: sqlite3.Row) -> FantasyTeam:
    return FantasyTeam(
        id=r["id"],
        league_id=r["league_id"],
        name=r["name"],
        manager_name=r["manager_name"],
        manager_token=r["manager_token"],
        is_commissioner=bool(r["is_commissioner"]),
        waiver_priority=r["waiver_priority"],
        faab_balance=r["faab_balance"],
        wins=r["wins"],
        losses=r["losses"],
        ties=r["ties"],
        points_for=r["points_for"],
        points_against=r["points_against"],
        created_at=r["created_at"],
    )


def row_to_league(r: sqlite3.Row, teams: Optional[List[FantasyTeam]] = None) -> League:
    try:
        settings_dict = json.loads(r["settings_json"])
    except Exception:
        settings_dict = {}

    draft_order = []
    if "draft_order_json" in r.keys() and r["draft_order_json"]:
        try:
            draft_order = json.loads(r["draft_order_json"])
        except Exception:
            draft_order = []

    return League(
        id=r["id"],
        name=r["name"],
        season=r["season"],
        invite_token=r["invite_token"],
        commissioner_token=r["commissioner_token"],
        status=r["status"],
        settings=LeagueSettings.from_dict(settings_dict),
        created_at=r["created_at"],
        teams=teams or [],
        draft_order=draft_order,
        current_overall_pick=r["current_overall_pick"] if "current_overall_pick" in r.keys() else 1,
        current_pick_deadline=r["current_pick_deadline"] if "current_pick_deadline" in r.keys() else None,
        draft_paused_seconds=r["draft_paused_seconds"] if "draft_paused_seconds" in r.keys() else None,
    )


def row_to_player(r: sqlite3.Row) -> Player:
    return Player(
        id=r["id"],
        name=r["name"],
        position=r["position"],
        nfl_team=r["nfl_team"],
        bye_week=r["bye_week"],
        adp=r["adp"],
        projected_points=r["projected_points"],
        status=r["status"],
        headshot_url=r["headshot_url"],
    )


def row_to_draft_pick(r: sqlite3.Row, player: Optional[Player] = None) -> DraftPick:
    return DraftPick(
        id=r["id"],
        league_id=r["league_id"],
        round=r["round"],
        pick_number=r["pick_number"],
        overall_pick=r["overall_pick"],
        team_id=r["team_id"],
        player_id=r["player_id"],
        selected_at=r["selected_at"],
        is_auto_pick=bool(r["is_auto_pick"]),
        player=player,
        team_name=r["team_name"] if "team_name" in r.keys() else None,
        manager_name=r["manager_name"] if "manager_name" in r.keys() else None,
    )


def row_to_roster_player(r: sqlite3.Row, player: Optional[Player] = None) -> RosterPlayer:
    return RosterPlayer(
        id=r["id"],
        team_id=r["team_id"],
        player_id=r["player_id"],
        slot=r["slot"],
        acquired_type=r["acquired_type"],
        created_at=r["created_at"],
        player=player,
    )
