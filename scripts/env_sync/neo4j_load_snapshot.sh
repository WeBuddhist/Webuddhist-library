#!/bin/bash
# Runs as root on the TARGET Neo4j host (testing or dev), sent by sync_from_prod.sh over SSM.
#
# A copy of the prod Neo4j root disk (made from an EBS snapshot) is attached to this host.
# 1. Mount the copy and start a throwaway Neo4j on it, so the crash-consistent copy recovers cleanly.
# 2. Dump the `neo4j` database from the copy.
# 3. Load the dump into this host's own Neo4j container. Its `system` database (users, password)
#    is not touched, so the target keeps its own password.
#
# Usage: neo4j_load_snapshot.sh <volume id> <prod data path> <target container name>
set -euo pipefail

VOLUME_ID=$1
PROD_DATA_PATH=$2
CONTAINER=$3

MNT=/mnt/env-sync-prod
WORK=/var/tmp/env-sync
RECOVER=neo4j-env-sync

log() { echo "[$(date -u +%H:%M:%S)] $*"; }

cleanup() {
  docker rm -f "$RECOVER" >/dev/null 2>&1 || true
  if mountpoint -q "$MNT"; then umount "$MNT" || log "WARNING: could not unmount $MNT"; fi
  rm -rf "$WORK"
  # Never leave the target database down, even after a failed load.
  if [ "$(docker inspect -f '{{.State.Running}}' "$CONTAINER" 2>/dev/null)" != "true" ]; then
    log "Starting $CONTAINER again"
    docker start "$CONTAINER" >/dev/null || log "WARNING: could not start $CONTAINER"
  fi
}
trap cleanup EXIT

wait_started() { # <container> <docker logs --since value>
  for _ in $(seq 1 150); do
    if docker logs --since "$2" "$1" 2>&1 | grep -q "Started."; then return 0; fi
    if [ "$(docker inspect -f '{{.State.Running}}' "$1")" != "true" ]; then
      docker logs --tail 50 "$1" 2>&1 || true
      log "ERROR: $1 stopped while starting"
      return 1
    fi
    sleep 2
  done
  docker logs --tail 50 "$1" 2>&1 || true
  log "ERROR: $1 did not start in 300 s"
  return 1
}

# --- find the attached copy (Nitro: NVMe by volume id; Xen/t2: /dev/xvdf) ---
disk=""
for _ in $(seq 1 60); do
  nvme="/dev/disk/by-id/nvme-Amazon_Elastic_Block_Store_${VOLUME_ID/-/}"
  if [ -e "$nvme" ]; then disk=$(readlink -f "$nvme"); break; fi
  if [ -e /dev/xvdf ]; then disk=/dev/xvdf; break; fi
  sleep 2
done
[ -n "$disk" ] || { log "ERROR: volume $VOLUME_ID is not visible on this host"; exit 1; }

part=$(lsblk -lnpo NAME,LABEL "$disk" | awk '$2 == "cloudimg-rootfs" { print $1 }')
if [ -z "$part" ]; then
  part=$(lsblk -lnpo NAME,TYPE "$disk" | awk '$2 == "part" { print $1 }' | head -1)
fi
part=${part:-$disk}
if [ "$(findmnt -n -o SOURCE /)" = "$part" ]; then
  log "ERROR: $part is this host's own root disk"
  exit 1
fi
log "Prod copy is $part (volume $VOLUME_ID)"

# Read-write on purpose: it is a throwaway copy, and ext4 must replay its journal.
mkdir -p "$MNT"
mount "$part" "$MNT"
SRC="$MNT$PROD_DATA_PATH"
[ -d "$SRC/databases/neo4j" ] || { log "ERROR: no Neo4j database under $SRC"; exit 1; }

IMAGE=$(docker inspect -f '{{.Config.Image}}' "$CONTAINER")
TARGET_DATA=$(docker inspect -f '{{range .Mounts}}{{if eq .Destination "/data"}}{{.Source}}{{end}}{{end}}' "$CONTAINER")
[ -n "$TARGET_DATA" ] || { log "ERROR: $CONTAINER has no /data mount"; exit 1; }
log "Image $IMAGE; target data $TARGET_DATA"

# --- 1. recover the copy ---
log "Recovering the prod copy"
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker run -d --name "$RECOVER" -v "$SRC:/data" \
  -e NEO4J_AUTH=none \
  -e NEO4J_server_memory_heap_initial__size=512m \
  -e NEO4J_server_memory_heap_max__size=512m \
  -e NEO4J_server_memory_pagecache_size=256m \
  "$IMAGE" >/dev/null
wait_started "$RECOVER" "$since"
docker stop -t 120 "$RECOVER" >/dev/null
docker rm "$RECOVER" >/dev/null

# --- 2. dump ---
log "Dumping"
mkdir -p "$WORK"
chown 7474:7474 "$WORK"
docker run --rm --user 7474:7474 -v "$SRC:/data" -v "$WORK:/backups" "$IMAGE" \
  neo4j-admin database dump neo4j --to-path=/backups --overwrite-destination=true
ls -lh "$WORK"

# --- 3. load into the target ---
log "Stopping $CONTAINER and loading the dump"
docker stop -t 120 "$CONTAINER" >/dev/null
docker run --rm --user 7474:7474 -v "$TARGET_DATA:/data" -v "$WORK:/backups" "$IMAGE" \
  neo4j-admin database load neo4j --from-path=/backups --overwrite-destination=true
since=$(date -u +%Y-%m-%dT%H:%M:%SZ)
docker start "$CONTAINER" >/dev/null
wait_started "$CONTAINER" "$since"
log "Target Neo4j has the prod data"
