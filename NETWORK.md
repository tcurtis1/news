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
3. Match network chrome (Tools / News / Finance nav); shared teal actions `#0f766e` / `#2dd4bf` (orange/red are warning colors).  
4. No signup required for core reading; comments allow Anonymous.  
5. **Small, mechanical, single-file fix?** Try the local LLM first (free, no token cost) before Grok/Claude/Codex — `~/local-llm-setup/scripts/aider-java.sh --message "…" path/to/file.py`, then `git diff` before trusting it. See money `NETWORK.md` rule 9 for the full workflow and escalation criteria.
