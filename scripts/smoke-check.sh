#!/usr/bin/env bash
# News post-deploy smoke checks — catch 500s / broken pages before/after deploy.
#
# Usage:
#   ./scripts/smoke-check.sh              # http against local :3010
#   ./scripts/smoke-check.sh [BASE]       # http against BASE
#
# Also probes preferred-source filtering for many popular topics × leans
# (see scripts/smoke-topics.txt). Skip with SMOKE_SKIP_TOPICS=1.
#
# Exit 0 = all pass; non-zero = failures printed.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BASE="${1:-http://127.0.0.1:3010}"
if [[ "${SMOKE_BASE:-}" != "" ]]; then
  BASE="$SMOKE_BASE"
fi
BASE="${BASE%/}"
SMOKE_SKIP_TOPICS="${SMOKE_SKIP_TOPICS:-0}"
TOPICS_FILE="${SMOKE_TOPICS_FILE:-${ROOT}/scripts/smoke-topics.txt}"

FAIL=0
PASS=0
WARN=0

pass() { PASS=$((PASS + 1)); echo "  PASS  $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL  $*"; }
warn() { WARN=$((WARN + 1)); echo "  WARN  $*"; }

http_code() {
  curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 25 \
    -L --max-redirs 5 "$1" 2>/dev/null || echo "000"
}

http_body() {
  local t="${2:-25}"
  curl -sS --connect-timeout 5 --max-time "$t" -L --max-redirs 5 "$1" 2>/dev/null || true
}

# Any HTML response containing these has crashed server-side, regardless of
# status code reported (belt-and-suspenders alongside the code check itself).
check_clean_200() {
  local path="$1"
  local url="${BASE}${path}"
  local body code
  body="$(http_body "$url")"
  code="$(http_code "$url")"
  if [[ "$code" != "200" ]]; then
    fail "HTTP $code $path"
    return
  fi
  if grep -qE "Internal Server Error|Traceback \(most recent call last\)|jinja2\.exceptions" <<< "$body"; then
    fail "HTTP $code $path but body looks like a crash page"
    return
  fi
  pass "HTTP $code $path"
  REPLY_BODY="$body"
}

json_hit_count() {
  # stdin = JSON body from /api/search or /api/headlines
  python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print(-1)
    raise SystemExit(0)
hits = d.get("hits") or d.get("stories") or []
if not isinstance(hits, list):
    print(-1)
else:
    print(len(hits))
' 2>/dev/null || echo -1
}

json_field() {
  local key="$1"
  python3 -c '
import json, sys
key = sys.argv[1]
try:
    d = json.load(sys.stdin)
except Exception:
    print("")
    raise SystemExit(0)
v = d.get(key)
if v is None:
    print("")
elif isinstance(v, bool):
    print("true" if v else "false")
else:
    print(v)
' "$key" 2>/dev/null || true
}

# Warm preferred pools once so topic cards share cache (avoids timeout flakiness).
warm_preferred_pools() {
  local lean body n
  echo ""
  echo "=== Warm preferred-source pools ==="
  for lean in balanced conservative liberal; do
    body="$(http_body "${BASE}/api/headlines?lean=${lean}&offset=0&limit=20" 45)"
    n="$(printf '%s' "$body" | json_hit_count)"
    if [[ "$n" -lt 1 ]]; then
      fail "warm headlines lean=${lean} returned ${n} hits (need ≥1)"
    else
      pass "warm headlines lean=${lean} → ${n} hits (page)"
    fi
  done
  # Unfiltered / default lean path
  body="$(http_body "${BASE}/api/headlines?offset=0&limit=20" 30)"
  n="$(printf '%s' "$body" | json_hit_count)"
  if [[ "$n" -lt 1 ]]; then
    fail "warm headlines (default lean) returned ${n} hits"
  else
    pass "warm headlines default lean → ${n} hits"
  fi
}

# Matrix: popular topics × source leans (MyNews lite path).
check_topic_filter_matrix() {
  local topics=()
  local line t lean body n mode empty_ok=0
  local leans=("" "balanced" "conservative" "liberal")
  local checked=0
  local empty_fail=0
  local bad_json=0

  if [[ ! -f "$TOPICS_FILE" ]]; then
    fail "topics file missing: $TOPICS_FILE"
    return
  fi
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    [[ -z "$line" ]] && continue
    topics+=("$line")
  done < "$TOPICS_FILE"

  if [[ "${#topics[@]}" -lt 10 ]]; then
    fail "topics file has only ${#topics[@]} topics (want ≥10 popular chips)"
    return
  fi

  echo ""
  echo "=== Topic × lean filter matrix (${#topics[@]} topics × ${#leans[@]} leans) ==="

  for t in "${topics[@]}"; do
    for lean in "${leans[@]}"; do
      local qpath
      qpath="/api/search?q=$(python3 -c 'import urllib.parse,sys; print(urllib.parse.quote(sys.argv[1]))' "$t")&lite=1&geo=US"
      if [[ -n "$lean" ]]; then
        qpath="${qpath}&lean=${lean}"
      fi
      body="$(http_body "${BASE}${qpath}" 35)"
      n="$(printf '%s' "$body" | json_hit_count)"
      mode="$(printf '%s' "$body" | json_field mode)"
      checked=$((checked + 1))
      if [[ "$n" -lt 0 ]]; then
        fail "topic=\"${t}\" lean=${lean:-default} bad JSON"
        bad_json=$((bad_json + 1))
        continue
      fi
      if [[ "$n" -lt 1 ]]; then
        # A niche topic (Tesla, NASA, ...) genuinely having no hits in the
        # current general-interest pool is expected/honest now that MyNews
        # chips no longer pad with unrelated headlines (fixed 2026-08-16 —
        # see source_prefs.prefer_topical). Only the aggregate rate below
        # gates the deploy; per-combo emptiness is informational.
        warn "topic=\"${t}\" lean=${lean:-default} → 0 preferred-source hits (mode=${mode:-?})"
        empty_fail=$((empty_fail + 1))
        continue
      fi
      # Quiet pass — matrix is large; only print first few samples + totals
      if [[ "$checked" -le 6 || "$n" -ge 8 ]]; then
        pass "topic=${t} lean=${lean:-default} → ${n} hits"
      else
        PASS=$((PASS + 1))
      fi
    done
  done

  echo ""
  echo "  … matrix checked ${checked} topic×lean combos (${#topics[@]} topics)"
  if [[ "$bad_json" -gt 0 ]]; then
    fail "${bad_json}/${checked} topic×lean combos returned bad JSON (real API error)"
  fi
  # Zero-hit combos are expected now for niche topics (no more padding with
  # unrelated headlines — see 2026-08-16 fix). Only fail the aggregate if
  # the empty rate is extreme enough to suggest the pool fetch itself broke
  # (e.g. even Politics/Trump/Economy come back empty), not normal sparsity
  # in a small general-interest RSS pool.
  local empty_pct=$((empty_fail * 100 / checked))
  if [[ "$empty_fail" -eq 0 ]]; then
    pass "all topic×lean combos returned ≥1 preferred-source hit"
  elif [[ "$empty_pct" -ge 90 ]]; then
    fail "${empty_fail}/${checked} (${empty_pct}%) topic×lean combos returned zero hits — pool may have failed to fetch"
  else
    warn "${empty_fail}/${checked} (${empty_pct}%) topic×lean combos returned zero hits (niche topics, small pool — expected; watch the trend, not any single deploy)"
  fi

  # Explicit filter vs non-filter sanity: same topic, different leans should both work
  body="$(http_body "${BASE}/api/search?q=Politics&lite=1&geo=US&lean=conservative" 30)"
  local nc nb
  nc="$(printf '%s' "$body" | json_hit_count)"
  body="$(http_body "${BASE}/api/search?q=Politics&lite=1&geo=US&lean=balanced" 30)"
  nb="$(printf '%s' "$body" | json_hit_count)"
  if [[ "$nc" -ge 1 && "$nb" -ge 1 ]]; then
    pass "Politics works filtered (conservative=${nc}) and balanced (${nb})"
  else
    fail "Politics filter comparison failed cons=${nc} balanced=${nb}"
  fi

  # Headlines pagination (no topic) still works for each lean
  for lean in balanced conservative liberal; do
    body="$(http_body "${BASE}/api/headlines?lean=${lean}&offset=0&limit=20" 25)"
    n="$(printf '%s' "$body" | json_hit_count)"
    local more
    more="$(printf '%s' "$body" | json_field has_more)"
    if [[ "$n" -ge 1 ]]; then
      pass "headlines lean=${lean} page0=${n} has_more=${more}"
    else
      fail "headlines lean=${lean} empty page"
    fi
  done
}

echo ""
echo "=== News HTTP smoke (${BASE}) ==="

probe="$(http_code "${BASE}/health")"
if [[ "$probe" != "200" ]]; then
  fail "cannot reach ${BASE}/health (HTTP $probe) — is the stack up?"
  echo ""
  echo "=== Summary: ${PASS} pass · ${WARN} warn · ${FAIL} fail ==="
  exit 1
fi
pass "health reachable ($probe)"

check_clean_200 "/"
check_clean_200 "/search"
check_clean_200 "/search?q=news"
if [[ -n "${REPLY_BODY:-}" ]] && grep -q "byline-link" <<< "$REPLY_BODY"; then
  pass "search?q=news rendered at least one byline link"
fi
check_clean_200 "/my"
check_clean_200 "/sports"
check_clean_200 "/sports/scores"
check_clean_200 "/sports/nfl"
check_clean_200 "/safety"
check_clean_200 "/robots.txt"
check_clean_200 "/sitemap.xml"
check_clean_200 "/topic/news"
check_clean_200 "/journalist/smoke-test-nobody-yet"
if [[ -n "${REPLY_BODY:-}" ]] && ! grep -q "Not enough data" <<< "$REPLY_BODY"; then
  fail "journalist page for an unseen byline should say 'Not enough data'"
fi
check_clean_200 "/api/trends?geo=US"
check_clean_200 "/api/pulse"
check_clean_200 "/api/sports/scoreboard?league=nfl"
check_clean_200 "/static/whats-new.json"

style_body="$(http_body "${BASE}/static/style.css")"
logo_body="$(http_body "${BASE}/static/logo-compass.svg")"
if grep -E -- "--accent: #0f766e" <<< "$style_body" >/dev/null && \
   grep -E -- "--accent: #5eead4" <<< "$style_body" >/dev/null && \
   grep -E -- "--accent-fill: #2dd4bf" <<< "$style_body" >/dev/null; then
  pass "shared Yoyosup teal theme tokens"
else
  fail "News CSS is missing shared Yoyosup teal theme tokens"
fi
if grep -E '#c2410c|#f97316|#fb923c|rgba\(249, 115, 22' <<< "$style_body" >/dev/null || \
   grep -E '#f97316' <<< "$logo_body" >/dev/null; then
  fail "old orange brand chrome remains in News CSS/logo"
else
  pass "old orange brand chrome removed"
fi
if grep -E '#2dd4bf' <<< "$logo_body" >/dev/null; then
  pass "compass mark uses shared teal north tip"
else
  fail "compass mark is missing shared teal north tip"
fi

# Internal maintenance endpoint must reject non-loopback callers.
internal_code="$(http_code "${BASE}/internal/backfill-bylines")"
# POST-only route; a GET should 405, not 200/500 — either way it must not be 200.
if [[ "$internal_code" == "200" ]]; then
  fail "/internal/backfill-bylines answered GET with 200 (should be loopback/POST-only)"
else
  pass "/internal/backfill-bylines not open to GET (HTTP $internal_code)"
fi

# Discuss expands under the story. Do not accept a jump-only /topic/#comments path.
check_discuss_comment_flow() {
  echo ""
  echo "=== Discuss expands under the story ==="
  local home search js comments_json post_json counts_json like_json slug
  home=""
  local i
  for i in 1 2 3 4 5; do
    home="$(http_body "${BASE}/" 30)"
    if grep -q 'data-discuss' <<< "$home"; then
      break
    fi
    sleep 1
  done
  search="$(http_body "${BASE}/search" 30)"
  js="$(http_body "${BASE}/static/inline-discuss.js")"

  if grep -q 'data-discuss' <<< "$home"; then
    pass "Pulse stories have inline Discuss buttons"
  else
    fail "Pulse missing data-discuss buttons"
  fi
  if grep -q 'data-discuss' <<< "$search"; then
    pass "Intersection/search has inline Discuss buttons"
  else
    fail "Intersection/search missing data-discuss buttons"
  fi
  if grep -q 'inline-discuss' <<< "$js" && grep -q '/api/topic/' <<< "$js"; then
    pass "inline-discuss.js loads comments under the card"
  else
    fail "inline-discuss.js missing or does not call /api/topic/"
  fi

  slug="$(
    printf '%s' "$home" | python3 -c '
import re, sys
html = sys.stdin.read()
m = re.search(r"data-slug=\"([^\"]+)\"", html)
print(m.group(1) if m else "news")
'
  )"
  comments_json="$(http_body "${BASE}/api/topic/${slug}/comments" 20)"
  if printf '%s' "$comments_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d.get("comments"), list)'; then
    pass "GET /api/topic/${slug}/comments returns comments[]"
  else
    fail "GET /api/topic/${slug}/comments is not a comments list"
  fi
  post_json="$(curl -sS --connect-timeout 5 --max-time 20 -X POST \
    -H 'Content-Type: application/json' \
    -d '{"name":"Smoke","body":""}' \
    "${BASE}/api/topic/${slug}/comments" 2>/dev/null || true)"
  if printf '%s' "$post_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ok") is False'; then
    pass "POST empty comment is rejected without leaving the feed"
  else
    fail "POST /api/topic/${slug}/comments did not reject an empty body"
  fi
  counts_json="$(http_body "${BASE}/api/comments/counts?slugs=${slug}" 15)"
  if printf '%s' "$counts_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert isinstance(d.get("counts"), dict)'; then
    pass "GET /api/comments/counts returns a counts map"
  else
    fail "GET /api/comments/counts is not a counts map"
  fi
  like_json="$(curl -sS --connect-timeout 5 --max-time 15 -X POST \
    "${BASE}/api/topic/${slug}/comments/not-a-real-id/like" 2>/dev/null || true)"
  if printf '%s' "$like_json" | python3 -c 'import json,sys; d=json.load(sys.stdin); assert d.get("ok") is False'; then
    pass "POST like on a missing comment is rejected"
  else
    fail "POST like missing comment did not return ok=false"
  fi
}

if [[ "$SMOKE_SKIP_TOPICS" == "1" ]]; then
  warn "SMOKE_SKIP_TOPICS=1 — skipped topic×lean matrix"
else
  warm_preferred_pools
  check_topic_filter_matrix
fi

check_discuss_comment_flow

echo ""
echo "=== Summary: ${PASS} pass · ${WARN} warn · ${FAIL} fail ==="
if [[ "$FAIL" -gt 0 ]]; then
  echo "SMOKE CHECK FAILED"
  exit 1
fi
echo "SMOKE CHECK OK"
