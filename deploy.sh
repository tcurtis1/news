#!/usr/bin/env bash
# Deploy yoyosup news/pulse app to basement host on port 3010.
# Usage: ./deploy.sh [user@host]
set -euo pipefail

TARGET="${1:-tony@192.168.1.44}"
REMOTE_DIR="~/apps/news"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SSH="ssh -i ${HOME}/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"

echo "Deploying news app → ${TARGET}:${REMOTE_DIR}"

$SSH "${TARGET}" "mkdir -p ${REMOTE_DIR}/logs"

rsync -avz --delete -e "$SSH" \
  --exclude .git \
  --exclude .env \
  --exclude .venv \
  --exclude venv \
  --exclude '__pycache__/' \
  --exclude 'data/*.json' \
  --exclude 'data/comments/' \
  --exclude 'logs/' \
  "${ROOT}/" "${TARGET}:${REMOTE_DIR}/"

# Sync secrets separately (not deleted by --delete when missing locally)
if [[ -f "${ROOT}/.env" ]]; then
  rsync -avz -e "$SSH" "${ROOT}/.env" "${TARGET}:${REMOTE_DIR}/.env"
  echo "Synced .env → ${TARGET}:${REMOTE_DIR}/.env"
else
  echo "No local .env — left remote .env unchanged (if any)."
fi

$SSH "${TARGET}" bash -s <<'REMOTE'
set -euo pipefail
cd ~/apps/news
chmod +x deploy.sh scripts/warm-trends.sh

docker compose build
docker compose up -d --force-recreate
docker compose ps
echo ""
sleep 2
curl -sS -o /dev/null -w "local health: %{http_code}\n" http://127.0.0.1:3010/health || true

# Install daily warm-cron at 06:00 America/Denver (server local time)
# Keeps existing crontab lines; replaces only our managed marker block.
WARM="${HOME}/apps/news/scripts/warm-trends.sh"
MARKER_BEGIN="# BEGIN yoyosup-news-warm"
MARKER_END="# END yoyosup-news-warm"
CRON_LINE="0 6 * * * /bin/bash ${WARM}"

EXISTING="$(crontab -l 2>/dev/null || true)"
FILTERED="$(printf '%s\n' "${EXISTING}" | sed "/${MARKER_BEGIN}/,/${MARKER_END}/d" || true)"
{
  printf '%s\n' "${FILTERED}"
  echo "${MARKER_BEGIN}"
  echo "${CRON_LINE}"
  echo "${MARKER_END}"
} | sed '/^$/N;/^\n$/D' | crontab -

echo "crontab installed:"
crontab -l | sed -n "/${MARKER_BEGIN}/,/${MARKER_END}/p"
echo ""
# Kick an immediate warm so cache is ready after deploy
/bin/bash "${WARM}" || true
tail -n 8 ~/apps/news/logs/warm-trends.log 2>/dev/null || true
REMOTE

echo ""
echo "LAN:    http://192.168.1.44:3010/"
echo "Public: https://news.yoyosup.com/"
curl -sS -o /dev/null -w "public root: %{http_code}\n" --max-time 20 https://news.yoyosup.com/ || true
