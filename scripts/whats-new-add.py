#!/usr/bin/env python3
"""Append a What’s New entry for news (shows in network Updates widget)."""
from __future__ import annotations
import argparse, json, re, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
JSON_PATH = ROOT / "app" / "static" / "whats-new.json"
ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--detail", required=True)
    ap.add_argument("--href", default="https://news.yoyosup.com/")
    ap.add_argument("--tags", default="news")
    ap.add_argument("--date", default="")
    ap.add_argument("--seq", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    eid = args.id.strip().lower()
    if not ID_RE.match(eid):
        print("bad id", file=sys.stderr); return 2
    if JSON_PATH.exists():
        data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    else:
        data = {"version": 1, "site": "news", "entries": []}
    entries = data.setdefault("entries", [])
    if any(isinstance(e, dict) and e.get("id") == eid for e in entries):
        print(f"id exists: {eid}", file=sys.stderr); return 2
    seqs = [int(e.get("seq") or 0) for e in entries if isinstance(e, dict)]
    seq = args.seq if args.seq > 0 else (max(seqs) + 1 if seqs else 1)
    entry = {
        "id": eid, "seq": seq,
        "date": args.date.strip() or date.today().isoformat(),
        "title": args.title.strip(), "detail": args.detail.strip(),
        "href": args.href.strip(),
        "tags": [t.strip() for t in args.tags.split(",") if t.strip()] or ["news"],
        "site": "news",
    }
    data["entries"] = [entry] + entries
    data["site"] = "news"
    text = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    if args.dry_run:
        print(text); return 0
    JSON_PATH.write_text(text, encoding="utf-8")
    print(f"OK {JSON_PATH} id={eid} seq={seq}")
    return 0
if __name__ == "__main__":
    raise SystemExit(main())
