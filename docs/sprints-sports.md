# Sports sprints — make `/sports` compelling

Live: https://news.yoyosup.com/sports  
Code: `app/sports.py`, `app/main.py` (`_sports_board`, `_sports_event_view`), `app/templates/sports.html`, `sports_game.html`, `app/static/sports.js`

**Thesis:** glanceable scores, then a door into News. Free, no signup, mobile-first, plain English. Not an ESPN clone.

Three agents (product, UX, content) reviewed the live board on 2026-09-01. They agreed: the scoreboard works as a proxy; the **mix, clocks, live updates, and “news” claim** are what make it feel empty.

## What we saw on the live page

- Title: “Sports scores **and news**.” Page: scores only.
- **One** live MLB game on Top (Giants–Pirates, extra innings). `/sports/mlb` had **~10 live** games the same night. Cause: `get_sports_home_summary` takes ESPN’s **first 5** events per league (`res.data[:5]`), and ESPN lists early finals first, so later live games never make Top.
- Then Aug 29 CFB finals, then CFB/NFL/WNBA/NBA/CBB **days to months** out, including November CBB TBDs. One flat “Top games” grid.
- First paint times are **UTC** (“Sep 1, 10:40 PM UTC”) while copy says local. Scheduled cards also keep ESPN Eastern (`9/3 - 8:00 PM EDT`) — two clocks.
- Live is a 1px teal border. Easy to miss on a phone.
- `sports.js` only applies `games[0]` on poll. Home HTML is ESPN `current`; home poll injects **UTC today** — different cache keys. Tapping the live card can show a **stale game package** (score/inning behind the board).
- Game page often renders empty “Batting — —” tables. `winner` and `tv_broadcasters` are parsed on `Event` then **dropped** in `_sports_event_view`.
- Sports is not on `ROADMAP.md` / README “what’s live.” Nav still treats it like a peer of Top Stories.

## What a phone fan wants in 5 seconds

1. Who is **live** (score + inning/quarter/clock).
2. What **just ended tonight**.
3. What’s **next tonight**, one local time, TV if we already have it.
4. Optionally: three honest headlines, or pin my teams (device only).

They do not want odds, video, fantasy, injury databases, LLM recaps, or more leagues until Top is not a landfill.

## Do not build

| Temptation | Why not |
|------------|---------|
| ESPN recap / `news.articles` reprint | Editorial + ToS; we already have Google News search |
| Per-game SEO landings | Thin URL farm; sitemap already omits `/sports/game/*` |
| Odds / DraftKings from JSON | Present in ESPN payload; off-thesis |
| Streaks / “2 GB in the WC” | Not in fields we parse today |
| Video, Watch, fantasy, more leagues | Kitchen sink |
| Sports subdomain or a second engine | Extend `/sports` |

## Sequence

**S0 → S1 are mandatory.** S2 repairs the tap. S3–S4 are the first yoyosup-shaped layers (MyTeams, real news). S5 is hygiene + share. Do not start S4/S5 while Top still buries live baseball under November CBB.

---

## S0 — Top is a glance, not a landfill — **shipped 2026-09-01**

**Goal:** A US-evening phone load answers who’s playing / who won / what’s next in one screen.

**User-visible**
- `/sports` sections: **Live** / **Final** / **Upcoming** (skip empty sections).
- Home mix: **all** live/halftime across leagues (no `[:5]` truncation), then today’s finals (cap), then scheduled in the next ~36–48 hours (cap). Drop NBA/CBB/NHL from Top when they have nothing in that window.
- Live pill + period/clock. Hide first-pitch time on live/final cards. Winner weight on finals. Shrink or hide “—” scores on unplayed games.
- One local clock on first paint (same anti-flash idea as theme). Scheduled status is local from `start_time_iso`, not ESPN `9/3 - 8:00 PM EDT` plus UTC.
- Title/copy say **scores** until news actually ships (S4).
- “All scores” is a real today-in-local-date slate (`/sports/scores`) or it is demoted to a text link.

**Out of scope:** Headlines, favorites, game-center rewrite, new leagues.

**Effort:** M — `get_sports_home_summary` / `_sports_board`, `sports.html` sections, time helper, tests that live games are not truncated to ESPN’s first 5.

**What’s New:** yes (visitor sees a different board).

---

## S1 — Live scores that actually move — **shipped 2026-09-01**

**Goal:** If a card is live, the number changes. Tapping it does not rewind the game.

**User-visible**
- Every live card on a board ticks (loop all `payload.events` by `data-game-id`).
- Home poll uses the **same** ESPN `current` cache key as SSR (stop forcing UTC `date=` on `/api/sports/scoreboard` when omitted).
- Game hero score/status matches the board (prefer scoreboard payload for score/clock if the game package lags).
- Fallback/stale labeled honestly. One empty notice, not “unavailable” **and** “no games.”

**Out of scope:** Play-by-play, sub-30s poll, new providers.

**Effort:** S/M — `sports.js`, API date default, league boards live-first sort.

**What’s New:** only if a visitor would notice live scores ticking who already used the page.

---

## S2 — Game page worth one tap — **shipped 2026-09-01**

**Goal:** Tap a live/final game, see score, situation, real scoring/leaders if ESPN sent them, TV. Omit empty panels.

**User-visible**
- No fake “Batting — —” rows (filter boxscore **group headers**).
- Show `tv_broadcasters` and `winner` (already on `Event`).
- Honest empty: “No scoring plays in this feed yet,” not a hollow table.
- Optional: outs/count only if present on the competition object.

**Out of scope:** Recaps, shot charts, video, injury report.

**Effort:** M — need fixtures from a real live `gamepackageJSON`, not header-only mocks.

**What’s New:** yes if the game page stops looking empty.

---

## S3 — Context chips + MyTeams (device only)

**Goal:** Second visit is personal; first visit has a one-line “why this card” **only when ESPN already sent a string**.

**User-visible**
- Muted line when present: records (`83-55`), `notes[].headline`, week number, CFB/CBB rank if not 99, weather if present. **No** invented streaks/playoff math. **No** odds.
- Star a team → `localStorage` (MyNews pattern). Top pins those matchups. No account, no push.

**Out of scope:** Sync, login, follow player, standings product.

**Effort:** M — extend `Event` + `_sports_event_view`; client chips like `mynews.js`.

**What’s New:** yes for the star/pin if it is visible on Top.

---

## S4 — News, finally, and only as News

**Goal:** Stop lying about “sports news” by **linking into** search/topic/MyNews. Do not mint recaps.

**User-visible**
- Game page: “News: [Away] · [Home]” → `/search?q=` with **full** `displayName` (not `Giants`). Optional 3 headlines via `/api/search?lite=1&days=1`, fail open, do not block score SSR.
- League page: 6–8 Google News RSS headlines (same client as Pulse), outbound to the publisher. “More” → `/search?q=NFL`.
- “Follow {team} in MyNews” writes one chip, links `/my`.
- Rank-map chip only if the **full team name** is actually in today’s Intersection tops. No chip otherwise.

**Out of scope:** ESPN `Recap` / `HeadlineNews` scrape, `/sports/news` index, per-game topic URLs, LLM blurbs.

**Effort:** M — reuse `run_search` / `pulse._fetch_google_news_top`; cache like Pulse (~30 min).

**What’s New:** yes (visitor sees headlines). Then the page title can say scores and news.

---

## S5 — Index hygiene + share (after S0–S1)

**Goal:** Game URLs stay useful boxes, not a farm. A score is forwardable.

**User-visible**
- Almost none for robots: `noindex` `/sports/game/*`; keep `/sports` and `/sports/{league}` indexed (`seo.py` already omits games from the sitemap; `base.html` still `index,follow`s every `/sports*` path).
- Compact share of `SF 12 @ PIT 12, Bot 8th` (SMS / Web Share), after the board is trustworthy.
- Date nav in **local** calendar date, not UTC (US evening is tomorrow in UTC).

**Out of scope:** OG image factory, notifications, widgets.

**Effort:** S–M.

**What’s New:** share only, if a visitor can tap it.

---

## Implementation notes

| File | Why |
|------|-----|
| `app/sports.py` | Home mix, parse records/notes/week/rank/TV, cache keys |
| `app/main.py` | Board grouping, UTC dates, event view dropping winner/TV, poll URL vs SSR |
| `app/templates/sports.html` | Live/Final/Upcoming, copy/title |
| `app/static/sports.js` | Update **all** cards; first-paint local time |
| `app/templates/sports_game.html` | Hide empty panels |
| `app/seo.py` + `base.html` | Game `noindex` |
| `tests/test_sports.py` / `test_sports_routes.py` | Live-not-truncated; poll updates every id |

Analytics: sports currently has **zero** `ALLOWED_CLIENT_EVENTS`. Add `sports_view` / `sports_team_star` only when S3 ships, if News analytics allowlist is the right place.

## Anti-dev-circle

1. Equivalent exists: extend `/sports` + existing search/topic/MyNews. Do not add `/scores` on a new host.
2. Evidence: live board 2026-09-01 (one live on Top vs ~10 on `/sports/mlb`) plus poll/cache bugs in `sports.js` / `_sports_board`. Not a GSC guess.
3. Invariants: free, no signup, teal, no ads on the board, no per-game SEO farm.
