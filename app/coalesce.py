"""In-process single-flight coalescing for async fetches.

pulse.py / trends.py / search.py each hand-roll their own read/fetch/write
cache (in-memory tuple + a file cache in pulse/trends). None of them protect
against concurrent misses: once a cache entry goes stale, every request that
lands before the first refetch finishes independently starts its own
upstream call (RSS/Reddit/Trends scrape) instead of sharing one -- the same
"cache stampede" bug fixed in the finance repo's app/cache.get_or_fetch().

This is the minimal version for news: it only coalesces concurrent calls to
an async function by key, and leaves each module's own cache read/write/
fallback logic untouched. Wrap just the actual network fetch, not the whole
read-cache-then-fetch-then-write function, so each module keeps its own
memory/file cache and stale-on-error handling exactly as it is.

Single-process-safe only (this app runs one uvicorn worker, no threads) --
a plain dict of Tasks is enough, no cross-process lock needed.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

_inflight: dict[str, asyncio.Task] = {}


async def coalesced(key: str, fetch_fn: Callable[[], Awaitable[Any]]) -> Any:
    """Run fetch_fn() exactly once for `key`, even if several callers race
    for it at the same time.

    The fetch runs as an independent asyncio.Task, and every caller awaits
    it through asyncio.shield() -- several call sites here wrap their own
    call in asyncio.wait_for(budget) with *different* per-caller budgets
    (see search.py's _pull_preferred_pool). Without shield, the first
    caller's timeout cancelling its own wait would cancel the shared task
    out from under a different, still-waiting caller. With shield, one
    caller giving up only cancels its own wait; the underlying fetch keeps
    running (and still populates the module's own cache on success) for
    whoever else is still waiting on it.
    """
    task = _inflight.get(key)
    if task is None:
        task = asyncio.ensure_future(fetch_fn())
        _inflight[key] = task

        def _cleanup(t: asyncio.Task, _key: str = key) -> None:
            if _inflight.get(_key) is t:
                _inflight.pop(_key, None)

        task.add_done_callback(_cleanup)

    return await asyncio.shield(task)
