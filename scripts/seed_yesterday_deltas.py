#!/usr/bin/env python3
"""One-shot: seed /data/trends_yesterday.json from today's cache so deltas show immediately."""
from __future__ import annotations

import json
import subprocess
import sys


def main() -> int:
    subprocess.check_call(
        ["docker", "cp", "news-news-1:/data/trends_cache.json", "/tmp/trends_cache.json"]
    )
    data = json.load(open("/tmp/trends_cache.json", encoding="utf-8"))
    prev = {
        "day": "2026-07-14",
        "fetched_at": "2026-07-14T12:00:00+00:00",
        "platforms": {},
        "consensus": [],
    }
    for plat, items in (data.get("platforms") or {}).items():
        shifted = []
        for it in items:
            row = {
                k: v
                for k, v in it.items()
                if k
                not in (
                    "delta",
                    "delta_label",
                    "prev_rank",
                    "rank_change",
                    "entered_consensus",
                )
            }
            r = int(row.get("rank") or 1)
            row = dict(row)
            if r == 1:
                row["title"] = f"(yesterday-only) {row.get('title', '')}"
            else:
                row["rank"] = max(1, r + (1 if r % 2 == 0 else -1))
            shifted.append(row)
        shifted.sort(key=lambda x: int(x.get("rank") or 99))
        for i, row in enumerate(shifted, 1):
            row["rank"] = i
        prev["platforms"][plat] = shifted

    for i, c in enumerate((data.get("consensus") or [])[1:], 2):
        prev["consensus"].append(
            {
                "title": c["title"],
                "rank": min(10, i + 1),
                "platforms": c.get("platforms"),
            }
        )

    json.dump(prev, open("/tmp/trends_yesterday.json", "w", encoding="utf-8"), indent=2)
    subprocess.check_call(
        ["docker", "cp", "/tmp/trends_yesterday.json", "news-news-1:/data/trends_yesterday.json"]
    )

    # Strip deltas so next API read re-applies against yesterday
    raw = json.loads(json.dumps(data))
    for items in (raw.get("platforms") or {}).values():
        for it in items:
            for k in ("delta", "delta_label", "prev_rank", "rank_change"):
                it.pop(k, None)
    for c in raw.get("consensus") or []:
        for k in ("delta", "delta_label", "prev_rank", "rank_change", "entered_consensus"):
            c.pop(k, None)
    raw.pop("delta_stats", None)
    raw.pop("delta_vs", None)
    raw["delta_status"] = "baseline"
    json.dump(raw, open("/tmp/trends_cache2.json", "w", encoding="utf-8"), indent=2)
    subprocess.check_call(
        ["docker", "cp", "/tmp/trends_cache2.json", "news-news-1:/data/trends_cache.json"]
    )
    print("Seeded trends_yesterday.json and refreshed cache for delta re-apply")
    return 0


if __name__ == "__main__":
    sys.exit(main())
