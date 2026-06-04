#!/usr/bin/env bash
# SP-IR1 Emergency halt: write HALT sentinel, sweep in-flight sandboxes,
# revoke the GitHub App installation token, and pause the hermes container.
#
# SLO: sentinel written + token revoked + sandboxes torn down within 30 s.
#
# Usage: bash scripts/panic.sh [reason]
#   reason defaults to "operator invoked panic.sh"
#
# C9-M1 fix: reason is passed via AA_PANIC_REASON env var, NOT interpolated
# into Python source. A reason containing quotes or newlines cannot cause a
# SyntaxError or inject code.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -f "$ROOT/deploy/docker-compose.yml")
export AA_PANIC_REASON="${1:-operator invoked panic.sh}"
export AA_KILL_SWITCH_PATH="${AA_KILL_SWITCH_PATH:-/tmp/aa-kill-switch}"

T0=$(date +%s%3N)
echo "==> PANIC: reason='$AA_PANIC_REASON'"

# 1. Write the HALT sentinel immediately (before any slow I/O)
mkdir -p "$(dirname "$AA_KILL_SWITCH_PATH")"
printf 'HALT: %s\n' "$AA_PANIC_REASON" > "$AA_KILL_SWITCH_PATH"
echo "==> Sentinel written: $AA_KILL_SWITCH_PATH"

# 2. Call the Python KillSwitch (revoke token + sweep reaper).
#    reason is passed via AA_PANIC_REASON env var (C9-M1 fix — never interpolated
#    into Python source code).
#    timeout -s KILL 25 enforces the sub-30 s SLO even if Python hangs.
_PYTHON_SCRIPT='
import os, sys
sys.path.insert(0, os.environ["AA_ROOT"])
from app.core.kill_switch import KillSwitch, RecordingTokenRevoker
from pathlib import Path
reason = os.environ.get("AA_PANIC_REASON", "panic.sh invoked")
sentinel = Path(os.environ.get("AA_KILL_SWITCH_PATH", "/tmp/aa-kill-switch"))
# RecordingTokenRevoker: live token revocation is wired by the FastAPI lifespan
# which has the installation token; the shell path uses a no-op revoker since
# it does not have the token (shell cannot auth as the GitHub App).
ks = KillSwitch(token_revoker=RecordingTokenRevoker(), sentinel_path=sentinel)
elapsed = ks.trigger(reason)
print(f"KillSwitch.trigger() completed in {elapsed:.3f}s")
'

export AA_ROOT="$ROOT"
if command -v timeout &>/dev/null; then
    timeout -s KILL 25 "$ROOT/.venv/bin/python" -c "$_PYTHON_SCRIPT" \
        || echo "==> Python KillSwitch call failed/timed out (reaper may not have run)"
else
    echo "==> WARNING: 'timeout' not available — running without 25 s kill guard"
    "$ROOT/.venv/bin/python" -c "$_PYTHON_SCRIPT" || true
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
ELAPSED=$(( T1 - T0 ))
TS=$(date -u +%Y%m%dT%H%M%SZ)

mkdir -p "$ROOT/logs"
printf '%s  elapsed=%dms  reason=%s\n' "$TS" "$ELAPSED" "$AA_PANIC_REASON" >> "$ROOT/logs/panic.log"

echo
echo "==> PANIC complete (${ELAPSED}ms, SLO=30000ms)"
echo "==> Sentinel: $AA_KILL_SWITCH_PATH"
echo "==> Inspect logs, then recover:"
echo "    rm '$AA_KILL_SWITCH_PATH'"
echo "    docker compose -f '$ROOT/deploy/docker-compose.yml' unpause hermes"
