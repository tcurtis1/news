#!/usr/bin/env bash
# Deploy yoyosup news/pulse app to basement host on port 3010.
# Usage: ./deploy.sh [user@host|local]
set -euo pipefail

TARGET="${1:-tony@192.168.1.44}"
REMOTE_DIR_TILDE="~/apps/news"
REMOTE_DIR_ABS="${HOME}/apps/news"
ROOT="$(cd "$(dirname "$0")" && pwd)"
SSH="ssh -i ${HOME}/.ssh/id_ed25519 -o StrictHostKeyChecking=accept-new -o ConnectTimeout=10"
# Who's deploying (claude / grok / tony) -- shows up in the deploy-lock message
# if someone else tries to deploy at the same time. Set before invoking, e.g.
# DEPLOY_AGENT=grok ./deploy.sh
DEPLOY_AGENT="${DEPLOY_AGENT:-unknown}"

is_local_deploy() {
  case "${TARGET}" in
    local|localhost|127.0.0.1) return 0 ;;
  esac
  local host_part="${TARGET#*@}"
  local ips
  ips="$(hostname -I 2>/dev/null || true)"
  for ip in $ips; do
    if [[ "$host_part" == "$ip" ]]; then
      return 0
    fi
  done
  if [[ "$host_part" == "192.168.1.44" ]] && ip -4 addr show 2>/dev/null | grep -q 'inet 192.168.1.44/'; then
    return 0
  fi
  return 1
}

run_stack() {
  set -euo pipefail
  cd "${REMOTE_DIR_ABS}"
  chmod +x deploy.sh scripts/warm-trends.sh 2>/dev/null || true

  LOCK=".deploy.lock"
  if [[ -f "$LOCK" ]]; then
    age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
    if [[ $age -lt 600 ]]; then
      echo "DEPLOY LOCKED: $(cat "$LOCK") (${age}s ago) — another deploy may be running. Aborting."
      echo "If that's stale (crashed run), remove it: rm ${REMOTE_DIR_ABS}/${LOCK}"
      exit 1
    fi
    echo "Stale lock (${age}s old) — removing and continuing."
  fi
  echo "${DEPLOY_AGENT:-unknown} $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK"
  trap 'rm -f "$LOCK"' EXIT

  docker compose build
  docker compose up -d --force-recreate
  docker compose ps
  echo ""
  for i in 1 2 3 4 5 6 7 8 9 10; do
    if curl -sf -o /dev/null http://127.0.0.1:3010/health; then
      break
    fi
    sleep 1
  done
  curl -sS -o /dev/null -w "local health: %{http_code}\n" http://127.0.0.1:3010/health || true

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
  /bin/bash "${WARM}" || true
  tail -n 8 "${HOME}/apps/news/logs/warm-trends.log" 2>/dev/null || true
}

RSYNC_EXCLUDES=(
  --exclude .git
  --exclude .env
  --exclude .venv
  --exclude venv
  --exclude '__pycache__/'
  --exclude 'data/*.json'
  --exclude 'data/comments/'
  --exclude 'logs/'
  --exclude '.deploy.lock'
)

if is_local_deploy; then
  echo "Deploying news app locally → ${REMOTE_DIR_ABS} (no SSH; this host is the target)"
  mkdir -p "${REMOTE_DIR_ABS}/logs"
  rsync -avz --delete \
    "${RSYNC_EXCLUDES[@]}" \
    "${ROOT}/" "${REMOTE_DIR_ABS}/"
  if [[ -f "${ROOT}/.env" ]]; then
    rsync -avz "${ROOT}/.env" "${REMOTE_DIR_ABS}/.env"
    echo "Synced .env → ${REMOTE_DIR_ABS}/.env"
  else
    echo "No local .env — left ${REMOTE_DIR_ABS}/.env unchanged (if any)."
  fi
  run_stack
else
  echo "Deploying news app → ${TARGET}:${REMOTE_DIR_TILDE}"
  $SSH "${TARGET}" "mkdir -p ${REMOTE_DIR_TILDE}/logs"
  rsync -avz --delete -e "$SSH" \
    "${RSYNC_EXCLUDES[@]}" \
    "${ROOT}/" "${TARGET}:${REMOTE_DIR_TILDE}/"
  if [[ -f "${ROOT}/.env" ]]; then
    rsync -avz -e "$SSH" "${ROOT}/.env" "${TARGET}:${REMOTE_DIR_TILDE}/.env"
    echo "Synced .env → ${TARGET}:${REMOTE_DIR_TILDE}/.env"
  else
    echo "No local .env — left remote .env unchanged (if any)."
  fi
  $SSH "${TARGET}" "DEPLOY_AGENT='${DEPLOY_AGENT}' bash -s" <<'REMOTE'
set -euo pipefail
cd ~/apps/news
chmod +x deploy.sh scripts/warm-trends.sh

LOCK=".deploy.lock"
if [[ -f "$LOCK" ]]; then
  age=$(( $(date +%s) - $(stat -c %Y "$LOCK" 2>/dev/null || echo 0) ))
  if [[ $age -lt 600 ]]; then
    echo "DEPLOY LOCKED: $(cat "$LOCK") (${age}s ago) — another deploy may be running. Aborting."
    echo "If that's stale (crashed run), remove it: rm ~/apps/news/${LOCK}"
    exit 1
  fi
  echo "Stale lock (${age}s old) — removing and continuing."
fi
echo "${DEPLOY_AGENT:-unknown} $(date -u +%Y-%m-%dT%H:%M:%SZ)" > "$LOCK"
trap 'rm -f "$LOCK"' EXIT

docker compose build
docker compose up -d --force-recreate
docker compose ps
echo ""
sleep 2
curl -sS -o /dev/null -w "local health: %{http_code}\n" http://127.0.0.1:3010/health || true
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
/bin/bash "${WARM}" || true
tail -n 8 ~/apps/news/logs/warm-trends.log 2>/dev/null || true
REMOTE
fi

echo ""
echo "LAN:    http://192.168.1.44:3010/"
echo "Public: https://news.yoyosup.com/"
curl -sS -o /dev/null -w "public root: %{http_code}\n" --max-time 20 https://news.yoyosup.com/ || true
