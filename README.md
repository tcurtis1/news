# Yoyosup News — Daily Intersection

Meta-aggregator for **https://news.yoyosup.com**

## What’s live

| Feature | Status |
|---------|--------|
| **Pulse** — HN + Reddit consensus feed | Live |
| **Daily Intersection** — Top 10 per platform | Live |
| **Consensus Top 10** — topics on 2+ platforms | Live |
| **Rank map** — search a term → rank per site | Live |
| **Polymarket** — top markets by 24h volume | Live |
| Bias badges | Not yet |
| Topic comments (Fark-style) | Not yet |

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
