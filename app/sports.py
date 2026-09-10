import asyncio
import logging
import time
from datetime import datetime, timezone, date, timedelta
from typing import Dict, Any, Optional, List, Tuple
from enum import Enum
from pydantic import BaseModel, Field
import httpx

logger = logging.getLogger(__name__)

class EventState(str, Enum):
    SCHEDULED = "scheduled"
    PREGAME = "pregame"
    IN_PROGRESS = "in_progress"
    HALFTIME = "halftime"
    FINAL = "final"
    POSTPONED = "postponed"
    SUSPENDED = "suspended"
    CANCELLED = "cancelled"
    UNKNOWN = "unknown"

class Team(BaseModel):
    id: str
    name: str
    abbreviation: str
    score: Optional[int] = None
    is_home: bool
    winner: Optional[bool] = None
    logo: Optional[str] = None
    rank: Optional[int] = None

class Event(BaseModel):
    id: str
    name: str
    short_name: str
    start_time: datetime
    state: EventState
    clock: Optional[str] = None
    period: int = 0
    home_team: Team
    away_team: Team
    tv_broadcasters: List[str] = Field(default_factory=list)
    league: str = ""
    status_detail: str = ""
    venue: Optional[str] = None
    situation: Optional[str] = None
    context_line: Optional[str] = None
    scoring_summary: List[Dict[str, str]] = Field(default_factory=list)
    team_stats: List[Dict[str, str]] = Field(default_factory=list)
    leaders: List[Dict[str, str]] = Field(default_factory=list)
    outs: Optional[int] = None
    balls: Optional[int] = None
    strikes: Optional[int] = None
    on_first: bool = False
    on_second: bool = False
    on_third: bool = False
    down_distance: Optional[str] = None
    possession_text: Optional[str] = None
    is_red_zone: bool = False
    batter_name: Optional[str] = None
    pitcher_name: Optional[str] = None
    last_play: Optional[str] = None

class SportsPayload(BaseModel):
    updated_at: datetime
    freshness: str  # "fresh", "stale", "fallback"
    provider_label: str
    error: Optional[str] = None
    data: Any


def compact_score_line(
    away_abbr: str,
    home_abbr: str,
    *,
    away_score: str = "—",
    home_score: str = "—",
    status: str = "",
    started: bool = False,
    away_rank: Optional[int] = None,
    home_rank: Optional[int] = None,
) -> str:
    """One-line score for SMS / Web Share: ``#5 OSU 24 @ #12 PSU 14, Final``."""
    away = (away_abbr or "AWAY").strip() or "AWAY"
    home = (home_abbr or "HOME").strip() or "HOME"
    if away_rank and 1 <= away_rank <= 25:
        away = f"#{away_rank} {away}"
    if home_rank and 1 <= home_rank <= 25:
        home = f"#{home_rank} {home}"
    status = " ".join((status or "").split())
    blank = {"", "—", "-"}
    scored = started and away_score not in blank and home_score not in blank
    if scored:
        line = f"{away} {away_score} @ {home} {home_score}"
    else:
        line = f"{away} @ {home}"
    if status:
        line = f"{line}, {status}"
    return line

LEAGUES = {
    "nfl": {"sport": "football", "league": "nfl", "name": "NFL", "short_name": "NFL", "path": "nfl"},
    "nba": {"sport": "basketball", "league": "nba", "name": "NBA", "short_name": "NBA", "path": "nba"},
    "mlb": {"sport": "baseball", "league": "mlb", "name": "Major League Baseball", "short_name": "MLB", "path": "mlb"},
    "nhl": {"sport": "hockey", "league": "nhl", "name": "NHL", "short_name": "NHL", "path": "nhl"},
    "wnba": {"sport": "basketball", "league": "wnba", "name": "WNBA", "short_name": "WNBA", "path": "wnba"},
    "epl": {"sport": "soccer", "league": "eng.1", "name": "English Premier League", "short_name": "EPL", "path": "soccer"},
    "mls": {"sport": "soccer", "league": "usa.1", "name": "Major League Soccer", "short_name": "MLS", "path": "soccer"},
    "cfb": {"sport": "football", "league": "college-football", "name": "College Football", "short_name": "CFB", "path": "college-football"},
    "mcbb": {"sport": "basketball", "league": "mens-college-basketball", "name": "Men's College Basketball", "short_name": "CBB (M)", "path": "mens-college-basketball"},
    "wcbb": {"sport": "basketball", "league": "womens-college-basketball", "name": "Women's College Basketball", "short_name": "CBB (W)", "path": "womens-college-basketball"},
}

CACHE_TTL = 30  # seconds
HEADLINES_TTL = 30 * 60
HEADLINES_LIMIT = 8
LEAGUE_NEWS_QUERY = {
    "nfl": "NFL",
    "nba": "NBA",
    "mlb": "MLB",
    "nhl": "NHL",
    "wnba": "WNBA",
    "epl": "Premier League",
    "mls": "MLS soccer",
    "cfb": "college football",
    "mcbb": "college basketball",
    "wcbb": "women's college basketball",
}
LIVE_STATES = {EventState.IN_PROGRESS, EventState.HALFTIME}
FINAL_STATES = {EventState.FINAL}
UPCOMING_STATES = {EventState.SCHEDULED, EventState.PREGAME}
HOME_FINAL_HOURS = 12
HOME_UPCOMING_HOURS = 48
HOME_FINAL_CAP = 8
HOME_UPCOMING_CAP = 12
RANKINGS_TTL = 15 * 60
RANKINGS_LEAGUES = frozenset({"cfb", "mcbb", "wcbb"})
RANKINGS_POLL_ORDER = ("ap", "cfp", "usa")
RANKINGS_SKIP_TYPES = frozenset({"fcs", "afca"})
STANDINGS_TTL = 15 * 60
STANDINGS_LEAGUES = frozenset({"mlb", "nfl", "nba", "nhl", "wnba", "mls", "epl"})

class CachedData(BaseModel):
    timestamp: float
    data: Any

_app_cache: Dict[str, CachedData] = {}
_headlines_cache: Dict[str, CachedData] = {}
_cache_locks: Dict[str, asyncio.Lock] = {}

def get_cache_lock(key: str) -> asyncio.Lock:
    if key not in _cache_locks:
        _cache_locks[key] = asyncio.Lock()
    return _cache_locks[key]

class ProviderAdapter:
    async def fetch(self, url: str, params: Optional[Dict[str, Any]] = None, timeout: float = 5.0) -> Any:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "YoyoSup-News/1.0", "Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()

_provider = ProviderAdapter()

def set_provider(provider: ProviderAdapter):
    global _provider
    _provider = provider

def clear_cache():
    _app_cache.clear()
    _headlines_cache.clear()
    _cache_locks.clear()


def league_news_query(league: str) -> str:
    return LEAGUE_NEWS_QUERY.get(league, "")


def has_college_rankings(league: str) -> bool:
    return (league or "") in RANKINGS_LEAGUES


def has_standings(league: str) -> bool:
    return (league or "") in STANDINGS_LEAGUES


async def get_sports_headlines(query: str, limit: int = HEADLINES_LIMIT) -> dict:
    """Cached Google News RSS for a league or team phrase. Never raises."""
    from app.search import google_news_headlines

    q = " ".join((query or "").split())[:80]
    cap = max(1, min(int(limit or HEADLINES_LIMIT), HEADLINES_LIMIT))
    if not q:
        return {"query": "", "headlines": []}
    key = f"headlines:{q.lower()}:{cap}"
    now = time.time()
    cached = _headlines_cache.get(key)
    if cached and (now - cached.timestamp < HEADLINES_TTL):
        return cached.data
    try:
        headlines = await google_news_headlines(q, limit=cap)
        payload = {"query": q, "headlines": headlines}
        _headlines_cache[key] = CachedData(timestamp=now, data=payload)
        return payload
    except Exception as exc:
        logger.warning("sports headlines failed q=%r: %s", q, exc)
        if cached:
            return cached.data
        return {"query": q, "headlines": []}

def _broadcast_names(*bags: Any) -> List[str]:
    names: List[str] = []
    seen = set()
    for bag in bags:
        items = bag if isinstance(bag, list) else []
        for item in items:
            if not isinstance(item, dict):
                continue
            media = item.get("media") if isinstance(item.get("media"), dict) else {}
            for candidate in (
                *(item.get("names") or []),
                item.get("station"),
                media.get("shortName"),
                media.get("name"),
            ):
                label = str(candidate or "").strip()
                key = label.lower()
                if label and key not in seen:
                    seen.add(key)
                    names.append(label)
    return names[:4]


def _extract_scoring(package: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    plays = package.get("scoringPlays")
    if not isinstance(plays, list) or not plays:
        plays = [p for p in (package.get("plays") or []) if isinstance(p, dict) and p.get("scoringPlay")]
    for play in plays:
        if not isinstance(play, dict):
            continue
        text = str(play.get("text") or play.get("shortText") or "").strip()
        if not text:
            continue
        period = play.get("period")
        if isinstance(period, dict):
            clock = str(period.get("displayValue") or period.get("type") or "")
        else:
            clock_obj = play.get("clock")
            if isinstance(clock_obj, dict):
                clock = str(clock_obj.get("displayValue") or "")
            else:
                clock = str(clock_obj or period or "")
        rows.append({"clock": clock, "text": text})
        if len(rows) >= 12:
            break
    return rows


def _nested_stat_map(groups: Any) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not isinstance(groups, list):
        return out
    for group in groups:
        if not isinstance(group, dict):
            continue
        nested = group.get("stats")
        if isinstance(nested, list):
            for stat in nested:
                if not isinstance(stat, dict):
                    continue
                name = str(stat.get("name") or "")
                value = str(stat.get("displayValue") or stat.get("value") or "").strip()
                if name and value:
                    out[name] = value
        else:
            name = str(group.get("name") or "")
            value = str(group.get("displayValue") or group.get("value") or "").strip()
            if name and value:
                out[name] = value
    return out


def _extract_team_stats(package: Dict[str, Any], home: Team, away: Team, home_extra: Dict[str, Any], away_extra: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    has_line = home_extra.get("hits") is not None or home_extra.get("errors") is not None
    if has_line or (home.score or 0) or (away.score or 0):
        rows.append({"label": "Runs", "away": str(away.score if away.score is not None else "—"), "home": str(home.score if home.score is not None else "—")})
    if away_extra.get("hits") is not None or home_extra.get("hits") is not None:
        rows.append({"label": "Hits", "away": str(away_extra.get("hits", "—")), "home": str(home_extra.get("hits", "—"))})
    if away_extra.get("errors") is not None or home_extra.get("errors") is not None:
        rows.append({"label": "Errors", "away": str(away_extra.get("errors", "—")), "home": str(home_extra.get("errors", "—"))})
    box_teams = ((package.get("boxscore") or {}).get("teams") or []) if isinstance(package.get("boxscore"), dict) else []
    by_home = {}
    for team in box_teams:
        if isinstance(team, dict):
            by_home[bool(team.get("homeAway") == "home")] = team
    away_map = _nested_stat_map((by_home.get(False) or {}).get("statistics"))
    home_map = _nested_stat_map((by_home.get(True) or {}).get("statistics"))
    def _season_totals(stat_map: Dict[str, str]) -> bool:
        for key in ("gamesPlayed", "teamGamesPlayed"):
            try:
                if int(float(stat_map.get(key) or "0")) > 2:
                    return True
            except (TypeError, ValueError):
                continue
        return False
    if _season_totals(away_map) or _season_totals(home_map):
        away_map, home_map = {}, {}
    seen_labels = {row["label"].lower() for row in rows}
    for name in ("homeRuns", "strikeouts", "walks", "totalYards", "firstDowns", "turnovers", "possessionTime", "totalRebounds", "assists"):
        if name not in away_map and name not in home_map:
            continue
        label = {
            "homeRuns": "Home runs", "strikeouts": "Strikeouts", "walks": "Walks",
            "totalYards": "Total yards", "firstDowns": "First downs", "turnovers": "Turnovers",
            "possessionTime": "Possession", "totalRebounds": "Rebounds", "assists": "Assists",
        }[name]
        if label.lower() in seen_labels:
            continue
        rows.append({"label": label, "away": away_map.get(name, "—"), "home": home_map.get(name, "—")})
        seen_labels.add(label.lower())
        if len(rows) >= 8:
            break
    return [row for row in rows if not (row["away"] in ("—", "") and row["home"] in ("—", ""))]


def _extract_leaders(package: Dict[str, Any]) -> List[Dict[str, str]]:
    rows: List[Dict[str, str]] = []
    for category in package.get("leaders") or []:
        if not isinstance(category, dict):
            continue
        entries = category.get("leaders") or []
        if not entries:
            continue
        athlete = entries[0].get("athlete") or {}
        name = str(athlete.get("displayName") or athlete.get("shortName") or "").strip()
        value = str(entries[0].get("displayValue") or entries[0].get("value") or "").strip()
        if name and value and value not in ("—", "-"):
            rows.append({
                "category": str(category.get("displayName") or category.get("name") or "Leader"),
                "name": name,
                "value": value,
            })
        if len(rows) >= 4:
            return rows
    players = ((package.get("boxscore") or {}).get("players") or []) if isinstance(package.get("boxscore"), dict) else []
    hitters: List[tuple] = []
    for side in players:
        if not isinstance(side, dict):
            continue
        team = (side.get("team") or {}).get("abbreviation") or ""
        for group in side.get("statistics") or []:
            if not isinstance(group, dict):
                continue
            labels = [str(x) for x in (group.get("labels") or [])]
            if "H" not in labels or "RBI" not in labels:
                continue
            hi, ri = labels.index("H"), labels.index("RBI")
            for athlete_row in group.get("athletes") or []:
                stats = athlete_row.get("stats") or []
                if len(stats) <= max(hi, ri):
                    continue
                try:
                    hits = int(str(stats[hi]).split("-")[0])
                    rbi = int(str(stats[ri]))
                    ab = int(str(stats[labels.index("AB")])) if "AB" in labels else 0
                except (TypeError, ValueError):
                    continue
                if hits > 6 or ab > 6:
                    continue
                person = athlete_row.get("athlete") or {}
                name = str(person.get("shortName") or person.get("displayName") or "").strip()
                if name and (hits or rbi):
                    hitters.append((hits, rbi, name, team, f"{hits} H, {rbi} RBI"))
    hitters.sort(reverse=True)
    for hits, rbi, name, team, value in hitters[:4]:
        rows.append({"category": f"{team} batting".strip(), "name": name, "value": value})
    return rows[:4]


def _extract_situation(package: Dict[str, Any], competition: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    sit = (
        competition.get("situation")
        if isinstance(competition.get("situation"), dict)
        else None
    ) or (
        package.get("situation")
        if isinstance(package.get("situation"), dict)
        else {}
    )
    outs = sit.get("outs")
    if outs is None:
        outs = competition.get("outs")
    try:
        outs = int(outs) if outs is not None else None
    except (TypeError, ValueError):
        outs = None

    balls, strikes = sit.get("balls"), sit.get("strikes")
    try:
        balls = int(balls) if balls is not None else None
    except (TypeError, ValueError):
        balls = None
    try:
        strikes = int(strikes) if strikes is not None else None
    except (TypeError, ValueError):
        strikes = None

    on_first = bool(sit.get("onFirst"))
    on_second = bool(sit.get("onSecond"))
    on_third = bool(sit.get("onThird"))

    down_distance = sit.get("downDistanceText")
    if down_distance:
        down_distance = str(down_distance).strip()
    possession_text = sit.get("possessionText")
    if possession_text:
        possession_text = str(possession_text).strip()
    is_red_zone = bool(sit.get("isRedZone"))

    batter_obj = sit.get("batter", {}).get("athlete") if isinstance(sit.get("batter"), dict) else None
    batter_name = (batter_obj.get("shortName") or batter_obj.get("displayName") or batter_obj.get("fullName")) if isinstance(batter_obj, dict) else None
    batter_summary = sit.get("batter", {}).get("summary") if isinstance(sit.get("batter"), dict) else None

    pitcher_obj = sit.get("pitcher", {}).get("athlete") if isinstance(sit.get("pitcher"), dict) else None
    pitcher_name = (pitcher_obj.get("shortName") or pitcher_obj.get("displayName") or pitcher_obj.get("fullName")) if isinstance(pitcher_obj, dict) else None
    pitcher_summary = sit.get("pitcher", {}).get("summary") if isinstance(sit.get("pitcher"), dict) else None

    last_play = None
    last_play_obj = sit.get("lastPlay")
    if isinstance(last_play_obj, dict) and last_play_obj.get("text"):
        last_play = str(last_play_obj.get("text")).strip()

    bits = []
    if down_distance:
        bits.append(down_distance)
        if possession_text:
            bits.append(possession_text)
    else:
        if balls is not None and strikes is not None:
            bits.append(f"{balls}-{strikes}")
        if outs is not None:
            bits.append("0 outs" if outs == 0 else ("1 out" if outs == 1 else f"{outs} outs"))
        on = []
        if on_first:
            on.append("1st")
        if on_second:
            on.append("2nd")
        if on_third:
            on.append("3rd")
        if on:
            bits.append("runners on " + ", ".join(on))

    line = ", ".join(bits)
    parsed_fields = {
        "outs": outs,
        "balls": balls,
        "strikes": strikes,
        "on_first": on_first,
        "on_second": on_second,
        "on_third": on_third,
        "down_distance": down_distance,
        "possession_text": possession_text,
        "is_red_zone": is_red_zone,
        "batter_name": f"{batter_name} ({batter_summary})" if batter_name and batter_summary else batter_name,
        "pitcher_name": f"{pitcher_name} ({pitcher_summary})" if pitcher_name and pitcher_summary else pitcher_name,
        "last_play": last_play,
    }
    return line, parsed_fields


def _overall_record(comp: Dict[str, Any]) -> str:
    for rec in comp.get("records") or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "total" or str(rec.get("name") or "").lower() == "overall":
            summary = str(rec.get("summary") or "").strip()
            if summary and summary not in ("0-0", "0-0-0"):
                return summary
    return ""


def _team_logo(league_key: str, t_data: Dict[str, Any]) -> Optional[str]:
    if not isinstance(t_data, dict):
        return None
    logo = t_data.get("logo")
    if not logo and isinstance(t_data.get("logos"), list) and t_data["logos"]:
        first = t_data["logos"][0]
        if isinstance(first, dict):
            logo = first.get("href")
    if logo:
        return str(logo).strip()
    team_id = str(t_data.get("id") or "").strip()
    if not team_id or team_id in ("0", "1", "unknown"):
        return None
    if league_key in ("cfb", "mcbb", "wcbb"):
        return f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png"
    elif league_key in ("nfl", "mlb", "nba", "nhl", "wnba"):
        return f"https://a.espncdn.com/i/teamlogos/{league_key}/500/{team_id}.png"
    elif league_key in ("mls", "epl"):
        return f"https://a.espncdn.com/i/teamlogos/soccer/500/{team_id}.png"
    return None


def _team_rank(comp: Dict[str, Any]) -> Optional[int]:
    if not isinstance(comp, dict):
        return None
    candidates = []
    cr = comp.get("curatedRank")
    if isinstance(cr, dict):
        candidates.append(cr.get("current"))
    elif cr is not None:
        candidates.append(cr)
    t = comp.get("team")
    if isinstance(t, dict):
        t_cr = t.get("curatedRank")
        if isinstance(t_cr, dict):
            candidates.append(t_cr.get("current"))
        elif t_cr is not None:
            candidates.append(t_cr)
        candidates.append(t.get("rank"))
    candidates.append(comp.get("rank"))

    for cand in candidates:
        try:
            value = int(cand)
            if 1 <= value <= 25:
                return value
        except (TypeError, ValueError):
            continue
    return None


def _context_line(ev: Dict[str, Any], package: Dict[str, Any], competition: Dict[str, Any], home: Team, away: Team, home_comp: Dict[str, Any], away_comp: Dict[str, Any]) -> str:
    bits: List[str] = []
    week = ev.get("week") or package.get("week") or (package.get("header") or {}).get("week") or {}
    number = week.get("number") if isinstance(week, dict) else None
    try:
        week_n = int(number)
    except (TypeError, ValueError):
        week_n = 0
    if week_n > 0:
        bits.append(f"Week {week_n}")
    for note in competition.get("notes") or ev.get("notes") or []:
        if not isinstance(note, dict):
            continue
        headline = str(note.get("headline") or "").strip()
        if headline and "ticket" not in headline.lower():
            bits.append(headline)
            break
    rec_bits = []
    for team, comp in ((away, away_comp), (home, home_comp)):
        rank = _team_rank(comp)
        record = _overall_record(comp)
        label = (f"#{rank} " if rank else "") + team.abbreviation
        if record:
            rec_bits.append(f"{label} {record}")
        elif rank:
            rec_bits.append(label)
    if rec_bits:
        bits.append(" at ".join(rec_bits) if len(rec_bits) == 2 else rec_bits[0])
    weather = competition.get("weather") or (package.get("gameInfo") or {}).get("weather") or {}
    display = ""
    if isinstance(weather, dict):
        display = str(weather.get("displayValue") or weather.get("condition") or "").strip()
    if display:
        bits.append(display)
    return " · ".join(bits)[:180]


def parse_espn_event(league_key: str, ev: Dict[str, Any]) -> Event:
    package = ev
    if isinstance(ev.get("header"), dict):
        ev = ev["header"]

    raw_id = ev.get("id", "unknown")
    id_ = f"{league_key}_{raw_id}"

    competitions = ev.get("competitions", [{}])
    comp0 = competitions[0] if competitions else {}

    name = ev.get("name")
    short_name = ev.get("shortName")

    start_time_str = ev.get("date") or comp0.get("date")

    if not name:
        teams = comp0.get("competitors", [])
        if len(teams) == 2:
            name = f"{teams[1].get('team', {}).get('displayName', 'Away')} at {teams[0].get('team', {}).get('displayName', 'Home')}"
            short_name = short_name or f"{teams[1].get('team', {}).get('abbreviation', 'AWY')} @ {teams[0].get('team', {}).get('abbreviation', 'HOM')}"
        else:
            name = "Unknown Event"
            short_name = short_name or name

    if not short_name:
        short_name = name

    try:
        if start_time_str:
            start_time = datetime.fromisoformat(str(start_time_str).replace("Z", "+00:00"))
            if start_time.tzinfo is None:
                start_time = start_time.replace(tzinfo=timezone.utc)
        else:
            start_time = datetime.now(timezone.utc)
    except (ValueError, TypeError):
        start_time = datetime.now(timezone.utc)

    status_dict = ev.get("status") or comp0.get("status") or {}
    status_type = status_dict.get("type", {})
    state_str = status_type.get("name", "")
    status_detail = status_type.get("shortDetail") or status_type.get("detail") or status_type.get("description") or ""
    state_enum_str = status_type.get("state", "pre")

    state = EventState.UNKNOWN
    if state_str == "STATUS_SCHEDULED":
        state = EventState.SCHEDULED
    elif state_str == "STATUS_PREGAME":
        state = EventState.PREGAME
    elif state_str in ("STATUS_IN_PROGRESS", "STATUS_IN_PROGRESS_2"):
        state = EventState.IN_PROGRESS
    elif state_str in ("STATUS_HALFTIME", "STATUS_END_PERIOD", "STATUS_DELAYED", "STATUS_RAIN_DELAY", "STATUS_INTERMISSION"):
        state = EventState.HALFTIME
    elif state_str == "STATUS_FINAL":
        state = EventState.FINAL
    elif state_str == "STATUS_POSTPONED":
        state = EventState.POSTPONED
    elif state_str in ("STATUS_CANCELED", "STATUS_CANCELLED"):
        state = EventState.CANCELLED
    elif state_str == "STATUS_SUSPENDED":
        state = EventState.SUSPENDED
    elif state_enum_str == "pre":
        state = EventState.SCHEDULED
    elif state_enum_str == "in":
        state = EventState.IN_PROGRESS
    elif state_enum_str == "post":
        state = EventState.FINAL

    clock = status_dict.get("displayClock")
    period = status_dict.get("period", 0)

    competitors = comp0.get("competitors", [])
    home_team = None
    away_team = None
    home_extra: Dict[str, Any] = {}
    away_extra: Dict[str, Any] = {}
    home_comp: Dict[str, Any] = {}
    away_comp: Dict[str, Any] = {}
    for comp in competitors:
        t_data = comp.get("team", {})
        score_str = comp.get("score")
        score = int(score_str) if score_str and str(score_str).isdigit() else None
        logo = _team_logo(league_key, t_data)
        rank = _team_rank(comp)

        team = Team(
            id=str(t_data.get("id", "0")),
            name=t_data.get("displayName", "Unknown"),
            abbreviation=t_data.get("abbreviation", "UNK"),
            score=score,
            is_home=(comp.get("homeAway") == "home"),
            winner=comp.get("winner"),
            logo=logo,
            rank=rank,
        )
        extra = {"hits": comp.get("hits"), "errors": comp.get("errors")}
        if team.is_home:
            home_team = team
            home_extra = extra
            home_comp = comp
        else:
            away_team = team
            away_extra = extra
            away_comp = comp

    if not home_team:
        home_team = Team(id="0", name="Home", abbreviation="HOM", is_home=True)
    if not away_team:
        away_team = Team(id="1", name="Away", abbreviation="AWY", is_home=False)

    pkg = package if isinstance(package, dict) else {}
    tv = _broadcast_names(pkg.get("broadcasts"), comp0.get("broadcasts"))
    venue = (
        (comp0.get("venue") or {}).get("fullName")
        or ((pkg.get("gameInfo") or {}).get("venue") or {}).get("fullName")
    )
    scoring_summary = _extract_scoring(pkg)
    team_stats = _extract_team_stats(pkg, home_team, away_team, home_extra, away_extra)
    leaders = _extract_leaders(pkg)
    situation, situation_fields = _extract_situation(pkg, comp0)
    context_line = _context_line(ev, pkg, comp0, home_team, away_team, home_comp, away_comp)

    return Event(
        id=id_,
        name=name,
        short_name=short_name,
        start_time=start_time,
        state=state,
        clock=clock,
        period=period,
        home_team=home_team,
        away_team=away_team,
        tv_broadcasters=tv,
        league=league_key,
        status_detail=str(status_detail),
        venue=venue,
        situation=situation or None,
        context_line=context_line or None,
        scoring_summary=scoring_summary,
        team_stats=team_stats,
        leaders=leaders,
        **situation_fields,
    )

async def _fetch_with_cache(key: str, fetch_func, fallback_data: Any, ttl: int = CACHE_TTL) -> SportsPayload:
    lock = get_cache_lock(key)
    now = time.time()

    async with lock:
        cached = _app_cache.get(key)
        if cached and (now - cached.timestamp < ttl):
            return SportsPayload(
                updated_at=datetime.fromtimestamp(cached.timestamp, tz=timezone.utc),
                freshness="fresh",
                provider_label="espn",
                data=cached.data
            )

        try:
            data = await fetch_func()
            _app_cache[key] = CachedData(timestamp=now, data=data)
            return SportsPayload(
                updated_at=datetime.fromtimestamp(now, tz=timezone.utc),
                freshness="fresh",
                provider_label="espn",
                data=data
            )
        except Exception as e:
            if cached:
                return SportsPayload(
                    updated_at=datetime.fromtimestamp(cached.timestamp, tz=timezone.utc),
                    freshness="stale",
                    provider_label="espn",
                    error=str(e),
                    data=cached.data
                )
            else:
                return SportsPayload(
                    updated_at=datetime.now(tz=timezone.utc),
                    freshness="fallback",
                    provider_label="fallback",
                    error=str(e),
                    data=fallback_data
                )

async def get_league_catalog() -> SportsPayload:
    async def fetcher():
        return [{"slug": slug, **meta} for slug, meta in LEAGUES.items()]
    fallback = [{"slug": slug, **meta} for slug, meta in LEAGUES.items()]
    return await _fetch_with_cache("league_catalog", fetcher, fallback)

async def get_scoreboard(league: str, target_date: Optional[date] = None) -> SportsPayload:
    if league not in LEAGUES:
        raise ValueError(f"Unsupported league: {league}")

    l_info = LEAGUES[league]
    url = f"https://cdn.espn.com/core/{l_info['path']}/scoreboard"
    params = {"xhr": "1"}
    if l_info["path"] == "soccer":
        params["league"] = l_info["league"]
    if target_date:
        params["dates"] = target_date.strftime("%Y%m%d")

    key = f"scoreboard_{league}_{target_date.strftime('%Y%m%d') if target_date else 'current'}"

    async def fetcher():
        raw_data = await _provider.fetch(url, params=params)
        raw_data = (((raw_data.get("content") or {}).get("sbData")) or raw_data)
        events = []
        seen = set()
        for ev in raw_data.get("events", []):
            parsed = parse_espn_event(league, ev)
            if parsed.id not in seen:
                seen.add(parsed.id)
                events.append(parsed)
        return events

    return await _fetch_with_cache(key, fetcher, [])


def _poll_sort_key(poll_type: str) -> int:
    try:
        return RANKINGS_POLL_ORDER.index(poll_type)
    except ValueError:
        return 99


def _poll_type(block: Dict[str, Any]) -> str:
    ptype = str(block.get("type") or "").strip().lower()
    if ptype:
        return ptype
    pid = str(block.get("id") or "").strip()
    return {"1": "ap", "2": "usa", "7": "cfp", "8": "cfp", "20": "fcs", "11": "afca", "12": "afca"}.get(pid, pid)


def _id_from_team_url(url: str) -> str:
    parts = (url or "").split("/")
    for i, part in enumerate(parts):
        if part == "id" and i + 1 < len(parts) and parts[i + 1].isdigit():
            return parts[i + 1]
    return ""


def _week_label(raw: Dict[str, Any], chosen: Dict[str, Any]) -> str:
    occ = chosen.get("occurrence") if isinstance(chosen.get("occurrence"), dict) else {}
    label = str(occ.get("displayValue") or "").strip()
    if label:
        return label
    season = raw.get("requestedSeason") if isinstance(raw.get("requestedSeason"), dict) else {}
    week = season.get("week") if isinstance(season.get("week"), dict) else {}
    label = str(week.get("displayValue") or "").strip()
    if label:
        return label
    for item in raw.get("weekFilters") or []:
        if isinstance(item, dict) and item.get("selected"):
            return str(item.get("label") or "").strip()
    return ""


def _rank_trend(current: int, previous: Any, trend_raw: Any) -> Tuple[str, str]:
    try:
        prev = int(previous)
    except (TypeError, ValueError):
        prev = 0
    if prev <= 0:
        return "NR", "new"
    if prev == current:
        return str(prev), "—"
    raw = str(trend_raw or "").strip()
    if raw in ("", "0"):
        delta = prev - current
        if delta > 0:
            raw = f"+{delta}"
        elif delta < 0:
            raw = str(delta)
        else:
            raw = "—"
    return str(prev), raw


def parse_espn_rankings(league: str, raw: Dict[str, Any], poll: Optional[str] = None) -> Dict[str, Any]:
    """AP Top 25 (and CFP/coaches when ESPN sends them). Never invent ranks."""
    news_q = league_news_query(league)
    polls: List[Dict[str, Any]] = []
    for block in raw.get("rankings") or []:
        if not isinstance(block, dict):
            continue
        ptype = _poll_type(block)
        if not ptype or ptype in RANKINGS_SKIP_TYPES or ptype.startswith("afca"):
            continue
        ranks = [row for row in (block.get("ranks") or []) if isinstance(row, dict)]
        if not ranks:
            continue
        polls.append({
            "id": str(block.get("id") or ptype),
            "type": ptype,
            "name": str(block.get("name") or block.get("shortName") or block.get("short_name") or "Top 25").strip(),
            "short_name": str(block.get("shortName") or block.get("short_name") or block.get("name") or "Top 25").strip(),
            "ranks": ranks,
            "occurrence": block.get("occurrence") if isinstance(block.get("occurrence"), dict) else {},
        })
    polls.sort(key=lambda item: _poll_sort_key(item["type"]))
    wanted = (poll or "").strip().lower()
    chosen = None
    if wanted:
        for item in polls:
            if item["type"] == wanted or item["id"] == wanted:
                chosen = item
                break
    if chosen is None and polls:
        chosen = polls[0]
    week_label = _week_label(raw, chosen or {})
    teams: List[Dict[str, Any]] = []
    if chosen:
        for row in chosen["ranks"][:25]:
            current_raw = row.get("current") if row.get("current") is not None else row.get("rank")
            try:
                current = int(current_raw)
            except (TypeError, ValueError):
                continue
            if current <= 0 or current > 25:
                continue
            team = row.get("team") if isinstance(row.get("team"), dict) else {}
            name = str(
                team.get("nickname")
                or team.get("location")
                or team.get("displayName")
                or team.get("name")
                or row.get("team_display_name")
                or ""
            ).strip()
            abbr = str(team.get("abbreviation") or row.get("team_abbreviation") or "").strip() or "TEAM"
            team_id = str(team.get("id") or "").strip() or _id_from_team_url(str(row.get("team_url") or ""))
            if not name or not team_id:
                continue
            team_dict = dict(team)
            if "id" not in team_dict and team_id:
                team_dict["id"] = team_id
            logo = _team_logo(league, team_dict) or (f"https://a.espncdn.com/i/teamlogos/ncaa/500/{team_id}.png" if league in ("cfb", "mcbb", "wcbb") and team_id else None)
            previous = row.get("previous") if row.get("previous") is not None else row.get("previous_rank")
            record = str(row.get("recordSummary") or row.get("formatted_record") or "").strip()
            previous_label, trend = _rank_trend(current, previous, row.get("trend"))
            query = " ".join(part for part in (name, news_q) if part)
            teams.append({
                "current": current,
                "previous_label": previous_label,
                "trend": trend,
                "record": record,
                "team_id": team_id,
                "name": name,
                "abbreviation": abbr,
                "logo": logo,
                "news_query": query,
            })
    return {
        "available": bool(teams),
        "league": league,
        "poll": chosen["type"] if chosen else "",
        "poll_name": chosen["name"] if chosen else "Top 25",
        "week_label": week_label,
        "polls": [{"type": item["type"], "name": item["short_name"]} for item in polls],
        "teams": teams,
    }


async def get_rankings(league: str, poll: Optional[str] = None) -> SportsPayload:
    if league not in LEAGUES:
        raise ValueError(f"Unsupported league: {league}")
    if league not in RANKINGS_LEAGUES:
        raise ValueError(f"No Top 25 poll for league: {league}")
    l_info = LEAGUES[league]
    key = f"rankings_{league}"
    sources = (
        (f"https://site.web.api.espn.com/apis/site/v2/sports/{l_info['sport']}/{l_info['path']}/rankings", None),
        (f"https://cdn.espn.com/core/{l_info['path']}/rankings", {"xhr": "1"}),
    )

    async def fetcher():
        last_error = None
        for url, params in sources:
            try:
                raw = await _provider.fetch(url, params=params, timeout=12.0)
            except Exception as exc:
                last_error = exc
                continue
            if not isinstance(raw, dict):
                continue
            content = raw.get("content") if isinstance(raw.get("content"), dict) else {}
            inner = content.get("data") if isinstance(content.get("data"), dict) else {}
            if inner.get("rankings"):
                return inner
            if raw.get("rankings"):
                return raw
        if last_error:
            raise last_error
        return {}

    payload = await _fetch_with_cache(key, fetcher, {}, ttl=RANKINGS_TTL)
    parsed = parse_espn_rankings(league, payload.data or {}, poll=poll)
    return SportsPayload(
        updated_at=payload.updated_at,
        freshness=payload.freshness,
        provider_label=payload.provider_label,
        error=payload.error,
        data=parsed,
    )


def parse_espn_standings(league: str, raw: Dict[str, Any]) -> Dict[str, Any]:
    content = raw.get("content", {}) if isinstance(raw.get("content"), dict) else raw
    st = content.get("standings", {}) if isinstance(content.get("standings"), dict) else content
    divisions = []

    def parse_entries(entries: list) -> list:
        rows = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            t = entry.get("team", {})
            logo = _team_logo(league, t)
            stats = {
                s.get("name"): s.get("displayValue")
                for s in entry.get("stats", [])
                if isinstance(s, dict) and s.get("name")
            }
            wins = str(stats.get("wins") or "0")
            losses = str(stats.get("losses") or "0")
            ties = stats.get("ties") or stats.get("OTLosses")
            pct = stats.get("winPercent") or ""
            gb = stats.get("gamesBehind") or stats.get("divisionGamesBehind") or "-"
            diff = stats.get("pointDifferential") or stats.get("differential") or ""
            streak = stats.get("streak") or ""
            l10 = stats.get("Last Ten Games") or ""
            pts = stats.get("points")
            clinch = stats.get("clincher")
            abbr = str(t.get("abbreviation") or "")
            name = str(t.get("displayName") or t.get("name") or "Unknown")
            rows.append({
                "id": str(t.get("id") or ""),
                "name": name,
                "abbreviation": abbr,
                "logo": logo,
                "wins": wins,
                "losses": losses,
                "ties": str(ties) if ties is not None else None,
                "pct": pct,
                "gb": gb,
                "diff": diff,
                "streak": streak,
                "l10": l10,
                "points": str(pts) if pts is not None else None,
                "clinch": clinch,
                "news_query": f"{abbr} {name}".strip(),
            })
        return rows

    def collect(group: dict, parent_name: str = ""):
        name = group.get("name", "")
        subgroups = group.get("groups") or group.get("children") or []
        entries = (group.get("standings") or {}).get("entries") or group.get("entries") or []
        if subgroups:
            for sub in subgroups:
                collect(sub, parent_name=name)
        elif entries:
            div_name = name or parent_name or "Standings"
            rows = parse_entries(entries)
            if rows:
                divisions.append({
                    "name": div_name,
                    "teams": rows,
                })

    top_groups = st.get("groups") or st.get("children") or []
    if top_groups:
        for g in top_groups:
            if isinstance(g, dict):
                collect(g)
    else:
        entries = (st.get("standings") or {}).get("entries") or st.get("entries") or []
        if entries:
            rows = parse_entries(entries)
            if rows:
                divisions.append({"name": "Standings", "teams": rows})

    return {
        "league": league,
        "available": bool(divisions),
        "divisions": divisions,
    }


async def get_standings(league: str) -> SportsPayload:
    if league not in LEAGUES:
        raise ValueError(f"Unsupported league: {league}")
    if league not in STANDINGS_LEAGUES:
        raise ValueError(f"No standings available for league: {league}")
    l_info = LEAGUES[league]
    key = f"standings_{league}"

    if league in ("mls", "epl"):
        sub_league = "usa.1" if league == "mls" else "eng.1"
        sources = [
            (f"https://cdn.espn.com/core/soccer/standings", {"xhr": "1", "league": sub_league}),
            (f"https://site.web.api.espn.com/apis/v2/sports/soccer/{sub_league}/standings", None),
        ]
    else:
        sources = [
            (f"https://cdn.espn.com/core/{l_info['path']}/standings", {"xhr": "1"}),
            (f"https://site.web.api.espn.com/apis/v2/sports/{l_info['sport']}/{l_info['path']}/standings", None),
        ]

    async def fetcher():
        last_error = None
        for url, params in sources:
            try:
                raw = await _provider.fetch(url, params=params, timeout=12.0)
            except Exception as exc:
                last_error = exc
                continue
            if not isinstance(raw, dict):
                continue
            parsed = parse_espn_standings(league, raw)
            if parsed.get("divisions"):
                return raw
        if last_error:
            raise last_error
        return {}

    payload = await _fetch_with_cache(key, fetcher, {}, ttl=STANDINGS_TTL)
    parsed = parse_espn_standings(league, payload.data or {})
    return SportsPayload(
        updated_at=payload.updated_at,
        freshness=payload.freshness,
        provider_label=payload.provider_label,
        error=payload.error,
        data=parsed,
    )


def overlay_scoreboard_event(detail: Event, board: Event) -> Event:
    """Prefer the live scoreboard clock/score when the game package lags."""
    detail.state = board.state
    if board.status_detail:
        detail.status_detail = board.status_detail
    if board.clock:
        detail.clock = board.clock
    if board.period:
        detail.period = board.period
    if board.venue:
        detail.venue = board.venue
    if board.home_team.score is not None:
        detail.home_team.score = board.home_team.score
    if board.away_team.score is not None:
        detail.away_team.score = board.away_team.score
    if board.home_team.winner is not None:
        detail.home_team.winner = board.home_team.winner
    if board.away_team.winner is not None:
        detail.away_team.winner = board.away_team.winner
    if board.context_line:
        detail.context_line = board.context_line
    if board.situation:
        detail.situation = board.situation
    if board.outs is not None:
        detail.outs = board.outs
    if board.balls is not None:
        detail.balls = board.balls
    if board.strikes is not None:
        detail.strikes = board.strikes
    detail.on_first = board.on_first
    detail.on_second = board.on_second
    detail.on_third = board.on_third
    if board.down_distance:
        detail.down_distance = board.down_distance
    if board.possession_text:
        detail.possession_text = board.possession_text
    detail.is_red_zone = board.is_red_zone
    if board.batter_name:
        detail.batter_name = board.batter_name
    if board.pitcher_name:
        detail.pitcher_name = board.pitcher_name
    if board.last_play:
        detail.last_play = board.last_play
    if board.home_team.logo and not detail.home_team.logo:
        detail.home_team.logo = board.home_team.logo
    if board.away_team.logo and not detail.away_team.logo:
        detail.away_team.logo = board.away_team.logo
    if board.home_team.rank and not detail.home_team.rank:
        detail.home_team.rank = board.home_team.rank
    if board.away_team.rank and not detail.away_team.rank:
        detail.away_team.rank = board.away_team.rank
    return detail


async def get_game_detail(game_id: str) -> SportsPayload:
    parts = game_id.split("_", 1)
    if len(parts) != 2 or parts[0] not in LEAGUES:
        raise ValueError(f"Invalid game ID format: {game_id}")

    league, raw_id = parts
    l_info = LEAGUES[league]
    url = f"https://cdn.espn.com/core/{l_info['path']}/game"
    params = {"xhr": "1", "gameId": raw_id}
    if l_info["path"] == "soccer":
        params["league"] = l_info["league"]

    key = f"game_{game_id}"

    async def fetcher():
        raw_data = await _provider.fetch(url, params=params)
        package = raw_data.get("gamepackageJSON") or raw_data.get("__gamepackage__") or raw_data
        return parse_espn_event(league, package)

    payload = await _fetch_with_cache(key, fetcher, None)
    detail = payload.data
    if not isinstance(detail, Event):
        return payload
    try:
        board = await get_scoreboard(league, None)
    except Exception:
        return payload
    for event in board.data or []:
        if event.id == game_id:
            overlay_scoreboard_event(detail, event)
            break
    return payload


async def get_sports_home_summary(target_date: Optional[date] = None) -> SportsPayload:
    now = datetime.now(tz=timezone.utc)
    tasks = [get_scoreboard(lg, target_date) for lg in LEAGUES.keys()]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    summary_data = {}
    stale = False
    fallback = False

    for lg, res in zip(LEAGUES.keys(), results):
        if isinstance(res, Exception):
            summary_data[lg] = []
            stale = True
        else:
            summary_data[lg] = list(res.data or [])
            if res.freshness == "stale":
                stale = True
            elif res.freshness == "fallback":
                fallback = True

    freshness = "fresh"
    if stale:
        freshness = "stale"
    elif fallback:
        freshness = "fallback"

    return SportsPayload(
        updated_at=now,
        freshness=freshness,
        provider_label="espn",
        data=summary_data
    )


def group_events(
    events: List[Event],
    now: Optional[datetime] = None,
    *,
    window: bool = False,
) -> Dict[str, List[Event]]:
    """Split a slate into Live / Final / Upcoming.

    ``window=True`` is the /sports home mix: every live game, recent finals,
    and games in the next two days — not ESPN's first five per league.
    """
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    live: List[Event] = []
    final: List[Event] = []
    upcoming: List[Event] = []
    final_after = now - timedelta(hours=HOME_FINAL_HOURS)
    upcoming_until = now + timedelta(hours=HOME_UPCOMING_HOURS)
    for event in events:
        start = event.start_time
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if event.state in LIVE_STATES:
            live.append(event)
        elif event.state in FINAL_STATES:
            if not window or start >= final_after:
                final.append(event)
        elif event.state in UPCOMING_STATES:
            if not window or start <= upcoming_until:
                upcoming.append(event)
        elif not window:
            upcoming.append(event)
    live.sort(key=lambda event: event.start_time, reverse=True)
    final.sort(key=lambda event: event.start_time, reverse=True)
    upcoming.sort(key=lambda event: event.start_time)
    if window:
        final = final[:HOME_FINAL_CAP]
        upcoming = upcoming[:HOME_UPCOMING_CAP]
    return {"live": live, "final": final, "upcoming": upcoming}
