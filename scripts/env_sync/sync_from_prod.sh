#!/bin/bash
# Copy prod data into the testing (or dev) environment. Prod never stops.
#
# Runs on the GitHub runner (workflow sync-from-prod.yml) with the AWS CLI and jq, using a role
# that can read prod but write only to the target. Steps:
#   1. Snapshot the prod Neo4j root disk.
#   2. Copy prod S3 files to the target bucket.
#   3. Make a volume from the snapshot and attach it to the target Neo4j host.
#   4. Stop the target API, load the prod database into the target Neo4j (neo4j_load_snapshot.sh).
#   5. Detach and delete the volume and the snapshot.
#   6. Install constraints and triggers, rebuild the target's search indexes, start the API
#      (api_after_sync.sh).
#
# Required environment:
#   TARGET                    testing | dev
#   AWS_REGION
#   PROD_NEO4J_INSTANCE_ID    prod Neo4j EC2 instance
#   PROD_S3_BUCKET
#   TARGET_NEO4J_INSTANCE_ID  must carry the tag env=$TARGET
#   TARGET_API_INSTANCE_ID    must carry the tag env=$TARGET
#   TARGET_S3_BUCKET
# Optional:
#   APP_DIR (/opt/openpecha-backend), SERVICE_NAME (openpecha-api), NEO4J_CONTAINER (neo4j),
#   PROD_NEO4J_DATA_PATH (/var/lib/docker/volumes/neo4j_data/_data),
#   S3_SYNC_PREFIXES ("base_texts recordings"), KEEP_SNAPSHOT (false)
set -euo pipefail

HERE=$(cd "$(dirname "$0")" && pwd)
APP_DIR=${APP_DIR:-/opt/openpecha-backend}
SERVICE_NAME=${SERVICE_NAME:-openpecha-api}
NEO4J_CONTAINER=${NEO4J_CONTAINER:-neo4j}
PROD_NEO4J_DATA_PATH=${PROD_NEO4J_DATA_PATH:-/var/lib/docker/volumes/neo4j_data/_data}
S3_SYNC_PREFIXES=${S3_SYNC_PREFIXES:-base_texts recordings}
KEEP_SNAPSHOT=${KEEP_SNAPSHOT:-false}
export AWS_DEFAULT_REGION=$AWS_REGION AWS_PAGER=""

log() { echo "[$(date -u +%H:%M:%S)] $*"; }
fail() { echo "::error::$*"; exit 1; }

# --- safety checks: never write to prod ---
case "${TARGET:-}" in testing | dev) ;; *) fail "TARGET must be testing or dev, got '${TARGET:-}'" ;; esac
for v in PROD_NEO4J_INSTANCE_ID PROD_S3_BUCKET TARGET_NEO4J_INSTANCE_ID TARGET_API_INSTANCE_ID TARGET_S3_BUCKET; do
  [ -n "${!v:-}" ] || fail "$v is not set"
done
[ "$TARGET_NEO4J_INSTANCE_ID" != "$PROD_NEO4J_INSTANCE_ID" ] || fail "target Neo4j is the prod Neo4j"
[ "$TARGET_S3_BUCKET" != "$PROD_S3_BUCKET" ] || fail "target bucket is the prod bucket"

instance() { aws ec2 describe-instances --instance-ids "$1" --query 'Reservations[0].Instances[0]' --output json; }
env_tag() { jq -r '[.Tags[]? | select(.Key == "env") | .Value][0] // ""' <<<"$1"; }

target_neo4j=$(instance "$TARGET_NEO4J_INSTANCE_ID")
target_api=$(instance "$TARGET_API_INSTANCE_ID")
for pair in "$TARGET_NEO4J_INSTANCE_ID:$target_neo4j" "$TARGET_API_INSTANCE_ID:$target_api"; do
  id=${pair%%:*}
  tag=$(env_tag "${pair#*:}")
  [ "$tag" = "$TARGET" ] || fail "instance $id has tag env='$tag', expected env=$TARGET"
done
target_az=$(jq -r '.Placement.AvailabilityZone' <<<"$target_neo4j")
jq -e '[.BlockDeviceMappings[].DeviceName] | index("/dev/sdf") | not' <<<"$target_neo4j" >/dev/null \
  || fail "/dev/sdf is already in use on $TARGET_NEO4J_INSTANCE_ID"

prod_neo4j=$(instance "$PROD_NEO4J_INSTANCE_ID")
prod_volume=$(jq -r '.RootDeviceName as $r | .BlockDeviceMappings[] | select(.DeviceName == $r) | .Ebs.VolumeId' <<<"$prod_neo4j")
[ -n "$prod_volume" ] || fail "could not find the root volume of $PROD_NEO4J_INSTANCE_ID"

# --- run a script on an instance over SSM and wait for it (no 100 s waiter limit) ---
ssm_run() { # <instance> <script file> [args...]
  local instance=$1 file=$2 cmd status
  shift 2
  # Write the script to a file first: with `bash -s`, any child that reads stdin would eat the script.
  cmd="f=\$(mktemp) && cat >\"\$f\" <<'ENV_SYNC_SCRIPT'"$'\n'"$(cat "$file")"$'\n'"ENV_SYNC_SCRIPT"$'\n'
  cmd+="bash \"\$f\" $(printf '%q ' "$@"); rc=\$?; rm -f \"\$f\"; exit \$rc"
  jq -n --arg c "$cmd" '{commands: [$c], executionTimeout: ["3600"]}' >/tmp/ssm_params.json
  local id
  id=$(aws ssm send-command --instance-ids "$instance" --document-name AWS-RunShellScript \
    --parameters file:///tmp/ssm_params.json --query Command.CommandId --output text)
  log "SSM $id on $instance: $(basename "$file") $*"
  for _ in $(seq 1 400); do
    sleep 10
    status=$(aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
      --query Status --output text 2>/dev/null || echo Pending)
    case "$status" in Pending | InProgress | Delayed) continue ;; *) break ;; esac
  done
  aws ssm get-command-invocation --command-id "$id" --instance-id "$instance" \
    --query '[StandardOutputContent, StandardErrorContent]' --output text || true
  [ "$status" = "Success" ] || fail "SSM command on $instance ended with status $status"
}

oneline() { # <instance> <command...> as a script
  local instance=$1 f
  shift
  f=$(mktemp)
  printf '%s\n' "set -euo pipefail" "$*" >"$f"
  ssm_run "$instance" "$f"
  rm -f "$f"
}

snapshot="" volume="" api_stopped=false
cleanup() {
  set +e
  if [ -n "$volume" ]; then
    if [ "$(aws ec2 describe-volumes --volume-ids "$volume" --query 'Volumes[0].State' --output text)" = "in-use" ]; then
      log "Detaching $volume"
      aws ec2 detach-volume --volume-id "$volume" >/dev/null
      aws ec2 wait volume-available --volume-ids "$volume"
    fi
    aws ec2 delete-volume --volume-id "$volume" && log "Deleted volume $volume" \
      || echo "::warning::delete volume $volume by hand"
  fi
  if [ -n "$snapshot" ] && [ "$KEEP_SNAPSHOT" != "true" ]; then
    aws ec2 delete-snapshot --snapshot-id "$snapshot" && log "Deleted snapshot $snapshot" \
      || echo "::warning::delete snapshot $snapshot by hand"
  fi
  if [ "$api_stopped" = true ]; then
    log "Starting the $TARGET API again after a failure"
    oneline "$TARGET_API_INSTANCE_ID" "systemctl start $(printf '%q' "$SERVICE_NAME")"
  fi
}
trap cleanup EXIT

tags="{Key=purpose,Value=env-sync},{Key=env,Value=$TARGET}"

# --- 1. snapshot prod ---
log "Snapshot of prod Neo4j volume $prod_volume"
snapshot=$(aws ec2 create-snapshot --volume-id "$prod_volume" \
  --description "env-sync prod -> $TARGET $(date -u +%F)" \
  --tag-specifications "ResourceType=snapshot,Tags=[$tags]" --query SnapshotId --output text)
for _ in $(seq 1 12); do # up to 2 hours; the waiter itself gives up after 10 minutes
  aws ec2 wait snapshot-completed --snapshot-ids "$snapshot" && break
done
[ "$(aws ec2 describe-snapshots --snapshot-ids "$snapshot" --query 'Snapshots[0].State' --output text)" = completed ] \
  || fail "snapshot $snapshot did not complete"
log "Snapshot $snapshot complete"

# --- 2. S3 (prod keeps running; files for everything in the snapshot already exist) ---
for prefix in $S3_SYNC_PREFIXES; do
  log "S3 $PROD_S3_BUCKET/$prefix -> $TARGET_S3_BUCKET/$prefix"
  aws s3 sync "s3://$PROD_S3_BUCKET/$prefix" "s3://$TARGET_S3_BUCKET/$prefix" --only-show-errors
done

# --- 3. volume on the target host ---
volume=$(aws ec2 create-volume --snapshot-id "$snapshot" --availability-zone "$target_az" --volume-type gp3 \
  --tag-specifications "ResourceType=volume,Tags=[$tags]" --query VolumeId --output text)
aws ec2 wait volume-available --volume-ids "$volume"
aws ec2 attach-volume --volume-id "$volume" --instance-id "$TARGET_NEO4J_INSTANCE_ID" --device /dev/sdf >/dev/null
aws ec2 wait volume-in-use --volume-ids "$volume" --filters Name=attachment.status,Values=attached
log "Volume $volume attached to $TARGET_NEO4J_INSTANCE_ID"

# --- 4. swap the target database ---
oneline "$TARGET_API_INSTANCE_ID" "systemctl stop $(printf '%q' "$SERVICE_NAME")"
api_stopped=true
ssm_run "$TARGET_NEO4J_INSTANCE_ID" "$HERE/neo4j_load_snapshot.sh" "$volume" "$PROD_NEO4J_DATA_PATH" "$NEO4J_CONTAINER"

# --- 5. volume and snapshot go away now (cleanup also does this after a failure) ---
aws ec2 detach-volume --volume-id "$volume" >/dev/null
aws ec2 wait volume-available --volume-ids "$volume"
aws ec2 delete-volume --volume-id "$volume"
volume=""
log "Temporary volume deleted"

# --- 6. schema, search, start ---
ssm_run "$TARGET_API_INSTANCE_ID" "$HERE/api_after_sync.sh" "$APP_DIR" "$SERVICE_NAME"
api_stopped=false
log "$TARGET now has prod data from snapshot $snapshot"
