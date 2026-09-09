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
    roster_slots: Dict[str, int] = field(default_factory=lambda: dict(DEFAULT_ROSTER_SLOTS))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scoring_format": self.scoring_format,
            "max_teams": self.max_teams,
            "waiver_type": self.waiver_type,
            "faab_budget": self.faab_budget,
            "trade_review_hours": self.trade_review_hours,
            "roster_slots": dict(self.roster_slots),
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
            trade_review_hours=int(data.get("trade_review_hours") or 24),
            roster_slots=slots,
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
