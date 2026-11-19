#!/bin/bash
# Runs as root on the TARGET API host (testing or dev), sent by sync_from_prod.sh over SSM,
# after the target Neo4j holds a fresh copy of prod. The API service is stopped (writes paused).
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
# The dump holds data only; constraints and triggers come from the code this environment runs.
py scripts.install_neo4j_constraints
py scripts.install_neo4j_triggers
# This environment's code is usually ahead of prod, so prod's data may need its pending upgrades
# (the same ones the next release runs on prod). Audit first: it fails on data the upgrade cannot fix.
py scripts.migrate
py scripts.migrate --apply --content
# The search indexes must describe this environment's data, not the old data.
py scripts.reindex --writes-paused

systemctl start "$SERVICE"
if ! curl --fail --silent --retry 10 --retry-delay 2 --retry-all-errors http://127.0.0.1:8000/__/health; then
  journalctl -u "$SERVICE" -n 100 --no-pager
  exit 1
fi
echo
