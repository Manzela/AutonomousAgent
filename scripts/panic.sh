#!/usr/bin/env bash
# Emergency halt: pause all agent activity, snapshot, leave containers
# running for inspection.
#
# Note: only `hermes` is paused. hermes-gateway was collapsed into hermes
# in commit 408459e. The escalation-watcher / litellm-proxy / phoenix
# containers stay running so you can still see traces + receive the
# panic notification on Telegram.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE="docker compose -f $ROOT/deploy/docker-compose.yml"

echo "==> PANIC: pausing hermes"
$COMPOSE pause hermes

echo "==> Snapshotting state"
"$ROOT/scripts/snapshot.sh"

TS=$(date -u +%Y%m%d-%H%M%S)
echo "==> Marking panic event"
mkdir -p "$ROOT/logs"
echo "$TS panic invoked by user" >> "$ROOT/logs/panic.log"

echo
echo "Agent halted. Inspect logs, then resume with:"
echo "    $COMPOSE unpause hermes"
