"""Lightweight topic comments (JSON files under CACHE_DIR). No accounts yet."""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger("comments")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
COMMENTS_DIR = CACHE_DIR / "comments"
MAX_BODY = 2000
MAX_NAME = 40
MIN_BODY = 2
MAX_PER_TOPIC = 500
# Simple in-process rate limit: one post per IP per N seconds
RATE_LIMIT_SEC = int(os.environ.get("COMMENT_RATE_LIMIT_SEC", "20"))
_lock = threading.Lock()
_last_post: dict[str, float] = {}


def _topic_path(slug: str) -> Path:
    safe = re.sub(r"[^\w\-]", "", (slug or "").lower())[:80] or "topic"
    return COMMENTS_DIR / f"{safe}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def list_comments(slug: str) -> list[dict[str, Any]]:
    path = _topic_path(slug)
    try:
        if not path.exists():
            return []
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("comments") or []
        if not isinstance(items, list):
            return []
        # newest last for Fark-style chronological thread
        return items[-MAX_PER_TOPIC:]
    except Exception as e:
        log.warning("list_comments %s: %s", slug, e)
        return []


def _save(slug: str, comments: list[dict[str, Any]]) -> None:
    COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = _topic_path(slug)
    payload = {
        "slug": slug,
        "updated_at": _now_iso(),
        "comments": comments[-MAX_PER_TOPIC:],
    }
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def add_comment(
    slug: str,
    *,
    name: str,
    body: str,
    client_ip: str = "",
    honeypot: str = "",
) -> tuple[bool, str, dict[str, Any] | None]:
    """
    Add a comment. Returns (ok, error_message, comment_or_none).
    """
    # Bot trap
    if (honeypot or "").strip():
        return False, "Rejected.", None

    name = re.sub(r"\s+", " ", (name or "").strip())[:MAX_NAME]
    body = (body or "").strip()
    if len(body) < MIN_BODY:
        return False, "Comment is too short.", None
    if len(body) > MAX_BODY:
        return False, f"Comment is too long (max {MAX_BODY} chars).", None
    if not name:
        name = "Anonymous"
    # Strip basic HTML
    body = re.sub(r"<[^>]+>", "", body)
    name = re.sub(r"<[^>]+>", "", name)

    ip = (client_ip or "unknown").split(",")[0].strip() or "unknown"
    now = time.time()
    with _lock:
        last = _last_post.get(ip, 0)
        if now - last < RATE_LIMIT_SEC:
            wait = int(RATE_LIMIT_SEC - (now - last)) + 1
            return False, f"Please wait {wait}s before posting again.", None
        _last_post[ip] = now

        comments = list_comments(slug)
        comment = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "body": body,
            "created_at": _now_iso(),
        }
        comments.append(comment)
        try:
            _save(slug, comments)
        except Exception as e:
            log.warning("save comment failed: %s", e)
            return False, "Could not save comment.", None
        return True, "", comment
