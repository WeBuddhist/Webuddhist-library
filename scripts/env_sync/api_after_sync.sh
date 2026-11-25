#!/bin/bash
# Runs as root on the TARGET API host (testing or dev), sent by sync_from_prod.sh over SSM,
# after the target Neo4j holds a fresh copy of prod. The API service is stopped (writes paused).
# The environment's own API keys were saved by api_before_sync.sh; they replace prod's here.
#
# Usage: api_after_sync.sh <app dir> <service name>
set -euo pipefail

APP_DIR=$1
SERVICE=$2

cd "$APP_DIR"
owner=$(stat -c '%U' .)
# Neo4j driver notifications flood the output and push real errors past SSM's output limit.
py() { runuser -u "$owner" -- .venv/bin/python -m "$@" 2>&1 | { grep -v 'neo4j.notifications' || true; }; }

echo "Code: $(git -c safe.directory="$PWD" rev-parse --short HEAD)"
# Replace the copied prod keys with the target's saved keys before upgrading its data.
state=/var/lib/webuddhist-env-sync
runuser -u "$owner" -- .venv/bin/python "$state/api_keys.py" restore "$state/api-keys.json"
rm -f "$state/api-keys.json"
# Apply runs the schema audit, installs constraints and triggers, and upgrades the restored data.
py scripts.migrate --apply --content
py scripts.reindex --writes-paused

systemctl start "$SERVICE"
if ! curl --fail --silent --retry 10 --retry-delay 2 --retry-all-errors http://127.0.0.1:8000/__/health; then
  journalctl -u "$SERVICE" -n 100 --no-pager
  exit 1
fi
echo
