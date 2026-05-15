#!/usr/bin/env bash
# Emergency halt: pause all agent activity, snapshot, leave containers running for inspection.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

echo "==> PANIC: pausing hermes-agent and hermes-gateway"
$COMPOSE pause hermes-agent hermes-gateway

echo "==> Snapshotting state"
"$ROOT/scripts/snapshot.sh"

TS=$(date -u +%Y%m%d-%H%M%S)
echo "==> Marking panic event"
echo "$TS panic invoked by user" >> "$ROOT/logs/panic.log"

echo
echo "✓ Agent halted. Inspect logs, then resume with:"
echo "    $COMPOSE unpause hermes-agent hermes-gateway"
