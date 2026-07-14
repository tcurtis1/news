# Yoyosup News (Pulse MVP)

Meta-aggregator for **https://news.yoyosup.com**

## MVP status

| Feature | Status |
|---------|--------|
| Daily **Pulse** (top ~20 from free feeds + consensus) | **Live** |
| Meta search | Placeholder |
| Bias badges | Not yet |
| Comments | Not yet |

## Stack

- FastAPI + Jinja2 + HTMX-ready HTML
- Docker on host port **3010** → Cloudflare tunnel `news.yoyosup.com`
- Sources: Hacker News + Reddit (`news`, `worldnews`, `technology`)

## Local

```bash
docker compose up --build
# http://127.0.0.1:3010/
```

## Deploy (on home server)

```bash
./deploy.sh
```

Deploys to `~/apps/news`, publishes on **3010**.

## Cloudflare

- DNS: `news` CNAME → `<tunnel-id>.cfargotunnel.com` (proxied)
- Tunnel Public Hostname: `news.yoyosup.com` → `http://localhost:3010`
# news
# news
