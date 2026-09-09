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
