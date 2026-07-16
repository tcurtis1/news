# Yoyosup News — roadmap / todo

Living product list. Ship small; prefer honest UX over flashy features.

## Now / next

### 1. Bias / political leaning badge — **shipped v1 (0.9.0)**
- Per-hit outlet badge + topic/query coverage aggregate.
- Labels: Lean left · Lean right · Mixed / center · Unclear.
- Method: curated outlet domain/name map in `app/bias.py` (not a truth score).
- **Later:** expand outlet list; optional third-party bias datasets; Pulse story cards.

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
