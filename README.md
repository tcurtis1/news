# Yoyosup News (Pulse + Meta Search)

Meta-aggregator for **https://news.yoyosup.com**

## MVP status

| Feature | Status |
|---------|--------|
| Daily **Pulse** (HN + Reddit consensus) | **Live** |
| **Meta search** (Google News + Bing News + HN + portals) | **Live** |
| **Daily platform trends** (Google · Bing · YouTube · X) | **Live** |
| Bias badges | Not yet |
| Comments | Not yet |

## Daily platform trends (recommended cadence)

Trends from Google, Bing, YouTube, and X are pulled **once per UTC day** and cached under `/data/trends_cache.json`.

| Platform | Source (no API keys) |
|----------|----------------------|
| **Google** | [Trends RSS](https://trends.google.com/trending/rss?geo=US) — daily search trends + related news |
| **Bing** | Homepage **Popular Now** suggestions + Bing News RSS |
| **YouTube** | [charts.youtube.com](https://charts.youtube.com) US **daily Top Videos** |
| **X** | US trends mirrored on [trends24.in](https://trends24.in/united-states/) |

**Why once a day?** These lists move slowly enough that a daily snapshot is useful, avoids rate limits / blocks, and keeps the basement box polite to third parties. Force a re-pull with `?force=1` on `/api/trends` or `/search`.

## Stack

- FastAPI + Jinja2
- Docker on host port **3010** → Cloudflare tunnel `news.yoyosup.com`

## Local

```bash
docker compose up --build
# http://127.0.0.1:3010/
# http://127.0.0.1:3010/search   ← daily platform trends
```

## Deploy (from Mac → basement)

```bash
./deploy.sh
# optional: ./deploy.sh tony@192.168.1.44
```

Deploy also installs a **host cron** (06:00 America/Denver) that runs
`scripts/warm-trends.sh` → `GET /api/trends?force=1` so the daily cache is
warm before anyone hits the site. Log: `~/apps/news/logs/warm-trends.log`.

## API

| Endpoint | Notes |
|----------|--------|
| `GET /api/pulse` | Consensus list (`?force=1` refresh ~30m cache) |
| `GET /api/trends` | Daily Google/Bing/YouTube/X (`?force=1` re-pull) |
| `GET /api/search?q=` | Meta search; empty `q` returns trends payload |

## Cloudflare

- DNS: `news` CNAME → tunnel (proxied)
- Public Hostname: `news.yoyosup.com` → `http://localhost:3010`
