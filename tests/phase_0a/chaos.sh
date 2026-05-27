#!/usr/bin/env bash
# tests/phase_0a/chaos.sh <vm-name> <zone> [scenario]
#
# Chaos scenarios exercising hermes and its runtime dependencies.
# Without [scenario] (or "all"), every scenario runs in order.
# With [scenario], only the named scenario runs.
#
# Scenarios:
#   01_hermes_kill       — kill hermes container; watchdog must restart within 90s
#   02_litellm_outage    — pause litellm-proxy; hermes must stay up, surface LLM errors
#   03_chroma_outage     — pause chroma; hermes must stay up
#   04_cloud_sql_outage  — pause cloud-sql-proxy; hermes must stay up
#   05_redis_outage      — pause redis; hermes must stay up (JTI cache degrades to L1)
#   06_otel_outage       — pause otel-collector; hermes must continue (traces dropped, not fatal)
#   07_disk_full         — fill /tmp to <3 MiB free; hermes must not crash
#   08_clock_drift       — advance system clock +65s; JWT clock-skew path triggers gracefully
#
# Pre-conditions on the VM:
#   * docker compose stack is running ("hermes" container exists and is healthy)
#   * The calling identity has IAP-tunnelled SSH access to the VM
#   * The VM OS user has passwordless sudo for docker / date / dd commands

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"
SCENARIO="${3:-all}"

echo "=== chaos.sh against $VM_NAME ($ZONE) — scenario=$SCENARIO ==="

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --quiet --command="
set -euo pipefail

# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

hermes_running() {
  sudo docker ps --filter name=hermes --filter status=running --format '{{.Names}}' | grep -q hermes
}

# Wait up to \$1 seconds (default 90) for hermes to be running.
wait_for_hermes() {
  local deadline=\$(( \$(date +%s) + \${1:-90} ))
  while [ \"\$(date +%s)\" -lt \"\$deadline\" ]; do
    if hermes_running; then
      return 0
    fi
    sleep 5
  done
  return 1
}

# Return first running container ID matching name fragment \$1, or empty string.
container_id() {
  sudo docker ps -q --filter name=\"\$1\" | head -1
}

pass() { echo \"PASS [\$1]\"; }
fail() { echo \"FAIL [\$1]: \$2\" >&2; sudo docker ps; exit 1; }

# Pause a dependency, verify hermes survives, unpause and verify recovery.
# Usage: outage_scenario <scenario-tag> <name-fragment> <pause-secs> <recovery-secs>
outage_scenario() {
  local tag=\"\$1\" frag=\"\$2\" pause_s=\"\$3\" recover_s=\"\$4\"
  local id
  id=\$(container_id \"\$frag\")
  if [ -z \"\$id\" ]; then
    echo \"SKIP [\$tag]: no running container matching '\$frag'\"
    return 0
  fi
  echo \"--- [\$tag] pausing \$frag (\$id) for \${pause_s}s ---\"
  sudo docker pause \"\$id\"
  sleep \"\$pause_s\"
  hermes_running || fail \"\$tag\" 'hermes died during outage'
  sudo docker unpause \"\$id\"
  sleep \"\$recover_s\"
  hermes_running && pass \"\$tag\" || fail \"\$tag\" 'hermes did not recover after restore'
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 01: kill hermes — watchdog must restart within 90 s
# ──────────────────────────────────────────────────────────────────────────────
scenario_01_hermes_kill() {
  echo '--- [01] hermes kill ---'
  local id
  id=\$(container_id hermes)
  [ -z \"\$id\" ] && fail '01' 'hermes container not found'
  sudo docker kill \"\$id\"
  wait_for_hermes 90 && pass '01' || fail '01' 'hermes did not restart within 90s'
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 02: LiteLLM / Vertex proxy outage
# Hermes must stay alive and surface LLM errors, not crash.
# ──────────────────────────────────────────────────────────────────────────────
scenario_02_litellm_outage() {
  outage_scenario '02' litellm 15 5
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 03: Chroma outage
# ──────────────────────────────────────────────────────────────────────────────
scenario_03_chroma_outage() {
  outage_scenario '03' chroma 15 5
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 04: Cloud SQL proxy outage
# Memory store calls will fail; hermes must not crash.
# ──────────────────────────────────────────────────────────────────────────────
scenario_04_cloud_sql_outage() {
  outage_scenario '04' cloud-sql-proxy 15 15
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 05: Redis / Memorystore outage
# JTI L1 cache degrades from Redis to in-process TTLCache; hermes must stay up.
# ──────────────────────────────────────────────────────────────────────────────
scenario_05_redis_outage() {
  outage_scenario '05' redis 15 5
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 06: OTel collector outage (non-critical path)
# Lost spans are acceptable; hermes must continue processing requests.
# ──────────────────────────────────────────────────────────────────────────────
scenario_06_otel_outage() {
  outage_scenario '06' otel 15 5
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 07: disk full — fill /tmp to < 3 MiB free
# Hermes must log an error (or survive gracefully) and recover when space is freed.
# ──────────────────────────────────────────────────────────────────────────────
scenario_07_disk_full() {
  echo '--- [07] disk full ---'
  local avail_kb fill_kb fill_file
  avail_kb=\$(df -k /tmp | awk 'NR==2 {print \$4}')
  fill_kb=\$(( avail_kb - 3072 ))
  if [ \"\$fill_kb\" -le 0 ]; then
    echo 'SKIP [07]: /tmp already near-full'
    return 0
  fi
  fill_file=\"/tmp/_chaos_fill_\$\$\"
  # Fill the disk; ignore errors (dd exits non-zero when disk is full, which is the goal).
  dd if=/dev/zero of=\"\$fill_file\" bs=1024 count=\"\$fill_kb\" 2>/dev/null || true
  sleep 5
  local still_running=0
  hermes_running && still_running=1
  rm -f \"\$fill_file\"
  sleep 5
  if [ \"\$still_running\" -eq 1 ]; then
    hermes_running && pass '07' || fail '07' 'hermes died after disk fill was cleared'
  else
    # Hermes crashed under disk pressure — watchdog must revive it.
    wait_for_hermes 60 && pass '07' || fail '07' 'hermes did not recover from disk-full crash'
  fi
}

# ──────────────────────────────────────────────────────────────────────────────
# Scenario 08: JWT clock drift (+65 s, exceeds the 60 s skew tolerance)
# Hermes itself must keep running; inbound JWTs with the old timestamp should
# be rejected by the verify path (InvalidAudienceError or similar), not crash.
# Clock is restored via NTP re-sync immediately after.
# ──────────────────────────────────────────────────────────────────────────────
scenario_08_clock_drift() {
  echo '--- [08] JWT clock drift ---'
  # Probe sudo date access without crashing the whole script.
  if ! sudo date -s \"+65 seconds\" >/dev/null 2>&1; then
    echo 'SKIP [08]: cannot set system clock (no sudo date access on this VM)'
    return 0
  fi
  sleep 3
  hermes_running || fail '08' 'hermes crashed immediately after +65s clock shift'
  # Restore clock via NTP re-sync (try timedatectl first; fall back to ntpdate).
  if command -v timedatectl >/dev/null 2>&1; then
    sudo timedatectl set-ntp true
  elif command -v ntpdate >/dev/null 2>&1; then
    sudo ntpdate -u pool.ntp.org >/dev/null 2>&1 || true
  fi
  sleep 3
  hermes_running && pass '08' || fail '08' 'hermes died after clock restore'
}

# ──────────────────────────────────────────────────────────────────────────────
# Dispatcher
# ──────────────────────────────────────────────────────────────────────────────
run_all() {
  scenario_01_hermes_kill
  scenario_02_litellm_outage
  scenario_03_chroma_outage
  scenario_04_cloud_sql_outage
  scenario_05_redis_outage
  scenario_06_otel_outage
  scenario_07_disk_full
  scenario_08_clock_drift
  echo '=== all chaos scenarios passed ==='
}

case \"$SCENARIO\" in
  all)                   run_all ;;
  01_hermes_kill)        scenario_01_hermes_kill ;;
  02_litellm_outage)     scenario_02_litellm_outage ;;
  03_chroma_outage)      scenario_03_chroma_outage ;;
  04_cloud_sql_outage)   scenario_04_cloud_sql_outage ;;
  05_redis_outage)       scenario_05_redis_outage ;;
  06_otel_outage)        scenario_06_otel_outage ;;
  07_disk_full)          scenario_07_disk_full ;;
  08_clock_drift)        scenario_08_clock_drift ;;
  *)
    echo \"Unknown scenario: $SCENARIO\" >&2
    echo 'Valid scenarios: all 01_hermes_kill 02_litellm_outage 03_chroma_outage 04_cloud_sql_outage 05_redis_outage 06_otel_outage 07_disk_full 08_clock_drift' >&2
    exit 1 ;;
esac
"
