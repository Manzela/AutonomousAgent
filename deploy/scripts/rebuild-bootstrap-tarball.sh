#!/usr/bin/env bash
# deploy/scripts/rebuild-bootstrap-tarball.sh
# Rebuilds the Hermes VM bootstrap tarball from current repo HEAD and uploads
# to gs://autonomous-agent-2026-snapshots/bootstrap/.
#
# Run from repo root: ./deploy/scripts/rebuild-bootstrap-tarball.sh
# Requires: gsutil authenticated, SOPS-free (secrets are runtime-loaded, not in tarball)
# Idempotent: safe to re-run.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
STAGING_DIR="$(mktemp -d /tmp/hermes-bootstrap-staging.XXXXXX)"
TARBALL_PATH="$(mktemp /tmp/hermes-bootstrap.XXXXXX.tar.gz)"
GCS_BUCKET="gs://autonomous-agent-2026-snapshots/bootstrap"

trap 'rm -rf "${STAGING_DIR}" "${TARBALL_PATH}"' EXIT

echo "=== rebuilding bootstrap tarball from ${REPO_ROOT} ==="
echo "staging: ${STAGING_DIR}"

# --- 1. Copy runtime directories (bind-mounted by docker-compose.gcp.override.yml) ---
# lib/: bind-mounted as ./lib:/app/lib:ro in hermes, watcher services, litellm-proxy
cp -r "${REPO_ROOT}/lib"        "${STAGING_DIR}/lib"

# config/: bind-mounted as ./config/... in hermes + litellm-proxy + otel-collector
cp -r "${REPO_ROOT}/config"     "${STAGING_DIR}/config"

# scripts/: bind-mounted as ./scripts:/app/scripts:ro in watcher services
# Exclude vm-bootstrap/ — those live at staging root, not in ./scripts
cp -r "${REPO_ROOT}/scripts"    "${STAGING_DIR}/scripts"
rm -rf "${STAGING_DIR}/scripts/vm-bootstrap"

# docs/: hermes uses docs/conventions/ as context (new-repo-template.md)
mkdir -p "${STAGING_DIR}/docs/conventions"
cp "${REPO_ROOT}/docs/conventions/new-repo-template.md" \
   "${STAGING_DIR}/docs/conventions/new-repo-template.md"

# --- 2. Copy deploy artefacts ---
# Compose files must be at staging root (systemd unit runs:
#   docker compose -f .../docker-compose.yml -f .../docker-compose.gcp.override.yml)
cp "${REPO_ROOT}/deploy/docker-compose.yml"             "${STAGING_DIR}/docker-compose.yml"
cp "${REPO_ROOT}/deploy/docker-compose.gcp.override.yml" "${STAGING_DIR}/docker-compose.gcp.override.yml"

# otel/: bind-mounted by otel-collector as ./otel/collector.prod.yaml
mkdir -p "${STAGING_DIR}/otel"
cp "${REPO_ROOT}/deploy/otel/collector.prod.yaml"       "${STAGING_DIR}/otel/collector.prod.yaml"

# litellm/: bind-mounted by litellm-proxy as ./litellm/config.yaml
mkdir -p "${STAGING_DIR}/litellm"
cp "${REPO_ROOT}/deploy/litellm/config.yaml"            "${STAGING_DIR}/litellm/config.yaml"

# --- 3. Copy vm-bootstrap files (installed as system files by install.sh) ---
mkdir -p "${STAGING_DIR}/systemd"
cp "${REPO_ROOT}/scripts/vm-bootstrap/systemd/"*.service "${STAGING_DIR}/systemd/"
cp "${REPO_ROOT}/scripts/vm-bootstrap/load-secrets.sh"   "${STAGING_DIR}/load-secrets.sh"
cp "${REPO_ROOT}/scripts/vm-bootstrap/hermes-watchdog.sh" "${STAGING_DIR}/hermes-watchdog.sh"
cp "${REPO_ROOT}/scripts/vm-bootstrap/expected-containers.txt" "${STAGING_DIR}/expected-containers.txt"
chmod +x "${STAGING_DIR}/load-secrets.sh" "${STAGING_DIR}/hermes-watchdog.sh"

# --- 4. Remove artefacts that must NOT be in the tarball ---
# .git/ leaks history; __pycache__/*.pyc are build artefacts; secrets/ is runtime-only
find "${STAGING_DIR}" -name ".git" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}" -name "*.pyc" -delete 2>/dev/null || true
find "${STAGING_DIR}" -name ".DS_Store" -delete 2>/dev/null || true
find "${STAGING_DIR}" -name ".venv" -prune -exec rm -rf {} + 2>/dev/null || true
find "${STAGING_DIR}/lib" -name "tests" -prune -exec rm -rf {} + 2>/dev/null || true
# Secrets are SOPS-encrypted and loaded at runtime by hermes-secrets.service
# via GCP Secret Manager. Do NOT include secrets/ in the tarball.
rm -rf "${STAGING_DIR}/secrets" 2>/dev/null || true

# --- 5. Build tarball ---
echo "building tarball..."
tar -czf "${TARBALL_PATH}" -C "${STAGING_DIR}" .
ENTRY_COUNT=$(tar -tzf "${TARBALL_PATH}" | wc -l)
TARBALL_SIZE=$(du -h "${TARBALL_PATH}" | cut -f1)
echo "tarball: ${ENTRY_COUNT} entries, ${TARBALL_SIZE}"

# Smoke-check: key files must be present
for f in \
  "./config/hermes/AGENTS.md" \
  "./config/hermes/cli-config.yaml" \
  "./config/toolsets.yaml" \
  "./lib/a2a/server.py" \
  "./docker-compose.gcp.override.yml" \
  "./systemd/docker-compose-hermes.service" \
  "./otel/collector.prod.yaml" \
  "./litellm/config.yaml"; do
  if ! tar -tzf "${TARBALL_PATH}" | grep -q "^${f}$"; then
    echo "ERROR: missing from tarball: ${f}"
    exit 1
  fi
done
echo "smoke check: all required files present"

# --- 6. Upload to GCS ---
echo "uploading tarball..."
gsutil cp "${TARBALL_PATH}" "${GCS_BUCKET}/hermes-bootstrap.tar.gz"
echo "uploading install.sh..."
gsutil cp "${REPO_ROOT}/scripts/vm-bootstrap/install.sh" "${GCS_BUCKET}/install.sh"

# --- 7. Verify upload ---
echo "verifying GCS upload..."
gsutil ls -l "${GCS_BUCKET}/hermes-bootstrap.tar.gz"
gsutil ls -l "${GCS_BUCKET}/install.sh"

echo "=== bootstrap tarball rebuild complete ==="
echo "  tarball: ${ENTRY_COUNT} entries, ${TARBALL_SIZE}"
echo "  GCS:     ${GCS_BUCKET}/hermes-bootstrap.tar.gz"
