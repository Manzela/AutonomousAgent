#!/usr/bin/env bash
# Graceful shutdown: snapshot first, then bring stack down.
# Pass --remove-volumes to also delete persistent data (DESTRUCTIVE).
#
# `down [-v]` operates on every service in the compose file, so the
# rename of hermes-agent → hermes and the removal of chroma /
# honcho-db is automatically picked up — there are no per-service
# exec calls in this script.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml -f $ROOT/deploy/docker-compose.dev.yml"

echo "==> Snapshotting before teardown"
"$ROOT/scripts/snapshot.sh"

echo "==> Stopping stack"
if [ "${1:-}" = "--remove-volumes" ]; then
  read -r -p "REMOVE ALL DATA VOLUMES? Type YES: " confirm
  if [ "$confirm" = "YES" ]; then
    $COMPOSE down -v
    echo "Stack and volumes removed"
  else
    echo "Aborted."; exit 1
  fi
else
  $COMPOSE down
  echo "Stack stopped (data volumes preserved)"
fi
