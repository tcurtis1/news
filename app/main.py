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
from app.topics import build_topic, slugify, unslug
from app.trends import build_trends, rank_lookup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")
MOD_ADMIN_TOKEN = os.environ.get("MOD_ADMIN_TOKEN", "").strip()

app = FastAPI(title="Yoyosup News", version="0.7.1")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))
templates.env.globals["slugify"] = slugify


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
    return {
        "ok": True,
        "service": "yoyosup-news",
        "public": PUBLIC_BASE,
        "version": "0.7.1",
        "moderation": moderation_enabled(),
    }


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
async def search_page(request: Request, q: str = "", force: bool = False):
    results = await run_search(q, force_trends=force)
    title = f"Rank map: {q.strip()}" if q.strip() else "Daily Intersection"
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "public_base": PUBLIC_BASE,
            "q": q.strip(),
            "results": results,
            "page_title": title,
            "topic_slug": slugify(q) if q.strip() else "",
        },
    )


@app.get("/api/search")
async def api_search(q: str = "", force: bool = False):
    data = await run_search(q, force_trends=force)
    if q.strip():
        data["topic_path"] = f"/topic/{slugify(q)}"
    return JSONResponse(data)


@app.get("/api/trends")
async def api_trends(force: bool = False):
    return JSONResponse(await build_trends(force=force))


@app.get("/api/rank")
async def api_rank(q: str = "", force: bool = False):
    trends = await build_trends(force=force)
    return JSONResponse(rank_lookup(q, trends))


@app.get("/topic", response_class=HTMLResponse)
async def topic_redirect(q: str = ""):
    if not q.strip():
        return RedirectResponse("/search", status_code=302)
    return RedirectResponse(f"/topic/{slugify(q)}", status_code=302)


@app.get("/topic/{slug}", response_class=HTMLResponse)
async def topic_page(request: Request, slug: str, force: bool = False):
    topic = await build_topic(slug, force=force)
    if slugify(slug) != topic["slug"] and unslug(slug):
        return RedirectResponse(f"/topic/{topic['slug']}", status_code=302)

    return templates.TemplateResponse(
        request,
        "topic.html",
        {
            "public_base": PUBLIC_BASE,
            "topic": topic,
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
async def api_topic(slug: str, force: bool = False):
    return JSONResponse(await build_topic(slug, force=force))


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
