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
    situation: Optional[str] = None
    context_line: Optional[str] = None
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
    _headlines_cache.clear()
    _cache_locks.clear()


def league_news_query(league: str) -> str:
    return LEAGUE_NEWS_QUERY.get(league, "")


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


def _extract_situation(package: Dict[str, Any], competition: Dict[str, Any]) -> str:
    sit = package.get("situation") if isinstance(package.get("situation"), dict) else {}
    outs = sit.get("outs")
    if outs is None:
        outs = competition.get("outs")
    balls, strikes = sit.get("balls"), sit.get("strikes")
    bits = []
    if balls is not None and strikes is not None:
        bits.append(f"{balls}-{strikes}")
    if outs is not None:
        bits.append("0 outs" if outs == 0 else ("1 out" if outs == 1 else f"{outs} outs"))
    on = []
    if sit.get("onFirst"):
        on.append("1st")
    if sit.get("onSecond"):
        on.append("2nd")
    if sit.get("onThird"):
        on.append("3rd")
    if on:
        bits.append("runners on " + ", ".join(on))
    return ", ".join(bits)


def _overall_record(comp: Dict[str, Any]) -> str:
    for rec in comp.get("records") or []:
        if not isinstance(rec, dict):
            continue
        if rec.get("type") == "total" or str(rec.get("name") or "").lower() == "overall":
            summary = str(rec.get("summary") or "").strip()
            if summary and summary not in ("0-0", "0-0-0"):
                return summary
    return ""


def _team_rank(comp: Dict[str, Any]) -> Optional[int]:
    rank = (comp.get("curatedRank") or (comp.get("team") or {}).get("curatedRank") or {}).get("current")
    try:
        value = int(rank)
    except (TypeError, ValueError):
        return None
    if value <= 0 or value >= 99:
        return None
    return value


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

        team = Team(
            id=str(t_data.get("id", "0")),
            name=t_data.get("displayName", "Unknown"),
            abbreviation=t_data.get("abbreviation", "UNK"),
            score=score,
            is_home=(comp.get("homeAway") == "home"),
            winner=comp.get("winner")
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
    situation = _extract_situation(pkg, comp0)
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
    if board.context_line:
        detail.context_line = board.context_line
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
