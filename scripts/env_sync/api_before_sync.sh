#!/bin/bash
# Runs as root on the TARGET API host (testing or dev), sent by sync_from_prod.sh over SSM,
# before the target Neo4j is replaced with a copy of prod.
# 1. Save this environment's own API keys (api_keys.py save); api_after_sync.sh puts them back.
# 2. Stop the API, so nothing writes while the database is replaced.
#
# Usage: api_before_sync.sh <app dir> <service name> <api_keys.py, base64>
set -euo pipefail

APP_DIR=$1
SERVICE=$2
KEYS_PY_B64=$3
STATE=/var/lib/webuddhist-env-sync

cd "$APP_DIR"
owner=$(stat -c '%U' .)
install -d -m 700 -o "$owner" "$STATE"
printf '%s' "$KEYS_PY_B64" | base64 -d >"$STATE/api_keys.py"
chown "$owner" "$STATE/api_keys.py"

if [ -s "$STATE/api-keys.json" ]; then
  # A sync that failed after loading prod left these. The database may now hold prod's keys,
  # so saving again would lose this environment's own keys.
  echo "Keeping the keys saved by an earlier sync that did not finish ($STATE/api-keys.json)"
else
  runuser -u "$owner" -- .venv/bin/python "$STATE/api_keys.py" save "$STATE/api-keys.json"
fi

systemctl stop "$SERVICE"
echo "Stopped $SERVICE"
