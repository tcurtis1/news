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

from app.comments import (
    add_comment,
    list_all_for_admin,
    list_comments,
    report_comment,
    set_comment_status,
)
from app.moderation import moderation_enabled
from app.pulse import build_pulse
from app.search import run_search
from app.seo import collect_sitemap_urls, render_robots_txt, render_sitemap_xml
from app.places import default_place, list_places_for_ui, resolve_place
from app.topics import build_topic, slugify, unslug
from app.trends import build_trends, rank_lookup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")
MOD_ADMIN_TOKEN = os.environ.get("MOD_ADMIN_TOKEN", "").strip()
APP_VERSION = "0.9.2"
GEO_COOKIE = "yoyonews_geo"
GEO_COOKIE_MAX_AGE = 60 * 60 * 24 * 365  # 1 year

app = FastAPI(title="Yoyosup News", version=APP_VERSION)
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["slugify"] = slugify
templates.env.globals["app_version"] = APP_VERSION


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
async def api_pulse(force: bool = False):
    return JSONResponse(await build_pulse(force=force))


@app.get("/search", response_class=HTMLResponse)
async def search_page(
    request: Request, q: str = "", force: bool = False, geo: str = ""
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
            return RedirectResponse("/search?" + "&".join(params), status_code=302)

    place = resolve_place(geo or None)
    results = await run_search(q, force_trends=force, geo=place.code)
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
            "page_title": title,
            "topic_slug": slugify(q) if q.strip() else "",
        },
    )
    # Remember location for next visit (server-side; avoids FOUC redirect)
    if (geo or "").strip():
        _set_geo_cookie(resp, place.code)
    return resp


@app.get("/api/search")
async def api_search(q: str = "", force: bool = False, geo: str = ""):
    place = resolve_place(geo or None)
    data = await run_search(q, force_trends=force, geo=place.code)
    if q.strip():
        data["topic_path"] = f"/topic/{slugify(q)}?geo={place.code}"
    return JSONResponse(data)


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
    request: Request, slug: str, force: bool = False, geo: str = ""
):
    place = resolve_place(geo or None)
    topic = await build_topic(slug, force=force, geo=place.code)
    if slugify(slug) != topic["slug"] and unslug(slug):
        return RedirectResponse(
            f"/topic/{topic['slug']}?geo={place.code}", status_code=302
        )

    return templates.TemplateResponse(
        request,
        "topic.html",
        {
            "public_base": PUBLIC_BASE,
            "topic": topic,
            "geo": place.code,
            "place": place.to_dict(),
            "page_title": topic["title"],
            "flash_error": request.query_params.get("err") or "",
            "flash_ok": request.query_params.get("ok") or "",
            "form_name": request.query_params.get("name") or "",
            "form_body": "",
        },
    )


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
