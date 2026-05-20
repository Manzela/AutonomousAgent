#!/usr/bin/env bash
# scripts/vm-bootstrap/hermes-watchdog.sh
# Polls docker compose ps every 30s. Restarts the stack if any expected
# container is missing or not running. Emits structured JSON to stdout
# so gcplogs captures structured log entries in Cloud Logging.

set -euo pipefail

COMPOSE_FILES=(-f /opt/hermes/bootstrap/docker-compose.yml \
               -f /opt/hermes/bootstrap/docker-compose.gcp.override.yml)
EXPECTED_FILE="${EXPECTED_FILE:-/etc/hermes/expected-containers.txt}"
INTERVAL="${INTERVAL:-30}"

log_json() {
  local level="$1" msg="$2" detail="${3:-null}"
  printf '{"ts":"%s","level":"%s","msg":"%s","detail":%s}\n' \
    "$(date -u +%FT%TZ)" "$level" "$msg" "$detail"
}

mapfile -t expected < "$EXPECTED_FILE"

while true; do
  running=$(docker compose "${COMPOSE_FILES[@]}" ps --format json 2>/dev/null \
    | jq -r 'select(.State=="running") | .Service' \
    | sort -u)

  missing=()
  for svc in "${expected[@]}"; do
    if ! printf '%s\n' "$running" | grep -qx "$svc"; then
      missing+=("$svc")
    fi
  done

  missing_json="[$(printf '"%s",' "${missing[@]+"${missing[@]}"}" | sed 's/,$//')]"
  running_count=$(printf '%s\n' "$running" | grep -c . || true)

  log_json "info" "hermes_watchdog_tick" \
    "{\"expected\":${#expected[@]},\"running\":${running_count},\"missing\":${missing_json}}"

  if [ "${#missing[@]}" -gt 0 ]; then
    log_json "warn" "hermes_watchdog_restart_triggered" \
      "{\"missing\":${missing_json}}"
    if ! docker compose "${COMPOSE_FILES[@]}" up -d; then
      log_json "error" "hermes_watchdog_restart_failed" "null"
    fi
  fi

  sleep "$INTERVAL"
done
