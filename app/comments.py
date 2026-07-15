"""Topic comments with moderation status (JSON under CACHE_DIR)."""

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

from app.moderation import moderate_text

log = logging.getLogger("comments")

CACHE_DIR = Path(os.environ.get("CACHE_DIR", "/data"))
COMMENTS_DIR = CACHE_DIR / "comments"
REPORTS_FILE = COMMENTS_DIR / "_reports.json"
MAX_BODY = 2000
MAX_NAME = 40
MIN_BODY = 2
MAX_PER_TOPIC = 500
RATE_LIMIT_SEC = int(os.environ.get("COMMENT_RATE_LIMIT_SEC", "20"))
_lock = threading.Lock()
_last_post: dict[str, float] = {}


def _topic_path(slug: str) -> Path:
    safe = re.sub(r"[^\w\-]", "", (slug or "").lower())[:80] or "topic"
    return COMMENTS_DIR / f"{safe}.json"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_topic(slug: str) -> dict[str, Any]:
    path = _topic_path(slug)
    try:
        if not path.exists():
            return {"slug": slug, "comments": []}
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data.get("comments"), list):
            data["comments"] = []
        return data
    except Exception as e:
        log.warning("load topic %s: %s", slug, e)
        return {"slug": slug, "comments": []}


def _save_topic(slug: str, data: dict[str, Any]) -> None:
    COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
    data["slug"] = slug
    data["updated_at"] = _now_iso()
    data["comments"] = (data.get("comments") or [])[-MAX_PER_TOPIC:]
    _topic_path(slug).write_text(json.dumps(data, indent=2), encoding="utf-8")


def list_comments(slug: str, *, include_held: bool = False) -> list[dict[str, Any]]:
    """Public list: only published (and legacy comments without status)."""
    items = _load_topic(slug).get("comments") or []
    out = []
    for c in items:
        status = c.get("status") or "published"
        if status == "published" or (include_held and status == "held"):
            # Never expose raw moderation scores to public
            pub = {
                "id": c.get("id"),
                "name": c.get("name"),
                "body": c.get("body"),
                "created_at": c.get("created_at"),
                "status": status,
            }
            out.append(pub)
        elif status == "published":
            out.append(c)
    return out[-MAX_PER_TOPIC:]


def list_all_for_admin(slug: str | None = None) -> list[dict[str, Any]]:
    """Admin: held + reported across topics (or one slug)."""
    COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, Any]] = []
    paths = []
    if slug:
        paths = [_topic_path(slug)]
    else:
        paths = sorted(COMMENTS_DIR.glob("*.json"))
        paths = [p for p in paths if p.name != "_reports.json"]

    for path in paths:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        s = data.get("slug") or path.stem
        for c in data.get("comments") or []:
            status = c.get("status") or "published"
            if status in ("held", "removed") or c.get("report_count", 0) > 0:
                row = dict(c)
                row["slug"] = s
                rows.append(row)
    rows.sort(key=lambda r: r.get("created_at") or "", reverse=True)
    return rows


async def add_comment(
    slug: str,
    *,
    name: str,
    body: str,
    client_ip: str = "",
    honeypot: str = "",
) -> tuple[bool, str, dict[str, Any] | None]:
    """
    Add a comment after moderation.
    Returns (ok, message, comment_or_none).
    ok=True with status held still returns True (accepted but not public).
    """
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
    body = re.sub(r"<[^>]+>", "", body)
    name = re.sub(r"<[^>]+>", "", name)

    # Moderate name + body together
    mod = await moderate_text(f"{name}\n{body}")
    action = mod.get("action") or "allow"

    if action == "block":
        return (
            False,
            "This comment can’t be posted — it may violate our safety guidelines.",
            None,
        )

    ip = (client_ip or "unknown").split(",")[0].strip() or "unknown"
    now = time.time()
    with _lock:
        last = _last_post.get(ip, 0)
        if now - last < RATE_LIMIT_SEC:
            wait = int(RATE_LIMIT_SEC - (now - last)) + 1
            return False, f"Please wait {wait}s before posting again.", None
        _last_post[ip] = now

        status = "held" if action == "hold" else "published"
        comment = {
            "id": uuid.uuid4().hex[:12],
            "name": name,
            "body": body,
            "created_at": _now_iso(),
            "status": status,
            "report_count": 0,
            "moderation": {
                "action": action,
                "reason": mod.get("reason"),
                "source": mod.get("source"),
                "flagged": mod.get("flagged"),
                # Store category flags only (not full score dump) for admin
                "categories": {
                    k: v for k, v in (mod.get("categories") or {}).items() if v
                },
            },
        }
        data = _load_topic(slug)
        comments = data.get("comments") or []
        comments.append(comment)
        data["comments"] = comments
        try:
            _save_topic(slug, data)
        except Exception as e:
            log.warning("save comment failed: %s", e)
            return False, "Could not save comment.", None

        # Public-facing copy omits moderation internals
        public = {
            "id": comment["id"],
            "name": comment["name"],
            "body": comment["body"],
            "created_at": comment["created_at"],
            "status": status,
        }
        if status == "held":
            return (
                True,
                "Thanks — your comment was held for safety review and is not public yet.",
                public,
            )
        return True, "Comment posted.", public


def report_comment(
    slug: str,
    comment_id: str,
    *,
    reason: str = "",
    client_ip: str = "",
) -> tuple[bool, str]:
    reason = re.sub(r"<[^>]+>", "", (reason or "").strip())[:500]
    with _lock:
        data = _load_topic(slug)
        comments = data.get("comments") or []
        found = None
        for c in comments:
            if c.get("id") == comment_id:
                found = c
                break
        if not found:
            return False, "Comment not found."
        if found.get("status") == "removed":
            return True, "Already removed."

        found["report_count"] = int(found.get("report_count") or 0) + 1
        found["last_reported_at"] = _now_iso()
        # Auto-hold after 2 reports if still published
        if found.get("status", "published") == "published" and found["report_count"] >= 2:
            found["status"] = "held"
            found["held_reason"] = "user_reports"
        _save_topic(slug, data)

        # Append to global reports log
        try:
            COMMENTS_DIR.mkdir(parents=True, exist_ok=True)
            reports = []
            if REPORTS_FILE.exists():
                reports = json.loads(REPORTS_FILE.read_text(encoding="utf-8"))
                if not isinstance(reports, list):
                    reports = []
            reports.append(
                {
                    "slug": slug,
                    "comment_id": comment_id,
                    "reason": reason,
                    "ip": (client_ip or "")[:64],
                    "at": _now_iso(),
                }
            )
            REPORTS_FILE.write_text(
                json.dumps(reports[-2000:], indent=2), encoding="utf-8"
            )
        except Exception as e:
            log.warning("report log failed: %s", e)

        return True, "Report received. Thank you."


def set_comment_status(
    slug: str, comment_id: str, status: str
) -> tuple[bool, str]:
    if status not in ("published", "held", "removed"):
        return False, "Invalid status."
    with _lock:
        data = _load_topic(slug)
        for c in data.get("comments") or []:
            if c.get("id") == comment_id:
                c["status"] = status
                c["moderated_at"] = _now_iso()
                _save_topic(slug, data)
                return True, f"Set to {status}."
    return False, "Comment not found."
