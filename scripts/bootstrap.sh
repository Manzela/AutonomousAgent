#!/usr/bin/env bash
# One-shot bootstrap for local Hermes Agent deployment.
# Idempotent — safe to re-run.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

# Resolve a working Python: prefer the project venv (created in T7), then python3,
# then python. Bare `python` is unreliable on macOS where there is no system
# `python` symlink.
if [ -x "$ROOT/.venv/bin/python" ]; then
  PYTHON="$ROOT/.venv/bin/python"
elif command -v python3 >/dev/null 2>&1; then
  PYTHON="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
  PYTHON="$(command -v python)"
else
  echo "✗ no python interpreter found. Run T7 (Python project layout) first." >&2
  exit 1
fi

# The compose stack reads decrypted secrets via env_file: + secrets:. Those
# files are produced by step 2 below and consumed by step 4 onward.
COMPOSE=(docker compose -f deploy/docker-compose.yml -f deploy/docker-compose.dev.yml)

echo "==> 1/6 Verify host prerequisites"
./scripts/verify-prereqs.sh

echo "==> 2/6 Decrypt secrets"
./scripts/decrypt-secrets.sh

echo "==> 3/6 Validate config files (using $PYTHON)"
"$PYTHON" -m lib.limits_validator config/limits.yaml

echo "==> 4/6 Pull/build container images"
"${COMPOSE[@]}" pull --ignore-pull-failures
"${COMPOSE[@]}" build

echo "==> 5/6 Bring stack up"
"${COMPOSE[@]}" up -d
echo "    waiting 10s for healthchecks to settle..."
sleep 10

echo "==> 6/6 Run smoke tests"
./scripts/smoke.sh

echo
echo "✓ Bootstrap complete. Talk to the agent:"
echo "    ${COMPOSE[*]} exec hermes-agent hermes"
echo "Or send a Telegram message to @Manzelagent_bot."
