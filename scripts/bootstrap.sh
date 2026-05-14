#!/usr/bin/env bash
# One-shot bootstrap for local Hermes Agent deployment.
# Idempotent — safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

echo "==> 1/6 Verify host prerequisites"
./scripts/verify-prereqs.sh

echo "==> 2/6 Decrypt secrets"
./scripts/decrypt-secrets.sh

echo "==> 3/6 Validate config files"
python -m lib.limits_validator config/limits.yaml

echo "==> 4/6 Pull/build container images"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml pull
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml build

echo "==> 5/6 Bring stack up"
docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml up -d
sleep 5  # let healthchecks settle

echo "==> 6/6 Run smoke tests"
./scripts/smoke.sh

echo
echo "✓ Bootstrap complete. Talk to the agent:"
echo "    docker compose -f deploy/docker-compose.yml exec hermes-agent hermes"
echo "Or send a Telegram message to your bot."
