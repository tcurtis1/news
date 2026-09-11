"""FastAPI routes for Yoyo Fantasy Football."""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Form, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from app.fantasy.models import DEFAULT_ROSTER_SLOTS, ScoringFormat
from app.fantasy.service import (
    create_league,
    get_audit_log,
    get_league,
    get_league_by_invite,
    get_players,
    get_user_teams,
    join_league,
    update_league_settings,
)
from app.fantasy.draft import (
    auto_pick_if_timed_out,
    get_draft_board,
    get_draft_status,
    get_team_roster,
    make_draft_pick,
    manage_draft_queue,
    pause_draft,
    reset_draft,
    resume_draft,
    set_draft_order,
    start_draft,
    undo_last_pick,
)
from app.fantasy.matchups import (
    ensure_lineups_for_week,
    finalize_week,
    generate_league_schedule,
    get_league_matchups_for_week,
    get_matchup_details,
    get_team_lineup,
    get_team_matchup_for_week,
    simulate_week_stats,
    swap_lineup_slots,
)
from app.fantasy.waivers import (
    add_drop_free_agent,
    cancel_waiver_claim,
    get_available_players,
    get_league_transactions,
    get_team_waiver_claims,
    process_waivers,
    submit_waiver_claim,
)
from app.fantasy.trades import (
    get_league_trades,
    get_trade_details,
    propose_trade,
    respond_to_trade,
)
from app.fantasy.playoffs import (
    advance_playoff_round_after_week,
    generate_playoff_bracket,
    get_playoff_bracket,
    get_playoff_seedings,
)

router = APIRouter(prefix="/sports/fantasy", tags=["fantasy"])

PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com").rstrip("/")
COOKIE_NAME = "yoyo_fantasy_tokens"


def get_tokens_from_cookie(request: Request) -> List[str]:
    raw = request.cookies.get(COOKIE_NAME)
    if not raw:
        return []
    try:
        data = json.loads(raw)
        return [str(t) for t in data if t] if isinstance(data, list) else []
    except Exception:
        return []


def set_token_cookie(response: Response, current_tokens: List[str], new_token: str) -> None:
    tokens = list(current_tokens)
    if new_token and new_token not in tokens:
        tokens.append(new_token)
    response.set_cookie(
        key=COOKIE_NAME,
        value=json.dumps(tokens[-20:]),
        max_age=31536000,
        path="/",
        samesite="lax",
    )


@router.get("", response_class=HTMLResponse)
async def fantasy_index(request: Request):
    templates: Jinja2Templates = request.app.state.templates
    tokens = get_tokens_from_cookie(request)
    my_teams = get_user_teams(tokens)

    return templates.TemplateResponse(request, "fantasy/index.html", {
        "public_base": PUBLIC_BASE,
        "page_title": "Fantasy Football",
        "meta_description": "Calm, lightning-fast NFL fantasy leagues. Ad-free, no gambling spam, zero bloat.",
        "my_teams": my_teams,
        "active_nav": "fantasy",
    })


@router.get("/create", response_class=HTMLResponse)
async def fantasy_create_form(request: Request):
    templates: Jinja2Templates = request.app.state.templates
    return templates.TemplateResponse(request, "fantasy/create.html", {
        "public_base": PUBLIC_BASE,
        "page_title": "Create a Fantasy League",
        "meta_description": "Start an ad-free NFL fantasy football league in seconds.",
        "scoring_formats": [
            ("half_ppr", "0.5 PPR (Recommended)", "Half point per reception. Balanced and competitive."),
            ("full_ppr", "Full PPR", "1 point per reception. High-scoring receiver value."),
            ("standard", "Standard", "0 points per reception. Traditional ground-game value."),
        ],
        "team_counts": [8, 10, 12, 14],
    })


@router.post("/create", response_class=HTMLResponse)
async def fantasy_create_post(
    request: Request,
    league_name: str = Form(...),
    manager_name: str = Form(...),
    team_name: str = Form(...),
    max_teams: int = Form(10),
    scoring_format: str = Form("half_ppr"),
    waiver_type: str = Form("faab"),
):
    league, commish_team = create_league(
        name=league_name,
        manager_name=manager_name,
        team_name=team_name,
        scoring_format=scoring_format,
        max_teams=max_teams,
        waiver_type=waiver_type,
    )

    tokens = get_tokens_from_cookie(request)
    redirect = RedirectResponse(url=f"/sports/fantasy/league/{league.id}", status_code=303)
    set_token_cookie(redirect, tokens, commish_team.manager_token)
    return redirect


@router.get("/join/{invite_token}", response_class=HTMLResponse)
async def fantasy_join_form(request: Request, invite_token: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league_by_invite(invite_token)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league invite link not found.")

    spots_left = max(0, league.settings.max_teams - len(league.teams))
    is_full = spots_left <= 0

    return templates.TemplateResponse(request, "fantasy/join.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"Join {league.name}",
        "league": league,
        "invite_token": invite_token,
        "spots_left": spots_left,
        "is_full": is_full,
    })


@router.post("/join/{invite_token}", response_class=HTMLResponse)
async def fantasy_join_post(
    request: Request,
    invite_token: str,
    manager_name: str = Form(...),
    team_name: str = Form(...),
):
    templates: Jinja2Templates = request.app.state.templates
    league, team, err = join_league(invite_token, manager_name=manager_name, team_name=team_name)
    if err or not league or not team:
        existing_league = get_league_by_invite(invite_token)
        return templates.TemplateResponse(request, "fantasy/join.html", {
            "public_base": PUBLIC_BASE,
            "page_title": "Join League",
            "league": existing_league,
            "invite_token": invite_token,
            "error": err or "Could not join league.",
            "spots_left": max(0, (existing_league.settings.max_teams if existing_league else 0) - (len(existing_league.teams) if existing_league else 0)),
            "is_full": False,
        }, status_code=400)

    tokens = get_tokens_from_cookie(request)
    redirect = RedirectResponse(url=f"/sports/fantasy/league/{league.id}", status_code=303)
    set_token_cookie(redirect, tokens, team.manager_token)
    return redirect


@router.get("/league/{league_id}", response_class=HTMLResponse)
async def fantasy_league_hub(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league not found.")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)
    audit = get_audit_log(league_id, limit=10)

    invite_url = f"{PUBLIC_BASE}/sports/fantasy/join/{league.invite_token}"
    spots_left = max(0, league.settings.max_teams - len(league.teams))

    return templates.TemplateResponse(request, "fantasy/league.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{league.name} — League Hub",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "invite_url": invite_url,
        "spots_left": spots_left,
        "audit_log": audit,
        "active_tab": "overview",
    })


@router.get("/league/{league_id}/teams", response_class=HTMLResponse)
async def fantasy_league_teams(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league not found.")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)

    return templates.TemplateResponse(request, "fantasy/teams.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{league.name} — Teams & Rosters",
        "league": league,
        "my_team": my_team,
        "active_tab": "teams",
    })


@router.get("/league/{league_id}/settings", response_class=HTMLResponse)
async def fantasy_league_settings_view(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league not found.")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)

    return templates.TemplateResponse(request, "fantasy/settings.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{league.name} — Settings",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "active_tab": "settings",
    })


@router.post("/league/{league_id}/settings", response_class=HTMLResponse)
async def fantasy_league_settings_post(
    request: Request,
    league_id: str,
    name: str = Form(...),
    scoring_format: str = Form(...),
    max_teams: int = Form(...),
    waiver_type: str = Form("faab"),
    faab_budget: int = Form(100),
    pick_timer_seconds: int = Form(60),
):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league not found.")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)

    if not is_commish:
        raise HTTPException(status_code=403, detail="Only the commissioner can update league settings.")

    updated, err = update_league_settings(
        league_id=league_id,
        commissioner_token=league.commissioner_token,
        new_settings={
            "name": name,
            "scoring_format": scoring_format,
            "max_teams": max_teams,
            "waiver_type": waiver_type,
            "faab_budget": faab_budget,
            "pick_timer_seconds": pick_timer_seconds,
        },
        actor_name=my_team.manager_name if my_team else "Commissioner",
    )

    return RedirectResponse(url=f"/sports/fantasy/league/{league_id}/settings?saved=1", status_code=303)


@router.get("/players", response_class=HTMLResponse)
async def fantasy_players_view(request: Request, q: str = "", pos: str = "ALL"):
    templates: Jinja2Templates = request.app.state.templates
    players = get_players(query=q, position=pos, limit=100)
    positions = ["ALL", "QB", "RB", "WR", "TE", "K", "DST"]

    return templates.TemplateResponse(request, "fantasy/players.html", {
        "public_base": PUBLIC_BASE,
        "page_title": "NFL Fantasy Player Directory",
        "meta_description": "Search NFL players, fantasy projections, bye weeks, and depth charts.",
        "players": players,
        "query": q,
        "active_pos": pos.upper(),
        "positions": positions,
    })


@router.get("/league/{league_id}/draft", response_class=HTMLResponse)
async def fantasy_draft_room_view(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="Fantasy league not found.")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)
    draft_status = get_draft_status(league_id) or {}
    players = get_players(limit=250)
    my_roster = get_team_roster(my_team.id) if my_team else []
    my_queue = manage_draft_queue(my_team.id, "list", "") if my_team else []
    board = get_draft_board(league_id)

    return templates.TemplateResponse(request, "fantasy/draft.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{league.name} — Draft Room",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "draft_status": draft_status,
        "players": players,
        "my_roster": my_roster,
        "my_queue": my_queue,
        "board": board,
        "active_tab": "draft",
    })


@router.get("/api/league/{league_id}/draft-state")
async def api_draft_state(request: Request, league_id: str, board: int = 0):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)

    status = get_draft_status(league_id)
    if not status:
        raise HTTPException(status_code=404, detail="Draft status unavailable")

    status["my_team_id"] = my_team.id if my_team else None
    status["is_your_turn"] = bool(my_team and status.get("on_the_clock") and status["on_the_clock"]["id"] == my_team.id)
    status["is_commissioner"] = is_commish
    status["my_roster"] = get_team_roster(my_team.id) if my_team else []
    status["my_queue"] = manage_draft_queue(my_team.id, "list", "") if my_team else []
    if board:
        status["draft_board"] = get_draft_board(league_id)

    return JSONResponse(status)


@router.post("/api/league/{league_id}/draft/pick")
async def api_draft_pick(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    try:
        data = await request.json()
    except Exception:
        data = {}

    player_id = (data.get("player_id") or "").strip()
    if not player_id:
        return JSONResponse({"error": "Player ID is required."}, status_code=400)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    commish_token = league.commissioner_token if (my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token) else None

    pick, err = make_draft_pick(
        league_id=league_id,
        player_id=player_id,
        manager_token=my_team.manager_token if my_team else None,
        commissioner_token=commish_token,
    )
    if err:
        return JSONResponse({"error": err}, status_code=400)

    return JSONResponse({
        "success": True,
        "pick": pick.to_dict() if pick else None,
        "state": get_draft_status(league_id),
    })


@router.post("/api/league/{league_id}/draft/queue")
async def api_draft_queue(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    try:
        data = await request.json()
    except Exception:
        data = {}

    action = data.get("action", "list")
    player_id = (data.get("player_id") or "").strip()

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    if not my_team:
        return JSONResponse({"error": "No active team on this device."}, status_code=403)

    queue = manage_draft_queue(my_team.id, action, player_id)
    return JSONResponse({"queue": queue})


@router.post("/api/league/{league_id}/draft/control")
async def api_draft_control(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (my_team and my_team.manager_token == league.commissioner_token)

    if not is_commish:
        return JSONResponse({"error": "Only the commissioner can control the draft."}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}

    action = (data.get("action") or "").strip()
    err: Optional[str] = None

    if action == "start":
        _, err = start_draft(league_id, league.commissioner_token)
    elif action == "pause":
        _, err = pause_draft(league_id, league.commissioner_token)
    elif action == "resume":
        _, err = resume_draft(league_id, league.commissioner_token)
    elif action == "undo":
        _, err = undo_last_pick(league_id, league.commissioner_token)
    elif action == "randomize_order":
        _, err = set_draft_order(league_id, league.commissioner_token, randomize=True)
    elif action == "reset":
        _, err = reset_draft(league_id, league.commissioner_token)
    else:
        err = f"Unknown action: '{action}'."

    if err:
        return JSONResponse({"error": err}, status_code=400)

    return JSONResponse({
        "success": True,
        "state": get_draft_status(league_id),
    })


@router.get("/api/league/{league_id}/draft-board")
async def api_draft_board(league_id: str):
    board = get_draft_board(league_id)
    return JSONResponse(board)


@router.get("/api/league/{league_id}")
async def api_fantasy_league(league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    data = league.to_dict()
    # Mask commissioner token for public API
    data["commissioner_token"] = "***"
    for t in data.get("teams", []):
        t["manager_token"] = "***"
    return JSONResponse(data)


@router.get("/api/players")
async def api_fantasy_players(q: str = "", pos: str = "ALL", limit: int = 50):
    players = get_players(query=q, position=pos, limit=limit)
    return JSONResponse({"players": [p.to_dict() for p in players]})


# ---------------------------------------------------------------------------
# SPRINT 3: WEEKLY SCHEDULE, HEAD-TO-HEAD MATCHUPS & LINEUPS
# ---------------------------------------------------------------------------

@router.get("/league/{league_id}/matchup", response_class=HTMLResponse)
async def fantasy_current_matchup(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    week = league.current_week or 1

    # Ensure schedule is generated if league has teams
    generate_league_schedule(league_id)

    matchup_id: Optional[str] = None
    if my_team:
        matchup_id = get_team_matchup_for_week(league_id, my_team.id, week)

    if not matchup_id:
        week_matchups = get_league_matchups_for_week(league_id, week)
        if week_matchups:
            matchup_id = week_matchups[0]["id"]

    if not matchup_id:
        return RedirectResponse(f"/sports/fantasy/league/{league_id}/matchups/{week}", status_code=303)

    return RedirectResponse(f"/sports/fantasy/league/{league_id}/matchup/{matchup_id}", status_code=303)


@router.get("/league/{league_id}/matchup/{matchup_id}", response_class=HTMLResponse)
async def fantasy_matchup_view(request: Request, league_id: str, matchup_id: str):
    templates: Jinja2Templates = request.app.state.templates
    details = get_matchup_details(matchup_id)
    if not details:
        raise HTTPException(status_code=404, detail="Matchup not found")

    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    return templates.TemplateResponse(request, "fantasy/matchup.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{details['home']['team']['name']} vs {details['away']['team']['name']} · Week {details['matchup']['week']}",
        "league": league,
        "matchup": details["matchup"],
        "home": details["home"],
        "away": details["away"],
        "comparison_starters": details["comparison_starters"],
        "home_prob": details["home_prob"],
        "away_prob": details["away_prob"],
        "week_matchups": details["week_matchups"],
        "my_team": my_team,
        "is_commissioner": is_commish,
        "active_nav": "matchup",
    })


@router.get("/league/{league_id}/schedule", response_class=HTMLResponse)
async def fantasy_schedule_redirect(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")
    week = league.current_week or 1
    return RedirectResponse(f"/sports/fantasy/league/{league_id}/matchups/{week}", status_code=303)


@router.get("/league/{league_id}/matchups/{week}", response_class=HTMLResponse)
async def fantasy_schedule_view(request: Request, league_id: str, week: int):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    # Ensure schedule is generated
    generate_league_schedule(league_id)

    playoff_extra = 3 if league.settings.playoff_teams == 6 else (2 if league.settings.playoff_teams == 4 else 1)
    total_season_weeks = league.settings.regular_season_weeks + playoff_extra
    week = max(1, min(total_season_weeks, week))
    matchups = get_league_matchups_for_week(league_id, week)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    return templates.TemplateResponse(request, "fantasy/schedule.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"Week {week} Scoreboard · {league.name}",
        "league": league,
        "week": week,
        "total_season_weeks": total_season_weeks,
        "regular_season_weeks": league.settings.regular_season_weeks,
        "matchups": matchups,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "active_nav": "schedule",
    })


@router.get("/league/{league_id}/lineup", response_class=HTMLResponse)
async def fantasy_lineup_view(request: Request, league_id: str, week: Optional[int] = None, team_id: Optional[str] = None):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    playoff_extra = 3 if league.settings.playoff_teams == 6 else (2 if league.settings.playoff_teams == 4 else 1)
    total_season_weeks = league.settings.regular_season_weeks + playoff_extra
    view_week = week if week is not None and 1 <= week <= total_season_weeks else (league.current_week or 1)

    target_team = None
    if team_id:
        target_team = next((t for t in league.teams if t.id == team_id), None)
    if not target_team:
        target_team = my_team or (league.teams[0] if league.teams else None)

    if not target_team:
        raise HTTPException(status_code=404, detail="No teams in league")

    lineup_data = get_team_lineup(league_id, target_team.id, view_week)
    can_edit = bool(my_team and my_team.id == target_team.id) or is_commish

    return templates.TemplateResponse(request, "fantasy/lineup.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"{target_team.name} Lineup · Week {view_week}",
        "league": league,
        "team": target_team,
        "week": view_week,
        "lineup": lineup_data,
        "can_edit": can_edit,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "active_nav": "lineup",
    })


@router.post("/api/league/{league_id}/lineup/swap")
async def api_swap_lineup(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    team_id = data.get("team_id")
    week = int(data.get("week", 1))
    slot_id_1 = data.get("slot_id_1")
    slot_id_2 = data.get("slot_id_2")

    tokens = get_tokens_from_cookie(request)
    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found"}, status_code=404)

    # Actor token: check if user is manager of this team or commish
    my_team = next((t for t in league.teams if t.manager_token in tokens and (t.id == team_id or t.is_commissioner)), None)
    if not my_team:
        commish_token_match = next((tok for tok in tokens if tok == league.commissioner_token), None)
        if not commish_token_match:
            return JSONResponse({"error": "Unauthorized to modify this team's lineup"}, status_code=403)
        actor_token = commish_token_match
    else:
        actor_token = my_team.manager_token

    ok, msg = swap_lineup_slots(league_id, team_id, week, slot_id_1, slot_id_2, actor_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)

    updated_lineup = get_team_lineup(league_id, team_id, week)
    return JSONResponse({"success": True, "message": msg, "lineup": updated_lineup})


@router.get("/api/league/{league_id}/matchup/{matchup_id}")
async def api_matchup_details(league_id: str, matchup_id: str):
    details = get_matchup_details(matchup_id)
    if not details:
        return JSONResponse({"error": "Matchup not found"}, status_code=404)
    return JSONResponse(details)


@router.post("/api/league/{league_id}/matchups/finalize")
async def api_finalize_week(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found"}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)
    if not is_commish:
        return JSONResponse({"error": "Only commissioner can finalize the week"}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}

    week = int(data.get("week", league.current_week))
    ok, msg = finalize_week(league_id, week, league.commissioner_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


@router.post("/api/league/{league_id}/matchups/simulate-week")
async def api_simulate_week_stats(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found"}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)
    if not is_commish:
        return JSONResponse({"error": "Only commissioner can trigger stat simulations"}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}

    week = int(data.get("week", league.current_week))
    ok, msg = simulate_week_stats(league_id, week, league.commissioner_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


# --- WAIVERS & FREE AGENCY ROUTES ---

@router.get("/league/{league_id}/waivers", response_class=HTMLResponse)
async def fantasy_waivers_view(
    request: Request,
    league_id: str,
    q: str = "",
    pos: str = "ALL",
    avail: str = "ALL",
    page: int = 1,
):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    limit = 25
    offset = max(0, (page - 1) * limit)
    data = get_available_players(league_id, query=q, position=pos, availability=avail, limit=limit, offset=offset)

    my_roster = get_team_roster(my_team.id) if my_team else []
    my_claims = get_team_waiver_claims(league_id, my_team.id, status="pending") if my_team else []

    total_pages = max(1, (data["total_count"] + limit - 1) // limit)

    return templates.TemplateResponse(request, "fantasy/waivers.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"Waivers & Free Agency · {league.name}",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "players": data["players"],
        "total_count": data["total_count"],
        "query": q,
        "pos": pos.upper(),
        "avail": avail.upper(),
        "page": page,
        "total_pages": total_pages,
        "my_roster": my_roster,
        "my_claims": my_claims,
        "positions": ["ALL", "QB", "RB", "WR", "TE", "K", "DST"],
        "active_nav": "waivers",
    })


@router.post("/api/league/{league_id}/waivers/add-drop")
async def api_add_drop_free_agent(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    add_player_id = data.get("add_player_id")
    drop_player_id = data.get("drop_player_id")
    if not add_player_id:
        return JSONResponse({"error": "No player selected to add."}, status_code=400)

    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    if not my_team:
        commish_token_match = next((tok for tok in tokens if tok == league.commissioner_token), None)
        if not commish_token_match:
            return JSONResponse({"error": "Unauthorized to perform add/drop transactions."}, status_code=403)
        actor_token = commish_token_match
        target_team_id = data.get("team_id", league.teams[0].id if league.teams else "")
    else:
        actor_token = my_team.manager_token
        target_team_id = my_team.id

    ok, msg = add_drop_free_agent(league_id, target_team_id, add_player_id, drop_player_id, actor_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


@router.post("/api/league/{league_id}/waivers/claim")
async def api_submit_waiver_claim(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    add_player_id = data.get("add_player_id")
    drop_player_id = data.get("drop_player_id")
    bid_amount = int(data.get("bid_amount", 0))
    priority = int(data.get("priority", 1))

    if not add_player_id:
        return JSONResponse({"error": "No player selected to claim."}, status_code=400)

    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    if not my_team:
        return JSONResponse({"error": "Unauthorized: team manager credentials required."}, status_code=403)

    ok, msg = submit_waiver_claim(league_id, my_team.id, add_player_id, drop_player_id, bid_amount, priority, my_team.manager_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


@router.post("/api/league/{league_id}/waivers/cancel")
async def api_cancel_waiver_claim(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    claim_id = data.get("claim_id")
    if not claim_id:
        return JSONResponse({"error": "Claim ID required."}, status_code=400)

    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    if not my_team:
        return JSONResponse({"error": "Unauthorized: team manager credentials required."}, status_code=403)

    ok, msg = cancel_waiver_claim(league_id, my_team.id, claim_id, my_team.manager_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


@router.post("/api/league/{league_id}/waivers/process")
async def api_process_waivers(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    is_commish = league.commissioner_token in tokens or any(t.is_commissioner and t.manager_token in tokens for t in league.teams)
    if not is_commish:
        return JSONResponse({"error": "Only commissioner can trigger waiver processing."}, status_code=403)

    ok, msg, summary = process_waivers(league_id, league.commissioner_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg, "summary": summary})


# --- TRADES ROUTES ---

@router.get("/league/{league_id}/trades", response_class=HTMLResponse)
async def fantasy_trades_view(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    trades = get_league_trades(league_id, status="ALL")

    my_incoming_trades = []
    my_outgoing_trades = []
    league_trades = []

    if my_team:
        for tr in trades:
            if tr.recipient_team_id == my_team.id and tr.status in ("proposed", "accepted"):
                my_incoming_trades.append(tr)
            elif tr.proposer_team_id == my_team.id and tr.status in ("proposed", "accepted"):
                my_outgoing_trades.append(tr)
            else:
                league_trades.append(tr)
    else:
        league_trades = trades

    # Teams and rosters for trade partner selector
    teams_with_rosters = []
    for t in league.teams:
        r = get_team_roster(t.id)
        teams_with_rosters.append({
            "team": t,
            "roster": r,
        })

    return templates.TemplateResponse(request, "fantasy/trades.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"The Trade Machine · {league.name}",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "incoming_trades": my_incoming_trades,
        "outgoing_trades": my_outgoing_trades,
        "league_trades": league_trades,
        "teams_with_rosters": teams_with_rosters,
        "active_nav": "trades",
    })


@router.post("/api/league/{league_id}/trades/propose")
async def api_propose_trade(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    recipient_team_id = data.get("recipient_team_id")
    proposer_player_ids = data.get("proposer_player_ids") or []
    recipient_player_ids = data.get("recipient_player_ids") or []
    note = data.get("note", "")

    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    if not my_team:
        return JSONResponse({"error": "Unauthorized: team manager credentials required."}, status_code=403)

    ok, msg, trade_id = propose_trade(
        league_id=league_id,
        proposer_team_id=my_team.id,
        recipient_team_id=recipient_team_id,
        proposer_player_ids=proposer_player_ids,
        recipient_player_ids=recipient_player_ids,
        note=note,
        actor_token=my_team.manager_token,
    )
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg, "trade_id": trade_id})


@router.post("/api/league/{league_id}/trades/respond")
async def api_respond_trade(request: Request, league_id: str):
    try:
        data = await request.json()
    except Exception:
        data = {}

    trade_id = data.get("trade_id")
    action = data.get("action")  # accept, reject, cancel, veto, approve
    if not trade_id or not action:
        return JSONResponse({"error": "Trade ID and action required."}, status_code=400)

    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    actor_token = league.commissioner_token if league.commissioner_token in tokens else (my_team.manager_token if my_team else "")
    if not actor_token:
        return JSONResponse({"error": "Unauthorized to respond to trades."}, status_code=403)

    ok, msg = respond_to_trade(league_id, trade_id, action, actor_token)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg})


# --- ACTIVITY WIRE ROUTES ---

@router.get("/league/{league_id}/activity", response_class=HTMLResponse)
async def fantasy_activity_view(request: Request, league_id: str, type: Optional[str] = None):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    tx_type = type if type and type != "ALL" else None
    transactions = get_league_transactions(league_id, transaction_type=tx_type, limit=50)

    return templates.TemplateResponse(request, "fantasy/activity.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"League Activity Wire · {league.name}",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "transactions": transactions,
        "current_filter": type or "ALL",
        "active_nav": "activity",
    })


@router.get("/api/league/{league_id}/activity")
async def api_league_activity(league_id: str, type: Optional[str] = None, limit: int = 50):
    txs = get_league_transactions(league_id, transaction_type=type, limit=limit)
    return JSONResponse({"transactions": [tx.to_dict() for tx in txs]})


# --- PLAYOFFS & CHAMPIONSHIP BRACKET ROUTES ---

@router.get("/league/{league_id}/playoffs", response_class=HTMLResponse)
async def fantasy_playoffs_view(request: Request, league_id: str):
    templates: Jinja2Templates = request.app.state.templates
    league = get_league(league_id)
    if not league:
        raise HTTPException(status_code=404, detail="League not found")

    tokens = get_tokens_from_cookie(request)
    my_team = next((t for t in league.teams if t.manager_token in tokens), None)
    is_commish = bool(my_team and my_team.is_commissioner) or (league.commissioner_token in tokens)

    playoff_data = get_playoff_bracket(league_id)

    return templates.TemplateResponse(request, "fantasy/playoffs.html", {
        "public_base": PUBLIC_BASE,
        "page_title": f"Playoffs & Championship · {league.name}",
        "league": league,
        "my_team": my_team,
        "is_commissioner": is_commish,
        "playoff_data": playoff_data,
        "active_nav": "playoffs",
    })


@router.post("/api/league/{league_id}/playoffs/generate")
async def api_generate_playoffs(request: Request, league_id: str):
    league = get_league(league_id)
    if not league:
        return JSONResponse({"error": "League not found."}, status_code=404)

    tokens = get_tokens_from_cookie(request)
    is_commish = league.commissioner_token in tokens or any(t.is_commissioner and t.manager_token in tokens for t in league.teams)
    if not is_commish:
        return JSONResponse({"error": "Only commissioner can generate playoff bracket."}, status_code=403)

    try:
        data = await request.json()
    except Exception:
        data = {}
    force_restart = bool(data.get("force_restart", False))

    ok, msg, res_data = generate_playoff_bracket(league_id, league.commissioner_token, force_restart=force_restart)
    if not ok:
        return JSONResponse({"error": msg}, status_code=400)
    return JSONResponse({"success": True, "message": msg, "data": res_data})


@router.get("/api/league/{league_id}/playoffs")
async def api_get_playoffs(league_id: str):
    data = get_playoff_bracket(league_id)
    return JSONResponse(data)



