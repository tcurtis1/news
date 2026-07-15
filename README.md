# Yoyosup News — Daily Intersection

Meta-aggregator for **https://news.yoyosup.com**

## What’s live

| Feature | Status |
|---------|--------|
| **Curious Pulse** — Google News + Reddit + light HN | Live |
| **Daily Intersection** — Top 10 per platform | Live |
| **Consensus Top 10** — topics on 2+ platforms | Live |
| **Day-over-day deltas** — NEW / ↑ / ↓ vs yesterday | Live |
| **Rank map** — search a term → rank per site | Live |
| **Topic pages** — `/topic/{slug}` ranks + news | Live |
| **Comments** — public thread per topic (no login yet) | Live |
| **Safety** — guidelines + OpenAI/local moderation + report | Live |
| **Polymarket** — top markets by 24h volume | Live |
| Bias badges | Not yet |
| Accounts / full auth | Not yet |

## Platforms (daily cache)

| Platform | Source |
|----------|--------|
| Google | Trends RSS |
| Bing | Popular Now + News RSS |
| YouTube | US daily Top Videos chart |
| X | trends24 US mirror |
| Polymarket | Gamma API `volume24hr` (no key) |
| TikTok | Creative Center hashtags (+ news pad) |
| Facebook | News-buzz proxy (no free Meta top-10 API) |
| Instagram | News-buzz proxy (no free Meta top-10 API) |

Trends refresh **once per UTC day** (`/data/trends_cache.json`).  
Force: `?force=1` on `/api/trends` or `/search`.

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
| `GET /api/trends` | Platforms + `consensus` + `top10` |
| `GET /api/rank?q=` | Rank map for a query |
| `GET /api/search?q=` | Rank map + news hits (empty `q` = full trends) |
| `GET /topic/{slug}` | Topic page (ranks + news + comments) |
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
