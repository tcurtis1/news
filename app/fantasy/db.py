"""SQLite storage and schema migrations for Yoyo Fantasy Football."""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.fantasy.models import (
    AuditLogEntry,
    DraftPick,
    DraftQueueItem,
    FantasyTeam,
    League,
    LeagueSettings,
    LeagueTransaction,
    LineupSlot,
    Matchup,
    Player,
    PlayerGameStats,
    PlayerWaiverStatus,
    RosterPlayer,
    Trade,
    TradeItem,
    WaiverClaim,
)
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

            conn.execute("""
            CREATE TABLE IF NOT EXISTS matchups (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                week INTEGER NOT NULL,
                home_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                away_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                home_score REAL NOT NULL DEFAULT 0.0,
                away_score REAL NOT NULL DEFAULT 0.0,
                home_projected REAL NOT NULL DEFAULT 0.0,
                away_projected REAL NOT NULL DEFAULT 0.0,
                is_final INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_matchups_league_week ON matchups(league_id, week);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_matchups_home ON matchups(home_team_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_matchups_away ON matchups(away_team_id);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_matchups_unique_match ON matchups(league_id, week, home_team_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS lineup_slots (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                week INTEGER NOT NULL,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                slot TEXT NOT NULL,
                is_starter INTEGER NOT NULL DEFAULT 1,
                is_locked INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_lineup_team_week ON lineup_slots(league_id, team_id, week);")
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_lineup_slot_unique ON lineup_slots(league_id, team_id, week, player_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS player_game_stats (
                id TEXT PRIMARY KEY,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                season INTEGER NOT NULL,
                week INTEGER NOT NULL,
                pass_yd INTEGER NOT NULL DEFAULT 0,
                pass_td INTEGER NOT NULL DEFAULT 0,
                pass_int INTEGER NOT NULL DEFAULT 0,
                rush_yd INTEGER NOT NULL DEFAULT 0,
                rush_td INTEGER NOT NULL DEFAULT 0,
                rec INTEGER NOT NULL DEFAULT 0,
                rec_yd INTEGER NOT NULL DEFAULT 0,
                rec_td INTEGER NOT NULL DEFAULT 0,
                fumble_lost INTEGER NOT NULL DEFAULT 0,
                two_pt INTEGER NOT NULL DEFAULT 0,
                fg_made INTEGER NOT NULL DEFAULT 0,
                pat_made INTEGER NOT NULL DEFAULT 0,
                dst_sack INTEGER NOT NULL DEFAULT 0,
                dst_int INTEGER NOT NULL DEFAULT 0,
                dst_fumble_rec INTEGER NOT NULL DEFAULT 0,
                dst_safety INTEGER NOT NULL DEFAULT 0,
                dst_td INTEGER NOT NULL DEFAULT 0,
                dst_points_allowed INTEGER NOT NULL DEFAULT 0,
                raw_stats_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_player_stats_uniq ON player_game_stats(player_id, season, week);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_player_stats_lookup ON player_game_stats(season, week);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS waiver_claims (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                add_player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                drop_player_id TEXT REFERENCES players(id) ON DELETE SET NULL,
                bid_amount INTEGER NOT NULL DEFAULT 0,
                priority INTEGER NOT NULL DEFAULT 1,
                status TEXT NOT NULL DEFAULT 'pending',
                fail_reason TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_waiver_claims_league ON waiver_claims(league_id, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_waiver_claims_team ON waiver_claims(team_id, status);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS player_waiver_status (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE,
                waiver_until TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_player_waiver_unique ON player_waiver_status(league_id, player_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_player_waiver_until ON player_waiver_status(league_id, waiver_until);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                proposer_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                recipient_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                status TEXT NOT NULL DEFAULT 'proposed',
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                expires_at TEXT,
                processed_at TEXT
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_league ON trades(league_id, status);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_proposer ON trades(proposer_team_id);")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_recipient ON trades(recipient_team_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_items (
                id TEXT PRIMARY KEY,
                trade_id TEXT NOT NULL REFERENCES trades(id) ON DELETE CASCADE,
                from_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                to_team_id TEXT NOT NULL REFERENCES teams(id) ON DELETE CASCADE,
                player_id TEXT NOT NULL REFERENCES players(id) ON DELETE CASCADE
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_trade_items_trade ON trade_items(trade_id);")

            conn.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id TEXT PRIMARY KEY,
                league_id TEXT NOT NULL REFERENCES leagues(id) ON DELETE CASCADE,
                team_id TEXT REFERENCES teams(id) ON DELETE SET NULL,
                type TEXT NOT NULL,
                description TEXT NOT NULL,
                details_json TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL
            );
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_transactions_league ON transactions(league_id, created_at);")

            # Column migrations for leagues table
            for col, ctype in [
                ("draft_order_json", "TEXT DEFAULT '[]'"),
                ("current_overall_pick", "INTEGER NOT NULL DEFAULT 1"),
                ("current_pick_deadline", "TEXT"),
                ("draft_paused_seconds", "INTEGER"),
                ("current_week", "INTEGER NOT NULL DEFAULT 1"),
                ("champion_team_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
                ("second_place_team_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
                ("third_place_team_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
                ("sacko_team_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE leagues ADD COLUMN {col} {ctype};")
                except sqlite3.OperationalError:
                    pass

            # Column migrations for teams table
            for col, ctype in [
                ("playoff_seed", "INTEGER"),
                ("final_rank", "INTEGER"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE teams ADD COLUMN {col} {ctype};")
                except sqlite3.OperationalError:
                    pass

            # Column migrations for matchups table
            for col, ctype in [
                ("matchup_type", "TEXT DEFAULT 'regular'"),
                ("bracket_slot", "TEXT"),
                ("playoff_round", "INTEGER DEFAULT 0"),
                ("home_seed", "INTEGER"),
                ("away_seed", "INTEGER"),
                ("winner_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
                ("loser_id", "TEXT REFERENCES teams(id) ON DELETE SET NULL"),
            ]:
                try:
                    conn.execute(f"ALTER TABLE matchups ADD COLUMN {col} {ctype};")
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
        playoff_seed=r["playoff_seed"] if "playoff_seed" in r.keys() else None,
        final_rank=r["final_rank"] if "final_rank" in r.keys() else None,
        created_at=r["created_at"],
    )


def row_to_league(
    r: sqlite3.Row,
    teams: Optional[List[FantasyTeam]] = None,
    champion_team: Optional[FantasyTeam] = None,
    second_place_team: Optional[FantasyTeam] = None,
    third_place_team: Optional[FantasyTeam] = None,
    sacko_team: Optional[FantasyTeam] = None,
) -> League:
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
        current_week=r["current_week"] if "current_week" in r.keys() else 1,
        champion_team_id=r["champion_team_id"] if "champion_team_id" in r.keys() else None,
        second_place_team_id=r["second_place_team_id"] if "second_place_team_id" in r.keys() else None,
        third_place_team_id=r["third_place_team_id"] if "third_place_team_id" in r.keys() else None,
        sacko_team_id=r["sacko_team_id"] if "sacko_team_id" in r.keys() else None,
        champion_team=champion_team,
        second_place_team=second_place_team,
        third_place_team=third_place_team,
        sacko_team=sacko_team,
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


def row_to_matchup(
    r: sqlite3.Row,
    home_team: Optional[FantasyTeam] = None,
    away_team: Optional[FantasyTeam] = None,
) -> Matchup:
    return Matchup(
        id=r["id"],
        league_id=r["league_id"],
        week=r["week"],
        home_team_id=r["home_team_id"],
        away_team_id=r["away_team_id"],
        home_score=float(r["home_score"]),
        away_score=float(r["away_score"]),
        home_projected=float(r["home_projected"]) if "home_projected" in r.keys() else 0.0,
        away_projected=float(r["away_projected"]) if "away_projected" in r.keys() else 0.0,
        is_final=bool(r["is_final"]),
        created_at=r["created_at"],
        home_team=home_team,
        away_team=away_team,
        matchup_type=r["matchup_type"] if "matchup_type" in r.keys() and r["matchup_type"] else "regular",
        bracket_slot=r["bracket_slot"] if "bracket_slot" in r.keys() else None,
        playoff_round=r["playoff_round"] if "playoff_round" in r.keys() and r["playoff_round"] is not None else 0,
        home_seed=r["home_seed"] if "home_seed" in r.keys() else None,
        away_seed=r["away_seed"] if "away_seed" in r.keys() else None,
        winner_id=r["winner_id"] if "winner_id" in r.keys() else None,
        loser_id=r["loser_id"] if "loser_id" in r.keys() else None,
    )


def row_to_lineup_slot(r: sqlite3.Row, player: Optional[Player] = None) -> LineupSlot:
    return LineupSlot(
        id=r["id"],
        league_id=r["league_id"],
        team_id=r["team_id"],
        week=r["week"],
        player_id=r["player_id"],
        slot=r["slot"],
        is_starter=bool(r["is_starter"]),
        is_locked=bool(r["is_locked"]) if "is_locked" in r.keys() else False,
        created_at=r["created_at"],
        player=player,
    )


def row_to_player_game_stats(r: sqlite3.Row) -> PlayerGameStats:
    return PlayerGameStats(
        id=r["id"],
        player_id=r["player_id"],
        season=r["season"],
        week=r["week"],
        pass_yd=r["pass_yd"],
        pass_td=r["pass_td"],
        pass_int=r["pass_int"],
        rush_yd=r["rush_yd"],
        rush_td=r["rush_td"],
        rec=r["rec"],
        rec_yd=r["rec_yd"],
        rec_td=r["rec_td"],
        fumble_lost=r["fumble_lost"],
        two_pt=r["two_pt"],
        fg_made=r["fg_made"],
        pat_made=r["pat_made"],
        dst_sack=r["dst_sack"],
        dst_int=r["dst_int"],
        dst_fumble_rec=r["dst_fumble_rec"],
        dst_safety=r["dst_safety"],
        dst_td=r["dst_td"],
        dst_points_allowed=r["dst_points_allowed"],
        raw_stats_json=r["raw_stats_json"] if "raw_stats_json" in r.keys() else "{}",
        updated_at=r["updated_at"],
    )


def row_to_waiver_claim(
    r: sqlite3.Row,
    add_player: Optional[Player] = None,
    drop_player: Optional[Player] = None,
    team: Optional[FantasyTeam] = None,
) -> WaiverClaim:
    return WaiverClaim(
        id=r["id"],
        league_id=r["league_id"],
        team_id=r["team_id"],
        add_player_id=r["add_player_id"],
        drop_player_id=r["drop_player_id"],
        bid_amount=r["bid_amount"],
        priority=r["priority"],
        status=r["status"],
        fail_reason=r["fail_reason"],
        created_at=r["created_at"],
        processed_at=r["processed_at"],
        add_player=add_player,
        drop_player=drop_player,
        team=team,
    )


def row_to_trade(
    r: sqlite3.Row,
    proposer_team: Optional[FantasyTeam] = None,
    recipient_team: Optional[FantasyTeam] = None,
    proposer_sends: Optional[List[Player]] = None,
    recipient_sends: Optional[List[Player]] = None,
) -> Trade:
    return Trade(
        id=r["id"],
        league_id=r["league_id"],
        proposer_team_id=r["proposer_team_id"],
        recipient_team_id=r["recipient_team_id"],
        status=r["status"],
        note=r["note"] if "note" in r.keys() else "",
        created_at=r["created_at"],
        expires_at=r["expires_at"] if "expires_at" in r.keys() else None,
        processed_at=r["processed_at"] if "processed_at" in r.keys() else None,
        proposer_team=proposer_team,
        recipient_team=recipient_team,
        proposer_sends=proposer_sends or [],
        recipient_sends=recipient_sends or [],
    )


def row_to_transaction(r: sqlite3.Row, team: Optional[FantasyTeam] = None) -> LeagueTransaction:
    return LeagueTransaction(
        id=r["id"],
        league_id=r["league_id"],
        team_id=r["team_id"],
        type=r["type"],
        description=r["description"],
        details_json=r["details_json"] if "details_json" in r.keys() else "{}",
        created_at=r["created_at"],
        team=team,
    )
