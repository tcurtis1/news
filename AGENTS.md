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

- Dark UI, `#f97316`, shared network header/footer pattern with tools/finance.  
- Geo / trends caching is sensitive — don’t casually change cache keys or cron without reading `README` / app code.  
- Keep comment UX low-friction (optional name, Anonymous OK).

## Don’t

- Don’t race money/finance deploys without checking locks.  
- Don’t put hard sell ads in comment boxes.  
- Don’t commit secrets (OpenAI / admin tokens belong in server env).
