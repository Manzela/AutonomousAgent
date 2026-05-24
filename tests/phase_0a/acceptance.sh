#!/usr/bin/env bash
# tests/phase_0a/acceptance.sh <vm-name> <zone>
# Runs all 10 acceptance criteria from spec section 11.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"
PROJECT_ID="${PROJECT_ID:-autonomous-agent-2026}"

PASS=0; FAIL=0; DEFER=0
pass() { echo "PASS  #$1: $2"; PASS=$((PASS+1)); }
fail() { echo "FAIL  #$1: $2"; FAIL=$((FAIL+1)); }
defer() { echo "DEFER #$1: $2"; DEFER=$((DEFER+1)); }

echo "=== acceptance.sh against $VM_NAME ($ZONE) ==="

# 1. Pre-flight blocker closed (P0-A)
if [ -f "audit/2026-05-20-state-of-the-repo/p0a-rca/REPRODUCTION-SUMMARY.md" ]; then
  pass 1 "Pre-flight P0-A closed (summary found)"
else
  defer 1 "Pre-flight P0-A summary not found — assuming closed if VM is live"
fi

# 2. 11 long-running containers up for 72h consecutive
if bash tests/phase_0a/smoke.sh "$VM_NAME" "$ZONE"; then
  pass 2 "containers running (sample)"
else
  fail 2 "containers not all running"
fi
defer 2 "(72h consecutive requires soak window)"

# 3. Uptime check 99%+ over 7-day window
defer 3 "(requires 7-day window — check Cloud Monitoring after soak)"

# 4. Watchdog steady-state + chaos test
if bash tests/phase_0a/chaos.sh "$VM_NAME" "$ZONE"; then
  pass 4 "watchdog restarts killed container"
else
  fail 4 "watchdog did not restart"
fi

# 5. Daily PD snapshot for 7 days
snap_count=$(gcloud compute snapshots list --project="$PROJECT_ID" --filter="sourceDisk:autonomousagent-vm-data" --format="value(name)" 2>/dev/null | wc -l)
if [ "$snap_count" -gt 0 ]; then
  pass 5 "snapshots exist ($snap_count present)"
else
  # Check if policy exists and is attached
  if gcloud compute resource-policies describe autonomousagent-data-daily-snapshot --region=us-central1 --project="$PROJECT_ID" --quiet >/dev/null 2>&1; then
    defer 5 "policy exists but no snapshots yet (normal for first 24h)"
  else
    fail 5 "no snapshots found and policy missing"
  fi
fi
defer 5 "(7 consecutive snapshots requires 7d)"

# 6. Test recovery (snapshot restore -> new VM)
defer 6 "(out-of-band test — see docs/runbooks/phase-0a-recovery.md)"

# 7. CI workflow end-to-end <10min
gh run list --workflow="Phase 0a Deploy" --branch=main --status=success --limit=1 --json conclusion,startedAt,updatedAt > /tmp/run.json 2>/dev/null || echo "[]" > /tmp/run.json
if [ "$(cat /tmp/run.json)" != "[]" ]; then
  duration_sec=$(python3 -c "import json,datetime; data=json.load(open('/tmp/run.json')); \
    d=data[0]; \
    s=datetime.datetime.fromisoformat(d['startedAt'].replace('Z', '+00:00')); \
    e=datetime.datetime.fromisoformat(d['updatedAt'].replace('Z', '+00:00')); \
    print(int((e-s).total_seconds()))" 2>/dev/null || echo "9999")
  if [ "$duration_sec" -lt 600 ]; then
    pass 7 "CI ran in ${duration_sec}s"
  else
    fail 7 "CI took ${duration_sec}s (>600)"
  fi
else
  defer 7 "no successful CI run found on main yet"
fi

# 8. WIF works (no JSON keys in repo or GH secrets)
if grep -rqE "private_key_id|[B]EGIN [P]RIVATE [K]EY" --include="*.json" . 2>/dev/null; then
  fail 8 "JSON key found in repo"
else
  pass 8 "no JSON keys in repo"
fi
if gh secret list --json name --jq '.[].name' 2>/dev/null | grep -iqE "GCP.*KEY|SA.*JSON"; then
  fail 8 "GH secret holds GCP JSON key"
else
  pass 8 "no GH secrets named *GCP*KEY*"
fi

# 9. Secret Manager reachable from VM
if gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --quiet --command="
  sudo bash -c 'ls /run/hermes/env/*.env >/dev/null 2>&1 && grep -q HONCHO_API_KEY /run/hermes/env/honcho.env'
"; then
  pass 9 "secrets present in /run/hermes/env"
else
  fail 9 "secrets missing on VM"
fi

# 10. Cost actuals ±20% of $125/mo estimate
defer 10 "(requires 1 billing cycle — check after 30d)"

echo "=== Summary: PASS=$PASS FAIL=$FAIL DEFER=$DEFER ==="
[ "$FAIL" -eq 0 ]
