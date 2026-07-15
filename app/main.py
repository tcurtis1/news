"""Yoyosup News — Pulse MVP (daily trends)."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app.pulse import build_pulse

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("news")

BASE = Path(__file__).resolve().parent
PUBLIC_BASE = os.environ.get("PUBLIC_BASE", "https://news.yoyosup.com")

app = FastAPI(title="Yoyosup News", version="0.1.0")
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
async def search_placeholder(request: Request, q: str = ""):
    """Placeholder route — multi-source search is next."""
    return templates.TemplateResponse(
        request,
        "search.html",
        {
            "public_base": PUBLIC_BASE,
            "q": q,
            "page_title": "Search (coming soon)",
        },
    )
