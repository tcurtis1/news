"""Yoyosup News — Pulse + Intersection + topics + moderated comments."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.analytics import (
    ADMIN_KEY as ANALYTICS_ADMIN_KEY,
    country_label,
    get_stats,
    is_probable_bot,
    record_client_event,
    record_hit,
)
from app.comments import (
    add_comment,
    list_all_for_admin,
    list_comments,
    report_comment,
    set_comment_status,
)
from app.journalists import add_reader_rating, build_journalist, run_backfill
from app.moderation import moderation_enabled
from app.pulse import build_pulse, paginate_pulse
from app.search import (
    _filter_recent,
    fetch_preferred_headlines,
    paginate_hits,
    run_search,
)
from app.seo import collect_sitemap_urls, render_robots_txt, render_sitemap_xml
from app.places import default_place, list_places_for_ui, resolve_place
from app.source_prefs import (
    PREF_DEFAULT,
    list_catalog,
    normalize_pref,
    pref_meta,
)
from app.topics import build_topic, slugify, unslug
from app.trends import build_trends, rank_lookup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")
MOD_ADMIN_TOKEN = os.environ.get("MOD_ADMIN_TOKEN", "").strip()
APP_VERSION = "0.11.4"
GEO_COOKIE = "yoyonews_geo"
LEAN_COOKIE = "yoyonews_lean"
GEO_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year
LEAN_COOKIE_MAX_AGE = GEO_COOKIE_MAX_AGE

app = FastAPI(title="Yoyosup News", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")


@app.middleware("http")
async def cors_whats_new(request: Request, call_next):
    """Allow Tools Updates widget (and others) to read the public What’s New feed."""
    response = await call_next(request)
    if request.url.path.rstrip("/").endswith("whats-new.json"):
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Cache-Control"] = "no-store"
    return response
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["slugify"] = slugify
templates.env.globals["app_version"] = APP_VERSION
templates.env.globals["country_label"] = country_label


@app.middleware("http")
async def analytics_middleware(request: Request, call_next):
    response = await call_next(request)
    if response.status_code < 400:
        await record_hit(
            path=request.url.path,
            ref=request.query_params.get("ref"),
            country_raw=request.headers.get("cf-ipcountry"),
            state_raw=request.headers.get("cf-region"),
            referer=request.headers.get("referer"),
            city_raw=request.headers.get("cf-ipcity"),
            region_code=request.headers.get("cf-region-code"),
            user_agent=request.headers.get("user-agent"),
            cf_verified_bot=request.headers.get("cf-verified-bot"),
        )
    return response


def _geo_cookie_place(request: Request):
    """Saved non-default location from cookie (validated)."""
    raw = (request.cookies.get(GEO_COOKIE) or "").strip()
    if not raw:
        return None
    place = resolve_place(raw)
    # Only redirect when preference differs from site default (avoids useless hop)
    if place.code == default_place().code:
        return None
    return place


def _set_geo_cookie(response: Response, geo_code: str) -> None:
    response.set_cookie(
        key=GEO_COOKIE,
        value=geo_code,
        max_age=GEO_COOKIE_MAX_AGE,
        httponly=False,  # JS mirrors to localStorage
        samesite="lax",
        secure=PUBLIC_BASE.startswith("https"),
        path="/",
    )


def _lean_from_request(request: Request, lean: str = "") -> str:
    """Explicit ?lean= wins; else cookie; else default (balanced)."""
    if (lean or "").strip():
        return normalize_pref(lean)
    raw = (request.cookies.get(LEAN_COOKIE) or "").strip()
    if raw:
        return normalize_pref(raw)
    return PREF_DEFAULT


def _set_lean_cookie(response: Response, lean: str) -> None:
    response.set_cookie(
        key=LEAN_COOKIE,
        value=normalize_pref(lean),
        max_age=LEAN_COOKIE_MAX_AGE,
        httponly=False,  # JS can read/sync with localStorage
        samesite="lax",
        secure=PUBLIC_BASE.startswith("https"),
        path="/",
    )


def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for") or ""
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host or ""
    return ""


def _admin_ok(token: str) -> bool:
    return bool(MOD_ADMIN_TOKEN) and token == MOD_ADMIN_TOKEN


@app.get("/health")
async def health():
    d = default_place()
    return {
        "ok": True,
        "service": "yoyosup-news",
        "public": PUBLIC_BASE,
        "version": APP_VERSION,
        "default_geo": d.code,
        "moderation": moderation_enabled(),
    }


@app.post("/api/analytics-event")
async def analytics_event(request: Request):
    if is_probable_bot(request.headers.get("user-agent"), request.headers.get("cf-verified-bot")):
        return Response(status_code=204)
    # Normal browser beacons are same-origin. Reject cross-site submissions that
    # could otherwise contaminate the small aggregate event counters.
    fetch_site = (request.headers.get("sec-fetch-site") or "").lower()
    if fetch_site and fetch_site not in {"same-origin", "same-site"}:
        return Response(status_code=204)
    try:
        payload = await request.json()
    except Exception:
        return Response(status_code=204)
    await record_client_event(str(payload.get("name") or ""))
    return Response(status_code=204)


@app.get("/api/places")
async def api_places():
    return JSONResponse(list_places_for_ui())


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    return render_robots_txt()


@app.get("/sitemap.xml")
async def sitemap_xml():
    urls = await collect_sitemap_urls()
    xml = render_sitemap_xml(urls)
    return Response(content=xml, media_type="application/xml")


@app.get("/", response_class=HTMLResponse)
async def pulse_home(request: Request):
    data = await build_pulse(force=False)
    return templates.TemplateResponse(
        request,
        "pulse.html",
        {
            "public_base": PUBLIC_BASE,
            "pulse": data,
            "page_title": "Curious Pulse",
        },
    )


@app.get("/safety", response_class=HTMLResponse)
async def safety_page(request: Request):
    return templates.TemplateResponse(
        request,
        "safety.html",
        {
            "public_base": PUBLIC_BASE,
            "page_title": "Safety & guidelines",
        },
    )


@app.get("/api/pulse")
async def api_pulse(
    force: bool = False,
    offset: int = 0,
    limit: int = 0,
):
    """
    Full pulse payload by default.
    Pass limit>0 (and optional offset) for paged slices used by "Load 20 more".
    """
    data = await build_pulse(force=force)
    if limit and int(limit) > 0:
        return JSONResponse(paginate_pulse(data, offset=offset, limit=limit))
    return JSONResponse(data)


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request,
    q: str = "",
    force: bool = False,
    geo: str = "",
    lean: str = "",
):
    # Prefer explicit ?geo=; else cookie (no client paint-then-redirect flash)
    if not (geo or "").strip():
        saved = _geo_cookie_place(request)
        if saved is not None:
            params = []
            if q.strip():
                params.append(f"q={quote(q.strip())}")
            if force:
                params.append("force=1")
            params.append(f"geo={quote(saved.code)}")
            lean_saved = _lean_from_request(request, lean)
            if lean_saved:
                params.append(f"lean={quote(lean_saved)}")
            return RedirectResponse("/search?" + "&".join(params), status_code=302)

    place = resolve_place(geo or None)
    lean_pref = _lean_from_request(request, lean)
    # Empty Intersection must stay fast: defer preferred headlines to
    # /api/headlines (client hydrates). Topic search still runs inline.
    results = await run_search(
        q,
        force_trends=force,
        geo=place.code,
        lean=lean_pref,
        defer_headlines=not bool(q.strip()),
    )
    title = f"Rank map: {q.strip()}" if q.strip() else "Daily Intersection"
    places_ui = list_places_for_ui()
    resp = templates.TemplateResponse(
        request,
        "search.html",
        {
            "public_base": PUBLIC_BASE,
            "q": q.strip(),
            "geo": place.code,
            "place": place.to_dict(),
            "places_ui": places_ui,
            "results": results,
            "lean_pref": lean_pref,
            "lean_meta": pref_meta(lean_pref),
            "page_title": title,
            "topic_slug": slugify(q) if q.strip() else "",
        },
    )
    # Remember location for next visit (server-side; avoids FOUC redirect)
    if (geo or "").strip():
        _set_geo_cookie(resp, place.code)
    # Always persist lean so next visit matches the control
    _set_lean_cookie(resp, lean_pref)
    return resp


@app.get("/my", response_class=HTMLResponse)
@app.get("/mynews", response_class=HTMLResponse)
async def mynews_page(request: Request, geo: str = "", lean: str = ""):
    """
    Personal topic board (client-side localStorage). No auth.
    Shell is server-rendered; topics + headlines hydrate in the browser.
    """
    if not (geo or "").strip():
        saved = _geo_cookie_place(request)
        if saved is not None:
            lean_saved = _lean_from_request(request, lean)
            return RedirectResponse(
                f"/my?geo={quote(saved.code)}&lean={quote(lean_saved)}",
                status_code=302,
            )

    place = resolve_place(geo or None)
    lean_pref = _lean_from_request(request, lean)
    places_ui = list_places_for_ui()
    resp = templates.TemplateResponse(
        request,
        "mynews.html",
        {
            "public_base": PUBLIC_BASE,
            "geo": place.code,
            "place": place.to_dict(),
            "places_ui": places_ui,
            "lean_pref": lean_pref,
            "lean_meta": pref_meta(lean_pref),
            "page_title": "MyNews",
        },
    )
    if (geo or "").strip():
        _set_geo_cookie(resp, place.code)
    _set_lean_cookie(resp, lean_pref)
    return resp


@app.get("/api/search")
async def api_search(
    request: Request,
    q: str = "",
    force: bool = False,
    geo: str = "",
    lean: str = "",
    lite: bool = False,
    days: int = 0,
):
    """
    Full search by default. Pass lite=1 for MyNews cards (faster:
    preferred headlines + rank map only, hard time budget).
    Pass days=N to keep only hits with a parsed publish date in the last N days
    (undated hits are dropped, since recency can't be verified for them).
    """
    place = resolve_place(geo or None)
    lean_pref = _lean_from_request(request, lean)
    data = await run_search(
        q,
        force_trends=force,
        geo=place.code,
        lean=lean_pref,
        lite=bool(lite),
        # API callers who want empty-q headlines use /api/headlines
        defer_headlines=not bool(q.strip()),
        days=days or None,
    )
    if q.strip():
        data["topic_path"] = (
            f"/topic/{slugify(q)}?geo={place.code}&lean={lean_pref}"
        )
    return JSONResponse(data)


@app.get("/api/headlines")
async def api_headlines(
    request: Request,
    lean: str = "",
    q: str = "",
    offset: int = 0,
    limit: int = 20,
    days: int = 0,
):
    """
    Preferred-source headlines only (cached, hard budget).
    Used by Intersection after the page paints — never blocks SSR.

    Pagination: `offset` + `limit` (default 20). Filter with `lean` and
    optional `days` recency (same as MyNews date bar). `has_more` tells
    the client whether "Load 20 more" should stay available.
    """
    lean_pref = _lean_from_request(request, lean)
    hits = await fetch_preferred_headlines(
        lean_pref, q, budget_sec=6.5, use_google=bool((q or "").strip())
    )
    try:
        days_n = int(days or 0)
    except (TypeError, ValueError):
        days_n = 0
    if days_n > 0:
        hits = _filter_recent(hits, days_n)
    page = paginate_hits(hits, offset=offset, limit=limit)
    meta = pref_meta(lean_pref)
    return JSONResponse(
        {
            "lean_pref": lean_pref,
            **meta,
            "q": (q or "").strip(),
            "days": days_n if days_n > 0 else None,
            "cached": True,  # process may have served from cache
            **page,
        }
    )


@app.get("/api/source-prefs")
async def api_source_prefs(lean: str = ""):
    """Catalog of Conservative / Balanced / Liberal source lists."""
    if (lean or "").strip():
        return JSONResponse(list_catalog(lean))
    return JSONResponse(list_catalog())


@app.get("/api/trends")
async def api_trends(force: bool = False, geo: str = ""):
    place = resolve_place(geo or None)
    return JSONResponse(await build_trends(force=force, geo=place.code))


@app.get("/api/rank")
async def api_rank(q: str = "", force: bool = False, geo: str = ""):
    place = resolve_place(geo or None)
    trends = await build_trends(force=force, geo=place.code)
    data = rank_lookup(q, trends)
    data["geo"] = place.code
    data["place"] = place.to_dict()
    return JSONResponse(data)


@app.get("/topic", response_class=HTMLResponse)
async def topic_redirect(q: str = "", geo: str = ""):
    if not q.strip():
        suffix = f"?geo={quote(geo)}" if geo else ""
        return RedirectResponse(f"/search{suffix}", status_code=302)
    place = resolve_place(geo or None)
    return RedirectResponse(
        f"/topic/{slugify(q)}?geo={place.code}", status_code=302
    )


@app.get("/topic/{slug}", response_class=HTMLResponse)
async def topic_page(
    request: Request,
    slug: str,
    force: bool = False,
    geo: str = "",
    lean: str = "",
):
    place = resolve_place(geo or None)
    lean_pref = _lean_from_request(request, lean)
    topic = await build_topic(slug, force=force, geo=place.code, lean=lean_pref)
    if slugify(slug) != topic["slug"] and unslug(slug):
        return RedirectResponse(
            f"/topic/{topic['slug']}?geo={place.code}&lean={lean_pref}",
            status_code=302,
        )

    resp = templates.TemplateResponse(
        request,
        "topic.html",
        {
            "public_base": PUBLIC_BASE,
            "topic": topic,
            "geo": place.code,
            "place": place.to_dict(),
            "lean_pref": lean_pref,
            "lean_meta": pref_meta(lean_pref),
            "page_title": topic["title"],
            "flash_error": request.query_params.get("err") or "",
            "flash_ok": request.query_params.get("ok") or "",
            "form_name": request.query_params.get("name") or "",
            "form_body": "",
        },
    )
    _set_lean_cookie(resp, lean_pref)
    return resp


@app.post("/topic/{slug}/comments")
async def topic_comment_post(
    request: Request,
    slug: str,
    name: str = Form(""),
    body: str = Form(""),
    website: str = Form(""),
):
    canon = slugify(unslug(slug) or slug)
    ok, msg, comment = await add_comment(
        canon,
        name=name,
        body=body,
        client_ip=_client_ip(request),
        honeypot=website,
    )
    if ok:
        return RedirectResponse(
            f"/topic/{canon}?ok={quote(msg)}#comments",
            status_code=303,
        )
    return RedirectResponse(
        f"/topic/{canon}?err={quote(msg)}&name={quote(name[:40])}#comment-form",
        status_code=303,
    )


@app.post("/topic/{slug}/comments/{comment_id}/report")
async def topic_comment_report(
    request: Request,
    slug: str,
    comment_id: str,
    reason: str = Form(""),
):
    canon = slugify(unslug(slug) or slug)
    ok, msg = report_comment(
        canon,
        comment_id,
        reason=reason,
        client_ip=_client_ip(request),
    )
    param = "ok" if ok else "err"
    return RedirectResponse(
        f"/topic/{canon}?{param}={quote(msg)}#c-{comment_id}",
        status_code=303,
    )


@app.get("/api/topic/{slug}")
async def api_topic(slug: str, force: bool = False, geo: str = ""):
    place = resolve_place(geo or None)
    return JSONResponse(await build_topic(slug, force=force, geo=place.code))


@app.get("/api/topic/{slug}/comments")
async def api_topic_comments(slug: str):
    canon = slugify(unslug(slug) or slug)
    return JSONResponse({"slug": canon, "comments": list_comments(canon)})


@app.get("/journalist/{slug}", response_class=HTMLResponse)
async def journalist_page(request: Request, slug: str):
    journalist = build_journalist(slug)
    if slugify(slug) != journalist["slug"] and unslug(slug):
        return RedirectResponse(f"/journalist/{journalist['slug']}", status_code=302)

    return templates.TemplateResponse(
        request,
        "journalist.html",
        {
            "public_base": PUBLIC_BASE,
            "journalist": journalist,
            "page_title": f"{journalist['name']} — journalist bias estimate",
            "flash_error": request.query_params.get("err") or "",
            "flash_ok": request.query_params.get("ok") or "",
            "form_name": request.query_params.get("name") or "",
        },
    )


@app.post("/journalist/{slug}/comments")
async def journalist_comment_post(
    request: Request,
    slug: str,
    name: str = Form(""),
    body: str = Form(""),
    website: str = Form(""),
):
    canon = slugify(unslug(slug) or slug)
    ok, msg, comment = await add_comment(
        f"jrn-{canon}",
        name=name,
        body=body,
        client_ip=_client_ip(request),
        honeypot=website,
    )
    if ok:
        return RedirectResponse(
            f"/journalist/{canon}?ok={quote(msg)}#comments",
            status_code=303,
        )
    return RedirectResponse(
        f"/journalist/{canon}?err={quote(msg)}&name={quote(name[:40])}#comment-form",
        status_code=303,
    )


@app.post("/journalist/{slug}/comments/{comment_id}/report")
async def journalist_comment_report(
    request: Request,
    slug: str,
    comment_id: str,
    reason: str = Form(""),
):
    canon = slugify(unslug(slug) or slug)
    ok, msg = report_comment(
        f"jrn-{canon}",
        comment_id,
        reason=reason,
        client_ip=_client_ip(request),
    )
    param = "ok" if ok else "err"
    return RedirectResponse(
        f"/journalist/{canon}?{param}={quote(msg)}#c-{comment_id}",
        status_code=303,
    )


@app.post("/journalist/{slug}/rate")
async def journalist_rate(request: Request, slug: str, choice: str = Form("")):
    canon = slugify(unslug(slug) or slug)
    ok, msg = add_reader_rating(canon, choice, client_ip=_client_ip(request))
    param = "ok" if ok else "err"
    return RedirectResponse(
        f"/journalist/{canon}?{param}={quote(msg)}#rate",
        status_code=303,
    )


@app.get("/api/journalist/{slug}")
async def api_journalist(slug: str):
    return JSONResponse(build_journalist(slug))


@app.post("/internal/backfill-bylines")
async def internal_backfill_bylines(request: Request, limit: int = 25):
    """Loopback-only maintenance hook — called by scripts/backfill-bylines.sh via cron."""
    host = request.client.host if request.client else ""
    if host not in ("127.0.0.1", "::1"):
        return JSONResponse({"ok": False, "error": "Forbidden"}, status_code=403)
    result = await run_backfill(limit=max(1, min(limit, 100)))
    return JSONResponse({"ok": True, **result})


@app.get("/api/analytics")
async def api_analytics(key: str = "", day: str = ""):
    """JSON stats for tools admin network rollup (admin key required)."""
    if not ANALYTICS_ADMIN_KEY or key != ANALYTICS_ADMIN_KEY:
        return JSONResponse({"ok": False, "error": "Unauthorized"}, status_code=401)
    return JSONResponse(get_stats(day=day or None))


@app.get("/admin/mod", response_class=HTMLResponse)
async def admin_mod(request: Request, token: str = ""):
    authorized = _admin_ok(token)
    rows = list_all_for_admin() if authorized else []
    return templates.TemplateResponse(
        request,
        "admin_mod.html",
        {
            "public_base": PUBLIC_BASE,
            "page_title": "Moderation queue",
            "authorized": authorized,
            "token": token if authorized else "",
            "rows": rows,
        },
    )


@app.post("/admin/mod/action")
async def admin_mod_action(
    token: str = Form(""),
    slug: str = Form(""),
    comment_id: str = Form(""),
    status: str = Form(""),
):
    if not _admin_ok(token):
        return RedirectResponse("/admin/mod", status_code=303)
    set_comment_status(slug, comment_id, status)
    return RedirectResponse(f"/admin/mod?token={quote(token)}", status_code=303)
