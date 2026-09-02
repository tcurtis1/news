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
    scoring_summary: List[Dict[str, str]] = Field(default_factory=list)
    team_stats: List[Dict[str, str]] = Field(default_factory=list)
    leaders: List[Dict[str, str]] = Field(default_factory=list)

class SportsPayload(BaseModel):
    updated_at: datetime
    freshness: str  # "fresh", "stale", "fallback"
    provider_label: str
    error: Optional[str] = None
    data: Any

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
LIVE_STATES = {EventState.IN_PROGRESS, EventState.HALFTIME}
FINAL_STATES = {EventState.FINAL}
UPCOMING_STATES = {EventState.SCHEDULED, EventState.PREGAME}
HOME_FINAL_HOURS = 12
HOME_UPCOMING_HOURS = 48
HOME_FINAL_CAP = 8
HOME_UPCOMING_CAP = 12

class CachedData(BaseModel):
    timestamp: float
    data: Any

_app_cache: Dict[str, CachedData] = {}
_cache_locks: Dict[str, asyncio.Lock] = {}

def get_cache_lock(key: str) -> asyncio.Lock:
    if key not in _cache_locks:
        _cache_locks[key] = asyncio.Lock()
    return _cache_locks[key]

class ProviderAdapter:
    async def fetch(self, url: str, params: Optional[Dict[str, Any]] = None) -> Any:
        async with httpx.AsyncClient(timeout=5.0) as client:
            resp = await client.get(url, params=params, headers={"User-Agent": "YoyoSup-News/1.0", "Accept": "application/json"})
            resp.raise_for_status()
            return resp.json()

_provider = ProviderAdapter()

def set_provider(provider: ProviderAdapter):
    global _provider
    _provider = provider

def clear_cache():
    _app_cache.clear()
    _cache_locks.clear()

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
    for comp in competitors:
        t_data = comp.get("team", {})
        score_str = comp.get("score")
        score = int(score_str) if score_str and str(score_str).isdigit() else None

        team = Team(
            id=str(t_data.get("id", "0")),
            name=t_data.get("displayName", "Unknown"),
            abbreviation=t_data.get("abbreviation", "UNK"),
            score=score,
            is_home=(comp.get("homeAway") == "home"),
            winner=comp.get("winner")
        )
        if team.is_home:
            home_team = team
        else:
            away_team = team

    if not home_team:
        home_team = Team(id="0", name="Home", abbreviation="HOM", is_home=True)
    if not away_team:
        away_team = Team(id="1", name="Away", abbreviation="AWY", is_home=False)

    tv = []
    broadcasters = comp0.get("broadcasts", [])
    for b in broadcasters:
        names = b.get("names", [])
        if names:
            tv.extend(names)
        elif "media" in b and "shortName" in b["media"]:
            tv.append(b["media"]["shortName"])

    venue = (comp0.get("venue") or {}).get("fullName")

    scoring_summary = []
    for play in package.get("scoringPlays", []) if isinstance(package, dict) else []:
        text = play.get("text") or play.get("shortText")
        if text:
            scoring_summary.append({"clock": str((play.get("clock") or {}).get("displayValue") or play.get("period", "")), "text": str(text)})

    team_stats = []
    box_teams = ((package.get("boxscore") or {}).get("teams") or []) if isinstance(package, dict) else []
    if len(box_teams) >= 2:
        by_home = {bool(t.get("homeAway") == "home"): t for t in box_teams}
        away_stats = {s.get("name"): s for s in (by_home.get(False, {}).get("statistics") or [])}
        home_stats = {s.get("name"): s for s in (by_home.get(True, {}).get("statistics") or [])}
        for stat_name in list(away_stats)[:12]:
            away_stat = away_stats[stat_name]
            home_stat = home_stats.get(stat_name, {})
            team_stats.append({
                "label": str(away_stat.get("label") or away_stat.get("displayName") or stat_name),
                "away": str(away_stat.get("displayValue") or away_stat.get("value") or "—"),
                "home": str(home_stat.get("displayValue") or home_stat.get("value") or "—"),
            })

    leaders = []
    for category in package.get("leaders", []) if isinstance(package, dict) else []:
        entries = category.get("leaders") or []
        if entries:
            athlete = entries[0].get("athlete") or {}
            leaders.append({
                "category": str(category.get("displayName") or category.get("name") or "Leader"),
                "name": str(athlete.get("displayName") or athlete.get("shortName") or "—"),
                "value": str(entries[0].get("displayValue") or entries[0].get("value") or "—"),
            })

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
        scoring_summary=scoring_summary,
        team_stats=team_stats,
        leaders=leaders,
    )

async def _fetch_with_cache(key: str, fetch_func, fallback_data: Any) -> SportsPayload:
    lock = get_cache_lock(key)
    now = time.time()

    async with lock:
        cached = _app_cache.get(key)
        if cached and (now - cached.timestamp < CACHE_TTL):
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
