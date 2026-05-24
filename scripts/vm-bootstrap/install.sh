#!/usr/bin/env bash
# scripts/vm-bootstrap/install.sh
# Master bootstrap for the AutonomousAgent GCE VM. Runs on first boot
# via startup-script-url metadata. Idempotent — safe to re-run.
#
# Naming note: GCP resources use autonomousagent-* prefix.
# VM-side paths use /opt/hermes/ (workload-descriptive, unchanged).

set -euo pipefail

LOG=/var/log/hermes-bootstrap.log
exec > >(tee -a "$LOG") 2>&1
echo "=== hermes bootstrap start $(date -u +%FT%TZ) ==="

PROJECT_ID="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/project/project-id)"
HERMES_IMAGE_REPO="$(curl -fsSL -H 'Metadata-Flavor: Google' \
  http://metadata.google.internal/computeMetadata/v1/instance/attributes/hermes-image-repo)"

export PROJECT_ID HERMES_IMAGE_REPO

# 1. System prep
apt-get update -qq
apt-get install -y --no-install-recommends \
  ca-certificates curl gnupg lsb-release jq

# 2. Docker + compose plugin
if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  rm -f /etc/apt/keyrings/docker.gpg
  curl -fsSL https://download.docker.com/linux/debian/gpg \
    | gpg --batch --no-tty --dearmor -o /etc/apt/keyrings/docker.gpg
  chmod a+r /etc/apt/keyrings/docker.gpg
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] \
    https://download.docker.com/linux/debian $(lsb_release -cs) stable" \
    > /etc/apt/sources.list.d/docker.list
  apt-get update -qq
  apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi

# 3. Mount data disk if not yet mounted
DATA_DEV=/dev/disk/by-id/google-hermes-data
DATA_DIR=/opt/hermes/data
if ! mountpoint -q "$DATA_DIR"; then
  mkdir -p "$DATA_DIR"
  if ! blkid "$DATA_DEV" >/dev/null 2>&1; then
    mkfs.ext4 -F "$DATA_DEV"
  fi
  echo "${DATA_DEV} ${DATA_DIR} ext4 defaults,nofail 0 2" >> /etc/fstab
  mount "$DATA_DIR"
fi

# 4. Fetch bootstrap tarball from GCS
mkdir -p /opt/hermes/bootstrap
gsutil cp "gs://autonomous-agent-2026-snapshots/bootstrap/hermes-bootstrap.tar.gz" \
  /opt/hermes/bootstrap/hermes-bootstrap.tar.gz
tar -xzf /opt/hermes/bootstrap/hermes-bootstrap.tar.gz -C /opt/hermes/bootstrap/

# 5. Install systemd units and scripts
install -m 0644 /opt/hermes/bootstrap/systemd/hermes-secrets.service        /etc/systemd/system/
install -m 0644 /opt/hermes/bootstrap/systemd/docker-compose-hermes.service  /etc/systemd/system/
install -m 0644 /opt/hermes/bootstrap/systemd/hermes-watchdog.service        /etc/systemd/system/
install -m 0755 /opt/hermes/bootstrap/load-secrets.sh                         /usr/local/bin/
install -m 0755 /opt/hermes/bootstrap/hermes-watchdog.sh                      /usr/local/bin/
mkdir -p /etc/hermes
install -m 0644 /opt/hermes/bootstrap/expected-containers.txt                 /etc/hermes/expected-containers.txt
mkdir -p /run/hermes/env
# Symlink /opt/hermes/secrets -> /run/hermes/env so docker-compose env_file
# paths (../secrets/*.env relative to /opt/hermes/bootstrap/) resolve to tmpfs.
ln -sfn /run/hermes/env /opt/hermes/secrets
# openrouter is optional (required: false in compose); create placeholder
touch /run/hermes/env/openrouter.env
chmod 600 /run/hermes/env/openrouter.env

# 6. Authenticate Docker with Artifact Registry
gcloud auth configure-docker us-central1-docker.pkg.dev --quiet

# 7. Enable + start units in dependency order
systemctl daemon-reload
systemctl enable hermes-secrets.service docker-compose-hermes.service hermes-watchdog.service
systemctl start hermes-secrets.service          # one-shot: loads SM secrets
systemctl start docker-compose-hermes.service   # one-shot: brings compose up
systemctl start hermes-watchdog.service         # continuous: monitors containers

echo "=== hermes bootstrap done $(date -u +%FT%TZ) ==="
