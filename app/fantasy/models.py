"""Data models for Yoyo News Fantasy NFL Football."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional
import uuid


class ScoringFormat(str, Enum):
    HALF_PPR = "half_ppr"
    FULL_PPR = "full_ppr"
    STANDARD = "standard"


class LeagueStatus(str, Enum):
    PRE_DRAFT = "pre_draft"
    DRAFTING = "drafting"
    DRAFT_PAUSED = "draft_paused"
    IN_SEASON = "in_season"
    PLAYOFFS = "playoffs"
    COMPLETE = "complete"


DEFAULT_ROSTER_SLOTS = {
    "QB": 1,
    "RB": 2,
    "WR": 2,
    "TE": 1,
    "FLEX": 1,  # RB / WR / TE
    "K": 1,
    "DST": 1,
    "BENCH": 6,
    "IR": 1,
}

SCORING_PRESETS = {
    ScoringFormat.HALF_PPR.value: {
        "pass_yd": 0.04,        # 1 pt per 25 pass yds
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,         # 1 pt per 10 rush yds
        "rush_td": 6.0,
        "rec": 0.5,             # 0.5 point per reception
        "rec_yd": 0.1,          # 1 pt per 10 rec yds
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_pt": 2.0,
        "fg_0_39": 3.0,
        "fg_40_49": 4.0,
        "fg_50_plus": 5.0,
        "pat_made": 1.0,
        "dst_sack": 1.0,
        "dst_int": 2.0,
        "dst_fumble_rec": 2.0,
        "dst_safety": 2.0,
        "dst_td": 6.0,
        "dst_points_0": 10.0,
        "dst_points_1_6": 7.0,
        "dst_points_7_13": 4.0,
        "dst_points_14_20": 1.0,
        "dst_points_21_27": 0.0,
        "dst_points_28_34": -1.0,
        "dst_points_35_plus": -4.0,
    },
    ScoringFormat.FULL_PPR.value: {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rec": 1.0,             # 1.0 point per reception
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_pt": 2.0,
        "fg_0_39": 3.0,
        "fg_40_49": 4.0,
        "fg_50_plus": 5.0,
        "pat_made": 1.0,
        "dst_sack": 1.0,
        "dst_int": 2.0,
        "dst_fumble_rec": 2.0,
        "dst_safety": 2.0,
        "dst_td": 6.0,
        "dst_points_0": 10.0,
        "dst_points_1_6": 7.0,
        "dst_points_7_13": 4.0,
        "dst_points_14_20": 1.0,
        "dst_points_21_27": 0.0,
        "dst_points_28_34": -1.0,
        "dst_points_35_plus": -4.0,
    },
    ScoringFormat.STANDARD.value: {
        "pass_yd": 0.04,
        "pass_td": 4.0,
        "pass_int": -2.0,
        "rush_yd": 0.1,
        "rush_td": 6.0,
        "rec": 0.0,             # 0 point per reception
        "rec_yd": 0.1,
        "rec_td": 6.0,
        "fumble_lost": -2.0,
        "two_pt": 2.0,
        "fg_0_39": 3.0,
        "fg_40_49": 4.0,
        "fg_50_plus": 5.0,
        "pat_made": 1.0,
        "dst_sack": 1.0,
        "dst_int": 2.0,
        "dst_fumble_rec": 2.0,
        "dst_safety": 2.0,
        "dst_td": 6.0,
        "dst_points_0": 10.0,
        "dst_points_1_6": 7.0,
        "dst_points_7_13": 4.0,
        "dst_points_14_20": 1.0,
        "dst_points_21_27": 0.0,
        "dst_points_28_34": -1.0,
        "dst_points_35_plus": -4.0,
    },
}


@dataclass
class LeagueSettings:
    scoring_format: str = ScoringFormat.HALF_PPR.value
    max_teams: int = 10
    waiver_type: str = "faab"       # "faab" or "rolling"
    faab_budget: int = 100
    trade_review_hours: int = 24
    draft_type: str = "snake"       # "snake"
    pick_timer_seconds: int = 60    # 60s per pick; 0 for untimed
    total_rounds: int = 15          # 15 rounds = 9 starters + 6 bench
    roster_slots: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ROSTER_SLOTS))
    regular_season_weeks: int = 14
    playoff_teams: int = 4          # 2, 4, or 6
    playoff_consolation: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scoring_format": self.scoring_format,
            "max_teams": self.max_teams,
            "waiver_type": self.waiver_type,
            "faab_budget": self.faab_budget,
            "trade_review_hours": self.trade_review_hours,
            "draft_type": self.draft_type,
            "pick_timer_seconds": self.pick_timer_seconds,
            "total_rounds": self.total_rounds,
            "roster_slots": dict(self.roster_slots),
            "regular_season_weeks": self.regular_season_weeks,
            "playoff_teams": self.playoff_teams,
            "playoff_consolation": self.playoff_consolation,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> LeagueSettings:
        slots = dict(DEFAULT_ROSTER_SLOTS)
        if isinstance(data.get("roster_slots"), dict):
            slots.update(data["roster_slots"])
        return cls(
            scoring_format=str(data.get("scoring_format") or ScoringFormat.HALF_PPR.value),
            max_teams=int(data.get("max_teams") or 10),
            waiver_type=str(data.get("waiver_type") or "faab"),
            faab_budget=int(data.get("faab_budget") or 100),
            trade_review_hours=int(data.get("trade_review_hours") if data.get("trade_review_hours") is not None else 24),
            draft_type=str(data.get("draft_type") or "snake"),
            pick_timer_seconds=int(data.get("pick_timer_seconds") if data.get("pick_timer_seconds") is not None else 60),
            total_rounds=int(data.get("total_rounds") or 15),
            roster_slots=slots,
            regular_season_weeks=int(data.get("regular_season_weeks") or 14),
            playoff_teams=int(data.get("playoff_teams") or 4),
            playoff_consolation=bool(data.get("playoff_consolation", True)),
        )


@dataclass
class Player:
    id: str
    name: str
    position: str             # QB, RB, WR, TE, K, DST
    nfl_team: str             # KC, BUF, SF, etc.
    bye_week: int
    adp: float = 999.0
    projected_points: float = 0.0
    status: str = "ACT"       # ACT, Q, D, O, IR
    headshot_url: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "position": self.position,
            "nfl_team": self.nfl_team,
            "bye_week": self.bye_week,
            "adp": self.adp,
            "projected_points": self.projected_points,
            "status": self.status,
            "headshot_url": self.headshot_url,
        }


@dataclass
class FantasyTeam:
    id: str
    league_id: str
    name: str
    manager_name: str
    manager_token: str
    is_commissioner: bool = False
    waiver_priority: int = 1
    faab_balance: int = 100
    wins: int = 0
    losses: int = 0
    ties: int = 0
    points_for: float = 0.0
    points_against: float = 0.0
    playoff_seed: Optional[int] = None
    final_rank: Optional[int] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "name": self.name,
            "manager_name": self.manager_name,
            "manager_token": self.manager_token,
            "is_commissioner": self.is_commissioner,
            "waiver_priority": self.waiver_priority,
            "faab_balance": self.faab_balance,
            "wins": self.wins,
            "losses": self.losses,
            "ties": self.ties,
            "points_for": self.points_for,
            "points_against": self.points_against,
            "playoff_seed": self.playoff_seed,
            "final_rank": self.final_rank,
            "created_at": self.created_at,
        }


@dataclass
class League:
    id: str
    name: str
    season: int = 2026
    invite_token: str = ""
    commissioner_token: str = ""
    status: str = LeagueStatus.PRE_DRAFT.value
    settings: LeagueSettings = field(default_factory=LeagueSettings)
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    teams: List[FantasyTeam] = field(default_factory=list)
    draft_order: List[str] = field(default_factory=list)
    current_overall_pick: int = 1
    current_pick_deadline: Optional[str] = None
    draft_paused_seconds: Optional[int] = None
    current_week: int = 1
    champion_team_id: Optional[str] = None
    second_place_team_id: Optional[str] = None
    third_place_team_id: Optional[str] = None
    sacko_team_id: Optional[str] = None
    champion_team: Optional[FantasyTeam] = None
    second_place_team: Optional[FantasyTeam] = None
    third_place_team: Optional[FantasyTeam] = None
    sacko_team: Optional[FantasyTeam] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "season": self.season,
            "invite_token": self.invite_token,
            "commissioner_token": self.commissioner_token,
            "status": self.status,
            "settings": self.settings.to_dict(),
            "created_at": self.created_at,
            "teams": [t.to_dict() for t in self.teams],
            "draft_order": list(self.draft_order),
            "current_overall_pick": self.current_overall_pick,
            "current_pick_deadline": self.current_pick_deadline,
            "draft_paused_seconds": self.draft_paused_seconds,
            "current_week": self.current_week,
            "champion_team_id": self.champion_team_id,
            "second_place_team_id": self.second_place_team_id,
            "third_place_team_id": self.third_place_team_id,
            "sacko_team_id": self.sacko_team_id,
            "champion_team": self.champion_team.to_dict() if self.champion_team else None,
            "second_place_team": self.second_place_team.to_dict() if self.second_place_team else None,
            "third_place_team": self.third_place_team.to_dict() if self.third_place_team else None,
            "sacko_team": self.sacko_team.to_dict() if self.sacko_team else None,
        }


@dataclass
class DraftPick:
    id: str
    league_id: str
    round: int
    pick_number: int        # pick within round (1..N)
    overall_pick: int       # 1..total_picks
    team_id: str
    player_id: str
    selected_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    is_auto_pick: bool = False
    player: Optional[Player] = None
    team_name: Optional[str] = None
    manager_name: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "round": self.round,
            "pick_number": self.pick_number,
            "overall_pick": self.overall_pick,
            "team_id": self.team_id,
            "player_id": self.player_id,
            "selected_at": self.selected_at,
            "is_auto_pick": bool(self.is_auto_pick),
            "player": self.player.to_dict() if self.player else None,
            "team_name": self.team_name,
            "manager_name": self.manager_name,
        }


@dataclass
class RosterPlayer:
    id: str
    team_id: str
    player_id: str
    slot: str               # "QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DST", "BENCH"
    acquired_type: str = "draft"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    player: Optional[Player] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "team_id": self.team_id,
            "player_id": self.player_id,
            "slot": self.slot,
            "acquired_type": self.acquired_type,
            "created_at": self.created_at,
            "player": self.player.to_dict() if self.player else None,
        }


@dataclass
class DraftQueueItem:
    id: str
    team_id: str
    player_id: str
    priority: int = 1
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    player: Optional[Player] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "team_id": self.team_id,
            "player_id": self.player_id,
            "priority": self.priority,
            "created_at": self.created_at,
            "player": self.player.to_dict() if self.player else None,
        }


@dataclass
class AuditLogEntry:
    id: str
    league_id: str
    actor_name: str
    action: str
    description: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "actor_name": self.actor_name,
            "action": self.action,
            "description": self.description,
            "created_at": self.created_at,
        }


@dataclass
class Matchup:
    id: str
    league_id: str
    week: int
    home_team_id: str
    away_team_id: str
    home_score: float = 0.0
    away_score: float = 0.0
    home_projected: float = 0.0
    away_projected: float = 0.0
    is_final: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    home_team: Optional[FantasyTeam] = None
    away_team: Optional[FantasyTeam] = None
    matchup_type: str = "regular"
    bracket_slot: Optional[str] = None
    playoff_round: int = 0
    home_seed: Optional[int] = None
    away_seed: Optional[int] = None
    winner_id: Optional[str] = None
    loser_id: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "week": self.week,
            "home_team_id": self.home_team_id,
            "away_team_id": self.away_team_id,
            "home_score": self.home_score,
            "away_score": self.away_score,
            "home_projected": self.home_projected,
            "away_projected": self.away_projected,
            "is_final": bool(self.is_final),
            "created_at": self.created_at,
            "home_team": self.home_team.to_dict() if self.home_team else None,
            "away_team": self.away_team.to_dict() if self.away_team else None,
            "matchup_type": self.matchup_type,
            "bracket_slot": self.bracket_slot,
            "playoff_round": self.playoff_round,
            "home_seed": self.home_seed,
            "away_seed": self.away_seed,
            "winner_id": self.winner_id,
            "loser_id": self.loser_id,
        }


@dataclass
class LineupSlot:
    id: str
    league_id: str
    team_id: str
    week: int
    player_id: str
    slot: str  # "QB", "RB1", "RB2", "WR1", "WR2", "TE", "FLEX", "K", "DST", "BENCH"
    is_starter: bool = True
    is_locked: bool = False
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    player: Optional[Player] = None
    points: float = 0.0
    projected_points: float = 0.0
    stats_summary: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "team_id": self.team_id,
            "week": self.week,
            "player_id": self.player_id,
            "slot": self.slot,
            "is_starter": bool(self.is_starter),
            "is_locked": bool(self.is_locked),
            "created_at": self.created_at,
            "player": self.player.to_dict() if self.player else None,
            "points": self.points,
            "projected_points": self.projected_points,
            "stats_summary": self.stats_summary,
        }


@dataclass
class PlayerGameStats:
    id: str
    player_id: str
    season: int
    week: int
    pass_yd: int = 0
    pass_td: int = 0
    pass_int: int = 0
    rush_yd: int = 0
    rush_td: int = 0
    rec: int = 0
    rec_yd: int = 0
    rec_td: int = 0
    fumble_lost: int = 0
    two_pt: int = 0
    fg_made: int = 0
    pat_made: int = 0
    dst_sack: int = 0
    dst_int: int = 0
    dst_fumble_rec: int = 0
    dst_safety: int = 0
    dst_td: int = 0
    dst_points_allowed: int = 0
    raw_stats_json: str = "{}"
    updated_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "player_id": self.player_id,
            "season": self.season,
            "week": self.week,
            "pass_yd": self.pass_yd,
            "pass_td": self.pass_td,
            "pass_int": self.pass_int,
            "rush_yd": self.rush_yd,
            "rush_td": self.rush_td,
            "rec": self.rec,
            "rec_yd": self.rec_yd,
            "rec_td": self.rec_td,
            "fumble_lost": self.fumble_lost,
            "two_pt": self.two_pt,
            "fg_made": self.fg_made,
            "pat_made": self.pat_made,
            "dst_sack": self.dst_sack,
            "dst_int": self.dst_int,
            "dst_fumble_rec": self.dst_fumble_rec,
            "dst_safety": self.dst_safety,
            "dst_td": self.dst_td,
            "dst_points_allowed": self.dst_points_allowed,
            "updated_at": self.updated_at,
        }


@dataclass
class PlayerWaiverStatus:
    id: str
    league_id: str
    player_id: str
    waiver_until: str
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "player_id": self.player_id,
            "waiver_until": self.waiver_until,
            "created_at": self.created_at,
        }


@dataclass
class WaiverClaim:
    id: str
    league_id: str
    team_id: str
    add_player_id: str
    drop_player_id: Optional[str] = None
    bid_amount: int = 0
    priority: int = 1
    status: str = "pending"  # pending, successful, failed, cancelled
    fail_reason: Optional[str] = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    processed_at: Optional[str] = None
    add_player: Optional[Player] = None
    drop_player: Optional[Player] = None
    team: Optional[FantasyTeam] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "team_id": self.team_id,
            "add_player_id": self.add_player_id,
            "drop_player_id": self.drop_player_id,
            "bid_amount": self.bid_amount,
            "priority": self.priority,
            "status": self.status,
            "fail_reason": self.fail_reason,
            "created_at": self.created_at,
            "processed_at": self.processed_at,
            "add_player": self.add_player.to_dict() if self.add_player else None,
            "drop_player": self.drop_player.to_dict() if self.drop_player else None,
            "team": self.team.to_dict() if self.team else None,
        }


@dataclass
class TradeItem:
    id: str
    trade_id: str
    from_team_id: str
    to_team_id: str
    player_id: str
    player: Optional[Player] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "trade_id": self.trade_id,
            "from_team_id": self.from_team_id,
            "to_team_id": self.to_team_id,
            "player_id": self.player_id,
            "player": self.player.to_dict() if self.player else None,
        }


@dataclass
class Trade:
    id: str
    league_id: str
    proposer_team_id: str
    recipient_team_id: str
    status: str = "proposed"  # proposed, accepted, rejected, cancelled, vetoed, processed
    note: str = ""
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    expires_at: Optional[str] = None
    processed_at: Optional[str] = None
    proposer_team: Optional[FantasyTeam] = None
    recipient_team: Optional[FantasyTeam] = None
    proposer_sends: List[Player] = field(default_factory=list)
    recipient_sends: List[Player] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "league_id": self.league_id,
            "proposer_team_id": self.proposer_team_id,
            "recipient_team_id": self.recipient_team_id,
            "status": self.status,
            "note": self.note,
            "created_at": self.created_at,
            "expires_at": self.expires_at,
            "processed_at": self.processed_at,
            "proposer_team": self.proposer_team.to_dict() if self.proposer_team else None,
            "recipient_team": self.recipient_team.to_dict() if self.recipient_team else None,
            "proposer_sends": [p.to_dict() for p in self.proposer_sends],
            "recipient_sends": [p.to_dict() for p in self.recipient_sends],
        }


@dataclass
class LeagueTransaction:
    id: str
    league_id: str
    team_id: Optional[str]
    type: str  # free_agent_add, waiver_claim, drop, trade, commissioner
    description: str
    details_json: str = "{}"
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    team: Optional[FantasyTeam] = None

    def to_dict(self) -> Dict[str, Any]:
        import json
        details = {}
        try:
            details = json.loads(self.details_json) if self.details_json else {}
        except Exception:
            pass
        return {
            "id": self.id,
            "league_id": self.league_id,
            "team_id": self.team_id,
            "type": self.type,
            "description": self.description,
            "details": details,
            "created_at": self.created_at,
            "team": self.team.to_dict() if self.team else None,
        }
