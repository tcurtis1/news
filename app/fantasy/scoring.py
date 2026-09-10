"""Fantasy Football scoring calculations, stat line formatting, and win probability engine."""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

from app.fantasy.models import SCORING_PRESETS, PlayerGameStats, ScoringFormat


def get_scoring_rules(scoring_format: str = "half_ppr", custom_rules: Optional[Dict[str, float]] = None) -> Dict[str, float]:
    """Retrieve combined scoring rule map for a scoring preset or custom overrides."""
    base = dict(SCORING_PRESETS.get(scoring_format, SCORING_PRESETS[ScoringFormat.HALF_PPR.value]))
    if custom_rules:
        base.update(custom_rules)
    return base


def calculate_dst_points_allowed_score(points_allowed: int, rules: Dict[str, float]) -> float:
    """Calculate defense points allowed bracket."""
    if points_allowed <= 0:
        return rules.get("dst_points_0", 10.0)
    elif 1 <= points_allowed <= 6:
        return rules.get("dst_points_1_6", 7.0)
    elif 7 <= points_allowed <= 13:
        return rules.get("dst_points_7_13", 4.0)
    elif 14 <= points_allowed <= 20:
        return rules.get("dst_points_14_20", 1.0)
    elif 21 <= points_allowed <= 27:
        return rules.get("dst_points_21_27", 0.0)
    elif 28 <= points_allowed <= 34:
        return rules.get("dst_points_28_34", -1.0)
    else:
        return rules.get("dst_points_35_plus", -4.0)


def calculate_player_points(
    stats: Optional[PlayerGameStats],
    scoring_format: str = "half_ppr",
    custom_rules: Optional[Dict[str, float]] = None,
    position: Optional[str] = None,
) -> float:
    """Calculate total fantasy points for a player's weekly game stats."""
    if not stats:
        return 0.0

    rules = get_scoring_rules(scoring_format, custom_rules)
    pos = (position or "").upper()

    pts = 0.0

    # Passing
    pts += stats.pass_yd * rules.get("pass_yd", 0.04)
    pts += stats.pass_td * rules.get("pass_td", 4.0)
    pts += stats.pass_int * rules.get("pass_int", -2.0)

    # Rushing
    pts += stats.rush_yd * rules.get("rush_yd", 0.1)
    pts += stats.rush_td * rules.get("rush_td", 6.0)

    # Receiving
    pts += stats.rec * rules.get("rec", 0.5)
    pts += stats.rec_yd * rules.get("rec_yd", 0.1)
    pts += stats.rec_td * rules.get("rec_td", 6.0)

    # Turnovers & Misc
    pts += stats.fumble_lost * rules.get("fumble_lost", -2.0)
    pts += stats.two_pt * rules.get("two_pt", 2.0)

    # Kicking
    pts += stats.fg_made * rules.get("fg_0_39", 3.0)
    pts += stats.pat_made * rules.get("pat_made", 1.0)

    # Defense
    is_defense = pos == "DST" or (
        stats.dst_sack > 0
        or stats.dst_int > 0
        or stats.dst_fumble_rec > 0
        or stats.dst_safety > 0
        or stats.dst_td > 0
        or stats.dst_points_allowed > 0
    )

    if is_defense:
        pts += stats.dst_sack * rules.get("dst_sack", 1.0)
        pts += stats.dst_int * rules.get("dst_int", 2.0)
        pts += stats.dst_fumble_rec * rules.get("dst_fumble_rec", 2.0)
        pts += stats.dst_safety * rules.get("dst_safety", 2.0)
        pts += stats.dst_td * rules.get("dst_td", 6.0)
        pts += calculate_dst_points_allowed_score(stats.dst_points_allowed, rules)

    return round(pts, 2)


def format_player_stats_summary(stats: Optional[PlayerGameStats], position: str) -> str:
    """Return a concise summary of a player's boxscore line."""
    if not stats:
        return "—"

    pos = (position or "").upper()
    parts = []

    if pos == "QB":
        if stats.pass_yd or stats.pass_td or stats.pass_int:
            parts.append(f"{stats.pass_yd} YDS")
            if stats.pass_td:
                parts.append(f"{stats.pass_td} TD")
            if stats.pass_int:
                parts.append(f"{stats.pass_int} INT")
        if stats.rush_yd or stats.rush_td:
            parts.append(f"{stats.rush_yd} RUSH")
            if stats.rush_td:
                parts.append(f"{stats.rush_td} R-TD")
    elif pos == "RB":
        if stats.rush_yd or stats.rush_td:
            parts.append(f"{stats.rush_yd} RUSH")
            if stats.rush_td:
                parts.append(f"{stats.rush_td} TD")
        if stats.rec or stats.rec_yd:
            parts.append(f"{stats.rec} REC")
            parts.append(f"{stats.rec_yd} REC YDS")
            if stats.rec_td:
                parts.append(f"{stats.rec_td} REC TD")
    elif pos in ("WR", "TE"):
        if stats.rec or stats.rec_yd:
            parts.append(f"{stats.rec} REC")
            parts.append(f"{stats.rec_yd} YDS")
            if stats.rec_td:
                parts.append(f"{stats.rec_td} TD")
        if stats.rush_yd or stats.rush_td:
            parts.append(f"{stats.rush_yd} RUSH")
            if stats.rush_td:
                parts.append(f"{stats.rush_td} TD")
    elif pos == "K":
        parts.append(f"{stats.fg_made} FG")
        parts.append(f"{stats.pat_made} XP")
    elif pos == "DST":
        if stats.dst_sack:
            parts.append(f"{stats.dst_sack} SCK")
        if stats.dst_int:
            parts.append(f"{stats.dst_int} INT")
        if stats.dst_fumble_rec:
            parts.append(f"{stats.dst_fumble_rec} FR")
        if stats.dst_safety:
            parts.append(f"{stats.dst_safety} SFTY")
        if stats.dst_td:
            parts.append(f"{stats.dst_td} TD")
        parts.append(f"{stats.dst_points_allowed} PA")
    else:
        if stats.pass_yd:
            parts.append(f"{stats.pass_yd} PASS")
        if stats.rush_yd:
            parts.append(f"{stats.rush_yd} RUSH")
        if stats.rec_yd:
            parts.append(f"{stats.rec_yd} REC")

    return ", ".join(parts) if parts else "0 YDS"


def calculate_win_probability(
    home_projected_total: float,
    away_projected_total: float,
    is_final: bool = False,
    home_actual: float = 0.0,
    away_actual: float = 0.0,
) -> Tuple[int, int]:
    """Calculate win probabilities (home_pct, away_pct) between 0 and 100."""
    if is_final:
        if home_actual > away_actual:
            return 100, 0
        elif away_actual > home_actual:
            return 0, 100
        else:
            return 50, 50

    diff = home_projected_total - away_projected_total
    k = 0.09
    p_home = 1.0 / (1.0 + math.exp(-k * diff))
    home_pct = int(round(p_home * 100))
    home_pct = max(1, min(99, home_pct))
    away_pct = 100 - home_pct
    return home_pct, away_pct
