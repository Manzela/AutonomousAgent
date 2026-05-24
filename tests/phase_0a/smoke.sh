#!/usr/bin/env bash
# tests/phase_0a/smoke.sh <vm-name> <zone>
# Post-deploy smoke check. Returns 0 on pass, non-zero on fail.

set -euo pipefail

VM_NAME="${1:?vm-name required}"
ZONE="${2:?zone required}"

echo "=== smoke.sh against $VM_NAME ($ZONE) ==="

# 1. Reachable via IAP
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --command="echo ok" --quiet >/dev/null
echo "PASS: IAP SSH reachable"

# 2. All expected containers running
# Note: we use /opt/hermes/bootstrap as the deploy dir (confirmed by VM inspection).
remote_out=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --quiet --command="
  sudo bash -c '
    cd /opt/hermes/bootstrap
    expected=\$(cat /etc/hermes/expected-containers.txt | sort -u)
    running=\$(docker compose -f docker-compose.yml -f docker-compose.gcp.override.yml ps --format json \
      | jq -r \"select(.State==\\\"running\\\") | .Service\" | sort -u)
    missing=\$(comm -23 <(echo \"\$expected\") <(echo \"\$running\"))
    if [ -n \"\$missing\" ]; then
      echo \"MISSING: \$missing\" >&2
      exit 1
    fi
    echo \"all expected containers running\"
  '
")
echo "PASS: $remote_out"

# 3. litellm-proxy reported as healthy within 90s
deadline=$(( $(date +%s) + 90 ))
while [ "$(date +%s)" -lt "$deadline" ]; do
  status=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --tunnel-through-iap --quiet --command="
    sudo docker inspect --format='{{.State.Health.Status}}' \$(sudo docker ps -q --filter name=litellm-proxy) 2>/dev/null
  ")
  if [ "$status" = "healthy" ]; then
    echo "PASS: litellm-proxy health check passed"
    exit 0
  fi
  sleep 5
done

echo "FAIL: litellm-proxy did not become healthy within 90s" >&2
exit 1
