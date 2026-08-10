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
  if echo "$body" | grep -qE "Internal Server Error|Traceback \(most recent call last\)|jinja2\.exceptions"; then
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
        empty_fail=$((empty_fail + 1))
        continue
      fi
      if [[ "$n" -lt 1 ]]; then
        fail "topic=\"${t}\" lean=${lean:-default} → 0 preferred-source hits (mode=${mode:-?})"
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
  if [[ "$empty_fail" -eq 0 ]]; then
    pass "all topic×lean combos returned ≥1 preferred-source hit"
  else
    fail "${empty_fail}/${checked} topic×lean combos returned zero hits"
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
if [[ -n "${REPLY_BODY:-}" ]] && echo "$REPLY_BODY" | grep -q "byline-link"; then
  pass "search?q=news rendered at least one byline link"
fi
check_clean_200 "/my"
check_clean_200 "/safety"
check_clean_200 "/robots.txt"
check_clean_200 "/sitemap.xml"
check_clean_200 "/topic/news"
check_clean_200 "/journalist/smoke-test-nobody-yet"
if [[ -n "${REPLY_BODY:-}" ]] && ! echo "$REPLY_BODY" | grep -q "Not enough data"; then
  fail "journalist page for an unseen byline should say 'Not enough data'"
fi
check_clean_200 "/api/trends?geo=US"
check_clean_200 "/api/pulse"
check_clean_200 "/static/whats-new.json"

# Internal maintenance endpoint must reject non-loopback callers.
internal_code="$(http_code "${BASE}/internal/backfill-bylines")"
# POST-only route; a GET should 405, not 200/500 — either way it must not be 200.
if [[ "$internal_code" == "200" ]]; then
  fail "/internal/backfill-bylines answered GET with 200 (should be loopback/POST-only)"
else
  pass "/internal/backfill-bylines not open to GET (HTTP $internal_code)"
fi

if [[ "$SMOKE_SKIP_TOPICS" == "1" ]]; then
  warn "SMOKE_SKIP_TOPICS=1 — skipped topic×lean matrix"
else
  warm_preferred_pools
  check_topic_filter_matrix
fi

echo ""
echo "=== Summary: ${PASS} pass · ${WARN} warn · ${FAIL} fail ==="
if [[ "$FAIL" -gt 0 ]]; then
  echo "SMOKE CHECK FAILED"
  exit 1
fi
echo "SMOKE CHECK OK"
