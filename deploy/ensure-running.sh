#!/bin/sh
set -eu

LOCK=/tmp/ireland-news-watchdog.lock
PATTERN='uvicorn app:app --host 127.0.0.1 --port 8200'
APP_DIR=/home/ubuntu/ireland-news

exec 9>"$LOCK"
flock -n 9 || exit 0

if pgrep -u "$(id -u)" -f "$PATTERN" >/dev/null 2>&1; then
  exit 0
fi

cd "$APP_DIR"
mkdir -p backups
printf '%s watchdog: uvicorn absent; starting fallback\n' "$(date --iso-8601=seconds)" >> backups/watchdog.log
nohup ./.venv/bin/uvicorn app:app --host 127.0.0.1 --port 8200 >> backups/uvicorn-fallback.log 2>&1 &
