#!/usr/bin/env bash
# scripts/vm-bootstrap/load-secrets.sh
# Pulls autonomousagent-* secrets from Secret Manager into /run/hermes/env/.
# Runs once per boot as a systemd one-shot (hermes-secrets.service).
# /run/hermes/env/ is a tmpfs — secrets exist only in memory, never on disk.

set -euo pipefail

PROJECT_ID="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)"

ENV_DIR=/run/hermes/env
mkdir -p "$ENV_DIR"
chmod 700 "$ENV_DIR"

# List every secret with the autonomousagent- prefix
SECRETS=$(gcloud secrets list --project="$PROJECT_ID" \
  --filter="name:autonomousagent-" --format="value(name)")

if [ -z "$SECRETS" ]; then
  echo "ERROR: no autonomousagent-* secrets found in project $PROJECT_ID" >&2
  exit 1
fi

for secret in $SECRETS; do
  # Strip project path: "projects/123/secrets/autonomousagent-honcho" -> "honcho"
  short="${secret##*/}"          # strip path prefix
  name="${short#autonomousagent-}"  # strip autonomousagent- prefix
  out="$ENV_DIR/${name}.env"
  gcloud secrets versions access latest --secret="$short" --project="$PROJECT_ID" > "$out"
  chmod 600 "$out"
  echo "loaded $short -> $out"
done

echo "load-secrets done $(date -u +%FT%TZ)"
