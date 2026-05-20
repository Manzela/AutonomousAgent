#!/usr/bin/env bash
# scripts/migrate-secrets-to-secret-manager.sh
# Idempotent migration from SOPS env files to Google Secret Manager.
# Each secrets/NAME.env.sops becomes SM secret "autonomousagent-NAME"
# whose value is the full decrypted env-file content.
#
# Uses SHA-256 hash comparison to skip re-uploading unchanged secrets.
# Run with DRY_RUN=true to preview without creating versions.
#
# Prerequisites: sops binary, gcloud authed as an account with
#   roles/secretmanager.secretVersionAdder on project i-for-ai.

set -euo pipefail

PROJECT_ID="${PROJECT_ID:-i-for-ai}"
DRY_RUN="${DRY_RUN:-false}"
SECRETS_DIR="${SECRETS_DIR:-secrets}"

if ! command -v sops >/dev/null 2>&1; then
  echo "ERROR: sops not installed. Run: brew install sops" >&2
  exit 1
fi

shopt -s nullglob
sops_files=("$SECRETS_DIR"/*.env.sops)
if [ "${#sops_files[@]}" -eq 0 ]; then
  echo "ERROR: no .env.sops files found in $SECRETS_DIR" >&2
  exit 1
fi

for sops_file in "${sops_files[@]}"; do
  name=$(basename "$sops_file" .env.sops)
  secret_id="autonomousagent-${name}"

  echo "=== $name -> $secret_id ==="

  # Decrypt to a temp file deleted on EXIT
  tmp=$(mktemp)
  # shellcheck disable=SC2064
  trap "rm -f '$tmp'" EXIT

  sops -d "$sops_file" > "$tmp"

  new_hash=$(sha256sum "$tmp" | awk '{print $1}')

  # Compare to latest SM version hash (if one exists)
  existing_hash=""
  if gcloud secrets versions list "$secret_id" --project="$PROJECT_ID" \
      --limit=1 --format="value(name)" 2>/dev/null | grep -q .; then
    existing_hash=$(gcloud secrets versions access latest \
      --secret="$secret_id" --project="$PROJECT_ID" 2>/dev/null \
      | sha256sum | awk '{print $1}')
  fi

  if [ "$new_hash" = "$existing_hash" ]; then
    echo "  no change (hash match); skipping"
    rm -f "$tmp"
    trap - EXIT
    continue
  fi

  if [ "$DRY_RUN" = "true" ]; then
    echo "  DRY_RUN: would create new version of $secret_id (hash=$new_hash)"
  else
    gcloud secrets versions add "$secret_id" \
      --project="$PROJECT_ID" --data-file="$tmp"
    echo "  created new version (hash=$new_hash)"
  fi

  rm -f "$tmp"
  trap - EXIT
done

echo "Migration complete."
