#!/usr/bin/env bash
# News post-deploy smoke checks — catch 500s / broken pages before/after deploy.
#
# Usage:
#   ./scripts/smoke-check.sh              # http against local :3010
#   ./scripts/smoke-check.sh [BASE]       # http against BASE
#
# Exit 0 = all pass; non-zero = failures printed.
set -euo pipefail

BASE="${1:-http://127.0.0.1:3010}"
if [[ "${SMOKE_BASE:-}" != "" ]]; then
  BASE="$SMOKE_BASE"
fi
BASE="${BASE%/}"

FAIL=0
PASS=0

pass() { PASS=$((PASS + 1)); echo "  PASS  $*"; }
fail() { FAIL=$((FAIL + 1)); echo "  FAIL  $*"; }

http_code() {
  curl -sS -o /dev/null -w "%{http_code}" --connect-timeout 5 --max-time 25 \
    -L --max-redirs 5 "$1" 2>/dev/null || echo "000"
}

http_body() {
  curl -sS --connect-timeout 5 --max-time 25 -L --max-redirs 5 "$1" 2>/dev/null || true
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

echo ""
echo "=== News HTTP smoke (${BASE}) ==="

probe="$(http_code "${BASE}/health")"
if [[ "$probe" != "200" ]]; then
  fail "cannot reach ${BASE}/health (HTTP $probe) — is the stack up?"
  echo ""
  echo "=== Summary: ${PASS} pass · ${FAIL} fail ==="
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

# Internal maintenance endpoint must reject non-loopback callers.
internal_code="$(http_code "${BASE}/internal/backfill-bylines")"
# POST-only route; a GET should 405, not 200/500 — either way it must not be 200.
if [[ "$internal_code" == "200" ]]; then
  fail "/internal/backfill-bylines answered GET with 200 (should be loopback/POST-only)"
else
  pass "/internal/backfill-bylines not open to GET (HTTP $internal_code)"
fi

echo ""
echo "=== Summary: ${PASS} pass · ${FAIL} fail ==="
if [[ "$FAIL" -gt 0 ]]; then
  echo "SMOKE CHECK FAILED"
  exit 1
fi
echo "SMOKE CHECK OK"
