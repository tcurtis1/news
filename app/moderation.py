"""
Standards-aligned text moderation for UGC.

Primary: OpenAI Moderations API (shared industry taxonomy).
Fallback: local heuristics when OPENAI_API_KEY is unset or the API fails.

Policy outcomes:
  allow  — publish immediately
  hold   — store but hide pending review (violence/sexual/hate thresholds)
  block  — reject; never publish (e.g. sexual/minors, severe self-harm)
"""

from __future__ import annotations

import logging
import os
import re
from typing import Any

import httpx

log = logging.getLogger("moderation")

OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "").strip()
OPENAI_MODERATION_MODEL = os.environ.get(
    "OPENAI_MODERATION_MODEL", "omni-moderation-latest"
)
# Category score >= this → hold (when not hard-blocked)
HOLD_THRESHOLD = float(os.environ.get("MOD_HOLD_THRESHOLD", "0.65"))

# Always block if true / high score (CSAM-adjacent and severe self-harm intents)
HARD_BLOCK_CATEGORIES = (
    "sexual/minors",
    "self-harm/intent",
    "self-harm/instructions",
)

# Hold for human review when flagged or score high
HOLD_CATEGORIES = (
    "sexual",
    "violence",
    "violence/graphic",
    "hate",
    "hate/threatening",
    "harassment/threatening",
    "illicit/violent",
    "self-harm",
)

# Minimal local fallback (not a substitute for the API)
_LOCAL_BLOCK = re.compile(
    r"\b("
    r"child\s*porn|childporn|cp\s*video|underage\s*sex|sexual\s*content\s*involving\s*minors?"
    r")\b",
    re.I,
)
_LOCAL_HOLD = re.compile(
    r"\b("
    r"rape|behead|gore|dismember|kill\s+yourself|kys\b|nazi\s+gas|"
    r"explicit\s+sex|onlyfans\s+leak"
    r")\b",
    re.I,
)


def _empty_result(action: str, reason: str, source: str) -> dict[str, Any]:
    return {
        "action": action,  # allow | hold | block
        "reason": reason,
        "source": source,  # openai | local | off
        "categories": {},
        "category_scores": {},
        "flagged": action != "allow",
    }


async def moderate_text(text: str) -> dict[str, Any]:
    """
    Moderate free text. Always returns an action decision.
    """
    content = (text or "").strip()
    if not content:
        return _empty_result("allow", "empty", "off")

    if OPENAI_API_KEY:
        try:
            return await _moderate_openai(content)
        except Exception as e:
            log.warning("OpenAI moderation failed, using local fallback: %s", e)

    return _moderate_local(content)


async def _moderate_openai(content: str) -> dict[str, Any]:
    async with httpx.AsyncClient(timeout=12.0) as client:
        r = await client.post(
            "https://api.openai.com/v1/moderations",
            headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "Content-Type": "application/json",
            },
            json={"model": OPENAI_MODERATION_MODEL, "input": content},
        )
        r.raise_for_status()
        data = r.json()
    results = (data.get("results") or [{}])[0]
    categories = {
        k: bool(v) for k, v in (results.get("categories") or {}).items()
    }
    scores = {
        k: float(v) for k, v in (results.get("category_scores") or {}).items()
    }
    flagged = bool(results.get("flagged"))

    # Hard block
    for cat in HARD_BLOCK_CATEGORIES:
        if categories.get(cat) or scores.get(cat, 0) >= 0.2:
            return {
                "action": "block",
                "reason": f"blocked:{cat}",
                "source": "openai",
                "categories": categories,
                "category_scores": scores,
                "flagged": True,
            }

    # Hold if any hold category is true or score high
    for cat in HOLD_CATEGORIES:
        score = scores.get(cat, 0.0)
        if categories.get(cat) or score >= HOLD_THRESHOLD:
            return {
                "action": "hold",
                "reason": f"held:{cat}:{score:.3f}",
                "source": "openai",
                "categories": categories,
                "category_scores": scores,
                "flagged": True,
            }

    if flagged:
        return {
            "action": "hold",
            "reason": "held:flagged",
            "source": "openai",
            "categories": categories,
            "category_scores": scores,
            "flagged": True,
        }

    return {
        "action": "allow",
        "reason": "ok",
        "source": "openai",
        "categories": categories,
        "category_scores": scores,
        "flagged": False,
    }


def _moderate_local(content: str) -> dict[str, Any]:
    if _LOCAL_BLOCK.search(content):
        return _empty_result("block", "blocked:local_policy", "local")
    if _LOCAL_HOLD.search(content):
        return _empty_result("hold", "held:local_policy", "local")
    return _empty_result("allow", "ok", "local")


def moderation_enabled() -> dict[str, Any]:
    return {
        "openai_configured": bool(OPENAI_API_KEY),
        "model": OPENAI_MODERATION_MODEL if OPENAI_API_KEY else None,
        "hold_threshold": HOLD_THRESHOLD,
        "hard_block_categories": list(HARD_BLOCK_CATEGORIES),
        "hold_categories": list(HOLD_CATEGORIES),
    }
