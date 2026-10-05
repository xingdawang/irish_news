#!/bin/sh
# Stream JSON over SSH; the publish token stays on the server.
set -eu
if [ "$#" -ne 1 ] || [ ! -f "$1" ]; then
  echo "usage: publish-over-ssh.sh edition.json" >&2
  exit 2
fi
KEY=${IRELAND_NEWS_SSH_KEY:-$HOME/Downloads/tecent_01.pem}
TARGET=${IRELAND_NEWS_SSH_TARGET:-ubuntu@162.62.233.52}
ssh -o BatchMode=yes -o ConnectTimeout=20 -i "$KEY" "$TARGET" \
  'cd /home/ubuntu/ireland-news && ./.venv/bin/python publish.py -' < "$1"
