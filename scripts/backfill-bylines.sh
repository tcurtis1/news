#!/usr/bin/env bash
# Backfill journalist bylines by fetching a batch of queued article pages
# (run via cron on the host, off the request path).
set -euo pipefail

BASE_URL="${NEWS_BASE_URL:-http://127.0.0.1:3010}"
LOG_DIR="${NEWS_LOG_DIR:-${HOME}/apps/news/logs}"
mkdir -p "${LOG_DIR}"
LOG="${LOG_DIR}/backfill-bylines.log"

ts() { date -u +"%Y-%m-%dT%H:%M:%SZ"; }

{
  echo "[$(ts)] backfill-bylines start ${BASE_URL}"
  code=$(curl -sS -o /tmp/yoyonews-backfill.json -w "%{http_code}" \
    --max-time 90 -X POST \
    "${BASE_URL}/internal/backfill-bylines?limit=25" || echo "000")
  echo "[$(ts)] /internal/backfill-bylines → HTTP ${code}"
  if [[ -f /tmp/yoyonews-backfill.json ]]; then
    cat /tmp/yoyonews-backfill.json
    echo ""
  fi
  echo "[$(ts)] backfill-bylines done"
} >>"${LOG}" 2>&1
