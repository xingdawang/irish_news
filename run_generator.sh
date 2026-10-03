#!/bin/bash
set -u
APP=/home/ubuntu/ireland-news
LOG=$APP/backups/generator.log
LOCK=/tmp/ireland-news-generator.lock
mkdir -p "$APP/backups"
exec 9>"$LOCK"
flock -n 9 || exit 0
HOUR=$(TZ=Europe/Dublin date +%H)
case "$HOUR" in
  00|06|12|18) ;;
  *) exit 0 ;;
esac
cd "$APP" || exit 1
STAMP=$(TZ=Europe/Dublin date --iso-8601=seconds)
echo "$STAMP generator start" >> "$LOG"
if ./.venv/bin/python generator.py >> "$LOG" 2>&1; then
  echo "$(TZ=Europe/Dublin date --iso-8601=seconds) generator success" >> "$LOG"
  exit 0
fi
echo "$(TZ=Europe/Dublin date --iso-8601=seconds) generator failed; retrying in 180s" >> "$LOG"
sleep 180
if ./.venv/bin/python generator.py >> "$LOG" 2>&1; then
  echo "$(TZ=Europe/Dublin date --iso-8601=seconds) generator retry success" >> "$LOG"
  exit 0
fi
echo "$(TZ=Europe/Dublin date --iso-8601=seconds) generator retry failed" >> "$LOG"
exit 1
