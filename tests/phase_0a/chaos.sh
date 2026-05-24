#!/usr/bin/env bash
# tests/phase_0a/chaos.sh <vm-name> <zone>
# Kill hermes container and verify watchdog brings it back within 90s.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"

echo "=== chaos.sh against $VM_NAME ($ZONE) ==="

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --quiet --command="
  set -euo pipefail
  echo 'pre-kill state:'
  sudo docker ps --filter name=hermes --format '{{.Names}}: {{.Status}}'

  echo 'killing hermes container...'
  # Find container ID for hermes. Use sudo.
  HERMES_ID=\$(sudo docker ps -q --filter name=hermes)
  if [ -z \"\$HERMES_ID\" ]; then
    echo 'ERROR: hermes container not found' >&2
    exit 1
  fi
  sudo docker kill \"\$HERMES_ID\"

  # Wait up to 90s for watchdog to restart
  deadline=\$(( \$(date +%s) + 90 ))
  while [ \"\$(date +%s)\" -lt \"\$deadline\" ]; do
    if sudo docker ps --filter name=hermes --filter status=running --format '{{.Names}}' | grep -q hermes; then
      echo 'PASS: hermes restarted by watchdog'
      exit 0
    fi
    sleep 5
  done

  echo 'FAIL: hermes did not restart within 90s' >&2
  sudo docker ps --filter name=hermes
  exit 1
"
