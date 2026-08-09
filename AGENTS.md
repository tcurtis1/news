# News — agent notes

**https://news.yoyosup.com/** — Daily Intersection / Pulse / MyNews.

## Cold start

1. **[NETWORK.md](NETWORK.md)** — deploy lock, What’s New, multi-repo habits.  
2. **[README.md](README.md)** — what’s live.  
3. **[ROADMAP.md](ROADMAP.md)** — backlog.  
4. `git pull` before editing.

## Deploy

```bash
DEPLOY_AGENT=grok ./deploy.sh
# or DEPLOY_AGENT=claude ./deploy.sh
```

Server lock: `~/apps/news/.deploy.lock`.

## Conventions

- Calm safety UI: warm off-white, white panels, navy text, blue links, and shared teal `#0f766e` / `#2dd4bf` actions; reserve orange/red for warnings. Keep the shared network header/footer and cross-site theme behavior aligned with tools/finance.
- Geo / trends caching is sensitive — don’t casually change cache keys or cron without reading `README` / app code.  
- Keep comment UX low-friction (optional name, Anonymous OK).

## Analytics measurement (2026-08-08)

Commit `3b33be7` replaced raw-request engagement assumptions with a privacy-friendly measurement layer:

- `app/analytics.py` excludes declared crawlers, headless clients, and common SEO bots before counting page traffic.
- `app/static/analytics.js` records one new/returning session after a 30-minute inactivity window using localStorage timestamps. It stores no visitor ID and sends no topic names or personal data.
- `POST /api/analytics-event` accepts only the event allowlist in `ALLOWED_CLIENT_EVENTS`; keep arbitrary client labels out of stored analytics.
- `actions` in the admin API now means browser-generated meaningful actions, not page views. Daily event totals live in `by_day_event`.
- MyNews records add/remove/clear actions; shared analytics records searches, topic opens, and outbound story clicks.

The pre-2026-08-08 cumulative page totals include known crawler traffic and must not be treated as people. Use new/returning sessions and daily events collected after this release for retention decisions. Keep `tests/test_analytics.py` passing when changing analytics.

## Don’t

- Don’t race money/finance deploys without checking locks.  
- Don’t put hard sell ads in comment boxes.  
- Don’t commit secrets (OpenAI / admin tokens belong in server env).
