# yoyosup network — agent bootstrap (news copy)

**Canonical process doc:** keep in sync with **`money/NETWORK.md`** (Tools repo). If they differ, trust **money** and update this copy.

This repo: **news** → https://news.yoyosup.com/  
Permanent siblings: `~/work/money` (tools + process), `~/work/finance`, `~/work/convert` (image.yoyosup.com).  
Anything else on yoyosup is temporary.

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

Visitor-visible news ships get an entry in **this** repo: `app/static/whats-new.json` via `python3 scripts/whats-new-add.py …`. The Tools Updates widget merges it. Do not add the entry only on money. Never admin/operator, schema-only, or one-off preview entries.

## Product map (news)

See **README.md** / **ROADMAP.md** for Pulse, Intersection, MyNews, bias badges, comments, geo, etc.

## Network rules (summary)

1. **`git pull` first** — that is the real lock across machines. Commit small/often.
2. **One deploy after the group is done, and only if Tony asked.** Agents do not ship on their own. On tools: `TONY_SAID_SHIP=1 DEPLOY_AGENT=grok ./deploy.sh` after he asks; otherwise `offer-ship`. Respect `~/apps/news/.deploy.lock`.
3. Match network chrome (Tools / News / Finance nav); shared teal actions `#0f766e` / `#2dd4bf` (orange/red are warning colors).
4. No signup required for core reading; comments allow Anonymous.
5. Idle? Do not invent a product. Prefer improving an existing page. On tools, `./scripts/gsc.sh report --days 90`.
6. Do not add agent-chat / idle-watcher coordination. Do not touch AdSense/Impact plumbing while those reviews are pending.
7. **Small, mechanical, single-file fix?** Try the local LLM first (free, no token cost) before Grok/Claude/Codex — `~/local-llm-setup/scripts/aider-java.sh --message "…" path/to/file.py`, then `git diff` before trusting it. See money `NETWORK.md` for the full workflow.
