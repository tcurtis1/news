# Yoyosup News — roadmap / todo

Living product list. Ship small; prefer honest UX over flashy features.

## Now / next

### Sports — make `/sports` a glance, then news
Scoreboard is live (ESPN public feed). **Sprints:** [docs/sprints-sports.md](docs/sprints-sports.md). **S0–S5 shipped** (live-first mix, ticking scores, game pages, MyTeams, headlines + Follow, noindex game URLs, local date nav, compact share).

### 1. Bias / political leaning badge — **shipped v1 (0.9.0)**
- Per-hit outlet badge + topic/query coverage aggregate.
- Labels: Lean left · Lean right · Mixed / center · Unclear.
- Method: curated outlet domain/name map in `app/bias.py` (not a truth score).
- **Later:** expand outlet list; optional third-party bias datasets; Pulse story cards.

### 1b. Source preference (Conservative / Balanced / Liberal) — **shipped v1 (0.10.0+)**
- User picks **Conservative · Balanced · Liberal**; saved on device (localStorage + cookie).
- Headlines on Intersection, MyNews, topic pages, and `/api/search` are populated from preferred outlet lists.
- **Conservative** domains from Feedspot’s public list: https://news.feedspot.com/conservative_news_websites/
- **Liberal** from Feedspot liberal political list + progressive news outlets.
- **Balanced** = wire/center + light left/right mix.
- Fetch path: **native outlet RSS** (primary diversity) + Google News `site:` batches
  (throttled — Google 503s if over-parallelized) + filter/boost + source round-robin.
- Conservative Google batches cover the full ~60-domain set; ~38 native RSS feeds scraped.
- Code: `app/source_prefs.py` (`OUTLET_RSS`), `static/lean-pref.js`, `?lean=` + cookie `yoyonews_lean`.

### 2. Comment names: anonymous + remember — **shipped (localStorage)**
- **Live:** optional name → empty becomes **Anonymous**; moderated; reportable.
- **Shipped:** remember preferred display name in localStorage (`comment-name.js`).
- **Todo — register a name (lightweight):** magic-link email so a name is portable across devices.
- **Todo — comments on more surfaces:** “Discuss here” link added on search news hits → topic thread; more entry points still possible from Intersection consensus cards.
- **Always keep:** true anonymous posting (no force signup).
- **Safety:** keep OpenAI/local moderation + report + admin hold queue.

## Later
- Full auth only if needed for trust / anti-abuse.
- AdSense on news once tools monetization is clean.
- Richer rank map + more geo coverage.
- Brand logo on tools + news (SVG) — **shipped**.

## Done (high level)
- Pulse, Daily Intersection, geo, consensus Top 10, deltas, rank map, topic pages, moderated comments, Polymarket, sitemap/robots.
- Bias badges v1, comment name memory, YoyoSup logo.
- Bot-filtered page analytics plus privacy-friendly new/returning sessions and action events (no persistent visitor identifier).
