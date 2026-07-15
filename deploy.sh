#!/usr/bin/env bash
# Deploy yoyosup news/pulse app to this host (192.168.1.44) on port 3010.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"
REMOTE_DIR="${HOME}/apps/news"

echo "Deploying news app → ${REMOTE_DIR}"
mkdir -p "${REMOTE_DIR}"

rsync -av --delete \
  --exclude .git \
  --exclude .env \
  --exclude .venv \
  --exclude venv \
  --exclude '__pycache__/' \
  --exclude 'data/*.json' \
  "${ROOT}/" "${REMOTE_DIR}/"

cd "${REMOTE_DIR}"
docker compose build
docker compose up -d --force-recreate
docker compose ps

echo ""
echo "Local:  http://127.0.0.1:3010/"
echo "Public: https://news.yoyosup.com/"
curl -sS -o /dev/null -w "local health: %{http_code}\n" http://127.0.0.1:3010/health || true
curl -sS -o /dev/null -w "public  root: %{http_code}\n" --max-time 20 https://news.yoyosup.com/ || true
