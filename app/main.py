"""Yoyosup News — Pulse + meta search + daily platform trends."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.pulse import build_pulse
from app.search import run_search
from app.trends import build_trends

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")

app = FastAPI(title="Yoyosup News", version="0.3.0")
app.mount("/static", StaticFiles(directory=BASE / "static"), name="static")
templates = Jinja2Templates(directory=str(BASE / "templates"))


@app.get("/health")
async def health():
    return {"ok": True, "service": "yoyosup-news", "public": PUBLIC_BASE}


@app.get("/", response_class=HTMLResponse)
async def pulse_home(request: Request):
    data = await build_pulse(force=False)
    return templates.TemplateResponse(
        request,
        "pulse.html",
        {
            "public_base": PUBLIC_BASE,
            "pulse": data,
            "page_title": "Pulse — daily trends",
        },
    )


@app.get("/api/pulse")
async def api_pulse(force: bool = False):
    data = await build_pulse(force=force)
    return JSONResponse(data)


@app.get("/search", response_class=HTMLResponse)
async def search_page(request: Request, q: str = "", force: bool = False):
    results = await run_search(q, force_trends=force)
    title = f"Search: {q.strip()}" if q.strip() else "Meta search — platform trends"
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "public_base": PUBLIC_BASE,
            "q": q.strip(),
            "results": results,
            "page_title": title,
        },
    )


@app.get("/api/search")
async def api_search(q: str = "", force: bool = False):
    data = await run_search(q, force_trends=force)
    return JSONResponse(data)


@app.get("/api/trends")
async def api_trends(force: bool = False):
    """Daily Google / Bing / YouTube / X trends (cached once per UTC day)."""
    data = await build_trends(force=force)
    return JSONResponse(data)
