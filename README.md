# Yoyosup News — Daily Intersection

Meta-aggregator for **https://news.yoyosup.com**

**Agents (cold start):** [NETWORK.md](NETWORK.md) → [AGENTS.md](AGENTS.md) → this README / [ROADMAP.md](ROADMAP.md).

**Deploy:** `DEPLOY_AGENT=grok ./deploy.sh` (or `claude` / `tony`) — remote deploy lock; see NETWORK.md.

## What’s live

| Feature | Status |
|---------|--------|
| **Curious Pulse** — Google News + Reddit + light HN | Live |
| **Daily Intersection** — Top 10 per platform | Live |
| **Location** — country + U.S. state (`?geo=`) | Live |
| **Consensus Top 10** — topics on 2+ platforms | Live |
| **Day-over-day deltas** — NEW / ↑ / ↓ vs yesterday | Live |
| **Rank map** — search a term → rank per site | Live |
| **Topic pages** — `/topic/{slug}` ranks + news | Live |
| **Comments** — public thread per topic; optional name or **Anonymous** (no login) | Live |
| **Comment name memory** — localStorage preferred display name | Live |
| **Bias / lean badges** — Lean left · right · mixed · unclear (outlet map) | Live |
| **Safety** — guidelines + OpenAI/local moderation + report | Live |
| **Polymarket** — top markets by 24h volume | Live |
| **YoyoSup logo** — SVG wordmark + mark | Live |
| **MyNews** — personal topic board (`/my`, localStorage, no auth) | Live |

## Feature backlog (product todo)

| Priority | Item | Notes |
|----------|------|--------|
| ~~P1~~ | ~~Bias / leaning badge~~ | **Shipped v1** — `app/bias.py`; expand outlet list over time |
| ~~P1a~~ | ~~Remember comment name~~ | **Shipped** — `static/comment-name.js` |
| **P1b** | **Register a name** | Lightweight account (magic-link) so name is portable; keep anonymous |
| **P1c** | **Discuss from Intersection cards** | Search hits already link “Discuss here”; wire consensus rows too |
| P2 | Accounts / full auth | Only if registered names need verification, moderation trust, or rate-limit relief. |
| P2 | News AdSense / monetization | After tools site ads are stable; avoid hard ads in comment boxes. |
| P3 | Outlet bias sources | Wire public media-bias datasets for broader provenance. |

## Platforms (daily cache)

| Platform | Source | Geo |
|----------|--------|-----|
| Google | Trends RSS | Country + U.S. state (`US-UT`) |
| Bing | Popular Now + News RSS | Country market; **states → local Bing News** |
| YouTube | Daily Top Videos chart | Country (no free state chart) |
| X | trends24 mirror | Country (no free state chart) |
| Polymarket | Gamma API `volume24hr` (no key) | Always global |
| TikTok | Creative Center hashtags (+ news pad) | Country; **states → local TikTok news buzz** |
| Facebook | News-buzz proxy (no free Meta top-10 API) | Country; **states → local news buzz** |
| Instagram | News-buzz proxy (no free Meta top-10 API) | Country; **states → local news buzz** |

Trends refresh **once per UTC day per place** (`/data/trends/{GEO}.json`).  
Default geo (`TRENDS_GEO`, usually `US`) is warmed by cron; other places lazy-load on first visit.  
Force: `?force=1&geo=US-UT` on `/api/trends` or `/search`.

## Local

```bash
docker compose up --build
# http://127.0.0.1:3010/search
```

## Deploy (Mac → basement)

```bash
./deploy.sh
```

Installs app on `tony@192.168.1.44:~/apps/news` (port **3010**) and a **06:00 America/Denver** cron to warm trends.

## API

| Endpoint | Notes |
|----------|--------|
| `GET /api/pulse` | Story consensus (`?force=1`) |
| `GET /api/trends` | Platforms + `consensus` + `top10` (`?geo=US` / `US-UT` / `GB`) |
| `GET /api/places` | Country + U.S. state catalog for the picker |
| `GET /api/rank?q=` | Rank map for a query (`?geo=`) |
| `GET /api/search?q=` | Rank map + news hits (empty `q` = full trends; `?geo=`) |
| `GET /search?geo=` | Intersection UI with location control |
| `GET /topic/{slug}` | Topic page (ranks + news + comments; `?geo=`) |
| `POST /topic/{slug}/comments` | Add comment (`name`, `body`) — moderated |
| `POST /topic/{slug}/comments/{id}/report` | Report a comment |
| `GET /safety` | Community guidelines |
| `GET /robots.txt` | Crawl rules + sitemap pointer |
| `GET /sitemap.xml` | Core pages + consensus/topic URLs |
| `GET /admin/mod?token=` | Held/reported queue (`MOD_ADMIN_TOKEN`) |
| `GET /api/topic/{slug}` | Topic JSON |

### Search engines

After deploy, submit in consoles:

- Sitemap: `https://news.yoyosup.com/sitemap.xml`
- Robots: `https://news.yoyosup.com/robots.txt`

## Safety / moderation

Comments are checked with a **standards-based** pipeline:

1. **OpenAI Moderations API** when `OPENAI_API_KEY` is set (recommended).  
2. **Local fallback** heuristics if the key is missing or the API fails.  
3. Outcomes: **block** (reject), **hold** (saved, not public), **publish**.  
4. Users can **Report**; 2+ reports auto-holds a published comment.  
5. Admins review at `/admin/mod?token=…` with `MOD_ADMIN_TOKEN`.

Hard blocks include categories like `sexual/minors` and severe self-harm intent.  
Hold covers high sexual/violence/hate scores (see `MOD_HOLD_THRESHOLD`).

```bash
# 1) Copy template (already have .env on this repo — or: cp .env.example .env)
# 2) Edit .env — paste OPENAI_API_KEY=sk-...
# 3) Deploy (syncs .env to basement)
./deploy.sh

# Admin queue:
# https://news.yoyosup.com/admin/mod?token=<MOD_ADMIN_TOKEN from .env>
```

See [`.env.example`](.env.example) for all variables.
