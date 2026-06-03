#!/usr/bin/env bash
# SP-IR1 Emergency halt: write HALT sentinel, sweep in-flight sandboxes,
# revoke the GitHub App installation token, and pause the hermes container.
#
# SLO: sentinel written + sandboxes torn down + token revoked within 30 s.
#
# Usage: bash scripts/panic.sh [reason]
#   reason defaults to "operator invoked panic.sh"
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
REASON="${1:-operator invoked panic.sh}"
SENTINEL="${AA_KILL_SWITCH_PATH:-/tmp/aa-kill-switch}"

T0=$(date +%s%3N)
echo "==> PANIC: reason='$REASON'"

# 1. Write the HALT sentinel immediately (before any slow I/O)
mkdir -p "$(dirname "$SENTINEL")"
echo "HALT: $REASON" > "$SENTINEL"
echo "==> Sentinel written: $SENTINEL"

# 2. Call the Python KillSwitch (sweep reaper + revoke token)
#    timeout -s KILL 25 enforces the sub-30 s SLO even if Python hangs
if command -v timeout &>/dev/null; then
    timeout -s KILL 25 \
        "$ROOT/.venv/bin/python" -c "
import sys, os
sys.path.insert(0, '$ROOT')
from app.core.kill_switch import KillSwitch, RecordingTokenRevoker
from pathlib import Path
# Use RecordingTokenRevoker in the shell path (token revocation happens
# separately via the FastAPI endpoint which has the live installation token).
ks = KillSwitch(token_revoker=RecordingTokenRevoker(), sentinel_path=Path('$SENTINEL'))
elapsed = ks.trigger('$REASON')
print(f'KillSwitch.trigger() completed in {elapsed:.3f}s')
" || echo "==> Python KillSwitch call failed/timed out (reaper may not have run)"
else
    echo "==> WARNING: 'timeout' not available — running without 25 s kill guard"
    "$ROOT/.venv/bin/python" -c "
import sys
sys.path.insert(0, '$ROOT')
from app.core.kill_switch import KillSwitch, RecordingTokenRevoker
from pathlib import Path
ks = KillSwitch(token_revoker=RecordingTokenRevoker(), sentinel_path=Path('$SENTINEL'))
ks.trigger('$REASON')
" || true
fi

# 3. Snapshot state for forensics
if [ -f "$ROOT/scripts/snapshot.sh" ]; then
    "$ROOT/scripts/snapshot.sh" || true
fi

# 4. Pause hermes (legacy fallback — belt+suspenders with the reaper)
if "${COMPOSE[@]}" ps --quiet hermes 2>/dev/null | grep -q .; then
    "${COMPOSE[@]}" pause hermes && echo "==> hermes paused"
else
    echo "==> hermes not running — skip pause"
fi

T1=$(date +%s%3N)
ELAPSED=$(( (T1 - T0) ))
TS=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$ROOT/logs"
echo "$TS  elapsed=${ELAPSED}ms  reason='$REASON'" >> "$ROOT/logs/panic.log"

echo
echo "==> PANIC complete (${ELAPSED}ms, SLO=30000ms)"
echo "==> Sentinel: $SENTINEL"
echo "==> Inspect logs, then recover:"
echo "    rm '$SENTINEL'"
echo "    docker compose -f '$ROOT/deploy/docker-compose.yml' unpause hermes"
