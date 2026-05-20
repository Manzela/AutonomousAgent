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

# ENV file secrets (full env-file content stored per secret).
# Mirrors secret_manager.tf sops_env_files + autonomousagent- prefix.
ENV_SECRETS=(
  "autonomousagent-chroma-cloud"
  "autonomousagent-hermes-provider"
  "autonomousagent-honcho"
  "autonomousagent-litellm-db"
  "autonomousagent-telegram"
)

for secret in "${ENV_SECRETS[@]}"; do
  name="${secret#autonomousagent-}"
  out="$ENV_DIR/${name}.env"
  gcloud secrets versions access latest --secret="$secret" --project="$PROJECT_ID" > "$out"
  chmod 600 "$out"
  echo "loaded $secret -> $out"
done

# Individual Docker secret files (raw value, no KEY= prefix).
# Used as Docker compose bind-mount secrets (file: ../secrets/<name>).
INDIVIDUAL_SECRETS=(
  "autonomousagent-github-pat"
  "autonomousagent-litellm-master-key"
)

for secret in "${INDIVIDUAL_SECRETS[@]}"; do
  name="${secret#autonomousagent-}"
  out="$ENV_DIR/${name}"
  gcloud secrets versions access latest --secret="$secret" --project="$PROJECT_ID" > "$out"
  chmod 600 "$out"
  echo "loaded $secret -> $out"
done

echo "load-secrets done $(date -u +%FT%TZ)"
