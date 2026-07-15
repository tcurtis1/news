#!/usr/bin/env bash
# Warm daily Google/Bing/YouTube/X trends cache (run via cron on the host).
# Force-refresh so the first human visitor never waits on upstream pulls.
set -euo pipefail

BASE_URL="${NEWS_BASE_URL:-http://127.0.0.1:3010}"
LOG_DIR="${NEWS_LOG_DIR:-${HOME}/apps/news/logs}"
mkdir -p "${LOG_DIR}"
LOG="${LOG_DIR}/warm-trends.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

{
  echo "[$(ts)] warm-trends start ${BASE_URL}"
  # force=1 → re-pull all platforms and rewrite daily cache
  code=$(curl -sS -o /tmp/yoyonews-trends-warm.json -w "%{http_code}" \
    --max-time 120 \
    "${BASE_URL}/api/trends?force=1" || echo "000")
  echo "[$(ts)] /api/trends?force=1 → HTTP ${code}"
  if [[ -f /tmp/yoyonews-trends-warm.json ]]; then
    # compact one-line summary if python is available
    python3 - <<'PY' 2>/dev/null || true
import json
try:
    d = json.load(open("/tmp/yoyonews-trends-warm.json"))
    print(f"  day={d.get('day')} sources={d.get('sources_ok')} counts={d.get('counts')}")
except Exception as e:
    print(f"  parse_error={e}")
PY
  fi
  # light pulse warm (uses its own shorter cache; no force)
  pcode=$(curl -sS -o /dev/null -w "%{http_code}" --max-time 90 \
    "${BASE_URL}/api/pulse" || echo "000")
  echo "[$(ts)] /api/pulse → HTTP ${pcode}"
  echo "[$(ts)] warm-trends done"
} >>"${LOG}" 2>&1
