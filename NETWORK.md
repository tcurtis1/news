# yoyosup network — agent bootstrap (news copy)

**Canonical process doc:** keep in sync with **`money/NETWORK.md`** (Tools repo). If they differ, trust **money** and update this copy.

This repo: **news** → https://news.yoyosup.com/  
Sibling: `~/work/money` (process + What’s New), `~/work/finance`

---

## Deploy (always label the agent)

```bash
cd ~/work/news
git pull
DEPLOY_AGENT=grok ./deploy.sh    # or claude / tony
```

- Remote lock: `~/apps/news/.deploy.lock` (~10 min).  
- Source of truth is this git repo, not only `~/apps/news`.

## What’s New

News has no standalone Tools-style Updates panel. If a news change should show on the **tools** network feed, add money `hub/whats-new.json` with `https://news.yoyosup.com/…` (money `AGENTS.md` rule 8).

## Product map (news)

See **README.md** / **ROADMAP.md** for Pulse, Intersection, MyNews, bias badges, comments, geo, etc.

## Network rules (summary)

1. `git pull` first; commit small/often.  
2. `DEPLOY_AGENT=… ./deploy.sh` — respect deploy locks.  
3. Match network chrome (Tools / News / Finance nav); accent `#f97316`.  
4. No signup required for core reading; comments allow Anonymous.
