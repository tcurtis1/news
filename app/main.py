"""Yoyosup News — Pulse + Intersection + topics + comments."""

from __future__ import annotations

import logging
import os
from pathlib import Path
from urllib.parse import quote

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.comments import add_comment, list_comments
from app.pulse import build_pulse
from app.search import run_search
from app.topics import build_topic, slugify, unslug
from app.trends import build_trends, rank_lookup

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")

app = FastAPI(title="Yoyosup News", version="0.6.0")
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


@app.get("/health")
async def health():
    return {"ok": True, "service": "yoyosup-news", "public": PUBLIC_BASE, "version": "0.6.0"}


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


@app.get("/api/pulse")
async def api_pulse(force: bool = False):
    data = await build_pulse(force=force)
    return JSONResponse(data)


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
    data = await build_trends(force=force)
    return JSONResponse(data)


@app.get("/api/rank")
async def api_rank(q: str = "", force: bool = False):
    trends = await build_trends(force=force)
    return JSONResponse(rank_lookup(q, trends))


@app.get("/topic", response_class=HTMLResponse)
async def topic_redirect(q: str = ""):
    """Canonicalize ?q= into /topic/{slug}."""
    if not q.strip():
        return RedirectResponse("/search", status_code=302)
    return RedirectResponse(f"/topic/{slugify(q)}", status_code=302)


@app.get("/topic/{slug}", response_class=HTMLResponse)
async def topic_page(request: Request, slug: str, force: bool = False):
    topic = await build_topic(slug, force=force)
    # Canonical slug redirect
    if slugify(slug) != topic["slug"] and unslug(slug):
        return RedirectResponse(f"/topic/{topic['slug']}", status_code=302)

    flash_error = request.query_params.get("err") or ""
    flash_ok = request.query_params.get("ok") or ""
    return templates.TemplateResponse(
        request,
        "topic.html",
        {
            "public_base": PUBLIC_BASE,
            "topic": topic,
            "page_title": topic["title"],
            "flash_error": flash_error,
            "flash_ok": flash_ok,
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
    website: str = Form(""),  # honeypot
):
    canon = slugify(unslug(slug) or slug)
    ok, err, _comment = add_comment(
        canon,
        name=name,
        body=body,
        client_ip=_client_ip(request),
        honeypot=website,
    )
    if ok:
        return RedirectResponse(
            f"/topic/{canon}?ok={quote('Comment posted.')}#comments",
            status_code=303,
        )
    return RedirectResponse(
        f"/topic/{canon}?err={quote(err)}&name={quote(name[:40])}#comment-form",
        status_code=303,
    )


@app.get("/api/topic/{slug}")
async def api_topic(slug: str, force: bool = False):
    topic = await build_topic(slug, force=force)
    return JSONResponse(topic)


@app.get("/api/topic/{slug}/comments")
async def api_topic_comments(slug: str):
    canon = slugify(unslug(slug) or slug)
    return JSONResponse({"slug": canon, "comments": list_comments(canon)})
